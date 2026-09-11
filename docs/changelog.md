# Changelog

## Dernière release

### 3.44.1 — CLI et hook plus rapides au démarrage, flow status corrigé sur abandon

- **fix(flows): un abandon (ou un refus MAST) avant tout progrès n'affiche plus tous les nodes comme complétés dans `grimoire flow status` (#417).**
  `FlowEngine.status()` dérivait `completed_nodes`/`pending_nodes` de la
  seule position de `current_node` dans l'ordre topologique — sur un run
  ABORTED/REFUSED, cela rendait `completed_nodes == order` en entier même
  quand aucun node n'était réellement fait. `completed_nodes` reflète
  désormais les `completed_steps` du dernier checkpoint réel du
  `RuntimeKernel` ; comportement identique sous les deux backends
  (`GRIMOIRE_FLOWS_BACKEND=python|rust`).
- **perf(cli): sous-commandes chargées à la demande — `grimoire --version`
  313→89 ms, `grimoire doctor .` 448→270 ms (#418).** `LazyTyperGroup`
  remplace l'enregistrement direct des 20 modules `cmd_*` par un registre
  résolu à la demande ; aide identique au caractère près (`--help` vérifié
  avant/après pour les 47 commandes).
- **perf(hosts): `grimoire.hosts.decisions` découpé par décision —
  `grimoire-hook PreToolUse` 70→64 ms (#420).** Suite de mesure de #418 :
  ce point d'entrée ne passe jamais par le CLI Typer, donc pas concerné par
  le lazy-loading ci-dessus. Cible des 50 ms non atteinte pour
  `PreToolUse`, assumé — le profil restant tient à trois postes
  incompressibles (dataclasses/inspect, `standard_state`/`ruamel.yaml`,
  moteur de politique) ; suivi en issue #419.

## Releases précédentes

### 3.44.0 — Trois cœurs Rust optionnels de plus : hosts, flows, dispatch

- **`grimoire.hosts.collect`/`.surface` portés en Rust optionnel (#412).**
  Lecture du frontmatter d'agent et garde de distinction, bascule
  `GRIMOIRE_HOSTS_BACKEND=python|rust|auto`. L'oracle Rust a révélé un
  `ValueError` non rattrapé sur un chiffre Unicode non-ASCII dans
  `_max_turns` (`"²".isdigit()` vrai, `int()` refuse) — corrigé côté Python.
  Un verbe d'outil hors périmètre n'est plus silencieusement ignoré : une
  note nommant les jetons rejetés apparaît désormais sous les deux backends.
- **La machine à états du moteur de flows portée en Rust optionnel (#413).**
  Contrat de sortie, node courant, `resume()`, `status()`, transitions du
  kernel ; bascule `GRIMOIRE_FLOWS_BACKEND=python|rust|auto`. Divergence
  trouvée par l'oracle : `REFUSED` manquait aux statuts terminaux côté
  moteur de flows — un `resume()` sur un run refusé levait une erreur
  générique au lieu du message dédié. Corrigé, les deux backends s'accordent.
- **La cascade de dispatch et le routage de fournisseurs portés en Rust
  optionnel (#415).** Résolution de palier, rendu d'invocation, analyse de
  la sortie d'un ouvrier délégué, classification de revue ; bascule
  `GRIMOIRE_DISPATCH_BACKEND=python|rust|auto`. Deux défauts trouvés par
  l'oracle et corrigés côté Python : un coût JSON `true`/`false` était
  coercé en `1.0`/`0.0` (`isinstance(True, int)` vrai en Python) ; le JSON
  est désormais parsé strictement (RFC 8259), les jetons `NaN`/`Infinity`
  que `serde_json` refuse font désormais échouer les deux côtés à
  l'identique.
- Chacun des trois ports garde la roue publiée `py3-none-any` — les crates
  Rust ne quittent jamais `rust/`. Voir `CHANGELOG.md` pour le détail
  complet.

### 3.43.1 — Le validateur, doctor et concierge alignés ; le gate Rust enfin requis

- **Le validateur ne plante plus sur les champs énumérés (#409, #410).**
  Une liste ou une table sur un des neuf champs énumérés du schéma levait
  `TypeError: unhashable type` au lieu d'une erreur de validation propre ;
  cinq champs déclarés chaînes n'étaient en outre jamais type-vérifiés. Le
  cœur Rust servait déjà d'oracle correct ; parité stricte désormais testée
  entre les deux backends.
- **`doctor` ne confond plus un paquet npx/uvx/pipx/... avec un chemin
  (#393, #403).** `@playwright/mcp@0.0.80` ressemblait à un chemin selon
  l'heuristique et déclenchait un faux `path does not exist`.
- **Le concierge n'est plus jamais retenu comme porteur d'un skill proposé
  (#402, #406).** La persona d'entrée servait presque toujours de repli, donc
  presque toute proposition lui était attachée à tort — contraire à la
  doctrine (un skill s'attache à l'agent qui fait le travail).
- **`rust-gate` devient l'unique check requis pour les cœurs Rust (#407,
  #408).** Les anciens jobs, filtrés par chemin, pouvaient rester « attendus »
  indéfiniment sur une PR hors de leur périmètre.
- Voir `CHANGELOG.md` pour le détail complet, dont le script de mesure des
  gains Rust (#354, #404).


### 3.43.0 — Le système observe ses propres angles morts

- **Le concierge journalise ses non-choix (#389).** Symétrique du choix
  d'agent (#366) : quand aucun spécialiste ne correspond, ou qu'il se rabat
  sur un généraliste, `grimoire agent-miss` journalise catégorie, spécialité
  cherchée et agent de repli — jamais le contenu de la demande.
  `grimoire registry dispatches` affiche désormais les non-choix agrégés à
  côté des choix.
- **La répétition d'un non-choix propose un artefact (#395).** Le
  déclencheur lit ces non-choix et, passé un seuil configurable, écrit une
  proposition `pending` (agent ou skill selon la doctrine) — jamais de
  création sans acceptation explicite (`grimoire proposals accept/reject`),
  visible en CLI et dans le cockpit.
- **La fraîcheur des agents se signale, ne se retire jamais (#396, #398).**
  Un agent jamais dispatché depuis un seuil configurable est signalé par
  `grimoire doctor`, `grimoire registry dispatches` et le cockpit — jamais
  déprécié automatiquement.
- **Second port Rust, optionnel — le schéma et le validateur (#354, #392).**
  `grimoire.core.schema` et `grimoire.core.validator` basculent sur
  `GRIMOIRE_SCHEMA_BACKEND=python|rust|auto` ; le cœur Rust rejette des
  entrées que le validateur Python laissait passer sans erreur, sans roue
  publiée.
- Un correctif : la clé `proposals` manquait au schéma et au port Rust
  (#400), corrigé avant tout usage réel.

## Releases précédentes

### 3.42.0 — Le modèle hybride d'agent : skills attachées, doctrine, cockpit qui les gère

- **Doctrine de création d'artefact (#370) et contexte câblé sur le dispatch
  (#378).** `docs/artifact-doctrine.md` fixe quand un agent, un skill ou un
  prompt mérite d'exister ; le contexte qu'un agent déclare entre désormais
  dans le contrat de la tâche qu'il reçoit.
- **Cinq archétypes refaits en généraliste plus skills attachés** —
  `platform-engineering`, `creative-studio`, `meta`, `stack`, `web-app`/
  `fix-loop`/`minimal` et `infra-ops` (#375, #380) — plus un nouvel agent de
  sécurité offensive `security-auditor` (#357) et le retrait des manifestes
  d'équipe fantômes (#350).
- **Le cockpit gère les agents du projet.** Une seule commande de service
  ouverte sur le projet courant (#356), une table des agents avec skills et
  usage réel, assignables depuis l'inspecteur (#382).
- **Les skills se paient à l'usage, pas au tour de session (#377).** Une
  skill déclarée par un agent se replie dans le fichier de cet agent au lieu
  d'être émise pour tout le projet — mesuré à 0 token payé hors activation
  contre 1480/tour avant. Le fichier émis ne charge plus que le contexte
  déclaré (#379), et le choix de l'agent d'entrée est désormais tracé (#366).
- **Premier port Rust, optionnel — `grimoire.policies` (#354).** Un second
  cœur du moteur de règles, en Rust via PyO3, jamais publié sur PyPI et sans
  effet quand il n'est pas construit.
- Neuf correctifs, dont les écritures du cockpit qui répondaient 404 dès
  qu'un projet était sélectionné (#358) et `grimoire up` qui pouvait
  rétrograder silencieusement le profil du standard (#348). Voir
  `CHANGELOG.md` pour le détail complet.

## Releases précédentes

### 3.41.0 — Le dispatch choisit sa preuve, le flow conduit un node à la fois

- **Le dispatch choisit sa cascade selon ce qu'il faut prouver — épic
  routage par vérifiabilité (#307).** Chaque tâche dérive une classe de
  vérifiabilité (V0/V1/V2) de son contrat, qui pilote la cascade de
  `grimoire task dispatch` ; le palier de départ s'ajuste à l'historique des
  verdicts, la relisibilité et les incertitudes déclarées affinent la
  décision, et la politique qui en résulte est émise aux hôtes avec l'état
  des fournisseurs dès le `SessionStart`.
- **Les fournisseurs se pilotent au coût.** Registre étendu par palier de
  coût, `grimoire providers status` et `grimoire providers audit` pour
  sonder sans dépenser, choix de modèle qui croise reasoning et coût.
- **Le moteur de flow conduit un node à la fois (#204).** `grimoire flow
  run|status|resume|abort`, avec l'exécuteur par dispatch en cascade du
  routage par vérifiabilité branché dedans.
- **Garde-fous runtime et hôtes.** Plafonds de tours et de budget par
  instance, garde des échanges pair-à-pair, claims à fichiers exclusifs,
  sous-agents bornés.
- **Les écritures mémoire sont validées et la médiation d'outils vérifiée.**
  Contrat MCP annoté, contenu externe marqué comme tel.
- Export de traces OTel GenAI conforme, évals avec pass^k.

## Releases précédentes

### 3.40.0 — La tâche se souvient, l'éditeur comprend

- **Le claim rappelle ce que la mémoire sait de la tâche.** Historique,
  tâches voisines avec leur cause d'arrêt, mémoire consolidée : en CLI
  (`grimoire task recall`), en MCP (`task_recall`), au SessionStart et dans
  l'inspecteur d'Exécuter. La consolidation n'écrit qu'à la clôture ou au
  blocage.
- **L'éditeur Source comprend le langage du kit.** Colorisation, diagnostics
  (chemin mort, agent inconnu, clé de frontmatter inconnue, terme absent du
  glossaire, pattern ou workflow inconnu) et complétion, sans dépendance,
  adossés au doctor, au manifeste, au catalogue et au glossaire.
- **Le bridge du standard converge avec la norme.** Trente-six patterns
  rattachés au catalogue de la norme, cinq exigences obligatoires couvertes,
  plus aucun trou de N1 à N5.
- **Moins de code mort.** Onze accesseurs sans appelant retirés, trois
  branchés sur une vraie commande, un inventaire qui refuse le retour.
- Piloter distingue un kit aligné d'un kit installé.

## Releases précédentes

### 3.39.1 — Le glossaire sur une installation nue

- **La vue de travail s'ouvre sur `pip install grimoire-kit` sans extra.** Le
  glossaire importait PyYAML alors que la dépendance déclarée est ruamel ;
  les six espaces s'ouvraient avec une erreur sur une installation nue (#292,
  trouvé par le passage consommateur sur un projet réel). Chargeur du kit à la
  place, avec un test qui bloque `yaml`.
- Voir `CHANGELOG.md` pour le détail des autres changements de cette version.

## Releases précédentes

### 3.39.0 — La vue de travail

- **Une coque unique pour l'atelier et le cockpit.** Six espaces de travail,
  Piloter, Concevoir, Exécuter, Observer, Mémoire et Source, remplacent les
  pages d'outil ; les anciennes redirigent vers l'espace correspondant
  (`?legacy=1` pour y revenir). Panneaux repliés, entrouverts ou épinglés,
  raccourcis 1 à 5, mode concentration, palette ⌘K qui atteint projets,
  workflows, tâches, fichiers et commandes du kit.
- **Des infobulles qui expliquent le système.** Survol, Alt pour épingler,
  termes liés en bulles enfants, Échap ferme la pile ; une seule source, le
  glossaire du kit, dont la documentation est générée.
- **Source.** Les fichiers du projet par étage, kit généré, overrides et
  projections des hôtes ; éditer un fichier du kit crée l'override ; diff
  contre le catalogue de digests, provenance, historique. Une console qui
  n'exécute que les sous-commandes autorisées du kit, sans shell.
- **Des données réelles ou rien.** Le portefeuille lit les vrais champs, un
  état vide dit d'où viendra la donnée, la démo reste opt-in.
- **Palette Encre, cinq surfaces, sombre et clair**, contrastes mesurés par un
  test ; fontes Geist embarquées. Tests de navigateur Chromium en CI.

## Releases précédentes

### 3.38.0 — Les agents connaissent leur tâche, les gardes parlent, la campagne enforced est mesurée

- **Les agents lisent, réclament et clôturent leurs tâches.** Outils MCP
  `task_list_ready`, `task_show`, `task_claim`, `task_update`, `task_context`
  sur le même service que le CLI ; la tâche courante se résout depuis le claim
  actif du Mission Ledger ; `grimoire task trace <id>` rend la timeline unifiée
  d'une tâche, refus de policy et gates rouges compris.
- **Les gardes n'échouent plus en silence.** Une exception dans la politique
  de hook rend `ask`, un fichier de gates illisible ferme toutes les portes, un
  board stigmergique corrompu est mis de côté, un manifeste d'équipe cassé est
  nommé, le rendu des surfaces hôtes dit quand il échoue, et la CI ne porte
  plus d'étape qui ne peut pas échouer.
- **Six artefacts de plus pour la norme.** Registres d'acceptation, de
  rétention, d'outils, d'incidents, de capacités et matrice risques-contrôles :
  plus aucune exigence obligatoire sans artefact de N1 à N5.
- **Campagne enforced contre activated, pré-enregistrée.** Vingt-quatre runs
  par bras : zéro régression dure sous enforcement, l'artefact de preuve
  garanti, ni complétion ni test supplémentaire, 39 % de tours en plus.
- **Release plus honnête.** L'étage TestPyPI qui n'a jamais tourné est retiré
  au profit de `make wheel-check` ; chaque PR exécute la CI complète.

## Releases précédentes

### 3.37.0 — Le bridge trace la norme, Windows compte, l'identité se déclare

- **Le bridge du standard est tracé.** La révision de la norme est épinglée et
  `grimoire standard upstream` détecte quand elle avance ; `traceability.yaml`
  relie chaque artefact et chaque famille de vérificateurs aux exigences `AG-*`
  et contrôles `CTRL-*` avec citation, et `grimoire standard traceability` rend
  la matrice et les trous par niveau. Deux artefacts que la norme rend
  obligatoires sont livrés : le claim ledger (tous profils) et le registre des
  surfaces runtime (`governed`, `production`). Voir
  [Intégration du standard](standard/integration.md).
- **La persona d'entrée se choisit par projet** (`agents.entry`) ; un projet
  qui porte déjà son orchestrateur déclare `""`. Voir
  [Surfaces hôtes](hosts.md#persona-dentree).
- **`grimoire setup` écrit la source de vérité** qu'il déclare, puis vérifie les
  miroirs contre le fichier relu.
- **Windows est bloquant en CI.** Quarante-six outils ne meurent plus sur une
  console cp1252, le dernier rouge réel est corrigé, la jambe Windows des
  tests d'outils compte comme ubuntu.
- **Le garde de release vérifie que chaque changement fusionné a son entrée,
  au bon endroit** — le cas des trente-huit blocs égarés de la 3.36.0 ne peut
  plus se reproduire en silence.

## Historique complet

Consultez le [CHANGELOG complet](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md) sur GitHub.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
