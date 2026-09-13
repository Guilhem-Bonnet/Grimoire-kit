"""Le pilote change le comportement observé de ``DispatchExecutor`` (issue #209).

Deux critères d'acceptation de l'issue, prouvés bout-en-bout : la politique
change le palier de départ réellement observé, et un plafond de coût par
node arrête la cascade avec un refus nommé. Mêmes fournisseurs factices que
``test_flows_dispatch_executor.py`` : de vrais scripts Python, exécutés par
de vrais ``subprocess``, jamais un vrai fournisseur LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
PILOT_POLICY = STANDARD / "pilot.yaml"

_WRITER = """\
    import re, json, sys
    prompt = sys.argv[1]
    path = re.search(r"Écris ta sortie dans le fichier (\\S+)", prompt).group(1)
    pins = {
        m.group(1): {"contract": m.group(2)}
        for m in re.finditer(r"^  - (\\S+) : (\\S+)$", prompt, re.MULTILINE)
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pins": pins}, fh)
    print(json.dumps({"total_cost_usd": COST}))
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


def _write_pilot_policy(root: Path, body: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / PILOT_POLICY).write_text(body, encoding="utf-8")


def _blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "pilote-flow",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "acceptance": ["la suite de tests passe", {"run": "true"}],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    path = tmp_path / "pilote.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows", project_root=tmp_path)


# ── 1. La politique change le palier de départ observé ──────────────────────


def test_pilot_policy_change_le_palier_de_depart_observe(tmp_path: Path) -> None:
    cheap = _writer_script(tmp_path, "cheap.py", cost=0.01)
    mid = _writer_script(tmp_path, "mid.py", cost=0.01)
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )
    bp = _blueprint(tmp_path)

    # Sans politique : un node V0 part à "cheap" (comportement inchangé).
    outcome_sans_politique = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)
    assert outcome_sans_politique.nodes[0].provider == "cheap-writer"

    # Avec `pilot.yaml` déclarant V0 -> mid : le node part directement à "mid".
    _write_pilot_policy(tmp_path, "start_tier:\n  V0: mid\n")
    outcome_avec_politique = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)
    assert outcome_avec_politique.nodes[0].provider == "mid-writer"
    assert outcome_avec_politique.nodes[0].escalations == 0  # parti directement à mid, jamais tenté cheap


# ── 2. Un plafond de coût par node arrête la cascade, refus nommé ───────────


def test_pilot_max_cost_arrete_la_cascade_avec_un_refus_nomme(tmp_path: Path) -> None:
    cheap_rouge = tmp_path / "scripts" / "cheap_rouge.py"
    cheap_rouge.parent.mkdir(parents=True, exist_ok=True)
    cheap_rouge.write_text(
        dedent(
            """\
            import json
            print(json.dumps({"total_cost_usd": 0.5}))
            """
        ),
        encoding="utf-8",
    )  # n'écrit jamais l'enveloppe attendue : le check échoue, verdict rouge
    mid = _writer_script(tmp_path, "mid.py", cost=0.5)
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap_rouge)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )
    _write_pilot_policy(tmp_path, "max_cost_usd_per_node: 0.4\n")
    bp = _blueprint(tmp_path)

    outcome = run_with_dispatch(_engine(tmp_path), bp, project_root=tmp_path)

    assert outcome.status == "blocked"
    node = outcome.nodes[0]
    assert node.verdict == "cost_capped"
    assert node.attempts == 1  # jamais tenté "mid" : le plafond était déjà dépassé
    assert node.provider == "cheap-writer"
