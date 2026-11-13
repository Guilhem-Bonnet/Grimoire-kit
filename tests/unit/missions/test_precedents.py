"""GAO-jurisprudenc-001 : une réponse humaine vaut pour toutes les questions de la même classe.

Critères : une réponse humaine (origine ``human``) enregistre un précédent — classe, option, date, question ;
le pilote applique un précédent de la même classe avant de laisser la question à l'humain (source
``precedent`` et référence dans ``autopilot.decided``) ; ``task questions --precedents`` liste et
``--forget`` retire (canal humain) ; jamais pour une catégorie réservée ; fichier illisible : échec fermé, et
le pilote le dit ; la politique est désactivée par défaut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions.autopilot import DECIDED_EVENT, DEFERRED_EVENT, AutopilotStore, load_autopilot_policy
from grimoire.missions.mandate import MandateRefusedError, MandateRequest, MandateStore
from grimoire.missions.precedents import (
    PrecedentError,
    find,
    load,
    precedents_path,
    question_class,
)
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT, Question

ACC = ("la PR est fusionnée",)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
PROV = {"kind": "rule", "ref": "critere-1"}
WHY = "la PR est verte et relue, les critères sont remplis"
#: Une cause libre, ni réservée ni reconnue : la classe est un identifiant normalisé du ``why``.
CAUSE = "le worker demande une préférence de style"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def _tick() -> Any:
    """L'appelant est le code du tick (``acting_as_orchestrator``), comme la réponse automatique du pilote."""
    with md.acting_as_orchestrator():
        yield


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def grant(root: Path) -> md.Mandate:
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def pilot_on(root: Path) -> None:
    AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via="test")


def write_policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


def asks(
    root: Path, text: str = "Quelle voie ?", *, title: str = "Correctif", **ask: Any
) -> tuple[TaskService, str, str]:
    """Une tâche faite à la main qui attend une réponse : ``(service, tâche, question)``."""
    service = TaskService(root)
    tid = service.add(title, ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, text, "w", **ask)
    return service, tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def human(service: TaskService, tid: str, qid: str, text: str) -> Any:
    """La réponse humaine qui fait jurisprudence."""
    return service.answer(tid, text, "human", origin=ORIGIN_HUMAN, question_id=qid)


