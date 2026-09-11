<!-- ARCHETYPE: stack/terraform — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer une infrastructure cloud déclarative en Terraform — plan avant apply, modules, state. À utiliser dès qu'une demande porte sur un fichier .tf/.tfvars — pas pour les conteneurs (Docker/K8s) ni l'automatisation de postes (Ansible)."
tools: ["read", "edit", "execute"]
---

# Terraform (ex-agent Terra)

Ancien agent dédié (`terraform-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Plan obligatoire avant tout apply — jamais `terraform apply -auto-approve` sans avoir affiché le plan complet.
- State = source de vérité — jamais modifier l'infra hors Terraform.
- Modules pour la réutilisabilité, pas de ressources dupliquées ; variables typées, documentées, validées ; outputs documentés.
- Secrets en variables sensibles, jamais de valeur hardcodée dans un `.tf`.

## Garde-fou

`terraform destroy`, ressources critiques avec `prevent_destroy = false` → afficher les ressources impactées et demander confirmation explicite.

## Modifier l'infrastructure

Lire les fichiers `.tf` et le state → `terraform plan` avant toute modification → modifier → `terraform validate` + `terraform fmt` → `cc-verify.sh --stack terraform`.
