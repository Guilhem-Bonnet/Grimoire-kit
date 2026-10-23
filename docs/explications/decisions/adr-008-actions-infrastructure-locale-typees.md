---
audience: décision
---

# ADR-008 — Actions d'infrastructure locale typées depuis le cockpit

- **Statut** : proposé (en attente de la décision de Guilhem)
- **Date** : 2026-10-17
- **Décide pour** : `grimoire cockpit serve` (cockpit multi-projets) et, à
  périmètre égal, `grimoire serve` (atelier mono-projet)
- **Remplace** : rien. **Complète** : ADR-006 D7 (la Console) **sans élargir la
  Console** ; s'appuie sur ADR-005 (le ledger reste la source des tâches)
- **Débloque** : la tâche « actions typées A et B » (composer la mémoire,
  démarrer une pile, « redémarrer avec la machine ») et celle des emplacements
  (attribuer, retirer, séparer un projet)

## Contexte

Le cockpit sait *montrer* l'état de l'infrastructure locale d'un projet : piles
mémoire (Qdrant, Neo4j), emplacements de mémoire partagés, extras Python. Il ne
sait pas la *modifier*. Aujourd'hui l'utilisateur copie une commande depuis la
page et la lance dans un terminal.

Ce qui existe, vérifié dans le dépôt :

- `src/grimoire/tools/memory_setup.py` sépare déjà **plan** et **application** :
  `build_memory_plan(...)` calcule un `MemoryPlan` (sondes de services,
  différence de configuration), `apply_memory_plan(plan)` l'exécute et renvoie
  la liste de ce qu'il a fait. C'est la forme que cet ADR généralise.
- `framework/memory/docker-compose.memory.tpl.yml` et
  `docker-compose.memory-target.tpl.yml` : les piles du kit, avec
  `restart: unless-stopped` et un volume nommé (`grimoire-qdrant-storage`).
  Le démarrage avec la machine est donc **déjà** le comportement des piles du
  kit tant que le démon Docker est activé.
- ADR-006 D7 : la Console exécute une **liste blanche de 23 sous-commandes de
  lecture**. `init`, `up`, `migrate`, `serve`, `cockpit`, `upgrade`, `ext`,
  `plugins` en sont exclues exprès : « un terminal dans un onglet n'est pas le
  bon geste pour ça ».
- ADR-006 D5 : les lectures passent par la table partagée, les écritures sont
  câblées uniquement dans l'hôte d'atelier ; le cockpit se déclare
  `readOnly: true`.

Le besoin est donc un geste **autre que la Console** pour les quelques
modifications d'infrastructure légitimes — un geste qui se prévoit, se montre
avant de s'exécuter, se vérifie après et se défait.

## Décisions

### D1 — Une action est un objet typé, pas une commande

**Proposé.** Chaque action du catalogue déclare cinq éléments, tous lisibles
sans exécuter quoi que ce soit :

| Élément | Contenu |
|---|---|
| **Préconditions** | Ce qui doit être vrai avant : projet résolu et enrôlé, démon Docker joignable, port libre, fichier cible présent et non modifié depuis le plan. Une précondition fausse bloque `plan`, avec la raison. |
| **Plan** | La liste ordonnée et complète des effets prévus, calculée sans écriture : fichiers touchés avec différence, commande exacte, services concernés. Le plan porte une empreinte (hachage du contenu cible) et une échéance. |
| **Effets** | Ce que `apply` modifie, et seulement cela. Un effet hors plan est une erreur, pas un bonus. |
| **Vérification** | Une sonde exécutée après `apply` (service joignable, configuration relue, `grimoire doctor` sans régression). L'action n'est « réussie » que si la vérification passe. |
| **Inverse** | Le retour arrière, calculé au moment du plan et enregistré au journal : restauration du fichier, arrêt du service démarré, retrait de l'emplacement attribué. Une action sans inverse documenté n'entre pas au catalogue. |

Le catalogue est une table en code (`action_id` → spécification), pas une
saisie libre : l'interface n'envoie jamais de commande, elle envoie
`{action_id, paramètres}`, et les paramètres sont validés contre un schéma.

### D2 — Quatre classes, décidées par ce que l'action touche

