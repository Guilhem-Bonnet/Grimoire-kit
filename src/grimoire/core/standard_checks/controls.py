"""Les vérificateurs des contrôles gouvernés (sécurité, qualité, runtime).

Chaque fonction lit un artefact et pousse ses constats dans le
StandardVerificationResult via _add_check. Les identifiants qu'elles
émettent sont déclarés dans grimoire.core.standard_checks.registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import (
    StandardProfile,
    StandardVerificationResult,
    _add_check,
    _is_inside_root,
    _load_yaml_file,
    _text_file,
)
from grimoire.core.standard_generation import EVIDENCE_DIR, STANDARD_DIR


def _verify_score_and_exceptions(root: Path, result: StandardVerificationResult) -> None:
    score_path = STANDARD_DIR / "compliance-score.yaml"
    score_data = _load_yaml_file(root, score_path, result)
    if isinstance(score_data, dict):
        for key in ("dimensions", "thresholds"):
            if not isinstance(score_data.get(key), dict) or not score_data.get(key):
                _add_check(
                    result, f"score.{key}_missing", "error", f"Compliance score must declare {key}.", path=score_path
                )

    for rel_path, list_key in (
        (STANDARD_DIR / "accepted-risks.yaml", "risks"),
        (STANDARD_DIR / "waivers.yaml", "waivers"),
        (STANDARD_DIR / "remediation-plan.yaml", "actions"),
    ):
        data = _load_yaml_file(root, rel_path, result)
        if isinstance(data, dict) and not isinstance(data.get(list_key), list):
            _add_check(
                result, f"{list_key}.invalid", "error", f"{rel_path} must declare {list_key} as a list.", path=rel_path
            )


def _verify_blast_radius_policy(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "blast-radius-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    strict_profile = profile.id in {"governed", "production"}
    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        _add_check(
            result,
            "tools.blast_radius_defaults_missing",
            "error",
            "Blast-radius policy must declare defaults.",
            path=rel_path,
        )
    elif defaults.get("enforce") is not True:
        severity = "error" if strict_profile else "warning"
        _add_check(
            result,
            "tools.blast_radius_unenforced",
            severity,
            "Blast-radius policy defaults must enforce limits (fail closed).",
            path=rel_path,
        )

    limits = data.get("limits")
    if not isinstance(limits, list) or not limits:
        _add_check(
            result,
            "tools.blast_radius_undeclared",
            "warning",
            "Blast-radius policy declares no per-task tool limits.",
            path=rel_path,
        )
        return

    for limit in limits:
        if not isinstance(limit, dict):
            _add_check(
                result,
                "tools.blast_radius_invalid",
                "error",
                "Blast-radius limit entries must be mappings.",
                path=rel_path,
            )
            continue
        limit_id = str(limit.get("id", "")).strip()
        if not limit_id:
            _add_check(result, "tools.blast_radius_id_missing", "error", "Blast-radius limit has no id.", path=rel_path)

        network = str(limit.get("network", "")).strip()
        if network not in {"deny", "allowlist"}:
            _add_check(
                result,
                "tools.blast_radius_network_invalid",
                "error",
                f"Blast-radius limit {limit_id!r} network must be 'deny' or 'allowlist'.",
                path=rel_path,
            )
        elif network == "allowlist" and not (
            isinstance(limit.get("network_allowlist"), list) and limit.get("network_allowlist")
        ):
            _add_check(
                result,
                "tools.blast_radius_allowlist_empty",
                "error",
                f"Blast-radius limit {limit_id!r} uses a network allowlist but declares no hosts.",
                path=rel_path,
            )

        production_touch = str(limit.get("production_touch", "")).strip()
        if production_touch not in {"deny", "dry-run", "allow"}:
            _add_check(
                result,
                "tools.blast_radius_production_invalid",
                "error",
                f"Blast-radius limit {limit_id!r} production_touch must be 'deny', 'dry-run' or 'allow'.",
                path=rel_path,
            )
        elif production_touch == "allow":
            severity = "error" if strict_profile else "warning"
            _add_check(
                result,
                "tools.blast_radius_production_allow",
                severity,
                f"Blast-radius limit {limit_id!r} allows unguarded production access.",
                path=rel_path,
            )

        writable = limit.get("writable_paths")
        if not isinstance(writable, list):
            _add_check(
                result,
                "tools.blast_radius_writable_invalid",
                "error",
                f"Blast-radius limit {limit_id!r} writable_paths must be a list.",
                path=rel_path,
            )
        else:
            for raw in writable:
                candidate = (root / str(raw)).resolve()
                if not _is_inside_root(root, candidate):
                    _add_check(
                        result,
                        "tools.blast_radius_writable_outside_root",
                        "error",
                        f"Blast-radius limit {limit_id!r} writable path escapes project root: {raw}",
                        path=rel_path,
                    )


def _verify_privilege_boundary(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "privilege-boundary.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    strict_profile = profile.id in {"governed", "production"}
    if data.get("controller_token_scrub") is not True:
        _add_check(
            result,
            "privilege.scrub_disabled",
            "error" if strict_profile else "warning",
            "Controller token scrub must be enabled before agent spawn.",
            path=rel_path,
        )
    boundaries = data.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        _add_check(
            result,
            "privilege.boundary_undeclared",
            "warning",
            "No controller/agent privilege boundary is declared.",
            path=rel_path,
        )
        return
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            _add_check(
                result,
                "privilege.boundary_invalid",
                "error",
                "Privilege boundary entries must be mappings.",
                path=rel_path,
            )
            continue
        if boundary.get("infra_tokens_denied") is not True:
            boundary_id = str(boundary.get("id", "")).strip()
            _add_check(
                result,
                "privilege.infra_token_exposed",
                "error",
                f"Boundary {boundary_id!r} does not deny infrastructure tokens.",
                path=rel_path,
            )


def _verify_prompt_firewall(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "prompt-firewall.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    strict_profile = profile.id in {"governed", "production"}
    if data.get("isolate_external_content") is not True:
        _add_check(
            result,
            "firewall.isolation_disabled",
            "error" if strict_profile else "warning",
            "External content must be isolated from control instructions.",
            path=rel_path,
        )
    if data.get("instruction_override_blocked") is not True:
        _add_check(
            result,
            "firewall.override_allowed",
            "error" if strict_profile else "warning",
            "Instruction override by external content must be blocked.",
            path=rel_path,
        )
    sources = data.get("untrusted_sources")
    if not isinstance(sources, list) or not sources:
        _add_check(result, "firewall.no_sources", "warning", "No untrusted content source is declared.", path=rel_path)
        return
    for source in sources:
        if isinstance(source, dict) and source.get("quarantine") is not True:
            source_id = str(source.get("id", "")).strip()
            _add_check(
                result,
                "firewall.source_not_quarantined",
                "error",
                f"Untrusted source {source_id!r} is not quarantined.",
                path=rel_path,
            )


def _verify_remote_hygiene(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "remote-hygiene.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    if data.get("check_stale_refs") is not True:
        _add_check(result, "remote.stale_check_disabled", "warning", "Stale ref detection is disabled.", path=rel_path)
    if data.get("require_remote_reachable") is not True:
        _add_check(
            result,
            "remote.reachability_unchecked",
            "warning",
            "Remote reachability is not verified before audit.",
            path=rel_path,
        )
    age = data.get("max_branch_age_days")
    if not isinstance(age, int) or isinstance(age, bool) or age <= 0:
        _add_check(
            result, "remote.age_unbounded", "warning", "max_branch_age_days must be a positive integer.", path=rel_path
        )
    branches = data.get("max_open_branches")
    if not isinstance(branches, int) or isinstance(branches, bool) or branches <= 0:
        _add_check(
            result,
            "remote.branches_unbounded",
            "warning",
            "max_open_branches must be a positive integer.",
            path=rel_path,
        )


def _verify_decision_council(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "decision-council.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    quorum = data.get("quorum")
    if not isinstance(quorum, int) or isinstance(quorum, bool) or quorum < 2:
        _add_check(
            result, "council.quorum_too_low", "error", "Decision council quorum must be at least 2.", path=rel_path
        )
    veto_roles = data.get("veto_roles")
    if not isinstance(veto_roles, list) or not veto_roles:
        _add_check(
            result, "council.no_veto", "error", "Decision council must declare at least one veto role.", path=rel_path
        )
    if data.get("budget_cap_usd") is None:
        _add_check(result, "council.no_budget_cap", "warning", "Decision council has no budget cap.", path=rel_path)
    triggers = data.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        _add_check(
            result, "council.no_triggers", "warning", "Decision council declares no escalation triggers.", path=rel_path
        )


def _verify_compression_gate(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "compression-gate.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    preserve_checks = (
        ("preserve_provenance", "compression.provenance_dropped", "provenance"),
        ("preserve_constraints", "compression.constraints_dropped", "constraints"),
        ("preserve_tool_atomicity", "compression.atomicity_dropped", "tool-call/tool-result atomicity"),
        ("preserve_evidence", "compression.evidence_dropped", "evidence"),
    )
    for key, code, label in preserve_checks:
        if data.get(key) is not True:
            _add_check(result, code, "error", f"Context compression must preserve {label}.", path=rel_path)
    ratio = data.get("max_compression_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 < ratio <= 1:
        _add_check(
            result,
            "compression.ratio_invalid",
            "warning",
            "max_compression_ratio must be a number in (0, 1].",
            path=rel_path,
        )


def _verify_memory_integrity(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "memory-integrity.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    integrity_checks = (
        ("check_provenance", "integrity.provenance_unchecked", "provenance"),
        ("check_drift", "integrity.drift_unchecked", "drift"),
        ("check_poisoning", "integrity.poisoning_unchecked", "poisoning"),
    )
    for key, code, label in integrity_checks:
        if data.get(key) is not True:
            _add_check(result, code, "error", f"Memory integrity validator must check {label}.", path=rel_path)
    if data.get("expiry_required") is not True:
        _add_check(result, "integrity.no_expiry", "warning", "Promoted memories must declare expiry.", path=rel_path)


def _verify_merge_lane(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "merge-lane.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    classes = data.get("classes")
    transient = classes.get("transient") if isinstance(classes, dict) else None
    hard = classes.get("hard") if isinstance(classes, dict) else None
    if not (isinstance(transient, list) and transient and isinstance(hard, list) and hard):
        _add_check(
            result,
            "merge.classes_incomplete",
            "error",
            "Merge lane must declare non-empty transient and hard fault classes.",
            path=rel_path,
        )
    budget = data.get("transient_retry_budget")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        _add_check(
            result,
            "merge.no_retry_budget",
            "warning",
            "Transient retry budget must be a positive integer.",
            path=rel_path,
        )
    if data.get("escalate_on_hard") is not True:
        _add_check(
            result,
            "merge.hard_not_escalated",
            "error",
            "Hard merge faults must escalate instead of retrying.",
            path=rel_path,
        )


def _verify_cost_registry(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "cost-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    pricing = data.get("pricing")
    if not isinstance(pricing, list) or not pricing:
        _add_check(result, "cost.no_pricing", "error", "Cost registry must declare per-model pricing.", path=rel_path)
    else:
        for entry in pricing:
            if not (
                isinstance(entry, dict)
                and str(entry.get("model", "")).strip()
                and str(entry.get("provider", "")).strip()
            ):
                _add_check(
                    result,
                    "cost.pricing_incomplete",
                    "error",
                    "Each pricing entry must declare model and provider.",
                    path=rel_path,
                )
    budgets = data.get("budgets")
    if not isinstance(budgets, dict) or budgets.get("per_mission_usd") is None:
        _add_check(result, "cost.no_budget", "warning", "Cost registry has no per-mission budget.", path=rel_path)
    slo = data.get("slo")
    if not isinstance(slo, dict) or slo.get("max_crash_rate_pct") is None or slo.get("max_unhealthy_rate_pct") is None:
        _add_check(
            result,
            "cost.no_slo",
            "warning",
            "Cost registry has no session reliability SLO (crash/unhealthy rate).",
            path=rel_path,
        )


def _verify_guardrail_contract(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "guardrail-contract.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    if not str(data.get("version", "")).strip():
        _add_check(
            result, "guardrail.unversioned", "error", "Guardrail contract must declare a version.", path=rel_path
        )
    guardrails = data.get("guardrails")
    if not isinstance(guardrails, dict):
        _add_check(
            result,
            "guardrail.faces_missing",
            "error",
            "Guardrail contract must declare input/output/tool/model guardrails.",
            path=rel_path,
        )
        return
    valid_modes = {"enforce", "monitor"}
    valid_actions = {"block", "escalate", "log"}
    for face in ("input", "output", "tool", "model"):
        spec = guardrails.get(face)
        if not isinstance(spec, dict):
            _add_check(
                result, f"guardrail.{face}_missing", "error", f"Guardrail face {face!r} is missing.", path=rel_path
            )
            continue
        if spec.get("mode") not in valid_modes:
            _add_check(
                result,
                "guardrail.invalid_mode",
                "error",
                f"Guardrail face {face!r} mode must be 'enforce' or 'monitor'.",
                path=rel_path,
            )
        if spec.get("on_violation") not in valid_actions:
            _add_check(
                result,
                "guardrail.no_violation_action",
                "error",
                f"Guardrail face {face!r} must declare a violation action.",
                path=rel_path,
            )


def _verify_visual_evidence(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "visual-evidence.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    require_for = data.get("require_for")
    if not isinstance(require_for, list) or not require_for:
        _add_check(
            result, "visual.no_triggers", "warning", "Visual evidence gate declares no UI/UX triggers.", path=rel_path
        )
    kinds = data.get("required_artifact_kinds")
    kind_set = {str(kind) for kind in kinds} if isinstance(kinds, list) else set()
    if not {"screenshot", "dom"} <= kind_set:
        _add_check(
            result,
            "visual.missing_artifact_kinds",
            "error",
            "Visual evidence must require at least screenshot and dom artifacts.",
            path=rel_path,
        )
    if data.get("journey_required") is not True:
        _add_check(
            result,
            "visual.journey_not_required",
            "warning",
            "Visual evidence gate does not require a user journey.",
            path=rel_path,
        )


def _verify_workspace_isolation(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "workspace-isolation.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    if str(data.get("isolation_mode", "")).strip() not in {"container", "venv", "sandbox", "process"}:
        _add_check(
            result,
            "workspace.isolation_undeclared",
            "warning",
            "Workspace isolation_mode must be container/venv/sandbox/process.",
            path=rel_path,
        )
    writable = data.get("writable_roots")
    if not isinstance(writable, list) or not writable:
        _add_check(
            result,
            "workspace.writable_unbounded",
            "warning",
            "Workspace must declare bounded writable_roots.",
            path=rel_path,
        )
    if str(data.get("network", "")).strip() not in {"deny", "allowlist"}:
        _add_check(
            result, "workspace.network_open", "error", "Workspace network must be 'deny' or 'allowlist'.", path=rel_path
        )


def _verify_environment_policy(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "environment-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    environments = data.get("environments")
    if not isinstance(environments, dict) or not {"local", "ci", "staging", "production"} <= set(environments):
        _add_check(
            result,
            "env.environments_missing",
            "error",
            "Environment policy must declare local, ci, staging and production.",
            path=rel_path,
        )
        return
    production = environments.get("production")
    if not isinstance(production, dict) or not (
        production.get("dry_run") is True or production.get("approval") is True
    ):
        _add_check(
            result,
            "env.production_unguarded",
            "warning",
            "Production environment must require dry-run or approval.",
            path=rel_path,
        )


def _verify_browser_tool_contract(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "browser-tool-contract.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    if data.get("require_dom") is not True:
        _add_check(
            result, "browser.dom_required", "error", "Browser tool contract must require DOM capture.", path=rel_path
        )
    if data.get("require_screenshot") is not True:
        _add_check(
            result,
            "browser.screenshot_required",
            "error",
            "Browser tool contract must require screenshots.",
            path=rel_path,
        )
    if not isinstance(data.get("allowed_domains"), list):
        _add_check(
            result,
            "browser.domains_invalid",
            "error",
            "Browser tool contract allowed_domains must be a list.",
            path=rel_path,
        )


def _verify_runtime_provider_contract(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "runtime-provider-contract.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    lifecycle = data.get("lifecycle")
    if not isinstance(lifecycle, dict) or not {"start", "stop", "health", "cleanup"} <= set(lifecycle):
        _add_check(
            result,
            "runtime.lifecycle_incomplete",
            "error",
            "Runtime provider contract must declare start/stop/health/cleanup.",
            path=rel_path,
        )
    if not isinstance(data.get("resources"), dict):
        _add_check(
            result,
            "runtime.resources_unbounded",
            "warning",
            "Runtime provider contract should declare resource limits.",
            path=rel_path,
        )
    if not isinstance(data.get("logs"), dict):
        _add_check(
            result,
            "runtime.logs_undeclared",
            "warning",
            "Runtime provider contract should declare a logs policy.",
            path=rel_path,
        )


def _verify_prompt_version_log(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "prompt-version-log.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    track = data.get("track")
    track_set = {str(item) for item in track} if isinstance(track, list) else set()
    if not {"prompt", "provider"} <= track_set:
        _add_check(
            result,
            "promptver.tracking_incomplete",
            "warning",
            "Prompt version tracking should include at least prompt and provider.",
            path=rel_path,
        )
    if data.get("link_to_evals") is not True:
        _add_check(
            result, "promptver.evals_unlinked", "warning", "Prompt versions should be linked to evals.", path=rel_path
        )


def _verify_cluster_action_policy(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "cluster-action-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    actions = data.get("high_risk_actions")
    if not isinstance(actions, list) or not actions:
        _add_check(
            result,
            "cluster.actions_missing",
            "warning",
            "Cluster action policy declares no high-risk actions.",
            path=rel_path,
        )
    if data.get("require_dry_run") is not True:
        _add_check(
            result,
            "cluster.dry_run_required",
            "error",
            "High-risk cluster actions must require dry-run.",
            path=rel_path,
        )
    if data.get("require_rollback") is not True:
        _add_check(
            result,
            "cluster.rollback_required",
            "error",
            "High-risk cluster actions must require rollback proof.",
            path=rel_path,
        )


def _verify_doc_graph_pipeline(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "doc-graph-pipeline.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        _add_check(
            result, "docgraph.sources_missing", "warning", "Doc-to-graph pipeline declares no sources.", path=rel_path
        )
    extract = data.get("extract")
    if not isinstance(extract, dict) or extract.get("relations") is not True:
        _add_check(
            result,
            "docgraph.relations_disabled",
            "warning",
            "Doc-to-graph pipeline should extract relations.",
            path=rel_path,
        )
    if data.get("provenance_required") is not True:
        _add_check(
            result,
            "docgraph.provenance_optional",
            "warning",
            "Doc-to-graph nodes/edges should require provenance.",
            path=rel_path,
        )


def _verify_flow_dsl_manifest(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "flow-dsl-manifest.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    flow = data.get("flow")
    if not isinstance(flow, list) or not flow:
        _add_check(
            result, "flowdsl.steps_missing", "error", "Flow manifest must declare at least one step.", path=rel_path
        )
    if not str(data.get("export_format", "")).strip():
        _add_check(
            result,
            "flowdsl.export_undeclared",
            "warning",
            "Flow manifest should declare an export_format.",
            path=rel_path,
        )


def _verify_workflow_state_manifest(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "workflow-state-manifest.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    states = data.get("states")
    state_set = {str(state) for state in states} if isinstance(states, list) else set()
    if not state_set:
        _add_check(result, "wsm.states_missing", "error", "Workflow state manifest must declare states.", path=rel_path)
        return
    if str(data.get("initial_state", "")).strip() not in state_set:
        _add_check(
            result,
            "wsm.initial_undeclared",
            "error",
            "Workflow initial_state must be one of the declared states.",
            path=rel_path,
        )
    transitions = data.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        _add_check(
            result,
            "wsm.transitions_missing",
            "warning",
            "Workflow state manifest declares no transitions.",
            path=rel_path,
        )
        return
    for transition in transitions:
        if (
            not isinstance(transition, dict)
            or str(transition.get("from", "")) not in state_set
            or str(transition.get("to", "")) not in state_set
        ):
            _add_check(
                result,
                "wsm.transition_invalid",
                "error",
                "Each transition must reference declared from/to states.",
                path=rel_path,
            )


def _verify_k8s_agent_manifest(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "k8s-agent-manifest.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    if not str(data.get("crd_kind", "")).strip():
        _add_check(result, "k8s.crd_missing", "error", "K8s agent manifest must declare a crd_kind.", path=rel_path)
    resource_limits = data.get("resource_limits")
    if not isinstance(resource_limits, dict) or not resource_limits:
        _add_check(
            result,
            "k8s.resource_limits_missing",
            "error",
            "K8s agent manifest must declare resource_limits.",
            path=rel_path,
        )
    if not str(data.get("service_account", "")).strip():
        _add_check(
            result,
            "k8s.service_account_missing",
            "warning",
            "K8s agent manifest should declare a service_account.",
            path=rel_path,
        )
    telemetry = data.get("telemetry")
    if not isinstance(telemetry, dict) or telemetry.get("otel") is not True:
        _add_check(
            result,
            "k8s.telemetry_missing",
            "warning",
            "K8s agent manifest should enable OTel telemetry.",
            path=rel_path,
        )


# ---------------------------------------------------------------------------
# Les artefacts qui ferment les dix-sept exigences AG-* sans artefact (#246).
# Un registre vierge est un avertissement ; une déclaration fausse — un
# critère « passé » sans preuve, une source remplacée sans remplaçante, un
# incident fermé sans prévention — est une erreur dès que le profil est
# gouverné, un avertissement avant.
# ---------------------------------------------------------------------------


def _strict(profile: StandardProfile) -> bool:
    return profile.id in {"governed", "production"}


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _verify_acceptance_record(
    root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult
) -> None:
    """AG-QUA-003 : le livrable est accepté ou refusé par celui qui le reçoit, sur des critères prouvés."""
    rel_path = EVIDENCE_DIR / task_id / "acceptance-record.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    severity = "error" if _strict(profile) else "warning"
    template_row = "| AC-001 |  |  | à vérifier |"
    rows = [line for line in text.splitlines() if line.startswith("| AC-") and line.strip() != template_row]
    if not rows:
        _add_check(result, "acceptance.empty", "warning", "Acceptance record still holds only the template criterion.", path=rel_path)
    failed = False
    for line in rows:
        cells = _cells(line)
        if len(cells) < 4:
            _add_check(result, "acceptance.row_invalid", "warning", f"Criterion row is malformed: {line[:60]}", path=rel_path)
            continue
        criterion_id, _criterion, proof, status = cells[:4]
        if status == "passé" and not proof:
            _add_check(
                result, "acceptance.passed_without_evidence", "error",
                f"{criterion_id} is marked passé with no proof.", path=rel_path,
            )
        failed = failed or status == "échoué"
    decisions = [
        _cells(line) for line in text.splitlines()
        if line.startswith("| ") and _cells(line)[0] in {"accepté", "refusé", "ajustement demandé", "en attente"}
    ]
    for cells in decisions:
        decision, validator, date = [*cells, "", "", ""][:3]
        if decision == "en attente":
            _add_check(result, "acceptance.decision_pending", "warning", "No acceptance decision has been recorded yet.", path=rel_path)
        elif decision == "accepté":
            if not validator or not date:
                _add_check(
                    result, "acceptance.accepted_without_validator", "error",
                    "The deliverable is marked accepté without a named validator and a date.", path=rel_path,
                )
            if failed:
                _add_check(
                    result, "acceptance.accepted_with_failed_criterion", severity,
                    "The deliverable is marked accepté while a criterion is échoué.", path=rel_path,
                )


def _allowed_values(data: dict[str, Any], key: str) -> list[str]:
    raw = data.get("allowed")
    values = raw.get(key) if isinstance(raw, dict) else None
    return [str(v) for v in values] if isinstance(values, list) else []


def _entries(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = data.get(key)
    return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []


def _verify_retention_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-RET-001, -003, -004, -005 : destination de chaque artefact, remplaçante, nettoyages, purges suivies."""
    rel_path = STANDARD_DIR / "retention-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    severity = "error" if _strict(profile) else "warning"
    statuses = _allowed_values(data, "statuses")
    artifacts = _entries(data, "artifacts")
    if not artifacts:
        _add_check(result, "retention.no_artifact", "warning", "Retention registry lists no produced artifact.", path=rel_path)
    for entry in artifacts:
        rid = str(entry.get("id", "?"))
        retention = str(entry.get("retention") or "")
        if not retention:
            _add_check(result, "retention.artifact_retention_missing", severity, f"{rid} has no destination (retention).", path=rel_path)
        elif retention != "durable" and not entry.get("expires_at"):
            _add_check(result, "retention.expiry_missing", "warning", f"{rid} is {retention!r} without expires_at.", path=rel_path)
        if "indexable" not in entry:
            _add_check(result, "retention.artifact_indexable_missing", "warning", f"{rid} does not say whether it is indexable.", path=rel_path)
        status = str(entry.get("status") or "")
        if statuses and status and status not in statuses:
            _add_check(result, "retention.artifact_status_unknown", "warning", f"{rid}: status {status!r} is not an allowed value.", path=rel_path)
        if status == "superseded" and not entry.get("superseded_by"):
            _add_check(
                result, "retention.superseded_without_replacement", "error",
                f"{rid} is superseded but names no replacement.", path=rel_path,
            )
    cleanups = _entries(data, "cleanup")
    if not cleanups:
        _add_check(result, "retention.no_cleanup", "warning", "No periodic cleanup has been recorded yet.", path=rel_path)
    for entry in cleanups:
        if not entry.get("next_review"):
            _add_check(result, "retention.cleanup_next_review_missing", severity, "A cleanup entry sets no next_review.", path=rel_path)
    for entry in _entries(data, "purges"):
        if not entry.get("incident_id"):
            _add_check(
                result, "retention.purge_without_incident", severity,
                f"Purge of {entry.get('target', '?')!r} is not tracked as an incident.", path=rel_path,
            )


