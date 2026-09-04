from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOOK_SAFETY_GATE_VERSION = "0.3.1"

REGISTRY_RELATIVE_PATH = Path("_grimoire-runtime") / "_config" / "hook-safety-registry.json"
RUNTIME_LOG_RELATIVE_PATH = Path("_grimoire-runtime-output") / "hook-runtime" / "safety-gate" / "events.jsonl"
VALID_MODES = frozenset({"shadow", "canary", "enforced", "disabled"})
SHADOW_MODES = frozenset({"shadow", "canary"})
STATUS_ORDER = ("invalid", "pending", "modified", "shadow", "canary", "enforced", "disabled")


class HookSafetyGateError(RuntimeError):
    """Raised when the hook safety gate configuration is invalid."""


@dataclass(frozen=True, slots=True)
class HookSpec:
    hook_id: str
    event: str
    target: Path
    control_files: tuple[Path, ...]

    @property
    def fingerprint_paths(self) -> tuple[Path, ...]:
        return (self.target, *self.control_files)


@dataclass(frozen=True, slots=True)
class HookStatus:
    hook_id: str
    event: str
    configured_mode: str
    effective_mode: str
    state: str
    reason: str
    current_digest: str
    validated_digest: str
    missing_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HookExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    status: HookStatus


@dataclass(frozen=True, slots=True)
class ManifestAuditIssue:
    manifest_path: str
    event: str
    entry_index: int
    reason: str


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def registry_path(project_root: Path) -> Path:
    return project_root / REGISTRY_RELATIVE_PATH


def runtime_log_path(project_root: Path) -> Path:
    return project_root / RUNTIME_LOG_RELATIVE_PATH


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        msg = f"Registre hooks introuvable: {path}"
        raise HookSafetyGateError(msg)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Registre hooks invalide: {exc.msg}"
        raise HookSafetyGateError(msg) from exc
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        msg = "Le registre hooks doit contenir un mapping 'hooks'."
        raise HookSafetyGateError(msg)
    return payload


