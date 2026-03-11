#!/usr/bin/env python3
"""
hpe-executors.py — HPE Execution Backends (BM-58 / BM-19).
===========================================================

Fournit les stratégies d'exécution pour le HPE Runner.
Chaque executor est un callable(task_dict, plan_outputs) → (success, result).

Backends disponibles :
  - MCPExecutor       : Dispatch via agent-caller + MCP (quand dispo)
  - SequentialExecutor: Fallback sans MCP, exécution séquentielle même contexte
  - SubagentExecutor  : Dispatch via VS Code runSubagent (Copilot Chat)
  - DryRunExecutor    : Dry-run pour tests et validation du DAG
  - AutoExecutor      : Détecte le meilleur backend disponible (MCP → Subagent → Sequential)

Usage dans HPE Runner :
  from hpe_executors import auto_executor
  plan, results = run_plan(plan, project_root, executor=auto_executor(project_root))

Usage standalone :
  python3 hpe-executors.py --project-root . detect
  python3 hpe-executors.py --project-root . test --backend mcp --agent dev --task "hello"

Stdlib only — importe agent-caller.py et message-bus.py par importlib.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger("grimoire.hpe_executors")

HPE_EXECUTORS_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

TRACE_DIR = "_grimoire-output/.hpe/traces"
DEFAULT_TIMEOUT = 120


# ── Protocol ─────────────────────────────────────────────────────────────────


class TaskExecutor(Protocol):
    """Protocol pour les executors HPE."""

    def __call__(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Exécute une tâche HPE.

        Args:
            task: Dictionnaire de la tâche HPE (id, agent, task, etc.)
            outputs: Outputs accumulés des tâches précédentes

        Returns:
            (success: bool, result: dict avec clé 'error' si échec)
        """
        ...


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ExecutionTrace:
    """Trace d'exécution d'une tâche."""

    trace_id: str = ""
    task_id: str = ""
    agent: str = ""
    backend: str = ""
    status: str = ""
    duration_ms: int = 0
    tokens_used: int = 0
    model_used: str = ""
    input_summary: str = ""
    output_summary: str = ""
    error: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.trace_id:
            self.trace_id = f"tr-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BackendCapability:
    """Capacité d'un backend d'exécution."""

    name: str = ""
    available: bool = False
    reason: str = ""
    priority: int = 0  # lower = preferred


# ── Module Loaders ───────────────────────────────────────────────────────────


def _load_module(name: str, filename: str):
    """Charge un module frère par importlib."""
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = Path(__file__).parent / filename
    if not mod_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        _log.warning("Cannot load %s: %s", filename, e)
        return None


def _load_agent_caller():
    return _load_module("agent_caller", "agent-caller.py")


def _load_message_bus():
    return _load_module("message_bus", "message-bus.py")


# ── Trace Writer ─────────────────────────────────────────────────────────────


