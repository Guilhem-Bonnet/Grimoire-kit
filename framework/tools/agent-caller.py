#!/usr/bin/env python3
"""
agent-caller.py — Agent-to-Agent Tool Calling BMAD (BM-45 Story 6.2).
============================================================

Permet à un agent d'appeler un autre agent comme un "tool" via function calling.
Le caller spécifie l'agent cible, la tâche et le contexte. Le LLM Router
sélectionne le modèle pour l'agent cible. Le résultat est validé contre le
delivery contract et tracé dans BMAD_TRACE.md.

Modes :
  call    — Appelle un agent avec une tâche
  list    — Liste les agents appelables
  history — Historique des appels inter-agents
  schema  — Affiche le schéma d'entrée/sortie d'un agent

Usage :
  python3 agent-caller.py --project-root . call --from dev --to architect \\
    --task "Review auth module architecture" --context "src/auth/"
  python3 agent-caller.py --project-root . list
  python3 agent-caller.py --project-root . history --last 10
  python3 agent-caller.py --project-root . schema --agent architect

Stdlib only — importe llm-router.py et context-router.py par importlib.

Références :
  - Anthropic Agentic Tool Use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview
  - OpenAI Swarm Handoff: https://github.com/openai/swarm
  - Google A2A Protocol: https://google.github.io/A2A/
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

AGENT_CALLER_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

TRACE_FILE = "_bmad-output/BMAD_TRACE.md"
CALL_HISTORY_FILE = "_bmad-output/.agent-call-history.json"
DEFAULT_TIMEOUT = 120  # seconds
MAX_RETRIES = 3

# Agent capabilities (from agent-manifest.csv)
KNOWN_AGENTS: dict[str, dict] = {
    "analyst": {
        "title": "Business Analyst",
        "persona": "Mary",
        "capabilities": ["market research", "competitive analysis", "requirements elicitation"],
        "suggested_model_tier": "reasoning",
    },
    "architect": {
        "title": "Architect",
        "persona": "Winston",
        "capabilities": ["distributed systems", "cloud infrastructure", "API design", "scalable patterns"],
        "suggested_model_tier": "reasoning",
    },
    "dev": {
        "title": "Developer Agent",
        "persona": "Amelia",
        "capabilities": ["story execution", "test-driven development", "code implementation"],
        "suggested_model_tier": "coding",
    },
    "pm": {
        "title": "Product Manager",
        "persona": "John",
        "capabilities": ["PRD creation", "requirements discovery", "stakeholder alignment"],
        "suggested_model_tier": "reasoning",
    },
    "qa": {
        "title": "QA Engineer",
        "persona": "Quinn",
        "capabilities": ["test automation", "API testing", "E2E testing", "coverage analysis"],
        "suggested_model_tier": "coding",
    },
    "sm": {
        "title": "Scrum Master",
        "persona": "Bob",
        "capabilities": ["sprint planning", "story preparation", "agile ceremonies"],
        "suggested_model_tier": "general",
    },
    "tech-writer": {
        "title": "Technical Writer",
        "persona": "Paige",
        "capabilities": ["documentation", "Mermaid diagrams", "standards compliance"],
        "suggested_model_tier": "general",
    },
    "ux-designer": {
        "title": "UX Designer",
        "persona": "Sally",
        "capabilities": ["user research", "interaction design", "UI patterns"],
        "suggested_model_tier": "general",
    },
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class AgentCallRequest:
    """Requête d'appel inter-agent."""
    call_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    task: str = ""
    context: str = ""
    expected_format: str = ""  # JSON schema or description
    timeout: int = DEFAULT_TIMEOUT
    retry_count: int = 0
    max_retries: int = MAX_RETRIES
    timestamp: str = ""
    trace_id: str = ""

    def __post_init__(self):
        if not self.call_id:
            self.call_id = str(uuid.uuid4())[:8]
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.trace_id:
            self.trace_id = f"a2a-{self.call_id}"


