"""Ce qu'une mise à jour écrirait dans un projet, par catégorie et par fichier (P44).

Lecture pure : le plan de ``grimoire up`` (:func:`grimoire.cli.cmd_up.kit_refresh_plan`)
est construit puis comparé, octet pour octet, à ce qui est sur le disque. Rien
n'est écrit, aucun sous-processus n'est lancé — c'est ce qui permet au cockpit
d'afficher la liste des changements et le diff d'un fichier au clic, sans
passer par la route qui écrit (``POST /api/projects/update``).

Même règle que ``ProjectScaffolder.execute`` : l'étage ``seed`` n'est jamais
écrasé quand il existe, l'étage ``kit`` est réécrit s'il diffère.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Change",
    "category_of",
    "changes_view",
    "diff_view",
]

#: Au-delà, le diff d'un fichier n'est plus un diff lisible dans une fiche :
#: la réponse le dit (``truncated``) au lieu de charger un fichier entier.
MAX_DIFF_CHARS = 200_000

#: Catégories d'affichage, dans l'ordre. L'identité (`id`) sert aux tests et au
#: DOM ; le libellé est ce que lit l'utilisateur.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("agents", "Agents"),
    ("kit", "Kit (_grimoire/kit)"),
    ("copilot", "Instructions et prompts Copilot (.github)"),
    ("hotes", "Hôtes et configuration MCP"),
    ("autres", "Autres fichiers"),
)

_STATUS_LABEL = {"nouveau": "nouveau", "modifie": "modifié"}


@dataclass(frozen=True, slots=True)
class Change:
    """Un fichier que la mise à jour créerait (``nouveau``) ou réécrirait (``modifie``)."""

    path: str
    status: str
    added: int
    removed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "label": _STATUS_LABEL[self.status],
            "added": self.added,
            "removed": self.removed,
        }


def category_of(rel: str) -> str:
    """L'identifiant de catégorie d'un chemin relatif au projet (séparateurs `/`)."""
    parts = rel.split("/")
    if "agents" in parts[:-1]:
        return "agents"
    if rel.startswith("_grimoire/"):
        return "kit"
    if rel.startswith(".github/"):
        return "copilot"
    first = parts[0]
    if first in {".claude", ".cursor", ".codex", ".gemini", ".vscode"} or first in {".mcp.json", "CLAUDE.md", "AGENTS.md"}:
        return "hotes"
    return "autres"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _wanted(plan: Any, root: Path) -> dict[str, str]:
    """Le contenu texte que le plan écrirait, par chemin relatif.

    Seuls les fichiers texte UTF-8 sont comparables ligne à ligne ; un binaire
    ou un fichier illisible n'est pas listé (il n'a pas de diff à montrer).
    """
    from grimoire.core.scaffold import TIER_SEED, _render_placeholders, is_build_artifact

    wanted: dict[str, str] = {}

    def keep(dst: Path, tier: str, content: str | None) -> None:
        if content is None:
            return
        if tier == TIER_SEED and dst.exists():
            return
        try:
            rel = dst.resolve().relative_to(root).as_posix()
        except ValueError:
            return
        wanted[rel] = content

    for copy in plan.copies:
        if copy.src.is_dir():
            for entry in sorted(copy.src.rglob("*")):
                if not entry.is_file() or is_build_artifact(entry.relative_to(copy.src)):
                    continue
                keep(copy.dst / entry.relative_to(copy.src), copy.tier, _read(entry))
            continue
        content = _read(copy.src)
        if content is not None and copy.render_vars:
            content = _render_placeholders(content, copy.render_vars)
        keep(copy.dst, copy.tier, content)
    for tpl in plan.templates:
        keep(tpl.dst, tpl.tier, tpl.content)
    return wanted


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _count(base: str, head: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(_lines(base), _lines(head), n=0):
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _pending(project_root: Path) -> tuple[dict[str, str], list[Change], int]:
    from grimoire.cli.cmd_up import kit_refresh_plan

    root = project_root.resolve()
    wanted = _wanted(kit_refresh_plan(root), root)
    changes: list[Change] = []
    unchanged = 0
    for rel in sorted(wanted):
        current = _read(root / rel)
        if current is None:
            if (root / rel).exists():
                continue  # existe mais illisible en texte : pas comparable
            added, _ = _count("", wanted[rel])
            changes.append(Change(rel, "nouveau", added, 0))
        elif current == wanted[rel]:
            unchanged += 1
        else:
            added, removed = _count(current, wanted[rel])
            changes.append(Change(rel, "modifie", added, removed))
    return wanted, changes, unchanged


def changes_view(project_root: Path) -> dict[str, Any]:
    """``GET /api/workspace/upgrade/changes`` — les changements, groupés par catégorie."""
    _, changes, unchanged = _pending(project_root)
    by_category: dict[str, list[Change]] = {cat_id: [] for cat_id, _ in CATEGORIES}
    for change in changes:
        by_category[category_of(change.path)].append(change)
    return {
        "categories": [
            {"id": cat_id, "label": label, "files": [c.to_dict() for c in by_category[cat_id]]}
            for cat_id, label in CATEGORIES
            if by_category[cat_id]
        ],
        "total": len(changes),
        "unchanged": unchanged,
    }


def diff_view(project_root: Path, raw_path: str | None) -> dict[str, Any]:
    """``GET /api/workspace/upgrade/diff?path=`` — le diff d'un fichier que la mise à jour toucherait.

    Un chemin que la mise à jour ne toucherait pas est refusé (``ValueError``,
    400) : cette route ne lit jamais un fichier arbitraire du projet.
    """
    if not raw_path:
        msg = "paramètre `path` manquant"
        raise ValueError(msg)
    root = project_root.resolve()
    wanted, changes, _ = _pending(root)
    change = next((c for c in changes if c.path == raw_path), None)
    if change is None:
        msg = f"cette mise à jour ne toucherait pas : {raw_path}"
        raise ValueError(msg)
    current = _read(root / raw_path) or ""
    diff = "".join(
        difflib.unified_diff(
            _lines(current),
            _lines(wanted[raw_path]),
            fromfile=f"projet/{raw_path}",
            tofile=f"après mise à jour/{raw_path}",
        )
    )
    truncated = len(diff) > MAX_DIFF_CHARS
    return {
        "path": raw_path,
        "status": change.status,
        "label": _STATUS_LABEL[change.status],
        "added": change.added,
        "removed": change.removed,
        "unified": diff[:MAX_DIFF_CHARS],
        "truncated": truncated,
    }
