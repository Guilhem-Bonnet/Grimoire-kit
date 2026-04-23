/**
 * rendering/sprite-image-loader.ts — V3.S3.7 part 2.
 *
 * Async preloader that turns a `SpriteRegistry` (pure references) into
 * a ready-to-use map of loaded images. The concrete `Image` constructor
 * is injected so tests can supply a fake that resolves synchronously
 * without touching the DOM.
 */

import type { SpriteRegistry } from './sprite-registry';

/**
 * Minimal surface we need from an HTMLImageElement. Enough to draw to
 * a canvas via `drawImage`. Matches both the DOM `Image` and any fake.
 */
export interface SpriteImageLike {
  src: string;
  width: number;
  height: number;
  complete: boolean;
  addEventListener?(type: string, handler: () => void): void;
  onload?: (() => void) | null;
  onerror?: (() => void) | null;
}

export interface SpriteLoadError {
  readonly sheetId: string;
  readonly url: string;
  readonly reason: string;
}

export interface SpriteImageBundle {
  readonly images: ReadonlyMap<string, SpriteImageLike>;
  readonly errors: readonly SpriteLoadError[];
}

export interface LoadSpriteImagesOptions {
  /** Factory for a new image. Defaults to `new Image()` in browsers. */
  readonly createImage?: () => SpriteImageLike;
  /**
   * Optional URL rewrite (e.g. prefixing with a CDN or Vite base).
   * Default: identity.
   */
  readonly resolveUrl?: (url: string) => string;
  /** Individual image timeout in ms. Default 10_000. */
  readonly timeoutMs?: number;
  /** Platform-provided timeout scheduler (for tests). Default setTimeout. */
  readonly setTimeoutFn?: (fn: () => void, ms: number) => unknown;
  readonly clearTimeoutFn?: (handle: unknown) => void;
}

function defaultCreateImage(): SpriteImageLike {
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  return new (globalThis as unknown as { Image: new () => SpriteImageLike }).Image();
}

/**
 * Load every sheet referenced by the registry in parallel. Failures do
 * not reject the whole bundle: unreachable sheets are reported in
 * `errors`. Callers can decide to fall back (e.g. colored cells) for
 * the missing ids.
 */
export function loadSpriteImages(
  registry: SpriteRegistry,
  options: LoadSpriteImagesOptions = {}
): Promise<SpriteImageBundle> {
  const createImage = options.createImage ?? defaultCreateImage;
  const resolveUrl = options.resolveUrl ?? ((url) => url);
  const timeoutMs = options.timeoutMs ?? 10_000;
  const setTimeoutFn =
    options.setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms) as unknown);
  const clearTimeoutFn =
    options.clearTimeoutFn ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  const images = new Map<string, SpriteImageLike>();
  const errors: SpriteLoadError[] = [];

  const tasks: Promise<void>[] = [];
  for (const sheet of registry.sheets.values()) {
    const img = createImage();
    const url = resolveUrl(sheet.url);

    tasks.push(
      new Promise<void>((resolve) => {
        let settled = false;
        const finish = (err?: string): void => {
          if (settled) return;
          settled = true;
          clearTimeoutFn(timer);
          if (err) {
            errors.push({ sheetId: sheet.id, url, reason: err });
          } else {
            images.set(sheet.id, img);
          }
          resolve();
        };

        const onLoad = (): void => finish();
        const onError = (): void => finish('error');

        if (img.addEventListener) {
          img.addEventListener('load', onLoad);
          img.addEventListener('error', onError);
        } else {
          img.onload = onLoad;
          img.onerror = onError;
        }

        const timer = setTimeoutFn(() => finish('timeout'), timeoutMs);
        img.src = url;
        if (img.complete) finish();
      })
    );
  }

  return Promise.all(tasks).then(() => ({ images, errors }));
}