**Classe A — configuration du projet.** Fichiers du projet que le kit possède
déjà : `grimoire.yaml` (bloc `memory`), `project-context.yaml`, emplacements de
mémoire. Action = édition ciblée d'une clé, jamais réécriture d'un fichier.
Inverse : restauration du contenu antérieur depuis le journal. Exemples :
« composer la mémoire » (appliquer un `MemoryPlan`), attribuer ou retirer un
emplacement.

**Classe B — services locaux.** Démarrer, arrêter, redémarrer une pile **issue
d'un compose du kit** (les deux gabarits de `framework/memory/`), nommée par son
identifiant de catalogue et non par un chemin. Règles :

- le fichier compose est celui du kit, rendu par le gabarit ; l'interface ne
  fournit ni chemin ni option brute ;
- **jamais `-v`** : `down` ne supprime pas les volumes, aucune action de classe B
  n'efface de données. Un arrêt est réversible par un démarrage ;
- « redémarrer avec la machine » bascule `restart: unless-stopped` /
  `restart: "no"` par pile, en classe A sur le fichier rendu puis classe B pour
  recréer le conteneur ; défaut conservé : `unless-stopped`.

**Classe C — extras Python.** `pip`/`uv` et les extras du kit **ne sont jamais
exécutés par l'interface**. L'action produit la *commande à copier* — avec le
bon installeur résolu pour l'environnement du projet — et l'affiche. Le plan est
la commande ; `apply` n'existe pas pour cette classe. Raison : installer du code
dans l'environnement de l'utilisateur est l'exact geste qu'ADR-006 D7 a écarté,
et le réseau n'est pas un effet que le cockpit doit déclencher.

**Classe D — données.** Contenu des bases (points Qdrant, nœuds Neo4j),
volumes, mémoire elle-même. **Hors catalogue** pour cet ADR. Toute modification
de données passe par le gestionnaire de mémoire du kit (`MemoryManager`) et ses
propres garde-fous, ou par une commande de CLI que l'utilisateur lance. Un
checkpoint destructif depuis le cockpit exige une décision séparée : il demande
une attestation équivalente à celle des lots de sécurité de la mission, qui
n'existe pas encore.

### D3 — Trois routes : plan, apply, rollback

**Proposé.** Un préfixe `/api/workspace/actions/` et trois verbes :

| Route | Effet | Écrit ? |
|---|---|---|
| `POST …/actions/<id>/plan` | Valide les paramètres, vérifie les préconditions, renvoie le plan, son empreinte et l'identifiant de plan. | Non (seul le plan est consigné au journal) |
| `POST …/actions/<id>/apply` | Reçoit un identifiant de plan **et son empreinte** ; refuse si l'état a changé depuis le plan ; exécute les effets ; lance la vérification ; consigne le résultat. | Oui |
| `POST …/actions/<id>/rollback` | Reçoit l'identifiant d'une exécution consignée ; applique l'inverse enregistré ; vérifie. | Oui |

Le plan et l'application sont deux requêtes distinctes : l'interface montre le
plan, l'utilisateur confirme, et seul ce plan-là est exécutable. Une requête
`apply` sans plan correspondant, expiré ou périmé est refusée.

Une lecture `GET …/actions` liste le catalogue (identifiants, classes,
préconditions évaluées) et `GET …/actions/journal` relit le journal. Les
lectures suivent ADR-006 D5 (table partagée). Les trois écritures sont câblées
dans le seul module d'actions, avec le même principe de modules neufs que
ADR-006 D6 : aucun métier dans `forge_server.py`.

**Condition de mise en service.** Ces routes d'écriture ne sont servies par le
cockpit qu'une fois en place la garde d'écriture commune à toutes les écritures
du cockpit (lots 1 et 2 de la vague 0 du plan). Tant que cette garde n'est pas
livrée, le catalogue reste visible et chaque action s'affiche en mode « copier
la commande », comme la classe C. Cet ADR ne modifie pas le traitement des
requêtes d'écriture existant.

### D4 — Le journal est la preuve, et la source du retour arrière

