"""Cibles du cockpit : slug de requête, racine enrôlée, corps.

Sorti de ``cmd_cockpit`` pour ne pas en gonfler le module (cliquet de taille) :
les fonctions ici sont pures — elles prennent le chemin de requête ou le slug
déjà extrait — et toute la politique de résolution multi-projets vit à un seul
endroit, avec sa documentation. Les écritures (lots 1 et 2) et les lectures
(lot 4) résolvent leur projet ici.

Depuis le lot 1 de l'écriture multi-projets (2026-11-07), une écriture vise
n'importe quel projet ENRÔLÉ : le slug de ``?project=`` est **obligatoire** et
sert de clé à :func:`registered_root`, jamais de chemin ; le dossier doit
exister. Aucun repli sur ``selected_slug()`` (état partagé entre onglets).
Depuis le lot 5 (2026-11-12), tout projet enrôlé se pilote comme le projet
d'ouverture : plus de confirmation nommée. Le journal de la garde
(``write-guard.jsonl``) continue de noter le projet visé de chaque écriture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from grimoire.tools.project_registry import registered_root, selected_slug
from grimoire.tools.workspace_routes import PREFIX

#: Écritures d'AUTORITÉ (lot 3, 2026-11-08) : le mandat, le pilote automatique
#: et les fournisseurs. Comme toute écriture de la vue de travail, elles visent
#: la racine ENRÔLÉE résolue depuis ``?project=``. Elles nomment en plus leur
#: projet dans le corps : un slug d'URL inconnu est un 404 et un slug du corps
#: qui ne désigne pas la même racine est un 400 — plutôt que le refus générique
#: « lecture seule » (403) des autres écritures. Absent du corps, le slug ne dit
#: rien (compatibilité des appels historiques).
AUTHORITY_WRITES = frozenset(
    {
        f"{PREFIX}autonomy/autopilot",
        f"{PREFIX}autonomy/mandate/grant",
        f"{PREFIX}autonomy/mandate/revoke",
        f"{PREFIX}providers",
        f"{PREFIX}providers/set-key",
    }
)


def is_authority_write(path: str) -> bool:
    """Vrai pour une écriture qui nomme son projet dans le corps (mandat, pilote, fournisseurs)."""
    return path in AUTHORITY_WRITES


def write_refusal_status(path: str) -> tuple[int, str]:
    """``(code, message)`` quand la cible d'une écriture ne résout pas.

    Un slug absent de l'URL garde le refus historique — 403 « lecture seule »,
    ou 400 « projet non nommé » pour une écriture d'autorité, qui doit nommer sa
    cible. Un slug présent mais inconnu du registre (ou dont le dossier a
    disparu) est un 404 : la cible n'existe pas, ce n'est pas une permission
    manquante.
    """
    if not explicit_query_slug(path):
        if is_authority_write(urlparse(path).path):
            return 400, "projet non nommé"
        return 403, "hôte en lecture seule"
    return 404, "projet inconnu ou non enrôlé"


def body_mismatch_status(path: str) -> int:
    """Code de refus quand le ``project`` du corps contredit ``?project=``.

    Le corps nomme une cible qui diffère de l'URL : requête mal formée, donc
    400 pour toute écriture (lot 5, 2026-11-12).
    """
    return 400


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


def read_root(path: str) -> Path | None:
    """Racine ENRÔLÉE visée par une LECTURE, ou ``None`` (404).

    Lot 4 (2026-11-08) : une lecture nomme son projet comme une écriture. Le
    slug de ``?project=`` sert de clé à ``registered_root`` — jamais de chemin —
    et un slug inconnu ne mène à aucune racine, au lieu de retomber en silence
    sur un autre projet. Deux onglets peuvent donc lire deux projets différents
    sans partager d'état.

    Sans paramètre, le repli historique reste en place : la sélection courante
    (``selected_slug``, elle-même retombant sur le projet primaire), pour que
    les liens ouverts avant le lot 4 continuent de marcher.

    ``project=*`` (Flotte en direct, issue #1193) n'est pas un slug : c'est la
    demande de TOUS les projets enrôlés. La route n'en a pas besoin pour
    servir — elle lit le registre elle-même — mais l'hôte doit lui donner une
    racine pour passer le contrôle d'existence : la sélection courante, comme
    une lecture sans paramètre. Les autres lectures refusent toujours ``*`` :
    une étoile ne désigne pas de projet qu'on pourrait lire fichier par fichier.
    """
    slug = explicit_query_slug(path)
    if slug == "*" and urlparse(path).path == f"{PREFIX}fleet/live":
        return registered_root(slug=selected_slug())
    return registered_root(slug=slug or selected_slug())


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


def unresolvable_status(path: str, opening_slug: str | None) -> dict[str, object] | None:
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
        "home": opening_slug or None,
        "env": "local",
        "demo": False,
    }
