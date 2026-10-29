---
audience: contributeur
---

# Captures P47 — panneau « Assigner une session »

`assigner-panneau.png` : cockpit 8420, kit 3.73.0, tâche prête `GAO-p45-premier-001`, Playwright 1440 × 900, le 2026-10-28.

Ce qu'elle montre, critère par critère de `GAO-p47-assigne-001` :

1. le modèle calculé depuis la classe (V2, raison citée), le worktree et la branche, et la commande `claude --bg` équivalente ;
2. les huit vérifications avant lancement, dont « Admission » et « Dépendances ouvertes » bloquantes avec leur raison ;
3. « Lancer la session » non actionnable tant qu'un contrôle bloque (bandeau « Pas prêt ») ; rien n'a été cliqué.

Validée par Claude sous délégation explicite de Guilhem le 2026-10-28 (« tu as le droit d'approuver tout en mon nom »), comme consigné sur la PR #1033.
