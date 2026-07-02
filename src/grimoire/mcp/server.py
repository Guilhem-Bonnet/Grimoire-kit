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
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError
from grimoire.core.project_layout import detect_project_layout

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    msg = "MCP SDK not installed. Run: pip install grimoire-kit[mcp]"
    raise ImportError(msg) from _exc

mcp = FastMCP(
    name="grimoire",
    instructions="Grimoire Kit — Composable AI agent platform. "
    "Use these tools to inspect and manage Grimoire projects.",
)


@dataclass(frozen=True, slots=True)
class _KnowledgeScope:
    """Curated scope definition for repo knowledge search."""

    name: str
    patterns: tuple[str, ...]
    weight: int


@dataclass(frozen=True, slots=True)
class _CommandRecommendation:
    """Structured validation or execution recommendation."""

    kind: str
    value: str
    reason: str
    priority: int


_REPO_KNOWLEDGE_SCOPES: dict[str, _KnowledgeScope] = {
    "docs": _KnowledgeScope(
        name="docs",
        patterns=(
            "README.md",
            "CHANGELOG.md",
            "docs/**/*.md",
            "grimoire-kit/README.md",
            "grimoire-kit/ARCHITECTURE.md",
            "grimoire-kit/docs/**/*.md",
        ),
        weight=25,
    ),
    "plans": _KnowledgeScope(
        name="plans",
        patterns=(
            "docs/exploitation/**/*.md",
            "_grimoire-runtime-output/planning-artifacts/**/*.md",
            "_grimoire-runtime-output/implementation-artifacts/**/*.md",
        ),
        weight=35,
    ),
    "skills": _KnowledgeScope(
        name="skills",
        patterns=(".github/skills/**/SKILL.md",),
        weight=30,
    ),
    "instructions": _KnowledgeScope(
        name="instructions",
        patterns=(".github/instructions/**/*.md",),
        weight=28,
    ),
    "agents": _KnowledgeScope(
        name="agents",
        patterns=(".github/agents/**/*.md", "_grimoire-runtime/**/agents/**/*.md"),
        weight=20,
    ),
    "prompts": _KnowledgeScope(
        name="prompts",
        patterns=(".github/prompts/**/*.md", "_grimoire-runtime/**/workflows/**/*.md"),
        weight=18,
    ),
    "runtime": _KnowledgeScope(
        name="runtime",
        patterns=(
            "project-context.yaml",
            "grimoire-kit/project-context.yaml",
            "_grimoire-runtime/core/config.yaml",
            "_grimoire-runtime/_config/**/*.yaml",
            "_grimoire-runtime/_config/**/*.csv",
            ".vscode/*.json",
        ),
        weight=30,
    ),
}
_DEFAULT_REPO_KNOWLEDGE_SCOPES = ("docs", "plans", "skills", "instructions", "runtime")
_MAX_KNOWLEDGE_FILE_BYTES = 262_144
_MCP_TRUSTED_REMOTE_HOSTS = {
    "api.githubcopilot.com": {
        "product": "GitHub MCP",
        "trust_level": "trusted-remote",
        "mutability": "read-write",
        "auth_mode": "client-managed",
    },
    "mcp.context7.com": {
        "product": "Context7",
        "trust_level": "trusted-remote",
        "mutability": "read-mostly",
        "auth_mode": "anonymous-or-rate-limited",
    },
}
_DEFAULT_MCP_POLICY_PATH = "_grimoire-runtime/_config/mcp-policy.yaml"


def _find_config() -> GrimoireConfig:
    """Find and load the project config from cwd upward."""
    return GrimoireConfig.find_and_load()


def _dump_json(payload: dict[str, Any]) -> str:
    """Serialize MCP tool results consistently."""
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _error(message: str, **context: Any) -> str:
    """Return a structured JSON error payload."""
    return _dump_json({"error": message, **context})


def _resolve_path(path_value: str, *, base: Path) -> Path:
    """Resolve a possibly relative path against a base directory."""
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _ensure_within_root(path: Path, *, root: Path, label: str) -> Path:
    """Ensure a resolved path stays within an allowed root."""
    resolved = path.resolve()
    allowed_root = root.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise GrimoireError(
            f"{label} must stay within {allowed_root}"
        ) from exc
    return resolved


def _resolve_path_within(
    path_value: str,
    *,
    base: Path,
    root: Path,
    label: str,
) -> Path:
    """Resolve a path and reject escapes outside the allowed root."""
    return _ensure_within_root(
        _resolve_path(path_value, base=base),
        root=root,
        label=label,
    )


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float = 600,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a subprocess and return a JSON-serializable result."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=process_env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "command": command,
            "cwd": str(cwd),
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        }

    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
        "cwd": str(cwd),
    }


def _parse_json_output(stdout: str) -> Any | None:
    """Parse JSON subprocess output when available."""
    content = stdout.strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _resolve_assets_root(project_root: Path, assets_root: str = "") -> Path | None:
    """Resolve the grimoire-game-assets root from the project or workspace."""
    detected_root = _find_assets_root(project_root)
    if assets_root:
        candidate = _resolve_path(assets_root, base=project_root)
        if (
            detected_root
            and candidate == detected_root
            and (candidate / "tools").is_dir()
            and (candidate / "10-curated").is_dir()
        ):
            return candidate
        return None
    return detected_root


