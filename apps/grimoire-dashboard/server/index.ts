/**
 * server/index.ts — tails activity.jsonl and broadcasts over WebSocket.
 *
 * This is the **live data backend** of the grimoire-dashboard (v0).
 *
 * Scope of v0:
 *   - read-only tail of `_grimoire-runtime/_memory/activity.jsonl`
 *   - last N events replayed on connect (default 200)
 *   - newline-delimited JSON parsed and re-emitted one-by-one
 *   - simple resilient polling (no fs.watch — activity.jsonl is append-only
 *     so file-size tailing is enough and portable across filesystems)
 *
 * NOT in v0:
 *   - command channel (write path to runtime). The protocol has a
 *     `command` message but the server replies with `error:not_implemented`.
 *     That channel will be wired to real runtime APIs in a later iteration.
 *   - authentication. The server only binds to 127.0.0.1 by default.
 */

import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { WebSocketServer, type WebSocket } from 'ws';

import {
  DASHBOARD_PROTOCOL_VERSION,
  clientMessageSchema,
  type ServerMessage
} from '../src/contracts/wsProtocol.js';

const DEFAULT_PORT = Number.parseInt(process.env.GRIMOIRE_DASHBOARD_PORT ?? '4175', 10);
const DEFAULT_HOST = process.env.GRIMOIRE_DASHBOARD_HOST ?? '127.0.0.1';
const DEFAULT_REPLAY_SIZE = Number.parseInt(process.env.GRIMOIRE_DASHBOARD_REPLAY ?? '200', 10);
const POLL_INTERVAL_MS = 500;

function resolveActivityFile(): string {
  if (process.env.GRIMOIRE_ACTIVITY_FILE) {
    return path.resolve(process.env.GRIMOIRE_ACTIVITY_FILE);
  }
  // Default: walk up from this file (apps/grimoire-dashboard/server/) to the
  // monorepo root, then to `_grimoire-runtime/_memory/activity.jsonl`.
  const here = path.resolve(__dirname ?? process.cwd());
  const candidates = [
    path.resolve(here, '../../..', '_grimoire-runtime/_memory/activity.jsonl'),
    path.resolve(here, '../../../..', '_grimoire-runtime/_memory/activity.jsonl'),
    path.resolve(process.cwd(), '_grimoire-runtime/_memory/activity.jsonl')
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0]!; // will trigger an error message on read
}

function readLastLines(file: string, count: number): string[] {
  if (!fs.existsSync(file)) return [];
  const raw = fs.readFileSync(file, 'utf8');
  const lines = raw.split('\n').filter((line) => line.trim().length > 0);
  return lines.slice(-count);
}

function parseLine(line: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(line);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

interface TailState {
  offset: number;
  pending: string;
}

function tailFile(file: string, state: TailState): string[] {
  if (!fs.existsSync(file)) return [];
  const stat = fs.statSync(file);
  if (stat.size < state.offset) {
    // File truncated or rotated — reset.
    state.offset = 0;
    state.pending = '';
  }
  if (stat.size === state.offset) return [];
  const fd = fs.openSync(file, 'r');
  const toRead = stat.size - state.offset;
  const buffer = Buffer.alloc(toRead);
  try {
    fs.readSync(fd, buffer, 0, toRead, state.offset);
  } finally {
    fs.closeSync(fd);
  }
  state.offset = stat.size;
  const chunk = state.pending + buffer.toString('utf8');
  const parts = chunk.split('\n');
  state.pending = parts.pop() ?? '';
  return parts.filter((line) => line.trim().length > 0);
}

function sendJson(ws: WebSocket, message: ServerMessage): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(message));
  }
}

export interface DashboardServerHandle {
  port: number;
  host: string;
  activityFile: string;
  close: () => Promise<void>;
}

export function startDashboardServer(
  options: { port?: number; host?: string; activityFile?: string; replaySize?: number } = {}
): Promise<DashboardServerHandle> {
  const port = options.port ?? DEFAULT_PORT;
  const host = options.host ?? DEFAULT_HOST;
  const activityFile = options.activityFile ?? resolveActivityFile();
  const replaySize = options.replaySize ?? DEFAULT_REPLAY_SIZE;
  const serverId = `dashboard-${process.pid}-${Date.now()}`;

  const httpServer = http.createServer((req, res) => {
    if (req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          status: 'ok',
          protocol_version: DASHBOARD_PROTOCOL_VERSION,
          activity_file: activityFile,
          exists: fs.existsSync(activityFile)
        })
      );
      return;
    }
    res.writeHead(404);
    res.end();
  });

  const wss = new WebSocketServer({ server: httpServer, path: '/ws' });
  const clients = new Set<WebSocket>();
  const state: TailState = { offset: 0, pending: '' };

  if (fs.existsSync(activityFile)) {
    state.offset = fs.statSync(activityFile).size;
  }

  wss.on('connection', (ws) => {
    clients.add(ws);
    sendJson(ws, {
      type: 'hello',
      protocol_version: DASHBOARD_PROTOCOL_VERSION,
      server_id: serverId,
      source: activityFile,
      replay_size: replaySize
    });

    const replay = readLastLines(activityFile, replaySize)
      .map(parseLine)
      .filter((e): e is Record<string, unknown> => e !== null);
    sendJson(ws, { type: 'snapshot', events: replay });

    ws.on('message', (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data.toString());
      } catch {
        sendJson(ws, { type: 'error', code: 'invalid_json', message: 'message is not valid JSON' });
        return;
      }
      const result = clientMessageSchema.safeParse(parsed);
      if (!result.success) {
        sendJson(ws, {
          type: 'error',
          code: 'invalid_message',
          message: result.error.message
        });
        return;
      }
      const msg = result.data;
      if (msg.type === 'command') {
        sendJson(ws, {
          type: 'error',
          code: 'not_implemented',
          message: `command '${msg.verb}' is not wired in v0`
        });
      }
      // subscribe is currently a no-op (everything is broadcast); future
      // server will use msg.scopes to filter.
    });

    ws.on('close', () => {
      clients.delete(ws);
    });
  });

  const pollTimer = setInterval(() => {
    const lines = tailFile(activityFile, state);
    if (lines.length === 0) return;
    for (const line of lines) {
      const event = parseLine(line);
      if (!event) continue;
      for (const client of clients) {
        sendJson(client, { type: 'event', event });
      }
    }
  }, POLL_INTERVAL_MS);

  return new Promise((resolve, reject) => {
    httpServer.once('error', reject);
    httpServer.listen(port, host, () => {
      httpServer.off('error', reject);
      resolve({
        port,
        host,
        activityFile,
        close: async () => {
          clearInterval(pollTimer);
          for (const client of clients) client.close();
          await new Promise<void>((r) => wss.close(() => r()));
          await new Promise<void>((r) => httpServer.close(() => r()));
        }
      });
    });
  });
}

// CLI entrypoint.
const isDirectRun =
  import.meta.url === `file://${process.argv[1]}` ||
  process.argv[1]?.endsWith('server/index.ts') === true;
if (isDirectRun) {
  startDashboardServer()
    .then((handle) => {
      console.log(
        `[dashboard] listening on http://${handle.host}:${handle.port} — tail ${handle.activityFile}`
      );
    })
    .catch((err: unknown) => {
      console.error('[dashboard] failed to start:', err);
      process.exit(1);
    });
}
