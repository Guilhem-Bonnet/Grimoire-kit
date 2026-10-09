"""Les sept genres de node que le statique ne sait pas exprimer (lot 4, issue #207).

Mêmes principes que ``tests/unit/test_flows_composite.py`` (lot 3, #206) dont
ces sept genres généralisent le mécanisme (lancer un sous-flow enfant) : de
vrais scripts Python, exécutés par de vrais ``subprocess`` — jamais un mock,
jamais un appel réseau.

Le script factice ci-dessous est unique pour toute la suite : il lit son
propre prompt pour savoir quel *node* il sert (dernier segment de la tâche,
``FLOW-<run>-<node>``) et, quand l'appelant a posé un ``extra_context``
portant ``[genre_attempt_index=N]`` (issue #207 : verify-panel/judge/fanout/
loop-until-dry l'utilisent pour différencier une tentative), une clé
``idxN`` — ce qui permet à un seul fournisseur factice de se comporter
différemment par tentative, par node, ou par les deux.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.dispatch_executor import resume_with_dispatch, run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.extract import extract_blueprint

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
PILOT_POLICY = STANDARD / "pilot.yaml"

_NODE_RE = r"Tâche \S+-(\w+) :"
_INDEX_RE = r"\[genre_attempt_index=(\d+)\]"
_RESULT_RE = r"Écris ta sortie dans le fichier (\S+)"
_PIN_RE = r"^  - (\S+) : (\S+)$"

_WRITER_TMPL = f"""\
    import re, json, sys
    prompt = sys.argv[1]
    node_match = re.search(r"{_NODE_RE}", prompt)
    node = node_match.group(1) if node_match else "?"
    index_match = re.search(r"{_INDEX_RE}", prompt)
    key = "idx" + index_match.group(1) if index_match else node
    path = re.search(r"{_RESULT_RE}", prompt).group(1)
    pins = {{m.group(1): {{"contract": m.group(2)}} for m in re.finditer(r"{_PIN_RE}", prompt, re.MULTILINE)}}
    payload = {{"pins": pins}}
    extra_map = EXTRA_MAP_JSON
    cost_map = COST_MAP_JSON
    fail_for = FAIL_FOR_JSON
    if key in fail_for or node in fail_for:
        sys.exit(0)  # rien écrit : le check échoue, la cascade lit un rouge.
    payload.update(extra_map.get(key, extra_map.get(node, {{}})))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    cost = cost_map.get(key, cost_map.get(node, 0.05))
    if cost is not None:  # None = fournisseur muet : aucun coût imprimé
        print(json.dumps({{"total_cost_usd": cost}}))
    """


def _writer_script(
    tmp_path: Path,
    *,
    extra_map: dict[str, dict] | None = None,
    cost_map: dict[str, float | None] | None = None,
    fail_for: tuple[str, ...] = (),
) -> Path:
    path = tmp_path / "scripts" / "writer.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        _WRITER_TMPL.replace("EXTRA_MAP_JSON", json.dumps(extra_map or {}))
        .replace("COST_MAP_JSON", repr(cost_map or {}))  # repr, pas json : ``None`` (muet), pas ``null``
        .replace("FAIL_FOR_JSON", json.dumps(list(fail_for)))
    )
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _invocation(script: Path) -> str:
    return f"{sys.executable} {script} {{prompt}} --model {{model}}"


def _write_registry(root: Path, script: Path) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "writer"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["writer-model"]
    currency: "api"
    invocation: "{_invocation(script)}"
    models:
      - id: "writer-model"
        tier: "cheap"
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _write_pilot_policy(root: Path, body: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / PILOT_POLICY).write_text(body, encoding="utf-8")


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows", project_root=tmp_path)


def _leaf(node_id: str = "leaf") -> dict:
    return {"id": node_id, "kind": "pattern", "ref": "ORC-01", "acceptance": [{"run": "true"}], "pins": []}


def _write_bp(path: Path, blueprint_id: str, nodes: list[dict], edges: list[dict] | None = None) -> Path:
    path.write_text(
        json.dumps({"blueprintVersion": 1, "id": blueprint_id, "nodes": nodes, "edges": edges or []}),
        encoding="utf-8",
    )
    return path


def _child(tmp_path: Path, name: str = "child.blueprint.json", leaf_id: str = "leaf") -> Path:
    return _write_bp(tmp_path / name, name.removesuffix(".blueprint.json"), [_leaf(leaf_id)])


# ── 1. fanout ────────────────────────────────────────────────────────────


def test_fanout_produit_n_elements_et_recolle(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path, extra_map={"spread": {"fanout_items": ["a", "b"]}}))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\n")
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "fanout-demo",
        [{"id": "spread", "kind": "fanout", "ref": "child.blueprint.json", "acceptance": [{"run": "true"}], "pins": []}],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "green"


def test_fanout_sans_plafond_pilote_est_refuse(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path, extra_map={"spread": {"fanout_items": ["a", "b"]}}))
    # Aucun pilot.yaml : max_fanout_n absent — le fan-out doit être refusé,
    # jamais silencieusement illimité.
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "fanout-demo",
        [{"id": "spread", "kind": "fanout", "ref": "child.blueprint.json", "acceptance": [{"run": "true"}], "pins": []}],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "blocked"
    assert outcome.node_id == "spread"


# ── 2. verify-panel ──────────────────────────────────────────────────────


def test_verify_panel_majorite_verte_ferme_le_node(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path))
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "panel-demo",
        [
            {
                "id": "panel",
                "kind": "verify-panel",
                "ref": "child.blueprint.json",
                "config": {"verifyPanel": {"k": 3, "angles": ["a", "b", "c"]}},
                "pins": [],
            }
        ],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "panel")
    assert node.verdict == "green"
    assert node.attempts == 3


def test_verify_panel_minorite_verte_nest_jamais_un_vert(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path, fail_for=("idx0", "idx1")))
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "panel-demo",
        [
            {
                "id": "panel",
                "kind": "verify-panel",
                "ref": "child.blueprint.json",
                "config": {"verifyPanel": {"k": 3, "angles": ["a", "b", "c"]}},
                "pins": [],
            }
        ],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "blocked"
    assert outcome.node_id == "panel"


def test_verify_panel_k_insuffisant_refuse_au_chargement(tmp_path: Path) -> None:
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "panel-demo",
        [
            {
                "id": "panel",
                "kind": "verify-panel",
                "ref": "child.blueprint.json",
                "config": {"verifyPanel": {"k": 1, "angles": ["a"]}},
                "pins": [],
            }
        ],
    )
    with pytest.raises(GrimoireRuntimeError, match=r"verifyPanel\.k"):
        load_blueprint(bp, tmp_path)


# ── 3. loop-until-dry ────────────────────────────────────────────────────


def test_loop_until_dry_sarrete_des_quun_tour_ne_dit_rien_de_neuf(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        _writer_script(
            tmp_path,
            extra_map={"idx0": {"novelty_key": "k1"}, "idx1": {"novelty_key": "k2"}, "idx2": {"novelty_key": "k1"}},
        ),
    )
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "loop-demo",
        [
            {
                "id": "loop",
                "kind": "loop-until-dry",
                "ref": "child.blueprint.json",
                "config": {"loopUntilDry": {"maxRounds": 5}},
                "pins": [],
            }
        ],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "loop")
    assert node.verdict == "green"
    assert node.attempts == 3  # sec au 3e tour (novelty_key "k1" déjà vue)


def test_loop_until_dry_maxrounds_invalide_refuse_au_chargement(tmp_path: Path) -> None:
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "loop-demo",
        [{"id": "loop", "kind": "loop-until-dry", "ref": "child.blueprint.json", "config": {"loopUntilDry": {"maxRounds": 0}}, "pins": []}],
    )
    with pytest.raises(GrimoireRuntimeError, match="maxRounds"):
        load_blueprint(bp, tmp_path)


# ── 4. judge ─────────────────────────────────────────────────────────────


def test_judge_designe_un_gagnant_parmi_n_tentatives(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path, extra_map={"judge": {"judge_winner": 1}}))
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "judge-demo",
        [
            {
                "id": "judge",
                "kind": "judge",
                "ref": "child.blueprint.json",
                "acceptance": [{"run": "true"}],
                "config": {"judge": {"n": 2, "angles": ["angle-a", "angle-b"]}},
                "pins": [],
            }
        ],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "judge")
    assert node.verdict == "green"


def test_judge_angles_mal_comptees_refuse_au_chargement(tmp_path: Path) -> None:
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "judge-demo",
        [
            {
                "id": "judge",
                "kind": "judge",
                "ref": "child.blueprint.json",
                "acceptance": [{"run": "true"}],
                "config": {"judge": {"n": 2, "angles": ["angle-a"]}},
                "pins": [],
            }
        ],
    )
    with pytest.raises(GrimoireRuntimeError, match=r"judge\.angles"):
        load_blueprint(bp, tmp_path)


# ── 5. checkpoint ────────────────────────────────────────────────────────


def test_checkpoint_toujours_pending_reject_bloque_avec_motif_approve_avance(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path))
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "checkpoint-demo",
        [
            {"id": "gate", "kind": "checkpoint", "ref": "", "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            {"id": "after", "kind": "pattern", "ref": "ORC-01", "acceptance": [{"run": "true"}], "pins": [{"id": "in", "direction": "in", "contract": "c1"}]},
        ],
        [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "waiting_host"
    assert outcome.node_id == "gate"

    executor = InteractiveNodeExecutor(stream=io.StringIO())
    gate_output = {"pins": {"out": {"contract": "c1"}}}
    reject_outcome = engine.resume(
        outcome.run_id,
        output={**gate_output, "checkpoint_decision": "reject", "checkpoint_reason": "pas prêt"},
        executor=executor,
    )
    assert reject_outcome.ok is False
    assert any("pas prêt" in f for f in reject_outcome.faults)
    status = engine.status(outcome.run_id)
    assert status.current_node == "gate"  # reprise exacte, jamais le node suivant

    approve_outcome = engine.resume(
        outcome.run_id, output={**gate_output, "checkpoint_decision": "approve"}, executor=executor
    )
    assert approve_outcome.ok is True
    assert approve_outcome.node_id == "after"


def test_checkpoint_decision_invalide_est_un_refus_nomme(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path))
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "checkpoint-demo",
        [{"id": "gate", "kind": "checkpoint", "ref": "", "pins": []}],
    )
    engine = _engine(tmp_path)
    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    executor = InteractiveNodeExecutor(stream=io.StringIO())
    bad = engine.resume(outcome.run_id, output={"pins": {}, "checkpoint_decision": "maybe"}, executor=executor)
    assert bad.ok is False
    assert any("checkpoint_decision" in f for f in bad.faults)


# ── 6. budget ────────────────────────────────────────────────────────────


def test_budget_abandonne_les_passes_optionnelles_avant_depassement(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        _writer_script(tmp_path, cost_map={"leaf_a": 0.1, "leaf_b": 0.9, "leaf_c": 0.1}),
    )
    _child(tmp_path, "pass0.blueprint.json", "leaf_a")
    _child(tmp_path, "pass1.blueprint.json", "leaf_b")
    _child(tmp_path, "pass2.blueprint.json", "leaf_c")
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "budget-demo",
        [
            {
                "id": "spend",
                "kind": "budget",
                "ref": "",
                "config": {
                    "budget": {
                        "maxCostUsd": 0.15,
                        "passes": ["pass0.blueprint.json", "pass1.blueprint.json", "pass2.blueprint.json"],
                    }
                },
                "pins": [],
            }
        ],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "spend")
    assert node.verdict == "green"
    assert node.cost_usd == pytest.approx(1.0)  # pass0 (.1) + pass1 (.9) ; pass2 abandonnée


def test_budget_passes_vide_refuse_au_chargement(tmp_path: Path) -> None:
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "budget-demo",
        [{"id": "spend", "kind": "budget", "ref": "", "config": {"budget": {"maxCostUsd": 1.0, "passes": []}}, "pins": []}],
    )
    with pytest.raises(GrimoireRuntimeError, match=r"budget\.passes"):
        load_blueprint(bp, tmp_path)


# ── 7. replay-diff ───────────────────────────────────────────────────────


def test_replay_diff_rejoue_deux_fois_et_expose_la_divergence(tmp_path: Path) -> None:
    _write_registry(tmp_path, _writer_script(tmp_path))
    _child(tmp_path)
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "replay-demo",
        [{"id": "replay", "kind": "replay-diff", "ref": "child.blueprint.json", "pins": []}],
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "replay")
    assert node.verdict == "green"
    assert node.attempts == 2


# ── 8. rejeu : un blueprint de démonstration exerce les sept genres ───────


def _demo_blueprint(tmp_path: Path) -> Path:
    return _write_bp(
        tmp_path / "demo.blueprint.json",
        "sept-genres-demo",
        [
            {"id": "gate", "kind": "checkpoint", "ref": "", "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            {
                "id": "spend",
                "kind": "budget",
                "ref": "",
                "config": {"budget": {"maxCostUsd": 10.0, "passes": ["pass.blueprint.json"]}},
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c1"},
                    {"id": "out", "direction": "out", "contract": "c2"},
                ],
            },
            {
                "id": "spread",
                "kind": "fanout",
                "ref": "child.blueprint.json",
                "acceptance": [{"run": "true"}],
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c2"},
                    {"id": "out", "direction": "out", "contract": "c3"},
                ],
            },
            {
                "id": "panel",
                "kind": "verify-panel",
                "ref": "child.blueprint.json",
                "config": {"verifyPanel": {"k": 2, "angles": ["a", "b"]}},
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c3"},
                    {"id": "out", "direction": "out", "contract": "c4"},
                ],
            },
            {
                "id": "loop",
                "kind": "loop-until-dry",
                "ref": "child.blueprint.json",
                "config": {"loopUntilDry": {"maxRounds": 3}},
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c4"},
                    {"id": "out", "direction": "out", "contract": "c5"},
                ],
            },
            {
                "id": "judge",
                "kind": "judge",
                "ref": "child.blueprint.json",
                "acceptance": [{"run": "true"}],
                "config": {"judge": {"n": 2, "angles": ["angle-a", "angle-b"]}},
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c5"},
                    {"id": "out", "direction": "out", "contract": "c6"},
                ],
            },
            {
                "id": "replay",
                "kind": "replay-diff",
                "ref": "child.blueprint.json",
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c6"},
                    {"id": "out", "direction": "out", "contract": "c7"},
                ],
            },
            {
                "id": "sub",
                "kind": "composite",
                "ref": "child.blueprint.json",
                "pins": [{"id": "in", "direction": "in", "contract": "c7"}],
            },
        ],
        [
            {"from": "gate.out", "to": "spend.in", "contract": "c1"},
            {"from": "spend.out", "to": "spread.in", "contract": "c2"},
            {"from": "spread.out", "to": "panel.in", "contract": "c3"},
            {"from": "panel.out", "to": "loop.in", "contract": "c4"},
            {"from": "loop.out", "to": "judge.in", "contract": "c5"},
            {"from": "judge.out", "to": "replay.in", "contract": "c6"},
            {"from": "replay.out", "to": "sub.in", "contract": "c7"},
        ],
    )


def _run_demo_to_completion(engine: FlowEngine, bp: Path, tmp_path: Path) -> str:
    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "waiting_host", outcome.to_dict()
    assert outcome.node_id == "gate"
    engine.resume(
        outcome.run_id,
        output={"pins": {"out": {"contract": "c1"}}, "checkpoint_decision": "approve"},
        executor=InteractiveNodeExecutor(stream=io.StringIO()),
    )
    final = resume_with_dispatch(engine, outcome.run_id, project_root=tmp_path)
    assert final.status == "finished", final.to_dict()
    return outcome.run_id


def test_demo_sept_genres_tourne_sextrait_et_se_rejoue_a_lidentique(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        _writer_script(
            tmp_path,
            extra_map={
                "spread": {"fanout_items": ["a", "b"]},
                "judge": {"judge_winner": 0},
                "idx0": {"novelty_key": "k1"},
                "idx1": {"novelty_key": "k1"},
            },
            cost_map={"leaf": 0.05},
        ),
    )
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\n")
    _child(tmp_path)
    _child(tmp_path, "pass.blueprint.json", "leaf_pass")
    bp = _demo_blueprint(tmp_path)
    engine = _engine(tmp_path)

    run_id = _run_demo_to_completion(engine, bp, tmp_path)
    first_status = engine.status(run_id)
    assert first_status.completed_nodes == ("gate", "spend", "spread", "panel", "loop", "judge", "replay", "sub")

    draft, trace = extract_blueprint(engine, run_id, tmp_path)
    draft_kinds = {n["id"]: n["kind"] for n in draft["nodes"]}
    assert draft_kinds == {
        "gate": "checkpoint",
        "spend": "budget",
        "spread": "fanout",
        "panel": "verify-panel",
        "loop": "loop-until-dry",
        "judge": "judge",
        "replay": "replay-diff",
        "sub": "composite",
    }
    # Aucun genre n'est aplati : chaque node garde sa config d'origine verbatim.
    spend_draft = next(n for n in draft["nodes"] if n["id"] == "spend")
    assert spend_draft["config"]["budget"]["passes"] == ["pass.blueprint.json"]
    assert {t.node_id: t.status for t in trace} == {
        "gate": "completed",
        "spend": "completed",
        "spread": "completed",
        "panel": "completed",
        "loop": "completed",
        "judge": "completed",
        "replay": "completed",
        "sub": "completed",
    }

    draft_path = tmp_path / f"{draft['id']}.blueprint.json"
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    replay_run_id = _run_demo_to_completion(engine, draft_path, tmp_path)
    second_status = engine.status(replay_run_id)
    assert second_status.completed_nodes == first_status.completed_nodes
    assert second_status.status == first_status.status


# ── 9. Coût inconnu et plafond : jamais un frère ni un vert jeté (W1-01, revue) ──


def _silent_script(tmp_path: Path, **kwargs: object) -> Path:
    """Le même fournisseur, mais muet : il n'imprime jamais ``total_cost_usd``."""
    script = _writer_script(tmp_path, **kwargs)  # type: ignore[arg-type]
    text = script.read_text(encoding="utf-8")
    muted = text.replace("cost = cost_map.get(key, cost_map.get(node, 0.05))", "cost = None")
    assert muted != text
    script.write_text(muted, encoding="utf-8")
    return script


