"""Canaux de mise à jour et bundle hors ligne : les règles pures et l'installation réelle sans réseau (D-04)."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from grimoire.cli.updater import resolve_latest_version, verified_install_cmd
from grimoire.tools import install_trust as trust
from grimoire.tools import update_bundle as bundles
from grimoire.tools import update_channels as channels

_REPO = "Guilhem-Bonnet/Grimoire-kit"


def _release(tag: str, *, wheel: str | None = None, prerelease: bool = True, draft: bool = False, host: str = "github.com") -> dict:
    name = wheel or f"grimoire_kit-{tag.removeprefix('v').replace('-rc', 'rc')}-py3-none-any.whl"
    return {
        "tag_name": tag, "prerelease": prerelease, "draft": draft,
        "assets": [{"name": name, "browser_download_url": f"https://{host}/{_REPO}/releases/download/{tag}/{name}"}],
    }


# ── stable : jamais une pré-version ───────────────────────────────────────────


def test_info_version_is_not_a_fallback_when_it_is_a_prerelease() -> None:
    """PyPI rend ``info.version`` = la dernière version même pré-version si aucune stable n'existe."""
    only_pre = {"info": {"version": "3.64.0rc1"}, "releases": {"3.64.0rc1": [{"yanked": False}]}}
    assert resolve_latest_version(only_pre) is None
    assert resolve_latest_version({"info": {"version": "3.64.0rc1"}}) is None
    assert resolve_latest_version({"info": {"version": "3.64.0.dev1"}}) is None
    assert resolve_latest_version({"info": {"version": "not-a-version"}}) is None


def test_the_highest_stable_still_wins_over_newer_prereleases() -> None:
    data = {
        "info": {"version": "3.64.0rc1"},
        "releases": {"3.62.0": [{}], "3.63.0": [{"yanked": True}], "3.64.0rc1": [{}], "3.64.0.dev2": [{}]},
    }
    assert resolve_latest_version(data) == "3.62.0"


# ── preview : la pré-version publiée sur GitHub ───────────────────────────────


def test_select_preview_takes_the_highest_prerelease() -> None:
    ref = channels.select_preview([_release("v3.63.0-rc1"), _release("v3.64.0-rc2"), _release("v3.64.0-rc10")])
    assert ref is not None and ref.tag == "v3.64.0-rc10" and ref.version == "3.64.0rc10"
    assert ref.wheel.filename == "grimoire_kit-3.64.0rc10-py3-none-any.whl"


@pytest.mark.parametrize(
    "release",
    [
        _release("v3.64.0-rc1", draft=True),
        _release("v3.64.0-rc1", prerelease=False),
        _release("v3.64.0", prerelease=True),  # un tag stable n'est pas une pré-version
        _release("v3.64.0-rc1", host="evil.example"),
        _release("v3.64.0-rc1", wheel="other_pkg-3.64.0rc1-py3-none-any.whl"),
        _release("v3.64.0-rc1", wheel="grimoire_kit-3.99.0rc1-py3-none-any.whl"),  # numéro ≠ tag
        _release("v3.64.0-rc1", wheel="grimoire_kit-3.64.0rc1.tar.gz"),
        {"tag_name": "v3.64.0-rc1", "prerelease": True, "assets": "none"},
        "not a dict",
    ],
)
def test_select_preview_ignores_what_it_cannot_trust(release: object) -> None:
    assert channels.select_preview([release]) is None


def test_select_preview_on_garbage_is_none() -> None:
    assert channels.select_preview({"message": "rate limited"}) is None
    assert channels.select_preview(None) is None


def test_select_preview_can_target_the_installed_prerelease() -> None:
    listing = [_release("v3.64.0-rc1"), _release("v3.64.0-rc2")]
    ref = channels.select_preview(listing, "3.64.0rc1")
    assert ref is not None and ref.tag == "v3.64.0-rc1"
    assert channels.select_preview(listing, "3.70.0rc1") is None


