import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DashboardWsClient, type WebSocketLike } from '../src/dashboard-ws-client';
import type { ServerMessage } from '../src/contracts/wsProtocol';

class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  private handlers = new Map<string, Array<(event: unknown) => void>>();
  constructor(public url: string) {}
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.emit('close', {});
  }
  addEventListener(type: 'open' | 'close' | 'error' | 'message', handler: (event: unknown) => void): void {
    const list = this.handlers.get(type) ?? [];
    list.push(handler);
    this.handlers.set(type, list);
  }
  emit(type: string, event: unknown): void {
    for (const handler of this.handlers.get(type) ?? []) handler(event);
  }
}

describe('DashboardWsClient', () => {
  let sockets: FakeSocket[];
  let scheduled: Array<{ fn: () => void; delay: number }>;

  beforeEach(() => {
    sockets = [];
    scheduled = [];
  });

  function makeClient(onMessage: (msg: ServerMessage) => void): DashboardWsClient {
    return new DashboardWsClient({
      url: 'ws://test.local/ws',
      onMessage,
      createSocket: (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s;
      },
      schedule: (fn, delay) => {
        scheduled.push({ fn, delay });
        return () => {};
      }
    });
  }

  it('transitions to open on socket open', () => {
    const states: string[] = [];
    const client = new DashboardWsClient({
      url: 'ws://test.local/ws',
      onMessage: () => {},
      onState: (s) => states.push(s),
      createSocket: (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s;
      },
      schedule: () => () => {}
    });
    client.start();
    sockets[0]!.readyState = 1;
    sockets[0]!.emit('open', {});
    expect(states).toContain('connecting');
    expect(states).toContain('open');
  });

  it('parses valid messages through Zod', () => {
    const received: ServerMessage[] = [];
    const client = makeClient((m) => received.push(m));
    client.start();
    sockets[0]!.emit('open', {});
    sockets[0]!.emit('message', {
      data: JSON.stringify({
        type: 'hello',
        protocol_version: '1.0',
        server_id: 'test',
        source: '/tmp/a.jsonl',
        replay_size: 5
      })
    });
    expect(received).toHaveLength(1);
    expect(received[0]!.type).toBe('hello');
  });

  it('ignores malformed payloads', () => {
    const received: ServerMessage[] = [];
    const client = makeClient((m) => received.push(m));
    client.start();
    sockets[0]!.emit('message', { data: 'not json' });
    sockets[0]!.emit('message', { data: JSON.stringify({ type: 'unknown' }) });
    expect(received).toHaveLength(0);
  });

  it('schedules reconnect with increasing backoff on close', () => {
    const client = makeClient(() => {});
    client.start();
    sockets[0]!.emit('close', {});
    sockets[0]!.emit('close', {});
    expect(scheduled.length).toBeGreaterThanOrEqual(2);
    expect(scheduled[0]!.delay).toBeLessThanOrEqual(scheduled[1]!.delay);
  });

  it('refuses to send when not open', () => {
    const client = makeClient(() => {});
    client.start();
    const ok = client.send({ type: 'subscribe', scopes: ['tool'] });
    expect(ok).toBe(false);
  });
});
