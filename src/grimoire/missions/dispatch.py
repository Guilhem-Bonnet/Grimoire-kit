"""``grimoire task dispatch`` — cascade par classe de vérifiabilité (issue #323).

Le lot 3 (#311) attend le moteur de flow (#204) pour orchestrer un run
multi-étapes ; en attendant, la topologie hybride de l'épic — l'hôte tient
la session, le kit remet une tranche de travail à un exécuteur puis applique
le gate — peut déjà se livrer au niveau d'une tâche unique, en branchant deux
briques déjà posées : la classe de vérifiabilité (#309, ``verifiability.py``)
et le registre par palier de coût (#310, ``providers/``).

Le principe qui gouverne tout ce module : **la classe décide qui a le droit
d'appeler, jamais le contraire.** Une tâche V2 (aucun verdict mécanique ni
revue reconnue) est refusée avant tout calcul — déléguer un travail que rien
ne sait juger produirait un vert qui ne prouve rien. Une tâche V0 peut
cascader depuis le palier le moins cher ; une tâche V1, seulement à partir de
``mid`` (le jugement humain qui suit un jour justifie un modèle plus capable
dès le premier essai), et son vert n'est jamais une fermeture — seulement un
passage en revue (``NEEDS_VERIFICATION``), parce que le check mécanique n'est
ici qu'un indice, pas le verdict qu'une tâche V1 exige réellement.

Chaque tentative — qu'elle échoue à l'appel (429, timeout) ou échoue au check
— laisse un événement ``task.dispatched`` dans le Mission Ledger : c'est
l'historique brut dont le lot 4 (#312, budget et coûts) a besoin, et il doit
survivre même quand la cascade entière finit rouge.

Constat de la session du 2026-09-08 (sous-issue de #307) : des dispatchs
verts aux tests ont quand même dû être corrigés par relecture forte, sur des
diffs qui touchaient des surfaces où une erreur de jugement coûte cher (CLI,
MCP, exports publics...). La classe de vérifiabilité dit qui a le droit de
produire ; la **classe de relisibilité** (#327) dit quoi relire une fois le
vert obtenu : un diff qui touche une surface sensible est ``review_required``,
le reste ``review_optional``.

Même constat, autre angle : les trois corrections apportées ce jour-là ont
été trouvées exactement là où l'ouvrier délégué déclarait douter en prose.
Les **incertitudes déclarées** (#328) rendent ce canal d'escalade lisible par
un programme : le prompt demande à l'ouvrier de terminer par un bloc JSON
délimité, que le dispatch extrait et stocke plutôt que de laisser un
relecteur humain espérer tomber dessus au bon endroit dans une sortie longue.

Backend
-------
La logique pure de ce module (chaîne de paliers, rendu d'invocation, analyse
de la sortie d'un ouvrier, classification de revue, verdict final d'une
cascade) et celle de ``providers.routing.candidates`` (ordre et filtrage par
refroidissement des fournisseurs) ont un second port, optionnel, en Rust
compilé par PyO3 (``rust/grimoire-dispatch-core/``, issue #354, cinquième
port du kit). L'implémentation Python ci-dessous reste la référence et la
seule garantie de fonctionner : ``grimoire_dispatch_core`` n'est jamais
installé par une dépendance du paquet publié, et si son import échoue tout
retombe silencieusement sur le chemin Python pur.

``GRIMOIRE_DISPATCH_BACKEND`` (``auto`` par défaut) force le choix :
``"python"`` ignore le module compilé même présent, ``"rust"`` l'exige et
lève :class:`~grimoire.core.exceptions.GrimoireMissionError` s'il est
absent. Voir ``tests/unit/test_dispatch_rust_parity.py``.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.missions.dispatch_history import recommend_start_tier
from grimoire.missions.schemas import TaskState
from grimoire.missions.verifiability import Verifiability, classify
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS, ProviderSpec
from grimoire.providers.routing import candidates as provider_candidates
from grimoire.providers.state import record_failure, record_success

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

try:
    import grimoire_dispatch_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_dispatch_core`` module is importable.

    Purely informational (tests, diagnostics) — every delegating function
    below resolves its own backend fresh via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend this call should use.

    Reads ``GRIMOIRE_DISPATCH_BACKEND`` fresh every time rather than once at
    import time, so tests can flip it with ``monkeypatch.setenv`` around a
    single call.
    """
    override = os.environ.get("GRIMOIRE_DISPATCH_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireMissionError(
                "GRIMOIRE_DISPATCH_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_dispatch_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-dispatch-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireMissionError(f"GRIMOIRE_DISPATCH_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


__all__ = [
    "CHECK_TIMEOUT_S",
    "DEFAULT_CALL_TIMEOUT_S",
    "DEFAULT_REVIEW_SURFACES",
    "CheckResult",
    "DispatchAttempt",
    "DispatchReport",
    "Uncertainty",
    "agent_declared_context",
    "build_prompt",
    "render_invocation",
    "run_dispatch",
    "rust_backend_available",
    "start_tier_for",
]

#: Palier de départ de la cascade selon la classe de vérifiabilité. V2 n'a pas
#: d'entrée : ``run_dispatch`` refuse avant même de consulter cette table.
_START_TIER: dict[Verifiability, str] = {
    Verifiability.V0: "cheap",
    Verifiability.V1: "mid",
}

#: Timeout par défaut d'un appel fournisseur — configurable (``--timeout``) :
#: un modèle fort peut légitimement prendre plus longtemps qu'un modèle cheap.
DEFAULT_CALL_TIMEOUT_S = 600.0

#: Timeout de chaque ``--check`` — fixe, non exposé en option : c'est
#: l'utilisateur qui écrit la commande, il en connaît le coût attendu, et une
#: valeur par tâche ouvrirait un réglage de plus sans bénéfice net ici.
CHECK_TIMEOUT_S = 600.0

#: Motifs d'échec d'appel reconnus dans la sortie, indépendamment du code de
#: sortie — un fournisseur peut répondre 0 et pourtant décrire un 429 dans son
#: propre format de sortie (CLI headless qui avale l'erreur HTTP).
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "overloaded", "quota")

