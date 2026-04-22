/**
 * hook-events-client.ts — browser polling client for the static snapshot.
 *
 * Contract: consumes ``./hook-events.json`` produced by the Vite prepare
 * script (V1.5b, see examples/prepare-runtime-cockpit-app.ts). The
 * browser never hits the ledger directly; the prepare step is the single
 * read boundary.
 *
 * The client is idempotent and self-healing:
 *   - polls on a fixed interval (default 2s)
 *   - re-renders into ``#hook-events-ledger`` whenever the mount appears
 *     (mode change, re-render); disappearance is a no-op
 *   - fetch failures degrade to a neutral "ledger offline" state without
 *     throwing into the main render loop
 */

import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent,
  type HookEventCounters,
  type HookEventPhase,
  type HookEventScope
} from '../src/contracts/hookEvents';

export const HOOK_EVENTS_MOUNT_ID = 'hook-events-ledger';

interface SnapshotPayload {
  schemaVersion: string;
  generatedAt: string;
  events: HookEvent[];
  counters: HookEventCounters;
}

export interface HookEventsClientOptions {
  url?: string;
  intervalMs?: number;
  /** Dependency injection for tests. */
  fetcher?: (url: string) => Promise<Response>;
  /** Dependency injection for tests. */
  documentRef?: Document;
  /** Max events to show in the activity list. */
  maxActivity?: number;
}

interface ClientState {
  snapshot: SnapshotPayload | null;
  error: string | null;
  lastSuccessAt: string | null;
}

const DEFAULT_URL = './hook-events.json';
const DEFAULT_INTERVAL_MS = 2000;
const DEFAULT_MAX_ACTIVITY = 20;

export class HookEventsClient {
  private readonly url: string;
  private readonly intervalMs: number;
  private readonly fetcher: (url: string) => Promise<Response>;
  private readonly documentRef: Document;
  private readonly maxActivity: number;
  private state: ClientState = {
    snapshot: null,
    error: null,
    lastSuccessAt: null
  };
  private timer: ReturnType<typeof setInterval> | null = null;
  private started = false;

  constructor(options: HookEventsClientOptions = {}) {
    this.url = options.url ?? DEFAULT_URL;
    this.intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS;
    this.fetcher = options.fetcher ?? ((url) => fetch(url, { cache: 'no-store' }));
    this.documentRef = options.documentRef ?? document;
    this.maxActivity = options.maxActivity ?? DEFAULT_MAX_ACTIVITY;
  }

  /** Start polling. Safe to call multiple times. */
  start(): void {
    if (this.started) {
      return;
    }
    this.started = true;
    void this.tick();
    this.timer = setInterval(() => {
      void this.tick();
    }, this.intervalMs);
  }

  /** Stop polling. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.started = false;
  }

  /** Exposed for tests — force one fetch + render cycle. */
  async tick(): Promise<void> {
    try {
      const response = await this.fetcher(this.url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = (await response.json()) as SnapshotPayload;
      if (typeof data?.schemaVersion !== 'string') {
        throw new Error('malformed snapshot: missing schemaVersion');
      }
      this.state = {
        snapshot: data,
        error: null,
        lastSuccessAt: new Date().toISOString()
      };
    } catch (error) {
      this.state = {
        snapshot: this.state.snapshot,
        error: error instanceof Error ? error.message : String(error),
        lastSuccessAt: this.state.lastSuccessAt
      };
    }
    this.render();
  }

  /** Read current state (for tests). */
  getState(): Readonly<ClientState> {
    return this.state;
  }

  /** Render into the mount if it exists; otherwise no-op. */
  render(): void {
    const mount = this.documentRef.getElementById(HOOK_EVENTS_MOUNT_ID);
    if (!mount) {
      return;
    }
    mount.innerHTML = this.buildHtml();
  }

  private buildHtml(): string {
    const { snapshot, error, lastSuccessAt } = this.state;

    if (!snapshot && error) {
      return `
        <div class="muted">Ledger runtime indisponible — ${escape(error)}.
        La snapshot ${escape(DEFAULT_URL)} sera reprise a la prochaine publication.</div>
      `;
    }
    if (!snapshot) {
      return '<div class="muted">Chargement du ledger runtime&hellip;</div>';
    }
    if (snapshot.schemaVersion !== HOOK_EVENT_SCHEMA_VERSION) {
      return `<div class="muted">Schema version ${escape(snapshot.schemaVersion)} non supportee (attendu ${HOOK_EVENT_SCHEMA_VERSION}).</div>`;
    }

    const counters = snapshot.counters;
    const total = counters.total;
    const recent = snapshot.events.slice(-this.maxActivity).reverse();

    const scopeRows = Object.entries(counters.byScope)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([scope, phases]) => {
        const cells = (['start', 'end', 'block', 'correct', 'info'] as HookEventPhase[])
          .map((phase) => `<td class="num">${phases[phase] ?? 0}</td>`)
          .join('');
        return `<tr><th scope="row">${escape(scope)}</th>${cells}</tr>`;
      })
      .join('');

    const sourceList = Object.entries(counters.bySourceHook)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 10)
      .map(([hook, count]) => `<li><code>${escape(hook)}</code> · ${count}</li>`)
      .join('');

    const activityRows = recent
      .map((event) => {
        const agentId = event.agent && typeof event.agent === 'object' && 'id' in event.agent
          ? String((event.agent as { id?: unknown }).id ?? '')
          : '';
        return `
          <tr>
            <td class="mono">${escape(event.ts)}</td>
            <td>${escape(event.scope)}</td>
            <td>${escape(event.phase)}</td>
            <td class="mono">${escape(event.source_hook)}</td>
            <td class="mono">${escape(agentId)}</td>
          </tr>`;
      })
      .join('');

    const errorBadge = error
      ? `<span class="pill tone-warning">fetch: ${escape(error)}</span>`
      : '';

    return `
      <header class="section-head">
        <div>
          <h3>Ledger runtime</h3>
          <p class="muted">
            ${total} evenement(s) capture(s) · genere ${escape(snapshot.generatedAt)}
            ${lastSuccessAt ? `· dernier fetch ${escape(lastSuccessAt)}` : ''}
          </p>
        </div>
        ${errorBadge}
      </header>

      <div class="grid-2">
        <section>
          <h4>Compteurs par scope / phase</h4>
          <table class="ledger-table">
            <thead>
              <tr>
                <th scope="col">scope</th>
                <th scope="col">start</th>
                <th scope="col">end</th>
                <th scope="col">block</th>
                <th scope="col">correct</th>
                <th scope="col">info</th>
              </tr>
            </thead>
            <tbody>
              ${scopeRows || '<tr><td colspan="6" class="muted">Aucun evenement.</td></tr>'}
            </tbody>
          </table>
        </section>

        <section>
          <h4>Hooks contributeurs</h4>
          <ul class="ledger-list">
            ${sourceList || '<li class="muted">Aucun hook actif.</li>'}
          </ul>
        </section>
      </div>

      <section>
        <h4>Activite recente (${recent.length})</h4>
        <table class="ledger-table ledger-activity">
          <thead>
            <tr>
              <th scope="col">ts</th>
              <th scope="col">scope</th>
              <th scope="col">phase</th>
              <th scope="col">hook</th>
              <th scope="col">agent</th>
            </tr>
          </thead>
          <tbody>
            ${activityRows || '<tr><td colspan="5" class="muted">Aucune activite.</td></tr>'}
          </tbody>
        </table>
      </section>
    `;
  }
}

function escape(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Narrow re-exports for tests and main.ts. */
export type { HookEventScope, HookEventPhase };
