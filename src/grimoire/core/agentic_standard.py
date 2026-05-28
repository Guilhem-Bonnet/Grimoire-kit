"""Agentic standard profile setup and verification."""

from __future__ import annotations

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


def _format_destination(path_template: str, task_id: str) -> Path:
    return Path(path_template.replace("{task-id}", task_id))


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
    rendered = template
    rendered = rendered.replace("- Project:\n", f"- Project: {project_name}\n")
    rendered = rendered.replace("- Selected profile: `starter | controlled | orchestrated | governed | production`\n", f"- Selected profile: `{profile.id}`\n")
    rendered = rendered.replace("- Declared profile: `starter | controlled | orchestrated | governed | production`\n", f"- Declared profile: `{profile.id}`\n")
    rendered = rendered.replace("- Upstream standard reference:\n", "- Upstream standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Standard reference:\n", "- Standard reference: processus-developpement-agentique/docs/norme-structure-agentique.md\n")
    rendered = rendered.replace("- Date:\n", f"- Date: {generated_at}\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{project_name}\"\n")
    rendered = rendered.replace("  project: \"\"\n", f"  project: \"{project_name}\"\n")
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


def setup_standard_profile(
    project_root: Path,
    *,
    profile_id: str,
    task_id: str = "bootstrap",
    project_name: str | None = None,
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

    enabled = [provider for provider in providers if isinstance(provider, dict) and provider.get("enabled") is True]
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
            data_policy = provider.get("data_policy")
            if not isinstance(data_policy, dict):
                _add_check(result, "providers.data_policy_missing", "error", f"Enabled provider {provider_id!r} has no data policy.", path=rel_path)

    routing = data.get("routing")
    if not isinstance(routing, dict):
        _add_check(result, "providers.routing_missing", "warning", "Provider registry has no routing policy.", path=rel_path)
    elif routing.get("require_capability_match") is not True or routing.get("require_data_policy_match") is not True:
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
        trust = source.get("trust")
        if source.get("enabled") is True and not isinstance(trust, dict):
            _add_check(result, "knowledge.trust_missing", "error", f"Knowledge source {source_id!r} has no trust block.", path=rel_path)
        elif isinstance(trust, dict) and not trust.get("level"):
            _add_check(result, "knowledge.trust_level_missing", "warning", f"Knowledge source {source_id!r} has no trust level.", path=rel_path)

    if profile.id in {"orchestrated", "governed", "production"} and real_sources == 0:
        _add_check(
            result,
            "knowledge.no_real_source",
            "warning",
            "Profile expects indexed external knowledge, but only placeholder sources are present.",
            path=rel_path,
        )


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
    _verify_evidence_pack(root, task_id, result)
    _verify_compliance_declaration(root, result)

    return result
