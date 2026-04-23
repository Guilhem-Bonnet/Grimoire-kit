import { describe, expect, it } from 'vitest';

import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent
} from '../../src/contracts/hookEvents';
import {
  OFFICE_DEBUG_PANEL_SCHEMA_VERSION,
  createOfficeDebugPanelView
} from '../../src/state/office-debug-panel-view';

let counter = 0;
function makeEvent(overrides: Partial<HookEvent> = {}): HookEvent {
  counter += 1;
  const baseAgent =
    overrides.agent === undefined ? { id: 'dev', role: 'dev' } : overrides.agent;
  return {
    schema_version: HOOK_EVENT_SCHEMA_VERSION,
    event_id: overrides.event_id ?? `evt-${counter}`,
    ts: '2026-04-22T12:00:00.000Z',
    scope: 'tool',
    phase: 'start',
    source_hook: 'PreToolUse',
    agent: baseAgent,
    correlation_id: null,
    payload: {},
    ...overrides
  } as HookEvent;
}

describe('createOfficeDebugPanelView', () => {
  it('returns empty when no events have an agent id', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({ agent: null }),
      makeEvent({ agent: { role: 'orphan' } })
    ]);
    expect(view.empty).toBe(true);
    expect(view.agents).toEqual([]);
    expect(view.totalEvents).toBe(0);
    expect(view.schemaVersion).toBe(OFFICE_DEBUG_PANEL_SCHEMA_VERSION);
  });

  it('groups events per agent', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({ agent: { id: 'dev', role: 'dev' } }),
      makeEvent({ agent: { id: 'dev', role: 'dev' } }),
      makeEvent({ agent: { id: 'qa', role: 'qa' } })
    ]);
    expect(view.agents).toHaveLength(2);
    const dev = view.agents.find((a) => a.agentId === 'dev');
    const qa = view.agents.find((a) => a.agentId === 'qa');
    expect(dev?.eventCount).toBe(2);
    expect(qa?.eventCount).toBe(1);
    expect(view.totalEvents).toBe(3);
  });

  it('orders events most-recent first inside each agent', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({
        event_id: 'old',
        agent: { id: 'dev', role: 'dev' },
        ts: '2026-04-22T12:00:00.000Z'
      }),
      makeEvent({
        event_id: 'new',
        agent: { id: 'dev', role: 'dev' },
        ts: '2026-04-22T12:00:05.000Z'
      })
    ]);
    const dev = view.agents.find((a) => a.agentId === 'dev');
    expect(dev?.events.map((e) => e.eventId)).toEqual(['new', 'old']);
  });

  it('truncates to maxEventsPerAgent (default 10)', () => {
    const events = Array.from({ length: 15 }, (_, i) =>
      makeEvent({
        event_id: `evt-${i}`,
        agent: { id: 'dev', role: 'dev' },
        ts: `2026-04-22T12:00:${String(i).padStart(2, '0')}.000Z`
      })
    );
    const view = createOfficeDebugPanelView(events);
    const dev = view.agents.find((a) => a.agentId === 'dev');
    expect(dev?.events).toHaveLength(10);
    expect(dev?.eventCount).toBe(15);
    expect(dev?.events[0]?.eventId).toBe('evt-14');
  });

  it('honours custom maxEventsPerAgent', () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      makeEvent({
        event_id: `evt-${i}`,
        agent: { id: 'dev', role: 'dev' },
        ts: `2026-04-22T12:00:0${i}.000Z`
      })
    );
    const view = createOfficeDebugPanelView(events, { maxEventsPerAgent: 2 });
    expect(view.agents[0]?.events).toHaveLength(2);
  });

  it('caps the agents list with maxAgents (most-recently-active first)', () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      makeEvent({
        event_id: `evt-${i}`,
        agent: { id: `agent-${i}`, role: `r${i}` },
        ts: `2026-04-22T12:00:0${i}.000Z`
      })
    );
    const view = createOfficeDebugPanelView(events, { maxAgents: 2 });
    expect(view.agents).toHaveLength(2);
    expect(view.agents[0]?.agentId).toBe('agent-4');
    expect(view.agents[1]?.agentId).toBe('agent-3');
  });

  it('exposes derivedState alongside the raw event', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({
        event_id: 'block',
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'block'
      })
    ]);
    const row = view.agents[0]?.events[0];
    expect(row?.derivedState).toBe('wait');
    expect(row?.scope).toBe('tool');
    expect(row?.phase).toBe('block');
  });

  it('surfaces lastState per agent', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({
        event_id: 'first',
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'start',
        ts: '2026-04-22T12:00:00.000Z'
      }),
      makeEvent({
        event_id: 'last',
        agent: { id: 'dev', role: 'dev' },
        scope: 'subagent',
        phase: 'start',
        ts: '2026-04-22T12:00:05.000Z'
      })
    ]);
    expect(view.agents[0]?.lastState).toBe('walk');
    expect(view.agents[0]?.lastEventTs).toBe('2026-04-22T12:00:05.000Z');
  });

  it('omits payload by default but copies it when includePayload is true', () => {
    const events = [
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        payload: { tool: 'read_file', secret: 'do-not-leak' }
      })
    ];
    const lean = createOfficeDebugPanelView(events);
    expect(lean.agents[0]?.events[0]?.payload).toBeUndefined();
    const verbose = createOfficeDebugPanelView(events, {
      includePayload: true
    });
    expect(verbose.agents[0]?.events[0]?.payload).toEqual({
      tool: 'read_file',
      secret: 'do-not-leak'
    });
  });

  it('echoes options.now into generatedAt', () => {
    const view = createOfficeDebugPanelView([], { now: '2026-04-22T13:00:00.000Z' });
    expect(view.generatedAt).toBe('2026-04-22T13:00:00.000Z');
  });

  it('keeps role from the latest event when subsequent events drop it', () => {
    const view = createOfficeDebugPanelView([
      makeEvent({ agent: { id: 'dev', role: 'dev-engineer' } }),
      makeEvent({ agent: { id: 'dev' } })
    ]);
    expect(view.agents[0]?.role).toBe('dev-engineer');
  });
});
