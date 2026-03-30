---
description: 'Auto-diagnostic et réparation Grimoire — identifie et corrige les problèmes courants'
agent: 'agent'
tools: ['read', 'search', 'execute', 'edit']
---

Diagnostique et tente de réparer les problèmes du projet Grimoire.

## 1. Scan des problèmes

Vérifie les points suivants et liste les problèmes trouvés :

### Configuration
- `project-context.yaml` existe et est un YAML valide ?
- `_grimoire/_memory/config.yaml` contient `user_name` et `communication_language` ?

### Agents
- `.github/agents/` existe et contient des `.agent.md` ?
- Les fichiers agents ont tous un frontmatter YAML avec `description:` ?
- Les agents dans `.github/agents/` pointent vers des fichiers existants dans `_grimoire/_config/custom/agents/` ?

### Mémoire
- `_grimoire/_memory/shared-context.md` existe et n'est pas vide ?
- Pas de fichiers JSON malformés dans `_grimoire/_memory/` ?

### Structure
- `_grimoire/_config/custom/agents/` contient des agents ?
- `_grimoire-output/` existe ?

## 2. Triage des problèmes

Pour chaque problème trouvé, classifie-le :
- 🔴 **CRITIQUE** — bloque le fonctionnement des agents
- 🟡 **AVERTISSEMENT** — dégrade l'expérience mais ne bloque pas
- 🔵 **INFO** — amélioration possible

## 3. Réparations automatiques

Pour les problèmes **critiques** simples, propose et applique :
- Créer un `shared-context.md` vide si manquant
- Ajouter un frontmatter YAML minimal à un agent sans description
- Créer les dossiers manquants

Pour les problèmes complexes, fournis des instructions précises à l'utilisateur.

## 4. Rapport

```
🔧 GRIMOIRE SELF-HEAL REPORT
════════════════════════════

Problèmes trouvés : [n]
  🔴 Critiques   : [n]
  🟡 Avertissements : [n]
  🔵 Infos       : [n]

Réparations appliquées : [n]
  ✅ [action appliquée]
  ...

Actions manuelles requises :
  1. [instruction précise]
  ...
```
