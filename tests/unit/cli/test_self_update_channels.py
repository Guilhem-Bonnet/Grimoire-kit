"""``grimoire self update`` : canaux stable/preview, ``--check --offline`` et bundle hors ligne (D-04).

Constat : la commande ne connaissait ni canal ni mode hors ligne. Sans réseau elle
sortait en erreur (« No internet »), sans moyen d'installer une release que l'on
a déjà ; et le canal unique pouvait choisir une pré-version, quand PyPI rend
``info.version`` pré-version faute de stable.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

from ..test_update_channels import _sums, _wheel
from ._fake_release import FakeGithub, FakeRelease, sha

runner = CliRunner()


def _invoke(net, *args: str, installed: str = "3.0.0", online: bool = True, which=None):
    ok = MagicMock(returncode=0)
    calls: list[list[str]] = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        return ok

    with patch("grimoire.cli.app.is_online", return_value=online), \
         patch("grimoire.cli.app.__version__", installed), \
         patch("urllib.request.urlopen", net), \
         patch("shutil.which", side_effect=which or (lambda _n: None)), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=[]), \
         patch("subprocess.run", side_effect=run):
        result = runner.invoke(app, ["self", "update", *args])
    return result, calls


class _NoNetwork:
    """``urlopen`` qui échoue le test si on l'appelle, et sonde de connectivité idem."""

    def __call__(self, url, timeout=None):
        raise AssertionError(f"requête réseau interdite : {url}")


# ── stable ne choisit jamais une pré-version ──────────────────────────────────


def test_stable_never_takes_a_prerelease_that_pypi_reports_as_its_version() -> None:
    """PyPI rend ``info.version`` pré-version faute de stable : le canal stable ne l'installe pas."""
    result, calls = _invoke(FakeRelease(version="99.0.0rc1"))
    assert "Updated" not in result.output
    assert not calls, calls
    assert result.exit_code == 1 and "latest version" in result.output


def test_stable_ignores_the_github_prereleases_and_does_not_even_ask_for_them() -> None:
    net = FakeGithub(stable="3.62.0")
    result, calls = _invoke(net, installed="3.60.0")
    assert result.exit_code == 0, result.output
    assert "Updated to 3.62.0" in result.output
    assert FakeGithub.API not in net.requested
    assert "rc" not in " ".join(calls[-1])


# ── preview ───────────────────────────────────────────────────────────────────


def test_preview_installs_the_newer_github_prerelease_with_only_the_release_sums() -> None:
    net = FakeGithub(stable="3.62.0", tag="v3.64.0-rc1")
    result, calls = _invoke(net, "--channel", "preview", installed="3.62.0")
    assert result.exit_code == 0, result.output
    assert "Updated to 3.64.0rc1" in result.output
    assert "trust_level: release-only" in result.output
    assert net.rc_url in net.requested
    assert calls[-1][-1].endswith(net.rc_name)
    assert f"https://pypi.org/pypi/grimoire-kit/{net.version}" not in " ".join(net.requested)


def test_preview_prefers_the_stable_release_once_it_is_newer_than_the_prerelease() -> None:
    net = FakeGithub(stable="3.65.0", tag="v3.64.0-rc1")
    result, calls = _invoke(net, "--channel", "preview", installed="3.62.0")
    assert result.exit_code == 0, result.output
    assert "Updated to 3.65.0" in result.output and "trust_level: verified" in result.output
    assert not any("rc1" in part for part in calls[-1])


def test_preview_refuses_a_prerelease_whose_release_has_no_sums() -> None:
    net = FakeGithub(pre_sums=None)
    result, calls = _invoke(net, "--channel", "preview", installed="3.62.0")
    assert result.exit_code == 1
    assert "SHA256SUMS" in result.output and "Nothing was installed" in result.output
    assert not calls


def test_preview_refuses_a_tampered_prerelease_wheel() -> None:
    result, calls = _invoke(FakeGithub(pre_served=b"tampered"), "--channel", "preview", installed="3.62.0")
    assert result.exit_code == 1 and "Nothing was installed" in result.output
    assert not calls


