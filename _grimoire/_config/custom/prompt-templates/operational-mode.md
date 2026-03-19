# Template: operational-mode

> Pattern IVEV — Identifier → Vérifier → Exécuter → Valider
> Utilisé par 20 prompts (39%) chez Forge, Vault, Flow, Hawk, Helm, Phoenix.

## Structure

```
{{agent_alias}} entre en mode {{domain}}.

RAISONNEMENT :
1. IDENTIFIER : quel {{target_object}} ? Demander si non spécifié.
2. VÉRIFIER : lire {{config_paths}} — état actuel, dépendances, risques.
3. EXÉCUTER : {{action_description}}.
4. VALIDER : {{validation_commands}}.

{{#if destructive}}
⚠️ {{destructive_action}} → afficher les {{resource_type}} impactées avant exécution.
Demander confirmation explicite pour : {{dangerous_operations}}.
{{/if}}

{{#if inter_agent}}
VIA INTER-AGENT : {{inter_agent_refs}}
{{/if}}

Résumer : "X ok, Y changed, Z failed".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `agent_alias` | Nom court de l'agent | Forge, Hawk |
| `domain` | Domaine d'opération | Terraform, PromQL |
| `target_object` | Objet cible | resource, dashboard, rule |
| `config_paths` | Fichiers à consulter | `terraform/`, `ansible/roles/` |
| `action_description` | Ce que fait l'agent | écrire/modifier les fichiers |
| `validation_commands` | Commandes de validation | `terraform plan`, `promtool check rules` |
| `destructive` | Action destructive ? (bool) | true/false |
| `destructive_action` | Label de l'action dangereuse | terraform destroy |
| `resource_type` | Type de ressource impactée | VMs, containers |
| `dangerous_operations` | Opérations nécessitant confirmation | destroy, delete, purge |
| `inter_agent_refs` | Références inter-agents | [Forge→Hawk] pour alerting |

## Prompts utilisant ce template

| Prompt ID | Agent | domain | target_object |
|-----------|-------|--------|--------------|
| terraform-ops | Forge | Terraform | resource |
| ansible-ops | Forge | Ansible | role/playbook |
| docker-ops | Forge | Docker | stack/container |
| monitoring-ops | Forge | Monitoring | config |
| secrets-management | Vault | Secrets | secret/key |
| github-actions | Flow | GitHub Actions | workflow |
| taskfile-ops | Flow | Taskfile | target |
| scripts-automation | Flow | Scripts | script |
| promql-ops | Hawk | PromQL | rule |
| grafana-ops | Hawk | Grafana | dashboard |
| loki-ops | Hawk | Loki | pipeline |
| alertmanager-ops | Hawk | Alertmanager | route/receiver |
| blackbox-ops | Hawk | Blackbox | probe |
| workload-ops | Helm | K8s Workloads | manifest |
| fluxcd-ops | Helm | FluxCD | kustomization |
| longhorn-ops | Helm | Longhorn | volume |
| gpu-ops | Helm | GPU | device plugin |
| network-ops | Helm | Network | ingress/service |
| snapshot-ops | Phoenix | Snapshots | snapshot |
| longhorn-backup | Phoenix | Longhorn | backup schedule |
