<p align="right"><a href="../../README.md">README</a></p>

# <img src="../../docs/assets/icons/grimoire.svg" width="32" height="32" alt=""> Exemple : Terraform-HouseServer

Ce dossier montre comment le Grimoire Kit est utilisé dans le projet d'origine [Terraform-HouseServer](https://github.com/Guilhem-Bonnet/Terraform-HouseServer).

## <img src="../../docs/assets/icons/brain.svg" width="28" height="28" alt=""> Contexte

- **Infrastructure** : Proxmox VE homelab avec 6 LXC + cluster K3s
- **Stack** : Terraform, Ansible, Docker Compose, K3s, FluxCD
- **Monitoring** : Prometheus, Grafana, Loki, Alertmanager
- **Archétype** : `infra-ops` (10 agents)

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/brain.svg" width="28" height="28" alt=""> `project-context.yaml`

```yaml
project:
  name: "Terraform-HouseServer"
  description: "Infrastructure as Code pour homelab Proxmox"
  type: "infrastructure"
  stack:
    - "Terraform"
    - "Ansible"
    - "Docker Compose"
    - "K3s"
    - "FluxCD"
  repos:
    - name: "infra-prod-home-"
      path: "./infra-prod-home-"

user:
  name: "Guilhem"
  language: "Français"

infrastructure:
  hosts:
    developadream:
      ip: "192.168.2.22"
      role: "Proxmox VE host"
    core-services:
      ip: "192.168.2.60"
      role: "LXC 210 — Traefik, Monitoring, Docker stacks"
    adguard-dns:
      ip: "192.168.2.64"
      role: "LXC 215 — AdGuard Home DNS"
    k3s-master:
      ip: "192.168.2.70"
      role: "VM 220 — K3s control-plane + GPU"
    k3s-worker:
      ip: "192.168.2.71"
      role: "Bare-metal — K3s worker + GPU + Longhorn"
  network:
    cidr: "192.168.2.0/24"
    gateway: "192.168.2.1"

agents:
  archetype: "infra-ops"
  custom_agents:
    - name: "forge"
      icon: "🔧"
      domain: "Infrastructure & Provisioning"
      keywords: "terraform ansible docker compose lxc proxmox vm deploy"
    - name: "hawk"
      icon: "📡"
      domain: "Observabilité & Monitoring"
      keywords: "prometheus grafana loki alertmanager dashboard alert promql"
    # ... (voir le fichier complet du projet)
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Ce qui a été personnalisé

1. **Identités des agents** : Chaque `<identity>` mentionne les IPs, LXC IDs et services spécifiques
2. **Exemples** : Tous les `<example>` utilisent des commandes réelles du projet
3. **shared-context.md** : Topologie réseau complète avec tous les conteneurs et VMs
4. **Pre-commit hook** : Intégré dans le pipeline `pre-commit` existant avec ansible-lint, tflint, etc.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/chart.svg" width="28" height="28" alt=""> Résultats

- **10 agents** actifs avec mémoire sémantique partagée
- **Health-check** automatique à chaque session (rate-limité 24h)
- **Contradiction detection** automatique sur chaque ajout de mémoire
- **Consolidation** automatique des learnings à chaque commit et session
- **Dispatch sémantique** fonctionnel pour router les requêtes vers le bon agent
