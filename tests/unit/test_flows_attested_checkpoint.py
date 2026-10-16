"""W2-01 — une décision de checkpoint n'avance qu'attestée par un tour humain.

Avant : ``checkpoint_decision_faults`` ne lisait que la valeur, et n'importe
quel JSON ``{"checkpoint_decision": "approve"}`` franchissait le checkpoint
destructif de ``project-upgrade``. Ici, le tour humain passe par le vrai
chemin du hook (``grimoire.task-context`` sur ``UserPromptSubmit``), la
session par l'environnement (``GRIMOIRE_SESSION_ID``), comme sous un hôte.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent

runner = CliRunner()
_GATE_OUT = {"pins": {"out": {"contract": "c1"}}}


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


def _blueprint(root: Path, *, destructive: bool, name: str = "bp") -> Path:
    gate: dict[str, Any] = {
        "id": "gate",
        "kind": "checkpoint",
        "ref": "",
        "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
    }
    if destructive:
        gate["config"] = {"checkpoint": {"destructive": True}}
    after = {"id": "after", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]}
    path = root / f"{name}.blueprint.json"
    path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": f"attested-{name}",
                "nodes": [gate, after],
                "edges": [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _start(root: Path, *, destructive: bool = True, name: str = "bp") -> tuple[FlowEngine, str]:
    engine = _engine(root)
    wfi, contract = engine.run(_blueprint(root, destructive=destructive, name=name), executor=_quiet())
    assert contract.kind == "checkpoint"
    return engine, wfi.id


def _quiet() -> InteractiveNodeExecutor:
    return InteractiveNodeExecutor(stream=io.StringIO())


def _human_turn(root: Path, session_id: str = "S1", *, host: str = "host-claude-code-cli", prompt: str = "ok") -> None:
    """Un ``UserPromptSubmit`` tel que le hook de l'hôte le reçoit, horodaté après ce qui précède."""
    time.sleep(0.02)  # horloges grossières (Windows) : jamais le même instant que le challenge
    run_decision(
        "grimoire.task-context",
        HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=root, prompt=prompt, session_id=session_id, host=host),
    )
    time.sleep(0.02)


def load_turns(root: Path) -> Any:
    from grimoire.hosts.decisions.human_turns import load_turns as _load

    return _load(root)


def _approve(engine: FlowEngine, run_id: str, *, directive_id: str | None = None, **extra: Any) -> Any:
    return engine.resume(
        run_id,
        output={**_GATE_OUT, "checkpoint_decision": "approve", **extra},
        executor=_quiet(),
        directive_id=directive_id,
    )


def test_approve_sans_tour_posterieur_au_challenge_est_refuse(tmp_path: Path) -> None:
    _human_turn(tmp_path)  # antérieur au challenge : ne compte pas
    engine, run_id = _start(tmp_path)

    outcome = _approve(engine, run_id)

    assert outcome.ok is False
    assert outcome.attestation["status"] == "unattested"
    assert outcome.attestation["reason"] == "no_human_turn_since_challenge"
    fault = " ".join(outcome.faults)
    assert "awaiting_human" in fault and "no_human_turn_since_challenge" in fault
    assert "Limite" in fault  # la limite de l'attestation est écrite dans le refus
    status = engine.status(run_id)
    assert status.current_node == "gate" and status.status == "blocked"
    assert "awaiting_human" in status.last_refusal["reason"]


def test_tour_posterieur_de_la_meme_session_est_attested(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)

    outcome = _approve(engine, run_id)

    assert outcome.ok is True and outcome.node_id == "after"
    assert outcome.attestation["status"] == "attested"
    assert outcome.warnings == ()
    turns = load_turns(tmp_path)
    assert turns is not None and all("ok" not in t.prompt_sha256 for t in turns)  # jamais le texte


def test_tour_d_une_autre_session_n_ouvre_pas_le_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path, session_id="S2")  # sous-session `claude -p "approve"` : son propre session_id

    refused = _approve(engine, run_id)
    assert refused.ok is False
    assert refused.attestation["reason"] == "no_human_turn_since_challenge"

    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S2")  # la sous-session soumet elle-même
    from_child = _approve(engine, run_id)
    assert from_child.ok is False
    assert from_child.attestation["reason"] == "other_session"


