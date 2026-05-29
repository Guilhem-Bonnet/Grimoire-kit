"""Agentic standard profile setup and verification."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.data import framework_path

PROFILE_MAP_PATH = Path("agentic-standard/profile-map.yaml")
STANDARD_DIR = Path("_grimoire/standard")
EVIDENCE_DIR = Path("_grimoire-output/evidence")
CONTEXT_DIR = Path("_grimoire-output/context")
DECISION_DIR = Path("_grimoire-output/decisions")
EVENT_DIR = Path("_grimoire-output/events")
KNOWLEDGE_DIR = Path("_grimoire-output/knowledge")
SCORE_DIR = Path("_grimoire-output/standard")
STANDARD_PROFILE_FILE = STANDARD_DIR / "standard-profile.yaml"
LLM_PROVIDER_REGISTRY_FILE = STANDARD_DIR / "llm-provider-registry.yaml"
EVENT_JOURNAL_FILE = EVENT_DIR / "runtime-journal.jsonl"
SUPPORTED_PROVIDER_IDS = ("github-copilot", "openai", "anthropic", "google-gemini", "local")
SUPPORTED_PROVIDER_POLICIES = ("hosted-safe", "local-first", "mixed")
PROVIDER_ALIASES = {
    "copilot": "github-copilot",
    "github": "github-copilot",
    "github-copilot": "github-copilot",
    "codex": "openai",
    "openai": "openai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google-gemini",
    "google": "google-gemini",
    "google-gemini": "google-gemini",
    "ollama": "local",
    "local": "local",
}
PROVIDER_DEFAULT_MODELS = {
    "github-copilot": ("copilot-integrated-models",),
    "openai": ("gpt-5.5", "gpt-5.4", "gpt-5.3-codex"),
    "anthropic": ("claude-sonnet-4.6", "claude-opus-4.7", "claude-haiku-4.5"),
    "google-gemini": ("gemini-family",),
    "local": ("local-open-weight",),
}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
BOARD_STATES = {
    "proposed",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "accepted",
    "released",
    "archived",
}
REQUIRED_MEMORY_TYPES = {
    "session",
    "task",
    "project",
    "workspace",
    "organization",
    "procedural",
    "semantic",
    "episodic",
    "long_term",
    "external_knowledge_cache",
}
REQUIRED_DECISION_TYPES = {
    "task_prioritization",
    "context_source_selection",
    "memory_injection",
    "provider_routing",
    "agent_role_routing",
    "tool_authorization",
    "state_transition",
    "release_authorization",
}
KNOWN_HOOK_PHASES = {
    "pre_context_build",
    "post_context_build",
    "pre_provider_call",
    "post_provider_call",
    "pre_tool_call",
    "post_tool_call",
    "pre_state_transition",
    "post_state_transition",
    "pre_release",
    "on_failure",
    "on_rollback",
}
KNOWN_HOOK_ACTIONS = {
    "allow",
    "warn",
    "block",
    "redact",
    "reroute",
    "require_evidence",
    "escalate",
    "create_remediation",
    "rollback",
}


@dataclass(frozen=True, slots=True)
class StandardProfile:
    """Operational profile declared in ``profile-map.yaml``."""

    id: str
    display_name: str
    required_artifacts: tuple[str, ...]
    mapped_capabilities: tuple[str, ...]
    minimum_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StandardArtifact:
    """Generated file for a standard-aware project."""

    artifact_type: str
    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class StandardProviderDetection:
    """Non-secret signal showing whether a provider appears available locally."""

    id: str
    available: bool
    signals: tuple[str, ...]
    note: str


@dataclass(slots=True)
class StandardSetupResult:
    """Result of standard artifact setup."""

    profile: str
    project_root: Path
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        """True when files were written or would be written."""
        return bool(self.written)


@dataclass(slots=True)
class StandardCheck:
    """One content or structure check emitted by verification."""

    id: str
    severity: str
    message: str
    path: Path | None = None

    @property
    def is_error(self) -> bool:
        """True when this check must fail verification."""
        return self.severity == "error"


@dataclass(slots=True)
class StandardVerificationResult:
    """Verification result for a standard-aware project."""

    profile: str
    project_root: Path
    present: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    invalid_yaml: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[StandardCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when mandatory files exist, parseable YAML is valid, and no check fails."""
        return not self.missing and not self.invalid_yaml and not any(check.is_error for check in self.checks)

    @property
    def warning_count(self) -> int:
        """Number of warning checks."""
        return sum(1 for check in self.checks if check.severity == "warning") + len(self.warnings)

    @property
    def error_count(self) -> int:
        """Number of error checks, including missing files and invalid YAML."""
        return len(self.missing) + len(self.invalid_yaml) + sum(1 for check in self.checks if check.is_error)


@dataclass(frozen=True, slots=True)
class StandardRuntimeArtifact:
    """Generated runtime artifact path and data."""

    path: Path
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StandardGateResult:
    """Result of evaluating evidence gates for a task."""

    ok: bool
    task_id: str
    profile: str
    state: str | None
    missing: tuple[str, ...]
    checks: tuple[StandardCheck, ...]


@dataclass(frozen=True, slots=True)
class StandardScoreResult:
    """Compliance score computed from the current standard verification result."""

    ok: bool
    profile: str
    score: int
    threshold: int
    warnings: int
    errors: int
    output_path: Path


@dataclass(frozen=True, slots=True)
class StandardRemediationAction:
    """Structured remediation action proposed by standard audit."""

    check_id: str
    severity: str
    action: str
    path: Path | None
    message: str


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    return yaml


def normalize_provider_ids(provider_ids: Iterable[str]) -> tuple[str, ...]:
    """Normalize user-facing provider names to registry ids."""
    normalized: list[str] = []
    for raw_provider in provider_ids:
        for raw_part in str(raw_provider).split(","):
            provider = raw_part.strip().lower()
            if not provider:
                continue
            provider_id = PROVIDER_ALIASES.get(provider)
            if provider_id is None:
                available = ", ".join(SUPPORTED_PROVIDER_IDS)
                msg = f"Unknown LLM provider {raw_part!r}. Available: {available}"
                raise ValueError(msg)
            if provider_id not in normalized:
                normalized.append(provider_id)
    return tuple(normalized)


def _masked_env_signal(env: Mapping[str, str], *names: str) -> list[str]:
    return [f"env:{name}=set" for name in names if env.get(name)]


def detect_standard_providers(env: Mapping[str, str] | None = None) -> tuple[StandardProviderDetection, ...]:
    """Detect provider availability without reading or logging secret values."""
    environment = os.environ if env is None else env
    provider_signals: dict[str, list[str]] = {
        "github-copilot": [],
        "openai": [],
        "anthropic": [],
        "google-gemini": [],
        "local": [],
    }

    executable_signals = {
        "github-copilot": ("gh",),
        "openai": ("codex",),
        "anthropic": ("claude",),
        "google-gemini": ("gemini",),
        "local": ("ollama",),
    }
    for provider_id, executables in executable_signals.items():
        for executable in executables:
            if shutil.which(executable):
                provider_signals[provider_id].append(f"exe:{executable}")

    provider_signals["github-copilot"].extend(
        _masked_env_signal(environment, "GITHUB_COPILOT_TOKEN", "GITHUB_TOKEN", "VSCODE_PID")
    )
    provider_signals["openai"].extend(_masked_env_signal(environment, "OPENAI_API_KEY", "OPENAI_BASE_URL"))
    provider_signals["anthropic"].extend(_masked_env_signal(environment, "ANTHROPIC_API_KEY"))
    provider_signals["google-gemini"].extend(_masked_env_signal(environment, "GEMINI_API_KEY", "GOOGLE_API_KEY"))
    provider_signals["local"].extend(_masked_env_signal(environment, "OLLAMA_HOST"))

    notes = {
        "github-copilot": "Copilot availability still depends on the editor/CLI runtime authorization.",
        "openai": "OpenAI/Codex availability requires project-approved credentials.",
        "anthropic": "Claude availability requires project-approved credentials.",
        "google-gemini": "Gemini availability requires project-approved credentials.",
        "local": "Local providers still require explicit data classification.",
    }
    return tuple(
        StandardProviderDetection(
            id=provider_id,
            available=bool(provider_signals[provider_id]),
            signals=tuple(provider_signals[provider_id]),
            note=notes[provider_id],
        )
        for provider_id in SUPPORTED_PROVIDER_IDS
    )


