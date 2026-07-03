# Grimoire Game

Ce package porte les projections runtime et les démos web locales du Grimoire Game.

La démo actuelle ne montre pas une page unique fusionnée. Elle matérialise explicitement dix surfaces complementaires alimentees par les memes read models runtime, plus un panel VS Code hote branche sur les memes projections.

- `Cockpit` : surface principale pour lire l'état, diagnostiquer et décider.
- `Game UI` : HUD board-first pour lire rooms, agents, lanes et guardrails depuis le même `GameState`.
- `Observability` : deck dédié aux métriques, timelines, blockers, health checks et hotspots de collaboration/sécurité.
- `Spectator` : surface read-only partageable, avec token et lien borné pour la lecture web et VS Code.
- `Observer` : projection spatiale des rooms, entités et handoffs.
- `Workflow` : lecture des chemins, décisions et audit trails par trace/task.
- `Expert` : revue approfondie avec preuve, replay et deep inspection.
- `Observatory` : surface adjacente en lecture seule, branchée sur les HTML existants du projet.
- `War Room` : surface explicative dérivée, pensée pour challenger et raconter la causalité du run.
- `Host Bridge` : vue generique des canaux navigateur, VS Code et hotes externes sur les memes primitives runtime.
- `VS Code Panel` : shell webview borné, avec fallback navigateur explicite, persistance d'état locale et remontée de commandes read-only vers l'hôte.

## Commandes utiles

```bash
npm run check
npm run build
npm run cockpit:dev
npm run cockpit:build
npm run cockpit:verify
npm run demo:views
npm run demo:report
npm run release:verify
```

## Sorties générées

- `npm run cockpit:dev` prépare les sources Observatory disponibles puis sert la surface locale Vite.
- `npm run cockpit:build` produit l'application locale dans `.release/cockpit-app/`.
- `npm run cockpit:verify` rejoue la preuve complète du cockpit local : démo console, rapport statique et app buildable.
- `npm run demo:views` exécute la démo console sur les scénarios runtime.
- `npm run demo:report` régénère le shell HTML dans `.release/runtime-views-report.html`.
- `npm run release:verify` rejoue typecheck, build, couverture, shells web et packaging npm.
- Le client navigateur associé est copié dans `.release/runtime-views-report-client.js` et inclut aussi les modes `game-ui`, `observability`, `host-bridge` et `vscode` en preview/fallback.

## Validation ciblée

```bash
npx vitest run tests/integration/power-cards-view.test.ts tests/integration/provenance-compliance-view.test.ts
npx vitest run tests/integration/generic-host-bridge-view.test.ts tests/contracts/runtime-web-scenarios.contract.test.ts
npx vitest run tests/contracts/vscode-webview-bridge.contract.test.ts tests/integration/vscode-panel-view.test.ts
npx vitest run tests/integration/runtime-game-ui-view.test.ts tests/integration/runtime-observability-surface-view.test.ts
```

## Ce que montre le shell

### Cockpit

Le cockpit expose les vues `power cards`, `provenance compliance` et `branch finisher`, plus un walkthrough, une lecture host/proof et un inspecteur JSON pour vérifier rapidement les signaux bloquants.

La surface Vite locale conserve `GameState` comme source de vérité et réutilise les mêmes scénarios sérialisés que le rapport statique pour garder une parité stricte entre les deux shells.

### Game UI, Observability, Spectator, Observer, Workflow, Expert, Host Bridge et panel VS Code

Le shell local et le rapport statique exposent aussi des surfaces runtime additionnelles, puis un shell VS Code hote sur les memes read models.

- `Game UI` projette les rooms, les lanes de tâches et de vérification, les cartes de décision et les guardrails sécurité dans un HUD dédié.
- `Observability` projette une lecture explicitement tournée vers la timeline, les blockers de vérification, la santé des connexions et les hotspots sécurité/collaboration.
- `Spectator` projette un mode partageable read-only. Le lien embarque `mode=spectator`, le scénario et un token émis via `spectator.share`; les mutations restent refusées, mais la navigation de focus demeure lisible.
- `Observer` projette les rooms, les entités et les handoffs tout en vérifiant la parité avec le cockpit.
- `Workflow` déroule les paths, steps, decisions et audit trails dérivés du run.
- `Expert` agrège les décisions host, la preuve de vérification, le replay et la deep inspection d'agent.
- `Host Bridge` projette les canaux, packets, reviews et imports de contexte pour navigateur, VS Code, Copilot, Claude et MCP sans second modele metier.
- `VS Code Panel` projette les lanes runtime, la file de vérification, le host bridge et un lot borné de commandes read-only. En navigateur pur, le mode reste en preview et n'ouvre aucune voie d'écriture.

## API publique

Le point d'entrée du package réexporte aussi les projections runtime prêtes à intégrer dans une UI ou un shell local.

- `createRuntimeDashboardStore` et `createRuntimeDashboardView` pour hydrater et projeter le run.
- `createRuntimeCockpitView`, `createRuntimeGameUiView`, `createRuntimeObservabilitySurfaceView`, `createRuntimeObserverView` et `createWorkflowVisualizationView` pour exposer des surfaces opérateur, HUD, observability et de traçage.
- `createSpectatorSurfaceView` pour brancher une surface read-only partageable sans réouvrir de write path.
- `createExpertCockpitView` et `createDeepInspectionView` pour les vues expertes et l'analyse détaillée.
- `createGenericHostBridgeView` pour projeter un host bridge generique sur les memes primitives runtime.
- `createVsCodePanelView` et `createVsCodePanelBridge` pour projeter le panel webview VS Code sans créer de second modèle métier.

## Parcours contributeur et claims publics

- Parcours contributeur executable : [../../../docs/exploitation/parcours-contributeur-cockpit-v5.md](../../../docs/exploitation/parcours-contributeur-cockpit-v5.md)
- Claims, non-claims et experimental scopes : [../../../docs/governance/claims-publics-cockpit-v5.md](../../../docs/governance/claims-publics-cockpit-v5.md)
- Gates de release : [../../../docs/governance/release-checklist-v0.1.0.md](../../../docs/governance/release-checklist-v0.1.0.md)

### Observatory

L'observatory reste une intégration read-only. Le shell cherche en priorité les fichiers suivants et les embarque si disponibles.

- `_grimoire-runtime-output/observatory.html`
- `_grimoire-output/observatory.html`

Pour la surface Vite locale, ces sorties sont copiées au moment de `npm run cockpit:prepare` vers des assets temporaires sous `.generated/` avant d'être servies en iframe.

### War Room

La war room ne crée pas de second modèle métier. Elle reformule les mêmes projections runtime en zones tactiques pour rendre les handoffs, les frictions et les gates de release plus lisibles.
