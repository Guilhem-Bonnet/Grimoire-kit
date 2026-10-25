"""Piloter — propositions : deux gestes restés sans test navigateur (#536).

- « Ouvrir le fichier » d'une proposition ``repair`` ouvre l'espace Source sur
  le fichier cité, la ligne sélectionnée ;
- « Accepter » sur un projet qui n'est PAS le projet de lancement du cockpit
  (la dérogation nommée du #490, ``is_proposal_decision``) : jusqu'ici prouvé
  par un POST direct (``test_cmd_cockpit_proposals.py``), jamais par le clic.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Page


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.module === id", arg=space)


def _row(page: Page, specialty: str):
    section = page.locator(".pl-section", has=page.locator("h3", has_text="Propositions"))
    section.wait_for()
    return section.locator(".pl-prop-row", has_text=specialty)


def test_ouvrir_le_fichier_d_une_reparation_selectionne_la_ligne_citee(workspace: Page, real_project: Path) -> None:
    from grimoire.proposals import create_manual_proposal, list_proposals, reject_proposal

    cited = real_project / "_grimoire" / "overrides" / "_e2e_repair_open.md"
    cited.parent.mkdir(parents=True, exist_ok=True)
    cited.write_text("première ligne\nVoir dead-target-open pour le détail.\ntroisième ligne\n", encoding="utf-8")
    specialty = "repair-e2e-open-file"
    create_manual_proposal(
        real_project,
        slug="repair-e2e-open",
        specialty=specialty,
        artifact_type="repair",
        carrier_reason="aucune substitution évidente",
        artifact_ref="_grimoire/overrides/_e2e_repair_open.md:2 → dead-target-open",
    )
    try:
        workspace.reload(wait_until="domcontentloaded")
        workspace.wait_for_selector("body[data-ready='1']")
        _goto(workspace, "piloter")
        workspace.wait_for_selector(".pl-sheet")
        row = _row(workspace, specialty)
        row.wait_for()
        assert row.get_by_role("button", name="Accepter").count() == 0, "pas de substitution évidente"

        row.get_by_role("button", name="Ouvrir le fichier").click()

        workspace.wait_for_function("() => window.GrimoireWorkspace.module === 'source'")
        workspace.wait_for_selector(".sr-textarea")
        selected = workspace.evaluate(
            "() => { const t = document.querySelector('.sr-textarea');"
            " return t.value.slice(t.selectionStart, t.selectionEnd); }"
        )
        assert selected == "Voir dead-target-open pour le détail."
        assert "_e2e_repair_open.md" in workspace.locator(".sr-docrow .mono").first.inner_text()
    finally:
        if any(p.slug == "repair-e2e-open" for p in list_proposals(real_project, sync=False)):
            reject_proposal(real_project, "repair-e2e-open")
        cited.unlink(missing_ok=True)


def test_accepter_une_proposition_depuis_un_projet_qui_n_est_pas_le_projet_de_lancement(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    """Le cockpit est lancé sans projet de lancement (``_HOME_SLUG`` vide) : le
    projet regardé est donc en lecture seule pour toute écriture, SAUF décider
    une proposition (#490). Le clic « Accepter » doit écrire l'artefact."""
    from grimoire.proposals import create_manual_proposal

    served, slug, root = served_cockpit_upgrade
    with urllib.request.urlopen(f"{served}/api/status?project={slug}", timeout=10) as resp:  # noqa: S310 — loopback
        assert json.loads(resp.read())["readOnly"] is True, "le projet regardé doit être en lecture seule"

    skill = "orbital-nonhome-accept"
    dest = root / "_grimoire" / "overrides" / "skills" / f"{skill}.md"
    assert not dest.exists()
    create_manual_proposal(
        root,
        slug=skill,
        specialty=skill,
        artifact_type="skill",
        target_agent="security-auditor",
        carrier_reason="proposé pour security-auditor",
    )
    page = cockpit_upgrade_workspace
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _goto(page, "piloter")
    page.wait_for_selector(".pl-sheet", timeout=15_000)
    row = _row(page, skill)
    row.wait_for(timeout=15_000)

    row.get_by_role("button", name="Accepter").click()

    page.wait_for_function(
        "(slug) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(slug))",
        arg=skill,
    )
    assert dest.is_file(), "accepter sur un projet non-home doit écrire le skill (dérogation #490)"
