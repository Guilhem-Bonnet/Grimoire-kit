"""Couche de données de ``grimoire serve`` — la vérité du projet, jamais la démo.

Le site embarqué dans la wheel porte deux natures de JSON sous ``web/data/`` :

* des **références du kit** (catalogue de patterns, marketplace d'extensions,
  anatomie, couverture) — identiques pour tout le monde, servies telles quelles ;
* des **couches de télémétrie projet** (``meta``, ``taskboard``,
  ``observatory``, ``activity``, ``insights``, ``memory``) — qui, dans la wheel,
  sont l'instantané de la vitrine publique : des projets inventés
  (« Atlas Ops », « Sentinel Sec »…) et un store mémoire à 141 entrées.

Servir le second groupe en local, c'est montrer les chiffres d'un autre projet
sous le nom de celui qu'on ouvre. Ce module coupe court : les couches projet ne
viennent QUE d'une génération faite sur le projet servi, et n'existent pas tant
qu'elle n'a pas tourné. Mieux vaut une page vide qu'une page fausse.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.data import site_script
from grimoire.tools.project_registry import registry_home, slug_for_path, slugify

# Couches produites par ``gen-site-data.py`` pour un projet donné. Toute
# ressource de cette liste est propre au projet servi : elle ne peut pas venir
# du site embarqué. Le test ``test_project_layers_match_the_generator`` garde
# cette liste alignée sur le générateur.
PROJECT_LAYERS = frozenset(
    {
        "meta.json",
        "taskboard.json",
        "observatory.json",
        "activity.json",
        "insights.json",
        "memory.json",
        "health.json",
    }
)

# Index de projets : en local il décrit le registre réel de la machine, jamais
# la galerie de démonstration de la vitrine.
REGISTRY_LAYER = "projects.json"

# Délai au-delà duquel une génération est considérée comme perdue (le
# sous-processus a été tué, la machine a dormi…) et peut être relancée.
GENERATION_TIMEOUT_S = 300


def project_slug(project_root: Path) -> str:
    """Clé de cache du projet : son slug au registre, sinon nom + empreinte.

    Le registre garantit l'unicité de ses slugs ; un projet qu'il ne connaît pas
    n'a que son nom de dossier, et deux dépôts peuvent parfaitement s'appeler
    ``web``. Sans l'empreinte du chemin, ils partageraient leur cache — et le
    second afficherait les chiffres du premier, exactement le défaut que ce
    module existe pour fermer.
    """
    registered = slug_for_path(project_root)
    if registered:
        return registered
    digest = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()[:8]
    return f"{slugify(project_root.name)}-{digest}"


def data_dir(project_root: Path) -> Path:
    """Dossier où vit la couche de données générée pour ce projet.

    Sous ``atelier/``, pas sous ``serve/`` : ce dernier est la racine web que
    ``grimoire cockpit serve`` publie telle quelle. Y ranger le cache de
    l'atelier exposerait la couche de chaque projet à ``/<slug>/data/…``, et un
    projet dont le slug est ``data`` écrirait dans le dossier de données du
    cockpit lui-même.
    """
    return registry_home() / "atelier" / project_slug(project_root) / "data"


def is_project_layer(rel: str) -> bool:
    """Vrai si la ressource ``data/<rel>`` est propre à un projet."""
    rel = rel.lstrip("/")
    return rel in PROJECT_LAYERS or rel == REGISTRY_LAYER or rel.startswith("projects/")


def resolve(rel: str, project_root: Path, bundled_data: Path | None) -> Path | None:
    """Chemin réel à servir pour ``data/<rel>``, ou ``None`` s'il n'y en a pas.

    Les couches projet viennent exclusivement du dossier généré ; les
    références du kit, exclusivement du site embarqué. Aucun repli croisé : un
    repli est précisément ce qui faisait passer la vitrine pour le projet local.
    """
    rel = rel.lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    if is_project_layer(rel):
        base = data_dir(project_root)
    elif bundled_data is not None:
        base = bundled_data
    else:
        return None
    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        return None
    return target if target.is_file() else None


class DataLayer:
    """Génère et suit la couche de données du projet servi.

    Une seule génération à la fois, en arrière-plan : ``grimoire serve`` doit
    répondre immédiatement, et une couche absente est un état affichable — pas
    une raison de faire attendre le navigateur.
    """

    def __init__(self, project_root: Path) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._project_root = project_root.resolve()
        self._state = "idle"
        self._error: str | None = None
        self._started_at: datetime | None = None

    @property
    def project_root(self) -> Path:
        return self._project_root

    def retarget(self, project_root: Path) -> None:
        """Change le projet suivi (sélection d'un autre projet dans l'atelier)."""
        with self._lock:
            self._project_root = project_root.resolve()
            self._state = "idle"
            self._error = None
            self._started_at = None

    def _generated_at(self) -> str | None:
        index = data_dir(self._project_root) / REGISTRY_LAYER
        if not index.is_file():
            return None
        try:
            payload = json.loads(index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        value = payload.get("generated_at")
        return str(value) if value else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            state, error, started = self._state, self._error, self._started_at
        if state == "generating" and started is not None:
            elapsed = (datetime.now(UTC) - started).total_seconds()
            if elapsed > GENERATION_TIMEOUT_S:
                state = "failed"
                error = "génération interrompue (délai dépassé)"
        return {
            "state": state,
            "projectRoot": str(self._project_root),
            "slug": project_slug(self._project_root),
            "dataDir": str(data_dir(self._project_root)),
            "generatedAt": self._generated_at(),
            "error": error,
        }

    def refresh(self) -> dict[str, Any]:
        """Lance une génération en arrière-plan si aucune n'est en cours."""
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running:
                return {"started": False, "reason": "génération déjà en cours"}
            self._state = "generating"
            self._error = None
            self._started_at = datetime.now(UTC)
            root = self._project_root
            thread = threading.Thread(
                target=self._generate, args=(root,), name="grimoire-serve-data", daemon=True
            )
            self._thread = thread
        thread.start()
        return {"started": True}

    def _generate(self, root: Path) -> None:
        state, error = "ready", None
        try:
            self.generate_sync(root)
        except FileNotFoundError as exc:
            state, error = "failed", f"générateur introuvable : {exc}"
        except subprocess.SubprocessError as exc:
            state, error = "failed", f"génération échouée : {exc}"
        except OSError as exc:
            state, error = "failed", f"écriture impossible : {exc}"
        with self._lock:
            # Une sélection de projet pendant la génération périme le résultat :
            # on ne réécrit l'état que s'il porte encore sur la même racine.
            if self._project_root == root:
                self._state = state
                self._error = error

    @staticmethod
    def generate_sync(root: Path) -> Path:
        """Génère la couche du projet et renvoie son dossier. Bloquant."""
        out = data_dir(root)
        out.mkdir(parents=True, exist_ok=True)
        script = site_script("gen-site-data.py")
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(root),
                "--out-dir",
                str(out),
                "--name",
                root.name,
            ],
            capture_output=True,
            text=True,
            timeout=GENERATION_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            msg = detail[-1] if detail else f"code {result.returncode}"
            raise subprocess.SubprocessError(msg)
        return out
