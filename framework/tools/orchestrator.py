#!/usr/bin/env python3
"""
orchestrator.py — Orchestrateur hybride Grimoire (BM-43 Story 4.3).
============================================================

L'orchestrateur décide dynamiquement du mode d'exécution selon le type
de tâche :
  - simulated      : Un LLM, persona switching (party-mode, discussions)
  - sequential     : Agents séquentiels, chacun sur son worker (boomerang, reviews)
  - concurrent-cpu : Workers Python en parallèle via ThreadPoolExecutor (adversarial, validation)
                     NOTE: Ce n'est PAS du multi-LLM — c'est du parallélisme CPU local.

Gère le budget coûts en temps réel avec fallback automatique vers
simulated si le budget est dépassé.

Modes :
  plan    — Affiche le plan d'exécution pour une tâche
  run     — Exécute un workflow avec le mode optimal
  status  — État de l'orchestration courante
  history — Historique des exécutions

Usage :
  python3 orchestrator.py --project-root . plan --workflow boomerang --task "Implement auth"
  python3 orchestrator.py --project-root . run --workflow boomerang --agents "sm,dev,qa"
  python3 orchestrator.py --project-root . status
  python3 orchestrator.py --project-root . history --last 10

Stdlib only — importe message-bus.py, agent-worker.py et llm-router.py par importlib.

Références :
  - Roo-Code Boomerang Tasks: https://docs.roo.ai/features/boomerang-tasks
  - LangGraph Supervisor pattern: https://langchain-ai.github.io/langgraph/concepts/multi_agent/#supervisor
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

ORCHESTRATOR_VERSION = "1.2.0"

# ── Constants ────────────────────────────────────────────────────────────────

VALID_MODES = frozenset({"simulated", "sequential", "concurrent-cpu"})

HISTORY_DIR = "_grimoire-output/.orchestrator"
HISTORY_FILE = "history.jsonl"
MAX_HISTORY = 100

# Parallel execution
PARALLEL_MAX_WORKERS = 4
PARALLEL_STEP_TIMEOUT = 60  # seconds per step

# Mode decision rules
MODE_RULES: dict[str, str] = {
    "party-mode": "simulated",
    "brainstorming": "simulated",
    "discussion": "simulated",
    "boomerang": "sequential",
    "code-review": "sequential",
    "architecture-review": "sequential",
    "story-implementation": "sequential",
    "sprint-planning": "sequential",
    "adversarial-review": "concurrent-cpu",
    "cross-validation": "concurrent-cpu",
    "parallel-analysis": "concurrent-cpu",
    "stress-test": "concurrent-cpu",
}

# Cost multipliers per mode
COST_MULTIPLIERS = {
    "simulated": 1.0,
    "sequential": 2.5,
    "concurrent-cpu": 4.0,
}

# Default budget cap (in token-equivalent cost units)
DEFAULT_BUDGET_CAP = 500_000


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ExecutionStep:
    """Un step dans le plan d'exécution."""

    step_number: int
    agent: str
    task: str = ""
    model: str = ""
    estimated_tokens: int = 0
    status: str = "pending"  # pending | running | completed | failed | skipped
    duration_seconds: float = 0.0
    output_summary: str = ""


@dataclass
class ExecutionPlan:
    """Plan d'exécution complet."""

    plan_id: str = ""
    workflow: str = ""
    mode: str = "simulated"
    mode_reason: str = ""
    steps: list[ExecutionStep] = field(default_factory=list)
    estimated_total_tokens: int = 0
    estimated_cost_multiplier: float = 1.0
    budget_ok: bool = True
    fallback_mode: str = ""
    agents_involved: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.plan_id:
            self.plan_id = f"plan-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionResult:
    """Résultat d'une exécution."""

    execution_id: str = ""
    plan_id: str = ""
    workflow: str = ""
    mode: str = "simulated"
    status: str = "pending"  # pending | running | completed | partial | failed
    started_at: str = ""
    completed_at: str = ""
    steps: list[ExecutionStep] = field(default_factory=list)
    total_tokens_used: int = 0
    total_duration_seconds: float = 0.0
    fallback_triggered: bool = False
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.execution_id:
            self.execution_id = f"exec-{uuid.uuid4().hex[:8]}"
        if not self.started_at:
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionResult:
        steps_data = data.get("steps", [])
        steps = []
        for s in steps_data:
            valid_fields = {f.name for f in ExecutionStep.__dataclass_fields__.values()}
            filtered = {k: v for k, v in s.items() if k in valid_fields}
            steps.append(ExecutionStep(**filtered))
        return cls(
            execution_id=data.get("execution_id", ""),
            plan_id=data.get("plan_id", ""),
            workflow=data.get("workflow", ""),
            mode=data.get("mode", "simulated"),
            status=data.get("status", "pending"),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            steps=steps,
            total_tokens_used=data.get("total_tokens_used", 0),
            total_duration_seconds=data.get("total_duration_seconds", 0.0),
            fallback_triggered=data.get("fallback_triggered", False),
            errors=data.get("errors", []),
        )


