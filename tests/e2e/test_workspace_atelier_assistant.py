"""Atelier de spec, lot 5 (#1197, #1342) : l'assistant qui guide le cadrage, contre un faux agent ACP.

Le serveur du cockpit lance ``tests/fixtures/acp/atelier_agent.py`` à la place de l'agent de l'hôte : il fait ce que le
skill ``grimoire-cadrage`` demande (consigner la réponse, poser la question suivante, déposer une suggestion), sans modèle.
Le navigateur est le vrai. Chaque preuve est lue deux fois : à l'écran, et dans ce que l'agent a *reçu* (son journal).

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 et 1024x768 ; sans la variable, elles sont ignorées.
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

from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier, workspace_atelier_assistant

AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "atelier_agent.py"
APPEAR_MS = 30_000
PARA = "Le bouton maintenu sert à parler tant que la touche est tenue."


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


def _init(tmp_path_factory: pytest.TempPathFactory, label: str, *, flag: bool) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp(label) / "projet-assistant"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-assistant", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    slug = ""
    if flag:
        workspace_atelier.set_atelier(root, True)
        slug = new_chantier(root, "Dicter une idée").slug
        path = root / "_grimoire" / "cadrage" / slug / "02-brainstorm.md"
        path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — pistes.", PARA), encoding="utf-8")
    return root, slug


def _serve(root: Path, tmp_path_factory: pytest.TempPathFactory, extra_env: dict[str, str]) -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-asst"))
    env["GRIMOIRE_CHAT_GRACE_SECONDS"] = "0"
    env["NO_COLOR"] = "1"
    env.update(extra_env)
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture(scope="module")
def on_root(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    return _init(tmp_path_factory, "asst-on", flag=True)


@pytest.fixture(scope="module")
def agent_log(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("asst-agent") / "agent-log.jsonl"


@pytest.fixture(scope="module")
def with_agent(on_root: tuple[Path, str], agent_log: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    env = {"GRIMOIRE_CHAT_AGENT_COMMAND": f"{sys.executable} {AGENT} {agent_log}"}
    yield from _serve(on_root[0], tmp_path_factory, env)


@pytest.fixture(scope="module")
def without_agent(on_root: tuple[Path, str], tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    from grimoire.hosts.acp_hosts import HOST_ENV, KANBAN

    yield from _serve(on_root[0], tmp_path_factory, {HOST_ENV: KANBAN, "GRIMOIRE_CHAT_AGENT_COMMAND": ""})


@pytest.fixture(scope="module")
def flag_off(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root, _slug = _init(tmp_path_factory, "asst-off", flag=False)
    yield from _serve(root, tmp_path_factory, {})


def _open(browser: Browser, url: str, hash_: str, viewport: tuple[int, int] = (1440, 900)) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
    try:
        yield page
    finally:
        context.close()


def _capture(page: Page, name: str) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}.png"), full_page=False)


def _prompts(log: Path) -> list[str]:
    if not log.is_file():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        msg = json.loads(line)
        if msg.get("method") == "session/prompt":
            out.append("".join(p.get("text", "") for p in msg["params"]["prompt"]))
    return out


def _state(root: Path, slug: str) -> dict[str, object]:
    return dict(json.loads((root / "_grimoire" / "cadrage" / slug / "entretien.json").read_text(encoding="utf-8")))


def _question(page: Page) -> object:
    return page.locator("[data-role='assistant-question']")


def test_parcours_de_l_assistant(
    browser: Browser, with_agent: str, on_root: tuple[Path, str], agent_log: Path
) -> None:
    root, slug = on_root
    for page in _open(browser, with_agent, f"#atelier?chantier={slug}"):
        # Le fil du chat est ouvert d'abord (le flux écoute), puis la page revient sur l'onglet du dock plus bas.
        page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
        page.locator("[data-dock-tab='orchestrateur']").click()
        page.wait_for_selector(".chat-input")
        page.evaluate("() => window.GrimoireWorkspace.setAtelier(false)")
        # 1. Aucun entretien : l'invitation et « Démarrer le cadrage ».
        page.locator("[data-role='assistant-invite']").wait_for(timeout=APPEAR_MS)
        _capture(page, "01-invitation-1440x900")
        page.locator("[data-act='start']").click()
        # L'assistant parle en premier : la première question arrive dans la barre, avec l'angle et l'avancement.
        _question(page).wait_for(timeout=APPEAR_MS)
        assert "Qui utilisera cela" in _question(page).inner_text()  # type: ignore[attr-defined]
        assert "Utilisateurs et usage réel" in _question(page).inner_text()  # type: ignore[attr-defined]
        assert "0 angle sur" in page.locator("[data-role='assistant']").inner_text()
        first = _prompts(agent_log)[0]
        assert first.startswith(workspace_atelier_assistant.GESTES["demarrer"])  # la consigne fixe, choisie côté serveur
        assert f"chantier: {slug}\nphase: brief" in first
        assert PARA not in first  # le texte du document ne part jamais dans la consigne
        _capture(page, "02-question-1440x900")

        # 2. Une réponse : consignée dans la feuille, puis la question suivante.
        page.locator(".at-asst-area").fill("Les mainteneurs du kit.")
        page.locator(".at-asst-area").press("Enter")
        page.locator(".at-sheet", has_text="Les mainteneurs du kit.").wait_for(timeout=APPEAR_MS)
        page.locator("[data-role='assistant-question']", has_text="À quoi voit-on").wait_for(timeout=APPEAR_MS)
        assert "1 angle sur" in page.locator("[data-role='assistant']").inner_text()
        assert _prompts(agent_log)[1].startswith("Les mainteneurs du kit.\n\n[Contexte de l'Atelier")
        _capture(page, "03-reponse-consignee-1440x900")

        # 3. « Je ne sais pas » : une hypothèse consignée, la question suivante.
        page.locator("[data-act='unknown']").click()
        page.locator("[data-role='assistant-question']", has_text="exclu").wait_for(timeout=APPEAR_MS)
        angles = _state(root, slug)["angles"]
        assert angles["criteres"]["answers"][0].startswith("À valider (je ne sais pas)")  # type: ignore[index]
        asked = workspace_atelier_assistant.GESTES["ne-sais-pas"].format(angle="criteres")
        assert _prompts(agent_log)[2] == asked + "\n\n" + workspace_atelier_assistant.brief(slug, "brief")

        # 4. « Pause » arrête le tour en cours ; « Reprendre » demande la suite.
        page.locator(".at-asst-area").fill("lent")
        page.locator("[data-act='answer']").click()
        page.locator("[data-role='assistant-busy']").wait_for(timeout=APPEAR_MS)
        _capture(page, "04-pause-1440x900")
        page.locator("[data-act='pause']").click()
        page.locator("[data-act='resume']").wait_for(timeout=APPEAR_MS)
        page.locator("[data-act='resume']").click()
        _question(page).wait_for(timeout=APPEAR_MS)

        # 5. « Sans objet » écarte l'angle avec sa raison.
        assert "Hors périmètre" in _question(page).inner_text()  # type: ignore[attr-defined]
        page.locator("[data-act='skip']").click()
        # L'angle écarté est celui que la barre affichait au clic (« Hors périmètre »), nommé dans la consigne fixe.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _state(root, slug)["angles"].get("hors-perimetre", {}).get("state") == "ecarte":  # type: ignore[attr-defined]
                break
            time.sleep(0.2)
        assert _state(root, slug)["angles"]["hors-perimetre"]["state"] == "ecarte"  # type: ignore[index]
        assert "sur l'angle hors-perimetre" in _prompts(agent_log)[-1]

        # 5 bis. Une question sans angle nommé : on peut répondre, mais les deux gestes qui visent un angle sont fermés.
        page.locator(".at-asst-area").fill("sans marqueur")
        page.locator("[data-act='answer']").click()
        page.locator("[data-role='assistant-noangle']").wait_for(timeout=APPEAR_MS)
        assert page.locator("[data-act='unknown']").is_disabled() and page.locator("[data-act='skip']").is_disabled()
        assert "Sans objet" in page.locator("[data-act='skip']").inner_text()
        _capture(page, "04b-sans-angle-1440x900")
        page.locator(".at-asst-area").fill("Les risques sont faibles.")
        page.locator("[data-act='answer']").click()
        page.locator("[data-role='assistant-noangle']").wait_for(state="detached", timeout=APPEAR_MS)
        assert "Sans objet : " in page.locator("[data-act='skip']").inner_text()  # le libellé rappelle l'angle visé

        # 6. « Challenger ce bloc » : une suggestion sous le bloc, par l'outil existant.
        block = page.locator(".at-blk", has_text="Le bouton maintenu").first
        block.hover()
        block.locator("[data-act='challenge']").click()
        _capture(page, "05-challenger-1440x900")
        block.locator("[data-posture='sceptique']").click()
        page.locator(".at-sugg").first.wait_for(timeout=APPEAR_MS)
        assert "Version plus simple proposée" in page.locator(".at-sugg").first.inner_text()
        assert "posture « sceptique »" in _prompts(agent_log)[-1]
        assert PARA not in _prompts(agent_log)[-1]  # l'agent lit le bloc lui-même : la consigne ne le recopie pas
        _capture(page, "06-suggestion-1440x900")

        # 7. Le fil du chat dit le chantier visé.
        page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
        page.locator("[data-dock-tab='orchestrateur']").click()
        page.locator(".chat-chantier").first.wait_for(timeout=APPEAR_MS)
        assert "Chantier : Dicter une idée" in page.locator(".chat-chantier").first.inner_text()

    for page in _open(browser, with_agent, f"#atelier?chantier={slug}", (1024, 768)):
        page.locator("[data-role='assistant']").wait_for(timeout=APPEAR_MS)
        _capture(page, "07-barre-1024x768")


def test_le_chat_libre_porte_le_chantier(browser: Browser, with_agent: str, on_root: tuple[Path, str], agent_log: Path) -> None:
    _root, slug = on_root
    before = len(_prompts(agent_log))
    for page in _open(browser, with_agent, f"#atelier?chantier={slug}"):
        page.evaluate("() => window.GrimoireWorkspace.setAtelier(true)")
        page.locator("[data-dock-tab='orchestrateur']").click()
        page.wait_for_selector(".chat-input")
        page.locator(".chat-input").fill("Une remarque libre depuis le chat")
        page.locator(".chat-input").press("Enter")
        deadline = time.monotonic() + 30
        while len(_prompts(agent_log)) <= before and time.monotonic() < deadline:
            time.sleep(0.2)
    sent = _prompts(agent_log)[before]
    assert sent.startswith("Une remarque libre depuis le chat\n\n[Contexte de l'Atelier")
    assert f"chantier: {slug}\n" in sent


def test_sans_agent_d_hote_la_barre_le_dit_et_donne_la_commande(
    browser: Browser, without_agent: str, on_root: tuple[Path, str]
) -> None:
    _root, slug = on_root
    for page in _open(browser, without_agent, f"#atelier?chantier={slug}"):
        off = page.locator("[data-role='assistant-off']")
        off.wait_for(timeout=APPEAR_MS)
        assert "pas joignable" in off.inner_text()
        assert f"grimoire cadrage interview --chantier {slug}" in page.locator("[data-role='assistant']").inner_text()
        assert page.locator("[data-act='start']").count() == 0  # pas de bouton mort
        _capture(page, "08-sans-agent-1440x900")


def test_drapeau_eteint_rien(flag_off: str) -> None:
    try:
        urllib.request.urlopen(f"{flag_off}/api/workspace/atelier/chantiers/x/entretien", timeout=10)  # noqa: S310
        raise AssertionError("la route répond alors que le drapeau est éteint")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    request = urllib.request.Request(  # noqa: S310 — loopback de test
        f"{flag_off}/api/workspace/atelier/chantiers/x/assistant", data=b'{"geste":"demarrer"}',
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)  # noqa: S310 — loopback de test
        raise AssertionError("le geste part alors que le drapeau est éteint")
    except urllib.error.HTTPError as exc:
        assert exc.code in (403, 404)


@pytest.fixture(scope="module")
def model_on_without_key(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Option du modèle du kit allumée, AUCUNE clé (HOME isolé, variable vide) : aucun appel réseau n'est possible."""
    root, slug = _init(tmp_path_factory, "asst-model", flag=True)
    context = root / "project-context.yaml"
    text = context.read_text(encoding="utf-8").replace("atelier: true", "atelier: true\n  atelier_model:\n    enabled: true", 1)
    assert "atelier_model" in text
    context.write_text(text, encoding="utf-8")
    home = tmp_path_factory.mktemp("asst-home")
    yield from ((url, slug) for url in _serve(root, tmp_path_factory, {
        "HOME": str(home), "USERPROFILE": str(home), "ANTHROPIC_API_KEY": "",
        "GRIMOIRE_CHAT_AGENT_COMMAND": f"{sys.executable} {AGENT} {home / 'log.jsonl'}"}))


