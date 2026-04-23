/**
 * rendering/sprite-registry.ts — V3.S3.7 part 2.
 *
 * Pure data layer mapping each agent `role` to a visual appearance:
 * a base sprite sheet + per-state frame + optional cosmetic overlays
 * (accessories drawn on top of the base, same frame dimensions).
 *
 * The registry is **renderer-agnostic**: it contains no DOM, no Image,
 * no canvas. It emits plain references (sheet id + frame col/row) and
 * lets a platform adapter resolve them against a real bitmap later.
 *
 * Rationale (session cadrage 20260423): "prend la base des personnages
 * rajoute leurs juste des cosmétiques / accessoires pour créer des
 * roles". One base character × swapped cosmetics = role identity, no
 * duplication of base art per role.
 */

import type { OfficeCharacterState } from '../state/office-view';

export const SPRITE_REGISTRY_SCHEMA_VERSION = '1.0.0';

export interface SpriteSheet {
  /** Unique id referenced by SpriteFrame.sheetId. */
  readonly id: string;
  /** Asset URL (relative to a renderer-provided base). */
  readonly url: string;
  /** Width of one frame in pixels. */
  readonly frameWidth: number;
  /** Height of one frame in pixels. */
  readonly frameHeight: number;
  /** Number of columns of frames on the sheet. */
  readonly cols: number;
  /** Number of rows of frames on the sheet. */
  readonly rows: number;
}

export interface SpriteFrame {
  readonly sheetId: string;
  /** 0-based column index. */
  readonly col: number;
  /** 0-based row index. */
  readonly row: number;
}

export interface RoleAppearance {
  /** Fallback frame used when a state has no dedicated entry. */
  readonly base: SpriteFrame;
  /** Per-state frames. Missing states fall back to `base`. */
  readonly states?: Partial<Record<OfficeCharacterState, SpriteFrame>>;
  /** Cosmetic overlays drawn on top of the base, in array order. */
  readonly cosmetics?: readonly SpriteFrame[];
}

export interface SpriteRegistry {
  readonly schemaVersion: string;
  readonly sheets: ReadonlyMap<string, SpriteSheet>;
  /** Role → appearance. `__default__` is used when a role is not listed. */
  readonly roles: ReadonlyMap<string, RoleAppearance>;
}

export interface SpriteRegistryInput {
  readonly sheets: readonly SpriteSheet[];
  readonly roles: Readonly<Record<string, RoleAppearance>>;
  /** Appearance used when a role has no entry in `roles`. */
  readonly defaultRole: RoleAppearance;
}

export const DEFAULT_ROLE_KEY = '__default__';

export class SpriteRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SpriteRegistryError';
  }
}

/**
 * Build an immutable registry from a flat input. Validates that:
 *   - every role (incl. default) references an existing sheet;
 *   - every frame is within its sheet's cols/rows bounds;
 *   - cosmetics may live on a different sheet but must still be valid.
 *
 * Throws `SpriteRegistryError` on the first invalid reference so bad
 * data fails loud at bootstrap rather than silently at draw time.
 */
export function createSpriteRegistry(input: SpriteRegistryInput): SpriteRegistry {
  const sheets = new Map<string, SpriteSheet>();
  for (const sheet of input.sheets) {
    if (sheets.has(sheet.id)) {
      throw new SpriteRegistryError(`duplicate sheet id '${sheet.id}'`);
    }
    if (sheet.cols <= 0 || sheet.rows <= 0) {
      throw new SpriteRegistryError(`sheet '${sheet.id}' has non-positive grid`);
    }
    sheets.set(sheet.id, sheet);
  }

  const roles = new Map<string, RoleAppearance>();
  const validate = (roleKey: string, appearance: RoleAppearance): void => {
    validateFrame(roleKey, 'base', appearance.base, sheets);
    if (appearance.states) {
      for (const [state, frame] of Object.entries(appearance.states)) {
        if (frame) validateFrame(roleKey, `states.${state}`, frame, sheets);
      }
    }
    if (appearance.cosmetics) {
      for (let i = 0; i < appearance.cosmetics.length; i += 1) {
        validateFrame(roleKey, `cosmetics[${i}]`, appearance.cosmetics[i]!, sheets);
      }
    }
  };

  validate(DEFAULT_ROLE_KEY, input.defaultRole);
  roles.set(DEFAULT_ROLE_KEY, input.defaultRole);

  for (const [role, appearance] of Object.entries(input.roles)) {
    if (role === DEFAULT_ROLE_KEY) {
      throw new SpriteRegistryError(`role key '${DEFAULT_ROLE_KEY}' is reserved`);
    }
    validate(role, appearance);
    roles.set(role, appearance);
  }

  return {
    schemaVersion: SPRITE_REGISTRY_SCHEMA_VERSION,
    sheets,
    roles
  };
}

function validateFrame(
  roleKey: string,
  field: string,
  frame: SpriteFrame,
  sheets: ReadonlyMap<string, SpriteSheet>
): void {
  const sheet = sheets.get(frame.sheetId);
  if (!sheet) {
    throw new SpriteRegistryError(
      `role '${roleKey}' ${field} references unknown sheet '${frame.sheetId}'`
    );
  }
  if (frame.col < 0 || frame.col >= sheet.cols) {
    throw new SpriteRegistryError(
      `role '${roleKey}' ${field} col ${frame.col} out of range on sheet '${sheet.id}' (cols=${sheet.cols})`
    );
  }
  if (frame.row < 0 || frame.row >= sheet.rows) {
    throw new SpriteRegistryError(
      `role '${roleKey}' ${field} row ${frame.row} out of range on sheet '${sheet.id}' (rows=${sheet.rows})`
    );
  }
}

/**
 * Resolve the appearance for a role. Falls back to the default when
 * unknown. The resolution is case-sensitive.
 */
export function resolveAppearance(registry: SpriteRegistry, role: string): RoleAppearance {
  return registry.roles.get(role) ?? registry.roles.get(DEFAULT_ROLE_KEY)!;
}

/**
 * Pick the frame for a given state, falling back to the base frame.
 */
export function resolveFrame(
  appearance: RoleAppearance,
  state: OfficeCharacterState
): SpriteFrame {
  return appearance.states?.[state] ?? appearance.base;
}
