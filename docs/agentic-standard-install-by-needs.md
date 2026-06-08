# Installation par besoins

Grimoire Kit propose une **installation custom pilotée par les besoins** : vous
déclarez ce que votre projet doit faire (RAG sémantique, graphe de connaissances,
cache de session chaud, gouvernance des hooks…), et le runtime résout
automatiquement le **profil**, les **patterns** à activer, les **artefacts** à
générer et les **technologies** (extras `pip`) à installer.

```text
Besoins (needs-catalog.yaml)
  → patterns requis (capability-map.yaml : pattern → artifacts + rules + checks + tech_extras + profile_min)
    → profil recommandé (max des profile_min) + union des artefacts + union des extras
      → scaffold ciblé + _grimoire/standard/install-manifest.yaml
        → commande tech : pip install 'grimoire-kit[redis,weaviate,neo4j]'
          → doctor : vérifie que les extras sont importables, sinon dégradation par pattern
```

Trois fichiers déclaratifs sous `framework/agentic-standard/` pilotent le tout :

- `needs-catalog.yaml` — les besoins exposés à l'utilisateur ;
- `capability-map.yaml` — la matrice pattern → exigences + technologies ;
- `install-manifest.yaml` — généré par projet, trace la sélection pour audit et ré-exécution.

## Commandes

| Commande | Rôle |
|---|---|
| `grimoire standard needs` | Liste les besoins disponibles et leurs patterns. |
| `grimoire standard plan --needs a,b,c` | Affiche le plan résolu (profil, patterns, extras, commande `pip`) sans rien écrire. |
| `grimoire standard init <dir> --needs a,b,c` | Résout le plan, scaffolde les artefacts, écrit `install-manifest.yaml` et imprime la commande `pip`. |
| `grimoire standard init <dir> --interactive` | Assistant interactif : sélection des besoins, aperçu du plan, confirmation. |
| `grimoire standard doctor` | Vérifie que les extras technologiques sélectionnés sont importables et signale la dégradation. |

Sélecteurs supplémentaires de `init`/`plan` :

- `--pattern <id>` — ajoute un pattern précis hors besoin ;
- `--memory <tier>` — ajoute une couche mémoire (`semantic-memory`, `graph-memory`, `hot-memory`, `legacy-migration`) ;
- `--profile <id>` — force le profil (sinon calculé comme le plus haut `profile_min`) ;
- `--install-extras` — exécute `pip install` best-effort (désactivé par défaut : la commande est seulement imprimée).

!!! note "Point d'entrée"
    La surface canonique est `grimoire standard init`. Le `grimoire init` de premier
    niveau reste l'initialisation d'archétype historique et n'est pas modifié.

### Exemple

```console
$ grimoire standard plan --needs semantic-memory-rag,hot-session-cache
profile: governed
patterns: governed-memory-policy, advanced-context-orchestrator, redis-hot-memory-soft-gate
tech_extras: redis, weaviate
pip: pip install 'grimoire-kit[redis,weaviate]'

$ grimoire standard init ./my-project --needs semantic-memory-rag
# scaffolde le profil orchestrated + artefacts patterns
# écrit ./my-project/_grimoire/standard/install-manifest.yaml
# imprime: pip install 'grimoire-kit[weaviate]'
```

## Catalogue de besoins

| Besoin | Description | Profil | Mémoire |
|---|---|---|---|
| `solo-prototyping` | Solo dev / quick prototype (minimal governance) | `starter` | — |
| `provider-neutral` | Provider-neutral / local-first LLM routing | `controlled` | — |
| `semantic-memory-rag` | Semantic memory / RAG over your documents | `orchestrated` | semantic-memory |
| `knowledge-graph` | Verifiable knowledge / code graph | `orchestrated` | graph-memory |
| `hot-session-cache` | Fast session / hot memory cache | `governed` | hot-memory |
| `multi-agent-orchestration` | Multiple agents with handoff / escalation | `orchestrated` | — |
| `tool-mediation-security` | Mediate tools / MCP calls (OWASP agentic threats) | `governed` | — |
| `hooks-skills-governance` | Govern hooks and skills (gateway + classification) | `governed` | — |
| `observability-cockpit` | Observability cockpit / governed dashboards | `governed` | — |
| `enterprise-governance` | Organization-grade governance (full controls) | `governed` | semantic-memory, graph-memory |
| `production-release-gating` | Production critical flow with release gates | `production` | semantic-memory, graph-memory, hot-memory |

## Matrice patterns → profil → technologie

| Pattern | Catégorie | Profil minimum | Extra technologique |
|---|---|---|---|
| `advanced-context-orchestrator` | context | `orchestrated` | — |
| `governed-memory-policy` | memory | `orchestrated` | — |
| `evidence-gated-fsm` | workflow | `starter` | — |
| `provider-routing-contract` | provider | `controlled` | — |
| `runtime-journal` | runtime | `governed` | — |
| `redis-hot-memory-soft-gate` | memory | `governed` | `redis` |
| `governed-hook-gateway` | governance | `governed` | — |
| `skill-classification-matrix` | orchestration | `governed` | — |
| `governed-observability-cockpit` | observability | `governed` | — |
| `code-graph-projection` | memory | `orchestrated` | `neo4j` |
| `governed-agent-orchestration` | orchestration | `orchestrated` | — |
| `governed-knowledge-indexing` | knowledge | `orchestrated` | — |
| `mission-evidence-ledger` | workflow | `governed` | — |
| `tool-mediation-gate` | security | `governed` | `mcp` |
| `provider-cost-slo` | provider | `production` | — |

## Technologies et dégradation

Aucune technologie externe n'est obligatoire : chaque extra absent **dégrade en
sécurité** vers une alternative locale. `grimoire standard doctor` détaille ce qui
est importable et la conséquence par pattern.

| Extra (`pip install 'grimoire-kit[...]'`) | Rôle | Dégradation si absent |
|---|---|---|
| `redis` | Mémoire chaude : TTL, leases, locks, streams/pubsub. Jamais source de vérité durable. | sidecar SQLite local |
| `weaviate` | Mémoire sémantique/vectorielle durable (backend cible). | sidecar SQLite local |
| `neo4j` | Projection graphe durable : mémoire, code, tâche, evidence, décision. | pas de projection graphe |
| `qdrant` | Backend vectoriel legacy, limité à la migration/rollback. | weaviate ou local |
| `mempalace` | Store vectoriel local expérimental (chromadb). | sidecar SQLite local |
| `ollama` | Provider LLM local pour routage local-first / hors-ligne. | routage provider hébergé |
| `mcp` | Serveur Model Context Protocol, outils et bridges. | pas de médiation outil MCP |

## Politique de sous-ensemble d'artefacts

- **`starter` / `controlled` / `orchestrated`** : les artefacts des patterns sélectionnés
  sont **additifs** au-dessus des `required_artifacts` du profil.
- **`governed` / `production`** : le jeu complet d'artefacts gouvernés est toujours généré ;
  la sélection ne pilote alors que les **extras technologiques** et les **patterns actifs**.
  Le principe « governed = tout » est préservé.

L'`install-manifest.yaml` généré conserve la sélection complète (besoins, patterns,
couches mémoire, extras, commande `pip`) pour rejouer ou auditer l'installation.
