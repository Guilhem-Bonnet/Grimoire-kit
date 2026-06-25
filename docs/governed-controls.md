# Référence des contrôles gouvernés

> Page de référence générée depuis `framework/agentic-standard/templates/pattern-catalog.yaml`
> (source unique). Régénérer via `python docs/gen-governed-controls.py`.

**36 patterns gouvernés** répartis sur 11 catégories. Chaque pattern pose un
artefact déclaratif (`_grimoire/standard/*.yaml`) vérifié *fail-closed* par
`grimoire standard verify` / `audit` / `score` / `gate`. Le profil minimal indique à partir
de quelle maturité (`starter → controlled → orchestrated → governed → production`) le pattern
devient pertinent.

## Context

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `advanced-context-orchestrator` | orchestrated | Build deterministic, budgeted, redacted context bundles. | `context-contract.yaml`, `context-bundle.yaml` | `context.budget_invalid`, `context.redaction_required` |
| `context-compression-gate` | orchestrated | Allow context compression only if provenance, constraints, tool atomicity, and evidence remain verifiable. | `compression-gate.yaml` | `compression.provenance_dropped`, `compression.atomicity_dropped` |

## Memory

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `code-graph-projection` | orchestrated | Project code, tasks and evidence into a verifiable graph (TOUCHES_CODE/COVERS_CODE/MEMORY_FOR) for traceable recall. | `memory-policy.yaml` | `memory.graph_projection_unverified` |
| `governed-memory-policy` | orchestrated | Separate session, task, project, organization, semantic, episodic, and external cache memories. | `memory-policy.yaml` | `memory.required_types_missing` |
| `memory-integrity-validator` | orchestrated | Validate provenance, drift, poisoning, and expiry of promoted memories before recall. | `memory-integrity.yaml` | `integrity.provenance_unchecked`, `integrity.poisoning_unchecked` |
| `redis-hot-memory-soft-gate` | governed | Use Redis for TTL-bound session state, leases and transient events without making it a durable source of truth. | `memory-policy.yaml`, `evidence-pack.md` | `memory.hot_memory_partial` |

## Knowledge

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `doc-to-graph-pipeline` | production | Extract sourced relations and contradictions into a documentary graph with provenance. | `doc-graph-pipeline.yaml` | `docgraph.sources_missing`, `docgraph.relations_disabled` |
| `governed-knowledge-indexing` | orchestrated | Index normative documents and patterns and bind each governed check back to its normative source. | `knowledge-source-registry.yaml` | `knowledge.source_unindexed` |

## Orchestration

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `flow-dsl-minimal` | production | Tool-neutral flow manifest: ordered steps, exportable diagram, determinism flag. | `flow-dsl-manifest.yaml` | `flowdsl.steps_missing`, `flowdsl.export_undeclared` |
| `governed-agent-orchestration` | orchestrated | Declare agent roles, handoffs, escalation and fallback so multi-agent routing cannot drift undeclared. | `orchestration-policy.yaml` | `orchestration.role_undeclared`, `orchestration.handoff_unverified` |
| `skill-classification-matrix` | governed | Classify skills as core templates, project packs or incubator assets before promotion. | `hook-registry.yaml`, `pattern-catalog.yaml` | `skills.classification_missing` |

## Workflow

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `evidence-gated-fsm` | governed | Block lifecycle transitions unless evidence is present. | `evidence-gates.yaml`, `evidence-pack.md` | `gates.transitions_missing`, `evidence.pending_gate` |
| `mission-evidence-ledger` | governed | Bind missions, tasks and verification verdicts into an append-only evidence ledger that gates release. | `evidence-gates.yaml`, `evidence-pack.md` | `ledger.mission_unlinked` |
| `workflow-state-manifest` | starter | Declare a durable mission state machine (states/guards/interrupts); execution delegated to LangGraph/Conductor. | `workflow-state-manifest.yaml` | `wsm.states_missing`, `wsm.transition_invalid` |

## Provider

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `llm-cost-registry` | production | Track LLM cost per model/provider and report session reliability SLOs (CrashRate / UnhealthyRate). | `cost-registry.yaml` | `cost.no_pricing`, `cost.no_slo` |
| `provider-cost-slo` | production | Budget provider cost and declare SLOs so production routing reports overruns and latency breaches. | `llm-provider-registry.yaml` | `provider.cost_unbudgeted`, `provider.slo_undeclared` |
| `provider-routing-contract` | controlled | Route provider calls through declared capability and data-policy constraints. | `llm-provider-registry.yaml` | `providers.default_unknown`, `providers.routing_policy_weak` |

