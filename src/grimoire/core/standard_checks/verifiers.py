"""Les vérificateurs du standard agentique.

Chaque fonction lit un artefact et pousse ses constats dans le
StandardVerificationResult via _add_check. Les identifiants qu'elles
émettent sont déclarés dans grimoire.core.standard_checks.registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import (
    BOARD_STATES,
    KNOWN_HOOK_ACTIONS,
    KNOWN_HOOK_PHASES,
    MEMORY_OS_LEGACY_VECTOR_SOURCES,
    MEMORY_OS_REQUIRED_PROMOTION_GATES,
    MEMORY_OS_REQUIRED_RUNTIME_COMMANDS,
    MEMORY_OS_REQUIRED_TARGET,
    REQUIRED_DECISION_TYPES,
    REQUIRED_MEMORY_TYPES,
    StandardProfile,
    StandardVerificationResult,
    _add_check,
    _is_inside_root,
    _load_yaml_file,
    _memory_os_severity,
    _require_keys,
    _text_file,
)
from grimoire.core.standard_checks.controls import (
    _verify_blast_radius_policy,
    _verify_browser_tool_contract,
    _verify_cluster_action_policy,
    _verify_compression_gate,
    _verify_cost_registry,
    _verify_decision_council,
    _verify_doc_graph_pipeline,
    _verify_environment_policy,
    _verify_flow_dsl_manifest,
    _verify_guardrail_contract,
    _verify_memory_integrity,
    _verify_merge_lane,
    _verify_privilege_boundary,
    _verify_prompt_firewall,
    _verify_prompt_version_log,
    _verify_remote_hygiene,
    _verify_runtime_provider_contract,
    _verify_visual_evidence,
    _verify_workflow_state_manifest,
    _verify_workspace_isolation,
)
from grimoire.core.standard_generation import (
    EVIDENCE_DIR,
    STANDARD_DIR,
    STANDARD_PROFILE_FILE,
    normalize_task_id,
)


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
        _add_check(
            result,
            "mission.profile_missing",
            "error",
            "Mission brief does not declare the selected profile.",
            path=rel_path,
        )
    if "processus-developpement-agentique/docs/norme-structure-agentique.md" not in text:
        _add_check(
            result,
            "mission.upstream_missing",
            "warning",
            "Mission brief does not reference the upstream standard.",
            path=rel_path,
        )


def _verify_provider_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "llm-provider-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        _add_check(
            result, "providers.none", "error", "Provider registry must declare at least one provider.", path=rel_path
        )
        return

    provider_ids = {
        str(provider.get("id")) for provider in providers if isinstance(provider, dict) and provider.get("id")
    }
    enabled = [provider for provider in providers if isinstance(provider, dict) and provider.get("enabled") is True]
    enabled_ids = {str(provider.get("id")) for provider in enabled if provider.get("id")}
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
            _add_check(
                result,
                "providers.enabled_not_bool",
                "error",
                f"Provider {provider_id!r} enabled flag must be boolean.",
                path=rel_path,
            )
        if provider.get("enabled") is True:
            capabilities = provider.get("allowed_capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                _add_check(
                    result,
                    "providers.capabilities_missing",
                    "error",
                    f"Enabled provider {provider_id!r} has no capabilities.",
                    path=rel_path,
                )
            models = provider.get("default_models")
            if not isinstance(models, list) or not models:
                _add_check(
                    result,
                    "providers.models_missing",
                    "warning",
                    f"Enabled provider {provider_id!r} has no default models.",
                    path=rel_path,
                )
            data_policy = provider.get("data_policy")
            if not isinstance(data_policy, dict):
                _add_check(
                    result,
                    "providers.data_policy_missing",
                    "error",
                    f"Enabled provider {provider_id!r} has no data policy.",
                    path=rel_path,
                )
            else:
                forbidden = data_policy.get("forbidden_data_classes")
                if provider.get("provider_type") == "hosted" and (not isinstance(forbidden, list) or not forbidden):
                    _add_check(
                        result,
                        "providers.hosted_forbidden_missing",
                        "error",
                        f"Hosted provider {provider_id!r} has no forbidden data classes.",
                        path=rel_path,
                    )
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
        _add_check(
            result, "providers.routing_missing", "warning", "Provider registry has no routing policy.", path=rel_path
        )
    else:
        default_provider = str(routing.get("default_provider", ""))
        if enabled and default_provider not in provider_ids:
            _add_check(
                result,
                "providers.default_unknown",
                "error",
                f"Default provider {default_provider!r} is not declared.",
                path=rel_path,
            )
        elif enabled and default_provider not in enabled_ids:
            _add_check(
                result,
                "providers.default_not_enabled",
                "error",
                f"Default provider {default_provider!r} is not enabled.",
                path=rel_path,
            )
        fallback_chain = routing.get("default_fallback_chain", [])
        if isinstance(fallback_chain, list):
            unknown = [str(candidate) for candidate in fallback_chain if str(candidate) not in provider_ids]
            if unknown:
                _add_check(
                    result,
                    "providers.routing_unknown_fallback",
                    "error",
                    f"Routing references unknown fallback(s): {', '.join(unknown)}.",
                    path=rel_path,
                )
        if routing.get("require_capability_match") is not True or routing.get("require_data_policy_match") is not True:
            _add_check(
                result,
                "providers.routing_policy_weak",
                "warning",
                "Routing should require capability and data-policy matches.",
                path=rel_path,
            )


def _verify_knowledge_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "knowledge-source-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    rules = data.get("rules")
    if not isinstance(rules, dict):
        _add_check(
            result,
            "knowledge.rules_missing",
            "error",
            "Knowledge registry must declare separation rules.",
            path=rel_path,
        )
    else:
        for key in ("knowledge_is_not_memory", "knowledge_is_not_session_context", "explicit_source_of_truth_required"):
            if rules.get(key) is not True:
                _add_check(result, f"knowledge.{key}", "error", f"Rule {key} must be true.", path=rel_path)

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        _add_check(
            result, "knowledge.sources_missing", "warning", "No external knowledge source is declared.", path=rel_path
        )
        return

    real_sources = 0
    for source in sources:
        if not isinstance(source, dict):
            _add_check(
                result, "knowledge.invalid_source", "error", "Knowledge source entries must be mappings.", path=rel_path
            )
            continue
        source_id = str(source.get("id", "")).strip()
        locator = str(source.get("locator", "")).strip()
        source_type = str(source.get("type", "")).strip()
        if source_id and locator and "|" not in source_type:
            real_sources += 1
        if source.get("enabled") is True and not locator:
            _add_check(
                result,
                "knowledge.locator_missing",
                "warning",
                "Enabled knowledge source has no locator.",
                path=rel_path,
            )
        local_locator = locator.startswith((".", "/", "~"))
        if source.get("enabled") is True and locator and source_type == "folder":
            local_locator = True
        if source.get("enabled") is True and locator and local_locator:
            locator_path = (root / locator).resolve()
            if not _is_inside_root(root, locator_path):
                _add_check(
                    result,
                    "knowledge.locator_outside_root",
                    "error",
                    f"Knowledge source {source_id!r} locator must stay within project root: {locator}",
                    path=rel_path,
                )
            elif not locator_path.exists():
                _add_check(
                    result,
                    "knowledge.locator_not_found",
                    "warning",
                    f"Knowledge source {source_id!r} locator does not exist: {locator}",
                    path=rel_path,
                )
        trust = source.get("trust")
        if source.get("enabled") is True and not isinstance(trust, dict):
            _add_check(
                result,
                "knowledge.trust_missing",
                "error",
                f"Knowledge source {source_id!r} has no trust block.",
                path=rel_path,
            )
        elif isinstance(trust, dict) and not trust.get("level"):
            _add_check(
                result,
                "knowledge.trust_level_missing",
                "warning",
                f"Knowledge source {source_id!r} has no trust level.",
                path=rel_path,
            )
        elif (
            isinstance(trust, dict)
            and trust.get("source_of_truth") is True
            and trust.get("level") not in {"high", "authoritative"}
        ):
            _add_check(
                result,
                "knowledge.truth_low_trust",
                "warning",
                f"Source of truth {source_id!r} should have high or authoritative trust.",
                path=rel_path,
            )
        evidence = source.get("evidence")
        if source.get("enabled") is True and isinstance(evidence, dict):
            manifest = str(evidence.get("index_manifest", "")).strip()
            if manifest and manifest != "planned":
                manifest_path = root / manifest
                if not _is_inside_root(root, manifest_path):
                    _add_check(
                        result,
                        "knowledge.index_manifest_outside_root",
                        "error",
                        f"Knowledge source {source_id!r} index manifest must stay within project root: {manifest}",
                        path=rel_path,
                    )
                elif not manifest_path.exists():
                    _add_check(
                        result,
                        "knowledge.index_manifest_missing",
                        "warning",
                        f"Knowledge source {source_id!r} index manifest is missing: {manifest}",
                        path=rel_path,
                    )

    if profile.id in {"orchestrated", "governed", "production"} and real_sources == 0:
        _add_check(
            result,
            "knowledge.no_real_source",
            "warning",
            "Profile expects indexed external knowledge, but only placeholder sources are present.",
            path=rel_path,
        )


def _verify_task_envelope(
    root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult
) -> None:
    rel_path = EVIDENCE_DIR / task_id / "task-envelope.md"
    text = _text_file(root, rel_path)
    if not text:
        return

    strict_profile = profile.id in {"governed", "production"}
    if "- Current state: `intake | planned | executing | validating | blocked | done`" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(
            result,
            "task.state_placeholder",
            severity,
            "Task envelope still contains the state placeholder.",
            path=rel_path,
        )
    if "|  |  |  |  |  |" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(
            result,
            "task.context_placeholder",
            severity,
            "Context orchestration table has no concrete selection.",
            path=rel_path,
        )
    if "|  | read-only |  |  |" in text:
        severity = "error" if strict_profile else "warning"
        _add_check(
            result, "task.tool_boundary_placeholder", severity, "Tool boundary is not concretely scoped.", path=rel_path
        )
    if "pending |" in text.lower() and strict_profile:
        _add_check(
            result,
            "task.pending_gate",
            "error",
            "Governed and production profiles cannot keep pending task gates.",
            path=rel_path,
        )


def _verify_evidence_pack(root: Path, task_id: str, result: StandardVerificationResult) -> None:
    rel_path = EVIDENCE_DIR / task_id / "evidence-pack.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    if "pending" in text.lower():
        _add_check(
            result, "evidence.pending_gate", "warning", "Evidence pack still contains pending gates.", path=rel_path
        )
    if "- Outcome:\n" in text or "- Final state:\n" in text:
        _add_check(
            result,
            "evidence.summary_placeholder",
            "warning",
            "Evidence pack summary is still placeholder-only.",
            path=rel_path,
        )
    if "|  |  |  |  |" in text:
        _add_check(
            result,
            "evidence.inventory_placeholder",
            "warning",
            "Evidence inventory has no concrete evidence rows.",
            path=rel_path,
        )


def _verify_claim_ledger(
    root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult
) -> None:
    """AG-QUA-002 : une affirmation critique sans preuve reste une hypothèse.

    Un registre encore vierge est un avertissement : il attend d'être rempli.
    Ce qui est une erreur, c'est une affirmation dite prouvée sans preuve, ou —
    en profil governed et production — une affirmation utilisée alors qu'elle
    n'est pas prouvée, et une synthèse laissée vide.
    """
    rel_path = EVIDENCE_DIR / task_id / "claim-ledger.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    strict = profile.id in {"governed", "production"}
    template_row = "| CL-001 |  | fait |  | hypothèse | faible | vérifier |"
    rows = [line for line in text.splitlines() if line.startswith("| CL-") and line.strip() != template_row]
    if not rows:
        _add_check(result, "claims.empty", "warning", "Claim ledger still holds only the template row.", path=rel_path)
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            _add_check(result, "claims.row_invalid", "warning", f"Claim row is malformed: {line[:60]}", path=rel_path)
            continue
        claim_id, _claim, _kind, proof, status, _confidence, decision = cells[:7]
        if status == "prouvé" and not proof:
            _add_check(
                result, "claims.proved_without_evidence", "error",
                f"{claim_id} is marked prouvé with no source or evidence.", path=rel_path,
            )
        if decision == "utiliser" and status != "prouvé":
            _add_check(
                result, "claims.used_unproved", "error" if strict else "warning",
                f"{claim_id} is used while its status is {status}.", path=rel_path,
            )
    if strict and rows and "| Affirmations bloquantes non prouvées |  |" in text:
        _add_check(result, "claims.summary_placeholder", "error", "Claim ledger summary is still empty.", path=rel_path)


def _verify_runtime_surface_registry(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    """AG-TOL-007 et AG-RET-006 : chaque surface runtime a un owner, un mode, un statut, une rétention."""
    rel_path = STANDARD_DIR / "runtime-surface-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    strict = profile.id in {"governed", "production"}
    raw_allowed = data.get("allowed")
    allowed: dict[str, Any] = raw_allowed if isinstance(raw_allowed, dict) else {}
    controls = data.get("control_surfaces") or []
    outputs = data.get("output_surfaces") or []
    if not controls:
        _add_check(
            result, "surfaces.no_control_surface", "warning",
            "No control surface is registered: hooks, agents and policies run unowned.", path=rel_path,
        )
    for entry in controls if isinstance(controls, list) else []:
        if not isinstance(entry, dict):
            _add_check(result, "surfaces.control_invalid", "warning", "A control surface entry is not a mapping.", path=rel_path)
            continue
        sid = str(entry.get("id", "?"))
        for key in ("surface", "owner", "mode", "status"):
            if not entry.get(key):
                _add_check(
                    result, f"surfaces.control_{key}_missing", "error" if strict else "warning",
                    f"{sid} has no {key}.", path=rel_path,
                )
        for key, allowed_key in (("type", "types"), ("mode", "modes"), ("risk", "risks"), ("status", "statuses")):
            value = entry.get(key)
            values = allowed.get(allowed_key)
            if value and isinstance(values, list) and value not in values:
                _add_check(result, f"surfaces.control_{key}_unknown", "warning", f"{sid}: {key} {value!r} is not an allowed value.", path=rel_path)
    for entry in outputs if isinstance(outputs, list) else []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id", "?"))
        if not entry.get("retention"):
            _add_check(
                result, "surfaces.output_retention_missing", "error" if strict else "warning",
                f"{sid} declares no retention.", path=rel_path,
            )
        if "indexable" not in entry:
            _add_check(result, "surfaces.output_indexable_missing", "warning", f"{sid} does not say whether it is indexable.", path=rel_path)


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


def _verify_task_board(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "task-board.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    states = data.get("states")
    if not isinstance(states, list) or not set(BOARD_STATES) <= {str(state) for state in states}:
        _add_check(
            result,
            "board.states_incomplete",
            "error",
            "Task board must declare the normative lifecycle states.",
            path=rel_path,
        )
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        _add_check(result, "board.tasks_missing", "error", "Task board must declare a tasks list.", path=rel_path)
        return
    if not tasks:
        _add_check(result, "board.no_tasks", "warning", "Task board has no declared tasks yet.", path=rel_path)
        return
    for task in tasks:
        if not isinstance(task, dict):
            _add_check(result, "board.task_invalid", "error", "Task board entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="board.task",
            data=task,
            keys=("task_id", "title", "status", "acceptance_criteria", "evidence_pack_ref"),
        )
        task_id = str(task.get("task_id", "")).strip()
        if task_id:
            try:
                normalize_task_id(task_id)
            except ValueError as exc:
                _add_check(result, "board.task_id_invalid", "error", str(exc), path=rel_path)
        status = str(task.get("status", "")).strip()
        if status and status not in BOARD_STATES:
            _add_check(
                result,
                "board.status_invalid",
                "error",
                f"Task {task_id!r} has invalid status {status!r}.",
                path=rel_path,
            )
        if status == "blocked" and not task.get("blockers"):
            _add_check(
                result,
                "board.blocker_reason_missing",
                "error",
                f"Task {task_id!r} is blocked without blocker details.",
                path=rel_path,
            )
        if (
            profile.id in {"governed", "production"}
            and status in {"accepted", "released"}
            and not task.get("decision_trace_ref")
        ):
            _add_check(
                result,
                "board.decision_trace_missing",
                "error",
                f"Task {task_id!r} requires a decision trace.",
                path=rel_path,
            )
        for ref_key in ("context_bundle_ref", "decision_trace_ref", "evidence_pack_ref", "remediation_ref"):
            ref = str(task.get(ref_key, "")).strip()
            if ref and not _is_inside_root(root, root / ref):
                _add_check(
                    result,
                    f"board.{ref_key}_outside_root",
                    "error",
                    f"Task {task_id!r} {ref_key} escapes project root.",
                    path=rel_path,
                )


def _verify_memory_os_contract(
    memory_policy: Mapping[str, Any],
    *,
    profile: StandardProfile,
    result: StandardVerificationResult,
    path: Path,
) -> None:
    memory_os = memory_policy.get("memory_os")
    if not isinstance(memory_os, dict):
        _add_check(
            result,
            "memory.os_contract_missing",
            _memory_os_severity(profile),
            "Memory policy should declare a Memory OS contract for Redis, Weaviate, Neo4j, SQLite, and Qdrant migration.",
            path=path,
        )
        return

    target = memory_os.get("target")
    if not isinstance(target, dict):
        _add_check(
            result,
            "memory.os_target_missing",
            _memory_os_severity(profile),
            "Memory OS contract must declare target backends.",
            path=path,
        )
        return

    for key, expected in MEMORY_OS_REQUIRED_TARGET.items():
        actual = str(target.get(key, "")).strip()
        if actual != expected:
            _add_check(
                result,
                f"memory.os_{key}_target",
                _memory_os_severity(profile),
                f"Memory OS target {key!r} should be {expected!r}, got {actual!r}.",
                path=path,
            )

    legacy_sources = target.get("legacy_vector_sources")
    declared_legacy = {str(source) for source in legacy_sources} if isinstance(legacy_sources, list) else set()
    missing_legacy = sorted(MEMORY_OS_LEGACY_VECTOR_SOURCES - declared_legacy)
    if missing_legacy:
        _add_check(
            result,
            "memory.os_legacy_sources_missing",
            "warning",
            f"Memory OS should keep Qdrant only as legacy/migration source(s): {', '.join(missing_legacy)}.",
            path=path,
        )

    promotion_gates = memory_os.get("promotion_gates")
    declared_gates = {str(gate) for gate in promotion_gates} if isinstance(promotion_gates, list) else set()
    missing_gates = sorted(MEMORY_OS_REQUIRED_PROMOTION_GATES - declared_gates)
    if missing_gates:
        _add_check(
            result,
            "memory.os_promotion_gates_missing",
            _memory_os_severity(profile),
            f"Memory OS promotion gates missing: {', '.join(missing_gates)}.",
            path=path,
        )

    runtime_commands = memory_os.get("runtime_commands")
    declared_commands = {str(command) for command in runtime_commands} if isinstance(runtime_commands, list) else set()
    missing_commands = sorted(MEMORY_OS_REQUIRED_RUNTIME_COMMANDS - declared_commands)
    if missing_commands:
        _add_check(
            result,
            "memory.os_runtime_commands_missing",
            "warning",
            f"Memory OS runtime command evidence should include: {', '.join(missing_commands)}.",
            path=path,
        )


def _verify_memory_policy(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "memory-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    memory_types = data.get("memory_types")
    if not isinstance(memory_types, list) or not memory_types:
        _add_check(result, "memory.types_missing", "error", "Memory policy must declare memory_types.", path=rel_path)
        return
    declared = {str(entry.get("type")) for entry in memory_types if isinstance(entry, dict)}
    missing = sorted(REQUIRED_MEMORY_TYPES - declared)
    if missing:
        _add_check(
            result,
            "memory.required_types_missing",
            "error",
            f"Memory policy misses required type(s): {', '.join(missing)}.",
            path=rel_path,
        )
    for entry in memory_types:
        if not isinstance(entry, dict):
            _add_check(result, "memory.type_invalid", "error", "Memory type entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="memory",
            data=entry,
            keys=(
                "memory_id",
                "type",
                "scope",
                "read_policy",
                "write_policy",
                "retention",
                "freshness",
                "trust_level",
                "redaction_policy",
                "provider_compatibility",
                "allowed_context_uses",
            ),
        )
        if not isinstance(entry.get("provider_compatibility"), list):
            _add_check(
                result,
                "memory.provider_compatibility_invalid",
                "error",
                "Memory provider compatibility must be a list.",
                path=rel_path,
            )
        if not isinstance(entry.get("allowed_context_uses"), list):
            _add_check(
                result,
                "memory.context_uses_invalid",
                "error",
                "Memory allowed context uses must be a list.",
                path=rel_path,
            )
    _verify_memory_os_contract(data, profile=profile, result=result, path=rel_path)


def _verify_context_contract(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "context-contract.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    _require_keys(
        result,
        path=rel_path,
        check_prefix="context",
        data=data,
        keys=("inputs", "bundle_sections", "budget", "redaction", "checks"),
    )
    for key in ("inputs", "bundle_sections", "checks"):
        if not isinstance(data.get(key), list) or not data.get(key):
            _add_check(
                result,
                f"context.{key}_invalid",
                "error",
                f"Context contract {key} must be a non-empty list.",
                path=rel_path,
            )
    budget = data.get("budget")
    if not isinstance(budget, dict):
        _add_check(
            result, "context.budget_invalid", "error", "Context contract budget must be a mapping.", path=rel_path
        )
    redaction = data.get("redaction")
    if not isinstance(redaction, dict) or redaction.get("required") is not True:
        _add_check(
            result, "context.redaction_required", "error", "Context contract must require redaction.", path=rel_path
        )


def _verify_decision_graph(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "decision-graph.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    record_fields = data.get("required_decision_record")
    if not isinstance(record_fields, list) or not record_fields:
        _add_check(
            result,
            "decision.record_schema_missing",
            "error",
            "Decision graph must declare required_decision_record.",
            path=rel_path,
        )
    decision_types = data.get("decision_types")
    if not isinstance(decision_types, list) or not decision_types:
        _add_check(
            result, "decision.types_missing", "error", "Decision graph must declare decision_types.", path=rel_path
        )
        return
    declared = {str(entry.get("id")) for entry in decision_types if isinstance(entry, dict)}
    missing = sorted(REQUIRED_DECISION_TYPES - declared)
    if missing:
        _add_check(
            result,
            "decision.required_types_missing",
            "error",
            f"Decision graph misses required type(s): {', '.join(missing)}.",
            path=rel_path,
        )
    for entry in decision_types:
        if not isinstance(entry, dict):
            _add_check(
                result, "decision.type_invalid", "error", "Decision type entries must be mappings.", path=rel_path
            )
            continue
        _require_keys(result, path=rel_path, check_prefix="decision", data=entry, keys=("id", "inputs", "outputs"))


def _verify_rule_packs(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "rule-packs.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        _add_check(result, "rules.none", "error", "Rule packs must declare at least one rule.", path=rel_path)
        return
    for rule in rules:
        if not isinstance(rule, dict):
            _add_check(result, "rules.invalid", "error", "Rule entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="rules",
            data=rule,
            keys=(
                "id",
                "family",
                "source_normative",
                "severity",
                "phase",
                "condition",
                "action",
                "event",
                "remediation",
            ),
        )
        if str(rule.get("phase", "")) not in KNOWN_HOOK_PHASES:
            _add_check(
                result,
                "rules.unknown_phase",
                "error",
                f"Rule {rule.get('id')!r} uses unknown phase {rule.get('phase')!r}.",
                path=rel_path,
            )
        if str(rule.get("action", "")) not in KNOWN_HOOK_ACTIONS:
            _add_check(
                result,
                "rules.unknown_action",
                "error",
                f"Rule {rule.get('id')!r} uses unknown action {rule.get('action')!r}.",
                path=rel_path,
            )


def _verify_hook_registry(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "hook-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        _add_check(result, "hooks.none", "error", "Hook registry must declare at least one hook.", path=rel_path)
        return
    for hook in hooks:
        if not isinstance(hook, dict):
            _add_check(result, "hooks.invalid", "error", "Hook entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result, path=rel_path, check_prefix="hooks", data=hook, keys=("id", "phase", "action", "rule_ref", "reason")
        )
        if str(hook.get("phase", "")) not in KNOWN_HOOK_PHASES:
            _add_check(
                result,
                "hooks.unknown_phase",
                "error",
                f"Hook {hook.get('id')!r} uses unknown phase {hook.get('phase')!r}.",
                path=rel_path,
            )
        if str(hook.get("action", "")) not in KNOWN_HOOK_ACTIONS:
            _add_check(
                result,
                "hooks.unknown_action",
                "error",
                f"Hook {hook.get('id')!r} uses unknown action {hook.get('action')!r}.",
                path=rel_path,
            )


def _verify_orchestration_policy(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "orchestration-policy.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        _add_check(
            result, "orchestration.roles_missing", "error", "Orchestration policy must declare roles.", path=rel_path
        )
        return
    for role in roles:
        if not isinstance(role, dict):
            _add_check(result, "orchestration.role_invalid", "error", "Role entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="orchestration.role",
            data=role,
            keys=(
                "role_id",
                "persona_or_archetype",
                "responsibilities",
                "allowed_tools",
                "allowed_memory_types",
                "allowed_providers",
                "autonomy_level",
                "handoff_contracts",
                "escalation_triggers",
                "review_gates",
                "rollback_policy",
            ),
        )


def _verify_evidence_gates(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "evidence-gates.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    transitions = data.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        _add_check(
            result, "gates.transitions_missing", "error", "Evidence gates must declare transitions.", path=rel_path
        )
        return
    for transition in transitions:
        if not isinstance(transition, dict):
            _add_check(
                result,
                "gates.transition_invalid",
                "error",
                "Evidence gate transitions must be mappings.",
                path=rel_path,
            )
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="gates.transition",
            data=transition,
            keys=("id", "from", "to", "required_evidence"),
        )
        if transition.get("from") not in BOARD_STATES or transition.get("to") not in BOARD_STATES:
            _add_check(
                result,
                "gates.unknown_state",
                "error",
                f"Transition {transition.get('id')!r} references an unknown state.",
                path=rel_path,
            )
        if not isinstance(transition.get("required_evidence"), list) or not transition.get("required_evidence"):
            _add_check(
                result,
                "gates.required_evidence_missing",
                "error",
                f"Transition {transition.get('id')!r} has no required evidence.",
                path=rel_path,
            )


def _verify_pattern_catalog(root: Path, result: StandardVerificationResult) -> None:
    rel_path = STANDARD_DIR / "pattern-catalog.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        _add_check(
            result, "patterns.categories_missing", "error", "Pattern catalog must declare categories.", path=rel_path
        )
    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        _add_check(result, "patterns.none", "error", "Pattern catalog must declare executable patterns.", path=rel_path)
        return
    declared_categories = {str(category) for category in categories} if isinstance(categories, list) else set()
    for pattern in patterns:
        if not isinstance(pattern, dict):
            _add_check(result, "patterns.invalid", "error", "Pattern entries must be mappings.", path=rel_path)
            continue
        _require_keys(
            result,
            path=rel_path,
            check_prefix="patterns",
            data=pattern,
            keys=("id", "category", "maturity", "source_normative", "intent", "required_artifacts", "check_refs"),
        )
        category = str(pattern.get("category", ""))
        if declared_categories and category not in declared_categories:
            _add_check(
                result,
                "patterns.unknown_category",
                "error",
                f"Pattern {pattern.get('id')!r} uses unknown category {category!r}.",
                path=rel_path,
            )
        if not isinstance(pattern.get("required_artifacts"), list):
            _add_check(
                result,
                "patterns.required_artifacts_invalid",
                "error",
                f"Pattern {pattern.get('id')!r} required_artifacts must be a list.",
                path=rel_path,
            )


def run_verifiers(root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult) -> None:
    """Run every verifier, in the order the artifacts build on each other.

    Extracted from ``agentic_standard.verify_standard_profile``: that module
    is grandfathered by the code ratchet and may only shrink, and this list
    is the one thing every new artifact has to grow.
    """
    _verify_manifest(root, profile, task_id, result)
    _verify_mission_brief(root, profile, result)
    _verify_provider_registry(root, profile, result)
    _verify_knowledge_registry(root, profile, result)
    _verify_task_envelope(root, profile, task_id, result)
    _verify_evidence_pack(root, task_id, result)
    _verify_claim_ledger(root, profile, task_id, result)
    _verify_compliance_declaration(root, result)
    _verify_profile_specific_controls(root, profile, result)
    _verify_task_board(root, profile, result)
    _verify_memory_policy(root, profile, result)
    _verify_context_contract(root, result)
    _verify_decision_graph(root, result)
    _verify_rule_packs(root, result)
    _verify_hook_registry(root, result)
    _verify_runtime_surface_registry(root, profile, result)
    _verify_orchestration_policy(root, result)
    _verify_evidence_gates(root, result)
    _verify_pattern_catalog(root, result)
    _verify_blast_radius_policy(root, profile, result)
    _verify_privilege_boundary(root, profile, result)
    _verify_prompt_firewall(root, profile, result)
    _verify_remote_hygiene(root, result)
    _verify_decision_council(root, result)
    _verify_compression_gate(root, result)
    _verify_memory_integrity(root, result)
    _verify_merge_lane(root, result)
    _verify_cost_registry(root, result)
    _verify_guardrail_contract(root, result)
    _verify_visual_evidence(root, result)
    _verify_workspace_isolation(root, result)
    _verify_environment_policy(root, result)
    _verify_browser_tool_contract(root, result)
    _verify_runtime_provider_contract(root, result)
    _verify_prompt_version_log(root, result)
    _verify_cluster_action_policy(root, result)
    _verify_doc_graph_pipeline(root, result)
    _verify_flow_dsl_manifest(root, result)
    _verify_workflow_state_manifest(root, result)