#: Surfaces sensibles par défaut (issue #327) — un diff qui en touche une
#: exige une relecture forte même si les checks mécaniques sont au vert.
#: Chaque motif est un glob ``fnmatch`` appliqué au chemin relatif rendu par
#: ``git diff --name-only``. Surchargeable projet par projet via
#: ``_grimoire/standard/orchestration-policy.yaml`` (clé ``review_surfaces``).
DEFAULT_REVIEW_SURFACES: tuple[str, ...] = (
    # Motifs génériques, pas l'arborescence du kit : le dispatch tourne dans
    # le projet de l'utilisateur, dont on ne connaît pas les chemins. Un
    # projet surcharge `review_surfaces` s'il nomme ses surfaces autrement.
    "*/__init__.py",  # exports publics d'un paquet
    "*/cli/*",  # surfaces d'entrée : ligne de commande
    "*/mcp/*",  # surfaces d'entrée : outils MCP
    "*/api/*",  # surfaces d'entrée : API
    "*schema*",  # schémas, quel que soit le langage
    "framework/agentic-standard/*",  # templates du standard (kit)
    "_grimoire/standard/*",  # instances du standard (projet enrôlé)
    "*/verifiability.py",  # vocabulaire de décision du routage
    "*/policies/*",  # politiques
    "*/security/*",  # sécurité
    "*/hooks/*",  # hooks d'hôte
)

#: Chemin relatif de la politique d'orchestration — même fichier que celui lu
#: par ``core.agentic_standard`` et ``standard_checks``, redéclaré ici pour ne
#: pas tirer tout ``agentic_standard`` dans un module qui ne fait que lire une
#: clé optionnelle.
_ORCHESTRATION_POLICY_FILE = STANDARD_DIR / "orchestration-policy.yaml"

#: Bloc de fin de prompt (issue #328) : le canal d'escalade le moins cher qui
#: existe est l'incertitude que l'ouvrier déclare lui-même — encore faut-il
#: qu'il la mette dans un format qu'un programme, pas seulement un relecteur
#: humain, sait retrouver et compter.
_UNCERTAINTIES_INSTRUCTION = (
    "\nAvant de conclure, termine ta réponse par un bloc délimité :\n"
    "```grimoire-uncertainties\n"
    '[{"where": "fichier ou zone concernée", "what": "ce dont tu doutes", '
    '"why": "pourquoi tu doutes"}]\n'
    "```\n"
    "Liste JSON, vide (`[]`) si tu n'as aucune incertitude à déclarer — jamais "
    "de prose à la place du JSON, jamais le bloc omis par excès de confiance."
)

_UNCERTAINTIES_BLOCK_RE = re.compile(r"```grimoire-uncertainties\s*\n(.*?)```", re.DOTALL)


def _reject_non_standard(token: str) -> float:
    """``parse_constant`` de ``json.loads`` : refuse les jetons hors RFC 8259.

    Par défaut, ``json.loads`` de CPython accepte les jetons non standards
    ``NaN``/``Infinity``/``-Infinity`` — une extension que le cœur Rust de ce
    port (``rust/grimoire-dispatch-core/``, strict RFC 8259 via
    ``serde_json``) n'accepte pas nativement. La règle du port est que Rust
    est l'oracle : c'est ce module qui s'aligne sur le comportement strict,
    pas l'inverse. Toute occurrence de ces jetons — y compris ailleurs que
    dans le champ effectivement lu — lève ``ValueError``, rattrapée comme un
    JSON invalide par chaque appelant ci-dessous : un document qui en porte
    un devient aussi peu exploitable des deux côtés que s'il était mal formé.
    """
    raise ValueError(f"jeton JSON hors RFC 8259 non accepté : {token}")


def start_tier_for(verifiability: Verifiability) -> str | None:
    """Le premier palier autorisé pour cette classe — ``None`` si aucun (V2)."""
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        rust_tier = _rust_core.start_tier_for_py(verifiability.value)
        return str(rust_tier) if rust_tier is not None else None
    return _START_TIER.get(verifiability)


