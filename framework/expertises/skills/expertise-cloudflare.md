<!-- EXPERTISE: expertise-cloudflare — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une configuration Cloudflare — DNS-as-code, Workers (wrangler), WAF/rate limiting, API tokens scopés par zone. À utiliser dès qu'un projet référence des ressources Cloudflare (provider `cloudflare` en Terraform, `wrangler.toml`, zones DNS Cloudflare, `CLOUDFLARE_API_TOKEN`) — pas pour AWS (`aws`), OVH (`ovh`), Hetzner (`hcloud`) ni Scaleway (`scaleway`), et pas pour du code applicatif sans lien avec l'edge/DNS/CDN."
tools: ["read", "edit", "execute"]
---

# Cloudflare

Expertise infra activée sur détection d'un provider `cloudflare` (Terraform), d'un fichier
`wrangler.toml`, ou d'une gestion de zone DNS Cloudflare dans le projet. Cloudflare combine trois
surfaces distinctes qu'il faut traiter avec des disciplines différentes : le DNS (doit être as-code
pour éviter la dérive), les Workers (compute en périphérie, contrat de déploiement porté par
`wrangler.toml`), et la couche sécurité/WAF devant l'origine.

## Principes

- API tokens scopés par zone et par permission (ex. `Zone.DNS:Edit` sur une zone précise) — jamais
  la clé API globale legacy pour une automatisation ou une CI.
- DNS-as-code : une fois qu'une zone est gérée par Terraform (provider `cloudflare`), tout
  enregistrement passe par le code — une modification manuelle dans le dashboard crée une dérive
  que le prochain `terraform plan` révélera de façon inattendue.
- `wrangler.toml` comme contrat de déploiement des Workers, avec séparation explicite des
  environnements (`[env.staging]`, `[env.production]`) — pas de déploiement direct en production
  sans passer par l'environnement de staging défini.
- WAF, règles de pare-feu et rate limiting comme couche de sécurité devant l'origine, pensés comme
  la première ligne de défense et non comme un complément optionnel.
- Purge de cache ciblée (par tag ou par préfixe d'URL) comme réflexe par défaut — la purge globale
  ("Purge Everything") est une opération lourde à réserver aux cas où le ciblage est impossible.
- Séparation claire entre les identifiants Workers KV/Durable Objects et leur modèle de cohérence
  réel, documentée dans le code plutôt que supposée par l'équipe.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression ou modification d'un
enregistrement DNS de production, désactivation du proxy Cloudflare (passage en DNS only) sur un
enregistrement qui masquait l'IP d'origine, purge globale du cache, déploiement d'un Worker
directement en environnement de production — exige l'affichage des ressources impactées et une
confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire la configuration Terraform (provider `cloudflare`) ou `wrangler.toml` et l'état existant →
confirmer la zone et l'environnement ciblés → `terraform plan -out=plan.tfplan` pour les ressources
DNS/WAF, relire en particulier les changements de statut `proxied` → appliquer
(`terraform apply plan.tfplan`) → pour un Worker, `wrangler deploy --env staging` d'abord, valider,
puis `wrangler deploy --env production` → vérifier dans le dashboard Cloudflare (Analytics) que le
trafic bascule comme attendu après déploiement.

## Diagnostiquer un incident

Identifier la zone ou le Worker concernés → `wrangler tail --env <env>` pour les logs en direct
d'un Worker → le dashboard Cloudflare (Security Events) pour les requêtes bloquées par le WAF ou le
rate limiting → Analytics pour corréler la latence/le taux d'erreur avec un déploiement récent ou un
changement DNS → si le doute porte sur l'exposition de l'origine, vérifier le statut `proxied` de
l'enregistrement DNS concerné avant de conclure à une fuite d'IP.

## Coûts et sécurité

- Jamais la clé API globale legacy dans une CI ou un script — uniquement des tokens scopés par zone
  et par permission minimale.
- Règles WAF et rate limiting revues après tout changement d'exposition publique d'un service, pas
  seulement à la mise en place initiale.
- Purge de cache par tag/préfixe documentée dans le déploiement, pour éviter le réflexe de purge
  globale qui dégrade temporairement les performances de tous les visiteurs.
- Statut `proxied` vérifié explicitement sur chaque enregistrement DNS censé masquer l'IP d'origine
  — un enregistrement en DNS only expose directement le serveur.
- Modèle de cohérence de Workers KV/Durable Objects vérifié dans la documentation avant de
  l'utiliser pour un état qui exige une cohérence forte.

## Checklist de revue

- Aucun usage de la clé API globale legacy ; uniquement des tokens scopés par zone/permission.
- Tout enregistrement DNS de production géré par Terraform, aucune modification manuelle non reportée dans le code.
- Statut `proxied` cohérent avec l'intention (masquage d'IP d'origine ou non) sur chaque enregistrement concerné.
- Déploiement Worker passé par l'environnement de staging avant la production.
- Règles WAF/rate limiting en place devant toute origine exposée publiquement.
