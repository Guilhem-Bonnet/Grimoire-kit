"""Atelier de spec, lot 3 (#1197, #1342) : des commentaires ancrés à un passage, entre l'humain et l'agent.

L'humain sélectionne un passage, clique « Commenter », répond, résout, rouvre ; l'agent commente et répond par la
fonction que l'outil MCP appelle, et ne résout rien. L'ancre survit à une modification du bloc voisin (critère de
l'épique), retrouve son extrait quand le bloc visé change, et devient « orphelin » (visible, avec l'extrait d'origine)
quand l'extrait disparaît. Drapeau éteint : 404.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ; sans la variable le test de capture ne
fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core import atelier_comments as store
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_comments, workspace_atelier_suggestions

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
BRAINSTORM = "02-brainstorm.md"
PARA_A = "Le bouton maintenu sert à parler tant que la touche est tenue."
PARA_B = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "tant que la touche est tenue"
EXIGENCES = "# Exigences\n\n- REQ-001 [Must] : maintenir un bouton pour dicter une idée\n  - la transcription apparaît en moins de 2 secondes\n"


def _folder(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug


def _chantier(root: Path, titre: str, *, exigences: bool = False) -> str:
    made = new_chantier(root, titre)
    path = _folder(root, made.slug) / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", f"{PARA_A}\n\n{PARA_B}"), encoding="utf-8")
    if exigences:
        (_folder(root, made.slug) / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str, text: str, phase: str = "brainstorm") -> str:
    found = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == phase)
    return str(next(b for b in found["blocks"] if text in b["text"])["id"])


def _agent(root: Path, slug: str, texte: str, text: str = PARA_A, **extra: str) -> dict[str, object]:
    """Commente comme le fait l'outil MCP : un appel de fonction, jamais un clic."""
    done = workspace_atelier_comments.agent_comment(str(root), slug, texte, _block_id(root, slug, text), **extra)
    return dict(done["comment"])


def _stored(root: Path, slug: str) -> list[store.Comment]:
    return store.load(_folder(root, slug))


def _rewrite(root: Path, slug: str, old: str, new: str) -> None:
    path = _folder(root, slug) / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


@pytest.fixture(scope="module")
def project_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier3-off", roots):
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier3-on", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _open(browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _select(page: Page, quote: str) -> None:
    """Sélectionne ``quote`` dans le bloc rendu, comme le ferait la souris (la sélection est celle du navigateur)."""
    done = page.evaluate(
        """(quote) => {
            const host = [...document.querySelectorAll('.at-blk')].find((h) => h.textContent.includes(quote));
            const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                const at = node.data.indexOf(quote);
                if (at < 0) continue;
                const range = document.createRange();
                range.setStart(node, at);
                range.setEnd(node, at + quote.length);
                const selection = getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                return true;
            }
            return false;
        }""",
        quote,
    )
    assert done, quote


def _refresh(page: Page) -> None:
    """Le retour sur l'onglet relit le document (un agent a pu commenter entre-temps)."""
    page.evaluate("window.dispatchEvent(new Event('focus'))")


def _thread(page: Page, comment_id: str) -> object:
    return page.locator(f"[data-com='{comment_id}']")


def test_flag_off_routes_are_404(browser: Browser, project_off: tuple[str, Path]) -> None:
    url, _root = project_off
    for page in _open(browser, url):
        script = """async (calls) => {
            const { api, ApiError } = await import('/workspace/api.js');
            const out = [];
            for (const run of calls) {
                try { await api[run[0]](...run.slice(1)); out.push(200); }
                catch (error) { out.push(error instanceof ApiError ? error.code : String(error)); }
            }
            return out;
        }"""
        calls = [["atelierCommenter", "x", "brainstorm.a", "q", 0, "texte"], ["atelierFil", "x", "com-1", "resolve"]]
        assert page.evaluate(script, calls) == [404, 404]
        raw = page.evaluate("async () => (await fetch('/api/workspace/atelier/chantiers/x/comments')).status")
        assert raw == 404


