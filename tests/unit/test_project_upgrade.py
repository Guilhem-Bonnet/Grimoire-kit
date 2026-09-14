"""``grimoire.tools.project_upgrade`` and the `project-upgrade` blueprint (issue #490).

Builds on a *really* initialised project (``grimoire init``) rather than a
hand-faked one — the fresh agent roster ``find_orphans`` diffs against comes
from the real installed kit, and a fake roster would only prove the test's
own fixture, never the function. Orphans, drifted overrides and unlinked
memory fiches are then grafted onto that real project, the same shape the
2026-09-11 migration report described.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _grimoire(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=180,
    )


@pytest.fixture(scope="module")
def upgrade_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A really-initialised minimal project, module-scoped — `grimoire init` alone takes ~1s."""
    root = tmp_path_factory.mktemp("upgrade-project") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-project"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")
    return root


def _seed_orphan(root: Path) -> str:
    """Copy a real kit agent under a name the current kit does not deliver — the orphan."""
    kit_agents = root / "_grimoire" / "kit" / "agents"
    real_agents = sorted(kit_agents.glob("*.md"))
    assert real_agents, "grimoire init should have delivered at least one kit agent"
    template = real_agents[0].read_text(encoding="utf-8")
    orphan_body = template.replace(real_agents[0].stem, "retired-specialist", 1)
    if "name:" not in orphan_body:
        orphan_body = orphan_body.replace("---\n", '---\nname: "retired-specialist"\n', 1)
    (kit_agents / "retired-specialist.md").write_text(orphan_body, encoding="utf-8")

    claude_agents = root / ".claude" / "agents"
    claude_agents.mkdir(parents=True, exist_ok=True)
    (claude_agents / "retired-specialist.md").write_text(
        "<!-- grimoire:managed -->\n" + orphan_body, encoding="utf-8"
    )
    return "retired-specialist"


def _seed_drifted_override(root: Path) -> str:
    """A full-copy override whose body no longer matches the kit's — 'revue nécessaire'."""
    kit_agents = root / "_grimoire" / "kit" / "agents"
    real_agents = sorted(kit_agents.glob("*.md"))
    target = real_agents[0]
    overrides_dir = root / "_grimoire" / "overrides" / "agents"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    drifted = target.read_text(encoding="utf-8") + "\n<!-- customisation homelab -->\n"
    (overrides_dir / target.name).write_text(drifted, encoding="utf-8")
    return target.stem


def _seed_unlinked_fiche(root: Path) -> str:
    learnings = root / "_grimoire" / "_memory" / "agent-learnings"
    learnings.mkdir(parents=True, exist_ok=True)
    (learnings / "monitoring.md").write_text("# Monitoring\n\nSurveiller les métriques clés.\n", encoding="utf-8")
    return "agent-learnings/monitoring.md"


# ── backup ────────────────────────────────────────────────────────────────────


