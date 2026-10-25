"""Travail conforme à l'artboard (P64) : en-tête, cartes, panneau de détail, saisie préservée.

Un projet réel et peuplé (tâches dans plusieurs colonnes, une session vivante, une
carte en revue dont une condition de passage est rouge), servi par ``grimoire serve``.
Ce que chaque test prouve :

- l'en-tête porte le titre, les vues, le filtre « Demande une action de moi » et la
  formule CLI ; le filtre garde ce qui attend un humain (question, revue, blocage) ;
- une carte dit son identifiant, sa classe de route, son anneau de preuve (un segment
  par condition du gate), son titre, sa session vivante, son blocage ;
- la carte sélectionnée ouvre le panneau de détail avec les conditions de passage
  vertes ou rouges et leur gate — le verdict est celui du serveur ;
- **la course connue** : un re-rendu du panneau de détail ne vole jamais une saisie en
  cours (valeur, focus, activation du bouton), qu'il soit reporté (rafraîchissement
  live) ou exécuté (clic sur la même carte).

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les captures (clair, 1440x900 carte
sélectionnée, et 390 px) sont écrites ; sans la variable ``_capture`` ne fait rien
(jamais ``pytest.skip`` : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

PHONE = {"width": 390, "height": 844}
APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria"]
  - id: ready_to_in_progress
    from: ready
    to: in_progress
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["acceptance_criteria", "review_gate"]
profile_strictness:
  governed: hard_fail
"""

READY_TITLE = "Nettoyage du poste : worktrees et branches des PR mergées"
RUNNING_TITLE = "Refonte UI/UX du cockpit : revues d'experts et maquettes Design"
REVIEW_TITLE = "Kanban agentique : livrer les lots B et C et inventorier les restes"
BLOCKED_TITLE = "Commande de hook résolue à l'émission et contrôlée par doctor"
ASK_TITLE = "Flow de mise à jour : décider le checkpoint destructif"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists() if sys.platform == "linux" else True


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _goto(page: Page) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card", timeout=APPEAR_MS)


@pytest.fixture(scope="module")
def travail_project(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("travail-p64") / "projet-travail-p64"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-travail-p64", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet peuplé de P64 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    ids: dict[str, str] = {}

    def add(title: str, owner: str, *, ready: bool = True) -> str:
        added = service.add(title, ("un critère observable",), actor="t", owner=owner, ready=ready)
        ids[title] = added.task.id
        return added.task.id

    add(READY_TITLE, "guilhem")
    add("Mettre à jour le Qdrant partagé du poste, une mineure à la fois", "guilhem")
    add("Documenter la politique de branches", "kit-dev", ready=False)

    running = add(RUNNING_TITLE, "claude")
    service.claim(running, "agent-a", host="host-claude-code-cli")
    service.transition(running, TaskState.RUNNING, "agent-a")
    now = datetime.now(UTC)
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "session-41c89d23.json").write_text(
        json.dumps({
            "schema_version": 1, "session_id": "41c89d23", "started_at": (now - timedelta(minutes=12)).isoformat(),
            "updated_at": (now - timedelta(minutes=1)).isoformat(), "task_id": running, "rules": {},
        }),
        encoding="utf-8",
    )
    service.attach_session(running, "41c89d23", session_host="claude-code-cli", actor="agent-a")

    for title in (BLOCKED_TITLE, "Suppressions destructives PowerShell reconnues par la politique"):
        blocked = add(title, "claude")
        service.claim(blocked, "agent-a", host="host-claude-code-cli")
        service.transition(blocked, TaskState.RUNNING, "agent-a")
        service.transition(blocked, TaskState.BLOCKED, "agent-a", "aucune preuve déclarée")

    for title in (REVIEW_TITLE, "Mettre à jour le Qdrant 1.19.1 : vérification"):
        review = add(title, "guilhem")
        service.claim(review, "agent-a", host="host-claude-code-cli")
        service.transition(review, TaskState.RUNNING, "agent-a")
        service.transition(review, TaskState.NEEDS_VERIFICATION, "agent-a")

    asked = add(ASK_TITLE, "guilhem")
    service.claim(asked, "agent-a", host="host-claude-code-cli")
    service.transition(asked, TaskState.RUNNING, "agent-a")
    service.ask(asked, "Supprimer les fichiers devenus inutiles ?", "agent-a", kind="choice", options=("Oui", "Plus tard"))
    return root, ids


