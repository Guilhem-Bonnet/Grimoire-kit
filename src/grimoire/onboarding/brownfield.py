"""Base de connaissance d'un projet existant : dérivée du code, sourcée, vérifiable.

``build_knowledge`` lit le projet sans modèle ni réseau (graphe de code pour
Python, motifs pour JS/TS, manifestes de la racine) et écrit
``_grimoire-output/knowledge/project-knowledge.{json,md}``. Deux appels sur
le même code écrivent les mêmes octets : aucun horodatage, tout est trié.

Chaque fait cite ses sources (``fichier:ligne``) et l'empreinte des fichiers
cités ; la base garde aussi l'empreinte de tout ce qu'elle a lu.
``check_knowledge`` recalcule et rend ``current``, ``stale`` (fichiers
ajoutés / modifiés / supprimés, faits touchés) ou ``unverified`` (base
absente, illisible, de schéma inconnu) — jamais ``current`` sans comparaison.

Ce qui demande du jugement (intention métier, raison d'un découpage, commande
introuvable) n'est pas deviné : c'est une question ouverte, au format des
questions structurées (``grimoire task ask <id> --from <base>.json``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.codegraph.freshness import combine_hashes, diff_hashes, hash_files
from grimoire.core import fsio
from grimoire.onboarding.facts import Fact, collect_project_files, fact_fingerprint
from grimoire.onboarding.scan_js import JsFile, JsScan, scan_js
from grimoire.onboarding.scan_manifests import ManifestScan, scan_manifests
from grimoire.onboarding.scan_python import LARGE_MODULE_LINES, PyScan, scan_python

__all__ = [
    "KNOWLEDGE_DIR",
    "KNOWLEDGE_JSON",
    "KNOWLEDGE_MD",
    "SHARED_CONTEXT",
    "KnowledgeFreshness",
    "build_knowledge",
    "check_knowledge",
    "render_markdown",
    "sync_shared_context",
    "write_knowledge",
]

KNOWLEDGE_DIR = Path("_grimoire-output/knowledge")
KNOWLEDGE_JSON = KNOWLEDGE_DIR / "project-knowledge.json"
KNOWLEDGE_MD = KNOWLEDGE_DIR / "project-knowledge.md"
SHARED_CONTEXT = Path("_grimoire/_memory/shared-context.md")
_SCHEMA = 1
_KIND = "grimoire.project-knowledge"
_MAX_ROWS = 12
_MAX_QUESTIONS = 12
_BLOCK_START = "<!-- grimoire:project-knowledge:start -->"
_BLOCK_END = "<!-- grimoire:project-knowledge:end -->"
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("components", "Composants"),
    ("entry_points", "Points d'entrée"),
    ("commands", "Commandes"),
    ("dependencies", "Dépendances"),
    ("conventions", "Conventions observées"),
    ("risks", "Zones à risque"),
    ("tests", "Carte des tests"),
)
_LIMITS = (
    "Le graphe de code ne lit que Python ; JS/TS est lu par motifs (fichiers, imports, serveurs), sans symboles.",
    "Intentions métier et raisons de conception ne sont pas déduites : elles sont dans les questions ouvertes.",
    "La couverture est lue dans `coverage.xml` s'il existe ; aucun test n'est exécuté.",
    "Un composant testé seulement par sous-processus (CLI lancée par un test) apparaît comme non testé.",
)


def _question(key: str, text: str, why: str) -> dict[str, Any]:
    return {"key": key, "text": text, "kind": "choice", "blocking": False, "why": why}


def _questions(
    root: Path, py: PyScan, manifests: ManifestScan, has_entry: bool, js_components: dict[str, list[JsFile]],
) -> list[dict[str, Any]]:
    readme = next((n for n in ("README.md", "README.rst", "README") if (root / n).is_file()), "")
    out = [_question(
        "onboard:purpose", "Quel problème métier ce projet résout-il, et pour qui ?",
        "L'intention métier ne se déduit pas du code" + (f" ; `{readme}` existe, à confirmer ou corriger." if readme else "."),
    )]
    for need, (status, command, source) in manifests.commands.items():
        if status == "assumed":
            out.append(_question(f"onboard:command:{need}", f"La commande {need} est-elle bien `{command}` ?",
                                 f"Supposée d'après le marqueur `{source}`, aucun script ni configuration ne la porte."))
        elif status == "unknown" and need == "test-runner":
            out.append(_question("onboard:command:test-runner", "Quelle commande lance les tests ?",
                                 "Ni déclarée, ni observée, ni supposée par un marqueur."))
    if not has_entry:
        out.append(_question("onboard:entry", "Comment le projet se lance-t-il (CLI, serveur, bibliothèque importée) ?",
                             "Aucun point d'entrée trouvé (script, garde __main__, serveur, bin)."))
    sized = [(sum(m.lines for m in py.components[c]), c) for c in py.undocumented]
    sized += [(sum(f.lines for f in members), c) for c, members in js_components.items()]
    for _, comp in sorted(sized, key=lambda x: (-x[0], x[1]))[:5]:
        out.append(_question(f"onboard:responsibility:{comp}", f"Quelle est la responsabilité de `{comp}` ?",
                             "Aucune docstring ne la déclare ; la base ne la devine pas."))
    for mod in py.large[:3]:
        out.append(_question(f"onboard:large:{mod.key}",
                             f"`{mod.key}` ({mod.lines} lignes) : découpage voulu ou dette à réduire ?",
                             "La taille se mesure, la raison d'un découpage ne se déduit pas."))
    return out[:_MAX_QUESTIONS]


def _large_facts(py: PyScan, js: JsScan) -> list[Fact]:
    big = [(m.key, m.lines) for m in py.large] + [(f.key, f.lines) for f in js.files if f.lines > LARGE_MODULE_LINES]
    return [
        Fact(id=f"large:{key}", section="risks", text=f"`{key}` : {lines} lignes (seuil {LARGE_MODULE_LINES})",
             sources=(f"{key}:1",), data={"lines": lines})
        for key, lines in sorted(big, key=lambda x: (-x[1], x[0]))
    ]


def _summary(
    files: dict[str, Path], facts: list[Fact], questions: list[dict[str, Any]], commands: dict[str, tuple[str, str, str]],
) -> dict[str, Any]:
    by_section: dict[str, int] = {}
    for fact in facts:
        by_section[fact.section] = by_section.get(fact.section, 0) + 1
    langs = {"python": sum(1 for k in files if k.endswith(".py")),
             "javascript/typescript": sum(1 for k in files if k.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")))}
    return {
        "files_read": len(files),
        "languages": {k: v for k, v in langs.items() if v},
        "facts": len(facts),
        "by_section": dict(sorted(by_section.items())),
        "questions": len(questions),
        "commands": {need: {"status": s, "command": c} for need, (s, c, _) in commands.items()},
    }


def build_knowledge(root: Path) -> dict[str, Any]:
    """Lit le projet sous *root* et rend la base (sans rien écrire)."""
    root = root.resolve()
    files = collect_project_files(root)
    hashes = hash_files(files)
    py = scan_python(root, files)
    js = scan_js(files)
    manifests = scan_manifests(root)
    facts = [*py.facts, *js.facts, *manifests.facts, *_large_facts(py, js)]
    order = {name: i for i, (name, _) in enumerate(_SECTIONS)}
    facts.sort(key=lambda f: (order.get(f.section, len(order)), f.id))
    has_entry = any(f.section == "entry_points" for f in facts)
    questions = _questions(root, py, manifests, has_entry, js.components)
    return {
        "schema": _SCHEMA,
        "kind": _KIND,
        "generator": "grimoire onboard brownfield",
        "project": root.name,
        "fingerprint": combine_hashes(hashes),
        "summary": _summary(files, facts, questions, manifests.commands),
        "facts": [f.to_dict(hashes) for f in facts],
        "questions": questions,
        "limits": list(_LIMITS),
        "files": hashes,
    }


def _refs(sources: list[str]) -> str:
    with_line = {s.rpartition(":")[0] for s in sources if s.rpartition(":")[2].isdigit()}
    kept = [s for s in sources if s not in with_line]
    shown = [f"`{s}`" for s in kept[:2]]
    more = len(kept) - 2
    return ", ".join(shown) + (f" (+{more})" if more > 0 else "")


def render_markdown(kb: dict[str, Any]) -> str:
    """Vue lisible, courte : au plus quelques lignes par section, le reste est dans le JSON."""
    summary = kb["summary"]
    langs = ", ".join(f"{name} ({n} fichiers)" for name, n in summary["languages"].items()) or "aucun code reconnu"
    lines = [
        f"# Connaissance du projet — {kb['project']}",
        "",
        "> Dérivée du code par `grimoire onboard brownfield`, sans modèle. Chaque ligne cite sa source.",
        "> Avant de s'y fier : `grimoire onboard brownfield --check` (périmée si le code a changé).",
        "",
        f"Code : {langs} · {summary['facts']} faits · {summary['questions']} questions ouvertes · "
        f"empreinte `{kb['fingerprint'][:12]}`",
    ]
    by_section: dict[str, list[dict[str, Any]]] = {}
    for fact in kb["facts"]:
        by_section.setdefault(fact["section"], []).append(fact)
    for name, title in _SECTIONS:
        rows = by_section.get(name, [])
        if not rows:
            continue
        lines += ["", f"## {title}", ""]
        for fact in rows[:_MAX_ROWS]:
            ref = _refs(fact["sources"])
            lines.append(f"- {fact['text']}" + (f" — {ref}" if ref else ""))
        if len(rows) > _MAX_ROWS:
            lines.append(f"- … et {len(rows) - _MAX_ROWS} de plus dans `project-knowledge.json`")
    if kb["questions"]:
        lines += ["", "## Questions ouvertes (à trancher par un humain, pas à deviner)", ""]
        lines += [f"- {q['text']} — {q['why']}" for q in kb["questions"]]
    lines += ["", "## Limites", "", *(f"- {limit}" for limit in kb["limits"])]
    return "\n".join(lines) + "\n"


def write_knowledge(root: Path, kb: dict[str, Any]) -> tuple[Path, Path]:
    """Écrit le JSON et le Markdown ; rend leurs chemins."""
    root = root.resolve()
    json_path, md_path = root / KNOWLEDGE_JSON, root / KNOWLEDGE_MD
    fsio.atomic_write(json_path, json.dumps(kb, indent=2) + "\n")
    fsio.atomic_write(md_path, render_markdown(kb).encode("utf-8", "backslashreplace"))
    return json_path, md_path


def sync_shared_context(root: Path, kb: dict[str, Any]) -> Path | None:
    """Pose (ou remplace) un bloc court dans ``shared-context.md``, lu par tous les agents.

    N'écrit que si le fichier existe (projet enrôlé) ; le reste du fichier
    n'est jamais touché. Rend le chemin écrit, ou ``None``.
    """
    target = root.resolve() / SHARED_CONTEXT
    if not target.is_file():
        return None
    block = "\n".join([
        _BLOCK_START,
        "## Connaissance du code (dérivée, vérifiable)",
        "",
        f"- Base : `{KNOWLEDGE_MD.as_posix()}` (détail : `{KNOWLEDGE_JSON.as_posix()}`), "
        "dérivée du code sans modèle, chaque fait cite sa source.",
        "- Avant de t'y fier : `grimoire onboard brownfield --check` ; périmée, régénère-la.",
        f"- {kb['summary']['questions']} question(s) ouverte(s) : ne pas les deviner, les poser à l'humain.",
        _BLOCK_END,
    ])
    text = target.read_text(encoding="utf-8")
    start, end = text.find(_BLOCK_START), text.find(_BLOCK_END)
    if start != -1 and end > start:
        updated = text[:start] + block + text[end + len(_BLOCK_END):]
    else:
        updated = text.rstrip("\n") + "\n\n" + block + "\n"
    if updated != text:
        fsio.atomic_write(target, updated)
    return target


@dataclass(frozen=True, slots=True)
class KnowledgeFreshness:
    """Verdict de fraîcheur de la base."""

    verdict: str
    reason: str = ""
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    stale_facts: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "reason": self.reason, "added": list(self.added),
            "modified": list(self.modified), "removed": list(self.removed), "stale_facts": list(self.stale_facts),
        }


def check_knowledge(root: Path) -> KnowledgeFreshness:
    """``current`` / ``stale`` / ``unverified`` ; ne lève jamais."""
    root = root.resolve()
    path = root / KNOWLEDGE_JSON
    if not path.is_file():
        return KnowledgeFreshness("unverified", "no_knowledge")
    try:
        kb = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(kb, dict) or kb.get("schema") != _SCHEMA or kb.get("kind") != _KIND:
            return KnowledgeFreshness("unverified", "schema_unknown")
        recorded = {str(k): str(v) for k, v in dict(kb["files"]).items()}
        facts = [(str(f["id"]), [str(s) for s in f["sources"]], str(f["fingerprint"])) for f in kb["facts"]]
    except (OSError, ValueError, KeyError, TypeError):
        return KnowledgeFreshness("unverified", "unreadable")
    try:
        current = hash_files(collect_project_files(root))
    except (OSError, UnicodeError):
        return KnowledgeFreshness("unverified", "source_unreadable")
    added, modified, removed = diff_hashes(recorded, current)
    stale = tuple(sorted(
        fid for fid, sources, fingerprint in facts
        if sources and fact_fingerprint(tuple(sources), current) != fingerprint
    ))
    if combine_hashes(current) == combine_hashes(recorded) and not stale:
        return KnowledgeFreshness("current")
    reason = "files_changed" if (added or modified or removed) else "facts_changed"
    return KnowledgeFreshness("stale", reason, added, modified, removed, stale)