def _relative_display_path(path: Path, *, base: Path) -> str:
    """Return a stable POSIX path relative to a project root when possible."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_csv_or_lines(value: str) -> list[str]:
    """Split a comma or newline separated string while preserving ordering."""
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def _parse_paths_input(paths: str, *, base: Path) -> list[str]:
    """Parse newline/comma-separated paths into normalized project-relative paths."""
    candidates = _parse_csv_or_lines(paths)
    normalized: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        resolved = _resolve_path(candidate, base=base)
        rel = _relative_display_path(resolved, base=base)
        if rel in seen:
            continue
        seen.add(rel)
        normalized.append(rel)

    return normalized


def _normalize_kit_relative_path(path: str) -> Path:
    """Normalize paths so heuristics work from workspace root or kit root."""
    candidate = Path(path)
    if candidate.parts[:1] == ("grimoire-kit",):
        return Path(*candidate.parts[1:])
    return candidate


def _find_git_root(start: Path) -> Path:
    """Resolve the Git repository root when available."""
    result = _run_subprocess(["git", "rev-parse", "--show-toplevel"], cwd=start, timeout_seconds=30)
    if result["ok"]:
        stdout = result["stdout"].strip()
        if stdout:
            return Path(stdout).resolve()
    return start.resolve()


def _collect_candidate_paths(
    project_root: Path,
    *,
    paths: str,
    base_ref: str,
    limit: int,
) -> tuple[list[str], str, bool, list[str]]:
    """Return explicit or Git-derived changed paths with deterministic truncation."""
    max_paths = max(1, min(limit, 200))
    if paths.strip():
        parsed = _parse_paths_input(paths, base=project_root)
        return parsed[:max_paths], "paths", len(parsed) > max_paths, []

    git_root = _find_git_root(project_root)
    diff_target = base_ref if ".." in base_ref else f"{base_ref}...HEAD" if base_ref else "HEAD"
    tracked = _run_subprocess(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", diff_target, "--"],
        cwd=git_root,
        timeout_seconds=60,
    )
    if not tracked["ok"]:
        message = tracked["stderr"].strip() or "git diff failed"
        return [], "git-diff", False, [message]

    untracked = _run_subprocess(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=git_root,
        timeout_seconds=60,
    )
    raw_entries = [line.strip() for line in tracked["stdout"].splitlines() if line.strip()]
    if untracked["ok"]:
        raw_entries.extend(line.strip() for line in untracked["stdout"].splitlines() if line.strip())

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        resolved = _resolve_path(entry, base=git_root)
        rel = _relative_display_path(resolved, base=project_root)
        if rel in seen:
            continue
        seen.add(rel)
        normalized.append(rel)

    return normalized[:max_paths], "git-diff", len(normalized) > max_paths, []


def _path_categories(path: str) -> list[str]:
    """Classify a changed path into deterministic impact categories."""
    lowered = Path(path).as_posix().lower()
    normalized = _normalize_kit_relative_path(path)
    normalized_lowered = normalized.as_posix().lower()
    categories: list[str] = []

    if normalized_lowered.startswith("src/") and normalized.suffix.lower() == ".py":
        categories.append("python-source")
    if normalized_lowered.startswith("framework/tools/") and normalized.suffix.lower() == ".py":
        categories.append("python-tooling")
    if normalized_lowered.startswith("tests/") and normalized.suffix.lower() == ".py":
        categories.append("python-tests")
    if "/mcp/" in f"/{normalized_lowered}":
        categories.append("mcp-surface")
    if normalized_lowered.startswith("apps/grimoire-game/src/") and normalized.suffix.lower() in {".ts", ".tsx"}:
        categories.append("game-runtime-source")
    if normalized_lowered.startswith("apps/grimoire-game/tests/") and normalized.suffix.lower() in {".ts", ".tsx"}:
        categories.append("game-tests")
    if lowered.startswith(".github/skills/") and lowered.endswith("skill.md"):
        categories.append("skill-definition")
    if lowered.startswith(".github/instructions/") and lowered.endswith(".md"):
        categories.append("instruction-definition")
    if lowered.startswith(".github/prompts/") and lowered.endswith(".md"):
        categories.append("prompt-definition")
    if lowered.startswith(".github/agents/") and lowered.endswith(".md"):
        categories.append("agent-definition")
    if lowered.startswith("_grimoire-runtime/_config/") or lowered.endswith("project-context.yaml"):
        categories.append("runtime-config")
    if lowered.startswith(".vscode/"):
        categories.append("workspace-config")
    if lowered.startswith("grimoire-game-assets/"):
        categories.append("asset-pipeline")
    if lowered.endswith(".md"):
        categories.append("documentation")

    return list(dict.fromkeys(categories)) or ["other"]


def _discover_related_tests(project_root: Path, path: str) -> list[str]:
    """Find test files that are likely impacted by a changed source path."""
    related: list[str] = []
    normalized = _normalize_kit_relative_path(path)
    normalized_value = normalized.as_posix()
    kit_root = _find_kit_root(project_root)
    if kit_root is None:
        return related

    tests_root = kit_root / "tests"
    if normalized_value.startswith("tests/") and normalized.suffix.lower() == ".py":
        related.append(path)
    elif normalized_value.startswith("src/") and normalized.suffix.lower() == ".py":
        stem = normalized.stem.replace("-", "_")
        if stem == "__init__":
            stem = normalized.parent.name.replace("-", "_")

        for candidate in tests_root.rglob(f"test_{stem}.py"):
            related.append(_relative_display_path(candidate, base=project_root))
        if not related:
            for candidate in tests_root.rglob(f"*{stem}*.py"):
                if candidate.name.startswith("test_"):
                    related.append(_relative_display_path(candidate, base=project_root))

    game_tests_root = kit_root / "apps" / "grimoire-game" / "tests"
    if normalized_value.startswith("apps/grimoire-game/tests/") and normalized.suffix.lower() in {".ts", ".tsx"}:
        related.append(path)
    elif normalized_value.startswith("apps/grimoire-game/src/") and normalized.suffix.lower() in {".ts", ".tsx"}:
        stem = normalized.stem
        patterns = (
            f"{stem}.test.ts",
            f"{stem}.test.tsx",
            f"{stem}*.test.ts",
            f"{stem}*.test.tsx",
        )
        for pattern in patterns:
            for candidate in game_tests_root.rglob(pattern):
                related.append(_relative_display_path(candidate, base=project_root))

    return sorted(dict.fromkeys(related))


def _shell_join(parts: list[str]) -> str:
    """Render a shell-safe command string."""
    return " ".join(shlex.quote(part) for part in parts)


def _serialize_commands(recommendations: list[_CommandRecommendation]) -> list[dict[str, Any]]:
    """Serialize and deduplicate recommendations deterministically."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in recommendations:
        key = (item.kind, item.value)
        bucket = merged.setdefault(
            key,
            {
                "kind": item.kind,
                "value": item.value,
                "priority": item.priority,
                "reasons": [],
            },
        )
        bucket["priority"] = min(bucket["priority"], item.priority)
        if item.reason not in bucket["reasons"]:
            bucket["reasons"].append(item.reason)

    ordered = sorted(merged.values(), key=lambda item: (item["priority"], item["kind"], item["value"]))
    return [
        {
            "id": f"cmd-{index + 1}",
            "kind": item["kind"],
            "value": item["value"],
            "reason": "; ".join(item["reasons"]),
            "priority": item["priority"],
        }
        for index, item in enumerate(ordered)
    ]


def _game_app_root_from_path(path: str) -> str | None:
    """Return the app root prefix for a grimoire-game file path."""
    posix_path = Path(path).as_posix()
    for marker in ("/src/", "/tests/"):
        if marker in posix_path:
            return posix_path.split(marker, maxsplit=1)[0]
    return None


def _impact_notes(categories: list[str]) -> list[str]:
    """Return concise notes for notable impact categories."""
    notes: list[str] = []
    if "mcp-surface" in categories:
        notes.append("Surface MCP modifiée: revalider les tools exposés et leurs contrats de test.")
    if "skill-definition" in categories:
        notes.append("Une skill modifiée peut changer le routage et la validation du workspace.")
    if "game-runtime-source" in categories:
        notes.append("Le runtime game exige au minimum check, build et tests ciblés.")
    if "runtime-config" in categories:
        notes.append("La configuration runtime impacte le bootstrap et la preflight du projet.")
    return notes


