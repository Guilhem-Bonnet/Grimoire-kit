# Contexte Partagé — {{project_name}}

<!-- ARCHETYPE: web-app — Template de shared-context pour applications web.
     Adaptez les sections à votre stack. Les agents déployés dépendent
     du stack détecté par grimoire-init.sh --auto (Gopher, Pixel, Serpent, Container...) -->

> Ce fichier est chargé par tous les agents au démarrage.
> Il est la source de vérité pour le contexte projet.

## Projet

- **Nom** : {{project_name}}
- **Description** : {{project_description}}
- **Dépôt** : {{repo_url}}
- **Type** : Web App — {{app_type}}  _(ex: SPA + API REST, fullstack Next.js, backend headless...)_

## Stack Technique

| Couche | Technologie | Version | Répertoire |
|--------|-------------|---------|------------|
| Frontend | {{frontend_tech}} | {{frontend_version}} | `{{frontend_dir}}` |
| Backend | {{backend_tech}} | {{backend_version}} | `{{backend_dir}}` |
| Base de données | {{db_tech}} | {{db_version}} | — |
| Auth | {{auth_tech}} | — | — |
| Cache | {{cache_tech}} | — | — |
| Build/Deploy | {{deploy_tech}} | — | — |

## Architecture

```
{{project_name}}/
├── {{frontend_dir}}/      # Frontend {{frontend_tech}}
│   ├── src/
│   │   ├── components/    # Composants UI
│   │   ├── pages/         # Pages / Routes
│   │   ├── hooks/         # Custom hooks
│   │   ├── api/           # Appels API (client)
│   │   └── types/         # Types TypeScript
│   └── tests/
├── {{backend_dir}}/       # Backend {{backend_tech}}
│   ├── handlers/          # HTTP handlers
│   ├── services/          # Logique métier
│   ├── repository/        # Accès données (DB)
│   ├── middleware/        # Auth, logging, CORS
│   └── tests/
└── docker/                # Docker Compose dev + prod
```

## API

- **Base URL** : `{{api_base_url}}`  _(ex: http://localhost:8080/api/v1)_
- **Auth** : {{auth_method}}  _(ex: JWT Bearer, session cookie, API key)_
- **Format** : JSON
- **Docs** : {{api_docs_url}}  _(ex: /swagger, /docs)_

### Routes principales

| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/health` | Health check |
| POST | `/auth/login` | Authentification |
| GET | `/{{resource_1}}` | Lister {{resource_1}} |
| POST | `/{{resource_1}}` | Créer {{resource_1}} |

## Base de données

- **Moteur** : {{db_tech}}
- **Connexion** : variable d'env `{{db_env_var}}`  _(ex: DATABASE_URL)_
- **Migrations** : {{migration_tool}}  _(ex: golang-migrate, Alembic, Prisma)_
- **Schéma** : `{{schema_dir}}`

### Tables / Collections principales

| Table | Description | Colonnes clés |
|-------|-------------|---------------|
| {{table_1}} | {{table_1_desc}} | id, created_at, {{cols_1}} |
| {{table_2}} | {{table_2_desc}} | id, {{cols_2}} |

## Environnement local

```bash
# Démarrer le stack complet
{{dev_start_command}}   # ex: docker compose up -d && make dev

# Backend seul
{{backend_dev_cmd}}     # ex: go run ./cmd/api / uvicorn main:app --reload

# Frontend seul
{{frontend_dev_cmd}}    # ex: npm run dev / pnpm dev

# Tests
{{test_cmd}}            # ex: go test ./... / pytest / npm test
```

## Variables d'environnement

| Variable | Env | Description |
|----------|-----|-------------|
| `DATABASE_URL` | dev/prod | DSN base de données |
| `JWT_SECRET` | prod | Secret pour JWT |
| `API_URL` | frontend | URL de l'API backend |
| `{{var_1}}` | {{env_1}} | {{var_1_desc}} |

> ⚠️ Ne jamais commiter les valeurs réelles — utiliser `.env.local` (gitignored)

## Équipe d'Agents Custom

> Complétez avec les agents réellement déployés par `grimoire-init.sh --auto`

| Agent | Nom | Icône | Domaine |
|-------|-----|-------|---------|
| project-navigator | Atlas | 🗺️ | Navigation & Registre des services |
| agent-optimizer | Sentinel | 🔍 | Qualité agents + Self-Improvement Loop |
| memory-keeper | Mnemo | 🧠 | Mémoire & Contradictions |
| go-expert _(si stack Go)_ | Gopher | 🐹 | Backend Go — handlers, tests, perf |
| typescript-expert _(si stack TS)_ | Pixel | ⚛️ | Frontend TS/React — types, hooks, RTL |
| python-expert _(si stack Python)_ | Serpent | 🐍 | Backend Python — types, pytest |
| docker-expert _(si Docker)_ | Container | 🐋 | Docker, Compose, CI images |

## Conventions

- Langue de communication : {{communication_language}}
- Style de commits : {{commit_style}}  _(ex: Conventional Commits)_
- Branches : {{branch_strategy}}  _(ex: main + feature/*)_
- Toutes les décisions sont loggées dans `decisions-log.md`
- Les transferts inter-agents passent par `handoff-log.md`

## Points de vigilance

<!-- Remplir au fur et à mesure — décisions d'architecture, contraintes connues -->

- [ ] CORS configuré pour les environnements dev / prod
- [ ] Secrets en variables d'environnement (jamais dans le code)
- [ ] Rate limiting sur les routes auth
- [ ] CSP headers configurés
- [ ] Tests d'intégration E2E définis

## Requêtes inter-agents

<!-- Les agents ajoutent ici les requêtes pour d'autres agents -->
<!-- Format: [AGENT_SOURCE→AGENT_CIBLE] description -->
