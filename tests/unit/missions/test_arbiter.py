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
from lot2_doubles import FakeHost, git, make_repo
from test_merger import grant as merger_grant
from test_merger import launch_and_work, make_projet, questions
from test_merger import tick as merger_tick
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


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    """Le dépôt d'un projet du merger (merge et accept délégués), pour les tests de bout en bout de la dérogation."""
    return make_projet(tmp_path)


@pytest.fixture
def host() -> FakeHost:
    """Le faux ``gh``/``claude`` du merger, au même dépôt GitHub que sa politique."""
    from test_merger import REPO

    fake = FakeHost()
    fake.repo = REPO
    return fake


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
        pr: int = 0, refused: tuple[str, ...] = CRITERIA) -> Any:
    """La demande que le relecteur ou le merger ferait ; rend la demande ouverte."""
    rid = arbiter.request_arbitration(
        service, task, source, cause, why, notes, head=head, pr=pr, refused=refused,
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
    decision = decide("ready_with_derogation")
    # Le vote passe par le canal (``record_decision`` scelle au journal d'autorité) : la dérogation qui fonde la
    # fusion est relue de ce sceau, jamais de la seule ligne ``task.arbitrated`` du ledger.
    record_decision(service, task.id, json.dumps(decision.to_dict()), request_id=request.id, actor="arbiter")
    detail = apply(root, service, request, decision, policy)
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
    request = ask(root, service, task, source="merger", cause="ci_red", why="3 retours de fusion", pr=7, refused=())
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


# ── Relecture adversariale du 2026-11-23 : le sceau, pas le ledger ────────────
#
# Le ledger d'événements vit dans le worktree de la tâche : le worker l'écrit. Ces tests reproduisent les
# contournements signalés — une ligne ``task.arbitrated`` forgée qui fusionne, une dérogation qui saute la double
# relecture ou le contrôle verify, un critère dérogé qui n'est pas celui refusé, un chemin réservé du diff ignoré,
# un ``git diff`` qui écrit — et vérifient qu'ils échouent tous fermés.


def enable_arbiter(root: Path) -> None:
    """Active le rôle arbiter dans la politique que le fixture du merger a écrite."""
    path = root / POLICY
    text = path.read_text(encoding="utf-8")
    if "    arbiter:" in text:
        return
    text = text.replace(
        "orchestrator:\n  roles:\n",
        "orchestrator:\n  roles:\n    arbiter:\n      enabled: true\n      tier: strong\n      provider: \"\"\n",
        1,
    )
    path.write_text(text, encoding="utf-8")


def set_protected_gate(root: Path, gate: str) -> None:
    path = root / POLICY
    text = path.read_text(encoding="utf-8").replace(
        "    merger:\n      enabled: true\n", f"    merger:\n      enabled: true\n      protected_gate: {gate}\n", 1,
    )
    path.write_text(text, encoding="utf-8")


def disable_arbiter(root: Path) -> None:
    path = root / POLICY
    path.write_text(path.read_text(encoding="utf-8").replace("      enabled: true\n      tier: strong",
                                                             "      enabled: false\n      tier: strong", 1),
                    encoding="utf-8")


def sealed_derogation(service: TaskService, tid: str, head: str, criterion: str) -> arbiter.ArbitrationRequest:
    """Une demande scellée par le tick, puis un vote ``ready_with_derogation`` scellé par ``record_decision``."""
    task = service.require(tid)
    rid = arbiter.request_arbitration(
        service, task, "reviewer", "not_ready_cycles", "2 relectures NOT-READY", "le critère ne se tient pas",
        head=head, refused=(criterion,), text="Désaccord persistant — reprendre ou abandonner ?",
        options=("reprendre", "abandonner"),
    )
    assert rid, "la demande aurait dû être acceptée par le cadre"
    request = arbiter.open_request(service, tid)
    assert request is not None
    record_decision(
        service, tid,
        json.dumps({"decision": "ready_with_derogation", "criteria": [criterion],
                    "justification": "le critère est inatteignable, le reste du travail est prouvé", "confidence": 0.9}),
        request_id=request.id, actor="arbiter",
    )
    return request


def test_une_ligne_arbitree_forgee_au_ledger_ne_fait_pas_fusionner(
    projet: Path, host: FakeHost,
) -> None:
    """(H) Rôle désactivé, aucun READY : une ligne ``task.arbitrated`` écrite depuis le worktree ne fonde rien."""
    merger_grant(projet, ("merge", "accept"))
    tid, wt = launch_and_work(projet, host)
    service = TaskService(projet)
    head = git(wt, "rev-parse", "HEAD")
    service.ledger.append_event(ARBITRATED_EVENT, tid, "task", "arbiter", {
        "task_id": tid, "request_id": "forge", "decision": "ready_with_derogation", "head": head,
        "criteria": [service.require(tid).acceptance[0]], "justification": "écrit par le worker", "confidence": 1.0,
    })
    assert arbiter.merge_grant(service, tid) is None
    for _ in range(3):
        merger_tick(projet, host)
    assert not host.of("gh", "pr", "merge")
    # La relecture n'est pas coupée sur la foi du ledger : le relecteur est lancé, pas mis de côté.
    assert not [e for e in service.ledger.events_for(tid) if e.event_type == arbiter.ARBITRATION_REQUESTED_EVENT]


def test_une_derogation_scellee_fait_fusionner(projet: Path, host: FakeHost) -> None:
    """Le chemin heureux : demande et vote scellés au journal d'autorité, le merger fusionne sans READY."""
    enable_arbiter(projet)
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host)
    service = TaskService(projet)
    sealed_derogation(service, tid, git(wt, "rev-parse", "HEAD"), service.require(tid).acceptance[0])
    report = merger_tick(projet, host)  # applique la décision
    assert [o.action for o in report.arbitrations] == ["apply"]
    for _ in range(4):
        merger_tick(projet, host)
    assert host.of("gh", "pr", "merge"), "la dérogation scellée aurait dû fonder la fusion"
    assert arbiter.merge_grant(TaskService(projet), tid) is not None