def _recommended_commands(
    project_root: Path,
    path: str,
    categories: list[str],
    related_tests: list[str],
) -> list[_CommandRecommendation]:
    """Build deterministic command recommendations from impact metadata."""
    commands: list[_CommandRecommendation] = []
    kit_root = _find_kit_root(project_root)
    kit_prefix = _relative_display_path(kit_root, base=project_root) if kit_root else ""
    python_bin = f"{kit_prefix}/.venv/bin/python" if kit_root and kit_prefix != "." else ".venv/bin/python"

    if {"python-source", "python-tooling", "python-tests"} & set(categories):
        pytest_targets = related_tests or [path] if "python-tests" in categories else related_tests
        if pytest_targets:
            commands.append(
                _CommandRecommendation(
                    kind="shell",
                    value=_shell_join([python_bin, "-m", "pytest", *pytest_targets, "-q", "--tb=short", "-x"]),
                    reason="Valider la régression Python ciblée.",
                    priority=10,
                )
            )
        elif "python-source" in categories:
            commands.append(
                _CommandRecommendation(
                    kind="task",
                    value="grimoire: test-all",
                    reason="Aucun test ciblé trouvé, rejouer la suite Python du kit.",
                    priority=50,
                )
            )

        ruff_targets = [path, *related_tests] if related_tests else [path]
        commands.append(
            _CommandRecommendation(
                kind="shell",
                value=_shell_join([python_bin, "-m", "ruff", "check", *ruff_targets]),
                reason="Vérifier le lint sur les fichiers Python impactés.",
                priority=20,
            )
        )
        if {"python-tooling", "python-tests"} & set(categories):
            commands.append(
                _CommandRecommendation(
                    kind="task",
                    value="grimoire: quickcheck",
                    reason="Rejouer le garde-fou rapide du kit sur l'outillage et les tests Python.",
                    priority=40,
                )
            )

    if {"game-runtime-source", "game-tests"} & set(categories):
        app_root = _game_app_root_from_path(path)
        if app_root:
            commands.append(
                _CommandRecommendation(
                    kind="shell",
                    value=f"npm --prefix {app_root} run check",
                    reason="Type-checker le runtime game.",
                    priority=20,
                )
            )
            if "game-runtime-source" in categories:
                commands.append(
                    _CommandRecommendation(
                        kind="shell",
                        value=f"npm --prefix {app_root} run build",
                        reason="Valider le build distribué du runtime game.",
                        priority=30,
                    )
                )

            test_targets = related_tests or [path] if "game-tests" in categories else related_tests
            for test_path in test_targets:
                relative_test = test_path[len(app_root) + 1 :] if test_path.startswith(f"{app_root}/") else test_path
                commands.append(
                    _CommandRecommendation(
                        kind="shell",
                        value=f"npm --prefix {app_root} run test -- {relative_test}",
                        reason="Rejouer les tests Vitest ciblés.",
                        priority=10,
                    )
                )

    if "skill-definition" in categories:
        commands.append(
            _CommandRecommendation(
                kind="task",
                value="grimoire: validate-skills",
                reason="Valider la syntaxe et les contrats des skills.",
                priority=35,
            )
        )

    if {"documentation", "instruction-definition", "prompt-definition", "agent-definition"} & set(categories):
        commands.append(
            _CommandRecommendation(
                kind="task",
                value="grimoire: auto-doc-check",
                reason="Vérifier la cohérence documentaire et les dérives d'artefacts.",
                priority=45,
            )
        )

    if {
        "skill-definition",
        "instruction-definition",
        "prompt-definition",
        "agent-definition",
        "runtime-config",
        "workspace-config",
    } & set(categories):
        commands.append(
            _CommandRecommendation(
                kind="task",
                value="grimoire: preflight",
                reason="Revalider le bootstrap et les invariants du projet.",
                priority=50,
            )
        )

    return commands


def _analyze_path_impact(project_root: Path, path: str) -> dict[str, Any]:
    """Aggregate categories, related tests, notes and commands for a path."""
    categories = _path_categories(path)
    related_tests = _discover_related_tests(project_root, path)
    recommendations = _recommended_commands(project_root, path, categories, related_tests)
    return {
        "path": path,
        "exists": (project_root / path).exists(),
        "categories": categories,
        "related_tests": related_tests,
        "notes": _impact_notes(categories),
        "recommendations": recommendations,
    }


def _parse_knowledge_scopes(scopes: str) -> tuple[list[str], list[str]]:
    """Resolve scope names while preserving user ordering."""
    requested = [item.lower() for item in _parse_csv_or_lines(scopes)]
    if not requested:
        requested = list(_DEFAULT_REPO_KNOWLEDGE_SCOPES)
    if "all" in requested:
        requested = list(_REPO_KNOWLEDGE_SCOPES)

    resolved: list[str] = []
    invalid: list[str] = []
    for item in requested:
        if item in _REPO_KNOWLEDGE_SCOPES and item not in resolved:
            resolved.append(item)
        elif item not in _REPO_KNOWLEDGE_SCOPES and item not in invalid:
            invalid.append(item)
    return resolved, invalid


def _knowledge_scope_files(project_root: Path, scopes: list[str]) -> list[tuple[str, Path, int]]:
    """Enumerate searchable files for curated knowledge scopes."""
    files: list[tuple[str, Path, int]] = []
    seen: set[str] = set()
    for scope_name in scopes:
        scope = _REPO_KNOWLEDGE_SCOPES[scope_name]
        for pattern in scope.patterns:
            for candidate in project_root.glob(pattern):
                if not candidate.is_file():
                    continue
                try:
                    if candidate.stat().st_size > _MAX_KNOWLEDGE_FILE_BYTES:
                        continue
                except OSError:
                    continue
                relative = _relative_display_path(candidate, base=project_root)
                if relative in seen:
                    continue
                seen.add(relative)
                files.append((scope_name, candidate, scope.weight))
    return files


