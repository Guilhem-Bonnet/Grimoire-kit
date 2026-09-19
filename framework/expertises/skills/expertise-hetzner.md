<!-- EXPERTISE: expertise-hetzner — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure Hetzner Cloud — API tokens scopés par projet, Terraform provider `hcloud`, firewalls comme ressource Cloud à part entière, sobriété de coûts comme contrainte de conception. À utiliser dès qu'un projet référence des ressources Hetzner (provider `hcloud` en Terraform, CLI `hcloud`, `HCLOUD_TOKEN`) — pas pour AWS (`aws`), OVH (`ovh`), Scaleway (`scaleway`) ni Cloudflare (`cloudflare`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# Hetzner Cloud

Expertise infra activée sur détection d'un provider `hcloud` (Terraform) ou d'un usage de la CLI
`hcloud`/variable `HCLOUD_TOKEN` dans le projet. Hetzner justifie son usage par le prix : une
instance surdimensionnée, un volume oublié ou une snapshot qui traîne annulent directement la
raison de choisir ce fournisseur plutôt qu'un hyperscaler. La sobriété de coût est donc un critère
de conception de premier ordre, pas une optimisation a posteriori.

## Principes

- Tokens API scopés par projet (un projet Hetzner Cloud = un token) — jamais un token
  organisation-large réutilisé entre plusieurs projets ou environnements.
- Moindre privilège sur les tokens : un token utilisé par une automatisation en lecture seule
  (monitoring, inventaire) ne doit avoir que le droit `read`, jamais `read/write` par défaut.
- Infra as code via Terraform (provider `hcloud`) — pas de serveur créé à la main dans la Console
  pour un environnement qui doit rester reproductible.
- Firewalls comme ressource Cloud à part entière (`hcloud_firewall`), pas seulement des règles
  iptables au niveau OS : la ressource Terraform est la source de vérité, elle doit être attachée
  explicitement aux serveurs concernés.
- Snapshot avant toute opération risquée (changement de type de serveur, migration d'image,
  suppression) — une snapshot Hetzner est rapide et bon marché, l'absence de snapshot avant une
  opération destructrice n'a pas de justification économique.
- Load Balancer Hetzner Cloud (`hcloud_load_balancer`) pour la haute disponibilité plutôt qu'un
  montage maison devant plusieurs serveurs, sauf contrainte spécifique documentée.
- Dimensionnement des serveurs revu à chaque changement de charge notable : un type de serveur
  choisi une fois n'est pas supposé rester correct indéfiniment.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression d'un serveur ou d'un volume
sans snapshot récente, détachement d'un firewall `hcloud_firewall` d'un serveur exposé, ouverture
d'une règle firewall à `0.0.0.0/0` sur autre chose qu'un port de service public assumé — exige
l'affichage des ressources impactées et une confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf` avec le provider `hcloud` et le state existant → `hcloud context list` pour
confirmer le projet/contexte actif → `terraform plan -out=plan.tfplan` → relire le plan en
particulier les changements sur les firewalls et les IP flottantes → appliquer
(`terraform apply plan.tfplan`) → `hcloud server list` / `hcloud firewall describe <id>` pour
vérifier l'état réel post-application → confirmer qu'aucun volume ou snapshot orphelin n'a été
laissé par l'opération.

## Diagnostiquer un incident

Identifier le serveur ou la ressource concernés → `hcloud server describe <id>` pour l'état et les
métadonnées → la Console Hetzner Cloud (vue Metrics) pour le CPU/réseau/disque sur la fenêtre
temporelle de l'incident → `hcloud firewall describe <id>` si le doute porte sur une règle réseau
bloquante → vérifier qu'une IP flottante (`hcloud floating-ip list`) n'est pas restée assignée à un
serveur détruit, cause fréquente de perte de connectivité silencieuse.

## Coûts et sécurité

- Vue coût/usage de la Hetzner Cloud Console consultée régulièrement, pas seulement en fin de mois
  — les volumes et snapshots non attachés sont le poste de coût qui s'accumule le plus
  silencieusement.
- `hcloud volume list` et `hcloud image list --type snapshot` passés en revue périodiquement pour
  repérer les ressources orphelines après suppression d'un serveur.
- Tokens API projet-scopés avec le droit minimal (`read` seul quand c'est suffisant), jamais un
  token `read/write` par réflexe.
- IP flottantes désassignées explicitement avant la suppression d'un serveur, pour éviter de payer
  une IP qui ne sert plus rien.
- Firewalls Hetzner Cloud comme couche de filtrage principale, redondants avec un pare-feu OS
  minimal plutôt que remplacés par lui.

## Checklist de revue

- Aucun token API organisation-large réutilisé entre projets ; droit minimal (`read` vs `read/write`).
- Snapshot récente avant toute opération destructrice sur un serveur.
- Toute règle firewall ouverte à `0.0.0.0/0` correspond à un port de service public assumé.
- Aucun volume ou snapshot orphelin laissé après suppression d'un serveur.
- Aucune IP flottante assignée à un serveur qui n'existe plus.
