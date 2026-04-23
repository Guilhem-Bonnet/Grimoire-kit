import { describe, it, expect } from 'vitest';
import {
  createSpriteRegistry,
  DEFAULT_ROLE_KEY,
  resolveAppearance,
  resolveFrame,
  SpriteRegistryError,
  type SpriteRegistryInput
} from '../../src/rendering/sprite-registry';

function fixture(): SpriteRegistryInput {
  return {
    sheets: [
      { id: 'base', url: 'a.png', frameWidth: 16, frameHeight: 32, cols: 4, rows: 2 },
      { id: 'hats', url: 'h.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 }
    ],
    roles: {
      dev: {
        base: { sheetId: 'base', col: 0, row: 0 },
        states: {
          walk: { sheetId: 'base', col: 1, row: 0 },
          type: { sheetId: 'base', col: 2, row: 0 }
        },
        cosmetics: [{ sheetId: 'hats', col: 0, row: 0 }]
      }
    },
    defaultRole: {
      base: { sheetId: 'base', col: 3, row: 1 }
    }
  };
}

describe('sprite-registry', () => {
  it('builds an immutable registry from valid input', () => {
    const reg = createSpriteRegistry(fixture());
    expect(reg.schemaVersion).toBe('1.0.0');
    expect(reg.sheets.has('base')).toBe(true);
    expect(reg.roles.has('dev')).toBe(true);
    expect(reg.roles.has(DEFAULT_ROLE_KEY)).toBe(true);
  });

  it('rejects a role referencing an unknown sheet', () => {
    const input = fixture();
    (input.roles as Record<string, unknown>).bad = {
      base: { sheetId: 'nope', col: 0, row: 0 }
    };
    expect(() => createSpriteRegistry(input)).toThrow(SpriteRegistryError);
  });

  it('rejects out-of-bounds frames', () => {
    const input = fixture();
    (input.roles as Record<string, unknown>).bad = {
      base: { sheetId: 'base', col: 99, row: 0 }
    };
    expect(() => createSpriteRegistry(input)).toThrow(/col 99/);
  });

  it('rejects duplicate sheet ids', () => {
    const input = fixture();
    const dup = [...input.sheets, input.sheets[0]!];
    expect(() => createSpriteRegistry({ ...input, sheets: dup })).toThrow(/duplicate/);
  });

  it('forbids the reserved default role key in input.roles', () => {
    const input = fixture();
    (input.roles as Record<string, unknown>)[DEFAULT_ROLE_KEY] = input.defaultRole;
    expect(() => createSpriteRegistry(input)).toThrow(/reserved/);
  });

  it('resolveAppearance returns dedicated then default', () => {
    const reg = createSpriteRegistry(fixture());
    expect(resolveAppearance(reg, 'dev').base.col).toBe(0);
    expect(resolveAppearance(reg, 'unknown-role').base.col).toBe(3);
  });

  it('resolveFrame falls back to base when state has no mapping', () => {
    const reg = createSpriteRegistry(fixture());
    const dev = resolveAppearance(reg, 'dev');
    expect(resolveFrame(dev, 'walk').col).toBe(1);
    expect(resolveFrame(dev, 'wait').col).toBe(0);
  });
});
