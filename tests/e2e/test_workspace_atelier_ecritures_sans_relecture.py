"""Atelier de spec : aucune écriture de la page n'est prise pour un dépôt extérieur par le pouls (suite de #1487).

Course déterministe, la même pour chaque écriture : un pouls (``…/entretien``) part AVANT le geste ; le geste écrit côté serveur ;
la réponse du geste est retenue (la page n'en sait encore rien) ; le pouls répond alors avec l'empreinte du fichier ou du
magasin neuf. Ce n'est que l'écriture de la page elle-même : aucune relecture du document ni aucune annonce « nouveau
commentaire » / « suggestion de l'assistant » n'est due. Puis le pouls suivant part (la page a fini de traiter le précédent),
ce qui borne l'observation sans attente fixe.

Les écritures qui ne modifient ni un fichier de phase ni un magasin comparé par le pouls (création d'un chantier, tâches du
découpage, gestes de l'agent) ne sont pas concernées et ne sont pas exercées ici.
"""

# Les fixtures de projet viennent des modules voisins (importées pour être réutilisées, F811) ; chaque geste est défini et joué dans le
# tour de boucle de sa page (B023 sans objet : `for page in …` ne fait qu'un tour).
# ruff: noqa: F811, B023
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, Request, Route, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_comments

from .test_workspace_atelier_comparer_restaurer import (  # noqa: F401
    BRIEF_1,
    Project,
    _compare_with_current,
    _doc,
    _open_panel,
    _phase,
    project_git,
)
from .test_workspace_atelier_statut_lisible import (  # noqa: F401
    BRAINSTORM,
    OUTSIDE,
    PARA,
    STATUS,
    TOASTS,
    WAIT_MS,
    _block_id,
    _chantier,
    _page,
    project,
    sans_agent,
)


def _fresh(browser: Browser, url: str, slug: str) -> Iterator[Page]:
    """Un chantier neuf (rubriques vides, donc des invites et pas de texte) ouvert dans une page."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=WAIT_MS)
        page.locator(".at-sheet").wait_for(timeout=WAIT_MS)
        yield page
    finally:
        context.close()


def _race(page: Page, slug: str, write_glob: str, gesture: Callable[[], None]) -> Callable[[], None]:
    """Joue la course ; rend la fonction qui libère la réponse de l'écriture (la page finit alors son geste)."""
    parked: list[Route] = []
    written: list[tuple[Route, object]] = []
    docs: list[str] = []
    page.on("response", lambda r: docs.append(r.url) if r.request.method == "GET" and r.url.split("?")[0].endswith(f"/chantiers/{slug}") else None)

    def hold_pulse(route: Route) -> None:
        parked.append(route)

    def hold_write(route: Route) -> None:
        written.append((route, route.fetch()))
        page.evaluate("document.body.dataset.writeHeld = '1'")  # le serveur a écrit : l'état voulu est observable

    def is_pulse(request: Request) -> bool:
        return request.url.split("?")[0].endswith(f"/chantiers/{slug}/entretien")

    pulse_glob = f"**/chantiers/{slug}/entretien*"
    page.route(pulse_glob, hold_pulse)
    page.route(write_glob, hold_write)
    with page.expect_request(is_pulse, timeout=WAIT_MS):  # un pouls part : le geste suit, il est donc antérieur à l'écriture
        pass
    expect(page.locator("body")).to_be_visible()  # un aller-retour : la route a retenu le pouls
    assert len(parked) == 1, "le pouls n'a pas été retenu"
    before = len(docs)
    gesture()
    expect(page.locator("body")).to_have_attribute("data-write-held", "1", timeout=WAIT_MS)
    assert len(written) == 1, "l'écriture n'a pas atteint le serveur"
    page.unroute(pulse_glob, hold_pulse)  # les pouls suivants passent
    with page.expect_request(is_pulse, timeout=WAIT_MS):  # le pouls suivant ne part qu'une fois celui-ci traité
        parked[0].fulfill(response=parked[0].fetch())
    assert len(docs) == before, f"un pouls répondu pendant l'écriture de la page a relu le document : {len(docs) - before} lecture(s)"
    toasts = page.locator(TOASTS).inner_text()
    assert not any(phrase in toasts for phrase in OUTSIDE), f"son propre geste annoncé comme dépôt extérieur : {toasts!r}"
    route, response = written[0]

    def release() -> None:
        route.fulfill(response=response)  # type: ignore[arg-type]

    return release


