"""`grimoire setup` écrit la source de vérité qu'il déclare, puis vérifie contre elle.

Le module annonce en tête que ``project-context.yaml`` est la source de vérité.
Les options ``--user``/``--lang``/``--skill-level`` n'y arrivaient jamais : elles
vivaient dans un objet en mémoire, ``apply`` ne réécrivait que le miroir
Copilot, puis vérifiait le miroir contre l'objet en mémoire — et annonçait
« in sync » au-dessus d'une divergence qu'il venait de créer.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_setup import apply, check, load_user_values

runner = CliRunner()

_CONTEXT_WITH_USER = """project:
  name: "origine-projet"
  type: "library"

user:
  name: "Origine"
  language: "Français"
  document_language: "Français"
  skill_level: "beginner"

memory:
  backend: "auto"
"""

_CONTEXT_WITHOUT_USER = """project:
  name: "origine-projet"
  type: "library"

memory:
  backend: "auto"
"""

_COPILOT = """# Projet

- **Project**: origine-projet
- **User**: Origine
- **Communication Language**: Français
- **Document Output Language**: Français
- **User Skill Level**: beginner
"""


def _project(root: Path, context: str) -> Path:
    (root / "project-context.yaml").write_text(context, encoding="utf-8")
    (root / ".github").mkdir()
    (root / ".github" / "copilot-instructions.md").write_text(_COPILOT, encoding="utf-8")
    return root


def test_apply_writes_the_override_into_project_context(tmp_path: Path) -> None:
    root = _project(tmp_path, _CONTEXT_WITH_USER)
    vals = load_user_values(root / "project-context.yaml")
    vals.user_name, vals.user_skill_level = "Autre", "expert"
    apply(root, vals)
    reloaded = load_user_values(root / "project-context.yaml")
    assert (reloaded.user_name, reloaded.user_skill_level) == ("Autre", "expert")


def test_apply_creates_the_user_section_and_leaves_project_name_alone(tmp_path: Path) -> None:
    """Le cas des projets antérieurs à la section ``user:`` — et ``project.name`` a aussi une clé ``name``."""
    root = _project(tmp_path, _CONTEXT_WITHOUT_USER)
    vals = load_user_values(root / "project-context.yaml")
    vals.user_name = "Autre"
    apply(root, vals)
    text = (root / "project-context.yaml").read_text(encoding="utf-8")
    reloaded = load_user_values(root / "project-context.yaml")
    assert reloaded.user_name == "Autre"
    assert reloaded.project_name == "origine-projet", text
    assert text.count("name:") == 2, text


def test_in_sync_means_mirror_matches_the_file_not_the_memory(tmp_path: Path) -> None:
    root = _project(tmp_path, _CONTEXT_WITH_USER)
    vals = load_user_values(root / "project-context.yaml")
    vals.user_name = "Autre"
    result = apply(root, vals)
    assert result.is_synced
    # La vérification qui compte : relire la source et comparer le miroir à elle.
    assert check(root, load_user_values(root / "project-context.yaml")).diffs == []


def test_cli_check_agrees_with_the_setup_that_just_ran(tmp_path: Path) -> None:
    """Le repro d'origine : ``setup --user X`` puis ``setup --check`` se contredisaient."""
    root = _project(tmp_path, _CONTEXT_WITH_USER)
    first = runner.invoke(app, ["setup", str(root), "--user", "Autre", "--skill-level", "expert"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["setup", str(root), "--check"])
    assert second.exit_code == 0, second.output
    assert "Remaining diffs" not in second.output
