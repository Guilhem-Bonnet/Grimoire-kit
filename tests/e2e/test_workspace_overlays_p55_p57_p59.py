"""Finitions d'interaction du cockpit, lot B : P55, P57, P59.

Un vrai cockpit sur un faux répertoire personnel : « forge-lotb » (le projet
servi, deux tâches prêtes, une mémoire locale), « voisin-lotb » et
« calme-lotb » (des entrées que « Ajouter » rend telles quelles), « Projet
renommé » (un nom choisi à la main) et « disparu » (dossier absent). Chaque
test crée ou choisit SES objets (tâche, entrée mémoire, projet) : aucun ne
dépend de la position d'un élément ni de l'état laissé par un autre.

- P55 : la palette ⌘K et l'aide sont des ``<dialog>`` modaux, les menus « … »
  des ``popover="auto"``, les infobulles des ``popover="manual"`` ; Échap ferme
  et le focus revient au déclencheur — par le navigateur, plus par shell.js.
  Sans Popover API, le menu s'ouvre et se ferme encore au bouton.
- P57 : chaque geste réversible (bloquer une tâche prête, retirer une entrée
  mémoire, retirer un projet du registre ou de la Flotte) part tout de suite et
  montre un toast « Annuler » de 10 s qui rétablit l'état par la route inverse ;
  le délai se met en pause au survol et au focus, et sa fin se déclenche ici à
  la demande (``GrimoireWorkspace.toast.expire()``) — aucun test n'attend 10 s.
  Un geste irréversible n'a jamais de toast et passe par une confirmation.
- P59 : à 1440, 1024 et 768 px, le détail se pose à côté de la liste quand la
  place le permet et dessous sinon, les tuiles se replient, aucun texte ne
  déborde de son panneau.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les captures de
relecture (clair et sombre) ; sans la variable il ne fait rien.
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
from playwright.sync_api import Browser, Page

from .conftest import _alive, _free_port, _wait_ready

READY_UNDO = "Brancher le banc du lot B"
READY_EXPIRE = "Relire la maquette du lot B"
READY_SHOTS = "Poser les captures du lot B"
CUSTOM_NAME = "Projet renommé"

_SEED = r"""
import json, sys
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
ids = {}
for item in json.loads(sys.argv[1]):
    entry = mgr.store(item["text"], metadata={"type": "decisions"})
    ids[item["key"]] = entry.id
