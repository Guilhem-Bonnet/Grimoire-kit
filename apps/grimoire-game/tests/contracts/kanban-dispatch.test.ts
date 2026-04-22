import { describe, expect, it } from 'vitest';

import {
  SWITCHBOARD_ROLES,
  SWITCHBOARD_ROLE_TO_AGENT,
  roleToAgentId,
  isSwitchboardRole
} from '../../src/contracts/switchboard-roles';
import {
  CardDispatchInputSchema,
  CardDispatchRequestSchema,
  planCardDispatch
} from '../../src/state/kanban-dispatch';

describe('Switchboard role taxonomy', () => {
  it('exposes the 7 canonical roles', () => {
    expect([...SWITCHBOARD_ROLES]).toEqual([
      'planner',
      'lead_coder',
      'coder',
      'reviewer',
      'acceptance',
      'analyst',
      'intern'
    ]);
  });

  it('maps every role to an existing sub-agent', () => {
    for (const role of SWITCHBOARD_ROLES) {
      const agent = roleToAgentId(role);
      expect(SWITCHBOARD_ROLE_TO_AGENT[role]).toBe(agent);
      expect(agent.length).toBeGreaterThan(0);
    }
  });

  it('recognises role strings via the type guard', () => {
    expect(isSwitchboardRole('coder')).toBe(true);
    expect(isSwitchboardRole('unknown')).toBe(false);
    expect(isSwitchboardRole(42)).toBe(false);
    expect(isSwitchboardRole(null)).toBe(false);
  });
});

describe('planCardDispatch', () => {
  const fixedCorrelationId = '00000000-0000-4000-8000-000000000001';
  const fixedClock = '2026-04-22T12:00:00.000Z';
  const options = {
    correlationIdFactory: () => fixedCorrelationId,
    clock: () => fixedClock
  };

  it('builds a valid dispatch request for a well-formed drop', () => {
    const plan = planCardDispatch(
      {
        cardId: 'CARD-101',
        title: 'Wire hook gateway',
        targetRole: 'coder',
        promptContext: 'Honor the fail-open contract.'
      },
      options
    );

    expect(plan.allowed).toBe(true);
    expect(plan.rejection).toBeNull();
    expect(plan.request).toEqual({
      cardId: 'CARD-101',
      correlationId: fixedCorrelationId,
      targetRole: 'coder',
      targetAgentId: 'dev',
      title: 'Wire hook gateway',
      promptContext: 'Honor the fail-open contract.',
      complexity: 'medium',
      actorId: null,
      plannedAt: fixedClock
    });
    // Re-parsing through the output schema confirms the shape is canonical.
    expect(CardDispatchRequestSchema.safeParse(plan.request).success).toBe(true);
  });

  it('preserves the caller-provided correlationId when present', () => {
    const plan = planCardDispatch(
      {
        cardId: 'CARD-102',
        title: 'Resume',
        targetRole: 'reviewer',
        correlationId: 'resume-xyz'
      },
      options
    );
    expect(plan.request?.correlationId).toBe('resume-xyz');
    expect(plan.request?.targetAgentId).toBe('qa');
  });

  it('defaults promptContext to empty and complexity to medium', () => {
    const plan = planCardDispatch(
      { cardId: 'CARD-103', title: 'Scope', targetRole: 'planner' },
      options
    );
    expect(plan.request?.promptContext).toBe('');
    expect(plan.request?.complexity).toBe('medium');
    expect(plan.request?.targetAgentId).toBe('pm');
  });

  it('respects explicit complexity hint', () => {
    const plan = planCardDispatch(
      { cardId: 'CARD-104', title: 'Spike', targetRole: 'intern', complexity: 'low' },
      options
    );
    expect(plan.request?.complexity).toBe('low');
    expect(plan.request?.targetAgentId).toBe('quick-flow-solo-dev');
  });

  it('rejects an unknown role with a schema error', () => {
    const plan = planCardDispatch(
      // @ts-expect-error — exercise runtime validation with an invalid enum.
      { cardId: 'CARD-105', title: 'X', targetRole: 'ghost' },
      options
    );
    expect(plan.allowed).toBe(false);
    expect(plan.rejection).toBe('invalid_input');
    expect(plan.request).toBeNull();
    expect(plan.reason).toContain('targetRole');
  });

  it('rejects a blank cardId', () => {
    const plan = planCardDispatch(
      { cardId: '', title: 'X', targetRole: 'coder' },
      options
    );
    expect(plan.allowed).toBe(false);
    expect(plan.request).toBeNull();
  });

  it('rejects missing title', () => {
    const plan = planCardDispatch(
      // @ts-expect-error — title required.
      { cardId: 'CARD-106', targetRole: 'coder' },
      options
    );
    expect(plan.allowed).toBe(false);
  });

  it('accepts the actorId when provided', () => {
    const plan = planCardDispatch(
      {
        cardId: 'CARD-107',
        title: 'Approve',
        targetRole: 'acceptance',
        actorId: 'operator-1'
      },
      options
    );
    expect(plan.request?.actorId).toBe('operator-1');
    expect(plan.request?.targetAgentId).toBe('tea');
  });

  it('defaultComplexity option overrides the baseline', () => {
    const plan = planCardDispatch(
      { cardId: 'CARD-108', title: 'Audit', targetRole: 'analyst' },
      { ...options, defaultComplexity: 'high' }
    );
    expect(plan.request?.complexity).toBe('high');
  });

  it('exposes a usable input schema for external validators', () => {
    const result = CardDispatchInputSchema.safeParse({
      cardId: 'CARD-109',
      title: 'Z',
      targetRole: 'lead_coder'
    });
    expect(result.success).toBe(true);
  });
});
