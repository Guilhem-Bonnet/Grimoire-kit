---
audience: décision
---

# Pilote automatique : revue adversariale du modèle d'autorité (P82)

Périmètre : `src/grimoire/missions/autopilot.py`, `orchestrator_session.py` (`answer_plan`, `eligible_questions`,
`_apply_recommendations`), `service.py` (`answer`, `ask`), `cli/cmd_autonomy.py`, `tools/workspace_routes.py`.
Méthode : pour chaque pouvoir que le pilote ajoute, qui peut le prendre, avec quelle preuve, et ce qu'un attaquant
(un worker malveillant, une session d'agent, un contenu de PR) obtient en le forgeant. Les tests cités sont dans
`tests/unit/missions/test_autopilot.py`.

## Ce que le pilote ajoute

Rien n'est nouveau côté **gestes** : une réponse d'origine `orchestrator` passe toujours par `service.answer`, le mandat
(geste `answer` ou `resume`, type de tâche, politique d'escalade), la provenance et, pour une session, l'inscription
bornée au canal `task answer`. Le pilote élargit **quelles questions** ces gardes laissent passer, et ajoute trois gardes :
l'état du pilote, la liste fermée, la décision (recommandation, justification, confiance).

## Menaces et réponses

| # | Menace | Réponse | Test |
|---|---|---|---|
| 1 | Un agent active ou coupe le pilote | `AutopilotStore.set` refuse toute origine autre que `human` ; la CLI n'inscrit qu'attesté par un tour humain de la session (challenge lié à `on/off`, projet, mandat, session ; tour consommé, un tour = une décision) ; le cockpit exige la clé de contrôle, y compris pour couper | `test_no_non_human_origin_switches_the_pilot`, `test_the_cli_channel_without_a_human_turn_writes_nothing`, `test_the_cockpit_route_needs_the_control_key` |
| 2 | Rejouer un tour humain (celui d'un `off` pour un `on`, ou d'un octroi de mandat) | Le challenge porte l'empreinte `{on/off, projet, mandat}` ; le registre des tours consommés est commun au mandat, aux flows et au pilote | (mécanisme du mandat, réutilisé) |
| 3 | Le pilote survit au mandat | L'entrée « activé » nomme le mandat ; `state()` exige mandat actif **et** identifiant identique. Révoqué, expiré, remplacé, journal illisible : coupé, sans écrire. Un nouveau mandat ne rallume rien | `test_revoking_the_mandate_cuts_the_pilot…`, `test_a_tampered_journal_carries_no_pilot` |
| 4 | Falsifier le journal | Chaîne de hash, tête et marqueurs du mandat (inchangés) ; une entrée « activé » sans `by: human` est ignorée | `test_a_tampered_journal…` |
| 5 | Décider une question de la liste fermée | `reserved_reason` est jugé dans `answer_plan` (donc pour toute réponse `orchestrator`, session comme tick) **et** dans `eligible_questions` (la session ne la voit pas). Texte plié : sans accents, casse, caractères invisibles ; lettres non latines (homoglyphes) réservées ; `permission` : seuls des chemins explicites dans le dépôt, résolus (liens symboliques suivis), passent, une variable (`$HOME`, `%X%`) ou l'absence de chemin réserve | `test_the_closed_list_stays_human_even_with_the_pilot` (12 cas), `test_a_permission_inside_the_repository_is_not_reserved` |
| 6 | Retirer une catégorie par la politique | La liste est dans le code. `autopilot` n'admet que `reserved` (liste de textes, qui ajoute), `default_action` (`ask` ou `apply_recommended`), `precedents` (`on` ou `off`) et `precedent_days` (1 à 365), dont aucun ne touche à la liste : toute autre clé, tout autre type, toute autre valeur rend la politique invalide ; une politique invalide réserve **tout** et interdit l'activation | `test_a_policy_that_tries_to_remove_or_garbles_the_list_reserves_everything` |
| 7 | Un worker écrit `recommended` pour s'auto-répondre | La recommandation est une donnée non fiable : elle passe par les mêmes gardes (mandat, liste fermée, geste), et doit être l'une des `options` quand il y en a. Le tick ne la prend, et elle ne vaut décision d'une session, que si l'agent peut se la répondre (`author_plan`) : jamais pour une question `permission` ou `merge`, jamais « abandonner » ni un choix `danger`, et la liste fermée est alors jugée titre de la tâche compris, quelles que soient la nature et les options que l'agent a choisies. La recommandation elle-même fait partie des textes jugés par la liste fermée (un texte libre ne la contourne pas), et une seconde liste de mots — fusion, publication, déploiement, autorisation, tag (`engaging_reason`) — retient les questions d'agent rédigées en choix ordinaire, sans toucher aux questions que le kit écrit ni au jugement assumé d'une session. Une réponse d'orchestrateur est inscrite comme **commentaire**, pas comme consigne contraignante (`kind="comment"`) | `test_a_recommendation_outside_the_options…`, `test_le_tick_n_applique_pas_la_recommandation_d_un_agent_qui_engage`, `test_la_session_ne_recopie_pas_la_recommandation_d_un_agent_qui_engage` |
| 8 | Une session « décide » sans justification, ou en confiance basse | `judge` refuse : la question reste à l'humain, `autopilot.deferred` l'inscrit, `eligible_questions` ne la représente plus | `test_low_or_undeclared_confidence…` |
| 9 | « Abandonner » sans trace | Permis seulement pilote activé, avec une raison d'au moins une phrase, inscrite comme raison d'annulation et dans `autopilot.decided` (`reversible: false`) | `test_the_pilot_can_cancel_with_a_reason…` |
| 10 | Boucle de sessions (présentations sans borne) | Le plafond de sessions du jour, le WIP, le disjoncteur et le mandat bornent encore les lancements | `test_presentations_are_no_longer_bounded…` |

## Action par défaut (`autopilot.default_action`)

Sous `apply_recommended`, l'option recommandée d'une question tranche quand personne n'assume de décision (source
`default`). Sans le réglage, rien de ce qui suit n'existe : `default_plan` ne rend aucune option. Ce que le réglage
ajoute vraiment : le tick applique sans session l'option par défaut des questions de conduite que le kit écrit
(« reprendre », « réessayer »), là où une session jugeait. Pour une question d'agent, l'option recommandée est
appliquée par le tick dès que le pilote est actif, avec ou sans ce réglage, dans les limites de la menace 7 : le
repli « confiance basse » n'en couvre qu'un sous-ensemble. Les tests cités sont dans `tests/unit/missions/test_autopilot_default_action.py`.

| # | Menace | Réponse | Test |
|---|---|---|---|
| 11 | Une valeur mal écrite active un comportement inattendu | Valeur exacte exigée (ni casse, ni synonyme, ni vide) ; sinon politique invalide : tout est réservé, le pilote ne s'active pas | `test_une_valeur_inconnue_rend_la_politique_invalide` |
| 12 | Une question réservée est tranchée par défaut | La liste fermée est jugée avant toute décision (`answer_plan`), puis une seconde fois pour la décision par défaut, titre de la tâche compris tant que le kit n'a pas écrit la question | `test_apply_recommended_ne_tranche_jamais_une_categorie_reservee` |
| 13 | Un agent imite une question du kit (nature `resume_branch`, options « reprendre / abandonner ») pour sortir de la réserve | `kit_written` ne lit que ce que le code du kit écrit : l'origine `tick` (aucune commande ni outil MCP ne la laisse déclarer) ou l'événement d'escalade qui nomme la question ; la forme ne prouve rien | `test_une_question_d_agent_ne_sort_pas_de_la_liste_fermee_en_imitant_le_kit` |
| 14 | L'option appliquée n'est pas une option de la question, ou annule la tâche | `default_option` rend l'option telle que la question l'écrit, jamais « abandonner » ni un choix `danger` ; rien pour une question `merge` ou `permission` | `test_aucune_option_par_defaut_hors_des_options_sures_de_la_question`, `test_une_decision_par_defaut_n_abandonne_jamais` |
| 15 | Boucle escalade → reprise par défaut (le merger ne remet son compteur à zéro qu'à une réponse humaine) | Au plus deux décisions par défaut par tâche depuis la dernière réponse humaine, en plus de la borne des reprises ; au-delà, la question se juge comme sous `ask` | `test_les_decisions_par_defaut_d_une_tache_sont_bornees` |
| 16 | Une reprise par défaut relance une tâche dont le worker travaille encore | Échec fermé (`worker_at_work`) : l'option n'est appliquée que si `claude agents` est lisible, que le tick a noté une session pour la tâche, et que cette session y figure dans un état de repos reconnu. L'état est traduit par `liveness.probe_status` (`status: busy` ou `state: working` selon la version de l'hôte) ; illisible, introuvable, inconnu ou vide vaut « au travail ». La voie de la session (`answer_plan`) porte la même garde | `test_le_tick_n_applique_rien_par_defaut_tant_que_le_worker_n_est_pas_prouve_a_l_arret`, `test_une_session_en_confiance_basse_ne_reprend_pas_par_defaut_un_worker_au_travail` |
| 17 | Une option appliquée sans jugement passe inaperçue | `autopilot.decided` (source `default` ou `recommended`), inscrit **avant** la réponse : un arrêt entre les deux ne laisse pas une réponse sans décision. Bloc « Décisions à revoir » de « À traiter » pour les deux sources, journal avec « Revenir » | `test_la_route_d_attention_rend_le_bloc_des_decisions_a_revoir`, `test_le_bloc_montre_aussi_la_recommandation_d_un_agent_appliquee_sans_la_compter_dans_la_borne` |

Une décision par défaut reste une **réponse** : elle écrit la réponse et rend la tâche `ready`. Elle ne fusionne,
n'accepte ni ne publie rien — les questions « fusionner ? » et « accepter ? » du merger n'ont ni option ni
recommandation, et le merger ne lit aucune réponse. Le risque résiduel 1 vaut ici comme ailleurs : « publier » ou
« déployer » ne sont pas dans la liste fermée ; un projet qui règle `apply_recommended` les ajoute par
`autopilot.reserved` s'il ne veut pas qu'un worker agisse sur une telle réponse.

## Jurisprudence (`autopilot.precedents`)

Sous `on`, la dernière réponse humaine à une question que le kit a écrite tranche les questions de la même classe
(source `precedent`). C'est une extension d'autorité : une réponse donnée une fois décide ensuite seule. Sans le
réglage, `precedent_plan` ne rend rien ; seule la cause codée des escalades est écrite en plus au ledger. Le mandat
n'est pas touché : ni geste, ni provenance, ni empreinte. Les tests cités sont dans
`tests/unit/missions/test_precedents.py`.

| # | Menace | Réponse | Test |
|---|---|---|---|
| 18 | Un agent rédige sa question pour tomber sous un précédent favorable | La classe ne lit ni le texte, ni les options, ni la raison : elle vient de la cause codée de l'événement d'escalade qui nomme la question, ou de la nature d'une question d'origine `tick` attestée par la récolte. Une question d'agent n'a pas de classe | `test_une_question_d_agent_n_a_ni_classe_ni_precedent`, `test_la_classe_d_une_escalade_ne_passe_pas_a_une_autre_question_de_la_meme_tache` |
| 19 | Un agent se fabrique un précédent | Aucun fichier de précédents : ils sont relus au ledger, d'une réponse dont l'origine `human` est posée par le canal de réponse. Forger un précédent, c'est forger une réponse humaine — la surface existante, pas une de plus | `test_une_reponse_d_une_autre_origine_ne_fait_pas_precedent` |
| 20 | Jurisprudence en chaîne : une décision du pilote devient précédent | Seul `task.human_answered` d'origine `human` compte ; jugement, option par défaut et précédent appliqué sont d'origine `orchestrator` | `test_ni_une_decision_du_pilote_ni_un_precedent_applique_ne_font_precedent` |
| 21 | « Fusionne » ou « accepte », dit une fois, vaut pour toujours | Les questions « fusionner ? » et « accepter ? » du merger, comme les escalades sur pack de preuve absent, clôture refusée ou relecteur non inscrit, sont hors de la liste fermée des classes ; nature `merge` ou `permission` : jamais | `test_une_reponse_humaine_a_une_question_de_fusion_ou_d_acceptation_ne_fait_jamais_jurisprudence`, `test_une_escalade_sans_cause_reconnue_ne_fait_pas_precedent` |
| 22 | Un précédent applique ce que la décision par défaut refuse | Mêmes filtres : liste fermée, option encore proposée, jamais « abandonner » ni un choix `danger`, worker prouvé à l'arrêt (tick et session), borne par tâche partagée avec le défaut | `test_un_precedent_abandonner_ne_s_applique_jamais…`, `test_un_precedent_ne_s_applique_jamais_a_une_categorie_reservee`, `test_un_precedent_attend_la_preuve_que_le_worker_ne_travaille_plus`, `test_les_precedents_appliques_partagent_la_borne_des_decisions_par_defaut` |
| 23 | Une vieille réponse gouverne indéfiniment, ou contre l'avis de l'humain | Péremption (`precedent_days`, 30 jours) ; la dernière réponse de la classe l'emporte, même « abandonner » — jamais appliqué, mais l'option par défaut ne reprend pas contre lui ; `--forget` (canal humain) et « Revenir » sur une application la retirent, sans ressusciter une réponse plus ancienne | `test_un_precedent_expire`, `test_oublier_un_precedent_le_retire_sans_ressusciter_le_precedent`, `test_revenir_sur_une_application_la_defait_et_retire_le_precedent` |
| 24 | Un agent oublie un précédent, ou l'orchestrateur sous mandat | `forget` refuse toute origine autre que `human`, établie comme pour `task answer` ; aucun mandat n'ouvre ce geste | `test_seul_le_canal_humain_oublie_un_precedent`, `test_cli_forget_est_refuse_a_un_agent` |
| 25 | Lecture incertaine des précédents | Ledger illisible, oubli ou application sans identifiant : aucun précédent appliqué, et le tick comme la raison de la question laissée le disent | `test_des_precedents_illisibles_n_appliquent_rien_et_le_tick_le_dit` |

Risque résiduel propre à la jurisprudence : la classe dit *pourquoi* le kit escalade, pas *sur quoi*. « Reprendre »
après deux relectures refusées sur une tâche vaut ensuite pour une autre tâche, dont le désaccord peut être d'une
autre nature. Les garde-fous sont la liste fermée (jugée sur le texte de la nouvelle question), la borne par tâche,
la péremption et la visibilité (« Décisions à revoir », « Revenir ») ; l'option appliquée ne peut être que la reprise.

## Risques résiduels (assumés, à connaître)

1. **Classement lexical.** La liste fermée repose sur des motifs, pas sur une compréhension. Une question qui dit
   « déployer en prod » ou « publier la version » n'est pas dans la liste du ticket : le projet l'ajoute par
   `autopilot.reserved`. Un synonyme rare ou une autre langue passe. Le biais est de réserver à tort (faux positifs
   coûteux en questions, pas en sécurité), mais il n'y a pas de garantie de complétude. La session reçoit en plus la
   liste dans sa consigne.
2. **Une réponse d'orchestrateur ne donne aucun accès.** Répondre à une question `permission` ou `merge` écrit une
   réponse et réveille la tâche ; elle n'élargit pas les permissions de l'hôte et ne fusionne rien (aucun canal de
   fusion n'est ouvert à la session). Le danger est qu'un worker agisse sur une réponse trop large : c'est pourquoi la
   réponse reste un commentaire et la liste fermée exclut ce qui engage.
3. **Identité de session non secrète** (limite du #962, inchangée mais plus coûteuse). Un worker qui sort de tout
   worktree et présente seulement l'identifiant volé de l'orchestrateur peut répondre à toute question présentée à
   cette session, **y compris la sienne**. Pilote activé, ces questions incluent `retry_exhausted` et `merge`, et les
   tâches faites à la main sont servies (la vérification « tâche lancée par le tick » ne s'applique plus). La liste
   fermée, le mandat et la justification tracée bornent les dégâts ; seule une preuve de l'appelant par le hook de
   l'hôte fermerait ce trou.
4. **Attestation par tour humain** : comme pour le mandat, un agent peut émettre le challenge, et un tour humain
   quelconque et postérieur de la session l'atteste (`LIMIT_NOTE`). Le pilote n'ajoute pas ce risque, il le partage ;
   exiger que le tour nomme la demande n'est pas faisable sans hook de l'hôte.
5. **Confiance et justification déclaratives.** Un LLM peut déclarer `high` à tort. Elles sont journalisées au
   ledger ; la garantie est la traçabilité et la révocation (couper le pilote ou le mandat), pas l'exactitude.
6. **L'abandon décidé par le pilote reste réouvrable** : la machine à états n'a pas de sortie de `cancelled`, donc
   le pilote range la tâche en `failed` (transition `waiting → failed`, ajoutée) avec sa raison au ledger ; un humain
   la rouvre par `task move --to ready`. Il n'annule jamais.
7. **Autres limites du lot** : l'acceptation sous dérogation n'est pas traitée (aucune notion de dérogation dans le
   kit) ; un checkpoint de flow reste à l'humain (sa décision s'atteste dans le flux).

## Conclusion

Le pilote ne crée aucun nouveau canal d'autorité : il ne peut être ni activé ni coupé sans tour humain ou clé du
cockpit, il tombe avec le mandat, et ce qu'il décide passe par les gardes existantes plus une liste fermée jugée côté
noyau (pas dans la consigne). Les deux risques à surveiller sont l'exhaustivité du classement lexical (1) et le vol
d'identité de la session orchestrateur dont le rayon grandit (3).
