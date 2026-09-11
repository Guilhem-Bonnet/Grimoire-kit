"""Cross-backend parity for la machine à états du moteur de flows (issue #354).

``grimoire.flows.engine`` (``check_output_against_contract``,
``FlowEngine._current_node``, la décision de ``resume()`` post-contrat, le
découpage completed/pending de ``status()``) et ``grimoire.runtime.kernel``
(``RuntimeKernel._transition``, la précondition de ``advance_step``)
délèguent optionnellement à un cœur Rust compilé par PyO3
(``rust/grimoire-flows-core/``) quand il est importable, et retombent sinon
sur l'implémentation Python pure — voir les docstrings de ``engine.py`` et
``kernel.py`` pour la bascule ``GRIMOIRE_FLOWS_BACKEND`` que ce fichier
utilise.

``tests/unit/test_flows_engine.py`` (le contrat de ``FlowEngine``) et
``tests/unit/test_runtime.py`` (le contrat de ``RuntimeKernel``) restent le
golden test : ils tournent tels quels sous les deux backends (voir
``.github/workflows/rust-cores.yml``). Ce fichier ajoute :

1. La grille complète des transitions (9×9 statuts), comparée au dict Python
   de référence.
2. Un défaut réel trouvé côté Python en portant cette grille vers Rust :
   ``FlowEngine._TERMINAL_STATUSES`` omettait ``REFUSED`` — un statut
   pourtant déjà terminal côté ``RuntimeKernel`` (aucune transition
   sortante). Corrigé dans cette même PR ; ce fichier prouve le correctif et
   la parité entre les deux backends désormais alignés.
3. Un comportement délibérément **non changé**, documenté plutôt que
   « corrigé » : ``FlowEngine.status()`` dérive ``completed_nodes`` de la
   position de ``current_node`` dans l'ordre topologique — sur un run
   ABORTED avant tout progrès, cela affiche tous les nodes comme complétés.
   Les deux backends reproduisent ce comportement à l'identique (voir le
   docstring de ``rust/grimoire-flows-core/src/lib.rs`` pour pourquoi ce
   n'est pas corrigé ici).
4. Un statut inconnu dans des métadonnées persistées à la main : les deux
   backends refusent (jamais un défaut silencieux), documenté plutôt que
   changé.
5. Une relecture d'un corpus réel de blueprints livrés
   (``registry/blueprints/*.blueprint.json``) sur la grille de transitions
   complète (run → resume × N → status), sous les deux backends.

Quand le module compilé n'est pas installé (l'environnement contributeur par
défaut, et le job CI normal), les tests marqués ``requires_rust_core`` sont
sautés plutôt qu'échoués. Le job CI dédié installe le cœur en premier et est
là où ce fichier exerce vraiment les deux côtés.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine, check_output_against_contract, rust_backend_available
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.schemas import NodeContract, PinRef
from grimoire.runtime.kernel import _WF_TRANSITIONS, RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext, WorkflowStatus

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_flows_core not installed — build it locally (maturin develop) or run the Rust CI job",
)


def _with_backend(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_FLOWS_BACKEND", backend)


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


def _write_three_node_blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes",
        "name": "Trois nodes",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "label": "A", "pins": [{"id": "out", "direction": "out", "contract": "c1"}]},
            {
                "id": "b",
                "kind": "extension-node",
                "ref": "demo/demo-node",
                "label": "B",
                "pins": [{"id": "in", "direction": "in", "contract": "c1"}, {"id": "out", "direction": "out", "contract": "c2"}],
            },
            {"id": "c", "kind": "pattern", "ref": "QUA-04", "label": "C", "pins": [{"id": "in", "direction": "in", "contract": "c2"}]},
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


# ── Grille complète des transitions (9×9), contre la table Python ─────────

_ALL_STATUSES: tuple[WorkflowStatus, ...] = tuple(WorkflowStatus)


@requires_rust_core
@pytest.mark.parametrize("from_status", _ALL_STATUSES)
@pytest.mark.parametrize("to_status", _ALL_STATUSES)
def test_transition_grid_agrees_with_kernel_table(from_status: WorkflowStatus, to_status: WorkflowStatus) -> None:
    import grimoire_flows_core as rust_core

    expected = to_status in _WF_TRANSITIONS.get(from_status, frozenset())
    actual = bool(rust_core.can_transition_status(from_status.value, to_status.value))
    assert actual == expected, f"{from_status.value} -> {to_status.value}"


@requires_rust_core
@pytest.mark.parametrize("status", _ALL_STATUSES)
def test_allowed_transitions_from_agrees_with_kernel_table(status: WorkflowStatus) -> None:
    import grimoire_flows_core as rust_core

    expected = {s.value for s in _WF_TRANSITIONS.get(status, frozenset())}
    actual = set(rust_core.allowed_transitions_from(status.value))
    assert actual == expected


def test_kernel_transition_agrees_across_backends_on_every_legal_and_illegal_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercice de bout en bout de ``RuntimeKernel._transition`` (pas
    seulement la table brute) sous les deux backends, sur chaque paire
    (from, to) — légale ou non."""
    for from_status in _ALL_STATUSES:
        for to_status in _ALL_STATUSES:
            expected_legal = to_status in _WF_TRANSITIONS.get(from_status, frozenset())
            for backend in ("python", "rust") if rust_backend_available() else ("python",):
                _with_backend(backend, monkeypatch)
                kernel = RuntimeKernel(tmp_path / f"k-{backend}-{from_status.value}-{to_status.value}")
                ctx = ExecutionContext(
                    run_id="RUN-x", mission_id="MIS-x", task_id="TASK-x", workflow_instance_id="", actor_id="cli", host_id="local",
                    risk_profile="standard",
                )
                wfi = kernel.create_instance(ctx, recipe_id="demo")
                # Place l'instance dans `from_status` en passant par
                # `_transition` directement (accès à l'API privée : c'est
                # exactement la fonction sous test).
                object.__setattr__(wfi, "status", from_status)
                if expected_legal:
                    updated = kernel._transition(wfi, to_status)
                    assert updated.status is to_status
                else:
                    with pytest.raises(GrimoireRuntimeError):
                        kernel._transition(wfi, to_status)


