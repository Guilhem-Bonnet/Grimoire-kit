"""Tests de la famille résilience (P2.2) — failure-edges & config.resilience."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import blueprint_resilience as r
from grimoire.tools.forge_server import ForgeAPI


@pytest.fixture
def api(tmp_path: Path) -> ForgeAPI:
    root = tmp_path / "p"
    root.mkdir()
    return ForgeAPI(root, tmp_path, None)


def _unit(nid: str, out_contract: str = "task-envelope", **over: object) -> dict:
    node: dict = {
        "id": nid,
        "kind": "pattern",
        "ref": "ORC-01",
        "label": nid,
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": out_contract},
        ],
    }
    node.update(over)
    return node


class TestResilienceShape:
    def test_retry_without_max_is_blocking(self) -> None:
        # Preuve SPEC 4 : retry non borné refuse de compiler (R-F1).
        node = _unit("u", config={"resilience": {"retry": {"backoffMs": 100}}})
        errors = r.resilience_shape_errors(node)
        assert any("R-F1" in e for e in errors)

    def test_valid_retry_accepted(self) -> None:
        node = _unit("u", config={"resilience": {
            "retry": {"max": 2, "backoffMs": 500, "strategy": "exponential"},
            "timeoutMs": 120000,
            "onExhaustion": "escalate",
        }})
        assert r.resilience_shape_errors(node) == []

    def test_max_out_of_range(self) -> None:
        node = _unit("u", config={"resilience": {"retry": {"max": 99}}})
        assert any("hors [1,10]" in e for e in r.resilience_shape_errors(node))

    def test_bad_enums(self) -> None:
        node = _unit("u", config={"resilience": {
            "retry": {"max": 1, "strategy": "random"}, "onExhaustion": "boom",
        }})
        errors = r.resilience_shape_errors(node)
        assert any("strategy invalide" in e for e in errors)
        assert any("onExhaustion invalide" in e for e in errors)

    def test_no_policy_no_error(self) -> None:
        assert r.resilience_shape_errors(_unit("u")) == []


class TestResilienceLint:
    def test_r_f2_failure_edge_must_be_error_envelope(self) -> None:
        # Preuve SPEC : edge failure non-error refuse de compiler (R-F2).
        nodes = [_unit("a", out_contract="error-envelope"), _unit("b")]
        edges = [{"from": "a.out", "to": "b.in", "contract": "task-envelope",
                  "channel": "failure"}]
        errors, _ = r.resilience_lint(nodes, edges)
        assert any("R-F2" in e for e in errors)

    def test_r_f2_error_envelope_accepted(self) -> None:
        nodes = [_unit("a", out_contract="error-envelope"), _unit("b")]
        edges = [{"from": "a.out", "to": "b.in", "contract": "error-envelope",
                  "channel": "failure"}]
        errors, _ = r.resilience_lint(nodes, edges)
        assert not any("R-F2" in e for e in errors)

    def test_r_f4_escalation_must_be_terminal(self) -> None:
        nodes = [_unit("a"), _unit("esc"), _unit("back")]
        edges = [
            {"from": "a.out", "to": "esc.in", "contract": "error-envelope",
             "channel": "escalation"},
            {"from": "esc.out", "to": "back.in", "contract": "task-envelope"},
        ]
        errors, _ = r.resilience_lint(nodes, edges)
        assert any("R-F4" in e for e in errors)

    def test_r_f3_external_without_failure_path_warns(self) -> None:
        ext = {"id": "crew", "kind": "extension-node", "ref": "c/r", "pins": []}
        _, warnings = r.resilience_lint([ext], [])
        assert any("R-F3" in w for w in warnings)

    def test_r_f3_silenced_by_resilience_or_failure_edge(self) -> None:
        ext = {"id": "crew", "kind": "extension-node", "ref": "c/r",
               "config": {"resilience": {"retry": {"max": 2}}}, "pins": []}
        _, warnings = r.resilience_lint([ext], [])
        assert not any("R-F3" in w for w in warnings)


class TestResilienceCompileAndReproducible:
    def _resilient_bp(self) -> dict:
        # crew (retry+fallback+escalation) -> evidence ; fallback -> evidence ;
        # escalation -> human.
        crew = {
            "id": "crew", "kind": "pattern", "ref": "COG-01", "label": "crew",
            "config": {"resilience": {
                "retry": {"max": 2, "backoffMs": 1000, "strategy": "exponential"},
                "timeoutMs": 120000, "onExhaustion": "escalate",
            }},
            "pins": [
                {"id": "in", "direction": "in", "contract": "task-envelope"},
                {"id": "result", "direction": "out", "contract": "handoff-packet"},
                {"id": "error", "direction": "out", "contract": "error-envelope"},
            ],
        }
        evidence = {
            "id": "evidence", "kind": "pattern", "ref": "QUA-04", "label": "ev",
            "pins": [{"id": "in", "direction": "in", "contract": "handoff-packet"}],
        }
        secours = {
            "id": "secours", "kind": "pattern", "ref": "COG-01", "label": "sec",
            "pins": [{"id": "in", "direction": "in", "contract": "error-envelope"}],
        }
        human = {
            "id": "human", "kind": "pattern", "ref": "GOV-15", "label": "esc",
            "pins": [{"id": "in", "direction": "in", "contract": "error-envelope"}],
        }
        return {
            "blueprintVersion": 1, "id": "resilient",
            "nodes": [crew, evidence, secours, human],
            "edges": [
                {"from": "crew.result", "to": "evidence.in", "contract": "handoff-packet"},
                {"from": "crew.error", "to": "secours.in", "contract": "error-envelope",
                 "channel": "failure"},
                {"from": "crew.error", "to": "human.in", "contract": "error-envelope",
                 "channel": "escalation"},
            ],
        }

    def test_compile_emits_on_failure_section(self, api: ForgeAPI) -> None:
        result = api.blueprint_compile(self._resilient_bp())
        content = (api.project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "#### Résilience (on_failure)" in content
        assert "réessayer au plus 2 fois" in content
        assert "invoquer `secours`" in content
        assert "Escalade (dead-letter)" in content

    def test_reproducible_with_resilience(self, api: ForgeAPI) -> None:
        # Preuve SPEC 2 : le blueprint durci compile de façon reproductible.
        bp = self._resilient_bp()
        r1 = api.blueprint_compile(dict(bp))
        r2 = api.blueprint_compile(dict(bp))
        assert r1["hash"] == r2["hash"]

    def test_unbounded_retry_refuses_to_compile(self, api: ForgeAPI) -> None:
        bp = self._resilient_bp()
        del bp["nodes"][0]["config"]["resilience"]["retry"]["max"]
        errors = api.blueprint_validate(bp)
        assert any("R-F1" in e for e in errors)


class TestFailureInjection:
    """P3.1 — injection d'échec : le what-if de résilience."""

    def _bp(self) -> dict:
        crew = {
            "id": "crew", "kind": "pattern", "ref": "COG-01",
            "config": {"resilience": {"retry": {"max": 3}, "onExhaustion": "escalate"}},
            "pins": [
                {"id": "in", "direction": "in", "contract": "task-envelope"},
                {"id": "result", "direction": "out", "contract": "handoff-packet"},
                {"id": "error", "direction": "out", "contract": "error-envelope"},
            ],
        }
        secours = _unit("secours")
        human = _unit("human")
        return {
            "blueprintVersion": 1, "id": "bp",
            "nodes": [crew, secours, human],
            "edges": [
                {"from": "crew.error", "to": "secours.in", "contract": "error-envelope",
                 "channel": "failure"},
                {"from": "crew.error", "to": "human.in", "contract": "error-envelope",
                 "channel": "escalation"},
            ],
        }

    def test_injected_failure_follows_failure_edges(self) -> None:
        # Preuve RAFFINEMENT P3.1 : un échec injecté suit le failure-edge attendu.
        trace = r.trace_failure(self._bp(), "crew", "timeout")
        assert trace["valid"] is True
        assert trace["path"] == ["crew", "secours", "human"]
        kinds = [s["kind"] for s in trace["steps"]]
        assert kinds == ["attempt", "fallback", "escalation", "onExhaustion"]
        assert trace["steps"][0]["attempts"] == 3  # retry borné respecté

    def test_unknown_node_is_invalid(self) -> None:
        trace = r.trace_failure(self._bp(), "ghost", "timeout")
        assert trace["valid"] is False
        assert any("inconnu" in n for n in trace["notes"])

    def test_unknown_class_noted(self) -> None:
        trace = r.trace_failure(self._bp(), "crew", "cosmic-ray")
        assert any("classe d'erreur inconnue" in n for n in trace["notes"])

    def test_node_without_failure_path_noted(self) -> None:
        bp = self._bp()
        bp["edges"] = []  # crew n'a plus aucun chemin de défaillance
        trace = r.trace_failure(bp, "crew", "timeout")
        assert trace["path"] == ["crew"]
        assert any("aucun chemin de défaillance" in n for n in trace["notes"])
