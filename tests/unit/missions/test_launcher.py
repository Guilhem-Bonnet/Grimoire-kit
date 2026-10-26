"""Lot 2 de #876 (verrous 1 et 13) : le lanceur du rôle worker et le disjoncteur, contre un faux ``claude``.

Critères 1 et 3 : le tick lance une session détachée par tâche admise, dans le worktree ``task/<id>``, dans la limite
du WIP et du plafond de sessions par jour ; rôle désactivé, rien n'est lancé ; disjoncteur ouvert, rien n'est lancé, et il
s'ouvre après des échecs répétés. Chaque garde échoue fermée.
"""

from __future__ import annotations

import json
import os
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
    if enabled and "permission_mode" not in extra:
        extra += "      permission_mode: acceptEdits\n"  # explicite : un rôle activé sans mode est refusé
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
        # Lot 3 : l'identifiant de session est fixé par le lanceur, le même pour claude et pour les commandes grimoire.
        assert env == {"GRIMOIRE_UNATTENDED": "1", "GRIMOIRE_SESSION_ID": argv[argv.index("--session-id") + 1]}
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
    assert [a.task_id for a in report.plan.admitted] == [tid] and report.breaker is None
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
    assert payload["launches"][0]["status"] == "launched" and "breaker" not in payload and "cleanup" not in payload


def test_cli_tick_plan_ne_lance_rien(projet: Path, service: TaskService) -> None:
    tid = ready(service, "plan")
    out = runner.invoke(app, ["--output", "json", "orchestrate", "tick", "--plan", "--project-root", str(projet)])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert not {"launches", "cleanup", "breaker"} & set(data)  # rôle désactivé : la sortie d'avant, clé pour clé
    assert TaskService(projet).require(tid).status is TaskState.READY


