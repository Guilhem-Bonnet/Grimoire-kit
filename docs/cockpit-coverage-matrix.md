<!-- markdownlint-disable MD013 -->
# Matrice de couverture — cockpit (contrôles × états, routes × codes)

Inventaire exhaustif construit depuis le code (`web/workspace/`, `src/grimoire/tools/*_routes.py`,
`src/grimoire/tools/project_update.py`, `src/grimoire/cli/cmd_cockpit.py`, `src/grimoire/proposals.py`)
et confronté aux tests réels du dépôt (`tests/e2e/*.py`, `tests/unit/test_workspace_*.py`,
`tests/unit/cli/test_cmd_cockpit*.py`, `tests/unit/test_project_upgrade.py`). Le résumé chiffré
ci-dessous est régénéré par `python scripts/cockpit-coverage.py` — jamais compté à la main :
lancer le script après toute modification de ce fichier, coller sa sortie dans le bloc « Résumé ».

## Méthodologie et un correctif en cours de route

Première version de cet inventaire construite par erreur sur un clone partagé resté à la version
3.49.0 du kit (`/mnt/Travail/Projets/Dev/Grimoire-Forge/grimoire-kit`) au lieu d'un worktree sur
`origin/main` (3.52.1). Corrigé avant publication : `git diff` entre les deux arbres a montré que
5 fichiers backend (`cmd_cockpit.py`, `workspace_routes.py`, `project_update.py`, `proposals.py`,
`project_registry.py`) et une partie du frontend (`piloter.js`, `observer.js`, `shell.js`) avaient
changé depuis (issues #490, #506, #510, #511, #513, #524) — les lignes concernées de ce document
ont été relues directement sur le worktree à jour, pas recopiées du premier passage. Deux
affirmations d'un premier inventaire automatique se sont révélées fausses une fois vérifiées sur le
code actuel (`#project-chip` a un handler, `Observer.setViews` passe bien `onPick`, la troncature de
preview à 2000 caractères et la navigation par `location.search` sur « Ouvrir ce projet » n'existent
plus — corrigées par les revues du 2026-09-14/15) ; deux autres se sont confirmées et ont chacune reçu
une issue (voir plus bas). La liste des fichiers de test elle-même a dû être redressée pour la même
raison : le clone périmé ne connaissait pas `tests/e2e/test_workspace_controls.py`,
`tests/e2e/test_workspace_cockpit_upgrade_flow.py`, `tests/e2e/test_workspace_piloter_review_button.py`,
`tests/e2e/test_workspace_piloter_upgrade_checkpoint_badge.py`, `tests/e2e/test_workspace_piloter_fleet_cache.py`,
`tests/unit/cli/test_cmd_cockpit_proposals.py` ni `tests/unit/cli/test_cmd_upgrade_flow*.py` — tous
présents sur `origin/main` et tous lus pour cette matrice.

Convention de lecture : une ligne = un couple (contrôle, état) ou (route, code/état de données).
« Couvert = oui » exige un test qui échoue si le comportement décrit disparaît — pas seulement un
test qui rend la page. Glisser-déposer et menu contextuel : recherche `draggable`/`dragstart`/
`contextmenu` sur tout `web/workspace/` → aucune occurrence, ces deux catégories de contrôle
n'existent pas dans le cockpit (case vide légitime, pas un oubli).

## Résumé

<!-- BEGIN:cockpit-coverage-summary -->
| Section | Lignes | Couvertes | % |
|---|---:|---:|---:|
| Coque (shell.js) | 32 | 31 | 97% |
| Piloter | 34 | 29 | 85% |
| Concevoir | 21 | 12 | 57% |
| Executer | 16 | 10 | 62% |
| Observer | 10 | 7 | 70% |
| Mémoire | 9 | 4 | 44% |
| Source | 17 | 15 | 88% |
| Routes API | 24 | 16 | 67% |
| **TOTAL** | **163** | **124** | **76%** |
<!-- END:cockpit-coverage-summary -->

*(régénéré par `python scripts/cockpit-coverage.py` — recopier sa sortie ici après toute modification des tables ci-dessous ; la CI de PR 1 le vérifie via `--check`.)*

## Bugs trouvés en construisant cette matrice

- **#534** — le bouton de rail « Preuves » (touche `3`, `triggerRailAction('evidence')`,
  `shell.js:454`) n'est enregistré par aucun espace (`ctx.rail.on('evidence', …)` : zéro occurrence
  sous `web/workspace/spaces/*.js`, seul `library` l'est, dans `concevoir.js:295`). Clic ou touche
  `3` : aucun effet, aucune erreur. **Corrigé par PR #539** : `evidence` rejoint `PANELS` (tenu par
  la coque, comme Explorateur/Inspecteur, jamais par un espace), nouveau panneau alimenté par
  `GET /api/workspace/evidence`.
