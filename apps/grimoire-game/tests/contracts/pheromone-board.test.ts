import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  PHEROMONE_BOARD_SCHEMA_VERSION,
  PHEROMONE_TYPES,
  buildPheromoneHeatmap,
  computePheromoneCounters,
  createPheromoneBoardSnapshot,
  emptyPheromoneBoard,
  filterPheromones,
  parsePheromoneBoard,
  type Pheromone,
  type PheromoneBoard
} from '../../src/contracts/pheromoneBoard';
import {
  buildPheromoneBoardSnapshot,
  readPheromoneBoard
} from '../../src/server/pheromone-board-feed';

function makePheromone(overrides: Partial<Pheromone> = {}): Pheromone {
  return {
    pheromone_id: 'PH-' + Math.random().toString(36).slice(2, 10),
    pheromone_type: 'ALERT',
    location: 'preflight/structure',
    text: 'Dossier config manquant',
    emitter: 'preflight-check',
    timestamp: '2026-04-21T10:00:00.000Z',
    intensity: 1,
    tags: ['blocker'],
    reinforcements: 0,
    reinforced_by: [],
    resolved: false,
    resolved_by: null,
    resolved_at: null,
    ...overrides
  } as Pheromone;
}

function makeBoard(pheromones: Pheromone[] = []): PheromoneBoard {
  return {
    version: PHEROMONE_BOARD_SCHEMA_VERSION,
    half_life_hours: 72,
    pheromones
  };
}

describe('pheromoneBoard contract (mirror of Python grimoire.tools.stigmergy)', () => {
  it('exposes the canonical SCHEMA_VERSION', () => {
    expect(PHEROMONE_BOARD_SCHEMA_VERSION).toBe('1.0.0');
  });

  it('exposes all 6 canonical pheromone types', () => {
    expect(new Set(PHEROMONE_TYPES)).toEqual(
      new Set(['NEED', 'ALERT', 'OPPORTUNITY', 'PROGRESS', 'COMPLETE', 'BLOCK'])
    );
  });

  it('parses a valid board JSON written by the Python writer', () => {
    const raw = JSON.stringify({
      version: '1.0.0',
      half_life_hours: 72,
      pheromones: [
        {
          pheromone_id: 'PH-abc',
          pheromone_type: 'ALERT',
          location: 'preflight/structure',
          text: 'foo',
          emitter: 'preflight-check',
          timestamp: '2026-04-21T10:00:00.000Z',
          intensity: 0.7,
          tags: ['blocker'],
          reinforcements: 2,
          reinforced_by: ['preflight-check'],
          resolved: false,
          resolved_by: null,
          resolved_at: null
        }
      ]
    });
    const board = parsePheromoneBoard(raw);
    expect(board.pheromones).toHaveLength(1);
    expect(board.pheromones[0]?.pheromone_type).toBe('ALERT');
    expect(board.pheromones[0]?.intensity).toBeCloseTo(0.7);
  });

  it('returns an empty board when input is blank or invalid JSON', () => {
    expect(parsePheromoneBoard('').pheromones).toHaveLength(0);
    expect(parsePheromoneBoard('not-json').pheromones).toHaveLength(0);
    expect(parsePheromoneBoard('{"unrelated":true}').pheromones).toHaveLength(0);
  });

  it('rejects a pheromone with an unknown type by returning an empty board', () => {
    const raw = JSON.stringify({
      version: '1.0.0',
      half_life_hours: 72,
      pheromones: [{ ...makePheromone(), pheromone_type: 'UNKNOWN' }]
    });
    expect(parsePheromoneBoard(raw).pheromones).toHaveLength(0);
  });

  it('builds an empty board fixture with the correct shape', () => {
    const board = emptyPheromoneBoard();
    expect(board.version).toBe('1.0.0');
    expect(board.half_life_hours).toBe(72);
    expect(board.pheromones).toEqual([]);
  });
});

describe('filterPheromones', () => {
  const board = makeBoard([
    makePheromone({
      pheromone_id: 'a',
      timestamp: '2026-04-21T08:00:00.000Z',
      resolved: true
    }),
    makePheromone({
      pheromone_id: 'b',
      timestamp: '2026-04-21T10:00:00.000Z',
      pheromone_type: 'BLOCK'
    }),
    makePheromone({
      pheromone_id: 'c',
      timestamp: '2026-04-21T12:00:00.000Z',
      location: 'tests/contracts'
    })
  ]);

  it('sorts by timestamp descending by default', () => {
    const out = filterPheromones(board);
    expect(out.map((p) => p.pheromone_id)).toEqual(['c', 'b', 'a']);
  });

  it('drops resolved entries when activeOnly is set', () => {
    const out = filterPheromones(board, { activeOnly: true });
    expect(out.map((p) => p.pheromone_id)).toEqual(['c', 'b']);
  });

  it('filters by pheromone type', () => {
    const out = filterPheromones(board, { type: 'BLOCK' });
    expect(out.map((p) => p.pheromone_id)).toEqual(['b']);
  });

  it('filters by location prefix', () => {
    const out = filterPheromones(board, { locationPrefix: 'tests/' });
    expect(out.map((p) => p.pheromone_id)).toEqual(['c']);
  });

  it('caps with limit after sorting', () => {
    const out = filterPheromones(board, { limit: 2 });
    expect(out.map((p) => p.pheromone_id)).toEqual(['c', 'b']);
  });
});

