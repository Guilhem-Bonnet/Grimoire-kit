# Template: secret-rotation

> Rotation sécurisée des secrets, clés et tokens.
> Utilisable par Vault (SOPS/age, API tokens), Forge (credentials services), Phoenix (clés backup).

## Structure

```
{{agent_alias}} effectue la rotation {{secret_type}}.

RAISONNEMENT :
1. INVENTORIER les secrets concernés :
   {{#each inventory_commands}}
   - {{this}}
   {{/each}}
   - Âge du secret actuel : {{current_age}}
   - Politique de rotation : {{rotation_policy}}

2. PRÉ-ROTATION — Vérifications de sécurité :
   - ✅ Backup de la clé/secret actuel(le) existe ?
   - ✅ Accès au système de chiffrement (age/SOPS) fonctionnel ?
   - ✅ Les services dépendants sont identifiés ?
   {{#if dependencies}}
   Services dépendants :
   {{#each dependencies}}
   - {{this}}
   {{/each}}
   {{/if}}

3. ROTATION :
   a) Générer le nouveau secret :
      {{generate_command}}
   b) Mettre à jour les fichiers chiffrés :
      {{#each encrypted_files}}
      - `sops --in-place {{this}}`
      {{/each}}
   c) Déployer la mise à jour :
      {{deploy_command}}

4. VALIDATION post-rotation :
   {{#each validation_commands}}
   - {{this}}
   {{/each}}
   - Vérifier que TOUS les services dépendants fonctionnent encore

5. NETTOYAGE :
   - Révoquer/archiver l'ancien secret si applicable
   - Mettre à jour decisions-log.md avec la date de rotation
   - Mettre à jour le calendrier de prochaine rotation

⚠️ JAMAIS de secret en clair dans les logs, commits, ou outputs.
⚠️ TOUJOURS tester le rollback AVANT de supprimer l'ancien secret.

Résumer : "{{secret_type}} roté(s) : X secrets, Y services redémarrés, prochaine rotation: YYYY-MM-DD".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `agent_alias` | Nom court de l'agent | Vault, Forge |
| `secret_type` | Type de secret | clé age, API token, certificat TLS, mot de passe |
| `inventory_commands` | Commandes d'inventaire | `sops -d secrets.yml \| grep -c "^"`, `age-keygen -y key.txt` |
| `current_age` | Âge du secret actuel | 90 jours, 6 mois |
| `rotation_policy` | Politique de rotation | 90 jours, annuel, sur incident |
| `dependencies` | Services dépendants du secret | Grafana, Traefik, FluxCD |
| `generate_command` | Commande de génération | `age-keygen -o new-key.txt`, `openssl rand -hex 32` |
| `encrypted_files` | Fichiers SOPS à mettre à jour | `ansible/group_vars/all/secrets.yml` |
| `deploy_command` | Commande de déploiement | `ansible-playbook deploy-secrets.yml` |
| `validation_commands` | Commandes de validation | `curl -sf https://service/health`, `sops -d file.yml > /dev/null` |

## Prompts utilisant ce template

| Prompt ID | Agent | secret_type | contexte |
|-----------|-------|-------------|----------|
| secrets-management (rotate) | Vault | Clés age/SOPS | Rotation chiffrement |
| secrets-management (tokens) | Vault | API tokens | Grafana, Discord, GitHub |
| terraform-ops (creds) | Forge | Provider credentials | Proxmox API token |
| backup-audit (keys) | Phoenix | Clés de chiffrement backup | age key pour vzdump |