# ── check_output_against_contract : cas limites, deux backends ────────────


def _contract(node_id: str, outputs: tuple[PinRef, ...]) -> NodeContract:
    return NodeContract(
        node_id=node_id, kind="pattern", label=node_id, description="", inputs=(), outputs=outputs, tool_boundary=(), acceptance=()
    )


_CONTRACT_CASES: tuple[tuple[NodeContract, dict[str, Any]], ...] = (
    (_contract("a", (PinRef("out", "c1"),)), {"pins": {"out": {"contract": "c1"}}}),  # conforme
    (_contract("a", ()), {"pins": {}}),  # contrat vide, sortie vide
    (_contract("a", ()), {"pins": {"bonus": {"contract": "x"}}}),  # contrat vide, pin en trop ignorée
    (_contract("a", (PinRef("out", "c1"),)), {"pins": {}}),  # pin manquante
    (_contract("a", (PinRef("out", "c1"),)), {}),  # sortie sans objet 'pins'
    (_contract("a", (PinRef("out", "c1"),)), {"pins": None}),  # 'pins' n'est pas un dict
    (_contract("a", (PinRef("out", "c1"),)), None),  # sortie elle-même None
    (_contract("a", (PinRef("out", "c1"),)), "pas un dict"),  # sortie elle-même pas un dict
    (_contract("a", (PinRef("out", "c1"),)), {"pins": {"out": None}}),  # pin présente mais pas un dict
    (_contract("a", (PinRef("out", "c1"),)), {"pins": {"out": {"contract": None}}}),  # contrat produit None
    (_contract("a", (PinRef("out", "c1"),)), {"pins": {"out": {"contract": "mauvais"}}}),  # mauvais contrat
    (
        _contract("a", (PinRef("x", "c1"), PinRef("y", "c2"))),
        {"pins": {"x": {"contract": "c1"}}},
    ),  # une pin sur deux manquante
)


@requires_rust_core
@pytest.mark.parametrize(("contract", "output"), _CONTRACT_CASES)
def test_check_output_against_contract_agrees_across_backends(
    monkeypatch: pytest.MonkeyPatch, contract: NodeContract, output: dict[str, Any]
) -> None:
    _with_backend("python", monkeypatch)
    python_faults = check_output_against_contract(contract, output)
    _with_backend("rust", monkeypatch)
    rust_faults = check_output_against_contract(contract, output)
    assert python_faults == rust_faults


def test_no_optional_pin_concept_exists_every_declared_output_is_mandatory() -> None:
    """Le cadrage de ce port évoque une "pin déclarée optionnelle" comme cas
    limite — ``NodeContract``/``PinRef`` (``grimoire/flows/schemas.py``) ne
    portent aucun champ ``optional`` : chaque pin de sortie déclarée par un
    node est vérifiée sans condition (voir ``check_output_against_contract``
    ci-dessus). Ce test fige cette absence plutôt que de fabriquer une
    fonctionnalité qui n'existe pas : un contrat sans aucune pin de sortie
    (déjà couvert ci-dessus) est le seul équivalent actuel de "rien à
    vérifier"."""
    contract = _contract("a", (PinRef("out", "c1"),))
    # Omettre la pin ne la rend jamais optionnelle : c'est un défaut nommé.
    assert check_output_against_contract(contract, {"pins": {}}) == ["node=a pin=out : absente de la sortie soumise"]


