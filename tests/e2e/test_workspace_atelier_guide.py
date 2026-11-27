"""Atelier de spec, peaufinage (#1197) : le cadrage guidé SANS agent, et la boucle après « Valider et découper ».

Sans agent joignable, la page joue l'entretien du kit (aucun modèle) : questions une à une, réponse consignée dans le
bon bloc, « Je ne sais pas », retour à la question précédente, reprise après rechargement. Après le découpage, le
chantier avance d'après les faits, ses tâches sont listées avec un lien vers l'espace Travail, et la liste dit ce qui
attend la personne. Sans MOA déclarée, la page donne la clé exacte à poser.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures ; sans la variable, elles sont ignorées.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser

from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_assistant import APPEAR_MS, _capture, _init, _open, _serve

EXIGENCES = """# Exigences

- REQ-001 [Must] : maintenir un bouton pour dicter une idée
  - la transcription apparaît en moins de 2 secondes
- REQ-002 (Should) : repli sur le navigateur sans GPU
  - `pytest tests/test_repli.py`
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _human_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """La MOA au terminal, simulée pour *préparer* l'état ; l'écran n'a aucun moyen de valider."""
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


@pytest.fixture(scope="module")
def sans_agent(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, str]]:
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    root, slug = _init(tmp_path_factory, "guide-on", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root, slug


def _declare_moa(root: Path, name: str) -> None:
    from ruamel.yaml import YAML

    yaml = YAML()
    path = root / "project-context.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"))
    governance = data.get("governance") or {}
    governance["roles"] = {**(governance.get("roles") or {}), "moa": name}
    data["governance"] = governance
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


@pytest.fixture(scope="module")
def avec_moa(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "boucle-on", roots):
        _declare_moa(roots[0], "Ada")
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


@pytest.fixture(scope="module")
def sans_moa(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "boucle-sans-moa", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _read(root: Path, slug: str, name: str) -> str:
    return (root / "_grimoire" / "cadrage" / slug / name).read_text(encoding="utf-8")


def test_cadrage_guide_sans_agent_de_bout_en_bout(browser: Browser, sans_agent: tuple[str, Path, str]) -> None:
    url, root, slug = sans_agent
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        # Le moteur est dit en mots simples ; le bouton est vivant (plus de commandes de terminal seules).
        page.locator("[data-role='engine-label']").wait_for(timeout=APPEAR_MS)
        assert "Questions guidées, sans IA" in page.locator("[data-role='engine-label']").inner_text()
        start = page.locator("[data-act='start']")
        assert start.is_enabled()
        start.click()

        # Une question à la fois, avec sa place dans l'entretien.
        question = page.locator("[data-role='guided-question']")
        question.wait_for(timeout=APPEAR_MS)
        assert "Question 1 sur" in question.inner_text()
        first = page.locator("[data-role='guided-text']").inner_text()
        assert page.locator("[data-act='previous']").is_disabled()  # rien à corriger avant la première

        # Une réponse : consignée dans le bon bloc du document, puis la question suivante.
        page.locator(".at-asst-area").fill("Les mainteneurs du kit.")
        page.locator(".at-asst-area").press("Enter")
        page.locator(".at-sheet", has_text="Les mainteneurs du kit.").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='guided-question']", has_text="Question 2 sur").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='guided-text']").inner_text() != first
        assert "- Les mainteneurs du kit." in _read(root, slug, "01-brief.md")

        # Retour à la question précédente : la ligne quitte le document, la réponse revient dans le champ.
        page.locator("[data-act='previous']").click()
        page.locator("[data-role='guided-question']", has_text="Question 1 sur").wait_for(timeout=APPEAR_MS)
        page.wait_for_function("() => document.querySelector('.at-asst-area')?.value === 'Les mainteneurs du kit.'")
        assert "Les mainteneurs du kit." not in _read(root, slug, "01-brief.md")
        page.locator(".at-asst-area").fill("Les mainteneurs et les relecteurs du kit.")
        page.locator("[data-act='answer']").click()
        page.locator(".at-sheet", has_text="relecteurs du kit").wait_for(timeout=APPEAR_MS)
        assert "- Les mainteneurs et les relecteurs du kit." in _read(root, slug, "01-brief.md")

        # « Je ne sais pas » : une hypothèse à valider, l'angle suivant.
        page.locator("[data-role='guided-question']", has_text="Question 2 sur").wait_for(timeout=APPEAR_MS)
        page.locator("[data-act='unknown']").click()
        page.locator("[data-role='atelier-status']", has_text="hypothèse").wait_for(timeout=APPEAR_MS)
        assert "À valider (je ne sais pas)" in _read(root, slug, "03-comprehension.md")
        # La capture montre le panneau, pas la pile de notifications (certaines se ferment seules : on n'attend pas leur clic).
        page.evaluate("() => document.querySelectorAll('[data-act=\"toast-close\"]').forEach((b) => b.click())")
        _capture(page, "02-cadrage-guide-question")

        # Reprise après rechargement : la même question revient, déduite de l'état écrit.
        page.locator("[data-role='guided-text']", has_text="À quoi voit-on").wait_for(timeout=APPEAR_MS)  # l'angle est clos
        before = page.locator("[data-role='guided-text']").inner_text()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator("[data-role='guided-text']").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='guided-text']").inner_text() == before
        # L'avancement dit la vérité : « je ne sais pas » est couvert, mais des blocs restent au gabarit.
        assert "au gabarit" in page.locator("[data-role='avancement']").inner_text()


