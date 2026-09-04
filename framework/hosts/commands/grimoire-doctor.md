---
description: "Diagnostiquer et réparer l'installation Grimoire du projet"
argument-hint: "[--fix]"
tools: ["read", "edit", "execute"]
---

Diagnostique l'installation Grimoire de ce projet.

1. `grimoire -o json doctor`
2. `grimoire up --dry-run` pour voir ce qu'une remise à niveau changerait.
3. `grimoire -o json host status` pour l'état des surfaces hôtes
   (agents, skills, commandes, hooks) et ce qui est dégradé sur cet hôte.

Présente les anomalies par ordre de gravité, avec la commande exacte qui
corrige chacune. N'applique une réparation que si l'argument `--fix` est
fourni, et annonce alors ce que tu as modifié.
