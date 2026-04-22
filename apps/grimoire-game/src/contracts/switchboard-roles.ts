/**
 * switchboard-roles.ts — Mission Board column role taxonomy (V2.1).
 *
 * The Mission Board (V2) drives real agents via drag→trigger. Each column
 * represents a role (not a task status). When a card lands in a column,
 * the orchestrator dispatches the card to the mapped sub-agent.
 *
 * Contract reference: planning-artifacts/maturation-agentique-20260421
 * §V2 — S2.1 / S2.2.
 *
 * Role → sub-agent mapping is stable but expressive: multiple roles can
 * map to the same agent (e.g. coder + lead_coder → dev) when the
 * distinction is a dispatch hint rather than a different specialist.
 */

import { z } from 'zod';

export const SWITCHBOARD_ROLES = [
  'planner',
  'lead_coder',
  'coder',
  'reviewer',
  'acceptance',
  'analyst',
  'intern'
] as const;

export const SwitchboardRoleSchema = z.enum(SWITCHBOARD_ROLES);
export type SwitchboardRole = (typeof SWITCHBOARD_ROLES)[number];

/**
 * Canonical role → sub-agent mapping. Uses agent names declared under
 * `.github/agents/*.agent.md`. The orchestrator (`grimoire-master`)
 * dispatches to these; sub-agents remain invisible to the user per SOG
 * protocol.
 */
export const SWITCHBOARD_ROLE_TO_AGENT: Readonly<Record<SwitchboardRole, string>> = Object.freeze({
  planner: 'pm',
  lead_coder: 'architect',
  coder: 'dev',
  reviewer: 'qa',
  acceptance: 'tea',
  analyst: 'analyst',
  intern: 'quick-flow-solo-dev'
});

export function roleToAgentId(role: SwitchboardRole): string {
  return SWITCHBOARD_ROLE_TO_AGENT[role];
}

export function isSwitchboardRole(value: unknown): value is SwitchboardRole {
  return typeof value === 'string' && (SWITCHBOARD_ROLES as readonly string[]).includes(value);
}
