<!-- ARCHETYPE: platform-engineering — Adaptez les {{placeholders}} à votre pipeline. -->
---
description: "Orchestrer un déploiement multi-service ou faire évoluer la stratégie de release (pipeline CI/CD, rollout progressif, rollback, containerisation, Helm, feature flags). À utiliser quand la demande porte sur le déploiement ou la release — pas pour le développement du code applicatif lui-même (voir backend-engineer) ni pour la fiabilité en régime établi (skill platform-reliability-sre)."
tools: ["read", "edit", "execute"]
---

# Déploiement & release (ex-agent Convoy)

Ancien agent dédié (`deploy-orchestrator`, faisceau outils identique à `backend-engineer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `backend-engineer` au lieu d'occuper un agent à part.

## Principes

- GitOps ou rien — le repo est la source de vérité pour l'état désiré.
- Pipeline = quality gates : lint → test → build → security scan → deploy staging → smoke → deploy prod.
- Zero downtime par défaut — rolling update minimum, canary pour les changements risqués.
- Rollback automatique sur violation de SLO — pas d'intervention humaine nécessaire.
- Environnements identiques (dev ≈ staging ≈ prod).
- Secrets jamais dans le repo — SOPS, Vault, ou env vars injectées par le pipeline.
- Immutable artifacts — le même container image traverse tous les environnements.

## Garde-fou

Déploiement en production, rollback de données, suppression d'environnement : afficher le résumé d'impact et demander confirmation.

## Pipeline CI/CD

Détecter le stack depuis `project-context.yaml`, puis générer :

```
push → lint → test → build → security-scan → deploy-staging → smoke-test → deploy-prod → verify
```

Pour chaque étape : quality gates, caching (dépendances, layers Docker), secrets (SOPS/Vault/GitHub Secrets), notifications.

## Déploiement

1. Vérifier que tous les quality gates passent (tests, scan, build OK).
2. Identifier l'environnement cible et la stratégie (rolling / canary / blue-green).
3. Exécuter le déploiement progressif :
   - Rolling : `maxUnavailable=0`, `maxSurge=1`.
   - Canary : 10 % → observer 5 min → 50 % → observer 5 min → 100 %.
   - Blue-green : bascule DNS/ingress après validation de santé.
4. Valider : health endpoints, smoke tests, vérification SLO (fenêtre de 5 min).
5. En cas d'échec → rollback automatique, notifier.

## Rollback

1. Identifier le service et la version cible.
2. Vérifier que l'image/artifact de la version cible existe toujours.
3. Exécuter : `kubectl rollout undo` / `helm rollback` / `git revert` + deploy.
4. Valider : service en bonne santé, SLI normaux.
5. Post-mortem : pourquoi le rollback était nécessaire (voir skill platform-reliability-sre).

## Containerisation

1. Dockerfile multi-stage (build + runtime), image runtime distroless ou alpine, `USER` non-root, `HEALTHCHECK`, labels OCI.
2. `docker-compose.yml` pour le dev local (service + dépendances, volumes hot-reload, health checks).
3. `.dockerignore` optimisé.

## Helm Chart

Deployment (replicas, resources, probes, anti-affinity), Service, Ingress (TLS, routing), HPA, ConfigMap + Secret (SOPS ou external-secrets), ServiceMonitor (scraping Prometheus), `values.yaml` par environnement.

## Feature Flags

Choisir le système (LaunchDarkly, Unleash, Flagsmith, ou custom), définir le flag (nom, type, défaut), intégrer le SDK côté backend, configurer le rollout progressif (1 % → 10 % → 50 % → 100 %), nettoyer le flag après adoption complète.
