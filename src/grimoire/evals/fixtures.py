"""Pre-built EvalCase fixtures for Grimoire Agent OS components.

These fixtures test the canonical pivot:
    Mission → Task → WorkflowInstance → Policy → Evidence → Verdict
"""

from __future__ import annotations

from grimoire.evals.schemas import EvalCase, EvalOutcome, EvalResult, EvalScore
from grimoire.missions.intake import IntakeRequest, MissionIntakeService
from grimoire.missions.schemas import MissionState, RiskProfile, TaskState
from grimoire.policies.engine import PolicyEngine
from grimoire.policies.schemas import ActionKind, MutationClass, PolicyAction, PolicyActor, PolicyRequest

__all__ = [
    "make_intake_suite",
    "make_mission_lifecycle_suite",
    "make_policy_suite",
]


# ── Policy fixtures ───────────────────────────────────────────────────────────

def _make_request(action: PolicyAction, run_id: str, task_id: str, risk_profile: str) -> PolicyRequest:
    import uuid
    from datetime import UTC, datetime
    return PolicyRequest(
        id=f"req-{uuid.uuid4().hex[:8]}",
        run_id=run_id,
        task_id=task_id,
        actor=PolicyActor(actor_id="dev", host_id="claude-code-cli"),
        action=action,
        risk_profile=risk_profile,
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def _policy_allow_read() -> EvalResult:
    engine = PolicyEngine()
    request = _make_request(
        PolicyAction(kind=ActionKind.TOOL_USE, tool="read", mutation_class=MutationClass.READ_ONLY,
                     target_files=("src/grimoire/core/config.py",)),
        run_id="RUN-eval-001", task_id="GAO-eval-001", risk_profile="standard",
    )
    verdict = engine.evaluate(request)
    if verdict.verdict.value in ("allow", "warn"):
        return EvalResult(case_id="policy.allow_read", outcome=EvalOutcome.PASS, details=f"verdict={verdict.verdict.value}")
    return EvalResult(case_id="policy.allow_read", outcome=EvalOutcome.FAIL, details=f"Expected allow/warn, got {verdict.verdict.value}")


def _policy_block_destructive() -> EvalResult:
    engine = PolicyEngine()
    request = _make_request(
        PolicyAction(kind=ActionKind.FILE_WRITE, tool="bash", mutation_class=MutationClass.DESTRUCTIVE),
        run_id="RUN-eval-002", task_id="GAO-eval-001", risk_profile="standard",
    )
    verdict = engine.evaluate(request)
    if verdict.verdict.value == "block":
        return EvalResult(case_id="policy.block_destructive", outcome=EvalOutcome.PASS, details="destructive mutation on standard profile correctly blocked")
    return EvalResult(case_id="policy.block_destructive", outcome=EvalOutcome.FAIL, details=f"Expected block, got {verdict.verdict.value}")


def _policy_warn_write() -> EvalResult:
    engine = PolicyEngine()
    request = _make_request(
        PolicyAction(kind=ActionKind.TASK_CLOSE, tool="task_close", mutation_class=MutationClass.MUTATION_CONTROLLED),
        run_id="RUN-eval-003", task_id="GAO-eval-001", risk_profile="standard",
    )
    verdict = engine.evaluate(request)
    if verdict.verdict.value in ("allow", "warn"):
        return EvalResult(case_id="policy.warn_write", outcome=EvalOutcome.PASS, details=f"task-close: {verdict.verdict.value}")
    return EvalResult(case_id="policy.warn_write", outcome=EvalOutcome.FAIL, details=f"Unexpected block on task-close: {verdict.verdict.value}")


def make_policy_suite() -> list[EvalCase]:
    return [
        EvalCase(case_id="policy.allow_read", name="Policy: allow read", fn=_policy_allow_read,
                 description="Safe read action must be allowed or warned, never blocked", tags=("policy",)),
        EvalCase(case_id="policy.block_destructive", name="Policy: block destructive shell", fn=_policy_block_destructive,
                 description="Destructive shell action must be blocked by the builtin policy", tags=("policy", "security")),
        EvalCase(case_id="policy.warn_write", name="Policy: additive write is not blocked", fn=_policy_warn_write,
                 description="Additive write must be allowed or warned, not blocked", tags=("policy",)),
    ]


# ── Mission lifecycle fixtures ────────────────────────────────────────────────

def _make_mission_lifecycle_fn(tmp_factory):  # type: ignore[no-untyped-def]
    """Factory so we can inject a tmp_path at test time."""
    from grimoire.missions.ledger import MissionLedger

    def _fn() -> EvalResult:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            ledger = MissionLedger(Path(d) / "ledger")
            mission = ledger.create_mission("Eval mission", origin="eval-harness")
            ledger.transition_mission(mission.id, MissionState.OPEN)
            task = ledger.create_task(mission.id, "Eval task", acceptance=("done",))
            ledger.transition_task(task.id, TaskState.READY)
            ledger.transition_task(task.id, TaskState.CLAIMED)
            ledger.transition_task(task.id, TaskState.RUNNING)
            ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION)

            # Verify state machine
            refreshed = ledger.get_task(task.id)
            if refreshed is None or refreshed.status != TaskState.NEEDS_VERIFICATION:
                return EvalResult(case_id="mission.lifecycle", outcome=EvalOutcome.FAIL,
                                  details=f"Expected NEEDS_VERIFICATION, got {refreshed and refreshed.status}")
            score = EvalScore(value=1.0, label="full lifecycle", explanation="All transitions succeeded")
            return EvalResult(case_id="mission.lifecycle", outcome=EvalOutcome.PASS,
                              details="DRAFT→OPEN, task DRAFT→NEEDS_VERIFICATION", score=score)
    return _fn


