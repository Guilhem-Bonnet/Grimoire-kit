"""Route de cérémonie déterministe et profondeur de test opposable (W6-01).

Constat : le kit n'avait aucune route par tâche. Une tâche V0 d'un fichier et
une migration qui touche les secrets passaient par la même cérémonie, et rien
ne disait jusqu'où une tâche devait être testée avant d'être acceptée. Le
composer d'AI-DLC choisit, lui, une profondeur par un jugement de LLM, sans
calibration publiée : deux appels peuvent rendre deux routes.

Ce module rend la route **calculée**, jamais devinée :

- :func:`suggest` ne lit que des entrées déclarées (classe de vérifiabilité,
  risque de l'intake, fichiers touchés, commande de test résolue, importeurs
  directs des fichiers touchés) et rend une des cinq routes de :data:`ROUTES`. Aucun LLM, aucune
  horloge, aucun hasard : deux appels sur les mêmes entrées rendent les mêmes
  octets (:func:`render_json`).
- Chaque règle donne un **plancher** ; la route est le maximum des planchers.
  L'ordre des règles ne change donc rien, et une entrée inconnue ne peut que
  monter la route (fichiers inconnus = changement qu'on ne sait pas borner),
  jamais la descendre.
- Les seuils sont des :class:`Index` qui portent ``calibrated`` et leur date :
  aucun n'est affiché comme précis s'il n'a pas été ajusté sur des données.
  Aujourd'hui aucun ne l'est, et la sortie le dit.

La profondeur de test (:class:`DepthLevel`) se mesure sur ce que la tâche a
réellement fait passer — les commandes de son dernier verdict
``grimoire task verify`` —, pas sur ce qu'elle promet : ``floor`` (une commande
verte), ``standard`` (dont une commande de test du projet), ``strict`` (en plus,
une analyse statique verte). Le gate de fermeture la rend opposable
(:func:`depth_shortfall`, code :data:`DEPTH_UNMET`).

Ce module ne choisit pas de modèle : le palier de LLM reste l'affaire du
routeur de dispatch. Il choisit la cérémonie (revues, checkpoint, session).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.evidence.schemas import VerdictResult
from grimoire.missions.intake import IntakeRequest, MissionIntakeService
from grimoire.missions.schemas import RiskProfile
from grimoire.missions.verifiability import Verifiability, classify_criteria

if TYPE_CHECKING:
    from grimoire.evidence.schemas import EvidencePack, VerificationVerdict

__all__ = [
    "DEPTH_UNMET",
    "INDICES",
    "MAX_ROUTES",
    "ROUTES",
    "SCHEMA",
    "DepthLevel",
    "Index",
    "Route",
    "RouteInputs",
    "RouteSuggestion",
    "changed_files",
    "check_kinds",
    "depth_shortfall",
    "import_fan_in",
    "inputs_for",
    "reached_depth",
    "render_json",
    "route_named",
    "suggest",
    "verified_commands",
]

SCHEMA = "grimoire.ceremony_route.v1"

#: Code du refus de gate quand la profondeur exigée n'est pas atteinte.
DEPTH_UNMET = "acceptance.depth_floor_unmet"

#: Plafond du nombre de routes : au-delà, la route devient un réglage fin
#: qu'on ne sait plus ni expliquer ni calibrer.
MAX_ROUTES = 5


class DepthLevel(StrEnum):
    """Jusqu'où une tâche doit avoir été testée pour être acceptée."""

    FLOOR = "floor"
    STANDARD = "standard"
    STRICT = "strict"


_DEPTH_ORDER: tuple[DepthLevel, ...] = (DepthLevel.FLOOR, DepthLevel.STANDARD, DepthLevel.STRICT)

_DEPTH_MEANING: dict[DepthLevel, str] = {
    DepthLevel.FLOOR: "au moins une commande déclarée, verte au dernier verdict",
    DepthLevel.STANDARD: "au moins une commande de test du projet, verte au dernier verdict",
    DepthLevel.STRICT: "une commande de test ET une analyse statique (lint ou typage), vertes au dernier verdict",
}


