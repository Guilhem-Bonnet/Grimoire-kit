"""IntelliSense déterministe de l'espace Source — tokens, diagnostics, complétions.

Issue #280, voie 1 (« IntelliSense classique »). La voie 2 — un petit modèle
local pour des suggestions de contenu — resterait derrière celle-ci, jamais à
sa place : ce module est le point d'entrée qu'elle brancherait, en passant sa
propre proposition par les mêmes diagnostics avant affichage.

Rien ici ne réimplémente ``grimoire doctor`` ou le glossaire : les chemins
morts viennent de :mod:`grimoire.core.integrity` (la logique du doctor,
appliquée à un texte en mémoire — un brouillon non enregistré — plutôt qu'aux
seuls fichiers déjà livrés sur disque) ; les agents installés, de la même
source ; le glossaire, de :func:`grimoire.tools.workspace_api.glossary_view`.
Trois sources de vérité que ce module lit, aucune qu'il recopie.

Déterministe et borné : aucune notion d'IA ici, seulement des expressions
régulières et des tables de correspondance déjà tenues à jour ailleurs
(manifeste d'agents, catalogue de workflows, catalogue de patterns). Un fichier
que rien ne reconnaît (pas de frontmatter, pas d'extension YAML) rend des
tokens et des diagnostics vides plutôt que d'inventer une famille.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from grimoire.core import integrity
from grimoire.tools import workspace_api

__all__ = [
    "AGENT_FRONTMATTER_KEYS",
    "TOKEN_KINDS",
    "WORKFLOW_FRONTMATTER_KEYS",
    "language_view",
    "pattern_catalogue_ids",
    "tokenize",
]

# ── Ce qu'un token peut être — chacun a sa classe CSS côté éditeur ──────────

TOKEN_KINDS = (
    "comment",
    "key",
    "string",
    "tag",
    "placeholder",
    "path",
    "agent",
    "pattern",
    "workflow",
)

Kind = Literal[
    "comment", "key", "string", "tag", "placeholder", "path", "agent", "pattern", "workflow"
]

# ── Le schéma des deux familles de frontmatter que Source édite ────────────
#
# Il n'y a pas de fichier JSON Schema pour ces clés : elles sont ce que
# :mod:`grimoire.hosts.collect` (agents) et :mod:`grimoire.workflows.registry`
# (workflows) savent lire. Une clé hors de cette liste n'est pas forcément une
# faute — un projet peut en ajouter — mais elle n'est comprise par aucune des
# deux surfaces qui projettent ces fichiers vers un hôte, donc elle mérite un
# avertissement plutôt qu'un silence.
AGENT_FRONTMATTER_KEYS = frozenset({"name", "description", "tools", "model_affinity"})
WORKFLOW_FRONTMATTER_KEYS = frozenset(
    {"kind", "description", "agents", "team", "patterns", "memory", "triggers", "deprecated_by"}
)

# ── Expressions régulières, compilées une fois ──────────────────────────────

_FRONTMATTER_DELIM = re.compile(r"^---\s*$")
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*)(\s*:)(.*)$")
_LIST_KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)(\s*:)(.*)$")
_LIST_ITEM = re.compile(r"^(\s*)-\s*(.*)$")
_FLOW_LIST = re.compile(r"^\s*\[(.*)\]\s*(#.*)?$")
_AGENT_OPEN_TAG = re.compile(r"<agent\s+id=")
_TAG_NAME = re.compile(r"</?(agent|activation|step|workflow)\b")
_AGENT_TAG_ATTR = re.compile(r'\btag="([\w-]+)"')
_PLACEHOLDER_BRACE = re.compile(r"\{[A-Za-z][\w-]*\}")
_PLACEHOLDER_MUSTACHE = re.compile(r"\{\{[^{}]*\}\}")
_PATTERN_ID = re.compile(r"\b[A-Z]{2,4}-\d{2,3}\b")
_WORKFLOW_REF = re.compile(r"`/([a-z][\w-]*)`")
_GLOSSARY_ID_LINE = re.compile(r"^\s*-?\s*id:\s*(\S+)\s*$")
_WORD_AT = re.compile(r"[\w./-]+$")
#: Le curseur est encore sur le nom de la clé — indentation, puis un
#: identifiant partiel, rien d'autre (pas encore de ``:``).
_KEY_PARTIAL = re.compile(r"^\s*[\w-]*$")


@dataclass(slots=True)
class Token:
    line: int
    start: int
    end: int
    kind: Kind
    text: str
    #: Identifiant du glossaire si ce token cite un concept qui y a une entrée
    #: — l'éditeur (``spaces/source-editor.js``) s'en sert pour rappeler la
    #: même pile d'infobulles épinglable que le reste de la vue de travail
    #: (``glossary.js``), au survol de la souris.
    glossary_id: str | None = None
    #: Un token ``agent``/``pattern`` colorié n'est pas toujours une citation
    #: destinée à résoudre : ``agents: [architect, dev]`` en frontmatter décrit
    #: des rôles à titre indicatif (un gabarit du cadre, partagé par des
    #: archétypes dont le manifeste diffère), et un ``BM-19`` cité en prose
    #: n'appartient pas à l'espace de noms du catalogue de patterns
    #: (``ORC-``/``ORG-``/…). Seule une citation *strict* — la table de
    #: routage ``<agent tag="…">`` que le doctor valide déjà, ou la clé
    #: ``patterns:`` du frontmatter — alimente un diagnostic ; le reste reste
    #: colorié sans être jugé, pour ne pas noyer un vrai défaut sous des
    #: faux positifs sur les gabarits du cadre.
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "line": self.line, "start": self.start, "end": self.end, "kind": self.kind,
        }
        if self.glossary_id:
            payload["glossaryId"] = self.glossary_id
        return payload


@dataclass(slots=True)
class Diagnostic:
    line: int
    start: int
    end: int
    severity: Literal["error", "warning"]
    family: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line, "start": self.start, "end": self.end,
            "severity": self.severity, "family": self.family, "message": self.message,
        }


# ── Détection de la famille du fichier ──────────────────────────────────────


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    """Les indices de la ligne ``---`` d'ouverture et de fermeture, ou ``None``.

    Tolère ce que :func:`grimoire.hosts.collect.parse_frontmatter` accepte
    déjà pour lire le même fichier — un BOM, puis un commentaire HTML
    ``<!-- … -->`` avant le premier ``---`` : la convention que porte
    *chaque* agent du kit (``<!-- ARCHETYPE: … -->``). Un scan qui l'ignorait
    ne trouvait de frontmatter sur aucun agent réel — colorisation, clés
    inconnues et complétion se taisaient partout où il en fallait le plus.
    """
    if not lines:
        return None
    start = 0
    if lines[0].lstrip("\ufeff").startswith("<!--"):
        while start < len(lines) and "-->" not in lines[start]:
            start += 1
        start += 1
    if start >= len(lines):
        return None
    head = lines[start].lstrip("\ufeff") if start == 0 else lines[start]
    if not _FRONTMATTER_DELIM.match(head):
        return None
    for idx in range(start + 1, len(lines)):
        if _FRONTMATTER_DELIM.match(lines[idx]):
            return start, idx
    return None


def _detect_family(rel_path: str, lines: list[str], fm_span: tuple[int, int] | None) -> Literal["agent", "workflow", "glossary", None]:
    """Agent, workflow, glossaire, ou aucun schéma connu.

    Un fichier de glossaire se reconnaît à son nom (c'est la convention que
    :func:`grimoire.tools.workspace_api.glossary_view` suit déjà). Un fichier
    d'agent se reconnaît au bloc ``<agent id="…">`` que chaque persona porte
    dans son corps. Un fichier de workflow se reconnaît à sa clé ``kind:`` ou à
    son emplacement — même heuristique que
    :mod:`grimoire.workflows.registry`.
    """
    if rel_path.endswith("glossary.yaml"):
        return "glossary"
    body_start = fm_span[1] + 1 if fm_span else 0
    body = "\n".join(lines[body_start:])
    if _AGENT_OPEN_TAG.search(body):
        return "agent"
    if fm_span:
        for line in lines[fm_span[0] + 1 : fm_span[1]]:
            match = _TOP_KEY.match(line)
            if match and match.group(1) == "kind" and match.group(3).strip() in {"orchestration", "command"}:
                return "workflow"
    if "/workflows/" in rel_path or rel_path.startswith(".github/prompts/"):
        return "workflow"
    return None


# ── Tokenisation ─────────────────────────────────────────────────────────────


def _claim(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    """Vrai et enregistre *[start, end)* si rien ne le chevauche déjà."""
    for a, b in claimed:
        if start < b and a < end:
            return False
    claimed.append((start, end))
    return True


def _scan_generic(
    line: str, lineno: int, claimed: list[tuple[int, int]], glossary_ids: frozenset[str]
) -> list[Token]:
    """Les motifs qui peuvent apparaître n'importe où : tags, placeholders,
    chemins du kit, identifiants d'agent/pattern/workflow."""
    tokens: list[Token] = []

    for match in _TAG_NAME.finditer(line):
        if _claim(claimed, match.start(), match.end()):
            tokens.append(Token(lineno, match.start(), match.end(), "tag", match.group(0)))

    for match in _AGENT_TAG_ATTR.finditer(line):
        start, end = match.start(1), match.end(1)
        if _claim(claimed, start, end):
            value = match.group(1)
            # La table de routage d'un concierge : c'est ce que le doctor
            # confronte déjà au manifeste (`roster_incoherences`), donc c'est
            # strict ici aussi — même portée, même vérité.
            tokens.append(Token(lineno, start, end, "agent", value, _glossary_hit(value, glossary_ids), strict=True))

    for match in _PLACEHOLDER_MUSTACHE.finditer(line):
        if _claim(claimed, match.start(), match.end()):
            tokens.append(Token(lineno, match.start(), match.end(), "placeholder", match.group(0)))
    for match in _PLACEHOLDER_BRACE.finditer(line):
        if _claim(claimed, match.start(), match.end()):
            tokens.append(Token(lineno, match.start(), match.end(), "placeholder", match.group(0)))

    for target, start, end in integrity.iter_path_targets(line):
        if _claim(claimed, start, end):
            tokens.append(Token(lineno, start, end, "path", target, _glossary_hit_in_path(target, glossary_ids)))

    for match in _PATTERN_ID.finditer(line):
        if _claim(claimed, match.start(), match.end()):
            tokens.append(Token(lineno, match.start(), match.end(), "pattern", match.group(0)))

    for match in _WORKFLOW_REF.finditer(line):
        start, end = match.start(1), match.end(1)
        if _claim(claimed, start, end):
            tokens.append(Token(lineno, start, end, "workflow", match.group(1)))

    return tokens


