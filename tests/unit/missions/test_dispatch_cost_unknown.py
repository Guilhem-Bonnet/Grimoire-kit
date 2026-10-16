"""Un coût inconnu n'est jamais zéro : plafond, rapport et trace (W1-01, issue #709).

Constat sur 7c172595 : ``_extract_cost_usd`` rend ``None`` dès qu'un
fournisseur ne sort pas de JSON portant ``total_cost_usd`` (la plupart des
fournisseurs headless), et ``run_dispatch`` additionnait ce ``None`` comme
``0.0`` : le plafond de coût du pilote ne s'y déclenchait jamais, et la trace
``dispatch.outcome`` écrivait ``estimated_cost_usd: 0.0``.

Fournisseurs factices : de vrais scripts Python locaux, exécutés par de vrais
``subprocess`` (même convention que ``test_dispatch.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.service import TaskService
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
LEDGER = Path("_grimoire-runtime-output/ledger")
V0_CRITERION = "la suite de tests passe"

#: Écrit un marqueur faux (check rouge) et ne dit rien de son coût.
_SILENT_RED = """\
    from pathlib import Path
    Path("marker.txt").write_text("wrong", encoding="utf-8")
    """
#: Écrit un marqueur faux (check rouge) et annonce 0.5 USD.
_PRICED_RED = """\
    import json
    from pathlib import Path
    Path("marker.txt").write_text("wrong", encoding="utf-8")
    print(json.dumps({"total_cost_usd": 0.5}))
    """
_WRITE_DONE = """\
    from pathlib import Path
    Path("marker.txt").write_text("done", encoding="utf-8")
    """


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _provider(pid: str, tier: str, script: Path) -> str:
    return (
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n'
        f'    allowed_capabilities: ["chat", "code"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f'    invocation: "{sys.executable} {script} {{prompt}} --model {{model}}"\n'
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "{tier}"\n'
        f"    fallback_order: []\n"
    )


def _registry(root: Path, *providers: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        '$schema: "grimoire-llm-provider-registry/v1"\n'
        'metadata:\n  project: "demo"\n  owner: ""\n  policy: "No provider/model call outside this registry."\n'
        "providers:\n" + "\n".join(providers) + "\n"
        'routing:\n  default_provider: ""\n  default_fallback_chain: []\n'
        "  require_capability_match: true\n  require_data_policy_match: true\n",
        encoding="utf-8",
    )


def _task(tmp_path: Path) -> tuple[TaskService, str]:
    service = TaskService(tmp_path, LEDGER)
    mission = service.ledger.create_mission("Démo dispatch", origin="test")
    task = service.ledger.create_task(mission.id, "Tâche déléguée", acceptance=(V0_CRITERION,), owner="amelia")
    return service, task.id


def _chain(tmp_path: Path, cheap_body: str, mid_body: str = _WRITE_DONE) -> tuple[TaskService, str]:
    _registry(
        tmp_path,
        _provider("cheap-writer", "cheap", _script(tmp_path, "cheap.py", cheap_body)),
        _provider("mid-writer", "mid", _script(tmp_path, "mid.py", mid_body)),
    )
    return _task(tmp_path)


def _only_outcome(tmp_path: Path):
    outcomes = [t for t in TraceLedger(tmp_path / TRACES_DIR).list_traces() if DISPATCH_OUTCOME_TAG in t.tags]
    assert len(outcomes) == 1, outcomes
    return outcomes[0]


def test_fournisseur_muet_et_plafond_pose_arretent_l_escalade_pour_cout_inconnu(tmp_path: Path) -> None:
    """Critère 1 de #709 : sans coût rapporté, ``spent_so_far`` valait 0.0 à chaque palier."""
    service, tid = _chain(tmp_path, _SILENT_RED)

    report = run_dispatch(service, tid, checks=("grep -q done marker.txt",), max_cost_usd=0.4)

    assert len(report.attempts) == 1  # jamais d'appel à `mid`
    assert report.succeeded is False
    assert report.cost_capped is not None
    assert "coût inconnu" in report.cost_capped
    assert report.cost_cap_reason == "cost_unknown"
    assert report.to_dict()["cost_cap_reason"] == "cost_unknown"


def test_la_trace_dit_pourquoi_le_plafond_a_arrete_l_escalade(tmp_path: Path) -> None:
    service, tid = _chain(tmp_path, _SILENT_RED)

    run_dispatch(service, tid, checks=("grep -q done marker.txt",), max_cost_usd=0.4)

    assert "cost_cap:cost_unknown" in _only_outcome(tmp_path).tags


def test_politique_continue_flagged_laisse_escalader_et_garde_le_cout_signale(tmp_path: Path) -> None:
    service, tid = _chain(tmp_path, _SILENT_RED)

    report = run_dispatch(
        service, tid, checks=("grep -q done marker.txt",), max_cost_usd=0.4, cost_unknown_policy="continue_flagged"
    )

    assert report.succeeded is True
    assert len(report.attempts) == 2
    assert report.cost_capped is None
    cost = report.to_dict()["cost"]
    assert cost["status"] == "unknown" and cost["usd"] is None and cost["unpriced_calls"] == 2


def test_plafond_atteint_par_un_cout_connu_garde_sa_raison(tmp_path: Path) -> None:
    service, tid = _chain(tmp_path, _PRICED_RED)

    report = run_dispatch(service, tid, checks=("grep -q done marker.txt",), max_cost_usd=0.4)

    assert report.cost_cap_reason == "cost_reached"
    assert "0.4" in (report.cost_capped or "")


def test_deux_tentatives_dont_une_muette_donnent_lower_bound(tmp_path: Path) -> None:
    """Critère de la fiche : un minimum, jamais présenté comme le total."""
    service, tid = _chain(tmp_path, _PRICED_RED, mid_body=_WRITE_DONE)

    report = run_dispatch(service, tid, checks=("grep -q done marker.txt",))  # sans plafond : escalade

    assert len(report.attempts) == 2
    cost = report.to_dict()["cost"]
    assert cost == {"usd": 0.5, "status": "lower_bound", "unpriced_calls": 1}
    outcome = _only_outcome(tmp_path)
    assert outcome.token_usage.estimated_cost_usd == 0.5
    assert outcome.token_usage.unpriced_calls == 1
    assert outcome.token_usage.cost.status == "lower_bound"


def test_la_trace_dispatch_outcome_d_un_fournisseur_muet_n_ecrit_pas_zero(tmp_path: Path) -> None:
    service, tid = _chain(tmp_path, _WRITE_DONE)

    run_dispatch(service, tid, checks=("grep -q done marker.txt",))

    usage = _only_outcome(tmp_path).token_usage
    assert usage.estimated_cost_usd is None
    assert usage.unpriced_calls == 1
    assert usage.cost.status == "unknown"


def test_dispatch_outcome_stats_rend_inconnu_pour_des_couts_inconnus(tmp_path: Path) -> None:
    """Critère 2 de #709, de bout en bout : le chemin réel de ``grimoire task dispatch``."""
    service, tid = _chain(tmp_path, _WRITE_DONE)
    run_dispatch(service, tid, checks=("grep -q done marker.txt",))

    stats = TraceLedger(tmp_path / TRACES_DIR).dispatch_outcome_stats()

    assert stats.overall.resolved == 1
    assert stats.overall.cost_per_resolved_task_usd is None
    assert stats.overall.total_cost_usd is None
    assert stats.to_dict()["overall"]["cost_status"] == "unknown"
