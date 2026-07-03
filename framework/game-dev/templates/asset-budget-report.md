# Asset Budget Report

> Vérification d'un asset contre ses budgets et conventions avant intégration (UC-12, règle GR-07).

## Identité

- Asset :
- Type : `mesh 3D | texture | matériau | VFX | audio | animation`
- Cible (plateforme / contexte) :
- Date :

## Budgets

| Métrique | Budget | Mesuré | Verdict |
|---|---|---|---|
| Polycount / tris |  |  | `ok | dépassé` |
| Résolution texture |  |  | `ok | dépassé` |
| Taille mémoire |  |  | `ok | dépassé` |
| Coût GPU (ms) |  |  | `ok | dépassé` |
| Coût mémoire audio |  |  | `ok | dépassé` |

## Conventions

| Critère | Résultat | Notes |
|---|---|---|
| Naming |  | `pass | fail` |
| Format / espace couleur |  | `pass | fail` |
| Références (pas d'orphelin) |  | `pass | fail` |
| Pivot / échelle / origine |  | `pass | fail` |

## Provenance & licence

- Source : `procédural | DCC | modèle spécialisé | banque licenciée`
- Licence vérifiée : `oui | non`

## Verdict

- Intégration : `autorisée | bloquée`

---
Trace amont : `knowledge/use-cases-jeux-video.md#uc-12` · socle : GOV-08, QUA-14.
