"""Savoir › Carte 3D (P81) : tout le graphe, rendu sur le GPU, mesuré dans un vrai navigateur.

Un projet réel (``grimoire init``) porte un instantané de graphe de code de
22 000 nœuds et ~92 000 liens (``_grimoire-output/codegraph.json``), plus son
store de mémoire. Prouvé par navigateur :

- le nombre de nœuds rendus égale celui de la route : aucun échantillonnage ;
- premier rendu sous 3 s ; 30 images/s en rotation de caméra. Chromium
  headless n'a pas de GPU : WebGL passe par SwiftShader (logiciel). Le seuil
  d'images/s est donc ``MIN_FPS`` ci-dessous, documenté, et se resserre avec
  ``GRIMOIRE_CARTE_MIN_FPS`` sur un poste muni d'un GPU ;
- la disposition tourne dans un Web Worker, le fil principal ne bloque jamais
  plus de 100 ms (longtask) ;
- un clic sur un nœud ouvre l'entrée dans l'inspecteur ; la recherche d'un
  symbole centre la caméra dessus ;
- aucune ressource hors de 127.0.0.1 ; sans WebGL, un repli 2D lisible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from tests.unit.tools.test_workspace_graph import write_codegraph

from .conftest import _alive, _free_port, _wait_ready

NODES, EDGES = 22_000, 92_000
#: Seuil d'images/s en rotation sous WebGL logiciel (SwiftShader) : 30 est l'exigence
#: du produit ; sans GPU on mesure la même boucle avec la décimation de liens du
#: mouvement, et le seuil se règle par l'environnement.
MIN_FPS = float(os.environ.get("GRIMOIRE_CARTE_MIN_FPS", "30"))


@pytest.fixture(scope="module")
def served_carte(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("carte") / "projet-carte"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    created = subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-carte"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")
    shutil.rmtree(root / "_grimoire-runtime-output" / "ledger", ignore_errors=True)
    write_codegraph(root, NODES, EDGES)
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-carte"))
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


_LONGTASK = """
window.__longtasks = [];
try {
  new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__longtasks.push(e.duration); })
    .observe({ type: 'longtask', buffered: true });
} catch (e) { /* longtask indisponible */ }
"""

_NO_WEBGL = """
const real = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
  if (String(kind).startsWith('webgl') || kind === 'experimental-webgl') return null;
  return real.call(this, kind, ...rest);
};
"""


def _open(browser: Browser, base: str, init_script: str = _LONGTASK, scheme: str = "dark") -> tuple[Page, list[str]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme=scheme)
    context.add_init_script(init_script)
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(f"{base}/workspace/index.html#savoir/carte", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireCarte && window.GrimoireCarte.frames > 0", timeout=60_000)
    return page, requests


@pytest.fixture
def carte(browser: Browser, served_carte: str) -> Iterator[tuple[Page, list[str]]]:
    page, requests = _open(browser, served_carte)
    try:
        yield page, requests
    finally:
        page.context.close()


def test_le_graphe_complet_est_rendu_sans_echantillonnage(carte: tuple[Page, list[str]], served_carte: str) -> None:
    page, _ = carte
    route = page.evaluate("() => fetch('/api/workspace/memory/graph').then(r => r.json()).then(j => j.counts)")
    assert route["nodes"] >= 20_000 and route["edges"] >= 80_000
    assert page.evaluate("() => window.GrimoireCarte.mode") == "webgl"
    assert page.evaluate("() => window.GrimoireCarte.renderedNodes") == route["nodes"]
    assert page.evaluate("() => window.GrimoireCarte.nodes") == route["nodes"]
    stage = page.locator(".sc-stage")
    assert stage.get_attribute("data-nodes") == str(route["nodes"])
    assert stage.get_attribute("data-edges") == str(route["edges"])


def test_premier_rendu_sous_3_s_et_images_par_seconde_en_rotation(carte: tuple[Page, list[str]]) -> None:
    page, _ = carte
    first = page.evaluate("() => window.GrimoireCarte.firstRenderMs")
    assert first < 3000, f"premier rendu en {first:.0f} ms"
    page.wait_for_function("() => window.GrimoireCarte.workerDone", timeout=120_000)
    fps = page.evaluate(
        """() => new Promise((resolve) => {
          const c = window.GrimoireCarte; c.setAutoRotate(true);
          let frames = 0; const t0 = performance.now();
          const tick = (now) => {
            frames += 1;
            if (now - t0 >= 2000) { c.setAutoRotate(false); resolve(frames / ((now - t0) / 1000)); }
            else requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        })"""
    )
    assert fps >= MIN_FPS, f"{fps:.1f} images/s en rotation (seuil {MIN_FPS})"


def test_la_disposition_tourne_dans_un_worker_sans_bloquer_le_fil_principal(carte: tuple[Page, list[str]]) -> None:
    page, _ = carte
    page.wait_for_function("() => window.GrimoireCarte.workerTicks > 0", timeout=60_000)
    assert page.evaluate("() => window.GrimoireCarte.workerTicks") > 0, "le worker a rendu des positions"
    page.wait_for_function("() => window.GrimoireCarte.workerDone", timeout=120_000)
    longest = page.evaluate("() => Math.max(0, ...window.__longtasks)")
    assert longest <= 100, f"tâche longue de {longest:.0f} ms sur le fil principal"
    workers = [w for w in page.workers if w.url.endswith("carte-worker.js")]
    assert workers, "le calcul des forces vit dans un Web Worker"


def test_un_clic_ouvre_l_entree_et_la_recherche_centre_la_camera(carte: tuple[Page, list[str]]) -> None:
    page, _ = carte
    page.wait_for_function("() => window.GrimoireCarte.workerDone", timeout=120_000)
    index = page.evaluate("() => window.GrimoireCarte.find('sym1234')")
    assert index >= 0
    page.locator(".sc-bar input[type='search']").fill("sym1234")
    page.locator(".sc-bar input[type='search']").press("Enter")
    page.wait_for_function("() => !window.GrimoireCarte.flying", timeout=10_000)
    target = page.evaluate("() => window.GrimoireCarte.target")
    pos = page.evaluate("(i) => window.GrimoireCarte.position(i)", index)
    assert max(abs(a - b) for a, b in zip(target, pos, strict=True)) < 1.0, "la caméra est centrée sur le symbole"
    assert page.evaluate("() => window.GrimoireCarte.selected") == index
    detail = page.locator("#inspector-body .sc-detail")
    detail.wait_for()
    assert "sym1234" in detail.inner_text()

    # Clic réel : le nœud sélectionné est au centre de la toile ; un clic dessus le choisit (picking GPU).
    page.wait_for_function("() => window.GrimoireCarte.frames > 0")
    screen = page.evaluate("(i) => window.GrimoireCarte.screenOf(i)", index)
    box = page.locator(".sc-stage canvas").bounding_box()
    assert box is not None and screen is not None
    page.evaluate("() => { window.__sel = window.GrimoireCarte.selected; }")
    page.mouse.click(box["x"] + screen["x"], box["y"] + screen["y"])
    page.wait_for_function("(i) => window.GrimoireCarte.selected === i", arg=index, timeout=10_000)
    assert "sym1234" in page.locator("#inspector-body .sc-detail").inner_text()


def test_aucune_ressource_hors_de_127_0_0_1(carte: tuple[Page, list[str]]) -> None:
    page, requests = carte
    page.wait_for_function("() => window.GrimoireCarte.workerDone", timeout=120_000)
    foreign = [u for u in requests if not u.startswith(("http://127.0.0.1:", "data:", "blob:"))]
    assert foreign == []


def test_sans_webgl_un_repli_2d_lisible(browser: Browser, served_carte: str) -> None:
    page, requests = _open(browser, served_carte, init_script=_NO_WEBGL, scheme="light")
    try:
        assert page.evaluate("() => window.GrimoireCarte.mode") == "2d"
        assert page.locator(".sc-stage").get_attribute("data-mode") == "2d"
        assert "repli 2D" in page.locator("[data-note='fallback']").inner_text()
        assert page.evaluate("() => window.GrimoireCarte.renderedNodes") == page.evaluate("() => window.GrimoireCarte.nodes")
        # Lisible : le canvas 2D porte des pixels non vides (autre chose que le fond).
        painted = page.evaluate(
            """() => { const c = document.querySelector('.sc-stage canvas'); const d = c.getContext('2d')
              .getImageData(0, 0, c.width, c.height).data; const first = d.slice(0, 4).join();
              let diff = 0; for (let i = 0; i < d.length; i += 4) { if (d.slice(i, i + 4).join() !== first) diff += 1; }
              return diff; }"""
        )
        assert painted > 2000
        assert [u for u in requests if not u.startswith(("http://127.0.0.1:", "data:", "blob:"))] == []
    finally:
        page.context.close()