def test_a_pulse_answered_before_the_reads_that_follow_an_own_block_write_does_not_reload(browser: Browser, project: tuple[str, Path]) -> None:
    """Après l'écriture d'un bloc commenté, la page relit le document (avancement, fils) avant d'adopter l'état des magasins : un pouls
    parti après l'écriture et répondu avant ces lectures n'y voit pas un dépôt extérieur. Les lectures sont retenues, deux pouls passent."""
    url, root = project
    slug = _chantier(root, "Course Lectures")
    workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug), "texte": "Une question", "quote": "Une piste"}, "humain")
    for page, _pulses in _page(browser, url, slug):
        page.locator("[data-com]").first.wait_for(timeout=WAIT_MS)
        reads: list[Route] = []
        requested: list[str] = []
        page.on("request", lambda r: requested.append(r.url) if r.method == "GET" and r.url.split("?")[0].endswith(f"/chantiers/{slug}") else None)

        def hold_read(route: Route) -> None:
            reads.append(route)

        def is_pulse(request: Request) -> bool:
            return request.url.split("?")[0].endswith(f"/chantiers/{slug}/entretien")

        page.locator(".at-blk", has_text="Une piste").click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill(f"{PARA} Réécrite.")
        page.route(f"**/chantiers/{slug}?*", hold_read)  # les lectures qui suivent l'écriture : retenues
        with page.expect_request(lambda r: r.method == "GET" and r.url.split("?")[0].endswith(f"/chantiers/{slug}"), timeout=WAIT_MS):
            area.press("Control+Enter")
        expect(page.locator(STATUS)).to_have_text("Bloc enregistré.", timeout=WAIT_MS)
        expect(page.locator("body")).to_be_visible()  # un aller-retour : la route a retenu la lecture
        assert reads, "aucune lecture après l'écriture"
        held = len(requested)
        for _ in range(2):  # un pouls part après l'écriture ; le suivant ne part qu'une fois celui-ci traité (lectures du geste toujours en attente)
            try:
                with page.expect_request(is_pulse, timeout=WAIT_MS):
                    pass
            except PlaywrightTimeout:  # le pouls attend le rechargement qu'il a lancé : retenu lui aussi
                break
        assert len(requested) == held, f"un pouls répondu avant les lectures de l'écriture a relu le document : {len(requested) - held} lecture(s)"
        page.unroute(f"**/chantiers/{slug}?*", hold_read)  # les lectures retenues repartent
        expect(page.locator(STATUS)).to_have_text("Bloc enregistré.")


