"""Une release, une seule voie (D-01).

Avant : ``release.yml`` et ``publish.yml`` créaient chacun la release GitHub du
même tag. ``publish-pypi`` étant opt-in, ``github-release`` (qui en dépend) était
ignoré tant que PyPI était désactivé — c'est ``release.yml`` qui écrivait alors
la release, avec un ``git log`` ; PyPI activé, les deux la réécrivaient.

Ces tests simulent la sémantique de GitHub Actions (un job dont un ``needs``
n'a pas réussi est ignoré, sauf si son ``if`` porte une fonction de statut) sur
le vrai ``publish.yml``, et exécutent les deux scripts de la voie unique dans
des dépôts git jetables.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_SCRIPTS = _ROOT / "scripts"
_PUBLISH = _WORKFLOWS / "publish.yml"

pytestmark = pytest.mark.skipif(not _PUBLISH.is_file(), reason="dépôt source uniquement")


def _load_workflows() -> dict[str, dict[str, Any]]:
    yaml = YAML(typ="safe")
    return {p.name: yaml.load(p.read_text(encoding="utf-8")) for p in sorted(_WORKFLOWS.glob("*.yml"))}


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. Un seul créateur de release ───────────────────────────────────────────


def test_exactly_one_workflow_step_creates_the_github_release() -> None:
    creators = [
        (name, job_name)
        for name, wf in _load_workflows().items()
        for job_name, job in (wf.get("jobs") or {}).items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("softprops/action-gh-release")
    ]
    assert creators == [("publish.yml", "github-release")], f"créateurs de release : {creators}"


def test_only_publish_yml_reacts_to_a_release_tag() -> None:
    # YAML 1.1 lit la clé `on` comme le booléen True ; ruamel « safe » suit.
    reacting = [
        name
        for name, wf in _load_workflows().items()
        if "tags" in ((wf.get(True) or wf.get("on") or {}).get("push") or {})
    ]
    assert reacting == ["publish.yml"], f"workflows déclenchés par un tag : {reacting}"


# ── 2. Sémantique des jobs : la release survit à PyPI désactivé ──────────────

_STATUS_FUNCTIONS = re.compile(r"\b(always|cancelled|failure|success)\(\)")


def _evaluate(expr: str, results: dict[str, str], outputs: dict[str, dict[str, str]], variables: dict[str, str]) -> bool:
    text = " ".join(expr.replace("${{", " ").replace("}}", " ").split())
    text = text.replace("!cancelled()", "True")
    text = re.sub(r"needs\.([\w-]+)\.result", lambda m: repr(results.get(m.group(1), "skipped")), text)
    text = re.sub(
        r"needs\.([\w-]+)\.outputs\.([\w-]+)", lambda m: repr(outputs.get(m.group(1), {}).get(m.group(2), "")), text
    )
    text = re.sub(r"vars\.([\w-]+)", lambda m: repr(variables.get(m.group(1), "")), text)
    text = text.replace("&&", " and ").replace("||", " or ")
    assert re.fullmatch(r"[\w\s'()=!.\-]*", text), f"expression non simulée : {text}"
    return bool(eval(text, {"__builtins__": {}}, {}))  # noqa: S307 — expression issue de notre propre workflow


def _simulate(
    stable: bool, *, failing: frozenset[str] = frozenset(), variables: dict[str, str] | None = None
) -> dict[str, str]:
    """Résultat de chaque job de publish.yml pour un tag stable ou pré-version."""
    jobs: dict[str, dict[str, Any]] = _load_workflows()["publish.yml"]["jobs"]
    outputs = {"gates": {"stable": "true" if stable else "false", "version": "9.9.9"}}
    results: dict[str, str] = {}
    pending = list(jobs)
    while pending:
        progressed = False
        for name in list(pending):
            needs = jobs[name].get("needs", [])
            needs = [needs] if isinstance(needs, str) else list(needs)
            if any(n not in results for n in needs):
                continue
            expr = str(jobs[name].get("if", "true"))
            if _STATUS_FUNCTIONS.search(expr) or "!cancelled()" in expr:
                runs = _evaluate(expr, results, outputs, variables or {})
            elif any(results[n] != "success" for n in needs):
                runs = False  # success() implicite
            else:
                runs = _evaluate(expr, results, outputs, variables or {}) if "if" in jobs[name] else True
            results[name] = ("failure" if name in failing else "success") if runs else "skipped"
            pending.remove(name)
            progressed = True
        assert progressed, "cycle de dépendances entre jobs"
    return results


def test_stable_tag_publishes_to_pypi_then_creates_the_release() -> None:
    results = _simulate(stable=True)
    assert results["publish-pypi"] == "success"
    assert results["github-release"] == "success"


def test_release_is_still_created_when_pypi_is_disabled() -> None:
    # PyPI désactivé = pré-version (ou variable absente avant D-01) : la release
    # GitHub ne dépend pas d'un job ignoré.
    results = _simulate(stable=False)
    assert results["publish-pypi"] == "skipped"
    assert results["github-release"] == "success"


@pytest.mark.parametrize("stable", [True, False])
def test_a_failed_smoke_test_blocks_publication_and_release(stable: bool) -> None:
    results = _simulate(stable=stable, failing=frozenset({"test"}))
    assert results["publish-pypi"] == "skipped"
    assert results["github-release"] == "skipped"


def test_a_failed_pypi_publication_leaves_no_release() -> None:
    results = _simulate(stable=True, failing=frozenset({"publish-pypi"}))
    assert results["github-release"] == "skipped"


def test_failed_gates_stop_everything() -> None:
    results = _simulate(stable=True, failing=frozenset({"gates"}))
    assert {n: r for n, r in results.items() if n != "gates"} == {
        "build": "skipped",
        "test": "skipped",
        "attest": "skipped",
        "publish-pypi": "skipped",
        "github-release": "skipped",
    }


@pytest.mark.parametrize("stable", [True, False])
def test_a_failed_attestation_blocks_publication_and_release(stable: bool) -> None:
    # D-03 : sans attestation, ni PyPI ni la release GitHub ne partent.
    results = _simulate(stable=stable, failing=frozenset({"attest"}))
    assert results["publish-pypi"] == "skipped"
    assert results["github-release"] == "skipped"


# ── 3. Les notes sont la section du CHANGELOG validée ────────────────────────


def _release_step() -> dict[str, Any]:
    steps = _load_workflows()["publish.yml"]["jobs"]["github-release"]["steps"]
    return next(s for s in steps if str(s.get("uses", "")).startswith("softprops/action-gh-release"))


def test_release_body_is_the_changelog_file_and_nothing_generated() -> None:
    with_ = _release_step()["with"]
    assert with_.get("body_path") == "RELEASE_NOTES.md"
    assert "body" not in with_, "un corps inline s'ajouterait au fichier"
    assert with_.get("generate_release_notes") is False, "les notes générées s'ajouteraient à la section"


def test_release_notes_file_is_written_by_the_changelog_section_script() -> None:
    steps = _load_workflows()["publish.yml"]["jobs"]["github-release"]["steps"]
    writers = [s for s in steps if "changelog-section.py" in str(s.get("run", ""))]
    assert writers and all("RELEASE_NOTES.md" in s["run"] for s in writers)
    assert steps.index(writers[0]) < steps.index(_release_step())


def test_the_gates_run_the_changelog_and_tag_checks_before_any_build() -> None:
    jobs = _load_workflows()["publish.yml"]["jobs"]
    runs = " ".join(str(s.get("run", "")) for s in jobs["gates"]["steps"])
    for script in ("check-release-tag.py", "check-changelog-release.py", "gen-kit-hashes.py --check"):
        assert script in runs, f"{script} absent des garde-fous"
    needs = jobs["build"]["needs"]
    assert "gates" in ([needs] if isinstance(needs, str) else needs)


# ── 4. scripts/changelog-section.py ──────────────────────────────────────────

_CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-02

- feat: la deux.
  - détail

## [1.1.0] - 2026-01-01

- fix: la une.

## [1.0.0-rc1] - 2025-12-31

- chore: pré-version.
"""


