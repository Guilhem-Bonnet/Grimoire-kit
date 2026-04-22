import { describe, expect, it } from 'vitest';

import {
  DEFAULT_KIND_TO_ROLE,
  FALLBACK_ROLE,
  createSwitchboardView
} from '../../src/state/switchboard-view';
import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEventSnapshot
} from '../../src/contracts/hookEvents';
import type { KanbanCard, KanbanView } from '../../src/state/kanban-view';
import { SWITCHBOARD_ROLES } from '../../src/contracts/switchboard-roles';

function makeCard(overrides: Partial<KanbanCard> & { taskId: string }): KanbanCard {
  return {
    taskId: overrides.taskId,
    title: overrides.title ?? 'Card ' + overrides.taskId,
    rawStatus: overrides.rawStatus ?? 'backlog',
    syncedStatus: overrides.syncedStatus ?? 'backlog',
    syncState: overrides.syncState ?? 'aligned',
    syncReason: overrides.syncReason ?? '',
    priority: overrides.priority ?? null,
    kind: overrides.kind ?? null,
    dependencyIds: overrides.dependencyIds ?? [],
    blockedReason: overrides.blockedReason ?? null,
    assignee: overrides.assignee ?? null,
    traceIds: overrides.traceIds ?? [],
    verificationStatus: overrides.verificationStatus ?? 'not_applicable',
    reviewGateBlocked: overrides.reviewGateBlocked ?? false,
    doneGateBlocked: overrides.doneGateBlocked ?? false,
    blockers: overrides.blockers ?? [],
    availableTransitions: overrides.availableTransitions ?? []
  } as KanbanCard;
}

function makeView(cards: KanbanCard[]): KanbanView {
  return {
    protocolVersion: '1',
    lastSequenceId: 0,
    cards,
    columns: [],
    metrics: {
      cardCount: cards.length,
      syncedCount: cards.length,
      unsyncedCount: 0,
      blockedCount: 0,
      readyForDoneCount: 0,
      readyForReviewCount: 0
    },
    capabilities: { role: null, canAssign: false, canDrag: false, canEditMetadata: false }
  };
}

function makeSnapshot(): HookEventSnapshot {
  return {
    schemaVersion: HOOK_EVENT_SCHEMA_VERSION,
    generatedAt: '2026-04-22T13:00:00.000Z',
    events: [
      {
        schema_version: HOOK_EVENT_SCHEMA_VERSION,
        event_id: 'e1',
        ts: '2026-04-22T12:00:00.000Z',
        scope: 'task',
        phase: 'start',
        source_hook: 'test',
        correlation_id: 'corr-T1',
        payload: {}
      }
    ],
    counters: {
      total: 1,
      byScope: {} as HookEventSnapshot['counters']['byScope'],
      bySourceHook: {}
    }
  };
}

