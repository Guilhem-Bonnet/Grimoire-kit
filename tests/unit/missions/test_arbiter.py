"""GAO-r-le-arbitre-001 : un modèle fort tranche un désaccord worker/relecteur avant toute question à l'humain.

Contre un faux ``claude`` (:class:`lot2_doubles.FakeHost`) et un journal d'autorité neuf. Critères : un JSON par
décision rend l'effet décrit (dérogation, amendement, scission, abandon, retour au worker) ; le rôle désactivé garde
le comportement d'avant ; un JSON invalide ou une confiance basse rendent la question à l'humain avec l'analyse ; une
catégorie réservée du pilote n'est jamais arbitrée ; un second désaccord sur la même cause va à l'humain ; le relecteur
confie son escalade au tick, qui lance une session en lecture seule bornée au canal ``task-arbitrate``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, make_repo
from test_reviewer import launched_and_in_review, receipt_from, reviewer_sid
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import arbiter
from grimoire.missions import mandate as md
from grimoire.missions.arbiter import (
    ARBITRATED_EVENT,
    ARBITRATION_DECIDED_EVENT,
    ARBITRATION_REJECTED_EVENT,
    ARBITRATION_REQUESTED_EVENT,
    ArbiterStage,
    parse_decision,
    record_decision,
)
from grimoire.missions.ledger_rules import CRITERION_AMENDED_EVENT, CRITERION_REMOVED_EVENT
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import Choice

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")
CRITERIA = ("la route rend 404 sur un id inconnu",)
HEAD = "a" * 40
runner = CliRunner()
#: Le seul canal d'une session arbitre : `task arbitrate` est exercé ici (vote accepté et refusé).
pytestmark = pytest.mark.covers("cli:task arbitrate")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session ni principal hérités du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED",
                md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path, *, arbiter_on: bool = True, provider: str = "", reviewer: bool = False) -> None:
    review = (
        "    reviewer:\n      enabled: true\n      max_cycles: 1\n      permission_mode: acceptEdits\n"
        if reviewer else ""
    )
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n"
        "    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n      permission_mode: acceptEdits\n"
        f"{review}"
        f"    arbiter:\n      enabled: {str(arbiter_on).lower()}\n      tier: strong\n      provider: \"{provider}\"\n",
        encoding="utf-8",
    )


def write_registry(root: Path) -> None:
    """Un fournisseur factice local, jamais Anthropic : le modèle du palier ne sort pas de l'hôte."""
    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        'schema: "grimoire-llm-provider-registry/v1"\n'
        "providers:\n"
        "  - id: acme\n"
        "    enabled: true\n"
        "    provider_type: local\n"
        "    anthropic_base_url: http://127.0.0.1:8099\n"
        "    models: [{id: acme-strong, tier: strong}]\n"
        'routing: {default_data_class: ""}\n',
        encoding="utf-8",
    )


