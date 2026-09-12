# Mode local : `grimoire serve` et les blueprints

```bash
pip install grimoire-kit
grimoire serve
```

!!! tip "Cette page décrit l'outil ; le langage est ailleurs"
    Ici : les pages de l'atelier, les gestes, ce que fait chaque bouton. Pour
    ce qu'un blueprint **contient** — nodes, pins, contrats, canaux, portes —
    voir [Le système nodal](nodal/index.md), et
    [Votre premier blueprint](nodal/premier-blueprint.md) pour le parcourir en
    ligne de commande.

`grimoire serve` ouvre la **vue de travail** sur
`http://127.0.0.1:4173/workspace/index.html` : la coque unique décrite par
[l'ADR de la vue de travail](adr-006-vue-de-travail.md), avec l'espace
Concevoir pour l'éditeur de blueprints. L'UI complète reste embarquée dans le
paquet. Le serveur est lié à `127.0.0.1` — c'est un outil local, pas un
service.

Le site public (GitHub Pages) et l'atelier local sont **la même UI** : sans
API locale, les pages « atelier » affichent l'écran de premier lancement avec
les commandes ci-dessus ; avec `grimoire serve`, elles se branchent sur le
projet réel.

!!! note "Les pages ci-dessous restent servies"
    `atelier.html`, `blueprints.html`, `patterns.html` et `extensions.html`
    répondent toujours, mais redirigent désormais vers l'espace de la vue de
    travail qui les remplace (Concevoir, pour les trois derniers). Ajouter
    `?legacy=1` à l'URL les sert sans redirection — c'est ce que décrit le
    reste de cette page.

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
| `memory.html` · `kanban.html` · `observability.html` | Observer : mémoire, tableau gouverné, télémétrie — **du projet servi, ou rien** |

Les pages partagées avec le site publié — accueil, portefeuille — gardent leur
habillage vitrine, mais l'atelier sait qu'il est servi en local : ni lien vers
la démo publique, ni invitation à installer ce qui tourne déjà. Le passage vers
`atelier.html` reste offert en un lien.

### Changer de projet

Le bouton de projet, en haut de la barre latérale, ouvre le sélecteur : les
projets connus de la machine, une navigation dossier par dossier (ou un chemin
collé), et un scan borné d'une racine qui propose sans enrôler. Choisir un
projet re-route le serveur en cours — pas de second processus à lancer.

Le registre est celui de `grimoire cockpit` (`~/.grimoire/cockpit/registry.json`) :
un projet ouvert dans l'atelier apparaît dans le portefeuille, et
réciproquement. Ouvrir un projet n'écrit rien dans son arbre : la couche de
données générée vit sous `~/.grimoire/cockpit/atelier/<slug>/data/`.

La découverte ne regarde pas partout. Un chemin venu d'une requête HTTP ne doit
pas pouvoir désigner n'importe quel dossier du système, même derrière un garde
d'hôte. Sont autorisés :

- le répertoire personnel ;
- le projet servi et son dossier parent — de quoi scanner ses voisins dès le
  premier lancement ;
- le dossier parent de chaque projet déjà enrôlé, ce qui garde atteignable un
  dépôt hors de `$HOME`.

Une racine entièrement nouvelle s'ouvre par `grimoire cockpit add <chemin>`,
qui n'est pas exposé au réseau.

### Ce que montrent Observatoire, Mémoire et Kanban

Une couche générée sur le projet servi (`gen-site-data.py`, régénérée en
arrière-plan au démarrage et à chaque changement de projet), jamais
l'instantané de la vitrine publique embarqué dans la wheel. Un projet qui n'a
lancé aucun agent a un observatoire vide, et le dit — afficher des traces
inventées horodatées à l'instant serait pire. La chip **données** du tableau de
bord montre l'état de la couche ; un clic la régénère.

## Le board (espace Exécuter)

`kanban.html` reste servi comme vitrine statique (lecture du JSON plat du
projet primaire, sans écriture), mais le board vivant est dans l'espace
**Exécuter** de la vue de travail : `workspace/index.html#executer`, sur
`grimoire serve` comme sur `grimoire cockpit serve`. Il lit les tâches
réelles du Mission Ledger (ADR-005) plutôt qu'un `task-board.yaml` écrit à la
main, et change de contenu avec le projet sélectionné — sur le cockpit,
`?project=<slug>` cible un autre board à chaque lecture, jamais « le dernier
projet regardé ».

**Lecture** — chaque carte porte son corps réel : description, critères
d'acceptation, garde-fous, preuves attendues, dépendances (et les blocages
qu'elles impliquent). Rien de tout cela n'existait dans `task-board.yaml`,
qui ne connaît qu'un titre, un owner et des références de fichiers ; c'est le
`MissionTask` du ledger qui les porte, et l'inspecteur les affiche tel quel.

**Commandes d'intention, jamais de mutation libre** — une carte ne s'édite
pas par glisser-déposer. Le bouton « Réaliser » de l'inspecteur propose une
transition (réclamer, avancer, bloquer, clôturer) au même service que la CLI
(`grimoire task …`, voir [référence CLI](cli-reference.md)) : la machine à
états puis le gate de preuve s'appliquent avant toute écriture. Un gate rouge
répond `200` avec `blocked: true` et nomme l'artefact manquant et son remède
— ce n'est pas une panne du serveur, c'est la réponse ; la carte ne bouge
pas. Exactement le principe qu'ADR-007 pose pour toute webview du kit : *la
webview n'est jamais une autorité causale*.

**Restriction d'écriture** — comme le reste de la vue de travail, ces
commandes ne s'exécutent que pour le projet que l'hôte sert en direct
(`grimoire serve`, ou le projet de lancement de `grimoire cockpit serve`) ;
regarder un autre projet du registre depuis le cockpit reste une lecture
seule, refusée en `403` côté serveur si on tente d'agir dessus — pas
seulement grisée côté client.

Le drill-down vers la timeline complète d'une tâche (Mission Ledger,
TraceLedger, runtime) est un onglet séparé du même espace, décrit par
[l'ADR de la vue de travail](adr-006-vue-de-travail.md).

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
| `GET /api/stigmergy` | Vue live du tableau phéromonique (signaux actifs, trails, métriques) — beta |
| `GET /api/projects` · `POST /api/projects/select` | Registre de la machine · re-router le serveur sur un autre projet |
| `POST /api/projects/add` · `/scan` | Enrôler un chemin · découvrir les projets sous une racine (sans enrôler) |
| `GET /api/fs/browse?path=` | Navigation dossier par dossier, pour désigner un projet à la main — bornée aux racines permises |
| *(cockpit)* `GET /api/fs/browse` · `POST /api/projects/add\|scan` | Même découverte depuis le portefeuille : peupler le registre de la machine n'est pas une écriture sur un projet |
| `GET /api/data/status` · `POST /api/data/refresh` | État et régénération de la couche de données du projet servi |
| `GET /api/health` | Alignement kit, flows composés, exécutions en vol et activité réelle du projet |
| `POST /api/projects/update` | `grimoire up` sur le projet — aperçu par défaut, écriture sur `confirm: true` |

Le bloc `behavior` de `GET /api/stigmergy` porte les métriques de promotion
beta→stable et la thèse qu'elles testent (QUA-13, mesure-sans-hypothèse) :

- `usefulRatio` — part des signaux émis ayant produit une coordination utile
  (résolution ou relais) ;
- `targetUsefulRatio` (`0.4`) — seuil de promotion visé ;
- `minEmitted` (`20`) — volume minimal d'émissions pour que la mesure compte ;
- `hypothesis` — l'hypothèse testée, en clair ;
- `promotionReady` — `true` quand `usefulRatio >= targetUsefulRatio` sur au
  moins `minEmitted` émissions : la mesure sert une décision.

Chaque mutation servie (`POST /api/extensions/add|remove`, toggle de feature,
`PUT` et `compile` de blueprint) est tracée en JSONL dans
`_grimoire-runtime-output/hook-runtime/serve-mutations.jsonl` (QUA-08) ; les
`GET` restent silencieux. La sélection d'un projet ne l'est pas : ouvrir un
dépôt n'est pas le modifier, et journaliser dans son arbre salirait le
`git status` de tout projet qu'on se contente de regarder.

Les blueprints du Studio (format v2, positionné) sont acceptés directement :
le serveur en dérive la projection compilable (pins typés depuis les contrats,
sous-flows aplatis) pour valider, simuler et compiler.

Pour un contrôle fin (UI custom, racine du kit), la forme longue reste
disponible : `python -m grimoire.tools.forge_server --ui-dir <dir> --kit-root <dir>`.
