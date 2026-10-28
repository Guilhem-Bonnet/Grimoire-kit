"""``changelog-assemble.py`` — les fragments de ``changelog.d/`` rejoignent la section de la release."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSEMBLE = _load("changelog-assemble")
CHECK = _load("check-changelog-release")
HEADER = "# Changelog\n\n"


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        HEADER + "## [Unreleased]\n\n- ancienne entrée\n\n## [1.0.0] - 2026-01-01\n\n- départ\n", encoding="utf-8"
    )
    (tmp_path / "changelog.d" / "README.md").write_text("- pas un fragment\n", encoding="utf-8")
    (tmp_path / "changelog.d" / "a.md").write_text("- feat: a\n", encoding="utf-8")
    (tmp_path / "changelog.d" / "b.md").write_text("- fix: b\n", encoding="utf-8")
    return tmp_path


def test_fragments_land_under_the_version_and_are_removed(kit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ASSEMBLE.main(["1.1.0", "--date", "2026-02-02", "--repo", str(kit)]) == 0
    text = (kit / "CHANGELOG.md").read_text(encoding="utf-8")
    section = text.split("## [1.1.0] - 2026-02-02")[1].split("## [1.0.0]")[0]
    assert "- ancienne entrée" in section and "- feat: a" in section and "- fix: b" in section
    assert text.index("[Unreleased]") < text.index("[1.1.0]") < text.index("[1.0.0]")
    assert sorted(p.name for p in (kit / "changelog.d").iterdir()) == ["README.md"]

    (kit / "version.txt").write_text("1.1.0\n", encoding="utf-8")
    monkeypatch.setattr(CHECK, "REPO", kit)
    monkeypatch.setattr(CHECK, "CHANGELOG", kit / "CHANGELOG.md")
    monkeypatch.setattr(CHECK, "VERSION_FILE", kit / "version.txt")
    assert CHECK.main([]) == 0


def test_release_guard_refuses_an_unassembled_fragment(kit: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (kit / "CHANGELOG.md").write_text(HEADER + "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n- départ\n", encoding="utf-8")
    (kit / "version.txt").write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(CHECK, "REPO", kit)
    monkeypatch.setattr(CHECK, "CHANGELOG", kit / "CHANGELOG.md")
    monkeypatch.setattr(CHECK, "VERSION_FILE", kit / "version.txt")
    assert CHECK.main([]) == 1
    assert "fragment" in capsys.readouterr().err


def test_nothing_to_assemble_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(HEADER + "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n", encoding="utf-8")
    assert ASSEMBLE.main(["1.1.0", "--repo", str(tmp_path)]) == 1
