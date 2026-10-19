// Rendu image par image d'une page HTML pilotée par window.__seek(ms) → MP4 (ffmpeg).
// Usage : NODE_PATH=<node_modules playwright> node render.mjs <page.html> <out.mp4> [w] [h] [fps] [query]
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(process.env.NODE_PATH + '/');
const { chromium } = require('playwright');

const [, , page_path, out, W = '1920', H = '1080', FPS = '30', query = '', audio = ''] = process.argv;
const w = +W, h = +H, fps = +FPS;

const browser = await chromium.launch({ args: ['--enable-gpu', '--use-angle=vulkan', '--ignore-gpu-blocklist', '--allow-file-access-from-files'] });
const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on('console', m => { if (m.type() === 'error') console.error('[page]', m.text()); });
await page.goto(pathToFileURL(page_path).href + (query ? '?' + query : ''));
await page.evaluate(() => document.fonts.ready);
const duration = await page.evaluate(() => window.__duration);
const frames = Math.round(duration / 1000 * fps);

const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-i', '-',
  ...(audio ? ['-i', audio, '-c:a', 'aac', '-b:a', '192k', '-shortest'] : []),
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-tune', 'animation', '-pix_fmt', 'yuv420p',
  '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });

for (let i = 0; i < frames; i++) {
  await page.evaluate(ms => window.__seek(ms), i * 1000 / fps);
  const buf = await page.screenshot({ type: 'png' });
  if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
  if (i % (fps * 5) === 0) console.log(`image ${i}/${frames}`);
}
ff.stdin.end();
await new Promise(r => ff.on('close', r));
await browser.close();
console.log(`OK ${out} — ${frames} images, ${duration} ms, ${w}×${h} @ ${fps}`);
