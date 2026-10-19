"""W2-11 — la décision d'un checkpoint destructif est liée à l'empreinte du plan de retrait.

Avant : le challenge d'un checkpoint ne portait que la session et l'horodatage.
Un humain pouvait donc approuver « ce qu'il avait vu » alors que le projet
avait changé entre la présentation et la décision (un orphelin de plus, un
fichier archivé restauré) : l'``approve`` franchissait quand même le node.
Ici, le node déclare ``config.checkpoint.plan`` ; la liste projetée est
hachée à la présentation et recalculée à la décision. Un écart donne
``plan_drifted`` (avec les écarts), consomme le challenge, n'use pas le tour
humain, et la présentation suivante exige un nouveau tour.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows import attestation
from grimoire.flows.blueprint_loader import load_blueprint
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent

_GATE_OUT = {"pins": {"out": {"contract": "c1"}}}
_PROVIDER = "grimoire.tools.project_upgrade.projected_removals"


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


class _Plan:
    """Le plan projeté, que le test fait varier entre la présentation et la décision."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, items: list[str]) -> None:
        self.items = items
        self.error: Exception | None = None
        monkeypatch.setattr(_PROVIDER, self._project, raising=False)

    def _project(self, _root: Path) -> list[str]:
        if self.error is not None:
            raise self.error
        return list(self.items)


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


