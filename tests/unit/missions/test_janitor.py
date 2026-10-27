"""Lot 2 de #876 (verrou 12) : le janitor nettoie une tâche arrivée à un état terminal, contre un faux ``claude`` et un faux ``gh``.

Critère 2 : à l'état terminal, il rend le créneau CI, arrête la session, retire le worktree et supprime la branche
fusionnée. Git est le vrai (dépôt et remote nue dans ``tmp_path``) ; chaque garde échoue fermée, une branche qu'on ne
peut pas dire fusionnée n'est jamais supprimée, et un échec est rapporté sans bloquer le tick.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git, make_repo

from grimoire.missions.admission import CI_PUSHED_EVENT, derive_view, load_capacity
from grimoire.missions.breaker import read_breaker, record_failure
from grimoire.missions.janitor import (
    ACTION_EVENT,
    ARCHIVE_DIR,
    SWEPT_EVENT,
    WORKTREE_ARCHIVE_DIR,
    Janitor,
    janitor_active,
)
from grimoire.missions.launcher import ExecResult, launched_sessions
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.orchestrator_policy import OrchestratorPolicy, load_orchestrator_policy
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT, find_task_worktree

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


def write_policy(root: Path, *, close_issue: bool = False, enabled: bool = True) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        f"orchestrator:\n  roles:\n    worker:\n      enabled: {str(enabled).lower()}\n      wip: 3\n      permission_mode: acceptEdits\n"
        f"  janitor:\n    close_issue: {str(close_issue).lower()}\n",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    return root


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def policy_of(root: Path) -> OrchestratorPolicy:
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    return policy


def worked_task(projet: Path, host: FakeHost, *, description: str = "", push: bool = True, ci: bool = True) -> str:
    """Une tâche lancée par le tick (worktree, claim, session), avec un commit poussé, un créneau CI tenu et une preuve."""
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Janitor", origin="test")
    task = service.ledger.create_task(mission.id, "à nettoyer", acceptance=("ok",), owner="amelia", description=description)
    service.ledger.transition_task(task.id, TaskState.READY)
    report = run_tick(projet, apply=True, exec_=host)
    assert [o.status for o in report.launches] == ["launched"], report.launches
    wt = find_task_worktree(TaskService(projet), task.id)
    assert wt is not None
    (wt.path / "travail.txt").write_text("fait\n", encoding="utf-8")
    git(wt.path, "add", "--", "travail.txt")
    git(wt.path, "commit", "-q", "-m", "travail")
    if push:
        git(wt.path, "push", "-q", "origin", wt.branch)
    service = TaskService(projet)
    if ci:
        service.ledger.append_event(CI_PUSHED_EVENT, task.id, "task", "test", {"branch": wt.branch})
    evidence = projet / "_grimoire-output" / "evidence" / task.id
    evidence.mkdir(parents=True)
    (evidence / "evidence-pack.md").write_text("preuve\n", encoding="utf-8")
    host.calls.clear()
    return task.id


def finish(projet: Path, tid: str, state: TaskState) -> None:
    ledger = TaskService(projet).ledger
    if state is TaskState.CLOSED:
        ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
        ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    elif state is TaskState.FAILED:
        ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, state, actor_id="test")


def occupants(projet: Path) -> frozenset[str]:
    service = TaskService(projet)
    return derive_view(service.ledger, load_capacity(projet), now=datetime.now(UTC)).ci_occupants


def remote_has(projet: Path, branch: str) -> bool:
    return bool(git(projet, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"))


def local_has(projet: Path, branch: str) -> bool:
    return bool(git(projet, "branch", "--list", branch))


def sweep(projet: Path, host: FakeHost, **kw):  # type: ignore[no-untyped-def]
    return Janitor(TaskService(projet), policy_of(projet), exec_=host, **kw).sweep()


def by_action(outcomes) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {o.action: o.status for o in outcomes}


# ── Critère 2 : l'état terminal ─────────────────────────────────────────────────


def test_a_l_etat_terminal_le_janitor_nettoie_tout(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None and wt.path.is_dir() and tid in occupants(projet) and remote_has(projet, wt.branch)
    session = launched_sessions(TaskService(projet))[tid]["session"]
    host.prs[wt.branch] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)

    outcomes = sweep(projet, host)

    assert by_action(outcomes) == {
        "ci_slot": "done", "session": "done", "worktree": "done", "branch_local": "done", "branch_remote": "done",
        "evidence": "done", "issue": "skipped", "breaker": "done",
    }
    assert tid not in occupants(projet)  # le créneau CI est rendu
    assert host.of("claude", "stop") == [["claude", "stop", session]]  # la session détachée est arrêtée
    assert not wt.path.exists() and "worktree" not in git(projet, "worktree", "list")
    assert not local_has(projet, wt.branch) and not remote_has(projet, wt.branch)  # branche fusionnée : partie, en local et en distant
    assert git(projet, "ls-remote", "--heads", "origin", "refs/heads/main")  # main reste
    assert (projet / ARCHIVE_DIR / tid / "evidence-pack.md").read_text(encoding="utf-8") == "preuve\n"
    assert (projet / "_grimoire-output" / "evidence" / tid / "evidence-pack.md").is_file()  # copiée, jamais déplacée
    events = TaskService(projet).ledger.events_for(tid)
    assert {e.payload["action"] for e in events if e.event_type == ACTION_EVENT} >= {"ci_slot", "session", "worktree", "branch_remote"}
    assert any(e.event_type == SWEPT_EVENT for e in events)


def test_le_janitor_est_idempotent(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.prs[f"task/{tid}"] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    assert sweep(projet, host)
    host.calls.clear()
    before = len(TaskService(projet).ledger.events_for(tid))
    assert sweep(projet, host) == () and host.calls == []  # rien n'est rejoué, aucun processus lancé
    assert len(TaskService(projet).ledger.events_for(tid)) == before


@pytest.mark.parametrize("state", [TaskState.CANCELLED, TaskState.FAILED])
def test_annulee_ou_en_echec_le_worktree_part_mais_la_branche_non_fusionnee_reste(projet: Path, host: FakeHost, state: TaskState) -> None:
    tid = worked_task(projet, host)
    branch = f"task/{tid}"
    finish(projet, tid, state)  # aucune PR : rien ne prouve la fusion
    status = by_action(sweep(projet, host))
    assert status["worktree"] == "done" and status["session"] == "done" and status["ci_slot"] == "done"
    assert status["branch_local"] == "refused" and status["branch_remote"] == "refused"
    assert local_has(projet, branch) and remote_has(projet, branch)  # le travail n'est jamais perdu
    assert any(e.event_type == SWEPT_EVENT for e in TaskService(projet).ledger.events_for(tid))  # annulée : gardée pour de bon
    host.calls.clear()
    assert sweep(projet, host) == () and host.calls == []


def test_close_et_pas_fusionnee_le_janitor_reessaie(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    branch = f"task/{tid}"
    host.prs[branch] = "OPEN"
    finish(projet, tid, TaskState.CLOSED)
    first = by_action(sweep(projet, host))
    assert first["branch_local"] == "refused" and first["branch_remote"] == "refused" and local_has(projet, branch)
    assert not any(e.event_type == SWEPT_EVENT for e in TaskService(projet).ledger.events_for(tid))
    logged = len([e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == ACTION_EVENT])
    host.prs[branch] = "MERGED"  # la PR se fusionne plus tard
    second = by_action(sweep(projet, host))
    assert second == {"branch_local": "done", "branch_remote": "done"}  # le reste était fait, rien n'est rejoué
    assert not local_has(projet, branch) and not remote_has(projet, branch)
    assert logged >= 1


def test_un_refus_identique_n_est_pas_rejournalise_a_chaque_tick(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.prs[f"task/{tid}"] = "OPEN"
    finish(projet, tid, TaskState.CLOSED)
    sweep(projet, host)
    count = len([e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == ACTION_EVENT])
    sweep(projet, host), sweep(projet, host)
    assert len([e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == ACTION_EVENT]) == count


def test_verification_impossible_la_branche_n_est_pas_supprimee(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    branch = f"task/{tid}"
    host.gh_broken = True  # échec fermé : un gh muet ne vaut pas « fusionnée »
    finish(projet, tid, TaskState.CLOSED)
    status = by_action(sweep(projet, host))
    assert status["branch_local"] == "failed" and status["branch_remote"] == "failed"
    assert local_has(projet, branch) and remote_has(projet, branch)
    assert status["worktree"] == "done"  # un échec n'arrête pas les autres actions


def test_reponse_de_gh_illisible_la_branche_reste(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    finish(projet, tid, TaskState.CLOSED)

    def odd(argv, cwd, env=None):  # type: ignore[no-untyped-def]
        if argv[:3] == ["gh", "pr", "view"]:
            return ExecResult(0, "pas du json")
        return host(argv, cwd, env)

    out = Janitor(TaskService(projet), policy_of(projet), exec_=odd).sweep()
    assert by_action(out)["branch_local"] == "failed" and local_has(projet, f"task/{tid}")


def test_sans_pr_la_fusion_locale_attestee_suffit(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host, push=False)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    head = git(projet, "rev-parse", wt.branch)
    git(projet, "merge", "-q", "--ff-only", wt.branch)  # la fusion locale de merge_task, avec son reçu
    TaskService(projet).ledger.append_event(MERGED_EVENT, tid, "task", "cli", {"head_sha": head, "into": "main"})
    finish(projet, tid, TaskState.CLOSED)
    status = by_action(sweep(projet, host))
    assert status["branch_local"] == "done" and not local_has(projet, wt.branch)
    assert status["branch_remote"] == "skipped"  # jamais poussée : rien à supprimer en distant


def test_sans_pr_ni_recu_une_branche_contenue_dans_la_cible_reste(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host, push=False)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    git(projet, "merge", "-q", "--ff-only", wt.branch)  # contenue dans main, mais le ledger n'a aucun task.merged
    finish(projet, tid, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["branch_local"] == "refused" and local_has(projet, wt.branch)


def test_un_echec_d_arret_de_session_est_rapporte_puis_rejoue(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.prs[f"task/{tid}"] = "MERGED"
    host.stop_result = ExecResult(1, "", "boom")
    finish(projet, tid, TaskState.CLOSED)
    first = {o.action: o for o in sweep(projet, host)}
    assert first["session"].status == "failed" and "boom" in first["session"].detail
    assert first["worktree"].status == "done"  # les autres actions ont suivi
    assert not any(e.event_type == SWEPT_EVENT for e in TaskService(projet).ledger.events_for(tid))
    host.stop_result = ExecResult(0)
    assert by_action(sweep(projet, host))["session"] == "done"  # rejoué, et la tâche est enfin close pour le janitor
    assert any(e.event_type == SWEPT_EVENT for e in TaskService(projet).ledger.events_for(tid))


def test_une_session_deja_absente_est_un_succes(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.stop_result = ExecResult(1, "", "no such session")
    finish(projet, tid, TaskState.CANCELLED)
    assert by_action(sweep(projet, host))["session"] == "done"


def test_un_arbre_de_travail_sale_n_est_pas_retire(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    (wt.path / "non-commite.txt").write_text("brouillon\n", encoding="utf-8")
    host.prs[wt.branch] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    status = by_action(sweep(projet, host))
    assert status["worktree"] == "failed" and (wt.path / "non-commite.txt").is_file()  # sans --force : le travail reste
    assert status["branch_local"] == "failed" and local_has(projet, wt.branch)  # la branche est extraite : git refuse


def kit_outputs(path: Path, tid: str) -> None:
    """Ce que le relecteur et le runtime du kit écrivent dans le worktree de la tâche, non suivi."""
    reviews = path / "_grimoire-output" / "reviews" / tid
    reviews.mkdir(parents=True)
    (reviews / "verdict.json").write_text('{"verdict": "READY"}\n', encoding="utf-8")
    runtime = path / "_grimoire-runtime-output" / "ledger"
    runtime.mkdir(parents=True)
    (runtime / "trace.jsonl").write_text("{}\n", encoding="utf-8")


def test_un_arbre_qui_ne_porte_que_les_sorties_du_kit_est_retire_et_archive(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    kit_outputs(wt.path, tid)
    host.prs[wt.branch] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    status = by_action(sweep(projet, host))
    assert status["worktree"] == "done" and not wt.path.exists()
    assert status["branch_local"] == "done" and not local_has(projet, wt.branch)
    archive = projet / WORKTREE_ARCHIVE_DIR / tid
    assert (archive / "_grimoire-output" / "reviews" / tid / "verdict.json").read_text(encoding="utf-8") == '{"verdict": "READY"}\n'
    assert (archive / "_grimoire-runtime-output" / "ledger" / "trace.jsonl").is_file()


@pytest.mark.parametrize("extra", ["non-commite.txt", "modifie"])
def test_les_sorties_du_kit_n_excusent_pas_un_autre_changement(projet: Path, host: FakeHost, extra: str) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    kit_outputs(wt.path, tid)
    if extra == "modifie":
        (wt.path / "travail.txt").write_text("réécrit sans commit\n", encoding="utf-8")
    else:
        (wt.path / extra).write_text("brouillon\n", encoding="utf-8")
    host.prs[wt.branch] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["worktree"] == "failed" and wt.path.is_dir()
    assert not (projet / WORKTREE_ARCHIVE_DIR / tid).exists()


def test_le_creneau_ci_est_rendu_meme_sans_gh(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.gh_broken = True
    finish(projet, tid, TaskState.FAILED)
    assert tid in occupants(projet)  # un push vu survit à l'état échoué
    assert by_action(sweep(projet, host))["ci_slot"] == "done" and tid not in occupants(projet)


def test_fermer_l_issue_est_derriere_un_reglage_et_exige_la_fusion(projet: Path, host: FakeHost) -> None:
    write_policy(projet, close_issue=True)
    tid = worked_task(projet, host, description="Closes #42")
    host.prs[f"task/{tid}"] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["issue"] == "done" and host.closed_issues == ["42"]


def test_issue_non_fermee_sans_reglage_ni_fusion(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host, description="Closes #42")
    host.prs[f"task/{tid}"] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["issue"] == "skipped" and host.closed_issues == []
    write_policy(projet, close_issue=True)
    other = worked_task(projet, host, description="Closes #43")
    host.prs[f"task/{other}"] = "OPEN"
    finish(projet, other, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["issue"] == "refused" and host.closed_issues == []


def test_une_tache_close_remet_le_disjoncteur_a_zero_une_tache_en_echec_le_compte(projet: Path, host: FakeHost) -> None:
    record_failure(projet, 5, "ancien")
    ok = worked_task(projet, host)
    host.prs[f"task/{ok}"] = "MERGED"
    finish(projet, ok, TaskState.CLOSED)
    sweep(projet, host)
    assert read_breaker(projet).failures == 0
    bad = worked_task(projet, host)
    finish(projet, bad, TaskState.FAILED)
    sweep(projet, host)
    assert read_breaker(projet).failures == 1


def test_le_janitor_ne_touche_que_les_taches_qu_il_connait(projet: Path, host: FakeHost) -> None:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Humain", origin="test")
    task = service.ledger.create_task(mission.id, "fermée à la main", acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.CANCELLED)
    assert sweep(projet, host) == () and host.calls == []


# ── Dans le tick ────────────────────────────────────────────────────────────────


def test_le_tick_nettoie_et_un_echec_ne_bloque_pas_le_tick(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    host.gh_broken = True
    finish(projet, tid, TaskState.CLOSED)
    report = run_tick(projet, apply=True, exec_=host)
    assert report.status == "ran" and report.plan is not None  # le tick est allé au bout
    assert {o.action: o.status for o in report.cleanup}["branch_local"] == "failed"
    assert any("janitor : branch_local" in a and "failed" in a for a in report.attention)
    assert tid not in occupants(projet)  # le reste a été fait


def test_le_janitor_est_inactif_sans_role_qui_lance_des_sessions(projet: Path, host: FakeHost) -> None:
    write_policy(projet, enabled=False)
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Inactif", origin="test")
    task = service.ledger.create_task(mission.id, "t", acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    assert not janitor_active(service, policy_of(projet))
    report = run_tick(projet, apply=True, exec_=host)
    assert report.cleanup == () and host.calls == []


def test_le_janitor_reste_actif_pour_ce_qui_a_ete_lance_meme_role_coupe(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    write_policy(projet, enabled=False)
    host.prs[f"task/{tid}"] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    report = run_tick(projet, apply=True, exec_=host)
    assert {o.action: o.status for o in report.cleanup}["session"] == "done"


def test_en_plan_le_janitor_dit_ce_qu_il_ferait_sans_rien_toucher(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    finish(projet, tid, TaskState.CANCELLED)
    report = run_tick(projet, apply=False, exec_=host)
    assert [(o.task_id, o.status) for o in report.cleanup] == [(tid, "planned")]
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None and wt.path.is_dir() and host.calls == []


# ── Revue adversariale du lot 2 ─────────────────────────────────────────────────


def push_more(projet: Path, branch: str, *, remote_only: bool = False) -> str:
    """Quelqu'un pousse un commit de plus sur la branche, après la fusion de la PR."""
    clone = projet.parent / "autre-clone"
    if not clone.exists():
        git(projet, "fetch", "-q", "origin", branch)
        git(projet.parent, "clone", "-q", str(projet.parent / "remote.git"), str(clone))
        git(clone, "config", "user.name", "Autre")
        git(clone, "config", "user.email", "autre@example.invalid")
        git(clone, "config", "commit.gpgsign", "false")
    git(clone, "checkout", "-q", branch)
    (clone / "apres-fusion.txt").write_text("nouveau\n", encoding="utf-8")
    git(clone, "add", "--", "apres-fusion.txt")
    git(clone, "commit", "-q", "-m", "apres la fusion")
    git(clone, "push", "-q", "origin", branch)
    return git(clone, "rev-parse", "HEAD")


