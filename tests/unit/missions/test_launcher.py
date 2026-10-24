"""Lot 2 de #876 (verrous 1 et 13) : le lanceur du rôle worker et le disjoncteur, contre un faux ``claude``.

Critères 1 et 3 : le tick lance une session détachée par tâche admise, dans le worktree ``task/<id>``, dans la limite
du WIP et du plafond de sessions par jour ; rôle désactivé, rien n'est lancé ; disjoncteur ouvert, rien n'est lancé, et il
s'ouvre après des échecs répétés. Chaque garde échoue fermée.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import breaker as brk
from grimoire.missions.admission import Admit
from grimoire.missions.breaker import read_breaker, record_failure, record_success, reset, trip
from grimoire.missions.launcher import LAUNCH_FAILED_EVENT, LAUNCHED_EVENT, WorkerLauncher, launched_sessions
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import find_task_worktree

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
runner = CliRunner()


def write_policy(root: Path, *, enabled: bool = True, wip: int = 3, per_day: int = 5, extra: str = "", breaker: int = 3) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n"
        f"      enabled: {str(enabled).lower()}\n      wip: {wip}\n      sessions_per_day: {per_day}\n{extra}"
        f"  breaker:\n    threshold: {breaker}\n",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def ready(service: TaskService, title: str) -> str:
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="Lot 2", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=("le travail est fait",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def tick(projet: Path, host: FakeHost, now: datetime | None = None):  # type: ignore[no-untyped-def]
    return run_tick(projet, apply=True, exec_=host, now=now)


# ── Critère 1 : le lanceur ─────────────────────────────────────────────────────


def test_le_tick_lance_une_session_par_tache_admise_dans_son_worktree(projet: Path, service: TaskService, host: FakeHost) -> None:
    first, second = ready(service, "une"), ready(service, "deux")
    report = tick(projet, host)
    launched = [o for o in report.launches if o.status == "launched"]
    assert {o.task_id for o in launched} == {first, second}
    bg = host.of("claude", "--bg")
    assert len(bg) == 2
    for argv, cwd, env in ((a, c, e) for a, c, e in host.calls if a[:2] == ["claude", "--bg"]):
        tid = argv[argv.index("--name") + 1].removeprefix("grimoire-")
        assert cwd == find_task_worktree(TaskService(projet), tid).path  # type: ignore[union-attr]
        assert cwd.name == tid and (cwd / ".git").is_file()  # le worktree task/<id>, pas le projet
        assert env == {"GRIMOIRE_UNATTENDED": "1"}
    after = TaskService(projet)
    assert {after.require(t).status for t in (first, second)} == {TaskState.CLAIMED}  # claim et bail posés avant le lancement
    assert set(launched_sessions(after)) == {first, second}
    assert [o.session for o in launched] == sorted(o.session for o in launched) and len({o.session for o in launched}) == 2


def test_la_syntaxe_est_celle_de_claude_bg_et_jamais_un_sous_agent(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, extra="      model: sonnet\n      permission_mode: acceptEdits\n")
    tid = ready(service, "syntaxe")
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert argv[:4] == ["claude", "--bg", "--name", f"grimoire-{tid}"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert tid in argv[-1]  # la consigne est le dernier argument, sans shell
    # Verrou 13 : une session distincte (son budget, ses compteurs), jamais l'outil Agent.
    assert not {"--agent", "--agents", "--resume", "--continue", "--fork-session"} & set(argv)


def test_le_modele_vient_de_la_politique_puis_du_palier(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, extra="      tier: strong\n")
    ready(service, "palier")
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert argv[argv.index("--model") + 1] == "opus"


def test_role_desactive_rien_n_est_lance(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, enabled=False)
    tid = ready(service, "désactivé")
    report = tick(projet, host)
    assert host.calls == [] and report.launches == () and report.dispatched == ()
    assert TaskService(projet).require(tid).status is TaskState.READY and find_task_worktree(TaskService(projet), tid) is None


def test_sans_politique_le_comportement_est_inchange(projet: Path, service: TaskService, host: FakeHost) -> None:
    (projet / POLICY).unlink()
    tid = ready(service, "sans politique")
    report = tick(projet, host)
    assert host.calls == [] and report.launches == () and report.plan is not None
    assert [a.task_id for a in report.plan.admitted] == [tid] and report.breaker is not None and not report.breaker.open
    assert TaskService(projet).require(tid).status is TaskState.READY


def test_le_wip_borne_les_sessions_en_cours(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, wip=1)
    ready(service, "a"), ready(service, "b")
    first = tick(projet, host)
    assert [o.status for o in first.launches] == ["launched", "skipped"]
    assert "WIP du worker atteint (1/1)" in first.launches[1].detail
    assert len(host.of("claude", "--bg")) == 1
    again = tick(projet, host)  # la première session tient toujours son créneau de WIP
    assert [o.status for o in again.launches] == ["skipped"] and len(host.of("claude", "--bg")) == 1


def test_le_plafond_de_sessions_par_jour_borne_les_lancements(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, wip=9, per_day=2)
    for n in range(3):
        ready(service, f"t{n}")
    report = tick(projet, host)
    assert [o.status for o in report.launches] == ["launched", "launched", "skipped"]
    assert "plafond de sessions du jour atteint (2/2)" in report.launches[2].detail
    assert len(host.of("claude", "--bg")) == 2
    # Un autre jour, le compteur repart : le lanceur, appelé à une autre date, lance la troisième.
    after = TaskService(projet)
    policy = load_orchestrator_policy(projet).policy
    third = report.launches[2].task_id
    later = WorkerLauncher(after, policy, exec_=host, now=datetime(2099, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
    assert [o.status for o in later((Admit(third),))] == ["launched"]


def test_politique_illisible_rien_n_est_lance(projet: Path, service: TaskService, host: FakeHost) -> None:
    (projet / POLICY).write_text("orchestrator:\n  roles:\n    worker:\n      enabled: yes-please\n", encoding="utf-8")
    assert load_orchestrator_policy(projet).policy is None
    tid = ready(service, "illisible")
    report = tick(projet, host)
    assert host.calls == [] and any("politique d'orchestration illisible" in a for a in report.attention)
    assert TaskService(projet).require(tid).status is TaskState.READY


@pytest.mark.parametrize(
    "body",
    [
        "orchestrator:\n  roles:\n    worker:\n      wip: 0\n",
        "orchestrator:\n  roles:\n    worker:\n      wip: true\n",
        "orchestrator:\n  roles:\n    worker:\n      colour: red\n",
        "orchestrator:\n  roles:\n    reviewer:\n      enabled: true\n",
        "orchestrator:\n  roles:\n    worker:\n      tier: gigantic\n",
        "orchestrator:\n  breaker:\n    threshold: -1\n",
        "orchestrator: [1, 2]\n",
        "orchestrator: {unclosed",
    ],
)
def test_la_politique_echoue_fermee(projet: Path, body: str) -> None:
    (projet / POLICY).write_text(body, encoding="utf-8")
    read = load_orchestrator_policy(projet)
    assert read.policy is None and read.error and not read.usable


def test_les_defauts_sont_ceux_du_pilote(tmp_path: Path) -> None:
    read = load_orchestrator_policy(tmp_path)
    assert read.usable and read.source == "default"
    worker = read.policy.worker  # type: ignore[union-attr]
    assert (worker.enabled, worker.wip, worker.sessions_per_day) == (False, 1, 5) and read.policy.breaker_threshold == 3  # type: ignore[union-attr]


def test_une_preparation_refusee_ne_lance_rien_et_ne_compte_pas_au_disjoncteur(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready(service, "branche existante")
    from lot2_doubles import git

    git(projet, "branch", f"task/{tid}")  # une branche sans worktree noté : create_task_worktree refuse, rien n'est écrasé
    report = tick(projet, host)
    assert report.launches[0].status == "failed" and "préparation refusée" in report.launches[0].detail
    assert host.calls == [] and read_breaker(projet).failures == 0
    assert TaskService(projet).require(tid).status is TaskState.READY


# ── Critère 3 : le disjoncteur ─────────────────────────────────────────────────


def test_un_lancement_echoue_rend_le_claim_et_compte_au_disjoncteur(projet: Path, service: TaskService, host: FakeHost) -> None:
    host.bg_fails = True
    tid = ready(service, "échec")
    report = tick(projet, host)
    assert report.launches[0].status == "failed" and "claude --bg a échoué" in report.launches[0].detail
    after = TaskService(projet)
    assert after.require(tid).status is TaskState.READY  # le claim est rendu : la tâche repart à la file
    assert any(e.event_type == LAUNCH_FAILED_EVENT for e in after.ledger.events_for(tid))
    assert read_breaker(projet).failures == 1 and not read_breaker(projet).open


def test_le_disjoncteur_s_ouvre_apres_n_echecs_consecutifs_et_bloque_tout_lancement(projet: Path, service: TaskService, host: FakeHost) -> None:
    host.bg_fails = True
    for n in range(3):
        ready(service, f"t{n}")
    first = tick(projet, host)
    assert [o.status for o in first.launches] == ["failed", "failed", "failed"]  # N = 3 : le troisième échec ouvre
    ready(service, "t3")
    assert read_breaker(projet).open and "3 échec(s) consécutif(s)" in read_breaker(projet).reason
    assert any("disjoncteur" in a and "OUVERT" in a for a in first.attention)
    host.bg_fails = False
    calls = len(host.calls)
    again = tick(projet, host)
    assert len(host.calls) == calls and all(o.status == "skipped" and "disjoncteur ouvert" in o.detail for o in again.launches)
    assert again.breaker is not None and again.breaker.open  # l'état s'affiche dans le rapport du tick


def test_le_seuil_du_disjoncteur_est_reglable(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, breaker=1)
    host.bg_fails = True
    ready(service, "a"), ready(service, "b")
    assert [o.status for o in tick(projet, host).launches] == ["failed", "skipped"]


def test_trip_ouvre_sur_un_evenement_externe_et_garde_la_premiere_raison(projet: Path, service: TaskService, host: FakeHost) -> None:
    ready(service, "a")
    state = trip(projet, "check modifié après la création de GAO-1", source="lot1")
    assert state.open and state.source == "lot1"
    assert trip(projet, "autre raison").reason == "check modifié après la création de GAO-1"
    report = tick(projet, host)
    assert host.calls == [] and report.launches[0].status == "skipped"


def test_un_disjoncteur_ouvert_ne_se_referme_ni_par_un_succes_ni_par_le_temps(projet: Path) -> None:
    trip(projet, "externe")
    assert record_success(projet).open
    assert record_failure(projet, 3, "x").open
    assert read_breaker(projet).open


def test_un_succes_remet_le_compteur_a_zero(projet: Path) -> None:
    record_failure(projet, 3, "a"), record_failure(projet, 3, "b")
    assert read_breaker(projet).failures == 2
    assert record_success(projet).failures == 0


def test_un_etat_illisible_vaut_ouvert(projet: Path) -> None:
    path = projet / brk.BREAKER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{pas du json", encoding="utf-8")
    state = read_breaker(projet)
    assert state.open and state.source == "unreadable"
    assert not reset(projet).open  # l'action humaine le referme


def test_reset_referme_et_journalise(projet: Path) -> None:
    trip(projet, "externe")
    state = reset(projet, actor="guilhem")
    assert not state.open and state.failures == 0
    assert [h["event"] for h in state.history][-2:] == ["trip", "reset"] and state.history[-1]["actor"] == "guilhem"


# ── CLI ─────────────────────────────────────────────────────────────────────────


def test_cli_breaker_status_trip_reset(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = str(projet)
    out = runner.invoke(app, ["orchestrate", "breaker", "trip", "essai", "--project-root", root])
    assert out.exit_code == 0 and "OUVERT" in out.output
    status = runner.invoke(app, ["--output", "json", "orchestrate", "breaker", "status", "--project-root", root])
    assert json.loads(status.output)["open"] is True
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "agent")
    refused = runner.invoke(app, ["orchestrate", "breaker", "reset", "--project-root", root])
    assert refused.exit_code == 1 and read_breaker(projet).open  # un agent ne referme pas le disjoncteur
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")
    done = runner.invoke(app, ["orchestrate", "breaker", "reset", "--project-root", root])
    assert done.exit_code == 0 and not read_breaker(projet).open


def test_cli_tick_affiche_le_disjoncteur_et_les_lancements(projet: Path, service: TaskService, host: FakeHost) -> None:
    ready(service, "rendu")
    from grimoire.cli import cmd_orchestrate

    report = tick(projet, host)
    cmd_orchestrate._render_tick(report)
    assert report.launches and report.launches[0].status == "launched"
    payload = report.to_dict()
    assert payload["launches"][0]["status"] == "launched" and payload["breaker"]["open"] is False and payload["cleanup"] == []


def test_cli_tick_plan_ne_lance_rien(projet: Path, service: TaskService) -> None:
    tid = ready(service, "plan")
    out = runner.invoke(app, ["--output", "json", "orchestrate", "tick", "--plan", "--project-root", str(projet)])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["launches"] == [] and data["breaker"]["open"] is False
    assert TaskService(projet).require(tid).status is TaskState.READY


def test_le_minuteur_porte_le_path_du_poste(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions.timer import plan_timer

    monkeypatch.setenv("PATH", "/usr/local/bin:/opt/claude/bin")
    assert 'Environment="PATH=/usr/local/bin:/opt/claude/bin"' in plan_timer(projet).service


# ── Fumée réelle, désactivée par défaut ─────────────────────────────────────────


@pytest.mark.live_host
def test_fumee_reelle_une_tache_jouet_sur_l_hote(projet: Path, service: TaskService) -> None:
    """Lance une vraie session ``claude --bg`` sur une tâche jouet. PAYANT, hors CI : ``pytest -m live_host``.

    Nettoyage : le janitor arrête la session une fois la tâche annulée ; le test n'est jamais lancé par défaut.
    """
    import shutil

    if shutil.which("claude") is None:
        pytest.skip("claude absent du poste")
    write_policy(projet, wip=1, per_day=1, extra="      model: haiku\n      permission_mode: plan\n")
    tid = ready(service, "tâche jouet : écrire un fichier vide hello.txt")
    report = run_tick(projet, apply=True)
    assert [o.status for o in report.launches] == ["launched"]
    service.ledger.transition_task(tid, TaskState.CANCELLED, actor_id="test")
    cleaned = run_tick(projet, apply=True)
    assert any(o.action == "session" and o.status == "done" for o in cleaned.cleanup)
    assert LAUNCHED_EVENT in {e.event_type for e in TaskService(projet).ledger.events_for(tid)}


# ── Revue adversariale du lot 2 : base et cible viennent de la politique ou d'origin/HEAD ─────────────


def _checkout_elsewhere(projet: Path) -> str:
    """Le poste est sur une autre branche, en avance sur main : un tick lancé d'ici ne doit pas en partir."""
    git(projet, "checkout", "-q", "-b", "autre-travail")
    (projet / "autre.txt").write_text("pas dans main\n", encoding="utf-8")
    git(projet, "add", "--", "autre.txt")
    git(projet, "commit", "-q", "-m", "autre")
    return git(projet, "rev-parse", "HEAD")


