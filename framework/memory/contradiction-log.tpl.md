# Log des Contradictions — {{project_name}}

> Mis à jour automatiquement par `maintenance.py memory-audit` et par les agents via `grimoire memory`.
> Une contradiction = deux mémoires incompatibles sur le même sujet.
> Résolution = archiver l'ancienne, conserver la plus récente (sauf décision architecturale).

## Contradictions Actives

| Date | Agent source | Ancienne mémoire | Nouvelle mémoire | Statut | Résolution |
|------|-------------|-----------------|-----------------|--------|-----------|
| — | — | — | — | — | — |

## Contradictions Résolues

| Date détection | Date résolution | Agent | Sujet | Action |
|---------------|----------------|-------|-------|--------|
| — | — | — | — | — |

## Format lu par `grimoire memory lint`

Seule une ligne de liste commençant par `Resolved:` (préfixe `[date ISO]` obligatoire pour couvrir
des entrées datées) marque une paire de mémoires comme résolue. Les tableaux ci-dessus sont lus
par les humains et par `memory-audit`, pas par `memory lint`. La date doit être égale ou postérieure
aux deux entrées contradictoires : une contradiction qui réapparaît plus tard est de nouveau signalée.

- [YYYY-MM-DD] Resolved: <sujet commun aux deux mémoires> ; <décision retenue>

---

## Règles de résolution

1. **Plus récent gagne** — sauf pour les décisions architecturales (marquées `[ARCH]` dans decisions-log.md)
2. **Mnemo décide** — toute résolution de contradiction passe par Mnemo (memory-keeper)
3. **Traçabilité** — chaque résolution est loguée ici avec l'action prise
4. **Escalade** — si la contradiction implique une décision architecturale → Mnemo → Atlas → utilisateur

## Format d'une entrée

```
| 2026-02-23 | gopher | "SQLite in-memory pour les tests" | "SQLite fichier :memory: pour les tests" | ✅ résolu | Mémoire #42 archivée — les deux disent la même chose différemment |
| 2026-02-22 | sakura | "Theme: dark mode via CSS var --bg-primary" | "Theme: dark mode via Tailwind dark: prefix" | escaladé | Décision architecturale → Atlas consulté |
```

**Statuts** : `⏳ actif` | `✅ résolu` | `escaladé` | `🗃️ archivé`