def _search_knowledge_file(
    path: Path,
    *,
    project_root: Path,
    scope_name: str,
    scope_weight: int,
    query: str,
    tokens: list[str],
) -> list[dict[str, Any]]:
    """Return ranked line-level matches for a curated knowledge file."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    relative = _relative_display_path(path, base=project_root)
    path_lower = relative.lower()
    path_score = 2 if query in path_lower else 0
    matches: list[dict[str, Any]] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        exact = query in lowered
        token_hits = {token for token in tokens if token in lowered}
        if not exact and not token_hits:
            continue

        snippet = text[:240]
        matches.append(
            {
                "path": relative,
                "scope": scope_name,
                "line": line_number,
                "text": snippet,
                "snippet": snippet,
                "score": scope_weight + path_score + len(token_hits) * 3 + (5 if exact else 0),
            }
        )
        if len(matches) >= 3:
            break

    return matches


def _load_mcp_config(config_path: Path) -> dict[str, Any]:
    """Load a VS Code MCP config file."""
    with config_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("MCP config root must be an object")
    servers = data.get("servers")
    if servers is None:
        return {"servers": {}}
    if not isinstance(servers, dict):
        raise ValueError("MCP config 'servers' must be an object")
    return data


def _coerce_string_list(value: Any) -> list[str]:
    """Normalize a list of strings from user config."""
    if not isinstance(value, (list, tuple, set)):
        return []
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _load_mcp_policy(project_root: Path, policy_path: str = _DEFAULT_MCP_POLICY_PATH) -> dict[str, Any]:
    """Load optional MCP policy overrides from the project runtime config."""
    resolved = _resolve_path(policy_path, base=project_root)
    policy: dict[str, Any] = {
        "loaded": False,
        "source": _relative_display_path(resolved, base=project_root),
        "trusted_remote_hosts": sorted(_MCP_TRUSTED_REMOTE_HOSTS),
        "trusted_workspace_servers": [],
        "fail_closed_remote_hosts": False,
        "server_overrides": {},
    }
    if not resolved.is_file():
        return policy

    from grimoire.tools._common import load_yaml

    raw = load_yaml(resolved)
    if raw is None:
        policy["loaded"] = True
        return policy
    if not isinstance(raw, dict):
        msg = f"MCP policy root must be an object: {resolved}"
        raise ValueError(msg)

    combined_hosts = sorted(
        set(policy["trusted_remote_hosts"]) | set(_coerce_string_list(raw.get("trusted_remote_hosts")))
    )
    server_overrides = raw.get("server_overrides")
    policy.update(
        {
            "loaded": True,
            "trusted_remote_hosts": combined_hosts,
            "trusted_workspace_servers": _coerce_string_list(raw.get("trusted_workspace_servers")),
            "fail_closed_remote_hosts": bool(raw.get("fail_closed_remote_hosts", False)),
            "server_overrides": server_overrides if isinstance(server_overrides, dict) else {},
        }
    )
    return policy


def _looks_like_placeholder(value: str) -> bool:
    """Detect template placeholders rather than literal secrets."""
    return "${" in value or value.startswith("YOUR_") or value.endswith(("_TOKEN", "_API_KEY"))


def _secret_field_name(name: str) -> bool:
    """Return whether a field name is likely carrying a secret."""
    lowered = name.lower()
    return any(token in lowered for token in ("authorization", "token", "api_key", "apikey", "secret", "key"))


def _infer_auth_mode(server_name: str, config: dict[str, Any], host: str) -> str:
    """Infer how a server is authenticated, if at all."""
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    for key, value in headers.items():
        if not isinstance(value, str):
            continue
        if _secret_field_name(key):
            return "indirect-header-secret" if _looks_like_placeholder(value) else "hardcoded-header-secret"

    env = config.get("env") if isinstance(config.get("env"), dict) else {}
    for key, value in env.items():
        if not isinstance(value, str):
            continue
        if _secret_field_name(key):
            return "indirect-env-secret" if _looks_like_placeholder(value) else "hardcoded-env-secret"

    args = config.get("args") if isinstance(config.get("args"), list) else []
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        lowered = arg.lower()
        if lowered in {"--api-key", "--token", "--auth-token"} and index + 1 < len(args):
            candidate = args[index + 1]
            if isinstance(candidate, str):
                return "indirect-arg-secret" if _looks_like_placeholder(candidate) else "hardcoded-arg-secret"

    trusted = _MCP_TRUSTED_REMOTE_HOSTS.get(host, {})
    if trusted.get("auth_mode"):
        return str(trusted["auth_mode"])
    if host:
        return "none"
    return "not-applicable"


def _infer_mutability(server_name: str, host: str) -> str:
    """Infer an approximate mutability profile for a server."""
    trusted = _MCP_TRUSTED_REMOTE_HOSTS.get(host, {})
    if trusted.get("mutability"):
        return str(trusted["mutability"])

    lowered = server_name.lower()
    if any(token in lowered for token in ("context7", "docs", "documentation")):
        return "read-mostly"
    if any(token in lowered for token in ("github", "grimoire", "playwright", "browser", "git")):
        return "read-write"
    return "unknown"


def _infer_trust_level(config: dict[str, Any], host: str) -> str:
    """Infer whether a server is trusted local, trusted remote, or unreviewed."""
    if host in _MCP_TRUSTED_REMOTE_HOSTS:
        return str(_MCP_TRUSTED_REMOTE_HOSTS[host]["trust_level"])

    command = config.get("command")
    if isinstance(command, str) and command:
        if "${workspaceFolder}" in command or command.startswith(("./", "/")):
            return "workspace-local"
        return "ambient-local"

    if host:
        return "unreviewed-remote"
    return "unknown"


def _infer_transport(config: dict[str, Any]) -> str:
    """Infer transport from config shape."""
    explicit = config.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit
    if isinstance(config.get("command"), str):
        return "stdio"
    if isinstance(config.get("url"), str):
        return "http"
    return "unknown"


def _mcp_server_endpoint(config: dict[str, Any]) -> str:
    """Return the primary endpoint or command for display."""
    for key in ("url", "serverUrl", "httpUrl"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    command = config.get("command")
    if isinstance(command, str):
        return command
    return ""


def _mcp_policy_notes(
    *,
    transport: str,
    trust_level: str,
    auth_mode: str,
    mutability: str,
    risk_flags: list[str],
) -> list[str]:
    """Generate human-readable policy notes for a server."""
    notes: list[str] = []
    if transport == "stdio":
        notes.append("Serveur local: toute commande expose une surface d'exécution locale.")
    if transport in {"http", "streamable-http", "streamableHttp"}:
        notes.append("Serveur distant: les réponses entrent depuis le réseau et doivent rester traitées comme données.")
    if trust_level == "trusted-remote":
        notes.append("Hôte distant reconnu et explicitement autorisé par la policy locale.")
    elif trust_level == "trusted-local":
        notes.append("Serveur local explicitement autorisé par la policy du repo.")
    elif trust_level in {"workspace-local", "ambient-local"}:
        notes.append("Serveur local: vérifier que la commande lancée correspond bien au binaire attendu.")
    elif trust_level == "unreviewed-remote":
        notes.append("Serveur distant non revu: traiter comme non fiable tant qu'une allowlist n'existe pas.")
    if auth_mode.startswith("hardcoded"):
        notes.append("Secret en clair détecté dans la configuration MCP.")
    elif auth_mode.startswith("indirect"):
        notes.append("Secret injecté indirectement: acceptable si la source reste hors dépôt.")
    if "fail-closed-remote-deny" in risk_flags:
        notes.append("Remote refusé par la policy fail-closed du repo.")
    if mutability == "read-write":
        notes.append("Ce serveur expose probablement des opérations mutables; l'usage doit rester intentionnel.")
    if "package-runtime-install" in risk_flags:
        notes.append("La commande dépend d'un exécutable résolu à l'exécution (`npx`).")
    return notes


def _classify_mcp_server(
    server_name: str,
    config: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a configured MCP server into policy dimensions."""
    effective_policy = policy or _load_mcp_policy(Path.cwd())
    endpoint = _mcp_server_endpoint(config)
    parsed = urlparse(endpoint) if endpoint.startswith(("http://", "https://")) else None
    host = parsed.netloc.lower() if parsed else ""
    transport = _infer_transport(config)
    auth_mode = _infer_auth_mode(server_name, config, host)
    mutability = _infer_mutability(server_name, host)
    trust_level = _infer_trust_level(config, host)
    overrides = effective_policy.get("server_overrides", {}).get(server_name, {})
    if isinstance(overrides, dict):
        if isinstance(overrides.get("auth_mode"), str) and overrides["auth_mode"]:
            auth_mode = overrides["auth_mode"]
        if isinstance(overrides.get("mutability"), str) and overrides["mutability"]:
            mutability = overrides["mutability"]
        if isinstance(overrides.get("trust_level"), str) and overrides["trust_level"]:
            trust_level = overrides["trust_level"]

    trusted_remote_hosts = set(_coerce_string_list(effective_policy.get("trusted_remote_hosts")))
    trusted_workspace_servers = set(_coerce_string_list(effective_policy.get("trusted_workspace_servers")))
    if host and host in trusted_remote_hosts:
        trust_level = "trusted-remote"
    if server_name in trusted_workspace_servers and trust_level in {"workspace-local", "ambient-local", "unknown"}:
        trust_level = "trusted-local"

    risk_flags: list[str] = []

    if transport == "stdio":
        risk_flags.append("local-command-execution")
    if transport in {"http", "streamable-http", "streamableHttp"}:
        risk_flags.append("remote-network")
    command = config.get("command")
    if command == "npx":
        risk_flags.append("package-runtime-install")
    if transport == "stdio" and isinstance(command, str) and "${workspaceFolder}" not in command and "/" not in command:
        risk_flags.append("ambient-executable")
    if trust_level == "unreviewed-remote":
        risk_flags.append("unreviewed-remote")
    if auth_mode.startswith("hardcoded"):
        risk_flags.append("hardcoded-secret")
    if host and trust_level == "unreviewed-remote" and auth_mode == "none":
        risk_flags.append("unauthenticated-remote")
    if host and trust_level == "unreviewed-remote" and effective_policy.get("fail_closed_remote_hosts"):
        risk_flags.append("fail-closed-remote-deny")
    if mutability == "read-write":
        risk_flags.append("write-capable")

    if "hardcoded-secret" in risk_flags or "fail-closed-remote-deny" in risk_flags:
        status = "fail"
    elif trust_level in {"trusted-remote", "trusted-local"} and "ambient-executable" not in risk_flags:
        status = "pass"
    elif "local-command-execution" in risk_flags or "unreviewed-remote" in risk_flags or "ambient-executable" in risk_flags:
        status = "warn"
    else:
        status = "pass"

    return {
        "name": server_name,
        "transport": transport,
        "endpoint": endpoint,
        "host": host or None,
        "auth_mode": auth_mode,
        "mutability": mutability,
        "trust_level": trust_level,
        "status": status,
        "risk_flags": risk_flags,
        "notes": _mcp_policy_notes(
            transport=transport,
            trust_level=trust_level,
            auth_mode=auth_mode,
            mutability=mutability,
            risk_flags=risk_flags,
        ),
    }