def _fanout_bp(tmp_path: Path) -> Path:
    _child(tmp_path)
    return _write_bp(
        tmp_path / "bp.blueprint.json",
        "fanout-demo",
        [{"id": "spread", "kind": "fanout", "ref": "child.blueprint.json", "acceptance": [{"run": "true"}], "pins": []}],
    )


def test_fanout_fournisseur_muet_sous_plafond_large_lance_tous_les_elements(tmp_path: Path) -> None:
    """S1 : un coût inconnu ne jette pas les frères — ``cost_unknown`` est réservé à l'escalade."""
    _write_registry(tmp_path, _silent_script(tmp_path, extra_map={"spread": {"fanout_items": ["a", "b", "c"]}}))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 100\n")
    bp = _fanout_bp(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "green", outcome.to_dict()
    assert node.attempts == 3
    assert node.cost_cap_reason is None
    assert node.to_dict()["cost_status"] == "unknown"  # le statut reste sur le node


def test_fanout_dont_le_cout_connu_atteint_le_plafond_dit_cost_reached(tmp_path: Path) -> None:
    """S3 : la raison du plafond est portée par le node, pas seulement le verdict."""
    _write_registry(
        tmp_path, _writer_script(tmp_path, extra_map={"spread": {"fanout_items": ["a", "b", "c"]}}, cost_map={"leaf": 0.4})
    )
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 0.5\n")
    bp = _fanout_bp(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "cost_capped", outcome.to_dict()
    assert node.cost_cap_reason == "cost_reached"
    assert node.to_dict()["cost_cap_reason"] == "cost_reached"


def test_budget_muet_dit_pourquoi_la_passe_optionnelle_n_est_pas_lancee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3 : ``launched: False`` porte ``skipped_reason`` (coût inconnu sous la politique ``stop``)."""
    from grimoire.flows import genres

    captured: list[tuple[str, object]] = []
    real = genres.execute_genre_node

    def spy(executor, contract, context_pack):  # type: ignore[no-untyped-def]
        result = real(executor, contract, context_pack)
        captured.append((contract.node_id, result))
        return result

    monkeypatch.setattr(genres, "execute_genre_node", spy)
    _write_registry(tmp_path, _silent_script(tmp_path))
    _child(tmp_path, "pass0.blueprint.json", "leaf_a")
    _child(tmp_path, "pass1.blueprint.json", "leaf_b")
    bp = _write_bp(
        tmp_path / "bp.blueprint.json",
        "budget-demo",
        [
            {
                "id": "spend",
                "kind": "budget",
                "ref": "",
                "config": {"budget": {"maxCostUsd": 0.15, "passes": ["pass0.blueprint.json", "pass1.blueprint.json"]}},
                "pins": [],
            }
        ],
    )

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    result = next(r for node_id, r in captured if node_id == "spend")
    rows = result.output["budget_passes"]  # type: ignore[attr-defined]
    assert rows[0]["launched"] is True
    assert rows[0]["cost_status"] == "unknown"
    assert rows[1]["launched"] is False
    assert rows[1]["skipped_reason"] == "cost_unknown"


def test_lignes_enfants_et_resume_du_juge_portent_le_statut_du_cout() -> None:
    """S6 : un minimum se lit ``>= 0.42``, jamais « coût 0.42 » ; la ligne porte statut et décompte."""
    from grimoire.costs import Cost
    from grimoire.flows.genres import _candidates_summary, _child_row, _ChildRun

    partial = _ChildRun(
        child_run_id="WFI-1", status="finished", cost=Cost.exact(0.42) + Cost.unpriced(2),
        faults=(), host_reason=None, attempts=3, escalations=0,
    )
    silent = _ChildRun(
        child_run_id="WFI-2", status="finished", cost=Cost.unpriced(1),
        faults=(), host_reason=None, attempts=1, escalations=0,
    )

    row = _child_row(0, partial)
    assert (row["cost_usd"], row["cost_status"], row["unpriced_calls"]) == (0.42, "lower_bound", 2)
    assert _child_row(1, silent)["cost_status"] == "unknown"
    summary = _candidates_summary([partial, silent])
    assert "coût >= 0.4200 USD (2 non pricés)" in summary
    assert "coût inconnu (1 non pricés)" in summary
    assert "coût 0.42\n" not in summary


@pytest.mark.parametrize("policy", ["", "max_cost_usd_per_node: 100\n"], ids=["sans_plafond", "plafond_large"])
def test_fanout_phase1_muette_laisse_le_cout_du_node_inconnu(tmp_path: Path, policy: str) -> None:
    """La phase 1 (``_execute_plain``) sans coût imprimé ne devient pas « exact » : le node et le run le disent."""
    _write_registry(
        tmp_path,
        _writer_script(
            tmp_path, extra_map={"spread": {"fanout_items": ["a", "b"]}}, cost_map={"spread": None, "leaf": 0.05}
        ),
    )
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\n" + policy)
    bp = _fanout_bp(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "green", outcome.to_dict()
    node_dict = node.to_dict()
    assert node_dict["cost_status"] == "lower_bound"
    assert node_dict["unpriced_calls"] >= 1
    assert abs(node_dict["cost_usd"] - 0.1) < 1e-9  # minimum : les deux enfants chiffrés
    run_dict = outcome.to_dict()
    assert run_dict["cost_status"] == "lower_bound"


@pytest.mark.parametrize("policy", ["", "max_cost_usd_per_node: 100\n"], ids=["sans_plafond", "plafond_large"])
def test_fanout_phase1_payee_est_comptee_dans_le_node(tmp_path: Path, policy: str) -> None:
    """Phase 1 à 0.07 + deux enfants à 0.05 = 0.17, pas 0.10 : la phase 1 n'est pas écrasée."""
    _write_registry(
        tmp_path,
        _writer_script(
            tmp_path, extra_map={"spread": {"fanout_items": ["a", "b"]}}, cost_map={"spread": 0.07, "leaf": 0.05}
        ),
    )
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\n" + policy)
    bp = _fanout_bp(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "green", outcome.to_dict()
    node_dict = node.to_dict()
    assert abs(node_dict["cost_usd"] - 0.17) < 1e-9
    assert node_dict["cost_status"] == "exact"
    assert node_dict["unpriced_calls"] == 0


def test_fanout_phase1_payee_compte_pour_le_plafond_des_freres(tmp_path: Path) -> None:
    """Le plafond voit ce que la phase 1 a déjà dépensé : 0.07 + 0.05 atteint 0.1 avant le 2e enfant."""
    _write_registry(
        tmp_path,
        _writer_script(
            tmp_path, extra_map={"spread": {"fanout_items": ["a", "b", "c"]}}, cost_map={"spread": 0.07, "leaf": 0.05}
        ),
    )
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 0.1\n")
    bp = _fanout_bp(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == "spread")
    assert node.verdict == "cost_capped", outcome.to_dict()
    assert node.cost_cap_reason == "cost_reached"
    assert node.attempts == 1  # seul le premier enfant est lancé


# ── 10. Revue du tour 3 : plafond non évalué, coût du juge, motif par genre ─


def _sibling_bp(tmp_path: Path, kind: str) -> tuple[Path, str, dict[str, dict]]:
    """Un blueprint à un node du genre *kind* (à frères), son id, et la ``extra_map`` du fournisseur."""
    _child(tmp_path)
    node: dict = {"id": "gn", "kind": kind, "ref": "child.blueprint.json", "pins": []}
    extra: dict[str, dict] = {}
    if kind == "fanout":
        node["acceptance"] = [{"run": "true"}]
        extra["gn"] = {"fanout_items": ["a", "b", "c"]}
    elif kind == "verify-panel":
        node["config"] = {"verifyPanel": {"k": 3, "angles": ["a", "b", "c"]}}
    elif kind == "loop-until-dry":
        node["config"] = {"loopUntilDry": {"maxRounds": 4}}
        extra.update({f"idx{i}": {"novelty_key": f"k{i}"} for i in range(4)})
    elif kind == "judge":
        node["acceptance"] = [{"run": "true"}]
        node["config"] = {"judge": {"n": 3, "angles": ["a", "b", "c"]}}
        extra["gn"] = {"judge_winner": 0}
    elif kind == "replay-diff":
        pass
    return _write_bp(tmp_path / "bp.blueprint.json", f"{kind}-demo", [node]), "gn", extra


SIBLING_KINDS = ["fanout", "verify-panel", "loop-until-dry", "judge", "replay-diff"]


@pytest.mark.parametrize("kind", SIBLING_KINDS)
def test_frere_muet_sous_plafond_et_stop_continue_mais_marque_le_plafond_non_evalue(tmp_path: Path, kind: str) -> None:
    """Un genre à frères continue sous un coût inconnu (S1) ; il le DIT : plafond non évalué, pas « sous le plafond »."""
    bp, node_id, extra = _sibling_bp(tmp_path, kind)
    _write_registry(tmp_path, _silent_script(tmp_path, extra_map=extra))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 0.01\non_unknown_cost: stop\n")

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == node_id)
    assert node.verdict == "green", outcome.to_dict()
    assert node.cost_cap_reason is None
    assert node.cost_cap_unevaluated is True
    assert node.to_dict()["cost_cap_unevaluated"] is True
    assert node.to_dict()["cost_status"] == "unknown"


@pytest.mark.parametrize("kind", SIBLING_KINDS)
@pytest.mark.parametrize(
    "policy",
    [
        "max_cost_usd_per_node: 100\non_unknown_cost: continue_flagged\n",
        "on_unknown_cost: stop\n",
    ],
    ids=["continue_flagged", "sans_plafond"],
)
def test_frere_muet_sans_plafond_a_evaluer_ne_porte_pas_la_marque(tmp_path: Path, kind: str, policy: str) -> None:
    """La marque n'existe que si un plafond est posé ET que la politique ``stop`` n'a pas pu le juger."""
    bp, node_id, extra = _sibling_bp(tmp_path, kind)
    _write_registry(tmp_path, _silent_script(tmp_path, extra_map=extra))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\n" + policy)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == node_id)
    assert node.verdict == "green", outcome.to_dict()
    assert node.cost_cap_unevaluated is False


@pytest.mark.parametrize("kind", SIBLING_KINDS)
def test_frere_dont_le_cout_est_connu_et_sous_le_plafond_ne_porte_pas_la_marque(tmp_path: Path, kind: str) -> None:
    bp, node_id, extra = _sibling_bp(tmp_path, kind)
    _write_registry(tmp_path, _writer_script(tmp_path, extra_map=extra, cost_map={"leaf": 0.01, "gn": 0.01}))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 100\n")

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == node_id)
    assert node.verdict == "green", outcome.to_dict()
    assert node.cost_cap_unevaluated is False
    assert node.to_dict()["cost_status"] == "exact"


@pytest.mark.parametrize(
    ("kind", "leaf_cost"),
    [("fanout", 0.4), ("verify-panel", 0.4), ("loop-until-dry", 0.4), ("judge", 0.4), ("replay-diff", 0.6)],
)
def test_cout_atteint_dit_cost_reached_sur_chaque_genre(tmp_path: Path, kind: str, leaf_cost: float) -> None:
    """S3 : ``cost_cap_reason == 'cost_reached'`` porté par le node ET sérialisé, pour chaque genre à frères."""
    bp, node_id, extra = _sibling_bp(tmp_path, kind)
    _write_registry(tmp_path, _writer_script(tmp_path, extra_map=extra, cost_map={"leaf": leaf_cost}))
    _write_pilot_policy(tmp_path, "max_fanout_n: 5\nmax_cost_usd_per_node: 0.5\n")

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    node = next(n for n in outcome.nodes if n.node_id == node_id)
    assert node.verdict == "cost_capped", outcome.to_dict()
    assert node.cost_cap_reason == "cost_reached"
    assert node.to_dict()["cost_cap_reason"] == "cost_reached"
    assert node.cost_cap_unevaluated is False  # le plafond a été jugé : atteint


# -- le juge : le coût des candidats survit à toute sortie non verte -------------


def _judge_bp(tmp_path: Path, *, with_acceptance: bool = True) -> Path:
    _child(tmp_path)
    node: dict = {
        "id": "judge",
        "kind": "judge",
        "ref": "child.blueprint.json",
        "config": {"judge": {"n": 2, "angles": ["angle-a", "angle-b"]}},
        "pins": [],
    }
    # Un critère de texte ambigu fait un node V2 : le juge lui-même revient à l'hôte (``pending``).
    node["acceptance"] = [{"run": "true"}] if with_acceptance else ["le meilleur candidat est choisi avec soin"]
    return _write_bp(tmp_path / "bp.blueprint.json", "judge-cost", [node])


@pytest.mark.parametrize("candidates", ["muets", "payes"])
@pytest.mark.parametrize("issue", ["winner_invalide", "juge_rouge", "juge_en_attente"])
def test_juge_non_vert_garde_le_cout_des_candidats(tmp_path: Path, candidates: str, issue: str) -> None:
    kwargs: dict = {"cost_map": {"leaf": None if candidates == "muets" else 0.1}}
    if issue == "winner_invalide":
        kwargs["extra_map"] = {"judge": {"judge_winner": 9}}
    elif issue == "juge_rouge":
        kwargs["fail_for"] = ("judge",)
    bp = _judge_bp(tmp_path, with_acceptance=issue != "juge_en_attente")
    _write_registry(tmp_path, _writer_script(tmp_path, **kwargs))

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    assert outcome.status in {"blocked", "waiting_host"}, outcome.to_dict()
    node = next(n for n in outcome.nodes if n.node_id == "judge")
    assert node.verdict == {"winner_invalide": "red", "juge_rouge": "red", "juge_en_attente": "waiting_host"}[issue]
    d = node.to_dict()
    if candidates == "muets":
        assert d["unpriced_calls"] >= 2, d  # les deux candidats muets, jamais perdus
        assert d["cost_status"] in {"lower_bound", "unknown"}
        assert outcome.to_dict()["unpriced_calls"] >= 2
    else:
        judge_cost = 0.05 if issue == "winner_invalide" else 0.0  # le juge rouge/en attente n'imprime rien
        assert d["cost_usd"] == pytest.approx(0.2 + judge_cost), d  # les 0.20 USD des candidats sont conservés
        assert outcome.to_dict()["total_cost_usd"] == pytest.approx(0.2 + judge_cost)
    assert d["attempts"] >= 2
