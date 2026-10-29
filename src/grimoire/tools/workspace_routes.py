"""Le dispatch de ``/api/workspace/…`` — une table, deux hôtes, une cible.

La vue de travail est la même coque pour ``grimoire serve`` (un projet) et
``grimoire cockpit serve`` (une flotte). Ce module est le point où cette
promesse devient vérifiable : les lectures sont une table pure
``(méthode, chemin) → fonction(project_root, …)``, sans état ni notion d'hôte.
L'atelier l'appelle avec sa racine unique, le cockpit avec celle qu'il a
résolue depuis ``?project=`` — et le test
``tests/unit/test_workspace_routes.py`` prouve que chaque route honore la
cible qu'on lui donne.

Les écritures sont ailleurs dans le même fichier, mais derrière une porte
différente : :func:`workspace_post` reste agnostique de l'hôte appelant (il
exécute pour le ``project_root`` qu'on lui donne, un point c'est tout), mais
qui a le droit de l'appeler ne l'est pas. L'atelier le câble sans condition.
Le cockpit (``cmd_cockpit.py::_CockpitHandler.do_POST``) ne le câble que pour
le projet qu'il sert en direct — ``_HOME_SLUG``, résolu une fois au lancement,
jamais pour un autre projet du registre qu'on ne fait que regarder (#351/#356).
Lui donner de quoi réclamer une tâche ou créer un override dans un dépôt qu'il
ne sert pas serait une régression de gouvernance, pas une commodité.

Une exception nommée à cette règle (issue #490) : décider une proposition
(``proposals/<slug>/accept|reject``, voir :func:`is_proposal_decision`). La
doctrine du déclencheur (:mod:`grimoire.proposals`) est « proposée puis
validée, jamais automatique » — le cockpit est précisément l'endroit où
l'humain qui pilote une flotte valide, y compris pour un projet qu'il ne fait
que regarder via le registre. C'est la même porte que
``POST /api/projects/update`` (le bouton « Mettre à jour ») ouvre déjà pour
n'importe quel projet du registre : une décision explicite sur un artefact
proposé n'est pas une mutation plus dangereuse qu'un ``grimoire up`` distant.

Deuxième exception nommée, même précédent (issue #559 suite, #560) : migrer
les tâches d'un board du standard vers le Mission Ledger
(``tasks/migrate-standard``, voir :func:`is_registry_scoped_write`). Le
cockpit pilote une flotte — jongler entre plusieurs projets déjà enrôlés — et
cette migration est le même genre de geste explicite et ponctuel que mettre à
jour ou décider une proposition, jamais une écriture continue comme réclamer
une tâche. Elle suit donc la même route de résolution que
``POST /api/projects/update`` (le registre, jamais un chemin libre fourni par
le client) plutôt que la garde ``_HOME_SLUG``.

Troisième exception nommée (issue #638, lot C) : agir sur une tâche depuis le
**portefeuille** (``portfolio/tasks/<id>/<action>``, voir
:func:`is_registry_scoped_write`). L'orchestrateur humain pilote depuis le
cockpit les chantiers de tous les projets du registre — prioriser, bloquer,
annuler une tâche d'un projet qu'il ne fait que regarder est précisément ce
que le portefeuille existe pour permettre. L'action est routée vers le projet
propriétaire (``?project=<slug>``, résolu par le registre comme
``POST /api/projects/update``) et passe par le ``TaskService`` de CE projet,
gate de preuve compris ; un ``project`` du corps qui ne confirme pas la
racine résolue est refusé. Le chemin historique ``tasks/<id>/<action>`` reste
``_HOME_SLUG`` seulement.

Le reste de :data:`POST_ROUTES` — écrire un fichier, réclamer une tâche
par le chemin historique, créer un override — reste strictement
``_HOME_SLUG`` : chaque dérogation est nommée, jamais générale.

Le flux du pouls (``GET /api/workspace/stream``, P10) n'est pas une entrée de
:data:`GET_ROUTES` : celles-ci rendent une charge utile, lui un flux sans fin.
:func:`workspace_stream` rend le générateur, l'hôte le recopie sur la socket
avec :data:`STREAM_HEADERS`.

Ajouter une lecture : une entrée dans :data:`GET_ROUTES`. Ajouter une écriture :
une entrée dans :data:`POST_ROUTES`. Les deux tables sont énumérées par les
tests, donc une route ajoutée sans test de cible se voit.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable, Generator, Mapping
from pathlib import Path
from typing import Any

from grimoire.missions.liveness import probe_entries
from grimoire.tools import (
    source_assist,
    workspace_api,
    workspace_attention,
    workspace_chat,
    workspace_config,
    workspace_exec,
    workspace_hygiene,
    workspace_knowledge,
    workspace_language,
    workspace_machine,
    workspace_memory,
    workspace_portfolio,
    workspace_pulse,
    workspace_recall,
    workspace_reviews,
    workspace_sessions,
    workspace_setup_guide,
    workspace_since,
)

__all__ = [
    "CHAT_STREAM_PATH",
    "GET_ROUTES",
    "POST_ROUTES",
    "PREFIX",
    "STREAM_HEADERS",
    "STREAM_PATH",
    "WORKSPACE_UNHANDLED",
    "is_proposal_decision",
    "is_registry_scoped_write",
    "workspace_get",
    "workspace_post",
    "workspace_stream",
]

#: Tout ce que la vue de travail ajoute vit sous ce préfixe : aucune collision
#: possible avec les routes héritées, et un seul `startswith` à câbler par hôte.
PREFIX = "/api/workspace/"

#: Sentinelle, même contrat que ``forge_routes.API_GET_UNHANDLED`` : distingue
#: « chemin inconnu » d'une route qui rendrait légitimement ``None``.
WORKSPACE_UNHANDLED = object()

_Query = dict[str, list[str]]
_GetHandler = Callable[[Path, _Query], Any]
_PostHandler = Callable[[Path, dict[str, Any]], Any]


def _one(query: _Query, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _translate(exc: Exception) -> Exception:
    """Traduit une erreur du domaine en erreur que le transport sait coder.

    Les hôtes n'attrapent que ``FileNotFoundError`` (404), ``PermissionError``
    (403) et ``ValueError`` (400). Une :class:`GrimoireError` qui remonterait
    telle quelle deviendrait un 500 avec une trace — donc une panne là où il y
    a un refus explicable. La traduction se fait ici, dans notre couche, plutôt
    qu'en élargissant le ``except`` de deux fichiers hérités.
    """
    from grimoire.core.exceptions import GrimoireError, GrimoireMissionError

    if isinstance(exc, GrimoireMissionError):
        return FileNotFoundError(str(exc))
    if isinstance(exc, GrimoireError):
        return ValueError(str(exc))
    return exc


# ── Lectures ────────────────────────────────────────────────────────────────


def _glossary(project_root: Path, _query: _Query) -> Any:
    return workspace_api.glossary_view(project_root)


#: La sonde d'hôte (``claude agents --json``) lance un sous-processus. Avec
#: l'actualisation automatique de l'interface (UI-3), chaque lecture des tâches
#: d'un projet dont une session bat ferait un lancement : un résultat vaut donc
#: ce délai, pour les deux lectures qui l'utilisent (projet et portefeuille).
_PROBE_TTL_SECONDS = 10.0
_probe_memo: tuple[float, Mapping[str, Mapping[str, Any]] | None] | None = None
_probe_lock = threading.Lock()


def _host_entries() -> Mapping[str, Mapping[str, Any]] | None:
    """Les entrées de ``claude agents --json`` (vue Sessions, P33), mémorisées :data:`_PROBE_TTL_SECONDS`."""
    global _probe_memo
    with _probe_lock:
        now = time.monotonic()
        if _probe_memo is not None and now - _probe_memo[0] < _PROBE_TTL_SECONDS:
            return _probe_memo[1]
        result = probe_entries()
        _probe_memo = (now, result)
        return result


def _host_probe() -> Mapping[str, str] | None:
    """``{session_id: statut}`` : la même sonde mémorisée que :func:`_host_entries`."""
    entries = _host_entries()
    return None if entries is None else {sid: str(e.get("status")) for sid, e in entries.items()}


def _tasks(project_root: Path, query: _Query) -> Any:
    return workspace_api.tasks_view(
        project_root, mission=_one(query, "mission"), status=_one(query, "status"), probe=_host_probe
    )


def _waves(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/waves[?mission=<id>]`` — les vagues du plan et, pour une mission, le checkpoint de chacune (W5-05).

    ``checkpoints`` (statut, empreinte du diff fusionné, tâches couvertes, requis ou non) et
    ``claim_blocked`` (le motif exact qui ferme le claim d'une tâche) sont ceux de ``grimoire task waves``.
    """
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve())
    if not service.has_ledger:
        return {"waves": [], "ledger": False}
    return {**service.waves(_one(query, "mission") or None), "ledger": True}


