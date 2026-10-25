"""Espace Exécuter — contrôles restés sans test navigateur (#536).

Un projet dédié (jamais ``real_project``, partagé par toute la suite) porte
deux tâches posées par la CLI, donc par le même ``TaskService`` que
l'interface :

- « Preuves déclarées » : cinq preuves attendues, pour voir ce que dit l'en-tête
  de la colonne (``porte : requiert …``) ;
- « Prête à bloquer » : réclamable, pour le champ « raison du blocage ».

Couvre : sélection d'une ligne de la Liste, anneau de porte des cartes,
bandeau de porte par colonne, champ « raison du blocage » (refus sans raison,
transition avec) et filtre de gravité de la Timeline.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, Route

from .conftest import _alive, _free_port, _wait_ready

_EVIDENCE = ["acceptance_criteria", "owner_or_agent_role", "evidence_pack", "test_report", "review_note"]
_BLOCK_REASON = "attend la clé d'API du fournisseur"


def _grimoire(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )


def _add(root: Path, title: str, *extra: str) -> str:
    added = _grimoire(root, "task", "add", title, "-a", "un critère observable", "--owner", "winston", *extra)
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche ici : {added.stderr[-400:]}")
    return match.group(1)


_QUICK_TITLE = "Carte des actions rapides"
#: Identifiant de la tâche des actions rapides, posé par ``served_inspector``.
_QUICK: dict[str, str] = {}


@pytest.fixture(scope="session")
def served_inspector(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, str]]:
    root = tmp_path_factory.mktemp("exec-gaps") / "projet-gaps"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    _grimoire(root, "init", ".", "-y", "--name", "projet-gaps", "--backend", "local")
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    _grimoire(root, "standard", "init", "--profile", "governed")
    for name in ("events.jsonl", ".events.jsonl.lock"):
        (root / "_grimoire-runtime-output" / "ledger" / name).unlink(missing_ok=True)

    evidence_args = [arg for e in _EVIDENCE for arg in ("--expect-evidence", e)]
    _add(root, "Preuves déclarées", *evidence_args)
    to_block = _add(root, "Prête à bloquer")
    moved = _grimoire(root, "task", "move", to_block, "--to", "ready")
    if moved.returncode != 0:
        pytest.skip(f"préparation de {to_block} interrompue : {moved.stderr[-400:]}")
    # Une tâche à part pour les actions rapides de carte : la fixture vit toute la session et
    # `test_bloquer_avec_une_raison…` bloque « Prête à bloquer » ; lancés dans l'ordre (sans xdist),
    # les tests d'action rapide ne trouvaient plus son bouton « Bloquer… ».
    quick = _add(root, _QUICK_TITLE)
    if _grimoire(root, "task", "move", quick, "--to", "ready").returncode != 0:
        pytest.skip(f"préparation de {quick} interrompue")
    _QUICK["id"] = quick

    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-exec-gaps"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", root, to_block
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def exec_page(browser: Browser, served_inspector: tuple[str, Path, str]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served_inspector[0]}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('executer')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card")
    try:
        yield page
    finally:
        context.close()


def _view(page: Page, label: str) -> None:
    page.locator("#view-seg button", has_text=label).click()


# ── Liste ───────────────────────────────────────────────────────────────────


def test_cliquer_une_ligne_de_la_liste_ouvre_la_tache_dans_l_inspecteur(exec_page: Page) -> None:
    _view(exec_page, "Liste")
    row = exec_page.locator("tr[data-task]", has_text="Prête à bloquer")
    row.wait_for()

    row.click()

    inspector = exec_page.locator("#inspector-body")
    inspector.get_by_text(row.get_attribute("data-task") or "", exact=True).wait_for()
    assert "Prête à bloquer" in inspector.inner_text()


# ── Pastilles de preuves et bandeau de porte ────────────────────────────────


def test_la_carte_rend_la_progression_de_sa_porte_en_anneau(exec_page: Page) -> None:
    """Les anneau de porte des cartes ont cédé la place à un anneau « n/m » (#915) : il dit combien
    de conditions de passage vers la colonne suivante sont réunies, et son titre nomme cette colonne."""
    ring = exec_page.locator(".ex-card", has_text="Prête à bloquer").first.locator("[data-role='proof']")

    assert re.fullmatch(r"\d+/\d+", ring.inner_text().strip()), ring.inner_text()
    assert "conditions de passage vers" in (ring.get_attribute("title") or "")


def test_le_bandeau_de_colonne_nomme_les_preuves_requises(exec_page: Page) -> None:
    _view(exec_page, "Huit colonnes")
    exec_page.wait_for_function("() => document.querySelectorAll('.ex-col').length >= 8")
    column = exec_page.locator(".ex-col", has=exec_page.locator(".ex-card", has_text="Preuves déclarées")).first

    gate = column.locator(".ex-col-gate").inner_text()

    assert gate.startswith("porte : requiert ")
    for evidence in _EVIDENCE[:3]:
        assert evidence in gate
    assert _EVIDENCE[3] not in gate, "le bandeau n'en cite que trois"


def test_une_colonne_sans_preuve_declaree_le_dit(exec_page: Page) -> None:
    _view(exec_page, "Huit colonnes")
    exec_page.wait_for_function("() => document.querySelectorAll('.ex-col').length >= 8")

    gates = [g.inner_text() for g in exec_page.locator(".ex-col .ex-col-gate").all()]

    assert "porte : aucune preuve déclarée" in gates


def test_une_colonne_repliee_du_board_4_nomme_ses_colonnes(exec_page: Page) -> None:
    gates = [g.inner_text() for g in exec_page.locator(".ex-col .ex-col-gate").all()]

    assert any(g.startswith("colonnes repliées : Proposée") for g in gates), gates


# ── Raison du blocage ───────────────────────────────────────────────────────


def _select_blockable(page: Page) -> None:
    page.locator(".ex-card", has_text="Prête à bloquer").first.click()
    page.locator("#inspector-body [data-role='block-reason']").wait_for(timeout=15_000)


def _status_of(root: Path, task_id: str) -> str:
    from grimoire.missions.service import TaskService

    return str(next(t for t in TaskService(root).list_tasks() if t.id == task_id).status)


def test_bloquer_sans_raison_est_refuse_et_ne_bouge_pas_la_tache(
    exec_page: Page, served_inspector: tuple[str, Path, str]
) -> None:
    _, root, task_id = served_inspector
    _select_blockable(exec_page)

    exec_page.locator("#inspector-body button", has_text="Réaliser : bloquer").click()

    exec_page.wait_for_function(
        "() => document.querySelector('#inspector-body .ex-refusal')?.textContent.includes('reason')"
    )
    assert "échec" in exec_page.locator("#inspector-body .ex-refusal").inner_text()
    assert "blocked" not in _status_of(root, task_id)


def test_bloquer_avec_une_raison_passe_la_tache_en_bloquee(
    exec_page: Page, served_inspector: tuple[str, Path, str]
) -> None:
    _, root, task_id = served_inspector
    _select_blockable(exec_page)

    exec_page.locator("#inspector-body [data-role='block-reason']").fill(_BLOCK_REASON)
    exec_page.locator("#inspector-body button", has_text="Réaliser : bloquer").click()

    exec_page.wait_for_function("() => document.querySelector('#inspector-body')?.textContent.includes('transition :')")
    assert "blocked" in _status_of(root, task_id)
    exec_page.wait_for_function(
        "(reason) => [...document.querySelectorAll('.ex-card')].some(c => c.textContent.includes(reason))",
        arg=_BLOCK_REASON,
    )


# ── Filtre de gravité de la Timeline ────────────────────────────────────────


def _fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def test_le_filtre_de_gravite_ne_garde_que_les_causes(exec_page: Page) -> None:
    trace = {
        "task_id": "x",
        "task": None,
        "sources": {"ledger": "x"},
        "causes": [],
        "entries": [
            {
                "at": "2026-10-23T10:00:01Z",
                "source": "ledger",
                "kind": "claim",
                "summary": "claim ok",
                "failure": False,
            },
            {
                "at": "2026-10-23T10:00:02Z",
                "source": "ledger",
                "kind": "gate",
                "summary": "gate refusée",
                "failure": True,
            },
            {"at": "2026-10-23T10:00:03Z", "source": "hooks", "kind": "tool", "summary": "outil ok", "failure": False},
        ],
    }
    exec_page.route("**/api/workspace/tasks/*/trace*", lambda route: _fulfill(route, trace))
    exec_page.locator(".ex-card", has_text="Preuves déclarées").first.click()
    exec_page.locator("#inspector-body").get_by_text("Preuves déclarées").first.wait_for()
    _view(exec_page, "Chronologie")
    exec_page.wait_for_selector(".ex-tl-count")
    assert exec_page.locator(".ex-tl-count").inner_text() == "3/3"

    exec_page.locator(".ex-tl-filters select").nth(1).select_option("causes")

    assert exec_page.locator(".ex-tl-count").inner_text() == "1/3"
    shown = exec_page.locator(".ex-timeline").inner_text()
    assert "gate refusée" in shown
    assert "claim ok" not in shown


# ── Action rapide d'une carte ───────────────────────────────────────────────


def test_les_boutons_d_action_rapide_d_une_carte_ne_propagent_pas_le_clic(
    exec_page: Page, served_inspector: tuple[str, Path, str]
) -> None:
    """« Bloquer… » vit dans la carte, qui se
    sélectionne elle-même au clic : le bouton doit arrêter la propagation, sinon
    le clic remonte à la carte et sélectionne la tâche une seconde fois. Un
    écouteur posé sur la carte (phase de bulle) le prouve sans dépendre d'un
    effet de bord."""
    task_id = _QUICK["id"]
    card = exec_page.locator(".ex-card", has_text=_QUICK_TITLE).first
    card.evaluate(
        "(el) => { window.__parentClicks = 0; el.addEventListener('click', () => { window.__parentClicks += 1; }); }"
    )

    card.get_by_role("button", name="Bloquer…").click()
    exec_page.locator("#inspector-body").get_by_text(task_id, exact=True).wait_for()

    assert exec_page.evaluate("() => window.__parentClicks") == 0


def test_un_clic_sur_la_carte_elle_meme_selectionne_la_tache(
    exec_page: Page, served_inspector: tuple[str, Path, str]
) -> None:
    """Contre-épreuve du test précédent : le même écouteur voit bien un clic sur la carte."""
    task_id = _QUICK["id"]
    card = exec_page.locator(".ex-card", has_text=_QUICK_TITLE).first
    card.evaluate(
        "(el) => { window.__parentClicks = 0; el.addEventListener('click', () => { window.__parentClicks += 1; }); }"
    )

    card.locator("h4, .ex-card-title, strong").first.click()

    exec_page.locator("#inspector-body").get_by_text(task_id, exact=True).wait_for()
    assert exec_page.evaluate("() => window.__parentClicks") == 1