def grant(root: Path, gestures: tuple[str, ...] = ("answer",)) -> md.Mandate:
    request = MandateRequest.build(gestures, ("implementation",), 10, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def in_review(service: TaskService, title: str = "route 404", acceptance: tuple[str, ...] = CRITERIA) -> str:
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="M", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=acceptance, owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.claim(task.id, "worker")
    service.ledger.transition_task(task.id, TaskState.RUNNING, actor_id="worker")
    service.ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    return task.id


def scene(tmp_path: Path, *, arbiter_on: bool = True, provider: str = "",
          acceptance: tuple[str, ...] = CRITERIA) -> tuple[Path, TaskService, Any, Any]:
    """Un dépôt, un mandat ``answer``, et une tâche en revue prête à être escaladée."""
    root = make_repo(tmp_path)
    write_policy(root, arbiter_on=arbiter_on, provider=provider)
    if provider:
        write_registry(root)
    grant(root)
    service = TaskService(root)
    tid = in_review(service, acceptance=acceptance)
    return root, service, service.require(tid), load_orchestrator_policy(root).policy


def ask(root: Path, service: TaskService, task: Any, *, cause: str = "not_ready_cycles", source: str = "reviewer",
        head: str = HEAD, why: str = "2 relectures NOT-READY", notes: str = "le critère n'est pas tenu",
        pr: int = 0) -> Any:
    """La demande que le relecteur ou le merger ferait ; rend la demande ouverte."""
    rid = arbiter.request_arbitration(
        service, task, source, cause, why, notes, head=head, pr=pr,
        text=f"Désaccord persistant sur {task.id} ({why}) — reprendre la tâche ou l'abandonner ?",
        title="Reprendre la tâche ou l'abandonner ?", context="Le relecteur refuse encore le travail rendu",
        options=("reprendre", "abandonner"), recommended="reprendre",
        choices=(
            Choice("reprendre", "Reprendre la tâche", "Relance une session sur la branche existante"),
            Choice("abandonner", "Abandonner", "Annule la tâche : plus aucune reprise", danger=True),
        ),
    )
    assert rid
    request = arbiter.open_request(service, task.id)
    assert request is not None
    return request


def decide(decision: str, **extra: Any) -> arbiter.ArbiterDecision:
    data = {"decision": decision, "criteria": list(CRITERIA),
            "justification": "le critère reste inatteignable et le travail rendu le montre",
            "confidence": 0.9, **extra}
    return parse_decision(data)


def apply(root: Path, service: TaskService, request: Any, decision: arbiter.ArbiterDecision,
          policy: Any, **kw: Any) -> str:
    return arbiter.apply_decision(
        service, request, decision, model="acme-strong", mandate_id="M-1", policy=policy, **kw,
    )


def events(service: TaskService, tid: str, kind: str) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in service.ledger.events_for(tid) if e.event_type == kind]


# ── Politique et JSON ────────────────────────────────────────────────────────


def test_le_role_arbitre_se_lit_desactive_par_defaut_et_en_dontask(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    default = load_orchestrator_policy(root).policy
    assert default is not None and default.arbiter.enabled is False
    assert default.arbiter.tier == "strong" and default.arbiter.permission_mode == "dontAsk"
    assert default.to_dict()["arbiter"]["sessions_per_day"] == 5
    write_policy(root)
    role = load_orchestrator_policy(root).policy.arbiter  # type: ignore[union-attr]
    assert role.enabled and role.tier == "strong" and role.permission_mode == "dontAsk"
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    arbiter:\n      enabled: true\n      permission_mode: bypassPermissions\n",
        encoding="utf-8",
    )
    assert "toujours en dontAsk" in load_orchestrator_policy(root).error
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    arbiter:\n      enabled: true\n      model: opus\n", encoding="utf-8"
    )
    assert "clé inconnue model" in load_orchestrator_policy(root).error
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    arbiter:\n      enabled: true\n      tier: turbo\n", encoding="utf-8"
    )
    assert "tier : 'turbo' inconnu" in load_orchestrator_policy(root).error


def test_une_decision_json_est_validee_par_du_code() -> None:
    fenced = (
        "voici mon verdict\n```json\n"
        '{"schema": "grimoire-arbiter-decision/v1", "decision": "back_to_worker", "criteria": ["la route rend 404 sur un id inconnu"],'
        ' "justification": "le worker peut la tenir avec un test de plus", "confidence": 0.8}\n```'
    )
    decision = parse_decision(fenced)
    assert decision.decision == "back_to_worker" and decision.confident
    for bad in (
        "pas du JSON",
        '{"decision": "yolo", "criteria": [], "justification": "xxxxxxxxxxxxxx", "confidence": 0.9}',
        '{"decision": "abandon", "criteria": [], "justification": "court", "confidence": 0.9}',
        '{"decision": "abandon", "criteria": [], "justification": "xxxxxxxxxxxxxx", "confidence": 2}',
        '{"decision": "amend_criterion", "criteria": ["a"], "justification": "xxxxxxxxxxxxxx", "confidence": 0.9}',
        '{"decision": "abandon", "criteria": [], "justification": "xxxxxxxxxxxxxx", "confidence": 0.9, "quoi": 1}',
    ):
        with pytest.raises(arbiter.ArbiterDecisionError):
            parse_decision(bad)