def _files(project_root: Path, query: _Query) -> Any:
    return workspace_api.files_view(project_root, tier=_one(query, "tier"))


def _file(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_view(project_root, _one(query, "path"))


def _file_diff(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_diff(project_root, _one(query, "path"))


def _upgrade_changes(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/upgrade/changes`` — ce qu'une mise à jour écrirait, par catégorie (P44). Lecture pure."""
    from grimoire.tools import upgrade_changes

    return upgrade_changes.changes_view(project_root)


def _upgrade_diff(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/upgrade/diff?path=`` — le diff d'un fichier que la mise à jour toucherait (P44). Lecture pure."""
    from grimoire.tools import upgrade_changes

    return upgrade_changes.diff_view(project_root, _one(query, "path"))


def _blueprints(project_root: Path, _query: _Query) -> Any:
    return workspace_api.blueprints_view(project_root)


def _agents(project_root: Path, _query: _Query) -> Any:
    return workspace_api.agents_view(project_root)


def _hooks(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/hooks`` — le registre de sûreté des hooks (P66). Lecture pure."""
    return workspace_config.hooks_view(project_root)


def _providers(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/providers`` — les fournisseurs de modèles, état et palier (P66). Lecture pure."""
    return workspace_config.providers_view(project_root)


def _options(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/options`` — les options du projet (P75) : taux USD→CAD et sa provenance."""
    from grimoire.tools.workspace_work import project_options

    return project_options(project_root)


def _options_write(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/options`` — pose ``cockpit.usd_cad`` dans ``project-context.yaml`` (P75).

    Une seule clé acceptée : le taux de conversion du coût affiché par la
    colonne Fait. Un taux illisible ou hors borne est un 400, jamais écrit.
    """
    from grimoire.tools.workspace_work import write_usd_cad

    raw = body.get("usd_cad")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ValueError("`usd_cad` doit être un nombre (CAD pour 1 USD)")
    return write_usd_cad(project_root, float(raw))


def _notify(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/notify`` — la commande de notification effective et sa provenance (I1)."""
    from grimoire.tools.workspace_notify import notify_state

    return notify_state(project_root)


def _notify_write(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/notify`` — pose (ou retire) ``options.notify.command`` du profil du standard (I1)."""
    from grimoire.tools.workspace_notify import write_notify_command

    raw = body.get("command")
    if not isinstance(raw, str):
        raise ValueError("`command` doit être une chaîne (vide pour la retirer)")
    return write_notify_command(project_root, raw)


def _notify_test(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/notify/test`` — lance la commande configurée avec une décision d'essai (I1)."""
    from grimoire.tools.workspace_notify import test_notify_command

    raw = body.get("command")
    return test_notify_command(project_root, raw if isinstance(raw, str) else None)


def _proposals(project_root: Path, _query: _Query) -> Any:
    return workspace_api.proposals_view(project_root)


def _sheet(project_root: Path, _query: _Query) -> Any:
    return workspace_api.sheet_view(project_root)


def _flow_runs(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/flows/runs`` — les runs de flow connus (#506).

    Distinct du TraceLedger (dispatch/agent-miss) qu'Observer lit par
    ailleurs : un ``grimoire upgrade-flow run`` n'y écrit jamais de span, et
    Observer affichait « TraceLedger vide » même juste après un run réel.
    ``blueprint`` filtre (ex. ``project-upgrade``) ; omis, tous les flows.
    """
    from grimoire.tools.flow_runs import flow_runs_view

    return flow_runs_view(project_root, blueprint_id=_one(query, "blueprint"))


def _file_usage(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_usage(project_root, _one(query, "path"))


def _file_history(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_history(project_root, _one(query, "path"))


def _commands(_project_root: Path, _query: _Query) -> Any:
    return {"commands": workspace_exec.catalogue(), "count": len(workspace_exec.ALLOWED)}


def _doctor(project_root: Path, query: _Query) -> Any:
    probe = _one(query, "probe") not in (None, "", "0", "false")
    return workspace_exec.doctor_view(project_root, probe=probe)


def _memory_overview(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/overview`` — agrégation multi-projets (#172).

    ``projects=all`` agrège tout le registre de la machine, ``slug1,slug2``
    un sous-ensemble ; omis, la réponse ne porte que le projet déjà servi —
    jamais toute la flotte par défaut.
    """
    return workspace_memory.memory_overview(project_root, _one(query, "projects"))


def _portfolio_tasks(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/portfolio/tasks`` — le board agrégé du registre (#638).

    Toutes les tâches de tous les projets enregistrés au cockpit, chacune
    avec son projet ; filtres ``state`` (état ledger ou colonne board),
    ``slug`` (un projet — ``?project=`` étant déjà, sur le cockpit, le projet
    servi par l'hôte, pas un filtre), ``live`` (session dont le journal a
    moins de ``minutes`` minutes, défaut
    :data:`workspace_portfolio.DEFAULT_LIVE_MINUTES`). Les chemins viennent du
    registre, jamais de la requête.
    """
    live_raw = _one(query, "live")
    minutes_raw = _one(query, "minutes")
    return workspace_portfolio.portfolio_tasks(
        project_root,
        state=_one(query, "state") or None,
        project=_one(query, "slug") or None,
        live=live_raw not in (None, "", "0", "false"),
        live_minutes=int(minutes_raw) if minutes_raw else workspace_portfolio.DEFAULT_LIVE_MINUTES,
        probe=_host_probe,
    )


def _sessions(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/sessions[?scope=portfolio]`` — la vue Sessions de Travail (P33). Lecture pure.

    Le projet servi seul, ou tout le registre (``scope=portfolio``) : état de
    chaque session (active, attend ta réponse, silencieuse, terminée), tâches
    tenues, dernier signe, consignes en attente, activité des quinze dernières
    minutes et commande de reprise à copier.
    """
    return workspace_sessions.sessions_view(
        project_root, portfolio=_one(query, "scope") == "portfolio", probe=_host_entries
    )


def _pulse(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/pulse`` — « quelque chose a-t-il changé ? » (UI-3).

    ``since`` est l'empreinte que l'interface connaît : identique, la réponse
    ne relit rien et dit ``changed: false`` ; différente, elle porte le compte
    des décisions en attente. ``scope=portfolio`` agrège le registre du
    cockpit (le compteur de la barre du haut), sinon le projet servi seul.
    ``interval_seconds`` est l'intervalle de sondage que l'interface doit
    suivre (:data:`workspace_pulse.INTERVAL_ENV`).
    """
    return workspace_pulse.pulse_view(
        project_root,
        since=_one(query, "since") or None,
        portfolio=_one(query, "scope") == "portfolio",
    )


def _attention(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/attention`` — ce qui attend une décision humaine (P22).

    Le projet servi seul ; ``scope=portfolio`` tout le registre, ``slug=`` un
    projet du registre. ``probe=1`` relance la sonde mémoire (« Réessayer »)
    au lieu de lire son cache. Sections par urgence :
    :func:`workspace_attention.attention_order`.
    """
    return workspace_attention.attention_view(
        project_root,
        portfolio=_one(query, "scope") == "portfolio",
        slug=_one(query, "slug") or None,
        probe=_one(query, "probe") not in (None, "", "0", "false"),
        host_probe=_host_probe,
    )


def _attention_since(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/attention/since`` — « depuis ta dernière visite » (P53).

    ``since`` est l'instant de la visite, gardé par le navigateur : la route
    lit, elle ne retient rien. Mêmes portées que ``attention``.
    """
    return workspace_since.since_view(
        project_root,
        since=_one(query, "since") or None,
        portfolio=_one(query, "scope") == "portfolio",
        slug=_one(query, "slug") or None,
    )


def _reviews(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/reviews`` — les PR ouvertes à revoir, les plus prêtes d'abord (P48).

    Même portée que ``attention`` (``scope=portfolio``, ``slug=``). Lecture
    seule : une lecture ``gh pr list`` bornée par dépôt, partagée avec la
    route d'attention ; jamais de fusion ni de commentaire depuis le cockpit.
    """
    return workspace_reviews.reviews_view(
        project_root,
        portfolio=_one(query, "scope") == "portfolio",
        slug=_one(query, "slug") or None,
    )


def _evidence(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/evidence`` — le panneau « Preuves » du rail (#534).

    Lecture pure du board du standard (``check_evidence_gates`` par tâche) :
    jamais d'écriture, jamais une relecture parallèle des artefacts que le
    standard sait déjà lire lui-même.
    """
    return workspace_api.evidence_view(project_root)


def _memory_search(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/search`` — recherche croisée en lecture
    seule (#172), même chaîne que ``grimoire memory search`` par projet
    sélectionné, jamais de fusion des stores."""
    limit_raw = _one(query, "limit")
    limit = int(limit_raw) if limit_raw else 10
    return workspace_memory.memory_search(
        project_root, _one(query, "q"), _one(query, "projects"), limit=limit,
        include_superseded=_one(query, "include_superseded") == "1",
        include_stale=_one(query, "include_stale") == "1",
    )


def _memory_entries(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/entries`` — une page d'entrées d'un projet (P24).

    ``slug`` (registre) ou le projet servi ; ``kind`` souvenir/projection,
    ``types``, ``since``, ``offset`` (curseur), ``limit``. Jamais tout le store.
    """
    return workspace_knowledge.memory_entries(
        project_root, slug=_one(query, "slug") or None, kind=_one(query, "kind"),
        types=_one(query, "types"), since=_one(query, "since"),
        offset=_one(query, "offset") or 0, limit=_one(query, "limit") or workspace_knowledge.DEFAULT_PAGE,
        include_superseded=_one(query, "include_superseded") == "1",
    )


def _memory_entry(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/entry`` — provenance, remplaçant et nœuds liés d'une entrée (P24)."""
    return workspace_knowledge.memory_entry(project_root, _one(query, "id"), slug=_one(query, "slug") or None)


def _memory_graph(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/graph`` — le graphe complet, en colonnes compactes (P81)."""
    from grimoire.tools import workspace_graph

    return workspace_graph.graph_view(project_root)


def _memory_quality(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/quality`` — santé de la mémoire d'un projet et sa file d'hygiène (P25).

    ``slug`` (registre) ou le projet servi ; lecture seule, mesure fraîche.
    """
    return workspace_hygiene.memory_quality(project_root, slug=_one(query, "slug") or None)


def _data_machine(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/data/machine`` — les services mémoire du poste et qui les partage (P26).

    Lecture seule, mesure fraîche : ``docker ps``/``docker inspect``, connexions
    TCP et ``GET`` de version bornés ; rien ne démarre, aucun secret ne sort.
    """
    return workspace_machine.machine_view(project_root)


def _data_actions(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/data/actions`` — le catalogue des actions typées (ADR-008), sans rien exécuter."""
    from grimoire.tools import workspace_actions

    return workspace_actions.actions_catalogue(project_root)


def _data_actions_journal(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/data/actions/journal`` — le journal ajout seul des actions, le plus récent d'abord."""
    from grimoire.tools import workspace_actions

    raw = _one(query, "limit") or ""
    return workspace_actions.actions_journal(project_root, limit=int(raw) if raw.isdigit() else 100)


def _setup_guide(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/setup/guide`` — la machine vue par le guide de premier lancement (P36).

    Lecture seule : détection, profils mémoire possibles ou non avec leur raison,
    plan et commandes à copier. Rien ne s'écrit, rien ne démarre.
    """
    return workspace_setup_guide.setup_guide(project_root)


def _memory_recall(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/recall`` — les derniers reçus de rappel du projet (P27).

    ``task`` filtre une tâche, ``slug`` vise un projet du registre ; lecture seule.
    """
    return workspace_recall.recall_receipts(
        project_root, task_id=_one(query, "task") or None, slug=_one(query, "slug") or None,
        limit=_one(query, "limit") or workspace_recall.MAX_RECEIPTS,
    )


def _memory_recall_replay(project_root: Path, query: _Query) -> Any:
    """``GET /api/workspace/memory/recall/replay`` — rejoue un reçu ou une requête libre (P27).

    ``receipt`` (clé d'un reçu, ``task`` pour le cloisonner) ou ``q`` (requête
    libre) et ``base`` (la liste du passage précédent). Un calcul, aucune
    écriture : pas même un reçu de lecture.
    """
    return workspace_recall.recall_replay(
        project_root, receipt=_one(query, "receipt") or None, task_id=_one(query, "task") or None,
        query=_one(query, "q"), base=_one(query, "base"), limit=_one(query, "limit") or workspace_recall.FREE_LIMIT,
        slug=_one(query, "slug") or None,
    )


def _capacity(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/capacity`` — WIP, réservoirs d'admission, quota et file (P71).

    Le même plan à blanc que ``grimoire queue plan`` (K10), rendu pour le
    cockpit : occupation dérivée du ledger réel, jamais un compteur à côté,
    jamais une écriture. Voir :mod:`grimoire.tools.workspace_capacity`.
    """
    from grimoire.tools import workspace_capacity

    return workspace_capacity.capacity_view(project_root)


def _assist_status(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/assist`` — l'opt-in et la disponibilité, sans coût.

    Jamais d'appel à ``/api/generate`` ici : voir
    :func:`grimoire.tools.source_assist.assist_status`. L'éditeur l'appelle au
    montage pour savoir si le bouton « Suggérer » doit même apparaître
    (issue #280, voie 2 — « sinon l'interface ne montre rien et ne tente
    rien »).
    """
    return source_assist.assist_status(project_root)


def _language(project_root: Path, query: _Query) -> Any:
    """Tokens, diagnostics et complétions de l'éditeur Source (#280).

    ``text`` porte le brouillon affiché — s'il est absent, la lecture vient du
    disque, comme :func:`_file`. ``line``/``col`` sont 0-indexées ; les deux
    doivent être présentes pour obtenir des complétions, sinon la réponse n'a
    que ``tokens``/``diagnostics``.
    """
    line = _one(query, "line")
    col = _one(query, "col")
    return workspace_language.language_view(
        project_root,
        _one(query, "path"),
        text=_one(query, "text"),
        line=int(line) if line is not None else None,
        col=int(col) if col is not None else None,
    )


#: Chemins exacts. Les routes paramétrées par un identifiant de tâche sont
#: traitées à part dans :func:`workspace_get`, parce qu'un identifiant de ledger
#: n'est pas un segment fixe.
def _mandate_state(project_root: Path, _query: _Query) -> Any:
    """État du mandat de l'orchestrateur pour ce projet (#876, lot 1) : lecture seule, jamais d'octroi ici."""
    from grimoire.missions.mandate import GESTURES, MandateStore, control_key_path, ensure_control_key, load_escalation

    with contextlib.suppress(OSError):
        ensure_control_key()  # la clé existe dès la première lecture ; sa valeur ne sort jamais d'ici
    policy = load_escalation(project_root.resolve())
    return {
        **MandateStore(project_root).state().to_dict(),
        "gestures": list(GESTURES),
        "escalation": {"delegated": sorted(policy.delegated), "error": policy.error},
        "control_key_path": str(control_key_path()),
    }


def _autopilot_state(project_root: Path, _query: _Query) -> Any:
    """État du pilote automatique (P82) : lecture seule, jamais d'activation ici ; les décisions sont au ledger."""
    from grimoire.missions.autopilot import RESERVED, AutopilotStore, load_autopilot_policy

    policy = load_autopilot_policy(project_root.resolve())
    return {
        **AutopilotStore(project_root).state().to_dict(),
        "reserved": sorted(RESERVED), "extra_reserved": list(policy.extra), "policy_error": policy.error,
    }


GET_ROUTES: dict[str, _GetHandler] = {
f"{PREFIX}autonomy/autopilot": _autopilot_state,
f"{PREFIX}autonomy/mandate": _mandate_state,
    f"{PREFIX}glossary": _glossary,
    f"{PREFIX}capacity": _capacity,
    f"{PREFIX}tasks": _tasks,
    f"{PREFIX}waves": _waves,
    f"{PREFIX}files": _files,
    f"{PREFIX}file": _file,
    f"{PREFIX}file/diff": _file_diff,
    f"{PREFIX}file/usage": _file_usage,
    f"{PREFIX}file/history": _file_history,
    f"{PREFIX}upgrade/changes": _upgrade_changes,
    f"{PREFIX}upgrade/diff": _upgrade_diff,
    f"{PREFIX}commands": _commands,
    f"{PREFIX}doctor": _doctor,
    f"{PREFIX}language": _language,
    f"{PREFIX}assist": _assist_status,
    f"{PREFIX}blueprints": _blueprints,
    f"{PREFIX}agents": _agents,
    f"{PREFIX}hooks": _hooks,
    f"{PREFIX}providers": _providers,
    f"{PREFIX}options": _options,
    f"{PREFIX}notify": _notify,
    f"{PREFIX}proposals": _proposals,
    f"{PREFIX}sheet": _sheet,
    f"{PREFIX}evidence": _evidence,
    f"{PREFIX}portfolio/tasks": _portfolio_tasks,
    f"{PREFIX}sessions": _sessions,
    f"{PREFIX}pulse": _pulse,
    f"{PREFIX}attention": _attention,
    f"{PREFIX}attention/since": _attention_since,
    f"{PREFIX}reviews": _reviews,
    f"{PREFIX}flows/runs": _flow_runs,
    f"{PREFIX}memory/overview": _memory_overview,
    f"{PREFIX}memory/search": _memory_search,
    f"{PREFIX}memory/entries": _memory_entries,
    f"{PREFIX}memory/entry": _memory_entry,
    f"{PREFIX}memory/graph": _memory_graph,
    f"{PREFIX}memory/quality": _memory_quality,
    f"{PREFIX}data/machine": _data_machine,
    f"{PREFIX}data/actions": _data_actions,
    f"{PREFIX}data/actions/journal": _data_actions_journal,
    f"{PREFIX}setup/guide": _setup_guide,
    f"{PREFIX}memory/recall": _memory_recall,
    f"{PREFIX}memory/recall/replay": _memory_recall_replay,
    f"{PREFIX}chat/state": workspace_chat.chat_state,
    f"{PREFIX}chat/hosts": workspace_chat.chat_hosts,
}


#: Les lectures que les espaces relancent à chaque pouls changé (P79) : gardées par empreinte
#: (:mod:`grimoire.tools.workspace_read_cache`), seulement quand la requête porte ``live=1``.
#: Le paramètre que la page pose sur une lecture du direct (P79) ; il ne change jamais la réponse.
PULSE_RELOAD = "pulse_reload"
LIVE_ROUTES = frozenset({f"{PREFIX}tasks", f"{PREFIX}attention", f"{PREFIX}sessions", f"{PREFIX}sheet"})

#: Le flux Server-Sent Events du pouls (P10) : même charge utile que ``pulse``,
#: poussée dès qu'elle change au lieu d'être sondée.
STREAM_PATH = f"{PREFIX}stream"

#: En-têtes de la réponse du flux. Pas de ``Content-Length`` : la réponse dure
#: tant que la page reste ouverte.
STREAM_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Type", "text/event-stream; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Accel-Buffering", "no"),
)


def workspace_stream(project_root: Path, query: _Query) -> Generator[bytes, None, None]:
    """Le flux du pouls pour *project_root*, servi par l'hôte sur :data:`STREAM_PATH`.

    Mêmes paramètres que ``pulse`` : ``since`` (l'empreinte connue) et
    ``scope=portfolio`` (le registre du cockpit).
    """
    return workspace_pulse.pulse_stream(
        project_root,
        since=_one(query, "since") or None,
        portfolio=_one(query, "scope") == "portfolio",
    )


#: Le flux du chat de l'orchestrateur (ACP2) : SSE, servi comme le pouls par l'hôte.
CHAT_STREAM_PATH = f"{PREFIX}chat/stream"


def chat_stream(project_root: Path, query: _Query) -> Generator[bytes, None, None]:
    """Le flux de la conversation de l'orchestrateur du projet, servi sur :data:`CHAT_STREAM_PATH`."""
    return workspace_chat.chat_stream(project_root, query)


def _task_id(path: str, suffix: str = "") -> str:
    """Extrait l'identifiant de ``/api/workspace/tasks/<id>[/<suffix>]``."""
    rest = path[len(f"{PREFIX}tasks/") :]
    if suffix:
        rest = rest[: -(len(suffix) + 1)]
    task_id = rest.strip("/")
    if not task_id or "/" in task_id:
        raise ValueError("identifiant de tâche invalide")
    return task_id


def workspace_get(project_root: Path, path: str, query: _Query) -> Any:
    """Résout une lecture de la vue de travail pour *project_root*.

    Rend :data:`WORKSPACE_UNHANDLED` si le chemin n'est pas à nous, pour que
    l'hôte appelant poursuive sa propre chaîne (fichiers statiques, 404).
    """
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError

    try:
        handler = GET_ROUTES.get(path)
        # Une lecture d'un autre projet que celui servi (`project=`, la fiche en portefeuille) ou du portefeuille n'est
        # jamais gardée : son empreinte couvre des ledgers que ce serveur n'écrit pas (e2e du portefeuille rouge en CI
        # seulement, 2026-10-28). Le cache reste celui du projet servi, le cas mesuré par P79.
        foreign = _one(query, "scope") == "portfolio" or bool(_one(query, "project"))
        # Le marqueur du direct n'est jamais un paramètre de route : retiré avant toute lecture. Il s'appelait `live`,
        # déjà le filtre « Sessions vivantes » de `tasks` en portefeuille : la fiche du projet B y disparaissait (CI de
        # main, 2026-10-29).
        reload = _one(query, PULSE_RELOAD) == "1"
        query = {k: v for k, v in query.items() if k != PULSE_RELOAD}
        if handler is not None and path in LIVE_ROUTES and reload and not foreign:
            # P79 : un rechargement du direct (`live=1`, posé par la page pendant un pouls changé) est servi une fois
            # par empreinte, pas une par onglet. Un chargement de page ou une navigation relisent toujours.
            from grimoire.tools import workspace_pulse, workspace_read_cache

            plain = query
            return workspace_read_cache.cached_read(
                path, project_root, plain, lambda: handler(project_root, plain),
                lambda: workspace_pulse.fingerprint_of(project_root),
            )
        if handler is not None:
            return handler(project_root, query)
        if path.startswith(f"{PREFIX}sessions/"):
            # Bloc Activité d'une session (P69v2) : le fil des tâches que son claim tient.
            rest = path[len(f"{PREFIX}sessions/") :].strip("/")
            session_id, _, tail = rest.partition("/")
            if session_id and tail == "feed":
                from grimoire.tools import workspace_session_feed

                return workspace_session_feed.session_feed(project_root, session_id)
        if path.startswith(f"{PREFIX}tasks/"):
            if path.endswith("/launch-plan"):
                # Préflight d'assignation (P47 lot 1) : plan en lecture seule, rien n'est lancé (ADR-008).
                from grimoire.tools import workspace_launch_preflight

                return workspace_launch_preflight.launch_preflight(project_root, _task_id(path, "launch-plan"))
            if path.endswith("/feed"):
                # Bloc Activité d'une tâche (P69v2) : le fil dérivé du ledger.
                from grimoire.tools import workspace_session_feed

                return workspace_session_feed.task_feed(project_root, _task_id(path, "feed"))
            if path.endswith("/trace"):
                return workspace_api.task_trace_view(project_root, _task_id(path, "trace"))
            if path.endswith("/recall"):
                return workspace_api.task_recall_view(project_root, _task_id(path, "recall"))
            if path.endswith("/receipts"):
                return workspace_recall.recall_receipts(project_root, task_id=_task_id(path, "receipts"))
            return workspace_api.task_view(project_root, _task_id(path))
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED


# ── Écritures — hôte mono-projet seulement ──────────────────────────────────


def _create_override(project_root: Path, body: dict[str, Any]) -> Any:
    """Copie un fichier de l'étage kit vers l'étage overrides.

    C'est le geste que la spécification attache à « éditer un fichier du kit » :
    on ne modifie pas le kit, on prend possession d'une copie qui prime et qui
    survit à ``grimoire up``. Un override déjà là n'est jamais écrasé — la
    réponse le dit et le geste est idempotent.
    """
    from grimoire.core import layout

    root = project_root.resolve()
    source = workspace_api.safe_relpath(root, body.get("path"))
    rel = source.relative_to(root).as_posix()
    prefix = f"{layout.KIT_DIR}/"
    if not rel.startswith(prefix):
        raise workspace_api.WorkspacePathError(
            "seul un fichier de l'étage kit se prend en override"
        )
    if not source.is_file():
        raise FileNotFoundError(f"introuvable : {rel}")
    dest_rel = f"{layout.OVERRIDES_DIR}/{rel[len(prefix) :]}"
    dest = root / dest_rel
    if dest.is_file():
        return {"created": False, "override_path": dest_rel, "note": "override déjà présent"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return {"created": True, "override_path": dest_rel, "from": rel}


def _write_file(project_root: Path, body: dict[str, Any]) -> Any:
    """Écrit un fichier d'un étage éditable — l'étage overrides, et lui seul.

    Écrire dans l'étage kit serait perdu à la prochaine mise à jour ; le refus
    nomme l'override à créer plutôt que de laisser l'utilisateur découvrir la
    perte trois jours plus tard.
    """
    root = project_root.resolve()
    target = workspace_api.safe_relpath(root, body.get("path"))
    tier = workspace_api.tier_of(root, target)
    if tier is None or not tier["editable"]:
        raise workspace_api.WorkspacePathError(
            "étage non éditable : prenez d'abord un override (POST /api/workspace/file/override)"
        )
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("`text` requis")
    if len(text.encode("utf-8")) > workspace_api.FILE_TEXT_LIMIT:
        raise ValueError("contenu trop volumineux")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return workspace_api.file_view(root, target.relative_to(root).as_posix())


def _assist(project_root: Path, body: dict[str, Any]) -> Any:
    """Suggestion de contenu par un petit modèle local (issue #280, voie 2).

    Jamais à la place de l'IntelliSense déterministe (:mod:`workspace_language`,
    voie 1) : voir :mod:`grimoire.tools.source_assist` pour l'opt-in, la
    sonde Ollama et la vérification des identifiants cités. Toujours un 200
    côté transport — un refus (opt-in absent, Ollama indisponible, délai
    dépassé) est une valeur (``available: False``), pas une exception.
    """
    return source_assist.assist_view(project_root, body)


def _command(project_root: Path, body: dict[str, Any]) -> Any:
    argv = body.get("argv")
    if isinstance(argv, str):
        argv = argv.split()
    if not isinstance(argv, list):
        raise ValueError("`argv` doit être une liste de mots")
    return workspace_exec.run_command(
        project_root, argv, allow_mutation=body.get("confirm") is True
    )


def _task_action(project_root: Path, task_id: str, action: str, body: dict[str, Any]) -> Any:
    """Réclame, déplace, bloque, ferme — ou prioritise, commente, annule, accuse (issue #638).

    Le service est le même que celui du CLI et du serveur MCP : un gate
    contourné ici le serait partout, donc il n'y a qu'un endroit où il pourrait
    l'être, et ce n'est pas celui-ci. La webview n'écrit jamais elle-même
    (ADR-007) : elle demande, le service décide, le ledger enregistre.
    """
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve())
    actor = str(body.get("actor") or "workspace")
    if action in _STEERING_ACTIONS:
        return _task_steer(service, task_id, action, actor, body).to_dict()
    if action == "claim":
        move = service.claim(task_id, actor, str(body.get("host") or "workspace"))
    elif action == "close":
        move = service.transition(task_id, TaskState.CLOSED, actor, str(body.get("reason") or ""))
    elif action == "block":
        reason = str(body.get("reason") or "")
        if not reason:
            raise ValueError("`reason` requis pour bloquer une tâche")
        move = service.transition(task_id, TaskState.BLOCKED, actor, reason)
    elif action == "move":
        target = body.get("to")
        try:
            state = TaskState(str(target))
        except ValueError as exc:
            raise ValueError(
                f"état inconnu : {target!r} — parmi {', '.join(s.value for s in TaskState)}"
            ) from exc
        move = service.transition(task_id, state, actor, str(body.get("reason") or ""))
    else:  # pragma: no cover — garde de complétude
        raise ValueError(f"action inconnue : {action}")
    return move.to_dict()


def _task_migrate_standard(project_root: Path, _body: dict[str, Any]) -> Any:
    """Bouton « Migrer les tâches » de l'espace Exécuter (ADR-007, issue #559).

    Même moteur que ``grimoire task migrate-standard`` (`missions.task_unification`,
    lot 4.1 1/3) : idempotent, réversible par instantané horodaté — jamais
    exposé ici, la restauration reste un geste CLI délibéré. ``project_root``
    est déjà résolu par l'appelant (le registre, via :func:`is_registry_scoped_write`
    côté cockpit — issue #559 suite, #560) : n'importe quel projet du
    registre, pas seulement celui de lancement direct.
    """
    from grimoire.missions.task_unification import migrate_standard_tasks

    return migrate_standard_tasks(project_root).to_dict()


def _task_add(project_root: Path, body: dict[str, Any]) -> Any:
    """« Nouvelle tâche » de Travail (P35) : ``TaskService.add``, l'entrée de ``grimoire task add`` et de ``task_add``.

    Titre, critères d'acceptation (une liste, ou un texte à un critère par ligne)
    et description. Écriture du projet servi seulement : le cockpit ne câble
    cette route que pour son projet de lancement (garde P02, aucune dérogation).
    """
    from grimoire.missions.service import TaskService

    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("titre requis : nomme la tâche")
    raw = body.get("acceptance")
    items = raw.splitlines() if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise ValueError("`acceptance` doit être une liste de critères")
    acceptance = tuple(str(item).strip() for item in items if str(item).strip())
    if not acceptance:
        raise ValueError("au moins un critère d'acceptation est requis")
    from grimoire.core.exceptions import GrimoireMissionError

    try:
        added = TaskService(project_root.resolve()).add(
            title, acceptance, description=str(body.get("description") or ""),
            actor=str(body.get("actor") or "workspace"), origin="cockpit",
        )
    except GrimoireMissionError as exc:  # description trop longue, critère refusé : un 400, pas un 404
        raise ValueError(str(exc)) from exc
    return added.to_dict()


def _mandate_grant(project_root: Path, body: dict[str, Any]) -> Any:
    """Accorde le mandat de l'orchestrateur (#876, lot 1, verrou 15) : l'origine humaine est posée ici.

    Le cockpit local, derrière le jeton de session, est le canal de l'humain pour ses commentaires et ses
    réponses. Le jeton ne suffit pas pour un mandat : il est injecté dans toute page HTML servie sur la
    loopback, qu'un processus local peut récupérer. L'octroi exige en plus la clé de contrôle
    (``~/.grimoire/control/cockpit-key``), que l'humain lit depuis son terminal et que le frein refuse aux
    agents (règle ``cockpit-key`` des secrets). Sans clé valide : 403, rien n'est écrit.
    """
    from grimoire.missions.mandate import (
        JournalError,
        MandateRefusedError,
        MandateRequest,
        MandateStore,
        verify_control_key,
    )
    from grimoire.missions.schemas import ORIGIN_HUMAN

    if not verify_control_key(body.get("control_key")):
        raise PermissionError("clé de contrôle absente ou invalide : accorder un mandat est un geste d'humain")
    try:
        request = MandateRequest.build(
            body.get("gestures") or (), body.get("task_types") or (), body.get("max_sessions_per_day") or 0,
            str(body.get("until") or ""),
        )
        mandate = MandateStore(project_root).grant(request, origin=ORIGIN_HUMAN, via="cockpit")
    except (MandateRefusedError, JournalError) as exc:
        raise ValueError(str(exc)) from exc  # une demande invalide ou un journal rompu : un 400, pas un 404
    return {"granted": True, "mandate": mandate.to_dict()}


def _mandate_revoke(project_root: Path, body: dict[str, Any]) -> Any:
    """Révoque le mandat actif — toujours permis derrière le jeton : retirer une autorité n'en demande aucune."""
    from grimoire.missions.mandate import REVOKE_MANUAL, MandateStore

    revoked = MandateStore(project_root).revoke(REVOKE_MANUAL, str(body.get("reason") or ""))
    return {"revoked": revoked.to_dict() if revoked else None}


def _autopilot_set(project_root: Path, body: dict[str, Any]) -> Any:
    """Active ou coupe le pilote automatique (P82) : l'origine humaine est posée ici, derrière la clé de contrôle.

    Comme l'octroi du mandat, le jeton de session ne suffit pas (une page servie sur la loopback le porte) :
    couper comme activer exige la clé de contrôle. Sans clé valide : 403, rien n'est écrit. L'activation exige un
    mandat actif, auquel le pilote reste lié.
    """
    from grimoire.missions.autopilot import AutopilotStore
    from grimoire.missions.mandate import JournalError, MandateRefusedError, verify_control_key
    from grimoire.missions.schemas import ORIGIN_HUMAN

    if not verify_control_key(body.get("control_key")):
        raise PermissionError("clé de contrôle absente ou invalide : le pilote automatique est un geste d'humain")
    if not isinstance(body.get("on"), bool):
        raise ValueError("`on` (booléen) requis : true active, false coupe")
    try:
        entry = AutopilotStore(project_root).set(body["on"], origin=ORIGIN_HUMAN, via="cockpit")
    except (MandateRefusedError, JournalError) as exc:
        raise ValueError(str(exc)) from exc
    return {"autopilot": entry["state"], "mandate_id": entry["mandate_id"], "ts": entry["ts"]}


#: Écritures à chemin fixe. Les actions de tâche sont paramétrées, cf. plus bas.
POST_ROUTES: dict[str, _PostHandler] = {
    f"{PREFIX}autonomy/autopilot": _autopilot_set,
    # Chat de l'orchestrateur (ACP2) : un tour humain relayé à l'agent ; le cockpit n'écrit rien (ADR-007).
    f"{PREFIX}chat/send": workspace_chat.chat_send,
    f"{PREFIX}chat/permission": workspace_chat.chat_permission,
    f"{PREFIX}chat/cancel": workspace_chat.chat_cancel,
    f"{PREFIX}chat/stop": workspace_chat.chat_stop,
    f"{PREFIX}chat/host": workspace_chat.chat_host,
    f"{PREFIX}autonomy/mandate/grant": _mandate_grant,
    f"{PREFIX}autonomy/mandate/revoke": _mandate_revoke,
    f"{PREFIX}file/override": _create_override,
    f"{PREFIX}file/write": _write_file,
    f"{PREFIX}command": _command,
    f"{PREFIX}assist": _assist,
    f"{PREFIX}tasks/migrate-standard": _task_migrate_standard,
    f"{PREFIX}tasks/add": _task_add,
    f"{PREFIX}options": _options_write,
    f"{PREFIX}notify": _notify_write,
    f"{PREFIX}notify/test": _notify_test,
}

def _task_steer(service: Any, task_id: str, action: str, actor: str, body: dict[str, Any]) -> Any:
    """Les gestes de l'orchestrateur humain (issue #638, lot B ; K6 : répondre) — refus rendus par le service."""
    from grimoire.missions.schemas import ORIGIN_HUMAN

    if action == "prioritize":
        return service.prioritize(task_id, str(body.get("to") or ""), actor, str(body.get("reason") or ""))
    if action == "comment":
        # Le cockpit local est le canal de l'humain : l'origine est posée ici,
        # jamais lue du corps de la requête (issue #706). `actor` n'est qu'un nom.
        return service.comment(
            task_id, str(body.get("text") or ""), actor, str(body.get("kind") or "comment"), origin=ORIGIN_HUMAN
        )
    if action == "cancel":
        return service.cancel(task_id, str(body.get("reason") or ""), actor, force=body.get("force") is True)
    if action == "answer":
        # K6 : la réponse à une décision attendue. Le cockpit local, derrière le jeton de session
        # (W1-10), est le canal de l'humain : l'origine est posée ici, jamais lue du corps.
        return service.answer(
            task_id, str(body.get("text") or ""), actor, origin=ORIGIN_HUMAN,
            question_id=str(body.get("question_id") or ""),
            answer_data=body["answer_data"] if isinstance(body.get("answer_data"), dict) else None,
        )
    if action == "accept":
        # P88 : l'acceptation de la preuve déclarative (#865), même canal humain que `answer` — l'origine est
        # posée ici ; le service refuse hors revue, sans motif ou sans pack.
        return service.accept(task_id, str(body.get("reason") or ""), actor, origin=ORIGIN_HUMAN)
    directive_id = str(body.get("directive_id") or "")
    if not directive_id:
        raise ValueError("`directive_id` requis pour accuser réception")
    return service.acknowledge(task_id, directive_id, actor)


#: Les gestes de pilotage humain (issue #638) : jamais une transition d'état
#: sauf `cancel`, qui passe par le même gate que `move --to cancelled` plus la
#: raison obligatoire et le verrou de claim.
_STEERING_ACTIONS = ("prioritize", "comment", "cancel", "ack", "answer", "accept")

#: Les verbes qu'une tâche accepte depuis l'interface.
TASK_ACTIONS = ("claim", "move", "block", "close", *_STEERING_ACTIONS)


# ── Agents (issue #374) ─────────────────────────────────────────────────────
#
# Toute écriture ici porte sur la couche ``overrides`` du projet, jamais sur
# le kit : un projet personnalise, il ne modifie pas ce que le kit livre
# (doctrine, ``docs/artifact-doctrine.md``). Le geste est celui de
# ``grimoire_add_agent`` (``src/grimoire/mcp/server.py``) et de
# ``_create_override`` ci-dessus, appliqué au frontmatter d'un agent déjà
# installé plutôt qu'à un fichier neuf ou une copie brute : on écrit d'abord
# dans l'override, puis on valide avec la même lecture que
# ``collect_agents`` — la garde qui refuse déjà un skill ou un contexte
# introuvable — et on annule l'écriture si elle échoue. Le message d'erreur
# est donc exactement celui que ``collect`` produit, jamais dupliqué ici.

#: Frontmatter d'un fichier agent : un commentaire HTML d'archétype optionnel,
#: un bloc YAML entre ``---``, puis le corps. Même forme que
#: ``grimoire.hosts.collect._FRONTMATTER_RE``, mais le commentaire est capturé
#: ici (pas seulement sauté) pour pouvoir le réécrire tel quel.
_AGENT_FM_RE = re.compile(
    r"\A(?P<bom>﻿)?(?P<comment>(?:<!--.*?-->\s*)?)---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)

#: Champs de frontmatter que le cockpit sait modifier. Le reste (``name``,
#: ``description``, ``model_affinity``…) n'est pas de la configuration au sens
#: de l'issue — le modifier romprait l'identité de l'agent, pas son emploi.
_AGENT_STRING_FIELDS = frozenset({"use_when", "dont_use_when", "tool_boundary", "tools"})
_AGENT_LIST_FIELDS = frozenset({"skills", "context"})


def _agent_target(project_root: Path, name: str) -> Path:
    """Fichier installé de l'agent *name*, nommé comme ``agents_view`` le nomme.

    Résolu via :func:`collect_agents`, la même lecture qu'``agents_view``
    emploie pour construire la liste affichée : un agent visible à la lecture
    doit rester résoluble à l'écriture, sous le même nom. Depuis #381,
    ``collect_agents`` partage avec ``layout.installed_agents`` (le
    diagnostic, la carte de routage) la même lecture d'identité
    (:func:`grimoire.core.layout.agent_identity`) ; un gabarit non rendu comme
    ``custom-agent.md`` (``name: "{{agent_tag}}"`` non substitué) n'a d'identité
    pour aucun des deux et n'apparaît donc pas ici — ce n'est pas un agent
    installé, seulement un modèle à compléter.
    """
    from grimoire.hosts import collect

    root = project_root.resolve()
    skills = collect.collect_skills(root)
    known_skills = frozenset(s.slug for s in skills)
    for agent in collect.collect_agents(root, known_skills=known_skills):
        if agent.name == name:
            return root / agent.definition_ref
    raise FileNotFoundError(f"agent introuvable : {name}")


def _agent_override_path(project_root: Path, source: Path) -> Path:
    """Le chemin overrides d'un agent installé à *source*.

    Même nom de fichier qu'à la source, quel que soit l'étage d'où elle vient
    (kit, ou un répertoire hérité) : les agents n'ont jamais qu'un seul niveau
    de sous-dossier (``agents/<tag>.md``), donc le nom de fichier suffit à
    reconstruire le chemin d'override sans connaître l'étage de départ.
    """
    from grimoire.core import layout

    return layout.overrides_dir(project_root) / layout.AGENTS_SUBDIR / source.name


def _load_agent_frontmatter(path: Path) -> tuple[str, str, Any, str]:
    """Lit *path* en ``(bom, commentaire, données YAML éditables, corps)``."""
    from ruamel.yaml import YAML

    text = path.read_text(encoding="utf-8")
    match = _AGENT_FM_RE.match(text)
    if match is None:
        raise ValueError(f"fichier agent sans frontmatter : {path}")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    data = yaml.load(match.group("yaml"))
    if not isinstance(data, dict):
        raise ValueError(f"frontmatter d'agent invalide : {path}")
    return match.group("bom") or "", match.group("comment"), data, match.group("body")


def _dump_agent_frontmatter(bom: str, comment: str, data: Any, body: str) -> str:
    """Réassemble un fichier agent après modification de son bloc YAML."""
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(data, buf)
    return f"{bom}{comment}---\n{buf.getvalue()}---\n{body}"


def _str_list(raw: Any) -> tuple[str, ...]:
    """Lit une clé frontmatter liste-de-chaînes, tolérante à une chaîne seule.

    Même règle que ``grimoire.hosts.collect._str_tuple`` — dupliquée plutôt
    qu'importée : quatre lignes, aucune logique métier, et l'import d'un nom
    privé d'un autre module aurait été le mauvais genre de réutilisation.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _apply_agent_updates(project_root: Path, name: str, updates: dict[str, Any]) -> Any:
    """Écrit *updates* dans l'override de l'agent *name*, valide, ou annule.

    Crée l'override s'il n'existe pas encore — un override **partiel**
    (``extends: kit``, issue #427) quand un agent kit de même nom existe déjà :
    seuls les champs de *updates* y apparaissent, le corps et le reste du
    frontmatter restent hérités du fichier kit, empreinte
    (``kit_source_hash``) à l'appui pour que `doctor`/le cockpit signalent une
    dérive future. Sans contrepartie kit (répertoire hérité), la seule
    personnalisation possible reste une copie intégrale, comme avant cette
    issue. Une valeur ``None``, chaîne vide ou liste vide retire la clé
    plutôt que d'écrire une déclaration vide. La validation est celle de
    ``collect_agents`` — skill ou contexte introuvable lève
    ``GrimoireAgentError``, translatée en ``ValueError`` par
    :func:`workspace_post`, avec le message que ``collect`` produit déjà.
    """
    from grimoire.core import layout
    from grimoire.core.exceptions import GrimoireAgentError
    from grimoire.core.override_drift import compute_kit_source_hash
    from grimoire.hosts import collect

    root = project_root.resolve()
    source = _agent_target(root, name)
    override_path = _agent_override_path(root, source)
    pre_existing = override_path.is_file()
    if not pre_existing:
        override_path.parent.mkdir(parents=True, exist_ok=True)
        if layout.is_kit_owned(root, source):
            skeleton = _dump_agent_frontmatter(
                "", "",
                {"extends": "kit", "kit_source_hash": compute_kit_source_hash(source)},
                "",
            )
            override_path.write_text(skeleton, encoding="utf-8")
        else:
            override_path.write_bytes(source.read_bytes())
    original_text = override_path.read_text(encoding="utf-8")

    bom, comment, data, body = _load_agent_frontmatter(override_path)
    is_partial = str(data.get("extends", "")).strip().lower() == "kit"
    kit_meta: dict[str, Any] = {}
    if is_partial:
        # `source` est déjà le fichier kit pour un override partiel — voir
        # `_agent_target` : son `definition_ref` pointe vers le kit depuis
        # l'issue #427. Nécessaire pour distinguer, en cas d'effacement, « le
        # kit ne déclare rien ici non plus » (retirer la clé, comme avant
        # cette issue) de « le kit déclare une valeur, et il ne faut pas
        # qu'elle refasse surface par simple absence » (garder la clé, vide
        # explicitement).
        from grimoire.hosts.collect import parse_frontmatter

        kit_meta, _ = parse_frontmatter(source.read_text(encoding="utf-8"))
    for key, value in updates.items():
        if value in (None, "", []):
            if is_partial and kit_meta.get(key) not in (None, "", []):
                data[key] = [] if key in _AGENT_LIST_FIELDS else ""
            else:
                data.pop(key, None)
        else:
            data[key] = value
    override_path.write_text(_dump_agent_frontmatter(bom, comment, data, body), encoding="utf-8")

    try:
        skills = collect.collect_skills(root)
        collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError:
        if pre_existing:
            override_path.write_text(original_text, encoding="utf-8")
        else:
            override_path.unlink(missing_ok=True)
        raise
    return workspace_api.agents_view(root)


def _agent_skill_action(project_root: Path, name: str, body: dict[str, Any]) -> Any:
    """Assigne ou retire un skill — l'écriture porte toujours sur la liste entière."""
    slug = str(body.get("skill", "")).strip()
    action = str(body.get("action", "")).strip()
    if not slug:
        raise ValueError("`skill` requis")
    if action not in {"assign", "remove"}:
        raise ValueError("`action` doit valoir « assign » ou « remove »")

    root = project_root.resolve()
    _, _, data, _ = _load_agent_frontmatter(_agent_target(root, name))
    current = _str_list(data.get("skills"))
    if action == "assign":
        updated = list(current) if slug in current else [*current, slug]
    else:
        updated = [s for s in current if s != slug]
    return _apply_agent_updates(root, name, {"skills": updated})


def _agent_fields_update(project_root: Path, name: str, body: dict[str, Any]) -> Any:
    """Modifie la clause d'emploi, les outils ou le contexte déclaré d'un agent.

    Chaque champ présent dans *body* est validé pour sa propre forme ; la
    validation croisée (skill connu, contexte qui existe sur disque) reste
    celle de ``collect_agents``, appliquée par :func:`_apply_agent_updates`.
    """
    from grimoire.hosts.surface import ToolVerb

    updates: dict[str, Any] = {}
    for field in ("use_when", "dont_use_when", "tool_boundary"):
        if field in body:
            updates[field] = str(body[field] or "").strip()

    if "tools" in body:
        raw = body["tools"]
        values = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(values, list):
            raise ValueError("`tools` doit être une liste, ou une chaîne séparée par des virgules")
        cleaned = [str(v).strip().lower() for v in values if str(v).strip()]
        known = {v.value for v in ToolVerb}
        unknown = [v for v in cleaned if v not in known]
        if unknown:
            raise ValueError(f"outil(s) inconnu(s) : {', '.join(unknown)} — parmi {', '.join(sorted(known))}")
        updates["tools"] = ", ".join(cleaned)

    if "context" in body:
        raw = body["context"]
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError("`context` doit être une liste de chemins")
        updates["context"] = [str(p).strip() for p in raw if str(p).strip()]

    unknown_fields = set(body) - _AGENT_STRING_FIELDS - _AGENT_LIST_FIELDS
    if unknown_fields:
        raise ValueError(f"champ(s) non modifiable(s) : {', '.join(sorted(unknown_fields))}")
    if not updates:
        raise ValueError("aucun champ à modifier")
    return _apply_agent_updates(project_root, name, updates)


def _agent_route(path: str) -> tuple[str, str] | None:
    """``(nom, action)`` depuis ``/api/workspace/agents/<nom>/<action>``, ou ``None``."""
    rest = path[len(f"{PREFIX}agents/") :]
    name, _, action = rest.partition("/")
    if not name or not action or "/" in action:
        return None
    return name, action


# ── Propositions d'artefact (issue #395) ────────────────────────────────────
#
# Même moteur que la CLI (``grimoire proposals``) : :mod:`grimoire.proposals`
# décide, écrit et valide — cette route ne fait que le brancher, comme
# ``_agent_skill_action``/``_agent_fields_update`` le font déjà pour les
# agents. Aucune création silencieuse : accepter écrit un artefact réel (et
# annule si la garde de distinction le refuse), refuser ne fait que marquer
# la proposition — jamais l'inverse.


def _proposal_route(path: str) -> tuple[str, str] | None:
    """``(slug, action)`` depuis ``/api/workspace/proposals/<slug>/<action>``, ou ``None``."""
    rest = path[len(f"{PREFIX}proposals/") :]
    slug, _, action = rest.partition("/")
    if not slug or not action or "/" in action:
        return None
    return slug, action


def is_proposal_decision(path: str) -> bool:
    """Vrai pour ``proposals/<slug>/accept`` ou ``…/reject`` — jamais autre chose.

    Le seul point d'entrée que ``cmd_cockpit.py`` interroge pour ouvrir, pour
    cette écriture précise, la même porte que ``POST /api/projects/update``
    plutôt que la garde ``_HOME_SLUG`` générale (issue #490, voir le docstring
    du module). Volontairement étroit : un chemin qui ressemble à une
    proposition sans être exactement un ``accept``/``reject`` (une future
    action, une faute de frappe côté client) reste soumis à la garde par
    défaut plutôt que de s'y dérober par accident.
    """
    if not path.startswith(f"{PREFIX}proposals/"):
        return False
    parsed = _proposal_route(path)
    return parsed is not None and parsed[1] in ("accept", "reject")


def _portfolio_action_route(path: str) -> tuple[str, str] | None:
    """``(task_id, action)`` depuis ``/api/workspace/portfolio/tasks/<id>/<action>``,
    ou ``None`` — seules les actions de :data:`TASK_ACTIONS` sont reconnues."""
    prefix = f"{PREFIX}portfolio/tasks/"
    if not path.startswith(prefix):
        return None
    task_id, _, action = path[len(prefix) :].strip("/").partition("/")
    if not task_id or "/" in task_id or action not in TASK_ACTIONS:
        return None
    return task_id, action


def is_registry_scoped_write(path: str) -> bool:
    """Vrai pour ``tasks/migrate-standard`` et pour une action du portefeuille
    ``portfolio/tasks/<id>/<claim|move|block|close>`` — dérogations nommées à
    ``_HOME_SLUG``.

    Même famille que :func:`is_proposal_decision` (issue #490) : le point
    d'entrée que ``cmd_cockpit.py`` interroge pour ouvrir, pour ces écritures
    précises, la même porte que ``POST /api/projects/update`` plutôt que la
    garde ``_HOME_SLUG`` générale (issue #559 suite, #560, voir le docstring
    du module). Migrer un board du standard vers le Mission Ledger est un
    geste explicite et ponctuel sur un projet du registre qu'on pilote depuis
    la flotte ; agir sur une carte depuis le portefeuille (issue #638, lot C)
    est le geste même de l'orchestrateur humain sur sa flotte — le
    ``TaskService`` du projet propriétaire garde la transition. Réclamer une
    tâche ou écrire un fichier par les chemins historiques reste soumis à la
    garde générale.
    """
    return path == f"{PREFIX}tasks/migrate-standard" or _portfolio_action_route(path) is not None


def _portfolio_task_action(project_root: Path, task_id: str, action: str, body: dict[str, Any]) -> Any:
    """Une action du portefeuille : même moteur que :func:`_task_action`, sur
    la racine que l'hôte a résolue depuis ``?project=`` — et que le ``project``
    du corps, s'il est là, doit confirmer (fail-closed sur le dépôt visé)."""
    from grimoire.tools.project_registry import slug_for_path

    wanted = str(body.get("project") or "").strip()
    if wanted:
        actual = slug_for_path(project_root)
        if actual != wanted:
            raise PermissionError(
                f"le portefeuille visait le projet {wanted!r}, l'hôte a résolu {actual or 'un projet hors registre'!r}"
            )
    return _task_action(project_root, task_id, action, body)


def _proposal_accept(project_root: Path, slug: str, _body: dict[str, Any]) -> Any:
    from grimoire.proposals import accept_proposal

    return accept_proposal(project_root, slug)


def _proposal_reject(project_root: Path, slug: str, _body: dict[str, Any]) -> Any:
    from grimoire.proposals import reject_proposal

    return reject_proposal(project_root, slug)


def workspace_post(project_root: Path, path: str, body: dict[str, Any]) -> Any:
    """Résout une écriture de la vue de travail pour ``project_root``.

    Agnostique de l'hôte appelant : c'est à l'appelant de décider s'il a le
    droit d'écrire ici (l'atelier, toujours ; le cockpit, seulement pour son
    projet de lancement — voir le docstring du module).
    """
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.service import TaskRefusedError

    try:
        handler = POST_ROUTES.get(path)
        if handler is not None:
            return handler(project_root, body)
        if path.startswith(f"{PREFIX}tasks/"):
            tail = path[len(f"{PREFIX}tasks/") :].strip("/")
            task_id, _, action = tail.partition("/")
            if task_id and action == "launch":
                # Apply d'assignation (P47 lot 3) : l'empreinte du plan engage
                # le lancement (ADR-008) ; la garde d'écriture commune est déjà
                # passée côté hôte, comme pour toute écriture.
                from grimoire.tools import workspace_launch_apply

                return workspace_launch_apply.launch_apply(project_root, task_id, body)
            if task_id and action in TASK_ACTIONS:
                return _task_action(project_root, task_id, action, body)
        if path.startswith(f"{PREFIX}data/actions/"):
            # Actions typées (ADR-008 D3) : plan → apply → rollback, journal ajout seul.
            from grimoire.tools import workspace_actions

            action_id, _, verb = path[len(f"{PREFIX}data/actions/") :].strip("/").rpartition("/")
            handlers = {
                "plan": workspace_actions.plan_action,
                "apply": workspace_actions.apply_action,
                "rollback": workspace_actions.rollback_action,
            }
            if action_id in workspace_actions.CATALOGUE and verb in handlers:
                if verb != "plan" and not workspace_actions.CATALOGUE[action_id].applicable:
                    return WORKSPACE_UNHANDLED
                return handlers[verb](project_root, action_id, body)
            return WORKSPACE_UNHANDLED
        portfolio_action = _portfolio_action_route(path)
        if portfolio_action is not None:
            return _portfolio_task_action(project_root, *portfolio_action, body)
        if path.startswith(f"{PREFIX}agents/"):
            parsed = _agent_route(path)
            if parsed is not None:
                agent_name, action = parsed
                if action == "skill":
                    return _agent_skill_action(project_root, agent_name, body)
                if action == "fields":
                    return _agent_fields_update(project_root, agent_name, body)
        if path.startswith(f"{PREFIX}proposals/"):
            parsed_proposal = _proposal_route(path)
            if parsed_proposal is not None:
                slug, action = parsed_proposal
                if action == "accept":
                    return _proposal_accept(project_root, slug, body)
                if action == "reject":
                    return _proposal_reject(project_root, slug, body)
    except TaskRefusedError as exc:
        # Un gate rouge n'est pas une panne du serveur : c'est la réponse. On
        # la rend telle quelle, avec la preuve manquante et son remède, comme
        # le fait déjà l'outil MCP `task_update`.
        return exc.to_dict()
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED
