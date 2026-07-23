"""Tests des évals comportementaux first-class (P1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import blueprint_evals as ev
from grimoire.tools.forge_server import ForgeAPI


@pytest.fixture
def api(tmp_path: Path) -> ForgeAPI:
    root = tmp_path / "p"
    root.mkdir()
    return ForgeAPI(root, tmp_path, None)


def _unit(nid: str, **over: object) -> dict:
    node: dict = {
        "id": nid,
        "kind": "pattern",
        "ref": "ORC-01",
        "label": nid,
        "pins": [
            {"id": "in", "direction": "in", "contract": "task-envelope"},
            {"id": "out", "direction": "out", "contract": "task-envelope"},
        ],
    }
    node.update(over)
    return node


def _suite(*asserts: dict, version: str | None = "1", cid: str = "c1") -> dict:
    return {
        "version": version,
        "cases": [{"id": cid, "input": {"q": "x"}, "assert": list(asserts)}],
    }


class TestEvalsShape:
    def test_versioned_suite_with_all_assertion_kinds_ok(self) -> None:
        node = _unit("u", config={"evals": _suite(
            {"kind": "contract", "contract": "handoff-packet"},
            {"kind": "cost", "maxTokens": 5000},
            {"kind": "no-refusal"},
            {"kind": "verdict", "expected": "pass"},
        )})
        assert ev.evals_shape_errors(node) == []

    def test_suite_without_version_is_blocking(self) -> None:
        # R-E1 : la preuve est versionnée avec le blueprint.
        node = _unit("u", config={"evals": _suite(
            {"kind": "no-refusal"}, version=None,
        )})
        assert any("R-E1" in e for e in ev.evals_shape_errors(node))

    def test_duplicate_case_ids_blocking(self) -> None:
        suite = {"version": "1", "cases": [
            {"id": "dup", "input": {}, "assert": [{"kind": "no-refusal"}]},
            {"id": "dup", "input": {}, "assert": [{"kind": "no-refusal"}]},
        ]}
        node = _unit("u", config={"evals": suite})
        assert any("dupliqué" in e for e in ev.evals_shape_errors(node))

    def test_case_without_input_or_assert_blocking(self) -> None:
        suite = {"version": "1", "cases": [{"id": "c", "assert": []}]}
        node = _unit("u", config={"evals": suite})
        errors = ev.evals_shape_errors(node)
        assert any("`input`" in e for e in errors)
        assert any("`assert`" in e for e in errors)

    def test_bad_assertion_fields(self) -> None:
        node = _unit("u", config={"evals": _suite(
            {"kind": "cost"},                       # sans seuil
            {"kind": "verdict", "expected": "boom"},  # verdict inconnu
            {"kind": "contract"},                     # sans contract
        )})
        errors = ev.evals_shape_errors(node)
        assert any("`cost`" in e for e in errors)
        assert any("verdict" in e for e in errors)
        assert any("`contract`" in e for e in errors)

    def test_unknown_assertion_kind(self) -> None:
        node = _unit("u", config={"evals": _suite({"kind": "vibes"})})
        assert any("genre d'assertion invalide" in e for e in ev.evals_shape_errors(node))

    def test_evals_not_object_blocking(self) -> None:
        node = _unit("u", config={"evals": [1, 2]})
        assert any("objet attendu" in e for e in ev.evals_shape_errors(node))

    def test_no_suite_no_error(self) -> None:
        assert ev.evals_shape_errors(_unit("u")) == []


class TestEvalsLint:
    def test_r_e2_effectful_node_without_eval_warns(self) -> None:
        ext = {"id": "crew", "kind": "extension-node", "ref": "c/r", "pins": []}
        _, warnings = ev.evals_lint({"nodes": [ext], "edges": []})
        assert any("R-E2" in w for w in warnings)

    def test_r_e2_silenced_by_node_eval(self) -> None:
        ext = {"id": "crew", "kind": "extension-node", "ref": "c/r", "pins": [],
               "config": {"evals": _suite({"kind": "no-refusal"})}}
        _, warnings = ev.evals_lint({"nodes": [ext], "edges": []})
        assert not any("R-E2" in w for w in warnings)

    def test_path_taken_unknown_node_is_blocking(self) -> None:
        bp = {"nodes": [_unit("a")], "edges": [], "evals": _suite(
            {"kind": "path-taken", "inject": {"node": "ghost", "class": "timeout"},
             "path": ["ghost"]},
        )}
        errors, _ = ev.evals_lint(bp)
        assert any("node inconnu" in e for e in errors)

    def test_r_e3_path_taken_divergent_warns(self) -> None:
        # L'éval prétend un chemin que le plan de défaillance ne suit pas.
        crew = {
            "id": "crew", "kind": "pattern", "ref": "COG-01",
            "config": {"resilience": {"retry": {"max": 2}}},
            "pins": [{"id": "error", "direction": "out", "contract": "error-envelope"}],
        }
        bp = {
            "nodes": [crew, _unit("secours")],
            "edges": [{"from": "crew.error", "to": "secours.in",
                       "contract": "error-envelope", "channel": "failure"}],
            "evals": _suite(
                {"kind": "path-taken", "inject": {"node": "crew", "class": "timeout"},
                 "path": ["crew"]},  # ignore le fallback réel -> divergent
            ),
        }
        _, warnings = ev.evals_lint(bp)
        assert any("R-E3" in w for w in warnings)

    def test_r_e3_silent_when_path_matches_declared_plan(self) -> None:
        crew = {
            "id": "crew", "kind": "pattern", "ref": "COG-01",
            "config": {"resilience": {"retry": {"max": 2}}},
            "pins": [{"id": "error", "direction": "out", "contract": "error-envelope"}],
        }
        bp = {
            "nodes": [crew, _unit("secours")],
            "edges": [{"from": "crew.error", "to": "secours.in",
                       "contract": "error-envelope", "channel": "failure"}],
            "evals": _suite(
                {"kind": "path-taken", "inject": {"node": "crew", "class": "timeout"},
                 "path": ["crew", "secours"]},
            ),
        }
        _, warnings = ev.evals_lint(bp)
        assert not any("R-E3" in w for w in warnings)


class TestEvalsSummary:
    def test_declared_without_results(self) -> None:
        node = _unit("u", config={"evals": _suite(
            {"kind": "no-refusal"}, {"kind": "verdict", "expected": "pass"},
        )})
        summary = ev.evals_summary({"nodes": [node]})
        assert summary["scopes"]["u"]["declared"] == 1
        assert summary["scopes"]["u"]["executed"] == 0
        assert summary["scopes"]["u"]["rate"] is None
        assert summary["totals"]["declared"] == 1

    def test_pass_rate_with_results(self) -> None:
        node = _unit("u", config={"evals": _suite({"kind": "no-refusal"})})
        results = {"u": {"c1": True, "c2": False, "c3": True}}
        summary = ev.evals_summary({"nodes": [node]}, results)
        assert summary["scopes"]["u"]["executed"] == 3
        assert summary["scopes"]["u"]["passed"] == 2
        assert summary["scopes"]["u"]["rate"] == pytest.approx(0.6667, abs=1e-3)
        assert summary["totals"]["rate"] == pytest.approx(0.6667, abs=1e-3)

    def test_blueprint_scope_counted(self) -> None:
        bp = {"nodes": [], "evals": _suite({"kind": "no-refusal"})}
        summary = ev.evals_summary(bp)
        assert ev.BLUEPRINT_SCOPE in summary["scopes"]


class TestEvalsAcceptance:
    """« agent-test vert sur un blueprint réel » : la chaîne verticale P1.2."""

    def _bp_with_evals(self) -> dict:
        crew = {
            "id": "crew", "kind": "pattern", "ref": "COG-01", "label": "crew",
            "config": {
                "resilience": {"retry": {"max": 2}, "onExhaustion": "escalate"},
                "evals": {
                    "version": "1",
                    "cases": [
                        {
                            "id": "happy",
                            "input": {"task": "résumer"},
                            "assert": [
                                {"kind": "contract", "contract": "handoff-packet"},
                                {"kind": "cost", "maxTokens": 8000},
                                {"kind": "no-refusal"},
                                {"kind": "verdict", "expected": "pass"},
                            ],
                        },
                        {
                            "id": "timeout-escalade",
                            "input": {"task": "résumer", "slow": True},
                            "assert": [
                                {"kind": "path-taken",
                                 "inject": {"node": "crew", "class": "timeout"},
                                 "path": ["crew", "secours", "human"]},
                            ],
                        },
                    ],
                },
            },
            "pins": [
                {"id": "in", "direction": "in", "contract": "task-envelope"},
                {"id": "result", "direction": "out", "contract": "handoff-packet"},
                {"id": "error", "direction": "out", "contract": "error-envelope"},
            ],
        }
        secours = {"id": "secours", "kind": "pattern", "ref": "COG-01", "label": "sec",
                   "pins": [{"id": "in", "direction": "in", "contract": "error-envelope"}]}
        human = {"id": "human", "kind": "pattern", "ref": "GOV-15", "label": "esc",
                 "pins": [{"id": "in", "direction": "in", "contract": "error-envelope"}]}
        return {
            "blueprintVersion": 1, "id": "reel",
            "nodes": [crew, secours, human],
            "edges": [
                {"from": "crew.error", "to": "secours.in",
                 "contract": "error-envelope", "channel": "failure"},
                {"from": "crew.error", "to": "human.in",
                 "contract": "error-envelope", "channel": "escalation"},
            ],
        }

    def test_real_blueprint_validates_without_eval_errors(self, api: ForgeAPI) -> None:
        errors = api.blueprint_validate(self._bp_with_evals())
        assert not any("evals" in e or "R-E1" in e for e in errors)

    def test_lint_exposes_eval_health_panel(self, api: ForgeAPI) -> None:
        result = api.blueprint_lint(self._bp_with_evals())
        # path-taken aligné sur le plan déclaré -> pas de R-E3.
        assert not any("R-E3" in w for w in result["warnings"])
        panel = result["evals"]
        assert panel["scopes"]["crew"]["declared"] == 2
        assert panel["scopes"]["crew"]["version"] == "1"

    def test_compile_emits_eval_section(self, api: ForgeAPI) -> None:
        result = api.blueprint_compile(self._bp_with_evals())
        content = (api.project_root / result["artifact"]).read_text(encoding="utf-8")
        assert "#### Évals (preuve comportementale, v1)" in content
        assert "honore le contrat `handoff-packet`" in content
        assert "agent-test" in content