**Proposé.** Un journal en ajout seul, `_grimoire-runtime-output/actions/journal.jsonl`
(hors de l'arbre versionné), une ligne par événement :

`plan` → `apply_started` → `effects` → `verification` → `applied | failed` →
`rollback_applied`.

Chaque ligne porte : horodatage, identifiant d'exécution, `action_id`, classe,
projet, empreinte du plan, paramètres validés, effets réellement observés,
résultat de la vérification, et — pour les classes A — le contenu antérieur
nécessaire à l'inverse. Aucun secret n'y est écrit (les variables d'environnement
sont consignées par nom, jamais par valeur).

Une exécution dont la vérification échoue **n'est pas annulée en silence** :
elle est marquée `failed`, son inverse reste proposé, et l'interface le dit.
Un journal illisible ou non inscriptible bloque `apply` : pas de journal, pas
d'effet.

### D5 — Ce que cet ADR n'élargit pas

- **La Console reste en lecture.** ADR-006 D7 n'est pas modifié : la liste
  blanche de 23 sous-commandes, l'absence de `shell=True`, l'exclusion de
  `init`, `up`, `migrate`, `serve`, `cockpit`, `upgrade`, `ext`, `plugins`
  restent en vigueur. Les actions typées n'ajoutent aucune entrée à
  `workspace_exec.ALLOWED` et ne passent pas par elle ; elles complètent D7 par
  un autre chemin, plus étroit, où l'on ne tape rien.
- **Pas d'exécution d'agents** depuis le cockpit, pas d'écriture multi-projets
  en masse : une action vise un projet à la fois, celui que l'utilisateur a
  sélectionné et que la requête nomme.
- **Pas d'accès réseau ajouté** : les actions n'appellent que le démon local et
  les ports de la machine. Tirer une image (`docker pull`) est un effet
  réseau : il est signalé dans le plan, jamais implicite.
- **Pas de nouveau moteur** : ADR-005 reste vrai, ce qui touche aux tâches passe
  par le service du ledger, pas par ce catalogue.

## Alternatives écartées

- **Élargir la liste blanche de la Console** (`up`, `init`…) : mélange la lecture
  libre et l'écriture, et donne à l'utilisateur la charge de composer des
  arguments qu'on sait déjà valider.
- **Un bouton qui lance une commande préfabriquée sans plan** : pas de
  prévisualisation, pas d'inverse, pas de preuve.
- **Exécuter les extras (classe C)** : modifie l'environnement Python de
  l'utilisateur et déclenche du réseau ; la commande affichée suffit.
- **Journal dans la base de mémoire** : couple la preuve au service qu'elle est
  censée contrôler.

## Conséquences

**Bonnes.** Chaque modification d'infrastructure se montre avant, se vérifie
après et se défait ; le journal sert de preuve. Le périmètre est borné par une
table, testable route par route, et ne dépend pas de la bonne foi d'une chaîne
de caractères.

**À payer.** Un catalogue à maintenir : une action de plus, c'est une
spécification, un inverse et ses tests. Le retour arrière de classe B rétablit
l'état des conteneurs, pas celui de données qu'un service aurait écrites entre
temps — d'où la classe D hors catalogue. Les routes d'écriture attendent la
garde d'écriture commune (D3).

**Renoncé pour l'instant.** Actions de classe D, exécution des extras,
orchestration de plusieurs projets en un geste.

## Critères de vérification de la mise en œuvre

Chacun doit être prouvé par un test avant que cet ADR passe à « accepté » :

1. aucune action sans les cinq éléments de D1 (test sur la table du catalogue) ;
2. aucune commande de classe B ne contient `-v` ni `--volumes` ;
3. la classe C n'expose aucune route `apply` ;
4. `apply` refuse un plan périmé, expiré ou d'une autre empreinte ;
5. `apply` est refusé quand le journal n'est pas inscriptible ;
6. `rollback` rétablit l'état antérieur de chaque action de classe A ;
7. `workspace_exec.ALLOWED` est inchangé par la mise en œuvre.

## Décision

À renseigner par Guilhem — **accepté**, **amendé** ou **refusé**, avec date et
motif. Tant que ce champ est vide, le statut reste « proposé » et aucune
tâche de mise en œuvre ne démarre.

| Décision | Date | Motif / amendements |
|---|---|---|
| _(en attente)_ | | |

## Références

- ADR-005 — le Mission Ledger est la source des tâches
- ADR-006 — D5 (lectures et écritures), D6 (modules neufs), D7 (la Console)
- `src/grimoire/tools/memory_setup.py` — `build_memory_plan` / `apply_memory_plan`
- `framework/memory/docker-compose.memory.tpl.yml`,
  `framework/memory/docker-compose.memory-target.tpl.yml`
