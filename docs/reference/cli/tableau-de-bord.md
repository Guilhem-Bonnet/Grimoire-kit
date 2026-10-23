---
audience: référence
---

# Le tableau de bord

## Web

Le groupe `grimoire web` est le chemin par lequel un agent atteint le web. Il
existe parce que le chemin nu ne suffit pas : `web-browser.py` rend son flux tel
quel, et un agent qui colle ce flux dans son contexte ne distingue plus ce qu'il
a lu de ce qu'on lui a demandé (OWASP LLM01).

| Commande | Description |
| --- | --- |
| `grimoire web fetch <url> [--selector CSS] [--timeout N] [--json]` | Récupérer une page et la rendre enveloppée « donnée externe, pas instruction » |

La sortie est encadrée par deux marqueurs portant un identifiant tiré au hasard
à chaque appel, que la page ne peut pas connaître ; une source qui tente de
recopier le marqueur le voit neutralisé et signalé. En `--json`, le résultat
porte `origin: external` : il ne peut pas relayer une approbation.

Le navigateur tourne en sous-processus — le script est en zone gelée et n'est
jamais importé. Voir
[Contenu externe : donnée, pas instruction](../../guides/rendre-la-preuve-bloquante.md#contenu-externe--donnée-pas-instruction).

## Cockpit multi-projets

Cockpit local de gouvernance, servi sur `127.0.0.1` uniquement. Le registre vit
dans `~/.grimoire/cockpit/registry.json` ; `grimoire init` y inscrit le projet
et l'annonce, et `GRIMOIRE_NO_COCKPIT` désactive cette inscription.

Le cockpit **ne scanne jamais le disque** : `list` lit le registre tel quel,
jamais le système de fichiers. Un projet qui porte les marqueurs Grimoire mais
n'a jamais été enregistré n'apparaît donc pas dans `list` — ce n'est pas un
défaut de détection, `grimoire cockpit list` signale « ce dossier ressemble à
un projet Grimoire mais n'est pas enregistré » et donne la commande
`grimoire cockpit add <chemin>` à lancer.

`serve` et `start` font l'exception (#351) : lancés depuis un dossier qui
porte un marqueur Grimoire, ils l'enregistrent automatiquement s'il ne l'était
pas encore et l'ouvrent sélectionné d'emblée — sans cesser d'être
multi-projets, la bascule vers un autre projet reste dans l'UI. C'est
l'intention exprimée en lançant la commande depuis ce dossier qui justifie
l'ajout automatique, pas une décision prise à la place de l'utilisateur : le
registre reste modifiable (`remove`, `prune`). Un registre vide, ou un
lancement hors de tout projet Grimoire, nomme `grimoire cockpit scan <racine>`.

| Commande | Description |
| --- | --- |
| `grimoire cockpit add <chemin>` | Enregistrer un projet local |
| `grimoire cockpit list` | Lister les projets gouvernés |
| `grimoire cockpit remove <slug>` | Retirer un projet du registre |
| `grimoire cockpit prune` | Retirer les projets dont le chemin a disparu |
| `grimoire cockpit scan <racine>` | Découvrir les projets sous une racine et les enrôler |
| `grimoire cockpit refresh` | Régénérer la couche de données sans servir |
| `grimoire cockpit serve` | Servir le cockpit (bloquant) |
| `grimoire cockpit start` / `stop` / `status` | Lancer en arrière-plan, arrêter, interroger |
| `grimoire cockpit open` | Ouvrir le cockpit en cours d'exécution dans le navigateur |

`serve`, `start` et `open` ouvrent `workspace/index.html` — la même coque que
`grimoire serve`, avec le niveau Flotte et le sélecteur de projet en plus
(`?project=<slug>`). `portfolio.html` (et les autres pages historiques
partagées avec l'atelier) reste servie mais redirige vers l'espace
correspondant ; `?legacy=1` sur l'ancienne URL l'ouvre sans redirection. Voir
[l'ADR de la vue de travail](../../explications/decisions/adr-006-vue-de-travail.md).

---

## `grimoire serve` *(alias déprécié)*

`grimoire serve` est un alias de [`grimoire cockpit serve`](#cockpit-multi-projets)
(#351) : les deux commandes ouvraient la même UI, l'une en mono-projet, l'autre
en multi-projets — `cockpit serve` fait maintenant les deux, en s'ouvrant
directement sur le projet courant quand on le lance depuis un projet Grimoire.
`serve` reste utilisable pour ne pas casser les scripts existants ; il affiche
un avertissement et délègue à `cockpit serve`. `--project-root` est ignoré :
lancer la commande depuis le projet visé suffit.

Pour une UI custom ou une racine de kit explicite (`--ui-dir`, `--kit-root`),
utiliser la forme longue : `python -m grimoire.tools.forge_server`.

### La vue de travail, page par défaut

`--open` ouvre `workspace/index.html` — la coque unique décrite par
`web/DESIGN-SPEC-workspace-2026-09.md` et son
[ADR](../../explications/decisions/adr-006-vue-de-travail.md). Les pages historiques
(`atelier.html`, `kanban.html`, `observability.html`, `memory.html`,
`blueprints.html`, `patterns.html`, `extensions.html`, `labs.html`,
`documentation.html`) restent servies mais redirigent désormais vers l'espace
de la coque qui les remplace ; ajouter `?legacy=1` à l'URL sert encore
l'ancienne page, sans redirection.

#### Source : IntelliSense déterministe (issue 280)

L'éditeur de l'espace Source colorie le frontmatter, les blocs `<agent>`/
`<activation>`/`<step>`, les placeholders (`{project-root}`, `{{…}}`) et les
identifiants d'agents, de patterns et de workflows, sans dépendance ni
bundler (surcouche par recouvrement sur la `<textarea>`, voir
[ADR-006](../../explications/decisions/adr-006-vue-de-travail.md) D2). Un correcteur signale un chemin du
kit qui ne se résout pas (même logique que `grimoire doctor`), un agent
routé mais absent du manifeste, une clé de frontmatter inconnue, un terme du
glossaire cité mais non défini, ou un pattern/workflow inconnu du catalogue —
recalculé à l'enregistrement et au repos de saisie, et repris dans l'onglet
Problèmes du dock. La complétion (`Ctrl+Espace`, ou déclenchée par `{`, `@`,
`/`) propose agents, workflows, patterns, chemins du kit et clés de schéma ;
le survol d'un identifiant lié à une entrée du glossaire ouvre la même bulle
épinglable que le reste de la vue de travail. Tout vient de
`GET /api/workspace/language` (`src/grimoire/tools/workspace_language.py`),
servi en lecture seule sur les deux hôtes — voir le tableau des routes dans
`web/workspace/README.md`.

#### Source : suggestions par modèle local (issue 280, voie 2)

Derrière cette IntelliSense déterministe, jamais à sa place : un petit
modèle local (Ollama) peut proposer du *texte* — compléter une clause
d'emploi (`use_when`/`dont_use_when`), rédiger le corps d'un agent ou d'une
étape de workflow, expliquer un diagnostic — mais ne fait jamais autorité sur
les identifiants, les chemins ou les diagnostics, qui restent ceux de
`workspace_language.py`. Deux conditions cumulatives, sinon l'interface ne
montre rien et ne tente rien :

1. le doctor voit Ollama sur le poste (même sonde que
   `grimoire providers audit` — `GET /api/tags`, sans dépenser de quota) ;
2. `project-context.yaml` déclare `source.assist.model: "<modèle>"` — vide
   par défaut. Le modèle se choisit parmi ceux réellement présents
   (`ollama list`) ; `qwen3-coder:30b`, ou le plus petit modèle codeur
   trouvé, est un défaut raisonnable.

`GET /api/workspace/assist` (sans coût, jamais d'appel au modèle) dit si le
bouton **Suggérer** de l'éditeur doit apparaître ; `POST /api/workspace/assist`
(chemin, texte, position, intention) l'appelle réellement, avec un délai
borné à 10 s. La réponse porte la suggestion brute du modèle *et* la liste
de ce qu'elle cite sans figurer dans le paquet de langage (agent, workflow,
pattern ou chemin) — jamais corrigée à la place de l'utilisateur, seulement
marquée « inconnu ». Le panneau d'aperçu (`Ctrl+Maj+Espace`, ou le bouton
**Suggérer**) affiche cette suggestion à côté de l'éditeur ; **Insérer** la
place au curseur par le même chemin que la frappe clavier (colorisation et
diagnostics se recalculent dessus), **Ignorer** referme le panneau sans rien
écrire. Aucun envoi automatique à la frappe, aucun fournisseur distant,
aucune clé, aucune donnée hors de la machine — le module
`src/grimoire/tools/source_assist.py` ne contacte jamais qu'Ollama en local.
Si l'URL Ollama résolue (`OLLAMA_HOST`) ne pointe pas vers une adresse de
bouclage, l'appel est refusé (nommé) sauf déclaration explicite de
`source.assist.allow_lan: true`.

Un modèle qui n'est pas encore chargé en mémoire (`GET /api/ps`) n'est pas
une panne : `GET /api/workspace/assist` le déclenche lui-même en tâche de
fond (un préchauffage sans génération) et répond « chargement du modèle,
réessayez dans quelques secondes » — le bouton **Suggérer** reste visible
mais désactivé, et l'éditeur re-sonde le statut toutes les 3 s jusqu'à ce
qu'il devienne prêt. Le délai de 10 s ci-dessus ne s'applique qu'à un modèle
déjà résident (issue #450).

### Projets de la machine

Le bouton de projet, en haut de la barre latérale, ouvre le sélecteur. Trois
entrées, parce qu'il y a trois situations :

| Entrée | Ce qu'elle fait |
| --- | --- |
| Liste | Les projets déjà connus de la machine (registre partagé avec `grimoire cockpit`). Un clic re-route le serveur sur le projet choisi. |
| Parcourir | Navigation dossier par dossier depuis `$HOME`, ou chemin absolu collé. Les projets sont signalés au passage. |
| Scanner | Parcours borné d'une racine (profondeur 4 par défaut). Le scan **propose** : rien n'est enrôlé sans sélection explicite. |

Le projet servi est enrôlé au registre à l'ouverture, s'il porte un marqueur
(`.git`, `project-context.yaml`, `_grimoire/`). Ouvrir un projet n'écrit rien
dans son arbre : registre et couche de données vivent sous
`~/.grimoire/`. Purger les entrées mortes : `grimoire cockpit prune`.

### Données affichées : celles du projet, ou rien

Observatoire, Mémoire et Kanban lisent une couche générée sur **le projet
servi** (`gen-site-data.py`, régénérée en arrière-plan au démarrage et à chaque
changement de projet). Tant qu'elle n'existe pas, ces pages sont vides et le
disent.

Le site embarqué dans la wheel contient aussi l'instantané de la vitrine
publique — des projets de démonstration et les chiffres du dépôt du kit. Il
n'est jamais servi en local : afficher 141 entrées mémoire et des traces
d'agents vieilles de deux minutes pour un projet qui n'a rien lancé est pire
qu'une page vide. Seules les références du kit (catalogue de patterns,
marketplace, anatomie) restent servies telles quelles.

L'état de la couche est visible sur le tableau de bord, chip **données** ; un
clic la régénère (`POST /api/data/refresh`).
