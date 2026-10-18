"""``grimoire self update`` n'installe qu'une wheel dont l'empreinte est confirmée par deux sources.

Constat (D-03) : la commande déléguait à ``uv tool upgrade`` / ``pipx upgrade`` /
``pip install --upgrade``, qui retéléchargent tout et ne confrontent le fichier
à aucune empreinte indépendante. Une wheel altérée dans le CDN ou dans un miroir
était installée sans que le kit le sache.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse
from urllib.request import url2pathname

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
        seen["exists"] = _wheel_path(spec).is_file() if " @ file:" in spec else None
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


# ── Ce que l'outil retient de l'installation (revue D-03, S1-02 et S1-03) ─────


def _managed_update(
    manager: str, state_dir: Path, *, on_install=None, installed: str = "3.0.0",
):
    """``self update`` sous ``uv`` ou ``pipx`` : ``state_dir`` est le dossier que l'outil annonce.

    ``on_install(cmd)`` joue ce que fait l'outil à l'installation (écrire son reçu).
    """
    calls: list[list[str]] = []

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == manager else None

    def run(cmd, **_kw):
        calls.append(list(cmd))
        if "list" in cmd:
            return MagicMock(stdout="grimoire-kit 3.0.0\n", returncode=0)
        if cmd[1:3] == ["tool", "dir"] or "environment" in cmd:
            return MagicMock(stdout=f"{state_dir}\n", returncode=0)
        if on_install is not None:
            on_install(cmd)
        return MagicMock(returncode=0)

    with patch("grimoire.cli.app.is_online", return_value=True), \
         patch("grimoire.cli.app.__version__", installed), \
         patch("urllib.request.urlopen", FakeRelease()), \
         patch("shutil.which", side_effect=which), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=["mcp"]), \
         patch("subprocess.run", side_effect=run):
        result = runner.invoke(app, ["self", "update"])
    return result, calls


def _wheel_path(spec: str) -> Path:
    """Le fichier désigné par ``<nom> @ file:...`` (``/C:/x`` sous Windows n'est pas un chemin)."""
    return Path(url2pathname(urlparse(spec.split(" @ ", 1)[1]).path))


def _wheel_of(cmd: list[str]) -> str:
    return str(_wheel_path(cmd[-1]))


def _uv_state(tmp_path: Path, receipt_text: str) -> Path:
    tools = tmp_path / "tools"
    (tools / "grimoire-kit").mkdir(parents=True)
    (tools / "grimoire-kit" / "uv-receipt.toml").write_text(receipt_text, encoding="utf-8")
    return tools


_UV_RECEIPT = (
    '[tool]\nrequirements = [\n    { name = "grimoire-kit", path = "/old/grimoire_kit-3.0.0-py3-none-any.whl" },\n'
    '    { name = "iniconfig" },\n]\npython = "3.12"\nentrypoints = []\n'
)


def test_uv_replays_the_with_packages_and_python_of_the_original_install(tmp_path: Path) -> None:
    """D03-S1-02 : ``uv tool install --force`` seul perdait les ``--with`` (``uv tool upgrade`` les gardait)."""
    tools = _uv_state(tmp_path, _UV_RECEIPT)
    result, calls = _managed_update("uv", tools)
    assert result.exit_code == 0, result.output
    install = calls[-1]
    assert install[:4] == ["/usr/bin/uv", "tool", "install", "--force"]
    assert "--with=iniconfig" in install and "--python=3.12" in install
    assert install[-1].startswith("grimoire-kit[mcp] @ file://")  # le spécifieur reste en dernier


def test_uv_refuses_an_install_it_cannot_reproduce_instead_of_dropping_it(tmp_path: Path) -> None:
    tools = _uv_state(tmp_path, _UV_RECEIPT.replace("entrypoints = []", 'constraints = [{ name = "x", specifier = ">=1" }]\nentrypoints = []'))
    result, calls = _managed_update("uv", tools)
    assert result.exit_code == 1
    assert "Update refused" in result.output and "constraints" in result.output
    assert "Nothing was installed" in result.output
    assert not any("install" in c for c in calls), calls


def test_uv_receipt_is_released_from_the_temporary_wheel_after_the_install(tmp_path: Path) -> None:
    """D03-S1-03 : le reçu pointait vers le dossier temporaire supprimé, ``uv tool upgrade`` échouait."""
    import tomllib

    tools = _uv_state(tmp_path, _UV_RECEIPT)
    receipt_file = tools / "grimoire-kit" / "uv-receipt.toml"

    def uv_writes_its_receipt(cmd: list[str]) -> None:
        wheel = _wheel_of(cmd)
        receipt_file.write_text(
            '[tool]\nrequirements = [\n    { name = "grimoire-kit", extras = ["mcp"], path = ' + json.dumps(wheel) + ' },\n'
            '    { name = "iniconfig" },\n]\nentrypoints = []\n',
            encoding="utf-8",
        )

    result, _ = _managed_update("uv", tools, on_install=uv_writes_its_receipt)
    assert result.exit_code == 0, result.output
    requirements = tomllib.loads(receipt_file.read_text(encoding="utf-8"))["tool"]["requirements"]
    assert requirements == [{"name": "grimoire-kit", "extras": ["mcp"]}, {"name": "iniconfig"}]
    assert "still remembers" not in result.output


def test_a_receipt_that_cannot_be_released_says_the_command_that_repairs_it(tmp_path: Path) -> None:
    tools = _uv_state(tmp_path, _UV_RECEIPT)
    receipt_file = tools / "grimoire-kit" / "uv-receipt.toml"

    def uv_writes_a_shape_we_do_not_know(cmd: list[str]) -> None:
        wheel = _wheel_of(cmd)
        receipt_file.write_text(
            '[tool]\nrequirements = [\n    { name = "grimoire-kit", path = ' + json.dumps(wheel) + ' },\n'
            '    { name = "iniconfig", path = ' + json.dumps(wheel) + ' },\n]\n',
            encoding="utf-8",
        )

    # la même chaîne apparaît deux fois : aucune réécriture à l'aveugle
    result, _ = _managed_update("uv", tools, on_install=uv_writes_a_shape_we_do_not_know)
    assert result.exit_code == 0, result.output
    assert "still remembers a temporary file" in result.output
    assert "uv tool install --force --python=3.12 --with=iniconfig 'grimoire-kit[mcp]'" in " ".join(result.output.split())


def test_pipx_metadata_is_released_from_the_temporary_wheel_after_the_install(tmp_path: Path) -> None:
    venvs = tmp_path / "venvs"
    (venvs / "grimoire-kit").mkdir(parents=True)
    metadata_file = venvs / "grimoire-kit" / "pipx_metadata.json"

    def pipx_writes_its_metadata(cmd: list[str]) -> None:
        metadata_file.write_text(json.dumps({
            "main_package": {"package_or_url": f"grimoire-kit[mcp]@ {Path(_wheel_of(cmd)).as_uri()}"},
            "injected_packages": {"iniconfig": {}},
        }), encoding="utf-8")

    result, calls = _managed_update("pipx", venvs, on_install=pipx_writes_its_metadata)
    assert result.exit_code == 0, result.output
    data = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert data["main_package"]["package_or_url"] == "grimoire-kit[mcp]"
    assert "iniconfig" in data["injected_packages"]
    assert calls[-1][:3] == ["/usr/bin/pipx", "install", "--force"]
