"""Isolation de l'hôte pour chaque ``claude -p`` du banc (B-08, lot M) : rien n'est hérité de l'opérateur.

Le tamis du lot M a été arrêté le 2026-10-20 (``docs/bench-three-arms.md`` §17.8) : les dépôts de tâche vivaient sous
l'arbre de la Forge, Claude Code remontait l'arborescence et chargeait son ``.mcp.json`` (``grimoire``, ``playwright``,
``context7``), les connecteurs claude.ai du compte copié, ses skills et ses agents. Le bras ``nu`` n'était pas nu. Ce
module ferme ces voies, une par une :

- **Serveurs MCP** : :func:`mcp_isolation_args` (``--strict-mcp-config --mcp-config <json>``). Le bras ``nu`` n'en a
  aucun ; un autre bras ne déclare que le ``.mcp.json`` que son installation a écrit dans le dépôt de tâche
  (:func:`declared_mcp_servers`), jamais ceux de l'opérateur. Les connecteurs claude.ai sont coupés par
  ``ENABLE_CLAUDEAI_MCP_SERVERS=false``.
- **Environnement** : :func:`without_operator_session` retire ce qu'une session Claude Code parente, VS Code ou
  l'opérateur laissent dans l'environnement (jeton de messagerie entre sessions, assistant d'identifiants GitHub par
  ``GIT_CONFIG_*``, mots de passe, agent SSH).
- **Workspace** : :func:`refuse_project_ancestors` refuse un workspace dont lui-même ou un ancêtre porte un
  ``.mcp.json``, un ``CLAUDE.md``, un ``AGENTS.md`` ou un ``.claude/`` (le ``HOME`` réel de l'opérateur en a un) :
  Claude Code les chargerait comme instructions ou réglages de projet.
- **Contrôle avant dépense** : :func:`run_control` lance le run le plus court possible, compté au journal de budget,
  et :func:`check_control` lit son événement ``init`` (aucun serveur MCP hors de ceux déclarés, mémoire automatique
  sous le ``HOME`` isolé) et le journal d'un hook ``InstructionsLoaded`` (aucun fichier d'instructions hors du dépôt
  de contrôle ; le ``CLAUDE.md`` témoin du dépôt doit y figurer, sinon le hook est muet et rien n'est prouvé).
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: Workspace par défaut, hors de l'arbre de tout projet (aucun marqueur dans ses ancêtres, vérifié le 2026-10-20).
#: ``GRIMOIRE_BENCH_WORKSPACE`` ou ``--workspace`` le remplacent ; :func:`refuse_project_ancestors` juge le résultat.
DEFAULT_WORKSPACE = Path("/mnt/Travail/Projets/Dev/_bench-grimoire")

#: Fichiers et dossiers que Claude Code lit en remontant l'arborescence depuis le dépôt de tâche.
PROJECT_MARKERS: tuple[str, ...] = (".mcp.json", "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude")

#: Option qui lève le refus, documentée au §3 de ``docs/bench-three-arms.md``.
ALLOW_OPTION = "--allow-project-ancestors"

_DROP_EXACT = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
        "CLAUDE_AGENT_SDK_VERSION",
        "CLAUDE_CONFIG_DIR",
        "MCP_CONNECTION_NONBLOCKING",
        "SSH_AUTH_SOCK",
        "SSH_ASKPASS",
        "GIT_ASKPASS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
    }
)
_DROP_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_OPS_", "VSCODE_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GH_", "GITHUB_")
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|_KEY$|_KEY_", re.IGNORECASE)
#: Seule clé gardée : le mode ``api-key`` du banc l'exige (§3.1), et ``--bare`` ne lit rien d'autre.
_KEEP = frozenset({"ANTHROPIC_API_KEY"})

CONTROL_PROMPT = "Contrôle d'isolation du banc. Réponds seulement « ok », sans utiliser aucun outil."
CONTROL_CANARY = (
    "# Témoin du contrôle d'isolation du banc\n\nCe fichier prouve que le hook InstructionsLoaded est actif.\n"
)
#: Plafond d'un run de contrôle (une réponse d'un mot) ; le reste du plafond de campagne s'applique s'il est plus bas.
CONTROL_MAX_USD = 0.5
CONTROL_TIMEOUT_S = 300


class IsolationError(RuntimeError):
    """Le banc ne part pas : workspace sous un projet, ou contrôle d'isolation non concluant."""


