import { describe, it, expect } from 'vitest';
import type { HookEvent } from '../../src/contracts/hookEvents';
import { createOfficeView } from '../../src/state/office-view';
import { resolveOfficePlacement } from '../../src/state/office-placement';
import { createSpriteRegistry } from '../../src/rendering/sprite-registry';
import { buildOfficeDrawPlan } from '../../src/rendering/office-draw-plan';
import {
  renderOfficeDrawPlan,
  type CanvasLike
} from '../../src/rendering/office-canvas-renderer';
import type { SpriteImageLike } from '../../src/rendering/sprite-image-loader';

interface DrawImageCall {
  image: SpriteImageLike;
  args: readonly number[];
}

class SpyCanvas implements CanvasLike {
  public fillStyle = '';
  public imageSmoothingEnabled: boolean | undefined = undefined;
  public fillRectCalls: Array<readonly number[]> = [];
  public clearRectCalls: Array<readonly number[]> = [];
  public drawImageCalls: DrawImageCall[] = [];

  fillRect(x: number, y: number, w: number, h: number): void {
    this.fillRectCalls.push([x, y, w, h]);
  }
  clearRect(x: number, y: number, w: number, h: number): void {
    this.clearRectCalls.push([x, y, w, h]);
  }
  drawImage(
    image: SpriteImageLike,
    sx: number,
    sy: number,
    sw: number,
    sh: number,
    dx: number,
    dy: number,
    dw: number,
    dh: number
  ): void {
    this.drawImageCalls.push({ image, args: [sx, sy, sw, sh, dx, dy, dw, dh] });
  }
}

function fakeImage(): SpriteImageLike {
  return { src: 'fake', width: 16, height: 32, complete: true };
}

function evt(agentId: string, role: string): HookEvent {
  return {
    schema_version: '1.0',
    event_id: `e-${agentId}`,
    ts: '2026-04-23T10:00:00.000Z',
    scope: 'subagent',
    phase: 'start',
    source_hook: 't',
    agent: { id: agentId, role },
    payload: {}
  };
}

function buildFixture(): {
  plan: ReturnType<typeof buildOfficeDrawPlan>;
  images: Map<string, SpriteImageLike>;
} {
  const registry = createSpriteRegistry({
    sheets: [
      { id: 'base', url: 'b.png', frameWidth: 16, frameHeight: 32, cols: 4, rows: 2 }
    ],
    roles: {
      dev: {
        base: { sheetId: 'base', col: 0, row: 0 },
        cosmetics: [{ sheetId: 'base', col: 1, row: 0 }]
      }
    },
    defaultRole: { base: { sheetId: 'base', col: 0, row: 0 } }
  });
  const office = createOfficeView([evt('dev-1', 'dev')]);
  const placement = resolveOfficePlacement(office.characters, office.grid);
  const plan = buildOfficeDrawPlan(office, placement, registry, { cellSize: 16 });
  const images = new Map<string, SpriteImageLike>([['base', fakeImage()]]);
  return { plan, images };
}

describe('office-canvas-renderer', () => {
  it('draws every op when images are loaded', () => {
    const { plan, images } = buildFixture();
    const ctx = new SpyCanvas();
    const report = renderOfficeDrawPlan(ctx, plan, images);
    expect(report.total).toBe(2); // base + 1 cosmetic
    expect(report.drawn).toBe(2);
    expect(report.fallback).toBe(0);
    expect(ctx.drawImageCalls.length).toBe(2);
    expect(ctx.imageSmoothingEnabled).toBe(false);
  });

  it('falls back to a colored rect when an image is missing', () => {
    const { plan } = buildFixture();
    const ctx = new SpyCanvas();
    const report = renderOfficeDrawPlan(ctx, plan, new Map());
    expect(report.drawn).toBe(0);
    expect(report.fallback).toBe(2);
    expect(ctx.fillRectCalls.length).toBe(2);
  });

  it('uses custom fallback fill colour', () => {
    const { plan } = buildFixture();
    const ctx = new SpyCanvas();
    renderOfficeDrawPlan(ctx, plan, new Map(), { fallbackFill: '#FF6B3D' });
    expect(ctx.fillStyle).toBe('#FF6B3D');
  });

  it('clears the canvas at the requested explicit size', () => {
    const { plan, images } = buildFixture();
    const ctx = new SpyCanvas();
    renderOfficeDrawPlan(ctx, plan, images, { canvasWidth: 320, canvasHeight: 240 });
    expect(ctx.clearRectCalls).toEqual([[0, 0, 320, 240]]);
  });

  it('passes correct source rect from frame col/row to drawImage', () => {
    const { plan, images } = buildFixture();
    const ctx = new SpyCanvas();
    renderOfficeDrawPlan(ctx, plan, images);
    const base = ctx.drawImageCalls[0]!;
    // base frame: col 0, row 0 → sx=0, sy=0, sw=16, sh=32
    expect(base.args.slice(0, 4)).toEqual([0, 0, 16, 32]);
    const cosmetic = ctx.drawImageCalls[1]!;
    // cosmetic: col 1, row 0 → sx=16, sy=0
    expect(cosmetic.args.slice(0, 4)).toEqual([16, 0, 16, 32]);
  });

  it('returns empty report for empty plan', () => {
    const ctx = new SpyCanvas();
    const report = renderOfficeDrawPlan(
      ctx,
      { schemaVersion: '1.0.0', ops: [], missingAgents: [], overflowAgents: [] },
      new Map()
    );
    expect(report.total).toBe(0);
    expect(report.drawn).toBe(0);
    expect(report.fallback).toBe(0);
  });
});
