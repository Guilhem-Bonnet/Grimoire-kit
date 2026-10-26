"""« Depuis ta dernière visite » (P53), dans un vrai navigateur, sur un vrai cockpit.

Le projet servi porte quatre faits réels, écrits par la CLI : une tâche livrée
(relue puis clôturée), une clôture refusée par le gate (aucun reçu de revue),
une tâche bloquée et une question posée par ``task ask``.

Ce que chaque test prouve (critères de GAO-p53-r-sum-d-001) :

- sans visite précédente, aucun résumé n'est affiché ni demandé au serveur,
  et l'ouverture devient la visite ;
- avec une visite enregistrée, le résumé liste ce qui a été livré, refusé ou
  laissé en attente, chaque ligne liée à sa tâche dans Travail ;
- un rechargement ne l'efface pas avant lecture ; « Refermer » le marque vu ;
- quitter À traiter vers un autre écran le marque vu aussi.

La visite est écrite par le navigateur (``localStorage``) : les tests la
posent au passé fixe 2020, jamais en comparant deux horloges.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test y écrit les captures
1440x900 clair et sombre ; sans la variable il ne fait rien (il ne se skippe
pas : le mode strict de la CI promeut tout skip en échec).
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

import pytest
from playwright.sync_api import Browser, Page

from .conftest import _alive, _free_port, _wait_ready

HOME_NAME = "Atelier Veille"
DELIVERED = "Livrer l'export CSV"
REFUSED = "Relire le module de paie"
BLOCKED = "Brancher la CI du lot C"
BLOCKED_REASON = "la CI est rouge sur main"
QUESTION = "Choisir le format d'archive"
QUESTION_TEXT = "Zip ou tar pour l'archive ?"
PAST_VISIT = "2020-01-01T00:00:00.000Z"


def _grimoire(cwd: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False, timeout=180,
    )


def _must(folder: Path, env: dict[str, str], *args: str) -> str:
    outcome = _grimoire(folder, *args, env=env)
    if outcome.returncode != 0:
        pytest.skip(f"préparation interrompue à {args} : {outcome.stderr[-400:]}")
    return outcome.stdout + outcome.stderr


def _running(folder: Path, title: str, env: dict[str, str]) -> str:
    added = _must(folder, env, "task", "add", title, "-a", "un critère observable", "--owner", "winston")
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added)
    if not match:
        pytest.skip("`grimoire task add` n'a pas ouvert de tâche")
    task_id = match.group(1)
    _must(folder, env, "task", "move", task_id, "--to", "ready")
    _must(folder, env, "task", "claim", task_id)
    _must(folder, env, "task", "move", task_id, "--to", "running")
    return task_id


@pytest.fixture(scope="session")
def served_visit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("depuis-visite-p53")
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-p53")
    env = dict(os.environ)
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID"):
        env.pop(var, None)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"

    home = base / HOME_NAME
    home.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(home), check=False, capture_output=True)
    _grimoire(home, "init", ".", "-y", "--name", HOME_NAME, "--backend", "local", env=env)
    if not (home / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")

    delivered = _running(home, DELIVERED, env)
    _must(home, env, "task", "move", delivered, "--to", "needs_verification")
    _must(home, env, "task", "review", delivered, "--verdict", "READY")
    _must(home, env, "task", "move", delivered, "--to", "closed")
    refused = _running(home, REFUSED, env)
    _must(home, env, "task", "move", refused, "--to", "needs_verification")
    if _grimoire(home, "task", "move", refused, "--to", "closed", env=env).returncode == 0:
        pytest.skip("le gate n'a pas refusé la clôture sans reçu de revue")
    blocked = _running(home, BLOCKED, env)
    _must(home, env, "task", "block", blocked, "--reason", BLOCKED_REASON)
    question = _running(home, QUESTION, env)
    _must(home, env, "task", "ask", question, QUESTION_TEXT, "-o", "Zip", "-o", "tar")

    _grimoire(base, "cockpit", "add", str(home), env=env)
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    slugs = {Path(e["path"]).name: str(e["slug"]) for e in json.loads(registry_path.read_text(encoding="utf-8"))}
    if HOME_NAME not in slugs:
        pytest.skip("le registre ne porte pas le projet")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--project-root", str(home),
         "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(home), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {
            "url": f"http://127.0.0.1:{port}", "home": slugs[HOME_NAME], "delivered": delivered,
            "refused": refused, "blocked": blocked, "question": question,
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
    _settled(page)


def _settled(page: Page) -> None:
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")
    # Le résumé se décide après le premier rendu : il pose toujours son état.
    page.wait_for_function("() => { const s = document.querySelector('section.ats'); "
                           "return s && s.dataset.state && s.dataset.state !== 'loading'; }")


def _visits(page: Page) -> dict[str, str]:
    """Les visites enregistrées par la coque : `{ clé localStorage + champ: instant }`."""
    return page.evaluate("""() => {
      const out = {};
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (!key.startsWith('grimoire.workspace.')) continue;
        let state = {};
        try { state = JSON.parse(localStorage.getItem(key) || '{}'); } catch { continue; }
        if (!state || typeof state !== 'object') continue;
        for (const [field, value] of Object.entries(state)) {
          if (field.startsWith('visit:')) out[`${key}|${field}`] = value;
        }
      }
      return out;
    }""")


def _set_past_visit(page: Page) -> None:
    """Remplace chaque visite enregistrée par une visite fixe de 2020."""
    page.evaluate("""(past) => {
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (!key.startsWith('grimoire.workspace.')) continue;
        let state;
        try { state = JSON.parse(localStorage.getItem(key) || '{}'); } catch { continue; }
        if (!state || typeof state !== 'object') continue;
        let touched = false;
        for (const field of Object.keys(state)) {
          if (field.startsWith('visit:')) { state[field] = past; touched = true; }
        }
        if (touched) localStorage.setItem(key, JSON.stringify(state));
      }
    }""", PAST_VISIT)


def _summary_after_past_visit(page: Page, served: dict[str, str], suffix: str) -> None:
    _open(page, served, suffix)
    assert _visits(page), "la première ouverture devait enregistrer la visite"
    _set_past_visit(page)
    page.reload(wait_until="domcontentloaded")
    _settled(page)


def _line(page: Page, kind: str, task_id: str):
    return page.locator(f"section.ats .ats-line[data-kind='{kind}'][data-task='{task_id}']")


# ── Sans visite précédente ──────────────────────────────────────────────────


def test_sans_visite_precedente_aucun_resume(page: Page, served_visit: dict[str, str]) -> None:
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "attention/since" in request.url else None)

    _open(page, served_visit, f"?project={served_visit['home']}#a-traiter")

    summary = page.locator("section.ats")
    assert summary.get_attribute("data-state") == "none"
    assert summary.is_hidden()
    assert page.locator(".ats-line").count() == 0
    assert asked == []
    # L'ouverture devient la visite : un instant ISO, en UTC.
    visits = list(_visits(page).values())
    assert visits and all(v.endswith("Z") for v in visits)


# ── Avec une visite enregistrée ─────────────────────────────────────────────


def test_le_resume_liste_livre_refuse_en_attente_chaque_ligne_liee_a_sa_tache(
    page: Page, served_visit: dict[str, str]
) -> None:
    home = served_visit["home"]
    _summary_after_past_visit(page, served_visit, f"?project={home}#a-traiter")

    summary = page.locator("section.ats")
    assert summary.get_attribute("data-state") == "shown"
    assert summary.is_visible()
    assert summary.locator("h3").inner_text() == "Depuis ta dernière visite"
    for kind, task_id, title in (
        ("delivered", served_visit["delivered"], DELIVERED),
        ("refused", served_visit["refused"], REFUSED),
        ("waiting", served_visit["blocked"], BLOCKED),
        ("waiting", served_visit["question"], QUESTION),
    ):
        line = _line(page, kind, task_id)
        assert line.count() == 1, f"{kind} {task_id} absent du résumé"
        link = line.locator("a.ats-link")
        assert link.inner_text() == title
        assert link.get_attribute("href").endswith(f"#travail?task={task_id}")
    assert "clôture refusée" in _line(page, "refused", served_visit["refused"]).inner_text()
    assert BLOCKED_REASON in _line(page, "waiting", served_visit["blocked"]).inner_text()
    assert QUESTION_TEXT in _line(page, "waiting", served_visit["question"]).inner_text()
    # Les comptes en badges, chacun avec son mot.
    badges = summary.locator(".ats-counts .badge").all_inner_texts()
    assert any(b.endswith("livré") for b in badges) and any(b.endswith("refusé") for b in badges)

    # La ligne ouvre Travail sur sa tâche.
    _line(page, "delivered", served_visit["delivered"]).locator("a.ats-link").click()
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    page.wait_for_function(f"() => location.hash.includes('task={served_visit['delivered']}')")


def test_un_rechargement_garde_le_resume_et_refermer_le_marque_vu(page: Page, served_visit: dict[str, str]) -> None:
    _summary_after_past_visit(page, served_visit, f"?project={served_visit['home']}#a-traiter")
    assert page.locator("section.ats").get_attribute("data-state") == "shown"

    page.reload(wait_until="domcontentloaded")
    _settled(page)
    assert page.locator("section.ats").get_attribute("data-state") == "shown"

    page.locator("section.ats [data-action='since-close']").click()
    assert page.locator("section.ats").is_hidden()
    assert PAST_VISIT not in _visits(page).values()

    page.reload(wait_until="domcontentloaded")
    _settled(page)
    assert page.locator("section.ats").get_attribute("data-state") in ("empty", "none")
    assert page.locator(".ats-line").count() == 0


def test_quitter_a_traiter_marque_le_resume_vu(page: Page, served_visit: dict[str, str]) -> None:
    _summary_after_past_visit(page, served_visit, f"?project={served_visit['home']}#a-traiter")
    assert page.locator("section.ats").get_attribute("data-state") == "shown"

    page.evaluate("() => window.GrimoireWorkspace.goto('travail')")
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'travail'")
    assert PAST_VISIT not in _visits(page).values()


def test_tous_les_projets_nomme_le_projet_de_chaque_ligne(page: Page, served_visit: dict[str, str]) -> None:
    _summary_after_past_visit(page, served_visit, "?scope=all#a-traiter")

    line = _line(page, "delivered", served_visit["delivered"])
    assert line.count() == 1
    assert HOME_NAME in line.locator(".ats-sub").inner_text()
    href = line.locator("a.ats-link").get_attribute("href")
    assert href.startswith("?scope=all#travail?") and f"task={served_visit['delivered']}" in href
    assert f"of={served_visit['home']}" in href


# ── Captures pour la relecture ──────────────────────────────────────────────


def test_captures_de_relecture(browser: Browser, served_visit: dict[str, str]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for scheme in ("light", "dark"):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme,
                                      reduced_motion="reduce")
        shot = context.new_page()
        try:
            shot.emulate_media(color_scheme=scheme)
            _summary_after_past_visit(shot, served_visit, f"?project={served_visit['home']}#a-traiter")
            shot.evaluate("s => document.querySelector(`#st-theme [data-theme-pref='${s}']`).click()", scheme)
            shot.screenshot(path=str(out / f"p53-depuis-visite-{'clair' if scheme == 'light' else 'sombre'}.png"))
        finally:
            context.close()
    assert len(list(out.glob("p53-depuis-visite-*.png"))) == 2
