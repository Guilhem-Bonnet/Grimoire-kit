---
audience: référence
---

# L'intendant du projet

`grimoire steward` fait l'état des lieux d'un projet **en lecture seule** :
ce qui reste réellement à faire, ce qui est déjà livré, ce qui est en double,
quelles PR ont été abandonnées. La carte est calculée par du code ; le
jugement est celui d'un agent qui suit la skill `grimoire-steward` ; chaque
preuve citée par l'agent est revérifiée par du code.

Rien n'est écrit au Mission Ledger, au dépôt git ni sur GitHub. Les seules
écritures sont sous `_grimoire-output/steward/`, un dossier qui s'ignore
lui-même (son propre `.gitignore`) : `git status` ne bouge pas.

## `grimoire steward map`

```bash
grimoire steward map [--json] [--cible origin/main] [--inactivite-heures 48]
                     [--locales-seulement] [--sans-sonde] [--repo OWNER/REPO]
```

La carte, schéma `grimoire-steward-map/v1`, aussi écrite dans
`_grimoire-output/steward/map.json`. Déterministe, sans LLM.

| Section | Contenu |
|---|---|
| `taches` | tâches du Mission Ledger : état, critères, dernière activité, claim, session et vivacité (`vivant`, `inactif`, `inconnu`, `terminé`), issues liées |
| `branches` | branches locales et distantes du remote de la cible : dernier commit, avance/retard, fusion, conflit, orpheline |
| `worktrees` | `git worktree list` : chemin, branche, verrouillé, élagable |
| `prs` | PR ouvertes (`gh`) : CI, `mergeable`, `mergeStateStatus`, dernière activité, auteur, tâches et sessions liées, conflit |
| `issues` | issues ouvertes : numéro, titre, labels, dernière activité |
| `signaux` | indices calculés, jamais des verdicts |

**Fusion.** Une branche est `fusionnee` si elle est ancêtre de la cible
(`ancetre`), si un commit de la cible porte exactement son arbre (`arbre` :
fusion squash ou rebase d'une branche à jour), si une PR fusionnée avait sa
tête (`pr #N`), ou si `git merge-tree` dans la cible ne changerait rien
(`fusion_sans_apport`). Sinon `non_fusionnee`, avec le conflit que la fusion
produirait : `aucun`, `changelog_seul` ou `autre`. `git merge-tree` écrit ses
arbres dans un magasin d'objets jetable, jamais dans `.git/objects`.

**Signaux.** `pr_inactive_sans_session_vivante` (aucune activité depuis
`--inactivite-heures` et aucune session liée `vivant` ou `inactif`),
`tache_ouverte_issue_fermee`, `tache_ouverte_citee_par_commit` (un identifiant
homonyme peut tromper : à vérifier dans le code), `conflit_changelog_seul`,
`conflit_autre`, `branche_orpheline` (`task/<id>` sans tâche vivante),
`branche_fusionnee_sans_etre_ancetre`, `worktree_elagable`.

**Non mesuré.** Sans `gh`, hors ligne ou sans cible, la section concernée
porte `"mesure": false` et sa raison, un champ vaut `"non mesuré"` ; la carte
n'échoue pas. `--sans-sonde` n'interroge pas l'hôte (`claude agents`) : la
vivacité ne repose alors que sur les journaux de session.

## `grimoire steward verdicts`

```bash
grimoire steward verdicts --file verdicts.json [--json] [--cible origin/main]
```

Revérifie un fichier `grimoire-steward-verdicts/v1` et écrit
`_grimoire-output/steward/validation.json` (`grimoire-steward-validation/v1`).
Code 0 si tout est établi, 1 si un verdict est rejeté, 2 si le fichier est
illisible.

```json
{
  "$schema": "grimoire-steward-verdicts/v1",
  "verdicts": [
    {"element": "tache:GAO-x-001", "verdict": "livree", "theme": "missions",
     "preuves": [{"type": "commit", "sha": "564e4add9"}]}
  ]
}
```

`element` : `tache:<id>`, `branche:<nom>`, `pr:<n>`, `issue:<n>` ou
`worktree:<chemin>`. Verdicts : `a_faire`, `livree`, `obsolete`, `doublon`,
`abandonnee`, `conflit_simple`, `conflit_semantique`, `humain`.

| Preuve | Champs | Vérification |
|---|---|---|
| `commit` | `sha`, `message_contient` | le commit existe et est ancêtre de la cible |
| `fichier` | `chemin`, `motif`, `absent` | chemin (et motif, `git grep -F`) présent sur la cible, ou absent si `absent: true` |
| `issue` | `numero`, `etat` (`fermee`) | état lu par `gh issue view` |
| `pr` | `numero`, `etat` (`fusionnee`) | état lu par `gh pr view` ; fusionnée : commit de fusion sur la cible |
| `inactivite` | `heures` | dernière activité recalculée (PR, branche, tâche) et aucune session liée vivante |
| `doublon_de` | `element` | l'autre élément existe |
| `note` | `texte` | aucune ; ne suffit jamais seule |

Suffisance : `livree` exige `commit`, `fichier`, `pr` fusionnée ou `issue`
fermée ; `obsolete` exige `commit`, `fichier`, `pr` ou `issue` ; `doublon`
exige `doublon_de` ; `abandonnee` exige `inactivite`. `conflit_simple` et
`conflit_semantique` sont recalculés (CHANGELOG seul, ou autre fichier).
Toute preuve fausse rejette le verdict, avec la raison ; un élément
introuvable ou non mesurable aussi (échec fermé).

## `grimoire steward report`

```bash
grimoire steward report [--verdicts verdicts.json] [--json]
```

Carte et verdicts validés, regroupés par action proposée : fermer avec
preuve, reprendre une PR abandonnée, réaligner, à faire par thème, à
l'humain, verdicts rejetés avec leur raison, signaux sans verdict établi.
Écrit `report.json` (`grimoire-steward-report/v1`) et `report.txt` sous
`_grimoire-output/steward/`. **Aucune action n'est faite.**

## Limites

- Le jugement reste celui d'un LLM : seule la preuve citée est vérifiée. Une
  preuve vraie peut appuyer un verdict faux (un fichier présent ne dit pas
  que le critère est rempli) ; la skill demande de lire le code.
- La fusion par arbre ne voit pas une fusion squash d'une branche en retard
  dont la cible a depuis retouché les mêmes lignes, sauf si une PR fusionnée
  avait sa tête : elle reste `non_fusionnee`, avec son conflit (échec fermé).
- Étape 1 : une proposition. Fermer, reprendre ou réaligner reste à faire.
