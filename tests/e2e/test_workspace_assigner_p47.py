"""Travail › panneau « Assigner une session » (P47 lot 2, GAO-p47-assigne-001).

L'inspecteur d'une tâche lançable (prête ou proposée) offre « Assigner une
session… » : une section — jamais une modale — qui rend le plan de la vraie
route ``GET /api/workspace/tasks/<id>/launch-plan`` (P47 lot 1). Modèle avec sa
classe de vérifiabilité, worktree et branche que le lanceur créerait, les cinq
vérifications du préflight avec leurs verdicts, le bouton « Lancer la session »
— réel depuis le lot 3 : l'empreinte du plan part dans le corps et le serveur
lance par le lanceur du tick, toutes gardes comprises ; ici la politique
d'orchestration est celle d'un ``init`` (rôle worker désactivé), donc le clic
rend un refus nommé et ne lance jamais rien — et la commande équivalente à
copier.

Le presse-papiers est stubbé AVANT le clic (pattern déterministe de
``test_workspace_decisions_p68.py``) : le vrai ``writeText`` est un aléa en
chromium headless, le contrat testé est ce que l'UI a demandé de copier et le
statut qui le dit.
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

APPEAR_MS = 10_000

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions: []
profile_strictness:
  governed: hard_fail
"""

READY_TITLE = "Brancher le préflight d'assignation"
PROPOSED_TITLE = "Esquisser le panneau de lancement"
RUNNING_TITLE = "Tâche en cours, hors du périmètre du panneau"

