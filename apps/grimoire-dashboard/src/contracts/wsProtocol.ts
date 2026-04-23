import { z } from 'zod';

/**
 * Server → Client messages.
 *
 * The dashboard reads `activity.jsonl` in tail mode; each new line is
 * parsed and forwarded as an `event` message. On first connect the
 * server replays the last N events as a `snapshot` so the UI can hydrate
 * without waiting for the next tail tick.
 *
 * All messages are Zod-validated on both ends to guarantee the WS
 * contract stays typed end-to-end.
 */
export const serverHelloSchema = z.object({
  type: z.literal('hello'),
  protocol_version: z.literal('1.0'),
  server_id: z.string(),
  source: z.string(), // absolute path of the tailed file
  replay_size: z.number().int().nonnegative()
});

export const serverSnapshotSchema = z.object({
  type: z.literal('snapshot'),
  events: z.array(z.record(z.string(), z.unknown()))
});

export const serverEventSchema = z.object({
  type: z.literal('event'),
  event: z.record(z.string(), z.unknown())
});

export const serverErrorSchema = z.object({
  type: z.literal('error'),
  code: z.string(),
  message: z.string()
});

export const serverMessageSchema = z.discriminatedUnion('type', [
  serverHelloSchema,
  serverSnapshotSchema,
  serverEventSchema,
  serverErrorSchema
]);

export type ServerHello = z.infer<typeof serverHelloSchema>;
export type ServerSnapshot = z.infer<typeof serverSnapshotSchema>;
export type ServerEvent = z.infer<typeof serverEventSchema>;
export type ServerError = z.infer<typeof serverErrorSchema>;
export type ServerMessage = z.infer<typeof serverMessageSchema>;

/**
 * Client → Server messages.
 *
 * `subscribe` is idempotent and restricts the stream to the scopes listed.
 * `command` is the placeholder for the future pilot-control channel (start
 * agent, dispatch, etc.) — v0 just logs and returns an `error` with
 * `code: 'not_implemented'`.
 */
export const clientSubscribeSchema = z.object({
  type: z.literal('subscribe'),
  scopes: z.array(z.string()).optional()
});

export const clientCommandSchema = z.object({
  type: z.literal('command'),
  id: z.string(),
  verb: z.string(),
  args: z.record(z.string(), z.unknown()).optional()
});

export const clientMessageSchema = z.discriminatedUnion('type', [
  clientSubscribeSchema,
  clientCommandSchema
]);

export type ClientSubscribe = z.infer<typeof clientSubscribeSchema>;
export type ClientCommand = z.infer<typeof clientCommandSchema>;
export type ClientMessage = z.infer<typeof clientMessageSchema>;

export const DASHBOARD_PROTOCOL_VERSION = '1.0';
