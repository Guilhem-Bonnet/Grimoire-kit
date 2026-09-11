"""Le parcours des propositions d'artefact, dans un vrai navigateur (#395).

Critère d'arrêt de l'issue : « le cockpit montre et actionne la même chose,
avec un test navigateur » — liste, accepter, refuser. Le moteur et le seuil
sont déjà couverts par ``tests/unit/test_proposals.py`` ; ce module écrit
directement les fichiers de proposition (même forme que
``grimoire.proposals.sync_proposals`` en produirait) pour isoler ce qui ne
se prouve qu'ici : le bouton clique, l'artefact apparaît sur disque, la
proposition disparaît de la liste des « en attente ».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

# Spécialités synthétiques, choisies pour ne collisionner avec aucun agent
# livré par l'archétype du projet e2e partagé (voir test_workspace_agents.py
# pour la même précaution sur `security-auditor`).
ACCEPT_SPECIALTY = "geospatial-indexing"
REJECT_SPECIALTY = "quantum-annealing-ops"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _write_pending_proposal(project: Path, specialty: str) -> str:
    """Une proposition ``pending`` prête à afficher, sans passer par le seuil.

    Écrite avec le même moteur (``grimoire.proposals``) que le déclencheur
    produirait — jamais un fichier YAML à la main qui pourrait diverger du
    format réel.
    """
    from grimoire.proposals import _build_proposal, _proposal_path, _save_proposal

    proposal = _build_proposal(
        specialty=specialty,
        count=2,
        category="infra",
        fallback_agent="",
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-01-01T00:00:00+00:00",
    )
    _save_proposal(_proposal_path(project, proposal.slug), proposal)
    return proposal.slug


def _proposals_section(page: Page):
    return page.locator(".pl-section", has=page.locator("h3", has_text="Propositions"))


def test_lister_accepter_et_refuser_une_proposition(
    workspace: Page, real_project: Path
) -> None:
    accept_slug = _write_pending_proposal(real_project, ACCEPT_SPECIALTY)
    reject_slug = _write_pending_proposal(real_project, REJECT_SPECIALTY)
    accept_dest = real_project / "_grimoire" / "overrides" / "agents" / f"{accept_slug}.md"
    reject_dest = real_project / "_grimoire" / "overrides" / "agents" / f"{reject_slug}.md"
    assert not accept_dest.is_file(), "l'agent ne doit pas déjà exister avant ce test"

    # « piloter » est l'espace par défaut de la coque (`shell.js` :
    # `current || 'piloter'`) : la fixture `workspace` l'a donc déjà monté,
    # avec les données d'avant l'écriture des propositions ci-dessus. Un
    # second `goto('piloter')` sur l'espace déjà actif est un no-op
    # (`shell.js::goto` : `if (current === space.id && !params) return`) —
    # il faut recharger pour que le mount reparte du disque à jour.
    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']")
    _goto(workspace, "piloter")
    workspace.wait_for_selector(".pl-sheet")
    section = _proposals_section(workspace)
    section.wait_for()

    # ── Liste : les deux propositions et leurs faits sont visibles ────────
    assert ACCEPT_SPECIALTY in section.inner_text()
    assert REJECT_SPECIALTY in section.inner_text()
    assert "2 non-choix" in section.inner_text()

    # ── Accepter ────────────────────────────────────────────────────────
    accept_row = section.locator(".pl-prop-row", has_text=ACCEPT_SPECIALTY)
    accept_row.get_by_role("button", name="Accepter").click()

    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=ACCEPT_SPECIALTY,
    )
    assert accept_dest.is_file(), "accepter doit écrire un fichier réel dans overrides"

    # La table des agents, juste au-dessus, montre le nouvel agent sans recharger la page.
    workspace.wait_for_function(
        "(slug) => document.querySelector('.pl-table')?.textContent.includes(slug)",
        arg=accept_slug,
    )

    # ── Refuser ─────────────────────────────────────────────────────────
    reject_row = section.locator(".pl-prop-row", has_text=REJECT_SPECIALTY)
    reject_row.get_by_role("button", name="Refuser").click()

    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=REJECT_SPECIALTY,
    )
    assert not reject_dest.is_file(), "refuser ne doit jamais écrire l'artefact"

    from grimoire.proposals import list_proposals

    proposals = {p.slug: p for p in list_proposals(real_project, sync=False)}
    assert proposals[accept_slug].status == "accepted"
    assert proposals[reject_slug].status == "rejected"
