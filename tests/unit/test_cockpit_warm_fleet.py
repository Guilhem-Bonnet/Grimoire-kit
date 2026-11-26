"""La chauffe de la Flotte : bornée en temps, annulable, jamais deux à la fois, et rien d'autre que les lectures de la page.

Critère de coût : le nombre de lectures de route faites (``workspace_get``), jamais une durée. Tout fil lancé est joint
avec un délai court.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.cli import cockpit_http, cockpit_warm
from grimoire.tools import workspace_routes

if TYPE_CHECKING:
    from pathlib import Path

JOIN_S = 5.0
FLEET = "/api/workspace/fleet/live"


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, list[str]]]]:
    """Les lectures de route demandées : la chauffe passe par la route de la page, donc par les mêmes caches."""
    seen: list[tuple[str, dict[str, list[str]]]] = []

    def get(root: Path, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        seen.append((path, query))
        return {}

    monkeypatch.setattr(workspace_routes, "workspace_get", get)
    return seen


def test_warms_the_current_project_then_the_global_view(tmp_path: Path, reads: list[Any]) -> None:
    assert cockpit_warm.warm_fleet(tmp_path) == 2
    assert reads == [(FLEET, {}), (FLEET, {"project": ["*"]})]


def test_stop_already_set_reads_nothing(tmp_path: Path, reads: list[Any]) -> None:
    stop = threading.Event()
    stop.set()
    assert cockpit_warm.warm_fleet(tmp_path, stop) == 0
    assert reads == []


def test_stop_during_the_first_view_skips_the_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    seen: list[dict[str, list[str]]] = []

    def get(root: Path, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        seen.append(query)
        stop.set()  # le serveur s'arrête pendant la première vue : elle se termine, la suivante ne commence pas
        return {}

    monkeypatch.setattr(workspace_routes, "workspace_get", get)
    assert cockpit_warm.warm_fleet(tmp_path, stop) == 1
    assert seen == [{}]


def test_budget_spent_after_the_first_view_skips_the_second(tmp_path: Path, reads: list[Any]) -> None:
    ticks = iter([0.0, 0.0, 61.0, 61.0])  # début, 1re garde, 2e garde : le budget de 60 s est dépassé
    assert cockpit_warm.warm_fleet(tmp_path, budget_s=60.0, clock=lambda: next(ticks)) == 1
    assert reads == [(FLEET, {})]


def test_zero_budget_reads_nothing(tmp_path: Path, reads: list[Any]) -> None:
    assert cockpit_warm.warm_fleet(tmp_path, budget_s=0.0) == 0
    assert reads == []


def test_one_warm_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inside = threading.Event()
    release = threading.Event()
    seen: list[dict[str, list[str]]] = []

    def get(root: Path, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        seen.append(query)
        inside.set()
        release.wait(JOIN_S)
        return {}

    monkeypatch.setattr(workspace_routes, "workspace_get", get)
    first = threading.Thread(target=cockpit_warm.warm_fleet, args=(tmp_path,))
    first.start()
    try:
        assert inside.wait(JOIN_S)
        assert cockpit_warm.warm_fleet(tmp_path) == 0  # une chauffe est en cours : pas de seconde
    finally:
        release.set()
        first.join(JOIN_S)
    assert not first.is_alive()
    assert seen == [{}, {"project": ["*"]}]  # la seconde n'a rien lu de plus
    assert cockpit_warm.warm_fleet(tmp_path) == 2  # le verrou est rendu : une nouvelle chauffe passe


def test_a_failed_view_costs_only_that_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, list[str]]] = []

    def get(root: Path, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        seen.append(query)
        if not query:
            raise RuntimeError("registre illisible")
        return {}

    monkeypatch.setattr(workspace_routes, "workspace_get", get)
    assert cockpit_warm.warm_fleet(tmp_path) == 1
    assert seen == [{}, {"project": ["*"]}]
    assert cockpit_warm.warm_fleet(tmp_path) == 1  # le verrou a été rendu malgré l'échec


def test_warm_sheet_ends_with_the_fleet_even_if_an_earlier_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.tools import workspace_api

    order: list[str] = []

    def boom(root: Path) -> None:
        order.append("sheet")
        raise RuntimeError("fiche illisible")

    monkeypatch.setattr(workspace_api, "sheet_view", boom)
    monkeypatch.setattr(workspace_api, "tasks_view", lambda root: order.append("tasks"))
    monkeypatch.setattr(cockpit_warm, "warm_fleet", lambda root, stop=None: order.append("fleet"))
    thread = threading.Thread(target=cockpit_http.warm_sheet, args=(tmp_path,))
    thread.start()
    thread.join(JOIN_S)
    assert not thread.is_alive()
    assert order == ["sheet", "tasks", "fleet"]  # une étape en échec n'empêche pas la Flotte
