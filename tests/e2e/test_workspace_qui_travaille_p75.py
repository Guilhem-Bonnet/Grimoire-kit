"""Travail › qui travaille, avec quoi, à quel coût (P75, GAO-p75-qui-trav-001).

Ce que la demande d'origine n'avait pas :

- une carte en cours ne disait ni le palier RÉEL du modèle, ni l'effort, ni les
  skills que la session invoque, ni ses tokens ;
- la colonne Fait ne disait ni le total de tokens ni un coût lisible en CAD ;
- aucun endroit pour régler le taux de conversion.

Chaque test relit le DOM rendu par ``grimoire serve`` sur un projet réel :
les tokens viennent des traces repliées (``fold_claude_transcript``), le
modèle/l'effort/les skills du journal Claude Code (``CLAUDE_CONFIG_DIR``),
le taux des options du projet (``project-context.yaml``).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

RUNNING_TITLE = "Brancher le palier réel sur la carte"
MUTE_TITLE = "Tâche tenue sans journal rapporté"
DONE_TITLE = "Carte close au coût replié"
DONE_UNKNOWN_TITLE = "Carte close sans source d'usage vérifiée"
RUNNING_SESSION = "41c89d23-db0e-4d2a-8888-000000000075"
MUTE_SESSION = "41c89d23-db0e-4d2a-8888-000000000076"
DONE_SESSION = "77bf861f-aaaa-bbbb-cccc-000000000075"


def _assistant_line(message_id: str, tokens_in: int, tokens_out: int, *, skills: tuple[str, ...] = ()) -> str:
    return json.dumps({
        "type": "assistant",
        "timestamp": "2026-10-27T10:00:00Z",
        "effort": "high",
        "sessionId": RUNNING_SESSION,
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5-5",
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": s}} for s in skills],
        },
    })


def seed_project(root: Path, claude_home: Path) -> None:
    """Le projet du scénario P75 — réutilisé par la capture (scratchpad/p75)."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import fold_claude_transcript

    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-p75", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P75 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    now = datetime.now(UTC)
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)

    def journal(sid: str, task_id: str, minutes: int) -> None:
        (runs / f"session-{sid}.json").write_text(
            json.dumps({
                "schema_version": 1, "session_id": sid,
                "started_at": (now - timedelta(minutes=minutes + 30)).isoformat(),
                "updated_at": (now - timedelta(minutes=minutes)).isoformat(),
                "task_id": task_id, "host": "host-claude-code-cli", "rules": {},
            }),
            encoding="utf-8",
        )

    def run_states(tid: str, *states: TaskState, host: str = "host-claude-code-cli") -> None:
        for state in states:
            claim = TaskClaim("kit-dev", host) if state is TaskState.CLAIMED else None
            service.ledger.transition_task(tid, state, actor_id="kit-dev", claim=claim)

    ledger = TraceLedger(root / TRACES_DIR)

    # En cours, journal Claude Code rapporté : palier réel, effort, skills, tokens.
    running = service.add(RUNNING_TITLE, ("palier affiché",), owner="guilhem", actor="t").task.id
    run_states(running, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING)
    service.attach_session(running, RUNNING_SESSION, session_host="claude-code-cli", actor="kit-dev")
    journal(RUNNING_SESSION, running, 2)
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
    transcript = claude_home / "projects" / slug / f"{RUNNING_SESSION}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        _assistant_line("m1", 20_000, 10_000, skills=("grimoire-evidence",)) + "\n"
        + _assistant_line("m2", 10_000, 8_000, skills=("code-review",)) + "\n",
        encoding="utf-8",
    )
    fold_claude_transcript(ledger, transcript, task_id=running)

    # En cours, aucun journal Claude Code : « non rapporté », rien d'inventé.
    mute = service.add(MUTE_TITLE, ("honnêteté affichée",), owner="guilhem", actor="t").task.id
    run_states(mute, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING)
    service.attach_session(mute, MUTE_SESSION, session_host="claude-code-cli", actor="kit-dev")
    journal(MUTE_SESSION, mute, 3)

    # Fait, usage replié : total de tokens + coût approximatif en CAD.
    done = service.add(DONE_TITLE, ("vue livrée",), owner="claude", actor="t").task.id
    run_states(done, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION)
    service.attach_session(done, DONE_SESSION, session_host="claude-code-cli", actor="kit-dev")
    done_transcript = root / "_grimoire-output" / f"transcript-{DONE_SESSION}.jsonl"
    done_transcript.write_text(
        json.dumps({
            "type": "assistant", "timestamp": "2026-10-26T10:00:00Z", "sessionId": DONE_SESSION,
            "message": {
                "id": "d1", "model": "claude-sonnet-5-5",
                "usage": {
                    "input_tokens": 3_000_000, "output_tokens": 100_000,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                },
                "content": [],
            },
        }) + "\n",
        encoding="utf-8",
    )
    fold_claude_transcript(ledger, done_transcript, task_id=done)
    service.ledger.transition_task(done, TaskState.CLOSED, actor_id="kit-dev")

    # Fait, hôte sans source d'usage vérifiée : « coût inconnu » (unavailable).
    unknown = service.add(DONE_UNKNOWN_TITLE, ("honnêteté close",), owner="claude", actor="t").task.id
    run_states(unknown, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION, host="host-cursor")
    service.ledger.transition_task(unknown, TaskState.CLOSED, actor_id="kit-dev")


