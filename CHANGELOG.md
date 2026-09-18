# <img src="docs/assets/icons/chart.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

- feat(standard): gate auto-suffisant — artefacts scaffoldés, messages avec chemin, run-tests intégré (issue #582 lot G1). Mesure d'origine : le banc à trois bras du 2026-09-17 (`docs/bench/diagnostic-surcout-kit-2026-09-17.md`, analyse tour par tour de 21 runs kit-gov) attribue une médiane de 11 tours par run (jusqu'à 22) à la « spéléologie » dans le source installé du kit après un `FAIL missing context_bundle` sans chemin ni remède — 21 runs sur 21 ont ouvert `site-packages/grimoire/`. Cible du lot G : bras gouverné sous 1,5× les tours de Claude nu.
  - Messages de gate avec chemin et remède : `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) rend, pour chaque `gate.<clé>_missing`, le chemin attendu et une commande shell copiable (racine en absolu), via la nouvelle table unique `src/grimoire/core/standard_checks/gate_remedy.py` (`gate_artifact_relpath`, `remedy_command`, `remedy_for_relpath`, `missing_artifact_message`). `grimoire standard gate check` en mode texte (`src/grimoire/cli/cmd_standard.py`) rend désormais tous les checks (identifiant, chemin, message) et plus seulement `missing <clé>` ; `grimoire standard verify` fait suivre chaque chemin manquant de son remède ; le résumé du hook `Stop` (`src/grimoire/hosts/decisions/gate_summary.py`) relaie ces messages au lieu de la clé nue. Rouge-avant : `FAIL evidence gates for task X` / `missing context_bundle` ; vert-après : `gate.context_bundle_missing (_grimoire-output/context/X/context-bundle.yaml): Artefact de gate manquant : context_bundle — attendu à … ; remède : grimoire standard task scaffold /racine --task-id X`.
  - Nouvelle commande `grimoire standard task scaffold [racine] [--task-id X] [--dry-run]` (`src/grimoire/core/standard_task_scaffold.py`, sous-groupe `task` de `src/grimoire/cli/cmd_standard.py`) : crée, s'ils manquent seulement, les six artefacts par tâche que les gates réclament (`task-envelope.md`, `evidence-pack.md`, `claim-ledger.md`, `acceptance-record.md` selon le profil actif ; `context-bundle.yaml` et `decision-trace.yaml` via `build_context_bundle`/`build_decision_trace`), jamais un fichier présent. Pré-remplissage sans question : identifiant, titre, critères d'acceptation (une ligne `AC-00n … à vérifier` par critère, lus dans le Mission Ledger — ADR-007 — ou à défaut sur le board), profil, état courant, niveau de risque, `HEAD` git (`subprocess.run(..., encoding="utf-8")`), commande de test résolue par `resolve_need("test-runner", …)`, date ; le résumé placeholder du pack de preuve (`- Outcome:` vide) devient un résumé généré (titre + critères). Un squelette frais passe `gate check` jusqu'à `review` sans motif de forme. Tâche inconnue du ledger et du board → refus (`grimoire task add` d'abord). `--dry-run` n'écrit ni fichier ni événement ; `task.scaffolded` est journalisé sinon.
  - Le hook `SessionStart` (`src/grimoire/hosts/decisions/activation.py::_scaffold_active_task`, appelé par `decide_activation` quand `_is_governed` — lot A — reconnaît le projet) scaffolde la tâche active, silencieusement (seul `detail["scaffolded"]` le dit) et de façon idempotente ; best-effort, jamais une session cassée. Chemin « tout existe déjà » : six `stat`, sans YAML ni ledger — mesuré 0,18 ms en médiane (30 itérations, `scaffold_task_artifacts` no-op), `decide_activation` complet 2,6 ms ; borné à 100 ms par `tests/unit/test_hosts.py::test_session_start_scaffold_noop_stays_under_budget` et `tests/unit/core/test_standard_task_scaffold.py::test_scaffold_noop_stays_under_the_session_start_budget`. La directive d'activation (`src/grimoire/core/claude_activation.py`, étape 1) nomme le scaffold.
  - `gate check --strict` exécute lui-même `gate run-tests` (`src/grimoire/core/standard_checks/gate_test_run.py::ensure_fresh_test_run`) quand l'état de la tâche doit une preuve d'exécution (`in_progress`, `review`, `accepted`, `released`), qu'une commande de test est résolue et qu'aucun run vert et frais (`tree_fingerprint`, lot B) n'est enregistré, puis évalue comme avant ; le JSON porte `test_run` (`ran`, `reason`, `command`, `ok`, `exit_code`, `path`). `--no-run` restaure l'ancien comportement. Nouveau check `acceptance.test_run_failed` (`src/grimoire/core/standard_checks/controls.py::_verify_recorded_test_run_is_green`, déclaré dans `registry.py`) : un run enregistré, frais et rouge est une erreur — dans `gate check` comme dans le hook `Stop` (qui ne relance jamais de tests) ; un run rouge périmé ne l'est pas. Les identifiants `gate.task_board_missing`, `gate.task_envelope_missing`, `gate.context_bundle_missing`, `gate.memory_policy_missing`, émis depuis toujours mais jamais déclarés, sont ajoutés au registre.
  - Docs : `docs/cli-reference.md` (sous-groupe `task`, `gate check --no-run`, `gate run-tests`), `docs/standard/integration.md` (section « Gate auto-suffisant : chemin, remède, scaffold, tests intégrés »).

- feat(hosts): les hooks alimentent le pack de preuves depuis les actions observées (issue #582 lot G2). Le rapport du banc à trois bras (`_scratch/bench-f/analyse-tours-kit-gov.md`, 21 runs) montre l'agent recopiant systématiquement, à la main, dans `evidence-pack.md`, ce que le hook `PostToolUse` venait déjà de lui rappeler (« Écriture enregistrée… ») — un coût de tours pour une information que le hook connaît.
  - Nouveau module `src/grimoire/core/standard_checks/evidence_journal.py` : `append_evidence_event` (append-only, `encoding="utf-8"`) écrit dans `_grimoire-output/.runs/evidence/<task_id>/evidence-log.jsonl` (corrigé en relecture de PR — voir l'entrée `fix(hosts)` ci-dessous, initialement sous `EVIDENCE_DIR`) ; `read_evidence_log` le relit ligne à ligne, garde fermée — un fichier absent ou dont aucune ligne ne parse en JSON valide se lit comme `[]` (« rien observé »), sans jamais fabriquer de preuve ni faire remonter d'erreur ; une ligne corrompue au milieu n'invalide pas ses voisines. `build_bash_event`/`build_file_write_event` classent une commande Bash en `test_run` (motif `pytest|npm test|cargo test|go test|ctest|mvn … test|gradlew? test`) ou `bash` — un motif de commande, pas un appel à `resolve_need` (trop coûteux à chaque outil de chaque tour, voir le lot G3 pour l'extension de `resolve_need` lui-même). `render_observed_inventory`/`regenerate_observed_inventory_section` projettent le journal en section `## Inventaire observé` dans `evidence-pack.md`, délimitée par des marqueurs HTML (`<!-- grimoire:observed-inventory:start/end -->`), insérée sous la section manuelle « Evidence inventory » qui reste éditable, régénérée en place (jamais dupliquée) à chaque appel.
  - Le hook `PostToolUse` (`src/grimoire/hosts/decisions/evidence_trace.py::_record_observed_actions`) consigne, pour un projet enrôlé au standard : chaque commande Bash exécutée (tronquée à 240 caractères, code de sortie s'il est connu dans `tool_response`), chaque cible d'un outil d'écriture/édition, chaque run de test reconnu. Best-effort (`except Exception: return`), jamais au prix du hook. Mesuré (`tests/unit/test_hosts.py::test_post_tool_use_journal_write_stays_under_the_30ms_budget`, 30 itérations) : 0,31 ms en médiane, 0,40 ms au 95e percentile — budget de garde 30 ms.
  - `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) projette le journal (écrit la section observée) avant d'évaluer `_verify_evidence_pack`, pour les états `review`/`accepted`/`released` — seul appelant à le faire : `_verify_evidence_pack` (`src/grimoire/core/standard_checks/verifiers.py`, partagée avec `grimoire standard verify`) reste strictement lecture seule et se contente de lire `has_observed_inventory`, parce qu'elle sert aussi `grimoire_standard_verify`/`grimoire_standard_audit` côté MCP (`readOnlyHint`, `tests/unit/mcp/test_server.py::TestReadOnlyToolsWriteNothing`) — un premier essai qui projetait depuis `_verify_evidence_pack` elle-même faisait échouer ce test en écrivant `evidence-pack.md` sous un outil annoncé lecture seule. Le check `evidence.inventory_placeholder` n'est plus émis dès qu'au moins une action a été observée, même si la section manuelle « Evidence inventory » reste à l'état de gabarit ; reste un avertissement, jamais une erreur (transition douce, comme le reste des constats de contenu de cette fonction — voir `tests/test_agentic_standard.py::test_gate_check_surfaces_an_unproven_passed_criterion_without_failing`, qui fixe cette règle et qu'une première version de ce lot faisait régresser en promouvant le check en erreur pour les profils stricts).
  - Rouge-avant / vert-après (`tests/unit/core/test_evidence_journal.py::test_governed_gate_flags_empty_inventory_then_accepts_the_observed_log`) : avant ce lot, le module `evidence_journal` n'existe pas (`ModuleNotFoundError` à la collecte des tests) ; après, `check_evidence_gates(..., target_state="review")` sur une tâche scaffoldée sans dépôt git (donc sans ligne d'inventaire manuelle) rapporte `evidence.inventory_placeholder` tant qu'aucune action n'a été observée, puis cesse de le rapporter dès un seul `append_evidence_event`.
  - Tests : `tests/unit/core/test_evidence_journal.py` (garde fermée, classification, idempotence de la projection, rouge-avant/vert-après), `tests/unit/test_hosts.py` (écriture par le hook, budget de latence, silence sur un projet non enrôlé).

- fix(hosts): déplace le journal de preuve observé (lot G2 ci-dessus) sous `_grimoire-output/.runs/`. Relecture de PR #598 : `evidence-log.jsonl` vivait sous `EVIDENCE_DIR`, un dossier suivi par git dans tout projet gouverné (la Forge y commite `evidence-pack.md`) — un fichier append-only qui grossit à chaque appel d'outil aurait pollué `git status` et les commits de chaque projet. Déplacé sous `_grimoire-output/.runs/evidence/<task_id>/evidence-log.jsonl` (`evidence_log_relpath`, `src/grimoire/core/standard_checks/evidence_journal.py`) ; la projection dans `evidence-pack.md` (la preuve versionnée) ne bouge pas.
  - Nouvelle constante `RUNS_DIR` et nouvelle fonction `ensure_grimoire_gitignore` (`src/grimoire/core/standard_generation.py`), même marqueur/contenu (`# --- Grimoire Kit ---`, `_grimoire-output/.runs/`) que `ProjectScaffolder._plan_gitignore` (`src/grimoire/core/scaffold.py`), qui délègue désormais à cette fonction partagée plutôt que de dupliquer le texte.
  - `grimoire standard init` seul (`setup_standard_profile`, sans passer par le scaffold complet de `grimoire init`) n'écrivait jamais la ligne `.gitignore` pour `_grimoire-output/.runs/` — un vrai trou, comblé : `setup_standard_profile` appelle désormais `ensure_grimoire_gitignore` (best-effort, jamais en `--dry-run`).
  - Tests : `tests/unit/core/test_evidence_journal.py` (chemin sous `RUNS_DIR`, création/idempotence/préservation du contenu existant de `ensure_grimoire_gitignore`, `setup_standard_profile` seul pose la ligne, aucune écriture en `--dry-run`).

- fix(missions): la migration `task-board.yaml` -> Mission Ledger (ADR-007) ne remplace plus priorité, rôles et référence de remédiation d'une tâche par des défauts, et ne jette plus les champs de board qu'elle ne connaît pas encore. Défaut réel constaté le 2026-09-17 sur la Forge (kit 3.55.0) après `grimoire up` → `migrate-standard` : `git show 5de804a^:_grimoire/standard/task-board.yaml` (avant) contre `git show 5de804a:_grimoire/standard/task-board.yaml` (après) montre, pour les 10 tâches migrées, `priority: "high"` écrasé en `medium`, `agent_roles: [orchestrator, ...]` réduit à `[implementation]`, et `remediation_ref` disparu dès que le statut d'origine n'était pas `blocked`. Cause : `_import_one_task` (`src/grimoire/missions/task_unification.py`) ne transmettait ni `priority`, ni `agent_roles`, ni `remediation_ref` à `MissionLedger.create_task` — et `MissionTask` (`src/grimoire/missions/schemas.py`) n'avait nulle part où les ranger, ce qui forçait `_task_entry` (`src/grimoire/missions/board.py`) à toujours (re)dériver `priority` de `risk_profile` (défaut `medium`) et `agent_roles` de `type` (défaut `[implementation]`) — et à ne poser `remediation_ref` que pour une carte `blocked`. Tout champ de board que ce schéma ne modélise pas (ex. `labels`) était silencieusement jeté par le même chemin, faute de tout endroit où le ranger.
  - `MissionTask` gagne trois champs optionnels (`priority: str = ""`, `agent_roles: tuple[str, ...] = ()`, `remediation_ref: str = ""`) et un fourre-tout `extra: dict[str, Any]` pour toute clé de board non encore modélisée — compatibilité ascendante garantie : un événement `task.created` du ledger sans ces clés (tout l'historique existant) retombe sur les défauts actuels via `MissionTask.from_dict`.
  - `MissionLedger.create_task` (`src/grimoire/missions/ledger.py`) accepte ces quatre nouveaux paramètres, tous par défaut à leur valeur neutre.
  - `_import_one_task` (`task_unification.py`) transmet désormais `priority`/`agent_roles`/`remediation_ref` lus tels quels sur l'entrée de board, et collecte dans `extra` toute clé absente de `_KNOWN_BOARD_KEYS` (les clés déjà portées par un champ dédié, plus celles que `board.py` recalcule à chaque projection — `context_bundle_ref`, `decision_trace_ref`, `evidence_pack_ref`, `verifiability`, `blockers` — pour ne jamais les désynchroniser du ledger qui les régénère).
  - `_task_entry` (`board.py`) préfère désormais `task.priority`/`task.agent_roles`/`task.remediation_ref` quand ils sont posés, ne retombant sur la dérivation historique (`risk_profile`/`type`/défaut `blocked` seulement) que pour une tâche créée directement dans le ledger sans ces champs — les deux tests déjà existants sur cette dérivation (`tests/unit/test_missions_board.py`) restent verts sans modification. Toute clé de `task.extra` est reportée par `entry.setdefault(...)`, donc jamais prioritaire sur un champ déjà posé par la projection.
  - Tests rouge-avant/vert-après (`tests/unit/missions/test_task_unification.py`) : `test_priority_roles_remediation_ref_and_unknown_fields_survive_migration` reproduit exactement le board de la Forge (priorité `high`, deux `agent_roles`, `remediation_ref`, un champ `labels` inconnu, statut `accepted`) et compare le board reprojeté champ à champ à l'original (seul `verifiability` est un écart admis) — rouge avant le correctif (`priority == 'medium'` au lieu de `'high'`, vérifié en stashant les quatre fichiers source touchés puis en rejouant le test), vert après. `test_task_list_shows_the_original_status_after_migration` couvre le critère « `grimoire task list` montre l'état d'origine » (ledger `closed`, colonne de board `accepted`). `test_repair_sequence_restore_then_remigrate_matches_original_board` prouve la séquence de réparation d'un projet déjà migré à perte : migration simulée avec l'ancien comportement (`_old_lossy_import_one_task`, copie figée du code pré-correctif) → `restore_task_unification` sur l'instantané de cette migration lossy (board et ledger reviennent bit à bit à l'état d'avant migration, `events.jsonl` redevient absent) → remigration avec le correctif → board identique à l'original.
  - **Réparation d'un projet déjà migré à perte (ex. la Forge)** : chaque migration prend un instantané avant d'écrire (`_snapshot`, `SNAPSHOT_ROOT = "_grimoire-output/.migrations"`) et le rapport de migration porte son horodatage (`stamp`). Séquence : mettre à niveau vers la version du kit qui porte ce correctif, puis `grimoire task migrate-standard <racine> --restore <stamp>` (l'horodatage du run lossy, visible dans le rapport JSON d'origine ou dans `_grimoire-output/.migrations/<stamp>-tasks-unification/manifest.json`) pour ramener `task-board.yaml` et le Mission Ledger à leur état d'avant cette migration, puis rejouer `grimoire task migrate-standard <racine>` (sans `--restore`) pour remigrer avec le correctif. `restore_task_unification` existait déjà (`--restore`, lot 4.1) ; ce correctif ne change que ce que la remigration produit ensuite.
- fix(upgrade): le nœud `verify` lit désormais le manifeste réellement écrit par `backup`, jamais un chemin recalculé. Repro réelle, 2026-09-17 (homelab, kit 3.55.0), via `POST /api/projects/update` (cockpit) : `_archive/<date>-pre-<version>/` contenait déjà un `grimoire-state.tar.gz` fait à la main avant que le flow ne tourne. `backup_project` (`src/grimoire/tools/project_upgrade.py::backup_project`) a, comme documenté, poussé sa propre sauvegarde sur le suffixe `-2` (`grimoire-state-2.tar.gz`, `memory-manifest-sha256-2.txt`), mais `src/grimoire/cli/cmd_upgrade_flow.py` — dans `upgrade_flow_verify` (commande `verify`), `_check_verify` (acceptance `check verify`) et `_node_handlers()._verify` (nœud `verify` de `run`) — recalculait le chemin du manifeste depuis `archive_root(root) / "memory-manifest-sha256.txt"` (nom canonique fixe), un fichier que `backup` n'avait jamais écrit ce run-là : `verify` refusait avec « manifeste introuvable [...] le nœud backup a-t-il tourné ? » alors qu'`apply` avait réussi et que le projet était réellement mis à niveau. Le rapport JSON d'un run en échec (`_fail_run`) ne portait en plus jamais `backup_path` — `null` côté cockpit même quand `backup` avait réussi juste avant le nœud fautif ; seul le chemin spécial de refus d'`apply` le portait.
  - Nouvelle fonction `latest_backup_manifest` (`src/grimoire/tools/project_upgrade.py`) : résout le manifeste le plus récemment écrit dans l'archive du jour (glob + mtime) au lieu de deviner un nom fixe — utilisée par `upgrade_flow_verify` et `_check_verify`.
  - `_node_handlers()` (`cmd_upgrade_flow.py`) prend désormais `node_outputs` en paramètre : `_verify` lit le chemin littéral que le nœud `backup` a rapporté ce run (`node_outputs["backup"]["manifest"]`), avec `latest_backup_manifest` en repli seulement si ce détail est absent.
  - `_fail_run` porte désormais `backup_path` (même clé/forme que le chemin de refus d'`apply`, issue #510) : les deux appels de la boucle de `run` transmettent `(node_outputs.get("backup") or {}).get("tarball")`.
  - Tests rouge-avant/vert-après : `tests/unit/test_project_upgrade.py::test_upgrade_flow_run_verifies_against_a_suffixed_manifest_when_backup_is_pushed_to_dash_2` (archive préexistante → backup suffixé → verify doit réussir jusqu'au checkpoint `destructive`, `backup_path` doit pointer sur le tarball suffixé) et `tests/unit/cli/test_cmd_upgrade_flow_run.py::test_a_node_failure_after_backup_still_reports_the_backup_path` (un nœud après `backup` qui échoue doit quand même rapporter `backup_path`).
  - Pas de `--repair`/reprise dédiée dans le code existant pour rejouer `verify` seul sur un run `upgraded-but-failed` : le rejeu complet (`grimoire upgrade-flow run` relancé depuis le début) reste la voie testée et prouvée idempotente (`test_upgrade_flow_run_end_to_end`, second appel en fin de test).
- fix(hosts): la garde « force push » ne confond plus `-f` dans un nom de branche avec l'option `-f`. Défaut réel constaté le 2026-09-17 : `git push -u origin docs/rejeu-lot-f-2026-09-17` a été refusé par le hook `PreToolUse` comme une poussée forcée, forçant un renommage de branche. Cause : dans `_DESTRUCTIVE_PATTERNS` (`src/grimoire/hosts/decisions/tool_facts.py::classify_tool`), le motif `\bgit\s+push\b.*(--force|-f)\b` finit sur un `\b` — qui n'atteste qu'une transition mot/non-mot, et `-` compte comme non-mot au même titre qu'un espace — donc `-f-` au milieu d'un nom de branche se lit comme l'option `-f`. Même défaut sur `--hard` (`git reset --hard`) et sur le point final de `git checkout -- .` (`git checkout -- .gitignore` se lisait comme un abandon complet de l'arbre de travail).
  - Les trois motifs ancrent désormais sur `(?=\s|$)` (fin de jeton shell réelle — espace ou fin de chaîne) plutôt que sur `\b`. Le motif de poussée forcée reconnaît en plus `--force-with-lease[=réf]`, `--force-if-includes`, et les options courtes combinées contenant `f` (`-uf`, `-fu`) via `-[a-z]*f[a-z]*`, chacune bornée en jeton entier.
  - Tests rouge-avant/vert-après (`tests/unit/test_hosts.py`) : `test_force_push_detection_matches_the_flag_not_a_branch_name` (`git push -u origin docs/rejeu-lot-f-2026-09-17` et `git push origin feature-force` ne sont pas une poussée forcée ; `--force`, `-f`, `--force-with-lease[=réf]`, `--force-if-includes`, `-uf`, `-fu` en sont une), `test_hard_reset_detection_requires_the_flag_to_stand_alone`, `test_checkout_discard_all_requires_the_dot_to_stand_alone`.

## [3.55.0] - 2026-09-17

- fix(cockpit): la migration des tâches d'un board du standard vers le Mission Ledger s'applique désormais à n'importe quel projet du registre, pas seulement au projet de lancement du cockpit — issue #559 suite, #560. `POST /api/workspace/tasks/migrate-standard` (`src/grimoire/tools/workspace_routes.py::_task_migrate_standard`) et `POST /api/projects/update`/les décisions de proposition (#507, #490) ouvrent déjà cette porte pour n'importe quel projet du registre : migrer les tâches d'un projet qu'on pilote depuis la flotte (« jongler entre mes projets ») est le même genre de geste explicite et ponctuel, pas une écriture continue comme réclamer une tâche ou écrire un fichier — celles-là restent `_HOME_SLUG` seulement. Nouvelle fonction `is_registry_scoped_write` (`workspace_routes.py`), même patron que `is_proposal_decision` (#490) : le point d'entrée que `cmd_cockpit.py::_CockpitHandler.do_POST` interroge, en plus de `is_proposal_decision`, pour ouvrir la garde générale `_HOME_SLUG` sur cette seule route nommée. La résolution du projet cible reste celle déjà partagée par toutes les écritures de la vue de travail (`_resolve_project_path(self._query_slug())`, le registre `~/.grimoire`, jamais un chemin libre fourni par le client) : un slug inconnu ou hors registre reste un 404 explicite, un slug malformé ne correspond à aucune entrée du registre et reste donc refusé de la même façon. Côté client, `web/workspace/api.js::migrateStandardTasks` appelle désormais `postOpen` (comme `updateProject`/`proposalAction`) au lieu de `post`, qui aurait refusé l'appel sur le verrou général `readOnly` avant même d'atteindre le serveur ; le projet ciblé reste celui affiché, porté par la mécanique `withProject` existante, sans paramètre `project` dupliqué (piège #507). `web/workspace/spaces/executer.js` : le bouton « Migrer les tâches » n'est plus désactivé par `ctx.host.readOnly`, même précédent que les boutons Accepter/Refuser d'une proposition dans Piloter. Tests rouge-avant/vert-après (`tests/unit/test_workspace_routes.py`) : `ImportError` sur `is_registry_scoped_write` avant le correctif (la fonction n'existait pas) ; nouveaux cas verts après — projet du registre non home (`test_post_migrate_standard_fonctionne_sur_un_projet_du_registre_hors_lancement`, migration réellement effective, pas un 200 muet), slug inconnu (`test_post_migrate_standard_sur_un_slug_inconnu_est_un_404`), slug malformé (`test_post_migrate_standard_sur_un_slug_malforme_reste_refuse`), reconnaissance étroite de la dérogation (`test_is_registry_scoped_write_ne_reconnait_que_migrate_standard`, six cas). Les deux tests déjà paramétrés sur `POST_ROUTES` (`test_le_cockpit_refuse_les_ecritures_sur_un_projet_qu_il_ne_lance_pas`, `test_le_cockpit_refuse_toujours_l_autre_projet_meme_avec_un_lancement_direct`) excluent désormais explicitement cette route (`_REGISTRY_SCOPED_POST_ROUTES`), sans affaiblir la garde pour le reste de la table — même patron que #507 pour les décisions de proposition. `docs/cockpit-coverage-matrix.md` (ligne `POST /api/workspace/tasks/migrate-standard`) et `scripts/cockpit-coverage.py --check` synchronisés.
- feat(standard): une ligne `acceptance-record.md` marquée « passé » ne peut plus rester une déclaration de texte libre sur un projet qui a une commande de test connue — phase 2bis « Cœur » lot B (issue #582), diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §2) : `_verify_acceptance_record` (`src/grimoire/core/standard_checks/controls.py`) et `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) acceptaient une cellule « preuve » écrite par l'agent lui-même, jamais confrontée à une exécution réelle — constaté sur le banc (pass^k du bras kit 85 % contre 90 % nu/ecc, deux runs `go/palindrome-products`/`javascript/transpose` clos `completed` alors que les tests cachés du harnais les contredisaient). Réutilise le mécanisme d'acceptance déjà exécuté ailleurs dans le kit (`AcceptanceEvidence(kind="test")`, `src/grimoire/flows/schemas.py`/`dispatch_executor.py`, issue #428) plutôt que d'en dupliquer un : `_run_checks` (`src/grimoire/missions/dispatch.py`, importé localement pour ne pas alourdir l'import de `agentic_standard` du coût de `providers.audit`, ~60 ms mesurés) exécute la commande de test résolue par `grimoire.core.execution_needs.resolve_need("test-runner", …)` (déclaration `needs.commands.test-runner` dans `project-context.yaml`, ou détection par marqueur `pyproject.toml`/`package.json`/`Cargo.toml`/`go.mod`, déjà existante — issue #205). Nouvelle commande `grimoire standard gate run-tests [--task-id]` (`record_acceptance_test_run`, extrait dans son propre module `src/grimoire/core/standard_checks/acceptance_test_run.py` pour respecter le ratchet de taille — `agentic_standard.py` est grandfathered à 1916 lignes, `scripts/check-code-ratchet.py` R2) : exécute cette commande et enregistre le verdict (code de sortie, sortie tronquée) sous `_grimoire-output/evidence/<task-id>/test-run.json`. `_verify_acceptance_record` et `check_evidence_gates` (qui la réutilise désormais pour les états `review`/`accepted`/`released`, pas seulement `standard verify`) lisent ce fichier : une ligne « passé » sans run enregistré et vert lève **`acceptance.passed_without_test_run`** (avertissement — transition douce documentée ci-dessous, jamais un blocage sur cette release) ; un projet sans commande de test détectable reste déclaratif comme avant, signalé par **`acceptance.no_test_command_detected`**. **Transition WARN → FAIL** : cette release répond en avertissement quel que soit le profil ; une prochaine release promouvra `acceptance.passed_without_test_run` en erreur bloquante pour les profils `governed`/`production` — laisser le temps aux projets déjà gouvernés d'adopter `gate run-tests` avant que le gate ne les bloque. Tests rouge-avant/vert-après (`tests/unit/core/test_standard_gap_artifacts.py` : import de `record_acceptance_test_run` en échec avant le correctif faute d'exister, quatre nouveaux cas verts après — commande connue/inconnue, run réel vert/rouge ; `tests/test_agentic_standard.py` : `check_evidence_gates` surface le même avertissement sans faire échouer le gate, `gate run-tests` bout-en-bout via CLI). Rejeu sur la Forge (`grimoire standard gate check --task-id bootstrap --strict`, projet réel gouverné) : le gate reste vert, aucune ligne acceptance-record marquée « passé » sans run n'y étant déclarée aujourd'hui — le nouveau signal n'apparaît que lorsqu'une telle ligne existe. **Suite (revue de la PR) : un run enregistré n'était lié à aucun état du code** — un agent pouvait lancer `gate run-tests` tôt puis modifier le code sans jamais relancer les tests, et le gate restait vert sur du code jamais exercé. Nouveau module `src/grimoire/core/standard_checks/tree_fingerprint.py` (`compute_tree_fingerprint`) : dans un dépôt git, sha256 de `git rev-parse HEAD` + `git status --porcelain=v1 -z` + `git diff HEAD` (`_grimoire-output/` exclu par pathspec `:(exclude)`, sous peine d'auto-invalidation à chaque écriture de `test-run.json`) ; hors git (ou si git échoue), sha256 de la liste triée `(chemin, taille, mtime_ns)` de tout fichier hors `_grimoire-output/`, `.venv/`, `node_modules/`, `target/`, `.git/`. `record_acceptance_test_run` calcule l'empreinte juste avant d'exécuter la commande (pas après : la commande elle-même écrit des artefacts non suivis — `.pytest_cache`, `.coverage` — qui périmeraient le run dès son écriture si l'empreinte était prise après coup) et l'enregistre dans `test-run.json` (`tree_fingerprint`). `_verify_acceptance_test_execution` (`controls.py`) recalcule et compare à la vérification : un run vert dont l'empreinte diverge (ou est absente — garde fermée, un ancien format ou un fichier altéré compte comme périmé) lève **`acceptance.test_run_stale`** (avertissement, même transition douce WARN → FAIL que les deux autres constats de ce lot), message « run antérieur aux dernières modifications, relancez `gate run-tests` ». Subprocess git en `encoding="utf-8"`. Tests rouge-avant/vert-après (`tests/unit/core/test_standard_gap_artifacts.py::test_modifying_a_source_file_after_a_green_run_makes_it_stale` : le module n'existait pas avant, faisait échouer l'assertion par absence du constat ; vert après) et de non-régression (`test_modifying_only_grimoire_output_does_not_make_a_run_stale` : le mécanisme n'invalide jamais son propre run) ; nouvelle suite unitaire dédiée `tests/unit/core/test_tree_fingerprint.py` (9 cas : stabilité, détection d'un fichier suivi modifié, d'un fichier non suivi ajouté, exclusion de `_grimoire-output`/`.venv`/`node_modules`/`target`, repli filesystem sans commit git) — dépôts git jetables construits en `tmp_path`, jamais le dépôt ambiant. **Suite (deuxième revue de `tree_fingerprint.py`) — deux défauts corrigés avant fusion.** (1) L'empreinte était calculée AVANT le run : sur un projet git sans `.gitignore` adapté, un cache non suivi que la commande de test crée elle-même (`.pytest_cache/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`, `.hypothesis/`, `.coverage`) apparaissait dans `git status --porcelain` dès la vérification suivante, périmant le run à peine enregistré. Calculée désormais APRÈS le run (l'état de référence est celui que les tests laissent derrière eux) et ces caches ajoutés à `_EXCLUDED_DIRS` des deux modes, avec le pathspec git correspondant — vérifié empiriquement que `:(exclude)**/<nom>/**` seul, sans la magie `glob`, ne filtre qu'une occurrence à la racine, jamais en profondeur ; la forme retenue combine `:(exclude,glob)**/<nom>` (fichier plat, ex. `.coverage`) et `:(exclude,glob)**/<nom>/**` (dossier, à toute profondeur). (2) En mode git, une entrée `??` (fichier non suivi) n'est représentée que par son chemin : retoucher son contenu après le run ne changeait jamais l'empreinte — le cas courant d'un agent qui crée un fichier puis le retouche. Chaque entrée `??` du status ajoute désormais au condensé `(chemin, taille, mtime_ns)` du fichier, ou de chaque fichier sous le répertoire si l'entrée est un dossier (mêmes exclusions de cache appliquées à la récursion). Tests rouge-avant/vert-après : `test_a_cache_created_by_the_test_run_itself_does_not_make_it_stale` (dépôt git jetable, commande de test créant `.pytest_cache/x` non ignoré — rouge avant : `acceptance.test_run_stale` apparaissait à tort) et `test_editing_an_untracked_file_created_before_the_run_makes_it_stale` (fichier non suivi créé avant le run, retouché après — rouge avant : aucun `stale` détecté), tous deux dans `tests/unit/core/test_standard_gap_artifacts.py` ; six nouveaux cas unitaires dans `tests/unit/core/test_tree_fingerprint.py` (caches ignorés à la racine et sous un dossier déjà suivi, en mode git et filesystem ; fichier non suivi retouché, à la racine et sous un dossier lui-même non suivi). **Suite (revue de la PR, point 2) : la directive gouvernée mandate désormais `gate run-tests` avant `gate check`** — `_DIRECTIVE_TEMPLATE` (`src/grimoire/core/claude_activation.py`, étape 3) ajoute une ligne `grimoire standard gate run-tests --task-id {task_id}` juste avant `gate check --strict`, sans toucher au reste du texte ; le projet non gouverné (contexte court du lot A) est inchangé. Directive gouvernée 761 → 824 caractères (+63, un seul ajout de ligne) ; contexte `SessionStart` complet sur la Forge 1248 → 1311 caractères (même delta, lot C déjà appliqué). Tests : `test_default_directive_matches_preregistered_mechanism` (nouvel ancrage `gate run-tests --task-id bootstrap`) et nouveau `test_gate_run_tests_precedes_gate_check_in_the_directive` (`tests/unit/test_claude_activation.py`) vérifient l'ordre ; suite `test_hosts.py`/`test_claude_activation.py` complète revérifiée verte (l'assertion relative `short_len < governed_len / 2` de `test_an_ungoverned_project_gets_the_short_notice_instead` absorbe la croissance sans modification).
- perf(hosts): la persona d'entrée du `SessionStart` n'impose plus la lecture intégrale du fichier de l'agent — phase 2bis « Cœur » lot C (issue #582), diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §1.2.1) : `entry_persona_context` (`src/grimoire/hosts/decisions/activation.py`) mandatait « lis `{definition_ref}` en entier avant de répondre » sur *toute* session, mesuré à ~2 800 tokens pour la persona `concierge` livrée (11 Ko) — sans objet pour une session batch (`claude -p`) qui a déjà reçu sa tâche entière et n'a personne à trier. `HookInput` ne porte aujourd'hui aucun signal interactif/batch fiable (pas de TTY, rien qui survit dans le sous-processus du hook) pour réserver le mandat complet à l'interactif gouverné ; option retenue documentée dans le code : un résumé d'une ligne (nom, rôle, frontière d'outils) s'applique désormais uniformément, avec renvoi conditionnel — « lis le fichier complet si la demande est ambiguë ou si tu dois trier » — au lieu du mandat inconditionnel. Mesuré sur la Forge (projet gouverné, `grimoire.hosts.runtime` avec `--project-root`) : contexte `SessionStart` 1558 → 1248 caractères (-310, même delta mesuré sur un projet non gouverné jetable créé avec `grimoire init --backend local --lite`, 903 → 593). Test rouge-avant/vert-après (`tests/unit/test_hosts.py::test_the_entry_persona_is_a_one_line_summary_not_a_full_read_mandate`) : la chaîne `"en entier avant de répondre"` était présente avant ce correctif, absente après, remplacée par un renvoi conditionnel ; suite `test_hosts.py` complète revérifiée verte (aucun test existant ne dépendait du libellé du mandat complet).
- fix(hosts): la directive complète du `SessionStart` (enveloppe de tâche, pack de preuve, mandat `gate check --strict` + `verify .`) n'est plus injectée sur un projet qui n'a pas réellement adopté le standard agentique — phase 2bis « Cœur » lot A (issue #582), issues #551 #552, diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §1.2/§3, PR #581) : `decide_activation` (`src/grimoire/hosts/decisions/activation.py`) envoyait cette directive à *toute* session, y compris sur un dépôt jamais passé par `grimoire standard init` — reproduit : `grimoire standard verify .` y échoue alors sur 7 artefacts qu'aucune commande n'a jamais demandé de créer, et la session part faire des tours de « gouvernance » sur du vide (mesuré : ~90-95 % du surcoût de coût du bras kit, ratio de tours ×3,17 face au bras nu). Nouvelle fonction `_is_governed(project_root)` : un projet est gouverné quand `_grimoire/standard/` existe ET porte un signal fort (`task-board.yaml`) ou, à défaut, un profil enregistré (`_read_manifest_profile`, `agentic_standard.py`) qui ne ressemble pas à un simple répertoire minimal abandonné dans un terrain de jeu (`_looks_like_a_playground`, déjà utilisé par `grimoire init --lite`, issue #552 lot 2.6) — un `task-board.yaml` réel reste un signal suffisant à lui seul, jamais annulé par l'heuristique de terrain de jeu. Un projet non gouverné reçoit deux lignes (`[Grimoire — projet non gouverné]`) au lieu du mandat complet ; le comportement d'un projet gouverné reste bit-à-bit identique (mesuré en test : ~760 caractères inchangés contre ~250 pour l'avis court). `Decision.detail` gagne une clé `governed: bool`, terrain d'observation pour la métrique 3b (lot 2.5) sans rien y brancher ici. Tests (`tests/unit/test_hosts.py`) : cas rouge-avant sur un `tmp_path` vierge (aucune ligne `[Grimoire Standard — activation]`), non-régression sur un projet réellement gouverné (directive inchangée, ordre persona/rappel/directive), quatre tests existants qui présumaient — à tort, c'est exactement le défaut corrigé — qu'un projet avec seulement des agents personnalisés recevait la directive complète, migrés vers la fixture `governed` ou un board minimal écrit à la main.
- test(bench): garde-fou disque et tokens de cache dans le harnais du banc à trois bras — phase 2bis lot D (issue #582), issues #551 #552. `scripts/bench/three_arms.py` : `disk_guard_ok()` vérifie l'espace libre du workspace (`shutil.disk_usage`, seuil `DISK_GUARD_MIN_FREE_GB = 5.0`) avant chaque run de la boucle principale de `main()` — sous le seuil, la campagne s'arrête proprement (aucun nouveau run lancé) et écrit son rapport partiel avec ce qui a déjà tourné, plutôt que de crasher au milieu d'un run ; `cleanup_build_artifacts()` supprime `target/` (Rust) et `node_modules/` (JS) sous chaque dépôt de tâche après CHAQUE run (pas seulement en fin de campagne), appelée dans `_run_one` juste après `run_hidden_tests`. `RunOutcome` et `RunRecord` gagnent `cache_read_input_tokens`/`cache_creation_input_tokens` (lus depuis `usage` de l'événement `result` du flux `stream-json`, défaut 0 si absents) et `RunRecord` gagne `model_usage` — `RunOutcome.model_usage` était déjà rempli mais jamais recopié dans `results.jsonl`, un oubli documenté par le diagnostic du surcoût. Tests (`tests/unit/test_bench_three_arms.py`, 8 nouveaux cas, aucun appel réseau ni `claude -p`) : extraction des tokens de cache et de `modelUsage` depuis un `subprocess.Popen` simulé, valeurs par défaut à 0 sans ces clés, trajet complet jusqu'au `RunRecord` via `_run_one` avec collaborateurs simulés, nettoyage `target`/`node_modules` (présence et absence), garde disque sous/au-dessus du seuil, arrêt propre de `main()` avec rapport partiel quand le disque est déjà sous le seuil avant le premier run.
- feat(bench): rejouer un sous-ensemble de bras (`--arms`) et tracer les runs de tests du bras kit — phase 2bis lot E, harnais (issue #582). `scripts/bench/three_arms.py` : nouvelle option `--arms nu,ecc,kit` (`parse_arms`, défaut : les trois, repli sur les trois si la valeur est vide/absente, erreur explicite sur un bras inconnu) qui restreint quels bras la boucle principale de `main()` rejoue réellement — `_run_one` n'est plus jamais appelé pour un bras absent de la sélection. Compatible `--resume` : les lignes déjà présentes dans `results.jsonl` pour les bras non sélectionnés restent chargées et comptées dans `build_report`/`render_report_markdown`, avec une mention « bras repris de la campagne du `<date>` » dès qu'un bras a des runs mais n'a pas été rejoué par cette exécution (`carried_over_label`, basé sur le nouveau champ `RunRecord.recorded_at` — absent sur les lignes antérieures à ce lot, repli explicite « date inconnue » plutôt qu'une date inventée). Correctif au passage dans `main()` : le critère d'arrêt statistique (`should_stop_early`) comptait les tâches de TOUS les bras présents dans `records`, y compris ceux simplement repris via `--resume` — avec `--arms kit` et des lignes nu/ecc déjà complètes pour les 20 tâches, il se déclenchait à tort dès la première tâche kit (« les 20 tâches ont été rejouées ») ; désormais réservé aux exécutions qui rejouent bien les trois bras (`selected_arms == ARMS`). Nouvelle option `--label` (texte libre, reprise telle quelle dans `report.md`/`report.json`, y compris via `--report-only --arms --label` pour annoter un rapport régénéré sans rien rejouer). Bras `kit` : `RunRecord.kit_test_run_evidence` (`has_test_run_evidence()`, glob `_grimoire-output/evidence/*/test-run.json` dans le dépôt de tâche en fin de run — peu importe le `--task-id` choisi par l'agent) atteste que `grimoire standard gate run-tests` a tourné (lot B, #585) ; nouvelle section « Détail par run — bras kit » de `report.md` (tours, coût, temps, preuve `test-run.json`) construite par `build_report`. Tests (`tests/unit/test_bench_three_arms.py`, 17 nouveaux cas, aucun appel réseau ni `claude -p`) : `parse_arms` (défaut, valeur unique, ordre canonique indépendant de l'ordre saisi, espaces/doublons tolérés, bras inconnu rejeté, repli argparse via `SystemExit`), `has_test_run_evidence` (présence/absence, `task-id` arbitraire), `carried_over_label` (date la plus ancienne, repli explicite sans `recorded_at`), `build_report` (notes de reprise uniquement pour les bras non rejoués avec des données, absence de note quand `rerun_arms` est `None`, détail par run kit), et un test de bout en bout de `main(["--full", "--resume", "--arms", "kit", ...])` vérifiant qu'aucun run nu/ecc n'est lancé et que le rapport final comptabilise bien les lignes reprises et les nouvelles lignes kit.
- docs(bench): rejeu du banc à trois bras après les lots A/B/C — phase 2bis « Cœur » lot E (issue #582). `docs/bench/rejeu-lot-e-2026-09-17.md` : `scripts/bench/three_arms.py --full --resume --arms kit --seed 551` rejoué sur `main` @ `6d9db6c6` (lots A #584, B #585, C #586, harnais lot E #589), bras `nu`/`ecc` repris tels quels de la campagne du 2026-09-17. Résultat : tours kit/nu ×3,17 → ×1,0 (cible du lot A largement atteinte, temps médian kit désormais sous nu/ecc), coût médian/tâche résolue kit 1,247 $ → 0,416 $ (33 % de son propre niveau d'avant les lots, mais ≈104-112 % du coût `nu` — au-dessus, pas en-dessous, du seuil de 70 % du nu fixé par `docs/plan-2026-q4.md`), succès et pass^k kit strictement identiques à la lettre, tâche par tâche, à la campagne d'avant les lots (90 %/85 %, IC95 % très chevauchants avec nu/ecc). **Constat qui rouvre la mesure du lot B** : les 60 runs `kit` de ce rejeu n'ont jamais produit de `_grimoire-output/evidence/*/test-run.json` (0/60) — `setup_arm_kit()` (`scripts/bench/three_arms.py`) provisionne le bras `kit` avec `grimoire init`/`host sync` mais jamais `grimoire standard init`, donc `_grimoire/standard/` n'existe jamais dans les dépôts de tâche du banc et `_is_governed()` (lot A, `src/grimoire/hosts/decisions/activation.py`) classe le bras `kit` comme non gouverné à chaque run — la directive du lot B (`gate run-tests` avant `gate check`) ne part donc jamais, vérifié en isolation (`grimoire init . --backend local --no-cockpit` dans un dépôt jetable) et confirmé par l'identité bit à bit de `go/palindrome-products` (kit 1/3) et `javascript/transpose` (kit 2/3) — les deux tâches visées par le lot B — entre l'ancien et le nouveau rapport. Nouveau lot F consigné dans `docs/plan-2026-q4.md` (phase 2bis) : fermer cette lacune de méthode (`setup_arm_kit()` doit provisionner un projet réellement enrôlé) avant tout nouveau verdict sur le lot B. Verdict global : critère de sortie de phase (`docs/plan-2026-q4.md` §4) partiellement atteint seulement — tours atteint, coût et succès non atteints au sens strict — phases 3 et 4 restent gelées. `docs/plan-2026-q4.md` (lignes de la table des lots, critère de fait, notices de gel des phases 3/4, registre des risques) et `docs/index.md` mis à jour en conséquence. Données brutes de la campagne (`results.jsonl`, `selection.json`, `report.json`) non poussées, conservées localement (Grimoire-Forge, hors dépôt kit).
- docs(plan): verdict du banc à trois bras (2026-09-17, graine 551, [rapport](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/bench-reports/_grimoire-output/bench-reports/three-arms/2026-09-17/report.md)) consigné dans `docs/plan-2026-q4.md` — issue #551. Critère d'arrêt §3 déclenché : **ARRÊT**, le kit gouverné par défaut coûte ×3,1 le coût médian par tâche résolue de l'hôte nu (1,247 $ vs 0,399 $) et ×3,0 le temps (189 s vs 64 s médian), pour une réussite égale ou légèrement inférieure (90,0 % vs 91,7 %, IC95% chevauchants). §2 mis à jour avec les valeurs mesurées (succès, pass^k, tours, coût/tour par bras). Décision : extras du plan (reçu portable, rejeu, skills, parité première heure — phases 3 et 4) gelés jusqu'à un rejeu du banc où le kit atteint réussite ≥ nu et coût ≤ 70 % du nu ; la plomberie déjà en vol (unification des tâches, lot 4.1/#559) continue, hors du chemin d'exécution mesuré. Nouvelle phase 2bis « Cœur » (issue #582) insérée entre les phases 2 et 3, avec les lots A (classer la tâche avant le mandat de gouvernance), B (gates reliées à une exécution réelle), C (persona d'entrée à la demande), D (harnais : garde-fous et mesures manquantes) et E (rejeu du banc comme critère de fait) — d'après `docs/bench/diagnostic-surcout-kit-2026-09-17.md` (PR #581). Décision hors-lot consignée : la cascade de dispatch n'est jamais sollicitée sur le chemin `claude -p` (`dispatch_stats.overall.total == 0` sur 60/60 runs kit) — mesure à étendre à un bras « flow du kit » séparé. Correction du lot 5.5 (§6) : le gate `grimoire-skill-analyzer` n'existe que côté Forge, le kit doit créer son propre `skills-ref validate`, pas le réutiliser. §7 : deux risques ajoutés (gouvernance non conditionnelle, preuve déclarative = garde qui échoue ouvert).
- docs(bench): diagnostic du surcoût du bras kit sur le banc à trois bras du 2026-09-17 — issues #551 #552, `docs/bench/diagnostic-surcout-kit-2026-09-17.md`. Le rapport (`bench-reports@c43483ac`) déclenche le critère d'arrêt du plan produit 2026-Q4 (kit ×3,1 le coût et ×3,0 le temps de Claude Code nu pour un succès égal ou inférieur) : ce document attribue le surcoût, sur les agrégats de `results.jsonl` et une reproduction mécanique du chemin `grimoire init --backend local --no-cockpit` + `host sync --host claude` (pas les transcripts bruts de la campagne, disparus au nettoyage disque — limite documentée en tête du rapport). Constat central : les ratios tours/temps/coût du bras kit sont quasi identiques (×3,17/×3,36/×3,19) alors que son coût moyen par tour n'est pas plus élevé que celui du bras nu — le surcoût est un effet de nombre de tours, pas de contexte plus lourd par tour, pas du temps de setup (hors `wall_seconds` par construction du harnais), pas de la cascade de dispatch (`dispatch_stats.overall.total == 0` sur 60/60 runs kit, jamais invoquée sur le chemin `claude -p`). Cause reproduite : `decide_activation` (`src/grimoire/hosts/decisions/activation.py`) injecte au `SessionStart`, sans aucune condition liée au projet ou à la tâche, une persona d'entrée (lecture forcée de `concierge.md`, 2800 tokens) et la directive du standard (`_DIRECTIVE_TEMPLATE`, `src/grimoire/core/claude_activation.py`) même sur un projet qui n'a jamais initialisé `_grimoire/standard/` — vérifié en conditions réelles sur un dépôt vierge. Preuve du patron « gates verts sans tests » (échecs `go/palindrome-products`, `javascript/transpose`, `terminated_reason: completed`) : `grimoire standard gate check --strict` répond `OK` (exit 0) sur un projet sans aucune gouvernance initialisée (l'état de tâche retombe sur une chaîne vide qui ne déclenche aucune vérification d'artefact) ; `grimoire standard verify` ne vérifie que l'existence et le parsing de fichiers déclaratifs — grep exhaustif de `src/grimoire/core/standard_checks/verifiers.py` (43 fonctions `_verify_*`) : zéro appel `subprocess`/`pytest`/`cargo test`/`go test`/`npm test` ; `_verify_acceptance_record` (`controls.py`) parse un tableau Markdown écrit par l'agent lui-même, jamais confronté à une exécution réelle. Trois lots de cœur proposés (S/M/S) : classer la tâche avant d'imposer le mandat de gouvernance, relier les gates au mécanisme d'exécution déjà existant `AcceptanceRun`/`AcceptanceEvidence(kind="test")` du moteur de flows (`dispatch_executor.py`, issue #428) au lieu du tableau déclaratif, alléger la persona d'entrée sur une session batch — chacun avec sa mesure de preuve sur le même banc (`three_arms.py --full`, graine 551). Aucun code de `src/grimoire` modifié par cette PR (diagnostic en lecture seule).
- feat(missions): moteur de migration `task-board.yaml` -> Mission Ledger — issue #559, lot 4.1 du plan 2026-Q4 (1/3), ADR-007 (`docs/adr-007-unification-des-taches.md`). ADR-005 (2026-08-27) avait déjà décidé que le `MissionLedger` est la source de vérité et `_grimoire/standard/task-board.yaml` sa projection, mais `grimoire standard init` scaffold ce board en copiant le template statique sans jamais ouvrir de ledger (constat #521) : tout projet gouverné a un board sans source, et `workspace_api.tasks_view` (Exécuter, Preuves du cockpit) ne lit que le ledger. Nouveau module `src/grimoire/missions/task_unification.py` : `migrate_standard_tasks()` reconstruit dans le ledger toute tâche du board absente de lui (appariée par `task_id`, rattachée à une mission `MIS-standard-import-001`), en marchant sur `_TASK_TRANSITIONS` (`ledger.py`) pour atteindre l'état cible de chacune des 8 colonnes du board — idempotent (rejouer sur un projet déjà migré n'importe rien de plus) et réversible (`restore_task_unification`, patron snapshot/apply/restore de `grimoire migrate`, manifeste JSON sous `_grimoire-output/.migrations/`). `tasks_unification_status()` expose déjà la divergence board/ledger, terrain du futur check `doctor` (PR 2/3). Nouvelle sous-commande additive `grimoire task migrate-standard [--dry-run] [--restore STAMP]` (`src/grimoire/cli/cmd_task.py`) — aucune commande existante renommée. `MissionTask` gagne un champ `finition: str = ""` (`""`/`maquette`/`peaufine`, validé dans `__post_init__`), propagé dans la projection board (`missions/board.py::_task_entry`) : terrain du lot 4.3 (issue #561), non exploité par aucune interface ici. Cette PR ne touche ni `grimoire standard init` ni `grimoire up` (PR 2/3) ni le cockpit (PR 3/3) — purement additive. Tests (`tests/unit/missions/test_task_unification.py`, 21 cas) : le bug documenté avant le correctif (board scaffoldé sans ledger), les 8 états de `BOARD_LIFECYCLE` couverts (avec la classe d'équivalence `released`→`accepted` déjà documentée par ADR-005), idempotence, réversibilité bit-à-bit, aucune perte des références de preuve (`context_bundle_ref`/`decision_trace_ref`/`evidence_pack_ref`), statut de divergence, champ `finition`, CLI bout-en-bout.
- feat(standard): `grimoire standard init` ouvre le Mission Ledger, `up` migre en meilleur effort, `doctor` signale la divergence — issue #559, lot 4.1 du plan 2026-Q4 (2/3), ADR-007 points 1/3/4 (`docs/adr-007-unification-des-taches.md`). Point 1 : `setup_standard_profile()` (`src/grimoire/core/agentic_standard.py`) n'écrit plus l'artefact `task_board` en copiant le template statique `framework/agentic-standard/templates/task-board.yaml` — nouvelle fonction `ensure_task_board_via_ledger()` (`core/task_board_ledger.py`) ouvre (`ledger.create_mission`/`ledger.create_task`, mission `MIS-standard-bootstrap-001`) puis projette (`build_board`/`write_board`) la tâche `bootstrap` dans le Mission Ledger, exactement comme `TaskService.project_board()` le fait déjà pour chaque transition ; les règles `force`/`refresh`/`keep` de `standard_generation.decide()` restent calculées sur le rendu du template inchangé, seul ce qui est écrit sur disque à l'action `write` a changé — un projet déjà édité à la main garde son board (invariant I4). Idempotent : rejouer l'init (`refresh=True`, comme `up`) ne recrée ni mission ni tâche. Point 3 : nouveau pas `step_task_unification()` (`cli/cmd_up_task_unification.py`), exécuté indépendamment de `--no-standard` (un projet peut être enrôlé d'un `init` précédent que ce passage ne répète pas) — sur un projet enrôlé (`is_standard_enrolled`) dont `tasks_unification_status()` (`task_unification.py`, PR 1/3) rapporte une divergence, appelle `migrate_standard_tasks()` en meilleur effort (jamais bloquant : une erreur devient un pas `failed` nommé, jamais un `up` interrompu) et le dit dans son rapport (texte et JSON, via `StepResult`). Point 4 : nouveau check `task_unification` dans `grimoire doctor` (`apply_task_unification_check()`, `core/task_unification_doctor.py`, appelé depuis `src/grimoire/cli/app.py`) — `WARN` nommé (jamais `FAIL`, ADR-007 : « jamais une correction automatique ») avec le remède exact (`grimoire task migrate-standard .`) quand un projet enrôlé diverge (board sans ledger, ou tâches manquantes), silencieux sinon. Quatre tests préexistants ajustés pour la même raison (une fixture qui présumait — à tort, c'est le défaut que ce lot corrige — qu'un `standard init` gouverné ne laissait aucun ledger) : `tests/unit/cli/test_cmd_task_board.py` (le board « écrit à la main » et le cas « sans ledger » sont désormais simulés explicitement plutôt que d'être l'état par défaut d'un init frais ; le compte de tâches JSON inclut la tâche `bootstrap`), `tests/unit/missions/test_trace.py` (le cas « sans ledger » retire explicitement le ledger que l'init ouvre maintenant), `tests/unit/test_hosts.py` (`_set_task_in_progress` édite la structure YAML parsée au lieu d'un remplacement de texte littéral `status: "proposed"`, qui ne correspond plus au formatage `ruamel.yaml` non cité du board projeté depuis le ledger). Tests neufs : `tests/unit/missions/test_task_board_init.py` (ledger ouvert à l'init, idempotence, équivalence du board produit face à `grimoire standard gate check` comparé à l'ancien template statique), `tests/unit/cli/test_doctor_task_unification.py` (WARN nommé rouge-avant, silence après migration, silence sur un projet non enrôlé), `tests/test_cmd_up.py::TestUpTaskUnification` (migration d'un board legacy sans ledger, idempotence, rien à migrer sur un projet fraîchement `up`). Les trois ajouts extraits dans leur propre module (`task_board_ledger.py`, `cmd_up_task_unification.py`, `task_unification_doctor.py`) plutôt que d'agrandir `agentic_standard.py`/`cmd_up.py`/`app.py` — trois fichiers déjà au-dessus du seuil de `scripts/check-code-ratchet.py` (1500 lignes, grandfathered pour les deux premiers) ; `app.py` reste 3 lignes au-dessus de son ancien plafond malgré l'extraction (un appel minimal à trois lignes, cohérent avec le reste de `doctor()`), plafond relevé à la main dans `scripts/code-ratchet-baseline.json` (2798 -> 2801, chemin prévu par le script lui-même pour une exception délibérée).
- feat(cockpit): l'espace Exécuter et le panneau Preuves lisent le Mission Ledger, migration proposée dans l'interface — issue #559, lot 4.1 du plan 2026-Q4 (3/3), ADR-007 point 5 (`docs/adr-007-unification-des-taches.md`). Point 5 (« aucun changement requis côté cockpit ») vérifié explicitement plutôt que supposé : nouveaux tests prouvant que `workspace_api.tasks_view`/`task_view` (Exécuter, Preuves) et `grimoire task list` (`TaskService.list_tasks`) lisent déjà la même source, pour un projet fraîchement `standard init` (point 1) et pour un projet migré (point 3) — `tests/unit/test_workspace_api.py::test_tasks_view_et_task_list_lisent_le_meme_ledger_apres_un_init_frais`/`_apres_migration`. `tasks_view()` (`src/grimoire/tools/workspace_api.py`) distingue désormais, dans son `ledger: false`, un projet enrôlé au standard dont le board n'a jamais été migré (`migration_available: true`, message dédié) d'un projet qui n'a même pas de board (message générique inchangé) — `tasks_unification_status()` (`missions/task_unification.py`, lot 1/3) porte déjà ce diagnostic. Nouvelle route `POST /api/workspace/tasks/migrate-standard` (`src/grimoire/tools/workspace_routes.py`) : même moteur que `grimoire task migrate-standard` (`migrate_standard_tasks()`), exposée en écriture uniquement sur le projet de lancement direct du cockpit — même garde `_HOME_SLUG` que le reste de `POST_ROUTES`, prouvée automatiquement par les tests déjà paramétrés sur cette table. Espace Exécuter (`web/workspace/spaces/executer.js`) : un projet enrôlé mais non migré affiche désormais un bouton « Migrer les tâches » (au lieu du seul rappel `grimoire task add`, qui ouvrirait une tâche neuve plutôt que d'importer celles déjà déclarées) — le clic appelle la nouvelle route et recharge le board. Champ `finition` (ADR-007 point 6, lot 4.3 à venir, issue #561) affiché en lecture seule dans la fiche tâche de l'inspecteur, absent tant qu'il n'est pas posé (aucune interface ne l'écrit encore). `docs/cockpit-coverage-matrix.md` + `scripts/cockpit-coverage.py` : deux lignes ajoutées (bouton de migration, affichage `finition`) et une route, résumé régénéré (169 lignes, 133 couvertes, 79 %). Quatre tests préexistants ajustés pour la même raison que la PR précédente (un `standard init --profile governed` scaffoldé sans ledger était la précondition de plusieurs fixtures e2e, plus vraie depuis le lot 2/3) : `tests/e2e/conftest.py::served_empty` retire désormais explicitement le ledger fraîchement ouvert pour rester le projet « sans Mission Ledger » qu'il documente — ce qui en fait aussi, sans rien y ajouter, le projet « enrôlé mais jamais migré » que le nouveau test du bouton utilise. Tests neufs : `tests/unit/test_workspace_api.py` (`migration_available`, disparition du message après migration, même source Exécuter/`task list` × 2 scénarios, `finition` en lecture seule), `tests/unit/test_workspace_routes.py::test_post_migrate_standard_importe_le_board_non_migre_sur_le_projet_de_lancement` (rouge-avant : 404 sans la route), `tests/e2e/test_workspace_controls.py::test_executer_etat_non_migre_propose_le_bouton_migrer_les_taches` (clic réel, board recharge, tâche `bootstrap` visible). Closes #559, Closes #521.
- fix(init): `grimoire init` en mode `auto` (défaut, y compris `-y`) ne câble plus silencieusement un projet neuf sur un backend mémoire réseau trouvé sur l'hôte — issue #496, reconfirmée par #578 sur ce même poste (un Weaviate de dogfooding sur `:8080` avait écrit `backend: weaviate-server` sur un projet jetable, cas réel #493). `detect_memory_backend()` (`src/grimoire/cli/cmd_init.py`) ne décide plus le backend : sans `--backend` explicite, `run_init` retombe toujours sur `lexical`, et le service détecté (Weaviate, Qdrant, Ollama) n'est plus que *suggéré* dans le rapport texte et JSON, avec la commande pour l'activer (`grimoire memory up --profile standard --apply`). Le wizard interactif peut toujours proposer le service détecté, mais uniquement via une question explicite (`_choose_memory_profile`) — jamais comme sélection par défaut ; un `--backend` déjà explicite ne déclenche aucune question. Sur un backend partagé choisi explicitement (`weaviate-server`, `qdrant-local`, `qdrant-server`), la collection est désormais nommée d'après le projet (`collection_prefix`, dérivé de son slug via `grimoire.memory.taxonomy.slugify`) plutôt que le nom fixe `GrimoireMemory` que tous les projets partageaient jusqu'ici (retiré de `BACKEND_CONNECTION` dans `src/grimoire/memory/profiles.py`) ; `ProjectScaffolder` (`src/grimoire/core/scaffold.py`) accepte ce `collection_prefix` et l'émet dans `project-context.yaml`. S'attacher à une collection déjà peuplée exige désormais `--memory-collection <nom>` explicite — sans lui, `init` sonde la collection visée (`collection_has_content`, best-effort, une sonde en échec ne bloque jamais) et refuse avec un message clair si elle contient déjà des données. Reproduit et vérifié en conditions réelles sur ce poste (Weaviate de dogfooding réel sur `:8080`, projets jetables, aucune écriture dans le service) : avant le correctif, `init -y` écrivait `backend: "weaviate-server"` + `weaviate_collection: "GrimoireMemory"` ; après, `backend: "lexical"` avec la suggestion, et `--backend weaviate-server` explicite refuse de s'attacher à la collection `GrimoireKitMemory` existante (peuplée) tant que `--memory-collection` n'est pas fourni. Tests (`tests/test_cmd_init.py`, `tests/unit/cli/test_app.py`) : détection purement informationnelle, sonde de contenu de collection tolérante aux pannes, question explicite du wizard (acceptée/refusée/jamais posée si déjà explicite), CLI bout-en-bout (repli lexical malgré un service détecté, suggestion dans le rapport texte/JSON, `--backend` explicite toujours honoré, nommage par slug, `--memory-collection` en override, refus sur collection non vide, backends locaux non affectés). Docs : `docs/getting-started.md` (nouvelle section « Mémoire par défaut »), `docs/memory-system.md` (§ Mise en place et diagnostic).
- feat(init): `grimoire init --lite` (alias `--profile lite`) — issue #552, phase 2 du plan produit 2026-Q4, lot 2.6. Un preset nommé, pas un nouveau mécanisme : chaque réglage qu'il pose est déjà un flag existant (`--backend lexical`, `--memory-profile lexical`, `--no-cockpit`, `--archetype minimal` par défaut si aucun n'est fourni) — appliqué avant la résolution habituelle dans `run_init` (`src/grimoire/cli/cmd_init.py`), donc un assistant interactif peut toujours les changer (pas de `--yes` implicite, cohérent avec le comportement existant de tous les autres flags d'`init`). Le rapport texte et JSON (`_display_report`/`_display_json`) nomme les trois choses laissées de côté (mémoire lexicale seule, pas de cockpit, standard non activé) et la commande exacte pour activer chacune plus tard (`grimoire memory up --profile standard --apply`, `grimoire cockpit add .`, `grimoire standard init .`). Le wizard interactif recommande désormais `lexical` en premier (au lieu du profil par défaut) quand `--lite` est passé ou quand le dépôt ressemble à un terrain de jeu (`_looks_like_a_playground` : aucun marqueur de CI top-level, aucun répertoire de tests non vide) — heuristique légère, jamais un scan récursif, qui ne change jamais ce qui est écrit, seulement la suggestion. Mesuré sur un projet jetable (`GRIMOIRE_NO_COCKPIT=1`, 3 exécutions chacun) : temps d'`init` 0,31-0,35 s en lite contre 0,37-0,45 s par défaut sur cette machine (qui a détecté un Weaviate + Neo4j réels sur localhost et écrit `backend: weaviate-server` — exactement le risque de non-déterminisme que `--lite` évite en pinnant `lexical` sans probe réseau) ; 102 fichiers écrits en lite contre 103 par défaut (`docker-compose.memory-target.yml` en moins) ; tokens injectés au premier hook `SessionStart` identiques dans les deux cas (1409 caractères ≈ 352 tokens — l'archétype `minimal` est déjà le même des deux côtés sur ce dépôt sans stack détectée, donc `--lite` ne réduit pas ce total ici, contrairement aux fichiers/services). Tests (`tests/unit/cli/test_init_lite_profile.py`, nouveau, 16 cas) : heuristique de détection, recommandation du wizard, backend lexical écrit, aucune écriture cockpit (HOME isolé de la suite, `conftest._isolate_user_state`), aucun répertoire `_grimoire/standard`, contenu du rapport texte et JSON, alias `--profile lite`, rejet d'un nom de profil inconnu, `doctor` vert sur le projet généré, hook `SessionStart` réel qui répond sainement, aucune référence à qdrant/weaviate/ollama dans la config écrite.
- feat(cli): `grimoire --help` ne liste plus 49+ commandes de premier niveau par défaut (issue #552, phase 2 du plan produit 2026-Q4, métrique 2, cible ≤ 5). Cinq commandes couvrent la boucle réelle de la première heure — scaffolder (`init`), tout enchaîner (`up`), diagnostiquer (`doctor`), l'unité de travail gouvernée avec son reçu de preuve (`flow`), le tableau de bord (`cockpit`) — rendues par `_render_condensed_help` (`src/grimoire/cli/_condensed_help.py`, extrait d'`app.py` — R2 du ratchet de taille, `scripts/code-ratchet-baseline.json`, interdit toute croissance d'un fichier déjà au-dessus du seuil) ; `grimoire --help --all` (n'importe quel ordre des deux flags) restitue l'affichage complet à 100 % inchangé (mêmes panneaux, mêmes 52 commandes mesurées ce jour, aucune renommée ni retirée). Le rendu condensé n'importe plus aucun module `cmd_*` lazy (`LazyTyperGroup`, #405) — c'était le vrai coût : la vue complète walke les 24 groupes + 5 commandes paresseux pour lire leur description courte, ce qui les importe tous. Mesuré (5 exécutions, `/usr/bin/time`) : `grimoire --help` 530-540 ms → 120-130 ms (plancher mesuré = `grimoire --version`, ~130 ms, coût d'interpréteur incompressible hors périmètre de ce lot) ; `grimoire --help --all` reste à 500-560 ms, inchangé. Détection de `--all` indépendante de l'ordre des flags : lue depuis les arguments bruts dans `_RootHelpGroup.parse_args`, pas depuis `ctx.params` (l'eager processing de click suit l'ordre de saisie, pas l'ordre de déclaration — `--help --all` et `--all --help` donnaient un résultat différent avec la première implémentation). `docs/getting-started.md` réécrit autour des cinq commandes (nouvelle section en tête, table `grimoire --help --all`/`docs/cli-reference.md` en aval remplaçant l'ancien tableau de 27 lignes qui contredisait le message). Tests : `tests/unit/cli/test_help_first_hour.py` (garde de dérive — `_FIRST_HOUR_COMMANDS` ne peut pas dépasser cinq entrées ; rendu par défaut vs `--all` ; sous-commande non affectée) et deux gardes anti-régression d'import dans `tests/unit/cli/test_lazy_startup.py` (`--help` ne fuite aucun module lourd, `--help --all` continue de tous les importer — preuve que le gain n'est pas un registre cassé).
- fix(ci): le job « Framework Tools Tests (pytest, windows-latest) » (`.github/workflows/ci-validate.yml`) stagnait ~9 minutes sans progression pytest à chaque commit de `main` depuis au moins le 14 septembre 2026 (`timeout-minutes: 15`, annulé aléatoirement — PR #590, #591), toujours au même point de la collecte (juste après `tests/test_cmd_init.py`), absent sur `ubuntu-latest`/`macos-latest`. Cause confirmée par les journaux d'un run réel (`gh api .../actions/jobs/<id>/logs`, trou de 09:13:17 à 09:22:31 entre 23 % et 25 % de la collecte) recoupés avec l'ordre de collecte local : `TestInitCLI`/`TestInitNoCockpit` (`tests/test_cmd_init.py`) invoquent `-y init` sans `--backend` explicite sur une vingtaine de cas qui n'ont rien à voir avec la détection mémoire (dry-run, archétypes, sortie JSON, `.gitignore`…) — ce qui déclenche pour de vrai `detect_memory_backend()` (issue #496), jusqu'à six sondes HTTP/TCP non mockées vers `localhost` (Weaviate :8080, Qdrant :6333, Ollama :11434, jusqu'à 2 s de timeout chacune). Un port loopback fermé refuse instantanément sous Linux ; pas sous le runner Windows hébergé, où chaque sonde paie son timeout en entier — exactement le risque que `tests/conftest.py::_init_real_project` documentait déjà pour la fixture `real_project` (issue #493) sans que `TestInitCLI` en hérite. Correctif : nouvelle fixture `_stub_unreachable_memory_services` (autouse, portée aux deux classes concernées) qui neutralise `_is_weaviate_reachable`/`_is_qdrant_reachable`/`_is_ollama_reachable` par défaut — les tests qui testent réellement la détection (`TestDetectMemoryBackend` et consorts) gardent la main via leur propre `patch(...)`, plus spécifique, qui l'emporte. Extraction de `_is_ollama_reachable()` (`src/grimoire/cli/cmd_init.py`), jusqu'ici un `_http_ok(...)` en ligne dans `detect_memory_backend()` sans rien à patcher isolément, sur le même patron que les deux fonctions sœurs. `--durations=25` ajouté au job pytest des deux OS pour garder cette visibilité à l'avenir. Preuve : job Windows 13m58s-14m24s avant sur `main` (runs 2026-09-15/17), confirmé à 13m48s sur cette branche avant correctif (`--durations=25 -v` : chaque test `TestInitCLI`/`TestInitNoCockpit` mesuré à 24,13-25,23s) → **3m43s** après correctif (même branche, même runner) ; `ubuntu-latest` inchangé (1m21s, le trou n'existait déjà pas côté Linux).

## [3.54.0] - 2026-09-17

- docs(security): nettoyage code scanning, lot 2 — triage (phase 2 du plan produit 2026-Q4, épic #552, suite de la PR #573). Le constat de départ (« ~50 alertes, presque toutes py/path-injection ») était largement dépassé : 402 alertes ouvertes au moment du triage, cause identifiée dans `.github/workflows/codeql-analysis.yml` — `queries: security-and-quality` charge ~250 requêtes de qualité/style (imports inutilisés, `except: pass`, retours mixtes…) déjà couvertes par le gate `ruff`/`mypy --strict` bloquant de cette CI, en plus des ~150 alertes de sécurité réelles ; le bruit est ce qui rend le check illisible. Narrowing vers `security-extended` (garde toutes les requêtes de sécurité, retire les requêtes de qualité). `docs/security/code-scanning-triage-2026-09.md` : méthode et preuve du triage des `py/path-injection` restants — lecture directe de chaque fichier signalé, constat de trois mécanismes de confinement de chemin déjà en place et non reconnus par CodeQL comme sanitizers (`resolve_within_allowed`/`allowed_roots` dans `project_registry.py`, `_blueprint_path`/`SLUG_RE` dans `forge_server.py`, `_ensure_inside_root` dans `agentic_standard.py`), et pour les fichiers sans ces helpers, un paramètre racine systématiquement dérivé de la CLI/config locale de l'opérateur, jamais d'un réseau distant. Alertes `py/path-injection` restantes dismissées `false positive` avec justification précise ; alertes de qualité/style dismissées `won't fix` (hors périmètre sécurité, doublon du gate existant). Alerte historique #223 (`py/command-line-injection`, `cmd_cockpit.py`) déjà dismissée le 2026-08-27 par Guilhem, rien à faire. Objectif de fait : 0 alerte ouverte non justifiée après ce lot, check CodeQL vert sur cette même PR.
- perf(cockpit): la fiche Piloter faisait encore HUIT appels réseau à son premier rendu, dominée par `doctor` (issue #548, phase 2 du plan produit 2026-Q4, épic #552, lot 2 — suite de #542/#547). Cause du cache `proposals` inopérant CONFIRMÉE en lisant `sync_proposals()` (`src/grimoire/proposals.py`) : les branches `keep_rejected` et `refresh_pending` appelaient `_save_proposal()` INCONDITIONNELLEMENT à chaque appel, même quand rien n'avait changé — cette écriture bouge la mtime du dossier `_grimoire-output/proposals/`, or c'est exactement ce que surveille la signature de `proposals_view()` (`grimoire.tools.view_cache`) : auto-invalidation à chaque lecture, le cache ne pouvait jamais faire hit (mesuré 0,46s→0,52s→0,49s, jamais 0,00s). Corrigé : les deux branches comparent désormais le `Proposal` recalculé à l'existant (`frozen=True` → égalité de valeur native) avant d'écrire ; nouveaux tests rouges avant/verts après dans `tests/unit/test_proposals.py` (mtimes des fichiers de propositions identiques avant/après une resynchronisation sans rien de nouveau) et au niveau vue dans `tests/unit/test_workspace_api.py`. Mesuré ensuite (micro-benchmark `time.perf_counter`, projet réel) : `kit_alignment()` (`project_health.py`) domine `/api/health` à elle seule (~120ms, sha256 par fichier shipé du kit à CHAQUE appel) ; `doctor_view()` (`workspace_exec.py`, relance un `grimoire doctor` complet en sous-processus) est en réalité la route la PLUS chère des sept, ~350ms — jamais mesurée jusqu'ici. Les deux gagnent le même patron de cache que `agents_view` (`view_cache`, signature de mtimes des dossiers pertinents — `_grimoire/kit` pour `health`, config/agents/skills/kit/overrides/wrappers VS Code/policies pour `doctor`) avec `?probe=1` pour forcer un recalcul frais (même câblage que `memory_link_view`). Nouvelle route agrégée `GET /api/workspace/sheet?project=` (`workspace_api.sheet_view`, câblée dans `workspace_routes.py::GET_ROUTES`) : santé, mémoire (mode rapide), agents, propositions, dernier run du wizard, dernier run de flow `project-upgrade` et nom du projet (registre) calculés en parallèle (`ThreadPoolExecutor`, même principe que `fleet_status`) en une seule réponse — `doctor` volontairement absent (mesuré comme le vrai coût des sept, l'onglet Problèmes le rend séparément, à la demande). `web/workspace/spaces/piloter.js::loadSheet()` remplacé par un unique `ctx.api.sheet(slug)` ; le nom du projet (cockpit) vient désormais de `sheet.name`, plus de second appel `projects()`. Mesures avant/après (harnais e2e `tests/e2e/test_workspace_piloter_perf.py`, projets jetables) : fiche Piloter 8 appels réseau → 1 (`/api/workspace/sheet`), interactive en ~811-849ms → ~110ms (seuil du test : < 400ms, critère d'arrêt de l'issue).
- fix(security): nettoyage code scanning, lot 1 — correctifs (phase 2 du plan produit 2026-Q4, épic #552). **(a)** `ForgeAPI.blueprint_compile` (`src/grimoire/tools/forge_server.py`) construisait `artifact_rel`/`artifact_path` à partir de `blueprint["id"]` (issu du corps d'une requête HTTP cockpit, `POST /api/blueprints/<id>/compile`) *avant* le premier appel à `_blueprint_path`, seule fonction du module à valider un id de blueprint (`SLUG_RE` + confinement sous `.github/prompts/`) — un id contenant `../` écrivait le mission pack hors du projet (CodeQL py/path-injection, alertes réelles, pas un faux positif). Même garde `SLUG_RE` appliquée dès l'extraction de `bp_id`, réutilisée telle quelle plutôt que dupliquée. `blueprint_validate`/`blueprint_lint` (mêmes routes cockpit, ouvertes en lecture sur toute la flotte sans garde `_HOME_SLUG`) laissaient aussi un `ref` de node (`artifact`/`composite`/schéma de gate) avec `../` transformer une vérification d'existence en oracle de fichier arbitraire sur la machine — `_schema_resolves`, la vérification d'artefact et celle de sous-blueprint confinent désormais le chemin résolu sous `project_root` avant tout `.exists()`/`.is_file()` (même message « absent » pour un chemin manquant et pour un chemin qui s'échappe, aucune fuite de bit). **(b)** `_maybe_register_cockpit` (`src/grimoire/cli/cmd_init.py`) journalisait `target` (nom de dossier fourni par l'appelant) avec `%s` — un nom de dossier contenant un retour à la ligne pouvait forger une fausse entrée de log (py/log-injection) ; `%r` (repr) échappe désormais `\n`/`\r`. **(c)** `ensure_go_toolchain` (`scripts/bench/three_arms.py`) appelait `tarfile.extractall()` sans filtre sur l'archive Go téléchargée (py/tarslip) — même une archive officielle pinnée reste vulnérable en cas de compromission de la source ou de MITM ; extraction factorisée dans `_extract_go_archive` avec `filter="data"` (refuse tout membre `../`/absolu), désormais testable sans réseau. Deux constantes `_NODE_RE` mortes retirées (`tests/unit/test_flows_composite.py`, `tests/unit/test_flows_extract.py`, py/unused-global-variable). Tests rouges avant/verts après pour les trois correctifs de sécurité (`tests/unit/tools/test_forge_server.py::test_compile_rejects_path_traversal_id`, `tests/test_cmd_init.py::TestMaybeRegisterCockpitLogging`, `tests/unit/test_bench_three_arms.py::test_extract_go_archive_rejects_a_path_traversal_member`) ; suite complète revérifiée verte, aucun changement de comportement pour un chemin/id légitime. Triage documenté des alertes restantes (lot 2, PR séparée) : `docs/security/code-scanning-triage-2026-09.md`.
- feat(bench): harnais du banc à trois bras (issue #551, plan produit 2026-Q4 phase 1 lot 1.1) — `scripts/bench/three_arms.py` compare Claude Code nu, Claude Code + ecc (github.com/affaan-m/ECC, MIT, plugin scope projet) et Claude Code + grimoire-kit (`grimoire init --backend local --no-cockpit` + `host sync`) sur un sous-ensemble reproductible du benchmark polyglot d'Aider (20 exercices Exercism, 5 par langue sur Python/JavaScript/Go/Rust, graine fixée `551`, k=3 rejeux, ordre des bras tiré au sort par tâche). Garde-fous par run (timeout 15 min, détection « tourne en rond » sur commandes Bash répétées via le flux `stream-json`), critère d'arrêt statistique (IC bootstrap disjoints entre bras sur le succès), rapport `report.md`/`report.json` (succès, pass^k, temps médian, coût médian par tâche résolue, IC95%). `--dry-run` prépare les 60 dépôts de tâche sans appel modèle ; `--pilot` (2 tâches x 3 bras x 1 run) écrit le coût attendu avant `--full` (20 x 3 x k=3) ; alerte (sans bloquer) si le coût réel dépasse 3x l'attendu. Isolation : `HOME` jetable par bras avec les identifiants Claude Code du poste recopiés le temps d'un run puis effacés dans un `finally` (jamais lus/affichés, jamais laissés à demeure — balayage de fin de campagne), jamais d'écriture dans `~/.grimoire` ni le `HOME` réel. Tests unitaires sans LLM ni réseau (`tests/unit/test_bench_three_arms.py`) : tirage reproductible, préparation de dépôt de tâche (tests cachés), détection de succès sur tests verts/rouges, calcul de pass^k et de son IC, détection de boucle, nettoyage des identifiants sur succès/exception. Protocole complet dans `docs/bench-three-arms.md`.
- fix(status): `kit_alignment()` (`src/grimoire/tools/project_health.py`) affichait « aligné sur 3.46.0 » pour un projet à jour en 3.51.1 (issue #519) — `aligned` date un contenu encore présent à sa *première* introduction au catalogue de digests, pas au dernier passage réel de l'outil ; un projet dont l'essentiel n'a pas changé depuis une vieille release se lisait donc comme en retard. `grimoire init`/`up` (`ProjectScaffolder`) écrit désormais un marqueur `_grimoire/kit/.up-version` (tier kit, régénéré à chaque `up` comme le reste de cette arborescence — jamais figé comme `project-context.yaml`) avec la version de l'outil qui vient de tourner ; `kit_alignment()` le lit dans un nouveau champ `upVersion`, seule source ajoutée (jamais une seconde estimation). `tool_version_gap()` (doctor `tool_version`, #515) compare désormais l'outil courant à `upVersion` en priorité — correctif de fond, pas seulement d'affichage : un outil dont la version tombe *entre* la première introduction d'un contenu et le vrai dernier `up` se disait à tort « à jour » en comparant à l'ancienne référence. La fiche Piloter (`web/workspace/spaces/piloter.js`) affiche « à jour (kit X) » quand `upToDate` et `upVersion` sont connus, avec « contenu inchangé depuis Y » en détail secondaire si `aligned` diffère ; repli inchangé (« aligné sur X, installé Y », #288) sur un projet sans marqueur connu. Tests rouges avant (`tests/unit/test_project_health.py`, `tests/test_scaffold.py`) : `KeyError: 'upVersion'` sans le marqueur, faux « pas en retard » avec un outil intermédiaire entre les deux références ; `tests/unit/cli/test_doctor_tool_version.py` et l'e2e `test_piloter_kit_reel_a_jour_dit_a_jour_avec_le_kit_du_dernier_up` (`tests/e2e/test_workspace_lot4_spaces.py`) mis à jour pour la nouvelle référence.
- fix(cockpit): `POST /api/blueprints/<id>/compile` manquait de `_CockpitHandler` (`src/grimoire/cli/cmd_cockpit.py`), issue #546, même défaut que `PUT` avant #543 : `do_POST` n'avait aucune branche pour `/compile`, réponse 404 « route inconnue » avant même d'atteindre une garde — sur le seul serveur qui tourne réellement pour `grimoire serve` et `grimoire cockpit serve` depuis #351. Câblée avec la même garde que `/api/setup` et `PUT` : `blueprint_compile` écrit un artefact `.prompt.md` sur disque et persiste la section `compiled` dans le blueprint d'origine, ce n'est pas un calcul comme `/validate`/`/simulate` — seul le projet de lancement direct (`_HOME_SLUG`) peut donc compiler, 403 sinon. `/validate` et `/simulate` étaient déjà câblés et volontairement sans cette garde (calcul pur) : confirmé avec un vrai handler plutôt que supposé. `grimoire blueprint evals` vérifié comme commande CLI, jamais une route HTTP ni sur le cockpit ni sur l'atelier — rien à câbler. Nouveau `tests/unit/cli/test_cmd_cockpit_blueprint_writes.py`, paramétré sur les trois routes (200 home, 404 blueprint inconnu, 403 hors home pour `/compile` seulement, 400 blueprint bloqué) contre un vrai `_CockpitHandler` — rouge avant, vert après. `docs/cockpit-coverage-matrix.md` mis à jour (`/compile` et `PUT` passent « oui », étiquette « atelier only » de `PUT` corrigée, périmée depuis #543) et résumé régénéré par `scripts/cockpit-coverage.py` (Routes API 17→18, Concevoir 15→16, total 77→78 %).
- fix(archetypes): `minimal` et `agentic-standard` (`archetype.dna.yaml`) référençaient `project-navigator` et `memory-keeper` (issue #550), retirés du paquet par la refonte meta #387 (commit `f636633c`) sans mise à jour des DNA en aval — `grimoire init --archetype minimal` déclarait deux agents qu'il ne pouvait pas installer. Vérifié sur `git log -S project-navigator -- archetypes` : retrait volontaire (savoir-faire porté depuis par les skills `meta-project-navigation`/`meta-memory-quality` attachés à `agent-optimizer`), donc retrait des références plutôt que rétablissement. `checked_by: memory-keeper` réaligné sur `agent-optimizer` dans les deux DNA. Nouveau test `tests/unit/registry/test_archetype_dna_agents.py`, paramétré sur tous les `archetype.dna.yaml` livrés : échoue si un agent référencé n'a pas de fichier `.md` livré (rouge avant ce correctif, vert après) — attrape aussi tout futur cas.
- docs(veille): lots 1.2 et 1.3 de la phase 1 du plan produit 2026-Q4 (issue #551). `docs/veille/anthropic-direction-2026-09.md` : verdict chevaucher/envelopper/ignorer par capacité Anthropic récente (Agent Teams, Managed Agents, subagents, skills, plugins/marketplace, hooks, mémoire, sessions/reprise, tâches en arrière-plan, sandbox, computer use, prompt caching, MCP Apps), sources officielles lues le 2026-09-16 (`code.claude.com/docs`, `platform.claude.com/docs`, `github.com/anthropics/skills`, changelog Claude Code) — chevauche déjà Agent Teams via le genre de flow `fanout` (`src/grimoire/flows/genres.py:246`, preuve et coût que l'expérimental Anthropic n'a pas). `docs/veille/idees-a-recolter-2026-09.md` : instruction des huit idées du plan (§6) avec fichier source et licence vérifiés dans chaque dépôt concurrent (affaan-m/ECC 292 skills/68 agents, SuperClaude, cline/cline, langchain-ai/langgraph, mastra-ai/mastra, bmad-code-org/BMAD-METHOD), deux corrections (licence Mastra Apache-2.0 et non Elastic-2.0 ; le gate `grimoire-skill-analyzer` cité par le plan est un outil de la Forge, absent de `src/grimoire/` — écart réel de la phase 5.5, pas une capacité déjà livrée), une neuvième idée ajoutée (gate de confiance pré-implémentation, skill `confidence-check` de SuperClaude). Section « Révision 2026-09-16 » de `framework/agentic-industry-reference.md` complétée avec un lien vers les deux documents.
- fix(memory): `memory up --profile` et le schéma parlaient deux vocabulaires (issue #527, phase 2 du plan produit 2026-Q4, épic #552). **(a)** La CLI acceptait `lexical|vector|full` (`grimoire.tools.memory_setup`) pendant que le schéma et `grimoire.memory.profiles` parlent `lexical|standard|graphe|complet` (`layer_profile`) — `memory up` n'écrivait jamais `layer_profile` et laissait `retrieval_mode` sur son défaut (`vector`) même en profil `full`, faisant croire au cockpit et à `memory status` qu'un projet passé en composition `complet` tournait encore en `standard`. `vector`/`full` sont maintenant des alias résolus par `grimoire.memory.profiles.ALIASES` vers `standard`/`complet` ; `build_memory_plan` écrit toujours `layer_profile`/`retrieval_mode`/`vector_database` d'après ce que la machine sert réellement (`_finalize_layers`, jamais d'après le profil demandé — un `complet` sans Neo4j ni Redis s'écrit `standard`, sans aucun service vectoriel s'écrit `lexical`). **(b)** `memory migrate plan` retombait sur `cfg.memory.backend` (le backend COURANT) faute de `migration_source_backend` — bug nommé par l'issue : un plan ne peut pas migrer un backend dans lui-même. `memory up --apply` laisse maintenant une trace du backend qu'il quitte (`_grimoire/_memory/migration/previous-backend.json`, `write_previous_backend_breadcrumb`) ; `plan` la lit et l'expose (`source_known`). Nouvelle commande `memory migrate run [--source] [--apply]` : lit n'importe quel backend Memory OS via l'API publique du manager (`get_all`) et réécrit dans le backend courant (`store_many`, qui recalcule ses propres embeddings) — remplace le couple manuel `memory export` + `memory import`, seul chemin qui fonctionnait jusqu'ici pour un lexical → vecteur. **(c)** `memory graph sync-memories` est additif seulement : un souvenir supprimé du store, ou un essai antérieur écrit sous une autre collection Weaviate, laisse des nœuds `GrimoireMemory` orphelins pour toujours (cas réel observé sur la Forge : store=75, graph=3782). Nouvelle commande `memory graph purge-orphans` (dry-run par défaut, `--apply` pour supprimer), bornée à la collection du projet et à l'ancienne collection générique `GrimoireMemory` d'avant les profils (`Neo4jMemoryGraph.find_orphan_memory_nodes`/`purge_memory_nodes`) — jamais un scan non borné qui risquerait un autre projet partageant la même instance Neo4j. **(d)** `RedisHotMemory` namespace déjà ses clés par `collection_prefix`, mais un projet resté sur le défaut partagé (`grimoire`) collisionnait avec tout autre projet dans le même cas sur une instance Redis mutualisée (constat réel : Forge sur `redis://localhost:6379/0` partagé avec un autre projet du poste) — `_redis_namespace` (`grimoire.memory.manager`) retombe désormais sur le slug du nom de projet quand le préfixe est encore générique, et `memory up` le rapporte (`plan.notes`, nouveau champ). Tests rouges avant (backends mockés — driver Neo4j factice, `store_many` factice, aucun service réel) : `tests/unit/tools/test_memory_setup.py`, `tests/unit/memory/test_migration.py`, `tests/unit/memory/test_neo4j_graph.py`, `tests/unit/memory/test_projections.py`, `tests/unit/memory/test_manager.py`, `tests/unit/cli/test_cmd_memory.py`, `tests/unit/cli/test_cmd_memory_ops.py` (nouveau).
- fix(tests): `test_status_non_resident_declenche_un_chargement_sans_generer_de_texte` (`tests/unit/test_source_assist.py`) était instable en CI (`assert 2 == 1`, issue #529). Cause confirmée par une reproduction isolée (frontière exacte entre la fin d'un test et le début du suivant) : `_trigger_warm_up` (`src/grimoire/tools/source_assist.py`) démarre un thread démon fire-and-forget que ni `assist_status` ni les tests ne joignent ; un test précédent qui déclenche un chargement (`running = ()`) puis rend la main avant la fin de ce thread laisse ce dernier écrire dans `_FakeOllama.generate_calls` (classvar partagée entre tous les tests) pendant le test SUIVANT, une fois la liste réinitialisée par la fixture `fake_ollama` — d'où tantôt 1 tantôt 2 entrées vues par l'assertion suivante, selon l'ordonnancement. Code de production non modifié : `_trigger_warm_up` ne fait déjà qu'un seul appel réel par déclenchement (garde `_WARM_UP_INFLIGHT`), vérifié en lisant le code. Correctif : nouvelle fonction `_drain_warm_up()` dans le test, qui attend que `_WARM_UP_INFLIGHT` se vide avant que la fixture ne réinitialise l'état partagé et n'arrête le serveur — plus aucun thread en vol ne peut polluer le test suivant. Preuve : harnais de reproduction isolé montrant la contamination à 10/10 sans le correctif et 0/10 avec ; suite complète relancée 15 fois sans échec après correctif.
- docs(plan): axes phase/finition et délégation optimale dans le plan produit 2026-Q4 (décisions Guilhem du 2026-09-16 matin, suite à #558). `docs/plan-2026-q4.md` §2 : métriques 4 et 5 deviennent des métriques de délégation (part des tâches résolues au palier de départ, escalades justifiées vs subies, temps avant demande d'aide, boucles détectées), le coût par tâche et pass^k passent en métrique de conséquence (4bis) — le banc compare qualité puis temps puis coût. §3 : le banc à trois bras s'arrête sur clarté statistique (IC disjoints ou n suffisant), alerte (jamais un blocage) à 3× le coût attendu, jamais un plafond en dollars. §4 phase 4 réordonnée (préalable #521/#559 unification des tâches, puis phase et finition déclarées #560, propagation aux tâches et au kanban #561, pilote adaptatif #562, boucle de finition + juge #563, politique de question #564, reçu portable, rejeu diff, skills, cascade coût visible) ; deux nouvelles sections "Phase et finition" (deux axes, tableau phase × finition par défaut, cinq moments où l'utilisateur choisit) et "Délégation optimale" (signaux/actions du pilote, formes d'équipe). Issue #551 mise à jour avec la définition complète du banc (bras Claude Code nu/+ecc/+grimoire-kit, jeu de tâches Aider polyglot/Exercism, 20 tâches extensible, arrêt statistique). Six nouvelles issues #559-#564 (une par lot 4.1-4.6).
- docs(plan): plan produit 2026-Q4 (issue #557, epics #551-#556). Cible développeur seul/entreprise/équipe, promesse « preuve, coût, portabilité », dix métriques de succès mesurées sur un projet jetable le 2026-09-16 (`grimoire --help` : 49 commandes de premier niveau ; `grimoire-hook` PreToolUse : ~50 ms ; `grimoire init` : 0,24 s ; SessionStart `additionalContext` : 1409 caractères ≈ 352 tokens ; PreToolUse `additionalContext` pour Claude Code : 0 par construction, `src/grimoire/hosts/runtime.py:166-174` ; 2 émetteurs d'hôtes riches sur 5 hôtes cibles ; 20 skills et 15 agents livrés dans les 10 archétypes), sept phases (0 à 6) et un transversal, matrice de parité reprise de la veille avec colonne phase, registre des risques (waivers chromadb corrigés à `2027-02-28`, `.github/security/dependency-waivers.yaml`), section « Révision 2026-09-16 » ajoutée à `framework/agentic-industry-reference.md` recentrant la référence sur le segment développeurs-sur-un-hôte.

## [3.53.0] - 2026-09-15

- perf(cockpit): moitié front de la perf Piloter (issue #541, suite du backend #542 déjà mergé). (1) `loadFleet()` (`web/workspace/spaces/piloter.js`) appelait encore `projects()` PUIS `health()`/`memoryStatus()` par projet du registre — remplacé par un unique appel `ctx.api.fleet()` (`GET /api/fleet`). (2) `mount()`/`draw()` appelait `ctx.api.projects()` (nom du projet, cockpit) en SÉRIE avant `loadSheet()` — les deux partent désormais en même temps (`Promise.all`). (3) `draw()` peignait un écran vide jusqu'à la résolution complète des appels — une coquille « Chargement… » s'affiche maintenant tout de suite, retirée avant le rendu réel (jamais empilée dessous). (4) Un `draw()` déclenché pendant qu'un tour précédent tourne encore rejoint désormais la même promesse (`inFlight`) plutôt que d'en relancer un second en concurrence — prouvé par un test qui chevauche deux rafraîchissements forcés au niveau DOM (rouge sans la garde : 2 appels `/api/fleet` ; vert avec : 1) ; corrigé en cours de route pour ne jamais avaler un changement de cible légitime survenu pendant ce tour (clic de zoom Flotte/Projet), attrapé par le harnais générique de bascule de segments : un rendu frais s'enchaîne désormais après coup UNIQUEMENT si la cible a réellement changé, jamais pour un second déclenchement identique. (5) `ctx.api.memoryStatus()` accepte désormais `{ probe: true }` (`GET /api/memory/status?probe=1`) ; nouveau bouton « Sonder la mémoire » dans la fiche Piloter, seul déclencheur d'une sonde réseau fraîche (jamais de `setInterval`), mise à jour ciblée (pas de redessin de toute la fiche). Complément mineur côté backend (pas une réouverture de #542) : `_fleet_entry()` (`project_health.py`) manquait `name` et `managed` — la Flotte (`renderFleet`/`watchReasons`) les lit sur chaque entrée et n'a plus d'autre appel pour les obtenir depuis que `loadFleet` ne fait plus qu'UN appel réseau. Le cache 60s de la Flotte n'était pas non plus invalidé après création d'un projet depuis ce même écran — corrigé.

  | Scénario (harnais e2e, 3 projets jetables) | Avant | Après |
  |---|---|---|
  | Flotte — appels réseau (registre à froid) | 7 (`projects` + 3×`health`+`memoryStatus`) | 1 (`fleet`) |
  | Flotte — temps interactif | ~269 ms | ~262 ms |
  | Fiche Piloter — appels réseau | 8 | 8 (inchangé, hors périmètre — voir ci-dessous) |
  | Fiche Piloter — étalement des départs de requête (parallélisme réel) | ~12,7 ms | ~1,3 ms |
  | Fiche Piloter — temps interactif | ~811-849 ms | ~813-850 ms (dominé par le backend, voir ci-dessous) |
  | Rafraîchissement Flotte chevauché (forcé) — appels `/api/fleet` | 2 | 1 |

  Mesures par `tests/e2e/test_workspace_piloter_perf.py` (rouge sur le code d'avant, vert après), pas inventées. Deux écarts documentés par rapport à la cible initiale de l'issue, les deux hors périmètre d'une PR front-only : le budget d'appels de la fiche Piloter reste à HUIT (`loadSheet` en porte SEPT depuis #513, un chantier antérieur — le ramener à SIX demanderait d'y retirer un appel, pas d'en changer l'ordonnancement) ; le seuil d'interactivité de la fiche vise 700 ms dans l'issue mais plafonne à ~810 ms sur ce harnais quel que soit l'ordonnancement front, la réponse la plus lente des huit endpoints (`doctor`/`health`, IO disque réelle) dominant le total — le gain mesuré ici est la disparition du petit aller-retour série (`projects()` avant `loadSheet`), pas le temps total.
- test(cockpit): lot B de couverture systématique — raccourcis et palette (issue #536). Dix tests e2e nouveaux (`tests/e2e/test_workspace_shell_palette_actions.py`) : les six raccourcis `⌘1`–`⌘6` (changement d'espace, jamais pressés réellement — seul `GrimoireWorkspace.goto` direct l'était), le raccourci `` ` `` (ouvre la Console du dock), et l'exécution réelle (pas seulement l'ouverture) des trois sections de palette restées non exercées : « Tâches » (`grimoire task show`), « Workflows » (navigation Concevoir), « Projets » (rechargement `?project=`). Constat en cours de route, pas un bug : `buildPalette()` (`shell.js`) ne se reconstruit qu'au chargement de la page, jamais à l'ouverture — une tâche/un blueprint créé après coup n'apparaît dans la palette qu'après rechargement (documenté dans la matrice). Résumé chiffré régénéré : 163 lignes, 124 couvertes (76 %, coque à 97 %).
- fix(cockpit): Piloter n'affichait ni ne rafraîchissait jamais les propositions en attente après une mise à jour confirmée qui échoue en `upgraded-but-failed` (issue #538, `xfail(strict=True)` posé par le lot A de couverture ci-dessous). `project_update.py::_run_upgrade_flow` joint déjà `report`/`proposals` sur cet état précis, mais le handler du bouton « Confirmer la mise à jour » (`piloter.js`) gardait l'affichage du nombre de propositions ET son rafraîchissement (`refreshProposals()`) derrière `result.ok` — or `state: "upgraded-but-failed"` implique toujours `ok: false` (`_derive_state`), donc les deux blocs restaient systématiquement morts sur cet état, alors qu'ils fonctionnent pour `completed`/`upgraded-checkpoint-pending`. Les deux gardes portent désormais sur la présence réelle du champ (`Array.isArray(result.proposals)`), indépendamment de `result.ok` ; `markKitCheckpointPending`/`checkpointPendingRunId` restent réservés au succès (le nœud `destructive` n'est jamais atteint sur un run interrompu à `apply`). Retire le `xfail` de `test_confirmer_upgraded_but_failed_devrait_rafraichir_les_propositions` — rouge avant, vert après.
- test(cockpit): lot A de couverture systématique — écritures et décisions (issue #536). Six tests e2e nouveaux pour les six `artifact_type` de proposition (`tests/e2e/test_workspace_proposals_types.py`, issue #490) : jusqu'ici seul le type `agent` du déclencheur passait par un vrai clic Piloter + la route `POST /api/workspace/proposals/<slug>/accept`, les cinq autres (`skill`, `repair` avec/sans substitution, `override-migration`, `memory-link`, `needs-hosts`) n'étaient prouvés qu'au niveau moteur. Un test pour le repli « presse-papiers indisponible » du bouton « Revoir dans l'IDE » (`tests/e2e/test_workspace_piloter_review_button.py`) et un pour l'aperçu en échec de la mise à jour (`tests/e2e/test_workspace_cockpit_upgrade_flow_states.py`). Un bug confirmé et marqué `xfail(strict=True)` en attendant correction : Piloter n'affiche ni ne rafraîchit jamais les propositions en attente après une mise à jour confirmée qui échoue en `upgraded-but-failed`, alors que la route les renvoie déjà (#538). Deux corrections de la matrice (deux lignes marquées à tort « non » en PR précédente : la garde 404 sur projet hors registre et les erreurs de création de projet sont déjà couvertes au niveau route). Résumé chiffré régénéré : 162 lignes, 116 couvertes (72 %, +8 points).
- perf(cockpit): l'espace Piloter était lent à charger (mesuré sur le cockpit réel : `/api/memory/status` 0,88s, `/api/workspace/agents` 1,58s, `/api/workspace/proposals` 0,46s ; aucun cache n'existait nulle part, côté serveur comme côté client). (1) `memory/status` : `MemoryManager.health_check()` sondait Weaviate/backend, Redis et Neo4j SÉQUENTIELLEMENT, sans timeout — désormais en parallèle (`ThreadPoolExecutor`), chaque sonde bornée à 300ms (`DEFAULT_PROBE_TIMEOUT_SECONDS`) : un service injoignable ne fait plus jamais attendre la route plus que ce délai. `memory_link_status()` gagne un mode rapide par défaut (`probe=False`) qui ne sonde JAMAIS le réseau : il rend le dernier statut sondé (nouveau cache process `grimoire.memory.health_cache`, TTL 25s, invalidé sur toute écriture mémoire — `store`/`upsert`/`update`/`delete`/`store_many`/facts/diary) avec `probedAt`/`probed`/`stale` explicites ; `probe=True` (`GET /api/memory/status?probe=1`) force une sonde fraîche. Trouvé en profilant sur un projet jetable avec Neo4j réellement injoignable : la CONSTRUCTION du client Neo4j (`Neo4jMemoryGraph.__init__` appelle `ensure_schema()`, un vrai aller-retour Cypher) peut à elle seule bloquer ~30s via le propre backoff du driver, avant même que `health_check()` ne s'exécute — `MemoryManager.from_config()` la borne désormais aussi (`_create_memory_graph_bounded`, 3s), un no-op pour tout appel en mode rapide puisque celui-ci ne construit jamais de `MemoryManager`. Corrige au passage un bug latent trouvé par ce même profilage : `RedisHotMemory.health_check()` ne rattrapait que les exceptions builtin (`ConnectionError`/`TimeoutError`/`OSError`) — `redis.exceptions.ConnectionError` n'en dérive pas, et un Redis réellement injoignable crashait toute la route au lieu de rendre `healthy: False`. (2) `agents_view()`/`proposals_view()` (`grimoire.tools.workspace_api`) relisaient et re-résolvaient toute leur surface (agents/skills/overrides, propositions) à chaque appel — nouveau cache générique par signature de mtimes (`grimoire.tools.view_cache`), invalidation naturelle dès qu'un fichier surveillé change. (3) Nouvelle route agrégée `GET /api/fleet` (`grimoire.tools.project_health.fleet_status`, câblée dans `forge_routes.py`) : santé + mémoire (mode rapide) de tous les projets du registre en une seule réponse, calculés en parallèle — remplace les `2×N` appels HTTP que `web/workspace/spaces/piloter.js::loadFleet()` faisait un par un et par projet. Mesuré sur un projet jetable (backend local, Redis/Neo4j pointés vers une IP non routable) : `memory/status` 3108ms → 2,6ms (mode rapide, après une première sonde) ; `agents_view` 58ms → 0,3ms ; `proposals_view` 4,4ms → 0,0ms.
- docs(cockpit): matrice de couverture contrôles × états (issue #536). Inventaire exhaustif construit depuis le code (`web/workspace/*.js`, `workspace_routes.py`, `forge_routes.py`, `project_update.py`, `cmd_cockpit.py`, `proposals.py`), confronté aux tests réels (`tests/e2e/*.py`, `tests/unit/test_workspace_*.py`, `tests/unit/cli/test_cmd_cockpit*.py`, `tests/unit/test_project_upgrade.py`) — `docs/cockpit-coverage-matrix.md`, résumé chiffré régénéré (jamais estimé à la main) par `scripts/cockpit-coverage.py`, gardé synchronisé par `tests/unit/test_cockpit_coverage_matrix.py`. Deux régressions confirmées sur `origin/main` en construisant l'inventaire, chacune avec son issue : le bouton de rail « Preuves » (touche `3`) n'est enregistré par aucun espace (#534), et l'ajout d'un nœud depuis la Bibliothèque de Concevoir ne persiste jamais sur disque malgré le message de succès affiché (#535).
- fix(cockpit): le bouton « Preuves » du rail (touche 3) n'avait aucun effet, sur aucun des six espaces (issue #534, trouvé par la matrice de couverture contrôles × états ci-dessus). `triggerRailAction('evidence', …)` (`web/workspace/shell.js`) cherchait un handler que seul un espace pouvait enregistrer via `ctx.rail.on(…)` — comme « 2 »/bibliothèque, propre à Concevoir — mais aucun des six espaces n'enregistrait `'evidence'` : bouton mort et raccourci mort partout, contraire à la doctrine du cockpit (« aucun contrôle mort visible »). Contrairement à « 2 », « Preuves » n'a pas de sens propre à un espace : `evidence` rejoint désormais `PANELS` aux côtés d'Explorateur/Inspecteur, un panneau tenu par la coque elle-même — pas par ce qui est monté au centre — donc utilisable identiquement depuis les six écrans, avec la mécanique générique déjà existante des trois états (replié/entrouvert/épinglé, largeur mémorisée par espace et par projet). Nouveau panneau `#panel-evidence` (`index.html`) listant les tâches du standard gouverné, l'état de leurs gates et le chemin de leur pack — via `GET /api/workspace/evidence` (nouveau, `workspace_routes.py`/`workspace_api.py::evidence_view`), qui rejoue `grimoire.core.agentic_standard.check_evidence_gates` (la même lecture que `grimoire standard verify`/`grimoire_standard_gate`), jamais une relecture parallèle des fichiers ; `enrolled: false` nomme `grimoire standard init` quand le projet n'en a pas. Cliquer une tâche ouvre son pack dans Source (`goto('source', { file })`) — ce qui a exigé un quatrième étage Source, `evidence` (`_grimoire-output/evidence/`, lecture seule) : sans lui, `file_view` refusait (403) tout chemin sous `_grimoire-output`, le même échec que le bouton qu'il corrige. Nouveaux tests unitaires (`tests/unit/test_workspace_api.py`, nouvelle fixture `governed_project` dans `conftest.py` — `real_project` est mutée par `project_with_task` dans d'autres tests du même fichier, ce qui aurait rendu ceux-ci dépendants de l'ordre de collecte) et e2e (`tests/e2e/test_workspace_shell.py` : la touche 3 ouvre le panneau depuis Piloter, le clic sur une tâche ouvre Source — rouge avant le correctif, vert après).

- fix(cockpit): dans Concevoir, ajouter un nœud depuis la Bibliothèque ne persistait jamais malgré le message de succès affiché (issue #535, trouvé par la matrice de couverture ci-dessus). `addNode()` (`concevoir.js`) ne mutait que `state.blueprint` en mémoire ; corrigé en deux moitiés distinctes, trouvées l'une après l'autre. **Client** : `state.dirty`, posé par `addNode` et remis à `false` au chargement/à l'enregistrement — indicateur « modifié — non enregistré » dans la barre d'outils du graphe, bouton « Enregistrer » (`saveBlueprint()`, désactivé sans modification) qui appelle `ctx.api.blueprintPut` (jamais automatique), et confirmation avant de rouvrir un blueprint modifié (`zoomToWorkflow`, même mécanisme que `source.js::openFile`). **Serveur, trouvé en écrivant le test e2e** : même le client corrigé échouait encore — `_CockpitHandler` (`cmd_cockpit.py`), le seul serveur qui tourne réellement pour `grimoire serve` **et** `grimoire cockpit serve` depuis la fusion #351, ne définissait aucun `do_PUT` : `http.server` répondait 501 avant d'atteindre la moindre garde, y compris sur l'atelier (`readOnly: false`). `forge_http.py::do_PUT` (couvert par `tests/unit/tools/test_forge_server.py`) est un chemin mort en pratique — jamais celui qu'emprunte un navigateur réel. Nouveau `_CockpitHandler.do_PUT` : `PUT /api/blueprints/<id>` routé vers `blueprint_put`, même garde `_HOME_SLUG` que `/api/setup` (un projet du registre qu'on ne fait que regarder reste en lecture seule, #356). `blueprint_put` (`forge_server.py`) gagne aussi une garde structurelle — un corps dont `nodes`/`edges` n'est pas une liste d'objets levait une `AttributeError` non rattrapée (500) au lieu d'un refus explicite (400) — sans devenir une validation de schéma stricte : un nœud tout juste ajouté a un `ref` vide, « à compléter dans Propriétés », un brouillon légitime que le lint (informatif, jamais bloquant) laisse déjà passer. Nouveaux tests : `tests/unit/tools/test_forge_server.py` (structure), `tests/unit/cli/test_cmd_cockpit_blueprint_put.py` (200 sur le projet de lancement, 403 sur un autre projet du registre, 400 sur un corps invalide, 404 hors `/api/blueprints/`), `tests/e2e/test_workspace_concevoir.py` (nouvelle fixture `concevoir_scratch_blueprint` — jamais `workspace-demo`, partagé et jamais réinitialisé pour toute la session e2e : ajouter-et-enregistrer un nœud réel l'aurait pollué pour tous les autres tests du module) — indicateur affiché, persistance après rechargement, confirmation avant d'écraser un brouillon ; rouge avant chaque moitié du correctif, vert après.

## [3.52.1] - 2026-09-15

- feat(cockpit): thème plus coloré « de façon intelligente » (retour direct de Guilhem — « ça manque de couleur, ça paraît terne/sombre » en sombre, « trop sobre, sans vie, teintes trop proches » en clair). Les cinq niveaux de surface (`--bg`/`--e1`/`--bar`/`--e2`/`--e3`, `tokens.css`) avaient un rapport de luminance de 1,06 à 1,17 entre voisins — sous le seuil de lisibilité d'une superposition — dans les deux thèmes ; rouverts à ~1,30, encre secondaire (`--ink2`/`--ink3`) retenue en retour pour garder l'AA. `--acc` (interaction : survol, focus, onglet actif, ligne sélectionnée) valait `--ink` — aucune teinte propre, cause principale du rendu terne — reçoit désormais la teinte de la série 1 (bleu froid), distincte de `--pri` (toujours l'unique orange par écran). Nouveaux vocabulaires de couleur, toujours informatifs — jamais décoratifs, toujours point + mot ou filet fin : six teintes d'identité d'espace (`--id-piloter…--id-source`, filet sous l'onglet actif et liseré de panneau) et des badges sémantiques pour le genre de blueprint (Concevoir), le `risk_profile` des tâches (Exécuter) et le type de proposition — six valeurs distinctes, `agent`/`skill`/`repair`/`memory-link`/`override-migration`/`needs-hosts` — jusqu'ici confondues sous un unique point orange (Piloter). Corrige au passage un contraste limite préexistant : `.cv-node .kind`/`.cv-prim .d` (survol, Concevoir) passent de `--ink3` à `--ink2`. Nouveau test `tests/e2e/test_workspace_shell.py::test_les_surfaces_voisines_s_ecartent_assez_pour_se_distinguer` (rouge sur les tokens d'avant, vert après) mesure l'écart de surface sur le DOM rendu, dans les deux thèmes.
- feat(cockpit): thème, seconde marche plus franche (suite directe de la précédente entrée — accord de Guilhem : « accepté tel quel, puis une seconde marche plus franche »). Quatre changements, chacun mesuré : (1) les écarts de surface repassent de ~1,30 à 1,26-1,42 selon la paire, avec un vrai palier entre panneaux (`--e1`) et cartes (`--e2`) pour que ces dernières se détachent nettement (sombre : `--bar`/`--e2` 1,30→1,41 ; clair : `--bar`/`--e2` 1,42→2,06) ; `--bg` sombre reçoit une teinte froide (bascule R/B, luminance inchangée) et `--bg` clair remonte franchement près du blanc chaud. Coût documenté : l'écart `--ink`/`--ink2` (sombre) se resserre de 1,32 à 1,21. (2) `--acc` se voit désormais sur chaque élément actif, pas seulement une bordure fine : onglet (fond `--accsoft` + filet 3 px), vue (`.seg > button[aria-pressed]`, fond plein + nouveau `--onfill`), ligne sélectionnée (`.tree > .sel`, liseré 3 px ajouté), bouton par défaut (nouveau `.btn[aria-pressed="true"]`, fond plein), champ focus (`.input:focus-within`, nouveau). (3) Les statuts kit/CI/doctor/mémoire (Inspecteur et Flotte), le type de proposition et le nœud du déroulé passent du point + mot à la pastille pleine (`.chip.pill.<état>`, nouvelle primitive shell.css, fond saturé + `--onfill` — mesuré ≥4,5:1 sur les six couleurs, marge la plus juste 5,72:1 sur `--bad`) ; genre de blueprint et `risk_profile` restent en point + mot, hors du périmètre demandé. (4) Le filet d'identité d'espace devient un bandeau plein (`panel-head`, 2 px → 4 px) et le panneau qui a le focus clavier gagne un liseré élargi (`:focus-within`, 3 px, pas d'état posé côté JS). `web/DESIGN-SPEC-workspace-2026-09.md` et `tests/e2e/test_workspace_lot4_spaces.py` (sélecteur `.dot` → `.chip.pill`, même assertion) mis à jour ; harnais de contraste inchangé (texte AA 4,5:1, surfaces 1,25:1 minimum).
- feat(cockpit): thème clair, fond franchement blanc (troisième retouche, clair seulement — malgré des rapports de surface déjà conformes, `--bg` clair mesurait L* 94,7, encore perçu comme gris sur les captures des deux passes précédentes). `--bg` clair remonte à L* 96,5 (luminance 0,912, teinte chaude inchangée) ; `--e1`/`--bar`/`--e3` recomposés par le pas le plus court qui tienne 1,25:1 à chaque paire adjacente — pas un simple report proportionnel, pour rester clair plutôt que retomber dans le gris (`--bg`/`--e1` 1,34→1,27, `--e1`/`--bar` 1,34→1,27, `--bar`/`--e2` 2,06→1,77, `--e2`/`--e3` 1,43→1,27, toutes ≥1,25). `--e2` reste l'unique blanc pur. `--ink2`/`--ink3` inchangées : la surface la plus sombre qu'elles touchent (`--bar`) est désormais plus claire qu'avant, donc plus de marge — 6,67:1 et 5,44:1 mesurés sur `--bar`, contre 5,74:1/4,68:1 avant cette retouche. Thème sombre et pastilles (issues des deux passes précédentes) non touchés.
- feat(cockpit): thème clair, pile inversée (quatrième retouche, clair seulement). Remonter `--bg` trois fois n'avait rien changé à l'écran : `--bg` ne se voit qu'aux marges (`#canvas`), le gris perçu venait de `--e1` (`.pl-kpi`/`.pl-watch-row`/`.pl-table` — les cartes de contenu, L* 87) et `--bar` (`.pl-table th` — les en-têtes, L* 79). La pile est inversée comme un thème clair sobre : `--e1`/`--e2` (cartes) blancs ou quasi (L* 99/100), `--bg`/`--bar`/`--e3` (fond de page et barres) un gris clair proche (L* 90), chacun ≥1,25:1 de son voisin déclaré (`--bg`/`--e1` 1,27, `--e1`/`--bar` 1,26, `--bar`/`--e2` 1,30, `--e2`/`--e3` 1,27). `--bar` ne peut pas atteindre la bande visée à l'origine (L* 93-95) sans redescendre sous 1,25:1 face à `--e1` quasi blanc — contrainte algébrique documentée dans `tokens.css` plutôt que forcée. `--line` clair monte de .13 à .16 (bordures de carte plus marquées, sans le secours d'un fond gris derrière). `--ink2`/`--ink3` inchangées, marge élargie (surface la plus sombre qu'elles touchent, `--bar`, plus claire qu'avant : 9,08:1 et 7,40:1 contre 6,67:1/5,44:1). Sombre et pastilles non touchés.

## [3.52.0] - 2026-09-15

- fix(cockpit): cinq contrôles inertes ou muets de la vue de travail, trouvés par un audit factuel (issue #523). (1) Observer (`spaces/observer.js`) appelait `ctx.docbar.setViews([...], 'runtime')` sans le callback `onPick` (3e argument) — Runtime/Activité/RTK/Bench ne réagissaient à aucun clic ; les trois onglets secondaires n'avaient de toute façon aucun contenu prévu (dead code), ils rendent désormais un flux d'événements réel (Activité) ou l'admettent explicitement (RTK/Bench, aucune route serveur). (2) Mémoire (`spaces/memoire.js`) appelait `setViews` une seule fois, hors de `draw()` : cliquer « Couches » changeait le contenu mais laissait « Store » visuellement actif — `setViews` est désormais rappelé à chaque rendu, comme `executer.js`/`concevoir.js`. (3) `#project-chip` n'avait aucun gestionnaire de clic ; il ouvre désormais la palette sur une section « Projets » mise en tête, et la palette (⌘K) est restructurée en sections titrées (Projets, Espaces, Commandes, Tâches, Workflows, Fichiers) au lieu d'une liste plate de 30+ entrées — le chip gagne aussi curseur/survol/chevron. (4) Le bouton « Suggérer » (`spaces/source-editor.js`) n'était expliqué qu'au survol (`title`) ; il porte désormais une aide permanente, et l'état « modèle configuré mais indisponible » (Ollama injoignable, modèle absent…) — qui faisait disparaître bouton ET statut en entier, sans le moindre indice — affiche maintenant la vraie raison rendue par le serveur. (5) Les états vides de Concevoir et Exécuter (pas de blueprint / pas de Mission Ledger) faisaient disparaître les boutons de vue par early-return ; ils restent désormais visibles (désactivés) et l'état vide cite la commande CLI exacte qui produit la première donnée. Nouveau test générique (`tests/e2e/test_workspace_controls.py`) : clique chaque bouton de chaque `view-seg`/`zoom-seg`, deux fois, dans deux ordres différents, et vérifie `aria-pressed` ET le style calculé — rouge avant le correctif sur Observer/Mémoire, vert après ; couvre aussi le chip projet, l'aide de Suggérer et les états vides.
- feat(upgrade-review): revue de mise à jour — un mission pack pour décider ce qu'un `grimoire upgrade-flow run` a laissé en attente (issues #490/#506/#510, #520). Nouvelle commande `grimoire upgrade-flow review [--json]` (lecture seule) : rend l'écart outil/kit (`tool_version`, issue #515 — refuse de tourner s'il est en retard), le dernier run `project-upgrade` (`grimoire.tools.flow_runs.list_flow_runs`) et les propositions en attente (`grimoire.proposals.list_proposals`, lecture pure disque), sans jamais rien appliquer. Nouveau skill `upgrade-review` (archétype `meta`, attaché au concierge) et prompt mission pack `grimoire-upgrade-review` (projeté par `host sync` : commande Claude Code, prompt Copilot) qui portent le même protocole — une ligne par proposition, questions posées par lot, décision appliquée uniquement via `grimoire proposals accept|reject` et `grimoire flow resume --result` (jamais une écriture directe). Doc : `docs/upgrade.md#revoir-les-décisions`.
- feat(cockpit): bouton « Revoir dans l'IDE » dans Piloter (issue #520, lot 2). À côté de « Mettre à jour », présent dans la page seulement s'il y a une proposition en attente ou un checkpoint destructif en attente (`GET /api/workspace/proposals`/`flows/runs`, déjà chargés par la fiche — aucun nouvel appel réseau). Au clic : copie dans le presse-papiers (repli affiché si indisponible) un texte prérempli `/grimoire-upgrade-review` + l'id du dernier run `project-upgrade` + les slugs des propositions en attente, et montre `grimoire upgrade-flow review` à lancer dans le projet — aucun mécanisme d'ouverture d'IDE n'existe dans ce dépôt (aucun schéma `vscode://`, aucun champ de config d'éditeur), donc aucun lien de ce type n'est inventé. `.pl-preview` renommé `.pl-review-preview` pour ce nouveau bloc — le sélecteur `.pl-preview` des tests e2e existants doit continuer à ne trouver que celui du bouton « Mettre à jour ».

## [3.51.1] - 2026-09-15

- fix(cockpit): trois restes de la validation finale de la boucle de mise à jour (issues #506/#510, PR #511/#513). (1) `POST /api/projects/update` rendait `state: null` sur tout run qui n'était pas le refus d'`apply` — y compris un run complet 9/9 arrêté, non décidé, au checkpoint `destructive`. Nouvelle fonction `_derive_state()` (`tools/project_update.py`), dérivée des mêmes champs structurés que `nodes`/`stoppedAt` (`done`, `stopped_at`, `ok`, `dry_run`), jamais du texte libre : `preview-only`, `upgraded-checkpoint-pending`, `upgraded-but-failed` (relayé tel quel), `failed`, `completed`. (2) Le badge Kit « mis à niveau, checkpoint destructif en attente » (Piloter) était un état purement client, posé une fois après le clic « Confirmer » — perdu à la première navigation Flotte → Projet. `grimoire.tools.flow_runs.list_flow_runs()` pose désormais `status`/`currentNode` (depuis le kernel réel, `FlowEngine.status()`) sur chaque run déjà retenu ; la fiche Piloter dérive le badge, à chaque rendu, du dernier run `project-upgrade` du projet dont le nœud courant est `destructive` en `checkpointed`, avec son `run_id`. `api.flowRuns()` accepte désormais un `project` explicite, nécessaire à la fiche d'un projet consulté depuis la Flotte. (3) Le Dock de l'espace Observer citait `grimoire upgrade-flow status`, une commande qui n'existe pas (`grimoire upgrade-flow --help` n'a pas de sous-commande `status`) — remplacée par la vraie commande, `grimoire flow status <run_id>`, avec l'id du run le plus récent.
- perf(cockpit): la Flotte de Piloter ne rebalaye plus tout le registre à chaque changement de projet (issue #510 point 5). Cas réel : chaque changement de projet dans le cockpit lançait `GET /api/health` + `GET /api/memory/status` pour CHAQUE projet du registre (34 requêtes pour 17 projets, 20 pour 10) — le fan-out vivait dans `loadFleet()` (`web/workspace/spaces/piloter.js`), seul point d'entrée à balayer tout le registre ; sélectionner un projet ne l'a jamais touché, une seule lecture ciblée (`loadSheet`) suffisait déjà. `loadFleet()` gagne un cache mémoire par slug (60 s) : un retour sur la vue Flotte dans la fenêtre sert les données déjà lues plutôt que de tout relire. « Rafraîchir la flotte » (nouveau bouton, `renderFleet`) et une mise à jour de projet confirmée (`invalidateFleetCache`, dans `renderSheet`) restent les deux seules façons de le contourner — la seconde pour que la ligne du projet qu'on vient de mettre à niveau ne reste jamais périmée jusqu'à 60 s.
- feat(doctor,cockpit): écart entre l'outil CLI de l'utilisateur et le kit du projet, désormais nommé plutôt que tu (issue #510 point 4). Cas réel : un homelab piloté par un `grimoire` pipx en 3.50.1 alors que le cockpit (un process séparé, 3.50.2) avait mis le projet à niveau — ni `doctor` ni la fiche projet du cockpit ne le disaient, le badge Kit ne comparait la version alignée du projet qu'au serveur cockpit. Nouveau contrôle `doctor` `tool_version` (`grimoire.cli.app`) : WARN — jamais FAIL — quand l'outil qui exécute la commande est plus vieux que le kit aligné du projet (`grimoire.tools.project_health.tool_version_gap`), nommant `pipx upgrade grimoire-kit` / `pip install -U grimoire-kit`. `project_health.kit_alignment()` gagne `projectTool` : la version que CE projet déclare comme la sienne (`.venv/bin/grimoire` à la racine, ou `tool:` dans `project-context.yaml`), distincte du process qui répond — jamais devinée via `$PATH`. La fiche projet du cockpit (Piloter, Inspecteur « Kit ») affiche cette troisième version quand elle diverge des deux autres, ou « inconnu, vérifiez `grimoire --version` dans le projet » quand rien n'est déclaré.

## [3.51.0] - 2026-09-15

- fix(upgrade-flow): projections hôte fantômes retirées par `orphans`, overrides pleins « fresh » proposés à la conversion (issue #510, points 2 et 6 — troisième rejeu réel, neuf projets). (1) `host sync` n'écrit que ce que son plan actuel résout, et `find_orphans()` partait de `layout.installed_agents` : une projection hôte managée (`.claude/agents/x.md`, `.github/agents/x.agent.md`) dont la source avait disparu de toutes les tiers (kit/overrides/custom, manifeste) n'était donc jamais vue par personne, alors que `grimoire doctor` la signalait déjà (`agents_referenced`) — c'est exactement la forme trouvée sur la Forge. Nouvelle fonction `_ghost_managed_projections()` (`tools/project_upgrade.py`) : toute projection managée dont le nom n'a aucun installé correspondant devient un orphelin comme un autre, archivée sous `_archive/.../orphans/` — jamais un fichier hôte sans le marqueur `grimoire:managed`, jamais une suppression. Choisi dans le nœud `orphans` plutôt que `host sync` : le mécanisme d'archivage y vit déjà, et ce nœud tourne avant `apply`. (2) `propose_override_migrations()` sautait tout override en statut de dérive `"fresh"` (le kit n'a pas bougé depuis la prise), y compris un override **plein** dont le corps est resté une copie exacte du kit — la forme la plus simple à convertir, puisque « fresh » signifie justement que rien n'a changé côté kit. Un homelab réel avait sept overrides pleins de cette forme, chacun ne portant qu'un `context:`, sans qu'aucun ne reçoive de proposition. Chaque override plein reçoit désormais un essai de conversion (`convert_override --dry-run`) quel que soit son statut de dérive ; seul un override déjà partiel (`extends: kit`) est sauté, sans condition — c'est la forme déjà convertie.
- fix(upgrade-flow): ligne de base de `preview` étendue à tous les contrôles `grimoire doctor`, rapport explicite quand `apply` refuse après `up` (issue #510, points 1 et 3 — troisième rejeu réel, neuf projets). (1) La ligne de base que `preview` enregistre (`doctor-baseline.json`) ne couvrait que `paths_resolve` (#502) : sur la Forge, `apply` a refusé sur un FAIL `agents_referenced` préexistant (une projection hôte managée sans source dans aucune tier), sans proposition. Elle couvre désormais tout contrôle doctor — une signature `"<contrôle> : <détail>"` par check (`other_failures`, à côté de `dead_references`, déjà par ligne). Un contrôle déjà en échec au moment de `preview` ne fait plus refuser `apply` : il devient une proposition `repair` nommant le contrôle (`propose_doctor_repairs`, `category: "doctor-preexisting"`) ; seule une régression apparue depuis `preview` échoue encore. (2) Quand `apply` refuse malgré tout, `grimoire upgrade-flow run --json` rendait `done: []`, `stoppedAt: null`, sans proposition ni piste — alors que `up` avait déjà tourné. Il rend désormais `done` (les nœuds réellement exécutés), `stopped_at: "apply"`, `state: "upgraded-but-failed"`, `failing_checks` (le ou les contrôles en cause) et `backup_path` ; un `_grimoire-output/upgrade/<date>/report.md` est écrit avant le refus, commençant par « Mis à niveau, flow en échec sur `<contrôle>`. ». `grimoire.tools.project_update.update_project()` (atelier/cockpit) porte les mêmes `state`/`backupPath` et relit ce rapport. `ApplyResult` gagne `failing_checks` ; nouvelles fonctions `propose_doctor_repairs`/`write_apply_failure_report` (`tools/project_upgrade.py`).
- fix(tests): le registre cockpit réel de la machine (`~/.grimoire/cockpit/registry.json`) contenait sept entrées mortes `x`/`x-2`…`x-7` pointant vers des répertoires `tempfile.mkdtemp()` disparus (`/tmp/tmpXXXXXXXX/...`), en plus de l'entrée `probe` déjà trouvée (#492). Reproduction exhaustive (suite complète `tests/unit`, `tests/test_*.py`, `tests/integration` rejouée sous un `$HOME` simulé instrumenté pour refuser toute écriture hors de l'isolation) : aucune fuite dans les suites pytest telles qu'elles s'exécutent aujourd'hui — l'isolation `HOME`/`GRIMOIRE_COCKPIT_HOME` de `tests/conftest.py` (#153) tient. La forme des chemins retrouvés (`tempfile.mkdtemp()` direct, pas le `tmp_path` imbriqué de pytest) désigne un `grimoire init` manuel sur un dossier jetable, jamais nettoyé — un smoke test ou une session d'agent, sans `--no-cockpit`. Durcissement à la source plutôt qu'un correctif de test isolé : `_maybe_register_cockpit` (`cli/cmd_init.py`, chemin partagé par `init`/`up`) refuse désormais silencieusement (trace `logging.DEBUG`) d'enrôler un projet dont le chemin résolu vit à un ou deux niveaux sous la racine temporaire du système (`tools/project_registry.py::is_scratch_path`, nouveau) — assez large pour couvrir `/tmp/tmpXXXXXXXX/<nom>` et `/tmp/<nom>`, assez étroit pour ne jamais toucher le `tmp_path` isolé de pytest (toujours ≥3 niveaux sous cette racine). Les actions explicites (`grimoire cockpit add`/`create`, `grimoire serve --project-root`) restent inchangées. Le garde de session existant (`tests/conftest.py::pytest_sessionfinish`, #339) couvre désormais aussi l'état cockpit (`cockpit.json`), pas seulement le registre.
- feat(cockpit): décider une proposition (Accepter/Refuser, espace Piloter) fonctionne désormais pour n'importe quel projet du registre depuis le cockpit, pas seulement le projet de lancement direct (issue #506) — première moitié de « fermer la boucle de mise à jour » après le premier test réel du flow `upgrade-flow`. `POST /api/workspace/proposals/<slug>/accept|reject` ouvrait la même garde `_HOME_SLUG` que le reste des écritures de la vue de travail, alors que `POST /api/projects/update` (le bouton « Mettre à jour » juste au-dessus, dans le même écran) écrit déjà dans n'importe quel projet du registre — décider une proposition (jamais la créer automatiquement) est le geste que le cockpit existe pour rendre possible sur toute la flotte. Nouvelle fonction `workspace_routes.is_proposal_decision()` nomme la dérogation ; le reste des écritures (fichier, tâche, override) reste strictement réservé au projet de lancement. Distingue aussi côté UI le type `repair` (issue #502) : une substitution évidente s'accepte comme les autres propositions, une réparation sans substitution évidente n'offre plus que « Refuser » et « Ouvrir le fichier » (lien vers l'espace Source à la ligne citée, avec saut de curseur). Corrige au passage un bug trouvé pendant le test réel : `withProject()` (web/workspace/api.js) ajoutait `project` à la query string même quand l'appelant en avait déjà posé un explicite, produisant un paramètre dupliqué (`?project=terraform&project=grimoire-forge`) qui pouvait faire lire la garde d'écriture sur le mauvais projet.

- feat(cockpit): montrer ce que le flow de mise à jour a fait — deuxième moitié de « fermer la boucle de mise à jour » (issue #506). `/api/projects/update` rend désormais un statut par nœud (`nodes` — `fait`/`proposition`/`sauté`/`erreur`/`checkpoint en attente`, `project_update.py::_node_statuses`) et le chemin de la sauvegarde (`backupPath`, jusqu'ici jeté après avoir servi de `detail` au contrat de flow — `cmd_upgrade_flow.py`, `_fail_run` porte aussi `done`/`failed_node` sur un refus, perdus jusqu'ici par le `_fail` générique). Piloter affiche ce déroulé nœud par nœud, un état « en cours » pendant l'appel (jusqu'à une minute), et rend `preview.md` en Markdown structuré (nouveau module partagé `web/workspace/markdown.js`, extrait de l'éditeur Source) plutôt qu'en texte brut tronqué à 2000 caractères sans indicateur ; le badge Kit passe à « mis à niveau, checkpoint destructif en attente » après un run confirmé, et nomme les fichiers en retard. Corrige un bug trouvé en testant : `options.refresh()`, appelé juste après avoir montré le résultat de la confirmation, redessinait toute la fiche et l'effaçait aussitôt (« l'Inspecteur revient silencieusement à l'état initial ») — retiré du chemin de confirmation, remplacé par un rafraîchissement ciblé des propositions. Corrige aussi « Ouvrir ce projet » (rechargeait toute la page et retombait sur la vue Flotte au lieu d'ouvrir l'écran Projet demandé) en navigation interne (`ctx.goto`), et ajoute `GET /api/workspace/flows/runs` (nouveau module `grimoire.tools.flow_runs`) pour qu'Observer cesse de dire « TraceLedger vide » quand un run de flow existe bel et bien, juste ailleurs que dans le TraceLedger.

## [3.50.2] - 2026-09-14

- fix(upgrade-flow): second rejeu réel du flow `upgrade-flow` sur dix projets (issue #502) — le nœud `orphans` archivait encore des agents vivants, 1/3/13 par projet, quand ils vivaient dans une tier possédée par le projet (`_grimoire/_config/custom/agents/`, legacy) sans être déclarés sous `agents.custom_agents` : `project_upgrade.find_orphans()` ne posait pas la question dans le bon ordre, un agent absent du roster frais ET non déclaré ET hors overrides passait pour orphelin même hors tier kit. Un candidat orphelin doit désormais vivre dans la tier kit elle-même (`_grimoire/kit/agents/`) — la seule que ce nœud archive — en plus des critères existants (roster frais, déclaration). `grimoire doctor` gagne un nouveau check `agents_referenced` (`core/integrity.py`) : un agent que `agent-manifest.csv` ou une projection hôte gérée (`.claude/agents/`, `.github/agents/`, …) nomme mais dont le fichier source a disparu est désormais un FAIL nommé — un projet réel était passé à 25/25 malgré 13 agents manifestés disparus. La génération du manifeste (`core/scaffold.py`) exclut au passage le gabarit vierge `custom-agent.md` (`name: "{{agent_tag}}"` non rendu) qu'elle listait par erreur comme un agent installé.
- fix(upgrade-flow): suite du second rejeu réel (issue #502) — deux défauts supplémentaires. (1) `probe_hook` (`project_upgrade.py`) jugeait le hook SessionStart en erreur dès que la sous-chaîne « erreur »/« error » apparaissait n'importe où dans le JSON rendu ; un projet dont le rappel de tâche mémoire mentionnait une erreur passée (cas réel : TTS-Voice) faisait échouer `apply` sur un hook parfaitement sain. La détection ne porte plus que sur des signaux structurés — clé `error` de premier niveau, ou le marqueur exact `[Grimoire] hook <id> en erreur` (`hosts/decisions/__init__.py::_failed_decision`) en tête d'un des blocs de texte réellement rendus (`additionalContext`, `systemMessage`, `reason`) — jamais une recherche libre dans le texte. (2) `apply` refusait sur des références `_grimoire/...` périmées déjà présentes AVANT la mise à niveau (`_memory/decisions-log.md`, `.github/copilot-instructions.md`, `.claude/skills/*/SKILL.md`), laissant trois projets réels « mis à niveau mais flow en échec » sans proposition. Le nœud `preview` enregistre désormais une ligne de base des références mortes (`_grimoire-output/upgrade/<date>/doctor-baseline.json`) ; `apply` n'échoue plus que sur une régression (référence absente de cette ligne de base) — chaque référence déjà connue devient une proposition `repair` (`propose_repairs`/`_accept_repair`, nouveau `artifact_type`), nommant une substitution vers la tier kit quand elle est évidente (`_suggest_repair_substitution`, jamais une automatique) et une revue humaine sinon. Le rapport de fin du flow (`grimoire upgrade-flow apply`/`run`) dit désormais clairement « mis à niveau, N défaut(s) préexistant(s) en proposition » quand c'est le cas.

## [3.50.1] - 2026-09-14

- fix(upgrade-flow): premier rejeu réel du flow `upgrade-flow` (#490/#491/#492) sur un projet réel (archétype `infra-ops`+`fix-loop`, feature `vector-memory`) — trois défauts, issue #499. (1) Le nœud `orphans` archivait des agents encore vivants (`fix-loop-orchestrator`, `vectus`), cassant le projet jusqu'à réparation manuelle : `cmd_up._infer_resolved()` codait `feature_agents=()` en dur malgré son propre docstring, gelant tout agent de feature (`vectus`) sur `up` et le faisant passer pour orphelin — corrigé par `_available_features()`, qui l'infère depuis le disque comme `stack_agents` l'était déjà. `project_upgrade.find_orphans()` ignorait en plus `agents.custom_agents` (`project-context.yaml`) et la tier des overrides (un agent fully-custom sans base kit) — les deux sont désormais des motifs d'exclusion explicites, en plus du roster frais ; en cas de doute, jamais d'archivage. (2) `backup_project()` écrasait silencieusement le tarball/manifeste d'un run précédent le même jour dont le contenu avait changé depuis (dry-run puis run réel) : un digest SHA-256 du contenu réel décide désormais réutilisation (identique) vs nouveau fichier suffixé `-2`/`-3`/… (différent) — le nom canonique, une fois écrit, n'est plus jamais réécrit. (3) `grimoire upgrade-flow --help`/`run --help` décrivent désormais l'ordre des 9 nœuds, la distinction V0 (mécanique)/V1 (proposition)/V2 (checkpoint) et l'emplacement de la sauvegarde.

## [3.50.0] - 2026-09-14

- feat(cockpit): mettre à jour un projet depuis le cockpit lance le flow, pas `up` seul (#490). `POST /api/projects/update` (`src/grimoire/tools/project_update.py`, deux hôtes — `cmd_cockpit.py` et `forge_server.py`, câblage inchangé) appelle désormais `grimoire upgrade-flow run` en sous-processus : aperçu par défaut (`--dry-run`, la réponse porte le contenu exact de `preview.md`, jamais un résumé reformulé), `confirm: true` pour le flow complet sous `--executor interactive` (mécanique, arrêté au checkpoint `destructive` comme en CLI — la réponse porte alors `report.md` et les propositions encore `pending`). Projet non enregistré ou chemin hors registre : refusé (`404`), jamais un repli sur le projet servi. Le bouton « Mettre à jour — aperçu » de l'espace Piloter garde son nom ; les propositions qu'un run complet écrit apparaissent dans sa section Propositions dès le redessin qui suit la confirmation, sans mécanisme neuf. `docs/upgrade.md` gagne la section « Depuis le cockpit ».
- feat(upgrade): mettre à jour un projet est un flow, pas un `up` nu (issue #490). `registry/blueprints/project-upgrade.blueprint.json` : sauvegarde (tarball + manifeste SHA-256 de `_grimoire/_memory/`) -> aperçu (`up`/`host sync --dry-run`) -> orphelins (agents/wrappers `grimoire:managed` que le kit installé ne livre plus, archivés sous `_archive/<date>-pre-<version>/orphans/`, jamais supprimés — avant `apply`, sinon la garde de distinction de `up`/`host sync` refuse) -> application (`up`, acceptance : doctor vert + hook SessionStart sans erreur) -> deux nœuds de jugement V1 qui ne font jamais qu'écrire une proposition via le mécanisme `proposals` existant (nouveaux `artifact_type` `override-migration` et `memory-link`, jamais d'écriture directe) : overrides en dérive (convertir en partiel si le corps est identique au kit, sinon revue humaine avec diff) et fiches mémoire non raccordées à un `context:` -> besoins/hôtes non déclarés (proposition `needs-hosts`) -> vérification (rapport final, manifeste recomparé) -> checkpoint forcé (`kind: "checkpoint"`) pour tout retrait au-delà d'un archivage. Nouvelle commande `grimoire upgrade-flow` (nom distinct de `grimoire upgrade`, déjà pris par la migration de structure v2→v3) : `run [--dry-run] [--executor interactive|dispatch]`, plus les utilitaires `backup`/`preview`/`apply`/`orphans`/`propose`/`verify`/`check`/`probe-hook` que les acceptances du blueprint invoquent elles-mêmes. `grimoire.cli.cmd_up.fresh_kit_agent_roster` (nouveau) diffuse le plan du scaffolder sans rien écrire, pour que l'orphelinage se détecte sans dépendre d'un `up` déjà passé. `grimoire.proposals` gagne `create_manual_proposal` (propositions hors déclencheur de non-choix) et le traitement `accept_proposal` des trois nouveaux `artifact_type` (`override-migration`, `memory-link`, `needs-hosts`) : un `memory-link` accepté sur un agent kit sans override lui en crée un **partiel** (`extends: kit`, `kit_source_hash`, issue #427) ne portant que `context:` — jamais un refus pour absence d'override, seul un porteur non trouvé refuse encore ; un agent déjà overridé voit sa fiche ajoutée au `context:` existant. `needs-hosts` écrit désormais `hosts.enabled` dans `project-context.yaml` par round-trip (`grimoire.tools._common.load_yaml_roundtrip`/`save_yaml`, #430 : commentaires intacts) quand la proposition porte une valeur mécanique réelle (détection sur disque) ; `needs.commands` reste, sciemment, un refus nommé — un besoin non résolu n'a par définition aucune commande à écrire, en inventer une contredirait `grimoire.core.execution_needs`. Corrige au passage `grimoire blueprint validate` : sa liste de `kind` connus n'avait pas suivi les sept genres de node (#207/#488), refusant tout blueprint qui en déclare un comme « unknown kind » malgré un schéma qui l'acceptait déjà.
- fix(tests): `real_project` (fixture) était sondable par un vrai backend mémoire réseau sur l'hôte qui lance la suite — `_init_real_project` (`tests/conftest.py`) appelait `grimoire init` sans `--backend`, donc `auto` : `detect_memory_backend()` sonde de vrais ports localhost (Weaviate `:8080`, Qdrant `:6333`, Ollama `:11434`), hors du périmètre que `_isolate_user_state` protège. Sur un poste où l'un de ces backends répond réellement, `TaskService.recall()` remontait des `decisions`/`failures` du poste sans rapport avec le projet jetable du test : `test_le_rappel_d_une_tache_neuve_est_honnetement_vide` et son jumeau e2e échouaient en local, jamais en CI (ports injoignables). Épingle `--backend local` (mémoire fichier) dans `_init_real_project` ; garde de régression `test_le_projet_reel_n_a_pas_de_backend_memoire_sondable_sur_l_hote`. Aucun changement côté `src/` (#494, closes #493).

## [3.49.0] - 2026-09-14

- feat(flows): les sept genres de node que le statique ne sait pas exprimer — lot 4 de l'épic moteur de flows (#207). `kind` gagne `fanout` (un node produit N éléments via `fanout_items`, N sous-flows instanciés, résultats recollés, N borné par `pilot.max_fanout_n` — absent, refus), `verify-panel` (k vérificateurs indépendants sous angles imposés, `config.verifyPanel.k`/`.angles`, majorité requise, coût = k tentatives toujours, jamais un vert sur 1/k), `loop-until-dry` (relance jusqu'à `config.loopUntilDry.maxRounds` tours sans `novelty_key` neuve, dédupliquée contre tout le vu), `judge` (N tentatives sous angles imposés, `config.judge.n`/`.angles`, le node lui-même redispatché comme juge V1 qui désigne `judge_winner`, la trace des N survit dans leurs propres runs), `checkpoint` (`approve`/`reject`/`amend`, toujours une décision d'hôte forcée — jamais auto-décidée par la cascade, `reject` bloque en nommant le motif dans l'événement de refus du kernel), `budget` (`config.budget.maxCostUsd`/`.passes` — première passe obligatoire, les suivantes abandonnées avant dépassement, jamais tentées au-delà) et `replay-diff` (même flow rejoué deux fois sur la même entrée, `replay_diverged` expose une divergence sans faire échouer le node). Six des sept genres généralisent le lancement de sous-flow déjà posé par `composite` (#206, `_launch_child`, factorisé) ; `checkpoint` seul n'a pas de `ref`. Paramétrage refusé nommément au chargement (`blueprint_loader._validate_genre_config`) ; `flow extract` ne les aplatit jamais. Nouveau module `flows/genres.py`, compagnon de `flows/dispatch_executor.py` : le cliquet de code (`scripts/check-code-ratchet.py`, R2, seuil 1500 lignes) a refusé de laisser `dispatch_executor.py` grossir au-delà (1695 lignes) — même geste que la scission historique de `forge_server.py`. Aucun changement Rust : orchestration Python (E/S, runs enfants), hors périmètre des fonctions pures déjà portées par `grimoire-flows-core`.
- feat(flows): un flow est un node — un node `kind: "composite"` (déjà réservé par le schéma pour un sous-flow, jusqu'ici jamais exécuté par le moteur) lance son `ref` (`.blueprint.json`, id de registre local, ou refus nommé pour `use-case:` — réservé au Studio) comme un run enfant à part entière, lié au parent (`FlowRunMeta.parent_run_id`/`parent_node_id`), dont le coût est remonté et plafonné par le pilote sur le **total** du sous-flow ; profondeur de composition bornée à 3, cycle ou référence introuvable refusés au chargement, jamais au node. `flow extract` ne l'aplatit jamais (`kind`/`ref` préservés, `extraction.child_run_id` tracé). `grimoire.flows.registry.describe_flow` + `flow list` affichent, par flow, sa version, sa plage de compatibilité kit (`kitMin`/`kitMax`), l'empreinte SHA-256 du fichier et l'union des besoins d'exécution (`run_need`) du flow et de ses sous-flows ; `flow list --require-measure` couvre un sous-flow sans code neuf (même clé `blueprint_id` que #473). Aucun changement Rust (#206).
- fix(policies): rechute du 2026-09-12 (#463), constatée le 2026-09-14 sur Grimoire-Forge : une session dont `per_session.max_duration_min` (`block`, sans `tool_pattern`) avait dépassé sa fenêtre refusait `git status` (lecture) et — surtout — `grimoire policies reset-session`, la commande que son propre message de refus recommandait. L'exemption de réparation (`_is_repair_exempt`, miroir Rust `is_repair_exempt`) couvrait déjà tout appel non mutant et l'édition de `_grimoire/standard/policies.yaml`/`_grimoire-output/.runs/session-*.json`, mais pas l'invocation de `grimoire policies` elle-même : son premier mot (`grimoire`) n'est reconnu ni lecture seule (`is_read_only_command`) ni comme ciblant l'un des deux fichiers exemptés. Reconnaît désormais l'invocation de `grimoire policies` (`status`, `reset-session`) dans ses trois formes documentées (`grimoire policies …`, `<venv>/bin/grimoire policies …`, `python -m grimoire policies …`), en n'inspectant que le premier mot de chaque segment shell — jamais une commande qui se contente de la *nommer* (`echo grimoire policies …` reste refusé). `grimoire doctor` signale désormais (`WARN`, `policy_budget_duration_guard`) toute règle `per_session` sans `tool_pattern` en `block` dont `max_duration_min` est inférieur à 2880 min (48h), une session Claude Code pouvant rester ouverte plusieurs jours. (#481)
- feat(cockpit): agrégation mémoire multi-projets — dernier volet de l'issue #172 (« Cockpit — du générateur statique au portefeuille actif », Refs #468). `GET /api/workspace/memory/overview?projects=all|<slugs>` (`src/grimoire/tools/workspace_memory.py`) rend, par projet du registre de la machine, backend configuré/résolu, nombre d'entrées, dernière écriture et état de l'index lexical (primaire/compagnon/absent) — réutilise `memory_link_status()` et `MemoryManager`, aucun nouveau lecteur ; un projet dont la mémoire est absente ou illisible est listé avec sa raison, jamais compté comme un store vide en silence (#264). `GET /api/workspace/memory/search?q=...&projects=...` interroge la même chaîne que `grimoire memory search` (`run_memory_search`, hybride si le projet a un compagnon lexical) projet par projet et fusionne seulement la LISTE de résultats, étiquetés par `projectSlug`, ordre stable (score puis slug) — jamais de fusion des stores. L'espace Mémoire (`web/workspace/spaces/memoire.js`) gagne un onglet Flotte : zoom docbar « Ce projet / Tous les projets », tableau agrégé, recherche croisée dont les résultats s'ouvrent dans le panneau d'inspection avec leur projet d'origine. Lecture seule ; `cmd_cockpit.py` non modifié (les deux routes passent par la table `workspace_routes.GET_ROUTES` déjà partagée par l'atelier et le cockpit).

## [3.48.0] - 2026-09-13

- feat(flows): un node de flow déclare un besoin d'exécution (`{"run_need": "test-runner"}`) plutôt qu'une commande en dur — lot 2 de l'épic moteur de flows (#205). Catalogue fixe (`test-runner`, `lint`, `typecheck`, `build`, `migration-tool`, `format`, `grimoire.core.execution_needs`), résolu en deux temps au chargement du blueprint : `needs.commands` déclaré dans `project-context.yaml` (schéma/validateur/Rust en parité, `rust/grimoire-schema-core/`) l'emporte toujours sur la détection par marqueur de projet (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`). Un besoin non résolvable **refuse le chargement du blueprint entier** en le nommant, avant que le premier node soit présenté à l'hôte — jamais une installation qui échoue au troisième node. `grimoire needs resolve` affiche le verdict par besoin et sa source. Rétrocompatible : les blueprints à commandes en dur restent valides ; `grimoire flow run` avertit (jamais un refus) quand une commande en dur correspond à un besoin résolu pour ce projet. Aucun changement côté `rust/grimoire-flows-core/` : la résolution d'un besoin est une lecture disque (config + marqueurs), donc hors périmètre des fonctions pures déjà portées (`check_output_against_contract` ne voit que les pins, jamais l'acceptance).
- feat(flows): `grimoire flow extract <run-id> [--out <fichier>]` — un flow s'extrait d'un run, il ne se dessine pas (transversale T2, issue #210). Joint les métadonnées du run (`FlowRunMeta`) au Mission Ledger (`node_dispatch_history`) pour produire un blueprint brouillon dans le format `.blueprint.json` existant : l'acceptance d'un node **complété** par la cascade est remplacée par la commande réellement exécutée, réinférée en `{"run_need": ...}` sur correspondance caractère pour caractère avec un besoin résolu (#205) — jamais sur une correspondance partielle (une commande observée avec des arguments en plus reste `{"run": ...}` verbatim, deviner la coupure serait une invention). Un node **jamais dispatché** (classe V2, ou exécuté par un hôte interactif) garde son acceptance d'origine verbatim ; un node **jamais atteint** (run incomplet ou abandonné) est marqué `not_reached`. Chaque node du brouillon porte un objet `"extraction"` (`status`, `note`) traçant lequel de ces trois cas s'est produit — jamais une inférence depuis la prose. Le brouillon extrait est rejouable : `flow run`/`resume` le chargent comme n'importe quel blueprint et reproduisent la même séquence de nodes que le run source. Aucun changement côté `rust/grimoire-flows-core/` : l'extraction ne fait que lire `FlowRunMeta`/le Mission Ledger via l'API publique déjà existante de `FlowEngine` (`run_meta`, `status`), aucune nouvelle fonction pure candidate au portage.
- feat(flows): le flow porte sa preuve, le registre local l'affiche — lot 5 réduit de l'épic moteur de flows (#208). `TraceLedger.dispatch_outcome_stats()` gagne une troisième ventilation `by_flow` (en plus de `by_class`/`by_provider`, #442) : le préfixe `blueprint_id` du tag `replay:<blueprint_id>:<node_id>` qu'un node de flow porte déjà (#205/#210), jamais un dispatch hors flow (qui retombe sur l'id de tâche, sans `:`). `grimoire dispatch stats` affiche cette ventilation ; nouvelle commande `grimoire flow list [--require-measure <blueprint-id>]` — le registre local des flows (runs connus groupés par blueprint, dernier run, nœuds résolus/coût/escalade quand une mesure de dispatch existe) ; `--require-measure` refuse (sortie 1) si le blueprint désigné n'a aucune mesure. Calculé en Python dans les deux backends (jamais un sixième port Rust : une dimension de plus sur les mêmes enregistrements que `by_class`/`by_provider`, pas une fonction assez chaude pour le justifier) — vérifié : `to_dict()` reste identique entre `GRIMOIRE_TRACES_BACKEND=python` et `=rust` sur le corpus de parité. Pas de nouveau registre publié (#206 reste verrouillé) : une lecture locale, jamais un second calcul.
- feat(flows): le pilote — la fonction qui décide combien dépenser par node, pas une couche (transversale T1, issue #209). Nouveau module `flows/pilot.py` : `decide()` nomme et rassemble la décision déjà éparpillée entre `missions.dispatch.start_tier_for`/`_tier_chain` (inchangées, toujours pures, toujours portées Rust — `grimoire-dispatch-core`) et le refus V2 de `flows.dispatch_executor` ; une politique de projet optionnelle `_grimoire/standard/pilot.yaml` (`start_tier` par classe, `max_escalations`, `max_cost_usd_per_node`) la paramètre, absente elle ne change rien. `missions.dispatch.run_dispatch` gagne le mécanisme réellement neuf : un paramètre `max_cost_usd` qui arrête l'escalade entre deux paliers (jamais au milieu d'une tentative, jamais après un vert déjà acquis) avec un refus nommé (`DispatchReport.cost_capped`, même mécanique que `unrunnable` — aucun changement Rust requis). `DispatchExecutor` charge la politique une fois par run ; `--max-tier` explicite de la CLI reste prioritaire sur le plafond d'escalade de la politique. Aucune CLI nouvelle. Un `pilot.yaml` malformé refuse nommément le chargement plutôt que de retomber en silence (contrairement à `orchestration-policy.yaml` : un plafond de coût est un mécanisme de sécurité).
- fix(framework): cinq parseurs mémoire de `framework/tools/` avalaient `OSError`/`UnicodeDecodeError` et rendaient `[]`/`None`/`continue` — un fichier illisible (encodage étranger, verrouillé, tronqué) disparaissait du résumé, de la synchro Qdrant et de l'index RAG sans une ligne, `"errors": []` en tête. `SectionParser.parse_file` (`context-summarizer.py`), `MemoryParser.parse_file` (`memory-sync.py`) et `ChunkingStrategy.chunk_file` (`rag-indexer.py`) prennent désormais un `errors: list[str] | None` optionnel, nommant le fichier et la cause dans `report.errors` côté `summarize()`/`push()`/`index_collection()` (chemins déjà outillés d'un rapport). `ToolDiscoverer._inspect_python_tool`/`_inspect_shell_tool`/`_inspect_markdown_tool` (`tool-registry.py`) et le fallback fichier de `rag-retriever.py` (aucun rapport structuré sur ces chemins) écrivent la même information sur stderr. Diff net nul sur chacun des cinq fichiers (`framework/` gelé, shrink-only) : le garde anti-boucle est fusionné avec le `return`/`continue` d'origine sur la même ligne. (#264)
- fix(framework): un pheromone board corrompu de `framework/tools/` (`stigmergy.py` et `stigmergy_hooks/scripts/stigmergy_hook.py`) était remplacé par un board vide au dépôt suivant — `load_board` rendait un board vide sur `JSONDecodeError`/`OSError` sans le signaler, et `deposit_pheromone`/`save_board` écrasait ensuite le fichier corrompu avec un board ne contenant que le nouveau signal : une écriture tronquée (disque plein, deux hooks concurrents) détruisait tout l'historique stigmergique, `deposit_pheromone` rendant une `Pheromone` comme si de rien n'était. `load_board` met désormais le fichier corrompu de côté (`<nom>.corrupt-<horodatage>`) avant de rendre un board vide, et l'annonce sur stderr. La copie `src/grimoire/tools/stigmergy.py` a le même correctif depuis #270 ; celle-ci porte le correctif dans `framework/` (gelé, shrink-only) à diff net négatif sur les deux fichiers (garde compacté sur la même ligne que le `return`, plus deux signatures et un appel resserrés sur une ligne). (#265)
- fix(cli): `grimoire.sh help` bloquait indéfiniment (timeout 30s) sous Git Bash Windows — `find_project_root()` remontait l'arborescence via `dirname` jusqu'à `/`, mais sous Git Bash Windows `dirname` peut cesser de progresser (ex. se stabiliser sur `.`) avant d'atteindre `/`, transformant la boucle en boucle infinie. La boucle s'arrête désormais dès que `dirname` ne progresse plus, en plus de la condition d'arrêt à `/`. `TestGrimoireShRouting` repasse de `@requires_bash_posix_only` à `@requires_bash` (couverte sous Windows) (#461).
- fix(tests): `TestInstallSh::test_install_sh_has_shebang` levait un `UnicodeDecodeError` cp1252 sous Windows — `INSTALL_SH.read_text()` sans `encoding=` explicite décodait le fichier (légitimement UTF-8, avec des caractères accentués) selon la locale par défaut de Windows plutôt qu'en UTF-8 (même famille que #192). `read_text(encoding="utf-8")` corrige le test ; `install.sh` lui-même n'a pas changé. `TestInstallSh` repasse de `@requires_bash_posix_only` à `@requires_bash` ; `requires_bash_posix_only` n'ayant plus d'utilisateur (dernier hors-scope #231 traité), il est retiré (#462).

## [3.47.0] - 2026-09-13

- feat(cockpit): le wizard de setup (espace Piloter) exécute réellement le projet au lieu d'écrire une commande à copier-coller — `POST /api/setup` appelle `grimoire up` en direct (`cmd_up.run_up_pipeline`, jamais un sous-processus), needs transmis (B3 rebranché sur B2, `GET /api/needs`), refus fail-closed avant toute écriture (archétype/backend/need inconnu, chemin non inscriptible), rapport d'exécution persistant (`_grimoire/setup-run.json`, doctor compris) affiché dans la fiche projet. `_select_cwd_project` honore désormais un `--project-root` explicite même sur un dossier vierge (sans quoi aucune écriture n'était jamais possible sur le cas que l'issue décrit) ; le mode « copier-coller la commande » reste un repli explicite (#171).
- feat(standard): le need `project-discovery` scaffolde réellement `_grimoire/cadrage/` par appel de code direct (`cmd_up.py::_step_cadrage`), plus par une simple suggestion textuelle de `needs_suggest.py`. Sa complétude devient un contrôle du standard agentique (`_verify_cadrage`, dimension `artifacts`) visible dans `standard verify`/`gate`/`score`/`audit` : `info`/`warning` par défaut, `error` sur les phases gate (exigences, cahier des charges) uniquement quand `project-discovery` a été explicitement choisi. Documentation dédiée `docs/cadrage.md`, référencée depuis le README et `docs/cli-reference.md` (#173).
- feat(cockpit): « nouveau projet » depuis le portefeuille (espace Piloter, niveau Flotte) — un bouton ouvre le même formulaire que le wizard de setup (#171) sur un chemin choisi ; `POST /api/projects/create` (cmd_cockpit.py) exécute le plan sur ce chemin (chaîne `execute_setup_plan`/`grimoire up`, comme `/api/setup`), refuse un chemin hors des racines permises ou déjà un projet Grimoire, puis enregistre le projet dans le registre — `grimoire up`/`init` l'enrôlent comme n'importe quel autre. Le sélecteur de projets (le tableau Flotte) le montre aussitôt, sans redémarrer le cockpit. Point 1 (API réelle de lecture) déjà livré par #143 ; l'agrégation mémoire multi-projets (point 3) reste hors périmètre — toujours des lectures projet par projet (#172).
- feat(hosts): déclarer les hôtes activés (`hosts.enabled` dans `project-context.yaml`, schéma/validateur/Rust en parité), détection filesystem par défaut quand la clé est absente (`claude` seul si rien n'est détecté), `grimoire init` écrit la clé avec le résultat de la détection. `grimoire host sync`/`up` n'émettent plus que les hôtes activés ; un `--host <x>` désactivé est un refus nommé sauf `--force-host` ; `grimoire host status --host all` liste les fichiers orphelins d'un hôte désactivé, `grimoire host sync --prune-disabled` les retire (opt-in). `grimoire doctor` ajoute INFO (orphelins) et WARN (hôte activé sans fichier émis). Alternative « canal plugin » Claude Code écartée par décision (#177).
- fix(policies): quatre défauts de conception de la règle de budget de session, révélés le 2026-09-12 par un incident réel sur la Forge (`per_session: {max_writes: 800, max_tool_calls: 3000}` sans `tool_pattern` a fini par refuser tous les outils de la session, y compris l'édition du fichier de règles qui la portait). (1) `max_writes` comptait tout appel `Bash` portant une commande, lectures comprises (`cat`, `grep`, `find`, `git status`...) ; `classify_tool` distingue désormais un `Bash` de lecture seule (`grimoire.hosts.decisions.tool_facts.is_read_only_command`), conservateur par construction. (2) Une règle `per_session` en `block` n'atteint plus jamais un appel non-mutant ni une écriture ciblant `_grimoire/standard/policies.yaml`/`_grimoire-output/.runs/session-*.json` — exemption de réparation codée en dur, miroir Rust compris (`rust/grimoire-policies-core`). (3) `per_session.subagents` (`shared`/`separate`) nomme le partage de budget entre une session et ses sous-agents (le payload de hook de Claude Code ne les distingue pas) ; `separate` échoue au chargement (`GR-POL-003`) plutôt que de simuler une isolation absente. (4) `grimoire policies reset-session [--session-id]` supprime l'état d'une session, `grimoire policies status` avertit sous 10 % de plafond restant, et le message de refus d'un budget nomme désormais cette commande et le fichier de règles. `grimoire doctor` signale (`WARN`) toute règle `per_session` sans `tool_pattern` en `verdict_on_match: block` (#463).
- fix(cli): sous Windows, `grimoire-init.sh reset`/`uninstall`/`quick-update` restaient non prouvés -- les seize (puis vingt-deux) tests correspondants etaient satures depuis #256 sans jamais avoir tourne verts. La vraie cause n'etait pas les commandes elles-memes (elles repondent en <1s une fois invoquees) mais le harnais de test : `subprocess.run(..., timeout=30)` tue bien le `bash` direct a l'expiration, puis redraine les tubes sans timeout -- si un petit-fils Windows (`cp.exe`, `mkdir.exe`...) tenait encore le tube stdout/stderr ouvert, ce second appel bloquait indefiniment le job jusqu'a son plafond. `_run()` tue desormais l'arbre de processus complet (`taskkill /T /F`) avant de redrainer. Les vingt-deux tests des trois commandes tournent et passent sous `windows-latest` (matrice restauree, job bloquant conserve) ; `grimoire.sh help` et `install.sh` restent hors scope avec leurs propres bugs Windows distincts (#461, #462) (#231).

## [3.46.1] - 2026-09-12

- feat(cli): `grimoire init` et `grimoire up` enrôlaient chaque projet dans le registre cockpit réel (`~/.grimoire/cockpit/registry.json`), même les jetables (`/tmp`, un scratchpad, une recette) — seule la variable d'environnement non documentée `GRIMOIRE_NO_COCKPIT` pouvait l'éviter, et rien ne la mentionnait à côté des options des deux commandes. Ajoute `--no-cockpit` à `init` et à `up` (même effet que la variable, documentée au même endroit dans `--help`) (#305).
- fix(cli): `grimoire up` annonçait `refresh: done` alors que `host sync --dry-run` trouvait encore des dizaines de fichiers à écrire pour Claude Code, Copilot, Codex, Cursor et Gemini CLI juste après — `refresh` ne régénère que le tier kit (`_grimoire/kit/`), pas les surfaces hôtes qui en découlent (chemin de code séparé, `grimoire.hosts.emitters`). `up` exécute désormais une étape `host_sync` après `refresh` et `standard` qui appelle la même synchronisation que `grimoire host sync --host all` ; `host sync --dry-run` exécuté juste après `up` ne trouve plus rien à écrire (#296).
- fix(cli): un projet qui déclare déjà l'archétype `agentic-standard` restait en FAIL permanent après `grimoire up` — le profil `starter` (choix par défaut sans `--needs`) ne couvre pas les acceptance criteria hard de la DNA de cet archétype (`llm-provider-registry.yaml`, `compliance-declaration.md`). `up` résout désormais le need `provider-neutral` (-> profil `controlled`) par défaut quand cet archétype est déjà déclaré ; `knowledge-source-registry.yaml`, écrit seulement à partir du profil `orchestrated`, rejoint les chemins « déclarés avant usage récurrent » que `doctor` ne réclame plus tant qu'aucune source n'est indexée (#295).
- fix(cli): `grimoire doctor -o json .` (l'option après le sous-commande, telle que documentée par `doctor --help`) échouait avec « No such option: -o » — seule la forme globale `grimoire -o json doctor .` fonctionnait. `doctor` accepte désormais un `--output`/`-o` local (même contrat que le `-o` global : mêmes contrôles, même structure JSON) (#293).
- fix(policies): `tool_pattern` des politiques temporelles n'était comparé qu'au nom nu de l'outil, si bien qu'un motif documenté comme `Bash(git push:*)` ou `Bash(rm:*)` ne matchait jamais — `require_approval: true` répondait `allow` dès le premier appel. Introduit une clé d'outil façon permissions Claude Code (`Bash(<commande complète>)`, `<Tool>(<file_path>)`) contre laquelle le motif parenthésé est désormais comparé, rétro-compatible avec les motifs nus (`"*"`, `"Bash"`) (#449).
- fix(core): déclarer `source.assist` (`model`, `allow_lan`) dans le schéma et le validateur, Python et Rust, pour que `grimoire check .` accepte l'exemple documenté de l'assistant Source au lieu de refuser `source` comme clé inconnue (#451).
- fix(cockpit): l'assistant Source distingue le chargement du modèle local d'un dépassement de délai — `GET /api/workspace/assist` sonde `/api/ps`, déclenche lui-même le chargement (`keep_alive`, sans générer) et répond « chargement du modèle » ; le bouton **Suggérer** reste visible mais désactivé et se re-sonde toutes les 3 s au lieu d'échouer au premier clic (#450).

## [3.46.0] - 2026-09-12

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

## [3.45.0] - 2026-09-12

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

## [3.44.2] - 2026-09-11

- **fix(hosts): `collect_agents` résout ses propres skills quand `known_skills` n'est pas fourni — `SessionStart` et le porteur de proposition ne plantent plus sur un agent à skills attachés (#423).**
  `entry_persona_context` et `_category_carrier` (déclencheur de propositions)
  appelaient `collect_agents(project_root)` sans lui passer l'inventaire des
  skills du projet ; le défaut retombait alors sur un ensemble vide, donc
  *tout* agent déclarant un `skills:` en frontmatter — la forme par défaut
  des archétypes depuis #377/#387 — échouait fail-closed comme si ses
  skills n'existaient pas. `collect_agents(project_root, *, known_skills=None, …)`
  résout maintenant lui-même l'inventaire via `collect_skills(project_root)`
  quand `known_skills` vaut `None`, pour qu'aucun appelant ne puisse
  retomber dans ce trou ; un inventaire explicite reste accepté pour les
  appelants qui en réutilisent un déjà collecté (`build_surface`, etc.).

- **perf(hosts): cache JSON de l'état du standard, invalidé par empreinte — `grimoire-hook` évite `ruamel.yaml` sur cache chaud (#419).**
  Suite du découpage de `hosts/decisions.py` (#420) : le profil résiduel
  identifiait `grimoire.core.standard_state` (`active_task_id`/`active_profile_id`)
  + `ruamel.yaml` comme le seul poste encore compressible, ~9 ms sur
  `PreToolUse` destructif. Deux changements distincts s'additionnent :
  l'import de `ruamel.yaml` dans `standard_state.py` était au niveau module
  (payé par *tout* appel de hook, même sur un projet non enrôlé, sans
  standard-profile.yaml ni task-board.yaml à lire) — il est maintenant local
  à `_load_mapping`, donc jamais payé quand ces fichiers n'existent pas. Sur
  un projet enrôlé, où ils existent, un cache JSON (stdlib `json`, jamais
  `ruamel`) sous `_grimoire-output/.runs/standard-state-cache.json` — même
  emplacement que l'état de session éphémère existant, gitignoré — mémorise
  `profile_id` et les tâches `in_progress` du board, invalidé par empreinte
  fichier (`[mtime_ns, size]`) : une source modifiée est relue et le cache
  regénéré ; un cache absent, tronqué ou au mauvais format retombe
  silencieusement sur la lecture YAML, jamais une erreur de hook. Écriture
  atomique (fichier temporaire + `os.replace`). Un outil MCP annoté
  `readOnlyHint` (`grimoire_host_status`, `task_recall`) ne doit rien écrire :
  `active_profile_id`/`resolve_active_task`/`active_task_id` et
  `hosts.collect.build_surface` acceptent un `write_cache: bool = True` que
  ces deux tools seuls mettent à `False` — ils lisent un cache déjà chaud
  sans jamais le créer ni le corriger. Le cache est aussi invalidé
  (supprimé, best-effort) juste après une écriture connue de ces YAML —
  `setup_standard_profile`, `TaskService.project_board`, `grimoire task
  board export` — pour que le changement soit visible dès l'appel de hook
  suivant plutôt que celui d'après ; pas requis pour la correction, l'empreinte
  périmée suffit, seulement pour la latence d'un cas rare.

  Mesuré par `scripts/bench-rust-cores.py --macro-runs 15` (même machine,
  projet de bench isolé non enrôlé — c'est le seul fixture du script, donc la
  seule chose qu'il mesure ici est l'import `ruamel` devenu conditionnel) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire-hook PreToolUse` (tool-policy, destructif — mesure officielle #418/#419) | 62,7 ms | 55,1 ms | < 50 ms | **non atteinte** |

  Le cache lui-même n'a rien à faire sur un projet non enrôlé (aucun YAML à
  mettre en cache) : mesuré séparément sur un projet **enrôlé** (`standard
  init --profile governed`, `_grimoire/standard/standard-profile.yaml` +
  `task-board.yaml` réels), même méthodologie (médiane de 15 exécutions) :

  | événement (décision) | avant | après |
  |---|---|---|
  | `PreToolUse` (tool-policy) | 75,7 ms | 54,2 ms |
  | `SessionStart` (activation) | 73,1 ms | 70,3 ms |
  | `UserPromptSubmit` (task-context) | 58,9 ms | 39,6 ms |
  | `PostToolUse` (evidence-trace) | 57,0 ms | 49,0 ms |
  | `SubagentStop` (subagent-gate) | 131,7 ms | 127,9 ms |
  | `PreCompact` (context-capsule) | 132,1 ms | 121,2 ms |
  | `Stop` (evidence-gate) | 144,4 ms | 127,9 ms |

  (Les trois derniers événements sont plus lents dans l'absolu sur un projet
  `governed` : `evidence-gate`/`context-capsule`/`subagent-gate` y exercent de
  vraies vérifications de gate, hors périmètre de ce cache.) `cProfile` sur
  cache chaud (`PreToolUse`, projet enrôlé) : ni `ruamel` ni
  `grimoire.core.standard_state._load_mapping` n'apparaissent plus dans le
  profil — confirmé aussi par un `sys.modules` vide de tout module `ruamel.*`
  en sous-processus frais. **Cible des 50 ms toujours non atteinte sur la
  mesure officielle (projet non enrôlé)**, assumé : il ne reste, sur ce
  chemin, que le coût `dataclasses`/`inspect` de la première dataclass
  chargée dans le process (stdlib, ~6-9 ms, inhérent à `@dataclass` sur
  `HookInput`/`Decision`/`ToolFacts`/`ActiveTask` — `dataclasses.py` importe
  `inspect` sans condition, aucune combinaison de `frozen`/`slots` n'y
  change quoi que ce soit) et le moteur de politique lui-même
  (`policies.engine`/`policies.schemas`, ~5 ms, propre à `tool-policy`,
  aucun fichier lu). Les deux sont documentés comme incompressibles avec ce
  mécanisme par #419 lui-même ; ni l'un ni l'autre n'a de solution triviale
  sans réécrire ces classes à la main.
  Tests : `tests/unit/core/test_standard_state.py` (cache créé/lu/régénéré
  sur source modifiée/corrompu/absent, `invalidate_cache`) ; garde de
  non-régression `tests/unit/test_hook_cost.py::test_a_warm_standard_state_cache_never_imports_ruamel`
  (sous-processus froid puis chaud, `sys.modules` sans aucun `ruamel.*` au
  second) ; `tests/unit/mcp/test_server.py::TestReadOnlyToolsWriteNothing`
  (déjà existant, aucune modification requise) couvre le contrat
  `write_cache=False`. Aucun changement de décision : les tests existants de
  `hosts/decisions.py` et `test_hosts.py` passent sans modification.

## [3.44.1] - 2026-09-11

- **perf(hosts): découper `hosts/decisions.py` par décision — `grimoire-hook` payait la moitié du fichier pour chaque appel (#419).**
  Suite de mesure de #418 : `grimoire-hook PreToolUse` restait à 69-70 ms
  malgré le chargement paresseux du CLI, parce que ce point d'entrée
  (`grimoire.hosts.runtime:main`) ne passe jamais par `grimoire.cli.app`.
  `grimoire.hosts.decisions` était un seul module de 918 lignes portant les
  sept décisions (`grimoire.activation`, `.task-context`, `.tool-policy`,
  `.evidence-trace`, `.evidence-gate`, `.subagent-gate`, `.context-capsule`) :
  quelle que soit la décision demandée, tout le fichier — et donc le moteur
  de politique (`grimoire.policies.engine`/`.schemas`) — était importé. C'est
  devenu un paquet, un module par décision, résolu paresseusement par
  `grimoire.hosts.decisions.run_decision` via un registre id → module ;
  `__init__.py` ré-exporte tous les noms publics existants (et le privé
  `_gate_summary` qu'un test pinne) par `__getattr__` de module, donc aucun
  import existant ne change. `grimoire.hosts.surface` (446 lignes : IR
  agent/modèle, fingerprinting, sonde du cœur Rust) est passé par le même
  chemin : les trois enums que le hook lit sur *chaque* appel (`HookEvent`,
  `ToolVerb`, `Enforcement`) vivent maintenant dans `grimoire.hosts.events`
  (nouveau, réexporté par `surface.py` pour compatibilité) — et
  `grimoire/hosts/__init__.py`, qui importait `ProjectSurface` (donc tout
  `surface.py`) au niveau module rien que pour une ré-export dont rien dans
  le dépôt ne se sert (`from grimoire.hosts import ProjectSurface`), le fait
  maintenant paresseusement lui aussi.
  Mesuré par `scripts/bench-rust-cores.py --macro-runs 15` (même machine) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire-hook PreToolUse` (tool-policy, destructif) | 70 ms | 64 ms | < 50 ms | **non atteinte** |
  | `grimoire-hook PreToolUse` (lecture seule) | 69 ms | 58 ms | — | — |
  | `grimoire-hook SessionStart` | 77 ms | 72 ms | — | — |
  | `grimoire-hook UserPromptSubmit` | 63 ms | 49 ms | — | atteinte |
  | `grimoire-hook PostToolUse` | 64 ms | 57 ms | — | — |
  | `grimoire-hook SubagentStop` | 64 ms | 50 ms | — | — |
  | `grimoire-hook PreCompact` | 65 ms | 50 ms | — | — |
  | `grimoire-hook Stop` | 69 ms | 56 ms | — | — |

  **Cible des 50 ms non atteinte pour `PreToolUse`, assumé.** Le profil
  restant après découpage tient en trois postes incompressibles avec ce
  mécanisme : le coût `dataclasses`/`inspect` de la première dataclass
  chargée dans le process (stdlib, ~6-9 ms), `grimoire.core.standard_state`
  + `ruamel.yaml` (~9 ms — nécessaire : même une commande destructive lit
  `active_task_id`/`active_profile_id` avant que le moteur de politique ne
  tranche, sur tous les événements sauf aucun), et le moteur de politique
  lui-même (`policies.engine`/`policies.schemas`, ~5 ms, propre à
  `tool-policy`). Découper `decisions.py` plus finement ne change rien à ces
  trois postes ; les réduire est un chantier distinct (bascule de parseur
  YAML pour le premier, hors périmètre — `ruamel.yaml` est la seule
  dépendance YAML déclarée du kit, `pyyaml` n'est que transitive).
  Garde de régression : `tests/unit/test_hook_cost.py` — un sous-processus
  propre par décision vérifie qu'exécuter une décision n'importe jamais le
  module d'une décision sœur (`test_running_one_decision_imports_only_its_own_submodule`,
  paramétré sur les sept), plus un budget de temps large et portable dans le
  même esprit que `_COCKPIT_REFRESH_BUDGET_SECONDS`
  (`tests/test_invariants.py`).

- **perf(cli): charger les sous-commandes à la demande — 60 % du temps de `doctor` était l'arbre Typer (#405).**
  `grimoire.cli.app` importait sans condition les 20 modules `cmd_*` derrière
  chaque sous-commande (`cmd_flow`, `cmd_host`, `cmd_memory_lexical`,
  `cmd_cockpit`, `cmd_up`...) pour construire l'arbre Typer/Click, quelle que
  soit la commande demandée — `grimoire --version` payait donc l'import de
  `grimoire.flows`, `grimoire.missions.dispatch`, `grimoire.memory`, etc.
  `grimoire.cli._lazy.LazyTyperGroup` (nouveau, `cls=` du `typer.Typer`
  racine) remplace `add_typer()`/`command()` pour ces sous-commandes par un
  registre nom → (module, attribut, aide courte, panneau, visibilité) ; le
  module n'est importé qu'à la résolution réelle de la commande (dispatch, ou
  son propre `--help`) — `grimoire --version` et `grimoire doctor .` ne
  touchent plus aucun d'entre eux. `grimoire.cli.cmd_up` importait en outre
  `cmd_init`/`core.scaffold`/`hosts.sync` à son propre niveau module rien que
  pour les fonctions que `doctor` utilise réellement (`run_env_checks`) —
  ces imports descendent maintenant dans les fonctions qui les utilisent
  vraiment (`up()`, `_step_init`, `repair_project_artifacts`).
  `grimoire --help` et l'aide de chaque sous-commande restent identiques au
  caractère près (ordre des panneaux et des lignes inclus, garanti par un
  registre `order` explicite dans `LazyTyperGroup.configure`) —
  `scripts/compare-cli-help.py` (nouveau) le vérifie avant/après pour les 47
  commandes. Garde de régression : `tests/unit/cli/test_lazy_startup.py`
  (sous-processus propre) échoue si l'import de `grimoire.cli.app` amène
  `grimoire.flows`, `grimoire.missions.dispatch`, `grimoire.memory` ou
  `grimoire.cli.cmd_cockpit` dans `sys.modules`.
  Mesuré par `scripts/bench-rust-cores.py` (médiane de 15, même machine) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire --version` | 313 ms | 89 ms | < 150 ms | atteinte |
  | `grimoire doctor .` | 448 ms | 270 ms | < 300 ms | atteinte |
  | `grimoire-hook PreToolUse` | 70 ms | 69 ms | < 50 ms | **non atteinte** |

  `grimoire-hook` (point d'entrée séparé, `grimoire.hosts.runtime:main`) ne
  passe jamais par `grimoire.cli.app` et n'est donc pas concerné par ce
  mécanisme ; son coût restant vient de `grimoire.hosts.decisions` (918
  lignes, tous les types de décision dans un seul module) et de
  `grimoire.hosts.capabilities`/`.surface` — hors du périmètre de ce ticket
  (« enregistrer les sous-commandes sans importer leur module », pas refondre
  le moteur de décision des hooks). `grimoire-mcp` (`grimoire.mcp.server`)
  vérifié : n'importe déjà pas `grimoire.cli.app`, rien à faire.

- fix(flows): un abandon (ou un refus MAST) avant tout progrès n'affiche plus tous les nodes comme complétés dans `grimoire flow status` (#414). `FlowEngine.status()` dérivait `completed_nodes`/`pending_nodes` de la seule position de `current_node` dans l'ordre topologique — sur un run ABORTED/REFUSED, `current_node` devient `None` (terminal) exactement comme un run réussi, donc l'ancien découpage rendait `completed_nodes == order` en entier même quand *aucun* node n'était réellement fait. `completed_nodes` reflète désormais les `completed_steps` du dernier checkpoint réel du `RuntimeKernel` sur un run mort (liste vide si aucun checkpoint n'existe encore, c'est-à-dire un abandon avant le premier `resume()`) ; `pending_nodes` reste vide par convention (on ne sait pas si l'hôte comptait reprendre). Corrigé des deux côtés en même temps, même verdict : `rust/grimoire-flows-core/src/lib.rs` (`status_slices_core`) miroité, `grimoire flow status` (texte et JSON) en hérite sans changement de code propre. Nouveaux tests de parité sous les deux backends (`GRIMOIRE_FLOWS_BACKEND=python|rust`) : abandon avant tout progrès, abandon après k nodes, refus avant progrès, complétion normale (inchangée) — `tests/unit/test_flows_rust_parity.py`.

## [3.44.0] - 2026-09-11

- feat(dispatch): cinquième port Rust optionnel de la cascade de dispatch et du routage de fournisseurs — `start_tier_for`/`_tier_chain` (plancher et chaîne de paliers par classe de vérifiabilité), la résolution `--start-tier` explicite contre le plancher, `render_invocation` (rendu d'un gabarit en argv), `_looks_rate_limited`, `_extract_cost_usd`/`_uncertainties_search_text`/`_extract_uncertainties` (analyse de la sortie d'un ouvrier délégué), `_matches_review_surface`/`_classify_review` (classification de revue), `DispatchReport.succeeded`/`.exit_code`/`.refusal_message`, et l'ordre/filtre de refroidissement de `providers.routing.candidates` (issue #354). `rust/grimoire-dispatch-core/` (dépendance `serde_json` épinglée `=1.0.151`, strict RFC 8259), bascule `GRIMOIRE_DISPATCH_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.missions.dispatch` et `grimoire.providers.routing`, qui ne s'importent pas l'un l'autre), jobs CI `rust-dispatch / cargo` et `rust-dispatch / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Deux défauts trouvés par l'oracle Rust en portant `_extract_cost_usd`, corrigés côté Python dans cette même PR : `isinstance(True, int)` est vrai en Python, donc un `total_cost_usd` JSON `true`/`false` était jusqu'ici coercé en `1.0`/`0.0` — désormais exclu explicitement des deux côtés ; `json.loads` de CPython accepte par défaut les jetons hors RFC 8259 `NaN`/`Infinity`/`-Infinity`, que le cœur Rust (`serde_json`, strict) n'accepte pas nativement — **corrigé côté Python : JSON strict** (`_reject_non_standard` passé en `parse_constant` à chaque `json.loads` de `dispatch.py`, rattrapé comme un JSON invalide), un document portant un de ces jetons — même ailleurs que dans le champ lu — est désormais rejeté en bloc des deux côtés (coût absent, aucune incertitude extraite), jamais l'inverse (Rust est l'oracle, pas Python). Nouveau corpus fixture de dix sorties d'ouvrier réalistes (`tests/fixtures/dispatch_worker_outputs/`, aucune sortie enregistrée réelle trouvée dans le dépôt) et fuzz léger (200 chaînes aléatoires) prouvant qu'aucune des deux fonctions d'analyse ne lève jamais, sous aucun backend.

- feat(flows): quatrième port Rust optionnel de la machine à états du moteur de flows — `check_output_against_contract`, le calcul du node courant (`FlowEngine._current_node`), la décision de `resume()`/le calcul de `ResumeOutcome`, le découpage completed/pending de `status()`, et la table de transitions de `RuntimeKernel` (`_transition`/`advance_step`) (issue #354). `rust/grimoire-flows-core/`, bascule `GRIMOIRE_FLOWS_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.flows.engine` et `grimoire.runtime.kernel`), jobs CI `rust-flows / cargo` et `rust-flows / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Divergence trouvée par l'oracle Rust et corrigée dans cette PR : `FlowEngine._TERMINAL_STATUSES` omettait `REFUSED` (terminal côté `RuntimeKernel` depuis les plafonds MAST, B11) — `resume()` sur un run REFUSED levait une erreur au message générique du kernel au lieu du message nommé de `resume()`, et `status()` affichait un `current_node`/`pending_nodes` périmés comme si le run continuait de progresser ; `REFUSED` ajouté à `_TERMINAL_STATUSES`, les deux backends s'accordent désormais. Comportement documenté et volontairement non changé : `status()` dérive `completed_nodes` de la position de `current_node` dans l'ordre topologique — un run ABORTED/REFUSED avant tout progrès affiche tous les nodes comme complétés, les deux backends reproduisant ce même comportement à l'identique (voir `rust/grimoire-flows-core/src/lib.rs` et l'issue de suivi citée dans la PR). Nouveau test de parité sur le corpus réel (`registry/blueprints/*.blueprint.json`) et sur la grille complète des 9×9 transitions de `WorkflowStatus`.

- feat(hosts): troisième port Rust optionnel de `grimoire.hosts.collect`/`grimoire.hosts.surface` — lecture du frontmatter d'agent et garde de distinction (issue #354). `rust/grimoire-hosts-core/`, bascule `GRIMOIRE_HOSTS_BACKEND=python|rust|auto`, jobs CI `rust-hosts / cargo` et `rust-hosts / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Divergences trouvées par l'oracle Rust : `_max_turns` plantait avec un `ValueError` non rattrapé sur un chiffre Unicode non-ASCII (`"²"`, `str.isdigit()` vrai mais `int()` refuse) — corrigé dans cette PR (`str.isascii()` en plus) ; `_tool_verbs` ignore un verbe d'outil hors de `ToolVerb` — le périmètre d'outils reste inchangé (changer ça modifierait silencieusement celui d'agents existants), mais le rejet n'est plus silencieux : `collect_agents`/`build_surface` remonte désormais une note `ProjectSurface.notes` par agent nommant les jetons rejetés, identique sous les deux backends. Nouveau test de parité sur le corpus réel (`archetypes/*/agents/*.md`) prouvant que `yaml-rust2` et ruamel s'accordent sur ce que le kit expédie effectivement.

## [3.43.1] - 2026-09-11
### Corrigé

- **fix(core): le validateur ne plante plus sur les champs énumérés et type-vérifie enfin cinq champs déclarés chaînes (#409).**
  `src/grimoire/core/validator.py` comparait `valeur not in <frozenset>` pour
  neuf champs énumérés (`project.type`, `user.skill_level`,
  `memory.backend`/`short_term_backend`, les cinq modes de couche mémoire,
  `agents.archetype`) — une liste ou une table en entrée levait
  `TypeError: unhashable type` au lieu d'une erreur de validation propre (un
  scalaire hachable non-chaîne, lui, ne plantait pas et reste traité comme
  n'importe quelle valeur inconnue : comportement inchangé, aligné sur
  l'oracle Rust). `project.repos[].name`, `project.repos[].path`/
  `default_branch`, les éléments d'`installed_archetypes[]` et
  `user.name`/`language`/`document_language` n'étaient en outre jamais
  type-vérifiés, bien que `schema.py` les déclare comme des chaînes. Le cœur
  Rust du validateur (#392) rejetait déjà proprement ces cas et servait
  d'oracle ; `tests/unit/test_schema_validator_rust_parity.py` passe des
  divergences documentées à la parité stricte entre les deux backends.

- **fix(doctor): ne plus lire un spec de paquet npx/uvx/pipx/bunx/docker/podman comme un chemin (#393).**
  `.mcp.json server '<nom>': path '<spec>' does not exist` était un faux
  positif dès que `command` était un lanceur de paquet (ex. `npx`) et que le
  premier argument contenait un `/` et une version (`@playwright/mcp@0.0.80`
  ressemblait à un chemin selon l'heuristique). Pour ces lanceurs, `doctor`
  vérifie désormais seulement que le lanceur est sur le PATH (FAIL sinon,
  remède « installer <lanceur> ») et rapporte le paquet en INFO sans le
  tester comme chemin. Le contrôle est inchangé pour les commandes qui
  pointent vers un fichier ou un binaire.

- fix(core): la persona d'entrée (`concierge`) n'est plus jamais retenue
  comme porteuse d'un skill proposé (#402). Le déclencheur de propositions
  (#395) traitait tout agent de repli comme un porteur valide ; comme le
  concierge est presque toujours ce repli (c'est lui qui fait le triage),
  presque toute proposition devenait « skill attaché à concierge », contraire
  à la doctrine (un skill s'attache à l'agent qui fait le travail). Un repli
  qui est la persona d'entrée est désormais traité comme une absence de
  repli : une recherche par catégorie parmi les agents déclarés (`use_when`,
  ou faisceau `execute` pour les catégories d'exécution) propose un porteur à
  sa place si un seul agent convient, sinon la proposition redevient un
  agent. `fallback_agent` reste le fait brut observé ; le nouveau champ
  `carrier_reason` explique le choix, affiché par `grimoire proposals list`
  et le cockpit.

### Ajouté

- **`rust-gate` devient l'unique check requis pour les deux cœurs Rust
  optionnels (#407, #408).** `rust-policies-core.yml` et
  `rust-schema-core.yml` étaient filtrés par `paths:` : absents de toute PR
  hors de leur périmètre, un check requis qui ne se déclare jamais bloquait
  ces PR-là (#397 a fusionné avec `cargo fmt` rouge faute de protection
  possible). `rust-cores.yml` se déclenche sans filtre de chemin sur toute
  PR vers `main` ; un job `changes` calcule quel(s) crate(s) sont touchés,
  les jobs par crate hors périmètre sont sautés (satisfaisant un check
  requis), et `rust-gate` agrège les quatre jobs comme seul check requis à
  ajouter à la protection de `main`.

- **chore(bench): mesurer le gain réel des cœurs Rust optionnels (#354).**
  `scripts/bench-rust-cores.py` compare Python et Rust pour les deux cœurs
  livrés à ce jour (`grimoire.policies.engine.PolicyEngine.evaluate` #363,
  `grimoire.core.schema.generate_schema` / `grimoire.core.validator.validate_config`
  #392) sur trois plans : micro (temps par appel, `timeit`, entrée réaliste
  et entrée volumineuse), macro (`grimoire doctor`, `host sync`, `standard
  verify`, décision `PreToolUse`, médiane de 10 exécutions contre le coût
  fixe de `grimoire --version`), et profil (`importtime` + `cProfile` sur
  `grimoire doctor .`). Verdict mesuré publié en commentaire sur #354 :
  aucun des deux ports ne change un temps perceptible par l'utilisateur,
  le surcoût de la frontière PyO3 dépassant le gain sur ces tailles
  d'entrée. `docs/rust-cores-benchmark.md` documente comment le relancer.

## [3.43.0] - 2026-09-11
### Ajouté

- **feat(core): instrumenter les non-choix du concierge (#389).** Symétrique
  du choix d'agent (#366) : quand le concierge cherche un spécialiste et n'en
  trouve aucun, ou se rabat sur un généraliste, `grimoire agent-miss`
  journalise le fait dans le même TraceLedger — catégorie de la demande,
  spécialité cherchée si nommable, agent de repli, raison, jamais le contenu
  de la demande. La résolution se fait dans le raisonnement de la persona
  concierge (`archetypes/meta/agents/concierge.md`, désormais instruite
  d'appeler cette commande), pas dans du code du kit ; c'est donc le seul
  canal d'écriture. `grimoire registry dispatches` lit désormais les deux
  côte à côte : choix par agent, non-choix agrégés par spécialité manquante
  avec leur compte. Écriture best-effort, comme son symétrique.

- **feat(core): proposer un artefact à la répétition d'un non-choix (#395).**
  Le déclencheur lit `TraceLedger.agent_miss_counts()` (#394, désormais enrichi
  de la catégorie et de l'agent de repli les plus récents) et, quand une même
  spécialité manquante atteint un seuil configurable (`proposals.threshold`
  dans `project-context.yaml`, défaut 2, jamais 1), écrit une proposition
  `pending` sous `_grimoire-output/proposals/<slug>.yaml` — nom, rôle,
  `use_when`/`dont_use_when`/`tools` déduits mécaniquement des étiquettes
  observées, jamais d'appel LLM. Type d'artefact selon la doctrine
  (`docs/artifact-doctrine.md`) : un agent de repli déjà observé propose un
  skill à lui attacher, son absence propose un agent. Jamais de création sans
  acceptation explicite (`grimoire proposals accept <slug>`, aucune option
  `--auto`), qui écrit l'artefact réel via le même chemin que l'outil d'ajout
  d'agent réparé par #367 (`grimoire.tools.agent_creation`, désormais partagé)
  et annule si la garde de distinction (#372) refuse le faisceau. Refuser
  (`grimoire proposals reject <slug>`) marque la proposition ; la même
  spécialité n'est reproposée que si son compte a doublé depuis le refus.
  `grimoire proposals list` en CLI, section Propositions du cockpit (espace
  Piloter, #382) avec les mêmes deux actions, et une ligne SessionStart à côté
  des fournisseurs (« N proposition(s) d'artefact en attente ») ; clé
  `proposals` dans le schéma (`grimoire.core.schema`) et le port Rust
  (`rust/grimoire-schema-core/`).

- **feat(core): règle de fraîcheur des agents — signaler, jamais retirer (#396).**
  Un agent livré ou en override qui n'apparaît dans aucun `agent.dispatch`
  du journal de traces depuis `agents.freshness_threshold_days` jours
  (défaut 90, configurable dans `project-context.yaml`) est signalé, jamais
  retiré ni déprécié automatiquement. `grimoire doctor` gagne un contrôle
  `agent_freshness` (INFO si le journal a moins de N jours d'historique —
  l'absence de données n'est jamais une absence d'usage —, WARN sinon,
  jamais FAIL) ; `grimoire registry dispatches` gagne la liste des agents
  jamais choisis ou périmés sur la période ; le cockpit (section agents,
  #382) affiche « jamais invoqué » ou « il y a N jours » dans la colonne
  d'usage et un badge « périmé » au-delà du seuil. Calcul centralisé dans
  `grimoire.traces.ledger.compute_agent_freshness`, composé pour les trois
  surfaces par `grimoire.core.agent_freshness`. Le seuil est aussi porté au
  port Rust du schéma (`rust/grimoire-schema-core/`).

- feat(core): second port Rust optionnel, `rust/grimoire-schema-core/` — `grimoire.core.schema.generate_schema` et `grimoire.core.validator.validate_config` bascule sur `GRIMOIRE_SCHEMA_BACKEND=python|rust|auto` (sœur de `GRIMOIRE_POLICIES_BACKEND`), repli Python inchangé par défaut, sans roue publiée (#354). Le cœur Rust rejette explicitement des entrées que le validateur Python de référence laisse aujourd'hui passer sans erreur (`installed_archetypes[]`, `project.repos[].name`, `user.name`/`language`/`document_language` jamais type-vérifiés malgré `schema.py`) ou fait planter (`TypeError: unhashable type` sur un `type`/`backend`/... de forme liste ou table) — vérifié dans les deux configurations, contrat des tests existants inchangé.

### Corrigé

- fix(core): la règle de fraîcheur ne juge pas un agent plus jeune que le seuil (fichier de définition récent, marqué « trop récent ») ; job cargo remis au vert (#398)

## [3.42.0] - 2026-09-11
### Ajouté

- **Doctrine de création d'artefact et sa garde (#370).** `docs/artifact-doctrine.md`
  pose le critère de nécessité d'un artefact (décision écrite d'avance, qui
  l'invoque) et la règle de frontière d'outils, avec renvois depuis
  `creating-agents.md` et `workflow-taxonomy.md`. `use_when`, `dont_use_when`,
  `tools` et `tool_boundary` deviennent des champs obligatoires du frontmatter
  de tout agent livré sous `archetypes/*/agents/` ; les 33 agents existants
  reçoivent une frontière écrite à la main, et `test_artifact_employment_clause.py`
  fait échouer tout agent livré sans les quatre champs.
- **Le contexte déclaré d'un agent entre dans le contrat qu'on lui remet (#378).**
  `build_prompt` lit désormais le `context` déclaré de l'agent dispatché
  (`--agent` de `grimoire task dispatch`, ou l'agent unique d'un
  `DispatchExecutor` de flow) et l'ajoute au contrat de la tâche, sans
  régression pour un agent sans contexte déclaré ou un `--agent` inconnu.
  `grimoire host status` affiche aussi les collisions de faisceau entre
  agents livrés par le kit, jusqu'ici invisibles.
- **Archétype `platform-engineering` refait — 2 agents à faisceau distinct plus
  2 skills attachés (#375).** `platform-architect` et `backend-engineer`
  restent des agents ; `deploy-orchestrator` et `reliability-engineer`, au
  même faisceau `{read, edit, execute}` que `backend-engineer` sans frontière
  propre, deviennent les skills attachés `platform-deploy-release` et
  `platform-reliability-sre`. Aucun savoir-faire supprimé.
- **Archétype `creative-studio` refait — 3 agents à faisceau distinct plus 1
  skill attaché (#375).** `blender-expert` et `illustration-expert` restent
  des agents (contexte propre via MCP + boucle vision) ; `content-creator`,
  au même faisceau que `brand-designer` sans frontière propre, devient le
  skill attaché `creative-content-copywriting`. Aucun savoir-faire supprimé.
- **Refonte de l'archétype `meta` — généraliste plus skills attachés (#375).**
  Les 3 agents à faisceau distinct (`concierge`, `agent-optimizer`, `security-auditor`)
  restent ; `art-director`, `creative-toolsmith`, `memory-keeper` et
  `project-navigator`, tous indiscernables au sens de la garde de #372,
  deviennent quatre skills attachés à `agent-optimizer` sans rien perdre du
  savoir-faire ; `security-auditor` gagne un `context:` propre qui le distingue
  désormais de `fix-loop-orchestrator`. `scaffold._plan_meta_agents` copie
  maintenant `archetypes/meta/skills/*.md`, meta étant déployé à tout projet
  indépendamment de l'archétype choisi.
- **Archétype `stack` refait — un généraliste plus sept skills attachés (#375).** Les sept experts par techno (`python-expert`, `go-expert`, `typescript-expert`, `docker-expert`, `terraform-expert`, `ansible-expert`, `k8s-expert`) partageaient le même faisceau outils et aucun contexte propre — c'était un seul agent décrit sept fois. Ils deviennent des skills attachés à un nouvel agent généraliste, `stack-engineer` ; le corps de chaque agent devient le corps de son skill, rien n'est supprimé. `grimoire init --archetype stack` livre désormais un agent, et une composition `web-app,stack` reste sans note de collision pour cet archétype.
- **Archétypes `web-app` et `fix-loop`/`minimal` alignés sur la forme validée
  d'`infra-ops` (#375).** `web-app` livrait deux agents (`frontend-specialist`,
  `fullstack-dev`) au même faisceau d'outils exact `{read, edit, execute}`,
  sans contexte ni skill propres — la garde de distinction (#372) les
  déclarait indiscernables. `frontend-specialist` devient le skill attaché
  `web-frontend-ux` (composants, accessibilité WCAG 2.1 AA, revue UX,
  performance UI) porté par `fullstack-dev`, son corps repris sans perte.
  `fix-loop` (un seul agent, contexte propre) et `minimal` (gabarit `.tpl`
  jamais scaffoldé comme agent) étaient déjà conformes, aucun changement.
  Connu et hors périmètre : `fix-loop-orchestrator` partage son faisceau avec
  `security-auditor` (archétype `meta`) — dette à régler côté méta.
- **Archétype `infra-ops` refait — un généraliste plus quatre skills attachés
  (#380).** infra-ops livrait sept agents dont cinq au même faisceau exact
  `{read, edit, execute}`, sans contexte ni skill propres. Réduit à trois
  agents à faisceau distinct (`ops-engineer` généraliste, `monitoring-specialist`,
  `systems-debugger`) ; `pipeline-architect`, `security-hardener`,
  `backup-dr-specialist` et `k8s-navigator` deviennent quatre skills attachés
  à `ops-engineer`, corps repris sans perte. `layout.skill_dirs()` et
  `scaffold._plan_archetype_agents` transportent désormais les skills
  d'archétype, jusque-là sans mécanisme de copie.
- **Équipes fantômes retirées, périmètre de `infra-ops` documenté (#350).**
  Les trois manifestes d'équipe livrés (team-build, team-ops, team-vision)
  nommaient neuf agents d'une pile jamais fournie par aucun des neuf
  archétypes du kit ; supprimés, avec un test de garde qui échoue si un
  manifeste cite un agent absent. `infra-ops` documente désormais son
  périmètre réel (laboratoire personnel Proxmox/K3s/FluxCD) dans le wizard
  `grimoire init` et `archetype.dna.yaml`, plutôt que de laisser croire à un
  usage cloud/entreprise sous un nom générique.
- **Nouvel agent de sécurité offensive `security-auditor` (#357).**
  Cartographie de surface, fuzzing (`hypothesis` en cœur de métier, `atheris`
  pour les formats non structurés) et rétro-ingénierie Ghidra cadrée aux
  seuls binaires — jamais au code Python source. Première campagne réelle
  sur `grimoire.providers.registry.read_registry` : 0 plantage sur ~850
  exemples générés plus 11 graines fixées, rejouables via un corpus versionné.
- **Une seule commande de service, ouverte sur le projet courant (#356).**
  `serve` et `cockpit serve` ouvraient la même interface pour la seule
  différence mono/multi-projets. `cockpit serve`/`start` détecte le dossier
  courant, enrôle le projet au registre s'il n'y était pas, et l'ouvre
  sélectionné ; `serve` devient un alias déprécié qui délègue. Un port
  occupé propose une alternative libre au lieu d'afficher seulement
  l'exception.
- **Voir et configurer les agents du projet depuis le cockpit (#382).** La
  fiche projet de Piloter gagne une table des agents (couche, outils,
  skills, usage réel agrégé depuis le journal de traces) ; l'inspecteur
  permet d'assigner ou retirer des skills et d'éditer clause d'emploi,
  outils et contexte, avec revalidation via `collect_agents` avant toute
  écriture — un skill ou un contexte inconnu annule l'écriture.
- **Skills attachés aux agents par défaut, payées seulement à l'usage
  (#377).** `AgentSpec` gagne `skills` (résolus contre l'inventaire collecté,
  fail-closed sur un slug inconnu) et `context` ; un skill déclaré par un
  agent est replié dans le fichier de cet agent (Claude Code, Copilot)
  plutôt qu'émis au niveau du projet, où chaque tour de session en paierait
  la description qu'il serve ou non. Mesuré : 1480 tokens/tour avec dix
  skills transversales contre 0 avec les mêmes dix attachées. Une garde de
  distinction fait échouer `build_surface()` sur deux agents des overrides
  d'un projet qui partagent le même faisceau outils + contexte + skills
  (note non bloquante quand les deux viennent du kit — dette connue).
- **Le fichier d'agent émis ne charge que le contexte déclaré (#379).** Un
  agent qui déclare `context:` reçoit dans son fichier `.claude/agents/*.md`
  (et `.github/agents/*.agent.md`) une instruction d'activation qui charge
  ces chemins-là, et eux seuls, à la place du contexte partagé par défaut
  qu'il n'a pas demandé. Un agent sans déclaration reçoit exactement ce qu'il
  recevait avant — testé bit à bit. Mesuré sur trois agents de nature
  différente (navigation, mémoire, sécurité) : le contexte partagé du kit
  (~193 tokens) disparaît de leur activation au profit du seul contexte
  qu'ils déclarent. Suite de #378, qui avait câblé la même déclaration côté
  dispatch de tâche.
- **Enregistrer quel agent a été choisi (#366).** Le choix de la persona
  d'entrée laisse désormais un fait dans le journal de traces existant :
  quel agent, quand, dans quel projet — rien du contenu échangé, aucune
  télémétrie sortante. Écriture best-effort : un journal indisponible ne
  fait jamais échouer l'activation qu'il se contente d'observer.
- **Premier port Rust, optionnel — `grimoire.policies` (#354).** Le moteur
  de règles a désormais un second cœur, en Rust, exposé via PyO3 depuis
  `rust/grimoire-policies-core/` ; `PolicyEngine.evaluate` l'utilise quand il
  est disponible et retombe silencieusement sur l'implémentation Python
  pure sinon (`GRIMOIRE_POLICIES_BACKEND=python|rust|auto` force le choix
  des deux côtés pour les tests). Rien n'est publié : la wheel PyPI reste la
  wheel universelle actuelle, le crate porte le classifieur `Private :: Do
  Not Upload`, et construire l'extension localement (`maturin develop`,
  voir CONTRIBUTING.md) n'est jamais nécessaire pour contribuer au kit. Un
  job CI dédié (`rust-policies-core.yml`) construit et teste le crate sans
  toucher à la publication. `tests/unit/test_policies.py` tourne sans
  modification dans les deux configurations ;
  `tests/unit/test_policies_rust_parity.py` prouve que les deux
  implémentations rendent le même verdict sur les mêmes entrées.

### Corrigé

- **Identité d'agent unifiée entre `installed_agents` et `collect_agents`
  (#383).** Les deux fonctions nommaient différemment le même fichier
  gabarit non rendu (`name: "{{agent_tag}}"`) : invisible pour l'une, nommé
  d'après le fichier pour l'autre. `layout.agent_identity()` devient la
  lecture unique, sans jamais retomber sur le nom de fichier quand un
  `name:` est déclaré mais inutilisable.
- **`grimoire_add_agent` crée un vrai fichier dans `overrides/agents`
  (#367).** L'outil se contentait d'ajouter un nom à une liste
  (`agents.custom_agents`) jamais lue pour du chargement ou du routage ; il
  rend désormais le gabarit `custom-agent` vers la couche que le diagnostic
  et la carte de routage résolvent réellement, en refusant un doublon ou un
  nom de fichier non sûr.
- **Couche overrides rendue visible à `doctor` et à la carte de routage
  (#349).** Un agent déposé dans `overrides/agents/` — le moyen sanctionné
  de personnaliser un projet — était invisible au diagnostic et non
  routable par la persona d'entrée ; les deux lecteurs passent désormais
  par `layout.agent_dirs()` avec la même priorité de tiers que le reste du
  kit.
- **`grimoire up` persiste les `needs` et refuse de rétrograder le profil du
  standard (#348).** Sans `--needs`, `up` réécrivait le manifeste du
  standard avec le profil `starter` par défaut, désenregistrant
  silencieusement un profil supérieur (governed, orchestrated…) installé
  via `--needs`. Les `needs` choisis à l'installation sont désormais relus
  depuis `install-manifest.yaml`, et une garde anti-rétrogradation préserve
  le profil en place quand il couvre plus que celui résolu.
- **Les écritures du cockpit répondaient 404 dès qu'un projet était
  sélectionné (#358).** L'interface ajoute `?project=<slug>` à toute
  requête, POST comprises, dès qu'un projet est sélectionné ; `do_POST`
  comparait le chemin, query string incluse, à des routes exactes — la
  totalité des écritures du cockpit (sélection de projet, alignement de
  kit, ajout et scan de dossier, actions mémoire) était morte.
- **Registre de test du cockpit isolé, chemins disparus ignorés au lieu
  d'être purgés d'office (#343).** Un registre pollué de 276 chemins
  `/tmp/pytest-of-*` disparus faisait passer `cockpit refresh` de 31s à
  1,15s une fois filtré. Une empreinte du vrai registre en début et fin de
  suite fait échouer tout test qui écrit hors de l'isolation `HOME` ;
  `gen-site-data.py` ignore les chemins morts au lieu de les traiter, et le
  cockpit nomme leur nombre plutôt que de les retirer sans le demander —
  `cockpit prune` reste le geste de l'utilisateur.
- **Étape d'enregistrement d'un projet rendue visible (#342).** Le cockpit
  ne scanne jamais le disque, il lit le registre — un projet Grimoire valide
  et jamais enregistré disparaissait donc en silence. `cockpit list` et
  `cockpit serve` signalent désormais le dossier courant quand il porte un
  marqueur Grimoire absent du registre, et nomment la commande d'ajout
  attendue.
- **Bouton d'action principale retiré de l'en-tête du cockpit (#362).**
  Aucun écouteur d'événement n'existait pour ce bouton, qui changeait de
  libellé selon l'espace actif sans jamais rien déclencher au clic. Un test
  de non-régression échoue si un futur bouton littéral de l'en-tête est
  ajouté sans écouteur ni câblage glossaire.
- **Le budget de temps du cockpit tient compte des runners Windows (#390),**
  trois fois plus lents sur cent sous-processus git, sans cesser d'attraper
  la régression de #340.

## [3.41.0] - 2026-09-08
### Ajouté

- **Classe de vérifiabilité et dispatch en cascade — épic routage par
  vérifiabilité (#307).** Le contrat d'une tâche dérive une classe V0/V1/V2
  (#316) ; `grimoire task dispatch` cascade dessus (#325) ; le palier de
  départ du dispatch s'ajuste à l'historique des verdicts (#335) ; une classe
  de relisibilité et les incertitudes déclarées viennent affiner la décision
  (#334) ; la politique de dispatch résultante est émise aux hôtes, avec
  l'état des fournisseurs au `SessionStart` (#332).
- **Fournisseurs et coût.** Registre de fournisseurs étendu par palier de
  coût, état runtime et `grimoire providers status` (#317) ; `grimoire
  providers audit` sonde les fournisseurs sans dépenser (#331) ; le choix de
  modèle croise reasoning et coût, Copilot déclare l'écart (#314).
- **Moteur de flow (#204).** `grimoire flow run|status|resume|abort`
  conduit un node à la fois (#326) ; l'exécuteur par dispatch en cascade du
  routage par vérifiabilité est branché dans `flow run` (#336).
- **Garde-fous runtime et hôtes.** Plafonds de tours et de budget par
  instance, garde des échanges pair-à-pair (#320) ; claims à fichiers
  exclusifs, sous-agents bornés, événements `SubagentStart` et
  `PostToolUseFailure` (#321).
- **Sécurité des écritures mémoire et médiation d'outils (#324).** Écritures
  mémoire validées, contrat MCP annoté, contenu externe marqué, médiation
  d'outils vérifiée.
- **Référence agentique industrielle (#315).** Chargée à la demande par les
  agents, plutôt que systématiquement.

### Corrigé

- **Export OTel GenAI et évals avec pass^k (#322).** L'export de traces est
  conforme au schéma GenAI d'OpenTelemetry ; les évals calculent pass^k.
- **Accord pluriel et catalogue de digests après #319 (#333).** Correction
  d'accord introduite par le retrait d'AORA/DCF, et catalogue de digests
  rafraîchi en conséquence.
- **Le garde de CHANGELOG accepte un repli par numéro de PR.** Seize PR de
  cette version ont fusionné sans toucher `CHANGELOG.md` à leur propre
  commit ; `scripts/check-changelog-release.py` accepte désormais un commit
  qui ne le touche pas si son sujet cite `(#NNN)` et que ce numéro apparaît
  dans la section de la version publiée — jamais dans `[Unreleased]` ni une
  version antérieure. La règle par commit reste la référence.

### Retiré

- **Le socle contredisait la doctrine qu'il citait lui-même.** AORA et DCF étaient
  présentés comme actifs dans `framework/agent-base.md`,
  `framework/agent-base-compact.md` et `framework/orchestrator-gateway.md`, alors que
  le retrait des deux (2026-07-12, zéro usage constaté) n'était consigné que dans ce
  changelog et dans la doctrine des projets consommateurs. Les trois fichiers
  distribuaient donc un protocole que le produit ne tenait plus. Les deux sections sont
  remplacées par un bandeau de retrait ; les deux règles opposables qu'AORA portait
  (circuit breaker, cascading initiative) survivent sans son nom sous « Plan
  d'exécution ». PIP passe en statut observer-only, explicite dans les trois fichiers :
  décrit, non instrumenté, aucune obligation. La calibration exécuter/proposer/escalader
  que portait DCF reste assurée par ALS seul.
- **Deux fiches obsolètes qui documentaient un produit imaginaire.**
  `framework/mcp/grimoire-mcp-server.md` décrivait neuf outils MCP fictifs et une
  roadmap « MCP v2 Sampling » alors que Sampling est déprécié et que le serveur réel
  (`src/grimoire/mcp/server.py`) expose vingt-deux outils sans sampling.
  `framework/workflows/state-checkpoint.md` (BM-06) documentait en prose un mécanisme
  de checkpoint doublon du kernel d'exécution réel (`src/grimoire/runtime/kernel.py`,
  machine à états, checkpoints JSONL, reprise). Les deux fiches sont conservées —
  chacune référencée ailleurs dans le dépôt — mais leur contenu est remplacé par un
  bandeau d'obsolescence de moins de dix lignes pointant vers l'implémentation réelle.

## [3.40.0] - 2026-09-07
### Ajouté

- **Source : IntelliSense déterministe — colorisation, correcteur, complétion
  (issue 280).** L'éditeur de l'espace Source colorie le frontmatter, les
  blocs `<agent>`/`<activation>`/`<step>`, les placeholders et les
  identifiants d'agents, de patterns et de workflows, par recouvrement sur la
  `<textarea>` — aucune dépendance ni bundler (ADR-006 D2). Un correcteur
  signale, recalculé à l'enregistrement et au repos de saisie : un chemin du
  kit qui ne se résout pas (même logique que `grimoire doctor`,
  `grimoire.core.integrity`), un agent routé mais absent du manifeste, une
  clé de frontmatter inconnue de son schéma, un terme du glossaire cité sans
  entrée, un pattern ou un workflow inconnu du catalogue — repris dans
  l'onglet Problèmes du dock. La complétion (`Ctrl+Espace`, ou déclenchée par
  `{`, `@`, `/`) propose agents, workflows, patterns, chemins du kit et clés
  de schéma ; le survol d'un identifiant lié au glossaire ouvre la même bulle
  épinglable que le reste de la vue de travail. Nouvelle route en lecture
  seule, servie par les deux hôtes : `GET /api/workspace/language`
  (`src/grimoire/tools/workspace_language.py`). La piste d'un petit modèle
  local (Ollama) pour des suggestions de contenu reste hors périmètre — elle
  brancherait derrière cette IntelliSense déterministe, jamais à sa place.
- **Les codes de patterns convergent vers un seul catalogue, et les cinq DOIT
  restants de #246 sont couverts (#246).** `tools/blueprint_*.py`,
  `tools/handoff.py`, `tools/cost_model.py`, huit extensions et le bridge du
  standard (`framework/agentic-standard/`) citaient chacun des codes
  `ORG-`/`ORC-`/`COG-`/`KNO-`/`MOD-`/`GOV-`/`QUA-`/`RUN-` sans qu'aucun ne
  référence l'autre. Les 36 patterns du bridge
  (`templates/pattern-catalog.yaml`) portent désormais un `catalog_ref` vers
  le ou les codes du catalogue de 78 patterns
  (`web/data/catalogue-export.json`, même révision upstream que
  `profile-map.yaml`) qu'ils implémentent ; `evidence/schemas.py` (QUA-04) et
  `missions/ledger.py` (QUA-03), qui implémentaient chacun un pattern du
  catalogue sans le citer, le citent maintenant. Nouveau module partagé
  `grimoire.core.standard_checks.pattern_codes` ; `standard verify` refuse un
  `catalog_ref` qui ne nomme pas un code réel
  (`patterns.catalog_ref_unknown`) ; `tests/unit/
  test_pattern_code_convergence.py` (27 cas) refuse un code cité n'importe où
  dans le dépôt sans entrée au catalogue.

  Les cinq exigences DOIT que la matrice ne montrait pas comme trous
  (AG-MIS-005, AG-ORC-005, AG-LLM-004, AG-OBS-003, AG-OBS-005) sont
  désormais couvertes par un artefact réel, chacune au niveau où la norme
  les attend : `grimoire task record-model-call` journalise un appel modèle
  (modèle, rôle, coût, latence, erreur) dans le TraceLedger — les champs
  `model`/`token_usage` existaient dans le schéma sans qu'aucun code ne les
  peuple ; `grimoire task handoff` donne enfin un appelant réel à
  `tools.handoff.build_handoff` (#275 l'avait laissé orphelin) et trace la
  communication inter-agents en événement append-only au Mission Ledger ; le
  gabarit `task-envelope.md` gagne une section « Ambiguïtés résolues » ; le
  gabarit `retention-registry.yaml` gagne une entrée RET-004 (type incident,
  rétention durable) ; le gabarit `observability-policy.yaml` gagne une
  entrée `trace_ledger` déclarant les dimensions réellement capturées
  (modèle, outil, hook, verdict, preuve) plutôt que le seul
  `runtime-journal.jsonl`, qu'aucun code n'écrit. `standard traceability
  --profile governed` : zéro trou non justifié ; `standard verify` sur les
  cinq profils, projet jetable : mêmes quatre erreurs préexistantes
  qu'avant sur `governed`/`production` (#269), aucune nouvelle.

- **Le claim rappelle ce que la mémoire sait de la tâche — L6 Task memory
  (#141).** `grimoire task recall <id>`, l'outil MCP `task_recall` et le hook
  `SessionStart` (entre la persona d'entrée et la directive du standard,
  seulement pour une tâche réclamée) rendent le même rappel : l'historique
  propre de la tâche (tentatives précédentes bloquées ou échouées), ses
  voisines — liées par `task link`, de la même mission, ou au titre proche —
  avec la cause de leur arrêt même si elles ont depuis été rouvertes, et ce
  que la mémoire du projet a consolidé sur des sujets voisins quand un backend
  mémoire est configuré. Borné en tokens. `TaskService` consolide dans cette
  mémoire ce qu'une clôture (`decisions`) ou un blocage (`failures`) enseigne
  — jamais un mouvement ordinaire, conformément aux garde-fous de
  `planning/memory-os-roadmap.md` (étape 6, Kanban Task Memory). Nouveau
  module `grimoire.missions.recall` ; `grimoire.missions.service.TaskService`
  gagne `.recall()` et une résolution paresseuse de la mémoire du projet.
  L'espace Exécuter (`spaces/executer.js`) affiche le rappel dans
  l'inspecteur, avec parcimonie — pas de section pour un rappel vide.

## [3.39.1] - 2026-09-07

### Corrigé

- **Piloter distingue kit aligné et kit installé (#288).** Le badge Kit
  affichait « à jour » à côté d'un sous-texte `aligné sur 3.36.0, installé
  3.38.0` — deux versions différentes contredisant un badge d'alignement
  exact. `kit.upToDate` (aucun fichier livré n'a de révision plus récente au
  catalogue) et `kit.aligned === kit.installed` (synchronisation exacte) sont
  deux affirmations distinctes ; le badge dit maintenant « aligné » quand la
  première est vraie sans la seconde, « à jour » seulement quand les deux
  versions coïncident. Aucune donnée serveur ne change
  (`project_health.kit_alignment` reste correct) : la correction est dans le
  rendu, `web/workspace/spaces/piloter.js`.
- **Le glossaire se charge sur une installation nue de la wheel.** Le serveur
  de la vue de travail importait PyYAML alors que la dépendance déclarée est
  ruamel : sur `pip install grimoire-kit` sans extra, `/api/workspace/glossary`
  tombait et les six espaces s'ouvraient avec une erreur (#292, trouvé par le
  passage consommateur sur un projet réel). Le glossaire et le registre des
  plans passent par le chargeur du kit ; un test bloque le module `yaml` et un
  autre refuse tout `import yaml` direct dans ces modules.

### Retiré

- **Onze accesseurs publics sans appelant ni test (#275).** `MemoryManager.
  hot_store/hot_recall/hot_delete/hot_acquire_lease/hot_release_lease`
  (Redis hot memory — R&D non portée, #94) ; `CodeGraph.get_node` et
  `get_dependents` ; `MemPalaceBackend.search_preview` (« convenience method
  used during experiments », jamais adoptée) ; `RecipeRegistry.get_or_raise`
  et `list_recipes` (la classe entière n'a aucun appelant) ;
  `ProjectSurface.hooks_for` (même mode de panne qu'`entry_agent()`, #233).
  Trois autres (`MissionLedger.blocked_tasks`/`events_for`,
  `missions.projections.build_cockpit_from_paths`) restent orphelins par
  choix — zone `missions/` active en parallèle au moment de cette passe,
  documentés dans `KNOWN_ORPHANS` (`tests/unit/test_public_accessor_
  inventory.py`) plutôt que tranchés en silence.

### Ajouté

- **Trois accesseurs orphelins branchés sur un appelant réel (#275).**
  `grimoire task pack <task-id> <pack-id>` lit un pack de preuve complet
  (`EvidenceService.get_pack`), jusqu'ici accessible seulement en liste.
  `grimoire task trace-export <dest> --format otel|langfuse` exporte le
  `TraceLedger` (`export_otel_jsonl` et `export_langfuse` n'avaient tous les
  deux aucun appelant produit). `grimoire memory graph coverage` liste les
  nœuds publics sans arête `TESTED_BY` (`CodeGraph.uncovered_nodes`), sans
  dépendance Neo4j.
- **Garde-fou anti-régression (#275)** :
  `tests/unit/test_public_accessor_inventory.py` réplique l'inventaire AST
  qui a produit #275 et échoue si un nouvel accesseur public de
  `src/grimoire/` se retrouve sans appelant ni test hors de sa propre
  définition.

## [3.39.0] - 2026-09-06
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
