import { defineConfig, type Plugin } from 'vite';
import path from 'node:path';
import fs from 'node:fs';

const ASSETS_ROOT = path.resolve(
  __dirname,
  '../../../grimoire-game-assets/10-curated/characters'
);

/**
 * Dev-only middleware that serves the curated character sprite sheets
 * at `/assets/characters/*`. Reuses the single source of truth from
 * `grimoire-game-assets/10-curated/` instead of duplicating assets.
 */
function curatedAssetsPlugin(): Plugin {
  return {
    name: 'grimoire-dashboard-curated-assets',
    configureServer(server) {
      server.middlewares.use('/assets/characters', (req, res, next) => {
        const url = (req.url ?? '/').split('?')[0] ?? '/';
        const safe = path.posix.normalize(url).replace(/^(\/?\.\.\/?)+/, '/');
        const file = path.join(ASSETS_ROOT, safe);
        if (!file.startsWith(ASSETS_ROOT) || !fs.existsSync(file)) return next();
        res.setHeader('content-type', 'image/png');
        fs.createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      // Copy curated sprite sheets into the build output.
      const outDir = path.resolve(__dirname, 'dist', 'assets', 'characters');
      if (!fs.existsSync(ASSETS_ROOT)) return;
      fs.mkdirSync(outDir, { recursive: true });
      for (const entry of fs.readdirSync(ASSETS_ROOT)) {
        if (entry.endsWith('.png')) {
          fs.copyFileSync(path.join(ASSETS_ROOT, entry), path.join(outDir, entry));
        }
      }
    }
  };
}

// Grimoire Dashboard — live local control plane.
// Runs on :4174 by default to avoid clashing with the cockpit demo (:5173).
export default defineConfig({
  root: path.resolve(__dirname, 'app'),
  publicDir: path.resolve(__dirname, 'public'),
  plugins: [curatedAssetsPlugin()],
  resolve: {
    alias: {
      '@game': path.resolve(__dirname, '../grimoire-game/src'),
      '@dashboard': path.resolve(__dirname, 'src')
    }
  },
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    sourcemap: true
  },
  server: {
    port: 4174,
    strictPort: true,
    proxy: {
      '/ws': {
        target: 'ws://localhost:4175',
        ws: true,
        changeOrigin: true
      }
    }
  }
});
