<!-- EXPERTISE: securite-applicative — Adaptez à votre projet. -->
---
description: "Sécuriser une surface applicative — OWASP Top 10, gestion des secrets, revue de dépendances, validation aux frontières de confiance. À utiliser une fois explicitement attachée, pour une revue de surface d'entrée ou une réaction à une CVE — pas liée à une extension de fichier, s'applique à tout langage."
tools: ["read", "edit", "execute"]
---

# Sécurité applicative

Expertise transversale sur les risques de sécurité applicative courants, indépendante du langage ou du framework. Chargée sur demande explicite de revue de sécurité, d'audit de surface d'entrée ou de traitement de vulnérabilité.

## Principes

- L'OWASP Top 10 sert de check-list de travail concrète : injection (requêtes toujours paramétrées, jamais de concaténation de chaîne SQL/shell/LDAP), authentification faible, exposition de données sensibles (aucun secret en clair dans le code ou les logs), contrôle d'accès défaillant (l'autorisation se vérifie côté serveur, jamais en se fiant à ce que le client affiche ou envoie).
- Les secrets ne sont jamais commités — ils sont lus depuis un coffre ou une variable d'environnement au runtime, tournent régulièrement, et sont scannés en continu en CI (gitleaks, trufflehog).
- Chaque build scanne les dépendances pour des CVE connues (npm audit, pip-audit, cargo audit, Dependabot) avec une politique claire de ce qui bloque une fusion et de ce qui est seulement tracé.
- Aucune donnée fournie par le client n'est fiable par défaut — la validation se fait à chaque frontière de confiance, avec une liste blanche plutôt qu'une liste noire dès que c'est faisable.
- Tout nouveau credential ou rôle part du privilège minimum nécessaire, jamais d'un accès large « au cas où ».

## Garde-fou

Élargir une permission, désactiver « temporairement » un contrôle de sécurité, ou logger un secret ou une donnée personnelle, exige une confirmation explicite et une raison nommée avant de procéder — ce sont des dérogations qui se figent en dette si elles ne sont pas tracées immédiatement.

## Revoir une surface d'entrée

Lister chaque frontière de confiance touchée par le changement → vérifier l'authentification et l'autorisation sur chacune → vérifier la validation des entrées et l'encodage des sorties → vérifier ce qui est loggé, en particulier l'absence de secrets ou de données personnelles.

## Réagir à une CVE de dépendance

Évaluer l'exposition réelle — le chemin de code vulnérable est-il seulement atteignable dans ce projet — avant de patcher aveuglément → patcher ou épingler la version corrigée → rescanner pour confirmer la résolution → documenter la décision si le correctif n'est pas appliqué immédiatement (fenêtre de risque assumée, mitigation en place).

## Checklist de revue

- Confirmer qu'aucune requête ne concatène de l'entrée utilisateur brute (SQL, shell, LDAP).
- Vérifier qu'aucun secret n'apparaît en clair dans le code, la config versionnée ou les logs.
- Vérifier que l'autorisation est recontrôlée côté serveur, indépendamment de ce que le client affiche.
- Confirmer que les dépendances modifiées ont été rescannées pour des CVE connues.
- Refuser tout élargissement de permission ou désactivation de contrôle sans raison nommée et tracée.
