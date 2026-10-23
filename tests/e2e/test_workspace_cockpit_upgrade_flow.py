"""Le bouton « Mettre à jour » depuis le cockpit lance le flow, pas `up` seul (#490).

Refs #490. La route ``POST /api/projects/update`` appelle désormais
``grimoire upgrade-flow run`` (``src/grimoire/tools/project_update.py``) :
aperçu par défaut (rapport ``preview.md`` renvoyé et affiché dans la fiche
projet de Piloter), ``confirm: true`` pour le flow complet (nœuds de
jugement mécaniques, checkpoint final laissé à l'humain). Ce module prouve
le seul chemin qui compte pour un utilisateur — un vrai navigateur, les deux
clics du bouton — et que ce que le flow complet propose (issue #490 : une
fiche mémoire non raccordée) apparaît bien dans la section Propositions de
Piloter, sans recharger la page à la main.

``tests/e2e/test_workspace_cockpit_update_route.py`` couvre déjà l'aperçu
seul (régression #358, query string). Ce module-ci ajoute la confirmation et
la proposition qui doit en sortir — la partie de l'issue #490 que l'aperçu
seul ne peut pas prouver.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)


def _proposals_section(page: Page):
    return page.locator(".pl-section", has=page.locator("h3", has_text="Propositions"))


def test_confirmer_depuis_piloter_fait_apparaitre_une_proposition(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    workspace = cockpit_upgrade_workspace
    _goto(workspace, "piloter")

    # Le niveau Flotte est le défaut côté cockpit — passer au niveau Projet
    # est ce qu'un utilisateur ferait pour atteindre les actions du projet.
    workspace.wait_for_selector("#zoom-seg button")
    workspace.locator("#zoom-seg button", has_text="Projet").click()

    update_button = workspace.locator("button", has_text="Mettre à jour — aperçu")
    update_button.wait_for(state="visible", timeout=15_000)
    update_button.click()

    preview = workspace.locator(".pl-preview")
    preview.wait_for(state="visible", timeout=30_000)
    preview_text = preview.inner_text()
    assert "refusé" not in preview_text, f"l'aperçu a échoué : {preview_text!r}"
    assert "404" not in preview_text, f"la route a répondu 404 : {preview_text!r}"

    confirm_button = preview.get_by_role("button", name="Confirmer la mise à jour")
    confirm_button.wait_for(state="visible", timeout=15_000)
    confirm_button.click()

    # Le déroulé nœud par nœud doit apparaître ET rester affiché — issue #506 :
    # le constat terrain (« l'Inspecteur revient silencieusement à l'état
    # initial ») venait de `options.refresh()`, appelé juste après avoir
    # montré le résultat, qui redessinait toute la fiche et l'effaçait. Le
    # correctif retire cet appel du chemin de confirmation ; ce test vérifie
    # maintenant l'état stable ET le message transitoire, plutôt que
    # d'esquiver ce dernier comme avant.
    workspace.wait_for_function(
        "() => document.body.innerText.includes('monitoring')",
        timeout=90_000,
    )
    section = _proposals_section(workspace)
    section.wait_for(state="visible", timeout=15_000)
    assert "monitoring" in section.inner_text(), (
        "la fiche mémoire non raccordée doit apparaître comme proposition dans Piloter"
    )

    preview_text = preview.inner_text()
    assert "s'est arrêté au checkpoint final" in preview_text, (
        "le message de confirmation doit rester affiché, pas disparaître au refresh des propositions"
    )
    for node_label in ("Sauvegarde", "Aperçu", "Application", "Destructif"):
        assert node_label in preview_text, f"le déroulé nœud par nœud doit nommer « {node_label} »"
    assert "checkpoint destructif en attente" in workspace.locator("body").inner_text(), (
        "le badge Kit doit passer à un état explicite après un run arrêté au checkpoint"
    )


def _flow_runs(page: Page, served: str, slug: str) -> dict:
    response = page.request.get(f"{served}/api/workspace/flows/runs", params={"project": slug})
    assert response.ok, response.status
    return response.json()


def _open_project_sheet(page: Page) -> None:
    _goto(page, "piloter")
    page.wait_for_selector("#zoom-seg button")
    page.locator("#zoom-seg button", has_text="Projet").click()
    page.locator("button", has_text="Mettre à jour — aperçu").wait_for(state="visible", timeout=15_000)


def test_un_apercu_apres_un_checkpoint_pendant_garde_le_signal_et_ne_compte_aucun_run(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    """P07 (revue UX B2) : après un run arrêté, non décidé, au checkpoint
    `destructive` (test précédent, fixture de session partagée), un aperçu
    créait un run ordinaire qui devenait « le dernier » — la pastille repassait
    à « aligné » et « Revoir dans l'IDE » disparaissait au rechargement.
    L'aperçu ne compte plus comme run, son message dit ce qu'il a écrit, et la
    fiche rechargée signale toujours le checkpoint.
    """
    served, slug, root = served_cockpit_upgrade
    before = _flow_runs(cockpit_upgrade_workspace, served, slug)
    pending = [r for r in before["runs"] if r["status"] == "checkpointed" and r["currentNode"] == "destructive"]
    assert pending, f"le test précédent doit avoir laissé un checkpoint pendant : {before}"

    workspace = cockpit_upgrade_workspace
    _open_project_sheet(workspace)
    workspace.locator("button", has_text="Mettre à jour — aperçu").click()
    preview = workspace.locator(".pl-preview")
    preview.get_by_text("Aperçu : sauvegarde créée, aucun fichier du projet modifié.").wait_for(timeout=90_000)
    assert "rien d'autre écrit" not in preview.inner_text()

    after = _flow_runs(workspace, served, slug)
    assert after["total"] == before["total"], "un aperçu n'est pas un run de flow compté"
    assert [r["runId"] for r in after["runs"]] == [r["runId"] for r in before["runs"]]
    previews = [
        p for p in (root / "_grimoire-runtime-output" / "flows").glob("*.json")
        if json.loads(p.read_text(encoding="utf-8")).get("preview") is True
    ]
    assert previews, "l'aperçu a bien tourné : son run marqué `preview` est sur disque"

    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _open_project_sheet(workspace)
    workspace.get_by_text("mis à niveau, checkpoint destructif en attente").wait_for(timeout=15_000)
    assert workspace.locator("button", has_text="Revoir dans l'IDE").count() == 1

    # Le KPI de la fiche et la liste d'Observer : une seule source.
    kpi = workspace.locator(".kpi-item", has_text="runs de flow")
    assert kpi.locator(".kpi-val").inner_text().strip() == str(after["total"])
    _goto(workspace, "observer")
    workspace.wait_for_selector(".ob-runs", timeout=15_000)
    assert f"· {after['total']}" in workspace.locator(".ob-runs h4").inner_text()
    assert "Aucune trace" not in workspace.locator("#canvas").inner_text()


def test_un_run_plus_recent_non_checkpointe_ne_masque_pas_le_checkpoint_pendant(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    """P07 : le checkpoint `destructive` pendant d'un run ancien reste signalé
    quand un run plus récent n'est pas checkpointé (cas qui régressait quand la
    fiche ne lisait que `upgradeRuns[0]`). Le run ancien est celui laissé par le
    test de confirmation (fixture de session partagée) ; le run récent est une
    métadonnée sans kernel (`status` nul), retirée en fin de test.
    """
    served, slug, root = served_cockpit_upgrade
    before = _flow_runs(cockpit_upgrade_workspace, served, slug)
    assert any(r["status"] == "checkpointed" and r["currentNode"] == "destructive" for r in before["runs"]), (
        f"le test de confirmation doit avoir laissé un checkpoint pendant : {before}"
    )

    newer = root / "_grimoire-runtime-output" / "flows" / "WFI-project-upgrade-recent-sans-checkpoint.json"
    newer.write_text(
        json.dumps({
            "schema_version": "grimoire.flow_run_meta.v1",
            "run_id": "WFI-project-upgrade-recent-sans-checkpoint",
            "blueprint_id": "project-upgrade",
            "blueprint_path": "whatever.yaml",
            "order": ["backup", "preview"],
            "created_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }),
        encoding="utf-8",
    )
    try:
        after = _flow_runs(cockpit_upgrade_workspace, served, slug)
        assert after["runs"][0]["runId"] == "WFI-project-upgrade-recent-sans-checkpoint"
        assert after["runs"][0]["status"] != "checkpointed"

        workspace = cockpit_upgrade_workspace
        workspace.reload(wait_until="domcontentloaded")
        workspace.wait_for_selector("body[data-ready='1']", timeout=30_000)
        _open_project_sheet(workspace)
        workspace.get_by_text("checkpoint destructif en attente").first.wait_for(timeout=15_000)
        assert workspace.locator("button", has_text="Revoir dans l'IDE").count() == 1
    finally:
        newer.unlink(missing_ok=True)


def test_observer_montre_le_run_de_flow_meme_traceledger_vide(
    cockpit_upgrade_workspace: Page, served_cockpit_upgrade: tuple[str, str, Path]
) -> None:
    """Issue #506 : Observer disait « TraceLedger vide » même juste après un
    run réel de `project-upgrade` — vrai du TraceLedger (dispatch/agent-miss),
    trompeur pour qui vient de confirmer une mise à jour depuis Piloter. Ce
    test s'appuie sur le run déjà confirmé par le test précédent dans ce même
    fichier (fixture de session partagée) : sans lui, aucun run n'existerait
    encore et ce test ne prouverait rien.
    """
    workspace = cockpit_upgrade_workspace
    _goto(workspace, "observer")
    workspace.wait_for_selector(".ob-runs", timeout=15_000)
    runs_text = workspace.locator(".ob-runs").inner_text()
    assert "project-upgrade" in runs_text, (
        "un run de project-upgrade existe (test précédent) : Observer doit le nommer, "
        "pas seulement dire le TraceLedger vide"
    )
