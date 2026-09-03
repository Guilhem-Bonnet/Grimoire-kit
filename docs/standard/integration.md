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

## Version du standard tracée

Le bridge ne redéfinit pas la norme : il la trace. Pour que « aligné sur le
standard » soit vérifiable, `profile-map.yaml` épingle la révision exacte du
corpus normatif contre laquelle il a été réconcilié :

```yaml
metadata:
  upstream_standard:
    remote: "https://github.com/Guilhem-Bonnet/processus-developpement-agentique.git"
    commit: "53b2c34258580bd965631bcc186b21947b44c71e"
    pinned_on: "2026-06-12"
```

`grimoire standard upstream` compare cette révision à la tête distante :

| Sortie | Sens |
|---|---|
| 0 | la révision épinglée est la tête distante |
| 2 | le standard a avancé — réconcilier le profile-map, puis mettre à jour `commit` |
| 3 | distant injoignable — non vérifié, ce qui n'est pas « à jour » |
| 1 | aucune révision épinglée |

La CI du bridge exécute cette vérification en avertissement : une dérive du
corpus est un signal à traiter, pas une raison de refuser un commit qui ne
l'a pas causée.

## Traçabilité vers la norme

`framework/agentic-standard/traceability.yaml` relie chaque artefact du bridge
aux exigences `AG-*` et aux contrôles `CTRL-*` de la norme, avec la citation
qui justifie le lien, et chaque profil du kit à un niveau de conformité
(`starter` → N1 … `production` → N5). Un lien n'est posé que si la norme nomme
l'objet que l'artefact produit ; une entrée sans lien dit pourquoi.

```bash
grimoire standard traceability --profile governed
grimoire -o json standard traceability
```

La commande rend, pour les artefacts requis par le profil, les exigences et
contrôles satisfaits, puis les exigences obligatoires jusqu'au niveau atteint
que le kit ne couvre par aucun artefact — les trous, cumulés de N1 au niveau
demandé. C'est la matrice qu'AG-AUD-001 exige : une conformité déclarée reliée
à exigence, contrôle et preuve, plutôt qu'affirmée.

## Artefacts obligatoires de la norme

Deux artefacts que la norme exige et que les profils livrent désormais :

| Artefact | Exigence | Profils | Fichier |
|---|---|---|---|
| Claim ledger | AG-QUA-002 — une affirmation sans preuve reste une hypothèse | tous | `_grimoire-output/evidence/<task>/claim-ledger.md` |
| Registre des surfaces runtime | AG-TOL-007, AG-RET-006 — owner, mode, rétention, statut par surface | `governed`, `production` | `_grimoire/standard/runtime-surface-registry.yaml` |

`grimoire standard verify` les lit. Un registre vierge est un avertissement :
il attend d'être rempli. Une affirmation marquée `prouvé` sans source, une
affirmation `utiliser` qui n'est pas prouvée en profil gouverné, une surface de
contrôle sans owner : erreurs. Un projet déjà enrôlé reçoit les deux fichiers
par `grimoire standard fix --apply`.

## Profils de conformité opérationnelle

