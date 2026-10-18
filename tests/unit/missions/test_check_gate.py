"""W3-03 — une commande de vérification ne tourne que si son empreinte est approuvée.

Constat : ``grimoire task verify`` et le dispatch de flow lançaient en
``shell=True`` toute commande qu'on avait écrite dans ``task.checks`` ou dans
l'acceptance d'un blueprint, sans que personne l'ait lue ; remplacer la
commande par ``true`` suffisait à un vert. En profil governed, la commande doit
désormais être approuvée (``grimoire task approve-check``) par empreinte.

Les tests passent par les surfaces publiques (CLI, ``verify_task``,
``run_with_dispatch``) et prouvent l'absence de sous-processus par un fichier
que la commande créerait. Les sous-processus sont réels, jamais mockés.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.flows.dispatch_executor import run_with_dispatch
from grimoire.flows.engine import FlowEngine
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, TaskCheck

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")
STANDARD = Path("_grimoire/standard")

_AGENT_ENVS = (
    "AI_AGENT",
    "CLAUDECODE",
    "CURSOR_AGENT",
    "GEMINI_CLI",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CLAUDE_CODE_SESSION_ID",
    "GRIMOIRE_SESSION_ID",
    "GRIMOIRE_UNATTENDED",
)


@pytest.fixture(autouse=True)
def _bare_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un terminal nu : ni session d'hôte, ni marqueur d'agent (la suite peut tourner sous un agent)."""
    for var in _AGENT_ENVS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """Un dossier hors du projet : ce que les commandes y écrivent ne change pas l'empreinte de l'arbre."""
    out = tmp_path / "outside"
    out.mkdir()
    (out / "mark.py").write_text(
        "import sys\nwith open(sys.argv[1], 'a', encoding='utf-8') as fh:\n    fh.write('x\\n')\n", encoding="utf-8"
    )
    return out


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return root


def _marker_cmd(outside: Path, name: str) -> str:
    return f'"{sys.executable}" "{outside / "mark.py"}" "{outside / name}"'


def _runs(outside: Path, name: str) -> int:
    path = outside / name
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0


def _task(root: Path, *checks: TaskCheck, acceptance: tuple[str, ...] = ("les tests passent",)) -> MissionTask:
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission("m", origin="user")
    return ledger.create_task(mission.id, "t", acceptance=acceptance, checks=checks)


def _cli(root: Path, *args: str) -> Any:
    return runner.invoke(app, ["task", *args, "--project-root", str(root)])


def _verify(root: Path, task_id: str) -> Any:
    return _cli(root, "verify", task_id)


def _approve(root: Path, task_id: str, *extra: str) -> Any:
    return _cli(root, "approve-check", task_id, *extra)


# ── Aucun sous-processus sans approbation ────────────────────────────────────


def test_unapproved_command_runs_no_subprocess(project: Path, outside: Path) -> None:
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    res = _verify(project, task.id)
    assert res.exit_code == 3, res.output
    assert "unapproved" in res.output
    assert _runs(outside, "ran") == 0


def test_unapproved_batch_runs_nothing_even_the_approved_one(project: Path, outside: Path) -> None:
    approved = TaskCheck(cmd=_marker_cmd(outside, "a"))
    first = _task(project, approved)
    assert _approve(project, first.id).exit_code == 0
    second = _task(project, approved, TaskCheck(cmd=_marker_cmd(outside, "b")))
    res = _verify(project, second.id)
    assert res.exit_code == 3, res.output
    assert _runs(outside, "a") == 0
    assert _runs(outside, "b") == 0


def test_approved_command_runs(project: Path, outside: Path) -> None:
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    approval = _approve(project, task.id)
    assert approval.exit_code == 0, approval.output
    res = _verify(project, task.id)
    assert res.exit_code == 0, res.output
    assert _runs(outside, "ran") == 1


def test_approval_log_records_sha_actor_and_ts(project: Path, outside: Path) -> None:
    cmd = _marker_cmd(outside, "ran")
    task = _task(project, TaskCheck(cmd=cmd))
    assert _approve(project, task.id, "--actor", "guilhem").exit_code == 0
    lines = (project / "_grimoire-output/.runs/check-approvals.authority.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["command_sha256"] == __import__("hashlib").sha256(cmd.encode("utf-8")).hexdigest()
    assert entry["actor"] == "guilhem"
    assert entry["ts"]
    assert entry["attestation"] == "unattested"
    assert entry["attestation_reason"] == "no_challenge"  # terminal nu : aucun challenge émis


# ── Un octet, une attente affaiblie, `true` à la place ───────────────────────


def test_one_byte_change_invalidates_the_approval(project: Path, outside: Path) -> None:
    cmd = _marker_cmd(outside, "ran")
    first = _task(project, TaskCheck(cmd=cmd))
    assert _approve(project, first.id).exit_code == 0
    assert _verify(project, first.id).exit_code == 0
    edited = _task(project, TaskCheck(cmd=cmd + " "))  # un seul espace de plus
    res = _verify(project, edited.id)
    assert res.exit_code == 3, res.output
    assert _runs(outside, "ran") == 1


def test_replacing_the_command_by_true_invalidates_the_receipt(project: Path, outside: Path) -> None:
    first = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, first.id).exit_code == 0
    assert _verify(project, first.id).exit_code == 0
    swapped = _task(project, TaskCheck(cmd="true"))
    res = _verify(project, swapped.id)
    assert res.exit_code == 3, res.output
    receipts = (project / "_grimoire-output/.runs/check-receipts.authority.jsonl").read_text(encoding="utf-8")
    hashes = {json.loads(line)["command_sha256"] for line in receipts.splitlines()}
    assert __import__("hashlib").sha256(b"true").hexdigest() not in hashes


