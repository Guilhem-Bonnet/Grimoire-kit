import type { DashboardStateSnapshot } from '../dashboard-store';

export interface View {
  route: string;
  title: string;
  status: 'live' | 'partial';
  mount: (root: HTMLElement) => void;
  update: (snapshot: DashboardStateSnapshot) => void;
  unmount?: () => void;
}

export function createViewShell(params: {
  title: string;
  subtitle: string;
  status: 'live' | 'partial';
}): { root: HTMLElement; body: HTMLElement } {
  const root = document.createElement('section');
  root.className = 'view';
  root.innerHTML = `
    <header class="view__header">
      <div>
        <h1 class="view__title"></h1>
        <p class="view__subtitle"></p>
      </div>
      <span class="view__status view__status--${params.status}">${
        params.status === 'live' ? 'LIVE DATA' : 'PARTIAL — PLACEHOLDER'
      }</span>
    </header>
    <div class="view__body"></div>
  `;
  const title = root.querySelector<HTMLHeadingElement>('.view__title')!;
  const subtitle = root.querySelector<HTMLParagraphElement>('.view__subtitle')!;
  title.textContent = params.title;
  subtitle.textContent = params.subtitle;
  const body = root.querySelector<HTMLDivElement>('.view__body')!;
  return { root, body };
}