def test_boucle_apres_le_decoupage(browser: Browser, avec_moa: tuple[str, Path]) -> None:
    url, root = avec_moa
    made = new_chantier(root, "Du découpage à la tâche")
    slug = made.slug
    (root / "_grimoire" / "cadrage" / slug / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")  # le geste de la MOA, au terminal
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator("[data-act='split']").wait_for(timeout=APPEAR_MS)
        page.wait_for_function("() => !document.querySelector('[data-act=\"split\"]').disabled", timeout=APPEAR_MS)
        assert "Aucune tâche" not in page.locator("[data-role='chantier-taches']").inner_text()
        page.locator("[data-act='split']").click()
        page.locator("[data-act='split-confirm']").click()
        # Le bandeau donne l'identifiant COMPLET de chaque tâche, avec un lien ; la page du chantier les liste.
        done = page.locator("[data-role='split-done'] [data-act='open-task']")
        done.first.wait_for(timeout=APPEAR_MS)
        task_id = done.first.inner_text()
        assert len(task_id) > 4 and "…" not in task_id
        listed = page.locator("[data-role='chantier-taches'] [data-task]")
        listed.first.wait_for(timeout=APPEAR_MS)
        assert listed.first.get_attribute("data-task") == task_id
        assert page.locator(".at-title .badge").first.inner_text().strip() == "En cours"
        page.locator(".at-title").scroll_into_view_if_needed()
        _capture(page, "03-apres-decoupage")
        # Le lien ouvre l'espace Travail sur cette tâche.
        listed.first.locator("[data-act='open-task']").click()
        page.wait_for_function("(id) => location.hash.includes('travail') && location.hash.includes(id)", arg=task_id, timeout=APPEAR_MS)
        # La liste des chantiers : l'état d'après les faits, et ce qui attend (ici, plus rien à valider).
        page.evaluate("() => { location.hash = '#atelier'; }")
        row = page.locator(f"[data-slug='{slug}']")
        row.wait_for(timeout=APPEAR_MS)
        assert "En cours" in row.inner_text()
        assert "à valider" not in row.inner_text()
    chantier = (root / "_grimoire" / "cadrage" / slug / "chantier.yaml").read_text(encoding="utf-8")
    assert 'etat: "en-cours"' in chantier  # l'état écrit suit le fait, pas une saisie


def test_sans_moa_la_page_donne_la_cle_a_poser(browser: Browser, sans_moa: tuple[str, Path]) -> None:
    url, root = sans_moa
    slug = new_chantier(root, "Sans maîtrise d'ouvrage").slug
    (root / "_grimoire" / "cadrage" / slug / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        help_ = page.locator("[data-role='moa-help']")
        # Repliée par défaut derrière un lien court ; le lien l'ouvre sur place.
        page.locator("[data-act='moa-why']").wait_for(timeout=APPEAR_MS)
        assert not help_.is_visible()
        page.locator("[data-act='moa-why']").click()
        help_.wait_for(state="visible", timeout=APPEAR_MS)
        assert "governance.roles.moa" in help_.inner_text() and "project-context.yaml" in help_.inner_text()
        assert "moa: " in page.locator("[data-role='moa-example']").inner_text()
        assert help_.get_by_role("button").count() == 1  # copier l'exemple : aucun bouton n'écrit la MOA
        # La consigne de validation dit où lancer la commande et ce que l'écran affichera.
        page.locator("[data-act='validate']").first.click()
        guidance = page.locator("[data-role='validation-help']").first
        guidance.wait_for(timeout=APPEAR_MS)
        assert "toi-même" in guidance.inner_text() and "hors de toute session d’agent" in guidance.inner_text()
        assert "validée" in guidance.inner_text()