def _run_assets_python_tool(
    tool_name: str,
    project_root: Path,
    assets_root: Path,
    script_name: str,
    args: list[str],
    **context: Any,
) -> str:
    """Run a Python asset tool and return a structured response."""
    script = assets_root / "tools" / script_name
    if not script.is_file():
        return _error(
            f"Asset tool not found: {script}",
            tool=tool_name,
            project_root=str(project_root),
            assets_root=str(assets_root),
        )

    result = _run_subprocess(
        [sys.executable, str(script), *args],
        cwd=assets_root,
        timeout_seconds=900,
    )
    return _dump_json(
        {
            "tool": tool_name,
            "project_root": str(project_root),
            "assets_root": str(assets_root),
            **context,
            **result,
        }
    )


def _run_assets_shell_tool(
    tool_name: str,
    project_root: Path,
    assets_root: Path,
    script_name: str,
    *,
    env: dict[str, str],
    **context: Any,
) -> str:
    """Run a shell asset tool and return a structured response."""
    script = assets_root / "tools" / script_name
    if not script.is_file():
        return _error(
            f"Asset tool not found: {script}",
            tool=tool_name,
            project_root=str(project_root),
            assets_root=str(assets_root),
        )

    result = _run_subprocess(
        ["bash", str(script)],
        cwd=assets_root,
        timeout_seconds=900,
        env=env,
    )
    return _dump_json(
        {
            "tool": tool_name,
            "project_root": str(project_root),
            "assets_root": str(assets_root),
            **context,
            **result,
        }
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
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
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_status(project_path: str = ".") -> str:
    """Return project health status as JSON — config validity, structure, agents.

    Args:
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    layout = detect_project_layout(target)
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

    for d in layout.required_dirs:
        check(f"dir_{d}", (target / d).is_dir())

    passed = sum(1 for c in checks if c["ok"])
    return json.dumps({
        "project_root": str(target),
        "layout": layout.name,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "healthy": passed == len(checks),
        "grimoire_kit_version": __version__,
    }, indent=2)


@mcp.tool()
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
        return json.dumps({"error": str(exc)})

    # Find kit root (where archetypes/ lives)
    kit_root = _find_kit_root(target)
    if not kit_root:
        return json.dumps({"error": "Cannot find archetypes/ directory", "agents": []})

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
        return json.dumps({"error": str(exc), "agents": []})


@mcp.tool()
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


@mcp.tool()
def grimoire_config(project_path: str = ".") -> str:
    """Return the raw parsed project-context.yaml as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools._common import load_yaml

    target = Path(project_path).resolve()
    config_file = target / "project-context.yaml"
    if not config_file.is_file():
        return json.dumps({"error": f"No project-context.yaml found at {target}"})
    try:
        raw = load_yaml(config_file)
        return json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_memory_store(text: str, user_id: str = "", project_path: str = ".") -> str:
    """Store a memory entry in the project's configured memory backend.

    Args:
        text: The text to remember.
        user_id: Optional user ID to scope the memory.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg)
        entry = mgr.store(text, user_id=user_id)
        return json.dumps(entry.to_dict(), indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
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
        mgr = MemoryManager.from_config(cfg)
        entries = mgr.search(query, user_id=user_id, limit=limit)
        return json.dumps({
            "query": query,
            "results": [e.to_dict() for e in entries],
            "count": len(entries),
        }, indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_add_agent(agent_id: str, project_path: str = ".") -> str:
    """Add a custom agent to the project configuration.

    Args:
        agent_id: The agent identifier to add.
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        return json.dumps({"error": "No project-context.yaml found"})

    try:
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.preserve_quotes = True
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.load(fh)

        agents = data.get("agents") or {}
        custom: list[str] = agents.get("custom_agents") or []

        if agent_id in custom:
            return json.dumps({"status": "already_present", "agent_id": agent_id})

        custom.append(agent_id)
        agents["custom_agents"] = custom
        data["agents"] = agents

        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh)

        return json.dumps({"status": "added", "agent_id": agent_id})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_preflight_check(project_path: str = ".") -> str:
    """Run the structured preflight check on a Grimoire project."""
    from grimoire.tools.preflight_check import PreflightCheck

    target = Path(project_path).resolve()
    try:
        report = PreflightCheck(target).run().to_dict()
        return _dump_json(
            {
                "tool": "grimoire_preflight_check",
                "project_root": str(target),
                "ok": report["go_nogo"] != "NO-GO",
                **report,
            }
        )
    except Exception as exc:
        return _error(
            str(exc),
            tool="grimoire_preflight_check",
            project_root=str(target),
        )