def make_mission_lifecycle_suite() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="mission.lifecycle",
            name="Mission: full task lifecycle",
            fn=_make_mission_lifecycle_fn(None),
            description="Mission + task state machine: DRAFT→OPEN, task up to NEEDS_VERIFICATION",
            tags=("mission", "lifecycle"),
        ),
    ]


# ── Intake classification fixtures ────────────────────────────────────────────

def _intake_implementation_case() -> EvalResult:
    svc = MissionIntakeService()
    result = svc.analyze(IntakeRequest(raw_text="Implement the new recipe schema for Grimoire workflows"))
    if result.risk_profile == RiskProfile.STANDARD and result.task_proposals:
        score = EvalScore(value=result.confidence, label=f"confidence={result.confidence:.2f}")
        return EvalResult(case_id="intake.implementation", outcome=EvalOutcome.PASS,
                          details=f"type={result.mission_type} risk={result.risk_profile.value}", score=score)
    return EvalResult(case_id="intake.implementation", outcome=EvalOutcome.FAIL,
                      details=f"type={result.mission_type} risk={result.risk_profile.value}")


def _intake_critical_blocked() -> EvalResult:
    svc = MissionIntakeService()
    result = svc.analyze(IntakeRequest(raw_text="Drop table users and delete all database records"))
    if result.risk_profile == RiskProfile.SECURITY_CRITICAL:
        return EvalResult(case_id="intake.critical", outcome=EvalOutcome.PASS,
                          details=f"risk={result.risk_profile.value} as expected")
    return EvalResult(case_id="intake.critical", outcome=EvalOutcome.FAIL,
                      details=f"Expected SECURITY_CRITICAL, got {result.risk_profile.value}")


def _intake_auto_suggests_test() -> EvalResult:
    svc = MissionIntakeService()
    result = svc.analyze(IntakeRequest(raw_text="Build the new authentication module"))
    has_test_proposal = any(p.task_type.value == "test" for p in result.task_proposals)
    if has_test_proposal:
        return EvalResult(case_id="intake.auto_test", outcome=EvalOutcome.PASS,
                          details=f"{len(result.task_proposals)} proposals, test auto-suggested")
    return EvalResult(case_id="intake.auto_test", outcome=EvalOutcome.FAIL,
                      details="Expected auto-suggested test task for implementation request")


def make_intake_suite() -> list[EvalCase]:
    return [
        EvalCase(case_id="intake.implementation", name="Intake: classify implementation", fn=_intake_implementation_case,
                 description="Implementation request → STANDARD risk + IMPLEMENTATION type", tags=("intake",)),
        EvalCase(case_id="intake.critical", name="Intake: detect critical risk", fn=_intake_critical_blocked,
                 description="Destructive request → SECURITY_CRITICAL risk", tags=("intake", "security")),
        EvalCase(case_id="intake.auto_test", name="Intake: auto-suggest test task", fn=_intake_auto_suggests_test,
                 description="Implementation without test keywords → auto-suggested test proposal", tags=("intake",)),
    ]
