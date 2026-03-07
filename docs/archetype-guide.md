# Guide des Archétypes

## Qu'est-ce qu'un archétype ?

Un archétype est un ensemble pré-configuré d'agents, de workflows, d'un DNA de comportements et de templates adapté à un type de projet spécifique. Chaque archétype déclare ses **traits** (règles comportementales), ses **constraints** (bloquants), ses **tools_required** et ses **acceptance_criteria** machine-lisibles.

```bash
# Installer un archétype dans un projet existant
bash bmad-init.sh install --archetype web-app
bash bmad-init.sh install --archetype stack/go
bash bmad-init.sh install --list          # voir tous les disponibles
bash bmad-init.sh install --inspect infra-ops  # détails avant install

# Valider les fichiers DNA
bash bmad-init.sh validate --all

# Diagnostiquer l'installation
bash bmad-init.sh doctor
```

## Archétypes disponibles

### `minimal` — Archétype racine universel

**Cas d'usage** : Tout type de projet — le strict nécessaire pour démarrer.

**Traits DNA :** Plan/Act Mode `[PLAN]/[ACT]`, Extended Thinking `[THINK]`, Failure Museum, CC-aware  
**Tools requis :** bash, git (python3 recommandé)

**Agents inclus :**
| Agent | Icône | Rôle |
|-------|-------|------|
| Atlas (project-navigator) | 🗺️ | Navigation projet, registre des services, Repo Map `[RM]` |
| Sentinel (agent-optimizer) | 🔍 | Audit qualité des agents, optimisation prompts, Self-Improvement Loop |
| Mnemo (memory-keeper) | 🧠 | Mémoire Qdrant, contradictions, consolidation |

**+ 1 template vierge** (`custom-agent.tpl.md`) pour créer vos propres agents.

**Acceptance Criteria (générables via `gen-tests.py`) :**
- `cc-pass-before-done` — cc-verify.sh PASS avant toute déclaration terminé (**hard**)
- `memory-updated-end-of-session` — Qdrant agent-learnings mis à jour (**soft**)
- `no-raw-secrets-committed` — zéro secret en clair commité (**hard**)

**Quand l'utiliser :** projets de tout type, base pour tous les autres archétypes.

---

### `web-app` — Full-Stack Web

**Cas d'usage** : SPA + API REST, fullstack Next.js, backend headless.

**Traits DNA :** TDD obligatoire, TypeScript strict, API Contract First, Accessibilité WCAG 2.1 AA  
**Tools requis :** node, docker (recommandé), navigateur headless (E2E)

**Acceptance Criteria notables :**
- `tests-written-before-code` — tests avant implémentation (**hard**)
- `typescript-strict-no-any` — zéro `any` explicite (**hard**)
- `api-schema-before-impl` — OpenAPI/type avant code API (**soft**)
- `aria-labels-on-interactive` — WCAG 2.1 AA (**soft**)

**Détection automatique :**
```bash
bash bmad-init.sh --name "Mon App" --user "Guilhem" --auto
# → stack détecté : go frontend docker
# → archétype : web-app
# → agents stack : Gopher + Pixel + Container
```

---

### `infra-ops` — Infrastructure & DevOps

**Cas d'usage** : Homelab, clusters K8s, IaC Terraform/Ansible, monitoring.

**Traits DNA :** Infrastructure-as-Code, Plan-before-Apply, Security First, Backup-before-Change, Observability Mandatory  
**Tools requis :** terraform, docker, kubectl (optionnel), ansible (optionnel)

**Agents inclus (3 meta + 7 spécialisés) :**

| Agent | Icône | Rôle |
|-------|-------|------|
| Atlas | 🗺️ | Navigation & Mémoire projet |
| Sentinel | 🔍 | Qualité & Optimisation agents |
| Mnemo | 🧠 | Mémoire & Qualité connaissances |
| Forge (ops-engineer) | 🔧 | Infrastructure & Provisioning |
| Vault (security-hardener) | 🛡️ | Sécurité & Hardening (SOPS, TLS) |
| Flow (pipeline-architect) | ⚡ | CI/CD & Automation |
| Hawk (monitoring-specialist) | 📡 | Observabilité (Prometheus, Grafana) |
| Helm (k8s-navigator) | ☸️ | Kubernetes & Orchestration |
| Phoenix (backup-dr-specialist) | 🏰 | Backup & Disaster Recovery |
| Probe (systems-debugger) | 🔬 | Systems Debugging |

