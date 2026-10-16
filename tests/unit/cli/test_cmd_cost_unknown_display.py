"""Affichage CLI d'un coût inconnu ou partiel (W1-01, revue S4/S9, issue #709).

Un coût que le fournisseur n'a pas rendu s'affiche ``inconnu``, jamais ``0.0000``
(``dispatch stats``, ``flow status``, ``flow list``) ; un minimum s'affiche
``>= X USD (N non pricés)``, avec le décompte des appels sans prix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_flow import _engine as _cli_engine
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.schemas import TraceOutcome

runner = CliRunner()
_WIDE = {"COLUMNS": "250"}

_SILENT_WRITER = """\
import re, json, sys
prompt = sys.argv[1]
path = re.search(r"Écris ta sortie dans le fichier (\\S+)", prompt).group(1)
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"pins": {}}, fh)
"""


def _record_outcome(root: Path, token_usage: dict, *, day: int) -> None:
    TraceLedger(root / TRACES_DIR).record(
        run_id=f"dispatch-{day}",
        workflow_instance_id="",
        mission_id="",
        task_id=f"GAO-{day}",
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS,
        started_at=f"2026-01-{day:02d}T00:00:00+00:00",
        agent_id="p",
        token_usage=token_usage,
        tags=[
            DISPATCH_OUTCOME_TAG,
            "class:V0",
            "tier:cheap",
            "acceptance:judged",
            "resolved:true",
            "provider:p",
            f"replay:n{day}",
        ],
    )


def test_dispatch_stats_affiche_inconnu_pas_zero(tmp_path: Path) -> None:
    _record_outcome(tmp_path, {"prompt_tokens": 10}, day=1)

    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert "inconnu" in result.output
    assert "$0.0000" not in result.output
    assert "0.0000 USD" not in result.output


def test_dispatch_stats_affiche_un_minimum_avec_le_decompte(tmp_path: Path) -> None:
    _record_outcome(tmp_path, {"estimated_cost_usd": 0.42}, day=1)
    _record_outcome(tmp_path, {"prompt_tokens": 10}, day=2)

    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert ">= 0.2100 USD (1 non pricés)" in result.output  # 0.42 connu / 2 résolus, un appel sans prix


def _silent_flow(tmp_path: Path) -> tuple[FlowEngine, str, str]:
    script = tmp_path / "scripts" / "silent.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(dedent(_SILENT_WRITER), encoding="utf-8")
    standard = tmp_path / "_grimoire" / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    invocation = f"{sys.executable} {script} {{prompt}} --model {{model}}"
    (standard / "llm-provider-registry.yaml").write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "silent"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["silent-model"]
    currency: "api"
    invocation: "{invocation}"
    models:
      - id: "silent-model"
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
    bp = tmp_path / "silent.blueprint.json"
    bp.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "silent-flow",
                "nodes": [{"id": "n", "kind": "pattern", "ref": "ORC-01", "acceptance": [{"run": "true"}], "pins": []}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    engine = _cli_engine(tmp_path)  # le même moteur que les commandes `flow` relisent
    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)
    assert outcome.status == "finished", outcome.to_dict()
    return engine, outcome.run_id, "silent-flow"


def test_flow_status_affiche_inconnu_pour_un_node_muet(tmp_path: Path) -> None:
    _, run_id, _ = _silent_flow(tmp_path)

    result = runner.invoke(app, ["flow", "status", run_id, "--project-root", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert "coût inconnu (1 non pricés)" in result.output
    assert "0.0000" not in result.output


def test_flow_list_affiche_inconnu_pour_un_flow_mesure_muet(tmp_path: Path) -> None:
    _silent_flow(tmp_path)

    result = runner.invoke(app, ["flow", "list", "--project-root", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert "inconnu" in result.output
    assert "0.0000" not in result.output
