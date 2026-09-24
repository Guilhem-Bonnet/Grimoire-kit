#!/usr/bin/env node
// Disposition Claude Design du cockpit — tokens et styles seuls, hors
// convertisseur (le cockpit `web/workspace/` est une coque JavaScript sans
// React ni build : il n'y a ni package.json ni dist/ à convertir).
//
// Ce que ça produit sous ds-bundle/ (contrat de /design-sync, validé par
// .ds-sync/package-validate.mjs) :
//   styles.css            → @import de tokens/tokens.css, tokens/spaces.css et _ds_bundle.css
//   tokens/tokens.css     → web/workspace/tokens.css, url() des fontes réécrites vers ../fonts/
//   tokens/spaces.css     → le CSS que chaque module d'espace injecte (style.textContent = `…`)
//   _ds_bundle.css        → shell.css + spaces/source.css, tels quels
//   fonts/                → Geist et Geist Mono (woff2 + OFL)
//   guidelines/           → la spec et la revue de design de la vue de travail
//   _ds_bundle.js         → bundle vide avec l'en-tête @ds-bundle (0 composant)
//   README.md             → .design-sync/conventions.md + index généré des tokens et des classes
//   .ds-build-meta.json, _ds_sync.json (ancre, écrite en dernier)
//
// Usage : node .design-sync/build-cockpit.mjs   (depuis la racine du dépôt)
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const ROOT = resolve('.');
const SRC = join(ROOT, 'web', 'workspace');
const OUT = join(ROOT, 'ds-bundle');
const cfg = JSON.parse(readFileSync(join(ROOT, '.design-sync', 'config.json'), 'utf8'));
const { styleShaFor, auxShaFor, scriptsShaFor, KEY_RECIPE } = await import(
  new URL('../.ds-sync/lib/sync-hashes.mjs', import.meta.url).href,
);

rmSync(OUT, { recursive: true, force: true });
for (const d of ['tokens', 'fonts', 'guidelines']) mkdirSync(join(OUT, d), { recursive: true });

// tokens.css : les fontes vivent à côté dans web/workspace/fonts/, ici sous fonts/.
const tokensCss = readFileSync(join(SRC, 'tokens.css'), 'utf8').replaceAll('url("./fonts/', 'url("../fonts/');
writeFileSync(join(OUT, 'tokens', 'tokens.css'), tokensCss);
for (const f of readdirSync(join(SRC, 'fonts'))) cpSync(join(SRC, 'fonts', f), join(OUT, 'fonts', f));

// CSS injecté par les espaces : chaque `style.textContent = \`…\`` d'un module.
const spaceFiles = readdirSync(join(SRC, 'spaces')).filter((f) => f.endsWith('.js')).sort();
const chunks = [];
const skipped = [];
for (const f of spaceFiles) {
  const js = readFileSync(join(SRC, 'spaces', f), 'utf8');
  for (const m of js.matchAll(/style\.textContent\s*=\s*`([\s\S]*?)`;/g)) {
    if (m[1].includes('${')) { skipped.push(f); continue; }
    chunks.push(`/* ── ${f} ── */\n${m[1].trim()}\n`);
  }
}
writeFileSync(join(OUT, 'tokens', 'spaces.css'),
  `/* CSS des six espaces, extrait des modules web/workspace/spaces/*.js (injecté à l'exécution). */\n\n${chunks.join('\n')}`);

// Coque + espace Source : le "CSS de composants" du kit.
writeFileSync(join(OUT, '_ds_bundle.css'),
  `/* web/workspace/shell.css */\n${readFileSync(join(SRC, 'shell.css'), 'utf8')}\n\n/* web/workspace/spaces/source.css */\n${readFileSync(join(SRC, 'spaces', 'source.css'), 'utf8')}`);
writeFileSync(join(OUT, 'styles.css'),
  `@import "./tokens/tokens.css";\n@import "./_ds_bundle.css";\n@import "./tokens/spaces.css";\n`);

