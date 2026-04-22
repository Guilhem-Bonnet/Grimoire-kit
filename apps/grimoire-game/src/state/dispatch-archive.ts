/**
 * dispatch-archive.ts — V2.7 pure JSONL serializer for dispatch
 * outcomes. The actual file write (append to
 * `_grimoire-runtime-output/mission-board/archive.jsonl`) is delegated
 * to the extension host; this module only produces the canonical lines.
 *
 * Each archive entry bundles the original request, the final resolved
 * card activity (status + counts) and a wall-clock archivedAt, enough
 * to reconstruct a mission post-mortem without the live ledger.
 */

import type { CardActivity, CardActivityStatus } from './card-activity';
import type { CardDispatchRequest } from './kanban-dispatch';

export const ARCHIVE_SCHEMA_VERSION = '1.0';

export interface DispatchArchiveEntry {
  schemaVersion: string;
  archivedAt: string;
  cardId: string;
  correlationId: string;
  targetRole: CardDispatchRequest['targetRole'];
  targetAgentId: string;
  title: string;
  complexity: CardDispatchRequest['complexity'];
  actorId: string | null;
  plannedAt: string;
  finalStatus: CardActivityStatus;
  startedAt: string | null;
  endedAt: string | null;
  blockCount: number;
  eventCount: number;
  lastActivityTs: string | null;
}

export interface BuildArchiveEntryOptions {
  /** DI for tests — defaults to `new Date().toISOString()`. */
  clock?: () => string;
}

function defaultClock(): string {
  return new Date().toISOString();
}

export function buildDispatchArchiveEntry(
  request: CardDispatchRequest,
  activity: CardActivity,
  options: BuildArchiveEntryOptions = {}
): DispatchArchiveEntry {
  const clock = options.clock ?? defaultClock;
  return {
    schemaVersion: ARCHIVE_SCHEMA_VERSION,
    archivedAt: clock(),
    cardId: request.cardId,
    correlationId: request.correlationId,
    targetRole: request.targetRole,
    targetAgentId: request.targetAgentId,
    title: request.title,
    complexity: request.complexity,
    actorId: request.actorId,
    plannedAt: request.plannedAt,
    finalStatus: activity.status,
    startedAt: activity.startedAt,
    endedAt: activity.endedAt,
    blockCount: activity.blockCount,
    eventCount: activity.events.length,
    lastActivityTs: activity.lastActivityTs
  };
}

/**
 * Serialize an archive entry as a single JSONL line (no trailing
 * newline). Callers append `'\n'` themselves when writing to the file.
 */
export function serializeArchiveEntry(entry: DispatchArchiveEntry): string {
  return JSON.stringify(entry);
}

/**
 * Parse an archive line back into an entry. Returns `null` if the line
 * is empty or malformed — the caller decides whether to quarantine.
 */
export function parseArchiveLine(line: string): DispatchArchiveEntry | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed) as DispatchArchiveEntry;
  } catch {
    return null;
  }
}
