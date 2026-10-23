"""CHAIN-ACK : une rupture du journal de preuve s'explique par un humain, sans réécrire le journal.

Avant : ``evidence.chain_broken`` demandait que « la rupture soit expliquée » mais aucun mécanisme ne
l'permettait ; la ligne sans ``prev`` d'un hook d'ancienne version laissait ``standard verify`` et le
gate ``Stop`` rouges pour toujours, et une session sans tâche (repli ``bootstrap``) bloquée à chaque
fin de tour. Maintenant : diagnostic nommé (ligne non chaînée intercalée, la chaîne continue autour) et
acquittement nominatif, tapé par l'humain, lié aux empreintes de la ligne et de ses voisines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile
from grimoire.core.standard_checks.evidence_chain import analyze_chain
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    evidence_log_relpath,
)
from grimoire.hosts.decisions import HookInput, Outcome, decide_task_context, decide_tool_policy
from grimoire.hosts.decisions import chain_ack as ca
from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX
from grimoire.hosts.events import HookEvent

TASK = "bootstrap"
HOST = "claude-code-cli"
pytestmark = pytest.mark.covers("cli:evidence chain explain")
runner = CliRunner()


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id=TASK)
    return tmp_path


def _log(root: Path) -> Path:
    return root / evidence_log_relpath(TASK)


def _event(root: Path, index: int) -> None:
    append_evidence_event(root, TASK, build_bash_event(f"echo {index}", exit_code=0))


def _old_writer_line(root: Path) -> int:
    """Ajoute à la main une ligne sans ``prev`` (hook d'une version antérieure) ; rend son numéro."""
    with open(_log(root), "ab") as handle:
        handle.write(json.dumps({"ts": "2026-10-20T12:24:44Z", "type": "bash", "command": "pip install -U grimoire-kit"}).encode() + b"\n")
    return len(_log(root).read_bytes().splitlines())


@pytest.fixture
def broken(governed: Path) -> tuple[Path, int]:
    """Journal 3 lignes chaînées, une ligne sans ``prev``, puis 3 lignes chaînées sur elle (le cas réel)."""
    for index in range(3):
        _event(governed, index)
    line = _old_writer_line(governed)
    for index in range(3, 6):
        _event(governed, index)
    return governed, line


def _checks(root: Path) -> dict[str, str]:
    result = verify_standard_profile(root, task_id=TASK)
    return {check.id: check.severity for check in result.checks if check.id.startswith("evidence.chain")}


def _hook(root: Path, prompt: str, *, session: str = "s-1", host: str = HOST, event: HookEvent = HookEvent.USER_PROMPT_SUBMIT) -> HookInput:
    return HookInput(event=event, project_root=root, prompt=prompt, session_id=session, host=host)


def _phrase(line: int, reason: str = "pip install -U pendant la mise à jour du kit") -> str:
    return ca.explain_phrase(TASK, line, reason)


def test_unchained_line_between_chained_ones_is_named_not_called_a_modified_line(broken: tuple[Path, int]) -> None:
    root, line = broken
    report = analyze_chain(_log(root))
    assert report.status == "broken" and report.kind == "unchained_inserted"
    assert [item.line for item in report.inserted] == [line]
    assert "sans `prev`" in report.message and "la chaîne continue autour" in report.message
    assert "a été modifiée" not in report.message
    result = verify_standard_profile(root, task_id=TASK)
    [error] = [c for c in result.checks if c.id == "evidence.chain_broken"]
    assert error.severity == "error" and f"ligne(s) {line}" in error.message
    assert f"Explain Chain {TASK} line {line}:" in error.message


def test_human_typed_phrase_turns_the_error_into_a_listed_warning_and_never_rewrites_the_log(broken: tuple[Path, int]) -> None:
    root, line = broken
    before = _log(root).read_bytes()
    head_before = (_log(root).with_name("evidence-log.head.json")).read_bytes()
    context, detail = ca.record_chain_ack_request(_hook(root, _phrase(line)))
    assert detail["chain_ack"]["status"] == "recorded" and "expliquée" in context
    assert _log(root).read_bytes() == before  # jamais réécrit ni rechaîné
    assert (_log(root).with_name("evidence-log.head.json")).read_bytes() == head_before
    [ack] = ca.read_acks(root, TASK)
    assert ack["line"] == line and ack["reason"].startswith("pip install") and ack["author"]["session_id"] == "s-1"
    assert ack["author"]["host"] == HOST and ack["ts"]

    checks = _checks(root)
    assert checks["evidence.chain_break_explained"] == "warning"
    assert "evidence.chain_broken" not in checks  # expliquée : plus une erreur
    result = verify_standard_profile(root, task_id=TASK)
    [shown] = [c for c in result.checks if c.id == "evidence.chain_break_explained"]
    assert f"ligne {line}" in shown.message and "pip install" in shown.message and "s-1" in shown.message
    assert ack["ts"] in shown.message  # la raison, l'auteur et la date sont cités : jamais masquée
    assert checks["evidence.chain_anchor_frozen"] == "warning"  # l'ancre figée à la rupture est dite


def test_typed_phrase_goes_through_the_user_prompt_hook(broken: tuple[Path, int]) -> None:
    root, line = broken
    decision = decide_task_context(_hook(root, _phrase(line)))
    assert decision.detail["chain_ack"]["status"] == "recorded"
    assert "evidence.chain_broken" not in _checks(root)


@pytest.mark.parametrize(
    ("session", "host", "event", "unattended"),
    [
        ("s-1", "", HookEvent.USER_PROMPT_SUBMIT, False),  # hôte inconnu : hook rejoué à la main
        ("s-1", HOST, HookEvent.USER_PROMPT_SUBMIT, True),  # GRIMOIRE_UNATTENDED=1 : agent non surveillé
        ("", HOST, HookEvent.USER_PROMPT_SUBMIT, False),  # pas de session
        ("s-1", HOST, HookEvent.POST_TOOL_USE, False),  # la phrase arrive par un outil, pas par un tour
    ],
)
def test_an_agent_cannot_acknowledge_its_own_break(
    broken: tuple[Path, int], monkeypatch: pytest.MonkeyPatch, session: str, host: str, event: HookEvent, unattended: bool
) -> None:
    root, line = broken
    if unattended:
        monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    context, detail = ca.record_chain_ack_request(_hook(root, _phrase(line), session=session, host=host, event=event))
    if event is HookEvent.USER_PROMPT_SUBMIT:
        assert detail["chain_ack"]["status"] == "refused" and "refusée" in context
    assert not (root / ca.acks_relpath(TASK)).exists()
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)
    assert _checks(root)["evidence.chain_broken"] == "error"


