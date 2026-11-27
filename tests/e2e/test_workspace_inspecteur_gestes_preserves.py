"""Un dessin de l'inspecteur ne balaie pas un geste que l'utilisateur vient de faire.

Deux courses, provoquées en retardant une réponse (``page.route``) :

- Exécuter : un dessin du pouls est déjà en vol quand l'utilisateur clique « Réaliser : bloquer » sans raison ;
  le refus s'écrit, puis le dessin arrive et remplace tout l'inspecteur. Le refus doit survivre.
- Mémoire : le détail d'une entrée se dessine deux fois (la ligne, puis ``memory/entry``) ; une confirmation
  « Supprimer définitivement » ouverte entre les deux doit survivre au second dessin, sans que le geste
  destructif parte seul.

Un cockpit sur un faux répertoire personnel, un projet, une tâche prête et une entrée mémoire.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route

from .conftest import _alive, _free_port, _wait_ready

SLUG = "forge-gestes"
TASK_TITLE = "Prête à bloquer, gestes préservés"

_SEED = r"""
import json, sys
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
print(mgr.store(sys.argv[1], metadata={"type": "decisions"}).id)
"""


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120, encoding="utf-8")


def _grimoire(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "grimoire", *args], cwd, env)


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("gestes") / "fake-home"
    root.mkdir(parents=True)
    forge = root / SLUG
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    forge.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(forge), check=False, capture_output=True)
    init = _grimoire(forge, env_init, "init", ".", "-y", "--name", SLUG, "--backend", "local")
    if not (forge / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    added = _grimoire(forge, env_init, "task", "add", TASK_TITLE, "-a", "un critère observable", "--owner", "winston")
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche : {added.stderr[-400:]}")
    task_id = match.group(1)
    moved = _grimoire(forge, env_init, "task", "move", task_id, "--to", "ready")
    if moved.returncode != 0:
        pytest.skip(f"`grimoire task move --to ready` refusé : {moved.stderr[-400:]}")

    cockpit_home = Path(env["GRIMOIRE_COCKPIT_HOME"])
    cockpit_home.mkdir(parents=True)
    registry = [{"name": SLUG, "path": str(forge.resolve()), "slug": SLUG}]
    (cockpit_home / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "forge": forge, "env": env_init, "task": task_id}
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
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    shot = context.new_page()
    errors: list[str] = []
    shot.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield shot
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"


def _open(page: Page, srv: dict[str, Any], suffix: str) -> None:
    page.goto(f"{srv['base']}/workspace/index.html{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


class _Held:
    """Les requêtes retenues par ``page.route`` ; ``release`` les laisse partir, et les suivantes aussi."""

    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.open = False

    def __call__(self, route: Route) -> None:
        if self.open:
            route.continue_()
        else:
            self.routes.append(route)

    def release(self) -> None:
        self.open = True
        for route in self.routes:
            route.continue_()


def _hold(page: Page, pattern: str) -> _Held:
    held = _Held()
    page.route(re.compile(pattern), held)
    return held


def _wait_held(page: Page, held: _Held) -> None:
    """Attend qu'une requête soit retenue, en laissant tourner la page image par image (pas de sommeil)."""
    deadline = time.monotonic() + 30
    while not held.routes:
        assert time.monotonic() < deadline, "la requête attendue n'est jamais partie"
        page.evaluate("() => new Promise((resolve) => requestAnimationFrame(resolve))")