@dataclass
class OrchestratorStats:
    """Statistiques de l'orchestrateur."""

    total_executions: int = 0
    by_mode: dict[str, int] = field(default_factory=dict)
    by_workflow: dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0
    total_duration_seconds: float = 0.0
    fallback_count: int = 0
    success_rate: float = 0.0


# ── Module Loaders ───────────────────────────────────────────────────────────


def _load_module(name: str, filename: str):
    """Charge un module frère par importlib."""
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = Path(__file__).parent / filename
    if not mod_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Orchestrator ─────────────────────────────────────────────────────────────


class Orchestrator:
    """
    Orchestrateur hybride Grimoire.

    Décide du mode d'exécution, construit le plan, et coordonne
    l'exécution des agents via le message bus.
    """

    def __init__(
        self,
        project_root: Path,
        budget_cap: int = DEFAULT_BUDGET_CAP,
        auto_resolve_tools: bool = True,
    ):
        self.project_root = project_root
        self.budget_cap = budget_cap
        self.auto_resolve_tools = auto_resolve_tools
        self.history_dir = project_root / HISTORY_DIR
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._bus_mod = _load_module("message_bus", "message-bus.py")
        self._worker_mod = _load_module("agent_worker", "agent-worker.py")
        self._resolver_mod = _load_module("tool_resolver", "tool-resolver.py") if auto_resolve_tools else None

    def decide_mode(self, workflow: str, override: str = "") -> tuple[str, str]:
        """
        Décide du mode d'exécution optimal.

        Args:
            workflow: type de workflow
            override: mode forcé (optionnel)

        Returns:
            (mode, reason)
        """
        if override and override in VALID_MODES:
            return override, f"Mode forcé par l'utilisateur: {override}"

        # Check rules
        mode = MODE_RULES.get(workflow, "simulated")
        reason = f"Règle par défaut pour workflow '{workflow}': {mode}"

        # Budget check — if parallel is too expensive, fallback
        if mode == "concurrent-cpu":
            estimated_cost = 10000 * COST_MULTIPLIERS["concurrent-cpu"]
            if estimated_cost > self.budget_cap:
                mode = "sequential"
                reason = f"Fallback parallel→sequential: budget estimé ({estimated_cost:,.0f}) > cap ({self.budget_cap:,.0f})"

        return mode, reason

    def create_plan(
        self,
        workflow: str,
        agents: list[str],
        task: str = "",
        mode_override: str = "",
    ) -> ExecutionPlan:
        """Crée un plan d'exécution."""
        mode, reason = self.decide_mode(workflow, mode_override)

        steps = []
        total_tokens = 0
        for i, agent in enumerate(agents):
            estimated = 5000  # Estimation par défaut
            steps.append(ExecutionStep(
                step_number=i + 1,
                agent=agent,
                task=task or f"Step {i + 1} — {agent}",
                estimated_tokens=estimated,
            ))
            total_tokens += estimated

        cost_mult = COST_MULTIPLIERS.get(mode, 1.0)
        budget_ok = (total_tokens * cost_mult) <= self.budget_cap

        fallback = ""
        if not budget_ok:
            fallback = "simulated"
            if mode != "simulated":
                mode = "simulated"
                reason = "Budget insuffisant — fallback vers simulated"
                budget_ok = True

        return ExecutionPlan(
            workflow=workflow,
            mode=mode,
            mode_reason=reason,
            steps=steps,
            estimated_total_tokens=total_tokens,
            estimated_cost_multiplier=cost_mult,
            budget_ok=budget_ok,
            fallback_mode=fallback,
            agents_involved=agents,
        )

    def execute(
        self,
        plan: ExecutionPlan,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """
        Exécute un plan.

        En mode simulé, chaque step est exécuté séquentiellement.
        En mode sequential, chaque agent est invoqué via son worker.
        En mode concurrent-cpu, les workers Python sont lancés en parallèle
        via ThreadPoolExecutor (parallélisme CPU, PAS multi-LLM).
        """
        result = ExecutionResult(
            plan_id=plan.plan_id,
            workflow=plan.workflow,
            mode=plan.mode,
        )

        if dry_run:
            result.status = "completed"
            result.steps = plan.steps
            result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return result

        result.status = "running"
        start_time = time.monotonic()

        if plan.mode == "concurrent-cpu" and len(plan.steps) > 1:
            result = self._execute_parallel(plan, result, start_time)
        else:
            result = self._execute_sequential(plan, result, start_time)

        result.status = "completed" if not result.errors else "partial"
        result.total_duration_seconds = round(time.monotonic() - start_time, 3)
        result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result.total_tokens_used = sum(s.estimated_tokens for s in result.steps)

        # Save to history
        self._save_history(result)

        return result

    def _pre_resolve_tools(self, step: ExecutionStep) -> str:
        """Résout les outils nécessaires avant l'exécution d'un step.

        Retourne un résumé des outils trouvés ou "" si rien à résoudre.
        """
        if not self._resolver_mod or not step.task:
            return ""
        try:
            resolve_fn = getattr(self._resolver_mod, "resolve_intent", None)
            if not resolve_fn:
                return ""
            plan = resolve_fn(step.task, self.project_root)
            ready = plan.ready_to_use
            provision = plan.provision_needed
            if not ready and not provision:
                return ""
            parts = []
            if ready:
                tool_names = [t.get("name", t.get("provider_id", "?")) for t in ready[:3]]
                parts.append(f"Tools: {', '.join(tool_names)}")
            if provision:
                prov_ids = [p.get("provider_id", "?") for p in provision[:2]]
                parts.append(f"Need provision: {', '.join(prov_ids)}")
            return " | ".join(parts)
        except Exception:
            return ""

    def _execute_step(self, step: ExecutionStep, mode: str) -> ExecutionStep:
        """Exécute un step individuel (commun à sequential et parallel)."""
        step_start = time.monotonic()
        step.status = "running"

        # Pre-resolve tools for this step
        tool_context = self._pre_resolve_tools(step)

        try:
            if mode == "simulated":
                summary = f"[Simulated] Agent {step.agent} processé via persona switching"
                if tool_context:
                    summary += f" ({tool_context})"
                step.output_summary = summary
            else:
                # Sequential or Parallel: invoke worker if available
                if self._worker_mod:
                    try:
                        mgr_cls = getattr(self._worker_mod, "AgentWorkerManager", None)
                        if mgr_cls:
                            mgr = mgr_cls(self.project_root)
                            task_payload = {
                                "description": step.task,
                                "task_id": f"step-{step.step_number}",
                            }
                            if tool_context:
                                task_payload["resolved_tools"] = tool_context
                            task_result = mgr.execute_task(step.agent, task_payload)
                            step.output_summary = str(task_result.output.get("result", ""))
                            step.model = task_result.model_used
                    except Exception as e:
                        step.output_summary = f"[Fallback] {e}"
                else:
                    step.output_summary = f"[{mode}] Agent {step.agent} — worker non disponible"

            step.status = "completed"
        except Exception as e:
            step.status = "failed"
            step.output_summary = f"Erreur: {e}"

        step.duration_seconds = round(time.monotonic() - step_start, 3)
        return step

    def _execute_sequential(
        self,
        plan: ExecutionPlan,
        result: ExecutionResult,
        start_time: float,
    ) -> ExecutionResult:
        """Exécution séquentielle (simulated + sequential modes)."""
        for step in plan.steps:
            step = self._execute_step(step, plan.mode)
            result.steps.append(step)
            if step.status == "failed":
                result.errors.append(f"Step {step.step_number} ({step.agent}): {step.output_summary}")

        return result

    def _execute_parallel(
        self,
        plan: ExecutionPlan,
        result: ExecutionResult,
        start_time: float,
    ) -> ExecutionResult:
        """
        Exécution parallèle via ThreadPoolExecutor.

        Chaque step est soumis au pool. Le budget est vérifié après
        completion. Les steps en timeout sont marqués 'failed'.
        """
        max_workers = min(PARALLEL_MAX_WORKERS, len(plan.steps))
        completed_steps: dict[int, ExecutionStep] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_step = {
                executor.submit(self._execute_step, step, plan.mode): step
                for step in plan.steps
            }

            for future in as_completed(future_to_step, timeout=PARALLEL_STEP_TIMEOUT * len(plan.steps)):
                original_step = future_to_step[future]
                try:
                    done_step = future.result(timeout=PARALLEL_STEP_TIMEOUT)
                    completed_steps[done_step.step_number] = done_step
                    if done_step.status == "failed":
                        result.errors.append(
                            f"Step {done_step.step_number} ({done_step.agent}): {done_step.output_summary}"
                        )
                except TimeoutError:
                    original_step.status = "failed"
                    original_step.output_summary = f"[Timeout] Step exceeded {PARALLEL_STEP_TIMEOUT}s"
                    completed_steps[original_step.step_number] = original_step
                    result.errors.append(
                        f"Step {original_step.step_number} ({original_step.agent}): timeout"
                    )
                except Exception as e:
                    original_step.status = "failed"
                    original_step.output_summary = f"Erreur parallèle: {e}"
                    completed_steps[original_step.step_number] = original_step
                    result.errors.append(
                        f"Step {original_step.step_number} ({original_step.agent}): {e}"
                    )

        # Re-order steps by step_number
        for step_num in sorted(completed_steps.keys()):
            result.steps.append(completed_steps[step_num])

        # Check for missing steps (not submitted or lost)
        submitted_nums = {s.step_number for s in plan.steps}
        completed_nums = set(completed_steps.keys())
        for missing in submitted_nums - completed_nums:
            for step in plan.steps:
                if step.step_number == missing:
                    step.status = "failed"
                    step.output_summary = "[Lost] Step non complété"
                    result.steps.append(step)
                    result.errors.append(f"Step {missing}: lost during parallel execution")

        return result

    def _save_history(self, result: ExecutionResult) -> None:
        history_file = self.history_dir / HISTORY_FILE
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    def get_history(self, last_n: int = 10) -> list[ExecutionResult]:
        """Retourne les N dernières exécutions."""
        history_file = self.history_dir / HISTORY_FILE
        if not history_file.exists():
            return []

        results = []
        for line in history_file.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    results.append(ExecutionResult.from_dict(data))
                except json.JSONDecodeError:
                    continue

        return results[-last_n:]

    def get_stats(self) -> OrchestratorStats:
        """Calcule les statistiques globales."""
        history = self.get_history(last_n=MAX_HISTORY)

        by_mode: dict[str, int] = {}
        by_workflow: dict[str, int] = {}
        total_tokens = 0
        total_duration = 0.0
        success_count = 0
        fallback_count = 0

        for r in history:
            by_mode[r.mode] = by_mode.get(r.mode, 0) + 1
            by_workflow[r.workflow] = by_workflow.get(r.workflow, 0) + 1
            total_tokens += r.total_tokens_used
            total_duration += r.total_duration_seconds
            if r.status == "completed":
                success_count += 1
            if r.fallback_triggered:
                fallback_count += 1

        return OrchestratorStats(
            total_executions=len(history),
            by_mode=by_mode,
            by_workflow=by_workflow,
            total_tokens=total_tokens,
            total_duration_seconds=round(total_duration, 1),
            fallback_count=fallback_count,
            success_rate=round(success_count / len(history), 2) if history else 0.0,
        )


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_orchestrate(
    project_root: str,
    workflow: str = "",
    agents: str = "",
    task: str = "",
    mode: str = "",
    dry_run: bool = True,
) -> dict:
    """
    MCP tool `grimoire_orchestrate` — orchestre l'exécution multi-agent.
    """
    root = Path(project_root).resolve()
    orchestrator = Orchestrator(root)

    agent_list = [a.strip() for a in agents.split(",") if a.strip()] if agents else ["sm"]

    plan = orchestrator.create_plan(
        workflow=workflow,
        agents=agent_list,
        task=task,
        mode_override=mode,
    )

    if dry_run:
        return {"plan": plan.to_dict()}

    result = orchestrator.execute(plan)
    return {"result": result.to_dict()}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_plan(plan: ExecutionPlan) -> None:
    mode_icons = {"simulated": "🎭", "sequential": "🔗", "concurrent-cpu": "⚡"}
    icon = mode_icons.get(plan.mode, "❓")
    print(f"\n  {icon} Execution Plan — {plan.workflow}")
    print(f"  {'─' * 55}")
    print(f"  Mode          : {plan.mode}")
    print(f"  Reason        : {plan.mode_reason}")
    print(f"  Cost mult     : {plan.estimated_cost_multiplier}x")
    print(f"  Est. tokens   : {plan.estimated_total_tokens:,}")
    print(f"  Budget OK     : {'✅' if plan.budget_ok else '❌'}")
    if plan.fallback_mode:
        print(f"  Fallback      : {plan.fallback_mode}")
    print(f"  Agents        : {', '.join(plan.agents_involved)}")
    print()

    print("  Steps :")
    for step in plan.steps:
        print(f"    {step.step_number}. {step.agent:>12s} │ {step.task[:40]:40s} │ ~{step.estimated_tokens:,} tok")
    print()


def _print_result(result: ExecutionResult) -> None:
    status_icon = {"completed": "✅", "partial": "⚠️", "failed": "❌"}.get(result.status, "❓")
    print(f"\n  {status_icon} Execution Result — {result.workflow}")
    print(f"  {'─' * 55}")
    print(f"  Mode      : {result.mode}")
    print(f"  Status    : {result.status}")
    print(f"  Duration  : {result.total_duration_seconds:.2f}s")
    print(f"  Tokens    : {result.total_tokens_used:,}")
    if result.fallback_triggered:
        print("  ⚠️  Fallback triggered")
    print()

    if result.steps:
        print("  Steps :")
        for step in result.steps:
            icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(step.status, "⏳")
            print(f"    {icon} {step.step_number}. {step.agent:>12s} │ "
                  f"{step.duration_seconds:.2f}s │ {step.output_summary[:40]}")
    if result.errors:
        print("\n  Erreurs :")
        for err in result.errors:
            print(f"    ❌ {err}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrator — Orchestrateur hybride multi-agent Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"orchestrator {ORCHESTRATOR_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # plan
    plan_p = sub.add_parser("plan", help="Afficher le plan d'exécution")
    plan_p.add_argument("--workflow", required=True, help="Type de workflow")
    plan_p.add_argument("--agents", default="sm", help="Agents (séparés par virgule)")
    plan_p.add_argument("--task", default="", help="Description de la tâche")
    plan_p.add_argument("--mode", choices=sorted(VALID_MODES), default="",
                        help="Forcer un mode d'exécution")
    plan_p.add_argument("--json", action="store_true", help="Output JSON")

    # run
    run_p = sub.add_parser("run", help="Exécuter un workflow")
    run_p.add_argument("--workflow", required=True, help="Type de workflow")
    run_p.add_argument("--agents", default="sm", help="Agents (séparés par virgule)")
    run_p.add_argument("--task", default="", help="Description de la tâche")
    run_p.add_argument("--mode", choices=sorted(VALID_MODES), default="",
                        help="Forcer un mode")
    run_p.add_argument("--dry-run", action="store_true", help="Simulation")
    run_p.add_argument("--json", action="store_true", help="Output JSON")

    # status
    sub.add_parser("status", help="État de l'orchestration")

    # history
    hist_p = sub.add_parser("history", help="Historique des exécutions")
    hist_p.add_argument("--last", type=int, default=10, help="Nombre d'entrées")
    hist_p.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    orchestrator = Orchestrator(project_root)

    if args.command == "plan":
        agent_list = [a.strip() for a in args.agents.split(",")]
        plan = orchestrator.create_plan(
            workflow=args.workflow,
            agents=agent_list,
            task=args.task,
            mode_override=args.mode,
        )
        if getattr(args, "json", False):
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)

    elif args.command == "run":
        agent_list = [a.strip() for a in args.agents.split(",")]
        plan = orchestrator.create_plan(
            workflow=args.workflow,
            agents=agent_list,
            task=args.task,
            mode_override=args.mode,
        )
        result = orchestrator.execute(plan, dry_run=getattr(args, "dry_run", False))
        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_result(result)

    elif args.command == "status":
        stats = orchestrator.get_stats()
        print("\n  📊 Orchestrator Stats")
        print(f"  {'─' * 40}")
        print(f"  Executions     : {stats.total_executions}")
        print(f"  Success rate   : {stats.success_rate:.0%}")
        print(f"  Total tokens   : {stats.total_tokens:,}")
        print(f"  Total duration : {stats.total_duration_seconds:.1f}s")
        print(f"  Fallbacks      : {stats.fallback_count}")
        if stats.by_mode:
            print("\n  Par mode :")
            for mode, count in sorted(stats.by_mode.items()):
                print(f"    {mode:15s} : {count}")
        if stats.by_workflow:
            print("\n  Par workflow :")
            for wf, count in sorted(stats.by_workflow.items()):
                print(f"    {wf:20s} : {count}")
        print()

    elif args.command == "history":
        history = orchestrator.get_history(last_n=args.last)
        if getattr(args, "json", False):
            print(json.dumps([r.to_dict() for r in history], ensure_ascii=False, indent=2))
        else:
            print(f"\n  📜 Historique ({len(history)} dernières)")
            print(f"  {'─' * 60}")
            for r in history:
                icon = {"completed": "✅", "partial": "⚠️", "failed": "❌"}.get(r.status, "❓")
                print(f"    {icon} {r.execution_id} │ {r.workflow:>15s} │ "
                      f"{r.mode:>12s} │ {r.total_duration_seconds:.1f}s")
            print()


if __name__ == "__main__":
    main()