@mcp.tool()
def grimoire_quick_check(project_path: str = ".") -> str:
    """Run the repo quick-check shell validation workflow."""
    target = Path(project_path).resolve()
    kit_root = _find_kit_root(target)
    if not kit_root:
        return _error(
            "Cannot find kit root containing archetypes/",
            tool="grimoire_quick_check",
            project_root=str(target),
        )

    script = kit_root / "framework" / "tools" / "quick-check.sh"
    if not script.is_file():
        return _error(
            f"Quick check script not found: {script}",
            tool="grimoire_quick_check",
            project_root=str(target),
            kit_root=str(kit_root),
        )

    result = _run_subprocess(["bash", str(script)], cwd=kit_root, timeout_seconds=900)
    return _dump_json(
        {
            "tool": "grimoire_quick_check",
            "project_root": str(target),
            "kit_root": str(kit_root),
            **result,
        }
    )


@mcp.tool()
def grimoire_memory_lint(project_path: str = ".") -> str:
    """Run the memory coherence linter on the project memory."""
    from grimoire.tools.memory_lint import MemoryLint

    target = Path(project_path).resolve()
    try:
        report = MemoryLint(target).run().to_dict()
        return _dump_json(
            {
                "tool": "grimoire_memory_lint",
                "project_root": str(target),
                "ok": report["summary"]["errors"] == 0,
                **report,
            }
        )
    except Exception as exc:
        return _error(
            str(exc),
            tool="grimoire_memory_lint",
            project_root=str(target),
        )


@mcp.tool()
def grimoire_validate_skills(
    project_path: str = ".",
    skill: str = "",
    strict: bool = False,
) -> str:
    """Run the deterministic skill validator against the project skills."""
    target = Path(project_path).resolve()
    kit_root = _find_kit_root(target)
    if not kit_root:
        return _error(
            "Cannot find kit root containing archetypes/",
            tool="grimoire_validate_skills",
            project_root=str(target),
        )

    script = kit_root / "framework" / "tools" / "skill-validator.py"
    if not script.is_file():
        return _error(
            f"Skill validator not found: {script}",
            tool="grimoire_validate_skills",
            project_root=str(target),
            kit_root=str(kit_root),
        )

    command = [
        sys.executable,
        str(script),
        "--project-root",
        str(target),
        "--json",
    ]
    if skill:
        command.extend(["--skill", skill])
    if strict:
        command.append("--strict")

    result = _run_subprocess(command, cwd=kit_root, timeout_seconds=900)
    payload: dict[str, Any] = {
        "tool": "grimoire_validate_skills",
        "project_root": str(target),
        "kit_root": str(kit_root),
        "skill": skill or None,
        "strict": strict,
        **result,
    }
    report = _parse_json_output(result["stdout"])
    if report is not None:
        payload["report"] = report
    return _dump_json(payload)


@mcp.tool()
def grimoire_repo_knowledge_search(
    query: str,
    project_path: str = ".",
    scopes: str = "docs,plans,skills,instructions,runtime",
    limit: int = 10,
) -> str:
    """Search curated repository knowledge scopes and return ranked snippets."""
    target = Path(project_path).resolve()
    normalized_query = query.strip().lower()
    if not normalized_query:
        return _error(
            "Query must not be empty",
            tool="grimoire_repo_knowledge_search",
            project_root=str(target),
        )

    requested_scopes, invalid_scopes = _parse_knowledge_scopes(scopes)
    if invalid_scopes:
        return _error(
            "Unknown knowledge scopes",
            tool="grimoire_repo_knowledge_search",
            project_root=str(target),
            invalid_scopes=invalid_scopes,
            available_scopes=sorted(_REPO_KNOWLEDGE_SCOPES),
        )

    tokens = re.findall(r"[a-z0-9]{2,}", normalized_query)
    max_results = max(1, min(limit, 100))
    matches: list[dict[str, Any]] = []
    searchable_files = _knowledge_scope_files(target, requested_scopes)

    for scope_name, file_path, weight in searchable_files:
        matches.extend(
            _search_knowledge_file(
                file_path,
                project_root=target,
                scope_name=scope_name,
                scope_weight=weight,
                query=normalized_query,
                tokens=tokens,
            )
        )

    matches.sort(key=lambda item: (-item["score"], item["path"], item["line"]))
    sliced_matches = matches[:max_results]

    return _dump_json(
        {
            "tool": "grimoire_repo_knowledge_search",
            "project_root": str(target),
            "query": query,
            "scopes": requested_scopes,
            "searched_files": len(searchable_files),
            "count": len(sliced_matches),
            "truncated": len(matches) > max_results,
            "results": sliced_matches,
        }
    )


