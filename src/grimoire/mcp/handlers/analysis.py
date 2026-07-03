"""MCP tools — analysis, test recommendations, and diff impact tools."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from grimoire.mcp._helpers import (
    _dump_json,
    _error,
    _find_kit_root,
    _normalize_kit_relative_path,
    _parse_json_output,
    _parse_paths_input,
    _relative_display_path,
    _resolve_path,
    _run_subprocess,
)
from grimoire.mcp._instance import _CommandRecommendation, mcp

# ── Analysis helpers ──────────────────────────────────────────────────────────

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
        idx = posix_path.find(marker)
        if idx > 0:
            return posix_path[:idx]
    return None


def _impact_notes(categories: list[str]) -> list[str]:
    """Return brief human-readable notes based on impact categories."""
    notes: list[str] = []
    if "python-source" in categories:
        notes.append("Impacte le code Python source — tests unitaires recommandés.")
    if "python-tests" in categories:
        notes.append("Fichier de test modifié — vérifier couverture et assertions.")
    if "mcp-surface" in categories:
        notes.append("Surface MCP modifiée — valider les contrats d'outils.")
    if "game-runtime-source" in categories:
        notes.append("Code de jeu modifié — tests TypeScript requis.")
    if "skill-definition" in categories:
        notes.append("Définition de skill modifiée — vérifier la conformité YAML.")
    if "agent-definition" in categories:
        notes.append("Définition d'agent modifiée — vérifier les handoffs et triggers.")
    if "runtime-config" in categories:
        notes.append("Config runtime modifiée — vérifier la validité YAML et les champs requis.")
    if "asset-pipeline" in categories:
        notes.append("Pipeline d'assets modifié — régénérer le baseline si nécessaire.")
    return notes


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


def _recommended_commands(
    project_root: Path,
    path: str,
    categories: list[str],
    related_tests: list[str],
) -> list[_CommandRecommendation]:
    """Build a prioritized list of recommended commands for a changed path."""
    commands: list[_CommandRecommendation] = []
    kit_root = _find_kit_root(project_root)

    if "python-source" in categories or "python-tests" in categories or "python-tooling" in categories:
        if related_tests:
            for test_path in related_tests:
                commands.append(_CommandRecommendation(
                    kind="test",
                    value=_shell_join(["uv", "run", "pytest", test_path, "-x", "--tb=short"]),
                    reason=f"Test file linked to {path}",
                    priority=1,
                ))
        commands.append(_CommandRecommendation(
            kind="lint",
            value=_shell_join(["uv", "run", "ruff", "check", path]),
            reason="Lint modified Python file",
            priority=2,
        ))
        commands.append(_CommandRecommendation(
            kind="lint",
            value=_shell_join(["uv", "run", "ruff", "format", "--check", path]),
            reason="Format check modified Python file",
            priority=3,
        ))

    if "game-runtime-source" in categories or "game-tests" in categories:
        app_root = _game_app_root_from_path(path)
        if app_root and kit_root:
            abs_app_root = kit_root / app_root
            commands.append(_CommandRecommendation(
                kind="test",
                value=_shell_join(["npm", "--prefix", str(abs_app_root), "test", "--", "--run"]),
                reason=f"TypeScript tests for {path}",
                priority=1,
            ))

    if "skill-definition" in categories:
        commands.append(_CommandRecommendation(
            kind="validate",
            value="grimoire validate",
            reason="Validate skill definition YAML",
            priority=2,
        ))

    if "runtime-config" in categories:
        commands.append(_CommandRecommendation(
            kind="validate",
            value="grimoire validate",
            reason="Validate runtime config",
            priority=1,
        ))

    if "mcp-surface" in categories:
        commands.append(_CommandRecommendation(
            kind="test",
            value=_shell_join(["uv", "run", "pytest", "tests/unit/mcp/", "-x", "--tb=short"]),
            reason="Run MCP surface tests",
            priority=1,
        ))

    return commands


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


def _find_git_root(project_root: Path) -> Path:
    """Resolve the Git repository root when available."""
    result = _run_subprocess(["git", "rev-parse", "--show-toplevel"], cwd=project_root, timeout_seconds=30)
    if result["ok"]:
        stdout = result["stdout"].strip()
        if stdout:
            return Path(stdout).resolve()
    return project_root.resolve()


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


# ── Tools ─────────────────────────────────────────────────────────────────────

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
