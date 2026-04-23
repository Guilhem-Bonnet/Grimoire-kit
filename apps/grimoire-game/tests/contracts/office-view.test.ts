import { describe, expect, it } from 'vitest';

import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent,
  type HookEventPhase,
  type HookEventScope
} from '../../src/contracts/hookEvents';
import {
  DEFAULT_OFFICE_GRID,
  OFFICE_VIEW_SCHEMA_VERSION,
  assignSeat,
  createOfficeView,
  mapEventToState
} from '../../src/state/office-view';

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
    source_hook: 'PostToolUse',
    agent: baseAgent,
    correlation_id: null,
    payload: {},
    ...overrides
  } as HookEvent;
}

describe('mapEventToState', () => {
  const cases: Array<[HookEventScope, HookEventPhase, string | null]> = [
    ['tool', 'start', 'type'],
    ['tool', 'end', 'idle'],
    ['tool', 'block', 'wait'],
    ['tool', 'correct', 'wait'],
    ['subagent', 'start', 'walk'],
    ['subagent', 'end', 'idle'],
    ['prompt', 'start', 'read'],
    ['prompt', 'end', 'idle'],
    ['task', 'start', 'walk'],
    ['task', 'end', 'idle'],
    ['task', 'block', 'wait'],
    ['compact', 'start', 'wait'],
    ['stop', 'info', 'wait'],
    ['anomaly', 'info', 'wait'],
    ['session', 'start', null]
  ];
  for (const [scope, phase, expected] of cases) {
    it(`maps ${scope}/${phase} → ${expected}`, () => {
      expect(mapEventToState(scope, phase)).toBe(expected);
    });
  }
});

describe('assignSeat', () => {
  it('returns a seat inside the grid', () => {
    const seat = assignSeat('dev', DEFAULT_OFFICE_GRID);
    expect(seat.col).toBeGreaterThanOrEqual(0);
    expect(seat.col).toBeLessThan(DEFAULT_OFFICE_GRID.cols);
    expect(seat.row).toBeGreaterThanOrEqual(0);
    expect(seat.row).toBeLessThan(DEFAULT_OFFICE_GRID.rows);
  });
  it('is deterministic per id', () => {
    const a = assignSeat('grimoire-master', DEFAULT_OFFICE_GRID);
    const b = assignSeat('grimoire-master', DEFAULT_OFFICE_GRID);
    expect(a).toEqual(b);
  });
  it('spreads adjacent ids', () => {
    const a = assignSeat('agent-1', DEFAULT_OFFICE_GRID);
    const b = assignSeat('agent-2', DEFAULT_OFFICE_GRID);
    expect(a).not.toEqual(b);
  });
});

