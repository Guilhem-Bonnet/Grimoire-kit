"""`grimoire task trace` : la cause d'un échec sans ouvrir un seul fichier (#139).

Le critère d'acceptation de l'issue, opposé tel quel : sur une tâche
volontairement mise en échec, la timeline montre l'outil refusé par la policy,
le gate rouge, ou l'abort avec sa raison. Et le trou de corrélation — les
événements de hooks écrits sans `task_id` — est fermé par un test qui échoue
s'il se rouvre.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.cmd_task import task_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.hosts.runtime import run_hook
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import IncidentSeverity, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService
from grimoire.missions.trace import (
    DEFAULT_EVIDENCE_RELPATH,
    DEFAULT_KERNEL_RELPATH,
    DEFAULT_LEDGER_RELPATH,
    build_task_timeline,
)
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext
from grimoire.traces.ledger import TraceLedger

runner = CliRunner()
ACCEPTATION = "la cause est visible sans ouvrir un fichier"


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def ouvre_et_reclame(projet: Path) -> tuple[TaskService, str]:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, "Livrer la timeline", acceptance=(ACCEPTATION,), owner="amelia")
    service.transition(task.id, TaskState.READY, "amelia")
    service.context(task.id)
    service.claim(task.id, "claude", "claude-code")
    return service, task.id


def hook(projet: Path, tool: str, tool_input: dict[str, str]) -> None:
    run_hook(
        {"hook_event_name": "PreToolUse", "cwd": str(projet), "tool_name": tool, "tool_input": tool_input, "session_id": "s-1"},
        host_id=HostId.CLAUDE_CODE_CLI,
    )


def ctx(task_id: str, wfi: str = "") -> ExecutionContext:
    return ExecutionContext(run_id="RUN-1", mission_id="m", task_id=task_id, workflow_instance_id=wfi,
                            actor_id="claude", host_id="claude-code", risk_profile="standard")


def trace(projet: Path, *args: str) -> tuple[int, str]:
    res = runner.invoke(task_app, ["trace", *args, "--project-root", str(projet)])
    return res.exit_code, res.output


# ── le trou de corrélation : les hooks portent la tâche réclamée ─────────────

def test_un_refus_de_policy_est_journalise_sous_la_tache_reclamee(projet: Path) -> None:
    """Avant : `decide_tool_policy` ne mettait pas la tâche dans son détail, le
    gateway écrivait `task_id=""`, et aucun refus n'était retrouvable par tâche."""
    _, tid = ouvre_et_reclame(projet)
    hook(projet, "Bash", {"command": "rm -rf src"})
    traces = TraceLedger(projet / TRACES_DIR).list_traces(task_id=tid)
    assert [tc.verdict for t in traces for tc in t.tool_calls] == ["block"]
    assert {t.task_id for t in TraceLedger(projet / TRACES_DIR).list_traces()} == {tid}, "aucune trace orpheline"


def test_un_gate_rouge_laisse_une_trace_mais_rien_au_ledger(projet: Path) -> None:
    service, tid = ouvre_et_reclame(projet)
    service.transition(tid, TaskState.RUNNING, "claude")
    avant = len(service.ledger.list_events())
    with pytest.raises(TaskRefusedError):
        service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude")
    assert len(service.ledger.list_events()) == avant
    gate = [t for t in TraceLedger(projet / TRACES_DIR).list_traces(task_id=tid) if t.recipe_id == "grimoire.task-gate"]
    assert len(gate) == 1
    assert {pv.verdict_id for pv in gate[0].policy_verdicts} == {"evidence_pack", "decision_trace"}


# ── le critère d'acceptation : la cause, sans ouvrir un fichier ──────────────

