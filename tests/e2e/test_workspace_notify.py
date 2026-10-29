"""Prévenir d'une décision attendue, onglet masqué ou fermé (I1).

Preuves jouées dans un vrai navigateur, contre un vrai ``grimoire serve`` : le titre de l'onglet compte
les décisions, l'interrupteur « Me prévenir dans le navigateur » émet UNE notification par nouvelle
question (même après un rechargement), et le gel de l'onglet masqué ne cède que pour lui. La classe
``Notification`` est remplacée par un faux (``add_init_script``) qui journalise dans ``localStorage`` :
le journal survit au ``page.reload()``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.missions.decisions import pending_decisions
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskClaim, TaskState
from grimoire.missions.service import TaskService

from .conftest import _serve_empty_project

_INIT = """
(() => {
  const log = () => JSON.parse(localStorage.getItem('__notifs') || '[]');
  class FakeNotification {
    constructor(title, options) {
      const list = log();
      list.push({ title, body: (options || {}).body || '', tag: (options || {}).tag || '' });
      localStorage.setItem('__notifs', JSON.stringify(list));
      this.onclick = null;
    }
    close() {}
    static requestPermission() { FakeNotification.permission = 'granted'; localStorage.setItem('__granted', '1'); return Promise.resolve('granted'); }
  }
  FakeNotification.permission = localStorage.getItem('__granted') ? 'granted' : (window.__permission || 'default');
  window.Notification = FakeNotification;
  const Real = window.EventSource;
  window.__streams = [];
  window.EventSource = class extends Real {
    constructor(...args) { super(...args); window.__streams.push(this); }
  };
})();
"""
_HIDE = """() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  document.dispatchEvent(new Event('visibilitychange'));
}"""
_SHOW = """() => {
  delete document.visibilityState;
  delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
}"""


@pytest.fixture(scope="module")
def served_notify(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "workspace-notify", roots):
        yield url, roots[0]


def _running(service: TaskService, title: str) -> str:
    tid = service.add(title, ("critère",), owner="amelia", actor="t").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="agent-a", claim=claim)
    return tid


def _ask(service: TaskService, title: str, text: str) -> str:
    tid = _running(service, title)
    service.ask(tid, text, "agent-a", kind="choice", options=("oui", "non"))
    return tid


def _settle(service: TaskService, task_id: str) -> None:
    """Répondue, la tâche reprend : sa consigne non accusée et son claim sans session sont d'autres
    éléments d'attention que ce test ne cherche pas. On les écarte pour ne compter que les décisions."""
    for directive in service.require(task_id).directives:
        service.acknowledge(task_id, directive.id, "agent-a")
    service.cancel(task_id, "essai terminé", "guilhem", force=True)


def _answer_all(service: TaskService) -> None:
    for decision in pending_decisions(service.ledger):
        service.answer(decision.task_id, "oui", "guilhem", origin=ORIGIN_HUMAN, question_id=decision.question.id)
        _settle(service, decision.task_id)


def _answer(service: TaskService, task_id: str) -> None:
    (decision,) = [d for d in pending_decisions(service.ledger) if d.task_id == task_id]
    service.answer(task_id, "oui", "guilhem", origin=ORIGIN_HUMAN, question_id=decision.question.id)
    _settle(service, task_id)


def _open(browser: Browser, url: str, *, permission: str = "default", width: int = 1440) -> Page:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce", color_scheme="dark")
    page = context.new_page()
    page.add_init_script(f"window.__permission = {json.dumps(permission)};")
    page.add_init_script(_INIT)
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    return page


def _notifs(page: Page) -> list[dict[str, str]]:
    return page.evaluate("() => JSON.parse(localStorage.getItem('__notifs') || '[]')")


@pytest.fixture
def service(served_notify: tuple[str, Path]) -> TaskService:
    service = TaskService(served_notify[1])
    _answer_all(service)
    return service


def test_titre_compte_decisions(browser: Browser, served_notify: tuple[str, Path], service: TaskService) -> None:
    first = _ask(service, "Choisir le format", "Quel format pour l'export ?")
    second = _ask(service, "Choisir le nom", "Quel nom pour la piste ?")
    page = _open(browser, served_notify[0])
    try:
        page.wait_for_function("() => document.title.startsWith('(2) Grimoire')", timeout=20_000)
        assert page.title().startswith("(2) Grimoire")
        _answer(service, first)
        page.wait_for_function("() => document.title.startsWith('(1) Grimoire')", timeout=20_000)
        _answer(service, second)
        page.wait_for_function("() => !document.title.startsWith('(')", timeout=20_000)
        assert page.title() == "Grimoire — vue de travail"
        assert page.locator("link[data-role='notify-icon']").count() == 0
    finally:
        page.context.close()


def test_pastille_du_favicon_suit_le_compteur(browser: Browser, served_notify: tuple[str, Path], service: TaskService) -> None:
    tid = _ask(service, "Choisir la couleur", "Quelle couleur ?")
    page = _open(browser, served_notify[0])
    try:
        page.wait_for_selector("link[data-role='notify-icon']", state="attached", timeout=20_000)
        assert page.locator("link[data-role='notify-icon']").get_attribute("href").startswith("data:image/png")
        _answer(service, tid)
        page.wait_for_selector("link[data-role='notify-icon']", state="detached", timeout=20_000)
    finally:
        page.context.close()


