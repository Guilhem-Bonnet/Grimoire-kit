"""W2-07 — le check ``profile_floor`` de ``grimoire doctor``.

Extrait hors de :mod:`grimoire.cli.app` (fichier déjà au-dessus du seuil de
taille, ``scripts/code-ratchet-baseline.json``) : la seule appelante est la
commande ``doctor``. Jamais un ``FAIL`` : un plancher manquant est une dette de
gouvernance, pas un projet cassé, mais il doit être nommé avec son remède.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["apply_profile_floor_check", "profile_floor_entry"]

_GOVERNED = "governed"


def apply_profile_floor_check(project_root: Path, results: list[dict[str, Any]], *, fmt: str, console: Any) -> None:
    """Append the ``profile_floor`` doctor entry to *results* and print it (one line in ``cli/app.py``)."""
    entry = profile_floor_entry(project_root)
    if entry is None:
        return
    results.append(entry)
    if fmt != "json":
        tag = "[yellow]WARN[/yellow]" if entry.get("level") == "warn" else "[green]OK[/green]"
        console.print(f"  {tag}  {entry['detail']}")


def profile_floor_entry(project_root: Path) -> dict[str, Any] | None:
    """Un résultat ``doctor`` pour ``profile_floor``, ou ``None`` hors d'un projet enrôlé au standard."""
    from grimoire.core.standard_generation import PROFILE_FLOOR_FILE, PROFILE_LADDER
    from grimoire.core.standard_state import (
        declared_profile_id,
        is_standard_enrolled,
        profile_below_floor,
        profile_floor_id,
    )

    if not is_standard_enrolled(project_root):
        return None
    declared = declared_profile_id(project_root, write_cache=False)
    floor = profile_floor_id(project_root, write_cache=False)
    rel = PROFILE_FLOOR_FILE.as_posix()
    warn = "warn"
    if not floor:
        if declared in PROFILE_LADDER and PROFILE_LADDER.index(declared) >= PROFILE_LADDER.index(_GOVERNED):
            return {
                "name": "profile_floor",
                "passed": True,
                "level": warn,
                "detail": (
                    f"profil « {declared} » sans plancher committé : un `profile:` abaissé dans "
                    f"standard-profile.yaml relâche tous les seuils sans trace. Crée `{rel}` "
                    f"(`floor: {declared}`) et committe-le."
                ),
            }
        return None
    written = (project_root / PROFILE_FLOOR_FILE).is_file()
    if written and not _floor_is_valid(project_root):
        return {
            "name": "profile_floor",
            "passed": True,
            "level": warn,
            "detail": (
                f"`{rel}` n'a pas de champ `floor:` valide ({', '.join(PROFILE_LADDER)}) : "
                f"« {floor} » s'applique par défaut (fail-closed). Corrige le fichier."
            ),
        }
    if profile_below_floor(declared, floor):
        return {
            "name": "profile_floor",
            "passed": True,
            "level": warn,
            "detail": (
                f"profil déclaré « {declared} » sous le plancher « {floor} » : le plancher s'applique "
                "(`policy.floor_applied` tracé). Relève `profile:` ou retire le plancher en connaissance de cause."
            ),
        }
    return {"name": "profile_floor", "passed": True, "detail": f"Plancher de profil « {floor} » committé, respecté."}


def _floor_is_valid(project_root: Path) -> bool:
    from grimoire.core.standard_generation import PROFILE_FLOOR_FILE, PROFILE_LADDER
    from grimoire.core.standard_state import _load_mapping

    return _load_mapping(project_root.resolve() / PROFILE_FLOOR_FILE).get("floor") in PROFILE_LADDER
