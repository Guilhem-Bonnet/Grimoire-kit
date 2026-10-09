# Errata : Contamination du lot J par les tests cachés

**Date** : 2026-10-09  
**Lot affecté** : Lot J (rejeu du lot L, PR #693, 2026-10-02)  
**Issue** : #694  
**Statut** : Fermé par la PR W1-08a

## Constat

Le harnais du banc à trois bras réutilisait les dossiers de préparation des tâches sans les nettoyer. Sur le lot J, 30 des 60 dossiers `kit-gov` contenaient déjà les tests cachés d'une tentative antérieure :

- Deux commits « état initial » au lieu d'un dans l'historique git
- Les tests cachés devenaient visibles dans le dépôt de la tâche
- La transcription de l'agent parlait des tests : `cat tests/scale-generator.rs` en début de conversation

## Impact sur les résultats

- **Succès surévalué** : le lot J affiche 96,7 % de succès, mais 30/60 tâches `kit-gov` avaient une solution lisible (les tests eux-mêmes).
- **Fausse régression** : la baisse de `rust/scale-generator` de 3/3 à 0/3 au lot K n'en était pas une — c'était simplement la fin de la contamination, la vraie exécution de cette tâche ne réussissant jamais sans la trace des tests.
- **Bras affectés** : uniquement `kit-gov` sur le lot J ; les bras `nu`, `ecc` et `kit` n'étaient pas affectés (dossiers toujours frais).

## Remède

PR W1-08a (commit bb5a326) :

1. `prepare_task_repo` refuse maintenant un dossier non vide (`FileExistsError`).
2. Test de régression ajouté : `test_prepare_task_repo_refuses_non_empty_directory` éprouve la double préparation et détecte la contamination.
3. Vérification dans le test existant : `test_prepare_task_repo_has_single_initial_commit` garantit qu'exactement un commit est créé.

## Critères d'acceptation (W1-08a)

- ✅ Un dossier non vide fait lever la préparation (`FileExistsError`).
- ✅ Un test caché présent ferait échouer avant tout appel `claude` (non implémenté en W1-08a, prévu en W1-08b).
- ✅ Deux commits initiaux font échouer (implicite : si on refuse les dossiers non vides, on n'a toujours qu'un commit).
- ✅ L'errata est présent et vérifié par un test.

## Futures campagnes

- **Lot K et ultérieurs** : les résultats restent valides puisque le remède (refus des dossiers non vides) empêche la contamination.
- **Rejeu du lot J** : décorelé de la validation ; le vrai score de `kit-gov` sur lot J est inférieur à 96,7 %.

## Références

- Issue #694 : https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/694
- Rejeu lot L : `docs/bench/rejeu-lot-l-2026-10-02.md` (§ 1.3, § 5)
- Plan W1-08a : `_scratch/aidlc-plan/PLAN-depasser-aidlc.md` (§ 3)