@dataclass(frozen=True, slots=True)
class Index:
    """Un seuil de la route — avec, toujours, s'il est calibré et depuis quand."""

    name: str
    value: int
    calibrated: bool
    as_of: str
    meaning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "calibrated": self.calibrated,
            "as_of": self.as_of,
            "meaning": self.meaning,
        }


#: Seuils posés à la main sur le corpus figé d'``evals/ceremony-budget.yaml``,
#: jamais ajustés sur des runs réels : ``calibrated`` vaut donc ``False``.
INDICES: tuple[Index, ...] = (
    Index("files_wide", 10, False, "2026-10-18",
          "au-delà de ce nombre de fichiers touchés, le changement est large"),
    Index("impact_wide", 20, False, "2026-10-18",
          "au-delà de ce nombre d'importeurs directs des fichiers touchés, l'impact est large"),
)
_INDEX = {index.name: index for index in INDICES}

#: Étapes qui exigent une personne : ce qu'une route coûte en tours humains.
_HUMAN_STEPS: frozenset[str] = frozenset({"human_review", "checkpoint", "session_design"})


@dataclass(frozen=True, slots=True)
class Route:
    """Une route de cérémonie : la profondeur exigée et les étapes imposées."""

    name: str
    rank: int
    test_depth: DepthLevel
    steps: tuple[str, ...]
    meaning: str

    @property
    def human_turns(self) -> int:
        return sum(1 for step in self.steps if step in _HUMAN_STEPS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rank": self.rank,
            "test_depth": self.test_depth.value,
            "steps": list(self.steps),
            "human_turns": self.human_turns,
            "meaning": self.meaning,
        }


ROUTES: tuple[Route, ...] = (
    Route("express", 0, DepthLevel.FLOOR, ("task_verify",),
          "verdict mécanique seul : les commandes déclarées rendent le verdict"),
    Route("checked", 1, DepthLevel.STANDARD, ("task_verify", "agent_review"),
          "commandes de test, puis relecture par un agent"),
    Route("reviewed", 2, DepthLevel.STANDARD, ("task_verify", "agent_review", "human_review"),
          "commandes de test, relecture par un agent puis par une personne"),
    Route("guarded", 3, DepthLevel.STRICT, ("task_verify", "agent_review", "human_review", "checkpoint"),
          "tests et analyse statique, double relecture, checkpoint humain avant fermeture"),
    Route("session", 4, DepthLevel.STRICT, ("session_design", "task_verify", "human_review", "checkpoint"),
          "jugement ou conception : se traite en session avec une personne"),
)


def route_named(name: str) -> Route:
    for route in ROUTES:
        if route.name == name:
            return route
    raise KeyError(name)


# ── Entrées ─────────────────────────────────────────────────────────────────

_RISK_FLOOR: dict[RiskProfile, int] = {
    RiskProfile.LIGHT: 0,
    RiskProfile.STANDARD: 0,
    RiskProfile.STRICT: 2,
    RiskProfile.SECURITY_CRITICAL: 3,
    RiskProfile.RELEASE: 3,
}
_RISK_ORDER: tuple[RiskProfile, ...] = tuple(_RISK_FLOOR)

_CLASS_FLOOR: dict[Verifiability, int] = {Verifiability.V0: 0, Verifiability.V1: 1, Verifiability.V2: 4}

#: Chemins dont la seule présence dans le changement impose la route ``guarded``.
_GUARDED_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("secret", re.compile(
        r"(^|/)(\.env([^/]*)|[^/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)[^/]*|"
        r"[^/]*(secret|credential)[^/]*)$", re.IGNORECASE)),
    ("ci_workflow", re.compile(r"(^|/)\.(github/workflows|gitlab-ci\.yml$|circleci/)", re.IGNORECASE)),
    ("db_migration", re.compile(r"(^|/)(migrations?|alembic)/", re.IGNORECASE)),
    ("release", re.compile(r"(^|/)(publish|release)[^/]*\.(ya?ml|toml|py|sh)$", re.IGNORECASE)),
)