def _split_comment(value: str) -> tuple[str, tuple[int, int] | None]:
    """Coupe *value* à un ``#`` de commentaire, en respectant les guillemets."""
    quote: str | None = None
    for idx, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (idx == 0 or value[idx - 1] == " "):
            return value[:idx], (idx, len(value))
    return value, None


def _quoted_span(value: str, offset: int) -> tuple[int, int] | None:
    stripped = value.strip()
    if not stripped or stripped[0] not in "\"'":
        return None
    quote = stripped[0]
    start = offset + value.index(quote)
    end = value.find(quote, start - offset + 1)
    if end == -1:
        return start, offset + len(value)
    return start, offset + end + 1


#: Une clé de frontmatter dont les items de liste sont des identifiants
#: connus, pas de la prose — ils prennent la couleur de leur famille plutôt
#: que de rester du texte YAML nu.
_LIST_VALUE_KIND: dict[str, Kind] = {"agents": "agent", "agent": "agent", "patterns": "pattern"}


def _flow_list_items(
    value: str, value_offset: int, kind: Kind, claimed: list[tuple[int, int]], *, strict: bool
) -> list[Token]:
    flow = _FLOW_LIST.match(value)
    if not flow:
        return []
    tokens: list[Token] = []
    search_from = value.index("[") + 1
    for item in flow.group(1).split(","):
        stripped = item.strip().strip("'\"")
        if not stripped:
            continue
        # Recherche locale plutôt qu'arithmétique cumulative : insensible aux
        # espaces irréguliers autour des virgules.
        local = value.find(stripped, search_from)
        if local == -1:
            continue
        start = value_offset + local
        end = start + len(stripped)
        if _claim(claimed, start, end):
            tokens.append(Token(-1, start, end, kind, stripped, strict=strict))
        search_from = local + len(stripped)
    return tokens


