from __future__ import annotations

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import (
    STANDARD_PROFILE_FILE,
    apply_remediation_actions,
    build_context_bundle,
    build_decision_trace,
    build_knowledge_graph,
    build_knowledge_index,
    calculate_compliance_score,
    detect_standard_providers,
    list_profiles,
    list_standard_patterns,
    load_capability_map,
    load_needs_catalog,
    resolve_install_plan,
    setup_standard_profile,
    simulate_standard_hooks,
    verify_standard_profile,
)


def _posix_path(value: str) -> str:
    return value.replace("\\", "/")


def test_profiles_include_orchestrated() -> None:
    profiles = {profile.id for profile in list_profiles()}
    assert {"starter", "controlled", "orchestrated", "governed", "production"} <= profiles


def test_setup_generates_orchestrated_artifacts(tmp_path: Path) -> None:
    result = setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")

    assert result.profile == "orchestrated"
    assert (tmp_path / STANDARD_PROFILE_FILE).is_file()
    assert (tmp_path / "_grimoire/standard/mission-brief.md").is_file()
    assert (tmp_path / "_grimoire/standard/knowledge-source-registry.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/llm-provider-registry.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/task-board.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/memory-policy.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/context-contract.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/decision-graph.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/rule-packs.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/hook-registry.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/orchestration-policy.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/evidence-gates.yaml").is_file()
    assert (tmp_path / "_grimoire/standard/pattern-catalog.yaml").is_file()
    assert (tmp_path / "_grimoire-output/evidence/bootstrap/task-envelope.md").is_file()
    assert (tmp_path / "_grimoire-output/evidence/bootstrap/evidence-pack.md").is_file()

    mission = (tmp_path / "_grimoire/standard/mission-brief.md").read_text(encoding="utf-8")
    memory_policy = YAML(typ="safe").load(tmp_path / "_grimoire/standard/memory-policy.yaml")
    assert "- Project: Demo" in mission
    assert "- Selected profile: `orchestrated`" in mission
    assert memory_policy["memory_os"]["target"]["hot_memory"] == "redis"
    assert memory_policy["memory_os"]["target"]["semantic_memory"] == "weaviate-server"
    assert memory_policy["memory_os"]["target"]["graph_projection"] == "neo4j"


def test_setup_sanitizes_project_name_before_template_rendering(tmp_path: Path) -> None:
    setup_standard_profile(
        tmp_path,
        profile_id="controlled",
        project_name='Evil"\n  malicious_key: true',
    )

    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    data = YAML(typ="safe").load(registry)
    mission = (tmp_path / "_grimoire/standard/mission-brief.md").read_text(encoding="utf-8")

    assert data["metadata"]["project"] == 'Evil" malicious_key: true'
    assert "malicious_key" not in data["metadata"]
    assert "\n  malicious_key" not in mission


def test_setup_rejects_task_id_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid task_id"):
        setup_standard_profile(tmp_path, profile_id="starter", task_id="../../outside")


def test_verify_detects_missing_artifacts(tmp_path: Path) -> None:
    result = verify_standard_profile(tmp_path, profile_id="controlled")

    assert not result.ok
    assert STANDARD_PROFILE_FILE in result.missing


