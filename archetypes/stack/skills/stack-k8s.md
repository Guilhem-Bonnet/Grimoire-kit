<!-- ARCHETYPE: stack/k8s — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer des manifestes Kubernetes et des charts Helm — dry-run, resource limits, troubleshooting. À utiliser dès qu'une demande porte sur un manifest K8s ou un pod en échec — pas pour le build d'image (Docker) ni l'infrastructure hors conteneurs (Terraform/Ansible)."
tools: ["read", "edit", "execute"]
---

# Kubernetes (ex-agent Kube)

Ancien agent dédié (`k8s-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Dry-run obligatoire avant tout apply (`kubectl apply --dry-run=server -f manifest.yaml`).
- Resource limits (requests + limits CPU/mémoire) et probes (readiness + liveness) sur chaque workload sans exception.
- Déclaratif plutôt qu'impératif — manifests dans Git, pas de `kubectl edit` en prod.

## Garde-fou

`kubectl delete namespace`, `drain node`, suppression de PVC avec données → afficher l'impact et demander confirmation.

## Modifier un workload

Lire les manifests existants → dry-run serveur → modifier → appliquer et vérifier les pods `Running` → `cc-verify.sh --stack k8s`.

## Troubleshooting pods

Séquence stricte : `kubectl get events --sort-by=.lastTimestamp` → `kubectl logs <pod> --tail=50` (+ `--previous` si CrashLoop) → `kubectl describe pod` → vérifier ressources (OOM ?), image (pull ?), volumes, réseau (DNS ?) → corriger et revalider.
