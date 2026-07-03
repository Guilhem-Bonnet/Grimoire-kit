# Capability Routing Record

> Trace de routage par compétence et modalité (MOD-03). Quand une tâche sort du cœur LLM (image, audio, 3D, vidéo), router vers la cible capable plutôt que produire un livrable médiocre.

## Identité

- Besoin / tâche :
- Date :

## Décision de routage

| Aspect | Valeur |
|---|---|
| Modalité dominante | `texte | code | image | audio | 3D | vidéo` |
| Cœur de compétence LLM ? | `oui | non` |
| Cible retenue | `LLM frontier | LLM standard | LLM économique | modèle spécialisé | outil/DCC | humain` |
| Justification |  |
| Repli déclaré |  |

## Limite assumée (si hors cœur)

- Ce que l'agent ne tente PAS de produire seul :
- Escalade : `GOV-15 humain | RUN-15 acquisition d'outil`
- Statut : `routé | en attente d'outil | escaladé`

## Preuve produite

- Artefact / sortie :
- Validé par :

---
Trace amont : `knowledge/matrice-capacites-modalites-jeux-video.md` · socle : MOD-03, GOV-15, RUN-15.
