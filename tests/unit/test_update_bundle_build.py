"""D-15 : ``grimoire self bundle`` construit le bundle que ``grimoire self update --from`` accepte, sans réseau."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.updater import verified_install_cmd
from grimoire.tools import install_trust as trust
from grimoire.tools import update_bundle as bundles
from grimoire.tools import update_bundle_build as build

pytestmark = pytest.mark.covers("cli:self bundle", level="integration")

runner = CliRunner()


# ── fabrique de wheels minimales (pas de réseau, pas de build du dépôt) ───────


def _b64(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _wheel(directory: Path, name: str, version: str, requires: str = "") -> Path:
    module = name.replace("-", "_")
    info = f"{module}-{version}.dist-info"
    files = {
        f"{module}.py": f"VALUE = {version!r}\n".encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{requires}".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(f"{p},{_b64(d)},{len(d)}\n" for p, d in files.items()) + f"{info}/RECORD,,\n"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for p, d in files.items():
            archive.writestr(p, d)
        archive.writestr(f"{info}/RECORD", record)
    return path


def _kit(tmp_path: Path, requires: str = "") -> Path:
    return _wheel(tmp_path / "src", "grimoire-kit", "99.0.0", requires)


def _venv_python(root: Path) -> str:
    made = subprocess.run(
        [sys.executable, "-m", "venv", str(root)], capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"venv indisponible : {made.stderr[-200:]}")
    return str(root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


@pytest.fixture(scope="module")
def pip_python(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Un interpréteur qui a pip (l'environnement de test peut ne pas en avoir : uv)."""
    python = _venv_python(tmp_path_factory.mktemp("pipenv"))
    probe = subprocess.run([python, "-m", "pip", "--version"], capture_output=True, timeout=60, check=False)
    if probe.returncode != 0:
        pytest.skip("pip indisponible dans le venv jetable")
    return python


def _offline_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    """pip ne sort pas : les dépendances ne viennent que des ``--find-links`` donnés."""
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)


# ── aller-retour : construire -> lecteur d'installation -> vrai pip dans un venv jetable ──


