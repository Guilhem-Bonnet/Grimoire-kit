"""W1-03 : un ``resume`` rejoué ne casse plus un run sain.

Défaut reproduit sur la base : après un ``resume`` réussi du node ``a``, le
même JSON renvoyé (réponse perdue, nouvel essai de l'hôte) est validé contre le
contrat du node ``b`` ; il le viole, ``fail_step`` écrit ``STEP_FAILED`` et le
run passe BLOCKED alors que rien n'avait mal tourné.

Correctif : l'empreinte sha256 canonique de la sortie acceptée est rangée dans
le checkpoint du node (``evidence_refs``) ; une sortie invalide pour le node
courant mais identique à celle d'un node franchi répond ``ok, replay=True`` et
n'écrit rien.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine, rust_backend_available
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import RunEventType, WorkflowStatus

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_flows_core not installed — build it locally (maturin develop) or run the Rust CI job",
)

OUT_A = {"pins": {"out": {"contract": "c1"}}}
OUT_B = {"pins": {"out": {"contract": "c2"}}}
OUT_C = {"pins": {}}


def _node(node_id: str, pins: list[dict[str, str]], ref: str = "ORC-01") -> dict[str, Any]:
    return {"id": node_id, "kind": "pattern", "ref": ref, "label": node_id.upper(), "pins": pins}


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes",
        "name": "Trois nodes",
        "nodes": [
            _node("a", [{"id": "out", "direction": "out", "contract": "c1"}]),
            {
                **_node("b", [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}], "demo/demo-node"),
                "kind": "extension-node",
            },
            _node("c", [{"id": "in", "direction": "in", "contract": "c2"}], "QUA-04"),
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


@pytest.fixture
def bp(tmp_path: Path) -> Path:
    return _blueprint(tmp_path)


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


def _start(tmp_path: Path, bp: Path) -> tuple[FlowEngine, str]:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return engine, wfi.id


def _events(tmp_path: Path, run_id: str) -> list[RunEventType]:
    return [e.event_type for e in RuntimeKernel(tmp_path / "runtime").get_run_events(run_id)]


def _snapshot(tmp_path: Path) -> dict[str, bytes]:
    """Contenu de tous les fichiers du kernel et des métas de run (hors verrous)."""
    return {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and p.suffix != ".lock" and p.suffix != ".blueprint.json" and "blueprint" not in p.name
    }


def test_identical_resume_is_a_replay_and_does_not_block_the_run(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    assert engine.resume(run_id, output=OUT_A).ok

    again = engine.resume(run_id, output=OUT_A)

    assert again.ok, again.faults
    assert RunEventType.STEP_FAILED not in _events(tmp_path, run_id)
    status = engine.status(run_id)
    assert status.status != WorkflowStatus.BLOCKED.value
    assert status.current_node == "b"
    assert status.last_refusal is None
    assert again.replay is True
    assert again.replay_of == "a"
    assert again.node_id == "b"
    assert again.contract is not None and again.contract.node_id == "b"
    assert again.finished is False


def test_two_identical_resumes_over_three_nodes_leave_the_run_healthy(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    first_a = engine.resume(run_id, output=OUT_A)
    second_a = engine.resume(run_id, output=OUT_A)
    first_b = engine.resume(run_id, output=OUT_B)

    assert [first_a.ok, second_a.ok, first_b.ok] == [True] * 3
    assert [first_a.replay, second_a.replay, first_b.replay] == [False, True, False]
    assert RunEventType.STEP_FAILED not in _events(tmp_path, run_id)
    status = engine.status(run_id)
    assert status.current_node == "c"
    assert status.completed_nodes == ("a", "b")

    last = engine.resume(run_id, output=OUT_C)
    assert last.ok and last.finished and not last.replay
    assert engine.status(run_id).status == WorkflowStatus.COMPLETED.value
    assert RunEventType.STEP_FAILED not in _events(tmp_path, run_id)


def test_replay_of_an_output_the_current_node_also_accepts_is_not_detectable(tmp_path: Path, bp: Path) -> None:
    """Limite écrite : ``c`` n'a pas de pin de sortie, il accepte donc toute sortie bien formée.

    Renvoyer la sortie de ``b`` quand ``c`` est courant est valide pour ``c`` :
    c'est une soumission normale, pas un rejeu (sinon deux nodes sans pin de
    sortie ne pourraient jamais s'enchaîner).
    """
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    engine.resume(run_id, output=OUT_B)

    outcome = engine.resume(run_id, output=OUT_B)

    assert outcome.ok and outcome.finished and not outcome.replay


def test_replay_writes_nothing(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    before = _snapshot(tmp_path)
    events_before = _events(tmp_path, run_id)

    outcome = engine.resume(run_id, output=OUT_A)

    assert outcome.ok
    assert outcome.replay is True
    assert _events(tmp_path, run_id) == events_before
    assert _snapshot(tmp_path) == before


def test_replay_is_recognised_by_a_fresh_engine_after_a_crash(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)

    reborn = _engine(tmp_path)
    outcome = reborn.resume(run_id, output=OUT_A)

    assert outcome.ok and outcome.replay is True
    assert RunEventType.STEP_FAILED not in _events(tmp_path, run_id)


def test_replay_key_order_does_not_matter(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output={"pins": {"out": {"contract": "c1", "note": "x"}}, "extra": 1})
    reordered = {"extra": 1, "pins": {"out": {"note": "x", "contract": "c1"}}}

    outcome = engine.resume(run_id, output=reordered)

    assert outcome.ok and outcome.replay is True


def test_replay_after_the_last_node_on_a_completed_run(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    engine.resume(run_id, output=OUT_B)
    assert engine.resume(run_id, output=OUT_C).finished

    outcome = engine.resume(run_id, output=OUT_C)

    assert outcome.ok and outcome.finished and outcome.replay is True
    assert outcome.replay_of == "c"
    assert engine.status(run_id).status == WorkflowStatus.COMPLETED.value


def test_unknown_output_on_a_completed_run_still_raises(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    engine.resume(run_id, output=OUT_B)
    engine.resume(run_id, output=OUT_C)

    with pytest.raises(GrimoireRuntimeError, match="rien à reprendre"):
        engine.resume(run_id, output={"pins": {"autre": {"contract": "zz"}}})


def test_invalid_output_is_still_refused(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)

    refused = engine.resume(run_id, output={"pins": {"out": {"contract": "mauvais-contrat"}}})

    assert not refused.ok
    assert refused.replay is False
    assert refused.node_id == "b"
    assert any("node=b" in f and "pin=out" in f for f in refused.faults)
    assert engine.status(run_id).status == WorkflowStatus.BLOCKED.value


def test_near_miss_of_an_accepted_output_is_not_a_replay(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)

    refused = engine.resume(run_id, output={**OUT_A, "extra": True})

    assert not refused.ok
    assert refused.replay is False
    assert engine.status(run_id).status == WorkflowStatus.BLOCKED.value


def test_refused_output_is_never_recorded_as_accepted(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    bad = {"pins": {"out": {"contract": "mauvais-contrat"}}}
    assert not engine.resume(run_id, output=bad).ok

    again = engine.resume(run_id, output=bad)

    assert not again.ok
    assert again.replay is False


def test_replay_while_blocked_does_not_write_either(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    engine.resume(run_id, output={"pins": {}})  # refusé : b attend la pin out
    assert engine.status(run_id).status == WorkflowStatus.BLOCKED.value
    before = _snapshot(tmp_path)

    outcome = engine.resume(run_id, output=OUT_A)

    assert outcome.ok and outcome.replay is True
    assert _snapshot(tmp_path) == before
    assert engine.resume(run_id, output=OUT_B).ok


def test_a_valid_output_identical_to_an_earlier_one_advances_normally(tmp_path: Path) -> None:
    """Deux nodes sans pin de sortie acceptent la même sortie vide : jamais un rejeu."""
    blueprint = {
        "blueprintVersion": 1,
        "id": "deux-vides",
        "name": "Deux vides",
        "nodes": [_node("x", []), _node("y", [], "QUA-04")],
        "edges": [],
    }
    path = tmp_path / "deux-vides.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    engine, run_id = _start(tmp_path, path)

    first = engine.resume(run_id, output=OUT_C)
    second = engine.resume(run_id, output=OUT_C)

    assert first.ok and not first.replay and first.node_id == "y"
    assert second.ok and not second.replay and second.finished


def test_allow_replay_false_keeps_the_strict_semantics(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)

    strict = engine.resume(run_id, output=OUT_A, allow_replay=False)

    assert not strict.ok
    assert strict.replay is False
    assert engine.status(run_id).status == WorkflowStatus.BLOCKED.value


def test_digest_is_canonical_and_survives_a_lone_surrogate() -> None:
    from grimoire.flows.engine import _output_digest

    assert _output_digest({"b": 1, "a": [1, 2]}) == _output_digest({"a": [1, 2], "b": 1})
    assert _output_digest({"a": 1}) != _output_digest({"a": 2})
    lone = {"pins": {"out": {"contract": "c" + chr(0xD800)}}}
    digest = _output_digest(lone)
    assert digest is not None and len(digest) == 64
    # Même empreinte que la forme ASCII échappée : rien ne dépend de l'encodage UTF-8.
    assert digest == _output_digest(json.loads(json.dumps(lone)))
    assert _output_digest({"a": object()}) is None


def test_resume_outcome_to_dict_exposes_replay(tmp_path: Path, bp: Path) -> None:
    engine, run_id = _start(tmp_path, bp)
    engine.resume(run_id, output=OUT_A)
    payload = engine.resume(run_id, output=OUT_A).to_dict()
    assert payload["ok"] is True
    assert payload["replay"] is True
    assert payload["replay_of"] == "a"


def _random_output(rng: random.Random) -> dict[str, Any]:
    contract = rng.choice(["c1", "c2", "mauvais", ""])
    pins: dict[str, Any] = {} if rng.random() < 0.2 else {"out": {"contract": contract}}
    out: dict[str, Any] = {"pins": pins}
    if rng.random() < 0.3:
        out["extra"] = rng.randint(0, 3)
    return out


Step = tuple[bool, bool, bool, str | None, str | None]


def _model(outputs: list[dict[str, Any]]) -> list[Step]:
    """Oracle indépendant du moteur : la spécification W1-03 écrite à la main."""
    order = ["a", "b", "c"]
    expected = {"a": "c1", "b": "c2"}
    idx = 0
    accepted: list[tuple[str, dict[str, Any]]] = []
    trace: list[Step] = []
    for output in outputs:
        replay_of = next((node for node, prior in accepted if prior == output), None)
        if idx == len(order):
            trace.append((False, False, False, None, "raised") if replay_of is None else (True, True, True, "c", replay_of))
            continue
        node = order[idx]
        pins = output["pins"]
        valid = node == "c" or pins.get("out", {}).get("contract") == expected[node]
        if valid:
            accepted.append((node, output))
            idx += 1
            trace.append((True, idx == len(order), False, None if idx == len(order) else order[idx], None))
        elif replay_of is not None:
            trace.append((True, False, True, node, replay_of))
        else:
            trace.append((False, False, False, node, None))
    return trace


def _scenario(tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch, seed: int) -> tuple[list[Step], list[Step]]:
    monkeypatch.setenv("GRIMOIRE_FLOWS_BACKEND", backend)
    rng = random.Random(seed)
    work = tmp_path / f"{backend}-{seed}"
    work.mkdir(parents=True, exist_ok=True)
    engine, run_id = _start(work, _blueprint(work))
    trace: list[Step] = []
    submitted: list[dict[str, Any]] = []
    for _ in range(7):
        output = rng.choice(submitted) if submitted and rng.random() < 0.5 else _random_output(rng)
        submitted.append(output)
        try:
            outcome = engine.resume(run_id, output=output)
        except GrimoireRuntimeError:
            trace.append((False, False, False, None, "raised"))
            continue
        trace.append((outcome.ok, outcome.finished, outcome.replay, outcome.node_id if not outcome.finished or outcome.replay else None, outcome.replay_of))
    return trace, _model(submitted)


@pytest.mark.parametrize("seed", range(100))
def test_replay_decisions_match_the_specification_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """100 scénarios aléatoires : le moteur (backend Python) suit l'oracle écrit à la main."""
    observed, expected = _scenario(tmp_path, "python", monkeypatch, seed)
    # Un run terminé rend `node_id=<dernier>` en rejeu ; l'oracle ne le nomme pas autrement.
    assert observed == expected


@requires_rust_core
@pytest.mark.parametrize("seed", range(100))
def test_replay_decisions_agree_across_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """100 cas : même sortie avec et sans le cœur Rust (job CI Rust)."""
    assert _scenario(tmp_path, "python", monkeypatch, seed) == _scenario(tmp_path, "rust", monkeypatch, seed)
