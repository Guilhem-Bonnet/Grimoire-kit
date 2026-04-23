import { describe, expect, it } from 'vitest';

import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent
} from '../../src/contracts/hookEvents';
import {
  OFFICE_TIMELINE_SCHEMA_VERSION,
  buildOfficeTimeline,
  scrubOfficeTimeline
} from '../../src/state/office-timeline-view';

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

function timestampedEvents(count: number): HookEvent[] {
  return Array.from({ length: count }, (_, i) =>
    makeEvent({
      event_id: `evt-${i}`,
      agent: { id: i % 2 === 0 ? 'dev' : 'qa', role: i % 2 === 0 ? 'dev' : 'qa' },
      scope: i % 3 === 0 ? 'subagent' : 'tool',
      phase: i % 5 === 0 ? 'end' : 'start',
      ts: `2026-04-22T12:00:${String(i).padStart(2, '0')}.000Z`
    })
  );
}

describe('buildOfficeTimeline', () => {
  it('returns an empty timeline for no events', () => {
    const tl = buildOfficeTimeline([]);
    expect(tl.empty).toBe(true);
    expect(tl.frames).toEqual([]);
    expect(tl.bounds).toBeNull();
    expect(tl.schemaVersion).toBe(OFFICE_TIMELINE_SCHEMA_VERSION);
  });

  it('sorts events chronologically', () => {
    const tl = buildOfficeTimeline([
      makeEvent({ event_id: 'b', ts: '2026-04-22T12:00:05.000Z' }),
      makeEvent({ event_id: 'a', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({ event_id: 'c', ts: '2026-04-22T12:00:10.000Z' })
    ]);
    expect(tl.frames.map((f) => f.eventId)).toEqual(['a', 'b', 'c']);
  });

  it('caps to maxEvents (default 30) keeping the most-recent suffix', () => {
    const tl = buildOfficeTimeline(timestampedEvents(35));
    expect(tl.frames).toHaveLength(30);
    expect(tl.frames[0]?.eventId).toBe('evt-5');
    expect(tl.frames[29]?.eventId).toBe('evt-34');
  });

  it('honours custom maxEvents', () => {
    const tl = buildOfficeTimeline(timestampedEvents(10), { maxEvents: 4 });
    expect(tl.frames).toHaveLength(4);
    expect(tl.frames[0]?.eventId).toBe('evt-6');
  });

  it('keeps everything when maxEvents is 0', () => {
    const tl = buildOfficeTimeline(timestampedEvents(8), { maxEvents: 0 });
    expect(tl.frames).toHaveLength(8);
  });

  it('computes bounds with duration in milliseconds', () => {
    const tl = buildOfficeTimeline(timestampedEvents(5));
    expect(tl.bounds?.start).toBe('2026-04-22T12:00:00.000Z');
    expect(tl.bounds?.end).toBe('2026-04-22T12:00:04.000Z');
    expect(tl.bounds?.durationMs).toBe(4000);
  });
});

describe('scrubOfficeTimeline', () => {
  it('returns an empty view when the timeline is empty', () => {
    const tl = buildOfficeTimeline([]);
    const result = scrubOfficeTimeline(tl, { index: 0 });
    expect(result.cursorIndex).toBe(-1);
    expect(result.frame).toBeNull();
    expect(result.view.empty).toBe(true);
  });

  it('replays only events up to and including cursor index', () => {
    const tl = buildOfficeTimeline(timestampedEvents(5));
    const result = scrubOfficeTimeline(tl, { index: 2 });
    expect(result.cursorIndex).toBe(2);
    expect(result.frame?.eventId).toBe('evt-2');
    // 3 events feed the view (indices 0,1,2).
    expect(result.view.characters.length).toBeGreaterThan(0);
  });

  it('clamps a high index to the last frame', () => {
    const tl = buildOfficeTimeline(timestampedEvents(3));
    const result = scrubOfficeTimeline(tl, { index: 999 });
    expect(result.cursorIndex).toBe(2);
    expect(result.frame?.eventId).toBe('evt-2');
  });

  it('clamps a negative index to "before timeline" (-1)', () => {
    const tl = buildOfficeTimeline(timestampedEvents(3));
    const result = scrubOfficeTimeline(tl, { index: -5 });
    expect(result.cursorIndex).toBe(-1);
    expect(result.frame).toBeNull();
    expect(result.view.empty).toBe(true);
  });

  it('scrubs by ratio in [0, 1]', () => {
    const tl = buildOfficeTimeline(timestampedEvents(5));
    const start = scrubOfficeTimeline(tl, { ratio: 0 });
    const end = scrubOfficeTimeline(tl, { ratio: 1 });
    const middle = scrubOfficeTimeline(tl, { ratio: 0.5 });
    expect(start.cursorIndex).toBe(0);
    expect(end.cursorIndex).toBe(4);
    expect(middle.cursorIndex).toBe(2);
  });

  it('clamps an out-of-range ratio', () => {
    const tl = buildOfficeTimeline(timestampedEvents(3));
    const below = scrubOfficeTimeline(tl, { ratio: -0.5 });
    const above = scrubOfficeTimeline(tl, { ratio: 5 });
    expect(below.cursorIndex).toBe(0);
    expect(above.cursorIndex).toBe(2);
  });

  it('scrubs by timestamp (highest frame whose ts <= cursor)', () => {
    const tl = buildOfficeTimeline(timestampedEvents(5));
    const result = scrubOfficeTimeline(tl, { ts: '2026-04-22T12:00:02.500Z' });
    expect(result.cursorIndex).toBe(2);
    expect(result.frame?.eventId).toBe('evt-2');
  });

  it('returns -1 when ts is before the first frame', () => {
    const tl = buildOfficeTimeline(timestampedEvents(3));
    const result = scrubOfficeTimeline(tl, { ts: '2020-01-01T00:00:00.000Z' });
    expect(result.cursorIndex).toBe(-1);
    expect(result.view.empty).toBe(true);
  });

  it('matches the full createOfficeView when scrubbed at the end', () => {
    const tl = buildOfficeTimeline(timestampedEvents(8));
    const fullScrub = scrubOfficeTimeline(tl, { ratio: 1 });
    // The state at ratio=1 must surface both agents (dev + qa).
    const ids = fullScrub.view.characters.map((c) => c.agentId).sort();
    expect(ids).toEqual(['dev', 'qa']);
  });

  it('forwards viewOptions to createOfficeView', () => {
    const tl = buildOfficeTimeline(timestampedEvents(4));
    const result = scrubOfficeTimeline(
      tl,
      { ratio: 1 },
      { grid: { cols: 8, rows: 6 } }
    );
    expect(result.view.grid).toEqual({ cols: 8, rows: 6 });
  });
});
