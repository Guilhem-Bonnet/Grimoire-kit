"""Questions courtes et entretien de cadrage par messages vocaux (#1343 V4, #1342), moteurs et agent simulés."""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.core import cadrage_interview
from grimoire.tools import workspace_chat, workspace_chat_pilot, workspace_routes
from grimoire.voice import interview, outbox_service, stt_service
from grimoire.voice.engines import EngineSpec, Speech, SpeechToText, TextToSpeech, Transcript, register_engine
from grimoire.voice.outbox import QUESTION_MAX_S, Outbox, asks_a_question, spoken_instruction, spoken_question

TURN = "1a2b3c4d"


class FakeTTS(TextToSpeech):
    spoken: ClassVar[list[str]] = []

    def __init__(self, **_options: Any) -> None:
        pass

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        FakeTTS.spoken.append(text)
        return Speech(text=text, audio=b"RIFFfake", sample_rate=24_000)


class FakeSTT(SpeechToText):
    heard: ClassVar[str] = ""

    def __init__(self, **_options: Any) -> None:
        pass

    def transcribe(self, audio: Any, *, language: str | None = None, vocabulary: Sequence[str] = ()) -> Transcript:
        return Transcript(text=FakeSTT.heard, language="fr", duration_s=1.0)


def wav() -> str:
    n = 3200
    header = (
        b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
    )
    return base64.b64encode(header + b"data" + struct.pack("<I", n) + b"\0" * n).decode("ascii")


@pytest.fixture
def project(tmp_path: Path, isolated_registry: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    register_engine(EngineSpec("fake", "tts", "Faux TTS", f"{__name__}:FakeTTS"))
    register_engine(EngineSpec("fake", "stt", "Faux STT", f"{__name__}:FakeSTT", ("json",)))
    FakeTTS.spoken = []
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project-context.yaml").write_text(
        "interaction:\n  mode: voice\nvoice:\n  tts: fake\n  stt: fake\n  spoken_max_seconds: 45\n", encoding="utf-8"
    )
    outbox_service.reset_outboxes()
    yield root
    outbox_service.reset_outboxes()
    stt_service.reset_engines()


class Agent:
    """Faux agent ACP : il lit la consigne du tour (balise au jeton), agit, puis répond par écrit + message parlé."""

    def __init__(self) -> None:
        self.state = "ready"
        self.prompts: list[str] = []
        self.act: Callable[[str], None] = lambda _text: None
        self.speak = ""  # le message parlé du tour ; vide : pas de balise
        self.conv: workspace_chat.Conversation | None = None

    def prompt(self, text: str, on_permission: Any = None) -> Iterator[dict[str, Any]]:
        self.prompts.append(text)
        self.act(text)
        if self.speak and self.conv is not None:
            token = text.split("<voix-")[1].split(">")[0]
            self.conv._turn_text.append(f"Écrit.\n<voix-{token}>{self.speak}</voix-{token}>")
        yield {"type": "turn_ended", "stop_reason": "end_turn"}


def conversation(root: Path, agent: Agent, events: list[dict[str, Any]]) -> workspace_chat.Conversation:
    conv = workspace_chat.Conversation(root)
    conv.ensure_started = lambda: None  # type: ignore[method-assign]
    conv._client = agent  # type: ignore[assignment]
    conv._emit = events.append  # type: ignore[method-assign]
    conv._maybe_schedule_close = lambda: None  # type: ignore[method-assign]
    agent.conv = conv
    return conv


QUESTION = "Qui utilisera ce tableau de bord, et dans quelle situation ?"


# ── la consigne vocale ────────────────────────────────────────────────────────


def test_interview_brief_asks_one_short_question_and_the_answer_call() -> None:
    brief = spoken_instruction(45, TURN, "principal")
    assert "UNE seule question" in brief and "grimoire cadrage interview answer" in brief
    assert "--chantier" not in brief and "transcription" in brief
    assert "--chantier refonte answer" in spoken_instruction(45, TURN, "refonte")
    assert "UNE seule question" not in spoken_instruction(45, TURN)


def test_interview_brief_only_when_an_interview_is_open(project: Path) -> None:
    conv = workspace_chat.Conversation(project)
    assert "UNE seule question" not in conv._with_spoken_brief("Salut")
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord pour l'équipe")
    assert "UNE seule question" in conv._with_spoken_brief("Salut") and conv._interview == "principal"
    for angle in cadrage_interview.interview_plan(project)["open_required"]:
        cadrage_interview.answer(project, None, angle, "ok")
    conv._with_spoken_brief("Salut")
    assert conv._interview is None  # entretien terminé : plus de consigne de question