def _verify_tool_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-TOL-001, -003, -005 : outils avec risque et permissions, contrats MCP, capture des erreurs."""
    rel_path = STANDARD_DIR / "tool-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    severity = "error" if _strict(profile) else "warning"
    risks = _allowed_values(data, "risks")
    permissions = _allowed_values(data, "permissions")
    tools = _entries(data, "tools")
    if not tools:
        _add_check(result, "toolreg.no_tool", "warning", "Tool registry lists no tool.", path=rel_path)
    for tool in tools:
        tid = str(tool.get("id", "?"))
        risk = str(tool.get("risk") or "")
        if not risk:
            _add_check(result, "toolreg.tool_risk_missing", severity, f"{tid} declares no risk.", path=rel_path)
        elif risks and risk not in risks:
            _add_check(result, "toolreg.tool_risk_unknown", "warning", f"{tid}: risk {risk!r} is not an allowed value.", path=rel_path)
        perms = tool.get("permissions")
        if not isinstance(perms, list) or not perms:
            _add_check(result, "toolreg.tool_permissions_missing", severity, f"{tid} declares no permissions.", path=rel_path)
        elif permissions and any(str(p) not in permissions for p in perms):
            _add_check(result, "toolreg.tool_permission_unknown", "warning", f"{tid} uses a permission outside the allowed set.", path=rel_path)
    for server in _entries(data, "mcp_servers"):
        sid = str(server.get("id", "?"))
        for key in ("owner", "scopes", "timeout_s", "logging"):
            if server.get(key) in (None, "", []):
                _add_check(result, f"toolreg.mcp_{key}_missing", severity, f"MCP server {sid} has no {key}.", path=rel_path)
    capture = data.get("error_capture")
    if not isinstance(capture, dict) or not capture.get("policy"):
        _add_check(result, "toolreg.error_capture_missing", "error", "Tool registry does not say how tool errors are captured.", path=rel_path)
    else:
        policies = _allowed_values(data, "policies")
        if policies and str(capture.get("policy")) not in policies:
            _add_check(result, "toolreg.error_capture_policy_unknown", "error", f"error_capture.policy {capture.get('policy')!r} is not evidence or incident.", path=rel_path)


def _verify_incident_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-INC-001, -002, -003 : containment déclaré, incidents complets, récurrences capitalisées."""
    rel_path = STANDARD_DIR / "incident-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    severity = "error" if _strict(profile) else "warning"
    containment = data.get("containment")
    if not isinstance(containment, dict):
        _add_check(result, "incidents.containment_policy_missing", "error", "Incident registry declares no containment policy.", path=rel_path)
    else:
        actions = _allowed_values(data, "on_critical")
        action = str(containment.get("on_critical") or "")
        if not action or (actions and action not in actions):
            _add_check(result, "incidents.containment_action_unknown", "error", f"containment.on_critical {action!r} must be stop or reduce_autonomy.", path=rel_path)
        if not containment.get("critical_severities"):
            _add_check(result, "incidents.containment_severities_missing", "error", "containment.critical_severities is empty: nothing would ever stop.", path=rel_path)
        if not containment.get("escalate_to"):
            _add_check(result, "incidents.containment_escalation_missing", "warning", "containment.escalate_to names nobody.", path=rel_path)
    severities = _allowed_values(data, "severities")
    statuses = _allowed_values(data, "statuses")
    for incident in _entries(data, "incidents"):
        iid = str(incident.get("id", "?"))
        status = str(incident.get("status") or "")
        if severities and str(incident.get("severity") or "") not in severities:
            _add_check(result, "incidents.severity_unknown", "warning", f"{iid}: severity is not an allowed value.", path=rel_path)
        if statuses and status not in statuses:
            _add_check(result, "incidents.status_unknown", "warning", f"{iid}: status {status!r} is not an allowed value.", path=rel_path)
        closed = status in {"corrigé", "fermé"}
        for key in ("containment", "correction", "memory_purge", "prevention"):
            if key != "containment" and not closed:
                continue
            if not incident.get(key):
                _add_check(result, f"incidents.{key}_missing", severity, f"{iid} ({status}) has no {key}.", path=rel_path)
        if incident.get("recurrence_of") and not incident.get("feeds"):
            _add_check(
                result, "incidents.recurrence_without_feedback", severity,
                f"{iid} recurs from {incident.get('recurrence_of')} but feeds no eval, hook or governance rule.", path=rel_path,
            )