def test_origine_hote_et_execution_sans_humain_n_ouvrent_pas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path, host="")  # grimoire-hook sans --host : origin=host
    assert [t.origin for t in load_turns(tmp_path) or []] == ["host"]
    assert _approve(engine, run_id).ok is False

    _human_turn(tmp_path)  # vrai tour humain…
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")  # …mais décision soumise en mode sans humain
    unattended = _approve(engine, run_id)
    assert unattended.ok is False
    assert unattended.attestation["reason"] == "unattended"


def test_tour_enregistre_sous_unattended_est_d_origine_hote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path)
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    _human_turn(tmp_path)
    monkeypatch.delenv("GRIMOIRE_UNATTENDED")
    assert _approve(engine, run_id).ok is False


def test_sans_hook_de_prompt_l_autorite_est_improuvable(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    outcome = _approve(engine, run_id)
    assert outcome.ok is False
    assert outcome.attestation["reason"] == "human_authority_unprovable"


def test_cli_nue_sans_session_est_unattested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_SESSION_ID")
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    outcome = _approve(engine, run_id)
    assert outcome.ok is False
    assert outcome.attestation["reason"] == "no_session"


def test_blocked_awaiting_human_refuse_la_reprise_sans_tour_atteste(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    assert _approve(engine, run_id).ok is False  # parked : blocked/awaiting_human

    again = _approve(engine, run_id)
    assert again.ok is False and "awaiting_human" in " ".join(again.faults)
    assert engine.status(run_id).status == "blocked"

    _human_turn(tmp_path)
    released = _approve(engine, run_id)
    assert released.ok is True and released.attestation["status"] == "attested"


def test_un_tour_ne_sert_qu_une_decision(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    rejected = engine.resume(
        run_id, output={**_GATE_OUT, "checkpoint_decision": "reject", "checkpoint_reason": "pas prêt"}, executor=_quiet()
    )
    assert rejected.ok is False and rejected.attestation["status"] == "attested"

    # Le refus a consommé le challenge : le tour qui l'a précédé n'ouvre pas la présentation suivante.
    assert _approve(engine, run_id).ok is False
    _human_turn(tmp_path)
    assert _approve(engine, run_id).ok is True


def test_un_tour_ne_sert_qu_une_decision_meme_entre_deux_runs(tmp_path: Path) -> None:
    """W2-01-S2 : deux runs destructifs de la même session, un seul tour humain, une seule décision."""
    engine, run_a = _start(tmp_path, name="a")
    _, run_b = _start(tmp_path, name="b")
    _human_turn(tmp_path)

    first = _approve(engine, run_a)
    second = _approve(engine, run_b)

    assert first.ok is True and first.attestation["status"] == "attested"
    assert second.ok is False
    assert second.attestation["status"] == "unattested"
    assert second.attestation["reason"] == "no_human_turn_since_challenge"
    assert "awaiting_human" in " ".join(second.faults)


def test_deux_tours_servent_deux_decisions_de_deux_runs(tmp_path: Path) -> None:
    engine, run_a = _start(tmp_path, name="a")
    _, run_b = _start(tmp_path, name="b")
    _human_turn(tmp_path, prompt="ok a")
    first = _approve(engine, run_a)
    _human_turn(tmp_path, prompt="ok b")
    second = _approve(engine, run_b)

    assert first.ok is True and second.ok is True
    assert first.attestation["turn_ts"] != second.attestation["turn_ts"]


def test_le_tour_consomme_survit_a_un_autre_moteur(tmp_path: Path) -> None:
    engine, run_a = _start(tmp_path, name="a")
    _, run_b = _start(tmp_path, name="b")
    _human_turn(tmp_path)
    assert _approve(engine, run_a).ok is True

    # Un autre processus (autre instance de moteur) lit le même registre.
    assert _approve(_engine(tmp_path), run_b).ok is False


def test_un_refus_non_atteste_ne_consomme_aucun_tour(tmp_path: Path) -> None:
    engine, run_a = _start(tmp_path, name="a")
    _, run_b = _start(tmp_path, name="b")
    assert _approve(engine, run_a).ok is False  # aucun tour : refus, rien de consommé
    _human_turn(tmp_path)
    assert _approve(_engine(tmp_path), run_b).ok is True


def test_le_registre_des_tours_consommes_est_un_journal_d_autorite(tmp_path: Path) -> None:
    from grimoire.flows.attestation import CONSUMED_TURNS_FILENAME
    from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX

    assert CONSUMED_TURNS_FILENAME.endswith(AUTHORITY_SUFFIX)  # la garde d'écriture le couvre
    engine, run_a = _start(tmp_path, name="a")
    _human_turn(tmp_path)
    assert _approve(engine, run_a).ok is True
    registry = tmp_path / "_grimoire-runtime-output" / "flows" / "authority" / CONSUMED_TURNS_FILENAME
    entry = json.loads(registry.read_text(encoding="utf-8").splitlines()[0])
    assert entry["session_id"] == "S1" and entry["turn_sha256"] and entry["turn_ts"]
    # Aucun identifiant de run ne peut produire ce nom.
    from grimoire.flows.attestation import CheckpointJournal

    for run_id in ("@consumed-turns", "consumed-turns", "x/../@consumed-turns"):
        assert CheckpointJournal(tmp_path, run_id).path.name != CONSUMED_TURNS_FILENAME


def test_checkpoint_non_destructif_accepte_unattested_avec_avertissement(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path, destructive=False)
    outcome = _approve(engine, run_id)
    assert outcome.ok is True
    assert outcome.attestation["status"] == "unattested"
    assert outcome.warnings and "unattested" in outcome.warnings[0]
    assert engine.status(run_id).checkpoint_decisions[0]["attestation"] == "unattested"


def test_motif_d_amend_relu_par_status_json(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    outcome = engine.resume(
        run_id,
        output={**_GATE_OUT, "checkpoint_decision": "amend", "checkpoint_reason": "garder les overrides locaux"},
        executor=_quiet(),
    )
    assert outcome.ok is True

    result = runner.invoke(app, ["--output", "json", "flow", "status", run_id, "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    decisions = json.loads(result.stdout)["checkpoint_decisions"]
    assert decisions == [
        {
            "node_id": "gate",
            "decision": "amend",
            "reason": "garder les overrides locaux",
            "attestation": "attested",
            "attestation_reason": "",
            "ts": decisions[0]["ts"],
        }
    ]


def test_destructive_doit_etre_un_booleen(tmp_path: Path) -> None:
    path = _blueprint(tmp_path, destructive=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["nodes"][0]["config"]["checkpoint"]["destructive"] = "yes"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GrimoireRuntimeError, match="destructive doit être un booléen"):
        load_blueprint(path, tmp_path)


def test_le_checkpoint_de_project_upgrade_est_declare_destructif() -> None:
    from grimoire.flows.attestation import destructive_checkpoints
    from grimoire.tools.project_upgrade import bundled_blueprint_path

    blueprint = json.loads(bundled_blueprint_path().read_text(encoding="utf-8"))
    assert destructive_checkpoints(blueprint) == {"destructive"}


def test_directive_perimee_sur_checkpoint_destructif_est_refusee_sans_consommer_le_tour(tmp_path: Path) -> None:
    """W2-01 + W2-03 ensemble : le directive_id gouverne AVANT l'attestation.

    Le refus ``awaiting_human`` est un ``blocked`` (quatre kinds) à tentative
    suivante ; une décision visant la directive d'avant est rejetée sans rien
    écrire, donc sans consommer le tour humain, qui sert ensuite la bonne.
    """
    engine, run_id = _start(tmp_path)
    first = engine.directive(run_id)
    assert first.kind == "checkpoint" and first.node_id == "gate"

    assert _approve(engine, run_id, directive_id=first.directive_id).ok is False  # parked
    parked = engine.directive(run_id)
    assert parked.kind == "blocked" and parked.reason == "awaiting_human"
    assert parked.node_id == "gate" and parked.attempt == 2
    assert parked.directive_id != first.directive_id

    _human_turn(tmp_path)
    decisions_before = engine.status(run_id).checkpoint_decisions
    with pytest.raises(GrimoireRuntimeError, match="directive_id"):
        _approve(engine, run_id, directive_id=first.directive_id)  # vise la directive d'avant le refus
    after_stale = engine.status(run_id)
    assert after_stale.status == "blocked" and after_stale.current_node == "gate"
    assert after_stale.checkpoint_decisions == decisions_before

    released = _approve(engine, run_id, directive_id=parked.directive_id)
    assert released.ok is True and released.attestation["status"] == "attested"  # le tour n'avait pas été brûlé
    assert released.directive is not None and released.directive.node_id == "after"
