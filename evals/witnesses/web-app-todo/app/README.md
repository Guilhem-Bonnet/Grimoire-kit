# Baseline `web-app-todo`

Application de gestion de tâches volontairement imparfaite, servant de témoin
d'évaluation (voir `../SPEC.md` et `../RUN-PROTOCOL.md`). Les deux bras
(`governed` / `baseline`) partent de cet état figé.

## Stack

| Couche | Techno |
|---|---|
| Frontend | React (Vite, TypeScript), tests Vitest |
| API | Go 1.22, `net/http` (ServeMux 1.22), driver `lib/pq` |
| Base | PostgreSQL 16, migrations SQL numérotées réversibles |
| Orchestration | `docker-compose` (postgres + api) |

## Démarrage

```bash
# Postgres + API (migrations et seed appliqués au démarrage de l'API)
docker compose up -d --build

# SPA en mode dev (proxy /api -> http://localhost:8080)
cd web && npm install && npm run dev
```

Ports publiés par défaut : API `8080`, PostgreSQL `5432`. Si un port est déjà
pris sur l'hôte, les surcharger sans modifier le compose :

```bash
API_PORT=18080 POSTGRES_PORT=15432 docker compose up -d --build
# et pour la SPA : API_PROXY_TARGET=http://localhost:18080 npm run dev
```

## Tests

```bash
cd api && go test ./... && go vet ./...   # sans Go local : via docker run golang:1.22
cd web && npm test
```

Les tests de la baseline sont verts et servent de filet de régression pour les
runs d'évaluation. Sans toolchain Go sur l'hôte :

```bash
cd api && docker run --rm -v "$PWD":/src -w /src golang:1.22 \
  sh -c "go test ./... && go vet ./..."
```

## Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/tasks` | Liste des tâches avec leurs tags |
| POST | `/tasks` | Créer une tâche (`{"title": "..."}`) |
| PUT | `/tasks/{id}` | Modifier titre et/ou done |
| POST | `/tasks/{id}/complete` | Marquer terminée (unitaire) |

## Migrations

`api/migrations/NNNN_*.up.sql` sont appliquées au démarrage de l'API dans
l'ordre lexical (journal `schema_migrations`). Chaque migration a son
`NNNN_*.down.sql` (réversibilité) ; application manuelle :

```bash
docker compose exec -T postgres psql -U todo -d todo < api/migrations/0002_seed.down.sql
```

## Défauts intentionnels — NE PAS corriger

Ces défauts sont les points d'ancrage des tâches d'évaluation
(`evals/tasks/web-app-todo.yaml`). Les « corriger » hors d'un run invalide le
témoin.

1. **N+1 sur `GET /tasks`** — `api/handlers.go` (`listTasks`) exécute une
   requête tags par tâche via `TagsForTask`. Le compteur `TagQueries` du
   `fakeStore` (`api/handlers_test.go`) permet un test de comptage de
   requêtes. Ancre de `fix-n-plus-one`.
2. **Affichage UTC brut** — `web/src/components/TaskItem.tsx` affiche
   `task.created_at` tel quel (ISO 8601 UTC), sans conversion au fuseau du
   navigateur. Le stockage en base est en UTC et doit le rester. Ancre de
   `fix-timezone-display`.
3. **Validation inline dans les handlers** — `api/handlers.go` valide les
   payloads directement dans `createTask` / `updateTask`, sans package dédié.
   Ancre de `refactor-handlers`.
4. **Appels `fetch` dispersés** — `TaskList.tsx`, `TaskItem.tsx` et
   `AddTaskForm.tsx` appellent chacun `fetch` directement, sans client API
   centralisé ni gestion d'erreurs uniforme. Ancre de `refactor-api-client`.
5. **Go 1.22 + API dépréciées** — `api/go.mod` est épinglé `go 1.22` ;
   `api/handlers.go` utilise `io/ioutil.ReadAll` (déprécié depuis 1.16) et
   `strings.Title` (déprécié depuis 1.18), toutes deux toujours signalées par
   les linters (SA1019) en Go 1.23. Note factuelle : les release notes Go 1.23
   ne marquent aucune API stdlib comme nouvellement dépréciée ; l'ancre de
   `migrate-go-version` est le bump 1.22 → 1.23 (toolchain, image Docker
   `golang:1.22-alpine`) avec traitement des dépréciations signalées.
6. **Aucun rate-limit** — les endpoints d'écriture (`POST /tasks`,
   `PUT /tasks/{id}`, `POST /tasks/{id}/complete`) sont servis sans aucune
   limitation par IP. Ancre de `sec-rate-limit`.
7. **Pas d'échéance, complete unitaire** — le schéma `tasks` n'a pas de champ
   d'échéance (ancre de `feat-due-dates`) et il n'existe qu'un endpoint
   `complete` unitaire, pas de version batch (ancre de `feat-bulk-complete`).

## Structure

```text
app/
├── docker-compose.yml
├── api/
│   ├── main.go            # bootstrap, attente DB, migrations
│   ├── migrate.go         # runner de migrations (embed)
│   ├── handlers.go        # 4 endpoints, validation inline
│   ├── store.go           # types + interface Store
│   ├── store_pg.go        # implémentation PostgreSQL
│   ├── handlers_test.go   # tests handlers sur fakeStore en mémoire
│   └── migrations/        # NNNN_*.up.sql / NNNN_*.down.sql
└── web/
    └── src/
        ├── App.tsx
        ├── components/    # TaskList, TaskItem, AddTaskForm (fetch directs)
        └── test/          # Vitest + Testing Library
```