def _tier_chain(start_tier: str, max_tier: str | None) -> tuple[str, ...]:
    """Les paliers à essayer, dans l'ordre, entre *start_tier* et *max_tier*.

    Vide si *max_tier* est strictement en dessous de *start_tier* — la classe
    exige un palier que la borne posée par l'appelant interdit ; ``()`` le
    dit sans lever, à charge de l'appelant de le traduire en refus.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return tuple(_rust_core.tier_chain_py(start_tier, max_tier))
    start_idx = SUPPORTED_MODEL_TIERS.index(start_tier)
    end_idx = SUPPORTED_MODEL_TIERS.index(max_tier) if max_tier else len(SUPPORTED_MODEL_TIERS) - 1
    if end_idx < start_idx:
        return ()
    return SUPPORTED_MODEL_TIERS[start_idx : end_idx + 1]


def _read_declared_context(project_root: Path, paths: tuple[str, ...]) -> str:
    """Concatène le contenu des chemins déclarés par l'agent, dans l'ordre déclaré.

    Best-effort côté lecture (un fichier disparu entre la déclaration et le
    dispatch ne doit pas faire échouer la délégation), mais l'existence est
    déjà vérifiée à la construction de l'``AgentSpec`` (``collect.py``) — ce
    n'est un filet que pour la course, pas la garde principale.
    """
    blocs: list[str] = []
    for rel in paths:
        fpath = project_root / rel
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            continue
        blocs.append(f"### {rel}\n\n{text.strip()}")
    return "\n\n".join(blocs)


def agent_declared_context(project_root: Path, agent_name: str) -> tuple[str, ...]:
    """Les chemins que l'agent *agent_name* déclare comme son propre contexte.

    ``()`` si l'agent est introuvable ou ne déclare rien — dans les deux cas,
    ``build_prompt`` retombe sur le comportement d'avant #373, sans régression
    (issue #373, critère 2).
    """
    from grimoire.core.standard_state import is_standard_enrolled
    from grimoire.hosts.collect import collect_agents, collect_skills

    root = project_root.resolve()
    governed = is_standard_enrolled(root)
    skills = collect_skills(root, governed=governed)
    agents = collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    for agent in agents:
        if agent.name == agent_name:
            return agent.context
    return ()


def build_prompt(
    task: MissionTask, *, agent_context: tuple[str, ...] = (), project_root: Path | None = None
) -> str:
    """Le contrat autonome envoyé au fournisseur : ce qu'il doit faire, rien de plus.

    Volontairement plus étroit que le context bundle du standard
    (``build_context_bundle`` — board, mémoire, registres...) : un exécuteur
    délégué par cascade n'a besoin ni de la politique de mémoire du projet ni
    du registre de fournisseurs, seulement de ce que la tâche exige et de la
    garde qui rend le vert du check opposable — ne pas retoucher les
    vérifications, sous peine de rendre le verdict qui suit sans objet.

    *agent_context* (issue #373) ajoute ce que l'agent dispatché déclare comme
    son propre contexte (``AgentSpec.context``) — la seule chose que ce prompt
    admet en plus du contrat de la tâche. Absent (agent sans ``context:``
    déclaré, ou pas d'agent nommé), le prompt est identique à celui produit
    avant #373 : aucune régression pour ces agents.
    """
    lignes = [f"Tâche {task.id} : {task.title}"]
    if agent_context and project_root is not None:
        bloc = _read_declared_context(project_root, agent_context)
        if bloc:
            lignes.append(f"\nContexte déclaré de l'agent :\n\n{bloc}")
    if task.description:
        lignes.append(f"\nContexte : {task.description}")
    if task.acceptance:
        lignes.append("\nCritères d'acceptation :")
        lignes.extend(f"  - {c}" for c in task.acceptance)
    if task.expected_evidence:
        lignes.append("\nPreuve attendue :")
        lignes.extend(f"  - {e}" for e in task.expected_evidence)
    if task.guardrails:
        lignes.append("\nGarde-fous :")
        lignes.extend(f"  - {g}" for g in task.guardrails)
    lignes.append(
        "\nConsigne : réalise ce travail dans le dépôt courant. Ne modifie pas "
        "les commandes de vérification (`--check`) qui jugeront le résultat — "
        "leur code de sortie est le seul verdict qui compte ici."
    )
    prompt = "\n".join(lignes)
    return prompt + _UNCERTAINTIES_INSTRUCTION


def render_invocation(template: str, *, prompt: str, model: str) -> list[str]:
    """La commande à exécuter, ``{prompt}`` et ``{model}`` substitués token par token.

    ``shlex.split`` d'abord, substitution ensuite : le prompt devient le
    contenu d'UN argument de ``subprocess`` (``shell=False``), jamais un
    fragment de ligne de commande réinterprété par un shell. Une invocation
    mal écrite (``{prompt}`` absent) échouera à l'appel, pas silencieusement —
    ``choose``/``candidates`` a déjà écarté les fournisseurs sans
    ``invocation``, mais pas ceux dont le gabarit oublie le placeholder.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        try:
            return list(_rust_core.render_invocation_py(template, prompt, model))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    return [tok.replace("{prompt}", prompt).replace("{model}", model) for tok in shlex.split(template)]


def _looks_rate_limited(text: str) -> bool:
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return bool(_rust_core.looks_rate_limited_py(text))
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _extract_cost_usd(stdout: str) -> float | None:
    """``total_cost_usd`` si la sortie est un JSON qui le porte — ``None`` sinon.

    Best-effort : la plupart des fournisseurs headless ne rendent pas de JSON
    du tout, et ce n'est pas un échec — juste un coût qui restera inconnu pour
    cette tentative.

    Exclut explicitement les booléens et les nombres non finis (défaut trouvé
    en portant cette fonction vers Rust, issue #354) : ``isinstance(True,
    int)`` est vrai en Python, donc un ``total_cost_usd`` JSON ``true``/
    ``false`` était jusqu'ici accepté et coercé en ``1.0``/``0.0`` — un coût
    qui n'en est pas un. ``json.loads`` acceptait par ailleurs ``NaN``/
    ``Infinity``/``-Infinity`` (extension non-RFC 8259 de CPython, hors de ce
    que le cœur Rust strict accepte) : ``parse_constant=_reject_non_standard``
    les refuse désormais explicitement, où qu'ils apparaissent dans le
    document — un coût non fini n'est pas plus exploitable qu'un coût absent,
    et un ``1e400`` (littéral JSON valide mais hors bornes de ``float``)
    reste couvert par le contrôle ``math.isfinite`` ci-dessous.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        rust_cost = _rust_core.extract_cost_usd_py(stdout)
        return float(rust_cost) if rust_cost is not None else None
    try:
        data = json.loads(stdout, parse_constant=_reject_non_standard)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cost = data.get("total_cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return None
    cost = float(cost)
    return cost if math.isfinite(cost) else None


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    return yaml


def _review_surfaces(project_root: Path) -> tuple[str, ...]:
    """Les globs de surfaces sensibles — la surcharge du projet, sinon la liste par défaut.

    Best-effort et permissif : ``orchestration-policy.yaml`` sert d'abord au
    standard agentique, une clé absente ou un fichier illisible n'est pas une
    raison de faire échouer un dispatch — juste de retomber sur la liste que
    l'issue #327 fixe elle-même.
    """
    path = project_root / _ORCHESTRATION_POLICY_FILE
    if not path.is_file():
        return DEFAULT_REVIEW_SURFACES
    try:
        data = _yaml().load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError):
        return DEFAULT_REVIEW_SURFACES
    if not isinstance(data, dict):
        return DEFAULT_REVIEW_SURFACES
    surfaces = data.get("review_surfaces")
    if not isinstance(surfaces, list):
        return DEFAULT_REVIEW_SURFACES
    cleaned = tuple(str(item) for item in surfaces if str(item).strip())
    return cleaned or DEFAULT_REVIEW_SURFACES


def _matches_review_surface(path: str, surfaces: tuple[str, ...]) -> bool:
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return bool(_rust_core.matches_review_surface_py(path, list(surfaces)))
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in surfaces)


def _classify_review(project_root: Path) -> tuple[str, tuple[str, ...], str | None]:
    """Classe le diff courant — ``(review, review_files, review_note)`` (issue #327).

    ``git diff --name-only`` plutôt qu'une lecture du working tree : c'est
    exactement ce que le prochain relecteur verra. Un projet non versionné ne
    permet pas de calculer quoi que ce soit ; le déclarer ``review_required``
    par défaut inventerait une garantie qu'on ne peut pas tenir, donc
    ``review_optional`` avec une note plutôt qu'un silence trompeur.
    """
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only"], cwd=project_root, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return "review_optional", (), "projet non versionné (git indisponible) : relisibilité non calculée"
    if completed.returncode != 0:
        return "review_optional", (), "projet non versionné (pas un dépôt git) : relisibilité non calculée"
    files = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    surfaces = _review_surfaces(project_root)
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        required, matched_list = _rust_core.classify_review_py(list(files), list(surfaces))
        if required:
            return "review_required", tuple(matched_list), None
        return "review_optional", (), None
    matched = tuple(f for f in files if _matches_review_surface(f, surfaces))
    if matched:
        return "review_required", matched, None
    return "review_optional", (), None


@dataclass(frozen=True, slots=True)
class Uncertainty:
    """Une incertitude déclarée par l'ouvrier délégué (issue #328).

    Trois champs, tous exigés à l'extraction : un objet qui n'en porte pas un
    des trois n'est pas assez précis pour qu'un relecteur sache où regarder —
    autant l'ignorer avec un avertissement que le stocker à moitié vide.
    """

    where: str
    what: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"where": self.where, "what": self.what, "why": self.why}


def _uncertainties_search_text(stdout: str) -> str:
    """Où chercher le bloc : le champ ``result`` si *stdout* est un JSON qui le porte, sinon *stdout* brut.

    Un fournisseur headless qui rend un JSON enveloppe souvent la réponse
    texte de l'ouvrier sous ``result`` (même convention que ``total_cost_usd``
    plus haut) — le bloc délimité vit alors dedans, pas dans le JSON lui-même.

    ``parse_constant=_reject_non_standard`` (voir ``_extract_cost_usd``) :
    un ``NaN``/``Infinity``/``-Infinity`` ailleurs dans l'enveloppe JSON,
    même sans rapport avec ``result``, rend tout le document aussi peu
    exploitable qu'un JSON mal formé des deux côtés — le cœur Rust
    (``serde_json``, strict) rejetterait le document entier, pas seulement
    le jeton concerné.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return str(_rust_core.uncertainties_search_text_py(stdout))
    try:
        data = json.loads(stdout, parse_constant=_reject_non_standard)
    except (json.JSONDecodeError, ValueError):
        return stdout
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, str):
            return result
    return stdout


def _extract_uncertainties(stdout: str) -> tuple[tuple[Uncertainty, ...], tuple[str, ...]]:
    """Le bloc ``grimoire-uncertainties`` de *stdout*, jamais un échec (issue #328).

    Trois issues : bloc absent → vide, sans avertissement (l'ouvrier n'a rien
    à déclarer, ou ne connaît pas encore la convention — pas une anomalie à
    signaler à chaque dispatch) ; bloc présent mais illisible (JSON invalide,
    pas une liste) → vide, un avertissement ; bloc présent et lisible →
    chaque objet sans ``where``/``what``/``why`` est ignoré avec son propre
    avertissement, les autres sont gardés.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        rust_triples, rust_warnings = _rust_core.extract_uncertainties_py(stdout)
        rust_uncertainties = tuple(Uncertainty(where=w, what=wh, why=y) for (w, wh, y) in rust_triples)
        return rust_uncertainties, tuple(rust_warnings)
    match = _UNCERTAINTIES_BLOCK_RE.search(_uncertainties_search_text(stdout))
    if match is None:
        return (), ()
    body = match.group(1).strip()
    try:
        payload = json.loads(body, parse_constant=_reject_non_standard) if body else []
    except (json.JSONDecodeError, ValueError):
        return (), (f"bloc grimoire-uncertainties illisible (JSON invalide) : {body[:200]!r}",)
    if not isinstance(payload, list):
        return (), (f"bloc grimoire-uncertainties illisible (attendu une liste JSON) : {body[:200]!r}",)
    uncertainties: list[Uncertainty] = []
    warnings: list[str] = []
    for item in payload:
        if (
            isinstance(item, dict)
            and isinstance(item.get("where"), str)
            and isinstance(item.get("what"), str)
            and isinstance(item.get("why"), str)
        ):
            uncertainties.append(Uncertainty(where=item["where"], what=item["what"], why=item["why"]))
        else:
            warnings.append(f"incertitude ignorée (clés where/what/why manquantes ou non textuelles) : {item!r}")
    return tuple(uncertainties), tuple(warnings)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Le verdict d'une commande ``--check`` : verte ou non, rien d'autre à savoir."""

    cmd: str
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"cmd": self.cmd, "ok": self.ok}


@dataclass(frozen=True, slots=True)
class DispatchAttempt:
    """Une tentative de la cascade : un fournisseur, un palier, un verdict.

    ``verdict`` prend l'une de cinq valeurs : ``"green"`` (appel et checks
    au vert), ``"red"`` (appel réussi, un check au moins a échoué),
    ``"rate_limit"``, ``"timeout"`` ou ``"error"`` (l'appel lui-même a
    échoué — saturation, délai dépassé, ou panne locale du fournisseur ; le
    fournisseur suivant du même palier prend le relais, les checks ne
    tournent pas).

    ``review``/``review_files``/``review_note`` (#327) ne sont posés que pour
    la tentative verte — au plus une par cascade — car relire n'a de sens
    qu'une fois un résultat accepté. ``uncertainties``/``uncertainty_warnings``
    (#328) sont extraits pour toute tentative où l'appel a réussi, vert ou
    rouge : l'ouvrier peut avoir douté d'un travail que le check juge encore
    insuffisant.
    """

    attempt: int
    tier: str
    provider: str
    model: str
    exit_code: int | None
    duration_s: float
    checks: tuple[CheckResult, ...]
    verdict: str
    cost_usd: float | None
    review: str | None = None
    review_files: tuple[str, ...] = ()
    review_note: str | None = None
    uncertainties: tuple[Uncertainty, ...] = ()
    uncertainty_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "checks": [c.to_dict() for c in self.checks],
            "verdict": self.verdict,
            "cost_usd": self.cost_usd,
            "review": self.review,
            "review_files": list(self.review_files),
            "review_note": self.review_note,
            "uncertainties": [u.to_dict() for u in self.uncertainties],
            "uncertainty_warnings": list(self.uncertainty_warnings),
        }


