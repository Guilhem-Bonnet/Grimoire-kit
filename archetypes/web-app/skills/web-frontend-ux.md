<!-- ARCHETYPE: web-app — Adaptez les {{placeholders}} et exemples à votre framework frontend -->
---
description: "Construire ou faire évoluer un composant, un écran ou un parcours SPA : accessibilité WCAG 2.1 AA, performance UI (Core Web Vitals), design system. À utiliser quand la demande porte spécifiquement sur la couche frontend — pas pour une feature qui touche aussi l'API ou la base de données (voir fullstack-dev directement)."
tools: ["read", "edit", "execute"]
---

# Frontend & UX (ex-agent Pixel)

Ancien agent dédié (`frontend-specialist`, faisceau outils identique à `fullstack-dev` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `fullstack-dev` au lieu d'occuper un agent à part.

## Principes

- Accessibilité d'abord — WCAG 2.1 AA minimum sur tous les composants interactifs.
- Composant = une responsabilité, une interface (props typées), un test.
- Performance UI : < 100ms de First Input Delay, > 90 Lighthouse score.
- Design system : cohérence avant originalité.
- Mobile-first : styles de base pour mobile, media queries pour desktop.
- Pas de magic numbers : toutes les valeurs UI (couleurs, spacing, breakpoints) dans le design system ou variables CSS.

## Garde-fou

Composant > 150 lignes → proposer un découpage. Jamais de div cliquable sans rôle `button`. Jamais de valeur hex hardcodée dans un composant.

## Nouveau composant

Raisonnement : identifier le composant (nom, localisation dans `src/components/`) → définir le contrat props (types stricts, valeurs par défaut) → vérifier le design system (tokens existants) → implémenter (composant + accessibilité + styles) → écrire le test (render + interaction + snapshot si visuel stable) → CC VERIFY `--stack ts`.

Checklist minimum : props typées (pas de `any`), valeurs par défaut sur props optionnelles, textes alternatifs (`alt=""`), labels sur tous les inputs, contraste WCAG AA (ratio ≥ 4.5:1), navigation clavier fonctionnelle, test d'accessibilité (jest-axe/vitest-axe).

## Revue UX

Analyse en 4 dimensions : clarté (objectif immédiatement compris), feedback (retours visuels sur les actions), cohérence (patterns UI alignés avec les autres écrans), accessibilité (clavier, lecteurs d'écran, contrastes). Noter chacune 1-5 avec exemples concrets, recommandations priorisées (effort S/M/L).

## Audit accessibilité WCAG 2.1 AA

Perceivable (alternatives textuelles, contrastes ≥4.5:1 texte / ≥3:1 UI), Operable (navigation clavier, pas de trap, focus visible, pas de flash), Understandable (labels formulaires, messages d'erreur explicites, langue définie), Robust (HTML sémantique, ARIA correct). Lister les violations avec composant + critère WCAG + correction recommandée ; suggérer jest-axe pour les tests automatisés.

## Audit performance (Core Web Vitals)

Cibles : LCP < 2.5s, FID/INP < 200ms, CLS < 0.1.

Vérifications : bundle size (dépendances lourdes), code splitting (lazy `import()` sur pages/composants lourds), images (format optimisé, `width`/`height`, `loading="lazy"`), fonts (`font-display: swap`, preload), re-renders (`useMemo`/`useCallback` sur calculs coûteux uniquement).

Produire une liste hiérarchisée avec impact estimé (High/Medium/Low).
