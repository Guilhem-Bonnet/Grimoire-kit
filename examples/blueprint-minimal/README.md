# Exemple : blueprint minimal

Valide `registry/blueprints/minimal.blueprint.json` (schéma JSON puis contrôles
structurels). Aucun modèle n'est appelé.

```bash
bash examples/blueprint-minimal/replay.sh
```

`expected.json` porte la sortie attendue (`replay`) et le paquet de preuve
(`proof`). Le score est `null` : un rejeu de validation ne produit pas de score.