# ── Le défaut trouvé et corrigé : REFUSED absent de _TERMINAL_STATUSES ─────


def _run_to_refused(tmp_path: Path, engine: FlowEngine) -> tuple[Any, str]:
    bp = _write_three_node_blueprint(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    ctx = ExecutionContext(
        run_id=wfi.run_id,
        mission_id=wfi.mission_id,
        task_id=wfi.task_id,
        workflow_instance_id=wfi.id,
        actor_id="cli",
        host_id="local",
        risk_profile="standard",
    )
    kernel = engine._kernel
    inst = kernel.get_instance(wfi.id)
    assert inst is not None
    for _ in range(inst.max_tool_calls + 1):
        kernel.mediate_tool("demo-tool", {}, ctx, wfi.id)
    refused = kernel.get_instance(wfi.id)
    assert refused is not None and refused.status is WorkflowStatus.REFUSED
    return wfi, wfi.id


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_refused_run_is_terminal_to_resume_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """Le défaut trouvé en portant la grille de transitions vers Rust (voir
    le docstring du module) : avant correctif, ``resume()`` sur un run
    REFUSED levait bien une erreur mais avec le message générique de
    ``RuntimeKernel.advance_step`` (pas le message nommé de ``resume()``),
    et ``status()`` renvoyait un ``current_node``/``pending_nodes`` périmés
    comme si le run continuait. Corrigé pour les deux backends : ce test
    prouve le même verdict, le même message, sous ``python`` ET ``rust``."""
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    engine = _engine(tmp_path)
    _wfi, run_id = _run_to_refused(tmp_path, engine)

    with pytest.raises(GrimoireRuntimeError) as excinfo:
        engine.resume(run_id, output={"pins": {"out": {"contract": "c1"}}})
    assert f"run {run_id} est refused, rien à reprendre" in str(excinfo.value)

    status = engine.status(run_id)
    assert status.status == WorkflowStatus.REFUSED.value
    assert status.current_node is None
    assert status.pending_nodes == ()


# ── Documenté, pas corrigé : découpage completed/pending sur un abandon précoce ──


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_aborted_run_status_slicing_documented_not_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """Comportement volontairement NON changé (voir le docstring de
    ``rust/grimoire-flows-core/src/lib.rs``) : un run ABORTED avant tout
    progrès a ``current_node is None`` (terminal), et ``status()`` dérive
    ``completed_nodes`` de la position de ``current_node`` dans l'ordre —
    ``None`` retombe sur ``idx = len(order)``, donc ``completed_nodes``
    affiche TOUS les nodes comme faits alors qu'aucun ne l'est. Les deux
    backends reproduisent ce comportement à l'identique ; ce test fige la
    parité, pas une correction."""
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    engine = _engine(tmp_path)
    bp = _write_three_node_blueprint(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))

    before_abort = engine.status(wfi.id)
    assert before_abort.current_node == "a"
    assert before_abort.completed_nodes == ()

    engine.abort(wfi.id, reason="test")
    after_abort = engine.status(wfi.id)
    assert after_abort.status == WorkflowStatus.ABORTED.value
    assert after_abort.current_node is None
    # Le défaut documenté : "tout fait" alors que rien ne l'est.
    assert after_abort.completed_nodes == ("a", "b", "c")
    assert after_abort.pending_nodes == ()


# ── Documenté, pas corrigé : statut inconnu, refus explicite des deux côtés ──


def test_unknown_status_string_rejected_by_python_reference() -> None:
    with pytest.raises(ValueError):
        WorkflowStatus("bogus-status")


@requires_rust_core
def test_unknown_status_string_rejected_by_both_backends() -> None:
    """Un statut inconnu dans des métadonnées persistées à la main
    (``instances.jsonl`` édité) fait déjà lever une ``ValueError`` non
    rattrapée côté Python (``WorkflowStatus(d["status"])`` dans
    ``WorkflowInstance.from_dict``) — jamais un comportement par défaut
    silencieux. Le cœur Rust fait de même (``Err``, jamais une variante par
    défaut) : les deux backends refusent, documenté plutôt que changé."""
    import grimoire_flows_core as rust_core

    with pytest.raises(ValueError):
        WorkflowStatus("bogus-status")
    with pytest.raises(ValueError):
        rust_core.parse_workflow_status("bogus-status")
    with pytest.raises(ValueError):
        rust_core.is_flow_terminal_status("bogus-status")
    with pytest.raises(ValueError):
        rust_core.current_node("bogus-status", ["a"], None, False, None, None)


