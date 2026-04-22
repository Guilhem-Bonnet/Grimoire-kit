import { describe, expect, it } from 'vitest';

import {
  PHEROMONE_BOARD_SCHEMA_VERSION,
  PHEROMONE_TYPES,
  createPheromoneBoardSnapshot,
  type Pheromone,
  type PheromoneBoard,
  type PheromoneBoardSnapshot
} from '../../src/contracts/pheromoneBoard';
import { createPheromonePanelView } from '../../src/state/pheromone-panel-view';

function makePheromone(overrides: Partial<Pheromone> = {}): Pheromone {
  return {
    pheromone_id: 'PH-' + Math.random().toString(36).slice(2, 10),
    pheromone_type: 'ALERT',
    location: 'preflight',
    text: 'sample',
    emitter: 'preflight-check',
    timestamp: '2026-04-22T10:00:00.000Z',
    intensity: 0.5,
    tags: [],
    reinforcements: 0,
    reinforced_by: [],
    resolved: false,
    resolved_by: null,
    resolved_at: null,
    ...overrides
  } as Pheromone;
}

function makeSnapshot(pheromones: Pheromone[]): PheromoneBoardSnapshot {
  const board: PheromoneBoard = {
    version: PHEROMONE_BOARD_SCHEMA_VERSION,
    half_life_hours: 72,
    pheromones
  };
  return createPheromoneBoardSnapshot(board, {
    generatedAt: '2026-04-22T12:00:00.000Z',
    now: '2026-04-22T12:00:00.000Z'
  });
}