#: Motif de refus posé avant toute tentative — la cascade n'a jamais appelé
#: personne. Distinct de la chaîne épuisée (des tentatives ont eu lieu, aucune
#: n'est allée au vert).
_REFUSAL_MESSAGES: dict[str, str] = {
    "v2": "classe de vérifiabilité V2 : aucun verdict ne peut juger ce travail, pas de délégation",
    "no_check": "au moins un `--check` est requis : la classe dit que le verdict est mécanique, la commande dit lequel",
    "no_tier": "aucun palier disponible entre le plancher de la classe et --max-tier",
    "no_provider": "aucun fournisseur invocable sur la chaîne de paliers prévue",
}


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Le résultat complet d'un ``run_dispatch`` — ce que le CLI affiche et sérialise."""

    task_id: str
    verifiability: str
    dry_run: bool
    planned_chain: tuple[str, ...]
    prompt: str
    attempts: tuple[DispatchAttempt, ...] = ()
    refusal: str | None = None
    transitioned_to: str | None = None
    transition_refused: str | None = None
    start_tier: str | None = None
    start_tier_reason: str | None = None

    @property
    def refusal_message(self) -> str | None:
        """Le motif de refus en clair — ``None`` si la cascade n'a pas été refusée."""
        if self.refusal is None:
            return None
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return str(_rust_core.refusal_message_py(self.refusal))
        return _REFUSAL_MESSAGES.get(self.refusal, self.refusal)

    @property
    def succeeded(self) -> bool:
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return bool(_rust_core.dispatch_succeeded_py([a.verdict for a in self.attempts]))
        return any(a.verdict == "green" for a in self.attempts)

    @property
    def exit_code(self) -> int:
        """0 vert, 1 chaîne épuisée, 2 refus — voir la surface de la commande."""
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return int(_rust_core.dispatch_exit_code_py(self.dry_run, self.refusal, self.succeeded))
        if self.dry_run:
            return 0
        if self.refusal is not None:
            return 2
        return 0 if self.succeeded else 1

    @property
    def review(self) -> str | None:
        """``review_required``/``review_optional`` de la dernière tentative — ``None`` s'il n'y en a aucune (#327)."""
        return self.attempts[-1].review if self.attempts else None

    @property
    def review_files(self) -> tuple[str, ...]:
        return self.attempts[-1].review_files if self.attempts else ()

    @property
    def review_note(self) -> str | None:
        return self.attempts[-1].review_note if self.attempts else None

    @property
    def uncertainties(self) -> tuple[Uncertainty, ...]:
        """Les incertitudes déclarées par la dernière tentative appelée (#328)."""
        return self.attempts[-1].uncertainties if self.attempts else ()

    @property
    def uncertainty_warnings(self) -> tuple[str, ...]:
        return self.attempts[-1].uncertainty_warnings if self.attempts else ()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "verifiability": self.verifiability,
            "dry_run": self.dry_run,
            "planned_chain": list(self.planned_chain),
            "prompt": self.prompt,
            "attempts": [a.to_dict() for a in self.attempts],
            "exit_code": self.exit_code,
            "review": self.review,
            "review_files": list(self.review_files),
            "review_note": self.review_note,
            "uncertainties": [u.to_dict() for u in self.uncertainties],
            "uncertainty_warnings": list(self.uncertainty_warnings),
            "start_tier": self.start_tier,
            "start_tier_reason": self.start_tier_reason,
        }
        if self.refusal is not None:
            data["refusal"] = self.refusal
            data["refusal_reason"] = self.refusal_message
        if self.transitioned_to is not None:
            data["transitioned_to"] = self.transitioned_to
        if self.transition_refused is not None:
            data["transition_refused"] = self.transition_refused
        return data


