"""Anti-drift guard: every version surface must agree with __version__.py.

`version.txt` is consumed by the shell entrypoints (grimoire.sh,
grimoire-init.sh, tests/smoke-test.sh); `make release` bumps both files.
Releases that bypass `make release` historically left version.txt stale
(stuck at 3.4.2 while the SDK shipped 3.17.0), so this test fails closed
on any divergence.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.__version__ import __version__

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_txt_matches_package_version() -> None:
    version_file = _REPO_ROOT / "version.txt"
    assert version_file.is_file(), "version.txt missing at repo root"
    assert version_file.read_text().strip() == __version__


def test_version_is_semver_like() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
