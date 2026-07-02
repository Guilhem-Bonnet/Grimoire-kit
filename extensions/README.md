# Extensions Grimoire

Une extension est un bundle d'artefacts gouvernés (agents, skills, hooks,
workflows, instructions) décrit par un manifeste `extension.json` et installé
dans un projet cible via `grimoire ext`.

## Commandes

```bash
grimoire ext add extensions/crewai   # installer depuis un dossier
grimoire ext list                    # extensions installées
grimoire ext verify crewai           # vérification post-installation
grimoire ext remove crewai           # désinstallation
```

## Contrat

- Le manifeste est validé avant toute installation (voir [extension.schema.json](extension.schema.json)).
- `patterns.implements` est obligatoire : chaque extension se positionne sur le catalogue de patterns agentiques.
- Tout hook fourni s'enregistre en mode `shadow`, sans exception.
- Les chemins sont relatifs et sans remontée ; l'état installé vit dans `_grimoire/extensions/installed.json` du projet cible.

## Distinction avec `grimoire plugins`

`grimoire plugins` découvre des entry-points Python installés dans
l'environnement. Une extension s'installe dans un projet et fournit des
artefacts gouvernés — les deux mécanismes sont complémentaires.

## Extensions disponibles

| Extension | Description | Patterns |
| --- | --- | --- |
| [crewai](crewai/extension.json) | Pont CrewAI : crews importés en Recipes gouvernées via `grimoire.runtime.crewai_adapter` | ORC-01, ORC-02, ORC-03 |