def test_section_body_is_exactly_the_section_without_its_heading() -> None:
    mod = _load_script("changelog-section")
    assert mod.section_body(_CHANGELOG, "1.2.0") == "- feat: la deux.\n  - détail"
    assert mod.section_body(_CHANGELOG, "1.1.0") == "- fix: la une."
    assert mod.section_body(_CHANGELOG, "1.0.0-rc1") == "- chore: pré-version."


def test_section_body_is_none_for_an_unknown_version() -> None:
    assert _load_script("changelog-section").section_body(_CHANGELOG, "9.9.9") is None


def test_script_refuses_a_missing_or_empty_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script("changelog-section")
    log = tmp_path / "CHANGELOG.md"
    log.write_text(_CHANGELOG, encoding="utf-8")
    assert mod.main(["9.9.9", "--changelog", str(log)]) == 1
    assert mod.main(["Unreleased", "--changelog", str(log)]) == 1  # section vide
    assert "9.9.9" in capsys.readouterr().err


def test_script_writes_the_section_and_accepts_a_v_prefix(tmp_path: Path) -> None:
    mod = _load_script("changelog-section")
    log = tmp_path / "CHANGELOG.md"
    log.write_text(_CHANGELOG, encoding="utf-8")
    out = tmp_path / "RELEASE_NOTES.md"
    assert mod.main(["v1.2.0", "--changelog", str(log), "--output", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "- feat: la deux.\n  - détail\n"


def _utf16_units(text: str) -> int:
    """Indépendant du script : ce que ``String.prototype.substring`` compte."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)


def _log_with_body_of(tmp_path: Path, units: int, filler: str = "a") -> Path:
    """Un CHANGELOG dont la section 9.9.9 pèse, une fois écrite (corps + saut de ligne), ``units`` unités UTF-16."""
    per_char = 2 if ord(filler) > 0xFFFF else 1
    count = (units - 1) // per_char  # -1 : le "\n" final
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Changelog\n\n## [9.9.9] - 2026-01-01\n\n" + filler * count + "\n", encoding="utf-8")
    return log


@pytest.mark.parametrize(
    ("filler", "units"),
    [("a", 124_998), ("a", 124_999), ("\U0001f4a1", 124_997)],
    ids=["bmp-sous-le-seuil", "bmp-au-seuil", "hors-bmp-sous-le-seuil"],
)
def test_script_accepts_a_body_the_release_action_would_keep_whole(tmp_path: Path, filler: str, units: int) -> None:
    mod = _load_script("changelog-section")
    log = _log_with_body_of(tmp_path, units, filler)
    out = tmp_path / "RELEASE_NOTES.md"
    assert mod.main(["9.9.9", "--changelog", str(log), "--output", str(out)]) == 0
    assert _utf16_units(out.read_text(encoding="utf-8")) == units


@pytest.mark.parametrize(
    ("filler", "units"),
    [("a", 125_000), ("a", 139_761), ("\U0001f4a1", 125_001)],
    ids=["bmp-juste-au-dessus", "bmp-le-cas-reproduit", "hors-bmp-juste-au-dessus"],
)
def test_script_refuses_a_body_the_release_action_would_silently_truncate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filler: str, units: int
) -> None:
    mod = _load_script("changelog-section")
    log = _log_with_body_of(tmp_path, units, filler)
    out = tmp_path / "RELEASE_NOTES.md"
    assert mod.main(["9.9.9", "--changelog", str(log), "--output", str(out)]) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert err.startswith("::error::") and "124999" in err


def test_script_counts_utf16_units_not_python_characters(tmp_path: Path) -> None:
    """62 500 caractères hors BMP = 125 000 unités : sous 124 999 en ``len()``, mais la release les couperait."""
    mod = _load_script("changelog-section")
    log = tmp_path / "CHANGELOG.md"
    log.write_text("## [9.9.9] - 2026-01-01\n\n" + "\U0001f4a1" * 62_500 + "\n", encoding="utf-8")
    assert mod.main(["9.9.9", "--changelog", str(log)]) == 1


def test_the_real_changelog_has_a_section_for_the_current_version() -> None:
    mod = _load_script("changelog-section")
    version = (_ROOT / "version.txt").read_text(encoding="utf-8").strip()
    body = mod.section_body((_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    assert body, f"CHANGELOG.md n'a pas de section non vide pour {version}"
    assert not body.startswith("## ")
    assert _utf16_units(body + "\n") <= 124_999



# ── 5. scripts/check-release-tag.py ──────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, version: str, message: str) -> None:
    (repo / "version.txt").write_text(version + "\n", encoding="utf-8")
    _git(repo, "add", "version.txt")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Un dépôt avec `origin/main` simulé par une référence de suivi."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    _commit(root, "1.2.3", "release")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def test_tag_on_main_matching_version_is_accepted(repo: Path, tmp_path: Path) -> None:
    mod = _load_script("check-release-tag")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "Release 1.2.3")
    out = tmp_path / "gh_output"
    assert mod.main(["--repo", str(repo), "--tag", "v1.2.3", "--github-output", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "version=1.2.3\nstable=true\n"


def test_prerelease_tag_is_accepted_and_not_stable(repo: Path, tmp_path: Path) -> None:
    mod = _load_script("check-release-tag")
    _commit(repo, "1.3.0-rc1", "rc")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "tag", "v1.3.0-rc1")
    out = tmp_path / "gh_output"
    assert mod.main(["--repo", str(repo), "--tag", "v1.3.0-rc1", "--github-output", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "version=1.3.0-rc1\nstable=false\n"


def test_tag_differing_from_version_txt_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script("check-release-tag")
    _git(repo, "tag", "v1.2.4")
    assert mod.main(["--repo", str(repo), "--tag", "v1.2.4"]) == 1
    assert "version.txt" in capsys.readouterr().err


def test_tag_on_a_commit_outside_main_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script("check-release-tag")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "1.2.4", "branche de travail")
    _git(repo, "tag", "-a", "v1.2.4", "-m", "hors main")
    assert mod.main(["--repo", str(repo), "--tag", "v1.2.4"]) == 1
    assert "n'est pas sur origin/main" in capsys.readouterr().err


def test_missing_main_ref_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script("check-release-tag")
    _git(repo, "tag", "v1.2.3")
    assert mod.main(["--repo", str(repo), "--tag", "v1.2.3", "--main-ref", "origin/inconnue"]) == 1
    assert "introuvable" in capsys.readouterr().err


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3.4", "v1.2.3-", "latest"])
def test_malformed_tags_fail(repo: Path, tag: str) -> None:
    mod = _load_script("check-release-tag")
    assert mod.problems(repo, tag, "origin/main")


# ── 6. Ce que l'arbre ne peut pas garantir : les contrôles hors arbre ────────
#
# Les gardes de `publish.yml` vivent dans l'arbre du tag : un arbre antérieur au
# merge (les branches aidlc/* en contiennent déjà) garde l'ancien `release.yml`
# et l'ancien `publish.yml` conditionné par `vars.GRIMOIRE_ENABLE_PYPI`, et un
# arbre taggué peut modifier le garde qui le surveille. Aucun test de code ne
# peut le fermer : c'est de la configuration GitHub. Ce qu'un test peut tenir,
# c'est que la doc ne laisse pas croire le contraire et nomme les actions.


def _contributing_release_section() -> str:
    text = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    start = text.find("### Contrôles hors arbre")
    assert start != -1, "CONTRIBUTING.md ne nomme pas les contrôles hors arbre de la voie de release"
    end = text.find("\n#", start + 1)
    return text[start : end if end != -1 else len(text)]


def _changelog_d01_entry() -> str:
    # L'entrée vit dans le CHANGELOG tant que la note de sa version tient sous la
    # limite de GitHub ; sinon dans le détail intégral de la version (3.63.0).
    text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    detail = _ROOT / "docs" / "_archive" / "changelog-3.63.0-detail.md"
    if detail.is_file():
        text += "\n" + detail.read_text(encoding="utf-8")
    start = text.index("- fix(release): une seule voie de release")
    end = text.find("\n## [", start)
    return text[start : end if end != -1 else len(text)]


def test_contributing_lists_the_out_of_tree_actions_to_take_after_the_merge() -> None:
    section = _contributing_release_section()
    # 1. la variable qui rouvre PyPI aux anciens arbres
    assert "GRIMOIRE_ENABLE_PYPI" in section and "supprimer" in section.lower()
    # 2. l'environnement `pypi` : restreint aux tags v*, sans prétendre vérifier main
    assert "`pypi`" in section and "v*" in section
    assert "ne vérifie pas" in section or "ne peut pas vérifier" in section
    # 3. le seul vrai contrôle : une règle sur les tags
    assert "refs/tags/v*" in section and "ruleset" in section.lower()
    # 4. les branches antérieures au merge sont rebasées avant tout tag
    assert "aidlc/*" in section and "rebas" in section.lower()


def test_contributing_says_the_guarantees_only_hold_for_trees_containing_the_change() -> None:
    section = _contributing_release_section()
    assert "contient" in section and "voie unique" in section
    assert "désarmer" in section or "désarme" in section, (
        "un arbre taggué peut modifier le garde qui le surveille : la doc doit le dire"
    )


def test_changelog_entry_does_not_overstate_the_guarantee() -> None:
    entry = _changelog_d01_entry()
    assert "GRIMOIRE_ENABLE_PYPI" in entry and "refs/tags/v*" in entry
    assert "contiennent" in entry or "contient" in entry
    assert "désarm" in entry


# ── 7. Une pré-version doit pouvoir traverser les deux portes ────────────────
#
# `check-release-tag.py` accepte `vX.Y.Z-rc1` ; `tests/unit/test_version_sync.py`
# tourne sur chaque pull request et exigeait trois champs numériques. Aucune
# graphie de pré-version ne passait donc les deux : la voie « release GitHub
# sans PyPI » existait dans le workflow et n'était atteignable par aucun tag.
# Ces tests rejouent les deux portes ensemble, sur la même version, dans un
# dépôt jetable (le vrai `test_version_sync.py` et le vrai garde y sont copiés).


def _gate_repo(root: Path, version: str) -> Path:
    """Un dépôt jetable qui porte ``version`` partout et le tag ``v<version>`` sur ``origin/main``."""
    (root / "grimoire").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "grimoire" / "__init__.py").write_text("", encoding="utf-8")
    (root / "grimoire" / "__version__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "version.txt").write_text(version + "\n", encoding="utf-8")
    for rel in ("tests/unit/test_version_sync.py", "scripts/check-release-tag.py"):
        (root / rel).write_text((_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "release")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "tag", f"v{version}")
    return root


def _version_sync_passes(root: Path) -> bool:
    """Le test de synchronisation des versions, tel que la CI le lance sur chaque PR."""
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_version_sync.py", "-q", "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
    )
    return done.returncode == 0


def _tag_gate_passes(root: Path, version: str) -> bool:
    done = subprocess.run(
        [sys.executable, str(root / "scripts" / "check-release-tag.py"), "--repo", str(root), "--tag", f"v{version}"],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
    )
    return done.returncode == 0


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : rejoue un garde de workflow écrit pour bash sous ubuntu-latest (CI) ; Git Bash de Windows (CRLF, chemins, sortie redirigée) n'est pas la cible de ce garde"
    ),
)
@pytest.mark.parametrize("version", ["3.63.0", "3.63.0-rc1"])
def test_a_release_version_crosses_both_gates(tmp_path: Path, version: str) -> None:
    root = _gate_repo(tmp_path / "r", version)
    assert _tag_gate_passes(root, version), "le garde de tag refuse la version"
    assert _version_sync_passes(root), "test_version_sync refuse la version que le garde de tag accepte"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : rejoue un garde de workflow écrit pour bash sous ubuntu-latest (CI) ; Git Bash de Windows (CRLF, chemins, sortie redirigée) n'est pas la cible de ce garde"
    ),
)
@pytest.mark.parametrize("version", ["3.63.0", "3.63.0-rc1", "3.63.0rc1", "3.63", "3.63.0-"])
def test_the_two_gates_agree_on_every_spelling(tmp_path: Path, version: str) -> None:
    root = _gate_repo(tmp_path / "r", version)
    assert _version_sync_passes(root) == _tag_gate_passes(root, version)


# ── 8. Reprise après un téléversement PyPI partiel ───────────────────────────
#
# Un téléversement PyPI peut s'arrêter entre le sdist et la wheel. Relancer le
# job échoue alors sur le fichier déjà reçu (HTTP 400) tant que l'action n'a
# pas `skip-existing: true` ; `github-release` dépend de `publish-pypi`, donc
# la release ne se crée plus, et il n'existe plus de second workflow pour la
# rattraper (`release.yml` avait un `workflow_dispatch`).


def _pypi_step() -> dict[str, Any]:
    steps = _load_workflows()["publish.yml"]["jobs"]["publish-pypi"]["steps"]
    return next(s for s in steps if str(s.get("uses", "")).startswith("pypa/gh-action-pypi-publish"))


def test_pypi_step_skips_files_already_uploaded() -> None:
    options = _pypi_step().get("with") or {}
    assert str(options.get("skip-existing", "false")).lower() == "true"


def test_rerun_after_a_partial_pypi_upload_still_creates_the_release() -> None:
    # PyPI refuse un fichier déjà présent ; sans skip-existing, la reprise échoue.
    skips = str((_pypi_step().get("with") or {}).get("skip-existing", "false")).lower() == "true"
    rerun_fails = frozenset() if skips else frozenset({"publish-pypi"})
    results = _simulate(stable=True, failing=rerun_fails)
    assert results["publish-pypi"] == "success"
    assert results["github-release"] == "success"


# ── 9. Le Makefile n'ouvre pas une seconde voie vers PyPI ────────────────────
#
# `make release` annonçait « Then publish to PyPI: make publish », et la cible
# `publish` lançait `twine upload dist/*` sans garde de stabilité, de tag sur
# main, de CHANGELOG ni de test de la wheel. Un fichier ainsi envoyé serait en
# outre sauté sans erreur par `skip-existing` de publish.yml.

_MAKEFILE = _ROOT / "Makefile"


def _makefile_code() -> str:
    """Le Makefile sans ses lignes de commentaire (la doc peut nommer l'interdit)."""
    lines = _MAKEFILE.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _recipe(target: str) -> str:
    out: list[str] = []
    active = False
    for line in _makefile_code().splitlines():
        if re.match(rf"^{re.escape(target)}\s*:", line):
            active = True
            continue
        if active and line.startswith("\t"):
            out.append(line)
        elif active and line.strip():
            break
    return "\n".join(out)


@pytest.mark.skipif(not _MAKEFILE.is_file(), reason="dépôt source uniquement")
def test_makefile_never_uploads_to_pypi() -> None:
    assert not re.search(r"\btwine\b", _makefile_code()), "le Makefile téléverse sur PyPI hors de publish.yml"


@pytest.mark.skipif(not _MAKEFILE.is_file(), reason="dépôt source uniquement")
def test_make_release_does_not_advertise_a_manual_publish() -> None:
    recipe = _recipe("release")
    assert recipe, "recette `release` introuvable"
    assert not re.search(r"make\s+publish|publish to pypi", recipe, re.IGNORECASE), recipe


@pytest.mark.skipif(not _MAKEFILE.is_file() or shutil.which("make") is None, reason="make absent")
def test_make_publish_refuses_and_points_to_the_tag_path() -> None:
    # Exécution réelle (`-n` n'exécute pas la recette, donc ne prouve pas le refus) ;
    # PYTHON=true neutralise tout téléversement si une voie manuelle revenait.
    run = subprocess.run(
        ["make", "-f", str(_MAKEFILE), "PYTHON=true", "publish"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert run.returncode != 0, "make publish réussit : une voie manuelle reste ouverte"
    assert "publish.yml" in run.stdout + run.stderr