def test_preview_ignores_a_draft_and_a_release_not_marked_prerelease() -> None:
    for kw in ({"draft": True}, {"prerelease": False}):
        net = FakeGithub(stable="3.62.0", **kw)
        result, _ = _invoke(net, "--channel", "preview", installed="3.62.0")
        assert "Already up to date" in result.output, (kw, result.output)


def test_preview_ignores_an_asset_hosted_outside_the_kit_repository() -> None:
    net = FakeGithub(stable="3.62.0", asset_url="https://evil.example/x/grimoire_kit-3.64.0rc1-py3-none-any.whl")
    result, calls = _invoke(net, "--channel", "preview", installed="3.62.0")
    assert "Already up to date" in result.output and not calls


def test_an_unknown_channel_is_a_refusal() -> None:
    result, calls = _invoke(FakeRelease(), "--channel", "nightly")
    assert result.exit_code == 1 and "Unknown channel" in result.output
    assert not calls


# ── --check ───────────────────────────────────────────────────────────────────


def test_check_offline_reports_offline_without_any_network_attempt() -> None:
    with patch("grimoire.cli.app.is_online", side_effect=AssertionError("sonde interdite")):
        result, calls = _invoke(_NoNetwork(), "--check", "--offline")
    assert result.exit_code == 0, result.output
    assert "offline" in result.output
    assert not calls


