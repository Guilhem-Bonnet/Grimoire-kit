"""GAO-cockpit-lect-001 : les lectures lourdes du cockpit se calculent une seule fois à la fois.

Constat du 2026-11-12 : plusieurs onglets (ou un rechargement) lancent la même
lecture en même temps et chacun la recalcule. ``/api/workspace/files`` prenait
27 à 31 s sur un gros projet ; pendant ce temps les autres routes ralentissaient
d'un facteur 3 à 10. Deux corrections sont prouvées ici, pas constatées :

- **vol unique** — deux fils qui demandent la même lecture au même instant
  partagent un seul calcul, la seconde reçoit la réponse (ou l'erreur) de la
  première ; une erreur n'est jamais gardée ;
- **liste légère pour la palette** — ``/api/workspace/tasks?fields=id,title`` ne
  rend que ces clés par tâche (488 Ko → quelques octets par carte), et un champ
  inconnu est un 400, jamais une clé silencieusement absente.

``evidence``, ``capacity`` et ``fleet/live`` rejoignent aussi le cache : ils
ralentissaient jusqu'à 7,6 s pendant une lecture lourde.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.data import web_path
from grimoire.tools import workspace_api, workspace_portfolio, workspace_read_cache
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.workspace_read_cache import cacheable_read
from grimoire.tools.workspace_routes import LIVE_ROUTES, PREFIX, workspace_get

from ._cockpit_http import get as http_get

TASKS = f"{PREFIX}tasks"
EVIDENCE = f"{PREFIX}evidence"
CAPACITY = f"{PREFIX}capacity"
FLEET = f"{PREFIX}fleet/live"


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """Le cache est global au processus : chaque test part d'un vol neuf."""
    workspace_read_cache.clear()
    yield
    workspace_read_cache.clear()


# ── Vol unique ──────────────────────────────────────────────────────────────


def test_deux_fils_partagent_un_seul_calcul(tmp_path: Path) -> None:
    """Défaut empêché : deux onglets au même pouls paient chacun la même lecture lente."""
    started = threading.Event()
    release = threading.Event()
    computed: list[int] = []

    def slow() -> str:
        computed.append(1)
        started.set()
        assert release.wait(5), "le calcul n'a jamais été libéré"
        return "réponse"

    results: list[str] = []

    def ask() -> None:
        results.append(workspace_read_cache.cached_read("r", tmp_path, {}, slow, lambda: "même"))

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(5), "le calcul n'a jamais commencé"
    # Les autres fils ont eu le temps d'arriver pendant que le calcul est en vol.
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive(), "un appelant est resté bloqué après le vol"

    assert computed == [1], "la lecture lente doit être calculée une fois, pas une par appelant"
    assert results == ["réponse"] * 4, "tous les appelants lisent la réponse du même vol"


def test_une_erreur_est_rendue_aux_deux_appelants_et_n_est_pas_gardee(tmp_path: Path) -> None:
    """Défaut empêché : le second onglet recalcule (et paie) une lecture qui vient d'échouer.

    L'erreur du vol est la même pour tous ceux qui y ont participé, puis le vol
    disparaît : le prochain appel rouvre un calcul au lieu de resservir l'échec.
    """
    started = threading.Event()
    release = threading.Event()
    computed: list[int] = []

    def boom() -> Any:
        computed.append(1)
        started.set()
        assert release.wait(5), "le calcul n'a jamais été libéré"
        raise RuntimeError("panne")

    errors: list[BaseException] = []

    def ask() -> None:
        try:
            workspace_read_cache.cached_read("r", tmp_path, {}, boom, lambda: "même")
        except RuntimeError as exc:  # le type compte : c'est l'erreur du calcul, pas un blocage
            errors.append(exc)

    leader = threading.Thread(target=ask)
    leader.start()
    assert started.wait(5), "le calcul n'a jamais commencé"
    follower = threading.Thread(target=ask)
    follower.start()
    follower.join(0.5)
    assert follower.is_alive(), "le second appelant doit attendre le vol, pas ouvrir un second calcul"
    release.set()
    for thread in (leader, follower):
        thread.join(5)
        assert not thread.is_alive()

    assert computed == [1], "un seul calcul en vol, même quand il échoue"
    assert len(errors) == 2 and errors[0] is errors[1], "les deux appelants reçoivent la même erreur"

    # Pas gardée : le prochain appel recalcule (et échoue de nouveau).
    with pytest.raises(RuntimeError, match="panne"):
        workspace_read_cache.cached_read("r", tmp_path, {}, boom, lambda: "même")
    assert computed == [1, 1]


