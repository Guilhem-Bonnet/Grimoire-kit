/**
 * rendering/office-draw-plan.ts — V3.S3.7 part 2.
 *
 * Pure projection that turns `(OfficeView, OfficePlacement, SpriteRegistry)`
 * into a sorted list of draw operations. The result is a plain data
 * structure — no DOM, no canvas — so it is trivially testable and can
 * be consumed by any renderer (2D canvas, WebGL, SVG, print, etc.).
 *
 * Painter's algorithm: ops are sorted by `y` ascending then `x`
 * ascending, so a naive renderer iterating the list produces a
 * back-to-front order where bottom sprites visually overlap top ones.
 *
 * One character emits:
 *   1. one `sprite` op for the base/state frame;
 *   2. N `sprite` ops for each cosmetic overlay, in registry order,
 *      drawn at the same position so accessories layer on the base.
 */

import type { OfficeCharacter, OfficeView } from '../state/office-view';
import type { OfficePlacement } from '../state/office-placement';
import {
  resolveAppearance,
  resolveFrame,
  type SpriteFrame,
  type SpriteRegistry,
  type SpriteSheet
} from './sprite-registry';

export const OFFICE_DRAW_PLAN_SCHEMA_VERSION = '1.0.0';

export interface DrawOp {
  /** Pixel coordinate on the output surface. */
  readonly x: number;
  readonly y: number;
  /** Destination size in pixels (may differ from frame size if scaled). */
  readonly width: number;
  readonly height: number;
  /** Source frame reference. */
  readonly frame: SpriteFrame;
  /** Resolved sheet (avoids a second map lookup during render). */
  readonly sheet: SpriteSheet;
  /**
   * Layer hint:
   *   - `base`: the main character body
   *   - `cosmetic-{i}`: the i-th overlay (0-based)
   */
  readonly layer: string;
  /** Stable agent id this op belongs to. Useful for picking/tests. */
  readonly agentId: string;
}

export interface OfficeDrawPlan {
  readonly schemaVersion: string;
  readonly ops: readonly DrawOp[];
  /** Characters that had no sprite in registry (skipped in ops). */
  readonly missingAgents: readonly string[];
  /** Characters in office but not placed (placement.overflow). */
  readonly overflowAgents: readonly string[];
}

export interface OfficeDrawPlanOptions {
  /** Pixel size of one office cell (renders sprites at this size). Default: sprite frame size. */
  readonly cellSize?: number;
  /** Origin offset applied to every op.x. Default 0. */
  readonly originX?: number;
  /** Origin offset applied to every op.y. Default 0. */
  readonly originY?: number;
  /**
   * When true, scale base & cosmetics to `cellSize × cellSize` regardless
   * of the sheet frame aspect ratio. Default false — sprites preserve
   * their native aspect and are top-left aligned on the cell.
   */
  readonly fitSquare?: boolean;
}

/**
 * Compute a deterministic draw plan from the office state.
 *
 * Determinism: ops are sorted by (y, x, agentId, layer). Input order
 * does not affect output. Two calls with the same inputs return ops
 * with the same ordering and identical fields.
 */
export function buildOfficeDrawPlan(
  office: OfficeView,
  placement: OfficePlacement,
  registry: SpriteRegistry,
  options: OfficeDrawPlanOptions = {}
): OfficeDrawPlan {
  const originX = options.originX ?? 0;
  const originY = options.originY ?? 0;

  const ops: DrawOp[] = [];
  const missing: string[] = [];

  for (const character of office.characters) {
    const seat = placement.seats.get(character.agentId);
    if (!seat) continue; // listed separately via overflowAgents

    const appearance = registry.roles.get(character.role)
      ?? registry.roles.get('__default__');
    if (!appearance) {
      missing.push(character.agentId);
      continue;
    }

    const baseFrame = resolveFrame(appearance, character.state);
    const baseSheet = registry.sheets.get(baseFrame.sheetId);
    if (!baseSheet) {
      missing.push(character.agentId);
      continue;
    }

    const cellSize = options.cellSize ?? baseSheet.frameWidth;
    const dims = resolveDims(baseSheet, cellSize, options.fitSquare === true);
    const x = originX + seat.col * cellSize;
    const y = originY + seat.row * cellSize;

    ops.push({
      x,
      y,
      width: dims.width,
      height: dims.height,
      frame: baseFrame,
      sheet: baseSheet,
      layer: 'base',
      agentId: character.agentId
    });

    const cosmetics = appearance.cosmetics ?? [];
    for (let i = 0; i < cosmetics.length; i += 1) {
      const cosmetic = cosmetics[i]!;
      const sheet = registry.sheets.get(cosmetic.sheetId);
      if (!sheet) continue;
      const overlayDims = resolveDims(sheet, cellSize, options.fitSquare === true);
      ops.push({
        x,
        y,
        width: overlayDims.width,
        height: overlayDims.height,
        frame: cosmetic,
        sheet,
        layer: `cosmetic-${i}`,
        agentId: character.agentId
      });
    }
  }

  ops.sort((a, b) => {
    if (a.y !== b.y) return a.y - b.y;
    if (a.x !== b.x) return a.x - b.x;
    if (a.agentId !== b.agentId) return a.agentId < b.agentId ? -1 : 1;
    return a.layer < b.layer ? -1 : a.layer > b.layer ? 1 : 0;
  });

  return {
    schemaVersion: OFFICE_DRAW_PLAN_SCHEMA_VERSION,
    ops,
    missingAgents: missing,
    overflowAgents: placement.overflow
  };
}

function resolveDims(
  sheet: SpriteSheet,
  cellSize: number,
  fitSquare: boolean
): { width: number; height: number } {
  if (fitSquare) return { width: cellSize, height: cellSize };
  // Preserve aspect ratio, scale so width = cellSize.
  const scale = cellSize / sheet.frameWidth;
  return {
    width: cellSize,
    height: Math.round(sheet.frameHeight * scale)
  };
}

/**
 * Helper: filter the plan to a single agent (for debug overlays / picking).
 */
export function drawPlanForAgent(plan: OfficeDrawPlan, agentId: string): readonly DrawOp[] {
  return plan.ops.filter((op) => op.agentId === agentId);
}

/**
 * Helper: count ops per character. Useful to assert registry coverage
 * and cosmetic layering in tests.
 */
export function drawPlanOpsByAgent(plan: OfficeDrawPlan): Map<string, number> {
  const by = new Map<string, number>();
  for (const op of plan.ops) {
    by.set(op.agentId, (by.get(op.agentId) ?? 0) + 1);
  }
  return by;
}

export type { OfficeCharacter };
