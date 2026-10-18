"""Règles parsables par couche : le code décide des conflits, pas un LLM.

Une règle de projet écrite en prose s'additionne sans jamais se contredire
« officiellement » : deux phrases opposées cohabitent, et c'est le modèle qui
tranche au moment de les lire. Ce module remplace la prose par une grammaire
fermée, pour que la contradiction, le dépassement de budget et l'écriture
hostile soient décidés par une fonction déterministe.

Grammaire (une règle par ligne) ::

    ALWAYS|NEVER <verbe> <objet> [@<portée>]

- ``<verbe>`` : un mot, minuscules latines, tiret admis (``run``, ``vérifier``).
- ``<objet>`` : un à douze mots (lettres ASCII, éventuellement accentuées,
  chiffres, ``_ . - / +``).
- ``@<portée>`` : un motif de chemin relatif à la racine du projet
  (``src/**``, ``docs/*.md``). Absent, la règle vaut pour tout le projet.
  Le motif est un glob exact : ``src`` ne couvre pas ``src/a.py``, écrire
  ``src/**``.

La grammaire est volontairement étroite. Tout ce qu'elle n'admet pas est refusé
avant d'être stocké : saut de ligne, caractère invisible, lettre qui n'est pas
de l'ASCII ou de l'ASCII accentué (homoglyphe cyrillique, grec ou latin comme
``ı`` ou ``ɑ``, qui ferait passer ``run`` pour deux objets distincts), guillemet, barre, substitution de commande, URL, mot-clé de règle
glissé dans l'objet, portée absolue ou qui remonte (``..``).

Couches, de la plus extérieure (qui l'emporte) à la plus intérieure :
``organization``, ``project``, ``agent``. Les fichiers vivent sous
``_grimoire/standard/rules/<couche>.rules``.

Décisions prises par le code (:func:`check_rule_conflicts`, fonction pure) :

- ``ALWAYS A`` et ``NEVER A`` (même verbe, même objet) dont les portées se
  recouvrent sont un conflit ;
- entre deux couches, la couche extérieure l'emporte (``winner``) ;
- dans une même couche, rien ne tranche : le conflit reste ouvert et refuse
  l'écriture comme il fait échouer ``standard verify``.

Toute écriture passe par :func:`add_rule`, donc par
:func:`grimoire.memory.validation.validate_memory_write`, puis par la grammaire,
le conflit et le plafond de la couche. Un refus n'écrit rien.

Limites connues, documentées plutôt que tues : la détection de conflit compare
des chaînes. Deux synonymes (``remove`` et ``delete``), un pluriel ou une
traduction forment deux objets distincts. Le filtre des règles qui affaiblissent
la gouvernance repose sur deux listes de mots, cherchées dans chaque fragment
d'un mot composé : un synonyme les contourne. Une lettre accentuée forme un
mot distinct de sa version nue pour le conflit (``maïn`` n'est pas ``main``),
alors que le filtre de gouvernance ignore les accents. Ce
module est une couche de défense en profondeur, pas une frontière étanche.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.core.standard_generation import STANDARD_DIR

__all__ = [
    "DEFAULT_MAX_TOKENS_PER_LAYER",
    "LAYERS",
    "MODALITIES",
    "RULES_DIR",
    "LayerRead",
    "Rule",
    "RuleConflict",
    "RuleProblem",
    "RuleRefusedError",
    "RuleWrite",
    "RulesReport",
    "add_rule",
    "analyze",
    "check_rule_conflicts",
    "layer_tokens",
    "parse_rule",
    "read_layer",
    "rules_max_tokens",
    "rules_path",
    "scopes_overlap",
]

#: Les deux modalités de la grammaire, en majuscules exactes.
MODALITIES: tuple[str, ...] = ("ALWAYS", "NEVER")

#: Couches, de la plus extérieure (précédence maximale) à la plus intérieure.
LAYERS: tuple[str, ...] = ("organization", "project", "agent")

#: Dossier des règles, à côté de ``standard-profile.yaml``.
RULES_DIR = STANDARD_DIR / "rules"
LAYER_SUFFIX = ".rules"

MAX_RULE_CHARS = 200
MAX_OBJECT_WORDS = 12
MAX_WORD_CHARS = 40
MAX_VERB_CHARS = 24
MAX_SCOPE_CHARS = 120

#: Plafond par défaut d'une couche, en jetons estimés. Deux mille jetons font
#: une quarantaine de règles de longueur moyenne : au-delà, la couche n'est plus
#: une liste de règles qu'on relit, c'est un document.
DEFAULT_MAX_TOKENS_PER_LAYER = 2000

#: Même ratio que ``grimoire.tools._common.estimate_tokens`` (quatre caractères
#: par jeton). Recopié plutôt qu'importé : ce paquet charge toute la CLI d'outils,
#: et ce module est lu par ``standard verify`` à froid.
_CHARS_PER_TOKEN = 4

_WORD_PUNCTUATION = frozenset("_.-/+")
_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.\-*?]+")

#: Verbes qui affaiblissent une garde quand la règle les affirme (``ALWAYS``).
_WEAKENING_VERBS = frozenset({
    "skip", "bypass", "disable", "ignore", "disregard", "override", "circumvent",
    "suppress", "remove", "delete", "drop", "waive", "silence", "deactivate",
    "contourner", "ignorer", "desactiver", "désactiver", "supprimer", "omettre",
})
#: Verbes qui l'affaiblissent quand la règle les interdit (``NEVER``).
_STRENGTHENING_VERBS = frozenset({
    "run", "enable", "enforce", "require", "keep", "apply", "perform", "request",
    "honor", "respect", "use", "activate",
    "lancer", "activer", "exiger", "appliquer", "respecter", "demander",
})
#: Objets de gouvernance : une règle qui les désarme n'est pas une règle de projet.
_GOVERNANCE_WORDS = frozenset({
    "guard", "hook", "gate", "verification", "verifier", "policy", "safety", "proof",
    "evidence", "review", "approval", "confirmation", "audit", "redaction",
    "validation", "sandbox", "permission", "garde", "vérification",
    "preuve", "revue", "approbation", "politique", "securite", "sécurité",
})
#: ``_GOVERNANCE_WORDS`` sans accents : c'est contre elle que se comparent les fragments.
_GOVERNANCE_PLAIN = frozenset(
    "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")
    for w in _GOVERNANCE_WORDS
)


class RuleRefusedError(GrimoireMemoryError):
    """Une règle refusée : grammaire, frontière de confiance, conflit ou plafond.

    Porte un ``code`` stable (``rules.syntax``, ``rules.conflict``…) et un
    ``remedy`` : un refus qui ne dit pas quoi faire est un mur.
    """

    def __init__(self, code: str, message: str, *, remedy: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.remedy = remedy

    def to_dict(self) -> dict[str, Any]:
        """Forme JSON du refus, telle que la rend la CLI."""
        return {"error": str(self), "refused": True, "code": self.code, "remedy": self.remedy}


@dataclass(frozen=True, slots=True)
class Rule:
    """Une règle parsée, sous sa forme canonique."""

    modality: str
    verb: str
    object: str
    scope: str = "**"
    layer: str = ""
    line: int = 0

    @property
    def key(self) -> tuple[str, str]:
        """Ce qui est commandé ou interdit : le couple (verbe, objet)."""
        return (self.verb, self.object)

    def render(self) -> str:
        """Texte canonique, celui qui s'écrit dans le fichier de couche."""
        suffix = "" if self.scope == "**" else f" @{self.scope}"
        return f"{self.modality} {self.verb} {self.object}{suffix}"


