import type { JsonValue } from '../contracts/events';

export type AgentConnectionStatus = 'live' | 'stale' | 'disconnected';

export interface AgentConnectionSample {
  agentId: string;
  timestamp: string;
}

export interface AgentConnectionHealthEntry {
  agentId: string;
  found: boolean;
  path: string;
  parsedLineCount: number;
  lastDataAt: string | null;
  scannedAt: string;
  staleAfterMs: number;
  ageMs: number | null;
  status: AgentConnectionStatus;
}

export interface AgentConnectionHealthSnapshot {
  found: boolean;
  path: string;
  parsedLineCount: number;
  lastDataAt: string | null;
  scannedAt: string;
  staleAfterMs: number;
  ageMs: number | null;
  status: AgentConnectionStatus;
  byAgent: Record<string, AgentConnectionHealthEntry>;
}

export interface CreateAgentConnectionHealthSnapshotOptions {
  found: boolean;
  path: string;
  scannedAt?: string;
  staleAfterMs: number;
  knownAgentIds?: readonly string[];
}

export const DEFAULT_CONNECTION_STALE_AFTER_MS = 5_000;

export function createAgentConnectionHealthSnapshot(
  samples: readonly AgentConnectionSample[],
  options: CreateAgentConnectionHealthSnapshotOptions
): AgentConnectionHealthSnapshot {
  const scannedAt = options.scannedAt ?? new Date().toISOString();
  const knownAgentIds = new Set<string>(options.knownAgentIds ?? []);
  const byAgentMutable: Record<
    string,
    {
      parsedLineCount: number;
      lastDataAt: string | null;
    }
  > = {};

  for (const sample of samples) {
    knownAgentIds.add(sample.agentId);
    const current = byAgentMutable[sample.agentId] ?? { parsedLineCount: 0, lastDataAt: null };
    const nextLastDataAt =
      current.lastDataAt === null || compareIsoTimestamp(sample.timestamp, current.lastDataAt) > 0
        ? sample.timestamp
        : current.lastDataAt;
    byAgentMutable[sample.agentId] = {
      parsedLineCount: current.parsedLineCount + 1,
      lastDataAt: nextLastDataAt
    };
  }

  const lastDataAt = samples.reduce<string | null>((currentMax, sample) => {
    if (currentMax === null) {
      return sample.timestamp;
    }
    return compareIsoTimestamp(sample.timestamp, currentMax) > 0 ? sample.timestamp : currentMax;
  }, null);
  const globalStatus = inferStatus(options.found, lastDataAt, scannedAt, options.staleAfterMs);

  const byAgent: Record<string, AgentConnectionHealthEntry> = {};
  for (const agentId of [...knownAgentIds].sort((left, right) => left.localeCompare(right))) {
    const stats = byAgentMutable[agentId] ?? { parsedLineCount: 0, lastDataAt: null };
    const status = inferStatus(options.found, stats.lastDataAt, scannedAt, options.staleAfterMs);
    byAgent[agentId] = {
      agentId,
      found: options.found,
      path: options.path,
      parsedLineCount: stats.parsedLineCount,
      lastDataAt: stats.lastDataAt,
      scannedAt,
      staleAfterMs: options.staleAfterMs,
      ageMs: status.ageMs,
      status: status.status
    };
  }

  return {
    found: options.found,
    path: options.path,
    parsedLineCount: samples.length,
    lastDataAt,
    scannedAt,
    staleAfterMs: options.staleAfterMs,
    ageMs: globalStatus.ageMs,
    status: globalStatus.status,
    byAgent
  };
}

export function serializeAgentConnectionHealthToConfig(
  snapshot: AgentConnectionHealthSnapshot
): Record<string, JsonValue> {
  return {
    'live.connection.path': snapshot.path,
    'live.connection.found': snapshot.found,
    'live.connection.parsedLineCount': snapshot.parsedLineCount,
    'live.connection.lastDataAt': snapshot.lastDataAt,
    'live.connection.scannedAt': snapshot.scannedAt,
    'live.connection.staleAfterMs': snapshot.staleAfterMs,
    'live.connection.ageMs': snapshot.ageMs,
    'live.connection.status': snapshot.status,
    'live.connection.byAgent': Object.fromEntries(
      Object.entries(snapshot.byAgent).map(([agentId, value]) => [
        agentId,
        {
          agentId: value.agentId,
          found: value.found,
          path: value.path,
          parsedLineCount: value.parsedLineCount,
          lastDataAt: value.lastDataAt,
          scannedAt: value.scannedAt,
          staleAfterMs: value.staleAfterMs,
          ageMs: value.ageMs,
          status: value.status
        }
      ])
    )
  };
}

function inferStatus(
  found: boolean,
  lastDataAt: string | null,
  scannedAt: string,
  staleAfterMs: number
): { status: AgentConnectionStatus; ageMs: number | null } {
  if (!found || lastDataAt === null) {
    return { status: 'disconnected', ageMs: null };
  }

  const scannedMs = Date.parse(scannedAt);
  const lastDataMs = Date.parse(lastDataAt);
  if (!Number.isFinite(scannedMs) || !Number.isFinite(lastDataMs)) {
    return { status: 'disconnected', ageMs: null };
  }

  const ageMs = Math.max(0, scannedMs - lastDataMs);
  return {
    status: ageMs > staleAfterMs ? 'stale' : 'live',
    ageMs
  };
}

function compareIsoTimestamp(left: string, right: string): number {
  const leftMs = Date.parse(left);
  const rightMs = Date.parse(right);

  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) {
    return leftMs - rightMs;
  }

  return left.localeCompare(right);
}
