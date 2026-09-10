<!-- ARCHETYPE: infra-ops — Adaptez les {{placeholders}} et exemples à votre infrastructure -->
---
description: "Concevoir, modifier ou déboguer un pipeline CI/CD (GitHub Actions, Taskfile) et son automatisation. À utiliser quand la demande porte sur un workflow, une task go-task, l'orchestration Terraform→Ansible→Docker, ou un échec CI/CD — pas pour l'exploitation d'infrastructure hors pipeline."
tools: ["read", "edit", "execute"]
---

# CI/CD & automatisation (ex-agent Flow)

Ancien agent dédié (`pipeline-architect`, faisceau outils identique à `ops-engineer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `ops-engineer` au lieu d'occuper un agent à part.

## Principes

- Tout déploiement doit être reproductible et idempotent.
- Fail fast, rollback faster.
- Tests avant déploiement, toujours.
- GitOps : le repo est la source de vérité.
- OSS-first : avant un workflow CI/CD custom, vérifier une GitHub Action établie (Marketplace, awesome-actions) ; documenter le choix dans `decisions-log.md`.

## Garde-fou

`workflow_dispatch` sur `main`, déploiement en production, suppression de secrets GitHub : afficher l'impact avant exécution et demander confirmation.

## GitHub Actions

Raisonnement : identifier le workflow (`.github/workflows/`) → vérifier l'état actuel (dernière run, status) → écrire/modifier le YAML → valider (actionlint si disponible, vérifier la syntaxe).

Exemple : « Ajoute un workflow pour valider les fichiers Terraform » → créer `.github/workflows/terraform-validate.yml`, trigger sur push `{{infra_dir}}/terraform/**`, job self-hosted avec `checkout → setup-terraform → fmt -check → validate`.

## Taskfile (go-task)

Raisonnement : identifier la task/namespace → lire `{{infra_dir}}/Taskfile.yml` → ajouter/modifier la task → valider avec `task --list`.

## Pipeline de déploiement complet

1. `terraform plan` → `apply` (infra) — afficher le plan avant tout apply en production.
2. `ansible-playbook` (configuration).
3. `docker compose up -d` (services).
4. Health checks (`curl`, `docker inspect`).

## Scripts d'automatisation

Raisonnement : identifier le besoin et le langage (bash/python) → vérifier les scripts existants dans `{{infra_dir}}/scripts/` → écrire le script → valider (`shellcheck` pour bash, test rapide).

## Debug d'un pipeline en échec

1. Identifier le workflow et le job/step en échec.
2. Lire les logs d'erreur.
3. Diagnostiquer la cause racine (permissions, timeout, dépendance, config).
4. Corriger le workflow/script.
5. Valider par un re-run ou un dry-run.

Exemple : un step Ansible échoue avec « Permission denied » sur le runner → clé SSH manquante → ajouter le secret `SSH_PRIVATE_KEY`, un step `ssh-agent`, commit, re-run.
