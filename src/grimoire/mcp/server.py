"""Grimoire MCP Server — expose Grimoire tools via Model Context Protocol.

Start with::

    python -m grimoire.mcp.server

Or configure in your MCP client (Claude Desktop, VS Code, etc.)::

    {
      "mcpServers": {
        "grimoire": {
          "command": "python",
          "args": ["-m", "grimoire.mcp.server"],
          "cwd": "/path/to/project"
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError

# Le SDK a renommé sa façade en 2.0 : `mcp.server.fastmcp.FastMCP` est devenu
# `mcp.server.mcpserver.MCPServer`. La surface qu'on utilise ici est identique
# des deux côtés — constructeur `name`/`instructions`, décorateur `.tool()`,
# `.run()` en stdio par défaut — donc un adaptateur suffit et évite d'enfermer
# les utilisateurs sous la 2.0. Vérifié contre 2.0.0, pas supposé.
_INSTRUCTIONS = (
    "Grimoire Kit — Composable AI agent platform. "
    "Use these tools to inspect and manage Grimoire projects."
)

if TYPE_CHECKING:
    from mcp.types import CallToolResult, ToolAnnotations

    # L'analyse statique ne s'attache à aucune des deux versions : elle décrit
    # la surface qu'on utilise, et rien d'autre. Un premier jet visait les
    # stubs de `FastMCP` ; la CI installe l'extra complet, donc désormais mcp
    # 2.x, où ce module n'existe plus — mypy retombait sur `Any` et détypait
    # les douze outils d'un coup. Ce protocole est exactement ce que le test
    # `test_surface_utilisee_toujours_presente` vérifie au runtime.
    _F = TypeVar("_F", bound=Callable[..., Any])

    class _ServerFacade(Protocol):
        """Ce que `FastMCP` (mcp 1.x) et `MCPServer` (mcp 2.x) ont en commun."""

        name: str

        def tool(self, *args: Any, **kwargs: Any) -> Callable[[_F], _F]: ...

        # Vérifiés fonctionnellement contre mcp 1.29.1 et mcp 2.x, pas supposés :
        # enregistrement dynamique avec `name=` explicite, `list_prompts()`,
        # `list_resources()`, `get_prompt()` et arguments déclarés se comportent
        # identiquement des deux côtés. Voir l'issue #176.
        def prompt(self, *args: Any, **kwargs: Any) -> Callable[[_F], _F]: ...

        def resource(self, *args: Any, **kwargs: Any) -> Callable[[_F], _F]: ...

        def run(self, *args: Any, **kwargs: Any) -> None: ...

    mcp: _ServerFacade
else:
    try:                                      # mcp >= 2
        from mcp.server.mcpserver import MCPServer as _Server
    except ImportError:
        try:                                  # mcp 1.x
            from mcp.server.fastmcp import FastMCP as _Server
        except ImportError as _exc:
            msg = "MCP SDK not installed. Run: pip install grimoire-kit[mcp]"
            raise ImportError(msg) from _exc

    # `mcp.types` n'a pas bougé entre les deux façades : c'est la couche
    # protocole, pas la couche serveur. Les annotations d'outil datent de la
    # révision 2025-03-26, `structuredContent` de 2025-06-18 — d'où la borne
    # basse du SDK dans pyproject.toml.
    from mcp.types import CallToolResult, ToolAnnotations

    mcp = _Server(name="grimoire", instructions=_INSTRUCTIONS)


def _find_config() -> GrimoireConfig:
    """Find and load the project config from cwd upward."""
    return GrimoireConfig.find_and_load()


# ── Annotations d'outil ───────────────────────────────────────────────────────
#
# MCP 2025-03-26 a introduit `readOnlyHint`, `destructiveHint`, `idempotentHint`
# et `openWorldHint`. Ce sont des *indices* : la spécification dit qu'un client
# ne doit pas s'y fier pour un serveur inconnu. Ils servent malgré tout à deux
# choses ici — un hôte qui distingue lecture et écriture peut auto-approuver la
# première, et un outil sans annotation ne peut plus se cacher : le test
# `test_chaque_outil_est_annote` échoue.
#
# `openWorldHint` vaut vrai quand l'outil peut sortir du projet : la mémoire
# adressée à un serveur distant (Weaviate, Qdrant, Ollama) et l'état des
# fournisseurs LLM sont les seuls cas.


# Les deux façades du SDK ne nomment pas ces champs pareil : `mcp` 1.x les
# déclare en camelCase, `mcp` 2.x en snake_case avec l'alias camelCase — le nom
# du fil reste celui de la spécification des deux côtés. Construire par
# mot-clé marchait sur l'un et échouait sous `mypy --strict` sur l'autre.
# `model_validate` prend les noms du fil dans les deux versions, et
# `annotation_hints` les relit dans les deux : c'est le seul point du module qui
# a besoin de le savoir. Vérifié contre 1.28.1 et 2.2.0, pas supposé.


def _annotations(**hints: bool) -> ToolAnnotations:
    """Construire une annotation par les noms du protocole, pas ceux du SDK."""
    return ToolAnnotations.model_validate(hints)


def annotation_hints(annotations: ToolAnnotations | None) -> dict[str, Any]:
    """Les indices d'une annotation, sous les noms du protocole.

    Sortie vide quand l'outil n'est pas annoté. Publique parce que la suite de
    tests s'en sert pour vérifier le contrat : la lire autrement reviendrait à
    tester la casse du SDK installé plutôt que ce que le client reçoit.
    """
    if annotations is None:
        return {}
    dumped: dict[str, Any] = annotations.model_dump(by_alias=True)
    return dumped


def _reads(*, open_world: bool = False) -> ToolAnnotations:
    """Un outil qui lit : sans effet de bord, rejouable."""
    return _annotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=open_world
    )


def _writes(*, destructive: bool, idempotent: bool, open_world: bool = False) -> ToolAnnotations:
    """Un outil qui écrit. `destructive` = l'effet n'est pas trivialement défait."""
    return _annotations(
        readOnlyHint=False,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _tool_error(payload: dict[str, Any]) -> str:
    """Render a frank tool failure: ``isError`` **plus** the JSON body.

    Le corps `{"error": ...}` que les appelants parsent déjà reste intact et
    devient le contenu du résultat ; le drapeau protocolaire s'ajoute par-dessus,
    jamais à la place (décision 3 du plan d'exécution). Sans lui, un client MCP
    lisait un `CallToolResult` réussi contenant le mot « error » : à la charge du
    modèle de s'en apercevoir.

    Le type de retour reste `str` parce que c'est le contrat des outils — leur
    `outputSchema` est `{"result": string}` ; `CallToolResult` est la forme
    protocolaire que FastMCP laisse traverser telle quelle (`convert_result`),
    à condition que `structuredContent` respecte ce schéma. Le `cast` dit
    exactement cela.
    """
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return cast(
        str,
        CallToolResult.model_validate({
            "content": [{"type": "text", "text": body}],
            "structuredContent": {"result": body},
            "isError": True,
        }),
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_reads())
def grimoire_project_context(project_path: str = ".") -> str:
    """Return the full project context (parsed project-context.yaml) as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    try:
        path = Path(project_path).resolve()
        config_file = path / "project-context.yaml"
        cfg = GrimoireConfig.from_yaml(config_file) if config_file.is_file() else GrimoireConfig.find_and_load(path)
        return json.dumps({
            "project": {
                "name": cfg.project.name,
                "type": cfg.project.type,
                "description": cfg.project.description,
                "stack": list(cfg.project.stack),
                "repos": [{"name": r.name, "path": r.path, "branch": r.default_branch} for r in cfg.project.repos],
            },
            "user": {
                "name": cfg.user.name,
                "language": cfg.user.language,
                "skill_level": cfg.user.skill_level,
            },
            "memory": {"backend": cfg.memory.backend},
            "agents": {
                "archetype": cfg.agents.archetype,
                "custom_agents": list(cfg.agents.custom_agents),
            },
            "grimoire_kit_version": __version__,
        }, indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_reads())
def grimoire_status(project_path: str = ".") -> str:
    """Return project health status as JSON — config validity, structure, agents.

    Args:
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    config_path = target / "project-context.yaml"
    check("config_exists", config_path.is_file())

    cfg = None
    if config_path.is_file():
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
            check("config_valid", True, f"project: {cfg.project.name}")
        except GrimoireConfigError as exc:
            check("config_valid", False, str(exc))

    for d in ("_grimoire", "_grimoire-output", "_grimoire/_memory"):
        check(f"dir_{d}", (target / d).is_dir())

    passed = sum(1 for c in checks if c["ok"])
    return json.dumps({
        "project_root": str(target),
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "healthy": passed == len(checks),
        "grimoire_kit_version": __version__,
    }, indent=2)


@mcp.tool(annotations=_reads())
def grimoire_agent_list(project_path: str = ".") -> str:
    """List all agents available in the project's archetype.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.registry.agents import AgentRegistry

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
    except GrimoireError as exc:
        return _tool_error({"error": str(exc)})

    # Find kit root (where archetypes/ lives)
    kit_root = _find_kit_root(target)
    if not kit_root:
        return _tool_error({"error": "Cannot find archetypes/ directory", "agents": []})

    registry = AgentRegistry(kit_root)
    archetype = cfg.agents.archetype
    try:
        dna = registry.get_dna(archetype)
        agents = [
            {
                "id": a.id,
                "path": str(a.path),
                "required": a.required,
                "exists": a.exists,
                "description": a.description,
            }
            for a in dna.agents
        ]
        return json.dumps({
            "archetype": archetype,
            "archetype_name": dna.name,
            "agents": agents,
            "total": len(agents),
        }, indent=2)
    except GrimoireError as exc:
        return _tool_error({"error": str(exc), "agents": []})


@mcp.tool(annotations=_reads())
def grimoire_harmony_check(project_path: str = ".") -> str:
    """Run architecture harmony check and return score + dissonances.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools.harmony_check import HarmonyCheck

    target = Path(project_path).resolve()
    hc = HarmonyCheck(target)
    result = hc.run()
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


@mcp.tool(annotations=_reads())
def grimoire_config(project_path: str = ".") -> str:
    """Return the raw parsed project-context.yaml as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools._common import load_yaml

    target = Path(project_path).resolve()
    config_file = target / "project-context.yaml"
    if not config_file.is_file():
        return _tool_error({"error": f"No project-context.yaml found at {target}"})
    try:
        raw = load_yaml(config_file)
        return json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_writes(destructive=True, idempotent=False, open_world=True))
