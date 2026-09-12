"""La classe V0 exige une acceptance structurée, sinon elle est traitée comme V1 (issue #428, suite).

Sans cette règle, un auteur de blueprint qui écrit ``acceptance: ["la suite de
tests passe"]`` sur un node classé V0 obtenait un vert sur la seule foi de
l'enveloppe de l'ouvrier — exactement le fossé que la forme structurée
(``{"run": ...}``, voir ``test_flows_dispatch_executor_acceptance_gate.py``)
comble quand l'auteur pense à la déclarer, mais qui restait ouvert par défaut
sinon. La classe est dérivée deux fois côté cascade — une fois par
``DispatchExecutor.execute`` (refus V2 précoce, rapport), une fois par
``run_dispatch`` lui-même (palier de départ, transition ``needs_verification``
du ledger) — donc la rétrogradation doit être décidée une seule fois et
transmise aux deux (``verifiability_override``/``verifiability_warning`` de
``run_dispatch``), pas seulement affichée après coup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
LEDGER = Path("_grimoire-runtime-output/ledger")

_RESULT_RE = r"Écris ta sortie dans le fichier (\S+)"
_PIN_RE = r"^  - (\S+) : (\S+)$"


def _blueprint(tmp_path: Path, *, acceptance: list[object]) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "v0-requires-structured",
        "name": "V0 exige une acceptance structurée (issue #428)",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": acceptance,
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    path = tmp_path / "v0-requires-structured.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _always_conforms_writer(tmp_path: Path) -> Path:
    """Un ouvrier factice qui écrit toujours une enveloppe conforme — jamais de vrai travail."""
    body = (
        "import json, re, sys\n"
        "prompt = sys.argv[1]\n"
        f'path = re.search(r"{_RESULT_RE}", prompt).group(1)\n'
        "pins = {m.group(1): {\"contract\": m.group(2)} "
        f'for m in re.finditer(r"{_PIN_RE}", prompt, re.MULTILINE)}}\n'
        "with open(path, \"w\", encoding=\"utf-8\") as fh:\n"
        "    json.dump({\"pins\": pins}, fh)\n"
    )
    script_path = tmp_path / "scripts" / "cheap.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(body, encoding="utf-8")
    return script_path


def _write_registry(root: Path) -> None:
    """Un fournisseur toujours vert, décliné sur les trois paliers.

    La règle testée ici (V0 sans acceptance structurée → V1) déplace le
    palier de départ de ``cheap`` à ``mid`` — un registre qui ne déclarerait
    que ``cheap`` refuserait la cascade (``no_provider``) pour un node
    rétrogradé, ce qui ne teste rien de la règle elle-même.
    """
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    invocation = f"{sys.executable} {_always_conforms_writer(root)} {{prompt}} --model {{model}}"
    providers = "\n".join(
        f"""\
  - id: "{pid}"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["{pid}-model"]
    currency: "api"
    invocation: "{invocation}"
    models:
      - id: "{pid}-model"
        tier: "{tier}"
    fallback_order: []
"""
        for pid, tier in (("cheap-writer", "cheap"), ("mid-writer", "mid"), ("strong-writer", "strong"))
    )
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
{providers}
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


# ── 1. V0 purement textuel : rétrogradé en V1, jamais fermé sur la foi seule ─


def test_v0_textuel_sans_acceptance_structuree_est_traite_comme_v1(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bp = _blueprint(tmp_path, acceptance=["la suite de tests passe"])
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    node = outcome.nodes[0]
    assert node.verdict == "green"
    assert node.verifiability == "V1"
    assert node.needs_review is True
    assert node.acceptance_status == "judged"
    assert node.verifiability_warning is not None
    assert "n" in node.verifiability_warning
    assert "V0" in node.verifiability_warning
    assert "V1" in node.verifiability_warning

    # Le ledger le confirme : la tâche est bien passée en revue, pas fermée
    # sur la seule enveloppe — même transition qu'un vrai V1 (run_dispatch).
    service = TaskService(tmp_path, LEDGER)
    task = service.require(node.task_id)
    assert task.status is TaskState.NEEDS_VERIFICATION


# ── 2. V0 structuré : inchangé (pas de rétrogradation, pas d'avertissement) ──


def test_v0_structure_reste_v0_inchange(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bp = _blueprint(tmp_path, acceptance=["la suite de tests passe", {"run": "true"}])
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    node = outcome.nodes[0]
    assert node.verdict == "green"
    assert node.verifiability == "V0"
    assert node.needs_review is False
    assert node.acceptance_status == "executed"
    assert node.verifiability_warning is None

    service = TaskService(tmp_path, LEDGER)
    task = service.require(node.task_id)
    # V0 ne transitionne jamais vers needs_verification (voir run_dispatch) :
    # la tâche reste dans l'état où la cascade l'a laissée.
    assert task.status is not TaskState.NEEDS_VERIFICATION


# ── 3. V1 déclaré : inchangé (la règle ne s'applique qu'à un V0 dérivé) ──────


def test_v1_declare_reste_inchange(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bp = _blueprint(tmp_path, acceptance=["revue humaine avant fusion"])
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    node = outcome.nodes[0]
    assert node.verdict == "green"
    assert node.verifiability == "V1"
    assert node.needs_review is True
    assert node.acceptance_status == "judged"
    # Un V1 déclaré n'a jamais été un V0 dérivé : pas cet avertissement précis.
    assert node.verifiability_warning is None

    service = TaskService(tmp_path, LEDGER)
    task = service.require(node.task_id)
    assert task.status is TaskState.NEEDS_VERIFICATION