def _norm_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


@dataclass(frozen=True, slots=True)
class RouteInputs:
    """Ce que la route lit — rien d'autre. ``None`` veut dire inconnu, jamais zéro."""

    verifiability: Verifiability
    risk_profile: RiskProfile
    #: Fichiers touchés, triés et dédoublonnés ; ``None`` = inconnus.
    files: tuple[str, ...] | None
    #: Commande de test résolue pour le projet ; ``None`` = non résolue.
    test_runner: str | None
    #: Fichiers qui importent directement un fichier touché ; ``None`` = non mesuré.
    impact_files: int | None

    @classmethod
    def build(
        cls,
        *,
        verifiability: Verifiability,
        risk_profile: RiskProfile,
        files: Iterable[str] | None,
        test_runner: str | None,
        impact_files: int | None,
    ) -> RouteInputs:
        normalized = None if files is None else tuple(sorted({_norm_path(f) for f in files if f.strip()}))
        if impact_files is not None and (isinstance(impact_files, bool) or impact_files < 0):
            raise ValueError(f"impact_files : entier positif attendu, reçu {impact_files!r}")
        runner = test_runner.strip() if test_runner and test_runner.strip() else None
        return cls(verifiability, risk_profile, normalized, runner, impact_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifiability": self.verifiability.value,
            "risk_profile": self.risk_profile.value,
            "files": None if self.files is None else list(self.files),
            "test_runner": self.test_runner,
            "impact_files": self.impact_files,
        }


def _max_risk(*risks: RiskProfile) -> RiskProfile:
    return max(risks, key=_RISK_ORDER.index)


def inputs_for(
    *,
    title: str,
    acceptance: Sequence[str],
    expected_evidence: Sequence[str] = (),
    has_checks: bool,
    declared_risk: RiskProfile | None = None,
    description: str = "",
    files: Iterable[str] | None,
    test_runner: str | None,
    impact_files: int | None,
) -> RouteInputs:
    """Les entrées d'une tâche décrite par son texte, dérivées comme le ledger les dérive.

    La classe vient de :func:`classify_criteria` avec la même remontée que
    ``classify`` : sans commande déclarée, une V0 n'est qu'une promesse (V1).
    Le risque est le plus haut de l'intake (titre + description) et du risque
    déclaré : l'intake ne peut que monter un risque, jamais le baisser.
    """
    klass = classify_criteria((*acceptance, *expected_evidence))
    if klass is Verifiability.V0 and not has_checks:
        klass = Verifiability.V1
    text = f"{title}\n{description}".strip()
    risk = MissionIntakeService().analyze(IntakeRequest(raw_text=text)).risk_profile
    if declared_risk is not None:
        risk = _max_risk(risk, declared_risk)
    return RouteInputs.build(
        verifiability=klass, risk_profile=risk, files=files, test_runner=test_runner, impact_files=impact_files,
    )


# ── Suggestion ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class _Fired:
    rule: str
    floor: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "floor": ROUTES[self.floor].name, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class RouteSuggestion:
    route: Route
    inputs: RouteInputs
    fired: tuple[_Fired, ...]
    limits: tuple[str, ...]

    @property
    def inputs_digest(self) -> str:
        """Empreinte des entrées ET des seuils : changer un seuil change l'empreinte."""
        material = {"inputs": self.inputs.to_dict(), "indices": {i.name: i.value for i in INDICES}}
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "route": self.route.name,
            "test_depth": self.route.test_depth.value,
            "test_depth_meaning": _DEPTH_MEANING[self.route.test_depth],
            "steps": list(self.route.steps),
            "human_turns": self.route.human_turns,
            "inputs": self.inputs.to_dict(),
            "inputs_digest": self.inputs_digest,
            "fired": [f.to_dict() for f in self.fired],
            "limits": list(self.limits),
            "indices": [i.to_dict() for i in INDICES],
        }


