import { describe, expect, it } from 'vitest';

import type { OfficeCharacter, OfficeGrid } from '../../src/state/office-view';
import {
  OFFICE_PLACEMENT_SCHEMA_VERSION,
  isCellFree,
  resolveOfficePlacement
} from '../../src/state/office-placement';

function makeChar(
  overrides: Partial<OfficeCharacter> & { agentId: string }
): OfficeCharacter {
  return {
    agentId: overrides.agentId,
    role: overrides.role ?? overrides.agentId,
    state: overrides.state ?? 'idle',
    seat: overrides.seat ?? { col: 0, row: 0 },
    parent: overrides.parent ?? null,
    lastEventTs: overrides.lastEventTs ?? '2026-04-22T12:00:00.000Z',
    lastEventId: overrides.lastEventId ?? 'evt',
    lastEventKind: overrides.lastEventKind ?? 'tool/start',
    isMaster: overrides.isMaster ?? false
  };
}

const GRID: OfficeGrid = { cols: 4, rows: 3 };

describe('resolveOfficePlacement', () => {
  it('returns an empty placement for no characters', () => {
    const placement = resolveOfficePlacement([], GRID);
    expect(placement.seats.size).toBe(0);
    expect(placement.overflow).toEqual([]);
    expect(placement.schemaVersion).toBe(OFFICE_PLACEMENT_SCHEMA_VERSION);
  });

  it('keeps the preferred seat when free', () => {
    const p = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 2, row: 1 } })],
      GRID
    );
    expect(p.seats.get('dev')).toEqual({ col: 2, row: 1 });
  });

  it('never places two agents in the same cell', () => {
    const chars = [
      makeChar({ agentId: 'a', seat: { col: 1, row: 1 } }),
      makeChar({ agentId: 'b', seat: { col: 1, row: 1 } }),
      makeChar({ agentId: 'c', seat: { col: 1, row: 1 } })
    ];
    const p = resolveOfficePlacement(chars, GRID);
    const cells = Array.from(p.seats.values()).map((s) => `${s.col},${s.row}`);
    expect(new Set(cells).size).toBe(cells.length);
    expect(p.overflow).toEqual([]);
  });

  it('places collisions on nearest free cell via spiral search', () => {
    const chars = [
      makeChar({ agentId: 'a', seat: { col: 1, row: 1 } }),
      makeChar({ agentId: 'b', seat: { col: 1, row: 1 } })
    ];
    const p = resolveOfficePlacement(chars, GRID);
    const a = p.seats.get('a')!;
    const b = p.seats.get('b')!;
    const cheb = Math.max(Math.abs(b.col - a.col), Math.abs(b.row - a.row));
    expect(cheb).toBe(1);
  });

  it('is deterministic regardless of input order', () => {
    const input1 = [
      makeChar({ agentId: 'a', seat: { col: 0, row: 0 } }),
      makeChar({ agentId: 'b', seat: { col: 0, row: 0 } }),
      makeChar({ agentId: 'c', seat: { col: 0, row: 0 } })
    ];
    const input2 = [input1[2]!, input1[0]!, input1[1]!];
    const p1 = resolveOfficePlacement(input1, GRID);
    const p2 = resolveOfficePlacement(input2, GRID);
    for (const id of ['a', 'b', 'c']) {
      expect(p2.seats.get(id)).toEqual(p1.seats.get(id));
    }
  });

  it('reports overflow when the grid is full', () => {
    const tiny: OfficeGrid = { cols: 2, rows: 2 };
    const chars = Array.from({ length: 5 }, (_, i) =>
      makeChar({ agentId: `a${i}`, seat: { col: 0, row: 0 } })
    );
    const p = resolveOfficePlacement(chars, tiny);
    expect(p.seats.size).toBe(4);
    expect(p.overflow).toHaveLength(1);
  });

  it('clamps out-of-bounds preferred seats into the grid', () => {
    const p = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 99, row: -5 } })],
      GRID
    );
    const seat = p.seats.get('dev')!;
    expect(seat.col).toBeGreaterThanOrEqual(0);
    expect(seat.col).toBeLessThan(GRID.cols);
    expect(seat.row).toBeGreaterThanOrEqual(0);
    expect(seat.row).toBeLessThan(GRID.rows);
  });

  it('anchors agents on their previous cell when still free', () => {
    const first = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 2, row: 1 } })],
      GRID
    );
    // New frame: dev's preferred seat is now (0,0) but we want it to STAY
    // on (2,1) because previous said so.
    const second = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 0, row: 0 } })],
      GRID,
      { previous: first }
    );
    expect(second.seats.get('dev')).toEqual({ col: 2, row: 1 });
  });

  it('anchored agents block newcomers who wanted the same cell', () => {
    const first = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 1, row: 1 } })],
      GRID
    );
    const second = resolveOfficePlacement(
      [
        makeChar({ agentId: 'dev', seat: { col: 1, row: 1 } }),
        makeChar({ agentId: 'qa', seat: { col: 1, row: 1 } })
      ],
      GRID,
      { previous: first }
    );
    expect(second.seats.get('dev')).toEqual({ col: 1, row: 1 });
    const qa = second.seats.get('qa')!;
    expect(qa).not.toEqual({ col: 1, row: 1 });
  });

  it('releases the cell when an agent disappears', () => {
    const first = resolveOfficePlacement(
      [
        makeChar({ agentId: 'dev', seat: { col: 0, row: 0 } }),
        makeChar({ agentId: 'qa', seat: { col: 1, row: 0 } })
      ],
      GRID
    );
    // dev is gone; newcomer 'pm' prefers (0,0).
    const second = resolveOfficePlacement(
      [
        makeChar({ agentId: 'qa', seat: { col: 1, row: 0 } }),
        makeChar({ agentId: 'pm', seat: { col: 0, row: 0 } })
      ],
      GRID,
      { previous: first }
    );
    expect(second.seats.get('pm')).toEqual({ col: 0, row: 0 });
    expect(second.seats.get('qa')).toEqual({ col: 1, row: 0 });
  });
});

describe('isCellFree', () => {
  it('returns true for a free in-bounds cell', () => {
    const p = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 0, row: 0 } })],
      GRID
    );
    expect(isCellFree(p, { col: 1, row: 1 })).toBe(true);
  });

  it('returns false for an occupied cell', () => {
    const p = resolveOfficePlacement(
      [makeChar({ agentId: 'dev', seat: { col: 0, row: 0 } })],
      GRID
    );
    expect(isCellFree(p, { col: 0, row: 0 })).toBe(false);
  });

  it('returns false for an out-of-bounds cell', () => {
    const p = resolveOfficePlacement([], GRID);
    expect(isCellFree(p, { col: -1, row: 0 })).toBe(false);
    expect(isCellFree(p, { col: 99, row: 0 })).toBe(false);
  });
});