def answer(service: TaskService, tid: str, qid: str, text: str, **kw: Any) -> Any:
    return service.answer(tid, text, "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, **kw)


def events(service: TaskService, tid: str, kind: str) -> list[Any]:
    return [e for e in service.ledger.events_for(tid) if e.event_type == kind]


def enabled(project: Path) -> None:
    """Le pilote activé, mandat en cours, et les précédents réglés (désactivés par défaut)."""
    grant(project)
    write_policy(project, "autopilot:\n  precedents: true\n")
    pilot_on(project)


# ── La classe : rôle qui demande + cause normalisée ──────────────────────────────────────────────────────


def test_la_classe_reunit_le_role_et_une_cause_normalisee() -> None:
    merge = Question("q-1", "merge", "Fusionner ?", why="les chemins protégés sont touchés")
    assert question_class(merge, "merger") == "merge:protected_paths"
    review = Question("q-2", "choice", "Reprendre ?", why="3 relectures NOT-READY")
    assert question_class(review, "reviewer") == "review:not_ready_cycles"
    criterion = Question(
        "q-3",
        "choice",
        "Comment vérifier ?",
        key="cadrage:REQ-1:critere-vague:abc",
        why="critère vague : une validation humaine est nécessaire",
    )
    assert question_class(criterion, "worker") == "criterion:human_validation"


def test_les_identifiants_volatils_ne_font_pas_une_classe() -> None:
    one = Question("q-1", "choice", "GAO-1 : quelle couleur ?", why="PR #1206 : préférence de style")
    two = Question("q-2", "choice", "GAO-99 : quelle teinte ?", why="PR #7 : préférence de style")
    assert question_class(one, "worker") == question_class(two, "worker")


# ── Enregistrement : la réponse humaine, et elle seule ───────────────────────────────────────────────────


def test_une_reponse_humaine_enregistre_un_precedent(project: Path) -> None:
    service, tid, qid = asks(project, "Quelle couleur pour le bouton ?", why=CAUSE)
    question = service.require(tid).waiting.question
    assert question is not None
    human(service, tid, qid, "bleu")

    (entry,) = load(project)
    assert entry.id.startswith("p-")
    assert entry.question_class == question_class(question, "worker")
    assert entry.option == "bleu" and entry.question == "Quelle couleur pour le bouton ?"
    assert entry.answered_at and entry.task_id == tid and entry.question_id == qid


def test_une_reponse_d_orchestrateur_n_enregistre_rien(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE)
    answer(service, tid, qid, "bleu", justification=WHY, confidence="high")
    assert load(project) == ()


# ── Application : avant de laisser la question à l'humain ────────────────────────────────────────────────


def test_le_pilote_applique_le_precedent_de_meme_classe(project: Path) -> None:
    enabled(project)
    first, tid1, qid1 = asks(project, "Quelle couleur pour le bouton ?", why=CAUSE)
    human(first, tid1, qid1, "bleu")
    (precedent,) = load(project)

    service, tid, qid = asks(project, "Quelle teinte pour l'en-tête ?", why=CAUSE)
    move = answer(service, tid, qid, "vert", justification=WHY, confidence="low")

    assert move.task.status is TaskState.READY
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "bleu"
    assert decided.payload["recommendation_source"] == "precedent"
    assert decided.payload["precedent"] == precedent.id
    assert [e.payload["answer"] for e in events(service, tid, HUMAN_ANSWERED_EVENT)] == ["bleu"]


def test_le_precedent_le_plus_recent_l_emporte(project: Path) -> None:
    enabled(project)
    first, tid1, qid1 = asks(project, "Quelle couleur ?", why=CAUSE)
    human(first, tid1, qid1, "bleu")
    second, tid2, qid2 = asks(project, "Quelle teinte ?", why=CAUSE)
    human(second, tid2, qid2, "rouge")

    service, tid, qid = asks(project, "Quelle nuance ?", why=CAUSE)
    answer(service, tid, qid, "vert", justification=WHY, confidence="low")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "rouge"


def test_sans_la_politique_le_precedent_ne_s_applique_pas(project: Path) -> None:
    """Nouveauté de politique : un projet qui ne règle rien garde le comportement d'avant."""
    grant(project)
    pilot_on(project)
    first, tid1, qid1 = asks(project, "Quelle couleur ?", why=CAUSE)
    human(first, tid1, qid1, "bleu")

    service, tid, qid = asks(project, "Quelle teinte ?", why=CAUSE)
    with pytest.raises(GrimoireMissionError, match="confiance basse"):
        answer(service, tid, qid, "vert", justification=WHY, confidence="low")
    assert not events(service, tid, DECIDED_EVENT)
    (deferred,) = events(service, tid, DEFERRED_EVENT)
    assert "précédents" not in deferred.payload["reason"]  # rien n'est cherché : la politique est coupée


def test_un_precedent_ne_s_applique_jamais_a_une_categorie_reservee(project: Path) -> None:
    enabled(project)
    first, tid1, qid1 = asks(project, "Quel identifiant utiliser ?", why=CAUSE)
    human(first, tid1, qid1, "non")

    service, tid, qid = asks(project, "Quel mot de passe choisir ?", why=CAUSE)
    question = service.require(tid).waiting.question
    assert question is not None
    lookup = find(project, question, "worker", task=service.require(tid))
    assert lookup.precedent is None and "réservée à l'humain" in lookup.reason
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "oui", justification=WHY, confidence="low")
    assert not events(service, tid, DECIDED_EVENT)