def suggest(inputs: RouteInputs) -> RouteSuggestion:
    """La route des entrées : le maximum des planchers de chaque règle."""
    fired: list[_Fired] = []
    limits: list[str] = []

    klass_floor = _CLASS_FLOOR[inputs.verifiability]
    if klass_floor:
        fired.append(_Fired("verifiability", klass_floor, inputs.verifiability.value))

    risk_floor = _RISK_FLOOR[inputs.risk_profile]
    if risk_floor:
        fired.append(_Fired("risk_profile", risk_floor, inputs.risk_profile.value))

    files_wide = _INDEX["files_wide"].value
    if inputs.files is None:
        fired.append(_Fired("files_unknown", 1, "fichiers touchés inconnus : le changement n'est pas borné"))
    else:
        if len(inputs.files) > files_wide:
            fired.append(_Fired("files_wide", 1, f"{len(inputs.files)} fichiers > {files_wide}"))
        for label, pattern in _GUARDED_PATHS:
            hits = [f for f in inputs.files if pattern.search(f)]
            if hits:
                fired.append(_Fired(f"guarded_path:{label}", 3, ", ".join(hits)))

    impact_wide = _INDEX["impact_wide"].value
    if inputs.impact_files is None:
        limits.append("impact non mesuré (importeurs directs) : il n'a pas pu monter la route")
    elif inputs.impact_files > impact_wide:
        fired.append(_Fired("impact_wide", 1, f"{inputs.impact_files} importeurs directs > {impact_wide}"))

    route = ROUTES[max((f.floor for f in fired), default=0)]
    if inputs.test_runner is None and route.test_depth is not DepthLevel.FLOOR:
        limits.append(
            f"aucune commande de test résolue (needs.commands.test-runner) : la profondeur "
            f"« {route.test_depth.value} » ne pourra pas être atteinte en l'état"
        )
    if any(not i.calibrated for i in INDICES):
        limits.append("seuils non calibrés sur des runs réels (voir indices[].calibrated)")
    return RouteSuggestion(route=route, inputs=inputs, fired=tuple(fired), limits=tuple(limits))


