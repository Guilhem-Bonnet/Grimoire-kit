"""W5-06 — octroi d'autonomie à périmètre, coût et durée, révocable.

Avant : un checkpoint destructif n'avançait que sur une décision ``attested``
(W2-01). Sous ``GRIMOIRE_UNATTENDED=1`` (CI, banc) ou hors session, il refusait
toujours : aucune voie tracée pour faire tourner un profil governed sans humain.
Ici l'octroi est lui-même attesté (vrai chemin du hook ``UserPromptSubmit``),
puis décide les checkpoints de son périmètre avec ``granted:<grant_id>`` tant
qu'il n'est ni expiré, ni dépassé, ni frappé d'un gate rouge.
"""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.events import HookEvent

runner = CliRunner()
_OUT = {"pins": {"out": {"contract": "c1"}}}
_FLOWS = Path("_grimoire-runtime-output") / "flows"


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime", flows_root=root / _FLOWS, project_root=root
    )


def _quiet() -> InteractiveNodeExecutor:
    return InteractiveNodeExecutor(stream=io.StringIO())


def _blueprint(root: Path, name: str, *, with_work: bool = False) -> Path:
    gate: dict[str, Any] = {
        "id": "gate",
        "kind": "checkpoint",
        "ref": "",
        "config": {"checkpoint": {"destructive": True}},
        "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
    }
    after = {
        "id": "after",
        "kind": "pattern",
        "ref": "ORC-01",
        "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
    }
    nodes: list[dict[str, Any]] = [gate, after]
    edges = [{"from": "gate.out", "to": "after.in", "contract": "c1"}]
    if with_work:
        gate["pins"] = [
            {"id": "in", "direction": "in", "contract": "c1"},
            {"id": "out", "direction": "out", "contract": "c1"},
        ]
        work = {
            "id": "work",
            "kind": "pattern",
            "ref": "ORC-01",
            "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
        }
        nodes = [work, *nodes]
        edges = [{"from": "work.out", "to": "gate.in", "contract": "c1"}, *edges]
    path = root / f"{name}.blueprint.json"
    path.write_text(
        json.dumps({"blueprintVersion": 1, "id": f"grant-{name}", "nodes": nodes, "edges": edges}), encoding="utf-8"
    )
    return path


def _start(root: Path, name: str = "bp", *, with_work: bool = False) -> tuple[FlowEngine, str]:
    engine = _engine(root)
    wfi, _contract = engine.run(_blueprint(root, name, with_work=with_work), executor=_quiet())
    return engine, wfi.id


def _human_turn(root: Path, session_id: str = "S1") -> None:
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt="ok",
            session_id=session_id,
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def _journal(root: Path) -> Any:
    from grimoire.flows.autonomy import GrantJournal

    return GrantJournal(root / _FLOWS, root)


def _grant(root: Path, *scope: str, cost: float = 1.0, until: str = "+1h") -> str:
    """Un octroi par le vrai chemin : demande (challenge), tour humain de la session, demande relancée."""
    from grimoire.flows.autonomy import GrantRequest

    request = GrantRequest.build(scope, cost, until)
    pending, refused = _journal(root).request(request)
    assert pending is None and refused.reason in ("no_human_turn_since_challenge", "human_authority_unprovable")
    _human_turn(root)
    grant, attestation = _journal(root).request(request)
    assert grant is not None and attestation.status == "attested"
    return str(grant["grant_id"])


def _unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    """La CI ou le banc : aucune session d'hôte, exécution déclarée sans humain."""
    monkeypatch.delenv("GRIMOIRE_SESSION_ID", raising=False)
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")


def _approve(engine: FlowEngine, run_id: str) -> Any:
    return engine.resume(run_id, output={**_OUT, "checkpoint_decision": "approve"}, executor=_quiet())


def _dispatched(root: Path, run_id: str, node: str, *, cost: float | None, verdict: str = "green") -> None:
    from grimoire.missions.ledger import MissionLedger

    time.sleep(0.02)
    MissionLedger(root / "_grimoire-runtime-output" / "ledger").append_event(
        "task.dispatched", f"FLOW-{run_id}-{node}", "task", "test", {"cost_usd": cost, "verdict": verdict}
    )


