# Connaissance de domaine — Jeu vidéo

Ce dossier bundle, de façon self-contained, les concepts jeu vidéo du corpus normatif agentique amont (projet `processus-developpement-agentique`). Il sert de **base de connaissance** consultable pour l'archétype `game-dev` et le pont `framework/game-dev/`.

> La source amont reste la **source de vérité normative**. Grimoire Kit consomme et trace ; il ne redéfinit pas la norme. Ces copies portent un en-tête de provenance.

## Contenu

| Fichier | Rôle |
| --- | --- |
| `guide-jeux-video.md` | Guide de domaine : cycle de vie, disciplines, règles normatives, arbre de décision. |
| `use-cases-jeux-video.md` | Cluster de use-cases UC-08 → UC-50 (GDD, contenu, équilibrage, playtest, déterminisme, build/cert, live ops, art, audio, réseau, etc.). |
| `catalogue-skills-jeux-video.md` | L'épicerie de skills : 29 rayons (A → AC), chaque skill avec format, palier modèle, contexte, réflexion et preuve. |
| `profils-genres-jeux-video.md` | Lentilles par genre : UC porteurs, rayons prioritaires, signature d'ambiance, pièges. |
| `matrice-capacites-modalites-jeux-video.md` | Routage par compétence et modalité (MOD-03) : texte/image/audio/3D/vidéo/humain. |
| `diagrammes/` | Sources Mermaid (patterns jeu vidéo, épicerie de skills). |

## Références au socle agentique

Les fiches use-cases et skills citent des **patterns socle** par identifiant (`KNO-*`, `QUA-*`, `GOV-*`, `COG-*`, `ORC-*`, `ORG-*`, `RUN-*`, `MOD-*`). Ces identifiants renvoient au corpus amont (familles de patterns du standard agentique) et à l'intégration `framework/agentic-standard/` du kit. Ils ne sont pas redéfinis ici.

## Comment l'utiliser

- Pour cadrer un projet : lire `guide-jeux-video.md`, puis la lentille de genre dans `profils-genres-jeux-video.md`.
- Pour composer une mission : ouvrir la fiche UC concernée dans `use-cases-jeux-video.md`.
- Pour choisir un skill par besoin : parcourir `catalogue-skills-jeux-video.md`.
- Pour router une tâche hors cœur LLM (art, audio, 3D) : appliquer `matrice-capacites-modalites-jeux-video.md`.
- La carte machine-readable qui relie genres → UC → rayons → règles est dans `../domain-map.yaml`.
