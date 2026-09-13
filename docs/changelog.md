# Changelog

## Dernière release

### 3.48.0 — Moteur de flows complet (besoins, extraction, mesures, pilote de coût), gardes fail-open du framework fermées, Windows soutenu (grimoire.sh, install.sh)

- feat(flows): un node de flow déclare un besoin d'exécution (`{"run_need": "test-runner"}`) plutôt qu'une commande en dur — lot 2 de l'épic moteur de flows (#205). Catalogue fixe (`test-runner`, `lint`, `typecheck`, `build`, `migration-tool`, `format`, `grimoire.core.execution_needs`), résolu en deux temps au chargement du blueprint : `needs.commands` déclaré dans `project-context.yaml` (schéma/validateur/Rust en parité, `rust/grimoire-schema-core/`) l'emporte toujours sur la détection par marqueur de projet (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`). Un besoin non résolvable **refuse le chargement du blueprint entier** en le nommant, avant que le premier node soit présenté à l'hôte — jamais une installation qui échoue au troisième node. `grimoire needs resolve` affiche le verdict par besoin et sa source. Rétrocompatible : les blueprints à commandes en dur restent valides ; `grimoire flow run` avertit (jamais un refus) quand une commande en dur correspond à un besoin résolu pour ce projet. Aucun changement côté `rust/grimoire-flows-core/` : la résolution d'un besoin est une lecture disque (config + marqueurs), donc hors périmètre des fonctions pures déjà portées (`check_output_against_contract` ne voit que les pins, jamais l'acceptance).
- feat(flows): `grimoire flow extract <run-id> [--out <fichier>]` — un flow s'extrait d'un run, il ne se dessine pas (transversale T2, issue #210). Joint les métadonnées du run (`FlowRunMeta`) au Mission Ledger (`node_dispatch_history`) pour produire un blueprint brouillon dans le format `.blueprint.json` existant : l'acceptance d'un node **complété** par la cascade est remplacée par la commande réellement exécutée, réinférée en `{"run_need": ...}` sur correspondance caractère pour caractère avec un besoin résolu (#205) — jamais sur une correspondance partielle (une commande observée avec des arguments en plus reste `{"run": ...}` verbatim, deviner la coupure serait une invention). Un node **jamais dispatché** (classe V2, ou exécuté par un hôte interactif) garde son acceptance d'origine verbatim ; un node **jamais atteint** (run incomplet ou abandonné) est marqué `not_reached`. Chaque node du brouillon porte un objet `"extraction"` (`status`, `note`) traçant lequel de ces trois cas s'est produit — jamais une inférence depuis la prose. Le brouillon extrait est rejouable : `flow run`/`resume` le chargent comme n'importe quel blueprint et reproduisent la même séquence de nodes que le run source. Aucun changement côté `rust/grimoire-flows-core/` : l'extraction ne fait que lire `FlowRunMeta`/le Mission Ledger via l'API publique déjà existante de `FlowEngine` (`run_meta`, `status`), aucune nouvelle fonction pure candidate au portage.
- feat(flows): le flow porte sa preuve, le registre local l'affiche — lot 5 réduit de l'épic moteur de flows (#208). `TraceLedger.dispatch_outcome_stats()` gagne une troisième ventilation `by_flow` (en plus de `by_class`/`by_provider`, #442) : le préfixe `blueprint_id` du tag `replay:<blueprint_id>:<node_id>` qu'un node de flow porte déjà (#205/#210), jamais un dispatch hors flow (qui retombe sur l'id de tâche, sans `:`). `grimoire dispatch stats` affiche cette ventilation ; nouvelle commande `grimoire flow list [--require-measure <blueprint-id>]` — le registre local des flows (runs connus groupés par blueprint, dernier run, nœuds résolus/coût/escalade quand une mesure de dispatch existe) ; `--require-measure` refuse (sortie 1) si le blueprint désigné n'a aucune mesure. Calculé en Python dans les deux backends (jamais un sixième port Rust : une dimension de plus sur les mêmes enregistrements que `by_class`/`by_provider`, pas une fonction assez chaude pour le justifier) — vérifié : `to_dict()` reste identique entre `GRIMOIRE_TRACES_BACKEND=python` et `=rust` sur le corpus de parité. Pas de nouveau registre publié (#206 reste verrouillé) : une lecture locale, jamais un second calcul.
- feat(flows): le pilote — la fonction qui décide combien dépenser par node, pas une couche (transversale T1, issue #209). Nouveau module `flows/pilot.py` : `decide()` nomme et rassemble la décision déjà éparpillée entre `missions.dispatch.start_tier_for`/`_tier_chain` (inchangées, toujours pures, toujours portées Rust — `grimoire-dispatch-core`) et le refus V2 de `flows.dispatch_executor` ; une politique de projet optionnelle `_grimoire/standard/pilot.yaml` (`start_tier` par classe, `max_escalations`, `max_cost_usd_per_node`) la paramètre, absente elle ne change rien. `missions.dispatch.run_dispatch` gagne le mécanisme réellement neuf : un paramètre `max_cost_usd` qui arrête l'escalade entre deux paliers (jamais au milieu d'une tentative, jamais après un vert déjà acquis) avec un refus nommé (`DispatchReport.cost_capped`, même mécanique que `unrunnable` — aucun changement Rust requis). `DispatchExecutor` charge la politique une fois par run ; `--max-tier` explicite de la CLI reste prioritaire sur le plafond d'escalade de la politique. Aucune CLI nouvelle. Un `pilot.yaml` malformé refuse nommément le chargement plutôt que de retomber en silence (contrairement à `orchestration-policy.yaml` : un plafond de coût est un mécanisme de sécurité).
- fix(framework): cinq parseurs mémoire de `framework/tools/` avalaient `OSError`/`UnicodeDecodeError` et rendaient `[]`/`None`/`continue` — un fichier illisible (encodage étranger, verrouillé, tronqué) disparaissait du résumé, de la synchro Qdrant et de l'index RAG sans une ligne, `"errors": []` en tête. `SectionParser.parse_file` (`context-summarizer.py`), `MemoryParser.parse_file` (`memory-sync.py`) et `ChunkingStrategy.chunk_file` (`rag-indexer.py`) prennent désormais un `errors: list[str] | None` optionnel, nommant le fichier et la cause dans `report.errors` côté `summarize()`/`push()`/`index_collection()` (chemins déjà outillés d'un rapport). `ToolDiscoverer._inspect_python_tool`/`_inspect_shell_tool`/`_inspect_markdown_tool` (`tool-registry.py`) et le fallback fichier de `rag-retriever.py` (aucun rapport structuré sur ces chemins) écrivent la même information sur stderr. Diff net nul sur chacun des cinq fichiers (`framework/` gelé, shrink-only) : le garde anti-boucle est fusionné avec le `return`/`continue` d'origine sur la même ligne. (#264)
- fix(framework): un pheromone board corrompu de `framework/tools/` (`stigmergy.py` et `stigmergy_hooks/scripts/stigmergy_hook.py`) était remplacé par un board vide au dépôt suivant — `load_board` rendait un board vide sur `JSONDecodeError`/`OSError` sans le signaler, et `deposit_pheromone`/`save_board` écrasait ensuite le fichier corrompu avec un board ne contenant que le nouveau signal : une écriture tronquée (disque plein, deux hooks concurrents) détruisait tout l'historique stigmergique, `deposit_pheromone` rendant une `Pheromone` comme si de rien n'était. `load_board` met désormais le fichier corrompu de côté (`<nom>.corrupt-<horodatage>`) avant de rendre un board vide, et l'annonce sur stderr. La copie `src/grimoire/tools/stigmergy.py` a le même correctif depuis #270 ; celle-ci porte le correctif dans `framework/` (gelé, shrink-only) à diff net négatif sur les deux fichiers (garde compacté sur la même ligne que le `return`, plus deux signatures et un appel resserrés sur une ligne). (#265)
- fix(cli): `grimoire.sh help` bloquait indéfiniment (timeout 30s) sous Git Bash Windows — `find_project_root()` remontait l'arborescence via `dirname` jusqu'à `/`, mais sous Git Bash Windows `dirname` peut cesser de progresser (ex. se stabiliser sur `.`) avant d'atteindre `/`, transformant la boucle en boucle infinie. La boucle s'arrête désormais dès que `dirname` ne progresse plus, en plus de la condition d'arrêt à `/`. `TestGrimoireShRouting` repasse de `@requires_bash_posix_only` à `@requires_bash` (couverte sous Windows) (#461).
- fix(tests): `TestInstallSh::test_install_sh_has_shebang` levait un `UnicodeDecodeError` cp1252 sous Windows — `INSTALL_SH.read_text()` sans `encoding=` explicite décodait le fichier (légitimement UTF-8, avec des caractères accentués) selon la locale par défaut de Windows plutôt qu'en UTF-8 (même famille que #192). `read_text(encoding="utf-8")` corrige le test ; `install.sh` lui-même n'a pas changé. `TestInstallSh` repasse de `@requires_bash_posix_only` à `@requires_bash` ; `requires_bash_posix_only` n'ayant plus d'utilisateur (dernier hors-scope #231 traité), il est retiré (#462).

## Releases précédentes

### 3.47.0 — Premier contact exécutable (wizard, cadrage, création de projet), hôtes déclarés, budget de session durci, Windows soutenu

- feat(cockpit): le wizard de setup (espace Piloter) exécute réellement le projet au lieu d'écrire une commande à copier-coller — `POST /api/setup` appelle `grimoire up` en direct (`cmd_up.run_up_pipeline`, jamais un sous-processus), needs transmis (B3 rebranché sur B2, `GET /api/needs`), refus fail-closed avant toute écriture (archétype/backend/need inconnu, chemin non inscriptible), rapport d'exécution persistant (`_grimoire/setup-run.json`, doctor compris) affiché dans la fiche projet. `_select_cwd_project` honore désormais un `--project-root` explicite même sur un dossier vierge (sans quoi aucune écriture n'était jamais possible sur le cas que l'issue décrit) ; le mode « copier-coller la commande » reste un repli explicite (#171).
- feat(standard): le need `project-discovery` scaffolde réellement `_grimoire/cadrage/` par appel de code direct (`cmd_up.py::_step_cadrage`), plus par une simple suggestion textuelle de `needs_suggest.py`. Sa complétude devient un contrôle du standard agentique (`_verify_cadrage`, dimension `artifacts`) visible dans `standard verify`/`gate`/`score`/`audit` : `info`/`warning` par défaut, `error` sur les phases gate (exigences, cahier des charges) uniquement quand `project-discovery` a été explicitement choisi. Documentation dédiée `docs/cadrage.md`, référencée depuis le README et `docs/cli-reference.md` (#173).
- feat(cockpit): « nouveau projet » depuis le portefeuille (espace Piloter, niveau Flotte) — un bouton ouvre le même formulaire que le wizard de setup (#171) sur un chemin choisi ; `POST /api/projects/create` (cmd_cockpit.py) exécute le plan sur ce chemin (chaîne `execute_setup_plan`/`grimoire up`, comme `/api/setup`), refuse un chemin hors des racines permises ou déjà un projet Grimoire, puis enregistre le projet dans le registre — `grimoire up`/`init` l'enrôlent comme n'importe quel autre. Le sélecteur de projets (le tableau Flotte) le montre aussitôt, sans redémarrer le cockpit. Point 1 (API réelle de lecture) déjà livré par #143 ; l'agrégation mémoire multi-projets (point 3) reste hors périmètre — toujours des lectures projet par projet (#172).
- feat(hosts): déclarer les hôtes activés (`hosts.enabled` dans `project-context.yaml`, schéma/validateur/Rust en parité), détection filesystem par défaut quand la clé est absente (`claude` seul si rien n'est détecté), `grimoire init` écrit la clé avec le résultat de la détection. `grimoire host sync`/`up` n'émettent plus que les hôtes activés ; un `--host <x>` désactivé est un refus nommé sauf `--force-host` ; `grimoire host status --host all` liste les fichiers orphelins d'un hôte désactivé, `grimoire host sync --prune-disabled` les retire (opt-in). `grimoire doctor` ajoute INFO (orphelins) et WARN (hôte activé sans fichier émis). Alternative « canal plugin » Claude Code écartée par décision (#177).
- fix(policies): quatre défauts de conception de la règle de budget de session, révélés le 2026-09-12 par un incident réel sur la Forge (`per_session: {max_writes: 800, max_tool_calls: 3000}` sans `tool_pattern` a fini par refuser tous les outils de la session, y compris l'édition du fichier de règles qui la portait). (1) `max_writes` comptait tout appel `Bash` portant une commande, lectures comprises (`cat`, `grep`, `find`, `git status`...) ; `classify_tool` distingue désormais un `Bash` de lecture seule (`grimoire.hosts.decisions.tool_facts.is_read_only_command`), conservateur par construction. (2) Une règle `per_session` en `block` n'atteint plus jamais un appel non-mutant ni une écriture ciblant `_grimoire/standard/policies.yaml`/`_grimoire-output/.runs/session-*.json` — exemption de réparation codée en dur, miroir Rust compris (`rust/grimoire-policies-core`). (3) `per_session.subagents` (`shared`/`separate`) nomme le partage de budget entre une session et ses sous-agents (le payload de hook de Claude Code ne les distingue pas) ; `separate` échoue au chargement (`GR-POL-003`) plutôt que de simuler une isolation absente. (4) `grimoire policies reset-session [--session-id]` supprime l'état d'une session, `grimoire policies status` avertit sous 10 % de plafond restant, et le message de refus d'un budget nomme désormais cette commande et le fichier de règles. `grimoire doctor` signale (`WARN`) toute règle `per_session` sans `tool_pattern` en `verdict_on_match: block` (#463).
- fix(cli): sous Windows, `grimoire-init.sh reset`/`uninstall`/`quick-update` restaient non prouvés -- les seize (puis vingt-deux) tests correspondants etaient satures depuis #256 sans jamais avoir tourne verts. La vraie cause n'etait pas les commandes elles-memes (elles repondent en <1s une fois invoquees) mais le harnais de test : `subprocess.run(..., timeout=30)` tue bien le `bash` direct a l'expiration, puis redraine les tubes sans timeout -- si un petit-fils Windows (`cp.exe`, `mkdir.exe`...) tenait encore le tube stdout/stderr ouvert, ce second appel bloquait indefiniment le job jusqu'a son plafond. `_run()` tue desormais l'arbre de processus complet (`taskkill /T /F`) avant de redrainer. Les vingt-deux tests des trois commandes tournent et passent sous `windows-latest` (matrice restauree, job bloquant conserve) ; `grimoire.sh help` et `install.sh` restent hors scope avec leurs propres bugs Windows distincts (#461, #462) (#231).

### 3.46.1 — Correctifs trouvés en conditions réelles et par le triage

- feat(cli): `grimoire init` et `grimoire up` enrôlaient chaque projet dans le registre cockpit réel (`~/.grimoire/cockpit/registry.json`), même les jetables (`/tmp`, un scratchpad, une recette) — seule la variable d'environnement non documentée `GRIMOIRE_NO_COCKPIT` pouvait l'éviter, et rien ne la mentionnait à côté des options des deux commandes. Ajoute `--no-cockpit` à `init` et à `up` (même effet que la variable, documentée au même endroit dans `--help`) (#305).
- fix(cli): `grimoire up` annonçait `refresh: done` alors que `host sync --dry-run` trouvait encore des dizaines de fichiers à écrire pour Claude Code, Copilot, Codex, Cursor et Gemini CLI juste après — `refresh` ne régénère que le tier kit (`_grimoire/kit/`), pas les surfaces hôtes qui en découlent (chemin de code séparé, `grimoire.hosts.emitters`). `up` exécute désormais une étape `host_sync` après `refresh` et `standard` qui appelle la même synchronisation que `grimoire host sync --host all` ; `host sync --dry-run` exécuté juste après `up` ne trouve plus rien à écrire (#296).
- fix(cli): un projet qui déclare déjà l'archétype `agentic-standard` restait en FAIL permanent après `grimoire up` — le profil `starter` (choix par défaut sans `--needs`) ne couvre pas les acceptance criteria hard de la DNA de cet archétype (`llm-provider-registry.yaml`, `compliance-declaration.md`). `up` résout désormais le need `provider-neutral` (-> profil `controlled`) par défaut quand cet archétype est déjà déclaré ; `knowledge-source-registry.yaml`, écrit seulement à partir du profil `orchestrated`, rejoint les chemins « déclarés avant usage récurrent » que `doctor` ne réclame plus tant qu'aucune source n'est indexée (#295).
- fix(cli): `grimoire doctor -o json .` (l'option après le sous-commande, telle que documentée par `doctor --help`) échouait avec « No such option: -o » — seule la forme globale `grimoire -o json doctor .` fonctionnait. `doctor` accepte désormais un `--output`/`-o` local (même contrat que le `-o` global : mêmes contrôles, même structure JSON) (#293).
- fix(policies): `tool_pattern` des politiques temporelles n'était comparé qu'au nom nu de l'outil, si bien qu'un motif documenté comme `Bash(git push:*)` ou `Bash(rm:*)` ne matchait jamais — `require_approval: true` répondait `allow` dès le premier appel. Introduit une clé d'outil façon permissions Claude Code (`Bash(<commande complète>)`, `<Tool>(<file_path>)`) contre laquelle le motif parenthésé est désormais comparé, rétro-compatible avec les motifs nus (`"*"`, `"Bash"`) (#449).
- fix(core): déclarer `source.assist` (`model`, `allow_lan`) dans le schéma et le validateur, Python et Rust, pour que `grimoire check .` accepte l'exemple documenté de l'assistant Source au lieu de refuser `source` comme clé inconnue (#451).
- fix(cockpit): l'assistant Source distingue le chargement du modèle local d'un dépassement de délai — `GET /api/workspace/assist` sonde `/api/ps`, déclenche lui-même le chargement (`keep_alive`, sans générer) et répond « chargement du modèle » ; le bouton **Suggérer** reste visible mais désactivé et se re-sonde toutes les 3 s au lieu d'échouer au premier clic (#450).

### 3.46.0 — Politiques temporelles et overrides partiels, coût et pass^k dans les gates, timeline et assistant cockpit, pont MCP à jour

- **feat(policies): politiques temporelles par session sur la médiation d'outils — budgets, approbation préalable, refroidissement (#439).**
  Point 3 de l'audit de positionnement 2026-09-12 : `PolicyRule` gagne quatre
  clés optionnelles et rétrocompatibles (`tool_pattern`, `require_approval`,
  `per_session`, `cooldown_after`, voir `_grimoire/standard/policies.yaml`),
  validées au chargement (`GrimoirePolicyError` nommée sur clé inconnue). L'état
  de session (compteurs, approbations, horodatages — jamais de secret ni de
  contenu d'outil) vit dans `_grimoire-output/.runs/session-<id>.json`, écrit
  atomiquement, remis à zéro à `SessionStart` ; un fichier absent ou corrompu
  redevient une session neuve. La décision pure (règle + état → verdict) est
  portée à l'identique en Python (`grimoire.policies.temporal`) et en Rust
  (`rust/grimoire-policies-core`, `evaluate_temporal`), testée en parité ; le
  hook `PreToolUse` reste sous +5 ms de surcoût mesuré. `grimoire policies
  status` affiche les compteurs et budgets restants de la session — le
  cockpit n'est pas dans ce lot.

- **feat(hosts): un override d'agent peut désormais rester partiel (`extends: kit`) et signale sa dérive au lieu de figer silencieusement une copie (#427).**
  Migration réelle 3.38.0 → 3.44.2 : quatre overrides en copie intégrale
  n'avaient plus reçu une seule mise à niveau de leur agent depuis des mois,
  `doctor` étant 22/22. Trois changements : (1) tout override écrit par un
  chemin qui comprend le kit (cockpit, `grimoire agent override convert`)
  enregistre `kit_source_hash:` (empreinte tronquée du fichier kit au moment
  de l'écriture) ; `doctor` et le cockpit comparent cette empreinte à
  l'actuelle et signalent en WARN (jamais FAIL) une dérive, avec un résumé
  (sections de frontmatter ajoutées/retirées, delta du corps, ou champs
  figés pour un override partiel), et en INFO une empreinte inconnue
  (override antérieur à cette issue). (2) `extends: kit` dans le frontmatter
  d'un override ne redéfinit plus que les champs qu'il liste
  (`model_affinity`, `context`, `skills`, `tools`, `use_when`,
  `dont_use_when`, `max_turns`, `description`, `tool_boundary`) — le corps et
  le reste du frontmatter viennent du fichier kit de même nom, fusionnés
  dans `hosts/collect.py` (dicts Python, avant tout appel au port Rust
  optionnel — parité inchangée sous `GRIMOIRE_HOSTS_BACKEND=rust`) ; un
  `extends: kit` sans agent kit de même nom refuse au chargement, nommant
  l'agent. Le cockpit (assigner un skill, éditer une clause) écrit désormais
  un override partiel dès qu'un agent kit du même nom existe, une copie
  intégrale sinon. (3) `grimoire up` liste, après avoir rafraîchi le palier
  kit, les overrides à revoir — sans jamais les toucher — et
  `grimoire agent override convert <nom> [--dry-run]` convertit une copie
  intégrale en override partiel équivalent, refusant (lignes citées) quand
  le corps de la copie a divergé du kit plutôt que de fusionner du texte.

- **feat(dispatch): coût par tâche résolue et pass^k, comptabilité continue et contrôle dans les gates (#442).**
  Point 5 de l'audit de positionnement du 2026-09-12 : le kit dispatchait déjà des tâches en cascade sans jamais agréger en continu ce que ça coûte ni si ça marche de façon fiable — seules des campagnes d'évals manuelles (#308) répondaient à ces questions. `grimoire task dispatch`/`grimoire flow run --executor dispatch` écrivent maintenant un événement `dispatch.outcome` par cascade réellement tentée dans le journal de traces (classe, paliers tentés, coût total, verdict d'acceptance, résolu ou non — aucun contenu de prompt). Nouvelle commande `grimoire dispatch stats [--since 30d] [--json]`, sœur de `providers history` : coût par tâche résolue, taux d'escalade, part d'inexécutable (par classe et par fournisseur), et pass^k sur les nœuds rejoués (une série est « toute au vert » seulement si toutes ses exécutions ont résolu la tâche). Les agrégations pures vivent dans `rust/grimoire-traces-core/` (extension du sixième port), avec parité de test sous les deux backends. Le standard gagne le contrôle `dispatch.cost_slo` (pattern `provider-cost-slo`) : `INFO` faute de données, `WARN` en cas de dépassement (coût ou pass^k), `FAIL` uniquement si le projet déclare `dispatch_cost_slo.enforce: true` — jamais bloquant par défaut. Le cockpit n'est pas concerné par ce lot.

- **feat(cockpit): timeline unifiée par tâche dans la vue de travail, l'export OTel de #322 devient une source lue plutôt qu'un mécanisme mort (#139).**
  Audit de positionnement du 2026-09-12, point 2 : `TraceLedger.export_otel_jsonl`
  produit des spans GenAI conformes depuis #322, mais rien ne les consommait —
  `/api/otel` sert une pile d'événements différente (`blueprint_telemetry`,
  `events.jsonl`), sans rapport avec le TraceLedger. `grimoire.missions.trace`
  (déjà livré par #276) gagne une cinquième source, `otel` : si un export
  existe à l'emplacement conventionnel (`<traces>/otel-export.jsonl`), ses
  spans sont lus et corrélés par `grimoire.task_id` puis par `traceId`
  partagé avec les spans enfants — jamais par heuristique textuelle. Deux
  autres traces disparaissaient aussi en silence de la timeline avant ce
  correctif : les dispatchs d'agent (`agent.dispatch`, `agent.miss`) et tout
  futur fait du TraceLedger qui n'est ni un gate ni un appel d'outil — un
  repli générique les reprend désormais sous la source `hooks`. Côté cockpit :
  l'espace Exécuter (`web/workspace/spaces/executer.js`) ouvre la timeline
  d'une tâche depuis sa carte (bouton « Voir la timeline »), la filtre par
  source et par gravité, et détaille chaque ligne en accordéon ; l'espace
  Observer (`observer.js`) y renvoie depuis un span qui porte
  `grimoire.task_id`. Lecture seule (ADR-007) : aucune écriture, aucune
  reconstruction d'événement absent — une tâche sans trace montre « aucun
  événement » et nomme les sources lues. Tests : `tests/unit/missions/test_trace.py`
  (corrélation par identifiants, dispatch non perdu, otel présent/absent),
  `tests/unit/test_workspace_api.py`, `tests/e2e/test_workspace_lot4_spaces.py`
  (dispatch et transition refusée visibles, filtre par source). Docs :
  `docs/cli-reference.md`, `docs/serve-blueprints.md`,
  `docs/audits/positionnement-2026-09-12.md` et
  `framework/agentic-industry-reference.md` (section 10) mis à jour avec la
  date de correction.

- **feat(cockpit): suggestions de contenu par un petit modèle local (Ollama), toujours derrière l'IntelliSense déterministe de l'espace Source, jamais à sa place (#280).**
  Voie 2 de #280, derrière la voie 1 (IntelliSense déterministe, PR #303) :
  `project-context.yaml: source.assist.model` (vide par défaut, opt-in) plus
  la même sonde qu'`grimoire providers audit` (`GET /api/tags`) décident si
  le bouton **Suggérer** de l'éditeur Source apparaît — sinon l'interface ne
  montre rien et ne tente rien. `GET /api/workspace/assist` (sans coût) rend
  ce statut ; `POST /api/workspace/assist` (`src/grimoire/tools/source_assist.py`,
  projet d'accueil seulement) appelle réellement le modèle en local
  (`http://127.0.0.1:11434`, délai borné à 10 s), avec les identifiants du
  paquet de langage (agents, skills, workflows, patterns) injectés dans le
  prompt, et vérifie après coup tout identifiant cité par la réponse contre
  ce même paquet — marqué « inconnu » plutôt que corrigé à la place de
  l'utilisateur. Panneau d'aperçu dans l'éditeur (`Ctrl+Maj+Espace`, ou le
  bouton) avec **Insérer**/**Ignorer** ; l'insertion passe par le même
  chemin que la frappe clavier, la colorisation et les diagnostics se
  recalculent dessus. Aucun fournisseur distant, aucune clé, aucune écriture
  de fichier par la route. Garde de relecture : une URL Ollama résolue
  (`OLLAMA_HOST`) hors bouclage (`127.0.0.1`, `::1`, `localhost`) est
  refusée par défaut — `source.assist.allow_lan: true` l'autorise
  explicitement.

- **feat(mcp): migrer le pont MCP vers la révision de protocole 2026-07-28 (#436).**
  Le plancher `mcp>=1.10,<3` laissait un résolveur retenir un SDK qui plafonne
  à la révision 2025-11-25 (pas de `server/discover`, pas de mode sans état).
  Relevé à `mcp>=2.0,<3` : à partir de 2.0.0, le SDK négocie 2026-07-28 par
  défaut (`server/discover`, auto-dérivé des outils/prompts/ressources
  enregistrés) tout en servant encore, sur la même connexion, un hôte qui ne
  connaît que le handshake `initialize` (2025-06-18, 2025-11-25) —
  `serve_dual_era_loop` côté SDK. Aucune ligne du pont
  (`src/grimoire/mcp/server.py`) n'a dû changer : il ne câblait déjà ni
  Roots, ni Sampling, ni Logging (les trois fonctionnalités que 2026-07-28
  déprécie, retrait possible à partir de 2027-07-28), et ne garde aucun état
  entre deux appels d'outil en dehors des fichiers du projet ciblé. Nouveaux
  tests (`tests/unit/mcp/test_protocol_revision.py`) qui pilotent un vrai
  `ClientSession` sur des flux en mémoire : négociation 2026-07-28 par
  `server/discover`, compatibilité `initialize` à 2025-06-18 et 2025-11-25,
  et absence d'état partagé entre deux connexions successives. Documentation
  (`docs/mcp-integration.md`) et carte de correspondance
  (`framework/agentic-industry-reference.md`, section 10) mises à jour.
  Hors périmètre : transport HTTP/SSE, authentification, exposition réseau
  distante — le pont reste stdio, en local.

- fix(yaml): `grimoire upgrade` round-trippait `project-context.yaml` via un chargeur `safe` (aucune métadonnée de commentaire) puis un dumper round-trip — tous les commentaires du fichier disparaissaient silencieusement à chaque migration v2→v3 (#430).

- fix(flows): `grimoire.runtime.kernel.create_instance` tronquait silencieusement `recipe_id`/`blueprint_id` aux 16 derniers caractères pour construire `wfi_id`/`run_id` (`WFI-...`) — deux blueprints partageant ce suffixe (par ex. `tasklib-hardening` perdait déjà son premier caractère) pouvaient obtenir le même `run_id` sur des kernels indépendants. L'identifiant complet est gardé tant qu'il tient dans une borne large (64 caractères) ; au-delà, il est raccourci et désambiguïsé par une empreinte de 8 hex de `sha256(recipe_id)` plutôt qu'une simple coupe. Les runs déjà persistés sous l'ancien format restent lisibles par `flow status`/`flow list` (#446).

### 3.45.0 — Le gate de dispatch exécute l'acceptance, board du cockpit, sixième port Rust

- **fix(flows): le gate de `flow run --executor dispatch` exécute l'acceptance structurée d'un node, pas seulement l'enveloppe (#428).**
  Rejeu réel du 2026-09-11 (épic #307, lot 3) : un nœud V0 déclaré vert alors
  que sa vraie suite `pytest` ne pouvait pas être collectée (dépendance
  absente) — le gate ne vérifiait que la conformité de l'enveloppe JSON de
  l'ouvrier au contrat de sortie, jamais le texte de l'acceptance. `node.acceptance`
  accepte désormais, en plus du texte libre (rétrocompatible), une forme
  structurée exécutable (`{"run": "...", "expect_exit": 0, "cwd": ".",
  "timeout_s": 120, "expect_stdout_contains": "..."}`) ou une evidence
  mécanique (`{"path_exists": "..."}`, `{"test": "..."}`) — validée au
  chargement du blueprint (`blueprint_loader.py`), refus nommé sur une forme
  inconnue. Le gate (`missions/dispatch.py`) exécute ces commandes après la
  réponse de l'ouvrier et rend trois verdicts, jamais un quatrième :
  exécutée-verte (succès), exécutée-rouge (échec, suit la cascade normale :
  réessai/escalade), ou **acceptance inexécutable** (refus nommé — binaire
  absent, `pytest` 5/4, erreur d'import dans la sortie — qui arrête la
  cascade net, jamais un succès). `flow status` et le rapport de dispatch
  montrent par nœud le statut d'acceptance (exécutée/inexécutable/jugée) et
  la sortie tronquée à 2 Ko. **La classe V0 exige une acceptance
  structurée** : un nœud dont le texte seul le classerait V0 mais qui n'en
  déclare aucune est rétrogradé en V1 (cascade démarrant à `mid`, jamais
  fermé sur la seule enveloppe — marqué à relire), avec un avertissement
  nommé posé au chargement du blueprint et transmis à la fois au démarrage
  de la cascade et au rapport, pour que `flow run` et `flow status`
  s'accordent sur la même classe. Un nœud V1 **déclaré** (vocabulaire de
  revue) garde le comportement actuel du kit, documenté sans être étendu.
  Aucun changement côté port Rust des flows (`rust/grimoire-flows-core/`) :
  seule `NodeContract.outputs` (pins) y traverse la frontière PyO3, inchangée
  par ce correctif.

- **feat(cockpit): le board de l'espace Exécuter montre le corps réel d'une tâche (description, garde-fous, dépendances) et les commandes d'intention restent gated par la preuve en multi-projet (#140).**
  L'inspecteur de tâche affichait déjà les critères d'acceptation et les
  preuves attendues ; il gagne des blocs Description, Garde-fous et
  Dépendances, tirés du `MissionTask` du ledger — ce que `task-board.yaml`
  n'a jamais su porter (ADR-005). `kanban.html` reste la vitrine statique et
  gagne un lien vers le board vivant. Aucune écriture nouvelle : les
  commandes de transition, le gate de preuve et la restriction au projet de
  lancement du cockpit existaient déjà ; ce lot les couvre par des tests
  dédiés (unitaires sur la route `/api/workspace/tasks/<id>/<action>` —
  succès, refus nommant l'artefact manquant, refus hors projet de lancement —
  et navigateur sur le critère d'acceptation à la lettre : une carte visée
  vers *review* sans evidence pack est refusée avec l'artefact nommé, et le
  board change quand on change de projet).

- feat(traces): sixième port Rust optionnel du cœur du système d'artefact émergent — les agrégations du journal de traces (`TraceLedger.agent_dispatch_counts`/`.agent_miss_counts`/`.oldest_started_at`), la règle de fraîcheur (`compute_agent_freshness`, issue #396) et le déclencheur de propositions d'artefact (`grimoire.proposals` : nommage mécanique, résolution du porteur issue #402, décision de synchronisation seuil/refus issue #395/#394/#389) (issue #354). `rust/grimoire-traces-core/`, bascule `GRIMOIRE_TRACES_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.traces.ledger` et `grimoire.proposals`, qui ne s'importent pas l'un l'autre), jobs CI `rust-traces / cargo` et `rust-traces / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Défaut trouvé par l'oracle Rust et corrigé dans cette même PR : `grimoire.traces.ledger._parse_iso` attrapait `ValueError` mais pas la `TypeError` non rattrapée levée ensuite par `datetime.now(tz=UTC) - datetime.fromisoformat(...)` sur un horodatage ISO-8601 *naïf* (sans décalage) — un journal JSONL édité à la main peut en porter un, et `compute_agent_freshness` revendique explicitement ne jamais lever sur un journal arbitraire ; corrigé en traitant un horodatage naïf comme UTC des deux côtés (même correctif que `grimoire_traces_core`, qui n'a jamais eu ce trou). Invariants de la doctrine rendus impossibles à violer par construction (clampés/exclus dans la fonction de décision elle-même, jamais par un appelant qui pourrait l'oublier) : seuil de proposition jamais sous 2, aucune proposition au premier non-choix, une proposition refusée ne revient que si son compte a doublé depuis le refus, la persona d'entrée est exclue de la recherche de porteur avant même de lire son `use_when`, le match de catégorie se fait par mot entier (limite de mot Unicode) et non par sous-chaîne. Corpus fixture de dix journaux JSONL réalistes (`tests/fixtures/traces_ledgers/`) et fuzz léger (200 enregistrements aléatoires) prouvant qu'aucune des fonctions d'agrégation/fraîcheur ne lève jamais, sous aucun backend, et qu'un champ de contenu libre (`prompt`/`request`) écrit à la main n'est jamais agrégé.

- **fix(up): `identity` ne corrompt plus un scalaire commenté de `project-context.yaml` (#426).**
  `cmd_setup._apply_project_context` réécrivait chaque ligne `user:` connue
  avec une regex `.+` qui avalait tout le reste de la ligne — valeur *et*
  commentaire inline — comme si c'était « la valeur », puis rewrappait ce
  texte entier entre guillemets. `skill_level: "expert"  # beginner |
  intermediate | expert` devenait `skill_level: ""expert"  # beginner |
  intermediate | expert"` à chaque `grimoire up`, y compris quand la valeur
  ne changeait pas — et cassait ensuite `grimoire doctor` (`GR002`, YAML
  imparsable). Un nouveau `_split_scalar_and_comment` sépare correctement
  le scalaire (quoté ou non) du commentaire qui le suit avant toute lecture
  ou réécriture, et `_apply_project_context` recolle le commentaire original
  après la nouvelle valeur au lieu de le jeter.

### 3.44.2 — Correctif de régression : `SessionStart` sur un agent à skills attachés

- **fix(hosts): `collect_agents` résout ses propres skills quand `known_skills` n'est pas fourni — `SessionStart` et le porteur de proposition ne plantent plus sur un agent à skills attachés (#423).**
  `entry_persona_context` (le hook `SessionStart`) et `_category_carrier`
  (le porteur par catégorie du déclencheur de propositions) appelaient
  `collect_agents(project_root)` sans lui passer l'inventaire des skills du
  projet ; l'ensemble retombait alors sur un ensemble vide, ce qui faisait
  échouer fail-closed *tout* agent déclarant un `skills:` en frontmatter —
  la forme par défaut des archétypes depuis #377/#387. Tout projet avec un
  agent à skills attachés voyait son `SessionStart` répondre « en erreur »
  et sa persona d'entrée jamais injectée. `collect_agents` résout
  désormais lui-même l'inventaire via `collect_skills()` quand aucun n'est
  fourni, pour qu'aucun appelant ne puisse retomber dans ce trou.

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
