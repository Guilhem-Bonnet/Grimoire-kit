# Exemple : blueprint web-pipeline

Valide `registry/blueprints/web-pipeline.blueprint.json`, puis rejoue ses évals
contre `record.json`.

```bash
bash examples/blueprint-web-pipeline/replay.sh
```

`record.json` est une **fixture synthétique** écrite à la main : elle montre le
format de la trace qu'un hôte enregistre après un vrai run, elle ne mesure rien.
Elle ne couvre qu'un cas sur cinq ; les quatre autres sont rapportés « non
exécuté » et jamais comptés comme réussis. Les tokens de la fixture ne figurent
donc pas dans le paquet de preuve (`tokens: null`).
