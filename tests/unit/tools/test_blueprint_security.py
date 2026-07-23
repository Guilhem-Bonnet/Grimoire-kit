"""Tests du verdict de sécurité — surface d'attaque agrégée (P2.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import blueprint_security as sec
from grimoire.tools.forge_server import ForgeAPI


@pytest.fixture
def api(tmp_path: Path) -> ForgeAPI:
    root = tmp_path / "p"
    root.mkdir()
    return ForgeAPI(root, tmp_path, None)


def _ext(nid: str) -> dict:
    return {"id": nid, "kind": "extension-node", "ref": "crew/researcher", "pins": []}


def _gate(nid: str, mode: str, **params: object) -> dict:
    return {
        "id": nid, "kind": "pattern", "ref": "GOV-01", "label": nid,
        "config": {"gate": {"mode": mode, "params": dict(params)}},
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": "task-envelope"},
        ],
    }


def _unit(nid: str) -> dict:
    return {
        "id": nid, "kind": "pattern", "ref": "ORC-01", "label": nid,
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": "task-envelope"},
        ],
    }


class TestSecurityVerdict:
    def test_external_to_exit_unguarded_is_exposed(self) -> None:
        # ext -> sink, aucun filtre : R-G1 (pas de mcp-trust) + R-G2.
        nodes = [_ext("crew"), _unit("sink")]
        edges = [{"from": "crew.out", "to": "sink.in", "contract": "task-envelope"}]
        verdict = sec.security_verdict(nodes, edges)
        assert verdict["verdict"] == "exposed"
        rules = {e["rule"] for e in verdict["exposures"]}
        assert "R-G1" in rules
        assert "R-G2" in rules
        assert verdict["entryPoints"][0]["id"] == "crew"
        assert verdict["entryPoints"][0]["trustGate"] is False

    def test_guarded_flow_is_secure(self) -> None:
        # mcp-trust en amont de l'externe, guardrail in avant la sortie,
        # guardrail out avant le sink terminal.
        nodes = [
            _gate("trust", "mcp-trust", server="ctx7"),
            _ext("crew"),
            _gate("grin", "guardrail", direction="in", checks=["pii", "prompt-injection"]),
            _gate("grout", "guardrail", direction="out", checks=["secret-leak"]),
        ]
        edges = [
            {"from": "trust.out", "to": "crew.in", "contract": "task-envelope"},
            {"from": "crew.out", "to": "grin.in", "contract": "task-envelope"},
            {"from": "grin.out", "to": "grout.in", "contract": "task-envelope"},
        ]
        verdict = sec.security_verdict(nodes, edges)
        assert verdict["verdict"] == "secure"
        assert not [e for e in verdict["exposures"] if e["severity"] == "blocking"]
        assert verdict["entryPoints"][0]["trustGate"] is True
        assert verdict["entryPoints"][0]["inputGuardrail"] is True

    def test_filter_points_aggregated(self) -> None:
        nodes = [
            _gate("trust", "mcp-trust", server="ctx7"),
            _gate("grin", "guardrail", direction="in", checks=["pii"]),
        ]
        verdict = sec.security_verdict(nodes, [])
        types = {fp["type"] for fp in verdict["filterPoints"]}
        assert types == {"mcp-trust", "guardrail-in"}
        grin = next(fp for fp in verdict["filterPoints"] if fp["type"] == "guardrail-in")
        assert grin["checks"] == ["pii"]

    def test_exit_without_out_guardrail_is_info_only(self) -> None:
        # externe couvert en entrée mais sortie sans guardrail out : R-G5 info,
        # pas de blocage.
        nodes = [
            _gate("trust", "mcp-trust", server="ctx7"),
            _ext("crew"),
            _gate("grin", "guardrail", direction="in", checks=["pii"]),
        ]
        edges = [
            {"from": "trust.out", "to": "crew.in", "contract": "task-envelope"},
            {"from": "crew.out", "to": "grin.in", "contract": "task-envelope"},
        ]
        verdict = sec.security_verdict(nodes, edges)
        rg5 = [e for e in verdict["exposures"] if e["rule"] == "R-G5"]
        assert rg5 and all(e["severity"] == "info" for e in rg5)
        assert verdict["verdict"] == "secure"  # info ne bloque pas

    def test_no_surface_no_exposure(self) -> None:
        verdict = sec.security_verdict([_unit("a"), _unit("b")], [])
        assert verdict["verdict"] == "secure"
        assert verdict["entryPoints"] == []
        assert verdict["counts"]["attackSurface"] == 2  # 2 sorties, 0 externe


class TestSecurityIntegration:
    def _exposed_bp(self) -> dict:
        return {
            "blueprintVersion": 1, "id": "exposed",
            "nodes": [_ext("crew"), _unit("notify")],
            "edges": [{"from": "crew.out", "to": "notify.in", "contract": "task-envelope"}],
        }

    def _secure_bp(self) -> dict:
        return {
            "blueprintVersion": 1, "id": "secure",
            "nodes": [
                _gate("trust", "mcp-trust", server="ctx7"),
                _ext("crew"),
                _gate("grin", "guardrail", direction="in", checks=["pii"]),
                _gate("grout", "guardrail", direction="out", checks=["secret-leak"]),
            ],
            "edges": [
                {"from": "trust.out", "to": "crew.in", "contract": "task-envelope"},
                {"from": "crew.out", "to": "grin.in", "contract": "task-envelope"},
                {"from": "grin.out", "to": "grout.in", "contract": "task-envelope"},
            ],
        }

    def test_lint_exposes_security_panel(self, api: ForgeAPI) -> None:
        result = api.blueprint_lint(self._exposed_bp())
        assert result["security"]["verdict"] == "exposed"

    def test_exposed_flow_refuses_to_compile(self, api: ForgeAPI) -> None:
        # Preuve roadmap P2.4 : node externe -> sortie sans guardrail refuse.
        errors = api.blueprint_validate(self._exposed_bp())
        assert any("R-G2" in e for e in errors)

    def test_secure_verdict_renders_section(self) -> None:
        bp = self._secure_bp()
        lines = sec.compile_security_section(bp["nodes"], bp["edges"])
        text = "\n".join(lines)
        assert "### Surface d'attaque (sécurité)" in text
        assert "Verdict : **OK**" in text
        assert "Filtre `grin` : guardrail-in (pii)" in text
        assert "Entrée externe `crew` : mcp-trust, guardrail entrée" in text

    def test_gate_only_flow_compiles_with_attack_surface_section(self, api: ForgeAPI) -> None:
        # Flow à gates seuls (kind pattern) : compile réellement — pas d'extension
        # à installer — et l'artefact porte la section surface d'attaque.
        bp = {
            "blueprintVersion": 1, "id": "gates",
            "nodes": [
                _gate("grin", "guardrail", direction="in", checks=["pii"]),
                _gate("grout", "guardrail", direction="out", checks=["secret-leak"]),
            ],
            "edges": [{"from": "grin.out", "to": "grout.in", "contract": "task-envelope"}],
        }
        result = api.blueprint_compile(bp)
        content = (api.project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "### Surface d'attaque (sécurité)" in content
        assert "Filtre `grin` : guardrail-in" in content
