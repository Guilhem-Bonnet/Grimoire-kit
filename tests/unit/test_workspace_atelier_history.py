"""Atelier de spec : l'historique des versions d'un chantier (git, lecture seule).

De vrais dépôts git temporaires : commits successifs, renommage, fichier non suivi, projet hors dépôt, dépôt sans commit,
identifiant forgé ou étranger au chantier, option injectée, sortie énorme, délai. Aucun test n'écrit ailleurs que dans son
``tmp_path``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_history as h

P = "/api/workspace/atelier/chantiers/"
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git absent")


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.org",
        "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.org",
    })
    done = subprocess.run(
        ["git", "-c", "user.name=Ada", "-c", "user.email=ada@example.org", "-c", "commit.gpgsign=false", *args],
        cwd=root, capture_output=True, text=True, check=True, env=env,
    )
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: p\n", encoding="utf-8")
    wa.set_atelier(root, True)
    return root


def _files(root: Path, slug: str) -> dict[str, Path]:
    base = root / "_grimoire" / "cadrage" / slug
    return {p.name: p for p in base.glob("0*.md")}


@pytest.fixture
def repo(project: Path) -> tuple[Path, str, list[str]]:
    """Un dépôt avec un chantier et trois commits : brief, puis exigences, puis brief encore."""
    made = new_chantier(project, "Dictée vocale")
    slug = made.slug
    files = _files(project, slug)
    brief, exig = files["01-brief.md"], files["04-exigences.md"]
    git(project, "init", "-q", "-b", "main")
    shutil.rmtree(project / ".git" / "hooks", ignore_errors=True)
    shas: list[str] = []
    brief.write_text("# Brief\n\nUne ligne.\nDeux lignes.\n", encoding="utf-8")
    exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter\n", encoding="utf-8")
    git(project, "add", "--", str(brief.relative_to(project)), str(exig.relative_to(project)))
    git(project, "commit", "-q", "-m", "feat: premier jet")
    shas.append(git(project, "rev-parse", "HEAD"))
    exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter\n- REQ-002 [Should] : repli\n", encoding="utf-8")
    git(project, "add", "--", str(exig.relative_to(project)))
    git(project, "commit", "-q", "-m", "feat: exigences")
    shas.append(git(project, "rev-parse", "HEAD"))
    brief.write_text("# Brief\n\nUne ligne.\nTrois lignes.\nQuatre.\n", encoding="utf-8")
    git(project, "add", "--", str(brief.relative_to(project)))
    git(project, "commit", "-q", "-m", "fix: brief")
    shas.append(git(project, "rev-parse", "HEAD"))
    return project, slug, shas


# ── Liste ───────────────────────────────────────────────────────────────────────────────────────────────────


@needs_git
def test_listing_three_commits_newest_first(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, shas = repo
    out = h.listing(root, slug)
    assert out["available"] is True and out["reason"] == ""
    assert [v["id"] for v in out["versions"]] == list(reversed(shas))
    newest = out["versions"][0]
    assert newest["subject"] == "fix: brief" and newest["author"] == "Ada" and newest["kind"] == "commit"
    assert [p["id"] for p in newest["phases"]] == ["brief"]
    assert (newest["added"], newest["removed"]) == (2, 1)
    oldest = out["versions"][-1]
    assert [p["id"] for p in oldest["phases"]] == ["brief", "exigences"]
    assert oldest["short"] == shas[0][:7] and oldest["date"]
    assert out["has_more"] is False and out["page"] == 1 and out["page_size"] == h.PAGE_SIZE


@needs_git
def test_listing_ignores_other_files_and_other_chantiers(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _shas = repo
    (root / "autre.txt").write_text("x\n", encoding="utf-8")
    other = new_chantier(root, "Autre chantier")
    note = _files(root, other.slug)["01-brief.md"]
    note.write_text("# Brief\n\nautre\n", encoding="utf-8")
    git(root, "add", "--", "autre.txt", str(note.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: autre")
    assert len(h.listing(root, slug)["versions"]) == 3
    assert [v["subject"] for v in h.listing(root, other.slug)["versions"]] == ["chore: autre"]


@needs_git
def test_listing_working_entry(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _shas = repo
    exig = _files(root, slug)["04-exigences.md"]
    exig.write_text(exig.read_text(encoding="utf-8") + "- REQ-003 [Could] : x\n", encoding="utf-8")
    out = h.listing(root, slug)
    first = out["versions"][0]
    assert first["id"] == "working" and first["kind"] == "working" and first["date"] is None
    assert (first["added"], first["removed"]) == (1, 0) and [p["id"] for p in first["phases"]] == ["exigences"]
    assert len(out["versions"]) == 4
    assert h.listing(root, slug, "2")["versions"] == []  # la page 2 ne répète pas l'entrée


@needs_git
def test_listing_pagination(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, shas = repo
    monkeypatch.setattr(h, "PAGE_SIZE", 2)
    first = h.listing(root, slug)
    assert [v["id"] for v in first["versions"]] == [shas[2], shas[1]] and first["has_more"] is True
    second = h.listing(root, slug, "2")
    assert [v["id"] for v in second["versions"]] == [shas[0]] and second["has_more"] is False and second["page"] == 2
    for bad in ("abc", "", "-4", "0", "99999999"):
        assert h.listing(root, slug, bad)["page"] in (1, h.MAX_PAGE)


@needs_git
def test_listing_rename(repo: tuple[Path, str, list[str]]) -> None:
    """Un fichier de phase renommé hors du chantier puis remis : l'ajout est une version, rien ne casse."""
    root, slug, _shas = repo
    brief = _files(root, slug)["01-brief.md"]
    parked = root / "parking.md"
    git(root, "mv", str(brief.relative_to(root)), "parking.md")
    git(root, "commit", "-q", "-m", "chore: range le brief")
    git(root, "mv", "parking.md", str(brief.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: remet le brief")
    assert not parked.exists()
    subjects = [v["subject"] for v in h.listing(root, slug)["versions"]]
    assert subjects[:2] == ["chore: remet le brief", "chore: range le brief"]
    detail = h.detail(root, slug, h.listing(root, slug)["versions"][0]["id"])
    assert [p["id"] for p in detail["phases"]] == ["brief"] and detail["phases"][0]["removed"] == 0


@needs_git
def test_untracked_files_reason(project: Path) -> None:
    made = new_chantier(project, "Neuf")
    git(project, "init", "-q", "-b", "main")
    (project / "x.txt").write_text("x\n", encoding="utf-8")
    git(project, "add", "--", "x.txt")
    git(project, "commit", "-q", "-m", "init")
    out = h.listing(project, made.slug)
    assert out["versions"] == [] and out["reason"] == "fichiers-non-suivis" and out["available"] is True
    assert out["untracked"] and "suivis" in out["reason_text"]


@needs_git
def test_tracked_but_never_committed_touching_is_no_version(repo: tuple[Path, str, list[str]]) -> None:
    root, _slug, _ = repo
    made = new_chantier(root, "Sans commit")
    out = h.listing(root, made.slug)
    assert out["versions"] == [] and out["reason"] == "fichiers-non-suivis"


@needs_git
def test_aucune_version_reason(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _ = repo
    real = h._git

    def fake(r: Path, args: list[str], **kw: Any) -> h._Out:
        return h._Out(0, b"") if args[0] == "log" else real(r, args, **kw)

    monkeypatch.setattr(h, "_git", fake)
    monkeypatch.setattr(h, "_working", lambda *_a: (None, []))
    out = h.listing(root, slug)
    assert out["versions"] == [] and out["reason"] == "aucune-version" and out["available"] is True
    assert h.listing(root, slug, "2")["reason"] == ""


def test_unknown_chantier_is_404(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        h.listing(project, "inexistant")
    with pytest.raises(FileNotFoundError):
        h.detail(project, "inexistant", "working")
    with pytest.raises(FileNotFoundError):
        h.listing(project, "../etc")
    with pytest.raises(FileNotFoundError):
        h.listing(project, "Pas Un Slug")


@needs_git
def test_no_repository(project: Path) -> None:
    made = new_chantier(project, "Hors dépôt")
    out = h.listing(project, made.slug)
    assert out["versions"] == [] and out["reason"] == "pas-de-depot" and out["available"] is False
    assert out["reason_text"]
    with pytest.raises(FileNotFoundError):
        h.detail(project, made.slug, "working")


@needs_git
def test_repository_without_commit(project: Path) -> None:
    made = new_chantier(project, "Sans commit")
    git(project, "init", "-q", "-b", "main")
    out = h.listing(project, made.slug)
    assert out["versions"] == [] and out["reason"] == "depot-sans-commit" and out["available"] is True


def test_git_absent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = new_chantier(project, "Sans git")

    def boom(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(h.subprocess, "Popen", boom)
    out = h.listing(project, made.slug)
    assert out["versions"] == [] and out["reason"] == "git-absent" and out["available"] is False


def test_git_unlaunchable_is_unreadable(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = new_chantier(project, "Illisible")

    def boom(*_a: Any, **_k: Any) -> Any:
        raise PermissionError("git")

    monkeypatch.setattr(h.subprocess, "Popen", boom)
    assert h.listing(project, made.slug)["reason"] == "git-illisible"


@needs_git
def test_git_failing_log_is_unreadable(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _ = repo
    real = h._git

    def fake(r: Path, args: list[str], **kw: Any) -> h._Out:
        return h._Out(1, b"") if args[0] == "log" else real(r, args, **kw)

    monkeypatch.setattr(h, "_git", fake)
    assert h.listing(root, slug)["reason"] == "git-illisible"
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, shas_prefix(root))


def shas_prefix(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")[:10]


# ── Détail ──────────────────────────────────────────────────────────────────────────────────────────────────


@needs_git
def test_detail_structured_diff(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, shas = repo
    out = h.detail(root, slug, shas[2])
    assert out["id"] == shas[2] and out["subject"] == "fix: brief" and out["author"] == "Ada" and out["kind"] == "commit"
    assert (out["added"], out["removed"]) == (2, 1) and out["truncated"] is False
    (phase,) = out["phases"]
    assert phase["id"] == "brief" and phase["title"]
    kinds = [(r["t"], r["text"]) for r in phase["rows"]]
    assert ("del", "Deux lignes.") in kinds and ("add", "Trois lignes.") in kinds and ("add", "Quatre.") in kinds
    assert ("ctx", "Une ligne.") in kinds
    adds = [r for r in phase["rows"] if r["t"] == "add"]
    assert adds[0]["old"] is None and adds[0]["new"] == 4
    assert next(r for r in phase["rows"] if r["t"] == "del")["new"] is None


@needs_git
def test_detail_by_short_id_and_first_commit_is_all_additions(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, shas = repo
    out = h.detail(root, slug, shas[0][:7].upper())
    assert out["id"] == shas[0]
    assert [p["id"] for p in out["phases"]] == ["brief", "exigences"]
    assert all(p["removed"] == 0 and p["added"] > 0 for p in out["phases"])


@needs_git
def test_detail_working_tree(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _ = repo
    brief = _files(root, slug)["01-brief.md"]
    brief.write_text("# Brief\n\nUne ligne.\nTrois lignes.\nCinq.\n", encoding="utf-8")
    out = h.detail(root, slug, "working")
    assert out["kind"] == "working" and out["id"] == "working" and out["subject"] == "Modifications non commitées"
    assert (out["added"], out["removed"]) == (1, 1)
    assert [r["text"] for r in out["phases"][0]["rows"] if r["t"] == "add"] == ["Cinq."]


@needs_git
def test_detail_working_tree_ignores_untracked_phase(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _ = repo
    files = _files(root, slug)
    files["02-brainstorm.md"].write_text("# Brainstorm\n\nnouveau\n", encoding="utf-8")
    assert "working" not in [v["id"] for v in h.listing(root, slug)["versions"]]
    assert "brainstorm" in h.listing(root, slug)["untracked"]
    out = h.detail(root, slug, "working")
    assert out["phases"] == [] and (out["added"], out["removed"]) == (0, 0)


@needs_git
@pytest.mark.parametrize("forged", [
    "--output=/tmp/pwn", "-1", "--all", "-n", "../../etc", "abc", "abcdef", "zzzzzzz", "HEAD", "main", "HEAD~1",
    "a" * 41, "a" * 7 + ";rm -rf", "0" * 40, "", "working ", " working", "WORKING",
])
def test_forged_or_malformed_ids_are_refused(repo: tuple[Path, str, list[str]], forged: str) -> None:
    root, slug, _ = repo
    with pytest.raises((ValueError, FileNotFoundError)):
        h.detail(root, slug, forged)


@needs_git
def test_option_never_reaches_git(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _ = repo
    seen: list[list[str]] = []
    real = h._git

    def spy(r: Path, args: list[str], **kw: Any) -> h._Out:
        seen.append(list(args))
        return real(r, args, **kw)

    monkeypatch.setattr(h, "_git", spy)
    target = root / "pwn.txt"
    for forged in (f"--output={target}", "-p", "--ext-diff"):
        with pytest.raises(ValueError):
            h.detail(root, slug, forged)
    assert not target.exists()
    assert all(not a.startswith("--output") for args in seen for a in args)


@needs_git
def test_foreign_commit_is_refused(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _shas = repo
    (root / "ailleurs.txt").write_text("autre\n", encoding="utf-8")
    git(root, "add", "--", "ailleurs.txt")
    git(root, "commit", "-q", "-m", "chore: hors chantier")
    foreign = git(root, "rev-parse", "HEAD")
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, foreign)
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, foreign[:8])
    # même un commit d'un AUTRE chantier est étranger à celui-ci
    other = new_chantier(root, "Autre")
    note = _files(root, other.slug)["01-brief.md"]
    note.write_text("# Brief\n\nautre\n", encoding="utf-8")
    git(root, "add", "--", str(note.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: autre chantier")
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, git(root, "rev-parse", "HEAD"))


@needs_git
def test_ambiguous_prefix_is_refused(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, shas = repo
    twin = shas[0][:7] + "0" * 33
    real = h._git

    def fake(r: Path, args: list[str], **kw: Any) -> h._Out:
        if args[0] == "log" and "--format=%H" in args:
            return h._Out(0, f"{shas[0]}\n{twin}\n".encode())
        return real(r, args, **kw)

    monkeypatch.setattr(h, "_git", fake)
    with pytest.raises(FileNotFoundError):
        h.detail(root, slug, shas[0][:7])


@needs_git
def test_binary_file_has_no_line_diff(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _ = repo
    exig = _files(root, slug)["04-exigences.md"]
    exig.write_bytes(b"\x00\x01\x02binaire\n")
    git(root, "add", "--", str(exig.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: binaire")
    out = h.detail(root, slug, git(root, "rev-parse", "HEAD"))
    (phase,) = out["phases"]
    assert phase["rows"] == [] and "binaire" in phase["note"]
    assert h.listing(root, slug)["versions"][0]["added"] == 0  # numstat dit « - » pour un binaire


@needs_git
def test_huge_file_is_bounded(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _ = repo
    monkeypatch.setattr(h, "MAX_BLOB", 2000)
    brief = _files(root, slug)["01-brief.md"]
    brief.write_text("ligne très longue " * 5 + "\n" + "\n".join(f"l{i}" for i in range(2000)) + "\n", encoding="utf-8")
    git(root, "add", "--", str(brief.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: énorme")
    out = h.detail(root, slug, git(root, "rev-parse", "HEAD"))
    (phase,) = out["phases"]
    assert phase["truncated"] is True and phase["rows"] == [] and out["truncated"] is True
    brief.write_text(brief.read_text(encoding="utf-8") + "fin\n", encoding="utf-8")
    work = h.detail(root, slug, "working")  # trop gros côté disque et côté HEAD : rien d'inventé
    assert work["phases"][0]["rows"] == [] and work["phases"][0]["truncated"] is True
    assert h.listing(root, slug)["versions"][0]["id"] != "working"


@needs_git
def test_row_budget_is_bounded(repo: tuple[Path, str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    root, slug, _ = repo
    brief = _files(root, slug)["01-brief.md"]
    brief.write_text("\n".join(f"ligne {i}" for i in range(300)) + "\n", encoding="utf-8")
    git(root, "add", "--", str(brief.relative_to(root)))
    git(root, "commit", "-q", "-m", "chore: gros")
    monkeypatch.setattr(h, "MAX_ROWS", 50)
    out = h.detail(root, slug, git(root, "rev-parse", "HEAD"))
    assert len(out["phases"][0]["rows"]) <= 50 and out["truncated"] is True
    assert out["added"] >= 300  # les compteurs restent ceux du diff entier


def test_line_diff_pure() -> None:
    assert h.line_diff("a\nb\n", "a\nb\n") == {"added": 0, "removed": 0, "rows": [], "truncated": False}
    out = h.line_diff("\n".join(f"l{i}" for i in range(30)), "\n".join(f"l{i}" if i not in (2, 25) else f"x{i}" for i in range(30)))
    assert (out["added"], out["removed"]) == (2, 2)
    assert any(r["t"] == "gap" and r["skipped"] > 0 for r in out["rows"])
    assert out["rows"][0]["t"] == "ctx"
    assert [r["text"] for r in out["rows"] if r["t"] == "add"] == ["x2", "x25"]
    long = h.line_diff("", "y" * (h.MAX_LINE_CHARS + 50))
    assert long["rows"][0]["text"].endswith("…") and len(long["rows"][0]["text"]) == h.MAX_LINE_CHARS + 1
    many = "\n".join(str(i) for i in range(h.MAX_DIFF_LINES + 10))
    assert h.line_diff("", many)["truncated"] is True
    assert h.line_diff("", many, 5)["rows"].__len__() == 5
    assert h.line_diff("a", "")["rows"] == [{"t": "del", "old": 1, "new": None, "text": "a"}]


# ── Garde-fous du lanceur git ────────────────────────────────────────────────────────────────────────────


@needs_git
def test_git_runner_flags_and_no_shell(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    real = subprocess.Popen

    def spy(argv: list[str], **kw: Any) -> Any:
        captured["argv"], captured["kw"] = argv, kw
        return real(argv, **kw)

    monkeypatch.setenv("GIT_DIR", "/nowhere")
    monkeypatch.setattr(h.subprocess, "Popen", spy)
    h._git(project, ["--version"])
    argv, kw = captured["argv"], captured["kw"]
    assert isinstance(argv, list) and argv[0] == "git" and not kw.get("shell")
    assert argv[:7] == ["git", "--no-pager", "--literal-pathspecs", "-c", "core.quotepath=off", "-c", "core.fsmonitor=false"]
    assert "GIT_DIR" not in kw["env"] and kw["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert kw["stdin"] == subprocess.DEVNULL


@needs_git
def test_git_runner_cuts_huge_output(project: Path) -> None:
    git(project, "init", "-q", "-b", "main")
    out = h._git(project, ["log", "--format=%H", "-n", "1"], limit=10)  # dépôt vide : code 128, sortie vide
    assert out.truncated is False
    (project / "big.txt").write_text("x" * 5000, encoding="utf-8")
    git(project, "add", "--", "big.txt")
    git(project, "commit", "-q", "-m", "gros")
    cut = h._git(project, ["show", "HEAD:./big.txt"], limit=100)
    assert cut.truncated is True and len(cut.data) == 100


@needs_git
def test_git_runner_times_out(project: Path) -> None:
    class Slow:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.stdout = _Pipe()
            self.returncode = -9

        def kill(self) -> None:
            self.stdout.release.set()

        def wait(self) -> int:
            return self.returncode

    import threading

    class _Pipe:
        def __init__(self) -> None:
            self.release = threading.Event()

        def read(self, _n: int) -> bytes:
            self.release.wait(10)
            return b""

        def close(self) -> None:
            return None

    original = h.subprocess.Popen
    h.subprocess.Popen = Slow  # type: ignore[misc,assignment]
    try:
        out = h._git(project, ["log"], timeout=0.2)
    finally:
        h.subprocess.Popen = original  # type: ignore[misc]
    assert out.failed == "delai"


@needs_git
def test_slow_git_gives_clean_answer(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = new_chantier(project, "Lent")
    monkeypatch.setattr(h, "_git", lambda *_a, **_k: h._Out(-9, b"", failed="delai"))
    assert h.listing(project, made.slug)["reason"] == "git-illisible"


@needs_git
def test_symlinked_phase_is_skipped(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, _ = repo
    brief = _files(root, slug)["01-brief.md"]
    outside = root.parent / "dehors.md"
    outside.write_text("# secret\n", encoding="utf-8")
    brief.unlink()
    brief.symlink_to(outside)
    out = h.listing(root, slug)
    assert all(p["id"] != "brief" for v in out["versions"] for p in v["phases"] if v["id"] == "working")
    assert all(p["id"] != "brief" for p in h.detail(root, slug, "working")["phases"])


# ── Routes ──────────────────────────────────────────────────────────────────────────────────────────────────


@needs_git
def test_routes_through_atelier_get(repo: tuple[Path, str, list[str]]) -> None:
    root, slug, shas = repo
    listed = wa.get(root, f"{P}{slug}/history", {})
    assert [v["id"] for v in listed["versions"]] == list(reversed(shas))
    assert wa.get(root, f"{P}{slug}/history", {"page": ["2"]})["versions"] == []
    seen = wa.get(root, f"{P}{slug}/history/{shas[1]}", {})
    assert seen["id"] == shas[1] and seen["phases"][0]["id"] == "exigences"
    with pytest.raises(ValueError):
        wa.get(root, f"{P}{slug}/history/--output=x", {})
    with pytest.raises(FileNotFoundError):
        wa.get(root, f"{P}{slug}/history/{'a' * 40}", {})
    with pytest.raises(FileNotFoundError):
        wa.get(root, f"{P}{slug}/history/{shas[1]}/extra", {})


def test_routes_are_404_when_flag_is_off(tmp_path: Path) -> None:
    root = tmp_path / "off"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: p\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        wa.get(root, f"{P}x/history", {})


def test_no_mutating_git_command_is_used() -> None:
    source = Path(h.__file__).read_text(encoding="utf-8")
    for verb in ("commit", "checkout", "reset", "restore", "clean", "push", "fetch", "pull", "add", "stash", "rm", "merge"):
        assert f'["{verb}"' not in source and f', "{verb}"' not in source
    assert "shell=True" not in source and "os.system" not in source
