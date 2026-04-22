/**
 * card-activity.ts — V2.4 pure selector binding Mission Board cards to
 * the canonical hook ledger.
 *
 * Given a `HookEventSnapshot` (already surfaced to the browser by the
 * polling client) and a card's `correlationId`, derive:
 *   - the card status (queued / running / blocked / done / errored)
 *   - the ordered activity trail (task-scope events only)
 *   - light metadata (lastActivityTs, counts)
 *
 * No I/O, no DOM. Consumed by both the kanban view (card badge) and the
 * card detail panel.
 */

import type {
  HookEvent,
  HookEventSnapshot
} from '../contracts/hookEvents';

export const CARD_ACTIVITY_STATUSES = [
  'queued',
  'running',
  'blocked',
  'done',
  'errored'
] as const;

export type CardActivityStatus = (typeof CARD_ACTIVITY_STATUSES)[number];

export interface CardActivity {
  correlationId: string;
  status: CardActivityStatus;
  events: HookEvent[];
  lastActivityTs: string | null;
  startedAt: string | null;
  endedAt: string | null;
  blockCount: number;
}

function isTaskScope(event: HookEvent): boolean {
  return event.scope === 'task' || event.scope === 'anomaly';
}

/**
 * Derive card activity from a snapshot. Events are filtered on
 * correlation_id and sorted by ts ascending to keep UI rendering stable.
 */
export function selectCardActivity(
  snapshot: HookEventSnapshot | null | undefined,
  correlationId: string
): CardActivity {
  const empty: CardActivity = {
    correlationId,
    status: 'queued',
    events: [],
    lastActivityTs: null,
    startedAt: null,
    endedAt: null,
    blockCount: 0
  };

  if (!snapshot || !correlationId) {
    return empty;
  }

  const events = snapshot.events
    .filter(
      (event) => isTaskScope(event) && event.correlation_id === correlationId
    )
    .slice()
    .sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));

  if (events.length === 0) {
    return empty;
  }

  let status: CardActivityStatus = 'queued';
  let startedAt: string | null = null;
  let endedAt: string | null = null;
  let blockCount = 0;

  for (const event of events) {
    if (event.scope === 'anomaly') {
      status = 'errored';
      continue;
    }
    if (event.phase === 'start' && !startedAt) {
      startedAt = event.ts;
      if (status === 'queued') status = 'running';
    } else if (event.phase === 'end') {
      endedAt = event.ts;
      if (status !== 'errored') status = 'done';
    } else if (event.phase === 'block') {
      blockCount += 1;
      if (status !== 'errored' && status !== 'done') status = 'blocked';
    } else if (event.phase === 'correct' && status === 'blocked') {
      status = 'running';
    }
  }

  const last = events[events.length - 1];
  return {
    correlationId,
    status,
    events,
    lastActivityTs: last ? last.ts : null,
    startedAt,
    endedAt,
    blockCount
  };
}
