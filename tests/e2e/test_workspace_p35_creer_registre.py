"""P35 : créer une tâche depuis Travail et entretenir le registre depuis Configuration.

Un cockpit sert un projet initialisé pour de vrai, sous un HOME jetable : son
registre ne touche jamais celui du poste ni ``real_project``. Le registre porte
en plus un voisin (lecture seule), un projet à retirer et une entrée morte ;
un dossier à ajouter et un dossier à scanner attendent à côté.

Prouvé par navigateur, puis relu sur disque :

- « Nouvelle tâche » envoie titre, critères et description à ``TaskService.add``
  (la tâche est relue dans le ledger du projet servi) ; la confirmation
  s'affiche sur place avec un lien qui ouvre la tâche ; un formulaire
  incomplet est refusé sur place ;
- en portée « Tous les projets » et sur un autre projet, le bouton est
  désactivé et la raison est écrite ;
- ajouter, scanner puis ajouter, retirer (aperçu puis confirmation dans la
  page) et purger (aperçu puis confirmation) changent le registre, jamais le
  dossier d'un projet ; le projet servi ne se retire pas.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les captures 1440x900 (clair et sombre)
sont écrites ; sans la variable ``_capture`` ne fait rien.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

APPEAR_MS = 30_000

_TASKS = r"""
import json
from pathlib import Path
from grimoire.missions.service import TaskService
ledger = Path.cwd() / "_grimoire-runtime-output" / "ledger"
tasks = TaskService(Path.cwd()).ledger.list_tasks() if ledger.is_dir() else []
print(json.dumps([{"id": t.id, "title": t.title, "acceptance": list(t.acceptance), "description": t.description} for t in tasks]))
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


