import { describe, it, expect } from 'vitest';
import { createSpriteRegistry } from '../../src/rendering/sprite-registry';
import {
  loadSpriteImages,
  type SpriteImageLike
} from '../../src/rendering/sprite-image-loader';

type Behaviour = 'success' | 'error' | 'stall';

class FakeImage implements SpriteImageLike {
  private _src = '';
  public width = 16;
  public height = 32;
  public complete = false;
  public onload: (() => void) | null = null;
  public onerror: (() => void) | null = null;

  constructor(private readonly behaviour: Behaviour) {}

  get src(): string {
    return this._src;
  }

  set src(value: string) {
    this._src = value;
    const self = this;
    queueMicrotask(() => {
      if (self.behaviour === 'success') {
        self.complete = true;
        self.onload?.();
      } else if (self.behaviour === 'error') {
        self.onerror?.();
      }
    });
  }
}

function createFactory(plan: readonly Behaviour[]): {
  create: () => SpriteImageLike;
  created: FakeImage[];
} {
  const created: FakeImage[] = [];
  let index = 0;
  return {
    create() {
      const img = new FakeImage(plan[index] ?? 'success');
      index += 1;
      created.push(img);
      return img;
    },
    created
  };
}

describe('sprite-image-loader', () => {
  it('loads every sheet from the registry', async () => {
    const registry = createSpriteRegistry({
      sheets: [
        { id: 'a', url: 'a.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 },
        { id: 'b', url: 'b.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 }
      ],
      roles: {},
      defaultRole: { base: { sheetId: 'a', col: 0, row: 0 } }
    });
    const factory = createFactory(['success', 'success']);
    const bundle = await loadSpriteImages(registry, { createImage: factory.create });
    expect(bundle.images.size).toBe(2);
    expect(bundle.errors).toHaveLength(0);
  });

  it('reports errors but does not reject the whole load', async () => {
    const registry = createSpriteRegistry({
      sheets: [
        { id: 'a', url: 'a.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 },
        { id: 'b', url: 'b.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 }
      ],
      roles: {},
      defaultRole: { base: { sheetId: 'a', col: 0, row: 0 } }
    });
    const factory = createFactory(['success', 'error']);
    const bundle = await loadSpriteImages(registry, { createImage: factory.create });
    expect(bundle.images.size).toBe(1);
    expect(bundle.images.has('a')).toBe(true);
    expect(bundle.errors).toHaveLength(1);
    expect(bundle.errors[0]?.sheetId).toBe('b');
  });

  it('applies resolveUrl to rewrite asset URLs', async () => {
    const registry = createSpriteRegistry({
      sheets: [
        { id: 'a', url: 'a.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 }
      ],
      roles: {},
      defaultRole: { base: { sheetId: 'a', col: 0, row: 0 } }
    });
    const factory = createFactory(['success']);
    await loadSpriteImages(registry, {
      createImage: factory.create,
      resolveUrl: (u) => `/cdn/${u}`
    });
    expect(factory.created[0]?.src).toBe('/cdn/a.png');
  });

  it('times out stalled image loads', async () => {
    const registry = createSpriteRegistry({
      sheets: [
        { id: 'a', url: 'a.png', frameWidth: 16, frameHeight: 32, cols: 2, rows: 1 }
      ],
      roles: {},
      defaultRole: { base: { sheetId: 'a', col: 0, row: 0 } }
    });
    const factory = createFactory(['stall']);
    const bundle = await loadSpriteImages(registry, {
      createImage: factory.create,
      timeoutMs: 5,
      setTimeoutFn: (fn) => {
        queueMicrotask(fn);
        return 0;
      },
      clearTimeoutFn: () => undefined
    });
    expect(bundle.images.size).toBe(0);
    expect(bundle.errors[0]?.reason).toBe('timeout');
  });
});
