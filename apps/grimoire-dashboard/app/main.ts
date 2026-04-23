import './styles.css';
import { DashboardStore } from '../src/dashboard-store';
import { DashboardWsClient } from '../src/dashboard-ws-client';
import { createObservabilityView } from '../src/views/observability-view';
import { createOfficeView } from '../src/views/office-view';
import { createKanbanView } from '../src/views/kanban-view';
import { createFlowsView } from '../src/views/flows-view';
import { createConsoleView } from '../src/views/console-view';
import type { View } from '../src/views/view-common';

const DEFAULT_ROUTE = 'observability';

function resolveWsUrl(): string {
  // The Vite dev server proxies /ws → the node WS server on :4175.
  // In production, the same origin is expected to proxy /ws.
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws`;
}

function main(): void {
  const shell = document.querySelector<HTMLElement>('#shell')!;
  const mount = document.querySelector<HTMLElement>('#view-mount')!;
  const connState = document.querySelector<HTMLElement>('#conn-state')!;
  const connSource = document.querySelector<HTMLElement>('#conn-source')!;
  const connCount = document.querySelector<HTMLElement>('#conn-count')!;

  const store = new DashboardStore();
  const client = new DashboardWsClient({
    url: resolveWsUrl(),
    onMessage: (msg) => store.onServerMessage(msg),
    onState: (state, detail) => store.onConnectionState(state, detail)
  });

  const views: Record<string, View> = {
    observability: createObservabilityView(),
    office: createOfficeView(),
    kanban: createKanbanView(),
    flows: createFlowsView(),
    console: createConsoleView({ sendCommand: (cmd) => client.send(cmd) })
  };

  let currentRoute: string | null = null;
  let currentView: View | null = null;

  function activate(route: string): void {
    const target = views[route] ?? views[DEFAULT_ROUTE]!;
    if (currentView && currentView.route === target.route) return;
    if (currentView?.unmount) currentView.unmount();
    currentView = target;
    currentRoute = target.route;
    target.mount(mount);
    target.update(store.getState());
    for (const link of document.querySelectorAll<HTMLAnchorElement>('.nav__link')) {
      link.setAttribute('aria-current', link.dataset.route === target.route ? 'page' : 'false');
    }
  }

  function readRouteFromHash(): string {
    const match = /^#\/([\w-]+)/.exec(location.hash);
    return match?.[1] ?? DEFAULT_ROUTE;
  }

  store.subscribe((snapshot) => {
    shell.dataset.connection = snapshot.connection;
    connState.textContent = snapshot.connection + (snapshot.connectionDetail ? ` · ${snapshot.connectionDetail}` : '');
    connSource.textContent = snapshot.source ?? '—';
    connCount.textContent = String(snapshot.receivedCount);
    if (currentView) currentView.update(snapshot);
  });

  window.addEventListener('hashchange', () => activate(readRouteFromHash()));
  activate(readRouteFromHash());
  if (currentRoute === DEFAULT_ROUTE && location.hash === '') {
    history.replaceState(null, '', `#/${DEFAULT_ROUTE}`);
  }

  client.start();
}

main();
