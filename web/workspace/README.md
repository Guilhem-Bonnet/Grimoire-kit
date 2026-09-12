# La vue de travail — coque, contrats, lots

Squelette de la refonte décrite par
[`web/DESIGN-SPEC-workspace-2026-09.md`](../DESIGN-SPEC-workspace-2026-09.md).
Ce dossier contient **la coque et rien d'autre** : les six espaces sont des
modules vides mais navigables, que cinq lots remplissent en parallèle sur des
fichiers disjoints.

Décisions et alternatives écartées :
[`docs/adr-006-vue-de-travail.md`](../../docs/adr-006-vue-de-travail.md).

## Lancer

La coque se sert elle-même : elle a besoin de l'API locale.

```sh
# Atelier — un projet
grimoire serve --project-root /chemin/du/projet --port 4173 --no-open
# puis ouvrir http://127.0.0.1:4173/workspace/index.html

# Cockpit — une flotte
grimoire cockpit serve --port 8420
# puis http://127.0.0.1:8420/workspace/index.html?project=<slug>
```

Ouvrir `index.html` en `file://` ne marche pas et ne doit pas marcher : sans
API, il n'y a pas de projet, et la coque affiche l'état vide qui le dit.

## Tester

```sh
pytest tests/unit/test_workspace_api.py \
       tests/unit/test_workspace_routes.py \
       tests/unit/test_workspace_tokens.py \
       tests/unit/test_workspace_glossary.py \
       tests/unit/test_workspace_language.py \
       tests/unit/test_source_assist.py

# Harnais navigateur (facultatif — se skippe proprement sans Playwright)
pip install playwright && playwright install chromium
pytest tests/e2e
```

Les tests unitaires initialisent un vrai projet (`grimoire init` +
`grimoire standard init --profile governed`) : il n'y a aucune donnée de
démonstration dans cette suite, et il ne doit jamais y en avoir.

## Fichiers

