"""Travail › Décisions lisibles (P68) : une carte par type, reprises en lot, frise sans chevauchement.

Ce que la capture d'avant montrait : toutes les réponses en boutons primaires
pleine largeur dont le texte sortait de la pilule, « reprendre » plein comme
présélectionné, une zone de réponse ouverte sur chaque carte, l'UUID de session
et le jargon serveur en clair, « R52 — » en tête de titre. Ici, chaque type a sa
carte et une seule action primaire ; le texte serveur entier est dans « Détails ».
Les réponses partent par la vraie route (``POST tasks/<id>/answer``) et l'effet
se relit au ledger ; seul le refus du lot est simulé (``page.route``), parce
qu'un refus serveur réel au milieu d'un lot ne se provoque pas de façon stable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import Page, Route, expect

from grimoire.missions.decisions import pending_decisions
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskClaim, TaskState
from grimoire.missions.service import TaskService

LONG_OPTION = (
    "Porter la lecture du TraceLedger et du journal sous src/grimoire/ (nouveau module + test), "
    "et faire déléguer observatory.py en quelques lignes (≤28 de marge)"
)
UNFENCED = (
    "la session {sid} (hôte claude-code-cli, version inconnue, sans frein) ne donne plus signe ; reprendre ? "
    "Frein non prouvé pour cette version : GRIMOIRE_HOST_PROBE=1 la sonde au prochain tick "
    "(deux appels payants à l'hôte), ou `grimoire host status --probe`."
)
LONG_EVIDENCE = "Document ; audit _scratch/audit-restes-20260927/rapport.md ligne R49 ; issue #634"


def _answer_all(service: TaskService) -> None:
    for decision in pending_decisions(service.ledger):
        if decision.question.origin == "flow":
            continue
        service.answer(decision.task_id, "attendre", "guilhem", origin=ORIGIN_HUMAN, question_id=decision.question.id)


def _running(service: TaskService, title: str, session: str = "") -> str:
    tid = service.add(title, ("critère",), owner="amelia", actor="t").task.id
    # Mise en place seulement (projet gouverné, comme test_workspace_executer_question_batch) :
    # la tâche est amenée en `running` par le ledger, l'objet du test est la vue Décisions.
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(tid, state, actor_id="agent-a", claim=claim)
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor="agent-a")
    return tid


def _resume(service: TaskService, title: str, sid: str) -> str:
    tid = _running(service, title, sid)
    service.ask(tid, UNFENCED.format(sid=sid), "agent-a", kind="resume_unfenced", options=("reprendre", "abandonner"))
    return tid


def _open_decisions(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('travail', { view: 'decisions' })")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    page.wait_for_selector("[data-role='decisions']")


def _card(page: Page, tid: str):  # type: ignore[no-untyped-def]
    return page.locator(f"[data-role='decision'][data-task='{tid}']")


def _visible_primaries(card) -> int:  # type: ignore[no-untyped-def]
    return card.locator(".btn.pri").evaluate_all("(nodes) => nodes.filter((n) => n.getClientRects().length).length")


def test_chaque_type_de_decision_a_sa_carte_et_une_seule_action_primaire(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_all(service)
    geste = _running(service, "Poser le secret du banc")
    service.ask(geste, "Poser le secret du job grounding-probe : geste réservé à Guilhem.", "agent-a", kind="permission")
    choix = _running(service, "Faire lire l'export observatoire")
    service.ask(choix, "Comment faire lire les deux journaux ?", "agent-a", kind="choice", options=(
        LONG_OPTION, "Patch minimal dans observatory.py", "Retirer la vue observatoire",
    ))
    libre = _running(service, "Nommer la seconde piste")
    service.ask(libre, "Quel nom pour la seconde piste ?", "agent-a", kind="choice")
    sid = "7616c3bf-a42f-488a-9847-3a225d213b1c"
    reprise = _resume(service, "R14 — Régénérer l'inventaire", sid)

    _open_decisions(workspace)
    order = workspace.locator(".ex-dec-section").evaluate_all("(nodes) => nodes.map((n) => n.dataset.type)")
    assert order == ["geste", "choix", "libre", "reprise"], order
    for tid in (geste, choix, libre, reprise):
        assert _visible_primaries(_card(workspace, tid)) == 1, tid

    # Geste humain : « Fait » primaire, la zone libre repliée derrière « Autre réponse… ».
    card = _card(workspace, geste)
    assert card.get_attribute("data-type") == "geste"
    expect(card.get_by_role("button", name="Fait", exact=True)).to_have_class(re.compile(r"\bpri\b"))
    expect(card.locator("textarea")).to_be_hidden()
    card.get_by_role("button", name="Autre réponse…").click()
    expect(card.locator("textarea")).to_be_visible()
    expect(card.get_by_role("button", name="Autre réponse…")).to_have_attribute("aria-expanded", "true")

    # Question libre : la zone et « Répondre » primaire, ouvertes d'office.
    card = _card(workspace, libre)
    expect(card.locator("textarea")).to_be_visible()
    expect(card.get_by_role("button", name="Répondre")).to_have_class(re.compile(r"\bpri\b"))

    # Choix : liste verticale de boutons-choix, rien de présélectionné, « Valider » désactivé.
    card = _card(workspace, choix)
    options = card.locator("[data-role='answer-option']")
    expect(options).to_have_count(3)
    assert options.evaluate_all("(ns) => ns.map((n) => n.getAttribute('aria-pressed'))") == ["false"] * 3
    validate = card.get_by_role("button", name="Valider ce choix")
    expect(validate).to_be_disabled()
    long = options.first
    style = long.evaluate("(n) => { const s = getComputedStyle(n); return [s.textAlign, s.whiteSpace]; }")
    assert style == ["left", "normal"], style
    # Le libellé long tient dans son bouton : aucune ligne ne sort de la boîte.
    assert long.evaluate("(n) => n.scrollHeight <= n.clientHeight + 1 && n.scrollWidth <= n.clientWidth + 1")
    # Au clavier : focus puis Espace choisit.
    options.nth(1).focus()
    workspace.keyboard.press("Space")
    expect(options.nth(1)).to_have_attribute("aria-pressed", "true")
    long.click()
    expect(long).to_have_attribute("aria-pressed", "true")
    expect(options.nth(1)).to_have_attribute("aria-pressed", "false")
    expect(validate).to_be_enabled()
    with workspace.expect_response(lambda r: "/answer" in r.url) as answered:
        validate.click()
    assert answered.value.ok
    assert json.loads(answered.value.request.post_data or "{}")["text"] == LONG_OPTION
    assert TaskService(real_project).require(choix).status is TaskState.READY

    # Reprise : titre sans « R14 — », phrase écrite par la vue, jargon serveur replié dans les détails.
    card = _card(workspace, reprise)
    expect(card.locator(".ex-decision-title")).to_have_text("Régénérer l'inventaire")
    visible = card.inner_text()
    assert "ne donne plus signe de vie" in visible
    for jargon in ("GRIMOIRE_HOST_PROBE", sid, "claude --resume"):
        assert jargon not in visible, jargon
    details = card.locator("[data-role='decision-details']")
    details.locator("summary").click()
    expect(details).to_contain_text("GRIMOIRE_HOST_PROBE=1")
    expect(details).to_contain_text("R14")
    expect(details).to_contain_text(sid)
    # « Copier la commande » : le presse-papiers dans le clic, et le statut le dit.
    workspace.context.grant_permissions(["clipboard-read", "clipboard-write"])
    details.get_by_role("button", name="Copier la commande").click()
    expect(details.locator(".action-status")).to_contain_text("Commande copiée")
    assert "--resume" in workspace.evaluate("() => navigator.clipboard.readText()")
    resume = card.get_by_role("button", name="Reprendre")
    expect(resume).to_have_class(re.compile(r"\bpri\b"))
    expect(card.get_by_role("button", name="Abandonner")).not_to_have_class(re.compile(r"\bpri\b"))
    with workspace.expect_response(lambda r: "/answer" in r.url) as answered:
        resume.click()
    assert answered.value.ok
    assert json.loads(answered.value.request.post_data or "{}")["text"] == "reprendre"
    assert TaskService(real_project).require(reprise).status is not TaskState.WAITING
    _answer_all(TaskService(real_project))


def test_les_compteurs_filtrent_par_type(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_all(service)
    geste = _running(service, "Geste à filtrer")
    service.ask(geste, "Poser la clé.", "agent-a", kind="permission")
    reprise = _resume(service, "Reprise à filtrer", "c0ffee00-1111-4361-8a73-660d0a08b3cc")
    _open_decisions(workspace)
    chip = workspace.locator("[data-role='decision-filter'][data-filter='reprise']")
    expect(chip).to_contain_text("1")
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "true")
    expect(_card(workspace, geste)).to_be_hidden()
    expect(_card(workspace, reprise)).to_be_visible()
    workspace.locator("[data-role='decision-filter'][data-filter='']").click()
    expect(_card(workspace, geste)).to_be_visible()
    _answer_all(service)


def test_tout_reprendre_confirme_dans_la_page_et_un_refus_n_arrete_pas_le_lot(workspace: Page, real_project: Path) -> None:
    service = TaskService(real_project)
    _answer_all(service)
    tids = [
        _resume(service, f"Reprise en lot {i}", f"{i:08d}-0000-4361-8a73-660d0a08b3cc")
        for i in (1, 2, 3)
    ]
    refused = tids[1]
    dialogs: list[str] = []
    workspace.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    def refuse(route: Route) -> None:
        route.fulfill(status=409, content_type="application/json",
                      body=json.dumps({"error": "question déjà tranchée"}))

    workspace.route(f"**/tasks/{refused}/answer*", refuse)
    _open_decisions(workspace)
    bulk = workspace.locator("[data-role='bulk-resume']")
    expect(bulk).to_have_text("Tout reprendre (3)")
    bulk.click()
    confirm = workspace.locator("[data-role='bulk-confirm']")
    expect(confirm).to_contain_text("Reprendre 3 tâches")
    # Annuler ne lance rien.
    confirm.get_by_role("button", name="Annuler").click()
    expect(confirm).to_have_count(0)
    bulk.click()
    workspace.locator("[data-role='bulk-confirm-go']").click()

    status = workspace.locator("[data-role='bulk-status']")
    expect(status).to_have_attribute("aria-live", "polite")
    expect(status).to_contain_text("2 tâches reprises sur 3 ; 1 refus", timeout=20_000)
    expect(status).to_contain_text("Reprise en lot 2 — Refusé : question déjà tranchée")
    assert dialogs == [], "aucun confirm() natif"
    fresh = TaskService(real_project)
    assert [fresh.require(t).status is TaskState.WAITING for t in tids] == [False, True, False]
    workspace.unroute(f"**/tasks/{refused}/answer*")
    _answer_all(fresh)


def test_la_frise_ne_chevauche_pas_sa_colonne_voisine(
    timeline_workspace: Page, served_timeline: tuple[str, str]
) -> None:
    page = timeline_workspace
    _, task_id = served_timeline
    page.evaluate("(id) => window.GrimoireWorkspace.goto('travail', { task: id, view: 'timeline' })", task_id)
    page.wait_for_selector(".ex-tl-entry")
    rows = page.locator(".ex-tl-row").evaluate_all("""(rows) => rows.map((r) => {
      const at = r.querySelector('.ex-tl-at'), body = r.querySelector('.ex-tl-body');
      return { at: at.getBoundingClientRect().right, body: body.getBoundingClientRect().left,
               text: at.textContent, iso: at.getAttribute('datetime'), tag: at.tagName,
               fits: at.scrollWidth <= at.clientWidth + 1 };
    })""")
    assert rows
    for row in rows:
        assert row["at"] <= row["body"], row
        assert row["fits"], row
        assert row["tag"] == "TIME" and row["iso"], row
        # « 26 oct. 07:47:43 » : jamais les microsecondes ni le fuseau.
        assert re.fullmatch(r"\d{1,2} \S+( \d{4})? \d{2}:\d{2}:\d{2}", row["text"]), row


def test_une_preuve_attendue_au_libelle_long_ne_deborde_pas_de_sa_pastille(workspace: Page, real_project: Path) -> None:
    tid = TaskService(real_project).add(
        "Note de cadrage à preuve longue", ("critère",), owner="amelia", actor="t", expected_evidence=(LONG_EVIDENCE,),
    ).task.id
    workspace.evaluate("(id) => window.GrimoireWorkspace.goto('travail', { task: id })", tid)
    block = workspace.locator("#inspector-body [data-role='evidence-block']")
    block.wait_for()
    chip = block.locator(".chip", has_text="rapport.md")
    box = chip.evaluate("""(n) => {
      const r = n.getBoundingClientRect(), p = n.parentElement.getBoundingClientRect();
      return { fits: n.scrollHeight <= n.clientHeight + 1, inside: r.right <= p.right + 1, h: r.height };
    }""")
    assert box["fits"] and box["inside"], box
    assert box["h"] > 24, "le libellé long passe à la ligne dans la pastille"
    # Une pastille d'une ligne garde ses 24 px (barre du haut et filtres inchangés).
    one_line = workspace.locator(".chip.pill").first
    if one_line.count():
        assert one_line.evaluate("(n) => n.getBoundingClientRect().height") == 24
