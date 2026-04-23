import type { DashboardStateSnapshot } from '../dashboard-store';
import { createViewShell, type View } from './view-common';

/**
 * Observability view — timeline summary + debug panel listing.
 *
 * Backed by OfficeTimeline + OfficeDebugPanelView (live).
 */
export function createObservabilityView(): View {
  let bodyRef: HTMLElement | null = null;

  return {
    route: 'observability',
    title: 'Observability',
    status: 'live',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Observability',
        subtitle: 'Timeline + debug panel · HookEvents en direct depuis activity.jsonl',
        status: 'live'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
      this.update({} as DashboardStateSnapshot);
    },
    update(snapshot) {
      if (!bodyRef || !snapshot?.office) return;
      const { timeline, debugPanel, events } = snapshot;

      bodyRef.innerHTML = `
        <div class="grid-cols-2">
          <section class="panel">
            <h2 class="panel__title">Timeline</h2>
            <div class="kv-row"><span class="kv-row__k">frames</span><span class="kv-row__v">${timeline.frames.length}</span></div>
            <div class="kv-row"><span class="kv-row__k">premier</span><span class="kv-row__v">${timeline.bounds?.start ?? '—'}</span></div>
            <div class="kv-row"><span class="kv-row__k">dernier</span><span class="kv-row__v">${timeline.bounds?.end ?? '—'}</span></div>
            <div class="kv-row"><span class="kv-row__k">durée ms</span><span class="kv-row__v">${timeline.bounds?.durationMs ?? 0}</span></div>
            <div class="kv-row"><span class="kv-row__k">événements</span><span class="kv-row__v">${events.length}</span></div>
          </section>
          <section class="panel">
            <h2 class="panel__title">Debug Panel — agents</h2>
            <div class="kv-row"><span class="kv-row__k">agents suivis</span><span class="kv-row__v">${debugPanel.agents.length}</span></div>
            <div class="kv-row"><span class="kv-row__k">total événements</span><span class="kv-row__v">${debugPanel.totalEvents}</span></div>
            <div class="kv-row"><span class="kv-row__k">version schéma</span><span class="kv-row__v">${debugPanel.schemaVersion}</span></div>
          </section>
        </div>
        <section class="panel">
          <h2 class="panel__title">Derniers événements</h2>
          ${renderEventsTable(events.slice(-30).reverse())}
        </section>
      `;
    }
  };
}

function renderEventsTable(events: readonly { ts: string; scope: string; phase: string; event_id: string; agent?: { id?: string | undefined } | null | undefined; source_hook: string }[]): string {
  if (events.length === 0) {
    return '<div class="empty">Aucun événement reçu.</div>';
  }
  const rows = events
    .map(
      (e) => `
      <tr>
        <td>${escape(e.ts)}</td>
        <td><span class="badge">${escape(e.scope)}</span> <span class="badge">${escape(e.phase)}</span></td>
        <td>${escape(e.agent?.id ?? '—')}</td>
        <td>${escape(e.source_hook)}</td>
        <td>${escape(e.event_id.slice(0, 12))}</td>
      </tr>`
    )
    .join('');
  return `
    <table class="table">
      <thead><tr><th>ts</th><th>scope / phase</th><th>agent</th><th>source_hook</th><th>event_id</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function escape(value: string): string {
  return value.replace(/[&<>"]/g, (ch) => {
    if (ch === '&') return '&amp;';
    if (ch === '<') return '&lt;';
    if (ch === '>') return '&gt;';
    return '&quot;';
  });
}
