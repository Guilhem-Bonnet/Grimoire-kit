/**
 * state/flow-graph-view.ts — V3.S3.7 flow projection.
 *
 * Pure projection from a stream of `HookEvent` to a "flow graph" view:
 * events sharing the same `correlation_id` are grouped into a flow.
 * Each flow exposes its participants, phase sequence, scope mix,
 * duration, and first/last timestamps.
 *
 * Stays renderer-agnostic — the output is a plain data structure a
 * dashboard view, a graph library, or a JSON export can consume.
 */

import type { HookEvent } from '../contracts/hookEvents';

export const FLOW_GRAPH_VIEW_SCHEMA_VERSION = '1.0.0';

export interface FlowNode {
  readonly eventId: string;
  readonly ts: string;
  readonly scope: string;
  readonly phase: string;
  readonly sourceHook: string;
  readonly agentId: string | null;
}

export interface FlowGraph {
  readonly correlationId: string;
  readonly firstTs: string;
  readonly lastTs: string;
  readonly durationMs: number;
  readonly nodes: readonly FlowNode[];
  readonly agents: readonly string[];
  readonly scopes: readonly string[];
  readonly phaseCounters: Readonly<Record<string, number>>;
}

export interface FlowGraphView {
  readonly schemaVersion: string;
  readonly flows: readonly FlowGraph[];
  /** Events without a correlation_id, exposed separately. */
  readonly orphans: readonly FlowNode[];
}

export interface FlowGraphViewOptions {
  /**
   * Max flows returned (most recent by `lastTs`). Default: no limit.
   * Useful when the store holds thousands of events.
   */
  readonly limit?: number;
  /** Drop flows with fewer than this many events. Default: 1. */
  readonly minSize?: number;
}

/**
 * Project the event stream into a flow graph view.
 */
export function buildFlowGraphView(
  events: readonly HookEvent[],
  options: FlowGraphViewOptions = {}
): FlowGraphView {
  const minSize = options.minSize ?? 1;
  const buckets = new Map<string, HookEvent[]>();
  const orphans: FlowNode[] = [];

  for (const event of events) {
    const key = event.correlation_id;
    if (!key) {
      orphans.push(toNode(event));
      continue;
    }
    const bucket = buckets.get(key);
    if (bucket) bucket.push(event);
    else buckets.set(key, [event]);
  }

  const flows: FlowGraph[] = [];
  for (const [correlationId, bucket] of buckets) {
    if (bucket.length < minSize) continue;
    bucket.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    const nodes = bucket.map(toNode);
    const agents = uniq(
      nodes.map((n) => n.agentId).filter((a): a is string => a !== null)
    );
    const scopes = uniq(nodes.map((n) => n.scope));
    const phaseCounters: Record<string, number> = {};
    for (const n of nodes) {
      phaseCounters[n.phase] = (phaseCounters[n.phase] ?? 0) + 1;
    }
    const firstTs = nodes[0]!.ts;
    const lastTs = nodes[nodes.length - 1]!.ts;
    flows.push({
      correlationId,
      firstTs,
      lastTs,
      durationMs: Math.max(0, Date.parse(lastTs) - Date.parse(firstTs)),
      nodes,
      agents,
      scopes,
      phaseCounters
    });
  }

  flows.sort((a, b) => (a.lastTs < b.lastTs ? 1 : a.lastTs > b.lastTs ? -1 : 0));
  const limited = options.limit !== undefined ? flows.slice(0, options.limit) : flows;

  return {
    schemaVersion: FLOW_GRAPH_VIEW_SCHEMA_VERSION,
    flows: limited,
    orphans
  };
}

function toNode(event: HookEvent): FlowNode {
  return {
    eventId: event.event_id,
    ts: event.ts,
    scope: event.scope,
    phase: event.phase,
    sourceHook: event.source_hook,
    agentId: event.agent?.id ?? null
  };
}

function uniq(items: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    if (!seen.has(item)) {
      seen.add(item);
      out.push(item);
    }
  }
  return out;
}