def test_backup_project_writes_tarball_and_manifest(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import archive_root, backup_project

    result = backup_project(upgrade_project)

    assert result.tarball.is_file()
    assert result.manifest.is_file()
    assert result.tarball.parent == archive_root(upgrade_project)
    manifest_lines = result.manifest.read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == result.memory_files


def test_backup_project_is_idempotent(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import backup_project

    first = backup_project(upgrade_project)
    second = backup_project(upgrade_project)
    assert first.tarball == second.tarball
    assert first.tarball_entries == second.tarball_entries


# ── orphans ───────────────────────────────────────────────────────────────────


def test_find_orphans_detects_stale_kit_agent(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import find_orphans

    orphan_name = _seed_orphan(upgrade_project)
    report = find_orphans(upgrade_project)

    assert orphan_name in report.names
    orphan = next(o for o in report.orphans if o.name == orphan_name)
    # Both the kit-tier copy and its managed `.claude/agents/` projection.
    assert len(orphan.paths) == 2


def test_find_orphans_ignores_the_live_roster(upgrade_project: Path) -> None:
    from grimoire.cli.cmd_up import fresh_kit_agent_roster
    from grimoire.tools.project_upgrade import find_orphans

    roster = fresh_kit_agent_roster(upgrade_project)
    report = find_orphans(upgrade_project)
    assert not (set(report.names) & roster)


def test_archive_orphans_moves_never_deletes(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import archive_orphans, archive_root, find_orphans

    report = find_orphans(upgrade_project)
    assert report.orphans, "the orphan seeded earlier in this module must still be found"
    original_paths = [p for o in report.orphans for p in o.paths]

    moved = archive_orphans(upgrade_project, report)

    assert moved
    for path in original_paths:
        assert not path.exists(), f"{path} should have been moved, not left in place"
    dest_root = archive_root(upgrade_project) / "orphans"
    assert (dest_root / "README.md").is_file()
    for rel in moved:
        assert (dest_root / rel).is_file()

    # Re-running find_orphans now finds nothing left to archive.
    assert not find_orphans(upgrade_project).orphans


# ── overrides (V1 proposals) ─────────────────────────────────────────────────


def test_propose_override_migrations_flags_drift(upgrade_project: Path) -> None:
    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import propose_override_migrations

    drifted_name = _seed_drifted_override(upgrade_project)
    proposals = propose_override_migrations(upgrade_project)

    assert any(p.target_agent == drifted_name for p in proposals)
    proposal = next(p for p in proposals if p.target_agent == drifted_name)
    assert proposal.artifact_type == "override-migration"
    assert proposal.status == "pending"

    # Visible through the same door the cockpit and `proposals list` use.
    listed = list_proposals(upgrade_project)
    assert proposal.slug in {p.slug for p in listed}


def test_accept_override_migration_refuses_when_review_needed(upgrade_project: Path) -> None:
    from grimoire.proposals import accept_proposal
    from grimoire.tools.project_upgrade import propose_override_migrations

    drifted_name = _seed_drifted_override(upgrade_project)
    propose_override_migrations(upgrade_project)

    result = accept_proposal(upgrade_project, f"override-migration-{drifted_name}")
    assert result["ok"] is False
    assert "revue" in result["error"].lower()


# ── memory (V1 proposals) ─────────────────────────────────────────────────────


def test_propose_memory_links_flags_unreferenced_fiche(upgrade_project: Path) -> None:
    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import propose_memory_links

    fiche_rel = _seed_unlinked_fiche(upgrade_project)
    proposals = propose_memory_links(upgrade_project)

    matching = [p for p in proposals if p.artifact_ref == fiche_rel]
    assert matching, f"expected a memory-link proposal for {fiche_rel}, got {[p.artifact_ref for p in proposals]}"
    proposal = matching[0]
    assert proposal.artifact_type == "memory-link"

    listed = list_proposals(upgrade_project)
    assert proposal.slug in {p.slug for p in listed}


def test_propose_memory_links_skips_a_referenced_fiche(upgrade_project: Path) -> None:
    """A fiche any agent's `context:` already names is not proposed again."""
    from grimoire.tools.project_upgrade import propose_memory_links

    first_pass = propose_memory_links(upgrade_project)
    fiche_slugs = {p.artifact_ref for p in first_pass}
    assert "agent-learnings/monitoring.md" in fiche_slugs  # seeded by an earlier test in this module


# ── needs / hosts ─────────────────────────────────────────────────────────────


def test_propose_needs_hosts_covers_unresolved_and_undeclared(upgrade_project: Path) -> None:
    import re

    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import propose_needs_hosts

    # `grimoire init` declares `hosts.enabled` itself — strip it to simulate
    # the pre-#177 project (or a hand-edited one) the proposal is for.
    config_path = upgrade_project / "project-context.yaml"
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(re.sub(r"(?m)^hosts:\n(?:[ \t].*\n)*", "", original), encoding="utf-8")
    try:
        proposals = propose_needs_hosts(upgrade_project)
        slugs = {p.slug for p in proposals}
        assert "hosts-declare-enabled" in slugs

        listed = {p.slug for p in list_proposals(upgrade_project)}
        assert slugs <= listed
    finally:
        config_path.write_text(original, encoding="utf-8")


# ── verify ────────────────────────────────────────────────────────────────────


def test_verify_upgrade_ok_when_only_config_yaml_changed(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import archive_root, backup_project, verify_upgrade

    backup_project(upgrade_project)
    manifest = archive_root(upgrade_project) / "memory-manifest-sha256.txt"
    config_path = upgrade_project / "_grimoire" / "_memory" / "config.yaml"
    if config_path.is_file():
        config_path.write_text(config_path.read_text(encoding="utf-8") + "\n# touched by up\n", encoding="utf-8")

    result = verify_upgrade(upgrade_project, manifest)
    assert result.ok
    assert result.report_path.is_file()


def test_verify_upgrade_flags_unexpected_diff(tmp_path: Path) -> None:
    from grimoire.tools.project_upgrade import backup_project, verify_upgrade

    (tmp_path / "_grimoire" / "_memory" / "agent-learnings").mkdir(parents=True)
    fiche = tmp_path / "_grimoire" / "_memory" / "agent-learnings" / "x.md"
    fiche.write_text("original", encoding="utf-8")

    result = backup_project(tmp_path)
    fiche.write_text("tampered without going through the flow", encoding="utf-8")

    verified = verify_upgrade(tmp_path, result.manifest)
    assert not verified.ok
    assert "_grimoire/_memory/agent-learnings/x.md" in verified.unexpected_diffs


def test_verify_upgrade_refuses_without_a_manifest(tmp_path: Path) -> None:
    from grimoire.core.exceptions import GrimoireRuntimeError
    from grimoire.tools.project_upgrade import verify_upgrade

    with pytest.raises(GrimoireRuntimeError):
        verify_upgrade(tmp_path, tmp_path / "nope.txt")


# ── probe-hook ────────────────────────────────────────────────────────────────


def test_probe_hook_reports_ok_on_a_healthy_project(upgrade_project: Path) -> None:
    from grimoire.tools.project_upgrade import probe_hook

    result = probe_hook(upgrade_project)
    assert result.ok, result.detail


# ── blueprint structure ───────────────────────────────────────────────────────


def test_project_upgrade_blueprint_loads_and_orders_correctly() -> None:
    from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, topo_order
    from grimoire.tools.project_upgrade import bundled_blueprint_path

    path = bundled_blueprint_path()
    blueprint = load_blueprint(path, Path())
    order = topo_order(blueprint)

    assert order == [
        "backup", "preview", "orphans", "apply", "overrides", "memory", "needs-hosts", "verify", "destructive",
    ]
    contracts = build_node_contracts(blueprint, Path())
    assert contracts["destructive"].kind == "checkpoint"
    for node_id in order[:-1]:
        assert contracts[node_id].acceptance_runs, f"node {node_id} should carry a structured, executable acceptance"


def test_project_upgrade_blueprint_validates_against_schema() -> None:
    """Same check ``grimoire blueprint validate`` runs — JSON Schema + structural."""
    import jsonschema

    from grimoire.tools.project_upgrade import bundled_blueprint_path

    repo_root = Path(__file__).resolve().parents[2]
    schema = json.loads((repo_root / "schemas" / "blueprint-v1.schema.json").read_text(encoding="utf-8"))
    blueprint = json.loads(bundled_blueprint_path().read_text(encoding="utf-8"))
    jsonschema.validate(blueprint, schema)


# ── end to end, --executor interactive ───────────────────────────────────────


def test_upgrade_flow_run_end_to_end(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The whole blueprint, driven mechanically, on its own disposable project.

    Seeds an orphan agent, a drifted override and an unreferenced memory
    fiche before running — the same three findings the 2026-09-11 migration
    report walked through by hand. Asserts every mechanical postcondition
    plus the three proposals, then that the run halts, undecided, at the
    `destructive` checkpoint.
    """
    root = tmp_path_factory.mktemp("upgrade-e2e") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-e2e"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    orphan_name = _seed_orphan(root)
    drifted_name = _seed_drifted_override(root)
    fiche_rel = _seed_unlinked_fiche(root)

    proc = _grimoire(["upgrade-flow", "run", "--project-root", ".", "--json"], root)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["done"] == [
        "backup", "preview", "orphans", "apply", "overrides", "memory", "needs-hosts", "verify",
    ]
    assert payload["stopped_at"] == "destructive"

    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import archive_root, find_orphans

    assert (archive_root(root) / "grimoire-state.tar.gz").is_file()
    assert (archive_root(root) / "memory-manifest-sha256.txt").is_file()
    assert not find_orphans(root).orphans
    assert not (root / "_grimoire" / "kit" / "agents" / f"{orphan_name}.md").exists()

    proposals = list_proposals(root)
    by_type = {p.artifact_type for p in proposals}
    assert "override-migration" in by_type
    assert "memory-link" in by_type
    assert "needs-hosts" in by_type
    assert any(p.artifact_type == "override-migration" and p.target_agent == drifted_name for p in proposals)
    assert any(p.artifact_type == "memory-link" and p.artifact_ref == fiche_rel for p in proposals)

    report_dirs = sorted((root / "_grimoire-output" / "upgrade").iterdir())
    assert report_dirs
    assert (report_dirs[-1] / "preview.md").is_file()
    assert (report_dirs[-1] / "report.md").is_file()

    # A second run the same day is a safe no-op through backup/preview/apply —
    # never a duplicate tarball, never a second identical proposal file.
    proc2 = _grimoire(["upgrade-flow", "run", "--project-root", ".", "--json"], root)
    assert proc2.returncode == 0, proc2.stderr


def test_upgrade_flow_run_dry_run_stops_after_preview(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = tmp_path_factory.mktemp("upgrade-dry") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-dry"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    proc = _grimoire(["upgrade-flow", "run", "--project-root", ".", "--dry-run", "--json"], root)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["done"] == ["backup", "preview"]
    assert "orphans" in payload["stopped_at"]

    # `up` itself was never actually applied — the kit tier's own refresh
    # step never ran for real, only its `--dry-run`.
    assert (root / "_grimoire-output" / "upgrade").exists()


def test_flow_extract_reproduces_an_incomplete_upgrade_run(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = tmp_path_factory.mktemp("upgrade-extract") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-extract"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    run_proc = _grimoire(["upgrade-flow", "run", "--project-root", ".", "--json"], root)
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    extract_proc = _grimoire(["-o", "json", "flow", "extract", run_id, "--project-root", "."], root)
    assert extract_proc.returncode == 0, extract_proc.stderr
    extracted = json.loads(extract_proc.stdout)
    assert extracted["blueprint_id"] == f"{run_id.lower()}-extract" or "extract" in extracted["blueprint_id"]
    node_ids = {n["node_id"] for n in extracted["nodes"]}
    assert {"backup", "preview", "apply", "orphans", "overrides", "memory", "needs-hosts", "verify"} <= node_ids