def _events(root: Path) -> list[dict[str, Any]]:
    return list(_journal(root).events())


# ── Le cœur : sous octroi valide, le checkpoint destructif est décidé granted:<id> ──────────────


def test_sous_octroi_valide_le_checkpoint_destructif_est_decide_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, run_id = _start(tmp_path)
    grant_id = _grant(tmp_path, f"run:{run_id}")
    _unattended(monkeypatch)

    outcome = _approve(engine, run_id)

    assert outcome.ok is True and outcome.node_id == "after"
    assert outcome.attestation["status"] == f"granted:{grant_id}"
    assert outcome.attestation["status"] != "attested"
    assert outcome.warnings == ()
    decision = engine.status(run_id).checkpoint_decisions[0]
    assert decision["attestation"] == f"granted:{grant_id}"


def test_sans_octroi_le_meme_checkpoint_reste_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path)
    _unattended(monkeypatch)
    refused = _approve(engine, run_id)
    assert refused.ok is False and "awaiting_human" in " ".join(refused.faults)


def test_l_octroi_est_une_decision_attestee_qui_consomme_son_tour(tmp_path: Path) -> None:
    _, run_id = _start(tmp_path)
    _grant(tmp_path, f"run:{run_id}")
    grant = next(e for e in _events(tmp_path) if e["type"] == "grant")
    assert grant["attestation"] == "attested" and grant["session_id"] == "S1" and grant["turn_ts"]
    # Le tour qui a attesté l'octroi n'atteste plus rien d'autre : l'octroi est l'intervention humaine.
    other_engine, other_run = _start(tmp_path, "other")
    refused = _approve(other_engine, other_run)
    assert refused.ok is False and refused.attestation["reason"] == "no_human_turn_since_challenge"


def test_un_octroi_ne_s_inscrit_pas_sans_tour_humain_ni_sous_unattended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.flows.autonomy import GrantRequest

    request = GrantRequest.build(["blueprint:grant-bp"], 1.0, "+1h")
    assert _journal(tmp_path).request(request)[0] is None  # challenge émis
    assert _journal(tmp_path).request(request)[0] is None  # toujours aucun tour
    _human_turn(tmp_path, session_id="S2")  # sous-session : autre session_id
    assert _journal(tmp_path).request(request)[0] is None
    _human_turn(tmp_path)
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    grant, attestation = _journal(tmp_path).request(request)
    assert grant is None and attestation.reason == "unattended"
    challenges = [e for e in _events(tmp_path) if e["type"] == "grant_challenge"]
    assert len(challenges) == 1  # la même demande relancée retrouve son challenge


@pytest.mark.parametrize(
    ("scope", "cost", "until"),
    [
        ([], 1.0, "+1h"),
        (["*"], 1.0, "+1h"),
        (["run:a b"], 1.0, "+1h"),
        (["run:r1"], 0.0, "+1h"),
        (["run:r1"], float("nan"), "+1h"),
        (["run:r1"], 1.0, "demain"),
        (["run:r1"], 1.0, "2001-01-01T00:00:00+00:00"),
        (["run:r1"], 1.0, "2999-01-01T00:00:00"),  # sans fuseau
    ],
)
def test_une_demande_sans_borne_est_refusee(scope: list[str], cost: float, until: str) -> None:
    from grimoire.flows.autonomy import GrantRequest

    with pytest.raises(GrimoireRuntimeError):
        GrantRequest.build(scope, cost, until)


# ── Périmètre ─────────────────────────────────────────────────────────────────────────────────


