/**
 * dashboard-ws-client.ts — browser WebSocket client with auto-reconnect.
 *
 * Parses server messages via Zod and funnels them to typed callbacks.
 * Reconnect uses capped exponential backoff (1s → 2s → 4s → 8s → 15s).
 *
 * Contract: pure of DOM. Any rendering happens in the store layer.
 */

import { z } from 'zod';

import {
  clientMessageSchema,
  serverMessageSchema,
  type ClientMessage,
  type ServerMessage
} from './contracts/wsProtocol';

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

export interface DashboardWsClientOptions {
  url: string;
  onMessage: (msg: ServerMessage) => void;
  onState?: (state: ConnectionState, detail?: string) => void;
  /** Dependency injection for tests. */
  createSocket?: (url: string) => WebSocketLike;
  /** Dependency injection for tests. */
  schedule?: (fn: () => void, delayMs: number) => () => void;
  /** Override the backoff ladder (ms). */
  backoffLadder?: readonly number[];
}

export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  addEventListener(type: 'open' | 'close' | 'error' | 'message', handler: (event: unknown) => void): void;
}

const DEFAULT_BACKOFF = [1_000, 2_000, 4_000, 8_000, 15_000] as const;

function defaultSchedule(fn: () => void, delayMs: number): () => void {
  const handle = setTimeout(fn, delayMs);
  return () => clearTimeout(handle);
}

function defaultCreateSocket(url: string): WebSocketLike {
  // eslint-disable-next-line no-undef
  return new WebSocket(url) as unknown as WebSocketLike;
}

export class DashboardWsClient {
  private readonly url: string;
  private readonly onMessage: (msg: ServerMessage) => void;
  private readonly onState: (state: ConnectionState, detail?: string) => void;
  private readonly createSocket: (url: string) => WebSocketLike;
  private readonly schedule: (fn: () => void, delayMs: number) => () => void;
  private readonly backoff: readonly number[];

  private socket: WebSocketLike | null = null;
  private attempt = 0;
  private cancelReconnect: (() => void) | null = null;
  private closed = false;
  private state: ConnectionState = 'idle';

  constructor(options: DashboardWsClientOptions) {
    this.url = options.url;
    this.onMessage = options.onMessage;
    this.onState = options.onState ?? (() => {});
    this.createSocket = options.createSocket ?? defaultCreateSocket;
    this.schedule = options.schedule ?? defaultSchedule;
    this.backoff = options.backoffLadder ?? DEFAULT_BACKOFF;
  }

  getState(): ConnectionState {
    return this.state;
  }

  start(): void {
    if (this.closed) return;
    this.connect();
  }

  send(message: ClientMessage): boolean {
    if (!this.socket || this.state !== 'open') return false;
    const check = clientMessageSchema.safeParse(message);
    if (!check.success) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  close(): void {
    this.closed = true;
    if (this.cancelReconnect) {
      this.cancelReconnect();
      this.cancelReconnect = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setState('closed');
  }

  private connect(): void {
    this.setState('connecting');
    const socket = this.createSocket(this.url);
    this.socket = socket;

    socket.addEventListener('open', () => {
      this.attempt = 0;
      this.setState('open');
    });

    socket.addEventListener('message', (event: unknown) => {
      const raw = this.extractData(event);
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw) as unknown;
        const validated = serverMessageSchema.safeParse(parsed);
        if (!validated.success) {
          this.setState('error', 'invalid message from server');
          return;
        }
        this.onMessage(validated.data);
      } catch (err) {
        this.setState('error', err instanceof z.ZodError ? err.message : 'parse error');
      }
    });

    const reconnect = (): void => {
      if (this.closed) return;
      const delay = this.backoff[Math.min(this.attempt, this.backoff.length - 1)] ?? 15_000;
      this.attempt += 1;
      this.setState('closed', `reconnect in ${delay}ms`);
      this.cancelReconnect = this.schedule(() => {
        this.cancelReconnect = null;
        this.connect();
      }, delay);
    };

    socket.addEventListener('close', reconnect);
    socket.addEventListener('error', () => {
      this.setState('error');
    });
  }

  private extractData(event: unknown): string | null {
    if (typeof event === 'object' && event !== null && 'data' in event) {
      const data = (event as { data: unknown }).data;
      if (typeof data === 'string') return data;
    }
    return null;
  }

  private setState(state: ConnectionState, detail?: string): void {
    this.state = state;
    this.onState(state, detail);
  }
}
