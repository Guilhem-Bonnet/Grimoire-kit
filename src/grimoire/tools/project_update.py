"""Aligner un projet sur le kit installé, depuis l'UI locale.

``grimoire up`` est la commande d'alignement : idempotente, elle rapporte
« skipped » pour ce qui est déjà en place. Ce module l'expose aux hôtes locaux
sans la réimplémenter — le jour où la commande change, l'UI suit.

Deux règles, parce que cette action écrit dans le dépôt de quelqu'un :

* **l'aperçu d'abord** — ``dry_run=True`` par défaut, pour que l'UI puisse
  montrer ce qui changerait avant que qui que ce soit décide ;
* **le sous-processus, pas l'import** — la commande touche au système de
  fichiers, écrit sur des consoles Rich et peut sortir en erreur. L'isoler
  évite qu'un ``typer.Exit`` remonte dans le serveur et le tue.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

#: Un alignement complet lance init, identité, standard et doctor.
UPDATE_TIMEOUT_S = 600

#: Un verrou par projet. Le serveur est multi-thread et le bouton est
#: cliquable : deux ``grimoire up`` concurrents écriraient les mêmes fichiers
#: en même temps. La commande est idempotente, pas réentrante.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def update_project(project_root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Exécute ``grimoire up`` sur le projet et rend son compte rendu.

    Ne lève pas sur échec de la commande : un projet qui refuse de s'aligner
    est un résultat à afficher, pas une panne du serveur.
    """
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        msg = f"pas un dossier : {root}"
        raise FileNotFoundError(msg)

    lock = _lock_for(str(root))
    if not lock.acquire(blocking=False):
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": "une mise à jour de ce projet est déjà en cours", "output": "",
        }
    try:
        return _run_up(root, dry_run=dry_run)
    finally:
        lock.release()


def _run_up(root: Path, *, dry_run: bool) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "grimoire", "up", str(root)]
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=UPDATE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": f"délai dépassé ({UPDATE_TIMEOUT_S} s)", "output": "",
        }
    except OSError as exc:
        # Le processus n'a même pas démarré (interpréteur absent, descripteurs
        # épuisés…). C'est un échec à rapporter comme un autre : le laisser
        # remonter donnerait un 500 sans explication à une UI qui attend un
        # compte rendu.
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": f"lancement impossible : {exc}", "output": "",
        }
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0,
        "dryRun": dry_run,
        "path": str(root),
        "code": result.returncode,
        # Le compte rendu de ``up`` tient en quelques lignes ; on borne quand
        # même, une UI n'a pas à recevoir un journal entier.
        "output": output.strip()[-8000:],
        "error": None if result.returncode == 0 else "grimoire up a échoué",
    }
