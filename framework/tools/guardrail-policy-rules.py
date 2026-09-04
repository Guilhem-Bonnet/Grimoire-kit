from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


DEFAULT_GUARDRAIL_RULES: dict[str, Any] = {
    "challenge": {
        "skipTests": [
            "sans test",
            "sans tests",
            "pas de test",
            "pas de tests",
            "no test",
            "no tests",
            "skip tests",
            "skip test",
        ],
        "skipReview": [
            "sans review",
            "pas de review",
            "skip review",
            "sans pr",
            "pas de pr",
            "sans ci",
            "pas de ci",
        ],
        "bypass": [
            "bypass les hooks",
            "bypass hook",
            "contourne les hooks",
            "contourner les hooks",
            "desactive les hooks",
            "desactive le hook",
            "disable hooks",
            "disable hook",
            "ignore le linter",
            "ignore le lint",
            "disable validation",
        ],
        "dirtyShortcut": [
            "vite fait",
            "quick and dirty",
            "hack",
            "bourrin",
            "juste faire marcher",
            "just make it work",
        ],
        "criticalDelivery": [
            "en prod direct",
            "directement en prod",
            "push direct sur main",
            "push direct sur master",
            "sans filet",
        ],
    },
    "followThrough": {
        "taskMap": {
            "quick-check": "grimoire: quickcheck",
            "memory-lint": "grimoire: memory-lint",
            "preflight": "grimoire: preflight",
        },
        "taskSpecs": {
            "grimoire: quickcheck": {
                "flow": "quality",
                "command": ["bash", "framework/tools/quick-check.sh"],
                "timeoutSeconds": 240,
            },
            "grimoire: memory-lint": {
                "flow": "memory",
                "command": [".venv/bin/python", "framework/tools/memory-lint.py", "--project-root", "."],
                "timeoutSeconds": 240,
            },
            "grimoire: preflight": {
                "flow": "quality",
                "command": [".venv/bin/python", "framework/tools/preflight-check.py", "--project-root", "."],
                "timeoutSeconds": 240,
            },
        },
    },
}


def project_context_root(project_root: Path) -> Path:
    candidate = project_root / "grimoire-kit"
    return candidate if candidate.exists() else project_root


def resolve_rules_file(project_root: Path) -> Path:
    return project_context_root(project_root) / "framework" / "tools" / "guardrail-policy-rules.yaml"


def load_guardrail_rules(project_root: Path) -> dict[str, Any]:
    rules, _status = _resolve_guardrail_rules(project_root)
    return rules


def guardrail_rules_status(project_root: Path) -> dict[str, Any]:
    _rules, status = _resolve_guardrail_rules(project_root)
    return status


def _resolve_guardrail_rules(project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rules = copy.deepcopy(DEFAULT_GUARDRAIL_RULES)
    rules_file = resolve_rules_file(project_root)
    status = {
        "source": "defaults",
        "rulesFile": str(rules_file),
        "yamlAvailable": yaml is not None,
        "rulesFileExists": rules_file.exists(),
        "warning": "",
    }

    if yaml is None:
        status["source"] = "default-no-yaml"
        status["warning"] = (
            f"PyYAML indisponible — fallback vers les règles guardrail par défaut "
            f"({rules_file})."
        )
        return rules, status

    if not rules_file.exists():
        status["source"] = "default-missing-file"
        status["warning"] = (
            f"Fichier guardrail-policy-rules.yaml absent — fallback vers les règles par défaut "
            f"({rules_file})."
        )
        return rules, status

    try:
        payload = yaml.safe_load(rules_file.read_text(encoding="utf-8"))
    except Exception as exc:
        status["source"] = "default-parse-error"
        status["warning"] = (
            f"guardrail-policy-rules.yaml invalide ({exc}) — fallback vers les règles par défaut."
        )
        return rules, status

    if not isinstance(payload, dict):
        status["source"] = "default-invalid-payload"
        status["warning"] = (
            "guardrail-policy-rules.yaml doit contenir un mapping racine — "
            "fallback vers les règles par défaut."
        )
        return rules, status

    status["source"] = "merged"
    return _merge_rules(rules, _normalize_rules_payload(payload)), status


def challenge_terms(rules: dict[str, Any], key: str) -> tuple[str, ...]:
    challenge = rules.get("challenge") if isinstance(rules, dict) else {}
    values = challenge.get(key) if isinstance(challenge, dict) else []
    if not isinstance(values, list):
        return ()
    return tuple(_normalize_string(value) for value in values if _normalize_string(value))


def follow_through_task_map(rules: dict[str, Any]) -> dict[str, str]:
    follow_through = rules.get("followThrough") if isinstance(rules, dict) else {}
    mapping = follow_through.get("taskMap") if isinstance(follow_through, dict) else {}
    if not isinstance(mapping, dict):
        return {}
    return {
        str(key): normalized
        for key, value in mapping.items()
        for normalized in [_normalize_string(value)]
        if normalized
    }


def follow_through_task_specs(rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    follow_through = rules.get("followThrough") if isinstance(rules, dict) else {}
    specs = follow_through.get("taskSpecs") if isinstance(follow_through, dict) else {}
    if not isinstance(specs, dict):
        return {}

    normalized_specs: dict[str, dict[str, Any]] = {}
    for label, raw_spec in specs.items():
        if not isinstance(raw_spec, dict):
            continue

        normalized_label = _normalize_string(label)
        flow = _normalize_string(raw_spec.get("flow")) or "quality"
        command = _normalize_string_list(raw_spec.get("command"))
        timeout_seconds = _normalize_timeout(raw_spec.get("timeoutSeconds"))
        if not normalized_label or not command:
            continue

        normalized_specs[normalized_label] = {
            "flow": flow,
            "command": command,
            "timeoutSeconds": timeout_seconds,
        }

    return normalized_specs


def _normalize_rules_payload(payload: dict[str, Any]) -> dict[str, Any]:
    challenge = payload.get("challenge") if isinstance(payload.get("challenge"), dict) else {}
    follow_through = payload.get("followThrough") if isinstance(payload.get("followThrough"), dict) else {}
    return {
        "challenge": {
            "skipTests": _normalize_string_list(challenge.get("skipTests")),
            "skipReview": _normalize_string_list(challenge.get("skipReview")),
            "bypass": _normalize_string_list(challenge.get("bypass")),
            "dirtyShortcut": _normalize_string_list(challenge.get("dirtyShortcut")),
            "criticalDelivery": _normalize_string_list(challenge.get("criticalDelivery")),
        },
        "followThrough": {
            "taskMap": follow_through_task_map({"followThrough": follow_through}),
            "taskSpecs": follow_through_task_specs({"followThrough": follow_through}),
        },
    }


def _merge_rules(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_rules(merged[key], value)
            continue
        merged[key] = copy.deepcopy(value)
    return merged


def _normalize_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [normalized for item in value for normalized in [_normalize_string(item)] if normalized]


def _normalize_timeout(value: Any) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 240
        if parsed > 0:
            return parsed
    return 240