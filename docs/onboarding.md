---
audience: utilisateur
---

<p align="right"><a href="../README.md">README</a></p>

# <img src="../assets/icons/handoff.svg" width="32" height="32" alt=""> Guide de Démarrage Progressif — Grimoire Kit

> **J1** → Première session en 10 minutes 
> **S1** → Première semaine productive 
> **M1** → Premier mois, maîtrise complète

<img src="../assets/divider.svg" width="100%" alt="">


## <img src="../assets/icons/bolt.svg" width="28" height="28" alt=""> J1 — Premier Jour (10 min)

### 1. Installation et setup en une commande

```bash
# Chemin recommandé (SDK, aucun clone nécessaire)
pipx install grimoire-kit
cd votre-projet
grimoire up . --name "Mon Projet" --user "Votre Nom"
```

L'archétype est détecté depuis la pile du projet (`stack` pour une pile
sans domaine affirmé, `web-app`, `infra-ops`… sinon) ; `minimal` ne s'obtient
que sur demande explicite (`-a minimal`). `grimoire up` enchaîne : init express, propagation de l'identité, standard
agentique gouverné (profil `starter`), puis diagnostic. Idempotent — relancez-le
quand vous voulez, il ne touche que ce qui manque. Pour le wizard complet :
`grimoire up . --interactive`.

<details>
<summary>Chemin shell legacy (mode maintenance, nécessite --legacy)</summary>

```bash
curl -fsSL <url-install.sh> | bash -s -- --legacy \
  --name "Mon Projet" \
  --user "Votre Nom"
```

Sans `--legacy`, `install.sh` affiche les instructions SDK et s'arrête.

</details>

### 2. Vérifier l'installation

```bash
grimoire doctor
```

Attendu : tous les checks `OK`, y compris l'environnement (uv, docker, Qdrant,
Ollama — optionnels, avec remédiation affichée si absents). Pour régénérer des
wrappers agents ou un `.mcp.json` manquants : `grimoire doctor . --fix`.

### 2bis. Prouver que le hook tire (optionnel, recommandé)

`grimoire doctor` dit que le projet est en état ; il ne lance pas le hook que
votre hôte exécutera. Pour le vérifier pour de bon :

```bash
grimoire up . --prove
```

La commande ouvre une tâche jetable, exécute la commande `SessionStart`
déclarée par la configuration de chaque hôte (`.claude/settings.json`,
`.github/hooks/`) puis vérifie qu'un premier gate de preuve est passé du rouge au
vert grâce à ce hook. Elle sort en erreur si un hook est désactivé
(`disableAllHooks`), absent du `PATH` de l'hôte ou sans effet, alors que
`doctor` reste vert. Avec `--output json`, elle rend `hooks_fired`,
`first_gate` et `time_to_first_proof_s`.

Cette durée a un budget testé, pas un chiffre rond : le job de release installe
la roue dans un environnement vierge, lance `up --prove` et échoue au-delà de
**60 s** (`scripts/check-up-prove-budget.py`). La durée mesurée par ce job est
publiée dans son résumé, pas recopiée ici. Le budget ne couvre ni l'installation
du paquet ni le démarrage de l'interpréteur.

<!-- up-prove-budget-s: 60 -->

### 3. Enrôler vos autres projets (optionnel)

```bash
grimoire cockpit scan ~/dev --yes
```

Détecte récursivement les projets Grimoire de votre machine et les enrôle dans
le cockpit multi-projets (`grimoire cockpit`).

Le registre garde une entrée par projet enrôlé. Quand un projet est supprimé ou
déplacé, son entrée survit et pointe dans le vide :

```bash
grimoire cockpit prune --dry-run   # voir ce qui partirait
grimoire cockpit prune             # retirer, après confirmation
```

Par défaut, seules les entrées dont le chemin a disparu sont retirées : un
répertoire encore présent a pu être enrôlé délibérément, et supprimer une entrée
valide coûte plus cher que d'en garder une douteuse. `--stale` élargit aux
chemins qui existent mais ne portent plus de marqueur Grimoire.

### 4. Première interaction

Ouvrez votre hôte (Claude Code, Copilot Chat, Cursor…) dans le projet : les
agents installés sont disponibles comme personas, et le hook `SessionStart`
charge le contexte du projet. Depuis le terminal :

```bash
grimoire status          # état du projet, agents, mémoire
grimoire --all --help    # toutes les commandes, y compris celles masquées par défaut
```

### 5. Structure à connaître

