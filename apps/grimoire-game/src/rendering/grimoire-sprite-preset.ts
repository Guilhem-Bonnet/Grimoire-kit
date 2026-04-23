/**
 * rendering/grimoire-sprite-preset.ts — V3.S3.7 part 2.
 *
 * Opinionated registry that wires the curated asset set from
 * `grimoire-game-assets/10-curated/characters/` to the Grimoire agent
 * roster (dev, qa, architect, pm, …). Each role uses one of the
 * `character_seed_*` sheets as its base and layers a cosmetic overlay
 * derived from the same sheet (dedicated accessory frames) so we keep
 * the cadrage rule: *same base characters, cosmetics create roles*.
 *
 * Asset base URL is injected at call time so apps can serve the same
 * sheets from `/assets/characters/` (grimoire-game) or a CDN.
 */

import {
  createSpriteRegistry,
  type RoleAppearance,
  type SpriteRegistry,
  type SpriteSheet
} from './sprite-registry';

export interface GrimoirePresetOptions {
  /** URL prefix where the sprite sheets are served. Must end with `/`. */
  readonly baseUrl: string;
}

/**
 * Each sheet is 112×96 = 7 cols × 3 rows of 16×32 character frames.
 * Row 0: idle; row 1: walk/action; row 2: alt.
 * Column 0 is the canonical base portrait for any role.
 */
const FRAME_WIDTH = 16;
const FRAME_HEIGHT = 32;
const SHEET_COLS = 7;
const SHEET_ROWS = 3;

function sheet(id: string, file: string, baseUrl: string): SpriteSheet {
  return {
    id,
    url: `${baseUrl}${file}`,
    frameWidth: FRAME_WIDTH,
    frameHeight: FRAME_HEIGHT,
    cols: SHEET_COLS,
    rows: SHEET_ROWS
  };
}

export function createGrimoireSpritePreset(options: GrimoirePresetOptions): SpriteRegistry {
  const { baseUrl } = options;

  const sheets: SpriteSheet[] = [
    sheet('seed-01', 'character_seed_01_v01.png', baseUrl),
    sheet('seed-02', 'character_seed_02_v01.png', baseUrl),
    sheet('seed-03', 'character_seed_03_v01.png', baseUrl),
    sheet('seed-04', 'character_seed_04_v01.png', baseUrl),
    sheet('seed-05', 'character_seed_05_v01.png', baseUrl),
    sheet('seed-06', 'character_seed_06_v01.png', baseUrl),
    sheet('archivist', 'character_archivist_actions_v01.png', baseUrl),
    sheet('archivist-seed', 'character_archivist_seed_v01.png', baseUrl),
    sheet('operator-ember', 'character_operator_ember_actions_v01.png', baseUrl)
  ];

  const role = (
    baseSheetId: string,
    cosmeticSheetId: string,
    cosmeticCol = 1
  ): RoleAppearance => ({
    base: { sheetId: baseSheetId, col: 0, row: 0 },
    states: {
      idle: { sheetId: baseSheetId, col: 0, row: 0 },
      walk: { sheetId: baseSheetId, col: 1, row: 1 },
      type: { sheetId: baseSheetId, col: 2, row: 1 },
      read: { sheetId: baseSheetId, col: 3, row: 1 },
      wait: { sheetId: baseSheetId, col: 4, row: 0 }
    },
    cosmetics: [{ sheetId: cosmeticSheetId, col: cosmeticCol, row: 2 }]
  });

  const roles: Record<string, RoleAppearance> = {
    dev: role('seed-01', 'operator-ember', 1),
    qa: role('seed-02', 'archivist', 2),
    architect: role('seed-03', 'archivist', 3),
    pm: role('seed-04', 'archivist-seed', 1),
    analyst: role('seed-05', 'archivist-seed', 2),
    sm: role('seed-06', 'archivist-seed', 3),
    'tech-writer': role('seed-01', 'archivist', 4),
    'ux-designer': role('seed-02', 'archivist-seed', 4),
    'grimoire-master': role('archivist', 'operator-ember', 2),
    'agent-builder': role('seed-03', 'operator-ember', 3),
    'workflow-builder': role('seed-04', 'operator-ember', 4),
    'module-builder': role('seed-05', 'operator-ember', 5),
    rodin: role('seed-06', 'archivist', 5),
    tea: role('seed-01', 'archivist', 5)
  };

  const defaultRole: RoleAppearance = {
    base: { sheetId: 'seed-01', col: 0, row: 0 },
    states: {
      idle: { sheetId: 'seed-01', col: 0, row: 0 },
      walk: { sheetId: 'seed-01', col: 1, row: 1 },
      type: { sheetId: 'seed-01', col: 2, row: 1 },
      read: { sheetId: 'seed-01', col: 3, row: 1 },
      wait: { sheetId: 'seed-01', col: 4, row: 0 }
    }
  };

  return createSpriteRegistry({ sheets, roles, defaultRole });
}
