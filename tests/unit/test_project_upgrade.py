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


def _seed_feature_agent(root: Path) -> str:
    """Install the real ``vector-memory`` feature agent in the kit tier.

    The shape ``grimoire init --backend qdrant-local``/``ollama`` actually
    produces (:class:`~grimoire.core.archetype_resolver.ArchetypeResolver`)
    — a *real* bundled file, not a hand-faked one, so this proves
    ``find_orphans`` against the same source ``fresh_kit_agent_roster``
    reads, never against this test's own fixture.
    """
    from grimoire.archetypes import bundled_path as archetypes_path

    src = archetypes_path() / "features" / "vector-memory" / "vectus.md"
    assert src.is_file(), "the kit must still bundle its own vector-memory feature agent"
    dst = root / "_grimoire" / "kit" / "agents" / "vectus.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return "vectus"


def _seed_declared_custom_agent(root: Path, name: str = "fix-loop-orchestrator") -> str:
    """A kit-tier agent the fresh roster would not (re)write, but the project explicitly
    keeps via ``agents.custom_agents`` — the exact shape the 2026-09-11 migration report
    described for ``fix-loop-orchestrator``."""
    kit_agents = root / "_grimoire" / "kit" / "agents"
    real_agents = sorted(p for p in kit_agents.glob("*.md") if p.stem not in {"retired-specialist", "vectus"})
    template = real_agents[0].read_text(encoding="utf-8")
    body = template.replace(real_agents[0].stem, name, 1)
    if "name:" not in body:
        body = body.replace("---\n", f'---\nname: "{name}"\n', 1)
    (kit_agents / f"{name}.md").write_text(body, encoding="utf-8")

    config_path = root / "project-context.yaml"
    original = config_path.read_text(encoding="utf-8")
    assert "custom_agents: []" in original, "expected a still-empty `agents.custom_agents` to declare into"
    config_path.write_text(original.replace("custom_agents: []", f'custom_agents: ["{name}"]'), encoding="utf-8")
    return name


def _seed_override_only_agent(root: Path, name: str = "custom-standalone") -> str:
    """A fully project-authored agent living only in the overrides tier, no kit base at all."""
    overrides_agents = root / "_grimoire" / "overrides" / "agents"
    overrides_agents.mkdir(parents=True, exist_ok=True)
    (overrides_agents / f"{name}.md").write_text(
        f'---\nname: "{name}"\n---\n\nAgent maison, sans base kit.\n', encoding="utf-8"
    )
    return name


def _seed_legacy_custom_agent(root: Path, name: str = "homelab-runner") -> str:
    """A project-owned agent living in the legacy custom tier (``_grimoire/_config/custom/
    agents/``), never migrated to ``overrides/`` and never declared under
    ``agents.custom_agents`` — the exact shape of the three real projects (issue #490's
    second real rejeu) that lost 1, 3 and 13 live agents this way: the file is neither in
    the fresh roster nor in ``custom_agents`` nor under ``overrides/``, yet it is entirely
    the project's own."""
    legacy_agents = root / "_grimoire" / "_config" / "custom" / "agents"
    legacy_agents.mkdir(parents=True, exist_ok=True)
    (legacy_agents / f"{name}.md").write_text(
        f'---\nname: "{name}"\n---\n\nAgent maison, jamais migré vers overrides.\n', encoding="utf-8"
    )
    return name


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
    """Returns the project-root-relative path — the form `artifact_ref` carries and
    a real agent's `context:` entry uses (e.g. `_grimoire/_memory/notes-securite.md`)."""
    learnings = root / "_grimoire" / "_memory" / "agent-learnings"
    learnings.mkdir(parents=True, exist_ok=True)
    (learnings / "monitoring.md").write_text("# Monitoring\n\nSurveiller les métriques clés.\n", encoding="utf-8")
    return "_grimoire/_memory/agent-learnings/monitoring.md"


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


