# Bras « activated » — mécanisme d'activation du standard

Artefacts du troisième bras du protocole d'évaluation (v2, 2026-07-12).
Réponse à la recommandation 1 du rapport `evals/reports/2026-07-03/report.md` :
la campagne de juillet a mesuré la **présence passive** du standard (zéro
engagement sur 40 runs governed) ; ce bras mesure son **usage forcé**.

## Principe

`activated` = enrôlement identique au bras `governed` + deux hooks Claude Code
installés dans la copie de run :

| Hook | Événement | Rôle |
| --- | --- | --- |
| `activation-session-start.sh` | `SessionStart` | Injecte dans le contexte l'obligation d'ouvrir et de remplir l'enveloppe de tâche du standard avant tout code, et la consigne de clôture (`gate check` vert obligatoire) |
| `activation-stop-gate.sh` | `Stop` | Refuse la clôture de session (exit 2 + consigne de reprise) tant que l'enveloppe de tâche contient des placeholders ou que `grimoire standard gate check . --task-id bootstrap --target-state review` échoue |

La seule différence entre `activated` et `governed` est ce mécanisme : même
enrôlement, même code de départ, même tâche, même modèle, même budget. Le
contraste `activated` vs `governed` isole donc l'effet de l'activation.

## Contenu du dossier

| Fichier | Rôle |
| --- | --- |
| `claude-settings.json` | Configuration hooks à installer en `.claude/settings.json` de la copie de run |
| `hooks/activation-session-start.sh` | Hook `SessionStart` (stdout = contexte additionnel injecté) |
| `hooks/activation-stop-gate.sh` | Hook `Stop` fail-closed borné (voir garde-fous) |
| `install.sh` | Installe le tout dans une copie baseline enrôlée |

## Installation dans une copie baseline

```bash
# 1. Copie propre de la baseline (comme pour les autres bras)
cp -r evals/witnesses/web-app-todo/app "$RUN_DIR"
cd "$RUN_DIR"

# 2. Enrôlement — identique au bras governed
grimoire init . -a web-app -b local
grimoire standard init . --needs solo-prototyping

# 3. Activation — spécifique au bras activated
cd -
evals/witnesses/web-app-todo/activated/install.sh "$RUN_DIR"
```

L'installateur refuse une copie non enrôlée. Les hooks sont relatifs à
`$CLAUDE_PROJECT_DIR` : aucun chemin absolu n'est écrit dans la copie.

## Ce que le hook Stop exige (vérifié sur CLI grimoire-kit 3.22.0)

Sur le profil `starter` (résolu par `--needs solo-prototyping`), la séquence
suivante rend `gate check --target-state review` vert ; elle a été rejouée
intégralement hors LLM avant l'écriture de ces artefacts :

1. Remplir `_grimoire-output/evidence/bootstrap/task-envelope.md`
   (scaffoldée par `standard init`) — plus aucun placeholder d'état ni de
   périmètre d'outils.
2. Créer `_grimoire/standard/task-board.yaml` (non scaffoldé par le profil
   starter) avec une entrée minimale — `states` et `evidence_pack_ref` sont
   requis par `grimoire standard verify` :

   ```yaml
   $schema: "grimoire-agentic-standard-task-board/v1"
   states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]
   tasks:
     - task_id: bootstrap
       title: "<tâche du run>"
       status: review
       acceptance_criteria:
         - "<critères>"
       evidence_pack_ref: "_grimoire-output/evidence/bootstrap/evidence-pack.md"
   ```

3. `grimoire standard context build . --task-id bootstrap`
   (écrit `_grimoire-output/context/bootstrap/context-bundle.yaml`).
4. `grimoire standard decision trace . --task-id bootstrap`
   (écrit `_grimoire-output/decisions/bootstrap/decision-trace.yaml`).
5. Remplir `_grimoire-output/evidence/bootstrap/evidence-pack.md`.
6. `grimoire standard gate check . --task-id bootstrap --target-state review`
   → exit 0.

Le hook `SessionStart` donne ces étapes à l'agent ; on mesure s'il les exécute
et l'effet sur les livrables — pas sa capacité à les découvrir seul.

## Garde-fous du hook Stop

- **Borné** : au plus `ACTIVATION_MAX_BLOCKS` blocages (défaut 3, compteur
  dans `_grimoire-output/evals-activation/stop-blocks.count`) ; au-delà, la
  clôture est autorisée pour ne pas condamner le run — l'échec reste visible
  dans le run-record (`gate_ok=false`).
- **Fail-open environnemental** : copie non enrôlée ou CLI `grimoire` absent
  → aucune interférence, la clôture passe.
- **Aucune écriture hors de la copie de run** ; le compteur vit sous
  `_grimoire-output/`, déjà exclu du diff jugé.
- Les marqueurs de placeholder testés sont exactement ceux vérifiés par le kit
  (`_verify_task_envelope` dans `src/grimoire/core/agentic_standard.py`).

## Notes pour la campagne

- Figer la version du kit dans le rapport ; la validation mécanique ci-dessus
  a été faite avec le CLI 3.22.0 — rejouer la séquence du sandbox si la
  version pinnée diffère.
- `evals/collect.py` accepte `--arm activated` et collecte les métriques
  standard (verify/score/gate) comme pour `governed` ; pour `activated`, le
  gate est évalué à l'état `review` (celui que le mécanisme impose), et les
  métriques standard sont lues sur `--standard-task-id` (défaut `bootstrap`),
  distinct du label de tâche d'évaluation.
- Le run-record garde la preuve d'engagement : `gate_ok`, `gate_missing`,
  et le journal `_grimoire-output/events/runtime-journal.jsonl`
  (événements `gate.checked`).
