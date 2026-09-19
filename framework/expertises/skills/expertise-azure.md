<!-- EXPERTISE: expertise-azure — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure Azure — RBAC Entra ID, Terraform/Bicep, resource groups comme frontière de blast-radius, garde-fous réseau et coûts. À utiliser dès qu'un projet référence des ressources Azure (provider `azurerm` en Terraform, fichiers `.bicep`/ARM, `az` CLI) — pas pour AWS (`aws`) ni GCP (`google`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# Azure

Expertise infra activée sur détection d'un provider `azurerm` (Terraform), de fichiers `.bicep`/ARM,
ou d'un usage de `az` CLI dans le projet. Sur Azure, le resource group est la frontière de
blast-radius naturelle : une erreur de scope RBAC ou de policy s'y propage vite.

## Principes

- RBAC via les rôles Entra ID (Azure AD) scopés au resource group ou à la ressource, jamais au
  niveau abonnement complet sauf besoin explicite et documenté.
- Jamais `Owner` par défaut : `Contributor` ou un rôle custom plus étroit couvre la quasi-totalité
  des besoins de provisioning ; `Owner` est réservé à la gestion des accès elle-même.
- Infra as code via Terraform (provider `azurerm`) ou Bicep — jamais de ressource créée à la main
  dans le portail pour un environnement qui doit rester reproductible.
- Resource groups organisés par environnement/cycle de vie (un RG par env, pas un RG fourre-tout
  partagé entre dev/prod) — c'est ce découpage qui rend un `az group delete` sûr ou dangereux.
- Secrets et clés dans Azure Key Vault, jamais en valeur inline dans un template ARM, un fichier
  `.bicep`, une variable Terraform ou un pipeline YAML.
- Azure Policy pour les garde-fous à l'échelle (régions autorisées, SKUs autorisés, tags
  obligatoires) plutôt qu'une convention non appliquée par l'outillage.
- Network Security Groups en moindre exposition : jamais de règle entrante `Any`/`Internet` sur des
  ports autres que 80/443 d'une ressource publique assumée.
- State Terraform distant (backend `azurerm` : storage account + container), jamais local — mêmes
  raisons que pour tout autre provider : perte de données et collisions d'équipe.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — `az group delete` sur un resource group
de production, désactivation de la soft-delete/purge protection d'un Key Vault, attribution d'un
rôle `Owner`/`Contributor` au niveau abonnement, ouverture d'une NSG à `Any`/`Internet` hors 80/443 —
exige l'affichage du scope impacté et une confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf`/`.bicep` et le state existant → `az account show` pour confirmer
l'abonnement et le tenant ciblés → `terraform plan` (ou `az deployment group what-if` en Bicep/ARM)
→ relire le plan/what-if, en particulier les changements de rôle RBAC et de règles réseau →
appliquer (`terraform apply` / `az deployment group create`) → vérifier les tags obligatoires sur
les ressources créées → `cc-verify.sh --stack azure` si le projet expose ce gate.

## Diagnostiquer un incident

Localiser la ressource et la fenêtre temporelle → `az monitor activity-log list
--resource-group <rg> --start-time <horodatage>` pour l'historique des opérations de contrôle →
Log Analytics / Kusto (`az monitor log-analytics query`) pour les logs applicatifs et les métriques
corrélées → `az role assignment list --scope <scope> --all` si le doute porte sur qui a les droits
d'avoir fait l'action → Azure Monitor Alerts pour vérifier si un seuil avait déjà signalé la dérive.

## Dérive de coût

Repérer le service en cause via Azure Cost Management (regroupement par ressource puis par tag) →
si le suspect est du compute, vérifier les VM/App Service plans dimensionnés au-dessus du besoin
réel ou laissés démarrés hors horaires d'usage → si le suspect est du stockage, vérifier les tiers
d'accès (Hot/Cool/Archive) et les règles de cycle de vie → si le suspect est du data transfer,
vérifier qu'aucun flux ne traverse inutilement plusieurs régions.

## Coûts et sécurité

- `az role assignment list --scope <scope>` avant d'ajouter un rôle — vérifier ce qui existe déjà
  plutôt que d'empiler des attributions redondantes.
- Storage accounts : accès blob public désactivé par défaut au niveau compte
  (`allow_blob_public_access = false`), exception explicite et justifiée sinon.
- Azure Pipelines : service connections scopées au resource group qu'elles doivent toucher, jamais
  à l'abonnement entier par confort de configuration.
- Azure Cost Management avec budgets et alertes sur les abonnements actifs, pas seulement consultés
  a posteriori.
- Ressources éphémères (dev/PoC) rattachées à un resource group dédié avec suppression planifiée —
  un RG fourre-tout rend la destruction ciblée impossible sans risque de collatéral.

## Checklist de revue

- Aucun rôle RBAC `Owner`/`Contributor` attribué au niveau abonnement sans justification écrite.
- State Terraform en backend distant (`azurerm` storage account), jamais local.
- Aucune NSG ouverte à `Any`/`Internet` hors 80/443 d'une ressource publique assumée.
- Secrets absents des templates/variables ; présents uniquement dans Key Vault.
- Soft-delete et purge protection actives sur les Key Vault ; tags obligatoires présents.
