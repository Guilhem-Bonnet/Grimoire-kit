# Evals — campagne avant/après standard

Infrastructure de la campagne définie par [docs/evals-protocol.md](../docs/evals-protocol.md).

**Statut : quatre campagnes exécutées, toutes publiées dans `reports/`.**

| Campagne | Bras | Verdict |
|---|---|---|
| [2026-07-03](reports/2026-07-03/report.md) | baseline, governed | effet non démontré ([errata](reports/2026-07-03/ERRATA.md)) |
| [2026-07-09](reports/2026-07-09/report.md) | activated | engagement 40/40 contre 0/40, effet non démontré |
| [2026-08-27](reports/2026-08-27/report.md) | activated-v2, disclosed, baseline-v3 | effet non démontré selon A1-v3 |
| [2026-09-04](reports/2026-09-04/report.md) | enforced, activated-v3 | effet non démontré, indicatif (sous puissance, n = 3) — hors compteur A2 |

Le constat borné répliqué sur les trois campagnes : sur ce témoin, avec ce
runner et ce modèle, l'activation du standard élimine les régressions dures
(0 sur 96 runs activés cumulés, contre 9/40 en baseline contemporaine). Le
claim composite reste non démontré, et aucune revendication d'efficacité ne
peut être faite sur cette base.

## Contenu

| Chemin | Rôle |
|---|---|
| `tasks/web-app-todo.yaml` | Suite de tâches pré-enregistrée — témoin web (React + Go + PostgreSQL) |
| `tasks/terraform-houseserver.yaml` | Suite de tâches pré-enregistrée — témoin infra (Proxmox/Terraform/K3s) |
| `witnesses/web-app-todo/activated/` | Mécanisme d'activation du bras `activated` (hook SessionStart seul + installateur, voir `ACTIVATION.md`) |
| `witnesses/web-app-todo/enforced/` | Mécanisme du bras `enforced` (activation + hooks bloquants PreToolUse et Stop du kit, profil governed) |
| `runner.py` | Runner de campagne : copie propre, enrôlement, `claude -p`, jugement mécanique, ledger, collecte |
| `judge.py` | Paquets de jugement aveugles (anonymisés) et application des verdicts |
| `aggregate.py` | Agrégation d'une campagne et calcul du critère A1 entre deux bras |
| `collect.py` | Collecteur de run-record (verify/score/gate depuis les artefacts du kit ; métriques externes à `null`, renseignées par l'opérateur) |
| `runs/` | Sorties brutes par exécution (non committées) |
| `reports/` | Rapports agrégés par campagne |

## Règles

- Les suites de tâches sont **figées** : tout amendement post-enregistrement est
  journalisé dans le champ `pinned.amendments` du YAML concerné.
- Le collecteur n'invente aucune métrique : ce qui n'est pas mesurable depuis
  les artefacts du kit reste `null` jusqu'à saisie par l'opérateur.
- Le rapport final agrège **toutes** les exécutions (voir règles d'honnêteté du
  protocole).
