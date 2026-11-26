"""Drapeau ``cockpit.test_features.mascotte`` (mascotte vocale du chat, #1197/#1343) : éteint par défaut, exact, indépendant de l'Atelier."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import workspace_atelier as wa
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "# réglages du projet\nproject:\n  name: demo   # le nom\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    return tmp_path


def test_off_by_default(project: Path) -> None:
    assert workspace_get(project, f"{P}features", {})["mascotte"] is False
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False


def test_writing_the_flag_keeps_comments_and_leaves_the_atelier_alone(project: Path) -> None:
    assert workspace_post(project, f"{P}features", {"mascotte": True}) == {"atelier": False, "mascotte": True}
    text = (project / "project-context.yaml").read_text(encoding="utf-8")
    assert "# réglages du projet" in text and "# le nom" in text and "mascotte: true" in text
    assert workspace_post(project, f"{P}features", {"atelier": True}) == {"atelier": True, "mascotte": True}
    assert workspace_post(project, f"{P}features", {"mascotte": False}) == {"atelier": True, "mascotte": False}


def test_flag_must_be_a_real_boolean_and_exactly_true(project: Path) -> None:
    with pytest.raises(ValueError, match="mascotte"):
        workspace_post(project, f"{P}features", {"mascotte": "oui"})
    (project / "project-context.yaml").write_text(CONTEXT + 'cockpit:\n  test_features:\n    mascotte: "true"\n')
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False
    (project / "project-context.yaml").write_text("cockpit: 3\n")
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False