@dataclass(frozen=True, slots=True)
class RuleConflict:
    """Deux règles opposées sur la même chose, avec des portées qui se recouvrent."""

    first: Rule
    second: Rule
    #: La règle qui l'emporte, décidée par la précédence des couches ; ``None``
    #: quand rien ne tranche (même couche, couche inconnue).
    winner: Rule | None

    @property
    def decided_by(self) -> str:
        return "layer" if self.winner is not None else "unresolved"


@dataclass(frozen=True, slots=True)
class RuleProblem:
    """Un constat sur les fichiers de règles, avec de quoi le localiser."""

    kind: str  # unparsable | conflict | shadowed | over_budget | unknown_layer | policy_invalid
    message: str
    layer: str = ""
    line: int = 0
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class LayerRead:
    """Contenu d'une couche : les règles lues et les lignes qui n'en sont pas."""

    rules: tuple[Rule, ...]
    problems: tuple[RuleProblem, ...]


@dataclass(frozen=True, slots=True)
class RulesReport:
    """État des règles d'un projet."""

    layers: dict[str, tuple[Rule, ...]] = field(default_factory=dict)
    problems: tuple[RuleProblem, ...] = ()
    max_tokens: int = DEFAULT_MAX_TOKENS_PER_LAYER


@dataclass(frozen=True, slots=True)
class RuleWrite:
    """Une règle écrite, et celles de couches intérieures qu'elle met à l'ombre."""

    rule: Rule
    path: Path
    shadowed: tuple[Rule, ...] = ()