@mcp.tool()
def grimoire_test_recommendations(
    project_path: str = ".",
    paths: str = "",
    base_ref: str = "",
    limit: int = 25,
) -> str:
    """Recommend targeted tests and validation commands from changed paths."""
    target = Path(project_path).resolve()
    if limit < 1:
        return _error(
            "Limit must be >= 1",
            tool="grimoire_test_recommendations",
            project_root=str(target),
        )

    parsed_paths, source, truncated, errors = _collect_candidate_paths(
        target,
        paths=paths,
        base_ref=base_ref,
        limit=limit,
    )
    if errors:
        return _error(
            errors[0],
            tool="grimoire_test_recommendations",
            project_root=str(target),
            base_ref=base_ref or None,
        )

    related_tests: list[str] = []
    recommendations: list[_CommandRecommendation] = []
    per_path: list[dict[str, Any]] = []

    for path in parsed_paths:
        impact = _analyze_path_impact(target, path)
        per_path.append(
            {
                "path": impact["path"],
                "categories": impact["categories"],
                "related_tests": impact["related_tests"],
                "notes": impact["notes"],
            }
        )
        related_tests.extend(impact["related_tests"])
        recommendations.extend(impact["recommendations"])

    unique_tests = sorted(set(related_tests))

    return _dump_json(
        {
            "tool": "grimoire_test_recommendations",
            "project_root": str(target),
            "source": source,
            "truncated": truncated,
            "paths": parsed_paths,
            "changed_files": parsed_paths,
            "related_tests": unique_tests,
            "per_path": per_path,
            "recommended_commands": _serialize_commands(recommendations),
        }
    )