@pytest.fixture(scope="module")
def p75_dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    claude_home = tmp_path_factory.mktemp("claude-config-p75")
    root = tmp_path_factory.mktemp("qui-travaille-p75") / "projet-p75"
    seed_project(root, claude_home)
    return root, claude_home


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
    raise TimeoutError(f"`grimoire serve` n'a pas répondu sur :{port}")


@pytest.fixture(scope="module")
def p75_url(p75_dirs: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root, claude_home = p75_dirs
    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p75"))
    env["CLAUDE_CONFIG_DIR"] = str(claude_home)
    env["GRIMOIRE_PULSE_SECONDS"] = "1"
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


@pytest.fixture
def page(browser: Browser, p75_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p75_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    opened.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    opened.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    opened.wait_for_selector(".ex-card:visible", timeout=APPEAR_MS)
    try:
        yield opened
    finally:
        context.close()


def _card(page: Page, title_fragment: str):  # type: ignore[no-untyped-def]
    return page.locator(".ex-card").filter(has_text=title_fragment).first


def test_la_carte_en_cours_dit_palier_reel_effort_tokens_et_skills(page: Page) -> None:
    card = _card(page, "palier réel")
    line = card.locator("[data-role='card-session']")
    expect(line).to_have_count(1)
    expect(line.locator("[data-role='work-model']")).to_have_text("sonnet")
    assert "palier réel" in (line.locator("[data-role='work-model']").get_attribute("title") or "")
    # Effort, tokens en direct et derniers skills : la ligne de travail de la carte.
    live = card.locator("[data-role='work-live']")
    expect(live).to_have_count(1)
    expect(live.locator("[data-role='work-effort']")).to_have_text("effort high")
    expect(live.locator("[data-role='work-tokens']")).to_have_text("48 k tokens")
    skills = live.locator("[data-role='work-skills']")
    assert "code-review" in skills.inner_text()


def test_une_session_sans_journal_rapporte_n_ecrit_aucune_absence_sur_la_carte(page: Page) -> None:
    card = _card(page, "sans journal rapporté")
    line = card.locator("[data-role='card-session']")
    assert line.locator("[data-role='work-model']").count() == 0, "aucun palier deviné sur la ligne session"
    # P91 : ni « modèle non rapporté » ni « effort non rapporté » sur la carte (ils vivent dans l'inspecteur).
    assert "non rapporté" not in card.inner_text()
    live = card.locator("[data-role='work-live']")
    assert live.count() == 0


def test_l_inspecteur_liste_persona_modele_effort_skills_et_tokens(page: Page) -> None:
    card = _card(page, "palier réel")
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    block = inspector.locator("[data-role='work-block']")
    block.wait_for(timeout=APPEAR_MS)
    body = block.inner_text()
    assert "persona : kit-dev" in body
    assert "hôte : Claude Code" in body
    assert "modèle réel : claude-sonnet-5-5 (palier sonnet)" in body
    assert "effort : high" in body
    assert "skills invoqués : grimoire-evidence, code-review" in body
    assert "tokens : 48000 (30000 in / 18000 out, cache 0 lu / 0 écrit, 2 appel(s))" in body
    # Coût exact en CAD, taux par défaut : 0,24 USD × 1,40 = 0,34 $ CAD.
    assert "≈ 0,34 $ CAD" in body


def test_l_inspecteur_ecrit_le_statut_d_usage_en_toutes_lettres(page: Page) -> None:
    card = _card(page, "sans journal rapporté")
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    block = inspector.locator("[data-role='work-block']")
    block.wait_for(timeout=APPEAR_MS)
    assert "aucun usage replié" in block.inner_text()
    assert "(none)" in block.inner_text()


def test_la_colonne_fait_affiche_total_et_cad_ou_cout_inconnu(page: Page) -> None:
    done = _card(page, "coût replié")
    assert done.evaluate("(n) => n.closest('.ex-col').dataset.group") == "done"
    total = done.locator("[data-role='work-total']")
    expect(total).to_have_count(1)
    # 3,1 M tokens, 7 USD exacts × 1,40 (défaut) = 9,80 $ CAD.
    expect(total).to_have_text("total 3100 k tokens · ≈ 9,80 $ CAD")
    assert total.get_attribute("data-usage-source") == "claude-code-transcript"
    assert "défaut du kit" in (total.get_attribute("title") or "")
    unknown = _card(page, "sans source d'usage").locator("[data-role='work-total']")
    expect(unknown).to_have_text("coût inconnu")
    assert unknown.get_attribute("data-usage-source") == "unavailable"
    assert "journal d'usage vérifié" in (unknown.get_attribute("title") or "")


def test_le_taux_cad_se_change_dans_configuration_et_la_colonne_fait_suit(page: Page) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "fournisseurs")
    options = page.locator("[data-role='project-options']")
    options.wait_for(timeout=APPEAR_MS)
    source = options.locator("[data-role='options-source']")
    expect(source).to_contain_text("Défaut du kit")
    rate = options.locator("[data-role='usd-cad-input']")
    rate.wait_for(timeout=APPEAR_MS)
    expect(rate).to_have_value("1.4")
    rate.fill("2")
    options.locator("[data-role='usd-cad-save']").click()
    expect(options.locator("[data-role='usd-cad-status']")).to_contain_text("taux enregistré")
    expect(source).to_contain_text("déclaré par le projet", ignore_case=True)
    # Retour au board : la colonne Fait applique le nouveau taux (7 USD × 2 = 14).
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    page.wait_for_selector(".ex-card:visible", timeout=APPEAR_MS)
    total = _card(page, "coût replié").locator("[data-role='work-total']")
    expect(total).to_have_text("total 3100 k tokens · ≈ 14,00 $ CAD")


def take_capture(page: Page, out: Path) -> None:
    """Capture 1440×900 claire du board — appelée par scratchpad/p75/capture.py."""
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out))


def _usage_payload(url: str) -> dict[str, Any]:
    assert url.startswith("http://127.0.0.1:")  # loopback du fixture, jamais un schéma file:
    with urllib.request.urlopen(f"{url}/api/workspace/tasks", timeout=5) as resp:  # noqa: S310
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return data


def test_le_payload_sert_le_meme_travail_aux_deux_surfaces(p75_url: str) -> None:
    """Garde serveur : `work` est posé par `annotate_cards`, board projet compris."""
    tasks = _usage_payload(p75_url)["tasks"]
    by_title = {t["title"]: t for t in tasks}
    running = by_title[RUNNING_TITLE]["work"]
    assert running["session"]["tier"] == "sonnet"
    assert running["usage"]["usage_source"] == "claude-code-transcript"
    unknown = by_title[DONE_UNKNOWN_TITLE]["work"]
    assert unknown["usage"]["usage_source"] == "unavailable"
    assert unknown["cost_cad"] is None
