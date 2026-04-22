import { describe, expect, it } from 'vitest';

import {
  PHEROMONE_BOARD_SCHEMA_VERSION,
  createPheromoneBoardSnapshot,
  type Pheromone,
  type PheromoneBoard,
  type PheromoneBoardSnapshot
} from '../../src/contracts/pheromoneBoard';
import {
  annotateCardsWithStigmergy,
  defaultExtractTaskId,
  selectStigmergySignals
} from '../../src/state/stigmergy-signals';

function makePheromone(overrides: Partial<Pheromone> = {}): Pheromone {
  return {
    pheromone_id: 'PH-' + Math.random().toString(36).slice(2, 10),
    pheromone_type: 'ALERT',
    location: 'preflight',
    text: 'sample',
    emitter: 'preflight-check',
    timestamp: '2026-04-21T10:00:00.000Z',
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
    generatedAt: '2026-04-22T00:00:00.000Z'
  });
}

describe('defaultExtractTaskId', () => {
  it('matches task:<id>', () => {
    expect(defaultExtractTaskId(makePheromone({ tags: ['task:T-123'] }))).toBe('T-123');
  });

  it('matches taskId:<id> case-insensitively', () => {
    expect(defaultExtractTaskId(makePheromone({ tags: ['TASKID:abc'] }))).toBe('abc');
  });

  it('matches t=<id> shorthand', () => {
    expect(defaultExtractTaskId(makePheromone({ tags: ['T=card-7'] }))).toBe('card-7');
  });

  it('returns null when no tag matches', () => {
    expect(defaultExtractTaskId(makePheromone({ tags: ['blocker', 'structure'] }))).toBeNull();
  });

  it('returns null on empty tags', () => {
    expect(defaultExtractTaskId(makePheromone({ tags: [] }))).toBeNull();
  });
});

describe('selectStigmergySignals', () => {
  it('aggregates intensity across pheromones for the same task', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], intensity: 0.3 }),
      makePheromone({ tags: ['task:T-1'], intensity: 0.4, pheromone_type: 'BLOCK' }),
      makePheromone({ tags: ['task:T-2'], intensity: 0.6 })
    ]);
    const signals = selectStigmergySignals(snapshot);
    expect(signals.size).toBe(2);
    const t1 = signals.get('T-1');
    expect(t1?.intensitySum).toBeCloseTo(0.7);
    expect(t1?.count).toBe(2);
    expect(t1?.types).toEqual(['ALERT', 'BLOCK']);
    expect(t1?.boost).toBeCloseTo(0.7);
  });

  it('caps boost at 1.0 even when raw intensity sum exceeds 1', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], intensity: 1.0 }),
      makePheromone({ tags: ['task:T-1'], intensity: 0.8 })
    ]);
    const signal = selectStigmergySignals(snapshot).get('T-1');
    expect(signal?.intensitySum).toBeCloseTo(1.8);
    expect(signal?.boost).toBe(1);
  });

  it('skips pheromones without a matching task tag', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['blocker'] }),
      makePheromone({ tags: ['task:T-9'] })
    ]);
    const signals = selectStigmergySignals(snapshot);
    expect(Array.from(signals.keys())).toEqual(['T-9']);
  });

  it('skips resolved pheromones by default', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], resolved: true }),
      makePheromone({ tags: ['task:T-1'], intensity: 0.2 })
    ]);
    const signal = selectStigmergySignals(snapshot).get('T-1');
    expect(signal?.count).toBe(1);
    expect(signal?.intensitySum).toBeCloseTo(0.2);
    expect(signal?.active).toBe(true);
  });

  it('includes resolved pheromones when activeOnly is false', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], resolved: true, intensity: 0.4 })
    ]);
    const signal = selectStigmergySignals(snapshot, { activeOnly: false }).get('T-1');
    expect(signal?.count).toBe(1);
    expect(signal?.active).toBe(false);
  });

  it('uses the most recent timestamp across pheromones', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], timestamp: '2026-04-20T00:00:00.000Z' }),
      makePheromone({ tags: ['task:T-1'], timestamp: '2026-04-22T00:00:00.000Z' }),
      makePheromone({ tags: ['task:T-1'], timestamp: '2026-04-21T00:00:00.000Z' })
    ]);
    const signal = selectStigmergySignals(snapshot).get('T-1');
    expect(signal?.lastTimestamp).toBe('2026-04-22T00:00:00.000Z');
  });

  it('honours a custom taskIdOf extractor', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['custom:X'], pheromone_id: 'PH-1' }),
      makePheromone({ tags: ['other:Y'], pheromone_id: 'PH-2' })
    ]);
    const signals = selectStigmergySignals(snapshot, {
      taskIdOf: (p) => {
        const tag = p.tags.find((t) => t.startsWith('custom:'));
        return tag ? tag.slice('custom:'.length) : null;
      }
    });
    expect(Array.from(signals.keys())).toEqual(['X']);
  });

  it('lists distinct emitters sorted alphabetically', () => {
    const snapshot = makeSnapshot([
      makePheromone({ tags: ['task:T-1'], emitter: 'zeta' }),
      makePheromone({ tags: ['task:T-1'], emitter: 'alpha' }),
      makePheromone({ tags: ['task:T-1'], emitter: 'alpha' })
    ]);
    const signal = selectStigmergySignals(snapshot).get('T-1');
    expect(signal?.emitters).toEqual(['alpha', 'zeta']);
  });
});

describe('annotateCardsWithStigmergy', () => {
  it('attaches the matching signal to each card and null otherwise', () => {
    const snapshot = makeSnapshot([makePheromone({ tags: ['task:T-1'] })]);
    const signals = selectStigmergySignals(snapshot);
    const cards = [
      { taskId: 'T-1', title: 'one' },
      { taskId: 'T-2', title: 'two' }
    ];
    const annotated = annotateCardsWithStigmergy(cards, signals);
    expect(annotated[0]?.stigmergy?.taskId).toBe('T-1');
    expect(annotated[1]?.stigmergy).toBeNull();
    expect(annotated[0]?.card).toBe(cards[0]);
  });

  it('preserves card identity (does not mutate or wrap)', () => {
    const snapshot = makeSnapshot([]);
    const cards = [{ taskId: 'T-1' }];
    const annotated = annotateCardsWithStigmergy(cards, selectStigmergySignals(snapshot));
    expect(annotated).toHaveLength(1);
    expect(annotated[0]?.card).toBe(cards[0]);
  });
});