def test_hors_perimetre_l_octroi_ne_decide_rien(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(tmp_path, "blueprint:grant-other", "run:RUN-ailleurs")
    engine, run_id = _start(tmp_path)
    _unattended(monkeypatch)
    refused = _approve(engine, run_id)
    assert refused.ok is False and refused.attestation["status"] == "unattested"


def test_un_perimetre_de_blueprint_restreint_a_un_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grant_id = _grant(tmp_path, "blueprint:grant-bp/gate")
    narrow = _grant(tmp_path, "blueprint:grant-two/autre-node")
    engine, run_id = _start(tmp_path)
    engine_two, run_two = _start(tmp_path, "two")
    _unattended(monkeypatch)
    assert _approve(engine, run_id).attestation["status"] == f"granted:{grant_id}"
    refused = _approve(engine_two, run_two)
    assert refused.ok is False and narrow not in refused.attestation["status"]


def _lineage_flows(root: Path) -> None:
    """PARENT (blueprint X : build, gate) et son sous-flow CHILD (blueprint Y : gate) — des nodes homonymes."""
    flows = root / _FLOWS
    flows.mkdir(parents=True, exist_ok=True)
    (flows / "PARENT.json").write_text(
        json.dumps({"run_id": "PARENT", "blueprint_id": "X", "order": ["build", "gate"]}), encoding="utf-8"
    )
    (flows / "CHILD.json").write_text(
        json.dumps({"run_id": "CHILD", "blueprint_id": "Y", "order": ["gate"], "parent_run_id": "PARENT"}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("scope", ["blueprint:X/gate", "run:PARENT/gate"])
def test_la_restriction_de_node_ne_deborde_pas_sur_un_sous_flow(tmp_path: Path, scope: str) -> None:
    _lineage_flows(tmp_path)
    grant_id = _grant(tmp_path, scope)
    journal = _journal(tmp_path)

    covered = journal.for_checkpoint("PARENT", "gate")
    assert covered is not None and covered.grant_id == grant_id and covered.active
    assert journal.for_checkpoint("PARENT", "build") is None  # un autre node du run désigné
    assert journal.for_checkpoint("CHILD", "gate") is None  # le node homonyme du sous-flow n'est pas désigné


def test_sans_node_le_perimetre_couvre_toute_la_lignee(tmp_path: Path) -> None:
    _lineage_flows(tmp_path)
    grant_id = _grant(tmp_path, "blueprint:X")
    journal = _journal(tmp_path)
    for run_id, node in (("PARENT", "gate"), ("PARENT", "build"), ("CHILD", "gate")):
        state = journal.for_checkpoint(run_id, node)
        assert state is not None and state.grant_id == grant_id, (run_id, node)


def test_le_cout_d_un_octroi_a_node_couvre_toujours_les_sous_flows(tmp_path: Path) -> None:
    """La mesure de coût ignore le node (borne plus large, donc plus prudente) : la décision, elle, le respecte."""
    from grimoire.flows.autonomy import GrantState, Selector, _Runs

    _lineage_flows(tmp_path)
    runs = _Runs(tmp_path / _FLOWS).covered((Selector.parse("blueprint:X/gate"),))
    assert sorted(runs) == ["CHILD", "PARENT"]
    assert GrantState({"grant_id": "G", "scope": ["blueprint:X/gate"]}).scope == (Selector("blueprint", "X", "gate"),)


# ── Révocations : chacune est un événement, et la session repasse en décision humaine requise ──


def test_un_octroi_expire_repasse_en_decision_humaine_requise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.flows.autonomy as autonomy

    engine, run_id = _start(tmp_path)
    grant_id = _grant(tmp_path, f"run:{run_id}", until="+5m")
    later = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setattr(autonomy, "_now", lambda: later)
    _unattended(monkeypatch)

    refused = _approve(engine, run_id)

    assert refused.ok is False
    fault = " ".join(refused.faults)
    assert "awaiting_human" in fault and grant_id in fault and "expired" in fault
    _approve(engine, run_id)  # constatée une seule fois
    revokes = [e for e in _events(tmp_path) if e["type"] == "revoke"]
    assert [(e["grant_id"], e["reason"]) for e in revokes] == [(grant_id, "expired")]


def test_un_octroi_depasse_est_revoque(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path, with_work=True)
    grant_id = _grant(tmp_path, f"run:{run_id}", cost=1.0)
    _dispatched(tmp_path, run_id, "work", cost=0.6)
    _dispatched(tmp_path, run_id, "work", cost=0.5)
    assert engine.resume(run_id, output=_OUT, executor=_quiet()).node_id == "gate"
    _unattended(monkeypatch)

    refused = _approve(engine, run_id)

    assert refused.ok is False and "cost_reached" in " ".join(refused.faults)
    (revoke,) = [e for e in _events(tmp_path) if e["type"] == "revoke"]
    assert revoke["grant_id"] == grant_id and revoke["reason"] == "cost_reached"


def test_un_cout_inconnu_revoque(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path, with_work=True)
    grant_id = _grant(tmp_path, f"run:{run_id}", cost=100.0)
    _dispatched(tmp_path, run_id, "work", cost=0.01)
    _dispatched(tmp_path, run_id, "work", cost=None)  # fournisseur muet : jamais un faux zéro
    engine.resume(run_id, output=_OUT, executor=_quiet())
    _unattended(monkeypatch)

    refused = _approve(engine, run_id)

    assert refused.ok is False and "cost_unknown" in " ".join(refused.faults)
    state = next(s for s in _journal(tmp_path).states() if s.grant_id == grant_id)
    assert state.revoked == "cost_unknown" and state.spent.status == "lower_bound"


def test_le_cout_d_avant_l_octroi_et_d_un_autre_run_ne_compte_pas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, run_id = _start(tmp_path, with_work=True)
    _, other = _start(tmp_path, "other", with_work=True)
    _dispatched(tmp_path, run_id, "work", cost=50.0)  # avant l'octroi
    grant_id = _grant(tmp_path, f"run:{run_id}", cost=1.0)
    _dispatched(tmp_path, other, "work", cost=None, verdict="red")  # hors périmètre
    _dispatched(tmp_path, run_id, "work", cost=0.2)
    engine.resume(run_id, output=_OUT, executor=_quiet())
    _unattended(monkeypatch)

    assert _approve(engine, run_id).attestation["status"] == f"granted:{grant_id}"
    (state,) = _journal(tmp_path).states()
    assert state.active and state.spent.to_dict() == {"usd": pytest.approx(0.2), "status": "exact", "unpriced_calls": 0}


def test_un_gate_rouge_de_dispatch_revoque(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, run_id = _start(tmp_path, with_work=True)
    grant_id = _grant(tmp_path, f"run:{run_id}", cost=100.0)
    _dispatched(tmp_path, run_id, "work", cost=0.01, verdict="red")
    _dispatched(tmp_path, run_id, "work", cost=0.01, verdict="green")  # l'escalade verte n'efface pas le rouge
    engine.resume(run_id, output=_OUT, executor=_quiet())
    _unattended(monkeypatch)

    refused = _approve(engine, run_id)

    assert refused.ok is False and "gate_red" in " ".join(refused.faults)
    (revoke,) = [e for e in _events(tmp_path) if e["type"] == "revoke"]
    assert revoke["grant_id"] == grant_id and "work" in revoke["detail"]


def test_une_sortie_refusee_par_son_contrat_revoque_au_premier_gate_rouge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, run_id = _start(tmp_path, with_work=True)
    grant_id = _grant(tmp_path, "blueprint:grant-bp/gate")
    bad = engine.resume(run_id, output={"pins": {}}, executor=_quiet())  # pin absente : gate rouge sur `work`
    assert bad.ok is False
    (revoke,) = [e for e in _events(tmp_path) if e["type"] == "revoke"]
    assert revoke["grant_id"] == grant_id and revoke["reason"] == "gate_red"
    assert engine.resume(run_id, output=_OUT, executor=_quiet()).node_id == "gate"
    _unattended(monkeypatch)

    refused = _approve(engine, run_id)

    assert refused.ok is False and grant_id in " ".join(refused.faults)


def test_un_rejet_ou_un_amend_n_est_pas_un_gate_rouge(tmp_path: Path) -> None:
    engine, run_id = _start(tmp_path)
    _grant(tmp_path, f"run:{run_id}")
    engine.resume(run_id, output={**_OUT, "checkpoint_decision": "amend", "checkpoint_reason": "x"}, executor=_quiet())
    engine.resume(run_id, output={**_OUT, "checkpoint_decision": "reject", "checkpoint_reason": "y"}, executor=_quiet())
    assert [e for e in _events(tmp_path) if e["type"] == "revoke"] == []


# ── CLI et protection du journal ──────────────────────────────────────────────────────────────


def test_cli_grant_status_revoke(tmp_path: Path) -> None:
    args = ["autonomy", "grant", "--scope", "blueprint:grant-bp", "--max-cost-usd", "2", "--until", "+2h"]
    root = ["--project-root", str(tmp_path)]

    first = runner.invoke(app, ["-o", "json", *args, *root])
    assert first.exit_code == 3, first.output
    assert json.loads(first.stdout)["granted"] is False
    _human_turn(tmp_path)
    second = runner.invoke(app, ["-o", "json", *args, *root])
    assert second.exit_code == 0, second.output
    grant_id = json.loads(second.stdout)["grant"]["grant_id"]

    status = json.loads(runner.invoke(app, ["-o", "json", "autonomy", "status", *root]).stdout)
    assert status["grants"][0]["grant_id"] == grant_id and status["grants"][0]["state"] == "active"
    assert "hôte" in status["cost_scope"]  # la limite du coût mesuré est écrite

    revoked = runner.invoke(app, ["autonomy", "revoke", grant_id, "--reason", "fin du banc", *root])
    assert revoked.exit_code == 0, revoked.output
    again = runner.invoke(app, ["autonomy", "revoke", grant_id, *root])
    assert again.exit_code == 1
    events = json.loads(runner.invoke(app, ["-o", "json", "autonomy", "status", *root]).stdout)["events"]
    assert [e["type"] for e in events] == ["grant_challenge", "grant", "revoke"]
    assert events[-1]["reason"] == "manual" and events[-1]["detail"] == "fin du banc"


def test_le_journal_des_octrois_est_protege_en_ecriture(tmp_path: Path) -> None:
    from grimoire.flows.autonomy import GRANTS_FILENAME

    path = f"_grimoire-runtime-output/flows/authority/{GRANTS_FILENAME}"
    for tool, tool_input in (
        ("Write", {"file_path": path, "content": '{"type": "grant"}\n'}),
        ("Bash", {"command": f"echo '{{}}' >> {path}"}),
    ):
        decision = run_decision(
            "grimoire.tool-policy",
            HookInput(
                event=HookEvent.PRE_TOOL_USE,
                project_root=tmp_path,
                tool_name=tool,
                tool_input=tool_input,
                session_id="S1",
            ),
        )
        assert decision.outcome is Outcome.DENY, decision


# ── Challenge d'octroi : lié à la session qui demande ─────────────────────────────────────────


def _challenges(root: Path) -> list[dict[str, Any]]:
    return [e for e in _events(root) if e["type"] == "grant_challenge"]


@pytest.mark.parametrize("first_session", [None, "S0"])
def test_un_challenge_d_une_autre_session_ou_sans_session_ne_bloque_pas_la_session_courante(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_session: str | None
) -> None:
    from grimoire.flows.autonomy import GrantRequest

    request = GrantRequest.build(["blueprint:bp"], 1.0, "+1h")
    if first_session is None:
        monkeypatch.delenv("GRIMOIRE_SESSION_ID", raising=False)  # un terminal sans session
    else:
        monkeypatch.setenv("GRIMOIRE_SESSION_ID", first_session)
    stale, refused = _journal(tmp_path).request(request)
    assert stale is None and refused.status == "unattested"

    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    pending, _ = _journal(tmp_path).request(request)  # la session S1 demande : son propre challenge
    assert pending is None
    _human_turn(tmp_path)  # le tour humain de S1
    grant, attestation = _journal(tmp_path).request(request)  # la même commande, relancée
    assert grant is not None and attestation.status == "attested" and grant["session_id"] == "S1"
    challenges = _challenges(tmp_path)
    assert [c["session_id"] for c in challenges] == [first_session or "", "S1"]


def test_la_meme_session_retrouve_son_challenge_ouvert(tmp_path: Path) -> None:
    from grimoire.flows.autonomy import GrantRequest

    request = GrantRequest.build(["blueprint:bp"], 1.0, "+1h")
    _journal(tmp_path).request(request)
    _journal(tmp_path).request(request)
    assert len(_challenges(tmp_path)) == 1  # une relance de S1 ne multiplie pas les challenges
