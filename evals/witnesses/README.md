# Témoins d'évaluation

Codebases figées servant de point de départ aux runs A/B du protocole
`docs/evals-protocol.md`. Chaque témoin part du même état pour les deux bras
(`governed` / `baseline`) — seule l'installation du standard gouverné diffère.

| Témoin | Stack | Statut | Spec |
|---|---|---|---|
| `web-app-todo` | React + Go + PostgreSQL | spec prête, code à construire | [SPEC](web-app-todo/SPEC.md) · [run](web-app-todo/RUN-PROTOCOL.md) · [kickoff](web-app-todo/BUILD-KICKOFF.md) |
| `terraform-houseserver` | Terraform + Ansible + K3s | tâches figées, spec à écrire | — |

Voir aussi : [suites de tâches pré-enregistrées](../tasks/) · [collecteur](../collect.py).
