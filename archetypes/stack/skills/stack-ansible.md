<!-- ARCHETYPE: stack/ansible — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer des playbooks Ansible — idempotence, dry-run, secrets chiffrés. À utiliser dès qu'une demande porte sur un playbook ou un rôle Ansible — pas pour le provisioning cloud (Terraform) ni l'orchestration de conteneurs (K8s)."
tools: ["read", "edit", "execute"]
---

# Ansible (ex-agent Playbook)

Ancien agent dédié (`ansible-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Modules Ansible plutôt que `shell:`/`command:` quand un module existe — idempotence garantie.
- Dry-run (`--check`) avant toute exécution sur prod.
- Secrets chiffrés (`ansible-vault` ou SOPS), jamais en clair dans les vars ou fichiers.
- Tags sur chaque rôle pour l'exécution sélective ; `changed_when`/`failed_when` explicites sur les tâches shell.

## Garde-fou

`--limit all` combiné à des tags destroy/remove/delete, tâches `state: absent` sur des ressources critiques → afficher les hosts impactés et demander confirmation.

## Modifier un playbook ou un rôle

Lire le playbook/rôle entier → vérifier en mode `--check` d'abord → modifier → `ansible-lint` + `yamllint` → `cc-verify.sh --stack ansible`.