def _run_provider_call(
    project_root: Path, provider: ProviderSpec, model: str, prompt: str, *, call_timeout: float
) -> tuple[int | None, str, str, float, str | None]:
    """Exécute l'invocation rendue ; rend (code, stdout, stderr, durée, kind d'échec).

    *kind* vaut ``"timeout"``, ``"rate_limit"`` (sortie évoquant un 429 ou
    une limite), ``"error"`` (code non nul sans trace de limite) ou ``None``
    (appel réussi). ``invocation`` est
    garanti non vide par ``candidates()`` en amont ; ``shlex.split`` d'une
    chaîne vide rendrait de toute façon une commande vide, refusée par
    ``subprocess`` — pas de garde supplémentaire nécessaire ici.
    """
    argv = render_invocation(provider.invocation or "", prompt=prompt, model=model)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv, cwd=project_root, capture_output=True, text=True, timeout=call_timeout, shell=False
        )
    except subprocess.TimeoutExpired:
        return None, "", "", time.monotonic() - started, "timeout"
    duration = time.monotonic() - started
    # Une saturation (429, quota) et une panne locale (CLI absent, script qui
    # plante) n'ont pas le même sens pour l'état runtime : la première dit que
    # le fournisseur est bon mais occupé, la seconde qu'il est cassé ici. Les
    # deux refroidissent, mais sous un nom distinct, pour que `providers
    # status` et l'historique du lot 4 ne confondent pas les deux.
    if _looks_rate_limited(completed.stdout) or _looks_rate_limited(completed.stderr):
        kind = "rate_limit"
    elif completed.returncode != 0:
        kind = "error"
    else:
        kind = None
    return completed.returncode, completed.stdout, completed.stderr, duration, kind


