---
description: "Choisir et activer la bonne persona Grimoire du projet. À utiliser quand une demande relève clairement d'un rôle (architecture, tests, documentation, sécurité, produit) plutôt que d'une exécution directe, ou quand l'utilisateur demande un avis spécialisé."
tools: ["read", "search"]
---

# Dispatch de persona

Le projet embarque un jeu de personas Grimoire. Chacune porte un rôle, une
frontière d'outils et une mémoire propre. Les activer a un coût : un tour de
plus, un contexte à transmettre. Le dispatch se justifie quand la tâche demande
un point de vue, pas quand elle demande une exécution.

## Décider

| Situation | Action |
|---|---|
| Demande directe et bornée (« corrige ce test ») | traiter sans dispatch |
| Arbitrage de conception, compromis structurant | dispatcher l'architecte |
| Revue adverse, second avis sur une conclusion | dispatcher une persona distincte de celle qui a produit la conclusion |
| Travail long, isolable, à contexte volumineux | dispatcher pour isoler le contexte |
| Plusieurs volets indépendants | dispatcher en parallèle, un volet par persona |

Un second avis rendu par la persona qui a produit la première réponse n'est pas
un second avis.

## Inventaire

```bash
grimoire -o json status
```

Le tableau de bord donne le nombre de personas déployées. Leurs définitions
vivent dans `_grimoire/kit/agents/<nom>.md` et décrivent le rôle, le
protocole d'activation et les règles de chacune. `grimoire registry search
<mot-clé>` cherche dans le catalogue du kit ce que le projet n'a pas encore.

## Transmettre le contexte

Une persona activée ne voit pas la conversation. Lui fournir explicitement :

- l'objectif de la tâche et son `task_id`;
- les fichiers déjà lus ou modifiés, par chemin;
- ce qui a déjà été essayé et écarté, avec la raison;
- le format de retour attendu.

## Récupérer le résultat

Vérifier avant de reprendre à son compte ce qui revient : un chemin cité
existe-t-il, une commande annoncée verte l'est-elle. Une affirmation
invérifiable se signale comme telle plutôt que de se propager.
