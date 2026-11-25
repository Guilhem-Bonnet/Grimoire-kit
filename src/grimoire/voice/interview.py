"""L'entretien de cadrage vu depuis la boîte vocale (#1343, lot V4) : état des angles en lecture seule.

- :func:`interview_view` (``GET voice/interview``) : pour chaque entretien entamé, l'état de ses angles
  (ouvert, couvert, écarté). Sans entretien, une liste vide et rien d'écrit.
- :func:`open_interview` : le chantier dont l'entretien est en cours (angles obligatoires encore ouverts),
  pour que la consigne vocale du tour demande une question par message.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from grimoire.core.cadrage_interview import interview_states

__all__ = ["interview_view", "open_interview"]

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    if "error" in row:
        return {"chantier": row["chantier"], "error": row["error"]}
    return {
        "chantier": row["chantier"],
        "kind_label": row.get("kind_label", ""),
        "depth": row.get("depth", ""),
        "complete": bool(row.get("complete")),
        "open_required": list(row.get("open_required", [])),
        "angles": [
            {
                "id": a["id"],
                "title": a["title"],
                "required": a["required"],
                "state": a["state"],
                "answers": len(a.get("answers", [])),
                "reason": a.get("reason", ""),
            }
            for a in row.get("angles", [])
        ],
    }


def interview_view(root: Path, _query: Any = None) -> dict[str, Any]:
    rows = [_compact(r) for r in interview_states(root) if r.get("interview", True)]
    return {"active": any("error" not in r and not r["complete"] for r in rows), "interviews": rows}


def open_interview(root: Path) -> str | None:
    """Le chantier (``principal`` ou un nom sûr) dont l'entretien est en cours, ou ``None``."""
    try:
        rows = interview_states(root)
    except Exception:  # un cadrage illisible ne casse pas un tour
        return None
    for row in rows:
        name = str(row["chantier"])
        if "error" in row or row.get("complete", True) or not row.get("blocking", True):
            continue
        if name == "principal" or _SLUG_RE.match(name):
            return name
    return None
