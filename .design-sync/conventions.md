# Grimoire Cockpit — conventions pour construire avec ce design system

Ce design system est la **vue de travail** de grimoire-kit (`grimoire serve`, `grimoire cockpit serve`) : une coque dockée façon Blender ou Godot, en HTML et CSS sans framework. Il n'y a **aucun composant React** : tu construis avec les classes et les tokens ci-dessous, dans ton propre balisage, et tu lis `_ds_bundle.css`, `tokens/tokens.css` et `tokens/spaces.css` avant de styler quoi que ce soit.

## 1. Enveloppe et thème

- Pose les attributs sur la racine, ils pilotent tout le CSS : `<html data-theme="dark|light" data-density="decouverte|concentration" data-focus="off|on">`. Sans `data-theme`, c'est `prefers-color-scheme` qui décide. Le thème clair n'est pas une inversion : chaque token garde son rôle et change de valeur.
- L'espace actif se pose sur le corps : `<body data-active-space="piloter|concevoir|executer|observer|memoire|source">`. Il alimente le filet d'onglet et le liseré de `.panel-head` via les six teintes `--id-piloter` … `--id-source` (repère de navigation, jamais un aplat, jamais sur du texte).
- Grille de la coque, par identifiant : `#appbar` (44 px, marque `#brand`, chip de projet, `#spaces`), `#rail` (44 px, `.rail-btn` + `.rail-lbl`), `.panel.panel-left` / `.panel.panel-right` avec `data-state="pinned|peek|collapsed"` et `.panel-head` / `.panel-body` / `.panel-resize`, `#center` avec `#docbar`, `#canvas` et `#dock` (`#dock-tabs`, `#dock-body`), `#statusbar` (26 px), `#palette` (`#palette-box`, `#palette-input`, `#palette-list`).

## 2. Styler : uniquement `var(--token)`, jamais une couleur littérale

- Surfaces, du fond aux cartes : `--bg` (toile, jamais blanche), `--e1` (panneaux), `--bar` (barres et en-têtes), `--e2` (cartes, seule surface blanche en clair), `--e3` ; `--term` / `--termink` pour le terminal, sombre dans les deux thèmes ; `--line` pour toute bordure.
- Encre : `--ink` (titres et texte principal), `--ink2` (chips, infobulles), `--ink3` (secondaire, sur `--e1` et `--bar` seulement).
- Interaction : `--acc` sur CHAQUE élément actif (onglet, vue, ligne sélectionnée, bouton par défaut, champ focus), `--accsoft` pour une sélection légère, `--onfill` pour le texte posé sur un remplissage saturé.
- Marque et action primaire : `--pri` / `--onpri`, **une par écran**.
- États, toujours point + mot, jamais la couleur seule : `--ok`, `--warn`, `--bad`. Séries de données et badges sémantiques : `--s1`, `--s2`, `--s3` (jamais réutilisées pour un état).
- Typographie : `--font` (Geist), `--mono` (Geist Mono) ; tailles `--t-min` (plancher 13 px sombre, 12 px clair), `--t-s`, `--t-m`, `--t-l`, `--t-xl`.
- Géométrie : rayon unique `--r` (3 px), espacements `--sp-1` … `--sp-6`, hauteurs de chrome `--h-app`, `--h-doc`, `--h-status`, `--h-ctl`, largeurs `--w-rail`, `--w-panel`, `--w-inspector`, `--h-dock`. Mouvement : `--dur` (120 ms), survol et focus seulement.

## 3. Primitives de la coque (`_ds_bundle.css`)

- `.btn` (modificateurs `.pri` = action primaire, `.on` ou `aria-pressed="true"` = actif), `.chip` (et `.chip-pick` avec `.chevron` pour un sélecteur), `.chip.pill` + `.ok|.warn|.bad|.acc|.s1|.s2|.s3` = pastille pleine avec texte `--onfill`.
- `.dot` + `.ok|.warn|.bad|.acc|.s1|.s2|.s3` = point d'état, toujours suivi d'un mot.
- `.seg` (groupe de `button`, actif par `aria-pressed="true"`), `.tab` (`.on` ou `aria-selected="true"`), `.kbd` (raccourci), `.input` / `.field`, `.tree` (explorateur ; sélection `.sel` ou `aria-current="true"`), `.tip` (infobulle épinglable), `.empty` (état vide avec `h2` et `code`).
- Utilitaires : `.row`, `.grow`, `.soft`, `.lbl`, `.mono`, `.ico`, `.pin`, `.ev-row` / `.ev-id` / `.ev-title` / `.ev-msg` (ligne de preuve ou d'événement).

## 4. Contenu des espaces (`tokens/spaces.css`)

Chaque espace préfixe ses classes : `pl-` Piloter (KPI `.pl-kpi`, `.pl-kpi-item`, tables `.pl-table`), `cv-` Concevoir, `ex-` Exécuter, `ob-` Observer (`.ob-kpi`, `.ob-panel`), `me-` Mémoire, `sr-` Source (éditeur, `.sr-editor`, `.sr-diff`, jetons `.tok-key`, `.tok-string`…). Réutilise ces classes pour un écran d'espace ; pour un nouveau bloc, compose les primitives de §3 et les tokens de §2.

## 5. Exemple idiomatique

```html
<html data-theme="dark" data-density="decouverte">
<body data-active-space="observer">
  <div class="ob-kpi">
    <div class="ob-kpi-item"><div class="ob-kpi-val mono">96,7 %</div><div class="ob-kpi-lbl">succès</div></div>
    <div class="ob-kpi-item"><div class="ob-kpi-val mono">6,0</div><div class="ob-kpi-lbl">tours médians</div></div>
  </div>
  <div class="row" style="gap:var(--sp-2);margin-top:var(--sp-4)">
    <span class="chip pill ok">gate vert</span>
    <span class="dot warn"></span><span class="soft">CI en attente</span>
    <span class="grow"></span>
    <button class="btn pri" type="button">Publier</button>
  </div>
</body>
</html>
```
