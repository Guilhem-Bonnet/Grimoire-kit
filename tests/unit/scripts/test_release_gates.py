"""Les garde-fous de release refusent-ils vraiment ?

Trois scripts bloquent une publication : le ratchet de taille, la couverture du
catalogue de contenu livré, et la cohérence du CHANGELOG. Aucun des trois
n'avait de test — et deux ont été écrits après qu'une release soit partie avec
le défaut qu'ils prétendent attraper.

Un garde-fou sans contrôle négatif n'est pas un garde-fou, c'est une intention.
Le dépôt en a la démonstration : le job *Framework Tools Tests* a passé des
semaines au vert sans rien exécuter, parce que personne n'avait vérifié qu'il
savait échouer.

Chaque test ici construit l'état que le garde-fou prétend refuser, et vérifie
qu'il **refuse**. Le cas nominal n'est vérifié qu'en contre-épreuve, pour
qu'un garde-fou qui refuserait tout ne passe pas non plus.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    """Importe un script au nom tirets, que `import` ne sait pas atteindre."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(root: Path) -> None:
    """Un dépôt minimal : les trois scripts interrogent `git ls-files`."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _track(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", rel], cwd=root, check=True)
    return path


# ── R1/R2 — le ratchet de taille ──────────────────────────────────────────────


@pytest.fixture
def ratchet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Le ratchet, pointé sur un dépôt jetable au lieu du vrai."""
    root = tmp_path / "repo"
    _git_repo(root)
    module = _load("check-code-ratchet")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "BASELINE", root / "scripts" / "code-ratchet-baseline.json")
    return module


def _baseline(module: ModuleType, frozen: dict[str, int], oversized: dict[str, int]) -> None:
    module.BASELINE.parent.mkdir(parents=True, exist_ok=True)
    module.BASELINE.write_text(
        json.dumps({"frozen": frozen, "src_oversized": oversized}), encoding="utf-8"
    )


class TestRatchetRefuses:
    def test_a_frozen_file_that_grew(self, ratchet: ModuleType) -> None:
        """R1 : la zone gelée ne peut que décroître (framework/FREEZE.md)."""
        _track(ratchet.ROOT, "framework/tools/legacy.py", "a\nb\nc\n")
        _baseline(ratchet, {"framework/tools/legacy.py": 2}, {})
        assert ratchet.verify() == 1

    def test_a_new_file_in_the_frozen_zone(self, ratchet: ModuleType) -> None:
        """R1 : toute nouvelle capacité vit sous src/, pas dans framework/."""
        _track(ratchet.ROOT, "framework/tools/nouveau.py", "x\n")
        _baseline(ratchet, {}, {})
        assert ratchet.verify() == 1

    def test_a_grandfathered_src_file_that_grew(self, ratchet: ModuleType) -> None:
        """R2 : un module déjà trop gros s'extrait, il ne s'allonge pas."""
        _track(ratchet.ROOT, "src/grimoire/gros.py", "x\n" * 1600)
        _baseline(ratchet, {}, {"src/grimoire/gros.py": 1550})
        assert ratchet.verify() == 1

    def test_a_src_file_crossing_the_threshold_unannounced(self, ratchet: ModuleType) -> None:
        _track(ratchet.ROOT, "src/grimoire/neuf.py", "x\n" * 1600)
        _baseline(ratchet, {}, {})
        assert ratchet.verify() == 1


class TestRatchetAccepts:
    def test_a_frozen_file_that_shrank(self, ratchet: ModuleType) -> None:
        """Contre-épreuve : réduire est le but, pas une infraction."""
        _track(ratchet.ROOT, "framework/tools/legacy.py", "a\n")
        _baseline(ratchet, {"framework/tools/legacy.py": 40}, {})
        assert ratchet.verify() == 0

    def test_a_src_file_under_the_threshold(self, ratchet: ModuleType) -> None:
        _track(ratchet.ROOT, "src/grimoire/petit.py", "x\n" * 10)
        _baseline(ratchet, {}, {})
        assert ratchet.verify() == 0


