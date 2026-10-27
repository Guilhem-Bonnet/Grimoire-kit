"""#994 : sous mandat, une tâche faite à la main par une session interactive est relue par le relecteur du tick.

Avant : le relecteur ne relisait que les tâches lancées par le lanceur (``task.session_launched``) ; la session qui a
fait la tâche est refusée (``review.self_review``), une autre aussi (non inscrite relecteur au journal d'autorité). Une
tâche faite à la main restait en ``needs_verification`` sans issue tant qu'un mandat était actif.

Rejoué contre le faux ``gh`` et le faux ``claude`` sur un vrai git : la PR rattachée à la tâche (branche ``task/<id>``,
identifiant exact dans le titre, créée après le claim) est relue dans un worktree détaché recréé sur sa tête, scellée
avec sa PR, sa base et son dépôt ; le merger fusionne la PR ouverte ou constate la fusion, puis accepte sous mandat. Un
reçu signé par une session qui a tenu le claim est forgé. Revue adversariale : une vieille PR liée, une PR depuis une
branche d'intégration, un dépôt changé avant la relecture, un worktree pré-posé, une branche au nom piégé sont refusés ;
le merger ne pousse jamais sur une branche qui n'est pas ``task/<id>`` ; une clôture sans pack passe à l'humain.
Sans PR, rien n'est relu (et rien n'est réessayé avant une heure) ; sans mandat, rien ne change.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git
from test_merger import CRITERIA, GREEN, REPO, advance_main, give_ready, grant, isolate_home, merges, tick
from test_merger import make_projet as _make_projet

from grimoire.missions import ci_slots, review_seal
from grimoire.missions.mandate import MandateStore
from grimoire.missions.manual_review import MANUAL_KEY, REVIEW_DEFERRED_EVENT
from grimoire.missions.merger import MERGE_ACCEPTED_EVENT, MERGE_PUSHED_EVENT
from grimoire.missions.review_receipt import write_receipt
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
    # Les réglages, hooks et serveurs MCP qu'apporte la PR ne s'appliquent pas au relecteur.
    assert bg[bg.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in bg
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


def test_un_lien_libre_vers_une_pr_non_rattachee_n_est_qu_un_indice(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host, title="un titre sans identifiant",
                      extra={"pr_url": f"https://github.com/{REPO}/pull/{NUMBER}"})
    report = tick(projet, host)
    assert reviews(report) == [(tid, "no_pr", "skipped")], report.reviews  # ni task/<id>, ni identifiant au titre
    assert host.of("gh", "pr", "view", str(NUMBER)) and host.of("claude", "--bg") == []


def test_une_vieille_pr_fusionnee_liee_a_la_tache_est_refusee(projet: Path, host: FakeHost) -> None:
    """B1 : une PR fusionnée avant le claim, liée par la description, ne clôt pas la tâche sans relecture de son travail."""
    grant(projet)
    tid = manual_task(projet, host, extra={"pr_url": f"https://github.com/{REPO}/pull/{NUMBER}"})
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    host.pr_created[BRANCH] = old
    host.merged_at[BRANCH] = old
    human_merges(host, projet)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "créée après la tâche" in report.reviews[0].detail
    assert host.of("claude", "--bg") == [] and TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION
    assert any(f"relecteur : {tid} pr_refused" in a for a in report.attention)


def test_un_lien_hors_github_n_est_pas_un_indice(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid = manual_task(projet, host, extra={"pr_url": f"https://notgithub.com/{REPO}/pull/9"})
    host.pr_numbers[BRANCH] = 9
    tick(projet, host)
    assert host.of("gh", "pr", "view", "9") == [] and events(projet, tid, REVIEW_LAUNCHED_EVENT)


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
    # Revue une heure plus tard, pas à chaque tick : ni gh, ni attention, rien au journal d'autorité.
    [deferred] = events(projet, tid, REVIEW_DEFERRED_EVENT)
    assert deferred.payload["until"] > datetime.now(UTC).isoformat()
    calls, journal = len(host.calls), len(MandateStore(projet).journal.read())
    again = tick(projet, host)
    assert not any(a[0] == "gh" for a, _c, _e in host.calls[calls:]) and not any("no_pr" in a for a in again.attention)
    assert len(MandateStore(projet).journal.read()) == journal


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


def test_une_pr_depuis_main_vers_une_autre_base_est_refusee_sans_poussee(projet: Path, host: FakeHost) -> None:
    """B2 : la base vient de la politique (ou origin/HEAD), jamais de la PR ; une branche d'intégration n'est pas relue."""
    grant(projet)
    tid = manual_task(projet, host)
    remote = Path(str(host.real))
    git(remote, "branch", "release", "main")
    for table in (host.prs, host.pr_numbers, host.pr_titles, host.checks):
        table["main"] = table.pop(BRANCH)  # type: ignore[attr-defined]
    host.pr_bases["main"] = "release"
    before = git(remote, "rev-parse", "refs/heads/main")
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert host.of("claude", "--bg") == [] and git(remote, "rev-parse", "refs/heads/main") == before


def test_le_merger_ne_realigne_pas_une_branche_qui_n_est_pas_task_id(projet: Path, host: FakeHost) -> None:
    """B2 : une PR manuelle en retard sur la cible est laissée à l'humain, sans aucune poussée sur sa branche."""
    grant(projet)
    tid = manual_task(projet, host)
    advance_main(projet, "docs/autre.md", "autre\n")
    tick(projet, host)
    give_ready(host, tid, review_tree_of(projet, tid))
    tick(projet, host)  # READY scellé, CI verte vue une fois
    head = git(Path(str(host.real)), "rev-parse", f"refs/heads/{BRANCH}")
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")], report.merges
    assert "ne réaligne que les branches task/" in report.merges[0].detail
    assert events(projet, tid, MERGE_PUSHED_EVENT) == [] and git(Path(str(host.real)), "rev-parse", f"refs/heads/{BRANCH}") == head


