---
description: "Intendant d'un projet Grimoire : produire un fichier de verdicts prouvés (grimoire-steward-verdicts/v1) à partir de `grimoire steward map`, pour dire ce qui reste réellement à faire. À utiliser quand le kanban, les branches ou les PR ne reflètent plus l'état réel du dépôt (tâches déjà livrées, doublons, PR abandonnées)."
tools: ["read", "execute"]
---

# Intendant : verdicts prouvés

L'intendant fait l'état des lieux sans rien changer. La carte est calculée
par du code, le jugement est le tien, et seule la preuve que tu cites est
vérifiée par du code. Un verdict sans preuve vérifiable est rejeté.

## 1. Lire la carte

```bash
grimoire steward map --json > carte.json
```

Elle liste les tâches du Mission Ledger, les branches (fusionnées par
comparaison d'arbres, en conflit sur le seul CHANGELOG ou ailleurs), les
worktrees, les PR et issues ouvertes, et des **signaux** (`signaux`). Un
signal est un indice, pas un verdict. Un champ « non mesuré » n'est ni vrai
ni faux : ne conclus rien dessus.

## 2. Juger chaque élément contre le code

Pour chaque élément (`ref` : `tache:<id>`, `branche:<nom>`, `pr:<n>`,
`issue:<n>`, `worktree:<chemin>`) :

- **Lis le code de la cible** (`git show origin/main:<chemin>`,
  `git grep -n <motif> origin/main`), pas le titre de la tâche ni le message
  d'un commit. Un critère d'acceptation se vérifie dans le code livré.
- **Méfie-toi des identifiants homonymes** : un commit qui cite `#12` ou un
  identifiant de tâche peut parler d'autre chose. Le signal
  `tache_ouverte_citee_par_commit` invite à lire ce commit, il ne prouve rien.
- **Méfie-toi des notes périmées** : une description ou une consigne dit ce
  qu'on pensait à l'époque, le code dit ce qui est.
- **En cas de doute entre `livree` et `a_faire` : `a_faire`.** Fermer à tort
  perd du travail ; laisser ouvert coûte une relecture.

Verdicts : `a_faire`, `livree`, `obsolete`, `doublon`, `abandonnee`,
`conflit_simple` (conflit limité au CHANGELOG), `conflit_semantique`,
`humain` (décision, secret ou réglage que seul un humain peut poser).

## 3. Toujours une preuve typée

| Type | Champs | Ce que le code vérifie |
|---|---|---|
| `commit` | `sha`, `message_contient` | le commit est sur la cible, son message contient le texte |
| `fichier` | `chemin`, `motif`, `absent` | le fichier et le motif sont sur la cible ; `absent` : n'y sont plus **après y avoir été** |
| `issue` | `numero`, `etat` (`fermee` par défaut) | l'état et la raison de fermeture lus par `gh` |
| `pr` | `numero`, `etat` (`fusionnee` par défaut) | fusionnée dans la cible (base et commit de fusion) |
| `inactivite` | `heures` | dernière activité recalculée, sessions liées toutes terminées |
| `doublon_de` | `element` | l'autre élément existe, reste ouvert, n'est pas lui-même jugé doublon |
| `note` | `texte` | rien : une note seule ne suffit jamais |

La preuve doit porter **sur l'élément jugé** :

- `livree` d'une branche : seule sa fusion dans la cible compte (recalculée) ;
- `livree` d'une tâche ou d'une issue : un `commit` avec `message_contient`
  égal à l'identifiant (ou `#N`), une `pr` fusionnée qui le nomme (titre,
  corps, branche), ou l'issue elle-même fermée comme terminée — fermée
  `not_planned` ou `duplicate`, ce n'est pas livré ;
- `abandonnee` : seulement si les sessions liées sont **terminées** ;
  « inconnu » n'est pas « morte ». Sans tâche liée : aucun travail non
  commité et un dernier commit au-delà du seuil ;
- un worktree avec du travail non commité n'est jamais `livree`,
  `obsolete` ni `abandonnee`.

```json
{
  "$schema": "grimoire-steward-verdicts/v1",
  "verdicts": [
    {"element": "tache:GAO-r06-commande-001", "verdict": "livree", "theme": "code-mort",
     "preuves": [{"type": "fichier", "chemin": "src/grimoire/missions/beads_adapter.py", "absent": true},
                 {"type": "pr", "numero": 715}]},
    {"element": "tache:GAO-r02-d-cider-001", "verdict": "a_faire", "theme": "code-mort", "taille": "M",
     "note": "Les 4 classes restent orphelines."}
  ]
}
```

## 4. Faire valider, puis rapporter

```bash
grimoire steward verdicts --file verdicts.json   # code 1 si un verdict est rejeté
grimoire steward report --verdicts verdicts.json
```

Corrige ou retire chaque verdict rejeté (la raison est donnée) ; ne contourne
jamais un rejet par une note. Le rapport regroupe par action proposée et
**ne fait rien** : aucune tâche fermée, aucune PR touchée.
