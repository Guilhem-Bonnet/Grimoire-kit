import type { DashboardStateSnapshot } from '../dashboard-store';
import { createViewShell, type View } from './view-common';
import type { HookEvent } from '@game/contracts/hookEvents';

type KanbanColumn = 'backlog' | 'in_progress' | 'blocked' | 'done';

interface KanbanCard {
  taskId: string;
  title: string;
  agentId: string | null;
  column: KanbanColumn;
  lastTs: string;
  sourceHook: string;
}

const COLUMN_LABELS: Record<KanbanColumn, string> = {
  backlog: 'BACKLOG',
  in_progress: 'IN PROGRESS',
  blocked: 'BLOCKED',
  done: 'DONE'
};

/**
 * Kanban view — derived live from `task` scope events in activity.jsonl.
 *
 * Column mapping:
 *   - task/start   → in_progress
 *   - task/block   → blocked
 *   - task/correct → in_progress (re-activated after a correct phase)
 *   - task/end     → done
 *   - any task event with no prior record → backlog (just observed, not yet started)
 *
 * Card title uses `payload.title` if present, else a short form of task_id.
 */
export function createKanbanView(): View {
  let bodyRef: HTMLElement | null = null;
  return {
    route: 'kanban',
    title: 'Kanban',
    status: 'live',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Kanban',
        subtitle: 'Backlog dérivé des événements `task.*` en temps réel.',
        status: 'live'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
    },
    update(snapshot) {
      if (!bodyRef) return;
      const cards = deriveKanban(snapshot.events);
      const byColumn: Record<KanbanColumn, KanbanCard[]> = {
        backlog: [],
        in_progress: [],
        blocked: [],
        done: []
      };
      for (const card of cards) byColumn[card.column].push(card);

      bodyRef.innerHTML = `
        <div class="kanban">
          ${(['backlog', 'in_progress', 'blocked', 'done'] as const)
            .map((col) => renderColumn(col, byColumn[col]))
            .join('')}
        </div>
      `;
    }
  };
}

function deriveKanban(events: readonly HookEvent[]): KanbanCard[] {
  const cards = new Map<string, KanbanCard>();
  for (const event of events) {
    if (event.scope !== 'task') continue;
    const taskId = resolveTaskId(event);
    if (!taskId) continue;
    const column = phaseToColumn(event.phase, cards.get(taskId)?.column);
    const title = readString(event.payload, 'title') ?? taskId;
    cards.set(taskId, {
      taskId,
      title,
      agentId: event.agent?.id ?? null,
      column,
      lastTs: event.ts,
      sourceHook: event.source_hook
    });
  }
  return [...cards.values()].sort((a, b) => (a.lastTs < b.lastTs ? 1 : -1));
}

function resolveTaskId(event: HookEvent): string | null {
  return (
    readString(event.payload, 'task_id') ??
    readString(event.payload, 'taskId') ??
    readString(event.payload, 'id') ??
    event.correlation_id ??
    null
  );
}

function phaseToColumn(phase: string, previous: KanbanColumn | undefined): KanbanColumn {
  switch (phase) {
    case 'start':
      return 'in_progress';
    case 'block':
      return 'blocked';
    case 'correct':
      return 'in_progress';
    case 'end':
      return 'done';
    case 'info':
      return previous ?? 'backlog';
    default:
      return previous ?? 'backlog';
  }
}

function renderColumn(column: KanbanColumn, cards: readonly KanbanCard[]): string {
  const body =
    cards.length === 0
      ? '<div class="empty">—</div>'
      : cards.map(renderCard).join('');
  return `
    <div class="kanban__column">
      <h3 class="kanban__column-title">${COLUMN_LABELS[column]} · ${cards.length}</h3>
      ${body}
    </div>`;
}

function renderCard(card: KanbanCard): string {
  return `
    <article class="kanban__card">
      <p class="kanban__card-title">${escape(card.title)}</p>
      <p class="kanban__card-meta">
        ${escape(card.taskId.slice(0, 24))} · ${card.agentId ? escape(card.agentId) : 'n/a'} · ${escape(card.sourceHook)}
      </p>
    </article>`;
}

function readString(payload: Record<string, unknown> | null | undefined, key: string): string | null {
  if (!payload) return null;
  const value = payload[key];
  return typeof value === 'string' ? value : null;
}

function escape(value: string): string {
  return value.replace(/[&<>"]/g, (ch) => {
    if (ch === '&') return '&amp;';
    if (ch === '<') return '&lt;';
    if (ch === '>') return '&gt;';
    return '&quot;';
  });
}