# ── Le catalogue de contenu livré ─────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    root = tmp_path / "kit"
    _git_repo(root)
    (root / "version.txt").write_text("9.9.9\n", encoding="utf-8")
    module = _load("gen-kit-hashes")
    monkeypatch.setattr(module, "REPO", root)
    monkeypatch.setattr(module, "OUT", root / "registry" / "kit-file-hashes.json")
    return module


def _catalogued(module: ModuleType, paths: dict[str, str]) -> None:
    digests = {
        module._digest(content.encode("utf-8")): {"version": "1.0.0", "path": rel}
        for rel, content in paths.items()
    }
    module.OUT.parent.mkdir(parents=True, exist_ok=True)
    module.OUT.write_text(json.dumps({"digests": digests}), encoding="utf-8")


class TestCatalogRefuses:
    def test_a_shipped_file_absent_from_the_catalog(
        self, catalog: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Un digest inconnu, et `grimoire migrate` gèle le fichier hors des mises à jour."""
        _track(catalog.REPO, "framework/nouveau.md", "livré mais non catalogué\n")
        _catalogued(catalog, {})
        assert catalog._check({}, "9.9.9") == 1
        assert "framework/nouveau.md" in capsys.readouterr().err

    def test_a_shipped_file_whose_content_changed(self, catalog: ModuleType) -> None:
        """Le catalogue est indexé par digest : un contenu modifié est un inconnu."""
        _track(catalog.REPO, "framework/outil.md", "version deux\n")
        _catalogued(catalog, {"framework/outil.md": "version un\n"})
        cat = json.loads(catalog.OUT.read_text(encoding="utf-8"))["digests"]
        assert catalog._check(cat, "9.9.9") == 1


class TestCatalogAccepts:
    def test_a_fully_catalogued_tree(self, catalog: ModuleType) -> None:
        _track(catalog.REPO, "framework/outil.md", "contenu\n")
        _catalogued(catalog, {"framework/outil.md": "contenu\n"})
        cat = json.loads(catalog.OUT.read_text(encoding="utf-8"))["digests"]
        assert catalog._check(cat, "9.9.9") == 0

    def test_an_untracked_file_is_not_catalogued(self, catalog: ModuleType) -> None:
        """Régression : un `__pycache__` laissé par des tests injectait des
        digests de bytecode, propres à une version de Python. 304 s'étaient
        accumulées, dont 249 depuis la 3.32.0."""
        (catalog.REPO / "framework").mkdir(parents=True, exist_ok=True)
        (catalog.REPO / "framework" / "__pycache__").mkdir()
        (catalog.REPO / "framework" / "__pycache__" / "x.pyc").write_bytes(b"\x00bytecode")
        _track(catalog.REPO, "framework/outil.md", "contenu\n")
        _catalogued(catalog, {"framework/outil.md": "contenu\n"})
        cat = json.loads(catalog.OUT.read_text(encoding="utf-8"))["digests"]
        assert catalog._check(cat, "9.9.9") == 0


# ── Le CHANGELOG décrit-il la version publiée ? ───────────────────────────────

_HEADER = "# Changelog\n\n"


@pytest.fixture
def changelog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    root = tmp_path / "kit"
    root.mkdir()
    module = _load("check-changelog-release")
    monkeypatch.setattr(module, "REPO", root)
    monkeypatch.setattr(module, "CHANGELOG", root / "CHANGELOG.md")
    monkeypatch.setattr(module, "VERSION_FILE", root / "version.txt")
    return module


def _state(module: ModuleType, version: str, body: str) -> None:
    module.VERSION_FILE.write_text(f"{version}\n", encoding="utf-8")
    module.CHANGELOG.write_text(_HEADER + body, encoding="utf-8")


class TestChangelogRefuses:
    def test_entries_left_under_unreleased(
        self, changelog: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Elles partiraient dans le tag en étant annoncées « à venir »."""
        _state(
            changelog,
            "3.34.2",
            "## [Unreleased]\n\n- un correctif oublié\n\n## [3.34.2] - 2026-08-28\n\n- livré\n",
        )
        assert changelog.main() == 1
        assert "Unreleased" in capsys.readouterr().err

    def test_a_newest_section_that_is_not_the_version(
        self, changelog: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Le cas réel de la 3.34.0 : la note publiée décrivait une autre version."""
        _state(changelog, "3.34.0", "## [Unreleased]\n\n## [3.33.0] - 2026-08-27\n\n- livré\n")
        assert changelog.main() == 1
        assert "3.33.0" in capsys.readouterr().err

    def test_a_changelog_without_any_released_section(self, changelog: ModuleType) -> None:
        _state(changelog, "3.34.2", "## [Unreleased]\n")
        assert changelog.main() == 1


class TestChangelogAccepts:
    def test_a_closed_section_matching_the_version(self, changelog: ModuleType) -> None:
        _state(changelog, "3.34.2", "## [Unreleased]\n\n## [3.34.2] - 2026-08-28\n\n- livré\n")
        assert changelog.main() == 0

    def test_no_unreleased_heading_at_all(self, changelog: ModuleType) -> None:
        """Certains dépôts n'en tiennent pas : l'absence n'est pas une faute."""
        _state(changelog, "3.34.2", "## [3.34.2] - 2026-08-28\n\n- livré\n")
        assert changelog.main() == 0


# ── Le titre de PR, dont dépendent la note et le numéro de version ────────────


@pytest.mark.parametrize(
    "title",
    [
        "L0 — Un seul serveur cockpit, project-scoped",  # cas réel, PR #143
        "wip",
        "feature: pas un préfixe conventionnel",
        "feat:manque l'espace",
        "",
    ],
)
def test_pr_title_gate_refuses(title: str) -> None:
    """Hors convention, une PR sort sans catégorie et compte pour un correctif
    même si elle livre une fonctionnalité — panne muette dans un artefact publié."""
    assert _title_gate(title) == 1


@pytest.mark.parametrize(
    "title",
    [
        "feat(hosts): projeter le projet sur les surfaces natives",
        "fix: un correctif sans scope",
        "feat!: rupture déclarée",
        "fix(tests)!: rupture avec scope",
        "docs(changelog): recoudre la section Unreleased",
        "ci(release-drafter): permettre la régénération",
    ],
)
def test_pr_title_gate_accepts(title: str) -> None:
    assert _title_gate(title) == 0


#: L'expression exacte du job « Titre de PR conventionnel ». Recopiée telle
#: quelle, et confrontée au workflow par un test ci-dessous : la dupliquer sans
#: la confronter la laisserait diverger de celle qui décide réellement.
TITLE_RE = (
    'return "^(feat|fix|docs|test|ci|build|chore|refactor|perf|style)'
    '([(][^)]+[)])?(!)?: .+"'
).removeprefix("return ")


def _title_gate(title: str) -> int:
    """Rejoue le garde-fou dans bash, l'interpréteur qui l'exécute en CI."""
    script = "\n".join(
        [
            f"re={TITLE_RE}",
            'if [[ ! "$TITLE" =~ $re ]]; then exit 1; fi',
            "exit 0",
        ]
    )
    # L'environnement du runner est hérité : un `PATH` POSIX en dur ne survit
    # pas au runner Windows, où bash existe mais pas `/usr/bin`.
    env = {**os.environ, "TITLE": title}
    return subprocess.run(["bash", "-c", script], env=env, check=False).returncode


def test_the_gate_regex_matches_the_workflow() -> None:
    """Le test ne vaut que si l'expression testée est celle du workflow."""
    workflow = (
        Path(__file__).resolve().parents[3] / ".github" / "workflows" / "auto-label.yml"
    ).read_text(encoding="utf-8")
    assert f"re={TITLE_RE}" in workflow, (
        "l'expression du workflow a changé — mettre à jour TITLE_RE"
    )


def test_python_is_the_one_running_the_scripts() -> None:
    """Contre-épreuve du chargeur : les trois scripts s'importent vraiment."""
    for name in ("check-code-ratchet", "gen-kit-hashes", "check-changelog-release"):
        assert _load(name) is not None
    assert sys.version_info >= (3, 12)
