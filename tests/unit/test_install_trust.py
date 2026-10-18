"""Confiance dans l'installation : sommes, wheel publiée, RECORD local (D-03)."""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from importlib import metadata
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from grimoire.tools import install_trust as trust

from .cli._fake_release import FakeRelease, sha


def _b64(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _fake_install(
    root: Path, *, files: dict[str, bytes], extra_meta: str = "", direct_url: dict | None = None,
    with_record: bool = True,
) -> metadata.PathDistribution:
    info = root / "grimoire_kit-1.0.0.dist-info"
    info.mkdir(parents=True)
    rows = []
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append(f"{rel},{_b64(data)},{len(data)}")
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: grimoire-kit\nVersion: 1.0.0\n{extra_meta}", encoding="utf-8",
    )
    rows += [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    if with_record:
        (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    return metadata.PathDistribution(info)


# ── SHA256SUMS ────────────────────────────────────────────────────────────────


def test_sums_parse_text_and_binary_markers() -> None:
    a, b = sha(b"a"), sha(b"b")
    assert trust.parse_sha256sums(f"{a}  one.whl\n{b} *two.tar.gz\n\n") == {"one.whl": a, "two.tar.gz": b}


@pytest.mark.parametrize("text", [
    "not a sum line\n",
    f"{'a' * 63}  short.whl\n",
    f"{'a' * 64}  dir/file.whl\n",
    f"{'a' * 64}  f.whl\n{'b' * 64}  f.whl\n",
])
def test_a_malformed_or_contradictory_sums_file_is_refused_whole(text: str) -> None:
    with pytest.raises(trust.TrustError):
        trust.parse_sha256sums(text)


# ── Vérification d'une wheel téléchargée ──────────────────────────────────────


def _wheel(tmp_path: Path, data: bytes = b"wheel") -> Path:
    path = tmp_path / "grimoire_kit-99.0.0-py3-none-any.whl"
    path.write_bytes(data)
    return path


def test_verified_needs_both_sources_to_agree(tmp_path: Path) -> None:
    w = _wheel(tmp_path)
    verdict = trust.check_wheel(w, version="99.0.0", pypi_sha256=sha(b"wheel"), sums={w.name: sha(b"wheel")})
    assert verdict.level == "verified"


@pytest.mark.parametrize(("pypi", "sums"), [
    (sha(b"x"), {"grimoire_kit-99.0.0-py3-none-any.whl": sha(b"wheel")}),
    (sha(b"wheel"), {"grimoire_kit-99.0.0-py3-none-any.whl": sha(b"x")}),
    (sha(b"wheel"), {"autre.whl": sha(b"wheel")}),
    (None, {"grimoire_kit-99.0.0-py3-none-any.whl": sha(b"wheel")}),
    (sha(b"wheel"), None),
])
def test_any_gap_between_the_file_and_a_source_is_a_refusal(tmp_path: Path, pypi: str | None, sums: dict | None) -> None:
    with pytest.raises(trust.TrustError):
        trust.check_wheel(_wheel(tmp_path), version="99.0.0", pypi_sha256=pypi, sums=sums)


def test_a_version_before_the_sums_existed_is_pypi_only(tmp_path: Path) -> None:
    w = _wheel(tmp_path)
    verdict = trust.check_wheel(w, version="3.62.0", pypi_sha256=sha(b"wheel"), sums=None)
    assert verdict.level == "pypi-only"


def test_an_unreadable_version_is_treated_as_requiring_the_sums() -> None:
    assert trust.sums_required("n'importe quoi") is True
    assert trust.sums_required("3.63.0rc1") is True
    assert trust.sums_required("3.62.9") is False


# ── Réseau ────────────────────────────────────────────────────────────────────


def test_select_wheel_prefers_pure_python_and_keeps_the_pypi_digest() -> None:
    ref = trust.select_wheel(FakeRelease().pypi_payload(), "99.0.0")
    assert ref.filename.endswith("py3-none-any.whl") and ref.sha256 == sha(b"wheel-bytes")


def test_select_wheel_accepts_the_per_version_payload() -> None:
    payload = FakeRelease().pypi_payload()
    del payload["releases"]
    assert trust.select_wheel(payload, "99.0.0").filename.endswith(".whl")


def test_a_wheel_hosted_elsewhere_than_pythonhosted_is_refused() -> None:
    payload = FakeRelease().pypi_payload()
    payload["releases"]["99.0.0"][0]["url"] = "https://evil.example/grimoire_kit-99.0.0-py3-none-any.whl"
    with pytest.raises(trust.TrustError, match="hors de"):
        trust.select_wheel(payload, "99.0.0")


def test_no_wheel_listed_is_a_refusal() -> None:
    with pytest.raises(trust.TrustError):
        trust.select_wheel({"releases": {"1.0.0": [{"packagetype": "sdist"}]}}, "1.0.0")


def test_fetch_sums_distinguishes_absent_from_broken() -> None:
    assert trust.fetch_sums("99.0.0", FakeRelease(sums=None)) is None
    with pytest.raises(trust.TrustError, match="HTTP 502"):
        trust.fetch_sums("99.0.0", FakeRelease(sums=502))

    def offline(url: str, timeout: float | None = None):
        raise URLError("down")

    with pytest.raises(trust.TrustError, match="injoignable"):
        trust.fetch_sums("99.0.0", offline)


def test_fetch_sums_refuses_a_body_that_is_not_utf8() -> None:
    import io

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with pytest.raises(trust.TrustError, match="UTF-8"):
        trust.fetch_sums("99.0.0", lambda url, timeout=None: _Resp(b"\xff\xfe"))


def test_download_keeps_the_wheel_name_and_caps_the_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    net = FakeRelease()
    ref = trust.select_wheel(net.pypi_payload(), "99.0.0")
    path = trust.download_wheel(ref, tmp_path, net)
    assert path.name == net.filename and path.read_bytes() == b"wheel-bytes"
    monkeypatch.setattr(trust, "MAX_WHEEL_BYTES", 4)
    with pytest.raises(trust.TrustError, match="dépasse"):
        trust.download_wheel(ref, tmp_path, net)


def test_download_refuses_a_name_that_climbs_out_of_the_directory(tmp_path: Path) -> None:
    ref = trust.WheelRef("../evil.whl", "https://files.pythonhosted.org/x", None)
    with pytest.raises(trust.TrustError, match="suspect"):
        trust.download_wheel(ref, tmp_path, FakeRelease())


# ── Installation locale ───────────────────────────────────────────────────────


def test_local_trust_intact_then_modified(tmp_path: Path) -> None:
    dist = _fake_install(tmp_path, files={"grimoire/a.py": b"print(1)\n", "grimoire/b.py": b"print(2)\n"})
    assert trust.local_trust(dist).level == "intact"
    (tmp_path / "grimoire" / "a.py").write_bytes(b"print('backdoor')\n")
    (tmp_path / "grimoire" / "b.py").unlink()
    verdict = trust.local_trust(dist)
    assert verdict.level == "modified" and not verdict.ok
    assert set(verdict.problems) == {"modifié : grimoire/a.py", "absent : grimoire/b.py"}


def test_local_trust_ignores_bytecode_and_scripts_outside_the_package(tmp_path: Path) -> None:
    dist = _fake_install(tmp_path, files={"grimoire/a.py": b"x"})
    record = Path(str(dist.locate_file("grimoire_kit-1.0.0.dist-info/RECORD")))
    record.write_text(record.read_text() + "grimoire/__pycache__/a.cpython-312.pyc,sha256=zz,1\n../../bin/grimoire,sha256=zz,1\n")
    assert trust.local_trust(dist).level == "intact"


def test_local_trust_levels_for_dev_and_missing_record(tmp_path: Path) -> None:
    dev = _fake_install(tmp_path / "a", files={"grimoire/a.py": b"x"}, direct_url={"url": "file:///x", "dir_info": {"editable": True}})
    assert trust.local_trust(dev).level == "dev"
    bare = _fake_install(tmp_path / "b", files={"grimoire/a.py": b"x"}, with_record=False)
    assert trust.local_trust(bare).level == "unknown"


# ── RECORD de l'installation contre RECORD de la wheel ────────────────────────


def _wheel_with_record(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "grimoire_kit-1.0.0-py3-none-any.whl"
    rows = "".join(f"{rel},{_b64(data)},{len(data)}\n" for rel, data in files.items())
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
        zf.writestr("grimoire_kit-1.0.0.dist-info/RECORD", rows + "grimoire_kit-1.0.0.dist-info/RECORD,,\n")
    return path


def test_same_as_wheel_names_each_kind_of_gap(tmp_path: Path) -> None:
    files = {"grimoire/a.py": b"1", "grimoire/b.py": b"2"}
    dist = _fake_install(tmp_path / "site", files=files)
    wheel = _wheel_with_record(tmp_path, files)
    assert trust.same_as_wheel(wheel, dist) == ()
    (tmp_path / "w2").mkdir()
    other = _wheel_with_record(tmp_path / "w2", {"grimoire/a.py": b"CHANGED", "grimoire/c.py": b"3"})
    gaps = trust.same_as_wheel(other, dist)
    assert "absent de l'installation : grimoire/c.py" in gaps
    assert "ajouté à l'installation : grimoire/b.py" in gaps
    assert "empreinte différente : grimoire/a.py" in gaps


# ── Extras ────────────────────────────────────────────────────────────────────


def test_installed_extras_lists_only_extras_whose_dependencies_are_present(tmp_path: Path) -> None:
    meta = (
        'Requires-Dist: pytest ; extra == "tests"\n'
        'Requires-Dist: definitely-not-installed-pkg-xyz ; extra == "ghost"\n'
        'Requires-Dist: pytest ; extra == "both"\n'
        'Requires-Dist: definitely-not-installed-pkg-xyz ; extra == "both"\n'
        "Requires-Dist: typer\n"
    )
    dist = _fake_install(tmp_path, files={"grimoire/a.py": b"x"}, extra_meta=meta)
    assert trust.installed_extras(dist) == ["tests"]


def test_http_errors_are_the_only_404_that_means_absent() -> None:
    def broken(url: str, timeout: float | None = None):
        raise HTTPError(url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    with pytest.raises(trust.TrustError):
        trust.fetch_sums("99.0.0", broken)