def grimoire_memory_store(text: str, user_id: str = "", project_path: str = ".") -> str:
    """Store a memory entry in the project's configured memory backend.

    The write crosses the memory trust boundary first: content schema, size
    ceiling, refusal of instruction-shaped text, and redaction when the project
    policy requires it. A refusal comes back named, never as a silent success.

    Args:
        text: The text to remember.
        user_id: Optional user ID to scope the memory.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.memory.manager import MemoryManager
    from grimoire.memory.validation import MemoryWriteRefusedError

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg, project_root=target)
        entry = mgr.store(text, user_id=user_id, emitter="mcp")
        return json.dumps(entry.to_dict(), indent=2, ensure_ascii=False)
    except MemoryWriteRefusedError as exc:
        return _tool_error(exc.to_dict())
    except GrimoireError as exc:
        return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_reads(open_world=True))
def grimoire_memory_search(query: str, user_id: str = "", limit: int = 5, project_path: str = ".") -> str:
    """Search project memories by keyword or semantic similarity.

    Args:
        query: Search query text.
        user_id: Optional user ID to filter results.
        limit: Maximum number of results (default: 5).
        project_path: Path to project root (default: current directory).
    """
    from grimoire.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg, project_root=target)
        # Agents read through this tool. Fusing the vector and BM25 rankings
        # here is what makes a composed memory observable to them at all;
        # hybrid_search falls back to plain search when there is nothing to fuse.
        entries = mgr.hybrid_search(query, user_id=user_id, limit=limit)
        return json.dumps({
            "query": query,
            "results": [e.to_dict() for e in entries],
            "count": len(entries),
            "retrieval": "hybrid" if mgr.prefers_hybrid else "single",
        }, indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return _tool_error({"error": str(exc)})


_AGENT_ID_RE = re.compile(r"^[\w-]+$")


@mcp.tool(annotations=_writes(destructive=True, idempotent=False))
def grimoire_add_agent(agent_id: str, project_path: str = ".") -> str:
    """Create a custom agent as a real, installed file in the overrides tier.

    Renders the ``minimal`` archetype's ``custom-agent.tpl.md`` template into
    ``_grimoire/overrides/agents/<agent_id>.md`` — the tier that ``doctor`` and
    the routing map actually resolve (issue #349). Refuses when an agent with
    this id already exists in any tier, or when the id is not a safe filename.

    Args:
        agent_id: The agent identifier to create (used as tag and filename).
        project_path: Path to project root (default: current directory).
    """
    from grimoire.archetypes import bundled_path
    from grimoire.core import layout
    from grimoire.core.scaffold import _render_placeholders

    if not _AGENT_ID_RE.fullmatch(agent_id):
        return _tool_error({
            "error": f"Invalid agent_id {agent_id!r}: must match [\\w-]+ (safe as a filename)."
        })

    target = Path(project_path).resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        return _tool_error({"error": "No project-context.yaml found"})

    existing = layout.installed_agents(target)
    if agent_id in existing:
        _, existing_path = existing[agent_id]
        return _tool_error({
            "error": f"Agent '{agent_id}' already exists at {existing_path}",
            "agent_id": agent_id,
            "path": str(existing_path),
        })

    template_path = bundled_path() / "minimal" / "agents" / "custom-agent.tpl.md"
    if not template_path.is_file():
        return _tool_error({"error": f"Custom agent template not found at {template_path}"})

    agent_name = agent_id.replace("-", " ").replace("_", " ").title()
    variables = {
        "agent_tag": agent_id,
        "agent_name": agent_name,
        "agent_role": agent_name,
        "agent_icon": "sparkles",
    }
    rendered = _render_placeholders(template_path.read_text(encoding="utf-8"), variables)

    agents_dir = layout.overrides_dir(target) / layout.AGENTS_SUBDIR
    dest = agents_dir / f"{agent_id}.md"
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        return _tool_error({"error": str(exc)})

    return json.dumps({
        "status": "created",
        "agent_id": agent_id,
        "path": str(dest),
        "tier": "overrides",
    })


# ── Agentic standard ──────────────────────────────────────────────────────────

def _standard_checks_json(checks: tuple[Any, ...] | list[Any]) -> list[dict[str, str | None]]:
    """Serialize StandardCheck entries for JSON output."""
    return [
        {
            "id": check.id,
            "severity": check.severity,
            "message": check.message,
            "path": str(check.path) if check.path else None,
        }
        for check in checks
    ]


@mcp.tool(annotations=_reads())
def grimoire_standard_verify(project_path: str = ".", profile: str = "", task_id: str = "bootstrap") -> str:
    """Verify the project's governed agentic-standard artifacts (fail-closed).

    Args:
        project_path: Path to project root (default: current directory).
        profile: Expected profile (starter/controlled/orchestrated/governed/production).
            Empty string uses the generated manifest.
        task_id: Evidence task id to verify (default: "bootstrap").
    """
    from grimoire.core.agentic_standard import verify_standard_profile

    target = Path(project_path).resolve()
    try:
        result = verify_standard_profile(target, profile_id=profile or None, task_id=task_id)
        return json.dumps({
            "ok": result.ok,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "present": [str(p) for p in result.present],
            "missing": [str(p) for p in result.missing],
            "invalid_yaml": [str(p) for p in result.invalid_yaml],
            "warnings": result.warnings,
            "checks": _standard_checks_json(result.checks),
            "error_count": result.error_count,
            "warning_count": result.warning_count,
        }, indent=2, ensure_ascii=False)
    except (GrimoireError, ValueError, FileNotFoundError, OSError) as exc:
        return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_reads())
def grimoire_standard_audit(project_path: str = ".", profile: str = "", task_id: str = "bootstrap") -> str:
    """Audit governed standard artifacts and propose remediation actions.

    Args:
        project_path: Path to project root (default: current directory).
        profile: Expected profile. Empty string uses the generated manifest.
        task_id: Evidence task id to audit (default: "bootstrap").
    """
    from grimoire.core.agentic_standard import (
        propose_remediation_actions,
        verify_standard_profile,
    )

    target = Path(project_path).resolve()
    try:
        result = verify_standard_profile(target, profile_id=profile or None, task_id=task_id)
        actions = propose_remediation_actions(target, task_id=task_id, profile_id=profile or None)
        return json.dumps({
            "ok": result.ok,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "missing": [str(p) for p in result.missing],
            "invalid_yaml": [str(p) for p in result.invalid_yaml],
            "checks": _standard_checks_json(result.checks),
            "remediation_actions": [
                {
                    "check_id": action.check_id,
                    "severity": action.severity,
                    "action": action.action,
                    "path": str(action.path) if action.path else None,
                    "message": action.message,
                }
                for action in actions
            ],
        }, indent=2, ensure_ascii=False)
    except (GrimoireError, ValueError, FileNotFoundError, OSError) as exc:
        return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_writes(destructive=False, idempotent=True))
def grimoire_standard_score(project_path: str = ".", profile: str = "", task_id: str = "bootstrap") -> str:
    """Calculate and persist the standard compliance score (0-100 vs threshold).

    Args:
        project_path: Path to project root (default: current directory).
        profile: Expected profile. Empty string uses the generated manifest.
        task_id: Task id to score (default: "bootstrap").
    """
    from grimoire.core.agentic_standard import calculate_compliance_score

    target = Path(project_path).resolve()
    try:
        result = calculate_compliance_score(target, task_id=task_id, profile_id=profile or None)
        return json.dumps({
            "ok": result.ok,
            "profile": result.profile,
            "score": result.score,
            "threshold": result.threshold,
            "warnings": result.warnings,
            "errors": result.errors,
            "dimensions": result.dimensions,
            "output_path": str(result.output_path),
        }, indent=2, ensure_ascii=False)
    except (GrimoireError, ValueError, FileNotFoundError, OSError) as exc:
        return _tool_error({"error": str(exc)})


# Le passage d'une porte est journalisé : `check_evidence_gates` ajoute une
# ligne au journal d'événements du runtime. C'est voulu — une porte franchie
# sans trace ne prouve rien — donc ce n'est pas une lecture. Non idempotent :
# chaque appel ajoute une ligne.
@mcp.tool(annotations=_writes(destructive=False, idempotent=False))
def grimoire_standard_gate(
    project_path: str = ".",
    task_id: str = "bootstrap",
    target_state: str = "",
    profile: str = "",
) -> str:
    """Check standard evidence gates for a task (blocks transitions without proof).

    Args:
        project_path: Path to project root (default: current directory).
        task_id: Task id to evaluate (default: "bootstrap").
        target_state: Optional target lifecycle state. Empty string uses the board state.
        profile: Expected profile. Empty string uses the generated manifest.
    """
    from grimoire.core.agentic_standard import check_evidence_gates

    target = Path(project_path).resolve()
    try:
        result = check_evidence_gates(
            target,
            task_id=task_id,
            target_state=target_state or None,
            profile_id=profile or None,
        )
        return json.dumps({
            "ok": result.ok,
            "task_id": result.task_id,
            "profile": result.profile,
            "state": result.state,
            "missing": list(result.missing),
            "checks": _standard_checks_json(result.checks),
        }, indent=2, ensure_ascii=False)
    except (GrimoireError, ValueError, FileNotFoundError, OSError) as exc:
        return _tool_error({"error": str(exc)})


# ── Tâches (issue #138) ───────────────────────────────────────────────────────
#
# Un agent ne pouvait pas savoir qu'un board existait : le serveur exposait la
# configuration, la mémoire et le standard, aucune tâche. Ces cinq outils sont
# la surface que Beads et Vibe Kanban donnent à leurs agents — un outil, pas du
# texte dans un prompt. Ils appellent le même `TaskService` que `grimoire task`,
# donc le même gate de preuve : une transition que le CLI refuse, MCP la
# refuse, et le refus nomme la preuve manquante et le remède.

_TASK_ACTIONS = ("move", "block", "close")


def _task_service(project_path: str, ledger_root: str) -> Any:
    from grimoire.missions.service import TaskService

    return TaskService(Path(project_path).resolve(), Path(ledger_root))


def _task_json(task: Any) -> dict[str, Any]:
    from grimoire.missions.board import board_status_of
    from grimoire.missions.verifiability import as_dict as verifiability_as_dict

    data: dict[str, Any] = task.to_dict()
    data["board"] = board_status_of(task.status)
    data["verifiability"] = verifiability_as_dict(task)
    return data


def _task_error(exc: Exception) -> str:
    from grimoire.missions.service import TaskRefusedError

    if isinstance(exc, TaskRefusedError):
        # Un refus de gate n'est pas une panne d'outil : la porte a fonctionné,
        # et le corps nomme la preuve manquante. Le marquer `isError` dirait au
        # client que l'appel a échoué, alors qu'il a répondu.
        return json.dumps(exc.to_dict(), indent=2, ensure_ascii=False)
    return _tool_error({"error": str(exc)})


@mcp.tool(annotations=_reads())
def task_list_ready(
    project_path: str = ".", mission: str = "", ledger_root: str = "_grimoire-runtime-output/ledger"
) -> str:
    """List the tasks an agent can claim now (ledger state `ready`).

    Args:
        project_path: Path to project root (default: current directory).
        mission: Restrict to one mission id. Empty string lists every mission.
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    try:
        service = _task_service(project_path, ledger_root)
        if not service.has_ledger:
            return json.dumps({"tasks": [], "count": 0, "note": "no Mission Ledger yet — `task add` opens one"})
        tasks = service.list_ready(mission or None)
        return json.dumps({"tasks": [_task_json(t) for t in tasks], "count": len(tasks)}, indent=2, ensure_ascii=False)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


@mcp.tool(annotations=_reads())
def task_show(task_id: str, project_path: str = ".", ledger_root: str = "_grimoire-runtime-output/ledger") -> str:
    """Show one task: state, acceptance, claim, and what each next move will require.

    Args:
        task_id: Ledger task id (as listed by task_list_ready).
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    from grimoire.missions.board import board_status_of
    from grimoire.missions.gates import declared_transitions

    try:
        service = _task_service(project_path, ledger_root)
        task = service.require(task_id)
        here = board_status_of(task.status)
        requires = {
            to: list(entry.get("required_evidence", []) or [])
            for (src, to), entry in declared_transitions(service.project_root).items()
            if src == here
        }
        payload = _task_json(task)
        payload["next_moves_require"] = requires
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


@mcp.tool(annotations=_writes(destructive=True, idempotent=False))
def task_claim(
    task_id: str,
    actor: str = "mcp-agent",
    host: str = "mcp",
    project_path: str = ".",
    ledger_root: str = "_grimoire-runtime-output/ledger",
) -> str:
    """Claim a ready task (ready → claimed). Refused, with the missing proof named, when the gate is red.

    Args:
        task_id: Ledger task id to claim.
        actor: Who claims — the agent's name. Set GRIMOIRE_ACTOR to the same value so
            the session's activation hook resolves this claim as the current task.
        host: Runtime taking the task (default: "mcp").
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    try:
        move = _task_service(project_path, ledger_root).claim(task_id, actor, host)
        return json.dumps(move.to_dict(), indent=2, ensure_ascii=False)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


@mcp.tool(annotations=_writes(destructive=True, idempotent=False))
def task_update(
    task_id: str,
    action: str,
    to: str = "",
    reason: str = "",
    actor: str = "mcp-agent",
    project_path: str = ".",
    ledger_root: str = "_grimoire-runtime-output/ledger",
) -> str:
    """Move, block or close a task. Every transition passes the evidence gate: no proof, no move.

    Args:
        task_id: Ledger task id.
        action: "move" (needs `to`), "block" (needs `reason`), or "close".
        to: Target ledger state for "move" (ready, running, needs_verification, cancelled...).
        reason: Why — required for "block", optional otherwise.
        actor: Who acts (default: "mcp-agent").
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    from grimoire.missions.schemas import TaskState

    if action not in _TASK_ACTIONS:
        return _tool_error({"error": f"unknown action {action!r}", "actions": list(_TASK_ACTIONS)})
    if action == "move":
        try:
            target = TaskState(to)
        except ValueError:
            return _tool_error({"error": f"unknown state {to!r}", "states": [s.value for s in TaskState]})
    elif action == "block":
        if not reason.strip():
            return _tool_error({"error": "block requires a reason"})
        target = TaskState.BLOCKED
    else:
        target = TaskState.CLOSED
    try:
        move = _task_service(project_path, ledger_root).transition(task_id, target, actor, reason)
        return json.dumps(move.to_dict(), indent=2, ensure_ascii=False)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


# `task_context` n'est pas une lecture : `build_context_bundle` écrit
# `context-bundle.yaml` et ajoute une ligne au journal d'événements à chaque
# appel (agentic_standard.py). Il était annoté `_reads()` — une annotation qui
# ment est pire qu'une annotation absente, parce qu'un hôte auto-approuve sur sa
# foi. Idempotent : le bundle est recalculé au même contenu pour la même tâche.
@mcp.tool(annotations=_writes(destructive=False, idempotent=True))
def task_context(
    task_id: str = "", project_path: str = ".", ledger_root: str = "_grimoire-runtime-output/ledger"
) -> str:
    """Which task this session is on, and its context bundle.

    Args:
        task_id: Ledger task id. Empty string resolves the session's active task
            (GRIMOIRE_TASK_ID, then the ledger's active claim, then the board, then bootstrap).
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    from grimoire.core.standard_state import resolve_active_task

    root = Path(project_path).resolve()
    try:
        service = _task_service(project_path, ledger_root)
        if task_id:
            resolved, source = task_id, "argument"
        else:
            active = resolve_active_task(root)
            resolved, source = active.task_id, active.source
        payload: dict[str, Any] = {"task_id": resolved, "resolved_from": source}
        task = service.ledger.get_task(resolved) if service.has_ledger else None
        if task is not None:
            payload["task"] = _task_json(task)
            artifact = service.context(resolved)
            payload["context_bundle_path"] = str(artifact.path)
            payload["context_bundle"] = artifact.data
        else:
            payload["task"] = None
            payload["note"] = "no ledger task with this id — evidence still goes under this task_id"
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


@mcp.tool(annotations=_reads())
def task_recall(
    task_id: str = "", project_path: str = ".", ledger_root: str = "_grimoire-runtime-output/ledger"
) -> str:
    """What the project's memory knows about this task and its neighbours — the recall a claim surfaces.

    Own history of the task (past failed/blocked attempts), neighbouring tasks
    (explicit links, same mission, or close titles) with what stopped them, and
    anything the project's memory has consolidated on nearby subjects. Bounded
    in tokens. Same recall as `grimoire task recall` and the SessionStart hook.

    Args:
        task_id: Ledger task id. Empty string resolves the session's active task
            (GRIMOIRE_TASK_ID, then the ledger's active claim, then the board, then bootstrap).
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    from grimoire.core.standard_state import resolve_active_task

    root = Path(project_path).resolve()
    try:
        service = _task_service(project_path, ledger_root)
        resolved = task_id or resolve_active_task(root).task_id
        service.require(resolved)
        recall = service.recall(resolved)
        return json.dumps(recall.to_dict(), indent=2, ensure_ascii=False, default=str)
    except (GrimoireError, OSError, ValueError) as exc:
        return _task_error(exc)


# ── Host surfaces ─────────────────────────────────────────────────────────────
#
# A host that loads neither skill folders nor slash commands can still reach
# both over MCP: these three tools are how the surface stays available to a
# client the kit has no emitter for.


@mcp.tool(annotations=_reads())
def grimoire_host_status(project_path: str = ".") -> str:
    """Report, per host, what this project declares and what the host executes.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.hosts.capabilities import gaps_for, profile_for
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts

    target = Path(project_path).resolve()
    try:
        surface = build_surface(target)
    except (GrimoireError, OSError, ValueError) as exc:
        return _tool_error({"error": str(exc)})

    hosts = []
    for host_id in supported_hosts():
        emitter = emitter_for(host_id)
        if emitter is None:  # pragma: no cover - registry is complete
            continue
        plan = emitter.plan(surface, target)
        pending = apply_plan(plan, target, dry_run=True)
        profile = profile_for(host_id)
        hosts.append({
            "host": host_id.value,
            "display_name": profile.display_name,
            "in_sync": not pending.written and not pending.skipped,
            "pending": pending.written,
            "conflicts": pending.skipped,
            "degradations": [d.to_dict() for d in plan.degradations],
            "capability_gaps": [g.surface for g in gaps_for(profile)],
        })
    return json.dumps({"surface": surface.to_dict(), "hosts": hosts}, indent=2, ensure_ascii=False)


@mcp.tool(annotations=_reads(open_world=True))
def grimoire_providers_status(project_path: str = ".") -> str:
    """Report LLM provider availability per cost tier (issue #310, lot 2).

    Croisement du registre déclaratif (`llm-provider-registry.yaml`) et de
    l'état runtime (`_grimoire-output/providers-state.json`, refroidissement
    et dernier `grimoire providers audit`, issue #330) : quels fournisseurs
    sont activés, avec quels modèles par palier (cheap/mid/strong), lequel
    `choose()` retiendrait maintenant pour chaque palier, et ce que l'audit a
    vu (`available`, `probed_at`, `models_seen`, `probe_note`) — la même
    question que `grimoire providers status`, pour un client MCP qui n'a pas
    de terminal.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from datetime import UTC, datetime

    from grimoire.providers.registry import SUPPORTED_MODEL_TIERS, ProviderRegistryError, read_registry
    from grimoire.providers.routing import choose
    from grimoire.providers.state import load_state

    target = Path(project_path).resolve()
    try:
        providers = read_registry(target)
    except ProviderRegistryError as exc:
        return _tool_error({"error": str(exc)})

    now = datetime.now(UTC)
    state = load_state(target)

    def _provider_payload(provider: Any) -> dict[str, Any]:
        entry = state.get(provider.id)
        cooling_down = entry is not None and entry.is_cooling_down(now=now)
        return {
            "id": provider.id,
            "enabled": provider.enabled,
            "currency": provider.currency,
            "invocation": provider.invocation,
            "models": [{"id": model.id, "tier": model.tier} for model in provider.models],
            "cooling_down": cooling_down,
            "cooldown_until": entry.cooldown_until.isoformat() if entry and entry.cooldown_until else None,
            # Issue #330 : dernier résultat de `grimoire providers audit`.
            "available": entry.available if entry is not None else True,
            "probed_at": entry.probed_at if entry is not None else None,
            "models_seen": list(entry.models_seen) if entry is not None else [],
            "probe_note": entry.probe_note if entry is not None else None,
        }

    next_choice = {tier: choose(target, tier, now=now) for tier in SUPPORTED_MODEL_TIERS}
    return json.dumps(
        {
            "providers": [_provider_payload(provider) for provider in providers],
            "next_choice": {tier: (spec.id if spec is not None else None) for tier, spec in next_choice.items()},
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool(annotations=_reads())
def grimoire_skill(slug: str, project_path: str = ".") -> str:
    """Return a Grimoire skill body on demand, for hosts without native skills.

    Args:
        slug: Skill identifier, as listed by grimoire_host_status.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.hosts.collect import collect_skills

    target = Path(project_path).resolve()
    skills = collect_skills(target)
    for skill in skills:
        if skill.slug == slug:
            return json.dumps(
                {"slug": skill.slug, "description": skill.description, "body": skill.body},
                indent=2,
                ensure_ascii=False,
            )
    return _tool_error({"error": f"Unknown skill: {slug}", "available": [s.slug for s in skills]})


@mcp.tool(annotations=_reads())
def grimoire_command(slug: str, project_path: str = ".") -> str:
    """Return a Grimoire command body, for hosts without native slash commands.

    Args:
        slug: Command identifier, as listed by grimoire_host_status.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.hosts.collect import collect_commands

    target = Path(project_path).resolve()
    commands = collect_commands(target)
    for command in commands:
        if command.slug == slug:
            return json.dumps(
                {
                    "slug": command.slug,
                    "description": command.description,
                    "argument_hint": command.argument_hint,
                    "body": command.body,
                },
                indent=2,
                ensure_ascii=False,
            )
    return _tool_error({"error": f"Unknown command: {slug}", "available": [c.slug for c in commands]})


# ── Prompts and resources ─────────────────────────────────────────────────────
#
# The kit renders its commands and skills into per-host files: `.claude/commands`
# on Claude Code, `.github/prompts` on Copilot, a prose catalog everywhere else.
# MCP needs no emitter at all — a prompt is a slash command in *every* client,
# and a resource is a skill body any client can load on demand. This is the one
# surface that is genuinely host-independent, and the kit was exposing a third
# of it: fifteen tools, no prompt, no resource.
#
# Registration happens at import against the working directory, because that is
# what an MCP server is launched with. Failure is never fatal: a server that
# cannot read a project still serves its tools.

_SKILL_URI = "grimoire://skill/{slug}"


def _register_command(slug: str, description: str, body: str, argument_hint: str) -> None:
    """Expose one Grimoire command as an MCP prompt."""

    def handler(arguments: str = "") -> str:
        if not arguments:
            return body
        return f"{body}\n\nArgument fourni : {arguments}"

    handler.__name__ = slug.replace("-", "_")
    handler.__doc__ = description
    label = f"{description} — argument : {argument_hint}" if argument_hint else description
    mcp.prompt(name=slug, description=label)(handler)


def _register_skill(slug: str, description: str, body: str) -> None:
    """Expose one Grimoire skill as an MCP resource."""

    def handler() -> str:
        return body

    handler.__name__ = f"skill_{slug.replace('-', '_')}"
    handler.__doc__ = description
    mcp.resource(_SKILL_URI.format(slug=slug), name=slug, description=description)(handler)


def _register_surface(project_path: Path | None = None) -> tuple[int, int]:
    """Register the project's commands and skills; return how many of each.

    Never raises: an MCP server that cannot read a project is still a useful
    MCP server, and refusing to start over a missing directory would be a
    worse failure than serving fewer prompts.
    """
    try:
        from grimoire.hosts.collect import collect_commands, collect_skills

        root = (project_path or Path.cwd()).resolve()
        commands = collect_commands(root)
        skills = collect_skills(root)
    # Same reasoning: a server that cannot read a project is still a useful
    # MCP server, and refusing to start would be the worse failure.
    except Exception:
        return (0, 0)

    prompts = sum(
        _guarded(_register_command, c.slug, c.description, c.body, c.argument_hint) for c in commands
    )
    resources = sum(_guarded(_register_skill, k.slug, k.description, k.body) for k in skills)
    return (prompts, resources)


def _guarded(register: Callable[..., None], *args: Any) -> int:
    """Register one entry; a malformed one must not drop the rest of the surface."""
    try:
        register(*args)
    # Large on purpose: a malformed entry must not prevent the server from
    # starting, and there is no logger on this path.
    except Exception:
        return 0
    return 1


_register_surface()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_kit_root(start: Path) -> Path | None:
    """Walk up to find the directory containing archetypes/."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / "archetypes").is_dir():
            return parent
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server."""
    from grimoire.core.console_encoding import enable_utf8_output

    enable_utf8_output()
    mcp.run()


if __name__ == "__main__":
    main()
