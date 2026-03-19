<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

name: incident-response
description: Workflow partagé de réponse aux incidents et génération de post-mortems structurés
triggers:
 - alerte critique résolue
 - incident infra détecté
 - incident sécurité détecté

<!--
TEMPLATE — Remplacer les {{placeholders}} avant utilisation :
 {{ops_agent}} - Agent infrastructure/provisioning (ex: Forge)
 {{security_agent}} - Agent sécurité (ex: Vault)
 {{monitoring_agent}} - Agent observabilité (ex: Hawk)
 {{k8s_agent}} - Agent kubernetes/orchestration (ex: Helm)
 {{backup_agent}} - Agent backup/DR (ex: Phoenix)
 {{cicd_agent}} - Agent CI/CD (ex: Flow)
 {{debug_agent}} - Agent debugging système (ex: Probe)
 {{user_name}} - Nom de l'utilisateur principal
-->

# <img src="../../docs/assets/icons/shield-pulse.svg" width="32" height="32" alt=""> Workflow Incident Response

**But :** Fournir un processus structuré de diagnostic et de post-mortem pour tout incident infra ou sécurité. Ce workflow est partagé entre tous les agents opérationnels.

**Qui peut le déclencher :** Tout agent ({{ops_agent}}, {{security_agent}}, {{monitoring_agent}}, {{k8s_agent}}, {{backup_agent}}, {{cicd_agent}}) ou manuellement par l'utilisateur.

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> VARIANTES

| Variante | Lead | Support | Quand |
|----------|------|---------|-------|
| **Incident Infra** | {{ops_agent}} ou {{k8s_agent}} (selon périmètre LXC/K3s) | {{monitoring_agent}} (métriques), {{cicd_agent}} (CI/CD) | Container down, service inaccessible, performance dégradée |
| **Incident Sécurité** | {{security_agent}} | {{ops_agent}} (isolation), {{monitoring_agent}} (logs), {{k8s_agent}} (K8s RBAC) | Tentative d'intrusion, secret exposé, container compromis |
| **Incident Données** | {{backup_agent}} | {{k8s_agent}} (Longhorn), {{ops_agent}} (Proxmox) | Perte de données, corruption, backup échoué |

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> ÉTAPE 1 : DÉTECTION & TRIAGE (5 min max)

### 1.1 Contexte initial

Recueillir immédiatement :

```
- Quoi : quel service/composant est impacté ?
- Quand : timestamp de détection (alerte Hawk ou observation manuelle)
- Impact : utilisateurs/services affectés ?
- Sévérité : CRITIQUE (service down) / HAUTE (dégradé) / MOYENNE (risque) / BASSE (cosmétique)
```

### 1.2 Actions immédiates

| Sévérité | Action |
|----------|--------|
| CRITIQUE | Contenir immédiatement (isoler le composant), notifier {{user_name}} si hors-heures |
| HAUTE | Diagnostiquer en priorité, documenter au fil de l'eau |
| MOYENNE | Planifier dans la session courante |
| BASSE | Logger pour traitement ultérieur |

### 1.3 Assignation du lead

- Composant LXC (Terraform/Ansible/Docker) → **{{ops_agent}}** lead
- Composant K3s (pods/FluxCD/Longhorn) → **{{k8s_agent}}** lead
- Composant sécurité (secrets, intrusion, TLS) → **{{security_agent}}** lead
- Composant monitoring (alertes fausses, TSDB) → **{{monitoring_agent}}** lead
- Composant données (backup échoué, corruption) → **{{backup_agent}}** lead

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/microscope.svg" width="28" height="28" alt=""> ÉTAPE 2 : DIAGNOSTIC SYSTÉMATIQUE

### 2.1 Arbre de diagnostic

```
1. Le service répond-il ? (curl/probe)
   ├─ NON → Le container/pod tourne-t-il ?
   │   ├─ NON → Pourquoi ? (OOM, crash, scheduling, disk full)
   │   └─ OUI → Port/network issue ? (iptables, NetworkPolicy, DNS)
   └─ OUI mais lent/erreurs → Métriques anormales ?
       ├─ CPU/RAM saturé → Qui consomme ? (top, kubectl top)
       ├─ Disk I/O → Quel volume ? (iostat, df)
       └─ Erreurs applicatives → Logs ? (Loki, docker logs, kubectl logs)
```

### 2.2 Collecte de preuves

L'agent lead doit collecter et documenter :

```markdown
## Preuves collectées
- [ ] Logs du service (30 dernières minutes)
- [ ] Métriques Prometheus autour du timestamp incident
- [ ] Events K8s (si cluster) ou docker events
- [ ] État des resources (CPU, RAM, disk)
- [ ] Changements récents (deploys, commits, configs)
```

### 2.3 Actions de remédiation

1. Identifier la cause root
2. Appliquer le fix (direct, pas de proposition)
3. Valider : service restored, métriques normales
4. Si fix temporaire : documenter la dette technique

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> ÉTAPE 3 : POST-MORTEM

### 3.1 Template post-mortem

Générer automatiquement dans `{project-root}/_grimoire-output/implementation-artifacts/postmortems/` :

```markdown
---
date: YYYY-MM-DD
severity: CRITIQUE|HAUTE|MOYENNE
duration: Xh Xmin (détection → résolution)
lead: [agent name]
services_impacted: [liste]
---

# Post-Mortem : [Titre court]

## Timeline
| Heure | Événement |
|-------|-----------|
| HH:MM | Détection : [comment] |
| HH:MM | Diagnostic : [findings] |
| HH:MM | Remédiation : [action] |
| HH:MM | Validation : [résultat] |

## Cause root
[Description technique de la cause fondamentale]

## Impact
- Services affectés : [liste]
- Durée d'indisponibilité : [durée]
- Données perdues : [aucune / description]

## Impact sécurité
[OBLIGATOIRE — même si "Aucun impact sécurité identifié"]

## Remédiation appliquée
[Ce qui a été fait pour résoudre]

## Actions préventives
- [ ] [Action 1 — assignée à agent]
- [ ] [Action 2 — assignée à agent]

## Leçons apprises
- [Learning 1]
- [Learning 2]

## Métriques
- MTTD (Mean Time To Detect) : [durée] — Cible : < 5min
- MTTR (Mean Time To Repair) : [durée] — Cible : < 2h
```

### 3.2 Actions post-incident

1. Écrire le post-mortem dans le dossier dédié
2. Ajouter les learnings dans `_grimoire/_memory/agent-learnings/` (fichier de l'agent lead)
3. Mettre à jour `_grimoire/_memory/shared-context.md` si l'architecture a changé
4. Créer des requêtes inter-agents pour les actions préventives
5. Si NFR violé (MTTD > 5min, RTO > 2h) → ajouter une alerte Hawk pour prévenir la récurrence

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> NOTES DE MODÉRATION

- Ce workflow ne remplace pas les compétences des agents — il structure le processus
- L'agent lead a autorité sur les décisions techniques pendant l'incident
- En cas de désaccord entre agents, escalader à l'utilisateur
- Le post-mortem est blameless — focus sur le processus, pas les individus