**Acceptance Criteria notables :**
- `terraform-plan-before-apply` — plan validé avant apply (**hard**)
- `no-secrets-in-tf-state` — zéro secret dans state (**hard**)
- `backup-snapshot-before-destructive` — snapshot avant migration (**hard**)
- `monitoring-alert-on-new-service` — alerte sur chaque nouveau service (**soft**)

---

### `platform-engineering` — Ingénierie de Plateforme End-to-End

**Cas d'usage** : Plateformes distribuées, backends scalables, microservices/modular monolith. De l'architecture au service observable en production.

**Traits DNA :** Architecture First, Contract Driven, Observability by Design, Deploy Safely, Twelve-Factor, Resilience Patterns  
**Tools requis :** docker, kubectl (optionnel), terraform (optionnel), helm (optionnel)

**Agents inclus (3 meta + 4 spécialisés) :**

| Agent | Icône | Rôle |
|-------|-------|------|
| Atlas | 🗺️ | Navigation & Mémoire projet |
| Sentinel | 🔍 | Qualité & Optimisation agents |
| Mnemo | 🧠 | Mémoire & Qualité connaissances |
| Archie (platform-architect) | 🏛️ | Architecture système, DDD, event-driven, C4 diagrams |
| Stack (backend-engineer) | ⚙️ | Backend polyglotte, APIs, CQRS, event sourcing |
| Guardian (reliability-engineer) | 🛡️ | SRE, SLO/SLI, observabilité, incident response |
| Convoy (deploy-orchestrator) | 🚀 | Déploiement progressif, GitOps, pipelines CI/CD |

