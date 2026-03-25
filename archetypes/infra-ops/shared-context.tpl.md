# Contexte Partagé — $project_name

<!-- ARCHETYPE: infra-ops — Template de shared-context pour infrastructure.
     Les sections marquées ✏️ sont à compléter par l'utilisateur.
     Les variables $xxx sont auto-substituées par grimoire init. -->

> Ce fichier est chargé par tous les agents au démarrage.
> Il est la source de vérité pour le contexte projet.
> **Remplis les sections marquées ✏️ — c'est la chose la plus utile que tu puisses faire.**

## Projet

- **Nom** : $project_name
- **Description** : ✏️ _à compléter_
- **Type** : $project_type
- **Stack** : $stack_list

## Infrastructure ✏️

<!-- Adaptez cette section à votre environnement -->

| Hôte | IP | Rôle | Services |
|------|----|------|----------|
| ✏️ | ✏️ | ✏️ | ✏️ |

**Réseau** : ✏️ _à compléter_ _(ex: 192.168.1.0/24)_

## Conventions

- Langue de communication : $language
- Toutes les décisions sont loggées dans `decisions-log.md`
