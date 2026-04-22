/**
 * hookEvents.ts — mirror of grimoire.tools.events schema (Python side).
 *
 * Contract source of truth : grimoire-kit/src/grimoire/tools/events.py
 * (SCHEMA_VERSION "1.0"). This module provides the TypeScript reader for
 * the shared ledger written at
 * ``_grimoire-runtime/_memory/activity.jsonl`` by the hook gateway.
 *
 * Keep in sync : any change to the Python schema must be reflected here
 * and vice-versa; the contract tests enforce round-trip compatibility.
 */

import { z } from 'zod';

export const HOOK_EVENT_SCHEMA_VERSION = '1.0';

export const HOOK_EVENT_SCOPES = [
  'session',
  'prompt',
  'tool',
  'subagent',
  'compact',
  'stop',
  'task',
  'anomaly'
] as const;

export const HOOK_EVENT_PHASES = [
  'start',
  'end',
  'block',
  'correct',
  'info'
] as const;

export type HookEventScope = (typeof HOOK_EVENT_SCOPES)[number];
export type HookEventPhase = (typeof HOOK_EVENT_PHASES)[number];

const HookAgentSchema = z
  .object({
    id: z.string().optional(),
    role: z.string().optional(),
    parent: z.string().optional()
  })
  .passthrough();

export const HookEventSchema = z.object({
  schema_version: z.string(),
  event_id: z.string().min(1),
  ts: z.string().min(1),
  scope: z.enum(HOOK_EVENT_SCOPES),
  phase: z.enum(HOOK_EVENT_PHASES),
  source_hook: z.string().min(1),
  agent: HookAgentSchema.nullable().optional(),
  correlation_id: z.string().nullable().optional(),
  payload: z.record(z.unknown()).default({})
});

export type HookEvent = z.infer<typeof HookEventSchema>;

export interface HookEventFilter {
  sinceTs?: string;
  scope?: HookEventScope;
  limit?: number;
}

export interface HookEventCounters {
  total: number;
  byScope: Record<HookEventScope, Partial<Record<HookEventPhase, number>>>;
  bySourceHook: Record<string, number>;
}

/** Parse a single JSONL line into a validated HookEvent. Throws on failure. */
export function parseHookEventLine(line: string): HookEvent {
  const trimmed = line.trim();
  if (!trimmed) {
    throw new Error('empty line');
  }
  const data = JSON.parse(trimmed) as unknown;
  return HookEventSchema.parse(data);
}

/**
 * Parse a blob of JSONL text; returns only valid events. Invalid lines are
 * silently skipped (mirrors the Python reader behaviour which quarantines
 * errors but never blocks the pipeline).
 */
export function parseHookEventsJsonl(jsonl: string): HookEvent[] {
  const events: HookEvent[] = [];
  for (const rawLine of jsonl.split('\n')) {
    try {
      events.push(parseHookEventLine(rawLine));
    } catch {
      // ignore malformed lines — the Python side has already written them
      // to events-errors.jsonl.
    }
  }
  return events;
}

/** Apply filters to a parsed list. */
export function filterHookEvents(
  events: readonly HookEvent[],
  filter: HookEventFilter = {}
): HookEvent[] {
  let out = events.slice();
  if (filter.sinceTs) {
    const cutoff = filter.sinceTs;
    out = out.filter((event) => event.ts > cutoff);
  }
  if (filter.scope) {
    const wanted = filter.scope;
    out = out.filter((event) => event.scope === wanted);
  }
  if (typeof filter.limit === 'number' && filter.limit >= 0) {
    out = out.slice(-filter.limit);
  }
  return out;
}

/** Compute roll-up counters for observability surfaces. */
export function computeHookEventCounters(
  events: readonly HookEvent[]
): HookEventCounters {
  const byScope = {} as HookEventCounters['byScope'];
  const bySourceHook: Record<string, number> = {};
  for (const event of events) {
    const scopeBucket = byScope[event.scope] ?? {};
    scopeBucket[event.phase] = (scopeBucket[event.phase] ?? 0) + 1;
    byScope[event.scope] = scopeBucket;
    bySourceHook[event.source_hook] = (bySourceHook[event.source_hook] ?? 0) + 1;
  }
  return {
    total: events.length,
    byScope,
    bySourceHook
  };
}

/**
 * Serialize a filtered + aggregated snapshot for static publication
 * (``.generated/public/hook-events.json``). Consumed by the browser via
 * the polling client.
 */
export interface HookEventSnapshot {
  schemaVersion: string;
  generatedAt: string;
  events: HookEvent[];
  counters: HookEventCounters;
}

export function createHookEventSnapshot(
  events: readonly HookEvent[],
  options: { limit?: number; generatedAt?: string } = {}
): HookEventSnapshot {
  const limited = filterHookEvents(events, { limit: options.limit ?? 500 });
  return {
    schemaVersion: HOOK_EVENT_SCHEMA_VERSION,
    generatedAt: options.generatedAt ?? new Date().toISOString(),
    events: limited,
    counters: computeHookEventCounters(limited)
  };
}