def _run_checks(checks: tuple[str, ...], *, project_root: Path) -> tuple[CheckResult, ...]:
    """Chaque ``--check`` en ``shell=True`` — c'est l'utilisateur qui l'a écrit.

    Toutes tournent, même après un premier échec : le rapport final doit
    montrer l'état complet, pas seulement la première commande rouge.
    """
    results: list[CheckResult] = []
    for cmd in checks:
        try:
            # shell=True est délibéré : `cmd` est la commande de vérification que
            # l'utilisateur a lui-même écrite via `--check`, pas une entrée non
            # fiable — même contrat que `subprocess` ailleurs pour les checks de
            # gate. Le rendu du fournisseur, lui, reste `shell=False` (voir
            # `_run_provider_call`) car le prompt qui le traverse n'est pas écrit
            # par l'utilisateur au clavier de cette commande.
            completed = subprocess.run(cmd, cwd=project_root, shell=True, timeout=CHECK_TIMEOUT_S)  # noqa: S602
            ok = completed.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        results.append(CheckResult(cmd=cmd, ok=ok))
    return tuple(results)


def _dispatch_event_payload(
    attempt: DispatchAttempt,
    task: MissionTask,
    verifiability: Verifiability,
    start_tier: str,
    start_tier_reason: str,
) -> dict[str, Any]:
    """Le payload d'un événement ``task.dispatched`` — le type et la classe embarqués (lot 4, #312).

    ``task_type``/``verifiability`` sont écrits ici plutôt que re-dérivés au
    moment de la lecture : ``dispatch_history`` n'a alors ni besoin de
    recharger la tâche (qui peut avoir été close, voire disparue), ni de
    supposer que ses critères d'acceptation n'ont pas changé depuis. Un
    événement plus ancien, écrit avant ce lot, n'a pas ces clés —
    ``dispatch_history`` l'ignore sans échouer plutôt que d'inventer une
    classe qui n'a jamais été observée.
    """
    payload = attempt.to_dict()
    payload["task_id"] = task.id
    payload["task_type"] = task.type.value
    payload["verifiability"] = verifiability.value
    payload["start_tier"] = start_tier
    payload["start_tier_reason"] = start_tier_reason
    return payload