def test_weakened_expectation_invalidates_the_approval(project: Path, outside: Path) -> None:
    cmd = f'"{sys.executable}" -c "raise SystemExit(1)"'
    strict = _task(project, TaskCheck(cmd=cmd))  # attend 0 : la commande échoue
    assert _approve(project, strict.id).exit_code == 0
    assert _verify(project, strict.id).exit_code == 1
    weakened = _task(project, TaskCheck(cmd=cmd, expect_exit=1))  # même commande, attente retournée
    res = _verify(project, weakened.id)
    assert res.exit_code == 3, res.output


# ── Caractères cachés ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("code", "label"), [(0x202E, "U+202E"), (0x200B, "U+200B")])
def test_hidden_characters_are_refused(project: Path, outside: Path, code: int, label: str) -> None:
    cmd = _marker_cmd(outside, "ran") + chr(code)
    task = _task(project, TaskCheck(cmd=cmd))
    res = _approve(project, task.id)
    assert res.exit_code == 2, res.output
    assert label in res.output
    assert not (project / "_grimoire-output/.runs/check-approvals.authority.jsonl").exists()
    assert _verify(project, task.id).exit_code == 3
    assert _runs(outside, "ran") == 0


# ── Reçu et réutilisation ────────────────────────────────────────────────────


def test_two_identical_closings_count_one_subprocess(project: Path, outside: Path) -> None:
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, task.id).exit_code == 0
    first = _verify(project, task.id)
    second = _verify(project, task.id)
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert _runs(outside, "ran") == 1


def test_a_changed_tree_runs_again(project: Path, outside: Path) -> None:
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, task.id).exit_code == 0
    assert _verify(project, task.id).exit_code == 0
    (project / "src.py").write_text("x = 1\n", encoding="utf-8")
    assert _verify(project, task.id).exit_code == 0
    assert _runs(outside, "ran") == 2


def test_receipt_carries_command_sha_tree_fingerprint_and_exit(project: Path, outside: Path) -> None:
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, task.id).exit_code == 0
    res = runner.invoke(app, ["--output", "json", "task", "verify", task.id, "--project-root", str(project)])
    assert res.exit_code == 0, res.output
    receipt = json.loads(res.output)["receipts"][0]
    assert set(receipt) == {"command_sha256", "tree_fingerprint", "exit", "reused"}
    assert receipt["exit"] == 0
    assert len(receipt["command_sha256"]) == 64
    assert len(receipt["tree_fingerprint"]) == 64
    assert receipt["reused"] is False


def test_a_red_result_is_never_reused(project: Path, outside: Path) -> None:
    cmd = f'"{sys.executable}" "{outside / "mark.py"}" "{outside / "ran"}" && "{sys.executable}" -c "raise SystemExit(1)"'
    task = _task(project, TaskCheck(cmd=cmd))
    assert _approve(project, task.id).exit_code == 0
    assert _verify(project, task.id).exit_code == 1
    assert _verify(project, task.id).exit_code == 1
    assert _runs(outside, "ran") == 2


# ── Profil : le comportement historique hors governed ────────────────────────


def test_starter_profile_keeps_the_historic_behaviour(tmp_path: Path, outside: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    task = _task(root, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    res = _verify(root, task.id)
    assert res.exit_code == 0, res.output
    assert _runs(outside, "ran") == 1
    assert not (root / "_grimoire-output/.runs/check-receipts.authority.jsonl").exists()


# ── Acteur attesté par W2-01 ─────────────────────────────────────────────────


def _human_turn(root: Path, session_id: str) -> None:
    time.sleep(0.02)  # horloges grossières : jamais le même instant que le challenge
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt="approuvé",
            session_id=session_id,
            host="host-claude-code-cli",
        ),
    )


