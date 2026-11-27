"""Atelier de spec, mise en page (#1197) : l'assistant en colonne, les retours là où l'on agit, la barre des phases.

Chaque preuve est lue à l'écran, dans un vrai navigateur : l'assistant ne recouvre plus rien (colonne à droite sur toile large,
tiroir replié sous 1100 px, choix mémorisé par chantier), les retours d'action vivent dans une file ancrée au bas de la fenêtre
visible, la barre des phases est collante et suit le défilement (ancre d'URL, retour arrière), le bouton principal grisé dit
pourquoi, la barre d'outils d'un bloc ne couvre plus le texte, un commentaire orphelin se signale et se rattache par un mode
« choisis le bloc ».

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures (``GRIMOIRE_E2E_CAPTURE_PREFIX`` : ``avant`` / ``apres``, 1440x900 et
1024x768, clair et sombre) ; sans la variable le test de capture ne fait rien (il ne se skippe pas : le mode strict de la CI
promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.tools import workspace_atelier, workspace_atelier_comments, workspace_atelier_suggestions

from .conftest import _serve_empty_project

APPEAR_MS = 30_000
BRAINSTORM = "02-brainstorm.md"
PARA_A = "Le bouton maintenu sert à parler tant que la touche est tenue."
PARA_B = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "tant que la touche est tenue"
NEW_A = "Le bouton maintenu sert à parler jusqu'au relâchement."
EXIGENCES = (
    "# Exigences\n\n- REQ-001 [Must] : maintenir un bouton pour dicter une idée\n  - la transcription apparaît en moins de 2 secondes\n"
    "- REQ-002 [Should] : replier sur le navigateur\n  - le texte reste dicté sans le moteur du kit\n"
)
FILLER = [
    f"Paragraphe {i} du brainstorm : une piste de plus à garder sous les yeux, assez longue pour occuper une ligne ou deux "
    "de la feuille et rendre le document aussi long qu'un vrai chantier en cours de cadrage." for i in range(1, 13)
]
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)
CANVAS = "document.getElementById('canvas')"


@pytest.fixture(autouse=True)
def _human_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """La MOA au terminal, simulée pour *préparer* l'état (une exigence validée) : l'écran n'a aucun moyen de valider."""
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


def _moa(root: Path) -> None:
    from ruamel.yaml import YAML

    yaml = YAML()
    path = root / "project-context.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"))
    governance = data.get("governance") or {}
    governance["roles"] = {**(governance.get("roles") or {}), "moa": "Ada"}
    data["governance"] = governance
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-mise-en-page", roots):
        _moa(roots[0])
        workspace_atelier.set_atelier(roots[0], True)
        workspace_atelier.set_mascotte(roots[0], True)
        yield url, roots[0]


@pytest.fixture(scope="module")
def project_kanban(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """Un cockpit sans agent d'hôte joignable (repli kanban) : l'assistant dit qu'il ne l'est pas, le challenge refuse d'avance."""
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    from .test_workspace_atelier_assistant import _init, _serve

    root, _slug = _init(tmp_path_factory, "atelier-mise-en-page-kanban", flag=True)
    for url in _serve(root, tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""}):
        yield url, root


@pytest.fixture(scope="module")
def project_empty(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-mise-en-page-vide", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _folder(root: Path, slug: str) -> Path:
    return root / "_grimoire" / "cadrage" / slug


def _chantier(root: Path, titre: str, *, long: bool = False) -> str:
    made = new_chantier(root, titre)
    path = _folder(root, made.slug) / BRAINSTORM
    body = f"{PARA_A}\n\n{PARA_B}" + ("\n\n" + "\n\n".join(FILLER) if long else "")
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", body), encoding="utf-8")
    (_folder(root, made.slug) / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return made.slug


def _block_id(root: Path, slug: str, text: str, phase: str = "brainstorm") -> str:
    found = next(p for p in workspace_atelier.document(root, slug)["phases"] if p["id"] == phase)
    return str(next(b for b in found["blocks"] if text in b["text"])["id"])


def _suggest(root: Path, slug: str, texte: str) -> None:
    body = {"block_id": _block_id(root, slug, PARA_B), "texte": texte, "motif": "plus court"}
    workspace_atelier_suggestions.deposit(root, slug, body)


def _comment(root: Path, slug: str, texte: str, quote: str = QUOTE, para: str = PARA_A) -> str:
    made = workspace_atelier_comments.create(root, slug, {"block_id": _block_id(root, slug, para), "texte": texte, "quote": quote}, "humain")
    return str(made["comment"]["id"])


def _open(
    browser: Browser, url: str, hash_: str = "", viewport: tuple[int, int] = (1440, 900), *, scheme: str = "light", block_storage: bool = False
) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    if block_storage:
        context.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('bloqué'); } });")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _box(page: Page, selector: str) -> dict[str, float]:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, selector
    return box