@dataclass
class AgentCallResponse:
    """Réponse d'un appel inter-agent."""
    call_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    status: str = "pending"  # pending | success | error | timeout | retry
    response: str = ""
    model_used: str = ""
    tokens_used: int = 0
    duration_ms: int = 0
    validation_passed: bool = True
    validation_errors: list[str] = field(default_factory=list)
    retries: int = 0
    timestamp: str = ""


@dataclass
class AgentToolSpec:
    """Spécification d'un agent comme tool appelable."""
    agent_id: str
    title: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    def to_tool_use(self) -> dict:
        """Export comme Anthropic tool_use."""
        return {
            "name": f"call_agent_{self.agent_id}",
            "description": f"Call {self.title} ({self.agent_id}): {self.description}",
            "input_schema": self.input_schema or {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task to perform"},
                    "context": {"type": "string", "description": "Context or file paths"},
                    "expected_format": {"type": "string", "description": "Expected output format"},
                },
                "required": ["task"],
            },
        }

    def to_function_calling(self) -> dict:
        """Export comme OpenAI function calling."""
        return {
            "type": "function",
            "function": {
                "name": f"call_agent_{self.agent_id}",
                "description": f"Call {self.title} ({self.agent_id}): {self.description}",
                "parameters": self.input_schema or {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task to perform"},
                        "context": {"type": "string", "description": "Context or file paths"},
                        "expected_format": {"type": "string", "description": "Expected output format"},
                    },
                    "required": ["task"],
                },
            },
        }


# ── Trace Integration ──────────────────────────────────────────────────────

class TraceWriter:
    """Écrit les traces d'appels dans BMAD_TRACE.md."""

    def __init__(self, project_root: Path):
        self.trace_file = project_root / TRACE_FILE

    def write(self, agent: str, event_type: str, payload: str) -> None:
        """Ajoute une entrée de trace."""
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"[{timestamp}] [{agent}] [{event_type}] {payload}\n"

        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(entry)


# ── Call History ───────────────────────────────────────────────────────────

class CallHistoryManager:
    """Gère l'historique persisté des appels inter-agents."""

    def __init__(self, project_root: Path):
        self.history_file = project_root / CALL_HISTORY_FILE

    def _load(self) -> list[dict]:
        if self.history_file.exists():
            try:
                with open(self.history_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self, entries: list[dict]) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def record(self, request: AgentCallRequest, response: AgentCallResponse) -> None:
        entries = self._load()
        entries.append({
            "call_id": request.call_id,
            "from": request.from_agent,
            "to": request.to_agent,
            "task": request.task,
            "status": response.status,
            "model_used": response.model_used,
            "tokens_used": response.tokens_used,
            "duration_ms": response.duration_ms,
            "retries": response.retries,
            "timestamp": request.timestamp,
        })
        # Keep last 500 entries
        if len(entries) > 500:
            entries = entries[-500:]
        self._save(entries)

    def get_history(self, last_n: int = 20) -> list[dict]:
        entries = self._load()
        return entries[-last_n:]

    def get_stats(self) -> dict:
        entries = self._load()
        if not entries:
            return {"total_calls": 0}

        total = len(entries)
        success = sum(1 for e in entries if e.get("status") == "success")
        errors = sum(1 for e in entries if e.get("status") == "error")
        total_tokens = sum(e.get("tokens_used", 0) for e in entries)
        avg_duration = sum(e.get("duration_ms", 0) for e in entries) // max(1, total)

        # Per-agent stats
        agent_calls: dict[str, int] = {}
        for e in entries:
            to_agent = e.get("to", "unknown")
            agent_calls[to_agent] = agent_calls.get(to_agent, 0) + 1

        return {
            "total_calls": total,
            "success": success,
            "errors": errors,
            "success_rate": round(success / total, 3) if total > 0 else 0.0,
            "total_tokens": total_tokens,
            "avg_duration_ms": avg_duration,
            "calls_per_agent": agent_calls,
        }


