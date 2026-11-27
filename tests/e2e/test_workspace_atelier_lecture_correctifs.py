"""Atelier de spec, lot correctif de lecture : un rejeu d'usage réel (3.127.0) a trouvé des défauts de lecture et de navigation.

Chaque test échoue sur le code d'avant le lot et prouve la correction, lue à l'écran dans un vrai navigateur :

- D7 : Ctrl+F trouve une expression qui chevauche du gras, du code ou un lien (surlignage sur plusieurs nœuds, avec ou sans l'API
  CSS Custom Highlight) ;
- D8 : l'impression ne rogne rien, enchaîne les phases, porte un en-tête (titre, date) et les commentaires ouverts en fin de
  document, sans intitulé orphelin ;
- D9 : un clic sur « Imprimer / PDF » n'appelle `window.print()` qu'une fois ;
- D10 : « Copier » et « Télécharger .md » rendent un document propre ; « Fichiers bruts » garde le contenu stocké ;
- D5 : sous 900 px, le document passe avant l'assistant, les actions de bloc n'interceptent pas le clic, la barre des phases ne
  tronque pas ses libellés et les cibles font 40 px ;
- D20 / D21 / D22 : « Pleine largeur » mémorisée, lien profond vers une phase, pastille de phase de l'historique ;
- le lien d'évitement « Aller au document » et le panneau de conflit d'édition qui prend le focus.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures (480 px, PDF) ; sans la variable le test de capture ne fait rien (il ne se
skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import (
    APPEAR_MS,
    BRAINSTORM,
    PARA_A,
    PARA_B,
    _box,
    _chantier,
    _comment,
    _folder,
    _human_terminal,  # noqa: F401  (fixture autouse : la MOA au terminal pour préparer l'état)
    _steady_box,
)

FIND = "[data-role='atelier-recherche']"
INPUT = "[data-role='find-input']"
COUNT = "[data-role='find-count']"
MENU = "[data-role='document-menu']"
PANEL = "#at-docmenu-panel"
SAY = "[data-role='doc-say']"
NO_HIGHLIGHT_API = "Object.defineProperty(CSS, 'highlights', { value: undefined, configurable: true });"
# La feuille sans les lignes d'état réel des exigences, que le suivi en direct redessine de lui-même (rien à voir avec la recherche).
SNAPSHOT = "() => { const c = document.querySelector('.at-sheet').cloneNode(true); c.querySelectorAll('[data-live]').forEach((n) => n.remove()); return c.innerHTML; }"
COUNT_PRINTS = "window.__prints = 0; window.print = () => { window.__prints += 1; };"
BOLD_PARA = "Les comptables passent **trois jours** par mois sur `export` et [la doc](https://example.com/tva) du mois."
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Ada Lovelace", "GIT_AUTHOR_EMAIL": "ada@example.org",
    "GIT_COMMITTER_NAME": "Ada Lovelace", "GIT_COMMITTER_EMAIL": "ada@example.org",
}


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-lecture-correctifs", roots):
        workspace_atelier.set_atelier(roots[0], True)
        workspace_atelier.set_mascotte(roots[0], True)
        yield url, roots[0]


def _open_page(
    browser: Browser, url: str, slug: str, *, viewport: tuple[int, int] = (1440, 900), scripts: tuple[str, ...] = (), phase: str = "", scheme: str = "light"
) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce", color_scheme=scheme, accept_downloads=True)  # type: ignore[arg-type]
    for script in scripts:
        context.add_init_script(script)
    page = context.new_page()
    try:
        suffix = f"&phase={phase}" if phase else ""
        page.goto(f"{url}/workspace/index.html#atelier?chantier={slug}{suffix}", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        yield page
    finally:
        context.close()


def _bold_chantier(root: Path, titre: str) -> str:
    """Un chantier dont le brief porte du gras, du code et un lien dans un même paragraphe."""
    slug = _chantier(root, titre)
    brief = _folder(root, slug) / "01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("> À compléter — le problème.", BOLD_PARA), encoding="utf-8")
    return slug


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ── D7 : la recherche à travers les nœuds inline ────────────────────────────


@pytest.mark.parametrize("needle", ["passent trois jours par", "trois jours par mois sur export et la doc du", "export et la"])
def test_find_matches_across_bold_code_and_link_nodes(browser: Browser, project_on: tuple[str, Path], needle: str) -> None:
    url, root = project_on
    slug = _bold_chantier(root, f"Recherche inline {len(needle)}")
    for page in _open_page(browser, url, slug):
        page.keyboard.press("Control+f")
        page.locator(INPUT).fill(needle)
        expect(page.locator(COUNT)).to_have_text("1 sur 1", use_inner_text=True)
        # Le surlignage se pose après le décompte : on attend la plage voulue, puis on compare les deux.
        page.wait_for_function(
            "(want) => ['at-find', 'at-find-current'].every((k) => { const h = CSS.highlights.get(k); return h && [...h].map((r) => r.toString()).join('|') === want; })",
            arg=needle,
            timeout=APPEAR_MS,
        )
        got = page.evaluate("() => [...CSS.highlights.get('at-find')].map((r) => r.toString())")
        assert got == [needle]
        current = page.evaluate("() => [...CSS.highlights.get('at-find-current')].map((r) => r.toString())")
        assert current == got


def test_find_counts_each_occurrence_once_across_nodes(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _bold_chantier(root, "Recherche compteur")
    for page in _open_page(browser, url, slug):
        page.keyboard.press("Control+f")
        page.locator(INPUT).fill("comptables passent trois")
        expect(page.locator(COUNT)).to_have_text("1 sur 1", use_inner_text=True)
        page.locator(INPUT).fill("jours")
        expect(page.locator(COUNT)).to_have_text("1 sur 1", use_inner_text=True)
        page.locator(INPUT).fill("mois sur")
        expect(page.locator(COUNT)).to_have_text("1 sur 1", use_inner_text=True)
        page.locator(INPUT).fill("zzz inexistant")
        expect(page.locator(COUNT)).to_have_text("Aucun résultat", use_inner_text=True)


def test_find_fallback_wraps_each_node_in_a_mark_and_leaves_nothing_behind(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _bold_chantier(root, "Recherche repli")
    for page in _open_page(browser, url, slug, scripts=(NO_HIGHLIGHT_API,)):
        before = page.evaluate(SNAPSHOT)
        page.keyboard.press("Control+f")
        page.locator(INPUT).fill("passent trois jours par")
        expect(page.locator(COUNT)).to_have_text("1 sur 1", use_inner_text=True)
        # Le suivi en direct peut redessiner la feuille (la recherche relit alors ses occurrences) : on attend l'état posé.
        page.wait_for_function(
            "() => { const m = [...document.querySelectorAll('mark.at-hit')]; return m.length >= 2 && m.map((n) => n.textContent).join('') === 'passent trois jours par' && m.every((n) => n.classList.contains('is-current')); }",
            timeout=APPEAR_MS,
        )
        page.keyboard.press("Escape")
        expect(page.locator("mark.at-hit")).to_have_count(0)
        page.wait_for_function(f"(before) => ({SNAPSHOT})() === before", arg=before, timeout=APPEAR_MS)


# ── D9 et D10 : le menu « Document » ────────────────────────────────────────


def test_a_click_on_print_calls_window_print_once(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Imprimer une fois")
    for page in _open_page(browser, url, slug, scripts=(COUNT_PRINTS,)):
        page.locator(MENU).click()
        page.locator("[data-role='doc-print']").click()
        page.wait_for_function("() => window.__prints >= 1", timeout=APPEAR_MS)
        # « Une seule fois » est une assertion négative : le clic appelle ``window.print()`` de façon synchrone, donc un second appel
        # viendrait du même geste ; deux images d'animation de plus bornent l'attente sans minuterie arbitraire.
        page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
        assert page.evaluate("window.__prints") == 1


def _stored(root: Path, slug: str) -> str:
    texts = [p.read_text(encoding="utf-8") for p in sorted(_folder(root, slug).glob("0*.md"))]
    return "\n".join(t if t.endswith("\n") else t + "\n" for t in texts if t.strip())


def test_copy_and_download_give_a_clean_document_and_raw_files_keep_the_stored_content(
    browser: Browser, project_on: tuple[str, Path], tmp_path: Path
) -> None:
    url, root = project_on
    slug = _chantier(root, "Export propre")
    for page in _open_page(browser, url, slug):
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        page.locator(MENU).click()
        with page.expect_download(timeout=APPEAR_MS) as info:
            page.locator("[data-role='doc-download']").click()
        target = tmp_path / "propre.md"
        info.value.save_as(target)
        clean = target.read_text(encoding="utf-8")
        assert info.value.suggested_filename == f"{slug}.md"
        assert clean.startswith("# Export propre\n")
        assert "phase:" not in clean and "projet:" not in clean and "\n---\n" not in clean
        assert "À compléter" not in clean
        assert re.findall(r"^## (.+)$", clean, flags=re.MULTILINE) == ["Brainstorm — diverger", "Exigences"]
        assert PARA_A in clean and "REQ-001" in clean
        # Le menu se ferme après l'action et annonce le résultat.
        expect(page.locator(PANEL)).to_be_hidden()
        say = page.locator(SAY)
        expect(say).to_have_attribute("role", "status")
        expect(say).to_contain_text("téléchargé", use_inner_text=True)
        # La copie rend le même document.
        page.locator(MENU).click()
        page.locator("[data-role='doc-copy']").click()
        page.wait_for_function("() => document.querySelector('[data-role=doc-say]').textContent.includes('copié')", timeout=APPEAR_MS)
        assert page.evaluate("navigator.clipboard.readText()") == clean
        expect(page.locator(PANEL)).to_be_hidden()
        # Les fichiers bruts : le contenu stocké, tel quel.
        page.locator(MENU).click()
        with page.expect_download(timeout=APPEAR_MS) as raw:
            page.locator("[data-role='doc-download-raw']").click()
        raw_target = tmp_path / "brut.md"
        raw.value.save_as(raw_target)
        assert raw.value.suggested_filename.endswith(".md") and slug in raw.value.suggested_filename
        assert raw_target.read_text(encoding="utf-8") == _stored(root, slug)
        expect(page.locator(PANEL)).to_be_hidden()


# ── D8 : l'impression ───────────────────────────────────────────────────────


def _print_chantier(root: Path, titre: str) -> str:
    slug = _chantier(root, titre)
    folder = _folder(root, slug)
    brief = folder / "01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("> À compléter — le problème.", BOLD_PARA), encoding="utf-8")
    _comment(root, slug, "Une question encore ouverte sur ce passage.")
    # Un second commentaire dont le passage disparaît ensuite : il ne doit laisser aucun intitulé orphelin à l'impression.
    _comment(root, slug, "Ce passage va disparaître.", quote="repli du navigateur", para=PARA_B)
    path = folder / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace(PARA_B, "Un autre paragraphe tout neuf."), encoding="utf-8")
    return slug


def test_print_does_not_clip_chains_phases_and_carries_a_header_and_the_open_comments(
    browser: Browser, project_on: tuple[str, Path], tmp_path: Path
) -> None:
    url, root = project_on
    slug = _print_chantier(root, "Impression propre")
    for page in _open_page(browser, url, slug):
        page.emulate_media(media="print")
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        # Les commentaires ouverts sont imprimés en fin de document : leur présence dit que l'état d'impression est posé.
        notes = page.locator(".at-print-notes")
        expect(notes).to_be_visible()
        expect(notes).to_contain_text("Une question encore ouverte", use_inner_text=True)
        # Rien ne déborde à gauche de la feuille (la marge négative des blocs d'écran ne passe pas au papier).
        page.wait_for_function(
            "() => { const s = document.querySelector('.at-sheet').getBoundingClientRect().left; "
            "return [...document.querySelectorAll('.at-sheet .at-blk, .at-sheet p, .at-sheet li')].filter((n) => n.checkVisibility() && n.getBoundingClientRect().left < s - 0.5).length === 0; }",
            timeout=APPEAR_MS,
        )
        # Les phases s'enchaînent : aucun saut de page forcé avant une phase.
        page.wait_for_function("() => [...document.querySelectorAll('.at-sec')].filter((n) => ['page', 'always', 'left', 'right'].includes(getComputedStyle(n).breakBefore)).length === 0", timeout=APPEAR_MS)
        expect(page.locator(".at-print-title")).to_have_text("Impression propre", use_inner_text=True)
        expect(page.locator(".at-print-date")).to_have_text(re.compile(r"\d{1,2} \S+ 20\d\d"), use_inner_text=True)
        # Rien d'orphelin.
        page.wait_for_function("() => [...document.querySelectorAll('.at-cm-orphans')].filter((n) => n.checkVisibility()).length === 0", timeout=APPEAR_MS)
        assert "Passage modifié ou supprimé" not in page.evaluate("document.body.innerText")
        pdf = tmp_path / "atelier.pdf"
        page.pdf(path=str(pdf), format="A4", print_background=True)
        pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
        assert 1 <= pages <= 3, pages
        if shutil.which("pdftotext"):  # aucun mot amputé : le texte extrait du PDF contient chaque ligne en entier
            text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
            flat = _flat(text)
            for expected in ("Impression propre", "Les comptables passent trois jours par mois", PARA_A, "REQ-001", "Une question encore ouverte"):
                assert expected in flat, expected
            assert "Passage modifié ou supprimé" not in flat
            assert not re.search(r"(^|\n)\s*(es comptables|e problème|ravail)", text), "premier caractère rogné"


# ── D5 : sous 900 px ────────────────────────────────────────────────────────


def _narrow_scripts(slug: str) -> tuple[str, ...]:
    # Un choix d'un autre écran (l'assistant déplié sur le bureau) ne doit pas recouvrir le document d'un téléphone.
    return (f"try {{ localStorage.setItem('grimoire.atelier.assistant.{slug}', 'open'); }} catch (e) {{}}",)


def test_below_900px_the_document_comes_first_and_the_assistant_waits_behind_its_pill(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Etroit assistant", long=True)
    for page in _open_page(browser, url, slug, viewport=(480, 900), scripts=_narrow_scripts(slug)):
        expect(page.locator("[data-role='assistant']")).to_be_hidden()
        pill = page.locator("[data-role='assistant-pill']")
        expect(pill).to_be_visible()
        top = _box(page, ".at-sheet")["y"]
        assert top < 760, top
        pill.click()
        expect(page.locator("[data-role='assistant']")).to_be_visible()


def test_below_900px_the_phase_bar_scrolls_without_cutting_a_label_and_targets_are_touchable(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Etroit barre", long=True)
    for page in _open_page(browser, url, slug, viewport=(480, 900)):
        # Le libellé visible (le nœud de texte, pas le mot caché pour les lecteurs d'écran) tient dans sa pastille.
        # Mesures de mise en page : on attend l'état voulu (la mise en page se pose en place), le test échoue si elle ne s'y pose jamais.
        page.wait_for_function(
            """() => [...document.querySelectorAll('.at-phase')].length > 0 && [...document.querySelectorAll('.at-phase')].filter((b) => {
              const text = [...b.childNodes].find((n) => n.nodeType === 3 && n.nodeValue.trim());
              const range = document.createRange(); range.selectNodeContents(text);
              return range.getBoundingClientRect().right > b.getBoundingClientRect().right - 4;
            }).length === 0""",
            timeout=APPEAR_MS,
        )
        page.wait_for_function("() => ['auto', 'scroll'].includes(getComputedStyle(document.querySelector('.at-phases')).overflowX)", timeout=APPEAR_MS)
        page.wait_for_function(
            "() => ['.at-phase', \"[data-role='assistant-pill']\", \"[data-role='document-menu']\", \"[data-role='plan-toggle']\", '.at-cm-add', '.at-ch-add']"
            ".flatMap((s) => [...document.querySelectorAll(s)].filter((n) => n.checkVisibility()).map((n) => [s, Math.round(n.getBoundingClientRect().height)])).filter(([, h]) => h < 40).length === 0",
            timeout=APPEAR_MS,
        )


def test_below_900px_block_actions_never_cover_the_text_nor_take_its_click(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Etroit actions", long=True)
    for page in _open_page(browser, url, slug, viewport=(480, 900)):
        host = page.locator(".at-blk", has_text=PARA_A).first
        host.scroll_into_view_if_needed()
        page.wait_for_function(
            """() => {
              const host = [...document.querySelectorAll('.at-blk')].find((b) => b.textContent.includes('Le bouton maintenu'));
              const text = host.querySelector('p').getBoundingClientRect();
              return [...host.querySelectorAll('.at-cm-add, .at-ch-add')].filter((b) => {
                const r = b.getBoundingClientRect();
                return r.left < text.right && r.right > text.left && r.top < text.bottom && r.bottom > text.top;
              }).length === 0;
            }""",
            timeout=APPEAR_MS,
        )
        box = _steady_box(page.locator(".at-blk", has_text=PARA_A).first.locator("p"))
        page.mouse.click(box["x"] + box["width"] - 6, box["y"] + 4)
        expect(page.locator("textarea[aria-label^='Modifier le bloc']")).to_be_visible()
        expect(page.locator(".at-ch-menu:not([hidden])")).to_have_count(0)


# ── D20, D21, D22 ───────────────────────────────────────────────────────────


def test_full_width_choice_survives_a_reload_on_a_direct_landing(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Largeur rechargee")
    for page in _open_page(browser, url, slug):
        toggle = page.locator("[data-role='atelier-largeur']")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        toggle.click()
        expect(toggle).to_have_attribute("aria-pressed", "false")
        page.reload()
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        expect(page.locator("[data-role='atelier-largeur']")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("html")).to_have_attribute("data-focus", "off")


def test_a_deep_link_scrolls_to_the_phase_and_marks_it(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Lien profond", long=True)
    for page in _open_page(browser, url, slug, phase="exigences"):
        page.wait_for_function(
            "() => { const h = document.querySelector('[data-section=\"exigences\"] h2'); const t = h && h.getBoundingClientRect().top; return t > 0 && t < 260; }",
            timeout=APPEAR_MS,
        )
        expect(page.locator(".at-phase[data-phase='exigences']")).to_have_attribute("aria-current", "step")
        expect(page.locator(".at-phase[aria-current='step']")).to_have_count(1)


@pytest.mark.parametrize("start", ["same", "library"])
def test_a_deep_link_typed_on_a_page_already_open_scrolls_to_the_phase(browser: Browser, project_on: tuple[str, Path], start: str) -> None:
    url, root = project_on
    slug = _chantier(root, f"Lien profond {start}", long=True)
    for page in _open_page(browser, url, slug):
        if start == "library":
            page.evaluate("() => { location.hash = '#atelier'; }")
            page.wait_for_selector(".at-sheet", state="detached", timeout=APPEAR_MS)
        page.evaluate("(h) => { location.hash = h; }", f"#atelier?chantier={slug}&phase=exigences")
        page.wait_for_function(
            "() => { const h = document.querySelector('[data-section=\"exigences\"] h2'); const t = h && h.getBoundingClientRect().top; return t > 0 && t < 260; }",
            timeout=APPEAR_MS,
        )
        expect(page.locator(".at-phase[data-phase='exigences']")).to_have_attribute("aria-current", "step")


@pytest.fixture(scope="module")
def project_git(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, str]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-lecture-histo", roots):
        root = roots[0]
        workspace_atelier.set_atelier(root, True)
        made = new_chantier(root, "Historique phase")
        base = root / "_grimoire" / "cadrage" / made.slug
        brief, exig = base / "01-brief.md", base / "04-exigences.md"
        brief.write_text("# Brief\n\nDicter une idée au micro.\n", encoding="utf-8")
        exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter une idée\n", encoding="utf-8")
        rels = [str(brief.relative_to(root)), str(exig.relative_to(root))]
        subprocess.run(["git", "-c", "commit.gpgsign=false", "add", "--", *rels], cwd=root, check=True, capture_output=True, env=ENV)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "feat: premier jet"], cwd=root, check=True, capture_output=True, env=ENV)
        brief.write_text("# Brief\n\nDicter une idée au micro.\nSans clavier du tout.\n", encoding="utf-8")
        subprocess.run(["git", "-c", "commit.gpgsign=false", "add", "--", rels[0]], cwd=root, check=True, capture_output=True, env=ENV)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fix: precise le brief"], cwd=root, check=True, capture_output=True, env=ENV)
        yield url, root, made.slug


def test_history_marks_the_phase_whose_diff_is_shown(browser: Browser, project_git: tuple[str, Path, str]) -> None:
    url, _root, slug = project_git
    for page in _open_page(browser, url, slug):
        page.locator(".at-phase[data-phase='exigences']").click()
        expect(page.locator(".at-phase[aria-current='step']")).to_have_attribute("data-phase", "exigences")
        page.locator("[data-role='atelier-historique-bouton']").click()
        page.locator("[data-version]", has_text="precise le brief").click()
        page.locator("[data-role='hist-comparaison']").wait_for(timeout=APPEAR_MS)
        expect(page.locator(".at-cmp-phase")).to_have_count(1)
        expect(page.locator(".at-cmp-phase")).to_have_attribute("data-phase", "brief")
        expect(page.locator(".at-phase[aria-current='step']")).to_have_attribute("data-phase", "brief")
        page.keyboard.press("Escape")
        expect(page.locator("[data-role='hist-comparaison']")).to_have_count(0)


# ── Frictions : lien d'évitement, panneau de conflit ────────────────────────


def test_a_skip_link_leads_straight_to_the_first_block(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Lien evitement", long=True)
    for page in _open_page(browser, url, slug):
        link = page.locator("[data-role='skip-document']")
        expect(link).to_have_text("Aller au document", use_inner_text=True)
        first = page.evaluate("() => document.querySelector('.at-page').querySelector('a[href], button, input, select, textarea, [tabindex=\"0\"]').dataset.role")
        assert first == "skip-document"
        assert link.bounding_box() is not None
        link.focus()
        box = _steady_box(link)  # le lien d'évitement se pose à l'écran au focus : on mesure une fois sa position établie
        assert 0 <= box["x"] < 1440 and 0 <= box["y"] < 900
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.classList.contains('at-blk')", timeout=APPEAR_MS)


def test_the_conflict_panel_scrolls_into_view_and_takes_the_focus(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Conflit focus", long=True)
    path = _folder(root, slug) / BRAINSTORM
    for page in _open_page(browser, url, slug):
        page.locator(".at-blk", has_text=PARA_A).first.click()
        area = page.locator("[data-role='block-edit'] textarea")
        area.fill(PARA_A + " Ma version.")
        path.write_text(path.read_text(encoding="utf-8").replace(PARA_A, "Version de l'agent, écrite pendant la saisie."), encoding="utf-8")
        area.press("Control+Enter")
        box = page.locator("[data-role='block-conflict']")
        box.wait_for(timeout=APPEAR_MS)
        expect(box).to_have_attribute("role", "alert")
        page.wait_for_function("() => document.querySelector('[data-role=block-conflict]').contains(document.activeElement)", timeout=5000)
        top = _box(page, "[data-role='block-conflict']")["y"]
        assert 0 <= top < 900


# ── Captures ────────────────────────────────────────────────────────────────


def test_capture_lecture_correctifs(browser: Browser, project_on: tuple[str, Path]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    slug = _print_chantier(root, "Export CSV des factures")
    for page in _open_page(browser, url, slug, viewport=(480, 900), scripts=_narrow_scripts(slug)):
        page.screenshot(path=str(folder / "480-document.png"))
        page.locator(".at-blk", has_text=PARA_A).first.scroll_into_view_if_needed()
        page.screenshot(path=str(folder / "480-bloc.png"))
    for page in _open_page(browser, url, slug):
        page.emulate_media(media="print")
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        page.pdf(path=str(folder / "impression.pdf"), format="A4", print_background=True)
