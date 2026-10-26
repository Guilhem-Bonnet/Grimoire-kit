# La vue de travail — coque, contrats, lots

Squelette de la refonte décrite par
[`web/DESIGN-SPEC-workspace-2026-09.md`](../DESIGN-SPEC-workspace-2026-09.md).
Ce dossier contient **la coque et rien d'autre**. Depuis P31, la coque tient
**cinq espaces** — À traiter, Travail, Flotte, le 4e (son nom n'est écrit que
dans `glossary.js`, `FOURTH_SPACE`) et Configuration. Chaque espace a une ou
plusieurs **rubriques**, et chaque rubrique monte un module de `spaces/` :

| Espace | Rubriques → module |
|---|---|
| À traiter (⌘1, accueil) | À traiter → `a-traiter.js` |
| Travail (⌘2) | Tâches → `executer.js` |
| Flotte (⌘3) | Flotte → `piloter.js` (table en portée « Tous les projets », fiche en portée projet) |
| 4e espace (⌘4) | Mémoire → `memoire.js`, Journal → `observer.js` |
| Configuration (⌘5) | Fichiers → `source.js`, Blueprints → `concevoir.js`, Préférences (rendue par la coque) |

**Une seule portée** : « Tous les projets » ou un projet, choisie par la puce en
haut (`#project-chip`, palette réduite aux projets, sans plafond). Plus de zoom
Flotte/Projet par espace. **L'URL dit tout** : `?project=<slug>` ou
`?scope=all`, puis `#<espace>[/<rubrique>][?<élément>]` — par exemple
`?project=grimoire-forge#travail?task=GAO-12`. Les anciens `#piloter`,
`#executer`… mènent à la rubrique qui les remplace. Un projet consulté depuis
un cockpit ouvert ailleurs est en **lecture seule** : un bandeau le dit en tête,
avec « Revenir à … » et la commande qui l'ouvre en écriture.

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
| `shell.js` | Cinq espaces et leurs rubriques, portée, liens profonds, bandeau de lecture seule, panneaux, dock, palette, raccourcis, thème, densité, plein écran | lot 1, P31 |
| `live.js` | Le pouls : une boucle de sondage pour toute la coque, suspendue onglet masqué ; les mots du temps relatif, de la durée et du coût (trois états) | UI-3 |
| `fonts/` | Instrument Sans (variable) et Geist Mono en woff2, licences OFL | lot 1, P62 |
| `glossary.js` | Chargement du glossaire et pile d'infobulles épinglables ; nom du 4e espace (`FOURTH_SPACE`) | lot 2, P31 |
| `api.js` | **Le seul module qui appelle `fetch`.** Cible le bon projet sur les deux hôtes | figé par le squelette |
| `spaces/a-traiter.js` | Accueil : ce qui attend une décision, groupé par urgence (`/api/workspace/attention`) ; une ligne par élément (projet, raison, ancienneté, action en ligne qui ouvre l'écran de décision, sélection posée), détail dans l'inspecteur, états vide / erreur partielle / lecture seule, clavier j / k / Entrée | P31, P32 |
| `spaces/piloter.js` | Flotte et projet, KPI, « À traiter », agents (clause d'emploi, outils, contexte, skills, usage — #374) | lot 4 |
| `spaces/concevoir.js` | Toile, zoom, éditeur de graphe, bibliothèque, inspecteur de nœud | lot 3 |
| `spaces/executer.js` | Board gouverné, portes, carte de tâche, timeline | lot 4 |
| `spaces/observer.js` | Runtime : KPI, coût, latence, spans, traces | lot 4 |
| `spaces/memoire.js` | La recherche d'abord : filtres type, source, projet, date ; détail de l'entrée à droite | P24 |
| `spaces/memoire-gestes.js` | Correction directe d'une entrée dans son panneau de détail : Corriger (étiquettes, source, fusion, retrait), Marquer remplacée…, historique avec Annuler… ; chaque geste par un aperçu avant/après puis son application (`POST /api/memory`, `action: gesture`) | P34 |
| `spaces/sante.js` | Savoir › Santé et carte : critères de santé de la mémoire (en échec, réussis, inconnus, chacun avec sa mesure) et file d'hygiène, chaque geste par un aperçu puis sa commande à copier (`/api/workspace/memory/quality`) | P25 |
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
| `ctx.scope` | `{ all, project, name, set(slug \| null, cible?) }` — la portée de la coque ; `name` est le nom du projet (jamais le slug) ; `set` change la portée partout (rechargement) |
| `ctx.setItem` | `(params)` — la sélection entre dans l'URL (lien profond) sans remonter la rubrique |
| `ctx.glossary` | Le glossaire chargé : `get`, `open`, `size`, `missingTerms` |
| `ctx.explorer` | L'élément du panneau gauche — l'espace le remplit |
| `ctx.inspector` | L'élément du panneau de détail à droite — idem. Hors atelier (P62) le panneau n'est affiché que si l'espace y a posé du contenu : une phrase `p.lbl` seule ou un squelette ne comptent pas |
| `ctx.docbar` | `setBreadcrumb`, `setZoom`, `setViews`, `setValidation` — le fil d'Ariane ne reçoit que ce qui suit portée, espace et rubrique, que la coque pose |
| `ctx.dock` | `log(onglet, …lignes)`, `clear`, `setTab`, `echo(commande)` |
| `ctx.empty` | `(titre, phrase, commande, action?)` → le bloc d'état vide unique ; `action` est `{ label, run }` (un bouton qui fait, pas une commande à recopier) ; sans `action`, une commande que la Console du dock sait exécuter (liste blanche, sans argument) reçoit un bouton « Lancer dans la Console » |
| `ctx.loading` | `(libellé, lignes?)` → `[squelette, texte pour lecteurs d'écran]` à poser dans une zone qui attend ses données (jamais un « Chargement… » nu) ; la coque en pose un sur la toile à chaque changement d'espace et le retire dès que l'espace y écrit du contenu |
| `ctx.openPanel` | `(id)` → ouvre un panneau latéral (`explorer`, `inspector`, `evidence`) : épinglé au bureau, en tiroir sous 900 px |
| `ctx.signal` | Un `AbortSignal` annulé quand l'espace est démonté |
| `ctx.goto` | `goto(id, params?)` — naviguer vers un espace, une rubrique ou un ancien identifiant (`'source'`) ; `params` est vu au montage qui suit (ex. `goto('source', { file })` depuis ⌘K) et écrit dans le hash (lien profond) |
| `ctx.params` | Ce que l'appelant de `goto()` a demandé pour CE montage — `{}` la plupart du temps |
| `ctx.live` | `{ on(handler) }` — appelé quand le pouls annonce un changement du ledger ou des sessions (`live.js`) ; l'espace relit SES données, sans rien jeter de ce que l'utilisateur écrit ; remis à zéro par la coque à chaque changement d'espace |
| `ctx.rail` | `{ on(id, handler) }` — enregistre ce que fait un rail sans panneau dédié (ex. « 2 », bibliothèque de Concevoir) tant que cet espace reste monté ; remis à zéro par la coque à chaque changement d'espace |

Deux règles opposables :

1. **Aucun module d'espace n'appelle `fetch`.** Il passe par `ctx.api`. Une
   route qui manque s'ajoute dans `api.js` et dans
   `src/grimoire/tools/workspace_routes.py`, jamais en dur.
2. **Aucun module d'espace n'écrit une couleur.** Il utilise les tokens et les
   primitives de `shell.css`. `tests/unit/test_workspace_tokens.py` refuse tout
   littéral de couleur ailleurs que dans `tokens.css`.

## Petits écrans, thème, accessibilité

- **Sous 900 px** (`@media (max-width: 900px)` dans `shell.css`, `matchMedia`
  identique dans `shell.js` : les deux seuils sont gardés par
  `tests/unit/test_workspace_responsive.py`) la coque tient en une colonne. Le
  rail devient une barre basse, explorateur, inspecteur et preuves sont des
  **tiroirs** en surimpression (un seul à la fois, fermés à l'arrivée, jamais
  mémorisés : un tiroir ouvert sur mobile ne rouvre pas, épinglé, sur le bureau).
  Le bouton du rail ouvre, le focus entre dans le tiroir, Échap, son bouton
  « Fermer » ou un appui sur le voile le referment et rendent le focus au rail.
  La barre de document passe à la ligne ; les onglets des espaces défilent.
- **Mesure** : `scrollWidth` du document ne prouve rien ici (`#shell` est en
  `overflow: hidden`, il vaut `innerWidth` même quand le centre est écrasé à
  0 px). `tests/e2e/test_workspace_responsive_a11y.py` mesure la largeur du
  centre, le contenu de la toile et les bords des barres. Un espace qui ajoute
  une grille ou un tableau large la garde dans sa propre boîte défilante.
- **Thème** : trois états (`#st-theme`), `system` (suit `prefers-color-scheme` en
  direct), `light`, `dark` ; défaut `dark`, mémorisé par projet. `data-theme-pref`
  porte le choix, `data-theme` le thème résolu — tokens.css ne lit que lui.
- **Onglets** : `role="tablist"` / `tab` / `tabpanel`, un seul onglet dans l'ordre
  de tabulation, flèches gauche/droite (rebouclage), Début/Fin. Dock : la flèche
  active ; espaces : elle déplace le focus et Entrée active (monter un espace
  coûte des requêtes).
- **Chargement** : `ctx.loading()` pose un squelette décoratif et un texte pour
  les lecteurs d'écran ; la coque en pose un sur la toile à chaque changement
  d'espace et le retire dès que l'espace y écrit du contenu. Les zones de données
  (`#canvas`, `#explorer-body`, `#inspector-body`, `#evidence-body`) sont des
  régions `aria-live="polite"`.
- **États vides** : un bouton qui fait (`ctx.empty(…, action)`), pas une commande
  à recopier. Sans `action`, une commande que la Console sait exécuter (liste
  blanche, sans argument) reçoit « Lancer dans la Console ». Un explorateur ou un
  inspecteur laissé muet par un espace reçoit une explication et un bouton.

## Actualisation sans rechargement (UI-3)

La coque se met à jour seule : `live.js` ouvre le flux `/api/workspace/stream`
(Server-Sent Events, P10) que le serveur nourrit dès que l'empreinte bouge — une
écriture dans le ledger, un journal de session ou une proposition arrive en moins
de 2 s (`tests/unit/tools/test_workspace_stream.py`). Sans `EventSource`, ou si
l'hôte ne sert pas le flux, elle sonde `/api/workspace/pulse` à l'intervalle que
**le serveur déclare** (`interval_seconds`). Dans les deux cas, quand l'empreinte
change, elle appelle les espaces inscrits par `ctx.live.on`. Quatre règles,
gardées par `tests/e2e/test_workspace_live_pilot.py` (sondage) et
`tests/e2e/test_workspace_live_stream.py` (flux) :

1. **Un onglet masqué ne fait aucune requête** (`visibilitychange` : flux fermé,
   requête en cours annulée, minuteur coupé) ; revenu visible, le flux se rouvre
   (ou un sondage part) tout de suite.
2. **Sans changement, rien n'est relu** : le pouls répond `changed: false` et le
   DOM de l'espace n'est pas touché.
3. **Rien n'est jeté sous les doigts** : une réponse en cours de saisie (champ actif,
   ou texte déjà écrit) retarde l'actualisation de l'espace ; le défilement du
   board et la carte au clavier sont conservés.
4. **Un coût inconnu se lit « inconnu »**, une borne basse « ≥ X USD », jamais
   « 0 » (`costText`) ; une durée incalculable « inconnue ». Le coût d'une carte est
   celui des tentatives de dispatch ; celui de la session hôte n'est mesuré nulle part.

Le compteur de l'onglet « À traiter » (`#decisions-count`, plus de pastille séparée) est le
`count` de `/api/workspace/attention` dans la portée courante (hors « résolu automatiquement »),
caché à zéro, relu au premier pouls et quand l'empreinte du pouls change. Il égale la somme des
tuiles de l'espace. La barre d'état dit « mis à jour il y a Xs », ou
« actualisation suspendue » (onglet masqué), ou « API injoignable depuis Xs ».

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
| `GET /api/workspace/tasks?mission=&status=` | `{columns[8], states[9], ledger, count, tasks[], note?}` — chaque carte porte `session` (vivacité K5, `updated_at` = dernier signe de vie, `duration_s` du premier au dernier signe, `cost` de session) et `cost` (dispatch : `{usd, status exact\|lower_bound\|unknown, unpriced_calls, calls, scope}`, `usd: null` quand rien n'est chiffré, jamais `0`) |
| `GET /api/workspace/pulse?since=&scope=` | Le pouls (UI-3) : `{fingerprint, changed, interval_seconds, scope, decisions?}`. L'empreinte est calculée par `stat` seulement ; avec `since` identique, `changed: false` et aucun détail ; sinon `decisions: {pending, oldest_at, projects[], unreadable?}` (la lecture de `grimoire status`). `scope=portfolio` agrège le registre du cockpit. `interval_seconds` : `GRIMOIRE_PULSE_SECONDS`, borné à 1..300, défaut 5 |
| `GET /api/workspace/stream?since=&scope=` | Le même pouls en flux `text/event-stream` (P10) : un événement `pulse` (charge utile de `pulse`, `id` = empreinte) tout de suite, puis à chaque changement d'empreinte (recalculée toutes les 0,5 s, ledger, baux, journaux de session et propositions), et un battement `changed: false` par `interval_seconds` sans changement. Pas une entrée de `GET_ROUTES` : l'hôte recopie le générateur `workspace_stream()` |
| `GET /api/workspace/tasks/<id>` | La tâche + `board` + `next_moves_require: {colonne: [preuves]}` |
| `GET /api/workspace/tasks/<id>/trace` | `{task_id, task, sources{ledger,hooks,runtime,evidence}, entries[], causes[]}` |
| `GET /api/workspace/files?tier=` | `{projectRoot, tiers[]}` — `tiers[]` : `{id, term, label, note, roots[], editable, exists, truncated, count, files[]}` |
| `GET /api/workspace/file?path=` | Le fichier + `{tier, size, digest, shipped_by_kit, kit_version, editable, override_path?, overridden?, text, truncated, binary}` |
| `GET /api/workspace/file/diff?path=` | `{path, against, comparable, identical, unified, added, removed}` ou `{comparable: false, reason}` |
| `GET /api/workspace/upgrade/changes` | P44 : ce qu'une mise à jour écrirait, comparé au disque sans rien écrire : `{categories[], total, unchanged}` — `categories[]` : `{id, label, files[]}`, `files[]` : `{path, status nouveau\|modifie, label, added, removed}` |
| `GET /api/workspace/upgrade/diff?path=` | P44 : `{path, status, label, added, removed, unified, truncated}` ; 400 pour un chemin que la mise à jour ne toucherait pas |
| `GET /api/workspace/file/usage?path=` | `{path, projections[], loaded_by: {entries[], truncated}}` — inspecteur, onglet Utilisé par |
| `GET /api/workspace/file/history?path=` | `{path, is_repo, commits[]}` — inspecteur, onglet Historique ; `is_repo: false` hors dépôt git |
| `GET /api/workspace/commands` | Le catalogue des sous-commandes que la Console accepte |
| `GET /api/workspace/doctor` | `{ok, code, timed_out, command, lines[], stderr}` |
| `GET /api/workspace/language?path=&text=&line=&col=` | IntelliSense de Source (issue 280) : `{path, tokens[], diagnostics[], completions[]?}` — `tokens[]` : `{line, start, end, kind, glossaryId?}` ; `diagnostics[]` : `{line, start, end, severity, family, message}` ; `completions[]` (seulement si `line`/`col` fournis) : `{label, kind, insertText, detail}`. `text` porte le brouillon en cours d'édition — omis, la lecture vient du disque comme `file` |
| `GET /api/workspace/agents` | `{agents[], skills[], entry_point}` — `agents[]` : `{name, description, definition_ref, tools[], tools_origin, affinity, entry_point, max_turns, skills[], context[], layer, use_when, dont_use_when, tool_boundary, usage: {choices, last_chosen_at}}` ; `skills[]` : le catalogue disponible pour l'assignation (#374) |
| `GET /api/workspace/assist` | Suggestions par modèle local (issue 280, voie 2), lecture sans coût — jamais d'appel au modèle : `{enabled, model, available, reason}`. `enabled: false` = opt-in absent (`source.assist.model` vide) ; `available: false` avec `enabled: true` = Ollama indisponible ou modèle absent de `ollama list` |
| `GET /api/workspace/evidence` | Panneau « Preuves » du rail, possédé par la coque (issue #534) : `{enrolled, profile?, tasks[], count?, note?}` — `enrolled: false` = pas de `grimoire standard init` (`note` nomme la commande) ; `tasks[]` : `{task_id, title, status, gates: {ok, missing[], checks[]}, pack_path, pack_exists}`, la même lecture que `grimoire standard verify`/`grimoire_standard_gate`, jamais une relecture parallèle des fichiers |

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