def test_the_cli_writes_nothing_and_prints_the_sentence_a_human_types(broken: tuple[Path, int]) -> None:
    root, line = broken
    out = runner.invoke(app, ["evidence", "chain", "explain", "--task-id", TASK, "--line", str(line), "--reason", "mise à jour", "--project-root", str(root)])
    assert out.exit_code == 0, out.output
    assert f"Explain Chain {TASK} line {line}: mise à jour" in out.output
    assert not (root / ca.acks_relpath(TASK)).exists()  # le CLI n'atteste aucune origine humaine
    assert _checks(root)["evidence.chain_broken"] == "error"


def test_the_cli_refuses_a_line_that_is_not_explainable(broken: tuple[Path, int]) -> None:
    root, line = broken
    out = runner.invoke(app, ["evidence", "chain", "explain", "--task-id", TASK, "--line", str(line - 1), "--project-root", str(root)])
    assert out.exit_code == 1


def test_a_different_break_is_not_covered_by_the_acknowledgement(broken: tuple[Path, int]) -> None:
    root, line = broken
    ca.record_chain_ack_request(_hook(root, _phrase(line)))
    assert "evidence.chain_broken" not in _checks(root)
    # Une seconde ligne sans prev, ailleurs : non couverte.
    second = _old_writer_line(root)
    _event(root, 99)
    result = verify_standard_profile(root, task_id=TASK)
    errors = [c for c in result.checks if c.id == "evidence.chain_broken"]
    assert len(errors) == 1 and f"{second}" in errors[0].message and f"ligne(s) {line}" not in errors[0].message
    assert any(c.id == "evidence.chain_break_explained" for c in result.checks)  # la première reste expliquée