def save_registry(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def compute_digest(paths: tuple[Path, ...], project_root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item.relative_to(project_root))):
        relative = path.relative_to(project_root)
        hasher.update(str(relative).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def build_spec(project_root: Path, hook_id: str, event: str, target: str, control_files: list[str]) -> HookSpec:
    target_path = resolve_repo_path(project_root, target)
    control_paths = tuple(resolve_repo_path(project_root, item) for item in control_files)
    return HookSpec(hook_id=hook_id, event=event, target=target_path, control_files=control_paths)


def resolve_repo_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def get_registry_entry(registry: dict[str, Any], hook_id: str) -> dict[str, Any]:
    hooks = registry.setdefault("hooks", {})
    entry = hooks.setdefault(hook_id, {})
    if not isinstance(entry, dict):
        msg = f"L'entree hooks.{hook_id} doit etre un objet JSON."
        raise HookSafetyGateError(msg)
    return entry


def merge_control_files(explicit_control_files: list[str], registry_entry: dict[str, Any]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    registry_control_files = list(registry_entry.get("controlFiles") or [])

    for value in [*explicit_control_files, *registry_control_files]:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)

    return merged


def camel_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", value) if token]


def expand_registry_events(registry_event: str) -> set[str]:
    normalized = str(registry_event).strip()
    if not normalized:
        return set()

    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    if not parts:
        return set()

    expanded = {parts[0]}
    base_tokens = camel_tokens(parts[0])
    for part in parts[1:]:
        part_tokens = camel_tokens(part)
        if base_tokens and part_tokens and len(part_tokens) < len(base_tokens):
            prefix_tokens = base_tokens[: len(base_tokens) - len(part_tokens)]
            expanded.add("".join([*prefix_tokens, *part_tokens]))
            continue
        expanded.add(part)

    return expanded


def event_matches_registry(registry_event: str, manifest_event: str) -> bool:
    normalized_registry = str(registry_event).strip()
    normalized_manifest = str(manifest_event).strip()
    if not normalized_registry or not normalized_manifest:
        return False
    if normalized_registry == normalized_manifest:
        return True
    registry_events = expand_registry_events(normalized_registry)
    return normalized_manifest in registry_events


def gateway_script_path(project_root: Path) -> Path:
    return (project_root / ".github" / "hooks" / "scripts" / "grimoire-hook-gateway.sh").resolve()


def parse_gateway_command(command: str) -> tuple[Path | None, dict[str, list[str] | str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None, {}
    if not tokens:
        return None, {}

    parsed: dict[str, list[str] | str] = {"control-file": []}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"--hook-id", "--event", "--target", "--control-file"}:
            if index + 1 >= len(tokens):
                break
            value = tokens[index + 1]
            if token == "--control-file":
                control_files = parsed.setdefault("control-file", [])
                assert isinstance(control_files, list)
                control_files.append(value)
            else:
                parsed[token[2:]] = value
            index += 2
            continue
        index += 1

    return Path(tokens[0]), parsed


def audit_manifest_bindings(project_root: Path, registry: dict[str, Any]) -> list[ManifestAuditIssue]:
    hook_dir = project_root / ".github" / "hooks"
    expected_gateway = gateway_script_path(project_root)
    issues: list[ManifestAuditIssue] = []
    hooks = registry.get("hooks", {})

    for manifest in sorted(hook_dir.glob("*.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        entries = payload.get("hooks")
        if not isinstance(entries, dict):
            continue

        manifest_relative = str(manifest.relative_to(project_root))
        for event_name, commands in entries.items():
            if not isinstance(commands, list):
                continue
            for entry_index, command_entry in enumerate(commands, start=1):
                if not isinstance(command_entry, dict):
                    continue
                command = str(command_entry.get("command") or "").strip()
                command_path, parsed = parse_gateway_command(command)
                if command_path is None:
                    issues.append(
                        ManifestAuditIssue(manifest_relative, str(event_name), entry_index, "Commande hook vide ou invalide.")
                    )
                    continue

                resolved_command_path = resolve_repo_path(project_root, str(command_path))
                if resolved_command_path != expected_gateway:
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            "Le manifest bypass le gateway stable des hooks.",
                        )
                    )
                    continue

                hook_id = str(parsed.get("hook-id") or "")
                command_event = str(parsed.get("event") or "")
                command_target = str(parsed.get("target") or "")
                control_files = parsed.get("control-file") or []
                assert isinstance(control_files, list)

                if not hook_id:
                    issues.append(ManifestAuditIssue(manifest_relative, str(event_name), entry_index, "--hook-id manquant."))
                    continue
                if hook_id not in hooks:
                    issues.append(
                        ManifestAuditIssue(manifest_relative, str(event_name), entry_index, f"hook-id {hook_id} absent du registre."),
                    )
                    continue
                if command_event and command_event != str(event_name):
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            f"L'evenement du manifest ({event_name}) ne correspond pas a --event ({command_event}).",
                        )
                    )
                    continue
                if manifest_relative not in control_files:
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            "Le manifest courant doit etre declare en --control-file.",
                        )
                    )
                    continue

                registry_entry = get_registry_entry(registry, hook_id)
                registry_target = str(registry_entry.get("target") or "")
                registry_event = str(registry_entry.get("event") or "")
                registry_controls = list(registry_entry.get("controlFiles") or [])
                if not event_matches_registry(registry_event, str(event_name)):
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            f"Le registre declare l'evenement {registry_event or '∅'} au lieu de {event_name}.",
                        )
                    )
                if command_target and registry_target != command_target:
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            f"Le registre pointe vers {registry_target or '∅'} au lieu de {command_target}.",
                        )
                    )
                if manifest_relative not in registry_controls:
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            "Le manifest n'est pas reference dans controlFiles du registre.",
                        )
                    )
                for control_file in control_files:
                    if control_file == manifest_relative or control_file in registry_controls:
                        continue
                    issues.append(
                        ManifestAuditIssue(
                            manifest_relative,
                            str(event_name),
                            entry_index,
                            f"Le control-file {control_file} n'est pas reference dans controlFiles du registre.",
                        )
                    )

    return issues


