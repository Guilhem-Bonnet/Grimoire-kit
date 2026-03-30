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
