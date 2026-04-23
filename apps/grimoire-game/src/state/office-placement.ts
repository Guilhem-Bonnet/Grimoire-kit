/**
 * office-placement.ts — V3.S3.7 free placement + collision avoidance.
 *
 * The pure projection in `office-view.ts` assigns each agent a deterministic
 * "seat" via a hash. That works perfectly when agents are few, but two agent
 * ids can hash to the same cell → visual overlap → "bug de déplacement" in
 * the renderer.
 *
 * This module is the collision guard. It takes the `OfficeView.characters`
 * and produces a placement map (`agentId → {col, row}`) guaranteed to:
 *
 *   - be collision-free (each cell holds at most one character), assuming
 *     the total number of characters fits in `cols × rows` — otherwise
 *     overflow characters are reported in `overflow` and left unplaced;
 *   - be **stable** across frames: when `previous` is provided, agents that
 *     were already placed keep their previous cell whenever the cell is
 *     still free. Only newcomers and evicted agents move. This prevents
 *     the sprite-popping effect typical of naive re-hashing.
 *   - fall back to a deterministic spiral search (BFS over Chebyshev rings)
 *     when an agent's preferred seat is taken, so the result is reproducible
 *     for a given (sorted) input.
 *
 * Contract: pure (no DOM, no fs, no clock).
 */

import type { OfficeCharacter, OfficeGrid, OfficeSeat } from './office-view';

export const OFFICE_PLACEMENT_SCHEMA_VERSION = '1.0.0';

export interface OfficePlacement {
  schemaVersion: string;
  grid: OfficeGrid;
  /** Resolved cell per agent. Stable identity: agentId → seat. */
  seats: ReadonlyMap<string, OfficeSeat>;
  /** Agents that could not be placed because the grid was full. */
  overflow: readonly string[];
}

export interface ResolvePlacementOptions {
  /**
   * Previous placement, used to anchor agents that are still present and
   * keep them on their previous cell when possible. Brand-new agents and
   * evicted ones fall back to their preferred seat (spiral search).
   */
  previous?: OfficePlacement | null;
}

function keyOf(seat: OfficeSeat): number {
  // Grid cells never exceed ~10⁴ so a single int key is enough.
  return seat.row * 10_000 + seat.col;
}

function inBounds(seat: OfficeSeat, grid: OfficeGrid): boolean {
  return seat.col >= 0 && seat.col < grid.cols && seat.row >= 0 && seat.row < grid.rows;
}

/**
 * Iterate cells of the grid in Chebyshev-distance order around `center`.
 * Within each ring we walk clockwise starting at the top-left corner, which
 * yields a deterministic order for collision fallback.
 */
function* spiralFrom(center: OfficeSeat, grid: OfficeGrid): Generator<OfficeSeat> {
  const maxRing = Math.max(grid.cols, grid.rows);
  for (let ring = 0; ring <= maxRing; ring += 1) {
    if (ring === 0) {
      if (inBounds(center, grid)) yield { col: center.col, row: center.row };
      continue;
    }
    const top = center.row - ring;
    const bottom = center.row + ring;
    const left = center.col - ring;
    const right = center.col + ring;

    // Top row (left → right).
    for (let c = left; c <= right; c += 1) {
      const seat = { col: c, row: top };
      if (inBounds(seat, grid)) yield seat;
    }
    // Right column (top+1 → bottom).
    for (let r = top + 1; r <= bottom; r += 1) {
      const seat = { col: right, row: r };
      if (inBounds(seat, grid)) yield seat;
    }
    // Bottom row (right-1 → left).
    for (let c = right - 1; c >= left; c -= 1) {
      const seat = { col: c, row: bottom };
      if (inBounds(seat, grid)) yield seat;
    }
    // Left column (bottom-1 → top+1).
    for (let r = bottom - 1; r > top; r -= 1) {
      const seat = { col: left, row: r };
      if (inBounds(seat, grid)) yield seat;
    }
  }
}

function findFreeCell(
  preferred: OfficeSeat,
  grid: OfficeGrid,
  occupied: Set<number>
): OfficeSeat | null {
  for (const seat of spiralFrom(preferred, grid)) {
    if (!occupied.has(keyOf(seat))) {
      return seat;
    }
  }
  return null;
}

function clampToGrid(seat: OfficeSeat, grid: OfficeGrid): OfficeSeat {
  return {
    col: Math.max(0, Math.min(grid.cols - 1, seat.col)),
    row: Math.max(0, Math.min(grid.rows - 1, seat.row))
  };
}

/**
 * Compute a collision-free placement for the given characters.
 *
 * Algorithm (two-pass):
 *   1. Anchor pass — any agent already in `previous` whose previous cell is
 *      still inside the grid gets locked on its previous cell first, so
 *      stable frame-to-frame identity wins over preferred seat.
 *   2. Newcomer pass — unplaced agents get their `character.seat` as a
 *      preferred target, then spiral outward until a free cell is found.
 *
 * Both passes iterate agents in `agentId` ascending order so the result is
 * deterministic regardless of the input `characters` array order.
 */
export function resolveOfficePlacement(
  characters: readonly OfficeCharacter[],
  grid: OfficeGrid,
  options: ResolvePlacementOptions = {}
): OfficePlacement {
  const seats = new Map<string, OfficeSeat>();
  const occupied = new Set<number>();
  const overflow: string[] = [];

  const sorted = characters
    .slice()
    .sort((a, b) => (a.agentId < b.agentId ? -1 : a.agentId > b.agentId ? 1 : 0));

  const prev = options.previous?.seats ?? null;

  // Pass 1: anchor stable agents on their previous cell.
  if (prev) {
    for (const character of sorted) {
      const last = prev.get(character.agentId);
      if (!last) continue;
      const clamped = clampToGrid(last, grid);
      const k = keyOf(clamped);
      if (!occupied.has(k)) {
        occupied.add(k);
        seats.set(character.agentId, clamped);
      }
    }
  }

  // Pass 2: place newcomers / evicted via spiral from their preferred seat.
  for (const character of sorted) {
    if (seats.has(character.agentId)) continue;
    const preferred = clampToGrid(character.seat, grid);
    const cell = findFreeCell(preferred, grid, occupied);
    if (!cell) {
      overflow.push(character.agentId);
      continue;
    }
    occupied.add(keyOf(cell));
    seats.set(character.agentId, cell);
  }

  return {
    schemaVersion: OFFICE_PLACEMENT_SCHEMA_VERSION,
    grid,
    seats,
    overflow
  };
}

/**
 * Utility: is the given cell inside the grid and currently free in the
 * provided placement? Exposed for renderers that want to drop-hint a
 * drag-and-drop target.
 */
export function isCellFree(
  placement: OfficePlacement,
  seat: OfficeSeat
): boolean {
  if (!inBounds(seat, placement.grid)) return false;
  for (const existing of placement.seats.values()) {
    if (existing.col === seat.col && existing.row === seat.row) return false;
  }
  return true;
}
