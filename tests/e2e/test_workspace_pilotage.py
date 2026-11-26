"""Pilotage de l'orchestrateur dans le cockpit : mettre en pause, voir l'état, reprendre ; disjoncteur ; vue globale.

Un cockpit dédié sert deux projets enrôlés (deux dossiers git, sans kit : le pilotage ne lit que des fichiers). Les gestes
partent par les vraies routes avec la clé de contrôle lue dans le HOME de test ; l'état affiché est relu de l'API.

Captures : ``GRIMOIRE_E2E_CAPTURES=<dossier>`` les écrit (1440x900 et 1024x768) ; absent, rien n'est capturé.
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

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker, trip
from grimoire.missions.pause import DRAIN, SCOPE_GLOBAL, global_pause_path, project_pause_path, read_flag, write_pause

from .conftest import open_scope_all

SIZES = {"1440x900": (1440, 900), "1024x768": (1024, 768)}
PANEL = "[data-panel='pilotage']"
FLEET = "[data-panel='pilotage-flotte']"


def _key() -> str:
    md.ensure_control_key()
    return md.control_key_path().read_text(encoding="utf-8").strip()


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


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    original = page.viewport_size
    for label, (width, height) in SIZES.items():
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(150)
        page.screenshot(path=str(folder / f"{name}-{label}.png"), full_page=False)
    if original:
        page.set_viewport_size(original)


@pytest.fixture(scope="module")
def served_pilotage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    roots = {
        "alpha": tmp_path_factory.mktemp("pilotage-a") / "projet-alpha",
        "beta": tmp_path_factory.mktemp("pilotage-b") / "projet-beta",
    }
    for root in roots.values():
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    cockpit_home = tmp_path_factory.mktemp("cockpit-home-pilotage")
    registry = [
        {"name": root.name, "slug": root.name, "path": str(root.resolve())} for root in roots.values()
    ]
    (cockpit_home / "registry.json").write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(cockpit_home)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    env["NO_COLOR"] = "1"
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "roots": {k: v.resolve() for k, v in roots.items()}}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def clean(served_pilotage: dict[str, object]) -> Iterator[dict[str, Path]]:
    """Aucune pause ni disjoncteur avant, et rien de laissé après : les projets sont partagés par le module."""
    roots: dict[str, Path] = served_pilotage["roots"]  # type: ignore[assignment]

    def wipe() -> None:
        from grimoire.missions.breaker import reset

        for root in roots.values():
            project_pause_path(root).unlink(missing_ok=True)
            if read_breaker(root).open:
                reset(root)
        machine = global_pause_path()
        if machine is not None:
            machine.unlink(missing_ok=True)

    wipe()
    yield roots
    wipe()


@pytest.fixture
def page_alpha(browser: Browser, served_pilotage: dict[str, object]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{served_pilotage['url']}/workspace/index.html?project=projet-alpha", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _open_sheet(page: Page) -> None:
    page.evaluate("() => window.GrimoireWorkspace.goto('piloter')")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'piloter'")
    page.wait_for_selector(PANEL)


def _confirm(page: Page, key: str, reason: str = "") -> None:
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_be_visible()
    if reason:
        dialog.locator("input[type='text']").fill(reason)
    dialog.locator("input[type='password']").fill(key)
    dialog.locator("button[type='submit']").click()


def test_mettre_en_pause_afficher_l_etat_puis_reprendre(page_alpha: Page, clean: dict[str, Path]) -> None:
    page = page_alpha
    root = clean["alpha"]
    _open_sheet(page)
    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("Actif")
    _capture(page, "01-fiche-pilotage-actif")

    page.locator(f"{PANEL} button[data-gesture='drain']").click()
    _capture(page, "02-dialogue-mettre-en-pause")
    _confirm(page, _key(), "je relis la PR")

    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("En pause")
    expect(page.locator(f"{PANEL} .pi-meta")).to_contain_text("posée par le cockpit")
    expect(page.locator(f"{PANEL} .pi-meta")).to_contain_text("motif : je relis la PR")
    flag = read_flag(project_pause_path(root), "project")
    assert flag is not None and (flag.mode, flag.actor, flag.origin) == (DRAIN, "cockpit", "human")
    assert read_flag(project_pause_path(clean["beta"]), "project") is None
    _capture(page, "03-fiche-pilotage-en-pause")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    _open_sheet(page)
    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("En pause")  # l'état vient de l'API, pas du clic

    page.locator(f"{PANEL} button[data-gesture='resume']").click()
    _confirm(page, _key())
    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("Actif")
    assert not project_pause_path(root).exists()


def test_une_cle_fausse_est_refusee_dans_le_dialogue_et_rien_n_est_ecrit(page_alpha: Page, clean: dict[str, Path]) -> None:
    page = page_alpha
    _open_sheet(page)
    page.locator(f"{PANEL} button[data-gesture='hold']").click()
    _confirm(page, "pas-la-cle")

    expect(page.locator("dialog.pi-dialog .pi-error")).to_contain_text("Clé refusée")
    assert not project_pause_path(clean["alpha"]).exists()
    page.locator("dialog.pi-dialog button.ghost").click()
    expect(page.locator("dialog.pi-dialog")).to_have_count(0)
    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("Actif")


def test_une_pause_de_regle_d_usage_s_affiche_avec_son_auteur_et_se_leve_en_le_disant(
    page_alpha: Page, clean: dict[str, Path]
) -> None:
    page = page_alpha
    root = clean["alpha"]
    write_pause(root, mode=DRAIN, actor="usage-rule:day:gros", reason="consommation du jour au seuil")
    _open_sheet(page)

    expect(page.locator(f"{PANEL} .pi-meta")).to_contain_text("posée par la règle d’usage day:gros".replace("’", "'"))
    expect(page.locator(f"{PANEL} .pi-meta")).to_contain_text("consommation du jour au seuil")
    page.locator(f"{PANEL} button[data-gesture='resume']").click()
    expect(page.locator("dialog.pi-dialog")).to_contain_text("règle d’usage day:gros".replace("’", "'"))
    _capture(page, "04-reprendre-pause-d-une-regle")
    _confirm(page, _key())

    note = page.locator(f"{PANEL} .pi-note")
    expect(note).to_contain_text("Pause posée par la règle d'usage day:gros levée")
    assert not project_pause_path(root).exists()

    # Un pouls arrive après le geste : une pause posée ailleurs fait repeindre tout le bloc, la note doit y survivre.
    write_pause(root, mode=DRAIN, actor="guilhem", reason="posée ailleurs")
    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("En pause", timeout=20_000)
    expect(note).to_contain_text("Pause posée par la règle d'usage day:gros levée")
    expect(note).to_be_visible()


def test_un_disjoncteur_ouvert_affiche_son_motif_et_se_referme(page_alpha: Page, clean: dict[str, Path]) -> None:
    page = page_alpha
    root = clean["alpha"]
    trip(root, "3 échecs consécutifs, dernier : binaire absent")
    _open_sheet(page)

    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("Disjoncteur ouvert : 3 échecs consécutifs")
    page.locator(f"{PANEL} button[data-gesture='reset']").click()
    expect(page.locator("dialog.pi-dialog")).to_contain_text("binaire absent")
    _capture(page, "05-refermer-le-disjoncteur")
    _confirm(page, _key())

    expect(page.locator(f"{PANEL} .pi-line")).to_contain_text("Actif")
    assert read_breaker(root).open is False


def test_la_vue_globale_pause_un_projet_puis_la_machine_et_reprend(page_alpha: Page, clean: dict[str, Path]) -> None:
    page = page_alpha
    open_scope_all(page, "#flotte")
    page.wait_for_selector(FLEET)
    rows = page.locator(f"{FLEET} .pi-row")
    expect(rows).to_have_count(2)
    _capture(page, "06-vue-globale-pilotage")

    beta = page.locator(f"{FLEET} .pi-row[data-project='projet-beta']")
    beta.locator("button[data-gesture='drain']").click()
    _confirm(page, _key(), "maintenance")
    expect(beta.locator(".pi-row-state")).to_contain_text("En pause")
    assert read_flag(project_pause_path(clean["beta"]), "project") is not None
    assert not project_pause_path(clean["alpha"]).exists()
    beta.locator("button[data-gesture='resume']").click()
    _confirm(page, _key())
    expect(beta.locator(".pi-row-state")).to_contain_text("Actif")

    page.locator(f"{FLEET} .pi-machine button").click()
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_contain_text("projet-alpha")
    expect(dialog).to_contain_text("projet-beta")
    _capture(page, "07-confirmation-pause-de-la-machine")
    _confirm(page, _key(), "coupure réseau")
    expect(page.locator(f"{FLEET} .pi-machine")).to_contain_text("Toute la machine est en pause")
    flag = read_flag(global_pause_path(), SCOPE_GLOBAL)  # type: ignore[arg-type]
    assert flag is not None and flag.actor == "cockpit" and flag.reason == "coupure réseau"
    for state in page.locator(f"{FLEET} .pi-row-state").all_inner_texts():
        assert "toute la machine" in state
    _capture(page, "08-vue-globale-machine-en-pause")

    page.locator(f"{FLEET} .pi-machine button").click()
    _confirm(page, _key())
    expect(page.locator(f"{FLEET} .pi-machine")).to_contain_text("n’est pas en pause")
    assert not global_pause_path().exists()  # type: ignore[union-attr]