# ── current_node / resume() / status() : cas limites de la grille ──────────


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_crash_with_no_events_yet_resolves_to_first_node_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Ordre vide : ``_current_node`` ne peut renvoyer que ``None`` — aucune
    exception, sur les deux backends."""
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    engine = _engine(tmp_path)
    wfi = engine._kernel.create_instance(
        ExecutionContext(
            run_id="RUN-empty",
            mission_id="MIS-x",
            task_id="TASK-x",
            workflow_instance_id="",
            actor_id="cli",
            host_id="local",
            risk_profile="standard",
        ),
        recipe_id="demo",
    )
    assert engine._current_node(wfi, []) is None


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_resume_twice_after_abort_raises_named_error_both_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """``abort()`` deux fois, puis ``resume()`` : refus nommé, jamais un
    comportement par défaut, sur les deux backends."""
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    engine = _engine(tmp_path)
    bp = _write_three_node_blueprint(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    engine.abort(wfi.id, reason="premier abandon")
    with pytest.raises(GrimoireRuntimeError):
        engine.abort(wfi.id, reason="deuxieme abandon")  # ABORTED -> ABORTED illégal
    with pytest.raises(GrimoireRuntimeError):
        engine.resume(wfi.id, output={"pins": {}})


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_resume_with_node_that_is_not_current_still_targets_the_real_current_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """``resume()`` ne prend jamais un ``node_id`` en argument : il est
    toujours recalculé depuis le kernel (``_current_node``), donc "reprendre
    avec un node qui n'est pas le courant" n'est pas représentable côté
    appelant — la sortie soumise est jugée contre le VRAI node courant, quel
    que soit ce que l'hôte croit reprendre. Ce test fige cette garantie sous
    les deux backends : une sortie conforme au node 'b' soumise alors que le
    run est encore sur 'a' est jugée contre 'a' (mauvais contrat) et bloque,
    plutôt que d'avancer sur 'b' par erreur."""
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    engine = _engine(tmp_path)
    bp = _write_three_node_blueprint(tmp_path)
    wfi, _ = engine.run(bp, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    # Sortie qui satisferait 'b' (contrat c2), soumise alors que 'a' est
    # encore le node courant (contrat c1 attendu) : jugée contre 'a'.
    outcome = engine.resume(wfi.id, output={"pins": {"out": {"contract": "c2"}}})
    assert not outcome.ok
    assert outcome.node_id == "a"


# ── Corpus réel : les blueprints livrés, rejoués sur la grille de transitions ──

_REGISTRY_BLUEPRINTS = Path(__file__).resolve().parents[2] / "registry" / "blueprints"


def _run_to_completion(engine: FlowEngine, blueprint_path: Path) -> Any:
    from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, topo_order

    blueprint = load_blueprint(blueprint_path)
    order = topo_order(blueprint)
    contracts = build_node_contracts(blueprint)

    wfi, _first_contract = engine.run(blueprint_path, executor=InteractiveNodeExecutor(stream=io.StringIO()))
    for node_id in order:
        contract = contracts[node_id]
        output = {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contract.outputs}}
        outcome = engine.resume(wfi.id, output=output, executor=InteractiveNodeExecutor(stream=io.StringIO()))
        assert outcome.ok, f"{node_id}: {outcome.faults}"
    return engine.status(wfi.id)


@pytest.mark.parametrize("blueprint_name", ["minimal.blueprint.json", "web-pipeline.blueprint.json"])
@pytest.mark.parametrize("backend", ["python", "rust"])
def test_shipped_blueprints_run_to_completion_on_both_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blueprint_name: str, backend: str
) -> None:
    if backend == "rust" and not rust_backend_available():
        pytest.skip("grimoire_flows_core not installed")
    _with_backend(backend, monkeypatch)
    blueprint_path = _REGISTRY_BLUEPRINTS / blueprint_name
    assert blueprint_path.is_file(), f"corpus manquant : {blueprint_path}"
    engine = _engine(tmp_path)
    final = _run_to_completion(engine, blueprint_path)
    assert final.status == WorkflowStatus.COMPLETED.value
    assert final.current_node is None
    assert final.pending_nodes == ()


@requires_rust_core
@pytest.mark.parametrize("blueprint_name", ["minimal.blueprint.json", "web-pipeline.blueprint.json"])
def test_shipped_blueprints_agree_across_backends_node_by_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blueprint_name: str
) -> None:
    """Même corpus, rejoué sous les deux backends dans le même test : les
    deux ``FlowStatusView`` finaux doivent être identiques champ à champ."""
    blueprint_path = _REGISTRY_BLUEPRINTS / blueprint_name

    _with_backend("python", monkeypatch)
    python_final = _run_to_completion(_engine(tmp_path / "python"), blueprint_path)

    _with_backend("rust", monkeypatch)
    rust_final = _run_to_completion(_engine(tmp_path / "rust"), blueprint_path)

    assert python_final.status == rust_final.status
    assert python_final.current_node == rust_final.current_node
    assert python_final.completed_nodes == rust_final.completed_nodes
    assert python_final.pending_nodes == rust_final.pending_nodes
