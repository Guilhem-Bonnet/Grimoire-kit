"""Tests for the Grimoire pack registry and validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.registry.packs import PackRegistry


def _write(root: Path, relpath: str, content: str) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_base_pack(root: Path) -> None:
    _write(root, "base/skills/core/SKILL.md", "# Core Skill\n")
    _write(root, "base/policies/verification.yaml", "mode: strict\n")
    _write(root, "base/tests/base-smoke.yaml", "suite: smoke\n")
    _write(
        root,
        "base/pack.yaml",
        """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: base
  version: 0.1.0
  status: stable
  owner: grimoire-core
  description: Base shared pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: core-skill
    surface: skill
    path: skills/core/SKILL.md
    status: stable
    criticality: high
policies:
  - id: verification-minimal
    path: policies/verification.yaml
tests:
  - id: base-smoke
    kind: smoke
    path: tests/base-smoke.yaml
""",
    )


class TestPackRegistryDiscovery:
    def test_discovers_and_validates_composed_pack(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)
        _write(packs_root, "child/workflows/ledger.prompt.md", "# Ledger Workflow\n")
        _write(packs_root, "child/overlays/ledger.patch", "patch: true\n")
        _write(packs_root, "child/tests/child-contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "child/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: child
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Mission ledger overlay pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - base
requires:
  - surface: skill
    id: core-skill
  - surface: policy
    id: verification-minimal
components:
  - id: mission-ledger-workflow
    surface: workflow
    path: workflows/ledger.prompt.md
    status: experimental
    criticality: medium
overlays:
  - id: ledger-overlay
    kind: file_overlay
    path: overlays/ledger.patch
tests:
  - id: child-contract
    kind: contract
    path: tests/child-contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()

        assert [manifest.metadata.name for manifest in manifests] == ["base", "child"]

        report = registry.validate_manifest(next(manifest for manifest in manifests if manifest.metadata.name == "child"), {
            manifest.metadata.name: manifest for manifest in manifests
        })

        assert report.is_valid is True
        assert report.issues == ()


class TestPackRegistryResolution:
    def test_resolves_effective_pack_content_in_deterministic_order(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)
        _write(packs_root, "child/workflows/ledger.prompt.md", "# Ledger Workflow\n")
        _write(packs_root, "child/skills/core/SKILL.md", "# Child Override Skill\n")
        _write(packs_root, "child/overlays/ledger.patch", "patch: true\n")
        _write(packs_root, "child/tests/child-contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "child/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: child
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Mission ledger overlay pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - base
requires:
  - surface: skill
    id: core-skill
  - surface: policy
    id: verification-minimal
components:
  - id: mission-ledger-workflow
    surface: workflow
    path: workflows/ledger.prompt.md
    status: experimental
    criticality: medium
    exports:
      - mission-ledger
  - id: core-skill
    surface: skill
    path: skills/core/SKILL.md
    status: experimental
    criticality: high
overlays:
  - id: ledger-overlay
    kind: file_overlay
    path: overlays/ledger.patch
tests:
  - id: child-contract
    kind: contract
    path: tests/child-contract.yaml
exports:
  - runtime-ledger
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        index = {manifest.metadata.name: manifest for manifest in manifests}
        child_manifest = index["child"]

        resolution = registry.resolve_manifest(child_manifest, index)
        validation = registry.validate_manifest(child_manifest, index)

        assert validation.is_valid is True
        assert any(issue.code == "COMPONENT_COLLISION" for issue in validation.issues)
        assert [pack.name for pack in resolution.packs] == ["child", "base"]
        assert [(component.pack_name, component.surface, component.id) for component in resolution.components] == [
            ("child", "workflow", "mission-ledger-workflow"),
            ("child", "skill", "core-skill"),
        ]
        assert [policy.id for policy in resolution.policies] == ["verification-minimal"]
        assert [(test.pack_name, test.id) for test in resolution.tests] == [
            ("child", "child-contract"),
            ("base", "base-smoke"),
        ]
        assert [(requirement.pack_name, requirement.surface, requirement.id) for requirement in resolution.requirements] == [
            ("child", "skill", "core-skill"),
            ("child", "policy", "verification-minimal"),
        ]
        assert resolution.exports == ("runtime-ledger", "mission-ledger")
        assert resolution.fingerprint == registry.resolve_manifest(child_manifest, index).fingerprint

    def test_writes_deterministic_lock_document(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)
        _write(packs_root, "child/workflows/ledger.prompt.md", "# Ledger Workflow\n")
        _write(packs_root, "child/tests/child-contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "child/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: child
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Mission ledger overlay pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - base
requires:
  - surface: skill
    id: core-skill
components:
  - id: mission-ledger-workflow
    surface: workflow
    path: workflows/ledger.prompt.md
    status: experimental
tests:
  - id: child-contract
    kind: contract
    path: tests/child-contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        index = {manifest.metadata.name: manifest for manifest in manifests}
        child_manifest = index["child"]

        first_path = registry.write_lock(child_manifest, pack_index=index)
        first_content = first_path.read_text(encoding="utf-8")
        second_path = registry.write_lock(child_manifest, pack_index=index)
        second_content = second_path.read_text(encoding="utf-8")
        payload = json.loads(first_content)

        assert first_path == second_path
        assert first_path.name == "pack.lock.json"
        assert first_content == second_content
        assert payload["pack"]["name"] == "child"
        assert payload["pack"]["manifestPath"] == "child/pack.yaml"
        assert payload["packs"] == [
            {
                "manifestPath": "child/pack.yaml",
                "name": "child",
                "root": "child",
                "status": "experimental",
                "version": "0.1.0",
            },
            {
                "manifestPath": "base/pack.yaml",
                "name": "base",
                "root": "base",
                "status": "stable",
                "version": "0.1.0",
            },
        ]
        assert payload["components"][0]["path"] == "child/workflows/ledger.prompt.md"
        assert payload["fingerprint"] == registry.resolve_manifest(child_manifest, index).fingerprint


class TestPackRegistryMarketplace:
    def test_builds_verified_marketplace_catalog_with_publishable_entries(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)
        _write(packs_root, "child/workflows/ledger.prompt.md", "# Ledger Workflow\n")
        _write(packs_root, "child/tests/child-contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "child/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: child
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Mission ledger overlay pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - base
components:
  - id: mission-ledger-workflow
    surface: workflow
    path: workflows/ledger.prompt.md
    status: experimental
tests:
  - id: child-contract
    kind: contract
    path: tests/child-contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        index = {manifest.metadata.name: manifest for manifest in manifests}
        for manifest in manifests:
            registry.write_lock(manifest, pack_index=index)

        catalog = registry.build_verified_marketplace(core_version="0.1.0")

        assert catalog.summary.to_document() == {
            "entryCount": 2,
            "verifiedCount": 2,
            "installableCount": 2,
            "publishableCount": 2,
            "incompatibleCount": 0,
            "lockMismatchCount": 0,
        }
        child_entry = next(entry for entry in catalog.entries if entry.name == "child")
        assert child_entry.compatibility.compatible is True
        assert child_entry.distribution == "experimental"
        assert child_entry.publication.lock_exists is True
        assert child_entry.publication.lock_matches_resolution is True
        assert child_entry.publishable is True
        assert child_entry.surfaces == ("workflow", "skill")
        assert child_entry.exports == ()

    def test_flags_incompatible_or_unlocked_pack_in_marketplace_catalog(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(packs_root, "future/skills/core/SKILL.md", "# Future Skill\n")
        _write(packs_root, "future/tests/contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "future/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: future-pack
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Pack requiring a newer core.
compatibility:
  core:
    min: 1.0.0
    max: 1.x
components:
  - id: future-skill
    surface: skill
    path: skills/core/SKILL.md
    status: experimental
tests:
  - id: future-contract
    kind: contract
    path: tests/contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        catalog = registry.build_verified_marketplace(core_version="0.1.0")

        assert catalog.summary.to_document() == {
            "entryCount": 1,
            "verifiedCount": 1,
            "installableCount": 0,
            "publishableCount": 0,
            "incompatibleCount": 1,
            "lockMismatchCount": 0,
        }
        entry = catalog.entries[0]
        assert entry.name == "future-pack"
        assert entry.verified is True
        assert entry.distribution == "experimental"
        assert entry.installable is False
        assert entry.publishable is False
        assert entry.publication.lock_exists is False
        assert entry.compatibility.compatible is False
        assert "Core version 0.1.0 is below minimum supported version 1.0.0." in entry.gate_reasons
        assert "Lock file pack.lock.json is missing." in entry.gate_reasons

    def test_marks_compatible_pack_without_lock_as_non_installable(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)

        registry = PackRegistry(packs_root)
        catalog = registry.build_verified_marketplace(core_version="0.1.0")

        entry = catalog.entries[0]

        assert entry.name == "base"
        assert entry.compatibility.compatible is True
        assert entry.distribution == "official"
        assert entry.installable is False
        assert entry.publishable is False
        assert entry.install_gate.allowed is False
        assert entry.publish_gate.allowed is False
        assert entry.missing_tests == ()
        assert entry.missing_policies == ()
        assert "Lock file pack.lock.json is missing." in entry.install_gate.reasons

    def test_writes_verified_marketplace_catalog_deterministically(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        index = {manifest.metadata.name: manifest for manifest in manifests}
        registry.write_lock(manifests[0], pack_index=index)

        first_path = registry.write_verified_marketplace(core_version="0.1.0")
        first_content = first_path.read_text(encoding="utf-8")
        second_path = registry.write_verified_marketplace(core_version="0.1.0")
        second_content = second_path.read_text(encoding="utf-8")
        payload = json.loads(first_content)

        assert first_path == second_path
        assert first_path.name == "verified-marketplace.json"
        assert first_content == second_content
        assert payload["kind"] == "VerifiedPackMarketplace"
        assert payload["summary"] == {
            "entryCount": 1,
            "verifiedCount": 1,
            "installableCount": 1,
            "publishableCount": 1,
            "incompatibleCount": 0,
            "lockMismatchCount": 0,
        }
        assert payload["entries"][0]["publication"]["lockExists"] is True
        assert payload["entries"][0]["publication"]["lockMatchesResolution"] is True


class TestPackRegistryValidation:
    def test_detects_missing_component_path(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(
            packs_root,
            "broken/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: broken
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Broken component path.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: missing-skill
    surface: skill
    path: skills/missing/SKILL.md
    status: experimental
""",
        )

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "broken/pack.yaml")
        report = registry.validate_manifest(manifest, {manifest.metadata.name: manifest})

        assert report.is_valid is False
        assert any(issue.code == "MISSING_COMPONENT_PATH" for issue in report.issues)

    def test_detects_include_cycle(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(packs_root, "alpha/docs/readme.md", "alpha\n")
        _write(packs_root, "beta/docs/readme.md", "beta\n")
        _write(
            packs_root,
            "alpha/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: alpha
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Alpha pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - beta
components:
  - id: alpha-docs
    surface: docs
    path: docs/readme.md
    status: experimental
""",
        )
        _write(
            packs_root,
            "beta/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: beta
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Beta pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
includes:
  - alpha
components:
  - id: beta-docs
    surface: docs
    path: docs/readme.md
    status: experimental
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        reports = registry.validate_all()

        assert len(manifests) == 2
        assert any(issue.code == "INCLUDE_CYCLE" for report in reports for issue in report.issues)

    def test_requires_tests_for_stable_pack(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(packs_root, "stable/skills/core/SKILL.md", "# Stable\n")
        _write(
            packs_root,
            "stable/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: stable-pack
  version: 0.1.0
  status: stable
  owner: grimoire-core
  description: Stable pack without tests.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: stable-skill
    surface: skill
    path: skills/core/SKILL.md
    status: stable
""",
        )

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "stable/pack.yaml")
        report = registry.validate_manifest(manifest, {manifest.metadata.name: manifest})

        assert report.is_valid is False
        assert any(issue.code == "STABLE_PACK_REQUIRES_TESTS" for issue in report.issues)

    def test_detects_unsatisfied_requirement(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(packs_root, "consumer/workflows/ledger.prompt.md", "# Ledger Workflow\n")
        _write(packs_root, "consumer/tests/contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "consumer/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: consumer
  version: 0.1.0
  status: experimental
  owner: grimoire-runtime
  description: Consumer pack with missing requirement.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
requires:
  - surface: skill
    id: missing-skill
components:
  - id: ledger-workflow
    surface: workflow
    path: workflows/ledger.prompt.md
    status: experimental
tests:
  - id: contract
    kind: contract
    path: tests/contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "consumer/pack.yaml")
        report = registry.validate_manifest(manifest, {manifest.metadata.name: manifest})

        assert report.is_valid is False
        assert any(issue.code == "UNSATISFIED_REQUIREMENT" for issue in report.issues)


class TestPackRegistryDistribution:
    def test_evaluates_distribution_report_and_enforces_install_gate(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "base/pack.yaml")
        report = registry.evaluate_distribution(manifest, {manifest.metadata.name: manifest}, core_version="0.1.0")

        assert report.status == "stable"
        assert report.distribution == "official"
        assert report.verified is True
        assert report.compatibility.compatible is True
        assert report.install_gate.allowed is False
        assert report.publish_gate.allowed is False
        assert report.publication.lock_exists is False
        assert report.missing_tests == ()
        assert report.missing_policies == ()
        assert "Lock file pack.lock.json is missing." in report.install_gate.reasons

        with pytest.raises(Exception, match="not installable"):
            registry.ensure_installable(manifest, {manifest.metadata.name: manifest}, core_version="0.1.0")

    def test_enforces_publish_gate_on_lock_fingerprint_drift(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "base/pack.yaml")
        index = {manifest.metadata.name: manifest}
        lock_path = registry.write_lock(manifest, pack_index=index)
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        payload["fingerprint"] = "deadbeef"
        lock_path.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")

        report = registry.evaluate_distribution(manifest, index, core_version="0.1.0")

        assert report.publication.lock_exists is True
        assert report.publication.lock_matches_resolution is False
        assert report.install_gate.allowed is False
        assert report.publish_gate.allowed is False
        assert "Lock fingerprint does not match resolved pack content." in report.install_gate.reasons

        with pytest.raises(Exception, match="not publishable"):
            registry.ensure_publishable(manifest, index, core_version="0.1.0")

    def test_reports_missing_tests_and_policies_for_publication(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write(packs_root, "broken/skills/core/SKILL.md", "# Broken\n")
        _write(
            packs_root,
            "broken/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: broken-publish
  version: 0.1.0
  status: stable
  owner: grimoire-runtime
  description: Broken publish report.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: broken-skill
    surface: skill
    path: skills/core/SKILL.md
    status: stable
policies:
  - id: missing-policy
    path: policies/missing.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifest = registry.load_manifest(packs_root / "broken/pack.yaml")
        report = registry.evaluate_distribution(manifest, {manifest.metadata.name: manifest}, core_version="0.1.0")

        assert report.verified is False
        assert report.install_gate.allowed is False
        assert report.publish_gate.allowed is False
        assert "Stable packs must declare at least one test." in report.missing_tests
        assert "Policy path does not exist: policies/missing.yaml" in report.missing_policies

    def test_preview_installation_reports_materialization_diff_without_writing(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)

        registry = PackRegistry(packs_root)
        original_manifest = registry.load_manifest(packs_root / "base/pack.yaml")
        registry.write_lock(original_manifest, pack_index={original_manifest.metadata.name: original_manifest})

        _write(packs_root, "base/skills/core/SKILL-v2.md", "# Core Skill v2\n")
        _write(packs_root, "base/overlays/runtime.patch", "patch: runtime\n")
        _write(
            packs_root,
            "base/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: base
  version: 0.1.1
  status: stable
  owner: grimoire-core
  description: Base shared pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: core-skill
    surface: skill
    path: skills/core/SKILL-v2.md
    status: stable
    criticality: high
overlays:
  - id: runtime-overlay
    kind: file_overlay
    path: overlays/runtime.patch
tests:
  - id: base-smoke
    kind: smoke
    path: tests/base-smoke.yaml
""",
        )

        updated_manifest = registry.load_manifest(packs_root / "base/pack.yaml")
        preview = registry.preview_installation(
            updated_manifest,
            {updated_manifest.metadata.name: updated_manifest},
            core_version="0.1.0",
        )

        assert preview.distribution == "official"
        assert preview.baseline_available is True
        assert preview.report.install_gate.allowed is False
        assert preview.components.changed[0].previous.path == Path("base/skills/core/SKILL.md")
        assert preview.components.changed[0].current.path == Path("base/skills/core/SKILL-v2.md")
        assert preview.overlays.added[0].id == "runtime-overlay"
        assert preview.policies.removed[0].id == "verification-minimal"

    def test_builds_operator_view_with_distribution_and_policy_presence(self, tmp_path: Path) -> None:
        packs_root = tmp_path / "packs"
        _write_base_pack(packs_root)
        _write(packs_root, "community/skills/ops/SKILL.md", "# Community Ops\n")
        _write(packs_root, "community/tests/community-contract.yaml", "suite: contract\n")
        _write(
            packs_root,
            "community/pack.yaml",
            """\
apiVersion: grimoire/v1alpha1
kind: Pack
metadata:
  name: community-pack
  version: 0.2.0
  status: stable
  owner: external-lab
  description: Community maintained pack.
compatibility:
  core:
    min: 0.1.0
    max: 0.x
components:
  - id: community-ops
    surface: skill
    path: skills/ops/SKILL.md
    status: stable
policies:
  - id: community-policy
    path: policies/missing.yaml
tests:
  - id: community-contract
    kind: contract
    path: tests/community-contract.yaml
""",
        )

        registry = PackRegistry(packs_root)
        manifests = registry.discover()
        index = {manifest.metadata.name: manifest for manifest in manifests}
        registry.write_lock(index["base"], pack_index=index)

        view = registry.build_operator_view(core_version="0.1.0")

        assert view.summary.to_document() == {
            "packCount": 2,
            "installableCount": 1,
            "publishableCount": 1,
            "blockedInstallCount": 1,
            "blockedPublishCount": 1,
            "policyCount": 2,
            "missingPolicyCount": 1,
            "officialCount": 1,
            "communityCount": 1,
            "experimentalCount": 0,
            "internalCount": 0,
        }
        community_entry = next(entry for entry in view.packs if entry.name == "community-pack")
        assert community_entry.distribution == "community"
        assert community_entry.publishable is False
        assert "Policy path does not exist: policies/missing.yaml" in community_entry.missing_policies
        community_policy = next(entry for entry in view.policies if entry.policy_id == "community-policy")
        assert community_policy.present is False