- **#535** — dans Concevoir, `addNode()` (`concevoir.js:625`) n'écrit que `state.blueprint` en
  mémoire puis journalise « nœud ajouté » dans le dock : aucun appel d'écriture
  (`blueprintPut` n'existe même pas côté client, commentaire `concevoir.js:284`). Le nœud disparaît
  au rechargement malgré le message de succès affiché.

## Constat (lot B, pas un bug) : la palette ne se reconstruit qu'au chargement

`buildPalette()` (`shell.js:886`) n'est appelée qu'une fois, dans `main()` — jamais depuis
`openPalette()`. Une tâche ou un blueprint créé après le chargement de la page (le cas courant sur un
poste réel : on ouvre le cockpit, puis on travaille) n'apparaît dans les sections « Tâches »/
« Workflows » de la palette qu'après un rechargement complet. Découvert en écrivant les tests
d'exécution de ces deux sections (`tests/e2e/test_workspace_shell_palette_actions.py`), qui doivent
recharger explicitement après avoir écrit la donnée pour la voir. Comportement cohérent avec le reste
de la palette (« Projets », « Fichiers » ont la même limite), donc pas traité comme une régression —
mais à garder en tête si Guilhem constate un jour « la palette ne voit pas ma tâche toute fraîche ».

### Espace : Coque (shell.js)

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Onglet d'espace | `#spaces .tab` | `goto(space.id)` | aucune | sélection change de canvas | `test_chaque_espace_s_ouvre_sur_un_projet_reel` | oui |
| Onglet d'espace | `#spaces .tab` | idem | aucune | exactement 6, pas 1 de plus | `test_la_coque_annonce_les_six_espaces_et_pas_un_de_plus` | oui |
| Raccourci `⌘1`–`⌘6` | `document keydown` | `goto(SPACES[n-1].id)` | aucune | sélectionne l'espace n | `test_le_raccourci_meta_chiffre_change_d_espace` (paramétré sur les 6) | oui |
| Bouton de rail (clic) | `.rail-btn[data-panel]` | ouvre en surimpression (`peek`) | aucune | peek, sans redimensionner la grille | `test_le_clic_sur_le_rail_ouvre_en_surimpression` | oui |
| Survol de rail (450 ms) | `.rail-btn` `pointerenter` | ouvre en `peek` après délai | aucune | rien avant 450 ms, peek après | `test_le_survol_du_rail_450ms_entrouvre` | oui |
| Survol du contenu | `#canvas` `pointerenter`(indirect) | n'ouvre jamais de panneau | aucune | jamais d'ouverture | `test_le_survol_du_contenu_n_ouvre_jamais_un_panneau` | oui |
| Cadenas de panneau | `[data-pin]` | épingle dans la grille | aucune | pinned, redimensionne le centre | `test_le_cadenas_epingle_dans_la_grille` | oui |
| `⌘/Ctrl`+clic sur rail | `.rail-btn` | épingle direct sans passer par peek | aucune | pinned | `test_le_cmd_clic_sur_le_rail_epingle_sans_passer_par_l_entrouvert` | oui |
| Poignée de redimension | `.panel-resize` | drag largeur panneau | aucune | largeur augmente | `test_la_poignee_redimensionne_le_panneau_epingle` | oui |
| Raccourci panneau `1`/`4` | `document keydown` | `togglePanel(id)` | aucune | bascule explorer/inspector | `test_les_raccourcis_de_panneau_basculent` | oui |
| Raccourci panneau `2` (bibliothèque) | `document keydown` | `triggerRailAction('library')` | aucune | délégué à Concevoir seul | `test_le_raccourci_2_du_rail_ouvre_la_bibliotheque_de_noeuds` | oui |
| Raccourci panneau `3` (preuves) | `document keydown` | `togglePanel('evidence')` (`evidence` a rejoint `PANELS`, corrigé par #534/PR #539) | `GET /api/workspace/evidence` | ouvre le panneau Preuves, tenu par la coque comme Explorateur/Inspecteur | `test_le_raccourci_3_ouvre_les_preuves_depuis_l_espace_par_defaut` | oui |
| Raccourci panneau `5` (dock) | `document keydown` | bascule `#dock` pinned/collapsed | aucune | bascule | — | non |
| Raccourci backtick `` ` `` | `document keydown` | `selectDockTab('console')` + pin | aucune | ouvre le dock sur Console | `test_le_raccourci_accent_grave_ouvre_la_console_du_dock` | oui |
| Mode concentration `⇧⌘F` | `document keydown` | `toggleFocus()` | aucune | replie tout, toile plein écran | `test_le_mode_concentration_replie_tout` | oui |
| Palette : ouverture `⌘K` | `document keydown` | `openPalette()` | `GET /api/workspace/commands` `+projects+blueprints+tasks+files` | ouverte, sections visibles | `test_la_palette_s_ouvre_au_clavier_et_montre_les_commandes` | oui |
| Palette : navigation clavier | `#palette-list li` | `ArrowUp`/`ArrowDown`/`Escape` | aucune | sélection se déplace, ferme sur Échap | `test_la_palette_se_navigue_au_clavier` | oui |
| Palette : exécution `Enter` | `#palette-input` | `item.run()` | dépend de la section | exécute l'entrée sélectionnée | `test_la_palette_execute_l_entree_selectionnee_a_l_entree` | oui |
| Palette section Espaces | `#palette-list li` | `goto(space)` | aucune | change d'espace | `test_la_palette_execute_l_entree_selectionnee_a_l_entree` | oui |
| Palette section Commandes | `#palette-list li` | `runCommand(key)` | `POST /api/workspace/command` | libellé `grimoire …` | `test_la_palette_s_ouvre_au_clavier_et_montre_les_commandes` | oui |
| Palette section Projets (liste) | `#palette-list li` | — | `GET /api/projects` | en tête quand ouverte depuis le chip | `test_le_chip_projet_ouvre_la_palette_sur_la_section_projets` | oui |
| Palette section Projets (sélection) | `#palette-list li` | `location.search = '?project=' + slug` | rechargement complet | change de projet servi | `test_choisir_un_projet_dans_la_palette_recharge_sur_ce_projet` | oui |
| Palette section Tâches (sélection) | `#palette-list li` | `runCommand(['task','show',id])` | `POST /api/workspace/command` | exécute `task show` | `test_choisir_une_tache_dans_la_palette_execute_task_show` | oui |
| Palette section Workflows (sélection) | `#palette-list li` | `goto('concevoir')` | aucune | va sur Concevoir | `test_choisir_un_workflow_dans_la_palette_va_sur_concevoir` | oui |
| Palette section Fichiers | `#palette-list li` | `goto('source', {file})` | `GET /api/workspace/files` | ouvre le fichier dans Source | `test_la_palette_atteint_les_fichiers_de_source`, `test_choisir_un_fichier_dans_la_palette_l_ouvre_dans_source` | oui |
| Chip projet | `#project-chip` | `openPalette('Projets')` | aucune | ouvre palette, section Projets en tête | `test_le_chip_projet_ouvre_la_palette_sur_la_section_projets` | oui |
| Chip projet — indice visuel | `#project-chip` | curseur/chevron/survol | aucune | curseur pointer, chevron, fond change au survol | `test_le_chip_projet_a_un_indice_visuel_de_clic` | oui |
| Bouton thème | `#st-theme` | `toggleTheme()` | aucune (client, `localStorage`) | bascule + survit au rechargement | `test_le_theme_et_la_densite_se_choisissent_et_survivent_au_rechargement` | oui |
| Bouton densité | `#st-density` | `toggleDensity()` | aucune | bascule + survit au rechargement | `test_la_densite_se_choisit_et_survit_au_rechargement` | oui |
| État panneaux mémorisé par espace | (interne) | `localStorage` par espace/projet | aucune | 2 espaces gardent des états distincts après reload | `test_l_etat_des_panneaux_est_memorise_par_espace_et_survit_au_rechargement` | oui |
| Infobulle glossaire (survol/Alt/pile) | `.tip` | `glossary.open(...)` | `GET /api/workspace/glossary` | ouvre, empile (3 niveaux max), ferme sur Échap | `test_une_bulle_s_ouvre_se_fige_et_la_pile_se_ferme` | oui |
| Absence d'erreur console au boot | — | — | — | 0 erreur sur les 6 espaces | `test_aucune_erreur_de_console_a_l_amorcage` | oui |

### Espace : Piloter

Note (lot A) : « garde d'écriture 403 » pour le bouton « Mettre à jour » n'apparaît pas ci-dessous —
vérifié sur le code, `POST /api/projects/update` n'est délibérément **pas** gardée par
`_is_home_request()` (portée flotte assumée, cf. section Routes API) : il n'y a pas de 403 à tester
pour ce contrôle précis.

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Zoom Flotte/Projet | `#zoom-seg button` | `setZoom` | — | bascule visuelle (aria-pressed) | `test_piloter_zoom_seg_bascule_correctement` | oui |
| Rafraîchir la flotte | `.btn` « Rafraîchir » | `refreshBtn.click` → `forceFleet` | `GET /api/projects` (+ health par projet) | invalide le cache 60 s | `test_revenir_sur_la_flotte_sert_le_cache_dans_la_fenetre_de_60s`, `test_le_changement_de_projet_n_interroge_que_le_projet_cible` | oui |
| Ligne projet (Flotte) | `tr`/`.card` | `onSelect(slug)` | — | ouvre le niveau Projet | `test_piloter_cockpit_flotte_montre_un_tableau_avec_inconnue` | oui |
| Bouton « Ouvrir ce projet » | `.btn` texte « Ouvrir ce projet » | `ctx.goto('piloter', {openProject})` | — | affiché seulement si cockpit + slug ≠ projet servi | — | non |
| Mettre à jour — aperçu | `.btn` texte « Mettre à jour — aperçu » | `updateProject(slug, false)` | `POST /api/projects/update` `{confirm:false}` | en cours → succès (preview.md rendu) | `test_mettre_a_jour_depuis_piloter_ne_repond_pas_404_sur_un_projet_selectionne` | oui |
| Mettre à jour — aperçu | idem | idem | idem | échec (erreur affichée, bouton réactivé) | `test_apercu_en_echec_affiche_l_erreur_et_reactive_le_bouton` | oui |
| Mettre à jour — aperçu | idem | idem | idem | projet hors registre → 404 | `test_the_cockpit_refuses_an_unknown_project` (route directe, `tests/unit/test_project_update.py`) | oui |
| Confirmer la mise à jour | `.btn.pri` « Confirmer » | `updateProject(slug, true)` | `POST /api/projects/update` `{confirm:true}` | succès, `state=completed`/`upgraded-checkpoint-pending`, badge checkpoint posé | `test_confirmer_depuis_piloter_fait_apparaitre_une_proposition`, `test_the_checkpoint_badge_survives_a_fleet_then_project_navigation` | oui |
| Confirmer la mise à jour | idem | idem | idem | échec `upgraded-but-failed` : `report.md`+`proposals` renvoyés par la route mais jamais lus par le handler `if (result.ok)` → pas de rafraîchissement des Propositions à l'écran | `test_confirmer_upgraded_but_failed_devrait_rafraichir_les_propositions` (**xfail strict — bug #538**) | non |
| Revoir dans l'IDE | `.btn` texte « Revoir dans l'IDE » | copie presse-papiers du prompt `/grimoire-upgrade-review` | aucune écriture | absent sans travail en attente | `test_the_review_button_is_absent_with_nothing_pending` | oui |
| Revoir dans l'IDE | idem | idem | aucune | présent avec proposition en attente | `test_the_review_button_appears_with_a_pending_proposal` | oui |
| Revoir dans l'IDE | idem | idem | aucune | présent avec checkpoint en attente, nomme le run | `test_the_review_button_appears_with_a_pending_checkpoint_and_names_the_run` | oui |
| Revoir dans l'IDE | idem | idem | aucune | presse-papiers indisponible → texte affiché à copier à la main | `test_the_review_button_falls_back_to_manual_copy_when_the_clipboard_is_unavailable` | oui |
| Accepter une proposition | `.btn` « Accepter » | `acceptBtn.click` | `POST /api/workspace/proposals/<slug>/accept` | type `agent` (déclencheur) | `test_lister_accepter_et_refuser_une_proposition` | oui |
| Accepter une proposition | idem | idem | idem | type `skill` | `test_accepter_une_proposition_type_skill_cree_le_fichier_et_l_attache` | oui |
| Accepter une proposition | idem | idem | idem | type `repair`, avec substitution évidente | `test_accepter_une_proposition_repair_avec_substitution_corrige_le_fichier` | oui |
| Accepter une proposition | idem | idem | idem | type `repair`, sans substitution évidente : bouton absent (jamais un refus muet), Refuser reste utilisable | `test_une_proposition_repair_sans_substitution_n_offre_pas_accepter` | oui |
| Accepter une proposition | idem | idem | idem | type `override-migration` (refus « revue nécessaire ») | `test_accepter_une_proposition_override_migration_refusee_journalise_le_motif` | oui |
| Accepter une proposition | idem | idem | idem | type `memory-link` (refus sans porteur) | `test_accepter_une_proposition_memory_link_refusee_sans_porteur` | oui |
| Accepter une proposition | idem | idem | idem | type `needs-hosts` | `test_accepter_une_proposition_type_needs_hosts_declare_les_hotes_actives` | oui |
| Accepter une proposition | idem | idem | idem | sur un projet non-home de la Flotte (dérogation #490) | `test_accept_works_on_a_non_home_project_even_when_another_is_home` (HTTP direct, pas via clic UI) | non |
| Refuser une proposition | `.btn` « Refuser » | `rejectBtn.click` | `POST /api/workspace/proposals/<slug>/reject` | disparaît de la liste « en attente » | `test_lister_accepter_et_refuser_une_proposition` | oui |
| Ouvrir la référence (proposition `repair`) | `.btn` (bloc repair) | `ctx.goto('source', {file, line})` | — | ouvre le fichier citant à la ligne | — | non |
| Ligne d'agent (table) | `tr` | `onSelect(agent.name)` | `GET /api/workspace/agents` | ouvre l'inspecteur agent | `test_la_vue_expose_la_clause_d_emploi_et_la_couche` | oui |
| Sauver la clause d'emploi | `.btn` « Sauver » (clause) | `saveClauseBtn.click` | `POST /api/workspace/agents/<name>/fields` | succès + refus champ inconnu | `test_modifier_la_clause_d_emploi_et_les_outils`, `test_un_champ_non_modifiable_est_refuse` | oui |
| Sauver les outils | `.btn` « Sauver » (outils) | `saveToolsBtn.click` | idem | succès + outil inconnu refusé | `test_un_outil_inconnu_est_refuse_sans_toucher_le_disque` | oui |
| Sauver le contexte | `.btn` « Sauver » (contexte) | `saveContextBtn.click` | idem | succès + chemin inexistant refusé | `test_un_contexte_existant_est_accepte_et_efface_ensuite`, `test_un_contexte_inexistant_sur_disque_est_refuse_avec_le_message_de_collect` | oui |
| Assigner un skill | `.btn` « Assigner » | `assignBtn.click` | `POST /api/workspace/agents/<name>/skill` `{action:"assign"}` | crée l'override | `test_assigner_un_skill_cree_l_override_vu_par_le_diagnostic` | oui |
| Retirer un skill | `.chip` bouton retrait | `remove.click` | idem `{action:"remove"}` | override disparaît | `test_retirer_le_skill_fait_disparaitre_le_chip_et_la_declaration` | oui |
| Créer un projet | `.btn` « Créer » (Flotte) | `createBtn.click` | `POST /api/projects/create` | succès, apparaît dans la liste | `test_creating_a_project_from_the_fleet_makes_it_appear` | oui |
| Créer un projet | idem | idem | idem | chemin déjà existant → 409 refusé | `test_creating_a_project_on_an_existing_path_is_refused` | oui |
| Créer un projet | idem | idem | idem | archétype inconnu / chemin hors racines → 400/403 | `test_refuses_an_unknown_archetype_before_writing`, `test_refuses_a_path_outside_allowed_roots` (route directe, `tests/unit/cli/test_cmd_cockpit_create_project.py` — correction : marqué à tort « non » dans la première version de cette matrice) | oui |
| Wizard — lancer une étape | `.btn` « Lancer » (wizard) | `runBtn.click` | `POST /api/setup` | succès bout en bout | `test_wizard_initializes_a_blank_project_end_to_end` | oui |
| Wizard — repli (fallback) | `.btn` (fallback) | `fallbackBtn.click` | `POST /api/setup` | chemin d'erreur / archétype de repli | — | non |

### Espace : Concevoir

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Vue (Carte/Board/Liste) | `#view-seg button` | `setView` | — | bascule visuelle + contenu | `test_concevoir_view_seg_bascule_correctement`, `test_la_vue_liste_montre_les_six_colonnes_de_la_spec`, `test_la_vue_board_groupe_par_genre` | oui |
| Vue — état vide | `#view-seg button` | idem | `GET /api/workspace/blueprints` (vide) | 3 boutons visibles mais désactivés | `test_concevoir_etat_vide_montre_la_commande_et_desactive_les_vues` | oui |
| Carte de container (clic) | `.card`/`tr` | `selectContainer(id)` | `GET /api/blueprints/<id>` | remplit l'inspecteur | `test_la_selection_d_un_container_remplit_l_inspecteur` | oui |
| Carte de container (double-clic) | idem | `zoomToWorkflow(id)` | idem + validate/simulate | dessine le graphe niveau Workflow | `test_le_double_clic_zoome_sur_le_workflow_et_dessine_le_graphe` | oui |
| Carte de container (clavier `Enter`) | idem | idem | idem | même effet au clavier | — | non |
| Carte projet (Flotte) | `.card` | `location.search = '?project='+slug` | rechargement | change de projet servi (légitime, contexte serveur) | — | non |
| Ouvrir le workflow (bouton) | `.btn` « Ouvrir » | `zoomToWorkflow(id)` | idem | équivalent bouton du double-clic | — | non |
| Valider (bouton) | `.btn` « Valider » | `runValidate` | `POST /api/blueprints/<id>/validate` | verdict écrit dans le dock Problèmes | `test_valider_ecrit_le_verdict_dans_le_dock_problemes` | oui |
| Simuler (bouton) | `.btn` « Simuler » | `runSimulate` | `POST /api/blueprints/<id>/simulate` | résultat simulation | — | non |
| Compiler (bouton) | `.btn` « Compiler » | `runCompile` | `POST /api/blueprints/<id>/compile` (atelier seul) | artefact nommé / refus si cockpit | — | non |
| Bibliothèque (rail 2 / bouton toolbar) | `.rail-btn`/`btnLib` | ouvre/ferme `state.paletteOpen` | `GET /api/primitives` | 7 primitives listées | `test_la_bibliotheque_de_noeuds_liste_les_sept_primitives`, `test_le_raccourci_2_du_rail_ouvre_la_bibliotheque_de_noeuds` | oui |
| Primitive de la Bibliothèque (clic) | `.cv-prim` | `addNode(name)` | **aucune écriture (bug #535)** | nœud ajouté en mémoire, jamais persisté | — | non |
| Nœud du graphe (clic) | `.node`/box | `selectedNodeId = id` | — | sélection + 4 onglets inspecteur | `test_le_niveau_noeud_montre_un_inspecteur_a_quatre_onglets` | oui |
| Nœud du graphe (double-clic) | idem | `zoomToNode(id)` | — | zoom niveau Nœud | — | non |
| Nœud du graphe (clavier `Enter`) | idem | idem | — | équivalent clavier | — | non |
| Onglet nœud (4 onglets) | `.tab` inspecteur nœud | change `_cvTab` | — | contenu change par onglet | `test_les_quatre_onglets_du_noeud_changent_le_contenu_affiche` | oui |
| Aucun texte sous le plancher sombre | — | — | — | contraste/tailles du graphe | `test_aucun_texte_du_graphe_sous_le_plancher_dark` | oui |
| Rail « Preuves » depuis Concevoir | `.rail-btn[data-panel=evidence]` | `togglePanel('evidence')` (corrigé par #534/PR #539, `evidence` tenu par la coque) | `GET /api/workspace/evidence` | ouvre le panneau Preuves comme dans les cinq autres espaces | `test_le_raccourci_3_ouvre_les_preuves_depuis_l_espace_par_defaut` (couvre le comportement générique, pas un test dédié à Concevoir) | oui |
| Zoom Flotte/Projet/Workflow/Nœud | `#zoom-seg button` | `setZoom` | — | 3-4 niveaux selon hôte | — | non |
| Trois vues au niveau Projet | `#view-seg` | — | — | présence des 3 vues | `test_les_trois_vues_sont_proposees_au_niveau_projet` | oui |
| Blueprint réel s'ouvre | — | `mount` | `GET /api/workspace/blueprints` | rend un blueprint réel, jamais démo | `test_concevoir_s_ouvre_sur_le_blueprint_reel_du_projet` | oui |

### Espace : Executer

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Vue (Carte/Liste/Board) | `#view-seg button` | `setView` | `GET /api/workspace/tasks` | bascule, Board par défaut | `test_executer_propose_trois_vues_et_le_board_par_defaut`, `test_executer_view_seg_bascule_correctement` | oui |
| Vue — état vide | `#view-seg button` | idem | idem (0 tâche) | 4 vues visibles mais désactivées | `test_executer_etat_vide_montre_la_commande_et_desactive_les_vues` | oui |
| Carte de tâche (clic/Enter) | `.card` | `onSelect(id)` | `GET /api/workspace/tasks/<id>` | ouvre l'inspecteur | (via `test_executer_l_inspecteur_ne_montre_pas_de_rappel_vide`) | oui |
| Bouton d'action rapide sur carte | `.btn` (carte) | `onSelect` + `stopPropagation` | idem | ne déclenche pas le clic parent | — | non |
| Ligne de tâche (Liste) | `tr` | `onSelect(id)` | idem | ouvre l'inspecteur | — | non |
| Filtre timeline — source | `select` (source) | `timelineFilter.source` | `GET /api/workspace/tasks/<id>/trace` | filtre affiché | `test_executer_timeline_montre_dispatch_et_refus_et_se_filtre_par_source` | oui |
| Filtre timeline — gravité | `select` (gravité) | `timelineFilter.gravite` | idem | filtre affiché | — | non |
| Bouton Timeline | `.btn` « Timeline » | `onTimeline(taskId)` | idem | ouvre le dock Traces | `test_executer_timeline_montre_dispatch_et_refus_et_se_filtre_par_source` | oui |
| Réaliser (transition de gate) | `.btn.pri` « Réaliser » | `taskAction(id, action)` | `POST /api/workspace/tasks/<id>/{claim,move,block,close}` | succès (transition) | `test_executer_un_move_reussi_deplace_la_carte_puis_un_claim_est_refuse` | oui |
| Réaliser (transition de gate) | idem | idem | idem | refusé (`blocked:true`, preuve manquante) | `test_executer_un_move_reussi_deplace_la_carte_puis_un_claim_est_refuse` | oui |
| Réaliser — lecture seule cockpit | idem | idem | idem | bouton désactivé, texte « Écriture désactivée » | `test_cockpit_desactive_les_ecritures_d_executer` | oui |
| Champ « raison du blocage » | `input` (blocked) | valeur passée à `block` | `POST .../block` | requis pour la transition `blocked` | — | non |
| Board — changement de projet | — | `mount` re-fetch | `GET /api/workspace/tasks` | board se recharge par projet | `test_executer_le_board_change_quand_on_change_de_projet` | oui |
| Review sans evidence pack | — | affichage refus | `GET /api/workspace/tasks/<id>` | nomme l'artefact manquant | `test_executer_review_sans_evidence_pack_est_refuse_et_nomme_l_artefact` | oui |
| Chips `expected_evidence` | `.chip` | affichage seul | — | tronqué à 3/4 selon le contexte | — | non |
| Colonnes de gate (bandeau) | `.ex-col-gate` | affichage seul | — | nomme les preuves requises par colonne | — | non |

### Espace : Observer

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Vue (Runtime/Activité/RTK/Bench) | `#view-seg button` | `setView` (callback repassé à chaque `draw`, bug historique corrigé) | `GET /api/otel`, `/api/workspace/flows/runs`, `/api/events/log` | bascule visuelle + contenu | `test_observer_view_seg_bascule_correctement` | oui |
| Vue Activité/RTK/Bench — jamais muettes | idem | idem | idem | jamais un écran vide silencieux | `test_observer_activite_rtk_bench_ne_restent_jamais_muets` | oui |
| Vue RTK — indisponible | idem | `renderUnavailable` | aucune route serveur | message + commande CLI de repli | (couvert par le test ci-dessus) | oui |
| Vue Bench — indisponible | idem | idem | aucune | message + commande CLI de repli | (couvert par le test ci-dessus) | oui |
| Span OTel (ligne, clic) | `tr` | `onSelect(span)` | `GET /api/otel` | sélection dans l'inspecteur | — | non |
| Ligne d'événement (clic) | `.line` | `onSelect(list[0])` | `GET /api/events/log` | sélection | — | non |
| Bouton « voir la tâche » | `.btn` | `goto('executer', {task, view})` | — | navigue vers Executer | — | non |
| Run de flow visible sans TraceLedger | — | `mount` | `GET /api/workspace/flows/runs` | affiche le run même TraceLedger vide | `test_observer_montre_le_run_de_flow_meme_traceledger_vide` | oui |
| Sans trace — bloc vide unique | — | `mount` | toutes routes vides | un seul bloc vide, 0 erreur console | `test_observer_sans_trace_rend_un_seul_bloc_vide_sans_erreur_console` | oui |
| Aucune donnée de démo | — | — | — | jamais le mot « démo » | `test_chaque_espace_s_ouvre_sur_un_projet_reel` (paramétré) | oui |

### Espace : Mémoire

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Vue (zoom docbar) | `#view-seg button` | `setView` (dans `draw()`, bug historique corrigé) | `GET /api/memory/status` | bascule visuelle | `test_memoire_view_seg_bascule_correctement` | oui |
| Zoom Flotte / Ce projet | `#zoom-seg button` | bascule agrégation | `GET /api/workspace/memory/overview?projects=` | Flotte = 2 lignes (registre) | `test_switching_to_all_projects_shows_two_rows` | oui |
| Recherche croisée (bouton) | `searchBtn` | `runSearch` | `GET /api/workspace/memory/search` | résultats étiquetés par projet | `test_cross_project_search_labels_the_matching_project_only` | oui |
| Recherche croisée (Entrée clavier) | `input` keydown | `runSearch` | idem | équivalent clavier au clic | — | non |
| Recherche — requête vide | idem | idem | idem (`q` vide) | `results:[], count:0` sans requête lancée | — | non |
| Item mémoire (clic) | `.item` | `openInspector` | — | ouvre le panneau d'inspection | — | non |
| Item mémoire (clavier Enter/Espace) | idem | idem | — | équivalent clavier | — | non |
| Store affiché avant l'architecture | — | `mount` | `GET /api/memory/status` | ordre d'affichage | `test_memoire_montre_le_store_avant_l_architecture` | oui |
| Projet sans mémoire liée | — | `mount` | `GET /api/workspace/memory/overview` | état `unavailable`/`uninitialized`/`unreadable` nommé | — | non |

### Espace : Source

| Contrôle | Sélecteur | Handler | Route | État | Test(s) | Couvert |
|---|---|---|---|---|---|---|
| Étage (dossier racine) | `dirBtn` | ouvre/ferme | `GET /api/workspace/files?tier=` | 3 étages listés | `test_l_espace_source_liste_les_trois_etages` | oui |
| Fichier (ligne, clic) | `.sr-tree-file` | `openFile` | `GET /api/workspace/file?path=` | ouvre, bandeau lecture seule si étage kit | `test_ouvrir_un_fichier_du_kit_montre_le_bandeau_lecture_seule` | oui |
| Prendre un override | `.sr-banner .btn.pri` | `createOverrideAndOpen` | `POST /api/workspace/file/override` | crée puis rend éditable | `test_editer_un_fichier_du_kit_cree_l_override_diffuse_et_laisse_doctor_vert` | oui |
| Enregistrer (bouton) | `.btn` « Enregistrer » | `saveCurrent` | `POST /api/workspace/file/write` | succès | `test_l_enregistrement_reste_fonctionnel_avec_l_editeur_colore` | oui |
| Enregistrer — conflit/étage non éditable | idem | idem | idem | 403 nommant l'override à créer | — | non |
| Enregistrer — fichier hors racine | idem | idem | idem | chemin refusé (`WorkspacePathError`) | — | non |
| Onglet inspecteur (Fichier/Usage/Historique) | `.tab` | change `inspectorTab` | `GET /api/workspace/file/{usage,history}` | contenu par onglet | `test_l_inspecteur_expose_fichier_usage_et_historique` | oui |
| Console — commande autorisée | `input` console | `keydown` → exécute | `POST /api/workspace/command` | sortie affichée | `test_la_console_execute_grimoire_status_et_refuse_rm` | oui |
| Console — commande refusée (`rm`) | idem | idem | idem | refus explicite | `test_la_console_execute_grimoire_status_et_refuse_rm` | oui |
| Colorisation syntaxique | — | `source-editor.js` | — | tokens visibles | `test_la_colorisation_est_visible` | oui |
| Diagnostic sur chemin mort | — | éditeur | `GET /api/workspace/language` | souligné + message | `test_un_diagnostic_apparait_sur_un_chemin_mort` | oui |
| Complétion (agent) | — | éditeur | `GET /api/workspace/language?line&col` | insertion au choix | `test_la_completion_d_un_agent_s_insere` | oui |
| Infobulle glossaire depuis l'éditeur | — | éditeur | `GET /api/workspace/glossary` | ouverture bulle | `test_l_infobulle_du_glossaire_s_ouvre_depuis_l_editeur` | oui |
| Bouton Suggérer — aide permanente | `.sr-assist-help` | affichage | `GET /api/workspace/assist` | visible sans survol | `test_suggerer_a_une_aide_permanente_visible_sans_survol` | oui |
| Bouton Suggérer — indisponible | `.sr-assist-btn` | `disabled` | idem (`available:false`) | statut lisible sans survol, raison affichée | `test_suggerer_indisponible_est_lisible_sans_survol` | oui |
| Bouton Suggérer — clic (insérer) | `.sr-assist-btn` | `insérer` | `POST /api/workspace/assist` | aperçu + diagnostics recalculés | `test_suggerer_affiche_l_apercu_et_inserer_recalcule_les_diagnostics` | oui |
| Bouton Suggérer — pendant chargement | idem | `disabled` puis réactivé | idem (`loading:true`) | désactivé puis réactivé | `test_bouton_desactive_pendant_le_chargement_puis_reactive` | oui |

## Routes API

| Route | Fichier | Code | État de données | Test(s) | Couvert |
|---|---|---|---|---|---|
| GET /api/projects | cmd_cockpit.py:278 | 200 | liste vide / peuplée | `tests/test_cmd_cockpit.py::test_add_and_list` | oui |
| GET /api/fs/browse | cmd_cockpit.py:281 | 200/403/404 | — | — | non |
| POST /api/projects/select | cmd_cockpit.py:352 | 200/400/404 | — | — | non |
| POST /api/projects/update | cmd_cockpit.py:372 → project_update.py | 200 (`ok` variable) | `preview-only`, `completed`, `upgraded-checkpoint-pending`, `upgraded-but-failed`, `failed`, verrou concurrent, timeout 600 s | `test_mettre_a_jour_depuis_piloter_ne_repond_pas_404_sur_un_projet_selectionne`, `test_confirmer_depuis_piloter_fait_apparaitre_une_proposition`, `tests/unit/cli/test_cmd_upgrade_flow_run.py` (CLI, pas la route HTTP elle-même) | oui |
| POST /api/projects/update | idem | 404 (projet hors registre) | — | — | non |
| POST /api/projects/add / scan | cmd_cockpit.py:389 | 200/400/403/404 | scan tronqué (`MAX_SCAN_RESULTS`) | — | non |
| POST /api/setup / setup/plan | cmd_cockpit.py:423 | 200/400/403/404 | extensions partiellement échouées (200) | `test_wizard_initializes_a_blank_project_end_to_end` | oui |
| POST /api/projects/create | cmd_cockpit.py:451 | 200/400/403/409 | — | `test_creating_a_project_from_the_fleet_makes_it_appear`, `test_creating_a_project_on_an_existing_path_is_refused` | oui |
| POST /api/workspace/* (garde) | cmd_cockpit.py:494 | 403 hors home, **sauf** `proposals/<slug>/{accept,reject}` (issue #490, `is_proposal_decision`) | — | `test_the_write_guard_refuses_a_foreign_host_exactly_like_update`, `test_other_workspace_writes_stay_home_only` | oui |
| POST /api/blueprints/<id>/validate / simulate | cmd_cockpit.py:534 | 200/400/404 ; `ValueError` d'id malformé non catché ici | erreurs/avertissements dans un 200 | `test_valider_ecrit_le_verdict_dans_le_dock_problemes` | oui |
| POST /api/blueprints/<id>/compile | forge (atelier only) | — | — | — | non |
| POST /api/memory | cmd_cockpit.py:569 | 200/400/403/504 | mutation sans confirm → 403 | — | non |
| GET catch-all /api/* | cmd_cockpit.py:302 | 404 | — | `tests/unit/test_workspace_routes.py::test_une_route_inconnue_sous_le_prefixe_reste_un_404` | oui |
| GET /api/status, /api/setup, /api/archetypes, /api/needs, /api/setup/run, /api/extensions, /api/blueprints, /api/events/log, /api/stigmergy, /api/features, /api/cost-model, /api/otel, /api/primitives, /api/backends, /api/memory/status, /api/health | forge_routes.py (inchangé depuis 3.49.0, vérifié par diff) | 200 quasi toujours (repli interne documenté par module) | vides/partiels documentés par route (voir `forge_server.py`) | `tests/unit/test_workspace_api.py` (couverture large, non ligne-à-ligne ici) | oui |
| GET /api/blueprints/<id>[/diff] | cmd_cockpit.py:311 | 200/404 | — | `test_concevoir_s_ouvre_sur_le_blueprint_reel_du_projet` (indirect) | oui |
| GET collapse générique (OSError/PermissionError/FileNotFoundError → 500) | cmd_cockpit.py:299 | 500 au lieu de 404/403/400 documentés | toute lecture `/api/workspace/*` en erreur | — | non |
| GET /api/workspace/glossary | workspace_routes.py | 200 | vide / erreur dans le 200 | `test_le_glossaire_est_charge_depuis_l_api` | oui |
| GET /api/workspace/tasks | workspace_routes.py | 200 (collapse 500 si erreur) | `ledger:false`, filtré par mission/statut | `test_un_projet_sans_ledger_le_dit_au_lieu_de_rendre_un_board_vide` | oui |
| GET /api/workspace/tasks/<id> | workspace_routes.py | 200/500 (id malformé, tâche inconnue) | `next_moves_require` | `test_une_tache_inconnue_est_un_404_pas_un_500` (nom du test contredit le code actuel — à vérifier en priorité, cf. Lot B) | non |
| GET /api/workspace/flows/runs | workspace_routes.py (nouvelle route, issue #506) | 200 | filtrée par `blueprint` | `test_observer_montre_le_run_de_flow_meme_traceledger_vide` | oui |
| GET /api/workspace/evidence | workspace_routes.py (nouvelle route, issue #534) | 200 | `enrolled:false` sans standard, sinon tâches + gates + pack par tâche du board | `test_le_standard_enrole_liste_ses_taches_avec_gates_et_pack`, `test_un_projet_sans_standard_le_dit_au_lieu_de_rendre_un_board_vide` | oui |
| GET /api/workspace/agents | workspace_routes.py | 200/500 (`collect_agents` en erreur → 500 réel) | `freshness` par agent | `test_lire_les_agents_a_travers_le_cockpit` | oui |
| POST /api/workspace/proposals/<slug>/accept | workspace_routes.py → proposals.py | 200 toujours (jamais d'exception) | 6 `artifact_type` distincts (agent/skill/override-migration/memory-link/needs-hosts/repair) | les 6, via un vrai clic Piloter : `tests/e2e/test_workspace_proposals.py` (agent), `tests/e2e/test_workspace_proposals_types.py` (les 5 autres) | oui |
| POST /api/workspace/proposals/<slug>/reject | workspace_routes.py → proposals.py | 200 toujours | déjà acceptée → `ok:false` sans erreur HTTP | `test_reject_marks_without_writing_an_artifact` | oui |
