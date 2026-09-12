"""``DispatchExecutor`` — le moteur de flow (#204) piloté par la cascade (#311).

Mêmes fournisseurs factices que ``tests/unit/missions/test_dispatch.py`` :
de vrais scripts Python locaux, exécutés par de vrais ``subprocess`` — pas de
mock. Chaque script « intelligent » lit le prompt qu'il reçoit pour savoir
quel node il sert (le premier mot après ``FLOW-<run-id>-``) et où écrire sa
sortie (la ligne ``Écris ta sortie dans le fichier ...``) : un seul registre
de fournisseurs peut ainsi se comporter différemment selon le node, sans que
le kit n'ait besoin de le savoir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

from grimoire.flows.dispatch_executor import resume_with_dispatch, run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.runtime.schemas import WorkflowStatus

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
LEDGER = Path("_grimoire-runtime-output/ledger")

# ── Blueprint à trois nodes : a (V0) -> b (V0, rouge puis vert) -> c (V1) ────

_V0 = "la suite de tests passe"
_V1 = "revue humaine avant fusion"
_V2 = "le code est propre"

# Depuis l'issue #428 (suite), un V0 sans acceptance structurée est rétrogradé
# en V1 (jamais fermé sur la seule foi de l'ouvrier) — voir
# test_flows_verifiability_v0_requires_structured.py. Ces tests-ci couvrent la
# mécanique de cascade (escalade, checkpoints, reprise), pas cette règle :
# `{"run": "true"}` (toujours vert, sans effet de bord) garde les nodes "a" et
# "b" authentiquement V0, comme avant ce correctif. Ajouté même au blueprint
# du node V2 : un critère structuré supplémentaire ne fait pas redescendre un
# critère par ailleurs ambigu (verifiability.classify — un seul ambigu suffit).
_V0_STRUCTURED = {"run": "true"}


def _blueprint(tmp_path: Path, *, b_acceptance: str = _V0, c_acceptance: str = _V1) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes-dispatch",
        "name": "Trois nodes dispatch",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "A",
                "acceptance": [_V0, _V0_STRUCTURED],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b",
                "kind": "pattern",
                "ref": "QUA-04",
                "label": "B",
                "acceptance": [b_acceptance, _V0_STRUCTURED],
                "pins": [
                    {"id": "in", "direction": "in", "contract": "c1"},
                    {"id": "out", "direction": "out", "contract": "c2"},
                ],
            },
            {
                "id": "c",
                "kind": "pattern",
                "ref": "QUA-04",
                "label": "C",
                "acceptance": [c_acceptance],
                "pins": [{"id": "in", "direction": "in", "contract": "c2"}],
            },
        ],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "c1"},
            {"from": "b.out", "to": "c.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "trois-nodes.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


# ── Fournisseurs factices « intelligents » ───────────────────────────────────
# Chacun relit son propre prompt : le node servi (suffixe de la tâche
# ``FLOW-<run-id>-<node>``) et le fichier + les pins à écrire (rendus en toutes
# lettres par ``DispatchExecutor._find_or_create_task``).

_NODE_RE = r"Tâche \S+-(\w+) :"
_RESULT_RE = r"Écris ta sortie dans le fichier (\S+)"
_PIN_RE = r"^  - (\S+) : (\S+)$"

_WRITER = f"""\
    import re, json, sys
    prompt = sys.argv[1]
    node = re.search(r"{_NODE_RE}", prompt).group(1)
    if node in FAIL_FOR:
        sys.exit(0)  # ne rien écrire : le check échouera, la cascade passe au palier suivant
    path = re.search(r"{_RESULT_RE}", prompt).group(1)
    pins = {{m.group(1): {{"contract": m.group(2)}} for m in re.finditer(r"{_PIN_RE}", prompt, re.MULTILINE)}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({{"pins": pins}}, fh)
    """


def _writer_script(tmp_path: Path, name: str, *, fail_for: tuple[str, ...] = ()) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _WRITER.replace("FAIL_FOR", repr(fail_for))
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _invocation(script: Path) -> str:
    return f"{sys.executable} {script} {{prompt}} --model {{model}}"


def _provider_yaml(pid: str, tier: str, invocation: str) -> str:
    return (
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n'
        f'    allowed_capabilities: ["chat", "code"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f'    invocation: "{invocation}"\n'
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "{tier}"\n'
        f"    fallback_order: []\n"
    )


def _write_registry(root: Path, *providers_yaml: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    body = "\n".join(providers_yaml)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
{body}
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


def _setup_cheap_then_mid(tmp_path: Path, *, fail_for: tuple[str, ...]) -> None:
    """Un registre à deux paliers : ``cheap`` échoue pour *fail_for*, ``mid`` réussit toujours."""
    cheap = _writer_script(tmp_path, "cheap.py", fail_for=fail_for)
    mid = _writer_script(tmp_path, "mid.py", fail_for=())
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )


# ── 1. Cascade verte sur les trois nodes, avec checkpoints ───────────────────


def test_cascade_verte_enchaine_les_trois_nodes_avec_checkpoints(tmp_path: Path) -> None:
    _setup_cheap_then_mid(tmp_path, fail_for=())
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    assert [n.node_id for n in outcome.nodes] == ["a", "b", "c"]
    assert all(n.verdict == "green" for n in outcome.nodes)
    assert outcome.escalations == 0

    # Un checkpoint par node fait : le kernel a bien avancé node par node.
    status = engine.status(outcome.run_id)
    assert status.status == WorkflowStatus.COMPLETED.value
    assert status.completed_nodes == ("a", "b", "c")


# ── 2. Node rouge en cheap, vert en mid : escalade comptée ───────────────────


def test_node_rouge_en_cheap_puis_vert_en_mid_compte_une_escalade(tmp_path: Path) -> None:
    _setup_cheap_then_mid(tmp_path, fail_for=("b",))
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    by_node = {n.node_id: n for n in outcome.nodes}
    assert by_node["a"].escalations == 0
    assert by_node["a"].provider == "cheap-writer"
    assert by_node["b"].escalations == 1
    assert by_node["b"].attempts == 2
    assert by_node["b"].provider == "mid-writer"
    assert outcome.escalations == 1


# ── 3. Node V2 : suspension nommant le node, rien dispatché ──────────────────


def test_node_v2_suspend_le_run_en_nommant_le_node(tmp_path: Path) -> None:
    _setup_cheap_then_mid(tmp_path, fail_for=())
    bp = _blueprint(tmp_path, b_acceptance=_V2)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "waiting_host"
    assert outcome.node_id == "b"
    assert outcome.host_reason is not None
    assert "V2" in outcome.host_reason
    # 'a' a bien été dispatché avant que 'b' n'arrête la cascade.
    assert [n.node_id for n in outcome.nodes] == ["a"]

    # Le run reste ouvert au node 'b' — CHECKPOINTED entre deux nodes, l'état
    # qu'un `flow resume` interactif rouvre exactement de la même façon
    # qu'après un crash (voir ``FlowEngine.resume``).
    status = engine.status(outcome.run_id)
    assert status.status == WorkflowStatus.CHECKPOINTED.value
    assert status.current_node == "b"


# ── 4. V1 vert : le node est marqué à relire ─────────────────────────────────


def test_v1_vert_marque_le_node_a_relire(tmp_path: Path) -> None:
    _setup_cheap_then_mid(tmp_path, fail_for=())
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    by_node = {n.node_id: n for n in outcome.nodes}
    assert by_node["c"].verifiability == "V1"
    assert by_node["c"].needs_review is True
    assert by_node["a"].needs_review is False

    # Le ledger le confirme : la tâche du node 'c' est passée en revue.
    service = TaskService(tmp_path, LEDGER)
    task = service.require(by_node["c"].task_id)
    assert task.status is TaskState.NEEDS_VERIFICATION


# ── 5. Reprise après crash simulé au node courant, --executor dispatch ───────


def test_reprise_apres_crash_relance_le_node_courant_avec_dispatch(tmp_path: Path) -> None:
    _setup_cheap_then_mid(tmp_path, fail_for=())
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    # Démarre en interactif : le node 'a' est ouvert, jamais répondu — un
    # « crash » avant que l'hôte ne revienne avec --result laisse le run
    # exactement dans cet état (RUNNING, node 'a' courant).
    wfi, contract = engine.run(bp)
    assert contract.node_id == "a"
    assert engine.status(wfi.id).status == WorkflowStatus.RUNNING.value

    outcome = resume_with_dispatch(engine, wfi.id, project_root=tmp_path)

    assert outcome.status == "finished"
    assert [n.node_id for n in outcome.nodes] == ["a", "b", "c"]

    status = engine.status(wfi.id)
    assert status.status == WorkflowStatus.COMPLETED.value


def test_reprise_apres_crash_entre_deux_nodes_ne_rejoue_pas_le_node_fait(tmp_path: Path) -> None:
    """Le node 'a', déjà vert via l'hôte, ne doit pas être redispatché — seul 'b' l'est."""
    _setup_cheap_then_mid(tmp_path, fail_for=())
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    wfi, _ = engine.run(bp)
    step1 = engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
    assert step1.node_id == "b"  # 'b' ouvert, jamais avancé : l'état d'un crash

    outcome = resume_with_dispatch(engine, wfi.id, project_root=tmp_path)

    assert outcome.status == "finished"
    # 'a' n'a jamais été redispatché par cette reprise : aucune tâche
    # FLOW-...-a dans le rapport, seuls 'b' et 'c' le sont.
    assert [n.node_id for n in outcome.nodes] == ["b", "c"]

    status = engine.status(wfi.id)
    assert status.status == WorkflowStatus.COMPLETED.value
    assert status.completed_nodes == ("a", "b", "c")


# ── Le check mécanique relit le même contrat que ``flow resume`` ────────────


def test_verify_cli_valide_le_meme_contrat_que_flow_resume(tmp_path: Path) -> None:
    from grimoire.flows.dispatch_executor import main as verify_main

    bp = _blueprint(tmp_path)
    result_path = tmp_path / "out.json"
    result_path.write_text(json.dumps({"pins": {"out": {"contract": "c1"}}}), encoding="utf-8")

    assert verify_main(["--verify", str(bp), "a", str(result_path)]) == 0

    result_path.write_text(json.dumps({"pins": {"out": {"contract": "faux"}}}), encoding="utf-8")
    assert verify_main(["--verify", str(bp), "a", str(result_path)]) == 1
