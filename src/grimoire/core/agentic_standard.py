"""Agentic standard profile setup and verification."""

from __future__ import annotations

import io
import json
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core import standard_generation as gen
from grimoire.core.standard_checks.base import (
    BOARD_STATES as BOARD_STATES,
)
from grimoire.core.standard_checks.base import (
    KNOWN_HOOK_PHASES as KNOWN_HOOK_PHASES,
)
from grimoire.core.standard_checks.base import (
    MEMORY_OS_LEGACY_VECTOR_SOURCES as MEMORY_OS_LEGACY_VECTOR_SOURCES,
)
from grimoire.core.standard_checks.base import (
    MEMORY_OS_REQUIRED_PROMOTION_GATES as MEMORY_OS_REQUIRED_PROMOTION_GATES,
)
from grimoire.core.standard_checks.base import (
    MEMORY_OS_REQUIRED_TARGET as MEMORY_OS_REQUIRED_TARGET,
)
from grimoire.core.standard_checks.base import (
    StandardCheck as StandardCheck,
)
from grimoire.core.standard_checks.base import (
    StandardProfile as StandardProfile,
)
from grimoire.core.standard_checks.base import (
    StandardVerificationResult as StandardVerificationResult,
)
from grimoire.core.standard_checks.base import (
    _add_check as _add_check,
)
from grimoire.core.standard_checks.base import (
    _is_inside_root as _is_inside_root,
)
from grimoire.core.standard_checks.base import (
    _load_yaml_file as _load_yaml_file,
)
from grimoire.core.standard_checks.base import (
    _yaml as _yaml,
)
from grimoire.core.standard_checks.controls import (
    _verify_k8s_agent_manifest,
    _verify_score_and_exceptions,
)
from grimoire.core.standard_checks.registry import (
    DEFAULT_SCORE_DIMENSIONS as DEFAULT_SCORE_DIMENSIONS,
)
from grimoire.core.standard_checks.registry import (
    DIMENSION_CHECK_PREFIXES as DIMENSION_CHECK_PREFIXES,
)
from grimoire.core.standard_checks.registry import (
    dimension_for,
)
from grimoire.core.standard_checks.verifiers import (
    _verify_memory_policy,
    run_verifiers,
)
from grimoire.core.standard_generation import (
    CONTEXT_DIR,
    DECISION_DIR,
    EVIDENCE_DIR,
    SCORE_DIR,
    STANDARD_DIR,
    STANDARD_PROFILE_FILE,
    _render_template,
    normalize_task_id,
)
from grimoire.core.standard_profile_manifest import read_artifact_paths, read_profile
from grimoire.core.standard_state import board_omits_task, task_from_board
from grimoire.data import framework_path

PROFILE_MAP_PATH = Path("agentic-standard/profile-map.yaml")
CAPABILITY_MAP_PATH = Path("agentic-standard/capability-map.yaml")
NEEDS_CATALOG_PATH = Path("agentic-standard/needs-catalog.yaml")
PROFILE_LADDER = ("starter", "controlled", "orchestrated", "governed", "production")
EVENT_DIR = Path("_grimoire-output/events")
KNOWLEDGE_DIR = Path("_grimoire-output/knowledge")

