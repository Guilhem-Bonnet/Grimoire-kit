"""The web data resolver and the generator must agree on what is per-project."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _generator_layers() -> set[str]:
    src = (ROOT / "scripts" / "gen-site-data.py").read_text(encoding="utf-8")
    body = src.split("def build_project(")[1].split("\ndef ")[0]
    return set(re.findall(r'_write\(out_dir,\s*"([^"]+\.json)"', body))


def _resolver_layers() -> set[str]:
    src = (ROOT / "web" / "atelier-nav.js").read_text(encoding="utf-8")
    block = src.split("const PROJECT_SCOPED = new Set([")[1].split("]);")[0]
    return set(re.findall(r"'([^']+\.json)'", block))


def test_project_scoped_layers_match_the_generator() -> None:
    """A drift here serves one project's data under another project's name."""
    assert _resolver_layers() == _generator_layers()


def test_read_version_tolerates_a_non_kit_project(tmp_path: Path) -> None:
    """A governed project is not grimoire-kit: no __version__.py, no version.txt."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gsd", ROOT / "scripts" / "gen-site-data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._read_version(tmp_path) == "unknown"
    assert mod._list_archetypes(tmp_path) == []
    assert mod._count_tools(tmp_path) == 0

    (tmp_path / "version.txt").write_text("9.9.9\n", encoding="utf-8")
    assert mod._read_version(tmp_path) == "9.9.9"
