import type { DashboardStateSnapshot } from '../dashboard-store';
import { createViewShell, type View } from './view-common';

/**
 * Flows view — placeholder. Lists distinct `source_hook` keys + their event
 * volume, as a first approximation of active flows. Real flow graph
 * (with edges between events) is deferred to a later iteration.
 */
export function createFlowsView(): View {
  let bodyRef: HTMLElement | null = null;
  return {
    route: 'flows',
    title: 'Flows',
    status: 'partial',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Flows',
        subtitle: 'Vue partielle — recensement par source_hook. Graphe de flow à venir.',
        status: 'partial'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
    },
    update(snapshot) {
      if (!bodyRef) return;
      const counts = new Map<string, { count: number; lastTs: string; scopes: Set<string> }>();
      for (const event of snapshot.events) {
        const key = event.source_hook || 'unknown';
        const entry = counts.get(key) ?? { count: 0, lastTs: event.ts, scopes: new Set<string>() };
        entry.count += 1;
        if (event.ts > entry.lastTs) entry.lastTs = event.ts;
        entry.scopes.add(event.scope);
        counts.set(key, entry);
      }
      const rows = [...counts.entries()]
        .sort((a, b) => b[1].count - a[1].count)
        .map(
          ([hook, data]) => `
          <tr>
            <td>${escape(hook)}</td>
            <td>${data.count}</td>
            <td>${[...data.scopes].map((s) => `<span class="badge">${escape(s)}</span>`).join(' ')}</td>
            <td>${escape(data.lastTs)}</td>
          </tr>`
        )
        .join('');
      bodyRef.innerHTML = `
        <div class="banner">
          <strong>PARTIEL</strong>
          Le véritable graphe de flows (edges HookEvent→HookEvent) n'est pas encore branché.
          Cette vue liste pour l'instant les hooks observés et leur volume.
        </div>
        <section class="panel">
          <h2 class="panel__title">Hooks observés</h2>
          ${
            counts.size === 0
              ? '<div class="empty">Aucun événement reçu.</div>'
              : `<table class="table">
                  <thead><tr><th>source_hook</th><th>events</th><th>scopes</th><th>dernier</th></tr></thead>
                  <tbody>${rows}</tbody>
                </table>`
          }
        </section>
      `;
    }
  };
}

function escape(value: string): string {
  return value.replace(/[&<>"]/g, (ch) => {
    if (ch === '&') return '&amp;';
    if (ch === '<') return '&lt;';
    if (ch === '>') return '&gt;';
    return '&quot;';
  });
}
