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

Tableau généré depuis les manifestes — ne pas éditer à la main :
`python scripts/gen-extensions-table.py` (drift bloqué par
`tests/unit/test_extensions_readme.py`).

<!-- extensions-table:start (généré par scripts/gen-extensions-table.py) -->
| Extension | Kind | Description | Patterns |
| --- | --- | --- | --- |
| [autogen](autogen/extension.json) | `flow-adapter` | Pont AutoGen (Microsoft) : conversations multi-agents exposées comme orchestrations Grimoire gouvernées — rôles bornés, tours de parole tracés, sortie en handoff packet | ORC-01, ORC-03 |
| [browser-use](browser-use/extension.json) | `capability` | Agents de navigation web gouvernés : chaque action UI produit une preuve (screenshot, DOM, URL), périmètre de navigation borné par allowlist, actions irréversibles simulées avant exécution | QUA-11, GOV-07 |
| [crewai](crewai/extension.json) | `flow-adapter` | Expose les crews CrewAI comme artefacts Grimoire gouvernés via l'adaptateur grimoire.runtime.crewai_adapter : import de flows en Recipes, exécution bornée, traces normalisées | ORC-01, ORC-02, ORC-03 |
| [fennara-godot](fennara-godot/extension.json) | `mcp-toolbox` | Boucle de feedback Godot réelle pour les agents via le serveur MCP Fennara : diagnostics, contexte scène/nœuds, screenshots, logs runtime et état éditeur — des preuves branchables sur les gates evidence-gated de l'archétype game-dev | QUA-12, QUA-04, RUN-08 |
| [grimoire-mcp](grimoire-mcp/extension.json) | `mcp-toolbox` | Le serveur MCP du kit comme toolbox gouvernée : accès outillé aux missions, preuves et mémoire du projet depuis tout client MCP, qualifié par le MCP Trust Gate (GOV-09) — permissions déclarées, surfaces bornées | GOV-09, GOV-07 |
| [haystack](haystack/extension.json) | `capability` | Pipelines RAG Haystack gouvernés : indexation documentaire déclarée (sources, fraîcheur, propriétaire), rappel avec provenance obligatoire — contre le RAG aveugle | KNO-06, KNO-02 |
| [langfuse](langfuse/extension.json) | `observability` | Pont d'observabilité Langfuse : exporte la télémétrie agentique locale (events.jsonl) vers Langfuse en mode best-effort, avec conventions de traces et guide d'installation | QUA-02, QUA-08, QUA-10 |
| [langgraph](langgraph/extension.json) | `flow-adapter` | Pont LangGraph : graphes d'agents à état persistant exposés comme workflows Grimoire gouvernés — état explicite, reprises auditées, checkpoints tracés | ORC-09, ORC-10 |
<!-- extensions-table:end -->