# ── Grammaire ─────────────────────────────────────────────────────────────────


#: Ligatures du français, sans équivalent ASCII mot à mot mais sans ressemblance
#: avec une lettre ASCII isolée : admises telles quelles.
_FRENCH_LIGATURES = frozenset("œŒæÆ")


@functools.cache
def _is_latin_letter(ch: str) -> bool:
    """Lettre admise dans un mot : ASCII, ou ASCII + diacritiques (``é``, ``ç``, ``ï``).

    Le nom Unicode ne sert pas de test : ``ı`` (U+0131), ``ɑ`` (U+0251), ``ɡ``
    (U+0261), ``ɴ`` (U+0274) ou ``ʀ`` (U+0280) s'appellent ``LATIN ...`` et se
    lisent pourtant ``i``, ``a``, ``g``, ``n``, ``r``. Une lettre est admise si
    sa décomposition canonique est une lettre ASCII suivie de marques seules ;
    ce qui n'a pas d'équivalent ASCII décomposable est refusé.
    """
    if ch.isascii():
        return ch.isalpha()
    if ch in _FRENCH_LIGATURES:
        return True
    decomposed = unicodedata.normalize("NFD", ch)
    return (
        len(decomposed) > 1
        and decomposed[0].isascii()
        and decomposed[0].isalpha()
        and all(unicodedata.category(c) == "Mn" for c in decomposed[1:])
    )


def _refuse_syntax(message: str, remedy: str = "") -> RuleRefusedError:
    return RuleRefusedError(
        "rules.syntax",
        f"Refusé : {message}",
        remedy=remedy or "Écrire une ligne « ALWAYS|NEVER <verbe> <objet> [@portée] ».",
    )


def _check_word(word: str, *, what: str) -> None:
    if not word or len(word) > MAX_WORD_CHARS:
        raise _refuse_syntax(f"{what} vide ou de plus de {MAX_WORD_CHARS} caractères.")
    head = word[1:] if word[0] == "." else word  # « .env », « .github » : un point initial est un nom de fichier
    if not head or not ((head[0].isascii() and head[0].isdigit()) or _is_latin_letter(head[0])):
        raise _refuse_syntax(f"{what} {word!r} doit commencer par une lettre latine ou un chiffre (point initial admis).")
    for ch in word:
        ascii_digit = ch.isascii() and ch.isdigit()
        if not (ascii_digit or _is_latin_letter(ch) or ch in _WORD_PUNCTUATION):
            raise _refuse_syntax(
                f"{what} {word!r} porte un caractère hors grammaire (U+{ord(ch):04X}).",
                "Lettres latines, chiffres et « _ . - / + » seulement : ni invisible, ni cyrillique, ni guillemet.",
            )
    if "//" in word or word.casefold() in {m.casefold() for m in MODALITIES}:
        raise _refuse_syntax(f"{what} {word!r} est un mot réservé ou une URL.")


def _parse_scope(raw: str) -> str:
    if not raw or len(raw) > MAX_SCOPE_CHARS:
        raise _refuse_syntax(f"portée vide ou de plus de {MAX_SCOPE_CHARS} caractères.")
    if raw.startswith("/"):
        raise _refuse_syntax("la portée est relative à la racine du projet, pas absolue.")
    segments = raw.split("/")
    for segment in segments:
        if segment in {"", ".", ".."}:
            raise _refuse_syntax(f"portée {raw!r} : segment vide, « . » ou « .. » interdit.")
        if not _SCOPE_SEGMENT.fullmatch(segment):
            raise _refuse_syntax(f"portée {raw!r} : caractère hors grammaire dans {segment!r}.")
        if "**" in segment and segment != "**":
            raise _refuse_syntax(f"portée {raw!r} : « ** » doit occuper un segment entier.")
    return "/".join(segments)


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return f"{word[:-3]}y"
    return word.removesuffix("s")


