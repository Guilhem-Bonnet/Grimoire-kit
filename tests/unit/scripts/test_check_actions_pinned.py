"""Tests pour le garde check-actions-pinned.py."""

import subprocess
import sys
from pathlib import Path
import tempfile
import pytest


# Récupérer le chemin absolu du script de garde
SCRIPT_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "check-actions-pinned.py"


class TestCheckActionsPinned:
    """Tests du garde d'épinglage d'actions."""

    def test_unpinned_actions_detected(self, tmp_path):
        """Vérifie qu'un workflow avec des actions non épinglées est détecté."""
        # Créer un workflow test avec une action non épinglée
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        test_workflow = workflows_dir / "test.yml"
        test_workflow.write_text("""
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v1
""")

        # Exécuter le script
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True
        )

        # Doit échouer
        assert result.returncode != 0, f"Script devrait échouer.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "actions/checkout@v1" in result.stderr

    def test_pinned_actions_pass(self, tmp_path):
        """Vérifie qu'un workflow avec des actions épinglées passe."""
        # Créer un workflow test avec une action épinglée par SHA
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        test_workflow = workflows_dir / "test.yml"
        test_workflow.write_text("""
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@a83e7850d592aacd9ed61157eb88bafe1c4b8289
""")

        # Exécuter le script
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True
        )

        # Doit réussir
        assert result.returncode == 0, f"Script devrait réussir.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "✓" in result.stdout or "validées" in result.stdout

    def test_multiple_unpinned_actions(self, tmp_path):
        """Vérifie la détection de plusieurs actions non épinglées."""
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        test_workflow = workflows_dir / "test.yml"
        test_workflow.write_text("""
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v3
      - uses: pypa/gh-action-pypi-publish@release/v1
""")

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True
        )

        # Doit échouer
        assert result.returncode != 0
        # Tous les trois doivent être détectés
        assert "checkout@v4" in result.stderr
        assert "setup-python@v3" in result.stderr
        assert "gh-action-pypi-publish@release/v1" in result.stderr

    def test_mixed_pinned_and_unpinned(self, tmp_path):
        """Vérifie que la présence d'une action non épinglée fail le script."""
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        test_workflow = workflows_dir / "test.yml"
        test_workflow.write_text("""
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@a83e7850d592aacd9ed61157eb88bafe1c4b8289
      - uses: actions/setup-python@v1
      - uses: softprops/action-gh-release@afae5a3dae4d92c94dfc71c96de8ed0ed67e0b12
""")

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True
        )

        # Doit échouer à cause de setup-python@v1
        assert result.returncode != 0
        assert "setup-python@v1" in result.stderr
        # Mais ne mentionne que les actions problématiques
        assert "checkout@a83e7850d592aacd9ed61157eb88bafe1c4b8289" not in result.stderr

    def test_current_repo_workflows(self):
        """Teste le repo actuel - doit réussir après le correctif."""
        # Ce test passe sur la branche avec le correctif D-02
        # et échoue sur origin/main (où les actions ne sont pas épinglées)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True
        )

        # Après le correctif, le repo doit avoir toutes les actions épinglées
        assert result.returncode == 0, f"Le repo doit avoir toutes les actions épinglées.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "✓" in result.stdout