@pytest.fixture(scope="module")
def travail_url(
    travail_project: tuple[Path, dict[str, str]], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    root, _ids = travail_project
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-travail"))
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


def _open(browser: Browser, url: str, viewport: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    context = browser.new_context(
        viewport=viewport or {"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light"
    )
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    # Thème clair explicite (la préférence mémorisée vaut « sombre » par défaut) : les captures se comparent à l'artboard.
    page.evaluate("() => document.querySelector('[data-theme-pref=\"light\"]').click()")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
    return page, context


@pytest.fixture
def page(browser: Browser, travail_url: str) -> Iterator[Page]:
    opened, context = _open(browser, travail_url)
    try:
        yield opened
    finally:
        context.close()


@pytest.fixture
def phone(browser: Browser, travail_url: str) -> Iterator[Page]:
    opened, context = _open(browser, travail_url, PHONE)
    try:
        yield opened
    finally:
        context.close()


def _card(page: Page, title: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title).first


# ── En-tête ─────────────────────────────────────────────────────────────────


def test_l_en_tete_porte_le_titre_les_vues_le_filtre_et_la_formule(page: Page) -> None:
    _goto(page)

    head = page.locator(".ex-head")
    assert head.locator("h2.space-title").inner_text() == "Travail"
    assert page.evaluate("getComputedStyle(document.querySelector('.ex-head .space-title')).fontSize") == "32px"
    # « Sessions » porte son compte (P33) : le libellé se lit sans lui.
    labels = [re.sub(r"\s*\d+$", "", t.strip()) for t in head.locator("#view-seg button").all_inner_texts()]
    assert labels == ["Tableau", "Huit colonnes", "Liste", "Sessions", "Chronologie", "Décisions"]
    assert head.locator("#view-seg button[aria-pressed='true']").inner_text() == "Tableau"
    assert "Demande une action de moi" in head.locator("[data-role='ask-me']").inner_text()
    assert head.locator(".ex-formula").inner_text().replace("\n", "") == "›grimoire task board"
    # La barre de document n'a plus rien à montrer : les vues vivent dans l'en-tête.
    assert not page.locator("#docbar").is_visible()


def test_le_filtre_garde_ce_qui_attend_un_humain(page: Page) -> None:
    _goto(page)
    ask = page.locator("[data-role='ask-me']")
    assert ask.get_attribute("aria-pressed") == "false"
    assert _card(page, READY_TITLE).count() == 1

    ask.click()

    expect(ask).to_have_attribute("aria-pressed", "true")
    expect(_card(page, READY_TITLE)).to_have_count(0)  # une tâche prête n'attend personne
    assert _card(page, RUNNING_TITLE).count() == 0
    for title in (REVIEW_TITLE, BLOCKED_TITLE, ASK_TITLE):
        assert _card(page, title).count() == 1, title
    ask.click()
    expect(_card(page, READY_TITLE)).to_have_count(1)


# ── Cartes et panneau de détail ─────────────────────────────────────────────


def test_une_carte_dit_son_identifiant_sa_route_sa_preuve_sa_session_et_son_blocage(page: Page) -> None:
    _goto(page)

    review = _card(page, REVIEW_TITLE)
    assert review.locator(".ex-card-id").inner_text().startswith("GAO-")
    # P11 : la route se lit « classe · modèle » (« V2 · modèle de session » quand aucun modèle n'est recommandé).
    route = review.locator(".ex-card-top [data-role='route']")
    assert route.get_attribute("data-class") in {"V0", "V1", "V2"}
    assert route.inner_text().startswith(route.get_attribute("data-class") + " · ")
    # Trois conditions au passage à « Acceptée » (acceptation écrite, verdict absent, et
    # le reçu de revue que la clôture exige — P11, comme `TaskService.gate`) : 1 verte sur 3.
    assert review.locator("[data-role='proof']").inner_text() == "1/3"
    assert review.locator(".ex-ring circle.ok").count() == 1
    assert review.locator(".ex-ring circle.bad").count() == 2
    assert "Aucun verdict de vérification" in review.locator("[data-role='missing-condition']").inner_text()

    ready = _card(page, READY_TITLE)
    assert ready.locator("[data-role='proof']").inner_text() == "2/2"
    assert ready.locator(".ex-ring circle.ok").count() == 2

    blocked = _card(page, BLOCKED_TITLE)
    assert "aucune preuve déclarée" in blocked.locator("[data-role='blocked']").inner_text()

    running = _card(page, RUNNING_TITLE)
    assert "41c89d23" in running.locator(".ex-session").inner_text()
    assert "session vivante" in running.locator("[data-role='live-facts']").inner_text()
    assert page.evaluate(
        "(t) => getComputedStyle([...document.querySelectorAll('.ex-card-title')].find((n) => n.textContent === t)).webkitLineClamp",
        RUNNING_TITLE,
    ) == "2"


def test_chaque_fait_de_carte_tient_sur_une_ligne_sans_couper_un_mot(page: Page) -> None:
    """La méta (owner, risque, priorité) et la session (identifiant, hôte) tiennent
    chacune sur une ligne : avant, l'identifiant encadré se cassait en deux, « Claude
    Code » passait à la ligne et la priorité restait seule sur la ligne suivante."""
    _goto(page)
    running = _card(page, RUNNING_TITLE)
    running.wait_for(timeout=APPEAR_MS)
    for selector in (".ex-card-meta", ".ex-session"):
        lines = running.locator(selector).evaluate(
            "(n) => { const lh = parseFloat(getComputedStyle(n).lineHeight) || 18; return Math.round(n.getBoundingClientRect().height / lh); }"
        )
        assert lines == 1, f"{selector} tient sur {lines} lignes"
    # Aucun fait de la ligne de vivacité ne se casse au milieu (chaque fait d'un bloc).
    broken = running.locator("[data-role='live-facts'] > *").evaluate_all(
        "(nodes) => nodes.filter((n) => n.getClientRects().length > 1 || n.getBoundingClientRect().height > 24).map((n) => n.textContent)"
    )
    assert broken == [], broken
    # La raison d'une inactivité et le texte entier d'un blocage restent au survol.
    assert "aucune preuve déclarée" in (_card(page, BLOCKED_TITLE).locator("[data-role='blocked']").get_attribute("title") or "")


def test_la_carte_selectionnee_ouvre_ses_conditions_de_passage_dans_le_panneau_de_detail(page: Page) -> None:
    _goto(page)
    assert not page.locator("#panel-inspector").is_visible(), "sans sélection, pas de colonne de détail"

    review = _card(page, REVIEW_TITLE)
    review.click()
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='gate-card']").wait_for()

    assert page.locator("#panel-inspector").is_visible()
    assert review.get_attribute("aria-current") == "true"
    assert inspector.locator("h3").inner_text() == REVIEW_TITLE
    assert "Pour passer en Acceptée" in inspector.locator("[data-role='gate-card'] .micro").inner_text()
    conditions = inspector.locator("[data-role='gate-conditions'] .ex-cond")
    assert conditions.count() == 3
    assert conditions.nth(0).get_attribute("data-ok") == "true"
    assert conditions.nth(0).get_attribute("data-gate") == "acceptance_criteria"
    red = inspector.locator(".ex-cond[data-ok='false']")
    assert [red.nth(i).get_attribute("data-gate") for i in range(red.count())] == ["review_gate", "review.receipt_missing"]
    assert "review_gate" in red.first.inner_text() and "Aucun verdict de vérification" in red.first.inner_text()
    # Les gestes sont ceux d'avant P64 : la transition réelle (que le gate refusera), pas un bouton inventé.
    assert inspector.get_by_role("button", name="Réaliser : → Acceptée").count() == 1
    assert inspector.locator("[data-role='last-events']").count() == 1
    assert inspector.locator("select[data-role='priority']").count() == 1
    _capture(page, "travail-clair-1440-carte-selectionnee")


def test_captures_mobiles(phone: Page) -> None:
    _goto(phone)
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _capture(phone, "travail-clair-390")
    _card(phone, REVIEW_TITLE).click()
    phone.locator("#inspector-body [data-role='gate-card']").wait_for()
    assert phone.locator("#inspector-body [data-role='gate-card']").is_visible()
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _capture(phone, "travail-clair-390-detail")


# ── La course connue : une saisie en cours n'est jamais écrasée ────────────


def test_un_rafraichissement_live_ne_vole_pas_la_raison_en_cours_de_saisie(
    page: Page, travail_project: tuple[Path, dict[str, str]]
) -> None:
    """Défaut d'origine : un re-rendu du panneau déclenché par le pouls, déjà en vol
    quand l'utilisateur se met à écrire, remplaçait le panneau et effaçait la raison.

    Le re-rendu est suspendu au milieu de ses lectures (la trace est retenue), la
    raison est saisie, puis la lecture est rendue : la saisie doit survivre, avec son
    focus, et « Annuler la tâche » rester actif.
    """
    from grimoire.missions.service import TaskService

    root, ids = travail_project
    task_id = ids["Documenter la politique de branches"]
    _goto(page)
    _card(page, "Documenter la politique de branches").click()
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='gate-card']").wait_for()

    held: list = []
    page.route(f"**/api/workspace/tasks/{task_id}/trace*", lambda route: held.append(route))
    TaskService(root).prioritize(task_id, "high", "t")
    deadline = time.monotonic() + 15
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert held, "le pouls n'a pas déclenché de relecture du panneau"

    # « Annuler » ne vit que dans le menu « ⋯ » (P11).
    inspector.locator("[data-role='more']").click()
    reason = inspector.locator("input[data-role='cancel-reason']")
    reason.fill("doublon d'une autre carte")
    cancel = inspector.locator("button[data-role='cancel-submit']")
    expect(cancel).to_be_enabled()

    for route in held:
        route.continue_()
    page.unroute(f"**/api/workspace/tasks/{task_id}/trace*")
    page.wait_for_timeout(1500)

    assert inspector.locator("input[data-role='cancel-reason']").input_value() == "doublon d'une autre carte"
    expect(inspector.locator("button[data-role='cancel-submit']")).to_be_enabled()
    assert page.evaluate("document.activeElement && document.activeElement.dataset.role") == "cancel-reason"
    # Le re-rendu reporté repart une fois la saisie terminée : la priorité écrite hors navigateur arrive.
    reason.fill("")
    page.locator("#canvas").click(position={"x": 5, "y": 5})
    expect(inspector.locator("select[data-role='priority']")).to_have_value("high", timeout=APPEAR_MS)


def test_un_re_rendu_force_du_panneau_rend_la_saisie_valeur_focus_et_bouton(page: Page) -> None:
    """Le re-rendu qui a lieu quand même (clic sur la carte déjà ouverte) rétablit la saisie."""
    _goto(page)
    _card(page, READY_TITLE).click()
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='gate-card']").wait_for()
    inspector.locator("[data-role='more']").click()
    reason = inspector.locator("input[data-role='cancel-reason']")
    reason.fill("raison à ne pas perdre")
    reason.evaluate("(el) => el.setSelectionRange(3, 7)")
    marker = inspector.locator("[data-role='gate-card']")
    marker.evaluate("(el) => { el.dataset.ancien = '1'; }")

    with page.expect_response(lambda r: "/recall" in r.url):
        _card(page, READY_TITLE).dispatch_event("click")
    page.wait_for_function("() => !document.querySelector('#inspector-body [data-ancien]')")

    again = inspector.locator("input[data-role='cancel-reason']")
    assert again.input_value() == "raison à ne pas perdre"
    expect(inspector.locator("button[data-role='cancel-submit']")).to_be_enabled()
    assert page.evaluate("document.activeElement && document.activeElement.dataset.role") == "cancel-reason"
    assert page.evaluate("[document.activeElement.selectionStart, document.activeElement.selectionEnd]") == [3, 7]
