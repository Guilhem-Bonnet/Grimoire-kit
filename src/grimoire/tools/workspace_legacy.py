"""Le pas 2 du basculement (ADR-006, « Basculement ») — une table, deux hôtes.

Les cinq lots de la vue de travail sont mergés : ``grimoire serve`` et
``grimoire cockpit serve`` ouvrent maintenant ``workspace/index.html`` par
défaut. Les 14 pages historiques restent servies telles quelles — l'ADR ne
supprime rien avant « une version après » (pas 3) — mais les dix qui sont des
*outils* (par opposition aux quatre pages vitrine, hors périmètre) redirigent
désormais vers l'espace de la coque qui les remplace, avec une sortie de
secours explicite : ``?legacy=1`` sur l'ancienne URL sert encore l'ancienne
page, sans redirection, pour quiconque n'est pas prêt à basculer.

Une seule table, importée par les deux serveurs (``forge_http.py`` pour
l'atelier, ``cmd_cockpit.py`` pour le cockpit) : la faire vivre à deux copies
finirait par diverger, exactement le défaut que D5 de l'ADR a fermé pour les
lectures ``/api/workspace/``.

La cible de chaque page vient de la spec (§4, « Ce qu'il remplace ») pour les
cinq qui y sont nommées. ``documentation.html`` et ``labs.html`` n'y figurent
pas — ni l'ADR ni la spec ne leur donnent d'espace : ``documentation.html``
pointe vers le manuel du kit (fichiers, référence) donc Source ; ``labs.html``
gouverne des capacités candidates par projet, donc Concevoir, aux côtés des
blueprints, patterns et extensions qu'il a déjà absorbés.
"""

from __future__ import annotations

__all__ = ["LEGACY_PAGES", "legacy_redirect_target"]

#: Page héritée (nom de fichier, sans slash) → espace de la coque qui la
#: remplace. Les quatre pages vitrine (`index.html`, `demo.html`,
#: `anatomy.html`, `game-ui.html`) sont hors périmètre (spec §7) et n'entrent
#: jamais dans cette table.
LEGACY_PAGES: dict[str, str] = {
    # La racine de l'application servie ouvre la coque ; `index.html` demandé
    # explicitement reste la page vitrine.
    "": "piloter",
    "atelier.html": "piloter",
    "portfolio.html": "piloter",
    "kanban.html": "executer",
    "observability.html": "observer",
    "memory.html": "memoire",
    "blueprints.html": "concevoir",
    "patterns.html": "concevoir",
    "extensions.html": "concevoir",
    "labs.html": "concevoir",
    "documentation.html": "source",
}


def legacy_redirect_target(rel_path: str) -> str | None:
    """L'espace vers lequel rediriger, ou ``None`` si ``rel_path`` n'est pas
    une page héritée que le basculement concerne (toute autre page statique,
    y compris les quatre pages vitrine, rend ``None``)."""
    return LEGACY_PAGES.get(rel_path)