def test_modele_du_kit_indique_sans_cle_que_la_fonction_est_indisponible(
    browser: Browser, model_on_without_key: tuple[str, str]
) -> None:
    url, slug = model_on_without_key
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        note = page.locator("[data-role='engine-note']")
        note.wait_for(timeout=APPEAR_MS)
        assert "sans modèle" in note.inner_text() and "aucune clé" in note.inner_text()
        block = page.locator(".at-blk", has_text="Le bouton maintenu").first
        block.hover()
        block.locator("[data-act='challenge']").click()
        assert "sans modèle" in block.locator("[data-posture='sceptique']").inner_text()
        off = block.locator("[data-role='model-off']")
        off.wait_for(timeout=APPEAR_MS)
        assert "aucune clé" in off.inner_text()
        assert block.locator("[data-geste]").count() == 0  # pas de bouton mort
        _capture(page, "09-modele-sans-cle-1440x900")


def test_modele_du_kit_eteint_par_defaut_aucun_selecteur(browser: Browser, with_agent: str, on_root: tuple[Path, str]) -> None:
    _root, slug = on_root
    for page in _open(browser, with_agent, f"#atelier?chantier={slug}"):
        assert "sans modèle du kit" in page.locator("[data-role='engine-note']").inner_text()
        block = page.locator(".at-blk", has_text="Le bouton maintenu").first
        block.hover()
        block.locator("[data-act='challenge']").click()
        assert block.locator("[data-role='engine']").count() == 0 and block.locator("[data-role='model-off']").count() == 0
