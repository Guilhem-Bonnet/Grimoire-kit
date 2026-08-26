---
description: "Mémoire projet Grimoire : retrouver une décision passée, un incident ou une convention, et consigner ce qui doit survivre à la session. À utiliser avant de reprendre un sujet déjà traité, et après toute décision non déductible du code."
tools: ["read", "execute"]
---

# Mémoire projet

La mémoire Grimoire retient ce que le dépôt ne dit pas : pourquoi une option a
été écartée, quel incident a motivé une contrainte, quelle convention l'équipe
applique sans l'avoir écrite.

## Avant de rouvrir un sujet

```bash
grimoire memory search "<sujet>"
```

Interroger avant de reconstruire une décision : réinventer un arbitrage déjà
tranché coûte plus cher que de le retrouver, et produit une réponse
contradictoire.

## Ce qui mérite d'être consigné

| Consigner | Ne pas consigner |
|---|---|
| une décision et son motif | ce que le code montre déjà |
| une option écartée et pourquoi | l'historique git |
| une contrainte imposée par un incident | l'état d'une tâche en cours |
| une convention non écrite | un détail propre à une seule session |

```bash
grimoire memory remember "<fait>" --type decisions --agent <tag-agent>
```

Une entrée sans motif est inutilisable plus tard : elle indique un choix sans
permettre de le réviser. Écrire le « pourquoi » dans la même entrée.

## Dates

Convertir toute date relative en date absolue au moment de l'écriture.
« La semaine dernière » ne veut plus rien dire à la relecture.