@mcp.tool()
def grimoire_diff_impact(
    project_path: str = ".",
    paths: str = "",
    base_ref: str = "",
    limit: int = 25,
) -> str:
    """Analyze changed paths and produce impact categories, tests, and commands."""
    target = Path(project_path).resolve()
    if limit < 1:
        return _error(
            "Limit must be >= 1",
            tool="grimoire_diff_impact",
            project_root=str(target),
        )

    parsed_paths, source, truncated, errors = _collect_candidate_paths(
        target,
        paths=paths,
        base_ref=base_ref,
        limit=limit,
    )
    if errors:
        return _error(
            errors[0],
            tool="grimoire_diff_impact",
            project_root=str(target),
            base_ref=base_ref or None,
        )

    impacts: list[dict[str, Any]] = []
    summary_commands: list[_CommandRecommendation] = []

    for path in parsed_paths:
        impact = _analyze_path_impact(target, path)
        summary_commands.extend(impact["recommendations"])
        impacts.append(
            {
                "path": impact["path"],
                "exists": impact["exists"],
                "categories": impact["categories"],
                "related_tests": impact["related_tests"],
                "notes": impact["notes"],
                "recommended_commands": _serialize_commands(impact["recommendations"]),
            }
        )

    category_counts: dict[str, int] = {}
    for impact in impacts:
        for category in impact["categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1

    return _dump_json(
        {
            "tool": "grimoire_diff_impact",
            "project_root": str(target),
            "source": source,
            "truncated": truncated,
            "paths": parsed_paths,
            "changed_files": parsed_paths,
            "impacts": impacts,
            "summary": {
                "path_count": len(parsed_paths),
                "category_counts": category_counts,
                "recommended_commands": _serialize_commands(summary_commands),
            },
        }
    )


@mcp.tool()
def grimoire_mcp_policy_report(
    project_path: str = ".",
    config_path: str = ".vscode/mcp.json",
    policy_path: str = _DEFAULT_MCP_POLICY_PATH,
) -> str:
    """Inspect the workspace MCP config and classify servers by policy risk."""
    target = Path(project_path).resolve()
    resolved_config_path = _resolve_path(config_path, base=target)
    if not resolved_config_path.is_file():
        return _error(
            f"MCP config not found: {resolved_config_path}",
            tool="grimoire_mcp_policy_report",
            project_root=str(target),
            config_path=_relative_display_path(resolved_config_path, base=target),
        )

    try:
        config = _load_mcp_config(resolved_config_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _error(
            str(exc),
            tool="grimoire_mcp_policy_report",
            project_root=str(target),
            config_path=_relative_display_path(resolved_config_path, base=target),
        )

    try:
        policy = _load_mcp_policy(target, policy_path)
    except (OSError, ValueError) as exc:
        return _error(
            str(exc),
            tool="grimoire_mcp_policy_report",
            project_root=str(target),
            policy_path=policy_path,
        )

    servers = [
        _classify_mcp_server(
            server_name,
            server_config if isinstance(server_config, dict) else {},
            policy=policy,
        )
        for server_name, server_config in sorted(config.get("servers", {}).items())
    ]

    status_counts = {"pass": 0, "warn": 0, "fail": 0}
    transport_counts: dict[str, int] = {}
    trust_level_counts: dict[str, int] = {}
    recommended_actions: list[str] = []

    for server in servers:
        status_counts[server["status"]] = status_counts.get(server["status"], 0) + 1
        transport = str(server["transport"])
        transport_counts[transport] = transport_counts.get(transport, 0) + 1
        trust_level = str(server["trust_level"])
        trust_level_counts[trust_level] = trust_level_counts.get(trust_level, 0) + 1

        flags = set(server["risk_flags"])
        if "hardcoded-secret" in flags:
            recommended_actions.append(
                f"{server['name']}: retirer tout secret en clair et passer par input/env/OAuth hors dépôt"
            )
        if "fail-closed-remote-deny" in flags:
            recommended_actions.append(
                f"{server['name']}: remote refusé par la policy, ajouter l'hôte à l'allowlist si l'usage est légitime"
            )
        elif "unreviewed-remote" in flags:
            recommended_actions.append(
                f"{server['name']}: ajouter ce remote à une allowlist explicite ou le désactiver"
            )
        if "local-command-execution" in flags and server["trust_level"] != "trusted-local":
            recommended_actions.append(
                f"{server['name']}: vérifier que la commande locale et son cwd restent attendus"
            )

    deduped_actions = list(dict.fromkeys(recommended_actions))

    return _dump_json(
        {
            "tool": "grimoire_mcp_policy_report",
            "project_root": str(target),
            "config_path": _relative_display_path(resolved_config_path, base=target),
            "policy": {
                "loaded": policy["loaded"],
                "source": policy["source"],
                "fail_closed_remote_hosts": policy["fail_closed_remote_hosts"],
                "trusted_remote_hosts": policy["trusted_remote_hosts"],
                "trusted_workspace_servers": policy["trusted_workspace_servers"],
            },
            "server_count": len(servers),
            "servers": servers,
            "summary": {
                "status_counts": status_counts,
                "transport_counts": transport_counts,
                "trust_level_counts": trust_level_counts,
                "recommended_actions": deduped_actions,
            },
        }
    )


@mcp.tool()
def grimoire_assets_generate_complete_baseline(
    project_path: str = ".",
    assets_root: str = "",
) -> str:
    """Generate the curated baseline asset set and refresh the asset index."""
    target = Path(project_path).resolve()
    resolved_assets_root = _resolve_assets_root(target, assets_root)
    if not resolved_assets_root:
        return _error(
            "Cannot resolve grimoire-game-assets root",
            tool="grimoire_assets_generate_complete_baseline",
            project_root=str(target),
            assets_root=assets_root or None,
        )

    return _run_assets_python_tool(
        "grimoire_assets_generate_complete_baseline",
        target,
        resolved_assets_root,
        "generate_complete_baseline.py",
        ["--assets-root", str(resolved_assets_root)],
    )


@mcp.tool()
def grimoire_assets_generate_character_action_variants(
    project_path: str = ".",
    assets_root: str = "",
) -> str:
    """Generate curated character action sheets and refresh the asset index."""
    target = Path(project_path).resolve()
    resolved_assets_root = _resolve_assets_root(target, assets_root)
    if not resolved_assets_root:
        return _error(
            "Cannot resolve grimoire-game-assets root",
            tool="grimoire_assets_generate_character_action_variants",
            project_root=str(target),
            assets_root=assets_root or None,
        )

    return _run_assets_python_tool(
        "grimoire_assets_generate_character_action_variants",
        target,
        resolved_assets_root,
        "generate_character_action_variants.py",
        ["--assets-root", str(resolved_assets_root)],
    )


@mcp.tool()
def grimoire_assets_extract_task_icons(
    project_path: str = ".",
    assets_root: str = "",
    source: str = "",
    output_dir: str = "",
    version: str = "v02",
    sat_thresh: float = 0.09,
    val_min: float = 0.78,
    fade_range: float = 0.05,
    padding: int = 8,
    dry_run: bool = False,
) -> str:
    """Extract individual task icons from the source sprite sheet."""
    target = Path(project_path).resolve()
    resolved_assets_root = _resolve_assets_root(target, assets_root)
    if not resolved_assets_root:
        return _error(
            "Cannot resolve grimoire-game-assets root",
            tool="grimoire_assets_extract_task_icons",
            project_root=str(target),
            assets_root=assets_root or None,
        )

    command = [
        "--version",
        version,
        "--sat-thresh",
        str(sat_thresh),
        "--val-min",
        str(val_min),
        "--fade-range",
        str(fade_range),
        "--padding",
        str(padding),
    ]
    if source:
        try:
            resolved_source = _resolve_path_within(
                source,
                base=resolved_assets_root,
                root=resolved_assets_root,
                label="source",
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_extract_task_icons",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="source",
                provided_path=source,
            )
        command.extend(
            [
                "--source",
                str(resolved_source),
            ]
        )
    if output_dir:
        try:
            resolved_output_dir = _resolve_path_within(
                output_dir,
                base=resolved_assets_root,
                root=resolved_assets_root,
                label="output_dir",
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_extract_task_icons",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="output_dir",
                provided_path=output_dir,
            )
        command.extend(
            [
                "--out",
                str(resolved_output_dir),
            ]
        )
    if dry_run:
        command.append("--dry-run")

    return _run_assets_python_tool(
        "grimoire_assets_extract_task_icons",
        target,
        resolved_assets_root,
        "extract_task_icons.py",
        command,
        dry_run=dry_run,
        version=version,
    )


@mcp.tool()
def grimoire_assets_publish_to_observatory(
    project_path: str = ".",
    assets_root: str = "",
    dry_run: bool = True,
    target_dir: str = "",
    curated_dir: str = "",
    index_file: str = "",
    sources_file: str = "",
    attribution_file: str = "",
) -> str:
    """Publish validated curated assets to the observatory target.

    Dry-run is enabled by default to preserve the fail-closed publication flow.
    """
    target = Path(project_path).resolve()
    resolved_assets_root = _resolve_assets_root(target, assets_root)
    if not resolved_assets_root:
        return _error(
            "Cannot resolve grimoire-game-assets root",
            tool="grimoire_assets_publish_to_observatory",
            project_root=str(target),
            assets_root=assets_root or None,
        )

    env = {
        "DRY_RUN": "1" if dry_run else "0",
    }
    workspace_root = resolved_assets_root.parent
    if target_dir:
        try:
            env["TARGET_DIR"] = str(
                _resolve_path_within(
                    target_dir,
                    base=target,
                    root=workspace_root,
                    label="target_dir",
                )
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_publish_to_observatory",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="target_dir",
                provided_path=target_dir,
            )
    if curated_dir:
        try:
            env["CURATED_DIR"] = str(
                _resolve_path_within(
                    curated_dir,
                    base=resolved_assets_root,
                    root=resolved_assets_root,
                    label="curated_dir",
                )
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_publish_to_observatory",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="curated_dir",
                provided_path=curated_dir,
            )
    if index_file:
        try:
            env["INDEX_FILE"] = str(
                _resolve_path_within(
                    index_file,
                    base=resolved_assets_root,
                    root=resolved_assets_root,
                    label="index_file",
                )
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_publish_to_observatory",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="index_file",
                provided_path=index_file,
            )
    if sources_file:
        try:
            env["SOURCES_FILE"] = str(
                _resolve_path_within(
                    sources_file,
                    base=resolved_assets_root,
                    root=resolved_assets_root,
                    label="sources_file",
                )
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_publish_to_observatory",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="sources_file",
                provided_path=sources_file,
            )
    if attribution_file:
        try:
            env["ATTRIBUTION_FILE"] = str(
                _resolve_path_within(
                    attribution_file,
                    base=resolved_assets_root,
                    root=resolved_assets_root,
                    label="attribution_file",
                )
            )
        except GrimoireError as exc:
            return _error(
                str(exc),
                tool="grimoire_assets_publish_to_observatory",
                project_root=str(target),
                assets_root=str(resolved_assets_root),
                parameter="attribution_file",
                provided_path=attribution_file,
            )

    return _run_assets_shell_tool(
        "grimoire_assets_publish_to_observatory",
        target,
        resolved_assets_root,
        "publish_to_observatory.sh",
        env=env,
        dry_run=dry_run,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_kit_root(start: Path) -> Path | None:
    """Walk up to find the kit directory containing archetypes/."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / "archetypes").is_dir():
            return parent.resolve()
        nested = parent / "grimoire-kit"
        if (nested / "archetypes").is_dir():
            return nested.resolve()
    return None


def _find_assets_root(start: Path) -> Path | None:
    """Walk up to find the grimoire-game-assets directory."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        for candidate in (parent, parent / "grimoire-game-assets"):
            if (candidate / "tools").is_dir() and (candidate / "10-curated").is_dir():
                return candidate.resolve()
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
