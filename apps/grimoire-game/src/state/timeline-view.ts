import {
  createAuditView,
  type AuditEntry,
  type AuditEntryKind,
  type AuditEntryLevel,
  type AuditFilter
} from './audit-view';
import type { GameState } from './game-state';

export interface TimelineFilter {
  agentId?: string;
  taskId?: string;
  roomId?: string;
  traceId?: string;
  kinds?: readonly AuditEntryKind[];
  levels?: readonly AuditEntryLevel[];
  query?: string;
  fromSequenceId?: number;
  toSequenceId?: number;
}

export interface TimelineViewOptions {
  maxEntries?: number;
}

export interface TimelineGap {
  fromSequenceId: number;
  toSequenceId: number;
  missingCount: number;
}

export interface TimelineMetrics {
  totalCount: number;
  filteredCount: number;
  earliestSequenceId: number | null;
  latestSequenceId: number | null;
  gapCount: number;
  missingSequenceCount: number;
  errorCount: number;
  warningCount: number;
  infoCount: number;
}

export interface TimelineView {
  protocolVersion: string;
  lastSequenceId: number;
  hasActiveFilters: boolean;
  filter: TimelineFilter;
  entries: readonly AuditEntry[];
  gaps: readonly TimelineGap[];
  metrics: TimelineMetrics;
}

export function createTimelineView(
  state: GameState,
  filter: TimelineFilter = {},
  options: TimelineViewOptions = {}
): TimelineView {
  const normalizedFilter = normalizeTimelineFilter(filter);
  const baseAuditView = createAuditView(state, toAuditFilter(normalizedFilter));
  const rangeEntries = [...baseAuditView.entries]
    .sort(compareTimelineEntries)
    .filter((entry) => matchesSequenceRange(entry, normalizedFilter));
  const maxEntries = normalizeMaxEntries(options.maxEntries);
  const entries = maxEntries === null || rangeEntries.length <= maxEntries ? rangeEntries : rangeEntries.slice(-maxEntries);
  const gaps = createTimelineGaps(entries);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    hasActiveFilters: isTimelineFilterActive(normalizedFilter),
    filter: normalizedFilter,
    entries,
    gaps,
    metrics: createTimelineMetrics(baseAuditView.entries.length, entries, gaps)
  };
}

function normalizeTimelineFilter(filter: TimelineFilter): TimelineFilter {
  const agentId = normalizeStringFilter(filter.agentId);
  const taskId = normalizeStringFilter(filter.taskId);
  const roomId = normalizeStringFilter(filter.roomId);
  const traceId = normalizeStringFilter(filter.traceId);
  const query = normalizeStringFilter(filter.query);
  const kinds = normalizeArrayFilter(filter.kinds);
  const levels = normalizeArrayFilter(filter.levels);
  const fromSequenceId = normalizeSequenceId(filter.fromSequenceId);
  const toSequenceId = normalizeSequenceId(filter.toSequenceId);

  const range =
    fromSequenceId !== undefined && toSequenceId !== undefined && fromSequenceId > toSequenceId
      ? { fromSequenceId: toSequenceId, toSequenceId: fromSequenceId }
      : {
          ...(fromSequenceId === undefined ? {} : { fromSequenceId }),
          ...(toSequenceId === undefined ? {} : { toSequenceId })
        };

  return {
    ...(agentId === undefined ? {} : { agentId }),
    ...(taskId === undefined ? {} : { taskId }),
    ...(roomId === undefined ? {} : { roomId }),
    ...(traceId === undefined ? {} : { traceId }),
    ...(query === undefined ? {} : { query }),
    ...(kinds === undefined ? {} : { kinds }),
    ...(levels === undefined ? {} : { levels }),
    ...range
  };
}

function toAuditFilter(filter: TimelineFilter): AuditFilter {
  return {
    ...(filter.agentId === undefined ? {} : { agentId: filter.agentId }),
    ...(filter.taskId === undefined ? {} : { taskId: filter.taskId }),
    ...(filter.roomId === undefined ? {} : { roomId: filter.roomId }),
    ...(filter.traceId === undefined ? {} : { traceId: filter.traceId }),
    ...(filter.query === undefined ? {} : { query: filter.query }),
    ...(filter.kinds === undefined ? {} : { kinds: filter.kinds }),
    ...(filter.levels === undefined ? {} : { levels: filter.levels })
  };
}

function createTimelineMetrics(
  totalCount: number,
  entries: readonly AuditEntry[],
  gaps: readonly TimelineGap[]
): TimelineMetrics {
  const firstEntry = entries[0] ?? null;
  const lastEntry = entries[entries.length - 1] ?? null;

  return {
    totalCount,
    filteredCount: entries.length,
    earliestSequenceId: firstEntry?.sequenceId ?? null,
    latestSequenceId: lastEntry?.sequenceId ?? null,
    gapCount: gaps.length,
    missingSequenceCount: gaps.reduce((sum, gap) => sum + gap.missingCount, 0),
    errorCount: countEntriesByLevel(entries, 'error'),
    warningCount: countEntriesByLevel(entries, 'warning'),
    infoCount: countEntriesByLevel(entries, 'info')
  };
}

function createTimelineGaps(entries: readonly AuditEntry[]): TimelineGap[] {
  const sequenceIds = [...new Set(entries.map((entry) => entry.sequenceId))].sort((left, right) => left - right);
  const gaps: TimelineGap[] = [];

  for (let index = 1; index < sequenceIds.length; index += 1) {
    const previous = sequenceIds[index - 1];
    const current = sequenceIds[index];
    if (previous === undefined || current === undefined) {
      continue;
    }

    const delta = current - previous;
    if (delta <= 1) {
      continue;
    }

    gaps.push({
      fromSequenceId: previous + 1,
      toSequenceId: current - 1,
      missingCount: delta - 1
    });
  }

  return gaps;
}

function isTimelineFilterActive(filter: TimelineFilter): boolean {
  return Object.keys(filter).length > 0;
}

function matchesSequenceRange(entry: AuditEntry, filter: TimelineFilter): boolean {
  if (filter.fromSequenceId !== undefined && entry.sequenceId < filter.fromSequenceId) {
    return false;
  }

  if (filter.toSequenceId !== undefined && entry.sequenceId > filter.toSequenceId) {
    return false;
  }

  return true;
}

function compareTimelineEntries(left: AuditEntry, right: AuditEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return left.sequenceId - right.sequenceId;
  }

  if (left.timestamp !== right.timestamp) {
    return left.timestamp.localeCompare(right.timestamp);
  }

  return left.id.localeCompare(right.id);
}

function countEntriesByLevel(entries: readonly AuditEntry[], level: AuditEntryLevel): number {
  return entries.filter((entry) => entry.level === level).length;
}

function normalizeStringFilter(value: string | undefined): string | undefined {
  if (value === undefined) {
    return undefined;
  }

  const normalized = value.trim();
  return normalized.length === 0 ? undefined : normalized;
}

function normalizeArrayFilter<T extends string>(values: readonly T[] | undefined): readonly T[] | undefined {
  if (values === undefined || values.length === 0) {
    return undefined;
  }

  return Array.from(new Set(values));
}

function normalizeSequenceId(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) {
    return undefined;
  }

  return Math.trunc(value);
}

function normalizeMaxEntries(value: number | undefined): number | null {
  if (value === undefined || !Number.isFinite(value)) {
    return null;
  }

  const normalized = Math.trunc(value);
  return normalized > 0 ? normalized : null;
}
