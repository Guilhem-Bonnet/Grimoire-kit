/**
 * pheromoneBoard.ts — mirror of grimoire.tools.stigmergy schema (Python side).
 *
 * Contract source of truth : grimoire-kit/src/grimoire/tools/stigmergy.py
 * (board version "1.0.0"). This module provides the TypeScript reader for
 * the durable board written at ``_grimoire-output/pheromone-board.json``
 * by the Python stigmergy layer.
 *
 * Keep in sync : any change to the Python schema (new pheromone type,
 * new field) must be reflected here; the contract tests enforce
 * round-trip compatibility against a sample written by the Python side.
 */

import { z } from 'zod';

export const PHEROMONE_BOARD_SCHEMA_VERSION = '1.0.0';

export const PHEROMONE_TYPES = [
  'NEED',
  'ALERT',
  'OPPORTUNITY',
  'PROGRESS',
  'COMPLETE',
  'BLOCK'
] as const;

export type PheromoneType = (typeof PHEROMONE_TYPES)[number];

export const PheromoneSchema = z
  .object({
    pheromone_id: z.string().min(1),
    pheromone_type: z.enum(PHEROMONE_TYPES),
    location: z.string().min(1),
    text: z.string(),
    emitter: z.string().min(1),
    timestamp: z.string().min(1),
    intensity: z.number().min(0).max(1),
    tags: z.array(z.string()).default([]),
    reinforcements: z.number().int().nonnegative().default(0),
    reinforced_by: z.array(z.string()).default([]),
    resolved: z.boolean().default(false),
    resolved_by: z.string().nullable().optional(),
    resolved_at: z.string().nullable().optional()
  })
  .passthrough();

export type Pheromone = z.infer<typeof PheromoneSchema>;

export const PheromoneBoardSchema = z.object({
  version: z.string(),
  half_life_hours: z.number().positive(),
  pheromones: z.array(PheromoneSchema)
});

export type PheromoneBoard = z.infer<typeof PheromoneBoardSchema>;

export interface PheromoneFilter {
  /** Keep only unresolved pheromones. */
  activeOnly?: boolean;
  /** Filter by pheromone_type. */
  type?: PheromoneType;
  /** Filter by location prefix (e.g. "preflight/"). */
  locationPrefix?: string;
  /** Cap output at the most-recent N entries (timestamp desc). */
  limit?: number;
}

/**
 * Parse + validate a board blob (raw JSON string). Returns an empty board
 * when the input is empty/invalid (mirrors the resilience contract of the
 * hookEvents reader: a missing or corrupted file never blocks the UI).
 */
export function parsePheromoneBoard(raw: string): PheromoneBoard {
  const trimmed = raw.trim();
  if (!trimmed) {
    return emptyPheromoneBoard();
  }
  let data: unknown;
  try {
    data = JSON.parse(trimmed);
  } catch {
    return emptyPheromoneBoard();
  }
  const parsed = PheromoneBoardSchema.safeParse(data);
  if (!parsed.success) {
    return emptyPheromoneBoard();
  }
  return parsed.data;
}

export function emptyPheromoneBoard(): PheromoneBoard {
  return {
    version: PHEROMONE_BOARD_SCHEMA_VERSION,
    half_life_hours: 72,
    pheromones: []
  };
}

/** Apply filters to the board's pheromones (pure). */
export function filterPheromones(
  board: PheromoneBoard,
  filter: PheromoneFilter = {}
): Pheromone[] {
  let out = board.pheromones.slice();
  if (filter.activeOnly) {
    out = out.filter((p) => !p.resolved);
  }
  if (filter.type) {
    const wanted = filter.type;
    out = out.filter((p) => p.pheromone_type === wanted);
  }
  if (filter.locationPrefix) {
    const prefix = filter.locationPrefix;
    out = out.filter((p) => p.location.startsWith(prefix));
  }
  out.sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0));
  if (typeof filter.limit === 'number' && filter.limit >= 0) {
    out = out.slice(0, filter.limit);
  }
  return out;
}

/**
 * Aggregate intensity per (location, type) cell, applying optional
 * exponential decay against ``now``.
 */
export interface HeatmapCell {
  location: string;
  type: PheromoneType;
  intensity: number;
  count: number;
  lastTimestamp: string;
}

