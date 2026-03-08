#!/usr/bin/env python3
"""
agent-worker.py — Agent Worker Process Grimoire (BM-43 Story 4.2).
============================================================

Chaque agent Grimoire peut tourner comme un worker isolé avec son propre
LLM, recevant des tâches via le message bus et produisant des résultats
validés par les delivery contracts.

Le worker :
  - Charge sa persona depuis _grimoire/_config/agents/
  - Se connecte au message bus
  - Écoute les messages entrants
  - Utilise le LLM Router pour sélectionner son modèle
  - Exécute la tâche et retourne le résultat

Modes :
  start   — Démarre un worker pour un agent
  stop    — Arrête un worker
  status  — Affiche l'état des workers actifs
  list    — Liste les agents disponibles avec leurs capabilities

Usage :
  python3 agent-worker.py --project-root . start --agent architect
  python3 agent-worker.py --project-root . status
  python3 agent-worker.py --project-root . list
  python3 agent-worker.py --project-root . stop --agent architect

Stdlib only — importe message-bus.py et llm-router.py par importlib.

Références :
  - LangGraph Multi-agent: https://langchain-ai.github.io/langgraph/concepts/multi_agent/
  - MetaGPT SOP: https://github.com/geekan/MetaGPT
  - Swarm (OpenAI): https://github.com/openai/swarm
  - Semantic Kernel Multi-agent: https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-chat
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.agent_worker")

# ── Version ──────────────────────────────────────────────────────────────────

AGENT_WORKER_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

WORKERS_DIR = "_grimoire-output/.workers"
MAX_PARALLEL_WORKERS = 5
HEALTHCHECK_INTERVAL = 60  # seconds
WORKER_TIMEOUT = 3600  # 1 hour
DEFAULT_PROVIDER = "copilot"

KNOWN_AGENTS: dict[str, dict] = {
    "analyst": {
        "title": "Business Analyst",
        "persona": "Mary",
        "capabilities": ["market-research", "competitive-analysis", "requirements-elicitation"],
        "suggested_model_tier": "mid",
        "suggested_provider": "copilot",
    },
    "architect": {
        "title": "Architect",
        "persona": "Winston",
        "capabilities": ["distributed-systems", "cloud-infrastructure", "api-design", "scalable-patterns"],
        "suggested_model_tier": "high",
        "suggested_provider": "anthropic",
    },
    "dev": {
        "title": "Developer Agent",
        "persona": "Amelia",
        "capabilities": ["story-execution", "test-driven-development", "code-implementation"],
        "suggested_model_tier": "high",
        "suggested_provider": "anthropic",
    },
    "pm": {
        "title": "Product Manager",
        "persona": "John",
        "capabilities": ["prd-creation", "requirements-discovery", "stakeholder-alignment"],
        "suggested_model_tier": "mid",
        "suggested_provider": "copilot",
    },
    "qa": {
        "title": "QA Engineer",
        "persona": "Quinn",
        "capabilities": ["test-automation", "api-testing", "e2e-testing", "coverage-analysis"],
        "suggested_model_tier": "mid",
        "suggested_provider": "copilot",
    },
    "sm": {
        "title": "Scrum Master",
        "persona": "Bob",
        "capabilities": ["sprint-planning", "story-preparation", "agile-ceremonies"],
        "suggested_model_tier": "mid",
        "suggested_provider": "copilot",
    },
    "tech-writer": {
        "title": "Technical Writer",
        "persona": "Paige",
        "capabilities": ["documentation", "mermaid-diagrams", "concept-explanation"],
        "suggested_model_tier": "low",
        "suggested_provider": "copilot",
    },
    "ux-designer": {
        "title": "UX Designer",
        "persona": "Sally",
        "capabilities": ["user-research", "interaction-design", "ui-patterns"],
        "suggested_model_tier": "mid",
        "suggested_provider": "copilot",
    },
}

VALID_PROVIDERS = frozenset({"anthropic", "openai", "ollama", "copilot"})


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class WorkerConfig:
    """Configuration d'un worker."""

    agent_id: str
    provider: str = DEFAULT_PROVIDER
    model: str = ""
    max_tasks: int = 100
    timeout_seconds: int = WORKER_TIMEOUT
    auto_restart: bool = False


