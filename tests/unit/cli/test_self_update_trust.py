"""``grimoire self update`` n'installe qu'une wheel dont l'empreinte est confirmée par deux sources.

Constat (D-03) : la commande déléguait à ``uv tool upgrade`` / ``pipx upgrade`` /
``pip install --upgrade``, qui retéléchargent tout et ne confrontent le fichier
à aucune empreinte indépendante. Une wheel altérée dans le CDN ou dans un miroir
était installée sans que le kit le sache.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

from ._fake_release import FakeRelease, sha

runner = CliRunner()


def _update(net: FakeRelease, *, installed: str = "3.0.0", extras: list[str] | None = None):
    ok = MagicMock(returncode=0)
    seen: dict[str, object] = {}

    def run(cmd, **_kw):
        spec = cmd[-1]
        seen["cmd"] = cmd
        # le fichier à installer existe au moment de l'appel (et seulement alors)
        seen["exists"] = Path(spec.split(" @ file://", 1)[1]).is_file() if " @ file://" in spec else None
        return ok

    with patch("grimoire.cli.app.is_online", return_value=True), \
         patch("grimoire.cli.app.__version__", installed), \
         patch("urllib.request.urlopen", net), \
         patch("shutil.which", return_value=None), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=extras or []), \
         patch("subprocess.run", side_effect=run) as mock_run:
        result = runner.invoke(app, ["self", "update"])
    return result, mock_run, seen


def test_verified_wheel_is_installed_from_the_verified_file() -> None:
    net = FakeRelease()
    result, mock_run, seen = _update(net)
    assert result.exit_code == 0, result.output
    assert "trust_level: verified" in result.output
    assert mock_run.call_count == 1
    cmd = seen["cmd"]
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert "grimoire-kit @ file://" in cmd[-1] and cmd[-1].endswith(net.filename)
    assert seen["exists"] is True


def test_extras_already_present_are_kept() -> None:
    result, _, seen = _update(FakeRelease(), extras=["mcp"])
    assert result.exit_code == 0, result.output
    assert str(seen["cmd"][-1]).startswith("grimoire-kit[mcp] @ file://")


def test_a_wheel_altered_after_publication_is_refused_before_any_install() -> None:
    net = FakeRelease(served_wheel=b"tampered")
    result, mock_run, _ = _update(net)
    assert result.exit_code == 1
    assert "Update refused" in result.output and "PyPI" in result.output
    assert "Nothing was installed" in result.output
    assert mock_run.call_count == 0


def test_a_wheel_matching_pypi_but_not_the_release_sums_is_refused() -> None:
    # PyPI et le fichier s'accordent (miroir cohérent), la release dit autre chose.
    net = FakeRelease(sums=f"{sha(b'other')}  grimoire_kit-99.0.0-py3-none-any.whl\n")
    result, mock_run, _ = _update(net)
    assert result.exit_code == 1
    assert "SHA256SUMS" in result.output
    assert mock_run.call_count == 0


def test_missing_sums_are_a_refusal_when_the_release_must_publish_them() -> None:
    result, mock_run, _ = _update(FakeRelease(sums=None))
    assert result.exit_code == 1
    assert "devrait publier SHA256SUMS" in result.output
    assert mock_run.call_count == 0


def test_unreachable_sums_are_a_refusal_not_a_downgrade_of_the_check() -> None:
    result, mock_run, _ = _update(FakeRelease(sums=503))
    assert result.exit_code == 1
    assert "injoignable" in result.output
    assert mock_run.call_count == 0


def test_a_release_older_than_the_sums_installs_with_the_weaker_level_stated() -> None:
    net = FakeRelease(version="3.62.5", sums=None)
    result, mock_run, _ = _update(net, installed="3.60.0")
    assert result.exit_code == 0, result.output
    assert "trust_level: pypi-only" in result.output
    assert mock_run.call_count == 1


def test_pypi_without_a_digest_is_a_refusal() -> None:
    result, mock_run, _ = _update(FakeRelease(pypi_sha=None))
    assert result.exit_code == 1
    assert mock_run.call_count == 0


@pytest.mark.parametrize("manager", ["uv", "pipx"])
def test_the_install_goes_through_the_manager_that_owns_the_environment(manager: str) -> None:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == manager else None

    listing = MagicMock(stdout="grimoire-kit 3.0.0\n")
    ok = MagicMock(returncode=0)
    calls: list[list[str]] = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        return listing if "list" in cmd else ok

    with patch("grimoire.cli.app.is_online", return_value=True), \
         patch("urllib.request.urlopen", FakeRelease()), \
         patch("shutil.which", side_effect=which), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=[]), \
         patch("subprocess.run", side_effect=run):
        result = runner.invoke(app, ["self", "update"])
    assert result.exit_code == 0, result.output
    install = calls[-1]
    assert install[0] == f"/usr/bin/{manager}" and "install" in install and "--force" in install
    assert " @ file://" in install[-1]