def _uncovered(page: Page, selector: str) -> bool:
    """Vrai si le centre du contrôle reçoit bien le pointeur, c'est-à-dire qu'aucun panneau ne le couvre."""
    return bool(page.evaluate(
        """(sel) => {
            const node = document.querySelector(sel);
            const b = node.getBoundingClientRect();
            const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
            return Boolean(hit) && node.contains(hit);
        }""",
        selector,
    ))


# ── 1. L'assistant : colonne latérale repliable, tiroir sous 1100 px ─────────

def test_the_assistant_is_a_side_column_that_folds_into_a_pill(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Colonne laterale", long=True)
    _suggest(root, slug, "Version plus courte.")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        asst = page.locator("[data-role='assistant']")
        asst.wait_for(timeout=APPEAR_MS)
        sheet, side = _box(page, ".at-sheet"), _box(page, "[data-role='assistant']")
        assert side["x"] >= sheet["x"] + sheet["width"] - 1, "la colonne est à droite du document, pas dessus"
        assert side["width"] <= 340
        # La mascotte y est une vignette de 40 px au plus ; le message du vocal indisponible tient en une ligne.
        thumb = _box(page, ".at-mascotte-btn")
        assert thumb["width"] <= 40 and thumb["height"] <= 40
        why = page.locator(".at-why")
        if why.count():
            note = page.locator(".conv-note")
            assert not note.is_visible()
            why.click()
            assert note.is_visible() and "Vocal indisponible" in note.inner_text()
            why.click()
            assert not note.is_visible()
        # Replier : il ne reste que la pastille, qui dit ce qui attend ; au clavier comme à la souris.
        asst.locator("[data-act='fold']").focus()
        page.keyboard.press("Enter")
        pill = page.locator("[data-role='assistant-pill']")
        pill.wait_for(timeout=APPEAR_MS)
        assert asst.is_hidden() and "1 suggestion" in pill.inner_text()
        assert page.evaluate("document.activeElement === document.querySelector('[data-role=\"assistant-pill\"]')")
        assert _box(page, ".at-sheet")["width"] > sheet["width"] - 1  # le document garde sa largeur
        # Mémorisé par chantier : le rechargement garde le choix, un autre chantier garde le sien.
        page.reload()
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator("[data-role='assistant-pill']").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='assistant']").is_hidden()
        page.locator("[data-role='assistant-pill']").click()
        assert page.locator("[data-role='assistant']").is_visible()
    other = _chantier(root, "Autre chantier")
    for page in _open(browser, url, f"#atelier?chantier={other}"):
        page.locator("[data-role='assistant']").wait_for(timeout=APPEAR_MS)  # déplié : rien de mémorisé pour lui


