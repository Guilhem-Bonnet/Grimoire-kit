---
name: grimoire-mcp-setup
description: Brancher le serveur MCP du kit à un client (Claude, IDE) avec qualification Trust Gate — configuration .mcp.json, inventaire des outils exposés, vérification du périmètre. À utiliser au branchement d'un client MCP sur le projet.
created: 2026-07-03
extension: grimoire-mcp
---

# Branchement du serveur MCP Grimoire

Cette skill guide le branchement du serveur MCP du kit à un client, en
respectant `mcp-trust-gate.instructions.md`.

## Prérequis

- `grimoire-kit[mcp]` installé dans le `.venv` du projet (fait par l'extension).
- Un client MCP (Claude Code, IDE compatible).

## Étapes

1. Inventorier les outils exposés : `python3 -c "from grimoire.mcp import server; print([t for t in dir(server) if not t.startswith('_')])"` et classer chaque outil (lecture / écriture / externe).
2. Déclarer le serveur dans `.mcp.json` du projet avec son périmètre :

   ```json
   {
     "mcpServers": {
       "grimoire": {
         "command": "python3",
         "args": ["-m", "grimoire.mcp"],
         "env": { "GRIMOIRE_PROJECT_ROOT": "." }
       }
     }
   }
   ```

3. Vérifier depuis le client que seuls les outils inventoriés apparaissent.
4. Consigner l'inventaire (outils, effets, périmètre) dans la base de connaissances du projet — c'est la preuve de qualification GOV-09.

## Limites

- Le serveur expose les surfaces du projet en lecture ; les écritures passent par le runtime et ses gates.
- Tout nouvel outil exposé par une mise à jour du kit exige une requalification.