def _profile_map_file() -> Path:
    path = framework_path() / PROFILE_MAP_PATH
    if not path.is_file():
        msg = f"Agentic standard profile map not found: {path}"
        raise FileNotFoundError(msg)
    return path


def load_profile_map() -> dict[str, Any]:
    """Load the bundled agentic standard profile map."""
    data = _yaml().load(_profile_map_file())
    if not isinstance(data, dict):
        msg = "Agentic standard profile map must be a YAML mapping."
        raise ValueError(msg)
    return data


def list_profiles() -> tuple[StandardProfile, ...]:
    """Return standard profiles declared by the bundled profile map."""
    data = load_profile_map()
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        msg = "profile-map.yaml must define a profiles list."
        raise ValueError(msg)

    parsed: list[StandardProfile] = []
    for raw in profiles:
        if not isinstance(raw, dict):
            msg = "Each profile entry must be a YAML mapping."
            raise ValueError(msg)
        parsed.append(StandardProfile(
            id=str(raw["id"]),
            display_name=str(raw.get("display_name", raw["id"])),
            required_artifacts=tuple(str(a) for a in raw.get("required_artifacts", ())),
            mapped_capabilities=tuple(str(c) for c in raw.get("mapped_capabilities", ())),
            minimum_evidence=tuple(str(e) for e in raw.get("minimum_evidence", ())),
        ))
    return tuple(parsed)


def get_profile(profile_id: str) -> StandardProfile:
    """Return one standard profile by id."""
    for profile in list_profiles():
        if profile.id == profile_id:
            return profile
    available = ", ".join(profile.id for profile in list_profiles())
    msg = f"Unknown agentic standard profile: {profile_id!r}. Available: {available}"
    raise ValueError(msg)


def _artifact_templates() -> dict[str, Path]:
    data = load_profile_map()
    artifact_types = data.get("artifact_types")
    if not isinstance(artifact_types, dict):
        msg = "profile-map.yaml must define artifact_types."
        raise ValueError(msg)

    templates: dict[str, Path] = {}
    fw = framework_path()
    for artifact_type, raw in artifact_types.items():
        if not isinstance(raw, dict) or "template" not in raw:
            msg = f"Artifact type {artifact_type!r} must declare a template."
            raise ValueError(msg)
        template_path = fw.parent / str(raw["template"])
        if not template_path.is_file():
            msg = f"Template for {artifact_type!r} not found: {template_path}"
            raise FileNotFoundError(msg)
        templates[str(artifact_type)] = template_path
    return templates


def _generation_targets() -> dict[str, str]:
    data = load_profile_map()
    generation_targets = data.get("generation_targets")
    if not isinstance(generation_targets, dict):
        msg = "profile-map.yaml must define generation_targets."
        raise ValueError(msg)

    targets: dict[str, str] = {}
    for group_name in ("project_root", "task_runtime"):
        entries = generation_targets.get(group_name, ())
        if not isinstance(entries, list):
            msg = f"generation_targets.{group_name} must be a list."
            raise ValueError(msg)
        for entry in entries:
            if not isinstance(entry, dict):
                msg = f"generation_targets.{group_name} entries must be mappings."
                raise ValueError(msg)
            artifact = str(entry["source_artifact"])
            targets[artifact] = str(entry["path"])
    return targets


def normalize_task_id(task_id: str) -> str:
    """Validate task ids before using them in generated paths."""
    normalized = str(task_id).strip()
    if not TASK_ID_PATTERN.fullmatch(normalized) or normalized in {".", ".."}:
        msg = (
            f"Invalid task_id {task_id!r}. Use 1-128 letters, numbers, dots, underscores, "
            "or hyphens, starting with a letter or number."
        )
        raise ValueError(msg)
    return normalized


