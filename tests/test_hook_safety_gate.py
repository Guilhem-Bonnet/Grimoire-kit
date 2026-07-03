"""Tests for hook-safety-gate.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "hook-safety-gate.py"
_SPEC = importlib.util.spec_from_file_location("hook_safety_gate", _TOOL_PATH)
hook_safety_gate = importlib.util.module_from_spec(_SPEC)
sys.modules["hook_safety_gate"] = hook_safety_gate
assert _SPEC.loader is not None
_SPEC.loader.exec_module(hook_safety_gate)


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_registry(tmp_path: Path, *, validated_digest: str = "", mode: str = "enforced") -> Path:
    registry_path = tmp_path / "_grimoire-runtime" / "_config" / "hook-safety-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "hooks": {
            "sample-hook": {
                "event": "PreToolUse",
                "target": ".github/hooks/scripts/sample-hook.sh",
                "controlFiles": [".github/hooks/sample-hook.json"],
                "mode": mode,
                "validatedDigest": validated_digest,
            }
        },
    }
    registry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return registry_path


def _sample_spec(tmp_path: Path):
    return hook_safety_gate.build_spec(
        tmp_path,
        "sample-hook",
        "PreToolUse",
        ".github/hooks/scripts/sample-hook.sh",
        [".github/hooks/sample-hook.json"],
    )


def _write_manifest(tmp_path: Path, command: str) -> Path:
    manifest_path = tmp_path / ".github" / "hooks" / "sample-hook.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hooks": {
            "PreToolUse": [
                {
                    "command": command,
                }
            ]
        }
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return manifest_path


def test_collect_status_marks_unvalidated_hook_pending(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '{}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path)

    registry = hook_safety_gate.load_registry(registry_path)
    statuses = hook_safety_gate.collect_statuses(tmp_path, registry)

    assert len(statuses) == 1
    assert statuses[0].state == "pending"
    assert statuses[0].effective_mode == "shadow"


def test_invoke_shadow_suppresses_pretool_decision(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\"}}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path)
    registry = hook_safety_gate.load_registry(registry_path)
    spec = _sample_spec(tmp_path)

    result = hook_safety_gate.invoke_hook(tmp_path, spec, '{"tool_name":"run_in_terminal"}', registry)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {}
    assert result.status.state == "pending"


def test_invoke_enforced_passthroughs_candidate_output(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\"}}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path)
    registry = hook_safety_gate.load_registry(registry_path)
    spec = _sample_spec(tmp_path)
    digest = hook_safety_gate.compute_digest(spec.fingerprint_paths, tmp_path)
    registry["hooks"]["sample-hook"]["validatedDigest"] = digest

    result = hook_safety_gate.invoke_hook(tmp_path, spec, '{"tool_name":"run_in_terminal"}', registry)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert result.status.state == "enforced"


def test_promote_updates_digest_and_mode(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '{}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path, mode="shadow")
    registry = hook_safety_gate.load_registry(registry_path)

    promoted = hook_safety_gate.promote_hooks(tmp_path, registry, ["sample-hook"], "enforced")

    assert promoted[0].state == "enforced"
    assert registry["hooks"]["sample-hook"]["validatedDigest"]
    assert registry["hooks"]["sample-hook"]["mode"] == "enforced"


def test_main_accepts_project_root_after_subcommand(tmp_path: Path, capsys) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '{}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    _write_registry(tmp_path)

    exit_code = hook_safety_gate.main(["status", "--project-root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["statuses"][0]["state"] == "pending"


def test_set_hook_modes_updates_hook_to_canary(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '{}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path, mode="enforced")
    registry = hook_safety_gate.load_registry(registry_path)

    updated = hook_safety_gate.set_hook_modes(tmp_path, registry, ["sample-hook"], "canary")

    assert updated[0].state == "canary"
    assert registry["hooks"]["sample-hook"]["mode"] == "canary"
    assert registry["hooks"]["sample-hook"]["validatedDigest"]


def test_set_hook_modes_updates_hook_to_shadow(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '{}'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id sample-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry_path = _write_registry(tmp_path, mode="enforced")
    registry = hook_safety_gate.load_registry(registry_path)

    updated = hook_safety_gate.set_hook_modes(tmp_path, registry, ["sample-hook"], "shadow")

    assert updated[0].state == "shadow"
    assert updated[0].effective_mode == "shadow"
    assert registry["hooks"]["sample-hook"]["mode"] == "shadow"


def test_print_statuses_includes_summary_counts(capsys) -> None:
    statuses = [
        hook_safety_gate.HookStatus(
            hook_id="pending-hook",
            event="PreToolUse",
            configured_mode="enforced",
            effective_mode="shadow",
            state="pending",
            reason="Aucun digest valide enregistre.",
            current_digest="abc",
            validated_digest="",
            missing_paths=(),
        ),
        hook_safety_gate.HookStatus(
            hook_id="enforced-hook",
            event="PostToolUse",
            configured_mode="enforced",
            effective_mode="enforced",
            state="enforced",
            reason="Hook valide et enforce.",
            current_digest="def",
            validated_digest="def",
            missing_paths=(),
        ),
    ]

    hook_safety_gate.print_statuses(statuses, [], json_output=False)
    output = capsys.readouterr().out

    assert "Summary:" in output
    assert "pending=1" in output
    assert "enforced=1" in output


def test_audit_manifest_bindings_rejects_direct_script_command(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\nprintf '{}\n'\n",
    )
    _write_manifest(tmp_path, ".github/hooks/scripts/sample-hook.sh")
    registry = hook_safety_gate.load_registry(_write_registry(tmp_path))

    issues = hook_safety_gate.audit_manifest_bindings(tmp_path, registry)

    assert len(issues) == 1
    assert "bypass le gateway" in issues[0].reason


def test_audit_manifest_bindings_rejects_unregistered_hook_id(tmp_path: Path) -> None:
    _write_script(
        tmp_path / ".github" / "hooks" / "scripts" / "sample-hook.sh",
        "#!/usr/bin/env bash\nprintf '{}\n'\n",
    )
    _write_manifest(
        tmp_path,
        ".github/hooks/scripts/grimoire-hook-gateway.sh --hook-id unknown-hook --event PreToolUse --target .github/hooks/scripts/sample-hook.sh --control-file .github/hooks/sample-hook.json",
    )
    registry = hook_safety_gate.load_registry(_write_registry(tmp_path))

    issues = hook_safety_gate.audit_manifest_bindings(tmp_path, registry)

    assert len(issues) == 1
    assert "absent du registre" in issues[0].reason