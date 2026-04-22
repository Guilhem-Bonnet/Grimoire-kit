import { describe, expect, it, vi } from 'vitest';

import { HookEventsClient, HOOK_EVENTS_MOUNT_ID } from '../../app/hook-events-client';
import { HOOK_EVENT_SCHEMA_VERSION, type HookEvent } from '../../src/contracts/hookEvents';

function buildSnapshot(events: HookEvent[] = []) {
  return {
    schemaVersion: HOOK_EVENT_SCHEMA_VERSION,
    generatedAt: '2026-04-21T10:00:00.000Z',
    events,
    counters: {
      total: events.length,
      byScope: events.reduce<Record<string, Record<string, number>>>((acc, e) => {
        const entry = acc[e.scope] ?? {};
        entry[e.phase] = (entry[e.phase] ?? 0) + 1;
        acc[e.scope] = entry;
        return acc;
      }, {}),
      bySourceHook: events.reduce<Record<string, number>>((acc, e) => {
        acc[e.source_hook] = (acc[e.source_hook] ?? 0) + 1;
        return acc;
      }, {})
    }
  };
}

function sampleEvent(overrides: Partial<HookEvent> = {}): HookEvent {
  return {
    schema_version: HOOK_EVENT_SCHEMA_VERSION,
    event_id: '01234567-89ab-cdef-0123-456789abcdef',
    ts: '2026-04-21T09:59:59.000Z',
    scope: 'tool',
    phase: 'info',
    source_hook: 'grimoire-post-edit',
    payload: {},
    ...overrides
  };
}

interface MountStub {
  innerHTML: string;
}

function makeDocStub(hasMount: boolean): { doc: Document; mount: MountStub | null } {
  const mount: MountStub | null = hasMount ? { innerHTML: '' } : null;
  const doc = {
    getElementById: (id: string) => (id === HOOK_EVENTS_MOUNT_ID ? mount : null)
  } as unknown as Document;
  return { doc, mount };
}

describe('HookEventsClient', () => {
  it('renders counters table when snapshot is fetched', async () => {
    const { doc, mount } = makeDocStub(true);
    const snapshot = buildSnapshot([sampleEvent(), sampleEvent({ phase: 'block' })]);
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => snapshot
    } as Response);
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();

    expect(mount?.innerHTML).toContain('Ledger runtime');
    expect(mount?.innerHTML).toContain('grimoire-post-edit');
    expect(mount?.innerHTML).toContain('2 evenement(s)');
  });

  it('displays neutral state when fetch fails initially', async () => {
    const { doc, mount } = makeDocStub(true);
    const fetcher = vi.fn().mockRejectedValue(new Error('network down'));
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();

    expect(mount?.innerHTML).toContain('Ledger runtime indisponible');
    expect(mount?.innerHTML).toContain('network down');
  });

  it('keeps last snapshot after a transient fetch error', async () => {
    const { doc, mount } = makeDocStub(true);
    const snapshot = buildSnapshot([sampleEvent()]);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => snapshot } as Response)
      .mockRejectedValueOnce(new Error('blip'));
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();
    await client.tick();

    expect(client.getState().snapshot).toEqual(snapshot);
    expect(client.getState().error).toBe('blip');
    expect(mount?.innerHTML).toContain('fetch: blip');
  });

  it('rejects unsupported schema version', async () => {
    const { doc, mount } = makeDocStub(true);
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ...buildSnapshot(), schemaVersion: '9.9' })
    } as Response);
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();

    expect(mount?.innerHTML).toContain('Schema version 9.9 non supportee');
  });

  it('is a no-op when mount is absent', async () => {
    const { doc } = makeDocStub(false);
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => buildSnapshot()
    } as Response);
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await expect(client.tick()).resolves.toBeUndefined();
    expect(client.getState().snapshot).not.toBeNull();
  });

  it('escapes HTML in event fields', async () => {
    const { doc, mount } = makeDocStub(true);
    const snapshot = buildSnapshot([sampleEvent({ source_hook: '<script>evil</script>' })]);
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => snapshot
    } as Response);
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();

    expect(mount?.innerHTML).not.toContain('<script>evil');
    expect(mount?.innerHTML).toContain('&lt;script&gt;evil&lt;/script&gt;');
  });

  it('start is idempotent; stop halts polling', () => {
    vi.useFakeTimers();
    try {
      const { doc } = makeDocStub(true);
      const fetcher = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => buildSnapshot()
      } as Response);
      const client = new HookEventsClient({ fetcher, documentRef: doc, intervalMs: 5_000 });
      client.start();
      client.start();
      expect(fetcher).toHaveBeenCalledTimes(1);
      client.stop();
      client.stop();
      vi.advanceTimersByTime(20_000);
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('degrades gracefully on non-2xx response', async () => {
    const { doc, mount } = makeDocStub(true);
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({})
    } as Response);
    const client = new HookEventsClient({ fetcher, documentRef: doc });
    await client.tick();

    expect(client.getState().error).toContain('404');
    expect(mount?.innerHTML).toContain('Ledger runtime indisponible');
  });
});
