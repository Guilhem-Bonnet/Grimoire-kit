# Mode local : `grimoire serve` et les blueprints

```bash
pip install grimoire-kit
grimoire serve
```

Ouvre `http://127.0.0.1:4173/` : l'UI complète est embarquée dans le paquet
(marketplace, éditeur de blueprints, wizard de setup). Le serveur est lié à
`127.0.0.1` — c'est un outil local, pas un service.

Principe non négociable : le serveur **lit, valide et écrit des artefacts** ;
il n'exécute rien. L'exécution appartient au runtime existant et passe par
ses gates.

## Les pages

| Page | Rôle |
| --- | --- |
| `extensions.html` | Marketplace : extensions publiées, recherche, filtres par famille, blueprints |
| `blueprint.html` | Catalogue des 78 patterns en graphe + éditeur de flows (mode local) |
| `setup.html` | Wizard : archetype, installation d'extensions, vue des artefacts gouvernés |

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
| `GET /api/setup` | Artefacts gouvernés, extensions installées, blueprints |
| `GET /api/archetypes` | Archetypes du kit (wizard) |
| `GET /api/extensions` · `POST /api/extensions/add` · `/remove` | Gestion des extensions |
| `GET/PUT /api/blueprints/<id>` | CRUD des blueprints |
| `POST /api/blueprints/<id>/validate` · `/simulate` · `/compile` | Lint, dry-run, compilation |
| `GET /api/events` (SSE) · `GET /api/events/log` | Télémétrie live et replay |

Options : `--project-root`, `--port`, `--ui-dir` (UI custom), `--kit-root`.
