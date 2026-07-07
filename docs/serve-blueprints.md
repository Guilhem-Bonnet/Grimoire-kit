# Mode local : `grimoire serve` et les blueprints

```bash
pip install grimoire-kit
grimoire serve
```

`grimoire serve` ouvre l'**atelier** sur `http://127.0.0.1:4173/atelier.html` :
l'UI complète est embarquée dans le paquet (hub de projet, marketplace,
éditeur de blueprints, wizard de setup). Le serveur est lié à `127.0.0.1` —
c'est un outil local, pas un service.

Le site public (GitHub Pages) et l'atelier local sont **la même UI** : sans
API locale, les pages « atelier » affichent l'écran de premier lancement avec
les commandes ci-dessus ; avec `grimoire serve`, elles se branchent sur le
projet réel.

Options utiles : `grimoire serve --port 8080`, `--project-root <chemin>`,
`--no-open` (ne pas ouvrir le navigateur).

Principe non négociable : le serveur **lit, valide et écrit des artefacts** ;
il n'exécute rien. L'exécution appartient au runtime existant et passe par
ses gates.

## Les pages

| Page | Rôle |
| --- | --- |
| `atelier.html` | Hub du projet : premier lancement, wizard de setup, blueprints, extensions et artefacts |
| `patterns.html` | Catalogue des 78 patterns (familles, contrats échangés, fiches) |
| `extensions.html` | Marketplace : extensions publiées, recherche, filtres par famille, blueprints publiés |
| `blueprints.html` | Éditeur de flows (Studio) : composer, connecter, valider, simuler, compiler |
| `memory.html` · `kanban.html` · `observability.html` | Observer : mémoire, tableau gouverné, télémétrie |

## L'éditeur de blueprints

Un blueprint (`_grimoire/blueprints/*.blueprint.json`) décrit un flow
agentique comme un graphe de nodes typés.

**Composer** — la palette latérale ajoute d'un clic : patterns du catalogue,
use-cases composites, artefacts du projet, nodes d'extensions.

**Connecter** — Maj + glisser d'un node vers un autre : la connexion se crée
si un contrat commun existe entre pins (task envelope, handoff packet...),
sinon elle est refusée. Une connexion sans contrat commun ne compile pas.

**Propriétés** — sélectionner un node : label éditable, contrats des pins
modifiables depuis la liste du catalogue, suppression. Ctrl+Z annule,
RÉORGANISER applique un layout dirigé.

**Valider** — lint normatif dérivé du catalogue : dépendances de patterns
absentes du flow, heuristique « Faux Done » (aucun pattern de preuve QUA-*),
nodes isolés.

**Simuler** — dry-run sans effet : ordre topologique, cycles bloquants,
prérequis par node (contrôles du pattern, artefact présent, extension
installée), verdict prêt/bloqué.

**Compiler** — un blueprint prêt devient un mission pack
`.github/prompts/{id}.blueprint.prompt.md` exécutable par l'orchestrateur :
plan d'exécution ordonné, obligations par pattern, contrats aux frontières.
La section `compiled` du blueprint trace le hash (détection de dérive).
Aucun apply automatique : le diff git reste la revue.

**Rejouer** — la télémétrie (`events.jsonl`) se rejoue sur le graphe via les
bindings du blueprint.

## API locale

| Route | Rôle |
| --- | --- |
| `GET /api/status` | Racine projet, version kit, UI servie |
| `GET /api/setup` · `POST /api/setup` | Vue des artefacts / plan d'init (wizard) |
| `GET /api/archetypes` | Archetypes du kit (wizard) |
| `GET /api/extensions` · `POST /api/extensions/add` · `/remove` | Gestion des extensions |
| `GET/PUT /api/blueprints/<id>` | CRUD des blueprints |
| `POST /api/blueprints/<id>/validate` · `/simulate` · `/compile` | Lint, dry-run, compilation |
| `GET /api/events` (SSE) · `GET /api/events/log` | Télémétrie live et replay |

Les blueprints du Studio (format v2, positionné) sont acceptés directement :
le serveur en dérive la projection compilable (pins typés depuis les contrats,
sous-flows aplatis) pour valider, simuler et compiler.

Pour un contrôle fin (UI custom, racine du kit), la forme longue reste
disponible : `python -m grimoire.tools.forge_server --ui-dir <dir> --kit-root <dir>`.
