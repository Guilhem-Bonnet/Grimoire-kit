"""Packs de règles par stack : chaque règle est vérifiée ou déclarée advisory (W6-08).

Une règle de bonne pratique livrée en prose n'engage personne : rien ne dit si le
code du projet la respecte. Un pack est une liste de règles de la grammaire de
:mod:`grimoire.core.layered_rules` où **chaque règle porte soit un vérificateur
mécanique** (:mod:`grimoire.core.stack_pack_verifiers`), **soit** ``advisory:
true``. Les règles advisory sont comptées à part, jamais confondues avec les
règles contrôlées.

Schéma ``grimoire-stack-rule-pack/v1`` (fichier ``<id>.yaml`` dans
``grimoire/data/stack-packs/``) ::

    $schema: grimoire-stack-rule-pack/v1
    id: python                  # identique au nom du fichier
    stack: python
    title: ...
    rules:
      - rule: NEVER use bare-except      # forme canonique de la grammaire
        rationale: ...
        verifier: {kind: python_ast, check: bare_except, paths: ["**/*.py"]}
      - rule: ALWAYS annotate public-functions
        advisory: true
        rationale: ...

Un pack est **refusé au chargement** (donc par la CI, qui charge tous les packs
livrés) quand : une règle n'a ni vérificateur ni ``advisory: true``, ou les deux ;
plus de la moitié de ses règles sont advisory ; une règle n'est pas sous sa forme
canonique ; deux règles se contredisent ; une clé est inconnue.

Une règle est « installée » quand sa forme canonique figure dans une couche de
``_grimoire/standard/rules/`` : le pack n'a pas d'état propre, donc pas de
dérive entre un registre d'installation et les fichiers de couche.

Limites : un pack n'existe qu'en livraison avec le kit (pas de pack local au
projet) ; les vérificateurs lisent des fichiers, ils ne prouvent pas un
comportement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.core.layered_rules import Rule, RuleRefusedError, add_rule, check_rule_conflicts, parse_rule
from grimoire.core.stack_pack_verifiers import (
    AST_CHECKS,
    KINDS,
    STATUS_FAIL,
    STATUS_UNEVALUATED,
    Corpus,
    Finding,
    Verdict,
    Verifier,
    verify,
)

__all__ = [
    "MAX_ADVISORY_RATIO",
    "PACK_SCHEMA",
    "InstallOutcome",
    "PackRule",
    "RuleStatus",
    "StackPack",
    "StackPackError",
    "StackRulesReport",
    "evaluate_project",
    "install_pack",
    "load_pack_file",
    "load_packs",
    "packs_dir",
    "parse_pack",
    "rules_score_fields",
]

PACK_SCHEMA = "grimoire-stack-rule-pack/v1"

#: Part maximale de règles advisory : au-delà, le pack est de la prose avec un habillage.
MAX_ADVISORY_RATIO = 0.5

STATUS_ADVISORY = "advisory"

_ID = re.compile(r"[a-z][a-z0-9-]{0,30}")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.\-*?]+")
_TOP_KEYS = frozenset({"$schema", "id", "stack", "title", "description", "rules"})
_RULE_KEYS = frozenset({"rule", "rationale", "verifier", "advisory"})
_VERIFIER_KEYS: dict[str, frozenset[str]] = {
    "forbid_regex": frozenset({"kind", "pattern", "paths"}),
    "require_regex": frozenset({"kind", "pattern", "paths"}),
    "require_file": frozenset({"kind", "paths"}),
    "python_ast": frozenset({"kind", "check", "paths"}),
}
_MAX_PATTERN_CHARS = 200
_MAX_PATHS = 8
_DEFAULT_AST_PATHS = ("**/*.py",)


class StackPackError(GrimoireMemoryError):
    """Un pack invalide. Porte un ``code`` stable (``stack_pack.advisory_ratio``…)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PackRule:
    """Une règle de pack : la règle parsée, sa raison, et son vérificateur (``None`` = advisory)."""

    rule: Rule
    rationale: str
    verifier: Verifier | None = None

    @property
    def advisory(self) -> bool:
        return self.verifier is None


