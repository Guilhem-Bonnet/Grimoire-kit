"""Décider une proposition depuis Piloter, pour chacun des six ``artifact_type``.

Contexte : la matrice de couverture (``docs/cockpit-coverage-matrix.md``,
PR docs(cockpit)) a confronté ``grimoire.proposals`` (six types depuis
l'issue #490 : ``agent``, ``skill``, ``repair``, ``override-migration``,
``memory-link``, ``needs-hosts``) aux tests existants. ``tests/e2e/
test_workspace_proposals.py`` et ``tests/unit/cli/test_cmd_cockpit_
proposals.py`` n'exercent que le type ``agent`` (celui du déclencheur de
non-choix) à travers le cockpit ; les cinq autres n'étaient prouvés qu'au
niveau moteur (``tests/unit/test_project_upgrade.py``, qui appelle
``accept_proposal`` directement) — jamais via un vrai clic dans Piloter, la
route ``POST /api/workspace/proposals/<slug>/accept`` incluse.

Chaque test ci-dessous écrit une proposition avec
``grimoire.proposals.create_manual_proposal`` (la même porte que les nœuds de
jugement du flow d'upgrade utilisent, jamais un YAML à la main qui pourrait
diverger du format réel), puis clique dans l'interface — même doctrine que
``test_workspace_proposals.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

# Spécialités/slugs synthétiques : jamais de collision avec un agent ou un
# skill livré par l'archétype de `real_project`, ni avec les spécialités
# utilisées par les autres modules e2e (`geospatial-indexing`,
# `quantum-annealing-ops` — déjà prises par test_workspace_proposals.py).
SKILL_SLUG = "orbital-mechanics-tuning"
TARGET_AGENT = "security-auditor"  # agent réel de l'archétype, déjà ciblé sans risque par test_workspace_agents.py
NEEDS_HOSTS_SLUG = "hosts-declare-enabled"  # slug fixe imposé par project_upgrade.py — jamais un autre
OVERRIDE_MIGRATION_SPECIALTY = "override-migration-e2e-review-needed"
MEMORY_LINK_SPECIALTY = "memory-link-e2e-no-carrier"
REPAIR_NO_SUB_SPECIALTY = "repair-e2e-no-substitution"
REPAIR_WITH_SUB_SPECIALTY = "repair-e2e-with-substitution"


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _proposals_section(page: Page):
    return page.locator(".pl-section", has=page.locator("h3", has_text="Propositions"))


def _open_piloter(page: Page) -> None:
    """Recharge puis va sur Piloter — un simple `goto` sur l'espace déjà actif
    est un no-op (`shell.js::goto`), il faut relire le disque à jour."""
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']")
    _goto(page, "piloter")
    page.wait_for_selector(".pl-sheet")


def _row(page: Page, specialty_or_slug: str):
    section = _proposals_section(page)
    section.wait_for()
    return section.locator(".pl-prop-row", has_text=specialty_or_slug)


def test_accepter_une_proposition_type_skill_cree_le_fichier_et_l_attache(
    workspace: Page, real_project: Path
) -> None:
    from grimoire.proposals import create_manual_proposal

    dest = real_project / "_grimoire" / "overrides" / "skills" / f"{SKILL_SLUG}.md"
    assert not dest.is_file(), "le skill ne doit pas déjà exister avant ce test"

    create_manual_proposal(
        real_project,
        slug=SKILL_SLUG,
        specialty=SKILL_SLUG,
        artifact_type="skill",
        target_agent=TARGET_AGENT,
        carrier_reason=f"proposé pour {TARGET_AGENT}",
    )

    _open_piloter(workspace)
    row = _row(workspace, SKILL_SLUG)
    row.wait_for()
    assert "skill" in row.inner_text().lower()
    row.get_by_role("button", name="Accepter").click()

    workspace.wait_for_function(
        "(slug) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(slug))",
        arg=SKILL_SLUG,
    )
    assert dest.is_file(), "accepter un type « skill » doit écrire le fichier skill réel"

    from grimoire.hosts.collect import collect_agents, effective_agent_frontmatter

    agents = list(collect_agents(real_project))
    agent = next(a for a in agents if a.name == TARGET_AGENT)
    skills = effective_agent_frontmatter(real_project, agent).get("skills") or []
    assert SKILL_SLUG in skills, "le skill accepté doit être attaché à l'agent porteur"


def test_accepter_une_proposition_type_needs_hosts_declare_les_hotes_actives(
    workspace: Page, real_project: Path
) -> None:
    """`real_project` est une fixture de session partagée par toute la suite
    e2e (voir la mise en garde de `served_empty`, `conftest.py`) : déclarer
    `hosts.enabled` dessus retentit sur des tests bien plus tard dans la même
    session (`grimoire status`/`grimoire doctor` jugent alors ce projet à
    l'aune de hôtes qu'il n'a jamais vraiment équipés — constaté : la config
    invalide a fait échouer un test de `test_workspace_source.py`, la config
    valide en a fait échouer un autre, « doctor vert » attendu). La route et
    l'UI sont donc bien exercées ci-dessus, mais `project-context.yaml` est
    restauré à l'identique en sortie, quoi qu'il arrive."""
    config_path = real_project / "project-context.yaml"
    original = config_path.read_text(encoding="utf-8")
    try:
        from grimoire.proposals import create_manual_proposal

        create_manual_proposal(
            real_project,
            slug=NEEDS_HOSTS_SLUG,
            specialty=NEEDS_HOSTS_SLUG,
            artifact_type="needs-hosts",
            artifact_ref="claude,copilot",
        )

        _open_piloter(workspace)
        row = _row(workspace, NEEDS_HOSTS_SLUG)
        row.wait_for()
        assert "besoins" in row.inner_text().lower()
        row.get_by_role("button", name="Accepter").click()

        workspace.wait_for_function(
            "(slug) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(slug))",
            arg=NEEDS_HOSTS_SLUG,
        )

        from grimoire.tools._common import load_yaml_roundtrip

        data = load_yaml_roundtrip(config_path)
        assert set(data.get("hosts", {}).get("enabled", [])) >= {"claude", "copilot"}
    finally:
        config_path.write_text(original, encoding="utf-8")


def test_une_proposition_repair_sans_substitution_n_offre_pas_accepter(
    workspace: Page, real_project: Path
) -> None:
    """`piloter.js::repairHasEvidentSubstitution` cache « Accepter » quand
    `carrier_reason` ne porte pas le motif de substitution — `_accept_repair`
    refuserait de toute façon (revue humaine requise). Le bouton « Refuser »,
    lui, doit rester utilisable : cette réparation n'est pas bloquée à vie."""
    from grimoire.proposals import create_manual_proposal, list_proposals

    create_manual_proposal(
        real_project,
        slug="repair-e2e-no-sub",
        specialty=REPAIR_NO_SUB_SPECIALTY,
        artifact_type="repair",
        carrier_reason="revue nécessaire : aucun remplacement évident trouvé",
        artifact_ref="docs/does-not-matter.md:1 → dead-target",
    )

    _open_piloter(workspace)
    row = _row(workspace, REPAIR_NO_SUB_SPECIALTY)
    row.wait_for()
    assert row.get_by_role("button", name="Accepter").count() == 0, (
        "une réparation sans substitution évidente ne doit jamais offrir « Accepter »"
    )
    row.get_by_role("button", name="Refuser").click()
    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=REPAIR_NO_SUB_SPECIALTY,
    )
    proposals = {p.slug: p for p in list_proposals(real_project, sync=False)}
    assert proposals["repair-e2e-no-sub"].status == "rejected"


