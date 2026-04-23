import { describe, it, expect, beforeEach } from 'vitest';
import { DashboardStore } from '../src/dashboard-store';
import type { ServerEvent, ServerHello, ServerSnapshot } from '../src/contracts/wsProtocol';

function makeEvent(partial: Partial<Record<string, unknown>>): Record<string, unknown> {
  const base = {
    schema_version: '1.0',
    event_id: `evt-${Math.random().toString(36).slice(2, 10)}`,
    ts: new Date().toISOString(),
    scope: 'tool',
    phase: 'start',
    source_hook: 'test.hook',
    session_id: 'sess-1',
    agent: { id: 'dev' },
    payload: {}
  };
  return { ...base, ...partial };
}

describe('DashboardStore', () => {
  let store: DashboardStore;

  beforeEach(() => {
    store = new DashboardStore({ bufferSize: 50 });
  });

  it('starts idle with empty office', () => {
    const snap = store.getState();
    expect(snap.connection).toBe('idle');
    expect(snap.events).toHaveLength(0);
    expect(snap.office.characters).toHaveLength(0);
  });

  it('hydrates from snapshot and projects office', () => {
    const msg: ServerSnapshot = {
      type: 'snapshot',
      events: [
        makeEvent({ agent: { id: 'dev' }, scope: 'subagent', phase: 'start' }),
        makeEvent({ agent: { id: 'qa' }, scope: 'subagent', phase: 'start' })
      ]
    };
    store.onServerMessage(msg);
    const snap = store.getState();
    expect(snap.events.length).toBeGreaterThanOrEqual(2);
    expect(snap.office.characters.length).toBeGreaterThanOrEqual(2);
    expect(snap.placement.seats.size).toBe(snap.office.characters.length);
  });

  it('appends live events without dropping the prior snapshot', () => {
    store.onServerMessage({
      type: 'snapshot',
      events: [makeEvent({ agent: { id: 'dev' } })]
    } satisfies ServerSnapshot);
    const before = store.getState().events.length;
    store.onServerMessage({
      type: 'event',
      event: makeEvent({ agent: { id: 'qa' }, phase: 'end' })
    } satisfies ServerEvent);
    expect(store.getState().events.length).toBe(before + 1);
  });

  it('records source on hello', () => {
    store.onServerMessage({
      type: 'hello',
      protocol_version: '1.0',
      server_id: 'srv-test',
      source: '/tmp/activity.jsonl',
      replay_size: 10
    } satisfies ServerHello);
    expect(store.getState().source).toBe('/tmp/activity.jsonl');
  });

  it('counts invalid events as dropped', () => {
    store.onServerMessage({
      type: 'event',
      event: { not: 'a hook event' }
    } satisfies ServerEvent);
    expect(store.getState().droppedCount).toBe(1);
    expect(store.getState().events).toHaveLength(0);
  });

  it('caps the ring buffer at bufferSize', () => {
    const events = Array.from({ length: 80 }, (_, i) =>
      makeEvent({ agent: { id: `agent-${i % 4}` }, event_id: `evt-${i}` })
    );
    store.onServerMessage({ type: 'snapshot', events } satisfies ServerSnapshot);
    expect(store.getState().events.length).toBe(50);
  });

  it('notifies subscribers on connection state change', () => {
    const seen: string[] = [];
    store.subscribe((snap) => seen.push(snap.connection));
    store.onConnectionState('connecting');
    store.onConnectionState('open');
    expect(seen).toContain('connecting');
    expect(seen).toContain('open');
  });
});
