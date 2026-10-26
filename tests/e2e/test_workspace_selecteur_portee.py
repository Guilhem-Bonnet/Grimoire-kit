"""Le sélecteur de portée et la palette ⌘K, au style Douce (revue du 2026-10-25).

Le registre compte trente projets aux dossiers profonds et aux noms longs —
le cas où la liste, une table à hauteur fixe de 34 px, laissait les chemins
passer sur deux ou trois lignes et chevaucher la ligne suivante.

Ce que chaque test prouve :

- à 1440 px comme à 400 px, aucune ligne de la liste ne chevauche sa voisine,
  et aucun texte ne déborde de sa ligne ; la page ne défile pas en largeur ;
- le sélecteur est un menu ancré sous la puce, sur un fond assombri mais pas
  opaque (l'ancien voile était l'accent doux plein, la coque disparaissait) ;
- « Tous les projets » vient en tête ; la portée actuelle porte une coche, une
  seule, et plus une colonne « Portée actuelle » ;
- la recherche ignore les accents (« reference » trouve « Référence ») et
  porte sur le dossier affiché, plus sur l'étiquette cachée « Projet » ;
  survol et clavier tiennent la même ligne, Entrée choisit celle-là, Échap
  ferme.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from itertools import pairwise

import pytest
from playwright.sync_api import Browser, Page

from .conftest import _alive, _free_port, _wait_ready

HOME_NAME = "Projet ouvert"
#: Un dossier profond, comme un vrai poste : le chemin dépasse largement la
#: largeur du sélecteur, même à 1440 px.
DEEP = (
    "Clients",
    "Entreprise-Tres-Longue-Raison-Sociale",
    "Departement-des-projets-agentiques",
    "sous-dossier-de-travail",
    "encore-un-niveau-de-profondeur",
)
OTHERS = tuple(
    f"Référence agentique {i:02d}" if i % 2 else f"cockpit-upgrade-avec-un-nom-vraiment-long-{i:02d}" for i in range(29)
)
ROW_SELECTOR = "#palette-list li[role='option']"


@pytest.fixture(scope="session")
def served_portee(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("selecteur-portee")
    home = base / HOME_NAME
    home.mkdir()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-portee")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"
    run = {
        "env": env,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
        "timeout": 180,
    }
    subprocess.run(["git", "init", "-q"], cwd=str(home), check=False, capture_output=True)
    created = subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", HOME_NAME, "--backend", "local"],
        cwd=str(home),
        **run,
    )
    assert (home / "_grimoire" / "kit").is_dir(), f"`grimoire init` a échoué : {created.stderr[-400:]}"
    added = subprocess.run([sys.executable, "-m", "grimoire", "cockpit", "add", str(home)], **run)
    registry_path = cockpit_home / "registry.json"
    assert registry_path.is_file(), f"`grimoire cockpit add` n'a pas peuplé le registre : {added.stderr[-400:]}"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    home_slug = next((str(e["slug"]) for e in registry if e.get("path") == str(home.resolve())), "")
    assert home_slug, "slug du projet ouvert introuvable au registre"
    for index, name in enumerate(OTHERS):
        folder = base.joinpath(*DEEP, f"dossier-{index:02d}", name.replace(" ", "-"))
        (folder / "_grimoire").mkdir(parents=True)
        registry.append({"name": name, "path": str(folder.resolve()), "slug": f"autre-{index:02d}"})
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "grimoire",
            "cockpit",
            "serve",
            "--project-root",
            str(home),
            "--port",
            str(port),
            "--no-open",
            "--no-refresh",
        ],
        cwd=str(home),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "home": home_slug}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


def _open_selector(browser: Browser, served: dict[str, str], width: int, theme: str = "dark") -> Page:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served['url']}/workspace/index.html?project={served['home']}#a-traiter", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
    page.locator("#project-chip").click()
    page.wait_for_selector("#palette:not([hidden])")
    page.wait_for_function(
        "(n) => document.querySelectorAll(\"#palette-list li[role='option']\").length >= n", arg=len(OTHERS) + 2
    )
    return page


#: Pour chaque option : sa boîte, et celle de chacun de ses textes. Les boîtes
#: sont lues dans le conteneur qui défile, sans dépendre de ce qui est visible.
_BOXES = """() => [...document.querySelectorAll("#palette-list li[role='option']")].map((li) => {
  const r = li.getBoundingClientRect();
  return {
    top: r.top, bottom: r.bottom, left: r.left, right: r.right,
    text: li.textContent,
    parts: [...li.querySelectorAll('span')].map((s) => {
      const b = s.getBoundingClientRect();
      return { top: b.top, bottom: b.bottom, cls: s.className, text: s.textContent };
    }),
  };
})"""


@pytest.mark.parametrize("width", [1440, 400])
def test_aucune_ligne_du_selecteur_ne_chevauche_sa_voisine(
    browser: Browser, served_portee: dict[str, str], width: int
) -> None:
    page = _open_selector(browser, served_portee, width)
    try:
        rows = page.evaluate(_BOXES)
        assert len(rows) == len(OTHERS) + 2
        for above, below in pairwise(rows):
            assert below["top"] >= above["bottom"] - 0.5, (
                f"à {width} px, « {below['text'][:40]} » chevauche « {above['text'][:40]} »"
            )
        for row in rows:
            for part in row["parts"]:
                assert part["top"] >= row["top"] - 0.5 and part["bottom"] <= row["bottom"] + 0.5, (
                    f"à {width} px, le texte « {part['text'][:50]} » ({part['cls']}) déborde de sa ligne"
                )
        # Le dossier tient sur UNE ligne, quelle que soit sa longueur.
        heights = page.evaluate(
            """() => [...document.querySelectorAll("#palette-list .pal-path")].map((p) => {
                 const lh = parseFloat(getComputedStyle(p).lineHeight) || 16;
                 return p.getBoundingClientRect().height / lh;
               })"""
        )
        assert heights and max(heights) < 1.5, f"un dossier passe à la ligne : {max(heights):.2f} lignes"
        # Le chemin entier reste lisible au survol.
        assert page.locator("#palette-list .pal-path").first.get_attribute("title")
        # Le menu tient dans l'écran, sans défilement horizontal de la page.
        box = page.locator("#palette-box").bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
    finally:
        page.context.close()


def test_le_selecteur_est_un_menu_ancre_sur_un_fond_assombri(browser: Browser, served_portee: dict[str, str]) -> None:
    page = _open_selector(browser, served_portee, 1440, theme="light")
    try:
        chip = page.locator("#project-chip").bounding_box()
        box = page.locator("#palette-box").bounding_box()
        assert chip is not None and box is not None
        assert 0 <= box["y"] - (chip["y"] + chip["height"]) <= 16, "le menu doit s'ouvrir sous la puce"
        assert abs(box["x"] - chip["x"]) <= 2, "le menu doit s'aligner sur la puce"
        alpha = page.evaluate(
            """() => {
                 const c = getComputedStyle(document.getElementById('palette'), '::backdrop').backgroundColor;
                 const m = c.match(/rgba?\\(([^)]+)\\)/);
                 const parts = m ? m[1].split(',').map(Number) : [];
                 return parts.length === 4 ? parts[3] : 1;
               }"""
        )
        assert alpha < 0.6, f"le fond doit assombrir l'interface, pas la masquer (alpha {alpha})"
        # Un clic sur le fond referme le menu.
        page.mouse.click(1300, 800)
        page.wait_for_selector("#palette", state="hidden")
    finally:
        page.context.close()


def test_tous_les_projets_en_tete_et_une_coche_sur_la_portee_actuelle(
    browser: Browser, served_portee: dict[str, str]
) -> None:
    page = _open_selector(browser, served_portee, 1440)
    try:
        rows = page.locator(ROW_SELECTOR)
        assert rows.first.locator("span").first.inner_text() == "Tous les projets"
        current = page.locator(f"{ROW_SELECTOR}[aria-current='true']")
        assert current.count() == 1
        assert current.locator("span").first.inner_text() == HOME_NAME
        assert current.locator("svg[aria-label='Portée actuelle']").count() == 1
        # Plus de colonne de texte « Projet » / « Portée actuelle ».
        hints = page.locator("#palette-list .lbl").all_inner_texts()
        assert not {"Projet", "Portée actuelle", "Portée"} & {h.strip() for h in hints}
    finally:
        page.context.close()


def test_la_recherche_ignore_les_accents_et_porte_sur_le_dossier(
    browser: Browser, served_portee: dict[str, str]
) -> None:
    page = _open_selector(browser, served_portee, 1440)
    try:
        names = lambda: [n.strip() for n in page.locator(f"{ROW_SELECTOR} .pal-label").all_inner_texts()]  # noqa: E731
        accented = sorted(n for n in OTHERS if n.startswith("Référence"))

        # Sans accent, comme on le tape d'habitude : main ne trouvait rien.
        page.locator("#palette-input").fill("reference")
        assert names() == accented
        page.locator("#palette-input").fill("ref 07")
        assert names() == ["Référence agentique 07"]

        # Le dossier affiché sous le nom se cherche aussi.
        page.locator("#palette-input").fill("dossier-04")
        assert names() == ["cockpit-upgrade-avec-un-nom-vraiment-long-04"]

        # L'ancienne étiquette cachée ne fait plus correspondre tout le registre.
        page.locator("#palette-input").fill("actuelle")
        assert names() == []
        assert "Aucun résultat" in page.locator("#palette-list").inner_text()

        # Clavier et survol tiennent la même ligne ; Entrée choisit celle-là.
        page.locator("#palette-input").fill("reference")
        page.keyboard.press("ArrowDown")
        selected = page.locator(f"{ROW_SELECTOR}[aria-selected='true']")
        assert selected.count() == 1 and selected.locator(".pal-label").inner_text() == accented[1]
        target = page.locator(ROW_SELECTOR).nth(3)
        target.hover()
        assert target.get_attribute("aria-selected") == "true"
        assert page.locator(f"{ROW_SELECTOR}[aria-selected='true']").count() == 1
        assert page.locator("#palette-input").get_attribute("aria-activedescendant") == target.get_attribute("id")

        # Échap ferme sans rien choisir.
        page.keyboard.press("Escape")
        page.wait_for_selector("#palette", state="hidden")
        assert f"project={served_portee['home']}" in page.url

        page.locator("#project-chip").click()
        page.locator("#palette-input").fill("ref 05")
        page.keyboard.press("Enter")
        page.wait_for_url("**project=autre-05**", timeout=15_000)
    finally:
        page.context.close()
