# Grimoire Kit

**The missing operating system for AI agents in your IDE.**

Transformez votre IDE en entreprise virtuelle peuplée d'agents IA spécialisés.

---

## Fonctionnalités clés

- **Teams & Personas** — Agents spécialisés avec personnalités, règles et mémoire
- **Mémoire sémantique** — Contexte persistant entre sessions
- **Workflows composables** — Enchaînements d'agents avec handoffs automatiques
- **Qualité automatisée** — Lint, preflight, harmony-check intégrés
- **Self-Healing** — Diagnostic et réparation autonomous des workflows
- **Archétypes** — Templates de projets prêts à l'emploi

## Quick Start

```bash
pip install grimoire-kit
grimoire init mon-projet
cd mon-projet
grimoire doctor
```

Consultez le [guide de démarrage](getting-started.md) pour les détails.

## Architecture

```
grimoire-kit/
├── src/grimoire/       # SDK Python
│   ├── core/           # Config, Project, Scanner
│   ├── cli/            # Commandes Typer
│   ├── tools/          # Harmony, Preflight, Memory Lint…
│   ├── memory/         # Backends mémoire (local, Qdrant)
│   └── mcp/            # Serveur MCP
├── archetypes/         # Templates de projets
├── framework/tools/    # 106 outils CLI standalone
└── docs/               # Cette documentation
```

## Liens rapides

| Ressource | Description |
|---|---|
| [Concepts](concepts.md) | Architecture et principes |
| [SDK Guide](sdk-guide.md) | API Python complète |
| [Créer un agent](creating-agents.md) | Guide pas-à-pas |
| [Archétypes](archetype-guide.md) | Templates de projets |
| [MCP](mcp-integration.md) | Intégration Model Context Protocol |
| [Troubleshooting](troubleshooting.md) | Résolution de problèmes |