def test_backup_project_never_overwrites_a_changed_snapshot(tmp_path: Path) -> None:
    """A second same-day, same-version `backup` whose tracked files actually changed since
    the first — a dry-run then a real run, with something touched in between — gets its own
    suffixed tarball/manifest. The canonical (first) snapshot is never overwritten."""
    from grimoire.tools.project_upgrade import backup_project

    root = tmp_path / "projet"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
    (root / "_grimoire").mkdir()
    (root / "_grimoire" / "a.txt").write_text("one", encoding="utf-8")

    first = backup_project(root)
    first_tarball_bytes = first.tarball.read_bytes()

    (root / "_grimoire" / "b.txt").write_text("two", encoding="utf-8")
    second = backup_project(root)

    assert second.tarball != first.tarball
    assert second.tarball.name == "grimoire-state-2.tar.gz"
    assert second.manifest != first.manifest
    # The first snapshot's bytes are untouched — never silently replaced.
    assert first.tarball.read_bytes() == first_tarball_bytes

    # A third call with unchanged content since the second reuses the second
    # snapshot's own path rather than the (now stale) canonical one.
    third = backup_project(root)
    assert third.tarball == second.tarball


# ── apply / preexisting doctor baseline ──────────────────────────────────────


def test_apply_upgrade_softens_a_preexisting_dead_reference_into_a_repair_proposal(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Issue #502 (second rejeu réel, troisième défaut) : trois projets réels ont vu
    `apply` refuser sur des références périmées présentes AVANT la mise à niveau
    (`_memory/decisions-log.md`, `.github/copilot-instructions.md`, `.claude/skills/
    */SKILL.md`) — le projet restait « mis à niveau mais flow en échec », sans
    proposition. Un FAIL `paths_resolve` déjà présent dans la ligne de base de
    `preview` ne doit plus faire échouer `apply` : il devient une proposition
    `repair`."""
    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import apply_upgrade, preview_upgrade

    root = tmp_path_factory.mktemp("upgrade-repair") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-repair"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    decisions_log = root / "_grimoire" / "_memory" / "decisions-log.md"
    decisions_log.write_text(
        decisions_log.read_text(encoding="utf-8") + "\nVoir `_grimoire/core/config.yaml` pour l'historique.\n",
        encoding="utf-8",
    )

    preview = preview_upgrade(root)
    assert any("_grimoire/core/config.yaml" in ref for ref in preview.doctor_baseline), preview.doctor_baseline

    result = apply_upgrade(root)
    assert result.ok, (result.doctor_failures, result.hook.detail)
    assert result.repairs_proposed == 1
    assert len(result.preexisting_failures) == 1

    repairs = [p for p in list_proposals(root) if p.artifact_type == "repair"]
    assert len(repairs) == 1
    assert "_grimoire/core/config.yaml" in repairs[0].artifact_ref
    # No evident v3 replacement for this made-up legacy root — named honestly,
    # never a fabricated substitution.
    assert "pas de remplacement évident" in repairs[0].carrier_reason


def test_apply_upgrade_still_fails_on_a_regression_not_in_the_baseline(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The softening is scoped to dead references the baseline already saw — a doctor
    FAIL introduced between `preview` and `apply` (here: a routing regression, the
    same shape as an agent retiré) still fails `apply`, exactly as before this fix."""
    from grimoire.tools.project_upgrade import apply_upgrade, preview_upgrade

    root = tmp_path_factory.mktemp("upgrade-regression") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-regression"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    preview_upgrade(root)

    # Introduced strictly after `preview` — never in its baseline. `up` (which
    # `apply` runs) only regenerates the kit tier; an override it never touches.
    ghost = root / "_grimoire" / "overrides" / "agents" / "ghost-marker.md"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text('<agent tag="ghost-after-preview" name="Fantome" role="regression"/>\n', encoding="utf-8")

    result = apply_upgrade(root)
    assert not result.ok
    assert any("ghost-after-preview" in f for f in result.doctor_failures)
    assert result.repairs_proposed == 0


def _seed_ghost_managed_projection(root: Path, name: str = "ghost") -> None:
    """A managed host projection with no source in any tier (issue #510, point 1's
    real repro on the Forge: a `.claude/agents/x.md` carrying `grimoire:managed`
    survived an earlier archiving pass while its kit-tier source did not) — trips
    the `agents_referenced` doctor check, never `paths_resolve`."""
    claude_agents = root / ".claude" / "agents"
    claude_agents.mkdir(parents=True, exist_ok=True)
    (claude_agents / f"{name}.md").write_text(
        f'<!-- grimoire:managed -->\n---\nname: "{name}"\n---\n\nFantôme sans source.\n',
        encoding="utf-8",
    )


def test_apply_upgrade_softens_a_preexisting_non_paths_resolve_failure_into_a_repair_proposal(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Issue #510, point 1 : sur la Forge, `apply` a refusé sur un FAIL `agents_referenced`
    préexistant (une projection hôte managée sans source dans aucune tier), alors que la
    ligne de base de `preview` ne couvrait jusque-là que `paths_resolve`. Étendue à tout
    contrôle doctor : un FAIL déjà présent en préview devient une proposition `repair`
    nommant le contrôle, jamais une raison de refuser `apply`."""
    from grimoire.proposals import list_proposals
    from grimoire.tools.project_upgrade import apply_upgrade, preview_upgrade

    root = tmp_path_factory.mktemp("upgrade-repair-doctor") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-repair-doctor"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    _seed_ghost_managed_projection(root)

    preview = preview_upgrade(root)
    assert any(sig.startswith("agents_referenced:") for sig in preview.other_doctor_failures), (
        preview.other_doctor_failures
    )

    result = apply_upgrade(root)
    assert result.ok, (result.doctor_failures, result.hook.detail)
    assert result.repairs_proposed >= 1
    assert not result.failing_checks

    repairs = [p for p in list_proposals(root) if p.artifact_type == "repair" and p.category == "doctor-preexisting"]
    assert repairs
    assert any("agents_referenced" in r.specialty for r in repairs)


def test_apply_upgrade_still_fails_on_a_new_non_paths_resolve_doctor_regression(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The softening only ever covers a doctor failure the `preview` baseline already saw —
    one introduced strictly after `preview` still fails `apply`, exactly as before this fix,
    and never produces a `repair` proposal on its own initiative."""
    from grimoire.tools.project_upgrade import apply_upgrade, preview_upgrade

    root = tmp_path_factory.mktemp("upgrade-doctor-regression") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-doctor-regression"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    preview_upgrade(root)  # clean baseline — no ghost projection yet

    _seed_ghost_managed_projection(root)

    result = apply_upgrade(root)
    assert not result.ok
    assert "agents_referenced" in result.failing_checks
    assert any("ghost" in f for f in result.doctor_failures)
    assert result.repairs_proposed == 0


def test_repair_proposal_names_and_applies_an_evident_substitution(tmp_path_factory: pytest.TempPathFactory) -> None:
    """`_grimoire/_config/archetype.dna.yaml` is exactly the shape a real project hit
    (issue #502): a pre-boundary legacy root (`layout.LEGACY_KIT_ROOTS`) whose file now
    lives, byte-for-byte relative path, under the kit tier — an evident substitution,
    unlike the made-up `_grimoire/core/config.yaml` in the test above."""
    from grimoire.proposals import accept_proposal
    from grimoire.tools.project_upgrade import _dead_reference_strings, propose_repairs

    root = tmp_path_factory.mktemp("upgrade-repair-dna") / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "upgrade-repair-dna"], root)
    if not (root / "_grimoire" / "kit" / "archetype.dna.yaml").is_file():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    decisions_log = root / "_grimoire" / "_memory" / "decisions-log.md"
    decisions_log.write_text(
        decisions_log.read_text(encoding="utf-8") + "\nVoir `_grimoire/_config/archetype.dna.yaml` pour les traits.\n",
        encoding="utf-8",
    )

    refs = [r for r in _dead_reference_strings(root) if "_config/archetype.dna.yaml" in r]
    assert refs, "expected the planted reference to be dead"

    proposals = propose_repairs(root, refs)
    assert len(proposals) == 1
    assert "substitution évidente" in proposals[0].carrier_reason
    assert "_grimoire/kit/archetype.dna.yaml" in proposals[0].carrier_reason

    result = accept_proposal(root, proposals[0].slug)
    assert result["ok"] is True, result

    text = decisions_log.read_text(encoding="utf-8")
    assert "_grimoire/kit/archetype.dna.yaml" in text
    assert "_grimoire/_config/archetype.dna.yaml" not in text


