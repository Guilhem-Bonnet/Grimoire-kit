import { describe, expect, it } from 'vitest';

import {
  CARD_ACTIVITY_STATUSES,
  selectCardActivity
} from '../../src/state/card-activity';
import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent,
  type HookEventSnapshot
} from '../../src/contracts/hookEvents';

function makeEvent(overrides: Partial<HookEvent>): HookEvent {
  return {
    schema_version: HOOK_EVENT_SCHEMA_VERSION,
    event_id: 'evt-' + Math.random().toString(36).slice(2, 10),
    ts: '2026-04-22T12:00:00.000Z',
    scope: 'task',
    phase: 'start',
    source_hook: 'test',
    correlation_id: 'corr-1',
    payload: {},
    ...overrides
  };
}

function makeSnapshot(events: HookEvent[]): HookEventSnapshot {
  return {
    schemaVersion: HOOK_EVENT_SCHEMA_VERSION,
    generatedAt: '2026-04-22T13:00:00.000Z',
    events,
    counters: {
      total: events.length,
      byScope: {} as HookEventSnapshot['counters']['byScope'],
      bySourceHook: {}
    }
  };
}

describe('selectCardActivity', () => {
  it('returns queued state when snapshot is empty or missing', () => {
    expect(selectCardActivity(null, 'corr-1').status).toBe('queued');
    expect(selectCardActivity(undefined, 'corr-1').status).toBe('queued');
    expect(selectCardActivity(makeSnapshot([]), 'corr-1').status).toBe('queued');
  });

  it('returns queued when correlationId is blank', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', correlation_id: 'corr-1' })
    ]);
    expect(selectCardActivity(snap, '').status).toBe('queued');
  });

  it('derives running status from task/start', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' })
    ]);
    const activity = selectCardActivity(snap, 'corr-1');
    expect(activity.status).toBe('running');
    expect(activity.startedAt).toBe('2026-04-22T12:00:00.000Z');
    expect(activity.endedAt).toBeNull();
    expect(activity.events).toHaveLength(1);
  });

  it('derives done status from task/end after start', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({ phase: 'end', ts: '2026-04-22T12:05:00.000Z' })
    ]);
    const activity = selectCardActivity(snap, 'corr-1');
    expect(activity.status).toBe('done');
    expect(activity.endedAt).toBe('2026-04-22T12:05:00.000Z');
  });

  it('derives blocked status and counts blocks', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({ phase: 'block', ts: '2026-04-22T12:02:00.000Z' }),
      makeEvent({ phase: 'block', ts: '2026-04-22T12:03:00.000Z' })
    ]);
    const activity = selectCardActivity(snap, 'corr-1');
    expect(activity.status).toBe('blocked');
    expect(activity.blockCount).toBe(2);
  });

  it('re-enters running after a correct phase', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({ phase: 'block', ts: '2026-04-22T12:02:00.000Z' }),
      makeEvent({ phase: 'correct', ts: '2026-04-22T12:04:00.000Z' })
    ]);
    expect(selectCardActivity(snap, 'corr-1').status).toBe('running');
  });

  it('upgrades to errored when an anomaly is attached', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({
        scope: 'anomaly',
        phase: 'info',
        ts: '2026-04-22T12:03:00.000Z'
      })
    ]);
    expect(selectCardActivity(snap, 'corr-1').status).toBe('errored');
  });

  it('errored sticks even if a later end event arrives', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({
        scope: 'anomaly',
        phase: 'info',
        ts: '2026-04-22T12:02:00.000Z'
      }),
      makeEvent({ phase: 'end', ts: '2026-04-22T12:05:00.000Z' })
    ]);
    expect(selectCardActivity(snap, 'corr-1').status).toBe('errored');
  });

  it('ignores events belonging to other correlations', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'start', correlation_id: 'corr-1', ts: '2026-04-22T12:00:00.000Z' }),
      makeEvent({ phase: 'end', correlation_id: 'corr-2', ts: '2026-04-22T12:05:00.000Z' })
    ]);
    const activity = selectCardActivity(snap, 'corr-1');
    expect(activity.status).toBe('running');
    expect(activity.events).toHaveLength(1);
  });

  it('sorts events chronologically regardless of snapshot order', () => {
    const snap = makeSnapshot([
      makeEvent({ phase: 'end', ts: '2026-04-22T12:05:00.000Z' }),
      makeEvent({ phase: 'start', ts: '2026-04-22T12:00:00.000Z' })
    ]);
    const activity = selectCardActivity(snap, 'corr-1');
    expect(activity.events[0]?.phase).toBe('start');
    expect(activity.events[1]?.phase).toBe('end');
    expect(activity.lastActivityTs).toBe('2026-04-22T12:05:00.000Z');
  });

  it('exposes the canonical status taxonomy', () => {
    expect([...CARD_ACTIVITY_STATUSES]).toEqual([
      'queued',
      'running',
      'blocked',
      'done',
      'errored'
    ]);
  });
});