def save_trace(project_root: Path, trace: ExecutionTrace) -> None:
    """Sauvegarde une trace d'exécution."""
    trace_dir = project_root / TRACE_DIR
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / f"{trace.trace_id}.json"
    trace_file.write_text(
        json.dumps(asdict(trace), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Backend Detection ────────────────────────────────────────────────────────


def detect_backends(project_root: Path) -> list[BackendCapability]:
    """Détecte les backends d'exécution disponibles.

    Ordre de priorité : MCP (1) → Subagent (2) → Sequential (3) → DryRun (4)
    """
    backends = []

    # 1. MCP via agent-caller
    caller_mod = _load_agent_caller()
    if caller_mod:
        try:
            caller = caller_mod.AgentCaller(project_root)
            agents = caller.list_agents()
            backends.append(BackendCapability(
                name="mcp",
                available=len(agents) > 0,
                reason=f"{len(agents)} agents disponibles via agent-caller",
                priority=1,
            ))
        except Exception as e:
            backends.append(BackendCapability(
                name="mcp", available=False, reason=str(e), priority=1,
            ))
    else:
        backends.append(BackendCapability(
            name="mcp", available=False, reason="agent-caller.py non trouvé", priority=1,
        ))

    # 2. Message bus (for worker dispatch)
    bus_mod = _load_message_bus()
    if bus_mod:
        backends.append(BackendCapability(
            name="message-bus",
            available=True,
            reason="message-bus.py disponible pour dispatch worker",
            priority=2,
        ))
    else:
        backends.append(BackendCapability(
            name="message-bus", available=False,
            reason="message-bus.py non trouvé", priority=2,
        ))

    # 3. Sequential (always available)
    backends.append(BackendCapability(
        name="sequential",
        available=True,
        reason="Fallback séquentiel toujours disponible",
        priority=3,
    ))

    # 4. DryRun (always available)
    backends.append(BackendCapability(
        name="dry-run",
        available=True,
        reason="Dry-run toujours disponible",
        priority=4,
    ))

    return sorted(backends, key=lambda b: b.priority)


def best_available_backend(project_root: Path) -> str:
    """Retourne le nom du meilleur backend disponible."""
    for b in detect_backends(project_root):
        if b.available:
            return b.name
    return "dry-run"


# ── Executors ────────────────────────────────────────────────────────────────


class DryRunExecutor:
    """Executor dry-run : marque toutes les tâches comme succès sans rien faire."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root

    def __call__(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        trace = ExecutionTrace(
            task_id=task.get("id", ""),
            agent=task.get("agent", ""),
            backend="dry-run",
            status="success",
            input_summary=task.get("task", "")[:100],
            output_summary="dry-run: no execution",
        )
        if self.project_root:
            save_trace(self.project_root, trace)

        return True, {
            "dry_run": True,
            "task_id": task.get("id", ""),
            "agent": task.get("agent", ""),
            "message": "Dry-run — aucune exécution réelle",
        }


class SequentialExecutor:
    """Executor séquentiel : simule l'exécution en local sans MCP.

    Construit un prompt structuré pour chaque tâche, incluant le contexte
    des outputs précédents. En mode standalone, retourne le prompt comme
    résultat. Avec un LLM callback, exécute réellement.
    """

    def __init__(
        self,
        project_root: Path,
        llm_callback: Any = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.project_root = project_root
        self.llm_callback = llm_callback
        self.timeout = timeout

    def _build_prompt(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> str:
        """Construit le prompt pour la tâche en injectant le contexte."""
        lines = [
            f"# Tâche : {task.get('id', 'unknown')}",
            f"**Agent** : {task.get('agent', 'unknown')}",
            "",
            "## Instructions",
            "",
            task.get("task", ""),
            "",
        ]

        # Inject les outputs des dépendances
        deps = task.get("depends_on", [])
        if deps:
            lines.append("## Contexte des tâches précédentes")
            lines.append("")
            for dep_key, dep_val in outputs.items():
                lines.append(f"### `{dep_key}`")
                if isinstance(dep_val, dict):
                    lines.append(f"```json\n{json.dumps(dep_val, indent=2, ensure_ascii=False)}\n```")
                else:
                    lines.append(str(dep_val))
                lines.append("")

        return "\n".join(lines)

    def __call__(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        start = time.time()
        prompt = self._build_prompt(task, outputs)

        if self.llm_callback:
            try:
                result = self.llm_callback(prompt, task.get("agent", "dev"))
                duration = int((time.time() - start) * 1000)
                trace = ExecutionTrace(
                    task_id=task.get("id", ""),
                    agent=task.get("agent", ""),
                    backend="sequential-llm",
                    status="success",
                    duration_ms=duration,
                    input_summary=task.get("task", "")[:100],
                    output_summary=str(result)[:200],
                )
                save_trace(self.project_root, trace)
                return True, result if isinstance(result, dict) else {"response": result}
            except Exception as e:
                duration = int((time.time() - start) * 1000)
                trace = ExecutionTrace(
                    task_id=task.get("id", ""),
                    agent=task.get("agent", ""),
                    backend="sequential-llm",
                    status="failed",
                    duration_ms=duration,
                    error=str(e),
                )
                save_trace(self.project_root, trace)
                return False, {"error": str(e)}

        # Sans LLM callback : retourne le prompt construit
        duration = int((time.time() - start) * 1000)
        trace = ExecutionTrace(
            task_id=task.get("id", ""),
            agent=task.get("agent", ""),
            backend="sequential-prompt",
            status="success",
            duration_ms=duration,
            input_summary=task.get("task", "")[:100],
            output_summary="prompt-only mode",
        )
        save_trace(self.project_root, trace)

        return True, {
            "prompt": prompt,
            "agent": task.get("agent", ""),
            "task_id": task.get("id", ""),
            "mode": "sequential-prompt",
        }


class MCPExecutor:
    """Executor MCP : dispatch via agent-caller vers les agents Grimoire.

    Utilise agent-caller.py pour construire des requêtes inter-agents
    tracées et validées. Si MCP n'est pas disponible, échoue proprement
    pour permettre au fallback de prendre le relais.
    """

    def __init__(
        self,
        project_root: Path,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.project_root = project_root
        self.timeout = timeout
        self._caller_mod = _load_agent_caller()
        self._caller = None
        if self._caller_mod:
            try:
                self._caller = self._caller_mod.AgentCaller(project_root)
            except Exception as e:
                _log.warning("AgentCaller init failed: %s", e)

    @property
    def available(self) -> bool:
        return self._caller is not None

    def __call__(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        if not self._caller:
            return False, {"error": "MCP backend non disponible (agent-caller absent)"}

        start = time.time()

        # Construire le contexte à partir des outputs précédents
        context_parts = []
        for key, val in outputs.items():
            if isinstance(val, dict):
                context_parts.append(f"[{key}]: {json.dumps(val, ensure_ascii=False)[:500]}")
            else:
                context_parts.append(f"[{key}]: {str(val)[:500]}")

        context = "\n".join(context_parts) if context_parts else ""

        request = self._caller_mod.AgentCallRequest(
            from_agent="hpe-orchestrator",
            to_agent=task.get("agent", "dev"),
            task=task.get("task", ""),
            context=context,
            timeout=self.timeout,
        )

        try:
            response = self._caller.call(request)
            duration = int((time.time() - start) * 1000)

            trace = ExecutionTrace(
                task_id=task.get("id", ""),
                agent=task.get("agent", ""),
                backend="mcp",
                status=response.status,
                duration_ms=duration,
                tokens_used=response.tokens_used,
                model_used=response.model_used,
                input_summary=task.get("task", "")[:100],
                output_summary=response.response[:200] if response.response else "",
                error="" if response.status == "success" else response.response,
            )
            save_trace(self.project_root, trace)

            if response.status == "success":
                return True, {
                    "response": response.response,
                    "model_used": response.model_used,
                    "tokens_used": response.tokens_used,
                    "call_id": response.call_id,
                }
            else:
                return False, {
                    "error": response.response,
                    "status": response.status,
                }

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            trace = ExecutionTrace(
                task_id=task.get("id", ""),
                agent=task.get("agent", ""),
                backend="mcp",
                status="error",
                duration_ms=duration,
                error=str(e),
            )
            save_trace(self.project_root, trace)
            return False, {"error": str(e)}


class AutoExecutor:
    """Executor auto-détection : essaie MCP, puis fallback séquentiel.

    Ordre de tentative :
    1. MCP (via agent-caller) — si disponible
    2. Sequential (fallback garanti)

    Si MCP échoue sur une tâche, bascule automatiquement sur Sequential
    pour cette tâche et log un warning.
    """

    def __init__(
        self,
        project_root: Path,
        llm_callback: Any = None,
        timeout: int = DEFAULT_TIMEOUT,
        force_backend: str | None = None,
    ):
        self.project_root = project_root
        self.timeout = timeout

        self._mcp = MCPExecutor(project_root, timeout)
        self._seq = SequentialExecutor(project_root, llm_callback, timeout)
        self._dry = DryRunExecutor(project_root)

        self._force_backend = force_backend
        self._fallback_count = 0
        self._mcp_failures = 0

    @property
    def active_backend(self) -> str:
        if self._force_backend:
            return self._force_backend
        if self._mcp.available:
            return "mcp"
        return "sequential"

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "active_backend": self.active_backend,
            "mcp_available": self._mcp.available,
            "fallback_count": self._fallback_count,
            "mcp_failures": self._mcp_failures,
        }

    def __call__(
        self,
        task: dict[str, Any],
        outputs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        # Force backend
        if self._force_backend == "dry-run":
            return self._dry(task, outputs)
        if self._force_backend == "sequential":
            return self._seq(task, outputs)
        if self._force_backend == "mcp":
            return self._mcp(task, outputs)

        # Auto-detect : try MCP first
        if self._mcp.available:
            success, result = self._mcp(task, outputs)
            if success:
                return success, result

            # MCP failed → fallback to sequential
            self._mcp_failures += 1
            self._fallback_count += 1
            _log.warning(
                "MCP failed for task %s (agent=%s), falling back to sequential. Error: %s",
                task.get("id", "?"), task.get("agent", "?"), result.get("error", "?"),
            )

        # Sequential fallback
        return self._seq(task, outputs)


# ── Factory Functions ────────────────────────────────────────────────────────


def auto_executor(
    project_root: Path | str,
    llm_callback: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    force_backend: str | None = None,
) -> AutoExecutor:
    """Factory pour créer un AutoExecutor pré-configuré.

    Args:
        project_root: Racine du projet.
        llm_callback: Optionnel — callable(prompt, agent_name) → result.
        timeout: Timeout par tâche en secondes.
        force_backend: Forcer un backend spécifique (mcp, sequential, dry-run).

    Returns:
        AutoExecutor prêt à l'emploi.
    """
    return AutoExecutor(
        project_root=Path(project_root).resolve(),
        llm_callback=llm_callback,
        timeout=timeout,
        force_backend=force_backend,
    )


def dry_executor(project_root: Path | str | None = None) -> DryRunExecutor:
    """Factory pour créer un DryRunExecutor."""
    root = Path(project_root).resolve() if project_root else None
    return DryRunExecutor(root)


def sequential_executor(
    project_root: Path | str,
    llm_callback: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> SequentialExecutor:
    """Factory pour créer un SequentialExecutor."""
    return SequentialExecutor(
        project_root=Path(project_root).resolve(),
        llm_callback=llm_callback,
        timeout=timeout,
    )


def mcp_executor(
    project_root: Path | str,
    timeout: int = DEFAULT_TIMEOUT,
) -> MCPExecutor:
    """Factory pour créer un MCPExecutor."""
    return MCPExecutor(
        project_root=Path(project_root).resolve(),
        timeout=timeout,
    )


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_hpe_detect_backends(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: détecte les backends d'exécution disponibles.

    Args:
        project_root: Racine du projet.

    Returns:
        Liste des backends avec disponibilité.
    """
    root = Path(project_root).resolve()
    backends = detect_backends(root)
    best = best_available_backend(root)
    return {
        "backends": [asdict(b) for b in backends],
        "recommended": best,
    }


def mcp_hpe_execute_task(
    task_json: str,
    outputs_json: str = "{}",
    backend: str = "auto",
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: exécute une tâche HPE isolée.

    Args:
        task_json: Tâche HPE en JSON.
        outputs_json: Outputs disponibles en JSON.
        backend: Backend à utiliser (auto, mcp, sequential, dry-run).
        project_root: Racine du projet.

    Returns:
        Résultat de l'exécution.
    """
    root = Path(project_root).resolve()
    task = json.loads(task_json)
    outputs = json.loads(outputs_json)

    if backend == "auto":
        executor = auto_executor(root)
    elif backend == "mcp":
        executor = mcp_executor(root)
    elif backend == "sequential":
        executor = sequential_executor(root)
    else:
        executor = dry_executor(root)

    success, result = executor(task, outputs)
    return {
        "success": success,
        "result": result,
        "backend_used": backend if backend != "auto" else getattr(executor, "active_backend", "unknown"),
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_detect(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    backends = detect_backends(root)
    best = best_available_backend(root)

    if args.json:
        print(json.dumps({
            "backends": [asdict(b) for b in backends],
            "recommended": best,
        }, indent=2, ensure_ascii=False))
    else:
        print("\n  🔍 Backends d'exécution HPE détectés\n")
        for b in backends:
            icon = "✅" if b.available else "❌"
            print(f"  {icon} [{b.priority}] {b.name} — {b.reason}")
        print(f"\n  ➡️  Recommandé : {best}")

    return 0


def cmd_test(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    backend = args.backend

    task = {
        "id": f"test-{uuid.uuid4().hex[:6]}",
        "agent": args.agent,
        "task": args.task,
        "depends_on": [],
        "output_key": "test_output",
    }

    if backend == "auto":
        executor = auto_executor(root)
    elif backend == "mcp":
        executor = mcp_executor(root)
    elif backend == "sequential":
        executor = sequential_executor(root)
    else:
        executor = dry_executor(root)

    success, result = executor(task, {})

    if args.json:
        print(json.dumps({"success": success, "result": result}, indent=2, ensure_ascii=False))
    else:
        icon = "✅" if success else "❌"
        print(f"\n  {icon} Test backend={backend} agent={args.agent}")
        if success:
            for k, v in result.items():
                val = str(v)[:80]
                print(f"    {k}: {val}")
        else:
            print(f"    Error: {result.get('error', '?')}")

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HPE Executors — Backends d'exécution pour le Hybrid Parallelism Engine",
    )
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--version", action="version", version=f"hpe-executors {HPE_EXECUTORS_VERSION}")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("detect", help="Detect available backends")

    p_test = sub.add_parser("test", help="Test a backend")
    p_test.add_argument("--backend", default="auto", choices=["auto", "mcp", "sequential", "dry-run"])
    p_test.add_argument("--agent", default="dev")
    p_test.add_argument("--task", default="Ping test")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "detect": cmd_detect,
        "test": cmd_test,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