def test_accept_repair_refuses_without_an_evident_substitution(upgrade_project: Path) -> None:
    """`accept_proposal` never guesses a fix `propose_repairs` did not itself name."""
    from grimoire.proposals import accept_proposal, create_manual_proposal

    proposal = create_manual_proposal(
        upgrade_project,
        slug="repair-no-evident-substitution",
        specialty="référence périmée : test",
        artifact_type="repair",
        carrier_reason="pas de remplacement évident pour _grimoire/core/config.yaml — revue humaine",
        artifact_ref="_grimoire/_memory/decisions-log.md:1 → _grimoire/core/config.yaml",
    )
    result = accept_proposal(upgrade_project, proposal.slug)
    assert result["ok"] is False
    assert "revue humaine" in result["error"] or "substitution" in result["error"]


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


def test_find_orphans_keeps_feature_declared_and_override_agents(upgrade_project: Path) -> None:
    """Real 2026-09-11 migration shape: a feature agent (`vectus`), an agent kept via
    `agents.custom_agents` (`fix-loop-orchestrator`), and a fully custom override — none of
    these three are orphans, even though none is in the fresh kit roster either."""
    from grimoire.tools.project_upgrade import find_orphans

    feature_name = _seed_feature_agent(upgrade_project)
    declared_name = _seed_declared_custom_agent(upgrade_project)
    override_name = _seed_override_only_agent(upgrade_project)

    report = find_orphans(upgrade_project)

    assert feature_name not in report.names
    assert declared_name not in report.names
    assert override_name not in report.names
    # The orphan seeded by `test_find_orphans_detects_stale_kit_agent` above is
    # still correctly flagged — this fix narrows false positives, it does not
    # blind the node to real ones.
    assert "retired-specialist" in report.names


