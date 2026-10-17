"""Anti-drift guard: every version surface must agree with __version__.py.

`version.txt` is consumed by the shell entrypoints (grimoire.sh,
grimoire-init.sh, tests/smoke-test.sh); `make release` bumps both files.
Releases that bypass `make release` historically left version.txt stale
(stuck at 3.4.2 while the SDK shipped 3.17.0), so this test fails closed
on any divergence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from grimoire.__version__ import __version__

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_release_version(version: str) -> bool:
    """La graphie de version du garde de tag (``scripts/check-release-tag.py``), pas une copie.

    Une version que ce test refuse ne peut jamais être taguée ; une version que
    le garde refuse ne doit pas passer ici non plus. Une pré-version
    (``3.63.0-rc1``) est valide dans les deux.
    """
    spec = importlib.util.spec_from_file_location("check_release_tag", _REPO_ROOT / "scripts" / "check-release-tag.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bool(module.is_release_version(version))


def test_version_txt_matches_package_version() -> None:
    version_file = _REPO_ROOT / "version.txt"
    assert version_file.is_file(), "version.txt missing at repo root"
    assert version_file.read_text(encoding="utf-8").strip() == __version__


def test_version_is_semver_like() -> None:
    assert _is_release_version(__version__), (
        f"{__version__!r} n'a pas la forme X.Y.Z ou X.Y.Z-<suffixe> que scripts/check-release-tag.py laisse partir"
    )