def _shim(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        exe = directory / name
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    return directory


@pytest.mark.skipif(os.name == "nt", reason="unité systemd et faux binaires à shebang : harnais POSIX")
def test_le_minuteur_porte_le_path_du_poste_reduit_aux_outils_utiles(projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le PATH n'est pas figé tel quel : ni un dossier temporaire (session d'agent), ni un dossier sans outil utile, ni un chemin relatif."""
    import tempfile

    from grimoire.missions.timer import plan_timer

    good = _shim(tmp_path / "bin", "claude", "gh")
    git_dir = _shim(tmp_path / "gitbin", "git")
    agent_tmp = tmp_path / "scratch"
    shim = _shim(agent_tmp / "claude-session-1a2b" / "bin", "claude", "gh")  # le PATH d'une session d'agent
    secret = _shim(tmp_path / "secret-token-AKIA123" / "bin", "unrelated")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(agent_tmp))
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim), "relatif/bin", str(secret), str(good), str(git_dir), str(tmp_path / "absent")]))
    service = plan_timer(projet).service
    line = next(row for row in service.splitlines() if row.startswith("Environment="))
    assert str(good) in line and str(git_dir) in line  # claude, gh et git y sont
    for banned in (str(shim), "relatif", "secret-token", "absent", "claude-session"):
        assert banned not in line
    assert "/usr/bin" in line  # les dossiers système restent


def test_sans_aucun_outil_utile_le_minuteur_ne_fige_que_les_dossiers_systeme(projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions.timer import plan_timer

    monkeypatch.setenv("PATH", str(_shim(tmp_path / "vide", "autre")))
    line = next(row for row in plan_timer(projet).service.splitlines() if row.startswith("Environment="))
    assert "vide" not in line and "/usr/bin" in line


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
        "orchestrator:\n  base: release\n  roles:\n    worker:\n      enabled: true\n      permission_mode: plan\n", encoding="utf-8"
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


# ── Revue adversariale du lot 2 : mode de permission explicite, valeurs sûres, bornes ─────────────────


def test_un_worker_active_sans_permission_mode_est_refuse(projet: Path, service: TaskService, host: FakeHost) -> None:
    """Sans `--permission-mode`, une session de fond se bloque à la première demande : le mode doit être déclaré."""
    (projet / POLICY).write_text("orchestrator:\n  roles:\n    worker:\n      enabled: true\n", encoding="utf-8")
    read = load_orchestrator_policy(projet)
    assert read.policy is None and "permission_mode" in read.error
    ready(service, "sans mode")
    report = tick(projet, host)
    assert report.launches == () and host.of("claude", "--bg") == []
    assert any("permission_mode" in a for a in report.attention)


@pytest.mark.parametrize(("key", "value"), [("permission_mode", "--dangerously-skip-permissions"), ("model", "-x"), ("model", "a b")])
def test_modele_et_mode_ne_peuvent_pas_etre_une_option(projet: Path, key: str, value: str) -> None:
    extra = "      permission_mode: plan\n" if key == "model" else ""
    (projet / POLICY).write_text(
        f"orchestrator:\n  roles:\n    worker:\n      enabled: true\n{extra}      {key}: '{value}'\n", encoding="utf-8"
    )
    assert load_orchestrator_policy(projet).usable is False


def test_le_wip_compte_aussi_les_sessions_bloquees_en_attente_de_verification(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, wip=1)
    first = ready(service, "a")
    tick(projet, host)
    ledger = TaskService(projet).ledger
    ledger.transition_task(first, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(first, TaskState.NEEDS_VERIFICATION, actor_id="worker")  # la session vit encore, en attente de relecture
    ready(TaskService(projet), "b")
    again = tick(projet, host)
    assert [o.status for o in again.launches] == ["skipped"] and len(host.of("claude", "--bg")) == 1


def test_un_lancement_echoue_compte_au_plafond_du_jour(projet: Path, service: TaskService, host: FakeHost) -> None:
    """Un `claude --bg` qui sort en délai dépassé a pu démarrer une session : la tentative compte."""
    write_policy(projet, wip=9, per_day=1, breaker=9)
    host.bg_fails = True
    ready(service, "a"), ready(service, "b")
    report = tick(projet, host)
    assert [o.status for o in report.launches] == ["failed", "skipped"]
    assert "plafond" in report.launches[1].detail


def test_sans_bloc_orchestrator_le_tick_ne_dit_rien_de_plus_qu_avant(projet: Path, service: TaskService, host: FakeHost, capsys: pytest.CaptureFixture[str]) -> None:
    (projet / POLICY).unlink()
    ready(service, "inchange")
    from grimoire.cli import cmd_orchestrate

    report = tick(projet, host)
    cmd_orchestrate._render_tick(report)
    assert "Disjoncteur" not in capsys.readouterr().out
    assert not {"launches", "cleanup", "breaker"} & set(report.to_dict())
    trip(projet, "essai")
    opened = tick(projet, host)
    cmd_orchestrate._render_tick(opened)
    assert "Disjoncteur" in capsys.readouterr().out and "breaker" in opened.to_dict()  # ouvert : on le dit


# ── #961 : le battement des workers vivants ───────────────────────────────────


def _later(minutes: int) -> datetime:
    from datetime import timedelta

    return datetime.now(UTC) + timedelta(minutes=minutes)


def test_un_worker_vivant_garde_son_bail_au_dela_du_ttl(projet: Path, service: TaskService, host: FakeHost) -> None:
    """Le bail n'est écrit qu'au claim : sans battement, 25 min de travail suffisaient à garer la tâche en waiting(human)."""
    tid = ready(service, "longue")
    (launch,) = tick(projet, host).launches
    host.agents = [{"id": launch.session, "kind": "background", "status": "busy", "name": f"grimoire-{tid}"}]
    for minutes in (15, 30, 45):  # trois ticks espacés de moins d'un TTL, bien au-delà du premier
        report = tick(projet, host, now=_later(minutes))
        assert not report.reaped, report.reaped
    assert TaskService(projet).require(tid).status is TaskState.CLAIMED


def test_un_worker_mort_au_repos_ou_illisible_ne_renouvelle_rien(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready(service, "morte")
    tick(projet, host)
    host.agents = None  # `claude agents` en panne : échec fermé, la récolte juge comme avant
    report = tick(projet, host, now=_later(25))
    assert [d.task_id for d in report.reaped] == [tid]
    other = ready(TaskService(projet), "autre")
    (second,) = (o for o in tick(projet, host).launches if o.task_id == other)
    name = f"grimoire-{other}"
    host.agents = [
        {"id": second.session, "kind": "interactive", "status": "busy", "name": name},  # pas une session de fond
        {"id": "ffffffff", "kind": "background", "status": "busy", "name": name},  # une autre session, même nom
        {"id": second.session, "kind": "background", "status": "busy", "name": "grimoire-ailleurs"},  # même id, autre nom
        {"id": second.session, "kind": "background", "status": "idle", "name": name},  # son tour est fini : elle ne travaille plus
    ]
    assert other in [d.task_id for d in tick(projet, host, now=_later(50)).reaped]


def test_la_session_d_un_claim_recolte_ne_tient_pas_le_claim_suivant(projet: Path, service: TaskService, host: FakeHost) -> None:
    """Une session restée vivante après la récolte de son claim ne renouvelle pas celui d'un autre titulaire."""
    tid = ready(service, "reprise")
    (launch,) = tick(projet, host).launches
    later = TaskService(projet)
    later.ledger.transition_task(tid, TaskState.READY, actor_id="worker", reason="rendue")
    later.claim(tid, "humain")  # epoch suivant, hors du lanceur : le dernier lancement reste celui de l'epoch 1
    host.agents = [{"id": launch.session, "kind": "background", "status": "busy", "name": f"grimoire-{tid}"}]
    assert tid in [d.task_id for d in tick(projet, host, now=_later(25)).reaped]


def test_un_battement_en_erreur_ne_casse_pas_le_tick(projet: Path, service: TaskService, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import launcher

    ready(service, "solide")
    tick(projet, host)

    def boom(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise OSError("disque plein")

    monkeypatch.setattr(launcher, "renew_live_workers", boom)
    report = tick(projet, host, now=_later(25))
    assert report.reaped and any("battement des workers" in line for line in report.attention)
