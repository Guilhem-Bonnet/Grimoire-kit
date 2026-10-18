"""Reçu d'installation ``uv`` / ``pipx`` : options rejouées avant, chemin retiré après (D-03, revue S1)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from grimoire.tools import install_receipt as receipt


def _receipt(*requirements: str, extra: str = "") -> str:
    body = ",\n".join(f"    {r}" for r in requirements)
    return f"[tool]\nrequirements = [\n{body},\n]\n{extra}entrypoints = [\n    {{ name = \"grimoire\", install-path = \"/b/grimoire\", from = \"grimoire-kit\" }},\n]\n"


def _kit(path: Path) -> str:
    return f'{{ name = "grimoire-kit", path = {json.dumps(str(path))} }}'


# ── uv : options à rejouer ────────────────────────────────────────────────────


def test_no_receipt_means_nothing_to_carry(tmp_path: Path) -> None:
    assert receipt.uv_carried_options(tmp_path / "absent.toml") == []


def test_the_with_requirements_and_python_of_the_original_install_are_replayed(tmp_path: Path) -> None:
    """D03-S1-02 : ``uv tool install --force`` seul les perdait (``uv tool upgrade`` les gardait)."""
    file = tmp_path / "uv-receipt.toml"
    file.write_text(
        _receipt(
            _kit(tmp_path / "old.whl"),
            '{ name = "iniconfig" }',
            '{ name = "rich", specifier = ">=13,<15" }',
            '{ name = "httpx", extras = ["http2", "brotli"], specifier = ">=0.27", marker = "python_version >= \'3.12\'" }',
            extra='python = "3.12"\n',
        ),
        encoding="utf-8",
    )
    assert receipt.uv_carried_options(file) == [
        "--python=3.12",
        "--with=iniconfig",
        "--with=rich>=13,<15",
        "--with=httpx[http2,brotli]>=0.27; python_version >= '3.12'",
    ]


@pytest.mark.parametrize(("extra", "needle"), [
    ('constraints = [{ name = "iniconfig", specifier = ">=2" }]\n', "constraints"),
    ('overrides = [{ name = "iniconfig", specifier = "==2.0" }]\n', "overrides"),
    ('build-constraint-dependencies = [{ name = "setuptools" }]\n', "build-constraint-dependencies"),
    ('options = { exclude-newer = "2026-01-01T00:00:00Z" }\n', "options"),
])
def test_options_that_cannot_be_replayed_refuse_instead_of_being_lost(tmp_path: Path, extra: str, needle: str) -> None:
    file = tmp_path / "uv-receipt.toml"
    file.write_text(_receipt(_kit(tmp_path / "old.whl"), extra=extra), encoding="utf-8")
    with pytest.raises(receipt.UnsupportedReceiptError, match=needle):
        receipt.uv_carried_options(file)


@pytest.mark.parametrize("requirement", [
    '{ name = "mylib", path = "/src/mylib" }',
    '{ name = "mylib", editable = "/src/mylib" }',
    '{ name = "mylib", index = "https://example.org/simple" }',
    '{ name = "mylib", git = "https://example.org/mylib.git" }',
])
def test_a_with_requirement_that_is_not_a_plain_spec_refuses(tmp_path: Path, requirement: str) -> None:
    file = tmp_path / "uv-receipt.toml"
    file.write_text(_receipt(_kit(tmp_path / "old.whl"), requirement), encoding="utf-8")
    with pytest.raises(receipt.UnsupportedReceiptError, match="mylib"):
        receipt.uv_carried_options(file)


def test_an_unreadable_receipt_refuses(tmp_path: Path) -> None:
    file = tmp_path / "uv-receipt.toml"
    file.write_text("[tool\nbroken", encoding="utf-8")
    with pytest.raises(receipt.UnsupportedReceiptError, match="illisible"):
        receipt.uv_carried_options(file)


# ── uv : retirer le chemin de la wheel temporaire ─────────────────────────────


def _wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / "tmp" / "grimoire_kit-9.9.9-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_bytes(b"w")
    return wheel


def test_the_receipt_no_longer_points_at_the_temporary_wheel(tmp_path: Path) -> None:
    """D03-S1-03 : sans cela, ``uv tool upgrade`` échoue une fois le dossier temporaire supprimé."""
    wheel = _wheel(tmp_path)
    file = tmp_path / "uv-receipt.toml"
    file.write_text(_receipt(_kit(wheel), '{ name = "iniconfig" }', extra='python = "3.12"\n'), encoding="utf-8")
    before = tomllib.loads(file.read_text(encoding="utf-8"))
    assert receipt.pin_uv_receipt(file, wheel) is None
    after = tomllib.loads(file.read_text(encoding="utf-8"))
    assert after["tool"]["requirements"] == [{"name": "grimoire-kit"}, {"name": "iniconfig"}]
    # le reste du reçu est intact
    assert after["tool"]["python"] == before["tool"]["python"] == "3.12"
    assert after["tool"]["entrypoints"] == before["tool"]["entrypoints"]
    assert str(wheel) not in file.read_text(encoding="utf-8")


def test_extras_survive_the_pin_whatever_the_key_order(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    for entry in (
        f'{{ name = "grimoire-kit", extras = ["mcp"], path = {json.dumps(str(wheel))} }}',
        f'{{ path = {json.dumps(str(wheel))}, name = "grimoire-kit", extras = ["mcp"] }}',
    ):
        file = tmp_path / "uv-receipt.toml"
        file.write_text(_receipt(entry), encoding="utf-8")
        assert receipt.pin_uv_receipt(file, wheel) is None
        assert tomllib.loads(file.read_text(encoding="utf-8"))["tool"]["requirements"] == [
            {"name": "grimoire-kit", "extras": ["mcp"]},
        ]


def test_a_registry_receipt_is_left_alone(tmp_path: Path) -> None:
    file = tmp_path / "uv-receipt.toml"
    text = _receipt('{ name = "grimoire-kit" }')
    file.write_text(text, encoding="utf-8")
    assert receipt.pin_uv_receipt(file, _wheel(tmp_path)) is None
    assert file.read_text(encoding="utf-8") == text
    assert receipt.pin_uv_receipt(tmp_path / "absent.toml", _wheel(tmp_path)) is None


def test_a_receipt_pointing_elsewhere_is_not_touched(tmp_path: Path) -> None:
    """Le chemin d'un autre fichier n'est pas le nôtre : on ne réécrit pas ce qu'on n'a pas posé."""
    file = tmp_path / "uv-receipt.toml"
    text = _receipt(_kit(tmp_path / "somewhere" / "else.whl"))
    file.write_text(text, encoding="utf-8")
    reason = receipt.pin_uv_receipt(file, _wheel(tmp_path))
    assert reason is not None and "autre fichier" in reason
    assert file.read_text(encoding="utf-8") == text


def test_a_receipt_shape_that_is_not_recognised_is_left_untouched(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    file = tmp_path / "uv-receipt.toml"
    # le chemin apparaît deux fois (le reçu d'un format futur) : aucune réécriture à l'aveugle
    text = _receipt(_kit(wheel), f'{{ name = "other", path = {json.dumps(str(wheel))} }}')
    file.write_text(text, encoding="utf-8")
    reason = receipt.pin_uv_receipt(file, wheel)
    assert reason is not None and "non reconnue" in reason
    assert file.read_text(encoding="utf-8") == text


# ── pipx ──────────────────────────────────────────────────────────────────────


def _pipx(tmp_path: Path, spec: str) -> Path:
    file = tmp_path / "pipx_metadata.json"
    file.write_text(json.dumps({
        "main_package": {"package": "grimoire-kit", "package_or_url": spec, "pip_args": []},
        "injected_packages": {"iniconfig": {"package_or_url": "iniconfig"}},
        "venv_args": [],
    }, indent=4), encoding="utf-8")
    return file


def test_pipx_forgets_the_temporary_wheel_and_keeps_the_extras_and_injected_packages(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    file = _pipx(tmp_path, f"grimoire-kit[mcp]@ {wheel.as_uri()}")
    assert receipt.pin_pipx_metadata(file, wheel) is None
    data = json.loads(file.read_text(encoding="utf-8"))
    assert data["main_package"]["package_or_url"] == "grimoire-kit[mcp]"
    assert data["main_package"]["pip_args"] == [] and "iniconfig" in data["injected_packages"]


def test_pipx_registry_or_foreign_metadata_is_not_rewritten(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    file = _pipx(tmp_path, "grimoire-kit")
    text = file.read_text(encoding="utf-8")
    assert receipt.pin_pipx_metadata(file, wheel) is None and file.read_text(encoding="utf-8") == text
    other = _pipx(tmp_path, "grimoire-kit @ file:///elsewhere/grimoire_kit-1-py3-none-any.whl")
    reason = receipt.pin_pipx_metadata(other, wheel)
    assert reason is not None and "autre fichier" in reason
    assert receipt.pin_pipx_metadata(tmp_path / "absent.json", wheel) is None


# ── Avec le vrai uv (sans réseau : wheel factice sans dépendance) ─────────────


def _dummy_wheel(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    info = "grimoire_kit-0.0.1.dist-info"
    files = {
        "grimoire/__init__.py": b"def main():\n    return 0\n",
        f"{info}/METADATA": b"Metadata-Version: 2.1\nName: grimoire-kit\nVersion: 0.0.1\n",
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: t\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/entry_points.txt": b"[console_scripts]\ngrimoire = grimoire:main\n",
    }

    def digest(data: bytes) -> str:
        return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()

    record = "".join(f"{k},{digest(v)},{len(v)}\n" for k, v in files.items()) + f"{info}/RECORD,,\n"
    wheel = directory / "grimoire_kit-0.0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{info}/RECORD", record)
    return wheel


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv absent")
def test_with_the_real_uv_the_installed_record_matches_the_wheel_and_the_receipt_survives_the_cleanup(
    tmp_path: Path,
) -> None:
    """D03-S1-01 et S1-03 de bout en bout : vrai ``uv tool install``, vrai reçu, vrai ``RECORD``."""
    from importlib import metadata

    from grimoire.tools import install_trust as trust

    uv = shutil.which("uv") or "uv"
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_PYTHON_DOWNLOADS": "never",
    }
    scratch = tmp_path / "scratch"
    wheel = _dummy_wheel(scratch)

    def uv_run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [uv, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        )

    installed = uv_run(
        "tool", "install", "--offline", "--no-managed-python", "--python", sys.executable,
        "--force", f"grimoire-kit @ {wheel.as_uri()}",
    )
    if installed.returncode != 0:
        pytest.skip(f"uv ne sait pas installer hors ligne ici : {installed.stderr[-200:]}")

    tool = tmp_path / "tools" / "grimoire-kit"
    info = next(tool.glob("lib/python*/site-packages/grimoire_kit-0.0.1.dist-info"))
    dist = metadata.PathDistribution(info)
    # S1-01 : uv_cache.json est dans le RECORD installé et pas dans la wheel
    assert "uv_cache.json" in (info / "RECORD").read_text(encoding="utf-8")
    assert trust.same_as_wheel(wheel, dist) == ()

    # S1-03 : le dossier temporaire disparaît ; le reçu ne doit plus en dépendre
    receipt_file = receipt.uv_receipt_path(tmp_path / "tools")
    assert str(wheel) in receipt_file.read_text(encoding="utf-8")
    scratch.rename(tmp_path / "gone")
    broken = uv_run("tool", "upgrade", "--offline", "grimoire-kit")
    assert "Distribution not found" in broken.stderr + broken.stdout  # le défaut, tel que l'outil le rend
    assert receipt.pin_uv_receipt(receipt_file, wheel) is None
    assert tomllib.loads(receipt_file.read_text(encoding="utf-8"))["tool"]["requirements"] == [{"name": "grimoire-kit"}]
    upgrade = uv_run("tool", "upgrade", "--offline", "grimoire-kit")
    assert "Distribution not found" not in upgrade.stderr + upgrade.stdout
