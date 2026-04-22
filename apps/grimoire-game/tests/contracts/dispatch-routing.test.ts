import { describe, expect, it } from 'vitest';

import {
  DEFAULT_ROUTING_TABLE,
  ROUTING_PROFILES,
  resolveDispatchRouting,
  type RoutingTable
} from '../../src/state/dispatch-routing';

describe('resolveDispatchRouting', () => {
  it('applies complexity override first', () => {
    const decision = resolveDispatchRouting(
      { agentId: 'pm', complexity: 'high' },
      DEFAULT_ROUTING_TABLE
    );
    expect(decision.profile).toBe('deep_reasoning');
    expect(decision.usedComplexityOverride).toBe(true);
    expect(decision.reason).toMatch(/complexity=high/);
  });

  it('uses agent default when override table is absent', () => {
    const table: RoutingTable = {
      profiles: DEFAULT_ROUTING_TABLE.profiles,
      agentDefaults: DEFAULT_ROUTING_TABLE.agentDefaults
    };
    const decision = resolveDispatchRouting(
      { agentId: 'architect', complexity: 'medium' },
      table
    );
    expect(decision.profile).toBe('deep_reasoning');
    expect(decision.usedComplexityOverride).toBe(false);
    expect(decision.reason).toMatch(/architect/);
  });

  it('falls back when the agent is unknown', () => {
    const table: RoutingTable = {
      profiles: DEFAULT_ROUTING_TABLE.profiles,
      agentDefaults: {}
    };
    const decision = resolveDispatchRouting(
      { agentId: 'ghost', complexity: 'medium' },
      table
    );
    expect(decision.usedFallback).toBe(true);
    expect(decision.profile).toBe('general_code');
  });

  it('flags missing profile definition', () => {
    const broken: RoutingTable = {
      profiles: {} as RoutingTable['profiles'],
      agentDefaults: { dev: 'general_code' }
    };
    const decision = resolveDispatchRouting(
      { agentId: 'dev', complexity: 'medium' },
      broken
    );
    expect(decision.usedFallback).toBe(true);
    expect(decision.primary).toBe('auto');
    expect(decision.preferred).toEqual([]);
    expect(decision.reason).toMatch(/missing profile/);
  });

  it('returns the preferred chain for the selected profile', () => {
    const decision = resolveDispatchRouting(
      { agentId: 'dev', complexity: 'medium' },
      DEFAULT_ROUTING_TABLE
    );
    expect(decision.profile).toBe('general_code');
    expect(decision.primary).toBe('auto');
    expect(decision.preferred.length).toBeGreaterThan(0);
  });

  it('exposes the canonical profile taxonomy', () => {
    expect([...ROUTING_PROFILES]).toEqual([
      'deep_reasoning',
      'general_code',
      'writing_structured',
      'fast_iter',
      'local_coder'
    ]);
  });

  it('low complexity drops to fast_iter via override', () => {
    const decision = resolveDispatchRouting(
      { agentId: 'architect', complexity: 'low' },
      DEFAULT_ROUTING_TABLE
    );
    expect(decision.profile).toBe('fast_iter');
    expect(decision.usedComplexityOverride).toBe(true);
  });
});
