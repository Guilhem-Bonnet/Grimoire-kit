/**
 * dispatch-routing.ts — V2.5 pure model selection for a dispatch request.
 *
 * Maps (targetAgentId, complexity) → model id according to a routing
 * table supplied by the caller. The canonical source at runtime is
 * `_grimoire-runtime/_config/model-routing.yaml`, but loading YAML is an
 * I/O concern handled by the extension host; this module only cares
 * about the already-resolved table.
 *
 * The resolution honours the task-aware override grid from the
 * orchestrator: complexity=high always promotes the decision to the
 * `deep_reasoning` profile regardless of the agent default.
 */

import type { CardDispatchComplexity } from './kanban-dispatch';

export const ROUTING_PROFILES = [
  'deep_reasoning',
  'general_code',
  'writing_structured',
  'fast_iter',
  'local_coder'
] as const;

export type RoutingProfile = (typeof ROUTING_PROFILES)[number];

export interface RoutingProfileDefinition {
  primary: string;
  preferred: readonly string[];
}

export interface RoutingTable {
  /** Profile definitions (primary + fallback chain). */
  profiles: Readonly<Record<RoutingProfile, RoutingProfileDefinition>>;
  /** Default profile per agent id. */
  agentDefaults: Readonly<Record<string, RoutingProfile>>;
  /** Optional override applied when complexity hits a high bucket. */
  complexityOverride?: Readonly<Record<CardDispatchComplexity, RoutingProfile>>;
}

export interface DispatchRoutingInput {
  agentId: string;
  complexity: CardDispatchComplexity;
}

export interface DispatchRoutingDecision {
  agentId: string;
  complexity: CardDispatchComplexity;
  profile: RoutingProfile;
  primary: string;
  preferred: readonly string[];
  reason: string;
  usedComplexityOverride: boolean;
  usedFallback: boolean;
}

const FALLBACK_PROFILE: RoutingProfile = 'general_code';

/**
 * Compute the effective routing decision. Pure and deterministic.
 *
 *   - If `complexityOverride[complexity]` is defined, it wins and the
 *     reason documents it.
 *   - Otherwise the agent's default profile is used.
 *   - If the agent is unknown, the fallback profile is used and the
 *     decision is flagged (`usedFallback=true`) so callers can surface
 *     the event to the operator.
 */
export function resolveDispatchRouting(
  input: DispatchRoutingInput,
  table: RoutingTable
): DispatchRoutingDecision {
  const override = table.complexityOverride?.[input.complexity];
  const agentDefault = table.agentDefaults[input.agentId];
  const fallbackUsed = override === undefined && agentDefault === undefined;

  const profile: RoutingProfile = override ?? agentDefault ?? FALLBACK_PROFILE;
  const definition = table.profiles[profile];
  if (!definition) {
    return {
      agentId: input.agentId,
      complexity: input.complexity,
      profile: FALLBACK_PROFILE,
      primary: 'auto',
      preferred: [],
      reason: `routing table is missing profile '${profile}'; falling back to auto.`,
      usedComplexityOverride: override !== undefined,
      usedFallback: true
    };
  }

  let reason: string;
  if (override !== undefined) {
    reason = `complexity=${input.complexity} forces profile '${profile}' (override).`;
  } else if (agentDefault !== undefined) {
    reason = `agent '${input.agentId}' default profile '${profile}'.`;
  } else {
    reason = `agent '${input.agentId}' unknown; fallback profile '${profile}'.`;
  }

  return {
    agentId: input.agentId,
    complexity: input.complexity,
    profile,
    primary: definition.primary,
    preferred: definition.preferred,
    reason,
    usedComplexityOverride: override !== undefined,
    usedFallback: fallbackUsed
  };
}

/**
 * Minimal default routing table inlined for tests and UI previews. The
 * real runtime table is loaded from model-routing.yaml by the host.
 */
export const DEFAULT_ROUTING_TABLE: RoutingTable = Object.freeze({
  profiles: Object.freeze({
    deep_reasoning: {
      primary: 'auto',
      preferred: ['gpt-5.4', 'gpt-5.3-codex', 'claude-opus-4.6']
    },
    general_code: {
      primary: 'auto',
      preferred: ['gpt-5.3-codex', 'gpt-5-mini', 'claude-sonnet-4.6']
    },
    writing_structured: {
      primary: 'auto',
      preferred: ['gpt-5-mini', 'claude-sonnet-4.6']
    },
    fast_iter: {
      primary: 'auto',
      preferred: ['gpt-5.4-mini', 'claude-haiku-4.5']
    },
    local_coder: {
      primary: 'qwen3-coder',
      preferred: []
    }
  }) as RoutingTable['profiles'],
  agentDefaults: Object.freeze({
    pm: 'writing_structured',
    architect: 'deep_reasoning',
    dev: 'general_code',
    qa: 'general_code',
    tea: 'general_code',
    analyst: 'writing_structured',
    'quick-flow-solo-dev': 'general_code'
  }) as RoutingTable['agentDefaults'],
  complexityOverride: Object.freeze({
    low: 'fast_iter',
    medium: 'general_code',
    high: 'deep_reasoning'
  }) as NonNullable<RoutingTable['complexityOverride']>
});
