# Revue de direction artistique — `grimoire serve` et `grimoire cockpit serve`

Date : 2026-09-04. Kit : 3.37.0 (`origin/main`, `08a86d5f`). Statut : revue avec
maquettes. Aucune modification de CSS ou de JS n'est fusionnée sans validation
des maquettes par Guilhem.

Point de départ : « la DA me semble lourde pour une application web aussi
riche ». Cette revue nomme ce qui est lourd, le mesure, et propose un système
réduit avec trois écrans maquettés.

Maquettes (canevas Claude Design, données réelles, thème clair en levier) :
https://claude.ai/code/artifact/d4b7e54f-a2e7-41bb-8b6c-d3b8fbb1b03e

## 1. Périmètre et méthode

- Worktree jetable sur `origin/main`, venv dédié, kit installé en editable.
- Projet servi : un projet jetable `proj-da` initialisé par `grimoire init -y`
  puis `grimoire standard init --profile governed`. Pas la démo.
- Cockpit : `GRIMOIRE_COCKPIT_HOME` détourné vers un répertoire jetable, deux
  projets enregistrés (`proj-da`, le clone du kit). Le registre réel n'a pas
  été utilisé.
- Captures : Chromium headless (Playwright), 1440×900 et 390×844, chaque page
  de l'atelier (`:44173`) et du cockpit (`:48420`). Il n'existe pas de thème
  clair : toutes les captures sont en sombre.
- Mesures : analyse statique des CSS et des blocs `<style>` (couleurs, tailles,
  rayons, ombres), styles calculés dans le navigateur (tailles réellement
  rendues, contraste WCAG), poids réseau par page.
- Captures de référence : `web/design-review-2026-09/` (JPEG 1280 px ou 390 px).

## 2. Inventaire des écrans

Les mêmes fichiers HTML servent trois mondes : la vitrine publique (GitHub
Pages), l'atelier mono-projet (`grimoire serve`) et le cockpit multi-projets
(`grimoire cockpit serve`). `grimoire-mode.js` pose `mode-vitrine` ou
`mode-atelier` sur `<html>` ; seul `atelier.css` réagit à ce mode.

| Écran | Hôte | CSS externe | Inline `<style>` | JS | Fontes | Capture |
|---|---|---|---|---|---|---|
| `portfolio.html` (portefeuille, pilotage de flotte) | cockpit | 29 Ko | 12 Ko | 20 Ko | 68 Ko | 01, 02, 03 |
| `atelier.html` (accueil atelier) | atelier | 55 Ko | 0 | 34 Ko | 68 Ko | 08, 09 |
| `kanban.html` (board de tâches) | atelier | 31 Ko | 9 Ko | 34 Ko | 68 Ko | 04, 05 |
| `observability.html` (observatoire) | les deux | 60 Ko | 22 Ko | 89 Ko | 68 Ko | 06, 07 |
| `memory.html` (mémoire) | les deux | 60 Ko | 23 Ko | 52 Ko | 68 Ko | 10 |
| `blueprints.html` (éditeur) | atelier | 96 Ko | 0 | 294 Ko | 68 Ko | 11 |
| `extensions.html` | les deux | 55 Ko | 1 Ko | 52 Ko | 68 Ko | 12 |
| `patterns.html` | les deux | 55 Ko | 2 Ko | 52 Ko | 68 Ko | 13 |
| `documentation.html`, `labs.html` | atelier | 55–60 Ko | 1–2 Ko | 34–52 Ko | 68 Ko | — |
| `index.html` (landing) | cockpit | 29 Ko | 9 Ko | 20 Ko | 68 Ko | 14 |
| `anatomy.html`, `demo.html`, `game-ui.html` | vitrine | 29 Ko | 6–10 Ko | 20–28 Ko | 68 Ko | — |

Fichiers CSS : `forge-tokens.css` 2,2 Ko, `forge-base.css` 21,3 Ko,
`atelier.css` 30,2 Ko, `bp2.css` 28,9 Ko, `bp2-team.css` 11,1 Ko,
`forge-motion.css` 4,3 Ko. Deux fichiers ne sont chargés par aucune page :
`forge-landing.css` (20 Ko) et `forge-charts.css` (10 Ko).