def test_pr_fusionnee_puis_nouveaux_commits_en_distant_la_branche_distante_reste(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    host.prs[wt.branch] = "MERGED"  # headRefOid = la tête fusionnée (la locale)
    merged_head = git(projet, "rev-parse", f"refs/heads/{wt.branch}")
    host.pr_heads[wt.branch] = merged_head
    pushed = push_more(projet, wt.branch)
    finish(projet, tid, TaskState.CLOSED)
    result = by_action(sweep(projet, host))
    assert result["branch_remote"] == "refused"
    assert git(projet, "ls-remote", "--heads", "origin", f"refs/heads/{wt.branch}").split()[0] == pushed  # les commits sont sauvés
    assert result["branch_local"] == "done"  # la locale est bien la tête fusionnée


def test_pr_fusionnee_puis_commits_locaux_la_branche_locale_reste(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    host.prs[wt.branch] = "MERGED"
    host.pr_heads[wt.branch] = git(projet, "rev-parse", f"refs/heads/{wt.branch}")
    (wt.path / "plus-tard.txt").write_text("x\n", encoding="utf-8")
    git(wt.path, "add", "--", "plus-tard.txt")
    git(wt.path, "commit", "-q", "-m", "après la fusion")
    finish(projet, tid, TaskState.CLOSED)
    result = by_action(sweep(projet, host))
    assert result["branch_local"] == "refused" and local_has(projet, wt.branch)
    assert result["branch_remote"] == "done"  # la distante est bien la tête fusionnée : seule la locale a avancé


def test_json_partiel_sans_tete_fusionnee_aucune_branche_supprimee(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    host.prs[wt.branch] = "MERGED"
    host.pr_no_head = True
    finish(projet, tid, TaskState.CLOSED)
    result = by_action(sweep(projet, host))
    assert result["branch_local"] == "failed" and result["branch_remote"] == "failed"
    assert local_has(projet, wt.branch) and remote_has(projet, wt.branch)


def test_une_ancienne_pr_fusionnee_d_une_tentative_precedente_ne_couvre_pas_la_branche(projet: Path, host: FakeHost) -> None:
    tid = worked_task(projet, host)
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    host.prs[wt.branch] = "MERGED"
    host.pr_heads[wt.branch] = "a" * 40  # la PR fusionnée porte une autre tête
    finish(projet, tid, TaskState.CLOSED)
    result = by_action(sweep(projet, host))
    assert result["branch_local"] == "refused" and result["branch_remote"] == "refused"
    assert local_has(projet, wt.branch) and remote_has(projet, wt.branch)


@pytest.mark.parametrize("code", [124, 126, 127])
def test_claude_stop_qui_ne_s_execute_pas_n_est_pas_une_session_absente(projet: Path, host: FakeHost, code: int) -> None:
    tid = worked_task(projet, host)
    host.stop_result = ExecResult(code, "", "claude : binaire introuvable")
    finish(projet, tid, TaskState.CANCELLED)
    result = by_action(sweep(projet, host))
    assert result["session"] == "failed"


def test_une_branche_task_que_le_lanceur_n_a_pas_posee_n_est_jamais_touchee(projet: Path, host: FakeHost) -> None:
    """Une tâche close avec un créneau CI mais sans worktree noté : sa branche ``task/<id>`` est celle de quelqu'un d'autre."""
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="x", origin="test")
    task = service.ledger.create_task(mission.id, "manuelle", acceptance=("ok",), owner="amelia")
    git(projet, "branch", f"task/{task.id}")
    git(projet, "push", "-q", "origin", f"task/{task.id}")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.ledger.append_event(CI_PUSHED_EVENT, task.id, "task", "test", {"branch": f"task/{task.id}"})
    service.ledger.transition_task(task.id, TaskState.CANCELLED, actor_id="test")
    host.prs[f"task/{task.id}"] = "MERGED"
    result = by_action(sweep(projet, host))
    assert result.get("branch_local") == "skipped" and result.get("branch_remote") == "skipped"
    assert local_has(projet, f"task/{task.id}") and remote_has(projet, f"task/{task.id}")


def test_une_simple_mention_d_issue_ne_la_ferme_pas(projet: Path, host: FakeHost) -> None:
    write_policy(projet, close_issue=True)
    tid = worked_task(projet, host, description="Voir aussi issue #42 pour le contexte")
    host.prs[f"task/{tid}"] = "MERGED"
    finish(projet, tid, TaskState.CLOSED)
    assert by_action(sweep(projet, host))["issue"] == "skipped" and host.closed_issues == []