def test_un_refus_de_realiser_survit_a_un_dessin_deja_en_vol(page: Page, served: dict[str, Any]) -> None:
    task_id = served["task"]
    _open(page, served, f"?project={SLUG}#travail")
    page.locator(f".ex-card[data-task='{task_id}']").click()
    page.locator("#inspector-body [data-role='block-reason']").wait_for(timeout=30_000)

    # Un dessin du pouls part (la priorité de la tâche change) ; sa lecture du détail est retenue en route.
    held = _hold(page, rf"/api/workspace/tasks/{task_id}(\?|$)")
    pri = _grimoire(served["forge"], served["env"], "task", "prioritize", task_id, "--to", "high")
    assert pri.returncode == 0, pri.stderr[-400:]
    _wait_held(page, held)

    # Pendant ce temps, l'utilisateur tente « Réaliser : bloquer » sans raison : refus.
    page.locator("#inspector-body button", has_text="Réaliser : bloquer").click()
    page.locator("#inspector-body .ex-refusal").wait_for(timeout=30_000)
    page.evaluate("() => { window.__feedback = document.querySelector(\"[data-role='transition-feedback']\"); }")

    # Le dessin en vol arrive et remplace l'inspecteur.
    held.release()
    page.wait_for_function(
        "() => { const f = document.querySelector(\"[data-role='transition-feedback']\");"
        " return f && f !== window.__feedback; }",
        timeout=30_000,
    )
    refusal = page.locator("#inspector-body .ex-refusal")
    assert refusal.count() == 1, "le refus a disparu avec le dessin : l'utilisateur ne sait pas pourquoi rien n'a bougé"
    assert "échec" in refusal.inner_text()

    # Un autre choix de tâche, ou un nouveau geste, l'efface : il ne colle pas.
    page.locator("#inspector-body [data-role='block-reason']").fill("raison posée")
    page.locator("#inspector-body button", has_text="Réaliser : bloquer").click()
    page.wait_for_function("() => !document.querySelector('#inspector-body .ex-refusal')", timeout=30_000)


def test_une_confirmation_de_suppression_survit_au_second_dessin_du_detail(page: Page, served: dict[str, Any]) -> None:
    word = "mot" + uuid.uuid4().hex[:10]
    seeded = _run([sys.executable, "-c", _SEED, f"Brouillon {word}, à supprimer."], served["forge"], served["env"])
    assert seeded.returncode == 0, seeded.stderr[-800:]
    entry_id = seeded.stdout.strip().splitlines()[-1]

    _open(page, served, f"?project={SLUG}#savoir/memoire")
    page.wait_for_selector(".kn-main .kn-summary:not(:empty)", timeout=30_000)
    field = page.locator(".kn-searchbar input[type='search']")
    field.fill(word)
    field.press("Enter")
    page.wait_for_function(
        "(t) => document.querySelector('.kn-searchbar .formula')?.textContent.includes(t)"
        " && document.querySelector('.kn-summary')?.textContent.includes('résultat')",
        arg=word, timeout=30_000,
    )

    # La lecture complète de l'entrée (second dessin) est retenue ; le premier dessin vient de la ligne.
    held = _hold(page, r"/api/workspace/memory/entry\?")
    page.locator(".kn-res", has_text=word).first.click()
    page.wait_for_function("(t) => document.querySelector('#inspector-body .kn-detail h2')?.textContent.includes(t)", arg=word)
    _wait_held(page, held)

    page.locator("#inspector-body .kn-actions button", has_text="Supprimer…").click()
    page.locator("#inspector-body .kn-confirm").wait_for()
    page.evaluate("() => { window.__detail = document.querySelector('#inspector-body .kn-detail'); }")

    held.release()
    page.wait_for_function(
        "() => { const d = document.querySelector('#inspector-body .kn-detail'); return d && d !== window.__detail; }",
        timeout=30_000,
    )
    assert page.locator("#inspector-body .kn-confirm").count() == 1, "le second dessin a balayé la confirmation ouverte"

    # Rien n'est parti seul : l'entrée existe, le journal ne porte aucune suppression.
    journal = served["forge"] / "_grimoire-output" / "memory-changes.jsonl"
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line] if journal.exists() else []
    assert not [r for r in rows if r.get("op") == "delete" and entry_id in {r.get("before_id"), r.get("after_id")}]

    # Et elle reste utilisable : Annuler la referme, la confirmation ne revient pas d'elle-même.
    page.locator("#inspector-body .kn-confirm button", has_text="Annuler").click()
    page.wait_for_function("() => !document.querySelector('#inspector-body .kn-confirm')")
    assert page.locator("#inspector-body .kn-actions button", has_text="Supprimer…").count() == 1