# ── état visible, lecture seule ───────────────────────────────────────────────


def test_interview_view_costs_nothing_without_an_interview(project: Path) -> None:
    assert interview.interview_view(project) == {"active": False, "interviews": []}
    assert interview.open_interview(project) is None
    assert not (project / "_grimoire").exists()  # rien d'écrit
    assert workspace_routes.PREFIX + "voice/interview" in workspace_routes.GET_ROUTES


def test_interview_view_shows_angle_states_and_writes_nothing(project: Path) -> None:
    plan = cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord pour l'équipe")
    cadrage_interview.answer(project, None, "utilisateurs", "L'équipe support")
    cadrage_interview.answer(project, None, "existant", "sans objet ici", skip=True)
    before = (project / "_grimoire" / "cadrage" / "entretien.json").read_bytes()
    view = interview.interview_view(project)
    assert (project / "_grimoire" / "cadrage" / "entretien.json").read_bytes() == before
    assert view["active"] is True and len(view["interviews"]) == 1
    states = {a["id"]: a["state"] for a in view["interviews"][0]["angles"]}
    assert states["utilisateurs"] == "couvert" and states["existant"] == "ecarte"
    assert len(states) == len(plan["angles"]) and "ouvert" in states.values()


# ── la question vocale ────────────────────────────────────────────────────────


def test_interview_spoken_question_is_short_and_keeps_options_on_screen() -> None:
    long_text = "Voulez-vous que le tableau de bord couvre " + "tous les services de l'entreprise " * 20 + "?"
    spoken = spoken_question(long_text, ("Oui", "Non"))
    assert len(spoken.split()) <= QUESTION_MAX_S * 2.5 + 8 and "écran" in spoken
    short = spoken_question("On fusionne ?", ("Oui", "Non", "Plus tard"))
    assert short == "On fusionne ? Options : Oui, Non ou Plus tard."
    assert "`" not in spoken_question("Lancer `rm -r x` dans src/a/b.py ?", ())
    assert asks_a_question("Qui ? Le détail est à l'écran.") and not asks_a_question("C'est fait.")