def test_un_fichier_illisible_ne_fait_rien_appliquer_et_le_dit(project: Path) -> None:
    enabled(project)
    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE)
    path = precedents_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ pas du JSON\n", encoding="utf-8")

    with pytest.raises(GrimoireMissionError, match="illisible"):
        answer(service, tid, qid, "vert", justification=WHY, confidence="low")
    assert not events(service, tid, DECIDED_EVENT)
    (deferred,) = events(service, tid, DEFERRED_EVENT)
    assert "illisible" in deferred.payload["reason"]
    with pytest.raises(PrecedentError, match="illisible"):
        load(project)


def test_un_precedent_dont_l_option_n_est_pas_proposee_ne_s_applique_pas(project: Path) -> None:
    enabled(project)
    first, tid1, qid1 = asks(project, "Quelle couleur ?", why=CAUSE)
    human(first, tid1, qid1, "bleu")

    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE, options=("rouge", "vert"))
    question = service.require(tid).waiting.question
    assert question is not None
    lookup = find(project, question, "worker", task=service.require(tid))
    assert lookup.precedent is None and "absent des options" in lookup.reason
    with pytest.raises(GrimoireMissionError, match="confiance basse"):
        answer(service, tid, qid, "vert", justification=WHY, confidence="low")
    assert not events(service, tid, DECIDED_EVENT)


# ── Le tick : le pilote applique aussi sans session ──────────────────────────────────────────────────────


def test_le_tick_applique_un_precedent_sans_session(project: Path) -> None:
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy
    from grimoire.missions.orchestrator_session import OrchestratorStage

    enabled(project)
    first, tid1, qid1 = asks(project, "Quelle couleur ?", why=CAUSE)
    human(first, tid1, qid1, "bleu")

    service, tid, _qid = asks(project, "Quelle teinte ?", why=CAUSE)
    policy = load_orchestrator_policy(project).policy
    assert policy is not None
    outcomes = OrchestratorStage(service, policy)._apply_recommendations()

    assert any(o.status == "done" and "précédent" in o.detail for o in outcomes)
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "bleu" and decided.payload["recommendation_source"] == "precedent"


# ── La CLI : lister et oublier ───────────────────────────────────────────────────────────────────────────


def test_une_valeur_inconnue_rend_la_politique_invalide(project: Path) -> None:
    """Échec fermé : ``autopilot.precedents`` n'est pas un booléen — le pilote ne s'active pas."""
    write_policy(project, "autopilot:\n  precedents: oui\n")
    policy = load_autopilot_policy(project)
    assert policy.error and "precedents" in policy.error
    grant(project)
    with pytest.raises(MandateRefusedError, match="pilote automatique refusé"):
        pilot_on(project)


def test_la_cli_liste_les_precedents(project: Path) -> None:
    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE)
    human(service, tid, qid, "bleu")
    (entry,) = load(project)

    out = runner.invoke(app, ["task", "questions", "--precedents", "--project-root", str(project)])
    assert out.exit_code == 0, out.output
    assert entry.id in out.output and "bleu" in out.output and entry.question_class in out.output


def test_la_cli_oublie_un_precedent_par_le_canal_humain(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE)
    human(service, tid, qid, "bleu")
    (entry,) = load(project)

    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: ORIGIN_HUMAN)
    out = runner.invoke(app, ["task", "questions", "--forget", entry.id, "--project-root", str(project)])
    assert out.exit_code == 0, out.output
    assert load(project) == ()
    assert (
        "introuvable"
        in runner.invoke(app, ["task", "questions", "--forget", entry.id, "--project-root", str(project)]).output
    )


def test_la_cli_refuse_d_oublier_sans_le_canal_humain(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, tid, qid = asks(project, "Quelle couleur ?", why=CAUSE)
    human(service, tid, qid, "bleu")
    (entry,) = load(project)

    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "agent")
    out = runner.invoke(app, ["task", "questions", "--forget", entry.id, "--project-root", str(project)])
    assert out.exit_code == 1 and "humain" in out.output
    assert load(project) == (entry,)
