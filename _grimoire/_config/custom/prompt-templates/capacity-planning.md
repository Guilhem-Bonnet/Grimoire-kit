# Template: capacity-planning

> Analyse de l'utilisation des ressources et planification de capacité.
> Utilisable par Hawk (métriques Prometheus), Forge (Proxmox/LXC), Helm (K3s), Phoenix (stockage).

## Structure

```
{{agent_alias}} analyse la capacité {{resource_domain}}.

RAISONNEMENT :
1. COLLECTER les métriques actuelles :
   {{#each metric_queries}}
   - {{this}}
   {{/each}}

2. ANALYSER par ressource :
   | Ressource | Utilisé | Total | % | Tendance 30j |
   |-----------|---------|-------|---|-------------|
   {{#each resources}}
   | {{this.name}} | {{this.used}} | {{this.total}} | {{this.pct}} | {{this.trend}} |
   {{/each}}

3. SEUILS d'alerte :
   - 🟢 < 60% : OK
   - 🟡 60-80% : Surveiller
   - 🔴 > 80% : Action requise
   - 🚨 > 90% : Critique — planifier upgrade immédiat

4. PROJECTIONS :
   - À consommation constante, quand atteint-on 80% ?
   - {{#if growth_factor}}Facteur de croissance : {{growth_factor}}{{/if}}
   - Services les plus gourmands (top 5)

5. RECOMMANDATIONS :
   {{#if rightsizing}}
   - Rightsizing : containers/VMs sur-provisionnés ou sous-provisionnés
   - Limites Docker/K8s à ajuster
   {{/if}}
   - Upgrades matériels nécessaires (RAM, disque)
   - Optimisations possibles (rétention, compression, archivage)

Résumer : "{{resource_domain}} : X/Y utilisé (Z%), projection 80% dans N jours, actions: [...]".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `agent_alias` | Nom court de l'agent | Hawk, Forge |
| `resource_domain` | Domaine de ressource | CPU, RAM, Stockage, IOPS |
| `metric_queries` | Requêtes Prometheus/commandes | `node_memory_MemAvailable_bytes`, `df -h` |
| `resources` | Liste de ressources à analyser | LXC containers, PVs, nodes |
| `growth_factor` | Facteur de croissance estimé | 1.1x/mois pour les logs |
| `rightsizing` | Inclure analyse rightsizing (bool) | true/false |

## Prompts utilisant ce template

| Prompt ID | Agent | resource_domain | contexte |
|-----------|-------|-----------------|----------|
| monitoring-ops (capacity) | Hawk | CPU/RAM/Disk | Métriques Prometheus |
| terraform-ops (sizing) | Forge | LXC resources | Proxmox sizing |
| k8s-ops (resources) | Helm | Pod resources | Requests/Limits K3s |
| backup-audit (storage) | Phoenix | Backup storage | Rétention, espace disque |
