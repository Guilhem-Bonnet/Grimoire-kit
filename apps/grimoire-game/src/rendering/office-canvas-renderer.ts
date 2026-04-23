/**
 * rendering/office-canvas-renderer.ts — V3.S3.7 part 2.
 *
 * Consumes an `OfficeDrawPlan` (already sorted painter's-algorithm)
 * and a map of loaded sprite images, then paints the office on a
 * canvas-like context. The context surface is a strict subset of the
 * browser `CanvasRenderingContext2D` so tests can use a fake spy.
 */

import type { OfficeDrawPlan, DrawOp } from './office-draw-plan';
import type { SpriteImageLike } from './sprite-image-loader';

export interface CanvasLike {
  /** Fill a rectangle with the current `fillStyle`. */
  fillRect(x: number, y: number, w: number, h: number): void;
  /**
   * Draw a region of a source image onto the canvas. Signature matches
   * the 9-arg variant of `drawImage`.
   */
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
  ): void;
  /** Clear a rectangle. */
  clearRect(x: number, y: number, w: number, h: number): void;
  /** Set current fill colour. */
  fillStyle: string;
  /** Disable smoothing so pixel art stays crisp (default in browsers). */
  imageSmoothingEnabled?: boolean | undefined;
}

export interface RenderOfficeOptions {
  /** Canvas width to clear before painting. Default: computed from plan. */
  readonly canvasWidth?: number;
  /** Canvas height to clear before painting. Default: computed from plan. */
  readonly canvasHeight?: number;
  /** Colour used as a fallback square when a sheet is missing. Default `#8a8f98`. */
  readonly fallbackFill?: string;
  /** Fallback square size in pixels. Default: op.width. */
  readonly fallbackSize?: number;
}

export interface OfficeRenderReport {
  /** Total ops in the plan. */
  readonly total: number;
  /** Ops that rendered an image successfully. */
  readonly drawn: number;
  /** Ops that fell back to a colored square (missing image). */
  readonly fallback: number;
}

/**
 * Render the plan onto the given canvas context. Disables image
 * smoothing before the first draw so pixel art does not blur. Returns
 * a small report useful for status badges and tests.
 */
export function renderOfficeDrawPlan(
  ctx: CanvasLike,
  plan: OfficeDrawPlan,
  images: ReadonlyMap<string, SpriteImageLike>,
  options: RenderOfficeOptions = {}
): OfficeRenderReport {
  const { drawn, fallback, maxX, maxY } = clearAndCountExtents(ctx, plan, options);

  ctx.imageSmoothingEnabled = false;

  let drawnCount = 0;
  let fallbackCount = 0;

  for (const op of plan.ops) {
    const image = images.get(op.sheet.id);
    if (image && image.complete !== false) {
      const sx = op.frame.col * op.sheet.frameWidth;
      const sy = op.frame.row * op.sheet.frameHeight;
      ctx.drawImage(
        image,
        sx,
        sy,
        op.sheet.frameWidth,
        op.sheet.frameHeight,
        op.x,
        op.y,
        op.width,
        op.height
      );
      drawnCount += 1;
    } else {
      renderFallback(ctx, op, options);
      fallbackCount += 1;
    }
  }

  return {
    total: plan.ops.length,
    drawn: drawnCount,
    fallback: fallbackCount
    // maxX/maxY intentionally unused in return — already applied via clearRect
  } satisfies OfficeRenderReport;
}

function clearAndCountExtents(
  ctx: CanvasLike,
  plan: OfficeDrawPlan,
  options: RenderOfficeOptions
): { drawn: number; fallback: number; maxX: number; maxY: number } {
  let maxX = 0;
  let maxY = 0;
  for (const op of plan.ops) {
    if (op.x + op.width > maxX) maxX = op.x + op.width;
    if (op.y + op.height > maxY) maxY = op.y + op.height;
  }
  const width = options.canvasWidth ?? maxX;
  const height = options.canvasHeight ?? maxY;
  ctx.clearRect(0, 0, width, height);
  return { drawn: 0, fallback: 0, maxX, maxY };
}

function renderFallback(ctx: CanvasLike, op: DrawOp, options: RenderOfficeOptions): void {
  ctx.fillStyle = options.fallbackFill ?? '#8a8f98';
  const size = options.fallbackSize ?? Math.min(op.width, op.height);
  ctx.fillRect(op.x, op.y, size, size);
}

/**
 * Convenience helper for the common case: resolve the 2D context of a
 * real HTMLCanvasElement and render. Kept separate so the core
 * `renderOfficeDrawPlan` stays DOM-free for tests.
 */
export function renderOfficeDrawPlanToCanvas(
  canvas: HTMLCanvasElement,
  plan: OfficeDrawPlan,
  images: ReadonlyMap<string, SpriteImageLike>,
  options: RenderOfficeOptions = {}
): OfficeRenderReport | null {
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  return renderOfficeDrawPlan(ctx as unknown as CanvasLike, plan, images, options);
}
