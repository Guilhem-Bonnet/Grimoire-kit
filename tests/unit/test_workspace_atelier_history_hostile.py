"""Historique de l'Atelier : un dépôt à configuration hostile ne fait lancer aucun programme à git.

Chaque cas arme un dépôt réel dont la configuration (ou les attributs) déclare un « programme » : un script qui écrit un
fichier marqueur. Après la liste, le détail de chaque version et les modifications non commitées, AUCUN marqueur ne doit
exister. Un contrôle positif (git brut) prouve que le piège est bien armé.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_history as h

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


class Trap:
    def __init__(self, root: Path) -> None:
        self.dir = root / "trap"
        self.marks = self.dir / "marks"
        self.marks.mkdir(parents=True)

    def prog(self, name: str) -> str:
        script = self.dir / f"{name}.sh"
        script.write_text(f'#!/bin/sh\necho "$*" >> "{self.marks}/{name}"\ncat >/dev/null 2>&1 || true\nexit 1\n', encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    def written(self) -> list[str]:
        return sorted(p.name for p in self.marks.iterdir())

    def clear(self) -> None:
        for p in self.marks.iterdir():
            p.unlink()


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)


# Aucune maintenance en arrière-plan : un `git commit` lancerait sinon `maintenance run --auto` détaché, dont le verrou
# (`.git/objects/maintenance.lock`) paraît ou disparaît pendant que le test compare `.git` avant/après.
_GIT_QUIET = ["git", "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
              "-c", "maintenance.auto=false"]


def _env() -> dict[str, str]:
    return {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "A",
            "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@x"}


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run([*_GIT_QUIET, *args], cwd=root, capture_output=True, text=True, check=True,
                          env=_env(), input=stdin, stdin=None if stdin else subprocess.DEVNULL)
    return done.stdout.strip()


def build(tmp_path: Path, *, signed: bool = False) -> tuple[Path, str, Path, Trap]:
    root = tmp_path / "p"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: p\n", encoding="utf-8")
    wa.set_atelier(root, True)
    slug = new_chantier(root, "Piege").slug
    brief = next((root / "_grimoire" / "cadrage" / slug).glob("01-*.md"))
    git(root, "init", "-q", "-b", "main")
    brief.write_text("# B\nun\n", encoding="utf-8")
    git(root, "add", "--", str(brief.relative_to(root)))
    git(root, "commit", "-q", "-m", "c1")
    brief.write_text("# B\nun\ndeux\n", encoding="utf-8")
    git(root, "add", "--", str(brief.relative_to(root)))
    git(root, "commit", "-q", "-m", "c2")
    if signed:  # un commit portant un en-tête de signature factice
        parent = git(root, "rev-parse", "HEAD")
        brief.write_text("# B\nun\ndeux\ntrois\n", encoding="utf-8")
        git(root, "add", "--", str(brief.relative_to(root)))
        tree = git(root, "write-tree")
        raw = (f"tree {tree}\nparent {parent}\nauthor A <a@x> 1700000000 +0000\ncommitter A <a@x> 1700000000 +0000\n"
               "gpgsig -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----\n\nc3 signe\n")
        git(root, "update-ref", "refs/heads/main", git(root, "hash-object", "-t", "commit", "-w", "--stdin", stdin=raw))
    brief.write_text(brief.read_text(encoding="utf-8") + "non commité\n", encoding="utf-8")
    return root, slug, brief, Trap(tmp_path)


def config(root: Path, **items: str) -> None:
    for key, value in items.items():
        git(root, "config", "--file", ".git/config", key.replace("__", "."), value)


def exercise(root: Path, slug: str) -> list[dict[str, object]]:
    listed = h.listing(root, slug)
    versions = listed["versions"]
    assert versions
    for version in versions:
        assert h.detail(root, slug, version["id"])["phases"]
    return list(versions)  # type: ignore[no-any-return]


def exercise_compare_and_restore(root: Path, slug: str, brief: Path) -> None:
    """Comparer (deux commits, puis la version actuelle) et restaurer la phase, puis annuler : par les routes de l'Atelier."""
    from grimoire.core.cadrage import CADRAGE_DIR
    from grimoire.tools import atelier_blocs

    del CADRAGE_DIR
    ids = [v["id"] for v in h.listing(root, slug)["versions"] if v["kind"] == "commit"]
    assert len(ids) >= 2
    old, new = ids[-1], ids[0]
    base = f"/api/workspace/atelier/chantiers/{slug}"
    assert wa.get(root, f"{base}/compare", {"from": [old], "to": [new]})["phases"]
    assert wa.get(root, f"{base}/compare", {"from": [old], "to": ["working"]})["phases"]
    fp = atelier_blocs.fingerprint(brief.read_text(encoding="utf-8"))
    code, done = wa.post_http(root, f"{base}/restore", {"phase": "brief", "expected": fp, "version": old})
    assert code == 200, done
    assert brief.read_text(encoding="utf-8") == "# B\nun\n"
    fp = atelier_blocs.fingerprint(brief.read_text(encoding="utf-8"))
    code, undone = wa.post_http(root, f"{base}/restore", {"phase": "brief", "expected": fp, "undo": done["undo"]})
    assert code == 200, undone
    assert brief.read_text(encoding="utf-8").endswith("non commité\n")


