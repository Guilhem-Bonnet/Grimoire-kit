"""Mode strict du harnais e2e : en CI, un skip est un échec (D-10).

Le harnais e2e se skippe quand Playwright, Chromium ou ``grimoire init`` manquent
(``conftest.py``). Sur un poste, c'est un confort. En CI, c'est un faux vert : le
job ``e2e`` installe Playwright et Chromium, donc tout skip y signale que la
suite n'a pas tourné là où elle devait. ``GRIMOIRE_E2E_STRICT=1`` (posé par le
job) convertit chaque skip d'exécution en échec, avec la raison d'origine.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

ENV_VAR = "GRIMOIRE_E2E_STRICT"


def strict_mode(env: Mapping[str, str] | None = None) -> bool:
    """Vrai si la variable d'environnement vaut 1, true ou yes (insensible à la casse)."""
    source = os.environ if env is None else env
    return source.get(ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def promote_skip(report: Any, env: Mapping[str, str] | None = None) -> bool:
    """Transforme un rapport skippé en échec en mode strict ; renvoie True si modifié."""
    if not report.skipped or not strict_mode(env):
        return False
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr)
    report.outcome = "failed"
    report.longrepr = f"skip interdit en mode strict ({ENV_VAR}=1) : {reason}"
    return True