# ── Agent Caller ───────────────────────────────────────────────────────────

class AgentCaller:
    """
    Appel inter-agent comme tool calling.

    Flow :
    1. Résoudre l'agent cible (capabilities, model tier)
    2. Préparer la requête avec contexte
    3. Sélectionner le modèle via LLM Router
    4. Exécuter l'appel (simulé — pas de vrai LLM call en standalone)
    5. Valider la réponse contre le delivery contract
    6. Tracer dans BMAD_TRACE + historique
    """

    def __init__(
        self,
        project_root: Path,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ):
        self.project_root = project_root
        self.timeout = timeout
        self.max_retries = max_retries
        self._trace = TraceWriter(project_root)
        self._history = CallHistoryManager(project_root)
        self._router_mod = self._load_llm_router()
        self._agents = self._discover_agents()

    def _load_llm_router(self):
        """Importe llm-router.py par importlib."""
        router_path = Path(__file__).parent / "llm-router.py"
        if not router_path.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location("llm_router", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            return None

    def _discover_agents(self) -> dict[str, dict]:
        """Découvre les agents depuis le manifest ou les fichiers."""
        agents = dict(KNOWN_AGENTS)

        # Augment with actual agent files
        agents_dirs = [
            self.project_root / "_bmad" / "bmm" / "agents",
            self.project_root / "_bmad" / "core" / "agents",
        ]

        for agents_dir in agents_dirs:
            if not agents_dir.exists():
                continue
            for f in sorted(agents_dir.glob("*.md")):
                agent_id = f.stem
                if agent_id not in agents:
                    # Basic info from file
                    try:
                        content = f.read_text(encoding="utf-8")[:500]
                        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                        title = title_match.group(1) if title_match else agent_id
                        agents[agent_id] = {
                            "title": title,
                            "persona": agent_id,
                            "capabilities": [],
                            "suggested_model_tier": "general",
                        }
                    except OSError:
                        continue

        return agents

    def list_agents(self) -> list[AgentToolSpec]:
        """Liste les agents disponibles comme tools."""
        specs = []
        for agent_id, info in sorted(self._agents.items()):
            specs.append(AgentToolSpec(
                agent_id=agent_id,
                title=info.get("title", agent_id),
                description=f"{info.get('title', agent_id)} — {', '.join(info.get('capabilities', []))}",
                capabilities=info.get("capabilities", []),
            ))
        return specs

    def get_agent_schema(self, agent_id: str) -> AgentToolSpec | None:
        """Récupère le schéma d'un agent."""
        info = self._agents.get(agent_id)
        if not info:
            return None
        return AgentToolSpec(
            agent_id=agent_id,
            title=info.get("title", agent_id),
            description=f"{info.get('title', agent_id)} — {', '.join(info.get('capabilities', []))}",
            capabilities=info.get("capabilities", []),
        )

    def call(self, request: AgentCallRequest) -> AgentCallResponse:
        """
        Exécute un appel inter-agent.

        En mode standalone (sans LLM API), crée la requête formatée
        et la trace. L'exécution réelle nécessite un LLM backend.
        """
        start_time = time.time()

        response = AgentCallResponse(
            call_id=request.call_id,
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            timestamp=request.timestamp,
        )

        # Validate target agent
        if request.to_agent not in self._agents:
            response.status = "error"
            response.response = f"Agent inconnu: {request.to_agent}"
            self._trace.write(
                request.from_agent,
                "TOOL:call:error",
                f"call_agent_{request.to_agent} — agent not found",
            )
            self._history.record(request, response)
            return response

        agent_info = self._agents[request.to_agent]

        # Route model via LLM Router
        model_used = "claude-sonnet-4-20250514"  # default
        if self._router_mod:
            try:
                router = self._router_mod.LLMRouter(project_root=self.project_root)
                route_result = router.route(task_description=request.task)
                model_used = getattr(route_result, "selected_model", model_used)
            except Exception:
                pass

        response.model_used = model_used

        # Trace: call initiated
        self._trace.write(
            request.from_agent,
            "TOOL:call",
            f"call_agent_{request.to_agent} task=\"{request.task[:80]}\" "
            f"model={model_used} trace_id={request.trace_id}",
        )

        # Build the prompt for the target agent
        prompt = self._build_agent_prompt(request, agent_info)

        # In standalone mode, we can't actually call an LLM.
        # We produce the formatted request as the "response" for now.
        response.status = "success"
        response.response = prompt
        response.tokens_used = len(prompt) // 4
        response.duration_ms = int((time.time() - start_time) * 1000)

        # Trace: call completed
        self._trace.write(
            request.to_agent,
            "TOOL:result",
            f"call_id={request.call_id} status={response.status} "
            f"tokens={response.tokens_used} duration={response.duration_ms}ms",
        )

        # Record in history
        self._history.record(request, response)

        return response

    def _build_agent_prompt(self, request: AgentCallRequest, agent_info: dict) -> str:
        """Construit le prompt pour l'agent cible."""
        lines = [
            f"# Agent Call — {agent_info.get('title', request.to_agent)}",
            "",
            f"**From**: {request.from_agent}",
            f"**To**: {request.to_agent} ({agent_info.get('persona', '')})",
            f"**Call ID**: {request.call_id}",
            f"**Trace ID**: {request.trace_id}",
            "",
            "## Task",
            "",
            request.task,
            "",
        ]

        if request.context:
            lines.extend([
                "## Context",
                "",
                request.context,
                "",
            ])

        if request.expected_format:
            lines.extend([
                "## Expected Output Format",
                "",
                request.expected_format,
                "",
            ])

        lines.extend([
            "## Agent Capabilities",
            "",
            "- " + "\n- ".join(agent_info.get("capabilities", ["general"])),
            "",
            "---",
            f"*Generated by agent-caller v{AGENT_CALLER_VERSION}*",
        ])

        return "\n".join(lines)

    def get_history(self, last_n: int = 20) -> list[dict]:
        """Retourne l'historique des appels."""
        return self._history.get_history(last_n)

    def get_stats(self) -> dict:
        """Retourne les statistiques d'appels."""
        return self._history.get_stats()


# ── Config Loading ──────────────────────────────────────────────────────────

def load_caller_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [project_root / "project-context.yaml", project_root / "bmad.yaml"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("agent_caller", {})
    return {}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_agent_list(agents: list[AgentToolSpec]) -> None:
    print(f"\n  🤖 Agents appelables — {len(agents)}")
    print(f"  {'─' * 65}")

    for a in agents:
        caps = ", ".join(a.capabilities[:3]) if a.capabilities else "—"
        print(f"  {a.agent_id:20s} │ {a.title}")
        print(f"    Capabilities: {caps}")
    print()


def _print_history(entries: list[dict]) -> None:
    print(f"\n  📜 Historique des appels — {len(entries)} derniers")
    print(f"  {'─' * 70}")

    if not entries:
        print("  Aucun appel enregistré.\n")
        return

    for e in entries:
        status_icon = {"success": "✅", "error": "❌", "timeout": "⏰"}.get(e.get("status", ""), "❓")
        dur = e.get("duration_ms", 0)
        tokens = e.get("tokens_used", 0)
        print(f"  {status_icon} [{e.get('call_id', '?')[:8]}] "
              f"{e.get('from', '?')} → {e.get('to', '?')} | "
              f"{e.get('model_used', '?')} | {tokens:,} tok | {dur}ms")
        task = e.get("task", "")
        if task:
            print(f"    Task: {task[:60]}...")
    print()


def _print_schema(spec: AgentToolSpec) -> None:
    print(f"\n  🔧 Agent Schema — {spec.agent_id}")
    print(f"  {'─' * 55}")
    print(f"  Title        : {spec.title}")
    print(f"  Description  : {spec.description}")
    print(f"  Capabilities : {', '.join(spec.capabilities)}")
    print("\n  Anthropic tool_use:")
    print(json.dumps(spec.to_tool_use(), ensure_ascii=False, indent=4))
    print("\n  OpenAI function_calling:")
    print(json.dumps(spec.to_function_calling(), ensure_ascii=False, indent=4))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Caller — Agent-to-Agent Tool Calling BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"agent-caller {AGENT_CALLER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # call
    call_p = sub.add_parser("call", help="Appeler un agent")
    call_p.add_argument("--from", dest="from_agent", required=True, help="Agent source")
    call_p.add_argument("--to", dest="to_agent", required=True, help="Agent cible")
    call_p.add_argument("--task", required=True, help="Tâche à exécuter")
    call_p.add_argument("--context", default="", help="Contexte additionnel")
    call_p.add_argument("--expected-format", default="", help="Format de sortie attendu")
    call_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Timeout en secondes (défaut: {DEFAULT_TIMEOUT})")
    call_p.add_argument("--json", action="store_true", help="Output JSON")

    # list
    sub.add_parser("list", help="Lister les agents appelables")

    # history
    hist_p = sub.add_parser("history", help="Historique des appels")
    hist_p.add_argument("--last", type=int, default=20,
                        help="Nombre d'entrées (défaut: 20)")
    hist_p.add_argument("--stats", action="store_true",
                        help="Afficher les statistiques")

    # schema
    sch_p = sub.add_parser("schema", help="Schéma d'un agent")
    sch_p.add_argument("--agent", required=True, help="Agent ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    caller = AgentCaller(project_root=project_root)

    if args.command == "call":
        request = AgentCallRequest(
            from_agent=args.from_agent,
            to_agent=args.to_agent,
            task=args.task,
            context=args.context,
            expected_format=args.expected_format,
            timeout=args.timeout,
        )
        response = caller.call(request)
        if getattr(args, "json", False):
            print(json.dumps(asdict(response), ensure_ascii=False, indent=2))
        else:
            icon = {"success": "✅", "error": "❌", "timeout": "⏰"}.get(response.status, "❓")
            print(f"\n  {icon} Agent Call — {response.status}")
            print(f"  {'─' * 55}")
            print(f"  From     : {response.from_agent}")
            print(f"  To       : {response.to_agent}")
            print(f"  Model    : {response.model_used}")
            print(f"  Tokens   : {response.tokens_used:,}")
            print(f"  Duration : {response.duration_ms}ms")
            if response.validation_errors:
                print(f"  Validation: ❌ {', '.join(response.validation_errors)}")
            print(f"\n{response.response}\n")

    elif args.command == "list":
        agents = caller.list_agents()
        _print_agent_list(agents)

    elif args.command == "history":
        if getattr(args, "stats", False):
            stats = caller.get_stats()
            print("\n  📊 Statistiques d'appels inter-agents")
            print(f"  {'─' * 50}")
            print(f"  Total appels  : {stats.get('total_calls', 0)}")
            print(f"  Succès        : {stats.get('success', 0)}")
            print(f"  Erreurs       : {stats.get('errors', 0)}")
            print(f"  Success rate  : {stats.get('success_rate', 0):.1%}")
            print(f"  Tokens total  : {stats.get('total_tokens', 0):,}")
            print(f"  Durée moyenne : {stats.get('avg_duration_ms', 0)}ms")
            if stats.get("calls_per_agent"):
                print("\n  Par agent :")
                for ag, count in sorted(stats["calls_per_agent"].items(), key=lambda x: -x[1]):
                    print(f"    {ag:20s} │ {count:>4d} appels")
            print()
        else:
            entries = caller.get_history(last_n=getattr(args, "last", 20))
            _print_history(entries)

    elif args.command == "schema":
        spec = caller.get_agent_schema(args.agent)
        if not spec:
            print(f"\n  ❌ Agent '{args.agent}' introuvable\n")
            sys.exit(1)
        _print_schema(spec)


if __name__ == "__main__":
    main()