def test_compare_and_restore_never_run_a_declared_program(tmp_path: Path) -> None:
    root, slug, brief, trap = build(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("post-checkout", "post-index-change", "reference-transaction", "pre-commit", "post-merge", "fsmonitor-watchman"):
        shutil.copy(trap.prog(f"hook-{name}"), hooks / name)
    (root / ".gitattributes").write_text("*.md diff=evil filter=evil text eol=crlf\n", encoding="utf-8")
    config(root, core__hooksPath=str(hooks), core__pager=trap.prog("pager"), core__fsmonitor=trap.prog("fsmonitor"),
           core__sshCommand=trap.prog("ssh"), diff__external=trap.prog("extdiff"), diff__evil__textconv=trap.prog("textconv"),
           diff__evil__command=trap.prog("diffcmd"), filter__evil__clean=trap.prog("clean"), filter__evil__smudge=trap.prog("smudge"),
           alias__show=f"!{trap.prog('alias')}", alias__log=f"!{trap.prog('alias2')}",
           log__showSignature="true", gpg__program=trap.prog("gpg"), core__editor=trap.prog("editor"))
    subprocess.run(["git", "diff", "HEAD~1", "HEAD"], cwd=root, capture_output=True, env=_env(), check=False)
    assert trap.written()  # contrôle positif : le piège est armé
    trap.clear()
    exercise_compare_and_restore(root, slug, brief)
    assert trap.written() == []
    assert git(root, "rev-parse", "HEAD") == git(root, "rev-parse", "refs/heads/main")


def test_compare_and_restore_never_fetch_a_promised_blob(tmp_path: Path) -> None:
    root, slug, brief, trap = build(tmp_path)
    rel = brief.relative_to(root).as_posix()
    blob = git(root, "rev-parse", f"HEAD:{rel}")
    (root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    config(root, core__repositoryformatversion="1", extensions__partialClone="origin", remote__origin__promisor="true",
           remote__origin__url=str(tmp_path / "nowhere"), remote__origin__uploadpack=trap.prog("uploadpack"),
           core__sshCommand=trap.prog("ssh"), core__gitProxy=trap.prog("proxy"))
    ids = git(root, "rev-list", "HEAD").split()  # la liste de l'Atelier n'a plus de compteurs : on nomme les commits à la main
    base = f"/api/workspace/atelier/chantiers/{slug}"
    wa.get(root, f"{base}/compare", {"from": [ids[-1]], "to": [ids[0]]})
    code, _ = wa.post_http(root, f"{base}/restore", {"phase": "brief", "expected": "x", "version": ids[0]})
    assert code in (400, 409)
    assert trap.written() == []


def test_a_restore_leaves_the_repository_untouched(tmp_path: Path) -> None:
    root, slug, brief, _trap = build(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / ".git").rglob("*") if p.is_file()
              and p.name != "index" and "/logs/" not in p.as_posix()}
    index = (root / ".git" / "index").read_bytes()
    exercise_compare_and_restore(root, slug, brief)
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / ".git").rglob("*") if p.is_file()
             and p.name != "index" and "/logs/" not in p.as_posix()}
    assert after == before
    assert (root / ".git" / "index").read_bytes() == index


def test_signature_verification_never_runs(tmp_path: Path) -> None:
    root, slug, _brief, trap = build(tmp_path, signed=True)
    config(root, log__showSignature="true", gpg__program=trap.prog("gpg"), gpg__ssh__program=trap.prog("gpgssh"))
    subprocess.run(["git", "log", "-n", "3", "--format=%H"], cwd=root, capture_output=True, env=_env(), check=True)
    assert "gpg" in trap.written()  # contrôle positif : le piège est armé
    trap.clear()
    exercise(root, slug)
    assert trap.written() == []


