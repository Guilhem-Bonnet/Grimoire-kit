# <img src="docs/assets/icons/chart.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

### Corrigé

- **La racine de `grimoire serve` et du cockpit ouvre la vue de travail.** Le
  basculement redirigeait les dix pages d'outil mais laissait `/` sur la page
  vitrine ; la racine mène maintenant à l'espace Piloter, `index.html` demandé
  explicitement reste la vitrine.

### Modifié

- **Basculement, pas 2 (`docs/adr-006-vue-de-travail.md`) — la vue de travail
  devient la page par défaut.** Les cinq lots sont mergés : `grimoire serve`
  et `grimoire cockpit serve` ouvrent désormais `workspace/index.html` dans le
  navigateur (`--open`, activé par défaut) au lieu de `atelier.html` et
  `portfolio.html`. Les quatorze pages historiques restent servies à
  l'identique — rien n'est supprimé avant le pas 3, une mineure ultérieure —
  mais les dix pages *outil* (`atelier.html`, `portfolio.html`, `kanban.html`,
  `observability.html`, `memory.html`, `blueprints.html`, `patterns.html`,
  `extensions.html`, `labs.html`, `documentation.html`) redirigent maintenant
  (302) vers l'espace de la coque qui les remplace ; `?legacy=1` sur l'ancienne
  URL sert encore l'ancienne page sans redirection, pour quiconque n'est pas
  prêt à basculer. Table partagée par les deux hôtes :
  `grimoire.tools.workspace_legacy`. Les quatre pages vitrine (`index.html`,
  `demo.html`, `anatomy.html`, `game-ui.html`) ne redirigent jamais — hors
  périmètre (spec §7).

### Ajouté

- **Squelette de la vue de travail — une coque, deux hôtes, cinq lots.**
  `web/workspace/` porte la coque unique que `grimoire serve` et
  `grimoire cockpit serve` servent à l'identique : six espaces navigables
  (Piloter, Concevoir, Exécuter, Observer, Mémoire, Source), panneaux à trois
  états, dock, palette `⌘K`, mode concentration, thèmes sombre et clair,
  densités Découverte et Concentration. Les espaces sont des stubs qui exposent
  `mount(root, ctx)` : les écrans viennent avec les lots d'implémentation.
  Décisions, alternatives écartées et découpage : `docs/adr-006-vue-de-travail.md`.
- **Lot 1 de la vue de travail — la coque et ses mécaniques.** Les trois états
  de panneau tiennent leur contrat complet : clic sur le rail ouvre en
  surimpression, survol 450 ms entrouvre (jamais au survol du contenu),
  cadenas ou `⌘`/`Ctrl` + clic épingle dans la grille, et le panneau épinglé se
  redimensionne à la souris par une poignée sur son bord. L'état — replié,
  entrouvert, épinglé, largeur — est mémorisé **par espace de travail et par
  projet**, dans `localStorage`, en échec silencieux hors navigation privée.
  Geist et Geist Mono sont embarquées en woff2 (400/500/600 et 400/500, 260 Ko,
  licence SIL OFL 1.1 dans `web/workspace/fonts/OFL.txt`) : plus aucun appel à
  Google Fonts. Neuf tests Playwright de plus couvrent chaque mécanique prise
  séparément (ouverture par clic, entrouverture par survol, non-ouverture au
  survol du contenu, épinglage au cadenas et au clic modifié, redimensionnement
  à la poignée, navigation clavier de la palette, bascule de densité,
  persistance par espace après rechargement).
- **Contrats d'API de la vue de travail**, sous le préfixe `/api/workspace/`.
  Lectures servies par **les deux hôtes** via la table partagée
  `forge_routes.api_get` : glossaire, tâches, détail de tâche avec les preuves
  que chaque pas suivant exigera, timeline unifiée (`task trace`), fichiers par
  étage avec leur empreinte confrontée au catalogue des digests du kit, contenu
  et diff d'un fichier, catalogue des commandes, diagnostic. Écritures réservées
  à l'hôte mono-projet, parce que le cockpit se déclare `readOnly` : claim,
  move, block, close d'une tâche (gate de preuve compris — un refus revient en
  200 avec la preuve manquante nommée), prise d'override, écriture d'un fichier
  d'un étage éditable, exécution d'une sous-commande `grimoire`.
- **`framework/glossary.yaml`** — 61 concepts, source unique des infobulles et
  de la documentation. Un projet peut le surcharger comme n'importe quel fichier
  du kit. Un test refuse un terme cité par l'interface sans entrée au glossaire,
  sur les sources comme sur le DOM rendu.
- **La pile d'infobulles épinglables** (`web/workspace/glossary.js`, lot 2 de
  la vue de travail — spécification §3.2). Survol de 500 ms (800 ms en densité
  Concentration, réduite au nom et au raccourci) ; Alt fige la bulle avec son
  cadenas, le pointeur peut y entrer, sa croix ou Échap la referment ; les
  termes liés ouvrent une bulle enfant, trois niveaux au plus — un quatrième
  est refusé par `glossary.open()` lui-même, pas seulement laissé sans bouton
  pour l'ouvrir. Un clic ailleurs referme les bulles non épinglées et laisse
  les épinglées. Accessible : tout `[data-term]` reçoit un `tabindex` s'il n'en
  avait pas, le focus clavier ouvre la bulle sans délai, `aria-describedby`
  relie l'ancre à sa bulle. `docs/glossaire.md` dérive du glossaire
  (`scripts/gen-glossaire-doc.py`) ; un test unitaire échoue si la page est en
  retard sur le YAML.
- **Console du dock** : `grimoire.tools.workspace_exec` exécute 23
  sous-commandes `grimoire` de lecture, sans shell, sur liste blanche stricte,
  avec drapeaux déclarés et délai maximal. `init`, `up`, `migrate`, `serve`,
  `cockpit`, `upgrade` et `ext` en sont volontairement absents.
- **Harnais de test** : `tests/e2e/` lance `grimoire serve` sur un port haut avec
  `GRIMOIRE_COCKPIT_HOME` détourné, et mesure sur le DOM rendu ce que la
  spécification exige — plancher typographique, contraste, mécaniques au
  clavier. Il se skippe proprement sans Playwright, jamais en faux vert.
- **Lot 3 de la vue de travail — l'espace Concevoir.** Zoom Projet → Workflow
  → Nœud (+ Flotte en lecture seule sur le cockpit) ; au niveau Projet, les
  blueprints du projet servi comme conteneurs en Carte, Board (groupés par
  genre — blueprint classique ou Studio v2) ou Liste (nom, genre, agents
  délégués, équipe, validation, dernière modification) ; au niveau Workflow,
  un éditeur de graphe réécrit contre les routes existantes de bp2
  (`/api/blueprints/<id>`, `/validate`, `/simulate`, `/compile`) — nœuds mis en
  page par rang topologique, bibliothèque des sept primitives en tiroir,
  résultats de validation dans le dock ; au niveau Nœud, le nœud et ses voisins
  directs avec un inspecteur à quatre onglets (Propriétés, Validation, Coût,
  Preuves — ces deux derniers lus sur la pression de contexte et les preuves
  exigées que rend `/simulate`). Nouvelle route partagée
  `GET /api/workspace/blueprints` (`workspace_api.blueprints_view`) : enrichit
  `/api/blueprints` avec ce que la Liste réclame et que
  `project_health.flows` ne porte pas. Limite assumée : l'éditeur de graphe
  n'existe que sur l'atelier — `/api/blueprints/<id>` et ses routes filles ne
  sont câblées que sur le serveur mono-projet ; le cockpit l'affiche en le
  disant plutôt que de tenter un appel qui rendrait 404.
- **Lot 4 de la vue de travail — Piloter, Exécuter, Observer, Mémoire.**
  Piloter : niveau Flotte (cockpit, tableau sur bureau / cartes sur mobile,
  KPI en une carte divisée, « À traiter » toujours visible) et niveau Projet
  (fiche du projet servi, même signaux) ; inspecteur avec mise à jour derrière
  aperçu (`/api/projects/update`, `confirm: false` par défaut) puis
  confirmation. Exécuter : Board 4 colonnes (états repliés nommés sous le
  titre) et Board 8, Liste, Timeline ; carte de tâche à trois niveaux ;
  inspecteur avec critères d'acceptation, preuves déclarées, prochaine porte
  actionnable (`claim`/`move`/`block`/`close`, gate compris) et sa commande
  équivalente dans le dock. Observer : six KPI, coût par modèle (`--s1..3`),
  latence p50/p95/p99 (une teinte neutre — une seule série), spans lents,
  traces par agent ; un seul état vide sur un projet sans trace, jamais un mur
  de zéros. Mémoire : store et graphe d'abord (`/api/memory/status`), couches
  ensuite, l'explication d'architecture derrière un onglet. Les quatre espaces
  ajoutent `api.health(project?)`, `api.memoryStatus(project?)`,
  `api.doctor(project?)`, `api.costModel()` et `api.updateProject()` à
  `web/workspace/api.js` (routes déjà servies par les deux hôtes, ou route
  cockpit existante pour `updateProject`) ; aucun ne dépend d'un jeu de
  données de démonstration.
- **Lot 5 de la vue de travail — l'espace Source et la Console du dock.**
  `web/workspace/spaces/source.js` remplit l'écran nouveau : arbre par étage
  (overrides possédés, kit généré, projections des hôtes) avec badge de
  dérive sur un override qui diverge de son homologue kit ; éditeur à trois
  onglets — Source (textarea avec numéros de ligne), Diff contre le kit
  (unifié, rendu par le serveur, ajouts et retraits par tokens), Rendu
  (Markdown minimal, sans dépendance, ADR-006 D2) ; bandeau « Ce fichier est
  généré par le kit » avec prise d'override en un geste ; enregistrement
  `⌘S` avec confirmation dans le dock. Inspecteur à trois onglets — Fichier
  (étage, version, empreinte au catalogue, override), Utilisé par (projections
  vers `.claude/`, `.github/`, `AGENTS.md` et fichiers qui citent celui-ci,
  deux nouvelles routes `GET /api/workspace/file/usage` et
  `GET /api/workspace/file/history`), Historique (`git log --follow`, ou un
  vide honnête hors dépôt git). La Console du dock — invite, historique au
  clavier, complétion sur la liste blanche, exécution via
  `POST /api/workspace/command`, refus explicite affiché — est rendue
  entièrement à travers le contrat `ctx.dock.log/clear` du lot 1, sans jamais
  toucher le DOM du dock. Neuf tests Python de plus (badge de dérive, usage,
  historique, symlink refusé, hôte étranger refusé sur `/api/workspace/`) et
  cinq tests Playwright sur un projet jetable réel : ouvrir, éditer, override,
  diff, `grimoire doctor` vert après, Console qui exécute et qui refuse.

### Corrigé

- **Le portefeuille peut désormais dire ce qu'il ne sait pas.**
  `project_health()` rend `commits_total` (compté sur `HEAD`, `None` hors
  dépôt git ou sans commit) et `ci_status` (`"unknown"`, honnêtement — aucune
  sonde locale ne le mesure encore) sous ces noms exacts : la vue héritée lisait
  `p.ci` et `p.commits`, deux champs que la donnée réelle n'a jamais portés.
  `antifragile` reste `None`, avec `antifragile_note: "pas encore mesurée"` —
  jamais un score à zéro pour un score qui n'a simplement jamais été calculé.
  `demo` vaut toujours `False` : ce module ne lit aucune donnée de vitrine.

- **Trois valeurs de la palette ne tenaient pas le contraste que la
  spécification exige d'elle-même**, trouvées par la mesure et non par la
  relecture. En thème clair, `--ink3` valait 3,78:1 sur `--e1` là où la spec
  annonce 4,6 ; il passe à `#656a72`. En thème sombre, `--ink3` tenait sur
  `--e1` mais tombait à 4,35:1 sur `--bar`, où la barre d'état et l'écho du dock
  le rendent ; il passe à `#818891`. Le libellé blanc de l'action primaire en
  thème clair valait 4,29:1 ; `--pri` passe à `#d24619`. Détail des mesures :
  `docs/adr-006-vue-de-travail.md`, section « Ce que la mise en œuvre a corrigé ».

- **Intégration des cinq lots — la palette `⌘K` listait les fichiers de Source
  mais n'en ouvrait aucun.** `run()` faisait `goto('source')` sans le chemin
  choisi, et `shell.js` écrasait `location.hash` avant que `source.js` n'ait pu
  le voir. `goto(id, params)` relaie désormais `{ file }` dans `ctx.params`,
  lu une fois au montage suivant — sans persister dans le hash.

- **Intégration des cinq lots — le raccourci `2` du rail (bibliothèque)
  n'activait rien**, ni au clic ni au clavier, faute d'un panneau de la coque
  à cet identifiant. `ctx.rail.on(id, handler)` laisse l'espace monté
  enregistrer ce que fait un rail sans panneau dédié ; Concevoir y branche le
  tiroir de bibliothèque de nœuds, remis à zéro à chaque changement d'espace.

## [3.38.0] - 2026-09-04

### Évalué

- **Campagne evals 2026-09-04 — bras `enforced` contre `activated-v3` : effet
  non démontré, indicatif.** Première campagne pré-enregistrée après
  l'amendement A2 (`evals/reports/2026-09-04/`). Le bras `enforced` ajoute à
  l'activation les deux hooks bloquants du kit (refus PreToolUse, gate Stop)
  au profil `governed`. Sur 24 runs par bras (3 répétitions, sous puissance :
  règle d'arrêt budgétaire puis limite de dépense du compte), le gate obtient
  l'artefact qu'il exige (`context-bundle` 23/24 contre 0/24) et rien d'autre :
  complétion 3 contre 5, coût par run +41 %, 0 régression dure contre 1. Le
  blocage change le volume de preuve, pas ce qui est livré. Verdict hors
  compteur de la clause 2 d'A2. Le mécanisme `enforced` est committé
  (`evals/witnesses/web-app-todo/enforced/`), ainsi que le runner de campagne,
  les paquets de jugement aveugle et l'agrégateur (`evals/runner.py`,
  `evals/judge.py`, `evals/aggregate.py`).

### Modifié

