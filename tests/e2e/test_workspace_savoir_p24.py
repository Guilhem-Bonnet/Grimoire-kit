"""Savoir › Mémoire (P24) : la recherche en entrée, sur deux projets réels qui partagent un store.

Le cockpit sert deux projets initialisés pour de vrai. Le store du second est
le même fichier que celui du premier (lien symbolique) : un emplacement
partagé. Le cockpit est lancé depuis le premier projet, seul projet où il
accepte d'écrire.

Prouvé par navigateur :

- l'écran d'entrée est la recherche, avec des filtres de type, de date et de
  projet ; chaque résultat montre son type, sa date, sa provenance, sa puce
  projet et sa chaîne de supersession ;
- en portée « Projet X », les entrées de l'autre projet du même fichier ne
  sont que comptées, jamais montrées ;
- la suppression demande une confirmation dans la page (aucune boîte native)
  et dit son résultat sur place ; hors du projet servi, elle n'est pas
  proposée.
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

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

_SEED = r"""
import json, sys
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
ids = {}
for item in json.loads(sys.argv[1]):
    meta = {"type": item["type"]} if item.get("type") else {}
    entry = mgr.store(
        item["text"], metadata=meta, tags=tuple(item.get("tags", ())),
        actor=item.get("actor", ""), session_id=item.get("session", ""), task_ref=item.get("task", ""),
    )
    ids[item["key"]] = entry.id
for old, new in json.loads(sys.argv[2]):
    mgr.supersede(ids[old], ids[new], reason="montée de version")
