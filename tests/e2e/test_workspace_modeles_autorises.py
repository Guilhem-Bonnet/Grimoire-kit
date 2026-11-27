"""Page « Modèles autorisés » (épique #1477, lot B1), contre un VRAI `grimoire serve`.

Ollama est simulé par un petit serveur HTTP local (``OLLAMA_HOST`` du serveur de test) : aucun modèle n'est appelé, aucun
service distant n'est joint (les clés ``*_API_KEY`` sont retirées de l'environnement du serveur, le fournisseur d'essai est
inconnu de la sonde de capacités). Chaque attente porte sur l'état voulu, jamais sur une lecture instantanée.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures de relecture ; sans la variable le test de capture ne fait rien.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from grimoire.missions import mandate as md
from grimoire.providers import direct_use_settings as ds

from .conftest import _alive, _free_port, _wait_ready

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
providers:
  - id: "ollama"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "ollama run {model} {prompt}"
    models:
      - {id: "llama3.2:3b", tier: "cheap"}
      - {id: "qwen2.5:14b", tier: "mid"}
  - id: "fakecloud"
    enabled: true
    provider_type: "hosted"
    currency: "api"
    invocation: "fakecloud-cli {model} {prompt}"
    models:
      - {id: "fake-strong", tier: "strong"}
"""
TAGS = json.dumps(
    {
        "models": [
            {"name": "llama3.2:3b", "details": {"parameter_size": "3.2B"}},
            {"name": "qwen2.5:14b", "details": {"parameter_size": "14.8B"}},
            {"name": "mistral:7b", "details": {"parameter_size": "7.2B"}},
        ]
    }
).encode()


