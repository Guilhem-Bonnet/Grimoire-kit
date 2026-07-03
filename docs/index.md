---
hide:
  - navigation
  - toc
---

# Grimoire Kit

<div class="kit-hero" markdown>
<div class="kit-eyebrow">SDK Python · Agents · Workflows · Mémoire</div>

**Le système de travail agentique pour cadrer, exécuter, vérifier et reprendre sans repartir de zéro.**

<span class="kit-badge"><span class="kit-dot"></span>v3 stable</span>

<div class="kit-hero-tagline">
Transformez votre IDE en espace de travail orchestré : rôles spécialisés, mémoire projet, handoffs automatiques et preuves réutilisables.
</div>

<div class="kit-stats" markdown>
<div class="kit-stat" markdown><strong>640+</strong><span>tests</span></div>
<div class="kit-stat" markdown><strong>20+</strong><span>agents</span></div>
<div class="kit-stat" markdown><strong>5</strong><span>archétypes</span></div>
<div class="kit-stat" markdown><strong>MCP</strong><span>intégré</span></div>
</div>
</div>

---

## Commencer ici

<div class="grid cards" markdown>

- :material-rocket-launch-outline: **Guide de démarrage**

    ---
    Installation, premier projet et parcours progressif. Cas "nouveau projet" et "projet existant" couverts.

    [:octicons-arrow-right-24: Guide de démarrage](getting-started.md)

- :material-book-open-outline: **Concepts**

    ---
    Architecture SOG, système de mémoire, workflow engine et taxonomie des patterns d'orchestration.

    [:octicons-arrow-right-24: Architecture](concepts.md)

- :material-code-braces: **Guide SDK Python**

    ---
    API complète, types, backends mémoire (JSON, Qdrant, Ollama) et intégration MCP.

    [:octicons-arrow-right-24: SDK Guide](sdk-guide.md)

- :material-account-hard-hat-outline: **Créer un agent**

    ---
    Guide pas-à-pas pour définir un agent avec rôle, mémoire, handoffs et règles qualité.

    [:octicons-arrow-right-24: Créer un agent](creating-agents.md)

</div>

---

## Fonctionnalités clés

<div class="grid" markdown>

<div class="card" markdown>

### :material-account-group-outline: Équipes & rôles

Agents spécialisés avec responsabilités distinctes, règles métier et mémoire dédiée. SOG — Smart Orchestrator Gateway — route les requêtes vers le bon expert sans friction utilisateur.

</div>

<div class="card" markdown>

### :material-brain: Mémoire sémantique

Contexte persistant entre sessions. Backends interchangeables : JSON local, Qdrant vectoriel, Ollama offline. L'orchestrateur sait ce qui a été fait, pourquoi, et par qui.

</div>

<div class="card" markdown>

### :material-sitemap: Workflows composables

Enchaînements d'agents avec handoffs automatiques. Engine YAML avec steps JIT, preuves par step, et reprise après interruption.

</div>

<div class="card" markdown>

### :material-shield-check-outline: Qualité intégrée

Lint, preflight et harmony-check natifs. Hooks de validation déterministes sur chaque edit. Guardrails runtime pour prévenir les mutations dangereuses.

</div>

<div class="card" markdown>

### :material-cube-outline: Archétypes

Templates de projets prêts : `minimal` (base), `web-app`, `platform-engineering` (Platform & Infra), `creative-studio`, `game-dev`, `fix-loop`. `infra-ops` est auto-détecté pour Terraform/K8s/Ansible. Un seul `grimoire init` pour bootstrapper avec tout le contexte agentique.

</div>

<div class="card" markdown>

### :material-server-network: MCP natif

Serveur Model Context Protocol intégré. Expose les outils Grimoire à tout client MCP-compatible (VS Code, Claude Desktop, curseur…).

</div>

</div>

---

## Démarrage rapide

=== "Nouveau projet"

    ```bash
    pip install grimoire-kit
    grimoire init mon-projet
    cd mon-projet
    grimoire doctor
    ```

=== "Projet existant"

    ```bash
    pip install grimoire-kit
    cd mon-projet-existant
    grimoire init --existing
    grimoire doctor
    ```

=== "Depuis un archétype"

    ```bash
    pip install grimoire-kit
    grimoire init mon-projet --archetype web-app
    cd mon-projet
    grimoire doctor
    ```

---

## Architecture

```
grimoire-kit/
├── src/grimoire/          # SDK Python
│   ├── core/              # Config, Project, Scanner, Validator
│   ├── cli/               # CLI Typer (grimoire <cmd>)
│   ├── tools/             # HarmonyCheck, Preflight, MemoryLint
│   ├── memory/            # Backends (JSON · Qdrant · Ollama)
│   ├── mcp/               # Serveur MCP
│   └── registry/          # AgentRegistry, LocalRegistry
├── archetypes/            # Templates de projets
├── framework/tools/       # Outils CLI standalone
└── docs/                  # Cette documentation
```

---

## Signaux du moment

Pour le fil complet des évolutions : [Signaux & nouveautés →](signaux.md)

{{ grimoire_signals_home }}

---

## Référence rapide

| Ressource | Description |
|---|---|
| [Guide de démarrage](getting-started.md) | Installation et premier projet |
| [Concepts](concepts.md) | Architecture et principes SOG |
| [Référence YAML](grimoire-yaml-reference.md) | Configuration complète |
| [Guide SDK](sdk-guide.md) | API Python |
| [Créer un agent](creating-agents.md) | Guide pas-à-pas |
| [Archétypes](archetype-guide.md) | Templates de projets |
| [Intégration MCP](mcp-integration.md) | Model Context Protocol |
| [Guardrails runtime](grimoire-game-runtime-guardrails.md) | Gouvernance et trust |
| [Dépannage](troubleshooting.md) | Résolution de problèmes |
| [Changelog](changelog.md) | Historique des versions |
