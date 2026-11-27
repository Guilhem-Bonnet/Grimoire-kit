"""Le parcours de ``core.integrity`` : mêmes fichiers qu'avant, coût linéaire.

``_readable_files`` comparait chaque fichier à chaque dépôt imbriqué (produit
fichiers x dépôts) et cherchait les ``.git`` dans tout le projet, deux fois par
``grimoire doctor``. Sur un projet qui porte 4 000 worktrees d'agents, le
contrôle dépassait 400 s. Ces tests tiennent deux promesses :

- la liste de fichiers retenus est STRICTEMENT celle de l'ancienne fonction,
  dont une copie figée sert de référence ici ;
- le coût se compte en appels système (``scandir``, ``stat``, ``lstat``), borné
  linéairement quand les dépôts imbriqués se multiplient — jamais en durée.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import integrity
from grimoire.core.integrity import (
    _DELIVERED_FILES,
    _DELIVERED_TREES,
    _READABLE_SUFFIXES,
    _SHARED_TREES,
    _SKIPPED_DIRS,
    _is_managed,
    _readable_files,
    dead_path_references,
    integrity_checks,
    roster_incoherences,
)

_MARK = "<!-- grimoire:managed -->\n"


# ── Référence : l'ancienne implémentation, figée ────────────────────────────


def _reference_nested_roots(project_root: Path) -> set[Path]:
    nested: set[Path] = set()
    for git_entry in project_root.rglob(".git"):
        root = git_entry.parent
        if root != project_root:
            nested.add(root)
    return nested


def _reference_readable_files(project_root: Path) -> list[Path]:
    nested = _reference_nested_roots(project_root)

    delivered: set[Path] = set()
    for tree in _DELIVERED_TREES:
        delivered.update((project_root / tree).rglob("*"))
    delivered.update(project_root / name for name in _DELIVERED_FILES)

    shared: set[Path] = set()
    for tree in _SHARED_TREES:
        shared.update((project_root / tree).rglob("*"))
    shared -= delivered

    files: list[Path] = []
    for path in sorted(delivered | shared):
        if not path.is_file() or path.suffix not in _READABLE_SUFFIXES:
            continue
        if any(part in _SKIPPED_DIRS for part in path.relative_to(project_root).parts):
            continue
        if any(root in path.parents for root in nested):
            continue
        if path in shared and not _is_managed(path):
            continue
        files.append(path)
    return files


# ── Constructeurs de projets ────────────────────────────────────────────────


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _git_dir(where: Path) -> None:
    (where / ".git").mkdir(parents=True, exist_ok=True)


def _git_file(where: Path) -> None:
    _write(where / ".git", "gitdir: elsewhere\n")


def _symlink(link: Path, target: Path, *, is_dir: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError):  # pragma: no cover - plateformes sans liens
        pytest.skip("liens symboliques indisponibles")


def _busy_project(root: Path) -> Path:
    """Un projet qui réunit chaque cas que le filtre doit trancher."""
    _git_dir(root)  # le `.git` du projet n'en fait pas un dépôt imbriqué
    _write(root / "project-context.yaml")
    _write(root / ".mcp.json", "{}")
    _write(root / ".pre-commit-config.yaml")
    _write(root / ".github" / "copilot-instructions.md")
    _write(root / ".github" / "prompts" / "p.md")
    _write(root / ".github" / "workflows" / "ci.yml")  # arbre partagé, sans marqueur
    _write(root / ".github" / "hooks" / "h.json", _MARK)

    # arbres livrés, avec dépôts imbriqués à plusieurs profondeurs
    _write(root / "_grimoire" / "kit" / "agents" / "a.md")
    _write(root / "_grimoire" / "kit" / "agent-manifest.csv")
    _write(root / "_grimoire" / "_memory" / "m.md")
    _git_dir(root / "_grimoire" / "kit" / "vendor")
    _write(root / "_grimoire" / "kit" / "vendor" / "v.md")
    _git_file(root / "_grimoire" / "kit" / "deep" / "a" / "b")
    _write(root / "_grimoire" / "kit" / "deep" / "a" / "b" / "c" / "d.md")
    _write(root / "_grimoire" / "kit" / "deep" / "a" / "keep.md")
    _git_dir(root / "_grimoire" / "_memory" / "sub")
    _git_dir(root / "_grimoire" / "_memory" / "sub" / "inner")  # dépôt dans un dépôt
    _write(root / "_grimoire" / "_memory" / "sub" / "inner" / "i.md")

    # dossiers exclus, et croisements avec les dépôts imbriqués
    _write(root / "_grimoire" / "kit" / "__pycache__" / "p.py")
    _write(root / "_grimoire" / "kit" / "_archived" / "old.md")
    _write(root / ".claude" / "node_modules" / "z.md", _MARK)
    _git_dir(root / ".claude" / "node_modules" / "pkg")  # dépôt DANS un dossier exclu
    _write(root / ".claude" / "node_modules" / "pkg" / "q.md", _MARK)
    _write(root / ".claude" / "agents" / "ok.md", _MARK)

    # worktrees d'agents, dossier exclu DANS un dépôt imbriqué
    for name in ("agent-a", "agent-b"):
        wt = root / ".claude" / "worktrees" / name
        _git_file(wt)
        _write(wt / "x.md", _MARK)
        _write(wt / "node_modules" / "n.md", _MARK)
        _git_dir(wt / "grimoire-kit")
        _write(wt / "grimoire-kit" / "k.md", _MARK)
    _write(root / ".claude" / "worktrees" / "plain" / "w.md", _MARK)  # sans `.git` : reste lu

    # arbres partagés : fichier marqué, fichier du projet
    _write(root / ".claude" / "agents" / "own.md", "à moi\n")
    _write(root / ".codex" / "c.md", _MARK)
    _git_dir(root / ".codex" / "clone")
    _write(root / ".codex" / "clone" / "c2.md", _MARK)
    _write(root / ".cursor" / "rules" / "r.md", _MARK)
    _write(root / ".gemini" / "g.md", _MARK)
    return root


def _with_links(root: Path) -> Path:
    outside = root.parent / (root.name + "-outside")
    _write(outside / "real.md", _MARK)
    _git_dir(outside / "sub")
    _write(outside / "sub" / "s.md", _MARK)
    _write(outside / "plain" / "p.md", _MARK)
    _symlink(root / ".gemini" / "linked", outside, is_dir=True)  # lien de dossier dans l'arbre
    _symlink(root / ".cursor" / "link.md", outside / "real.md")  # lien de fichier
    _symlink(root / ".cursor" / "broken.md", outside / "absent.md")  # lien cassé
    _symlink(root / ".codex" / "gitlink" / ".git", outside / "absent")  # `.git` lien cassé
    _write(root / ".codex" / "gitlink" / "gl.md", _MARK)
    return root


def _symlinked_tree(root: Path) -> Path:
    """Un arbre partagé qui est lui-même un lien vers un dossier portant un dépôt."""
    real = root.parent / (root.name + "-real")
    _write(real / "r.md", _MARK)
    _git_dir(real / "sub")
    _write(real / "sub" / "s.md", _MARK)
    _write(root / "project-context.yaml")
    _symlink(root / ".claude", real, is_dir=True)
    return root


def _symlinked_parent(root: Path) -> Path:
    """Un arbre livré sous un parent qui est un lien ; le parent porte un `.git`."""
    real = root.parent / (root.name + "-real")
    _git_dir(real)
    _write(real / "kit" / "a.md")
    _write(root / "project-context.yaml")
    _symlink(root / "_grimoire", real, is_dir=True)
    return root


def _nested_tree_root(root: Path) -> Path:
    """Un arbre livré ou partagé qui est lui-même un dépôt imbriqué."""
    _write(root / "project-context.yaml")
    _git_dir(root / ".github")
    _write(root / ".github" / "copilot-instructions.md")
    _write(root / ".github" / "prompts" / "p.md")
    _git_file(root / "_grimoire" / "kit")
    _write(root / "_grimoire" / "kit" / "k.md")
    _git_dir(root / "_grimoire")
    _write(root / "_grimoire" / "_memory" / "m.md")
    _git_dir(root / ".cursor")
    _write(root / ".cursor" / "c.md", _MARK)
    return root


def _empty_project(root: Path) -> Path:
    return root


def _file_where_a_tree_is(root: Path) -> Path:
    _write(root / ".claude", "pas un dossier\n")
    _write(root / "_grimoire" / "kit", "pas un dossier\n")
    return root


_BUILDERS: dict[str, Any] = {
    "busy": _busy_project,
    "busy+links": lambda root: _with_links(_busy_project(root)),
    "symlinked-tree": _symlinked_tree,
    "symlinked-parent": _symlinked_parent,
    "nested-tree-root": _nested_tree_root,
    "empty": _empty_project,
    "file-instead-of-tree": _file_where_a_tree_is,
}


@pytest.mark.parametrize("builder", sorted(_BUILDERS))
def test_the_kept_files_are_strictly_those_the_old_scan_kept(tmp_path: Path, builder: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _BUILDERS[builder](root)

    assert _readable_files(root) == _reference_readable_files(root)


def test_the_busy_project_exercises_every_branch(tmp_path: Path) -> None:
    """La comparaison ne vaut que si le projet de test garde et écarte vraiment des fichiers."""
    root = tmp_path / "project"
    root.mkdir()
    _busy_project(root)

    kept = {p.relative_to(root).as_posix() for p in _readable_files(root)}

    assert {
        "project-context.yaml",
        ".github/copilot-instructions.md",
        ".github/prompts/p.md",
        "_grimoire/kit/agents/a.md",
        "_grimoire/kit/deep/a/keep.md",
        "_grimoire/_memory/m.md",
        ".claude/agents/ok.md",
        ".claude/worktrees/plain/w.md",
        ".codex/c.md",
    } <= kept
    for excluded in (
        "_grimoire/kit/vendor/v.md",  # dépôt imbriqué
        "_grimoire/kit/deep/a/b/c/d.md",  # dépôt imbriqué, `.git` fichier, plus bas
        "_grimoire/_memory/sub/inner/i.md",  # dépôt dans un dépôt
        "_grimoire/kit/_archived/old.md",  # dossier exclu
        ".claude/node_modules/z.md",  # dossier exclu
        ".claude/node_modules/pkg/q.md",  # dépôt dans un dossier exclu
        ".claude/worktrees/agent-a/x.md",  # worktree d'agent
        ".claude/worktrees/agent-a/node_modules/n.md",  # dossier exclu dans un dépôt imbriqué
        ".claude/worktrees/agent-b/grimoire-kit/k.md",
        ".claude/agents/own.md",  # arbre partagé, sans marqueur
        ".codex/clone/c2.md",
    ):
        assert excluded not in kept


def test_a_nested_repository_behind_a_symlink_is_not_seen_as_before(tmp_path: Path) -> None:
    """Une recherche récursive ne traverse pas les liens : le filtre ne doit pas non plus."""
    root = tmp_path / "project"
    root.mkdir()
    _symlinked_tree(root)

    kept = {p.relative_to(root).as_posix() for p in _readable_files(root)}

    assert ".claude/sub/s.md" in kept  # ce que l'ancien parcours gardait, à l'identique
    assert kept == {p.relative_to(root).as_posix() for p in _reference_readable_files(root)}


# ── Coût : par comptage d'appels système, jamais par durée ──────────────────


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


def _count_syscalls(monkeypatch: pytest.MonkeyPatch, action: Any) -> int:
    counter = _Counter()
    real_scandir, real_stat, real_lstat = os.scandir, os.stat, os.lstat

    def scandir(*args: Any, **kwargs: Any) -> Any:
        counter.calls += 1
        return real_scandir(*args, **kwargs)

    def stat(*args: Any, **kwargs: Any) -> Any:
        counter.calls += 1
        return real_stat(*args, **kwargs)

    def lstat(*args: Any, **kwargs: Any) -> Any:
        counter.calls += 1
        return real_lstat(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", scandir)
        patch.setattr(os, "stat", stat)
        patch.setattr(os, "lstat", lstat)
        action()
    return counter.calls


def _project_with_worktrees(root: Path, count: int) -> Path:
    root.mkdir()
    _write(root / "project-context.yaml")
    _write(root / "_grimoire" / "kit" / "agents" / "a.md")
    _write(root / ".claude" / "agents" / "ok.md", _MARK)
    for index in range(count):
        wt = root / ".claude" / "worktrees" / f"agent-{index:04d}"
        _git_file(wt)
        for sub in ("a", "b"):
            _write(wt / sub / "deep" / "f.md", _MARK)
        _git_dir(wt / "grimoire-kit")
        _write(wt / "grimoire-kit" / "k.md", _MARK)
    return root


def test_the_cost_grows_linearly_with_the_number_of_nested_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = _project_with_worktrees(tmp_path / "small", 10)
    large = _project_with_worktrees(tmp_path / "large", 200)

    calls_small = _count_syscalls(monkeypatch, lambda: _readable_files(small))
    calls_large = _count_syscalls(monkeypatch, lambda: _readable_files(large))

    # 20x plus de dépôts : au plus 20x plus d'appels (l'ancien parcours en faisait 400x).
    assert calls_large <= 20 * calls_small
    # et un nombre d'appels par dépôt borné, sans rapport avec le nombre de fichiers dessous
    assert (calls_large - calls_small) / 190 <= 4


# ── Un seul parcours par appel à `integrity_checks`, rien qui survive ───────


def test_integrity_checks_lists_the_project_once_for_both_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _busy_project(tmp_path / "project")
    real = integrity._readable_files
    scans: list[Path] = []

    def spy(project_root: Path) -> list[Path]:
        scans.append(project_root)
        return real(project_root)

    monkeypatch.setattr(integrity, "_readable_files", spy)

    integrity_checks(root)

    assert scans == [root]


def test_explicit_files_replace_the_scan_in_both_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    cited = _write(
        root / "_grimoire" / "kit" / "agents" / "a.md",
        'voir _grimoire/absent.md <agent tag="ghost">\n',
    )
    _write(root / "_grimoire" / "kit" / "agents" / "real.md")

    def refuse(project_root: Path) -> list[Path]:
        raise AssertionError("aucun parcours attendu quand les fichiers sont fournis")

    monkeypatch.setattr(integrity, "_readable_files", refuse)
    monkeypatch.setattr(integrity, "installed_agent_tags", lambda project_root: {"real"})

    assert [ref.target for ref in dead_path_references(root, [cited])] == ["_grimoire/absent.md"]
    assert dead_path_references(root, []) == []
    assert roster_incoherences(root, [cited]).routed_but_absent == ["ghost"]
    assert roster_incoherences(root, []).maps_found == 0


def test_nothing_is_kept_between_two_calls(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write(root / "_grimoire" / "kit" / "a.md")
    assert [p.name for p in _readable_files(root)] == ["a.md"]

    _git_dir(root / "_grimoire" / "kit")  # le dossier devient un dépôt imbriqué
    assert _readable_files(root) == []

    (root / "_grimoire" / "kit" / ".git").rmdir()
    assert [p.name for p in _readable_files(root)] == ["a.md"]
