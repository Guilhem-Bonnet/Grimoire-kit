"""La table de routage des lectures projet — une route absente est un écran vide."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.tools.forge_routes import API_GET_UNHANDLED, api_get


class _StubAPI:
    """Enregistre la méthode appelée plutôt que de faire le travail."""

    def __init__(self) -> None:
        self.called: str | None = None

    def _mark(self, name: str) -> dict[str, Any]:
        self.called = name
        return {"route": name}

    def status(self) -> dict[str, Any]:
        return self._mark("status")

    def setup_view(self) -> dict[str, Any]:
        return self._mark("setup_view")

    def archetypes(self) -> list[dict[str, Any]]:
        return [self._mark("archetypes")]

    def extensions_view(self) -> dict[str, Any]:
        return self._mark("extensions_view")

    def blueprints_list(self) -> list[dict[str, Any]]:
        return [self._mark("blueprints_list")]

    def events_log(self, limit: int = 200) -> dict[str, Any]:
        return self._mark("events_log")

    def stigmergy_view(self) -> dict[str, Any]:
        return self._mark("stigmergy_view")

    def features_view(self) -> list[dict[str, Any]]:
        return [self._mark("features_view")]

    def cost_model_view(self, model: str | None = None) -> dict[str, Any]:
        self.model = model
        return self._mark("cost_model_view")

    def otel_export(self, limit: int = 200) -> dict[str, Any]:
        return self._mark("otel_export")

    def primitives_view(self) -> dict[str, Any]:
        return self._mark("primitives_view")

    def memory_link_view(self) -> dict[str, Any]:
        return self._mark("memory_link_view")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/status", "status"),
        ("/api/setup", "setup_view"),
        ("/api/archetypes", "archetypes"),
        ("/api/extensions", "extensions_view"),
        ("/api/blueprints", "blueprints_list"),
        ("/api/events/log", "events_log"),
        ("/api/stigmergy", "stigmergy_view"),
        ("/api/features", "features_view"),
        ("/api/cost-model", "cost_model_view"),
        ("/api/otel", "otel_export"),
        ("/api/primitives", "primitives_view"),
        ("/api/memory/status", "memory_link_view"),
    ],
)
def test_each_route_reaches_its_method(path: str, method: str) -> None:
    api = _StubAPI()
    assert api_get(api, path, {}) is not API_GET_UNHANDLED
    assert api.called == method


def test_cost_model_forwards_the_query_parameter() -> None:
    api = _StubAPI()
    api_get(api, "/api/cost-model", {"model": ["claude-opus-5"]})
    assert api.model == "claude-opus-5"


def test_backends_needs_no_project() -> None:
    """Catalogue statique : il répond même sans projet servi."""
    payload = api_get(_StubAPI(), "/api/backends", {})
    assert payload is not API_GET_UNHANDLED
    assert any(b["id"] == "auto" for b in payload["backends"])


@pytest.mark.parametrize(
    "path",
    ["/api/nope", "/api/blueprints/x", "/api/blueprints/x/diff", "/index.html", "/api/events"],
)
def test_unknown_and_atelier_only_paths_are_left_to_the_caller(path: str) -> None:
    """Blueprints et SSE restent des routes d'atelier : la table ne les revendique pas."""
    assert api_get(_StubAPI(), path, {}) is API_GET_UNHANDLED
