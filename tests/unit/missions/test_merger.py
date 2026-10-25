"""Lot 4 de #876 (verrou 12) : le merger, contre un faux ``gh`` et un faux ``claude``, sur un vrai git.

Critères : la file série fusionne une tâche relue READY dont la PR est ouverte, CI verte exigée (checks ignorés ou
non requis hors du verdict), branche réalignée par une fusion de la cible (union contrôlée du CHANGELOG, tout autre
conflit rendu au worker), fusion seulement sous un mandat qui couvre ``merge`` et que la politique délègue (sinon la
question « fusionner ? »), fusion notée une fois constatée sur la remote, acceptation sous mandat ou question
« accepter ? », catégories gardées à l'humain, chaque étape notée une fois et rejouable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git, make_repo

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker, trip
from grimoire.missions.launcher import launched_sessions
from grimoire.missions.mandate import ORIGIN_HUMAN, MandateRequest, MandateStore
from grimoire.missions.merger import (
    MERGE_BOUNCED_EVENT,
    MERGE_ESCALATED_EVENT,
    MERGE_REALIGNED_EVENT,
    MERGE_REQUEUED_EVENT,
    MERGE_RESUMED_EVENT,
)
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.review_receipt import write_receipt
from grimoire.missions.reviewer import REVIEW_LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
CRITERIA = ("la route rend 404 sur un id inconnu", "relu par un autre agent")
GREEN = [{"name": "tests", "bucket": "pass"}, {"name": "codecov/patch", "bucket": "fail"}]  # codecov : ignoré par défaut
CHANGELOG_MAIN = (
    "# Changelog\n\n## [Unreleased]\n\n- feat: une entrée de main\n\n## [1.0.0]\n\n"
    + "".join(f"- livré {n}\n" for n in range(1, 9)) + "- init\n"
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path, *, merger: str = "    merger:\n      enabled: true\n", escalation: str = "") -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        "      permission_mode: acceptEdits\n    reviewer:\n      enabled: true\n      wip: 3\n"
        f"      permission_mode: acceptEdits\n{merger}{escalation}",
        encoding="utf-8",
    )


DELEGATED = "escalation:\n  merge: orchestrator\n  accept: orchestrator\n"


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    # Ce que le kit écrit dans un worktree (reçus, preuve) n'est pas suivi : le janitor peut retirer le worktree.
    (root / ".gitignore").write_text("_grimoire-output/\n_grimoire-runtime-output/\n_grimoire/\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG_MAIN, encoding="utf-8")
    git(root, "add", "--", ".gitignore", "CHANGELOG.md")
    git(root, "commit", "-q", "-m", "gitignore et changelog")
    git(root, "push", "-q", "origin", "main")
    write_policy(root, escalation=DELEGATED)
    return root


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def grant(root: Path, gestures: tuple[str, ...] = ("merge", "accept")) -> md.Mandate:
    request = MandateRequest.build(gestures, ("implementation",), 5, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def tick(projet: Path, host: FakeHost):  # type: ignore[no-untyped-def]
    return run_tick(projet, apply=True, exec_=host)


def events(projet: Path, tid: str, kind: str) -> list:  # type: ignore[type-arg]
    return [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == kind]


def reviewer_sid(host: FakeHost) -> str:
    argv = [a for a in host.of("claude", "--bg") if "--name" in a and a[a.index("--name") + 1].startswith("grimoire-review-")][-1]
    return argv[argv.index("--session-id") + 1]


def launch_and_work(projet: Path, host: FakeHost, *, labels: tuple[str, ...] = (), edit: tuple[str, str] = ("route.py", "404\n"),
                    changelog: str = "") -> tuple[str, Path]:
    """Une tâche lancée par le tick, dont le worker a commité, poussé et ouvert la PR, remise en revue."""
    service = TaskService(projet)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="Lot 4", origin="test")
    task = service.ledger.create_task(mission.id, f"tâche {edit[0]}", acceptance=CRITERIA, owner="amelia",
                                      extra={"labels": list(labels)} if labels else None)
    service.ledger.transition_task(task.id, TaskState.READY)
    tick(projet, host)
    wt = Path(str(launched_sessions(TaskService(projet))[task.id]["cwd"]))
    (wt / edit[0]).write_text(edit[1], encoding="utf-8")
    files = [edit[0]]
    if changelog:
        (wt / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
        files.append("CHANGELOG.md")
    git(wt, "add", "--", *files)
    git(wt, "commit", "-q", "-m", f"travail de {task.id}")
    git(wt, "push", "-q", "origin", f"task/{task.id}")
    branch = f"task/{task.id}"
    host.prs[branch] = "OPEN"
    host.checks[branch] = list(GREEN)
    ledger = TaskService(projet).ledger
    ledger.transition_task(task.id, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    return task.id, wt


def give_ready(host: FakeHost, tid: str, wt: Path, session: str = "") -> None:
    """Ce que le relecteur lancé laisse par `task review` : un pack, puis le reçu READY de sa session."""
    item = EvidenceItem.from_text("check-01", EvidenceKind.TEST, "pytest -> exit 0", uri="check:pytest", summary="pytest -> exit 0")
    EvidenceService(wt / EVIDENCE).create_pack(tid, EvidenceProfile.LIGHT, [item], acceptance=CRITERIA)
    write_receipt(wt, tid, "READY", reviewer_id="reviewer", reviewer_session=session or reviewer_sid(host))


def review_ready(projet: Path, host: FakeHost, tid: str, wt: Path):  # type: ignore[no-untyped-def]
    """Le relecteur lancé rend READY ; le tick qui le juge fait passer le merger."""
    tick(projet, host)  # lance le relecteur
    give_ready(host, tid, wt)
    return tick(projet, host)


def merges(report) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(o.action, o.status) for o in report.merges]


def advance_main(projet: Path, path: str, text: str) -> None:
    """Un autre travail fusionné sur main pendant ce temps (poussé sur la remote)."""
    clone = projet.parent / "autre-clone"
    if not clone.exists():
        subprocess_clone(projet, clone)
    git(clone, "pull", "-q", "origin", "main")
    (clone / path).write_text(text, encoding="utf-8")
    git(clone, "add", "--", path)
    git(clone, "commit", "-q", "-m", f"main avance ({path})")
    git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")


def subprocess_clone(projet: Path, clone: Path) -> None:
    import subprocess

    remote = git(projet, "remote", "get-url", "origin")
    subprocess.run(["git", "clone", "-q", remote, str(clone)], check=True)
    git(clone, "config", "user.name", "Autre")
    git(clone, "config", "user.email", "autre@example.invalid")
    git(clone, "config", "commit.gpgsign", "false")


# ── Chemin vert, sous mandat ───────────────────────────────────────────────────


def test_vert_sous_mandat_fusionne_accepte_puis_le_janitor_nettoie(projet: Path, host: FakeHost) -> None:
    mandate = grant(projet)
    tid, wt = launch_and_work(projet, host)
    head = git(wt, "rev-parse", "HEAD")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("accept", "done")], report.merges
    [argv] = host.of("gh", "pr", "merge")
    assert argv == ["gh", "pr", "merge", "7", "--squash", "--match-head-commit", head]
    assert not {"--admin", "--auto"} & set(argv)
    [merged] = events(projet, tid, MERGED_EVENT)
    assert merged.payload["head_sha"] == head and merged.payload["reviewed"] is True and merged.payload["mandate_id"] == mandate.id
    git(projet, "fetch", "-q", "origin", "main")
    assert git(projet, "merge-base", "--is-ancestor", merged.payload["merged_sha"], "refs/remotes/origin/main") == ""
    gestures = MandateStore(projet).gestures_taken(tid)
    assert [g["gesture"] for g in gestures] == ["merge", "accept"]
    assert gestures[0]["origin"] == "orchestrator" and gestures[0]["provenance"]["kind"] == "receipt"
    assert head[:12] in gestures[0]["provenance"]["ref"]
    assert TaskService(projet).require(tid).status is TaskState.CLOSED
    cleanup = tick(projet, host).cleanup  # la tâche close : le janitor du lot 2
    done = {o.action for o in cleanup if o.status == "done"}
    assert {"session", "worktree", "branch_local", "branch_remote"} <= done, cleanup
    assert tick(projet, host).merges == ()  # idempotent : rien à refaire
    assert len(host.of("gh", "pr", "merge")) == 1 and len(events(projet, tid, MERGED_EVENT)) == 1
    assert len(MandateStore(projet).gestures_taken(tid)) == 2


def test_les_checks_requis_par_la_protection_font_seuls_le_verdict(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.checks[branch] = [{"name": "tests", "bucket": "pass"}, {"name": "lint-facultatif", "bucket": "fail"}]
    host.required[branch] = [{"name": "tests", "bucket": "pass"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("accept", "done")]


# ── CI ─────────────────────────────────────────────────────────────────────────


def test_ci_rouge_renvoie_au_worker_comme_un_not_ready(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    launch = launched_sessions(TaskService(projet))[tid]
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "fail"}]
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("bounce", "done")]
    [resume] = host.of("claude", "--bg", "--resume")
    assert resume[3] == launch["session_id"] and "tests" in resume[-1]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.RUNNING and host.of("gh", "pr", "merge") == []
    assert [e.payload["reason"] for e in events(projet, tid, MERGE_BOUNCED_EVENT)] == ["ci_red"]


def test_ci_en_attente_attend_sans_rien_ecrire_de_plus(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("wait", "waiting")]
    before = len(TaskService(projet).ledger.events_for(tid))
    assert merges(tick(projet, host)) == [("wait", "waiting")]
    assert len(TaskService(projet).ledger.events_for(tid)) == before and host.of("gh", "pr", "merge") == []
    host.checks[f"task/{tid}"] = list(GREEN)
    assert merges(tick(projet, host)) == [("accept", "done")]


def test_une_pr_sans_aucun_check_n_est_pas_verte(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks.pop(f"task/{tid}")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("wait", "waiting")] and "aucun check" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


# ── Réalignement ───────────────────────────────────────────────────────────────


def test_branche_en_retard_avec_conflit_de_changelog_union_sans_perdre_main(projet: Path, host: FakeHost) -> None:
    grant(projet)
    mine = CHANGELOG_MAIN.replace("- feat: une entrée de main\n", "- feat: l'entrée de la tâche\n- feat: une entrée de main\n")
    tid, wt = launch_and_work(projet, host, changelog=mine)
    advance_main(projet, "CHANGELOG.md", CHANGELOG_MAIN.replace("- feat: une entrée de main\n",
                                                                "- fix: une autre PR fusionnée\n- feat: une entrée de main\n"))
    before = git(wt, "rev-parse", "HEAD")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("realign", "done")], report.merges
    [realigned] = events(projet, tid, MERGE_REALIGNED_EVENT)
    assert realigned.payload["changelog_union"] is True and realigned.payload["from"] == before
    head = git(wt, "rev-parse", "HEAD")
    assert git(wt, "rev-parse", "HEAD^1") == before  # une fusion, pas un rebase
    assert git(projet, "ls-remote", "origin", f"refs/heads/task/{tid}").split()[0] == head  # poussée, sans forcer
    diff = git(wt, "diff", "refs/remotes/origin/main", "--", "CHANGELOG.md")
    assert not [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    text = (wt / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "l'entrée de la tâche" in text and "une autre PR fusionnée" in text
    assert host.of("gh", "pr", "merge") == []  # la nouvelle CI d'abord
    assert merges(tick(projet, host)) == [("accept", "done")]  # READY tenu par le réalignement, CI verte : fusionnée
    assert events(projet, tid, MERGED_EVENT)[0].payload["head_sha"] == head


def test_une_union_qui_perdrait_une_ligne_de_main_est_refusee(projet: Path, host: FakeHost) -> None:
    grant(projet)
    # Les deux côtés ajoutent en tête (le conflit) ; la tâche retire aussi, plus bas, une ligne que main garde : la
    # fusion à trois voies appliquerait ce retrait hors conflit, et l'union perdrait une ligne de main.
    top = "- feat: une entrée de main\n"
    mine = CHANGELOG_MAIN.replace(top, "- feat: la tâche\n" + top).replace("- init\n", "")
    tid, wt = launch_and_work(projet, host, changelog=mine)
    advance_main(projet, "CHANGELOG.md", CHANGELOG_MAIN.replace(top, "- fix: une autre PR\n" + top))
    before = git(wt, "rev-parse", "HEAD")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("bounce", "done")] and "perdue" in report.merges[0].detail
    assert git(wt, "rev-parse", "HEAD") == before and not (Path(git(wt, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").exists()


def test_un_autre_conflit_renvoie_au_worker(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=("README.md", "la version de la tâche\n"))
    advance_main(projet, "README.md", "la version de main\n")
    before = git(wt, "rev-parse", "HEAD")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("bounce", "done")] and "README.md" in report.merges[0].detail
    assert git(wt, "rev-parse", "HEAD") == before and git(wt, "status", "--porcelain", "--untracked-files=no") == ""
    assert git(projet, "ls-remote", "origin", f"refs/heads/task/{tid}").split()[0] == before  # rien de poussé
    assert [e.payload["reason"] for e in events(projet, tid, MERGE_BOUNCED_EVENT)] == ["conflict"]


def test_file_serie_une_fusion_a_la_fois_et_la_suivante_realignee(projet: Path, host: FakeHost) -> None:
    grant(projet)
    first, wt1 = launch_and_work(projet, host, edit=("a.py", "a\n"))
    second, wt2 = launch_and_work(projet, host, edit=("b.py", "b\n"))
    host.pr_numbers[f"task/{second}"] = 8
    tick(projet, host)  # deux relecteurs
    for tid, wt in ((first, wt1), (second, wt2)):
        give_ready(host, tid, wt, next(a[a.index("--session-id") + 1] for a in host.of("claude", "--bg") if f"grimoire-review-{tid}" in a))
    report = tick(projet, host)
    outcome = {o.task_id: (o.action, o.status) for o in report.merges}
    # La première fusionnée sort de la file : main a avancé, la seconde, devenue tête, est réalignée dans le même tick.
    assert outcome == {first: ("accept", "done"), second: ("realign", "done")}, report.merges
    assert len(host.of("gh", "pr", "merge")) == 1
    assert merges(tick(projet, host)) == [("accept", "done")]
    assert [a[3] for a in host.of("gh", "pr", "merge")] == ["7", "8"]


def test_deux_taches_en_retard_un_seul_realignement_a_la_fois(projet: Path, host: FakeHost) -> None:
    grant(projet)
    first, wt1 = launch_and_work(projet, host, edit=("a.py", "a\n"))
    second, wt2 = launch_and_work(projet, host, edit=("b.py", "b\n"))
    host.pr_numbers[f"task/{second}"] = 8
    advance_main(projet, "c.py", "c\n")  # les deux branches sont en retard
    tick(projet, host)
    for tid, wt in ((first, wt1), (second, wt2)):
        give_ready(host, tid, wt, next(a[a.index("--session-id") + 1] for a in host.of("claude", "--bg") if f"grimoire-review-{tid}" in a))
    report = tick(projet, host)
    assert {o.task_id: (o.action, o.status) for o in report.merges} == {first: ("realign", "done"), second: ("wait", "waiting")}
    assert "1 réalignement(s) à la fois" in next(o.detail for o in report.merges if o.task_id == second)
    assert host.of("gh", "pr", "merge") == []


# ── Sans mandat, ou hors de ses bornes ─────────────────────────────────────────


def test_sans_mandat_la_question_fusionner_puis_le_geste_au_nom_de_l_humain(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and host.of("gh", "pr", "merge") == []
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING and task.waiting is not None and task.waiting.reason == "human"
    question = task.waiting.question
    assert question is not None and question.text.startswith("Fusionner la PR #7") and "aucun mandat" in question.text
    workers = host.of("claude", "--bg", "--name", f"grimoire-{tid}")
    TaskService(projet).answer(tid, "fusionner", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)
    assert TaskService(projet).require(tid).status is TaskState.READY
    report = tick(projet, host)
    assert merges(report) == [("resume", "done")]
    assert host.of("claude", "--bg", "--name", f"grimoire-{tid}") == workers  # la réponse ne relance pas de worker
    assert [(o.status, "merger" in o.detail) for o in report.launches] == [("skipped", True)]
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION
    report = tick(projet, host)  # tout est revérifié, puis le geste de l'humain ; l'acceptation reste à lui
    assert merges(report) == [("escalate", "done")] and len(host.of("gh", "pr", "merge")) == 1
    [merged] = events(projet, tid, MERGED_EVENT)
    assert merged.payload["origin"] == ORIGIN_HUMAN and merged.payload["mandate_id"] == ""
    question = TaskService(projet).require(tid).waiting.question  # type: ignore[union-attr]
    assert question is not None and question.text.startswith(f"Accepter {tid}")
    TaskService(projet).answer(tid, "accepter", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)
    assert merges(tick(projet, host)) == [("resume", "done")]
    assert merges(tick(projet, host)) == [("accept", "done")]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.CLOSED
    verdict = EvidenceService(wt / EVIDENCE).get_latest_verdict(tid)
    assert verdict is not None and verdict.created_by == "human:guilhem"
    assert len(events(projet, tid, MERGE_RESUMED_EVENT)) == 2 and len(host.of("gh", "pr", "merge")) == 1


def test_une_reponse_reprendre_rend_la_tache_au_worker(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    review_ready(projet, host, tid, wt)
    question = TaskService(projet).require(tid).waiting.question  # type: ignore[union-attr]
    TaskService(projet).answer(tid, "reprendre", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)  # type: ignore[union-attr]
    report = tick(projet, host)
    assert report.merges == () and [o.status for o in report.launches] == ["launched"]


def test_le_mandat_qui_ne_couvre_pas_merge_ne_fusionne_pas(projet: Path, host: FakeHost) -> None:
    grant(projet, ("accept",))
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and "ne couvre pas ce geste" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []


def test_la_politique_qui_garde_merge_a_l_humain_l_emporte_sur_le_mandat(projet: Path, host: FakeHost) -> None:
    write_policy(projet, escalation="escalation:\n  merge: human\n  accept: orchestrator\n")
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and "garde merge" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


def test_une_categorie_gardee_n_est_ni_fusionnee_ni_acceptee_sous_mandat(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, labels=("security",))
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")]
    assert "catégorie gardée à l'humain : security" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []


def test_une_categorie_gardee_fusionnee_par_un_humain_n_est_pas_acceptee_sous_mandat(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, labels=("release",))
    review_ready(projet, host, tid, wt)
    question = TaskService(projet).require(tid).waiting.question  # type: ignore[union-attr]
    TaskService(projet).answer(tid, "fusionner", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)  # type: ignore[union-attr]
    tick(projet, host)
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")] and "catégorie gardée à l'humain : release" in report.merges[0].detail
    assert len(host.of("gh", "pr", "merge")) == 1 and [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == []
    assert TaskService(projet).require(tid).status is TaskState.WAITING


def test_une_relecture_non_attestee_ne_fonde_pas_une_fusion_sous_mandat(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)  # relecteur lancé sans mandat : sa session n'est pas inscrite
    grant(projet)
    give_ready(host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")] and "journal d'autorité" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


def test_mandat_qui_couvre_merge_mais_pas_accept(projet: Path, host: FakeHost) -> None:
    mandate = grant(projet, ("merge",))
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and "accept refusé" in report.merges[0].detail
    assert [(g["gesture"], g["mandate_id"]) for g in MandateStore(projet).gestures_taken(tid)] == [("merge", mandate.id)]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING and task.waiting.question.text.startswith(f"Accepter {tid}")  # type: ignore[union-attr]


# ── Constat sur la remote, rejeu, échecs ───────────────────────────────────────


def test_une_fusion_non_constatee_n_est_ni_notee_ni_acceptee(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.merge_lands = False  # gh sort en 0, la PR reste ouverte
    git(projet, "fetch", "-q", "origin", "main")
    host.merge_commits[f"task/{tid}"] = git(projet, "rev-parse", "refs/remotes/origin/main")  # un oid sans état MERGED
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("unconfirmed", "waiting")]
    assert events(projet, tid, MERGED_EVENT) == [] and MandateStore(projet).gestures_taken(tid) == []
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION
    host.merge_lands = True
    host.merge_commits.pop(f"task/{tid}")
    assert merges(tick(projet, host)) == [("accept", "done")]
    assert [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == ["merge", "accept"]


def test_un_commit_de_fusion_absent_de_la_cible_n_est_pas_une_fusion(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("wait", "waiting")]
    branch = f"task/{tid}"
    host.prs[branch], host.merge_commits[branch] = "MERGED", git(wt, "rev-parse", "HEAD")  # jamais arrivé sur main
    report = tick(projet, host)
    assert merges(report) == [("unconfirmed", "waiting")] and events(projet, tid, MERGED_EVENT) == []
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_une_coupure_entre_le_journal_d_autorite_et_le_ledger_n_inscrit_pas_deux_fois(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.missions.ledger import MissionLedger

    grant(projet)
    tid, wt = launch_and_work(projet, host)
    real = MissionLedger.append_event
    cut = {"once": True}

    def flaky(self: MissionLedger, kind: str, *args: object, **kwargs: object) -> object:
        if kind == MERGED_EVENT and cut["once"]:
            cut["once"] = False
            raise OSError("coupure simulée")
        return real(self, kind, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(MissionLedger, "append_event", flaky)
    assert merges(review_ready(projet, host, tid, wt)) == [("error", "failed")]
    assert [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == ["merge"]
    assert merges(tick(projet, host)) == [("accept", "done")]
    assert [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == ["merge", "accept"]
    assert len(host.of("gh", "pr", "merge")) == 1


def test_la_tete_de_file_garde_son_rang_meme_quand_la_suivante_est_prete(projet: Path, host: FakeHost) -> None:
    grant(projet)
    first, wt1 = launch_and_work(projet, host, edit=("a.py", "a\n"))
    second, wt2 = launch_and_work(projet, host, edit=("b.py", "b\n"))
    host.pr_numbers[f"task/{second}"] = 8
    host.checks[f"task/{first}"] = [{"name": "tests", "bucket": "pending"}]
    tick(projet, host)
    for tid, wt in ((first, wt1), (second, wt2)):
        give_ready(host, tid, wt, next(a[a.index("--session-id") + 1] for a in host.of("claude", "--bg") if f"grimoire-review-{tid}" in a))
    report = tick(projet, host)
    assert {o.task_id: (o.action, o.status) for o in report.merges} == {first: ("wait", "waiting"), second: ("wait", "waiting")}
    assert "une fusion à la fois" in next(o.detail for o in report.merges if o.task_id == second)
    assert host.of("gh", "pr", "merge") == []


def test_une_fusion_faite_et_non_notee_est_constatee_au_tick_suivant_jamais_refaite(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.broken_after_merge = True
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("unconfirmed", "failed")] and read_breaker(projet).failures == 1
    assert events(projet, tid, MERGED_EVENT) == []
    host.gh_broken = False
    assert merges(tick(projet, host)) == [("accept", "done")]
    assert len(host.of("gh", "pr", "merge")) == 1 and len(events(projet, tid, MERGED_EVENT)) == 1
    assert [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == ["merge", "accept"]  # inscrite une fois


def test_un_gh_en_panne_est_rapporte_compte_et_rejoue(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)
    give_ready(host, tid, wt)
    host.gh_broken = True
    report = tick(projet, host)
    assert merges(report) == [("wait", "failed")] and any(a.startswith("merger :") for a in report.attention)
    assert read_breaker(projet).failures == 1
    host.gh_broken = False
    assert merges(tick(projet, host)) == [("accept", "done")]


def test_disjoncteur_ouvert_rien_n_est_fusionne(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)
    trip(projet, "autre tâche")
    give_ready(host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("wait", "skipped")] and host.of("gh", "pr", "merge") == []


# ── Le READY doit tenir ────────────────────────────────────────────────────────


def test_un_ready_perime_rend_la_tache_a_la_relecture(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("wait", "waiting")]  # READY jugé, la CI tourne
    (wt / "route.py").write_text("autre chose\n", encoding="utf-8")  # commité et poussé après le jugement READY
    git(wt, "commit", "-q", "-am", "après la relecture")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    report = tick(projet, host)
    assert merges(report) == [("stale", "done")] and host.of("gh", "pr", "merge") == []
    assert len(events(projet, tid, MERGE_REQUEUED_EVENT)) == 1
    reviews = len(events(projet, tid, REVIEW_LAUNCHED_EVENT))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("launch", "launched")]  # une nouvelle relecture
    assert len(events(projet, tid, REVIEW_LAUNCHED_EVENT)) == reviews + 1 and report.merges == ()


def test_un_arbre_modifie_non_commite_renvoie_au_worker(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)
    item = EvidenceItem.from_text("check-01", EvidenceKind.TEST, "ok", uri="check:pytest", summary="ok")
    EvidenceService(wt / EVIDENCE).create_pack(tid, EvidenceProfile.LIGHT, [item], acceptance=CRITERIA)
    (wt / "brouillon.txt").write_text("pas commité\n", encoding="utf-8")
    write_receipt(wt, tid, "READY", reviewer_id="reviewer", reviewer_session=reviewer_sid(host))
    report = tick(projet, host)
    assert merges(report) == [("bounce", "done")] and "brouillon.txt" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


# ── Interrupteur, politique, robustesse ────────────────────────────────────────


def test_merger_desactive_par_defaut_rien_ne_change(projet: Path, host: FakeHost) -> None:
    write_policy(projet, merger="", escalation=DELEGATED)
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert report.merges == () and "merges" not in report.to_dict() and host.of("gh", "pr", "merge") == []
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_une_etape_du_merger_en_erreur_ne_casse_pas_le_tick(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import merger

    def boom(self: object) -> tuple[()]:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(merger.MergeStage, "__call__", boom)
    tid, wt = launch_and_work(projet, host)
    report = review_ready(projet, host, tid, wt)
    assert report.status == "ran" and any("merger : étape en erreur" in a for a in report.attention)
    assert [(o.action, o.status) for o in report.reviews] == [("ready", "done")]


def test_la_politique_du_merger_est_lue_et_validee(projet: Path) -> None:
    write_policy(projet, merger="    merger:\n      enabled: true\n      realign_max: 2\n      ignored_checks: [\"coverage/*\"]\n")
    role = load_orchestrator_policy(projet).policy.merger  # type: ignore[union-attr]
    assert (role.enabled, role.realign_max, role.ignored_checks, role.max_cycles) == (True, 2, ("coverage/*",), 2)
    write_policy(projet, merger="    merger:\n      enabled: true\n      wip: 2\n")
    assert "clé inconnue wip" in load_orchestrator_policy(projet).error
    write_policy(projet, merger="    merger:\n      ignored_checks: codecov\n")
    assert "ignored_checks" in load_orchestrator_policy(projet).error
    write_policy(projet, merger="")
    role = load_orchestrator_policy(projet).policy.merger  # type: ignore[union-attr]
    assert not role.enabled and role.ignored_checks == ("codecov/*",)


def test_un_echec_de_reprise_du_worker_remet_la_tache_en_revue(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "fail"}]
    tick(projet, host)
    host.bg_fails = True  # le relecteur est lancé ; c'est la reprise du worker qui échoue
    give_ready(host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("bounce", "failed")]
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION
    assert events(projet, tid, MERGE_BOUNCED_EVENT) == []


def test_ci_rouge_a_repetition_escalade_a_l_humain(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "fail"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("bounce", "done")]
    ledger = TaskService(projet).ledger
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")  # le worker remet en revue
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING and "échoue à répétition" in task.waiting.question.text  # type: ignore[union-attr]
    assert len(host.of("claude", "--bg", "--resume")) == 1
    assert [e.payload.get("escalated", False) for e in events(projet, tid, MERGE_BOUNCED_EVENT)] == [False, True]
    assert len(events(projet, tid, MERGE_ESCALATED_EVENT)) == 1


def test_une_tete_fusionnee_autre_que_la_relue_n_est_pas_acceptee_sous_mandat(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    assert merges(review_ready(projet, host, tid, wt)) == [("wait", "waiting")]
    # Quelqu'un fusionne la PR sur GitHub à une autre tête que celle que la relecture couvre.
    advance_main(projet, "ailleurs.py", "x\n")
    other = git(projet.parent / "autre-clone", "rev-parse", "HEAD")
    branch = f"task/{tid}"
    host.prs[branch], host.pr_heads[branch], host.merge_commits[branch] = "MERGED", other, other
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")] and "n'est pas celle que la relecture READY couvre" in report.merges[0].detail
    [merged] = events(projet, tid, MERGED_EVENT)
    assert merged.payload["reviewed"] is False and merged.payload["origin"] == "remote"
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []


def test_une_reponse_qui_ne_vient_pas_du_canal_humain_ne_vaut_pas_le_geste(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT

    tid, wt = launch_and_work(projet, host)
    review_ready(projet, host, tid, wt)
    question = TaskService(projet).require(tid).waiting.question  # type: ignore[union-attr]
    ledger = TaskService(projet).ledger
    ledger.append_event(HUMAN_ANSWERED_EVENT, tid, "task", "agent",
                        {"task_id": tid, "question_id": question.id, "answer": "fusionner", "origin": "agent"})  # type: ignore[union-attr]
    ledger.wake(tid, reason="réponse", cause="answer")
    report = tick(projet, host)
    assert report.merges == () and host.of("gh", "pr", "merge") == []
    assert [o.status for o in report.launches] == ["launched"]  # rendue au worker, pas reprise par le merger
