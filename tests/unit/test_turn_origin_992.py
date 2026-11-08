"""#992 : un réveil programmé, une notification de tâche ou un ``claude -p`` n'attestent pas un geste humain.

L'entrée du hook ``UserPromptSubmit`` ne dit pas qui a écrit le prompt, et le journal de session n'existe pas encore
au moment du hook (sonde Claude Code 2.1.319, 2026-11-07). Le hook note ``prompt_id`` et ``transcript_path`` ;
l'attestation lit ensuite, dans ce journal, le ``turnOrigin`` de l'entrée ``user`` qui porte ce ``promptId``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.flows.attestation import ATTESTED, NO_HUMAN_TURN, TURN_NOT_HUMAN, UNATTESTED, attest
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.decisions.human_turns import load_turns
from grimoire.hosts.events import HookEvent


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _challenge() -> dict[str, str]:
    return {"challenge_id": "MCH-test", "session_id": "S1", "ts": datetime.now(UTC).isoformat()}


def _turn(root: Path, prompt_id: str = "", transcript: Path | None = None, prompt: str = "ok") -> None:
    """Le vrai chemin du hook, avec l'entrée brute que Claude Code 2.1.319 envoie."""
    raw = {"prompt_id": prompt_id, "transcript_path": str(transcript)} if prompt_id and transcript else {}
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt=prompt,
            session_id="S1",
            host="host-claude-code-cli",
            raw=raw,
        ),
    )
    time.sleep(0.02)


def _transcript(path: Path, *entries: dict[str, object]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


def _user(prompt_id: str, **marks: object) -> dict[str, object]:
    return {"type": "user", "promptId": prompt_id, "message": {"role": "user", "content": "ok"}, **marks}


def test_le_hook_note_l_identifiant_du_prompt_et_le_journal(project: Path, tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    _turn(project, "P1", transcript)
    turns = load_turns(project)
    assert turns and turns[-1].prompt_id == "P1" and turns[-1].transcript_path == str(transcript)


def test_un_message_tape_par_l_humain_atteste(project: Path, tmp_path: Path) -> None:
    challenge = _challenge()
    transcript = _transcript(tmp_path / "s.jsonl", _user("P1", turnOrigin="human", origin={"kind": "human"}))
    _turn(project, "P1", transcript)
    assert attest(project, challenge).status == ATTESTED


@pytest.mark.parametrize(
    ("marks", "origin"),
    [
        ({"turnOrigin": "scheduled", "isMeta": True, "wakeupSource": "loop_wakeup"}, "scheduled"),
        ({"origin": {"kind": "task-notification"}}, "task-notification"),
        ({"turnOrigin": "sdk"}, "sdk"),
    ],
)
def test_un_tour_non_ecrit_par_l_humain_n_atteste_pas(
    project: Path, tmp_path: Path, marks: dict[str, object], origin: str
) -> None:
    challenge = _challenge()
    transcript = _transcript(tmp_path / "s.jsonl", _user("P1", **marks))
    _turn(project, "P1", transcript)
    result = attest(project, challenge)
    assert result.status == UNATTESTED and result.reason == f"{TURN_NOT_HUMAN}:{origin}"


def test_un_reveil_apres_un_vrai_message_laisse_le_vrai_message_attester(project: Path, tmp_path: Path) -> None:
    challenge = _challenge()
    transcript = _transcript(
        tmp_path / "s.jsonl", _user("P1", turnOrigin="human"), _user("P2", turnOrigin="scheduled", isMeta=True)
    )
    _turn(project, "P1", transcript, prompt="oui vas-y")
    _turn(project, "P2", transcript, prompt="réveil")
    assert attest(project, challenge).status == ATTESTED


def test_un_prompt_introuvable_au_journal_n_atteste_pas(project: Path, tmp_path: Path) -> None:
    challenge = _challenge()
    _turn(project, "P1", tmp_path / "absent.jsonl")
    result = attest(project, challenge)
    assert result.status == UNATTESTED and result.reason == f"{TURN_NOT_HUMAN}:unknown"


def test_un_hote_sans_prompt_id_garde_la_regle_d_avant(project: Path) -> None:
    challenge = _challenge()
    _turn(project)
    assert attest(project, challenge).status == ATTESTED


def test_le_journal_est_lu_prudemment(tmp_path: Path) -> None:
    """Lignes illisibles, entrées d'autres types au même promptId, prompt absent, entrée sans marque d'origine."""
    from grimoire.hosts.decisions.human_turns import HumanTurn, host_turn_origin

    path = tmp_path / "s.jsonl"
    path.write_text(
        '{"promptId": "P1", coupée\n'
        + json.dumps({"type": "assistant", "promptId": "P1"})
        + "\n"
        + json.dumps(_user("P1", turnOrigin="human"))
        + "\n"
        + json.dumps(_user("P3"))
        + "\n",
        encoding="utf-8",
    )

    def turn(prompt_id: str) -> HumanTurn:
        return HumanTurn("S1", "x", "2026-11-07T00:00:00+00:00", "user", prompt_id, str(path))

    assert host_turn_origin(turn("P1")) == "human"  # la ligne coupée et l'entrée assistant sont sautées
    assert host_turn_origin(turn("P2")) == "unknown"  # journal lisible, prompt absent
    assert host_turn_origin(turn("P3")) == "unknown"  # entrée user sans turnOrigin ni origin
    assert host_turn_origin(turn("")) == ""  # pas d'identifiant : règle d'avant


def test_sans_aucun_tour_le_motif_reste_le_meme(project: Path) -> None:
    challenge = _challenge()
    _turn(project)  # un tour avant le challenge ne compte pas
    later = {**challenge, "ts": datetime.now(UTC).isoformat()}
    assert attest(project, later).reason == NO_HUMAN_TURN
