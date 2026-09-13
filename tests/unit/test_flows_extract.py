"""``flow extract`` — un flow s'extrait d'un run, il ne se dessine pas (issue #210).

Mêmes fournisseurs factices que ``test_flows_dispatch_executor.py`` : de vrais
scripts Python locaux exécutés par de vrais ``subprocess``, jamais un mock,
jamais un vrai fournisseur LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.flows.extract import extract_blueprint

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"

_V0_RUN = {"run": "true"}
_V1 = "revue humaine avant fusion"
_V2 = "le code est propre"

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
    """


def _writer_script(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(_WRITER), encoding="utf-8")
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


def _setup_single_tier(tmp_path: Path) -> None:
    """``cheap`` pour les nodes V0, ``mid`` pour les nodes V1 — deux paliers, un seul script."""
    cheap = _writer_script(tmp_path, "cheap.py")
    mid = _writer_script(tmp_path, "mid.py")
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )


def _blueprint(tmp_path: Path, *, c_acceptance: object = _V1) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "trois-nodes-extract",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "A",
                "acceptance": [_V0_RUN],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b",
                "kind": "pattern",
                "ref": "QUA-04",
                "label": "B",
                "acceptance": [_V0_RUN],
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


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows", project_root=tmp_path)


# ── 1. Run complet : acceptance observée remplace la commande en dur ────────


def test_extract_completed_run_replaces_run_with_observed_acceptance(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path)
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "finished"

    draft, trace = extract_blueprint(engine, outcome.run_id, tmp_path)

    by_id = {n.node_id: n for n in trace}
    assert by_id["a"].status == "completed"
    assert by_id["a"].verifiability == "V0"
    assert by_id["b"].status == "completed"
    assert by_id["c"].status == "completed"
    assert by_id["c"].verifiability == "V1"

    nodes = {n["id"]: n for n in draft["nodes"]}
    assert nodes["a"]["acceptance"] == [{"run": "true"}]
    assert nodes["b"]["acceptance"] == [{"run": "true"}]
    # V1 : jamais d'acceptance exécutée observée — la prose d'origine survit,
    # jamais remplacée par une inférence depuis le texte.
    assert nodes["c"]["acceptance"] == [_V1]
    assert nodes["a"]["extraction"] == {"status": "completed"}


# ── 2. Le brouillon extrait est un blueprint valide et rejouable ────────────


def test_extract_draft_is_valid_and_replayable_with_same_sequence(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path)
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)
    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    draft, _trace = extract_blueprint(engine, outcome.run_id, tmp_path)
    draft_path = tmp_path / f"{draft['id']}.blueprint.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    # ``load_blueprint`` est le même validateur structurel que ``flow run`` —
    # un brouillon qui ne passe pas ce contrôle ne serait pas rejouable.
    reloaded = load_blueprint(draft_path)
    assert reloaded["id"] == draft["id"]

    replay_engine = _engine(tmp_path)
    replay_outcome = run_with_dispatch(replay_engine, draft_path, project_root=tmp_path)
    assert replay_outcome.status == "finished"
    assert [n.node_id for n in replay_outcome.nodes] == [n.node_id for n in outcome.nodes]
    assert all(n.verdict == "green" for n in replay_outcome.nodes)


# ── 3. Besoin inféré uniquement sur correspondance caractère pour caractère ──


def test_extract_infers_run_need_on_exact_match_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "test_dummy.py").write_text("def test_ok() -> None:\n    assert True\n", encoding="utf-8")
    _setup_single_tier(tmp_path)
    blueprint = {
        "blueprintVersion": 1,
        "id": "besoin-infere",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": [{"run": "pytest -q"}],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    bp = tmp_path / "besoin.blueprint.json"
    bp.write_text(json.dumps(blueprint), encoding="utf-8")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "finished"

    draft, trace = extract_blueprint(engine, outcome.run_id, tmp_path)
    assert trace[0].needs_inferred == ("test-runner",)
    assert draft["nodes"][0]["acceptance"] == [{"run_need": "test-runner"}]


def test_extract_does_not_infer_when_observed_command_has_extra_args(tmp_path: Path) -> None:
    """``lint`` se résout en ``ruff check`` ; la commande observée porte un chemin en plus.

    Deviner où coupe la commande résolue et où commencent les arguments
    serait exactement l'invention que #210 refuse — la commande observée
    reste ``{"run": ...}`` verbatim.
    """
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    lint: ruff check\n", encoding="utf-8"
    )
    (tmp_path / "some").mkdir()
    (tmp_path / "some" / "path.py").write_text("x = 1\n", encoding="utf-8")
    _setup_single_tier(tmp_path)
    blueprint = {
        "blueprintVersion": 1,
        "id": "besoin-non-infere",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": [{"run": "ruff check some/path.py"}],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    bp = tmp_path / "besoin.blueprint.json"
    bp.write_text(json.dumps(blueprint), encoding="utf-8")
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "finished"

    draft, trace = extract_blueprint(engine, outcome.run_id, tmp_path)
    assert trace[0].needs_inferred == ()
    assert draft["nodes"][0]["acceptance"] == [{"run": "ruff check some/path.py"}]


# ── 4. Node V2 : jamais dispatché, marqué et laissé verbatim ────────────────


def test_extract_marks_v2_node_as_host_pending_and_keeps_prose_verbatim(tmp_path: Path) -> None:
    _setup_single_tier(tmp_path)
    bp = _blueprint(tmp_path, c_acceptance=_V2)
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "waiting_host"
    assert outcome.node_id == "c"

    draft, trace = extract_blueprint(engine, outcome.run_id, tmp_path)
    by_id = {n.node_id: n for n in trace}
    assert by_id["a"].status == "completed"
    assert by_id["b"].status == "completed"
    assert by_id["c"].status == "host_pending"
    assert by_id["c"].verifiability == "V2"

    nodes = {n["id"]: n for n in draft["nodes"]}
    assert nodes["c"]["acceptance"] == [_V2]
    assert nodes["c"]["extraction"] == {"status": "host_pending"}


# ── 5. Run abandonné : les nodes non atteints sont marqués, jamais inventés ──


def test_extract_marks_not_reached_nodes_on_aborted_run(tmp_path: Path) -> None:
    bp = _blueprint(tmp_path)
    engine = _engine(tmp_path)

    # Exécution interactive (pas de dispatch) : aucune tâche du Mission Ledger
    # n'existe pour "a" — l'extraction ne doit rien y observer de mécanique.
    wfi, contract = engine.run(bp, executor=InteractiveNodeExecutor())
    output = {"pins": {p.pin_id: {"contract": p.contract} for p in contract.outputs}}
    engine.resume(wfi.id, output=output, executor=InteractiveNodeExecutor())
    engine.abort(wfi.id, reason="test — abandon volontaire avant b")

    draft, trace = extract_blueprint(engine, wfi.id, tmp_path)
    by_id = {n.node_id: n for n in trace}
    assert by_id["a"].status == "completed"
    assert by_id["a"].verifiability is None  # jamais dispatché : pas de tâche, pas de classe devinée
    assert by_id["a"].observed_commands == ()
    assert by_id["b"].status == "not_reached"
    assert by_id["c"].status == "not_reached"

    nodes = {n["id"]: n for n in draft["nodes"]}
    # Rien observé pour "a" (exécution interactive) : l'acceptance d'origine survit.
    assert nodes["a"]["acceptance"] == [_V0_RUN]
    assert nodes["b"]["extraction"] == {"status": "not_reached"}
    assert nodes["c"]["extraction"] == {"status": "not_reached"}
