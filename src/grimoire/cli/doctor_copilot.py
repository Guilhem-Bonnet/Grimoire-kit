"""Contrôles Copilot de ``grimoire doctor`` (épique #1280, lot P1-5).

Trois constats, tous tirés des fichiers du projet et de la documentation GitHub
(https://docs.github.com/en/copilot/reference/hooks-reference) :

- ``copilot_hook_format`` : les fichiers ``.github/hooks/grimoire-*.json`` ont-ils la
  forme que l'émetteur courant écrit (``version: 1``, ``matcher`` sur la garde,
  lanceurs ``bash``/``powershell``, délai ``timeoutSec``) ? Un fichier d'une
  version antérieure est à régénérer par ``grimoire up``. Le délai de la garde
  n'est pas contrôlé ici : ``hook_timeout_copilot`` s'en charge.
- ``copilot_hooks_doubled`` : Claude Code et Copilot sont tous deux câblés.
- ``copilot_claude_hooks_only`` : Copilot est activé ou détecté, mais seuls les hooks
  Claude Code (``.claude/settings.json``) lancent le kit.
- ``copilot_harness`` : ce que les fichiers permettent de dire du harness.

Séparé de :mod:`grimoire.cli.doctor_hooks` pour tenir sous le plafond de taille.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from grimoire.cli.doctor_hooks import _generated_hook_commands, _launches_grimoire_hook
from grimoire.cli.env_checks import EnvCheck

_LEGACY_OS_KEYS = ("windows", "linux", "osx")
_HOST_ARG = re.compile(r"--host\s+(\S+)")


def _kit_hook_files(target: Path) -> list[tuple[str, dict[str, Any]]]:
    """``(chemin relatif, contenu)`` de chaque ``.github/hooks/grimoire-*.json`` qui lance ``grimoire-hook``."""
    found: list[tuple[str, dict[str, Any]]] = []
    for path in sorted((target / ".github" / "hooks").glob("grimoire-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
            continue
        specs = [s for entries in data["hooks"].values() for s in (entries if isinstance(entries, list) else [])]
        if any(
            isinstance(s, dict)
            and any(
                _launches_grimoire_hook(str(s[k]))
                for k in ("command", "bash", "powershell", *_LEGACY_OS_KEYS)
                if s.get(k)
            )
            for s in specs
        ):
            found.append((path.relative_to(target).as_posix(), data))
    return found


def _format_problems(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if data.get("version") != 1:
        problems.append("no `version: 1`")
    for event, entries in data["hooks"].items():
        specs = [s for s in (entries if isinstance(entries, list) else []) if isinstance(s, dict)]
        if event.lower() == "pretooluse" and not any(s.get("matcher") for s in specs):
            problems.append(f"{event} guard has no `matcher`")
        if any(k in s for s in specs for k in _LEGACY_OS_KEYS) and not all(
            "bash" in s or "powershell" in s for s in specs
        ):
            problems.append("per-OS keys `windows`/`linux`/`osx` without `bash`/`powershell`")
        if any("timeout" in s and "timeoutSec" not in s for s in specs):
            problems.append("`timeout` instead of `timeoutSec`")
    return list(dict.fromkeys(problems))


def check_copilot_hook_format(target: Path) -> list[EnvCheck]:
    """Les hooks Copilot du kit ont-ils le format de l'émetteur courant ?"""
    files = _kit_hook_files(target)
    if not files:
        return []
    old = [f"{relpath} ({', '.join(problems)})" for relpath, data in files if (problems := _format_problems(data))]
    if old:
        return [
            EnvCheck(
                "copilot_hook_format",
                passed=True,
                level="warn",
                detail="Copilot hook file(s) in an older format: " + "; ".join(old),
                remedy="run `grimoire up` to regenerate the Copilot hooks in the current format",
            )
        ]
    return [
        EnvCheck(
            "copilot_hook_format",
            passed=True,
            level="ok",
            detail=f"{len(files)} Copilot hook file(s) in the current format (version 1, guard matcher)",
        )
    ]


def check_copilot_hooks_doubled(target: Path) -> list[EnvCheck]:
    """Claude Code et Copilot câblent-ils tous deux ``grimoire-hook`` ?

    La documentation GitHub dit que Copilot lit aussi ``.claude/settings.json`` et
    ``.claude/settings.local.json`` et que, pour un même événement, toutes les
    entrées de toutes les sources tournent ; elle ne dit pas si deux entrées
    identiques sont dédoublonnées. On ne l'affirme donc pas : on signale le risque.
    """
    if not _kit_hook_files(target):
        return []
    claude = [(rel, cmd) for rel, cmd, _ in _generated_hook_commands(target) if rel.startswith(".claude/")]
    if not claude:
        return []
    hosts = sorted({m.group(1) for _, cmd in claude if (m := _HOST_ARG.search(cmd))})
    identity = f" (they launch the kit with --host {', '.join(hosts)})" if hosts else ""
    return [
        EnvCheck(
            "copilot_hooks_doubled",
            passed=True,
            level="warn",
            detail=(
                "Claude Code and Copilot hooks are both wired for the kit: GitHub documents that Copilot also "
                "reads .claude/settings.json and runs all hook entries from all sources, and does not say "
                "whether identical entries are deduplicated — the guard may run twice, and the .claude "
                f"entries{identity} may run under another host identity than Copilot's"
            ),
            remedy="keep one of the two hosts enabled in the project config, or check a Copilot session log for a doubled guard",
        )
    ]