```
votre-projet/
├── _grimoire/                  # Cerveau du projet
│   ├── _config/            # Configuration des agents
│   ├── _memory/            # Mémoire persistante
│   └── bmm/                # Module méthodologie
├── _grimoire-output/           # Artefacts produits
│   ├── planning-artifacts/ # PRD, épics, brainstorms
│   └── implementation-artifacts/
└── project-context.yaml    # Identité du projet
```

**C'est tout pour J1.** Vous avez un projet Grimoire fonctionnel.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/bolt.svg" width="28" height="28" alt=""> S1 — Première Semaine

### Jour 2-3 : Comprendre les agents

Les agents sont des personas spécialisées. Chacun a un domaine d'expertise :

| Agent | Spécialité | Quand l'utiliser |
|-------|-----------|-----------------|
| Grimoire Master | Orchestration | Commencer ici, il route vers les autres |
| Analyst (Mary) | Business | Étude de marché, exigences métier |
| PM (John) | Produit | PRD, user stories, priorisation |
| Architect (Winston) | Technique | Architecture, choix technologiques |
| Dev (Amelia) | Code | Implémentation, TDD |
| QA (Quinn) | Qualité | Tests, couverture, E2E |
| SM (Bob) | Agile | Sprint planning, backlog |

**Astuce** : tapez `/grimoire-` dans Copilot Chat pour voir tous les workflows disponibles.

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
3. Affiche `&#x2713; CC PASS` ou ` CC FAIL`

Si CC FAIL → l'agent corrige automatiquement avant de rendre la main.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/bolt.svg" width="28" height="28" alt=""> M1 — Premier Mois

### Semaine 2 : Mémoire et contexte

Le système de mémoire à 4 couches :

1. **shared-context.md** — Vérité partagée (stack, conventions, décisions)
2. **agent-learnings/** — Leçons apprises par chaque agent
3. **decisions-log.md** — Journal des décisions ADR
4. **Procedural memory** — Patterns par type de tâche

```bash
# Vérifier la santé de la mémoire
grimoire memory doctor

# Voir l'état de la mémoire
grimoire memory status
```

### Semaine 2-3 : Outils CLI

Le kit inclut une CLI unifiée :

```bash
# Vue d'ensemble
grimoire status

# Santé du système
grimoire doctor

# Toutes les commandes disponibles
grimoire --all --help

# Qualité des artefacts
grimoire standard score

# Dépendances inter-outils
python3 framework/tools/dep-check.py --project-root . graph
```

### Semaine 3 : Archétypes

Les archétypes sont des configurations pré-packagées pour différents types de projets :

| Archétype | Pour qui | Agents inclus |
|-----------|---------|---------------|
| stack | Toute pile sans domaine affirmé (défaut détecté) | Atlas (stack-engineer) + skills de la pile détectée |
| web-app | Apps web full-stack | Frontend Specialist, Fullstack Dev |
| infra-ops | DevOps/Infrastructure | Ops Engineer, SRE |
| platform-engineering | Plateforme, backend, SRE | Backend Engineer, Platform Architect |
| fix-loop | Debugging intensif | Bug Hunter, Analyzer |
| creative-studio | Contenu, design | Agents créatifs |
| minimal | Base seule, sur demande explicite (`-a minimal`) | Meta-agents seulement |

```bash
# Installer un archétype
grimoire init . -a web-app

# Lister les archétypes disponibles
grimoire registry list
```

### Semaine 4 : Expertises et mise à jour

```bash
# Attacher une expertise à la demande (langage, cloud, compétence)
grimoire expertise list --detected
grimoire expertise add rust aws

# Mettre à jour le kit d'un projet déjà enrôlé
grimoire upgrade-flow run --dry-run
```

### Workflow typique M1

```
1. [PLAN] Demander au PM de créer un PRD
2. [PLAN] L'Architect propose l'architecture
3. [ACT]  Le SM découpe en stories
4. [ACT]  Le Dev implémente (TDD + CC)
5. [ACT]  Le QA valide la couverture
6.        `grimoire standard gate check` prouve la clôture
```

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/link.svg" width="28" height="28" alt=""> Aide rapide

| Besoin | Commande |
|--------|---------|
| Aide Grimoire | `grimoire --all --help` |
| Diagnostic | `grimoire doctor` |
| Sync config | `grimoire setup --check` |
| État mémoire | `grimoire memory status` |
| Qualité sortie | `grimoire standard score` |


*Grimoire Kit — Documentation progressive. Pour les détails, voir `docs/`.*