def test_round_trip_build_then_install_in_a_throwaway_venv(
    tmp_path: Path, pip_python: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_pip(monkeypatch)
    index = tmp_path / "index"
    _wheel(index, "zz-offline-dep", "1.2.3")
    kit = _kit(tmp_path, "Requires-Dist: zz-offline-dep>=1\n")
    dest = tmp_path / "bundle"

    report = build.build_bundle(dest, wheel=kit, with_deps=True, find_links=[str(index)], pip_python=pip_python)

    assert sorted(p.name for p in dest.iterdir()) == sorted([kit.name, "zz_offline_dep-1.2.3-py3-none-any.whl", "SHA256SUMS", "PROVENANCE.json"])
    assert report.dependencies == ("zz_offline_dep-1.2.3-py3-none-any.whl",)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    staged, verdict = bundles.stage_and_verify(bundles.open_bundle(dest), scratch)
    assert verdict.level == "bundle"  # jamais "verified" : le bundle prouve son intégrité, pas son origine

    env_python = _venv_python(tmp_path / "target")
    cmd = verified_install_cmd(staged, [], manager=("pip", None), offline_dir=scratch)
    cmd[0] = env_python
    installed = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=300, check=False,
        env={"PATH": "", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INDEX": "1", "SYSTEMROOT": str(tmp_path), "HOME": str(tmp_path)},
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    shown = subprocess.run(
        [env_python, "-c", "import grimoire_kit, zz_offline_dep; print(grimoire_kit.VALUE, zz_offline_dep.VALUE)"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert shown.stdout.split() == ["99.0.0", "1.2.3"], shown.stdout + shown.stderr


def test_the_sums_file_lists_every_file_with_its_real_digest_and_unix_newlines(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    build.build_bundle(dest, wheel=_kit(tmp_path))
    raw = (dest / "SHA256SUMS").read_bytes()
    assert b"\r" not in raw
    listed = trust.parse_sha256sums(raw.decode("utf-8"))
    assert sorted(listed) == ["PROVENANCE.json", "grimoire_kit-99.0.0-py3-none-any.whl"]
    for name, digest in listed.items():
        assert trust.sha256_file(dest / name) == digest


def test_provenance_records_what_was_and_was_not_verified(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    build.build_bundle(dest, wheel=_kit(tmp_path))
    data = json.loads((dest / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert data["source"]["kind"] == "local-wheel"
    assert data["build_trust"]["level"] == "local"
    assert data["attestation"]["status"] == "not-applicable"
    assert data["install_trust_level"] == "bundle"
    assert data["kit"]["sha256"] == trust.sha256_file(dest / data["kit"]["name"])


# ── refus : un bundle altéré ou incomplet ne s'installe pas ───────────────────


def _built(tmp_path: Path, pip_python: str | None = None, monkeypatch: pytest.MonkeyPatch | None = None) -> Path:
    dest = tmp_path / "bundle"
    if pip_python is None:
        build.build_bundle(dest, wheel=_kit(tmp_path))
        return dest
    assert monkeypatch is not None
    _offline_pip(monkeypatch)
    index = tmp_path / "index"
    _wheel(index, "zz-offline-dep", "1.2.3")
    build.build_bundle(dest, wheel=_kit(tmp_path, "Requires-Dist: zz-offline-dep>=1\n"), with_deps=True, find_links=[str(index)], pip_python=pip_python)
    return dest


def _install_check(dest: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "check"
    scratch.mkdir()
    bundles.stage_and_verify(bundles.open_bundle(dest), scratch)


def test_a_modified_kit_wheel_is_refused(tmp_path: Path) -> None:
    dest = _built(tmp_path)
    wheel = next(dest.glob("grimoire_kit-*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(trust.TrustError, match="diffère"):
        _install_check(dest, tmp_path)


def test_a_modified_dependency_is_refused(tmp_path: Path, pip_python: str, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = _built(tmp_path, pip_python, monkeypatch)
    dep = next(dest.glob("zz_offline_dep-*.whl"))
    dep.write_bytes(dep.read_bytes() + b"tampered")
    with pytest.raises(trust.TrustError, match="diffère"):
        _install_check(dest, tmp_path)


def test_a_dependency_missing_from_the_sums_is_refused(tmp_path: Path, pip_python: str, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = _built(tmp_path, pip_python, monkeypatch)
    sums = dest / "SHA256SUMS"
    kept = [line for line in sums.read_text(encoding="utf-8").splitlines() if "zz_offline_dep" not in line]
    sums.write_text("\n".join(kept) + "\n", encoding="utf-8")
    with pytest.raises(trust.TrustError, match="n'est pas dans SHA256SUMS"):
        _install_check(dest, tmp_path)


def test_a_missing_sums_file_is_refused(tmp_path: Path) -> None:
    dest = _built(tmp_path)
    (dest / "SHA256SUMS").unlink()
    with pytest.raises(trust.TrustError, match="un bundle sans somme ne s'installe pas"):
        bundles.open_bundle(dest)


def test_a_wheel_added_after_the_build_is_refused(tmp_path: Path) -> None:
    dest = _built(tmp_path)
    _wheel(dest, "intruder", "1.0")
    with pytest.raises(trust.TrustError, match="n'est pas dans SHA256SUMS"):
        _install_check(dest, tmp_path)


# ── la construction elle-même échoue fermé et ne laisse rien ──────────────────


def test_a_failed_dependency_download_leaves_nothing(tmp_path: Path, pip_python: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _offline_pip(monkeypatch)
    dest = tmp_path / "out" / "bundle"
    kit = _kit(tmp_path, "Requires-Dist: zz-not-anywhere>=1\n")
    with pytest.raises(build.BuildError, match="pip download a échoué"):
        build.build_bundle(dest, wheel=kit, with_deps=True, pip_python=pip_python)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []  # pas même le dossier de montage


def test_a_non_empty_destination_is_refused_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    dest.mkdir()
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(build.BuildError, match="dossier vide"):
        build.build_bundle(dest, wheel=_kit(tmp_path))
    assert [p.name for p in dest.iterdir()] == ["keep.txt"]


def test_an_empty_destination_is_filled(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    dest.mkdir()
    build.build_bundle(dest, wheel=_kit(tmp_path))
    assert (dest / "SHA256SUMS").is_file()


@pytest.mark.parametrize("name", ["other-1.0-py3-none-any.whl", "grimoire_kit-1.0.tar.gz", "not-a-wheel.whl"])
def test_a_file_that_is_not_a_kit_wheel_is_refused(tmp_path: Path, name: str) -> None:
    fake = tmp_path / name
    fake.write_bytes(b"x")
    with pytest.raises(build.BuildError):
        build.build_bundle(tmp_path / "bundle", wheel=fake)
    assert not (tmp_path / "bundle").exists()


def test_two_sources_or_target_flags_without_deps_are_refused(tmp_path: Path) -> None:
    kit = _kit(tmp_path)
    with pytest.raises(build.BuildError, match="une seule source"):
        build.build_bundle(tmp_path / "a", wheel=kit, version="1.0.0")
    with pytest.raises(build.BuildError, match="--with-deps"):
        build.build_bundle(tmp_path / "b", wheel=kit, platforms=["win_amd64"])


def test_target_options_reach_pip_download_and_only_wheels_are_allowed(tmp_path: Path) -> None:
    argv = build.download_argv(
        ["py", "-m", "pip"], tmp_path / "k.whl", tmp_path / "d", platforms=["win_amd64", "manylinux2014_x86_64"],
        python_version="3.12", implementation="cp", abis=["cp312"], index_url="https://mirror.example/simple", find_links=["wheels"],
    )
    assert "--only-binary=:all:" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--platform"] == ["win_amd64", "manylinux2014_x86_64"]
    assert argv[argv.index("--python-version") + 1] == "3.12"
    assert argv[argv.index("--abi") + 1] == "cp312" and argv[argv.index("--implementation") + 1] == "cp"
    assert argv[argv.index("--index-url") + 1] == "https://mirror.example/simple"
    assert argv[-1] == str(tmp_path / "k.whl")


def test_pip_is_found_or_the_build_says_why_not(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build.pip_command("/x/python") == ["/x/python", "-m", "pip"]
    monkeypatch.setattr(build.importlib.util, "find_spec", lambda _name: None)  # grimoire tourne sans pip (uv tool)
    assert build.pip_command(None, lambda n: "/u/pip" if n == "pip" else None) == ["/u/pip"]
    assert build.pip_command(None, lambda n: "/u/uv" if n == "uv" else None) == ["/u/uv", "tool", "run", "pip"]
    with pytest.raises(build.BuildError, match="aucun pip"):
        build.pip_command(None, lambda _n: None)


# ── sources en ligne (réseau simulé) : PyPI, release GitHub, attestation ──────


class _Net:
    """Un « réseau » à un seul kit : PyPI, SHA256SUMS et release GitHub servis depuis la mémoire."""

    def __init__(self, tmp_path: Path, *, sums_digest: str | None = None) -> None:
        self.wheel = _wheel(tmp_path / "published", "grimoire-kit", "99.0.0")
        self.data = self.wheel.read_bytes()
        digest = hashlib.sha256(self.data).hexdigest()
        files = f"https://files.pythonhosted.org/packages/ab/{self.wheel.name}"
        gh = f"https://github.com/{trust.REPO}/releases/download/v99.0.0/{self.wheel.name}"
        self.routes: dict[str, bytes] = {
            "https://pypi.org/pypi/grimoire-kit/json": json.dumps({"releases": {"99.0.0": [{"yanked": False}], "100.0.0rc1": [{"yanked": False}]}}).encode(),
            "https://pypi.org/pypi/grimoire-kit/99.0.0/json": json.dumps({"urls": [
                {"packagetype": "bdist_wheel", "filename": self.wheel.name, "url": files, "digests": {"sha256": digest}},
            ]}).encode(),
            files: self.data,
            f"https://github.com/{trust.REPO}/releases/download/v99.0.0/SHA256SUMS": f"{sums_digest or digest}  {self.wheel.name}\n".encode(),
            f"https://api.github.com/repos/{trust.REPO}/releases/tags/v99.0.0": json.dumps({
                "tag_name": "v99.0.0", "prerelease": False, "draft": False,
                "assets": [{"name": self.wheel.name, "browser_download_url": gh}],
            }).encode(),
            gh: self.data,
        }
        self.seen: list[str] = []

    def __call__(self, url: str, timeout: float = 0) -> io.BytesIO:
        self.seen.append(url)
        if url not in self.routes:
            raise OSError(f"hors réseau simulé : {url}")
        return io.BytesIO(self.routes[url])


def _gh(returncode: int, stderr: str = "") -> Any:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_pypi_source_is_checked_against_pypi_and_the_release_sums_then_attested(tmp_path: Path) -> None:
    net = _Net(tmp_path)
    gh = _gh(0)
    report = build.build_bundle(tmp_path / "b", opener=net, runner=gh, which=lambda n: "/usr/bin/gh" if n == "gh" else None)
    assert (report.version, report.source, report.build_trust, report.attestation) == ("99.0.0", "pypi", "verified", "verified")
    assert gh.calls[0][1:3] == ["attestation", "verify"] and gh.calls[0][-2:] == ["--repo", trust.REPO]
    assert not any("100.0.0" in u for u in net.seen)  # la pré-version n'est pas « la dernière stable »
    assert bundles.open_bundle(tmp_path / "b").version.public == "99.0.0"


def test_without_gh_the_attestation_is_marked_unverified_not_ok(tmp_path: Path) -> None:
    report = build.build_bundle(tmp_path / "b", version="99.0.0", opener=_Net(tmp_path), runner=_gh(0), which=lambda _n: None)
    assert report.attestation == "unverified"
    data = json.loads((tmp_path / "b" / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert data["attestation"]["status"] == "unverified" and "gh absent" in data["attestation"]["detail"]


def test_a_failed_attestation_refuses_the_build_unless_skipped_on_purpose(tmp_path: Path) -> None:
    net = _Net(tmp_path)
    with pytest.raises(build.BuildError, match="gh attestation verify a refusé"):
        build.build_bundle(tmp_path / "b", version="99.0.0", opener=net, runner=_gh(1, "no attestations found"), which=lambda _n: "gh")
    assert not (tmp_path / "b").exists()
    report = build.build_bundle(
        tmp_path / "c", version="99.0.0", opener=net, runner=_gh(1), which=lambda _n: "gh", skip_attestation=True,
    )
    assert report.attestation == "skipped"


def test_a_wheel_that_differs_from_the_release_sums_is_refused(tmp_path: Path) -> None:
    net = _Net(tmp_path, sums_digest="0" * 64)
    with pytest.raises(build.BuildError, match="diffère"):
        build.build_bundle(tmp_path / "b", version="99.0.0", opener=net, runner=_gh(0), which=lambda _n: None)
    assert not (tmp_path / "b").exists()


def test_github_release_source_uses_the_release_sums(tmp_path: Path) -> None:
    report = build.build_bundle(tmp_path / "b", release="v99.0.0", opener=_Net(tmp_path), runner=_gh(0), which=lambda _n: None)
    assert (report.source, report.build_trust) == ("github-release", "release-only")
    assert bundles.open_bundle(tmp_path / "b").version.public == "99.0.0"


def test_an_unknown_release_or_a_prerelease_asked_from_pypi_is_refused(tmp_path: Path) -> None:
    net = _Net(tmp_path)
    with pytest.raises(build.BuildError):
        build.build_bundle(tmp_path / "a", release="v1.2.3", opener=net, runner=_gh(0), which=lambda _n: None)
    with pytest.raises(build.BuildError, match="--from-release"):
        build.build_bundle(tmp_path / "b", version="100.0.0rc1", opener=net, runner=_gh(0), which=lambda _n: None)
    with pytest.raises(build.BuildError, match=r"vX\.Y\.Z"):
        build.build_bundle(tmp_path / "c", release="../evil", opener=net, runner=_gh(0), which=lambda _n: None)


# ── la commande ───────────────────────────────────────────────────────────────


def test_cli_self_bundle_builds_a_bundle_the_update_command_reads(tmp_path: Path) -> None:
    kit = _kit(tmp_path)
    dest = tmp_path / "bundle"
    result = runner.invoke(app, ["self", "bundle", str(dest), "--wheel", str(kit), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == "99.0.0" and payload["source"] == "local-wheel" and payload["dependencies"] == []
    checked = runner.invoke(app, ["self", "update", "--from", str(dest), "--check", "--json"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["trust_level"] == "bundle"


def test_cli_self_bundle_refuses_with_a_message_and_exit_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["self", "bundle", str(tmp_path / "b"), "--wheel", str(tmp_path / "absent.whl")])
    assert result.exit_code == 1 and "Nothing was written" in result.output
    flags = runner.invoke(app, ["self", "bundle", str(tmp_path / "c"), "--wheel", str(_kit(tmp_path)), "--platform", "win_amd64"])
    assert flags.exit_code == 1 and "--with-deps" in flags.output
