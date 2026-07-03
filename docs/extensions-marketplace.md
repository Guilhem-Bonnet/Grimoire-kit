# Extensions et marketplace

Une extension est un bundle d'artefacts gouvernés — agents, skills, hooks,
workflows, instructions — décrit par un manifeste `extension.json` et
installé dans les surfaces gouvernées de votre projet (`.github/agents/`,
`.github/skills/`...). Chaque extension déclare les patterns du
[catalogue agentique](https://github.com/Guilhem-Bonnet/grimoire-forge)
qu'elle matérialise : le marketplace n'est pas un annuaire, c'est une
projection du standard.

## Installer une extension

```bash
# Depuis un dossier local
grimoire ext add extensions/crewai

# Depuis le registry public (checksum sha256 vérifié avant extraction)
git clone https://github.com/Guilhem-Bonnet/grimoire-extensions-registry
grimoire ext add crewai --registry grimoire-extensions-registry
grimoire ext add crewai --registry grimoire-extensions-registry --version 0.1.1
```

Règles appliquées à l'installation :

- Le manifeste est validé (mapping patterns obligatoire, chemins relatifs
  sans remontée, permissions déclarées).
- Tout hook fourni s'enregistre en mode `shadow`, sans exception.
- L'état installé vit dans `_grimoire/extensions/installed.json` ; la
  provenance registry est tracée pour que `remove` et `verify` rejouent
  les scripts depuis l'archive d'origine.

## Gérer les extensions installées

```bash
grimoire ext list              # extensions installées et leurs patterns
grimoire ext verify crewai     # vérification post-installation
grimoire ext remove crewai     # désinstallation propre (hooks retirés)
```

## Publier une extension

```bash
grimoire ext publish mon-extension/ --registry <clone-du-registry>
cd <clone-du-registry> && bash scripts/publish-pr.sh mon-extension 0.1.0
```

L'archive est déterministe (contenu identique = checksum identique) ; la CI
du registry valide le manifeste, les checksums et l'existence des patterns
déclarés dans le catalogue.

## Blueprints publiables

Les fichiers `.blueprint.json` se publient et s'installent comme des
artefacts du marketplace :

```bash
grimoire ext publish mon-flow.blueprint.json --registry <clone>
grimoire ext add-blueprint mon-flow --registry <clone>
```

L'installation vérifie le checksum, copie vers `_grimoire/blueprints/` et
rapporte les extensions requises non installées.

## Le champ `kind`

| Kind | Sens |
| --- | --- |
| `flow-adapter` | Encapsule un framework agentique dans le flow (crewai, langgraph, autogen) |
| `mcp-toolbox` | Serveur MCP fournissant du grounding d'environnement (grimoire-mcp, fennara-godot) |
| `observability` | Backend d'observation (langfuse) |
| `capability` | Capacité outillée (browser-use, haystack) |

`kind` qualifie la nature de la prise — orthogonal aux familles de patterns,
qui disent ce que l'extension fait dans le flow.