def _scan_yaml_line(
    line: str, lineno: int, claimed: list[tuple[int, int]], active_key: str | None
) -> tuple[list[Token], str | None]:
    """Une ligne YAML (frontmatter ou fichier ``.yaml`` entier) → ses tokens.

    Rend aussi la clé de liste multi-lignes active après cette ligne (voir
    :data:`_LIST_VALUE_KIND`) : ``agents:`` suivi d'items sur les lignes
    suivantes doit encore savoir, trois lignes plus bas, que ces items sont
    des agents et pas de la prose.
    """
    tokens: list[Token] = []
    if _FRONTMATTER_DELIM.match(line):
        return tokens, active_key

    key_match = _LIST_KEY.match(line)
    if key_match:
        indent, key, colon, rest = key_match.groups()
        key_start = len(indent)
        key_end = key_start + len(key)
        if _claim(claimed, key_start, key_end):
            tokens.append(Token(lineno, key_start, key_end, "key", key))
        value_offset = key_end + len(colon)
        value, comment_span = _split_comment(rest)
        if comment_span:
            start = value_offset + comment_span[0]
            tokens.append(Token(lineno, start, value_offset + comment_span[1], "comment", rest[comment_span[0]:]))
        kind = _LIST_VALUE_KIND.get(key)
        if kind and _FLOW_LIST.match(value):
            # Seule ``patterns:`` est une citation destinée à résoudre — voir
            # le commentaire de ``Token.strict``. ``agents:``/``agent:``
            # restent coloriés mais ne nourrissent pas le diagnostic.
            tokens.extend(_flow_list_items(value, value_offset, kind, claimed, strict=key == "patterns"))
        else:
            quoted = _quoted_span(value, value_offset)
            if quoted and _claim(claimed, *quoted):
                tokens.append(Token(lineno, quoted[0], quoted[1], "string", line[quoted[0]:quoted[1]]))
        for tok in tokens:
            if tok.line == -1:
                tok.line = lineno
        next_active = key if not value.strip() else None
        return tokens, next_active

    item_match = _LIST_ITEM.match(line)
    if item_match:
        _indent, rest = item_match.groups()
        item_offset = len(line) - len(rest) if rest else len(line)
        value, comment_span = _split_comment(rest)
        if comment_span:
            start = item_offset + comment_span[0]
            tokens.append(Token(lineno, start, item_offset + comment_span[1], "comment", rest[comment_span[0]:]))
        kind = _LIST_VALUE_KIND.get(active_key or "")
        stripped = value.strip().strip("'\"")
        if kind and stripped:
            start = item_offset + value.index(stripped)
            end = start + len(stripped)
            if _claim(claimed, start, end):
                tokens.append(Token(lineno, start, end, kind, stripped, strict=active_key == "patterns"))
        else:
            quoted = _quoted_span(value, item_offset)
            if quoted and _claim(claimed, *quoted):
                tokens.append(Token(lineno, quoted[0], quoted[1], "string", line[quoted[0]:quoted[1]]))
        return tokens, active_key

    if not line.strip() or line.strip().startswith("#"):
        _value, comment_span = _split_comment(line)
        if comment_span:
            tokens.append(Token(lineno, comment_span[0], comment_span[1], "comment", line[comment_span[0]:]))
        return tokens, active_key  # ligne vide ou commentaire : la liste continue

    return tokens, None


