"""Smoke tests for the legacy framework/tools/agent-forge.py entrypoint."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "agent-forge.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_forge_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_project(project_root: Path) -> tuple[Path, Path, Path, Path]:
    shared_context = project_root / "_grimoire" / "_memory" / "shared-context.md"
    trace_file = project_root / "_grimoire-output" / "Grimoire_TRACE.md"
    agents_dir = project_root / "_grimoire" / "_config" / "custom" / "agents"
    proposals_dir = project_root / "_grimoire-output" / "forge-proposals"

    shared_context.parent.mkdir(parents=True, exist_ok=True)
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
    proposals_dir.mkdir(parents=True, exist_ok=True)

    (project_root / "project-context.yaml").write_text(
        "project_name: TestProject\nuser: Guilhem\n",
        encoding="utf-8",
    )
    return shared_context, trace_file, agents_dir, proposals_dir


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.scan_gaps_from_shared_context)
    assert callable(legacy_module.scan_gaps_from_trace)
    assert callable(legacy_module.read_active_dna)
    assert callable(legacy_module.install_proposal)
    assert callable(legacy_module.main)


def test_read_active_dna_limits_entries(tmp_path: Path) -> None:
    legacy_module = _load_module()
    dna_dir = tmp_path / "archetypes" / "ops"
    dna_dir.mkdir(parents=True)
    content = "acceptance_criteria:\n"
    for index in range(20):
        content += f"  - description: 'AC number {index:02d} for retention policy'\n"
    (dna_dir / "archetype.dna.yaml").write_text(content, encoding="utf-8")

    items = legacy_module.read_active_dna(tmp_path / "archetypes")
    assert len(items) == 10
    assert items[0].startswith("AC number")


def test_domain_taxonomy_profiles_keep_required_keys() -> None:
    legacy_module = _load_module()
    required = {"icon", "tag_prefix", "tools", "keywords", "role", "domain_word", "prompt_patterns", "cc_check"}
    assert "networking" in legacy_module.DOMAIN_TAXONOMY
    assert "storage" in legacy_module.DOMAIN_TAXONOMY
    for profile in legacy_module.DOMAIN_TAXONOMY.values():
        assert required <= set(profile)


def test_cli_list_reports_empty_directory(tmp_path: Path) -> None:
    _shared_context, _trace_file, _agents_dir, proposals_dir = _make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--list", "--out-dir", str(proposals_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Aucun proposal" in result.stdout


def test_cli_from_gap_creates_proposal(tmp_path: Path) -> None:
    shared_context, _trace_file, agents_dir, proposals_dir = _make_project(tmp_path)
    shared_context.write_text(
        "# Shared Context\n"
        "## Requêtes inter-agents\n"
        "- [ ] [forge→?] Besoin d'un agent pour gérer les backups\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--from-gap",
            "--shared-context",
            str(shared_context),
            "--project-context",
            str(tmp_path / "project-context.yaml"),
            "--agents-dir",
            str(agents_dir),
            "--out-dir",
            str(proposals_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    proposals = list(proposals_dir.glob("*.proposed.md"))
    assert len(proposals) == 1
    content = proposals[0].read_text(encoding="utf-8")
    assert "TestProject" in content
    assert "forge" in result.stdout


def test_cli_from_trace_creates_proposal(tmp_path: Path) -> None:
    _shared_context, trace_file, agents_dir, proposals_dir = _make_project(tmp_path)
    trace_file.write_text(
        "## 2026-01-01 | dev | story-1\n"
        "[FAILURE] db-migration failed\n"
        "[FAILURE] db-migration timeout\n"
        "[FAILURE] db-migration lock error\n"
        "[FAILURE] db-migration schema mismatch\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--from-trace",
            "--trace",
            str(trace_file),
            "--project-context",
            str(tmp_path / "project-context.yaml"),
            "--agents-dir",
            str(agents_dir),
            "--out-dir",
            str(proposals_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    proposals = list(proposals_dir.glob("*.proposed.md"))
    assert len(proposals) == 1
    assert "gap(s) détecté(s)" in result.stdout


def test_install_proposal_moves_file_and_updates_manifest(tmp_path: Path) -> None:
    legacy_module = _load_module()
    _shared_context, _trace_file, agents_dir, proposals_dir = _make_project(tmp_path)
    manifest = tmp_path / "_grimoire" / "_config" / "agent-manifest.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("name,module,path,description\n", encoding="utf-8")
    proposal_file = proposals_dir / "agent-db-backup.proposed.md"
    proposal_file.write_text("# Proposal\n", encoding="utf-8")

    legacy_module.install_proposal("db-backup", proposals_dir, agents_dir, manifest)

    installed = agents_dir / "db-backup.md"
    assert installed.exists()
    assert not proposal_file.exists()
    assert "db-backup,custom,db-backup.md" in manifest.read_text(encoding="utf-8")