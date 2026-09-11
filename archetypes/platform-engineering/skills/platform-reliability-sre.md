<!-- ARCHETYPE: platform-engineering — Adaptez les {{placeholders}} à votre stack d'observabilité. -->
---
description: "Définir des SLO/SLI, instrumenter l'observabilité, diagnostiquer un incident de fiabilité et rédiger le post-mortem/runbook associé. À utiliser pour la fiabilité en régime établi — pas pour la mise en place initiale de l'observabilité sans incident (voir monitoring-specialist de l'archétype infra-ops) ni pour le déploiement lui-même (skill platform-deploy-release)."
tools: ["read", "edit", "execute"]
---

# Fiabilité & SRE (ex-agent Guardian)

Ancien agent dédié (`reliability-engineer`, faisceau outils identique à `backend-engineer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `backend-engineer` au lieu d'occuper un agent à part.

## Principes

- SLO = contrat de fiabilité. Error budget = droit à l'innovation. Budget consommé = freeze.
- Les 3 piliers (métriques, logs, traces) sont indissociables — jamais diagnostiquer avec un seul.
- Alerter sur les symptômes (latency, error rate), pas les causes (CPU, memory).
- Post-mortems blameless — le système a échoué, pas les personnes.
- Runbooks pour chaque alerte critique.
- Chaos engineering proactif — trouver les failles avant les utilisateurs.

## Garde-fou

Réduction de rétention métriques/logs, suppression d'alertes critiques, modification de SLO : afficher l'impact et demander confirmation.

## SLO/SLI

1. Identifier le service et ses utilisateurs.
2. Définir les SLI : availability (% requêtes non-5xx), latency (p50/p95/p99), throughput, correctness.
3. Fixer les SLO (ex. 99.9 % availability, p99 < 500 ms).
4. Calculer l'error budget : (1 − SLO) × période.
5. Créer le dashboard SLO et les alertes sur burn rate multi-fenêtre.

## Observabilité — 3 piliers

- Métriques : `request_duration_seconds`, `request_total`, `active_connections`, métriques métier.
- Logs structurés JSON : `timestamp`, `level`, `service`, `trace_id`, `span_id`, `message`.
- Traces (OpenTelemetry) : un span par handler et par appel externe, propagation W3C TraceContext.

## Incident Response

1. Triage (5 min) : service, SLI violé, depuis quand, impact, mitigation immédiate possible.
2. Diagnostic : métriques (avant/après), logs corrélés, traces en échec.
3. Remédiation : fix ou rollback (voir skill platform-deploy-release), valider le retour à la normale.
4. Post-mortem blameless : résumé, timeline, root cause, error budget consommé, actions correctives, leçons apprises.

## Alerting

Stratégie multi-fenêtre burn rate : fenêtre 1h à 14,4x → page immédiat ; fenêtre 6h à 6x → alerte haute ; fenêtre 3j à 1x → ticket. Chaque alerte : requête PromQL/LogQL, seuil et durée, sévérité, lien runbook, canal de notification.

## Runbook

Symptômes observables, diagnostic rapide (commandes et ce qu'on y cherche), remédiation (options), escalade si non résolu, prévention.

## Load Testing & Chaos Engineering

Load testing : scénarios (nominal, pic, stress, soak), script k6/Locust, mesure (latency, error rate, throughput), identification du goulot, recommandation de scaling.

Chaos engineering : hypothèse → expérience → observation → conclusion, avec blast radius défini et rollback automatique.
