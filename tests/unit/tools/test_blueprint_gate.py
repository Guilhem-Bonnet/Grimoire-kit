"""Tests du Gate universel paramétré (P2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import blueprint_gate as bg
from grimoire.tools.forge_server import ForgeAPI


@pytest.fixture
def api(tmp_path: Path) -> ForgeAPI:
    root = tmp_path / "project"
    root.mkdir()
    return ForgeAPI(root, tmp_path, None)


def _node(nid: str, **over: object) -> dict:
    base: dict = {
        "id": nid,
        "kind": "pattern",
        "ref": "GOV-01",
        "label": nid,
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": "task-envelope"},
        ],
    }
    base.update(over)
    return base


def _gate(nid: str, mode: str, params: dict | None = None, **gate_over: object) -> dict:
    gate: dict = {"mode": mode, "params": params or {}}
    gate.update(gate_over)
    return _node(nid, config={"gate": gate})


_VALID_PARAMS: dict[str, dict] = {
    "human": {"action": "approve", "approvers": ["maintainer"]},
    "budget": {"maxUsd": 2.5, "scope": "segment"},
    "evidence": {"require": ["test-run", "verdict"]},
    "output-contract": {"schema": "contracts/report.schema.json"},
    "guardrail": {"direction": "in", "checks": ["injection", "pii"]},
    "mcp-trust": {"server": "github", "allowedTools": ["list_prs"]},
}


class TestGateShape:
    def test_all_six_modes_valid(self) -> None:
        for mode, params in _VALID_PARAMS.items():
            node = _gate("g", mode, params)
            assert bg.gate_shape_errors(node) == [], mode

    def test_invalid_mode_rejected(self) -> None:
        errors = bg.gate_shape_errors(_gate("g", "teleport"))
        assert any("mode invalide" in e for e in errors)

    def test_invalid_on_reject_rejected(self) -> None:
        node = _gate("g", "human", _VALID_PARAMS["human"], onReject="explode")
        assert any("onReject invalide" in e for e in bg.gate_shape_errors(node))

    def test_contradictory_role_rejected(self) -> None:
        node = _gate("g", "human", _VALID_PARAMS["human"])
        node["role"] = "Unit"
        assert any("role Gate" in e for e in bg.gate_shape_errors(node))

    def test_budget_requires_cap_and_scope(self) -> None:
        errors = bg.gate_shape_errors(_gate("g", "budget", {}))
        assert any("scope requis" in e for e in errors)
        assert any("maxTokens ou maxUsd requis" in e for e in errors)

    def test_human_sample_requires_pct(self) -> None:
        errors = bg.gate_shape_errors(_gate("g", "human", {"action": "sample"}))
        assert any("pct requis" in e for e in errors)

    def test_r_g4_schema_must_resolve(self) -> None:
        node = _gate("g", "output-contract", {"schema": "missing.schema.json"})
        errors = bg.gate_shape_errors(node, resolve_schema=lambda _: False)
        assert any("R-G4" in e for e in errors)
        assert bg.gate_shape_errors(node, resolve_schema=lambda _: True) == []


class TestGateCompile:
    def test_all_six_modes_compile_with_reject_path(self) -> None:
        # Preuve SPEC 1 : les six modes compilent via le switch unique.
        for mode, params in _VALID_PARAMS.items():
            section = bg.compile_gate_section(_gate("g", mode, params))
            assert f"#### Gate ({mode})" in section, mode
            assert any("Rejet" in line for line in section), mode

    def test_reject_routes_follow_on_reject(self) -> None:
        esc = bg.compile_gate_section(
            _gate("g", "human", _VALID_PARAMS["human"], onReject="escalation")
        )
        assert any("escalation" in line for line in esc)
        block = bg.compile_gate_section(_gate("g", "budget", _VALID_PARAMS["budget"]))
        assert any("arrêt dur" in line for line in block)

    def test_non_gate_node_emits_nothing(self) -> None:
        assert bg.compile_gate_section(_node("u")) == []


class TestTrustBoundary:
    def _ext(self, nid: str) -> dict:
        return _node(nid, kind="extension-node", ref="crew/research")

    def test_r_g1_external_without_mcp_trust_blocks(self, api: ForgeAPI) -> None:
        # Preuve SPEC 2 : accès externe sans Gate(mcp-trust) refuse de compiler.
        bp = {
            "blueprintVersion": 1,
            "nodes": [self._ext("crew"), _node("sink")],
            "edges": [{"from": "crew.out", "to": "sink.in",
                       "contract": "task-envelope"}],
        }
        errors = api.blueprint_validate(bp)
        assert any("R-G1" in e for e in errors)

    def test_r_g2_external_to_exit_without_guardrail_blocks(self) -> None:
        # Preuve SPEC 3 : externe -> sortie sans guardrail refuse de compiler.
        nodes = [self._ext("crew"), _node("sink")]
        edges = [{"from": "crew.out", "to": "sink.in", "contract": "task-envelope"}]
        errors, _ = bg.gate_lint(nodes, edges)
        assert any("R-G2" in e for e in errors)

    def test_guarded_external_passes(self) -> None:
        nodes = [
            _gate("trust", "mcp-trust", _VALID_PARAMS["mcp-trust"]),
            self._ext("crew"),
            _gate("filter", "guardrail", _VALID_PARAMS["guardrail"]),
            _node("sink"),
        ]
        edges = [
            {"from": "trust.out", "to": "crew.in", "contract": "task-envelope"},
            {"from": "crew.out", "to": "filter.in", "contract": "task-envelope"},
            {"from": "filter.out", "to": "sink.in", "contract": "task-envelope"},
        ]
        errors, _ = bg.gate_lint(nodes, edges)
        assert errors == []

    def test_r_g3_block_without_alternative_warns(self) -> None:
        nodes = [_gate("g", "budget", _VALID_PARAMS["budget"]), _node("next")]
        edges = [{"from": "g.out", "to": "next.in", "contract": "task-envelope"}]
        _, warnings = bg.gate_lint(nodes, edges)
        assert any("R-G3" in w for w in warnings)
        # Un edge escalation sortant fait taire R-G3.
        edges.append(
            {"from": "g.reject", "to": "next.in", "contract": "error-envelope",
             "channel": "escalation"}
        )
        _, warnings2 = bg.gate_lint(nodes, edges)
        assert not any("R-G3" in w for w in warnings2)

    def test_compile_emits_gate_section(self, api: ForgeAPI) -> None:
        bp = {
            "blueprintVersion": 1,
            "id": "bp-gate",
            "nodes": [
                _gate("check", "evidence", _VALID_PARAMS["evidence"]),
                _node("sink"),
            ],
            "edges": [{"from": "check.out", "to": "sink.in",
                       "contract": "task-envelope"}],
        }
        result = api.blueprint_compile(bp)
        content = (api.project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "#### Gate (evidence)" in content
        assert "QUA-04" in content