export interface HeatmapOptions {
  /** Reference time for decay; ISO-8601. Defaults to no decay. */
  now?: string;
  /** Override the board's half_life_hours (advanced). */
  halfLifeHours?: number;
  /** Skip resolved pheromones. Defaults to true. */
  activeOnly?: boolean;
}

export interface PheromoneCounters {
  total: number;
  active: number;
  resolved: number;
  byType: Record<PheromoneType, number>;
  byEmitter: Record<string, number>;
}

export function computePheromoneCounters(board: PheromoneBoard): PheromoneCounters {
  const byType = {} as Record<PheromoneType, number>;
  for (const t of PHEROMONE_TYPES) {
    byType[t] = 0;
  }
  const byEmitter: Record<string, number> = {};
  let active = 0;
  let resolved = 0;
  for (const p of board.pheromones) {
    byType[p.pheromone_type] += 1;
    byEmitter[p.emitter] = (byEmitter[p.emitter] ?? 0) + 1;
    if (p.resolved) {
      resolved += 1;
    } else {
      active += 1;
    }
  }
  return {
    total: board.pheromones.length,
    active,
    resolved,
    byType,
    byEmitter
  };
}

/**
 * Build the heatmap (location × type) used by the observability surface.
 * Intensity is summed across pheromones, with optional half-life decay
 * applied against ``options.now``.
 */
export function buildPheromoneHeatmap(
  board: PheromoneBoard,
  options: HeatmapOptions = {}
): HeatmapCell[] {
  const activeOnly = options.activeOnly ?? true;
  const halfLifeHours = options.halfLifeHours ?? board.half_life_hours;
  const decayEnabled = typeof options.now === 'string' && halfLifeHours > 0;
  const nowMs = decayEnabled ? Date.parse(options.now ?? '') : Number.NaN;
  const halfLifeMs = halfLifeHours * 3600 * 1000;
  const cells = new Map<string, HeatmapCell>();
  for (const p of board.pheromones) {
    if (activeOnly && p.resolved) {
      continue;
    }
    let weight = p.intensity;
    if (decayEnabled && !Number.isNaN(nowMs)) {
      const tsMs = Date.parse(p.timestamp);
      if (!Number.isNaN(tsMs)) {
        const ageMs = Math.max(0, nowMs - tsMs);
        weight = p.intensity * Math.pow(0.5, ageMs / halfLifeMs);
      }
    }
    const key = `${p.location}|${p.pheromone_type}`;
    const existing = cells.get(key);
    if (existing) {
      existing.intensity += weight;
      existing.count += 1;
      if (p.timestamp > existing.lastTimestamp) {
        existing.lastTimestamp = p.timestamp;
      }
    } else {
      cells.set(key, {
        location: p.location,
        type: p.pheromone_type,
        intensity: weight,
        count: 1,
        lastTimestamp: p.timestamp
      });
    }
  }
  return Array.from(cells.values()).sort((a, b) => b.intensity - a.intensity);
}

/**
 * Snapshot suitable for static publication (``.generated/public/pheromone-board.json``).
 * Consumed by the browser via the polling client.
 */
export interface PheromoneBoardSnapshot {
  schemaVersion: string;
  generatedAt: string;
  board: PheromoneBoard;
  counters: PheromoneCounters;
  heatmap: HeatmapCell[];
}

export interface CreateSnapshotOptions {
  generatedAt?: string;
  /** Cap raw pheromones in the snapshot. Defaults to 500. */
  limit?: number;
  /** Reference time used for decay in the heatmap. Defaults to ``generatedAt``. */
  now?: string;
}

export function createPheromoneBoardSnapshot(
  board: PheromoneBoard,
  options: CreateSnapshotOptions = {}
): PheromoneBoardSnapshot {
  const generatedAt = options.generatedAt ?? new Date().toISOString();
  const limit = options.limit ?? 500;
  const limited: PheromoneBoard = {
    version: board.version,
    half_life_hours: board.half_life_hours,
    pheromones: filterPheromones(board, { limit })
  };
  const heatmapOptions: HeatmapOptions = { activeOnly: true };
  const decayNow = options.now ?? generatedAt;
  if (decayNow) {
    heatmapOptions.now = decayNow;
  }
  return {
    schemaVersion: PHEROMONE_BOARD_SCHEMA_VERSION,
    generatedAt,
    board: limited,
    counters: computePheromoneCounters(board),
    heatmap: buildPheromoneHeatmap(board, heatmapOptions)
  };
}