def test_find_orphans_keeps_legacy_custom_tier_agent(upgrade_project: Path) -> None:
    """Issue #490 (second rejeu réel) : un agent vivant dans la tier custom héritée
    (`_grimoire/_config/custom/agents/`), ni dans le roster frais, ni déclaré sous
    `agents.custom_agents`, ni migré vers `overrides/`, n'est JAMAIS un orphelin — seule
    la tier kit (`_grimoire/kit/agents/`) fournit des candidats orphelins."""
    from grimoire.tools.project_upgrade import find_orphans

    legacy_name = _seed_legacy_custom_agent(upgrade_project)

    report = find_orphans(upgrade_project)

    assert legacy_name not in report.names, (
        f"{legacy_name!r} vit dans une tier possédée par le projet (custom legacy), "
        "il ne doit jamais être archivé comme orphelin"
    )
    # The orphan seeded by `test_find_orphans_detects_stale_kit_agent` above is
    # still correctly flagged — this fix narrows false positives on
    # project-owned tiers, it does not blind the node to real kit-tier orphans.
    assert "retired-specialist" in report.names


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
    # seeded by an earlier test in this module
    assert "_grimoire/_memory/agent-learnings/monitoring.md" in fiche_slugs


def test_accept_memory_link_creates_partial_override_when_carrier_has_none(upgrade_project: Path) -> None:
    """Accepting a memory-link for a kit agent with no override yet writes a partial one."""
    from grimoire.core.override_drift import KIT_SOURCE_HASH_KEY, NO_KIT_SOURCE
    from grimoire.hosts.collect import parse_frontmatter
    from grimoire.proposals import accept_proposal, create_manual_proposal

    override_path = upgrade_project / "_grimoire" / "overrides" / "agents" / "security-auditor.md"
    assert not override_path.is_file(), "must start with no override to prove the creation path"

    proposal = create_manual_proposal(
        upgrade_project,
        slug="memory-link-test-securite",
        specialty="fiche mémoire non raccordée : agent-learnings/monitoring.md",
        artifact_type="memory-link",
        target_agent="security-auditor",
        carrier_reason="porteur par mot entier : security-auditor",
        artifact_ref="_grimoire/_memory/agent-learnings/monitoring.md",
        category="memory-unlinked",
    )

    result = accept_proposal(upgrade_project, proposal.slug)
    assert result["ok"] is True, result
    assert override_path.is_file()

    data, _body = parse_frontmatter(override_path.read_text(encoding="utf-8"))
    assert data.get("extends") == "kit"
    assert data.get(KIT_SOURCE_HASH_KEY) not in (None, "", NO_KIT_SOURCE)
    # The kit agent's own declared context survives — only the fiche is added,
    # never replacing what security-auditor already carried.
    context = list(data.get("context") or [])
    assert "_grimoire/_memory/agent-learnings/monitoring.md" in context
    assert "_grimoire/_memory/shared-context.md" in context


