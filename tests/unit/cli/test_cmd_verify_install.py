"""``grimoire verify-install`` et le ``trust_level`` de ``grimoire doctor`` (D-03)."""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from grimoire.cli.app import app

from ._fake_release import FakeGithub, FakeRelease

runner = CliRunner()


def _b64(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _install(root: Path, files: dict[str, bytes], version: str = "99.0.0") -> metadata.PathDistribution:
    info = root / f"grimoire_kit-{version}.dist-info"
    info.mkdir(parents=True)
    rows = []
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        rows.append(f"{rel},{_b64(data)},{len(data)}")
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: grimoire-kit\nVersion: {version}\n", encoding="utf-8")
    (info / "RECORD").write_text("\n".join([*rows, f"{info.name}/RECORD,,"]) + "\n", encoding="utf-8")
    return metadata.PathDistribution(info)


def _wheel_bytes(tmp_path: Path, files: dict[str, bytes]) -> bytes:
    path = tmp_path / "built.whl"
    rows = "".join(f"{rel},{_b64(data)},{len(data)}\n" for rel, data in files.items())
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
        zf.writestr("grimoire_kit-99.0.0.dist-info/RECORD", rows)
    return path.read_bytes()


def _run(dist, *args: str, net: FakeRelease | None = None):
    with patch("grimoire.tools.install_trust.installed_distribution", return_value=dist), \
         patch("urllib.request.urlopen", net or FakeRelease()):
        return runner.invoke(app, ["verify-install", *args])


def test_offline_intact_install_is_reported_with_its_limit(tmp_path: Path) -> None:
    result = _run(_install(tmp_path, {"grimoire/a.py": b"1"}), "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["trust_level"] == "intact" and "provenance non vérifiée" in data["detail"]


def test_a_modified_file_fails_the_command(tmp_path: Path) -> None:
    dist = _install(tmp_path, {"grimoire/a.py": b"1"})
    (tmp_path / "grimoire" / "a.py").write_bytes(b"backdoor")
    result = _run(dist)
    assert result.exit_code == 1
    assert "modified" in result.output and "grimoire/a.py" in result.output


def test_online_verified_when_installation_equals_the_published_wheel(tmp_path: Path) -> None:
    files = {"grimoire/a.py": b"1"}
    net = FakeRelease(wheel=_wheel_bytes(tmp_path, files))
    result = _run(_install(tmp_path / "site", files), "--online", "--json", net=net)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["trust_level"] == "verified"


def test_online_flags_an_installation_that_differs_from_the_published_wheel(tmp_path: Path) -> None:
    published = _wheel_bytes(tmp_path, {"grimoire/a.py": b"1"})
    net = FakeRelease(wheel=published)
    # RECORD cohérent avec les fichiers (rien de « modifié » en local), mais pas celui de la wheel publiée.
    dist = _install(tmp_path / "site", {"grimoire/a.py": b"1", "grimoire/extra.py": b"evil"})
    result = _run(dist, "--online", "--json", net=net)
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["trust_level"] == "modified"
    assert "ajouté à l'installation : grimoire/extra.py" in data["problems"]


def test_online_refuses_a_published_wheel_that_does_not_match_its_digest(tmp_path: Path) -> None:
    files = {"grimoire/a.py": b"1"}
    net = FakeRelease(wheel=_wheel_bytes(tmp_path, files), served_wheel=b"tampered")
    result = _run(_install(tmp_path / "site", files), "--online", net=net)
    assert result.exit_code == 1
    assert "refusée" in result.output


def test_online_verifies_an_installed_prerelease_against_its_github_release(tmp_path: Path) -> None:
    """Une pré-version n'est pas sur PyPI : sans cette voie, ``verify-install --online`` échouait sur un canal preview."""
    files = {"grimoire/a.py": b"1"}
    net = FakeGithub(pre_wheel=_wheel_bytes(tmp_path, files))
    dist = _install(tmp_path / "site", files, version="3.64.0rc1")
    result = _run(dist, "--online", "--json", net=net)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["trust_level"] == "release-only"
    assert not any("pypi.org" in url for url in net.requested)


def test_online_flags_a_prerelease_installation_that_differs_from_its_release(tmp_path: Path) -> None:
    net = FakeGithub(pre_wheel=_wheel_bytes(tmp_path, {"grimoire/a.py": b"1"}))
    dist = _install(tmp_path / "site", {"grimoire/a.py": b"1", "grimoire/extra.py": b"evil"}, version="3.64.0rc1")
    result = _run(dist, "--online", "--json", net=net)
    assert result.exit_code == 1
    assert "ajouté à l'installation : grimoire/extra.py" in json.loads(result.output)["problems"]


def test_online_refuses_an_installed_prerelease_that_has_no_github_release(tmp_path: Path) -> None:
    files = {"grimoire/a.py": b"1"}
    net = FakeGithub(tag="v3.70.0-rc1", pre_wheel=_wheel_bytes(tmp_path, files))
    result = _run(_install(tmp_path / "site", files, version="3.64.0rc1"), "--online", net=net)
    assert result.exit_code == 1 and "aucune release GitHub" in result.output


def test_doctor_reports_trust_level_and_fails_on_a_modified_install(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert runner.invoke(app, ["init", str(project)]).exit_code == 0
    site = tmp_path / "site"
    dist = _install(site, {"grimoire/a.py": b"1"})
    with patch("grimoire.tools.install_trust.installed_distribution", return_value=dist):
        ok = runner.invoke(app, ["doctor", "-o", "json", str(project)])
        (site / "grimoire" / "a.py").write_bytes(b"backdoor")
        bad = runner.invoke(app, ["doctor", "-o", "json", str(project)])
    entry = next(c for c in json.loads(ok.output)["checks"] if c["name"] == "trust_level")
    assert entry["trust_level"] == "intact" and entry["passed"] is True
    entry = next(c for c in json.loads(bad.output)["checks"] if c["name"] == "trust_level")
    assert entry["trust_level"] == "modified" and entry["passed"] is False
    assert bad.exit_code == 1