def test_without_local_storage_the_panel_still_works(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Sans stockage")
    for page in _open(browser, url, f"#atelier?chantier={slug}", block_storage=True):
        asst = page.locator("[data-role='assistant']")
        asst.wait_for(timeout=APPEAR_MS)
        asst.locator("[data-act='fold']").click()
        page.locator("[data-role='assistant-pill']").wait_for(timeout=APPEAR_MS)
        assert asst.is_hidden()


def test_below_1100_the_assistant_is_a_folded_drawer_that_covers_no_control(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Tiroir", long=True)
    _suggest(root, slug, "Version plus courte.")
    for page in _open(browser, url, f"#atelier?chantier={slug}", (1024, 768)):
        pill = page.locator("[data-role='assistant-pill']")
        pill.wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-role='assistant']").is_hidden()  # replié par défaut : le document a toute la place
        # La pastille est dans la barre des phases : elle ne recouvre rien.
        bar, chip = _box(page, ".at-phasebar"), _box(page, "[data-role='assistant-pill']")
        assert chip["y"] >= bar["y"] and chip["y"] + chip["height"] <= bar["y"] + bar["height"] + 1
        pill.click()
        drawer = _box(page, "[data-role='assistant']")
        assert drawer["y"] + drawer["height"] <= 768 and drawer["height"] <= 768 * 0.5
        # Ouvert, le tiroir réserve sa place : au clavier, le bouton « Commenter » du dernier paragraphe n'est jamais dessous.
        block = page.locator(".at-blk", has_text="Paragraphe 12 du brainstorm")
        block.scroll_into_view_if_needed()
        block.hover()
        block.locator("[data-act='comment']").click()
        area = page.locator(".at-cm-new .at-cm-area")
        area.wait_for(timeout=APPEAR_MS)
        send = page.locator("[data-act='comment-send']")
        send.focus()
        assert _uncovered(page, "[data-act='comment-send']"), "le tiroir ne recouvre pas « Commenter »"
        assert _uncovered(page, ".at-cm-new .at-cm-area")
        page.evaluate(f"{CANVAS}.scrollTop = {CANVAS}.scrollHeight")
        last = page.locator(".at-blk").last
        assert last.bounding_box()["y"] + last.bounding_box()["height"] <= drawer["y"] + 2, "la fin du document se lit au-dessus du tiroir"


# ── 2. Retours d'action : une file ancrée au bas de la fenêtre visible ───────

def test_feedback_is_a_queue_anchored_to_the_visible_bottom(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Notifications", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator(".at-blk", has_text=PARA_B).wait_for(timeout=APPEAR_MS)
        block = page.locator(".at-blk", has_text="Paragraphe 12 du brainstorm")
        block.scroll_into_view_if_needed()
        block.click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill(area.input_value() + " Précision ajoutée.")
        page.evaluate("window.__active = document.activeElement")
        area.press("Control+Enter")
        toast = page.locator("[data-role='atelier-toasts'] [role='status']", has_text="Bloc enregistré")
        toast.wait_for(timeout=APPEAR_MS)
        box = toast.bounding_box()
        assert box is not None and box["y"] + box["height"] <= 900 and box["y"] > 450, "ancrée au bas de la fenêtre visible"
        assert page.evaluate("!document.activeElement.closest('[data-role=\"atelier-toasts\"]')"), "le focus n'est pas volé"
        # Elle suit la fenêtre, pas le document : en haut de page, elle est toujours là, au même endroit.
        page.evaluate(f"{CANVAS}.scrollTop = 0")
        again = toast.bounding_box()
        assert again is not None and abs(again["y"] - box["y"]) < 2
        toast.wait_for(state="detached", timeout=15_000)  # un succès disparaît seul


def test_an_error_stays_until_closed_and_is_repeated_at_the_foot_of_its_block(browser: Browser, project_kanban: tuple[str, Path]) -> None:
    url, root = project_kanban
    slug = _chantier(root, "Erreur de challenge")
    _suggest(root, slug, "Version plus courte.")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator("[data-role='assistant-off']").wait_for(timeout=APPEAR_MS)  # sans agent d'hôte : le geste refuse d'avance
        block = page.locator(".at-blk", has_text=PARA_B)
        block.hover()
        block.locator("[data-act='challenge']").click()
        block.locator("[data-posture='sceptique']").click()
        alert = page.locator("[data-role='atelier-toasts'] [role='alert']", has_text="Challenge impossible")
        alert.wait_for(timeout=APPEAR_MS)
        foot = block.locator("[data-role='block-error']")
        foot.wait_for(timeout=APPEAR_MS)
        assert "Challenge impossible" in foot.inner_text() and foot.get_attribute("role") == "alert"
        page.wait_for_timeout(7_500)  # au-delà de la durée d'un succès
        assert alert.is_visible()
        alert.get_by_role("button", name="Fermer la notification").click()
        alert.wait_for(state="detached", timeout=APPEAR_MS)


# ── 3. Barre des phases collante, suivie au défilement, ancrée dans l'URL ────

def test_the_phase_bar_is_sticky_follows_the_scroll_and_keeps_its_place_in_the_url(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Barre des phases", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator(".at-blk", has_text=PARA_B).wait_for(timeout=APPEAR_MS)
        states = {b.get_attribute("data-phase"): b.get_attribute("data-state") for b in page.locator(".at-phase").all()}
        assert states["brief"] == "vide" and states["brainstorm"] in ("en-cours", "complete") and states["cahier-des-charges"] == "vide"
        assert "en cours" in page.locator(".at-phase[data-phase='brainstorm']").inner_text()  # l'état se lit aussi sans la forme
        # Collante : en bas du document, elle reste calée en haut de la toile.
        page.evaluate(f"{CANVAS}.scrollTop = {CANVAS}.scrollHeight")
        bar, canvas = _box(page, ".at-phasebar"), _box(page, "#canvas")
        assert abs(bar["y"] - canvas["y"]) <= 2
        page.locator(".at-phase[data-phase='cahier-des-charges'][aria-current='step']").wait_for(timeout=APPEAR_MS)
        # La phase lue suit le défilement, et l'URL aussi.
        page.evaluate(
            "() => { const h = document.querySelector('[data-section=\"exigences\"] h2'); h.scrollIntoView({ block: 'start' }); }"
        )
        page.locator(".at-phase[data-phase='exigences'][aria-current='step']").wait_for(timeout=APPEAR_MS)
        page.wait_for_function("() => location.hash.includes('phase=exigences')", timeout=APPEAR_MS)
        # Un clic sur une phase crée une entrée d'historique : « Précédent » revient à la phase d'avant.
        page.locator(".at-phase[data-phase='brainstorm']").click()
        page.wait_for_function("() => location.hash.includes('phase=brainstorm')", timeout=APPEAR_MS)
        page.go_back()
        page.locator(".at-phase[data-phase='exigences'][aria-current='step']").wait_for(timeout=APPEAR_MS)
        page.wait_for_function("() => location.hash.includes('phase=exigences')", timeout=APPEAR_MS)
        # Le rechargement revient à la même phase, son titre sous la barre.
        page.reload()
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-phase[data-phase='exigences'][aria-current='step']").wait_for(timeout=APPEAR_MS)
        heading = _box(page, "[data-section='exigences'] h2")
        assert heading["y"] < 260 and heading["y"] > _box(page, ".at-phasebar")["y"]


# ── 4. Le bouton principal désactivé le paraît, et dit pourquoi ──────────────

def test_the_disabled_primary_button_looks_disabled_and_says_why(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Bouton grise")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        split = page.locator("[data-act='split']")
        page.wait_for_function("() => document.querySelector('[data-role=\"split-why\"]').textContent.includes('REQ-001')", timeout=APPEAR_MS)
        assert split.get_attribute("aria-disabled") == "true" and split.get_attribute("aria-describedby") == "at-split-why"
        assert float(split.evaluate("(n) => getComputedStyle(n).opacity")) < 0.8
        assert split.evaluate("(n) => getComputedStyle(n).cursor") == "not-allowed"
        why = page.locator("#at-split-why")
        assert "Valide d’abord les exigences Must : REQ-001" in (why.text_content() or "") and why.get_attribute("role") == "tooltip"
        assert not why.is_visible()
        split.hover()
        assert why.is_visible()  # visible au survol
        page.mouse.move(5, 5)
        assert not why.is_visible()
        split.focus()
        assert why.is_visible()  # et au focus, au clavier
        split.press("Enter")  # inerte : rien ne s'ouvre, la raison est redite
        assert page.locator("[data-role='decoupage']").count() == 0
        page.locator("[data-role='atelier-toasts'] [data-toast]", has_text="Valide d’abord").wait_for(timeout=APPEAR_MS)


# ── 5. La barre d'outils d'un bloc ne recouvre plus le texte ─────────────────

def test_the_block_toolbar_sits_above_the_text(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Barre d'outils")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        block = page.locator(".at-blk", has_text=PARA_A)
        block.wait_for(timeout=APPEAR_MS)
        block.hover()
        page.locator(".at-cm-add").first.wait_for(timeout=APPEAR_MS)
        gap = page.evaluate(
            """(text) => {
                const host = [...document.querySelectorAll('.at-blk')].find((n) => n.textContent.includes(text));
                const range = document.createRange();
                range.selectNodeContents(host.firstElementChild);
                const top = Math.min(...[...range.getClientRects()].map((r) => r.top));
                const tools = [host.querySelector('.at-cm-add'), host.querySelector('.at-ch-add')].map((n) => n.getBoundingClientRect().bottom);
                return top - Math.max(...tools);
            }""",
            PARA_A,
        )
        assert gap >= 0, f"les boutons « Challenger » et « Commenter » empiètent de {-gap}px sur le texte"


# ── 6. Commentaire orphelin : notification avec lien, puis rattachement par choix explicite ──

def test_rewriting_a_commented_passage_says_so_with_a_link_and_the_orphan_is_attached_by_picking(
    browser: Browser, project_on: tuple[str, Path]
) -> None:
    url, root = project_on
    slug = _chantier(root, "Orphelin notifie")
    cid = _comment(root, slug, "Que se passe-t-il quand la touche est lâchée ?")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        page.locator(f"[data-com='{cid}']").wait_for(timeout=APPEAR_MS)
        # Réécrit par le cockpit : le fil suit son bloc (« Le passage commenté a été modifié »), il n'est pas orphelin.
        page.locator(".at-blk", has_text=PARA_A).click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill(NEW_A)
        area.press("Control+Enter")
        page.locator(".at-blk", has_text="relâchement").wait_for(timeout=APPEAR_MS)
        followed = page.locator(f"[data-com='{cid}']")
        page.wait_for_function(f"() => document.querySelector(\"[data-com='{cid}']\")?.getAttribute('data-anchor') === 'retrouve'", timeout=APPEAR_MS)
        assert "Le passage commenté a été modifié" in followed.inner_text() and "n’a plus son passage" not in page.inner_text("body")
        followed.locator("[data-role='origin'] summary").click()  # l'extrait d'origine se déplie
        assert followed.locator("[data-role='origin'] blockquote").inner_text() == QUOTE
        # Réécrit hors du cockpit, passage ET voisin : plus rien ne le place, il est orphelin ; la page le dit.
        path = _folder(root, slug) / "02-brainstorm.md"
        path.write_text(path.read_text(encoding="utf-8").replace(NEW_A, "Un tout autre texte.").replace(PARA_B, f"{PARA_B} (revu)"), encoding="utf-8")
        page.evaluate("window.dispatchEvent(new Event('focus'))")  # le retour sur l'onglet relit le document
        page.wait_for_function(f"() => document.querySelector(\"[data-com='{cid}']\")?.getAttribute('data-anchor') === 'orphelin'", timeout=APPEAR_MS)
        assert "Orphelin" in page.locator(f"[data-com='{cid}']").inner_text()
        # « Rattacher ici » sans sélection : mode « choisis le bloc », clavier compris.
        page.locator(f"[data-com='{cid}']").get_by_role("button", name="Rattacher ce commentaire à la sélection du document ou au bloc courant").click()
        assert page.locator(".at-sheet.is-picking").count() == 1
        assert page.locator("[data-role='attach-pick']").is_visible()
        page.locator(".at-blk", has_text=PARA_B).focus()
        page.keyboard.press("Enter")  # Entrée choisit le bloc ; il ne s'ouvre pas en édition
        page.locator("[data-role='atelier-toasts'] [data-toast]", has_text="rattaché au bloc").wait_for(timeout=APPEAR_MS)
        assert page.locator(".is-editing").count() == 0 and page.locator(".at-sheet.is-picking").count() == 0
        assert page.locator(f"[data-com='{cid}']").get_attribute("data-anchor") == "ancre"


# ── 7 et 8. Repères, états vides et finitions ────────────────────────────────

def test_the_step_strip_and_the_small_finishes(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Reperes")
    _suggest(root, slug, "Une version plus courte.")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        strip = page.locator("[data-role='steps']")
        strip.wait_for(timeout=APPEAR_MS)
        assert [li.inner_text().split("\n")[0].strip() for li in strip.locator("li").all()] == ["Cadrer", "Exigences", "Valider", "Découper"]
        assert strip.locator("[aria-current='step']").count() == 1
        assert "Cadrer" in strip.locator("[aria-current='step']").inner_text()  # le brief et la compréhension sont à faire
        # « Agent propose », avec sa majuscule ; les accents graves d'une consigne en italique rendus comme du code.
        page.locator(".at-sugg-who").first.wait_for(timeout=APPEAR_MS)
        assert page.locator(".at-sugg-who").first.inner_text().strip() == "Agent propose"
        code = page.locator("[data-section='cahier-des-charges'] .at-intro code")
        assert code.inner_text() == "grimoire cadrage check"
        assert "`" not in page.locator("[data-section='cahier-des-charges'] .at-intro").inner_text()
        # Le pied de page n'a rien à voir avec l'Atelier de spec : « Panneaux ».
        assert page.locator("#st-atelier").inner_text().startswith("Panneaux")


def test_the_empty_library_explains_the_flow_and_the_empty_ideas_card_folds(browser: Browser, project_empty: tuple[str, Path]) -> None:
    url, _root = project_empty
    for page in _open(browser, url, "#atelier"):
        sub = page.locator(".at-sub")
        sub.wait_for(timeout=APPEAR_MS)
        assert "Valider et découper" in sub.inner_text() and "phase par phase" in sub.inner_text()
        ideas = page.locator("[data-role='ideas']")
        assert ideas.evaluate("(n) => n.tagName") == "DETAILS" and ideas.get_attribute("open") is None
        assert page.locator("[data-role='ideas'] .at-muted").first.is_hidden()  # repliée : une ligne


# ── Captures AVANT / APRÈS (hors CI : variable d'environnement) ──────────────

def test_captures(browser: Browser, project_on: tuple[str, Path]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    prefix = os.environ.get("GRIMOIRE_E2E_CAPTURE_PREFIX", "apres")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    url, root = project_on
    slug = _chantier(root, "Exporter le rapport mensuel en PDF", long=True)
    _suggest(root, slug, "Version plus courte.")
    _comment(root, slug, "Que se passe-t-il quand la touche est lâchée ?", quote=QUOTE, para=PARA_A)
    rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")
    for scheme in ("light", "dark"):
        for width, height in ((1440, 900), (1024, 768)):
            tag = f"{prefix}-{scheme}-{width}"
            for page in _open(browser, url, f"#atelier?chantier={slug}", (width, height), scheme=scheme):
                page.locator(".at-blk", has_text=PARA_B).wait_for(timeout=APPEAR_MS)
                page.wait_for_timeout(1500)  # l'entretien et la trace sont relus
                block = page.locator(".at-blk", has_text=PARA_B)
                block.scroll_into_view_if_needed()
                block.hover()
                block.locator("[data-act='comment']").click()
                page.locator(".at-cm-new .at-cm-area").fill("Ce passage manque d'un exemple.")
                page.wait_for_timeout(300)
                page.screenshot(path=str(folder / f"01-document-commentaire-{tag}.png"))
                page.get_by_role("button", name="Valider et découper").click()
                page.locator("[data-role='decoupage']").wait_for(timeout=APPEAR_MS)
                page.wait_for_timeout(300)
                page.screenshot(path=str(folder / f"02-decoupage-{tag}.png"))