def test_the_human_comments_a_selected_passage_then_replies_resolves_and_reopens(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Commentaire humain")
    path = _folder(root, slug) / BRAINSTORM
    before = path.read_bytes()
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator(".at-blk", has_text=QUOTE).wait_for(timeout=APPEAR_MS)
        _select(page, QUOTE)
        page.locator(".at-blk", has_text=QUOTE).get_by_role("button", name="Commenter").click()
        form = page.locator("[data-role='comment-form']")
        assert form.locator("[data-role='quote']").inner_text() == QUOTE
        form.get_by_label("Ton commentaire").fill("Pourquoi tenue plutôt que maintenue ?")
        form.get_by_role("button", name="Commenter").click()
        page.locator("[data-role='atelier-status']", has_text="Commentaire ajouté").wait_for(timeout=APPEAR_MS)
        (made,) = _stored(root, slug)
        thread = _thread(page, made.id)
        thread.wait_for(timeout=APPEAR_MS)
        assert made.auteur == "humain" and made.quote == QUOTE
        # Aucun nom déclaré (« Developer » est le repli de `grimoire init`) : l'auteur humain s'affiche « Toi ».
        assert "Toi" in thread.inner_text() and "Pourquoi tenue" in thread.inner_text()
        assert thread.locator("[data-role='quote']").inner_text() == QUOTE
        assert thread.get_attribute("data-anchor") == "ancre"
        assert page.evaluate("CSS.highlights.has('at-cm')") is True  # le passage est surligné, sans toucher au DOM du bloc
        assert path.read_bytes() == before  # commenter n'écrit jamais dans la phase
        assert page.locator("[data-role='doc-comment-count']").inner_text().strip() == "1 commentaire ouvert."
        # Répondre, au clavier : le bouton, puis Ctrl+Entrée.
        thread.get_by_role("button", name="Répondre").click()
        page.get_by_label("Ta réponse").fill("Parce que tenue dit l'action, maintenue dit l'état.")
        page.get_by_label("Ta réponse").press("Control+Enter")
        page.locator("[data-role='atelier-status']", has_text="Réponse ajoutée").wait_for(timeout=APPEAR_MS)
        assert "l'action" in _thread(page, made.id).inner_text()
        # Résoudre replie le fil ; rouvrir le rend.
        _thread(page, made.id).get_by_role("button", name="Résoudre").click()
        page.locator("[data-role='atelier-status']", has_text="Fil résolu").wait_for(timeout=APPEAR_MS)
        assert _thread(page, made.id).get_attribute("data-etat") == "resolu"
        assert page.evaluate("CSS.highlights.has('at-cm')") is False  # un fil résolu ne surligne plus
        _thread(page, made.id).locator("summary").click()
        _thread(page, made.id).get_by_role("button", name="Rouvrir").click()
        page.locator("[data-role='atelier-status']", has_text="Fil rouvert").wait_for(timeout=APPEAR_MS)
        assert _thread(page, made.id).get_attribute("data-etat") == "ouvert"
    (saved,) = _stored(root, slug)
    assert saved.etat == "ouvert" and [r["auteur"] for r in saved.replies] == ["humain"]
    assert path.read_bytes() == before


def test_the_comment_button_is_reachable_from_the_keyboard(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Commentaire au clavier")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        block = page.locator(".at-blk", has_text=PARA_A)
        block.wait_for(timeout=APPEAR_MS)
        block.focus()
        page.keyboard.press("Tab")
        button = block.get_by_role("button", name="Commenter")
        assert button.evaluate("el => el === document.activeElement")  # Tab depuis le bloc : le bouton, sans souris
        assert button.evaluate("el => getComputedStyle(el).opacity") == "1"  # visible au focus
        assert button.evaluate("el => getComputedStyle(el).outlineStyle") != "none"  # focus visible
        page.keyboard.press("Enter")
        area = page.get_by_label("Ton commentaire")
        assert area.evaluate("el => el === document.activeElement")
        assert page.locator("[data-role='comment-form'] [data-role='quote']").inner_text() == PARA_A  # sans sélection : le bloc
        page.keyboard.press("Escape")
        assert page.locator("[data-role='comment-form']").count() == 0
        assert button.evaluate("el => el === document.activeElement")
    assert _stored(root, slug) == []


def test_the_anchor_survives_a_change_of_the_neighbouring_block(browser: Browser, project_on: tuple[str, Path]) -> None:
    """Critère de l'épique : le bloc voisin change, le commentaire reste attaché à son passage."""
    url, root = project_on
    slug = _chantier(root, "Ancre et voisin")
    made = _agent(root, slug, "Et si la touche est lâchée ?", quote=QUOTE)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _thread(page, str(made["id"])).wait_for(timeout=APPEAR_MS)
        _rewrite(root, slug, PARA_B, "Un second paragraphe, réécrit de fond en comble.")
        _refresh(page)
        page.locator(".at-blk", has_text="réécrit de fond en comble").wait_for(timeout=APPEAR_MS)
        thread = _thread(page, str(made["id"]))
        assert thread.get_attribute("data-anchor") == "ancre"
        # Toujours dans la marge du bloc d'origine, et le passage est toujours surligné.
        assert page.locator(".at-cmrow", has_text=PARA_A).locator(f"[data-com='{made['id']}']").count() == 1
        assert page.evaluate("CSS.highlights.has('at-cm')") is True


def test_a_changed_block_gives_its_quote_back(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Bloc visé modifié")
    made = _agent(root, slug, "Pourquoi tenue ?", quote=QUOTE)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _thread(page, str(made["id"])).wait_for(timeout=APPEAR_MS)
        _rewrite(root, slug, "Le bouton maintenu", "Le bouton maintenu enfoncé")  # le bloc visé change, l'extrait non
        _refresh(page)
        page.locator(".at-blk", has_text="enfoncé").wait_for(timeout=APPEAR_MS)
        thread = _thread(page, str(made["id"]))
        thread.wait_for(timeout=APPEAR_MS)
        assert thread.get_attribute("data-anchor") == "retrouve" and "Passage modifié depuis le commentaire" in thread.inner_text()
        assert page.locator(".at-cmrow", has_text="enfoncé").locator(f"[data-com='{made['id']}']").count() == 1


def test_a_deleted_quote_becomes_a_visible_orphan_with_its_original_quote(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Extrait supprimé")
    made = _agent(root, slug, "Pourquoi tenue ?", quote=QUOTE)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _thread(page, str(made["id"])).wait_for(timeout=APPEAR_MS)
        _rewrite(root, slug, QUOTE, "tant que le doigt reste posé")
        _refresh(page)
        orphans = page.locator("[data-role='orphans']")
        orphans.wait_for(timeout=APPEAR_MS)
        thread = orphans.locator(f"[data-com='{made['id']}']")
        assert thread.get_attribute("data-anchor") == "orphelin" and "Orphelin" in thread.inner_text()
        assert thread.locator("[data-role='quote']").inner_text() == QUOTE  # l'extrait d'origine reste dit
        assert page.locator(".at-cmrow", has_text="tant que le doigt").locator("[data-com]").count() == 0  # jamais rattaché ailleurs
        assert page.evaluate("CSS.highlights.has('at-cm')") is False
        assert page.locator("[data-role='doc-comment-count']").inner_text().strip() == "1 commentaire ouvert."


def test_the_agent_comments_replies_links_a_suggestion_and_cannot_resolve(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "L'agent commente")
    filed = workspace_atelier_suggestions.deposit(root, slug, {"block_id": _block_id(root, slug, PARA_B), "texte": f"{PARA_B} Plus court."})
    made = _agent(root, slug, "Voir ma suggestion.", PARA_B, suggestion=str(filed["suggestion"]["id"]))
    asked_by_human = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug, PARA_A), "texte": "Une question", "quote": QUOTE}, "humain")
    workspace_atelier_comments.agent_comment(str(root), slug, "Voici ma réponse.", reply_to=str(asked_by_human["comment"]["id"]))
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        agent = _thread(page, str(made["id"]))
        agent.wait_for(timeout=APPEAR_MS)
        assert agent.get_attribute("data-author") == "agent" and "Agent" in agent.inner_text()
        human = _thread(page, str(asked_by_human["comment"]["id"]))
        assert human.get_attribute("data-author") == "humain" and "Voici ma réponse." in human.inner_text()
        assert human.locator(".at-cm-who.is-agent").count() == 1 and human.locator(".at-cm-who.is-humain").count() == 1
        # L'interface relie le commentaire à la suggestion.
        agent.get_by_role("button", name="Voir la suggestion liée").click()
        assert page.evaluate("document.activeElement.getAttribute('data-sugg')") == filed["suggestion"]["id"]
        # L'humain résout ; l'agent ne peut ni répondre dans un fil résolu, ni le rouvrir : aucune fonction pour ça.
        human.get_by_role("button", name="Résoudre").click()
        page.locator("[data-role='atelier-status']", has_text="Fil résolu").wait_for(timeout=APPEAR_MS)
        with pytest.raises(workspace_atelier.ConflictError):
            workspace_atelier_comments.agent_comment(str(root), slug, "Je rouvre ?", reply_to=str(asked_by_human["comment"]["id"]))
    states = {c.id: c.etat for c in _stored(root, slug)}
    assert states == {str(made["id"]): "ouvert", str(asked_by_human["comment"]["id"]): "resolu"}


def test_an_open_thread_on_a_requirement_shows_on_its_status_line_and_in_the_library(
    browser: Browser, project_on: tuple[str, Path]
) -> None:
    url, root = project_on
    slug = _chantier(root, "Exigence commentée", exigences=True)
    block = _block_id(root, slug, "REQ-001", "exigences")
    made = workspace_atelier_comments.agent_comment(str(root), slug, "Deux secondes, mesurées où ?", block, "transcription apparaît")["comment"]
    for page in _open(browser, url, "#atelier"):
        row = page.locator(f"a.at-row[data-slug='{slug}']")
        row.wait_for(timeout=APPEAR_MS)
        assert row.locator("[data-role='comment-count']").inner_text() == "1 commentaire"
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        line = page.locator(".at-live[data-live='REQ-001']:not([hidden])")
        line.wait_for(timeout=APPEAR_MS)
        assert line.locator("[data-role='live-comments']").inner_text() == "1 commentaire ouvert"
        _thread(page, str(made["id"])).get_by_role("button", name="Résoudre").click()
        page.locator("[data-role='atelier-status']", has_text="Fil résolu").wait_for(timeout=APPEAR_MS)
        page.locator(".at-live[data-live='REQ-001']:not([hidden])").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='live-comments']").count() == 0


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_captures(browser: Browser, project_on: tuple[str, Path], size: tuple[int, int]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = "dicter-une-idee-depuis-le-cockpit"
    if not (_folder(root, slug) / BRAINSTORM).is_file():
        slug = _chantier(root, "Dicter une idée depuis le cockpit", exigences=True)
        filed = workspace_atelier_suggestions.deposit(
            root, slug, {"block_id": _block_id(root, slug, PARA_B), "texte": f"{PARA_B} Plus court.", "motif": "Dire l'essentiel."}
        )
        human = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug, PARA_A), "texte": "Que se passe-t-il quand la touche est lâchée en pleine phrase ?", "quote": QUOTE}, "humain")
        workspace_atelier_comments.agent_comment(str(root), slug, "La transcription s'arrête et ce qui est dit est gardé ; je propose de le préciser.", reply_to=str(human["comment"]["id"]))
        workspace_atelier_comments.agent_comment(str(root), slug, "Ma reformulation va avec ce commentaire.", _block_id(root, slug, PARA_B), "second paragraphe", suggestion=str(filed["suggestion"]["id"]))
        workspace_atelier_comments.agent_comment(str(root), slug, "Deux secondes, mesurées où ?", _block_id(root, slug, "REQ-001", "exigences"), "transcription apparaît")
        lost = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug, PARA_B), "texte": "Le repli est-il testé ?", "quote": "repli du navigateur"}, "humain")
        _rewrite(root, slug, "repli du navigateur", "plan B")
        assert lost["ok"]
        _chantier(root, "Chantiers multiples du cadrage")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, "#atelier", size):
        page.locator("[data-role='comment-count']").first.wait_for(timeout=APPEAR_MS)
        page.screenshot(path=str(folder / f"atelier-rangement-commentaires-{size[0]}x{size[1]}.png"))
    for page in _open(browser, url, f"#atelier?chantier={slug}", size):
        page.locator(".at-cm").first.wait_for(timeout=APPEAR_MS)
        page.locator(".at-cmrow", has_text="touche est tenue").evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-commentaire-{size[0]}x{size[1]}.png"))
        page.locator("[data-role='orphans']").evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-orphelin-{size[0]}x{size[1]}.png"))
        page.locator(".at-live[data-live='REQ-001']:not([hidden])").wait_for(timeout=APPEAR_MS)
        page.locator(".at-live[data-live='REQ-001']").evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(folder / f"atelier-exigence-commentee-{size[0]}x{size[1]}.png"))