describe('createSwitchboardView', () => {
  it('produces a column per Switchboard role even when empty', () => {
    const view = createSwitchboardView(makeView([]), null);
    expect(view.columns.map((c) => c.role)).toEqual([...SWITCHBOARD_ROLES]);
    expect(view.totalCards).toBe(0);
    expect(view.totals).toEqual({
      queued: 0,
      running: 0,
      blocked: 0,
      done: 0,
      errored: 0
    });
  });

  it('dispatches cards via the default kind→role heuristic', () => {
    const cards = [
      makeCard({ taskId: 'T1', kind: 'feature' }),
      makeCard({ taskId: 'T2', kind: 'research' }),
      makeCard({ taskId: 'T3', kind: 'ops' }),
      makeCard({ taskId: 'T4', kind: 'security' })
    ];
    const view = createSwitchboardView(makeView(cards), null);
    const columnByRole = Object.fromEntries(
      view.columns.map((c) => [c.role, c])
    );
    expect(columnByRole.coder?.cards.map((c) => c.taskId)).toEqual(['T1']);
    expect(columnByRole.analyst?.cards.map((c) => c.taskId)).toEqual(['T2']);
    expect(columnByRole.lead_coder?.cards.map((c) => c.taskId)).toEqual(['T3']);
    expect(columnByRole.reviewer?.cards.map((c) => c.taskId)).toEqual(['T4']);
  });

  it('falls back to coder when kind is null', () => {
    const cards = [makeCard({ taskId: 'T1', kind: null })];
    const view = createSwitchboardView(makeView(cards), null);
    const coderCol = view.columns.find((c) => c.role === FALLBACK_ROLE);
    expect(coderCol?.cards.map((c) => c.taskId)).toEqual(['T1']);
  });

  it('honours explicit role assignment over heuristic', () => {
    const cards = [makeCard({ taskId: 'T1', kind: 'feature' })];
    const view = createSwitchboardView(makeView(cards), null, {
      explicitRoleOf: () => 'acceptance'
    });
    expect(view.columns.find((c) => c.role === 'acceptance')?.cards).toHaveLength(1);
    expect(view.columns.find((c) => c.role === 'coder')?.cards).toHaveLength(0);
  });

  it('binds card activity via correlationIdOf + snapshot', () => {
    const cards = [makeCard({ taskId: 'T1', kind: 'feature' })];
    const view = createSwitchboardView(makeView(cards), makeSnapshot(), {
      correlationIdOf: () => 'corr-T1'
    });
    const coderCol = view.columns.find((c) => c.role === 'coder');
    const card = coderCol?.cards[0];
    expect(card?.status).toBe('running');
    expect(card?.correlationId).toBe('corr-T1');
    expect(card?.activity.events).toHaveLength(1);
    expect(view.totals.running).toBe(1);
  });

  it('uses custom kindRoleMap override', () => {
    const view = createSwitchboardView(
      makeView([makeCard({ taskId: 'T1', kind: 'feature' })]),
      null,
      {
        kindRoleMap: {
          ...DEFAULT_KIND_TO_ROLE,
          feature: 'intern'
        }
      }
    );
    expect(view.columns.find((c) => c.role === 'intern')?.cards).toHaveLength(1);
  });

  it('counts active / blocked / done per column', () => {
    const cards = [
      makeCard({ taskId: 'T1', kind: 'feature' }),
      makeCard({ taskId: 'T2', kind: 'feature' }),
      makeCard({ taskId: 'T3', kind: 'feature' })
    ];
    const snap: HookEventSnapshot = {
      schemaVersion: HOOK_EVENT_SCHEMA_VERSION,
      generatedAt: '2026-04-22T13:00:00.000Z',
      events: [
        {
          schema_version: HOOK_EVENT_SCHEMA_VERSION,
          event_id: 'e1',
          ts: '2026-04-22T12:00:00.000Z',
          scope: 'task',
          phase: 'start',
          source_hook: 't',
          correlation_id: 'c1',
          payload: {}
        },
        {
          schema_version: HOOK_EVENT_SCHEMA_VERSION,
          event_id: 'e2',
          ts: '2026-04-22T12:01:00.000Z',
          scope: 'task',
          phase: 'start',
          source_hook: 't',
          correlation_id: 'c2',
          payload: {}
        },
        {
          schema_version: HOOK_EVENT_SCHEMA_VERSION,
          event_id: 'e3',
          ts: '2026-04-22T12:02:00.000Z',
          scope: 'task',
          phase: 'block',
          source_hook: 't',
          correlation_id: 'c2',
          payload: {}
        },
        {
          schema_version: HOOK_EVENT_SCHEMA_VERSION,
          event_id: 'e4',
          ts: '2026-04-22T12:03:00.000Z',
          scope: 'task',
          phase: 'start',
          source_hook: 't',
          correlation_id: 'c3',
          payload: {}
        },
        {
          schema_version: HOOK_EVENT_SCHEMA_VERSION,
          event_id: 'e5',
          ts: '2026-04-22T12:04:00.000Z',
          scope: 'task',
          phase: 'end',
          source_hook: 't',
          correlation_id: 'c3',
          payload: {}
        }
      ],
      counters: { total: 5, byScope: {} as HookEventSnapshot['counters']['byScope'], bySourceHook: {} }
    };
    const view = createSwitchboardView(makeView(cards), snap, {
      correlationIdOf: (id) => (id === 'T1' ? 'c1' : id === 'T2' ? 'c2' : 'c3')
    });
    const coder = view.columns.find((c) => c.role === 'coder');
    expect(coder?.count).toBe(3);
    expect(coder?.activeCount).toBe(1);
    expect(coder?.blockedCount).toBe(1);
    expect(coder?.doneCount).toBe(1);
    expect(view.totals).toEqual({
      queued: 0,
      running: 1,
      blocked: 1,
      done: 1,
      errored: 0
    });
  });
});
