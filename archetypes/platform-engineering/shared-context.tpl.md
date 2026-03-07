# Contexte Partagé — {{project_name}}

<!-- ARCHETYPE: platform-engineering — Template de shared-context pour plateformes.
     Adaptez les sections à votre architecture et stack.
     Les agents déployés sont: Archie (architecture), Stack (backend),
     Guardian (SRE), Convoy (deploy). Compatible avec les agents stack/ (Terra, Kube, etc.) -->

> Ce fichier est chargé par tous les agents au démarrage.
> Il est la source de vérité pour le contexte projet.

## Projet

- **Nom** : {{project_name}}
- **Description** : {{project_description}}
- **Dépôt** : {{repo_url}}
- **Type** : Platform Engineering — {{platform_type}}  _(ex: microservices, modular monolith, event-driven)_

## Stack Technique

| Couche | Technologie | Version | Répertoire |
|--------|-------------|---------|------------|
| Backend | {{backend_tech}} | {{backend_version}} | `{{backend_dir}}` |
| Base de données | {{db_tech}} | {{db_version}} | — |
| Messaging | {{messaging_tech}} | — | — |
| Container Runtime | {{container_runtime}} | — | — |
| Orchestration | {{orchestration_tech}} | — | — |
| IaC | {{iac_tech}} | — | `{{infra_dir}}` |
| Observabilité | {{observability_stack}} | — | — |
| CI/CD | {{cicd_platform}} | — | `.github/workflows/` |

## Architecture — Vue d'Ensemble

<!-- Référence complète : docs/architecture.md -->

```mermaid
graph TB
    Client[Clients] --> GW[API Gateway / Ingress]
    GW --> SVC1[{{service_1}}]
    GW --> SVC2[{{service_2}}]
    SVC1 --> DB1[({{db_1}})]
    SVC1 --> MQ{{{message_broker}}}
    MQ --> SVC2
    SVC2 --> DB2[({{db_2}})]
    SVC1 --> OBS[Observabilité]
    SVC2 --> OBS
```

### Services

| Service | Responsabilité | Port | SLO Availability | SLO Latency p99 |
|---------|---------------|------|------------------|-----------------|
| {{service_1}} | {{service_1_desc}} | {{port_1}} | {{slo_1}} | {{latency_1}} |
| {{service_2}} | {{service_2_desc}} | {{port_2}} | {{slo_2}} | {{latency_2}} |

### Flux de Données

| Source | Destination | Type | Protocole | Contrat |
|--------|------------|------|-----------|---------|
| {{service_1}} | {{service_2}} | async | {{messaging_tech}} | events/{{event_schema}} |
| Client | {{service_1}} | sync | REST/gRPC | openapi/{{api_spec}} |

## Infrastructure

| Environnement | Type | Détails |
|--------------|------|---------|
| dev | local | Docker Compose |
| staging | {{staging_infra}} | {{staging_details}} |
| production | {{prod_infra}} | {{prod_details}} |

**Réseau** : {{network_cidr}}

## Observabilité

| Pilier | Outil | Endpoint |
|--------|-------|----------|
| Métriques | {{metrics_tool}} | {{metrics_endpoint}} |
| Logs | {{logs_tool}} | {{logs_endpoint}} |
| Traces | {{traces_tool}} | {{traces_endpoint}} |
| Dashboards | {{dashboard_tool}} | {{dashboard_url}} |
| Alerting | {{alerting_tool}} | {{alerting_channel}} |

## Déploiement

- **Stratégie par défaut** : {{deploy_strategy}} _(rolling / canary / blue-green)_
- **GitOps** : {{gitops_tool}} _(FluxCD / ArgoCD / GitHub Actions)_
- **Rollback** : {{rollback_method}}
- **Secrets** : {{secrets_manager}} _(SOPS / Vault / AWS Secrets Manager)_

## Équipe d'Agents Platform

| Agent | Nom | Icône | Domaine |
|-------|-----|-------|---------|
| project-navigator | Atlas | 🗺️ | Navigation & Mémoire projet |
| agent-optimizer | Sentinel | 🔍 | Qualité & Optimisation agents |
| memory-keeper | Mnemo | 🧠 | Mémoire & Qualité connaissances |
| platform-architect | Archie | 🏛️ | Architecture système, DDD, patterns |
| backend-engineer | Stack | ⚙️ | Backend, APIs, event sourcing |
| reliability-engineer | Guardian | 🛡️ | SRE, SLO/SLI, observabilité |
| deploy-orchestrator | Convoy | 🚀 | Déploiement, GitOps, pipelines |

## Architecture Decision Records (ADRs)

<!-- Liste des ADRs actifs — source de vérité architecturale -->

| ADR | Titre | Statut | Date |
|-----|-------|--------|------|
| — | _Aucun ADR pour l'instant_ | — | — |

## Conventions

- Langue de communication : {{communication_language}}
- Toutes les décisions architecturales sont loggées en ADR dans `docs/adr/`
- Source de vérité architecture : `docs/architecture.md`
- Les transferts inter-agents passent par `handoff-log.md`

## Requêtes inter-agents

<!-- Les agents ajoutent ici les requêtes pour d'autres agents -->
<!-- Format: [AGENT_SOURCE→AGENT_CIBLE] description de la requête -->
