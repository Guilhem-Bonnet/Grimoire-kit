# Bras « activé » — mécanisme d'activation pré-enregistré

Pré-enregistré le 2026-07-09, avant toute exécution du bras `activated`.
Répond à la recommandation 1 du rapport `evals/reports/2026-07-03/report.md` :
la campagne 2026-07-03 a mesuré la **présence passive** des artefacts du
standard (zéro engagement sur 40 runs governed) ; ce bras mesure l'**usage
dirigé**.

## Mécanisme

Le bras `activated` est identique au bras `governed` (mêmes pins, même
enrôlement `grimoire init` + `grimoire standard init --needs solo-prototyping`),
plus un **hook SessionStart Claude Code** installé dans le répertoire du run :

- `.claude/settings.json` déclare un hook `SessionStart` de type `command`.
- Le hook émet sur stdout le contenu de `.claude/activation-context.md`,
  injecté comme contexte au démarrage de la session.
- Le prompt de tâche reste le champ `prompt` exact du YAML — **identique aux
  deux autres bras**. Seul le contexte de session diffère.

## Directive injectée (verbatim)

```text
[Grimoire Standard — activation]
Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font
partie de la tâche demandée :
1. AVANT toute modification de code : remplis
   `_grimoire-output/evidence/bootstrap/task-envelope.md` — objectif,
   périmètre outillé (tool boundary) concret, critères de sortie.
2. PENDANT le travail : consigne chaque preuve (commande exécutée, test
   vert, diff clé) comme ligne concrète de l'inventaire dans
   `_grimoire-output/evidence/bootstrap/evidence-pack.md`, et remplace le
   résumé placeholder.
3. AVANT de conclure : exécute
   `grimoire standard gate check --task-id bootstrap --strict` puis
   `grimoire standard verify .` et corrige tout échec.
Une clôture sans gates verts est une tâche non terminée.
```

## Justification des choix

- **Hook plutôt que consigne dans le prompt** : garde le prompt de tâche
  strictement identique entre bras ; la variable expérimentale est le
  mécanisme d'activation, pas la formulation de la demande.
- **Enveloppe `bootstrap` existante** plutôt que création d'une nouvelle
  enveloppe : surface déterministe (les fichiers existent après
  `standard init`), pas de dépendance à une capacité de scaffolding non
  documentée pour l'agent.
- **`--strict`** : les échecs de gates produisent un exit code non nul,
  signal exploitable par l'agent.

## Métriques d'engagement (nouvelles, ce bras)

Par run, collectées depuis les artefacts et la session :

- `envelope_filled` : task-envelope.md modifié et sans placeholder.
- `evidence_rows` : nombre de lignes concrètes de l'inventaire.
- `gate_check_invoked` : la session contient un appel `gate check`.
- `verify_ok` : `grimoire standard verify` sans erreur en fin de run.

## Comptage des régressions (pré-enregistré, recommandation 2)

- **Critère principal** : règle identique à la campagne 2026-07-03 (toute
  modification d'un test baseline sur tâche refactor = régression), pour
  comparabilité directe avec les bras `governed` et `baseline`.
- **Critère secondaire pré-enregistré** : distinction « cassé/supprimé »
  (build ou test rouge, test supprimé) vs « adapté vert » (test modifié
  restant vert). Les deux comptes sont rapportés.

## Paramètres figés

| Paramètre | Valeur |
|---|---|
| Baseline (pin) | `d1037105d9d4dee866d6281905b3b7ddfe6b58a2` — identique |
| Kit | grimoire-kit 3.18.0 via `evals/.venv` (le CLI global a évolué vers 3.22.0 ; le PATH du run préfixe le venv pinné) |
| Runner | Claude Code CLI 2.1.101 (identique), `claude -p`, `--max-turns 100`, timeout 1800 s |
| Modèle | `claude-sonnet-4-6`, température défaut |
| Tâches | les 8 tâches du YAML, 5 répétitions, bras `activated` uniquement (40 runs) |
| Jugement | grille `JUDGING.md` inchangée, appliquée strictement |

## Critère de succès du bras

Le mécanisme d'activation est déclaré **fonctionnel** si ≥ 80 % des runs
ont `gate_check_invoked` (l'inverse du zéro engagement observé).
L'**utilité** du standard activé est évaluée avec le même critère que la
campagne 2026-07-03 (régressions −30 % relatif vs baseline, sans baisse de
complétion ni surcoût > 25 %), en comparant `activated` au bras `baseline`
de la campagne 2026-07-03 (mêmes pins, même runner, même modèle).
