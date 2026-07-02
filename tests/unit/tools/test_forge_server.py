"""Tests for grimoire.tools.forge_server — API du mode local."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from grimoire.tools.forge_server import ForgeAPI, serve


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    (kit / "extensions").mkdir(parents=True)
    (kit / "archetypes" / "minimal").mkdir(parents=True)
    (kit / "archetypes" / "minimal" / "archetype.dna.yaml").write_text(
        'id: minimal\nname: "Minimal"\ndescription: "Base."\ntags: [core]\n',
        encoding="utf-8",
    )
    (kit / "version.txt").write_text("9.9.9\n", encoding="utf-8")
    return kit


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".github" / "agents").mkdir(parents=True)
    (root / ".github" / "agents" / "dev.agent.md").write_text("# dev\n", encoding="utf-8")
    return root


@pytest.fixture
def api(project_root: Path, kit_root: Path) -> ForgeAPI:
    return ForgeAPI(project_root, kit_root, ui_dir=None)


class TestForgeAPI:
    def test_status(self, api: ForgeAPI) -> None:
        status = api.status()
        assert status["kitVersion"] == "9.9.9"
        assert status["ui"] is None

    def test_setup_view_lists_artifacts(self, api: ForgeAPI) -> None:
        view = api.setup_view()
        assert view["artifacts"]["agents"] == [".github/agents/dev.agent.md"]
        assert view["extensions"] == {}

    def test_archetypes(self, api: ForgeAPI) -> None:
        archetypes = api.archetypes()
        assert archetypes == [
            {"id": "minimal", "name": "Minimal", "description": "Base.", "tags": ["core"]}
        ]

    def test_blueprint_roundtrip_and_validate(self, api: ForgeAPI) -> None:
        blueprint = {
            "blueprintVersion": 1,
            "id": "demo",
            "name": "Demo",
            "catalogRef": {"version": "1.0.0"},
            "nodes": [
                {
                    "id": "a",
                    "kind": "artifact",
                    "ref": ".github/agents/dev.agent.md",
                    "label": "Dev",
                    "pins": [{"id": "out", "direction": "out", "contract": "task-envelope"}],
                },
                {
                    "id": "b",
                    "kind": "pattern",
                    "ref": "ORC-01",
                    "label": "Orchestrateur",
                    "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
                },
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        result = api.blueprint_put("demo", blueprint)
        assert result["saved"] == "demo"
        assert api.blueprint_get("demo")["name"] == "Demo"
        assert api.blueprints_list()[0]["nodes"] == 2

    def test_validate_detects_broken_edge_and_missing_artifact(self, api: ForgeAPI) -> None:
        blueprint = {
            "nodes": [
                {"id": "a", "kind": "artifact", "ref": "absent.md", "label": "X", "pins": []}
            ],
            "edges": [{"from": "a.out", "to": "ghost.in", "contract": "c"}],
        }
        errors = api.blueprint_validate(blueprint)
        assert any("edge from inconnu" in e for e in errors)
        assert any("edge to inconnu" in e for e in errors)
        assert any("artefact absent" in e for e in errors)

    def test_blueprint_bad_id_rejected(self, api: ForgeAPI) -> None:
        with pytest.raises(ValueError, match="invalide"):
            api.blueprint_get("../evil")


class TestHTTP:
    @pytest.fixture
    def base_url(self, project_root: Path, kit_root: Path):
        server = serve(project_root, kit_root, ui_dir=None, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()

    def _get(self, url: str) -> dict:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - serveur de test local
            return json.loads(resp.read().decode("utf-8"))

    def test_status_endpoint(self, base_url: str) -> None:
        assert self._get(base_url + "/api/status")["kitVersion"] == "9.9.9"

    def test_setup_endpoint(self, base_url: str) -> None:
        view = self._get(base_url + "/api/setup")
        assert ".github/agents/dev.agent.md" in view["artifacts"]["agents"]

    def test_blueprint_put_then_get(self, base_url: str) -> None:
        payload = json.dumps({"id": "http-demo", "nodes": [], "edges": []}).encode()
        req = urllib.request.Request(  # noqa: S310 - serveur de test local
            base_url + "/api/blueprints/http-demo", data=payload, method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            assert json.loads(resp.read())["saved"] == "http-demo"
        assert self._get(base_url + "/api/blueprints/http-demo")["id"] == "http-demo"

    def test_static_without_ui_returns_hint(self, base_url: str) -> None:
        assert self._get(base_url + "/")["grimoire"] == "serve"