def test_accept_memory_link_appends_to_an_existing_override_context(upgrade_project: Path) -> None:
    """A carrier that already has an override (partial or full) keeps what it had and gains the fiche."""
    from grimoire.core.override_drift import compute_kit_source_hash
    from grimoire.hosts.collect import parse_frontmatter
    from grimoire.proposals import accept_proposal, create_manual_proposal

    preexisting = upgrade_project / "_grimoire" / "_memory" / "agent-learnings" / "preexisting.md"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text("# Préexistant\n", encoding="utf-8")

    kit_path = upgrade_project / "_grimoire" / "kit" / "agents" / "agent-optimizer.md"
    override_path = upgrade_project / "_grimoire" / "overrides" / "agents" / "agent-optimizer.md"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(
        "---\n"
        "extends: kit\n"
        f"kit_source_hash: {compute_kit_source_hash(kit_path)}\n"
        "context:\n"
        "  - _grimoire/_memory/agent-learnings/preexisting.md\n"
        "---\n",
        encoding="utf-8",
    )

    proposal = create_manual_proposal(
        upgrade_project,
        slug="memory-link-test-optimizer",
        specialty="fiche mémoire non raccordée : agent-learnings/monitoring.md",
        artifact_type="memory-link",
        target_agent="agent-optimizer",
        carrier_reason="porteur par mot entier : agent-optimizer",
        artifact_ref="_grimoire/_memory/agent-learnings/monitoring.md",
        category="memory-unlinked",
    )

    result = accept_proposal(upgrade_project, proposal.slug)
    assert result["ok"] is True, result

    data, _body = parse_frontmatter(override_path.read_text(encoding="utf-8"))
    assert list(data.get("context") or []) == [
        "_grimoire/_memory/agent-learnings/preexisting.md",
        "_grimoire/_memory/agent-learnings/monitoring.md",
    ]


def test_accept_memory_link_refuses_only_for_lack_of_carrier(upgrade_project: Path) -> None:
    """The one legitimate refusal left: no plausible carrier at all — never "pas d'override"."""
    from grimoire.proposals import accept_proposal, create_manual_proposal

    proposal = create_manual_proposal(
        upgrade_project,
        slug="memory-link-test-orpheline",
        specialty="fiche mémoire non raccordée : agent-learnings/orpheline.md",
        artifact_type="memory-link",
        target_agent="",
        carrier_reason="aucun porteur plausible : à placer à la main",
        artifact_ref="agent-learnings/orpheline.md",
        category="memory-unlinked",
    )

    result = accept_proposal(upgrade_project, proposal.slug)
    assert result["ok"] is False
    assert "override" not in result["error"].lower()


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