def test_accepter_une_proposition_repair_avec_substitution_corrige_le_fichier(
    workspace: Page, real_project: Path
) -> None:
    fixture = real_project / "docs" / "_coverage_repair_fixture_e2e.md"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("Voir dead-target-v2 pour le détail.\n", encoding="utf-8")

    from grimoire.proposals import create_manual_proposal

    create_manual_proposal(
        real_project,
        slug="repair-e2e-with-sub",
        specialty=REPAIR_WITH_SUB_SPECIALTY,
        artifact_type="repair",
        carrier_reason="substitution évidente : dead-target-v2 -> dead-target-v3",
        artifact_ref="docs/_coverage_repair_fixture_e2e.md:1 → dead-target-v2",
    )

    _open_piloter(workspace)
    row = _row(workspace, REPAIR_WITH_SUB_SPECIALTY)
    row.wait_for()
    row.get_by_role("button", name="Accepter").click()

    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=REPAIR_WITH_SUB_SPECIALTY,
    )
    assert fixture.read_text(encoding="utf-8") == "Voir dead-target-v3 pour le détail.\n"


def test_accepter_une_proposition_override_migration_refusee_journalise_le_motif(
    workspace: Page, real_project: Path
) -> None:
    """Sans le préfixe « conversion sûre » dans `carrier_reason`, `_accept_
    override_migration` refuse avant même de toucher un override — motif
    affiché via `ctx.dock.echo` (`#dock-echo`), jamais une exception muette."""
    from grimoire.proposals import create_manual_proposal, list_proposals

    slug = "override-migration-e2e"
    create_manual_proposal(
        real_project,
        slug=slug,
        specialty=OVERRIDE_MIGRATION_SPECIALTY,
        artifact_type="override-migration",
        target_agent=TARGET_AGENT,
        carrier_reason="revue nécessaire : divergence non triviale",
    )

    _open_piloter(workspace)
    row = _row(workspace, OVERRIDE_MIGRATION_SPECIALTY)
    row.wait_for()
    row.get_by_role("button", name="Accepter").click()

    workspace.wait_for_function("() => document.getElementById('dock-echo')?.textContent.includes('refusé')")
    assert "revue humaine requise" in workspace.locator("#dock-echo").inner_text()
    # La proposition reste affichée (jamais acceptée par erreur) — on la
    # referme explicitement pour ne pas polluer le projet partagé de session.
    row.get_by_role("button", name="Refuser").click()
    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=OVERRIDE_MIGRATION_SPECIALTY,
    )
    proposals = {p.slug: p for p in list_proposals(real_project, sync=False)}
    assert proposals[slug].status == "rejected"


