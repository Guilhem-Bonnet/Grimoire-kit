# 🏛️ Failure Museum — {{project_name}}

> **Principe** : Chaque échec instruit. Ce document capture les erreurs les plus significatives
> du projet pour que chaque agent les consulte au démarrage et apprenne AVANT de répéter.
>
> **Mise à jour** : Automatiquement par `sil-collect.sh` + manuellement par tout agent
> après un incident non-trivial.
>
> **Usage** : Les agents consultent ce fichier en step 2 d'activation (LAZY-LOAD si projet actif > 2 semaines).
>
> Format : `[YYYY-MM-DD] [agent] [category] [description courte]`
> Catégories : `CC-FAIL` | `WRONG-ASSUMPTION` | `CONTEXT-LOSS` | `HALLUCINATION` | `ARCH-MISTAKE` | `PROCESS-SKIP`

---

## 🔴 Top Erreurs Critiques (à lire ABSOLUMENT)

> Ces erreurs ont causé du revert, de la perte de travail, ou bloqué l'équipe.

<!-- Exemple :
### [2026-02-10] [dev] CC-FAIL — Tests non exécutés avant "terminé"
**Ce qui s'est passé** : L'agent a déclaré l'implémentation terminée sans lancer `go test ./...`
**Cause racine** : Pression pour terminer vite, skip du CC
**Impact** : 3 tests en échec découverts par QA, revert nécessaire
**Leçon** : TOUJOURS exécuter le CC, même pour "un petit changement"
**Règle instaurée** : Le mot "terminé" est maintenant interdit sans output CC PASS affiché
-->

*(Aucune erreur critique enregistrée — remplir dès le premier incident)*

---

## 🟡 Erreurs Importantes (à connaître)

> Ces erreurs ont causé du retard ou nécessité du rework.

<!-- Exemple :
### [2026-02-15] [architect] WRONG-ASSUMPTION — Stack détecté comme monolith alors que microservices
**Ce qui s'est passé** : L'agent a supposé une architecture monolith sans lire project-context.yaml
**Cause racine** : Context pas chargé en step 2
**Leçon** : TOUJOURS lire project-context.yaml avant toute décision architecturale
-->

*(Aucune erreur importante enregistrée)*

---

## 🟢 Micro-Erreurs (signaux faibles)

> Petites frictions récurrentes qui méritent attention.

*(Aucune micro-erreur enregistrée)*

---

## 📊 Statistiques

| Catégorie | Occurrences | Dernière date |
|---|---|---|
| CC-FAIL | 0 | — |
| WRONG-ASSUMPTION | 0 | — |
| CONTEXT-LOSS | 0 | — |
| HALLUCINATION | 0 | — |
| ARCH-MISTAKE | 0 | — |
| PROCESS-SKIP | 0 | — |

---

## 🛠️ Ajouter une entrée

```markdown
### [YYYY-MM-DD] [agent] [category] — [titre court]
**Ce qui s'est passé** : description factuelle en 2-3 phrases
**Cause racine** : pourquoi c'est arrivé (pas de jugement — just facts)
**Impact** : temps perdu, revert, blocage équipe...
**Leçon** : ce qu'on fait différemment maintenant
**Règle instaurée** : (si applicable) modification dans agent-base.md ou workflow
```

---

*Template Grimoire Custom Kit — BM-03 Failure Museum | framework/memory/failure-museum.tpl.md*
*Initialisé le : {{init_date}} — Projet : {{project_name}}*