def test_un_depot_change_avant_la_premiere_relecture_est_refuse(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1 : le dépôt vient du pin posé à l'octroi du mandat, pas de la configuration lue à la relecture."""
    grant(projet)  # fige acme/kit
    tid = manual_task(projet, host)
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: "evil/kit")  # la session a réécrit ci.repository
    host.repo = ""  # le faux gh servirait n'importe quel dépôt
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "ont changé" in report.reviews[0].detail and host.of("claude", "--bg") == []


def test_sans_depot_fige_avant_la_relecture_rien_n_est_relu(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1 : aucun pin (dépôt introuvable à l'octroi, aucune tâche du tick) : échec fermé."""

    def unresolved(_home: Path, _project: Path) -> str:
        raise RuntimeError("pas de remote GitHub")

    monkeypatch.setattr(ci_slots, "ci_repository", unresolved)
    grant(projet)
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: REPO)
    tid = manual_task(projet, host)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "réoctroyer le mandat" in report.reviews[0].detail and host.of("claude", "--bg") == []


def test_un_worktree_de_relecture_pre_pose_n_est_jamais_reutilise(projet: Path, host: FakeHost) -> None:
    """M1 : un dossier au nom prévisible, préparé avec un réglage piégé, n'est pas celui où le relecteur travaille."""
    grant(projet)
    tid = manual_task(projet, host)
    head = git(Path(str(host.real)), "rev-parse", f"refs/heads/{BRANCH}")
    git(projet, "fetch", "-q", "origin", BRANCH)
    planted = Path(git(projet, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "grimoire" / "review" / f"{tid}-{head[:12]}"
    git(projet, "worktree", "add", "-q", "--detach", str(planted), head)
    (planted / ".claude").mkdir()
    (planted / ".claude" / "settings.json").write_text('{"hooks": "piège"}', encoding="utf-8")
    assert reviews(tick(projet, host)) == [(tid, "launch", "launched")]
    tree = review_tree_of(projet, tid)
    # Le dossier pré-posé n'est pas celui du relecteur ; il n'est pas retiré non plus : son nom n'est pas celui d'une
    # relecture du tick (D5, <id>-<tête>-<aléa>), le tick ne touche pas un worktree qu'il n'a pas posé.
    assert tree != planted and not (tree / ".claude" / "settings.json").exists()


def test_un_nom_de_branche_piege_est_refuse(projet: Path, host: FakeHost) -> None:
    """M3 : un nom de branche hors [A-Za-z0-9._/-] n'entre ni dans une commande ni dans la consigne."""
    grant(projet)
    tid = manual_task(projet, host, branch="feat/p74$(id)")
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "nom de branche" in report.reviews[0].detail and host.of("claude", "--bg") == []


def test_une_cloture_sans_pack_de_preuve_passe_a_l_humain(projet: Path, host: FakeHost) -> None:
    """M4 : PR fusionnée, READY scellé, mais aucun pack dans le worktree de relecture : waiting(human), avec la raison."""
    grant(projet)
    tid = manual_task(projet, host)
    human_merges(host, projet)
    tick(projet, host)
    write_receipt(review_tree_of(projet, tid), tid, "READY", reviewer_id="reviewer",
                  reviewer_session=host.session_of("grimoire-review-"))  # un READY sans pack
    report = tick(projet, host)
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING, (report.reviews, report.merges)
    assert "aucun pack de preuve" in report.merges[0].detail


def test_un_indice_errone_n_empeche_pas_la_recherche(projet: Path, host: FakeHost) -> None:
    """D1 : un lien de description vers une PR inexistante est ignoré ; la PR rattachée est trouvée quand même."""
    grant(projet)
    tid = manual_task(projet, host, extra={"pr_url": f"https://github.com/{REPO}/pull/999"})
    report = tick(projet, host)
    assert reviews(report) == [(tid, "launch", "launched")], report.reviews
    assert host.of("gh", "pr", "view", "999") and events(projet, tid, REVIEW_LAUNCHED_EVENT)[-1].payload["pr"] == NUMBER


def test_une_pr_de_fork_au_meme_titre_ne_bloque_pas_la_relecture(projet: Path, host: FakeHost) -> None:
    """D2 : une PR de fork qui copie le titre n'est jamais candidate : elle ne rend pas le choix ambigu."""
    grant(projet)
    tid = manual_task(projet, host)
    fork = "feat/fork-copie"
    host.prs[fork], host.pr_numbers[fork], host.pr_titles[fork] = "OPEN", 77, host.pr_titles[BRANCH]
    host.pr_heads[fork] = git(Path(str(host.real)), "rev-parse", "refs/heads/main")
    host.cross_repo.add(fork)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "launch", "launched")], report.reviews
    assert events(projet, tid, REVIEW_LAUNCHED_EVENT)[-1].payload["pr"] == NUMBER


def test_la_consigne_d_une_pr_deja_fusionnee_donne_le_diff_de_la_fusion(projet: Path, host: FakeHost) -> None:
    """D3 : la PR fusionnée, `origin/<cible>...HEAD` est vide ; la consigne donne `gh pr diff` et le diff de la fusion."""
    grant(projet)
    tid = manual_task(projet, host)
    human_merges(host, projet)
    tick(projet, host)
    merge = host.merge_commits[BRANCH]
    [bg] = [a for a in host.of("claude", "--bg") if f"grimoire-review-{tid}" in a]
    assert f"gh pr diff {NUMBER}" in bg[-1] and f"git diff {merge}^1 {merge}" in bg[-1]


def test_un_pin_vide_du_mandat_ne_retombe_pas_sur_le_depot_d_une_tache_du_tick(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4 : le mandat actif n'a qu'un pin vide ; un dépôt figé pour une tâche du tick ne le remplace pas."""

    def unresolved(_home: Path, _project: Path) -> str:
        raise RuntimeError("pas de remote GitHub")

    monkeypatch.setattr(ci_slots, "ci_repository", unresolved)
    grant(projet)
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: REPO)
    review_seal.freeze_repository(projet, "GAO-tick-001")  # le gel d'une tâche du tick, plus tard
    tid = manual_task(projet, host)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "réoctroyer le mandat" in report.reviews[0].detail and host.of("claude", "--bg") == []


def test_un_pin_sans_url_de_origin_est_refuse(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    """D6 : un pin dont l'URL de origin est vide ne vaut pas pin."""
    real = review_seal._current_repository
    monkeypatch.setattr(review_seal, "_current_repository", lambda _root: {"repo": REPO, "origin_url": ""})
    grant(projet)
    monkeypatch.setattr(review_seal, "_current_repository", real)
    tid = manual_task(projet, host)
    report = tick(projet, host)
    assert reviews(report) == [(tid, "pr_refused", "skipped")], report.reviews
    assert "réoctroyer le mandat" in report.reviews[0].detail and host.of("claude", "--bg") == []


def test_le_retrait_des_worktrees_de_relecture_ne_touche_que_ceux_de_la_tache(projet: Path) -> None:
    """D5 : retirer les relectures de GAO-1 ne retire pas celles de GAO-1-bis."""
    from grimoire.missions.manual_review import _retire_trees

    parent = Path(git(projet, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "grimoire" / "review"
    head = git(projet, "rev-parse", "HEAD")
    mine, other = parent / f"GAO-1-{head[:12]}-{'a' * 12}", parent / f"GAO-1-bis-{head[:12]}-{'b' * 12}"
    for place in (mine, other):
        git(projet, "worktree", "add", "-q", "--detach", str(place), head)
    _retire_trees(projet, parent, "GAO-1")
    assert not mine.exists() and other.is_dir()