# ── Un cas par décision ──────────────────────────────────────────────────────


def test_ready_with_derogation_marque_le_critere_et_ouvre_la_fusion(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    detail = apply(root, service, request, decide("ready_with_derogation"), policy)
    assert "dérogé" in detail
    [arbitrated] = events(service, task.id, ARBITRATED_EVENT)
    assert arbitrated["decision"] == "ready_with_derogation" and arbitrated["head"] == HEAD
    assert arbitrated["model"] == "acme-strong" and arbitrated["mandate_id"] == "M-1"
    assert arbitrated["justification"] and arbitrated["confidence"] == 0.9
    [derogated] = events(service, task.id, arbiter.CRITERION_DEROGATED_EVENT)
    assert derogated["criterion"] == CRITERIA[0] and derogated["head"] == HEAD
    assert TaskService(root).require(task.id).status is TaskState.NEEDS_VERIFICATION
    grant_event = arbiter.merge_grant(service, task.id)
    assert grant_event is not None and grant_event.payload["head"] == HEAD


def test_amend_criterion_remplace_le_texte_et_rend_la_tache_au_worker(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    decision = decide("amend_criterion", replacement="la route rend 404 ou 410 sur un id inconnu")
    detail = apply(root, service, request, decision, policy)
    assert "blocked" in detail or "bloquée" in detail  # aucune session de worker à reprendre
    [amended] = events(service, task.id, CRITERION_AMENDED_EVENT)
    assert amended["from"] == CRITERIA[0] and amended["to"] == decision.replacement
    assert TaskService(root).require(task.id).acceptance == (decision.replacement,)
    assert events(service, task.id, ARBITRATED_EVENT)[0]["decision"] == "amend_criterion"


def test_split_followup_detache_le_critere_dans_une_tache_proposee(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path, acceptance=(*CRITERIA, "relu par un autre agent"))
    request = ask(root, service, task)
    decision = decide("split_followup", criteria=[CRITERIA[0]], followup_title="Suite : route inconnue")
    detail = apply(root, service, request, decision, policy)
    assert "détaché" in detail
    [removed] = events(service, task.id, CRITERION_REMOVED_EVENT)
    followup = TaskService(root).ledger.get_task(str(removed["followup"]))
    assert removed["criterion"] == CRITERIA[0] and followup is not None
    assert followup.status is TaskState.PROPOSED and followup.acceptance == (CRITERIA[0],)
    assert followup.title == "Suite : route inconnue"


def test_abandon_annule_la_tache_avec_sa_raison(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    decision = decide("abandon", criteria=[])
    detail = apply(root, service, request, decision, policy)
    assert "annulée" in detail
    assert TaskService(root).require(task.id).status is TaskState.CANCELLED
    assert events(service, task.id, ARBITRATED_EVENT)[0]["justification"] == decision.justification


def test_back_to_worker_ajoute_la_consigne_et_un_dernier_cycle(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    detail = apply(root, service, request, decide("back_to_worker", criteria=[]), policy)
    assert "bloquée" in detail or "reprise" in detail
    updated = TaskService(root).require(task.id)
    assert updated.status is TaskState.BLOCKED
    assert any("Arbitrage :" in d.text for d in updated.directives)
    assert events(service, task.id, ARBITRATED_EVENT)[0]["decision"] == "back_to_worker"


# ── Gardes : rôle, réservé, seconde fois ─────────────────────────────────────


def test_role_desactive_aucune_demande_et_comportement_actuel(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path, arbiter_on=False)
    rid = arbiter.request_arbitration(
        service, task, "reviewer", "not_ready_cycles", "2 relectures NOT-READY", "constat",
        text="Désaccord persistant — reprendre ou abandonner ?", options=("reprendre", "abandonner"),
    )
    assert rid == "" and events(service, task.id, ARBITRATION_REQUESTED_EVENT) == []
    assert "désactivé" in arbiter.request_refusal(service, task, "reviewer", "not_ready_cycles", policy=policy)
    assert ArbiterStage(service, policy)() == ()
    assert TaskService(root).require(task.id).waiting is None  # rien n'a été posé : l'escalade suit son cours


def test_categorie_reservee_n_est_jamais_arbitree(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    secret = TaskService(root).require(task.id)
    # Le constat nomme un secret : la liste fermée du pilote s'y oppose, comme pour l'autopilote.
    rid = arbiter.request_arbitration(
        service, secret, "reviewer", "not_ready_cycles", "2 relectures NOT-READY", "rotation des identifiants",
        text="Désaccord persistant — reprendre ou abandonner ?", options=("reprendre", "abandonner"),
    )
    assert rid == "" and events(service, task.id, ARBITRATION_REQUESTED_EVENT) == []
    refusal = arbiter.request_refusal(
        service, secret, "reviewer", "not_ready_cycles", texts=("rotation des identifiants",), policy=policy,
    )
    assert "réservée" in refusal


def test_le_merger_confie_ses_retours_de_fusion_a_l_arbitre(tmp_path: Path) -> None:
    root, service, task, _policy = scene(tmp_path)
    for cause in ("ci_red", "conflict", "desync", "stale_ready"):
        assert cause in arbiter.ARBITRABLE_CAUSES["merger"]
    request = ask(root, service, task, source="merger", cause="ci_red", why="3 retours de fusion", pr=7)
    assert request.source == "merger" and request.cause == "ci_red" and request.pr == 7
    [requested] = events(service, task.id, ARBITRATION_REQUESTED_EVENT)
    assert requested["source"] == "merger" and requested["pr"] == 7
    assert "fusion de" in requested["question"]["text"] or "reprendre" in requested["question"]["text"]


def test_second_desaccord_sur_la_meme_cause_va_a_l_humain(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    apply(root, service, request, decide("back_to_worker", criteria=[]), policy)
    refusal = arbiter.request_refusal(service, TaskService(root).require(task.id), "reviewer", "not_ready_cycles",
                                     policy=policy)
    assert "déjà été demandé" in refusal and "l'humain tranche" in refusal
    # Une autre cause reste arbitrable : la borne est par tâche ET par cause.
    assert arbiter.request_refusal(service, TaskService(root).require(task.id), "merger", "ci_red", policy=policy) == ""


# ── L'humain garde la main : JSON invalide, confiance basse ──────────────────


def test_json_invalide_rend_la_question_a_l_humain_avec_l_analyse(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    with pytest.raises(arbiter.ArbiterDecisionError):
        record_decision(service, task.id, "je ne sais pas trancher", request_id=request.id, actor="arbiter")
    [rejected] = events(service, task.id, ARBITRATION_REJECTED_EVENT)
    assert rejected["request_id"] == request.id and "JSON" in rejected["error"]
    [outcome] = ArbiterStage(service, policy)()
    assert (outcome.action, outcome.status) == ("fallback", "done")
    waiting = TaskService(root).require(task.id)
    assert waiting.status is TaskState.WAITING and waiting.waiting is not None
    question = waiting.waiting.question
    assert question is not None and "Analyse de l'arbitre" in question.context
    assert question.options == ("reprendre", "abandonner") and question.recommended == "reprendre"


def test_confiance_basse_rend_la_question_a_l_humain(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    record_decision(
        service, task.id,
        json.dumps({"decision": "back_to_worker", "criteria": [], "justification": "peut-être un test de plus",
                    "confidence": 0.2}),
        request_id=request.id, actor="arbiter",
    )
    [outcome] = ArbiterStage(service, policy)()
    assert outcome.action == "fallback" and "confiance trop basse" in outcome.detail
    task = TaskService(root).require(task.id)
    assert task.status is TaskState.WAITING and task.waiting is not None
    assert "peut-être un test de plus" in (task.waiting.question.context if task.waiting.question else "")


# ── Le tick lance la session, le canal vote ──────────────────────────────────


def _launch_event(service: TaskService, tid: str, cwd: Path) -> None:
    service.ledger.append_event(
        "task.session_launched", tid, "task", "launcher",
        {"task_id": tid, "session_id": "f0f0f0f0-0000-0000-0000-000000000000", "cwd": str(cwd), "model": "sonnet"},
    )


def test_le_tick_lance_une_session_arbitre_lecture_seule_sous_le_canal(tmp_path: Path) -> None:
    root, service, task, policy = scene(tmp_path, provider="acme")
    _launch_event(service, task.id, root)
    request = ask(root, service, task)
    host = FakeHost()
    [outcome] = ArbiterStage(service, policy, exec_=host)()
    assert (outcome.action, outcome.status) == ("launch", "launched")
    argv = host.of("claude", "--bg")[0]
    assert argv[argv.index("--name") + 1] == f"grimoire-arbiter-{task.id}"
    assert argv[argv.index("--model") + 1] == "acme-strong"  # le registre du fournisseur, pas un nom en dur
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Bash"
    allowed = argv[argv.index("--allowed-tools") + 1]
    assert "Bash(grimoire task arbitrate *)" in allowed and "Edit" not in allowed
    prompt = argv[-1]
    assert request.id in prompt and CRITERIA[0] in prompt and "DONNÉES NON FIABLES" in prompt
    [launched] = events(service, task.id, arbiter.ARBITRATION_LAUNCHED_EVENT)
    assert launched["request_id"] == request.id and launched["model"] == "acme-strong"
    # La session est inscrite en rôle orchestrator, bornée au canal `task-arbitrate` et datée (échec fermé sinon).
    session = host.session_of(f"grimoire-arbiter-{task.id}")
    [entry] = MandateStore(root).sessions(role="orchestrator")
    assert entry["session_id"] == session and entry["channels"] == [arbiter.ARBITRATION_CHANNEL]
    assert datetime.fromisoformat(str(entry["expires_at"])) > datetime.now(UTC)


def test_le_relecteur_escalade_confie_l_escalade_a_l_arbitre(tmp_path: Path) -> None:
    """Là où le relecteur posait la question à l'humain, il demande un arbitrage : le tick lance la session."""
    root = make_repo(tmp_path)
    write_policy(root, reviewer=True)
    grant(root)
    host = FakeHost()
    tid, launch = launched_and_in_review(root, host)
    from grimoire.missions.orchestrate import run_tick

    run_tick(root, apply=True, exec_=host)  # lance le relecteur
    receipt_from(Path(str(launch["cwd"])), tid, "NOT-READY", reviewer_sid(host))
    report = run_tick(root, apply=True, exec_=host)
    assert [(o.action, o.status) for o in report.reviews] == [("arbitrate", "done")]
    task = TaskService(root).require(tid)
    assert task.status is TaskState.NEEDS_VERIFICATION and task.waiting is None  # aucune question à l'humain
    [requested] = events(TaskService(root), tid, ARBITRATION_REQUESTED_EVENT)
    assert requested["source"] == "reviewer" and requested["cause"] == "not_ready_cycles"
    assert requested["head"]  # la dérogation portera sur la tête relue
    assert "Désaccord persistant" in requested["question"]["text"]
    assert [(o.action, o.status) for o in report.arbitrations] == [("launch", "launched")]


def test_le_vote_du_canal_est_valide_et_applique_au_tick_suivant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, service, task, _policy = scene(tmp_path)
    request = ask(root, service, task)
    session = "a" * 8 + "-0000-0000-0000-000000000000"
    md.register_orchestrator_session(root, session, channels=(arbiter.ARBITRATION_CHANNEL,),
                                     expires_at="2999-01-01T00:00:00+00:00")
    payload = json.dumps({"decision": "abandon", "criteria": [],
                          "justification": "le critère décrit une route qui n'existe pas", "confidence": 0.9})
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, "rule:arbitre")
    result = runner.invoke(app, ["task", "arbitrate", task.id, "--json", payload, "--request", request.id,
                                 "--project-root", str(root), "--model", "acme-strong"])
    assert result.exit_code == 0, result.output
    [decided] = events(TaskService(root), task.id, ARBITRATION_DECIDED_EVENT)
    assert decided["request_id"] == request.id and decided["decision"]["decision"] == "abandon"
    # Le vote n'applique rien : c'est le tick qui écrit `task.arbitrated` et annule la tâche.
    assert TaskService(root).require(task.id).status is TaskState.NEEDS_VERIFICATION
    [outcome] = ArbiterStage(TaskService(root), load_orchestrator_policy(root).policy)()
    assert outcome.action == "apply" and outcome.decision == "abandon"
    assert TaskService(root).require(task.id).status is TaskState.CANCELLED


def test_le_vote_depuis_le_worktree_de_la_tache_trouve_le_mandat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La session tourne dans le worktree de la tâche : le mandat est celui de la racine du projet, pas du worktree."""
    root = make_repo(tmp_path)
    write_policy(root)
    grant(root)
    host = FakeHost()
    tid, launch = launched_and_in_review(root, host)
    service = TaskService(root)
    request = ask(root, service, service.require(tid))
    session = "b" * 8 + "-0000-0000-0000-000000000000"
    md.register_orchestrator_session(root, session, channels=(arbiter.ARBITRATION_CHANNEL,),
                                     expires_at="2999-01-01T00:00:00+00:00")
    payload = json.dumps({"decision": "back_to_worker", "criteria": [],
                          "justification": "le worker a une session à reprendre", "confidence": 0.9})
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    result = runner.invoke(app, ["task", "arbitrate", tid, "--json", payload, "--request", request.id,
                                 "--project-root", str(launch["cwd"])])
    assert result.exit_code == 0, result.output
    [decided] = events(TaskService(root), tid, ARBITRATION_DECIDED_EVENT)
    assert decided["request_id"] == request.id


def test_une_session_non_inscrite_ne_peut_pas_voter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, service, task, _policy = scene(tmp_path)
    request = ask(root, service, task)
    payload = json.dumps({"decision": "abandon", "criteria": [],
                          "justification": "le critère décrit une route qui n'existe pas", "confidence": 0.9})
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "une-session-quelconque")
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    result = runner.invoke(app, ["task", "arbitrate", task.id, "--json", payload, "--request", request.id,
                                 "--project-root", str(root)])
    assert result.exit_code != 0
    assert events(TaskService(root), task.id, ARBITRATION_DECIDED_EVENT) == []


# ── Cockpit : information non bloquante ──────────────────────────────────────


def test_la_route_d_attention_liste_l_arbitrage_sans_le_compter(tmp_path: Path) -> None:
    from grimoire.tools import workspace_attention as wa
    from grimoire.tools import workspace_reviews

    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    apply(root, service, request, decide("ready_with_derogation"), policy)
    view = wa.attention_view(
        root, pr_reader=lambda repo, cwd: [], review_cache=workspace_reviews.ReviewCache(), pr_wait=1.0,
    )
    block = view["arbitrations"]
    assert block["label"] == "Arbitrages" and block["count"] == 1
    assert block["items"][0]["decision"] == "ready_with_derogation" and block["items"][0]["task_id"] == task.id
    # Non bloquant : l'arbitrage n'entre ni dans les compteurs d'attente ni dans aucune section.
    assert "arbitration" not in view["counts"]
    assert not [i for section in view["sections"] for i in section.get("items", []) if i["kind"] == "arbitration"]