def test_check_offline_json_has_a_status_field() -> None:
    result, _ = _invoke(_NoNetwork(), "--check", "--offline", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "offline" and data["latest"] is None and data["channel"] == "stable"


def test_check_without_connectivity_also_reports_offline_instead_of_an_error() -> None:
    result, _ = _invoke(_NoNetwork(), "--check", online=False)
    assert result.exit_code == 0 and "offline" in result.output


def test_check_reports_an_available_update_and_installs_nothing() -> None:
    net = FakeRelease()
    result, calls = _invoke(net, "--check", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert (data["status"], data["latest"], data["source"]) == ("update-available", "99.0.0", "pypi")
    assert not calls and net.url not in net.requested  # ni installation ni téléchargement


def test_check_reports_up_to_date() -> None:
    result, _ = _invoke(FakeRelease(version="3.0.0"), "--check", "--json")
    assert json.loads(result.output)["status"] == "up-to-date"


def test_offline_without_a_bundle_cannot_update_and_says_how() -> None:
    result, calls = _invoke(_NoNetwork(), "--offline")
    assert result.exit_code == 1 and "--from" in result.output
    assert not calls


def test_json_without_check_is_refused() -> None:
    result, _ = _invoke(FakeRelease(), "--json")
    assert result.exit_code == 1 and "--check" in result.output


def test_the_top_level_alias_takes_the_same_options() -> None:
    with patch("grimoire.cli.app.is_online", return_value=False):
        result = runner.invoke(app, ["update", "--check", "--offline", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "offline"


# ── bundle hors ligne ─────────────────────────────────────────────────────────


def _bundle(root: Path, *, wheel: bytes = b"bundle-wheel", version: str = "99.0.0", extra: dict[str, bytes] | None = None,
            listed_extra: bool = True, sums: str | None = "auto") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    name = f"grimoire_kit-{version}-py3-none-any.whl"
    (root / name).write_bytes(wheel)
    lines = [f"{sha(wheel)}  {name}"]
    for other, data in (extra or {}).items():
        (root / other).write_bytes(data)
        if listed_extra:
            lines.append(f"{sha(data)}  {other}")
    if sums is not None:
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n" if sums == "auto" else sums, encoding="utf-8")
    return root


def test_a_bundle_installs_with_every_socket_closed(tmp_path: Path) -> None:
    """Critère D-04 : installation réussie, sockets bloquées. Aucune requête, aucune sonde, index fermé."""
    bundle = _bundle(tmp_path / "bundle", extra={"pydantic-2.0-py3-none-any.whl": b"dep"})

    def no_socket(*_a, **_k):
        raise AssertionError("socket ouvert")

    with patch("grimoire.cli.app.is_online", side_effect=AssertionError("sonde interdite")), \
         patch.object(socket, "socket", no_socket), patch.object(socket, "create_connection", no_socket):
        result, calls = _invoke(_NoNetwork(), "--from", str(bundle), installed="3.0.0")
    assert result.exit_code == 0, result.output
    assert "Updated to 99.0.0" in result.output
    assert "trust_level: bundle" in result.output
    cmd = calls[-1]
    assert "--no-index" in cmd and "--only-binary" in cmd
    link = cmd[cmd.index("--find-links") + 1]
    # jamais le dossier du bundle (HTML, intrus) : le dossier temporaire où la wheel vérifiée a été copiée
    assert link != str(bundle) and cmd[-1].startswith(f"grimoire-kit @ {Path(link).as_uri()}/")
    assert cmd[-1].startswith("grimoire-kit @ file://") and cmd[-1].endswith("grimoire_kit-99.0.0-py3-none-any.whl")


def test_a_real_pip_cannot_reach_a_wheel_the_bundle_does_not_list(tmp_path: Path) -> None:
    """D04-S1-01 : un ``links.html`` du bundle vers une wheel extérieure ne fait pas entrer une dépendance non listée.

    La vraie commande de ``self update --from`` est exécutée par un vrai ``pip`` (venv neuf).
    """
    bundle, outside = tmp_path / "bundle", tmp_path / "outside"
    bundle.mkdir()
    outside.mkdir()
    kit = _wheel(bundle, "grimoire-kit", "99.0.0", "Requires-Dist: depx\n")
    depx = _wheel(outside, "depx", "1.0")
    (bundle / "SHA256SUMS").write_text(_sums(kit), encoding="utf-8")  # depx n'est listée nulle part
    (bundle / "links.html").write_text(f'<a href="{depx.as_uri()}">depx</a>', encoding="utf-8")

    env_dir = tmp_path / "venv"
    made = subprocess.run([sys.executable, "-m", "venv", str(env_dir)], capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    if made.returncode != 0:
        pytest.skip(f"venv indisponible : {made.stderr[-200:]}")
    python = str(env_dir / "bin" / "python")
    real_run = subprocess.run
    ran: list[subprocess.CompletedProcess[str]] = []

    def run(cmd, **_kw):
        if "install" in cmd:  # l'installation, pour de vrai, dans le venv jeté
            done = real_run(
                [python, *cmd[1:]], capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
                env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PIP_CONFIG_FILE": "/dev/null", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
            )
            ran.append(done)
            return MagicMock(returncode=done.returncode)
        return MagicMock(returncode=0)

    with patch("grimoire.cli.app.is_online", side_effect=AssertionError("sonde interdite")), \
         patch("grimoire.cli.app.__version__", "3.0.0"), patch("urllib.request.urlopen", _NoNetwork()), \
         patch("shutil.which", side_effect=lambda _n: None), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=[]), \
         patch("subprocess.run", side_effect=run):
        result = runner.invoke(app, ["self", "update", "--from", str(bundle)])
    probe = real_run([python, "-c", "import depx"], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert probe.returncode != 0, "depx, absente de SHA256SUMS, s'est installée depuis l'extérieur du bundle : " + (ran[-1].stdout if ran else "")
    assert result.exit_code != 0 and ran and ran[-1].returncode != 0  # pip ne résout pas depx : l'installation échoue


def test_a_single_wheel_beside_its_sums_installs_too(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    result, calls = _invoke(_NoNetwork(), "--from", str(bundle / "grimoire_kit-99.0.0-py3-none-any.whl"))
    assert result.exit_code == 0, result.output
    assert "--no-index" in calls[-1]


def test_uv_and_pipx_get_their_own_offline_flags(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    for manager, expected in (("uv", "--offline"), ("pipx", "--pip-args=--no-index")):
        calls: list[list[str]] = []

        def which(name: str, manager: str = manager) -> str | None:
            return f"/usr/bin/{name}" if name == manager else None

        def recording(cmd, *, sink: list[list[str]] = calls, **_kw):
            sink.append(list(cmd))
            return MagicMock(stdout="grimoire-kit 3.0.0\n", returncode=0)

        with patch("grimoire.cli.app.__version__", "3.0.0"), patch("shutil.which", side_effect=which), \
             patch("grimoire.tools.install_trust.installed_extras", return_value=[]), \
             patch("subprocess.run", side_effect=recording):
            result = runner.invoke(app, ["self", "update", "--from", str(bundle)])
        assert result.exit_code == 0, result.output
        install = next(c for c in calls if "install" in c)
        assert any(part.startswith(expected) for part in install), install
        assert str(bundle) not in " ".join(install)
        assert "--find-links" in " ".join(install)


def test_a_bundle_wheel_altered_after_the_sums_were_written_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (bundle / "grimoire_kit-99.0.0-py3-none-any.whl").write_bytes(b"tampered")
    result, calls = _invoke(_NoNetwork(), "--from", str(bundle))
    assert result.exit_code == 1
    assert "SHA256SUMS" in result.output and "Nothing was installed" in result.output
    assert not calls


def test_a_dependency_wheel_missing_from_the_sums_refuses_the_whole_bundle(tmp_path: Path) -> None:
    """Le gestionnaire lirait cette wheel via ``--find-links`` : elle doit être vérifiée comme l'autre."""
    bundle = _bundle(tmp_path / "bundle", extra={"dep-1.0-py3-none-any.whl": b"dep"}, listed_extra=False)
    result, calls = _invoke(_NoNetwork(), "--from", str(bundle))
    assert result.exit_code == 1 and "dep-1.0-py3-none-any.whl" in result.output
    assert not calls


def test_a_bundle_without_sums_is_refused(tmp_path: Path) -> None:
    result, calls = _invoke(_NoNetwork(), "--from", str(_bundle(tmp_path / "bundle", sums=None)))
    assert result.exit_code == 1 and "SHA256SUMS" in result.output
    assert not calls


def test_a_bundle_with_two_kit_wheels_is_ambiguous(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle", extra={"grimoire_kit-98.0.0-py3-none-any.whl": b"old"})
    result, calls = _invoke(_NoNetwork(), "--from", str(bundle))
    assert result.exit_code == 1 and "plusieurs" in result.output
    assert not calls


def test_a_bundle_that_is_not_newer_changes_nothing(tmp_path: Path) -> None:
    result, calls = _invoke(_NoNetwork(), "--from", str(_bundle(tmp_path / "b", version="3.0.0")), installed="3.0.0")
    assert result.exit_code == 0 and "Already up to date" in result.output
    assert not calls


def test_a_prerelease_bundle_needs_the_preview_channel(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle", version="99.0.0rc1")
    refused, calls = _invoke(_NoNetwork(), "--from", str(bundle))
    assert refused.exit_code == 1 and "preview" in refused.output and not calls
    accepted, calls = _invoke(_NoNetwork(), "--from", str(bundle), "--channel", "preview")
    assert accepted.exit_code == 0, accepted.output
    assert calls


def test_check_on_a_bundle_verifies_it_and_installs_nothing(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    result, calls = _invoke(_NoNetwork(), "--from", str(bundle), "--check", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert (data["status"], data["latest"], data["trust_level"]) == ("update-available", "99.0.0", "bundle")
    assert not calls
    (bundle / "grimoire_kit-99.0.0-py3-none-any.whl").write_bytes(b"tampered")
    broken, _ = _invoke(_NoNetwork(), "--from", str(bundle), "--check", "--json")
    assert broken.exit_code == 1


def test_a_failed_offline_install_does_not_suggest_a_command_that_reopens_the_network(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    with patch("grimoire.cli.app.__version__", "3.0.0"), patch("shutil.which", return_value=None), \
         patch("grimoire.tools.install_trust.installed_extras", return_value=[]), \
         patch("subprocess.run", return_value=MagicMock(returncode=1)):
        result = runner.invoke(app, ["self", "update", "--from", str(bundle)])
    assert result.exit_code == 1
    assert "Update failed" in result.output and "Try manually" not in result.output