def render_json(suggestion: RouteSuggestion) -> str:
    """La sortie canonique : mêmes entrées, mêmes octets."""
    return json.dumps(suggestion.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# ── Profondeur de test ──────────────────────────────────────────────────────

_TEST_RUNNERS: tuple[tuple[str, ...], ...] = (
    ("pytest",), ("py.test",), ("tox",), ("nox",), ("unittest",), ("go", "test"), ("cargo", "test"),
    ("cargo", "nextest"), ("npm", "test"), ("npm", "run", "test"), ("pnpm", "test"), ("pnpm", "run", "test"),
    ("yarn", "test"), ("bun", "test"), ("deno", "test"), ("jest",), ("vitest",), ("mocha",),
    ("dotnet", "test"), ("mvn", "test"), ("gradle", "test"), ("./gradlew", "test"), ("make", "test"),
    ("ctest",), ("phpunit",), ("rspec",), ("mix", "test"), ("bats",),
)
_STATIC_TOOLS: tuple[tuple[str, ...], ...] = (
    ("ruff",), ("mypy",), ("pyright",), ("flake8",), ("pylint",), ("eslint",), ("tsc",), ("biome",),
    ("cargo", "clippy"), ("golangci-lint",), ("go", "vet"), ("shellcheck",), ("hadolint",), ("yamllint",),
    ("npm", "run", "lint"), ("pnpm", "run", "lint"), ("yarn", "lint"), ("make", "lint"),
)
#: Préfixes qui lancent la vraie commande : on les retire avant de reconnaître l'outil.
_WRAPPERS: tuple[tuple[str, ...], ...] = (
    ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("hatch", "run"), ("pdm", "run"),
    ("pnpm", "exec"), ("npx",), ("bunx",), ("env",),
)
_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&", "|&"})
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON = re.compile(r"^python(\d+(\.\d+)?)?$")
#: Arguments qui n'exécutent rien : l'outil affiche une version ou une aide, ou liste sans lancer.
_INFO_FLAGS = frozenset({
    "--version", "-V", "--help", "-h", "--collect-only", "--co", "--dry-run",
    # pytest : prépare ou liste sans exécuter le moindre test.
    "--setup-only", "--setup-plan", "--fixtures", "--fixtures-per-test", "--markers",
    # ruff, flake8, pylint, bandit : le code de sortie est forcé à 0 quoi que l'outil trouve.
    "--exit-zero",
})


_PUNCTUATION = "();<>|&\n"
#: Un mot fait de ponctuation, découpé en ses opérateurs maximaux : le shell lit ``||(`` comme ``||``
#: puis ``(``, jamais comme un seul mot. Les redirections (``>``, ``>&``, ``&>``…) passent avant ``&``
#: et ``|`` pour ne pas être prises pour des opérateurs de chaîne.
_PUNCT_PIECE = re.compile(r"&>>|&>|<<<|<<|>>|>&|<&|>\||<>|&&|\|\||\|&|[;&|()<>\n]")


def _punctuation_pieces(token: str) -> list[str]:
    """Les opérateurs, parenthèses et redirections d'un mot de ponctuation, un saut de ligne valant ``;``.

    Un saut de ligne derrière un opérateur, un ``;`` ou une parenthèse ouvrante
    prolonge la commande : il ne sépare rien.
    """
    out: list[str] = []
    for piece in _PUNCT_PIECE.findall(token):
        if piece == "\n":
            if out and (out[-1] in _SHELL_OPERATORS or out[-1] == "("):
                continue
            piece = ";"
        out.append(piece)
    return out


def _tokens(cmd: str) -> list[str]:
    """Les mots de la commande ; un opérateur entre guillemets reste dans son mot."""
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=_PUNCTUATION)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        raw = list(lexer)
    except ValueError:
        raw = cmd.split()
    out: list[str] = []
    for token in raw:
        if token.strip(_PUNCTUATION):
            out.append(token)
        else:
            # Un mot de ponctuation fusionne ce que le shell lit séparément : ``||(``, ``;(``, ``\n(``.
            out.extend(_punctuation_pieces(token))
    return out


def _chain(cmd: str) -> list[tuple[str, list[str]]] | None:
    """La commande en segments ``(opérateur qui les précède, mots)``, ``None`` si elle est mal formée.

    Le premier segment est précédé de ``""``. Un ``;`` final est sans effet ;
    un autre opérateur final (``&``, ``||``…), un segment vide ou un opérateur
    en tête rendent la commande mal formée : on ne devine pas ce qu'elle lance.
    """
    segments: list[tuple[str, list[str]]] = [("", [])]
    for token in _tokens(cmd):
        if token in _SHELL_OPERATORS:
            if not segments[-1][1]:
                return None
            segments.append((token, []))
        else:
            segments[-1][1].append(token)
    if not segments[-1][1]:
        if segments[-1][0] != ";":
            return None
        segments.pop()
    return [(op, _strip(seg)) for op, seg in segments]


def _segments(cmd: str) -> list[list[str]]:
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    out: list[list[str]] = [[]]
    for token in tokens:
        if token in _SHELL_OPERATORS:
            out.append([])
        else:
            out[-1].append(token)
    return [_strip(seg) for seg in out if seg]