print(json.dumps(ids))
"""

_FORGE_ENTRIES = [
    {"key": "old", "type": "decisions", "text": "Qdrant partagé épinglé en 1.17.1, sans montée prévue."},
    {
        "key": "pin", "type": "decisions", "actor": "Guilhem", "session": "s-2026-10-05",
        "task": "GAO-qdrant-maj-001", "tags": ["qdrant"],
        "text": "Qdrant partagé épinglé en 1.19.1, une mineure à la fois. Montée depuis 1.17.1 en passant "
                "par chaque mineure, jamais de saut. Les snapshots vivent hors du volume : ils sont perdus "
                "si le conteneur est recréé.",
    },
    {
        "key": "trap", "type": "failures",
        "text": "Snapshots Qdrant hors volume. Perdus à la recréation du conteneur : sauvegarder avant toute "
                "montée de version.",
    },
    {
        "key": "rule", "type": "shared-context",
        "text": "Une mineure à la fois pour les services partagés. S’applique à Qdrant et aux autres services du poste.",
    },
    {"key": "loose", "text": "qdrant répond sur le port 6333 du poste"},
]
_POSTE_ENTRIES = [
    {"key": "own", "type": "decisions", "text": "Qdrant du poste : volume nommé qdrant_storage, jamais un dossier lié."},
]


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


@pytest.fixture(scope="session")
def served_savoir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    root = tmp_path_factory.mktemp("savoir-p24") / "fake-home"
    root.mkdir(parents=True)
    forge = root / "grimoire-forge"
    poste = root / "poste"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}

    for project, name in ((forge, "Grimoire-Forge"), (poste, "poste")):
        init = _run([sys.executable, "-m", "grimoire", "init", str(project), "-y", "--name", name, "--backend", "local"],
                    root, env_init)
        if not (project / "_grimoire" / "kit").is_dir():
            pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
        _run([sys.executable, "-m", "grimoire", "cockpit", "add", str(project)], root, env_init)

    seeded = _run([sys.executable, "-c", _SEED, json.dumps(_FORGE_ENTRIES), json.dumps([["old", "pin"]])], forge, env_init)
    assert seeded.returncode == 0, seeded.stderr[-800:]
    ids = json.loads(seeded.stdout.strip().splitlines()[-1])

    # L'emplacement partagé : le store de `poste` est le fichier de la Forge.
    shared = forge / "_grimoire" / "_memory" / "grimoire.json"
    assert shared.is_file(), "le store local de la Forge doit exister après les écritures"
    target = poste / "_grimoire" / "_memory" / "grimoire.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(target.with_suffix(".json.avant-partage"))
    target.symlink_to(shared)
    seeded_poste = _run([sys.executable, "-c", _SEED, json.dumps(_POSTE_ENTRIES), "[]"], poste, env_init)
    assert seeded_poste.returncode == 0, seeded_poste.stderr[-800:]
    ids.update({f"poste-{k}": v for k, v in json.loads(seeded_poste.stdout.strip().splitlines()[-1]).items()})

    registry = json.loads((Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json").read_text(encoding="utf-8"))
    slugs = {Path(str(e.get("path", ""))).name: str(e.get("slug", "")) for e in registry}
    if not slugs.get("grimoire-forge") or not slugs.get("poste"):
        pytest.skip("slugs introuvables au registre du cockpit après `add`")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"base": f"http://127.0.0.1:{port}", "forge": slugs["grimoire-forge"], "poste": slugs["poste"], **ids}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def savoir(browser: Browser, served_savoir: dict[str, str]) -> Iterator[tuple[Page, dict[str, str]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    page = context.new_page()
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    try:
        yield page, served_savoir
    finally:
        context.close()
    assert dialogs == [], f"aucune boîte de dialogue native attendue : {dialogs}"


def _open(page: Page, base: str, query: str) -> None:
    page.goto(f"{base}/workspace/index.html?{query}#savoir/memoire", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")
    page.wait_for_selector(".kn-main .kn-summary:not(:empty)", timeout=30_000)


def _search(page: Page, term: str) -> None:
    field = page.locator(".kn-searchbar input[type='search']")
    field.fill(term)
    field.press("Enter")
    page.wait_for_function(
        "(t) => document.querySelector('.kn-summary')?.textContent.includes('résultat')"
        " && document.querySelector('.kn-searchbar .formula')?.textContent.includes(t)",
        arg=term, timeout=30_000,
    )


def _settled(page: Page) -> None:
    """Attend la fin de la lecture lancée par un filtre (le résumé dit « Lecture… » pendant)."""
    page.wait_for_function(
        "() => { const t = document.querySelector('.kn-summary')?.textContent || '';"
        " return t && !t.startsWith('Lecture'); }",
        timeout=30_000,
    )


def test_l_ecran_d_entree_est_la_recherche_avec_ses_filtres(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    _open(page, srv["base"], "scope=all")

    assert page.locator(".kn-searchbar input[type='search']").is_visible()
    assert page.locator(".kn-searchbar .formula").text_content().strip() == "›grimoire memory list"
    assert page.locator("#view-seg button:visible").count() == 0, "plus d'onglets Store / Graphe / Couches"
    legends = [t.strip() for t in page.locator(".kn-filters legend").all_inner_texts()]
    assert legends == ["Type", "Source", "Projet", "Date"]
    assert page.locator(".kn-filters select[aria-label='Période']").count() == 1
    projects = page.locator(".kn-filters fieldset").nth(2).locator("input[type='checkbox']")
    assert projects.count() >= 2, "filtre projet multiple en portée « Tous les projets »"
    assert all(projects.nth(i).is_enabled() for i in range(projects.count()))

    _search(page, "qdrant")
    rows = page.locator(".kn-res")
    assert rows.count() >= 4
    for i in range(rows.count()):
        row = rows.nth(i)
        assert row.locator(".badge").inner_text().strip(), "chaque résultat porte son type"
        assert row.locator(".kn-date").inner_text().strip() not in {"", "—"}, "et sa date"
        assert row.locator(".kn-path").inner_text().strip(), "et sa provenance"
        assert row.locator(".kn-proj").count() == 1, "et sa puce projet"
        assert row.locator(".kn-state").inner_text().strip(), "et sa chaîne de supersession"
    assert "triés par rang fusionné entre projets" in page.locator(".kn-summary").inner_text()
    pin = rows.filter(has_text="épinglé en 1.19.1").first
    assert "remplace « Qdrant partagé épinglé en 1.17.1, sans montée prévue. »" in pin.inner_text()
    assert pin.locator(".badge").inner_text().strip() == "Décision"


def test_les_filtres_type_projet_et_remplacees(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    _open(page, srv["base"], "scope=all")
    _search(page, "qdrant")
    rows = page.locator(".kn-res")
    before = rows.count()
    types = page.locator(".kn-filters fieldset").first
    assert page.locator(".kn-res[data-type='untyped']").count() == 0, "« Fait non typé » est décoché par défaut"

    types.locator("label", has_text="Fait non typé").locator("input").check()
    _settled(page)
    assert page.locator(".kn-res[data-type='untyped']").count() == 1
    assert rows.count() == before + 1

    types.locator("label", has_text="Décision").locator("input").uncheck()
    _settled(page)
    assert page.locator(".kn-res[data-type='decision']").count() == 0
    assert rows.count() > 0

    types.locator("label", has_text="Décision").locator("input").check()
    _settled(page)
    page.locator(".kn-filters fieldset").nth(2).locator("label", has_text="poste").locator("input").uncheck()
    _settled(page)
    assert page.locator(f".kn-res[data-project='{srv['poste']}']").count() == 0
    assert page.locator(f".kn-res[data-project='{srv['forge']}']").count() >= 3

    old = page.locator(".kn-res:has(.kn-state[data-state='superseded'])")
    assert old.count() == 0, "la décision remplacée n'est pas servie par défaut"
    page.locator(".kn-filters label", has_text="Inclure les décisions remplacées").locator("input").check()
    _settled(page)
    assert "remplacée par « Qdrant partagé épinglé en 1.19.1, une mineure à la fois. »" in old.first.inner_text()


def test_la_periode_passe_au_serveur_et_filtre_les_dates(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    calls: list[str] = []
    page.on("request", lambda r: calls.append(r.url) if "memory/entries" in r.url else None)
    _open(page, srv["base"], f"project={srv['forge']}")
    assert page.locator(".kn-res").count() >= 3

    page.locator(".kn-filters select[aria-label='Période']").select_option("7")
    _settled(page)
    assert any("since=" in url for url in calls), "la période devient un filtre serveur `since`"
    assert page.locator(".kn-res").count() >= 3, "les entrées du jour restent dans les 7 derniers jours"


def test_en_portee_projet_les_entrees_de_l_autre_projet_ne_sont_que_comptees(
    savoir: tuple[Page, dict[str, str]],
) -> None:
    page, srv = savoir
    _open(page, srv["base"], f"project={srv['poste']}")

    rows = page.locator(".kn-res")
    assert rows.count() == 1
    assert "volume nommé qdrant_storage" in rows.first.inner_text()
    body = page.locator("#canvas").inner_text() + page.locator("#inspector-body").inner_text()
    assert "1.19.1" not in body and "Snapshots" not in body, "aucun contenu de la Forge en portée poste"
    note = page.locator(".kn-note[data-note='others']")
    assert "Grimoire-Forge" in note.inner_text() and "comptées, jamais montrées" in note.inner_text()

    _search(page, "qdrant")
    assert page.locator(".kn-res").count() == 1
    assert "1.19.1" not in page.locator("#canvas").inner_text()
    assert page.locator(".kn-note[data-note='others']").count() == 1

    hidden = page.evaluate(
        "(id) => fetch('/api/workspace/memory/entry?id=' + id + location.search.replace('?', '&')).then(r => r.json())",
        srv["pin"],
    )
    assert hidden["found"] is False and hidden["hidden"] is True and hidden["entry"] is None


def test_le_detail_montre_provenance_et_chaine(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    _open(page, srv["base"], f"project={srv['forge']}")
    _search(page, "épinglé")
    page.locator(".kn-res", has_text="1.19.1").first.click()
    detail = page.locator("#inspector-body .kn-detail")
    # L'historique (journal des écritures P14) vient avec la fiche complète (P34 : section « Historique »).
    page.wait_for_selector("#inspector-body .kn-hist li[data-op='store']")

    text = detail.inner_text()
    assert "en vigueur" in text
    assert "Décidé le" in text and "Guilhem, session s-2026-10-05" in text
    assert "Remplace" in text and "1.17.1, sans montée prévue" in text
    assert "GAO-qdrant-maj-001" in text
    assert detail.locator(".badge").first.inner_text().strip() == "Décision"
    assert page.locator("#panel-inspector").is_visible()


def test_la_suppression_se_confirme_dans_la_page(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    _open(page, srv["base"], f"project={srv['forge']}")
    page.locator(".kn-filters fieldset").first.locator("label", has_text="Fait non typé").locator("input").check()
    target = page.locator(".kn-res", has_text="port 6333")
    target.wait_for()
    target.click()

    page.locator("#inspector-body button", has_text="Supprimer…").click()
    confirm = page.locator("#inspector-body .kn-confirm")
    assert "Supprimer définitivement" in confirm.inner_text()
    confirm.locator("button", has_text="Annuler").click()
    assert page.locator("#inspector-body .kn-confirm").count() == 0

    page.locator("#inspector-body button", has_text="Supprimer…").click()
    page.locator("#inspector-body .kn-confirm button", has_text="Supprimer définitivement").click()
    page.wait_for_selector("#inspector-body .kn-status[data-tone='ok']", timeout=30_000)
    assert "Entrée supprimée." in page.locator("#inspector-body .kn-status").inner_text()
    assert page.locator(".kn-res", has_text="port 6333").count() == 0

    after = page.evaluate(
        "() => fetch('/api/workspace/memory/entries?types=untyped' + location.search.replace('?', '&')).then(r => r.json())"
    )
    assert not [e for e in after["entries"] if "6333" in e["text"]], "l'entrée a quitté le store"


def test_hors_du_projet_servi_la_suppression_n_est_pas_proposee(savoir: tuple[Page, dict[str, str]]) -> None:
    page, srv = savoir
    _open(page, srv["base"], f"project={srv['poste']}")
    page.locator(".kn-res").first.click()
    page.wait_for_selector("#inspector-body .kn-detail")
    assert page.locator("#inspector-body button", has_text="Supprimer").count() == 0
