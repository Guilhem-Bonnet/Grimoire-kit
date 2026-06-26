#!/usr/bin/env python3
"""
Grimoire Memory Seed — peuple le backend mémoire depuis la source-of-truth markdown.

Pourquoi : un store mémoire vide ne sert à rien (search ne rend rien -> les agents
ne l'utilisent pas -> reste vide). Le chemin d'écriture existe (mem0-bridge
add/search -> get_backend) mais rien n'ingère la connaissance déjà sur disque. Ce
script casse ce cercle.

Ingère les concepts markdown curatés (mémoire du projet + dossier optionnel) dans le
backend résolu par get_backend() — donc HONORE l'option de setup memory.vector_database
(lexical sans vecteur OU vectoriel). Idempotent (clé = metadata.source), gate evidence
+ scan de redaction.

Usage:
    python memory_seed.py                          # honore project-context.yaml
    python memory_seed.py --no-vector              # force le backend lexical (offline)
    python memory_seed.py --agent-memory <dir>     # ingère aussi un dossier *.md externe
    python memory_seed.py --dry-run
    python memory_seed.py --search "..."           # démo lecture
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backends  # noqa: E402  (résolu via sys.path ci-dessus)

# Fichiers _memory curatés porteurs de connaissance durable (pas les journaux JSONL).
CURATED = ["shared-context.md", "failure-museum.md", "handoff-log.md", "decisions-log.md"]

SECRET = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*\S+|sk-[A-Za-z0-9]{16,}"
)
PLACEHOLDER = re.compile(r"^_?\s*(aucun|aucune|n/?a|todo|—|-)\b", re.IGNORECASE)


def find_project_root() -> Path:
    """Remonte depuis cwd jusqu'à trouver project-context.yaml (sinon cwd)."""
    cur = Path.cwd().resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "project-context.yaml").exists():
            return parent
    return cur


def project_context(root: Path) -> dict:
    f = root / "project-context.yaml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.exists() else {}


def split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = yaml.safe_load(text[3:end]) or {}
            return (fm if isinstance(fm, dict) else {}), text[end + 4 :].lstrip("\n")
    return {}, text


def user_app_id(ctx: dict) -> tuple[str, str]:
    user = ctx.get("user", {}).get("name", "user").lower().replace(" ", "-")
    proj = ctx.get("project", {}).get("name", "grimoire-project").lower().replace(" ", "-")
    return user, f"grimoire-{proj}"


def collect(root: Path, agent_memory: Path | None) -> list[dict]:
    """Concepts source-of-truth : (text, metadata) prêts à ingérer."""
    concepts: list[dict] = []

    # 1. Dossier mémoire externe optionnel (frontmatter name/description/type).
    if agent_memory and agent_memory.exists():
        for p in sorted(agent_memory.glob("*.md")):
            if p.name.upper() in {"MEMORY.MD", "INDEX.MD"}:
                continue
            fm, body = split_frontmatter(p.read_text(encoding="utf-8"))
            meta = fm.get("metadata") or {}
            concepts.append({
                "text": body.strip(),
                "source": f"agent-memory/{p.name}",
                "type": str(fm.get("type") or meta.get("type") or "memory"),
                "title": str(fm.get("name") or p.stem),
            })

    # 2. Fichiers _memory curatés non triviaux du projet courant.
    mem_dir = root / "_grimoire" / "_memory"
    for name in CURATED:
        p = mem_dir / name
        if not p.exists():
            continue
        _, body = split_frontmatter(p.read_text(encoding="utf-8"))
        meat = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith(("#", ">"))
            and not PLACEHOLDER.match(ln.strip())
        ]
        if len("\n".join(meat)) < 40:  # vide / placeholder -> skip
            continue
        concepts.append({
            "text": "\n".join(meat).strip(),
            "source": f"_memory/{name}",
            "type": "project",
            "title": p.stem,
        })
    return concepts


def gate(c: dict) -> tuple[bool, list[str]]:
    """Promotion gate minimal : evidence (source) + corps non vide + redaction."""
    reasons: list[str] = []
    if not c["text"]:
        return False, ["corps vide"]
    if not c.get("source"):
        return False, ["pas de source (evidence manquante)"]
    if SECRET.search(c["text"]):
        reasons.append("secret détecté -> à caviarder avant provider hosted")
    return True, reasons


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed mémoire Grimoire depuis la source-of-truth")
    ap.add_argument("--no-vector", action="store_true", help="force le backend lexical")
    ap.add_argument("--agent-memory", type=Path, help="dossier *.md externe additionnel")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--search", type=str, help="démo lecture après seed")
    args = ap.parse_args()

    root = find_project_root()
    ctx = project_context(root)
    user_id, app_id = user_app_id(ctx)
    override = {"memory": {"vector_database": False}} if args.no_vector else None
    backend, name = backends.get_backend(override)
    print(f"Backend résolu : '{name}'  (projet={root.name}, user_id={user_id})")

    if args.search:
        hits = backend.search(args.search, user_id=user_id, limit=5)
        print(f"Recherche '{args.search}' -> {len(hits)} hit(s) :")
        for h in hits:
            title = h.get("metadata", {}).get("title", "?")
            print(f"  [{title}] {str(h.get('memory', ''))[:70]}  {h.get('score', '')}")
        return 0

    existing = {m.get("metadata", {}).get("source") for m in backend.get_all(user_id=user_id)}
    concepts = collect(root, args.agent_memory)
    seeded = skipped = flagged = 0
    for c in concepts:
        ok, reasons = gate(c)
        if not ok or c["source"] in existing:
            skipped += 1
            continue
        if any("secret" in r for r in reasons):
            flagged += 1
        if not args.dry_run:
            backend.add(c["text"], user_id=user_id, metadata={
                "agent": "grimoire-master",
                "app_id": app_id,
                "type": c["type"],
                "title": c["title"],
                "source": c["source"],
                "timestamp": datetime.now().isoformat(),
            })
        seeded += 1

    verb = "(dry-run) " if args.dry_run else ""
    print(f"{verb}seedé={seeded}  ignoré={skipped}  flag-redaction={flagged}")
    print(f"store total : {backend.count()} mémoires  ({backend.status().get('search')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
