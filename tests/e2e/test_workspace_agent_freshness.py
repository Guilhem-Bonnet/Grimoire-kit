"""Le badge de fraîcheur d'un agent, dans un vrai navigateur (issue #396).

Critère d'arrêt de l'issue, côté cockpit : « la colonne d'usage existante
affiche « jamais invoqué » ou « il y a N jours », et un badge sur les agents
au-delà du seuil ». ``tests/unit/test_workspace_agents.py`` couvre déjà la
route ``agents_view`` elle-même ; ce module vérifie que le badge est
effectivement visible dans la page, pas seulement présent dans le JSON.

Cible ``concierge`` — livré par tous les archétypes, jamais touché par les
tests d'assignation de skill de ``test_workspace_agents.py`` (qui ciblent
``security-auditor``) — et un ``agent.dispatch`` écrit directement dans le
journal du projet servi, pas via l'UI : cette suite prouve le rendu du badge,
pas l'écriture du journal (déjà couverte par ``tests/unit/test_traces.py``).

La coque ouvre ``#piloter`` par défaut au premier chargement
(``shell.js``, ``goto((location.hash || '#piloter').slice(1))``), avant que
le corps du test n'écrive quoi que ce soit dans le journal — et un second
``goto('piloter')`` vers l'espace déjà actif est un no-op documenté
(``shell.js``, "un deuxième goto() vers l'espace déjà actif est un
doublon"). Écrire la trace puis rouvrir directement l'espace lirait donc les
données d'avant l'écriture. Chaque test recharge la page après avoir écrit
sa trace, pour forcer un nouveau montage — donc un nouveau fetch.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

AGENT = "concierge"


def _age_agent_definition(project_root: Path, agent: str, *, days_ago: int) -> None:
    """Vieillit le fichier de définition — le plancher par agent (issue #396,
    suivi) n'accepterait pas de marquer périmé un agent dont le fichier vient
    d'être créé par ``grimoire init`` (fixture de session), même avec un
    ``agent.dispatch`` ancien : le plancher regarde la définition, pas le
    journal.
    """
    old_time = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    path = project_root / "_grimoire" / "kit" / "agents" / f"{agent}.md"
    os.utime(path, (old_time, old_time))


def _open_agent(page: Page) -> None:
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'piloter'")
    page.wait_for_selector(".pl-sheet")
    row = page.locator(".pl-table tbody tr", has_text=AGENT)
    row.wait_for()
    row.click()
    page.wait_for_selector("[data-agent-inspector]")


def _write_stale_dispatch(project_root: Path, *, days_ago: int) -> None:
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import AGENT_DISPATCH_TAG, TraceLedger, TraceOutcome

    ledger = TraceLedger(project_root / TRACES_DIR)
    ledger.record(
        run_id=f"RUN-e2e-freshness-{days_ago}",
        workflow_instance_id="",
        mission_id="",
        task_id="",
        recipe_id="grimoire.entry-persona",
        outcome=TraceOutcome.SUCCESS,
        started_at=(datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        agent_id=AGENT,
        tags=[AGENT_DISPATCH_TAG],
    )


def test_sans_journal_aucun_badge_perime(workspace: Page, real_project: Path) -> None:
    """Journal absent (ou pas encore assez d'historique) : pas de badge, pas d'affirmation."""
    _open_agent(workspace)
    row = workspace.locator(".pl-table tbody tr", has_text=AGENT)
    assert "périmé" not in row.inner_text()


def test_un_agent_perime_porte_le_badge_dans_la_table_et_l_inspecteur(
    workspace: Page, real_project: Path
) -> None:
    _age_agent_definition(real_project, AGENT, days_ago=100)
    _write_stale_dispatch(real_project, days_ago=100)

    _open_agent(workspace)

    row = workspace.locator(".pl-table tbody tr", has_text=AGENT)
    assert "périmé" in row.inner_text()
    assert "invoqué il y a 100 j" in row.inner_text()

    inspector = workspace.locator("[data-agent-inspector]")
    assert "périmé" in inspector.inner_text()
    assert "invoqué il y a 100 j" in inspector.inner_text()
