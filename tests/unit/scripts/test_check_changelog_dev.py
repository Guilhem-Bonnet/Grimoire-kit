"""``check-changelog-release.py --dev`` — la convention « une entrée par PR feat/fix/perf », posée avant le tag.

Chaque test construit une branche réelle (dépôt git jetable) et demande à la
garde de la juger. Le cas sain n'est là qu'en contre-épreuve.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-changelog-release.py"
START = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n- **Départ.** Rien.\n"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_changelog_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=test", *args],
        cwd=root, check=True, capture_output=True, encoding="utf-8",
    )


def _commit(root: Path, message: str, **files: str) -> None:
    for name, text in files.items():
        (root / name.replace("__", ".")).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "chore: départ", CHANGELOG__md=START, code__py="a = 1\n")
    _git(tmp_path, "checkout", "-q", "-b", "travail")
    return tmp_path


def test_a_branch_without_noteworthy_commit_needs_no_entry(repo: Path) -> None:
    _commit(repo, "docs: une précision", code__py="a = 2\n")
    assert MOD.dev_problems(repo) == []


def test_a_feat_without_entry_is_refused(repo: Path) -> None:
    _commit(repo, "feat(x): une fonctionnalité", code__py="a = 2\n")
    found = MOD.dev_problems(repo)
    assert len(found) == 1 and "sans nouvelle entrée" in found[0] and "feat(x)" in found[0]


@pytest.mark.parametrize("kind", ["fix", "perf"])
def test_fix_and_perf_are_noteworthy_too(repo: Path, kind: str) -> None:
    _commit(repo, f"{kind}: un changement", code__py="a = 2\n")
    assert MOD.dev_problems(repo)


def test_an_entry_under_unreleased_satisfies_the_branch(repo: Path) -> None:
    entry = START.replace("## [Unreleased]\n", "## [Unreleased]\n\n- **Nouveauté.** Elle fait ceci.\n", 1)
    _commit(repo, "feat: une fonctionnalité", CHANGELOG__md=entry, code__py="a = 2\n")
    assert MOD.dev_problems(repo) == []


def test_an_entry_slid_under_a_published_version_is_refused(repo: Path) -> None:
    glissee = START + "- **Nouveauté.** Elle est sous la 1.0.0.\n"
    _commit(repo, "feat: une fonctionnalité", CHANGELOG__md=glissee, code__py="a = 2\n")
    found = MOD.dev_problems(repo)
    assert len(found) == 1 and "n'est pas sous [Unreleased]" in found[0]


def test_an_entry_still_in_the_working_tree_counts(repo: Path) -> None:
    """Au pre-push comme au pre-commit, l'entrée peut ne pas être committée : on juge l'arbre, pas HEAD."""
    _commit(repo, "feat: une fonctionnalité", code__py="a = 2\n")
    (repo / "CHANGELOG.md").write_text(
        START.replace("## [Unreleased]\n", "## [Unreleased]\n\n- **Nouveauté.** Là.\n", 1), encoding="utf-8"
    )
    assert MOD.dev_problems(repo) == []


def test_no_base_reference_fails_instead_of_passing(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "autre")
    _commit(tmp_path, "feat: x", CHANGELOG__md=START)
    found = MOD.dev_problems(tmp_path)
    assert len(found) == 1 and "aucune base de comparaison" in found[0]


def test_an_explicit_base_is_honoured(repo: Path) -> None:
    _commit(repo, "feat: une fonctionnalité", code__py="a = 2\n")
    assert MOD.dev_problems(repo, "main")
    assert "aucune base" in MOD.dev_problems(repo, "n-existe-pas")[0]


def test_a_missing_unreleased_section_is_refused(repo: Path) -> None:
    _commit(repo, "chore: cassé", CHANGELOG__md="# Changelog\n\n## [1.0.0] - 2026-01-01\n")
    assert "pas de section [Unreleased]" in MOD.dev_problems(repo)[0]



def test_the_plain_entry_style_of_unreleased_is_recognised(repo: Path) -> None:
    """[Unreleased] écrit `- fix(scope): …` sans gras : la garde ne doit pas ne rien y voir."""
    entry = START.replace("## [Unreleased]\n", "## [Unreleased]\n\n- feat(x): une fonctionnalité. Constat.\n  - Changement : un détail.\n", 1)
    _commit(repo, "feat(x): une fonctionnalité", CHANGELOG__md=entry, code__py="a = 2\n")
    assert MOD.dev_problems(repo) == []


def test_an_indented_detail_line_alone_is_not_an_entry(repo: Path) -> None:
    detail = START.replace("- **Départ.** Rien.\n", "- **Départ.** Rien.\n  - Détail ajouté.\n", 1)
    _commit(repo, "feat: une fonctionnalité", CHANGELOG__md=detail, code__py="a = 2\n")
    assert "sans nouvelle entrée" in MOD.dev_problems(repo)[0]


# ── D11-S1-01 : la garde juge le titre de la PR, que le squash-merge conserve ─────


def test_a_fix_title_over_neutral_commits_is_refused(repo: Path) -> None:
    """Les commits de la branche ne portent pas le type : le titre de la PR, si."""
    _commit(repo, "corrige le calcul (relecture)", code__py="a = 2\n")
    assert MOD.dev_problems(repo) == []  # repli sur les commits : rien à voir
    found = MOD.dev_problems(repo, title="fix(x): corrige le calcul")
    assert len(found) == 1 and "sans nouvelle entrée" in found[0] and "titre de la PR" in found[0]


def test_a_docs_title_over_a_fix_commit_needs_no_entry(repo: Path) -> None:
    _commit(repo, "fix(docs): coquille relevée en relecture", code__py="a = 2\n")
    assert MOD.dev_problems(repo)  # sans titre, le sujet du commit déclenche
    assert MOD.dev_problems(repo, title="docs: corrige une coquille") == []


def test_a_fix_title_with_an_entry_under_unreleased_passes(repo: Path) -> None:
    entry = START.replace("## [Unreleased]\n", "## [Unreleased]\n\n- **Calcul.** Corrigé.\n", 1)
    _commit(repo, "corrige le calcul (relecture)", CHANGELOG__md=entry, code__py="a = 2\n")
    assert MOD.dev_problems(repo, title="fix(x): corrige le calcul (#1)") == []


def _event(tmp_path: Path, payload: object) -> dict[str, str]:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"GITHUB_EVENT_PATH": str(path)}


def test_the_title_is_read_from_the_github_event(tmp_path: Path) -> None:
    assert MOD.pr_title_from_event(_event(tmp_path, {"pull_request": {"title": " fix(x): a "}})) == "fix(x): a"


@pytest.mark.parametrize(
    "payload", [{"ref": "refs/heads/main"}, {"pull_request": {}}, {"pull_request": {"title": "  "}}, ["x"], {"pull_request": {"title": 3}}]
)
def test_an_event_without_a_title_gives_none(tmp_path: Path, payload: object) -> None:
    assert MOD.pr_title_from_event(_event(tmp_path, payload)) is None


def test_no_event_or_an_unreadable_one_gives_none(tmp_path: Path) -> None:
    assert MOD.pr_title_from_event({}) is None
    assert MOD.pr_title_from_event({"GITHUB_EVENT_PATH": str(tmp_path / "absent.json")}) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{pas du json", encoding="utf-8")
    assert MOD.pr_title_from_event({"GITHUB_EVENT_PATH": str(broken)}) is None


def test_main_judges_the_event_title_and_says_so(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "corrige le calcul (relecture)", code__py="a = 2\n")
    monkeypatch.setattr(MOD, "REPO", repo)
    monkeypatch.setenv("GITHUB_EVENT_PATH", _event(tmp_path, {"pull_request": {"title": "fix(x): corrige le calcul"}})["GITHUB_EVENT_PATH"])
    assert MOD.main(["--dev"]) == 1
    captured = capsys.readouterr()
    assert "titre de la PR (événement GitHub)" in captured.out
    assert "sans nouvelle entrée" in captured.err


def test_main_says_when_it_falls_back_on_commit_subjects(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "docs: une précision", code__py="a = 2\n")
    monkeypatch.setattr(MOD, "REPO", repo)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert MOD.main(["--dev"]) == 0
    assert "sujets des commits" in capsys.readouterr().out


def test_main_title_argument_wins_and_needs_a_value(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "fix(docs): coquille", code__py="a = 2\n")
    monkeypatch.setattr(MOD, "REPO", repo)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert MOD.main(["--dev", "--title", "docs: coquille"]) == 0
    assert "--title" in capsys.readouterr().out
    assert MOD.main(["--dev", "--title"]) == 2


def test_a_fragment_satisfies_the_branch(repo: Path) -> None:
    (repo / "changelog.d").mkdir()
    _commit(repo, "feat: une fonctionnalité", code__py="a = 2\n")
    (repo / "changelog.d" / "ma-pr.md").write_text("- feat: elle fait ceci\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: fragment")
    assert MOD.dev_problems(repo) == []


def test_an_empty_fragment_or_readme_does_not_count(repo: Path) -> None:
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / "README.md").write_text("- exemple\n", encoding="utf-8")
    (repo / "changelog.d" / "vide.md").write_text("rien\n", encoding="utf-8")
    _commit(repo, "fix: un correctif", code__py="a = 2\n")
    assert MOD.dev_problems(repo)


def test_two_prs_with_fragments_merge_without_conflict(repo: Path) -> None:
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / ".gitkeep").write_text("", encoding="utf-8")
    _commit(repo, "chore: dossier")
    _git(repo, "checkout", "-q", "-b", "pr-a", "main")
    _git(repo, "merge", "-q", "--ff-only", "travail")
    _git(repo, "checkout", "-q", "-b", "pr-b", "travail")
    _git(repo, "checkout", "-q", "pr-a")
    (repo / "changelog.d" / "a.md").write_text("- feat: a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: a")
    _git(repo, "checkout", "-q", "pr-b")
    (repo / "changelog.d" / "b.md").write_text("- feat: b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: b")
    proc = subprocess.run(["git", "merge-tree", "--write-tree", "pr-a", "pr-b"], cwd=repo, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
