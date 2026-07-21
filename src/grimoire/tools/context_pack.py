"""Génération d'un ``context-pack`` durable par repo.

Capacité produit portée depuis un hook d'atelier : elle matérialise un
``context-pack`` **conforme au contrat du catalogue** (``context-pack`` dans
``web/data/catalogue-export.json`` — le contrat est honoré, jamais défini ici),
utilisable comme source de contexte durable et réutilisable pour l'intake, sous
l'ordre d'autorité ORC-06 (source active > preuve vérifiée > mémoire durable >
similarité).

Déterministe et sans effet de bord hors du fichier produit.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CONTEXT_PACK_SCHEMA_VERSION = "grimoire-context-pack/v1"


@dataclass(frozen=True)
class _Candidate:
    rel: str
    reason: str
    confidence: str


# Sources candidates par ordre d'autorité ORC-06 décroissant : gouvernance
# active d'abord, puis structure, puis preuve/historique.
_CANDIDATES: tuple[_Candidate, ...] = (
    _Candidate("CLAUDE.md", "Instructions actives de gouvernance du repo", "high"),
    _Candidate("AGENTS.md", "Contrat d'agents actif", "high"),
    _Candidate(
        ".github/copilot-instructions.md", "Instructions actives de l'atelier", "high"
    ),
    _Candidate("README.md", "Description de référence du repo", "medium"),
    _Candidate("pyproject.toml", "Manifeste de build / dépendances", "medium"),
    _Candidate("CHANGELOG.md", "Historique vérifié des changements", "medium"),
)


def _git_head(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build_context_pack(
    root: Path, *, now: datetime | None = None, ttl_days: int = 30
) -> dict[str, Any]:
    """Construit un context-pack conforme au contrat catalogue pour *root*."""
    now = now or datetime.now(UTC).replace(microsecond=0)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for cand in _CANDIDATES:
        path = root / cand.rel
        if path.is_file():
            raw = path.read_bytes()
            included.append(
                {
                    "path": cand.rel,
                    "status": "included",
                    "reason": cand.reason,
                    "confidence": cand.confidence,
                    "sha256": hashlib.sha256(raw).hexdigest()[:16],
                    "lines": len(raw.splitlines()),
                }
            )
        else:
            excluded.append(
                {"path": cand.rel, "status": "absent", "reason": "non présent"}
            )
    head = _git_head(root)
    sufficient = any(
        s["path"] in {"CLAUDE.md", "AGENTS.md", "README.md"} for s in included
    )
    return {
        "schemaVersion": CONTEXT_PACK_SCHEMA_VERSION,
        "contract": "context-pack",
        "mission_id": f"repo-context:{root.name}",
        "context_profile": "repo-durable",
        "budget": "medium",
        "objective": (
            "Contexte durable et réutilisable du repo pour l'intake, sous "
            "l'ordre d'autorité ORC-06."
        ),
        "included_sources": included,
        "excluded_sources": excluded,
        "constraints": [
            "Vérité : les sources incluses priment sur mémoire/similarité (ORC-06).",
            "Fraîcheur : invalider si HEAD change ou après expiry.",
        ],
        "scorecard": {
            "sufficiency": "sufficient" if sufficient else "partial",
            "provenance": "repo-local",
            "freshness": head or "unknown",
            "included": len(included),
            "excluded": len(excluded),
        },
        "open_questions": []
        if sufficient
        else ["Aucune source de gouvernance active (CLAUDE.md/AGENTS.md) trouvée."],
        "expiry": {
            "generatedAt": now.isoformat(),
            "ttlDays": ttl_days,
            "expiresAt": (now + timedelta(days=ttl_days)).isoformat(),
            "invalidateOn": "git HEAD change",
            "head": head,
        },
    }


def default_output_path(root: Path) -> Path:
    """Emplacement par défaut du context-pack durable d'un repo."""
    return (
        root / "_grimoire-runtime-output" / "repo-contexts" / f"{root.name}.context-pack.json"
    )