def test_verify_passes_after_setup(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled")

    result = verify_standard_profile(tmp_path)

    assert result.ok
    assert result.profile == "controlled"
    assert result.warning_count > 0
    assert any(check.id == "providers.none_enabled" for check in result.checks)


def test_setup_can_enable_selected_provider(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", provider_ids=("github-copilot",))

    registry = (tmp_path / "_grimoire/standard/llm-provider-registry.yaml").read_text(encoding="utf-8")
    result = verify_standard_profile(tmp_path)

    assert 'default_provider: github-copilot' in registry
    assert result.ok
    assert not any(check.id == "providers.none_enabled" for check in result.checks)


@pytest.mark.parametrize(
    ("provider_id", "env_var"),
    [
        ("github-copilot", "GITHUB_TOKEN"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google-gemini", "GEMINI_API_KEY"),
        ("local", "OLLAMA_HOST"),
    ],
)
def test_detect_standard_providers_masks_secret_values(provider_id: str, env_var: str) -> None:
    detections = {provider.id: provider for provider in detect_standard_providers({env_var: "secret-value"})}

    assert detections[provider_id].available
    assert f"env:{env_var}=set" in detections[provider_id].signals
    assert "secret-value" not in str(detections[provider_id])


def test_verify_fails_when_default_provider_is_disabled(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", provider_ids=("github-copilot",))
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("default_provider: github-copilot", "default_provider: openai"), encoding="utf-8")

    result = verify_standard_profile(tmp_path)

    assert not result.ok
    assert any(check.id == "providers.default_not_enabled" for check in result.checks)


def test_governed_profile_fails_on_pending_task_gates(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", provider_ids=("github-copilot",))

    result = verify_standard_profile(tmp_path)

    assert not result.ok
    assert any(check.id == "task.pending_gate" for check in result.checks)


def test_orchestrated_verify_reports_knowledge_placeholder(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated")

    result = verify_standard_profile(tmp_path)

    assert result.ok
    assert any(check.id == "knowledge.no_real_source" for check in result.checks)


def test_verify_blocks_knowledge_locator_outside_project_root(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated")
    registry = tmp_path / "_grimoire/standard/knowledge-source-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace('locator: ""', 'locator: "../../../etc/passwd"'), encoding="utf-8")

    result = verify_standard_profile(tmp_path)

    assert not result.ok
    assert any(check.id == "knowledge.locator_outside_root" for check in result.checks)


def test_verify_blocks_knowledge_index_manifest_outside_project_root(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated")
    registry = tmp_path / "_grimoire/standard/knowledge-source-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace('index_manifest: ""', 'index_manifest: "../../../etc/passwd"'), encoding="utf-8")

    result = verify_standard_profile(tmp_path)

    assert not result.ok
    assert any(check.id == "knowledge.index_manifest_outside_root" for check in result.checks)


def test_runtime_builders_create_context_decision_hooks_and_events(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated", provider_ids=("openai",))

    context = build_context_bundle(tmp_path)
    decision = build_decision_trace(tmp_path)
    knowledge = build_knowledge_index(tmp_path)
    graph = build_knowledge_graph(tmp_path)
    hooks = simulate_standard_hooks(tmp_path, phase="pre_context_build")

    assert context.path == Path("_grimoire-output/context/bootstrap/context-bundle.yaml")
    assert context.data["provider_constraints"]["matched_provider"] == "openai"
    assert _posix_path(context.data["knowledge_graph_ref"]) == "_grimoire-output/knowledge/bootstrap/knowledge-graph.yaml"
    assert context.data["memory_os"]["target"]["semantic_memory"] == "weaviate-server"
    assert decision.path == Path("_grimoire-output/decisions/bootstrap/decision-trace.yaml")
    assert knowledge.path == Path("_grimoire-output/knowledge/bootstrap/index-manifest.yaml")
    assert graph.path == Path("_grimoire-output/knowledge/bootstrap/knowledge-graph.yaml")
    assert graph.data["nodes"]
    assert hooks.path == Path("_grimoire-output/events/bootstrap/hook-simulation-pre_context_build.yaml")
    assert (tmp_path / context.path).is_file()
    assert (tmp_path / decision.path).is_file()
    assert (tmp_path / knowledge.path).is_file()
    assert (tmp_path / graph.path).is_file()
    assert (tmp_path / hooks.path).is_file()
    assert (tmp_path / "_grimoire-output/events/runtime-journal.jsonl").is_file()


def test_compliance_score_includes_dimensions(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", provider_ids=("github-copilot",))

    result = calculate_compliance_score(tmp_path)

    assert result.output_path == Path("_grimoire-output/standard/bootstrap/compliance-score.yaml")
    assert "provider_policy" in result.dimensions
    assert "memory_os" in result.dimensions
    assert result.dimensions["provider_policy"]["percentage"] == 100


def test_apply_remediation_generates_missing_artifacts(tmp_path: Path) -> None:
    result = apply_remediation_actions(tmp_path, profile_id="starter")

    assert result.profile == "starter"
    assert STANDARD_PROFILE_FILE in result.written
    assert (tmp_path / STANDARD_PROFILE_FILE).is_file()
    assert (tmp_path / result.audit_path).is_file()


def test_standard_pattern_catalog_lists_patterns(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated")

    patterns = list_standard_patterns(tmp_path, category="context")

    assert any(pattern["id"] == "advanced-context-orchestrator" for pattern in patterns)


def test_verify_fails_on_manifest_profile_mismatch(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter")
    manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("profile: starter", "profile: production"), encoding="utf-8")

    result = verify_standard_profile(tmp_path, profile_id="starter")

    assert not result.ok
    assert any(check.id == "manifest.profile_mismatch" for check in result.checks)


def test_cli_standard_init_and_verify_json(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "init",
        str(tmp_path),
        "--profile",
        "starter",
    ])
    assert init_result.exit_code == 0
    init_data = json.loads(init_result.output)
    assert init_data["ok"] is True
    assert init_data["profile"] == "starter"

    verify_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "verify",
        str(tmp_path),
    ])
    assert verify_result.exit_code == 0
    verify_data = json.loads(verify_result.output)
    assert verify_data["ok"] is True
    assert verify_data["profile"] == "starter"
    assert "checks" in verify_data


def test_cli_standard_init_accepts_provider_selection(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "init",
        str(tmp_path),
        "--profile",
        "controlled",
        "--providers",
        "github-copilot,claude",
        "--provider-policy",
        "mixed",
    ])

    assert init_result.exit_code == 0
    init_data = json.loads(init_result.output)
    assert init_data["provider_policy"] == "mixed"

    verify_result = runner.invoke(app, ["-o", "json", "standard", "verify", str(tmp_path)])
    assert verify_result.exit_code == 0
    verify_data = json.loads(verify_result.output)
    assert not any(check["id"] == "providers.none_enabled" for check in verify_data["checks"])


def test_cli_standard_detect_providers_json() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["-o", "json", "standard", "detect-providers"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {provider["id"] for provider in data} >= {"github-copilot", "openai", "anthropic", "google-gemini", "local"}


def test_cli_standard_audit_markdown(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["standard", "init", str(tmp_path), "--profile", "orchestrated"])

    result = runner.invoke(app, [
        "standard",
        "audit",
        str(tmp_path),
        "--profile",
        "orchestrated",
        "--markdown",
    ])

    assert result.exit_code == 0
    assert "# Agentic Standard Audit" in result.output
    assert "knowledge.no_real_source" in result.output


def test_cli_standard_runtime_commands_json(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "init",
        str(tmp_path),
        "--profile",
        "orchestrated",
        "--provider",
        "openai",
    ])

    context_result = runner.invoke(app, ["-o", "json", "standard", "context", "build", str(tmp_path)])
    decision_result = runner.invoke(app, ["-o", "json", "standard", "decision", "trace", str(tmp_path)])
    knowledge_result = runner.invoke(app, ["-o", "json", "standard", "knowledge", "index", str(tmp_path)])
    graph_result = runner.invoke(app, ["-o", "json", "standard", "knowledge", "graph", str(tmp_path)])
    pattern_result = runner.invoke(app, ["-o", "json", "standard", "pattern", "list", str(tmp_path)])
    hooks_result = runner.invoke(app, ["-o", "json", "standard", "hooks", "simulate", str(tmp_path), "--phase", "pre_context_build"])
    gate_result = runner.invoke(app, ["-o", "json", "standard", "gate", "check", str(tmp_path), "--target-state", "review"])
    events_result = runner.invoke(app, ["-o", "json", "standard", "events", "audit", str(tmp_path)])
    fix_result = runner.invoke(app, ["-o", "json", "standard", "fix", str(tmp_path)])
    score_result = runner.invoke(app, ["-o", "json", "standard", "score", str(tmp_path)])

    assert context_result.exit_code == 0
    assert _posix_path(json.loads(context_result.output)["path"]) == "_grimoire-output/context/bootstrap/context-bundle.yaml"
    assert json.loads(context_result.output)["data"]["provider_constraints"]["matched_provider"] == "openai"
    assert decision_result.exit_code == 0
    assert knowledge_result.exit_code == 0
    assert _posix_path(json.loads(knowledge_result.output)["path"]) == "_grimoire-output/knowledge/bootstrap/index-manifest.yaml"
    assert graph_result.exit_code == 0
    assert _posix_path(json.loads(graph_result.output)["path"]) == "_grimoire-output/knowledge/bootstrap/knowledge-graph.yaml"
    assert pattern_result.exit_code == 0
    assert any(pattern["id"] == "advanced-context-orchestrator" for pattern in json.loads(pattern_result.output))
    assert hooks_result.exit_code == 0
    assert gate_result.exit_code == 0
    assert events_result.exit_code == 0
    assert json.loads(events_result.output)["event_count"] >= 4
    assert fix_result.exit_code == 0
    assert "actions" in json.loads(fix_result.output)
    assert score_result.exit_code in {0, 1}
    assert "dimensions" in json.loads(score_result.output)


def test_cli_standard_fix_apply_writes_safe_missing_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["-o", "json", "standard", "fix", str(tmp_path), "--profile", "starter", "--apply"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["applied"] is True
    assert _posix_path(str(STANDARD_PROFILE_FILE)) in [_posix_path(path) for path in data["written"]]
    assert (tmp_path / STANDARD_PROFILE_FILE).is_file()


def test_cli_standard_gate_strict_uses_ci_blocking_exit_for_governed(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["standard", "init", str(tmp_path), "--profile", "governed", "--provider", "github-copilot"])

    memory_result = runner.invoke(app, ["-o", "json", "standard", "memory", "verify", str(tmp_path), "--profile", "governed"])
    assert memory_result.exit_code == 0
    assert json.loads(memory_result.output)["ok"] is True

    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "standard",
            "gate",
            "check",
            str(tmp_path),
            "--profile",
            "governed",
            "--target-state",
            "review",
            "--strict",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["strict"] is True


def test_governed_gate_fails_when_memory_os_contract_drifted(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["standard", "init", str(tmp_path), "--profile", "governed", "--provider", "github-copilot"])
    memory_policy = tmp_path / "_grimoire/standard/memory-policy.yaml"
    memory_policy.write_text(
        memory_policy.read_text(encoding="utf-8").replace('semantic_memory: "weaviate-server"', 'semantic_memory: "qdrant-server"'),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "standard",
            "gate",
            "check",
            str(tmp_path),
            "--profile",
            "governed",
            "--target-state",
            "review",
            "--strict",
        ],
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert any(check["id"] == "memory.os_semantic_memory_target" for check in data["checks"])


def test_resolve_install_plan_unions_needs_patterns_and_extras() -> None:
    plan = resolve_install_plan(needs=["semantic-memory-rag", "hot-session-cache"])

    assert plan.profile == "governed"
    assert "redis" in plan.tech_extras
    assert "weaviate" in plan.tech_extras
    assert "redis-hot-memory-soft-gate" in plan.patterns
    assert "governed-memory-policy" in plan.patterns
    assert plan.pip_target == "grimoire-kit[redis,weaviate]"
    assert plan.warnings == ()


def test_resolve_install_plan_unknown_need_is_ignored_with_warning() -> None:
    plan = resolve_install_plan(needs=["does-not-exist"])

    assert plan.profile == "starter"
    assert any("does-not-exist" in warning for warning in plan.warnings)


def test_resolve_install_plan_explicit_profile_overrides_floor() -> None:
    plan = resolve_install_plan(patterns=["redis-hot-memory-soft-gate"], profile="production")

    assert plan.profile == "production"


def test_resolve_install_plan_dedups_tech_extras() -> None:
    plan = resolve_install_plan(
        patterns=["redis-hot-memory-soft-gate"],
        extra_tech_extras=["redis", "redis"],
    )

    assert plan.tech_extras.count("redis") == 1


def test_cli_standard_needs_json_lists_catalog() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["-o", "json", "standard", "needs"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = {entry["id"] for entry in data}
    assert {"solo-prototyping", "semantic-memory-rag", "hot-session-cache"} <= ids


def test_cli_standard_plan_json_resolves_extras() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["-o", "json", "standard", "plan", "--needs", "semantic-memory-rag"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile"] == "orchestrated"
    assert "weaviate" in data["tech_extras"]
    assert data["pip_target"] == "grimoire-kit[weaviate]"


def test_cli_standard_doctor_json_reports_extras() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["-o", "json", "standard", "doctor"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "ok" in data
    assert isinstance(data["extras"], list)
    assert all({"extra", "available"} <= set(entry) for entry in data["extras"])


def test_cli_standard_init_with_needs_writes_manifest(tmp_path: Path) -> None:
    runner = CliRunner()

    init_result = runner.invoke(app, [
        "-o",
        "json",
        "standard",
        "init",
        str(tmp_path),
        "--needs",
        "semantic-memory-rag",
        "--project-name",
        "RagApp",
    ])

    assert init_result.exit_code == 0
    init_data = json.loads(init_result.output)
    assert init_data["profile"] == "orchestrated"
    assert "weaviate" in init_data["plan"]["tech_extras"]

    manifest_path = tmp_path / "_grimoire" / "standard" / "install-manifest.yaml"
    assert manifest_path.is_file()
    manifest = YAML(typ="safe").load(manifest_path)
    assert manifest["selection"]["needs"] == ["semantic-memory-rag"]
    assert manifest["install"]["tech_extras"] == ["weaviate"]

    verify_result = runner.invoke(app, ["-o", "json", "standard", "verify", str(tmp_path)])
    assert verify_result.exit_code == 0
    assert json.loads(verify_result.output)["ok"] is True


_EXPECTED_PATTERNS = frozenset({
    "advanced-context-orchestrator",
    "governed-memory-policy",
    "evidence-gated-fsm",
    "provider-routing-contract",
    "runtime-journal",
    "redis-hot-memory-soft-gate",
    "governed-hook-gateway",
    "skill-classification-matrix",
    "governed-observability-cockpit",
    "code-graph-projection",
    "governed-agent-orchestration",
    "governed-knowledge-indexing",
    "mission-evidence-ledger",
    "tool-mediation-gate",
    "provider-cost-slo",
})


def test_capability_map_covers_all_expected_patterns() -> None:
    capability_map = load_capability_map()

    assert set(capability_map["patterns"]) == _EXPECTED_PATTERNS


def test_scaffolded_catalog_matches_capability_map(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed")
    catalog = YAML(typ="safe").load(tmp_path / "_grimoire/standard/pattern-catalog.yaml")
    catalog_ids = {pattern["id"] for pattern in catalog["patterns"]}

    assert catalog_ids == set(load_capability_map()["patterns"])
    assert catalog_ids == _EXPECTED_PATTERNS


def test_every_need_resolves_without_warnings() -> None:
    known_patterns = set(load_capability_map()["patterns"])

    for need in load_needs_catalog()["needs"]:
        plan = resolve_install_plan(needs=[need["id"]])
        assert plan.warnings == (), f"{need['id']} produced warnings: {plan.warnings}"
        assert set(plan.patterns) <= known_patterns
