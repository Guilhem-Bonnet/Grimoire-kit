# Bras « activated » — mécanisme d'activation committé

Artefacts reproductibles du mécanisme d'activation pré-enregistré dans
`../ACTIVATION.md` (2026-07-09) et reconduit à l'identique par
`../ACTIVATION-V2.md` (2026-07-12). Ce dossier committe le mécanisme qui a
tourné lors de la campagne du 2026-07-09 (réserve reproductibilité de la
revue PR #71) : un run `activated` doit pouvoir être rejoué sans rien
reconstruire à la main.

## Principe

`activated` = enrôlement identique au bras `governed` + un **hook
SessionStart Claude Code** installé dans la copie de run :

- `.claude/settings.json` déclare un hook `SessionStart` de type `command`.
- Le hook émet sur stdout le contenu de `.claude/activation-context.md`
  (directive verbatim d'`ACTIVATION.md`), injecté comme contexte au
  démarrage de la session.
- Le prompt de tâche reste le champ `prompt` exact du YAML — **identique
  aux deux autres bras**. Seul le contexte de session diffère.

Aucun hook `Stop` : le design pré-enregistré mesure si l'agent engage le
standard quand on le lui demande, pas s'il est mécaniquement empêché de
conclure. Un stop-gate fail-closed reste un candidat v3, hors de ce bras.

## Contenu du dossier

| Fichier | Rôle |
| --- | --- |
| `claude-settings.json` | Configuration hooks à installer en `.claude/settings.json` de la copie de run (SessionStart uniquement) |
| `activation-context.md` | Directive injectée, verbatim d'`ACTIVATION.md` §Directive injectée |
| `hooks/activation-session-start.sh` | Hook `SessionStart` (stdout = contexte additionnel injecté) |
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

## Collecte

`evals/collect.py` accepte `--arm activated` et collecte les métriques
standard (verify/score/gate) comme pour `governed`. Pour `activated`, le
gate est évalué à l'état `review` (sans état cible, un enrôlement pristine
passe trivialement le gate et la mesure d'engagement ne mesure rien), et
les métriques standard sont lues sur `--standard-task-id` (défaut
`bootstrap` — l'enveloppe que le mécanisme impose), distinct du label de
tâche d'évaluation (voir `evals/reports/2026-07-03/ERRATA.md`).

Les métriques d'engagement restantes (`envelope_filled`, `evidence_rows`)
sont relevées par inspection des artefacts, comme défini dans
`../ACTIVATION.md` §Métriques d'engagement.