def check_copilot_claude_hooks_only(target: Path) -> list[EnvCheck]:
    """Copilot activé ou détecté, mais seul ``.claude/settings.json`` lance le kit ?

    Copilot exécute aussi ces hooks (référence GitHub), avec ``--host claude-code``.
    Leur matcher est celui de Claude Code : ni la lecture ni la recherche n'y sont,
    car sous Claude Code la table de permissions refuse la lecture des secrets ;
    Copilot n'a pas de telle table. Le refus de la garde lui-même est rendu lisible
    par Copilot quand la charge le permet (``runtime.main``), pas cette couverture.
    """
    if _kit_hook_files(target):
        return []
    if not any(rel.startswith(".claude/") for rel, _, _ in _generated_hook_commands(target)):
        return []
    from grimoire.hosts.detection import resolve_enabled_hosts

    if "copilot" not in resolve_enabled_hosts(target):
        return []
    return [
        EnvCheck(
            "copilot_claude_hooks_only",
            passed=True,
            level="warn",
            detail=(
                "Copilot is enabled or detected for this project but only .claude/settings.json launches the "
                "kit: Copilot also runs those hooks (GitHub hooks reference), with Claude Code's matcher, which "
                "leaves file reads and searches to Claude Code's permission table — Copilot has none, so the "
                "secret-read refusal does not run under Copilot"
            ),
            remedy="enable the copilot host (`hosts.enabled` in project-context.yaml) and run `grimoire up` "
            "to write .github/hooks/grimoire-*.json",
        )
    ]


def check_copilot_harness(target: Path) -> list[EnvCheck]:
    """Ce que le projet permet de dire du harness Copilot (information seule)."""
    files = _kit_hook_files(target)
    if not files:
        return []
    camel = any(ev[:1].islower() for _, data in files for ev in data["hooks"])
    detail = (
        "Copilot hook events are camelCase: payloads are camelCase, i.e. the Copilot harness"
        if camel
        else "Copilot hook events are PascalCase: payloads are snake_case, which does not tell the Copilot "
        "harness from the deprecated Local one (nor from Claude Code); the harness is read from each "
        "payload's shape at hook time (camelCase keys = Copilot harness), not from the project files"
    )
    return [EnvCheck("copilot_harness", passed=True, level="info", detail=detail)]


def check_copilot_mcp(target: Path) -> list[EnvCheck]:
    """Le serveur MCP du kit est-il déclaré dans ``.vscode/mcp.json`` quand Copilot est activé ou détecté ?

    Absent (fichier, clé ``servers`` ou entrée ``grimoire``) : WARN, ``grimoire up`` l'écrit.
    Présent mais différent de ce que l'émetteur écrirait : INFO, l'entrée est celle de l'utilisateur
    et n'est jamais réécrite. La résolution de la commande reste le travail de ``check_mcp_json``.
    """
    from grimoire.hosts.detection import resolve_enabled_hosts

    if "copilot" not in resolve_enabled_hosts(target):
        return []
    from grimoire.hosts.collect import collect_mcp_servers
    from grimoire.hosts.emitters.copilot_mcp import VSCODE_MCP, server_entry

    rel = VSCODE_MCP.as_posix()
    remedy = "run `grimoire up` (or `grimoire host sync --host copilot`) to write it; existing servers are kept"
    path = target / VSCODE_MCP
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return [
            EnvCheck(
                "copilot_mcp",
                passed=True,
                level="warn",
                detail=f"{rel} is not valid JSON",
                remedy=f"fix its syntax, then {remedy}",
            )
        ]
    servers = data.get("servers") if isinstance(data, dict) else None
    entry = servers.get("grimoire") if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return [
            EnvCheck(
                "copilot_mcp",
                passed=True,
                level="warn",
                detail=f"Copilot is enabled or detected but {rel} declares no `grimoire` MCP server (VS Code reads `servers` there)",
                remedy=remedy,
            )
        ]
    expected = next((server_entry(s) for s in collect_mcp_servers(target) if s.name == "grimoire"), None)
    if expected is not None and entry != expected:
        return [
            EnvCheck(
                "copilot_mcp",
                passed=True,
                level="info",
                detail=f"{rel} `grimoire` server differs from what `grimoire up` would write; kept as is (yours)",
            )
        ]
    return [EnvCheck("copilot_mcp", passed=True, level="ok", detail=f"{rel} declares the `grimoire` MCP server")]


def check_copilot_hooks(target: Path) -> list[EnvCheck]:
    return [
        *check_copilot_mcp(target),
        *check_copilot_hook_format(target),
        *check_copilot_hooks_doubled(target),
        *check_copilot_claude_hooks_only(target),
        *check_copilot_harness(target),
    ]
