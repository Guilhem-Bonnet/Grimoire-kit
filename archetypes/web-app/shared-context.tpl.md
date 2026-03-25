# Contexte Partagé — $project_name

<!-- ARCHETYPE: web-app — Template de shared-context pour applications web.
     Les sections marquées ✏️ sont à compléter par l'utilisateur.
     Les variables $xxx sont auto-substituées par grimoire init. -->

> Ce fichier est chargé par tous les agents au démarrage.
> Il est la source de vérité pour le contexte projet.
> **Remplis les sections marquées ✏️ — c'est la chose la plus utile que tu puisses faire.**

## Projet

- **Nom** : $project_name
- **Description** : ✏️ _à compléter_
- **Type** : $project_type
- **Stack** : $stack_list

## Stack Technique ✏️

<!-- Complète ce tableau avec ta stack réelle. Les agents l'utilisent pour
     donner des recommandations adaptées. -->

| Couche | Technologie | Version | Répertoire |
|--------|-------------|---------|------------|
| Frontend | ✏️ _à compléter_ | — | `src/` |
| Backend | ✏️ _à compléter_ | — | `server/` |
| Base de données | ✏️ _à compléter_ | — | — |
| Auth | ✏️ _à compléter_ | — | — |
| Cache | ✏️ _à compléter_ | — | — |
| Build/Deploy | ✏️ _à compléter_ | — | — |

## Architecture ✏️

<!-- Décris la structure de ton projet en quelques lignes ou colle un tree simplifié. -->

## API ✏️

- **Base URL** : ✏️ _à compléter_ _(ex: http://localhost:8080/api/v1)_
- **Auth** : ✏️ _à compléter_ _(ex: JWT Bearer, session cookie, API key)_
- **Format** : JSON

### Routes principales

| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/health` | Health check |
| ✏️ | ✏️ | ✏️ |

## Environnement local ✏️

```bash
# Démarrer le stack complet
# ex: docker compose up -d && make dev

# Tests
# ex: npm test / pytest / go test ./...
```

## Variables d'environnement

| Variable | Env | Description |
|----------|-----|-------------|
| `DATABASE_URL` | dev/prod | DSN base de données |
| ✏️ | ✏️ | ✏️ |

> ⚠️ Ne jamais commiter les valeurs réelles — utiliser `.env.local` (gitignored)

## Conventions

- Langue de communication : $language
- Toutes les décisions sont loggées dans `decisions-log.md`

## Points de vigilance

<!-- Remplir au fur et à mesure — décisions d'architecture, contraintes connues -->

- [ ] CORS configuré pour les environnements dev / prod
- [ ] Secrets en variables d'environnement (jamais dans le code)
- [ ] Rate limiting sur les routes auth
- [ ] CSP headers configurés
- [ ] Tests d'intégration E2E définis
<!-- Format: [AGENT_SOURCE→AGENT_CIBLE] description -->
