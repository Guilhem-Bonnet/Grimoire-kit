/**
 * switchboard-view.ts — V2.1-UI pure projection of KanbanCards into
 * Switchboard role columns.
 *
 * The existing kanban organises cards by `TaskStatus` (backlog → done).
 * The Mission Board Switchboard presents the *same* cards grouped by the
 * target SwitchboardRole that would receive the card when dispatched.
 * This module is the canonical projection — it attaches card activity
 * derived from the hook snapshot and is entirely pure (no DOM, no fs).
 *
 * Role inference order:
 *   1. `explicitRoleOf(cardId)` if provided by the caller (UI drag state)
 *   2. default heuristic from `TaskKind`
 *   3. fallback to `coder`
 */

import type { HookEventSnapshot } from '../contracts/hookEvents';
import type { TaskKind } from '../contracts/events';
import {
  SWITCHBOARD_ROLES,
  SWITCHBOARD_ROLE_TO_AGENT,
  type SwitchboardRole
} from '../contracts/switchboard-roles';
import type { CardActivity } from './card-activity';
import { selectCardActivity } from './card-activity';
import type { KanbanCard, KanbanView } from './kanban-view';

export const DEFAULT_KIND_TO_ROLE: Readonly<Record<TaskKind, SwitchboardRole>> =
  Object.freeze({
    feature: 'coder',
    bug: 'coder',
    research: 'analyst',
    ops: 'lead_coder',
    security: 'reviewer'
  });

export const FALLBACK_ROLE: SwitchboardRole = 'coder';

export interface SwitchboardCard {
  taskId: string;
  title: string;
  role: SwitchboardRole;
  agentId: string;
  status: CardActivity['status'];
  kanbanStatus: KanbanCard['rawStatus'];
  correlationId: string | null;
  priority: KanbanCard['priority'];
  kind: KanbanCard['kind'];
  activity: CardActivity;
  blockerCount: number;
}

export interface SwitchboardColumn {
  role: SwitchboardRole;
  agentId: string;
  cards: readonly SwitchboardCard[];
  count: number;
  activeCount: number;
  blockedCount: number;
  doneCount: number;
}

export interface SwitchboardView {
  columns: readonly SwitchboardColumn[];
  totalCards: number;
  totals: Record<CardActivity['status'], number>;
}

export interface CreateSwitchboardViewOptions {
  /** Optional explicit role assignment per card (wins over heuristic). */
  explicitRoleOf?: (cardId: string) => SwitchboardRole | null | undefined;
  /** Correlation id associated with a card (for activity binding). */
  correlationIdOf?: (cardId: string) => string | null | undefined;
  /** Override the default kind→role heuristic. */
  kindRoleMap?: Readonly<Record<TaskKind, SwitchboardRole>>;
}

function resolveRole(
  card: KanbanCard,
  options: CreateSwitchboardViewOptions
): SwitchboardRole {
  const explicit = options.explicitRoleOf?.(card.taskId);
  if (explicit) return explicit;
  const map = options.kindRoleMap ?? DEFAULT_KIND_TO_ROLE;
  if (card.kind && card.kind in map) return map[card.kind];
  return FALLBACK_ROLE;
}

export function createSwitchboardView(
  kanban: KanbanView,
  snapshot: HookEventSnapshot | null,
  options: CreateSwitchboardViewOptions = {}
): SwitchboardView {
  const buckets = new Map<SwitchboardRole, SwitchboardCard[]>();
  for (const role of SWITCHBOARD_ROLES) {
    buckets.set(role, []);
  }

  const totals: Record<CardActivity['status'], number> = {
    queued: 0,
    running: 0,
    blocked: 0,
    done: 0,
    errored: 0
  };

  for (const card of kanban.cards) {
    const role = resolveRole(card, options);
    const correlationId = options.correlationIdOf?.(card.taskId) ?? null;
    const activity = selectCardActivity(snapshot, correlationId ?? '');
    const agentId = SWITCHBOARD_ROLE_TO_AGENT[role];
    const bucket = buckets.get(role);
    if (!bucket) continue;
    bucket.push({
      taskId: card.taskId,
      title: card.title,
      role,
      agentId,
      status: activity.status,
      kanbanStatus: card.rawStatus,
      correlationId,
      priority: card.priority,
      kind: card.kind,
      activity,
      blockerCount: card.blockers.length
    });
    totals[activity.status] += 1;
  }

  const columns: SwitchboardColumn[] = SWITCHBOARD_ROLES.map((role) => {
    const cards = buckets.get(role) ?? [];
    return {
      role,
      agentId: SWITCHBOARD_ROLE_TO_AGENT[role],
      cards,
      count: cards.length,
      activeCount: cards.filter((c) => c.status === 'running').length,
      blockedCount: cards.filter(
        (c) => c.status === 'blocked' || c.status === 'errored'
      ).length,
      doneCount: cards.filter((c) => c.status === 'done').length
    } satisfies SwitchboardColumn;
  });

  return {
    columns,
    totalCards: kanban.cards.length,
    totals
  };
}
