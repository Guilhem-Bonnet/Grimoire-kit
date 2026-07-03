"""Pack registry and validator for Grimoire-native composable packs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRegistryError

__all__ = [
    "PackActivationMode",
    "PackCompatibility",
    "PackCompatibilityCore",
    "PackComponent",
    "PackDistributionReport",
    "PackGateDecision",
    "PackInstallDryRun",
    "PackInstallDryRunChange",
    "PackInstallDryRunDiff",
    "PackInstallDryRunItem",
    "PackManifest",
    "PackMarketplaceCatalog",
    "PackMarketplaceCompatibility",
    "PackMarketplaceEntry",
    "PackMarketplacePublication",
    "PackMarketplaceSummary",
    "PackMetadata",
    "PackOperatorPackEntry",
    "PackOperatorPolicyEntry",
    "PackOperatorSummary",
    "PackOperatorView",
    "PackOverlay",
    "PackPolicy",
    "PackRegistry",
    "PackRequirement",
    "PackResolution",
    "PackResolutionComponent",
    "PackResolutionOverlay",
    "PackResolutionPack",
    "PackResolutionPolicy",
    "PackResolutionRequirement",
    "PackResolutionTest",
    "PackTest",
    "PackValidationIssue",
    "PackValidationReport",
]

class PackActivationMode(StrEnum):
    """Canonical pack lifecycle states (§10.4).

    Progression:
      discovered → validated → locked → active_shadow → active_canary → active_enforced
      Any state → disabled (operator override)
      active_* → quarantined (anomaly detected)
    """
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    LOCKED = "locked"
    DISABLED = "disabled"
    ACTIVE_SHADOW = "active_shadow"
    ACTIVE_CANARY = "active_canary"
    ACTIVE_ENFORCED = "active_enforced"
    QUARANTINED = "quarantined"


PACK_STATUSES = {"stable", "experimental", "internal"}
PACK_COMPONENT_SURFACES = {
    "skill",
    "prompt",
    "workflow",
    "instruction",
    "hook",
    "tool",
    "asset",
    "ui_surface",
    "policy",
    "docs",
}
PACK_REQUIREMENT_SURFACES = PACK_COMPONENT_SURFACES | {"policy"}
PACK_CRITICALITY = {"low", "medium", "high"}
PACK_OVERLAY_KINDS = {"file_overlay", "component_patch", "prompt_override", "policy_extension"}
PACK_TEST_KINDS = {"contract", "integration", "smoke", "policy", "visual"}
PACK_DISTRIBUTION_STATUSES = {"official", "community", "experimental", "internal"}


@dataclass(frozen=True, slots=True)
class PackMetadata:
    name: str
    version: str
    status: str
    owner: str
    description: str
    tags: tuple[str, ...] = ()
    license: str | None = None
    source: str | None = None
    provenance: tuple[str, ...] = ()
    distribution: str | None = None


@dataclass(frozen=True, slots=True)
class PackCompatibilityCore:
    min: str
    max: str


@dataclass(frozen=True, slots=True)
class PackCompatibility:
    core: PackCompatibilityCore
    surfaces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackComponent:
    id: str
    surface: str
    path: Path
    status: str
    criticality: str = "medium"
    policy_refs: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackRequirement:
    surface: str
    id: str


@dataclass(frozen=True, slots=True)
class PackOverlay:
    id: str
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class PackPolicy:
    id: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class PackTest:
    id: str
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class PackManifest:
    path: Path
    root: Path
    api_version: str
    kind: str
    metadata: PackMetadata
    compatibility: PackCompatibility
    components: tuple[PackComponent, ...]
    includes: tuple[str, ...] = ()
    requires: tuple[PackRequirement, ...] = ()
    overlays: tuple[PackOverlay, ...] = ()
    policies: tuple[PackPolicy, ...] = ()
    tests: tuple[PackTest, ...] = ()
    exports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackValidationIssue:
    code: str
    message: str
    severity: str = "error"
    path: str = ""


@dataclass(frozen=True, slots=True)
class PackValidationReport:
    manifest_path: Path
    pack_name: str
    issues: tuple[PackValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass(frozen=True, slots=True)
class PackResolutionPack:
    name: str
    version: str
    status: str
    root: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class PackResolutionComponent:
    pack_name: str
    id: str
    surface: str
    path: Path
    status: str
    criticality: str = "medium"
    policy_refs: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackResolutionOverlay:
    pack_name: str
    id: str
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class PackResolutionPolicy:
    pack_name: str
    id: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class PackResolutionTest:
    pack_name: str
    id: str
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class PackResolutionRequirement:
    pack_name: str
    surface: str
    id: str


@dataclass(frozen=True, slots=True)
class PackResolution:
    pack_name: str
    pack_version: str
    pack_status: str
    manifest_path: Path
    includes: tuple[str, ...]
    packs: tuple[PackResolutionPack, ...]
    components: tuple[PackResolutionComponent, ...]
    overlays: tuple[PackResolutionOverlay, ...]
    policies: tuple[PackResolutionPolicy, ...]
    tests: tuple[PackResolutionTest, ...]
    requirements: tuple[PackResolutionRequirement, ...]
    exports: tuple[str, ...]
    fingerprint: str

    def to_lock_document(self, include_fingerprint: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "apiVersion": "grimoire/v1alpha1",
            "kind": "PackLock",
            "pack": {
                "name": self.pack_name,
                "version": self.pack_version,
                "status": self.pack_status,
                "manifestPath": self.manifest_path.as_posix(),
                "includes": list(self.includes),
            },
            "packs": [
                {
                    "name": pack.name,
                    "version": pack.version,
                    "status": pack.status,
                    "root": pack.root.as_posix(),
                    "manifestPath": pack.manifest_path.as_posix(),
                }
                for pack in self.packs
            ],
            "components": [
                {
                    "pack": component.pack_name,
                    "id": component.id,
                    "surface": component.surface,
                    "path": component.path.as_posix(),
                    "status": component.status,
                    "criticality": component.criticality,
                    "policyRefs": list(component.policy_refs),
                    "exports": list(component.exports),
                }
                for component in self.components
            ],
            "overlays": [
                {
                    "pack": overlay.pack_name,
                    "id": overlay.id,
                    "kind": overlay.kind,
                    "path": overlay.path.as_posix(),
                }
                for overlay in self.overlays
            ],
            "policies": [
                {
                    "pack": policy.pack_name,
                    "id": policy.id,
                    "path": None if policy.path is None else policy.path.as_posix(),
                }
                for policy in self.policies
            ],
            "tests": [
                {
                    "pack": test.pack_name,
                    "id": test.id,
                    "kind": test.kind,
                    "path": test.path.as_posix(),
                }
                for test in self.tests
            ],
            "requires": [
                {
                    "pack": requirement.pack_name,
                    "surface": requirement.surface,
                    "id": requirement.id,
                }
                for requirement in self.requirements
            ],
            "exports": list(self.exports),
        }

        if include_fingerprint:
            document["fingerprint"] = self.fingerprint

        return document


@dataclass(frozen=True, slots=True)
class PackMarketplaceCompatibility:
    core_version: str | None
    minimum: str
    maximum: str
    compatible: bool | None
    reasons: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "coreVersion": self.core_version,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "compatible": self.compatible,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class PackMarketplacePublication:
    lock_path: Path
    lock_exists: bool
    lock_matches_resolution: bool
    fingerprint: str | None

    def to_document(self) -> dict[str, Any]:
        return {
            "lockPath": self.lock_path.as_posix(),
            "lockExists": self.lock_exists,
            "lockMatchesResolution": self.lock_matches_resolution,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PackGateDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class PackDistributionReport:
    status: str
    distribution: str
    source: str | None
    provenance: tuple[str, ...]
    compatibility: PackMarketplaceCompatibility
    publication: PackMarketplacePublication
    verified: bool
    install_gate: PackGateDecision
    publish_gate: PackGateDecision
    missing_tests: tuple[str, ...] = ()
    missing_policies: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    resolution_error: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "distribution": self.distribution,
            "source": self.source,
            "provenance": list(self.provenance),
            "compatibility": self.compatibility.to_document(),
            "publication": self.publication.to_document(),
            "verified": self.verified,
            "resolutionError": self.resolution_error,
            "missingTests": list(self.missing_tests),
            "missingPolicies": list(self.missing_policies),
            "missingRequirements": list(self.missing_requirements),
            "installGate": self.install_gate.to_document(),
            "publishGate": self.publish_gate.to_document(),
        }


@dataclass(frozen=True, slots=True)
class PackInstallDryRunItem:
    pack_name: str
    id: str
    kind: str
    path: Path | None = None
    status: str | None = None
    policy_refs: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "pack": self.pack_name,
            "id": self.id,
            "kind": self.kind,
            "path": None if self.path is None else self.path.as_posix(),
            "status": self.status,
            "policyRefs": list(self.policy_refs),
            "exports": list(self.exports),
        }


@dataclass(frozen=True, slots=True)
class PackInstallDryRunChange:
    previous: PackInstallDryRunItem
    current: PackInstallDryRunItem

    def to_document(self) -> dict[str, Any]:
        return {
            "previous": self.previous.to_document(),
            "current": self.current.to_document(),
        }


@dataclass(frozen=True, slots=True)
class PackInstallDryRunDiff:
    added: tuple[PackInstallDryRunItem, ...]
    removed: tuple[PackInstallDryRunItem, ...]
    changed: tuple[PackInstallDryRunChange, ...]
    unchanged: tuple[PackInstallDryRunItem, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "added": [item.to_document() for item in self.added],
            "removed": [item.to_document() for item in self.removed],
            "changed": [item.to_document() for item in self.changed],
            "unchanged": [item.to_document() for item in self.unchanged],
        }


@dataclass(frozen=True, slots=True)
class PackInstallDryRun:
    pack_name: str
    pack_version: str
    status: str
    distribution: str
    manifest_path: Path
    baseline_lock_path: Path
    baseline_available: bool
    report: PackDistributionReport
    components: PackInstallDryRunDiff
    overlays: PackInstallDryRunDiff
    policies: PackInstallDryRunDiff

    def to_document(self) -> dict[str, Any]:
        return {
            "pack": {
                "name": self.pack_name,
                "version": self.pack_version,
                "status": self.status,
                "distribution": self.distribution,
                "manifestPath": self.manifest_path.as_posix(),
            },
            "baseline": {
                "lockPath": self.baseline_lock_path.as_posix(),
                "available": self.baseline_available,
            },
            "distributionReport": self.report.to_document(),
            "components": self.components.to_document(),
            "overlays": self.overlays.to_document(),
            "policies": self.policies.to_document(),
        }


@dataclass(frozen=True, slots=True)
class PackMarketplaceEntry:
    name: str
    version: str
    status: str
    distribution: str
    owner: str
    description: str
    manifest_path: Path
    compatibility: PackMarketplaceCompatibility
    publication: PackMarketplacePublication
    tags: tuple[str, ...] = ()
    license: str | None = None
    source: str | None = None
    provenance: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    component_count: int = 0
    overlay_count: int = 0
    policy_count: int = 0
    test_count: int = 0
    requirement_count: int = 0
    validation_issues: tuple[PackValidationIssue, ...] = ()
    missing_tests: tuple[str, ...] = ()
    missing_policies: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    verified: bool = False
    installable: bool = False
    publishable: bool = False
    install_gate: PackGateDecision = field(default_factory=lambda: PackGateDecision(False))
    publish_gate: PackGateDecision = field(default_factory=lambda: PackGateDecision(False))
    gate_reasons: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "distribution": self.distribution,
            "owner": self.owner,
            "description": self.description,
            "manifestPath": self.manifest_path.as_posix(),
            "tags": list(self.tags),
            "license": self.license,
            "source": self.source,
            "provenance": list(self.provenance),
            "includes": list(self.includes),
            "surfaces": list(self.surfaces),
            "exports": list(self.exports),
            "componentCount": self.component_count,
            "overlayCount": self.overlay_count,
            "policyCount": self.policy_count,
            "testCount": self.test_count,
            "requirementCount": self.requirement_count,
            "missingTests": list(self.missing_tests),
            "missingPolicies": list(self.missing_policies),
            "missingRequirements": list(self.missing_requirements),
            "compatibility": self.compatibility.to_document(),
            "publication": self.publication.to_document(),
            "verified": self.verified,
            "installable": self.installable,
            "publishable": self.publishable,
            "installGate": self.install_gate.to_document(),
            "publishGate": self.publish_gate.to_document(),
            "gateReasons": list(self.gate_reasons),
            "validationIssues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity,
                    "path": issue.path,
                }
                for issue in self.validation_issues
            ],
        }


@dataclass(frozen=True, slots=True)
class PackMarketplaceSummary:
    entry_count: int
    verified_count: int
    installable_count: int
    publishable_count: int
    incompatible_count: int
    lock_mismatch_count: int

    def to_document(self) -> dict[str, Any]:
        return {
            "entryCount": self.entry_count,
            "verifiedCount": self.verified_count,
            "installableCount": self.installable_count,
            "publishableCount": self.publishable_count,
            "incompatibleCount": self.incompatible_count,
            "lockMismatchCount": self.lock_mismatch_count,
        }


@dataclass(frozen=True, slots=True)
class PackMarketplaceCatalog:
    root: Path
    core_version: str | None
    entries: tuple[PackMarketplaceEntry, ...]
    summary: PackMarketplaceSummary
    fingerprint: str

    def to_document(self, include_fingerprint: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "apiVersion": "grimoire/v1alpha1",
            "kind": "VerifiedPackMarketplace",
            "root": self.root.as_posix(),
            "coreVersion": self.core_version,
            "summary": self.summary.to_document(),
            "entries": [entry.to_document() for entry in self.entries],
        }

        if include_fingerprint:
            document["fingerprint"] = self.fingerprint

        return document


@dataclass(frozen=True, slots=True)
class PackOperatorPackEntry:
    name: str
    version: str
    status: str
    distribution: str
    owner: str
    manifest_path: Path
    verified: bool
    installable: bool
    publishable: bool
    component_count: int
    overlay_count: int
    policy_count: int
    test_count: int
    requirement_count: int
    includes: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    missing_tests: tuple[str, ...] = ()
    missing_policies: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    install_reasons: tuple[str, ...] = ()
    publish_reasons: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "distribution": self.distribution,
            "owner": self.owner,
            "manifestPath": self.manifest_path.as_posix(),
            "verified": self.verified,
            "installable": self.installable,
            "publishable": self.publishable,
            "componentCount": self.component_count,
            "overlayCount": self.overlay_count,
            "policyCount": self.policy_count,
            "testCount": self.test_count,
            "requirementCount": self.requirement_count,
            "includes": list(self.includes),
            "surfaces": list(self.surfaces),
            "missingTests": list(self.missing_tests),
            "missingPolicies": list(self.missing_policies),
            "missingRequirements": list(self.missing_requirements),
            "installReasons": list(self.install_reasons),
            "publishReasons": list(self.publish_reasons),
        }


@dataclass(frozen=True, slots=True)
class PackOperatorPolicyEntry:
    materialized_for: str
    source_pack: str
    policy_id: str
    path: Path | None
    present: bool

    def to_document(self) -> dict[str, Any]:
        return {
            "materializedFor": self.materialized_for,
            "sourcePack": self.source_pack,
            "policyId": self.policy_id,
            "path": None if self.path is None else self.path.as_posix(),
            "present": self.present,
        }


@dataclass(frozen=True, slots=True)
class PackOperatorSummary:
    pack_count: int
    installable_count: int
    publishable_count: int
    blocked_install_count: int
    blocked_publish_count: int
    policy_count: int
    missing_policy_count: int
    official_count: int
    community_count: int
    experimental_count: int
    internal_count: int

    def to_document(self) -> dict[str, Any]:
        return {
            "packCount": self.pack_count,
            "installableCount": self.installable_count,
            "publishableCount": self.publishable_count,
            "blockedInstallCount": self.blocked_install_count,
            "blockedPublishCount": self.blocked_publish_count,
            "policyCount": self.policy_count,
            "missingPolicyCount": self.missing_policy_count,
            "officialCount": self.official_count,
            "communityCount": self.community_count,
            "experimentalCount": self.experimental_count,
            "internalCount": self.internal_count,
        }


@dataclass(frozen=True, slots=True)
class PackOperatorView:
    root: Path
    core_version: str | None
    packs: tuple[PackOperatorPackEntry, ...]
    policies: tuple[PackOperatorPolicyEntry, ...]
    summary: PackOperatorSummary
    fingerprint: str

    def to_document(self, include_fingerprint: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "apiVersion": "grimoire/v1alpha1",
            "kind": "PackOperatorView",
            "root": self.root.as_posix(),
            "coreVersion": self.core_version,
            "summary": self.summary.to_document(),
            "packs": [entry.to_document() for entry in self.packs],
            "policies": [entry.to_document() for entry in self.policies],
        }

        if include_fingerprint:
            document["fingerprint"] = self.fingerprint

        return document


class PackRegistry:
    """Discover and validate local ``pack.yaml`` manifests."""

    def __init__(self, root: Path, manifest_name: str = "pack.yaml") -> None:
        self._root = root.resolve()
        self._manifest_name = manifest_name

    def discover_manifest_paths(self, search_root: Path | None = None) -> tuple[Path, ...]:
        base = (search_root or self._root).resolve()
        return tuple(sorted(path.resolve() for path in base.rglob(self._manifest_name)))

    def discover(self, search_root: Path | None = None) -> tuple[PackManifest, ...]:
        return tuple(self.load_manifest(path) for path in self.discover_manifest_paths(search_root))

    def load_manifest(self, path: Path) -> PackManifest:
        manifest_path = path.resolve()
        if not manifest_path.is_file():
            msg = f"Pack manifest not found: {manifest_path}"
            raise GrimoireRegistryError(msg)

        raw = _load_yaml(manifest_path)
        if not isinstance(raw, dict):
            msg = f"Pack manifest must be a mapping: {manifest_path}"
            raise GrimoireRegistryError(msg)

        metadata_raw = _require_mapping(raw.get("metadata"), "metadata", manifest_path)
        compatibility_raw = _require_mapping(raw.get("compatibility"), "compatibility", manifest_path)
        core_raw = _require_mapping(compatibility_raw.get("core"), "compatibility.core", manifest_path)
        components_raw = _require_sequence(raw.get("components"), "components", manifest_path)

        metadata = PackMetadata(
            name=_require_text(metadata_raw.get("name"), "metadata.name", manifest_path),
            version=_require_text(metadata_raw.get("version"), "metadata.version", manifest_path),
            status=_require_text(metadata_raw.get("status"), "metadata.status", manifest_path),
            owner=_require_text(metadata_raw.get("owner"), "metadata.owner", manifest_path),
            description=_require_text(metadata_raw.get("description"), "metadata.description", manifest_path),
            tags=tuple(_coerce_string_list(metadata_raw.get("tags"))),
            license=_optional_text(metadata_raw.get("license")),
            source=_optional_text(metadata_raw.get("source")),
            provenance=tuple(_coerce_string_list(metadata_raw.get("provenance"))),
            distribution=_optional_text(metadata_raw.get("distribution")),
        )
        compatibility = PackCompatibility(
            core=PackCompatibilityCore(
                min=_require_text(core_raw.get("min"), "compatibility.core.min", manifest_path),
                max=_require_text(core_raw.get("max"), "compatibility.core.max", manifest_path),
            ),
            surfaces=tuple(_coerce_string_list(compatibility_raw.get("surfaces"))),
        )

        components = tuple(self._parse_component(entry, manifest_path) for entry in components_raw)
        includes = tuple(_coerce_string_list(raw.get("includes")))
        requires = tuple(self._parse_requirement(entry, manifest_path) for entry in _coerce_sequence(raw.get("requires")))
        overlays = tuple(self._parse_overlay(entry, manifest_path) for entry in _coerce_sequence(raw.get("overlays")))
        policies = tuple(self._parse_policy(entry, manifest_path) for entry in _coerce_sequence(raw.get("policies")))
        tests = tuple(self._parse_test(entry, manifest_path) for entry in _coerce_sequence(raw.get("tests")))

        return PackManifest(
            path=manifest_path,
            root=manifest_path.parent,
            api_version=_require_text(raw.get("apiVersion"), "apiVersion", manifest_path),
            kind=_require_text(raw.get("kind"), "kind", manifest_path),
            metadata=metadata,
            compatibility=compatibility,
            components=components,
            includes=includes,
            requires=requires,
            overlays=overlays,
            policies=policies,
            tests=tests,
            exports=tuple(_coerce_string_list(raw.get("exports"))),
        )

    def validate_manifest(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
    ) -> PackValidationReport:
        issues: list[PackValidationIssue] = []
        manifests = pack_index or {item.metadata.name: item for item in self.discover(manifest.root.parent)}
        path_index = {item.path.resolve(): item for item in manifests.values()}

        if manifest.api_version.strip() == "":
            issues.append(PackValidationIssue(code="MISSING_API_VERSION", message="apiVersion is required."))

        if manifest.kind != "Pack":
            issues.append(PackValidationIssue(code="INVALID_KIND", message=f"Unsupported kind '{manifest.kind}'.", path="kind"))

        if manifest.metadata.status not in PACK_STATUSES:
            issues.append(
                PackValidationIssue(
                    code="INVALID_STATUS",
                    message=f"Unsupported pack status '{manifest.metadata.status}'.",
                    path="metadata.status",
                )
            )

        if (
            manifest.metadata.distribution is not None
            and manifest.metadata.distribution not in PACK_DISTRIBUTION_STATUSES
        ):
            issues.append(
                PackValidationIssue(
                    code="INVALID_DISTRIBUTION",
                    message=f"Unsupported pack distribution '{manifest.metadata.distribution}'.",
                    path="metadata.distribution",
                )
            )

        if manifest.metadata.status == "internal" and manifest.metadata.distribution not in {None, "internal"}:
            issues.append(
                PackValidationIssue(
                    code="INVALID_DISTRIBUTION_STATUS_COMBINATION",
                    message="Internal packs must use internal distribution.",
                    path="metadata.distribution",
                )
            )

        if manifest.metadata.status == "experimental" and manifest.metadata.distribution not in {None, "experimental"}:
            issues.append(
                PackValidationIssue(
                    code="INVALID_DISTRIBUTION_STATUS_COMBINATION",
                    message="Experimental packs must use experimental distribution.",
                    path="metadata.distribution",
                )
            )

        if manifest.metadata.status == "stable" and manifest.metadata.distribution in {"experimental", "internal"}:
            issues.append(
                PackValidationIssue(
                    code="INVALID_DISTRIBUTION_STATUS_COMBINATION",
                    message="Stable packs must use official or community distribution.",
                    path="metadata.distribution",
                )
            )

        if len(manifest.components) == 0:
            issues.append(PackValidationIssue(code="MISSING_COMPONENTS", message="At least one component is required."))

        if manifest.metadata.status == "stable" and len(manifest.tests) == 0:
            issues.append(
                PackValidationIssue(
                    code="STABLE_PACK_REQUIRES_TESTS",
                    message="Stable packs must declare at least one test.",
                    path="tests",
                )
            )

        include_closure, include_issues = self._collect_includes(manifest, manifests, path_index)
        issues.extend(include_issues)

        available_components = {
            (component.surface, component.id): source.metadata.name
            for source in include_closure
            for component in source.components
        }
        available_policies = {
            policy.id: source.metadata.name for source in include_closure for policy in source.policies if policy.id != ""
        }

        issues.extend(self._validate_components(manifest))
        issues.extend(self._validate_overlays(manifest))
        issues.extend(self._validate_policies(manifest))
        issues.extend(self._validate_tests(manifest))
        issues.extend(self._validate_requires(manifest, available_components, available_policies))
        issues.extend(self._validate_duplicate_components(include_closure))

        return PackValidationReport(
            manifest_path=manifest.path,
            pack_name=manifest.metadata.name,
            issues=tuple(issues),
        )

    def validate_all(self, search_root: Path | None = None) -> tuple[PackValidationReport, ...]:
        manifests = self.discover(search_root)
        index = {manifest.metadata.name: manifest for manifest in manifests}
        return tuple(self.validate_manifest(manifest, index) for manifest in manifests)

    def resolve_manifest(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
    ) -> PackResolution:
        manifests = (
            {item.metadata.name: item for item in self.discover(manifest.root.parent)}
            if pack_index is None
            else dict(pack_index)
        )
        manifests[manifest.metadata.name] = manifest

        validation = self.validate_manifest(manifest, manifests)
        if not validation.is_valid:
            msg = (
                f"Cannot resolve pack '{manifest.metadata.name}' because validation failed: "
                f"{_format_validation_issues(validation.issues)}"
            )
            raise GrimoireRegistryError(msg)

        path_index = {item.path.resolve(): item for item in manifests.values()}
        include_closure, include_issues = self._collect_includes(manifest, manifests, path_index)
        if include_issues:
            msg = (
                f"Cannot resolve pack '{manifest.metadata.name}' because includes are invalid: "
                f"{_format_validation_issues(include_issues)}"
            )
            raise GrimoireRegistryError(msg)

        resolution = PackResolution(
            pack_name=manifest.metadata.name,
            pack_version=manifest.metadata.version,
            pack_status=manifest.metadata.status,
            manifest_path=self._path_relative_to_registry(manifest.path),
            includes=manifest.includes,
            packs=tuple(self._materialize_pack_entry(item) for item in include_closure),
            components=self._materialize_components(include_closure),
            overlays=self._materialize_overlays(include_closure),
            policies=self._materialize_policies(include_closure),
            tests=self._materialize_tests(include_closure),
            requirements=self._materialize_requirements(include_closure),
            exports=self._materialize_exports(include_closure),
            fingerprint="",
        )
        fingerprint = _fingerprint_lock_document(resolution.to_lock_document(include_fingerprint=False))
        return replace(resolution, fingerprint=fingerprint)

    def write_lock(
        self,
        manifest: PackManifest,
        destination: Path | None = None,
        pack_index: dict[str, PackManifest] | None = None,
    ) -> Path:
        resolution = self.resolve_manifest(manifest, pack_index)
        raw_destination = manifest.root / "pack.lock.json" if destination is None else destination
        lock_path = (
            raw_destination.resolve()
            if raw_destination.is_absolute()
            else (manifest.root / raw_destination).resolve()
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(resolution.to_lock_document(), indent=2, sort_keys=True)
        lock_path.write_text(f"{payload}\n", encoding="utf-8")
        return lock_path

    def evaluate_distribution(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
        core_version: str | None = None,
        lock_path: Path | None = None,
    ) -> PackDistributionReport:
        report, _, _ = self._evaluate_distribution_state(
            manifest,
            pack_index=pack_index,
            core_version=core_version,
            lock_path=lock_path,
        )
        return report

    def ensure_installable(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
        core_version: str | None = None,
        lock_path: Path | None = None,
    ) -> PackDistributionReport:
        report = self.evaluate_distribution(
            manifest,
            pack_index=pack_index,
            core_version=core_version,
            lock_path=lock_path,
        )
        if report.install_gate.allowed:
            return report

        msg = (
            f"Pack '{manifest.metadata.name}' is not installable: "
            f"{'; '.join(report.install_gate.reasons)}"
        )
        raise GrimoireRegistryError(msg)

    def ensure_publishable(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
        core_version: str | None = None,
        lock_path: Path | None = None,
    ) -> PackDistributionReport:
        report = self.evaluate_distribution(
            manifest,
            pack_index=pack_index,
            core_version=core_version,
            lock_path=lock_path,
        )
        if report.publish_gate.allowed:
            return report

        msg = (
            f"Pack '{manifest.metadata.name}' is not publishable: "
            f"{'; '.join(report.publish_gate.reasons)}"
        )
        raise GrimoireRegistryError(msg)

    def preview_installation(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None = None,
        core_version: str | None = None,
        lock_path: Path | None = None,
    ) -> PackInstallDryRun:
        report, resolution, _ = self._evaluate_distribution_state(
            manifest,
            pack_index=pack_index,
            core_version=core_version,
            lock_path=lock_path,
        )
        resolved_lock_path = self._resolve_lock_path(manifest, lock_path)
        lock_document = self._load_lock_document(resolved_lock_path)

        current_components = () if resolution is None else _dry_run_components_from_resolution(resolution)
        current_overlays = () if resolution is None else _dry_run_overlays_from_resolution(resolution)
        current_policies = () if resolution is None else _dry_run_policies_from_resolution(resolution)

        previous_components = _dry_run_components_from_lock(lock_document)
        previous_overlays = _dry_run_overlays_from_lock(lock_document)
        previous_policies = _dry_run_policies_from_lock(lock_document)

        return PackInstallDryRun(
            pack_name=manifest.metadata.name,
            pack_version=manifest.metadata.version,
            status=manifest.metadata.status,
            distribution=report.distribution,
            manifest_path=self._path_relative_to_registry(manifest.path),
            baseline_lock_path=self._path_relative_to_registry(resolved_lock_path),
            baseline_available=lock_document is not None,
            report=report,
            components=_diff_dry_run_items(current_components, previous_components),
            overlays=_diff_dry_run_items(current_overlays, previous_overlays),
            policies=_diff_dry_run_items(current_policies, previous_policies),
        )

    def build_verified_marketplace(
        self,
        search_root: Path | None = None,
        core_version: str | None = None,
    ) -> PackMarketplaceCatalog:
        search_base = self._resolve_registry_path(search_root)
        manifests = self.discover(search_base)
        index = {manifest.metadata.name: manifest for manifest in manifests}
        entries = tuple(
            sorted(
                (self._build_marketplace_entry(manifest, index, core_version) for manifest in manifests),
                key=lambda entry: (entry.name, entry.version),
            )
        )
        summary = PackMarketplaceSummary(
            entry_count=len(entries),
            verified_count=sum(1 for entry in entries if entry.verified),
            installable_count=sum(1 for entry in entries if entry.installable),
            publishable_count=sum(1 for entry in entries if entry.publishable),
            incompatible_count=sum(1 for entry in entries if entry.compatibility.compatible is False),
            lock_mismatch_count=sum(
                1
                for entry in entries
                if entry.publication.lock_exists and not entry.publication.lock_matches_resolution
            ),
        )
        catalog = PackMarketplaceCatalog(
            root=self._path_relative_to_registry(search_base),
            core_version=core_version,
            entries=entries,
            summary=summary,
            fingerprint="",
        )
        fingerprint = _fingerprint_json_document(catalog.to_document(include_fingerprint=False))
        return replace(catalog, fingerprint=fingerprint)

    def write_verified_marketplace(
        self,
        destination: Path | None = None,
        search_root: Path | None = None,
        core_version: str | None = None,
    ) -> Path:
        catalog = self.build_verified_marketplace(search_root=search_root, core_version=core_version)
        raw_destination = self._root / "verified-marketplace.json" if destination is None else destination
        destination_path = (
            raw_destination.resolve()
            if raw_destination.is_absolute()
            else (self._root / raw_destination).resolve()
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(catalog.to_document(), indent=2, sort_keys=True)
        destination_path.write_text(f"{payload}\n", encoding="utf-8")
        return destination_path

    def build_operator_view(
        self,
        search_root: Path | None = None,
        core_version: str | None = None,
    ) -> PackOperatorView:
        search_base = self._resolve_registry_path(search_root)
        manifests = self.discover(search_base)
        index = {manifest.metadata.name: manifest for manifest in manifests}
        pack_entries: list[PackOperatorPackEntry] = []
        policy_entries: list[PackOperatorPolicyEntry] = []

        for manifest in manifests:
            distribution, resolution, _ = self._evaluate_distribution_state(
                manifest,
                pack_index=index,
                core_version=core_version,
            )
            pack_entries.append(
                PackOperatorPackEntry(
                    name=manifest.metadata.name,
                    version=manifest.metadata.version,
                    status=manifest.metadata.status,
                    distribution=distribution.distribution,
                    owner=manifest.metadata.owner,
                    manifest_path=self._path_relative_to_registry(manifest.path),
                    verified=distribution.verified,
                    installable=distribution.install_gate.allowed,
                    publishable=distribution.publish_gate.allowed,
                    component_count=len(resolution.components) if resolution is not None else len(manifest.components),
                    overlay_count=len(resolution.overlays) if resolution is not None else len(manifest.overlays),
                    policy_count=len(resolution.policies) if resolution is not None else len(manifest.policies),
                    test_count=len(resolution.tests) if resolution is not None else len(manifest.tests),
                    requirement_count=(
                        len(resolution.requirements) if resolution is not None else len(manifest.requires)
                    ),
                    includes=manifest.includes,
                    surfaces=self._materialize_marketplace_surfaces(manifest, resolution),
                    missing_tests=distribution.missing_tests,
                    missing_policies=distribution.missing_policies,
                    missing_requirements=distribution.missing_requirements,
                    install_reasons=distribution.install_gate.reasons,
                    publish_reasons=distribution.publish_gate.reasons,
                )
            )
            policy_entries.extend(self._build_operator_policy_entries(manifest, resolution))

        sorted_pack_entries = tuple(sorted(pack_entries, key=lambda entry: (entry.name, entry.version)))
        sorted_policy_entries = tuple(
            sorted(
                policy_entries,
                key=lambda entry: (entry.materialized_for, entry.source_pack, entry.policy_id),
            )
        )
        summary = PackOperatorSummary(
            pack_count=len(sorted_pack_entries),
            installable_count=sum(1 for entry in sorted_pack_entries if entry.installable),
            publishable_count=sum(1 for entry in sorted_pack_entries if entry.publishable),
            blocked_install_count=sum(1 for entry in sorted_pack_entries if not entry.installable),
            blocked_publish_count=sum(1 for entry in sorted_pack_entries if not entry.publishable),
            policy_count=len(sorted_policy_entries),
            missing_policy_count=sum(1 for entry in sorted_policy_entries if not entry.present),
            official_count=sum(1 for entry in sorted_pack_entries if entry.distribution == "official"),
            community_count=sum(1 for entry in sorted_pack_entries if entry.distribution == "community"),
            experimental_count=sum(1 for entry in sorted_pack_entries if entry.distribution == "experimental"),
            internal_count=sum(1 for entry in sorted_pack_entries if entry.distribution == "internal"),
        )
        view = PackOperatorView(
            root=self._path_relative_to_registry(search_base),
            core_version=core_version,
            packs=sorted_pack_entries,
            policies=sorted_policy_entries,
            summary=summary,
            fingerprint="",
        )
        fingerprint = _fingerprint_json_document(view.to_document(include_fingerprint=False))
        return replace(view, fingerprint=fingerprint)

    def _build_marketplace_entry(
        self,
        manifest: PackManifest,
        manifests: dict[str, PackManifest],
        core_version: str | None,
    ) -> PackMarketplaceEntry:
        distribution, resolution, validation = self._evaluate_distribution_state(
            manifest,
            pack_index=manifests,
            core_version=core_version,
        )

        gate_reasons = _dedupe_strings(
            [*distribution.install_gate.reasons, *distribution.publish_gate.reasons]
        )

        return PackMarketplaceEntry(
            name=manifest.metadata.name,
            version=manifest.metadata.version,
            status=manifest.metadata.status,
            distribution=distribution.distribution,
            owner=manifest.metadata.owner,
            description=manifest.metadata.description,
            manifest_path=self._path_relative_to_registry(manifest.path),
            compatibility=distribution.compatibility,
            publication=distribution.publication,
            tags=manifest.metadata.tags,
            license=manifest.metadata.license,
            source=manifest.metadata.source,
            provenance=manifest.metadata.provenance,
            includes=manifest.includes,
            surfaces=self._materialize_marketplace_surfaces(manifest, resolution),
            exports=resolution.exports if resolution is not None else manifest.exports,
            component_count=len(resolution.components) if resolution is not None else len(manifest.components),
            overlay_count=len(resolution.overlays) if resolution is not None else len(manifest.overlays),
            policy_count=len(resolution.policies) if resolution is not None else len(manifest.policies),
            test_count=len(resolution.tests) if resolution is not None else len(manifest.tests),
            requirement_count=len(resolution.requirements) if resolution is not None else len(manifest.requires),
            validation_issues=validation.issues,
            missing_tests=distribution.missing_tests,
            missing_policies=distribution.missing_policies,
            missing_requirements=distribution.missing_requirements,
            verified=distribution.verified,
            installable=distribution.install_gate.allowed,
            publishable=distribution.publish_gate.allowed,
            install_gate=distribution.install_gate,
            publish_gate=distribution.publish_gate,
            gate_reasons=tuple(gate_reasons),
        )

    def _evaluate_distribution_state(
        self,
        manifest: PackManifest,
        pack_index: dict[str, PackManifest] | None,
        core_version: str | None,
        lock_path: Path | None = None,
    ) -> tuple[PackDistributionReport, PackResolution | None, PackValidationReport]:
        manifests = (
            {item.metadata.name: item for item in self.discover(manifest.root.parent)}
            if pack_index is None
            else dict(pack_index)
        )
        manifests[manifest.metadata.name] = manifest

        validation = self.validate_manifest(manifest, manifests)
        resolution: PackResolution | None = None
        resolution_error: str | None = None

        try:
            resolution = self.resolve_manifest(manifest, manifests)
        except GrimoireRegistryError as exc:
            resolution_error = str(exc)

        compatibility = _evaluate_marketplace_compatibility(manifest.compatibility, core_version)
        publication, publication_reasons = self._evaluate_publication_status(
            manifest,
            resolution=resolution,
            lock_path=lock_path,
        )

        verified = validation.is_valid and resolution is not None
        distribution_status = _resolve_distribution_status(manifest.metadata)
        missing_tests = tuple(
            _collect_issue_messages(validation.issues, {"MISSING_TEST_PATH", "STABLE_PACK_REQUIRES_TESTS"})
        )
        missing_policies = tuple(_collect_issue_messages(validation.issues, {"MISSING_POLICY_PATH"}))
        missing_requirements = tuple(_collect_issue_messages(validation.issues, {"UNSATISFIED_REQUIREMENT"}))

        install_reasons: list[str] = []
        if not validation.is_valid:
            install_reasons.extend(issue.message for issue in validation.issues if issue.severity == "error")
        elif resolution_error is not None:
            install_reasons.append(resolution_error)

        install_reasons.extend(_build_gate_compatibility_reasons(core_version, compatibility))
        install_reasons.extend(publication_reasons)
        install_gate = PackGateDecision(
            allowed=verified and compatibility.compatible is True and publication.lock_exists and publication.lock_matches_resolution,
            reasons=tuple(_dedupe_strings(install_reasons)),
        )

        publish_gate = PackGateDecision(
            allowed=install_gate.allowed and not missing_tests and not missing_policies and not missing_requirements,
            reasons=tuple(
                _dedupe_strings(
                    [*install_gate.reasons, *missing_tests, *missing_policies, *missing_requirements]
                )
            ),
        )

        report = PackDistributionReport(
            status=manifest.metadata.status,
            distribution=distribution_status,
            source=manifest.metadata.source,
            provenance=manifest.metadata.provenance,
            compatibility=compatibility,
            publication=publication,
            verified=verified,
            install_gate=install_gate,
            publish_gate=publish_gate,
            missing_tests=missing_tests,
            missing_policies=missing_policies,
            missing_requirements=missing_requirements,
            resolution_error=resolution_error,
        )
        return report, resolution, validation

    def _evaluate_publication_status(
        self,
        manifest: PackManifest,
        resolution: PackResolution | None,
        lock_path: Path | None = None,
    ) -> tuple[PackMarketplacePublication, tuple[str, ...]]:
        resolved_lock_path = self._resolve_lock_path(manifest, lock_path)
        lock_exists = resolved_lock_path.is_file()
        lock_matches_resolution = False
        fingerprint = None if resolution is None else resolution.fingerprint
        reasons: list[str] = []

        if not lock_exists:
            reasons.append(f"Lock file {resolved_lock_path.name} is missing.")
        elif resolution is None:
            reasons.append(f"Lock file {resolved_lock_path.name} cannot be validated because resolution failed.")
        else:
            try:
                payload = json.loads(resolved_lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reasons.append(f"Lock file {resolved_lock_path.name} is unreadable.")
            else:
                lock_matches_resolution = payload.get("fingerprint") == resolution.fingerprint
                if not lock_matches_resolution:
                    reasons.append("Lock fingerprint does not match resolved pack content.")

        publication = PackMarketplacePublication(
            lock_path=self._path_relative_to_registry(resolved_lock_path),
            lock_exists=lock_exists,
            lock_matches_resolution=lock_matches_resolution,
            fingerprint=fingerprint,
        )
        return publication, tuple(_dedupe_strings(reasons))

    def _resolve_lock_path(self, manifest: PackManifest, lock_path: Path | None = None) -> Path:
        raw_lock_path = manifest.root / "pack.lock.json" if lock_path is None else lock_path
        return raw_lock_path.resolve() if raw_lock_path.is_absolute() else (manifest.root / raw_lock_path).resolve()

    def _load_lock_document(self, lock_path: Path) -> dict[str, Any] | None:
        if not lock_path.is_file():
            return None

        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        return payload if isinstance(payload, dict) else None

    def _materialize_marketplace_surfaces(
        self,
        manifest: PackManifest,
        resolution: PackResolution | None,
    ) -> tuple[str, ...]:
        surfaces = list(manifest.compatibility.surfaces)
        component_surfaces = (
            [component.surface for component in resolution.components]
            if resolution is not None
            else [component.surface for component in manifest.components]
        )

        return tuple(_dedupe_strings([*surfaces, *component_surfaces]))

    def _build_operator_policy_entries(
        self,
        manifest: PackManifest,
        resolution: PackResolution | None,
    ) -> list[PackOperatorPolicyEntry]:
        if resolution is not None:
            return [
                PackOperatorPolicyEntry(
                    materialized_for=manifest.metadata.name,
                    source_pack=policy.pack_name,
                    policy_id=policy.id,
                    path=policy.path,
                    present=True,
                )
                for policy in resolution.policies
            ]

        return [
            PackOperatorPolicyEntry(
                materialized_for=manifest.metadata.name,
                source_pack=manifest.metadata.name,
                policy_id=policy.id,
                path=None if policy.path is None else self._path_relative_to_registry(manifest.root / policy.path),
                present=policy.path is None or (manifest.root / policy.path).is_file(),
            )
            for policy in manifest.policies
        ]

    def _resolve_registry_path(self, path: Path | None) -> Path:
        if path is None:
            return self._root

        return path.resolve() if path.is_absolute() else (self._root / path).resolve()

    def _parse_component(self, entry: Any, manifest_path: Path) -> PackComponent:
        data = _require_mapping(entry, "components[]", manifest_path)
        return PackComponent(
            id=_require_text(data.get("id"), "components[].id", manifest_path),
            surface=_require_text(data.get("surface"), "components[].surface", manifest_path),
            path=Path(_require_text(data.get("path"), "components[].path", manifest_path)),
            status=_require_text(data.get("status"), "components[].status", manifest_path),
            criticality=_optional_text(data.get("criticality")) or "medium",
            policy_refs=tuple(_coerce_string_list(data.get("policyRefs"))),
            exports=tuple(_coerce_string_list(data.get("exports"))),
        )

    def _parse_requirement(self, entry: Any, manifest_path: Path) -> PackRequirement:
        data = _require_mapping(entry, "requires[]", manifest_path)
        return PackRequirement(
            surface=_require_text(data.get("surface"), "requires[].surface", manifest_path),
            id=_require_text(data.get("id"), "requires[].id", manifest_path),
        )

    def _parse_overlay(self, entry: Any, manifest_path: Path) -> PackOverlay:
        data = _require_mapping(entry, "overlays[]", manifest_path)
        return PackOverlay(
            id=_require_text(data.get("id"), "overlays[].id", manifest_path),
            kind=_require_text(data.get("kind"), "overlays[].kind", manifest_path),
            path=Path(_require_text(data.get("path"), "overlays[].path", manifest_path)),
        )

    def _parse_policy(self, entry: Any, manifest_path: Path) -> PackPolicy:
        if isinstance(entry, str):
            return PackPolicy(id=entry.strip())

        data = _require_mapping(entry, "policies[]", manifest_path)
        path_text = _optional_text(data.get("path"))
        return PackPolicy(
            id=_require_text(data.get("id"), "policies[].id", manifest_path),
            path=None if path_text is None else Path(path_text),
        )

    def _parse_test(self, entry: Any, manifest_path: Path) -> PackTest:
        data = _require_mapping(entry, "tests[]", manifest_path)
        return PackTest(
            id=_require_text(data.get("id"), "tests[].id", manifest_path),
            kind=_require_text(data.get("kind"), "tests[].kind", manifest_path),
            path=Path(_require_text(data.get("path"), "tests[].path", manifest_path)),
        )

    def _collect_includes(
        self,
        manifest: PackManifest,
        manifests: dict[str, PackManifest],
        path_index: dict[Path, PackManifest],
    ) -> tuple[tuple[PackManifest, ...], list[PackValidationIssue]]:
        collected: list[PackManifest] = [manifest]
        issues: list[PackValidationIssue] = []
        visiting = {manifest.metadata.name}
        visited = {manifest.metadata.name}

        def visit(current: PackManifest) -> None:
            for include_ref in current.includes:
                target = self._resolve_include(include_ref, current, manifests, path_index)
                if target is None:
                    issues.append(
                        PackValidationIssue(
                            code="UNKNOWN_INCLUDE",
                            message=f"Unable to resolve include '{include_ref}'.",
                            path=f"includes:{include_ref}",
                        )
                    )
                    continue

                if target.metadata.name in visiting:
                    issues.append(
                        PackValidationIssue(
                            code="INCLUDE_CYCLE",
                            message=f"Include cycle detected through '{target.metadata.name}'.",
                            path=f"includes:{include_ref}",
                        )
                    )
                    continue

                if target.metadata.name in visited:
                    continue

                visiting.add(target.metadata.name)
                visited.add(target.metadata.name)
                collected.append(target)
                visit(target)
                visiting.remove(target.metadata.name)

        visit(manifest)
        return tuple(collected), issues

    def _resolve_include(
        self,
        include_ref: str,
        manifest: PackManifest,
        manifests: dict[str, PackManifest],
        path_index: dict[Path, PackManifest],
    ) -> PackManifest | None:
        named = manifests.get(include_ref)
        if named is not None:
            return named

        candidate = (manifest.root / include_ref).resolve()
        if candidate.is_dir():
            candidate = candidate / self._manifest_name

        if candidate.name != self._manifest_name and candidate.suffix == "":
            candidate = candidate / self._manifest_name

        if not candidate.is_file():
            return None

        indexed = path_index.get(candidate)
        if indexed is not None:
            return indexed

        loaded = self.load_manifest(candidate)
        manifests[loaded.metadata.name] = loaded
        path_index[loaded.path.resolve()] = loaded
        return loaded

    def _materialize_pack_entry(self, manifest: PackManifest) -> PackResolutionPack:
        return PackResolutionPack(
            name=manifest.metadata.name,
            version=manifest.metadata.version,
            status=manifest.metadata.status,
            root=self._path_relative_to_registry(manifest.root),
            manifest_path=self._path_relative_to_registry(manifest.path),
        )

    def _materialize_components(self, manifests: tuple[PackManifest, ...]) -> tuple[PackResolutionComponent, ...]:
        components: list[PackResolutionComponent] = []
        seen_keys: set[tuple[str, str]] = set()

        for manifest in manifests:
            for component in manifest.components:
                key = (component.surface, component.id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                components.append(
                    PackResolutionComponent(
                        pack_name=manifest.metadata.name,
                        id=component.id,
                        surface=component.surface,
                        path=self._path_relative_to_registry(manifest.root / component.path),
                        status=component.status,
                        criticality=component.criticality,
                        policy_refs=component.policy_refs,
                        exports=component.exports,
                    )
                )

        return tuple(components)

    def _materialize_overlays(self, manifests: tuple[PackManifest, ...]) -> tuple[PackResolutionOverlay, ...]:
        overlays: list[PackResolutionOverlay] = []

        for manifest in manifests:
            for overlay in manifest.overlays:
                overlays.append(
                    PackResolutionOverlay(
                        pack_name=manifest.metadata.name,
                        id=overlay.id,
                        kind=overlay.kind,
                        path=self._path_relative_to_registry(manifest.root / overlay.path),
                    )
                )

        return tuple(overlays)

    def _materialize_policies(self, manifests: tuple[PackManifest, ...]) -> tuple[PackResolutionPolicy, ...]:
        policies: list[PackResolutionPolicy] = []
        seen_ids: set[str] = set()

        for manifest in manifests:
            for policy in manifest.policies:
                if policy.id in seen_ids:
                    continue
                seen_ids.add(policy.id)
                policies.append(
                    PackResolutionPolicy(
                        pack_name=manifest.metadata.name,
                        id=policy.id,
                        path=None if policy.path is None else self._path_relative_to_registry(manifest.root / policy.path),
                    )
                )

        return tuple(policies)

    def _materialize_tests(self, manifests: tuple[PackManifest, ...]) -> tuple[PackResolutionTest, ...]:
        tests: list[PackResolutionTest] = []

        for manifest in manifests:
            for test in manifest.tests:
                tests.append(
                    PackResolutionTest(
                        pack_name=manifest.metadata.name,
                        id=test.id,
                        kind=test.kind,
                        path=self._path_relative_to_registry(manifest.root / test.path),
                    )
                )

        return tuple(tests)

    def _materialize_requirements(self, manifests: tuple[PackManifest, ...]) -> tuple[PackResolutionRequirement, ...]:
        requirements: list[PackResolutionRequirement] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for manifest in manifests:
            for requirement in manifest.requires:
                key = (manifest.metadata.name, requirement.surface, requirement.id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                requirements.append(
                    PackResolutionRequirement(
                        pack_name=manifest.metadata.name,
                        surface=requirement.surface,
                        id=requirement.id,
                    )
                )

        return tuple(requirements)

    def _materialize_exports(self, manifests: tuple[PackManifest, ...]) -> tuple[str, ...]:
        exports: list[str] = []
        seen_exports: set[str] = set()

        for manifest in manifests:
            for export_name in manifest.exports:
                if export_name in seen_exports:
                    continue
                seen_exports.add(export_name)
                exports.append(export_name)

            for component in manifest.components:
                for export_name in component.exports:
                    if export_name in seen_exports:
                        continue
                    seen_exports.add(export_name)
                    exports.append(export_name)

        return tuple(exports)

    def _path_relative_to_registry(self, path: Path) -> Path:
        resolved = path.resolve()

        try:
            return resolved.relative_to(self._root)
        except ValueError:
            return resolved

    def _validate_components(self, manifest: PackManifest) -> list[PackValidationIssue]:
        issues: list[PackValidationIssue] = []
        seen_keys: set[tuple[str, str]] = set()

        for component in manifest.components:
            if component.surface not in PACK_COMPONENT_SURFACES:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_COMPONENT_SURFACE",
                        message=f"Unsupported component surface '{component.surface}'.",
                        path=f"components:{component.id}",
                    )
                )

            if component.status not in PACK_STATUSES:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_COMPONENT_STATUS",
                        message=f"Unsupported component status '{component.status}'.",
                        path=f"components:{component.id}.status",
                    )
                )

            if component.criticality not in PACK_CRITICALITY:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_COMPONENT_CRITICALITY",
                        message=f"Unsupported component criticality '{component.criticality}'.",
                        path=f"components:{component.id}.criticality",
                    )
                )

            if not (manifest.root / component.path).is_file():
                issues.append(
                    PackValidationIssue(
                        code="MISSING_COMPONENT_PATH",
                        message=f"Component path does not exist: {component.path}",
                        path=f"components:{component.id}.path",
                    )
                )

            key = (component.surface, component.id)
            if key in seen_keys:
                issues.append(
                    PackValidationIssue(
                        code="DUPLICATE_COMPONENT",
                        message=f"Duplicate component '{component.surface}/{component.id}' in one manifest.",
                        path=f"components:{component.id}",
                    )
                )
            seen_keys.add(key)

        return issues

    def _validate_overlays(self, manifest: PackManifest) -> list[PackValidationIssue]:
        issues: list[PackValidationIssue] = []
        for overlay in manifest.overlays:
            if overlay.kind not in PACK_OVERLAY_KINDS:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_OVERLAY_KIND",
                        message=f"Unsupported overlay kind '{overlay.kind}'.",
                        path=f"overlays:{overlay.id}.kind",
                    )
                )

            if not (manifest.root / overlay.path).is_file():
                issues.append(
                    PackValidationIssue(
                        code="MISSING_OVERLAY_PATH",
                        message=f"Overlay path does not exist: {overlay.path}",
                        path=f"overlays:{overlay.id}.path",
                    )
                )

        return issues

    def _validate_policies(self, manifest: PackManifest) -> list[PackValidationIssue]:
        issues: list[PackValidationIssue] = []
        for policy in manifest.policies:
            if policy.path is not None and not (manifest.root / policy.path).is_file():
                issues.append(
                    PackValidationIssue(
                        code="MISSING_POLICY_PATH",
                        message=f"Policy path does not exist: {policy.path}",
                        path=f"policies:{policy.id}.path",
                    )
                )

        return issues

    def _validate_tests(self, manifest: PackManifest) -> list[PackValidationIssue]:
        issues: list[PackValidationIssue] = []
        for test in manifest.tests:
            if test.kind not in PACK_TEST_KINDS:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_TEST_KIND",
                        message=f"Unsupported test kind '{test.kind}'.",
                        path=f"tests:{test.id}.kind",
                    )
                )

            if not (manifest.root / test.path).is_file():
                issues.append(
                    PackValidationIssue(
                        code="MISSING_TEST_PATH",
                        message=f"Test path does not exist: {test.path}",
                        path=f"tests:{test.id}.path",
                    )
                )

        return issues

    def _validate_requires(
        self,
        manifest: PackManifest,
        available_components: dict[tuple[str, str], str],
        available_policies: dict[str, str],
    ) -> list[PackValidationIssue]:
        issues: list[PackValidationIssue] = []
        for requirement in manifest.requires:
            if requirement.surface not in PACK_REQUIREMENT_SURFACES:
                issues.append(
                    PackValidationIssue(
                        code="INVALID_REQUIREMENT_SURFACE",
                        message=f"Unsupported requirement surface '{requirement.surface}'.",
                        path=f"requires:{requirement.id}.surface",
                    )
                )
                continue

            if requirement.surface == "policy":
                if requirement.id not in available_policies:
                    issues.append(
                        PackValidationIssue(
                            code="UNSATISFIED_REQUIREMENT",
                            message=f"Required policy '{requirement.id}' is not provided by this pack or its includes.",
                            path=f"requires:{requirement.id}",
                        )
                    )
                continue

            if (requirement.surface, requirement.id) not in available_components:
                issues.append(
                    PackValidationIssue(
                        code="UNSATISFIED_REQUIREMENT",
                        message=(
                            f"Required component '{requirement.surface}/{requirement.id}' is not provided "
                            "by this pack or its includes."
                        ),
                        path=f"requires:{requirement.id}",
                    )
                )

        return issues

    def _validate_duplicate_components(self, manifests: tuple[PackManifest, ...]) -> list[PackValidationIssue]:
        owners: dict[tuple[str, str], list[str]] = {}
        issues: list[PackValidationIssue] = []

        for manifest in manifests:
            for component in manifest.components:
                owners.setdefault((component.surface, component.id), []).append(manifest.metadata.name)

        for (surface, component_id), pack_names in owners.items():
            if len(pack_names) <= 1:
                continue
            issues.append(
                PackValidationIssue(
                    code="COMPONENT_COLLISION",
                    message=(
                        f"Component '{surface}/{component_id}' is provided by multiple packs without an explicit "
                        f"override strategy: {', '.join(sorted(pack_names))}."
                    ),
                    severity="warning",
                    path=f"components:{component_id}",
                )
            )

        return issues


def _load_yaml(path: Path) -> Any:
    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        with path.open("r", encoding="utf-8") as handle:
            return yaml.load(handle)
    except ModuleNotFoundError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            msg = "No YAML parser available. Install ruamel.yaml or PyYAML."
            raise GrimoireRegistryError(msg) from exc

        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception as exc:  # pragma: no cover - error message preservation
        msg = f"Failed to parse pack manifest {path}: {exc}"
        raise GrimoireRegistryError(msg) from exc


def _require_mapping(value: Any, field: str, manifest_path: Path) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    msg = f"Field '{field}' must be a mapping in {manifest_path}"
    raise GrimoireRegistryError(msg)


def _require_sequence(value: Any, field: str, manifest_path: Path) -> list[Any]:
    if isinstance(value, list):
        return value
    msg = f"Field '{field}' must be a sequence in {manifest_path}"
    raise GrimoireRegistryError(msg)


def _coerce_sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _require_text(value: Any, field: str, manifest_path: Path) -> str:
    if isinstance(value, str) and value.strip() != "":
        return value.strip()
    msg = f"Field '{field}' must be a non-empty string in {manifest_path}"
    raise GrimoireRegistryError(msg)


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() != "":
        return value.strip()
    return None


def _coerce_optional_path(value: Any) -> Path | None:
    normalized = _optional_text(value)
    return None if normalized is None else Path(normalized)


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [] if normalized == "" else [normalized]
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip() != ""]


def _format_validation_issues(issues: tuple[PackValidationIssue, ...] | list[PackValidationIssue]) -> str:
    formatted = []

    for issue in issues:
        location = "" if issue.path == "" else f"@{issue.path}"
        formatted.append(f"{issue.code}{location}")

    return ", ".join(formatted)


def _evaluate_marketplace_compatibility(
    compatibility: PackCompatibility,
    core_version: str | None,
) -> PackMarketplaceCompatibility:
    compatible: bool | None = None
    reasons: list[str] = []

    if core_version is not None:
        compatible, compatibility_reasons = _is_core_version_compatible(
            core_version,
            compatibility.core.min,
            compatibility.core.max,
        )
        reasons.extend(compatibility_reasons)

    return PackMarketplaceCompatibility(
        core_version=core_version,
        minimum=compatibility.core.min,
        maximum=compatibility.core.max,
        compatible=compatible,
        reasons=tuple(_dedupe_strings(reasons)),
    )


def _build_gate_compatibility_reasons(
    core_version: str | None,
    compatibility: PackMarketplaceCompatibility,
) -> list[str]:
    if core_version is None:
        return ["Core version is required to evaluate install and publish gates."]

    if compatibility.compatible is True:
        return []

    if len(compatibility.reasons) > 0:
        return list(compatibility.reasons)

    return ["Pack compatibility could not be determined for the requested core version."]


def _resolve_distribution_status(metadata: PackMetadata) -> str:
    if metadata.distribution in PACK_DISTRIBUTION_STATUSES:
        return metadata.distribution

    if metadata.status == "internal":
        return "internal"

    if metadata.status == "experimental":
        return "experimental"

    normalized_markers = {
        marker.strip().casefold()
        for marker in [*metadata.provenance, metadata.source or ""]
        if marker.strip() != ""
    }
    if "community" in normalized_markers:
        return "community"
    if "official" in normalized_markers:
        return "official"
    if metadata.owner.casefold().startswith("grimoire"):
        return "official"
    return "community"


def _is_core_version_compatible(core_version: str, minimum: str, maximum: str) -> tuple[bool | None, list[str]]:
    current = _parse_version_tuple(core_version)
    minimum_version = _parse_version_tuple(minimum)
    maximum_version = _parse_version_tuple(maximum)
    reasons: list[str] = []

    if current is None:
        return None, [f"Core version '{core_version}' is not parseable."]

    if minimum_version is None:
        return None, [f"Minimum compatibility version '{minimum}' is not parseable."]

    if _compare_version_tuples(current, minimum_version) < 0:
        reasons.append(f"Core version {core_version} is below minimum supported version {minimum}.")

    upper_bound_match = _matches_maximum_version(current, maximum, maximum_version)
    if upper_bound_match is None:
        return None, [f"Maximum compatibility version '{maximum}' is not parseable."]

    if not upper_bound_match:
        reasons.append(f"Core version {core_version} exceeds supported compatibility ceiling {maximum}.")

    return len(reasons) == 0, reasons


def _parse_version_tuple(version: str) -> tuple[int, ...] | None:
    parts: list[int] = []

    for token in version.split("."):
        normalized = token.strip().lower()
        if normalized == "":
            return None
        if normalized == "x":
            return None
        if not normalized.isdigit():
            return None
        parts.append(int(normalized))

    return tuple(parts)


def _matches_maximum_version(
    current: tuple[int, ...],
    maximum: str,
    parsed_maximum: tuple[int, ...] | None,
) -> bool | None:
    if "x" in maximum.lower().split("."):
        prefix: list[int] = []
        for token in maximum.split("."):
            normalized = token.strip().lower()
            if normalized == "x":
                break
            if not normalized.isdigit():
                return None
            prefix.append(int(normalized))
        return current[: len(prefix)] == tuple(prefix)

    if parsed_maximum is None:
        return None

    return _compare_version_tuples(current, parsed_maximum) <= 0


def _compare_version_tuples(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    max_length = max(len(left), len(right))
    padded_left = (*left, *([0] * (max_length - len(left))))
    padded_right = (*right, *([0] * (max_length - len(right))))

    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def _dry_run_components_from_resolution(
    resolution: PackResolution,
) -> tuple[PackInstallDryRunItem, ...]:
    return tuple(
        sorted(
            (
                PackInstallDryRunItem(
                    pack_name=component.pack_name,
                    id=component.id,
                    kind=component.surface,
                    path=component.path,
                    status=component.status,
                    policy_refs=component.policy_refs,
                    exports=component.exports,
                )
                for component in resolution.components
            ),
            key=_dry_run_item_sort_key,
        )
    )


def _dry_run_overlays_from_resolution(
    resolution: PackResolution,
) -> tuple[PackInstallDryRunItem, ...]:
    return tuple(
        sorted(
            (
                PackInstallDryRunItem(
                    pack_name=overlay.pack_name,
                    id=overlay.id,
                    kind=overlay.kind,
                    path=overlay.path,
                )
                for overlay in resolution.overlays
            ),
            key=_dry_run_item_sort_key,
        )
    )


def _dry_run_policies_from_resolution(
    resolution: PackResolution,
) -> tuple[PackInstallDryRunItem, ...]:
    return tuple(
        sorted(
            (
                PackInstallDryRunItem(
                    pack_name=policy.pack_name,
                    id=policy.id,
                    kind="policy",
                    path=policy.path,
                )
                for policy in resolution.policies
            ),
            key=_dry_run_item_sort_key,
        )
    )


def _dry_run_components_from_lock(lock_document: dict[str, Any] | None) -> tuple[PackInstallDryRunItem, ...]:
    if lock_document is None:
        return ()

    items: list[PackInstallDryRunItem] = []
    for entry in _coerce_sequence(lock_document.get("components")):
        if not isinstance(entry, dict):
            continue
        items.append(
            PackInstallDryRunItem(
                pack_name=_optional_text(entry.get("pack")) or "unknown",
                id=_optional_text(entry.get("id")) or "unknown",
                kind=_optional_text(entry.get("surface")) or "unknown",
                path=_coerce_optional_path(entry.get("path")),
                status=_optional_text(entry.get("status")),
                policy_refs=tuple(_coerce_string_list(entry.get("policyRefs"))),
                exports=tuple(_coerce_string_list(entry.get("exports"))),
            )
        )
    return tuple(sorted(items, key=_dry_run_item_sort_key))


def _dry_run_overlays_from_lock(lock_document: dict[str, Any] | None) -> tuple[PackInstallDryRunItem, ...]:
    if lock_document is None:
        return ()

    items: list[PackInstallDryRunItem] = []
    for entry in _coerce_sequence(lock_document.get("overlays")):
        if not isinstance(entry, dict):
            continue
        items.append(
            PackInstallDryRunItem(
                pack_name=_optional_text(entry.get("pack")) or "unknown",
                id=_optional_text(entry.get("id")) or "unknown",
                kind=_optional_text(entry.get("kind")) or "unknown",
                path=_coerce_optional_path(entry.get("path")),
            )
        )
    return tuple(sorted(items, key=_dry_run_item_sort_key))


def _dry_run_policies_from_lock(lock_document: dict[str, Any] | None) -> tuple[PackInstallDryRunItem, ...]:
    if lock_document is None:
        return ()

    items: list[PackInstallDryRunItem] = []
    for entry in _coerce_sequence(lock_document.get("policies")):
        if not isinstance(entry, dict):
            continue
        items.append(
            PackInstallDryRunItem(
                pack_name=_optional_text(entry.get("pack")) or "unknown",
                id=_optional_text(entry.get("id")) or "unknown",
                kind="policy",
                path=_coerce_optional_path(entry.get("path")),
            )
        )
    return tuple(sorted(items, key=_dry_run_item_sort_key))


def _diff_dry_run_items(
    current: tuple[PackInstallDryRunItem, ...],
    previous: tuple[PackInstallDryRunItem, ...],
) -> PackInstallDryRunDiff:
    current_by_key = {_dry_run_item_identity(item): item for item in current}
    previous_by_key = {_dry_run_item_identity(item): item for item in previous}
    added: list[PackInstallDryRunItem] = []
    removed: list[PackInstallDryRunItem] = []
    changed: list[PackInstallDryRunChange] = []
    unchanged: list[PackInstallDryRunItem] = []

    for key in sorted({*current_by_key.keys(), *previous_by_key.keys()}):
        current_item = current_by_key.get(key)
        previous_item = previous_by_key.get(key)
        if current_item is None and previous_item is not None:
            removed.append(previous_item)
            continue
        if previous_item is None and current_item is not None:
            added.append(current_item)
            continue
        if current_item == previous_item and current_item is not None:
            unchanged.append(current_item)
            continue
        if current_item is not None and previous_item is not None:
            changed.append(PackInstallDryRunChange(previous=previous_item, current=current_item))

    return PackInstallDryRunDiff(
        added=tuple(sorted(added, key=_dry_run_item_sort_key)),
        removed=tuple(sorted(removed, key=_dry_run_item_sort_key)),
        changed=tuple(sorted(changed, key=lambda item: _dry_run_item_sort_key(item.current))),
        unchanged=tuple(sorted(unchanged, key=_dry_run_item_sort_key)),
    )


def _dry_run_item_identity(item: PackInstallDryRunItem) -> tuple[str, str]:
    return item.kind, item.id


def _dry_run_item_sort_key(item: PackInstallDryRunItem) -> tuple[str, str, str]:
    return item.kind, item.id, item.pack_name


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)

    return result


def _collect_issue_messages(
    issues: tuple[PackValidationIssue, ...],
    codes: set[str],
) -> list[str]:
    return [issue.message for issue in issues if issue.code in codes and issue.severity == "error"]


def _fingerprint_json_document(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_lock_document(document: dict[str, Any]) -> str:
    return _fingerprint_json_document(document)