## Security

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `agent-privilege-boundary` | governed | Separate controller/agent privileges and scrub infrastructure tokens before agent spawn (fail closed). | `privilege-boundary.yaml` | `privilege.scrub_disabled`, `privilege.infra_token_exposed` |
| `guardrail-contract` | controlled | Version input/output/tool/model guardrails with explicit modes and violation actions; fail closed. | `guardrail-contract.yaml` | `guardrail.unversioned`, `guardrail.no_violation_action` |
| `prompt-injection-firewall` | controlled | Isolate external/untrusted content so it cannot override control instructions. | `prompt-firewall.yaml` | `firewall.isolation_disabled`, `firewall.override_allowed` |
| `tool-blast-radius-limiter` | starter | Bound each tool/task blast radius: writable paths, network egress, environment, cost, and production reach; fail closed. | `blast-radius-policy.yaml` | `tools.blast_radius_undeclared`, `tools.blast_radius_production_allow` |
| `tool-mediation-gate` | governed | Mediate tool/MCP calls through a trust gate aligned with OWASP agentic threats before execution. | `hook-registry.yaml`, `rule-packs.yaml` | `tools.unmediated_call`, `tools.threat_unmapped` |
| `workspace-isolation` | governed | Declare writable roots, network egress and env passthrough; fail closed. | `workspace-isolation.yaml` | `workspace.network_open`, `workspace.writable_unbounded` |

## Governance

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `cluster-action-dry-run` | production | High-risk cluster actions require dry-run, rollback proof and approval before execution. | `cluster-action-policy.yaml` | `cluster.dry_run_required`, `cluster.rollback_required` |
| `decision-council-gate` | governed | Escalate critical/high-uncertainty decisions to a quorum with veto, budget cap, and recorded disagreements. | `decision-council.yaml` | `council.quorum_too_low`, `council.no_veto` |
| `governed-hook-gateway` | governed | Route hooks through a policy gateway so tool mediation, traces and evidence gates stay auditable. | `hook-registry.yaml`, `rule-packs.yaml` | `hooks.gateway_missing`, `hooks.destructive_bypass` |
| `merge-lane-fault-classifier` | governed | Classify merge/review failures as transient (retryable) or hard (escalate) before any automatic retry. | `merge-lane.yaml` | `merge.classes_incomplete`, `merge.hard_not_escalated` |
| `policy-by-environment` | governed | Declare what changes across local/CI/staging/production; production fails closed. | `environment-policy.yaml` | `env.environments_missing`, `env.production_unguarded` |
| `remote-hygiene-guard` | controlled | Detect stale refs, oversized branch sets, and unreachable remotes before auditing source. | `remote-hygiene.yaml` | `remote.stale_check_disabled`, `remote.reachability_unchecked` |

## Quality

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `browser-tool-contract` | governed | Require DOM/screenshot/console logs from browser tools and bound reachable domains. | `browser-tool-contract.yaml` | `browser.dom_required`, `browser.screenshot_required` |
| `visual-evidence-gate` | governed | Require DOM, screenshot, and journey proof when a delivery touches UI/UX. | `visual-evidence.yaml` | `visual.missing_artifact_kinds`, `visual.no_triggers` |

## Runtime

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `k8s-agent-manifest` | production | Declarative K8s agent/sandbox contract (CRD/limits/network/SA/OTel); native provider delegated to kagent/client-go. | `k8s-agent-manifest.yaml` | `k8s.resource_limits_missing`, `k8s.telemetry_missing` |
| `runtime-journal` | governed | Record context, decision, hook, gate, and score events for auditability. | `runtime-journal.jsonl` | `events.invalid_line` |
| `runtime-provider-contract` | orchestrated | Uniform lifecycle/resources/logs/cleanup contract so runtimes are interchangeable. | `runtime-provider-contract.yaml` | `runtime.lifecycle_incomplete`, `runtime.logs_undeclared` |

## Observability

| Pattern | Profil min | Intention | Artefact | Checks clés |
|---|---|---|---|---|
| `governed-observability-cockpit` | governed | Generate a reproducible cockpit from governed artifacts and runtime events without making the cockpit authoritative. | `observability-policy.yaml`, `runtime-journal.jsonl`, `cockpit-report.md`, `evidence-pack.md` | `observability.cockpit_mutation`, `observability.input_undeclared`, `observability.secret_export` |
| `prompt-version-observability` | controlled | Track prompt/skill/provider/model versions linked to evals for regression attribution. | `prompt-version-log.yaml` | `promptver.tracking_incomplete`, `promptver.evals_unlinked` |

