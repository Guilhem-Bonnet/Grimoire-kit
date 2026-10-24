"""L'écran « À traiter » (P32), dans un vrai navigateur, sur un vrai cockpit.

Le cockpit est ouvert sur « Atelier Nord », qui porte trois éléments réels :
une question posée par ``task ask``, une proposition en attente et une tâche
bloquée. Le registre compte trois autres projets :

- « Studio Pixel », initialisé, une tâche bloquée — consulté depuis le cockpit
  d'Atelier Nord, donc en lecture seule ;
- « Calme », un dossier sans ledger — rien n'y attend ;
- « Banc d'essai », dont le dossier n'existe pas — il ne répond pas.

Ce que chaque test prouve :

- la liste est groupée par urgence, une ligne par élément avec projet, titre,
  raison, ancienneté et action en ligne ;
- l'action ouvre l'écran de décision avec la sélection posée : la tâche dans
  Travail (dans la portée du projet comme dans « Tous les projets »), la
  question dans la vue Décisions, la proposition sur la fiche du projet ;
- état vide : « Rien n'attend de décision » et l'heure de la vérification ;
- erreur partielle : « 1 projet n'a pas répondu », et Réessayer relance la
  lecture avec la sonde ;
- lecture seule : actions désactivées, la raison écrite à côté ;
- clavier : j / k passent d'une ligne à l'autre, Entrée ouvre l'action.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test y écrit les captures
de l'écran (clair, sombre, mobile, vide, lecture seule) pour la relecture ;
sans la variable il ne fait rien (il ne se skippe pas : le mode strict de la CI
promeut tout skip en échec).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page

from .conftest import _alive, _free_port, _wait_ready

HOME_NAME = "Atelier Nord"
BLOCKED_TITLE = "Brancher la CI du lot B"
BLOCKED_REASON = "la CI est rouge sur main"
QUESTION_TITLE = "Choisir le format d'export"
QUESTION_TEXT = "CSV ou JSON pour l'export ?"
SPECIALTY = "cartographie-des-flux"
STUDIO = "Studio Pixel"
STUDIO_TASK = "Valider la palette du studio"
CALM = "Calme"
MISSING = "Banc d'essai"


def _grimoire(cwd: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )


def _init(folder: Path, name: str, env: dict[str, str]) -> None:
    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(folder), check=False, capture_output=True)
    _grimoire(folder, "init", ".", "-y", "--name", name, "--backend", "local", env=env)
    if not (folder / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")


def _task(folder: Path, title: str, env: dict[str, str]) -> str:
    """Une tâche réclamée (ready → claimed), prête à être bloquée ou à poser une question."""
    added = _grimoire(folder, "task", "add", title, "-a", "un critère observable", "--owner", "winston", env=env)
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche : {added.stderr[-400:]}")
    task_id = match.group(1)
    for step in (["task", "move", task_id, "--to", "ready"], ["task", "claim", task_id]):
        outcome = _grimoire(folder, *step, env=env)
        if outcome.returncode != 0:
            pytest.skip(f"préparation interrompue à {step} : {outcome.stderr[-400:]}")
    return task_id


def _block(folder: Path, task_id: str, reason: str, env: dict[str, str]) -> None:
    outcome = _grimoire(folder, "task", "block", task_id, "--reason", reason, env=env)
    if outcome.returncode != 0:
        pytest.skip(f"`grimoire task block` refusé : {outcome.stderr[-400:]}")


def _proposal(folder: Path) -> str:
    """Une proposition ``pending``, écrite par le moteur réel (même voie que test_workspace_proposals)."""
    from grimoire.proposals import _build_proposal, _proposal_path, _save_proposal

    proposal = _build_proposal(
        specialty=SPECIALTY,
        count=2,
        category="infra",
        fallback_agent="",
        carrier="",
        carrier_reason="persona d'entrée exclue, aucun porteur : agent",
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-01-01T00:00:00+00:00",
    )
    _save_proposal(_proposal_path(folder, proposal.slug), proposal)
    return proposal.slug


@pytest.fixture(scope="session")
def served_inbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("a-traiter-p32")
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-a-traiter")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"

    home = base / HOME_NAME
    _init(home, HOME_NAME, env)
    question = _task(home, QUESTION_TITLE, env)
    asked = _grimoire(home, "task", "ask", question, QUESTION_TEXT, "-o", "CSV", "-o", "JSON", env=env)
    if asked.returncode != 0:
        pytest.skip(f"`grimoire task ask` refusé : {asked.stderr[-400:]}")
    blocked = _task(home, BLOCKED_TITLE, env)
    _block(home, blocked, BLOCKED_REASON, env)
    proposal = _proposal(home)

    studio = base / STUDIO
    _init(studio, STUDIO, env)
    _block(studio, _task(studio, STUDIO_TASK, env), "la maquette attend une relecture", env)
    calm = base / CALM
    (calm / "_grimoire").mkdir(parents=True)

    for folder in (home, studio, calm):
        _grimoire(base, "cockpit", "add", str(folder), env=env)
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slugs = {Path(e["path"]).name: str(e["slug"]) for e in registry}
    if not {HOME_NAME, STUDIO, CALM} <= set(slugs):
        pytest.skip("le registre ne porte pas les trois projets")
    # Un projet dont le dossier a disparu : la route d'attention le dit illisible.
    registry.append({"name": MISSING, "path": str(base / MISSING), "slug": "banc-d-essai"})
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
        yield {
            "url": f"http://127.0.0.1:{port}",
            "home": slugs[HOME_NAME],
            "studio": slugs[STUDIO],
            "calm": slugs[CALM],
            "blocked": blocked,
            "question": question,
            "proposal": proposal,
        }
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


def _open(page: Page, served: dict[str, str], suffix: str) -> None:
    page.goto(f"{served['url']}/workspace/index.html{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


def _fragment(page: Page) -> tuple[str, dict[str, list[str]]]:
    path, _, query = urlparse(page.url).fragment.partition("?")
    return path, parse_qs(query)


def _row(page: Page, title: str):
    return page.locator(".at-row", has=page.locator(".at-title", has_text=title))


# ── Liste groupée par urgence ───────────────────────────────────────────────


def test_liste_groupee_par_urgence_une_ligne_par_element(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    sections = page.locator(".at-section")
    assert [sections.nth(i).get_attribute("data-section") for i in range(sections.count())] == ["decide", "unblock"]
    assert sections.nth(0).locator(".at-section-head h3").inner_text() == "À décider"
    assert sections.nth(1).locator(".at-section-head h3").inner_text() == "À débloquer"
    kinds = [row.get_attribute("data-kind") for row in page.locator(".at-row").all()]
    assert kinds == ["question", "proposal", "task_blocked"]

    blocked = _row(page, BLOCKED_TITLE)
    assert blocked.locator(".at-project").inner_text() == HOME_NAME
    assert blocked.locator(".at-reason").inner_text() == BLOCKED_REASON
    since = blocked.locator("time.at-since")
    assert re.fullmatch(r"il y a \d+ (s|min|h|j)", since.inner_text())
    assert since.get_attribute("datetime")
    action = blocked.locator(".at-act")
    assert action.inner_text() == "Débloquer"
    assert action.get_attribute("href").endswith(f"#travail?task={served_inbox['blocked']}")
    # L'icône de type porte son mot pour les lecteurs d'écran.
    assert blocked.locator(".glyph svg[role='img']").get_attribute("aria-label") == "tâche bloquée"
    question = _row(page, QUESTION_TITLE)
    assert question.locator(".at-reason").inner_text() == QUESTION_TEXT
    assert question.locator(".at-act").inner_text() == "Répondre"

    # La première ligne est choisie : son détail est dans l'inspecteur.
    assert page.locator(".at-row").first.get_attribute("aria-current") == "true"
    assert QUESTION_TITLE in page.locator("#inspector-body .at-detail h3").inner_text()


def test_l_action_ouvre_travail_sur_la_tache_dans_la_portee_du_projet(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    _row(page, BLOCKED_TITLE).locator(".at-act").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(
        "title => document.querySelector('#inspector-body').innerText.includes(title)", arg=BLOCKED_TITLE
    )
    assert _fragment(page) == ("travail", {"task": [served_inbox["blocked"]]})
    assert page.evaluate("() => window.GrimoireWorkspace.scope.project") == served_inbox["home"]


def test_l_action_garde_la_portee_tous_les_projets_et_pose_la_tache(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, "?scope=all#a-traiter")
    # Les éléments de tous les projets lisibles, chacun avec le nom du sien.
    page.wait_for_selector(".at-row[data-kind='task_blocked']")
    projects = {row.inner_text() for row in page.locator(".at-row .at-project").all()}
    assert {HOME_NAME, STUDIO} <= projects
    _row(page, BLOCKED_TITLE).locator(".at-act").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(
        "title => document.querySelector('#inspector-body').innerText.includes(title)", arg=BLOCKED_TITLE
    )
    assert page.evaluate("() => window.GrimoireWorkspace.scope.all") is True
    assert _fragment(page) == ("travail", {"task": [served_inbox["blocked"]], "of": [served_inbox["home"]]})


def test_la_question_ouvre_la_vue_decisions_sur_sa_tache(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    _row(page, QUESTION_TITLE).locator(".at-act").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function("text => document.querySelector('#canvas').innerText.includes(text)", arg=QUESTION_TEXT)
    assert _fragment(page)[1]["view"] == ["decisions"]
    assert _fragment(page)[1]["task"] == [served_inbox["question"]]


def test_la_proposition_ouvre_la_fiche_du_projet_sur_elle(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    _row(page, SPECIALTY).locator(".at-act").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'flotte'")
    focused = page.locator(f".pl-prop-row[data-proposal='{served_inbox['proposal']}'][data-focused='true']")
    focused.wait_for()
    assert focused.is_visible()
    assert SPECIALTY in focused.inner_text()
    assert _fragment(page) == ("flotte", {"focus": ["proposal"], "proposal": [served_inbox["proposal"]]})


# ── États : vide, erreur partielle, lecture seule ───────────────────────────


def test_etat_vide_dit_rien_n_attend_et_l_heure_de_verification(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['calm']}#a-traiter")
    empty = page.locator("#canvas .empty")
    assert empty.locator("h2").inner_text() == "Rien n'attend de décision"
    assert re.search(r"Dernière vérification : \d{2}:\d{2}\.", empty.inner_text())
    assert page.locator(".at-row").count() == 0
    assert page.locator(".at-partial").count() == 0
    empty.locator("[data-empty-action]").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")


def test_erreur_partielle_nomme_le_projet_muet_et_reessaie_avec_la_sonde(
    page: Page, served_inbox: dict[str, str]
) -> None:
    _open(page, served_inbox, "?scope=all#a-traiter")
    partial = page.locator(".at-partial")
    assert partial.locator("strong").inner_text() == "1 projet n'a pas répondu"
    assert MISSING in partial.inner_text()
    # Jamais une omission silencieuse : il reste un élément « projet illisible ».
    assert page.locator(".at-row[data-kind='unreadable']").count() == 1

    with page.expect_request(lambda r: "/api/workspace/attention" in r.url and "probe=1" in r.url) as sent:
        partial.get_by_role("button", name="Réessayer").click()
    assert "scope=portfolio" in sent.value.url
    page.wait_for_function(
        "() => document.querySelector('.at-partial strong')?.innerText === \"1 projet n'a pas répondu\""
    )


def test_lecture_seule_desactive_les_actions_et_dit_pourquoi(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['studio']}#a-traiter")
    assert page.evaluate("() => window.GrimoireWorkspace.host.readOnly") is True
    row = _row(page, STUDIO_TASK)
    action = row.locator(".at-act")
    assert action.evaluate("el => el.tagName") == "BUTTON"
    assert action.is_disabled()
    assert action.get_attribute("aria-describedby") == "at-ro-reason"
    reason = page.locator("#at-ro-reason")
    assert reason.is_visible()
    assert reason.inner_text().startswith(f"Lecture seule : {STUDIO} se consulte d’ici")
    # Entrée sur la ligne n'ouvre rien : la décision ne se prend pas d'ici.
    row.click()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.GrimoireWorkspace.space") == "a-traiter"
    assert page.locator("#inspector-body .at-detail .at-act").is_disabled()


# ── Clavier ─────────────────────────────────────────────────────────────────


def test_j_et_k_parcourent_les_lignes_et_entree_ouvre_l_action(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    rows = page.locator(".at-row")
    current = (
        "() => [...document.querySelectorAll('.at-row')].findIndex(r => r.getAttribute('aria-current') === 'true')"
    )
    assert page.evaluate(current) == 0
    page.keyboard.press("j")
    assert page.evaluate(current) == 1
    assert page.evaluate("() => document.activeElement.dataset.kind") == "proposal"
    page.keyboard.press("j")
    page.keyboard.press("j")  # déjà sur la dernière ligne : on y reste
    assert page.evaluate(current) == 2
    page.keyboard.press("k")
    page.keyboard.press("j")
    assert rows.nth(2).get_attribute("data-kind") == "task_blocked"
    # La sélection entre dans l'URL : un lien copié la rouvre.
    assert _fragment(page)[1]["item"] == [rows.nth(2).get_attribute("data-item")]
    assert BLOCKED_TITLE in page.locator("#inspector-body .at-detail h3").inner_text()

    page.keyboard.press("Enter")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(
        "title => document.querySelector('#inspector-body').innerText.includes(title)", arg=BLOCKED_TITLE
    )
    assert _fragment(page) == ("travail", {"task": [served_inbox["blocked"]]})


def test_un_lien_profond_rouvre_la_ligne_choisie(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    item = page.locator(".at-row[data-kind='task_blocked']").get_attribute("data-item")
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter?item={item}")
    assert page.locator(".at-row[aria-current='true']").get_attribute("data-item") == item


# ── Conformité à la maquette Douce (P63) ────────────────────────────────────


def test_p63_tuiles_de_comptes_reelles_et_formule_en_tete(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    assert page.locator(".space-title").inner_text() == "À traiter"
    assert page.locator(".space-title").evaluate("e => getComputedStyle(e).fontSize") == "32px"
    assert page.locator(".space-sub").inner_text().startswith("Ce qui attend une décision de toi")
    assert page.locator(".space-head .at-formula").inner_text().replace("\n", "").strip() == "›grimoire status"
    tiles = page.locator(".kpi .kpi-item")
    # Atelier Nord : 2 à décider (question, proposition), 1 à débloquer — les comptes de l'API.
    assert [tiles.nth(i).locator(".kpi-lbl").inner_text() for i in range(tiles.count())] == ["À décider", "À débloquer"]
    assert [tiles.nth(i).locator(".kpi-val").inner_text() for i in range(tiles.count())] == ["2", "1"]
    assert tiles.nth(0).locator(".kpi-sub").inner_text().startswith("la plus ancienne : ")


def test_p63_pastille_teintee_et_action_primaire_sur_la_ligne_choisie(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    blocked = _row(page, BLOCKED_TITLE)
    assert blocked.locator(".at-rico").get_attribute("data-tone") == "bad"
    assert blocked.locator(".at-rico").evaluate("e => getComputedStyle(e).width") == "32px"
    assert _row(page, QUESTION_TITLE).locator(".at-rico").get_attribute("data-tone") == "warn"
    assert _row(page, QUESTION_TITLE).locator(".at-act").get_attribute("class").split().count("pri") == 1
    assert "pri" not in blocked.locator(".at-act").get_attribute("class").split()
    blocked.click()
    assert "pri" in blocked.locator(".at-act").get_attribute("class").split()
    assert "pri" not in _row(page, QUESTION_TITLE).locator(".at-act").get_attribute("class").split()
    # Aucun emoji : l'icône est un symbole du sprite.
    assert blocked.locator(".at-rico use").get_attribute("href").startswith("icons.svg#")


def test_p63_detail_contexte_paires_action_existante_et_equivalent_cli(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    _row(page, BLOCKED_TITLE).click()
    detail = page.locator("#inspector-body .at-detail")
    assert detail.locator(".at-eyebrow").first.inner_text() == f"{HOME_NAME} · tâche bloquée"
    assert detail.locator("h3").inner_text() == BLOCKED_TITLE
    assert detail.locator(".at-lead").inner_text() == BLOCKED_REASON
    keys = detail.locator(".at-kv dt").all_inner_texts()
    assert "Tâche" in keys and "En attente" in keys
    # Une seule action, celle de la ligne : rien de nouveau ne s'écrit depuis l'interface.
    assert detail.locator(".at-actions a, .at-actions button").all_inner_texts() == ["Débloquer"]
    assert detail.locator(".at-equiv .at-formula").inner_text().replace("\n", "").strip() == (
        f"›grimoire task show {served_inbox['blocked']}"
    )


def test_p63_le_badge_de_l_onglet_est_la_somme_des_tuiles(page: Page, served_inbox: dict[str, str]) -> None:
    for suffix in (f"?project={served_inbox['home']}#a-traiter", "?scope=all#a-traiter"):
        _open(page, served_inbox, suffix)
        assert page.locator("#decisions-pill").count() == 0
        page.wait_for_function("() => !document.getElementById('decisions-count').hidden")
        badge = int(page.locator("#tab-space-a-traiter #decisions-count").inner_text())
        total = sum(int(v) for v in page.locator(".kpi .kpi-val").all_inner_texts())
        count = page.evaluate("async () => (await (await fetch('/api/workspace/attention' + (new URLSearchParams(location.search).get('scope') === 'all' ? '?scope=portfolio' : ''))).json()).count")
        assert badge == total == count > 0, (suffix, badge, total, count)


def test_p63_le_badge_suit_l_espace_travail_aussi(page: Page, served_inbox: dict[str, str]) -> None:
    _open(page, served_inbox, f"?project={served_inbox['home']}#a-traiter")
    page.locator("#tab-space-travail").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function("() => !document.getElementById('decisions-count').hidden")
    assert page.locator("#decisions-count").inner_text() == "3"


# ── Captures pour la relecture ──────────────────────────────────────────────


def test_captures_de_relecture(browser: Browser, served_inbox: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    shots = (
        ("p32-a-traiter-tous-clair", "?scope=all#a-traiter", "light", (1440, 900)),
        ("p32-a-traiter-tous-sombre", "?scope=all#a-traiter", "dark", (1440, 900)),
        ("p32-a-traiter-projet-clair", f"?project={served_inbox['home']}#a-traiter", "light", (1440, 900)),
        ("p32-a-traiter-mobile", "?scope=all#a-traiter", "light", (390, 844)),
        ("p32-a-traiter-vide", f"?project={served_inbox['calm']}#a-traiter", "light", (1440, 900)),
        ("p32-a-traiter-lecture-seule", f"?project={served_inbox['studio']}#a-traiter", "dark", (1440, 900)),
    )
    for name, suffix, scheme, (width, height) in shots:
        context = browser.new_context(
            viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce"
        )
        shot = context.new_page()
        try:
            _open(shot, served_inbox, suffix)
            # La barre d'état se replie sous 900 px : le réglage passe par le DOM.
            shot.evaluate("s => document.querySelector(`#st-theme [data-theme-pref='${s}']`).click()", scheme)
            shot.screenshot(path=str(out / f"{name}.png"), full_page=False)
        finally:
            context.close()
    assert len(list(out.glob("p32-a-traiter-*.png"))) == len(shots)


def test_p63_captures_de_relecture(browser: Browser, served_inbox: dict[str, str]) -> None:
    """Captures de la maquette Douce (clair 1440x900, clair 390 px, tous les projets et un projet)."""
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    shots = (
        ("p63-a-traiter-tous-clair", "?scope=all#a-traiter", (1440, 900)),
        ("p63-a-traiter-projet-clair", f"?project={served_inbox['home']}#a-traiter", (1440, 900)),
        ("p63-a-traiter-mobile", "?scope=all#a-traiter", (390, 844)),
    )
    for name, suffix, (width, height) in shots:
        context = browser.new_context(
            viewport={"width": width, "height": height}, color_scheme="light", reduced_motion="reduce"
        )
        shot = context.new_page()
        try:
            _open(shot, served_inbox, suffix)
            shot.evaluate("() => document.querySelector(`#st-theme [data-theme-pref='light']`).click()")
            shot.wait_for_function("() => !document.getElementById('decisions-count').hidden")
            shot.screenshot(path=str(out / f"{name}.png"), full_page=False)
        finally:
            context.close()
    assert len(list(out.glob("p63-a-traiter-*.png"))) == len(shots)
