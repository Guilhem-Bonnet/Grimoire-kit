# Intégration du standard agentique

Grimoire Kit ne remplace pas le corpus normatif agentique. Il sert de **kit consommable** pour appliquer ce corpus dans un projet réel : profils, templates, registres, limites d'outils, preuves et déclarations de conformité.

## Positionnement

| Surface | Responsabilité | Ne doit pas faire |
|---|---|---|
| Corpus normatif externe | Définit les obligations, contrôles, patterns et profils | Dépendre de Grimoire Kit |
| Grimoire Forge | Assemble et génère un kit cible à partir du profil choisi | Modifier la norme pendant une génération |
| Grimoire Kit | Fournit les artefacts exécutables ou copiables dans les projets | Se déclarer source normative |

Le pont vit dans :

- `framework/agentic-standard/profile-map.yaml`
- `framework/agentic-standard/templates/`
- `archetypes/agentic-standard/`

## Profils de conformité opérationnelle

| Profil | Usage | Artefacts minimaux |
|---|---|---|
| `starter` | Individu ou petit projet qui veut un flow standard-aware léger | Mission Brief, Task Envelope, Evidence Pack |
| `controlled` | Équipe qui veut gouvernance répétable et routage LLM explicite | Starter + LLM Provider Registry + Compliance Declaration |
| `orchestrated` | Multi-agents avec contexte avancé et documentation externe indexée | Controlled + Knowledge Source Registry |
| `governed` | Organisation avec politiques par environnement et audit | Orchestrated + preuves d'isolation, télémétrie, guardrails |
| `production` | Flow critique avec dry-run, rollback, SLO et coûts | Governed + preuves de release gates et métriques critiques |

## Knowledge Base Indexer

La base de connaissance indexée est volontairement séparée de la mémoire :

- **Mémoire** : apprentissage persistant sur le projet, décisions, erreurs et signaux d'usage.
- **Contexte de session** : informations bornées injectées pour une tâche précise.
- **Base de connaissance** : documentation externe indexée depuis dossier, dépôt, URL, API, MCP, base de données ou stockage.

Un projet déclare ses sources dans `knowledge-source-registry.yaml`. Une source indexée n'est source de vérité que si elle est explicitement marquée comme telle pour le périmètre concerné.

## Compatibilité multi-provider LLM

Le flow ne doit pas dépendre implicitement d'un fournisseur unique. Le registre `llm-provider-registry.yaml` déclare :

- providers activés : GitHub Copilot, OpenAI/Codex, Anthropic Claude, Gemini, local, etc. ;
- capabilities autorisées : chat, code, reasoning, embeddings, multimodal ;
- politiques de données ;
- fallback chain ;
- métadonnées d'audit.

La règle est simple : pas d'appel récurrent à un provider ou modèle non déclaré.

Le choix provider est maintenant explicite au moment de l'initialisation :

```bash
grimoire standard detect-providers
grimoire standard init . --profile orchestrated --provider github-copilot
grimoire standard init . --profile orchestrated --providers github-copilot,anthropic,openai --provider-policy mixed
```

La détection ne lit pas les secrets. Elle ne remonte que des signaux non sensibles comme la présence d'un exécutable (`gh`, `codex`, `claude`, `gemini`, `ollama`) ou le fait qu'une variable d'environnement connue soit définie.

## Installation dans un projet cible

```bash
grimoire init . --archetype minimal
grimoire-init.sh install --archetype agentic-standard
```

Ensuite, générer les artefacts selon le profil :

```bash
grimoire standard init . --profile orchestrated --provider github-copilot
grimoire standard verify . --profile orchestrated
grimoire standard audit . --profile orchestrated --markdown
```

Le profil choisi détermine les artefacts requis :

```text
_grimoire/standard/mission-brief.md
_grimoire/standard/compliance-declaration.md
_grimoire/standard/knowledge-source-registry.yaml
_grimoire/standard/llm-provider-registry.yaml
_grimoire-output/evidence/{task-id}/task-envelope.md
_grimoire-output/evidence/{task-id}/evidence-pack.md
```

## Ce qui est maintenant prêt

Le kit possède une première structure pour transformer le standard en flow actionnable sans polluer le corpus normatif :

1. cartographie profils -> artefacts ;
2. archétype installable `agentic-standard` ;
3. templates de mission, tâche, preuve, conformité, knowledge sources et providers ;
4. distinction explicite mémoire / contexte / base de connaissance ;
5. compatibilité provider-first pour Copilot, Codex/OpenAI, Claude, Gemini et modèles locaux.

## Limites actuelles

- Le registry provider est audité et vérifié, mais pas encore branché à un routeur runtime.
- Les gates d'évidence sont visibles dans le task envelope et l'evidence pack, mais pas encore imposées par une FSM d'exécution.
- Les sources knowledge sont déclarées ; le manifeste d'index automatique et le doc-to-graph pipeline restent à brancher.

## Étendre les profils

Les profils livrés par défaut vivent dans `framework/agentic-standard/profile-map.yaml`. Pour créer un profil projet ou organisation :

1. ajouter une entrée dans `profiles` avec un `id`, des `required_artifacts`, des `mapped_capabilities` et du `minimum_evidence` ;
2. déclarer tout nouveau type d'artefact dans `artifact_types` avec un template associé ;
3. ajouter sa destination dans `generation_targets` si l'artefact doit être généré ;
4. versionner les templates custom avec les autres artefacts de gouvernance.

La commande `standard init` ne remplace pas les artefacts existants sauf avec `--force`, ce qui permet de faire évoluer la carte de profils sans écraser une baseline projet déjà remplie.