def _glossary_hit(word: str, glossary_ids: frozenset[str]) -> str | None:
    return word if word in glossary_ids else None


def _glossary_hit_in_path(target: str, glossary_ids: frozenset[str]) -> str | None:
    """Le glossaire définit les étages (``kit``, ``override``, ``projection``)
    par leur nom, pas par un chemin complet : un segment du chemin — pas
    seulement le nom de fichier — peut donc être le terme cité. Le premier
    segment qui correspond l'emporte ; c'est presque toujours l'étage, qui
    ouvre le chemin.
    """
    for segment in target.split("/"):
        stem = segment.rsplit(".", 1)[0]
        if stem in glossary_ids:
            return stem
    return None


def tokenize(rel_path: str, text: str, glossary_ids: frozenset[str]) -> list[Token]:
    """Les tokens de colorisation de *text*, ligne par ligne.

    Trois régimes, décidés une fois pour tout le fichier :
    ``.yaml``/``.yml`` — chaque ligne est une ligne YAML ; le frontmatter d'un
    ``.md`` — la même règle, bornée aux deux premières lignes ``---`` ; le
    corps d'un ``.md`` — tags, placeholders, chemins et identifiants, sans
    lecture de clé (une ligne de prose n'est pas une ligne YAML).

    Les motifs génériques (chemins, placeholders, tags, patterns, références
    de workflow) sont cherchés sur *toute* ligne, YAML comprise : une valeur
    de frontmatter comme ``description: "voir {project-root}/_grimoire/…"``
    doit colorier son chemin autant qu'une ligne de corps.
    """
    lines = text.split("\n")
    fm_span = _frontmatter_span(lines) if rel_path.endswith(".md") else None
    is_yaml = rel_path.endswith((".yaml", ".yml"))
    tokens: list[Token] = []
    active_key: str | None = None

    for lineno, line in enumerate(lines):
        claimed: list[tuple[int, int]] = []
        in_frontmatter = fm_span is not None and fm_span[0] <= lineno <= fm_span[1]
        if is_yaml or in_frontmatter:
            yaml_tokens, active_key = _scan_yaml_line(line, lineno, claimed, active_key)
            tokens.extend(yaml_tokens)
            if in_frontmatter and lineno in (fm_span[0], fm_span[1]):  # type: ignore[index]
                continue  # la ligne ``---`` elle-même ne porte rien d'autre
        tokens.extend(_scan_generic(line, lineno, claimed, glossary_ids))

    return tokens


