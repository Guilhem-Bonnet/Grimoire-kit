# Intégration du domaine jeu vidéo

Grimoire Kit ne remplace pas le corpus normatif agentique ni son **cluster jeu vidéo**. Il sert de **pont domaine** consommable : il mappe les use-cases, rayons de compétences, lentilles de genre et la matrice de modalités du standard vers des artefacts exécutables ou copiables (archétype, agents-disciplines, templates, gates de preuve).

Le cluster jeu vidéo est **bâti sur le socle agentique** déjà intégré (voir `agentic-standard-integration.md`). `game-dev` en est l'extension domaine.

## Positionnement

| Surface | Responsabilité | Ne doit pas faire |
|---|---|---|
| Corpus normatif externe (cluster jeu) | Définit use-cases UC-08→UC-50, rayons A→AC, profils de genre, matrice MOD-03 | Dépendre de Grimoire Kit |
| Grimoire Forge | Assemble et génère un kit cible à partir de l'archétype `game-dev` | Modifier la norme pendant une génération |
| Grimoire Kit | Fournit l'archétype, les agents-disciplines, templates et gates | Se déclarer source normative |

Le pont vit dans :

- `framework/game-dev/domain-map.yaml` — le cœur machine-readable
- `framework/game-dev/knowledge/` — bundle self-contained des docs amont (provenance citée)
- `framework/game-dev/templates/`
- `archetypes/game-dev/`

## Règles normatives du domaine

L'archétype `game-dev` porte neuf invariants (traits), chacun rattaché à des use-cases et au socle agentique :

| Règle | Invariant | Use-cases |
|---|---|---|
| GR-01 | Le GDD est l'unique source de vérité ; tout contenu s'y rattache | UC-08 |
| GR-02 | Aucun contenu hors classification d'âge ; scan avant intégration | UC-15, UC-47 |
| GR-03 | Simulation testée déterministe : seed, pas fixe, hash d'état | UC-13 |
| GR-04 | Certification = gate de preuve (dry-run vert + evidence pack) | UC-15 |
| GR-05 | Pas d'ajustement live sans télémétrie ni canary | UC-16, UC-10 |
| GR-06 | Pas de changement d'équilibrage sans non-régression | UC-10 |
| GR-07 | Gate de validation de contenu avant merge (budgets, refs, naming, lore) | UC-12, UC-09 |
| GR-08 | Build reproductible et jalon prouvé | UC-15 |
| GR-09 | Tout patch live est rejouable ou compensable (rollback) | UC-16 |

## Disciplines (agents)

L'archétype installe huit agents-disciplines, dispatchés par l'orchestrateur :

| Agent | Persona | Rôle | Use-cases porteurs |
|---|---|---|---|
| `game-designer` | Aria | Gardien du GDD, décomposition en mécaniques vérifiables | UC-08, UC-09 |
| `narrative-designer` | Lyra | Cohérence du lore, narration à état | UC-08, UC-40 |
| `level-designer` | Dedale | Plan, blockout, rythme, validation de niveaux | UC-09, UC-12 |
| `gameplay-programmer` | Kano | Comportements (IA/combat/physique) + harnais déterministe | UC-13, UC-14, UC-23, UC-25, UC-36 |
| `systems-economist` | Vega | Équilibrage et économie prouvés par simulation | UC-10, UC-41, UC-42 |
| `tech-artist` | Sable | Pipeline d'assets gouverné, budgets, routage MOD-03 | UC-12, UC-17, UC-27, UC-35 |
| `game-qa` | Argus | Playtest agentique, tests rejouables, certification | UC-11, UC-15, UC-37 |
| `liveops-analyst` | Nova | Télémétrie, déploiement canari, rollback | UC-16, UC-42 |

## Lentilles de genre

Le `domain-map.yaml` décrit quinze lentilles de genre (use-cases porteurs, rayons, socle critique, ambiance, pièges). À l'init, le genre choisi est injecté dans `shared-context.md` (marqueur `GENRE-LENS`) pour cadrer le projet :

`fps-tps`, `action-rpg`, `strategy`, `moba-arena`, `puzzle`, `platformer`, `survival-horror`, `simulation`, `open-world`, `racing`, `fighting`, `roguelike`, `narrative-adventure`, `battle-royale`, `rhythm`.

## Matrice capacités / modalités (MOD-03)

Le routage des modalités est une règle de premier ordre : ce qui sort de la compétence cœur d'un LLM texte (image, audio, 3D, vidéo) est **routé** vers la cible capable (modèle spécialisé, outil DCC, humain), jamais simulé en asset final médiocre. Le LLM produit specs, placeholders et orchestration. Toute décision de routage produit un **Capability Routing Record**.

## Templates (artefacts de preuve)

Copiables depuis `framework/game-dev/templates/` :

| Template | Gate / Use-case |
|---|---|
| `gdd.md` | Source de vérité (UC-08) |
| `content-validation-record.md` | GR-07 (UC-09, UC-12) |
| `balance-regression-evidence.md` | GR-06 (UC-10) |
| `playtest-evidence-pack.md` | Playtest prouvé (UC-11) |
| `determinism-replay-record.md` | GR-03 (UC-13) |
| `certification-record.md` | GR-04 / GR-08 (UC-15) |
| `telemetry-decision-record.md` | GR-05 / GR-09 (UC-16) |
| `capability-routing-record.md` | MOD-03 |
| `asset-budget-report.md` | GR-07 (UC-12) |

## Installation dans un projet cible

```bash
grimoire init . --archetype game-dev
# ou via le script :
grimoire-init.sh install --archetype game-dev
```

L'assistant interactif (sans `--archetype`, en TTY) propose le choix de l'archétype, puis — si `game-dev` est retenu — le choix de la lentille de genre. Les artefacts générés au fil du cycle :

```text
docs/GDD.md
_grimoire-output/evidence/{task-id}/determinism-replay-record.md
_grimoire-output/evidence/{task-id}/balance-regression-evidence.md
_grimoire-output/evidence/{task-id}/playtest-evidence-pack.md
_grimoire-output/evidence/{task-id}/content-validation-record.md
_grimoire-output/evidence/{task-id}/capability-routing-record.md
_grimoire-output/evidence/milestones/{milestone}/certification-record.md
_grimoire-output/evidence/liveops/{patch}/telemetry-decision-record.md
```

## Ce qui est prêt

1. cartographie use-cases → rayons → genres → modalités → templates → disciplines (`domain-map.yaml`) ;
2. archétype installable `game-dev` (9 règles normatives en traits, 8 agents) ;
3. huit agents-disciplines (persona XML) couvrant tout le cycle ;
4. neuf templates de preuve alignés sur les gates ;
5. bundle de connaissance self-contained (`knowledge/`) avec provenance vers le corpus amont ;
6. sélection d'archétype et de lentille de genre à l'init.