class _Tags(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = TAGS if self.path == "/api/tags" else b"{}"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _key() -> str:
    md.ensure_control_key()
    return md.control_key_path().read_text(encoding="utf-8").strip()


@pytest.fixture(scope="module")
def served_models(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Tags)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    root = tmp_path_factory.mktemp("modeles-autorises") / "projet-modeles"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    created = subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-modeles", "--backend", "local"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")
    subprocess.run(
        [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    (root / "_grimoire" / "standard" / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    shutil.rmtree(root / "_grimoire-runtime-output" / "ledger", ignore_errors=True)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-modeles"))
    env["OLLAMA_HOST"] = f"127.0.0.1:{fake.server_address[1]}"
    env["NO_COLOR"] = "1"
    # Un faux binaire `ollama` propre au test, hors de la racine du projet : « Ollama local » exige un binaire que le PATH
    # résout, et ni la CI ni toute machine n'en a forcément un. La règle du produit n'est pas assouplie.
    fakebin = tmp_path_factory.mktemp("fakebin-modeles")
    (fakebin / "ollama").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fakebin / "ollama").chmod(0o755)
    env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        fake.shutdown()
        fake.server_close()
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


@pytest.fixture
def models_page(browser: Browser, served_models: tuple[str, Path]) -> Iterator[Page]:
    url, root = served_models
    # Un état de départ propre à chaque test : rien d'autorisé, registre d'origine (mistral « détecté, non configuré »).
    ds.settings_path(root).unlink(missing_ok=True)
    (root / "_grimoire" / "standard" / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="dark")
    page = context.new_page()
    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('modeles')")
    page.wait_for_selector("[data-role='modeles-autorises']", timeout=30_000)
    try:
        yield page
    finally:
        context.close()


def _model(page: Page, key: str):  # type: ignore[no-untyped-def]
    return page.locator(f"[data-role='model'][data-key='{key}']")


def _usage(page: Page, usage: str):  # type: ignore[no-untyped-def]
    return page.locator(f"[data-role='usage'][data-usage='{usage}']")


def _confirm_with(page: Page, key: str) -> None:
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_be_visible()
    dialog.locator("input[type='password']").fill(key)
    dialog.get_by_role("button", name="Enregistrer").click()


def _stored(root: Path) -> dict[str, list[tuple[str, int | None]]]:
    settings = ds.read_settings(root)
    return {u: [(a.key, a.daily_cap_tokens) for a in auths] for u, auths in settings.allowed.items() if auths}


def test_la_page_liste_les_modeles_detectes_avec_leur_etat_et_l_encart_vide(models_page: Page) -> None:
    page = models_page
    note = page.locator("[data-role='empty-note']")
    expect(note).to_contain_text("Aucun modèle autorisé : Grimoire fonctionne sans modèle.")
    expect(note).to_contain_text("Autoriser un modèle local ne fait rien sortir de ta machine.")

    local = _model(page, "ollama:llama3.2:3b")
    expect(local).to_contain_text("Disponible")
    expect(local).to_contain_text("Rien ne sort de la machine")
    expect(local).to_contain_text("Gratuit (local)")
    expect(local).to_contain_text("Niveau : léger (registre)")

    cloud = _model(page, "registre:fakecloud/fake-strong")
    expect(cloud).to_contain_text("Clé absente")
    expect(cloud).to_contain_text("Le texte peut sortir de la machine")
    expect(cloud).to_contain_text("Payant")
    # La clé ne se saisit pas ici : la commande du terminal est montrée, aucun champ de saisie de clé n'existe.
    expect(cloud).to_contain_text("grimoire providers set-key fakecloud FAKECLOUD_API_KEY")
    expect(cloud.locator("input")).to_have_count(0)

    extra = _model(page, "ollama:mistral:7b")
    expect(extra).to_contain_text("Détecté, non configuré")
    expect(extra.get_by_role("button", name="Ajouter mistral:7b au registre du projet")).to_be_visible()
    for usage in ("conversation", "brainstorm", "redaction", "autres"):
        expect(_usage(page, usage)).to_be_visible()
        expect(_usage(page, usage).locator("[data-role='decision']")).to_contain_text("sans modèle")


def test_autoriser_demande_la_cle_de_controle_puis_l_ordre_et_le_plafond_sont_enregistres(
    models_page: Page, served_models: tuple[str, Path]
) -> None:
    page = models_page
    _url, root = served_models
    conversation = _usage(page, "conversation")
    conversation.get_by_role("switch", name="Autoriser llama3.2:3b pour Conversation (cadrage)").check()
    conversation.get_by_role("switch", name="Autoriser qwen2.5:14b pour Conversation (cadrage)").check()
    expect(conversation.locator(".ma-row.on")).to_have_count(2)
    assert _stored(root) == {}  # rien n'est écrit tant que « Enregistrer » n'est pas confirmé

    # Ordre : monter le second à la souris ; puis Alt+Flèche haut au clavier (le premier redescend).
    conversation.get_by_role("button", name="Monter qwen2.5:14b dans l’ordre de préférence").click()
    expect(conversation.locator(".ma-row.on .ma-row-name").first).to_have_text("qwen2.5:14b")
    conversation.get_by_role("switch", name="Autoriser llama3.2:3b pour Conversation (cadrage)").focus()
    page.keyboard.press("Alt+ArrowUp")
    expect(conversation.locator(".ma-row.on .ma-row-name").first).to_have_text("llama3.2:3b")
    page.keyboard.press("Alt+ArrowDown")
    expect(conversation.locator(".ma-row.on .ma-row-name").first).to_have_text("qwen2.5:14b")

    conversation.get_by_label("Plafond par jour de llama3.2:3b (jetons, facultatif)").fill("12x")
    expect(conversation.get_by_role("button", name="Enregistrer…")).to_be_disabled()
    conversation.get_by_label("Plafond par jour de llama3.2:3b (jetons, facultatif)").fill("5000")
    conversation.get_by_role("button", name="Enregistrer…").click()

    # Une clé fausse est refusée : le dialogue revient avec son erreur, rien n'est écrit.
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_contain_text("Aucun de ces modèles ne fait sortir de texte")
    _confirm_with(page, "fausse-cle")
    expect(page.locator("dialog.pi-dialog .pi-error")).to_contain_text("Clé refusée")
    assert _stored(root) == {}

    _confirm_with(page, _key())
    expect(page.locator("dialog.pi-dialog")).to_have_count(0)
    expect(page.locator("[data-role='empty-note']")).to_have_count(0)
    expect(_usage(page, "conversation").locator("[data-role='decision']")).to_contain_text("Maintenant : qwen2.5:14b")
    assert _stored(root) == {"conversation": [("ollama:qwen2.5:14b", None), ("ollama:llama3.2:3b", 5000)]}

    # Relu de l'API après rechargement : l'ordre et le plafond tiennent.
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.evaluate("() => window.GrimoireWorkspace.goto('modeles')")
    again = _usage(page, "conversation")
    expect(again.locator(".ma-row.on .ma-row-name").first).to_have_text("qwen2.5:14b")
    expect(again.get_by_label("Plafond par jour de llama3.2:3b (jetons, facultatif)")).to_have_value("5000")


def test_brainstorm_ecarte_le_modele_trop_leger_et_la_trace_le_dit(models_page: Page) -> None:
    page = models_page
    brain = _usage(page, "brainstorm")
    brain.get_by_role("switch", name="Autoriser llama3.2:3b pour Brainstorm").check()
    brain.get_by_role("button", name="Enregistrer…").click()
    _confirm_with(page, _key())
    expect(page.locator("dialog.pi-dialog")).to_have_count(0)
    decision = _usage(page, "brainstorm").locator("[data-role='decision']")
    expect(decision).to_contain_text("trop léger pour brainstormer")
    expect(_usage(page, "brainstorm").locator(".ma-trace")).to_contain_text("llama3.2:3b (écarté)")


def test_retirer_le_dernier_modele_remet_l_encart_sans_modele(models_page: Page, served_models: tuple[str, Path]) -> None:
    page = models_page
    _url, root = served_models
    autres = _usage(page, "autres")
    autres.get_by_role("switch", name="Autoriser llama3.2:3b pour Autres usages").check()
    autres.get_by_role("button", name="Enregistrer…").click()
    _confirm_with(page, _key())
    expect(page.locator("[data-role='empty-note']")).to_have_count(0)
    assert _stored(root) == {"autres": [("ollama:llama3.2:3b", None)]}
    _usage(page, "autres").get_by_role("switch", name="Autoriser llama3.2:3b pour Autres usages").uncheck()
    _usage(page, "autres").get_by_role("button", name="Enregistrer…").click()
    _confirm_with(page, _key())
    expect(page.locator("[data-role='empty-note']")).to_be_visible()
    assert _stored(root) == {}


def test_ajouter_un_modele_detecte_passe_par_la_cle_de_controle(models_page: Page) -> None:
    page = models_page
    extra = _model(page, "ollama:mistral:7b")
    extra.get_by_role("button", name="Ajouter mistral:7b au registre du projet").click()
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_contain_text("rien ne sort de la machine")
    dialog.locator("input[type='password']").fill(_key())
    dialog.get_by_role("button", name="Ajouter").click()
    expect(page.locator("dialog.pi-dialog")).to_have_count(0)
    expect(_model(page, "ollama:mistral:7b")).not_to_contain_text("Détecté, non configuré")
    expect(_model(page, "ollama:mistral:7b").get_by_role("button", name="Ajouter mistral:7b au registre du projet")).to_have_count(0)
    # Remis dans le registre pour que les autres tests retrouvent « détecté, non configuré » : la page se relit.


def test_verifier_maintenant_relit_l_etat_sur_geste_explicite(models_page: Page) -> None:
    page = models_page
    page.get_by_role("button", name="Vérifier maintenant").click()
    sub = page.locator("[data-role='modeles-autorises'] .space-sub")
    expect(sub).to_contain_text("Relu à")
    expect(sub).to_contain_text("aucune clé transmise")


def test_verifier_les_connexions_demande_la_cle_et_dit_ce_qui_part(models_page: Page) -> None:
    page = models_page
    page.get_by_role("button", name="Vérifier les connexions…").click()
    dialog = page.locator("dialog.pi-dialog")
    expect(dialog).to_contain_text("Aucune clé d’API ne sera envoyée")
    expect(dialog).to_contain_text("aucun modèle n’est appelé")
    dialog.locator("input[type='password']").fill("fausse-cle")
    dialog.get_by_role("button", name="Vérifier").click()
    expect(page.locator("dialog.pi-dialog .pi-error")).to_contain_text("Clé refusée")
    page.locator("dialog.pi-dialog input[type='password']").fill(_key())
    page.locator("dialog.pi-dialog").get_by_role("button", name="Vérifier").click()
    expect(page.locator("[data-role='modeles-autorises'] .space-sub")).to_contain_text("Connexions vérifiées")


def test_un_plafond_sur_un_modele_non_mesure_est_dit_sans_effet(models_page: Page) -> None:
    page = models_page
    conversation = _usage(page, "conversation")
    conversation.get_by_role("switch", name="Autoriser llama3.2:3b pour Conversation (cadrage)").check()
    expect(conversation.locator("[data-role='cap-unmeasured']")).to_contain_text("consommation non mesurée pour ce modèle")


def test_la_page_tient_a_480_px_sans_defilement_horizontal(browser: Browser, served_models: tuple[str, Path]) -> None:
    url, _root = served_models
    context = browser.new_context(viewport={"width": 480, "height": 900}, reduced_motion="reduce", color_scheme="dark")
    page = context.new_page()
    try:
        page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready='1']", timeout=30_000)
        page.evaluate("() => window.GrimoireWorkspace.goto('modeles')")
        page.wait_for_selector("[data-role='modeles-autorises']", timeout=30_000)
        expect(page.locator("[data-role='usage']")).to_have_count(4)
        overflow = page.evaluate(
            """() => { const w = document.querySelector("[data-role='modeles-autorises']");
              const box = w.closest('main, [data-role=canvas], #canvas') || w.parentElement;
              return [document.documentElement.scrollWidth - innerWidth, w.scrollWidth - w.clientWidth, box.scrollWidth - box.clientWidth]; }"""
        )
        assert max(overflow) <= 1, overflow
    finally:
        context.close()


def test_captures_de_relecture(browser: Browser, served_models: tuple[str, Path]) -> None:
    target = os.environ.get("GRIMOIRE_E2E_CAPTURES", "").strip()
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    url, root = served_models
    (root / "_grimoire" / "standard" / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")

    from grimoire.providers.availability import detect

    # Même registre, même Ollama simulé : les empreintes sont celles que le serveur calcule.
    known = detect(
        root, use_cache=False, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: TAGS, acp=lambda _p, _r: (True, "")
    ).by_key()

    def authorize(usage: str, models: list[dict[str, object]]) -> None:
        ds.write_usage(root, usage, models, known)

    for state in ("vide", "regle"):
        ds.settings_path(root).unlink(missing_ok=True)
        if state == "regle":
            authorize("conversation", [{"key": "ollama:qwen2.5:14b"}, {"key": "ollama:llama3.2:3b", "daily_cap_tokens": 5000}])
            authorize("brainstorm", [{"key": "ollama:llama3.2:3b"}])
        for scheme in ("dark", "light"):
            for width, height in ((1440, 900), (480, 900)):
                context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
                page = context.new_page()
                try:
                    page.goto(f"{url}/workspace/index.html", wait_until="domcontentloaded")
                    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
                    page.evaluate("() => window.GrimoireWorkspace.goto('modeles')")
                    page.wait_for_selector("[data-role='modeles-autorises']", timeout=30_000)
                    expect(page.locator("[data-role='usage']")).to_have_count(4)
                    page.screenshot(path=str(out / f"modeles-autorises-{scheme}-{width}-{state}-haut.png"))
                    page.locator("[data-role='usage'][data-usage='conversation']").scroll_into_view_if_needed()
                    page.locator("[data-role='usage'][data-usage='brainstorm']").scroll_into_view_if_needed()
                    page.screenshot(path=str(out / f"modeles-autorises-{scheme}-{width}-{state}-usages.png"))
                finally:
                    context.close()
    ds.settings_path(root).unlink(missing_ok=True)