## 3. Constats mesurés

### 3.1 Les cinq constats les plus lourds

| # | Constat | Mesure |
|---|---|---|
| 1 | **Le texte est trop petit, presque partout.** Les tokens fixent un plancher `--text-xs: 0.8rem` (12,8 px) « plancher lisible », mais les feuilles utilisent 0,5 à 0,77 rem plus de 300 fois. | Part du texte rendu sous 12 px : kanban 78 %, labs 81 %, mémoire 72 %, patterns 72 %, observatoire 67 %, portefeuille 62 %. Sous 10,5 px : 162 éléments sur mémoire, 80 sur observatoire, 54 sur portefeuille. Minimum rencontré : 7 px (mémoire), 8 px (portefeuille), 8,8 px (kanban). 84 valeurs distinctes de `font-size` dans les sources ; jusqu'à 32 tailles distinctes calculées sur une seule page (mémoire). |
| 2 | **L'encre discrète ne passe pas le contraste.** `--ink-muted: #5B6068` donne 2,4 à 3,1:1 sur les trois élévations, pour du texte de 8 à 12 px. C'est la couleur de tous les libellés de KPI, de colonnes, de chemins et de sous-titres. | Styles de texte en échec AA : mémoire 36, landing cockpit 28, portefeuille 22, observatoire 20, blueprints 14, kanban 11. Sur mémoire, 107 éléments de texte sont en `#5B6068`. Deux styles à 1:1 (petit « AF » rouge sur fond rouge) sur portefeuille et landing. |
| 3 | **Chaque page d'outil commence par un hero de site marketing.** Titre 72 px (`--text-4xl`/`5xl`), surtitre `FORGE-0x · …` en mono espacé, paragraphe de pitch, badge. Les données arrivent sous le pli. | Portefeuille : premier KPI à y ≈ 600 sur un viewport de 900 ; mémoire, observatoire : h1 72 px ; kanban : 54 px. Sur portefeuille, les cartes projets sont en `.reveal` (opacité 0 jusqu'au scroll) : une capture pleine page sort vide, et l'utilisateur voit une section blanche tant qu'il n'a pas scrollé. |
| 4 | **Le vocabulaire visuel n'est pas tenu par les tokens.** 49 tokens déclarés, mais 116 couleurs codées en dur hors tokens, 22 rayons distincts pour 3 tokens, 43 ombres distinctes (halos orange `0 0 46px`, glows verts sur les points, ombres portées `0 30px 90px`). `kanban.html` ne charge pas `forge-tokens.css` et redéclare 24 propriétés ; 4 variables sont utilisées sans être définies (`--anim`, `--prio`, `--st`, `--warn`). | Rayons : `2px` ×49, `999px` ×26, `3px` ×16 en plus de `--r-sm/md/lg`. Ombres : 11 distinctes dans `atelier.css`, 13 dans `bp2.css`. Deux feuilles mortes (30 Ko). |
| 5 | **Trop de signaux concurrents par écran.** Grille blueprint en fond, cinq familles de chips (état, tag pattern, artefact, hooks, mode), points lumineux, bordures orange en tête de KPI, libellés majuscules espacés (`letter-spacing 0.16em`) sur quasi tout ce qui n'est pas un titre, mono partout. | Éléments avec bordure visible : patterns 90, mémoire 74, extensions 55, landing 53, observatoire 49, kanban 43. Texte en majuscules espacées : observatoire 67, mémoire 41, landing 29, portefeuille 27. Geist Mono porte 90 % du texte de l'atelier (48 éléments sur 50), y compris la navigation et les phrases. |

### 3.2 Constats secondaires

- **Fontes distantes dans un outil local.** Chaque page importe Geist (5
  graisses) et Geist Mono (3 graisses) depuis `fonts.googleapis.com` : 68 Ko,
  et hors ligne tout retombe sur `system-ui` sans que la mise en page l'ait
  prévu. `kanban.html` ajoute deux `preconnect`.