@dataclass
class WorkerStatus:
    """Statut d'un worker actif."""

    worker_id: str = ""
    agent_id: str = ""
    status: str = "stopped"  # stopped | starting | running | stopping | error
    pid: int = 0
    started_at: str = ""
    last_heartbeat: str = ""
    tasks_completed: int = 0
    tasks_failed: int = 0
    current_task: str = ""
    provider: str = DEFAULT_PROVIDER
    model: str = ""
    uptime_seconds: float = 0.0
    error: str = ""

    def __post_init__(self):
        if not self.worker_id:
            self.worker_id = f"w-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> WorkerStatus:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @property
    def is_alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


@dataclass
class WorkerList:
    """Liste des workers."""

    workers: list[WorkerStatus] = field(default_factory=list)
    running_count: int = 0
    total_count: int = 0
    max_parallel: int = MAX_PARALLEL_WORKERS


@dataclass
class TaskResult:
    """Résultat d'une tâche exécutée par un worker."""

    task_id: str = ""
    agent_id: str = ""
    status: str = "success"  # success | error | timeout
    output: dict = field(default_factory=dict)
    error: str = ""
    duration_seconds: float = 0.0
    model_used: str = ""
    tokens_used: int = 0


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


def _load_message_bus():
    return _load_module("message_bus", "message-bus.py")


def _load_llm_router():
    return _load_module("llm_router", "llm-router.py")


# ── Agent Worker Manager ──────────────────────────────────────────────────────


