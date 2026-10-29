"""P45 — premier lancement : « Lancer » exécute le plan du guide par les actions typées de P40.

Critères de la tâche ``GAO-p45-premier-001`` :

1. depuis le plan de P36, « Lancer » exécute les actions typées de P40 avec
   progression par étape, échec traduit, Réessayer, Sauter (étape non
   bloquante) et Revenir en arrière ;
2. la vérification fait un aller-retour mémoire réel (écrire, retrouver,
   retirer un souvenir de test) et lance doctor ; un écart s'affiche avec son
   remède ;
3. l'avancement du guide est enregistré et reprend à l'étape interrompue après
   rechargement ;
4. captures (``GRIMOIRE_E2E_CAPTURES=<dossier>``), à valider par Guilhem : sans
   la variable, le test de capture ne fait rien (il ne se skippe pas : la CI
   promeut tout skip en échec).

Le cockpit sert un projet de lancement équipé en lexical, avec un ``PATH`` vide :
Docker y est introuvable, donc l'étape « services » du profil graphe échoue
toujours de la même façon. Les états d'avancement sont posés sur disque par le
test (c'est le contrat de persistance), puis la page les relit et agit.
"""

from __future__ import annotations

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

from grimoire.tools import workspace_first_launch as fl

GUIDE = "[data-role='setup-guide']"
LAUNCH = f"{GUIDE} [data-role='setup-launch']"
URL_PATH = "/workspace/index.html#a-traiter?view=demarrer"


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
    raise TimeoutError(f"le cockpit n'a pas répondu sur :{port}")


@pytest.fixture(scope="module")
def cockpit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    base = tmp_path_factory.mktemp("p45")
    root = base / "projet-lancement"
    root.mkdir()
    init_env = {**os.environ, "GRIMOIRE_NO_COCKPIT": "1", "NO_COLOR": "1"}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grimoire",
            "init",
            ".",
            "-y",
            "--name",
            "projet-lancement",
            "--backend",
            "local",
            "--memory-profile",
            "lexical",
        ],
        cwd=str(root),
        env=init_env,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail(f"`grimoire init` n'a pas équipé {root}")
    home = tmp_path_factory.mktemp("p45-cockpit-home")
    env = {**os.environ, "GRIMOIRE_COCKPIT_HOME": str(home), "NO_COLOR": "1"}
    env.pop("GRIMOIRE_NO_COCKPIT", None)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "cockpit", "add", str(root)],
        env=env,
        capture_output=True,
        check=False,
        timeout=60,
    )
    empty_bin = tmp_path_factory.mktemp("p45-path")
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "grimoire",
            "cockpit",
            "serve",
            "--port",
            str(port),
            "--no-open",
            "--no-refresh",
            "--project-root",
            str(root),
        ],
        cwd=str(root),
        env={**env, "PATH": str(empty_bin)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield {"url": f"http://127.0.0.1:{port}", "root": root}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _open(browser: Browser, url: str, scheme: str = "dark") -> tuple[Page, list[tuple[str, str]]]:
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}, color_scheme=scheme, reduced_motion="reduce"
    )
    page = context.new_page()
    seen: list[tuple[str, str]] = []
    page.on("request", lambda req: seen.append((req.method, req.url)))
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(f"{GUIDE} [data-role='setup-state']", timeout=30_000)
    return page, seen


def _writes(seen: list[tuple[str, str]]) -> list[str]:
    return [u.split("/api/", 1)[1].split("?", 1)[0] for m, u in seen if m == "POST"]


def _reset(root: Path) -> None:
    fl._state_path(root).unlink(missing_ok=True)


def _seed(root: Path, profile: dict[str, object], statuses: dict[str, str]) -> None:
    """Pose un avancement sur disque : ce que la page relira au rechargement."""
    steps = fl._build_steps(profile)
    for step in steps:
        step["status"] = statuses.get(step["id"], "pending")
    state = {
        "schemaVersion": fl.SCHEMA_VERSION,
        "profile": {"id": profile["id"], "label": profile["label"]},
        "created": "2026-10-28T12:00:00+00:00",
        "steps": steps,
    }
    fl._write(root, state, fl.LaunchEnv())


def _row(page: Page, step: str):
    return page.locator(f"{LAUNCH} [data-step='{step}']")


GRAPHE = {"id": "graphe", "label": "Graphe", "start": ["Weaviate", "Neo4j"]}
LEXICAL = {"id": "lexical", "label": "Lexical", "start": []}


def test_lancer_execute_les_actions_typees_et_verifie_la_memoire(browser: Browser, cockpit: dict[str, object]) -> None:
    root = cockpit["root"]
    assert isinstance(root, Path)
    _reset(root)
    page, seen = _open(browser, f"{cockpit['url']}{URL_PATH}")
    try:
        assert page.locator(f"{GUIDE} button").filter(has_text="Lancer").count() == 1
        assert _writes(seen) == [], "rien n'écrit tant que Lancer n'est pas cliqué"
        page.locator(f"{GUIDE} [data-profile='lexical']").click()
        page.locator(f"{LAUNCH} [data-role='launch-start']").click()
        # Progression par étape, jusqu'au bout — ou jusqu'à un doctor en écart, sautable.
        page.wait_for_function(
            "() => { const l = document.querySelector(\"[data-role='setup-launch']\");"
            " return l && (l.dataset.finished === 'true' || l.querySelector(\"[data-step='verify.doctor'][data-status='failed']\")); }",
            timeout=120_000,
        )
        assert _row(page, "compose").get_attribute("data-status") == "done"
        memory = _row(page, "verify.memory")
        assert memory.get_attribute("data-status") == "done"
        text = memory.inner_text()
        for word in ("Écrire", "Retrouver", "Retirer"):
            assert word in text, text
        if _row(page, "verify.doctor").get_attribute("data-status") == "failed":
            error = _row(page, "verify.doctor").locator("[data-role='step-error']").inner_text()
            assert "--fix" in error, "un écart doctor s'affiche avec son remède"
            _row(page, "verify.doctor").locator("[data-role='step-skip']").click()
            page.wait_for_selector(f"{LAUNCH}[data-finished='true']", timeout=30_000)
            assert _row(page, "verify.doctor").get_attribute("data-status") == "skipped"
        else:
            assert _row(page, "verify.doctor").get_attribute("data-status") == "done"
        # Rien d'autre que le verbe de lancement n'a été écrit.
        assert set(_writes(seen)) == {"workspace/setup/launch"}, _writes(seen)
    finally:
        page.context.close()