def test_a_modified_neighbour_invalidates_the_acknowledgement(broken: tuple[Path, int]) -> None:
    root, line = broken
    ca.record_chain_ack_request(_hook(root, _phrase(line)))
    lines = _log(root).read_bytes().split(b"\n")
    lines[line - 2] = lines[line - 2].replace(b"echo 2", b"echo 222")  # la voisine précédente change
    _log(root).write_bytes(b"\n".join(lines))
    assert _checks(root)["evidence.chain_broken"] == "error"


def test_a_true_break_cannot_be_explained(governed: Path) -> None:
    for index in range(4):
        _event(governed, index)
    lines = _log(governed).read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b"echo 1", b"echo 999")  # ligne chaînée modifiée
    _log(governed).write_bytes(b"\n".join(lines))
    report = analyze_chain(_log(governed))
    assert report.status == "broken" and report.kind == "chain_broken" and not report.inserted
    for number in (2, 3):
        _context, detail = ca.record_chain_ack_request(_hook(governed, _phrase(number)))
        assert detail["chain_ack"]["status"] == "refused"
    assert _checks(governed)["evidence.chain_broken"] == "error"


def test_an_unchained_last_line_stays_a_break(governed: Path) -> None:
    for index in range(3):
        _event(governed, index)
    line = _old_writer_line(governed)  # rien ne la prolonge : ambiguïté, donc rupture
    report = analyze_chain(_log(governed))
    assert report.status == "broken" and report.kind == "chain_broken"
    _context, detail = ca.record_chain_ack_request(_hook(governed, _phrase(line)))
    assert detail["chain_ack"]["status"] == "refused"


def test_gate_check_stops_failing_on_the_explained_break(broken: tuple[Path, int]) -> None:
    from grimoire.core.agentic_standard import check_evidence_gates  # ce que `gate check` et le gate Stop évaluent

    def severities() -> dict[str, str]:
        result = check_evidence_gates(broken[0], task_id=TASK, target_state="review")
        return {c.id: c.severity for c in result.checks if c.id.startswith("evidence.chain")}

    assert severities()["evidence.chain_broken"] == "error"
    ca.record_chain_ack_request(_hook(broken[0], _phrase(broken[1])))
    after = severities()
    assert "evidence.chain_broken" not in after and after["evidence.chain_break_explained"] == "warning"


def test_the_acknowledgement_file_cannot_be_written_by_a_tool(broken: tuple[Path, int]) -> None:
    root, _line = broken
    assert ca.ACKS_FILENAME.endswith(AUTHORITY_SUFFIX)
    path = (root / ca.acks_relpath(TASK)).as_posix()
    forged = '{"type": "evidence.chain_ack"}'
    for tool, payload in (
        ("Write", {"file_path": path, "content": forged}),
        ("Edit", {"file_path": path, "old_string": "a", "new_string": forged}),
        ("Bash", {"command": f"echo '{forged}' >> {path}"}),
        ("Bash", {"command": "python -c \"import grimoire.hosts.decisions.chain_ack as c; c.record_chain_ack_request(None)\""}),
    ):
        hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name=tool, tool_input=payload, session_id="s-1", host=HOST)
        assert decide_tool_policy(hook).outcome is Outcome.DENY, tool


def test_a_quoted_phrase_inside_a_longer_prompt_acknowledges_nothing(broken: tuple[Path, int]) -> None:
    root, line = broken
    decide_task_context(_hook(root, f"La doc dit de taper « {_phrase(line)} », ne le fais pas."))
    assert not (root / ca.acks_relpath(TASK)).exists()