describe('createPheromonePanelView', () => {
  it('produces an empty view for an empty board', () => {
    const snapshot = makeSnapshot([]);
    const view = createPheromonePanelView(snapshot);

    expect(view.empty).toBe(true);
    expect(view.totals).toEqual({ total: 0, active: 0, resolved: 0 });
    expect(view.cells).toEqual([]);
    expect(view.attention).toEqual([]);
    expect(view.schemaVersion).toBe(PHEROMONE_BOARD_SCHEMA_VERSION);
    expect(view.generatedAt).toBe('2026-04-22T12:00:00.000Z');
  });

  it('exposes a stable summary slot for every pheromone type', () => {
    const snapshot = makeSnapshot([]);
    const view = createPheromonePanelView(snapshot);
    const types = view.typeSummaries.map((s) => s.type);
    expect(types).toEqual([...PHEROMONE_TYPES]);
    for (const s of view.typeSummaries) {
      expect(s.count).toBe(0);
      expect(s.intensity).toBe(0);
      expect(s.ratio).toBe(0);
    }
  });

  it('propagates totals from counters', () => {
    const snapshot = makeSnapshot([
      makePheromone({ pheromone_type: 'ALERT', resolved: false }),
      makePheromone({ pheromone_type: 'NEED', resolved: false }),
      makePheromone({ pheromone_type: 'BLOCK', resolved: true, resolved_by: 'sog' })
    ]);
    const view = createPheromonePanelView(snapshot);
    expect(view.totals.total).toBe(3);
    expect(view.totals.active).toBe(2);
    expect(view.totals.resolved).toBe(1);
    expect(view.empty).toBe(false);
  });

  it('sorts cells by intensity desc, then count desc, then timestamp desc', () => {
    const snapshot = makeSnapshot([
      makePheromone({
        location: 'low',
        pheromone_type: 'ALERT',
        intensity: 0.2,
        timestamp: '2026-04-22T10:00:00.000Z'
      }),
      makePheromone({
        location: 'hot',
        pheromone_type: 'ALERT',
        intensity: 0.9,
        timestamp: '2026-04-22T11:00:00.000Z'
      }),
      makePheromone({
        location: 'hot',
        pheromone_type: 'ALERT',
        intensity: 0.9,
        timestamp: '2026-04-22T11:30:00.000Z'
      }),
      makePheromone({
        location: 'mid',
        pheromone_type: 'NEED',
        intensity: 0.6,
        timestamp: '2026-04-22T10:30:00.000Z'
      })
    ]);
    const view = createPheromonePanelView(snapshot);
    const order = view.cells.map((c) => `${c.location}|${c.type}`);
    expect(order[0]).toBe('hot|ALERT');
    expect(order[1]).toBe('mid|NEED');
    expect(order[2]).toBe('low|ALERT');
  });

  it('classifies buckets using the default thresholds', () => {
    const snapshot = makeSnapshot([
      makePheromone({ location: 'tiny', pheromone_type: 'ALERT', intensity: 0.1 }),
      makePheromone({ location: 'soft', pheromone_type: 'ALERT', intensity: 0.7 }),
      makePheromone({ location: 'warm', pheromone_type: 'ALERT', intensity: 1.8 }),
      makePheromone({ location: 'hot', pheromone_type: 'ALERT', intensity: 3.5 })
    ]);
    const view = createPheromonePanelView(snapshot);
    const byId = Object.fromEntries(view.cells.map((c) => [c.location, c.bucket]));
    expect(byId['tiny']).toBe('low');
    expect(byId['soft']).toBe('medium');
    expect(byId['warm']).toBe('high');
    expect(byId['hot']).toBe('critical');
  });

  it('honours custom thresholds', () => {
    const snapshot = makeSnapshot([
      makePheromone({ location: 'x', pheromone_type: 'ALERT', intensity: 0.4 })
    ]);
    const view = createPheromonePanelView(snapshot, {
      thresholds: { medium: 0.1, high: 0.3, critical: 10 }
    });
    expect(view.cells[0]?.bucket).toBe('high');
  });

  it('caps cells with maxCells', () => {
    const pheromones = Array.from({ length: 10 }, (_, i) =>
      makePheromone({
        location: `loc-${i}`,
        pheromone_type: 'ALERT',
        intensity: 1 - i * 0.05
      })
    );
    const snapshot = makeSnapshot(pheromones);
    const view = createPheromonePanelView(snapshot, { maxCells: 3 });
    expect(view.cells).toHaveLength(3);
    expect(view.cells[0]?.location).toBe('loc-0');
  });

  it('derives critical attention for ALERT/BLOCK concentrations', () => {
    const snapshot = makeSnapshot([
      makePheromone({ location: 'prod', pheromone_type: 'BLOCK', intensity: 3.5 }),
      makePheromone({ location: 'stage', pheromone_type: 'ALERT', intensity: 2.0 })
    ]);
    const view = createPheromonePanelView(snapshot);
    expect(view.attention).toHaveLength(2);
    expect(view.attention[0]?.severity).toBe('critical');
    expect(view.attention[0]?.location).toBe('prod');
    expect(view.attention[1]?.severity).toBe('warning');
    expect(view.attention[1]?.location).toBe('stage');
  });

  it('downgrades soft signals (NEED/OPPORTUNITY) below critical', () => {
    const snapshot = makeSnapshot([
      makePheromone({ location: 'idea', pheromone_type: 'OPPORTUNITY', intensity: 3.5 }),
      makePheromone({ location: 'gap', pheromone_type: 'NEED', intensity: 1.8 })
    ]);
    const view = createPheromonePanelView(snapshot);
    const byLoc = Object.fromEntries(view.attention.map((a) => [a.location, a.severity]));
    expect(byLoc['idea']).toBe('warning');
    expect(byLoc['gap']).toBe('info');
  });

  it('skips non-alert types that never breach medium threshold', () => {
    const snapshot = makeSnapshot([
      makePheromone({ location: 'win', pheromone_type: 'COMPLETE', intensity: 5.0 }),
      makePheromone({ location: 'step', pheromone_type: 'PROGRESS', intensity: 5.0 })
    ]);
    const view = createPheromonePanelView(snapshot);
    expect(view.attention).toEqual([]);
  });

  it('caps attention with maxAttention', () => {
    const pheromones = Array.from({ length: 8 }, (_, i) =>
      makePheromone({
        location: `zone-${i}`,
        pheromone_type: 'ALERT',
        intensity: 3.5 + i * 0.1
      })
    );
    const snapshot = makeSnapshot(pheromones);
    const view = createPheromonePanelView(snapshot, { maxAttention: 3 });
    expect(view.attention).toHaveLength(3);
    expect(view.attention.every((a) => a.severity === 'critical')).toBe(true);
  });

  it('is deterministic for a given snapshot', () => {
    const pheromones = [
      makePheromone({ location: 'a', pheromone_type: 'ALERT', intensity: 0.9 }),
      makePheromone({ location: 'b', pheromone_type: 'NEED', intensity: 0.6 })
    ];
    const snapshot = makeSnapshot(pheromones);
    const v1 = createPheromonePanelView(snapshot);
    const v2 = createPheromonePanelView(snapshot);
    expect(v1).toEqual(v2);
  });

  it('aggregates intensity per type into the summary (ratio sums to total)', () => {
    const snapshot = makeSnapshot([
      makePheromone({ pheromone_type: 'ALERT', intensity: 0.5 }),
      makePheromone({ pheromone_type: 'ALERT', intensity: 0.3 }),
      makePheromone({ pheromone_type: 'NEED', intensity: 0.2 })
    ]);
    const view = createPheromonePanelView(snapshot);
    const alert = view.typeSummaries.find((s) => s.type === 'ALERT');
    const need = view.typeSummaries.find((s) => s.type === 'NEED');
    expect(alert?.count).toBe(2);
    expect(need?.count).toBe(1);
    const totalRatio = view.typeSummaries.reduce((acc, s) => acc + s.ratio, 0);
    expect(totalRatio).toBeCloseTo(1, 5);
  });
});