| Profil | Usage | Artefacts minimaux |
|---|---|---|
| `starter` | Individu ou petit projet qui veut un flow standard-aware léger | Mission Brief, Task Envelope, Evidence Pack |
| `controlled` | Équipe qui veut gouvernance répétable et routage LLM explicite | Starter + LLM Provider Registry + Compliance Declaration |
| `orchestrated` | Multi-agents avec contexte avancé et documentation externe indexée | Controlled + board, mémoire, contexte, décisions, rules/hooks, orchestration, evidence gates, patterns |
| `governed` | Organisation avec politiques par environnement et audit | Orchestrated + score, remediation, risques acceptés et waivers |
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
grimoire init . -a minimal,agentic-standard
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
_grimoire/standard/task-board.yaml
_grimoire/standard/memory-policy.yaml
_grimoire/standard/context-contract.yaml
_grimoire/standard/decision-graph.yaml
_grimoire/standard/rule-packs.yaml
_grimoire/standard/hook-registry.yaml
_grimoire/standard/orchestration-policy.yaml
_grimoire/standard/evidence-gates.yaml
_grimoire/standard/pattern-catalog.yaml
_grimoire-output/evidence/{task-id}/task-envelope.md
_grimoire-output/evidence/{task-id}/evidence-pack.md
```

### Activation de session (Claude Code)

`grimoire standard init` installe par défaut un hook `SessionStart`
Claude Code (`.claude/settings.json`) qui injecte la directive
d'activation au démarrage de chaque session — le mécanisme validé
40/40 par la campagne d'évals du 2026-07-09 (`evals/reports/`), là où
les artefacts seuls produisaient 0/40 d'engagement :

- la directive vit dans `.claude/activation-context.md` (éditable par
  projet, jamais écrasée si présente) ;
- le hook exécute `grimoire standard activation-context`, portable et
  versionné avec le kit ;
- fusion non destructive dans un `settings.json` existant ; un fichier
  malformé est laissé intact (le hook est alors sauté avec un
  avertissement) ;
- opt-out : `grimoire standard init . --no-claude-hook`.

## Commandes runtime normatives

Les profils `orchestrated`, `governed` et `production` ne se limitent plus aux templates de gouvernance : ils exposent une première tranche exécutable du runtime standard.

```bash
grimoire standard board verify .
grimoire standard memory verify .
grimoire standard context verify .
grimoire standard context build . --task-id bootstrap
grimoire standard decision trace . --task-id bootstrap
grimoire standard decision explain . --task-id bootstrap
grimoire standard rules verify .
grimoire standard hooks verify .
grimoire standard hooks simulate . --phase pre_context_build --task-id bootstrap
grimoire standard gate check . --task-id bootstrap --target-state review
grimoire standard gate check . --task-id bootstrap --target-state released --profile governed --strict
grimoire standard knowledge index . --task-id bootstrap
grimoire standard knowledge graph . --task-id bootstrap
grimoire standard knowledge verify . --task-id bootstrap
grimoire standard pattern list .
grimoire standard pattern show advanced-context-orchestrator .
grimoire standard events audit .
grimoire standard score . --task-id bootstrap
grimoire standard fix . --dry-run
grimoire standard fix . --apply
```

`grimoire standard memory verify` vérifie aussi le contrat Memory OS cible généré dans
`_grimoire/standard/memory-policy.yaml` : Redis reste la mémoire chaude TTL/streams/locks,
Weaviate devient la mémoire sémantique durable, Neo4j la projection graphe typée,
SQLite le sidecar/fallback local et Qdrant une source legacy de migration/rollback.
Les profils `governed` et `production` traitent une dérive de ce contrat comme une
erreur bloquante dans `standard gate check --strict`.

Les sorties opérationnelles restent dans `_grimoire-output/` :

- `context/{task-id}/context-bundle.yaml` : sources sélectionnées, mémoire injectée, contrat Memory OS, contraintes providers, routage provider évalué, redactions et preuves attendues ;
- `decisions/{task-id}/decision-trace.yaml` : traces task/context/memory/provider/agent/tool/state/release ;
- `knowledge/{task-id}/index-manifest.yaml` : sources, artefacts normatifs, patterns et checks ;
- `knowledge/{task-id}/knowledge-graph.yaml` : graphe local doc-to-graph des artefacts, sources folder autorisées et patterns ;
- `events/runtime-journal.jsonl` : journal des événements context, decision, knowledge, hooks, gates et score ;
- `events/applied-fixes.jsonl` : audit trail des remédiations sûres appliquées ;
- `standard/{task-id}/compliance-score.yaml` : score profil-aware avec dimensions pondérées.

Les commandes sont volontairement sûres : la simulation de hooks n'exécute aucune action externe, la remediation reste en dry-run par défaut et les chemins générés sont contraints au project root.

## Ce qui est maintenant prêt

Le kit possède une première structure pour transformer le standard en flow actionnable sans polluer le corpus normatif :

1. cartographie profils -> artefacts ;
2. archétype installable `agentic-standard` ;
3. templates de mission, tâche, preuve, conformité, knowledge sources et providers ;
4. templates runtime pour board, mémoire, contexte, décisions, rules/hooks, orchestration, evidence gates et patterns ;
5. distinction explicite mémoire / contexte / base de connaissance ;
6. compatibilité provider-first pour Copilot, Codex/OpenAI, Claude, Gemini et modèles locaux ;
7. génération de context bundle, decision trace, knowledge index/graph, hook simulation, gate check strict, event audit, score dimensionnel et remediation sûre.

## Limites actuelles

- Le registry provider est audité, vérifié et évalué dans le context bundle ; le branchement aux appels providers réels reste l'étape suivante.
- Les gates d'évidence peuvent bloquer explicitement les profils `governed`/`production` avec `standard gate check --strict`; l'intégration CI de chaque projet doit appeler cette commande.
- Le knowledge graph indexe les artefacts locaux et sources `folder` autorisées ; les connecteurs MCP, URL, base de données et vector store restent à brancher.

## Étendre les profils

Les profils livrés par défaut vivent dans `framework/agentic-standard/profile-map.yaml`. Pour créer un profil projet ou organisation :

1. ajouter une entrée dans `profiles` avec un `id`, des `required_artifacts`, des `mapped_capabilities` et du `minimum_evidence` ;
2. déclarer tout nouveau type d'artefact dans `artifact_types` avec un template associé ;
3. ajouter sa destination dans `generation_targets` si l'artefact doit être généré ;
4. versionner les templates custom avec les autres artefacts de gouvernance.

La commande `standard init` ne remplace pas les artefacts existants sauf avec `--force`, ce qui permet de faire évoluer la carte de profils sans écraser une baseline projet déjà remplie.

## Cible d’évolution

Le standard agentique a maintenant une cible de runtime normatif plus large :

- [Plan cible du runtime normatif agentique](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/planning/agentic-standard-target-plan.md) (document de travail, non publié)
- [Schéma et documentation cible du standard agentique](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/planning/agentic-standard-target-architecture.md) (document de travail, non publié)

Le contrat machine-readable associé est versionné dans `framework/agentic-standard/target-schema.yaml`.
