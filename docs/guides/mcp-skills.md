---
audience: utilisateur
---

# Skills servis par le serveur MCP

> Le serveur MCP du kit sert les skills du projet par l'extension officielle `io.modelcontextprotocol/skills` : le client les charge à la demande, sans rien installer dans le projet.

## Ce que c'est

Les skills du kit (`grimoire-memory`, `grimoire-evidence`, les skills de votre archétype…) sont déjà émis en fichiers pour chaque hôte (`.claude/skills/`, `.github/skills/`…). L'extension « Skills over MCP » (SEP-2640, annoncée le 2026-11-24) ajoute une seconde voie : le serveur `grimoire-mcp` les **publie lui-même**. Le client les découvre à la connexion, ne lit un skill que lorsqu'il le charge, et reçoit la version du serveur en cours, donc la mise à jour du kit suffit.

Côté protocole, le serveur :

- annonce la capacité `resources` et `capabilities.extensions["io.modelcontextprotocol/skills"]` dans `server/discover` ;
- répond à `skills/list` (paginé, `ttlMs` et `cacheScope`) et à `skills/get` ;
- sert chaque `SKILL.md` par `resources/read` sous l'URI `skill://<nom>/SKILL.md`.

Le manifeste de chaque skill porte, pour chaque fichier, le `digest` SHA-256 et la `size` en octets des octets réellement servis ; le `frontmatter` publié est celui de ces octets. `resources/directory/read` n'est pas implémenté, et `directoryRead` est donc annoncé à `false` : un skill du kit est un fichier unique, il n'y a aucun répertoire à lister.

## Conditions côté Claude Code

D'après la documentation de Claude Code au 2026-11-27 (à revérifier, la fonction est en bêta) :

- Claude Code **2.1.331** ou plus récent ; statut **bêta publique** ;
- connexion directe à Anthropic ; offres Pro, Max ou Ultra par `/login` (Team et Enterprise : désactivé tant que l'organisation ne l'a pas activé) ;
- terminal et sessions locales du Desktop ; non pris en charge : `claude -p`, sessions cloud, Agent SDK, GitHub Actions.

Le serveur doit être lancé en **stdio** par la session (`grimoire-mcp`, entrée `grimoire` de `.mcp.json`) : le projet est alors le dossier de la session.

## Invoquer un skill

Un skill servi par le serveur nommé `grimoire` dans votre configuration MCP s'invoque :

```text
/grimoire:grimoire-memory
```

`/skills` les liste aussi. Le préfixe est le nom que vous avez donné au serveur dans votre configuration, pas un nom fixé par le kit. Selon votre mode de permission, Claude Code peut demander une approbation au premier chargement et à chaque changement du contenu du skill.

## Ce qui est servi, ce qui est écarté

Sont servis les skills que `grimoire host sync` émettrait : ceux livrés avec le kit et ceux de votre archétype. Le contenu est rendu comme pour les autres hôtes : `name` (égal au nom du fichier), `description`, et `license` / `compatibility` s'ils existent. Les champs qui élargissent des permissions (`tools`, `allowed-tools`) ne sont **jamais** publiés.

Chaque entrée annonce son **origine** dans `_meta["io.grimoire/origin"]` : `kit` (livré avec le kit) ou `project` (fichier du projet). Un skill du projet qui porte le nom d'un skill livré par le kit ne le remplace pas : il est écarté (« masque un skill du kit : renomme-le ») et la version du kit est servie.

Le serveur ne sert que des fichiers réguliers du projet, sans suivre de lien, dans des limites de taille : les trois voies (`skill://`, `grimoire://skill/{slug}`, `grimoire_skill`) lisent le même catalogue et appliquent les mêmes exclusions. Un fichier est contrôlé avant d'être lu et sa lecture est bornée ; une erreur sur un fichier n'écarte que ce skill.

Un skill est **écarté**, avec sa raison, jamais renommé :

| Raison | Exemple |
| --- | --- |
| Nom hors de la spécification Agent Skills | majuscule, `--`, caractère interdit, plus de 64 caractères |
| `name` déclaré différent du nom du fichier | `name: autre` dans `triage.md` |
| Description ou `compatibility` hors limites | description de plus de 1024 caractères |
| Fichier source en lien symbolique, ou dossier hors du projet | aucun lien n'est suivi |
| Contenu binaire ou trop gros | octet nul ; plus de 100 000 octets (limite de fichier de Claude Code) |
| Fichier qui n'est pas régulier ou pas lisible | tube, périphérique, dossier ; texte non UTF-8 |
| Même nom qu'un skill du kit | à renommer ; la version du kit reste servie |
| Dossier trop fourni | au plus 500 fichiers examinés par dossier |

`grimoire doctor` affiche `skills servis par MCP : N (extension io.modelcontextprotocol/skills)` et nomme les skills écartés ; ce n'est qu'une information, jamais un échec. Le serveur journalise aussi chaque exclusion sur sa sortie d'erreur au démarrage.

## Ce qui reste émis en fichiers

Rien ne change pour les autres voies, qui restent servies à l'identique :

- les fichiers par hôte (`grimoire up`, `grimoire host sync`), seule voie de GitHub Copilot, de Cursor, de Codex et des hôtes qui ne lisent pas l'extension ;
- les prompts MCP (une commande Grimoire = un prompt), les ressources `grimoire://skill/{slug}` et l'outil `grimoire_skill`.

Un client qui ignore l'extension voit simplement, en plus, des ressources `skill://<nom>/SKILL.md` ordinaires dans `resources/list`. L'ancienne forme `grimoire://skill/{slug}` reste lisible mais n'est plus listée skill par skill : elle est publiée comme modèle de ressource (`resources/templates/list`), pour que chaque skill ne figure qu'une fois dans `resources/list`.

## Limites

- Catalogue pris **au démarrage** du serveur : un skill modifié pendant la session apparaît au prochain démarrage.
- Un seul fichier par skill (pas de fichiers annexes ni de scripts) : c'est ce que le kit connaît d'un skill.
- Claude Code accepte au plus 100 skills par serveur, 100 Ko par fichier texte et 400 Ko par skill. Le serveur écarte lui-même un skill trop gros (voir plus haut) ; il ne plafonne pas leur nombre, il les pagine par 50.
- **Démon HTTP partagé** (`grimoire mcp start`, #723) : son dossier courant n'est le projet de personne, donc aucun skill n'y est servi. La capacité est annoncée, `skills/list` répond une liste vide et `skills/get` refuse toute URI (`-32602`). Utilisez l'entrée stdio, ou `grimoire_skill`.
- Les empreintes (`digest`) prouvent la cohérence entre le manifeste et le contenu, pas la confiance : elles viennent du même serveur.

## Voir aussi

- [Serveur MCP](mcp.md)
- [Hôtes agentiques](../reference/hotes.md)
- Spécification : dépôt `modelcontextprotocol/ext-skills`, `specification/beta/skills.mdx`
