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


class TestCompile:
    def test_compiles_ready_blueprint(
        self, api_with_catalogue: ForgeAPI, project_root: Path
    ) -> None:
        bp = {
            "blueprintVersion": 1,
            "id": "flow-ok",
            "name": "Flow OK",
            "catalogRef": {"version": "1.0.0"},
            "nodes": [make_node("a", "ORC-01"), make_node("b", "QUA-04")],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        result = api_with_catalogue.blueprint_compile(bp)
        artifact = project_root / result["artifact"]
        assert artifact.is_file()
        content = artifact.read_text(encoding="utf-8")
        assert "Plan d'exécution" in content
        assert content.index("1. ORC-01") < content.index("2. QUA-04")
        assert "Contrats aux frontières" in content
        # Section compiled persistée avec hash cohérent
        saved = api_with_catalogue.blueprint_get("flow-ok")
        compiled = saved["compiled"]["artifacts"][0]
        assert compiled["path"] == result["artifact"]
        assert compiled["hash"] == result["hash"]
        import hashlib
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert compiled["hash"] == f"sha256:{digest}"

    def test_blocked_blueprint_refuses_compilation(self, api: ForgeAPI) -> None:
        bp = {
            "id": "flow-ko",
            "nodes": [
                {
                    "id": "x", "kind": "extension-node", "ref": "crewai/crewai-crew",
                    "label": "Crew",
                    "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
                }
            ],
            "edges": [],
        }
        with pytest.raises(ValueError, match="compilation refusée"):
            api.blueprint_compile(bp)

    def test_pattern_requirements_in_mission_pack(
        self, api_with_catalogue: ForgeAPI, project_root: Path
    ) -> None:
        # Enrichir le catalogue de contrôles pour GOV-01
        ui = api_with_catalogue.ui_dir
        assert ui is not None
        catalogue = json.loads((ui / "data" / "catalogue-export.json").read_text())
        catalogue["patterns"][1]["controls"] = ["allowlist", "dry-run"]
        (ui / "data" / "catalogue-export.json").write_text(json.dumps(catalogue))
        bp = {
            "id": "flow-req",
            "name": "Req",
            "catalogRef": {"version": "1.0.0"},
            "nodes": [make_node("g", "GOV-01"), make_node("q", "QUA-04")],
            "edges": [{"from": "g.out", "to": "q.in", "contract": "task-envelope"}],
        }
        result = api_with_catalogue.blueprint_compile(bp)
        content = (project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "allowlist, dry-run" in content


class TestStudioBridge:
    """Pont Studio (v2) → format compilable (v1)."""

    @staticmethod
    def studio_state() -> dict[str, object]:
        return {
            "blueprintVersion": 2,
            "id": "flow-studio",
            "name": "Flow studio",
            "nodes": [
                {"id": "n1", "ref": "ORC-02", "x": 100, "y": 200},
                {"id": "n2", "ref": "ORC-01", "x": 400, "y": 200},
                {
                    "id": "g1",
                    "kind": "group",
                    "name": "exécution",
                    "x": 700,
                    "y": 200,
                    "sub": {
                        "nodes": [{"id": "n3", "ref": "QUA-04", "x": 100, "y": 100}],
                        "edges": [],
                        "comments": [],
                    },
                },
            ],
            "edges": [
                {"id": "e1", "from": "n1", "to": "n2", "contract": "task-envelope"},
                {"id": "e2", "from": "n2", "to": "g1", "contract": "handoff-packet"},
            ],
            "comments": [],
            "view": None,
            "meta": {"validated": False, "simulated": False, "compiledAt": None},
        }

    def test_detects_studio_format(self, api: ForgeAPI) -> None:
        assert api._is_studio(self.studio_state())
        v1 = {
            "nodes": [{"id": "a", "kind": "pattern", "ref": "ORC-01", "pins": []}],
            "edges": [],
        }
        assert not api._is_studio(v1)

    def test_conversion_flattens_groups_and_derives_pins(self, api: ForgeAPI) -> None:
        v1 = api._studio_to_v1(self.studio_state())
        kinds = {n["id"]: n["kind"] for n in v1["nodes"]}
        assert kinds == {
            "n1": "pattern",
            "n2": "pattern",
            "g1": "composite-inline",
            "n3": "pattern",
        }
        n1 = next(n for n in v1["nodes"] if n["id"] == "n1")
        out_contracts = {p["contract"] for p in n1["pins"] if p["direction"] == "out"}
        assert "task-envelope" in out_contracts
        assert v1["edges"][0]["from"] == "n1.out-task-envelope"
        assert v1["edges"][0]["to"] == "n2.in-task-envelope"

    def test_studio_lint_has_no_structural_errors(self, api: ForgeAPI) -> None:
        lint = api.blueprint_lint(self.studio_state())
        assert lint["errors"] == []

    def test_studio_simulate_orders_steps(self, api: ForgeAPI) -> None:
        report = api.blueprint_simulate(self.studio_state())
        assert report["blockers"] == []
        order = [s["id"] for s in report["steps"]]
        assert order.index("n1") < order.index("n2") < order.index("g1")

    def test_studio_compile_persists_v2_source(self, api: ForgeAPI) -> None:
        state = self.studio_state()
        result = api.blueprint_compile(state)
        assert result["compiled"] == "flow-studio"
        artifact = api.project_root / result["artifact"]
        assert artifact.is_file()
        saved = json.loads(
            (api.project_root / "_grimoire" / "blueprints" / "flow-studio.blueprint.json")
            .read_text(encoding="utf-8")
        )
        assert saved["blueprintVersion"] == 2
        assert saved["compiled"]["artifacts"][0]["path"] == result["artifact"]
        assert saved["nodes"][2]["kind"] == "group"


def with_context(node: dict, context: dict) -> dict:
    return {**node, "config": {"context": context}}


class TestContextPolicy:
    """Ingénierie de contexte (tranche C1) : validation, lint, simulation, compile."""

    # ── forme + R-C4 (bloquants) ──

    def test_invalid_enum_is_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(make_node("a", "ORC-01"),
                                   {"budget": {"tier": "gigantic"}})],
            "edges": [],
        }
        errors = api.blueprint_validate(bp)
        assert any("budget.tier invalide" in e for e in errors)

    def test_invalid_types_are_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [
                with_context(make_node("a", "ORC-01"),
                             {"budget": {"maxTokens": "beaucoup"},
                              "compaction": {"strategy": "zip"},
                              "isolation": "airgap"}),
            ],
            "edges": [],
        }
        errors = api.blueprint_validate(bp)
        assert any("maxTokens invalide" in e for e in errors)
        assert any("compaction.strategy invalide" in e for e in errors)
        assert any("isolation invalide" in e for e in errors)

    def test_unknown_context_key_is_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(make_node("a", "ORC-01"), {"cache": True})],
            "edges": [],
        }
        errors = api.blueprint_validate(bp)
        assert any("clés inconnues" in e for e in errors)

    def test_r_c4_max_tokens_over_window_is_blocking(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(make_node("a", "ORC-01"),
                                   {"budget": {"maxTokens": 300000}})],
            "edges": [],
        }
        errors = api.blueprint_validate(bp)
        assert any("R-C4" in e for e in errors)

    def test_valid_context_passes(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(
                make_node("a", "ORC-01"),
                {"budget": {"tier": "small", "maxTokens": 12000},
                 "compaction": {"strategy": "digest",
                                "digestContract": "context-pack"},
                 "isolation": "shared"})],
            "edges": [],
        }
        assert api.blueprint_validate(bp) == []

    # ── R-C5 (bloquant) : node isolé à sortie non-digest ──

    def test_r_c5_isolated_with_non_digest_output_is_blocking(
        self, api: ForgeAPI
    ) -> None:
        bp = {
            "nodes": [
                with_context(make_node("a", "ORC-01"), {"isolation": "isolated"}),
                make_node("b", "GOV-01"),
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        errors = api.blueprint_validate(bp)
        assert any("R-C5" in e for e in errors)

    def test_r_c5_isolated_with_digest_output_passes(self, api: ForgeAPI) -> None:
        node_a = {
            "id": "a", "kind": "pattern", "ref": "ORC-01", "label": "ORC-01",
            "config": {"context": {"isolation": "isolated"}},
            "pins": [{"id": "out", "direction": "out", "contract": "handoff-packet"}],
        }
        node_b = {
            "id": "b", "kind": "pattern", "ref": "GOV-01", "label": "GOV-01",
            "pins": [{"id": "in", "direction": "in", "contract": "handoff-packet"}],
        }
        bp = {
            "nodes": [node_a, node_b],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "handoff-packet"}],
        }
        assert not [e for e in api.blueprint_validate(bp) if "R-C5" in e]

    def test_r_c5_blocks_compilation_from_studio(self, api: ForgeAPI) -> None:
        bp = {
            "blueprintVersion": 2,
            "id": "flow-iso",
            "nodes": [
                {"id": "n1", "ref": "ORC-01", "x": 0, "y": 0,
                 "config": {"context": {"isolation": "isolated"}}},
                {"id": "n2", "ref": "GOV-01", "x": 300, "y": 0},
            ],
            "edges": [{"id": "e1", "from": "n1", "to": "n2",
                       "contract": "task-envelope"}],
        }
        with pytest.raises(ValueError, match="R-C5"):
            api.blueprint_compile(bp)

    # ── R-C1 / R-C2 / R-C3 (avertissements) ──

    def test_r_c1_extension_feeding_shared_node_warns(self, api: ForgeAPI) -> None:
        ext = {
            "id": "x", "kind": "extension-node", "ref": "demo-ext/crew",
            "label": "crew",
            "pins": [{"id": "out", "direction": "out", "contract": "handoff-packet"}],
        }
        dst = {
            "id": "b", "kind": "pattern", "ref": "GOV-01", "label": "GOV-01",
            "pins": [{"id": "in", "direction": "in", "contract": "handoff-packet"}],
        }
        bp = {
            "blueprintVersion": 1,
            "nodes": [ext, dst],
            "edges": [{"from": "x.out", "to": "b.in", "contract": "handoff-packet"}],
        }
        lint = api.blueprint_lint(bp)
        assert any("R-C1" in w for w in lint["warnings"])
        # Le node aval isolé quarantine le contenu externe : plus de R-C1
        bp["nodes"][1] = with_context(dst, {"isolation": "isolated"})
        lint = api.blueprint_lint(bp)
        assert not any("R-C1" in w for w in lint["warnings"])

    def _chain(self, n: int, digest_on: str | None = None) -> dict:
        nodes, edges = [], []
        for i in range(n):
            node = make_node(f"n{i}", "ORC-01")
            if digest_on == f"n{i}":
                node = with_context(node, {"compaction": {"strategy": "digest"}})
            nodes.append(node)
            if i:
                edges.append({"from": f"n{i - 1}.out", "to": f"n{i}.in",
                              "contract": "task-envelope"})
        return {"blueprintVersion": 1, "nodes": nodes, "edges": edges}

    def test_r_c2_chain_of_four_without_digest_warns(self, api: ForgeAPI) -> None:
        lint = api.blueprint_lint(self._chain(4))
        assert any("R-C2" in w for w in lint["warnings"])

    def test_r_c2_digest_in_chain_silences(self, api: ForgeAPI) -> None:
        lint = api.blueprint_lint(self._chain(4, digest_on="n1"))
        assert not any("R-C2" in w for w in lint["warnings"])

    def test_r_c2_short_chain_is_silent(self, api: ForgeAPI) -> None:
        lint = api.blueprint_lint(self._chain(3))
        assert not any("R-C2" in w for w in lint["warnings"])

    def test_r_c3_deep_without_justification_warns(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(make_node("a", "ORC-01"),
                                   {"budget": {"tier": "deep"}})],
            "edges": [],
        }
        lint = api.blueprint_lint(bp)
        assert any("R-C3" in w for w in lint["warnings"])
        bp["nodes"][0]["config"]["context"]["budget"]["justification"] = (
            "audit multi-fichiers"
        )
        lint = api.blueprint_lint(bp)
        assert not any("R-C3" in w for w in lint["warnings"])

    # ── pression de contexte en simulation ──

    def test_context_pressure_full_accumulates(self, api: ForgeAPI) -> None:
        report = api.blueprint_simulate(self._chain(3))
        pressure = {p["nodeId"]: p for p in report["contextPressure"]}
        assert pressure["n0"]["estimatedTokens"] == 8000
        assert pressure["n1"]["estimatedTokens"] == 16000
        assert pressure["n2"]["estimatedTokens"] == 24000
        assert all(p["verdict"] == "ok" for p in report["contextPressure"])

    def test_context_pressure_digest_reduces_carry(self, api: ForgeAPI) -> None:
        report = api.blueprint_simulate(self._chain(3, digest_on="n0"))
        pressure = {p["nodeId"]: p for p in report["contextPressure"]}
        assert pressure["n1"]["estimatedTokens"] == 10000  # 8000 + digest 2000
        assert pressure["n2"]["estimatedTokens"] == 18000

    def test_context_pressure_isolated_resets_carry(self, api: ForgeAPI) -> None:
        bp = self._chain(2)
        bp["nodes"][0] = with_context(bp["nodes"][0], {"isolation": "isolated"})
        # sortie digest pour respecter R-C5
        bp["nodes"][0]["pins"] = [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": "handoff-packet"},
        ]
        bp["nodes"][1]["pins"][0]["contract"] = "handoff-packet"
        bp["edges"][0]["contract"] = "handoff-packet"
        report = api.blueprint_simulate(bp)
        pressure = {p["nodeId"]: p for p in report["contextPressure"]}
        assert pressure["n1"]["estimatedTokens"] == 8000

    def test_context_pressure_critical_and_r_c6(self, api: ForgeAPI) -> None:
        bp = {
            "nodes": [with_context(make_node("a", "ORC-01"),
                                   {"budget": {"maxTokens": 190000}})],
            "edges": [],
        }
        report = api.blueprint_simulate(bp)
        pressure = report["contextPressure"][0]
        assert pressure["estimatedTokens"] == 190000
        assert pressure["verdict"] == "critical"
        assert any("R-C6" in w for w in report["warnings"])

    def test_no_context_behaves_as_before(self, api: ForgeAPI) -> None:
        report = api.blueprint_simulate(self._chain(2))
        assert report["verdict"] == "prêt à appliquer"
        assert [p["verdict"] for p in report["contextPressure"]] == ["ok", "ok"]
        lint = api.blueprint_lint(self._chain(2))
        assert not any(w.startswith("R-C") for w in lint["warnings"])

    # ── compilation : section « Contexte » ──

    def test_compile_emits_context_section(
        self, api_with_catalogue: ForgeAPI, project_root: Path
    ) -> None:
        node_a = with_context(
            make_node("a", "ORC-01", out_contract="handoff-packet"),
            {"budget": {"tier": "deep", "maxTokens": 12000,
                        "justification": "analyse transverse"},
             "compaction": {"strategy": "digest"},
             "isolation": "isolated"},
        )
        node_b = {
            "id": "b", "kind": "pattern", "ref": "QUA-04", "label": "QUA-04",
            "config": {"context": {"compaction": {"strategy": "selective"}}},
            "pins": [{"id": "in", "direction": "in", "contract": "handoff-packet"}],
        }
        bp = {
            "blueprintVersion": 1,
            "id": "flow-ctx",
            "name": "Flow contexte",
            "catalogRef": {"version": "1.0.0"},
            "nodes": [node_a, node_b],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "handoff-packet"}],
        }
        result = api_with_catalogue.blueprint_compile(bp)
        content = (project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "#### Contexte" in content
        assert "tier `deep`, plafond 12000 tokens" in content
        assert "analyse transverse" in content
        assert "`handoff-packet` (ORC-03)" in content
        assert "SELECTIVE_LOAD" in content
        assert "sous-agent à capsule minimale" in content

    def test_compile_without_context_has_no_section(
        self, api_with_catalogue: ForgeAPI, project_root: Path
    ) -> None:
        bp = {
            "id": "flow-noctx",
            "name": "Sans contexte",
            "catalogRef": {"version": "1.0.0"},
            "nodes": [make_node("a", "ORC-01"), make_node("b", "QUA-04")],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "task-envelope"}],
        }
        result = api_with_catalogue.blueprint_compile(bp)
        content = (project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "#### Contexte" not in content

    def test_studio_projection_carries_config(self, api: ForgeAPI) -> None:
        bp = {
            "blueprintVersion": 2,
            "id": "flow-cfg",
            "nodes": [{"id": "n1", "ref": "ORC-01", "x": 0, "y": 0,
                       "config": {"context": {"budget": {"tier": "small"}}}}],
            "edges": [],
        }
        v1 = api._studio_to_v1(bp)
        assert v1["nodes"][0]["config"] == {"context": {"budget": {"tier": "small"}}}


class TestStigmergyView:
    def test_empty_board(self, api: ForgeAPI) -> None:
        view = api.stigmergy_view()
        assert view["active"] == []
        assert view["trails"] == []
        assert view["stats"]["active"] == 0

    def test_active_signals_and_convergence(self, api: ForgeAPI) -> None:
        from grimoire.tools import stigmergy as stig

        board = stig.load_board(api.project_root)
        stig.emit_pheromone(board, ptype="NEED", location="src/auth",
                            text="review", emitter="dev")
        stig.emit_pheromone(board, ptype="ALERT", location="src/auth",
                            text="faille", emitter="qa")
        stig.save_board(api.project_root, board)

        view = api.stigmergy_view()
        assert view["stats"]["active"] == 2
        assert {s["type"] for s in view["active"]} == {"NEED", "ALERT"}
        assert any(t["type"] == "convergence" for t in view["trails"])
        assert view["stats"]["byType"]["NEED"] == 1
