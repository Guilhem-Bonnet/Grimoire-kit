---
kind: command
description: 'Revoir ce que `grimoire upgrade-flow run` a laissé en attente (checkpoint destructif, propositions) et décider — jamais appliquer sans réponse explicite'
agent: 'agent'
tools: ['read', 'execute']
---

Tu appliques le protocole du skill `upgrade-review` : lire, expliquer,
recommander — jamais appliquer sans une réponse explicite de {user_name}
dans cette session. Toute décision passe par la CLI existante
(`grimoire proposals accept <slug>` ou `grimoire proposals reject <slug>`,
`grimoire flow resume <run_id> --result <fichier.json>`), jamais une
écriture directe de ce prompt.

## 1. Contexte

```bash
cd {project-root} && grimoire upgrade-flow review --json
```

Si cette commande refuse (outil `grimoire` plus ancien que le kit du
projet), STOP : afficher le message de refus et proposer la mise à jour de
l'outil (`pipx upgrade grimoire-kit` / `pip install -U grimoire-kit`) avant
toute autre étape.

Sinon, complète avec :

```bash
grimoire doctor -o json
git status --short
```

## 2. Une ligne par proposition

Pour chaque entrée de `pending_proposals` : type (`artifact_type`/`category`),
ce qu'accepter changerait concrètement (fichier visé, ou une commande
`--dry-run` à citer littéralement — ex. `grimoire agent override convert
<agent> --dry-run` pour `override-migration`), et une recommandation motivée
par CE projet :

- `memory-link` sans porteur plausible → proposer un porteur, sinon
  recommander `reject`.
- `needs-hosts` (`needs-declare-commands`/`hosts-declare-enabled`) →
  vérifier D'ABORD que la commande/l'hôte cité existe réellement dans le
  dépôt (Makefile, package.json, pyproject, `.claude/`, `.github/`) avant de
  recommander quoi que ce soit.
- `override-migration` → recommander `accept` si le contexte de l'override
  est préservé par la conversion (`--dry-run` d'abord), sinon revue humaine.
- `repair` avec substitution évidente → recommander `accept` ; sans
  substitution évidente → ouvrir le fichier à la ligne citée et décider.

## 3. Questions par lot

Un seul tableau numéroté pour toutes les propositions en attente — jamais
une question par proposition. Attendre une réponse explicite du type
`"1 3 5 accept, 2 reject, 4 plus tard"` avant de continuer.

## 4. Application

N'appliquer QUE ce que la réponse de {user_name} couvre explicitement,
un appel CLI par décision :

```bash
grimoire proposals accept <slug>   # ou reject
```

Puis :

```bash
grimoire doctor
```

## 5. Diff de `up`

```bash
git diff --stat
```

Proposer un message de commit (`chore(<projet>): consommer grimoire-kit
<version>`) — ne committer que sur accord explicite.

## 6. Checkpoint destructif

Si `checkpoint_pending` est vrai dans le contexte de l'étape 1 : lister ce
que le run a déjà identifié comme retirable
(`_archive/<date>-pre-<version>/orphans/`), attendre la décision explicite
de {user_name}, puis soumettre par la CLI :

```bash
grimoire flow resume <run_id> --result <fichier.json>
```

où `<fichier.json>` porte `{"pins": {"out": {"contract":
"upgrade-complete"}}, "checkpoint_decision": "approve"|"reject",
"checkpoint_reason": "..."}`. Jamais de suppression hors de ce mécanisme.

## 7. Fin

```bash
grimoire doctor
grimoire standard verify .
```

Résumer en trois listes, en {communication_language} : décidé, reporté,
restant.

## Gardes

- Jamais accepter une proposition ou un checkpoint « revue humaine »/destructif
  sans réponse explicite dans la session.
- Jamais lancer `grimoire up` ou `grimoire upgrade-flow run` toi-même — c'est
  le cockpit ou {user_name} qui déclenche le flow ; ce prompt ne fait que le
  relire et faire décider.
