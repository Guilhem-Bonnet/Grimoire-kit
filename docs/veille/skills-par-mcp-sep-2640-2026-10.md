---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Servir les skills du kit par l'extension MCP Skills, SEP-2640 (2026-10)

> Tâche `GAO-veille-serv-001`. Sources ouvertes le 2026-10-25. Aucun hôte n'a
> été lancé : le support réel de chaque hôte n'est **pas observé**, seulement lu
> dans la matrice communautaire du projet MCP.

## Ce que dit la spécification

[SEP-2640 : Skills Extension](https://modelcontextprotocol.io/seps/2640-skills-extension) `[doc]`, lue le 2026-10-25 (environ 64 000 caractères, les 400 premières lignes lues) :

- Statut **Final**, type Extensions Track, créée le 2026-04-23. La page est « un enregistrement historique » : les changements postérieurs ne s'y reflètent pas, la spécification courante fait foi.
- Identifiant d'extension : `io.modelcontextprotocol/skills`. Un serveur qui la déclare implémente `skills/list` (énumération) et `skills/get` (entrée d'un skill par URI) ; `resources/directory/read` est optionnel. L'implémentation est facultative des deux côtés.
- Un skill doit suivre la spécification Agent Skills ; chaque fichier du dossier est une ressource MCP, de préférence sous `skill://<chemin>/<fichier>`, dont le dernier segment du chemin égale le `name` du `SKILL.md`.
- Chaque entrée de liste porte les `resources` avec `digest` et `size` (ou `"resources": "dynamic"`) ; l'hôte doit vérifier le condensat, qui n'est « pas une frontière de sécurité ». Une liste vide ou partielle ne prouve pas l'absence de skills.
- Les limites chiffrées (section « Limits ») et les exigences de sécurité détaillées n'ont pas été lues : `non vérifié`.

## Matrice de support des hôtes

[Extension Support Matrix](https://modelcontextprotocol.io/extensions/client-matrix) `[doc]` (maintenue par la communauté, lue le 2026-10-25), colonne « Skills » :

| Hôte demandé | Ligne dans la matrice | Colonne Skills |
|---|---|---|
| Claude Code | **absent de la matrice** (seuls Claude web et Claude Desktop y figurent) | n/a |
| Codex | **absent** (ChatGPT figure) | n/a |
| opencode | **absent** | n/a |
| Copilot | « VS Code GitHub Copilot » et « Microsoft 365 Copilot » figurent | **case vide** |
| ChatGPT, fast-agent, MCP Inspector | présents | « Partial » |

La page [Claude Code MCP](https://code.claude.com/docs/en/mcp) `[doc]` (150 000 caractères, 100 000 lus) ne mentionne ni `skills/list` ni `skill://` dans la partie lue ; le reste est `non vérifié`. Conclusion lisible : aucun des quatre hôtes du kit n'est donné comme consommateur de l'extension.

## État dans le kit

- `git grep -n "SEP-2640" -- src docs framework` : une seule occurrence, `framework/agentic-industry-reference.md:220` (« est en revue »). La note date d'avant le statut Final : à corriger.
- `src/grimoire/mcp/server.py` s'appuie sur une façade qui n'expose que `tool`, `prompt`, `resource`, `run` (lignes 70-90) ; elle sert aussi bien `mcp` 1.x que 2.x (`pyproject.toml:33-35`). Si les SDK permettent d'enregistrer la méthode `skills/list` et de déclarer l'extension : `non vérifié`.
- Le kit émet déjà les `SKILL.md` dans les dossiers que Claude Code et Copilot lisent (`.claude/skills/`, `.github/skills/`), avec un contrôle de conformité (#874).

## Ce que ça change pour le kit

**Rien maintenant.** Décision consignée : on ne sert pas les skills par l'extension, parce qu'aucun des quatre hôtes ciblés n'est donné comme client (matrice), et que la voie de fichiers marche déjà pour deux d'entre eux. Ajouter une méthode au serveur sans consommateur serait de la surface à maintenir sans gain.

À faire à petit coût : corriger `framework/agentic-industry-reference.md:220` (la SEP est Final depuis la lecture du 2026-10-25) ; tâche S, `--check` : la ligne cite le statut « Final » et la date de lecture.

Condition de réouverture : Claude Code ou Codex apparaît avec « Supported » dans la colonne Skills de la matrice. Tâche à créer à ce moment, module `src/grimoire/mcp/server.py` : exposer `skills/list` et `skills/get` sur les skills de `framework/hosts/skills/`, avec condensats, après vérification que les SDK `mcp` 1.x et 2.x supportent l'enregistrement de la méthode.

## Sources consultées le 2026-10-25

- [SEP-2640: Skills Extension](https://modelcontextprotocol.io/seps/2640-skills-extension) — projet MCP `[doc]`
- [Extension Support Matrix](https://modelcontextprotocol.io/extensions/client-matrix) — projet MCP `[doc]`, maintenue par la communauté
- [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) — Claude Code Docs `[doc]`, lue en partie
- [experimental-ext-skills](https://github.com/modelcontextprotocol/experimental-ext-skills) — dépôt du groupe de travail `[doc]` : ne nomme aucun client
