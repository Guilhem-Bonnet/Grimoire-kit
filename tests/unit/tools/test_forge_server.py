"""Tests for grimoire.tools.forge_server — API du mode local."""

from __future__ import annotations

import json
import threading
import urllib.error
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


def make_node(node_id: str, ref: str, out_contract: str = "task-envelope") -> dict:
    return {
        "id": node_id,
        "kind": "pattern",
        "ref": ref,
        "label": ref,
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": out_contract},
        ],
    }


@pytest.fixture
def api_with_catalogue(
    project_root: Path, kit_root: Path, tmp_path: Path
) -> ForgeAPI:
    ui = tmp_path / "ui"
    (ui / "data").mkdir(parents=True)
    catalogue = {
        "patterns": [
            {"id": "ORC-01", "name": "Orchestrateur"},
            {"id": "GOV-01", "name": "Policy engine"},
            {"id": "QUA-04", "name": "Evidence pack"},
        ],
        "contracts": [{"id": "task-envelope"}, {"id": "handoff-packet"}],
        "relations": [{"from": "ORC-01", "to": "GOV-01", "kind": "depends"}],
        "useCases": [
            {"id": "revue-gouvernee", "name": "Revue gouvernée",
             "patterns": ["GOV-01", "QUA-04"]}
        ],
    }
    (ui / "data" / "catalogue-export.json").write_text(
        json.dumps(catalogue), encoding="utf-8"
    )
    return ForgeAPI(project_root, kit_root, ui_dir=ui)