def test_interview_pilot_question_becomes_a_short_marked_message(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {"task_id": "T-1", "question_id": "q1", "text": "Fusionner la PR ?", "options": ["Oui", "Non"]}
    rows: list[dict[str, Any]] = [row]
    monkeypatch.setattr(workspace_chat_pilot, "pending_questions", lambda _root: list(rows))
    events: list[dict[str, Any]] = []
    conv = conversation(project, Agent(), events)
    conv.pull_questions(interval=0.0)
    voiced = [e["message"] for e in events if e["type"] == "voice_message"]
    assert len(voiced) == 1
    message = voiced[0]
    assert message["kind"] == "question" and message["options"] == ["Oui", "Non"] and message["answered"] is False
    assert message["ref"] == {"task_id": "T-1", "question_id": "q1"}
    assert message["duration_s"] <= QUESTION_MAX_S and "Options : Oui ou Non" in message["text"]
    conv.pull_questions(interval=0.0)  # un second relevé ne redépose pas la question
    assert len([e for e in events if e["type"] == "voice_message"]) == 1
    rows.clear()
    conv.pull_questions(interval=0.0)
    last = [e["message"] for e in events if e["type"] == "voice_message"][-1]
    assert last["id"] == message["id"] and last["answered"] is True


def test_interview_unanswered_question_is_not_overwritten_by_a_report(project: Path) -> None:
    outbox_service.record_reply(project, "<voix-" + TURN + ">Premier compte rendu.</voix-" + TURN + ">", TURN)
    asked = outbox_service.record_pilot_question(
        project, {"task_id": "T-1", "question_id": "q1", "text": "On continue ?", "options": ["Oui", "Non"]}
    )
    assert asked is not None
    for i in range(3):  # des comptes rendus suivants, non écoutés : ils se remplacent entre eux, jamais la question
        outbox_service.record_reply(project, f"<voix-{TURN}>Suite {i}.</voix-{TURN}>", TURN)
    messages = outbox_service.outbox_state(project)["messages"]
    assert messages[0]["kind"] == "question" and messages[0]["id"] == asked["id"]  # en tête
    assert [m["text"] for m in messages if m["kind"] == "report"] == ["Suite 2."]
    assert messages[0]["text"].startswith("On continue ?")
    outbox_service.resolve_pilot_question(project, "T-1", "q1")
    after = outbox_service.outbox_state(project)["messages"]
    assert after[0]["kind"] == "report" and after[-1]["answered"] is True  # répondue : plus en tête


def test_interview_unanswered_question_survives_the_trim() -> None:
    box = Outbox(lambda text: Speech(text=text, audio=b"x", sample_rate=1), keep=3)
    asked = box.submit_question("Une question ?", (), ("T", "q"))
    assert asked is not None
    for i in range(6):
        box._open(f"rapport {i}", "rules")
    ids = [m["id"] for m in box.state()["messages"]]
    assert asked.id in ids and len(ids) <= 4


def test_interview_question_is_frozen_when_played(project: Path) -> None:
    asked = outbox_service.record_pilot_question(
        project, {"task_id": "T", "question_id": "q", "text": "Prêt ?", "options": []}
    )
    assert asked is not None
    played = outbox_service.outbox_play(project, {"id": asked["id"]})
    assert played["message"]["played"] is True and FakeTTS.spoken == ["Prêt ?"]
    assert played["message"]["answered"] is False  # écoutée mais toujours en attente de réponse


# ── de bout en bout : question vocale, réponse vocale, réponse consignée ──────


def test_interview_end_to_end_voice_question_then_voice_answer_is_recorded(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord pour l'équipe")
    answered: list[tuple[Any, ...]] = []
    real_answer = cadrage_interview.answer

    def spy(root: Path, chantier: str | None, angle_id: str, text: str, **kw: Any) -> dict[str, Any]:
        answered.append((chantier, angle_id, text, kw))
        return real_answer(root, chantier, angle_id, text, **kw)

    monkeypatch.setattr(cadrage_interview, "answer", spy)
    events: list[dict[str, Any]] = []
    agent = Agent()
    conv = conversation(project, agent, events)

    # 1. L'orchestrateur pose la première question de l'entretien : message vocal bref marqué question.
    agent.speak = QUESTION
    conv._run_turn("Je veux un tableau de bord")
    assert "UNE seule question" in agent.prompts[0] and "<voix-" in agent.prompts[0]
    messages = outbox_service.outbox_state(project)["messages"]
    assert [m["kind"] for m in messages] == ["question"] and messages[0]["text"] == QUESTION
    assert messages[0]["answered"] is False and messages[0]["duration_s"] <= QUESTION_MAX_S
    assert any(e["type"] == "voice_message" and e["message"]["kind"] == "question" for e in events)

    # 2. La personne écoute la question, puis répond en parlant : transcription affichée dans la saisie.
    outbox_service.outbox_play(project, {"id": messages[0]["id"]})
    FakeSTT.heard = "  l'équipe support,   surtout   Aïcha "
    heard = stt_service.transcribe(project, {"audio": wav()})["text"]
    assert heard.strip().startswith("l'équipe support")

    # 3. Corrigée puis envoyée : un tour de l'orchestrateur ; la question en attente est répondue.
    corrected = "L'équipe support, surtout Aïcha"
    agent.act = lambda _text: cadrage_interview.answer(project, None, "utilisateurs", corrected)
    agent.speak = "Que fait cette équipe aujourd'hui pour s'en passer ?"
    conv._run_turn(corrected)
    assert answered == [(None, "utilisateurs", corrected, {})]
    assert agent.prompts[1].startswith(corrected) and "UNE seule question" in agent.prompts[1]
    after = outbox_service.outbox_state(project)["messages"]
    asked_now = [m for m in after if m["kind"] == "question"]
    assert [m["answered"] for m in asked_now] == [False, True]  # la nouvelle en tête, l'ancienne répondue

    # 4. La réponse est consignée dans le cadrage comme une réponse écrite, et l'état visible la montre.
    brief = (project / "_grimoire" / "cadrage" / "01-brief.md").read_text(encoding="utf-8")
    assert corrected in brief
    angles = {a["id"]: a for a in interview.interview_view(project)["interviews"][0]["angles"]}
    assert angles["utilisateurs"]["state"] == "couvert" and angles["utilisateurs"]["answers"] == 1

    # 5. La question suivante est un message distinct, en tête ; le texte écrit du tour n'est pas lu.
    assert asked_now[0]["text"].startswith("Que fait cette équipe") and asked_now[0]["id"] != messages[0]["id"]
    assert all(m["kind"] == "question" for m in after)


def test_interview_answer_without_question_mark_is_a_report_not_a_question(project: Path) -> None:
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord pour l'équipe")
    events: list[dict[str, Any]] = []
    agent = Agent()
    conv = conversation(project, agent, events)
    agent.speak = "L'entretien est terminé, merci."
    conv._run_turn("Bonjour")
    messages = outbox_service.outbox_state(project)["messages"]
    assert [m["kind"] for m in messages] == ["report"]


def test_interview_question_message_is_json_serialisable(project: Path) -> None:
    asked = outbox_service.record_pilot_question(
        project, {"task_id": "T", "question_id": "q", "text": "Choix ?", "options": ["A", "B"]}
    )
    assert asked is not None and json.loads(json.dumps(asked))["options"] == ["A", "B"]


# ── relecture adversariale : le texte affiché est le texte complet, les bornes tiennent ───────────────────────


DANGEROUS = (
    "Puis-je lancer `rm -rf ~/projets` et pousser en force sur /home/guilhem/prod/repo ? "
    "Cela supprime tout l'historique distant, ce qui est irréversible."
)


def test_interview_question_shows_the_full_original_text_not_the_spoken_one(project: Path) -> None:
    row = {
        "task_id": "T",
        "question_id": "q",
        "text": DANGEROUS,
        "options": ["Oui", "Non"],
        "why": "nettoyage du dépôt",
        "kept": "catégorie réservée à l'humain",
        "reserved": True,
    }
    message = outbox_service.record_pilot_question(project, row)
    assert message is not None
    assert "rm -rf ~/projets" in message["full_text"] and "/home/guilhem/prod/repo" in message["full_text"]
    assert "nettoyage du dépôt" in message["full_text"] and "catégorie réservée" in message["full_text"]
    assert "rm -rf" not in message["text"] and "/home/" not in message["text"]  # le parlé est nettoyé
    assert message["reserved"] is True


def test_interview_question_without_reserved_flag_is_treated_as_reserved(project: Path) -> None:
    message = outbox_service.record_pilot_question(
        project, {"task_id": "T", "question_id": "q", "text": "Ok ?", "options": ["Oui"]}
    )
    assert message is not None and message["reserved"] is True


def test_interview_options_and_waiting_questions_are_bounded() -> None:
    box = Outbox(lambda text: Speech(text=text, audio=b"x", sample_rate=1))
    first = box.submit_question("Q0 ?", tuple("o" * 1000 for _ in range(50)), ("t", "q0"))
    assert first is not None and first.options == () and first.options_hidden == 50  # trop nombreuses : aucun bouton
    for i in range(1, 60):
        box.submit_question(f"Q{i} ?", (), ("t", f"q{i}"))
    state = box.state()
    waiting = [m for m in state["messages"] if m["kind"] == "question"]
    assert len(waiting) == 20 and state["older_questions"] == 40
    assert waiting[0]["text"] == "Q40 ?"  # les plus anciennes sont évincées


def test_interview_state_refuses_deep_nesting_oversize_and_symlinked_base(project: Path, tmp_path: Path) -> None:
    base = project / "_grimoire" / "cadrage"
    base.mkdir(parents=True)
    (base / "entretien.json").write_text("[" * 100_000, encoding="utf-8")
    view = interview.interview_view(project)
    assert view["interviews"][0]["chantier"] == "principal" and "error" in view["interviews"][0]
    (base / "entretien.json").write_text(json.dumps({"depth": "standard", "pad": "x" * 1_100_000}), encoding="utf-8")
    assert "error" in interview.interview_view(project)["interviews"][0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "entretien.json").write_text(json.dumps({"depth": "standard", "angles": {}}), encoding="utf-8")
    elsewhere = tmp_path / "p2"
    (elsewhere / "_grimoire").mkdir(parents=True)
    (elsewhere / "_grimoire" / "cadrage").symlink_to(outside, target_is_directory=True)
    rows = interview.interview_view(elsewhere)["interviews"]
    assert len(rows) == 1 and "error" in rows[0] and "kind_label" not in rows[0]
    assert interview.open_interview(elsewhere) is None


def test_interview_chantier_name_is_matched_in_full(project: Path) -> None:
    named = project / "_grimoire" / "cadrage" / "refonte\n"
    named.mkdir(parents=True)
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord")
    (named / "entretien.json").write_text(
        (project / "_grimoire" / "cadrage" / "entretien.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for angle in cadrage_interview.interview_plan(project)["open_required"]:
        cadrage_interview.answer(project, None, angle, "ok")  # le principal est terminé
    assert interview.open_interview(project) is None  # « refonte\n » n'est pas un nom sûr


# ── seconde contre-vérification : texte tronqué, caractères de contrôle, fichiers non ordinaires ──────────────


def _box() -> Outbox:
    return Outbox(lambda text: Speech(text=text, audio=b"x", sample_rate=1))


def test_interview_long_question_keeps_reasons_first_and_marks_the_cut() -> None:
    box = _box()
    body = "Début. " + "mot " * 3000 + "FIN DANGEREUSE : pousser en force"
    message = box.submit_question(
        "Court ?",
        ("Oui", "Non"),
        ("t", "q"),
        full_text=body,
        context="Reste à vous : réservé\nPourquoi : destructif",
        reserved=False,
    )
    assert message is not None
    assert message.full_text.startswith("Reste à vous : réservé\nPourquoi : destructif")  # la raison d'abord
    assert message.full_text.endswith("(texte tronqué : la question complète est dans le fil)")
    assert "FIN DANGEREUSE" not in message.full_text and len(message.full_text) <= 4000
    assert message.truncated is True and message.options == () and message.options_hidden == 2  # aucun bouton


def test_interview_short_question_is_not_truncated_and_keeps_its_buttons() -> None:
    message = _box().submit_question("Q ?", ("Oui", "Non"), ("t", "q"), full_text="Fusionner ?", reserved=False)
    assert message is not None and message.truncated is False and message.options == ("Oui", "Non")


@pytest.mark.parametrize("value", [None, 0, "", "no", 1])
def test_interview_reserved_is_true_unless_exactly_false(project: Path, value: Any) -> None:
    row = {"task_id": "T", "question_id": f"q{value!r}", "text": "Ok ?", "options": ["Oui"], "reserved": value}
    message = outbox_service.record_pilot_question(project, row)
    assert message is not None and message["reserved"] is True
    row = {"task_id": "T", "question_id": "qf", "text": "Ok ?", "options": ["Oui"], "reserved": False}
    free = outbox_service.record_pilot_question(project, row)
    assert free is not None and free["reserved"] is False and free["options"] == ["Oui"]


def test_interview_control_and_bidi_characters_never_reach_the_display_or_a_button() -> None:
    box = _box()
    message = box.submit_question(
        "Ligne\u202e inversée ?",
        ("oui\u202e", "non\nsuite", "ok"),
        ("t", "q"),
        full_text="Texte\u202e\nsur deux lignes\x07",
        reserved=False,
    )
    assert message is not None
    assert message.options == ("ok",) and message.options_hidden == 2  # une option modifiée n'a pas de bouton
    assert "\u202e" not in message.full_text and "\x07" not in message.full_text and "\n" in message.full_text
    assert "\u202e" not in message.text
    cut = box.submit_question("Q2 ?", ("x" * 201, "y" * 200), ("t", "q2"), reserved=False)
    assert cut is not None and cut.options == ("y" * 200,) and cut.options_hidden == 1
    many = box.submit_question("Q3 ?", tuple(str(i) for i in range(9)), ("t", "q3"), reserved=False)
    assert many is not None and many.options == () and many.options_hidden == 9


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="pas de FIFO ici")
def test_interview_fifo_state_is_refused_without_blocking(project: Path) -> None:
    base = project / "_grimoire" / "cadrage"
    base.mkdir(parents=True)
    os.mkfifo(base / "entretien.json")
    started = time.monotonic()
    view = interview.interview_view(project)
    assert interview.open_interview(project) is None
    assert time.monotonic() - started < 5  # jamais bloqué sur la FIFO
    assert "error" in view["interviews"][0]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="pas de FIFO ici")
def test_interview_fifo_brief_is_not_read(project: Path) -> None:
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord")
    os.mkfifo(project / "_grimoire" / "cadrage" / "01-brief.md")
    started = time.monotonic()
    with pytest.raises(ValueError, match="ordinaire"):
        cadrage_interview.answer(project, None, "utilisateurs", "L'équipe")
    assert cadrage_interview._request_text(project, None)  # le brief FIFO n'est pas lu, la demande reste calculable
    assert time.monotonic() - started < 5


def test_interview_empty_directories_do_not_hide_the_real_chantier(project: Path) -> None:
    base = project / "_grimoire" / "cadrage"
    for i in range(60):
        (base / f"a{i:02d}").mkdir(parents=True)
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord")
    real = base / "zz-vrai"
    real.mkdir()
    (real / "entretien.json").write_text((base / "entretien.json").read_text(encoding="utf-8"), encoding="utf-8")
    names = [r["chantier"] for r in cadrage_interview.interview_states(project)]
    assert names == ["principal", "zz-vrai"]


def test_interview_states_list_only_safe_chantier_names(project: Path) -> None:
    base = project / "_grimoire" / "cadrage"
    cadrage_interview.interview_plan(project, depth="standard", text="Un tableau de bord")
    payload = (base / "entretien.json").read_text(encoding="utf-8")
    for name in ("a\nb", "Maj", "ok-1"):
        (base / name).mkdir()
        (base / name / "entretien.json").write_text(payload, encoding="utf-8")
    assert [r["chantier"] for r in cadrage_interview.interview_states(project)] == ["principal", "ok-1"]