def evaluate_hook_status(project_root: Path, spec: HookSpec, registry_entry: dict[str, Any]) -> HookStatus:
    configured_mode = str(registry_entry.get("mode") or "shadow")
    if configured_mode not in VALID_MODES:
        configured_mode = "shadow"

    missing_paths = tuple(
        str(path.relative_to(project_root)) for path in spec.fingerprint_paths if not path.exists() or not path.is_file()
    )
    validated_digest = str(registry_entry.get("validatedDigest") or "")

    if missing_paths:
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="shadow",
            state="invalid",
            reason="Fichier hook introuvable ou non lisible.",
            current_digest="",
            validated_digest=validated_digest,
            missing_paths=missing_paths,
        )

    current_digest = compute_digest(spec.fingerprint_paths, project_root)

    if configured_mode == "disabled":
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="disabled",
            state="disabled",
            reason="Hook desactive par registre.",
            current_digest=current_digest,
            validated_digest=validated_digest,
            missing_paths=(),
        )

    if not validated_digest:
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="shadow",
            state="pending",
            reason="Aucun digest valide enregistre.",
            current_digest=current_digest,
            validated_digest="",
            missing_paths=(),
        )

    if current_digest != validated_digest:
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="shadow",
            state="modified",
            reason="Le hook a change depuis la derniere validation.",
            current_digest=current_digest,
            validated_digest=validated_digest,
            missing_paths=(),
        )

    if configured_mode == "shadow":
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="shadow",
            state="shadow",
            reason="Hook force en shadow non bloquant.",
            current_digest=current_digest,
            validated_digest=validated_digest,
            missing_paths=(),
        )

    if configured_mode == "canary":
        return HookStatus(
            hook_id=spec.hook_id,
            event=spec.event,
            configured_mode=configured_mode,
            effective_mode="shadow",
            state="canary",
            reason="Hook promu en canary uniquement.",
            current_digest=current_digest,
            validated_digest=validated_digest,
            missing_paths=(),
        )

    return HookStatus(
        hook_id=spec.hook_id,
        event=spec.event,
        configured_mode=configured_mode,
        effective_mode="enforced",
        state="enforced",
        reason="Hook valide et enforce.",
        current_digest=current_digest,
        validated_digest=validated_digest,
        missing_paths=(),
    )


def parse_json_output(raw_output: str) -> tuple[dict[str, Any] | None, str | None]:
    text = raw_output.strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"stdout JSON invalide ({exc.msg})"
    if not isinstance(parsed, dict):
        return None, "stdout hook doit etre un objet JSON"
    return parsed, None


def should_warn_in_shadow(exit_code: int, parse_error: str | None, stderr: str) -> bool:
    return exit_code != 0 or parse_error is not None or bool(stderr.strip())


def shadow_output(hook_id: str, state: str, exit_code: int, parse_error: str | None, stderr: str) -> str:
    if not should_warn_in_shadow(exit_code, parse_error, stderr):
        return "{}\n"

    fragments = [f"etat={state}"]
    if exit_code != 0:
        fragments.append(f"exit={exit_code}")
    if parse_error:
        fragments.append(parse_error)
    if stderr.strip():
        fragments.append("stderr non vide")
    message = f"Hook {hook_id} en validation non bloquante: {'; '.join(fragments[:3])}."
    payload = {"continue": True, "systemMessage": message}
    return json.dumps(payload, ensure_ascii=True) + "\n"


