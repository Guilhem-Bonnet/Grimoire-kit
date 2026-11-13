"""GAO-cockpit-les-001 : les lectures qui dépendent de l'instant ne sont jamais servies vieilles de plus de 3 s.

Constat de la relecture de la PR #1221 : ``fleet/live``, ``capacity`` et ``evidence`` sont entrés dans le cache
de lecture sous l'empreinte du pouls, or cette empreinte couvre le ledger, les flows, les packs et les reçus —
pas ce que lisent ces routes : ``fleet/live`` lit les journaux de session de l'hôte et la sonde des agents
vivants, ``capacity`` la sonde de l'hôte et l'heure, ``evidence`` le board et les gates. Leurs changements
pouvaient donc rester servis jusqu'à ``MAX_AGE_SECONDS`` (15 s) : une session morte apparaissait vivante.

Le cache borne maintenant chaque route (:func:`max_age_seconds`) : ``FRESH_MAX_AGE_SECONDS`` (3 s) pour ces
trois lectures, ``MAX_AGE_SECONDS`` pour les autres. Pour ``evidence``, le choix retenu est l'âge court plutôt
que l'ajout du board et des gates à l'empreinte du pouls : cette empreinte est partagée par toutes les routes,
et 3 s suffisent à borner une lecture dérivée de fichiers relus par ``grimoire standard verify``.

Chaque test décrit le défaut qu'il empêche, et injecte son horloge : aucun ne dépend du temps réel.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from grimoire.tools import workspace_read_cache
from grimoire.tools.workspace_read_cache import (
    FRESH_MAX_AGE_SECONDS,
    MAX_AGE_SECONDS,
    cached_read,
    max_age_seconds,
)
from grimoire.tools.workspace_routes import LIVE_ROUTES, PREFIX

FLEET = f"{PREFIX}fleet/live"
CAPACITY = f"{PREFIX}capacity"
EVIDENCE = f"{PREFIX}evidence"
TASKS = f"{PREFIX}tasks"


class _Clock:
    """Une horloge monotone contrôlée par le test : aucune attente réelle."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """Le cache est global au processus : chaque test part d'un vol neuf."""
    workspace_read_cache.clear()
    yield
    workspace_read_cache.clear()


def _counted(calls: list[int], value: str) -> Callable[[], str]:
    def compute() -> str:
        calls.append(1)
        return value

    return compute


def test_la_flotte_est_servie_du_cache_a_deux_secondes(tmp_path: Path) -> None:
    """Défaut empêché : deux lectures rapprochées de la Flotte en direct paient deux fois la lecture de l'hôte."""
    clock = _Clock()
    calls: list[int] = []
    compute = _counted(calls, "flotte")

    assert cached_read(FLEET, tmp_path, {}, compute, lambda: "même", clock=clock) == "flotte"
    clock.advance(2.0)
    assert cached_read(FLEET, tmp_path, {}, compute, lambda: "même", clock=clock) == "flotte"

    assert calls == [1], "à 2 s (< 3 s), la réponse gardée est resservie"


def test_la_flotte_est_recalculee_a_quatre_secondes(tmp_path: Path) -> None:
    """Défaut empêché : une session morte reste affichée vivante jusqu'à 15 s, l'empreinte du pouls immobile."""
    clock = _Clock()
    calls: list[int] = []
    first = _counted(calls, "vivante")

    assert cached_read(FLEET, tmp_path, {}, first, lambda: "même", clock=clock) == "vivante"
    clock.advance(4.0)
    second = _counted(calls, "morte")
    assert cached_read(FLEET, tmp_path, {}, second, lambda: "même", clock=clock) == "morte"

    assert calls == [1, 1], "à 4 s (> 3 s), la Flotte en direct est recalculée"


@pytest.mark.parametrize("route", [CAPACITY, EVIDENCE])
def test_capacity_et_evidence_suivent_le_meme_age_court(route: str, tmp_path: Path) -> None:
    """Défaut empêché : la sonde de l'hôte (capacity) et le board/gates (evidence) servis 15 s.

    Pour ``evidence``, c'est le choix consigné dans la PR : l'âge court, pas l'empreinte élargie au board.
    """
    clock = _Clock()
    calls: list[int] = []
    compute = _counted(calls, "réponse")

    cached_read(route, tmp_path, {}, compute, lambda: "même", clock=clock)
    clock.advance(4.0)
    cached_read(route, tmp_path, {}, compute, lambda: "même", clock=clock)

    assert calls == [1, 1], f"{route} doit être recalculée après 3 s"
    assert max_age_seconds(route) == FRESH_MAX_AGE_SECONDS


def test_une_route_a_age_long_n_est_pas_recalculee_a_quatre_secondes(tmp_path: Path) -> None:
    """Défaut empêché : l'âge court appliqué à toutes les routes ferait payer la liste des tâches sans raison."""
    clock = _Clock()
    calls: list[int] = []
    compute = _counted(calls, "tâches")

    cached_read(TASKS, tmp_path, {}, compute, lambda: "même", clock=clock)
    clock.advance(4.0)
    assert cached_read(TASKS, tmp_path, {}, compute, lambda: "même", clock=clock) == "tâches"

    assert calls == [1], "une route à âge long reste fraîche à 4 s"
    assert max_age_seconds(TASKS) == MAX_AGE_SECONDS
    assert TASKS in LIVE_ROUTES


def test_deux_lectures_simultanees_de_la_flotte_font_un_seul_calcul(tmp_path: Path) -> None:
    """Défaut empêché : deux onglets sur la Flotte en direct lancent deux lectures de l'hôte au même instant."""
    clock = _Clock()
    started = threading.Event()
    release = threading.Event()
    computed: list[int] = []

    def slow() -> str:
        computed.append(1)
        started.set()
        assert release.wait(5), "le calcul n'a jamais été libéré"
        return "flotte"

    results: list[str] = []

    def ask() -> None:
        results.append(cached_read(FLEET, tmp_path, {}, slow, lambda: "même", clock=clock))

    leader = threading.Thread(target=ask)
    leader.start()
    assert started.wait(5), "le calcul n'a jamais commencé"
    follower = threading.Thread(target=ask)
    follower.start()
    follower.join(0.05)
    assert follower.is_alive(), "la seconde lecture doit attendre le vol, pas recalculer"
    release.set()
    for thread in (leader, follower):
        thread.join(5)
        assert not thread.is_alive(), "un appelant est resté bloqué après le vol"

    assert computed == [1], "un seul calcul pour deux lectures simultanées de la Flotte"
    assert results == ["flotte", "flotte"]
