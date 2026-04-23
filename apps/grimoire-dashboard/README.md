# @grimoire-kit/grimoire-dashboard

Dashboard local de commande de l'agent platform Grimoire Forge.

**Différence avec [web/](../web/README.md) (vitrine publique)** : ici on consomme **des données runtime réelles** via WebSocket, et l'objectif est le pilotage jour-le-jour (observabilité agents, backlog, flows, console). Le site public `web/` reste une façade statique.

## Architecture (v0)

```
┌────────────────────────┐   fs.watch / tail    ┌────────────────────────────────────────┐
│ _grimoire-runtime/     │ ──────────────────▶  │ server/index.ts (Node + ws, :4175)     │
│ _memory/activity.jsonl │                      │  • hello → snapshot → event*           │
└────────────────────────┘                      │  • command → error:not_implemented (v0)│
                                                └──────────────┬─────────────────────────┘
                                                               │ ws://.../ws
                                                ┌──────────────▼──────────────┐
                                                │ DashboardWsClient (Zod)     │
                                                │ DashboardStore (projections)│
                                                │  • OfficeView + Placement   │
                                                │  • OfficeTimeline           │
                                                │  • OfficeDebugPanelView     │
                                                │  • kanban dérivé task.*     │
                                                └──────────────┬──────────────┘
                                                               │
                                                ┌──────────────▼──────────────┐
                                                │ 5 surfaces                  │
                                                │  • Observability    [live]  │
                                                │  • Agents · Office  [live]  │
                                                │  • Kanban           [live]  │
                                                │  • Flows        [partial]   │
                                                │  • Console      [partial]   │
                                                └─────────────────────────────┘
```

## Surfaces

| Surface | Statut | Backing |
|---|---|---|
| Observability | live | `buildOfficeTimeline` + `createOfficeDebugPanelView` |
| Agents · Office | live | `createOfficeView` + `resolveOfficePlacement` (S3.7 part 1) |
| Kanban | live | Dérivé local des événements `task.*` |
| Flows | partial | Recensement par `source_hook` · graphe d'édges à venir |
| Console | partial | Canal protocole câblé, serveur répond `error:not_implemented` |

## Utilisation

```bash
# 1 — installer
cd apps/grimoire-dashboard
npm install

# 2 — démarrer serveur WS + vite
npm run dev:all
# → http://localhost:4174 · tails _grimoire-runtime/_memory/activity.jsonl

# 3 — vérifier
npm run check   # tsc --noEmit
npm run test    # vitest
```

## Variables d'environnement

| Variable | Défaut | Effet |
|---|---|---|
| `GRIMOIRE_DASHBOARD_PORT` | `4175` | Port du serveur WebSocket |
| `GRIMOIRE_DASHBOARD_HOST` | `127.0.0.1` | Interface d'écoute |
| `GRIMOIRE_DASHBOARD_REPLAY` | `200` | Événements rejoués à la connexion |
| `GRIMOIRE_ACTIVITY_FILE` | auto | Chemin absolu vers `activity.jsonl` |

## Contrat WebSocket (`protocol_version: "1.0"`)

```ts
server → client : { type: 'hello' | 'snapshot' | 'event' | 'error', ... }
client → server : { type: 'subscribe' | 'command', ... }
```

Détails complets : [src/contracts/wsProtocol.ts](src/contracts/wsProtocol.ts)

## À faire

- Canal `command` câblé à de vraies APIs runtime (piloter agents, flows).
- Flows : projection avec graphe `source_hook × correlation_id`.
- Réutiliser les sprites pixel de grimoire-game quand S3.7 part 2 livrera le renderer.