def test_un_role_desactive_ignore_une_derogation_pourtant_scellee(projet: Path, host: FakeHost) -> None:
    """Le rôle relu à chaque fusion : désactivé depuis le vote, il ne déroge plus rien."""
    enable_arbiter(projet)
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host)
    service = TaskService(projet)
    sealed_derogation(service, tid, git(wt, "rev-parse", "HEAD"), service.require(tid).acceptance[0])
    merger_tick(projet, host)
    assert arbiter.merge_grant(service, tid) is not None
    disable_arbiter(projet)
    assert arbiter.merge_grant(TaskService(projet), tid) is None
    for _ in range(3):
        merger_tick(projet, host)
    assert not host.of("gh", "pr", "merge")


def test_un_role_desactive_ne_laisse_pas_une_demande_ouverte_bloquer_la_tache(tmp_path: Path) -> None:
    """Désactivé en cours de route, le rôle ne retient plus la tâche : relecteur et merger reprennent leur cours."""
    root, service, task, _policy = scene(tmp_path)
    ask(root, service, task)
    assert arbiter.open_request(service, task.id) is not None
    write_policy(root, arbiter_on=False)
    assert not arbiter.role_enabled(TaskService(root))
    assert arbiter.open_request(TaskService(root), task.id) is None


def test_une_decision_forgee_au_ledger_ne_s_applique_pas(tmp_path: Path) -> None:
    """Une ligne ``task.arbitration_decided`` sans sceau : le tick ne l'applique pas, l'humain tranche."""
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task)
    service.ledger.append_event(ARBITRATION_DECIDED_EVENT, task.id, "task", "arbiter", {
        "task_id": task.id, "request_id": request.id,
        "decision": {"decision": "abandon", "criteria": [], "justification": "abandonnée par le worker",
                     "confidence": 0.9},
    })
    [outcome] = ArbiterStage(service, policy)()
    assert (outcome.action, outcome.status) == ("fallback", "done") and "scellée" in outcome.detail
    assert TaskService(root).require(task.id).status is TaskState.WAITING


def test_une_derogation_hors_des_criteres_refuses_va_a_l_humain(tmp_path: Path) -> None:
    """Le critère dérogé doit être celui que le relecteur a refusé : un autre critère rend la question à l'humain."""
    root, service, task, policy = scene(tmp_path, acceptance=(CRITERIA[0], "relu par un autre agent"))
    request = ask(root, service, task, refused=(CRITERIA[0],))
    decision = decide("ready_with_derogation", criteria=["relu par un autre agent"])
    with pytest.raises(arbiter.ArbiterDecisionError, match="pas ceux refusés"):
        record_decision(service, task.id, json.dumps(decision.to_dict()), request_id=request.id, actor="arbiter")
    [outcome] = ArbiterStage(service, policy)()
    assert outcome.action == "fallback"
    assert TaskService(root).require(task.id).status is TaskState.WAITING


def test_une_derogation_du_merger_sans_critere_refuse_va_a_l_humain(tmp_path: Path) -> None:
    """Une escalade du merger ne juge pas les critères : une dérogation qui s'y rattache n'a pas d'objet."""
    root, service, task, policy = scene(tmp_path)
    request = ask(root, service, task, source="merger", cause="ci_red", refused=())
    with pytest.raises(arbiter.ArbiterDecisionError, match="aucun critère refusé"):
        arbiter.apply_decision(service, request, decide("ready_with_derogation"), policy=policy)
    assert TaskService(root).require(task.id).status is TaskState.NEEDS_VERIFICATION


