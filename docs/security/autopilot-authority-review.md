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
| 6 | Retirer une catégorie par la politique | La liste est dans le code. `autopilot` n'admet que `reserved` (liste de textes) : toute autre clé, tout autre type rend la politique invalide ; une politique invalide réserve **tout** et interdit l'activation | `test_a_policy_that_tries_to_remove_or_garbles_the_list_reserves_everything` |
| 7 | Un worker écrit `recommended` pour s'auto-répondre | La recommandation est une donnée non fiable : elle passe par les mêmes gardes (mandat, liste fermée, geste), et doit être l'une des `options`. Une réponse d'orchestrateur est inscrite comme **commentaire**, pas comme consigne contraignante (`kind="comment"`) | `test_a_recommendation_outside_the_options…` |
| 8 | Une session « décide » sans justification, ou en confiance basse | `judge` refuse : la question reste à l'humain, `autopilot.deferred` l'inscrit, `eligible_questions` ne la représente plus | `test_low_or_undeclared_confidence…` |
| 9 | « Abandonner » sans trace | Permis seulement pilote activé, avec une raison d'au moins une phrase, inscrite comme raison d'annulation et dans `autopilot.decided` (`reversible: false`) | `test_the_pilot_can_cancel_with_a_reason…` |
| 10 | Boucle de sessions (présentations sans borne) | Le plafond de sessions du jour, le WIP, le disjoncteur et le mandat bornent encore les lancements | `test_presentations_are_no_longer_bounded…` |

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
6. **L'annulation décidée par le pilote est terminale** : la machine à états n'a pas de sortie de `cancelled`. La raison
   est au ledger ; la rouvrir demande de recréer la tâche. (Le ticket attendait « réouvrable » : non tenable sans
   changer la machine à états, hors périmètre.)
7. **Autres limites du lot** : l'acceptation sous dérogation n'est pas traitée (aucune notion de dérogation dans le
   kit) ; un checkpoint de flow reste à l'humain (sa décision s'atteste dans le flux).

## Conclusion

Le pilote ne crée aucun nouveau canal d'autorité : il ne peut être ni activé ni coupé sans tour humain ou clé du
cockpit, il tombe avec le mandat, et ce qu'il décide passe par les gardes existantes plus une liste fermée jugée côté
noyau (pas dans la consigne). Les deux risques à surveiller sont l'exhaustivité du classement lexical (1) et le vol
d'identité de la session orchestrateur dont le rayon grandit (3).