describe('createOfficeView', () => {
  it('returns an empty view when no event has an agent id', () => {
    const view = createOfficeView([
      makeEvent({ agent: null }),
      makeEvent({ agent: { role: 'orphan' } })
    ]);
    expect(view.empty).toBe(true);
    expect(view.characters).toEqual([]);
    expect(view.schemaVersion).toBe(OFFICE_VIEW_SCHEMA_VERSION);
    expect(view.grid).toEqual(DEFAULT_OFFICE_GRID);
  });

  it('creates one character per distinct agent id', () => {
    const view = createOfficeView([
      makeEvent({ agent: { id: 'dev', role: 'dev' } }),
      makeEvent({ agent: { id: 'dev', role: 'dev' } }),
      makeEvent({ agent: { id: 'qa', role: 'qa' } })
    ]);
    expect(view.characters).toHaveLength(2);
    const ids = view.characters.map((c) => c.agentId).sort();
    expect(ids).toEqual(['dev', 'qa']);
  });

  it('drives the FSM from the latest event', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'start',
        ts: '2026-04-22T12:00:00.000Z'
      }),
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'block',
        ts: '2026-04-22T12:00:01.000Z'
      })
    ]);
    expect(view.characters[0]?.state).toBe('wait');
  });

  it('falls back to idle when scope/phase has no mapping', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        scope: 'session',
        phase: 'start'
      })
    ]);
    expect(view.characters[0]?.state).toBe('idle');
  });

  it('links sub-agents to their master via subagent/start (parent field)', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'qa', role: 'qa', parent: 'dev' },
        scope: 'subagent',
        phase: 'start'
      })
    ]);
    const qa = view.characters.find((c) => c.agentId === 'qa');
    const dev = view.characters.find((c) => c.agentId === 'dev');
    expect(qa?.parent).toBe('dev');
    expect(qa?.state).toBe('walk');
    expect(dev?.isMaster).toBe(true);
  });

  it('links sub-agents via correlation_id when no parent field is set', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'qa', role: 'qa' },
        scope: 'subagent',
        phase: 'start',
        correlation_id: 'dev'
      })
    ]);
    expect(view.characters.find((c) => c.agentId === 'qa')?.parent).toBe('dev');
    expect(view.characters.find((c) => c.agentId === 'dev')?.isMaster).toBe(true);
  });

  it('clears master flag once the last child ends', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'qa', role: 'qa', parent: 'dev' },
        scope: 'subagent',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'qa', role: 'qa', parent: 'dev' },
        scope: 'subagent',
        phase: 'end'
      })
    ]);
    expect(view.characters.find((c) => c.agentId === 'dev')?.isMaster).toBe(false);
  });

  it('keeps master flag when other children remain', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'qa', role: 'qa', parent: 'dev' },
        scope: 'subagent',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'tea', role: 'tea', parent: 'dev' },
        scope: 'subagent',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'qa', role: 'qa', parent: 'dev' },
        scope: 'subagent',
        phase: 'end'
      })
    ]);
    expect(view.characters.find((c) => c.agentId === 'dev')?.isMaster).toBe(true);
  });

  it('decays inactive characters back to idle after idleAfterMs', () => {
    const view = createOfficeView(
      [
        makeEvent({
          agent: { id: 'dev', role: 'dev' },
          scope: 'tool',
          phase: 'start',
          ts: '2026-04-22T12:00:00.000Z'
        })
      ],
      { now: '2026-04-22T12:00:30.000Z', idleAfterMs: 8_000 }
    );
    expect(view.characters[0]?.state).toBe('idle');
  });

  it('does not decay when within idle window', () => {
    const view = createOfficeView(
      [
        makeEvent({
          agent: { id: 'dev', role: 'dev' },
          scope: 'tool',
          phase: 'start',
          ts: '2026-04-22T12:00:00.000Z'
        })
      ],
      { now: '2026-04-22T12:00:01.000Z', idleAfterMs: 8_000 }
    );
    expect(view.characters[0]?.state).toBe('type');
  });

  it('counts characters per state', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'qa', role: 'qa' },
        scope: 'tool',
        phase: 'block'
      }),
      makeEvent({
        agent: { id: 'sm', role: 'sm' },
        scope: 'prompt',
        phase: 'start'
      })
    ]);
    expect(view.stateCounters.type).toBe(1);
    expect(view.stateCounters.wait).toBe(1);
    expect(view.stateCounters.read).toBe(1);
    expect(view.stateCounters.idle).toBe(0);
  });

  it('orders active characters before idle ones', () => {
    const view = createOfficeView([
      makeEvent({
        agent: { id: 'sleeper', role: 'sleeper' },
        scope: 'session',
        phase: 'start'
      }),
      makeEvent({
        agent: { id: 'worker', role: 'worker' },
        scope: 'tool',
        phase: 'start'
      })
    ]);
    expect(view.characters[0]?.agentId).toBe('worker');
    expect(view.characters[1]?.agentId).toBe('sleeper');
  });

  it('honours a custom grid', () => {
    const view = createOfficeView(
      [
        makeEvent({
          agent: { id: 'dev', role: 'dev' },
          scope: 'tool',
          phase: 'start'
        })
      ],
      { grid: { cols: 4, rows: 3 } }
    );
    expect(view.grid).toEqual({ cols: 4, rows: 3 });
    const seat = view.characters[0]?.seat;
    expect(seat?.col).toBeLessThan(4);
    expect(seat?.row).toBeLessThan(3);
  });

  it('records lastEventId, lastEventTs and lastEventKind', () => {
    const view = createOfficeView([
      makeEvent({
        event_id: 'evt-foo',
        agent: { id: 'dev', role: 'dev' },
        scope: 'tool',
        phase: 'start',
        ts: '2026-04-22T12:00:05.000Z'
      })
    ]);
    const dev = view.characters[0];
    expect(dev?.lastEventId).toBe('evt-foo');
    expect(dev?.lastEventTs).toBe('2026-04-22T12:00:05.000Z');
    expect(dev?.lastEventKind).toBe('tool/start');
  });

  it('falls back to agent id when role is missing', () => {
    const view = createOfficeView([
      makeEvent({ agent: { id: 'dev' }, scope: 'tool', phase: 'start' })
    ]);
    expect(view.characters[0]?.role).toBe('dev');
  });
});
