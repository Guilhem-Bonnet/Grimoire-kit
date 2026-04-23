import { describe, it, expect } from 'vitest';
import { createGrimoireSpritePreset } from '../../src/rendering/grimoire-sprite-preset';
import { resolveAppearance, resolveFrame } from '../../src/rendering/sprite-registry';

describe('grimoire-sprite-preset', () => {
  it('wires all 9 curated character sheets', () => {
    const reg = createGrimoireSpritePreset({ baseUrl: '/assets/characters/' });
    expect(reg.sheets.size).toBe(9);
    expect(reg.sheets.get('seed-01')?.url).toContain('character_seed_01_v01.png');
    expect(reg.sheets.get('archivist')?.url).toContain('character_archivist_actions_v01.png');
  });

  it('covers the core BMM roles', () => {
    const reg = createGrimoireSpritePreset({ baseUrl: '/' });
    for (const role of ['dev', 'qa', 'architect', 'pm', 'analyst', 'sm', 'grimoire-master']) {
      expect(reg.roles.has(role)).toBe(true);
    }
  });

  it('falls back for unknown roles', () => {
    const reg = createGrimoireSpritePreset({ baseUrl: '/' });
    const appearance = resolveAppearance(reg, 'nonexistent-role');
    expect(appearance).toBeDefined();
    expect(resolveFrame(appearance, 'idle')).toEqual(
      expect.objectContaining({ sheetId: 'seed-01' })
    );
  });

  it('every role references a known sheet + each sprite has a cosmetic', () => {
    const reg = createGrimoireSpritePreset({ baseUrl: '/' });
    for (const [role, appearance] of reg.roles) {
      if (role === '__default__') continue;
      expect(reg.sheets.has(appearance.base.sheetId)).toBe(true);
      expect(appearance.cosmetics?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it('sheet dimensions match the curated asset contract (112×96)', () => {
    const reg = createGrimoireSpritePreset({ baseUrl: '/' });
    for (const sheet of reg.sheets.values()) {
      expect(sheet.frameWidth * sheet.cols).toBe(112);
      expect(sheet.frameHeight * sheet.rows).toBe(96);
    }
  });
});