def _strip(tokens: list[str]) -> list[str]:
    seg = list(tokens)
    changed = True
    while seg and changed:
        changed = False
        while seg and _ENV_ASSIGN.match(seg[0]):
            seg.pop(0)
            changed = True
        if seg:
            seg[0] = seg[0].rsplit("/", 1)[-1] if not seg[0].startswith("./") else seg[0]
        for wrapper in _WRAPPERS:
            if tuple(seg[: len(wrapper)]) == wrapper:
                del seg[: len(wrapper)]
                changed = True
                break
        if len(seg) >= 2 and _PYTHON.match(seg[0]) and seg[1] == "-m":
            del seg[:2]
            changed = True
    return seg


def _starts(seg: list[str], prefixes: Iterable[tuple[str, ...]]) -> bool:
    return any(tuple(seg[: len(p)]) == p for p in prefixes if p)


def check_kinds(cmd: str, needs: Mapping[str, str | None] | None = None) -> frozenset[str]:
    """Ce qu'une commande vérifie : ``test``, ``static``, ou rien de reconnu.

    Reconnaît les outils courants et, d'abord, les commandes que le projet
    déclare (``test-runner``, ``lint``, ``typecheck``). Une commande composée
    (``a && b``) porte les natures de ses segments reliés par ``&&`` qui
    décident de son code de sortie ; un segment court-circuité (``true || pytest``),
    masqué (``pytest || true``), détaché (``pytest &``) ou qui n'affiche qu'une
    version ou une aide (``pytest --version``) ne vérifie rien.
    """
    declared = needs or {}
    test_prefixes = list(_TEST_RUNNERS)
    static_prefixes = list(_STATIC_TOOLS)
    for need_id, bucket in (("test-runner", test_prefixes), ("lint", static_prefixes), ("typecheck", static_prefixes)):
        command = declared.get(need_id)
        if command:
            for seg in _segments(command):
                bucket.append(tuple(seg))
    kinds: set[str] = set()
    for seg in _deciding_segments(cmd):
        if any(token in _INFO_FLAGS for token in seg):
            continue
        if _starts(seg, test_prefixes):
            kinds.add("test")
        if _starts(seg, static_prefixes):
            kinds.add("static")
    return frozenset(kinds)


def _deciding_segments(cmd: str) -> list[list[str]]:
    """Les segments dont le code de sortie décide de la commande, et qui ont sûrement tourné.

    Ce sont les segments finaux reliés par ``&&`` : le code de la commande est
    celui du dernier, et chaque ``&&`` exige que le précédent ait réussi. La
    chaîne ne compte que si elle démarre la commande ou suit un ``;`` : après un
    ``||``, un ``&`` ou un ``|``, rien ne garantit que son premier segment a
    tourné ni que son code compte (``true || pytest``, ``pytest &``,
    ``pytest || true``, ``pytest | tee``).
    """
    chain = _chain(cmd)
    if not chain:
        return []
    start = len(chain) - 1
    while start > 0 and chain[start][0] == "&&":
        start -= 1
    if chain[start][0] not in ("", ";"):
        return []
    return [seg for _, seg in chain[start:]]


def verified_commands(pack: EvidencePack | None, verdict: VerificationVerdict | None) -> tuple[tuple[str, bool], ...]:
    """Les commandes du dernier verdict ``task verify``, chacune avec son résultat.

    La commande vient de la pièce ``check-NN`` du pack (``uri = check:<cmd>``),
    le résultat du contrôle ``command-NN`` du verdict : ce qui a été lancé,
    pas ce que la tâche déclare aujourd'hui.
    """
    if pack is None or verdict is None:
        return ()
    results = {check.id: check.result for check in verdict.checks}
    out: list[tuple[str, bool]] = []
    for item in pack.items:
        if not item.id.startswith("check-") or not item.uri.startswith("check:"):
            continue
        result = results.get("command-" + item.id.removeprefix("check-"))
        out.append((item.uri.removeprefix("check:"), result is VerdictResult.PASSED))
    return tuple(out)