@dataclass(frozen=True, slots=True)
class StackPack:
    """Un pack chargé et validé."""

    id: str
    stack: str
    title: str
    rules: tuple[PackRule, ...]
    description: str = ""

    @property
    def verified(self) -> int:
        return sum(1 for r in self.rules if not r.advisory)

    @property
    def advisory(self) -> int:
        return sum(1 for r in self.rules if r.advisory)


# ── Chargement et schéma ──────────────────────────────────────────────────────


def packs_dir() -> Path:
    """Dossier des packs livrés avec le kit."""
    return Path(__file__).resolve().parent.parent / "data" / "stack-packs"


def _fail(code: str, where: str, message: str) -> StackPackError:
    return StackPackError(f"stack_pack.{code}", f"Pack refusé ({where}) : {message}")


def _text(value: object, where: str, what: str, *, maximum: int = 400) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("schema", where, f"{what} doit être un texte non vide.")
    if len(value) > maximum:
        raise _fail("schema", where, f"{what} dépasse {maximum} caractères.")
    return value.strip()


def _parse_paths(raw: object, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_PATHS:
        raise _fail("verifier", where, f"`paths` est une liste de 1 à {_MAX_PATHS} globs.")
    for glob in raw:
        if not isinstance(glob, str) or not glob or glob.startswith("/"):
            raise _fail("verifier", where, f"glob {glob!r} : relatif à la racine du projet attendu.")
        for segment in glob.split("/"):
            if segment in {"", ".", ".."} or not _PATH_SEGMENT.fullmatch(segment):
                raise _fail("verifier", where, f"glob {glob!r} : segment {segment!r} hors grammaire.")
    return tuple(raw)


def _parse_verifier(raw: object, where: str) -> Verifier:
    if not isinstance(raw, dict):
        raise _fail("verifier", where, "`verifier` est un mapping.")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise _fail("verifier", where, f"nature {kind!r} inconnue ; admises : {', '.join(KINDS)}.")
    extra = set(raw) - _VERIFIER_KEYS[kind]
    if extra:
        raise _fail("verifier", where, f"clé(s) inconnue(s) pour {kind} : {', '.join(sorted(map(str, extra)))}.")
    pattern = check = ""
    if kind in {"forbid_regex", "require_regex"}:
        pattern = _text(raw.get("pattern"), where, "`pattern`", maximum=_MAX_PATTERN_CHARS)
        try:
            re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            raise _fail("verifier", where, f"motif illisible : {exc}.") from exc
    if kind == "python_ast":
        check = _text(raw.get("check"), where, "`check`", maximum=60)
        if check not in AST_CHECKS:
            raise _fail("verifier", where, f"contrôle {check!r} inconnu ; admis : {', '.join(sorted(AST_CHECKS))}.")
    default = _DEFAULT_AST_PATHS if kind == "python_ast" else None
    paths = _parse_paths(raw["paths"] if "paths" in raw else list(default or ()), where)
    return Verifier(kind=kind, paths=paths, pattern=pattern, check=check)


def _parse_rule(raw: object, index: int, pack_id: str) -> PackRule:
    where = f"{pack_id}, règle {index}"
    if not isinstance(raw, dict):
        raise _fail("schema", where, "une règle est un mapping.")
    extra = set(raw) - _RULE_KEYS
    if extra:
        raise _fail("schema", where, f"clé(s) inconnue(s) : {', '.join(sorted(map(str, extra)))}.")
    text = _text(raw.get("rule"), where, "`rule`", maximum=200)
    try:
        rule = parse_rule(text)
    except RuleRefusedError as exc:
        raise _fail("rule_syntax", where, str(exc)) from exc
    if rule.render() != text:
        raise _fail("rule_not_canonical", where, f"« {text} » n'est pas sous forme canonique (« {rule.render()} »).")
    rationale = _text(raw.get("rationale"), where, "`rationale`")
    has_verifier = "verifier" in raw
    advisory = raw.get("advisory")
    if advisory is not None and advisory is not True:
        raise _fail("rule_unclassified", where, "`advisory` ne peut valoir que `true` : une règle sans vérificateur se déclare advisory.")
    if has_verifier and advisory:
        raise _fail("rule_both", where, f"« {text} » est à la fois vérifiée et advisory.")
    if not has_verifier and not advisory:
        raise _fail("rule_unclassified", where, f"« {text} » n'a ni `verifier` ni `advisory: true`.")
    return PackRule(rule=rule, rationale=rationale, verifier=_parse_verifier(raw["verifier"], where) if has_verifier else None)


def parse_pack(data: object, *, expected_id: str | None = None) -> StackPack:
    """Valider un pack déjà décodé, ou lever :class:`StackPackError`. Pure."""
    if not isinstance(data, dict):
        raise _fail("schema", expected_id or "?", "le pack est un mapping.")
    pack_id = data.get("id")
    where = pack_id if isinstance(pack_id, str) else (expected_id or "?")
    if data.get("$schema") != PACK_SCHEMA:
        raise _fail("schema", where, f"`$schema` doit valoir {PACK_SCHEMA!r}.")
    extra = set(data) - _TOP_KEYS
    if extra:
        raise _fail("schema", where, f"clé(s) inconnue(s) : {', '.join(sorted(map(str, extra)))}.")
    if not isinstance(pack_id, str) or not _ID.fullmatch(pack_id):
        raise _fail("schema", where, "`id` : minuscules, chiffres et tiret, 31 caractères au plus.")
    if expected_id is not None and pack_id != expected_id:
        raise _fail("schema", where, f"`id` ({pack_id}) doit être identique au nom du fichier ({expected_id}).")
    stack = _text(data.get("stack"), where, "`stack`", maximum=40)
    title = _text(data.get("title"), where, "`title`", maximum=120)
    description = _text(data["description"], where, "`description`", maximum=800) if "description" in data else ""
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise _fail("schema", where, "`rules` est une liste non vide.")
    rules = tuple(_parse_rule(raw, i, pack_id) for i, raw in enumerate(raw_rules, start=1))
    texts = [r.rule.render() for r in rules]
    if len(set(texts)) != len(texts):
        raise _fail("duplicate_rule", where, "une règle figure deux fois.")
    conflicts = check_rule_conflicts(r.rule for r in rules)
    if conflicts:
        first = conflicts[0]
        raise _fail("conflict", where, f"« {first.first.render()} » contredit « {first.second.render()} ».")
    advisory = sum(1 for r in rules if r.advisory)
    if advisory > len(rules) * MAX_ADVISORY_RATIO:
        raise _fail(
            "advisory_ratio",
            where,
            f"{advisory} règle(s) advisory sur {len(rules)} : plus de {int(MAX_ADVISORY_RATIO * 100)} % de prose non vérifiée.",
        )
    return StackPack(id=pack_id, stack=stack, title=title, rules=rules, description=description)


def load_pack_file(path: Path) -> StackPack:
    """Lire et valider un fichier de pack."""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise _fail("unreadable", Path(path).name, f"fichier illisible ({type(exc).__name__}).") from exc
    return parse_pack(data, expected_id=Path(path).stem)


def load_packs(directory: Path | None = None) -> tuple[StackPack, ...]:
    """Tous les packs d'un dossier (par défaut : ceux du kit), triés par ``id``.

    Un pack invalide fait échouer le chargement entier : un pack à moitié chargé
    laisserait croire que ses règles n'existent pas. Une règle qui figure dans
    deux packs est refusée : son statut (vérifiée ou advisory) serait ambigu.
    """
    folder = Path(directory) if directory is not None else packs_dir()
    packs = tuple(load_pack_file(p) for p in sorted(folder.glob("*.yaml")))
    seen: dict[str, str] = {}
    for pack in packs:
        for pack_rule in pack.rules:
            text = pack_rule.rule.render()
            if text in seen:
                raise _fail("duplicate_rule", pack.id, f"« {text} » figure déjà dans le pack {seen[text]}.")
            seen[text] = pack.id
    return packs


# ── Installation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    """Ce qu'une installation a fait, règle par règle."""

    written: tuple[str, ...] = ()
    present: tuple[str, ...] = ()
    refused: tuple[tuple[str, str, str], ...] = ()  # (règle, code, message)


def install_pack(root: Path, pack: StackPack, layer: str, *, project_name: str = "project") -> InstallOutcome:
    """Ajouter les règles d'un pack à une couche, par :func:`add_rule` (frontière de confiance comprise).

    Règle par règle : un refus (conflit avec une couche extérieure, plafond) n'empêche
    pas les autres, et ne laisse rien d'écrit pour la règle refusée.
    """
    written: list[str] = []
    present: list[str] = []
    refused: list[tuple[str, str, str]] = []
    for pack_rule in pack.rules:
        text = pack_rule.rule.render()
        try:
            add_rule(root, layer, text, project_name=project_name)
        except RuleRefusedError as exc:
            if exc.code == "rules.duplicate":
                present.append(text)
            else:
                refused.append((text, exc.code, str(exc)))
        else:
            written.append(text)
    return InstallOutcome(tuple(written), tuple(present), tuple(refused))


# ── Évaluation sur un projet ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RuleStatus:
    """Une règle de pack installée dans le projet, et ce que son vérificateur en dit."""

    pack: str
    rule: str
    status: str  # pass | fail | unevaluated | advisory
    findings: tuple[Finding, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StackRulesReport:
    """Règles de packs installées, et règles de couche qu'aucun pack ne qualifie."""

    statuses: tuple[RuleStatus, ...] = ()
    unclassified: int = 0

    @property
    def enforced(self) -> int:
        return sum(1 for s in self.statuses if s.status != STATUS_ADVISORY)

    @property
    def advisory(self) -> int:
        return sum(1 for s in self.statuses if s.status == STATUS_ADVISORY)

    @property
    def failing(self) -> int:
        return sum(1 for s in self.statuses if s.status == STATUS_FAIL)

    @property
    def unevaluated(self) -> int:
        return sum(1 for s in self.statuses if s.status == STATUS_UNEVALUATED)


def evaluate_project(
    root: Path, layers: Mapping[str, tuple[Rule, ...]], packs: tuple[StackPack, ...] | None = None
) -> StackRulesReport:
    """Évaluer les règles de packs présentes dans les couches du projet.

    Ne parcourt le projet que si au moins une règle vérifiée est installée : un
    projet qui n'a installé aucun pack ne paie rien.
    """
    loaded = load_packs() if packs is None else packs
    installed = {rule.render() for rules in layers.values() for rule in rules}
    known = {pr.rule.render() for pack in loaded for pr in pack.rules}
    wanted = [(pack, pr) for pack in loaded for pr in pack.rules if pr.rule.render() in installed]
    corpus = Corpus(root) if any(not pr.advisory for _, pr in wanted) else None
    statuses: list[RuleStatus] = []
    for pack, pr in wanted:
        text = pr.rule.render()
        if pr.verifier is None or corpus is None:
            statuses.append(RuleStatus(pack.id, text, STATUS_ADVISORY))
            continue
        verdict: Verdict = verify(corpus, pr.verifier)
        statuses.append(RuleStatus(pack.id, text, verdict.status, verdict.findings, verdict.reason))
    return StackRulesReport(tuple(statuses), unclassified=len(installed - known))


def rules_score_fields(root: Path) -> dict[str, int | None]:
    """Champs de ``standard score`` : règles contrôlées et règles advisory, comptées à part.

    - ``rules_enforced`` : règles installées qui portent un vérificateur (qu'il passe ou non) ;
    - ``rules_advisory`` : règles installées déclarées advisory ;
    - ``rules_failing`` / ``rules_unevaluated`` : parmi les contrôlées, celles dont le vérificateur
      échoue, ou n'a pas pu conclure ;
    - ``rules_unclassified`` : règles de couche qu'aucun pack ne qualifie (ni vérifiée, ni advisory).

    Un pack du kit illisible rend ``None`` partout : zéro serait un chiffre inventé.
    """
    from grimoire.core.layered_rules import analyze

    try:
        report = evaluate_project(root, analyze(root).layers)
    except StackPackError:
        return dict.fromkeys(
            ("rules_enforced", "rules_advisory", "rules_failing", "rules_unevaluated", "rules_unclassified")
        )
    return {
        "rules_enforced": report.enforced,
        "rules_advisory": report.advisory,
        "rules_failing": report.failing,
        "rules_unevaluated": report.unevaluated,
        "rules_unclassified": report.unclassified,
    }

