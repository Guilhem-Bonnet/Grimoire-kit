---
description: 'Tableau de bord Grimoire — agents actifs, mémoire, activité récente, état projet'
agent: 'agent'
tools: ['read', 'search']
---

Affiche le tableau de bord Grimoire du projet :

## Collecte des données

1. Lis `{project-root}/project-context.yaml`
2. Lis `{project-root}/_grimoire/_config/agent-manifest.csv`
3. Lis `{project-root}/_grimoire/_memory/config.yaml`
4. Lis `{project-root}/_grimoire/_memory/session-state.md`
5. Liste les fichiers dans `.github/agents/` (agents VS Code actifs)
6. Liste les fichiers dans `.github/prompts/` (workflows VS Code actifs)
7. Lis les 10 premières lignes de `_grimoire/_memory/decisions-log.md`

## Tableau de bord

```
╔════════════════════════════════════════════╗
║         GRIMOIRE STATUS BOARD              ║
╚════════════════════════════════════════════╝

🏗️  PROJET : [nom] | Archétype : [type] | Stack : [stack]

👤 UTILISATEUR : [nom] | Langue : [langue]

🤖 AGENTS ACTIFS ([n] agents)
  • [nom-agent] — [description courte]
  • ...

📋 WORKFLOWS DISPONIBLES ([n] prompts)
  • /grimoire-session-bootstrap  — Bootstrap session
  • /grimoire-health-check       — Health check
  • /grimoire-dream              — Dream mode
  • /grimoire-pre-push           — Validation pre-push
  • /grimoire-changelog          — Générer changelog
  • [autres prompts listés...]

🧠 MÉMOIRE
  Backend    : [backend]
  Estado     : [summary of session-state]

📅 DÉCISIONS RÉCENTES
  • [dernières décisions depuis decisions-log]

💡 ACTIONS SUGGÉRÉES
  1. [action prioritaire si des problèmes sont détectés]
  2. ...
```