@pytest.fixture(scope="module")
def served_p35(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("p35") / "fake-home"
    root.mkdir(parents=True)
    forge, other = root / "forge-p35", root / "voisin-p35"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    for project, name in ((forge, "forge-p35"), (other, "voisin-p35")):
        init = _run([sys.executable, "-m", "grimoire", "init", str(project), "-y", "--name", name, "--backend", "local"],
                    root, env_init)
        if not (project / "_grimoire" / "kit").is_dir():
            pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    # Des dossiers jetables, chacun un dépôt (marqueur `.git`) : à retirer, à ajouter, à scanner.
    removable, addable, scanned = root / "a-retirer", root / "a-ajouter", root / "lot" / "a-scanner"
    for folder in (removable, addable, scanned):
        (folder / ".git").mkdir(parents=True)
    registry = [
        {"name": "forge-p35", "path": str(forge.resolve()), "slug": "forge-p35"},
        {"name": "voisin-p35", "path": str(other.resolve()), "slug": "voisin-p35"},
        {"name": "a-retirer", "path": str(removable.resolve()), "slug": "a-retirer"},
        {"name": "disparu", "path": str(root / "disparu"), "slug": "disparu"},
    ]
    cockpit_home = Path(env["GRIMOIRE_COCKPIT_HOME"])
    cockpit_home.mkdir(parents=True)
    (cockpit_home / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {
            "base": f"http://127.0.0.1:{port}", "root": root, "forge": forge, "other": other, "env": env_init,
            "registry": cockpit_home / "registry.json", "removable": removable, "addable": addable,
            "scanned": scanned,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def page_p35(browser: Browser, served_p35: dict[str, Any]) -> Iterator[tuple[Page, dict[str, Any]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield page, served_p35
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"


def _set_theme(page: Page, theme: str) -> None:
    page.evaluate("(t) => document.querySelector(`[data-theme-pref=\"${t}\"]`).click()", theme)
    page.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)


_CONTRAST_JS = """
(selector) => {
  const parse = (value) => {
    const n = value.match(/[\\d.]+/g).map(Number);
    return [n[0] / 255, n[1] / 255, n[2] / 255, n.length > 3 ? n[3] : 1];
  };
  const lum = ([r, g, b]) => {
    const f = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const backdrop = (el) => {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0.9) return bg;
    }
    return parse(getComputedStyle(document.body).backgroundColor);
  };
  const out = [];
  for (const scope of document.querySelectorAll(selector)) {
    for (const el of scope.querySelectorAll('*')) {
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      if (el.closest('button:disabled, input:disabled')) continue;
      const text = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim())
        .map((n) => n.textContent.trim()).join('');
      if (!text) continue;
      const a = lum(parse(style.color)), b = lum(backdrop(el));
      out.push({ text: text.slice(0, 40), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) });
    }
  }
  return out;
}
"""


def _low_contrast(page: Page, selector: str) -> list[dict[str, Any]]:
    page.evaluate("() => document.fonts.ready")
    return [m for m in page.evaluate(_CONTRAST_JS, selector) if m["ratio"] < 4.5]


def _tasks(srv: dict[str, Any], project: str = "forge") -> list[dict[str, Any]]:
    done = _run([sys.executable, "-c", _TASKS], srv[project], srv["env"])
    assert done.returncode == 0, done.stderr[-800:]
    return list(json.loads(done.stdout.strip().splitlines()[-1]))


def _registry(srv: dict[str, Any]) -> list[str]:
    return [str(e.get("slug")) for e in json.loads(srv["registry"].read_text(encoding="utf-8"))]


def _open(page: Page, srv: dict[str, Any], query: str, hash_: str) -> None:
    page.goto(f"{srv['base']}/workspace/index.html{query}#{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    page.wait_for_selector("#canvas[aria-busy='false']", timeout=APPEAR_MS)


# ── Travail › Nouvelle tâche ───────────────────────────────────────────────


def test_nouvelle_tache_appelle_task_service_et_confirme_sur_place(page_p35: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p35
    before = {t["id"] for t in _tasks(srv)}
    _open(page, srv, "?project=forge-p35", "travail")
    button = page.locator("[data-role='new-task']")
    button.wait_for(timeout=APPEAR_MS)
    assert button.is_enabled()
    button.click()
    form = page.locator("[data-role='new-task-form']")
    form.wait_for(timeout=APPEAR_MS)

    # Incomplet : refus écrit sur place, rien d'écrit.
    form.locator("[data-role='new-task-title']").fill("Ouvrir une tâche depuis le cockpit")
    form.locator("[data-role='new-task-submit']").click()
    page.wait_for_function(
        "() => (document.querySelector(\"[data-role='new-task-form'] .action-status\")?.textContent || '').includes('critère')"
    )
    assert {t["id"] for t in _tasks(srv)} == before

    form.locator("[data-role='new-task-acceptance']").fill("la tâche existe dans le ledger\nelle porte sa description")
    # Corriger efface le refus : il ne doit plus prétendre qu'un critère manque.
    assert form.locator(".action-status").inner_text().strip() == ""
    form.locator("[data-role='new-task-description']").fill("Créée par le test e2e de P35.")
    assert _low_contrast(page, "[data-role='new-task-form']") == []
    _capture(page, "p35-nouvelle-tache-formulaire-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, "[data-role='new-task-form']") == []
    _capture(page, "p35-nouvelle-tache-formulaire-sombre")
    _set_theme(page, "light")

    form.locator("[data-role='new-task-submit']").click()
    done = page.locator("[data-role='new-task-done']")
    done.wait_for(timeout=APPEAR_MS)
    page.wait_for_function(
        "() => (document.querySelector(\"[data-role='new-task-done'] .action-status\")?.textContent || '').includes('créée')"
    )
    created = [t for t in _tasks(srv) if t["id"] not in before]
    assert len(created) == 1
    task = created[0]
    assert task["title"] == "Ouvrir une tâche depuis le cockpit"
    assert task["acceptance"] == ["la tâche existe dans le ledger", "elle porte sa description"]
    assert task["description"] == "Créée par le test e2e de P35."
    assert task["id"] in done.inner_text()
    assert page.locator("[data-role='new-task-form']").count() == 0
    # La carte est sur le tableau.
    page.locator(f".ex-card[data-task='{task['id']}']").wait_for(timeout=APPEAR_MS)
    assert _low_contrast(page, "[data-role='new-task-done']") == []
    _capture(page, "p35-nouvelle-tache-confirmation-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, "[data-role='new-task-done']") == []
    _capture(page, "p35-nouvelle-tache-confirmation-sombre")

    # Le lien ouvre la tâche dans l'inspecteur.
    done.locator("[data-role='open-created']").click()
    page.wait_for_function(
        "(id) => (document.getElementById('inspector-body')?.textContent || '').includes(id)", arg=task["id"],
    )
    done.get_by_role("button", name="Fermer").click()
    page.wait_for_function("() => !document.querySelector(\"[data-role='new-task-done']\")")


def test_nouvelle_tache_desactivee_hors_du_projet_servi(page_p35: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p35
    _open(page, srv, "?scope=all", "travail")
    button = page.locator("[data-role='new-task']")
    button.wait_for(timeout=APPEAR_MS)
    assert button.is_disabled()
    assert "Choisis un projet" in page.locator("[data-role='new-task-reason']").inner_text()

    _open(page, srv, "?project=voisin-p35", "travail")
    button = page.locator("[data-role='new-task']")
    button.wait_for(timeout=APPEAR_MS)
    assert button.is_disabled()
    assert "Lecture seule" in page.locator("[data-role='new-task-reason']").inner_text()
    assert page.locator("[data-role='new-task-form']").count() == 0


# ── Configuration › Projets ────────────────────────────────────────────────


def _notice(page: Page, words: str) -> None:
    page.wait_for_function(
        "(w) => (document.querySelector(\"[data-role='registry-notice']\")?.textContent || '').includes(w)", arg=words,
    )


def test_entretenir_le_registre_ajouter_scanner_retirer_purger(page_p35: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p35
    _open(page, srv, "?project=forge-p35", "configuration/projets")
    table = page.locator("table[aria-label='Projets du registre']")
    table.wait_for(timeout=APPEAR_MS)

    # Le projet servi ne se retire pas d'ici.
    assert page.locator("tr[data-slug='forge-p35'] [data-role='registry-remove']").is_disabled()

    # Ajouter un projet existant.
    page.locator("[data-role='registry-open-add']").click()
    page.locator("[data-role='registry-add-path']").fill(str(srv["addable"]))
    page.locator("[data-role='registry-add-submit']").click()
    _notice(page, "ajouté au registre")
    assert "a-ajouter" in _registry(srv)
    page.locator("tr[data-slug='a-ajouter']").wait_for(timeout=APPEAR_MS)

    # Scanner un dossier, puis ajouter le candidat trouvé.
    page.locator("[data-role='registry-open-scan']").click()
    page.locator("[data-role='registry-scan-root']").fill(str(srv["root"] / "lot"))
    page.locator("[data-role='registry-scan-submit']").click()
    candidate = page.locator(f"[data-role='registry-scan-results'] li[data-path='{srv['scanned'].resolve()}']")
    candidate.wait_for(timeout=APPEAR_MS)
    assert "a-scanner" not in _registry(srv)
    candidate.locator("[data-role='registry-scan-add']").click()
    page.wait_for_function(
        "() => (document.querySelector(\"[data-role='registry-scan'] .action-status\")?.textContent || '').includes('ajouté')"
    )
    page.locator("tr[data-slug='a-scanner']").wait_for(timeout=APPEAR_MS)
    assert "a-scanner" in _registry(srv)

    # Retirer : aperçu dans la page, puis confirmation.
    page.locator("tr[data-slug='a-retirer'] [data-role='registry-remove']").click()
    confirm = page.locator("[data-role='registry-remove-confirm'][data-slug='a-retirer']")
    confirm.wait_for(timeout=APPEAR_MS)
    page.wait_for_function(
        "() => (document.querySelector(\"[data-role='registry-remove-confirm'] .action-status\")?.textContent || '')"
        ".includes('reste sur le disque')"
    )
    assert "a-retirer" in _registry(srv)
    assert _low_contrast(page, "[data-role='registry-remove-confirm']") == []
    _capture(page, "p35-registre-retirer-confirmation-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, "[data-role='registry-remove-confirm']") == []
    _capture(page, "p35-registre-retirer-confirmation-sombre")
    _set_theme(page, "light")
    confirm.locator("[data-role='registry-remove-yes']").click()
    _notice(page, "retiré du registre")
    assert "a-retirer" not in _registry(srv)
    assert (srv["removable"] / ".git").is_dir(), "le dossier du projet a été touché"

    # Purger : l'aperçu liste l'entrée morte, la confirmation la retire.
    page.locator("[data-role='registry-open-prune']").click()
    prune = page.locator("[data-role='registry-prune']")
    prune.locator("[data-role='registry-prune-list'] li[data-slug='disparu']").wait_for(timeout=APPEAR_MS)
    assert "disparu" in _registry(srv)
    _capture(page, "p35-registre-purger-apercu-clair")
    _set_theme(page, "dark")
    assert _low_contrast(page, "[data-role='registry-prune']") == []
    _capture(page, "p35-registre-purger-apercu-sombre")
    _set_theme(page, "light")
    prune.locator("[data-role='registry-prune-confirm']").click()
    _notice(page, "1 entrée retirée")
    assert _registry(srv) == ["forge-p35", "voisin-p35", "a-ajouter", "a-scanner"]
    assert srv["forge"].is_dir() and srv["other"].is_dir()