def without_operator_session(env: Mapping[str, str]) -> dict[str, str]:
    """Copie de *env* sans les variables d'une session parente ni les secrets de l'opérateur ; claude.ai MCP coupé."""
    out = {
        name: value
        for name, value in env.items()
        if name in _KEEP or not (name in _DROP_EXACT or name.startswith(_DROP_PREFIXES) or _SECRET_NAME.search(name))
    }
    out["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    return out


def declared_mcp_servers(arm: str | None, task_dir: Path) -> dict[str, Any]:
    """Serveurs MCP qu'un bras déclare : aucun pour ``nu`` ; sinon le ``.mcp.json`` installé dans le dépôt de tâche."""
    if arm in (None, "nu"):
        return {}
    path = task_dir / ".mcp.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return dict(servers) if isinstance(servers, dict) else {}


def mcp_isolation_args(servers: Mapping[str, Any] | None = None) -> list[str]:
    """``--strict-mcp-config --mcp-config <json>`` : seuls les serveurs déclarés, rien de l'opérateur ni des parents."""
    config = json.dumps({"mcpServers": dict(servers or {})}, sort_keys=True, separators=(",", ":"))
    return ["--strict-mcp-config", "--mcp-config", config]


def project_markers(workspace: Path) -> list[Path]:
    """Marqueurs de projet présents dans *workspace* ou l'un de ses ancêtres (chemins résolus)."""
    root = workspace.resolve()
    return [d / name for d in (root, *root.parents) for name in PROJECT_MARKERS if (d / name).exists()]


def refuse_project_ancestors(workspace: Path, *, allow: bool = False) -> list[Path]:
    """Lève :class:`IsolationError` si un marqueur est trouvé, sauf *allow* (``--allow-project-ancestors``)."""
    found = project_markers(workspace)
    if found and not allow:
        raise IsolationError(
            f"workspace {workspace} refusé : Claude Code chargerait {', '.join(str(p) for p in found)} "
            f"(instructions, réglages ou serveurs MCP d'un projet parent). Choisis un workspace hors de tout projet "
            f"(défaut : {DEFAULT_WORKSPACE}) ou passe {ALLOW_OPTION} en connaissance de cause "
            "(docs/bench-three-arms.md §3)."
        )
    return found


def control_settings(instructions_log: Path) -> str:
    """Réglages ``--settings`` du run de contrôle : un hook ``InstructionsLoaded`` qui journalise chaque fichier chargé."""
    command = f"{{ cat; echo; }} >> {shlex.quote(str(instructions_log))}"
    return json.dumps({"hooks": {"InstructionsLoaded": [{"hooks": [{"type": "command", "command": command}]}]}})


def _init_event(lines: Iterable[str]) -> dict[str, Any] | None:
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def loaded_instruction_files(instructions_log: Path) -> list[str]:
    """``file_path`` de chaque événement ``InstructionsLoaded`` journalisé (vide si le hook n'a rien écrit)."""
    if not instructions_log.is_file():
        return []
    files: list[str] = []
    for line in instructions_log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict) and isinstance(event.get("file_path"), str):
            files.append(event["file_path"])
    return files


