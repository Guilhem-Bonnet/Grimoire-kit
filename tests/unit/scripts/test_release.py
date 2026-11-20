"""``scripts/release.py`` sur un faux dépôt jetable (remote nu local, aucun réseau)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "release.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.is_file() or shutil.which("git") is None, reason="dépôt source et git requis"
)

_CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-01

- feat: la précédente (#1)
"""

_CEREMONY = """$schema: "grimoire-ceremony-budget/v1"

calibration:
  calibrated: false
  as_of: "2026-01-01"
  kit_version: "{kit}"
  method: "test"
"""

# Les deux scripts lourds sont remplacés : l'un écrit le catalogue, l'autre échoue sur demande.
_HASHES_STUB = """import pathlib
root = pathlib.Path(__file__).resolve().parent.parent
(root / "registry").mkdir(exist_ok=True)
(root / "registry" / "kit-file-hashes.json").write_text('{"v": "' + (root / "version.txt").read_text().strip() + '"}\\n')
"""
_CHECK_STUB = """import pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
sys.exit(1 if (root / "FAIL_CHECK").exists() else 0)
"""


_LOADED: list[ModuleType] = []


def _module() -> ModuleType:
    """Chargé une fois : ``pytest.raises`` compare la classe ``ReleaseError`` de ce module-là."""
    if not _LOADED:
        spec = importlib.util.spec_from_file_location("release_under_test", _SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED.append(module)
    return _LOADED[0]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()


def _commit(repo: Path, message: str, *paths: str) -> None:
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    root = tmp_path / "kit"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "test"), ("commit.gpgsign", "false")):
        _git(root, "config", key, value)
    files = {
        "version.txt": "1.2.0\n",
        "src/grimoire/__version__.py": '__version__ = "1.2.0"\n',
        "CHANGELOG.md": _CHANGELOG,
        "changelog.d/README.md": "# Fragments\n",
        "evals/ceremony-budget.yaml": _CEREMONY.format(kit="1.2"),
        "scripts/changelog-assemble.py": (_REPO / "scripts" / "changelog-assemble.py").read_text(encoding="utf-8"),
        "scripts/gen-kit-hashes.py": _HASHES_STUB,
        "scripts/check-changelog-release.py": _CHECK_STUB,
        "registry/kit-file-hashes.json": '{"v": "1.2.0"}\n',
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _commit(root, "chore: base", *files)
    (root / "changelog.d" / "truc.md").write_text(
        "- feat(x): le truc\n- fix(x): le correctif\n  sur deux lignes\n", encoding="utf-8"
    )
    _commit(root, "feat(x): le truc (#1234)", "changelog.d/truc.md")
    (root / "changelog.d" / "deja.md").write_text("- fix(y): déjà numéroté (#77)\n", encoding="utf-8")
    _commit(root, "fix(y): déjà numéroté (#77)", "changelog.d/deja.md")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    return root


def _release(repo: Path, version: str = "1.3.0", **kwargs: object) -> tuple[int, list[str]]:
    wheels: list[str] = []
    code = _module().release(repo, version, today="2026-02-02", check_wheel=lambda _r, v: wheels.append(v), **kwargs)  # type: ignore[arg-type]
    return code, wheels


def test_dry_run_n_ecrit_rien(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    code, wheels = _release(repo, dry_run=True)
    out = capsys.readouterr().out
    assert code == 0 and wheels == []
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main" and _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert "release/1.3.0" in out and "(#1234)" in out and "2 fragment(s) seraient assemblés" in out
    assert "rien n'a été écrit" in out


def test_release_complete_jusqu_au_commit(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main_head = _git(repo, "rev-parse", "main")
    code, wheels = _release(repo)
    out = capsys.readouterr().out
    assert code == 0 and wheels == ["1.3.0"]
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "release/1.3.0"
    assert _git(repo, "log", "-1", "--format=%s") == "chore: release 1.3.0"
    assert _git(repo, "rev-parse", "main") == main_head, "main ne bouge pas"
    assert _git(repo, "status", "--porcelain") == "", "tout ce que la release touche est commité"

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split("## [1.3.0] - 2026-02-02", 1)[1].split("## [1.2.0]", 1)[0]
    assert "- feat(x): le truc (#1234)" in section
    assert "  sur deux lignes (#1234)" in section and "- fix(x): le correctif\n" in section
    assert "déjà numéroté (#77)" in section and "(#77) (#77)" not in section
    assert not (repo / "changelog.d" / "truc.md").exists() and (repo / "changelog.d" / "README.md").exists()
    assert (repo / "version.txt").read_text(encoding="utf-8") == "1.3.0\n"
    assert '__version__ = "1.3.0"' in (repo / "src/grimoire/__version__.py").read_text(encoding="utf-8")
    assert "1.3.0" in (repo / "registry/kit-file-hashes.json").read_text(encoding="utf-8")
    committed = set(_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert {
        "CHANGELOG.md",
        "version.txt",
        "src/grimoire/__version__.py",
        "registry/kit-file-hashes.json",
        "changelog.d/truc.md",
    } <= committed
    assert "evals/ceremony-budget.yaml" not in committed, "calibration à une mineure : rien à redater"
    # Ne fusionne pas, ne tague pas : imprime la suite.
    assert _git(repo, "tag") == ""
    assert 'git tag -a v1.3.0 -m "Release 1.3.0"' in out and "gh pr merge" in out


def test_main_sale_est_refuse(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text("bricolé\n", encoding="utf-8")
    with pytest.raises(_module().ReleaseError, match="modifications suivies"):
        _release(repo)


def test_main_en_retard_sur_origin_est_refuse(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "autre"
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other))
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "test"), ("commit.gpgsign", "false")):
        _git(other, "config", key, value)
    (other / "nouveau.txt").write_text("x\n", encoding="utf-8")
    _commit(other, "docs: ailleurs", "nouveau.txt")
    _git(other, "push", "-q", "origin", "main")
    with pytest.raises(_module().ReleaseError, match="diffère de origin/main"):
        _release(repo)


def test_hors_de_main_est_refuse(repo: Path) -> None:
    _git(repo, "switch", "-q", "-c", "travail")
    with pytest.raises(_module().ReleaseError, match="branche courante est travail"):
        _release(repo)


def test_branche_de_release_existante_est_refusee(repo: Path) -> None:
    _git(repo, "branch", "release/1.3.0")
    with pytest.raises(_module().ReleaseError, match="existe déjà"):
        _release(repo)


@pytest.mark.parametrize("version", ["1.2.0", "1.1.9", "1.2.0-rc1"])
def test_une_version_qui_n_avance_pas_est_refusee(repo: Path, version: str) -> None:
    with pytest.raises(_module().ReleaseError, match="n'avance pas"):
        _release(repo, version)


@pytest.mark.parametrize("version", ["v1.3.0", "1.3", "1.3.0.1", "latest"])
def test_une_version_mal_formee_est_refusee(repo: Path, version: str) -> None:
    with pytest.raises(_module().ReleaseError, match="version invalide"):
        _release(repo, version)


def test_ordre_des_versions() -> None:
    key = _module().version_key
    assert key("1.3.0-rc1") < key("1.3.0") < key("1.3.1") < key("1.10.0")


def test_calibration_perimee_bloque_sans_redatage(repo: Path) -> None:
    with pytest.raises(_module().ReleaseError, match="--redate-ceremony"):
        _release(repo, "1.5.0")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main", "refus avant toute écriture"


def test_redatage_de_la_calibration(repo: Path) -> None:
    code, _ = _release(repo, "1.5.0", redate=True)
    assert code == 0
    text = (repo / "evals/ceremony-budget.yaml").read_text(encoding="utf-8")
    assert 'kit_version: "1.5"' in text and 'as_of: "2026-02-02"' in text
    assert "evals/ceremony-budget.yaml" in _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()


def test_un_changelog_refuse_ne_commite_rien(repo: Path) -> None:
    (repo / "FAIL_CHECK").write_text("", encoding="utf-8")
    with pytest.raises(_module().ReleaseError, match="check-changelog-release"):
        _release(repo)
    assert _git(repo, "log", "-1", "--format=%s") == "fix(y): déjà numéroté (#77)"


def test_la_roue_en_echec_ne_commite_rien(repo: Path) -> None:
    module = _module()

    def broken(_repo: Path, _version: str) -> None:
        raise module.ReleaseError("la roue ne démarre pas")

    with pytest.raises(module.ReleaseError, match="la roue"):
        module.release(repo, "1.3.0", today="2026-02-02", check_wheel=broken)
    assert _git(repo, "log", "-1", "--format=%s") == "fix(y): déjà numéroté (#77)"


def test_main_rend_1_et_dit_comment_repartir(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "FAIL_CHECK").write_text("", encoding="utf-8")
    module = _module()
    assert module.main(["1.3.0", "--repo", str(repo), "--no-fetch"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("::error::") and "git branch -D release/1.3.0" in err


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("- a\n", "- a (#9)\n"),
        ("- a (#3)\n", "- a (#3)\n"),
        ("- a\n  suite\n- b\n", "- a\n  suite (#9)\n- b (#9)\n"),
        ("Texte libre\n- a\n", "Texte libre\n- a (#9)\n"),
    ],
)
def test_numero_de_pr_ajoute_a_chaque_puce(body: str, expected: str) -> None:
    assert _module().with_pr_number(body, "9") == expected


@pytest.mark.skipif(sys.platform == "win32", reason="faux gh en script shell")
def test_open_pr_pousse_la_branche_et_ouvre_la_pr(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh-args"
    fake = bin_dir / "gh"
    fake.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\necho https://github.com/o/r/pull/4242\n', encoding="utf-8"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    code, _ = _release(repo, open_pr=True)
    assert code == 0
    assert _git(repo, "ls-remote", "origin", "release/1.3.0"), "branche poussée sur le remote"
    args = log.read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["pr", "create"] and "chore: release 1.3.0" in args
    assert "--draft" not in args
    out = capsys.readouterr().out
    assert "gh pr merge 4242 --squash" in out