**Combinable avec les agents stack/** : Terra 🌍, Kube ⎈, Container 🐋, Gopher 🐹, Pixel ⚛️, Serpent 🐍 selon le stack détecté.

**Acceptance Criteria notables :**
- `architecture-documented` — chaque service documenté dans architecture.md (**soft**)
- `api-contract-before-impl` — contrat API/event avant implémentation (**hard**)
- `sli-defined-per-service` — SLI mesurables par service (**soft**)
- `rollback-tested` — rollback testé en staging avant prod (**soft**)
- `structured-logs` — logs JSON structurés avec trace_id (**soft**)

**Templates inclus :**
- `architecture.tpl.md` — source de vérité architecturale (C4, services, events, SLO, infra)

**Installation :**
```bash
bash bmad-init.sh install --archetype platform-engineering

# Combiné avec des stack agents
bash bmad-init.sh install --archetype platform-engineering
bash bmad-init.sh install --archetype stack/go
bash bmad-init.sh install --archetype stack/docker
bash bmad-init.sh install --archetype stack/k8s
```

**Section `project-context.yaml` dédiée :**
```yaml
platform:
  type: "microservices"
  services:
    - name: "user-service"
      tech: "go"
      slo_availability: "99.9%"
  messaging:
    broker: "nats"
  observability:
    metrics: "prometheus"
    logs: "loki"
    traces: "tempo"
  deployment:
    strategy: "canary"
    gitops: "fluxcd"
```

---

### `fix-loop` — Boucle de Correction Certifiée

**Cas d'usage** : Tout projet avec bugs récurrents — zéro "done" sans preuve d'exécution.

**Traits DNA :** Proof of Execution, FER Isolation, Severity Adaptive S1/S2/S3, Never-Assume-Fixed  
**Tools requis :** bash, python3 (recommandé)

**Agents inclus :**
| Agent | Icône | Rôle |
|-------|-------|------|
| Loop (fix-loop-orchestrator) | 🔁 | Orchestrateur boucle fermée, FER, META-REVIEW |

**Acceptance Criteria notables :**
- `fer-created-before-fix` — FER YAML créé avant d'écrire du code (**hard**)
- `all-tests-rerun-after-fix` — toute la suite relancée après fix (**hard**)
- `fer-closed-with-cc-pass` — CC PASS attaché au FER (**hard**)

**Concepts clés :**
- **FER** (Fix Evidence Record) : fichier YAML isolant chaque cycle de fix
- **Sévérité** : S3 = 3 phases, S2 = 6, S1 = 9 phases obligatoires
- **META-REVIEW** : auto-amélioration du workflow après cycle certifié

---

### `stack` — Modal Team Engine (7 experts spécialisés)

**Cas d'usage** : Agents stack déployés automatiquement selon le tech stack détecté.

**Agents et leurs DNA :**

| Agent | Icône | Stack | AC notables |
|-------|-------|-------|-------------|
| Gopher | 🐹 | `go.mod` | table-driven tests, error wrapping, no goroutine leak |
| Pixel | ⚛️ | `package.json` + react/vue | no `any`, props typées, async error handling |
| Serpent | 🐍 | `requirements.txt` / `pyproject.toml` | type hints, ruff clean, no blocking in async |
| Container | 🐋 | `Dockerfile` / `docker-compose.yml` | multi-stage, non-root user, healthchecks |
| Terra | 🌍 | `*.tf` | plan before apply, remote state, tfsec clean |
| Kube | ⎈ | `k8s/`, `kind: Deployment` | resource limits, RBAC least-privilege, probes |
| Playbook | 🎭 | `ansible/`, `playbook*.yml` | idempotence, vault for secrets, ansible-lint |

**Génération automatique de tests depuis les DNA :**
```bash
# Générer les squelettes de tests pour un agent stack
python3 framework/tools/gen-tests.py \
  --dna archetypes/stack/agents/go-expert.dna.yaml \
  --framework pytest

python3 framework/tools/gen-tests.py \
  --dna archetypes/stack/agents/typescript-expert.dna.yaml \
  --framework jest
```

**Déploiement automatique :**
```bash
bash bmad-init.sh --name "Mon API" --user "Guilhem" --auto
# → stack détecté : go docker
# → agents stack : Gopher 🐹 + Container 🐋
```

**Installation manuelle d'un agent stack :**
```bash
bash bmad-init.sh install --archetype stack/go
bash bmad-init.sh install --archetype stack/typescript
bash bmad-init.sh install --archetype stack/python
bash bmad-init.sh install --archetype stack/docker
bash bmad-init.sh install --archetype stack/k8s
bash bmad-init.sh install --archetype stack/terraform
bash bmad-init.sh install --archetype stack/ansible
```

---

## Accept Criteria & gen-tests.py (BM-27 + BM-29)

Chaque archétype déclare des `acceptance_criteria` dans son DNA. L'outil `gen-tests.py` les convertit en squelettes de tests dans le framework de votre choix.

```bash
# Lister les AC sans générer
python3 framework/tools/gen-tests.py \
  --dna archetypes/infra-ops/archetype.dna.yaml \
  --list-ac

# Générer les tests (bats pour infra)
python3 framework/tools/gen-tests.py \
  --dna archetypes/infra-ops/archetype.dna.yaml \
  --framework bats \
  --output tests/infra/

# Frameworks supportés : pytest | jest | bats | go-test | rspec | vitest
```

---

## .agent-rules — Override DNA par dossier (BM-25)

Un fichier `.agent-rules` dans n'importe quel dossier surcharge localement le DNA global :

```yaml
# src/payments/.agent-rules
scope: "src/payments/"
priority: 1
rules:
  - id: "pci-mandatory"
    description: "Validation Sentinel obligatoire avant toute modification payments"
    enforcement: hard
auto_load:
  - "docs/pci-dss-checklist.md"
reminders:
  - "⚠️  Module PCI-DSS — double review obligatoire"
```

Référence : [framework/agent-rules.md](../framework/agent-rules.md)

---

## Créer un nouvel archétype

```bash
# Structure minimale
mkdir -p archetypes/mon-archetype/agents/
cat > archetypes/mon-archetype/archetype.dna.yaml << 'EOF'
$schema: "bmad-archetype-dna/v1"
id: mon-archetype
name: "Mon Archétype"
version: "1.0.0"
description: "Description courte"
icon: "🎯"
author: "votre-nom"
tags: [custom]
inherits: minimal
traits: []
tools_required: []
acceptance_criteria: []
compatible_with: [minimal, fix-loop]
incompatible_with: []
EOF

# Valider le DNA
bash bmad-init.sh validate --dna archetypes/mon-archetype/archetype.dna.yaml

# Installer
bash bmad-init.sh install --archetype mon-archetype
```

Voir : [creating-agents.md](creating-agents.md) et [framework/archetype-dna.schema.yaml](../framework/archetype-dna.schema.yaml)

---

## Personnaliser un archétype installé

### Étape 1 : Adapter les identités agents

Chaque agent a des `{{placeholders}}` à remplacer :

```markdown
<!-- AVANT -->
Tu es Forge, expert IaC pour {{network_cidr}}, déploiement via {{infra_dir}}.

<!-- APRÈS -->
Tu es Forge, expert IaC pour 10.0.0.0/8, déploiement via terraform-prod/.
```

### Étape 2 : Remplir `shared-context.md`

Source de vérité lue par tous les agents — décrire stack, architecture, services, conventions.

### Étape 3 : Configurer `project-context.yaml`

```yaml
session_branch: "main"
installed_archetypes:
  - id: web-app
    installed_at: "2026-02-27"
context_budget:
  default_max_tokens: 80000
repo_map:
  enabled: true
  strategy: find
```

### Étape 4 : Créer des `.agent-rules` pour les modules critiques

```bash
echo 'rules: [{id: no-plaintext-secrets, description: "No secrets in yaml", enforcement: hard}]' \
  > src/config/.agent-rules
```

---

## Diagnostics

```bash
# Health check complet
bash bmad-init.sh doctor

# Valider tous les DNA
bash bmad-init.sh validate --all

# Générer CHANGELOG depuis les décisions agents
bash bmad-init.sh changelog

# Voir l'audit trail des actions
bash bmad-init.sh trace --tail 50
bash bmad-init.sh trace --type DECISION

# Budget de contexte LLM — vérifier que les agents ne saturent pas la fenêtre
bash bmad-init.sh guard                  # tous les agents
bash bmad-init.sh guard --suggest        # + recommandations de réduction
bash bmad-init.sh guard --json           # sortie CI-compatible

# DNA évolutive — proposer des mutations depuis l'usage réel (après quelques semaines)
bash bmad-init.sh evolve --report        # rapport sans modifier la DNA
bash bmad-init.sh evolve                 # proposer patch (revue humaine requise avant --apply)
```

Voir [framework/tools/README.md](../framework/tools/README.md) pour la référence complète des outils.

---

## Ressources complémentaires

- [getting-started.md](getting-started.md) — Démarrage en 7 étapes
- [memory-system.md](memory-system.md) — Mémoire Qdrant multi-collection
- [workflow-design-patterns.md](workflow-design-patterns.md) — 13 patterns universels
- [creating-agents.md](creating-agents.md) — Créer un agent custom
- [framework/archetype-dna.schema.yaml](../framework/archetype-dna.schema.yaml) — Schéma DNA complet
- [framework/context-router.md](../framework/context-router.md) — Gestion du budget contexte
- [framework/agent-rules.md](../framework/agent-rules.md) — Override DNA par dossier

---

### `web-app`

**Cas d'usage** : Applications web — SPA + API REST, fullstack Next.js, backend headless. Sélectionné automatiquement par `--auto` quand un frontend **et** un backend sont détectés.

**Contenu :**
- `shared-context.tpl.md` — sections : Stack, Architecture, API (routes + auth), Base de données, Variables d'env, Conventions, Points de vigilance
- Agents : les 3 meta (Atlas, Sentinel, Mnemo) + agents `stack` selon détection (`--auto`)

**Détection automatique :**
```bash
# Exemple : projet Go + React
bash bmad-init.sh --name "Mon App" --user "Guilhem" --auto
# → stack détecté : go frontend docker
# → archétype auto : web-app
# → agents stack déployés : Gopher + Pixel + Container
```

**Sections du `shared-context.tpl.md` à remplir :**
1. Stack Technique — frontend/backend/DB/auth/deploy avec versions
2. Architecture — arborescence répertoires
3. API — base URL, auth method, routes principales
4. Base de données — moteur, connexion, tables principales
5. Environnement local — commandes dev/test
6. Variables d'environnement — liste exhaustive
7. Conventions — commits, branches, outils

**Quand l'utiliser :**
- SPA (React, Vue, Next.js) + API REST (Go, Python, Node)
- Applications fullstack avec base de données
- Projets avec frontend et backend séparés dans le même repo

---

### `infra-ops`

**Cas d'usage** : Infrastructure, DevOps, homelab, serveurs — l'archétype complet.

**Agents inclus :** (les 3 meta + 7 spécialisés)

| Agent | Icône | Rôle |
|-------|-------|------|
| Atlas | 🗺️ | Navigation & Mémoire projet |
| Sentinel | 🔍 | Qualité & Optimisation agents |
| Mnemo | 🧠 | Mémoire & Qualité connaissances |
| Forge (ops-engineer) | 🔧 | Infrastructure & Provisioning (Terraform, Ansible, Docker) |
| Vault (security-hardener) | 🛡️ | Sécurité & Hardening (SOPS, TLS, firewall) |
| Flow (pipeline-architect) | ⚡ | CI/CD & Automation (GitHub Actions, Taskfile) |
| Hawk (monitoring-specialist) | 📡 | Observabilité (Prometheus, Grafana, alerting) |
| Helm (k8s-navigator) | ☸️ | Kubernetes & Orchestration (K3s, FluxCD) |
| Phoenix (backup-dr-specialist) | 🏰 | Backup & Disaster Recovery |
| Probe (systems-debugger) | 🔬 | Systems Debugging (kernel, perf, strace) |

**Quand l'utiliser :**
- Homelab Proxmox avec LXC/VMs
- Clusters Kubernetes (K3s, K8s)
- Infrastructure as Code (Terraform, Ansible)
- Stacks de monitoring (Prometheus/Grafana/Loki)

---

## Personnaliser un archétype

### Étape 1 : Adapter les identités

Chaque agent a une section `<identity>` avec des `{{placeholders}}`. Remplacez-les par vos valeurs :

```markdown
<!-- AVANT (template) -->
<identity>
Tu es Forge, expert IaC pour le projet décrit dans shared-context.md.
Tu gères {{network_cidr}}, déploiement via {{infra_dir}}.
</identity>

<!-- APRÈS (personnalisé) -->
<identity>
Tu es Forge, expert IaC pour l'infrastructure production.
Tu gères 10.0.0.0/8 avec 3 serveurs bare-metal, déploiement via terraform-prod/.
</identity>
```

### Étape 2 : Adapter les exemples

Les blocs `<example>` contiennent des exemples réalistes. Remplacez-les par des situations de votre projet.

### Étape 3 : Ajouter/retirer des agents

- **Retirer** : Supprimez le fichier `.md` et sa ligne dans `agent-manifest.csv` et `shared-context.md`
- **Ajouter** : Copiez `custom-agent.tpl.md`, remplissez, enregistrez (voir [creating-agents.md](creating-agents.md))

### Étape 4 : Remplir `shared-context.md`

Ce fichier est la source de vérité lue par tous les agents. Décrivez-y :
- Architecture du projet
- Topologie réseau
- Services et où ils tournent
- Conventions d'équipe

## Créer un nouvel archétype

Pour contribuer un archétype au kit :

1. Créer `archetypes/mon-archetype/agents/` avec les agents
2. Créer `archetypes/mon-archetype/shared-context.tpl.md`
3. Documenter dans ce guide
4. Tester avec `bmad-init.sh --archetype mon-archetype`

**Archétypes disponibles :**
- `minimal` — Meta-agents (Atlas, Sentinel, Mnemo) + template vierge ✅
- `infra-ops` — Infrastructure & DevOps (10 agents) ✅
- `fix-loop` — Boucle de correction certifiée (Loop, workflow 9 phases) ✅

**Archétypes envisagés :**
- `web-app` — Frontend + Backend + DB (React, Next.js, Rails, Django)
- `data-pipeline` — ETL, ML, analytics (dbt, Airflow, Spark)
- `game-dev` — Moteurs de jeu, assets, QA (Unity, Godot)

**Ressources complémentaires :**
- [Patterns de design workflow](workflow-design-patterns.md) — 13 patterns universels extraits de 86 fixes
- [Créer un agent](creating-agents.md) — Guide complet avec clause "Use when"
