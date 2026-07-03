# Shared Context — $project_name

> Contexte partagé entre tous les agents du studio de jeu.
> Charger ce fichier au démarrage de chaque agent.
> **Remplis les sections marquées ✏️ — le GDD reste la source de vérité.**

---

## 🎮 Identité du jeu

| Élément | Valeur |
|---|---|
| Nom du projet | $project_name |
| Genre(s) / lentille(s) | ✏️ _à compléter_ |
| Plateforme(s) cible(s) | ✏️ _à compléter_ |
| Classification d'âge visée | ✏️ _à compléter_ (ESRB/PEGI/IARC) |
| Moteur | ✏️ _à compléter_ (Godot / Unity / Unreal / custom) |
| Fantasy joueur (1 phrase) | ✏️ _à compléter_ |

<!-- GENRE-LENS:START -->
## 🎯 Lentille de genre

> Section injectée par `grimoire-init.sh` selon le genre choisi (UC porteurs, rayons prioritaires, signature d'ambiance, pièges). Référence : `framework/game-dev/domain-map.yaml#genre_lenses`.

✏️ _à compléter_ — voir `framework/game-dev/knowledge/profils-genres-jeux-video.md`.
<!-- GENRE-LENS:END -->

## 📜 Règles normatives du domaine (invariants)

1. **GR-01 — GDD source de vérité.** Tout contenu se rattache au GDD ; une divergence est un défaut.
2. **GR-02 — Aucun contenu hors classification d'âge.** Scan avant intégration et soumission.
3. **GR-03 — Simulation déterministe.** Seed + pas fixe + hash d'état pour tout test rejouable.
4. **GR-04 — Certification = gate de preuve.** Pas de soumission sans cert dry-run vert.
5. **GR-05 — Pas d'ajustement live sans télémétrie ni canary.**
6. **GR-06 — Pas de changement d'équilibrage sans non-régression.**
7. **GR-07 — Gate de validation de contenu avant merge.**
8. **GR-08 — Build reproductible, jalon prouvé.**
9. **GR-09 — Tout patch live est rejouable ou compensable.**

## 🧭 Routage par modalité (MOD-03)

Hors du cœur LLM texte (illustration, audio, modélisation 3D, vidéo) : **router** vers la cible capable (modèle spécialisé, outil/DCC, humain) et produire un Capability Routing Record. Ne jamais livrer un asset final hors maîtrise. Référence : `framework/game-dev/knowledge/matrice-capacites-modalites-jeux-video.md`.

## 👥 Disciplines (agents)

| Agent | Rôle |
|---|---|
| game-designer | Gardien du GDD, décomposition des mécaniques |
| narrative-designer | Cohérence du lore, narration interactive |
| level-designer | Plan et validation de niveaux |
| gameplay-programmer | Comportements + harnais déterministe |
| systems-economist | Équilibrage et économie simulés |
| tech-artist | Pipeline d'assets, budgets, routage modalité |
| game-qa | Playtest agentique, préparation certification |
| liveops-analyst | Télémétrie, déploiement canary/rollback |

## 📦 Preuves attendues (templates)

GDD · Content Validation Record · Balance Regression Evidence · Playtest Evidence Pack · Determinism/Replay Record · Certification Record · Telemetry Decision Record · Capability Routing Record · Asset Budget Report. Source : `framework/game-dev/templates/`.

## Conventions

- Langue de communication : $language
- Le GDD (`docs/GDD.md`) est la source de vérité — pas de contenu orphelin.
- Toute simulation testée est déterministe (seed loggé).
- Aucun asset sans licence/provenance vérifiée.
- Aucun jalon déclaré sans preuve attachée.
