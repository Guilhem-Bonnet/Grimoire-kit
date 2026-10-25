---
audience: décision
---

# ADR-006 — La vue de travail : coque unique, contrats d'API, cinq lots

- **Statut** : accepté
- **Date** : 2026-09-06
- **Décide pour** : `grimoire cockpit serve` (atelier mono-projet et cockpit multi-projets,
  fusionnés par #351 ; `grimoire serve` n'en est plus qu'un alias déprécié)
- **Source** : `web/DESIGN-SPEC-workspace-2026-09.md`, validée le 2026-09-05.
  Revue préalable : `web/DESIGN-REVIEW-2026-09.md`
- **Remplace** : rien. **Complète** : ADR-005 (Mission Ledger source de vérité)

## Contexte

La spécification dit ce que l'interface fait et à quoi elle ressemble. Elle ne
dit pas comment la coder. Cet ADR tranche le comment, et pose le squelette sur
lequel cinq lots d'implémentation travaillent en parallèle.

Ce qui existe aujourd'hui, mesuré et non supposé :

- **14 pages HTML** dans `web/`, dont 4 vitrine, 5 outil, 4 partagées qui
  changent d'habillage selon `localStorage`, et `portfolio.html` — un outil
  habillé en vitrine. Chacune amène son chrome : `forge-nav.js` (17 Ko) ou
  `atelier-nav.js` (32 Ko), les deux s'auto-désactivant selon le mode.
- **Un fichier de tokens qui n'est pas la source unique** : `forge-tokens.css`
  déclare 49 variables, `forge-landing.css` en redéclare 16 dans son propre
  `:root`, et `kanban.html` recopie les valeurs dans un `:root` inline avec des
  noms divergents. La revue a compté 25 tailles de police rendues sur une page,
  162 éléments sous 10,5 px sur une autre, 36 styles en échec de contraste.
- **17 routes `/api/*`** consommées par le front. Le cockpit en sert un
  sous-ensemble, ce qui oblige `memory.html` à tenter deux chemins différents.
- **Aucune fonte embarquée** : tout passe par Google Fonts, y compris quand le
  serveur est local et hors ligne.
- **Rien** pour les tâches, la trace, les fichiers par étage, le diff contre le
  kit, l'exécution d'une commande, le glossaire. L'espace Source est un écran
  nouveau, sans aucune API derrière lui.

## Décisions

### D1 — Vanilla JS en modules ES, sans bundler

**Décidé.** La coque est du JavaScript natif en modules ES, chargés par
`<script type="module">`, sans étape de build.

`web/` est `force-include` dans la wheel (`pyproject.toml`) : ce qui est dans
l'arbre est ce qui est livré. Introduire un bundler ajouterait une étape entre
le dépôt et le paquet — donc un artefact compilé à committer ou une étape de
build dans `publish.yml`, et un `web/` dont on ne peut plus dire, en le lisant,
ce que l'utilisateur reçoit. Le kit ne construit rien aujourd'hui ; ce n'est pas
cette PR qui doit l'apprendre.

Le coût réel est un import dynamique par espace (`import('./spaces/x.js')`),
soit six requêtes sur du loopback. C'est moins que ce que `blueprints.html`
charge aujourd'hui en scripts statiques.

*Écarté* : Vite + Preact. Gain réel sur l'éditeur de graphe du lot 3, mais il
achète une chaîne de build pour un serveur qui tourne en local et sert des
fichiers depuis un paquet Python.

### D2 — L'éditeur de Source : textarea améliorée en v1, CodeMirror en question ouverte

**Décidé pour la v1 :** un `<textarea>` avec numérotation de lignes, tabulation
et sauvegarde ; le diff est rendu par le serveur en unifié, pas par l'éditeur.

CodeMirror 6 en MIT pèse 400 à 600 Ko une fois ses paquets `@codemirror/*`
réunis — à embarquer dans la wheel, sans bundler, en modules ES avec leurs
dépendances croisées. La spécification met déjà l'IntelliSense et la
colorisation propres à l'éditeur agentique **hors périmètre** (§1, backlog).
Livrer 500 Ko pour une coloration syntaxique sur des fichiers Markdown et YAML
n'est pas justifié tant que le geste principal — ouvrir, comparer, prendre un
override — n'est pas éprouvé.

La décision est **révisable par le lot 5** si l'usage réel montre que l'édition
sans coloration est le point de friction. La frontière est propre : le diff
vient du serveur, donc changer d'éditeur ne change aucun contrat.

### D3 — Un dossier neuf, `web/workspace/`, et non une refonte sur place

**Décidé.** La coque vit dans `web/workspace/`. Les 14 pages existantes ne sont
pas touchées et restent servies.

Refondre sur place aurait obligé à casser `forge-nav.js` et `atelier-nav.js`,
donc les 4 pages vitrine hors périmètre, dans la même PR. Un dossier neuf rend
les cinq lots parallélisables, laisse une sortie de secours pendant tout le
chantier, et fait du basculement une décision explicite plutôt qu'un effet de
bord.

### D4 — `tokens.css` est la source unique de couleur, et il est neuf

**Décidé.** `web/workspace/tokens.css` porte tous les tokens de la spec §2.
`forge-tokens.css` n'est ni étendu ni renommé.

La spec dit « aucune couleur codée en dur hors de `forge-tokens.css` ». Ce
fichier sert les pages vitrine avec des noms qui ne sont pas ceux de la spec
(`--elev-1` contre `--e1`, pas de `--termink`, pas de `--bar`). Le fusionner
aurait forcé à renommer 49 variables dans 8 feuilles et 14 pages — donc à
toucher la vitrine, hors périmètre. La règle est donc portée à l'identique sur
un fichier neuf, et **un test l'applique** (`test_workspace_tokens.py`) sur
tout `web/workspace/`.

### D5 — Les lectures passent par la table partagée, les écritures non

**Décidé.** Un préfixe unique `/api/workspace/`, et deux portes différentes.

Le dépôt a déjà le bon point d'extension : `forge_routes.api_get` avec le
protocole `ReadableForgeAPI`, explicitement conçu pour que la même surface serve
les deux hôtes. **Une** délégation y suffit :

```python
payload = workspace_get(api.project_root, path, query)
if payload is not WORKSPACE_UNHANDLED:
    return payload
```

L'atelier appelle avec sa racine unique, le cockpit avec celle qu'il a résolue
depuis `?project=` — sans une ligne dans `cmd_cockpit.py`. C'est ce qui rend la
clause « chaque route honore la cible » (spec §5, §6.8) *vérifiable* et non
promise : `test_workspace_routes.py` sert deux projets réels par les deux hôtes
et compare les racines rendues.

Les écritures sont câblées **uniquement** dans `forge_http.do_POST`. Le cockpit
se déclare `readOnly: true` depuis sa création ; lui donner de quoi réclamer une
tâche ou créer un override dans un dépôt qu'il ne sert pas serait une régression
de gouvernance. Un test le vérifie route par route.

### D6 — Trois modules neufs, zéro ligne dans `forge_server.py`

**Décidé.** Le métier va dans `src/grimoire/tools/workspace_{api,exec,routes}.py`.

`forge_server.py` fait 1361 lignes pour un plafond de 1500
(`scripts/check-code-ratchet.py`, règle R2). Y ajouter les vues de la vue de
travail l'aurait mené au seuil, et la prochaine personne aurait payé
l'extraction. Les fichiers hérités touchés le sont d'un total de **17 lignes** :
`forge_routes.py` (+10, délégation en lecture et `project_root` au protocole) et
`forge_http.py` (+14, délégation en écriture).

`workspace_api.py` n'expose que des fonctions pures `(project_root, …) → dict`.
Pas d'état, pas de `ForgeAPI`, pas de notion d'hôte : c'est ce qui les rend
testables sans serveur et réutilisables par un futur outil MCP.

### D7 — La Console : liste blanche de sous-commandes, jamais de shell

**Décidé.** `workspace_exec.ALLOWED` déclare 23 sous-commandes `grimoire` de
lecture, chacune avec ses drapeaux acceptés et son nombre d'arguments
positionnels. Tout le reste est refusé **avant** exécution, en nommant ce qui
est ouvert.

Quatre garanties : pas de `shell=True` (argv littéral), liste blanche et non
liste noire, arguments bornés et sans octet nul ni retour à la ligne, délai
maximal et sortie tronquée. `init`, `up`, `migrate`, `serve`, `cockpit`,
`upgrade`, `ext` et `plugins` sont absents bien qu'étant des sous-commandes
`grimoire` : elles réécrivent l'arbre du projet ou parlent au réseau, et un
terminal dans un onglet n'est pas le bon geste pour ça.

`grimoire doctor` passe par la même liste : l'onglet Problèmes n'a pas de canal
privilégié vers le shell. Le jour où le diagnostic sort de `cli/app.py` et
devient importable, seule `doctor_view()` change.

### D8 — Le glossaire est un fichier du kit, en YAML, sous `framework/`

**Décidé.** `framework/glossary.yaml`, 57 entrées, schéma
`grimoire-glossary/v1`, une entrée par concept : `id`, `nom`, `définition`,
`raccourci`, `termes`, `doc`.

Deux vérifications faites, pas supposées :

- **Le gel de `framework/` porte sur `.py` et `.sh`.** `check-code-ratchet.py`,
  règle R1, ne classe en zone gelée que ces deux extensions. Un `.yaml` neuf y
  est légitime — vérifié, le ratchet passe.
- **`scripts/gen-kit-hashes.py` doit être régénéré.** `framework/` est un
  `SHIPPED_ROOTS`. Un fichier livré absent du catalogue est lu comme une
  personnalisation du projet par `grimoire migrate`, et **gelé hors de toutes
  les mises à jour suivantes** — le dégât apparaît des mois plus tard sous la
  forme « le kit ne se met plus à jour ». Régénéré : un digest ajouté, aucun
  retiré. Un test le vérifie à chaque exécution.

Le glossaire se surcharge comme n'importe quel fichier du kit : la résolution
passe par `core/layout.resolve()`, donc un projet peut adapter son vocabulaire
sans forker.

### D9 — Le contrat d'un espace : `mount(root, ctx)`

**Décidé.** Chaque espace exporte `mount(root, ctx)`. `ctx` est figé par ce
squelette (voir `web/workspace/README.md`). Deux règles opposables : aucun
module d'espace n'appelle `fetch`, aucun n'écrit une couleur.

C'est ce qui découple les lots 3, 4 et 5 : ils touchent des fichiers disjoints
et ne se voient que par `ctx`.

## Ce que la mise en œuvre a corrigé dans la spécification

Trois écarts trouvés **en mesurant**, pas en relisant. Ils sont ici parce
qu'ils modifient des valeurs validées le 2026-09-05 et méritent un accord
explicite.

| Sujet | Ce que dit la spec | Ce que mesure le test | Ce qui a été fait |
|---|---|---|---|
| `--ink3` clair | « ≥ 4,5:1 sur `--e1` (4,8 et 4,6 mesurés) » | `#767c85` sur `#f3f3f0` = **3,78:1** | `#656a72` — 4,90 sur `--e1`, 4,51 sur `--bar` |
| `--ink3` sombre | idem | `#7e858f` tient 4,69 sur `--e1` mais **4,35 sur `--bar`** (barre d'état, écho du dock) | `#818891` — 4,88 et 4,52 |
| Action primaire, clair | `--pri #d9481a`, texte `#ffffff` | **4,29:1** pour un libellé de bouton à 13 px | `--pri #d24619` — 4,54:1. L'encre sombre dessus (4,44) ne suffisait pas non plus |

Deux écarts de conception, moins lourds :

- **Le rail en densité Découverte.** La spec le donne à 44 px « avec
  libellés ». Un libellé de 13 px n'y tient pas ; l'écrire à 10 px aurait
  reproduit exactement le défaut que la revue a compté 270 fois. Le rail
  s'élargit à 84 px en Découverte, et reste à 44 px en Concentration.
- **`--ink3` sur `--e2` et `--e3`** reste sous 4,5:1 dans les deux thèmes. Ces
  surfaces portent `--ink2` ou `--ink` dans la coque actuelle, donc le défaut
  n'existe pas à l'écran. Non corrigé : changer davantage une palette validée
  sans usage constaté n'est pas un geste d'architecte. **À arbitrer si le lot 1
  ou 3 pose de l'encre tertiaire sur une carte ou une bulle.**

## Basculement

Les 14 pages restent servies pendant tout le chantier. Le basculement est une
décision, pas un effet de bord :

1. **Maintenant → lot 5 inclus.** `web/workspace/index.html` est atteignable
   mais n'est la page par défaut d'aucun hôte. `cmd_serve.py` continue d'ouvrir
   `atelier.html`, `cmd_cockpit.py` `portfolio.html`.
2. **Quand les cinq lots sont mergés.** `cmd_serve.py` et `cmd_cockpit.py`
   ouvrent `workspace/index.html` ; les anciennes pages restent servies et
   redirigent vers l'espace qui les remplace (`kanban.html` →
   `workspace/#executer`, etc.).
3. **Une version après.** Suppression de `atelier.html`, `kanban.html`,
   `observability.html`, `memory.html`, `blueprints.html`, `patterns.html`,
   `extensions.html`, `labs.html`, `portfolio.html`, `documentation.html`, de
   `atelier-nav.js`, `atelier.css`, `forge-observatory.js`, `bp2-*` et
   `bp2*.css`. Les 4 pages vitrine (`index.html`, `demo.html`, `anatomy.html`,
   `game-ui.html`) et leur chrome (`forge-nav.js`, `forge-base.css`,
   `forge-landing.css`, `forge-tokens.css`, `forge-motion.*`) **restent** :
   elles sont hors périmètre.

Le pas 3 est une suppression de surface publique : il relève d'une mineure au
sens d'ADR-002, et il a besoin de son propre accord.

## Les cinq lots

Fichiers disjoints, interfaces figées par ce squelette.

| Lot | Fichiers possédés | API consommées | Critères d'acceptation | Ordre |
|---|---|---|---|---|
| **1 — Coque et tokens** | `index.html`, `tokens.css`, `shell.css`, `shell.js`, `fonts/` | `/api/status`, `/api/health` | Trois états de panneau au clavier et à la souris ; 1 à 5, ⇧⌘F, ⌘K ; deux thèmes, deux densités ; fontes woff2 embarquées, plus aucun appel à Google Fonts ; test des tokens vert dans les deux thèmes ; captures avant/après | 1er — les autres en dépendent |
| **2 — Glossaire et infobulles** | `glossary.js`, `framework/glossary.yaml` | `/api/workspace/glossary` | Survol 500/800 ms, Alt fige, bulle enfant, 3 niveaux, Échap ferme la pile ; réduction en Concentration ; le test refuse un terme cité sans entrée, sur le DOM comme sur les sources | Parallèle au 1, merge après |
| **3 — Concevoir** | `spaces/concevoir.js` (+ `spaces/concevoir.css`) | `/api/blueprints`, `/api/blueprints/<id>` (GET/PUT), `/validate`, `/simulate`, `/compile`, `/api/primitives`, `/api/cost-model` | Zoom Projet → Workflow → Nœud ; Carte, Board, Liste ; palette de nœuds ; inspecteur à 4 onglets ; validation, simulation, compilation sur un blueprint réel | Après le 1 |
| **4 — Piloter, Exécuter, Observer, Mémoire** | `spaces/piloter.js`, `executer.js`, `observer.js`, `memoire.js` | `/api/projects`, `/api/health`, `/api/memory/status`, `/api/otel`, `/api/events/log`, `/api/workspace/tasks*` | Les corrections de la revue §4.1, §4.3, §4.4, §4.5 : `ci_status` et `commits_total` au portefeuille, `antifragile: null` = « pas encore mesurée », 8 colonnes annoncées et montrées, transition suivante la plus lisible, observatoire sans trace = un seul bloc vide | Après le 1, parallèle au 3 |
| **5 — Source et Console** | `spaces/source.js` (+ `spaces/source.css`) | `/api/workspace/files`, `/file`, `/file/diff`, `/file/override`, `/file/write`, `/command`, `/commands`, `/doctor` | Ouvrir, éditer un fichier du kit crée l'override, diff contre le kit, provenance correcte, `grimoire doctor` vert après ; une commande exécutée depuis le dock, une commande hors `grimoire` refusée | Après le 1, parallèle au 3 et au 4 |

### Où deux lots se touchent, et comment ils sont découplés

| Contact | Découplage |
|---|---|
| Tous les lots ↔ la coque | `mount(root, ctx)` et `ctx` figés par ce squelette. Un lot qui a besoin d'autre chose le demande au lot 1 ; il ne se sert pas dans le DOM de la coque. |
| Lot 2 ↔ tous | Les espaces citent `data-term="<id>"`. Ils ne connaissent pas la mécanique de bulle ; le lot 2 la câble globalement, une fois. |
| Lot 5 ↔ lot 1 (le dock) | Le lot 1 possède la coque du dock et expose `ctx.dock.log/clear/setTab/echo`. Le lot 5 possède ce que la Console **envoie**. Aucun des deux ne touche le DOM de l'autre. |
| Lot 3 ↔ lot 4 (la vue Board) | Deux boards différents — nœuds pour l'un, tâches pour l'autre. Ils partagent les primitives de `shell.css` (`.seg`, `.chip`, `.dot`), pas un composant. Si un composant de board s'impose, il remonte au lot 1. |
| Lot 4 ↔ lot 5 (la timeline) | L'onglet Timeline du dock lit `/api/workspace/tasks/<id>/trace`. Le lot 4 publie l'identifiant sélectionné via `ctx.dock.setTab('timeline')` ; le lot 5 ne le lit pas. |
| Lot 1 ↔ lot 2 (le mode Concentration) | La densité est un attribut sur `<html>` (`data-density`). Le lot 2 la lit, il ne la pilote pas. |

## Conséquences

**Bonnes.**
Les deux hôtes servent la même coque, et un test le prouve route par route
plutôt que de le promettre. Le vocabulaire a une source unique, et citer un
terme sans le définir fait échouer la CI. La surface de couleur est mesurée à
chaque exécution, dans les tokens et sur le DOM rendu — c'est ce qui a trouvé
les trois écarts ci-dessus, qu'aucune relecture n'avait vus. Cinq lots peuvent
avancer en parallèle sans se marcher dessus.

**À payer.**
`web/` grossit avant de maigrir : les 14 pages et la coque coexistent pendant
tout le chantier. Le harnais Playwright n'est pas dans la CI — Chromium n'est
pas une dépendance du kit — donc les mesures sur le DOM sont locales tant que
personne n'ajoute un job dédié ; le test des tokens couvre la même règle sans
navigateur et tourne partout. `--pri` et `--ink3` diffèrent de la spec validée
et demandent un accord.

**Renoncé pour l'instant.**
Pas de coloration syntaxique dans l'éditeur de Source (D2). Pas de flux SSE
pour les traces : le dock interroge, il n'écoute pas — `/api/events` existe et
pourra être branché par le lot 4 si le rafraîchissement se voit.

## Références

- `web/DESIGN-SPEC-workspace-2026-09.md` — la spécification
- `web/DESIGN-REVIEW-2026-09.md` — la revue qui l'a précédée
- `web/workspace/README.md` — comment lancer, où brancher chaque lot
- ADR-002 — politique SemVer, pour le pas 3 du basculement
- ADR-005 — le Mission Ledger est la source ; le board en est une projection
