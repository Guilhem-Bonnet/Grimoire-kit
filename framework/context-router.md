<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/boomerang.svg" width="32" height="32" alt=""> Context Budget Router — BM-07

> Système de gestion automatique du budget de contexte (tokens) pour les agents Grimoire. Inspiré du Context Window Management de mem0/MemGPT.

## <img src="../docs/assets/icons/microscope.svg" width="28" height="28" alt=""> Problème

Un agent qui charge tous ses fichiers mémoire + la codebase + l'historique dépasse rapidement la fenêtre de contexte disponible. Sans gestion explicite, l'agent truncate en silence, perd du contexte critique, et hallucine.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Solution : Priorité de chargement déclarée

Chaque agent déclare un `context_budget` dans son fichier YAML de configuration. Le router choisit les fichiers à charger selon leur priorité et la capacité restante estimée.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/chart.svg" width="28" height="28" alt=""> Niveaux de priorité

| Niveau | Nom | Chargement | Contenu typique |
|--------|-----|-----------|-----------------|
| **P0** | ALWAYS | Systématique | Agent persona, règles core, BASE PROTOCOL |
| **P1** | SESSION | À chaque activation | shared-context.md, decisions-log.md, agent-learnings récents |
| **P2** | TASK | Si pertinent pour la tâche | Fichiers liés à la story courante, ADRs du domaine |
| **P3** | LAZY | À la demande explicite | Archives, historiques longs, knowledge-digest |
| **P4** | ON-REQUEST | Sur commande explicite | Fichiers volumineux, dépendances externes |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Configuration par agent

Ajouter dans le frontmatter de chaque agent :

```yaml
---
name: "go-expert"
context_budget:
  strategy: "priority"
  max_tokens_estimate: 50000
  always_load:
    - "{project-root}/_grimoire/_config/custom/agent-base.md"
    - "{project-root}/_grimoire/_memory/shared-context.md"
  session_load:
    - "{project-root}/_grimoire/_memory/decisions-log.md"
    - "{project-root}/_grimoire/_memory/agent-learnings/go-backend.md"
    - "{project-root}/_grimoire/_memory/failure-museum.md"            # si > 2 semaines
  task_load:
    - "{current_story_file}"
    - "{related_adrs}"
  lazy_load:
    - "{project-root}/_grimoire/_memory/knowledge-digest.md"
    - "{project-root}/_grimoire/_memory/archives/"
---
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Règles de chargement dans agent-base.md

Les agents base.md héritent déjà de ce protocole via la règle LAZY-LOAD existante. Le Context Budget Router formalise et étend cette règle :

```
CONTEXT BUDGET RULES :
1. Calculer les tokens estimés des fichiers P0+P1 → si > 60% du budget → passer P1 en lazy
2. Repo Map → toujours LAZY (P3) sauf commande [RM] explicite
3. Failure Museum → P1 si projet > 2 semaines, sinon P3
4. Archives → jamais chargées automatiquement (P4)
5. Si contexte sature → résumer les sections les moins récentes via [THINK] → stocker le résumé → décharger l'original
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Agent Rules Resolution (BM-25)

En P0 (avant tout autre chargement), l'agent résout la chaîne `.agent-rules` :

```
AGENT RULES RESOLUTION PROTOCOL (P0 — avant toute action) :
1. Identifier le dossier courant de travail (activé par la tâche ou story)
2. Chercher .agent-rules dans le dossier courant → puis dans chaque parent → jusqu'à la racine projet
3. Merger toutes les règles trouvées : enfant surcharge parent (même id), union sinon
4. Pour chaque règle avec enforcement: "hard" → afficher un WARN PROMINENT avant la première action
5. Ajouter les auto_load[] de tous les .agent-rules au chargement P1 (SESSION)
6. Afficher les reminders[] du .agent-rules le plus proche
7. Vérifier tools_required[] du DNA actif → si check_command échoue et required: true → STOP + expliquer
```

Référence complète : [framework/agent-rules.md](agent-rules.md)

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/chart.svg" width="28" height="28" alt=""> Estimation de tokens

Règle approximative pour guidance des agents :

| Type de fichier | Tokens estimés |
|----------------|---------------|
| `agent-base.md` (complet) | ~3 000 |
| `shared-context.md` (infra moyenne) | ~2 000–5 000 |
| `decisions-log.md` (50 entrées) | ~2 000 |
| Fichier source Go/TS moyen (200 lignes) | ~1 500 |
| `repo-map.md` (projet 100 fichiers) | ~8 000 |
| `failure-museum.md` | ~1 500 |
| Story complète | ~500–1 000 |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/brain.svg" width="28" height="28" alt=""> Context Overflow Protocol

Quand un agent détecte un dépassement potentiel :

```
⚠️ CONTEXT BUDGET WARNING
Budget estimé : {used}/{max} tokens
Fichiers chargés : {list}
Action : Je vais résumer les sections > 30 jours dans decisions-log et knowledge-digest
avant de continuer. [THINK] mode activé.
```

Puis l'agent :
1. Résume les sections anciennes en 3-5 bullets
2. Archivage dans `_memory/archives/digest-{date}.md`
3. Remplace la section originale par le résumé
4. Continue avec le budget libéré

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration avec Session Branching

Chaque branche de session a son propre budget indépendant. Le router ne charge **pas** les artefacts des autres branches sauf sur `[cherry-pick]` explicite.

```yaml
# state.json (par branche)
{
  "branch": "feature-auth",
  "context_snapshot": {
    "loaded_files": [...],
    "estimated_tokens_used": 28000,
    "lazy_available": [...]
  }
}
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/brain.svg" width="28" height="28" alt=""> Configuration globale dans `project-context.yaml`

```yaml
context_budget:
  default_max_tokens: 80000      # Claude Sonnet : 200k, GPT-4o : 128k
  warning_threshold: 0.75        # alerte à 75% utilisé
  overflow_strategy: "summarize" # summarize | drop-oldest | ask-user
  repo_map_threshold: 0.50       # charger repo-map seulement si < 50% budget utilisé
```
