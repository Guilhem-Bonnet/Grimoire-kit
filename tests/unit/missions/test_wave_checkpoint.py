"""W5-05 : un checkpoint humain par vague, lié à l'empreinte du diff fusionné.

Avant : rien ne séparait deux vagues d'un plan. Les tâches de la vague 1 se
fusionnaient, celles de la vague 2 se réclamaient, sans qu'un humain ait vu le
contenu entré dans la cible, et sans que sa décision (si elle existait) soit
liée à ce contenu. Les tests construisent de vrais dépôts git et de vrais tours
humains (hook ``UserPromptSubmit``), comme ``test_flows_attested_checkpoint``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT, create_task_worktree, merge_task
from grimoire.missions.wave_checkpoint import claim_refusal, decide, report, request, require, wave_state

OK = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
ACC = "la vague se valide"
runner = CliRunner()
JSON = {"output": "json"}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def _commit(cwd: Path, name: str, content: str, message: str) -> str:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _human_turn(root: Path, session_id: str = "S1") -> None:
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=root, prompt="ok", session_id=session_id,
                  host="host-claude-code-cli"),
    )
    time.sleep(0.02)


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("_grimoire-runtime-output/\n", encoding="utf-8")
    _git(root, "add", "--", ".gitignore")
    _commit(root, "socle.txt", "socle\n", "socle")
    return root


class Plan:
    """Mission M : A et B en vague 1 (indépendantes), C en vague 2 (dépend de A et B)."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.service = TaskService(repo)
        ledger = self.service.ledger
        self.mission = ledger.create_mission(title="Plan", origin="test").id
        self.a, self.b = self._running("Enfant A"), self._running("Enfant B")
        c = ledger.create_task(self.mission, "Suite", acceptance=(ACC,), owner="amelia")
        ledger.transition_task(c.id, TaskState.READY)
        ledger.link_tasks(c.id, self.a)
        ledger.link_tasks(c.id, self.b)
        self.c = c.id

    def _running(self, titre: str) -> str:
        ledger = self.service.ledger
        task = ledger.create_task(self.mission, titre, acceptance=(ACC,), owner="amelia")
        ledger.transition_task(task.id, TaskState.READY)
        ledger.claim_task(task.id, "amelia", "local")
        ledger.transition_task(task.id, TaskState.RUNNING)
        return task.id

    def merge(self, tid: str, name: str, content: str) -> None:
        wt = create_task_worktree(self.service, tid, repo=self.repo)
        _commit(wt.path, name, content, f"travail {tid}")
        assert merge_task(self.service, tid, repo=self.repo, check=OK).status == "merged"

    def merge_wave_one(self) -> None:
        self.merge(self.a, "a.txt", "A\n")
        self.merge(self.b, "b.txt", "B\n")

    def close_wave_one(self) -> None:
        for tid in (self.a, self.b):
            self.service.ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION)
            self.service.ledger.transition_task(tid, TaskState.CLOSED)


@pytest.fixture
def plan(repo: Path) -> Plan:
    return Plan(repo)


def test_une_vague_non_fusionnee_est_incomplete_et_sans_empreinte(plan: Plan) -> None:
    plan.merge(plan.a, "a.txt", "A\n")

    state = wave_state(plan.service, plan.mission, 1)

    assert state.status == "incomplete"
    assert state.fingerprint == ""
    assert state.unmerged == (plan.b,)
    assert not decide(plan.service, plan.mission, 1, "approve", "x" * 64).ok


def test_l_empreinte_est_celle_du_contenu_fusionne(plan: Plan) -> None:
    plan.merge_wave_one()
    first = request(plan.service, plan.mission, 1)

    assert first.status == "awaiting_human"
    assert len(first.fingerprint) == 64
    assert wave_state(plan.service, plan.mission, 1).fingerprint == first.fingerprint  # reproductible
    assert wave_state(plan.service, plan.mission, 2).status == "incomplete"  # C n'est pas fusionnée