describe('computePheromoneCounters', () => {
  it('counts active vs resolved and breaks down by type and emitter', () => {
    const board = makeBoard([
      makePheromone({ pheromone_type: 'ALERT', emitter: 'e1', resolved: false }),
      makePheromone({ pheromone_type: 'ALERT', emitter: 'e1', resolved: true }),
      makePheromone({ pheromone_type: 'BLOCK', emitter: 'e2', resolved: false })
    ]);
    const c = computePheromoneCounters(board);
    expect(c.total).toBe(3);
    expect(c.active).toBe(2);
    expect(c.resolved).toBe(1);
    expect(c.byType.ALERT).toBe(2);
    expect(c.byType.BLOCK).toBe(1);
    expect(c.byType.NEED).toBe(0);
    expect(c.byEmitter).toEqual({ e1: 2, e2: 1 });
  });
});

describe('buildPheromoneHeatmap', () => {
  it('aggregates intensity per (location, type) cell', () => {
    const board = makeBoard([
      makePheromone({ location: 'a', pheromone_type: 'ALERT', intensity: 0.5 }),
      makePheromone({ location: 'a', pheromone_type: 'ALERT', intensity: 0.3 }),
      makePheromone({ location: 'a', pheromone_type: 'BLOCK', intensity: 1 }),
      makePheromone({ location: 'b', pheromone_type: 'ALERT', intensity: 0.2 })
    ]);
    const cells = buildPheromoneHeatmap(board);
    expect(cells).toHaveLength(3);
    const aAlert = cells.find((c) => c.location === 'a' && c.type === 'ALERT');
    expect(aAlert?.intensity).toBeCloseTo(0.8);
    expect(aAlert?.count).toBe(2);
  });

  it('skips resolved pheromones by default', () => {
    const board = makeBoard([
      makePheromone({ resolved: true }),
      makePheromone({ pheromone_id: 'x' })
    ]);
    const cells = buildPheromoneHeatmap(board);
    expect(cells).toHaveLength(1);
  });

  it('applies exponential decay against now using half_life_hours', () => {
    const board = makeBoard([
      makePheromone({
        timestamp: '2026-04-21T00:00:00.000Z',
        intensity: 1
      })
    ]);
    const cells = buildPheromoneHeatmap(board, {
      now: '2026-04-24T00:00:00.000Z',
      halfLifeHours: 72
    });
    expect(cells).toHaveLength(1);
    expect(cells[0]?.intensity).toBeCloseTo(0.5, 5);
  });

  it('returns cells sorted by intensity desc', () => {
    const board = makeBoard([
      makePheromone({ location: 'low', intensity: 0.1 }),
      makePheromone({ location: 'high', intensity: 0.9 })
    ]);
    const cells = buildPheromoneHeatmap(board);
    expect(cells.map((c) => c.location)).toEqual(['high', 'low']);
  });
});

describe('createPheromoneBoardSnapshot', () => {
  it('produces a deterministic snapshot with the given generatedAt', () => {
    const board = makeBoard([makePheromone()]);
    const snapshot = createPheromoneBoardSnapshot(board, {
      generatedAt: '2026-04-22T00:00:00.000Z'
    });
    expect(snapshot.schemaVersion).toBe('1.0.0');
    expect(snapshot.generatedAt).toBe('2026-04-22T00:00:00.000Z');
    expect(snapshot.board.pheromones).toHaveLength(1);
    expect(snapshot.counters.active).toBe(1);
    expect(snapshot.heatmap).toHaveLength(1);
  });

  it('caps the raw pheromones with the limit option', () => {
    const board = makeBoard(
      Array.from({ length: 5 }, (_, i) =>
        makePheromone({
          pheromone_id: `p${i}`,
          timestamp: `2026-04-21T0${i}:00:00.000Z`
        })
      )
    );
    const snapshot = createPheromoneBoardSnapshot(board, { limit: 2 });
    expect(snapshot.board.pheromones).toHaveLength(2);
    expect(snapshot.counters.total).toBe(5);
  });
});

describe('pheromone-board-feed (server reader)', () => {
  let workdir: string;

  beforeEach(() => {
    workdir = mkdtempSync(join(tmpdir(), 'grimoire-pheromone-feed-'));
  });

  afterEach(() => {
    rmSync(workdir, { recursive: true, force: true });
  });

  it('returns an empty board when the file does not exist', () => {
    const board = readPheromoneBoard({ projectRoot: workdir });
    expect(board.pheromones).toHaveLength(0);
  });

  it('reads + parses the canonical board file at the default path', () => {
    const dir = join(workdir, '_grimoire-output');
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, 'pheromone-board.json'),
      JSON.stringify({
        version: '1.0.0',
        half_life_hours: 72,
        pheromones: [
          {
            pheromone_id: 'PH-1',
            pheromone_type: 'NEED',
            location: 'docs',
            text: 'help wanted',
            emitter: 'analyst',
            timestamp: '2026-04-21T10:00:00.000Z',
            intensity: 0.4,
            tags: [],
            reinforcements: 0,
            reinforced_by: [],
            resolved: false,
            resolved_by: null,
            resolved_at: null
          }
        ]
      }),
      'utf8'
    );
    const board = readPheromoneBoard({ projectRoot: workdir });
    expect(board.pheromones).toHaveLength(1);
    expect(board.pheromones[0]?.pheromone_type).toBe('NEED');
  });

  it('builds a snapshot end-to-end from the project root', () => {
    const dir = join(workdir, '_grimoire-output');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'pheromone-board.json'), JSON.stringify(makeBoard()));
    const snapshot = buildPheromoneBoardSnapshot({
      projectRoot: workdir,
      generatedAt: '2026-04-22T00:00:00.000Z'
    });
    expect(snapshot.generatedAt).toBe('2026-04-22T00:00:00.000Z');
    expect(snapshot.board.pheromones).toHaveLength(0);
    expect(snapshot.counters.total).toBe(0);
  });
});
