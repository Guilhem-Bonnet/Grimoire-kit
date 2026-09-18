---
kind: command
deprecated_by: 'grimoire doctor'
description: 'Health check complet du projet Grimoire — agents, mémoire, config, intégrité'
agent: 'agent'
tools: ['read', 'search', 'execute']
---

Health check complet du projet Grimoire en 5 points. Chaque ligne du rapport
cite ce qui a été lu ou exécuté ; un point non regardé s'écrit « non vérifié »,
jamais ✅ ni ❌.

## 0. Mesure

```bash
grimoire -o json doctor {project-root}
```

Les clés `passed` et `failed` de cette sortie sont la seule source du bilan
chiffré ci-dessous. Sans cette commande, pas de bilan chiffré.

## 1. Configuration

Vérifie `{project-root}/project-context.yaml` :
- Le fichier existe et est valide YAML
- Il contient `project.name`, `user.name`, `memory.backend`

## 2. Agents

Vérifie `{project-root}/.github/agents/` :
- Compte les fichiers `.agent.md` présents
- Vérifie que chaque fichier a un frontmatter YAML avec `description:`
- Signale les agents sans description ou avec frontmatter invalide

## 3. Mémoire

Vérifie les fichiers dans `{project-root}/_grimoire/_memory/` :
- `shared-context.md` — présent et non vide ?
- `decisions-log.md` — présent ?
- `failure-museum.md` — messages d'erreurs récurrents à résoudre ?
- `config.yaml` — `user_name` et `communication_language` configurés ?

Lis les 20 premières lignes de `failure-museum.md` pour identifier des erreurs non résolues.

## 4. Structure

Vérifie que les dossiers essentiels existent :
- `.github/agents/` — agents VS Code Copilot
- `.github/prompts/` — prompts VS Code Copilot
- `_grimoire/kit/agents/` — agents Grimoire internes
- `_grimoire-output/` — outputs de travail

## 5. Git

```bash
cd {project-root} && git status --short && git log --oneline -5
```

Présente un **rapport de santé** :
```
╔══════════════════════════════╗
║ Grimoire Health Check         ║
╚══════════════════════════════╝

✅/⚠️/❌  Configuration    ...
✅/⚠️/❌  Agents           X agents actifs
✅/⚠️/❌  Mémoire          ...
✅/⚠️/❌  Structure        ...
✅/⚠️/❌  Git              ...

Contrôles doctor : {passed} verts / {passed + failed} (copié de la sortie ci-dessus)

Actions prioritaires :
1. ...
```