def test_agent_in_a_session_cannot_approve_without_a_human_turn(
    project: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _verify(project, task.id).exit_code == 3  # émet le challenge de la session
    res = _approve(project, task.id)
    assert res.exit_code == 3, res.output
    assert "awaiting_human" in res.output
    assert _verify(project, task.id).exit_code == 3
    assert _runs(outside, "ran") == 0


def test_a_human_turn_after_the_challenge_attests_the_approval(
    project: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _verify(project, task.id).exit_code == 3
    _human_turn(project, "S1")
    res = _approve(project, task.id)
    assert res.exit_code == 0, res.output
    entry = json.loads((project / "_grimoire-output/.runs/check-approvals.authority.jsonl").read_text("utf-8").splitlines()[-1])
    assert entry["attestation"] == "attested"
    assert _verify(project, task.id).exit_code == 0
    assert _runs(outside, "ran") == 1


def test_a_human_turn_of_another_session_does_not_attest(
    project: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _verify(project, task.id).exit_code == 3
    _human_turn(project, "S2")
    assert _approve(project, task.id).exit_code == 3


def test_an_agent_marker_without_a_session_needs_a_human_turn(
    project: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "1")
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, task.id).exit_code == 3


def test_unattended_execution_cannot_approve(project: Path, outside: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    task = _task(project, TaskCheck(cmd=_marker_cmd(outside, "ran")))
    assert _approve(project, task.id).exit_code == 3


def test_agents_cannot_write_the_approval_journal() -> None:
    """Le journal porte le suffixe protégé par la garde d'écriture d'autorité."""
    from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX
    from grimoire.missions.check_gate import APPROVALS_RELPATH, RECEIPTS_RELPATH

    assert APPROVALS_RELPATH.name.endswith(AUTHORITY_SUFFIX)
    assert RECEIPTS_RELPATH.name.endswith(AUTHORITY_SUFFIX)


# ── Dispatch de flow : l'acceptance du blueprint ─────────────────────────────


def _flow_blueprint(root: Path, command: str) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "approved-check",
        "name": "Approved check (W3-03)",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": [{"run": command}],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    path = root / "approved-check.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _provider(root: Path, outside: Path) -> None:
    """Un ouvrier factice qui écrit une enveloppe conforme et compte ses appels."""
    script = outside / "worker.py"
    script.write_text(
        "import json, re, sys\n"
        f"open({str(outside / 'worker-calls')!r}, 'a', encoding='utf-8').write('x\\n')\n"
        "prompt = sys.argv[1]\n"
        "path = re.search(r'Écris ta sortie dans le fichier (\\S+)', prompt).group(1)\n"
        "pins = {m.group(1): {'contract': m.group(2)} for m in re.finditer(r'^  - (\\S+) : (\\S+)$', prompt, re.MULTILINE)}\n"
        "json.dump({'pins': pins}, open(path, 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    invocation = f"{Path(sys.executable).as_posix()} {script.as_posix()} {{prompt}} --model {{model}}"
    (root / STANDARD / "llm-provider-registry.yaml").write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "cheap-writer"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["cheap-writer-model"]
    currency: "api"
    invocation: "{invocation}"
    models:
      - id: "cheap-writer-model"
        tier: "cheap"
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _flow(root: Path, outside: Path, command: str) -> Any:
    engine = FlowEngine(kernel_root=root / "runtime", flows_root=root / "flows")
    return run_with_dispatch(engine, _flow_blueprint(root, command), project_root=root)


def test_unapproved_blueprint_acceptance_runs_nothing_and_calls_no_provider(project: Path, outside: Path) -> None:
    _provider(project, outside)
    outcome = _flow(project, outside, _marker_cmd(outside, "accept"))
    assert outcome.status == "waiting_host"
    assert outcome.node_id == "n"
    assert "unapproved" in (outcome.host_reason or "")
    assert _runs(outside, "accept") == 0
    assert _runs(outside, "worker-calls") == 0


def test_approved_blueprint_acceptance_runs_and_a_modified_blueprint_asks_again(project: Path, outside: Path) -> None:
    from grimoire.flows.dispatch_executor import _task_id_for

    _provider(project, outside)
    command = _marker_cmd(outside, "accept")
    blocked = _flow(project, outside, command)
    assert blocked.status == "waiting_host"
    approval = _approve(project, _task_id_for(blocked.run_id, "n"))
    assert approval.exit_code == 0, approval.output

    done = _flow(project, outside, command)
    assert done.status == "finished", done.host_reason
    assert _runs(outside, "accept") == 1
    assert _runs(outside, "worker-calls") == 1

    edited = _flow(project, outside, _marker_cmd(outside, "accept2"))  # un agent a retouché le blueprint
    assert edited.status == "waiting_host"
    assert _runs(outside, "accept") == 1
    assert _runs(outside, "accept2") == 0
    assert _runs(outside, "worker-calls") == 1


def test_gate_runner_marks_unapproved_checks_unrunnable_without_spawning(project: Path, outside: Path) -> None:
    """Défense en profondeur : même sans pré-contrôle, le lanceur de ``run_dispatch`` ne lance rien."""
    from grimoire.missions.check_gate import CheckGate

    gate = CheckGate(project, "T-1")
    results = gate.run((_marker_cmd(outside, "ran"),), project_root=project)
    assert results[0].ok is False
    assert results[0].exit_code is None
    assert (results[0].unrunnable_reason or "").startswith("unapproved")
    assert _runs(outside, "ran") == 0
