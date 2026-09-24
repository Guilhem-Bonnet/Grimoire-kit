# design-sync — notes du dépôt

- Le cockpit (`web/workspace/`) est une coque JavaScript sans React ni build : `tokens.css` (seule source de couleur, sombre et clair, Geist embarquée), `shell.css`, `spaces/source.css`, `shell.js` + `spaces/*.js` qui rendent le DOM.
- Périmètre choisi par Guilhem le 2026-09-24 : **tokens et styles seuls** (componentCount 0, reconnu par `package-validate.mjs`), pas de composants React — une réécriture du balisage serait une réimplémentation à maintenir en parallèle de `shell.js`.
- La disposition `ds-bundle/` est produite hors convertisseur (pas de `package.json` ni de `dist/`) par `scripts/design-sync-cockpit.py` ; `package-validate.mjs` reste la gate.
- `forge-tokens.css` (vitrine/atelier historique) n'est PAS le cockpit : ne pas le confondre avec `web/workspace/tokens.css`.
- Validation : `node .ds-sync/package-validate.mjs ./ds-bundle --no-render-check` — le contrôle de rendu ne porte que sur des aperçus de composants, il n'y en a aucun ici. Avertissement `[FONT_MISSING] "Cascadia Mono"` accepté : c'est un repli de la pile `--mono`, Geist Mono est embarquée.
- Premier dépôt le 2026-09-24 dans le projet Claude Design « Grimoire Cockpit » (`projectId` dans config.json), chemin incrémental, 20 fichiers, ancre `_ds_sync.json` écrite en dernier.

## Risques de re-synchronisation

- `tokens/spaces.css` est extrait des chaînes `style.textContent = \`…\`` de `web/workspace/spaces/*.js` : un module qui passerait à une autre forme d'injection (interpolation `${}`, feuille séparée) sortirait silencieusement du bundle — le générateur le compte dans `spaceCssSkipped` de `.ds-build-meta.json`, à lire après chaque build.
- Le guide `conventions.md` nomme des tokens, classes et identifiants : à re-vérifier contre `ds-bundle/` après tout changement de `tokens.css` ou `shell.css` (le générateur ne le fait pas ; la passe de validation est dans l'historique de session du 2026-09-24, à refaire à la main ou à scripter).
- Aucun aperçu vérifié visuellement : la fidélité tient au CSS réel copié tel quel, pas à une capture.
