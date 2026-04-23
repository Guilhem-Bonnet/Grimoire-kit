/**
 * office-debug-panel-view.ts — V3.S3.4 debug panel pure projection.
 *
 * Side panel that lists, per agent visible in the office, the last N raw
 * HookEvents (most-recent first). Pure projection over the same input
 * the office view consumes: agents are derived from event.agent.id and
 * each row keeps only the fields a renderer needs (no payload retention
 * unless ``options.includePayload`` is set).
 *
 * Contract: pure (no DOM, no fs, no clock unless injected via options.now).
 */

import type {
  HookEvent,
  HookEventPhase,
  HookEventScope
} from '../contracts/hookEvents';
import {
  mapEventToState,
  type OfficeCharacterState
} from './office-view';

export interface DebugEventRow {
  eventId: string;
  ts: string;
  scope: HookEventScope;
  phase: HookEventPhase;
  sourceHook: string;
  /** Resulting FSM state for this event (null if scope/phase has no mapping). */
  derivedState: OfficeCharacterState | null;
  correlationId: string | null;
  /** Optional payload — included only when options.includePayload is true. */
  payload?: Record<string, unknown>;
}

export interface DebugAgentRow {
  agentId: string;
  role: string;
  /** Total events (across the input) for this agent, before truncation. */
  eventCount: number;
  /** Last N events for this agent, most-recent first. */
  events: readonly DebugEventRow[];
  /** ISO timestamp of the most-recent event. */
  lastEventTs: string;
  /** Most-recent FSM state for this agent (null when no event maps). */
  lastState: OfficeCharacterState | null;
}

export interface OfficeDebugPanelView {
  schemaVersion: string;
  generatedAt: string | null;
  agents: readonly DebugAgentRow[];
  /** Total events across all agents, before per-agent truncation. */
  totalEvents: number;
  empty: boolean;
}

export interface OfficeDebugPanelOptions {
  /** Max events kept per agent. Defaults to 10. Set 0 to keep all. */
  maxEventsPerAgent?: number;
  /** Max agents listed. Defaults to 32. Set 0 to keep all. */
  maxAgents?: number;
  /** ISO timestamp echoed back as ``generatedAt``. */
  now?: string;
  /** When true, copies event.payload into the rows. */
  includePayload?: boolean;
}

export const OFFICE_DEBUG_PANEL_SCHEMA_VERSION = '1.0.0';
const DEFAULT_MAX_EVENTS_PER_AGENT = 10;
const DEFAULT_MAX_AGENTS = 32;

interface AgentBuffer {
  agentId: string;
  role: string;
  events: HookEvent[];
  lastEventTs: string;
}

function buildRow(event: HookEvent, includePayload: boolean): DebugEventRow {
  const row: DebugEventRow = {
    eventId: event.event_id,
    ts: event.ts,
    scope: event.scope,
    phase: event.phase,
    sourceHook: event.source_hook,
    derivedState: mapEventToState(event.scope, event.phase),
    correlationId: event.correlation_id ?? null
  };
  if (includePayload) {
    row.payload = { ...event.payload };
  }
  return row;
}

export function createOfficeDebugPanelView(
  events: readonly HookEvent[],
  options: OfficeDebugPanelOptions = {}
): OfficeDebugPanelView {
  const maxEventsPerAgent =
    options.maxEventsPerAgent ?? DEFAULT_MAX_EVENTS_PER_AGENT;
  const maxAgents = options.maxAgents ?? DEFAULT_MAX_AGENTS;
  const includePayload = options.includePayload === true;

  const buffers = new Map<string, AgentBuffer>();
  let totalEvents = 0;

  for (const event of events) {
    const agentId = event.agent?.id;
    if (!agentId) continue;
    totalEvents += 1;
    let buffer = buffers.get(agentId);
    if (!buffer) {
      buffer = {
        agentId,
        role: event.agent?.role ?? agentId,
        events: [],
        lastEventTs: event.ts
      };
      buffers.set(agentId, buffer);
    }
    const role = event.agent?.role;
    if (role && role.length > 0) {
      buffer.role = role;
    }
    buffer.events.push(event);
    if (event.ts > buffer.lastEventTs) {
      buffer.lastEventTs = event.ts;
    }
  }

  const agentRows: DebugAgentRow[] = Array.from(buffers.values()).map(
    (buffer) => {
      const sorted = buffer.events
        .slice()
        .sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
      const truncated =
        maxEventsPerAgent > 0
          ? sorted.slice(0, maxEventsPerAgent)
          : sorted;
      const last = sorted[0];
      return {
        agentId: buffer.agentId,
        role: buffer.role,
        eventCount: buffer.events.length,
        events: truncated.map((event) => buildRow(event, includePayload)),
        lastEventTs: buffer.lastEventTs,
        lastState: last
          ? mapEventToState(last.scope, last.phase)
          : null
      };
    }
  );

  agentRows.sort((a, b) => {
    if (a.lastEventTs < b.lastEventTs) return 1;
    if (a.lastEventTs > b.lastEventTs) return -1;
    return a.agentId < b.agentId ? -1 : a.agentId > b.agentId ? 1 : 0;
  });

  const cappedAgents = maxAgents > 0 ? agentRows.slice(0, maxAgents) : agentRows;

  return {
    schemaVersion: OFFICE_DEBUG_PANEL_SCHEMA_VERSION,
    generatedAt: options.now ?? null,
    agents: cappedAgents,
    totalEvents,
    empty: cappedAgents.length === 0
  };
}
