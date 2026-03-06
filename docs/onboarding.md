# Guide de Démarrage Progressif — Grimoire Kit

> **J1** → Première session en 10 minutes  
> **S1** → Première semaine productive  
> **M1** → Premier mois, maîtrise complète

---

## J1 — Premier Jour (10 min)

### 1. Installation

```bash
# Cloner le kit dans votre projet
git clone <url-grimoire-kit> bmad-custom-kit

# Initialiser BMAD dans votre projet
cd votre-projet
bash path/to/bmad-custom-kit/bmad-init.sh \
  --name "Mon Projet" \
  --user "Votre Nom" \
  --archetype minimal
```

### 2. Vérifier l'installation

```bash
# Le doctor vérifie tout
bash bmad-custom-kit/grimoire.sh doctor
```

Attendu : tous les checks en ✅.

### 3. Première interaction

Ouvrez VS Code, activez GitHub Copilot Chat, puis tapez :

```
/bmad-master
```

L'agent BMad Master se présente avec un menu numéroté. Tapez le numéro d'une option pour commencer.

### 4. Structure à connaître

```
votre-projet/
├── _bmad/                  # Cerveau du projet
│   ├── _config/            # Configuration des agents
│   ├── _memory/            # Mémoire persistante
│   └── bmm/                # Module méthodologie
├── _bmad-output/           # Artefacts produits
│   ├── planning-artifacts/ # PRD, épics, brainstorms
│   └── implementation-artifacts/
└── project-context.yaml    # Identité du projet
```

**C'est tout pour J1.** Vous avez un projet BMAD fonctionnel.

---

## S1 — Première Semaine

### Jour 2-3 : Comprendre les agents

Les agents sont des personas spécialisées. Chacun a un domaine d'expertise :

| Agent | Spécialité | Quand l'utiliser |
|-------|-----------|-----------------|
| BMad Master | Orchestration | Commencer ici, il route vers les autres |
| Analyst (Mary) | Business | Étude de marché, exigences métier |
| PM (John) | Produit | PRD, user stories, priorisation |
| Architect (Winston) | Technique | Architecture, choix technologiques |
| Dev (Amelia) | Code | Implémentation, TDD |
| QA (Quinn) | Qualité | Tests, couverture, E2E |
| SM (Bob) | Agile | Sprint planning, backlog |

**Astuce** : tapez `/bmad-` dans Copilot Chat pour voir tous les workflows disponibles.

### Jour 4-5 : Les modes Plan/Act

Chaque agent supporte deux modes :

- **[PLAN]** — L'agent prépare, structure, propose. N'écrit rien sans votre accord.
- **[ACT]** — L'agent exécute directement. Mode par défaut.

Pour switcher : tapez `[PLAN]` ou `[ACT]` dans le chat.

**Règle d'or** : Utilisez [PLAN] pour les décisions structurantes, [ACT] pour l'exécution.

### Jour 5-7 : Le Completion Contract (CC)

Le CC est la règle fondatrice : un agent qui dit "terminé" doit le prouver.

Avant chaque "fait" :
1. L'agent détecte le stack (Python/Go/TS/...)
2. Lance les vérifications automatiques (tests, lint, build)
3. Affiche `✅ CC PASS` ou `🔴 CC FAIL`

Si CC FAIL → l'agent corrige automatiquement avant de rendre la main.

---

## M1 — Premier Mois

### Semaine 2 : Mémoire et contexte

Le système de mémoire à 4 couches :

1. **shared-context.md** — Vérité partagée (stack, conventions, décisions)
2. **agent-learnings/** — Leçons apprises par chaque agent
3. **decisions-log.md** — Journal des décisions ADR
4. **Procedural memory** — Patterns par type de tâche

```bash
# Vérifier la santé de la mémoire
python3 framework/memory/maintenance.py health-check

# Voir les patterns procéduraux enregistrés
python3 framework/tools/procedural-memory.py --project-root . list
```

### Semaine 2-3 : Outils CLI

Le kit inclut une CLI unifiée :

```bash
# Vue d'ensemble
bash grimoire.sh status

# Santé du système
bash grimoire.sh doctor

# Liste des outils disponibles
bash grimoire.sh tools

# Qualité des artefacts
python3 framework/tools/quality-score.py --project-root . batch _bmad-output/

# Dépendances inter-outils
python3 framework/tools/dep-check.py --project-root . graph
```

### Semaine 3 : Archétypes

Les archétypes sont des configurations pré-packagées pour différents types de projets :

| Archétype | Pour qui | Agents inclus |
|-----------|---------|---------------|
| minimal | Tout projet | Base seulement |
| web-app | Apps web full-stack | Frontend Specialist, Fullstack Dev |
| infra-ops | DevOps/Infrastructure | Ops Engineer, SRE |
| fix-loop | Debugging intensif | Bug Hunter, Analyzer |
| features | Feature development | Tous agents de dev |
| meta | Framework BMAD lui-même | Agent Optimizer, Art Director, Toolsmith |

```bash
# Installer un archétype
bash bmad-init.sh install --archetype web-app

# Lister les archétypes disponibles
bash bmad-init.sh install --list
```

### Semaine 4 : NSO et Intelligence Layer

Le Nervous System Orchestrator (NSO) est le méta-outil qui orchestre tout le système :

```bash
# Run complet du système nerveux
python3 framework/tools/nso.py --project-root . run

# Mode rapide
python3 framework/tools/nso.py --project-root . run --quick

# Rétrospective automatique
python3 framework/tools/nso.py --project-root . retro
```

### Workflow typique M1

```
1. [PLAN] Demander au PM de créer un PRD
2. [PLAN] L'Architect propose l'architecture
3. [ACT]  Le SM découpe en stories
4. [ACT]  Le Dev implémente (TDD + CC)
5. [ACT]  Le QA valide la couverture
6.        Le NSO fait une rétrospective
```

---

## Aide rapide

| Besoin | Commande |
|--------|---------|
| Aide BMAD | `/bmad-master` puis option aide |
| Diagnostic | `bash grimoire.sh doctor` |
| État mémoire | `python3 framework/memory/maintenance.py status` |
| Qualité sortie | `python3 framework/tools/quality-score.py --project-root . score fichier.md` |
| Recherche web | `python3 framework/tools/mcp-web-search.py --project-root . search "query"` |

---

*Grimoire Kit — Documentation progressive. Pour les détails, voir `docs/`.*
