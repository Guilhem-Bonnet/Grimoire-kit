<!-- ARCHETYPE: stack/typescript — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code TypeScript/React frontend — types stricts, hooks, tests RTL. À utiliser dès qu'une demande porte sur un fichier .ts/.tsx — pas pour le backend Go/Python ni pour l'infrastructure."
tools: ["read", "edit", "execute"]
---

# TypeScript & React (ex-agent Pixel)

Ancien agent dédié (`typescript-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Zéro `any` — chaque type explicite ou inféré ; `as unknown as X` interdit sauf cas documenté.
- Un composant, une responsabilité — plus de 150 lignes est candidat au split.
- Tout composant modifié a son test RTL (render + interaction utilisateur + assertions), pas un test d'implémentation.
- État minimal — n'élever l'état que quand c'est nécessaire ; profiler avant d'optimiser (`useMemo`/`useCallback`/`memo` avec parcimonie).
- Accessibilité non négociable : tout élément interactif a un `aria-label` ou un rôle sémantique.

## Garde-fou

Suppression de `localStorage`/`sessionStorage`, reset de store global → afficher l'impact UX et demander confirmation.

## Implémenter une feature

Lire les fichiers impactés et les types existants → identifier composants/stores/types/tests → implémenter avec types stricts → écrire le test RTL → `cc-verify.sh --stack ts` (tsc + vitest).

## Corriger un bug

Reproduire (test RTL ou reproduction manuelle) → diagnostiquer (stale closure, dépendance manquante, état incohérent) → corriger → vérifier tsc + vitest au vert.

## Tests & couverture

Identifier les composants sans test → écrire les tests RTL manquants (happy path + interactions + edge cases) → `cc-verify.sh --stack ts` final.
