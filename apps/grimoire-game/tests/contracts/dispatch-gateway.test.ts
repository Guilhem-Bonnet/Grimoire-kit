import { describe, expect, it, vi } from 'vitest';

import {
  DISPATCH_SOURCE_HOOK,
  buildDispatchStartEvent,
  dispatchCardStart
} from '../../src/server/control-plane/dispatch-gateway';
import {
  HOOK_EVENT_SCHEMA_VERSION,
  HookEventSchema,
  type HookEvent
} from '../../src/contracts/hookEvents';
import type { CardDispatchRequest } from '../../src/state/kanban-dispatch';

const REQUEST: CardDispatchRequest = {
  cardId: 'card-42',
  correlationId: '00000000-0000-4000-8000-0000000000aa',
  targetRole: 'coder',
  targetAgentId: 'dev',
  title: 'Wire dispatch gateway',
  promptContext: 'Refer to maturation plan V2.3',
  complexity: 'medium',
  actorId: 'guilhem',
  plannedAt: '2026-04-22T12:30:00.000Z'
};

const options = {
  eventIdFactory: () => '00000000-0000-4000-8000-0000000000bb',
  clock: () => '2026-04-22T12:30:01.000Z'
};

describe('buildDispatchStartEvent', () => {
  it('produces a schema-valid task/start HookEvent', () => {
    const event = buildDispatchStartEvent(REQUEST, options);
    expect(() => HookEventSchema.parse(event)).not.toThrow();
    expect(event.schema_version).toBe(HOOK_EVENT_SCHEMA_VERSION);
    expect(event.scope).toBe('task');
    expect(event.phase).toBe('start');
    expect(event.source_hook).toBe(DISPATCH_SOURCE_HOOK);
    expect(event.correlation_id).toBe(REQUEST.correlationId);
    expect(event.event_id).toBe('00000000-0000-4000-8000-0000000000bb');
    expect(event.ts).toBe('2026-04-22T12:30:01.000Z');
  });

  it('carries the agent identity and dispatch payload', () => {
    const event = buildDispatchStartEvent(REQUEST, options);
    expect(event.agent).toEqual({ id: 'dev', role: 'coder' });
    expect(event.payload).toEqual({
      cardId: 'card-42',
      title: 'Wire dispatch gateway',
      complexity: 'medium',
      actorId: 'guilhem',
      promptContext: 'Refer to maturation plan V2.3',
      plannedAt: '2026-04-22T12:30:00.000Z'
    });
  });
});

describe('dispatchCardStart', () => {
  it('emits the event via the provided emitter', () => {
    const emitted: HookEvent[] = [];
    const result = dispatchCardStart(REQUEST, {
      ...options,
      emit: (event) => emitted.push(event)
    });
    expect(result.accepted).toBe(true);
    expect(emitted).toHaveLength(1);
    expect(emitted[0]?.correlation_id).toBe(REQUEST.correlationId);
    expect(result.reason).toContain('card-42');
    expect(result.reason).toContain('dev');
  });

  it('accepts when no emitter is configured (build-only mode)', () => {
    const result = dispatchCardStart(REQUEST, options);
    expect(result.accepted).toBe(true);
    expect(result.event.scope).toBe('task');
    expect(result.reason).toMatch(/no emitter/i);
  });

  it('captures emitter failures without throwing', () => {
    const boom = vi.fn(() => {
      throw new Error('ledger write failed');
    });
    const result = dispatchCardStart(REQUEST, {
      ...options,
      emit: boom
    });
    expect(boom).toHaveBeenCalledTimes(1);
    expect(result.accepted).toBe(false);
    expect(result.reason).toContain('ledger write failed');
    expect(result.event).toBeDefined();
  });

  it('preserves the card correlation across retries', () => {
    const emitted: HookEvent[] = [];
    const emit: (event: HookEvent) => void = (event) => emitted.push(event);
    dispatchCardStart(REQUEST, { ...options, emit });
    dispatchCardStart(REQUEST, {
      emit,
      eventIdFactory: () => '00000000-0000-4000-8000-0000000000cc',
      clock: () => '2026-04-22T12:30:05.000Z'
    });
    expect(emitted).toHaveLength(2);
    expect(emitted[0]?.correlation_id).toBe(REQUEST.correlationId);
    expect(emitted[1]?.correlation_id).toBe(REQUEST.correlationId);
    expect(emitted[0]?.event_id).not.toBe(emitted[1]?.event_id);
  });
});