def _blueprint(root: Path, *, plan: Any = "upgrade-removals", destructive: bool = True) -> Path:
    config: dict[str, Any] = {"destructive": destructive}
    if plan is not None:
        config["plan"] = plan
    gate = {
        "id": "gate",
        "kind": "checkpoint",
        "ref": "",
        "config": {"checkpoint": config},
        "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
    }
    after = {"id": "after", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]}
    path = root / "bp.blueprint.json"
    path.write_text(
        json.dumps(
            {
                "blueprintVersion": 1,
                "id": "plan-drift",
                "nodes": [gate, after],
                "edges": [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _quiet() -> InteractiveNodeExecutor:
    return InteractiveNodeExecutor(stream=io.StringIO())


def _start(root: Path, **kw: Any) -> tuple[FlowEngine, str]:
    engine = _engine(root)
    wfi, _contract = engine.run(_blueprint(root, **kw), executor=_quiet())
    return engine, wfi.id


def _human_turn(root: Path) -> None:
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt="ok",
            session_id="S1",
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def _decide(engine: FlowEngine, run_id: str, decision: str = "approve", reason: str = "") -> Any:
    out: dict[str, Any] = {**_GATE_OUT, "checkpoint_decision": decision}
    if reason:
        out["checkpoint_reason"] = reason
    return engine.resume(run_id, output=out, executor=_quiet())


def _journal(root: Path, run_id: str) -> attestation.CheckpointJournal:
    return attestation.CheckpointJournal(root / "_grimoire-runtime-output" / "flows", run_id)


def _challenge(root: Path, run_id: str) -> dict[str, Any]:
    challenge = _journal(root, run_id).open_challenge("gate")
    assert challenge is not None
    return challenge


def test_plan_inchange_la_decision_attestee_passe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Plan(monkeypatch, ["orphan:a.md", "archived:b.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)

    outcome = _decide(engine, run_id)

    assert outcome.ok is True and outcome.node_id == "after"
    assert outcome.attestation["status"] == "attested"


def test_le_challenge_porte_l_empreinte_et_la_liste_projetee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Plan(monkeypatch, ["orphan:b.md", "orphan:a.md", "orphan:a.md"])
    _, run_id = _start(tmp_path)

    plan = _challenge(tmp_path, run_id)["plan"]

    assert plan["provider"] == "upgrade-removals"
    assert plan["items"] == ["orphan:a.md", "orphan:b.md"]  # trié, sans doublon
    assert plan["sha256"] == attestation.plan_fingerprint(["orphan:a.md", "orphan:b.md"])
    assert attestation.plan_fingerprint(["x", "y"]) == attestation.plan_fingerprint(["y", "x", "x"])
    assert attestation.plan_fingerprint(["x"]) != attestation.plan_fingerprint(["x", "y"])


def test_plan_modifie_apres_la_presentation_donne_plan_drifted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)  # un humain a bien décidé, après la présentation
    plan.items = ["orphan:a.md", "orphan:agent-critique.md"]  # le projet a bougé depuis

    outcome = _decide(engine, run_id)

    assert outcome.ok is False and outcome.node_id == "gate"
    fault = " ".join(outcome.faults)
    assert "plan_drifted" in fault
    assert "orphan:agent-critique.md" in fault  # l'écart est nommé
    assert outcome.attestation["reason"] == "plan_drifted"
    assert [d["decision"] for d in engine.status(run_id).checkpoint_decisions] == ["plan_drifted"]


def test_plan_reduit_apres_la_presentation_nomme_les_retraits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md", "archived:b.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    plan.items = ["orphan:a.md"]

    outcome = _decide(engine, run_id)

    assert outcome.ok is False
    fault = " ".join(outcome.faults)
    assert "plan_drifted" in fault and "archived:b.md" in fault


def test_apres_plan_drifted_il_faut_un_nouveau_tour_puis_le_nouveau_plan_passe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    plan.items = ["orphan:a.md", "orphan:b.md"]
    assert _decide(engine, run_id).ok is False  # plan_drifted : le challenge est rejoué avec le nouveau plan

    assert _challenge(tmp_path, run_id)["plan"]["items"] == ["orphan:a.md", "orphan:b.md"]
    # Le tour humain d'avant la nouvelle présentation ne vaut pas pour le nouveau plan.
    stale = _decide(engine, run_id)
    assert stale.ok is False and stale.attestation["reason"] == "no_human_turn_since_challenge"

    _human_turn(tmp_path)
    assert _decide(engine, run_id).ok is True


def test_plan_drifted_n_use_pas_le_tour_humain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    plan.items = ["orphan:z.md"]
    assert _decide(engine, run_id).ok is False

    assert _journal(tmp_path, run_id).consumed_turns() == frozenset()


def test_accept_as_is_est_lie_au_plan_comme_approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    for i in range(3):
        assert _decide(engine, run_id, "amend", f"révision {i + 1}").ok is False
    _human_turn(tmp_path)
    plan.items = []

    outcome = _decide(engine, run_id, "accept_as_is", "risque connu")

    assert outcome.ok is False and "plan_drifted" in " ".join(outcome.faults)


def test_reject_et_amend_ne_sont_pas_soumis_a_la_derive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    plan.items = ["orphan:a.md", "orphan:b.md"]

    amended = _decide(engine, run_id, "amend", "reprendre")
    rejected = _decide(engine, run_id, "reject", "non")

    assert "plan_drifted" not in " ".join(amended.faults)
    assert "plan_drifted" not in " ".join(rejected.faults)


def test_plan_non_projetable_a_la_decision_est_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path)
    _human_turn(tmp_path)
    plan.error = OSError("disque illisible")

    outcome = _decide(engine, run_id)

    assert outcome.ok is False
    fault = " ".join(outcome.faults)
    assert "plan_drifted" in fault and "disque illisible" in fault


def test_plan_non_projetable_a_la_presentation_ne_laisse_jamais_passer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    plan.error = OSError("disque illisible")
    engine, run_id = _start(tmp_path)
    assert _challenge(tmp_path, run_id)["plan"]["sha256"] == ""
    plan.error = None  # le disque revient : sans plan lié à la présentation, rien à comparer
    _human_turn(tmp_path)

    outcome = _decide(engine, run_id)

    assert outcome.ok is False and "plan_drifted" in " ".join(outcome.faults)


def test_checkpoint_sans_plan_declare_garde_son_comportement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _Plan(monkeypatch, ["orphan:a.md"])
    engine, run_id = _start(tmp_path, plan=None)
    _human_turn(tmp_path)
    plan.items = ["orphan:a.md", "orphan:b.md"]

    assert "plan" not in _challenge(tmp_path, run_id)
    assert _decide(engine, run_id).ok is True


@pytest.mark.parametrize("bad", ["inconnu", "", 3, True, ["upgrade-removals"]])
def test_plan_invalide_refuse_au_chargement(tmp_path: Path, bad: Any) -> None:
    path = _blueprint(tmp_path, plan=bad)
    with pytest.raises(GrimoireRuntimeError, match=r"config\.checkpoint\.plan"):
        load_blueprint(path, tmp_path)


def test_le_blueprint_project_upgrade_declare_le_plan_de_retrait() -> None:
    from grimoire.tools.project_upgrade import bundled_blueprint_path

    blueprint = json.loads(bundled_blueprint_path().read_text(encoding="utf-8"))
    node = next(n for n in blueprint["nodes"] if n["id"] == "destructive")
    assert node["config"]["checkpoint"]["destructive"] is True
    assert node["config"]["checkpoint"]["plan"] == "upgrade-removals"