def test_notification_dedupliquee(browser: Browser, served_notify: tuple[str, Path], service: TaskService) -> None:
    page = _open(browser, served_notify[0])
    try:
        switch = page.locator("#st-notify")
        expect(switch).to_have_attribute("aria-pressed", "false")
        switch.click()
        expect(switch).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#notify-live")).to_contain_text("Notifications activées")
        _ask(service, "Choisir le port", "Quel port pour le banc ?")
        page.wait_for_function("() => JSON.parse(localStorage.getItem('__notifs') || '[]').length === 1", timeout=20_000)
        (popup,) = _notifs(page)
        assert "Choisir le port" in popup["title"] and "Quel port" in popup["body"]
        # Rechargement : la question est déjà signalée, aucune seconde notification.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.wait_for_function("() => document.title.startsWith('(')", timeout=20_000)
        expect(page.locator("#st-notify")).to_have_attribute("aria-pressed", "true")
        page.wait_for_timeout(2500)
        assert len(_notifs(page)) == 1
        # Interrupteur coupé : une nouvelle question n'appelle plus rien.
        page.locator("#st-notify").click()
        expect(page.locator("#st-notify")).to_have_attribute("aria-pressed", "false")
        count = int(page.title().split(")")[0].removeprefix("("))
        _ask(service, "Choisir la clé", "Quelle clé de chiffrement ?")
        page.wait_for_function(f"() => document.title.startsWith('({count + 1})')", timeout=20_000)
        assert len(_notifs(page)) == 1
    finally:
        page.context.close()


def test_onglet_masque_interrupteur_actif_le_flux_reste_ouvert(
    browser: Browser, served_notify: tuple[str, Path], service: TaskService
) -> None:
    page = _open(browser, served_notify[0])
    try:
        page.locator("#st-notify").click()
        expect(page.locator("#st-notify")).to_have_attribute("aria-pressed", "true")
        page.wait_for_timeout(500)
        page.evaluate(_HIDE)
        page.wait_for_timeout(1500)
        assert page.evaluate("() => window.__streams.length > 0 && window.__streams.at(-1).readyState === 1")
        # Masqué, une nouvelle question prévient quand même.
        _ask(service, "Choisir le fuseau", "Quel fuseau horaire ?")
        page.wait_for_function("() => JSON.parse(localStorage.getItem('__notifs') || '[]').length === 1", timeout=20_000)
        page.evaluate(_SHOW)
    finally:
        page.context.close()


def test_onglet_masque_interrupteur_coupe_aucune_requete(
    browser: Browser, served_notify: tuple[str, Path], service: TaskService
) -> None:
    page = _open(browser, served_notify[0])
    try:
        page.wait_for_timeout(500)
        page.evaluate(_HIDE)
        page.wait_for_timeout(500)
        seen: list[str] = []
        page.on("request", lambda request: seen.append(request.url) if "/api/" in request.url else None)
        _ask(service, "Choisir la police", "Quelle police ?")
        page.wait_for_timeout(8000)
        assert seen == []
        assert page.evaluate("() => window.__streams.every((s) => s.readyState === 2)")
    finally:
        page.context.close()


def test_interrupteur_est_un_vrai_bouton_et_tient_sur_390_px(
    browser: Browser, served_notify: tuple[str, Path]
) -> None:
    page = _open(browser, served_notify[0], width=390)
    try:
        switch = page.locator("#st-notify")
        assert switch.evaluate("(node) => node.tagName") == "BUTTON"
        expect(switch).to_have_attribute("aria-pressed", "false")
        assert switch.get_attribute("title")
        assert "prévenir" in (switch.inner_text() + (switch.get_attribute("aria-label") or "")).lower()
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("#notify-live").get_attribute("aria-live") == "polite"
    finally:
        page.context.close()


def test_la_commande_de_notification_se_regle_et_s_essaie_dans_configuration(
    browser: Browser, served_notify: tuple[str, Path], tmp_path: Path
) -> None:
    page = _open(browser, served_notify[0])
    try:
        page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "fournisseurs")
        card = page.locator("[data-role='notify-options']")
        card.wait_for(timeout=20_000)
        field = card.locator("[data-role='notify-input']")
        status = card.locator("[data-role='notify-status']")
        # Vide : l'essai dit pourquoi il ne part pas.
        field.fill("")
        card.locator("[data-role='notify-test']").click()
        expect(status).to_contain_text("aucune commande")
        out = tmp_path / "essai.txt"
        field.fill(f"python3 -c \"open('{out}', 'w').write('ok')\" {{message}}")
        card.locator("[data-role='notify-save']").click()
        expect(status).to_contain_text("enregistrée")
        card.locator("[data-role='notify-test']").click()
        expect(status).to_contain_text("Essai réussi")
        assert out.read_text(encoding="utf-8") == "ok"
        field.fill("")
        card.locator("[data-role='notify-save']").click()
        expect(status).to_contain_text("retirée")
    finally:
        page.context.close()