def check_control(
    lines: Sequence[str],
    *,
    repo: Path,
    home: Path,
    instructions_log: Path,
    expected_mcp: Iterable[str] = (),
) -> tuple[list[str], dict[str, Any]]:
    """Problèmes d'isolation lus dans la transcription du run de contrôle (vide : isolé) et constat brut."""
    problems: list[str] = []
    init = _init_event(lines)
    loaded = loaded_instruction_files(instructions_log)
    facts: dict[str, Any] = {"instructions_loaded": loaded}
    if init is None:
        return ["aucun événement init dans la transcription du contrôle"], facts
    servers = [s.get("name") for s in init.get("mcp_servers") or [] if isinstance(s, dict)]
    mcp_tools = sorted(t for t in init.get("tools") or [] if isinstance(t, str) and t.startswith("mcp__"))
    memory = {k: v for k, v in (init.get("memory_paths") or {}).items() if isinstance(v, str)}
    facts.update(mcp_servers=servers, mcp_tools=mcp_tools, memory_paths=memory, cwd=init.get("cwd"))
    unexpected = sorted(set(map(str, servers)) - set(expected_mcp))
    if unexpected:
        problems.append(f"serveurs MCP non déclarés dans l'init : {', '.join(unexpected)}")
    if mcp_tools and not set(expected_mcp):
        problems.append(f"outils MCP exposés sans serveur déclaré : {', '.join(mcp_tools[:10])}")
    problems += [f"mémoire {k} hors du HOME isolé : {v}" for k, v in memory.items() if not _inside(Path(v), home)]
    if not any(_inside(Path(f), repo) and Path(f).name == "CLAUDE.md" for f in loaded):
        problems.append("hook InstructionsLoaded muet : le CLAUDE.md témoin du dépôt de contrôle n'est pas journalisé")
    problems += [f"instructions chargées hors du dépôt de contrôle : {f}" for f in loaded if not _inside(Path(f), repo)]
    return problems, facts


Runner = Callable[..., Any]


def run_control(
    arm: str,
    *,
    root: Path,
    home: Path,
    env: dict[str, str],
    guard: Any,
    runner: Runner,
    provision: Callable[[Path], Any],
    auth_mode: str,
    model: str | None,
    budget_args: Callable[[float | None], list[str]],
) -> dict[str, Any]:
    """Run de contrôle d'un bras, compté au journal ; rend le constat (``ok`` faux : la campagne ne part pas)."""
    repo, log, instructions = root / "repo", root / "control.stream.jsonl", root / "instructions-loaded.jsonl"
    shutil.rmtree(root, ignore_errors=True)
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "CLAUDE.md").write_text(CONTROL_CANARY, encoding="utf-8")
    call_id = f"isolation-control/{arm}"
    guard.begin(call_id)
    cap = min(CONTROL_MAX_USD, guard.remaining_usd())
    extra = [*budget_args(cap), "--settings", control_settings(instructions)]
    with provision(home) as creds:
        if creds is None and auth_mode != "api-key":
            guard.record(call_id, 0.0, True)
            raise IsolationError("aucun identifiant Claude Code pour le run de contrôle")
        outcome = runner(
            repo, CONTROL_PROMPT, home=home, env=env, timeout_s=CONTROL_TIMEOUT_S, auth_mode=auth_mode,
            model=model, arm=arm, extra_args=extra, log_path=log,
        )  # fmt: skip
    guard.record(call_id, outcome.total_cost_usd, bool(outcome.cost_known))
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.is_file() else []
    problems, facts = check_control(lines, repo=repo, home=home, instructions_log=instructions)
    return {
        "arm": arm,
        "ok": not problems,
        "problems": problems,
        "cost_usd": outcome.total_cost_usd,
        "cost_known": bool(outcome.cost_known),
        "terminated_reason": outcome.terminated_reason,
        **facts,
    }


def verify_arms(
    arms: Sequence[str],
    *,
    root: Path,
    homes: Mapping[str, Path],
    env_for: Callable[[str, Path], dict[str, str]],
    **run_kw: Any,
) -> list[dict[str, Any]]:
    """Un run de contrôle par bras (:func:`run_control`), sous ``root/<bras>``, dans l'environnement réel d'un run."""
    return [
        run_control(arm, root=root / arm, home=homes[arm], env=env_for(arm, root / arm / "repo"), **run_kw)
        for arm in arms
    ]