def test_approve_sans_tour_humain_est_refuse_puis_attested_avec(plan: Plan) -> None:
    plan.merge_wave_one()
    fp = request(plan.service, plan.mission, 1).fingerprint

    refused = decide(plan.service, plan.mission, 1, "approve", fp)
    assert not refused.ok and "awaiting_human" in refused.reason
    assert wave_state(plan.service, plan.mission, 1).status == "awaiting_human"

    _human_turn(plan.repo)
    approved = decide(plan.service, plan.mission, 1, "approve", fp)
    assert approved.ok and approved.status == "approved"
    assert wave_state(plan.service, plan.mission, 1).status == "approved"


def _grant(plan: Plan, scope: str) -> str:
    """Un vrai octroi W5-06 : challenge, tour humain de la session, relance."""
    from grimoire.flows.autonomy import GrantJournal, GrantRequest

    journal = GrantJournal(plan.repo / "_grimoire-runtime-output" / "flows", plan.repo)
    req = GrantRequest.build([scope], 5.0, "+1h")
    assert journal.request(req)[0] is None
    _human_turn(plan.repo)
    grant, _ = journal.request(req)
    assert grant is not None
    return str(grant["grant_id"])


def test_un_octroi_valide_leve_le_checkpoint_de_la_vague_sans_tour_humain(plan: Plan) -> None:
    plan.merge_wave_one()
    fp = request(plan.service, plan.mission, 1).fingerprint
    grant_id = _grant(plan, f"run:waves-{plan.mission}/wave-1")

    done = decide(plan.service, plan.mission, 1, "approve", fp)

    assert done.ok and done.status == "approved"
    last = [e for e in _journal_entries(plan) if e.get("type") == "decision"][-1]
    assert last["attestation"] == f"granted:{grant_id}"
    assert last["fingerprint"] == fp


def test_un_octroi_hors_perimetre_ne_leve_pas_le_checkpoint(plan: Plan) -> None:
    plan.merge_wave_one()
    fp = request(plan.service, plan.mission, 1).fingerprint
    _grant(plan, f"run:waves-{plan.mission}/wave-2")  # une autre vague

    refused = decide(plan.service, plan.mission, 1, "approve", fp)

    assert not refused.ok and "awaiting_human" in refused.reason
    assert wave_state(plan.service, plan.mission, 1).status == "awaiting_human"


def test_un_octroi_ne_couvre_pas_une_vague_dont_le_contenu_a_change(plan: Plan) -> None:
    plan.merge_wave_one()
    _grant(plan, f"run:waves-{plan.mission}")

    stale = decide(plan.service, plan.mission, 1, "approve", "0" * 64)

    assert not stale.ok and stale.status == "stale"


def _journal_entries(plan: Plan) -> list[dict[str, object]]:
    from grimoire.flows.attestation import CheckpointJournal

    return CheckpointJournal(plan.repo / "_grimoire-runtime-output" / "flows", f"waves-{plan.mission}").entries()


def test_une_decision_qui_cite_une_autre_empreinte_est_refusee(plan: Plan) -> None:
    plan.merge_wave_one()
    request(plan.service, plan.mission, 1)
    _human_turn(plan.repo)

    outcome = decide(plan.service, plan.mission, 1, "approve", "0" * 64)

    assert not outcome.ok and outcome.status == "stale"


def _remerge_with_other_content(plan: Plan) -> None:
    """A est re-fusionnée avec un autre contenu : un commit de plus sur main et un second reçu task.merged."""
    sha = _commit(plan.repo, "a.txt", "A modifié après validation\n", "A bis")
    previous = [e for e in plan.service.ledger.events_for(plan.a) if e.event_type == MERGED_EVENT][-1].payload
    plan.service.ledger.append_event(
        MERGED_EVENT, plan.a, "task", "cli", {**previous, "head_sha": sha, "merged_sha": sha},
    )


def test_la_decision_est_caduque_si_le_contenu_fusionne_change(plan: Plan) -> None:
    plan.merge_wave_one()
    fp = request(plan.service, plan.mission, 1).fingerprint
    _human_turn(plan.repo)
    assert decide(plan.service, plan.mission, 1, "approve", fp).ok

    _remerge_with_other_content(plan)

    state = wave_state(plan.service, plan.mission, 1)
    assert state.status == "stale"
    assert state.fingerprint != fp
    # et l'ancien challenge ne sert pas pour la nouvelle empreinte
    again = decide(plan.service, plan.mission, 1, "approve", state.fingerprint)
    assert not again.ok and again.status == "unrequested"


