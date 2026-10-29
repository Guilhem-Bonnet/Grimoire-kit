"""L'inspecteur de tâche de Travail (P11) : les gates et leur remède AVANT toute transition.

Un projet réel servi par ``grimoire serve``, avec une tâche en revue dont la
clôture a déjà été refusée (un ``task.gate.refused`` au TraceLedger), une tâche
en revue jamais tentée et une tâche prête. Ce que chaque test prouve :

- pour une tâche en revue, l'inspecteur liste chaque gate de chaque passage
  déclaré (``next_moves_require``), vert ou rouge, avec sa raison et son remède,
  et marque ceux que le dernier ``task.gate.refused`` a nommés — le tout avant le
  premier bouton de transition ;
- la classe V0/V1/V2, le modèle recommandé et le coût se lisent sur la carte ET
  dans l'inspecteur ; un coût que personne n'a mesuré s'écrit « inconnu »,
  jamais 0 ;
- dans le DOM de l'inspecteur : gates, puis bouton de transition, puis critères,
  preuves et session ; « Annuler » n'existe que derrière le menu « ⋯ » ;
- un refus de transition se lit sur place, raison et remède, sans le dock.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les captures (1440x900, clair et sombre,
tâche en revue et tâche prête) sont écrites ; sans la variable ``_capture`` ne
fait rien (jamais ``pytest.skip`` : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

APPEAR_MS = 10_000

# Deux passages déclarés depuis la revue : accepter (acceptation + verdict, plus
# le reçu de revue que la clôture exige toujours) et renvoyer en cours (trace).
GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: ready_to_in_progress
    from: ready
    to: in_progress
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["acceptance_criteria", "review_gate"]
  - id: review_to_in_progress
    from: review
    to: in_progress
    required_evidence: ["decision_trace"]
profile_strictness:
  governed: hard_fail
"""

REFUSED_TITLE = "Inspecteur P11 : clôture déjà refusée une fois"
REVIEW_TITLE = "Inspecteur P11 : revue jamais tentée"
READY_TITLE = "Inspecteur P11 : tâche prête au critère mécanique"


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


@pytest.fixture(scope="module")
def p11_project(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskRefusedError, TaskService

    root = tmp_path_factory.mktemp("travail-p11") / "projet-travail-p11"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-travail-p11", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet de P11 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    ids: dict[str, str] = {}

    def to_review(title: str) -> str:
        task_id = service.add(title, ("un critère observable",), actor="t", owner="guilhem", ready=True).task.id
        service.claim(task_id, "agent-a", host="host-claude-code-cli")
        service.transition(task_id, TaskState.RUNNING, "agent-a")
        service.transition(task_id, TaskState.NEEDS_VERIFICATION, "agent-a")
        ids[title] = task_id
        return task_id

    refused = to_review(REFUSED_TITLE)
    now = datetime.now(UTC)
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "session-p11a0001.json").write_text(
        json.dumps({
            "schema_version": 1, "session_id": "p11a0001", "started_at": (now - timedelta(minutes=12)).isoformat(),
            "updated_at": (now - timedelta(minutes=1)).isoformat(), "task_id": refused, "rules": {},
        }),
        encoding="utf-8",
    )
    service.attach_session(refused, "p11a0001", session_host="claude-code-cli", actor="agent-a")
    # La clôture tentée par l'agent : refusée par le gate, journalisée en `task.gate.refused`.
    with pytest.raises(TaskRefusedError):
        service.transition(refused, TaskState.CLOSED, "agent-a")
    to_review(REVIEW_TITLE)
    ids[READY_TITLE] = service.add(
        READY_TITLE, ("`pytest tests/unit/test_inspecteur.py` est vert",), actor="t", owner="kit-dev", ready=True,
    ).task.id
    return root, ids


@pytest.fixture(scope="module")
def p11_url(p11_project: tuple[Path, dict[str, str]], tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root, _ids = p11_project
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p11"))
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


def _set_theme(page: Page, theme: str) -> None:
    page.evaluate("(t) => document.querySelector(`[data-theme-pref=\"${t}\"]`).click()", theme)
    page.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)


