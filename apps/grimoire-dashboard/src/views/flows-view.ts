import { createViewShell, type View } from './view-common';

/**
 * Flows view — V3.S3.7 part 3.
 *
 * Consumes `DashboardStateSnapshot.flowGraph`, a pure projection that
 * groups HookEvents by `correlation_id`. Each flow exposes its
 * participants, scopes, phase mix, duration and node sequence.
 */
export function createFlowsView(): View {
  let bodyRef: HTMLElement | null = null;
  return {
    route: 'flows',
    title: 'Flows',
    status: 'live',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Flows',
        subtitle: 'Flows corrélés par correlation_id — agents, scopes, phases, durée.',
        status: 'live'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
    },
    update(snapshot) {
      if (!bodyRef) return;
      const { flowGraph } = snapshot;

      if (flowGraph.flows.length === 0 && flowGraph.orphans.length === 0) {
        bodyRef.innerHTML = `
          <section class="panel">
            <h2 class="panel__title">Flows corrélés</h2>
            <div class="empty">Aucun événement corrélé reçu.</div>
          </section>`;
        return;
      }

      const flowsHtml = flowGraph.flows
        .map((flow) => {
          const phases = Object.entries(flow.phaseCounters)
            .map(([p, c]) => `<span class="badge">${escape(p)}: ${c}</span>`)
            .join(' ');
          const agents = flow.agents.length
            ? flow.agents.map((a) => `<span class="badge">${escape(a)}</span>`).join(' ')
            : '<span class="muted">—</span>';
          const scopes = flow.scopes.map((s) => `<span class="badge">${escape(s)}</span>`).join(' ');
          const timeline = flow.nodes
            .map((n) => {
              const hms = n.ts.split('T')[1]?.replace('Z', '') ?? n.ts;
              return `<li><code>${escape(hms)}</code> · <strong>${escape(n.sourceHook)}</strong> · ${escape(n.phase)}${n.agentId ? ` · ${escape(n.agentId)}` : ''}</li>`;
            })
            .join('');
          return `
            <details class="panel" style="margin-bottom:12px;">
              <summary style="cursor:pointer; display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                <code>${escape(flow.correlationId.slice(0, 12))}…</code>
                <span class="badge">${flow.nodes.length} events</span>
                <span class="badge">${formatDuration(flow.durationMs)}</span>
                ${agents}
              </summary>
              <div style="margin-top:12px; display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                <div>
                  <div class="muted" style="font-family:var(--mono); font-size:12px; margin-bottom:4px;">scopes</div>
                  ${scopes}
                </div>
                <div>
                  <div class="muted" style="font-family:var(--mono); font-size:12px; margin-bottom:4px;">phases</div>
                  ${phases}
                </div>
              </div>
              <div style="margin-top:12px;">
                <div class="muted" style="font-family:var(--mono); font-size:12px; margin-bottom:4px;">séquence</div>
                <ol style="font-family:var(--mono); font-size:12px; line-height:1.6; padding-left:20px;">${timeline}</ol>
              </div>
            </details>`;
        })
        .join('');

      const orphanRow = flowGraph.orphans.length
        ? `<div class="banner"><strong>ORPHELINS</strong>${flowGraph.orphans.length} événement(s) sans correlation_id.</div>`
        : '';

      bodyRef.innerHTML = `
        <section class="panel">
          <h2 class="panel__title">Flows corrélés (${flowGraph.flows.length})</h2>
          ${orphanRow}
          ${flowsHtml || '<div class="empty">Aucun flow corrélé.</div>'}
        </section>`;
    }
  };
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = (ms / 1000).toFixed(1);
  return `${s}s`;
}

function escape(value: string): string {
  return value.replace(/[&<>"]/g, (ch) => {
    if (ch === '&') return '&amp;';
    if (ch === '<') return '&lt;';
    if (ch === '>') return '&gt;';
    return '&quot;';
  });
}