def append_runtime_event(project_root: Path, spec: HookSpec, status: HookStatus, exit_code: int, stdout: str, stderr: str) -> None:
    path = runtime_log_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    stdout_excerpt = stdout.strip().replace("\n", " ")[:240]
    stderr_excerpt = stderr.strip().replace("\n", " ")[:240]
    entry = {
        "ts": now_iso(),
        "hookId": spec.hook_id,
        "event": spec.event,
        "state": status.state,
        "configuredMode": status.configured_mode,
        "effectiveMode": status.effective_mode,
        "currentDigest": status.current_digest,
        "validatedDigest": status.validated_digest,
        "exitCode": exit_code,
        "stdoutExcerpt": stdout_excerpt,
        "stderrExcerpt": stderr_excerpt,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def execute_target(project_root: Path, spec: HookSpec, raw_input: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(spec.target)],
        input=raw_input,
        capture_output=True,
        text=True,
        cwd=project_root,
        check=False,
    )


def invoke_hook(project_root: Path, spec: HookSpec, raw_input: str, registry: dict[str, Any]) -> HookExecutionResult:
    entry = get_registry_entry(registry, spec.hook_id)
    status = evaluate_hook_status(project_root, spec, entry)

    if status.effective_mode == "disabled":
        result = HookExecutionResult(exit_code=0, stdout="{}\n", stderr="", status=status)
        append_runtime_event(project_root, spec, status, result.exit_code, result.stdout, result.stderr)
        return result

    if status.state == "invalid":
        stdout = shadow_output(spec.hook_id, status.state, 1, "configuration invalide", "")
        result = HookExecutionResult(exit_code=0, stdout=stdout, stderr="", status=status)
        append_runtime_event(project_root, spec, status, result.exit_code, result.stdout, result.stderr)
        return result

    try:
        completed = execute_target(project_root, spec, raw_input)
    except OSError as exc:
        stdout = shadow_output(spec.hook_id, status.state, 1, str(exc), "")
        result = HookExecutionResult(exit_code=0, stdout=stdout, stderr="", status=status)
        append_runtime_event(project_root, spec, status, result.exit_code, result.stdout, result.stderr)
        return result

    if status.effective_mode == "enforced":
        result = HookExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            status=status,
        )
        append_runtime_event(project_root, spec, status, result.exit_code, result.stdout, result.stderr)
        return result

    _, parse_error = parse_json_output(completed.stdout)
    stdout = shadow_output(spec.hook_id, status.state, completed.returncode, parse_error, completed.stderr)
    result = HookExecutionResult(exit_code=0, stdout=stdout, stderr="", status=status)
    append_runtime_event(project_root, spec, status, completed.returncode, completed.stdout, completed.stderr)
    return result


def collect_statuses(project_root: Path, registry: dict[str, Any]) -> list[HookStatus]:
    statuses: list[HookStatus] = []
    hooks = registry.get("hooks", {})
    for hook_id in sorted(hooks):
        entry = get_registry_entry(registry, hook_id)
        target = str(entry.get("target") or "")
        control_files = list(entry.get("controlFiles") or [])
        event = str(entry.get("event") or "")
        if not target or not event:
            status = HookStatus(
                hook_id=hook_id,
                event=event,
                configured_mode=str(entry.get("mode") or "shadow"),
                effective_mode="shadow",
                state="invalid",
                reason="Entree registre incomplete.",
                current_digest="",
                validated_digest=str(entry.get("validatedDigest") or ""),
                missing_paths=(),
            )
        else:
            spec = build_spec(project_root, hook_id, event, target, control_files)
            status = evaluate_hook_status(project_root, spec, entry)
        statuses.append(status)
    return statuses


