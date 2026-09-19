<!-- EXPERTISE: expertise-design-patterns — Adaptez à votre projet. -->
---
description: "Choisir, justifier ou remettre en cause un pattern de conception lors d'une décision de structure ou d'architecture — GoF, Repository/Adapter/CQRS, anti-patterns. S'applique dès qu'une décision de design est prise ou revue, quel que soit le langage ou l'extension de fichier — pas un remplacement de la revue de code générale."
tools: ["read", "edit", "execute"]
---

# Design Patterns

Expertise transversale, non liée à un langage : elle s'active quand une décision de structure ou d'architecture est en jeu (introduire une abstraction, choisir entre deux façons d'organiser un module, réviser une conception existante), pas sur détection d'une extension de fichier.

## Principes

- Un pattern est le nom d'une solution à un problème récurrent déjà identifié — jamais l'inverse : on ne part pas d'un pattern pour lui trouver un problème a posteriori.
- Créationnels (résoudre "comment construire") : Factory Method quand la logique de construction elle-même varie selon le contexte d'appel ; Builder quand la construction a des étapes optionnelles ou un ordre qui compte ; Singleton seulement pour une ressource véritablement unique au process (pas comme raccourci vers un état global).
- Structurels (résoudre "comment composer") : Adapter à une frontière avec un système externe dont on ne contrôle pas l'interface ; Decorator quand des comportements additionnels doivent se combiner sans exploser en sous-classes ; Facade pour simplifier un sous-système complexe côté appelant sans le modifier.
- Comportementaux (résoudre "comment interagir") : Strategy quand un comportement varie indépendamment du client qui l'invoque ; Observer quand un changement d'état doit se propager à des auditeurs inconnus ou variables au moment de la conception ; Command quand une action doit être mise en file, journalisée ou annulée.
- Patterns d'intégration/architecture au-delà du GoF : Repository quand la persistance doit rester substituable derrière une collection en mémoire ; Adapter en frontière de bounded context ou de service externe ; CQRS seulement quand les modèles de lecture et d'écriture divergent réellement (schémas différents, charges différentes) — pas par anticipation.
- Chaque pattern introduit une indirection qui a un coût de lecture ; ce coût doit être plus petit que le problème qu'il résout, sinon c'est une régression déguisée en bonne pratique.

## Garde-fou

Refuser d'introduire un pattern qui ajoute de l'indirection sans un problème nommé et présent dans le code actuel (pas hypothétique, pas "au cas où") — si la justification est "ça pourrait servir plus tard" ou "c'est plus propre", demander confirmation avant d'appliquer, et documenter explicitement le problème réel s'il existe.

## Choisir un pattern

Nommer les forces en présence (qu'est-ce qui varie, qui doit rester découplé de quoi, qu'est-ce qui doit être substituable) → vérifier qu'une solution plus simple (fonction, paramètre, if/else direct) ne couvre pas déjà ces forces sans abstraction supplémentaire → choisir le pattern dont la condition de déclenchement correspond exactement, pas le plus proche par familiarité → documenter le choix dans un commentaire au point d'introduction ou dans une ADR courte (quel problème, pourquoi ce pattern, quelle alternative écartée).

## Revoir une décision de conception

Repérer chaque abstraction introduite (interface à implémentation unique, hiérarchie de classes, indirection de fabrique) → pour chacune, demander si elle gagne son coût aujourd'hui ou si elle existe parce qu'un tutoriel ou une habitude l'a suggérée → si le problème qu'elle résout n'est pas identifiable dans le code actuel, proposer sa suppression plutôt que sa justification a posteriori → vérifier que le nom du pattern utilisé correspond à son usage réel (un "Factory" qui ne fait qu'un `new` déguisé n'est pas un Factory Method).

## Anti-patterns à signaler en revue

- God Object : une classe qui accumule des responsabilités non liées parce que c'est le point d'entrée le plus pratique — signal que le découpage par responsabilité a été reporté, pas résolu.
- Singleton-as-global-state : un singleton utilisé pour partager un état mutable entre composants plutôt que pour garantir une instance unique d'une ressource — réintroduit un couplage global sous couvert de pattern.
- Pattern pour le pattern (overengineering) : une hiérarchie de fabriques abstraites pour un script de 20 lignes, une interface à implémentation unique sans second cas d'usage prévu et daté.
- Pattern mal nommé : le code affiche le vocabulaire GoF (`*Factory`, `*Strategy`, `*Manager`) sans respecter le contrat du pattern — le nom crée une fausse attente pour le prochain lecteur.

## Checklist de revue

- Chaque abstraction introduite est adossée à un problème nommé, présent dans le code, pas anticipé.
- Le pattern choisi correspond à sa condition de déclenchement réelle, pas à la familiarité de l'auteur avec ce nom.
- Le choix est documenté (commentaire ou ADR courte) pour le prochain lecteur, surtout s'il écarte une alternative plus simple.
- Aucun God Object ni singleton porteur d'état mutable global introduit sans discussion explicite.
- Le nom du pattern dans le code (classe, méthode) correspond à son comportement réel.
