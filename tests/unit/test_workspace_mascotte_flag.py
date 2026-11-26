"""Mascotte (#1197/#1343) : préférence de COCKPIT de la machine, éteinte par défaut, exacte, indépendante de l'Atelier.

Elle vaut pour tous les projets et la vue « Tous les projets ». Tant qu'elle n'est pas posée, l'ancien drapeau par projet
(``cockpit.test_features.mascotte`` de ``project-context.yaml``) est repris, celui du projet servi comme celui de
n'importe quel projet du registre : qui l'avait allumé dans un projet ne perd pas son choix en regardant un autre.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from grimoire.tools import project_registry as registry
from grimoire.tools import workspace_atelier as wa
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "# réglages du projet\nproject:\n  name: demo   # le nom\n"
LEGACY = CONTEXT + "cockpit:\n  test_features:\n    mascotte: true\n"


@pytest.fixture(autouse=True)
def _clean_cockpit_state() -> Iterator[None]:
    """L'isolation du cockpit vaut pour la session : chaque test repart d'un état et d'un registre vides, rendus après."""
    state, projects = registry.read_state(), registry.load_registry()
    registry.write_state({})
    registry.save_registry([])
    wa._LEGACY_SEEN.clear()
    yield
    registry.write_state(state)
    registry.save_registry(projects)
    wa._LEGACY_SEEN.clear()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "un"
    root.mkdir()
    (root / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    return root


def _other(tmp_path: Path, text: str) -> Path:
    root = tmp_path / "deux"
    root.mkdir()
    (root / "project-context.yaml").write_text(text, encoding="utf-8")
    return root


def _register(*roots: Path) -> None:
    registry.save_registry([{"slug": r.name, "path": str(r), "name": r.name} for r in roots])


def test_off_by_default(project: Path) -> None:
    assert workspace_get(project, f"{P}features", {})["mascotte"] is False
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False
    assert wa.machine_pref("mascotte") is None


def test_the_cockpit_writes_the_machine_preference_not_the_project_file(project: Path) -> None:
    before = (project / "project-context.yaml").read_bytes()
    out = workspace_post(project, f"{P}features", {"mascotte": True})
    assert out == {"atelier": False, "mascotte": True, "mascotte_scope": "machine"}
    assert (project / "project-context.yaml").read_bytes() == before, "aucun fichier de projet n'est réécrit"
    assert wa.machine_pref("mascotte") is True
    assert json.loads(registry.state_file().read_text(encoding="utf-8"))["test_features"] == {"mascotte": True}
    # La préférence ne dépend pas du projet servi : un autre projet voit la même valeur.
    other = Path(str(project) + "-b")
    other.mkdir()
    (other / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    assert workspace_get(other, f"{P}features", {})["mascotte"] is True
    assert workspace_post(project, f"{P}features", {"atelier": True}) == {
        "atelier": True,
        "mascotte": True,
        "mascotte_scope": "machine",
    }
    assert workspace_post(project, f"{P}features", {"mascotte": False})["mascotte"] is False
    assert workspace_get(other, f"{P}features", {})["mascotte"] is False


def test_writing_the_machine_preference_keeps_the_other_cockpit_state(project: Path) -> None:
    registry.write_state({"last_project": "un", "test_features": {"autre": True}})
    workspace_post(project, f"{P}features", {"mascotte": True})
    state = json.loads(registry.state_file().read_text(encoding="utf-8"))
    assert state["last_project"] == "un" and state["test_features"] == {"autre": True, "mascotte": True}


def test_legacy_project_flag_is_adopted_in_every_project_and_the_global_view(tmp_path: Path, project: Path) -> None:
    flagged = _other(tmp_path, LEGACY)
    _register(project, flagged)
    # Le projet sans drapeau (celui que le cockpit ouvre par défaut) voit la mascotte allumée dans l'AUTRE projet.
    assert workspace_get(project, f"{P}features", {})["mascotte"] is True
    assert workspace_get(flagged, f"{P}features", {})["mascotte"] is True


def test_the_machine_preference_overrides_the_legacy_flags_both_ways(tmp_path: Path, project: Path) -> None:
    flagged = _other(tmp_path, LEGACY)
    _register(project, flagged)
    workspace_post(project, f"{P}features", {"mascotte": False})
    assert workspace_get(flagged, f"{P}features", {})["mascotte"] is False, "éteindre ici éteint partout"
    workspace_post(flagged, f"{P}features", {"mascotte": True})
    assert workspace_get(project, f"{P}features", {})["mascotte"] is True


def test_legacy_cache_follows_the_file(tmp_path: Path, project: Path) -> None:
    other = _other(tmp_path, CONTEXT)
    _register(project, other)
    assert workspace_get(project, f"{P}features", {})["mascotte"] is False
    (other / "project-context.yaml").write_text(LEGACY, encoding="utf-8")
    import os
    import time

    future = time.time() + 5
    os.utime(other / "project-context.yaml", (future, future))
    assert workspace_get(project, f"{P}features", {})["mascotte"] is True


def test_flag_must_be_a_real_boolean_and_exactly_true(project: Path) -> None:
    with pytest.raises(ValueError, match="mascotte"):
        workspace_post(project, f"{P}features", {"mascotte": "oui"})
    (project / "project-context.yaml").write_text(CONTEXT + 'cockpit:\n  test_features:\n    mascotte: "true"\n')
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False
    (project / "project-context.yaml").write_text("cockpit: 3\n")
    assert wa.enabled(project, wa.MASCOTTE_FLAG) is False
    registry.write_state({"test_features": {"mascotte": "oui"}})
    assert wa.machine_pref("mascotte") is None, "une valeur qui n'est pas un booléen n'est pas une préférence"


def test_the_project_level_writer_still_exists_for_the_old_flag(project: Path) -> None:
    wa.set_mascotte(project, True)
    text = (project / "project-context.yaml").read_text(encoding="utf-8")
    assert "# réglages du projet" in text and "mascotte: true" in text
    assert workspace_get(project, f"{P}features", {})["mascotte"] is True