def _ensure_inside_root(root: Path, path: Path, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        msg = f"{label} resolves outside project root: {path}"
        raise ValueError(msg)
    return resolved_path


def _is_inside_root(root: Path, path: Path) -> bool:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _format_destination(path_template: str, task_id: str) -> Path:
    return Path(path_template.replace("{task-id}", normalize_task_id(task_id)))


def _single_line_value(value: str) -> str:
    normalized = " ".join(str(value).split()).strip()
    return normalized or "Unnamed project"


def _yaml_double_quoted_value(value: str) -> str:
    return _single_line_value(value).replace("\\", "\\\\").replace('"', '\\"')


def _planned_artifacts(profile_id: str, *, task_id: str = "bootstrap") -> tuple[StandardArtifact, ...]:
    profile = get_profile(profile_id)
    templates = _artifact_templates()
    targets = _generation_targets()
    artifacts: list[StandardArtifact] = []

    for artifact_type in profile.required_artifacts:
        if artifact_type not in targets:
            msg = f"No generation target declared for artifact {artifact_type!r}."
            raise ValueError(msg)
        artifacts.append(StandardArtifact(
            artifact_type=artifact_type,
            source=templates[artifact_type],
            destination=_format_destination(targets[artifact_type], task_id),
        ))

    return tuple(artifacts)


def _render_template(
    template: str,
    *,
    project_name: str,
    profile: StandardProfile,
    generated_at: str,
) -> str:
    text_project_name = _single_line_value(project_name)
    yaml_project_name = _yaml_double_quoted_value(project_name)
    rendered = template
    rendered = rendered.replace("- Project:\n", f"- Project: {text_project_name}\n")
    rendered = rendered.replace("- Selected profile: `starter | controlled | orchestrated | governed | production`\n", f"- Selected profile: `{profile.id}`\n")
    rendered = rendered.replace("- Declared profile: `starter | controlled | orchestrated | governed | production`\n", f"- Declared profile: `{profile.id}`\n")
    rendered = rendered.replace("- Upstream standard reference:\n", "- Upstream standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Standard reference:\n", "- Standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Date:\n", f"- Date: {generated_at}\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{yaml_project_name}\"\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{yaml_project_name}\"\n")
    return rendered


def _manifest_content(profile: StandardProfile, project_name: str, task_id: str, artifacts: tuple[StandardArtifact, ...]) -> str:
    data = {
        "$schema": "grimoire-agentic-standard-profile/v1",
        "project": project_name,
        "profile": profile.id,
        "display_name": profile.display_name,
        "task_id": task_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "upstream_standard": {
            "repository": "processus-developpement-agentique",
            "entrypoint": "docs/norme-structure-agentique.md",
            "matrix": "docs/matrice-normative-maitresse.md",
        },
        "required_artifacts": list(profile.required_artifacts),
        "mapped_capabilities": list(profile.mapped_capabilities),
        "minimum_evidence": list(profile.minimum_evidence),
        "artifacts": [
            {
                "type": artifact.artifact_type,
                "path": str(artifact.destination),
            }
            for artifact in artifacts
        ],
    }
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return stream.getvalue()


def _provider_data_policy(provider_id: str, provider_policy: str) -> dict[str, Any]:
    if provider_policy not in SUPPORTED_PROVIDER_POLICIES:
        available = ", ".join(SUPPORTED_PROVIDER_POLICIES)
        msg = f"Unknown provider policy {provider_policy!r}. Available: {available}"
        raise ValueError(msg)

    hosted_allowed = ["public-docs", "project-source", "generated-artifacts", "non-secret-metadata"]
    hosted_forbidden = ["secrets", "credentials", "personal-data", "regulated-data"]
    if provider_id == "local":
        allowed = [*hosted_allowed, "sensitive-local-only"]
        forbidden = ["secrets-without-redaction", "regulated-data-without-approval"]
        retention = "Local execution still requires explicit data classification."
    else:
        allowed = hosted_allowed
        forbidden = hosted_forbidden
        retention = "Hosted provider use requires project-approved credentials and data policy approval."

    if provider_policy == "local-first" and provider_id != "local":
        retention = "Disabled unless local execution cannot satisfy the declared capability."
    elif provider_policy == "mixed":
        retention = "Allowed only when capability and data-policy routing both match."
    return {
        "allowed_data_classes": allowed,
        "forbidden_data_classes": forbidden,
        "retention_notes": retention,
    }


def configure_provider_registry(
    project_root: Path,
    *,
    provider_ids: Iterable[str],
    provider_policy: str = "hosted-safe",
) -> Path:
    """Enable selected providers in an existing generated provider registry."""
    selected = normalize_provider_ids(provider_ids)
    if not selected:
        msg = "At least one provider must be selected."
        raise ValueError(msg)

    root = project_root.resolve()
    registry_path = root / LLM_PROVIDER_REGISTRY_FILE
    if not registry_path.is_file():
        msg = f"Selected profile has no provider registry to configure: {LLM_PROVIDER_REGISTRY_FILE}"
        raise FileNotFoundError(msg)

    data = _yaml().load(registry_path)
    if not isinstance(data, dict):
        msg = f"{LLM_PROVIDER_REGISTRY_FILE} must be a YAML mapping."
        raise ValueError(msg)
    providers = data.get("providers")
    if not isinstance(providers, list):
        msg = f"{LLM_PROVIDER_REGISTRY_FILE} must define a providers list."
        raise ValueError(msg)

    declared = {str(provider.get("id")) for provider in providers if isinstance(provider, dict) and provider.get("id")}
    missing = [provider_id for provider_id in selected if provider_id not in declared]
    if missing:
        msg = f"Selected providers are not declared in the registry template: {', '.join(missing)}"
        raise ValueError(msg)

    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id", ""))
        enabled = provider_id in selected
        provider["enabled"] = enabled
        if provider_id in PROVIDER_DEFAULT_MODELS:
            provider["default_models"] = list(PROVIDER_DEFAULT_MODELS[provider_id])
        provider["data_policy"] = _provider_data_policy(provider_id, provider_policy)
        provider["fallback_order"] = [candidate for candidate in selected if candidate != provider_id]

    routing = data.get("routing")
    if not isinstance(routing, dict):
        routing = {}
        data["routing"] = routing
    routing["default_provider"] = selected[0]
    routing["default_fallback_chain"] = list(selected)
    routing["require_capability_match"] = True
    routing["require_data_policy_match"] = True

    stream = io.StringIO()
    _yaml().dump(data, stream)
    registry_path.write_text(stream.getvalue(), encoding="utf-8")
    return LLM_PROVIDER_REGISTRY_FILE


def setup_standard_profile(
    project_root: Path,
    *,
    profile_id: str,
    task_id: str = "bootstrap",
    project_name: str | None = None,
    provider_ids: Iterable[str] = (),
    provider_policy: str = "hosted-safe",
    force: bool = False,
    dry_run: bool = False,
) -> StandardSetupResult:
    """Generate standard-aware project artifacts for one profile."""
    root = project_root.resolve()
    profile = get_profile(profile_id)
    artifacts = _planned_artifacts(profile_id, task_id=task_id)
    name = project_name or root.name
    generated_at = datetime.now(UTC).date().isoformat()
    result = StandardSetupResult(profile=profile.id, project_root=root, dry_run=dry_run)

    for artifact in artifacts:
        dst = root / artifact.destination
        _ensure_inside_root(root, dst, label=f"Artifact {artifact.artifact_type!r}")
        if dst.exists() and not force:
            result.skipped.append(artifact.destination)
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            template = artifact.source.read_text(encoding="utf-8")
            dst.write_text(_render_template(template, project_name=name, profile=profile, generated_at=generated_at), encoding="utf-8")
        result.written.append(artifact.destination)

    manifest_dst = root / STANDARD_PROFILE_FILE
    if manifest_dst.exists() and not force:
        result.skipped.append(STANDARD_PROFILE_FILE)
    else:
        if not dry_run:
            manifest_dst.parent.mkdir(parents=True, exist_ok=True)
            manifest_dst.write_text(_manifest_content(profile, name, task_id, artifacts), encoding="utf-8")
        result.written.append(STANDARD_PROFILE_FILE)

    selected_providers = normalize_provider_ids(provider_ids)
    if selected_providers:
        if dry_run:
            result.written.append(LLM_PROVIDER_REGISTRY_FILE)
        else:
            configured = configure_provider_registry(
                root,
                provider_ids=selected_providers,
                provider_policy=provider_policy,
            )
            if configured not in result.written:
                result.written.append(configured)

    return result


def _read_manifest_profile(project_root: Path) -> str | None:
    manifest = project_root / STANDARD_PROFILE_FILE
    if not manifest.is_file():
        return None
    data = _yaml().load(manifest)
    if not isinstance(data, dict):
        msg = f"{STANDARD_PROFILE_FILE} must be a YAML mapping."
        raise ValueError(msg)
    profile = data.get("profile")
    return str(profile) if profile else None


def _load_yaml_file(root: Path, rel_path: Path, result: StandardVerificationResult) -> Any | None:
    path = root / rel_path
    if not path.is_file():
        return None
    try:
        data = _yaml().load(path)
    except YAMLError as exc:
        result.invalid_yaml.append(rel_path)
        result.checks.append(StandardCheck(
            id="yaml.invalid",
            severity="error",
            path=rel_path,
            message=f"{rel_path}: {exc}",
        ))
        return None
    if data is None:
        result.invalid_yaml.append(rel_path)
        result.checks.append(StandardCheck(
            id="yaml.empty",
            severity="error",
            path=rel_path,
            message=f"{rel_path}: empty YAML document",
        ))
    return data


def _add_check(
    result: StandardVerificationResult,
    check_id: str,
    severity: str,
    message: str,
    *,
    path: Path | None = None,
) -> None:
    result.checks.append(StandardCheck(id=check_id, severity=severity, message=message, path=path))


def _text_file(root: Path, rel_path: Path) -> str:
    path = root / rel_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _verify_manifest(
    root: Path,
    profile: StandardProfile,
    task_id: str,
    result: StandardVerificationResult,
) -> None:
    rel_path = STANDARD_PROFILE_FILE
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    if data.get("profile") != profile.id:
        _add_check(
            result,
            "manifest.profile_mismatch",
            "error",
            f"Manifest profile is {data.get('profile')!r}, expected {profile.id!r}.",
            path=rel_path,
        )
    if not data.get("project"):
        _add_check(result, "manifest.project_missing", "warning", "Manifest has no project name.", path=rel_path)
    if data.get("task_id") != task_id:
        _add_check(
            result,
            "manifest.task_id_mismatch",
            "warning",
            f"Manifest task_id is {data.get('task_id')!r}, expected {task_id!r}.",
            path=rel_path,
        )

    declared = {str(item) for item in data.get("required_artifacts", ())}
    expected = set(profile.required_artifacts)
    if declared != expected:
        _add_check(
            result,
            "manifest.required_artifacts_mismatch",
            "warning",
            "Manifest required_artifacts differs from the bundled profile map.",
            path=rel_path,
        )


def _verify_mission_brief(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "mission-brief.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    if "- Project:" not in text or "- Project: \n" in text:
        _add_check(result, "mission.project_missing", "warning", "Mission brief has no project name.", path=rel_path)
    if f"- Selected profile: `{profile.id}`" not in text:
        _add_check(result, "mission.profile_missing", "error", "Mission brief does not declare the selected profile.", path=rel_path)
    if "processus-developpement-agentique/docs/norme-structure-agentique.md" not in text:
        _add_check(result, "mission.upstream_missing", "warning", "Mission brief does not reference the upstream standard.", path=rel_path)


def _verify_provider_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "llm-provider-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        _add_check(result, "providers.none", "error", "Provider registry must declare at least one provider.", path=rel_path)
        return

    provider_ids = {
        str(provider.get("id"))
        for provider in providers
        if isinstance(provider, dict) and provider.get("id")
    }
    enabled = [provider for provider in providers if isinstance(provider, dict) and provider.get("enabled") is True]
    enabled_ids = {
        str(provider.get("id"))
        for provider in enabled
        if provider.get("id")
    }
    if profile.id in {"controlled", "orchestrated", "governed", "production"} and not enabled:
        _add_check(
            result,
            "providers.none_enabled",
            "warning",
            "No LLM provider is enabled; repeatable provider-neutral routing is not configured yet.",
            path=rel_path,
        )

    for provider in providers:
        if not isinstance(provider, dict):
            _add_check(result, "providers.invalid_entry", "error", "Provider entries must be mappings.", path=rel_path)
            continue
        provider_id = provider.get("id")
        if not provider_id:
            _add_check(result, "providers.id_missing", "error", "Provider entry has no id.", path=rel_path)
        if not isinstance(provider.get("enabled"), bool):
            _add_check(result, "providers.enabled_not_bool", "error", f"Provider {provider_id!r} enabled flag must be boolean.", path=rel_path)
        if provider.get("enabled") is True:
            capabilities = provider.get("allowed_capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                _add_check(result, "providers.capabilities_missing", "error", f"Enabled provider {provider_id!r} has no capabilities.", path=rel_path)
            models = provider.get("default_models")
            if not isinstance(models, list) or not models:
                _add_check(result, "providers.models_missing", "warning", f"Enabled provider {provider_id!r} has no default models.", path=rel_path)
            data_policy = provider.get("data_policy")
            if not isinstance(data_policy, dict):
                _add_check(result, "providers.data_policy_missing", "error", f"Enabled provider {provider_id!r} has no data policy.", path=rel_path)
            else:
                forbidden = data_policy.get("forbidden_data_classes")
                if provider.get("provider_type") == "hosted" and (not isinstance(forbidden, list) or not forbidden):
                    _add_check(result, "providers.hosted_forbidden_missing", "error", f"Hosted provider {provider_id!r} has no forbidden data classes.", path=rel_path)
        fallback_order = provider.get("fallback_order", [])
        if isinstance(fallback_order, list):
            unknown_fallbacks = [str(candidate) for candidate in fallback_order if str(candidate) not in provider_ids]
            if unknown_fallbacks:
                _add_check(
                    result,
                    "providers.unknown_fallback",
                    "error",
                    f"Provider {provider_id!r} references unknown fallback(s): {', '.join(unknown_fallbacks)}.",
                    path=rel_path,
                )

    routing = data.get("routing")
    if not isinstance(routing, dict):
        _add_check(result, "providers.routing_missing", "warning", "Provider registry has no routing policy.", path=rel_path)
    else:
        default_provider = str(routing.get("default_provider", ""))
        if enabled and default_provider not in provider_ids:
            _add_check(result, "providers.default_unknown", "error", f"Default provider {default_provider!r} is not declared.", path=rel_path)
        elif enabled and default_provider not in enabled_ids:
            _add_check(result, "providers.default_not_enabled", "error", f"Default provider {default_provider!r} is not enabled.", path=rel_path)
        fallback_chain = routing.get("default_fallback_chain", [])
        if isinstance(fallback_chain, list):
            unknown = [str(candidate) for candidate in fallback_chain if str(candidate) not in provider_ids]
            if unknown:
                _add_check(result, "providers.routing_unknown_fallback", "error", f"Routing references unknown fallback(s): {', '.join(unknown)}.", path=rel_path)
        if routing.get("require_capability_match") is not True or routing.get("require_data_policy_match") is not True:
            _add_check(result, "providers.routing_policy_weak", "warning", "Routing should require capability and data-policy matches.", path=rel_path)


def _verify_knowledge_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "knowledge-source-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    rules = data.get("rules")
    if not isinstance(rules, dict):
        _add_check(result, "knowledge.rules_missing", "error", "Knowledge registry must declare separation rules.", path=rel_path)
    else:
        for key in ("knowledge_is_not_memory", "knowledge_is_not_session_context", "explicit_source_of_truth_required"):
            if rules.get(key) is not True:
                _add_check(result, f"knowledge.{key}", "error", f"Rule {key} must be true.", path=rel_path)

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        _add_check(result, "knowledge.sources_missing", "warning", "No external knowledge source is declared.", path=rel_path)
        return

    real_sources = 0
    for source in sources:
        if not isinstance(source, dict):
            _add_check(result, "knowledge.invalid_source", "error", "Knowledge source entries must be mappings.", path=rel_path)
            continue
        source_id = str(source.get("id", "")).strip()
        locator = str(source.get("locator", "")).strip()
        source_type = str(source.get("type", "")).strip()
        if source_id and locator and "|" not in source_type:
            real_sources += 1
        if source.get("enabled") is True and not locator:
            _add_check(result, "knowledge.locator_missing", "warning", "Enabled knowledge source has no locator.", path=rel_path)
        local_locator = locator.startswith((".", "/", "~"))
        if source.get("enabled") is True and locator and source_type == "folder":
            local_locator = True
        if source.get("enabled") is True and locator and local_locator:
            locator_path = (root / locator).resolve()
            if not _is_inside_root(root, locator_path):
                _add_check(result, "knowledge.locator_outside_root", "error", f"Knowledge source {source_id!r} locator must stay within project root: {locator}", path=rel_path)
            elif not locator_path.exists():
                _add_check(result, "knowledge.locator_not_found", "warning", f"Knowledge source {source_id!r} locator does not exist: {locator}", path=rel_path)
        trust = source.get("trust")
        if source.get("enabled") is True and not isinstance(trust, dict):
            _add_check(result, "knowledge.trust_missing", "error", f"Knowledge source {source_id!r} has no trust block.", path=rel_path)
        elif isinstance(trust, dict) and not trust.get("level"):
            _add_check(result, "knowledge.trust_level_missing", "warning", f"Knowledge source {source_id!r} has no trust level.", path=rel_path)
        elif isinstance(trust, dict) and trust.get("source_of_truth") is True and trust.get("level") not in {"high", "authoritative"}:
            _add_check(result, "knowledge.truth_low_trust", "warning", f"Source of truth {source_id!r} should have high or authoritative trust.", path=rel_path)
        evidence = source.get("evidence")
        if source.get("enabled") is True and isinstance(evidence, dict):
            manifest = str(evidence.get("index_manifest", "")).strip()
            if manifest and manifest != "planned":
                manifest_path = root / manifest
                if not _is_inside_root(root, manifest_path):
                    _add_check(result, "knowledge.index_manifest_outside_root", "error", f"Knowledge source {source_id!r} index manifest must stay within project root: {manifest}", path=rel_path)
                elif not manifest_path.exists():
                    _add_check(result, "knowledge.index_manifest_missing", "warning", f"Knowledge source {source_id!r} index manifest is missing: {manifest}", path=rel_path)

    if profile.id in {"orchestrated", "governed", "production"} and real_sources == 0:
        _add_check(
            result,
            "knowledge.no_real_source",
            "warning",
            "Profile expects indexed external knowledge, but only placeholder sources are present.",
            path=rel_path,
        )


def _verify_task_envelope(root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult) -> None:
    rel_path = EVIDENCE_DIR / task_id / "task-envelope.md"
    text = _text_file(root, rel_path)
    if not text:
        return

    strict_profile = profile.id in {"governed", "production"}
    if "- Current state: `intake | planned | executing | validating | blocked | done`" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(result, "task.state_placeholder", severity, "Task envelope still contains the state placeholder.", path=rel_path)
    if "|  |  |  |  |  |" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(result, "task.context_placeholder", severity, "Context orchestration table has no concrete selection.", path=rel_path)
    if "|  | read-only |  |  |" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(result, "task.tool_boundary_placeholder", severity, "Tool boundary is not concretely scoped.", path=rel_path)
    if "pending |" in text.lower() and strict_profile:
        _add_check(result, "task.pending_gate", "error", "Governed and production profiles cannot keep pending task gates.", path=rel_path)


def _verify_evidence_pack(root: Path, task_id: str, result: StandardVerificationResult) -> None:
    rel_path = EVIDENCE_DIR / task_id / "evidence-pack.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    if "pending" in text.lower():
        _add_check(result, "evidence.pending_gate", "warning", "Evidence pack still contains pending gates.", path=rel_path)
    if "- Outcome:\n" in text or "- Final state:\n" in text:
        _add_check(result, "evidence.summary_placeholder", "warning", "Evidence pack summary is still placeholder-only.", path=rel_path)
    if "|  |  |  |  |" in text:
        _add_check(result, "evidence.inventory_placeholder", "warning", "Evidence inventory has no concrete evidence rows.", path=rel_path)


def _verify_compliance_declaration(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "compliance-declaration.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    unknown_count = text.count("| unknown |")
    if unknown_count:
        _add_check(
            result,
            "compliance.unknown_status",
            "warning",
            f"Compliance declaration still has {unknown_count} unknown status rows.",
            path=rel_path,
        )
    if "- Declaration owner:\n" in text:
        _add_check(result, "compliance.owner_missing", "warning", "Compliance declaration has no owner.", path=rel_path)


def _verify_profile_specific_controls(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    if profile.id not in {"governed", "production"}:
        return

    compliance = _text_file(root, STANDARD_DIR / "compliance-declaration.md").lower()
    mission = _text_file(root, STANDARD_DIR / "mission-brief.md").lower()
    combined = f"{compliance}\n{mission}"
    required_terms = {
        "governed": ("environment", "workspace", "telemetry"),
        "production": ("environment", "workspace", "telemetry", "dry-run", "rollback", "slo"),
    }
    for term in required_terms[profile.id]:
        if term not in combined:
            _add_check(
                result,
                f"profile.{term.replace('-', '_')}_missing",
                "warning",
                f"Profile {profile.id!r} should declare {term} controls.",
                path=STANDARD_DIR / "compliance-declaration.md",
            )


def _require_keys(
    result: StandardVerificationResult,
    *,
    path: Path,
    check_prefix: str,
    data: dict[str, Any],
    keys: Iterable[str],
    severity: str = "error",
) -> None:
    for key in keys:
        if key not in data or data[key] in (None, ""):
            _add_check(result, f"{check_prefix}.{key}_missing", severity, f"{path}: required key {key!r} is missing.", path=path)


def _verify_task_board(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "task-board.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    states = data.get("states")
    if not isinstance(states, list) or not set(BOARD_STATES) <= {str(state) for state in states}:
        _add_check(result, "board.states_incomplete", "error", "Task board must declare the normative lifecycle states.", path=rel_path)
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        _add_check(result, "board.tasks_missing", "error", "Task board must declare a tasks list.", path=rel_path)
        return
    if not tasks:
        _add_check(result, "board.no_tasks", "warning", "Task board has no declared tasks yet.", path=rel_path)
        return
    for task in tasks:
        if not isinstance(task, dict):
            _add_check(result, "board.task_invalid", "error", "Task board entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="board.task",
            data=task,
            keys=("task_id", "title", "status", "acceptance_criteria", "evidence_pack_ref"),
        )
        task_id = str(task.get("task_id", "")).strip()
        if task_id:
            try:
                normalize_task_id(task_id)
            except ValueError as exc:
                _add_check(result, "board.task_id_invalid", "error", str(exc), path=rel_path)
        status = str(task.get("status", "")).strip()
        if status and status not in BOARD_STATES:
            _add_check(result, "board.status_invalid", "error", f"Task {task_id!r} has invalid status {status!r}.", path=rel_path)
        if status == "blocked" and not task.get("blockers"):
            _add_check(result, "board.blocker_reason_missing", "error", f"Task {task_id!r} is blocked without blocker details.", path=rel_path)
        if profile.id in {"governed", "production"} and status in {"accepted", "released"} and not task.get("decision_trace_ref"):
            _add_check(result, "board.decision_trace_missing", "error", f"Task {task_id!r} requires a decision trace.", path=rel_path)
        for ref_key in ("context_bundle_ref", "decision_trace_ref", "evidence_pack_ref", "remediation_ref"):
            ref = str(task.get(ref_key, "")).strip()
            if ref and not _is_inside_root(root, root / ref):
                _add_check(result, f"board.{ref_key}_outside_root", "error", f"Task {task_id!r} {ref_key} escapes project root.", path=rel_path)


def _verify_memory_policy(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "memory-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    memory_types = data.get("memory_types")
    if not isinstance(memory_types, list) or not memory_types:
        _add_check(result, "memory.types_missing", "error", "Memory policy must declare memory_types.", path=rel_path)
        return
    declared = {str(entry.get("type")) for entry in memory_types if isinstance(entry, dict)}
    missing = sorted(REQUIRED_MEMORY_TYPES - declared)
    if missing:
        _add_check(result, "memory.required_types_missing", "error", f"Memory policy misses required type(s): {', '.join(missing)}.", path=rel_path)
    for entry in memory_types:
        if not isinstance(entry, dict):
            _add_check(result, "memory.type_invalid", "error", "Memory type entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="memory",
            data=entry,
            keys=(
                "memory_id",
                "type",
                "scope",
                "read_policy",
                "write_policy",
                "retention",
                "freshness",
                "trust_level",
                "redaction_policy",
                "provider_compatibility",
                "allowed_context_uses",
            ),
        )
        if not isinstance(entry.get("provider_compatibility"), list):
            _add_check(result, "memory.provider_compatibility_invalid", "error", "Memory provider compatibility must be a list.", path=rel_path)
        if not isinstance(entry.get("allowed_context_uses"), list):
            _add_check(result, "memory.context_uses_invalid", "error", "Memory allowed context uses must be a list.", path=rel_path)


def _verify_context_contract(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "context-contract.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    _require_keys(result, path=rel_path, check_prefix="context", data=data, keys=("inputs", "bundle_sections", "budget", "redaction", "checks"))
    for key in ("inputs", "bundle_sections", "checks"):
        if not isinstance(data.get(key), list) or not data.get(key):
            _add_check(result, f"context.{key}_invalid", "error", f"Context contract {key} must be a non-empty list.", path=rel_path)
    budget = data.get("budget")
    if not isinstance(budget, dict):
        _add_check(result, "context.budget_invalid", "error", "Context contract budget must be a mapping.", path=rel_path)
    redaction = data.get("redaction")
    if not isinstance(redaction, dict) or redaction.get("required") is not True:
        _add_check(result, "context.redaction_required", "error", "Context contract must require redaction.", path=rel_path)


def _verify_decision_graph(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "decision-graph.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    record_fields = data.get("required_decision_record")
    if not isinstance(record_fields, list) or not record_fields:
        _add_check(result, "decision.record_schema_missing", "error", "Decision graph must declare required_decision_record.", path=rel_path)
    decision_types = data.get("decision_types")
    if not isinstance(decision_types, list) or not decision_types:
        _add_check(result, "decision.types_missing", "error", "Decision graph must declare decision_types.", path=rel_path)
        return
    declared = {str(entry.get("id")) for entry in decision_types if isinstance(entry, dict)}
    missing = sorted(REQUIRED_DECISION_TYPES - declared)
    if missing:
        _add_check(result, "decision.required_types_missing", "error", f"Decision graph misses required type(s): {', '.join(missing)}.", path=rel_path)
    for entry in decision_types:
        if not isinstance(entry, dict):
            _add_check(result, "decision.type_invalid", "error", "Decision type entries must be mappings.", path=rel_path)
            continue
        _require_keys(result, path=rel_path, check_prefix="decision", data=entry, keys=("id", "inputs", "outputs"))


def _verify_rule_packs(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "rule-packs.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        _add_check(result, "rules.none", "error", "Rule packs must declare at least one rule.", path=rel_path)
        return
    for rule in rules:
        if not isinstance(rule, dict):
            _add_check(result, "rules.invalid", "error", "Rule entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="rules",
            data=rule,
            keys=("id", "family", "source_normative", "severity", "phase", "condition", "action", "event", "remediation", "check_id"),
        )
        if str(rule.get("phase", "")) not in KNOWN_HOOK_PHASES:
            _add_check(result, "rules.unknown_phase", "error", f"Rule {rule.get('id')!r} uses unknown phase {rule.get('phase')!r}.", path=rel_path)
        if str(rule.get("action", "")) not in KNOWN_HOOK_ACTIONS:
            _add_check(result, "rules.unknown_action", "error", f"Rule {rule.get('id')!r} uses unknown action {rule.get('action')!r}.", path=rel_path)


def _verify_hook_registry(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "hook-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        _add_check(result, "hooks.none", "error", "Hook registry must declare at least one hook.", path=rel_path)
        return
    for hook in hooks:
        if not isinstance(hook, dict):
            _add_check(result, "hooks.invalid", "error", "Hook entries must be mappings.", path=rel_path)
            continue
        _require_keys(result, path=rel_path, check_prefix="hooks", data=hook, keys=("id", "phase", "action", "rule_ref", "reason"))
        if str(hook.get("phase", "")) not in KNOWN_HOOK_PHASES:
            _add_check(result, "hooks.unknown_phase", "error", f"Hook {hook.get('id')!r} uses unknown phase {hook.get('phase')!r}.", path=rel_path)
        if str(hook.get("action", "")) not in KNOWN_HOOK_ACTIONS:
            _add_check(result, "hooks.unknown_action", "error", f"Hook {hook.get('id')!r} uses unknown action {hook.get('action')!r}.", path=rel_path)


def _verify_orchestration_policy(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "orchestration-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        _add_check(result, "orchestration.roles_missing", "error", "Orchestration policy must declare roles.", path=rel_path)
        return
    for role in roles:
        if not isinstance(role, dict):
            _add_check(result, "orchestration.role_invalid", "error", "Role entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="orchestration.role",
            data=role,
            keys=(
                "role_id",
                "persona_or_archetype",
                "responsibilities",
                "allowed_tools",
                "allowed_memory_types",
                "allowed_providers",
                "autonomy_level",
                "handoff_contracts",
                "escalation_triggers",
                "review_gates",
                "rollback_policy",
            ),
        )


def _verify_evidence_gates(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "evidence-gates.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    transitions = data.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        _add_check(result, "gates.transitions_missing", "error", "Evidence gates must declare transitions.", path=rel_path)
        return
    for transition in transitions:
        if not isinstance(transition, dict):
            _add_check(result, "gates.transition_invalid", "error", "Evidence gate transitions must be mappings.", path=rel_path)
            continue
        _require_keys(result, path=rel_path, check_prefix="gates.transition", data=transition, keys=("id", "from", "to", "required_evidence"))
        if transition.get("from") not in BOARD_STATES or transition.get("to") not in BOARD_STATES:
            _add_check(result, "gates.unknown_state", "error", f"Transition {transition.get('id')!r} references an unknown state.", path=rel_path)
        if not isinstance(transition.get("required_evidence"), list) or not transition.get("required_evidence"):
            _add_check(result, "gates.required_evidence_missing", "error", f"Transition {transition.get('id')!r} has no required evidence.", path=rel_path)


def _verify_score_and_exceptions(root: Path, result: StandardVerificationResult) -> None:
    score_path = STANDARD_DIR / "compliance-score.yaml"
    score_data = _load_yaml_file(root, score_path, result)
    if isinstance(score_data, dict):
        for key in ("dimensions", "thresholds"):
            if not isinstance(score_data.get(key), dict) or not score_data.get(key):
                _add_check(result, f"score.{key}_missing", "error", f"Compliance score must declare {key}.", path=score_path)

    for rel_path, list_key in (
        (STANDARD_DIR / "accepted-risks.yaml", "risks"),
        (STANDARD_DIR / "waivers.yaml", "waivers"),
        (STANDARD_DIR / "remediation-plan.yaml", "actions"),
    ):
        data = _load_yaml_file(root, rel_path, result)
        if isinstance(data, dict) and not isinstance(data.get(list_key), list):
            _add_check(result, f"{list_key}.invalid", "error", f"{rel_path} must declare {list_key} as a list.", path=rel_path)


def _verify_pattern_catalog(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "pattern-catalog.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        _add_check(result, "patterns.categories_missing", "error", "Pattern catalog must declare categories.", path=rel_path)
    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        _add_check(result, "patterns.none", "error", "Pattern catalog must declare executable patterns.", path=rel_path)
        return
    declared_categories = {str(category) for category in categories} if isinstance(categories, list) else set()
    for pattern in patterns:
        if not isinstance(pattern, dict):
            _add_check(result, "patterns.invalid", "error", "Pattern entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="patterns",
            data=pattern,
            keys=("id", "category", "maturity", "source_normative", "intent", "required_artifacts", "check_refs"),
        )
        category = str(pattern.get("category", ""))
        if declared_categories and category not in declared_categories:
            _add_check(result, "patterns.unknown_category", "error", f"Pattern {pattern.get('id')!r} uses unknown category {category!r}.", path=rel_path)
        if not isinstance(pattern.get("required_artifacts"), list):
            _add_check(result, "patterns.required_artifacts_invalid", "error", f"Pattern {pattern.get('id')!r} required_artifacts must be a list.", path=rel_path)


def verify_standard_profile(
    project_root: Path,
    *,
    profile_id: str | None = None,
    task_id: str = "bootstrap",
) -> StandardVerificationResult:
    """Verify that required standard-aware artifacts exist and parse."""
    root = project_root.resolve()
    selected_profile = profile_id or _read_manifest_profile(root)
    if selected_profile is None:
        selected_profile = "starter"
    profile = get_profile(selected_profile)
    artifacts = _planned_artifacts(profile.id, task_id=task_id)

    result = StandardVerificationResult(profile=profile.id, project_root=root)
    required_paths = [artifact.destination for artifact in artifacts]
    required_paths.append(STANDARD_PROFILE_FILE)

    for rel_path in required_paths:
        path = root / rel_path
        if path.is_file():
            result.present.append(rel_path)
        else:
            result.missing.append(rel_path)

    _verify_manifest(root, profile, task_id, result)
    _verify_mission_brief(root, profile, result)
    _verify_provider_registry(root, profile, result)
    _verify_knowledge_registry(root, profile, result)
    _verify_task_envelope(root, profile, task_id, result)
    _verify_evidence_pack(root, task_id, result)
    _verify_compliance_declaration(root, result)
    _verify_profile_specific_controls(root, profile, result)
    _verify_task_board(root, profile, result)
    _verify_memory_policy(root, result)
    _verify_context_contract(root, result)
    _verify_decision_graph(root, result)
    _verify_rule_packs(root, result)
    _verify_hook_registry(root, result)
    _verify_orchestration_policy(root, result)
    _verify_evidence_gates(root, result)
    _verify_pattern_catalog(root, result)
    _verify_score_and_exceptions(root, result)

    return result


def _selected_profile(project_root: Path, profile_id: str | None) -> StandardProfile:
    selected_profile = profile_id or _read_manifest_profile(project_root) or "starter"
    return get_profile(selected_profile)


def _read_yaml_mapping(root: Path, rel_path: Path) -> dict[str, Any]:
    path = root / rel_path
    if not path.is_file():
        return {}
    data = _yaml().load(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{rel_path} must be a YAML mapping."
        raise ValueError(msg)
    return data


def _write_yaml_mapping(root: Path, rel_path: Path, data: dict[str, Any]) -> Path:
    dst = root / rel_path
    _ensure_inside_root(root, dst, label=f"Runtime artifact {rel_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    _yaml().dump(data, stream)
    dst.write_text(stream.getvalue(), encoding="utf-8")
    return rel_path


def _append_runtime_event(root: Path, *, event_type: str, task_id: str, profile: str, details: dict[str, Any]) -> None:
    event_path = root / EVENT_JOURNAL_FILE
    _ensure_inside_root(root, event_path, label="Runtime event journal")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "grimoire-agentic-standard-runtime-event/v1",
        "event_type": event_type,
        "task_id": task_id,
        "profile": profile,
        "timestamp": datetime.now(UTC).isoformat(),
        "details": details,
    }
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _task_from_board(board: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        return {}
    for task in tasks:
        if isinstance(task, dict) and str(task.get("task_id", "")) == task_id:
            return task
    return {}


def build_context_bundle(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> StandardRuntimeArtifact:
    """Build a deterministic context bundle from standard artifacts."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    profile = _selected_profile(root, profile_id)
    board = _read_yaml_mapping(root, STANDARD_DIR / "task-board.yaml")
    memory_policy = _read_yaml_mapping(root, STANDARD_DIR / "memory-policy.yaml")
    knowledge_registry = _read_yaml_mapping(root, STANDARD_DIR / "knowledge-source-registry.yaml")
    provider_registry = _read_yaml_mapping(root, STANDARD_DIR / "llm-provider-registry.yaml")
    context_contract = _read_yaml_mapping(root, STANDARD_DIR / "context-contract.yaml")
    orchestration_policy = _read_yaml_mapping(root, STANDARD_DIR / "orchestration-policy.yaml")
    task = _task_from_board(board, normalized_task_id)

    enabled_providers = [
        str(provider.get("id"))
        for provider in provider_registry.get("providers", [])
        if isinstance(provider, dict) and provider.get("enabled") is True and provider.get("id")
    ]
    enabled_sources = [
        str(source.get("id"))
        for source in knowledge_registry.get("sources", [])
        if isinstance(source, dict) and source.get("enabled") is True and source.get("id")
    ]
    memory_entries = [
        {
            "type": str(entry.get("type")),
            "scope": str(entry.get("scope")),
            "freshness": str(entry.get("freshness")),
            "trust_level": str(entry.get("trust_level")),
        }
        for entry in memory_policy.get("memory_types", [])
        if isinstance(entry, dict)
    ]
    data: dict[str, Any] = {
        "$schema": "grimoire-agentic-standard-context-bundle/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_priority": context_contract.get("source_priority", []),
        "task_summary": {
            "title": task.get("title", "Undeclared task"),
            "status": task.get("status", "proposed"),
            "acceptance_criteria": task.get("acceptance_criteria", []),
        },
        "selected_sources": [
            {"source": "mission_brief", "path": str(STANDARD_DIR / "mission-brief.md")},
            {"source": "task_board", "path": str(STANDARD_DIR / "task-board.yaml")},
            {"source": "task_envelope", "path": str(EVIDENCE_DIR / normalized_task_id / "task-envelope.md")},
        ],
        "knowledge_nodes": enabled_sources,
        "memory_inclusions": memory_entries[: int(context_contract.get("budget", {}).get("max_memory_items", 8))],
        "memory_exclusions": [],
        "provider_constraints": {
            "enabled_providers": enabled_providers,
            "routing": provider_registry.get("routing", {}),
        },
        "agent_role_constraints": [
            str(role.get("role_id"))
            for role in orchestration_policy.get("roles", [])
            if isinstance(role, dict) and role.get("role_id")
        ],
        "redactions": {
            "required": True,
            "forbidden_data_classes": context_contract.get("redaction", {}).get("forbidden_data_classes", []),
        },
        "decision_inputs": {
            "task_ref": str(STANDARD_DIR / "task-board.yaml"),
            "memory_policy_ref": str(STANDARD_DIR / "memory-policy.yaml"),
            "provider_registry_ref": str(STANDARD_DIR / "llm-provider-registry.yaml"),
        },
        "evidence_requirements": [
            str(item)
            for item in task.get("acceptance_criteria", [])
        ],
        "fingerprints": {
            "contract": str(STANDARD_DIR / "context-contract.yaml"),
            "board": str(STANDARD_DIR / "task-board.yaml"),
        },
    }
    rel_path = CONTEXT_DIR / normalized_task_id / "context-bundle.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(root, event_type="context.built", task_id=normalized_task_id, profile=profile.id, details={"path": str(written)})
    return StandardRuntimeArtifact(path=written, data=data)


def build_decision_trace(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> StandardRuntimeArtifact:
    """Build an explainable decision trace skeleton for a task."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    profile = _selected_profile(root, profile_id)
    decision_graph = _read_yaml_mapping(root, STANDARD_DIR / "decision-graph.yaml")
    decision_types = decision_graph.get("decision_types", [])
    if not isinstance(decision_types, list):
        decision_types = []
    records: list[dict[str, Any]] = []
    for entry in decision_types:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        decision_type = str(entry["id"])
        records.append({
            "decision_id": f"{normalized_task_id}.{decision_type}",
            "task_id": normalized_task_id,
            "decision_type": decision_type,
            "profile": profile.id,
            "inputs": entry.get("inputs", []),
            "policy_refs": [str(STANDARD_DIR / "decision-graph.yaml")],
            "rule_refs": [],
            "result": "recorded",
            "confidence": "not-scored",
            "event": "decision.recorded",
            "evidence_refs": [str(CONTEXT_DIR / normalized_task_id / "context-bundle.yaml")],
            "remediation_ref": str(STANDARD_DIR / "remediation-plan.yaml"),
            "timestamp": datetime.now(UTC).isoformat(),
        })
    data = {
        "$schema": "grimoire-agentic-standard-decision-trace/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "records": records,
    }
    rel_path = DECISION_DIR / normalized_task_id / "decision-trace.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(root, event_type="decision.trace_built", task_id=normalized_task_id, profile=profile.id, details={"path": str(written)})
    return StandardRuntimeArtifact(path=written, data=data)


def list_standard_patterns(project_root: Path, *, category: str | None = None) -> tuple[dict[str, Any], ...]:
    """List executable patterns from the standard catalog."""
    root = project_root.resolve()
    catalog = _read_yaml_mapping(root, STANDARD_DIR / "pattern-catalog.yaml")
    patterns = catalog.get("patterns", [])
    if not isinstance(patterns, list):
        return ()
    selected: list[dict[str, Any]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            continue
        if category is not None and pattern.get("category") != category:
            continue
        selected.append(dict(pattern))
    return tuple(selected)


def show_standard_pattern(project_root: Path, pattern_id: str) -> dict[str, Any]:
    """Return one executable pattern by id."""
    for pattern in list_standard_patterns(project_root):
        if pattern.get("id") == pattern_id:
            return pattern
    msg = f"Unknown standard pattern: {pattern_id}"
    raise ValueError(msg)


def build_knowledge_index(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> StandardRuntimeArtifact:
    """Build a source-traceable knowledge index manifest for standard artifacts."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    profile = _selected_profile(root, profile_id)
    registry = _read_yaml_mapping(root, STANDARD_DIR / "knowledge-source-registry.yaml")
    patterns = list_standard_patterns(root)
    declared_sources = [
        {
            "id": str(source.get("id")),
            "type": str(source.get("type", "")),
            "locator": str(source.get("locator", "")),
            "enabled": bool(source.get("enabled")),
            "trust": source.get("trust", {}),
        }
        for source in registry.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    ]
    normative_artifacts = [
        str(path)
        for path in (
            STANDARD_DIR / "mission-brief.md",
            STANDARD_DIR / "task-board.yaml",
            STANDARD_DIR / "memory-policy.yaml",
            STANDARD_DIR / "context-contract.yaml",
            STANDARD_DIR / "decision-graph.yaml",
            STANDARD_DIR / "rule-packs.yaml",
            STANDARD_DIR / "hook-registry.yaml",
            STANDARD_DIR / "orchestration-policy.yaml",
            STANDARD_DIR / "evidence-gates.yaml",
            STANDARD_DIR / "pattern-catalog.yaml",
        )
        if (root / path).exists()
    ]
    data = {
        "$schema": "grimoire-agentic-standard-knowledge-index/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "rules": registry.get("rules", {}),
        "sources": declared_sources,
        "normative_artifacts": normative_artifacts,
        "patterns": [
            {
                "id": str(pattern.get("id")),
                "category": str(pattern.get("category")),
                "source_normative": str(pattern.get("source_normative")),
                "check_refs": pattern.get("check_refs", []),
            }
            for pattern in patterns
        ],
    }
    rel_path = KNOWLEDGE_DIR / normalized_task_id / "index-manifest.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(root, event_type="knowledge.index_built", task_id=normalized_task_id, profile=profile.id, details={"path": str(written)})
    return StandardRuntimeArtifact(path=written, data=data)


def verify_knowledge_index(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
) -> StandardVerificationResult:
    """Verify the generated knowledge index manifest."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    result = verify_standard_profile(root, task_id=normalized_task_id)
    rel_path = KNOWLEDGE_DIR / normalized_task_id / "index-manifest.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        _add_check(result, "knowledge_index.missing", "error", "Knowledge index manifest is missing or invalid.", path=rel_path)
        return result
    for key in ("sources", "normative_artifacts", "patterns"):
        if not isinstance(data.get(key), list):
            _add_check(result, f"knowledge_index.{key}_invalid", "error", f"Knowledge index {key} must be a list.", path=rel_path)
    return result


def simulate_standard_hooks(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    phase: str | None = None,
    profile_id: str | None = None,
) -> StandardRuntimeArtifact:
    """Simulate declared standard hooks without executing external actions."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    profile = _selected_profile(root, profile_id)
    if phase is not None and phase not in KNOWN_HOOK_PHASES:
        msg = f"Unknown hook phase {phase!r}. Available: {', '.join(sorted(KNOWN_HOOK_PHASES))}"
        raise ValueError(msg)
    registry = _read_yaml_mapping(root, STANDARD_DIR / "hook-registry.yaml")
    hooks = [
        hook
        for hook in registry.get("hooks", [])
        if isinstance(hook, dict) and (phase is None or hook.get("phase") == phase)
    ]
    data = {
        "$schema": "grimoire-agentic-standard-hook-simulation/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "phase": phase or "all",
        "simulated_at": datetime.now(UTC).isoformat(),
        "hooks": hooks,
        "executed_external_actions": False,
    }
    suffix = phase or "all"
    rel_path = EVENT_DIR / normalized_task_id / f"hook-simulation-{suffix}.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(root, event_type="hooks.simulated", task_id=normalized_task_id, profile=profile.id, details={"path": str(written), "phase": phase or "all"})
    return StandardRuntimeArtifact(path=written, data=data)


def check_evidence_gates(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    target_state: str | None = None,
    profile_id: str | None = None,
) -> StandardGateResult:
    """Evaluate task evidence gates against generated runtime artifacts."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    profile = _selected_profile(root, profile_id)
    if target_state is not None and target_state not in BOARD_STATES:
        msg = f"Unknown target state {target_state!r}. Available: {', '.join(sorted(BOARD_STATES))}"
        raise ValueError(msg)
    board = _read_yaml_mapping(root, STANDARD_DIR / "task-board.yaml")
    task = _task_from_board(board, normalized_task_id)
    state = str(target_state or task.get("status") or "")
    missing: list[str] = []
    checks: list[StandardCheck] = []
    required_paths = {
        "task_board": STANDARD_DIR / "task-board.yaml",
        "task_envelope": EVIDENCE_DIR / normalized_task_id / "task-envelope.md",
        "evidence_pack": EVIDENCE_DIR / normalized_task_id / "evidence-pack.md",
        "context_bundle": CONTEXT_DIR / normalized_task_id / "context-bundle.yaml",
        "decision_trace": DECISION_DIR / normalized_task_id / "decision-trace.yaml",
        "compliance_score": SCORE_DIR / normalized_task_id / "compliance-score.yaml",
    }
    if state in {"ready", "in_progress", "review", "accepted", "released"}:
        for key in ("task_board", "task_envelope"):
            if not (root / required_paths[key]).is_file():
                missing.append(key)
    if state in {"in_progress", "review", "accepted", "released"} and not (root / required_paths["context_bundle"]).is_file():
        missing.append("context_bundle")
    if state in {"review", "accepted", "released"}:
        for key in ("evidence_pack", "decision_trace"):
            if not (root / required_paths[key]).is_file():
                missing.append(key)
    if state == "released" and not (root / required_paths["compliance_score"]).is_file():
        missing.append("compliance_score")
    for key in missing:
        checks.append(StandardCheck(
            id=f"gate.{key}_missing",
            severity="error",
            message=f"Required gate artifact is missing: {key}.",
            path=required_paths.get(key),
        ))
    ok = not checks
    _append_runtime_event(root, event_type="gate.checked", task_id=normalized_task_id, profile=profile.id, details={"ok": ok, "target_state": state, "missing": missing})
    return StandardGateResult(ok=ok, task_id=normalized_task_id, profile=profile.id, state=state or None, missing=tuple(missing), checks=tuple(checks))


def audit_runtime_events(project_root: Path) -> dict[str, Any]:
    """Audit the standard runtime event journal."""
    root = project_root.resolve()
    journal = root / EVENT_JOURNAL_FILE
    if not journal.is_file():
        return {"ok": True, "path": str(EVENT_JOURNAL_FILE), "event_count": 0, "invalid_lines": []}
    invalid_lines: list[int] = []
    event_count = 0
    event_types: dict[str, int] = {}
    for line_no, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines.append(line_no)
            continue
        if not isinstance(event, dict) or not event.get("event_type") or not event.get("timestamp"):
            invalid_lines.append(line_no)
            continue
        event_count += 1
        event_type = str(event["event_type"])
        event_types[event_type] = event_types.get(event_type, 0) + 1
    return {
        "ok": not invalid_lines,
        "path": str(EVENT_JOURNAL_FILE),
        "event_count": event_count,
        "event_types": event_types,
        "invalid_lines": invalid_lines,
    }


def calculate_compliance_score(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> StandardScoreResult:
    """Calculate and persist a standard compliance score."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    result = verify_standard_profile(root, profile_id=profile_id, task_id=normalized_task_id)
    profile = get_profile(result.profile)
    score_policy = _read_yaml_mapping(root, STANDARD_DIR / "compliance-score.yaml")
    thresholds = score_policy.get("thresholds", {})
    profile_thresholds = thresholds.get(profile.id, {}) if isinstance(thresholds, dict) else {}
    threshold = int(profile_thresholds.get("fail_below", 70)) if isinstance(profile_thresholds, dict) else 70
    score = max(0, min(100, 100 - (result.error_count * 20) - (result.warning_count * 3)))
    data = {
        "$schema": "grimoire-agentic-standard-score-result/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "score": score,
        "threshold": threshold,
        "ok": score >= threshold and result.ok,
        "errors": result.error_count,
        "warnings": result.warning_count,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    rel_path = SCORE_DIR / normalized_task_id / "compliance-score.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(root, event_type="score.calculated", task_id=normalized_task_id, profile=profile.id, details={"path": str(written), "score": score, "threshold": threshold})
    return StandardScoreResult(
        ok=bool(data["ok"]),
        profile=profile.id,
        score=score,
        threshold=threshold,
        warnings=result.warning_count,
        errors=result.error_count,
        output_path=written,
    )


def propose_remediation_actions(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> tuple[StandardRemediationAction, ...]:
    """Return structured remediation actions from standard verification findings."""
    root = project_root.resolve()
    result = verify_standard_profile(root, profile_id=profile_id, task_id=task_id)
    actions: list[StandardRemediationAction] = []
    for missing in result.missing:
        actions.append(StandardRemediationAction(
            check_id="artifact.missing",
            severity="error",
            action="generate_missing_artifact",
            path=missing,
            message=f"Generate missing required artifact {missing}.",
        ))
    for check in result.checks:
        if check.severity not in {"error", "warning"}:
            continue
        if check.id.endswith("_outside_root"):
            action = "move_reference_inside_project_root"
        elif "provider" in check.id:
            action = "update_provider_registry"
        elif "knowledge" in check.id:
            action = "complete_knowledge_registry"
        elif "memory" in check.id:
            action = "complete_memory_policy"
        elif "context" in check.id:
            action = "complete_context_contract"
        elif "gate" in check.id or "evidence" in check.id:
            action = "attach_required_evidence"
        else:
            action = "complete_required_field"
        actions.append(StandardRemediationAction(
            check_id=check.id,
            severity=check.severity,
            action=action,
            path=check.path,
            message=check.message,
        ))
    return tuple(actions)
