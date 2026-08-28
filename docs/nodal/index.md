# Pourquoi dessiner un flow plutôt que le prompter

Vous savez déjà faire travailler une IA dans votre éditeur. Vous décrivez une
tâche, elle produit quelque chose, vous relisez, vous relancez. Ça marche, et
pour beaucoup de travail c'est suffisant. Cette page n'essaie pas de vous en
détourner : elle montre ce qu'un flow dessiné fait de plus, et ce qu'il coûte.

## La même tâche, deux fois

Prenons une tâche banale : **ajouter un endpoint à une API, et ne le livrer que
s'il est réellement testé.**

=== "Au prompt"

    Vous écrivez la demande, l'agent produit le code et les tests, vous
    relisez, vous demandez une correction, il corrige. Au bout de trois
    échanges, c'est bon.

    Ce que vous avez à la fin : du code, et votre mémoire de la conversation.

    Ce que vous n'avez pas :

    - **la même chose demain.** Relancé, le même prompt donnera un résultat
      voisin, pas identique. Vous ne pouvez pas le rejouer, seulement le
      refaire.
    - **une garantie.** Rien n'a empêché la livraison si les tests
      échouaient — vous avez regardé, et c'est vous qui avez tenu la porte.
    - **une trace exploitable.** Ce qui a été vérifié vit dans un fil de
      discussion, pas dans un fichier que la CI peut lire.
    - **un plafond.** L'échange aurait pu durer dix tours au lieu de trois.
      Rien ne l'aurait arrêté.

=== "En blueprint"

    Vous dessinez trois nodes : un qui cadre la mission, une **porte** qui
    exige une preuve, un qui vérifie. Vous les reliez. Vous compilez.

    ```json
    { "from": "govern.out", "to": "verify.in", "contract": "task-envelope" }
    ```

    Ce que vous avez à la fin :

    - **un fichier**, `mon-premier.blueprint.json`, versionné avec le code.
    - **un mission pack compilé**, avec son empreinte `sha256`. Deux
      compilations du même fichier donnent le même pack.
    - **une porte qui bloque.** Tant que la preuve exigée n'est pas là, rien
      ne passe. Ce n'est pas une convention, c'est un refus.
    - **des erreurs avant l'exécution.** Un contrat qui ne correspond pas est
      détecté à la validation, pas après avoir payé des jetons pour le
      découvrir.

    Ce que ça coûte : une demi-heure pour comprendre le vocabulaire, un fichier
    de plus à maintenir, et l'obligation de dire explicitement ce que vous
    attendiez implicitement.

## Le vrai partage

Un blueprint n'est pas un meilleur prompt. C'est un objet différent :

| Le prompt | Le blueprint |
| --- | --- |
| décrit une intention | déclare une structure |
| se relit | se **valide** |
| se refait | se **rejoue** |
| échoue quand vous vous en apercevez | échoue à la porte, sans vous |
| vit dans une conversation | vit dans le dépôt |

La question utile n'est donc pas « lequel est le meilleur ? » mais « cette
tâche mérite-t-elle d'être rendue reproductible ? ». Pour une exploration, non.
Pour ce que votre équipe refera chaque semaine, ou pour ce dont vous devrez
prouver le déroulement, oui.

## Ce que le graphe ne fait pas

Le Studio **n'exécute rien**. Il valide, il simule, il compile — et c'est tout.
L'exécution appartient au runtime, et passe par ses portes.

Ce n'est pas une limite qu'on espère lever un jour : c'est ce qui rend un flow
vérifiable avant d'avoir coûté quoi que ce soit. Voir
[Ce que le graphe fait, et ce qu'il ne fait pas](concepts/execution.md).

## Par où commencer

<div class="grid cards" markdown>

- **[Votre premier blueprint](premier-blueprint.md)**

    Un quart d'heure, de `pip install` au mission pack compilé. On casse
    volontairement un contrat en chemin — c'est la façon la plus rapide de
    comprendre le typage.

- **[Comprendre](concepts/execution.md)**

    Cinq pages sur ce qui fait la mécanique : l'exécution, les contrats, les
    canaux, les portes, les sept primitives.

- **[La référence](reference/index.md)**

    Tout ce qu'un blueprint peut contenir : 7 rôles, 30 contrats, 78 patterns,
    le format du fichier. Rendue depuis les sources.

</div>

## Le vocabulaire minimal

Cinq mots suffisent pour lire n'importe quel blueprint :

- Un **node** a un `kind` — d'où il vient — et un `role` — ce qu'il fait. Les
  deux sont indépendants.
- Un **pin** est un point de connexion typé : une direction, un contrat.
- Une **edge** relie deux pins qui portent **exactement** le même contrat, sur
  l'un des trois canaux.
- Une **porte** affirme une précondition et gouverne le passage. Elle ne
  transforme rien.
- Un **pattern** est une pratique normée du catalogue, désignée par son id
  (`ORC-02`, `QUA-04`).

Pour ouvrir l'atelier sur votre projet, voir
[Atelier local & blueprints](../serve-blueprints.md).
