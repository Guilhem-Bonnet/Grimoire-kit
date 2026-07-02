# Evals — campagne avant/après standard

Infrastructure de la campagne définie par [docs/evals-protocol.md](../docs/evals-protocol.md).

**Statut : pré-enregistrement. Aucune campagne exécutée, aucun résultat.**

## Contenu

| Chemin | Rôle |
|---|---|
| `tasks/web-app-todo.yaml` | Suite de tâches pré-enregistrée — témoin web (React + Go + PostgreSQL) |
| `tasks/terraform-houseserver.yaml` | Suite de tâches pré-enregistrée — témoin infra (Proxmox/Terraform/K3s) |
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