def test_lazy_fetch_of_a_missing_blob_never_runs(tmp_path: Path) -> None:
    root, slug, brief, trap = build(tmp_path)
    rel = brief.relative_to(root).as_posix()
    blob = git(root, "rev-parse", f"HEAD:{rel}")
    (root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()  # le blob devient « promis »
    config(root, core__repositoryformatversion="1", extensions__partialClone="origin", remote__origin__promisor="true", remote__origin__url=str(tmp_path / "nowhere"), remote__origin__uploadpack=trap.prog("uploadpack"), core__sshCommand=trap.prog("ssh"), core__gitProxy=trap.prog("proxy"))
    versions = h.listing(root, slug)["versions"]
    for version in versions:
        out = h.detail(root, slug, version["id"])
        assert all(p["rows"] == [] for p in out["phases"] if p["note"]) and isinstance(out["phases"], list)
    assert trap.written() == []


def test_missing_blob_is_reported_not_invented(tmp_path: Path) -> None:
    root, slug, brief, _trap = build(tmp_path)
    rel = brief.relative_to(root).as_posix()
    head = git(root, "rev-parse", "HEAD")
    blob = git(root, "rev-parse", f"HEAD:{rel}")
    (root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    out = h.detail(root, slug, head)
    (phase,) = out["phases"]
    assert phase["rows"] == [] and phase["added"] == 0 and "indisponible" in phase["note"] and out["truncated"] is True


def test_textconv_via_attributes_never_runs(tmp_path: Path) -> None:
    root, slug, _brief, trap = build(tmp_path)
    (root / ".gitattributes").write_text("*.md diff=evil filter=evil\n", encoding="utf-8")
    config(root, diff__evil__textconv=trap.prog("textconv"), diff__evil__command=trap.prog("diffcmd"), filter__evil__clean=trap.prog("clean"), filter__evil__smudge=trap.prog("smudge"))
    subprocess.run(["git", "diff", "HEAD~1", "HEAD"], cwd=root, capture_output=True, env=_env(), check=False)
    assert trap.written()  # contrôle positif : un programme déclaré par les attributs a bien été lancé par git brut
    trap.clear()
    exercise(root, slug)
    assert trap.written() == []


def test_external_diff_never_runs(tmp_path: Path) -> None:
    root, slug, _brief, trap = build(tmp_path)
    config(root, diff__external=trap.prog("extdiff"))
    subprocess.run(["git", "diff", "HEAD~1", "HEAD"], cwd=root, capture_output=True, env=_env(), check=False)
    assert "extdiff" in trap.written()  # contrôle positif
    trap.clear()
    exercise(root, slug)
    assert trap.written() == []


def test_hooks_path_pager_and_fsmonitor_never_run(tmp_path: Path) -> None:
    root, slug, _brief, trap = build(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("post-checkout", "post-index-change", "reference-transaction", "pre-commit", "fsmonitor-watchman"):
        shutil.copy(trap.prog(f"hook-{name}"), hooks / name)
    config(root, core__hooksPath=str(hooks), core__pager=trap.prog("pager"), core__fsmonitor=trap.prog("fsmonitor"), core__sshCommand=trap.prog("ssh2"), pager__log=trap.prog("pagerlog"), alias__log=f"!{trap.prog('alias')}")
    exercise(root, slug)
    assert trap.written() == []


def test_old_git_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _brief, _trap = build(tmp_path)
    real = h._git

    def old(r: Path, args: list[str], **kw: object) -> h._Out:
        return h._Out(0, b"git version 2.43.0\n") if args == ["--version"] else real(r, args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(h, "_git", old)
    out = h.listing(root, slug)
    assert out["versions"] == [] and out["reason"] == "git-trop-ancien" and out["available"] is False
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, "working")

    def odd(r: Path, args: list[str], **kw: object) -> h._Out:
        return h._Out(0, b"nope\n") if args == ["--version"] else real(r, args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(h, "_git", odd)
    assert h.listing(root, slug)["reason"] == "git-trop-ancien"


def test_version_probe_failure_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _brief, _trap = build(tmp_path)
    real = h._git
    calls: list[Callable[..., object]] = []

    def slow(r: Path, args: list[str], **kw: object) -> h._Out:
        calls.append(real)
        return h._Out(124, b"", failed="delai") if args == ["--version"] else real(r, args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(h, "_git", slow)
    assert h.listing(root, slug)["reason"] == "git-illisible"
