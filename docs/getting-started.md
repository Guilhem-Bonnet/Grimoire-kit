# Getting Started — Grimoire Kit

## Prérequis

- [BMAD Framework](https://github.com/bmadcode/BMAD-METHOD) v6.0+ installé dans votre projet
- Python 3.10+ (pour le système de mémoire)
- Git (pour les hooks pre-commit)

## Installation rapide

```bash
# 1. Cloner le kit
git clone https://github.com/Guilhem-Bonnet/grimoire-kit.git
cd grimoire-kit

# 2a. Initialiser en mode automatique (recommandé)
# Détecte le stack et déploie les agents adaptés automatiquement
cd votre-projet/
bash /chemin/vers/grimoire-kit/bmad-init.sh \
  --name "Mon Projet" \
  --user "Alice" \
  --auto

# 2b. OU initialiser manuellement avec un archétype spécifique
bash /chemin/vers/grimoire-kit/bmad-init.sh \
  --name "Mon Projet" \
  --user "Alice" \
  --archetype infra-ops

# 3. Personnaliser
# Éditer project-context.yaml dans votre projet
# Adapter les agents dans _bmad/_config/custom/agents/
```

## Structure créée

Après `bmad-init.sh`, votre projet contiendra :

```
mon-projet/
├── project-context.yaml          ← Configuration centralisée
├── _bmad/
│   ├── _config/
│   │   ├── custom/
│   │   │   ├── agent-base.md     ← Protocole commun (avec Completion Contract)
│   │   │   ├── cc-verify.sh      ← Vérificateur multi-stack (go/ts/docker/tf/k8s/...)
│   │   │   ├── sil-collect.sh    ← Collecteur Self-Improvement Loop
│   │   │   ├── agents/           ← Fichiers agents déployés
│   │   │   ├── prompt-templates/
│   │   │   └── workflows/
│   │   └── agent-manifest.csv
│   └── _memory/
│       ├── config.yaml
│       ├── maintenance.py
│       ├── mem0-bridge.py
│       ├── session-save.py
│       ├── shared-context.md     ← Contexte partagé
│       ├── decisions-log.md
│       ├── contradiction-log.md  ← Contradictions inter-agents
│       ├── memories.json
│       ├── activity.jsonl
│       └── agent-learnings/
└── _bmad-output/
    └── sil-report-latest.md      ← Rapport Self-Improvement Loop (généré)
```

## Premiers pas

### 1. Éditer `project-context.yaml`

Ce fichier centralise toute la configuration de votre projet :

```yaml
project:
  name: "Mon API Backend"
  type: "api"
  metaphor: "forteresse"                # Guide les agents (optionnel)
  stack: ["Python", "FastAPI", "PostgreSQL"]

user:
  name: "Alice"
  language: "Français"
  skill_level: "intermediate"           # beginner | intermediate | expert
```

> **`skill_level`** adapte la verbosité des agents : `beginner` = pédagogique avec explications détaillées, `intermediate` = équilibré, `expert` = direct et concis.
> **`metaphor`** donne une métaphore structurante au projet que les agents utilisent pour nommer et prioriser (ex: "forteresse" → vocabulaire sécurité-first).

### 2. Installer un archétype supplémentaire (optionnel)

```bash
# Lister tous les archétypes disponibles
bash /chemin/vers/grimoire-kit/bmad-init.sh install --list

# Installer un agent de stack spécifique dans un projet existant
bash /chemin/vers/grimoire-kit/bmad-init.sh install --archetype stack/go
bash /chemin/vers/grimoire-kit/bmad-init.sh install --archetype stack/typescript
bash /chemin/vers/grimoire-kit/bmad-init.sh install --archetype fix-loop

# Inspecter avant d'installer
bash /chemin/vers/grimoire-kit/bmad-init.sh install --inspect infra-ops
```

### 3. Session Branching — travailler sur une exploration séparée

```bash
# Créer une branche de session pour une exploration risquée
bash /chemin/vers/grimoire-kit/bmad-init.sh session-branch --name explore-graphql

# Lister les branches actives
bash /chemin/vers/grimoire-kit/bmad-init.sh session-branch --list

# Différences entre branches
bash /chemin/vers/grimoire-kit/bmad-init.sh session-branch --diff main explore-graphql

# Merger vers main quand valide
bash /chemin/vers/grimoire-kit/bmad-init.sh session-branch --merge explore-graphql
```

### 4. Mémoire structurée — protocol remember/recall

```bash
# Mémoriser une décision
python _bmad/_memory/mem0-bridge.py remember \
    --type decisions --agent atlas \
    "On utilise Terraform 1.7 pour tout le provisioning"

# Rechercher sémantiquement
python _bmad/_memory/mem0-bridge.py recall "base de données choix"

# Exporter la mémoire d'un agent en Markdown
python _bmad/_memory/mem0-bridge.py export-md --type agent-learnings \
    --output _bmad/_memory/agent-learnings/atlas.md
```

### 5. Plan/Act Mode et Extended Thinking

Dans Copilot Chat, avec un agent actif :

```
[PLAN]   → l'agent planifie sans modifier de fichier, attend votre validation
ok go    → bascule en mode ACT, l'agent exécute jusqu'à CC PASS
[THINK]  → délibération profonde : ≥3 options, simulation d'échecs, ADR
```

### 6. Personnaliser les agents

Chaque agent dans `_bmad/_config/custom/agents/` contient des `{{placeholders}}` à remplacer. Les sections clés :

- **`<identity>`** — Décrivez votre infrastructure/projet spécifique
- **`<example>`** — Remplacez par des exemples concrets de votre environnement

### 7. Vérifier l'installation

```bash
python _bmad/_memory/maintenance.py health-check --force
```

## Choix de l'archétype

| Archétype | Agents inclus | Cas d'usage |
|-----------|---------------|-------------|
| `minimal` | Atlas, Sentinel, Mnemo + 1 template vierge | Tout projet |
| `infra-ops` | 10 agents spécialisés infra/DevOps | Homelab, serveurs, K8s |
| `--auto` | Détecté par stack | Laissez le Modal Team Engine décider |

### Agents stack (déployés par `--auto` selon ce qui est détecté)

| Stack détecté | Agent déployé | Persona |
|---------------|--------------|--------|
| `go.mod` | Gopher | 🐹 Expert Go |
| `package.json` + react/vue | Pixel | ⚛️ Expert TypeScript/React |
| `requirements.txt` | Serpent | 🐍 Expert Python |
| `Dockerfile` | Container | 🐋 Expert Docker |
| `*.tf` | Terra | 🌍 Expert Terraform |
| `k8s/` ou `kind: Deployment` | Kube | ⎈ Expert K8s |
| `ansible/` ou `playbook*.yml` | Playbook | 🎭 Expert Ansible |

## Completion Contract

Tous les agents intègrent le Completion Contract : ils ne peuvent pas dire "terminé" sans passer
`cc-verify.sh`.

```bash
# Vérifier votre code manuellement
bash _bmad/_config/custom/cc-verify.sh

# Vérifier un stack spécifique seulement
bash _bmad/_config/custom/cc-verify.sh --stack go
bash _bmad/_config/custom/cc-verify.sh --stack k8s
```

Sortie : `✅ CC PASS — [go, typescript, docker] — 2026-02-23 21:28`

## Self-Improvement Loop (optionnel)

Après quelques semaines d'utilisation, analysez vos patterns d'échec :

```bash
# Collecter les signaux
bash _bmad/_config/custom/sil-collect.sh
# → génère : _bmad-output/sil-report-latest.md

# Analyser avec Sentinel
# Ouvrir Sentinel dans VS Code → [FA] Self-Improvement Loop
# Sentinel propose des règles à ajouter au framework
```

## Outils de performance & évolution

Après quelques semaines d'utilisation, quatre outils CLI vous aident à maintenir le kit performant.

### Bench — mesurer les performances agents

```bash
bash bmad-init.sh bench --summary           # scoreboard global
bash bmad-init.sh bench --report            # détail par agent
bash bmad-init.sh bench --improve           # génère bench-context.md pour Sentinel [FA]
```

Sortie : scores 0-100, tendance semaine, agents en dégradation.

### Forge — générer des squelettes d'agents

```bash
bash bmad-init.sh forge --from "agent expert en migrations DB PostgreSQL"
bash bmad-init.sh forge --from-gap          # depuis lacunes détectées dans BMAD_TRACE
bash bmad-init.sh forge --list              # lister les proposals générés
bash bmad-init.sh forge --install db-migrator  # installer un proposal
```

Sortie : `_bmad-output/forge-proposals/agent-[tag].proposed.md`

### Guard — budget de contexte LLM

Mesure le budget de contexte consommé par chaque agent **avant la première question** :

```bash
bash bmad-init.sh guard                          # tous les agents
bash bmad-init.sh guard --suggest                # + recommandations réduction
bash bmad-init.sh guard --agent atlas --detail   # détail fichier par fichier
bash bmad-init.sh guard --model gpt-4o           # fenetre GPT-4o (128K)
bash bmad-init.sh guard --json                   # CI-compatible (exit 2 = critique)
```

Seuils : < 40% ✅ OK — 40-70% ⚠️ WARNING — > 70% 🔴 CRITICAL

### Evolve — DNA vivante

Fait évoluer `archetype.dna.yaml` depuis l'usage réel (BMAD_TRACE, decisions, learnings) :

```bash
bash bmad-init.sh evolve                     # proposer évolutions
bash bmad-init.sh evolve --report            # rapport Markdown seul
bash bmad-init.sh evolve --since 2026-01-01  # depuis une date
bash bmad-init.sh evolve --apply             # appliquer après votre review
```

Sorties : `_bmad-output/dna-proposals/archetype.dna.patch.{date}.yaml` + rapport Markdown.

> ⚠️ `--apply` ne modifie jamais la DNA sans votre accord explicite — le gate humain est toujours conservé.

## Hooks pre-commit (optionnel)

Si votre projet utilise `pre-commit`, ajoutez dans `.pre-commit-config.yaml` :

```yaml
- repo: local
  hooks:
    - id: mnemo-consolidate
      name: "🧠 Mnemo — Consolidation mémoire"
      entry: bash -c 'python _bmad/_memory/maintenance.py consolidate-learnings && python _bmad/_memory/maintenance.py context-drift'
      language: system
      always_run: true
      pass_filenames: false
      stages: [pre-commit]
```

## Configuration VS Code

Pour une expérience optimale avec les agents BMAD (pas de confirmations bloquantes,
pas d'erreurs réseau, gestion des rate limits) :

→ **[docs/vscode-setup.md](vscode-setup.md)** — Guide complet de configuration VS Code