def test_un_challenge_ouvert_perime_est_remplace_et_exige_un_nouveau_tour(plan: Plan) -> None:
    plan.merge_wave_one()
    old = request(plan.service, plan.mission, 1).fingerprint
    _human_turn(plan.repo)  # le tour humain a vu l'ancienne empreinte

    _remerge_with_other_content(plan)
    new = request(plan.service, plan.mission, 1).fingerprint

    assert new != old
    refused = decide(plan.service, plan.mission, 1, "approve", new)
    assert not refused.ok and "no_human_turn_since_challenge" in refused.reason
    _human_turn(plan.repo)
    assert decide(plan.service, plan.mission, 1, "approve", new).ok


def test_un_commit_de_merge_reecrit_rend_la_vague_stale(plan: Plan) -> None:
    plan.merge_wave_one()
    fp = request(plan.service, plan.mission, 1).fingerprint
    _human_turn(plan.repo)
    assert decide(plan.service, plan.mission, 1, "approve", fp).ok

    _git(plan.repo, "reset", "-q", "--hard", "HEAD~1")  # l'historique est réécrit sous la décision

    assert wave_state(plan.service, plan.mission, 1).status == "stale"


def test_une_vague_requise_ferme_le_claim_de_la_suivante_jusqu_a_validation(plan: Plan) -> None:
    plan.merge_wave_one()
    plan.close_wave_one()
    require(plan.service, plan.mission, 1)

    assert "vague 1" in claim_refusal(plan.service, plan.c)
    with pytest.raises(GrimoireMissionError, match="checkpoint de la vague 1"):
        plan.service.claim(plan.c, "amelia")

    fp = request(plan.service, plan.mission, 1).fingerprint
    _human_turn(plan.repo)
    assert decide(plan.service, plan.mission, 1, "approve", fp).ok

    assert claim_refusal(plan.service, plan.c) == ""
    plan.service.claim(plan.c, "amelia")
    assert plan.service.require(plan.c).status is TaskState.CLAIMED


def test_sans_vague_requise_le_claim_est_inchange(plan: Plan) -> None:
    plan.merge_wave_one()
    plan.close_wave_one()

    plan.service.claim(plan.c, "amelia")

    assert plan.service.require(plan.c).status is TaskState.CLAIMED


def test_reject_est_inscrit_et_ferme_la_suite(plan: Plan) -> None:
    plan.merge_wave_one()
    plan.close_wave_one()
    require(plan.service, plan.mission, 1)
    fp = request(plan.service, plan.mission, 1).fingerprint

    assert decide(plan.service, plan.mission, 1, "reject", fp, "A déborde").status == "rejected"
    assert claim_refusal(plan.service, plan.c) != ""


def test_la_vague_close_reste_visible_dans_task_waves(plan: Plan) -> None:
    plan.merge_wave_one()
    plan.close_wave_one()
    require(plan.service, plan.mission, 1)

    result = runner.invoke(task_app, ["waves", "--mission", plan.mission, "--project-root", str(plan.repo)], obj=JSON)

    assert result.exit_code == 0, result.output
    checkpoints = json.loads(result.output)["checkpoints"]
    assert [(c["wave"], c["status"], c["required"]) for c in checkpoints] == [(1, "unrequested", True)]
    assert report(plan.service, plan.mission)[0]["fingerprint"] == checkpoints[0]["fingerprint"]


def test_cli_request_puis_approve(plan: Plan) -> None:
    plan.merge_wave_one()
    base = ["wave-checkpoint", plan.mission, "1", "--project-root", str(plan.repo)]

    asked = runner.invoke(task_app, [*base, "--action", "request"], obj=JSON)
    assert asked.exit_code == 0, asked.output
    fp = json.loads(asked.output)["fingerprint"]

    refused = runner.invoke(task_app, [*base, "--action", "approve", "--fingerprint", fp], obj=JSON)
    assert refused.exit_code == 1
    _human_turn(plan.repo)
    approved = runner.invoke(task_app, [*base, "--action", "approve", "--fingerprint", fp], obj=JSON)
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["status"] == "approved"