def test_echec_traduit_reessayer_sauter_revenir_en_arriere(browser: Browser, cockpit: dict[str, object]) -> None:
    root = cockpit["root"]
    assert isinstance(root, Path)
    config = root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")
    _seed(root, GRAPHE, {})
    page, seen = _open(browser, f"{cockpit['url']}{URL_PATH}")
    try:
        page.locator(f"{LAUNCH} [data-role='launch-resume']").click()
        failed = _row(page, "stack")
        failed.locator("[data-role='step-error']").wait_for(timeout=60_000)
        assert failed.get_attribute("data-status") == "failed"
        error = failed.locator("[data-role='step-error']").inner_text()
        assert "Docker" in error and "Réessayer" in error, error
        # Réessayer rejoue l'étape (même échec, Docker reste absent).
        count = len(_writes(seen))
        failed.locator("[data-role='step-retry']").click()
        page.wait_for_function(
            "(n) => performance.getEntriesByType('resource').filter(e => e.name.includes('setup/launch')).length > n",
            arg=count,
            timeout=30_000,
        )
        failed.locator("[data-role='step-error']").wait_for(timeout=60_000)
        assert len(_writes(seen)) > count
        # L'avancement survit au rechargement.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(f"{LAUNCH} [data-step='stack'][data-status='failed']", timeout=30_000)
        # Sauter : l'étape n'est pas bloquante, la suite reprend toute seule.
        _row(page, "stack").locator("[data-role='step-skip']").click()
        page.wait_for_selector(f"{LAUNCH} [data-step='stack'][data-status='skipped']", timeout=30_000)
        page.wait_for_function(
            "() => { const s = document.querySelector(\"[data-step='compose']\"); return s && ['done', 'failed'].includes(s.dataset.status); }",
            timeout=120_000,
        )
        assert _row(page, "compose").get_attribute("data-status") in ("done", "failed")
        # Revenir en arrière défait ce qui a été appliqué : le fichier d'avant revient.
        page.wait_for_function(
            "() => { const l = document.querySelector(\"[data-role='setup-launch']\");"
            " return l && !l.querySelector(\"[data-status='running']\") && !l.querySelector(\"[data-role='launch-resume']\"); }",
            timeout=120_000,
        )
        if page.locator(f"{LAUNCH} [data-role='launch-rollback']").count():
            page.wait_for_selector(f"{LAUNCH} [data-role='launch-rollback']:not([disabled])", timeout=120_000)
            page.locator(f"{LAUNCH} [data-role='launch-rollback']").click()
            page.wait_for_selector(f"{LAUNCH} [data-step='compose'][data-status='rolled_back']", timeout=30_000)
        assert config.read_text(encoding="utf-8") == before
    finally:
        page.context.close()
        _reset(root)
        config.write_text(before, encoding="utf-8")


def test_l_avancement_reprend_a_l_etape_interrompue_apres_rechargement(
    browser: Browser, cockpit: dict[str, object]
) -> None:
    root = cockpit["root"]
    assert isinstance(root, Path)
    _seed(root, LEXICAL, {"compose": "done", "verify.memory": "running"})
    page, _seen = _open(browser, f"{cockpit['url']}{URL_PATH}")
    try:
        interrupted = _row(page, "verify.memory")
        assert interrupted.get_attribute("data-status") == "interrupted"
        assert "interrompue" in interrupted.inner_text()
        assert _row(page, "compose").get_attribute("data-status") == "done"
        page.locator(f"{LAUNCH} [data-role='launch-resume']").click()
        page.wait_for_function(
            "() => { const s = document.querySelector(\"[data-step='verify.memory']\"); return s && s.dataset.status === 'done'; }",
            timeout=120_000,
        )
        assert _row(page, "compose").get_attribute("data-status") == "done", "l'étape faite n'est pas rejouée"
    finally:
        page.context.close()
        _reset(root)


def test_captures_de_relecture(browser: Browser, cockpit: dict[str, object]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    root = cockpit["root"]
    assert isinstance(root, Path)
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for scheme, word in (("light", "clair"), ("dark", "sombre")):
        for name, profile, statuses in (
            ("echec", GRAPHE, {}),
            ("interrompu", LEXICAL, {"compose": "done", "verify.memory": "running"}),
        ):
            _seed(root, profile, statuses)
            page, _seen = _open(browser, f"{cockpit['url']}{URL_PATH}", scheme)
            try:
                if name == "echec":
                    page.locator(f"{LAUNCH} [data-role='launch-resume']").click()
                    page.wait_for_selector(f"{LAUNCH} [data-role='step-error']", timeout=60_000)
                page.locator(LAUNCH).scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.locator(LAUNCH).screenshot(path=str(out / f"p45-lancer-{name}-{word}.png"))
            finally:
                page.context.close()
                _reset(root)
    assert len(list(out.glob("p45-*.png"))) == 4
