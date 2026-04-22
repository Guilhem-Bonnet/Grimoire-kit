import { describe, expect, it } from 'vitest';

import {
  ARCHIVE_SCHEMA_VERSION,
  buildDispatchArchiveEntry,
  parseArchiveLine,
  serializeArchiveEntry
} from '../../src/state/dispatch-archive';
import type { CardActivity } from '../../src/state/card-activity';
import type { CardDispatchRequest } from '../../src/state/kanban-dispatch';

const REQUEST: CardDispatchRequest = {
  cardId: 'card-42',
  correlationId: 'corr-42',
  targetRole: 'coder',
  targetAgentId: 'dev',
  title: 'Archive me',
  promptContext: '',
  complexity: 'high',
  actorId: 'guilhem',
  plannedAt: '2026-04-22T12:00:00.000Z'
};

const ACTIVITY: CardActivity = {
  correlationId: 'corr-42',
  status: 'done',
  events: [
    {
      schema_version: '1.0',
      event_id: 'e1',
      ts: '2026-04-22T12:00:00.000Z',
      scope: 'task',
      phase: 'start',
      source_hook: 't',
      correlation_id: 'corr-42',
      payload: {}
    },
    {
      schema_version: '1.0',
      event_id: 'e2',
      ts: '2026-04-22T12:10:00.000Z',
      scope: 'task',
      phase: 'end',
      source_hook: 't',
      correlation_id: 'corr-42',
      payload: {}
    }
  ],
  lastActivityTs: '2026-04-22T12:10:00.000Z',
  startedAt: '2026-04-22T12:00:00.000Z',
  endedAt: '2026-04-22T12:10:00.000Z',
  blockCount: 0
};

describe('buildDispatchArchiveEntry', () => {
  it('produces a canonical archive payload', () => {
    const entry = buildDispatchArchiveEntry(REQUEST, ACTIVITY, {
      clock: () => '2026-04-22T13:00:00.000Z'
    });
    expect(entry.schemaVersion).toBe(ARCHIVE_SCHEMA_VERSION);
    expect(entry.archivedAt).toBe('2026-04-22T13:00:00.000Z');
    expect(entry.cardId).toBe('card-42');
    expect(entry.correlationId).toBe('corr-42');
    expect(entry.finalStatus).toBe('done');
    expect(entry.eventCount).toBe(2);
    expect(entry.startedAt).toBe('2026-04-22T12:00:00.000Z');
    expect(entry.endedAt).toBe('2026-04-22T12:10:00.000Z');
    expect(entry.actorId).toBe('guilhem');
  });

  it('handles empty activity gracefully', () => {
    const entry = buildDispatchArchiveEntry(
      REQUEST,
      {
        correlationId: 'corr-42',
        status: 'queued',
        events: [],
        lastActivityTs: null,
        startedAt: null,
        endedAt: null,
        blockCount: 0
      },
      { clock: () => '2026-04-22T13:00:00.000Z' }
    );
    expect(entry.finalStatus).toBe('queued');
    expect(entry.eventCount).toBe(0);
    expect(entry.startedAt).toBeNull();
  });
});

describe('serializeArchiveEntry / parseArchiveLine', () => {
  it('round-trips an entry through JSONL', () => {
    const entry = buildDispatchArchiveEntry(REQUEST, ACTIVITY, {
      clock: () => '2026-04-22T13:00:00.000Z'
    });
    const line = serializeArchiveEntry(entry);
    expect(line).not.toContain('\n');
    const parsed = parseArchiveLine(line);
    expect(parsed).toEqual(entry);
  });

  it('parseArchiveLine returns null on blank input', () => {
    expect(parseArchiveLine('')).toBeNull();
    expect(parseArchiveLine('   ')).toBeNull();
  });

  it('parseArchiveLine returns null on malformed JSON', () => {
    expect(parseArchiveLine('{broken')).toBeNull();
  });
});
