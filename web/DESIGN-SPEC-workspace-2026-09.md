# Spécification — la vue de travail Grimoire (atelier et cockpit)

Statut : validée par Guilhem le 2026-09-05. Source de vérité pour la refonte de
`grimoire serve` et `grimoire cockpit serve`. Les maquettes qui l'illustrent
sont dans `web/design/workspace-2026-09/` (un fichier `.dc.html` par planche,
`canvas.json` pour la disposition, `gen-skins.py` et `gen-couleurs.py` qui les
régénèrent). La revue qui a précédé est `web/DESIGN-REVIEW-2026-09.md`.

Ce document dit ce que l'interface fait et à quoi elle ressemble. Il ne dit pas
comment la coder : c'est l'objet de l'ADR d'architecture qui l'accompagne.

## 1. Décisions

| Sujet | Décision |
|---|---|
| Modèle | Atelier docké (Blender, Godot, Unity) : espaces de travail par intention, explorateur à gauche, document au centre, inspecteur à droite, dock en bas. |
| Repli | Rail d'icônes, panneaux à trois états (replié, entrouvert, épinglé), raccourcis 1 à 5, mode concentration. |
| Toile | Niveaux de zoom Flotte, Projet, Workflow, Nœud dans un même espace ; le cockpit n'ajoute que le niveau Flotte. |
| Vues | Chaque collection se voit en Carte, Board ou Liste. La liste sert à gérer flows et groupes. |
| Infobulles | Épinglables et empilables (Alt fige, on entre, les termes ouvrent d'autres bulles, Échap ferme la pile). Une seule source de définitions. |
| Espaces | Six : Piloter, Concevoir, Exécuter, Observer, Mémoire, Source. |
| Palette | « Encre » : l'accent d'interaction est l'encre, l'orange ne reste que sur la marque et l'action primaire. Sombre et clair dès la première PR. |
| Surfaces | Cinq niveaux à écarts francs ; le terminal reste sombre dans les deux thèmes ; la toile n'est jamais blanche. |
| Géométrie | Rayons 3 px partout, points d'état ronds. Plancher typographique 13 px en sombre, 12 px en clair. |
| Densité | Deux réglages, Découverte et Concentration, choisis par l'utilisateur. |
| Backlog | IntelliSense, correcteur et colorisation propres à l'éditeur agentique de l'espace Source (petit modèle local ou IntelliSense classique). Hors de cette livraison. |

## 2. Tokens

> **Revue 2026-09 (contraste de surface et accent)** — retour direct de
> Guilhem : « ça manque un peu de couleur, de façon intelligente, ça paraît
> très terne/sombre ; en clair même problème : trop sobre, sans vie, sans
> couleur, teintes trop proches ». Mesure : les cinq niveaux de surface
> avaient un rapport de luminance de 1,06 à 1,17 entre voisins, sous le seuil
> de lisibilité d'une superposition (1,25) — et `--acc` valait `--ink`, donc
> aucune teinte propre pour le survol, le focus, l'onglet actif ou la ligne
> sélectionnée. Ce que la ligne « Palette » ci-dessus reste vraie sur le
> fond : un seul orange par écran, la couleur porte toujours une
> information. Ce qui change : les écarts de surface sont rouverts à ~1,30,
> `--acc` reçoit une teinte propre (celle de la série 1), et deux nouveaux
> vocabulaires de couleur apparaissent — six teintes d'identité d'espace
> (filet d'onglet, liseré de panneau, jamais un aplat) et les badges
> sémantiques des espaces Concevoir/Exécuter/Piloter (genre de blueprint,
> `risk_profile`, type de proposition — toujours point + mot, jamais la
> couleur seule). Détail et mesures : `tokens.css`, commentaires en tête de
> chaque bloc ; test : `tests/e2e/test_workspace_shell.py::test_les_surfaces_voisines_s_ecartent_assez_pour_se_distinguer`.
> Les valeurs ci-dessous restent indicatives — `tokens.css` est la seule
> source de vérité pour la couleur exacte.

> **Seconde marche (même revue, suite)** — Guilhem, sur la première marche
> ci-dessus : « accepté tel quel, puis une seconde marche plus franche ».
> Quatre changements : (1) les écarts de surface repassent de ~1,30 à
> 1,26-1,42 selon la paire, avec un vrai palier entre panneaux (`--e1`) et
> cartes (`--e2`) — `--bg` sombre reçoit une teinte froide, `--bg` clair
> remonte franchement près du blanc chaud (« fond franchement clair », pas
> juste moins terne) ; coût documenté : l'écart `--ink`/`--ink2` (sombre) se
> resserre de 1,32 à 1,21. (2) `--acc` se voit sur CHAQUE élément actif —
> onglet, vue, ligne sélectionnée, bouton par défaut (fond plein, texte
> `--onfill`), champ focus — pas seulement une bordure fine. (3) Les statuts
> kit/CI/doctor/mémoire (Inspecteur, Flotte), le type de proposition et le
> nœud du déroulé passent du point + mot à la pastille pleine (fond saturé +
> `--onfill`) ; genre de blueprint et `risk_profile` restent en point + mot
> (hors périmètre demandé). (4) Le filet d'onglet et le liseré de panneau
> deviennent un bandeau plein (2 px → 4 px), et le panneau qui a le focus
> clavier gagne un liseré élargi (3 px, `:focus-within`, aucun état posé côté
> JS). Harnais de contraste inchangé (texte AA 4,5:1, surfaces 1,25:1 min).

### 2.1 Surfaces (rôle unique chacune)

| Token | Rôle | Sombre | Clair |
|---|---|---|---|
| `--bg` | toile : la plus profonde en sombre (teinte froide), franchement claire en clair, grille de points `--line` au pas de 22 px | `#070A10` | `#F1F0EB` |
| `--e1` | panneaux : explorateur, inspecteur, rails | `#252C34` | `#D1D1CE` |
| `--bar` | barres : application, en-têtes de panneaux, onglets du dock, état | `#333C46` | `#B5B5B2` |
| `--e2` | posé : nœuds, cartes, champs, bulles (seul blanc pur en clair, petites surfaces, ombre `0 2px 8px rgba(0,0,0,.18)`) | `#465260` | `#FFFFFF` |
| `--e3` | survol, sélection de segment | `#54616F` | `#D7D8D3` |
| `--term` | terminal et journaux du dock, toujours sombre | `#0E1013` | `#1B1F25` |
| `--termink` | encre du terminal | `#C9CED6` | `#D6DAE0` |
| `--line` | la seule ligne | `rgba(255,255,255,.11)` | `rgba(23,25,28,.13)` |

### 2.2 Encre, accent, états, séries

| Token | Sombre | Clair | Contrainte |
|---|---|---|---|
| `--ink` | `#F2F3F5` | `#17191C` | |
| `--ink2` | `#D7DFEA` | `#34383E` | ≥ 4,5:1 sur `--e1`, `--bar`, `--e2` et `--e3` (posé sur `--e3` : chips, infobulles) |
| `--ink3` | `#A0AAB5` | `#42454B` | ≥ 4,5:1 sur `--e1` et `--bar` |
| `--acc` (interaction : onglet, vue, ligne sélectionnée, bouton par défaut, focus — chaque élément actif) | `#4C9BE8` | `#1F6FBF` | teinte de la série 1 — distincte de l'encre et de `--pri` |
| `--accsoft` (fond de sélection légère) | `rgba(76,155,232,.16)` | `rgba(31,111,191,.12)` | même teinte que `--acc` |
| `--onfill` (texte sur un remplissage saturé — accent ou badge d'état plein) | `#0E1013` | `#FFFFFF` | ≥ 4,5:1 sur chacune des six couleurs d'état/série (min 5,72:1) et sur `--acc` |
| `--pri` (action primaire, une par écran) et marque | `#FF6B3D` | `#D9481A` | texte dessus `#0E1013` / `#FFFFFF` |
| `--ok` | `#3DBE7A` | `#1D814F` | pastille pleine (kit/CI/doctor/mémoire, type de proposition, nœud du déroulé) ou point + mot ; jamais la couleur seule |
| `--warn` | `#E2B33C` | `#9C6D0C` | |
| `--bad` | `#E5645A` | `#B83A36` | |
| séries 1, 2, 3 (aussi vocabulaire des badges — genre de blueprint, type de proposition) | `#4C9BE8` `#B07CE8` `#D99A2B` | `#1F6FBF` `#6B48C4` `#9A6A08` | jamais réutilisées pour un état ; toujours pastille pleine ou point + mot |
| identité d'espace — 6 teintes désaturées (Piloter, Concevoir, Exécuter, Observer, Mémoire, Source) | voir `tokens.css` (`--id-*`) | id. | bandeau plein d'onglet et de panneau (4 px), jamais sur du texte |

Règle : aucune couleur codée en dur hors de `forge-tokens.css`. Un test
l'interdit.

### 2.3 Typographie et géométrie

- Geist pour l'interface, Geist Mono pour chiffres, identifiants, chemins,
  terminal. Fontes embarquées dans le paquet (woff2), `font-display: swap`,
  repli `system-ui` et `ui-monospace`.
- Tailles : 12, 13, 14, 16, 20 ; graisses 400, 500, 600. Plancher 13 px en
  sombre, 12 px en clair. Libellés en casse de phrase, sans `letter-spacing`
  hors du logo.
- Espacements 4, 8, 12, 16, 24, 32. Rayons 3 px. Contrôles 30 px (28 px en
  densité Concentration, 44 px sur mobile). `:focus-visible` = anneau `--acc`
  de 2 px avec liseré `--bg`.
- Aucun mouvement de révélation ; transitions 120 ms sur survol et focus
  seulement ; `prefers-reduced-motion` respecté.

## 3. Coque

```
┌ barre d'application 44 px : marque · projet · espaces (⌘1 à ⌘6) · ⌘K · action primaire ┐
│ rail 44 px │ panneau gauche 236 px │ barre du document 40 px          │ inspecteur 300 px │
│            │ (épinglable)          │ toile / vue                      │ (épinglable)      │
│            │                       ├ dock 200 px : Console · Traces · Timeline · Problèmes ┤
└ barre d'état 26 px : version du kit · API · standard · hôtes · mode concentration ⇧⌘F ────┘
```

### 3.1 Panneaux à trois états

| Geste | Effet |
|---|---|
| Clic sur l'icône du rail | ouvre le panneau en surimpression ; un clic ailleurs referme |
| Survol du rail, 450 ms | entrouvre en surimpression ; jamais au survol du contenu |
| Cadenas, ou ⌘ + clic | épingle dans la grille ; le contenu se redimensionne |
| 1 à 5 | bascule explorateur, bibliothèque, preuves, mémoire, dock |
| ⇧⌘F | mode concentration : tout se replie |

L'état de chaque panneau (replié, épinglé, largeur) est mémorisé par espace de
travail et par projet, côté client.

### 3.2 Infobulles épinglables

- Survol de 500 ms : bulle courte (nom, définition d'une phrase, raccourci,
  mention « Alt · épingler »).
- Alt fige la bulle ; le pointeur peut y entrer ; les termes soulignés en
  pointillé ouvrent une bulle enfant, elle aussi épinglable. Trois niveaux au
  plus. Échap ferme toute la pile. Une bulle épinglée porte un cadenas.
- Contenu : une seule source, le glossaire du kit (`framework/glossary.yaml`,
  une entrée par concept : `id`, `nom`, `définition`, `raccourci`, `termes`,
  `doc`). La documentation en dérive ; un test refuse un terme cité sans
  entrée.
- Mode Concentration : la bulle se réduit au nom et au raccourci, délai 800 ms ;
  Alt rouvre la définition.

### 3.3 Palette de commandes (⌘K)

Atteint projets, espaces, workflows, agents, tâches, fichiers de Source, et les
commandes du kit. Chaque entrée montre la commande `grimoire …` équivalente.
Depuis le dock, chaque action à la souris affiche aussi sa commande.

### 3.4 Réglages Découverte et Concentration

| | Découverte | Concentration |
|---|---|---|
| Rail | icônes avec libellés | icônes seules |
| Panneaux | explorateur et inspecteur épinglés | tout replié |
| Infobulles | complètes, 500 ms | nom et raccourci, 800 ms |
| Dock | Traces ouvert | Console ouverte, curseur dans le terminal |
| Densité | aérée, contrôles 30 px | dense, contrôles 28 px |

## 4. Espaces de travail

Chaque espace a une barre de document (fil d'Ariane, niveaux de zoom quand ils
s'appliquent, sélecteur de vue, état de validation) et son inspecteur.

| Espace | Toile / vues | Inspecteur | Ce qu'il remplace |
|---|---|---|---|
| Piloter | Flotte (cockpit) et Projet : tableau sur bureau, cartes sur mobile ; KPI en une carte divisée ; « À traiter » toujours visible | projet : kit, hôtes, standard, actions (initialiser, mettre à jour, ouvrir) | `portfolio.html`, `index.html` du cockpit |
| Concevoir | zoom Projet → Workflow → Nœud ; Carte (graphe), Board, Liste ; palette de nœuds ; validation, simulation, compilation | nœud ou workflow : propriétés, validation, coût, preuves | `blueprints.html`, `patterns.html`, `extensions.html` (bibliothèque en panneau) |
| Exécuter | tâches : Board 4 ou 8 colonnes, Liste, Timeline ; gates en une ligne sous chaque colonne ; carte de tâche à trois niveaux | tâche : critères, preuves, prochaine porte avec bouton, timeline | `kanban.html` |
| Observer | runtime seulement : six KPI, coût par modèle, latence, spans lents, traces par agent ; activité, RTK, bench en onglets ; un seul état vide qui dit d'où viendra la donnée | trace ou span | `observability.html` |
| Mémoire | store et graphe d'abord, couches et backends ensuite | entrée | `memory.html` |
| Source | fichiers par étage (overrides, kit, projections) ; éditeur avec Source, Diff contre le kit, Rendu ; « créer un override » quand on édite un fichier du kit | fichier : étage, version, empreinte, override, projeté vers, chargé par | nouveau |

Le dock est commun : Console (terminal du kit, exécute les sous-commandes
`grimoire` du projet servi, local seulement), Traces (TraceLedger), Timeline
(`grimoire task trace`), Problèmes (doctor, validation).

## 5. Données : réelles ou rien

- Une vue lit le projet servi ; sans donnée, un seul bloc vide dit d'où elle
  viendra et comment voir la démo. La démo reste opt-in et marquée.
- Corrections dues (revue §4.1, §4.4) : `ci_status` et `commits_total` au
  portefeuille ; `antifragile: null` = « pas encore mesurée » ; observatoire sans
  trace = état vide, pas d'erreur ; badge démo piloté par `demo`.
- Une interface partagée entre l'atelier mono-projet et le cockpit
  multi-projets prouve, par un test, que chaque route honore la cible.

## 6. Critères d'acceptation

1. Les six espaces s'ouvrent sur un projet réel initialisé avec le kit, sans
   donnée de démo, dans les deux thèmes et les deux densités.
2. Aucun texte rendu sous 13 px en sombre, 12 px en clair ; aucune encre sous
   4,5:1 sur `--e1` ; aucune couleur hors tokens. Mesuré par un test.
3. Les trois états de panneau, les raccourcis 1 à 5, ⇧⌘F et ⌘K fonctionnent au
   clavier et à la souris ; un test Playwright par mécanique.
4. La pile d'infobulles : survol, Alt, bulle enfant, Échap ; définitions
   depuis le glossaire ; un test refuse un terme sans entrée.
5. Source : ouvrir, éditer un fichier du kit crée l'override, diff contre le
   kit, provenance correcte ; `grimoire doctor` reste vert après.
6. Console : une commande `grimoire` exécutée depuis le dock, sortie affichée,
   refus d'une commande hors `grimoire`.
7. Captures avant et après par écran, même viewport, même projet jetable.
8. `grimoire serve` et `grimoire cockpit serve` servent la même coque ; le
   cockpit ajoute le niveau Flotte et le sélecteur de projet.

## 7. Hors périmètre

Pages vitrine (`index.html` public, `demo.html`, `anatomy.html`,
`game-ui.html`), IntelliSense de l'éditeur Source (backlog), thème système
automatique au-delà de la préférence `prefers-color-scheme`.
