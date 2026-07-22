# <img src="docs/assets/icons/chart.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

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
