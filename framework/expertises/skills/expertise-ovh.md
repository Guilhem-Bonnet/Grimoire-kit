<!-- EXPERTISE: expertise-ovh — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure OVHcloud Public Cloud — modèle application key/secret/consumer key, Terraform provider `ovh`, IAM OVH, garde-fous réseau et facturation par datacenter. À utiliser dès qu'un projet référence des ressources OVH (provider `ovh` en Terraform, appels à l'API OVH, `OVH_ENDPOINT`/`OVH_APPLICATION_KEY`) — pas pour AWS (`aws`), Hetzner (`hcloud`), Scaleway (`scaleway`) ni Cloudflare (`cloudflare`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# OVHcloud

Expertise infra activée sur détection d'un provider `ovh` (Terraform) ou d'appels à l'API OVH
(clés `OVH_APPLICATION_KEY`/`OVH_APPLICATION_SECRET`/`OVH_CONSUMER_KEY`) dans le projet. Le modèle
d'authentification OVH (application key + secret + consumer key, avec des règles d'accès explicites
par consumer key) est structurellement différent des rôles IAM à la AWS/GCP — une clé mal scopée
n'expire pas toute seule et reste valide tant qu'elle n'est pas révoquée à la main.

## Principes

- Modèle application key/secret/consumer key : chaque consumer key porte des règles d'accès
  (méthode HTTP + chemin, ex. `GET /cloud/project/*`) explicites — jamais une consumer key à accès
  large (`/*`) pour une automatisation qui n'en a besoin que sur un sous-ensemble de routes.
- IAM OVH (policies) plus récent et plus grossier que le IAM AWS-style : vérifier la portée réelle
  d'une policy avant de l'attacher, la granularité par action fine n'est pas toujours disponible.
- Infra as code via Terraform (provider `ovh`) pour les ressources Public Cloud (instances,
  réseaux privés, volumes) — pas de création manuelle dans le Manager pour un environnement qui
  doit rester reproductible.
- Snapshot ou backup automatisé sur les instances Public Cloud avant toute modification destructrice
  (resize, changement d'image, suppression) — OVH ne restaure pas une instance détruite sans
  snapshot préalable.
- Security groups par projet Public Cloud (`ovh_cloud_project_network_private` + règles associées)
  en moindre exposition — pas de règle entrante large sur un réseau privé partagé entre plusieurs
  services.
- Facturation et quotas segmentés par région/datacenter OVH (GRA, SBG, BHS, WAW...) : un quota
  atteint dans une région ne se reporte pas automatiquement sur une autre.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression d'une instance Public Cloud
ou d'un volume sans snapshot récent, révocation/élargissement d'une consumer key existante,
ouverture d'un security group à `0.0.0.0/0` sur autre chose qu'un load balancer public assumé —
exige l'affichage des ressources impactées et une confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf` avec le provider `ovh` et le state existant → vérifier l'identité active
(`GET /me` via l'API OVH signée ou `ovh-cli me`) et le projet Public Cloud ciblé (`service_name`) →
`terraform plan -out=plan.tfplan` → relire le plan en particulier les changements sur les security
groups, les consumer keys et le réseau privé → appliquer (`terraform apply plan.tfplan`) → vérifier
dans le Manager OVH que la ressource créée est bien rattachée au bon projet/datacenter → confirmer
qu'aucun quota région n'est dépassé.

## Diagnostiquer un incident

Identifier le projet Public Cloud et la région concernés → consulter les logs et métriques via le
Manager OVH (section Public Cloud > Monitoring) ou l'API (`GET /cloud/project/{serviceName}/instance/{id}`
pour l'état) → vérifier les alertes de facturation/quota dans le Manager (souvent la cause d'un
provisioning qui échoue silencieusement) → si le doute porte sur les permissions, relire les règles
de la consumer key utilisée avant de la réémettre à l'aveugle → pour le réseau, vérifier les règles
du security group et la configuration du réseau privé (vRack) associé à l'instance.

## Coûts et sécurité

- Application keys scopées et rotées : une clé avec des règles d'accès larges (`/*`) laissée sans
  rotation est le risque de sécurité le plus fréquent sur OVH.
- Quotas et facturation vérifiés par datacenter/région individuellement — ne pas supposer qu'un
  quota global couvre toutes les régions.
- Snapshots Public Cloud à durée de vie bornée (les snapshots facturés au stockage s'accumulent
  silencieusement si personne ne les purge après une campagne de tests).
- Propagation DNS OVH à anticiper : un changement de zone DNS peut prendre plusieurs minutes à
  plusieurs heures à se propager, ne jamais supposer une propagation immédiate lors d'un diagnostic.
- Security groups par projet plutôt que des règles ad hoc sur chaque instance, pour garder une
  politique réseau lisible et auditable.

## Checklist de revue

- Aucune consumer key avec des règles d'accès larges (`/*`) non justifiées.
- Snapshot ou backup récent avant toute opération destructrice sur une instance Public Cloud.
- Aucun security group ouvert à `0.0.0.0/0` hors un load balancer public assumé.
- Quotas et facturation vérifiés pour chaque région/datacenter concerné par le changement.
- Ressources Public Cloud provisionnées via Terraform, jamais créées à la main dans le Manager.
