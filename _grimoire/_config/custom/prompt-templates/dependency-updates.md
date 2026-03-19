# Template: dependency-updates

> Audit et mise à jour des dépendances du projet.
> Utilisable par Forge (Terraform providers, Docker images), Flow (GitHub Actions), Helm (charts Helm, K3s).

## Structure

```
{{agent_alias}} audite les dépendances {{dependency_domain}}.

RAISONNEMENT :
1. INVENTORIER : lister toutes les {{dependency_type}} avec leurs versions actuelles.
   {{#each inventory_commands}}
   - {{this}}
   {{/each}}
2. COMPARER : pour chaque dépendance, identifier la dernière version stable.
   - Sources : {{version_sources}}
   - Ignorer les versions RC/alpha/beta sauf demande explicite.
3. ÉVALUER les risques :
   - Breaking changes ? Consulter le changelog.
   - Dépendances croisées impactées ?
   - {{#if critical_deps}}Dépendances critiques (ne PAS mettre à jour sans validation) : {{critical_deps}}{{/if}}
4. APPLIQUER : mettre à jour les fichiers de configuration.
   {{#each config_files}}
   - {{this}}
   {{/each}}
5. VALIDER :
   {{#each validation_commands}}
   - {{this}}
   {{/each}}

⚠️ Mise à jour majeure (X.0.0) → afficher le changelog résumé avant application.

Résumer : "X à jour, Y mis à jour, Z ignorés (raison)".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `agent_alias` | Nom court de l'agent | Forge, Flow, Helm |
| `dependency_domain` | Domaine de dépendances | Terraform, Docker, GitHub Actions |
| `dependency_type` | Type de dépendance | provider, image, action, chart |
| `inventory_commands` | Commandes d'inventaire | `grep -r "required_providers"`, `grep -r "image:"` |
| `version_sources` | Sources de versions | registry.terraform.io, hub.docker.com, github releases |
| `critical_deps` | Dépendances à ne pas MAJ auto | proxmox provider, K3s, Longhorn |
| `config_files` | Fichiers à modifier | `terraform/*.tf`, `docker-compose*.yml` |
| `validation_commands` | Commandes de validation | `terraform init -upgrade`, `docker compose config` |

## Prompts utilisant ce template

| Prompt ID | Agent | dependency_domain | dependency_type |
|-----------|-------|-------------------|-----------------|
| terraform-ops (upgrade) | Forge | Terraform | provider/module |
| docker-ops (images) | Forge | Docker | image tag |
| github-actions (versions) | Flow | GitHub Actions | action version |
| k8s-ops (charts) | Helm | Kubernetes | chart/K3s version |