| Fichier | Rôle | Propriétaire |
|---|---|---|
| `index.html` | La coque : barre d'application, rail, panneaux, dock, barre d'état, palette | lot 1 |
| `tokens.css` | **La seule source de couleur.** Sombre et clair, densités, géométrie | lot 1 |
| `shell.css` | Grille de la coque, trois états de panneau, primitives (`.btn`, `.chip`, `.seg`, `.kbd`, `.tab`, `.dot`, `.tree`, `.empty`) | lot 1 |
| `shell.js` | Panneaux, dock, palette, raccourcis, thème, densité, routage des espaces | lot 1 |
| `fonts/` | Geist et Geist Mono en woff2 | lot 1 |
| `glossary.js` | Chargement du glossaire et pile d'infobulles épinglables | lot 2 |
| `api.js` | **Le seul module qui appelle `fetch`.** Cible le bon projet sur les deux hôtes | figé par le squelette |
| `spaces/piloter.js` | Flotte et projet, KPI, « À traiter », agents (clause d'emploi, outils, contexte, skills, usage — #374) | lot 4 |
| `spaces/concevoir.js` | Toile, zoom, éditeur de graphe, bibliothèque, inspecteur de nœud | lot 3 |
| `spaces/executer.js` | Board gouverné, portes, carte de tâche, timeline | lot 4 |
| `spaces/observer.js` | Runtime : KPI, coût, latence, spans, traces | lot 4 |
| `spaces/memoire.js` | Store et graphe d'abord, couches ensuite | lot 4 |
| `spaces/source.js` | Fichiers par étage, éditeur, diff, override, provenance | lot 5 |
| `spaces/source-editor.js` | IntelliSense de l'éditeur Source (issue 280) : colorisation par recouvrement, gouttière de diagnostics, complétion, infobulle du glossaire au survol ; suggestions par modèle local derrière (issue 280, voie 2) : barre « Suggérer », panneau d'aperçu, lien « Expliquer » sur un diagnostic | lot 5 |

## Le contrat d'un espace

Un module d'espace exporte une seule fonction :

```js
export async function mount(root, ctx) { /* … */ }
```

`root` est l'élément `#canvas`, vidé avant chaque montage. `ctx` est figé par
ce squelette — un lot qui a besoin d'autre chose le demande, il ne se sert pas
directement dans le DOM de la coque :

| Champ | Ce que c'est |
|---|---|
| `ctx.api` | Le client de `api.js`, déjà ciblé sur le bon projet |
| `ctx.host` | `{ kind: 'atelier' \| 'cockpit', project, readOnly, status }` |
| `ctx.glossary` | Le glossaire chargé : `get`, `open`, `size`, `missingTerms` |
| `ctx.explorer` | L'élément du panneau gauche — l'espace le remplit |
| `ctx.inspector` | L'élément du panneau droit — idem |
| `ctx.docbar` | `setBreadcrumb`, `setZoom`, `setViews`, `setValidation` |
| `ctx.dock` | `log(onglet, …lignes)`, `clear`, `setTab`, `echo(commande)` |
| `ctx.empty` | `(titre, phrase, commande)` → le bloc d'état vide unique |
| `ctx.signal` | Un `AbortSignal` annulé quand l'espace est démonté |
| `ctx.goto` | `goto(id, params?)` — naviguer vers un autre espace ; `params` n'est vu qu'au montage qui suit (ex. `goto('source', { file })` depuis ⌘K), jamais persisté dans le hash |
| `ctx.params` | Ce que l'appelant de `goto()` a demandé pour CE montage — `{}` la plupart du temps |
| `ctx.rail` | `{ on(id, handler) }` — enregistre ce que fait un rail sans panneau dédié (ex. « 2 », bibliothèque de Concevoir) tant que cet espace reste monté ; remis à zéro par la coque à chaque changement d'espace |

Deux règles opposables :

1. **Aucun module d'espace n'appelle `fetch`.** Il passe par `ctx.api`. Une
   route qui manque s'ajoute dans `api.js` et dans
   `src/grimoire/tools/workspace_routes.py`, jamais en dur.
2. **Aucun module d'espace n'écrit une couleur.** Il utilise les tokens et les
   primitives de `shell.css`. `tests/unit/test_workspace_tokens.py` refuse tout
   littéral de couleur ailleurs que dans `tokens.css`.

## Citer un concept

Un terme se cite avec `data-term="<id>"`, jamais avec une définition en dur :

```html
<span data-term="porte-de-preuve">porte</span>
```

L'identifiant vient de [`framework/glossary.yaml`](../../framework/glossary.yaml).
`tests/unit/test_workspace_glossary.py` échoue si un `data-term` n'a pas
d'entrée, et le harnais navigateur refait le contrôle sur le DOM rendu, donc y
compris pour les termes posés à l'exécution. Le remède d'un échec est toujours
d'écrire l'entrée, jamais de retirer la citation.

## Les routes

Tout ce que la vue de travail ajoute vit sous `/api/workspace/`. Les lectures
sont servies par **les deux hôtes** ; les écritures n'existent que sur
l'atelier mono-projet, parce que le cockpit se déclare `readOnly`.

### Lectures — atelier et cockpit

| Route | Rend |
|---|---|
| `GET /api/workspace/glossary` | `{schema, source, count, entries[]}` — `entries[]` : `{id, nom, definition, raccourci, termes[], doc}` |
| `GET /api/workspace/tasks?mission=&status=` | `{columns[8], states[9], ledger, count, tasks[], note?}` |
| `GET /api/workspace/tasks/<id>` | La tâche + `board` + `next_moves_require: {colonne: [preuves]}` |
| `GET /api/workspace/tasks/<id>/trace` | `{task_id, task, sources{ledger,hooks,runtime,evidence}, entries[], causes[]}` |
| `GET /api/workspace/files?tier=` | `{projectRoot, tiers[]}` — `tiers[]` : `{id, term, label, note, roots[], editable, exists, truncated, count, files[]}` |
| `GET /api/workspace/file?path=` | Le fichier + `{tier, size, digest, shipped_by_kit, kit_version, editable, override_path?, overridden?, text, truncated, binary}` |
| `GET /api/workspace/file/diff?path=` | `{path, against, comparable, identical, unified, added, removed}` ou `{comparable: false, reason}` |
| `GET /api/workspace/file/usage?path=` | `{path, projections[], loaded_by: {entries[], truncated}}` — inspecteur, onglet Utilisé par |
| `GET /api/workspace/file/history?path=` | `{path, is_repo, commits[]}` — inspecteur, onglet Historique ; `is_repo: false` hors dépôt git |
| `GET /api/workspace/commands` | Le catalogue des sous-commandes que la Console accepte |
| `GET /api/workspace/doctor` | `{ok, code, timed_out, command, lines[], stderr}` |
| `GET /api/workspace/language?path=&text=&line=&col=` | IntelliSense de Source (issue 280) : `{path, tokens[], diagnostics[], completions[]?}` — `tokens[]` : `{line, start, end, kind, glossaryId?}` ; `diagnostics[]` : `{line, start, end, severity, family, message}` ; `completions[]` (seulement si `line`/`col` fournis) : `{label, kind, insertText, detail}`. `text` porte le brouillon en cours d'édition — omis, la lecture vient du disque comme `file` |
| `GET /api/workspace/agents` | `{agents[], skills[], entry_point}` — `agents[]` : `{name, description, definition_ref, tools[], tools_origin, affinity, entry_point, max_turns, skills[], context[], layer, use_when, dont_use_when, tool_boundary, usage: {choices, last_chosen_at}}` ; `skills[]` : le catalogue disponible pour l'assignation (#374) |
| `GET /api/workspace/assist` | Suggestions par modèle local (issue 280, voie 2), lecture sans coût — jamais d'appel au modèle : `{enabled, model, available, reason}`. `enabled: false` = opt-in absent (`source.assist.model` vide) ; `available: false` avec `enabled: true` = Ollama indisponible ou modèle absent de `ollama list` |

### Écritures — atelier seulement (404 sur le cockpit)

| Route | Corps | Rend |
|---|---|---|
| `POST /api/workspace/tasks/<id>/claim` | `{actor?, host?}` | La tâche déplacée, ou `{blocked: true, refusals[]}` |
| `POST /api/workspace/tasks/<id>/move` | `{to, reason?, actor?}` | idem |
| `POST /api/workspace/tasks/<id>/block` | `{reason, actor?}` | idem |
| `POST /api/workspace/tasks/<id>/close` | `{actor?}` | idem |
| `POST /api/workspace/file/override` | `{path}` | `{created, override_path, from?}` |
| `POST /api/workspace/file/write` | `{path, text}` | Le fichier relu |
| `POST /api/workspace/command` | `{argv[]}` | `{ok, command, argv, code, stdout, stderr, output, timed_out, duration_ms}` |
| `POST /api/workspace/agents/<nom>/skill` | `{skill, action: "assign"\|"remove"}` | `{agents[], skills[], entry_point}` (la vue rafraîchie) — écrit toujours dans `overrides`, jamais dans le kit ; skill inconnu refusé avec le message que `collect_agents` produit déjà (#374) |
| `POST /api/workspace/agents/<nom>/fields` | Un sous-ensemble de `{use_when, dont_use_when, tool_boundary, tools[], context[]}` | idem — `tools` validé contre les verbes connus, `context` contre l'existence sur disque |
| `POST /api/workspace/assist` | `{path, text, position: {line, col}, intent: "complete-clause"\|"draft-body"\|"explain-diagnostic", diagnostic?}` | `{available, model?, intent?, suggestion?, unknown?[], reason?}` — `available: false` est une réponse normale (opt-in absent, Ollama indisponible, délai dépassé à 10 s), jamais une exception ; `unknown[]` : `{text, kind, line, start, end}`, les identifiants cités par le modèle mais absents du paquet de langage. N'écrit jamais de fichier — l'insertion reste un geste de l'éditeur |

Un gate de preuve rouge **n'est pas une erreur** : il revient en 200 avec
`blocked: true` et la preuve manquante nommée. L'interface l'affiche ; elle ne
l'avale pas.

Codes : 400 refus explicable (commande hors liste blanche, état inconnu, skill
ou contexte d'agent introuvable), 403 chemin hors projet ou étage non
éditable, 404 tâche, agent ou route inconnue.

Les écritures d'agents sont un cas particulier de « atelier seulement » : le
cockpit les honore aussi, mais uniquement pour son **projet de lancement**
(celui depuis lequel `grimoire cockpit serve` a démarré, `#356`) — jamais pour
un autre projet du registre qu'on ne fait que regarder.

## Ce que la Console accepte

`POST /api/workspace/command` n'exécute que des sous-commandes `grimoire` de
lecture, sans shell, avec un délai maximal. La liste vit dans
`src/grimoire/tools/workspace_exec.py` (`ALLOWED`) — l'ouvrir davantage se fait
là, avec le test qui va avec. `init`, `up`, `migrate`, `serve`, `cockpit`,
`upgrade` et `ext` en sont **volontairement absents** : ils réécrivent l'arbre
du projet ou parlent au réseau, et un terminal dans un onglet n'est pas le bon
geste pour ça.

## Où sont les anciennes pages

Elles restent servies et fonctionnelles tant que les cinq lots ne sont pas
livrés. Le basculement et les suppressions sont décrits dans l'ADR, section
« Basculement ».
