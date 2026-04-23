import { createViewShell, type View } from './view-common';
import type { OfficeCharacter } from '@game/state/office-view';
import type { OfficePlacement } from '@game/state/office-placement';
import { createGrimoireSpritePreset } from '@game/rendering/grimoire-sprite-preset';
import {
  loadSpriteImages,
  type SpriteImageLike
} from '@game/rendering/sprite-image-loader';
import { buildOfficeDrawPlan } from '@game/rendering/office-draw-plan';
import { renderOfficeDrawPlanToCanvas } from '@game/rendering/office-canvas-renderer';
import type { SpriteRegistry } from '@game/rendering/sprite-registry';

const CELL_PX = 24;

/**
 * Office view — pixel-agent mosaic.
 *
 * Uses the V3.S3.7 part 2 sprite renderer stack:
 *   - `createGrimoireSpritePreset` wires the 9 curated character sheets
 *   - `loadSpriteImages` preloads the bitmaps (served by the dev plugin
 *     at `/assets/characters/`)
 *   - `buildOfficeDrawPlan` projects the live office state
 *   - `renderOfficeDrawPlanToCanvas` paints on the canvas every frame
 *
 * Falls back to colored squares if an asset fails to load so the
 * surface never goes blank when running offline.
 */
export function createOfficeView(): View {
  let bodyRef: HTMLElement | null = null;
  let canvasRef: HTMLCanvasElement | null = null;
  let images: ReadonlyMap<string, SpriteImageLike> = new Map();
  let registry: SpriteRegistry | null = null;
  let assetsReady = false;

  return {
    route: 'office',
    title: 'Agents · Office',
    status: 'live',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Agents · Office',
        subtitle: 'Placement libre + sprites pixel (V3.S3.7 part 2). Base characters + cosmétiques = rôles.',
        status: 'live'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;

      registry = createGrimoireSpritePreset({ baseUrl: '/assets/characters/' });
      void loadSpriteImages(registry).then((bundle) => {
        images = bundle.images;
        assetsReady = true;
        if (bodyRef) {
          const status = bodyRef.querySelector('[data-assets-status]');
          if (status) {
            const err = bundle.errors.length;
            status.textContent = err > 0
              ? `${bundle.images.size}/${bundle.images.size + err} sprites chargés (${err} en fallback)`
              : `${bundle.images.size} sprites chargés`;
          }
        }
      });
    },
    update(snapshot) {
      if (!bodyRef) return;
      const { office, placement } = snapshot;
      const { grid, characters, stateCounters } = office;

      const legend = Object.entries(stateCounters)
        .map(([state, count]) => `<span class="badge">${state}: ${count}</span>`)
        .join(' ');

      const canvasWidth = grid.cols * CELL_PX;
      const canvasHeight = grid.rows * (CELL_PX * 2); // 16x32 frames

      bodyRef.innerHTML = `
        <section class="panel">
          <h2 class="panel__title">État du bureau</h2>
          <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px;">${legend}</div>
          <div style="display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start;">
            <div>
              <canvas
                data-office-canvas
                width="${canvasWidth}"
                height="${canvasHeight}"
                style="background: var(--elev-2); border: 1px solid var(--line); image-rendering: pixelated; display:block;"
              ></canvas>
              <div class="muted" data-assets-status style="margin-top:8px; font-family: var(--mono); font-size:12px;">
                ${assetsReady ? 'sprites prêts' : 'chargement des sprites…'}
              </div>
            </div>
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

      canvasRef = bodyRef.querySelector<HTMLCanvasElement>('[data-office-canvas]');
      if (canvasRef && registry) {
        const plan = buildOfficeDrawPlan(office, placement, registry, { cellSize: CELL_PX });
        renderOfficeDrawPlanToCanvas(canvasRef, plan, images, {
          canvasWidth,
          canvasHeight,
          fallbackFill: '#FF6B3D'
        });
      }
    }
  };
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
