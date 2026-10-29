"""P92 — la question v2 : rétrocompatible, validée à l'écriture, avec ses rounds lus au ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.missions.question_v2 import ask_kwargs, parse_attach, parse_choice
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import (
    Attachment,
    Choice,
    Question,
    Recommendation,
    Waiting,
    question_problem,
    split_legacy,
)
from grimoire.tools import workspace_attention as wa

V1_LINE = {
    "id": "q-1",
    "kind": "choice",
    "text": "Fusionner la PR ?",
    "options": ["oui", "non"],
    "origin": "agent",
    "asked_at": "2026-01-01T00:00:00+00:00",
    "why": "CI verte",
    "key": "k",
    "recommended": "oui",
}


def _running(root: Path) -> tuple[TaskService, str]:
    service = TaskService(root)
    tid = service.add("Travail", ("fait",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    return service, tid


CHOICES = (
    Choice("reprendre", "Reprendre la tâche", "Relance une session"),
    Choice("abandonner", "Abandonner", danger=True),
)


def test_une_ligne_v1_se_relit_a_l_identique() -> None:
    question = Question.from_dict(V1_LINE)
    assert question.to_dict() == V1_LINE and not question.is_v2
    assert "schema" not in question.to_dict() and "round" not in question.to_dict()


def test_une_question_v2_reste_affichable_par_text_et_options() -> None:
    question = Question(
        "q-2",
        "choice",
        "Reprendre la tâche ou l'abandonner ?",
        ("reprendre", "abandonner"),
        title="Reprendre ?",
        choices=CHOICES,
        attachments=(Attachment("link", "PR", ref="https://github.com/o/r/pull/1", stats=(("files", 3),)),),
        recommendation=Recommendation("reprendre", "le travail est avancé", "Relance une session"),
        round=2,
        supersedes="q-1",
    )
    data = json.loads(json.dumps(question.to_dict()))
    assert Question.from_dict(data) == question and data["schema"] == 2
    v1_reader = {k: data[k] for k in ("id", "kind", "text", "options", "origin")}  # ce que lit un cockpit v1
    assert v1_reader["text"] and v1_reader["options"] == ["reprendre", "abandonner"]
    assert Question.from_dict({**data, "inconnu": 1}) == question  # une clé d'un ledger plus récent est ignorée


@pytest.mark.parametrize(
    ("fields", "fragment"),
    [
        ({"choices": (Choice("a"), Choice("a"))}, "en double"),
        ({"choices": (Choice(""),)}, "sans valeur"),
        ({"choices": CHOICES, "recommendation": Recommendation("ailleurs", "x")}, "recommandation"),
        ({"attachments": (Attachment("video", "x", ref="y"),)}, "type de pièce inconnu"),
        ({"attachments": (Attachment("link", "x", ref="javascript:alert(1)"),)}, "http"),
        ({"attachments": (Attachment("link", "x", ref="ftp://h/f"),)}, "http"),
        ({"attachments": (Attachment("image", "x", ref="a.png"),)}, "alternatif"),
        ({"attachments": (Attachment("file", "x", excerpt="x" * 4001),)}, "trop long"),
        ({"answer_mode": "dictée"}, "mode de réponse inconnu"),
    ],
)
def test_une_question_v2_invalide_est_refusee_a_l_ecriture(tmp_path: Path, fields: dict, fragment: str) -> None:
    assert fragment in question_problem(Question("q", "choice", "t", **fields))
    service, tid = _running(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        service.ask(tid, "Que faire ?", "claude", **fields)
    assert service.require(tid).status is TaskState.RUNNING  # rien n'a été écrit


def test_la_lecture_d_un_ledger_plus_recent_ne_leve_pas() -> None:
    bad = Question.from_dict(
        {**V1_LINE, "choices": [{"value": "a"}, {"value": "a"}], "attachments": [{"type": "hologramme"}]}
    )
    assert Waiting("human", "", bad).to_dict()["question"][
        "choices"
    ]  # relu tel quel ; seul l'écrivain appelle problem()


def test_ask_v2_derive_text_options_et_recommandation(tmp_path: Path) -> None:
    service, tid = _running(tmp_path)
    service.ask(
        tid,
        "",
        "claude",
        title="Reprendre la tâche ou l'abandonner ?",
        context="La session s'est arrêtée.",
        choices=CHOICES,
        recommendation=Recommendation("reprendre", "travail avancé"),
    )
    question = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert question.text.startswith("Reprendre la tâche") and question.options == ("reprendre", "abandonner")
    assert question.recommended == "reprendre" and question.task_title == "Travail" and question.round == 1


def test_le_drapeau_choice_et_attach_se_lisent() -> None:
    assert parse_choice("!abandonner=Abandonner|Annule la tâche") == Choice(
        "abandonner", "Abandonner", "Annule la tâche", True
    )
    assert parse_attach("link:https://github.com/o/r/pull/1:La PR") == Attachment(
        "link", "La PR", ref="https://github.com/o/r/pull/1"
    )
    assert parse_attach("link:http://localhost:8080").ref == "http://localhost:8080"
    assert parse_attach("file:src/a.py:Le fichier").ref == "src/a.py"
    kwargs = ask_kwargs(title="T", recommend="a", because="parce que", choices=["a=A", "b=B"])
    assert kwargs["recommendation"] == Recommendation("a", "parce que") and len(kwargs["choices"]) == 2


def test_split_legacy() -> None:
    assert split_legacy("Fusionner la PR ? Le merger ne la fusionne pas.", "merge") == (
        "Fusionner la PR ?",
        "Le merger ne la fusionne pas.",
    )
    assert split_legacy("Du texte sans question", "merge")[0] == "Fusion : décision attendue"


def test_l_api_d_attention_rend_les_rounds_avec_reponse_et_suite_du_ledger(tmp_path: Path) -> None:
    service, tid = _running(tmp_path)
    fields = {"title": "Reprendre ?", "choices": CHOICES, "options": ("reprendre", "abandonner")}
    service.ask(tid, "Reprendre ?", "claude", **fields)
    first = service.require(tid).waiting.question  # type: ignore[union-attr]
    service.answer(tid, "reprendre", "guilhem", origin="human", answer_data={"choices": ["reprendre"], "note": "ok"})
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    service.ask(tid, "Reprendre ?", "claude", **fields)
    second = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert (first.round, second.round, second.supersedes) == (1, 2, first.id)

    answered = [e for e in service.ledger.list_events(tid) if e.event_type == "task.human_answered"]
    assert answered[0].payload["answer_data"] == {"choices": ["reprendre"], "question_id": first.id, "note": "ok"}

    item = wa._question_v2(service.ledger, tid, second)
    one, two = item["rounds"]
    assert (one["round"], one["question_id"], one["answer_label"]) == (1, first.id, "Reprendre la tâche")
    assert one["answered_at"] and one["aftermath"]  # lue au ledger (la transition qui a suivi la réponse)
    assert (two["round"], two["answered_at"], two["answer_label"], two["aftermath"]) == (2, "", "", "")
    assert item["question"]["round"] == 2 and item["question"]["title"] == "Reprendre ?"


def test_answer_data_refuse_un_choix_inconnu(tmp_path: Path) -> None:
    service, tid = _running(tmp_path)
    service.ask(tid, "Reprendre ?", "claude", title="Reprendre ?", choices=CHOICES)
    with pytest.raises(ValueError, match="choix inconnu"):
        service.answer(tid, "x", "guilhem", origin="human", answer_data={"choices": ["x"]})


def test_task_ask_v2_par_la_cli_et_par_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    service, tid = _running(tmp_path)
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "q.json"
    spec.write_text(json.dumps({"title": "Reprendre ?", "context": "La session s'est arrêtée.", "choices": [
        {"value": "reprendre", "label": "Reprendre la tâche"}, {"value": "abandonner", "label": "Abandonner", "danger": True}],
        "recommendation": {"value": "reprendre", "reason": "travail avancé"}}), encoding="utf-8")
    bad = CliRunner().invoke(app, ["task", "ask", tid, "--title", "T", "--choice", "a=A", "--choice", "a=B",
                                   "--project-root", str(tmp_path)])
    assert bad.exit_code == 1 and "en double" in bad.output
    done = CliRunner().invoke(app, ["task", "ask", tid, "--spec", str(spec), "--attach", "link:https://example.org/pr/1:La PR",
                                    "--project-root", str(tmp_path)])
    assert done.exit_code == 0, done.output
    question = TaskService(tmp_path).require(tid).waiting.question  # type: ignore[union-attr]
    assert question.title == "Reprendre ?" and question.recommended == "reprendre" and question.options == ("reprendre", "abandonner")
    assert [a.ref for a in question.attachments] == ["https://example.org/pr/1"]
