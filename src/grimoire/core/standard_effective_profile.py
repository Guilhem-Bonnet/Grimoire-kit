"""Le profil du manifeste, plancher committé compris (W2-07-S1-04).

Séparé de ``agentic_standard`` : ce module-là est plafonné par le code ratchet,
et ce calcul n'a besoin ni de ses générateurs ni de ses vérificateurs.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
from grimoire.core.standard_profile_manifest import read_profile
from grimoire.core.standard_state import active_profile_id, profile_floor_id


def effective_manifest_profile(project_root: Path) -> str | None:
    """Le profil que les hooks appliquent, plancher committé compris.

    ``None`` seulement quand il n'y a ni manifeste ni plancher. Sert à choisir
    le profil à vérifier ou à tracer quand aucun n'est donné ; l'enrôlement
    (``activation``) et ``up`` gardent la lecture brute du manifeste.
    """
    root = project_root.resolve()
    if read_profile(root / STANDARD_PROFILE_FILE) is None and not profile_floor_id(root):
        return None
    return active_profile_id(root, write_cache=False)
