"""Le chrome vitrine ne propose ni la démo ni l'installation à qui est déjà dans l'atelier.

``grimoire-mode.js`` force ``index.html`` et ``portfolio.html`` en habillage
vitrine, même servies par ``grimoire serve``. Jusqu'ici ``forge-nav.js`` y
rendait alors la nav publique telle quelle : un lien « DÉMO » vers la vitrine
marketing, et un bouton « LANCER L'ATELIER → pip install grimoire-kit » — à
quelqu'un qui vient de lancer l'atelier. L'origine est une dimension à part du
mode ; ce test la fait varier et lit ce que la nav rend vraiment.

Le rendu est exécuté par Node sur un DOM minimal : assez pour que le script
tourne et écrive ``innerHTML``, rien de plus.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

_HARNESS = r"""
const fs = require('fs');
// Avec `node -e`, argv[1] est déjà le premier argument : on lit depuis la fin.
const [origin, modePath, navPath] = process.argv.slice(-3);
const local = origin === 'local';
const html = {};
const noop = () => {};
const el = (id) => ({ id, set innerHTML(v) { html[id] = v; }, get innerHTML() { return html[id] || ''; },
                      addEventListener: noop, removeEventListener: noop, querySelectorAll() { return []; },
                      querySelector() { return null; }, remove: noop, appendChild: noop, setAttribute: noop,
                      getAttribute() { return null; }, getBoundingClientRect() { return { top: 0, height: 0 }; },
                      classList: { add: noop, remove: noop, toggle: noop, contains() { return false; } },
                      style: {}, dataset: {}, closest() { return null; }, textContent: '' });
const doc = {
  documentElement: { classList: { add() {}, contains() { return false; } } },
  getElementById: (id) => (id === 'forge-nav' || id === 'forge-footer' ? el(id) : null),
  createElement: () => ({ set textContent(v) {}, style: {}, classList: { add() {} }, appendChild() {} }),
  head: { appendChild() {} }, body: { appendChild() {}, classList: { add() {} } },
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
  readyState: 'complete',
};
global.document = doc;
global.window = global;
global.addEventListener = noop; global.removeEventListener = noop;
global.scrollY = 0; global.scrollTo = noop; global.innerWidth = 1280; global.innerHeight = 800;
global.getComputedStyle = () => ({ getPropertyValue() { return ''; } });
global.location = local
  ? { protocol: 'http:', hostname: '127.0.0.1', pathname: '/portfolio.html', search: '' }
  : { protocol: 'https:', hostname: 'guilhem-bonnet.github.io', pathname: '/Grimoire-kit/portfolio.html', search: '' };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {});
global.requestAnimationFrame = (f) => f();
global.IntersectionObserver = class { observe() {} disconnect() {} };
global.MutationObserver = class { observe() {} disconnect() {} };
global.matchMedia = () => ({ matches: false, addEventListener() {} });
global.history = { pushState() {}, replaceState() {} };
eval(fs.readFileSync(modePath, 'utf8'));
// Le chrome est écrit en tête de script ; ce qui suit (scroll, transitions,
// easter egg) touche des API que ce DOM minimal n'a pas. On lit ce qui a été
// rendu, et on signale sur stderr si le script n'est pas allé au bout.
try { eval(fs.readFileSync(navPath, 'utf8')); }
catch (e) { process.stderr.write('après le rendu du chrome : ' + e.message + '\n'); }
process.stdout.write(JSON.stringify(html));
"""


def _render(origin: str) -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node absent : le rendu du chrome ne peut pas être exécuté")
    proc = subprocess.run(
        [node, "-e", _HARNESS, origin, str(WEB / "grimoire-mode.js"), str(WEB / "forge-nav.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, f"node a échoué ({proc.returncode}) :\n{proc.stderr}"
    html = json.loads(proc.stdout)
    assert "forge-nav" in html and "forge-footer" in html, proc.stderr
    return html


def test_published_site_keeps_demo_and_install_cta() -> None:
    html = _render("published")
    nav, footer = html["forge-nav"], html["forge-footer"]
    assert "demo.html" in nav and "DÉMO" in nav
    assert "pip install grimoire-kit" in nav
    assert "demo.html" in footer


def test_local_atelier_hides_demo_and_install_cta() -> None:
    html = _render("local")
    nav, footer = html["forge-nav"], html["forge-footer"]
    assert "demo.html" not in nav
    assert "pip install grimoire-kit" not in nav
    assert "demo.html" not in footer
    # Le passage vers l'atelier reste offert : un lien, pas une invitation à installer.
    assert 'href="atelier.html"' in nav


def test_origin_is_exposed_separately_from_mode() -> None:
    src = (WEB / "grimoire-mode.js").read_text(encoding="utf-8")
    assert "window.GrimoireLocal" in src