print(json.dumps(ids))
"""


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")


def _grimoire(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "grimoire", *args], cwd, env)


def _ready_task(folder: Path, env: dict[str, str], title: str) -> str:
    added = _grimoire(folder, env, "task", "add", title, "-a", "un critère observable", "--owner", "winston")
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(f"`grimoire task add` n'a pas ouvert de tâche : {added.stderr[-400:]}")
    moved = _grimoire(folder, env, "task", "move", match.group(1), "--to", "ready")
    if moved.returncode != 0:
        pytest.skip(f"`grimoire task move --to ready` refusé : {moved.stderr[-400:]}")
    return match.group(1)


@pytest.fixture(scope="module")
def served_lotb(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("lotb") / "fake-home"
    root.mkdir(parents=True)
    forge, voisin = root / "forge-lotb", root / "voisin-lotb"
    calme, renamed = root / "calme-lotb", root / "dossier-renomme"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    for project, name in ((forge, "forge-lotb"), (voisin, "voisin-lotb")):
        project.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(project), check=False, capture_output=True)
        init = _grimoire(project, env_init, "init", ".", "-y", "--name", name, "--backend", "local")
        if not (project / "_grimoire" / "kit").is_dir():
            pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
    for folder in (calme, renamed):
        (folder / ".git").mkdir(parents=True)
    tasks = {title: _ready_task(forge, env_init, title) for title in (READY_UNDO, READY_EXPIRE, READY_SHOTS)}
    registry = [
        {"name": "forge-lotb", "path": str(forge.resolve()), "slug": "forge-lotb"},
        {"name": "voisin-lotb", "path": str(voisin.resolve()), "slug": "voisin-lotb"},
        {"name": "calme-lotb", "path": str(calme.resolve()), "slug": "calme-lotb"},
        {"name": CUSTOM_NAME, "path": str(renamed.resolve()), "slug": "projet-renomme"},
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
            "base": f"http://127.0.0.1:{port}", "forge": forge, "env": env_init, "tasks": tasks,
            "registry": cockpit_home / "registry.json",
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
def page(browser: Browser, served_lotb: dict[str, Any]) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    shot = context.new_page()
    errors: list[str] = []
    dialogs: list[str] = []
    shot.on("pageerror", lambda exc: errors.append(str(exc)))
    shot.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    try:
        yield shot
    finally:
        context.close()
    assert errors == [], f"erreurs de page : {errors}"
    assert dialogs == [], f"aucune boîte native attendue : {dialogs}"


def _open(page: Page, srv: dict[str, Any], suffix: str) -> None:
    page.goto(f"{srv['base']}/workspace/index.html{suffix}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => document.querySelector('#canvas').getAttribute('aria-busy') === 'false'")


def _travail(page: Page, srv: dict[str, Any]) -> None:
    _open(page, srv, "?project=forge-lotb#travail")
    page.wait_for_function("() => document.querySelectorAll('.ex-card').length >= 2", timeout=30_000)


def _select_task(page: Page, srv: dict[str, Any], title: str) -> str:
    task_id = srv["tasks"][title]
    page.locator(f".ex-card[data-task='{task_id}']").click()
    page.wait_for_function("(id) => document.querySelector('#inspector-body')?.innerText.includes(id)", arg=task_id)
    return task_id


def _status(srv: dict[str, Any], task_id: str) -> str:
    from grimoire.missions.service import TaskService

    return str(TaskService(srv["forge"]).require(task_id).status.value)


def _registry(srv: dict[str, Any]) -> list[str]:
    return [str(e.get("slug", "")) for e in json.loads(Path(srv["registry"]).read_text(encoding="utf-8"))]


def _toast_says(page: Page, words: str) -> None:
    page.wait_for_function(
        "(w) => (document.querySelector('#toast .toast-msg')?.textContent || '').includes(w)", arg=words, timeout=30_000,
    )


def _undo_and_wait(page: Page, words: str) -> None:
    page.locator("#toast [data-role='toast-undo']").click()
    page.wait_for_function(
        "(w) => { const c = document.querySelector('#toast .toast-card');"
        " return c && c.dataset.phase === 'done' && c.dataset.tone === 'ok' && c.textContent.includes(w); }",
        arg=words, timeout=30_000,
    )


def _block(page: Page, reason: str) -> None:
    page.locator("#inspector-body [data-role='block-reason']").fill(reason)
    page.locator("#inspector-body button", has_text="Réaliser : bloquer").click()


def _seed(srv: dict[str, Any], text: str) -> str:
    seeded = _run([sys.executable, "-c", _SEED, json.dumps([{"key": "e", "text": text}])], srv["forge"], srv["env"])
    assert seeded.returncode == 0, seeded.stderr[-800:]
    return str(json.loads(seeded.stdout.strip().splitlines()[-1])["e"])


def _journal_ops(srv: dict[str, Any], entry_id: str) -> list[str]:
    path = srv["forge"] / "_grimoire-output" / "memory-changes.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [str(r.get("op")) for r in rows if entry_id in {r.get("before_id"), r.get("after_id")}]


def _open_entry(page: Page, srv: dict[str, Any], word: str) -> None:
    _open(page, srv, "?project=forge-lotb#savoir/memoire")
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")
    page.wait_for_selector(".kn-main .kn-summary:not(:empty)", timeout=30_000)
    field = page.locator(".kn-searchbar input[type='search']")
    field.fill(word)
    field.press("Enter")
    page.wait_for_function(
        "(t) => document.querySelector('.kn-searchbar .formula')?.textContent.includes(t)"
        " && document.querySelector('.kn-summary')?.textContent.includes('résultat')",
        arg=word, timeout=30_000,
    )
    page.locator(".kn-res", has_text=word).first.click()
    page.wait_for_function("(t) => document.querySelector('#inspector-body .kn-detail h2')?.textContent.includes(t)", arg=word)


def _retract(page: Page, reason: str) -> None:
    page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
    panel = page.locator("#inspector-body .kn-gesture")
    panel.locator(".seg button", has_text="Retirer").click()
    panel.locator("button", has_text="Voir l’aperçu").click()
    panel.locator(".kn-diff").wait_for(timeout=30_000)
    panel.locator("input[name='reason']").fill(reason)
    panel.locator("button", has_text="Appliquer").click()


# ── P55 : palette et aide en <dialog>, menus et infobulles en popover ──────


def test_p55_la_palette_est_un_dialog_modal_qui_rend_le_focus(page: Page, served_lotb: dict[str, Any]) -> None:
    _travail(page, served_lotb)
    assert page.evaluate("() => document.getElementById('palette').tagName") == "DIALOG"
    page.locator("#palette-open").focus()
    page.keyboard.press("Control+k")
    assert page.evaluate("() => document.getElementById('palette').matches('dialog:modal')") is True
    assert page.evaluate("() => document.activeElement.id") == "palette-input"
    page.keyboard.press("Escape")
    assert page.evaluate("() => window.GrimoireWorkspace.paletteOpen") is False
    assert page.evaluate("() => document.activeElement.id") == "palette-open"

    # Un clic sur le fond (hors de la boîte) la ferme, le focus revient au bouton.
    page.locator("#palette-open").click()
    page.wait_for_selector("#palette[open]")
    page.mouse.click(12, 880)
    page.wait_for_selector("#palette", state="hidden")
    assert page.evaluate("() => document.activeElement.id") == "palette-open"

    # Le sélecteur de portée est le même <dialog>, ancré sous sa puce.
    page.locator("#project-chip").click()
    page.wait_for_selector("#palette[open][data-mode='scope']")
    page.keyboard.press("Escape")
    page.wait_for_selector("#palette", state="hidden")
    assert page.evaluate("() => document.activeElement.id") == "project-chip"


def test_p55_les_menus_sont_des_popovers_qui_rendent_le_focus(page: Page, served_lotb: dict[str, Any]) -> None:
    _travail(page, served_lotb)
    cards = page.locator(".ex-card")
    first, second = cards.nth(0), cards.nth(1)
    toggle = first.locator("[data-role='row-more']")
    panel = first.locator("[data-role='row-more-panel']")
    assert panel.get_attribute("popover") == "auto"
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert panel.evaluate("(n) => n.matches(':popover-open')") is True
    assert toggle.get_attribute("aria-expanded") == "true"
    # Échap depuis une action du menu : fermé, focus rendu au bouton « … ».
    panel.locator(".more-item").first.focus()
    page.keyboard.press("Escape")
    assert panel.evaluate("(n) => n.matches(':popover-open')") is False
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("() => document.activeElement.dataset.role") == "row-more"
    assert first.evaluate("(card, a) => card.contains(a)", page.evaluate_handle("() => document.activeElement")) is True

    # Un seul menu ouvert : ouvrir le second ferme le premier.
    toggle.click()
    second.hover()
    second.locator("[data-role='row-more']").click()
    assert panel.evaluate("(n) => n.matches(':popover-open')") is False
    assert second.locator("[data-role='row-more-panel']").evaluate("(n) => n.matches(':popover-open')") is True
    # Un clic à côté le referme.
    page.mouse.click(700, 120)
    assert second.locator("[data-role='row-more-panel']").evaluate("(n) => n.matches(':popover-open')") is False

    # Le menu de l'inspecteur est le même composant.
    _select_task(page, served_lotb, READY_UNDO)
    more = page.locator("#inspector-body button[data-role='more']")
    more.click()
    inspector_panel = page.locator("#inspector-body [data-role='more-panel']")
    assert inspector_panel.get_attribute("popover") == "auto"
    page.wait_for_function("() => document.activeElement?.dataset.role === 'cancel-reason'")
    page.keyboard.press("Escape")
    assert inspector_panel.evaluate("(n) => n.matches(':popover-open')") is False
    assert page.evaluate("() => document.activeElement.dataset.role") == "more"


def test_p55_les_infobulles_sont_des_popovers_et_echap_les_ferme(page: Page, served_lotb: dict[str, Any]) -> None:
    _open(page, served_lotb, "?scope=all#flotte")
    anchor = page.locator(".pl-fleet th[data-term='projet']")
    anchor.wait_for(timeout=30_000)
    anchor.focus()
    tip = page.locator(".tip[data-term='projet']")
    tip.wait_for()
    assert tip.get_attribute("popover") == "manual"
    assert tip.get_attribute("role") == "tooltip"
    assert tip.evaluate("(n) => n.matches(':popover-open')") is True
    # La bulle ne prend pas le focus : il reste sur le terme.
    assert page.evaluate("() => document.activeElement.dataset.term") == "projet"
    page.keyboard.press("Escape")
    assert page.locator(".tip").count() == 0
    assert page.evaluate("() => document.activeElement.dataset.term") == "projet"


def test_p55_sans_popover_api_le_menu_s_ouvre_et_se_ferme_au_bouton(browser: Browser, served_lotb: dict[str, Any]) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script("delete HTMLElement.prototype.showPopover; delete HTMLElement.prototype.hidePopover;")
    try:
        shot = context.new_page()
        _travail(shot, served_lotb)
        card = shot.locator(".ex-card").first
        panel = card.locator("[data-role='row-more-panel']")
        assert panel.get_attribute("popover") is None
        assert panel.is_hidden()
        card.locator("[data-role='row-more']").click()
        assert panel.is_visible()
        card.locator("[data-role='row-more']").click()
        assert panel.is_hidden()
    finally:
        context.close()


# ── P57 : « Annuler » plutôt que confirmer ──────────────────────────────────


def test_p57_bloquer_une_tache_prete_s_annule_et_la_rend_prete(page: Page, served_lotb: dict[str, Any]) -> None:
    _travail(page, served_lotb)
    task_id = _select_task(page, served_lotb, READY_UNDO)
    assert _status(served_lotb, task_id) == "ready"
    _block(page, "attend la clé du banc")
    _toast_says(page, f"{task_id} : Prête → Bloquée")
    region = page.locator("#toast")
    assert region.get_attribute("role") == "status" and region.get_attribute("aria-live") == "polite"
    card = region.locator(".toast-card")
    assert card.get_attribute("data-duration") == "10000"
    assert page.evaluate("() => window.GrimoireWorkspace.toast.duration") == 10_000
    assert card.get_by_role("button", name="Annuler").is_visible()
    assert _status(served_lotb, task_id) == "blocked"

    _undo_and_wait(page, "revenue en Prête")
    assert _status(served_lotb, task_id) == "ready"


def test_p57_le_delai_se_met_en_pause_au_survol_et_au_focus_puis_expire(page: Page, served_lotb: dict[str, Any]) -> None:
    _travail(page, served_lotb)
    task_id = _select_task(page, served_lotb, READY_EXPIRE)
    _block(page, "attend la relecture")
    _toast_says(page, f"{task_id} : Prête → Bloquée")
    probe = "() => window.GrimoireWorkspace.toast"
    assert page.evaluate(f"{probe}.held") is False
    page.locator("#toast .toast-card").hover()
    assert page.evaluate(f"{probe}.held") is True
    page.mouse.move(5, 450)
    assert page.evaluate(f"{probe}.held") is False
    # Au clavier : « Annuler » atteint, le délai attend.
    page.locator("#toast [data-role='toast-undo']").focus()
    assert page.evaluate(f"{probe}.held") is True
    page.locator("#palette-open").focus()
    assert page.evaluate(f"{probe}.held") is False
    # La fin du délai, déclenchée ici : le toast part, le geste reste.
    page.evaluate("() => window.GrimoireWorkspace.toast.expire()")
    assert page.locator("#toast .toast-card").count() == 0
    assert page.evaluate(f"{probe}.phase") is None
    assert _status(served_lotb, task_id) == "blocked"


def test_p57_retirer_une_entree_memoire_s_annule(page: Page, served_lotb: dict[str, Any]) -> None:
    word = "mot" + uuid.uuid4().hex[:10]
    entry_id = _seed(served_lotb, f"Périmée {word}, à retirer.")
    _open_entry(page, served_lotb, word)
    _retract(page, "faux depuis la 1.19")
    _toast_says(page, "Entrée retirée du service.")
    assert _journal_ops(served_lotb, entry_id)[-1] == "retract"
    page.wait_for_function("() => document.querySelector('#inspector-body .kn-badges')?.textContent.includes('retirée')")

    _undo_and_wait(page, "Entrée rendue au service.")
    assert _journal_ops(served_lotb, entry_id)[-1] == "unretract"
    page.wait_for_function("() => !document.querySelector('#inspector-body .kn-badges')?.textContent.includes('retirée')")


def test_p57_retirer_un_projet_du_registre_s_annule(page: Page, served_lotb: dict[str, Any]) -> None:
    _open(page, served_lotb, "?project=forge-lotb#configuration/projets")
    row = page.locator("tr[data-slug='calme-lotb']")
    row.wait_for(timeout=30_000)
    remove = row.locator("[data-role='registry-remove']")
    assert remove.inner_text().strip() == "Retirer", "un geste réversible n'annonce pas de second temps"
    remove.click()
    _toast_says(page, "« calme-lotb » retiré du registre")
    assert "calme-lotb" not in _registry(served_lotb)
    assert page.locator("[data-role='registry-remove-confirm']").count() == 0

    _undo_and_wait(page, "de nouveau au registre")
    assert "calme-lotb" in _registry(served_lotb)
    page.locator("tr[data-slug='calme-lotb']").wait_for(timeout=30_000)


def test_p57_retirer_de_la_flotte_s_annule(page: Page, served_lotb: dict[str, Any]) -> None:
    _open(page, served_lotb, "?scope=all#flotte")
    row = page.locator(".pl-fleet tbody tr[data-project='voisin-lotb']")
    row.wait_for(timeout=30_000)
    # Le projet servi ne se retire pas d'ici.
    assert page.locator(".pl-fleet tbody tr[data-project='forge-lotb'] .row-act[data-action='remove']").count() == 0
    row.hover()
    row.locator(".row-act[data-action='remove']").click()
    _toast_says(page, "« voisin-lotb » retiré du registre")
    assert "voisin-lotb" not in _registry(served_lotb)
    page.wait_for_function("() => !document.querySelector(\".pl-fleet tbody tr[data-project='voisin-lotb']\")")

    _undo_and_wait(page, "de nouveau au registre")
    assert "voisin-lotb" in _registry(served_lotb)
    page.locator(".pl-fleet tbody tr[data-project='voisin-lotb']").wait_for(timeout=30_000)


def test_p57_un_geste_irreversible_passe_par_une_confirmation_sans_toast(page: Page, served_lotb: dict[str, Any]) -> None:
    # Supprimer une entrée mémoire : définitif.
    word = "mot" + uuid.uuid4().hex[:10]
    entry_id = _seed(served_lotb, f"Brouillon {word}, à supprimer.")
    _open_entry(page, served_lotb, word)
    page.locator("#inspector-body .kn-actions button", has_text="Supprimer…").click()
    confirm = page.locator("#inspector-body .kn-confirm")
    confirm.wait_for()
    assert page.locator("#toast .toast-card").count() == 0
    confirm.locator("button", has_text="Supprimer définitivement").click()
    page.wait_for_function(
        "() => (document.querySelector('#inspector-body')?.textContent || '').includes('Entrée supprimée')", timeout=30_000,
    )
    assert "delete" in _journal_ops(served_lotb, entry_id)
    assert page.locator("#toast .toast-card").count() == 0, "un geste définitif n'offre jamais « Annuler »"

    # Retirer une entrée que « Ajouter » ne rendrait pas telle quelle : nom
    # choisi à la main, ou dossier disparu — aperçu puis confirmation.
    _open(page, served_lotb, "?project=forge-lotb#configuration/projets")
    for slug in ("projet-renomme", "disparu"):
        row = page.locator(f"tr[data-slug='{slug}']")
        row.wait_for(timeout=30_000)
        assert row.locator("[data-role='registry-remove']").inner_text().strip() == "Retirer…"
        row.locator("[data-role='registry-remove']").click()
        box = page.locator(f"[data-role='registry-remove-confirm'][data-slug='{slug}']")
        box.wait_for(timeout=30_000)
        assert page.locator("#toast .toast-card").count() == 0
        assert slug in _registry(served_lotb)
        box.get_by_role("button", name="Annuler").click()
        page.wait_for_function("() => !document.querySelector(\"[data-role='registry-remove-confirm']\")")
        assert slug in _registry(served_lotb)


_TOAST_CONTRAST_JS = """
() => {
  const parse = (v) => { const n = v.match(/[\\d.]+/g).map(Number); return [n[0] / 255, n[1] / 255, n[2] / 255, n.length > 3 ? n[3] : 1]; };
  const lum = ([r, g, b]) => { const f = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)); return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ground = (el) => { for (let n = el; n; n = n.parentElement) { const bg = parse(getComputedStyle(n).backgroundColor); if (bg[3] > 0.9) return bg; } return [1, 1, 1, 1]; };
  return [...document.querySelectorAll('#toast .toast-msg, #toast .toast-undo span')].map((el) => {
    const a = lum(parse(getComputedStyle(el).color)), b = lum(ground(el));
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
}
"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_p57_le_toast_tient_4_5_pour_1_dans_les_deux_themes(browser: Browser, served_lotb: dict[str, Any], scheme: str) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme=scheme)
    try:
        shot = context.new_page()
        _open(shot, served_lotb, "?project=forge-lotb#configuration/projets")
        shot.locator("tr[data-slug='calme-lotb'] [data-role='registry-remove']").click()
        _toast_says(shot, "retiré du registre")
        ratios = shot.evaluate(_TOAST_CONTRAST_JS)
        assert len(ratios) == 2 and min(ratios) >= 4.5, ratios
        _undo_and_wait(shot, "de nouveau au registre")
        ratios = shot.evaluate(_TOAST_CONTRAST_JS)
        assert ratios and min(ratios) >= 4.5, ratios
        assert "calme-lotb" in _registry(served_lotb)
    finally:
        context.close()


# ── P59 : mise en page adaptative par conteneur ─────────────────────────────

#: Un texte « déborde » s'il sort du panneau qui le porte (toile ou détail) sans
#: qu'un conteneur intermédiaire ne défile ou ne le coupe exprès (ellipse).
_OVERFLOW_JS = """
() => {
  const clipped = (el, stop) => {
    for (let n = el.parentElement; n && n !== stop; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox !== 'visible') return true;
    }
    return false;
  };
  const out = [];
  for (const id of ['canvas', 'panel-inspector']) {
    const panel = document.getElementById(id);
    if (!panel || !panel.getClientRects().length) continue;
    const edge = panel.getBoundingClientRect();
    for (const el of panel.querySelectorAll('*')) {
      const text = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (!text || !el.getClientRects().length || clipped(el, panel)) continue;
      const box = el.getBoundingClientRect();
      if (box.right > edge.right + 1 || box.left < edge.left - 1) out.push(`${id}: ${el.textContent.trim().slice(0, 30)}`);
    }
  }
  return { out, doc: document.documentElement.scrollWidth, iw: window.innerWidth };
}
"""

#: Les deux barres de la coque tiennent sur une ligne : aucun texte qui passe à
#: la ligne (les rectangles de ses lignes, pris sur le nœud texte lui-même),
#: aucun texte coupé (sauf le nom du projet, élidé exprès avec son `title`),
#: rien hors écran, et aucune boîte de la barre du haut qui en chevauche une autre.
_BARS_JS = """
() => {
  const out = [];
  const visible = (el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  for (const bar of ['appbar', 'statusbar']) {
    const root = document.getElementById(bar);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const el = node.parentElement;
      if (!node.textContent.trim() || !visible(el)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const tops = new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top)));
      const label = `${bar}: ${node.textContent.trim().slice(0, 30)}`;
      if (tops.size > 1) out.push(`${label} (sur ${tops.size} lignes)`);
      const box = el.getBoundingClientRect();
      if (box.right > window.innerWidth + 1) out.push(`${label} (hors écran)`);
      const clips = getComputedStyle(el).textOverflow === 'ellipsis';
      if (clips && el.id !== 'project-name' && el.scrollWidth > el.clientWidth + 1) out.push(`${label} (coupé)`);
    }
  }
  const parts = [...document.querySelectorAll('#brand, #project-chip, #spaces .tab, #top-live, #palette-open')]
    .filter(visible).map((el) => [el.id || el.textContent.trim().slice(0, 20), el.getBoundingClientRect()]);
  for (let i = 0; i < parts.length; i += 1) {
    for (let j = i + 1; j < parts.length; j += 1) {
      const [a, ra] = parts[i];
      const [b, rb] = parts[j];
      const overlap = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) > 0.5
        && Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top) > 0.5;
      if (overlap) out.push(`appbar: « ${a} » chevauche « ${b} »`);
    }
  }
  return out;
}
"""

_GEOMETRY_JS = """
() => {
  const r = (id) => document.getElementById(id).getBoundingClientRect();
  const c = r('center'), i = r('panel-inspector'), m = r('middle');
  return { center: [c.left, c.top, c.right, c.bottom], inspector: [i.left, i.top, i.right, i.bottom, i.width], middle: m.width };
}
"""


@pytest.mark.parametrize("width", [1440, 1024, 768])
def test_p59_le_detail_passe_sous_la_liste_quand_la_place_manque(browser: Browser, served_lotb: dict[str, Any], width: int) -> None:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce", color_scheme="light")
    try:
        shot = context.new_page()
        _travail(shot, served_lotb)
        _select_task(shot, served_lotb, READY_SHOTS)
        shot.locator("#panel-inspector").wait_for(state="visible")
        geo = shot.evaluate(_GEOMETRY_JS)
        center, inspector = geo["center"], geo["inspector"]
        if width >= 1180:
            assert inspector[0] >= center[2] - 1, f"à {width} px le détail reste à côté de la liste : {geo}"
        else:
            assert inspector[1] >= center[3] - 1, f"à {width} px le détail passe sous la liste : {geo}"
            assert inspector[4] >= geo["middle"] - 40, f"sous la liste, le détail prend toute la largeur : {geo}"
        measured = shot.evaluate(_OVERFLOW_JS)
        assert measured["doc"] <= measured["iw"], measured
        assert measured["out"] == [], f"texte hors de son panneau à {width} px : {measured['out'][:6]}"
        # Les barres de la coque : une ligne, rien de coupé, aucun chevauchement.
        bars = shot.evaluate(_BARS_JS)
        assert bars == [], f"barres de la coque à {width} px : {bars[:8]}"

        # Les tuiles (fiche du projet) se replient : aucune ne sort de sa rangée.
        _open(shot, served_lotb, "?project=forge-lotb#flotte")
        shot.wait_for_selector(".kpi .kpi-item", timeout=30_000)
        tiles = shot.evaluate(
            """() => { const row = document.querySelector('.kpi').getBoundingClientRect();
                 return [...document.querySelectorAll('.kpi .kpi-item')].map((t) => {
                   const b = t.getBoundingClientRect(); return [b.left, b.right, b.top, row.left, row.right]; }); }"""
        )
        assert tiles and all(t[0] >= t[3] - 1 and t[1] <= t[4] + 1 for t in tiles), tiles
        rows = len({round(t[2]) for t in tiles})
        per_row = max(1, int((tiles[0][4] - tiles[0][3]) // 150))
        assert rows >= -(-len(tiles) // per_row), f"les tuiles ne se replient pas à {width} px : {tiles}"
        measured = shot.evaluate(_OVERFLOW_JS)
        assert measured["out"] == [], f"texte hors de son panneau à {width} px : {measured['out'][:6]}"
    finally:
        context.close()


# ── Captures de relecture ───────────────────────────────────────────────────


def test_captures_palette_menu_toast_et_largeurs(browser: Browser, served_lotb: dict[str, Any]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for scheme, word in (("light", "clair"), ("dark", "sombre")):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
        try:
            shot = context.new_page()
            _travail(shot, served_lotb)
            shot.keyboard.press("Control+k")
            shot.wait_for_selector("#palette[open]")
            shot.screenshot(path=str(out / f"p55-palette-dialog-{word}.png"))
            shot.keyboard.press("Escape")
            card = shot.locator(".ex-card").first
            card.hover()
            card.locator("[data-role='row-more']").click()
            shot.screenshot(path=str(out / f"p55-menu-popover-{word}.png"))
            shot.keyboard.press("Escape")
            # Un geste réversible, puis son annulation, pour laisser la tâche prête.
            _open(shot, served_lotb, "?project=forge-lotb#configuration/projets")
            shot.locator("tr[data-slug='calme-lotb'] [data-role='registry-remove']").click()
            _toast_says(shot, "retiré du registre")
            shot.mouse.move(5, 450)
            shot.screenshot(path=str(out / f"p57-toast-annuler-{word}.png"))
            _undo_and_wait(shot, "de nouveau au registre")
            for width in (1440, 1024, 768):
                shot.set_viewport_size({"width": width, "height": 900})
                _travail(shot, served_lotb)
                _select_task(shot, served_lotb, READY_SHOTS)
                shot.mouse.move(5, 5)
                shot.screenshot(path=str(out / f"p59-travail-{width}-{word}.png"))
            shot.set_viewport_size({"width": 1440, "height": 900})
        finally:
            context.close()
