---
description: 'Bootstrap une nouvelle session Grimoire — contexte projet, historique, état git, santé'
agent: 'agent'
tools: ['read', 'search', 'execute']
---

Bootstrap une nouvelle session de travail Grimoire en 4 étapes :

## 1. Contexte projet

Lis `{project-root}/_grimoire/_memory/shared-context.md` pour comprendre le projet (nom, stack, conventions). Si absent, signale-le.
Si présent, lis aussi `{project-root}/_grimoire-runtime/_config/agent-surface-index.csv` pour identifier la surface agentique nominale avant toute lecture d'archives ou de wrappers legacy. Si `catalogKind` est disponible, utilise-le pour distinguer agents durables, builders, modes, profils de workflow, styles de sortie et aliases de compatibilité.

## 2. Historique récent

Lis `{project-root}/_grimoire/_memory/decisions-log.md` (section "Décisions récentes") et `{project-root}/_grimoire/_memory/session-state.md`. Identifie :
- Ce qui a été accompli récemment
- Ce qui était prévu mais non terminé
- Les décisions architecturales majeures

## 3. État git

```bash
cd {project-root} && git log --oneline -10 && git status --short
```

## 4. Santé du projet

Vérifie que les fichiers clés existent :
- `{project-root}/project-context.yaml`
- `{project-root}/_grimoire-runtime/_config/agent-surface-index.csv` si le runtime source `_grimoire-runtime/` existe, sinon `{project-root}/_grimoire/_config/agent-manifest.csv`
- `{project-root}/.github/agents/` (au moins un fichier `.agent.md`)

Si `agent-surface-index.csv` existe, utilise-le comme source de vérité pour distinguer les wrappers actifs, les aliases de compatibilité et les surfaces archivées. Si `catalogKind` est présent, considère que seuls les `durable_agent` décrivent le noyau agentique de premier rang; les autres catégories doivent être mentionnées à part. Ne consulte pas `.github/agents/_archived/` sauf si la santé du projet ou l'historique l'exige explicitement.

Présente un résumé concis en français :
- **Projet** : nom, type, stack
- **Dernières actions** : 3-5 décisions/changements récents
- **État git** : commits récents, fichiers modifiés
- **Surface agentique** : durables, builders, modes/styles, aliases si l'index les distingue
- **Santé** : ✅ tout OK ou ⚠️ problèmes détectés
- **Prochaine étape suggérée** : action prioritaire basée sur le contexte