def test_accepter_une_proposition_memory_link_refusee_sans_porteur(
    workspace: Page, real_project: Path
) -> None:
    """`target_agent` vide : `_accept_memory_link` refuse nommément plutôt que
    de deviner un porteur — le déclencheur documente déjà ce cas comme « à
    placer à la main »."""
    from grimoire.proposals import create_manual_proposal, list_proposals

    slug = "memory-link-e2e"
    create_manual_proposal(
        real_project,
        slug=slug,
        specialty=MEMORY_LINK_SPECIALTY,
        artifact_type="memory-link",
        artifact_ref="_grimoire/_memory/some-fiche.md",
    )

    _open_piloter(workspace)
    row = _row(workspace, MEMORY_LINK_SPECIALTY)
    row.wait_for()
    row.get_by_role("button", name="Accepter").click()

    workspace.wait_for_function("() => document.getElementById('dock-echo')?.textContent.includes('refusé')")
    assert "aucun porteur plausible" in workspace.locator("#dock-echo").inner_text()
    row.get_by_role("button", name="Refuser").click()
    workspace.wait_for_function(
        "(specialty) => !Array.from(document.querySelectorAll('.pl-prop-row')).some((r) => r.textContent.includes(specialty))",
        arg=MEMORY_LINK_SPECIALTY,
    )
    proposals = {p.slug: p for p in list_proposals(real_project, sync=False)}
    assert proposals[slug].status == "rejected"
