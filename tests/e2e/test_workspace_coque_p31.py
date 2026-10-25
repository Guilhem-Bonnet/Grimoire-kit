"""La coque à cinq espaces, sa portée unique et ses liens profonds (P31).

Le cockpit sert un projet au nom accentué, « Référence agentique », dont le
registre tire le slug mutilé ``r-f-rence-agentique`` (le cas réel de la revue
UX, M6), plus treize autres projets : au-delà des douze que la palette
plafonnait par section. Le projet accentué est le projet ouvert (écriture
honorée) ; les autres ne se consultent qu'en lecture seule.

Ce que chaque test prouve :

- cinq espaces, dans l'ordre, le 4e nommé par ``glossary.js`` ;
- un lien profond (portée, espace, élément) rouvre le même écran sur la même
  sélection, et l'URL suit la navigation ;
- la lecture seule s'annonce par un bandeau en tête, avec le geste qui rend
  l'écriture ;
- le sélecteur de portée liste tout le registre ;
- la puce et le fil d'Ariane disent le nom, jamais le slug, et choisir un
  projet ouvre sa fiche, pas la table de la Flotte ;
- plus aucun zoom Flotte/Projet ni sélecteur de portée par espace ;
- Densité et Plein écran sont deux réglages, deux mots — « Concentration »
  n'apparaît plus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page

from .conftest import _alive, _free_port, _wait_ready

HOME_NAME = "Référence agentique"
TASK_TITLE = "Câbler le lien profond de la coque"
#: Treize projets de plus que le projet ouvert : 14 au registre, plus
#: « Tous les projets » — au-delà du plafond de 12 par section de la palette.
OTHERS = (
    "Arcanum-Royale",
    "Investissement",
    "TTS-Voice",
    "Terraform",
    "PC",
    "Brainstrom",
    "Écritures",
    "Studio Pixel",
    "Pédagogie",
    "Météo locale",
    "Cuisine",
    "Musique",
    "Zéphyr",
)


@pytest.fixture(scope="session")
def served_coque(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("coque-p31")
    home = base / HOME_NAME
    home.mkdir()
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-coque")
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
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", HOME_NAME, "--backend", "local"],
        cwd=str(home),
        **run,
    )
    if not (home / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grimoire",
            "task",
            "add",
            TASK_TITLE,
            "-a",
            "un lien copié rouvre la tâche",
            "--owner",
            "winston",
        ],
        cwd=str(home),
        **run,
    )
    from grimoire.missions.service import TaskService

    task = next((t for t in TaskService(home).list_tasks() if t.title == TASK_TITLE), None)
    if task is None:
        pytest.skip("`grimoire task add` n'a pas créé la tâche")

    subprocess.run([sys.executable, "-m", "grimoire", "cockpit", "add", str(home)], **run)
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    home_slug = next((str(e["slug"]) for e in registry if e.get("path") == str(home.resolve())), "")
    if not home_slug:
        pytest.skip("slug du projet ouvert introuvable au registre")
    # Les autres projets n'ont pas besoin d'un `init` : le sélecteur les liste
    # depuis le registre, et l'un d'eux (PC) sert le cas « lecture seule ».
    for index, name in enumerate(OTHERS):
        folder = base / name
        (folder / "_grimoire").mkdir(parents=True)
        registry.append({"name": name, "path": str(folder.resolve()), "slug": f"autre-{index:02d}"})
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    pc_slug = f"autre-{OTHERS.index('PC'):02d}"

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
        yield {"url": f"http://127.0.0.1:{port}", "home": home_slug, "pc": pc_slug, "task": task.id}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"cockpit survivant : pid {process.pid}"


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def _open(page: Page, served: dict[str, str], suffix: str = "") -> None:
    page.goto(f"{served['url']}/workspace/index.html{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)


def _query(page: Page) -> dict[str, list[str]]:
    return parse_qs(urlparse(page.url).query)


# ── Cinq espaces, le 4e nommé à un seul endroit ────────────────────────────


def test_cinq_espaces_dans_l_ordre_et_le_4e_nomme_par_glossary_js(page: Page, served_coque: dict[str, str]) -> None:
    _open(page, served_coque)
    assert page.evaluate("() => window.GrimoireWorkspace.spaces") == [
        "a-traiter",
        "travail",
        "flotte",
        "savoir",
        "configuration",
    ]
    fourth = page.evaluate("async () => (await import('./glossary.js')).FOURTH_SPACE.label")
    labels = page.locator("#spaces [role='tab']").all_inner_texts()
    assert [label.strip() for label in labels] == ["À traiter", "Travail", "Flotte", fourth, "Configuration"]
    # « À traiter » est l'accueil.
    assert page.evaluate("() => window.GrimoireWorkspace.space") == "a-traiter"


# ── Lien profond : portée, espace, élément ─────────────────────────────────


def test_un_lien_profond_rouvre_la_tache_dans_sa_portee_et_l_url_suit(page: Page, served_coque: dict[str, str]) -> None:
    home, task = served_coque["home"], served_coque["task"]
    _open(page, served_coque, f"?project={home}#travail?task={task}")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(
        "title => document.querySelector('#inspector-body').innerText.includes(title)",
        arg=TASK_TITLE,
    )
    assert page.evaluate("() => window.GrimoireWorkspace.scope") == {
        "all": False,
        "project": home,
        "name": HOME_NAME,
    }
    assert task in page.locator("#breadcrumb").inner_text()

    # Changer d'espace réécrit l'URL sans perdre la portée…
    page.locator("#tab-space-configuration").click()
    page.wait_for_function("() => window.GrimoireWorkspace.section === 'hotes'")
    assert urlparse(page.url).fragment == "configuration/hotes"
    assert _query(page)["project"] == [home]

    # … et « Précédent » ramène la tâche sélectionnée.
    page.go_back()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(
        "title => document.querySelector('#inspector-body').innerText.includes(title)",
        arg=TASK_TITLE,
    )
    assert urlparse(page.url).fragment == f"travail?task={task}"


def test_un_ancien_lien_d_espace_mene_a_sa_rubrique(page: Page, served_coque: dict[str, str]) -> None:
    _open(page, served_coque, "#source")
    page.wait_for_function("() => window.GrimoireWorkspace.section === 'fichiers'")
    assert page.evaluate("() => window.GrimoireWorkspace.space") == "configuration"
    assert urlparse(page.url).fragment == "configuration/fichiers"


# ── Lecture seule ───────────────────────────────────────────────────────────


def test_la_lecture_seule_s_annonce_en_tete_avec_le_geste_qui_la_leve(page: Page, served_coque: dict[str, str]) -> None:
    _open(page, served_coque, f"?project={served_coque['pc']}#flotte")
    banner = page.locator("#readonly-banner")
    assert banner.is_visible()
    assert "Lecture seule" in banner.inner_text()
    # En tête : au-dessus de la toile, au sommet du centre (la barre de document
    # n'existe plus hors atelier quand l'écran n'a ni fil d'Ariane ni vue : P62).
    banner_top = banner.bounding_box()["y"]
    assert banner_top < page.locator("#canvas").bounding_box()["y"]
    assert page.locator("#readonly-banner code").inner_text().startswith("grimoire cockpit serve --project-root")

    back = banner.locator("[data-ro-action='home']")
    assert back.inner_text() == f"Revenir à {HOME_NAME}"
    back.click()
    page.wait_for_url(f"**project={served_coque['home']}**")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    assert page.evaluate("() => window.GrimoireWorkspace.host.readOnly") is False
    assert page.locator("#readonly-banner").is_hidden()


# ── Sélecteur de portée ─────────────────────────────────────────────────────


def test_le_selecteur_de_portee_liste_tout_le_registre_sans_plafond(page: Page, served_coque: dict[str, str]) -> None:
    _open(page, served_coque)
    page.locator("#project-chip").click()
    options = page.locator("#palette-list li[role='option']")
    page.wait_for_function("() => document.querySelectorAll('#palette-list li[role=option]').length > 0")
    names = [options.nth(i).locator("span").first.inner_text() for i in range(options.count())]
    assert len(names) == 2 + len(OTHERS) > 12
    assert names[0] == "Tous les projets"
    assert set(OTHERS) | {HOME_NAME} <= set(names)
    # Le sélecteur ne montre que la portée : aucune autre section.
    sections = page.locator("#palette-list .palette-section").all_inner_texts()
    assert [section.strip().lower() for section in sections] == ["projets"]


def test_choisir_un_projet_ouvre_sa_fiche_nom_et_non_slug(page: Page, served_coque: dict[str, str]) -> None:
    home = served_coque["home"]
    assert home != HOME_NAME and "é" not in home  # le slug mutile bien les accents
    _open(page, served_coque, "?scope=all#a-traiter")
    assert page.locator("#project-name").inner_text() == "Tous les projets"

    page.locator("#project-chip").click()
    page.locator("#palette-list li[role='option']", has_text=HOME_NAME).first.click()
    page.wait_for_url(f"**project={home}**")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'flotte'")

    assert page.locator("#project-name").inner_text() == HOME_NAME
    crumb = page.locator("#breadcrumb").inner_text()
    assert crumb.split("›")[0].strip() == HOME_NAME
    for shown in (page.locator("#project-chip").inner_text(), crumb):
        assert home not in shown
    # La fiche du projet, pas la table de la Flotte.
    page.wait_for_selector("#canvas .space-title")
    assert page.locator("#canvas .space-title").first.inner_text() == HOME_NAME
    # (« Antifragilité » est une colonne de la table des projets seulement.)
    assert page.locator("#canvas th", has_text="Antifragilité").count() == 0


# ── Plus de zoom Flotte/Projet ni de portée par espace ──────────────────────


@pytest.mark.parametrize("scope", ["?scope=all", ""])
def test_aucun_espace_ne_porte_son_propre_zoom_flotte_projet(
    page: Page,
    served_coque: dict[str, str],
    scope: str,
) -> None:
    _open(page, served_coque, scope)
    sections = page.evaluate("() => Object.values(window.GrimoireWorkspace.sections).flat()")
    for section in sections:
        page.evaluate("id => window.GrimoireWorkspace.goto(id)", section)
        page.wait_for_function("id => window.GrimoireWorkspace.section === id", arg=section)
        page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")
        zoom = [t.strip() for t in page.locator("#zoom-seg button").all_inner_texts()]
        assert not {"Flotte", "Projet", "Ce projet", "Tous les projets"} & set(zoom), (section, zoom)
        assert page.locator("select[data-role='scope']").count() == 0, section


# ── Densité et Plein écran : deux réglages, deux mots ───────────────────────


def test_densite_et_plein_ecran_sont_deux_reglages_distincts(page: Page, served_coque: dict[str, str]) -> None:
    _open(page, served_coque, "#configuration/preferences")
    page.wait_for_selector(".pref-group")
    assert page.locator("#st-density").inner_text() == "Densité : confortable"
    assert page.locator("#st-fullscreen").inner_text().startswith("Plein écran")
    titles = page.locator(".pref-title").all_inner_texts()
    assert titles == ["Thème", "Densité", "Contraste", "Plein écran"]
    assert "concentration" not in page.locator("body").inner_text().lower()

    page.locator("[data-density-pref='compacte']").click()
    assert page.evaluate("() => window.GrimoireWorkspace.density") == "compacte"
    assert page.evaluate("() => window.GrimoireWorkspace.focus") == "off"

    page.locator("[data-fullscreen-pref]").click()
    assert page.evaluate("() => window.GrimoireWorkspace.focus") == "on"
    assert page.evaluate("() => window.GrimoireWorkspace.density") == "compacte"
    assert page.locator("#st-fullscreen").get_attribute("aria-pressed") == "true"
