"""Mémoire (P34) : corriger une entrée depuis son panneau de détail, avec aperçu avant/après.

Le cockpit sert un projet initialisé pour de vrai (backend local) ; un second
projet du registre n'existe que pour prouver qu'on n'y écrit pas. Chaque test
crée ses propres entrées, aux mots uniques, et les retrouve par recherche :
aucun ne dépend de la position d'un élément ni de l'état laissé par un autre.

Prouvé par navigateur, et par le journal des écritures (P14) relu sur disque :

- éditer les étiquettes, marquer remplacée par, retirer, fusionner (mise à jour
  de la survivante puis remplacement des autres) et rattacher une source
  (tâche, session, fichier et ligne, commit) passent par un aperçu avant/après
  puis par ``MemoryManager`` ; une raison est exigée pour remplacer, retirer et
  fusionner ;
- chaque geste écrit le journal et s'annule depuis l'historique de l'entrée ;
- une application sur une entrée qui a changé depuis l'aperçu est refusée, et
  l'aperçu se recalcule ;
- une projection montre « Corriger la source » au lieu des gestes d'édition ;
- hors du projet servi, les gestes sont absents et la raison est écrite.
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
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
    entry = mgr.store(item["text"], metadata=item.get("meta", {"type": "decisions"}), tags=tuple(item.get("tags", ())))
    ids[item["key"]] = entry.id
print(json.dumps(ids))
"""

