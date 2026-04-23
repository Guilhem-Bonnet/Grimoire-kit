import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import WebSocket from 'ws';
import { startDashboardServer, type DashboardServerHandle } from '../server/index';

function wait(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function collect(url: string, count: number, timeoutMs: number): Promise<unknown[]> {
  const messages: unknown[] = [];
  const ws = new WebSocket(url);
  ws.on('message', (data) => {
    messages.push(JSON.parse(data.toString()));
  });
  await new Promise<void>((resolve, reject) => {
    ws.once('open', () => resolve());
    ws.once('error', reject);
  });
  const start = Date.now();
  await new Promise<void>((resolve) => {
    const check = setInterval(() => {
      if (messages.length >= count || Date.now() - start > timeoutMs) {
        clearInterval(check);
        resolve();
      }
    }, 25);
  });
  ws.close();
  return messages;
}

describe('dashboard server — WS live tail', () => {
  let tmpDir: string;
  let activityFile: string;
  let handle: DashboardServerHandle;

  beforeEach(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-srv-'));
    activityFile = path.join(tmpDir, 'activity.jsonl');
    fs.writeFileSync(
      activityFile,
      JSON.stringify({
        schema_version: '1.0',
        event_id: 'evt-seed',
        ts: '2026-04-01T00:00:00.000Z',
        scope: 'session',
        phase: 'start',
        source_hook: 'seed',
        session_id: 'sess-0',
        payload: {}
      }) + '\n'
    );
    handle = await startDashboardServer({
      port: 0 === 0 ? 41750 + Math.floor(Math.random() * 100) : 4175,
      host: '127.0.0.1',
      activityFile,
      replaySize: 50
    });
  });

  afterEach(async () => {
    await handle.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it('sends hello + snapshot on connect', async () => {
    const url = `ws://${handle.host}:${handle.port}/ws`;
    const msgs = await collect(url, 2, 2000);
    expect(msgs).toHaveLength(2);
    expect((msgs[0] as { type: string }).type).toBe('hello');
    expect((msgs[1] as { type: string }).type).toBe('snapshot');
    const snap = msgs[1] as { type: 'snapshot'; events: unknown[] };
    expect(snap.events).toHaveLength(1);
  });

  it('broadcasts new lines appended after connect', async () => {
    const url = `ws://${handle.host}:${handle.port}/ws`;
    const ws = new WebSocket(url);
    const received: { type: string }[] = [];
    await new Promise<void>((resolve, reject) => {
      ws.once('open', () => resolve());
      ws.once('error', reject);
    });
    ws.on('message', (data) => received.push(JSON.parse(data.toString())));
    // Drain hello + snapshot.
    await wait(200);
    fs.appendFileSync(
      activityFile,
      JSON.stringify({
        schema_version: '1.0',
        event_id: 'evt-live',
        ts: '2026-04-01T00:01:00.000Z',
        scope: 'tool',
        phase: 'end',
        source_hook: 'live',
        session_id: 'sess-0',
        payload: { ok: true }
      }) + '\n'
    );
    // Poll interval is 500ms in the server.
    await wait(1500);
    const eventMessages = received.filter((m) => m.type === 'event');
    expect(eventMessages.length).toBeGreaterThanOrEqual(1);
    ws.close();
  });

  it('rejects unknown commands with not_implemented', async () => {
    const url = `ws://${handle.host}:${handle.port}/ws`;
    const ws = new WebSocket(url);
    const received: { type: string; code?: string }[] = [];
    await new Promise<void>((resolve, reject) => {
      ws.once('open', () => resolve());
      ws.once('error', reject);
    });
    ws.on('message', (data) => received.push(JSON.parse(data.toString())));
    await wait(150);
    ws.send(JSON.stringify({ type: 'command', id: 'x1', verb: 'agent.start' }));
    await wait(300);
    const err = received.find((m) => m.type === 'error');
    expect(err?.code).toBe('not_implemented');
    ws.close();
  });
});
