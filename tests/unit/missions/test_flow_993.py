"""#993 : la boucle worker → relecteur → merger, telle qu'un tick réel l'a montrée bloquée (kit 3.68.0, 2026-10-26).

Quatre défauts, chacun rejoué contre le faux ``gh`` et le faux ``claude`` sur un vrai git :

1. les PR des workers restaient en brouillon et le merger attendait sans fin ;
2. le relecteur rendait READY sur une CI rouge ;
3. le merger attendait sans fin quand GitHub gardait un ``baseRefOid`` périmé, branche en conflit avec la cible ;
4. la consigne du relecteur diffait depuis la base de création du worktree : après un réalignement, tout ``main``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git
from test_merger import (
    REPO,
    advance_main,
    give_ready,
    grant,
    isolate_home,
    launch_and_work,
    merges,
    ready_judged,
    review_ready,
    tick,
)
from test_merger import make_projet as _make_projet

from grimoire.missions.launcher import launched_sessions
from grimoire.missions.mandate import MandateStore
from grimoire.missions.merger import (
    MAX_DESYNC_WAITS,
    MERGE_BOUNCED_EVENT,
    MERGE_REALIGNED_EVENT,
    MERGE_UNDRAFTED_EVENT,
    MERGE_WAITED_EVENT,
)
from grimoire.missions.review_seal import SEAL_ENTRY
from grimoire.missions.reviewer import REVIEW_JUDGED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT, find_task_worktree


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


def prompt_of(host: FakeHost, name: str) -> str:
    """La consigne passée à la dernière session de fond lancée sous *name*."""
    return next(a for a, _c, _e in reversed(host.calls) if a[:2] == ["claude", "--bg"] and name in a)[-1]


def remote_main(projet: Path) -> str:
    return git(projet, "ls-remote", "origin", "refs/heads/main").split()[0]


# ── 1. Brouillon ───────────────────────────────────────────────────────────────


def test_la_consigne_du_worker_demande_une_pr_qui_n_est_pas_un_brouillon(projet: Path, host: FakeHost) -> None:
    tid, _wt = launch_and_work(projet, host)
    prompt = prompt_of(host, f"grimoire-{tid}")
    assert "jamais en brouillon" in prompt and "sans `--draft`" in prompt and "`gh pr ready`" in prompt


def test_une_pr_en_brouillon_relue_ready_sort_du_brouillon_puis_fusionne(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.drafts.add(branch)
    head = git(wt, "rev-parse", "HEAD")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("undraft", "done")], report.merges
    assert host.of("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "-R", REPO]]
    assert branch not in host.drafts
    [undrafted] = events(projet, tid, MERGE_UNDRAFTED_EVENT)
    assert undrafted.payload["pr"] == 7 and undrafted.payload["head"] == head
    first = tick(projet, host)  # la CI de la PR prête se confirme comme toute autre
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail
    assert merges(tick(projet, host)) == [("accept", "done")]
    assert len(host.of("gh", "pr", "ready")) == 1 and len(events(projet, tid, MERGED_EVENT)) == 1


def test_un_brouillon_dont_le_ready_ne_tient_plus_reste_en_brouillon(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    ready_judged(projet, host, tid, wt)
    host.drafts.add(f"task/{tid}")  # la PR repasse en brouillon, et le worker pousse après le READY
    (wt / "src/app/route.py").write_text("404 et autre chose\n", encoding="utf-8")
    git(wt, "commit", "-qam", "après le READY")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    calls = len(host.of("gh", "pr", "ready"))
    report = tick(projet, host)
    assert merges(report) == [("stale", "done")], report.merges  # relu de nouveau, jamais sorti du brouillon
    assert len(host.of("gh", "pr", "ready")) == calls


def test_une_pr_remise_en_brouillon_par_un_humain_n_en_ressort_pas(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.drafts.add(f"task/{tid}")
    assert merges(review_ready(projet, host, tid, wt)) == [("undraft", "done")]
    host.drafts.add(f"task/{tid}")  # un humain la retient, même tête
    for _ in range(2):
        report = tick(projet, host)
        assert merges(report) == [("wait", "waiting")] and "remise en brouillon par un humain" in report.merges[0].detail
    assert len(host.of("gh", "pr", "ready")) == 1 and host.of("gh", "pr", "merge") == []


def test_une_pr_en_brouillon_qui_touche_un_chemin_protege_n_en_sort_pas(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=("docs/x.md", "x\n"))
    (wt / ".github" / "workflows").mkdir(parents=True)
    (wt / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    git(wt, "add", "--", ".github/workflows/ci.yml")
    git(wt, "commit", "-q", "-m", "la CI désactivée")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    host.drafts.add(f"task/{tid}")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")], report.merges
    assert ".github/workflows/ci.yml" in report.merges[0].detail and "gh pr ready 7" in report.merges[0].detail
    assert host.of("gh", "pr", "ready") == [] and f"task/{tid}" in host.drafts
    assert events(projet, tid, MERGE_UNDRAFTED_EVENT) == []


def test_un_gh_pr_ready_refuse_est_compte_et_rejoue(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.drafts.add(f"task/{tid}")
    host.ready_fails = True
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("undraft", "failed")] and "gh pr ready" in report.merges[0].detail
    assert events(projet, tid, MERGE_UNDRAFTED_EVENT) == []
    host.ready_fails = False
    assert merges(tick(projet, host)) == [("undraft", "done")]


# ── 2. READY sur une CI rouge ──────────────────────────────────────────────────


def test_un_ready_sur_une_ci_rouge_devient_not_ready_avec_les_checks_en_constat(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pass"}, {"name": "Ruff lint", "bucket": "fail"}]
    launch = launched_sessions(TaskService(projet))[tid]
    report = review_ready(projet, host, tid, wt)
    assert [(o.action, o.status) for o in report.reviews] == [("bounce", "done")], report.reviews
    assert merges(report) == [] and host.of("gh", "pr", "merge") == []
    [judged] = events(projet, tid, REVIEW_JUDGED_EVENT)
    assert judged.payload["verdict"] == "NOT-READY" and judged.payload["ci"] == "red"
    assert judged.payload["receipt_verdict"] == "READY"
    [resume] = host.of("claude", "--bg", "--resume")
    assert resume[3] == launch["session_id"] and "Ruff lint" in resume[-1] and "CI rouge" in resume[-1]
    assert TaskService(projet).require(tid).status is TaskState.RUNNING
    assert [e for e in MandateStore(projet).journal.read() if e.get("type") == SEAL_ENTRY] == []  # rien de scellé


def test_un_check_requis_rouge_defait_le_ready_meme_si_les_autres_sont_verts(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.checks[branch] = [{"name": "tests", "bucket": "pass"}]
    host.required[branch] = [{"name": "e2e", "bucket": "fail"}]
    report = review_ready(projet, host, tid, wt)
    assert [o.action for o in report.reviews] == ["bounce"] and "e2e" in host.of("claude", "--bg", "--resume")[0][-1]


def test_un_check_annule_ne_defait_pas_le_ready_il_reste_au_merger(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pass"}, {"name": "e2e", "bucket": "cancel"}]
    report = review_ready(projet, host, tid, wt)
    assert [o.action for o in report.reviews] == ["ready"], report.reviews
    assert events(projet, tid, REVIEW_JUDGED_EVENT)[-1].payload["ci"] == "pending"
    assert merges(report) == [("bounce", "done")]  # le merger, lui, tient un check annulé pour rouge
    assert [e.payload["reason"] for e in events(projet, tid, MERGE_BOUNCED_EVENT)] == ["ci_red"]


def test_un_check_ignore_ou_non_requis_en_echec_ne_defait_pas_le_ready(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)  # GREEN : tests pass, codecov/patch fail (ignoré)
    report = review_ready(projet, host, tid, wt)
    assert [o.action for o in report.reviews] == ["ready"], report.reviews
    [judged] = events(projet, tid, REVIEW_JUDGED_EVENT)
    assert judged.payload["verdict"] == "READY" and judged.payload["ci"] == "green"
    other, wt2 = launch_and_work(projet, host, edit=("src/app/b.py", "b\n"))
    host.pr_numbers[f"task/{other}"] = 8
    host.checks[f"task/{other}"] = [{"name": "tests", "bucket": "pass"}, {"name": "lint-facultatif", "bucket": "fail"}]
    host.required[f"task/{other}"] = [{"name": "tests", "bucket": "pass"}]
    tick(projet, host)
    give_ready(host, other, wt2, host.session_of(f"grimoire-review-{other}"))
    assert [o.action for o in tick(projet, host).reviews] == ["ready"]


def test_une_ci_en_cours_ou_sans_pr_ne_defait_pas_le_ready(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    assert [o.action for o in review_ready(projet, host, tid, wt).reviews] == ["ready"]  # le merger attend la CI verte
    other, wt2 = launch_and_work(projet, host, edit=("src/app/b.py", "b\n"))
    host.prs.pop(f"task/{other}")  # aucune PR : rien n'est conclu au relecteur
    tick(projet, host)
    give_ready(host, other, wt2, host.session_of(f"grimoire-review-{other}"))
    assert [o.action for o in tick(projet, host).reviews] == ["ready"]


# ── 3. Merger en attente sans fin ──────────────────────────────────────────────


def test_une_base_github_perimee_n_attend_pas_elle_realigne_puis_fusionne(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.pr_base_oids[branch] = remote_main(projet)  # le baseRefOid de la création de la PR, jamais rafraîchi
    advance_main(projet, "autre.py", "autre\n")
    first = review_ready(projet, host, tid, wt)
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail
    report = tick(projet, host)
    assert merges(report) == [("realign", "done")], report.merges
    [realigned] = events(projet, tid, MERGE_REALIGNED_EVENT)
    assert realigned.payload["into_sha"] == remote_main(projet) != host.pr_base_oids[branch]
    assert events(projet, tid, MERGE_WAITED_EVENT) == []
    assert merges(tick(projet, host)) == [("wait", "waiting")]  # la CI de la tête réalignée se confirme
    assert merges(tick(projet, host)) == [("accept", "done")]


def test_une_pr_en_conflit_sur_une_base_perimee_est_renvoyee_au_worker(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.pr_base_oids[branch] = remote_main(projet)
    host.mergeable[branch], host.merge_states[branch] = "CONFLICTING", "DIRTY"
    advance_main(projet, "src/app/route.py", "500\n")  # le même fichier, autrement : un vrai conflit
    review_ready(projet, host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("bounce", "done")], report.merges
    [bounced] = events(projet, tid, MERGE_BOUNCED_EVENT)
    assert bounced.payload["reason"] == "conflict" and "src/app/route.py" in bounced.payload["detail"]
    [resume] = host.of("claude", "--bg", "--resume")
    assert "`git merge origin/main`" in resume[-1] and "`git fetch origin main`" in resume[-1]
    assert TaskService(projet).require(tid).status is TaskState.RUNNING


def test_un_desaccord_persistant_est_borne_puis_renvoye_au_worker(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.pr_base_oids[f"task/{tid}"] = "f" * 40  # une base que la remote ne connaît pas : aucune filiation
    review_ready(projet, host, tid, wt)  # CI verte vue une fois : à confirmer
    for n in range(1, MAX_DESYNC_WAITS + 1):
        report = tick(projet, host)
        assert merges(report) == [("wait", "waiting")], report.merges
        assert f"({n}/{MAX_DESYNC_WAITS})" in report.merges[0].detail
    report = tick(projet, host)
    assert merges(report) == [("bounce", "done")], report.merges
    [bounced] = events(projet, tid, MERGE_BOUNCED_EVENT)
    assert bounced.payload["reason"] == "desync" and "git merge origin/main" in host.of("claude", "--bg", "--resume")[0][-1]


def test_un_desaccord_sur_la_tete_demande_de_pousser_la_branche_pas_de_fusionner_la_cible(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.pr_heads[branch] = git(wt, "rev-parse", "HEAD")  # GitHub reste sur la tête relue…
    clone = projet.parent / "pousse-tete"
    subprocess.run(["git", "clone", "-q", "-b", branch, git(projet, "remote", "get-url", "origin"), str(clone)], check=True)
    git(clone, "-c", "user.name=A", "-c", "user.email=a@example.invalid", "commit", "-q", "--allow-empty", "-m", "ailleurs")
    git(clone, "push", "-q", "origin", branch)  # … la remote est ailleurs
    review_ready(projet, host, tid, wt)  # CI verte vue une fois : à confirmer
    for n in range(1, MAX_DESYNC_WAITS + 1):
        report = tick(projet, host)
        assert merges(report) == [("wait", "waiting")] and "tête de la remote" in report.merges[0].detail, report.merges
        assert f"({n}/{MAX_DESYNC_WAITS})" in report.merges[0].detail
    assert merges(tick(projet, host)) == [("bounce", "done")]
    [resume] = host.of("claude", "--bg", "--resume")
    assert f"git push origin task/{tid}" in resume[-1] and "git merge origin/main" not in resume[-1]


def test_un_desaccord_en_conflit_annonce_est_renvoye_sans_attendre(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.pr_base_oids[f"task/{tid}"] = "f" * 40
    host.mergeable[f"task/{tid}"] = "CONFLICTING"
    advance_main(projet, "src/app/route.py", "500\n")  # un vrai conflit, hors CHANGELOG
    review_ready(projet, host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("bounce", "done")] and "CONFLICTING" in report.merges[0].detail
    assert "src/app/route.py" in report.merges[0].detail and events(projet, tid, MERGE_WAITED_EVENT) == []


def test_un_desaccord_en_conflit_du_seul_changelog_attend_l_union_du_realignement(projet: Path, host: FakeHost) -> None:
    grant(projet)
    text = (projet / "CHANGELOG.md").read_text(encoding="utf-8")
    tid, wt = launch_and_work(projet, host, changelog=text.replace("## [Unreleased]\n", "## [Unreleased]\n\n- fix: route\n"))
    branch = f"task/{tid}"
    host.pr_base_oids[branch] = "f" * 40
    host.mergeable[branch] = "CONFLICTING"
    advance_main(projet, "CHANGELOG.md", text.replace("## [Unreleased]\n", "## [Unreleased]\n\n- feat: autre\n"))
    review_ready(projet, host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("wait", "waiting")] and f"(1/{MAX_DESYNC_WAITS})" in report.merges[0].detail
    host.pr_base_oids.pop(branch)  # GitHub et la remote s'accordent : le réalignement unit le CHANGELOG
    assert merges(tick(projet, host)) == [("realign", "done")]
    assert events(projet, tid, MERGE_REALIGNED_EVENT)[0].payload["changelog_union"] is True


def test_le_compteur_d_attentes_repart_de_zero_quand_le_desaccord_cesse(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)  # sans mandat : la fusion va à l'humain, la tâche reste en file
    branch = f"task/{tid}"
    host.pr_base_oids[branch] = "f" * 40
    review_ready(projet, host, tid, wt)
    for _ in range(MAX_DESYNC_WAITS - 1):
        tick(projet, host)
    host.pr_base_oids.pop(branch)
    assert merges(tick(projet, host)) == [("escalate", "done")]  # « fusionner ? » posée à l'humain
    assert events(projet, tid, MERGE_WAITED_EVENT)[-1].payload["cleared"] is True
    host.pr_base_oids[branch] = "f" * 40
    report = tick(projet, host)
    assert merges(report) == [("wait", "waiting")] and f"(1/{MAX_DESYNC_WAITS})" in report.merges[0].detail


def test_un_tick_sans_desaccord_remet_le_compte_a_zero_meme_sur_une_ci_en_attente(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.pr_base_oids[branch] = "f" * 40
    review_ready(projet, host, tid, wt)
    assert f"(1/{MAX_DESYNC_WAITS})" in tick(projet, host).merges[0].detail
    host.pr_base_oids.pop(branch)
    host.checks[branch] = [{"name": "tests", "bucket": "pending"}]
    report = tick(projet, host)
    assert merges(report) == [("wait", "waiting")] and report.merges[0].detail.startswith("CI")
    assert events(projet, tid, MERGE_WAITED_EVENT)[-1].payload["cleared"] is True
    host.pr_base_oids[branch] = "f" * 40
    host.checks[branch] = [{"name": "tests", "bucket": "pass"}]
    assert "à confirmer" in tick(projet, host).merges[0].detail  # compte fermé : la CI d'abord, comme avant
    assert f"(1/{MAX_DESYNC_WAITS})" in tick(projet, host).merges[0].detail  # le compte repart de un


def test_au_dela_de_realign_max_une_pr_en_conflit_est_renvoyee_sans_attendre_son_tour(projet: Path, host: FakeHost) -> None:
    grant(projet)
    first, wt1 = launch_and_work(projet, host, edit=("src/app/a.py", "a\n"))
    second, wt2 = launch_and_work(projet, host, edit=("src/app/b.py", "b\n"))
    host.pr_numbers[f"task/{second}"] = 8
    host.mergeable[f"task/{second}"] = "CONFLICTING"
    advance_main(projet, "src/app/b.py", "autre b\n")  # les deux en retard, la seconde en conflit
    tick(projet, host)
    for tid, wt in ((first, wt1), (second, wt2)):
        give_ready(host, tid, wt, host.session_of(f"grimoire-review-{tid}"))
    tick(projet, host)
    report = tick(projet, host)
    outcome = {o.task_id: (o.action, o.status) for o in report.merges}
    assert outcome == {first: ("realign", "done"), second: ("bounce", "done")}, report.merges
    assert "src/app/b.py" in events(projet, second, MERGE_BOUNCED_EVENT)[0].payload["detail"]


def test_au_dela_de_realign_max_un_conflit_du_seul_changelog_attend_son_tour(projet: Path, host: FakeHost) -> None:
    grant(projet)
    first, wt1 = launch_and_work(projet, host, edit=("src/app/a.py", "a\n"))
    text = (wt1 / "CHANGELOG.md").read_text(encoding="utf-8")
    second, wt2 = launch_and_work(projet, host, edit=("src/app/b.py", "b\n"),
                                  changelog=text.replace("## [Unreleased]\n", "## [Unreleased]\n\n- fix: b\n"))
    host.pr_numbers[f"task/{second}"] = 8
    host.mergeable[f"task/{second}"] = "CONFLICTING"
    advance_main(projet, "CHANGELOG.md", text.replace("## [Unreleased]\n", "## [Unreleased]\n\n- feat: autre\n"))
    tick(projet, host)
    for tid, wt in ((first, wt1), (second, wt2)):
        give_ready(host, tid, wt, host.session_of(f"grimoire-review-{tid}"))
    tick(projet, host)
    report = tick(projet, host)
    outcome = {o.task_id: (o.action, o.status) for o in report.merges}
    assert outcome == {first: ("realign", "done"), second: ("wait", "waiting")}, report.merges  # l'union le résoudra


# ── 4. Base de diff du relecteur ───────────────────────────────────────────────


def test_la_consigne_du_relecteur_diffe_depuis_la_base_reelle_de_la_pr(projet: Path, host: FakeHost) -> None:
    tid, wt = launch_and_work(projet, host)
    base_sha = find_task_worktree(TaskService(projet), tid).base_sha  # type: ignore[union-attr]
    advance_main(projet, "autre.py", "autre\n")
    git(wt, "fetch", "-q", "origin", "main")
    git(wt, "merge", "-q", "--no-edit", "origin/main")  # le réalignement : main fusionnée dans task/<id>
    tick(projet, host)  # lance le relecteur
    prompt = prompt_of(host, f"grimoire-review-{tid}")
    assert f"{base_sha}...HEAD" not in prompt
    assert "git merge-base origin/main HEAD" in prompt and "gh pr diff" in prompt and "uv.lock" in prompt
    assert f"gh pr checks task/{tid} --required" in prompt
    found = re.search(r"`git diff (\S+)\.\.\.HEAD`", prompt)
    assert found is not None
    changed = subprocess.run(["git", "-C", str(wt), "diff", "--name-only", f"{found.group(1)}...HEAD"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert changed == ["src/app/route.py"]  # le travail de la tâche, pas tout main
