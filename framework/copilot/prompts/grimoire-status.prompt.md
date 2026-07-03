---
description: 'Tableau de bord Grimoire — agents actifs, mémoire, activité récente, état projet'
agent: 'agent'
tools: ['read', 'search']
---

Affiche le tableau de bord Grimoire du projet :

## Collecte des données

1. Lis `{project-root}/project-context.yaml`
2. Si présent, lis `{project-root}/_grimoire-runtime/_config/agent-surface-index.csv` pour distinguer agents actifs, aliases de compatibilité et archives. Si le fichier contient `catalogKind`, traite-le comme taxonomie canonique : `durable_agent`, `builder_utility`, `mode_profile`, `workflow_profile`, `output_style`, `compatibility_alias`. Sinon, fallback vers `{project-root}/_grimoire/_config/agent-manifest.csv` ou `{project-root}/_grimoire-runtime/_config/agent-manifest.csv` selon la surface disponible.
3. Lis `{project-root}/_grimoire/_memory/config.yaml`
4. Lis `{project-root}/_grimoire/_memory/session-state.md`
5. Liste les fichiers dans `.github/agents/` comme wrappers workspace actifs, mais ne compte comme agents de premier rang que les entrées `durable_agent`. Les `builder_utility`, `mode_profile`, `workflow_profile` et `output_style` doivent être affichés séparément. N'utilise `.github/agents/_archived/` qu'en cas d'investigation historique explicite.
6. Liste les fichiers dans `.github/prompts/` (workflows VS Code actifs)
7. Lis les 10 premières lignes de `_grimoire/_memory/decisions-log.md`

## Tableau de bord

```
╔════════════════════════════════════════════╗
║         GRIMOIRE STATUS BOARD              ║
╚════════════════════════════════════════════╝

🏗️  PROJET : [nom] | Archétype : [type] | Stack : [stack]

👤 UTILISATEUR : [nom] | Langue : [langue]

🤖 AGENTS DURABLES ACTIFS ([n] agents)
  • [nom-agent] — [description courte]
  • ...

🧰 UTILITAIRES BUILDERS ([n])
  • [nom-agent]
  • ...

🎛️ MODES / WORKFLOWS / STYLES ([n])
  • [nom-agent] — [kind]
  • ...

🧭 INDEX DE SURFACE
  Actifs          : [n]
  Compatibilité   : [n]
  Archivés        : [n]
  Source canonique: [agent-surface-index.csv ou manifest legacy]

🧱 TAXONOMIE
  Durables        : [n]
  Builders        : [n]
  Modes           : [n]
  Workflows       : [n]
  Styles          : [n]
  Aliases         : [n]

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

## Règles de présentation

- Si `agent-surface-index.csv` existe avec `catalogKind`, il est la source de vérité pour compter et présenter les agents.
- N'affiche jamais un `compatibility_alias` dans la liste des agents durables actifs.
- N'élève jamais un `mode_profile`, `workflow_profile` ou `output_style` au même rang qu'un `durable_agent`.
- Si `catalogKind` manque, signale que le tableau de bord repose sur une lecture legacy moins fiable.