- **Focus clavier presque absent.** 2 à 9 règles `:focus`/`:focus-visible` par
  page (portefeuille : 2), aucune sur les cartes, lignes de tableau, chips et
  boutons `.pf-tag`.
- **Mobile inutilisable dans l'atelier.** La sidebar de 228 px est fixe et ne
  se replie pas : à 390 px il reste 162 px de contenu (capture 09). Sur le
  portefeuille, le CTA « Ouvrir l'atelier » recouvre le sélecteur de projet
  (capture 03).
- **Le cockpit sert la landing marketing comme page d'accueil.** `index.html`
  sur `:8420` affiche « Un noyau agentique. Composé, gouverné, tracé. » et un
  terminal fictif `session-4a2f · 3 agents` sans rapport avec le registre
  (capture 14). La navigation d'entrée est `portfolio.html`, mais le logo y
  renvoie.
- **Honnêteté des données contredite par l'habillage.** Sur le portefeuille,
  badge `SNAPSHOT DÉMO` alors que `projects.json` porte `demo: false` et deux
  projets réels ; sur l'observatoire, `LIVE · 0 traces` et `SNAPSHOT DÉMO`
  côte à côte. Les trois statuts promis par les briefs (réel, snapshot démo,
  privé) ne sont pas câblés.
- **Cartes projets : trois valeurs fausses.** `CI VERT` pour `ci_status:
  "unknown"` (le code teste `p.ci`, la donnée s'appelle `ci_status`) ;
  `Commits 0` pour `commits_total: 1` et `643` (le code lit `p.commits`, champ
  de la carte d'enrichissement démo) ; `0/100 · FRAGILE` pour
  `antifragile: null`. Un cockpit de gouvernance qui affiche un vert par
  défaut est un problème de confiance avant d'être un problème de DA.
- **Observatoire vide : erreur JS.** `forge-observatory.js:272`
  (`gs.density.toFixed`) lève sur un projet sans trace ; la constellation ne
  se rend pas et rien ne le dit. Les KPI affichent six zéros et `$0.0000`.
- **Détails.** `documentation.html` déclenche un 501 (`HEAD` non supporté par
  le serveur de l'atelier) ; `memory.html` et `kanban.html` servis par le
  cockpit font un 404 sur une donnée absente ; `labs.html` n'est pas dans la
  garde `GUARDED` et s'ouvre sans projet.

## 4. Critique écran par écran

### 4.1 Portefeuille — pilotage de flotte (captures 01, 02, 03)

Premier écran du cockpit et premier livré en 3.36.0 (alignement kit, mise à
jour). Hiérarchie inversée : 380 px de hero, puis un bandeau de cinq KPI en
cartes à bordure orange, puis les cartes projets cachées par `.reveal`. Les
cartes mélangent sept métriques de même poids (commits, coût, contradictions,
antifragilité, couverture, trace, kit) ; l'information de flotte qui compte
— « ce projet est-il aligné sur le kit et que dois-je faire » — est un tag
de 9,3 px en bas de carte (`KIT 3.37.0 · ALIGNÉ`, `NON INITIALISÉ`,
`INITIALISER →`). Le bouton d'action est un `.pf-tag`, même taille que les
étiquettes passives. Les valeurs à 8 px (`pf-m-lbl`, `pf-m-sub`) et le chemin
tronqué à 9,3 px ne se lisent pas. Le bandeau « À surveiller » est bien pensé
mais masqué (`display:none`) tant que la règle de sévérité ne matche pas,
alors que le standard non initialisé du second projet est exactement un
signal à traiter.

Proposition (maquette « Portefeuille — pilotage de flotte ») : en-tête d'une
ligne (titre 20 px, méta, deux actions), bandeau KPI dans une seule carte
divisée, liste « À traiter » avec l'action à droite, puis un tableau de
projets (une ligne par projet, 48 px, colonnes kit / CI / commits /
antifragilité / mémoire / flows / dernier événement / action). Les états sont
un point coloré plus un mot, jamais la couleur seule ; `unknown` devient
« inconnue » en gris, pas un vert.

### 4.2 Atelier (captures 08, 09)

Le plus sobre des écrans, et le plus proche de la cible. Points lourds : tout
en mono, dont la navigation et les phrases d'action ; six chips d'état en
tête dont trois en orange plein alors qu'elles disent que tout va bien ; le
bloc « trois prochaines actions » encadré d'orange avec deux boutons
différents pour deux actions de même rang ; grille blueprint en fond. Sur
mobile, la sidebar fixe laisse 162 px.

### 4.3 Board de tâches (captures 04, 05)

Hero de 54 px et pitch de trois lignes avant le board. Bandeau
« chaque porte exige sa preuve » en orange + cinq chips cyan bordées, puis
quatre colonnes dont trois vides avec un libellé `vide` de 8,8 px. La carte
de tâche empile trois familles de chips (owner en orange bordé, rôles, quatre
artefacts en cyan) puis une ligne `→ Prêt · requiert critères d'acceptation`
à 8,8 px : c'est la seule information actionnable et elle est la plus petite.
Huit états annoncés, quatre montrés, sans dire où sont les quatre autres. Les
colonnes vides prennent la même largeur que la colonne pleine.

Proposition (maquette « Tâches — board gouverné ») : en-tête d'une ligne,
sélecteur de vue (flux 4 colonnes / 8 colonnes / liste) avec mention des
états repliés, porte de chaque colonne en une ligne 12 px sous le titre,
carte de tâche avec titre 14 px, owner et rôles en une ligne 13 px, quatre
preuves en chips grises (point vert = présente, gris = manquante) et la
transition suivante comme phrase + bouton. Colonnes vides en pointillé.

### 4.4 Observatoire (captures 06, 07)

La page la plus chargée : 25 tailles de police rendues, 88 éléments en
majuscules espacées, 82 Ko de CSS. Six KPI en cartes à bordure orange, puis
une carte « performance & coût » qui contient elle-même six sous-KPI en
cellules bordées, puis latence, spans lents, multi-LLM, constellation,
waterfall, gouvernance, économie RTK, activité git, CI, bench, routing,
mémoire, code. Vingt sections sur une page pour un projet qui n'a aucune
trace, et l'état vide est un mur de zéros (`$0.0000`, `0.0/min`) plus une
erreur JS silencieuse.

Proposition (maquettes « Observatoire — peuplé » et « Observatoire — état
vide ») : la page ne montre que le runtime (traces, agents, coût, latence,
erreurs, confiance) ; l'activité projet, l'économie RTK et le bench vont
dans leurs propres onglets ou pages. Six KPI dans une carte divisée, deux
panneaux de barres fines (coût par modèle en trois teintes de série validées
CVD ; latence p50/p95/p99 en une seule teinte neutre car une seule série),
tableau des spans lents, traces par agent. Sur un projet sans trace : un seul
bloc vide qui dit d'où viendra la donnée et comment voir la démo.

### 4.5 Mémoire (capture 10)

Même hero, puis deux grandes cartes « Vitrine / Cockpit local » qui
expliquent l'architecture au lieu de montrer la mémoire (une entrée). 32
tailles de police rendues, 162 éléments sous 10,5 px, 36 styles en échec de
contraste, 9 ombres. C'est la page où le brief (« Obsidian pour la mémoire
agentique ») a été implémenté comme une page d'explication.

### 4.6 Éditeur de blueprints (capture 11)

96 Ko de CSS et 294 Ko de JS, mais l'écran lui-même est le plus cohérent :
rail, palette, toile, panneau. Points lourds : la palette utilise 9,3 px pour
les refs de pattern et 10,2 px pour le pied ; les titres de groupe en cyan et
orange majuscules ; la barre haute mélange quatre styles de bouton
(`VALIDER` ghost, `SIMULER` ghost, `COMPILER · MODIFIÉ` orange bordé, un
bouton carré). La vignette de démarrage est bonne.

### 4.7 Extensions, patterns, docs, labs (captures 12, 13)

Ces pages partagent l'habillage vitrine (barre haute, footer marketing) même
en atelier. Extensions : 11 cartes à bordure et 55 éléments bordés, chips de
pattern en quatre couleurs de catégorie (cyan, violet, vert, ambre) plus une
chip orange `hooks · shadow`. Patterns : 370 éléments de texte dont 270 sous
12 px, 78 cartes identiques. Labs : lisible, ton juste, mais 81 % du texte
sous 12 px.

### 4.8 Landing servie par le cockpit (capture 14)

`index.html` est une page marketing (h1 72 px, terminal fictif, bandeau de
chiffres). Servie sur `:8420`, elle est le seul écran que le logo ouvre. Elle
ne devrait pas être atteignable depuis le cockpit.

## 5. Système proposé — « Forge 2 »

Même identité (sombre, orange, Geist), quatre fois moins de vocabulaire. La
planche « Système réduit » du canevas l'expose en entier.

| Dimension | Aujourd'hui (mesuré) | Proposé |
|---|---|---|
| Surfaces | 4 élévations + grille blueprint en fond | 3 : fond `#0B0C0E`, surface `#121418`, relief `#1A1D22`. Pas de grille sur les pages d'outil. |
| Encre | 3 tokens, dont `#5B6068` sous AA | 3 : `#F6F7F8`, `#A9AEB6` (8,6:1), `#80868F` (4,9:1 sur fond, ≥ 4,4:1 sur relief). |
| Lignes | 2 tokens + 20 valeurs ad hoc | 1 : blanc à 10 %. |
| Accent | 5 tokens orange (accent, hot, soft, glow, dim) + halos | 2 : `#FF6B3D` et un fond `rgba(255,107,61,.14)`. Usage : action primaire, nav active, anneau de focus. Jamais de glow, jamais de bordure de carte. |
| États | 5 couleurs data réutilisées pour les états et les séries | 3 états (`#34D399`, `#FCD34D`, `#F87171`), toujours point + libellé. Inconnu = encre discrète. |
| Séries de graphiques | cyan/violet/ambre à L ≈ 0,87 (hors bande sombre) | `#1F9BBF`, `#8A6EE0`, `#B8880E` : bande OKLCH sombre, ΔE CVD ≥ 8, ≥ 3:1 sur surface (validées). Une seule série = teinte neutre. |
| Total couleurs | 116 codées en dur + 64 propriétés | 13. |
| Typographie | 84 tailles sources, jusqu'à 32 rendues ; 5 + 3 graisses ; mono partout | 6 tailles : 12 / 13 / 14 / 16 / 20 / 26 ; graisses 400 / 500 / 600 ; Geist pour l'interface, Geist Mono pour chiffres, identifiants, chemins. Plancher 12 px. Libellés en casse de phrase, sans `letter-spacing`. |
| Fontes | Google Fonts, 8 graisses, 68 Ko | Geist et Geist Mono embarquées dans `web/fonts/` (woff2, 5 fichiers), `font-display: swap`, fallback `system-ui` avec métriques proches. |
| Espacements | valeurs libres | 4 / 8 / 12 / 16 / 24 / 32. |
| Rayons | 22 valeurs | 3 : 6 px (contrôles, chips), 10 px (cartes, tableaux), 999 px (points). |
| Élévation | 43 ombres | Bordure seule. Une ombre pour les surcouches : `0 16px 48px rgba(0,0,0,.5)`. |
| Contrôles | hauteurs libres, focus rare | 32 px bureau, 44 px mobile ; `:focus-visible` = anneau accent 2 px avec liseré fond. |
| En-tête de page | hero 54–96 px + surtitre + pitch + badge | une ligne de 56 px : titre 20 px, méta 13 px, actions à droite. |
| Navigation | sidebar fixe 228 px | sidebar 220 px, repliée en barre haute avec menu sous 900 px. |
| Mouvement | `reveal` au scroll, blur, pulses | aucun mouvement de révélation sur les pages d'outil ; transitions 120 ms sur hover et focus seulement. |
| Thème clair | inexistant | levier sur la maquette Portefeuille (accent `#D9481A`, encre `#15171A`). À décider ; non requis pour la première PR. |

## 6. Maquettes

Canevas : https://claude.ai/code/artifact/d4b7e54f-a2e7-41bb-8b6c-d3b8fbb1b03e

| Artboard | Écran | Données |
|---|---|---|
| Portefeuille — pilotage de flotte | cockpit, 1440×900, levier « Thème » sombre/clair | `projects.json` du registre jetable : `proj-da` (3.37.0 aligné, CI inconnue, 1 commit, 4 contradictions), `grimoire-kit-da` (3.37.0 non initialisé, CI vert, 643 commits, +30, antifragilité 42) |
| Portefeuille — mobile | 390×844 | idem |
| Tâches — board gouverné | atelier, 1440×900 | `task-board.yaml` de `proj-da` : une tâche `Bootstrap agentic standard runtime`, 8 états, 6 transitions gardées |
| Observatoire — peuplé | atelier, 1440×900 | `web/data/observatory.json`, snapshot démo du dépôt, marqué comme tel dans l'en-tête |
| Observatoire — état vide | atelier, 1440×900 | `proj-da`, 0 trace |
| Système réduit | planche de tokens | section 5 |

## 7. Plan de PR par écran

Chaque PR est indépendante, petite, et livre une capture avant/après dans sa
description (même viewport, même projet jetable, même méthode que cette
revue). Aucune n'est ouverte avant validation des maquettes.

| PR | Périmètre | Fichiers | Capture avant/après attendue | Risque |
|---|---|---|---|---|
| 1. Tokens et base | `forge-tokens.css` réduit (section 5), `forge-base.css` : plancher 12 px, encre discrète relevée, ombres et halos retirés, `:focus-visible` global, fontes embarquées. Suppression de `forge-landing.css` et `forge-charts.css`. | `forge-tokens.css`, `forge-base.css`, `web/fonts/`, 2 suppressions | atelier 1440 et 390 : même structure, texte lisible, pas de halo | Toutes les pages bougent un peu. Gain immédiat de contraste ; PR à fusionner seule. |
| 2. Chrome atelier | `atelier.css`, `atelier-nav.js` : Geist pour la nav, sidebar repliable sous 900 px, statusbar 12 px, retrait de la grille blueprint en mode atelier, un seul style de chip d'état. | `atelier.css`, `atelier-nav.js` | atelier et kanban à 390 : contenu pleine largeur, menu | Faible. |
| 3. Portefeuille | Maquette 1 : en-tête d'une ligne, KPI en carte divisée, « À traiter » toujours visible, tableau des projets, correction de `p.ci` → `ci_status`, `p.commits` → `commits_total`, `antifragile: null` → « pas encore mesurée », retrait de `.reveal`, badge démo piloté par `demo`. | `portfolio.html` | portefeuille 1440 : KPI et tableau au-dessus du pli ; 390 : liste | Moyen : la page porte aussi la vitrine (`mode-vitrine`). Le hero reste en vitrine, disparaît en atelier/cockpit. |
| 4. Board de tâches | Maquette 3 : en-tête, sélecteur de vue, portes en une ligne, carte de tâche à trois niveaux, colonnes vides en pointillé, `kanban.html` charge `forge-tokens.css` au lieu de ses 24 propriétés. | `kanban.html` | kanban 1440 : board au-dessus du pli ; carte lisible | Faible. |
| 5. Observatoire | Maquettes 4 et 5 : page limitée au runtime, six KPI, barres fines, état vide unique, correction de `renderConstellation` sur `graph_stats` absent, sections activité / RTK / bench déplacées derrière des onglets. Palette de séries validée. | `observability.html`, `forge-observatory.js` | observatoire 1440 sur `proj-da` (vide) et `--demo` (peuplé) | Moyen : beaucoup de sections câblées, à déplacer sans les casser. |
| 6. Mémoire | Même traitement : en-tête, retrait des cartes d'architecture, store et graphe en premier. | `memory.html` | mémoire 1440 | Moyen. |
| 7. Extensions, patterns, docs, labs | En-tête d'une ligne en mode atelier, chips de catégorie en une couleur + libellé, plancher 12 px, footer marketing retiré en atelier. | 4 pages | extensions et patterns 1440 | Faible. |
| 8. Blueprints | Tailles de la palette, un seul style de bouton dans la barre haute, rail cohérent avec la PR 2. | `bp2.css`, `bp2-team.css` | éditeur 1440 | Faible. |
| 9. Cockpit : accueil | `grimoire cockpit serve` n'expose plus `index.html` marketing ; le logo mène au portefeuille. | `cmd_cockpit.py`, `forge-nav.js` | — | Faible, hors CSS. |

Les PR 3 et 5 embarquent des corrections de données (vert par défaut, commits
faux, crash sur état vide). Si Guilhem préfère les isoler, elles peuvent
partir avant la DA, sans attendre la validation des maquettes : ce sont des
bugs.

## 8. Ce qui n'est pas à changer

- L'identité : fond sombre, orange `#FF6B3D`, Geist + Geist Mono, logo
  `GRIMOIRE KIT`. La proposition réduit le vocabulaire, elle ne le remplace
  pas.
- La structure de l'atelier : sidebar, trois groupes (piloter, comprendre,
  observer), statusbar. Elle est bonne ; elle a besoin de respirer, pas d'être
  refaite.
- L'éditeur de blueprints : rail, palette, toile, panneau, vignette de
  démarrage. Seuls les détails de taille et de boutons bougent.
- Le principe d'honnêteté des données (réel / snapshot démo / privé). Il faut
  le câbler, pas le retirer.
- Les pages vitrine (`index.html`, `demo.html`, `anatomy.html`,
  `game-ui.html`) en mode vitrine : hors périmètre de cette revue. Le hero y
  reste légitime.
- Les briefs `DESIGN-BRIEF-*.md` : ils restent la référence de contenu ; cette
  revue ne porte que sur la forme.
- La palette de séries n'est pas étendue au-delà de trois teintes : au-delà,
  « autres » ou petits multiples.

## 9. Décisions attendues

1. Valider ou amender le système réduit (section 5), en particulier le
   plancher de 12 px et le retrait de la grille blueprint sur les pages
   d'outil.
2. Thème clair : le porter (levier sur la maquette) ou rester sombre seul.
3. Portefeuille : tableau de projets (maquette) ou conserver des cartes avec
   la nouvelle hiérarchie.
4. Observatoire : accepter le recentrage sur le runtime avec les autres
   sections derrière des onglets.
5. Fontes embarquées dans le paquet (≈ 200 Ko de woff2 dans la wheel) ou
   maintien de Google Fonts avec un fallback dessiné.
6. Ordre des PR : tokens d'abord (PR 1), ou écran par écran en partant du
   portefeuille.
7. Sortir les corrections de données (PR 3 et 5) avant la DA.

## 10. Annexe — méthode de mesure

- Analyse statique : expressions régulières sur les CSS et les blocs
  `<style>` de chaque HTML (`#hex`, `rgba()`, `font-size`, `border-radius`,
  `box-shadow`, `var(--x)` et déclarations `--x:`).
- Styles calculés : pour chaque élément visible portant un nœud texte,
  `getComputedStyle` (taille, famille, couleur, transformation, espacement),
  et pour chaque élément visible (ombre, rayon, bordure, fond). Contraste
  WCAG 2 calculé sur la couleur composée avec le premier fond opaque
  ancêtre.
- Réseau : taille des réponses `.css`, `.js`, `.json`, fontes, par page,
  après `networkidle`.
- Captures : Chromium 151 headless, 1440×900 et 390×844, `full_page` après
  défilement programmatique pour déclencher les `.reveal`.

Ces scripts vivent dans la session de revue et ne sont pas versionnés ; ils
tiennent en 150 lignes de Python et sont reproductibles à partir de cette
annexe.
