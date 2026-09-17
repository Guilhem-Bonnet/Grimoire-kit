# Grimoire Kit

**The missing operating system for AI agents in your IDE.**

Transformez votre IDE en entreprise virtuelle peuplée d'agents IA spécialisés.

---

## Nouveautés stratégiques

Ajouts récents à explorer en priorité:

{{ grimoire_signals_home }}

## Commencer ici

Si vous arrivez depuis le README, suivez ce parcours:

1. [Guide de démarrage](getting-started.md)
2. [Concepts](concepts.md)
3. [Standard agentique gouverné](standard/integration.md) · [Contrôles gouvernés](standard/controles-gouvernes.md)
4. [Configuration YAML](grimoire-yaml-reference.md)

## Fonctionnalités clés

- **Standard agentique gouverné** — 36 patterns vérifiables *fail-closed* (`grimoire standard verify` / `audit` / `score` / `gate`), profils `starter → production`. Voir [Contrôles gouvernés](standard/controles-gouvernes.md).
- **Teams & Personas** — Agents spécialisés avec personnalités, règles et mémoire
- **Mémoire sémantique** — Contexte persistant entre sessions
- **Workflows composables** — Enchaînements d'agents avec handoffs automatiques
- **Qualité automatisée** — Lint, preflight, harmony-check intégrés
- **Self-Healing** — Diagnostic et réparation autonome des workflows
- **Archétypes** — Templates de projets prêts à l'emploi

## Quick Start

```bash
pip install grimoire-kit
grimoire init mon-projet
cd mon-projet
grimoire doctor
```

Consultez le [guide de démarrage](getting-started.md) pour le parcours complet et les cas "nouveau projet" vs "projet existant".

## Architecture

```text
grimoire-kit/
├── src/grimoire/       # SDK Python
│   ├── core/           # Config, Project, Scanner
│   ├── cli/            # Commandes Typer
│   ├── tools/          # Harmony, Preflight, Memory Lint…
│   ├── memory/         # Backends mémoire (local, Weaviate, Neo4j, Qdrant)
│   └── mcp/            # Serveur MCP
├── archetypes/         # Templates de projets
├── framework/tools/    # Outils CLI standalone
└── docs/               # Cette documentation
```

## Liens rapides

| Ressource | Description |
| --- | --- |
| [Guide de démarrage](getting-started.md) | Installation et premier projet en quelques minutes |
| [Concepts](concepts.md) | Architecture et principes |
| [Standard agentique](standard/integration.md) | Profils, patterns gouvernés, installation par besoins |
| [Contrôles gouvernés](standard/controles-gouvernes.md) | Référence des 36 patterns (`verify`/`audit`/`score`/`gate`) |
| [Guardrails runtime Grimoire Game](grimoire-game-runtime-guardrails.md) | Gouvernance des mutations, trust et compatibilité du runtime |
| [SDK Guide](sdk-guide.md) | API Python complète |
| [Créer un agent](creating-agents.md) | Guide pas-à-pas |
| [Archétypes](archetype-guide.md) | Templates de projets |
| [MCP](mcp-integration.md) | Intégration Model Context Protocol |
| [Troubleshooting](troubleshooting.md) | Résolution de problèmes |
| [Plan produit 2026-Q4](plan-2026-q4.md) | Cible, métriques mesurées, phases, matrice de parité, registre des risques |
| [Banc à trois bras](bench-three-arms.md) | Protocole Claude Code nu / + ecc / + grimoire-kit, rejouable (issue #551) |
| [Diagnostic du surcoût kit (2026-09-17)](bench/diagnostic-surcout-kit-2026-09-17.md) | Attribution du surcoût mesuré sur le banc, preuve des gates verts déconnectés des tests, lots de cœur |
| [Direction d'Anthropic — chevaucher/envelopper/ignorer](veille/anthropic-direction-2026-09.md) | Verdict par capacité Anthropic récente, lot du plan qui l'absorbe |
| [Idées à récolter — instruction et verdict](veille/idees-a-recolter-2026-09.md) | Adopter/adapter/écarter par idée concurrente, fichier source et licence |
