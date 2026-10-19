<!-- EXPERTISE: expertise-gcp — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure GCP — IAM par rôles scopés, Terraform, service accounts et Workload Identity Federation, garde-fous réseau et coûts. À utiliser dès qu'un projet référence des ressources GCP (provider `google` en Terraform, `gcloud` CLI, service accounts `.json`) — pas pour AWS (`aws`) ni Azure (`azurerm`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# GCP (Google Cloud Platform)

Expertise infra activée sur détection d'un provider `google` (Terraform), d'un usage de `gcloud`
CLI, ou de fichiers de clé de service account dans le projet. Sur GCP, le projet GCP lui-même est
la frontière de blast-radius : IAM et budget s'y raisonnent projet par projet.

## Principes

- IAM par rôles prédéfinis ou custom, scopés au projet ou à la ressource — jamais `roles/owner`
  attribué à un service account ou à un compte utilisé pour l'automatisation.
- Infra as code via Terraform (provider `google`) — jamais de ressource créée à la main dans la
  console pour un environnement qui doit rester reproductible.
- Service accounts avec scopes minimaux et sans clé JSON exportée dès que Workload Identity
  Federation est disponible (CI/CD, workloads externes) — une clé JSON long-lived est un secret
  permanent qui ne tourne pas de lui-même.
- Compte de service par défaut de Compute Engine jamais utilisé tel quel : il porte historiquement
  un rôle `Editor` large, à retirer ou remplacer par un service account dédié et scopé.
- VPC firewall rules en moindre exposition : jamais de règle `0.0.0.0/0` en ingress sauf sur les
  ports 80/443 d'une ressource publique assumée (load balancer, ingress).
- Cloud Audit Logs (Admin Activity + Data Access) actifs sur le projet — sans eux, un incident IAM
  ou réseau ne laisse aucune trace exploitable après coup.
- Budgets et alertes Cloud Billing configurés dès la création du projet, pas ajoutés après une
  facture surprise.
- State Terraform distant (backend `gcs`), jamais local — mêmes raisons que pour tout autre
  provider : perte de données et collisions d'équipe.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression d'un bucket Cloud Storage ou
d'une instance Cloud SQL sans sauvegarde préalable, attribution de `allUsers`/`allAuthenticatedUsers`
sur un bucket, ouverture d'une règle firewall à `0.0.0.0/0` hors 80/443 d'une ressource publique,
attribution de `roles/owner` à un service account — exige l'affichage des ressources/IAM bindings
impactés et une confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf` et le state existant → `gcloud auth list` et `gcloud config get-value
project` pour confirmer le compte et le projet ciblés → `terraform plan` → relire le plan, en
particulier les changements d'IAM bindings et de règles firewall → `terraform apply` → vérifier les
labels de coût sur les ressources créées → `cc-verify.sh --stack gcp` si le projet expose ce gate.

## Diagnostiquer un incident

Localiser le service et la fenêtre temporelle → `gcloud logging read
"resource.type=<type> AND timestamp>=<horodatage>" --limit=100` pour les logs structurés →
Cloud Monitoring pour corréler charge, latence et erreurs → `gcloud projects get-iam-policy
<project-id>` si le doute porte sur qui a les droits d'avoir fait l'action → Cloud Audit Logs (via
Logs Explorer, filtre `logName:"cloudaudit.googleapis.com"`) pour retracer l'appel d'API précis.

## Dérive de coût

Repérer le service en cause via le rapport de facturation Cloud Billing (regroupement par SKU puis
par label) → si le suspect est du compute, vérifier les instances Compute Engine sans politique
d'arrêt automatique hors horaires d'usage → si le suspect est BigQuery, vérifier les requêtes les
plus coûteuses via `INFORMATION_SCHEMA.JOBS` et l'absence de limite de bytes facturés → si le
suspect est du réseau, vérifier qu'aucun flux ne traverse inutilement plusieurs régions.

## Coûts et sécurité

- `gcloud projects get-iam-policy <project-id>` avant d'ajouter un rôle — vérifier ce qui existe
  déjà plutôt que d'empiler des bindings redondants.
- Cloud Storage : accès public (`allUsers`/`allAuthenticatedUsers`) interdit par défaut via
  l'Org Policy `constraints/storage.publicAccessPrevention`, exception explicite sinon.
- BigQuery : quotas et limites de coût par requête (`maximum_bytes_billed`) sur les jobs
  automatisés — une requête sans limite peut scanner des téraoctets par erreur de jointure.
- APIs inutilisées désactivées (`gcloud services disable`) — chaque API activée est une surface
  d'attaque et une source potentielle de facturation inattendue.
- `gcloud billing budgets list` pour vérifier qu'un budget avec alerte existe sur le compte de
  facturation lié au projet, pas seulement consulté a posteriori.

## Checklist de revue

- Aucun service account avec `roles/owner` ou rôle `Editor` par défaut laissé attaché.
- State Terraform en backend distant (`gcs`), jamais local.
- Aucune règle firewall ouverte à `0.0.0.0/0` hors 80/443 d'une ressource publique assumée.
- Aucun bucket avec `allUsers`/`allAuthenticatedUsers` sans justification écrite.
- Cloud Audit Logs actifs sur le projet ; budget et alerte de facturation configurés.