def test_hors_de_main_le_worktree_part_de_la_branche_par_defaut_du_remote(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready(service, "une")
    elsewhere = _checkout_elsewhere(projet)
    report = tick(projet, host)
    assert [o.status for o in report.launches] == ["launched"], report.launches
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None
    assert wt.into == "main" and wt.base_sha == git(projet, "rev-parse", "refs/remotes/origin/main") != elsewhere
    assert not (wt.path / "autre.txt").exists()


def test_la_cle_base_de_la_politique_prime_sur_origin_head(projet: Path, service: TaskService, host: FakeHost) -> None:
    git(projet, "branch", "release", "main")
    tid = ready(service, "une")
    (projet / POLICY).write_text(
        "orchestrator:\n  base: release\n  roles:\n    worker:\n      enabled: true\n", encoding="utf-8"
    )
    _checkout_elsewhere(projet)
    report = tick(projet, host)
    assert [o.status for o in report.launches] == ["launched"], report.launches
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None and wt.into == "release" and wt.base_sha == git(projet, "rev-parse", "refs/heads/release")


def test_sans_base_ni_origin_head_rien_n_est_lance(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready(service, "une")
    git(projet, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    report = tick(projet, host)
    assert [o.status for o in report.launches] == ["failed"] and "origin/HEAD" in report.launches[0].detail
    assert host.of("claude", "--bg") == [] and find_task_worktree(TaskService(projet), tid) is None
    assert read_breaker(projet).open is False  # pas une panne de session


def test_une_base_qui_n_est_pas_un_nom_de_branche_rend_la_politique_illisible(projet: Path) -> None:
    (projet / POLICY).write_text("orchestrator:\n  base: '--upload-pack=x'\n", encoding="utf-8")
    assert load_orchestrator_policy(projet).usable is False
