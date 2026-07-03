"""Smoke tests for the deprecated legacy doc-fetcher entrypoint."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "doc-fetcher.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("doc_fetcher_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def legacy_module():
    return _load_module()


def test_docstring_marks_legacy_status(legacy_module) -> None:
    doc = legacy_module.__doc__ or ""
    assert "deprecated" in doc.lower()
    assert "docs-fetcher.py" in doc


def test_cli_help_exposes_legacy_commands() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    output = result.stdout.lower()
    for command in ("fetch", "list", "search", "remove", "refresh"):
        assert command in output


def test_validate_url_keeps_ssrf_guard(legacy_module) -> None:
    assert legacy_module._validate_url("https://docs.python.org/3/") == "https://docs.python.org/3/"
    with pytest.raises(ValueError):
        legacy_module._validate_url("http://127.0.0.1/private")


def test_doc_index_roundtrip(tmp_path: Path, legacy_module) -> None:
    index = legacy_module.DocIndex()
    source = legacy_module.DocSource(
        name="python",
        base_url="https://docs.python.org/3/",
        paths=["library/pathlib.html"],
        last_refresh="2026-01-01",
        total_chunks=2,
    )
    source.pages.append(
        legacy_module.DocPage(
            url="https://docs.python.org/3/library/pathlib.html",
            title="pathlib",
            text="",
            hash="abc123",
            fetched_at="2026-01-01",
            size=128,
        )
    )
    index.sources[source.name] = source

    legacy_module.save_doc_index(tmp_path, index)
    loaded = legacy_module.load_doc_index(tmp_path)

    assert set(loaded.sources) == {"python"}
    loaded_source = loaded.sources["python"]
    assert loaded_source.base_url == source.base_url
    assert loaded_source.paths == source.paths
    assert len(loaded_source.pages) == 1
