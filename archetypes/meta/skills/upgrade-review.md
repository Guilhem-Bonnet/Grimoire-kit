<!-- ARCHETYPE: meta — Adaptez les {{placeholders}} à votre projet via project-context.yaml -->
---
description: "Revue de mise à jour : lire, expliquer et recommander ce qu'un `grimoire upgrade-flow run` a laissé en attente (checkpoint destructif, propositions d'override/mémoire/besoins-hôtes) — jamais appliquer. À utiliser après un `up`/`upgrade-flow run` qui s'est arrêté avant son terme, ou dès qu'une proposition attend une décision."
tools: ["read", "execute"]
---

# Revue de mise à jour (issues #490, #506, #510)

`grimoire upgrade-flow run` s'arrête volontairement, non décidé, dès qu'une
décision engage l'humain : un checkpoint `destructive` (retrait d'agents
orphelins), ou une proposition V1 (override en dérive, fiche mémoire non
raccordée, besoin/hôte non déclaré). Ce skill lit ce contexte, l'explique,
recommande — et n'applique jamais rien lui-même. Chaque décision passe par
la CLI existante, jamais par une écriture directe de ce skill.

## 1. Contexte

```bash
grimoire upgrade-flow review --project-root . --json
```

Refuse de tourner (message explicite) si l'outil `grimoire` est plus ancien
que le kit aligné du projet (`tool_version`, issue #515) — dans ce cas,
mettre l'outil à jour avant de continuer (`pipx upgrade grimoire-kit` /
`pip install -U grimoire-kit`).

Complète si utile : `grimoire doctor -o json`, `git status --short` (si
dépôt git).

## 2. Une ligne par proposition

Pour chaque entrée de `pending_proposals` : type (`artifact_type`/`category`),
ce qu'accepter changerait concrètement (fichier visé, ou commande
`--dry-run` à citer — ex. `grimoire agent override convert <agent>
--dry-run` pour `override-migration`), et une recommandation motivée par le
projet :

| `category` | Recommandation |
|---|---|
| `memory-unlinked` (`memory-link`) sans porteur plausible | proposer un porteur, sinon `reject` |
| `needs-unresolved` / `hosts-undeclared` (`needs-hosts`) | vérifier d'abord que la commande/l'hôte existe réellement dans le dépôt (Makefile, package.json, pyproject, `.claude/`, `.github/`) avant de recommander `accept` |
| `override-drift` (`override-migration`) | `accept` si le contexte de l'override est préservé par la conversion (`--dry-run` d'abord), sinon revue humaine |
| `dead-reference` / `doctor-preexisting` (`repair`) | `accept` si une substitution est évidente ; sinon ouvrir le fichier à la ligne citée et décider |

## 3. Questions par lot

Un seul tableau numéroté, jamais une question par proposition :

```
1. override-migration-agent-x   accept | reject | plus tard
2. memory-link-fiche-y          accept | reject | plus tard
3. hosts-declare-enabled        accept | reject | plus tard
```

Réponse attendue : `"1 3 accept, 2 reject"` (ou toute combinaison
explicite) — n'appliquer que ce que la réponse couvre.

## 4. Application

Une seule porte par type de décision, jamais une autre :

```bash
grimoire proposals accept <slug>   # ou reject
grimoire doctor
```

## 5. Diff de `up`

```bash
git diff --stat
```

Message de commit proposé : `chore(<projet>): consommer grimoire-kit
<version>` — commit seulement sur accord explicite, jamais automatique.

## 6. Checkpoint destructif

Si `checkpoint_pending` est vrai : lister ce que le nœud `orphans` a
déjà identifié comme retirable (`_archive/<date>-pre-<version>/orphans/`),
attendre la décision explicite, puis soumettre par la CLI — jamais de
suppression hors de ce mécanisme :

```bash
grimoire flow resume <run_id> --result <fichier.json>
```

où `<fichier.json>` porte
`{"pins": {"out": {"contract": "upgrade-complete"}}, "checkpoint_decision":
"approve"|"reject", "checkpoint_reason": "..."}`.

## 7. Fin

```bash
grimoire doctor
grimoire standard verify .   # si le standard est activé
```

Résumer en trois listes : décidé, reporté, restant.

## Gardes

- Jamais accepter une proposition ou un checkpoint destructif sans réponse
  explicite dans la session en cours.
- Jamais lancer `grimoire up` ou `grimoire upgrade-flow run` soi-même — c'est
  le cockpit ou l'utilisateur qui déclenche le flow ; ce skill ne fait que le
  relire.