@pytest.fixture
def page(browser: Browser, p11_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p11_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _set_theme(opened, "light")
    try:
        yield opened
    finally:
        context.close()


def _goto(page: Page) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector(".ex-card", timeout=APPEAR_MS)


def _card(page: Page, title: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title).first


def _open(page: Page, title: str):  # type: ignore[no-untyped-def]
    _goto(page)
    _card(page, title).click()
    inspector = page.locator("#inspector-body")
    inspector.locator("h3", has_text=title).wait_for(timeout=APPEAR_MS)
    inspector.locator("[data-role='gates']").wait_for(timeout=APPEAR_MS)
    return inspector


def _api(page: Page, path: str) -> dict:  # type: ignore[type-arg]
    response = page.request.get(page.url.split("/workspace/")[0] + path)
    assert response.ok, path
    return response.json()


def _precedes(page: Page, first: str, second: str) -> bool:
    """Vrai si le premier élément de *first* précède celui de *second* dans le DOM de l'inspecteur."""
    return page.evaluate(
        """([a, b]) => {
            const root = document.querySelector('#inspector-body');
            const x = root.querySelector(a), y = root.querySelector(b);
            return !!x && !!y && !!(x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING);
        }""",
        [first, second],
    )


# ── Critère 1 : chaque gate, vert ou rouge, raison et remède, avant le bouton ──


def test_une_tache_en_revue_liste_chaque_gate_avec_raison_et_remede_avant_la_transition(
    page: Page, p11_project: tuple[Path, dict[str, str]]
) -> None:
    _root, ids = p11_project
    task_id = ids[REFUSED_TITLE]
    inspector = _open(page, REFUSED_TITLE)
    detail = _api(page, f"/api/workspace/tasks/{task_id}")

    # Chaque passage déclaré depuis la revue a ses conditions, verdict du serveur à l'appui.
    assert set(detail["next_moves_require"]) == {"accepted", "in_progress"}
    gates = inspector.locator("[data-role='gates']")
    for target, required in detail["next_moves_require"].items():
        conditions = {c["gate"]: c for c in detail["gate_checks"][target]["conditions"]}
        assert set(required) <= set(conditions), target
        for name, condition in conditions.items():
            node = gates.locator(f".ex-cond[data-gate='{name}']")
            expect(node).to_have_count(1)
            assert node.get_attribute("data-ok") == ("true" if condition["ok"] else "false"), name
            if not condition["ok"]:
                assert node.locator("[data-role='gate-reason']").inner_text().lower() == condition["reason"].lower()
                assert node.locator("[data-role='gate-remedy']").inner_text() == f"Remède : {condition['remedy']}"
    # Une verte, des rouges : le verdict n'est pas uniforme.
    assert gates.locator(".ex-cond[data-ok='true']").count() >= 1
    assert gates.locator(".ex-cond[data-ok='false'] [data-role='gate-remedy']").count() >= 2

    # Le dernier `task.gate.refused` : la ligne du refus et les gates qu'il a nommés.
    refusal = detail["last_gate_refusal"]
    assert refusal and refusal["to"] == "accepted" and "review_gate" in refusal["missing"]
    line = gates.locator("[data-role='last-refusal']")
    assert refusal["transition_id"] in line.inner_text()
    for name in refusal["missing"]:
        assert name in line.inner_text()
    marked = gates.locator(".ex-cond[data-refused]")
    assert {marked.nth(i).get_attribute("data-gate") for i in range(marked.count())} == set(refusal["missing"])
    assert "déjà refusée le" in gates.locator(".ex-cond[data-gate='review_gate']").inner_text()

    # Tout cela AVANT le premier bouton de transition.
    assert _precedes(page, "[data-role='gates']", "[data-role='transition']")
    assert _precedes(page, "[data-role='last-refusal']", "[data-role='transition']")
    assert _precedes(page, ".ex-cond[data-gate='decision_trace']", "[data-role='transition']")


def test_une_revue_jamais_tentee_n_affiche_aucun_refus_invente(page: Page) -> None:
    inspector = _open(page, REVIEW_TITLE)
    assert inspector.locator("[data-role='last-refusal']").count() == 0
    assert inspector.locator(".ex-cond[data-refused]").count() == 0
    assert inspector.locator(".ex-cond[data-ok='false']").count() >= 1


# ── Critère 2 : classe, modèle, coût — carte et inspecteur ─────────────────


def test_classe_modele_et_cout_se_lisent_sur_la_carte_et_dans_l_inspecteur(
    page: Page, p11_project: tuple[Path, dict[str, str]]
) -> None:
    _root, ids = p11_project
    _goto(page)
    cards = {t["id"]: t for t in _api(page, "/api/workspace/tasks")["tasks"]}

    expected_model = {READY_TITLE: "sonnet", REVIEW_TITLE: "modèle de session"}
    expected_class = {READY_TITLE: "V1", REVIEW_TITLE: "V2"}
    for title, model in expected_model.items():
        data = cards[ids[title]]
        assert data["verifiability_class"] == expected_class[title]
        assert data["recommended_model"] == (None if model == "modèle de session" else model)
        card = _card(page, title)
        route = card.locator("[data-role='route']")
        assert route.inner_text() == f"{expected_class[title]} · {model}"
        assert "classe de route" in (route.get_attribute("title") or "")
        # Le coût est sur chaque carte, backlog compris : personne ne l'a mesuré, il est inconnu.
        # P91 : « coût inconnu » ne s'écrit plus sur la carte (il reste dans l'inspecteur).
        assert card.locator("[data-role='cost']").count() == 0

        card.click()
        inspector = page.locator("#inspector-body")
        inspector.locator("h3", has_text=title).wait_for(timeout=APPEAR_MS)
        routing = inspector.locator("[data-role='routing']")
        assert routing.inner_text() == f"classe {expected_class[title]} · {model} · coût inconnu"
    assert "0.0000" not in page.locator("#canvas").inner_text() + page.locator("#inspector-body").inner_text(), (
        "un coût inconnu ne s'écrit jamais 0"
    )


# ── Critère 3 : ordre du DOM, « Annuler » derrière « ⋯ » ────────────────────


def test_l_inspecteur_ordonne_gates_transition_criteres_preuves_session_et_cache_annuler(page: Page) -> None:
    inspector = _open(page, REFUSED_TITLE)

    order = [
        "[data-role='gates']",
        "[data-role='transition']",
        "[data-role='acceptance-block']",
        "[data-role='evidence-block']",
        "[data-role='session-block']",
    ]
    for first, second in pairwise(order):
        assert _precedes(page, first, second), f"{first} doit précéder {second}"
    assert "p11a0001" in inspector.locator("[data-role='session-block']").inner_text()

    # « Annuler » n'est accessible que par le menu « ⋯ » : invisible avant, nulle part ailleurs.
    more = inspector.locator("button[data-role='more']")
    assert more.get_attribute("aria-label") == "Plus d'actions"
    assert more.get_attribute("aria-expanded") == "false"
    expect(inspector.get_by_role("button", name="Annuler la tâche")).to_have_count(0)
    expect(inspector.locator("[data-role='cancel-reason']")).to_be_hidden()
    outside = inspector.locator("button:visible").filter(has_text="Annuler")
    assert outside.count() == 0

    more.click()
    expect(more).to_have_attribute("aria-expanded", "true")
    panel = inspector.locator("[data-role='more-panel']")
    expect(panel.get_by_role("button", name="Annuler la tâche")).to_be_visible()
    expect(panel.get_by_role("button", name="Annuler la tâche")).to_be_disabled()
    assert inspector.locator("[data-role='cancel-submit']").count() == 1, "« Annuler » n'existe qu'une fois, dans le menu"
    # Échap referme le menu et rend le focus au bouton « ⋯ ».
    panel.locator("[data-role='cancel-reason']").press("Escape")
    expect(panel).to_be_hidden()
    assert page.evaluate("document.activeElement && document.activeElement.dataset.role") == "more"


# ── Un refus se lit sur place ────────────────────────────────────────────────


def test_un_refus_de_transition_se_lit_sur_place_avec_son_remede(
    page: Page, p11_project: tuple[Path, dict[str, str]]
) -> None:
    from grimoire.missions.service import TaskService

    root, ids = p11_project
    inspector = _open(page, REVIEW_TITLE)
    # Accepter ne se défait pas (P57) : une confirmation sur place, jamais de toast.
    inspector.get_by_role("button", name="Réaliser : → Acceptée").click()
    with page.expect_response(lambda r: f"/tasks/{ids[REVIEW_TITLE]}/close" in r.url):
        inspector.locator("[data-role='transition-confirm-yes']").click()
    refusal = inspector.locator("[data-role='gate-card'] [data-role='refusal']")
    expect(refusal).to_be_visible()
    assert "refusé par le gate" in refusal.inner_text()
    assert "review_gate" in refusal.inner_text() and "Remède :" in refusal.inner_text()
    assert TaskService(root).require(ids[REVIEW_TITLE]).status.value == "needs_verification"


# ── Captures (conformité à l'artboard Travail) ──────────────────────────────


def test_captures_revue_et_prete_clair_et_sombre(page: Page) -> None:
    for theme in ("light", "dark"):
        _set_theme(page, theme)
        _open(page, REFUSED_TITLE)
        _capture(page, f"p11-revue-{theme}-1440")
        _open(page, READY_TITLE)
        _capture(page, f"p11-prete-{theme}-1440")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
