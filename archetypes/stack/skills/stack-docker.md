<!-- ARCHETYPE: stack/docker — Adaptez à votre projet. -->
---
description: "Écrire ou optimiser un Dockerfile ou une configuration docker-compose — multi-stage, sécurité, healthchecks. À utiliser dès qu'une demande porte sur un Dockerfile ou un docker-compose.yml — pas pour l'orchestration multi-nœuds (K8s) ni le provisioning cloud (Terraform)."
tools: ["read", "edit", "execute"]
---

# Docker & Compose (ex-agent Container)

Ancien agent dédié (`docker-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Multi-stage builds obligatoires pour les images de production (séparer build et runtime).
- Jamais de secrets dans les Dockerfiles (ENV hardcodé) — toujours `--build-arg` ou variable d'environnement runtime.
- Images légères : `-alpine` ou distroless, `.dockerignore` propre, `USER` non-root obligatoire en production.
- Healthchecks sur tous les services, `depends_on` avec condition.

## Garde-fou

`docker system prune -af`, `docker volume rm`, suppression de volumes avec données → afficher l'impact et demander confirmation.

## Modifier une image ou un service

Lire le Dockerfile/compose entier → identifier les couches impactées → modifier → `cc-verify.sh --stack docker` (`docker compose config` + build check).

## Troubleshooting

Séquence : `docker logs` → `docker exec` → `docker inspect` → `docker stats`/`docker events` pour les problèmes de ressources ou de réseau.
