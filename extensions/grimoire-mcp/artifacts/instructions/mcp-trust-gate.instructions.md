---
applyTo: "**"
description: Qualification et bornage des serveurs MCP du projet (MCP Trust Gate)
created: 2026-07-03
extension: grimoire-mcp
---

# MCP Trust Gate

Ces règles s'appliquent à tout serveur MCP branché au projet (pattern GOV-09
du catalogue, rayon limité par GOV-07).

## Règles

1. **Qualification avant branchement** : un serveur MCP entre dans `.mcp.json` seulement après inventaire de ses outils, de leurs effets (lecture/écriture/externe) et de son origine. Pas de serveur anonyme.
2. **Surface déclarée** : les outils à effet d'écriture ou externe sont listés dans la configuration du projet ; un outil non listé est traité comme refusé.
3. **Le policy engine arbitre** (GOV-01, requis) : les appels d'outils MCP passent par les mêmes gates que les outils natifs — un serveur MCP n'est pas un contournement.
4. **Rayon limité** (GOV-07) : un serveur MCP a accès à son périmètre déclaré, jamais au projet entier par défaut. Le serveur grimoire-mcp expose missions, preuves et mémoire en lecture ; toute écriture passe par le runtime.
5. **Révocation simple** : retirer un serveur = retirer sa entrée de configuration ; aucun état résiduel toléré.
