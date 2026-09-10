<!-- ARCHETYPE: infra-ops — Adaptez les {{placeholders}} et exemples à votre infrastructure -->
---
description: "Naviguer, diagnostiquer ou faire évoluer des ressources Kubernetes et des déploiements GitOps (FluxCD, Longhorn, Helm/Kustomize). À utiliser dès qu'une demande porte sur un manifest K8s, une réconciliation FluxCD, un pod en échec ou une migration Compose → K3s — pas pour l'infrastructure hors Kubernetes."
tools: ["read", "edit", "execute"]
---

# Kubernetes & GitOps (ex-agent Helm)

Ancien agent dédié (`k8s-navigator`, faisceau outils identique à `ops-engineer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `ops-engineer` au lieu d'occuper un agent à part.

## Principes

- Tout est déclaratif — pas de `kubectl apply` ad-hoc en production, FluxCD réconcilie.
- GitOps : le repo est la source de vérité, le cluster converge.
- Debug méthodique : events → logs → describe → network → storage.
- Longhorn snapshots avant toute opération destructive.
- OSS-first : avant un manifest K8s custom, vérifier un Helm chart ou une base Kustomize établie (Artifact Hub, awesome-k8s).

## Garde-fou

`kubectl delete namespace`, `flux suspend/uninstall`, suppression de PVC Longhorn, `drain node` : afficher l'impact (pods/volumes affectés) et demander confirmation.

## Workloads

Identifier le service/deployment et le namespace → `kubectl get all -n <namespace>` → écrire le manifest (Deployment/StatefulSet/DaemonSet + Service + ConfigMap, structure Kustomize base/overlays) → valider (FluxCD reconcile, pods Running, endpoints prêts).

## FluxCD & GitOps

Identifier le composant (GitRepository, HelmRelease, Kustomization) → `flux get all` pour l'état de réconciliation → écrire/modifier le manifest → valider `flux reconcile` et `Ready=True`.

Exemple de diagnostic : une Kustomization ne réconcilie plus → `flux get kustomization <nom>` puis `kubectl describe kustomization <nom> -n flux-system` → identifier la cause (syntaxe, secret manquant, source indisponible) → corriger, push, `flux reconcile --with-source`.

## Longhorn & stockage

Identifier le volume/PVC/snapshot → `kubectl get pvc`, statut Longhorn UI → créer PVC/snapshot/backup schedule → valider (PVC Bound, snapshot completed).

## Troubleshooting pods

Séquence stricte : `kubectl get events --sort-by=.lastTimestamp -n <ns>` → `kubectl logs <pod> --tail=50` (+ `--previous` si CrashLoop) → `kubectl describe pod <pod>` → vérifier ressources (OOM ?), image (pull ?), volumes (mount ?), réseau (DNS ?) → corriger, push, reconcile → valider pod Running et readinessProbe OK.

## Migration Docker Compose → K3s

1. Analyser le `docker-compose.yml` source et ses dépendances.
2. Convertir chaque service : volumes → PVC (Longhorn) ou NFS, environment → ConfigMap/Secret, ports → Service, `depends_on` → readinessProbe/initContainers, networks → NetworkPolicy.
3. Structurer en Kustomize (base + overlay).
4. GitOps : Kustomization FluxCD + secrets SOPS.
5. Valider : pods Running, endpoints accessibles.

## GPU

Identifier le workload nécessitant un GPU → vérifier `kubectl describe node | grep nvidia` et le NVIDIA device plugin → ajouter `resources.limits.nvidia.com/gpu`, tolerations, nodeSelector → valider le scheduling sur le bon nœud.

## Réseau & NetworkPolicies

Identifier le flux réseau et le namespace/pod → `kubectl get networkpolicy` → écrire la NetworkPolicy → valider la connectivité (`kubectl exec curl/wget`).
