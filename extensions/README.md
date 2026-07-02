# Extensions Grimoire

Une extension est un bundle d'artefacts gouvernés (agents, skills, hooks,
workflows, instructions) décrit par un manifeste `extension.json` et installé
dans un projet cible via `grimoire ext`.

## Commandes

```bash
grimoire ext add extensions/crewai            # installer depuis un dossier
grimoire ext add crewai --registry <clone>    # installer depuis le registry (checksum vérifié)
grimoire ext publish extensions/crewai --registry <clone>   # publier
grimoire ext list                             # extensions installées
grimoire ext verify crewai                    # vérification post-installation
grimoire ext remove crewai                    # désinstallation
```

Le registry vit dans un repo dédié :
[grimoire-extensions-registry](https://github.com/Guilhem-Bonnet/grimoire-extensions-registry)
(index versionné, archives à checksum, CI de conformité, publication par PR).

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
| [langfuse](langfuse/extension.json) | Observabilité : export best-effort de la télémétrie locale vers Langfuse, conventions de traces | QUA-02, QUA-08, QUA-10 |
| [langgraph](langgraph/extension.json) | Pont LangGraph : StateGraphs à état explicite, checkpoints audités, flow déclaré | ORC-09, ORC-10 |
| [autogen](autogen/extension.json) | Pont AutoGen : conversations multi-agents à rôles bornés, terminaison explicite, tours tracés | ORC-01, ORC-03 |
| [browser-use](browser-use/extension.json) | Navigation web gouvernée : preuve par action UI, allowlist, simulation avant irréversible | QUA-11, GOV-07 |
| [haystack](haystack/extension.json) | RAG gouverné : indexation avec provenance, rappel vérifiable — contre le RAG aveugle | KNO-06, KNO-02 |
