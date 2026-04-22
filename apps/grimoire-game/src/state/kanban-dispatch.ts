/**
 * kanban-dispatch.ts — Pure planner for Mission Board drag→trigger (V2.2).
 *
 * When a card is dropped on a role column, this module produces a
 * validated dispatch request without performing any I/O. The request is
 * the canonical input of the control-plane dispatch endpoint (V2.3) and
 * of any local mock/paste fallback.
 *
 * The planner is fail-closed: invalid transitions return a plan with
 * `allowed: false` and a human-readable reason, so the UI can render
 * the blocker inline instead of triggering a silent no-op.
 */

import { z } from 'zod';

import {
  SwitchboardRoleSchema,
  roleToAgentId,
  type SwitchboardRole
} from '../contracts/switchboard-roles';

export const CARD_DISPATCH_COMPLEXITY = ['low', 'medium', 'high'] as const;
export type CardDispatchComplexity = (typeof CARD_DISPATCH_COMPLEXITY)[number];

export const CardDispatchInputSchema = z.object({
  cardId: z.string().min(1),
  title: z.string().min(1),
  targetRole: SwitchboardRoleSchema,
  /** Optional free-form context injected into the agent prompt. */
  promptContext: z.string().default(''),
  /** Prior correlation id; reused if present (resume/retry). */
  correlationId: z.string().min(1).optional(),
  /** Optional complexity hint (drives model routing in V2.5). */
  complexity: z.enum(CARD_DISPATCH_COMPLEXITY).optional(),
  /** Optional identifier of the operator who triggered the drop. */
  actorId: z.string().min(1).optional()
});

export type CardDispatchInput = z.input<typeof CardDispatchInputSchema>;

export const CardDispatchRequestSchema = z.object({
  cardId: z.string(),
  correlationId: z.string(),
  targetRole: SwitchboardRoleSchema,
  targetAgentId: z.string(),
  title: z.string(),
  promptContext: z.string(),
  complexity: z.enum(CARD_DISPATCH_COMPLEXITY),
  actorId: z.string().nullable(),
  /** ISO timestamp of the planning decision. */
  plannedAt: z.string()
});

export type CardDispatchRequest = z.infer<typeof CardDispatchRequestSchema>;

export type CardDispatchRejection =
  | 'invalid_input'
  | 'unknown_role'
  | 'empty_card_id';

export interface CardDispatchPlan {
  allowed: boolean;
  reason: string;
  request: CardDispatchRequest | null;
  rejection: CardDispatchRejection | null;
}

export interface PlanCardDispatchOptions {
  /** Dependency injection for tests — defaults to crypto.randomUUID(). */
  correlationIdFactory?: () => string;
  /** Dependency injection for tests — defaults to new Date().toISOString(). */
  clock?: () => string;
  /** Default complexity applied when input omits the hint. */
  defaultComplexity?: CardDispatchComplexity;
}

function defaultCorrelationIdFactory(): string {
  // Node 22+ and modern browsers expose crypto.randomUUID.
  return globalThis.crypto.randomUUID();
}

function defaultClock(): string {
  return new Date().toISOString();
}

/**
 * Validate a drop event and produce a dispatch request.
 *
 * Pure function: no network, no fs, no global state. Suitable for both
 * the browser (drop handler) and the server (request replay).
 */
export function planCardDispatch(
  input: CardDispatchInput,
  options: PlanCardDispatchOptions = {}
): CardDispatchPlan {
  const parseResult = CardDispatchInputSchema.safeParse(input);
  if (!parseResult.success) {
    return {
      allowed: false,
      reason: parseResult.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('; '),
      request: null,
      rejection: 'invalid_input'
    };
  }

  const parsed = parseResult.data;
  if (!parsed.cardId.trim()) {
    return {
      allowed: false,
      reason: 'cardId must not be blank',
      request: null,
      rejection: 'empty_card_id'
    };
  }

  const correlationIdFactory = options.correlationIdFactory ?? defaultCorrelationIdFactory;
  const clock = options.clock ?? defaultClock;
  const defaultComplexity = options.defaultComplexity ?? 'medium';

  const request: CardDispatchRequest = {
    cardId: parsed.cardId,
    correlationId: parsed.correlationId ?? correlationIdFactory(),
    targetRole: parsed.targetRole,
    targetAgentId: roleToAgentId(parsed.targetRole),
    title: parsed.title,
    promptContext: parsed.promptContext,
    complexity: parsed.complexity ?? defaultComplexity,
    actorId: parsed.actorId ?? null,
    plannedAt: clock()
  };

  return {
    allowed: true,
    reason: `Card ${request.cardId} ready for dispatch to ${request.targetAgentId} (role=${parsed.targetRole}).`,
    request,
    rejection: null
  };
}

export function isSwitchboardRoleLike(value: unknown): value is SwitchboardRole {
  return SwitchboardRoleSchema.safeParse(value).success;
}