def test_choose_takes_the_highest_of_pypi_and_github() -> None:
    ref = channels.select_preview([_release("v3.64.0-rc1")])
    assert channels.choose("3.63.0", ref) == ("3.64.0rc1", ref)
    assert channels.choose("3.64.0", ref) == ("3.64.0", None)  # la stable dépasse sa propre pré-version
    assert channels.choose(None, ref) == ("3.64.0rc1", ref)
    assert channels.choose("3.63.0", None) == ("3.63.0", None)
    assert channels.choose(None, None) is None


def test_release_wheel_has_no_pypi_fallback_and_no_age_exemption(tmp_path: Path) -> None:
    wheel = tmp_path / "grimoire_kit-3.64.0rc1-py3-none-any.whl"
    wheel.write_bytes(b"w")
    digest = hashlib.sha256(b"w").hexdigest()
    with pytest.raises(trust.TrustError, match="SHA256SUMS"):
        trust.check_release_wheel(wheel, version="3.64.0rc1", sums=None)
    with pytest.raises(trust.TrustError, match="n'est pas dans"):
        trust.check_release_wheel(wheel, version="3.64.0rc1", sums={"other.whl": digest})
    with pytest.raises(trust.TrustError, match="diffère"):
        trust.check_release_wheel(wheel, version="3.64.0rc1", sums={wheel.name: "0" * 64})
    verdict = trust.check_release_wheel(wheel, version="3.64.0rc1", sums={wheel.name: digest})
    assert verdict.level == "release-only" and "une seule source" in verdict.detail


def test_sums_of_a_prerelease_are_read_at_its_own_tag() -> None:
    assert trust.sums_url("3.64.0rc1", "v3.64.0-rc1").endswith("/download/v3.64.0-rc1/SHA256SUMS")
    assert trust.sums_url("3.62.0").endswith("/download/v3.62.0/SHA256SUMS")


# ── bundle ────────────────────────────────────────────────────────────────────


def _sums(*files: Path) -> str:
    return "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in files)


def test_a_bundle_level_is_never_verified(tmp_path: Path) -> None:
    wheel = tmp_path / "grimoire_kit-99.0.0-py3-none-any.whl"
    wheel.write_bytes(b"w")
    (tmp_path / "SHA256SUMS").write_text(_sums(wheel), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    staged, verdict = bundles.stage_and_verify(bundles.open_bundle(tmp_path), scratch)
    assert verdict.level == "bundle" and "indépendante" in verdict.detail
    assert staged.parent == scratch and staged.read_bytes() == b"w"


def test_the_staged_copy_is_what_is_hashed(tmp_path: Path) -> None:
    """La wheel est copiée puis hachée : un fichier remplacé après l'ouverture est vu."""
    wheel = tmp_path / "grimoire_kit-99.0.0-py3-none-any.whl"
    wheel.write_bytes(b"w")
    (tmp_path / "SHA256SUMS").write_text(_sums(wheel), encoding="utf-8")
    bundle = bundles.open_bundle(tmp_path)
    wheel.write_bytes(b"swapped after open")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(trust.TrustError, match="diffère"):
        bundles.stage_and_verify(bundle, scratch)


@pytest.mark.parametrize("name", ["not-a-wheel.txt", "other_pkg-1.0-py3-none-any.whl"])
def test_a_file_that_is_not_a_kit_wheel_is_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"x")
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")
    with pytest.raises(trust.TrustError):
        bundles.open_bundle(tmp_path / name)


def test_a_missing_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(trust.TrustError, match="n'existe pas"):
        bundles.open_bundle(tmp_path / "absent")


# ── commandes d'installation hors ligne ───────────────────────────────────────


