<!-- EXPERTISE: expertise-scaleway — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure Scaleway — access key/secret key scopés par projet, Terraform provider `scaleway`, choix délibéré Instances vs Kapsule, IAM Object Storage. À utiliser dès qu'un projet référence des ressources Scaleway (provider `scaleway` en Terraform, CLI `scw`, `SCW_ACCESS_KEY`/`SCW_SECRET_KEY`) — pas pour AWS (`aws`), OVH (`ovh`), Hetzner (`hcloud`) ni Cloudflare (`cloudflare`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# Scaleway

Expertise infra activée sur détection d'un provider `scaleway` (Terraform) ou d'un usage de la CLI
`scw`/variables `SCW_ACCESS_KEY`/`SCW_SECRET_KEY` dans le projet. Scaleway propose deux surfaces de
compute nettement différentes — Instances (VM classiques) et Kapsule (Kubernetes managé) — et le
choix entre les deux doit être délibéré : Kapsule apporte une complexité opérationnelle qui ne se
justifie pas pour un service unique sans besoin d'orchestration.

## Principes

- Access key + secret key scopés par projet Scaleway — jamais des identifiants organisation-larges
  réutilisés entre plusieurs projets internes.
- IAM Scaleway scopé au projet plutôt qu'à l'organisation entière : une policy attachée au mauvais
  niveau donne un accès bien plus large que nécessaire sans que ce soit visible immédiatement.
- Infra as code via Terraform (provider `scaleway`) — pas de ressource créée à la main dans la
  Console pour un environnement qui doit rester reproductible.
- Choix explicite entre Instances et Kapsule selon le besoin réel : un service unique sans besoin
  d'orchestration multi-conteneurs part sur une Instance, pas par défaut sur Kapsule.
- Object Storage (compatible S3) avec des policies IAM dédiées par bucket/usage, jamais une clé
  d'accès Object Storage large réutilisée pour plusieurs buckets aux sensibilités différentes.
- Snapshot ou image avant toute opération destructrice sur une instance (resize, changement
  d'image, suppression) — pas de suppression à froid sans point de restauration récent.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression d'une instance ou d'un
volume sans snapshot récent, élargissement d'une policy IAM au niveau organisation au lieu du
projet, configuration d'un node pool Kapsule avec autoscaling pouvant descendre à zéro nœud sans
que ce soit l'intention explicite — exige l'affichage des ressources impactées et une confirmation
explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf` avec le provider `scaleway` et le state existant → `scw config get
access-key` / `scw info` pour confirmer le projet et la région/zone ciblés → `terraform plan
-out=plan.tfplan` → relire le plan en particulier les changements sur les policies IAM et les node
pools Kapsule → appliquer (`terraform apply plan.tfplan`) → `scw instance server list` ou `scw k8s
cluster list` pour vérifier l'état réel post-application → confirmer qu'aucun volume ou snapshot
orphelin n'a été laissé par l'opération.

## Diagnostiquer un incident

Identifier la ressource concernée (Instance ou cluster Kapsule) et la région → `scw instance server
get <id>` ou `scw k8s cluster get <id>` pour l'état → la Console Scaleway (vue Metrics/Cockpit) pour
le CPU/réseau/disque sur la fenêtre temporelle de l'incident → pour Kapsule, vérifier l'état du
node pool et son autoscaling (`scw k8s pool list`) avant de conclure à une panne applicative → si le
doute porte sur les permissions, relire la policy IAM du projet concerné avant de l'élargir à
l'aveugle.

## Coûts et sécurité

- Vue consommation/facturation de la Console Scaleway consultée régulièrement — volumes et
  snapshots non attachés sont un poste de coût qui s'accumule silencieusement, comme sur les autres
  clouds à budget serré.
- `scw instance volume list` et l'inventaire des snapshots passés en revue périodiquement pour
  repérer les ressources orphelines après suppression d'une instance.
- Autoscaling des node pools Kapsule vérifié explicitement : un scale-to-zero non voulu peut couper
  un service sans alerte si le minimum de nœuds n'est pas fixé correctement.
- Policies IAM scopées au projet plutôt qu'à l'organisation, revues à chaque ajout d'automatisation.
- Object Storage : policies par bucket plutôt qu'une clé d'accès unique large partagée entre
  plusieurs usages.

## Checklist de revue

- Aucune policy IAM scopée à l'organisation quand le scope projet suffirait.
- Snapshot ou image récente avant toute opération destructrice sur une instance.
- Choix Instances vs Kapsule justifié par le besoin réel, pas par défaut.
- Aucun volume ou snapshot orphelin laissé après suppression d'une ressource.
- Autoscaling des node pools Kapsule borné à un minimum de nœuds explicite, pas de scale-to-zero non voulu.