def _verify_risk_control_matrix(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-AUD-003, AG-QUA-005 : chaque risque a ses contrôles et sa preuve."""
    rel_path = STANDARD_DIR / "risk-control-matrix.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    severity = "error" if _strict(profile) else "warning"
    risks = _entries(data, "risks")
    if not risks:
        _add_check(result, "riskmatrix.none", severity, "Risk-control matrix names no risk.", path=rel_path)
        return
    likelihoods = _allowed_values(data, "likelihoods")
    impacts = _allowed_values(data, "impacts")
    statuses = _allowed_values(data, "statuses")
    unowned = 0
    for risk in risks:
        rid = str(risk.get("id", "?"))
        controls = risk.get("controls")
        if not isinstance(controls, list) or not controls:
            _add_check(result, "riskmatrix.controls_missing", severity, f"{rid} is mitigated by no control.", path=rel_path)
        if not risk.get("evidence"):
            _add_check(result, "riskmatrix.evidence_missing", severity, f"{rid} names no evidence that its controls hold.", path=rel_path)
        if (likelihoods and str(risk.get("likelihood") or "") not in likelihoods) or (impacts and str(risk.get("impact") or "") not in impacts):
            _add_check(result, "riskmatrix.level_unknown", "warning", f"{rid}: likelihood or impact is not an allowed value.", path=rel_path)
        if statuses and str(risk.get("status") or "") not in statuses:
            _add_check(result, "riskmatrix.status_unknown", "warning", f"{rid}: status is not an allowed value.", path=rel_path)
        unowned += not risk.get("owner")
    if unowned:
        _add_check(result, "riskmatrix.owner_missing", "warning", f"{unowned} risk(s) have no owner.", path=rel_path)


def _verify_capability_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-DYN-001 à -005 : gap nommé, durabilité classée, expiration ou promotion justifiée."""
    rel_path = STANDARD_DIR / "capability-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    severity = "error" if _strict(profile) else "warning"
    kinds = _allowed_values(data, "kinds")
    durabilities = _allowed_values(data, "durabilities")
    statuses = _allowed_values(data, "statuses")
    for cap in _entries(data, "capabilities"):
        cid = str(cap.get("id", "?"))
        if not cap.get("gap"):
            _add_check(result, "capabilities.gap_missing", severity, f"{cid} answers no identified gap.", path=rel_path)
        durability = str(cap.get("durability") or "")
        if durability not in (durabilities or ["ephemeral", "durable"]):
            _add_check(result, "capabilities.durability_invalid", "error", f"{cid}: durability {durability!r} must be ephemeral or durable.", path=rel_path)
        if durability == "ephemeral" and not cap.get("expires_at"):
            _add_check(result, "capabilities.ephemeral_without_expiry", severity, f"{cid} is ephemeral with no expiry or purge rule.", path=rel_path)
        raw_promotion = cap.get("promotion")
        promotion: dict[str, Any] = raw_promotion if isinstance(raw_promotion, dict) else {}
        if durability == "durable" and cap.get("promoted_from") and not all(promotion.get(k) for k in ("usage", "value", "validation")):
            _add_check(result, "capabilities.promotion_unjustified", severity, f"{cid} was promoted without usage, value and validation.", path=rel_path)
        if kinds and str(cap.get("kind") or "") not in kinds:
            _add_check(result, "capabilities.kind_unknown", "warning", f"{cid}: kind is not an allowed value.", path=rel_path)
        if statuses and str(cap.get("status") or "") not in statuses:
            _add_check(result, "capabilities.status_unknown", "warning", f"{cid}: status is not an allowed value.", path=rel_path)


def _verify_wip_limits(root: Path, result: StandardVerificationResult) -> None:
    """AG-ORC-004 : le WIP est borné et rien n'est délégué sans task envelope."""
    rel_path = STANDARD_DIR / "orchestration-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    wip = data.get("wip")
    if not isinstance(wip, dict):
        _add_check(result, "orchestration.wip_missing", "warning", "Orchestration policy sets no WIP limit (wip:).", path=rel_path)
        return
    for key in ("limit_per_role", "limit_global"):
        value = wip.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            _add_check(result, "orchestration.wip_limit_invalid", "error", f"wip.{key} must be a positive integer, got {value!r}.", path=rel_path)
    if str(wip.get("open_delegation") or "") not in {"forbidden", "envelope_required"}:
        _add_check(
            result, "orchestration.wip_open_delegation_invalid", "error",
            "wip.open_delegation must be forbidden or envelope_required: an open delegation has no task envelope.", path=rel_path,
        )


def _verify_compliance_traceability(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-AUD-001 : la déclaration de conformité cite la matrice et un verdict."""
    rel_path = STANDARD_DIR / "compliance-declaration.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    if "## Traceability" not in text:
        _add_check(result, "compliance.traceability_missing", "warning", "Compliance declaration has no Traceability section.", path=rel_path)
    elif _strict(profile) and "- Last verdict:\n" in text:
        _add_check(result, "compliance.verdict_missing", "warning", "Compliance declaration records no verification verdict.", path=rel_path)