def test_a_read_that_returns_after_a_newer_one_does_not_bring_back_the_old_document(browser: Browser, project: tuple[str, Path]) -> None:
    """Deux lectures du document qui se croisent : celle d'avant un dépôt extérieur, partie la première et arrivée la dernière, ne
    remplace pas le document que la lecture d'après a déjà dessiné (sinon la page montre l'ancien texte et l'ancien fil, et le
    prochain geste vise un bloc qui n'existe plus : « Rattacher ici » finissait sans confirmation)."""
    url, root = project
    slug = _chantier(root, "Course Lectures Croisees")
    path = root / "_grimoire" / "cadrage" / slug / BRAINSTORM
    first, second = "Un premier paragraphe sur la touche maintenue.", "Un second paragraphe indépendant."
    path.write_text(path.read_text(encoding="utf-8").replace(PARA, f"{first}\n\n{second}"), encoding="utf-8")
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    block = str(next(b for b in phase["blocks"] if b["text"] == first)["id"])
    made = workspace_atelier_comments.create(root, slug, {"block_id": block, "texte": "Pourquoi ?", "quote": "touche maintenue"}, "humain")
    cid = str(made["comment"]["id"])
    for page, _pulses in _page(browser, url, slug):
        thread = page.locator(f"[data-com='{cid}']")
        thread.wait_for(timeout=WAIT_MS)
        held: list[tuple[Route, object]] = []

        def hold_first_read(route: Route) -> None:
            if held:
                route.continue_()
                return
            held.append((route, route.fetch()))  # la lecture d'avant le dépôt : sa réponse est retenue
            page.evaluate("document.body.dataset.readHeld = '1'")

        page.route(f"**/chantiers/{slug}?*", hold_first_read)
        thread.locator("[data-act='reply']").click()
        area = thread.locator(".at-cm-area")
        area.fill("Une réponse")
        area.press("Control+Enter")  # l'écriture passe ; la lecture qui la suit est retenue
        expect(page.locator("body")).to_have_attribute("data-read-held", "1", timeout=WAIT_MS)
        path.write_text(path.read_text(encoding="utf-8").replace(first, "Un tout autre texte.").replace(second, f"{second} (revu)"), encoding="utf-8")
        page.evaluate("window.dispatchEvent(new Event('focus'))")  # la lecture d'après le dépôt : elle passe et dessine le nouveau document
        expect(thread).to_have_attribute("data-anchor", "orphelin", timeout=WAIT_MS)
        route, response = held[0]
        route.fulfill(response=response)  # type: ignore[arg-type]
        expect(page.locator(STATUS)).to_have_text("Réponse ajoutée.", timeout=WAIT_MS)  # le geste a fini d'être dit : la lecture tardive est traitée
        assert thread.get_attribute("data-anchor") == "orphelin", "la lecture tardive a ramené l'ancien fil"
        assert page.locator(".at-blk", has_text="(revu)").count() == 1, "la lecture tardive a ramené l'ancien texte"


def test_a_comment_created_while_a_pulse_answers_is_not_an_outside_comment(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Course Commentaire")
    for page, _pulses in _page(browser, url, slug):
        block = page.locator(".at-blk", has_text="Une piste")

        def gesture() -> None:
            block.hover()
            block.locator("[data-act='comment']").click()
            area = page.locator(".at-cm-new .at-cm-area")
            area.fill("Une question")
            area.press("Control+Enter")

        release = _race(page, slug, f"**/chantiers/{slug}/comments*", gesture)
        release()
        expect(page.locator(STATUS)).to_have_text("Commentaire ajouté.", timeout=WAIT_MS)


@pytest.mark.parametrize(("act", "status"), [("reply", "Réponse ajoutée."), ("resolve", "Fil résolu.")])
def test_a_thread_gesture_while_a_pulse_answers_does_not_reload(browser: Browser, project: tuple[str, Path], act: str, status: str) -> None:
    url, root = project
    slug = _chantier(root, f"Course Fil {act}")
    made = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug), "texte": "Une question", "quote": "Une piste"}, "humain")
    cid = str(made["comment"]["id"])
    for page, _pulses in _page(browser, url, slug):
        thread = page.locator(f"[data-com='{cid}']")
        thread.wait_for(timeout=WAIT_MS)

        def gesture() -> None:
            thread.locator(f"[data-act='{act}']").click()
            if act == "reply":
                area = thread.locator(".at-cm-area")
                area.fill("Une réponse")
                area.press("Control+Enter")

        release = _race(page, slug, f"**/chantiers/{slug}/comments/*", gesture)
        release()
        expect(page.locator(STATUS)).to_contain_text(status.rstrip("."), timeout=WAIT_MS)