class AgentWorkerManager:
    """
    Gère les workers d'agents Grimoire.

    Chaque worker a un manifest dans _grimoire-output/.workers/<worker-id>.json
    """

    def __init__(self, project_root: Path, max_parallel: int = MAX_PARALLEL_WORKERS):
        self.project_root = project_root
        self.workers_dir = project_root / WORKERS_DIR
        self.workers_dir.mkdir(parents=True, exist_ok=True)
        self.max_parallel = max_parallel
        self._bus_mod = _load_message_bus()
        self._router_mod = _load_llm_router()

    def _save_worker(self, status: WorkerStatus) -> None:
        path = self.workers_dir / f"{status.worker_id}.json"
        path.write_text(
            json.dumps(status.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_worker(self, worker_id: str) -> WorkerStatus | None:
        path = self.workers_dir / f"{worker_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return WorkerStatus.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def _find_worker_by_agent(self, agent_id: str) -> WorkerStatus | None:
        """Trouve un worker actif pour un agent."""
        for f in self.workers_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("agent_id") == agent_id and data.get("status") == "running":
                    return WorkerStatus.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return None

    def _count_running(self) -> int:
        count = 0
        for f in self.workers_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ws = WorkerStatus.from_dict(data)
                if ws.status == "running" and ws.is_alive:
                    count += 1
                elif ws.status == "running" and not ws.is_alive:
                    # Dead process — update status
                    ws.status = "error"
                    ws.error = "Process died"
                    self._save_worker(ws)
            except (json.JSONDecodeError, KeyError):
                continue
        return count

    def _resolve_model(self, agent_id: str) -> str:
        """Résout le modèle via LLM Router ou défaut."""
        agent_info = KNOWN_AGENTS.get(agent_id, {})
        tier = agent_info.get("suggested_model_tier", "mid")

        if self._router_mod:
            try:
                classifier_cls = getattr(self._router_mod, "TaskClassifier", None)
                if classifier_cls:
                    classifier = classifier_cls()
                    # Use tier as complexity proxy
                    model = classifier.classify(f"Agent {agent_id} task — tier {tier}")
                    return model
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Fallback
        tier_defaults = {
            "high": "claude-sonnet-4-20250514",
            "mid": "gpt-4o-mini",
            "low": "deepseek-v3",
        }
        return tier_defaults.get(tier, "claude-sonnet-4-20250514")

    def start_worker(
        self,
        agent_id: str,
        provider: str = "",
        model: str = "",
    ) -> WorkerStatus:
        """
        Démarre un worker pour un agent.

        Ne lance pas réellement un sous-processus (on est dans un framework
        mono-process). Enregistre le worker comme disponible dans le registry.
        """
        if agent_id not in KNOWN_AGENTS:
            raise ValueError(f"Agent inconnu: '{agent_id}'. Disponibles: {sorted(KNOWN_AGENTS)}")

        # Check if already running
        existing = self._find_worker_by_agent(agent_id)
        if existing and existing.is_alive:
            raise RuntimeError(f"Un worker pour '{agent_id}' est déjà actif (id: {existing.worker_id})")

        # Check concurrent limit
        running = self._count_running()
        if running >= self.max_parallel:
            raise RuntimeError(
                f"Maximum {self.max_parallel} workers atteint ({running} actifs). "
                f"Arrêtez un worker avant d'en démarrer un nouveau."
            )

        # Resolve model
        if not model:
            model = self._resolve_model(agent_id)
        if not provider:
            provider = KNOWN_AGENTS[agent_id].get("suggested_provider", DEFAULT_PROVIDER)

        status = WorkerStatus(
            agent_id=agent_id,
            status="running",
            pid=os.getpid(),  # Same process for now
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            last_heartbeat=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            provider=provider,
            model=model,
        )

        self._save_worker(status)
        return status

    def stop_worker(self, agent_id: str) -> WorkerStatus | None:
        """Arrête un worker par agent_id."""
        worker = self._find_worker_by_agent(agent_id)
        if not worker:
            return None

        worker.status = "stopped"
        self._save_worker(worker)
        return worker

    def get_status(self, agent_id: str) -> WorkerStatus | None:
        """Retourne le statut d'un worker pour un agent."""
        worker = self._find_worker_by_agent(agent_id)
        if not worker:
            return None

        # Update uptime
        if worker.started_at:
            try:
                start = time.mktime(time.strptime(worker.started_at, "%Y-%m-%dT%H:%M:%SZ"))
                worker.uptime_seconds = round(time.time() - start, 1)
            except (ValueError, OverflowError) as _exc:
                _log.debug("ValueError, OverflowError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        return worker

    def list_workers(self) -> WorkerList:
        """Liste tous les workers."""
        workers = []
        running_count = 0

        for f in sorted(self.workers_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ws = WorkerStatus.from_dict(data)
                if ws.status == "running":
                    if ws.is_alive:
                        running_count += 1
                    else:
                        ws.status = "error"
                        ws.error = "Process died"
                        self._save_worker(ws)
                workers.append(ws)
            except (json.JSONDecodeError, KeyError):
                continue

        return WorkerList(
            workers=workers,
            running_count=running_count,
            total_count=len(workers),
            max_parallel=self.max_parallel,
        )

    def execute_task(self, agent_id: str, task: dict) -> TaskResult:
        """
        Exécute une tâche pour un agent (simulation).

        En production, enverrait le prompt au LLM via le provider.
        Ici, crée un résultat simulé.
        """
        worker = self._find_worker_by_agent(agent_id)
        model_used = worker.model if worker else self._resolve_model(agent_id)

        start_time = time.monotonic()

        result = TaskResult(
            task_id=task.get("task_id", f"task-{uuid.uuid4().hex[:8]}"),
            agent_id=agent_id,
            status="success",
            output={
                "agent": agent_id,
                "model": model_used,
                "task": task.get("description", ""),
                "result": f"[Simulation] Agent {agent_id} a traité la tâche avec {model_used}",
            },
            model_used=model_used,
            duration_seconds=round(time.monotonic() - start_time, 3),
        )

        # Update worker stats
        if worker:
            worker.tasks_completed += 1
            worker.last_heartbeat = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save_worker(worker)

        return result

    def list_available_agents(self) -> list[dict]:
        """Liste les agents disponibles avec leurs capabilities."""
        agents = []
        for agent_id, info in sorted(KNOWN_AGENTS.items()):
            worker = self._find_worker_by_agent(agent_id)
            agents.append({
                "agent_id": agent_id,
                "title": info["title"],
                "persona": info["persona"],
                "capabilities": info["capabilities"],
                "suggested_model_tier": info["suggested_model_tier"],
                "worker_status": worker.status if worker else "stopped",
            })
        return agents


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_agent_worker(
    project_root: str,
    action: str = "list",
    agent_id: str = "",
    provider: str = "",
) -> dict:
    """
    MCP tool `bmad_agent_worker` — gère les workers d'agents.
    """
    root = Path(project_root).resolve()
    manager = AgentWorkerManager(root)

    if action == "list":
        return {"agents": manager.list_available_agents()}
    elif action == "start":
        if not agent_id:
            return {"error": "Le paramètre 'agent_id' est requis"}
        try:
            status = manager.start_worker(agent_id, provider=provider)
            return {"success": True, "worker": status.to_dict()}
        except (ValueError, RuntimeError) as e:
            return {"success": False, "error": str(e)}
    elif action == "stop":
        if not agent_id:
            return {"error": "Le paramètre 'agent_id' est requis"}
        worker = manager.stop_worker(agent_id)
        return {"success": worker is not None, "worker": worker.to_dict() if worker else None}
    elif action == "status":
        wl = manager.list_workers()
        return {
            "running": wl.running_count,
            "total": wl.total_count,
            "workers": [w.to_dict() for w in wl.workers],
        }
    else:
        return {"error": f"Action inconnue: {action}"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_workers(wl: WorkerList) -> None:
    print(f"\n  👷 Agent Workers ({wl.total_count})")
    print(f"  {'─' * 60}")
    print(f"  Actifs : {wl.running_count}/{wl.max_parallel}")
    print()

    if not wl.workers:
        print("  (aucun worker)\n")
        return

    for w in wl.workers:
        icon = {"running": "🟢", "stopped": "🔴", "error": "❌"}.get(w.status, "❓")
        tasks = f" [{w.tasks_completed} done]" if w.tasks_completed else ""
        print(f"    {icon} {w.worker_id} │ {w.agent_id:>10s} │ "
              f"{w.status:>8s} │ {w.model or '-':>20s}{tasks}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Worker — Workers isolés pour agents Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"agent-worker {AGENT_WORKER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # start
    start_p = sub.add_parser("start", help="Démarrer un worker")
    start_p.add_argument("--agent", required=True, help="Agent ID")
    start_p.add_argument("--provider", default="", choices=[""] + sorted(VALID_PROVIDERS),
                         help="LLM provider")
    start_p.add_argument("--model", default="", help="Modèle LLM spécifique")
    start_p.add_argument("--json", action="store_true", help="Output JSON")

    # stop
    stop_p = sub.add_parser("stop", help="Arrêter un worker")
    stop_p.add_argument("--agent", required=True, help="Agent ID")

    # status
    sub.add_parser("status", help="État des workers actifs")

    # list
    sub.add_parser("list", help="Lister les agents disponibles")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    manager = AgentWorkerManager(project_root)

    if args.command == "start":
        try:
            status = manager.start_worker(
                agent_id=args.agent,
                provider=args.provider,
                model=args.model,
            )
            if getattr(args, "json", False):
                print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"\n  🚀 Worker '{status.worker_id}' démarré pour {args.agent}")
                print(f"    Provider : {status.provider}")
                print(f"    Model    : {status.model}\n")
        except (ValueError, RuntimeError) as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "stop":
        worker = manager.stop_worker(args.agent)
        if worker:
            print(f"\n  🔴 Worker pour '{args.agent}' arrêté\n")
        else:
            print(f"  ❌ Aucun worker actif pour '{args.agent}'", file=sys.stderr)
            sys.exit(1)

    elif args.command == "status":
        wl = manager.list_workers()
        _print_workers(wl)

    elif args.command == "list":
        agents = manager.list_available_agents()
        print(f"\n  🤖 Agents disponibles ({len(agents)})")
        print(f"  {'─' * 60}")
        for a in agents:
            status_icon = "🟢" if a["worker_status"] == "running" else "⚪"
            caps = ", ".join(a["capabilities"][:3])
            print(f"    {status_icon} {a['agent_id']:>12s} │ {a['title']:>20s} │ {caps}")
        print()


if __name__ == "__main__":
    main()