def set_hook_modes(project_root: Path, registry: dict[str, Any], hook_ids: list[str], mode: str) -> list[HookStatus]:
    if mode not in VALID_MODES:
        msg = f"Mode hook invalide: {mode}"
        raise HookSafetyGateError(msg)

    selected_ids = hook_ids or sorted(registry.get("hooks", {}).keys())
    updated: list[HookStatus] = []

    for hook_id in selected_ids:
        entry = get_registry_entry(registry, hook_id)
        target = str(entry.get("target") or "")
        event = str(entry.get("event") or "")
        control_files = list(entry.get("controlFiles") or [])
        if not target or not event:
            msg = f"Entree registre incomplete pour {hook_id}."
            raise HookSafetyGateError(msg)
        spec = build_spec(project_root, hook_id, event, target, control_files)
        status = evaluate_hook_status(project_root, spec, entry)
        if status.missing_paths:
            msg = f"Impossible de changer le mode de {hook_id}: chemins manquants ({', '.join(status.missing_paths)})."
            raise HookSafetyGateError(msg)

        entry["mode"] = mode
        if mode != "disabled" and status.current_digest:
            entry["validatedDigest"] = status.current_digest
            entry["validatedAt"] = now_iso()
        updated.append(evaluate_hook_status(project_root, spec, entry))

    return updated


def promote_hooks(project_root: Path, registry: dict[str, Any], hook_ids: list[str], mode: str) -> list[HookStatus]:
    if mode not in VALID_MODES - {"disabled"}:
        msg = f"Mode de promotion invalide: {mode}"
        raise HookSafetyGateError(msg)

    selected_ids = hook_ids or sorted(registry.get("hooks", {}).keys())
    updated: list[HookStatus] = []

    for hook_id in selected_ids:
        entry = get_registry_entry(registry, hook_id)
        target = str(entry.get("target") or "")
        event = str(entry.get("event") or "")
        control_files = list(entry.get("controlFiles") or [])
        if not target or not event:
            msg = f"Entree registre incomplete pour {hook_id}."
            raise HookSafetyGateError(msg)

        spec = build_spec(project_root, hook_id, event, target, control_files)
        status = evaluate_hook_status(project_root, spec, entry)
        if status.missing_paths:
            msg = f"Impossible de promouvoir {hook_id}: chemins manquants ({', '.join(status.missing_paths)})."
            raise HookSafetyGateError(msg)
        if not status.current_digest:
            msg = f"Impossible de promouvoir {hook_id}: digest courant indisponible."
            raise HookSafetyGateError(msg)

        entry["validatedDigest"] = status.current_digest
        entry["validatedAt"] = now_iso()
        entry["mode"] = mode
        updated.append(evaluate_hook_status(project_root, spec, entry))

    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hook safety gate for Grimoire hooks")
    parser.add_argument("--project-root", type=Path, default=default_project_root())

    subparsers = parser.add_subparsers(dest="command", required=True)

    invoke_parser = subparsers.add_parser("invoke")
    invoke_parser.add_argument("--hook-id", required=True)
    invoke_parser.add_argument("--event")
    invoke_parser.add_argument("--target")
    invoke_parser.add_argument("--control-file", action="append", default=[])

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--strict", action="store_true")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--only-state", action="append", choices=STATUS_ORDER, default=[])
    status_parser.add_argument("--hook-id", action="append", default=[])

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("hook_ids", nargs="*")
    promote_parser.add_argument("--mode", default="enforced")

    set_mode_parser = subparsers.add_parser("set-mode")
    set_mode_parser.add_argument("mode", choices=sorted(VALID_MODES))
    set_mode_parser.add_argument("hook_ids", nargs="*")

    return parser


def normalize_global_options(argv: list[str]) -> list[str]:
    project_root_args: list[str] = []
    passthrough: list[str] = []
    index = 0

    while index < len(argv):
        token = argv[index]
        if token == "--project-root":
            if index + 1 >= len(argv):
                return argv
            project_root_args = [token, argv[index + 1]]
            index += 2
            continue
        if token.startswith("--project-root="):
            project_root_args = [token]
            index += 1
            continue
        passthrough.append(token)
        index += 1

    return [*project_root_args, *passthrough]


def status_exit_code(statuses: list[HookStatus], strict: bool) -> int:
    if any(status.state == "invalid" for status in statuses):
        return 1
    if strict and any(status.state in {"pending", "modified", "shadow", "canary"} for status in statuses):
        return 1
    return 0


def manifest_audit_exit_code(issues: list[ManifestAuditIssue]) -> int:
    return 1 if issues else 0


