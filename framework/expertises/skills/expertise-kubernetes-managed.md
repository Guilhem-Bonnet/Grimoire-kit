<!-- EXPERTISE: expertise-kubernetes-managed — Adaptez à votre projet. -->
---
description: "Opérer un cluster Kubernetes managé (EKS, AKS, GKE) — provisionnement du control plane, node pools, IAM d'accès au cluster. À utiliser dès qu'une demande porte sur le cycle de vie d'un cluster managé ou son intégration IAM au fournisseur — pas pour écrire ou déboguer des manifestes/Helm (voir stack-k8s), pas pour le provisionnement d'autres ressources cloud du même fournisseur (voir expertise-aws/azure/gcp)."
tools: ["read", "edit", "execute"]
---

# Kubernetes managé (EKS / AKS / GKE)

Expertise cloud activée sur détection d'une ressource de cluster managé (`aws_eks_cluster`, `azurerm_kubernetes_cluster`, `google_container_cluster` dans un `*.tf`, ou équivalent CLI/console) : ce que `stack-k8s` ne couvre pas — le cluster lui-même, pas ce qui tourne dedans.

## Principes

- Le control plane est la responsabilité du fournisseur ; les node pools, l'IAM d'accès et le réseau qui les entoure sont la vôtre — ne jamais confondre "managé" avec "sans opération".
- Accès au cluster via l'identité cloud native du fournisseur (IAM Roles for Service Accounts sur EKS, Workload Identity sur GKE, Managed Identity sur AKS) plutôt que des secrets de compte de service exportés à la main.
- Version du cluster et des node pools suivie explicitement (fenêtre de support du fournisseur) — une montée de version planifiée, jamais découverte en fin de support forcé.
- Node pools dimensionnés et autoscalés avec des bornes explicites (min/max), jamais un pool à taille fixe "au cas où" ou un autoscaling sans plafond.
- Le réseau du cluster (VPC/subnets dédiés, security groups/NSG du control plane et des nœuds) est déclaré en infra as code au même titre que le cluster — jamais retouché à la main dans la console.
- Le fichier `kubeconfig` généré pointe vers l'identité cloud courante (`aws eks update-kubeconfig`, `az aks get-credentials`, `gcloud container clusters get-credentials`) — jamais un kubeconfig statique partagé et copié entre postes.

## Garde-fou

Suppression d'un cluster, d'un node pool, ou changement de plan réseau (CIDR, sous-réseau du control plane) : opération destructive et souvent irréversible pour les workloads en cours — afficher l'impact (charges qui tournent dessus, PDB en place) et demander confirmation avant tout `terraform apply`/suppression console.

## Provisionner ou modifier un cluster

Lire la définition Terraform du cluster et des node pools existants → `terraform plan` et vérifier particulièrement les remplacements de node pool (souvent destructifs, pas de simple mise à jour) → appliquer → `aws eks update-kubeconfig`/`az aks get-credentials`/`gcloud container clusters get-credentials` pour rafraîchir l'accès → vérifier `kubectl get nodes` avant de considérer l'opération terminée.

## Diagnostiquer un incident

`kubectl get nodes` pour l'état des nœuds → logs et métriques du control plane côté fournisseur (CloudWatch Container Insights sur EKS, Azure Monitor pour AKS, Cloud Logging/Cloud Monitoring pour GKE — jamais uniquement `kubectl logs`, le control plane managé n'est pas visible par `kubectl`) → vérifier l'IAM d'accès si un pod ne peut pas atteindre une ressource cloud (rôle IRSA/Workload Identity/Managed Identity mal lié) → `kubectl describe node` pour un nœud NotReady.

## Coûts et sécurité

Node pools au bon type d'instance pour la charge réelle (pas de sur-dimensionnement "par prudence"), spot/preemptible pour les charges tolérantes aux interruptions, cluster autoscaler ou Karpenter/équivalent plutôt qu'un dimensionnement manuel, endpoint du control plane restreint (accès privé ou IP allowlist, jamais public sans restriction), audit logs du control plane activés côté fournisseur.

## Checklist de revue

- La version du cluster et des node pools est dans la fenêtre de support du fournisseur.
- L'accès du cluster aux autres ressources cloud passe par l'identité native (IRSA/Workload Identity/Managed Identity), jamais une clé exportée.
- Les node pools ont des bornes d'autoscaling explicites, pas de taille fixe arbitraire.
- L'endpoint du control plane n'est pas exposé publiquement sans restriction.
- Un changement de node pool proposé en `terraform plan` comme un remplacement (pas une mise à jour) a été vu et assumé, pas juste appliqué.
