"""Nature de la demande (#1342 M2b) : classement FR/EN, entretien par nature, incident jamais bloqué."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import scaffold
from grimoire.core.cadrage_interview import answer, check_interview, interview_plan
from grimoire.missions.intake import intake_proposal
from grimoire.missions.request_kind import KIND_PROFILES, RequestKind, classify_request_kind, parse_kind
from grimoire.missions.schemas import TaskType

pytestmark = pytest.mark.covers("cli:cadrage interview answer")
runner = CliRunner()

PHRASES: dict[RequestKind, tuple[str, ...]] = {
    RequestKind.NEW_PROJECT: (
        "Nouveau projet de suivi des dons",
        "Créer une application de réservation",
        "Démarrer un projet de portail interne",
        "Start a new project for billing",
        "Build an app for inventory",
    ),
    RequestKind.NEW_FEATURE: (
        "Ajouter un export CSV des tâches",
        "Je voudrais une page de profil",
        "Permettre de filtrer par étiquette",
        "Add dark mode to the dashboard",
        "I want a share button",
    ),
    RequestKind.EVOLUTION: (
        "Améliorer le tri de la liste",
        "Faire évoluer la recherche",
        "Étendre le formulaire d'import",
        "Improve the search ranking",
        "Extend the export to JSON",
    ),
    RequestKind.BUG: (
        "Corrige le calcul du total",
        "Le bouton ne fonctionne plus",
        "Bug sur la page de connexion",
        "Fix the login redirect",
        "The export is broken",
    ),
    RequestKind.INCIDENT: (
        "Ça plante en prod",
        "Incident : le site est HS",
        "La production est down depuis ce matin",
        "Outage on the payment API",
        "Production is down",
    ),
    RequestKind.INVESTIGATION: (
        "Investigue les lenteurs de l'API",
        "Diagnostic des timeouts nocturnes",
        "Trouver pourquoi la file se bloque",
        "Investigate the memory growth",
        "Find out why the job hangs",
    ),
    RequestKind.ANALYSIS: (
        "Analyse de l'architecture actuelle",
        "Audit du module de facturation",
        "État des lieux des tests",
        "Audit the billing module",
        "Assess the test coverage",
    ),
    RequestKind.RESEARCH: (
        "Compare les bases vectorielles",
        "Veille sur les outils de transcription",
        "Quel outil choisir entre Redis et Valkey ?",
        "Compare Postgres and MySQL",
        "Benchmark of static site generators",
    ),
    RequestKind.MAINTENANCE: (
        "Nettoyer le code mort",
        "Refactorer le module de config",
        "Réduire la dette des tests",
        "Clean up the unused helpers",
        "Refactor the config loader",
    ),
    RequestKind.MIGRATION: (
        "Migrer la base vers Postgres 16",
        "Montée de version de Python",
        "Basculer vers le nouveau CI",
        "Migrate the database to Postgres 16",
        "Upgrade from Node 18 to Node 22",
    ),
    RequestKind.SECURITY: (
        "Vulnérabilité dans la dépendance X",
        "Une faille XSS sur le formulaire",
        "Fuite de secret dans le dépôt",
        "Security review of the upload endpoint",
        "Fix the CVE in the parser",
    ),
    RequestKind.QUESTION: (
        "Pourquoi la synchro est lente ?",
        "Comment fonctionne le cache ?",
        "Explique le rôle du ledger",
        "Why is the cache cleared on boot?",
        "How does the router pick an agent?",
    ),
}


def test_request_kind_covers_twelve_natures_distinct_from_task_type() -> None:
    assert len(RequestKind) == 12 and set(KIND_PROFILES) == set(RequestKind)
    assert RequestKind is not TaskType and not issubclass(RequestKind, TaskType)
    assert len(TaskType) == 9


@pytest.mark.parametrize(("kind", "text"), [(k, t) for k, ts in PHRASES.items() for t in ts])
def test_request_kind_classifies_fr_and_en(kind: RequestKind, text: str) -> None:
    got = classify_request_kind(text)
    assert got["kind"] == kind.value, (text, got)
    assert got["reason"]


def test_request_kind_counts_three_fr_two_en_per_nature() -> None:
    assert all(len(v) >= 5 for v in PHRASES.values())


def test_request_kind_in_intake_proposal_with_reason() -> None:
    proposal = intake_proposal("Ça plante en prod depuis ce matin")
    assert proposal["request_kind"]["kind"] == "incident" and proposal["request_kind"]["reason"]
    assert "mission_type" in proposal  # TaskType et son classement restent intacts


def test_request_kind_parse_and_errors() -> None:
    assert parse_kind("Incident") is RequestKind.INCIDENT
    assert parse_kind("securite") is RequestKind.SECURITY
    with pytest.raises(ValueError, match="nature inconnue"):
        parse_kind("zzz")
    with pytest.raises(ValueError, match="vide"):
        classify_request_kind("  ")


def test_request_kind_fallbacks() -> None:
    assert classify_request_kind("Un truc")["confidence"] == "faible"
    assert classify_request_kind("la synchro ?")["kind"] == "question"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    scaffold(tmp_path, project_name="x", chantier="demo")
    return tmp_path


def test_cadrage_interview_kind_questions_deliverable_acceptance(root: Path) -> None:
    res = runner.invoke(
        app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "--kind", "anomalie", "-o", "json"]
    )
    assert res.exit_code == 0, res.output
    plan = json.loads(res.output)
    assert plan["kind"] == "anomalie" and plan["kind_source"] == "forcée"
    assert any("reproduire" in q for q in plan["kind_questions"])
    assert plan["deliverable"] == "correctif et test de non-régression"
    assert plan["acceptance"] == "le cas ne se reproduit plus"


def test_cadrage_interview_uses_proposed_kind(root: Path) -> None:
    plan = interview_plan(root, "demo", text="Corrige le calcul du total")
    assert plan["kind"] == "anomalie" and plan["kind_source"].startswith("intake")


def test_cadrage_interview_text_output_shows_kind(root: Path) -> None:
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "--kind", "migration"])
    assert res.exit_code == 0 and "Migration" in res.output and "retour arrière" in res.output


def test_cadrage_interview_bad_kind(root: Path) -> None:
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "--kind", "zzz"])
    assert res.exit_code == 1


def test_cadrage_interview_question_triggers_no_interview(root: Path) -> None:
    plan = interview_plan(root, "demo", text="Pourquoi la synchro est lente ?")
    assert plan["kind"] == "question" and plan["interview"] is False
    assert plan["angles"] == [] and plan["complete"] is True
    assert not (root / "_grimoire-output").exists() or not list(root.rglob("entretien.json"))
    assert check_interview(root, "demo") == []
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "--kind", "question"])
    assert res.exit_code == 0 and "aucun entretien" in res.output


def test_cadrage_interview_incident_three_questions_never_blocks(root: Path) -> None:
    plan = interview_plan(root, "demo", text="Ça plante en prod", depth="approfondi")
    assert plan["kind"] == "incident"
    assert len(plan["kind_questions"]) <= 3
    assert plan["open_required"] == [] and plan["complete"] is True
    assert "post-mortem" in plan["deferred"]
    assert check_interview(root, "demo") == []
    res = runner.invoke(app, ["cadrage", "check", "--chantier", "demo", str(root)])
    assert "angle(s) obligatoire(s)" not in res.output


def test_cadrage_interview_kind_change_keeps_answers(root: Path) -> None:
    interview_plan(root, "demo", kind="investigation", depth="standard")
    answer(root, "demo", "utilisateurs", "L'équipe support")
    answer(root, "demo", "risques", "", unknown=True)
    plan = interview_plan(root, "demo", kind="anomalie")
    assert plan["kind"] == "anomalie"
    users = next(a for a in plan["angles"] if a["id"] == "utilisateurs")
    assert users["state"] == "couvert" and users["answers"] == ["L'équipe support"]
    risks = next(a for a in plan["angles"] if a["id"] == "risques")
    assert risks["state"] == "couvert"
    # la nature suit et reste enregistrée au rappel suivant
    assert interview_plan(root, "demo")["kind"] == "anomalie"


def test_cadrage_interview_incident_then_bug_requires_angles(root: Path) -> None:
    interview_plan(root, "demo", kind="incident", depth="standard")
    assert check_interview(root, "demo") == []
    interview_plan(root, "demo", kind="anomalie")
    assert check_interview(root, "demo") != []


def test_cadrage_interview_deep_kinds_force_deep_depth(root: Path) -> None:
    assert interview_plan(root, "demo", text="Migrer la base vers Postgres 16")["depth"] == "approfondi"
