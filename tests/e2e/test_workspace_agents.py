"""Le parcours d'assignation d'un skill à un agent, dans un vrai navigateur (#374).

Le critère d'arrêt de l'issue est explicite : « assigner un skill à un agent
produit un fichier dans ``overrides`` que le diagnostic et la carte de routage
voient ; le retirer le fait disparaître ; les deux sont vérifiés dans le
navigateur, pas seulement sur la route. » Ce module fait exactement ça —
``tests/unit/test_workspace_agents.py`` couvre déjà la route elle-même.

``creative-toolsmith`` est l'agent ciblé, jamais ``concierge`` : un autre
harnais e2e (``test_workspace_source_language.py``) prend pour acquis l'ordre
et le contenu de l'agent ``concierge`` livré par le kit, et cette suite ne
doit pas lui faire courir un risque pour un défaut qu'elle ne teste pas.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

AGENT = "creative-toolsmith"
SKILL = "grimoire-agent-dispatch"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _open_agent(page: Page) -> None:
    _goto(page, "piloter")
    page.wait_for_selector(".pl-sheet")
    row = page.locator(".pl-table tbody tr", has_text=AGENT)
    row.wait_for()
    row.click()
    page.wait_for_selector("[data-agent-inspector]")


def test_assigner_un_skill_cree_l_override_vu_par_le_diagnostic(
    workspace: Page, real_project: Path
) -> None:
    override = real_project / "_grimoire" / "overrides" / "agents" / f"{AGENT}.md"
    assert not override.is_file(), "l'agent ne doit pas déjà être en override avant ce test"

    _open_agent(workspace)
    inspector = workspace.locator("[data-agent-inspector]")
    assert SKILL not in inspector.locator(".pl-chips").inner_text()

    inspector.locator("select").select_option(SKILL)
    inspector.get_by_role("button", name="Assigner").click()

    # Le chip apparaît dans l'UI...
    workspace.wait_for_function(
        "(skill) => document.querySelector('[data-agent-inspector] .pl-chips')?.textContent.includes(skill)",
        arg=SKILL,
    )
    # ... ET le fichier existe réellement sur disque, au format que
    # `collect_agents`/`grimoire host status` lisent.
    assert override.is_file(), "l'assignation doit écrire un fichier réel dans overrides"
    content = override.read_text(encoding="utf-8")
    assert "skills:" in content
    assert SKILL in content

    # La table de la fiche reflète aussi la nouvelle couche.
    row = workspace.locator(".pl-table tbody tr", has_text=AGENT)
    assert "overrides" in row.inner_text()

    # `grimoire host status` — la carte de routage — voit la même déclaration :
    # relire directement ce que `collect_agents` en ferait est le test le plus
    # court qui ne re-décrit pas cette lecture ; le comportement en lui-même
    # est déjà couvert par `tests/unit/test_workspace_agents.py`.
    from grimoire.hosts.collect import collect_agents, collect_skills

    skills = collect_skills(real_project)
    agents = collect_agents(real_project, known_skills=frozenset(s.slug for s in skills))
    agent = next(a for a in agents if a.name == AGENT)
    assert SKILL in agent.skills


def test_retirer_le_skill_fait_disparaitre_le_chip_et_la_declaration(
    workspace: Page, real_project: Path
) -> None:
    override = real_project / "_grimoire" / "overrides" / "agents" / f"{AGENT}.md"

    _open_agent(workspace)
    inspector = workspace.locator("[data-agent-inspector]")
    chip = inspector.locator(".pl-chip", has_text=SKILL)
    chip.wait_for()
    chip.get_by_role("button", name=f"Retirer {SKILL}").click()

    workspace.wait_for_function(
        "(skill) => !document.querySelector('[data-agent-inspector] .pl-chips')?.textContent.includes(skill)",
        arg=SKILL,
    )
    assert "aucun skill attaché" in inspector.locator(".pl-chips").inner_text()
    assert "skills:" not in override.read_text(encoding="utf-8")

    from grimoire.hosts.collect import collect_agents, collect_skills

    skills = collect_skills(real_project)
    agents = collect_agents(real_project, known_skills=frozenset(s.slug for s in skills))
    agent = next(a for a in agents if a.name == AGENT)
    assert SKILL not in agent.skills
