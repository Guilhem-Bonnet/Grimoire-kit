"""#994 : sous mandat, une tâche faite à la main par une session interactive est relue par le relecteur du tick.

Avant : le relecteur ne relisait que les tâches lancées par le lanceur (``task.session_launched``) ; la session qui a
fait la tâche est refusée (``review.self_review``), une autre aussi (non inscrite relecteur au journal d'autorité). Une
tâche faite à la main restait en ``needs_verification`` sans issue tant qu'un mandat était actif.

Rejoué contre le faux ``gh`` et le faux ``claude`` sur un vrai git : la PR de la tâche est trouvée (lien, recherche par
identifiant, branche ``task/<id>``), relue dans un worktree détaché sur sa tête, scellée ; le merger fusionne la PR
ouverte ou constate la fusion, puis accepte sous mandat. Un reçu signé par une session qui a tenu le claim est forgé.
Sans PR, rien n'est relu ; sans mandat, rien ne change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git
from test_merger import CRITERIA, GREEN, REPO, give_ready, grant, isolate_home, merges, tick
from test_merger import make_projet as _make_projet

from grimoire.missions.mandate import MandateStore
from grimoire.missions.manual_review import MANUAL_KEY
from grimoire.missions.merger import MERGE_ACCEPTED_EVENT
from grimoire.missions.review_seal import SEAL_ENTRY
from grimoire.missions.reviewer import REVIEW_FORGED_EVENT, REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT

#: La session interactive qui a fait la tâche (celle du cockpit qui fait les écrans P*).
HUMAN_SESSION = "41c89d23-db0e-4d3b-856f-3ee961a7188f"
EARLIER_SESSION = "0a1b2c3d-0000-4000-8000-000000000001"
BRANCH = "feat/cockpit-p74"
NUMBER = 42


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return isolate_home(tmp_path, monkeypatch)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return _make_projet(tmp_path)


@pytest.fixture
def host() -> FakeHost:
    fake = FakeHost()
    fake.repo = REPO
    return fake


def events(projet: Path, tid: str, kind: str) -> list:  # type: ignore[type-arg]
    return [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == kind]


def reviews(report) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(o.task_id, o.action, o.status) for o in report.reviews]  # type: ignore[misc]


def manual_task(projet: Path, host: FakeHost, *, branch: str = BRANCH, title: str = "", extra: dict | None = None,  # type: ignore[type-arg]
                earlier_claim: bool = False) -> str:
    """Une tâche réclamée par une session interactive, faite à la main sur *branch*, PR ouverte, remise en revue."""
    service = TaskService(projet)
    ledger = service.ledger
    mission = next(iter(ledger.list_missions()), None) or ledger.create_mission(title="#994", origin="test")
    task = ledger.create_task(mission.id, "P74 — cartes kanban", acceptance=CRITERIA, owner="amelia", extra=extra)
    tid = task.id
    ledger.transition_task(tid, TaskState.READY)
    if earlier_claim:  # une première session a tenu le claim, puis l'a rendu
        ledger.claim_task(tid, "cli", "local")
        ledger.attach_session(tid, EARLIER_SESSION, session_host="claude-code-cli", actor_id="cli")
        ledger.transition_task(tid, TaskState.READY, actor_id="cli", reason="rendue")
    ledger.claim_task(tid, "cli", "local")
    ledger.attach_session(tid, HUMAN_SESSION, session_host="claude-code-cli", actor_id="cli")
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="cli")
    remote = Path(git(projet, "remote", "get-url", "origin"))
    host.real = remote
    clone = projet.parent / f"session-{tid}"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(clone)], check=True)
    git(clone, "config", "user.name", "Session")
    git(clone, "config", "user.email", "session@example.invalid")
    git(clone, "config", "commit.gpgsign", "false")
    git(clone, "checkout", "-q", "-b", branch)
    (clone / "src" / "app").mkdir(parents=True, exist_ok=True)
    (clone / "src" / "app" / "cartes.py").write_text("CARTES = 74\n", encoding="utf-8")
    git(clone, "add", "--", "src/app/cartes.py")
    git(clone, "commit", "-q", "-m", f"cartes ({tid})")
    git(clone, "push", "-q", "origin", branch)
    host.prs[branch] = "OPEN"
    host.pr_numbers[branch] = NUMBER
    host.pr_titles[branch] = title or f"P74 — cartes kanban lisibles ({tid})"
    host.checks[branch] = list(GREEN)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="cli")
    return tid


def review_tree_of(projet: Path, tid: str) -> Path:
    return Path(str(events(projet, tid, REVIEW_LAUNCHED_EVENT)[-1].payload["cwd"]))


def human_merges(host: FakeHost, projet: Path, branch: str = BRANCH) -> None:
    """L'humain fusionne la PR lui-même (avant toute relecture) : GitHub l'a fusionnée."""
    head = git(Path(str(host.real)), "rev-parse", f"refs/heads/{branch}")
    assert host(["gh", "pr", "merge", str(NUMBER), "--squash", "--match-head-commit", head], projet).returncode == 0
    host.calls.pop()


def test_une_tache_manuelle_dont_la_pr_est_fusionnee_est_relue_puis_close_sous_mandat(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host)
    human_merges(host, projet)
    first = tick(projet, host)
    assert reviews(first) == [(tid, "launch", "launched")], first.reviews
    [launched] = events(projet, tid, REVIEW_LAUNCHED_EVENT)
    assert launched.payload[MANUAL_KEY] and launched.payload["pr"] == NUMBER and launched.payload["branch"] == BRANCH
    tree = review_tree_of(projet, tid)
    head = git(Path(str(host.real)), "rev-parse", f"refs/heads/{BRANCH}")
    assert git(tree, "rev-parse", "HEAD") == head and launched.payload["head"] == head  # détaché sur la tête de la PR
    reviewer = host.session_of("grimoire-review-")
    assert reviewer != HUMAN_SESSION
    # La vraie identité du relecteur est inscrite au journal d'autorité, en rôle reviewer pour cette tâche.
    assert [s["session_id"] for s in MandateStore(projet).sessions(role="reviewer", task_id=tid)] == [reviewer]
    [bg] = [a for a in host.of("claude", "--bg") if f"grimoire-review-{tid}" in a]
    assert f"gh pr diff {NUMBER}" in bg[-1] and f"gh pr view {NUMBER}" in bg[-1] and "origin/main...HEAD" in bg[-1]
    give_ready(host, tid, tree)
    report = tick(projet, host)
    [judged] = events(projet, tid, REVIEW_JUDGED_EVENT)
    assert judged.payload["verdict"] == "READY" and judged.payload["sealed"] and judged.payload["seal"]["head"] == head
    assert TaskService(projet).require(tid).status is TaskState.CLOSED, (report.reviews, report.merges)
    assert merges(report) == [("accept", "done")]
    assert len(events(projet, tid, MERGED_EVENT)) == 1 and len(events(projet, tid, MERGE_ACCEPTED_EVENT)) == 1
    assert [g["gesture"] for g in MandateStore(projet).gestures_taken(tid)] == ["accept"]  # sous mandat, par le tick
    assert host.of("gh", "pr", "merge") == []  # la fusion était humaine : le tick l'a constatée


def test_une_tache_manuelle_a_pr_ouverte_est_relue_puis_fusionnee_par_le_merger(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host)
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    give_ready(host, tid, review_tree_of(projet, tid))
    first = tick(projet, host)  # READY scellé ; la CI verte vue une première fois
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail, first.merges
    report = tick(projet, host)
    assert merges(report) == [("accept", "done")], report.merges
    assert host.of("gh", "pr", "merge", str(NUMBER))  # le merger l'a fusionnée, par son numéro
    assert TaskService(projet).require(tid).status is TaskState.CLOSED
    assert sorted(g["gesture"] for g in MandateStore(projet).gestures_taken(tid)) == ["accept", "merge"]


def test_la_pr_est_trouvee_par_le_lien_de_la_tache(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host, title="un titre sans identifiant",
                      extra={"pr_url": f"https://github.com/{REPO}/pull/{NUMBER}"})
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    assert events(projet, tid, REVIEW_LAUNCHED_EVENT)[-1].payload["pr"] == NUMBER
    assert not host.of("gh", "pr", "list")  # le lien suffit


def test_la_pr_est_trouvee_par_la_branche_task_id(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host, branch="task/placeholder", title="sans identifiant")
    # La branche de la tâche porte son identifiant : la session a suivi la convention task/<id>.
    host.prs[f"task/{tid}"] = host.prs.pop("task/placeholder")
    host.pr_numbers[f"task/{tid}"] = host.pr_numbers.pop("task/placeholder")
    host.pr_heads[f"task/{tid}"] = git(Path(str(host.real)), "rev-parse", "refs/heads/task/placeholder")
    git(Path(str(host.real)), "branch", f"task/{tid}", "task/placeholder")
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    assert events(projet, tid, REVIEW_LAUNCHED_EVENT)[-1].payload["branch"] == f"task/{tid}"


def test_un_recu_signe_par_une_session_qui_a_tenu_le_claim_est_forge(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.breaker import read_breaker

    grant(projet)
    tid = manual_task(projet, host, earlier_claim=True)
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    # Une session qui a tenu le claim avant la session courante écrit le reçu à la place du relecteur lancé.
    give_ready(host, tid, review_tree_of(projet, tid), session=EARLIER_SESSION)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "forged", "done")], report.reviews
    assert len(events(projet, tid, REVIEW_FORGED_EVENT)) == 1
    assert read_breaker(projet).open and not MandateStore(projet).state().active  # disjoncteur ouvert, mandat révoqué
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_sans_pr_trouvable_rien_n_est_relu_et_le_tick_le_signale(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host, title="sans identifiant")
    host.prs.clear()  # aucune PR : ni lien, ni recherche, ni task/<id>
    report = tick(projet, host)
    assert reviews(report) == [(tid, "no_pr", "skipped")], report.reviews
    assert "pas de PR à relire" in report.reviews[0].detail
    assert any(f"relecteur : {tid} no_pr" in a for a in report.attention), report.attention
    assert host.of("claude", "--bg") == [] and events(projet, tid, REVIEW_LAUNCHED_EVENT) == []


def test_sans_mandat_une_tache_manuelle_n_est_pas_relue_par_le_tick(projet: Path, host: FakeHost) -> None:
    tid = manual_task(projet, host)
    report = tick(projet, host)
    assert report.reviews == () and report.merges == ()
    assert host.of("claude", "--bg") == [] and host.of("gh", "pr", "list") == []
    assert events(projet, tid, REVIEW_LAUNCHED_EVENT) == []
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_le_sceau_porte_la_tete_relue_de_la_pr(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host)
    tick(projet, host)
    give_ready(host, tid, review_tree_of(projet, tid))
    tick(projet, host)
    seals = [e for e in MandateStore(projet).journal.read() if e.get("type") == SEAL_ENTRY and e.get("task_id") == tid]
    head = git(Path(str(host.real)), "rev-parse", f"refs/heads/{BRANCH}")
    assert [s["head"] for s in seals] == [head]


def test_mandat_retire_pendant_la_relecture_le_recu_est_refuse_et_rien_n_est_relance(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host)
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    MandateStore(projet).revoke("test", "révoqué entre le lancement et le jugement")
    give_ready(host, tid, review_tree_of(projet, tid))
    report = tick(projet, host)
    assert reviews(report) == [(tid, "refused", "done")] and "mandat inactif" in report.reviews[0].detail
    assert host.of("claude", "stop")  # le relecteur est arrêté
    assert tick(projet, host).reviews == () and len(host.of("claude", "--bg")) == 1  # jamais relancé sans mandat
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION
