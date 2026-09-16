<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/microscope.svg" width="32" height="32" alt=""> Idées à récolter — instruction et verdict (2026-09)

> Lot 1.3 du [plan produit 2026-Q4](../plan-2026-q4.md#6-idées-à-récolter),
> issue [#551](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/551).
> Instruit les huit idées provisoires du plan (§6) avec un fichier source
> vérifié dans le dépôt concurrent et corrige deux erreurs trouvées en
> vérifiant (licence Mastra, absence d'un score de qualité côté kit), puis
> ajoute une neuvième idée trouvée en lisant SuperClaude qui n'était pas dans
> la liste initiale.
>
> Doctrine appliquée pour le verdict (`.github/agents/grimoire-master.agent.md`
> et `.github/copilot-instructions.md` du dépôt Forge, doctrine des
> artefacts) : différenciateur vérifiable obligatoire, pas de reprise brute
> de contenu tiers, pas de bruit. **Adopter** = le kit construit l'équivalent
> chez lui, sans import du format tiers. **Adapter** = le kit a déjà le
> mécanisme, on lui ajoute la pièce manquante. **Écarter** = hors segment ou
> déjà couvert par l'hôte natif.

<img src="../assets/divider.svg" width="100%" alt="">

## Tableau

| Idée | Source (fichier, dépôt) | Licence | Ce que le kit a de proche (fichier) | Verdict | Effort | Lot |
|---|---|---|---|---|---|---|
| **Packs de règles par langage** | `rules/<langue>/*.md` — 22 langages, ex. `rules/python/{coding-style,fastapi,hooks,patterns,security,testing}.md`, dépôt [affaan-m/ECC](https://github.com/affaan-m/ECC) (292 skills, 68 agents confirmés au comptage direct du dépôt le 2026-09-16) | MIT, attribution requise | 20 skills sur 10 archétypes (`archetypes/*/skills/*.md`) — pas de découpage par langage à l'intérieur d'un archétype | **Adapter** | L | 4.4 (bibliothèque skills gatée par stack) |
| **Mémoire Markdown portable multi-harnais** | Format `ecc.memory.v1`, trois scopes (`<repo>/.ecc/memory/project\|team/`, `~/.ecc/memory/`), écriture create-only, statut `trust: unreviewed`, doctor de validation — `skills/unified-memory/SKILL.md` et `docs/design/ecc-memory-vault.md`, [affaan-m/ECC](https://github.com/affaan-m/ECC) | MIT, attribution requise | 4 profils mémoire, backends riches partagés par DB (Qdrant, Weaviate, Neo4j, lexical) mais aucun export diffable en fichiers Markdown versionnables (`src/grimoire/memory/backends/manager.py`) | **Adopter** | M | 4 (couche d'export diffable par scope, format propre au kit — pas d'import direct du format `.ecc/memory/`) |
| **Scoring de confiance de l'apprentissage continu** | Barres de confiance 0–100 %, seuil plancher configurable, boost additif par proximité de projet/stack (`DEFAULT_PROJECT_SCOPE_BOOST = 0.25`, `DEFAULT_STACK_MATCH_BOOST = 0.2`) dans `scripts/lib/instinct-relevance.js`, commande `commands/instinct-status.md`, [affaan-m/ECC](https://github.com/affaan-m/ECC) | MIT, attribution requise | `agent-miss` → propositions : compte d'occurrences contre un seuil fixe, pas de score continu (`src/grimoire/proposals.py:598` `_configured_threshold`, `sync_proposals`) | **Adapter** | M | 4 (ajouter un score continu au-dessus du seuil existant, pas un second mécanisme parallèle) |
| **Règles scopées par dossier** | Fichiers `.mdc` sous `.cursor/rules/`, quatre modes d'activation (Always/Auto Attached par glob/Agent Requested/Manual) — [cursor.com/docs/rules](https://cursor.com/docs/rules) | Documentation propriétaire Cursor, aucun code repris | Émetteur Cursor actuel : un seul fichier `.cursor/rules/grimoire.mdc` (`src/grimoire/hosts/emitters/generic.py:28`), pas de scoping par glob | **Adopter** | L | 3.1 (émetteur riche Cursor, déjà planifié) |
| **Checkpoints de fichiers** | `Task --> CheckpointSystem` (checkpoints Git après chaque outil), architecture décrite dans `.clinerules/cline-overview.md`, [cline/cline](https://github.com/cline/cline) | Apache-2.0 | Aucune boucle d'édition de fichiers indépendante de l'hôte à checkpointer — le kit n'édite jamais de fichiers lui-même, il gouverne un agent qui le fait | **Écarter** | — | Hors plan (§9) : ce que fait chaque hôte nativement (undo Claude Code, checkpoints Cline/Cursor) suffit |
| **Time travel (fork d'un run)** | `BaseCheckpointSaver` (`libs/checkpoint/langgraph/checkpoint/base/__init__.py`) et rejeu interne (`libs/langgraph/langgraph/_internal/_replay.py`), [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | MIT, attribution requise | `flow resume` reprend un run (`src/grimoire/cli/cmd_flow.py:340`), `flow extract`/`blueprint evals --record` existent ; pas de fork à un nœud arbitraire | **Adapter** | M | 4.2 (`flow resume --at-node`, à créer — pas de réécriture de moteur de graphe) |
| **Éval mémoire (LongMemEval)** | Harnais d'éval `explorations/longmemeval/` (`src/evaluation/longmemeval-metric.ts`, `README.md`, `USAGE.md`), [mastra-ai/mastra](https://github.com/mastra-ai/mastra) | **Correction** : `LICENSE.md` du dépôt donne **Apache-2.0** pour tout le code hors répertoires `ee/` (le plan produit cite « Elastic-2.0 », périmé ou confondu avec une version antérieure) — le protocole d'éval public (LongMemEval, jeu de données académique) reste réutilisable sans ambiguïté quelle que soit la licence du harnais Mastra | Aucun harnais trouvé (`grep memory bench` vide dans `src/grimoire/`) ; backends existants `src/grimoire/memory/backends/` | **Adopter** | M | 5.1 (`memory bench --longmemeval`, protocole public repris, pas le code TypeScript de Mastra) |
| **Personas de cycle produit (PM/QA/architecte)** | `skills/bmad-agent-pm/`, `skills/bmad-agent-architect/`, `skills/bmad-agent-analyst/`, `skills/bmad-agent-ux-designer/`, `skills/bmad-qa-generate-e2e-tests/`, [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | MIT, attribution requise (licence confirmée, correspond au plan) | Archétypes techniques (`archetypes/*/agents/`), aucun rôle PM/QA/architecte produit | **Écarter pour l'instant** | — | Hors plan explicite (§3, §9) — à revoir uniquement si un utilisateur équipe ou entreprise le demande nommément |
| **Gate de confiance pré-implémentation** *(idée non listée au plan, trouvée en lisant SuperClaude)* | Skill `confidence-check` : score pondéré sur 5 vérifications avant toute implémentation — pas de doublon (25 %), conformité d'architecture (25 %), documentation officielle vérifiée (20 %), référence OSS fonctionnelle (15 %), cause racine identifiée (15 %), seuil ≥ 90 % pour démarrer (`skills/confidence-check/SKILL.md`, `confidence.ts`), [SuperClaude-Org/SuperClaude_Framework](https://github.com/SuperClaude-Org/SuperClaude_Framework) | MIT, attribution requise | Rien de comparable trouvé côté kit : les 336 gates (`src/grimoire/missions/gates.py`) et l'`EvidencePack` (`src/grimoire/evidence/`) valident un travail **après coup**, aucune gate ne s'exécute **avant** de commencer une tâche | **Adapter** | S | À créer — cohérent avec la promesse « moins de manques d'agents » (métrique 6) et le reçu de tâche (métrique 1) ; proposer un lot dans la phase 4 (extras) plutôt qu'un nouveau mécanisme parallèle aux gates existantes |

## Observation sur opencode

[anomalyco/opencode](https://github.com/anomalyco/opencode) (MIT, 207 903 étoiles au 2026-09-16) a été lu pour cette veille : structure en monorepo (`packages/enterprise`, `packages/session-ui`, `packages/plugin`) confirmant le sujet déjà couvert par la matrice de parité — agents en tâche de fond avec statut, sans mécanisme de preuve ou de coût comparable au ledger du kit. Aucune idée supplémentaire n'en ressort au-delà de ce que la phase 3 (`flow run --background`, lot 3.4 émetteur opencode) couvre déjà ; pas de ligne dédiée pour éviter le bruit (doctrine des artefacts).

## Corrections apportées à la veille précédente

1. **Licence Mastra** : le plan produit (§6) citait Elastic-2.0 ; le `LICENSE.md` lu le 2026-09-16 donne Apache-2.0 pour tout le code hors `ee/`. La réutilisation du protocole d'éval public LongMemEval n'était de toute façon pas bloquée par la licence du harnais, mais l'étiquette de licence doit être corrigée dans tout document qui la répète.
2. **Score de qualité des skills** : le plan produit et `framework/agentic-industry-reference.md` attribuent au kit un gate `grimoire-skill-analyzer` (score ≥ 75/100). Ce mécanisme existe côté Forge (atelier de construction d'agents), **pas** dans `src/grimoire/` du kit — recherche exhaustive (`grep -rln "skill.analyzer\|skills-ref\|skill_gate"` dans `src/grimoire/`) sans résultat. Le kit n'a pas encore son propre gate de qualité de skill ; c'est exactement l'écart que couvre la phase 5.5 (`skills-ref validate`, à créer), pas une capacité déjà livrée.

## Sources consultées le 2026-09-16

- [affaan-m/ECC](https://github.com/affaan-m/ECC) — MIT, 260 055 étoiles, 292 skills / 68 agents (comptage direct du dépôt)
- [SuperClaude-Org/SuperClaude_Framework](https://github.com/SuperClaude-Org/SuperClaude_Framework) — MIT
- [anomalyco/opencode](https://github.com/anomalyco/opencode) — MIT
- [cline/cline](https://github.com/cline/cline) — Apache-2.0
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — MIT
- [mastra-ai/mastra](https://github.com/mastra-ai/mastra) — Apache-2.0 (hors `ee/`)
- [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) — MIT
- [Cursor — Rules](https://cursor.com/docs/rules) — documentation propriétaire, aucun code repris