_RETAG = r"""
import sys
from pathlib import Path
from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager

root = Path.cwd()
mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
mgr.update_tags(sys.argv[1], add=[sys.argv[2]])
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


@pytest.fixture(scope="session")
def served_p34(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("memoire-p34") / "fake-home"
    root.mkdir(parents=True)
    forge, other = root / "forge-p34", root / "autre-p34"
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["GRIMOIRE_COCKPIT_HOME"] = str(root / ".grimoire" / "cockpit")
    env["NO_COLOR"] = "1"
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    env_init = {**env, "GRIMOIRE_NO_COCKPIT": "1"}
    for project, name in ((forge, "forge-p34"), (other, "autre-p34")):
        init = _run([sys.executable, "-m", "grimoire", "init", str(project), "-y", "--name", name, "--backend", "local"],
                    root, env_init)
        if not (project / "_grimoire" / "kit").is_dir():
            pytest.skip(f"`grimoire init` indisponible ici : {init.stderr[-400:]}")
        _run([sys.executable, "-m", "grimoire", "cockpit", "add", str(project)], root, env_init)
    registry = json.loads((Path(env["GRIMOIRE_COCKPIT_HOME"]) / "registry.json").read_text(encoding="utf-8"))
    slugs = {Path(str(e.get("path", ""))).name: str(e.get("slug", "")) for e in registry}
    if not slugs.get("forge-p34") or not slugs.get("autre-p34"):
        pytest.skip("slugs introuvables au registre du cockpit après `add`")

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "cockpit", "serve", "--port", str(port), "--no-open", "--no-refresh"],
        cwd=str(forge), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {
            "base": f"http://127.0.0.1:{port}", "forge": forge, "other": other, "env": env_init,
            "forge_slug": slugs["forge-p34"], "other_slug": slugs["autre-p34"],
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _seed(srv: dict[str, Any], items: list[dict[str, Any]], *, project: str = "forge") -> dict[str, str]:
    seeded = _run([sys.executable, "-c", _SEED, json.dumps(items)], srv[project], srv["env"])
    assert seeded.returncode == 0, seeded.stderr[-800:]
    return dict(json.loads(seeded.stdout.strip().splitlines()[-1]))


def _journal(srv: dict[str, Any], entry_id: str) -> list[dict[str, Any]]:
    path = srv["forge"] / "_grimoire-output" / "memory-changes.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if entry_id in {r.get("before_id"), r.get("after_id")}]


@pytest.fixture
def page_p34(browser: Browser, served_p34: dict[str, Any]) -> Iterator[tuple[Page, dict[str, Any]]]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    page = context.new_page()
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    try:
        yield page, served_p34
    finally:
        context.close()
    assert dialogs == [], f"aucune boîte de dialogue native attendue : {dialogs}"


def _word() -> str:
    return "mot" + uuid.uuid4().hex[:10]


def _open_entry(page: Page, srv: dict[str, Any], term: str, title: str, *, project: str = "forge_slug") -> None:
    page.goto(f"{srv['base']}/workspace/index.html?project={srv[project]}#savoir/memoire", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'memoire'")
    page.wait_for_selector(".kn-main .kn-summary:not(:empty)", timeout=30_000)
    field = page.locator(".kn-searchbar input[type='search']")
    field.fill(term)
    field.press("Enter")
    page.wait_for_function(
        "(t) => document.querySelector('.kn-searchbar .formula')?.textContent.includes(t)"
        " && document.querySelector('.kn-summary')?.textContent.includes('résultat')",
        arg=term, timeout=30_000,
    )
    page.locator(".kn-res", has_text=title).first.click()
    page.wait_for_function(
        "(t) => document.querySelector('#inspector-body .kn-detail h2')?.textContent.includes(t)", arg=title,
    )


def _panel(page: Page) -> Any:
    return page.locator("#inspector-body .kn-gesture")


def _preview(page: Page) -> Any:
    _panel(page).locator("button", has_text="Voir l’aperçu").click()
    diff = _panel(page).locator(".kn-diff")
    diff.wait_for(timeout=30_000)
    return diff


def _apply_ok(page: Page, expected: str) -> None:
    _panel(page).locator("button", has_text="Appliquer").click()
    page.wait_for_function(
        "(t) => document.querySelector(\"#inspector-body .kn-status[data-tone='ok']\")?.textContent.includes(t)",
        arg=expected, timeout=30_000,
    )
    assert _panel(page).count() == 0, "le panneau du geste se referme après application"


def _undo(page: Page, op: str, expected: str) -> None:
    row = page.locator(f"#inspector-body .kn-hist li[data-op='{op}']").first
    row.locator(".kn-undo").wait_for(timeout=30_000)
    row.locator(".kn-undo").click()
    diff = _panel(page).locator(".kn-diff")
    diff.wait_for(timeout=30_000)
    assert diff.locator(".kn-diff-after").count() >= 1
    _apply_ok(page, expected)


def test_corriger_les_etiquettes_avec_apercu_puis_annuler_depuis_l_historique(
    page_p34: tuple[Page, dict[str, Any]],
) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [{"key": "e", "text": f"Étiquettes {word} à corriger.", "tags": ["qdrant"]}])
    _open_entry(page, srv, word, f"Étiquettes {word}")

    buttons = [b.strip() for b in page.locator("#inspector-body .kn-actions button").all_inner_texts()]
    assert buttons == ["Corriger", "Marquer remplacée…", "Demander une correction…", "Supprimer…"], "les gestes de la maquette (P34 + P42)"
    page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
    panel = _panel(page)
    assert [t.strip() for t in panel.locator(".seg button").all_inner_texts()] == ["Étiquettes", "Source", "Fusionner", "Retirer"]
    panel.locator("input[name='tags']").fill("qdrant, infra")
    diff = _preview(page)
    row = diff.locator(".kn-diff-row[data-field='Étiquettes']")
    assert row.locator(".kn-diff-before").inner_text().endswith("qdrant")
    assert row.locator(".kn-diff-after").inner_text().endswith("qdrant, infra")
    assert _journal(srv, ids["e"])[-1]["op"] == "store", "l'aperçu n'écrit rien"

    _apply_ok(page, "Étiquettes corrigées.")
    last = _journal(srv, ids["e"])[-1]
    assert last["op"] == "update_tags" and last["tags_added"] == ["infra"]
    assert "qdrant, infra" in page.locator("#inspector-body .kn-kv").inner_text()

    _undo(page, "update_tags", "Étiquettes corrigées.")
    assert _journal(srv, ids["e"])[-1]["tags_removed"] == ["infra"]
    page.wait_for_function("() => !document.querySelector('#inspector-body .kn-kv')?.textContent.includes('infra')")


def test_marquer_remplacee_exige_une_raison_et_s_annule(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [
        {"key": "old", "text": f"Ancienne {word} en 1.17."},
        {"key": "new", "text": f"Nouvelle {word} en 1.19."},
    ])
    _open_entry(page, srv, word, f"Ancienne {word}")
    page.locator("#inspector-body .kn-actions button", has_text="Marquer remplacée…").click()
    panel = _panel(page)
    panel.locator("select[name='replacement']").select_option(ids["new"])
    diff = _preview(page)
    assert f"remplacée par « Nouvelle {word} en 1.19. »" in diff.locator(".kn-diff-after").inner_text()
    assert "obligatoire" in panel.locator("label", has=page.locator("input[name='reason']")).inner_text()

    panel.locator("button", has_text="Appliquer").click()
    status = panel.locator(".action-status[data-tone='bad']")
    status.wait_for()
    assert "raison" in status.inner_text()
    assert _journal(srv, ids["old"])[-1]["op"] == "store", "sans raison, rien n'est écrit"

    panel.locator("input[name='reason']").fill("montée de version")
    _apply_ok(page, "Entrée marquée remplacée")
    last = _journal(srv, ids["old"])[-1]
    assert (last["op"], last["after_id"], last["reason"]) == ("supersede", ids["new"], "montée de version")
    page.wait_for_function("() => document.querySelector('#inspector-body .kn-badges')?.textContent.includes('remplacée')")

    _undo(page, "supersede", "Remplacement annulé")
    assert _journal(srv, ids["old"])[-1].get("undoes") == "supersede"


def test_retirer_avec_raison_puis_rendre_au_service_depuis_l_historique(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [{"key": "e", "text": f"Périmée {word}, à retirer."}])
    _open_entry(page, srv, word, f"Périmée {word}")
    page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
    panel = _panel(page)
    panel.locator(".seg button", has_text="Retirer").click()
    diff = _preview(page)
    assert diff.locator(".kn-diff-before").inner_text().endswith("en vigueur")
    assert "retirée" in diff.locator(".kn-diff-after").inner_text()
    panel.locator("input[name='reason']").fill("faux depuis la 1.19")
    _apply_ok(page, "Entrée retirée du service")
    assert _journal(srv, ids["e"])[-1]["op"] == "retract"
    page.wait_for_function("() => document.querySelector('#inspector-body .kn-badges')?.textContent.includes('retirée')")
    assert page.locator("#inspector-body .kn-actions button", has_text="Rendre au service…").count() == 1

    _undo(page, "retract", "Entrée rendue au service.")
    assert _journal(srv, ids["e"])[-1]["op"] == "unretract"


def test_fusionner_met_a_jour_la_survivante_puis_remplace_les_autres(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [
        {"key": "keep", "text": f"Survivante {word} : snapshots hors volume.", "tags": ["qdrant"]},
        {"key": "d1", "text": f"Doublon {word} un : snapshots perdus.", "tags": ["backup"]},
        {"key": "d2", "text": f"Doublon {word} deux : sauvegarder avant.", "tags": ["qdrant"]},
    ])
    _open_entry(page, srv, word, f"Survivante {word}")
    page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
    panel = _panel(page)
    panel.locator(".seg button", has_text="Fusionner").click()
    for key in ("d1", "d2"):
        panel.locator(f"input[type='checkbox'][value='{ids[key]}']").check()
    panel.locator("textarea[name='survivor-text']").fill(f"Survivante {word} : snapshots hors volume, sauvegarder avant.")
    diff = _preview(page)
    assert [r.get_attribute("data-entry") for r in diff.locator(".kn-diff-row").all()] == [
        ids["keep"], ids["keep"], ids["d1"], ids["d2"],
    ]
    assert diff.locator(".kn-diff-entry").count() == 3, "chaque entrée en jeu est nommée"
    panel.locator("input[name='reason']").fill("doublons")
    _apply_ok(page, "Fusion faite : 2 entrée(s)")

    survivor = _journal(srv, ids["keep"])
    assert [r["op"] for r in survivor[-3:]] == ["update", "supersede", "supersede"]
    assert survivor[-3]["gesture"] == "merge" and survivor[-3]["merged"] == [ids["d1"], ids["d2"]]
    assert all(r["reason"] == "doublons" for r in survivor[-3:])
    assert "backup" in page.locator("#inspector-body .kn-kv").inner_text()

    _undo(page, "update", "Écriture annulée")
    page.wait_for_function("() => !document.querySelector('#inspector-body .kn-kv')?.textContent.includes('backup')")


def test_rattacher_une_source_tache_session_fichier_et_ligne_commit(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [{"key": "e", "text": f"Sourcée {word} après coup."}])
    _open_entry(page, srv, word, f"Sourcée {word}")
    for kind, value, line, label in (
        ("task", "GAO-p34-correct-001", "", "tâche GAO-p34-correct-001"),
        ("session", "s-2026-10-25", "", "session s-2026-10-25"),
        ("commit", "583f4e4d3", "", "commit 583f4e4d3"),
        ("file", "project-context.yaml", "2", "fichier project-context.yaml:2"),
    ):
        page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
        panel = _panel(page)
        panel.locator(".seg button", has_text="Source").click()
        panel.locator("select[name='source-kind']").select_option(kind)
        panel.locator("input[name='source-value']").fill(value)
        if line:
            panel.locator("input[name='source-line']").fill(line)
        diff = _preview(page)
        assert diff.locator(".kn-diff-after").inner_text().endswith(label)
        _apply_ok(page, f"Source rattachée : {label}.")
        assert _journal(srv, ids["e"])[-1]["gesture"] == "source"
        page.wait_for_selector("#inspector-body .kn-hist", timeout=30_000)
    assert "project-context.yaml:2" in page.locator("#inspector-body .kn-kv").inner_text()

    _undo(page, "update", "Écriture annulée")
    page.wait_for_function("() => document.querySelector('#inspector-body .kn-kv')?.textContent.includes('583f4e4d3')")


def test_un_apercu_perime_est_refuse_puis_recalcule(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    ids = _seed(srv, [{"key": "e", "text": f"Concurrente {word}.", "tags": ["a"]}])
    _open_entry(page, srv, word, f"Concurrente {word}")
    page.locator("#inspector-body .kn-actions button", has_text="Corriger").click()
    panel = _panel(page)
    panel.locator("input[name='tags']").fill("a, b")
    diff = _preview(page)
    first_version = diff.get_attribute("data-version")

    retag = _run([sys.executable, "-c", _RETAG, ids["e"], "ailleurs"], srv["forge"], srv["env"])
    assert retag.returncode == 0, retag.stderr[-800:]

    panel.locator("button", has_text="Appliquer").click()
    status = panel.locator(".action-status[data-tone='bad']")
    status.wait_for(timeout=30_000)
    assert "a changé depuis l'aperçu" in status.inner_text()
    page.wait_for_function(
        "(v) => document.querySelector('#inspector-body .kn-diff')?.dataset.version !== v", arg=first_version,
    )
    assert panel.locator(".kn-diff-before").inner_text().endswith("a, ailleurs"), "l'aperçu relit l'entrée"
    assert _journal(srv, ids["e"])[-1]["tags_added"] == ["ailleurs"], "rien n'a été appliqué"

    _apply_ok(page, "Étiquettes corrigées.")
    assert _journal(srv, ids["e"])[-1]["tags_removed"] == ["ailleurs"]


def test_une_projection_montre_corriger_la_source(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    _seed(srv, [{
        "key": "p", "text": f"Projection {word} du fichier de configuration.",
        "meta": {"projection_group": "code", "source_kind": "projection", "file_path": "project-context.yaml",
                 "line_start": 3},
    }])
    _open_entry(page, srv, word, f"Projection {word}")
    detail = page.locator("#inspector-body .kn-detail")
    fix = detail.locator(".kn-fix[data-fix='source']")
    assert "elle se corrige à sa source" in fix.inner_text()
    assert detail.locator(".kn-actions").count() == 0, "aucun geste d'édition sur une projection"
    assert detail.locator("button", has_text="Marquer remplacée").count() == 0
    assert "grimoire memory vector sync-code" in fix.locator(".formula").inner_text()
    page.wait_for_selector("#inspector-body .kn-hist", timeout=30_000)
    assert page.locator("#inspector-body .kn-undo").count() == 0
    fix.locator("button", has_text="Corriger la source").click()
    page.wait_for_function("() => window.GrimoireWorkspace.module === 'source'", timeout=30_000)


def test_hors_du_projet_servi_les_gestes_sont_absents_avec_leur_raison(page_p34: tuple[Page, dict[str, Any]]) -> None:
    page, srv = page_p34
    word = _word()
    _seed(srv, [{"key": "e", "text": f"Ailleurs {word}, en lecture seule."}], project="other")
    _open_entry(page, srv, word, f"Ailleurs {word}", project="other_slug")
    detail = page.locator("#inspector-body .kn-detail")
    assert detail.locator("button", has_text="Corriger").count() == 0
    assert detail.locator(".kn-actions").count() == 0
    assert "lecture seule" in detail.locator(".kn-note[data-note='readonly']").inner_text()


def test_p42_captures_de_relecture(browser: Browser, served_p34: dict[str, Any]) -> None:
    """Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, écrit les captures de « Demander une correction… »."""
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for theme, label in (("light", "clair"), ("dark", "sombre")):
        word = _word()
        title = f"Décision {word}"
        _seed(served_p34, [{"key": "a", "text": f"{title} : la limite est de 10 mots", "tags": [word]}])
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme=theme)
        page = context.new_page()
        try:
            _open_entry(page, served_p34, word, title)
            page.evaluate(f"() => document.querySelector(`#st-theme [data-theme-pref='{theme}']`)?.click()")
            page.locator("#inspector-body button", has_text="Demander une correction…").click()
            page.locator(".kn-ask textarea").fill("La limite est de 20 mots, pas 10 : l’agent doit le vérifier dans le code.")
            assert "peut encore travailler" in page.locator(".kn-ask .kn-ask-note").inner_text()
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / f"p42-demander-une-correction-{label}.png"), full_page=False)
            page.locator(".kn-ask button[type='submit']").click()
            page.wait_for_selector("#inspector-body .kn-status[data-tone='ok']", timeout=30_000)
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / f"p42-tache-creee-{label}.png"), full_page=False)
        finally:
            context.close()
