"""Empreinte du diff courant, pour le signal « tourne en rond » du pilote (lot 4.4)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_CHECK_TIMEOUT_S = 600.0

#: Ce que la cascade écrit elle-même (ledger, état fournisseurs, sorties) n'est pas du travail
#: de l'ouvrier : il changerait l'empreinte à chaque tentative et masquerait la répétition.
_FINGERPRINT_EXCLUDES = (":(exclude)_grimoire-runtime-output", ":(exclude)_grimoire-output")


def diff_fingerprint(project_root: Path) -> str | None:
    """Empreinte du diff courant (suivi + fichiers non suivis), ``None`` hors dépôt git.

    Sert au signal « tourne en rond » du pilote (lot 4.4) : deux tentatives
    qui laissent exactement le même diff n'ont rien appris l'une à l'autre.
    """
    try:
        tracked = subprocess.run(
            ["git", "diff", "HEAD", "--", ".", *_FINGERPRINT_EXCLUDES], cwd=project_root, capture_output=True, encoding="utf-8", errors="replace", timeout=_CHECK_TIMEOUT_S
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", ".", *_FINGERPRINT_EXCLUDES], cwd=project_root, capture_output=True, encoding="utf-8", errors="replace", timeout=_CHECK_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if tracked.returncode != 0 or untracked.returncode != 0:
        return None
    digest = hashlib.sha256(tracked.stdout.encode("utf-8"))
    for name in sorted(line for line in untracked.stdout.splitlines() if line.strip()):
        digest.update(b"\0" + name.encode("utf-8") + b"\0")
        try:
            digest.update((project_root / name).read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def signal_if_stuck(service, task_id: str, actor: str, root: Path, previous: str | None, *, attempt: int, tier: str, next_tier: str | None) -> str | None:  # type: ignore[no-untyped-def]
    """Après une tentative rouge : écrit ``pilot.signal=stuck`` si le diff est celui de la précédente.

    Le palier change quoi qu'il arrive après un rouge ; le signal dit pourquoi, et vers où.
    Retourne l'empreinte courante, à repasser à l'appel suivant.
    """
    fingerprint = diff_fingerprint(root)
    if fingerprint is not None and fingerprint == previous:
        service.ledger.append_event(
            "pilot.signal", task_id, "task", actor,
            {
                "signal": "stuck",
                "reason": "même diff à deux tentatives rouges consécutives",
                "attempt": attempt,
                "from_tier": tier,
                "to_tier": next_tier,
                "diff_fingerprint": fingerprint,
            },
        )
    return fingerprint