def run_dispatch(
    service: TaskService,
    task_id: str,
    *,
    checks: tuple[str, ...] = (),
    max_tier: str | None = None,
    start_tier: str | None = None,
    provider_id: str | None = None,
    dry_run: bool = False,
    call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
    actor: str = "cli",
    agent: str | None = None,
    project_root: Path | None = None,
) -> DispatchReport:
    """Cascade la tâche *task_id* à travers les paliers de fournisseurs.

    *agent* (issue #373) nomme l'agent dispatché : son ``context`` déclaré
    (frontmatter de son fichier, résolu par :func:`agent_declared_context`)
    entre dans le prompt en plus du contrat de la tâche, rien de plus. Sans
    *agent*, ou pour un agent qui ne déclare aucun contexte, le prompt est
    identique à celui produit avant #373.

    Refuse avant tout appel si la classe est V2 ou si ``checks`` est vide.
    Sinon, essaie chaque palier de la chaîne, du palier de départ au plus
    cher : à chaque palier, chaque fournisseur disponible est tenté jusqu'à
    un appel qui réussit ; un check rouge fait passer au palier suivant (le
    fournisseur n'est pas en cause, le résultat l'est), un échec d'appel
    (429, timeout) fait passer au fournisseur suivant du même palier. Chaque
    tentative — y compris un échec d'appel — laisse un événement
    ``task.dispatched`` au ledger.

    Le palier de départ vient, par défaut, de l'historique des dispatchs
    passés pour ce couple (type de tâche, classe) — :mod:`dispatch_history`,
    issue #312 : ``cheap``/``mid`` pour V0/V1 tant que rien ne le contredit,
    ajusté quand un couple escalade trop souvent depuis son palier habituel,
    ou redescendu quand il ne le fait plus. *start_tier* (``--start-tier``)
    court-circuite entièrement cette recommandation — l'opérateur qui la
    fournit sait mieux que l'historique pour ce dispatch précis.
    """
    task = service.require(task_id)
    verifiability = classify(task)
    declared_context = agent_declared_context(project_root, agent) if agent and project_root else ()
    prompt = build_prompt(task, agent_context=declared_context, project_root=project_root)

    floor = start_tier_for(verifiability)
    if floor is None:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="v2",
        )

    if start_tier is not None:
        # L'option explicite ne peut que monter : le plancher de la classe est
        # la seule garantie qu'une V1 ne part pas sur un ouvrier sans juge.
        # Un `--start-tier cheap` sur une V1 est donc relevé, et dit pourquoi.
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            chosen_tier, was_raised = _rust_core.resolve_explicit_start_tier_py(floor, start_tier)
        else:
            was_raised = SUPPORTED_MODEL_TIERS.index(start_tier) < SUPPORTED_MODEL_TIERS.index(floor)
            chosen_tier = floor if was_raised else start_tier
        if was_raised:
            start_tier_reason = (
                f"palier explicite `{start_tier}` relevé au plancher `{floor}` de la classe {verifiability.value}"
            )
        else:
            start_tier_reason = "palier de départ explicite (--start-tier)"
    else:
        chosen_tier, start_tier_reason = recommend_start_tier(
            service.ledger, task_type=task.type.value, verifiability=verifiability.value, floor=floor
        )

    if not checks:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="no_check",
            start_tier=chosen_tier,
            start_tier_reason=start_tier_reason,
        )

    chain = _tier_chain(chosen_tier, max_tier)
    if not chain:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="no_tier",
            start_tier=chosen_tier,
            start_tier_reason=start_tier_reason,
        )

    if dry_run:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=True,
            planned_chain=chain,
            prompt=prompt,
            start_tier=chosen_tier,
            start_tier_reason=start_tier_reason,
        )

    root = service.project_root
    attempts: list[DispatchAttempt] = []
    attempt_no = 0
    for tier in chain:
        # Un fournisseur sans `invocation` déclarée n'est jamais candidat ici :
        # `candidates()` sert aussi `providers status`, où un fournisseur sans
        # commande d'appel reste une information utile à afficher — seule la
        # cascade, qui doit réellement l'exécuter, a besoin de l'exclure.
        tier_candidates = tuple(p for p in provider_candidates(root, tier) if p.invocation)
        if provider_id:
            tier_candidates = tuple(p for p in tier_candidates if p.id == provider_id)
        tier_settled = False
        for provider in tier_candidates:
            model = provider.models_for_tier(tier)[0].id
            attempt_no += 1
            code, stdout, _stderr, duration, failure_kind = _run_provider_call(
                root, provider, model, prompt, call_timeout=call_timeout
            )
            if failure_kind is not None:
                record_failure(root, provider.id, kind=failure_kind)
                attempt = DispatchAttempt(
                    attempt=attempt_no,
                    tier=tier,
                    provider=provider.id,
                    model=model,
                    exit_code=code,
                    duration_s=duration,
                    checks=(),
                    verdict=failure_kind,
                    cost_usd=None,
                )
                attempts.append(attempt)
                service.ledger.append_event(
                    "task.dispatched",
                    task_id,
                    "task",
                    actor,
                    _dispatch_event_payload(attempt, task, verifiability, chosen_tier, start_tier_reason),
                )
                continue  # fournisseur suivant, même palier

            record_success(root, provider.id)
            check_results = _run_checks(checks, project_root=root)
            green = all(c.ok for c in check_results)
            # La relecture ne se pose qu'une fois le résultat accepté (#327) —
            # un check rouge n'a rien produit qu'on ait besoin de relire.
            review, review_files, review_note = _classify_review(root) if green else (None, (), None)
            # L'incertitude déclarée, elle, vaut pour tout appel qui a répondu,
            # vert ou rouge (#328) : l'ouvrier peut avoir douté d'un travail
            # que le check juge encore insuffisant.
            uncertainties, uncertainty_warnings = _extract_uncertainties(stdout)
            attempt = DispatchAttempt(
                attempt=attempt_no,
                tier=tier,
                provider=provider.id,
                model=model,
                exit_code=code,
                duration_s=duration,
                checks=check_results,
                verdict="green" if green else "red",
                cost_usd=_extract_cost_usd(stdout),
                review=review,
                review_files=review_files,
                review_note=review_note,
                uncertainties=uncertainties,
                uncertainty_warnings=uncertainty_warnings,
            )
            attempts.append(attempt)
            service.ledger.append_event(
                "task.dispatched",
                task_id,
                "task",
                actor,
                _dispatch_event_payload(attempt, task, verifiability, chosen_tier, start_tier_reason),
            )
            tier_settled = True
            break  # appel réussi : ce palier a son verdict, vert ou rouge

        if tier_settled and attempts[-1].verdict == "green":
            break  # succès : la cascade s'arrête ici
        # rouge, ou palier épuisé sans appel réussi : palier suivant

    if not attempts:
        # Aucun fournisseur candidat sur toute la chaîne : la cascade n'a
        # jamais appelé personne. Distinct d'une chaîne épuisée (des
        # tentatives ont eu lieu, aucune n'est allée au vert) — c'est un
        # refus, pas un échec de la délégation elle-même.
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=False,
            planned_chain=chain,
            prompt=prompt,
            refusal="no_provider",
            start_tier=chosen_tier,
            start_tier_reason=start_tier_reason,
        )

    report = DispatchReport(
        task_id=task_id,
        verifiability=verifiability.value,
        dry_run=False,
        planned_chain=chain,
        prompt=prompt,
        attempts=tuple(attempts),
        start_tier=chosen_tier,
        start_tier_reason=start_tier_reason,
    )
    if not report.succeeded:
        return report
    if verifiability is not Verifiability.V1:
        return report
    try:
        service.transition(
            task_id,
            TaskState.NEEDS_VERIFICATION,
            actor,
            reason="grimoire task dispatch : cascade verte, vérification humaine requise (V1)",
        )
    except GrimoireMissionError as exc:
        return DispatchReport(
            task_id=report.task_id,
            verifiability=report.verifiability,
            dry_run=False,
            planned_chain=report.planned_chain,
            prompt=report.prompt,
            attempts=report.attempts,
            transition_refused=str(exc),
            start_tier=report.start_tier,
            start_tier_reason=report.start_tier_reason,
        )
    return DispatchReport(
        task_id=report.task_id,
        verifiability=report.verifiability,
        dry_run=False,
        planned_chain=report.planned_chain,
        prompt=report.prompt,
        attempts=report.attempts,
        transitioned_to=TaskState.NEEDS_VERIFICATION.value,
        start_tier=report.start_tier,
        start_tier_reason=report.start_tier_reason,
    )
