# Contexte Partagé — $project_name

<!-- ARCHETYPE: platform-engineering — Template de shared-context pour plateformes.
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

| Couche | Technologie | Version | Répertoire |
|--------|-------------|---------|------------|
| Backend | ✏️ _à compléter_ | — | `src/` |
| Base de données | ✏️ _à compléter_ | — | — |
| Messaging | ✏️ _à compléter_ | — | — |
| Container Runtime | ✏️ _à compléter_ | — | — |
| Orchestration | ✏️ _à compléter_ | — | — |
| IaC | ✏️ _à compléter_ | — | `infra/` |
| Observabilité | ✏️ _à compléter_ | — | — |
| CI/CD | ✏️ _à compléter_ | — | `.github/workflows/` |

## Architecture — Vue d'Ensemble ✏️

<!-- Décris ton architecture ici — microservices, monolith modulaire, event-driven, etc. -->
<!-- Référence complète : docs/architecture.md -->

### Services ✏️

| Service | Responsabilité | Port | SLO Availability |
|---------|---------------|------|------------------|
| ✏️ | ✏️ | ✏️ | ✏️ |

## Infrastructure ✏️

| Environnement | Type | Détails |
|--------------|------|---------|
| dev | local | Docker Compose |
| staging | ✏️ | ✏️ |
| production | ✏️ | ✏️ |

## Observabilité ✏️

| Pilier | Outil | Endpoint |
|--------|-------|----------|
| Métriques | ✏️ | ✏️ |
| Logs | ✏️ | ✏️ |
| Traces | ✏️ | ✏️ |
| Dashboards | ✏️ | ✏️ |

## Déploiement ✏️

- **Stratégie par défaut** : ✏️ _(rolling / canary / blue-green)_
- **GitOps** : ✏️ _(FluxCD / ArgoCD / GitHub Actions)_
- **Secrets** : ✏️ _(SOPS / Vault / AWS Secrets Manager)_

## Architecture Decision Records (ADRs)

<!-- Liste des ADRs actifs — source de vérité architecturale -->

| ADR | Titre | Statut | Date |
|-----|-------|--------|------|
| — | _Aucun ADR pour l'instant_ | — | — |

## Conventions

- Langue de communication : $language
- Toutes les décisions architecturales sont loggées en ADR dans `docs/adr/`
- Source de vérité architecture : `docs/architecture.md`
