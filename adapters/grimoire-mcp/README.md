<!-- markdownlint-disable MD025 MD007 MD026 MD060 -->

# grimoire-mcp — prise universelle (adaptateur MCP)

> Phase 1 de la direction D (bridge agnostique). Expose le **coeur grimoire** comme serveur MCP.
> Consommable par tout harness MCP-natif : OpenCode, Claude Code, Cursor, Gemini CLI...
> Source de verite : [grimoire-core.manifest.yaml](../../framework/grimoire-core.manifest.yaml) — contrat : [adapter-contract.md](../../framework/adapter-contract.md).

## Ce que c'est

Un serveur MCP minimal (stdio) qui expose les **8 verbes neutres** du contrat v1 comme outils MCP :

| Outil MCP | Verbe | Rôle |
|---|---|---|
| `grimoire_list_skills` | `list_skills` | Liste les 44 skills (filtre par domaine) |
| `grimoire_run_skill` | `run_skill` | Retourne les instructions d'une skill |
| `grimoire_route` | `route` | Route une intention vers la skill pertinente (confiance + fallback) |
| `grimoire_orchestrate` | `orchestrate` | Décompose un objectif en plan de dispatch SOG (étapes + handoffs + questions) |
| `grimoire_validate` | `validate` | Vérifie une sortie (CVTL/HUP) : trust score + verdict, cross-check modèle opt-in |
| `grimoire_escalate_questions` | `escalate_questions` | Consolide un lot de questions (QEC) en un batch dédupliqué |
| `grimoire_remember` | `remember` | Persiste un fait dans la mémoire lexicale (Memory OS) |
| `grimoire_recall` | `recall` | Recherche lexicale dans la mémoire |

Surface **bornée** (8 verbes de haut niveau) par principe anti-bloat : le long tail des capacités passe par `list_skills` / `run_skill`, pas par un outil MCP par skill.

## Principe

Le coeur décide, l'adaptateur traduit. Ce serveur **ne contient aucune logique métier** : il lit les skills depuis `.github/skills/` et les domaines depuis le manifeste, puis applique les protocoles (intent-routing, CVTL/HUP) du coeur. Toute règle d'orchestration vit dans `grimoire-kit/framework/`, pas ici.

## Lancer

Prérequis : Node >= 22.6 (TypeScript strip-only natif, pas d'étape de build — philosophie « erasable »).

```bash
# Test du coeur (sans dépendance externe, hors-ligne)
npm run smoke           # -> ALL GREEN (coeur, 8 verbes)

# Installer les dépendances (MCP SDK + pi-ai)
npm install

# Test du chemin moteur pi-ai (via provider faux, sans clef API)
npm run itest           # handshake MCP réel (8 outils)
node --experimental-strip-types src/engine-test.ts   # cross-validation CVTL

# Démarrer le serveur
npm start               # -> grimoire-mcp v0.2.0 — 8 verbes — prêt (sur stderr)
```

Le serveur parle MCP sur stdio (stdout = canal MCP, stderr = logs).

## Brancher sur OpenCode

OpenCode est MCP-natif. Ajouter un serveur MCP local dans `opencode.json` :

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "grimoire": {
      "type": "local",
      "command": ["node", "--experimental-strip-types",
        "grimoire-kit/adapters/grimoire-mcp/src/server.ts"],
      "enabled": true
    }
  }
}
```

Les outils `grimoire_*` deviennent alors disponibles dans OpenCode. (Le même serveur se branche sur Claude Code via `claude_desktop_config.json` / `mcp add`, sur Cursor via sa config MCP, etc. — une implémentation, tous les hôtes.)

## Moteur de cross-validation (pi-ai)

Les verbes sont **déterministes par défaut** (aucun appel modèle, fonctionnent hors-ligne). `validate` peut en plus déclencher une **vraie seconde-opinion CVTL** via `@earendil-works/pi-ai` (39 providers, MIT) :

```bash
# Modèle réel (nécessite une clef provider en env, ex. ANTHROPIC_API_KEY)
export GRIMOIRE_MCP_ENGINE=pi-ai
export GRIMOIRE_MCP_PROVIDER=anthropic        # défaut
export GRIMOIRE_MCP_MODEL=claude-haiku-4-5    # défaut

# Mode test sans clef (provider faux pi-ai) — déterministe, utilisé par engine-test.ts
export GRIMOIRE_MCP_ENGINE=faux
```

Quand le moteur est actif, `validate` ajoute les affirmations non étayées détectées par le modèle (`cross_checked: true`) et fusionne sa confiance. En cas d'absence de clef ou d'erreur, **dégradation gracieuse** : retour au verdict déterministe (`cross_checked: false`). Le chemin est testé de bout en bout via le provider faux de pi-ai, sans clef.

Le choix d'un moteur-**librairie** (pi-ai) plutôt qu'un harness-produit garde l'adaptateur indépendant de toute politique produit (cf. thèse, risque R4).

## Mémoire (Memory OS lexical)

`remember` / `recall` utilisent un magasin **JSONL lexical** (sans DB vectorielle), aligné sur la direction mémoire du projet. Chemin par défaut : `_grimoire-runtime-output/grimoire-mcp/memory.jsonl`, surchargé par `GRIMOIRE_MCP_MEMORY`.

## Conformité (contrat v1)

- Source unique : skills lues depuis `.github/skills/`, domaines depuis le manifeste.
- Pas de logique métier locale.
- Surface MCP bornée (4 verbes).
- Provider-neutre (moteur opt-in = pi-ai).
- Couvre les 4 verbes minimaux requis : `list_skills`, `run_skill`, `route`, `validate`.

## Structure

```text
src/core.ts          loader du coeur (skills + manifeste) + tokenizer, zéro dépendance
src/verbs.ts         les 8 verbes
src/memory.ts        Memory OS lexical (JSONL)
src/engine.ts        cross-validation CVTL via pi-ai (+ provider faux)
src/server.ts        serveur MCP stdio (wrapper mince, 8 outils)
src/smoke.ts         test du coeur, hors-ligne
src/engine-test.ts   test du chemin pi-ai (via faux, sans clef)
src/itest.ts         test handshake MCP réel
```