- **La borne `chromadb<0.7` est un choix instruit, plus un héritage.** Mesuré
  sur deux venv jetables (#174) : les sept appels que le backend `mempalace`
  fait au client embarqué rendent les mêmes clés en 0.6.3 et 1.5.9, un palais
  0.6 se relit tel quel en 1.x — et pas l'inverse — et la suite mémoire est
  verte sous 1.5.9. Lever la borne n'apporte rien et ajouterait une CVE
  d'injection pré-authentification au périmètre audité ; les trois CVE
  waivées n'ont de correctif sur aucune version. La borne reste, son
  commentaire dit pourquoi, et les trois waivers sont reconduits au
  2027-02-28 après revérification OSV du 2026-09-04.

- **L'étage TestPyPI, qui n'a jamais tourné, disparaît de `publish.yml`.** Le
  projet n'a jamais été enregistré sur test.pypi.org : le job échouait à
  chaque tag et son `continue-on-error` le faisait passer pour une
  pré-vérification (#195). Ce qui vérifie réellement qu'une version
  s'installe est nommé et documenté dans `CONTRIBUTING.md` : `make
  wheel-check` (wheel installée dans un venv neuf) avant le tag, les jobs
  `build` et `test` de `publish.yml` au tag, dont `publish-pypi` dépend sans
  `continue-on-error`. La cible `make publish-test` disparaît avec l'étage.

### Ajouté

- **`grimoire task trace <id>` — la cause d'un arrêt sans ouvrir un fichier
  (L4, #139).** Timeline unifiée d'une tâche, lue depuis les quatre journaux
  qui portaient déjà le `task_id` sans que rien ne les lise ensemble : Mission
  Ledger (transitions, incidents), TraceLedger des hooks (outils refusés par la
  policy, clôtures refusées), RuntimeKernel (run events, checkpoints, abort et
  sa raison), EvidenceService (packs, verdicts). Les entrées qui expliquent un
  arrêt sont marquées et reprises en « Cause(s) d'arrêt » ; `--causes` les
  isole, `--output json` rend tout. Aucune source absente n'est inventée, aucun
  dossier n'est créé en lisant, et la stack legacy `observatory.py` n'est pas
  sollicitée. Deux écritures rendent cela possible : le gateway de hooks —
  seul écrivain du TraceLedger — porte désormais le `task_id` résolu sur chaque
  événement (les refus de policy étaient journalisés sous un identifiant vide,
  donc introuvables par tâche), et un gate de transition rouge laisse une trace
  `grimoire.task-gate` (au TraceLedger, jamais au Mission Ledger : un refus
  n'est pas un changement d'état).

- **Aucun niveau de la norme ne laisse plus d'exigence obligatoire sans
  artefact.** `grimoire standard traceability` listait dix-sept exigences
  `AG-*` obligatoires jusqu'à N4 que le kit ne couvrait par rien (#246). Six
  artefacts les ferment, chacun avec son gabarit, son vérificateur et ses
  tests : le dossier d'acceptation (AG-QUA-003, tous profils), le registre de
  rétention (AG-RET-001/003/004/005, tous profils), le registre d'outils avec
  contrats MCP et capture des erreurs (AG-TOL-001/003/005, dès `controlled`),
  le registre d'incidents avec politique de containment (AG-INC-001/002/003,
  dès `controlled`), la matrice risques/contrôles/preuves pré-remplie des
  défauts IA que la norme nomme (AG-AUD-003, AG-QUA-005, dès `controlled`) et
  le registre des capacités dynamiques (AG-DYN-001 à -005, dès `governed`).
  Deux exigences tiennent dans un champ : le bloc `wip:` d'`orchestration-policy`
  (AG-ORC-004 — une délégation `allowed` est une erreur) et la section
  `Traceability` de la déclaration de conformité (AG-AUD-001). Pour celle-ci,
  `grimoire standard traceability <projet>` joint désormais à la matrice le
  verdict que `standard verify` rend sur chaque artefact, et compte les
  exigences effectivement vérifiées. Un projet neuf vérifie sans erreur
  nouvelle sur les cinq profils ; un projet enrôlé reçoit les fichiers par
  `standard fix --apply`. Deux tests interdisent le retour des trous : les
  dix-sept identifiants doivent rester couverts par un artefact requis au
  niveau où la norme les attend, et la section `gaps` doit rester vide.
- **Les agents lisent, réclament et clôturent leurs tâches par MCP (L3,
  #138).** Le serveur MCP expose `task_list_ready`, `task_show`, `task_claim`,
  `task_update` (move / block / close) et `task_context`. Ils appellent le
  service que `grimoire task` appelle désormais aussi
  (`grimoire.missions.service.TaskService`) : même machine à états, même gate
  de preuve, même refus structuré — la preuve manquante et le remède, et rien
  d'écrit au ledger. Un contrôle négatif échoue si l'un des deux contourne le
  gate. Prouvé par un vrai client du SDK `mcp` sur un projet enrôlé en
  `governed` : lister, réclamer, déplacer, être refusé sans pack de preuve,
  prouver, clore ; le board projeté passe `ready → in_progress → accepted`.

### Corrigé

- **Le rendu des surfaces hôtes dit quand il échoue.** `init`, `doctor --fix`
  et `standard init` régénéraient agents, skills, commandes et hooks derrière
  trois copies d'un `except Exception: return []`, chacune justifiée par
  « `host status` rapportera la dérive ». Un projet dont les surfaces ne se
  rendaient pas sortait donc d'`init` avec un rapport vert et aucun hook, et
  `doctor --fix` affichait « 22/22 checks passed ». Un seul écrivain désormais
  (`grimoire.hosts.sync`), qui rend l'échec en donnée et l'écrit sur stderr ;
  les trois commandes affichent l'avertissement, et `standard init -o json`
  le porte dans `host_surfaces`.

- **Un pheromone board corrompu est mis de côté, plus écrasé.** Un board
  tronqué (disque plein, deux hooks concurrents sans `flock`) était lu comme
  vide, puis réécrit par le dépôt suivant : tout l'historique stigmergique
  disparaissait et `deposit_pheromone` rendait un `Pheromone` comme si de
  rien n'était. Le fichier illisible est désormais renommé
  `pheromone-board.json.corrupt-<horodatage>` avant qu'un board vide reparte,
  et une ligne sur stderr le dit. La copie autonome `framework/tools/` a le
  même défaut, documenté en #265 (zone gelée).

- **Un fichier de gates illisible ne laisse plus passer toutes les
  transitions.** `evidence-gates.yaml` cassé était lu comme `{}` : aucune
  transition déclarée, donc aucune gardée, donc `task move` et `task close`
  passaient sans preuve — le gate était d'autant plus vert que son fichier
  était abîmé, à rebours de ce que le module promet. Le lecteur distingue
  désormais « absent » (aucune transition) de « illisible » (`GatesFileError`)
  : `check_transition` rend un verdict bloquant `hard_fail` qui nomme le
  fichier et la cause, `task show` le signale au lieu de se taire, et un
  registre de fournisseurs cassé dit « illisible » au lieu du faux diagnostic
  « aucun fournisseur activé ».

- **Un manifeste d'équipe illisible est nommé, plus omis.** `parse_team`
  répondait `None` pour « pas une équipe » comme pour « YAML cassé » : l'équipe
  manquait à `workflows teams` sans une ligne. Le chargeur distingue les deux
  (`TeamManifestError`), `load_team_catalog` rend les équipes lues et les
  fichiers illisibles avec leur cause, et `workflows teams` les affiche — en
  texte et dans `unreadable` en JSON.

- **Chaque PR exécute la CI complète.** `ci-sdk.yml` et `ci-validate.yml`
  filtraient les `pull_request` par chemin : une PR qui ne touchait ni `src/`
  ni `tests/` ne déclenchait aucun des checks que la protection de `main`
  exige depuis le 2026-09-04, et restait bloquée sans jamais avoir été
  vérifiée — le contraire de ce qu'un check requis promet. Les filtres
  restent sur `push` ; sur une PR, tout tourne. Même retrait pour
  `agentic-standard.yml`, dont le check `standard` est requis.
- **Les hooks ne dégradent plus en silence.** Quatre gardes pouvaient passer
  au vert sans l'avoir mérité, et le défaut n'est apparu qu'en écrivant le
  contrôle négatif. (1) Une décision qui plantait rendait `ALLOW` — sur
  `PreToolUse`, c'est `permissionDecision: allow`, et l'hôte n'affiche pas le
  contexte explicatif : le plantage du moteur de politique était une
  auto-approbation sans trace. Un appel que la politique n'a pas pu juger
  rend désormais `ask`, avec la cause dans le motif ; les hôtes qui ne savent
  pas demander la reçoivent en contexte. (2) Un gate de preuve qu'on ne sait
  pas évaluer — task-board illisible — laissait fermer une tâche `governed`
  avec un contexte que l'hôte ne lit pas sur `Stop` ; il bloque désormais une
  fois, en nommant le fichier à réparer, et `stop_hook_active` garantit que
  le second `Stop` passe. Les profils non bloquants sont prévenus, pas
  bloqués. (3) Le message système annonçait « capsule écrite avant
  compaction » même quand le disque avait refusé ; il dit maintenant qu'elle
  n'a pas été écrite, et pourquoi — et la capsule est écrite même quand les
  gates sont inévaluables, avec l'identifiant de tâche et le profil, les deux
  faits que la fenêtre suivante ne sait pas reconstruire. (4) Un verdict
  non bloquant sur `Stop` ne vivait que dans `additionalContext`, qu'aucun
  hôte ne lit à cet événement ; il est aussi rendu en `systemMessage`.

- **Deux étapes de CI ne pouvaient pas échouer, une troisième ne pouvait pas
  se prononcer.** Le verdict sur `grimoire-init.sh doctor` soustrayait trois
  `grep -c` l'un de l'autre — toute ligne citant Qdrant ou un chemin attendu
  comptait en négatif, erreur ou non, et un doctor qui n'avait pas tourné
  laissait un fichier vide, zéro erreur, étape verte ; `scripts/ci-doctor-gate.py`
  exige la bannière du doctor et fait échouer l'étape sur toute ligne `✗`
  hors manques attendus, en la nommant (six contrôles négatifs). L'étape de
  dérive du standard amont sortait en 2 ou 3 sous `continue-on-error` : rouge
  en permanence, ignorée par tous, et une vraie erreur de la commande passait
  au même rouge ignoré ; les deux cas connus sont dits en avertissement et
  sortent en 0, tout autre statut échoue. Enfin le job `standard`, check
  requis par la protection de `main`, était filtré par chemins : sur une PR
  qui ne touchait pas le standard il restait « attendu » sans jamais se
  prononcer et la PR ne pouvait pas être fusionnée — un garde qui bloque tout
  ce qu'il ne regarde pas. Il tourne sur chaque PR.

- **Le hook SessionStart nommait toujours `bootstrap`, quelle que soit la
  tâche réclamée.** Deux causes, toutes deux vérifiées sur un projet neuf :
  `.claude/activation-context.md`, écrit par `standard init`, portait
  `bootstrap` en dur et primait sur la tâche résolue ; et la résolution ne
  lisait que le board, une projection que rien ne régénérait après un claim.
  Le fichier installé est désormais un gabarit (`{task_id}`), un fichier
  ancien resté au défaut est rendu avec la tâche courante, et la résolution
  préfère le **claim actif du Mission Ledger** (`claimed` / `running`,
  restreint à `GRIMOIRE_ACTOR` s'il est posé) au board, sous `GRIMOIRE_TASK_ID`.
  Chaque écriture de `grimoire task` reprojette le board du standard ;
  `grimoire standard activation-context` sans `--task-id` résout la tâche
  courante au lieu de `bootstrap`. La règle est documentée dans la référence
  CLI (« Quelle tâche la session porte »).

- **Le score ne route plus les checks par correspondance de préfixe.** Un
  préfixe non déclaré tombait silencieusement dans le bucket `artifacts`.
  Mesuré sur les 262 identifiants que le kit émet aujourd'hui : **26 étaient
  mal dirigés** — les huit `gates.*`, que le préfixe déclaré `gate.` ne peut
  pas atteindre ; les douze `patterns.*` et les quatre `remote.*`, qu'aucun
  préfixe ne nommait. Chaque check émis est désormais déclaré dans
  `core/standard_checks/registry.py`, et trois tests interdisent la dérive : un
  check sans déclaration, une dimension inconnue du score, une entrée de
  registre sans check correspondant. La table de préfixes subsiste en repli
  pour les checks qu'un projet émet lui-même. Les dix-sept checks `claims.*` et
  `surfaces.*` arrivés avec la 3.37.0 sont déclarés avec les autres.

- **Deux checks étaient comptés sur une dimension sans poids, donc jamais
  comptés.** `observability_cockpit` figurait dans la table de routage sans
  entrée dans les poids ; or le calcul rend `100` dès que le poids est nul
  (`percentage = ... if weight else 100`). Les deux `promptver.*` marquaient
  donc 100 % quoi qu'il arrive, tout en ne pesant rien : verts à l'affichage,
  absents du résultat. Ils rejoignent `runtime_journal`, la dimension
  d'observabilité réellement pondérée.

  Ces deux corrections laissent le score inchangé sur un projet fraîchement
  scaffoldé — vérifié sur les cinq profils, dimension par dimension, aucun des
  checks concernés ne s'y déclenche. Elles ne changent le score que là où ces
  checks tombent, ce qui était précisément le défaut.

- **Le contrat d'adapter de runtime externe est unifié.** Il existait trois
  fois : un `_slugify` identique dans les trois adapters, une méthode d'entrée
  nommée successivement `import_flow`, `import_graph` puis `convert`, et aucune
  surface commune entre les trois rapports. `runtime/adapter_base.py` fournit
  l'unique `slugify`, un protocole `ImportReport` et un protocole
  `RecipeAdapter` dont la méthode canonique est `to_recipe`. Les anciens noms
  restent en alias, conformément à l'ADR-002. `grimoire.runtime` exporte
  désormais `Recipe`, `RecipeStep` et `VerificationGate`, qu'un auteur
  d'adapter devait importer depuis un sous-module.

  La valeur de `VerificationGate.blocking` n'est **pas** uniformisée, et c'est
  délibéré : le `blocking=False` de CrewAI et LangGraph est ce qui fait
  atterrir une tâche importée en `NEEDS_VERIFICATION` au lieu de se clôturer
  seule ; le `blocking=True` de Gas City porte sur les molécules qui déclarent
  exiger une preuve. Les deux sémantiques sont justes.

- **`GasCityConverter` vérifie enfin l'`output_schema`.** Il le transportait
  jusqu'à la `Recipe` sans jamais l'exiger, là où CrewAI et LangGraph refusent
  une définition qui ne déclare pas sa sortie : une formule invérifiable
  rendait `ok`. Son rapport gagne `missing_output_schema`, présent dans
  `to_dict()`. **Rupture assumée** : une formule sans `output_schema` est
  désormais refusée.

## [3.37.0] - 2026-09-03

### Ajouté

- **Le bridge trace chaque artefact vers les exigences de la norme.** Le
  profile-map parlait en noms d'artefacts propres au kit ; `grep AG-` sur le
  bridge rendait zéro ligne. `traceability.yaml` relie les 43 types d'artefacts
  et les familles de vérificateurs aux `AG-*` et `CTRL-*` qu'ils satisfont, avec
  la citation de la matrice normative qui justifie chaque lien — et une raison
  pour chaque artefact qui n'en a pas (dix-sept). Chaque profil porte son niveau
  de conformité, N1 à N5. `grimoire standard traceability` rend la matrice d'un
  profil et les exigences obligatoires à son niveau que rien ne couvre encore.
  C'est l'artefact qu'AG-AUD-001 demande.
- **Le garde de release vérifie que chaque changement fusionné a son entrée,
  au bon endroit.** Il vérifiait qu'`[Unreleased]` était vide et que la section
  la plus récente portait le numéro publié — deux propriétés vraies sur la
  3.36.0 alors que deux PR n'avaient aucune entrée et que trente-huit blocs de
  deux autres avaient glissé sous `[3.35.0]`, une version publiée sans eux : une
  PR ouverte avant une release et fusionnée après voit git recaler ses lignes
  par contexte. Pour chaque commit `feat`, `fix` ou `perf` depuis le dernier
  tag, le garde exige qu'il touche `CHANGELOG.md` et que chaque titre d'entrée
  qu'il a ajouté soit aujourd'hui sous la version publiée ou sous
  `[Unreleased]`. Sans tag atteignable, la couverture est déclarée non vérifiée
  — et non vérifié n'est pas vérifié.
- **Deux artefacts que la norme rend obligatoires et que le kit ne livrait
  pas.** Le claim ledger (AG-QUA-002, exigé dès le premier niveau) relie chaque
  affirmation qui pèse sur une décision à ce qui la prouve : `claim-ledger.md`
  est généré aux côtés de l'evidence pack pour tous les profils. Le registre des
  surfaces runtime (AG-TOL-007 et AG-RET-006, exigés dès `governed`) donne à
  chaque hook, agent, policy ou sortie un owner, un mode, une rétention et un
  statut : `runtime-surface-registry.yaml` est généré pour `governed` et
  `production`. Deux vérificateurs les lisent : un registre encore vierge est un
  avertissement, il attend d'être rempli ; une affirmation dite prouvée sans
  preuve, ou — en profil gouverné — utilisée sans l'être, et une surface sans
  owner sont des erreurs. Un projet déjà enrôlé les reçoit par
  `grimoire standard fix --apply`.

- **La persona d'entrée se choisit par projet.** Elle était `concierge` en dur :
  `collect_agents` acceptait un autre nom, mais son seul appelant ne le passait
  jamais. Un projet qui porte déjà son propre point d'entrée — un orchestrateur
  chargé par `CLAUDE.md` — en recevait un second à chaque `session_start`, sans
  rien qui dise lequel prime. `agents.entry` dans `project-context.yaml` nomme
  la persona (`concierge` par défaut) ou, vide, déclare qu'il n'en faut aucune.
  `host status` montre la persona retenue et signale une clé qui nomme un agent
  absent au lieu d'en inventer un. Le schéma et `lint` connaissent la clé.
- **Le bridge épingle la révision du standard qu'il trace.** `profile-map.yaml`
  nommait le corpus normatif par nom de dépôt et chemin de fichier, sans SHA ni
  date : impossible de dire si le bridge avait été relu après le dernier commit
  de la norme autrement qu'en comparant les deux dépôts à la main.
  `metadata.upstream_standard` porte désormais `remote`, `commit` et
  `pinned_on` ; `grimoire standard upstream` compare la révision épinglée à la
  tête distante et sort 0, 2 (le standard a avancé), 3 (injoignable, donc non
  vérifié) ou 1 (aucun pin). La CI du bridge l'exécute en avertissement.

### Corrigé

- **Quarante-six outils de `framework/tools/` ne meurent plus sur une console
  cp1252.** Filets de tableau, flèches, coches : chacun levait
  `UnicodeEncodeError` sur une simple commande de lecture chez un utilisateur
  Windows — une classe, pas un incident, révélée par la matrice Windows de
  #191. Le correctif est de classe et tient en deux lignes en tête de chaque
  `main()` : la sortie est reconfigurée en UTF-8 avec remplacement, et un flux
  sans `reconfigure` est laissé en paix. Pas de module partagé : ces outils
  sont chargés de trois façons — script, `import_module`, chargeur par chemin —
  et seul un code sans import survit aux trois. Un test prouve d'abord qu'une console
  cp1252 meurt bien sur un filet, puis que le même texte passe ; un autre
  refuse tout outil qui imprime hors cp1252 sans forcer l'UTF-8. Aucune variable
  d'environnement posée en CI : la matrice ne verdit que si le bug est corrigé.

- **`grimoire setup` écrit la source de vérité qu'il déclare.** Les options
  `--user`, `--lang`, `--doc-lang` et `--skill-level` vivaient dans un objet en
  mémoire : `apply` ne réécrivait que `copilot-instructions.md`, puis vérifiait
  ce miroir contre l'objet en mémoire et annonçait « All config files are in
  sync » — au-dessus de la divergence qu'il venait de créer, que `setup --check`
  signalait la seconde d'après. `apply` écrit désormais la section `user:` de
  `project-context.yaml` en premier — en la créant si le projet est antérieur à
  son introduction, sans toucher `project.name` — puis les miroirs, puis vérifie
  les miroirs contre le fichier relu. Un « in sync » ne peut plus être annoncé
  au-dessus d'une divergence.

## [3.36.0] - 2026-09-03

### Ajouté

- **La persona d'entrée entre dans la session.** `collect_agents` marquait
  depuis toujours une persona `entry_point`, et `ProjectSurface.entry_agent`
  savait la retrouver ; l'accesseur n'avait aucun appelant. Aucun hôte ne sait
  ouvrir une session *à l'intérieur* d'un agent — Claude Code n'instancie un
  sous-agent que par son outil Agent, Copilot que par le sélecteur de chat. Le
  manque est nommé : `HostProfile.agent_autostart`, faux sur les cinq profils,
  et `gaps_for` en donne le substitut hôte par hôte. Le substitut est fourni :
  `decide_activation` compose désormais la persona puis la directive du
  standard. La session n'est pas ouverte dans l'agent, sa persona est remise à
  la boucle principale, qui garde toute la surface d'outils de l'hôte.

- **Les workflows se déclarent.** Un frontmatter `kind` / `description` /
  `agents` / `team` / `triggers` fait entrer un workflow au catalogue, plutôt
  que de le deviner depuis son emplacement — sous `workflows/` vivent aussi
  des gabarits de rapport rendus à chaque run. Les six orchestrations et les
  sept commandes livrées le portent désormais.

- **`grimoire workflows teams`** — les équipes disponibles, leurs membres et
  la chaîne de handoff.


- **« Est-ce que ça tourne, et où ? » a une réponse.** Le noyau d'exécution du
  kit tenait déjà le registre — instances, événements, checkpoints sous
  `_grimoire-runtime-output/runtime/` — mais rien ne le lisait. Le portefeuille
  et le tableau de bord affichent maintenant les exécutions en vol avec l'étape
  courante nommée par leur dernier checkpoint, et ce qui reste à faire.
  Un processus interrompu n'écrit jamais son statut terminal : au-delà d'une
  heure sans signal, son exécution est montrée « sans nouvelles » plutôt que
  comptée comme active.

- **Le portefeuille pilote la flotte.** Chaque carte porte désormais trois faits
  vérifiables, tous issus de `grimoire.tools.project_health` :
  l'**alignement kit** (par digest de contenu — un fichier est en retard quand
  le kit connaît une révision plus récente du même chemin, pas quand son
  numéro de version est ancien), les **flows** réellement composés dans le
  projet, et l'**activité** : dernière trace écrite, avec sa fraîcheur, plus les
  tâches que le board déclare en cours. Rien n'affirme qu'un processus tourne —
  on rapporte ce qui est écrit sur le disque et ce que le projet dit de lui-même.

- **Mettre un projet à jour depuis l'UI.** `POST /api/projects/update` lance
  `grimoire up` sur le projet, sur les deux hôtes. L'aperçu (`--dry-run`) est le
  défaut ; l'écriture réelle exige `confirm: true` et laisse une trace gouvernée.
  Le portefeuille en fait un parcours en deux temps : on voit ce qui changerait,
  puis on décide.

- **`GET /api/health`** — alignement, flows et activité d'un projet, servi par
  l'atelier comme par le cockpit. La couche `health.json` est générée avec les
  autres.

- **Le tableau de bord de l'atelier porte les mêmes indicateurs** que le
  portefeuille — alignement kit et dernière activité, lus sur `/api/health` :
  les deux surfaces disent la même chose du même projet.

- **Le sélecteur de projets arrive sur le portefeuille.** Il vit maintenant dans
  `web/project-picker.js`, chargé à la demande par l'atelier comme par le
  cockpit : la page d'accueil du cockpit était le seul endroit sans entrée pour
  enrôler un projet, alors que c'est le plus naturel. Une seule implémentation —
  deux copies auraient divergé à la première correction.

- **Découverte des projets depuis l'atelier.** Le bouton de projet de la barre
  latérale ouvre un vrai sélecteur : les projets connus de la machine, une
  navigation dossier par dossier (ou un chemin collé), et un scan borné d'une
  racine qui propose sans enrôler. Choisir un projet re-route le serveur en
  cours — `grimoire serve` devient multi-projets sans second processus.

- **Nouvelles routes locales** : `GET /api/projects`, `GET /api/fs/browse`,
  `GET /api/data/status`, `POST /api/projects/{select,add,scan}`,
  `POST /api/data/refresh`. Le cockpit sert lui aussi la découverte
  (`/api/fs/browse`, `/api/projects/add|scan`) : les pages Mémoire, Kanban et
  Observatoire portent le chrome de l'atelier et y affichent le sélecteur, dont
  deux entrées sur trois auraient sinon renvoyé un 404.

### Modifié

- **Le seuil de couverture passe de 70 % à 75 %.** La couverture mesurée est
  de 75,6 %, identique en local et sur les trois plateformes de la matrice :
  le seuil laissait cinq points acquis qu'une régression pouvait rendre sans
  que rien n'échoue. Le seuil se relève quand la couverture monte, jamais
  l'inverse.

- **`cmd_memory` sort de la liste des fichiers hérités du ratchet.** Les
  commandes `memory graph` et `memory vector` partent dans
  `cmd_memory_projections.py` — même convention que `cmd_memory_ops.py`. Le
  module principal passe de 1554 à 1284 lignes, sous le seuil de 1500 : trois
  fichiers hérités deviennent deux. Aucun changement de surface CLI.

- **La récupération hybride est le chemin par défaut.** La fusion RRF du
  classement vectoriel et du classement BM25 existait, était testée, et
  n'était atteignable que derrière un `--hybrid` que rien n'activait : le
  serveur MCP — le seul chemin de lecture qu'empruntent les agents — appelait
  la recherche mono-backend. L'index compagnon était donc écrit à chaque
  `store` et interrogé par personne. La fusion s'applique désormais partout où
  il y a deux classements à fusionner ; `--no-hybrid` force le backend seul.



- **Les journaux d'événements sont lus par la fin.** `activity()` chargeait
  chaque fichier en entier pour n'en garder que la dernière ligne horodatée —
  204 Kio sur ce poste pour le journal task-flow, et ces fichiers ne font que
  grossir (un autre y atteint 14 Mo). Fenêtre de 64 Kio, ligne tronquée écartée.

- **Pas de couche `health.json` générée.** La fiche d'en-tête porte ce dont le
  portefeuille a besoin et `/api/health` sert la vérité fraîche sur les deux
  hôtes : une couche de plus se serait périmée entre deux régénérations et
  aurait pu contredire l'API.

- **La surface HTTP de l'atelier quitte `forge_server`** pour
  `grimoire.tools.forge_http` : gardes d'hôte, table de routage, service de
  fichiers et flux SSE d'un côté, `ForgeAPI` de l'autre. Le module dépassait le
  seuil de 1500 lignes du ratchet de taille ; la coupe suit une frontière réelle
  — ce qui se teste par une requête, et ce qui se teste par un appel de méthode.
  `make_handler`, `serve` et `main` restent ré-exportés à leur ancienne place.

- **Un seul parcours du dossier des blueprints.** `blueprints_list` et la vue
  santé lisaient chacune `_grimoire/blueprints`, avec deux définitions de son
  emplacement : elles auraient fini par répondre différemment sur le même
  projet. L'inventaire est désormais unique (`/api/blueprints` gagne au passage
  l'état de validation et la date de compilation).

- **Une mise à jour par projet à la fois.** `grimoire up` est idempotente, pas
  réentrante : deux exécutions concurrentes écriraient les mêmes fichiers en
  même temps. Un verrou par projet refuse la seconde au lieu de la lancer.

- **Un processus qui ne démarre pas est rapporté, pas levé.** L'`OSError` de
  `subprocess.run` remontait jusqu'au handler HTTP, qui ne l'attrapait pas —
  500 sans explication pour une UI qui attend un compte rendu.

### Corrigé

- **L'atelier local ne propose plus la démo ni « pip install » à qui
  l'exécute déjà.** `grimoire-mode.js` force `index.html` et `portfolio.html`
  en habillage vitrine, et `forge-nav.js` y rendait la nav publique telle
  quelle : un lien « DÉMO » vers la vitrine marketing et un bouton
  « LANCER L'ATELIER → pip install grimoire-kit », sur la page même qui pilote
  les projets de la machine. L'origine est une dimension à part du mode :
  `grimoire-mode.js` expose `window.GrimoireLocal`, et la nav s'en sert pour
  omettre la démo et remplacer l'invitation à installer par un lien direct vers
  l'atelier. Le test exécute les deux scripts sous Node et lit ce que la nav
  rend selon l'origine.

- **Quatre prompts livrés redisaient une commande du SDK.**
  `/grimoire-status`, `/grimoire-health-check`, `/grimoire-self-heal` et
  `/grimoire-pre-push` refont ce que `grimoire status`, `doctor`,
  `doctor --fix` et `check` font déjà — et occupaient la moitié du catalogue,
  qui donnait l'impression d'un produit sachant seulement diagnostiquer. Ils
  déclarent désormais la commande qui les remplace, sortent de la vue par
  défaut (`--all` les montre) et ne sont plus déployés dans les projets neufs.
  Ils restent livrés et installables : un projet qui les a ne les perd pas, et
  `workflows doctor` ne les réclame plus. Les trois autres —
  `/grimoire-changelog`, `/grimoire-dream`, `/grimoire-session-bootstrap` —
  lisent l'historique et la mémoire pour en tirer une synthèse, ce qu'aucune
  commande ne fait : ils restent de plein droit.

- **Les champs `team` et `patterns` étaient déclarés et vides.** Deux
  workflows de plus déclarent leur équipe — `subagent-orchestration` nomme le
  roster de `team-build`, et la spécialité de `team-ops` contient « Incident
  Response » — et trois citent le pattern qu'ils instancient (`ORC-01` pour
  les deux orchestrateurs, `ORC-09` pour le checkpoint d'état). Les trois
  restants n'en déclarent aucun : leurs fichiers ne l'ancrent pas, et le
  deviner contredirait la règle qui veut qu'une description soit dérivée de
  son artefact. Deux tests refusent un identifiant de pattern hors catalogue
  et une équipe qui ne se résout pas.

- **Deux workflows livrés perdaient leur frontmatter en silence.** Leur
  description contenait un `:` non échappé ; le YAML échouait, le parseur
  renvoyait un dictionnaire vide, et le workflow arrivait sans nom ni agents.
  Un test paramétré sur les fichiers livrés ferme le cas.

- **`grimoire serve` ne montre plus les données d'un autre projet.** Le site
  embarqué dans la wheel contient l'instantané de la vitrine publique : des
  projets inventés (« Atlas Ops », « Sentinel Sec », « Ledger Data ») et les
  chiffres du dépôt du kit. Comme `serve` n'exposait aucune surface projet,
  l'UI retombait sur cet index, n'y trouvait pas le projet ouvert et servait le
  primaire : Mémoire affichait « aucun backend · 141 entrées » sur un dépôt
  vide, Kanban dix tâches inventées, Observatoire des traces d'agents datées
  d'il y a deux minutes. Les couches de télémétrie (`meta`, `taskboard`,
  `observatory`, `activity`, `insights`, `memory`, `projects.json`) ne viennent
  désormais que d'une génération faite sur le projet servi ; leur absence est
  un 404, et les pages affichent leur état vide. Les références du kit
  (catalogue de patterns, marketplace, anatomie, couverture) restent servies.

- **Le nuage de la page Mémoire montre de vrais embeddings, ou rien.** Il était
  tiré au sort — `random.Random(42)` autour de cinq centres, avec les vrais noms
  de types dessus. Il projette maintenant les vecteurs que le backend possède
  réellement, par analyse en composantes principales (une trentaine de lignes,
  aucune dépendance nouvelle). Un backend lexical ou fichier n'a pas
  d'embedding : la réponse est alors « rien », et le panneau dit lequel des deux
  cas il montre au lieu d'un « vecteurs absents » qui se lisait comme une panne.
  Nouveau `MemoryBackend.vectors()`, vide par défaut, implémenté pour Qdrant.

- **Le remplissage de démonstration du générateur devient opt-in.**
  `gen-site-data.py` fabriquait des traces horodatées à l'instant, un board
  garni de dix cartes du template et un nuage vectoriel tiré au sort dès que le
  projet n'avait rien à montrer. Réservé à la vitrine publique, qui n'a pas de
  runtime propre, il s'active maintenant par `--demo`
  (`GRIMOIRE_SITE_DEMO=1` pour `serve-site.sh`).

- **Les liens d'un projet non servi passent par un re-routage.** Sur l'atelier,
  qui ne sert qu'un projet et ignore `?project=`, ouvrir la mémoire d'un autre
  projet affichait celle du projet servi sous son nom.

- **Le projet non initialisé a enfin un bouton.** L'action `grimoire up`
  n'apparaissait que sur les projets *en retard* — jamais sur ceux qui n'ont
  aucune couche Grimoire, c'est-à-dire ceux qui en ont le plus besoin.

- **L'étiquette d'alignement montre la version installée.** « KIT 3.32.0 ·
  ALIGNÉ » sous un kit 3.34.2 se lisait comme un retard alors que c'était
  l'inverse.

- **Travailler dans l'atelier compte comme une activité.** Les mutations
  servies sont journalisées dans un fichier que la télémétrie du runtime ne
  référence pas : un projet qu'on était en train de manipuler affichait
  « aucune trace ».

- **Le portefeuille n'invente plus sa flotte.** `portfolio.html`, page d'accueil
  du cockpit local, portait un repli codé en dur de quatre projets — « Grimoire
  Core », « Atlas Ops », « Sentinel Sec », « Ledger Data » — affiché dès que
  `data/projects.json` manquait, c'est-à-dire précisément sur un cockpit dont le
  registre est vide. Le repli est supprimé au profit d'un état vide qui donne la
  commande d'enrôlement.

- **Les cartes du portefeuille mènent quelque part.** Sur un cockpit local,
  « Observabilité » et « Mémoire » ouvrent la page du projet
  (`?project=<slug>`) au lieu d'un panneau expliquant comment lancer l'atelier —
  comportement qui n'a de sens que sur la vitrine publique. Le chemin du projet
  sur le disque est affiché sur la carte.

- **Le cockpit n'amorce plus sa couche de données avec la démo bundlée.** Un
  registre vide donne un cockpit vide, et la commande pour le remplir. Sur un
  poste qui avait déjà lancé le cockpit, la démo semée par une version
  antérieure est **purgée** : ne plus amorcer ne suffisait pas, les projets
  inventés étaient déjà sur le disque. Le critère est l'octet près — une couche
  produite par `cockpit refresh` diffère forcément du bundle et n'est jamais
  supprimée.

- **Deux projets homonymes ne partagent plus leur cache de données.** Un projet
  hors registre n'avait que son nom de dossier pour clé : deux dépôts nommés
  `web` se marchaient dessus, et le second affichait les chiffres du premier.
  La clé porte désormais une empreinte du chemin.

- **`grimoire serve` refuse un `Host` étranger sur les lectures aussi.** Le
  garde anti-rebinding DNS ne couvrait que les mutations. Le rebinding rend la
  page attaquante same-origin, donc CORS ne protège plus la lecture des
  réponses : le nouveau `GET /api/fs/browse` serait devenu un oracle sur les
  dossiers de la machine. Le garde s'applique maintenant à toute requête.

- **La découverte est bornée à des racines permises.** Un chemin venu d'une
  requête HTTP pouvait désigner n'importe quel dossier du système — signalé par
  CodeQL comme `py/path-injection` sur les trois entrées. Le garde d'hôte
  empêche une page tierce d'appeler ces routes, mais ce n'est pas une raison de
  laisser la surface illimitée. Sont autorisés : le répertoire personnel, le
  projet servi et son voisinage, et le dossier parent de chaque projet enrôlé —
  de quoi ouvrir un dépôt hors de `$HOME` et scanner ses voisins dès le premier
  usage. Pour une racine entièrement nouvelle, on passe par
  `grimoire cockpit add`, qui n'est pas exposé au réseau. La résolution précède
  la comparaison, sinon `~/../../etc` passerait.

- **Le cockpit refuse aussi un `Host` étranger.** Il ne vérifiait que l'adresse
  du pair : sous rebinding DNS la requête arrive bien depuis la loopback, donc
  ce contrôle passe et la page attaquante est same-origin. Les routes de
  découverte ajoutées à cet hôte — dont `GET /api/fs/browse` — devenaient un
  oracle sur les dossiers de la machine. Trouvé par CodeQL ; l'atelier, lui,
  était déjà fermé, et un test verrouille désormais l'absence de divergence.

- **La profondeur de scan est plafonnée** (8) : elle vient d'une requête HTTP,
  et un scan de `/` sans limite immobilisait un thread du serveur.

- **Le cache de l'atelier sort de la racine web du cockpit.** Il vit sous
  `~/.grimoire/cockpit/atelier/<slug>/data/` et non plus sous `serve/`, que
  `grimoire cockpit serve` publie tel quel : la couche générée de chaque projet
  de la machine y aurait été exposée à `/<slug>/data/…`, et un projet dont le
  slug est `data` aurait écrit dans le dossier de données du cockpit lui-même.


## [3.35.4] - 2026-08-30

### Corrigé

- **C'est le marqueur qui dit ce que le kit a écrit, plus une liste de
  répertoires.** La version précédente restreignait la vérification à une liste
  de sous-arbres hôtes ; mais un projet peut poser sa propre compétence à côté
  de celles du kit, dans le même répertoire, et elle était alors lue comme une
  livraison. Les émetteurs hôtes marquent déjà chaque fichier qu'ils
  régénèrent : le marqueur répond à la bonne question — qui a écrit *ce*
  fichier — là où une liste répond à qui écrit d'habitude dans *ce* dossier, et
  se périme. Seuls `.github/prompts/` et `.github/instructions/`, que le
  scaffolder remplit sans marquer, restent nommés.
  Mesuré : une installation saine ne rapporte plus rien, une installation
  3.34.2 défectueuse rend toujours ses 56 chemins morts et ses neuf agents
  fantômes, et un atelier réel passe de 3 signalements à 2 — les deux résidus
  d'une installation antérieure qu'il lui restait à supprimer.

## [3.35.3] - 2026-08-30

### Corrigé

- **La vérification d'intégrité lit ce que le kit a livré, plus tout le
  projet.** Elle parcourait l'arbre entier et lisait le travail propre du
  projet comme s'il venait du kit : un audit daté qui *rapporte* un chemin
  cassé, une ligne de journal générée qui en cite un, un hook écrit à la main.
  Trois versions de suite ont retiré une de ces sources de bruit — dépôts
  imbriqués, archives, désormais artefacts du projet ; c'étaient trois
  symptômes d'un seul parcours trop large.
  Ce que le kit livre est connaissable, pas devinable : les arbres qu'il
  régénère (`_grimoire/kit/`, `_grimoire/overrides/`, `_grimoire/_memory/`, et
  les sous-arbres hôtes que ses propres conventions lui attribuent), plus les
  fichiers portant le marqueur `grimoire:managed` dans les répertoires qu'il
  partage avec le projet. `.github/hooks/` et `.github/workflows/` restent au
  projet. Mesuré sur un atelier : 21 signalements deviennent 3, tous réels,
  sans rien perdre sur une installation défectueuse (56 chemins morts
  toujours détectés sur une 3.34.2, et les neuf agents fantômes).

- **Un chemin situé dans un autre arbre n'est plus compté comme manquant
  ici.** `grimoire-kit/_grimoire/kit/x` désigne un fichier d'un dépôt voisin ;
  la recherche en attrapait la fin et le déclarait absent du projet courant.
  L'ancrage laisse évidemment passer `{project-root}/_grimoire/…`, la forme
  qu'emploient presque toutes les personas.

## [3.35.2] - 2026-08-30

### Corrigé

- **La vérification d'intégrité honore la convention d'archive.** Un agent
  retiré et parqué sous `_archived/` conservait sa carte de routage d'époque ;
  la vérification la lisait comme une carte vivante et ressuscitait les agents
  que le projet avait délibérément retirés. La convention est déjà connue du
  code de migration du kit — un fichier archivé est un fichier que le projet a
  retiré, pas un fichier que le kit a livré. Une archive est un compte rendu de
  ce qui fut vrai, pas une promesse.

## [3.35.1] - 2026-08-30

### Corrigé

- **La vérification d'intégrité ignore les dépôts imbriqués.** Les deux checks
  introduits en 3.35.0 parcouraient tout l'arbre du projet, clones vendorisés,
  sous-modules et worktrees compris : un projet hébergeant un dépôt se voyait
  reprocher les chemins de ce dépôt comme s'il les avait installés lui-même.
  Constaté immédiatement à la mise à jour d'un atelier hébergeant un clone du
  kit : 395 références mortes annoncées, dont 345 venaient du clone — non
  réparables depuis `grimoire doctor`, et noyant les 50 qui étaient vraies.
  Un répertoire portant son propre `.git` répond à son arbre, pas à celui du
  projet englobant.

## [3.35.0] - 2026-08-30

### Corrigé

- **Les chemins que le kit écrit dans un projet s'y résolvent.** Le passage aux
  trois étages (`_grimoire/kit/`, `_grimoire/overrides/`) avait migré le code et
  laissé le contenu livré derrière : une installation neuve recevait 99
  références de chemin mortes, dont 23 vers `_grimoire/_config/`, le layout
  pré-frontière que `core/layout.py` déclare dépassé. `agent-base.md` — le
  fichier que tout agent charge en premier — en portait trois, dont le
  Completion Contract, donc inatteignable pour qui suit le socle à la lettre.
  La couche mémoire avait la même dérive : huit appels cherchaient
  `maintenance.py` sous `_grimoire/_memory/` alors qu'il est déployé sous
  `_grimoire/kit/memory/`. Les dix protocoles que le socle dit de charger à la
  demande — vérification croisée, incertitude honnête, remontée des questions,
  réseau d'agents — n'étaient déployés nulle part ; ils le sont. Les journaux
  qu'un agent reçoit l'ordre de charger existent à l'installation, fût-ce
  vides : `dependency-graph.md` était cité ligne 33 de huit personas, leur
  étape d'activation, sans qu'aucun gabarit ne le livre.

- **`grimoire hooks install` génère des hooks qui s'exécutent.** Le
  `.pre-commit-config.yaml` produit pointait vers `framework/hooks/*.sh`, un
  chemin que seul un clone du dépôt du kit possède : les quatre hooks Grimoire
  échouaient au premier `pre-commit run`, dans tout projet. Les scripts sont
  désormais miroités dans `_grimoire/kit/hooks/`, où la configuration et la
  chaîne Mnemo les trouvent. Au passage, `_framework_hooks_dir()` résolvait les
  données du paquet à la main et ratait l'installation editable ; elle passe
  par `framework_path()`, qui gère les deux cas.

- **Les outils qu'une persona appelle sont livrés.** `failure-museum.py`
  existait dans le kit sans jamais sortir de la wheel, alors que le concierge
  l'invoque dans son triage. Sont désormais déployés les outils qu'un contenu
  livré appelle vraiment — cinq universels, trois de plus avec
  `creative-studio` — et inventoriés dans un `tool-manifest.csv` généré comme
  l'est déjà `agent-manifest.csv`. Les quarante que rien ne nomme restent hors
  contrat. `mem0-bridge.py`, cité quinze fois dont depuis le socle et déployé
  nulle part, cède la place au CLI `grimoire memory`, qui couvre chacune de ses
  commandes — étape 2 de la transition prévue par l'ADR-003.

- **La carte de routage du point d'entrée décrit ce projet.** Le concierge
  livrait une liste d'agents écrite en dur, héritée d'une autre famille
  d'archétypes : sur un projet d'infrastructure, neuf de ses onze agents
  n'existaient pas, et dix-sept des dix-neuf réellement installés n'y
  figuraient pas. Ce n'était pas une substitution manquée — le fichier ne
  contenait aucun placeholder, et rien ne savait produire cette carte. Elle est
  générée depuis les agents effectivement déployés. Un agent absent de la carte
  n'est pas installé, et réciproquement.

- **Les journaux de mémoire portent le nom du projet.** Deux conventions
  restaient brutes pour deux raisons distinctes : `{{project_name}}` et
  `{{init_date}}` parce que le répertoire mémoire n'était pas dans le périmètre
  de rendu et qu'`init_date` n'existait dans aucune table de variables ;
  `$project_name` parce que le journal de décisions était écrit sans passer par
  le moteur de gabarit, alors que la valeur par défaut du contexte partagé,
  huit lignes plus haut dans le même fichier, y passe. Chaque projet recevait
  un musée des échecs intitulé `{{project_name}}`.

- **La légende qui documente les placeholders ne se substitue plus
  elle-même.** Le rendu remplaçait dans tout le fichier, y compris le bloc de
  commentaire qui *explique* les marqueurs : la légende installée annonçait
  « Stack — Nom de l'agent développement (ex: Amelia) ». Les légendes se
  marquent `<!-- grimoire:legend` et traversent le rendu intactes.

- **Les modèles de run ne sont plus rangés parmi les workflows.**
  `workflow-graph.tpl.yaml` et `workflow-status.tpl.md` sont des formes à
  copier dans un répertoire d'exécution — leur en-tête le dit. Le retrait du
  suffixe `.tpl` leur ôtait ce signal et les installait à côté des vrais
  workflows, si bien que leur distribution d'illustration (`dev/Amelia`,
  `qa/Quinn`) se lisait comme la table de routage du projet. Ils vont dans
  `workflows/examples/`.

- **`standard fix --apply` n'annonce plus manquant ce qu'il vient d'écrire.**
  Le reste à faire était calculé avant les écritures et imprimé après : un
  succès se lisait comme un échec.

- **La politique de garde juge l'action, plus la donnée qu'elle transporte.**
  Les motifs destructifs étaient cherchés dans toute la chaîne de commande :
  écrire un runbook par heredoc, rédiger un message de commit citant une
  commande interdite ou créer une fixture de test suffisait à déclencher un
  refus, alors que rien n'était exécuté — c'est-à-dire précisément le travail
  attendu sur un archétype `infra-ops`. Les corps de heredoc et les chaînes
  entre quotes sortent de l'inspection, sauf quand un shell est sur le point
  de les lancer : ce qui suit `bash -c`, `eval`, `xargs`, `su -c`, `ssh` ou
  `timeout` reste examiné, sans quoi le correctif ouvrait un contournement
  d'une ligne.

- **L'archive publiée par `publish_extension` ne dépend plus de l'heure qu'il
  est.** La docstring promettait une archive déterministe et seuls les membres
  du tar étaient normalisés ; `tarfile.open(..., "w:gz")` grave l'heure
  courante dans l'en-tête gzip (champ MTIME, RFC 1952). Republier une extension
  inchangée produisait deux sommes de contrôle dès que les deux publications
  tombaient de part et d'autre d'une frontière de seconde. Le test associé ne
  pouvait donc échouer que par malchance d'horloge, et rougissait au hasard en
  intégration continue.

- **`docs/sdk-guide.md` documente l'API qui existe.** L'exemple de résolution de
  chemins montrait quatre propriétés de `PathResolver` — `grimoire_dir`,
  `config_dir`, `memory_dir`, `agents_dir` — dont aucune n'a jamais existé.

- **Le catalogue de workflows ne montrait que la moitié de ce que le kit
  installe.** `grimoire workflows list` n'indexait que `.github/prompts/` :
  sept prompts d'hygiène, qui doublent pour la plupart une commande CLI. Les
  six workflows d'orchestration multi-agents — boomerang, subagent,
  party-mode, incident-response, state-checkpoint, repo-map-generator — sont
  déposés par le scaffold dans le tier kit et n'étaient listés par aucune
  surface : installés dans chaque projet, invocables depuis nulle part. Le
  catalogue indexe désormais les deux familles, avec la nature de chaque
  workflow, les agents qu'il mobilise et sa provenance ; `--kind` filtre.
- **Les manifestes d'équipe avaient un schéma, trois fichiers et aucun
  lecteur.** `framework/teams/` décrit la chaîne vision → build → ops :
  membres et rôles, contrats d'entrée et de sortie, phases de livraison,
  outils autorisés, condition de handoff. Rien dans le SDK ne les chargeait.
  Ils sont désormais installés dans le projet, résolus par tier, affichés par
  `grimoire workflows teams`, et rendus par `workflows show` quand un workflow
  déclare `team:`.
- **`workflows show` et `workflows install` ne pouvaient pas atteindre une
  orchestration.** Les deux résolvaient en `<slug>.prompt.md`, un nom que les
  workflows d'orchestration ne portent pas. `install` résout désormais dans le
  cadre livré, sans la précédence de lecture qui lui rendait la copie du
  projet, et dépose chaque workflow là où sa nature l'exige.
- **`host sync` ne remplace plus en silence un hook écrit à la main.**
  L'émetteur Copilot posait `managed=False` sur ses fichiers de hook, ce qui
  désactivait entièrement le contrôle de préservation : le drapeau confondait
  « ne peut pas porter de marqueur de gestion » — un JSON n'a pas de
  commentaires — avec « peut être écrasé sans prévenir ». Un projet ayant sa
  propre chaîne de gouvernance la perdait au premier sync, sans message, sans
  sauvegarde, et le dry-run l'annonçait comme un `[OK]` ordinaire.
  Un fichier qui ne peut pas porter de marqueur prouve désormais son
  appartenance autrement : par la commande qu'il invoque. Le sync réécrit les
  hooks qui appellent `grimoire-hook`, préserve les autres et les signale
  `[!]`, comme il le faisait déjà pour un agent écrit à la main. La liste des
  commandes reconnues, jusque-là propre à l'émetteur Claude Code, devient
  partagée — la même question ne doit pas recevoir deux réponses.

- **La porte de preuve refuse une tâche que le board ne connaît pas.**
  `grimoire standard gate check --task-id <inconnu>` répondait `ok`. Toutes les
  exigences de `check_evidence_gates` sont indexées sur des états nommés ; une
  tâche absente du board a un état vide, donc aucune exigence n'était évaluée
  et le verdict était favorable. Un identifiant mal orthographié dans un hook
  ou un job de CI rendait la porte décorative. Deux cas voisins sont
  préservés, chacun avec son test : une tâche `proposed` réellement inscrite
  ne doit toujours aucun artefact, et un projet sans board n'est pas gouverné
  au niveau tâche.
  **Changement de comportement** : une CI qui appelle `gate check` avec un
  identifiant absent du board passait au vert, elle passe désormais au rouge.
  C'est l'objet du correctif, mais la bascule est visible sans que rien n'ait
  changé côté appelant.
- **La page publique cesse d'affirmer ce que le dépôt ne mesure pas.** La
  section « Preuves » affichait six métriques et un témoignage que rien
  n'adosse, alors que le protocole d'évals du même dépôt interdit tout claim.
  Remplacées par des mesures reprises des rapports committés et de la CI, et
  le témoignage laisse place au verdict pré-enregistré — « effet non
  démontré ». Deux cartes annonçaient encore `UDF` et `AORA`, retirés le
  2026-07-12 pour usage nul ; elles décrivent désormais des capacités qui
  existent. Deux chiffres périmés recomptés : 164 commandes CLI, 5517 tests.
- **`from grimoire.cli import *` et `from grimoire.mcp import *` ne lèvent
  plus.** Les deux paquets déclaraient un `__all__` sans importer ce qu'ils
  annonçaient. La résolution est désormais paresseuse : les noms existent sans
  que l'import du paquet charge Typer et Rich. `app` n'est volontairement pas
  réexporté — le sous-module `grimoire.cli.app` et l'instance Typer portent le
  même nom, donc la valeur dépendrait de l'ordre des imports ; l'instance
  reste atteignable par `from grimoire.cli.app import app`.
- **Le job CI « Python Unit Tests » ne pouvait pas échouer.** Son code de
  sortie était celui de `tee`, la valeur réelle partait dans une sortie que
  rien ne consommait, et l'étape suivante pouvait écrire « Some tests failed »
  pendant que le workflow restait vert. Ses tests étant couverts par le SDK CI
  et par *Framework Tools Tests*, le job est supprimé plutôt que réparé.
- **L'index lexical compagnon est créé pour tout backend vectoriel.** Il exigeait
  `retrieval_mode == "vector"` au caractère près, ce qui excluait silencieusement
  tous les autres modes déclarés — dont celui dont la fusion est l'unique raison
  d'être. Seul `none` s'en exclut désormais.
- **Un store détecté reçoit ses réglages de connexion quelle que soit la
  composition.** Un projet généré sur une machine faisant tourner Weaviate
  sortait sans `weaviate_url` dès que la composition n'était pas celle des
  graphes, et `grimoire doctor` le signalait à chaque exécution.
- **Le serveur MCP lit et écrit dans le projet visé.** `grimoire_memory_store`
  et `grimoire_memory_search` construisaient leur `MemoryManager` sans
  `project_root`, donc avec un repli sur le répertoire courant du serveur.

- **`POST /api/projects/update` traite le projet demandé, pas celui qui est
  servi.** L'atelier ignorait la cible : le portefeuille liste tous les projets
  de la machine et il est servi par les deux hôtes, donc cliquer « mettre à
  jour » sur un projet lançait `grimoire up` dans le dépôt servi — avec
  confirmation, cela écrivait dans le mauvais dépôt. Une cible inconnue est
  désormais refusée plutôt que repliée silencieusement.
### Ajouté

- **`grimoire doctor` vérifie que ce qu'il installe tient debout.** Il
  contrôlait que ses répertoires existaient, jamais que les chemins écrits dans
  les fichiers qu'il venait d'installer menaient quelque part, ni que les agents
  vers lesquels ils routent étaient installés : un projet passait 20/20 en
  portant 99 chemins morts et une carte de routage dont neuf agents sur onze
  n'existaient pas. Deux vérifications comblent l'angle mort — résolution des
  chemins d'entrée livrés, cohérence de la carte de routage avec le manifeste.
  Les deux se taisent sur un projet sans étage `_grimoire/kit/` : un arbre fait
  main ou d'avant la frontière n'a rien fait livrer par le kit, et un contrôle
  qu'on ignore est pire que pas de contrôle.
- **Deux plans cibles** : `docs/flow-engine-target-plan.md` — brancher le
  noyau d'exécution qui existe mais n'est appelé par rien — et
  `docs/product-quality-target-plan.md`, qui le situe parmi les douze
  dimensions du produit et dit où sont les points.
- **Une règle opposable et ses gardes** : ce qui décrit un artefact doit être
  dérivé de cet artefact, ou testé contre lui. Trois tests l'appliquent —
  `test_docs_derivation.py` refuse un README d'évals qui nie ou omet une
  campagne publiée, `test_cli_reference_drift.py` refuse une commande exposée
  et absente de la référence, `test_public_exports.py` refuse un `__all__` qui
  promet des objets absents. Le deuxième a trouvé neuf commandes livrées et
  documentées nulle part.
- **La mémoire se choisit comme une composition, plus comme un backend.**
  `project-context.yaml` décrivait déjà sept couches indépendantes (mémoire
  courte, sémantique, sidecar structuré, graphes de connaissances, de
  souvenirs, de code, de tâches), mais le setup ne posait qu'une question —
  quel backend ? — et n'écrivait que deux blocs codés en dur. Deux
  compositions sur toutes les possibles étaient donc atteignables, et tous les
  autres projets héritaient des mêmes défauts. Quatre profils déclaratifs
  (`lexical`, `standard`, `graphe`, `complet`) fixent les sept couches d'un
  coup ; `grimoire init --memory-profile` et le wizard les exposent. Le wizard
  ne propose que les compositions que la machine peut réellement servir et
  affiche ce qui manque aux autres. Le profil `weaviate-neo4j` des versions
  antérieures reste reconnu — il désigne `graphe`.

- Le projet servi est enrôlé au registre à l'ouverture s'il porte un marqueur
  de projet. Ouvrir un projet n'écrit rien dans son arbre : registre et couche
  de données vivent sous `~/.grimoire/`.

### Modifié

- Le registre de projets et la découverte quittent `grimoire.cli.cmd_cockpit`
  pour `grimoire.tools.project_registry` : le cockpit et l'atelier lisent et
  écrivent le même fichier, et deux copies de cette logique auraient divergé.

## [3.34.2] - 2026-08-28

### Corrigé

- **Le kit ne meurt plus sur une console qui ne parle pas UTF-8.** Il imprime
  des filets (`─`), des flèches (`→`) et des marqueurs d'état dans presque
  toutes ses sorties ; sur une console Windows par défaut — cp1252 — `print`
  levait `UnicodeEncodeError` et la commande échouait, y compris sur une simple
  lecture. Ce n'était pas un défaut de l'ère shell : **Rich échoue de la même
  façon** dès que le texte à afficher, et non sa propre bordure, porte ces
  caractères. Les trois points d'entrée (`grimoire`, `grimoire-mcp`,
  `grimoire-hook`) reconfigurent désormais la sortie en UTF-8 tolérant, et
  posent `PYTHONIOENCODING` pour les sous-processus. Un caractère non
  représentable devient `?` au lieu d'interrompre la commande.


## [3.34.1] - 2026-08-28

### Corrigé

- **Le catalogue de contenu livré ne recense plus du bytecode.**
  `gen-kit-hashes.py` parcourait le disque : un `__pycache__` laissé par une
  exécution de tests suffisait à y injecter des digests de `.pyc`, propres à une
  version de Python. 304 entrées de ce type traînaient dans le catalogue, dont
  249 depuis la 3.32.0. Le scan lit désormais `git ls-files`, comme le scan d'un
  tag lisait déjà `git ls-tree` — les deux vues répondent enfin à la même
  question — et les entrées parasites sont retirées.
- **Le job CI *Framework Tools Tests* ne se déclarait vert qu'en apparence.** Il
  neutralisait le code de sortie de pytest (`|| true`) puis devinait le résultat
  par `grep` sur la dernière ligne. Il échouait en réalité à la collecte depuis
  un moment — `typer` et `ruamel.yaml` absents de ses dépendances — et personne
  ne le voyait. Il tourne désormais sur `ubuntu-latest` et `windows-latest`, et
  c'est le code de sortie qui décide.

### Modifié

- **Les vérificateurs du standard vivent dans `grimoire.core.standard_checks`**
  (`base`, `controls`, `verifiers`), extraits de `agentic_standard.py` qui perd
  1272 lignes. Aucun changement de comportement : la surface publique du module
  est inchangée à quatre constantes près, internes au moteur de vérification.

## [3.34.0] - 2026-08-28

### Ajouté

- **Surfaces hôtes (`grimoire host`)** — un projet ne se décrivait aux hôtes
  qu'en prose : `CLAUDE.md` pointait vers `.github/copilot-instructions.md`, et
  tout ce qu'un hôte sait exécuter — sous-agents à contexte propre, compétences
  chargées à la demande, commandes utilisateur, hooks capables de refuser,
  permissions déclaratives — restait inutilisé. Le kit construit désormais une
  **description host-neutre** du projet (`grimoire.hosts.surface`) et la rend
  par un émetteur dédié : `.claude/{agents,skills,commands}` et `settings.json`
  pour Claude Code, `.github/{agents,skills,prompts,hooks}` pour Copilot, un
  catalogue explicite pour Codex, Cursor et Gemini CLI. Commandes :
  `grimoire host list | surface | sync | status | run`. La synchronisation est
  déclenchée automatiquement par `grimoire init`, `grimoire up --fix` et
  `grimoire standard init`. Voir `docs/hosts.md`.

- **Gouvernance opposable, identique sur tous les hôtes** — les règles vivent
  dans un module de décisions host-neutre (`grimoire.hosts.decisions`), traduit
  dans le JSON de chaque hôte par `grimoire.hosts.runtime`. Un refus sous
  Copilot est le même texte que sous Claude Code, parce que c'est la même
  décision. Le hook `stop` refuse la clôture d'une tâche gouvernée dont les
  gates de preuve sont rouges — la consigne « une clôture sans gates verts est
  une tâche non terminée » devient une contrainte. Trois garde-fous : pas de
  blocage répété (`stop_hook_active`), pas de blocage sur un projet non enrôlé
  ou un profil non gouverné, pas de panne fatale (un projet cassé sort en
  « autorisé » avec l'erreur en contexte).

- **`PolicyEngine` branché en production** — le moteur de politique et ses
  règles OWASP n'étaient instanciés que par les fixtures d'évals. Le hook
  `pre_tool_use` lui soumet désormais chaque appel mutant, en lisant le
  vocabulaire d'outils de n'importe quel hôte (`Bash` comme `run_in_terminal`,
  `Edit` comme `replace_string_in_file`). Suppressions récursives, force push,
  destructions d'infrastructure et lectures de fichiers de secrets sont
  refusées ou soumises à confirmation selon le profil de risque.

- **Frontière d'outils par persona** — le champ `tools:` du frontmatter d'un
  agent fixe sa frontière ; sans lui, elle est déduite du texte de la persona
  (lecture et recherche toujours, écriture et exécution sur signal explicite).
  `grimoire host status` liste les personas dont la frontière est déduite.

- **Compétences et commandes livrées** — protocole de preuve, dispatch de
  persona et mémoire projet comme compétences chargées à la demande ; six
  commandes (`grimoire-status`, `-gate`, `-proof`, `-verify`, `-recall`,
  `-doctor`) rendues comme commandes natives sur les hôtes qui en ont.

- **Outils MCP `grimoire_host_status`, `grimoire_skill`, `grimoire_command`** —
  un client MCP sans émetteur dédié atteint la même surface, compétences et
  commandes chargeables à la demande comprises.

- **Hôtes Cursor et Gemini CLI** au registre de capacités, avec leurs manifestes.

- **Frontière kit/overrides** — un projet Grimoire sépare désormais ce que le kit
  génère (`_grimoire/kit/`, régénéré à chaque mise à jour) de ce que le projet
  possède (`_grimoire/overrides/`, jamais écrasé, prioritaire à la résolution).
  Une customisation se fait en déposant un fichier dans `overrides/` — elle
  survit à toutes les mises à jour par construction, sans plus dépendre d'une
  heuristique.

- **`grimoire migrate`** — opération unique qui fait passer un projet existant
  sur cette frontière : le contenu que le kit a déjà livré (reconnu par
  empreinte dans `registry/kit-file-hashes.json`) est régénéré, tout le reste
  part dans `overrides/`. Snapshot systématique, `--restore <horodatage>` pour
  revenir en arrière, `--adopt-kit` pour reprendre la version du kit sur les
  fichiers qui la masquaient sans raison. Après migration, `grimoire up` suffit.

- **Manifeste de génération du standard** (`_grimoire/standard/.generated.json`)
  — enregistre l'empreinte de chaque artefact écrit par le kit, ce qui permet de
  distinguer « policy que le kit a générée » de « décision que le projet a
  prise ». Les premières suivent les mises à jour, les secondes ne sont jamais
  touchées.

- **`grimoire memory shared`** — mémoire transverse entre projets, pour qu'un
  agent spécialiste accumule du savoir réutilisable sans corrompre celui des
  autres. Trois règles traitent les modes de corruption connus :
  **la frontière est physique** (un store séparé, pas une collection filtrée
  par métadonnée — un filtre oublié mélange deux projets sans rien signaler),
  **la promotion est refusée par défaut** (un souvenir ne monte que s'il reste
  vrai quand on efface le nom du projet : « l'app X utilise Postgres 16 » est
  un fait de projet, « les migrations Alembic cassent quand deux heads
  coexistent » est un motif), et **la confiance décroît** (un motif non
  revérifié est servi comme hypothèse, calcul fait à la lecture — une
  décroissance qui dépend d'un ordonnanceur est une décroissance qui n'arrive
  pas). `promote` écrit avec provenance, `confirm` restaure la fraîcheur,
  `recall` restitue en deux passes étiquetées, jamais fusionnées : un motif
  appris ailleurs ne doit pas être présenté avec l'assurance d'un fait vérifié
  ici. Opt-in via `memory.shared_collection`, vide par défaut.

- **`grimoire memory up`** — met en place la stack mémoire complète, que
  `grimoire init` laissait à moitié câblée : il détecte un backend vectoriel et
  écrit `memory.backend`, mais `neo4j_uri`, `knowledge_graph`, `memory_graph`,
  `code_graph`, `task_memory` et `redis_url` restaient commentés dans
  `project-context.tpl.yaml` sans que rien ne les décommente. Trois profils
  (`lexical`, `vector`, `full`). Règle centrale : **on n'active que ce qui
  répond** — écrire `memory_graph: neo4j` alors que Neo4j est éteint produirait
  une config qui échoue en silence au runtime, donc un service injoignable est
  signalé avec sa commande de démarrage, pas activé. La comparaison porte sur ce
  qui est écrit dans le fichier et non sur les valeurs par défaut : sinon
  `neo4j_password_env` ne serait jamais écrit et rien n'indiquerait quelle
  variable exporter. Plan par défaut, écriture sur `--apply`, idempotent,
  commentaires du YAML préservés.

- **`grimoire memory bundle`** — transport d'un modèle d'embedding vers un site
  sans accès sortant. `export` construit une archive depuis un repo Hub ou un
  répertoire local, `install` refuse toute archive dont un fichier ne correspond
  pas au SHA-256 déclaré au manifeste, `verify` recontrôle les empreintes puis
  charge le modèle avec les sockets sortantes bloquées — un moteur qui retombe
  sur un téléchargement distant échoue au lieu de réussir. `install --configure`
  renseigne `memory.embedding_model` dans `project-context.yaml` en préservant
  les commentaires. Grimoire ne redistribue aucun poids : l'archive est produite
  par l'opérateur depuis la source de son choix. Voir `docs/memory-system.md`.

- **Sondes Weaviate, Neo4j et Redis dans `grimoire doctor`** — la commande ne
  vérifiait que Qdrant et Ollama, donc la stack cible du Memory OS était
  invisible du diagnostic. Les sondes ne parlent que si le projet route
  réellement la couche, pour qu'un projet en `local` ne récolte pas trois
  avertissements pour des services qu'il n'utilise pas. La sonde Neo4j signale
  le cas où la socket répond alors que la variable de mot de passe est absente :
  chaque écriture de graphe échouerait alors silencieusement à
  l'authentification.

- **Bloc `parity` dans `grimoire memory status`** — compare les entrées du store
  aux nœuds mémoire Neo4j et à leurs références `WeaviateObject`. C'est le
  signal qui détecte un objet écrit d'un côté sans contrepartie de l'autre.
  Trois `COUNT`, assez léger pour une commande de statut, là où
  `memory graph verify` reconstruit tout le code graph.

- **`grimoire cockpit prune`** — retire du registre les projets dont le chemin a
  disparu. Le registre accumulait une entrée par projet enrôlé sans jamais en
  retirer : chaque projet supprimé ou déplacé y laissait un pointeur mort, et
  rien n'offrait de les nettoyer autrement qu'un par un via `cockpit remove`.
  Sur un poste de développement, 6 327 entrées mortes sur 6 460. Prudent par
  défaut — un répertoire encore présent a pu être enrôlé délibérément, donc
  seule l'absence du chemin justifie un retrait ; `--stale` élargit aux chemins
  présents mais sans marqueur Grimoire. `--dry-run` montre le plan, la purge
  demande confirmation sauf `--yes`.

- **Prompts et resources MCP — la surface qui ne demande aucun émetteur** — le
  protocole MCP a trois primitives, le serveur du kit en exposait une : quinze
  outils, zéro prompt, zéro resource. Or MCP est la seule surface que *tous* les
  hôtes partagent. Les six commandes deviennent des **prompts MCP**, donc des
  slash commands dans n'importe quel client — Claude Code, Copilot, Cursor,
  Codex, Gemini CLI, Zed, Continue — et les trois compétences deviennent des
  **resources** chargeables à la demande sous `grimoire://skill/<slug>`.
  Codex, Cursor et Gemini CLI cessent ainsi de recevoir un catalogue en prose là
  où une commande réelle était disponible sans écrire une ligne d'émetteur.
  La façade du serveur n'a été élargie qu'après vérification **fonctionnelle**
  sur mcp 1.29.1 et mcp 2.x — enregistrement dynamique, listing, `get_prompt`,
  arguments déclarés — et non sur un simple `hasattr` : c'est la discipline qui
  avait déjà évité une borne de version erronée. L'enregistrement ne peut jamais
  empêcher le serveur de démarrer.

- **La gouvernance laisse enfin une trace** — le ledger et les hooks de cycle de
  vie avaient été conçus l'un pour l'autre sans jamais être reliés :
  `ToolCallTrace` porte un `policy_verdict_id`, et `policy_block_rate()` se
  documente comme « fraction of tool calls that were blocked » — un nombre qui
  ne pouvait que valoir zéro tant que les hooks n'écrivaient rien. Chaque appel
  d'outil réellement évalué et chaque décision de clôture sont désormais
  consignés dans `_grimoire-output/traces/`. Mesuré sur un projet gouverné :
  `policy_block_rate` passe de 0.0 structurel à 0.5 réel.
  Trois propriétés le rendent sûr à garder : le chemin en lecture seule n'écrit
  rien (il sort avant toute évaluation), les arguments sont **hachés** et jamais
  stockés tels quels — le ledger part sur disque et s'exporte en OTel —, et un
  ledger impossible à écrire ne fait pas échouer la session.

- **`grimoire task board export`** — le task board gouverné est désormais une
  projection du Mission Ledger, régénérée depuis lui (ADR-005). La carte porte
  enfin ce que le YAML ignorait : description, garde-fous, preuves attendues,
  propriétaire réel (à défaut, le porteur du claim), priorité dérivée du profil
  de risque, et un motif sur toute carte bloquée.

- **ADR-005** — « Le Mission Ledger est la source, le task board une
  projection ». Tranche la coexistence de deux modèles de tâches concurrents :
  neuf états côté ledger, huit côté board, aucune conversion nulle part.

### Modifié

- **`grimoire standard verify` ne laisse plus supprimer un artefact activé par un
  besoin.** La vérification recalculait l'ensemble requis depuis le seul profil,
  alors que `setup_standard_profile` avait déjà persisté la liste complète —
  extras compris — dans `_grimoire/standard/standard-profile.yaml`. Après
  `standard init --needs solo-prototyping`, supprimer
  `_grimoire/standard/evidence-gates.yaml` laissait `ok=true, missing=[]`. La
  liste enregistrée fait désormais foi. **Rupture assumée** : un projet dont les
  artefacts d'extras ont disparu passe de vert à rouge sans qu'une ligne de son
  code ait bougé — c'est précisément ce que le contrôle doit signaler. La remise
  en conformité se fait par `grimoire up`, qui régénère le tier kit en préservant
  waivers, scores et task board.

- **`grimoire standard gate check --strict` sort en 2 sur les cinq profils.**
  L'escalade était réservée à `governed` et `production` : un projet `starter`,
  `controlled` ou `orchestrated` ne pouvait pas casser un pipeline, quel que soit
  le nombre de preuves manquantes.

- **Les sorties JSON du standard portent un schéma versionné.** `verify`, `audit`,
  `score` et `gate check` émettent une clé `schema`
  (`grimoire.standard-<verbe>/v1`) et leur ensemble de clés de premier niveau est
  verrouillé par un test : une CI tierce qui les parse dispose enfin d'un contrat,
  là où le schéma pouvait changer en correctif.

- Les blocs `paths:` de `ci-sdk.yml` incluent `framework/agentic-standard/**` :
  une PR qui ne modifie que du YAML du standard déclenchait `agentic-standard.yml`
  et `ci-validate.yml`, mais pas les tests de `tests/test_agentic_standard.py`.

- **`grimoire up` met réellement à jour un projet existant.** Auparavant il
  s'arrêtait dès que `project-context.yaml` existait : agents, framework,
  workflows, prompts et instructions restaient gelés à la version d'installation
  pour toute la vie du projet. Il régénère maintenant le tier kit et rafraîchit
  les artefacts standard non modifiés. L'écriture est différentielle : sans
  nouvelle version, rien n'est réécrit et rien n'est rapporté.

- Les prompts, fichiers d'instructions, passerelles d'assistants et wrappers
  d'agents ne sont plus protégés par un `if fichier existe : ne rien faire` —
  ils appartiennent au kit et suivent sa version. `.mcp.json`, le contexte
  projet et les journaux mémoire restent, eux, écrits une seule fois.

- **`.github/agents/` a un seul propriétaire** — le scaffolder générait un
  wrapper à frontière d'outils fixe (`read, search` ou `read, search, execute`)
  pointant vers un chemin d'agent codé en dur, et l'émetteur Copilot réécrivait
  le même fichier avec la frontière réellement résolue et le vrai chemin de la
  persona. Deux écrivains pour un chemin : `_plan_agent_wrappers` est retiré du
  scaffolder, l'émetteur est seul propriétaire. Les garanties que les tests du
  scaffolder épinglaient (frontmatter, `user-invocable`, référence au fichier
  d'agent, fichier écrit à la main préservé) sont épinglées sur l'émetteur.
  Le wrapper pointe la définition résolue par la frontière kit/overrides :
  l'override du projet quand il existe, la version du kit sinon.

- **`grimoire memory status` ne sort plus en erreur sur un backend mort.** Un
  diagnostic qui échoue quand son sujet échoue ne sert à rien : la commande
  reporte désormais le contrat des sept couches, calculé depuis la config, plus
  la raison de l'indisponibilité. Le marqueur de santé `[OK]` / `[XX]` était par
  ailleurs invisible, Rich interprétant les crochets comme des balises.

- **`memory_link_status()` porte le contrat de couches et la parité**, donc
  l'atelier et le cockpit lisent la même source au lieu de la déduire chacun de
  son côté.

- **La page mémoire du cockpit lit l'état réel.** Elle rendait un instantané
  généré qui devinait le backend depuis la présence d'un répertoire et lisait le
  store legacy ; ses cinq pseudo-couches ne correspondaient à aucune couche du
  runtime. Elle interroge maintenant l'API locale et affiche les sept couches
  réelles avec leur état, avec repli sur l'instantané si aucune API ne répond.

- `memory up` et `memory status` vivent dans `grimoire.cli.cmd_memory_ops`,
  chaîné depuis `cmd_memory_lexical` : le ratchet R2 interdit à `cmd_memory` de
  grossir, et il rétrécit de 56 lignes.

- **fastembed remplace sentence-transformers et torch** dans les extras
  `[qdrant]` et `[weaviate]`. Mesure : la pile passe de **4,8 Go à 203 Mo** pour
  le même modèle par défaut, dont 2,7 Go de wheels `nvidia/*` et 689 Mo de
  triton qui n'avaient aucune raison d'être là — la CI les retéléchargeait à
  chaque run. Aucun re-index n'est nécessaire : les deux moteurs produisent des
  vecteurs identiques à 2e-7 près par composante (écart de cosinus 5e-13, top-1
  à top-10 inchangés sur 40 entrées et 10 requêtes), l'export ONNX de Qdrant
  étant fidèle et non quantifié. `sentence-transformers` reste utilisé à
  l'exécution s'il est déjà installé. Nouveau module
  `grimoire.memory.embedding`, partagé par les backends Qdrant et Weaviate.

- **La dimension des vecteurs n'est plus lue dans une table** — elle vient d'un
  vecteur sonde au chargement, donc elle est juste pour tout modèle, y compris
  inconnu. L'ancienne table retombait silencieusement sur 384.

- **Le backend Qdrant refuse une collection d'une autre largeur** que le modèle
  courant, au lieu d'écrire des vecteurs incohérents dans un store existant.

- Nouvelles clés `memory.embedding_model_path`, `memory.embedding_cache_dir` et
  `memory.embedding_offline`. `memory bundle verify --embed` prouve désormais le
  chargement avec le moteur réellement installé, fastembed compris.

- **`grimoire init` interroge le réseau, plus le service** — la question porte
  désormais sur l'egress, et un projet déclaré sans accès sortant est généré en
  `retrieval_mode: lexical`. Le démarrage de Qdrant via Docker reste proposé
  quand l'egress existe, mais **par défaut non** au lieu de par défaut oui.

- **Sonde `env_embedding_model`** dans `grimoire up` et `grimoire doctor` :
  signale sans réseau ni téléchargement un `embedding_model_path` cassé, un
  `embedding_offline` sans modèle local, ou un bundle installé mais non câblé.

- La conversion d'états ledger ↔ board vit dans un module unique
  (`grimoire.missions.board`), testée dans les deux sens. Un état ajouté d'un
  seul côté casse un test plutôt que de faire disparaître une carte du tableau.

### Corrigé

- La propagation d'identité vers `.github/copilot-instructions.md` écrasait le
  champ suivant lorsqu'une valeur était vide (`\s*` franchissait le saut de
  ligne). Un projet sans `user.name` y perdait son réglage de langue.

- Le kit copiait ses propres `__pycache__` dans les projets.

- **Détection d'hôte** — `HostBridge.detect()` identifiait Claude Code sur la
  présence d'`ANTHROPIC_API_KEY` et Codex sur `OPENAI_API_KEY`. Une clé
  d'API dit qui paie les jetons, pas quel hôte s'exécute : toute session
  exportant les deux était mal routée. La détection repose désormais sur des
  marqueurs de processus (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_ENV`…).

- **`CLAUDE_CODE_CLI_MANIFEST`** déclarait `user_prompt_submit: False`. Claude
  Code expose bien cet événement ; le manifeste en excluait le seul hook capable
  d'enrichir un prompt avant que le modèle ne le lise.

- **Fenêtres de contexte des variantes longues** — `resolve_window` résolvait
  `claude-opus-5[1m]` vers la fenêtre standard de sa famille, sous-évaluant le
  budget d'un facteur cinq. Un marqueur explicite (`[1m]`, `-1m`, `:1m`) est
  désormais lu avant la famille.

- **Fusion JSON des émetteurs** — une variable de boucle réutilisée faisait
  passer le texte du fichier précédent à la fonction de fusion quand le fichier
  cible n'existait pas encore.

- **Suite de tests rouge sans l'extra `mcp`** — `tests/unit/mcp/test_server.py`
  importait `grimoire.mcp.server` au niveau module : sans `grimoire-kit[mcp]`
  installé, pytest remontait une *erreur de collecte* et toute la suite passait
  au rouge. Un `pytest.importorskip` énonce le même fait sans en faire un échec.
  C'est ce qui rendait le hook pre-commit systématiquement rouge en local, et
  donc `--no-verify` systématique.

- **`grimoire up` n'écrase plus les instructions du projet.** En 3.33.0,
  `.github/copilot-instructions.md` et les passerelles `CLAUDE.md` /
  `AGENTS.md` / `GEMINI.md` / `.cursorrules` étaient régénérés à chaque mise à
  jour. Sur un projet réel, le fichier d'instructions est passé de 227 à 112
  lignes, perdant la doctrine qui gouvernait le dépôt. Ces fichiers sont
  désormais semés une fois puis laissés au projet — contrepartie assumée : la
  table des agents installés s'y périme.

- **`grimoire migrate --adopt-kit` ne supprime plus les fichiers archivés par
  le projet.** Le masquage était détecté par nom de fichier, si bien qu'un
  `agents/_archived/concierge.md` passait pour un doublon de l'agent
  `concierge` livré par le kit ; l'adoption le supprimait sans que rien ne le
  régénère. La détection compare désormais le chemin complet.

- **La propagation d'identité n'efface plus un champ que la config ne déclare
  pas.** `project-context.yaml` n'impose pas de section `user:` ; en son
  absence, `grimoire up` vidait `**User**` dans le fichier d'instructions.

- **Le hook de cycle de vie coûtait 391 ms par appel d'outil** — et il était
  câblé sur `Read`, donc sur chaque lecture de fichier de chaque session.
  Trois causes, toutes mesurées :
  **le point d'entrée** (`grimoire host hook` construisait l'arbre Typer complet,
  chaque module `cmd_*` importé pour résoudre une sous-commande) — un script
  console dédié `grimoire-hook` le remplace dans les configurations générées,
  391 ms → 102 ms ;
  **les ré-exports impatients** (`grimoire/__init__.py` et
  `grimoire/core/__init__.py` importaient onze modules dont le scaffolder et le
  résolveur d'archétypes) — résolution paresseuse PEP 562, API inchangée ;
  **le moteur du standard importé pour rien** (`check_evidence_gates` au niveau
  module alors que la décision d'outil ne l'appelle jamais) — import différé, et
  les chemins de sortie du standard déménagent dans le module léger
  `standard_manifest`.
  Enfin `Read` sort du matcher sur les hôtes qui ont une table de permissions :
  les mêmes fichiers y sont déjà refusés déclarativement, à coût nul. L'accès par
  commande shell reste couvert, `Bash` restant dans le matcher.
  Deux tests épinglent le résultat, dont un qui échoue si le chemin des hooks
  réimporte le moteur au chargement.

- **Motifs de secrets et règles déclaratives avaient dérivé** — la détection
  couvrait neuf familles de fichiers de credentials, la table `deny` six. Trois
  familles (`.npmrc`, `credentials.json`, `service-account*.json` entre autres)
  n'étaient donc pas protégées du côté qui ne coûte rien. Les deux formes sont
  désormais déclarées ensemble dans `grimoire.hosts.secrets`, et un test refuse
  qu'une famille existe sans ses deux expressions.

### Sécurité

- Alerte CodeQL `py/command-line-injection` du dispatch cockpit classée après
  correction du risque réel (injection d'argument) : les valeurs issues d'une
  requête passent après `--` et refusent le préfixe `-`.

### Supprimé

- **Références déclaratives qui ne résolvaient nulle part.** `_verify_pattern_catalog`
  exigeait la *présence* des clés `check_refs`, `rule_refs` et `check_id`, jamais leur
  résolution : le catalogue promettait des contrôles que le moteur n'émet pas, et
  `docs/governed-controls.md` publiait ces promesses. Elles sont retirées plutôt
  qu'implémentées — on retire des promesses, pas des contrôles : `standard verify` sur un
  projet neuf produit exactement les mêmes identifiants de checks qu'avant, vérifié sur
  les cinq profils. Un test d'intégrité référentielle interdit désormais toute
  déclaration sans exécutant.
  - 17 `check_refs` sans check émis : `events.invalid_line`, `hooks.destructive_bypass`, `hooks.gateway_missing`, `knowledge.source_unindexed`, `ledger.mission_unlinked`, `memory.graph_projection_unverified`, `memory.hot_memory_partial`, `observability.cockpit_mutation`, `observability.input_undeclared`, `observability.secret_export`, `orchestration.handoff_unverified`, `orchestration.role_undeclared`, `provider.cost_unbudgeted`, `provider.slo_undeclared`, `skills.classification_missing`, `tools.threat_unmapped`, `tools.unmediated_call`.
  - 21 `rule_refs` sans règle correspondante dans `rule-packs.yaml` : `context.compression-preserves-provenance`, `decision.council-before-irreversible`, `governance.cluster-action-dry-run`, `governance.env-policy-declared`, `guardrail.versioned-four-faces`, `knowledge.doc-to-graph-sourced`, `memory.integrity-validated`, `merge.fault-classified-before-retry`, `observability.prompt-version-tracked`, `orchestration.flow-manifest-exportable`, `orchestration.workflow-state-declared`, `privilege.controller-agent-separated`, `prompt.external-content-isolated`, `provider.cost-and-slo-declared`, `quality.browser-evidence-required`, `quality.visual-evidence-required`, `remote.freshness-verified`, `runtime.k8s-agent-declared`, `runtime.provider-contract-uniform`, `security.workspace-isolated`, `tools.blast-radius-bounded`.
  - 13 `check_id` de règles pointant vers un check inexistant : `evidence.minimum_missing`, `hooks.destructive_bypass`, `hooks.gateway_missing`, `knowledge.source_unindexed`, `memory.freshness_missing`, `memory.hot_memory_partial`, `observability.cockpit_mutation`, `observability.input_undeclared`, `observability.secret_export`, `orchestration.handoff_unverified`, `provider.cost_unbudgeted`, `skills.classification_missing`, `tools.unmediated_call`.

- `check_id` n'est plus une clé obligatoire d'une règle de `rule-packs.yaml`. Une règle
  sans check déclare honnêtement qu'aucun contrôle ne l'applique ; celles qui en
  déclarent un doivent maintenant qu'il existe.

## [3.32.0] - 2026-08-18

### Ajouté

- **Workflow `party-mode`** — 25 agents livrés exposaient un menu `[PM] Party Mode`
  pointant vers `_grimoire/core/workflows/party-mode/workflow.md`, un chemin que
  le kit ne fournissait nulle part et qu'aucun installeur ne crée : la capacité
  était déclarée dans `framework/agent-base.md`, générée dans chaque nouvel agent
  par `agent-forge.py` et citée par la taxonomie, sans implémentation. Le playbook
  existe désormais (`framework/workflows/party-mode.md`) et toutes les références
  pointent vers son emplacement réel, `_grimoire/_config/custom/workflows/party-mode.md`.
  Panel de 3 à 5 agents, premier tour sans lecture croisée pour éviter la
  convergence, second tour limité aux désaccords, aucun vote, arbitrage rendu à
  l'utilisateur et tracé dans `decisions-log.md`.
- **Bornes du gauntlet** (archétype `fix-loop` 1.1.0, workflow closed-loop-fix
  v2.7) — CHALLENGER + GATEKEEPER restent opt-in, mais gagnent trois règles qui
  décident quand ils tournent et quand ils s'arrêtent. **Déclencheurs**
  (Phase 1.5) : cinq signaux objectifs escaladent la sévérité et rallument le
  passage adversarial sur un cycle classé trop bas — `T1-repeat` (2e tentative
  sur le même symptôme), `T2-security`, `T3-prod`, `T4-surface` (≥ 3 composants
  impactés), `T5-data` (écriture non réversible) ; la sévérité ne redescend
  jamais en cours de cycle. **Gate oracle** (Phase 2.4bis) : le gauntlet exige
  une commande avec `exit_code` attendu qui échoue avant le fix et passe après,
  les deux exécutions capturées ; sans oracle les deux phases sont désactivées,
  le rapport est marqué « appliqué, non certifié » et aucun pattern n'est écrit.
  **Arrêt sur boucle stérile** (Phase 4.6) : deux itérations dont la signature
  d'échec (commande + `exit_code` + 1re ligne `stderr`) est identique escaladent
  à l'humain sans consommer `max_iterations`. Nouveaux champs FER (v3.1) :
  `severity_escalated_from`, `gauntlet_trigger`, `oracle_available`,
  `failure_signatures[]`. La question 7 de la META-REVIEW mesure si un
  déclencheur a servi ou coûté pour rien, de quoi resserrer les seuils sur
  données réelles plutôt qu'à l'intuition.

### Corrigé

- **Les placeholders `{{…}}` n'étaient jamais résolus** — les agents et workflows
  arrivaient dans le projet avec leurs marqueurs bruts là où les noms d'agents de
  l'utilisateur devaient apparaître (25 dans le seul `closed-loop-fix`). Une passe
  de rendu résout à l'installation ce qui est connaissable : rôles de délégation
  (`{{ops_agent_name}}`, `{{debug_agent}}`, …) résolus depuis les agents réellement
  installés — un rôle sans agent rend « aucun » et laisse le workflow en mode SOLO,
  son défaut documenté —, plus `{{tech_stack_list}}`, `{{user_name}}`, `{{project_name}}`.
  La substitution est **opt-in par clé**, jamais un balayage : les trois autres
  familles de placeholders du kit survivent intactes — les slots runtime que le LLM
  remplit à chaque exécution (`{{current_step}}`, `{{progress_bar}}`), l'infra que
  le kit ne peut pas deviner (`{{lxc_id}}`, `{{host_ip}}`), et l'agent vierge de
  l'archétype `minimal`.

- **Les workflows d'archétype n'étaient jamais installés** — `grimoire init`
  (chemin recommandé) et `grimoire-init.sh --archetype` copiaient les agents
  d'un archétype, sa DNA et son `shared-context`, mais pas son dossier
  `workflows/`. Seul `framework/workflows/` atterrissait dans le projet. Le
  workflow `closed-loop-fix` de `fix-loop` — le seul workflow porté par un
  archétype — n'existait donc dans aucun projet initialisé, et le menu `[FX]`
  de son agent pointait vers `_grimoire/bmb/workflows/fix-loop/…`, un chemin
  qu'aucun installeur ne crée. La cible est désormais
  `_grimoire/_config/custom/workflows/`, là où vivent déjà les workflows du
  framework. Corrigé sur le SDK et sur `grimoire-init.sh install --archetype`.
  L'init complet de `grimoire-init.sh` n'est pas touché : la correction y ferait
  grossir un entrypoint gelé, ce que `framework/FREEZE.md` désigne comme le
  signal de porter la capacité sous `src/` — `grimoire init` est le chemin
  recommandé et il est correct.
- **Le suffixe `.tpl` fuitait dans les projets** — `fix-loop-orchestrator.tpl.md`
  et `workflow-closed-loop-fix.tpl.md` étaient copiés tels quels. `.tpl` marque
  une source du kit ; il est retiré à l'installation, ce qui aligne le nom
  installé sur celui que la documentation et les références utilisaient déjà.
- Test de contrat associé : toute cible `exec=` d'un agent qui désigne un
  workflow livré par le kit doit correspondre à un fichier réellement installé.

## [3.31.0] - 2026-08-15

Aucune entrée n'a été consignée pour cette publication. Le détail est dans
l'historique git (`git log v3.30.0..v3.31.0`) et dans les notes de release
GitHub ; il n'est pas reconstitué ici, pour ne pas attribuer après coup des
changements à une version au jugé.

## [3.30.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.29.0..v3.30.0`.

## [3.29.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.28.0..v3.29.0`.

## [3.28.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.27.0..v3.28.0`.

## [3.27.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.26.1..v3.27.0`.

## [3.26.1] - 2026-08-15

Aucune entrée consignée — voir `git log v3.26.0..v3.26.1`.

## [3.26.0] - 2026-08-14

### Ajouté

- **Verdict de sécurité — surface d'attaque agrégée** (blueprint, P2.4) — la
  couche guardrails (`Gate(guardrail, in|out)`, `Gate(mcp-trust)`) était déjà
  déclarable et lintée (R-G1/R-G2/R-G5) ; on ajoute la **vue de synthèse** :
  `security_verdict` agrège points d'entrée (sources externes + couverture
  mcp-trust/guardrail d'entrée), points de sortie (couverture guardrail de
  sortie), points de filtrage déclarés et expositions résiduelles, avec un
  verdict global `secure|exposed`. `blueprint_lint` renvoie un champ `security`
  additif et la compilation émet une section « Surface d'attaque (sécurité) ».
  Cohérent par construction avec `gate_lint` (mêmes helpers) — le flow où un
  node externe alimente une sortie sans guardrail refuse toujours de compiler
  (R-G2). Nouveau module `grimoire.tools.blueprint_security`.

- **Évals comportementaux first-class** (blueprint, P1.2) — une suite d'évals
  versionnée s'attache à un node (`config.evals`) ou au blueprint entier
  (`evals` top-level) : cas d'entrée + assertions typées (`contract`, `cost`,
  `no-refusal`, `verdict`, `path-taken`). Le lint valide la forme (R-E1 :
  versionnée) et **recoupe `path-taken` avec le plan de défaillance déclaré**
  (R-E3, jonction P3.1) ; un node externe sans preuve est signalé (R-E2). Le
  panneau santé expose un **taux de réussite d'éval par node** (`blueprint_lint`
  renvoie un champ `evals` additif), et la compilation émet une section « Évals
  (preuve comportementale) » — checks exécutés par l'hôte (`agent-test`), jamais
  par le Studio. Nouveau module `grimoire.tools.blueprint_evals` ; `$def`
  `evalSuite` au schéma.

- **Injection d'échec en simulation** (blueprint, P3.1) — le what-if de
  résilience : `blueprint_simulate` accepte une cible `{nodeId, class}` (ou
  `GET/POST …/simulate?injectNode=&injectClass=`) et trace le plan de
  défaillance réellement suivi — retry borné → fallback (edge `failure`) →
  escalade (edge `escalation`) → terminaison `onExhaustion` —, avec le `path`
  des nodes traversés (assertion `path-taken`). La simulation nominale reste le
  plan happy (`failureInjection: null`). Déterministe ; l'hôte reste
  l'exécutant.

### Corrigé

- **`grimoire update` échouait sur les installations `uv tool`** — la commande
  ne connaissait que `pipx` et `pip`, alors qu'un environnement `uv tool`
  (voie d'installation recommandée) n'expose pas `pip` : le repli
  `python -m pip install --upgrade` échouait. La détection de méthode teste
  désormais `uv tool` en premier, puis `pipx`, puis `pip`, et affiche la
  commande manuelle correspondante en cas d'échec.
- **`grimoire update` pouvait rester bloqué sur une version périmée** — la
  version cible venait de `info.version`, qui accuse un retard de propagation
  CDN de quelques minutes après une publication (« already up to date » à tort,
  ou montée vers une version périmée). La résolution prend maintenant le max
  des clés `releases` (hors versions retirées et pré-versions) et compare en
  sémantique stricte. La logique de mise à jour est extraite dans
  `grimoire/cli/updater.py`.

## [3.25.0] - 2026-07-23

### Ajouté

- **Famille résilience** (blueprint, P2.2) — comment un flow échoue, en format :
  politique node-local `config.resilience` (retry **borné** `max` 1-10 +
  `backoffMs`/`strategy`, `timeoutMs`, `onExhaustion`) et les quatre motifs
  (retry, fallback, compensation, dead-letter/escalade) exprimés via les edges
  `failure`/`escalation` de P0.2 portant le contrat `error-envelope`. Lint
  opposable : **R-F1** (retry sans `max` refuse de compiler), **R-F2** (edge de
  défaillance dont le contrat n'est pas `error-envelope`), **R-F4** (escalation
  non terminale) — bloquants ; **R-F3** (node externe sans chemin de
  défaillance ni résilience) — avertissement. La compilation émet une section
  `on_failure` par node résilient ; l'hôte reste l'exécutant. `$def
  resiliencePolicy` documenté au schéma.

### Corrigé

- **Robustesse des payloads de setup et du cadrage** : `POST /api/setup` avec
  `needs: null` ne plante plus (`TypeError`), et `extensions` en chaîne n'est
  plus itéré caractère par caractère (un `"demo"` installait `d/e/m/o`) ;
  `name`/`user` `null` ne produisent plus `--name "None"`. Les suggestions de
  needs tolèrent un catalogue `needs: null` ou des entrées non-dict.
  `grimoire cadrage status`/`check` ne plantent plus sur un fichier de phase
  non-UTF8 (octets invalides remplacés).

### Ajouté

- **`grimoire cadrage`** (B4) — comprendre avant de construire : un flux guidé
  en cinq phases (brief → brainstorm → compréhension → exigences → cahier des
  charges) matérialisé en artefacts gouvernés sous `_grimoire/cadrage/`.
  Discipline embarquée : brainstorm qui note ce qu'il écarte, faits séparés
  des hypothèses, exigences MoSCoW avec critères d'acceptation, périmètre ET
  hors-périmètre. `cadrage status` mesure la progression ; `cadrage check` est
  un **gate de complétude** (exigences + CDC exigés — on ne construit pas sur
  un engagement flou). Nouveau need `project-discovery` au catalogue.

- **Suggestions de needs pilotées par le projet** (B3) : `grimoire up` sans
  `--needs` analyse le projet réel (docs, CI + conteneurs, hooks/skills,
  configs MCP, agents déclarés, multi-stack) et suggère les needs du catalogue
  qui collent — avec la raison et la preuve de chaque suggestion, et la
  commande d'install sur mesure prête à copier. Best-effort : le défaut
  `starter` s'applique toujours, la suggestion n'interrompt jamais l'install
  (`grimoire.core.needs_suggest`).

- **Lien projet ↔ base mémoire visible et piloté** (B1) : nouveau
  `GET /api/memory/status` (backend configuré, backend résolu, disponibilité,
  volumétrie — best-effort, ne casse jamais si un serveur est éteint) et
  `GET /api/backends` (catalogue des backends avec descriptions humaines).
  Le hub de l'atelier affiche l'état du lien mémoire du projet.
- **Wizard de setup modernisé** (B2) : une étape « Mémoire / BDD » (choix du
  backend, validé côté serveur) et le plan compile désormais vers
  **`grimoire up`** — plus jamais vers l'installeur shell legacy. Logique
  extraite dans `grimoire.tools.project_setup`, testée.
- **Gate universel paramétré** (blueprint, P2.1) : une primitive, six modes —
  `human` (HITL riche : approve/edit/input/sample/escalate-on-uncertainty),
  `budget`, `evidence`, `output-contract`, `guardrail`, `mcp-trust` — déclarés
  en `config.gate {mode, onReject, params}`. Un seul compilateur (switch
  unique) ; le rejet réutilise les edges typés de P0.2 (`escalation`,
  `failure`, ou arrêt dur). Frontière de confiance opposable : **R-G1**
  (bloquant — node externe sans `Gate(mcp-trust)` en amont) et **R-G2**
  (bloquant — contenu externe atteignant une sortie sans `Gate(guardrail, in)`),
  plus R-G3 (block sans alternative), R-G4 (schéma non résolu), R-G5
  (sortie sans guardrail out). `$def gatePolicy` documenté au schéma.

### Modifié

- **Compilation reproductible** (blueprint, P0.1) : plus aucun horodatage dans
  le contenu compilé — `generatedAt` sort du mission pack, la date de
  compilation vit dans les métadonnées (`compiled.at`). Même blueprint + même
  catalogue ⇒ même contenu ⇒ même hash (preuve : test de double compilation).

## [3.24.0] - 2026-07-22

### Ajouté

- **`grimoire context-pack`** : matérialise un context-pack durable de repo,
  conforme au contrat `context-pack` du catalogue (sources incluses/exclues avec
  statut et confiance, scorecard de suffisance, expiry avec invalidation sur
  changement de HEAD), sous l'ordre d'autorité ORC-06. Capacité produit rapatriée
  depuis un hook d'atelier vers `grimoire.tools.context_pack` — testée et
  couverte par la CI.
- **`grimoire.tools.handoff`** : dérive de façon déterministe un `handoff-packet`
  conforme au contrat catalogue (ORC-03) depuis une capsule de SubagentStop —
  champs dérivables (`task_id`, `summary`, `evidence`, `next_trigger`, statut)
  remplis, champs d'analyse (`changes`, `assumptions`, `risks`,
  `memory_candidates`) marqués « à enrichir » plutôt qu'inventés. Capacité
  produit rapatriée d'un hook d'atelier, testée.
- **Régions d'isolation** (blueprint, C3) : un tableau `boundaries` déclare des
  régions `{id, mode: isolation, members}` — plusieurs nodes partageant une
  fenêtre quarantinée (patron orchestrateur-worker), le cas multi-nodes de
  l'isolation de node C1. La compilation émet **un seul dispatch quarantiné par
  région** (preuve : une région multi-nodes → un dispatch), la simulation
  expose la pression agrégée par région, et le lint **R-C7** refuse qu'une
  région exporte un contrat non-digest (quarantaine : seul un digest sort).
  Additif — sans `boundaries`, comportement inchangé.
- **Classe sémantique de node `role`** (blueprint, P0.3) : algèbre de 7
  primitives orthogonales — `Unit` (la seule « qui fait »), `Route`, `Scatter`,
  `Gather`, `Gate`, `Boundary`, `Reference`. `role` est orthogonal à `kind`
  (d'où vient le node vs ce qu'il fait), additif et optionnel. Les ~20 cases de
  la palette XXL deviennent des **paramètres** de ces 7 primitives (source de
  vérité `grimoire.tools.blueprint_primitives`, exposée par
  `GET /api/primitives`) : plus de bestiaire de `kind`, un tableau de
  configurations éprouvées. Validation du `role` à la sauvegarde.
- **Typage d'edge `channel`** (blueprint, P0.2) : chaque edge porte un canal
  `happy` (défaut) `| failure | escalation`. Additif et rétro-compatible —
  l'absence vaut `happy`, les blueprints existants migrent sans perte. La
  simulation ne suit que le canal nominal pour l'ordre et la pression de
  contexte (les chemins d'échec/escalade sont des routes alternatives), expose
  la répartition `channels`, et l'éditeur distingue visuellement ces chemins.
  Débloque la famille résilience (edges `failure`) sans nouveau `kind`.
- **Modèle de coût calibré** (ingénierie de contexte, tranche C2) : la table de
  coût par pattern, jusqu'ici en dur dans `web/bp2-cost.js`, devient une source
  de vérité serveur (`grimoire.tools.cost_model`) exposée par
  `GET /api/cost-model`. La simulation de pression de contexte calibre le coût
  d'entrée de chaque node sur son pattern (au lieu d'un forfait plat), la vue
  COÛT du Studio bascule sur « calibrée » quand le serveur répond (repli
  statique sinon), et l'assertion d'éval `cost-under` (`estimate_usd` /
  `cost_under`) se vérifie contre les mêmes taux — une seule source pour design,
  gate et éval.
- **`grimoire hooks`** (install/list/status) : port Python de
  `grimoire-init.sh hooks` — première étape du plan de résorption bash
  (`docs/resorption-bash.md`). Résolution correcte dans les worktrees git
  (`git rev-parse --git-path hooks`), sources depuis le checkout du kit ou
  les données embarquées du wheel, préservation des hooks tiers, sortie
  `-o json`.
- **Plan de résorption bash** : inventaire complet des 28 sous-commandes de
  `grimoire-init.sh` (couvert / wrapper mince / gap) et séquence de port
  dans `docs/resorption-bash.md`.
- **Backend mémoire `lexical`** : implémentation SQLite FTS5 avec classement
  BM25 et matching insensible aux diacritiques (`unicode61 remove_diacritics 2`),
  zéro dépendance externe. Honore le contrat `backend: lexical` /
  `retrieval_mode: lexical` déjà déclaré dans le schéma de configuration mais
  jamais implémenté. Migration automatique du store JSON local historique
  (IDs et timestamps préservés).
- **Backend mémoire `tantivy-local`** (extra `search`) : moteur full-text
  embarqué Tantivy (Rust, classe Lucene) avec BM25 et stemming français +
  anglais — `harmonisé` matche `harmonisation`. Prévu pour les corpus
  volumineux (code, docs). Installation : `pip install grimoire-kit[search]`.
- **Retrieval hybride** : module `grimoire.memory.retrieval` avec fusion
  reciprocal rank fusion (`rrf_fuse`) et `HybridRetriever` multi-backends
  tolérant aux pannes. `MemoryManager.hybrid_search()` fusionne le classement
  vectoriel et un index compagnon lexical FTS5, mirroré automatiquement à
  chaque écriture ; `reindex_lexical_companion()` pour le backfill.
- **Surface CLI retrieval** : `grimoire memory search --hybrid` (fusion RRF)
  et `grimoire memory reindex-lexical` (backfill du compagnon).
- **Projection docs** : `grimoire memory vector sync-docs` indexe les pages
  markdown (`docs/`, `README.md` par défaut) dans le backend mémoire actif —
  scope `docs` interrogeable via la recherche BM25/hybride. Le scope `code`
  est couvert par la projection backend-agnostique existante
  (`memory vector sync-code`), compatible avec les nouveaux backends.
- **Evals retrieval** : gold set recall@k
  (`tests/unit/memory/test_retrieval_quality.py`) gardant l'échelle de
  qualité — lexical jamais sous local, stemming tantivy complet sur les
  requêtes morphologiques françaises, fusion RRF récupérant les deux
  classements.
- **Tantivy insensible aux diacritiques** : champ `text_folded` (NFD) — les
  requêtes accentuées et non accentuées matchent dans les deux sens.

### Corrigé

- **`framework/hooks/pre-commit-cc.sh`** : le venv du projet est préfixé au
  PATH — le Completion Contract utilise le pytest/ruff/mypy du projet au
  lieu de l'outillage système.
- **`framework/hooks/pre-push.sh`** : étape quickcheck avec résolution de
  layout correcte (kit direct `framework/tools/` ou kit nested
  `grimoire-kit/framework/tools/`) — l'ancien hook installé cherchait un
  chemin valable uniquement depuis un projet hôte.

### Modifié

- **Résolution `backend: auto`** : sans serveur vectoriel configuré, le défaut
  local devient `lexical` (FTS5 BM25) quand SQLite le supporte, avec repli sur
  le backend JSON `local` sinon. `retrieval_mode: lexical` ou
  `vector_database: false` forcent désormais le backend lexical même si une
  URL serveur est présente.

## [3.23.0] - 2026-07-08

### Ajouté

- **`grimoire serve`** : commande de premier niveau lançant l'atelier local
  (UI Forge + API blueprints) sur `127.0.0.1`. Remplace l'ancien
  `python -m grimoire.tools.forge_server`, qui reste disponible pour l'usage
  avancé (`--ui-dir`, `--kit-root`).
- **Rework Vitrine/Atelier** : le site v2 est branché de bout en bout sur le
  réel — catalogue normatif (78 patterns), marketplace, éditeur de blueprints
  (Studio), wizard de setup, observatoire et mémoire lisent l'API locale et
  les données générées. Plus aucune donnée de démo dans le mode atelier.
- **`grimoire stigmergy`** *(canal beta)* : coordination indirecte par
  phéromones — `emit/sense/amplify/resolve/trails/evaporate/stats`, plus
  `install-hooks`/`uninstall-hooks` pour câbler l'émission et la captation
  automatiques via des hooks **non bloquants** (SessionStart, PostToolUse,
  Stop). Vue live dans l'observatoire.
- **`grimoire features`** : canaux de maturité stable / beta / experimental,
  activables par projet (`_grimoire/features.json`), avec page **Labs** dans
  l'atelier et journalisation des usages pour la promotion sur métriques.
- **Packaging** : le wheel embarque désormais `extensions/` et `version.txt`
  — un `pip install` dispose d'un marketplace réel et de la bonne version.

### Corrigé

- **Robustesse & concurrence** (audit du kit contre son propre catalogue) :
  écritures atomiques (board, features, journal), verrou inter-process contre
  les pertes de mise à jour des hooks concurrents, cap par zone anti
  signal-storm, journal stigmergique borné et versionné.
- **Sécurité du serveur local** : garde CSRF / DNS-rebinding sur les mutations
  (refus des Host non-loopback et Origin cross-origin), correctif de préfixe
  dans le service statique, télémétrie gouvernée des mutations.
- **`install_hooks`** : rollback transactionnel sur échec partiel.
- **`quick-check.sh`** : bit exécutable rétabli (déblocage des pre-push
  consommateurs).

## [3.22.0] - 2026-07-03

### Ajouté

- **UI embarquée** : les pages marketplace, blueprint et setup rejoignent le
  wheel (`grimoire/data/web`) — `grimoire serve` sans `--ui-dir` sert
  l'expérience complète après un simple `pip install grimoire-kit` (#57).
- **UX v2 de l'éditeur blueprint** : drag de connexion Maj+glisser avec
  contrats vérifiés au drop, palette latérale cliquable (recherche, groupes),
  panneau propriétés du node (label, contrats de pins), undo Ctrl+Z,
  layout automatique, aide contextuelle (#59).
- **Extension fennara-godot** : premier `mcp-toolbox` du marketplace
  (QUA-12, QUA-04, RUN-08) (#50).
- **Campagne evals web-app-todo** : cadrage du témoin, baseline, grille de
  jugement pré-enregistrée, mécanique de run standard-null hors bras
  governed (#53, #54, #55, #56).

### Corrigé

- Test `test_baseline_record_on_bare_project` aligné sur le protocole
  standard-null (#58).

## [3.21.1] - 2026-07-03

### Corrigé (issue #39 — suite)

- **Sanitisation MCP durcie** (C8) : les entrées sont normalisées avant scan
  (percent-decoding, caractères zero-width) — `%2e%2e%2f` et les mots-clés
  d'injection obfusqués ne contournent plus le filtre ; la traversée de chemin
  est détectée dès 2 segments même non consécutifs (`../a/../b`), le `../`
  isolé restant permis (chemins relatifs légitimes). Patterns d'injection
  élargis (disregard/forget/prior/earlier, marqueurs `<|im_start|>`),
  explicitement documentés comme heuristiques. 7 tests.
- **Commentaires « Silent exception » obsolètes retirés** (C5) : 100
  occurrences dans 42 outils pointaient « add logging » au-dessus de lignes
  qui loggent déjà — le cœur de C5 (chemins de routage) avait été traité
  par #41 avec des warnings contextualisés.


## [3.21.0] - 2026-07-03

### Ajouté

- **Extensions** : `grimoire ext add|list|remove|verify|publish` — bundles
  d'artefacts gouvernés décrits par `extension.json` (schéma versionné),
  installation locale ou depuis le registry dédié
  [grimoire-extensions-registry](https://github.com/Guilhem-Bonnet/grimoire-extensions-registry)
  avec checksum sha256 vérifié et extraction sûre. Six extensions publiées :
  crewai, langfuse, langgraph, autogen, browser-use, haystack — chacune
  ancrée sur le catalogue de patterns agentiques (`patterns.implements`
  obligatoire, hooks toujours en mode shadow).
- **`grimoire serve`** : mode local UI + API (127.0.0.1) — wizard de setup
  par archetypes, vue des artefacts gouvernés, gestion d'extensions, CRUD et
  validation de blueprints, stream SSE des events.jsonl.
- **Blueprints** : format `.blueprint.json` avec pins typés bloquants (une
  connexion sans contrat commun ne compile pas), lint normatif dérivé du
  catalogue (dépendances de patterns, heuristique Faux Done, nodes isolés)
  et replay de télémétrie via bindings.
- **Écriture mémoire typée dans le SDK** (`grimoire memory remember` /
  `recall`) — parité complète avec le protocole agent legacy : 5 types
  (shared-context, decisions, agent-learnings, failures, stories),
  déduplication UUID5 identique à mem0-bridge
  (`uuid5(DNS, "grimoire-{proj}:{agent}:{text[:150]}")`), upsert idempotent
  avec fallback anti-doublon pour les backends sans `upsert`. 23 tests.

- **Simulation pré-exécution des blueprints** et **publication de blueprints
  au marketplace** (workflow extensions).

### Corrigé (issue #39 — merci @zavrocKk)

- **Routage LLM réparé** (C1/C2) : l'agent-caller appelait le routeur avec un
  kwarg inexistant (`TypeError` avalé silencieusement → toujours le modèle par
  défaut) et `_resolve_model` de l'agent-worker retournait un objet
  `TaskClassification` au lieu d'un id de modèle.
- **SSRF avec résolution DNS** (C4) : les 4 outils fetch (web-browser,
  docs-fetcher, doc-fetcher, rag-indexer) filtraient par préfixe de chaîne
  sans résoudre le hostname — DNS rebinding et IP décimales/octales/hex
  passaient. La validation résout désormais via `getaddrinfo` et rejette
  loopback/privé/link-local/réservé ; `rag-indexer` conserve sa sémantique
  `allow_localhost` (LAN autorisé, metadata toujours bloqué). Le risque
  résiduel TOCTOU (pas de pinning d'IP) est documenté. 18 tests offline.

### Modifié

- **`agent-base.md` bascule sur le SDK** (étape 2 de l'ADR-003) : le protocole
  mémoire des agents pointe vers `grimoire memory remember`/`recall`,
  `mem0-bridge.py` devient le fallback documenté (SDK absent). Idem
  `agent-base-compact.md` et `grimoire-trace.md`. `export-md` reste legacy
  (pas d'équivalent SDK).

## [3.20.0] - 2026-07-02

### Ajouté

- **`grimoire doctor` : check « agents découvrables »** (suivi issue #33) — si des
  agents sont déployés mais qu'aucun wrapper `*.agent.md` n'existe dans
  `.github/agents/`, doctor échoue avec la remédiation (`grimoire init . --force`)
  au lieu d'annoncer un projet sain.

### Modifié

- **Console 100 % cp1252-safe** : derniers glyphes non-ASCII purgés des sorties
  terminal de `framework/tools/` (flèches `→` U+27A1 → `->`, barres `█▓░` →
  `#=-!`) — clôt la purge emoji étapes 2-3.
- **Docs SDK-first** : `archetype-guide.md` et `onboarding.md` présentent le
  chemin SDK en premier ; les commandes shell restent documentées (mode
  maintenance, certaines n'ont pas d'équivalent SDK).
- ADR-003 : prérequis de parité documenté — `agent-base.md` reste sur
  `mem0-bridge.py` tant que la CLI SDK n'offre pas d'écriture mémoire typée
  (`remember --type` + dédup UUID5).


## [3.19.0] - 2026-07-02

### Corrigé (issue #33 — merci @zavrocKk)

- **Windows : agents découvrables** — la détection des fichiers agents utilisait
  un test de sous-chaîne `"/agents/"` qui ne matche jamais avec des backslashes ;
  `.github/agents/` restait vide sous Windows. Remplacé par un test sur
  `path.parts` (helper `_is_agent_markdown`, 4 sites, tests PureWindowsPath).
- **Template `custom-agent.tpl.md` réparé** — 12 ouvertures de commentaires HTML
  avaient été écrasées par un search/replace débordant ; chaque `grimoire init`
  propageait le bruit. Les deux copies (archetypes + _grimoire/_config) sont
  restaurées (13 `<!--` = 13 `-->`).
- **`grimoire init . -y` fonctionne** — l'option `--yes/-y` documentée n'existait
  qu'au niveau global (`grimoire -y init`) ; elle est maintenant aussi locale à
  `init`, ce qui rétablit le mode express non-interactif (CI/scripts).
- **Portabilité Windows** — `stigmergy.py` : verrou fichier portable
  (fcntl POSIX / msvcrt Windows / no-op sinon) au lieu d'un `import fcntl`
  top-level fatal ; `agent-caller.py` : séparateurs box-drawing → ASCII
  (UnicodeEncodeError sur console cp1252) ; wizard `init` : indicateurs de
  progression `[■□□□]` → `[#---]`.
- **README.fr : config MCP réelle** — la section pointait vers un
  `framework/mcp/server.js` inexistant avec 7 outils fictifs ; remplacée par le
  vrai serveur (`grimoire-mcp`, Python) et la liste réelle des 12 outils.

### Modifié

- **`agent-caller.py` : statut `simulated`** — en mode standalone (sans backend
  LLM), `call` renvoyait `status="success"` et polluait les métriques aval
  (success_rate, fitness, dashboard) avec des exécutions n'ayant jamais eu
  lieu. Nouveau statut `simulated`, compté séparément dans `get_stats`.

## [3.18.0] - 2026-07-01

### Ajouté

- **Démo animée quickstart** (`docs/assets/demo-quickstart.svg`) intégrée aux
  README EN/FR — sorties réelles validées en sandbox (init → standard init
  `--needs solo-prototyping` → verify OK → score 81/70 → gate check OK).
- **`docs/evals-protocol.md`** : protocole pré-enregistré (bras governed vs
  baseline, métriques, règles d'honnêteté) pour mesurer l'effet du standard
  avant tout claim d'efficacité public.
- **Transition shell → SDK** : `grimoire-init.sh` passe en mode maintenance —
  avis non bloquant au lancement pointant vers `grimoire init` (SDK),
  supprimable via `GRIMOIRE_SUPPRESS_INIT_NOTICE=1` ; le README.fr présente le
  chemin SDK en premier. Le script reste fonctionnel (`validate --all` vert).
- **MCP — standard gouverné consommable par les agents** : 4 nouveaux outils MCP
  (`grimoire_standard_verify`, `grimoire_standard_audit`, `grimoire_standard_score`,
  `grimoire_standard_gate`) exposent verify/audit/score/gate au travers de
  `grimoire-mcp` ; l'audit inclut les actions de remédiation proposées. 12 tests.
- **Waivers gouvernés pour l'audit de dépendances** (issue #20) :
  `.github/security/dependency-waivers.yaml` (schéma waivers du standard, borné par
  `expires_at`) + `scripts/depaudit-waivers.py` qui traduit les waivers actifs en
  `--ignore-vuln` ; un waiver expiré re-durcit automatiquement le job dep-audit.
  Waiver initial : CVE-2025-3000 (torch, transitif, sans fix amont). 5 tests.
- **Garde anti-drift de version** : `tests/unit/test_version_sync.py` échoue si
  `version.txt` (consommé par grimoire.sh / grimoire-init.sh / smoke-test) diverge
  de `src/grimoire/__version__.py`.
- `docs/rnd.md` : les features expérimentales (session branching, darwinism,
  stigmergy, dream mode…) documentées séparément du cœur mûr.

### Modifié

- **README anglais** recentré sur le différenciateur (standard agentique gouverné,
  ≈180 lignes) ; la version française complète devient `README.fr.md`.
- Section MCP du README corrigée : liste réelle des 12 outils (l'ancienne liste
  documentait 10 outils inexistants) ; retrait du flag `--transport sse` non
  implémenté ; `grimoire standard gate` → `grimoire standard gate check`.
- `version.txt` resynchronisé (3.4.2 → 3.17.0) ; badge de version statique retiré
  du README (le badge PyPI dynamique fait foi) ; version en dur retirée
  d'`ARCHITECTURE.md`.
- **`framework/memory/` lint-clean** (36 erreurs ruff → 0) : corrections mécaniques
  (implicit Optional, contextlib.suppress, pathlib, ClassVar, FURB162) +
  `per-file-ignores` justifiés pour les patterns intentionnels (S110/S310 probing
  tolérant, SIM112 env vars legacy rétro-compat). Zone ajoutée au scope lint
  (Makefile + CI).

### Supprimé

- **Distribution npm mort-née** : `npm/`, `package.json` racine (version figée
  3.4.3) et workflow `npm-publish.yml` retirés — le paquet n'a jamais été publié
  sur npm ; PyPI est le canal de distribution.

## [3.17.0] - 2026-06-29

### Ajouté

- **Cockpit local — dashboard multi-projets** (`grimoire cockpit`) — un site web local,
  embarqué dans le paquet, qui gouverne tous les projets Grimoire de la machine :
  portefeuille, observabilité (coûts/traces), santé CI, et gestion mémoire gouvernée.
  - Mode daemon convivial : `start` (arrière-plan + ouverture navigateur, non bloquant),
    `stop`, `status`, `open`, plus `serve` (premier-plan). Cross-platform.
  - API locale (`127.0.0.1` only) : introspection en lecture (statut, lint, recherche,
    taxonomie) et écritures gouvernées (`gc`, `delete`, `sync`) sous confirmation
    explicite — toujours via l'API Memory OS, jamais d'accès brut.
  - Registre `~/.grimoire/cockpit/registry.json` géré par `add`/`remove`/`list` ;
    `grimoire init` auto-enregistre le projet scaffoldé (opt-out `GRIMOIRE_NO_COCKPIT`).
  - Vitrine publique vs cockpit : les actions de pilotage sont actives en local et
    verrouillées sur la vitrine (`*.github.io`) ; données de démo multi-projets pour la
    vitrine via `scripts/gen-demo-projects.py`.
- **Mémoire sans base de données vectorielle** — nouveau backend `lexical` (sqlite FTS5
  BM25, accent-insensible) offrant une recherche sans aucune DB vectorielle, service ni
  réseau. Pour les environnements (corpo, régulés, air-gapped) qui interdisent une base
  vectorielle locale.
  - Option de setup `memory.vector_database` (true|false) et `memory.retrieval_mode`
    (vector|lexical) dans `project-context.yaml`, émises par `grimoire init` et validées
    par le schéma. `vector_database: false` force le backend `lexical` et court-circuite
    l'auto-détection réseau (aucune sonde ollama/qdrant).
  - Profil gouverné `no_vector_target` (sqlite-fts5) dans le template `memory-policy.yaml`.
  - `mem0-bridge seed` — peuple le backend depuis la source-of-truth markdown (mémoire
    projet + dossier optionnel), avec gate evidence/redaction et idempotence.

## [3.16.0] - 2026-06-26

### Changé

- **Purge emoji — sortie terminal (étape 3, finale)** — Balayage déterministe de toute la
  sortie CLI : `framework/tools/*.py`, le SDK `src/grimoire/**` et les tests (126 fichiers,
  ~1900 occurrences).
  - Glyphes de statut → marqueurs ASCII maison : `✅✔✓`→`[OK]`, `❌✖✗`→`[x]`, `⚠`→`[!]`,
    `ℹ`→`[i]`, pastilles de sévérité `🔴`→`[!!]` / `🟡🟠`→`[!]` / `🟢`→`[ok]` / `🔵`→`[i]`,
    `🚫⛔🛑`→`[STOP]`.
  - Emojis purement décoratifs (en-têtes de section, icônes de catégorie) supprimés.
  - Symboles typographiques conservés (flèches `→ ← ↑ ↓`, tirets, points de suite) — ce ne
    sont pas des emojis.
  - Ternaires devenus identiques après strip remédiés (markers distincts : `[fix]`, `[sem]`/
    `[lex]`, `[~]`/`[+]`, `[+]`/`[-]`), échelle de santé `dashboard` re-distinguée
    (`[ok]`/`[~]`/`[!]`/`[!!]`).
  - Correctif de parsing : `antifragile-score._count_contradictions` ne dépend plus d'un
    glyphe `⏳` supprimé (active = non-résolu).
  - Suite complète verte : 5996 passed, 4 skipped ; ruff clean.

### Corrigé (release/hygiène)

- **Pipeline release robuste** — `release.yml` génère désormais `RELEASE_NOTES.md` en
  best-effort depuis la trace puis **retombe systématiquement sur `git log`** si le fichier
  est absent ou vide (corrige l'échec v3.15.0 où `cat RELEASE_NOTES.md` plantait).
- **Artefacts générés dé-trackés** — `_grimoire-output/Grimoire_TRACE.md` et
  `_grimoire/_memory/*.sqlite3` retirés du suivi git et ajoutés au `.gitignore` (ils avaient
  été inclus par erreur via `git add -A` en v3.15.0, ce qui faussait le workflow release).

## [3.15.0] - 2026-06-26

### Supprimé / Changé

- **Nettoyage du layout legacy — résidu complet** — Tout le code fonctionnel et l'outillage sont débarrassés de l'ancien layout de modules :
  - `agent-lint.py` retargeté de l'ancien layout `*/agents/` vers `_grimoire/*/agents/` (variable `grimoire_dir`, manifeste, messages) ;
  - `observatory.py` ne supporte plus l'ancien layout de sortie / fichier de trace (Grimoire uniquement) ; tests alignés ;
  - `github-cc-check.yml.tpl` (framework + copie déployée) rebrandé « Grimoire Completion Contract », chemin `_grimoire/_config/custom/cc-verify.sh`, hint `grimoire init` ;
  - `bug-finder.py` ignore désormais `.grimoire-rnd` (ancien nom obsolète) ;
  - docstrings/commentaires/aides nettoyés : `grimoire-setup.py`, `agent-test.py`, `skill-validator.py` ;
  - `grimoire-completion.zsh` : suppression des alias legacy (`*-master`, `compdef`) ;
  - `.github/CODEOWNERS`, `.vscode/settings.json` (`git.branchPrefix`), `.vscode/snippets` (préfixes `grimoire-*`), `examples/web-app-todo`, `tests/smoke-test.sh`, `tests/run-coverage.sh`, `_grimoire/_memory/requirements-full.txt` : rebrand `_grimoire`.

## [3.14.0] - 2026-06-25

### Changé

- **Purge emoji — terminal & exemples docs (étape 2)** — `framework/tools/context-guard.py` : `status_icon`/`role_icon` renvoient des marqueurs texte maison (`[OK]`/`[WARN]`/`[CRIT]`, `[agent]`/`[mem]`…) au lieu d'emojis (un SVG ne s'affiche pas en terminal) ; `test_python_tools` aligné. Exemples docs `creating-agents`/`archetype-guide` : emojis `icon:` → noms d'icônes maison / texte.
- **Layout legacy retiré de l'outil shell** — `framework/tools/grimoire-setup.py` ne synchronise plus les modules legacy `{bmm,core,cis,tea,bmb}` (suppression `MODULE_CONFIGS`/`check_config_file`/`apply_config_file`) ; propage l'identité vers `project-context.yaml` + `.github/copilot-instructions.md`. `grimoire.sh` inchangé. `test_grimoire_setup` aligné. **Reste** : références legacy résiduelles dans ~10 autres outils framework (scanners) + emojis dans les `print()` framework — sweep dédié.

## [3.13.0] - 2026-06-25

### Changé

- **Layout legacy retiré de `grimoire setup` (SDK)** — `grimoire setup` ne synchronise plus les configs de modules legacy `{bmm,core,cis,tea,bmb}/config.yaml` (taxonomie legacy que le scaffold actuel ne crée plus) ; il propage l'identité utilisateur (source `project-context.yaml`) vers `.github/copilot-instructions.md` uniquement. Docstrings (`app.py`, `project.py`) et docs (getting-started, grimoire-yaml-reference, onboarding) nettoyés de la marque d'origine (« Master » d'origine → « Grimoire Master »). Noms de modules internes (bmm/core/cis/tea/bmb) conservés. **Reste à traiter** : l'outil shell legacy `framework/tools/grimoire-setup.py` (+ `grimoire.sh`, test non-CI) — décision standalone vs délégation SDK.

## [3.12.0] - 2026-06-25

### Changé

- **Icônes maison pour les champs `icon:` (zéro emoji) — étape 1 de la purge emoji** — les valeurs `icon:` des archétypes, agents et de la taxonomie `agent_forge` ne sont plus des emojis Unicode mais des **noms d'icônes maison** (réf `docs/assets/icons/*.svg` : `server`, `shield-pulse`, `sparkle`, `plug`, `flask`, `wrench`, `network`, `chart`, `clipboard`, `bolt`, `grimoire`, `hexagon`, `temple`, `microscope`, `lightbulb`, `boomerang`, `seal`). 16 fichiers DNA + taxonomie SDK & framework + tests alignés. Politique : aucun emoji Unicode, icônes maison uniquement.

## [3.11.5] - 2026-06-25

### Corrigé

- **Review documentation web — couverture du standard agentique** — `index.md`, `concepts.md` et `cli-reference.md` couvrent désormais le standard agentique gouverné (fonctionnalité clé, concept dédié, groupe de commandes `grimoire standard …`), jusqu'ici absent de toute la doc cœur malgré v3.5–v3.11. Arbre d'architecture corrigé (mémoire : Weaviate/Neo4j/Qdrant). Emojis de diagramme (✅/🔴) remplacés par marques typographiques (✓/✗). Build `mkdocs --strict` propre ; nav 36/36 sans orphelin ni lien cassé.

## [3.11.4] - 2026-06-25

### Corrigé

- **Icônes maison uniquement (zéro emoji Unicode)** — purge des emojis Unicode du README : marqueur expérimental → icône maison `flask.svg`, section SDK Python (`🐍`) → `server.svg`. Politique projet : aucun emoji Unicode dans la documentation, toutes les icônes sont des SVG maison (`docs/assets/icons/`).

## [3.11.3] - 2026-06-25

### Corrigé

- **Passe d'honnêteté + maturité sur le README** — marqueurs « expérimental » (icône maison flask) sur les features exploratoires (Session Branching, Agent Darwinism, Stigmergy, Dream Mode, R&D Engine, les 15+ avancées) + légende de maturité ; reformulation des claims sur-vendus (« blockchain légère » → journal **hash-chaîné** sha256 ; « reinforcement learning » → **bandit ε-greedy** ; « Protocole BFT » → quorum ; « intelligence émergente » → coordination émergente) ; mise en avant du **standard agentique gouverné** comme différenciateur mûr. Aucune feature retirée — toutes sont réelles et testées.

## [3.11.2] - 2026-06-25

### Corrigé

- **`framework/memory` : env var canonique `GRIMOIRE_*`** — la sélection de backend lit désormais `GRIMOIRE_QDRANT_URL`/`GRIMOIRE_OLLAMA_URL` (casse de l'écosystème) avec repli **rétro-compatible** sur l'ancienne casse `Grimoire_*` (helper `_env_url`). Corrige le non-respect silencieux des overrides d'environnement sans casser les setups existants. Couvert par `tests/unit/test_framework_memory_backends.py`.
- **Durcissement lint `framework/memory`** — chaînage `raise … from None` sur les ré-émissions d'`ImportError` (B904), nettoyage `F401`/`RUF013`/`F541`/`E401`, `E741` reporté (script legacy non testé). Les patterns de probing tolérant aux pannes (`S110`/`S310`) sont conservés intentionnellement.

## [3.11.1] - 2026-06-25

### Corrigé

- **Parcours getting-started complété** — ajout des sections « Adopter le standard agentique gouverné » (`grimoire standard needs/init/verify/audit/score/gate`) et « Portabilité multi-assistant » (entrypoints CLAUDE/AGENTS/GEMINI/.cursorrules + `.mcp.json`), absentes du guide de démarrage malgré les releases v3.5–v3.11.

## [3.11.0] - 2026-06-25

### Ajouté

- **Page de référence des contrôles gouvernés** (`docs/governed-controls.md`) — les 36 patterns regroupés par catégorie (intention, profil minimal, artefact, checks clés), **générée** depuis `pattern-catalog.yaml` via `docs/gen-governed-controls.py` (source unique, zéro drift) et ajoutée à la navigation. Test anti-drift `test_governed_controls_doc_covers_all_patterns` : tout pattern de `capability-map.yaml` doit être documenté. Comble le manque de documentation par-contrôle (jusqu'ici seulement dans les YAML).

## [3.10.2] - 2026-06-25

### Corrigé

- **Hygiène lint `framework/memory`** — corrections ruff *sans impact comportemental* (tri d'imports, f-strings sans placeholder, mode `open()` redondant) sur le bridge mémoire legacy. Les patterns intentionnels (probing backend `S110`/`S310`) et les items risqués à toucher en code non testé (`B904`/`E741`) restent en dette tracée.
- **Flag : convention d'env var `framework/memory`** — le code lit `Grimoire_*` (casse mixte), divergente de `GRIMOIRE_*` (reste de l'écosystème) → un override `GRIMOIRE_QDRANT_URL` n'y est pas pris en compte. Cohérent dans tout `framework/memory` (legacy, non testé) ; **non corrigé** (casserait les setups existants) — signalé en code + backlog.

## [3.10.1] - 2026-06-25

### Corrigé

- **Attribution de score des contrôles gouvernés** — les checks des contrôles ajoutés en v3.6–v3.8 sont désormais routés vers leur dimension de score naturelle (`compression.`→context_contract, `integrity.`→memory_policy, `cost.`→provider_policy, `council.`→decision_graph, `guardrail.`→rule_packs, `merge.`/`cluster.`/`env.`→ci_release_gate, `wsm.`/`flowdsl.`/`runtime.`/`k8s.`→orchestration_policy, `visual.`/`browser.`→evidence_gates, `privilege.`/`firewall.`/`workspace.`/`tools.blast_radius`→hook_registry, `promptver.`→observability_cockpit) au lieu du bucket générique `artifacts`. `grimoire standard score` reflète ainsi correctement ces contrôles. Aucun impact sur les profils par défaut (contrôles optionnels, non scaffoldés).

## [3.10.0] - 2026-06-25

### Ajouté

- **`.mcp.json` portable généré par `grimoire init`** — enregistre le serveur MCP Grimoire via l'entrypoint console `grimoire-mcp` (OS-neutre, aucun chemin absolu codé en dur), lu par Claude Code, Cursor et autres clients MCP. Complète l'adaptivité multi-assistant : le MCP fonctionne out-of-the-box après `pip install 'grimoire-kit[mcp]'`. Non écrasé s'il existe déjà.

## [3.9.0] - 2026-06-25

### Ajouté

- **Entrypoints multi-assistant portables** — `grimoire init` génère désormais, à côté de `.github/copilot-instructions.md`, des entrypoints `CLAUDE.md` (Claude Code, via import `@`), `AGENTS.md` (standard cross-tool : Codex et autres), `GEMINI.md` (Gemini CLI) et `.cursorrules` (Cursor), tous pointant vers le fichier canonique (source unique, zéro drift, non écrasés s'ils existent). Un projet Grimoire fonctionne ainsi avec Copilot, Claude, Codex, Gemini et Cursor sans configuration manuelle. Comble le gap d'adaptivité multi-assistant (jusqu'ici Copilot/VS Code-first + MCP uniquement).

## [3.8.0] - 2026-06-25

### Ajouté

- **2 contrats déclaratifs (clôture du backlog déclaratif)** — `workflow-state-manifest` (machine à états de mission durable : états, transitions gardées, interrupts ; exécution déléguée à LangGraph/Conductor) et `k8s-agent-manifest` (contrat K8s déclaratif : CRD, resource limits, network allowlist, service account, OTel ; provider natif délégué à kagent). Catalogue de patterns **34 → 36** ; **`planned_capabilities` désormais vide** — tout le déclarable est implémenté, ne restent que les adapters runtime externes (LangGraph, kagent).

### Corrigé

- **README à jour** — badge de version corrigé (3.1.0 → 3.8.0) et ajout de la section « Standard agentique gouverné » (profils, 36 patterns, `verify`/`audit`/`score`/`gate`) qui manquait totalement malgré les releases v3.5–v3.7.
- **CITATION.cff** — version et date alignées (3.1.0/2025 → 3.8.0/2026).

## [3.7.0] - 2026-06-25

### Ajouté

- **8 contrats déclaratifs (lot benchmark v3.7)** — concrétisation des capacités `planned_capabilities` purement déclaratives, recette `capability-map` + template + `_verify_*` fail-closed + test : `workspace-isolation`, `policy-by-environment`, `browser-tool-contract`, `runtime-provider-contract`, `prompt-version-observability`, `cluster-action-dry-run`, `doc-to-graph-pipeline`, `flow-dsl-minimal`. Catalogue de patterns **26 → 34**. Chaque template vérifie *clean* (test paramétré `test_control_template_verifies_clean`). Promotion `planned_capabilities` → `mapped_capabilities` dans les profils concernés (controlled/orchestrated/governed/production) ; restent en `planned` les 2 sous-systèmes à adapter externe (`workflow-state-engine`/LangGraph, `kubernetes-agent-control-plane`/kagent).

## [3.6.1] - 2026-06-25

### Corrigé

- **Cohérence capability-map ↔ profils** — `mapped_capabilities` de chaque profil ne référence plus que des patterns réels ; les capacités encore non implémentées sont déplacées dans un nouveau champ `planned_capabilities`. Les 11 contrôles v3.6.0 sont rattachés aux bons profils (ex. `agent-privilege-boundary`/`decision-council-gate` → governed, `prompt-injection-firewall`/`guardrail-contract` → controlled). Test garde-fou `test_profile_mapped_capabilities_are_real_patterns` (mapped ⊆ patterns ; planned ∩ patterns = ∅) — l'incohérence ne peut plus réapparaître.
- **Vérificateur Completion Contract (`framework/cc-verify.sh`)** — résout désormais l'interpréteur du virtualenv projet (`.venv/bin/python`) pour pytest/ruff, avec fallback PATH et saut gracieux si indisponible (corrige un `ModuleNotFoundError` bloquant quand pytest n'est pas installé globalement).

## [3.6.0] - 2026-06-25

### Ajouté

- **11 contrôles gouvernés benchmark-driven** — issus de la comparaison avec le corpus agentique de référence (37 projets), concrétisant des capacités jusqu'ici seulement nommées dans `profile-map.yaml` : `tool-blast-radius-limiter`, `agent-privilege-boundary` (ScrubTokenEnv controller/agent), `prompt-injection-firewall` (GOV-12), `remote-hygiene-guard` (GOV-13), `decision-council-gate` (GOV-14), `context-compression-gate`, `memory-integrity-validator`, `merge-lane-fault-classifier`, `llm-cost-registry` (coût + SLO CrashRate/UnhealthyRate), `guardrail-contract` (input/output/tool/model versionnés), `visual-evidence-gate` (QUA-12). Chacun : pattern (`capability-map.yaml` + `pattern-catalog.yaml`), artefact + template, vérification `_verify_*` fail-closed dans `grimoire standard verify`. Catalogue de patterns 15 → 26.
- **Benchmark corpus & matrice d'écarts** — `docs/agentic-standard-benchmark-corpus-2026Q2.md` (22 patterns + 15 contrôles cibles vs couverture réelle) et `docs/travaux-inacheves-2026Q2.md` (backlog priorisé : v3.7.0+, Memory OS, R&D à porter, dette repo, branches/PR en attente).
- **Rampe « commencer petit » pour l'installation par besoins** — le `needs-catalog.yaml` est désormais **tiéré** (`essential` / `advanced` / `enterprise`) avec un besoin de départ recommandé (`solo-prototyping`, marqué `▶`). `grimoire standard needs` regroupe les besoins par tier et affiche leur **empreinte** (profil · nombre de patterns · nombre de services externes) ; `grimoire standard needs --explain` révèle à la demande les patterns derrière chaque besoin (divulgation progressive). L'assistant `standard init --interactive` ordonne les besoins essentiels d'abord et pré-sélectionne le besoin recommandé (Entrée = recommandé). Documentation : section « Commencer petit (rampe progressive) » dans `docs/agentic-standard-install-by-needs.md`.

### Changé

- **Défaut minimal de `grimoire standard init`** — sans `--needs`/`--profile`, l'init scaffolde désormais le profil **`starter`** (au lieu de `orchestrated`), avec un rappel pour choisir par besoin. Le comportement résolu via `--needs`/`--pattern` est inchangé.

## [3.5.0] - 2026-06-08

### Ajouté

- **Installation par besoins** — nouvelle couche d'installation custom : `grimoire standard needs`, `standard plan --needs ...`, `standard init --needs/--pattern/--memory/--interactive` et `standard doctor`. Deux fichiers déclaratifs (`framework/agentic-standard/capability-map.yaml`, `needs-catalog.yaml`) résolvent un besoin projet en profil + patterns + artefacts + extras technologiques, et écrivent un `install-manifest.yaml` auditable. Auto-install des extras opt-in via `--install-extras`.
- **Parité patterns R8/R9/R10** — back-port dans les templates Kit des patterns `redis-hot-memory-soft-gate`, `governed-hook-gateway`, `skill-classification-matrix`, `governed-observability-cockpit`, des familles de règles `hooks`/`skills`/`observability`, du contrat `observability-policy.yaml`, de la taxonomie `managed_sources` et de la dimension de score `observability_cockpit`.
- **Catalogue de patterns étendu (9 → 15)** — ajout de `code-graph-projection` (neo4j), `governed-agent-orchestration`, `governed-knowledge-indexing`, `mission-evidence-ledger`, `tool-mediation-gate` (mcp) et `provider-cost-slo`, câblés dans `capability-map.yaml` et `needs-catalog.yaml`.
- **Memory OS cible** — portage du socle Weaviate + Neo4j + SQLite sidecar, migration Qdrant -> Weaviate/Neo4j, projections graph/vector, commandes `grimoire memory graph`, `memory vector`, `memory gate` et noyaux missions/evidence/policies/runtime/traces/bridges/evals.
- **Standard Memory OS** — `grimoire standard init/verify/audit/score/gate` vérifie maintenant un contrat Memory OS cible : Redis hot memory, Weaviate mémoire sémantique durable, Neo4j projection graphe, SQLite sidecar/fallback et Qdrant en source legacy/migration uniquement.

### Changé

- **Détection mémoire** — `grimoire init --backend auto` privilégie désormais Weaviate quand il est disponible localement, conserve Qdrant comme fallback compatible (`qdrant-local`), puis Ollama et le backend local.

## [3.4.4] - 2026-05-29

### Corrigé

- **CI SDK multi-OS** — stabilisation complète de `Grimoire SDK CI` : assertions CLI robustes face aux rendus Typer/Rich, couverture agentic standard incluse, smoke Windows ciblé et workflow de tests portable.
- **Runtime standard** — sorties JSON et tests du runtime agentique rendus portables entre Linux, macOS et Windows, notamment les chemins `context`/`knowledge`.
- **Release readiness** — correction ShellCheck, test de backoff déterministe et durcissement des tests d’édition de configuration pour débloquer la publication PyPI.

## [3.4.3] - 2026-05-28

### Ajouté

- **Agentic Standard Bridge** — profils `minimal`, `orchestrated` et `governed`, génération des artefacts ISO/design-pattern, vérification/audit CLI et baseline de preuves.
- **Provider onboarding** — détection non-secrète des providers, activation explicite via `standard init --provider/--providers`, politiques `hosted-safe`, `local-first` et `mixed`.
- **Package npm préparé** — launcher `grimoire-kit` ajouté, publication npm différée en attendant l’authentification npm dédiée.

### Corrigé

- **Sécurité standard** — durcissement des chemins générés, rejet des `task_id` traversants, confinement des locators knowledge locaux et échappement des valeurs projet injectées dans les templates.
- **CI/Docs** — workflow ciblé agentic standard, documentation d’extension des profils et pin explicite du bridge consommé par Forge.

## [3.4.2] - 2026-03-30

### Corrigé

- **Init mémoire: durcissement non-interactif** — la réutilisation auto d'un setup détecté valide désormais la reachability (Qdrant/Ollama) avant sélection backend.
- **Secrets: non persistance dans project-context.yaml** — `qdrant_api_key` détectée n'est plus écrite automatiquement dans la configuration projet; usage recommandé via variable d'environnement.
- **Init YAML: échappement des remplacements sed** — les URLs injectées sont échappées pour éviter la corruption YAML quand des caractères spéciaux sont présents.
- **Backend Qdrant: compat env vars** — prise en charge de `GRIMOIRE_QDRANT_API_KEY` en plus de `Grimoire_QDRANT_API_KEY`.
- **README: rendu architecture GitHub** — suppression du wrapper HTML autour du diagramme Mermaid pour un rendu fiable sur GitHub.

### Ajouté

- **CLI: A1 — `--debug` / `-D` flag global** — Expose GRIMOIRE_DEBUG en flag CLI (à la ruff/uv). Fonctionne aussi via `GRIMOIRE_DEBUG=1` env var. Active les tracebacks complets via Rich. Message d'erreur mis à jour : « Use --debug or set GRIMOIRE_DEBUG=1 » (Round 37)
- **CLI: A5 — détection env vars conflictuelles** — La commande `env` détecte et signale les combinaisons incohérentes (ex: GRIMOIRE_DEBUG + GRIMOIRE_QUIET). Affiché en texte et JSON (champ `conflicts`) (Round 37)
- **Tests: +12** — R37 : DebugFlag (4) + OnlineDNS (2) + RepairAuditTrim (2) + ConfigSetExitCode (1) + EnvConflicts (3) → 373 tests CLI (Round 37)

### Corrigé

- **CLI: A2 — `repair` audit trim race condition** — Même pattern que R36-F2 : `splitlines()` + `write_text()` remplacés par lecture ligne par ligne + `writelines()` propre (Round 37)
- **CLI: A3 — `config set` exit codes sémantiques** — `config set` utilisait `Exit(1)` pour key-not-found alors que `_resolve_config_key` utilise `_EXIT_CONFIG=2`. Cohérence rétablie (Round 37)
- **CLI: A4 — `_is_online` DNS-based** — Remplace le socket brut vers 1.1.1.1:53 par une résolution DNS (`getaddrinfo`) en premier, avec fallback socket. Fonctionne derrière proxy/firewall corporate (Round 37)

### Précédent (Round 36)

#### Ajouté

- **CLI: E6 — exit codes sémantiques** — Constantes `_EXIT_OK=0`, `_EXIT_USER=1`, `_EXIT_CONFIG=2`. Appliquées à `_resolve_config_key` (key not found) et `GrimoireConfigError` dans status. Prêtes pour migration progressive (Round 36)
- **Tests: +13** — R36 : ExitCodeConstants (3) + LogOperationTruncate (1) + MergeCommand (5) + PluginsList (4) → 361 tests CLI (Round 36)
- **CLI: I2 — `history --clear`** — Nouveau flag `--clear` pour purger l'audit log avec confirmation (ou `--yes` pour skip). Supporte JSON output (Round 35)
- **Tests: +12** — R35 review complète : GetFmtHelper (3) + DoctorFixAudit (2) + DoctorJsonOptionals (2) + CompletionInstallAudit (1) + HistoryClear (4) → 348 tests CLI (Round 35)

### Corrigé

- **CLI: F2 — `_log_operation` truncate race condition** — Le truncate utilisait `splitlines()` + `write("\n".join(...))` en deux opérations distinctes. Remplacé par `readlines()` + `writelines()` + `truncate()` dans un seul handle (Round 36)
- **CLI: F1 — `doctor --fix` sans audit trail** — Les commandes mutatives loguent toutes via `_log_operation` sauf `doctor --fix`. Ajout de l'appel audit quand des répertoires sont créés (Round 35)
- **CLI: I4 — `doctor --json` omet les optionnels manquants** — Les packages optionnels non installés n'apparaissaient pas dans le JSON. Ils sont maintenant inclus avec `"optional": true` (Round 35)
- **CLI: I5 — `completion install` sans audit trail** — Commande mutatrice (écrit dans ~/.bashrc/.zshrc) sans trace. Ajout `_log_operation("completion_install", {"shell": shell})` (Round 35)
- **CLI: H4 — DRY output format** — Le pattern `(ctx.obj or {}).get("output", "text")` était répété 26× dans le code. Extrait helper `_get_fmt(ctx)` (Round 35)

### Précédent (Round 34)

#### Ajouté

- **Tests: +6** — R34 lint global output + env enrichment : LintGlobalOutput (2) + NullcontextImport (1) + EnvVarsComplete (3) → 336 tests CLI (Round 34)

#### Corrigé

- **CLI: H1 — lint ignore le flag global `-o`** — Seule des ~25 commandes, `lint` utilisait `--format/-f` au lieu de `ctx.obj["output"]`. Ajout de `ctx: typer.Context` ; `--format` reste comme fallback rétrocompat (Round 34)
- **CLI: H2 — `_status_spinner` lazy import** — `contextlib` importé localement alors que `nullcontext` peut être importé au top-level. Remplacé par import direct (Round 34)
- **CLI: H3 — `env` ne montre que 2/6 env vars** — Manquait GRIMOIRE_OUTPUT, GRIMOIRE_QUIET, GRIMOIRE_OFFLINE, NO_COLOR. Ajouté les 4 (Round 34)
- **CLI: I1 — `env` sans statut réseau** — `env` est utilisé pour le debug et les bug reports. Ajout de `is_online()` dans la sortie text et JSON (Round 34)

### Précédemment (Round 33)

#### Ajouté

- **Tests: +10** — R33 DRY refactors + history enhancement : CompletionDRY (4) + ConfigKeyResolver (3) + HistoryVersionColumn (3) → 330 tests CLI (Round 33)

#### Corrigé

- **CLI: H1+H4 — DRY completion** — `completion_install` et `completion_export` partageaient ~15 lignes identiques (subprocess + validation). Extrait helper `_generate_completion_script(shell)` + constante `_SUPPORTED_SHELLS = frozenset({"bash", "zsh", "fish"})` (Round 33)
- **CLI: H2 — DRY config traversal** — `config_show` et `config_get` partageaient 28 lignes de traversée dot-notation YAML. Extrait helper `_resolve_config_key(data, key)` (Round 33)
- **CLI: H3 — history sans colonne version** — `history` n'affichait pas le champ `"v"` ajouté en R32. Ajout colonne « Version » dans la table Rich (fallback « — » pour anciennes entrées) (Round 33)
- **CLI: I1 — history total_entries** — En mode JSON, `history` n'exposait que le nombre filtré (`total`). Ajout de `total_entries` (total brut du fichier) (Round 33)

### Précédemment (Round 32)

#### Ajouté

- **Tests: +9** — R32 audit & housekeeping : AuditVersionField (2) + RepairAuditLog (2) + SetupAuditLog (1) + DoctorNumbering (1) + SelfVersionImport (1) + CompletionParentDir (2) → 320 tests CLI (Round 32)

#### Corrigé

- **CLI: I1 — Audit log sans version** — `_log_operation()` n'incluait pas la version de grimoire-kit. Ajout d'un champ `"v": __version__` dans chaque enregistrement JSONL (Round 32)
- **CLI: H1 — repair sans audit** — `repair` ne loguait pas dans l'audit trail. Ajout de `_log_operation("repair", …)` après actions non-dry-run (Round 32)
- **CLI: H2 — setup sans audit** — `setup` ne loguait pas dans l'audit trail. Ajout de `_log_operation("setup", …)` après apply dans les deux chemins sync/override et défaut (Round 32)
- **CLI: H4 — doctor numérotation cassée** — Les commentaires de checks sautaient de 3 à 5 (check 4 supprimé sans renuméroter). Renuméroté séquentiellement 1→8 (Round 32)
- **CLI: H5 — self_version import redondant** — `self_version` faisait `import json as _json` localement alors que `json` est importé au niveau module. Supprimé en faveur de `json.loads()` (Round 32)
- **CLI: H6 — completion parent dir manquant** — `completion install` pour bash/zsh n'appelait pas `mkdir(parents=True)` sur le répertoire parent du fichier RC cible. Ajouté (fish l'avait déjà) (Round 32)

### Précédemment (Round 31)

#### Ajouté

- **Tests: +8** — R31 review fixes : EnvCmdNarrowException (2) + VersionCmdGrimoireError (1) + InterruptedRemoved (2) + CtxObjGuard (1) + SetupGlobalJson (2) → 311 tests CLI (Round 31)

#### Corrigé

- **CLI: C1 — env_cmd exception trop large** — `env` catchait `(typer.Exit, Exception)` masquant tout. Réduit à `(typer.Exit, GrimoireError)` — même correctif que R30 M4 sur `version_cmd` (Round 31)
- **CLI: C2 — version_cmd rate GrimoireProjectError** — `version` catchait `GrimoireConfigError` mais pas `GrimoireProjectError` (classes sœurs). Élargi à `GrimoireError` (base commune) (Round 31)
- **CLI: H1 — _interrupted dead variable** — Le flag `_interrupted` était set par `_handle_signal` mais jamais lu (`SystemExit` raised immédiatement). Supprimé (Round 31)
- **CLI: H2 — ctx.obj guard inconsistant** — 8 commandes utilisaient `ctx.obj.get()` sans guard None alors que d'autres utilisaient `(ctx.obj or {}).get()`. Standardisé vers le pattern sûr partout (Round 31)
- **CLI: H3 — setup ignore -o json global** — `setup` avait un flag `--json` dédié mais ignorait le flag global `-o json`. Ajout de `ctx: typer.Context` et unification : les deux méthodes fonctionnent (Round 31)

### Précédemment (Round 30)

#### Ajouté

- **Tests: +13** — R29 review fixes : EditorValidation (2) + SuggestIncludesAliases (1) + FlattenLists (3) + RequiredDirsConstant (2) + AuditLogAtomic (1) + HistorySkipCount (1) + RepairJsonOk (1) + VersionEnvFindConfig (2) → 294 tests CLI (Round 29)

#### Corrigé

- **CLI: C1 — Audit log race condition** — `_log_operation()` avait un TOCTOU entre `read_text()` et `write_text()` pour la troncation du log. Remplacé par un mode `r+` atomique (seek + truncate dans le même file handle) (Round 29)
- **CLI: C2 — Editor validation** — `config edit` appelait `os.execvp()` sans vérifier l'existence de l'éditeur. Ajout de `shutil.which()` avec suggestion `$VISUAL/$EDITOR` si absent (Round 29)
- **CLI: H3 — version/env hardcoded path** — `version` et `env` utilisaient `Path.cwd() / "project-context.yaml"` au lieu de `_find_config()`, ne fonctionnaient pas depuis un sous-répertoire (Round 29)
- **CLI: H4 — _flatten ignore lists-of-dicts** — `_flatten()` traitait les listes de dicts comme des valeurs opaques. Ajout de la récursion avec clés indexées : `repos.0.name`, `repos.1.path` (Round 29)
- **CLI: H8 — Aliases absent des suggestions** — `_suggest_command()` ne considérait que les commandes enregistrées, pas les alias. Ajout de `_KNOWN_COMMANDS.update(_ALIASES)` (Round 29)
- **CLI: H9 — DRY violation répertoires** — 8+ occurrences de tuples `("_grimoire", "_grimoire-output")` hardcodés. Extraction en constantes module `_REQUIRED_DIRS` + `_MEMORY_DIR` (Round 29)
- **CLI: M10 — config set acceptait des listes** — `config set` splittait les virgules pour créer des listes, comportement error-prone. Remplacé par un refus explicite avec guidance vers `config edit` (Round 29)
- **CLI: M13 — _log_operation muet sur erreur** — Le handler `OSError` ignorait silencieusement les erreurs. Ajout d'un avertissement console quand `GRIMOIRE_DEBUG` est défini (Round 29)
- **CLI: M14 — history ignore les entrées corrompues** — `history` sautait silencieusement les lignes JSONL invalides. Ajout d'un compteur `skipped` (affiché en texte et en JSON) (Round 29)
- **CLI: M15 — repair JSON manquait ok** — La sortie JSON de `repair` n'incluait pas le champ `"ok": true` contrairement aux autres commandes (Round 29)

### Précédemment (Round 28)

#### Ajouté

- **CLI: `grimoire config edit`** — Ouvre `project-context.yaml` dans `$VISUAL` / `$EDITOR` / `vi` (Round 28)
- **CLI: `grimoire config validate`** — Validation du schema config en place, JSON output `{valid, warnings}`, exit code 1 si invalide (Round 28)
- **CLI: `--profile` sur `check`** — 3 phases instrumentées : `check/lint`, `check/validate`, `check/structure` (Round 28)
- **Tests: +20** — R28 review fixes (13) + config edit (3) + config validate (4) → 281 tests CLI (Round 28)

### Corrigé

- **CLI: C1 — Audit filename in repair** — `repair` utilisait `"audit.jsonl"` au lieu de `_AUDIT_FILENAME` (`.grimoire-audit.jsonl`), le trimming du log ne fonctionnait jamais (Round 28)
- **CLI: C3 — Latence _is_online()** — Suppression du probe réseau 500ms dans le callback `main()` exécuté à chaque commande. Remplacé par `is_online()` lazy (cache une fois par process) (Round 28)
- **CLI: C4 — Config commands depuis subdirectory** — Les 5 commandes config (`show`, `get`, `path`, `set`, `list`) utilisaient un path hardcodé au lieu de `_find_config()` (Round 28)
- **CLI: C5 — Accumulation phase timings** — `_phase_timings` module-level jamais vidé entre invocations. Ajout de `.clear()` dans `cli()` (Round 28)
- **CLI: H6 — Newline échappé** — `\\n` dans l'affichage `--time` au lieu de `\n` (Round 28)
- **CLI: H7 — self version offline** — `self version` n'utilisait pas le flag offline, probe PyPI inutile quand hors-ligne (Round 28)
- **CLI: M5 — `_flatten` dupliqué** — Suppression de `_flatten_dict()` redondant, réutilisation de `_flatten()` dans `config list` (Round 28)

- **CLI: Command suggestions** — `_suggest_command()` détecte les fautes de frappe et propose des commandes proches via `difflib.get_close_matches()` (Round 27)
- **CLI: Signal handling** — Gestion propre de SIGINT/SIGTERM avec message et code de sortie Unix standard (128+signal) (Round 27)
- **CLI: `--profile` flag** — Breakdown timing par phase avec arbre Rich (`_timed_phase` context manager), instrumenté sur `doctor` (Round 27)
- **CLI: `grimoire repair`** — Auto-réparation des problèmes courants : création répertoires manquants, trim du audit log >90j, `--dry-run`, JSON output (Round 27)
- **CLI: Offline mode detection** — `_is_online()` avec test de connectivité rapide, `GRIMOIRE_OFFLINE=1` env var, `ctx.obj["offline"]` flag (Round 27)
- **Tests: +21** — TestCommandSuggestions (4) + TestSignalHandling (3) + TestPerformanceProfiling (4) + TestRepairCommand (6) + TestOfflineMode (4) → 261 tests CLI (Round 27)
- **CLI: Config auto-discovery** — `_find_config()` remonte l'arborescence pour trouver `project-context.yaml` quand on est dans un sous-répertoire (Round 26)
- **CLI: Rich spinners** — `_status_spinner()` affiche un spinner animé sur `upgrade` et `merge` (respecte `--quiet` et `-o json`) (Round 26)
- **CLI: Exemples dans l'aide** — Rich markup examples ajoutés aux docstrings de 8 commandes : init, doctor, validate, add, remove, status, check, upgrade (Round 26)
- **CLI: `grimoire history`** — Audit trail des opérations CLI récentes avec `--limit`, `--filter`, JSON output (`_grimoire/_memory/.grimoire-audit.jsonl`) (Round 26)
- **CLI: Audit log** — `_log_operation()` trace automatiquement init, add, remove, config_set, upgrade, merge dans un fichier JSONL (Round 26)
- **CLI: Deprecation framework** — `_DEPRECATED_FLAGS` dict + `_warn_deprecated()` pour gérer proprement les flags obsolètes dans les futures versions (Round 26)
- **Tests: +28** — TestAutoDiscovery (4) + TestSpinnerHelper (2) + TestSubcommandExamples (8) + TestAuditLog (4) + TestHistoryCommand (5) + TestDeprecationWarnings (3) + TestAuditIntegration (2) → 995 tests CLI (Round 26)
- **CLI: `--yes/-y` global flag** — Skip les confirmations interactives sur `remove` et `merge --undo` ; implicite en mode JSON (Round 25)
- **CLI: Confirmations interactives** — `remove` et `merge --undo` demandent confirmation avant toute action destructive (Round 25)
- **CLI: JSON output `upgrade`** — Sortie JSON structurée `{ok, version, dry_run, warnings, actions}` (Round 25)
- **CLI: Rich help panels** — Commandes organisées par catégorie : Project, Agents, Validation, Configuration, Utilities, Info (Round 25)
- **CLI: Error handler amélioré** — Affichage du code d'erreur et suggestions de récupération (`_format_error`, `_RECOVERY_HINTS`) (Round 25)
- **Tests: conftest.py CLI** — Fixtures `cli_project` et helper `assert_json_output` pour réduire la duplication de tests (Round 25)
- **Tests: +23** — TestYesFlag (5) + TestUpgradeJson (4) + TestHelpPanels (6) + TestErrorHandler (4) + TestConftestFixtures (3) + TestEpilog (1) → 967 tests (Round 25)
- **CLI: JSON output `init`** — Sortie JSON structurée `{ok, project, path, archetype, backend, directories}` (Round 24)
- **CLI: JSON output `up`** — Sortie JSON structurée `{ok, project, actions, dry_run, agents_count}` (Round 24)
- **CLI: `doctor --fix`** — Auto-correction des répertoires manquants avec rapport `fixed` en JSON (Round 24)
- **CLI: `--time` flag** — Affiche le temps d'exécution en ms après chaque commande (Round 24)
- **Tests: +19** — TestInitJson (3) + TestUpJson (2) + TestDoctorFix (4) + TestTimeFlag (2) + TestJsonOutputParametrized (8) → 944 tests (Round 24)
- **CLI: `config set KEY VALUE`** — Modification de clé config par dot-notation avec coercion de type, `--dry-run`, JSON (Round 23)
- **CLI: JSON output `add`/`remove`** — Sortie JSON structurée `{ok, action, agent}` pour scripting CI/CD (Round 23)
- **CLI: "Did you mean?" sur init** — Suggestions fuzzy pour archétypes et backends mal typés (Round 23)
- **Tests: +16** — TestConfigSet (7) + TestAddRemoveJson (6) + TestDidYouMean (3) → 925 tests (Round 23)
- **CLI: Command aliases** — Raccourcis courts : `i`=init, `d`=doctor, `s`=status, `v`=validate, `l`=lint, `ck`=check, `u`=up, `c`=config, `r`=registry (Round 22)
- **CLI: Env var overrides** — `GRIMOIRE_OUTPUT=json`, `GRIMOIRE_QUIET=1`, `NO_COLOR=1` pour scripting/CI sans flags (Round 22)
- **CLI: `add --dry-run` / `remove --dry-run`** — Flag `-n/--dry-run` sur les commandes add/remove pour prévisualiser sans modifier (Round 22)
- **CLI: `check` phases structurées** — Helper `_phase_header()` avec support `--quiet` pour une sortie plus propre (Round 22)
- **Tests: +16** — TestCommandAliases (5) + TestAddRemoveDryRun (7) + TestEnvVarOverrides (4) → 909 tests (Round 22)
- **CLI: `grimoire version`** — Commande standalone avec version, Python, plateforme, projet actif (text/JSON) (Round 21)
- **CLI: `grimoire self version`** — Version installée + vérification de mise à jour PyPI (text/JSON) (Round 21)
- **CLI: `grimoire self diagnose`** — Auto-diagnostic : dépendances, Python, entry point, statut global (text/JSON) (Round 21)
- **CLI: `grimoire config get KEY`** — Lecture d'une clé config par dot-notation (text/JSON) (Round 21)
- **CLI: `grimoire config path`** — Affiche le chemin résolu vers project-context.yaml (Round 21)
- **CLI: `grimoire config list`** — Liste toutes les clés config avec valeurs actuelles en table Rich (text/JSON) (Round 21)
- **CLI: Epilog Rich** — Exemples et aide rapide dans `grimoire --help` (Round 21)
- **Validator: détection clés inconnues** — Avertissements pour clés non reconnues avec suggestions "Did you mean?" (Round 21)
- **Tests: +27** — TestVersionCommand (3) + TestConfigGet (4) + TestConfigPath (2) + TestConfigList (3) + TestSelfVersion (2) + TestSelfDiagnose (3) + TestEpilog (1) + TestUnknownKeys (9) → 893 tests (Round 21)
- **CLI: `completion export`** — Export script de complétion vers stdout pour piping/dotfiles (Round 20)
- **CLI: Rich traceback** — Stack traces Rich avec `show_locals=True` quand `GRIMOIRE_DEBUG=1` (Round 20)
- **Makefile: `release`** — Target `make release VERSION=x.y.z` : bump, build, instructions tag (Round 20)
- **Makefile: `bench`** — Target `make bench` pour benchmarks de performance (Round 20)
- **Makefile: `audit`** — Target `make audit` pour pip-audit de sécurité (Round 20)
- **Tests: fixture `init_project`** — Fixture partagée dans conftest.py pour projets pré-initialisés (Round 20)
- **Tests: +8** — TestCompletionExport (3) + TestDoctorFixture (5) → 866 tests (Round 20)
- **pyproject.toml: marker `bench`** — Nouveau marker pytest pour les tests de performance (Round 20)
- **CLI: `--quiet` / `--no-color`** — Flags globaux pour le scripting et l'intégration CI (Round 19)
- **CLI: JSON output `doctor`** — Sortie JSON structurée pour `grimoire doctor` avec checks détaillés (Round 19)
- **CLI: JSON output `validate`** — Sortie JSON pour `grimoire validate` : `{valid, errors, count}` (Round 19)
- **CLI: JSON output `check`** — Sortie JSON pour `grimoire check` avec phases détaillées (Round 19)
- **Docs: section "JSON scripting"** — Tableau récapitulatif de toutes les commandes avec support JSON (Round 19)
- **Tests: +24** — TestDoctorJson (3) + TestValidateJson (3) + TestQuietNoColor (4) + TestCheck JSON (2) + TestSchema core (12) → 858 tests (Round 19)
- **CLI `grimoire schema`** — Export JSON Schema Draft 2020-12 pour `project-context.yaml` (validation IDE et CI) (Round 18)
- **CLI `grimoire check`** — Commande compound : lint + validate + structure check en une passe (Round 18)
- **Core: `__all__` exports** — Ajout de `__all__` dans `config.py`, `validator.py`, `project.py`, `schema.py` (Round 18)
- **Core: `schema.py`** — Nouveau module `grimoire.core.schema` : générateur JSON Schema depuis la structure config (Round 18)
- **Tests: +10** — TestSchema (5 cas) + TestCheck (5 cas) → 834 tests unitaires (Round 18)
- **CLI JSON output** — Sortie JSON (`-o json`) pour `status`, `registry list`, `registry search` (Round 17)
- **Ruff: +4 catégories** — Ajout FLY (f-string), FURB (refurb), RSE (raise), ERA (dead code) → 20 catégories
- **GitHub: Issue templates** — Ajout `docs-improvement.yml` et `performance-regression.yml`
- **Docs: ADR-002 SemVer** — Architecture Decision Record sur la politique de versionnage et stabilité API
- **SECURITY.md enrichi** — Classification de sévérité, processus de divulgation, scope, timeline
- **CLI `grimoire lint`** — Commande de lint YAML avancée : validation structure, types, contraintes et références (sortie text/JSON)
- **CLI `grimoire diff`** — Affiche le drift de config entre le projet et les défauts de l'archétype (sortie text/JSON)
- **CLI `init --dry-run`** — Flag `--dry-run` sur `grimoire init` pour prévisualiser sans écrire
- **GitHub: Release Drafter** — Workflow `release-drafter.yml` + config : génère automatiquement les notes de release à partir des PRs
- **GitHub: Stale issue closer** — Workflow `stale.yml` : ferme automatiquement les issues/PRs inactives (60j stale + 14j close)
- **GitHub: PR auto-labeler** — Workflow `auto-label.yml` + `labeler.yml` : labellise automatiquement les PRs selon les fichiers modifiés
- **Docs: API reference mkdocstrings** — Autodoc Python intégrée dans mkdocs via `mkdocstrings[python]`
- **Docs: Référence config** — Page `docs/config-reference.md` : toutes les clés, types, défauts, valeurs valides, variables d'environnement
- **Docs: Plugin Development Guide** — Page `docs/plugin-development.md` : création d'outils, backends, archétypes, entry points
- **mypy étendu aux tests** — `[[tool.mypy.overrides]]` pour tests/ avec relaxation `disallow_untyped_defs`
- **Ruff PERF102** — Fix `.items()` → `.values()` dans 2 fichiers de tests
- **Ruff: 3 catégories de règles** — Ajout PIE (misc), PERF (performance), LOG (logging) au linter
- **Public API enrichie** — `GrimoireProject` exporté dans `grimoire.__init__` aux côtés de `GrimoireConfig` et `GrimoireError`
- **Docs: Référence CLI** — Page `docs/cli-reference.md` : toutes les commandes, flags, options, variables d'environnement
- **Docs: FAQ** — Page `docs/faq.md` : installation, backends, agents, plugins, migration, dépannage
- **Tests lint** — 7 tests (no config, valid, JSON valid, JSON invalid, direct YAML, invalid config, help)
- **Tests init --dry-run** — 6 tests (plan affiché, aucun fichier créé, validation archetype/backend)
- **Tests diff** — 5 tests (no config, fresh project, JSON output, archetype, help)
- **CI: Dependency audit** — Job `pip-audit --strict --desc` dans `ci-sdk.yml` pour détecter les CVE dans les dépendances
- **CI: Codecov upload** — Upload automatique de `coverage.xml` vers Codecov avec `codecov-action@v5`
- **CI: Cross-platform** — Matrice étendue à `ubuntu-latest`, `windows-latest`, `macos-latest` (Win/Mac sur Python 3.12)
- **CI: SBOM CycloneDX** — Génération SBOM JSON (`sbom.cdx.json`) dans le workflow publish, attaché aux releases GitHub
- **CLI `grimoire config show`** — Commande lecture de config (YAML complet ou clé dot-notation) avec sortie text/JSON
- **CLI `grimoire completion install`** — Installation automatique shell completion (bash/zsh/fish)
- **Tests integration** — Nouveau répertoire `tests/integration/` avec 12 tests end-to-end (init→doctor, config show, env+plugins flow)
- **Tests config + completion** — 10 tests unitaires pour `config show` (dot-key, JSON, missing, help) et `completion install`
- **pyproject.toml URLs** — Ajout Changelog + Issues dans `[project.urls]` pour PyPI
- **`GrimoireConfig.validate()`** — Méthode de validation sémantique : détecte les incohérences config (backend sans URL, nom vide)
- **CLI `grimoire plugins list`** — Commande listant les plugins installés (tools + backends) avec sortie text/JSON
- **Doctor amélioré** — 3 nouveaux checks : validation config, dépendances optionnelles (qdrant/ollama/mcp), version Python
- **Tests config validation** — 6 tests pour `GrimoireConfig.validate()` (warnings qdrant, ollama, blank name, cas valides)
- **Tests env + plugins** — 12 tests pour `grimoire env` (text/JSON) et `grimoire plugins list` (text/JSON/mocked)
- **`__all__` corrigés** — CLI exporte `["app", "cli"]`, MCP exporte `["main"]`
- **README badges** — Badges Ruff + Mypy strict ajoutés

- **Plugin discovery** — Module `grimoire.registry.discovery` : `discover_tools()` / `discover_backends()` via `importlib.metadata` entry points
- **Error codes en production** — Tous les `raise GrimoireConfigError` assignent maintenant un `error_code` (GR001–GR003)
- **Tests CLI global flags** — 9 tests pour `--verbose`, `--log-format`, `--output` (mock + intégration)
- **Tests plugin discovery** — 6 tests pour `discover_tools()` / `discover_backends()` (chargement, erreurs, multiples)
- **API Reference** — Page `docs/api-reference.md` : GrimoireConfig, exceptions, logging, retry, plugins, error codes
- **Docs nav** — Section « Référence » dans mkdocs.yml avec API reference et Changelog
- **CI hardening** — `permissions: contents: read` ajouté au workflow `ci-sdk.yml`
- **CLI `--output`/`-o`** — Flag global `--output text|json` pour sortie machine-readable (implémenté sur `grimoire env`)
- **Rich markup mode** — `rich_markup_mode="rich"` activé dans le Typer app pour panel/markup dans `--help`
- **Plugin entry points** — `[project.entry-points."grimoire.tools"]` et `"grimoire.backends"` dans pyproject.toml
- **Feature request template** — `.github/ISSUE_TEMPLATE/feature-request.yml` (formulaire structuré)
- **FUNDING.yml** — Sponsor GitHub activé via `.github/FUNDING.yml`
- **Tests `@deprecated()`** — 11 tests : warning emission, version/alternative dans message, functools.wraps
- **Tests `error_codes`** — 27 tests : ErrorCode class, CODES registry, catégories, `__slots__`
- **Tests `configure_logging`** — 16 tests : niveaux, env vars, handler setup, JSONFormatter
- **Tests `@with_retry()`** — 11 tests : success/failure, backoff, jitter, préservation nom/retour
- **JSON logging** — `configure_logging(fmt="json")` + `JSONFormatter` pour logs structurés machine-readable
- **CLI `--log-format`** — Flag global `--log-format text|json` pour choisir le format de sortie des logs
- **CLI `grimoire env`** — Commande de diagnostic (version, OS, dépendances, projet) pour les bug reports
- **CLI error handler** — Gestionnaire global d'erreurs avec messages rich ; `GRIMOIRE_DEBUG=1` pour traceback complet
- **`@with_retry()`** — Décorateur retry avec backoff exponentiel + jitter dans `grimoire.core.retry`
- **Error codes** — Codes stables `GR0xx`–`GR5xx` dans `grimoire.core.error_codes` + attribut `error_code` sur `GrimoireError`
- **Shell completion** — Documentation dans README (bash/zsh/fish via Typer natif)
- **CLI `--verbose`/`-v`** — Flag global de verbosité intégré à `configure_logging()` (`-v` = INFO, `-vv` = DEBUG)
- **CodeQL/SAST** — Workflow GitHub Actions `codeql-analysis.yml` (scans hebdomadaires + PR)
- **CITATION.cff** — Fichier de citation académique CFF 1.2.0
- **`@deprecated()`** — Décorateur de dépréciation dans `grimoire.core.deprecation`
- **Branch coverage** — `branch = true` ajouté à la config coverage
- **Classifiers PyPI** — Ajout `Environment :: Console` et `Topic :: Scientific/Engineering :: Artificial Intelligence`
- **Logging centralisé** — `grimoire.core.log.configure_logging()` + env var `GRIMOIRE_LOG_LEVEL`
- **Exceptions** — `GrimoireTimeoutError`, `GrimoireNetworkError` dans la hiérarchie
- **`__all__`** — Exports explicites pour `cli`, `mcp`, `registry`, `exceptions`
- **`python -m grimoire`** — Support PEP 302 via `__main__.py`
- **DevContainer** — `.devcontainer/devcontainer.json` pour onboarding en 1 clic
- **MkDocs** — Site de documentation Material + workflow GitHub Pages
- **Pre-commit** — Enrichi avec mypy strict, yamllint, check-toml, large file check
- **CI** — Coverage enforced (`--cov-fail-under=70`), pip caching, artifact XML
- **Makefile** — 16 targets (lint, test, check, pre-push, docs, clean…)
- **Tests** — +143 tests scaffolding pour 11 outils non couverts

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.1.0] — 2026-03-11

### User Config Sync + HPE Parallel Execution + Architecture Doc

#### Ajouté

- **`grimoire setup`** — Nouvelle commande CLI pour synchroniser la configuration utilisateur
  (nom, langue, niveau) depuis `project-context.yaml` vers tous les fichiers de configuration.
  Modes : `--sync`, `--check` (CI-friendly), `--json`, overrides CLI (`--user`, `--lang`, `--skill-level`)
- **`grimoire-setup.py`** — Outil standalone (stdlib-only) pour la même synchronisation,
  utilisable sans pip via `grimoire.sh setup`
- **HPE — High-Performance Execution** — Moteur d'exécution parallèle pour les outils :
  `hpe-runner.py` (orchestrateur), `hpe-executors.py` (ThreadPool/ProcessPool/Async),
  `hpe-monitor.py` (métriques temps réel), `agent-task-system.py` (dispatch intelligent)
- **ARCHITECTURE.md** — Documentation détaillée de l'architecture du projet
- **Tests** — +3200 lignes de tests : `test_grimoire_setup.py` (50),
  `test_hpe_runner.py`, `test_hpe_executors.py`, `test_hpe_monitor.py`,
  `test_agent_task_system.py`
- **Archetypes bundled** — Les archetypes sont désormais inclus dans le wheel Python

#### Documentation

- `getting-started.md` — Ajout de `grimoire setup` + section "Configurer votre identité"
- `onboarding.md` — `grimoire setup` intégré dans le parcours J1
- `grimoire-yaml-reference.md` — Section "Synchronisation avec grimoire setup"
- Installation : `pipx` et `venv` documentés comme alternatives à `pip install` système

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.0.0] — 2026-03-08

### Réécriture complète — SDK Python pur + Indépendance totale

Le projet prend son indépendance sous le nom **Grimoire Kit** avec un **package Python installable**
(`pip install grimoire-kit`), architecture modulaire, API typée, et couverture de tests extensive.

#### Ajouté

- **SDK Core** (`grimoire.core`) — Modèles immutables (`@dataclass(frozen=True, slots=True)`),
  `GrimoireConfig` pour le chargement de `project-context.yaml`, résolution de chemins,
  système d'exceptions typées (`GrimoireConfigError`, `GrimoireProjectError`, `GrimoireRegistryError`)
- **CLI complète** (`grimoire.cli`) — 12 commandes Typer : `init`, `doctor`, `status`,
  `add`, `remove`, `validate`, `up`, `upgrade`, `merge`, `registry list`, `registry search`
- **MCP Server** (`grimoire.mcp`) — Intégration Model Context Protocol avec 6 tools
  et 4 resources pour les IDE compatibles MCP
- **Outils portés** (`grimoire.tools`) — `harmony-check`, `preflight-check`, `memory-lint`
  réécrits en modules Python avec API programmatique (`run()` / `RunResult`)
- **Système de registre** (`grimoire.registry`) — Résolution d'agents, workflows, tasks
  depuis les manifests CSV avec support multi-modules
- **Système de mémoire** (`grimoire.memory`) — Architecture à backends : fichier JSON,
  Ollama (embeddings), Qdrant (vector store) avec interface `MemoryBackend` abstraite
- **Archétypes** — 8 templates de projet : `web-app`, `creative-studio`, `fix-loop`,
  `infra-ops`, `meta`, `minimal`, `stack`, `features`
- **Merge engine** (`grimoire merge`) — Fusion intelligente de fichiers YAML/Markdown
  avec détection de conflits et dry-run
- **Upgrade engine** (`grimoire upgrade`) — Migration entre versions avec diff et backup
- **Documentation** — `getting-started.md`, `concepts.md`, `onboarding.md`,
  `memory-system.md`, `workflow-design-patterns.md`, `workflow-taxonomy.md`,
  `creating-agents.md`, `archetype-guide.md`, `vscode-setup.md`, `troubleshooting.md`
- **CI / Qualité** — 694 tests unitaires, 96% couverture, ruff lint, mypy strict,
  `py.typed` marker

#### Modifié

- **Rebranding complet** — Toutes les références de la marque d'origine renommées en `grimoire` dans le code source,
  tests, documentation, CI, shell scripts, et noms de répertoires (ancien layout → `_grimoire/`)
- **Entry points** — `grimoire` (CLI) et `grimoire-mcp` (serveur MCP) enregistrés
  dans `pyproject.toml`
- **Build** — Migration vers `hatchling` comme build backend
- **URLs** — Repo renommé en `Grimoire-kit`
- **MemoryManager** — Paramètre `project_root` explicite (déterministe, plus de `os.getcwd()`)
- **Atomic writes** — `LocalMemoryBackend._save()` utilise `tempfile` + `os.replace`

#### Supprimé

- Scripts shell standalone (remplacés par le SDK Python)
- Dépendance à `bash` pour l'exécution des outils
- Toute dépendance au package npm d'origine

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.1] — 2026-03-03

### Corrigé — Bug hunt (3 fichiers, 10 corrections)

- **cognitive-flywheel.py** — `cmd_analyze()` n'écrivait pas dans l'historique →
 la tendance (trend) restait toujours "stable" car `compute_score` n'avait
 jamais de cycle précédent. Ajout d'un `append_history()` à chaque analyse.
- **cognitive-flywheel.py** — Variable morte `high` dans `apply_gates()` :
 construite mais jamais utilisée dans le return (dead code supprimé).
- **tests/test_maintenance_advanced.py** — 6 appels `open()` sans
 `encoding="utf-8"` : crash potentiel sur Windows/locales non-UTF8.

### Vérifié — Aucun problème trouvé

- Division par zéro : 12 sites vérifiés, tous protégés (max, or, if guards)
- Pyflakes (F) : 0 erreur sur 48 outils + 53 tests
- Bare except : 0 (tous les except ont un type)
- eval/exec : 0 appel dangereux
- assert en production : 0
- Fonctions dupliquées : 0
- Mutable default args : 0
- Shadowing builtins : 0
- 82 swallowed-exception (`except ... pass`) : tous intentionnels (graceful degradation)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.0] — 2026-03-02

### Ajouté — Cross-pollination depuis zav-sandbox (GSANE)

- **cognitive-flywheel.py** (outil #47) — Boucle d'auto-amélioration continue :
 analyse Grimoire_TRACE.md pour détecter les patterns récurrents (failures,
 AC-FAIL), calcule un score de santé (A+ à D), génère des corrections
 automatiques avec système de gates (max 5 corrections, collision → escalade).
 6 commandes CLI : `analyze`, `report`, `apply`, `history`, `score`, `dashboard`
- **failure-museum.py** (outil #48) — Catalogue structuré des échecs :
 enregistre chaque failure avec root-cause, règle ajoutée, sévérité et tags.
 Persistance JSONL + sync markdown automatique.
 7 commandes CLI : `add`, `list`, `search`, `stats`, `export`, `lessons`, `check`
- **cleanup-branches.yml** — Workflow CI GitHub Actions pour supprimer
 automatiquement les branches mergées (protège main/develop/release/*)
- **tests/test_cognitive_flywheel.py** — 45 tests couvrant dataclasses,
 parsing trace, extraction de patterns, scoring, corrections, gates,
 persistence report/history, scoreboard, commandes, CLI, constantes
- **tests/test_failure_museum.py** — 43 tests couvrant dataclasses,
 persistence JSONL, markdown sync, commandes, CLI, intégration, constantes
- Total : **1 875 tests**, 0 échecs

### Inspiré par

- [zav-sandbox](https://github.com/zavrocKk/zav-sandbox) (framework GSANE) :
 Cognitive Flywheel, Failure Museum, branch cleanup CI

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.3.0] — 2026-03-02

### Ajouté — Couverture de tests complète (31 fichiers, +787 tests)

- **31 fichiers de tests** générés pour couvrir les 46 outils du framework :
 `bias-toolkit`, `context-guard`, `context-router`, `crescendo`, `crispr`,
 `dark-matter`, `dashboard`, `decision-log`, `desire-paths`, `digital-twin`,
 `distill`, `early-warning`, `harmony-check`, `immune-system`, `incubator`,
 `mirror-agent`, `mycelium`, `new-game-plus`, `nudge-engine`, `oracle`,
 `preflight-check`, `project-graph`, `quantum-branch`, `r-and-d`, `rosetta`,
 `self-healing`, `semantic-chain`, `sensory-buffer`, `swarm-consensus`,
 `time-travel`, `workflow-adapt`
- Chaque fichier teste : dataclasses, fonctions pures, fonctions projet,
 formats de sortie, constantes, parser CLI, intégration CLI
- **_gen_tests.py** — générateur automatique de tests par analyse AST
- Total : **1 787 tests**, 0 échecs, ~130 s d'exécution

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.1] — 2026-03-02

### Corrigé — Audit multi-cycles (15 cycles, 3 fichiers)

- **nso.py:323** — condition morte `status = "ok" if ... else "ok"` corrigée
 en `"warn"` — le NSO signale désormais correctement les erreurs memory-lint
- **gen-tests.py** — ajout `encoding="utf-8"` sur 2 appels `open()` (L167, L260)
 — évite les erreurs d'encodage sur Windows avec des fichiers contenant des
 accents/emojis
- **r-and-d.py:849** — ajout `encoding="utf-8"` sur `tool_file.open()` dans
 l'analyse de gap (même correctif portabilité Windows)

### Vérifié — Aucun problème trouvé

- Division par zéro : 15+ sites vérifiés, tous protégés par des gardes
- Regex : toutes les regex compilées valides
- Références croisées `_load_tool()` : 8 appels, tous vers des fichiers existants
- Aucun `open()` sans `with`, aucun chemin absolu hardcodé
- Aucune variable non-initialisée dans `finally`, aucun dict muté pendant itération
- Aucun import inutilisé, aucune variable morte (ruff F401/F841 clean)
- Chemins mémoire/output cohérents entre tous les outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.0] — 2026-03-02

### Corrigé — Fuites mémoire Python

- **r-and-d.py** — 5 correctifs mémoire :
 - `save_memory()` : ajout cap `MAX_MEMORY_SIZE = 500` — tronque aux N entrées
 les plus récentes au lieu de grossir indéfiniment
 - `_load_tool()` : cache via `sys.modules` — évite de recréer le module à chaque
 appel (14 exec_module/cycle → 1 par outil)
 - `load_cycle_reports()` : paramètre `last_n` — ne charge que les N derniers
 rapports au lieu de tout l'historique
 - `next_cycle_id()` : extraction directe depuis le nom du dernier fichier
 au lieu de charger et parser tous les rapports JSON
 - `tool_file.open()` L817 : ajout `with` context manager (file descriptor leak)
- **nso.py** — `_load_tool()` : même cache `sys.modules`
- **dream.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy
- **memory-lint.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.1] — 2026-03-01

### Supprimé

- **workflow-snippets.py** (389 lignes) — aucune intégration CLI, aucun test, aucune
 cross-référence. Overlap avec `workflow-design-patterns.md`
- **quorum.py** (400 lignes) — aucune intégration CLI, aucun test. Overlap fonctionnel
 avec `antifragile-score.py` (signaux SIL) et `stigmergy.py` (seuils phéromoniques)
- **confidence-scores.py** (572 lignes) — aucune intégration CLI, aucun test. Heuristiques
 simplistes, overlap avec `reasoning-stream.py` (niveaux de confiance)

### Corrigé

- Nettoyage des références aux 3 outils supprimés dans `docs/concepts.md`
- Total : **−1361 lignes** de dead code, 49 → 46 outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.0] — 2026-03-01

### Ajouté

- **CHANGELOG.md** — suivi formel des changements (issue R&D oracle-swot)
- **Multi-projet** pour `antifragile-score.py` — comparer la santé entre projets
 via `--multi-project dir1 dir2 ...`
- **Multi-projet** pour `dream.py` — croiser les insights entre projets
 via `--multi-project dir1 dir2 ...`
- **Moteur R&D v2.1** — filtre anti-chaîne de mutations + pénalité actionnabilité

### Corrigé

- Nettoyage du TODO orphelin dans le template prototype de `r-and-d.py`
- Moteur R&D : les mutations de mutations (profondeur > 1) sont progressivement
 pénalisées dans le challenge, réduisant le bruit combinatoire

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.0.0] — 2026-02-28

### Ajouté

- **r-and-d.py v2.0** — Moteur d'Innovation R&D avec apprentissage par renforcement
 - Closed-loop reward via health snapshots du projet réel
 - Challenge durci : GO threshold 0.60, CONDITIONAL 0.40, quota 20% rejet
 - Générateur de mutations des gagnants passés (transposition, escalade,
 inverse, fusion)
 - Générateur gap-driven (gaps réels : tests manquants, docs absentes,
 domaines sous-représentés, dépendances fragiles)
 - Commande `seed` pour initialiser la mémoire
 - Commande `health` — snapshot de santé du projet
 - Commande `prototype` — génération de squelettes Python
 - 13 sources de récolte (dream, oracle-swot, oracle-attract, early-warning,
 dna-drift, workflow-adapt, antifragile, harmony, stigmergy, incubator,
 synthetic, mutation, gap-analysis)

### Corrigé

- Déduplication inter-cycles dans le moteur R&D (idées recyclées filtrées)
- Générateur synthétique enrichi (21 templates de concept blending)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.6.0] — 2026-02-27

### Ajouté

- **Vague 6** — 7 outils d'exploration avancée :
 - `digital-twin.py` — simulation de l'écosystème projet
 - `quantum-branch.py` — exploration parallèle de décisions
 - `time-travel.py` — machine à remonter le temps projet
 - `crispr-rules.py` — mutation ciblée de règles agents
 - `decision-log.py` — journal structuré des décisions
 - `mirror-agent.py` — audit croisé inter-agents
 - `sensory-buffer.py` — tampon sensoriel entre sessions

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.5.0] — 2026-02-26

### Ajouté

- **Vague 5** — Dream Nervous System :
 - `dream.py` v2 — mémoire cross-session, décroissance temporelle, bigram keywords
 - Boucle fermée nervous system avec feedback loop et trigger intelligent
 - `memory-lint.py` — vérificateur d'hygiène mémoire
 - `nso.py` — orchestrateur du système nerveux

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.4.0] — 2026-02-25

### Ajouté

- **Vague 4** — Stigmergy :
 - `stigmergy.py` — coordination indirecte par phéromones numériques

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.3.0] — 2026-02-24

### Ajouté

- **Vague 3** — Cross-Project Migration + Agent Darwinism :
 - `cross-migrate.py` — migration d'artefacts entre projets
 - `agent-darwinism.py` — sélection naturelle des agents

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.2.0] — 2026-02-23

### Ajouté

- **Vague 2** — Anti-Fragile Score + Reasoning Stream :
 - `antifragile-score.py` — scoring de résilience adaptative
 - `reasoning-stream.py` — flux de raisonnement structuré

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.1.0] — 2026-02-22

### Ajouté

- **Vague 1** — Dream Mode + Adversarial Consensus :
 - `dream.py` — consolidation hors-session et insights émergents
 - `adversarial-consensus.py` — protocole de consensus adversarial

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.0.0] — 2026-02-20

### Ajouté

- Vagues précédentes : 25 outils de base, protocole cognitif Completion Contract,
 Modal Team Engine, Self-Improvement Loop, Vector DB, web-app archetype
- Architecture framework : agent-base, agent-rules, hooks, mémoire, sessions,
 outils, registre, équipes, workflows
- Archetypes : web-app, infra-ops, minimal, stack, meta, features, fix-loop
- Documentation : getting-started, archetype-guide, memory-system, troubleshooting,
 workflow-design-patterns, creating-agents
- Tests : smoke-test.sh + suite de tests Python (122 tests)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [0.1.0] — 2026-02-15

### Ajouté

- Initial commit — Grimoire Custom Kit structure de base
