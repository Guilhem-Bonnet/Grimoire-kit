/**
 * stigmergy-signals.ts — V4.4.a BM-19 stigmergy consumer.
 *
 * Pure projection that turns a PheromoneBoardSnapshot (V4.4.b BM-20)
 * into per-task stigmergy signals consumable by the Mission Board.
 *
 * Two layers, both pure (no DOM, no fs, no clock unless injected):
 *   1. ``selectStigmergySignals``: aggregates pheromones by task using a
 *      pluggable extractor that reads task ids from tags (default looks
 *      for ``task:<id>`` / ``taskId:<id>`` / ``task=<id>`` patterns).
 *   2. ``annotateCardsWithStigmergy``: enriches a list of cards with the
 *      derived signal so a column renderer can show a badge or boost.
 *
 * The intensity boost is the sum of the per-pheromone intensity, optionally
 * decayed via the underlying ``buildPheromoneHeatmap`` weights when the
 * caller uses the snapshot's heatmap. We expose both a "raw" sum
 * (useful for tests) and a normalised boost in [0, 1] capped at 1.
 */

import type {
  Pheromone,
  PheromoneBoardSnapshot,
  PheromoneType
} from '../contracts/pheromoneBoard';

export interface StigmergySignal {
  taskId: string;
  /** Sum of intensity across associated pheromones (raw, no decay). */
  intensitySum: number;
  /** Normalised priority boost in [0, 1] (currently min(intensitySum, 1)). */
  boost: number;
  /** Pheromone count associated to this task. */
  count: number;
  /** Most-recent timestamp (ISO-8601) across associated pheromones. */
  lastTimestamp: string;
  /** Distinct pheromone types present, sorted alphabetically. */
  types: readonly PheromoneType[];
  /** Distinct emitters, sorted alphabetically. */
  emitters: readonly string[];
  /** True if any associated pheromone is unresolved. */
  active: boolean;
}

export interface SelectStigmergyOptions {
  /**
   * Extract a taskId from a pheromone. Return null/undefined to skip.
   * Defaults to ``defaultExtractTaskId`` which scans tags for the
   * ``task:<id>`` / ``taskId:<id>`` / ``task=<id>`` patterns.
   */
  taskIdOf?: (pheromone: Pheromone) => string | null | undefined;
  /** Skip resolved pheromones. Defaults to true. */
  activeOnly?: boolean;
}

const TASK_TAG_PATTERNS: readonly RegExp[] = [
  /^task[:=]([A-Za-z0-9_.-]+)$/i,
  /^taskid[:=]([A-Za-z0-9_.-]+)$/i,
  /^t[:=]([A-Za-z0-9_.-]+)$/i
];

/**
 * Default extractor: scans the pheromone tags for known task ID patterns.
 * Returns the first match, lower-case prefix preserved as-is.
 */
export function defaultExtractTaskId(pheromone: Pheromone): string | null {
  for (const tag of pheromone.tags) {
    for (const re of TASK_TAG_PATTERNS) {
      const match = tag.match(re);
      if (match && match[1]) {
        return match[1];
      }
    }
  }
  return null;
}

/**
 * Aggregate pheromones from a snapshot into per-task signals.
 * Result is a Map keyed by taskId for O(1) lookup from the Mission Board.
 */
export function selectStigmergySignals(
  snapshot: PheromoneBoardSnapshot,
  options: SelectStigmergyOptions = {}
): Map<string, StigmergySignal> {
  const extractor = options.taskIdOf ?? defaultExtractTaskId;
  const activeOnly = options.activeOnly ?? true;
  const buckets = new Map<string, MutableBucket>();
  for (const pheromone of snapshot.board.pheromones) {
    if (activeOnly && pheromone.resolved) {
      continue;
    }
    const taskId = extractor(pheromone);
    if (!taskId) {
      continue;
    }
    let bucket = buckets.get(taskId);
    if (!bucket) {
      bucket = {
        taskId,
        intensitySum: 0,
        count: 0,
        lastTimestamp: pheromone.timestamp,
        types: new Set<PheromoneType>(),
        emitters: new Set<string>(),
        active: false
      };
      buckets.set(taskId, bucket);
    }
    bucket.intensitySum += pheromone.intensity;
    bucket.count += 1;
    bucket.types.add(pheromone.pheromone_type);
    bucket.emitters.add(pheromone.emitter);
    if (pheromone.timestamp > bucket.lastTimestamp) {
      bucket.lastTimestamp = pheromone.timestamp;
    }
    if (!pheromone.resolved) {
      bucket.active = true;
    }
  }
  const out = new Map<string, StigmergySignal>();
  for (const [taskId, bucket] of buckets) {
    out.set(taskId, {
      taskId,
      intensitySum: bucket.intensitySum,
      boost: Math.min(1, bucket.intensitySum),
      count: bucket.count,
      lastTimestamp: bucket.lastTimestamp,
      types: Array.from(bucket.types).sort(),
      emitters: Array.from(bucket.emitters).sort(),
      active: bucket.active
    });
  }
  return out;
}

interface MutableBucket {
  taskId: string;
  intensitySum: number;
  count: number;
  lastTimestamp: string;
  types: Set<PheromoneType>;
  emitters: Set<string>;
  active: boolean;
}

/**
 * Card-shape-agnostic enrichment helper. Returns a new array where each
 * card gains a ``stigmergy`` field (signal or null). Cards without a
 * matching signal pass through unchanged structurally (signal === null).
 */
export interface StigmergyAnnotation<TCard> {
  card: TCard;
  stigmergy: StigmergySignal | null;
}

export function annotateCardsWithStigmergy<TCard extends { taskId: string }>(
  cards: readonly TCard[],
  signals: ReadonlyMap<string, StigmergySignal>
): StigmergyAnnotation<TCard>[] {
  return cards.map((card) => ({
    card,
    stigmergy: signals.get(card.taskId) ?? null
  }));
}