def _without_accents(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


_FRAGMENT_SPLIT = re.compile(r"[_.\-/+]+")


def _governance_fragments(word: str) -> Iterable[str]:
    """Le mot entier, puis chaque fragment que les séparateurs du mot isolent.

    ``code-review``, ``pre-commit-hooks`` ou ``hooks/pre-push`` portent ``review``
    et ``hook`` : tester le mot entier seulement laissait passer ces composés.
    Les accents sont retirés (``vérification`` et ``verification`` se valent).
    """
    plain = _without_accents(word)
    yield plain
    yield from (frag for frag in _FRAGMENT_SPLIT.split(plain) if frag)


def _refuse_governance_weakening(modality: str, verb: str, words: list[str]) -> None:
    on_governance = any(
        frag in _GOVERNANCE_PLAIN or _singular(frag) in _GOVERNANCE_PLAIN
        for w in words
        for frag in _governance_fragments(w)
    )
    if not on_governance:
        return
    weakens = (modality == "ALWAYS" and verb in _WEAKENING_VERBS) or (
        modality == "NEVER" and verb in _STRENGTHENING_VERBS
    )
    if weakens:
        raise RuleRefusedError(
            "rules.weakens_governance",
            f"Refusé : « {modality} {verb} … » désarme une garde de gouvernance.",
            remedy="Une règle de projet ne désactive ni garde, ni preuve, ni revue : passer par une dérogation tracée.",
        )


def parse_rule(text: str, *, layer: str = "", line: int = 0) -> Rule:
    """Parser une ligne de règle, ou lever :class:`RuleRefusedError` (``rules.syntax``).

    Pure : aucune lecture de fichier, aucun état. Le texte doit déjà être sur une
    seule ligne, mots séparés par une espace unique.
    """
    if not isinstance(text, str):
        raise _refuse_syntax(f"une règle est une chaîne, reçu {type(text).__name__}.")
    if len(text) > MAX_RULE_CHARS:
        raise _refuse_syntax(f"{len(text)} caractères dépassent la limite de {MAX_RULE_CHARS}.")
    if "\n" in text or "\r" in text:
        raise _refuse_syntax("une règle tient sur une seule ligne.")
    tokens = text.split(" ")
    if "" in tokens:
        raise _refuse_syntax("espace en début, en fin ou doublée.")
    if tokens[0] not in MODALITIES:
        raise _refuse_syntax(f"la règle commence par ALWAYS ou NEVER (majuscules), reçu {tokens[0][:20]!r}.")
    modality = tokens[0]
    scope = "**"
    if tokens[-1].startswith("@"):
        scope = _parse_scope(tokens.pop()[1:])
    if len(tokens) < 3:
        raise _refuse_syntax("il manque le verbe ou l'objet.")
    verb, words = tokens[1], tokens[2:]
    _check_word(verb, what="verbe")
    if len(verb) < 2 or len(verb) > MAX_VERB_CHARS or not all(c == "-" or (c.isalpha() and c.islower()) for c in verb):
        raise _refuse_syntax(
            f"verbe {verb!r} : de 2 à {MAX_VERB_CHARS} lettres latines minuscules, tiret admis.",
            "Un seul mot à l'infinitif : run, commit, vérifier.",
        )
    if len(words) > MAX_OBJECT_WORDS:
        raise _refuse_syntax(f"l'objet compte {len(words)} mots, {MAX_OBJECT_WORDS} au plus.")
    for word in words:
        _check_word(word, what="mot de l'objet")
    folded = [w.casefold() for w in words]
    _refuse_governance_weakening(modality, verb, folded)
    return Rule(modality=modality, verb=verb, object=" ".join(folded), scope=scope, layer=layer, line=line)


# ── Portées ───────────────────────────────────────────────────────────────────


def _segments_intersect(p: str, q: str) -> bool:
    """Existe-t-il un nom de segment que les deux motifs (``*``, ``?``) acceptent ?"""
    n, m = len(p), len(q)

    @functools.cache
    def reach(i: int, j: int) -> bool:
        if i == n and j == m:
            return True
        if i < n and p[i] == "*":
            return reach(i + 1, j) or (j < m and reach(i, j + 1))
        if j < m and q[j] == "*":
            return reach(i, j + 1) or (i < n and reach(i + 1, j))
        if i == n or j == m:
            return False
        if p[i] == q[j] or p[i] == "?" or q[j] == "?":
            return reach(i + 1, j + 1)
        return False

    return reach(0, 0)


def scopes_overlap(a: str, b: str) -> bool:
    """Deux portées désignent-elles au moins un chemin commun ? Pure.

    ``**`` (segment entier) absorbe zéro ou plusieurs segments ; ``*`` et ``?``
    ne franchissent pas un ``/``.
    """
    first, second = a.split("/"), b.split("/")
    n, m = len(first), len(second)

    @functools.cache
    def reach(i: int, j: int) -> bool:
        if i == n and j == m:
            return True
        if i < n and first[i] == "**":
            return reach(i + 1, j) or (j < m and reach(i, j + 1))
        if j < m and second[j] == "**":
            return reach(i, j + 1) or (i < n and reach(i + 1, j))
        if i == n or j == m:
            return False
        return _segments_intersect(first[i], second[j]) and reach(i + 1, j + 1)

    return reach(0, 0)


# ── Conflits ──────────────────────────────────────────────────────────────────


def _winner(a: Rule, b: Rule) -> Rule | None:
    if a.layer == b.layer or a.layer not in LAYERS or b.layer not in LAYERS:
        return None
    return a if LAYERS.index(a.layer) < LAYERS.index(b.layer) else b


def check_rule_conflicts(rules: Iterable[Rule]) -> tuple[RuleConflict, ...]:
    """Les conflits d'un ensemble de règles. Fonction pure, sans lecture ni état.

    Un conflit oppose ``ALWAYS`` et ``NEVER`` sur le même (verbe, objet) quand
    leurs portées se recouvrent. L'ordre du résultat suit l'ordre d'entrée.
    """
    groups: dict[tuple[str, str], list[Rule]] = {}
    for rule in rules:
        groups.setdefault(rule.key, []).append(rule)
    conflicts: list[RuleConflict] = []
    for group in groups.values():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                if first.modality != second.modality and scopes_overlap(first.scope, second.scope):
                    conflicts.append(RuleConflict(first, second, _winner(first, second)))
    return tuple(conflicts)


# ── Plafond ───────────────────────────────────────────────────────────────────


def layer_tokens(rules: Iterable[Rule]) -> int:
    """Jetons estimés d'une couche : la somme des règles sous leur forme canonique."""
    return sum(max(1, len(rule.render()) // _CHARS_PER_TOKEN) for rule in rules)


def rules_max_tokens(root: Path) -> tuple[int, str]:
    """``rules.max_tokens_per_layer`` de ``memory-policy.yaml``, et le défaut qui s'applique sinon.

    Renvoie ``(plafond, problème)`` : le problème est non vide quand la clé existe
    mais n'est pas un entier strictement positif (le défaut s'applique alors, et
    ``standard verify`` le dit au lieu de l'avaler).
    """
    path = Path(root) / STANDARD_DIR / "memory-policy.yaml"
    if not path.is_file():
        return DEFAULT_MAX_TOKENS_PER_LAYER, ""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path)
    except (OSError, ValueError, YAMLError):
        return DEFAULT_MAX_TOKENS_PER_LAYER, ""  # silent-ok — memory-policy.yaml illisible : yaml.invalid le signale déjà
    block = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(block, dict) or "max_tokens_per_layer" not in block:
        return DEFAULT_MAX_TOKENS_PER_LAYER, ""
    value = block["max_tokens_per_layer"]
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value, ""
    return DEFAULT_MAX_TOKENS_PER_LAYER, (
        f"rules.max_tokens_per_layer = {value!r} n'est pas un entier strictement positif : "
        f"le plafond par défaut ({DEFAULT_MAX_TOKENS_PER_LAYER}) s'applique."
    )


# ── Fichiers de couche ────────────────────────────────────────────────────────


def rules_path(root: Path, layer: str) -> Path:
    """Fichier d'une couche connue (``layer`` doit être l'un de :data:`LAYERS`)."""
    if layer not in LAYERS:
        raise RuleRefusedError(
            "rules.layer_unknown",
            f"Refusé : couche {layer!r} inconnue.",
            remedy=f"Utiliser l'une de : {', '.join(LAYERS)}.",
        )
    return Path(root) / RULES_DIR / f"{layer}{LAYER_SUFFIX}"


def read_layer(root: Path, layer: str) -> LayerRead:
    """Lire une couche. Une ligne qui n'est ni vide, ni commentaire, ni règle est un constat."""
    path = rules_path(root, layer)
    if not path.is_file():
        return LayerRead((), ())
    rel = RULES_DIR / path.name
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        problem = RuleProblem("unparsable", f"{rel} illisible ({type(exc).__name__}).", layer=layer, path=rel)
        return LayerRead((), (problem,))
    rules: list[Rule] = []
    problems: list[RuleProblem] = []
    for number, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rules.append(parse_rule(stripped, layer=layer, line=number))
        except RuleRefusedError as exc:
            problems.append(RuleProblem("unparsable", f"{rel}:{number} : {exc}", layer=layer, line=number, path=rel))
    return LayerRead(tuple(rules), tuple(problems))


def analyze(root: Path) -> RulesReport:
    """L'état des règles d'un projet : lignes illisibles, conflits, plafond, couches inconnues."""
    root = Path(root)
    directory = root / RULES_DIR
    if not directory.is_dir():
        return RulesReport()
    problems: list[RuleProblem] = []
    max_tokens, policy_issue = rules_max_tokens(root)
    if policy_issue:
        problems.append(RuleProblem("policy_invalid", policy_issue, path=STANDARD_DIR / "memory-policy.yaml"))
    for entry in sorted(directory.iterdir()):
        if entry.name.endswith(LAYER_SUFFIX) and entry.name[: -len(LAYER_SUFFIX)] not in LAYERS:
            problems.append(RuleProblem(
                "unknown_layer",
                f"{RULES_DIR / entry.name} : couche inconnue, hors plafond. Couches : {', '.join(LAYERS)}.",
                path=RULES_DIR / entry.name,
            ))
    layers: dict[str, tuple[Rule, ...]] = {}
    for layer in LAYERS:
        read = read_layer(root, layer)
        layers[layer] = read.rules
        problems.extend(read.problems)
        used = layer_tokens(read.rules)
        if used > max_tokens:
            problems.append(RuleProblem(
                "over_budget",
                f"Couche {layer} : {used} jetons estimés dépassent rules.max_tokens_per_layer ({max_tokens}).",
                layer=layer,
                path=RULES_DIR / f"{layer}{LAYER_SUFFIX}",
            ))
    for conflict in check_rule_conflicts(rule for layer in LAYERS for rule in layers[layer]):
        a, b = conflict.first, conflict.second
        where = f"{a.layer}:{a.line} « {a.render()} » contre {b.layer}:{b.line} « {b.render()} »"
        if conflict.winner is None:
            problems.append(RuleProblem(
                "conflict", f"Conflit non décidé, même couche : {where}.",
                layer=a.layer, line=a.line, path=RULES_DIR / f"{a.layer}{LAYER_SUFFIX}",
            ))
        else:
            loser = b if conflict.winner is a else a
            problems.append(RuleProblem(
                "shadowed",
                f"Règle à l'ombre : {where} ; la couche {conflict.winner.layer} l'emporte.",
                layer=loser.layer, line=loser.line, path=RULES_DIR / f"{loser.layer}{LAYER_SUFFIX}",
            ))
    return RulesReport(layers=layers, problems=tuple(problems), max_tokens=max_tokens)


# ── Écriture ──────────────────────────────────────────────────────────────────


def _decide(root: Path, layer: str, rule: Rule, max_tokens: int) -> tuple[Rule, ...]:
    """Accepter ou refuser ``rule`` face à l'état courant ; renvoie les règles qu'elle met à l'ombre."""
    own = read_layer(root, layer)
    if own.problems:
        raise RuleRefusedError(
            "rules.layer_corrupt",
            f"Refusé : la couche {layer} porte {len(own.problems)} ligne(s) hors grammaire ; on n'y ajoute rien.",
            remedy="Corriger le fichier (grimoire memory rules check), puis réessayer.",
        )
    if any(r.modality == rule.modality and r.key == rule.key and r.scope == rule.scope for r in own.rules):
        raise RuleRefusedError(
            "rules.duplicate", f"Refusé : « {rule.render()} » existe déjà dans la couche {layer}.",
            remedy="Rien à faire : la règle est déjà là.",
        )
    existing = [r for name in LAYERS for r in (own.rules if name == layer else read_layer(root, name).rules)]
    shadowed: list[Rule] = []
    for conflict in check_rule_conflicts([*existing, rule]):
        if rule is not conflict.first and rule is not conflict.second:
            continue
        other = conflict.first if conflict.second is rule else conflict.second
        if conflict.winner is rule:
            shadowed.append(other)
            continue
        if conflict.winner is None:
            raise RuleRefusedError(
                "rules.conflict",
                f"Refusé : « {rule.render()} » contredit « {other.render()} » (couche {other.layer}, "
                f"ligne {other.line}), portées qui se recouvrent, même couche : rien ne tranche.",
                remedy="Retirer l'une des deux règles, ou séparer leurs portées.",
            )
        raise RuleRefusedError(
            "rules.conflict",
            f"Refusé : « {rule.render()} » contredit « {other.render()} » de la couche {other.layer}, "
            "qui l'emporte : la règle serait morte.",
            remedy=f"Retirer la règle de la couche {other.layer}, ou écrire celle-ci dans une couche extérieure.",
        )
    used = layer_tokens([*own.rules, rule])
    if used > max_tokens:
        raise RuleRefusedError(
            "rules.over_budget",
            f"Refusé : la couche {layer} passerait à {used} jetons estimés, "
            f"plafond rules.max_tokens_per_layer = {max_tokens}.",
            remedy="Retirer ou fusionner des règles, ou relever rules.max_tokens_per_layer dans memory-policy.yaml.",
        )
    return tuple(shadowed)


def add_rule(root: Path, layer: str, text: str, *, project_name: str = "project", emitter: str = "cli") -> RuleWrite:
    """Ajouter une règle à une couche, ou lever :class:`RuleRefusedError` sans rien écrire.

    L'ordre est la frontière : couche connue, puis
    :func:`~grimoire.memory.validation.validate_memory_write` (consigne déguisée,
    secret, taille), puis grammaire, puis conflit et plafond face à l'état
    courant, le tout une première fois sans rien créer, une seconde sous verrou.
    """
    root = Path(root)
    path = rules_path(root, layer)
    from grimoire.memory.validation import MemoryWritePolicy, MemoryWriteRefusedError, validate_memory_write

    try:
        validated = validate_memory_write(
            text,
            metadata={"project_name": project_name, "source_kind": "rule", "memory_type": "procedural"},
            emitter=emitter,
            policy=MemoryWritePolicy.from_project(root),
            content_origin="authored",
        )
    except MemoryWriteRefusedError as exc:
        raise RuleRefusedError(exc.code, str(exc), remedy=exc.remedy) from exc
    if validated.redactions:
        raise RuleRefusedError(
            "rules.secret",
            "Refusé : la règle contient un secret ; un caviardage changerait son sens.",
            remedy="Nommer la variable ou le coffre, jamais la valeur.",
        )
    if validated.text != text:
        raise RuleRefusedError(
            "rules.normalized",
            "Refusé : la règle porte des caractères invisibles, de compatibilité ou décomposés.",
            remedy="Retaper la règle en caractères ordinaires (accents composés, pas de largeur nulle).",
        )
    rule = parse_rule(text.strip(" "), layer=layer)
    max_tokens, _ = rules_max_tokens(root)
    _decide(root, layer, rule, max_tokens)

    from grimoire.core.fsio import atomic_write, locked

    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        shadowed = _decide(root, layer, rule, max_tokens)
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        separator = "" if not current or current.endswith("\n") else "\n"
        atomic_write(path, f"{current}{separator}{rule.render()}\n")
    return RuleWrite(rule=rule, path=path, shadowed=shadowed)
