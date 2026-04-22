import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  HOOK_EVENT_SCHEMA_VERSION,
  computeHookEventCounters,
  createHookEventSnapshot,
  filterHookEvents,
  parseHookEventLine,
  parseHookEventsJsonl,
  type HookEvent
} from '../../src/contracts/hookEvents';
import {
  buildHookEventSnapshot,
  readHookEventLedger
} from '../../src/server/hook-events-feed';

const VALID_LINE = JSON.stringify({
  schema_version: '1.0',
  event_id: 'evt-1',
  ts: '2026-04-21T10:00:00.000Z',
  scope: 'tool',
  phase: 'start',
  source_hook: 'grimoire-post-edit.sh',
  agent: null,
  correlation_id: null,
  payload: { file: 'foo.py' }
});

function makeEvent(overrides: Partial<HookEvent> = {}): HookEvent {
  return {
    schema_version: '1.0',
    event_id: 'evt-' + Math.random().toString(36).slice(2),
    ts: '2026-04-21T10:00:00.000Z',
    scope: 'tool',
    phase: 'info',
    source_hook: 'grimoire-post-edit.sh',
    agent: null,
    correlation_id: null,
    payload: {},
    ...overrides
  } as HookEvent;
}

describe('hookEvents contract (mirror of Python grimoire.tools.events)', () => {
  it('exposes the shared SCHEMA_VERSION', () => {
    expect(HOOK_EVENT_SCHEMA_VERSION).toBe('1.0');
  });

  it('parses a valid JSONL line written by the Python writer', () => {
    const event = parseHookEventLine(VALID_LINE);
    expect(event.scope).toBe('tool');
    expect(event.phase).toBe('start');
    expect(event.source_hook).toBe('grimoire-post-edit.sh');
    expect(event.payload).toEqual({ file: 'foo.py' });
  });

  it('rejects events with an unknown scope', () => {
    const bad = JSON.stringify({ ...JSON.parse(VALID_LINE), scope: 'bogus' });
    expect(() => parseHookEventLine(bad)).toThrow();
  });

  it('rejects events with an unknown phase', () => {
    const bad = JSON.stringify({ ...JSON.parse(VALID_LINE), phase: 'bogus' });
    expect(() => parseHookEventLine(bad)).toThrow();
  });

  it('rejects an empty line', () => {
    expect(() => parseHookEventLine('')).toThrow();
  });

  it('skips malformed lines when parsing JSONL blobs', () => {
    const blob = [VALID_LINE, 'not json', '', VALID_LINE].join('\n');
    expect(parseHookEventsJsonl(blob)).toHaveLength(2);
  });
});

describe('filterHookEvents', () => {
  const events: HookEvent[] = [
    makeEvent({ ts: '2026-01-01T00:00:00.000Z', scope: 'tool', phase: 'start' }),
    makeEvent({ ts: '2026-03-01T00:00:00.000Z', scope: 'subagent', phase: 'start' }),
    makeEvent({ ts: '2026-06-01T00:00:00.000Z', scope: 'tool', phase: 'end' })
  ];

  it('filters by sinceTs (strictly greater)', () => {
    const out = filterHookEvents(events, { sinceTs: '2026-02-01T00:00:00.000Z' });
    expect(out).toHaveLength(2);
    expect(out.every((event) => event.ts > '2026-02-01T00:00:00.000Z')).toBe(true);
  });

  it('filters by scope', () => {
    const out = filterHookEvents(events, { scope: 'subagent' });
    expect(out).toHaveLength(1);
    expect(out[0]?.scope).toBe('subagent');
  });

  it('applies a limit by keeping the most recent', () => {
    const out = filterHookEvents(events, { limit: 2 });
    expect(out).toHaveLength(2);
    expect(out[1]?.ts).toBe('2026-06-01T00:00:00.000Z');
  });
});

describe('computeHookEventCounters', () => {
  it('returns zeros for an empty array', () => {
    const counters = computeHookEventCounters([]);
    expect(counters.total).toBe(0);
    expect(counters.byScope).toEqual({});
    expect(counters.bySourceHook).toEqual({});
  });

  it('groups counts by scope/phase and source hook', () => {
    const counters = computeHookEventCounters([
      makeEvent({ scope: 'tool', phase: 'start', source_hook: 'a.sh' }),
      makeEvent({ scope: 'tool', phase: 'start', source_hook: 'a.sh' }),
      makeEvent({ scope: 'tool', phase: 'block', source_hook: 'a.sh' }),
      makeEvent({ scope: 'subagent', phase: 'start', source_hook: 'b.sh' })
    ]);
    expect(counters.total).toBe(4);
    expect(counters.byScope.tool).toEqual({ start: 2, block: 1 });
    expect(counters.byScope.subagent).toEqual({ start: 1 });
    expect(counters.bySourceHook).toEqual({ 'a.sh': 3, 'b.sh': 1 });
  });
});

describe('createHookEventSnapshot', () => {
  it('produces a snapshot with stable shape', () => {
    const snapshot = createHookEventSnapshot([makeEvent()], {
      generatedAt: '2026-04-21T12:00:00.000Z'
    });
    expect(snapshot.schemaVersion).toBe('1.0');
    expect(snapshot.generatedAt).toBe('2026-04-21T12:00:00.000Z');
    expect(snapshot.events).toHaveLength(1);
    expect(snapshot.counters.total).toBe(1);
  });
});

describe('readHookEventLedger (Node FS)', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'grimoire-hookfeed-'));
    mkdirSync(join(projectRoot, '_grimoire-runtime', '_memory'), { recursive: true });
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('returns [] when the ledger is missing', () => {
    expect(readHookEventLedger({ projectRoot })).toEqual([]);
  });

  it('reads and parses the ledger file', () => {
    writeFileSync(
      join(projectRoot, '_grimoire-runtime', '_memory', 'activity.jsonl'),
      VALID_LINE + '\n' + VALID_LINE + '\n'
    );
    const events = readHookEventLedger({ projectRoot });
    expect(events).toHaveLength(2);
    expect(events[0]?.scope).toBe('tool');
  });

  it('buildHookEventSnapshot assembles a publishable snapshot', () => {
    writeFileSync(
      join(projectRoot, '_grimoire-runtime', '_memory', 'activity.jsonl'),
      VALID_LINE + '\n'
    );
    const snapshot = buildHookEventSnapshot({
      projectRoot,
      generatedAt: '2026-04-21T12:00:00.000Z'
    });
    expect(snapshot.events).toHaveLength(1);
    expect(snapshot.counters.bySourceHook['grimoire-post-edit.sh']).toBe(1);
  });
});
