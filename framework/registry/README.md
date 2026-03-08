<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/grimoire.svg" width="32" height="32" alt=""> Archétype Registry Grimoire — BM-21

> Index des archétypes disponibles et protocole d'installation à la demande.

## <img src="../../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Concept

Le Registry permet d'installer des archétypes supplémentaires dans un projet existant sans réinitialiser. Équivalent de `npm install` mais pour les agents et workflows Grimoire.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Archétypes disponibles

### Archétypes Built-in (locaux, dans ce kit)

| ID | Nom | Agents | Cas d'usage |
|----|-----|--------|-------------|
| `minimal` | Minimal | Atlas, Sentinel, Mnemo + 1 tpl | Point de départ universel |
| `infra-ops` | Infra Ops | Forge, Vault, Flow, Hawk, Helm, Phoenix, Systems-Debugger | Infrastructure DevOps complète |
| `web-app` | Web App | Frontend Specialist, Fullstack Dev + stack auto | Applications web |
| `fix-loop` | Fix Loop | Loop (orchestrateur) + workflow 9 phases | Boucle de correction certifiée |
| `stack/go` | Go Expert | Gopher | Backend Go |
| `stack/typescript` | TypeScript | Pixel | Frontend TS/React |
| `stack/python` | Python | Serpent | Backend Python |
| `stack/terraform` | Terraform | Terra | IaC Terraform |
| `stack/k8s` | Kubernetes | Kube | Workloads K8s |
| `stack/docker` | Docker | Container | Containerisation |
| `stack/ansible` | Ansible | Playbook | Automation Ansible |
| `features/vector-memory` | Vector Memory | Vectus | Mémoire vectorielle Qdrant |

### Archétypes Communautaires (futurs — v2.0)

```
# Format prévu :
grimoire-init.sh install archetype ml-platform    # depuis registry communautaire
grimoire-init.sh install archetype data-pipeline  # depuis GitHub packages
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Commandes `install`

### Installer un archétype

```bash
# Installation d'un archétype built-in dans un projet existant
grimoire-init.sh install --archetype infra-ops

# Installer un agent de stack spécifique
grimoire-init.sh install --archetype stack/go

# Installer plusieurs archétypes
grimoire-init.sh install --archetype fix-loop
grimoire-init.sh install --archetype features/vector-memory

# Forcer la réinstallation (écraser les fichiers existants)
grimoire-init.sh install --archetype web-app --force
```

### Lister les archétypes disponibles

```bash
grimoire-init.sh install --list
```

### Inspecter un archétype avant installation

```bash
grimoire-init.sh install --inspect infra-ops
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Format du manifest d'un archétype (`archetype.dna.yaml`)

Chaque archétype déclare sa "DNA" — ses traits, contraintes et valeurs — dans un fichier `archetype.dna.yaml` à la racine de son répertoire.

Voir le schéma complet : [framework/archetype-dna.schema.yaml](../archetype-dna.schema.yaml)

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> Protocole d'installation

Lors d'un `install`, le script :

1. Localise l'archétype dans `archetypes/{id}/`
2. Lit `archetype.dna.yaml` pour déterminer ce qui doit être copié
3. **Agents** → copiés dans `_grimoire/_config/custom/agents/`
4. **Workflows** → copiés dans `_grimoire/_config/custom/workflows/`
5. **Shared context** → fusionné dans `_grimoire/_memory/shared-context.md` (append, pas écrasement)
6. **Prompts** → copiés dans `.github/prompts/{archetype}/` si existants
7. Met à jour `project-context.yaml` → `installed_archetypes: [...]`

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Registre local (`_grimoire/_config/installed-archetypes.yaml`)

Créé automatiquement à l'init et mis à jour par chaque `install` :

```yaml
# Auto-généré par grimoire-init.sh
installed:
  - id: minimal
    version: "1.0.0"
    installed_at: "2026-02-27"
    agents: [project-navigator, agent-optimizer, memory-keeper]
  - id: stack/go
    version: "1.0.0"
    installed_at: "2026-02-27"
    agents: [go-expert]
    traits_applied: [tdd, hexagonal-architecture, cc-mandatory]
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Versioning des archétypes

Structure du champ `version` dans `archetype.dna.yaml` : `MAJOR.MINOR.PATCH`

- **MAJOR** : changements breaking (renommage d'agents, suppression de commandes)
- **MINOR** : nouveaux agents ou workflows ajoutés
- **PATCH** : corrections, améliorations de prompts

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/folder-tree.svg" width="28" height="28" alt=""> Roadmap v2.0 — Registry Public

```
Phase 1 (actuelle) : archétypes built-in locaux
Phase 2 : grimoire-init.sh install archetype <github-user>/<archetype-name>
Phase 3 : grimoire-init.sh search "data pipeline"  → liste les archétypes pertinents
Phase 4 : grimoire-init.sh publish archetype ./my-archetype
```