def test_les_lectures_lourdes_du_rail_rejoignent_le_cache() -> None:
    """Défaut empêché : ``evidence``, ``capacity`` et ``fleet/live`` recalculés à chaque onglet."""
    assert {EVIDENCE, CAPACITY, FLEET} <= LIVE_ROUTES
    for route in (EVIDENCE, CAPACITY, FLEET):
        assert cacheable_read(route, {}, live_routes=LIVE_ROUTES), route


def test_la_flotte_tous_projets_reste_hors_cache() -> None:
    """``fleet/live?project=*`` agrège tout le registre sous l'empreinte d'un seul projet.

    Même piège que le portefeuille : un autre projet qui bouge ne changerait
    pas l'empreinte du projet servi, et la vue resterait figée.
    """
    assert not cacheable_read(FLEET, {"project": ["*"]}, live_routes=LIVE_ROUTES)
    assert cacheable_read(FLEET, {"project": ["projet-a"]}, live_routes=LIVE_ROUTES)


# ── Liste légère pour la palette ────────────────────────────────────────────


@pytest.fixture
def atelier(real_project: Path) -> Iterator[int]:
    """``grimoire serve`` sur le projet réel, comme les tests de transport."""
    api = ForgeAPI(real_project, Path(__file__).resolve().parents[2], web_path())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_fields_borne_chaque_tache_aux_cles_demandees(atelier: int) -> None:
    """Défaut empêché : la palette télécharge 488 Ko de tâches pour lire ``id`` et ``title``."""
    code, payload = http_get(atelier, f"{TASKS}?fields=id,title")

    assert code == 200
    assert payload["tasks"], "le projet réel doit porter au moins une tâche"
    for card in payload["tasks"]:
        assert set(card) == {"id", "title"}, card
    # L'enveloppe ne change pas : les autres appelants gardent le même contrat.
    assert {"columns", "states", "priorities", "ledger", "count"} <= set(payload)


def test_un_champ_inconnu_dans_fields_est_un_400(atelier: int) -> None:
    """Défaut empêché : filtrer silencieusement ferait croire que la tâche n'a pas le champ."""
    code, payload = http_get(atelier, f"{TASKS}?fields=id,inconnu")

    assert code == 400
    assert "inconnu" in str(payload)


def test_fields_de_base_ne_paie_pas_la_couche_d_annotations(
    real_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : borner la réponse sans éviter le calcul des gates et des coûts."""
    called: list[int] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        called.append(1)

    monkeypatch.setattr(workspace_portfolio, "annotate_cards", spy)

    payload = workspace_api.tasks_view(real_project, fields=["id", "title"])

    assert payload["tasks"]
    assert called == [], "`fields=id,title` ne doit pas payer les annotations"
    assert workspace_get(real_project, TASKS, {"fields": ["id,title"]})["tasks"] == payload["tasks"]


def test_un_champ_d_annotation_demande_paie_la_couche_qui_le_produit(
    real_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inverse du test précédent : ``fields=session`` ne peut pas rendre un champ jamais calculé."""
    called: list[int] = []
    real = workspace_portfolio.annotate_cards

    def spy(*args: Any, **kwargs: Any) -> None:
        called.append(1)
        real(*args, **kwargs)

    monkeypatch.setattr(workspace_portfolio, "annotate_cards", spy)

    payload = workspace_api.tasks_view(real_project, fields=["id", "session"])

    assert called, "un champ d'annotation demandé force la couche d'annotations"
    assert payload["tasks"]
    for card in payload["tasks"]:
        assert set(card) == {"id", "session"}