def test_attaching_an_orphan_while_a_pulse_answers_does_not_reload(browser: Browser, project: tuple[str, Path]) -> None:
    url, root = project
    slug = _chantier(root, "Course Rattachement")
    path = root / "_grimoire" / "cadrage" / slug / BRAINSTORM
    first, second = "Un premier paragraphe sur la touche maintenue.", "Un second paragraphe indépendant."
    path.write_text(path.read_text(encoding="utf-8").replace(PARA, f"{first}\n\n{second}"), encoding="utf-8")
    phase = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == "brainstorm")
    block = str(next(b for b in phase["blocks"] if b["text"] == first)["id"])
    made = workspace_atelier_comments.create(root, slug, {"block_id": block, "texte": "Pourquoi ?", "quote": "touche maintenue"}, "humain")
    cid = str(made["comment"]["id"])
    path.write_text(path.read_text(encoding="utf-8").replace(first, "Un tout autre texte.").replace(second, f"{second} (revu)"), encoding="utf-8")
    for page, _pulses in _page(browser, url, slug):
        thread = page.locator(f"[data-com='{cid}']")
        thread.wait_for(timeout=WAIT_MS)
        expect(thread).to_have_attribute("data-anchor", "orphelin", timeout=WAIT_MS)

        def gesture() -> None:
            thread.locator("[data-act='attach']").click()
            expect(page.locator(".at-sheet.is-picking")).to_have_count(1)
            page.locator(".at-blk", has_text="(revu)").focus()
            page.keyboard.press("Enter")

        release = _race(page, slug, f"**/chantiers/{slug}/comments/*", gesture)
        release()
        expect(page.locator(STATUS)).to_have_text("Commentaire rattaché au bloc.", timeout=WAIT_MS)
        expect(page.locator(TOASTS)).to_contain_text("rattaché au bloc", timeout=WAIT_MS)


def test_marking_a_section_not_applicable_while_a_pulse_answers_does_not_reload(browser: Browser, sans_agent: tuple[str, Path, str]) -> None:
    url, root, _slug = sans_agent
    slug = new_chantier(root, "Course Sans objet").slug
    for page in _fresh(browser, url, slug):
        invite = page.locator("[data-invite='Pourquoi maintenant']")
        invite.wait_for(timeout=WAIT_MS)

        def gesture() -> None:
            invite.locator("[data-act='na-open']").click()
            page.locator("[data-role='na-reason']").fill("Aucune date imposée")
            page.locator("[data-act='na-confirm']").click()

        release = _race(page, slug, f"**/chantiers/{slug}/blocs/*", gesture)
        release()
        expect(page.locator(STATUS)).to_contain_text("sans objet", timeout=WAIT_MS)


def test_a_guided_answer_while_a_pulse_answers_does_not_reload(browser: Browser, sans_agent: tuple[str, Path, str]) -> None:
    url, root, _slug = sans_agent
    slug = new_chantier(root, "Course Guidé").slug
    for page in _fresh(browser, url, slug):
        page.locator("[data-act='start']").click()
        page.locator("[data-role='guided-question']").wait_for(timeout=WAIT_MS)

        def gesture() -> None:
            page.locator(".at-asst-area").fill("Les mainteneurs du kit.")
            page.locator(".at-asst-area").press("Enter")

        release = _race(page, slug, f"**/chantiers/{slug}/assistant*", gesture)
        release()
        page.locator(".at-sheet", has_text="Les mainteneurs du kit.").wait_for(timeout=WAIT_MS)


def test_restoring_a_phase_while_a_pulse_answers_does_not_reload(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _compare_with_current(page, "feat: premier jet")
        _phase(page, "brief").locator("[data-role='hist-restaurer-phase']").click()
        page.locator("[data-role='hist-confirmation']").wait_for(state="visible", timeout=WAIT_MS)

        def gesture() -> None:
            page.locator("[data-role='hist-confirmer']").click()

        release = _race(page, project_git.slug, f"**/chantiers/{project_git.slug}/restore*", gesture)
        release()
        page.locator("[data-role='hist-accuse']").filter(has_text="La phase Brief restaurée").wait_for(timeout=WAIT_MS)
        assert project_git.text() == BRIEF_1


def test_restoring_a_block_while_a_pulse_answers_does_not_reload(browser: Browser, project_git: Project) -> None:
    project_git.reset()
    for page in _doc(browser, project_git.url, project_git.slug):
        _open_panel(page)
        _compare_with_current(page, "feat: premier jet")
        _phase(page, "brief").locator("[data-role='hist-restaurer-bloc']").first.click()
        page.locator("[data-role='hist-confirmation']").wait_for(state="visible", timeout=WAIT_MS)

        def gesture() -> None:
            page.locator("[data-role='hist-confirmer']").click()

        release = _race(page, project_git.slug, f"**/chantiers/{project_git.slug}/blocs/*", gesture)
        release()
        page.locator("[data-role='hist-accuse']").filter(has_text="Le bloc restauré").wait_for(timeout=WAIT_MS)
        assert project_git.text() == BRIEF_1
