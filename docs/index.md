# Grimoire Kit

**The missing operating system for AI agents in your IDE.**

Transformez votre IDE en entreprise virtuelle peuplée d'agents IA spécialisés.

---

## Nouveautés stratégiques

Ajouts récents à explorer en priorité:

{{ grimoire_signals_home }}

## Commencer ici

Si vous arrivez depuis le README, suivez ce parcours:

1. [Guide de demarrage](getting-started.md)
2. [Concepts](concepts.md)
3. [Configuration YAML](grimoire-yaml-reference.md)

## Fonctionnalites cles

- **Teams & Personas** — Agents spécialisés avec personnalités, règles et mémoire
- **Mémoire sémantique** — Contexte persistant entre sessions
- **Workflows composables** — Enchaînements d'agents avec handoffs automatiques
- **Qualité automatisée** — Lint, preflight, harmony-check intégrés
- **Self-Healing** — Diagnostic et reparation autonome des workflows
- **Archétypes** — Templates de projets prêts à l'emploi

## Quick Start

```bash
pip install grimoire-kit
grimoire init mon-projet
cd mon-projet
grimoire doctor
```

Consultez le [guide de demarrage](getting-started.md) pour le parcours complet et les cas "nouveau projet" vs "projet existant".

## Architecture

```text
grimoire-kit/
├── src/grimoire/       # SDK Python
│   ├── core/           # Config, Project, Scanner
│   ├── cli/            # Commandes Typer
│   ├── tools/          # Harmony, Preflight, Memory Lint…
│   ├── memory/         # Backends mémoire (local, Qdrant)
│   └── mcp/            # Serveur MCP
├── archetypes/         # Templates de projets
├── framework/tools/    # Outils CLI standalone
└── docs/               # Cette documentation
```

## Liens rapides

| Ressource | Description |
| --- | --- |
| [Guide de demarrage](getting-started.md) | Installation et premier projet en quelques minutes |
| [Concepts](concepts.md) | Architecture et principes |
| [Guardrails runtime Grimoire Game](grimoire-game-runtime-guardrails.md) | Gouvernance des mutations, trust et compatibilite du runtime |
| [SDK Guide](sdk-guide.md) | API Python complète |
| [Créer un agent](creating-agents.md) | Guide pas-à-pas |
| [Archétypes](archetype-guide.md) | Templates de projets |
| [MCP](mcp-integration.md) | Intégration Model Context Protocol |
| [Troubleshooting](troubleshooting.md) | Résolution de problèmes |
