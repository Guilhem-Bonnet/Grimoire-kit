/**
 * pheromone-panel-view.ts — V4.4.b BM-20 Pheromone board panel projection.
 *
 * Pure function that turns a ``PheromoneBoardSnapshot`` into a
 * render-ready structure for the Observability surface heatmap panel.
 *
 * Layering: the snapshot already provides aggregated counters and the
 * decayed heatmap (``PheromoneBoardSnapshot.heatmap``). This view sorts
 * cells hottest-first, buckets them for color-coding, derives an
 * attention list for high-severity pheromone concentrations, and exposes
 * a stable per-type summary so the cockpit panel can render even when a
 * type has zero activity.
 *
 * Contract: no DOM, no fs, no clock; deterministic given the same input.
 */

import {
  PHEROMONE_TYPES,
  type PheromoneBoardSnapshot,
  type PheromoneType
} from '../contracts/pheromoneBoard';

export type PheromonePanelBucket = 'low' | 'medium' | 'high' | 'critical';
export type PheromonePanelAttentionSeverity = 'critical' | 'warning' | 'info';

export interface PheromonePanelCell {
  id: string;
  location: string;
  type: PheromoneType;
  intensity: number;
  count: number;
  lastTimestamp: string;
  bucket: PheromonePanelBucket;
}

export interface PheromonePanelTypeSummary {
  type: PheromoneType;
  count: number;
  intensity: number;
  ratio: number;
}

export interface PheromonePanelAttention {
  id: string;
  severity: PheromonePanelAttentionSeverity;
  location: string;
  type: PheromoneType;
  intensity: number;
  count: number;
  detail: string;
}

export interface PheromonePanelTotals {
  total: number;
  active: number;
  resolved: number;
}

export interface PheromonePanelView {
  schemaVersion: string;
  generatedAt: string;
  totals: PheromonePanelTotals;
  typeSummaries: readonly PheromonePanelTypeSummary[];
  cells: readonly PheromonePanelCell[];
  attention: readonly PheromonePanelAttention[];
  empty: boolean;
}

export interface PheromonePanelThresholds {
  medium?: number;
  high?: number;
  critical?: number;
}

export interface PheromonePanelOptions {
  maxCells?: number;
  maxAttention?: number;
  thresholds?: PheromonePanelThresholds;
}

const DEFAULT_MAX_CELLS = 32;
const DEFAULT_MAX_ATTENTION = 5;
const DEFAULT_THRESHOLD_MEDIUM = 0.5;
const DEFAULT_THRESHOLD_HIGH = 1.5;
const DEFAULT_THRESHOLD_CRITICAL = 3.0;

const HARD_ALERT_TYPES: ReadonlySet<PheromoneType> = new Set<PheromoneType>([
  'ALERT',
  'BLOCK'
]);
const SOFT_SIGNAL_TYPES: ReadonlySet<PheromoneType> = new Set<PheromoneType>([
  'NEED',
  'OPPORTUNITY'
]);

function classifyBucket(
  intensity: number,
  thresholds: Required<PheromonePanelThresholds>
): PheromonePanelBucket {
  if (intensity >= thresholds.critical) return 'critical';
  if (intensity >= thresholds.high) return 'high';
  if (intensity >= thresholds.medium) return 'medium';
  return 'low';
}

function severityFor(
  type: PheromoneType,
  intensity: number,
  thresholds: Required<PheromonePanelThresholds>
): PheromonePanelAttentionSeverity | null {
  if (HARD_ALERT_TYPES.has(type)) {
    if (intensity >= thresholds.critical) return 'critical';
    if (intensity >= thresholds.high) return 'warning';
    if (intensity >= thresholds.medium) return 'info';
    return null;
  }
  if (SOFT_SIGNAL_TYPES.has(type)) {
    if (intensity >= thresholds.critical) return 'warning';
    if (intensity >= thresholds.high) return 'info';
    return null;
  }
  return null;
}

const SEVERITY_RANK: Record<PheromonePanelAttentionSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2
};

export function createPheromonePanelView(
  snapshot: PheromoneBoardSnapshot,
  options: PheromonePanelOptions = {}
): PheromonePanelView {
  const maxCells = options.maxCells ?? DEFAULT_MAX_CELLS;
  const maxAttention = options.maxAttention ?? DEFAULT_MAX_ATTENTION;
  const thresholds: Required<PheromonePanelThresholds> = {
    medium: options.thresholds?.medium ?? DEFAULT_THRESHOLD_MEDIUM,
    high: options.thresholds?.high ?? DEFAULT_THRESHOLD_HIGH,
    critical: options.thresholds?.critical ?? DEFAULT_THRESHOLD_CRITICAL
  };

  const counters = snapshot.counters;
  const totals: PheromonePanelTotals = {
    total: counters.total,
    active: counters.active,
    resolved: counters.resolved
  };

  const sortedCells = snapshot.heatmap.slice().sort((a, b) => {
    if (b.intensity !== a.intensity) return b.intensity - a.intensity;
    if (b.count !== a.count) return b.count - a.count;
    if (a.lastTimestamp < b.lastTimestamp) return 1;
    if (a.lastTimestamp > b.lastTimestamp) return -1;
    return 0;
  });

  const cappedCells = maxCells >= 0 ? sortedCells.slice(0, maxCells) : sortedCells;
  const cells: PheromonePanelCell[] = cappedCells.map((cell) => ({
    id: `${cell.location}|${cell.type}`,
    location: cell.location,
    type: cell.type,
    intensity: cell.intensity,
    count: cell.count,
    lastTimestamp: cell.lastTimestamp,
    bucket: classifyBucket(cell.intensity, thresholds)
  }));

  const typeIntensity = {} as Record<PheromoneType, number>;
  for (const t of PHEROMONE_TYPES) typeIntensity[t] = 0;
  for (const cell of snapshot.heatmap) {
    typeIntensity[cell.type] += cell.intensity;
  }

  const total = counters.total;
  const typeSummaries: PheromonePanelTypeSummary[] = PHEROMONE_TYPES.map((type) => {
    const count = counters.byType[type] ?? 0;
    return {
      type,
      count,
      intensity: typeIntensity[type],
      ratio: total > 0 ? count / total : 0
    };
  });

  const attentionCandidates: PheromonePanelAttention[] = [];
  for (const cell of sortedCells) {
    const severity = severityFor(cell.type, cell.intensity, thresholds);
    if (severity === null) continue;
    attentionCandidates.push({
      id: `${cell.location}|${cell.type}`,
      severity,
      location: cell.location,
      type: cell.type,
      intensity: cell.intensity,
      count: cell.count,
      detail: `${cell.type} @ ${cell.location} (intensity=${cell.intensity.toFixed(2)}, n=${cell.count})`
    });
  }
  attentionCandidates.sort((a, b) => {
    const rankDiff = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    if (rankDiff !== 0) return rankDiff;
    return b.intensity - a.intensity;
  });
  const attention =
    maxAttention >= 0 ? attentionCandidates.slice(0, maxAttention) : attentionCandidates;

  return {
    schemaVersion: snapshot.schemaVersion,
    generatedAt: snapshot.generatedAt,
    totals,
    typeSummaries,
    cells,
    attention,
    empty: total === 0
  };
}
