### Fixed

- **Une reprise vers `running` rouvre le bail.** Après `task move --to running` depuis `needs_verification` ou `waiting`, un claim déjà échu faisait récolter la tâche au tick suivant, alors que sa session vivait encore, et la tâche partait en attente humaine (#1149).