VERIF_IDS = [
    "conflit_reservation", "plafond_sessions", "admission", "dependances", "quota",
    "dossier_approuve", "gh_app", "pr_ouverte",
]
VERDICTS = {"ok", "warn", "bloquant", "inconnu"}


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
def p47_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from grimoire.missions.schemas import TaskClaim, TaskState
    from grimoire.missions.service import TaskService

    root = tmp_path_factory.mktemp("assigner-p47") / "projet-assigner-p47"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-assigner-p47", "--backend", "local"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.fail("`grimoire init` indisponible : le projet P47 ne peut pas être monté")
    gates = root / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(GATES, encoding="utf-8")

    service = TaskService(root)
    # Prête : candidate à l'admission, le panneau rend un plan « pret ».
    service.add(READY_TITLE, ("le panneau se lit",), owner="guilhem", actor="t", ready=True)
    # Proposée : le préflight répond, mais l'admission dit ce qui manque (pret=false).
    service.add(PROPOSED_TITLE, ("le plan dit pourquoi",), owner="guilhem", actor="t")
    # En cours : hors du périmètre — aucune action « Assigner une session… ».
    running = service.add(RUNNING_TITLE, ("rien à assigner",), owner="guilhem", actor="t").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("agent-a", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(running, state, actor_id="agent-a", claim=claim)
    return root


def _task_id(root: Path, title: str) -> str:
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    return next(t.id for t in service.ledger.list_tasks() if t.title == title)


@pytest.fixture(scope="module")
def p47_url(p47_project: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    import json as _json
    import stat

    port = _free_port()
    env = dict(os.environ)
    env["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path_factory.mktemp("cockpit-home-p47"))
    env["NO_COLOR"] = "1"
    # Critère 2 : le préflight sonde la confiance Claude Code (~/.claude.json)
    # et gh. Le serveur reçoit un HOME factice qui approuve le projet, et un
    # stub gh en tête de PATH (authentifié, aucune PR) : les contrôles rendent
    # « ok » sans toucher au vrai poste ni au réseau.
    home = tmp_path_factory.mktemp("home-p47")
    (home / ".claude.json").write_text(
        _json.dumps({"projects": {str(p47_project.resolve()): {"hasTrustDialogAccepted": True}}}), encoding="utf-8"
    )
    stub = tmp_path_factory.mktemp("bin-p47") / "gh"
    stub.write_text('#!/bin/sh\ncase "$1 $2" in\n"auth status") exit 0;;\n"pr list") echo "[]"; exit 0;;\nesac\nexit 2\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env["HOME"] = str(home)
    env["PATH"] = f"{stub.parent}{os.pathsep}" + env.get("PATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(p47_project), "--port", str(port), "--no-open"],
        cwd=str(p47_project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
def page(browser: Browser, p47_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", color_scheme="light")
    opened = context.new_page()
    opened.goto(f"{p47_url}/workspace/index.html", wait_until="domcontentloaded")
    opened.wait_for_selector("body[data-ready='1']", timeout=30_000)
    opened.evaluate("(id) => window.GrimoireWorkspace.goto(id)", "travail")
    opened.wait_for_function("() => window.GrimoireWorkspace.module === 'executer'")
    # Piège connu : wait_for_selector('.ex-card') ne regarde que le premier
    # nœud du DOM ; on attend une carte VISIBLE.
    opened.wait_for_selector(".ex-card:visible", timeout=APPEAR_MS)
    try:
        yield opened
    finally:
        context.close()


def _open_panel(page: Page, title_fragment: str) -> None:
    card = page.locator(".ex-card:visible").filter(has_text=title_fragment).first
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    opener = inspector.locator("[data-role='assign-open']")
    opener.wait_for(timeout=APPEAR_MS)
    if opener.get_attribute("aria-expanded") != "true":
        opener.click()
    inspector.locator(".ex-assign-panel").wait_for(timeout=APPEAR_MS)


def test_le_panneau_rend_le_plan_de_la_vraie_route(page: Page, p47_project: Path) -> None:
    tid = _task_id(p47_project, READY_TITLE)
    with page.expect_response(lambda r: f"/tasks/{tid}/launch-plan" in r.url) as plan_resp:
        _open_panel(page, "Brancher le préflight")
    assert plan_resp.value.ok
    plan = plan_resp.value.json()
    panel = page.locator(".ex-assign-panel")

    # Titre et sous-titre de la maquette (artboard Plan, panneau de droite).
    expect(panel.locator("h4").first).to_have_text("Assigner une session")
    expect(panel.locator(".ex-assign-sub")).to_contain_text(
        "Tu lances, la session travaille dans son worktree, elle finit par une PR que tu fusionnes."
    )

    # Modèle : la valeur et « classe V…, justification » viennent du payload.
    model_line = panel.locator("[data-role='assign-model']")
    expect(model_line).to_contain_text(f"classe {plan['modele']['classe']}")
    expect(model_line).to_contain_text(plan["modele"]["justification"])

    # Worktree (nom) et branche : la composition du lanceur, task/<id>.
    assert plan["worktree"]["branche"] == f"task/{tid}"
    expect(panel.locator("[data-role='assign-branch'] code")).to_have_text(f"task/{tid}")
    worktree_line = panel.locator("[data-role='assign-worktree']")
    if plan["worktree"]["chemin"]:
        expect(worktree_line.locator("code")).to_have_text(Path(plan["worktree"]["chemin"]).name)
    else:
        expect(worktree_line).to_contain_text("inconnu")


def test_les_cinq_verifications_du_preflight_sont_listees_avec_leurs_verdicts(page: Page) -> None:
    _open_panel(page, "Brancher le préflight")
    panel = page.locator(".ex-assign-panel")
    verifs = panel.locator("[data-role='assign-verif']")
    expect(verifs).to_have_count(8)
    ids = verifs.evaluate_all("(nodes) => nodes.map((n) => n.dataset.verif)")
    assert ids == VERIF_IDS, ids
    verdicts = verifs.evaluate_all("(nodes) => nodes.map((n) => n.dataset.verdict)")
    assert set(verdicts) <= VERDICTS, verdicts
    # Chaque ligne porte une icône d'état et un détail lisible.
    for i in range(8):
        line = verifs.nth(i)
        expect(line.locator(".sico .ico")).to_have_count(1)
        assert line.inner_text().strip(), f"vérification {ids[i]} sans texte"
    # La tâche est prête et seule : dépendances et conflit sont constatés « ok ».
    assert verdicts[VERIF_IDS.index("dependances")] == "ok"
    assert verdicts[VERIF_IDS.index("conflit_reservation")] == "ok"


def test_lancer_est_reel_et_le_refus_du_lanceur_est_nomme_et_journalise(page: Page, p47_project: Path) -> None:
    """Le clic envoie l'empreinte du plan à POST tasks/<id>/launch — le lanceur
    du tick répond « rôle worker désactivé » (politique d'un ``init``) : rien
    n'est lancé, le refus est affiché et l'apply est journalisé (ADR-008)."""
    tid = _task_id(p47_project, READY_TITLE)
    _open_panel(page, "Brancher le préflight")
    panel = page.locator(".ex-assign-panel")

    # Plan prêt (le rôle désactivé n'est qu'un warn) : le bouton est actif.
    launch = panel.locator("[data-role='assign-launch']")
    expect(launch).to_be_enabled()
    expect(launch).to_have_text("Lancer la session")

    with page.expect_response(lambda r: f"/tasks/{tid}/launch" in r.url and r.request.method == "POST") as resp:
        launch.click()
    assert resp.value.ok
    verdict = resp.value.json()
    assert verdict["lance"] is False and verdict["statut"] == "skipped", verdict
    status = panel.locator(".ex-assign-launch .action-status")
    expect(status).to_contain_text("Lancement refusé")
    expect(status).to_contain_text("rôle worker désactivé")
    expect(launch).to_be_enabled()
    # L'apply est tracé dans le journal ajout seul du projet, started puis effect.
    journal = p47_project / "_grimoire-runtime-output" / "cockpit" / "launch-apply.jsonl"
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [e["event"] for e in events][-2:] == ["apply_started", "apply_effect"]
    assert events[-1]["status"] == "skipped"


def test_la_commande_equivalente_se_copie(page: Page, p47_project: Path) -> None:
    tid = _task_id(p47_project, READY_TITLE)
    _open_panel(page, "Brancher le préflight")
    panel = page.locator(".ex-assign-panel")

    # Équivalent : la commande du payload dans un <code>.
    code = panel.locator("[data-role='assign-command']")
    command = code.inner_text()
    assert command.startswith("claude --bg"), command
    assert f"grimoire-{tid}" in command, command

    # « Copier la commande » : le presse-papiers dans le clic, et le statut le dit.
    # Stub AVANT le clic (pattern test_workspace_decisions_p68) : le vrai
    # presse-papiers est un aléa en chromium headless, le contrat testé est ce
    # que l'UI a demandé de copier.
    page.evaluate(
        "() => { window.__copied = []; navigator.clipboard.writeText ="
        " (t) => { window.__copied.push(t); return Promise.resolve(); }; }"
    )
    panel.get_by_role("button", name="Copier la commande").click()
    # Deux statuts vivent dans le panneau depuis le lot 3 (lancement + copie) :
    # on lit celui du bloc Équivalent.
    expect(panel.locator(".ex-assign-cmd .action-status")).to_contain_text("Commande copiée")
    copied = page.evaluate("() => window.__copied.join('')")
    assert command in copied, copied


def test_une_tache_proposee_dit_pas_pret_en_tete_et_une_tache_en_cours_n_a_pas_le_panneau(page: Page) -> None:
    # Proposée : le préflight répond, l'admission refuse — le panneau le dit en tête, ton warn.
    _open_panel(page, "Esquisser le panneau")
    panel = page.locator(".ex-assign-panel")
    not_ready = panel.locator("[data-role='assign-not-ready']")
    expect(not_ready).to_be_visible()
    expect(not_ready).to_contain_text("Pas prêt")
    admission = panel.locator("[data-role='assign-verif'][data-verif='admission']")
    expect(admission).to_have_attribute("data-verdict", "bloquant")
    # Pas prêt : le bouton réel reste désactivé, la raison écrite à côté (lot 3).
    launch = panel.locator("[data-role='assign-launch']")
    expect(launch).to_be_disabled()
    reason = panel.locator("[data-role='assign-launch-reason']")
    expect(reason).to_contain_text("Pas prêt")
    assert launch.get_attribute("aria-describedby") == reason.get_attribute("id")

    # En cours : l'état ne la rend pas lançable, aucune action « Assigner une session… ».
    card = page.locator(".ex-card:visible").filter(has_text="hors du périmètre").first
    card.dispatch_event("click")
    inspector = page.locator("#inspector-body")
    inspector.locator("[data-role='session-block']").wait_for(timeout=APPEAR_MS)
    expect(inspector.locator("[data-role='assign-open']")).to_have_count(0)
