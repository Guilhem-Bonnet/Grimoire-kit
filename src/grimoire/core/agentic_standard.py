"""Agentic standard profile setup and verification."""

from __future__ import annotations

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
STANDARD_PROFILE_FILE = STANDARD_DIR / "standard-profile.yaml"
LLM_PROVIDER_REGISTRY_FILE = STANDARD_DIR / "llm-provider-registry.yaml"
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
    import io

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

    import io

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

    return result