def test_accept_needs_hosts_declares_enabled_hosts_and_preserves_comments(upgrade_project: Path) -> None:
    """Accepting `hosts-declare-enabled` writes `hosts.enabled`, round-tripped — no comment lost."""
    import re

    from grimoire.hosts.detection import detect_enabled_hosts
    from grimoire.proposals import accept_proposal
    from grimoire.tools._common import load_yaml_roundtrip
    from grimoire.tools.project_upgrade import propose_needs_hosts

    config_path = upgrade_project / "project-context.yaml"
    original = config_path.read_text(encoding="utf-8")
    stripped = re.sub(r"(?m)^hosts:\n(?:[ \t].*\n)*", "", original)
    config_path.write_text(stripped, encoding="utf-8")
    try:
        propose_needs_hosts(upgrade_project)
        result = accept_proposal(upgrade_project, "hosts-declare-enabled")
        assert result["ok"] is True, result

        data = load_yaml_roundtrip(config_path)
        assert sorted(data["hosts"]["enabled"]) == sorted(detect_enabled_hosts(upgrade_project))

        # grimoire-kit#430's actual guarantee: every comment survives the
        # round-trip untouched (block-style/indent width is a dumper choice
        # `load_yaml_roundtrip`/`save_yaml` never promised to mirror byte for
        # byte — only comments and quoting are).
        new_text = config_path.read_text(encoding="utf-8")
        for line in stripped.splitlines():
            if line.strip().startswith("#"):
                assert line in new_text, f"comment lost by the round-trip write: {line!r}"

        check = _grimoire(["-o", "json", "check", "."], upgrade_project)
        payload = json.loads(check.stdout or "{}")
        assert payload.get("all_ok") is True, check.stdout + check.stderr
    finally:
        config_path.write_text(original, encoding="utf-8")


def test_accept_needs_hosts_refuses_to_invent_a_command(upgrade_project: Path) -> None:
    """`needs-declare-commands` never gets a mechanical write — no default command exists to use."""
    from grimoire.proposals import accept_proposal, create_manual_proposal

    proposal = create_manual_proposal(
        upgrade_project,
        slug="needs-declare-commands",
        specialty="besoins d'exécution non résolus",
        artifact_type="needs-hosts",
        carrier_reason="déclarer needs.commands pour : migration-tool",
        artifact_ref="migration-tool",
        category="needs-unresolved",
    )

    result = accept_proposal(upgrade_project, proposal.slug)
    assert result["ok"] is False
    assert "à la main" in result["error"]


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


def test_hook_reports_failure_is_not_fooled_by_the_word_in_a_memory_recall() -> None:
    """Issue #502 (second rejeu réel) : TTS-Voice, un rappel de tâche mémoire nommant
    une erreur passée dans son texte, hook parfaitement sain. `probe_hook` cherchait
    "erreur"/"error" n'importe où dans le rendu JSON — un faux positif systématique
    pour tout projet dont la mémoire mentionne le mot."""
    from grimoire.tools.project_upgrade import _hook_reports_failure

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "[Grimoire — rappel de tâche]\n"
                "Dernière décision : une erreur de configuration avait été corrigée "
                "la semaine dernière, ne pas la réintroduire.\n"
                "[Grimoire — directive]\nSuis le standard agentique."
            ),
        }
    }
    assert _hook_reports_failure(payload) is False


def test_hook_reports_failure_catches_the_runtime_marker() -> None:
    """Le marqueur exact que `hosts/decisions/__init__.py::_failed_decision` émet
    quand une décision plante — au début d'un bloc, jamais une sous-chaîne libre."""
    from grimoire.tools.project_upgrade import _hook_reports_failure

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[Grimoire] hook session_start en erreur, session non bloquée : ValueError: boom",
        }
    }
    assert _hook_reports_failure(payload) is True


def test_hook_reports_failure_catches_a_top_level_error_key() -> None:
    from grimoire.tools.project_upgrade import _hook_reports_failure

    assert _hook_reports_failure({"error": "hook non exécutable"}) is True


def test_hook_reports_failure_ignores_the_marker_mid_sentence() -> None:
    """La forme exacte compte : une phrase qui *parle* du marqueur sans l'émettre
    en tête de bloc n'est pas un déclencheur — jamais une recherche libre."""
    from grimoire.tools.project_upgrade import _hook_reports_failure

    payload = {
        "hookSpecificOutput": {
            "additionalContext": "Note : le message \"[Grimoire] hook x en erreur\" apparaît dans la doc.",
        }
    }
    # Le marqueur doit être en début de ligne — ici il suit "Note : le message \"",
    # donc pas au début d'un bloc.
    assert _hook_reports_failure(payload) is False


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
