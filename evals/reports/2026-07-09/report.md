# Rapport de campagne — bras « activé » — témoin `web-app-todo` — 2026-07-09

## Dispositif

| Paramètre | Valeur |
| --- | --- |
| Runs | 40 (8 tâches × 1 bras `activated` × 5 répétitions), tous agrégés, zéro exclusion |
| Comparaison | bras `governed` et `baseline` de la campagne 2026-07-03 (mêmes pins) |
| Baseline | pin `d1037105d9d4dee866d6281905b3b7ddfe6b58a2` — identique |
| Kit | grimoire-kit 3.18.0 via `evals/.venv` (pin de la suite ; PATH du run préfixé, le CLI global ayant évolué vers 3.22+) |
| Runner | Claude Code CLI 2.1.101 (identique), `claude -p`, `claude-sonnet-4-6`, `--max-turns 100`, timeout 30 min |
| Activation | hook SessionStart pré-enregistré (`witnesses/web-app-todo/ACTIVATION.md`, commit `c8301a2b` poussé avant exécution) ; prompt de tâche strictement identique aux autres bras |
| Jugement | grille pré-enregistrée `witnesses/web-app-todo/JUDGING.md` inchangée, appliquée strictement |
| Journal | `evals/runs/2026-07-09-activated/JOURNAL.md` (local) ; pilote rep-0 exclu de l'agrégation |
| Coût total | 32,56 $ (moyenne 0,81 $/run) |

## Résultats

| Tâche | completed | tests verts | régressions (règle primaire) |
| --- | --- | --- | --- |
| feat-due-dates | 0/5 | 5/5 | 0 |
| feat-bulk-complete | 0/5 | 5/5 | 0 |
| fix-timezone-display | 0/5 | 5/5 | 0 |
| fix-n-plus-one | 5/5 | 5/5 | 0 |
| refactor-handlers | 0/5 | 5/5 | 0 |
| refactor-api-client | 0/5 | 5/5 | 5 |
| migrate-go-version | 5/5 | 5/5 | 0 |
| sec-rate-limit | 5/5 | 5/5 | 0 |
| **Total activated** | **15/40** | **40/40** | **5** |
| Rappel governed 2026-07-03 | 7/40 | 38/40 | 16 |
| Rappel baseline 2026-07-03 | 6/40 | 38/40 | 13 |

Comptage secondaire pré-enregistré (ACTIVATION.md) : les 5 régressions de
la règle primaire sont toutes « adapté vert » (tests baseline réécrits sur
le client API mocké, restés verts) ; **« cassé/supprimé » = 0** sur les
40 runs.

## Verdict du critère pré-enregistré

> Le bras est déclaré « utile » si les régressions baissent d'au moins
> 30 % relatif vs baseline, sans baisse du taux de complétion ni hausse
> du coût tokens de plus de 25 %.

- Régressions : 5 vs 13 baseline → **−61,5 %** — critère atteint.
- Complétion : 15/40 vs 6/40 — hausse nette, critère atteint.
- Coût : 32,56 $ vs 22,09 $ → **+47,4 %** — dépasse le plafond de +25 %.

**Conclusion formelle : « non démontré »** — le critère composite échoue
sur sa composante coût. Conformément au protocole, aucune revendication
d'« utilité » au sens pré-enregistré ne peut être faite ; le README du kit
ne doit pas faire de claim sur ce critère.

## Le mécanisme d'activation fonctionne : engagement 40/40

Critère pré-enregistré du mécanisme (ACTIVATION.md) : ≥ 80 % de runs
engagés. Mesuré : **100 %** — enveloppe de tâche remplie 40/40,
evidence-pack avec lignes d'inventaire concrètes 40/40. Contre **0/40**
sur le bras governed 2026-07-03 (présence passive). Le hook SessionStart
suffit à faire engager le protocole du standard par l'agent.

Écart de mesure journalisé : `gate_check_invoked` ne peut pas être mesuré
depuis la sortie `claude -p --output-format json` (résultat final
seulement, pas de transcript) ; de plus `gate check --strict` passe sur
un enrôlement pristine (contrôle effectué), donc l'exit code final est
non informatif. L'engagement est mesuré par les artefacts (enveloppe,
inventaire d'evidence), plus fiables.

## Lecture exploratoire (non pré-enregistrée, signalée comme telle)

- **Coût par tâche complétée** : baseline 3,68 $ (22,09/6) vs activated
  **2,17 $** (32,56/15) — le surcoût brut de +47 % achète 2,5× plus de
  tâches complétées ; par unité de valeur, le bras activé est ~40 % moins
  cher. Le critère pré-enregistré plafonne le coût brut par run : cette
  lecture inverserait le verdict et devrait être tranchée *avant* la
  prochaine campagne.
- **Régressions « dures »** (règle de sensibilité du rapport 2026-07-03) :
  activated **0**, vs baseline 12, governed 7. Aucun build cassé, aucun
  test supprimé, 40/40 états finaux verts (un échec vitest local était un
  artefact d'environnement npm, rejoué vert — journalisé).
- Les complétions se concentrent sur les tâches où la discipline de preuve
  suffit (fix-n-plus-one : test de comptage N+1 écrit 5/5 ; migrate :
  changelog des dépréciations 5/5 ; sec-rate-limit : test 429 5/5).
  Les échecs de complétion restants sont des critères d'acceptation non
  devinables depuis le prompt (test de tri frontend, deux fuseaux,
  idempotence bornée, tests du nouveau package, erreurs 401/500/réseau) —
  identiques aux deux bras précédents.

## Menaces à la validité

- n = 5 par cellule ; signal, pas de test statistique.
- Comparaison inter-campagnes (2026-07-03 vs 2026-07-09) : mêmes pins,
  même runner, même modèle, mais pas de randomisation temporelle ; une
  dérive du modèle servi entre les deux dates ne peut pas être exclue.
- Opérateur unique (agent) pour le jugement ; grille pré-enregistrée
  appliquée strictement, cas limites tranchés `false`.
- Le comptage « adapté vert » sur refactor-api-client suit la règle
  primaire à la lettre ; les 5 modifications préservent le comportement
  testé au niveau client (assertions HTTP déléguées au client mocké).

## Recommandations pour la suite

1. **Trancher le critère coût avant toute nouvelle campagne** : coût brut
   par run (actuel) ou coût par tâche complétée (proposé). Le choix
   décide du verdict — il doit être pré-enregistré, pas choisi après coup.
2. Le mécanisme d'activation (hook SessionStart) est validé et peu coûteux
   à shipper : candidat à l'intégration produit (`grimoire standard init`
   pourrait l'installer par défaut pour Claude Code).
3. Ré-évaluer la parenthèse « tests du nouveau package » de
   refactor-handlers (récurrence : elle décide seule du 0/15 cumulé sur
   trois bras).
4. Si un bras « activé v2 » est lancé : inclure les critères d'acceptation
   dans le prompt d'une moitié des reps pour mesurer le coût de la
   non-divulgation (l'écart complétion est dominé par des critères non
   devinables).
