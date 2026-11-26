"""Épique #1280 : ce que `host sync --host copilot` livre du cadrage, de l'Atelier et du rappel de tâche.

Les surfaces livrées sous Claude Code (skill `grimoire-cadrage`, commandes `grimoire cadrage …`, outils MCP
`atelier_*`, rappel de tâche) doivent arriver à Copilot et y être dites. Tout est dérivé : skills de la surface,
application Typer `cadrage`, serveur MCP. Non vérifié sur une session Copilot réelle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import copilot as copilot_emitter
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.copilot import KIT_INSTRUCTIONS


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["copilot"]\n', encoding="utf-8"
    )
    return tmp_path


def _files(project: Path) -> dict[str, str]:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project, write_cache=False), project)
    return {f.relpath.as_posix(): f.content for f in plan.files}


def test_copilot_recoit_le_skill_de_cadrage_le_serveur_mcp_et_le_rappel_de_tache(project: Path) -> None:
    files = _files(project)
    assert ".github/skills/grimoire-cadrage/SKILL.md" in files
    assert "atelier_suggest" in files[".github/skills/grimoire-cadrage/SKILL.md"]
    assert ".github/hooks/grimoire-session-start.json" in files
    assert "SessionStart" in json.loads(files[".github/hooks/grimoire-session-start.json"])["hooks"]
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project, write_cache=False), project)
    assert any(".vscode/mcp.json" in str(getattr(m, "relpath", m)) for m in plan.merges)


def test_le_fichier_d_instructions_du_kit_nomme_skills_cadrage_et_atelier(project: Path) -> None:
    text = _files(project)[KIT_INSTRUCTIONS.as_posix()]
    assert text.startswith("---\n") and "applyTo: '**'" in text and "grimoire:managed" in text
    for slug in ("grimoire-cadrage", "grimoire-memory", "grimoire-steward", "grimoire-agent-dispatch"):
        assert f"`{slug}`" in text
    for command in ("interview", "recall", "trace", "export", "plan"):
        assert f"`{command}`" in text
    # Les gestes de la MOA ne sont jamais proposés comme commandes à lancer.
    commands_line = next(line for line in text.splitlines() if "`grimoire cadrage <commande>`" in line)
    assert "`approve`" not in commands_line.split("La validation")[0] and "`recette`" not in commands_line.split("La validation")[0]
    for tool in ("atelier_suggest", "atelier_comment", "atelier_comments"):
        assert f"`{tool}`" in text
    assert "SessionStart" not in text and len(text) < 1800  # chargé à chaque tour : court


def test_les_noms_sont_lus_dans_le_code_pas_ecrits_a_la_main(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.mcp import atelier_tools

    monkeypatch.setattr(atelier_tools, "__all__", ["atelier_suggest", "atelier_nouvel_outil", "register"])
    monkeypatch.setattr(copilot_emitter, "_cadrage_commands", lambda: ["interview", "futur"])
    text = _files(project)[KIT_INSTRUCTIONS.as_posix()]
    assert "`atelier_nouvel_outil`" in text and "`atelier_comment`" not in text
    assert "`futur`" in text


def test_sans_skill_de_cadrage_pas_de_phrase_qui_le_cite(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    surface = replace(build_surface(project, write_cache=False), skills=())
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(surface, project)
    text = next(f.content for f in plan.files if f.relpath == KIT_INSTRUCTIONS)
    assert "aucune" in text and "demande floue" not in text


def test_la_commande_cadrage_est_lue_dans_typer() -> None:
    names = copilot_emitter._cadrage_commands()
    assert {"interview", "trace", "recall", "export"} <= set(names)
    assert "approve" not in names and "recette" not in names
    assert {"atelier_suggest", "atelier_comment", "atelier_comments"} <= set(copilot_emitter._atelier_tools())


def test_la_matrice_a_une_ligne_cadrage_et_atelier() -> None:
    from grimoire.hosts.capability_matrix import CellState, build_capability_matrix

    matrix = build_capability_matrix(HostId.GITHUB_COPILOT)
    vscode = matrix.cell("cadrage_atelier", "vscode")
    assert vscode.state is CellState.UNVERIFIED and "atelier_suggest" in vscode.detail
    assert matrix.cell("cadrage_atelier", "cloud").state is CellState.UNVERIFIED  # sans plan : pas de dégradation connue
    assert build_capability_matrix(HostId.CLAUDE_CODE_CLI).cell("cadrage_atelier", "host").state is CellState.UNVERIFIED
