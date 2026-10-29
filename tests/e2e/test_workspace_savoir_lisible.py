"""Savoir › Mémoire : décisions avant projections, code en police fixe (GAO-i6-rendre-s-001).

Le cockpit sert un projet initialisé pour de vrai, avec une décision et deux
projections de code semées dans son store. Prouvé par navigateur :

- au premier chargement, aucune entrée « Code » ; le compteur « +N projections »
  les ramène et se met à jour ;
- une entrée de code ouverte montre un <pre> en police monospace qui garde les
  retours à la ligne et l'indentation de la source, dans une région focalisable
  et nommée ;
- axe-core ne relève rien sur ce bloc, en clair et en sombre ; à 390 px le bloc
  défile dans son cadre sans défilement de page.

Les captures (clair, sombre, 390 px) vont dans ``GRIMOIRE_SHOTS_DIR`` si défini.
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

_SOURCE = (
    "def charger(chemin: str) -> dict:\n"
    "    with open(chemin) as fh:\n"
    "        return {\n"
    "            'ok': True,\n"
    "            'ligne_tres_longue': 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',\n"
    "        }"
)

_SEED = r"""
import json, sys
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
for item in json.loads(sys.argv[1]):
    mgr.store(item["text"], metadata=item["meta"])
"""


def _code(name: str, source: str) -> str:
    return (
        f"Code symbol: {name}\nFile: src/demo/config.py\nModule: demo.config\nKind: function\n"
        f"Lines: 10-15\nTest: no\nPublic: yes\nDocstring:\nCharge la configuration.\nSource excerpt:\n{source}"
    )


_ITEMS = [
    {"text": _code("charger", _SOURCE), "meta": {"projection_group": "code", "source_kind": "projection", "file_path": "src/demo/config.py"}},
    {"text": _code("sauver", "def sauver(x):\n    return x"), "meta": {"projection_group": "code", "source_kind": "projection", "file_path": "src/demo/config.py"}},
    {"text": "Décision : épingler Qdrant en 1.19.1, une mineure à la fois.", "meta": {"type": "decisions"}},
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")


def _wait_ready(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire cockpit serve` n'a pas répondu sur :{port}")


@pytest.fixture(scope="module")
def served() -> Iterator[dict[str, Any]]:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="savoir-lisible-")) / "fake-home"
    root.mkdir(parents=True)
    project = root / "lisible"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    init = _run([sys.executable, "-m", "grimoire", "init", str(project), "-y", "--name", "lisible", "--backend", "local"], root, env_init)
    if not (project / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    _run([sys.executable, "-m", "grimoire", "cockpit", "add", str(project)], root, env_init)
    seeded = _run([sys.executable, "-c", _SEED, json.dumps(_ITEMS)], project, env_init)
    assert seeded.returncode == 0, seeded.stderr[-800:]
    registry = json.loads((Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json").read_text(encoding="utf-8"))
    slugs = {Path(str(e.get("path", ""))).name: str(e.get("slug", "")) for e in registry}
    if not slugs.get("lisible"):
        pytest.skip("slug introuvable au registre du cockpit après `add`")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "slug": slugs["lisible"]}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _open(browser: Browser, srv: dict[str, Any], *, scheme: str = "light", width: int = 1440, height: int = 900) -> Page:
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)
    page = context.new_page()
    page.goto(f"{srv['base']}/workspace/index.html?project={srv['slug']}#savoir/memoire", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")
    page.wait_for_function("() => { const t = document.querySelector('.kn-summary')?.textContent || ''; return t && !t.startsWith('Lecture'); }", timeout=30_000)
    return page


def _show_code(page: Page, *, drawer: bool = False) -> None:
    page.locator(".kn-proj-more").click()
    page.wait_for_selector(".kn-res[data-type='code']", timeout=30_000)
    page.locator(".kn-res[data-type='code']", has_text="charger").first.click()
    if drawer:  # en étroit, l'inspecteur est un tiroir que le rail ouvre
        page.locator(".rail-btn[data-panel='inspector']").click()
    page.wait_for_selector("#inspector-body .kn-code pre", state="visible", timeout=30_000)


def _shot(page: Page, name: str) -> None:
    out = os.environ.get("GRIMOIRE_SHOTS_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(out) / f"savoir-lisible-{name}.png"))


def test_projections_masquees(browser: Browser, served: dict[str, Any]) -> None:
    page = _open(browser, served)
    try:
        assert page.locator(".kn-res[data-type='code']").count() == 0, "aucune entrée Code au premier chargement"
        assert page.locator(".kn-res[data-type='decision']").count() == 1
        first = page.locator(".kn-res").first
        assert first.get_attribute("data-type") == "decision", "les décisions passent en tête"
        assert page.locator("#inspector-body .kn-detail").get_attribute("data-entry"), "la première décision est ouverte"
        more = page.locator(".kn-proj-more")
        assert more.inner_text() == "+2 projections"
        assert not page.get_by_label("Code (projection)").is_checked()
        page.get_by_label("Code (projection)").check()
        page.wait_for_selector(".kn-res[data-type='code']", timeout=30_000)
        assert page.locator(".kn-res[data-type='code']").count() == 2
        assert page.locator(".kn-proj-more").is_hidden(), "le compteur se met à jour une fois les codes cochés"
        title = page.locator(".kn-res[data-type='code'] .kn-title").first.inner_text()
        assert not title.startswith("Code symbol"), "le titre n'a plus le préfixe"
    finally:
        page.context.close()


def test_code_monospace(browser: Browser, served: dict[str, Any]) -> None:
    page = _open(browser, served)
    try:
        _show_code(page)
        pre = page.locator("#inspector-body .kn-code pre")
        assert "mono" in pre.evaluate("el => getComputedStyle(el).fontFamily").lower() or pre.evaluate(
            "el => { const f = getComputedStyle(el).fontFamily.toLowerCase(); return f.includes('mono') || f.includes('monospace'); }")
        assert pre.evaluate("el => el.textContent") == _SOURCE, "retours à la ligne et indentation conservés"
        # Chromium récent décompose `white-space` (white-space-collapse + text-wrap-mode) : le raccourci peut se lire vide
        # alors que le rendu est bien « pre » (CI du 2026-10-29). On vérifie les longhands quand ils existent.
        ws = pre.evaluate(
            "el => { const s = getComputedStyle(el); return [s.whiteSpace, s.whiteSpaceCollapse || '', s.textWrapMode || ''] }"
        )
        assert ws[0] == "pre" or (ws[1] == "preserve" and ws[2] == "nowrap"), ws
        assert pre.get_attribute("tabindex") == "0"
        assert pre.get_attribute("role") == "region"
        assert (pre.get_attribute("aria-label") or "").strip()
        assert page.locator("#inspector-body h2").inner_text() == "charger"
        assert page.locator("#inspector-body .kn-code button", has_text="Copier").count() == 1
    finally:
        page.context.close()


def _axe_path() -> Path | None:
    candidates = [os.environ.get("GRIMOIRE_AXE_JS", "")]
    candidates += [str(p) for p in Path.home().glob(".bun/install/cache/axe-core@*/axe.min.js")]
    candidates += [str(p) for p in Path.home().glob(".bun/install/cache/axe-core@*/axe.js")]
    candidates += [str(p) for p in Path.home().glob(".nvm/versions/node/*/lib/node_modules/**/axe-core/axe.min.js")]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_bloc_de_code_accessible(browser: Browser, served: dict[str, Any], scheme: str) -> None:
    axe = _axe_path()
    if axe is None:
        pytest.skip("axe-core introuvable : renseigne GRIMOIRE_AXE_JS")
    page = _open(browser, served, scheme=scheme)
    try:
        _show_code(page)
        page.add_script_tag(path=str(axe))
        result = page.evaluate(
            "async () => (await axe.run('#inspector-body .kn-detail', {rules: {'color-contrast': {enabled: true}}}))"
            ".violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | '))")
        assert result == [], result
        _shot(page, scheme)
    finally:
        page.context.close()


def test_mobile_le_bloc_defile_dans_son_cadre(browser: Browser, served: dict[str, Any]) -> None:
    page = _open(browser, served, width=390, height=844)
    try:
        _show_code(page, drawer=True)
        pre = page.locator("#inspector-body .kn-code pre")
        pre.scroll_into_view_if_needed()
        assert pre.evaluate("el => el.scrollWidth > el.clientWidth"), "la ligne longue défile dans le bloc"
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), "pas de défilement de page"
        _shot(page, "390")
    finally:
        page.context.close()
