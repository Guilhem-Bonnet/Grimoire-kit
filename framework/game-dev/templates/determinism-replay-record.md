# Determinism / Replay Record

> Preuve qu'une simulation est rejouable (UC-13, règle GR-03). Seed, pas fixe, hash d'état. Prérequis des tests, replays et debug réseau.

## Identité

- Simulation / système :
- Seed :
- Pas de temps fixe (tick) :
- Version du build :
- Date :

## Reproductibilité

| Run | Seed | Hash d'état final | Vainqueur / résultat | Identique au run 1 ? |
|---|---|---|---|---|
| 1 |  |  |  | — |
| 2 |  |  |  | `oui | non` |
| 3 |  |  |  | `oui | non` |

## Sources de non-déterminisme vérifiées

| Source | Maîtrisée ? | Méthode |
|---|---|---|
| RNG | `oui | non` | seed par entité |
| Ordre d'itération | `oui | non` |  |
| Threads / parallélisme | `oui | non` | résultats fusionnés de façon ordonnée |
| Temps réel / delta variable | `oui | non` | pas fixe |

## Verdict

- Déterminisme : `confirmé | rompu`
- Si rompu : cause racine + correction.

---
Trace amont : `knowledge/use-cases-jeux-video.md#uc-13` · socle : RUN-13, QUA-10.