def test_offline_install_commands_close_the_index_and_forbid_building(tmp_path: Path) -> None:
    wheel = tmp_path / "grimoire_kit-99.0.0-py3-none-any.whl"
    pip = verified_install_cmd(wheel, [], manager=("pip", None), offline_dir=tmp_path)
    assert pip[1:4] == ["-m", "pip", "install"]
    assert "--no-index" in pip and pip[pip.index("--only-binary") + 1] == ":all:"
    assert pip[pip.index("--find-links") + 1] == str(tmp_path)
    uv = verified_install_cmd(wheel, [], manager=("uv", "/u/uv"), offline_dir=tmp_path)
    assert uv[:5] == ["/u/uv", "tool", "install", "--force", "--offline"] and "--no-build" in uv
    pipx = verified_install_cmd(wheel, [], manager=("pipx", "/u/pipx"), offline_dir=tmp_path)
    assert pipx[3].startswith("--pip-args=--no-index --only-binary :all: --find-links ")
    for cmd in (pip, uv, pipx):
        assert cmd[-1].startswith("grimoire-kit @ file://")  # le spécifieur reste en dernier


def test_online_install_commands_are_unchanged(tmp_path: Path) -> None:
    wheel = tmp_path / "grimoire_kit-99.0.0-py3-none-any.whl"
    for manager in (("pip", None), ("uv", "/u/uv"), ("pipx", "/u/pipx")):
        cmd = verified_install_cmd(wheel, [], manager=manager)
        assert not any(("--no-index" in p) or p == "--offline" for p in cmd), cmd


# ── de bout en bout, vrai pip, vraie coupure réseau ───────────────────────────


def _b64(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _wheel(directory: Path, name: str, version: str, requires: str = "") -> Path:
    """Wheel pure-Python minimale : un module ``<name>.py`` et, au besoin, une dépendance."""
    module = name.replace("-", "_")
    info = f"{module}-{version}.dist-info"
    files = {
        f"{module}.py": f"VALUE = {version!r}\n".encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{requires}".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(f"{p},{_b64(d)},{len(d)}\n" for p, d in files.items()) + f"{info}/RECORD,,\n"
    path = directory / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for p, d in files.items():
            archive.writestr(p, d)
        archive.writestr(f"{info}/RECORD", record)
    return path


def _netns_available() -> bool:
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    probe = subprocess.run([unshare, "-rn", "true"], capture_output=True, timeout=20, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _netns_available(), reason="unshare -rn indisponible : pas de coupure réseau réelle")
def test_a_real_pip_install_from_a_bundle_works_in_a_network_less_namespace(tmp_path: Path) -> None:
    """Critère D-04 : « installation réussie avec les sockets bloquées », sans double et avec une dépendance."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    dep = _wheel(bundle, "zz-offline-dep", "1.2.3")
    kit = _wheel(bundle, "grimoire-kit", "99.0.0", "Requires-Dist: zz-offline-dep>=1\n")
    (bundle / "SHA256SUMS").write_text(_sums(dep, kit), encoding="utf-8")

    env_dir = tmp_path / "venv"
    made = subprocess.run([sys.executable, "-m", "venv", str(env_dir)], capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    if made.returncode != 0:
        pytest.skip(f"venv indisponible : {made.stderr[-200:]}")
    python = str(env_dir / "bin" / "python")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    staged, verdict = bundles.stage_and_verify(bundles.open_bundle(bundle), scratch)
    assert verdict.level == "bundle"
    cmd = verified_install_cmd(staged, [], manager=("pip", None), offline_dir=bundle)
    cmd[0] = python

    def netless(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["unshare", "-rn", *argv], capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PIP_CONFIG_FILE": "/dev/null", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )

    # le témoin : dans ce namespace, un socket vers l'extérieur échoue vraiment
    probe = netless(python, "-c", "import socket; socket.create_connection(('1.1.1.1', 53), timeout=2)")
    assert probe.returncode != 0, "la coupure réseau n'est pas effective : le test ne prouverait rien"

    installed = netless(*cmd)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    shown = netless(python, "-c", "import grimoire_kit, zz_offline_dep; print(grimoire_kit.VALUE, zz_offline_dep.VALUE)")
    assert shown.stdout.split() == ["99.0.0", "1.2.3"], shown.stdout + shown.stderr
