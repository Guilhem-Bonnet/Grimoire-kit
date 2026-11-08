"""Cibles d'écriture du cockpit : slug de requête, racine enrôlée, corps.

Sorti de ``cmd_cockpit`` pour ne pas en gonfler le module (cliquet de taille) :
les fonctions ici sont pures — elles prennent le chemin de requête ou le slug
déjà extrait — et toute la politique d'écriture multi-projets vit à un seul
endroit, avec sa documentation.

Depuis le lot 1 de l'écriture multi-projets (2026-11-07), une écriture vise
n'importe quel projet ENRÔLÉ : le slug de ``?project=`` est **obligatoire** et
sert de clé à :func:`registered_root`, jamais de chemin ; le dossier doit
exister. Aucun repli sur ``selected_slug()`` (état partagé entre onglets).
``file/write``, ``file/override`` et ``command`` restent réservés au projet
d'ouverture (lot 2), comme les mutations mémoire.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from grimoire.tools.project_registry import registered_root, selected_slug


def query_slug(path: str) -> str:
    """Projet visé par la requête : ``?project=`` explicite, sinon la sélection.

    Repli réservé aux LECTURES. Une écriture ne l'utilise jamais : la sélection
    est un état global partagé entre onglets, elle ne peut pas désigner la
    cible d'une écriture (voir :func:`explicit_query_slug`).
    """
    return explicit_query_slug(path) or selected_slug()


def explicit_query_slug(path: str) -> str:
    """Slug donné explicitement dans ``?project=`` — chaîne vide s'il manque.

    Aucun repli sur ``selected_slug()`` : deux onglets partagent cet état, si
    bien qu'une écriture sans slug explicite pourrait atterrir dans un projet
    que l'utilisateur ne regarde pas.
    """
    return parse_qs(urlparse(path).query).get("project", [""])[0].strip()


def enrolled_write_root(path: str) -> Path | None:
    """Racine ENRÔLÉE visée par une écriture, ou ``None`` (refus).

    Le slug de requête n'est jamais un chemin : il sert de clé de recherche à
    ``registered_root``, qui rend le chemin enregistré — et seulement si le
    dossier existe encore. C'est la seule résolution acceptée pour une écriture :
    un chemin fourni par le client, un ``..`` ou un slug inconnu ne mènent à
    aucune racine.
    """
    slug = explicit_query_slug(path)
    return registered_root(slug=slug) if slug else None


def home_write_root(path: str, home_slug: str | None) -> Path | None:
    """Racine du projet d'OUVERTURE pour les écritures qui y restent limitées.

    Lot 2 : ``file/write``, ``command``, ``file/override`` et les mutations
    mémoire gardent la règle historique. Le slug explicite doit être celui du
    lancement, et il doit toujours résoudre dans le registre.
    """
    slug = explicit_query_slug(path)
    if not home_slug or slug != home_slug:
        return None
    return registered_root(slug=slug)


def body_confirms_slug(body: Any, path: str) -> bool:
    """Le ``project`` du corps, s'il est présent, doit viser le même projet.

    Même contrat que les routes ``portfolio`` : ``?project=`` résout la racine,
    un ``project`` de corps contradictoire est refusé plutôt que silencieusement
    ignoré (un client qui se trompe de slug écrirait ailleurs que là où il
    croit). L'absence de ``project`` reste permise — les appels de la vue de
    travail le portent rarement.
    """
    if not isinstance(body, dict):
        return True
    wanted = str(body.get("project") or "").strip()
    return not wanted or wanted == explicit_query_slug(path)


def unresolvable_status(path: str, home_slug: str | None) -> dict[str, object] | None:
    """Statut minimal en lecture seule si la cible ne résout pas, sinon ``None``.

    Une cible non résoluble (absente du registre, dossier disparu) garde une
    réponse de statut — en lecture seule — plutôt qu'un 404 : c'est exactement
    ce que ``readOnly`` veut dire depuis le lot 1, et l'UI a besoin de le lire
    pour afficher son bandeau plutôt que de croire l'API injoignable.
    """
    slug = query_slug(path)
    if slug and registered_root(slug=slug) is not None:
        return None
    return {
        "host": "cockpit",
        "readOnly": True,
        "project": slug,
        "slug": slug or None,
        "home": home_slug or None,
        "env": "local",
        "demo": False,
    }