def reached_depth(
    commands: Iterable[tuple[str, bool]], needs: Mapping[str, str | None] | None = None,
) -> DepthLevel | None:
    """La profondeur effectivement atteinte : seules les commandes vertes comptent."""
    passed = [cmd for cmd, ok in commands if ok]
    if not passed:
        return None
    kinds: set[str] = set()
    for cmd in passed:
        kinds |= check_kinds(cmd, needs)
    if "test" in kinds and "static" in kinds:
        return DepthLevel.STRICT
    if "test" in kinds:
        return DepthLevel.STANDARD
    return DepthLevel.FLOOR


def depth_shortfall(
    required: str, commands: Iterable[tuple[str, bool]], needs: Mapping[str, str | None] | None = None,
) -> str | None:
    """``None`` si la profondeur exigée est atteinte ; sinon, ce qui manque.

    Un niveau exigé inconnu (écrit par une version plus récente, ou à la main)
    est un refus : ne pas savoir mesurer n'est pas une autorisation.
    """
    try:
        wanted = DepthLevel(required)
    except ValueError:
        accepted = ", ".join(d.value for d in _DEPTH_ORDER)
        return f"profondeur de test inconnue {required!r} (attendu : {accepted})"
    reached = reached_depth(commands, needs)
    if reached is not None and _DEPTH_ORDER.index(reached) >= _DEPTH_ORDER.index(wanted):
        return None
    got = reached.value if reached is not None else "aucune (pas de commande verte au dernier verdict)"
    return f"profondeur exigée « {wanted.value} » ({_DEPTH_MEANING[wanted]}), atteinte : {got}"


# ── Entrées mesurées depuis le projet ───────────────────────────────────────

def changed_files(project_root: Path, base: str) -> tuple[str, ...]:
    """Fichiers modifiés par rapport à *base* (arbre de travail compris), via git.

    Lève :class:`RuntimeError` si git refuse : des fichiers demandés et non
    obtenus ne deviennent pas une liste vide.
    """
    proc = subprocess.run(
        ["git", "-C", str(project_root), "diff", "--name-only", base, "--"],
        capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git diff {base} : code {proc.returncode}")
    return tuple(line for line in proc.stdout.splitlines() if line.strip())


def _module_of(path: Path, source: Path) -> str | None:
    try:
        parts = list(path.relative_to(source).with_suffix("").parts)
    except ValueError:
        return None
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or None


def _imported_names(node: ast.AST, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = node.module or ""
    if node.level:
        anchor = package.split(".") if package else []
        anchor = anchor[: max(len(anchor) - (node.level - 1), 0)]
        base = ".".join([*anchor, base] if base else anchor)
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def import_fan_in(project_root: Path, files: Iterable[str]) -> int | None:
    """Fichiers ``.py`` qui importent directement un ``.py`` touché ; ``None`` si rien à mesurer.

    Lu par ``ast`` sur ``src/`` (ou la racine), importeurs directs seulement
    (profondeur 1), imports relatifs résolus. Le code graph du kit n'est pas
    employé : ses arêtes d'import ne pointent que sur le paquet de tête
    (``to_node = "grimoire"``), et son ``impact_query`` rendrait 0 pour tout
    module — un zéro mesuré qui serait faux. Le résultat ne dépend que des
    sources lues : il est rejouable.
    """
    from grimoire.codegraph.parser import DEFAULT_EXCLUDE

    source = (project_root / "src" if (project_root / "src").is_dir() else project_root).resolve()
    touched: dict[str, Path] = {}
    for rel in files:
        if rel.endswith(".py"):
            path = (project_root / rel).resolve()
            module = _module_of(path, source)
            if module:
                touched[module] = path
    if not touched:
        return None
    touched_paths = set(touched.values())
    importers = 0
    for py in sorted(source.rglob("*.py")):
        if py in touched_paths or any(part in DEFAULT_EXCLUDE for part in py.relative_to(source).parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        module = _module_of(py, source) or ""
        package = module if py.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(tree):
            names = _imported_names(node, package)
            if any(n in touched or any(n.startswith(t + ".") for t in touched) for n in names):
                importers += 1
                break
    return importers