for (const g of ['DESIGN-SPEC-workspace-2026-09.md', 'DESIGN-REVIEW-2026-09.md']) cpSync(join(ROOT, 'web', g), join(OUT, 'guidelines', g));
cpSync(join(SRC, 'README.md'), join(OUT, 'guidelines', 'workspace-README.md'));

// Bundle vide : aucun composant React, la disposition ne porte que le style.
const header = { namespace: cfg.globalName, components: [], sourceHashes: {}, inlinedExternals: [], builtBy: 'cc-design-sync' };
writeFileSync(join(OUT, '_ds_bundle.js'),
  `/* @ds-bundle: ${JSON.stringify(header).replace(/\*\//g, '*\\/')} */\n(function(){window.${cfg.globalName}=window.${cfg.globalName}||{};})();\n`);

// README : conventions (rédigées) + index généré des tokens et des classes.
const tokenLines = [];
const seen = new Set();
for (const m of tokensCss.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
  if (seen.has(m[1])) continue;
  seen.add(m[1]);
  tokenLines.push(`| \`${m[1]}\` | \`${m[2].trim()}\` |`);
}
const classNames = (css) => [...new Set([...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1]))].sort();
const shellClasses = classNames(readFileSync(join(SRC, 'shell.css'), 'utf8'));
const spaceClasses = classNames(readFileSync(join(OUT, 'tokens', 'spaces.css'), 'utf8'));
const header_md = cfg.readmeHeader ? readFileSync(join(ROOT, cfg.readmeHeader), 'utf8') : '';
const body = `
## Index généré

Source : \`web/workspace/\` du dépôt grimoire-kit (${cfg.pkg}). Aucun composant React : ce design system ne porte que les tokens, la coque et le CSS des espaces.

### Tokens (\`tokens/tokens.css\`, valeurs du thème sombre ; le thème clair redéfinit chacun)

| Token | Valeur |
|---|---|
${tokenLines.join('\n')}

### Classes de la coque (\`_ds_bundle.css\`, ${shellClasses.length})

${shellClasses.map((c) => `\`.${c}\``).join(', ')}

### Classes des espaces (\`tokens/spaces.css\`, ${spaceClasses.length})

${spaceClasses.map((c) => `\`.${c}\``).join(', ')}

### Guides (\`guidelines/\`)

- \`DESIGN-SPEC-workspace-2026-09.md\` — la spécification validée de la vue de travail (décisions, tokens, chrome, espaces).
- \`DESIGN-REVIEW-2026-09.md\` — la revue de direction artistique qui l'a précédée.
- \`workspace-README.md\` — l'organisation des modules de la coque.
`;
writeFileSync(join(OUT, 'README.md'), `${header_md}\n${body}`);

writeFileSync(join(OUT, '.ds-build-meta.json'), JSON.stringify({ componentCount: 0, shape: 'package', pkg: cfg.pkg, globalName: cfg.globalName, builtAt: new Date().toISOString(), spaceCssSkipped: skipped }, null, 2) + '\n');
const bundleBuf = readFileSync(join(OUT, '_ds_bundle.js'));
writeFileSync(join(OUT, '_ds_sync.json'), JSON.stringify({
  shape: 'package',
  styleSha: styleShaFor(OUT, { includeBundleBody: true }),
  renderHashes: {},
  sourceKeys: {},
  keyRecipe: KEY_RECIPE,
  scriptsSha: scriptsShaFor(),
  sourceHashes: {},
  auxSha: auxShaFor(OUT),
  bundleSha12: createHash('sha256').update(bundleBuf).digest('hex').slice(0, 12),
}, null, 2) + '\n');
console.error(`  ds-bundle/ : ${tokenLines.length} tokens, ${shellClasses.length} classes de coque, ${spaceClasses.length} classes d'espaces, ${chunks.length} blocs de CSS d'espace${skipped.length ? ` (${skipped.length} bloc(s) interpolé(s) ignoré(s) : ${skipped.join(', ')})` : ''}`);