# ── Diagnostics ──────────────────────────────────────────────────────────────


def _diagnose_dead_paths(project_root: Path, tokens: list[Token]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for tok in tokens:
        if tok.kind != "path":
            continue
        if integrity.target_is_dead(project_root, tok.text):
            out.append(Diagnostic(
                tok.line, tok.start, tok.end, "error", "dead-path",
                f"chemin cité mais introuvable dans le projet : {tok.text}",
            ))
    return out


def _diagnose_unknown_agents(project_root: Path, tokens: list[Token]) -> list[Diagnostic]:
    installed = integrity.installed_agent_tags(project_root)
    if not installed:
        return []  # projet non scaffoldé — rien à confronter, comme le doctor
    out: list[Diagnostic] = []
    for tok in tokens:
        if tok.kind != "agent" or not tok.strict:
            continue
        if tok.text not in installed:
            out.append(Diagnostic(
                tok.line, tok.start, tok.end, "error", "unknown-agent",
                f"agent absent du manifeste installé : {tok.text}",
            ))
    return out


def _top_level_frontmatter_keys(lines: list[str], fm_span: tuple[int, int]) -> list[tuple[int, str, int, int]]:
    found: list[tuple[int, str, int, int]] = []
    for lineno in range(fm_span[0] + 1, fm_span[1]):
        match = _TOP_KEY.match(lines[lineno])
        if match:
            found.append((lineno, match.group(1), 0, len(match.group(1))))
    return found


def _diagnose_frontmatter_keys(
    rel_path: str, lines: list[str], family: str | None
) -> list[Diagnostic]:
    if family not in ("agent", "workflow"):
        return []
    fm_span = _frontmatter_span(lines)
    if fm_span is None:
        return []
    known = AGENT_FRONTMATTER_KEYS if family == "agent" else WORKFLOW_FRONTMATTER_KEYS
    out: list[Diagnostic] = []
    for lineno, key, start, end in _top_level_frontmatter_keys(lines, fm_span):
        if key not in known:
            out.append(Diagnostic(
                lineno, start, end, "warning", "unknown-frontmatter-key",
                f"clé de frontmatter inconnue du schéma {family} : {key} "
                f"(connues : {', '.join(sorted(known))})",
            ))
    return out


def _diagnose_glossary_self_refs(rel_path: str, lines: list[str], family: str | None) -> list[Diagnostic]:
    """Un fichier de glossaire dont ``termes:`` cite un id qui n'existe pas.

    Ne relit pas :func:`workspace_api.glossary_view` — ce diagnostic porte sur
    le brouillon en cours d'édition, qui peut ne pas être sur disque. La règle
    qu'il applique (« un terme cité doit avoir une entrée ») est la même que
    ``tests/unit/test_workspace_glossary.py`` applique au reste de l'interface.
    """
    if family != "glossary":
        return []
    known_ids = {m.group(1) for line in lines if (m := _GLOSSARY_ID_LINE.match(line))}
    out: list[Diagnostic] = []
    active = False
    for lineno, line in enumerate(lines):
        key_match = _LIST_KEY.match(line)
        if key_match:
            active = key_match.group(2) == "termes"
            rest = key_match.group(4)
            flow = _FLOW_LIST.match(rest)
            if flow:
                offset = line.index(rest)
                for item in flow.group(1).split(","):
                    value = item.strip().strip("'\"")
                    if not value:
                        continue
                    start = line.index(value, offset)
                    if value != "termes" and active and value not in known_ids:
                        out.append(Diagnostic(
                            lineno, start, start + len(value), "warning", "unknown-glossary-term",
                            f"terme absent du glossaire : {value}",
                        ))
                active = False
            continue
        if active:
            item_match = _LIST_ITEM.match(line)
            if item_match:
                value = item_match.group(2).strip().strip("'\"")
                start = line.index(value) if value else len(line)
                if value and value not in known_ids:
                    out.append(Diagnostic(
                        lineno, start, start + len(value), "warning", "unknown-glossary-term",
                        f"terme absent du glossaire : {value}",
                    ))
            elif line.strip():
                active = False
    return out


def _pattern_catalogue_ids() -> frozenset[str] | None:
    """Les identifiants du catalogue de patterns, ou ``None`` s'il est absent.

    ``None`` — et non un ensemble vide — dit à l'appelant de se taire plutôt
    que de signaler chaque pattern comme inconnu : un catalogue introuvable
    n'est pas la preuve que le pattern n'existe pas.
    """
    from grimoire.data import web_path

    try:
        candidate = web_path() / "data" / "catalogue-export.json"
    except (OSError, RuntimeError):  # pragma: no cover — dépend de l'installation
        return None
    if not candidate.is_file():
        return None
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return frozenset(str(p.get("id")) for p in raw.get("patterns", []) if p.get("id"))


def pattern_catalogue_ids() -> frozenset[str] | None:
    """Wrapper public de :func:`_pattern_catalogue_ids` (issue #280, voie 2).

    ``source_assist`` vérifie les identifiants de pattern qu'un modèle local
    cite dans une suggestion avec le même catalogue que le diagnostic
    ``unknown-pattern`` ci-dessous — jamais une seconde lecture du fichier.
    """
    return _pattern_catalogue_ids()


def _workflow_slugs(project_root: Path) -> frozenset[str]:
    from grimoire.workflows.registry import load_workflows

    return frozenset(entry.slug for entry in load_workflows(project_root))


def _diagnose_patterns_and_workflows(
    project_root: Path, tokens: list[Token], family: str | None
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    pattern_ids = _pattern_catalogue_ids()
    if pattern_ids is not None and family == "workflow":
        for tok in tokens:
            if tok.kind == "pattern" and tok.strict and tok.text not in pattern_ids:
                out.append(Diagnostic(
                    tok.line, tok.start, tok.end, "error", "unknown-pattern",
                    f"identifiant de pattern inconnu du catalogue : {tok.text}",
                ))
    slugs = _workflow_slugs(project_root)
    if slugs:
        for tok in tokens:
            if tok.kind == "workflow" and tok.text not in slugs:
                out.append(Diagnostic(
                    tok.line, tok.start, tok.end, "error", "unknown-workflow",
                    f"workflow inconnu du catalogue : /{tok.text}",
                ))
    return out


def diagnose(project_root: Path, rel_path: str, lines: list[str], tokens: list[Token]) -> list[Diagnostic]:
    fm_span = _frontmatter_span(lines) if rel_path.endswith(".md") else None
    family = _detect_family(rel_path, lines, fm_span)
    out: list[Diagnostic] = []
    out.extend(_diagnose_dead_paths(project_root, tokens))
    out.extend(_diagnose_unknown_agents(project_root, tokens))
    out.extend(_diagnose_frontmatter_keys(rel_path, lines, family))
    out.extend(_diagnose_glossary_self_refs(rel_path, lines, family))
    out.extend(_diagnose_patterns_and_workflows(project_root, tokens, family))
    return sorted(out, key=lambda d: (d.line, d.start))


# ── Complétions ──────────────────────────────────────────────────────────────


def _agent_items(project_root: Path, prefix: str) -> list[dict[str, Any]]:
    return [
        {"label": tag, "kind": "agent", "insertText": tag, "detail": "agent installé"}
        for tag in sorted(integrity.installed_agent_tags(project_root))
        if tag.startswith(prefix)
    ]


def _workflow_items(project_root: Path, prefix: str) -> list[dict[str, Any]]:
    from grimoire.workflows.registry import load_workflows

    return [
        {"label": entry.slug, "kind": "workflow", "insertText": entry.slug, "detail": entry.description or "workflow"}
        for entry in load_workflows(project_root)
        if entry.slug.startswith(prefix)
    ]


def _pattern_items(prefix: str) -> list[dict[str, Any]]:
    ids = _pattern_catalogue_ids() or frozenset()
    return [
        {"label": pid, "kind": "pattern", "insertText": pid, "detail": "pattern du catalogue"}
        for pid in sorted(ids)
        if pid.startswith(prefix.upper())
    ]


def _glossary_items(project_root: Path, prefix: str) -> list[dict[str, Any]]:
    payload = workspace_api.glossary_view(project_root)
    return [
        {"label": e["id"], "kind": "glossary", "insertText": e["id"], "detail": e.get("nom", "")}
        for e in payload["entries"]
        if e["id"].startswith(prefix)
    ]


def _kit_path_items(project_root: Path, prefix: str) -> list[dict[str, Any]]:
    """Chemins du kit qui commencent par *prefix* — bornés, pour rester locaux."""
    if not prefix.startswith("_grimoire/"):
        return []
    partial = prefix[len("_grimoire/"):]
    parent_rel, _, stem = partial.rpartition("/")
    base = project_root / "_grimoire" / parent_rel if parent_rel else project_root / "_grimoire"
    if not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in sorted(base.iterdir())[:50]:
        if not child.name.startswith(stem):
            continue
        rel = child.relative_to(project_root).as_posix()
        items.append({
            "label": rel + ("/" if child.is_dir() else ""),
            "kind": "path",
            "insertText": rel + ("/" if child.is_dir() else ""),
            "detail": "dossier du kit" if child.is_dir() else "fichier du kit",
        })
    return items


def _schema_key_items(family: str | None, prefix: str) -> list[dict[str, Any]]:
    if family not in ("agent", "workflow"):
        return []
    known = AGENT_FRONTMATTER_KEYS if family == "agent" else WORKFLOW_FRONTMATTER_KEYS
    return [
        {"label": key, "kind": "schema-key", "insertText": key, "detail": f"clé {family}"}
        for key in sorted(known)
        if key.startswith(prefix)
    ]


def _word_prefix(line: str, col: int) -> tuple[str, int]:
    """Le mot en cours de saisie avant la colonne *col*, et sa position de départ."""
    match = _WORD_AT.search(line[:col])
    if not match:
        return "", col
    return match.group(0), match.start()


def complete(
    project_root: Path, rel_path: str, lines: list[str], line: int, col: int
) -> list[dict[str, Any]]:
    """Les complétions à la position *(line, col)*, 0-indexées.

    Le caractère qui précède le mot en cours décide de la famille proposée —
    ``@`` pour un agent, ``/`` pour un workflow, ``_grimoire/`` en cours de
    frappe pour un chemin du kit. Sans déclencheur reconnu, c'est une clé de
    frontmatter (dans le frontmatter) ou l'union agents/workflows/patterns/
    glossaire (dans le corps) — Ctrl+Espace n'a pas besoin d'un contexte
    exact pour rester utile.
    """
    if line < 0 or line >= len(lines):
        return []
    current = lines[line]
    word, word_start = _word_prefix(current, col)
    before = current[:word_start]
    fm_span = _frontmatter_span(lines) if rel_path.endswith(".md") else None
    in_frontmatter = fm_span is not None and fm_span[0] < line < fm_span[1]
    family = _detect_family(rel_path, lines, fm_span)

    if in_frontmatter and ":" not in current[:col] and _KEY_PARTIAL.match(current[:col]):
        # Le curseur est sur le nom de clé, avant même le ``:`` — la forme la
        # plus courante en train de taper ``desc<Ctrl+Espace>``.
        return _schema_key_items(family, word)

    if before.endswith("@"):
        return _agent_items(project_root, word)
    if word.startswith("/"):
        # ``/`` fait partie de la classe de caractères du mot (il faut aussi
        # reconnaître ``_grimoire/kit/…`` comme un seul mot) : le déclencheur
        # workflow se lit donc sur le mot lui-même, pas sur ce qui le précède.
        return _workflow_items(project_root, word[1:])
    if word.startswith("_grimoire/") or before.endswith("{"):
        items = _kit_path_items(project_root, word)
        if before.endswith("{") and not word:
            placeholder = {"label": "project-root}", "kind": "path", "insertText": "project-root}", "detail": "placeholder"}
            items = [placeholder, *items]
        return items

    return (
        _agent_items(project_root, word)
        + _workflow_items(project_root, word)
        + _pattern_items(word)
        + _glossary_items(project_root, word)
    )[:40]


# ── Point d'entrée ───────────────────────────────────────────────────────────


def language_view(
    project_root: Path,
    raw_path: str | None,
    *,
    text: str | None = None,
    line: int | None = None,
    col: int | None = None,
) -> dict[str, Any]:
    """Tokens, diagnostics, et — si *line*/*col* sont donnés — complétions.

    *text* est le brouillon affiché par l'éditeur : s'il est fourni, il prime
    sur le contenu du disque, pour que la colorisation et les diagnostics
    restent justes pendant que l'utilisateur tape, avant tout enregistrement.
    Omis, le contenu vient du disque, comme n'importe quelle autre lecture de
    l'espace Source.
    """
    root = project_root.resolve()
    target = workspace_api.safe_relpath(root, raw_path)
    tier = workspace_api.tier_of(root, target)
    if tier is None:
        raise workspace_api.WorkspacePathError(
            "ce chemin n'appartient à aucun étage de la vue Source"
        )
    rel = target.relative_to(root).as_posix()

    if text is None:
        if not target.is_file():
            raise FileNotFoundError(f"introuvable : {raw_path}")
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise FileNotFoundError(f"illisible : {raw_path}") from exc
        if len(raw) > workspace_api.FILE_TEXT_LIMIT:
            return {"path": rel, "tokens": [], "diagnostics": [], "truncated": True}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": rel, "tokens": [], "diagnostics": [], "binary": True}
    elif len(text.encode("utf-8")) > workspace_api.FILE_TEXT_LIMIT:
        raise ValueError("contenu trop volumineux")

    glossary_payload = workspace_api.glossary_view(root)
    glossary_ids = frozenset(e["id"] for e in glossary_payload["entries"])

    lines = text.split("\n")
    tokens = tokenize(rel, text, glossary_ids)
    diagnostics = diagnose(root, rel, lines, tokens)

    payload: dict[str, Any] = {
        "path": rel,
        "tokens": [t.to_dict() for t in tokens],
        "diagnostics": [d.to_dict() for d in diagnostics],
    }
    if line is not None and col is not None:
        payload["completions"] = complete(root, rel, lines, line, col)
    return payload
