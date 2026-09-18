<!-- EXPERTISE: architecture-haut-niveau — Adaptez à votre projet. -->
---
description: "Structurer un système avant d'écrire du code à l'intérieur d'un module — bounded contexts DDD, ports/adapters, événements de domaine, découpage justifié. À utiliser pour une décision de structure (nouveau module, nouveau service, frontière entre composants) — pas pour l'implémentation d'une fonction ni pour le style de code interne à un fichier."
tools: ["read", "edit", "execute"]
---

# Architecture haut niveau

Expertise ciblée sur la structure d'un système — les frontières entre modules, services et couches — avant de descendre dans le code d'un module donné. Chargée quand l'utilisateur a explicitement besoin de ce niveau de raisonnement, pas par défaut.

## Principes

- Le domaine ne référence jamais un détail d'infrastructure (SQL, HTTP, un SDK cloud, un framework web) — seulement des ports qu'il définit lui-même ; l'infrastructure implémente ces ports, jamais l'inverse.
- Un bounded context porte son propre langage ubiquitaire — un même mot (« commande », « client ») peut désigner des concepts différents dans deux contextes ; ne pas forcer un modèle unique partagé entre eux.
- Les agrégats protègent leurs invariants par construction — aucune mutation d'état ne doit pouvoir laisser un agrégat dans un état incohérent, même via un chemin détourné.
- Un modèle de domaine anémique (des structs de données + toute la logique dans des services externes) est un signal d'alerte, pas une architecture — la logique métier vit avec les données qu'elle protège.
- Un événement de domaine se justifie quand « quelque chose s'est produit, plusieurs éléments peuvent réagir, et l'émetteur n'a pas à savoir lesquels » — ce n'est pas un défaut par défaut, chaque événement introduit du découplage au prix de la traçabilité et du raisonnement séquentiel.
- Un monolithe aux frontières internes propres bat un découpage en microservices sans frontières claires — le découpage en services suit un bounded context déjà prouvé, jamais une supposition.

## Garde-fou

Introduire un nouveau découpage de bounded context (nouveau service, nouvelle base de données séparée) ou un bus d'événements est une porte architecturale à sens unique — nommer explicitement le couplage que ce choix supprime et le coût opérationnel qu'il ajoute (déploiement, observabilité, cohérence éventuelle) avant de l'exécuter, et demander confirmation si le bénéfice n'est pas déjà mesuré ou vécu.

## Structurer un nouveau module

Nommer le bounded context et son langage ubiquitaire → définir les ports dont le domaine a besoin (interfaces, pas d'implémentation) → écrire la logique de domaine contre ces ports uniquement, sans importer d'infrastructure → câbler les adapters en dernier (persistance, API, messagerie) → vérifier qu'aucun import du domaine ne pointe vers un adapter.

## Revoir une architecture existante

Tracer une requête de bout en bout, en nommant chaque couche qu'elle traverse (entrée, application, domaine, infrastructure) → repérer tout raccourci adapter-à-adapter qui contourne le domaine (par exemple un handler HTTP qui appelle directement une requête SQL) → identifier les agrégats dont l'invariant peut être violé depuis l'extérieur → vérifier que chaque événement de domaine a au moins un consommateur réel, sinon le signaler comme dette.

## Ajouter un événement de domaine

Vérifier qu'un appel direct ne suffit pas (plusieurs réactions indépendantes, émetteur qui ne doit pas connaître les consommateurs) → nommer l'événement au passé dans le langage ubiquitaire du contexte → définir son contrat de données minimal (pas l'agrégat entier) → documenter le ou les consommateurs attendus avant de l'émettre en production.

## Checklist de revue

- Vérifier qu'aucun fichier de domaine n'importe une bibliothèque d'infrastructure.
- Vérifier que chaque port a au moins une implémentation testée et au moins un consommateur.
- Repérer les modèles anémiques : classes de données sans comportement, logique dispersée dans des services.
- Vérifier que tout événement de domaine a un contrat versionné et un consommateur identifié.
- Confirmer qu'un découpage en service séparé s'appuie sur un bounded context déjà stable, pas sur une anticipation.
