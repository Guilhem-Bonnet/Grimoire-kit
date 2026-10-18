# Exemples

Un exemple du kit n'est publiable que s'il est rejouable : le lecteur relance
la démonstration au lieu de croire un transcript.

## Exemples rejouables

Chaque répertoire contient `replay.sh` (sans modèle, sans réseau, déterministe)
et `expected.json` (sortie attendue du rejeu et paquet de preuve : gate, score,
coût). Depuis la racine du dépôt, avec `grimoire` dans le `PATH` :

```bash
bash examples/blueprint-minimal/replay.sh
bash examples/blueprint-web-pipeline/replay.sh
```

| Exemple | Ce que le rejeu prouve |
|---|---|
| [`blueprint-minimal`](blueprint-minimal/) | le plus petit blueprint v1 passe les deux couches de validation |
| [`blueprint-web-pipeline`](blueprint-web-pipeline/) | un flow multi-nœuds passe la validation ; ses évals sont rejouées contre `record.json` |

`tests/unit/test_examples_replay.py` rejoue chaque exemple et compare la sortie
à `expected.json` (Linux et macOS ; Windows ne vérifie que la structure).

## Exemples illustratifs

`terraform-houseserver` et `web-app-todo` sont des récits mis en forme à la
main. Ils portent la mention « non rejouable » et figurent dans la liste
fermée du test : un nouvel exemple doit être rejouable.
