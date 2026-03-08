#!/usr/bin/env python3
"""
conversation-branch.py — Conversation Branching Grimoire (BM-44 Story 5.1).
============================================================

Étend le Session Branching (BM-16) avec le branching de contexte
conversationnel. Chaque branche a son propre state.json avec
contexte isolé, snapshot de fichiers chargés, conversation résumée.

Modes :
  branch   — Créer une nouvelle branche de conversation
  list     — Lister les branches et leur arbre
  switch   — Basculer sur une branche
  info     — Détails d'une branche
  archive  — Archiver une branche terminée
  delete   — Supprimer une branche archivée

Usage :
  python3 conversation-branch.py --project-root . branch --name "explore-microservices" \\
    --agent architect --purpose "Explorer migration vers microservices"
  python3 conversation-branch.py --project-root . list
  python3 conversation-branch.py --project-root . switch --name "explore-microservices"
  python3 conversation-branch.py --project-root . info --name "explore-microservices"

Stdlib only.

Références :
  - Grimoire Session Branching (BM-16): framework/sessions/README.md
  - Git branching model: https://nvie.com/posts/a-successful-git-branching-model/
  - Letta conversation branching: https://github.com/letta-ai/letta
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

CONVERSATION_BRANCH_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

RUNS_DIR = "_grimoire-output/.runs"
DEFAULT_BRANCH = "main"
BRANCH_MANIFEST = "branch.json"
STATE_FILE = "state.json"
MAX_BRANCHES = 20

VALID_STATUSES = frozenset({"active", "archived", "merged", "deleted"})


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ContextSnapshot:
    """Snapshot du contexte conversationnel."""

    loaded_files_hash: str = ""
    loaded_files: list[str] = field(default_factory=list)
    conversation_summary: str = ""
    active_agents: list[str] = field(default_factory=list)
    decisions_in_branch: list[str] = field(default_factory=list)
    qdrant_snapshot_id: str = ""
    tokens_used: int = 0


@dataclass
class BranchInfo:
    """Informations sur une branche de conversation."""

    name: str
    parent: str = DEFAULT_BRANCH
    created_at: str = ""
    created_by: str = ""
    purpose: str = ""
    status: str = "active"
    context_snapshot: ContextSnapshot = field(default_factory=ContextSnapshot)
    children: list[str] = field(default_factory=list)
    artifacts_count: int = 0
    last_activity: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not self.last_activity:
            self.last_activity = self.created_at

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> BranchInfo:
        snapshot_data = data.get("context_snapshot", {})
        snapshot = ContextSnapshot(**{
            k: v for k, v in snapshot_data.items()
            if k in ContextSnapshot.__dataclass_fields__
        })
        return cls(
            name=data.get("name", ""),
            parent=data.get("parent", DEFAULT_BRANCH),
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", ""),
            purpose=data.get("purpose", ""),
            status=data.get("status", "active"),
            context_snapshot=snapshot,
            children=data.get("children", []),
            artifacts_count=data.get("artifacts_count", 0),
            last_activity=data.get("last_activity", ""),
        )


@dataclass
class BranchTree:
    """Arbre des branches."""

    branches: list[BranchInfo] = field(default_factory=list)
    active_branch: str = DEFAULT_BRANCH
    total_branches: int = 0
    active_count: int = 0
    archived_count: int = 0


# ── Branch Manager ───────────────────────────────────────────────────────────


class BranchManager:
    """
    Gère les branches de conversation Grimoire.

    Structure des fichiers :
      _grimoire-output/.runs/{branch-name}/branch.json
      _grimoire-output/.runs/{branch-name}/state.json
      _grimoire-output/.runs/{branch-name}/*.md (artifacts)
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.runs_dir = project_root / RUNS_DIR
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_main_branch()

    def _ensure_main_branch(self) -> None:
        """S'assure que la branche main existe."""
        main_dir = self.runs_dir / DEFAULT_BRANCH
        if not main_dir.exists():
            main_dir.mkdir(parents=True, exist_ok=True)
            info = BranchInfo(name=DEFAULT_BRANCH, parent="", purpose="Branche principale")
            self._save_branch_info(info)

    def _branch_dir(self, name: str) -> Path:
        return self.runs_dir / name

    def _save_branch_info(self, info: BranchInfo) -> None:
        branch_dir = self._branch_dir(info.name)
        branch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = branch_dir / BRANCH_MANIFEST
        manifest_path.write_text(
            json.dumps(info.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_branch_info(self, name: str) -> BranchInfo | None:
        manifest = self._branch_dir(name) / BRANCH_MANIFEST
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return BranchInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def _compute_files_hash(self) -> str:
        """Hash des fichiers mémoire et contexte courants."""
        memory_dir = self.project_root / "_grimoire" / "_memory"
        files = []
        if memory_dir.exists():
            files = sorted(memory_dir.rglob("*.md"))
        hasher = hashlib.sha256()
        for f in files[:50]:  # Cap for perf
            try:
                hasher.update(f.read_bytes())
            except OSError:
                continue
        return hasher.hexdigest()[:12]

    def _discover_loaded_files(self) -> list[str]:
        """Liste les fichiers mémoire/contexte chargés."""
        memory_dir = self.project_root / "_grimoire" / "_memory"
        files = []
        if memory_dir.exists():
            for f in sorted(memory_dir.rglob("*.md"))[:50]:
                try:
                    files.append(str(f.relative_to(self.project_root)))
                except ValueError:
                    files.append(str(f))
        return files

    def _get_active_branch(self) -> str:
        """Lit la branche active depuis project-context.yaml ou un marker."""
        marker = self.runs_dir / ".active-branch"
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip() or DEFAULT_BRANCH
        return DEFAULT_BRANCH

    def _set_active_branch(self, name: str) -> None:
        """Écrit la branche active."""
        marker = self.runs_dir / ".active-branch"
        marker.write_text(name + "\n", encoding="utf-8")

    def branch(
        self,
        name: str,
        agent: str = "",
        purpose: str = "",
        parent: str = "",
    ) -> BranchInfo:
        """
        Crée une nouvelle branche de conversation.

        Args:
            name: Nom de la branche (ex: "explore-microservices")
            agent: Agent qui crée la branche
            purpose: Raison de la branche
            parent: Branche parent (défaut: branche active courante)

        Returns:
            BranchInfo de la nouvelle branche
        """
        # Validate name
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Nom de branche invalide: '{name}' (alphanum, -, _ uniquement)")

        # Check not exists
        if self._branch_dir(name).exists():
            raise ValueError(f"La branche '{name}' existe déjà")

        # Check max branches
        existing = self.list_branches()
        if len(existing.branches) >= MAX_BRANCHES:
            raise ValueError(f"Maximum {MAX_BRANCHES} branches atteint. Archivez ou supprimez des branches.")

        # Resolve parent
        if not parent:
            parent = self._get_active_branch()

        # Create context snapshot
        snapshot = ContextSnapshot(
            loaded_files_hash=self._compute_files_hash(),
            loaded_files=self._discover_loaded_files(),
            active_agents=[agent] if agent else [],
        )

        info = BranchInfo(
            name=name,
            parent=parent,
            created_by=agent,
            purpose=purpose,
            context_snapshot=snapshot,
        )

        self._save_branch_info(info)

        # Update parent's children
        parent_info = self._load_branch_info(parent)
        if parent_info:
            if name not in parent_info.children:
                parent_info.children.append(name)
                self._save_branch_info(parent_info)

        return info

    def switch(self, name: str) -> BranchInfo:
        """
        Bascule sur une branche existante.

        Args:
            name: Nom de la branche cible

        Returns:
            BranchInfo de la branche activée
        """
        info = self._load_branch_info(name)
        if not info:
            raise ValueError(f"Branche '{name}' non trouvée")
        if info.status != "active":
            raise ValueError(f"Branche '{name}' status '{info.status}' — seules les branches actives sont switchables")

        self._set_active_branch(name)

        # Update last activity
        info.last_activity = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_branch_info(info)

        return info

    def get_info(self, name: str) -> BranchInfo | None:
        """Retourne les infos d'une branche."""
        info = self._load_branch_info(name)
        if info:
            # Count artifacts
            branch_dir = self._branch_dir(name)
            info.artifacts_count = len(list(branch_dir.glob("*.md")))
        return info

    def list_branches(self) -> BranchTree:
        """Liste toutes les branches."""
        branches = []
        active_branch = self._get_active_branch()

        if self.runs_dir.exists():
            for d in sorted(self.runs_dir.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    info = self._load_branch_info(d.name)
                    if info:
                        info.artifacts_count = len(list(d.glob("*.md")))
                        branches.append(info)

        active_count = sum(1 for b in branches if b.status == "active")
        archived_count = sum(1 for b in branches if b.status == "archived")

        return BranchTree(
            branches=branches,
            active_branch=active_branch,
            total_branches=len(branches),
            active_count=active_count,
            archived_count=archived_count,
        )

    def archive(self, name: str) -> BranchInfo:
        """Archive une branche terminée."""
        if name == DEFAULT_BRANCH:
            raise ValueError("Impossible d'archiver la branche main")

        info = self._load_branch_info(name)
        if not info:
            raise ValueError(f"Branche '{name}' non trouvée")

        info.status = "archived"
        info.last_activity = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_branch_info(info)

        # If archived branch was active, switch to main
        if self._get_active_branch() == name:
            self._set_active_branch(DEFAULT_BRANCH)

        return info

    def delete(self, name: str) -> bool:
        """Supprime une branche archivée."""
        if name == DEFAULT_BRANCH:
            raise ValueError("Impossible de supprimer la branche main")

        info = self._load_branch_info(name)
        if not info:
            raise ValueError(f"Branche '{name}' non trouvée")
        if info.status not in ("archived", "deleted"):
            raise ValueError(f"Seules les branches archivées peuvent être supprimées (status: {info.status})")

        branch_dir = self._branch_dir(name)
        if branch_dir.exists():
            shutil.rmtree(branch_dir)

        # Update parent's children
        parent_info = self._load_branch_info(info.parent)
        if parent_info and name in parent_info.children:
            parent_info.children.remove(name)
            self._save_branch_info(parent_info)

        return True

    def update_snapshot(self, name: str, **kwargs) -> BranchInfo | None:
        """Met à jour le snapshot de contexte d'une branche."""
        info = self._load_branch_info(name)
        if not info:
            return None
        for key, value in kwargs.items():
            if hasattr(info.context_snapshot, key):
                setattr(info.context_snapshot, key, value)
        info.last_activity = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_branch_info(info)
        return info


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_conversation_branch(
    project_root: str,
    action: str = "list",
    name: str = "",
    agent: str = "",
    purpose: str = "",
) -> dict:
    """
    MCP tool `bmad_conversation_branch` — gère les branches de conversation.
    """
    root = Path(project_root).resolve()
    manager = BranchManager(root)

    if action == "list":
        tree = manager.list_branches()
        return {
            "active_branch": tree.active_branch,
            "total": tree.total_branches,
            "branches": [b.to_dict() for b in tree.branches],
        }
    elif action == "branch":
        if not name:
            return {"error": "Le paramètre 'name' est requis"}
        try:
            info = manager.branch(name, agent=agent, purpose=purpose)
            return {"success": True, "branch": info.to_dict()}
        except ValueError as e:
            return {"success": False, "error": str(e)}
    elif action == "switch":
        if not name:
            return {"error": "Le paramètre 'name' est requis"}
        try:
            info = manager.switch(name)
            return {"success": True, "branch": info.to_dict()}
        except ValueError as e:
            return {"success": False, "error": str(e)}
    elif action == "info":
        info = manager.get_info(name)
        if info:
            return info.to_dict()
        return {"error": f"Branche '{name}' non trouvée"}
    else:
        return {"error": f"Action inconnue: {action}"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_tree(tree: BranchTree) -> None:
    """Affiche l'arbre des branches."""
    print(f"\n  🌿 Branches de conversation ({tree.total_branches})")
    print(f"  {'─' * 60}")
    print(f"  Active : {tree.active_branch}")
    print(f"  ({tree.active_count} actives, {tree.archived_count} archivées)")
    print()

    # Build tree display
    branch_map = {b.name: b for b in tree.branches}
    roots = [b for b in tree.branches if b.parent in ("", DEFAULT_BRANCH) or b.name == DEFAULT_BRANCH]

    def _print_branch(info: BranchInfo, indent: int = 0) -> None:
        prefix = "  " + "│   " * indent
        is_active = "◉" if info.name == tree.active_branch else "○"
        status_icon = {
            "active": "🟢",
            "archived": "📦",
            "merged": "🔀",
            "deleted": "🗑️",
        }.get(info.status, "❓")
        artifacts = f" [{info.artifacts_count} artifacts]" if info.artifacts_count else ""
        purpose = f" — {info.purpose}" if info.purpose else ""
        print(f"{prefix}{is_active} {status_icon} {info.name}{artifacts}{purpose}")

        for child_name in info.children:
            child = branch_map.get(child_name)
            if child:
                _print_branch(child, indent + 1)

    for root in roots:
        _print_branch(root)

    print()


def _print_info(info: BranchInfo) -> None:
    """Affiche les détails d'une branche."""
    status_icon = {"active": "🟢", "archived": "📦", "merged": "🔀"}.get(info.status, "❓")
    print(f"\n  {status_icon} Branche : {info.name}")
    print(f"  {'─' * 50}")
    print(f"  Parent      : {info.parent}")
    print(f"  Créée le    : {info.created_at}")
    print(f"  Créée par   : {info.created_by or '-'}")
    print(f"  Purpose     : {info.purpose or '-'}")
    print(f"  Status      : {info.status}")
    print(f"  Artifacts   : {info.artifacts_count}")
    print(f"  Last active : {info.last_activity}")

    cs = info.context_snapshot
    if cs.loaded_files or cs.conversation_summary:
        print("\n  Context Snapshot :")
        if cs.loaded_files_hash:
            print(f"    Files hash : {cs.loaded_files_hash}")
        if cs.loaded_files:
            print(f"    Loaded     : {len(cs.loaded_files)} files")
        if cs.active_agents:
            print(f"    Agents     : {', '.join(cs.active_agents)}")
        if cs.decisions_in_branch:
            print(f"    Decisions  : {len(cs.decisions_in_branch)}")
        if cs.tokens_used:
            print(f"    Tokens     : {cs.tokens_used:,}")
        if cs.conversation_summary:
            print(f"    Summary    : {cs.conversation_summary[:100]}...")

    if info.children:
        print(f"\n  Children : {', '.join(info.children)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conversation Branching — Branches de conversation Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"conversation-branch {CONVERSATION_BRANCH_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # branch
    br_p = sub.add_parser("branch", help="Créer une nouvelle branche")
    br_p.add_argument("--name", required=True, help="Nom de la branche")
    br_p.add_argument("--agent", default="", help="Agent créateur")
    br_p.add_argument("--purpose", default="", help="Raison de la branche")
    br_p.add_argument("--parent", default="", help="Branche parent")
    br_p.add_argument("--json", action="store_true", help="Output JSON")

    # list
    list_p = sub.add_parser("list", help="Lister les branches")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # switch
    sw_p = sub.add_parser("switch", help="Basculer sur une branche")
    sw_p.add_argument("--name", required=True, help="Nom de la branche")

    # info
    inf_p = sub.add_parser("info", help="Détails d'une branche")
    inf_p.add_argument("--name", required=True, help="Nom de la branche")
    inf_p.add_argument("--json", action="store_true", help="Output JSON")

    # archive
    arch_p = sub.add_parser("archive", help="Archiver une branche")
    arch_p.add_argument("--name", required=True, help="Nom de la branche")

    # delete
    del_p = sub.add_parser("delete", help="Supprimer une branche archivée")
    del_p.add_argument("--name", required=True, help="Nom de la branche")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    manager = BranchManager(project_root)

    if args.command == "branch":
        try:
            info = manager.branch(
                name=args.name,
                agent=args.agent,
                purpose=args.purpose,
                parent=args.parent,
            )
            if getattr(args, "json", False):
                print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"\n  ✅ Branche '{info.name}' créée (parent: {info.parent})\n")
        except ValueError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        tree = manager.list_branches()
        if getattr(args, "json", False):
            print(json.dumps({
                "active_branch": tree.active_branch,
                "total": tree.total_branches,
                "branches": [b.to_dict() for b in tree.branches],
            }, ensure_ascii=False, indent=2))
        else:
            _print_tree(tree)

    elif args.command == "switch":
        try:
            info = manager.switch(args.name)
            print(f"\n  🔀 Basculé sur '{info.name}'\n")
        except ValueError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "info":
        info = manager.get_info(args.name)
        if not info:
            print(f"  ❌ Branche '{args.name}' non trouvée", file=sys.stderr)
            sys.exit(1)
        if getattr(args, "json", False):
            print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_info(info)

    elif args.command == "archive":
        try:
            info = manager.archive(args.name)
            print(f"\n  📦 Branche '{info.name}' archivée\n")
        except ValueError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "delete":
        try:
            manager.delete(args.name)
            print(f"\n  🗑️  Branche '{args.name}' supprimée\n")
        except ValueError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