class TestLint:
    def test_typed_pin_mismatch_is_blocking(self, api: ForgeAPI) -> None:
        blueprint = {
            "nodes": [
                make_node("a", "ORC-01", out_contract="handoff-packet"),
                make_node("b", "GOV-01"),
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "handoff-packet"}],
        }
        errors = api.blueprint_validate(blueprint)
        assert any("contrats incompatibles" in e for e in errors)

    def test_edge_contract_must_match_pins(self, api: ForgeAPI) -> None:
        blueprint = {
            "nodes": [
                make_node("a", "ORC-01"),
                make_node("b", "GOV-01"),
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "handoff-packet"}],
        }
        errors = api.blueprint_validate(blueprint)
        assert any("contrat déclaré" in e for e in errors)

    def test_lint_warns_missing_dependency_and_proof(
        self, api_with_catalogue: ForgeAPI
    ) -> None:
        blueprint = {
            "nodes": [make_node("a", "ORC-01"), make_node("b", "ORC-01")],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        lint = api_with_catalogue.blueprint_lint(blueprint)
        assert lint["errors"] == []
        assert any("dépend de GOV-01" in w for w in lint["warnings"])
        assert any("Faux Done" in w for w in lint["warnings"])

    def test_lint_warns_isolated_node(self, api_with_catalogue: ForgeAPI) -> None:
        blueprint = {
            "nodes": [
                make_node("a", "ORC-01"),
                make_node("b", "GOV-01"),
                make_node("c", "QUA-04"),
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        lint = api_with_catalogue.blueprint_lint(blueprint)
        assert any("node isolé : c" in w for w in lint["warnings"])
        assert not any("Faux Done" in w for w in lint["warnings"])

    def test_events_log(self, api: ForgeAPI, project_root: Path) -> None:
        events = project_root / "_grimoire-runtime-output" / "hook-runtime"
        events.mkdir(parents=True)
        (events / "events.jsonl").write_text(
            '{"hook": "test", "n": 1}\n{"hook": "test", "n": 2}\n', encoding="utf-8"
        )
        log = api.events_log()
        assert [e["n"] for e in log["hook-runtime"]] == [1, 2]


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


@pytest.fixture
def kit_with_extension(kit_root: Path) -> Path:
    ext = kit_root / "extensions" / "demo-ext"
    (ext / "artifacts").mkdir(parents=True)
    (ext / "artifacts" / "demo.agent.md").write_text("# Demo\n", encoding="utf-8")
    manifest = {
        "manifestVersion": 1, "id": "demo-ext", "name": "Demo", "version": "0.1.0",
        "description": "Extension de test.", "license": "MIT",
        "authors": [{"name": "T"}], "compat": {"kit": ">=3.11", "manifest": 1},
        "provides": {"agents": ["artifacts/demo.agent.md"]},
        "patterns": {"implements": ["ORC-01"]},
        "permissions": {"filesystem": "artifacts", "network": False, "hooks": [], "memory": "none"},
        "install": {"steps": [{"kind": "copy", "from": "artifacts/demo.agent.md",
                               "to": ".github/agents/demo.agent.md"}]},
    }
    (ext / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    return kit_root


class TestExtensionsAndPlan:
    def test_extensions_view_lists_available(
        self, project_root: Path, kit_with_extension: Path
    ) -> None:
        api = ForgeAPI(project_root, kit_with_extension, ui_dir=None)
        view = api.extensions_view()
        assert view["available"][0]["id"] == "demo-ext"
        assert view["installed"] == {}

    def test_setup_plan_installs_and_writes_plan(
        self, project_root: Path, kit_with_extension: Path
    ) -> None:
        api = ForgeAPI(project_root, kit_with_extension, ui_dir=None)
        plan = api.setup_plan(
            {"name": "p", "user": "u", "archetype": "minimal", "extensions": ["demo-ext"]}
        )
        assert plan["extensionsInstalled"] == ["demo-ext v0.1.0"]
        assert plan["extensionErrors"] == []
        assert "grimoire-init.sh" in plan["initCommand"]
        assert (project_root / "_grimoire" / "setup-plan.json").is_file()
        api.extension_remove("demo-ext")
        assert api.extensions_view()["installed"] == {}

    def test_setup_plan_reports_extension_errors(
        self, project_root: Path, kit_root: Path
    ) -> None:
        api = ForgeAPI(project_root, kit_root, ui_dir=None)
        plan = api.setup_plan({"extensions": ["ghost"]})
        assert plan["extensionsInstalled"] == []
        assert len(plan["extensionErrors"]) == 1


class TestStatic:
    @pytest.fixture
    def static_url(self, project_root: Path, kit_root: Path, tmp_path: Path):
        ui = tmp_path / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<html>forge</html>", encoding="utf-8")
        server = serve(project_root, kit_root, ui_dir=ui, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()

    def test_serves_index(self, static_url: str) -> None:
        with urllib.request.urlopen(static_url + "/", timeout=5) as resp:  # noqa: S310
            assert b"forge" in resp.read()

    def test_missing_file_404(self, static_url: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(static_url + "/absent.css", timeout=5)  # noqa: S310
        assert exc.value.code == 404

    def test_post_route_extension_add_error(self, static_url: str) -> None:
        req = urllib.request.Request(  # noqa: S310
            static_url + "/api/extensions/add",
            data=json.dumps({"source": "ghost"}).encode(),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert exc.value.code == 422


class TestComposites:
    def composite(self, ref: str) -> dict:
        return {
            "id": "uc",
            "kind": "composite",
            "ref": ref,
            "label": "UC",
            "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
        }

    def test_unknown_use_case_is_blocking(self, api_with_catalogue: ForgeAPI) -> None:
        bp = {"nodes": [self.composite("use-case:ghost")], "edges": []}
        errors = api_with_catalogue.blueprint_validate(bp)
        assert any("use-case inconnu" in e for e in errors)

    def test_invalid_composite_ref_is_blocking(self, api: ForgeAPI) -> None:
        bp = {"nodes": [self.composite("nimporte-quoi")], "edges": []}
        errors = api.blueprint_validate(bp)
        assert any("ref composite invalide" in e for e in errors)

    def test_missing_sub_blueprint_is_blocking(self, api: ForgeAPI) -> None:
        bp = {"nodes": [self.composite("flows/absent.blueprint.json")], "edges": []}
        errors = api.blueprint_validate(bp)
        assert any("sous-blueprint absent" in e for e in errors)

    def test_use_case_patterns_feed_lint(self, api_with_catalogue: ForgeAPI) -> None:
        bp = {
            "nodes": [
                make_node("a", "ORC-01"),
                {**self.composite("use-case:revue-gouvernee"),
                 "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}]},
            ],
            "edges": [{"from": "a.out", "to": "uc.in", "contract": "task-envelope"}],
        }
        lint = api_with_catalogue.blueprint_lint(bp)
        assert lint["errors"] == []
        # Le use-case apporte GOV-01 et QUA-04 : ni dépendance manquante, ni Faux Done
        assert not any("dépend de GOV-01" in w for w in lint["warnings"])
        assert not any("Faux Done" in w for w in lint["warnings"])


class TestSimulate:
    def test_orders_flow_and_reports_requirements(
        self, api_with_catalogue: ForgeAPI
    ) -> None:
        bp = {
            "nodes": [
                make_node("b", "GOV-01"),
                make_node("a", "ORC-01"),
                make_node("c", "QUA-04"),
            ],
            "edges": [
                {"from": "a.out", "to": "b.in", "contract": "task-envelope"},
                {"from": "b.out", "to": "c.in", "contract": "task-envelope"},
            ],
        }
        report = api_with_catalogue.blueprint_simulate(bp)
        assert report["verdict"] == "prêt à appliquer"
        assert [s["id"] for s in report["steps"]] == ["a", "b", "c"]
        assert report["entryNodes"] == ["a"]
        assert report["exitNodes"] == ["c"]
        assert all(s["action"] == "appliquer le pattern" for s in report["steps"])

    def test_cycle_is_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [make_node("a", "ORC-01"), make_node("b", "GOV-01")],
            "edges": [
                {"from": "a.out", "to": "b.in", "contract": "task-envelope"},
                {"from": "b.out", "to": "a.in", "contract": "task-envelope"},
            ],
        }
        report = api.blueprint_simulate(bp)
        assert report["verdict"] == "bloqué"
        assert any("cycle détecté" in b for b in report["blockers"])

    def test_missing_extension_is_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [
                {
                    "id": "x", "kind": "extension-node", "ref": "crewai/crewai-crew",
                    "label": "Crew",
                    "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
                }
            ],
            "edges": [],
        }
        report = api.blueprint_simulate(bp)
        assert report["verdict"] == "bloqué"
        assert any("extension non installée" in b for b in report["blockers"])
        assert report["steps"][0]["ready"] is False

    def test_artifact_readiness(self, api: ForgeAPI, project_root: Path) -> None:
        bp = {
            "nodes": [
                {
                    "id": "a", "kind": "artifact", "ref": ".github/agents/dev.agent.md",
                    "label": "dev",
                    "pins": [{"id": "out", "direction": "out", "contract": "task-envelope"}],
                }
            ],
            "edges": [],
        }
        report = api.blueprint_simulate(bp)
        assert report["steps"][0]["ready"] is True
        assert report["verdict"] == "prêt à appliquer"
