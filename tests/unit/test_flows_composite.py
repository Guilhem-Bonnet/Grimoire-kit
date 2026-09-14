"""``kind: "composite"`` — un flow est un node (lot 3, issue #206).

Mêmes fournisseurs factices que ``tests/unit/test_flows_dispatch_executor.py``
et ``tests/unit/test_flows_dispatch_executor_pilot.py`` : de vrais scripts
Python locaux, exécutés par de vrais ``subprocess`` — jamais un mock, jamais
un appel réseau.

Décision de conception documentée dans la PR : ce lot donne des sémantiques
d'exécution réelles au ``kind: "composite"`` déjà réservé par le schéma
(``schemas/blueprint-v1.schema.json``, jusqu'ici seulement validé par le
compilateur Studio/``forge_server.py``, jamais exécuté par le moteur de
flows) plutôt que d'introduire un second ``kind`` redondant.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import MAX_COMPOSITE_DEPTH, load_blueprint
from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.flows.extract import extract_blueprint
from grimoire.flows.registry import describe_flow
from grimoire.runtime.schemas import WorkflowStatus

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
PILOT_POLICY = STANDARD / "pilot.yaml"

# ── Fournisseurs factices « intelligents » (mêmes principes que les autres
#    suites de tests dispatch) : le script lit son propre prompt pour savoir
#    quel node il sert et où écrire sa sortie, et annonce un coût fixe.

_NODE_RE = r"Tâche \S+-(\w+) :"
_RESULT_RE = r"Écris ta sortie dans le fichier (\S+)"
_PIN_RE = r"^  - (\S+) : (\S+)$"

_WRITER = f"""\
    import re, json, sys
    prompt = sys.argv[1]
    path = re.search(r"{_RESULT_RE}", prompt).group(1)
    pins = {{m.group(1): {{"contract": m.group(2)}} for m in re.finditer(r"{_PIN_RE}", prompt, re.MULTILINE)}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({{"pins": pins}}, fh)
    print(json.dumps({{"total_cost_usd": COST}}))
    """


def _writer_script(tmp_path: Path, name: str, *, cost: float) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(_WRITER).replace("COST", repr(cost)), encoding="utf-8")
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


def _write_pilot_policy(root: Path, *, max_cost_usd_per_node: float) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / PILOT_POLICY).write_text(
        f"max_cost_usd_per_node: {max_cost_usd_per_node}\n", encoding="utf-8"
    )


def _setup_single_tier(tmp_path: Path, *, cost: float, pid: str = "cheap-writer") -> None:
    script = _writer_script(tmp_path, f"{pid}.py", cost=cost)
    _write_registry(tmp_path, _provider_yaml(pid, "cheap", _invocation(script)))


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows", project_root=tmp_path)


def _leaf_node(node_id: str, *, out_contract: str | None = None, in_contract: str | None = None) -> dict:
    pins = []
    if in_contract:
        pins.append({"id": "in", "direction": "in", "contract": in_contract})
    if out_contract:
        pins.append({"id": "out", "direction": "out", "contract": out_contract})
    return {
        "id": node_id,
        "kind": "pattern",
        "ref": "ORC-01",
        "label": node_id,
        "acceptance": [{"run": "true"}],
        "pins": pins,
    }


def _child_blueprint(path: Path, *, blueprint_id: str = "child-flow", extra_node: dict | None = None) -> Path:
    nodes = [_leaf_node("c1", out_contract="cc"), _leaf_node("c2", in_contract="cc")]
    if extra_node is not None:
        nodes.append(extra_node)
    blueprint = {
        "blueprintVersion": 1,
        "id": blueprint_id,
        "name": "Child flow",
        "nodes": nodes,
        "edges": [{"from": "c1.out", "to": "c2.in", "contract": "cc"}],
    }
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _parent_blueprint(tmp_path: Path, *, ref: str, composite_pins: list[dict] | None = None) -> Path:
    composite_pins = composite_pins if composite_pins is not None else [{"id": "out", "direction": "out", "contract": "c2"}]
    blueprint = {
        "blueprintVersion": 1,
        "id": "parent-flow",
        "name": "Parent flow",
        "nodes": [
            _leaf_node("pre", out_contract="c1"),
            {
                "id": "sub",
                "kind": "composite",
                "ref": ref,
                "label": "sub-flow",
                "pins": [{"id": "in", "direction": "in", "contract": "c1"}, *composite_pins],
            },
            _leaf_node("post", in_contract="c2"),
        ],
        "edges": [
            {"from": "pre.out", "to": "sub.in", "contract": "c1"},
            {"from": "sub.out", "to": "post.in", "contract": "c2"},
        ],
    }
    path = tmp_path / "parent-flow.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


# ── 1. Composition à deux niveaux : run propre, coût remonté au parent ──────


def test_composite_lance_un_sous_flow_avec_son_propre_run_et_remonte_le_cout(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path, cost=0.1)
    child_path = _child_blueprint(tmp_path / "child-flow.blueprint.json")
    parent_path = _parent_blueprint(tmp_path, ref="child-flow.blueprint.json")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, parent_path, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    by_node = {n.node_id: n for n in outcome.nodes}
    assert by_node["pre"].verdict == "green"
    assert by_node["post"].verdict == "green"
    sub = by_node["sub"]
    assert sub.verdict == "green"
    assert sub.acceptance_status == "composite"
    assert sub.child_run_id is not None
    # Le sous-flow a deux nodes propres, chacun coûtant 0.1 : le coût du node
    # composite EST le total du sous-flow, jamais un chiffre à part.
    assert sub.cost_usd == pytest.approx(0.2)
    # Le coût total du run parent inclut celui du composite : pre + sub + post.
    assert outcome.total_cost_usd == pytest.approx(0.4)

    # Le run enfant existe pour de vrai, lié au parent, visible du même engine.
    child_status = engine.status(sub.child_run_id)
    assert child_status.status == WorkflowStatus.COMPLETED.value
    assert child_status.blueprint_id == "child-flow"
    child_meta = engine.run_meta(sub.child_run_id)
    assert child_meta.parent_run_id == outcome.run_id
    assert child_meta.parent_node_id == "sub"
    assert Path(child_meta.blueprint_path) == child_path.resolve() or Path(child_meta.blueprint_path) == child_path


# ── 2. Cycle de composition refusé au chargement ────────────────────────────


def test_cycle_de_composition_refuse_au_chargement(tmp_path: Path) -> None:
    a_path = tmp_path / "a.blueprint.json"
    b_path = tmp_path / "b.blueprint.json"
    a_path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "a-flow",
                "nodes": [{"id": "n", "kind": "composite", "ref": "b.blueprint.json", "pins": []}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    b_path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "b-flow",
                "nodes": [{"id": "n", "kind": "composite", "ref": "a.blueprint.json", "pins": []}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GrimoireRuntimeError, match="cycle de composition"):
        load_blueprint(a_path, tmp_path)


# ── 3. Référence introuvable refusée au chargement ──────────────────────────


def test_ref_composite_introuvable_refusee_au_chargement(tmp_path: Path) -> None:
    parent_path = _parent_blueprint(tmp_path, ref="absent.blueprint.json")

    with pytest.raises(GrimoireRuntimeError, match="introuvable"):
        load_blueprint(parent_path, tmp_path)


# ── 4. Profondeur de composition bornée à 3 ─────────────────────────────────


def _chain_blueprint(path: Path, *, blueprint_id: str, next_ref: str | None) -> Path:
    if next_ref is None:
        nodes = [_leaf_node("leaf")]
    else:
        nodes = [{"id": "n", "kind": "composite", "ref": next_ref, "pins": []}]
    path.write_text(
        json.dumps({"blueprintVersion": 1, "id": blueprint_id, "nodes": nodes, "edges": []}), encoding="utf-8"
    )
    return path


def test_profondeur_de_composition_dependant_max_trois_est_refusee(tmp_path: Path) -> None:
    assert MAX_COMPOSITE_DEPTH == 3
    level4 = _chain_blueprint(tmp_path / "level4.blueprint.json", blueprint_id="level4", next_ref=None)
    level3 = _chain_blueprint(tmp_path / "level3.blueprint.json", blueprint_id="level3", next_ref="level4.blueprint.json")
    level2 = _chain_blueprint(tmp_path / "level2.blueprint.json", blueprint_id="level2", next_ref="level3.blueprint.json")
    level1 = _chain_blueprint(tmp_path / "level1.blueprint.json", blueprint_id="level1", next_ref="level2.blueprint.json")
    del level4, level3, level2

    with pytest.raises(GrimoireRuntimeError, match="profondeur"):
        load_blueprint(level1, tmp_path)


# ── 5. ``use-case:`` refusé par le moteur de flows (réservé au Studio) ──────


def test_ref_use_case_refusee_par_le_moteur_de_flows(tmp_path: Path) -> None:
    parent_path = _parent_blueprint(tmp_path, ref="use-case:some-catalogue-flow")

    with pytest.raises(GrimoireRuntimeError, match="use-case"):
        load_blueprint(parent_path, tmp_path)


# ── 6. Le plafond de coût du pilote s'applique au TOTAL du sous-flow ────────


def test_plafond_du_pilote_arrete_le_sous_flow_sur_le_total(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path, cost=0.3)
    _write_pilot_policy(tmp_path, max_cost_usd_per_node=0.5)
    # Trois nodes à 0.3 chacun : 0.3 (sous plafond) -> 0.6 (dépasse 0.5) -> arrêt.
    extra = _leaf_node("c3", in_contract="cc")
    extra["pins"] = [{"id": "in2", "direction": "in", "contract": "cc-bis"}]
    child_path = tmp_path / "child-flow.blueprint.json"
    child_path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "child-flow",
                "nodes": [
                    _leaf_node("c1", out_contract="cc"),
                    _leaf_node("c2", out_contract="cc2", in_contract="cc"),
                    _leaf_node("c3", in_contract="cc2"),
                ],
                "edges": [
                    {"from": "c1.out", "to": "c2.in", "contract": "cc"},
                    {"from": "c2.out", "to": "c3.in", "contract": "cc2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    parent_path = _parent_blueprint(tmp_path, ref="child-flow.blueprint.json")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, parent_path, project_root=tmp_path)

    assert outcome.status == "blocked", outcome.to_dict()
    assert outcome.node_id == "sub"
    by_node = {n.node_id: n for n in outcome.nodes}
    assert by_node["sub"].verdict == "cost_capped"
    # Un seul node de plus après le dépassement : jamais un vert déjà acquis
    # qui serait rétracté, mais aucune tentative après le dépassement.
    assert by_node["sub"].cost_usd == pytest.approx(0.6)

    child_run_id = by_node["sub"].child_run_id
    assert child_run_id is not None
    child_status = engine.status(child_run_id)
    assert child_status.status == WorkflowStatus.ABORTED.value


# ── 7. ``flow extract`` : le node composite n'est jamais aplati ─────────────


def test_flow_extract_ne_flatten_jamais_le_node_composite(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path, cost=0.1)
    _child_blueprint(tmp_path / "child-flow.blueprint.json")
    parent_path = _parent_blueprint(tmp_path, ref="child-flow.blueprint.json")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, parent_path, project_root=tmp_path)
    assert outcome.status == "finished"
    sub_outcome = next(n for n in outcome.nodes if n.node_id == "sub")

    draft, trace = extract_blueprint(engine, outcome.run_id, tmp_path)

    draft_ids = [n["id"] for n in draft["nodes"]]
    assert draft_ids == ["pre", "sub", "post"]  # jamais remplacé par c1/c2
    sub_draft = next(n for n in draft["nodes"] if n["id"] == "sub")
    assert sub_draft["kind"] == "composite"
    assert sub_draft["ref"] == "child-flow.blueprint.json"
    assert sub_draft["extraction"]["status"] == "completed"
    assert sub_draft["extraction"]["child_run_id"] == sub_outcome.child_run_id

    sub_trace = next(n for n in trace if n.node_id == "sub")
    assert sub_trace.child_run_id == sub_outcome.child_run_id

    # Le brouillon reste un blueprint v1 chargeable — la composition n'y a
    # rien cassé (le fichier n'est écrit sur disque que par la CLI, voir
    # ``cmd_flow.flow_extract`` ; ici on revalide le dict rendu directement).
    draft_path = tmp_path / f"{draft['id']}.blueprint.json"
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    reloaded = load_blueprint(draft_path, tmp_path)
    assert reloaded["id"] == draft["id"]


# ── 8. Le registre local (issue #206) : version, compat, intégrité, besoins ─


def test_describe_flow_expose_version_compat_hash_et_besoins_agreges(tmp_path: Path) -> None:
    child_path = tmp_path / "child-flow.blueprint.json"
    child_blueprint = {
        "blueprintVersion": 1,
        "id": "child-flow",
        "nodes": [
            {
                "id": "c1",
                "kind": "pattern",
                "ref": "ORC-01",
                "acceptance": [{"run_need": "test-runner"}],
                "pins": [],
            }
        ],
        "edges": [],
    }
    child_path.write_text(json.dumps(child_blueprint), encoding="utf-8")

    parent_path = tmp_path / "parent-flow.blueprint.json"
    parent_blueprint = {
        "blueprintVersion": 1,
        "id": "parent-flow",
        "version": "1.2.0",
        "kitMin": "3.40.0",
        "kitMax": "3.99.0",
        "nodes": [
            {
                "id": "p1",
                "kind": "pattern",
                "ref": "ORC-01",
                "acceptance": [{"run_need": "lint"}],
                "pins": [],
            },
            {"id": "sub", "kind": "composite", "ref": "child-flow.blueprint.json", "pins": []},
        ],
        "edges": [],
    }
    parent_path.write_text(json.dumps(parent_blueprint), encoding="utf-8")

    info = describe_flow(parent_path, tmp_path)

    assert info.blueprint_id == "parent-flow"
    assert info.version == "1.2.0"
    assert info.kit_min == "3.40.0"
    assert info.kit_max == "3.99.0"
    assert info.required_needs == ("lint", "test-runner")  # union, triée, incluant le sous-flow
    assert info.integrity_sha256 == "sha256:" + hashlib.sha256(parent_path.read_bytes()).hexdigest()


def test_describe_flow_sans_version_ni_compat_retombe_sur_les_defauts(tmp_path: Path) -> None:
    path = tmp_path / "solo.blueprint.json"
    path.write_text(
        json.dumps({"blueprintVersion": 1, "id": "solo-flow", "nodes": [_leaf_node("n")], "edges": []}),
        encoding="utf-8",
    )
    info = describe_flow(path, tmp_path)
    assert info.version == "0.0.0"
    assert info.kit_min is None
    assert info.kit_max is None
    assert info.required_needs == ()


# ── 9. ``ref`` composite = id nu résolu par le registre local ──────────────


def test_ref_composite_id_nu_resolue_par_le_registre_local(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path, cost=0.1)
    registry_dir = tmp_path / "registry" / "blueprints"
    registry_dir.mkdir(parents=True)
    _child_blueprint(registry_dir / "child-flow.blueprint.json")
    parent_path = _parent_blueprint(tmp_path, ref="child-flow")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, parent_path, project_root=tmp_path)

    assert outcome.status == "finished", outcome.to_dict()
    sub = next(n for n in outcome.nodes if n.node_id == "sub")
    assert sub.verdict == "green"


# ── 10. flow list --require-measure couvre le sous-flow (issue #473, aucun code neuf) ─


def test_flow_list_require_measure_couvre_le_sous_flow(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    _setup_single_tier(tmp_path, cost=0.1)
    _child_blueprint(tmp_path / "child-flow.blueprint.json")
    parent_path = _parent_blueprint(tmp_path, ref="child-flow.blueprint.json")
    engine = _engine(tmp_path)
    outcome = run_with_dispatch(engine, parent_path, project_root=tmp_path)
    assert outcome.status == "finished"

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--output", "json", "flow", "list", "--project-root", str(tmp_path), "--require-measure", "child-flow"],
    )
    assert result.exit_code == 0, result.output
