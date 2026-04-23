import type { DashboardStateSnapshot } from '../dashboard-store';
import { createViewShell, type View } from './view-common';
import type { OfficeCharacter } from '@game/state/office-view';
import type { OfficePlacement } from '@game/state/office-placement';

const CELL_PX = 20;
const GAP_PX = 2;

/**
 * Office view — pixel-agent mosaic powered by `resolveOfficePlacement`
 * (free placement + collision avoidance, S3.7 part 1).
 *
 * Each agent shows up as a coloured cell on the grid, with its id and
 * state in the tooltip. Pure live — no pixel sprites yet (S3.7 part 2).
 */
export function createOfficeView(): View {
  let bodyRef: HTMLElement | null = null;
  return {
    route: 'office',
    title: 'Agents · Office',
    status: 'live',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Agents · Office',
        subtitle: 'Placement libre + évitement de collisions (V3.S3.7 part 1). Sprites pixel à venir en part 2.',
        status: 'live'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
    },
    update(snapshot) {
      if (!bodyRef) return;
      const { office, placement } = snapshot;
      const { grid, characters, stateCounters } = office;

      const legend = Object.entries(stateCounters)
        .map(([state, count]) => `<span class="badge">${state}: ${count}</span>`)
        .join(' ');

      bodyRef.innerHTML = `
        <section class="panel">
          <h2 class="panel__title">État du bureau</h2>
          <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px;">${legend}</div>
          <div style="display:flex; gap:24px; flex-wrap:wrap;">
            <div>${renderOfficeGrid(characters, placement, grid)}</div>
            <div style="flex:1; min-width:280px;">
              <h3 class="panel__title">Agents placés</h3>
              ${renderAgentList(characters, placement)}
              ${placement.overflow.length > 0
                ? `<div class="banner"><strong>OVERFLOW</strong>${placement.overflow.length} agent(s) sans cellule libre : ${placement.overflow.map((id) => escape(id)).join(', ')}</div>`
                : ''}
            </div>
          </div>
        </section>
      `;
    }
  };
}

function renderOfficeGrid(
  characters: readonly OfficeCharacter[],
  placement: OfficePlacement,
  grid: { cols: number; rows: number }
): string {
  const byCell = new Map<string, OfficeCharacter>();
  for (const char of characters) {
    const seat = placement.seats.get(char.agentId);
    if (seat) byCell.set(`${seat.row}:${seat.col}`, char);
  }
  const cells: string[] = [];
  for (let row = 0; row < grid.rows; row += 1) {
    for (let col = 0; col < grid.cols; col += 1) {
      const char = byCell.get(`${row}:${col}`);
      if (char) {
        cells.push(
          `<div class="office-cell" data-state="${escape(char.state)}" title="${escape(char.agentId)} · ${escape(char.role)} · ${escape(char.state)}"></div>`
        );
      } else {
        cells.push('<div class="office-cell"></div>');
      }
    }
  }
  const width = grid.cols * CELL_PX + (grid.cols - 1) * GAP_PX;
  return `<div class="office-grid" style="grid-template-columns: repeat(${grid.cols}, ${CELL_PX}px); width:${width + 16}px;">${cells.join('')}</div>`;
}

function renderAgentList(
  characters: readonly OfficeCharacter[],
  placement: OfficePlacement
): string {
  if (characters.length === 0) {
    return '<div class="empty">Aucun agent actif.</div>';
  }
  const rows = characters
    .slice()
    .sort((a, b) => (a.agentId < b.agentId ? -1 : 1))
    .map((char) => {
      const seat = placement.seats.get(char.agentId);
      return `
        <tr>
          <td>${escape(char.agentId)}</td>
          <td>${escape(char.role)}</td>
          <td><span class="badge">${escape(char.state)}</span></td>
          <td>${seat ? `(${seat.col}, ${seat.row})` : '—'}</td>
        </tr>`;
    })
    .join('');
  return `
    <table class="table">
      <thead><tr><th>agent</th><th>rôle</th><th>état</th><th>cellule</th></tr></thead>
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