def test_la_timeline_montre_la_cause_de_chaque_echec(projet: Path) -> None:
    service, tid = ouvre_et_reclame(projet)
    service.transition(tid, TaskState.RUNNING, "claude")
    # 1. un outil refusé par la policy
    hook(projet, "Bash", {"command": "rm -rf src"})
    hook(projet, "Edit", {"file_path": "src/a.py"})
    # 2. un gate rouge
    with pytest.raises(TaskRefusedError):
        service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude")
    # 3. un workflow abandonné avec sa raison, après un checkpoint
    kernel = RuntimeKernel(projet / DEFAULT_KERNEL_RELPATH)
    wfi = kernel.create_instance(ctx(tid), "recipe.livraison")
    kernel.start(wfi.id, ctx(tid, wfi.id))
    kernel.checkpoint(wfi.id, ctx(tid, wfi.id), step_id="tests", completed_steps=["build"], pending_steps=["tests", "docs"])
    kernel.abort(wfi.id, ctx(tid, wfi.id), reason="suite pytest rouge sur test_trace")
    # 4. un verdict échoué
    svc = EvidenceService(projet / DEFAULT_EVIDENCE_RELPATH)
    pack = svc.create_pack(task_id=tid, profile=EvidenceProfile.STANDARD, items=[
        EvidenceItem(id="e1", kind=EvidenceKind.LOG, uri="log://", digest="d", summary="sans rapport")],
        acceptance=(ACCEPTATION,))
    svc.verify(pack, acceptance=(ACCEPTATION,))
    # 5. un incident ouvert
    service.ledger.open_incident(service.require(tid).mission_id, tid, "ci-rouge", "la CI refuse la PR",
                                 severity=IncidentSeverity.HIGH, causes=("test_trace échoue",))

    timeline = build_task_timeline(projet, tid)
    assert timeline.task is not None and timeline.task.status is TaskState.RUNNING
    kinds = [c.kind for c in timeline.causes]
    assert "tool.block" in kinds, "l'outil refusé par la policy est une cause"
    assert "task.gate.refused" in kinds, "le gate rouge est une cause"
    assert "workflow.aborted" in kinds, "l'abort est une cause"
    assert "verdict.failed" in kinds or any(k.startswith("verdict.") for k in kinds), "le verdict échoué est une cause"
    assert "incident.open" in kinds
    resumes = " | ".join(c.summary for c in timeline.causes)
    assert "Bash refusé par la policy" in resumes
    assert "evidence_pack" in resumes and "decision_trace" in resumes
    assert "suite pytest rouge sur test_trace" in resumes
    assert "tool.allow" in [e.kind for e in timeline.entries], "l'écriture autorisée figure, sans être une cause"
    assert any(e.kind == "checkpoint" and "reprise sûre" in e.summary for e in timeline.entries)
    assert timeline.sources == {k: str(projet / v) for k, v in {
        "ledger": DEFAULT_LEDGER_RELPATH, "hooks": TRACES_DIR,
        "runtime": DEFAULT_KERNEL_RELPATH, "evidence": DEFAULT_EVIDENCE_RELPATH}.items()}
    assert [e.at for e in timeline.entries] == sorted(e.at for e in timeline.entries)


def test_le_cli_rend_les_causes_et_le_json_est_complet(projet: Path) -> None:
    service, tid = ouvre_et_reclame(projet)
    service.transition(tid, TaskState.RUNNING, "claude")
    hook(projet, "Bash", {"command": "rm -rf src"})
    service.transition(tid, TaskState.BLOCKED, "claude", reason="attente du service tiers")

    code, sortie = trace(projet, tid)
    assert code == 0, sortie
    assert "Cause(s) d'arrêt : 2" in sortie
    assert "Bash refusé par la policy" in sortie
    assert "attente du service tiers" in sortie

    code, sortie = trace(projet, tid, "--causes")
    assert code == 0 and "tâche ouverte" not in sortie and "refusé par la policy" in sortie

    res = runner.invoke(task_app, ["trace", tid, "--project-root", str(projet)], obj={"output": "json"})
    data = json.loads(res.output)
    assert data["task"]["id"] == tid and len(data["causes"]) == 2
    assert {e["source"] for e in data["entries"]} >= {"ledger", "hooks"}


def test_une_tache_saine_le_dit(projet: Path) -> None:
    _, tid = ouvre_et_reclame(projet)
    code, sortie = trace(projet, tid)
    assert code == 0 and "Aucune cause d'arrêt" in sortie
    assert "sources absentes : hooks, runtime, evidence" in sortie


# ── honnêteté : rien d'inventé, rien de créé ─────────────────────────────────

def test_une_tache_inconnue_partout_est_refusee(projet: Path) -> None:
    ouvre_et_reclame(projet)
    code, sortie = trace(projet, "GAO-nulle-part-001")
    assert code == 1 and "Aucune trace" in sortie


def test_bootstrap_se_trace_par_les_hooks_meme_sans_ledger(tmp_path: Path) -> None:
    """Les hooks écrivent sous `bootstrap` tant qu'aucune tâche n'est réclamée :
    ce journal doit rester lisible, sans exiger une carte au ledger."""
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    hook(tmp_path, "Bash", {"command": "rm -rf src"})
    timeline = build_task_timeline(tmp_path, "bootstrap")
    assert timeline.task is None and not timeline.is_empty
    assert [c.kind for c in timeline.causes] == ["tool.block"]
    code, sortie = trace(tmp_path, "bootstrap")
    assert code == 0 and "inconnue du ledger" in sortie


def test_lire_ne_seme_aucun_dossier(tmp_path: Path) -> None:
    timeline = build_task_timeline(tmp_path, "x")
    assert timeline.is_empty and all(v is None for v in timeline.sources.values())
    assert not (tmp_path / DEFAULT_LEDGER_RELPATH).exists()
    assert not (tmp_path / DEFAULT_KERNEL_RELPATH).exists()
    assert not (tmp_path / TRACES_DIR).exists()


def test_le_ledger_seul_suffit_a_dater_un_blocage(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)
    mission = ledger.create_mission(title="T", origin="test")
    task = ledger.create_task(mission.id, "Une", acceptance=("ok",), owner="x")
    ledger.transition_task(task.id, TaskState.READY)
    ledger.transition_task(task.id, TaskState.BLOCKED, actor_id="claude", reason="dépendance absente")
    timeline = build_task_timeline(tmp_path, task.id)
    assert [c.summary for c in timeline.causes] == ["ready → blocked par claude — dépendance absente"]