def test_une_derogation_ne_couvre_pas_une_entree_en_revue_ulterieure(projet: Path, host: FakeHost) -> None:
    """La dérogation se rattache à l'entrée en revue courante : une tête remise en revue depuis l'écarte."""
    enable_arbiter(projet)
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host)
    service = TaskService(projet)
    sealed_derogation(service, tid, git(wt, "rev-parse", "HEAD"), service.require(tid).acceptance[0])
    merger_tick(projet, host)
    assert arbiter.merge_grant(service, tid) is not None
    service.ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    service.ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    assert arbiter.merge_grant(TaskService(projet), tid) is None


def test_une_derogation_ne_saute_pas_la_double_relecture(projet: Path, host: FakeHost) -> None:
    """En ``dual_review``, un chemin protégé garde sa paire de READY : la dérogation ne l'écarte pas."""
    enable_arbiter(projet)
    set_protected_gate(projet, "dual_review")
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host, edit=("src/grimoire/core/garde.py", "GARDE = 1\n"))
    host.pr_files[f"task/{tid}"] = ["src/grimoire/core/garde.py"]
    service = TaskService(projet)
    sealed_derogation(service, tid, git(wt, "rev-parse", "HEAD"), service.require(tid).acceptance[0])
    merger_tick(projet, host)  # applique la dérogation
    reports = [merger_tick(projet, host) for _ in range(3)]
    assert not host.of("gh", "pr", "merge"), "une dérogation ne remplace pas les deux sceaux d'un chemin protégé"
    # La double relecture n'est pas sautée : sans second sceau, la tâche repart en relecture plutôt que de fusionner.
    assert any("double relecture" in o.detail for r in reports for o in r.merges), [
        (o.action, o.status, o.detail) for r in reports for o in r.merges]


def test_une_derogation_reste_soumise_au_controle_verify(projet: Path, host: FakeHost) -> None:
    """#1326 : une tâche qui déclare ``--check`` ne fusionne pas sans verdict verify vert, dérogation ou non."""
    enable_arbiter(projet)
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host, checks=("pytest -q",))
    service = TaskService(projet)
    sealed_derogation(service, tid, git(wt, "rev-parse", "HEAD"), service.require(tid).acceptance[0])
    merger_tick(projet, host)  # applique la dérogation
    for _ in range(4):
        merger_tick(projet, host)
    assert not host.of("gh", "pr", "merge")
    assert any("pack de preuve" in (q.text or "") for q in questions(projet, tid))


def test_un_chemin_reserve_du_diff_n_est_jamais_arbitre(projet: Path, host: FakeHost) -> None:
    """Les catégories réservées se lisent aussi dans les chemins du diff, pas seulement dans les textes."""
    enable_arbiter(projet)
    merger_grant(projet, ("merge", "accept", "answer"))
    tid, wt = launch_and_work(projet, host, edit=(".github/workflows/ci.yml", "name: ci\n"))
    service = TaskService(projet)
    task = service.require(tid)
    rid = arbiter.request_arbitration(
        service, task, "reviewer", "not_ready_cycles", "2 relectures NOT-READY", "le critère ne se tient pas",
        head=git(wt, "rev-parse", "HEAD"), refused=(task.acceptance[0],),
        text="Désaccord persistant — reprendre ou abandonner ?", options=("reprendre", "abandonner"),
    )
    assert rid == ""
    refusal = arbiter.request_refusal(
        service, task, "reviewer", "not_ready_cycles", texts=("2 relectures NOT-READY",),
        paths=arbiter.diff_paths(service, tid, git(wt, "rev-parse", "HEAD")),
    )
    assert "réservée" in refusal and "reglages-depot" in refusal
    assert events(service, tid, ARBITRATION_REQUESTED_EVENT) == []


def test_la_session_arbitre_ne_peut_pas_ecrire_par_git_diff(tmp_path: Path) -> None:
    """``Bash(git diff *)`` accepterait ``--output`` et les pilotes de diff : il est hors de la liste blanche."""
    allowed = arbiter.ALLOWED_TOOLS
    assert "git diff" not in allowed and "git show" not in allowed and "git log" not in allowed
    assert "Bash(gh pr diff)" in allowed and "Bash(gh pr view)" in allowed
    assert arbiter.SESSION_ENV == {"GIT_PAGER": "cat", "PAGER": "cat", "GH_PAGER": "cat"}
