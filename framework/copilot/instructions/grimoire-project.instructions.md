---
description: "Conventions du projet Grimoire. Chargé automatiquement pour tous les fichiers. Apply when: agent activation, memory files, project context, grimoire workflows, any project file."
applyTo: "**"
---

# Conventions Grimoire — {{project_name}}

## Contexte Projet

Ce projet utilise **Grimoire Kit** — une plateforme d'agents IA composables.

### Règle fondamentale

Avant toute action, charger :
- `{project-root}/_grimoire/_memory/shared-context.md` — contexte du projet
- `{project-root}/_grimoire/_memory/config.yaml` — `user_name` et `communication_language`

### Structure clé

| Dossier | Rôle |
|---------|------|
| `_grimoire/_config/custom/agents/` | Agents IA internes (persona + instructions) |
| `.github/agents/` | Wrappers VS Code Copilot (auto-générés) |
| `.github/prompts/` | Workflows disponibles via `/` dans Copilot Chat |
| `_grimoire/_memory/` | Mémoire persistante inter-sessions |
| `_grimoire-output/` | Outputs des agents (lecture seule) |

## Conventions de Communication

- Toujours répondre en {{language}} (configuré dans `_grimoire/_memory/config.yaml`)
- S'adresser à l'utilisateur par son prénom (`user_name` dans config)
- Éviter le jargon technique non nécessaire

## Mémoire Partagée

- **decisions-log.md** — logguer toute décision architecturale significative
- **failure-museum.md** — documenter les erreurs résolues pour éviter la récidive
- **shared-context.md** — source de vérité sur le projet (toujours à jour)

## Agents et Routing

- L'agent `concierge` est le point d'entrée unique — il route vers les spécialistes
- Les agents communiquent via `_grimoire/_memory/handoff-log.md`
- Chaque agent doit charger `_grimoire/_config/custom/agent-base.md` au démarrage

## Anti-patterns

- ❌ Ne jamais deviner des informations non présentes dans `shared-context.md`
- ❌ Ne jamais modifier les fichiers dans `.github/agents/` manuellement
- ❌ Ne jamais supposer le stack technique — le lire depuis `project-context.yaml`
- ❌ Ne jamais commiter des secrets ou tokens

## Workflows Rapides

| Prompt | Usage |
|--------|-------|
| `/grimoire-session-bootstrap` | Reprendre le travail après une pause |
| `/grimoire-health-check` | Vérifier la santé du projet |
| `/grimoire-dream` | Consolidation et insights hors-session |
| `/grimoire-pre-push` | Validation avant push |
| `/grimoire-changelog` | Générer les notes de version |
| `/grimoire-status` | Tableau de bord rapide |
| `/grimoire-self-heal` | Réparer les problèmes courants |

## Guide des Workflows

### Pour bien démarrer

1. **Premier accès** → `/grimoire-session-bootstrap` (charge contexte)
2. **Vérification rapide** → `/grimoire-health-check` (diagnostique)
3. **Pendant le travail** → `/grimoire-status` (vue d'ensemble)
4. **Avant commit** → `/grimoire-pre-push` (validation)
5. **Fin de journée** → `/grimoire-dream` (insights)

### En cas de problème

- Erreur pas claire → `/grimoire-health-check`
- Système cassé → `/grimoire-self-heal`
- Besoin de contexte → charger `_grimoire/_memory/shared-context.md`

## Activation d'un Agent

Quand un agent est activé, il doit suivre ce protocole :

1. ✅ **Charger le contexte** : `_grimoire/_memory/shared-context.md`
2. ✅ **Charger la config** : `_grimoire/_memory/config.yaml`
3. ✅ **Charger agent-base** : `_grimoire/_config/custom/agent-base.md`
4. ✅ **Suivre les instructions** : de ce fichier exactement
5. ✅ **Appliquer la persona** : jamais sortir de rôle

## Complétion Contract

Avant chaque commit, valider :

```bash
bash _grimoire/_config/custom/cc-verify.sh
```

Cela vérifie :
- ✅ Contexte à jour
- ✅ Memory cohérente
- ✅ Pas de secrets
- ✅ Decisions loggées
- ✅ Failures documentées
