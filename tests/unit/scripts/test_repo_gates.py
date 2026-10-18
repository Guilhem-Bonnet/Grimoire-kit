"""``grimoire gates repo`` — chaque garde refuse-t-elle vraiment, et la CI lance-t-elle la même commande ?

Un garde-fou sans contrôle négatif n'est pas un garde-fou, c'est une intention.
Ce fichier monte un faux clone du kit (copie des vrais scripts, dépôt git jetable)
qui passe les neuf gardes, puis le casse une garde à la fois : chaque casse doit
rendre **sa** garde rouge, et la contre-épreuve (le clone sain) doit les passer
toutes — sinon une garde qui refuserait tout passerait pour vigilante.

Ce qui est vérifié ici, pour de vrai, par sous-processus : le script de la garde
tourne depuis le clone jetable, avec son ``ROOT`` à lui. Rien n'est simulé.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from grimoire import repo_gates
from grimoire.cli.app import app

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"

runner = CliRunner()


# ── Un faux clone du kit, sain ───────────────────────────────────────────────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8")


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=test", "commit", "-q", "-m", message)


def build_kit(root: Path) -> Path:
    """Un clone du kit minimal qui passe les neuf gardes, sur une branche de travail sans commit propre."""
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "scripts").mkdir()
    for gate in repo_gates.GATES:
        shutil.copy(SCRIPTS / gate.script, root / "scripts" / gate.script)
    shutil.copy(SCRIPTS / "emitted-prose-allowlist.txt", root / "scripts" / "emitted-prose-allowlist.txt")
    _write(root, "scripts/code-ratchet-baseline.json", json.dumps({"frozen": {}, "src_oversized": {}}))
    _write(root, "version.txt", "1.0.0\n")
    _write(root, "src/grimoire/__init__.py", "")
    _write(root, "src/grimoire/core/scaffold.py", "_PROTOCOL_DOCS: ClassVar[tuple[str, ...]] = ()\n")
    _write(root, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n- **Départ.** Rien.\n")
    _write(root, ".github/workflows/ci.yml", f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{SHA}  # v7\n")
    _write(root, "tests/unit/test_a.py", "def test_a() -> None:\n    pass\n")
    tenets = "\n".join(f"### Tenet {n} — principe {n}\n\nTexte.\n\nTenu par : `tests/unit/test_a.py`.\n" for n in (1, 2, 3))
    _write(root, "CONTRIBUTING.md", f"# Contributing\n\n## Tenets\n\n{tenets}\n## Suite\n\nRien.\n")
    ids = ", ".join(f"`{gate_id}`" for gate_id in repo_gates.GATE_IDS)
    _write(root, ".github/PULL_REQUEST_TEMPLATE.md", f"- [ ] `grimoire gates repo` passe : {ids}\n")
    _write(root, "Makefile", "gates:\n\techo ok\n")
    for page in ("docs/index.md", "docs/guide.md"):
        _write(root, page, "---\naudience: utilisateur\n---\n\n# Page\n\nRien de chiffré.\n")
    _write(root, "README.md", "# Kit\n\nRien de chiffré.\n")
    _write(root, "README.fr.md", "# Kit\n\nRien de chiffré.\n")
    _commit(root, "chore: départ")
    _git(root, "checkout", "-q", "-b", "travail")
    return root


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    return build_kit(tmp_path / "kit")


# ── Contre-épreuve : le clone sain passe tout ────────────────────────────────────


def test_a_sound_kit_passes_every_gate(kit: Path) -> None:
    results = repo_gates.run_repo_gates(kit)
    assert [r.id for r in results] == list(repo_gates.GATE_IDS)
    assert all(r.status == "passed" for r in results), [(r.id, r.detail) for r in results if r.status != "passed"]
    assert repo_gates.all_passed(results)


# ── Une casse par garde : chacune doit rougir SA garde ───────────────────────────


def _break_ratchet(kit: Path) -> None:
    _write(kit, "framework/nouveau.py", "x = 1\n")
    _git(kit, "add", "framework/nouveau.py")


def _break_changelog(kit: Path) -> None:
    _write(kit, "src/grimoire/nouveau.py", "x = 1\n")
    _commit(kit, "feat: une fonctionnalité sans entrée de CHANGELOG")


def _break_prose(kit: Path) -> None:
    _write(kit, "framework/agent-base.md", "Score global : X/10\n")


def _break_waivers(kit: Path) -> None:
    _write(
        kit,
        ".github/security/dependency-waivers.yaml",
        'waivers:\n  - waiver_id: "W1"\n    vulnerability_id: "CVE-1"\n    reason: "r"\n'
        '    approved_by: "g"\n    expires_at: "2020-01-01"\n',
    )


def _break_actions(kit: Path) -> None:
    _write(kit, ".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")


def _break_tenets(kit: Path) -> None:
    (kit / "tests/unit/test_a.py").unlink()


def _break_doc_audience(kit: Path) -> None:
    _write(kit, "docs/sans-public.md", "# Sans front-matter\n")


def _break_doc_commands(kit: Path) -> None:
    _write(kit, "docs/guide.md", "---\naudience: utilisateur\n---\n\nLancer `grimoire commande-qui-nexiste-pas`.\n")


def _break_doc_claims(kit: Path) -> None:
    _write(kit, "README.md", "# Kit\n\nUn taux de réussite de 99 % mesuré.\n")


BREAKS: dict[str, Callable[[Path], None]] = {
    "ratchet": _break_ratchet,
    "changelog": _break_changelog,
    "prose": _break_prose,
    "waivers": _break_waivers,
    "actions-pinned": _break_actions,
    "tenets": _break_tenets,
    "doc-audience": _break_doc_audience,
    "doc-commands": _break_doc_commands,
    "doc-claims": _break_doc_claims,
}


def test_every_registered_gate_has_a_negative_case() -> None:
    """Aucune garde sans test négatif : ajouter une garde au registre oblige à écrire sa casse."""
    assert set(BREAKS) == set(repo_gates.GATE_IDS)


@pytest.mark.parametrize("gate_id", repo_gates.GATE_IDS)
def test_each_gate_refuses_its_own_violation(kit: Path, gate_id: str) -> None:
    BREAKS[gate_id](kit)
    results = {r.id: r for r in repo_gates.run_repo_gates(kit)}
    assert results[gate_id].status == "failed", results[gate_id].detail
    assert results[gate_id].detail, "une garde qui refuse dit pourquoi"
    others = [r.id for r in results.values() if r.id != gate_id and r.status == "failed"]
    assert others == [], f"la casse de {gate_id} ne devait pas rougir {others}"


# ── Fail-closed : une garde qui ne peut pas juger ne passe pas ───────────────────


def test_a_missing_script_is_unevaluated_and_fails_the_run(kit: Path) -> None:
    (kit / "scripts" / "check-code-ratchet.py").unlink()
    results = {r.id: r for r in repo_gates.run_repo_gates(kit)}
    assert results["ratchet"].status == "unevaluated"
    assert "absent" in results["ratchet"].detail
    assert not repo_gates.all_passed(list(results.values()))


def test_a_timeout_is_unevaluated_not_passed(kit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(repo_gates.subprocess, "run", hang)
    result = repo_gates.run_gate(repo_gates.GATES[0], kit)
    assert result.status == "unevaluated"


def test_a_spawn_failure_is_unevaluated_not_passed(kit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError("pas d'interpréteur")

    monkeypatch.setattr(repo_gates.subprocess, "run", refuse)
    assert repo_gates.run_gate(repo_gates.GATES[0], kit).status == "unevaluated"


def test_an_empty_run_is_not_a_pass() -> None:
    assert repo_gates.all_passed([]) is False


def test_unknown_gate_and_profile_are_errors_not_empty_filters(kit: Path) -> None:
    with pytest.raises(ValueError, match="garde inconnue"):
        repo_gates.run_repo_gates(kit, only=["nope"])
    with pytest.raises(ValueError, match="profil inconnu"):
        repo_gates.run_repo_gates(kit, profile="prod")


def test_release_profile_adds_the_unreleased_check(kit: Path) -> None:
    _write(kit, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- **Reste.** Là.\n\n## [1.0.0] - 2026-01-01\n")
    dev = repo_gates.run_repo_gates(kit, profile="dev", only=["changelog"])[0]
    release = repo_gates.run_repo_gates(kit, profile="release", only=["changelog"])[0]
    assert dev.status == "passed", dev.detail
    assert release.status == "failed"
    assert "Unreleased" in release.detail


# ── La commande ─────────────────────────────────────────────────────────────────


def test_cli_exit_codes(kit: Path) -> None:
    ok = runner.invoke(app, ["gates", "repo", "--root", str(kit), "--only", "doc-audience"])
    assert ok.exit_code == 0, ok.output
    _break_doc_audience(kit)
    bad = runner.invoke(app, ["gates", "repo", "--root", str(kit), "--only", "doc-audience"])
    assert bad.exit_code == 1, bad.output
    assert "sans-public.md" in bad.output


def test_cli_json_reports_every_gate(kit: Path) -> None:
    result = runner.invoke(app, ["gates", "repo", "--root", str(kit), "--only", "doc-audience", "--json"])
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert [r["id"] for r in payload["results"]] == ["doc-audience"]


def test_cli_outside_a_kit_checkout_says_so_instead_of_passing(tmp_path: Path) -> None:
    result = runner.invoke(app, ["gates", "repo", "--root", str(tmp_path)])
    assert result.exit_code == 2
    assert "rien à garder" in result.output


def test_cli_rejects_an_unknown_gate(kit: Path) -> None:
    result = runner.invoke(app, ["gates", "repo", "--root", str(kit), "--only", "nope"])
    assert result.exit_code == 2


# ── Même commande en local et en CI ──────────────────────────────────────────────


def _yaml(path: Path) -> dict[str, object]:
    loaded = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _precommit_command() -> str:
    config = _yaml(REPO / ".pre-commit-config.yaml")
    hooks = [
        hook
        for repo in config["repos"]  # type: ignore[attr-defined]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "repo-gates"
    ]
    assert len(hooks) == 1, "un seul hook `repo-gates` dans .pre-commit-config.yaml"
    assert hooks[0].get("pass_filenames") is False and hooks[0].get("always_run") is True
    return str(hooks[0]["entry"]).strip()


def _ci_command() -> str:
    workflow = _yaml(REPO / ".github" / "workflows" / "ci-sdk.yml")
    steps = workflow["jobs"]["lint"]["steps"]  # type: ignore[index]
    runs = [str(step["run"]).strip() for step in steps if step.get("name") == "Repo gates"]
    assert len(runs) == 1, "une seule étape `Repo gates` dans le job lint de ci-sdk.yml"
    return runs[0]


def test_precommit_and_ci_call_the_same_command() -> None:
    assert _precommit_command() == _ci_command() == "grimoire gates repo"


def test_ci_lint_job_reads_the_full_history() -> None:
    """Sans historique, la garde `changelog` ne trouve pas sa base : elle échouerait, ou pire, serait contournée."""
    workflow = _yaml(REPO / ".github" / "workflows" / "ci-sdk.yml")
    checkout = workflow["jobs"]["lint"]["steps"][0]  # type: ignore[index]
    assert checkout["with"]["fetch-depth"] == 0


def test_the_makefile_exposes_the_same_gates() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^gates:.*\n\t.*gates repo", makefile)


def test_every_registered_script_exists_in_the_repo() -> None:
    for gate in repo_gates.GATES:
        assert (SCRIPTS / gate.script).is_file(), gate.script


def test_the_real_repo_is_a_kit_checkout() -> None:
    assert repo_gates.is_kit_checkout(REPO)
    assert repo_gates.find_kit_root(REPO / "src" / "grimoire") == REPO
    assert sys.executable  # le sous-processus des gardes réutilise cet interpréteur
