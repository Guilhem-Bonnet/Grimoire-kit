---
audience: référence
---

# Le contrôle des preuves

## Gardes du dépôt : `grimoire gates repo`

Dans un clone du kit, une seule commande lance les gardes que la CI exécute :
cliquet de taille, cohérence du CHANGELOG, prose émise, waivers de dépendances,
actions épinglées par SHA, tenets de `CONTRIBUTING.md`, audience et commandes
citées de la documentation, chiffres de vitrine sourcés. Le pre-commit
(hook `repo-gates`, au pre-push), le job `lint` de `ci-sdk.yml` et `make gates`
appellent la même commande. Une garde qui ne peut pas rendre son verdict (script
absent, délai dépassé) est « non évaluée » et fait échouer l'exécution.

| Commande | Description |
| --- | --- |
| `grimoire gates repo [--profile dev\|release] [--only <garde>] [--json] [--root .]` | Exécute les gardes ; code 1 si l'une échoue ou n'a pas été évaluée, code 2 hors d'un clone du kit |

Le profil `dev` (défaut) juge une branche : un commit `feat`/`fix`/`perf` exige
une entrée sous `[Unreleased]`. Le profil `release` juge le tag : `[Unreleased]`
vide, section la plus récente égale à `version.txt`.

## Standard agentique gouverné

Le groupe `grimoire standard` pilote le standard agentique (profils, patterns gouvernés, preuves). Référence des patterns : [Contrôles gouvernés](../../standard/controles-gouvernes.md).

| Commande | Description |
| --- | --- |
| `grimoire standard profiles` | Lister les profils (`starter → production`) |
| `grimoire standard needs` | Lister les besoins projet (groupés par tier) |
| `grimoire standard plan --needs <id>` | Prévisualiser le plan (profil + patterns + extras) sans rien écrire |
| `grimoire standard init [.] --needs <id>` | Générer les artefacts standard du projet |
| `grimoire standard verify` | Vérifier les artefacts (fail-closed) |
| `grimoire standard audit` | Rapport de conformité + gaps restants |
| `grimoire standard score` | Calculer et persister un score de conformité ; rapporte à part `rules_enforced` (règles de pack vérifiées), `rules_advisory`, `rules_failing`, `rules_unevaluated` et `rules_unclassified` |
| `grimoire standard gate check [--strict] [--no-run]` | Gate CI : échoue si une preuve obligatoire manque ; chaque manque nomme son chemin et un remède copiable. `--strict` exécute lui-même `gate run-tests` si la tâche doit une preuve d'exécution et qu'aucun run vert et frais n'existe ; `--no-run` restaure l'ancien comportement |
| `grimoire standard gate run-tests` | Exécuter la commande de test connue et enregistrer le verdict (`test-run.json`) |
| `grimoire standard task scaffold [--task-id X] [--dry-run]` | Créer, s'ils manquent seulement, les artefacts par tâche que les gates exigent (enveloppe, pack de preuve, registre d'affirmations, dossier d'acceptance, context bundle, decision trace), pré-remplis depuis le Mission Ledger ; idempotent ; le hook SessionStart le fait déjà pour la tâche active |
| `grimoire standard fix [--apply]` | Planifier / appliquer des correctifs sûrs |
| `grimoire standard doctor` | Vérifier la disponibilité des extras technologiques |
| `grimoire standard pattern` | Lister / inspecter les patterns |
| `grimoire standard detect-providers` | Détecter les providers LLM disponibles |
| `grimoire standard traceability [projet]` | Matrice exigences / contrôles / preuves de la norme ; avec un projet, le verdict de `verify` par artefact |
| `grimoire standard upstream` | Le standard amont a-t-il avancé depuis la révision épinglée (sortie 0/2/3) |

Sous-groupes (chacun avec `--help`) : `board`, `memory`, `context`, `decision`, `rules`, `hooks`, `gate`, `events`, `pattern`, `knowledge`, `task`.

---

## Hooks git

| Commande | Description |
| --- | --- |
| `grimoire hooks list` | Lister les hooks disponibles et leur état d'installation |
| `grimoire hooks status` | Résumer l'état d'installation — sortie `1` si incomplet |
| `grimoire hooks install` | Installer les hooks git Grimoire dans le dépôt |

---
