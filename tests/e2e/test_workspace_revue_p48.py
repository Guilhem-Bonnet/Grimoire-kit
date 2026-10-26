"""PR à revoir (P48) : la ligne d'À traiter, puis l'écran de revue, dans un vrai navigateur.

Le cockpit sert « Atelier Revue », un dépôt git dont le remote ``origin``
pointe sur GitHub. Aucun appel réseau : un faux ``gh`` est placé en tête du
``PATH`` du cockpit ; il rend trois PR ouvertes (verte et liée à une tâche du
ledger par sa branche ``task/<id>``, en échec sur CodeQL, en cours) et un
brouillon, et journalise chacun de ses appels.

Ce que chaque test prouve :

- À traiter rend une section « PR à revoir », une ligne par PR ouverte avec
  l'état de sa CI et sa tâche liée (le brouillon n'y est pas) ;
- l'action d'une ligne mène à l'écran de revue, la PR déjà sélectionnée ;
- aucune fusion depuis le cockpit : le seul geste est un lien qui ouvre la PR
  sur GitHub dans un nouvel onglet, aucune écriture ne part, et ``gh`` n'est
  appelé qu'en lecture (``pr list``).

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test y écrit les captures
1440x900, clair et sombre, d'À traiter et de l'écran de revue ; sans la
variable il ne fait rien (il ne se skippe pas : le mode strict de la CI promeut
tout skip en échec).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page, Request

from .conftest import _alive, _free_port, _wait_ready
from .test_workspace_a_traiter_p32 import _grimoire, _init, _task

HOME_NAME = "Atelier Revue"
REPO = "exemple/atelier-revue"
GREEN_TITLE = "Lot vert lié à sa tâche"
FAILED_TITLE = "Lot en échec sur CodeQL"
PENDING_TITLE = "Lot dont la CI tourne"

#: Le faux ``gh`` : rend les PR du fichier voisin pour ``pr list``, refuse tout le reste, journalise chaque appel.
FAKE_GH = '''\
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
with (here / "calls.log").open("a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] != ["pr", "list"]:
    sys.stderr.write("faux gh : seule la lecture pr list est servie\\n")
    sys.exit(1)
sys.stdout.write((here / "prs.json").read_text(encoding="utf-8"))
'''


def _run(name: str, conclusion: str = "", status: str = "COMPLETED") -> dict[str, str]:
    return {"__typename": "CheckRun", "name": name, "workflowName": "CI", "status": status,
            "conclusion": conclusion, "detailsUrl": f"https://github.com/{REPO}/actions/runs/1"}


def _pr(number: int, title: str, branch: str, checks: list[dict[str, str]], *, draft: bool = False,
        files: tuple[str, ...] = ("src/atelier/module.py",)) -> dict[str, object]:
    return {
        "number": number, "title": title, "url": f"https://github.com/{REPO}/pull/{number}",
        "headRefName": branch, "baseRefName": "main", "isDraft": draft, "author": {"login": "agent-claude"},
        "additions": 40 + number, "deletions": number, "changedFiles": len(files), "body": "",
        "createdAt": "2026-10-20T10:00:00Z", "updatedAt": "2026-10-21T10:00:00Z",
        "statusCheckRollup": checks, "reviewDecision": "REVIEW_REQUIRED",
        "files": [{"path": p, "additions": 1, "deletions": 0} for p in files],
        "closingIssuesReferences": [{"number": 700 + number}],
    }


def _fake_gh(folder: Path, task_id: str) -> Path:
    folder.mkdir(parents=True)
    prs = [
        _pr(12, GREEN_TITLE, f"task/{task_id}",
            [_run("tests", "SUCCESS"), _run("lint", "SUCCESS"), _run("e2e", "SUCCESS"), _run("rust", "SKIPPED")],
            files=("src/atelier/module.py", "tests/test_module.py", "CHANGELOG.md")),
        _pr(15, FAILED_TITLE, "fix/parseur", [_run("tests", "SUCCESS"), _run("CodeQL", "FAILURE")]),
        _pr(20, PENDING_TITLE, "feat/export", [_run("tests", "SUCCESS"), _run("e2e", status="IN_PROGRESS")],
            files=("docs/export.md",)),
        _pr(21, "Brouillon pas encore prêt", "wip/idee", [], draft=True),
    ]
    (folder / "prs.json").write_text(json.dumps(prs, ensure_ascii=False), encoding="utf-8")
    (folder / "gh.py").write_text(FAKE_GH, encoding="utf-8")
    launcher = folder / "gh"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{folder / "gh.py"}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    (folder / "gh.cmd").write_text(f'@"{sys.executable}" "{folder / "gh.py"}" %*\r\n', encoding="utf-8")
    return folder


@pytest.fixture(scope="session")
def served_review(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("revue-p48")
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-revue")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["NO_COLOR"] = "1"

    home = base / HOME_NAME
    _init(home, HOME_NAME, env)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"],
                   cwd=str(home), check=False, capture_output=True)
    task_id = _task(home, "Livrer le lot vert", env)
    fake = _fake_gh(base / "faux-gh", task_id)
    _grimoire(base, "cockpit", "add", str(home), env=env)
    registry_path = cockpit_home / "registry.json"
    if not registry_path.is_file():
        pytest.skip("`grimoire cockpit add` n'a pas peuplé le registre")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    slug = next((str(e["slug"]) for e in registry if Path(e["path"]).name == HOME_NAME), "")
    if not slug:
        pytest.skip("le registre ne porte pas le projet")

    serve_env = dict(env)
    serve_env["PATH"] = str(fake) + os.pathsep + env.get("PATH", "")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--project-root", str(home),
         "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(home),
        env=serve_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "home": slug, "task": task_id, "calls": str(fake / "calls.log")}
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
    page.goto(f"{served['url']}/workspace/index.html?project={served['home']}{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'a-traiter'")


def _pr_row(page: Page, number: int):
    return page.locator(f".at-row[data-kind='pr_review'][data-item$='{REPO}#{number}']")


def _fragment(page: Page) -> tuple[str, dict[str, list[str]]]:
    path, _, query = urlparse(page.url).fragment.partition("?")
    return path, parse_qs(query)


def test_a_traiter_liste_les_pr_a_revoir_avec_ci_et_tache(page: Page, served_review: dict[str, str]) -> None:
    _open(page, served_review, "#a-traiter")
    page.wait_for_selector(".at-section[data-section='review'] .at-row", timeout=20_000)
    section = page.locator(".at-section[data-section='review']")
    assert section.locator(".at-section-head h3").inner_text() == "PR à revoir"
    numbers = sorted(int(n.split("#")[-1]) for n in section.locator(".at-row").evaluate_all(
        "rows => rows.map((r) => r.dataset.item)"))
    assert numbers == [12, 15, 20]  # le brouillon n'attend pas de revue

    green = _pr_row(page, 12)
    assert green.locator(".at-title").inner_text() == f"#12 {GREEN_TITLE}"
    assert green.locator(".at-reason").inner_text() == f"CI verte : 3 verts, 1 ignoré · tâche {served_review['task']}"
    assert green.locator(".at-act").inner_text() == "Revoir"
    assert _pr_row(page, 15).locator(".at-reason").inner_text().startswith("1 échec de CI : CodeQL")
    assert _pr_row(page, 20).locator(".at-reason").inner_text().startswith("CI en cours : 1 check sur 2")
    # Le détail de la ligne dit la CI et la tâche liée.
    green.click()
    detail = page.locator("#inspector-body .at-detail")
    assert served_review["task"] in detail.inner_text()
    assert "3 verts, 1 ignoré" in detail.inner_text()


def test_une_ligne_pr_a_revoir_mene_a_l_ecran_de_revue_la_pr_selectionnee(
    page: Page, served_review: dict[str, str]
) -> None:
    _open(page, served_review, "#a-traiter")
    page.wait_for_selector(".at-row[data-kind='pr_review']", timeout=20_000)
    _pr_row(page, 15).locator(".at-act").click()
    page.wait_for_selector(".rv-wrap .rv-pr[aria-current='true']", timeout=20_000)

    path, params = _fragment(page)
    assert path == "a-traiter"
    assert params["view"] == ["revue"] and params["pr"] == ["15"]
    selected = page.locator(".rv-pr[aria-current='true']")
    assert selected.get_attribute("data-pr") == "15"
    assert page.locator(".rv-main h1").inner_text() == FAILED_TITLE
    assert "PR #15 · fix/parseur" in page.locator(".rv-main .rv-eyebrow").first.inner_text()
    failed = page.locator(".rv-main .kpi-item[data-tone='bad']")
    assert failed.locator(".kpi-val").inner_text() == "1"
    assert "CodeQL" in page.locator(".rv-fails").inner_text()
    assert "échec" in page.locator(".rv-fails").inner_text()
    # Le lien du journal prend l'accent du thème, pas le bleu par défaut du navigateur.
    assert page.locator(".rv-fails .rv-link").evaluate(
        """a => {
            const probe = document.createElement('span');
            probe.style.color = 'var(--acc)';
            a.parentElement.append(probe);
            const same = getComputedStyle(probe).color === getComputedStyle(a).color;
            probe.remove();
            return same;
        }""")
    # Les plus prêtes d'abord : la verte avant l'échec.
    order = page.locator(".rv-pr").evaluate_all("rows => rows.map((r) => r.dataset.pr)")
    assert order.index("12") < order.index("15")
    assert "Trois questions" in page.locator("#inspector-body .rv-aside").inner_text()

    # Choisir une autre PR la pose dans l'URL, sans remontage.
    page.locator(".rv-pr[data-pr='12']").click()
    page.wait_for_function("() => document.querySelector('.rv-main h1')?.textContent.includes('Lot vert')")
    assert _fragment(page)[1]["pr"] == ["12"]
    assert served_review["task"] in page.locator(".rv-main .rv-eyebrow").first.inner_text()


def test_aucune_fusion_depuis_le_cockpit_le_bouton_ouvre_github(page: Page, served_review: dict[str, str]) -> None:
    writes: list[str] = []

    def _watch(request: Request) -> None:
        if request.method != "GET" and "/api/" in request.url:
            writes.append(f"{request.method} {request.url}")

    page.on("request", _watch)
    _open(page, served_review, "#a-traiter?view=revue&pr=12")
    page.wait_for_selector(".rv-pr[aria-current='true'][data-pr='12']", timeout=20_000)

    github = page.locator("a[data-action='open-github']")
    assert github.inner_text().strip() == "Ouvrir sur GitHub pour fusionner"
    assert github.get_attribute("href") == f"https://github.com/{REPO}/pull/12"
    assert github.get_attribute("target") == "_blank"
    assert "noopener" in (github.get_attribute("rel") or "")
    gestures = page.locator(".rv-main button, .rv-main a.btn").evaluate_all(
        "els => els.map((e) => e.textContent.trim())")
    assert not [g for g in gestures if "usionner" in g and g != "Ouvrir sur GitHub pour fusionner"]
    assert page.locator(".rv-main button").count() == 0
    task = page.locator("a[data-action='task']")
    assert task.get_attribute("href").endswith(f"#travail?task={served_review['task']}")

    assert writes == []
    calls = [json.loads(line) for line in Path(served_review["calls"]).read_text(encoding="utf-8").splitlines()]
    assert calls, "le faux gh n'a jamais été lu"
    assert all(call[:2] == ["pr", "list"] and "--state" in call and "open" in call for call in calls)


def test_captures_de_la_revue(browser: Browser, served_review: dict[str, str]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    for scheme in ("light", "dark"):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
        page = context.new_page()
        try:
            page.emulate_media(color_scheme=scheme)
            _open(page, served_review, "#a-traiter")
            page.wait_for_selector(".at-section[data-section='review'] .at-row", timeout=20_000)
            _pr_row(page, 12).click()
            page.screenshot(path=str(Path(out) / f"p48-a-traiter-{scheme}.png"))
            _open(page, served_review, "#a-traiter?view=revue&pr=15")
            page.wait_for_selector(".rv-pr[aria-current='true'][data-pr='15']", timeout=20_000)
            page.screenshot(path=str(Path(out) / f"p48-revue-{scheme}.png"))
        finally:
            context.close()