def filter_statuses(statuses: list[HookStatus], states: set[str], hook_ids: set[str]) -> list[HookStatus]:
    filtered = statuses
    if states:
        filtered = [status for status in filtered if status.state in states]
    if hook_ids:
        filtered = [status for status in filtered if status.hook_id in hook_ids]
    return filtered


def summarize_statuses(statuses: list[HookStatus]) -> dict[str, int]:
    summary = {"total": len(statuses)}
    for state in STATUS_ORDER:
        summary[state] = sum(1 for status in statuses if status.state == state)
    return summary


def print_statuses(statuses: list[HookStatus], issues: list[ManifestAuditIssue], json_output: bool) -> None:
    if json_output:
        payload = {
            "summary": summarize_statuses(statuses),
            "statuses": [
                {
                    "hookId": status.hook_id,
                    "event": status.event,
                    "configuredMode": status.configured_mode,
                    "effectiveMode": status.effective_mode,
                    "state": status.state,
                    "reason": status.reason,
                    "currentDigest": status.current_digest,
                    "validatedDigest": status.validated_digest,
                    "missingPaths": list(status.missing_paths),
                }
                for status in statuses
            ],
            "manifestIssues": [
                {
                    "manifestPath": issue.manifest_path,
                    "event": issue.event,
                    "entryIndex": issue.entry_index,
                    "reason": issue.reason,
                }
                for issue in issues
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return

    summary = summarize_statuses(statuses)
    fragments = [f"total={summary['total']}"]
    for state in STATUS_ORDER:
        if summary[state] > 0:
            fragments.append(f"{state}={summary[state]}")
    print(f"Summary: {' '.join(fragments)}")

    for status in statuses:
        prefix = (
            "ERROR"
            if status.state == "invalid"
            else "WARN"
            if status.state in {"pending", "modified", "shadow", "canary"}
            else "OK"
        )
        print(
            f"[{prefix}] {status.hook_id} ({status.event}) mode={status.configured_mode} "
            f"state={status.state} reason={status.reason}"
        )

    for issue in issues:
        print(
            f"[ERROR] {issue.manifest_path} ({issue.event}[{issue.entry_index}]) reason={issue.reason}"
        )

    if any(status.state in {"pending", "modified", "shadow", "canary"} for status in statuses):
        print("Hint: use 'set-mode canary <hook_id>' or 'set-mode shadow <hook_id>' to test without enforcing.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(normalize_global_options(raw_argv))
    project_root = args.project_root.resolve()
    registry = load_registry(registry_path(project_root))

    if args.command == "invoke":
        entry = get_registry_entry(registry, args.hook_id)
        event = str(entry.get("event") or args.event)
        target = str(entry.get("target") or args.target)
        control_files = merge_control_files(list(args.control_file), entry)
        spec = build_spec(project_root, args.hook_id, event, target, control_files)
        result = invoke_hook(project_root, spec, sys.stdin.read(), registry)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    if args.command == "status":
        statuses = collect_statuses(project_root, registry)
        issues = audit_manifest_bindings(project_root, registry)
        statuses = filter_statuses(statuses, set(args.only_state), set(args.hook_id))
        print_statuses(statuses, issues, args.json)
        return max(status_exit_code(statuses, args.strict), manifest_audit_exit_code(issues))

    if args.command == "promote":
        promoted = promote_hooks(project_root, registry, args.hook_ids, args.mode)
        save_registry(registry_path(project_root), registry)
        for status in promoted:
            print(f"[PROMOTED] {status.hook_id} -> {status.configured_mode} ({status.current_digest[:12]})")
        return 0

    if args.command == "set-mode":
        updated = set_hook_modes(project_root, registry, args.hook_ids, args.mode)
        save_registry(registry_path(project_root), registry)
        for status in updated:
            print(f"[MODE] {status.hook_id} -> {status.configured_mode} ({status.current_digest[:12]})")
        return 0

    parser.error(f"Commande inconnue: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())