LLM_PROVIDER_REGISTRY_FILE = STANDARD_DIR / "llm-provider-registry.yaml"
EVENT_JOURNAL_FILE = EVENT_DIR / "runtime-journal.jsonl"
APPLIED_FIXES_FILE = EVENT_DIR / "applied-fixes.jsonl"
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


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Resolved custom install: needs -> patterns -> profile + artifacts + tech extras."""

    profile: str
    needs: tuple[str, ...]
    patterns: tuple[str, ...]
    memory_capabilities: tuple[str, ...]
    artifacts: tuple[str, ...]
    extra_artifacts: tuple[str, ...]
    tech_extras: tuple[str, ...]
    pip_target: str
    pip_command: str
    warnings: tuple[str, ...]


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
    dimensions: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StandardRemediationAction:
    """Structured remediation action proposed by standard audit."""

    check_id: str
    severity: str
    action: str
    path: Path | None
    message: str


@dataclass(frozen=True, slots=True)
class StandardRemediationApplyResult:
    """Result of applying non-destructive standard remediations."""

    profile: str
    project_root: Path
    actions: tuple[StandardRemediationAction, ...]
    written: tuple[Path, ...]
    skipped: tuple[str, ...]
    audit_path: Path




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


def _capability_map_file() -> Path:
    path = framework_path() / CAPABILITY_MAP_PATH
    if not path.is_file():
        msg = f"Agentic standard capability map not found: {path}"
        raise FileNotFoundError(msg)
    return path


def load_capability_map() -> dict[str, Any]:
    """Load the bundled pattern -> artifacts/rules/checks/tech capability map."""
    data = _yaml().load(_capability_map_file())
    if not isinstance(data, dict):
        msg = "Agentic standard capability map must be a YAML mapping."
        raise ValueError(msg)
    return data


def _needs_catalog_file() -> Path:
    path = framework_path() / NEEDS_CATALOG_PATH
    if not path.is_file():
        msg = f"Agentic standard needs catalog not found: {path}"
        raise FileNotFoundError(msg)
    return path


def load_needs_catalog() -> dict[str, Any]:
    """Load the bundled user-facing needs/flows catalog."""
    data = _yaml().load(_needs_catalog_file())
    if not isinstance(data, dict):
        msg = "Agentic standard needs catalog must be a YAML mapping."
        raise ValueError(msg)
    return data


def _profile_rank(profile_id: str) -> int:
    try:
        return PROFILE_LADDER.index(profile_id)
    except ValueError:
        return -1


def _highest_profile(profile_ids: Iterable[str]) -> str:
    best = "starter"
    best_rank = 0
    for profile_id in profile_ids:
        rank = _profile_rank(profile_id)
        if rank > best_rank:
            best, best_rank = profile_id, rank
    return best


def resolve_install_plan(
    *,
    needs: Iterable[str] = (),
    patterns: Iterable[str] = (),
    memory_capabilities: Iterable[str] = (),
    profile: str | None = None,
    extra_tech_extras: Iterable[str] = (),
) -> InstallPlan:
    """Resolve selected needs/patterns into a profile, artifact set and tech extras."""
    capability_map = load_capability_map()
    catalog = load_needs_catalog()

    pattern_specs = capability_map.get("patterns")
    if not isinstance(pattern_specs, dict):
        msg = "capability-map.yaml must define a patterns mapping."
        raise ValueError(msg)
    mem_specs = capability_map.get("memory_capabilities")
    mem_specs = mem_specs if isinstance(mem_specs, dict) else {}
    need_specs = {str(n["id"]): n for n in catalog.get("needs", []) if isinstance(n, dict) and "id" in n}

    warnings: list[str] = []
    selected_needs: list[str] = []
    selected_patterns: list[str] = []
    selected_mem: list[str] = []
    extras: list[str] = []
    profile_floors: list[str] = []

    def _add_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    for raw_need in needs:
        need_id = str(raw_need).strip()
        if not need_id:
            continue
        spec = need_specs.get(need_id)
        if spec is None:
            warnings.append(f"Unknown need {need_id!r}; ignored.")
            continue
        _add_unique(selected_needs, need_id)
        for pattern_id in spec.get("patterns", []):
            _add_unique(selected_patterns, str(pattern_id))
        for cap in spec.get("memory_capabilities", []):
            _add_unique(selected_mem, str(cap))
        for extra in spec.get("extra_tech_extras", []):
            _add_unique(extras, str(extra))
        recommended = spec.get("recommended_profile")
        if recommended:
            profile_floors.append(str(recommended))

    for raw_pattern in patterns:
        _add_unique(selected_patterns, str(raw_pattern).strip())
    for raw_cap in memory_capabilities:
        _add_unique(selected_mem, str(raw_cap).strip())
    for raw_extra in extra_tech_extras:
        _add_unique(extras, str(raw_extra).strip())

    # A selected memory tier pulls in its backing pattern and tech extra.
    for cap in list(selected_mem):
        cap_spec = mem_specs.get(cap)
        if not isinstance(cap_spec, dict):
            warnings.append(f"Unknown memory capability {cap!r}; ignored.")
            selected_mem.remove(cap)
            continue
        backing = cap_spec.get("backs_pattern")
        if backing:
            _add_unique(selected_patterns, str(backing))
        for extra in cap_spec.get("tech_extras", []):
            _add_unique(extras, str(extra))

    artifact_set: list[str] = []
    pattern_artifacts: list[str] = []
    for pattern_id in selected_patterns:
        spec = pattern_specs.get(pattern_id)
        if not isinstance(spec, dict):
            warnings.append(f"Unknown pattern {pattern_id!r}; ignored.")
            continue
        for artifact in spec.get("artifacts", []):
            _add_unique(pattern_artifacts, str(artifact))
        for extra in spec.get("tech_extras", []):
            _add_unique(extras, str(extra))
        floor = spec.get("profile_min")
        if floor:
            profile_floors.append(str(floor))

    selected_patterns = [p for p in selected_patterns if p in pattern_specs]

    resolved_profile = profile if profile is not None else _highest_profile([*profile_floors, "starter"])

    profile_obj = get_profile(resolved_profile)
    for artifact in profile_obj.required_artifacts:
        _add_unique(artifact_set, str(artifact))
    extra_artifacts = [a for a in pattern_artifacts if a not in artifact_set]
    for artifact in extra_artifacts:
        _add_unique(artifact_set, artifact)

    # Order tech extras by their declaration order in the capability map.
    declared = capability_map.get("tech_extras")
    declared_order = list(declared) if isinstance(declared, dict) else []
    ordered_extras = [e for e in declared_order if e in extras]
    ordered_extras.extend(e for e in extras if e not in ordered_extras)

    pip_target = f"grimoire-kit[{','.join(ordered_extras)}]" if ordered_extras else "grimoire-kit"
    pip_command = f"pip install '{pip_target}'"

    return InstallPlan(
        profile=resolved_profile,
        needs=tuple(selected_needs),
        patterns=tuple(selected_patterns),
        memory_capabilities=tuple(selected_mem),
        artifacts=tuple(artifact_set),
        extra_artifacts=tuple(extra_artifacts),
        tech_extras=tuple(ordered_extras),
        pip_target=pip_target,
        pip_command=pip_command,
        warnings=tuple(warnings),
    )


def _ensure_inside_root(root: Path, path: Path, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        msg = f"{label} resolves outside project root: {path}"
        raise ValueError(msg)
    return resolved_path




def _format_destination(path_template: str, task_id: str) -> Path:
    return Path(path_template.replace("{task-id}", normalize_task_id(task_id)))


def _planned_artifacts(
    profile_id: str,
    *,
    task_id: str = "bootstrap",
    extra_artifacts: Iterable[str] = (),
) -> tuple[StandardArtifact, ...]:
    profile = get_profile(profile_id)
    templates = _artifact_templates()
    targets = _generation_targets()
    artifacts: list[StandardArtifact] = []

    planned_types: list[str] = list(profile.required_artifacts)
    for artifact_type in extra_artifacts:
        if artifact_type not in planned_types:
            planned_types.append(str(artifact_type))

    for artifact_type in planned_types:
        if artifact_type not in targets:
            msg = f"No generation target declared for artifact {artifact_type!r}."
            raise ValueError(msg)
        artifacts.append(StandardArtifact(
            artifact_type=artifact_type,
            source=templates[artifact_type],
            destination=_format_destination(targets[artifact_type], task_id),
        ))

    return tuple(artifacts)


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
    extra_artifacts: Iterable[str] = (),
    force: bool = False,
    dry_run: bool = False,
    refresh: bool = False,
) -> StandardSetupResult:
    """Generate standard-aware project artifacts for one profile.

    *refresh* updates the artifacts the kit generated and the project never
    touched, leaving edited ones (and everything predating the generation
    manifest) alone. It is how ``grimoire up`` carries standard updates into an
    existing project without ``--force``, which would flatten waivers, scores
    and task boards along with the policies.
    """
    root = project_root.resolve()
    profile = get_profile(profile_id)
    artifacts = _planned_artifacts(profile_id, task_id=task_id, extra_artifacts=extra_artifacts)
    name = project_name or root.name
    generated_at = datetime.now(UTC).date().isoformat()
    result = StandardSetupResult(profile=profile.id, project_root=root, dry_run=dry_run)

    manifest = gen.load_generation_manifest(root)
    generated: dict[str, str] = {}

    for artifact in artifacts:
        dst = root / artifact.destination
        _ensure_inside_root(root, dst, label=f"Artifact {artifact.artifact_type!r}")
        template = artifact.source.read_text(encoding="utf-8")
        content = _render_template(template, project_name=name, profile=profile, generated_at=generated_at)
        # ``destination`` is a str for some artifacts and a Path for others;
        # the manifest is JSON, so its keys are always strings.
        key = str(artifact.destination)
        action = gen.decide(root, key, content, force=force, refresh=refresh, manifest=manifest)
        if action != "write":
            result.skipped.append(artifact.destination)
            if action == "adopt":
                generated[key] = gen.digest(dst)
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
            generated[key] = gen.digest(dst)
        result.written.append(artifact.destination)

    manifest_dst = root / STANDARD_PROFILE_FILE
    profile_key = str(STANDARD_PROFILE_FILE)
    profile_content = _manifest_content(profile, name, task_id, artifacts)
    action = gen.decide(root, profile_key, profile_content, force=force, refresh=refresh, manifest=manifest)
    if action != "write":
        result.skipped.append(STANDARD_PROFILE_FILE)
        if action == "adopt":
            generated[profile_key] = gen.digest(manifest_dst)
    else:
        if not dry_run:
            manifest_dst.parent.mkdir(parents=True, exist_ok=True)
            manifest_dst.write_text(profile_content, encoding="utf-8")
            generated[profile_key] = gen.digest(manifest_dst)
        result.written.append(STANDARD_PROFILE_FILE)

    if generated and not dry_run:
        gen.save_generation_manifest(root, generated)

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
    return read_profile(project_root / STANDARD_PROFILE_FILE)


































def _memory_os_context(memory_policy: Mapping[str, Any]) -> dict[str, Any]:
    memory_os = memory_policy.get("memory_os")
    if not isinstance(memory_os, dict):
        return {
            "contract_declared": False,
            "target": dict(MEMORY_OS_REQUIRED_TARGET),
            "legacy_vector_sources": sorted(MEMORY_OS_LEGACY_VECTOR_SOURCES),
            "promotion_gates": sorted(MEMORY_OS_REQUIRED_PROMOTION_GATES),
            "policy_ref": str(STANDARD_DIR / "memory-policy.yaml"),
        }
    target = memory_os.get("target")
    return {
        "contract_declared": True,
        "target": target if isinstance(target, dict) else {},
        "promotion_gates": memory_os.get("promotion_gates", []),
        "runtime_commands": memory_os.get("runtime_commands", []),
        "policy_ref": str(STANDARD_DIR / "memory-policy.yaml"),
    }






























































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
    known = {str(path) for path in required_paths}
    for recorded in read_artifact_paths(root / STANDARD_PROFILE_FILE):
        if str(recorded) not in known:
            required_paths.append(recorded)

    for rel_path in required_paths:
        path = root / rel_path
        if path.is_file():
            result.present.append(rel_path)
        else:
            result.missing.append(rel_path)

    run_verifiers(root, profile, task_id, result)
    _verify_k8s_agent_manifest(root, result)
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


def _task_required_capabilities(task: Mapping[str, Any]) -> list[str]:
    declared = task.get("required_capabilities")
    if isinstance(declared, list):
        capabilities = [str(item) for item in declared if str(item).strip()]
        if capabilities:
            return capabilities
    role_capabilities = {
        "planner": "reasoning",
        "context_orchestrator": "chat",
        "reviewer": "review",
        "implementer": "code",
    }
    roles = task.get("agent_roles")
    if isinstance(roles, list):
        inferred = [
            role_capabilities[str(role)]
            for role in roles
            if str(role) in role_capabilities
        ]
        if inferred:
            return sorted(set(inferred))
    return ["chat"]


def _provider_routes(provider_registry: Mapping[str, Any], required_capabilities: Iterable[str]) -> tuple[dict[str, Any], ...]:
    required = tuple(required_capabilities)
    routes: list[dict[str, Any]] = []
    providers = provider_registry.get("providers")
    if not isinstance(providers, list):
        return ()
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("enabled") is not True or not provider.get("id"):
            continue
        capabilities = [
            str(capability)
            for capability in provider.get("allowed_capabilities", [])
            if str(capability).strip()
        ]
        missing_capabilities = [
            capability
            for capability in required
            if capability not in capabilities
        ]
        data_policy = provider.get("data_policy", {})
        routes.append({
            "provider": str(provider["id"]),
            "provider_type": str(provider.get("provider_type", "")),
            "capabilities": capabilities,
            "required_capabilities": list(required),
            "capability_match": not missing_capabilities,
            "missing_capabilities": missing_capabilities,
            "allowed_data_classes": data_policy.get("allowed_data_classes", []) if isinstance(data_policy, dict) else [],
            "forbidden_data_classes": data_policy.get("forbidden_data_classes", []) if isinstance(data_policy, dict) else [],
            "fallback_order": provider.get("fallback_order", []) if isinstance(provider.get("fallback_order"), list) else [],
        })
    return tuple(routes)


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
    task = task_from_board(board, normalized_task_id)

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
    required_capabilities = _task_required_capabilities(task)
    provider_routes = _provider_routes(provider_registry, required_capabilities)
    matched_provider = next((route["provider"] for route in provider_routes if route["capability_match"]), None)
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
        "knowledge_graph_ref": str(KNOWLEDGE_DIR / normalized_task_id / "knowledge-graph.yaml"),
        "memory_inclusions": memory_entries[: int(context_contract.get("budget", {}).get("max_memory_items", 8))],
        "memory_exclusions": [],
        "memory_os": _memory_os_context(memory_policy),
        "provider_constraints": {
            "enabled_providers": enabled_providers,
            "routing": provider_registry.get("routing", {}),
            "required_capabilities": required_capabilities,
            "routes": list(provider_routes),
            "matched_provider": matched_provider,
            "unmatched_capabilities": [] if matched_provider else required_capabilities,
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
    _append_runtime_event(
        root,
        event_type="provider.routing_evaluated",
        task_id=normalized_task_id,
        profile=profile.id,
        details={
            "matched_provider": matched_provider,
            "enabled_providers": enabled_providers,
            "required_capabilities": required_capabilities,
        },
    )
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


def build_knowledge_graph(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
    source_ids: Iterable[str] = (),
) -> StandardRuntimeArtifact:
    """Build a local doc-to-graph index from declared knowledge and normative artifacts."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    selected_sources = {str(source_id) for source_id in source_ids}
    profile = _selected_profile(root, profile_id)
    registry = _read_yaml_mapping(root, STANDARD_DIR / "knowledge-source-registry.yaml")
    patterns = list_standard_patterns(root)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    skipped_sources: list[dict[str, str]] = []

    normative_artifacts = [
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
        STANDARD_DIR / "compliance-score.yaml",
    ]
    for rel_path in normative_artifacts:
        if (root / rel_path).is_file():
            node_id = f"artifact:{rel_path.as_posix()}"
            nodes.append({
                "id": node_id,
                "type": "normative_artifact",
                "path": rel_path.as_posix(),
            })

    sources = registry.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict) or source.get("enabled") is not True or not source.get("id"):
                continue
            source_id = str(source["id"])
            if selected_sources and source_id not in selected_sources:
                continue
            source_type = str(source.get("type", ""))
            locator = str(source.get("locator", "")).strip()
            if source_type != "folder" or not locator:
                skipped_sources.append({"id": source_id, "reason": "only enabled folder sources with local locators are indexed"})
                continue
            locator_path = (root / locator).resolve()
            if not _is_inside_root(root, locator_path):
                skipped_sources.append({"id": source_id, "reason": "locator resolves outside project root"})
                continue
            if not locator_path.exists():
                skipped_sources.append({"id": source_id, "reason": "locator does not exist"})
                continue
            for path in sorted(locator_path.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".yaml", ".yml", ".json"}:
                    continue
                rel_path = path.relative_to(root)
                node_id = f"knowledge:{source_id}:{rel_path.as_posix()}"
                nodes.append({
                    "id": node_id,
                    "type": "knowledge_document",
                    "source_id": source_id,
                    "path": rel_path.as_posix(),
                })
                edges.append({
                    "from": node_id,
                    "to": "artifact:_grimoire/standard/knowledge-source-registry.yaml",
                    "type": "declared_by",
                })

    for pattern in patterns:
        pattern_id = str(pattern.get("id", ""))
        if not pattern_id:
            continue
        node_id = f"pattern:{pattern_id}"
        nodes.append({
            "id": node_id,
            "type": "pattern",
            "category": str(pattern.get("category", "")),
            "maturity": str(pattern.get("maturity", "")),
        })
        source_normative = str(pattern.get("source_normative", ""))
        if source_normative:
            edges.append({
                "from": node_id,
                "to": source_normative,
                "type": "derived_from",
            })
        check_refs = pattern.get("check_refs", [])
        if isinstance(check_refs, list):
            for check_ref in check_refs:
                edges.append({
                    "from": node_id,
                    "to": f"check:{check_ref}",
                    "type": "satisfies_check",
                })

    data = {
        "$schema": "grimoire-agentic-standard-knowledge-graph/v1",
        "task_id": normalized_task_id,
        "profile": profile.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_filter": sorted(selected_sources),
        "nodes": nodes,
        "edges": edges,
        "skipped_sources": skipped_sources,
    }
    rel_path = KNOWLEDGE_DIR / normalized_task_id / "knowledge-graph.yaml"
    written = _write_yaml_mapping(root, rel_path, data)
    _append_runtime_event(
        root,
        event_type="knowledge.graph_built",
        task_id=normalized_task_id,
        profile=profile.id,
        details={"path": str(written), "nodes": len(nodes), "edges": len(edges)},
    )
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
    board_path = STANDARD_DIR / "task-board.yaml"
    board = _read_yaml_mapping(root, board_path)
    task = task_from_board(board, normalized_task_id)
    state = str(target_state or task.get("status") or "")
    missing: list[str] = []
    checks: list[StandardCheck] = []
    if board_omits_task(root, task):
        checks.append(StandardCheck(
            id="gate.task_not_on_board",
            severity="error",
            message=f"Task {normalized_task_id!r} is not on the board: evidence gates cannot be evaluated.",
            path=board_path,
        ))
    required_paths = {
        "task_board": STANDARD_DIR / "task-board.yaml",
        "memory_policy": STANDARD_DIR / "memory-policy.yaml",
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
    if profile.id in {"orchestrated", "governed", "production"} and state in {"in_progress", "review", "accepted", "released"}:
        if not (root / required_paths["memory_policy"]).is_file():
            missing.append("memory_policy")
        else:
            memory_result = StandardVerificationResult(profile=profile.id, project_root=root)
            _verify_memory_policy(root, profile, memory_result)
            checks.extend(memory_result.checks)
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
    ok = not any(check.is_error for check in checks)
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


def _dimension_for_check(check_id: str) -> str:
    """Route a check to its score dimension, by explicit declaration.

    The registry is authoritative. The prefix table remains a fallback for ids a
    project emits itself, but every id this kit emits is declared —
    ``test_every_emitted_check_is_registered`` enforces it.
    """
    declared = dimension_for(check_id)
    if declared is not None:
        return declared
    for dimension, prefixes in DIMENSION_CHECK_PREFIXES.items():
        if check_id.startswith(prefixes):
            return dimension
    return "artifacts"


def _score_dimensions(
    result: StandardVerificationResult,
    *,
    score_policy: Mapping[str, Any],
    root: Path,
) -> dict[str, dict[str, int]]:
    raw_dimensions = score_policy.get("dimensions", {})
    if isinstance(raw_dimensions, dict) and raw_dimensions:
        weights = {str(dimension): int(weight) for dimension, weight in raw_dimensions.items()}
    else:
        weights = dict(DEFAULT_SCORE_DIMENSIONS)
    buckets = {
        dimension: {"weight": weight, "errors": 0, "warnings": 0}
        for dimension, weight in weights.items()
    }
    buckets.setdefault("artifacts", {"weight": 0, "errors": 0, "warnings": 0})

    buckets["artifacts"]["errors"] += len(result.missing) + len(result.invalid_yaml)
    buckets["artifacts"]["warnings"] += len(result.warnings)
    for check in result.checks:
        dimension = _dimension_for_check(check.id)
        buckets.setdefault(dimension, {"weight": 0, "errors": 0, "warnings": 0})
        if check.is_error:
            buckets[dimension]["errors"] += 1
        elif check.severity == "warning":
            buckets[dimension]["warnings"] += 1

    journal = audit_runtime_events(root)
    journal_bucket = buckets.setdefault("runtime_journal", {"weight": 0, "errors": 0, "warnings": 0})
    if not journal["ok"]:
        journal_bucket["errors"] += 1
    elif int(journal.get("event_count", 0)) == 0:
        journal_bucket["warnings"] += 1

    dimensions: dict[str, dict[str, int]] = {}
    for dimension, bucket in buckets.items():
        weight = max(0, bucket["weight"])
        errors = bucket["errors"]
        warnings = bucket["warnings"]
        penalty = (errors * weight) + (warnings * max(1, weight // 2))
        earned = max(0, weight - penalty)
        percentage = int((earned / weight) * 100) if weight else 100
        dimensions[dimension] = {
            "weight": weight,
            "earned": earned,
            "percentage": percentage,
            "errors": errors,
            "warnings": warnings,
        }
    return dimensions


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
    dimensions = _score_dimensions(result, score_policy=score_policy, root=root)
    total_weight = sum(item["weight"] for item in dimensions.values())
    total_earned = sum(item["earned"] for item in dimensions.values())
    score = int((total_earned / total_weight) * 100) if total_weight else max(0, min(100, 100 - (result.error_count * 20) - (result.warning_count * 3)))
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
        "dimensions": dimensions,
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
        dimensions=dimensions,
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


def _write_remediation_audit(
    root: Path,
    *,
    task_id: str,
    profile: str,
    written: Iterable[Path],
    skipped: Iterable[str],
) -> Path:
    audit_path = root / APPLIED_FIXES_FILE
    _ensure_inside_root(root, audit_path, label="Applied remediation audit")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "grimoire-agentic-standard-remediation-apply/v1",
        "task_id": task_id,
        "profile": profile,
        "timestamp": datetime.now(UTC).isoformat(),
        "written": [str(path) for path in written],
        "skipped": list(skipped),
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return APPLIED_FIXES_FILE


def apply_remediation_actions(
    project_root: Path,
    *,
    task_id: str = "bootstrap",
    profile_id: str | None = None,
) -> StandardRemediationApplyResult:
    """Apply only non-destructive standard remediations."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    verification = verify_standard_profile(root, profile_id=profile_id, task_id=normalized_task_id)
    actions = propose_remediation_actions(root, task_id=normalized_task_id, profile_id=verification.profile)
    safe_missing = [
        action
        for action in actions
        if action.action == "generate_missing_artifact" and action.path is not None
    ]
    written: tuple[Path, ...] = ()
    skipped: list[str] = [
        f"{action.action}:{action.check_id}"
        for action in actions
        if action not in safe_missing
    ]
    if safe_missing:
        setup_result = setup_standard_profile(
            root,
            profile_id=verification.profile,
            task_id=normalized_task_id,
            force=False,
            dry_run=False,
        )
        written = tuple(setup_result.written)
        skipped.extend(str(path) for path in setup_result.skipped)
    audit_path = _write_remediation_audit(
        root,
        task_id=normalized_task_id,
        profile=verification.profile,
        written=written,
        skipped=skipped,
    )
    _append_runtime_event(
        root,
        event_type="remediation.applied",
        task_id=normalized_task_id,
        profile=verification.profile,
        details={"written": [str(path) for path in written], "skipped": skipped, "audit_path": str(audit_path)},
    )
    return StandardRemediationApplyResult(
        profile=verification.profile,
        project_root=root,
        actions=actions,
        written=written,
        skipped=tuple(skipped),
        audit_path=audit_path,
    )
