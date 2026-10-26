// Espace Piloter — LOT 4.
//
// Remplace `portfolio.html` (cockpit) et `index.html` du cockpit.
// Cible : Flotte (cockpit) et Projet ; tableau sur bureau, cartes sur mobile ;
// KPI en une carte divisée ; « À traiter » toujours visible.
// Inspecteur : projet — kit, hôtes, standard, actions (initialiser, mettre à
// jour derrière aperçu + confirmation, ouvrir).
//
// Corrections dues par la revue §4.1 : la donnée réelle s'appelle
// `ci_status` et `commits_total` (jamais `p.ci` / `p.commits`, qui ne
// correspondaient à rien) ; `antifragile: null` se lit « pas encore mesurée »,
// jamais comme un score nul ; `unknown` se rend « inconnue » en gris, jamais
// une couleur inventée ; ce module n'ouvre aucun jeu de données de
// démonstration — `demo` reste toujours `false` ici.
//
// API consommées : api.sheet(project?) — remplace, depuis #548, les sept
// appels (api.health, api.memoryStatus, api.doctor, api.agents,
// api.proposals, api.setupRun, api.flowRuns) plus api.projects() pour le nom
// du projet, que le premier rendu de la fiche faisait un par un — puis
// api.updateProject(project, confirm), api.agentSkill(name, skill, action),
// api.agentFields(name, fields), api.doctor(project?) (onglet Problèmes,
// à la demande — jamais au premier rendu, voir `loadSheet` plus bas).
//
// Wizard de setup (#171) : api.archetypesCatalogue(), api.backendsCatalogue(),
// api.needsCatalogue(), api.setupPlan(payload) — exécute réellement (même
// mécanique que `grimoire up`) sauf `payload.planOnly`, qui garde le repli
// « copier-coller la commande ». Refusé côté serveur (readOnly) hors projet
// d'accueil, comme le reste des écritures de cette fiche.
//
// Agents (#374) : section de la fiche projet, pas un septième espace. La
// gestion d'agents est une facette du même objet que « kit, hôtes, standard,
// actions » — un réglage du projet, pas un artefact qu'on façonne (ça, c'est
// Concevoir) ni une trace d'exécution (Observer). Écritures désactivées
// (`ctx.host.readOnly`) hors projet d'accueil, comme le reste de la fiche.

import { renderMarkdown, truncateMarkdown } from '../markdown.js';
import { actionStatus, badge, failureText, icon, kpiRow, setActionStatus, spaceHead, STATE_ICON } from '../shell.js';

const STYLE_ID = 'pl-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .pl-sheet { padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-5); }
    .pl-wrap { padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-5); }
    .pl-fleet { padding: var(--sp-6); max-width: 1400px; width: 100%; box-sizing: border-box; margin: 0 auto; }
    .pl-formula { font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); white-space: nowrap; }
    .pl-formula b { color: var(--rub); font-weight: 500; margin-right: 6px; }
    .pl-filterbar { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); }
    .pl-search { width: 260px; max-width: 100%; box-sizing: border-box; border-radius: var(--r-pill); }
    .pl-filterbar .seg { height: 32px; border-radius: var(--r-pill); }
    .pl-titlerow { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-3); }
    .pl-seg-n { margin-left: 6px; color: var(--ink3); }
    .seg > button[aria-pressed="true"] .pl-seg-n { color: inherit; }
    .pl-fleet .tbl td { padding-top: 10px; padding-bottom: 10px; }
    .pl-name { font-weight: 500; }
    .pl-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; }
    .pl-badges { display: flex; flex-wrap: wrap; gap: 6px; }
    .pl-fleet .tbl { table-layout: fixed; }
    .pl-fleet .tbl th:nth-child(2) { width: 30%; }
    .pl-fleet .tbl th:nth-child(3) { width: 170px; }
    .pl-fleet .tbl th:nth-child(4) { width: 100px; }
    .pl-fleet .tbl th:nth-child(5) { width: 120px; }
    .pl-fleet .tbl th:nth-child(6) { width: 64px; }
    .pl-none { margin: 0; }
    @media (max-width: 900px) {
      .pl-sheet, .pl-wrap, .pl-fleet { padding: var(--sp-3); }
    }
    .pl-section h3 { font-size: var(--t-m); font-weight: 500; margin: 0 0 var(--sp-2); color: var(--ink); }
    .pl-cards { display: none; flex-direction: column; gap: var(--sp-2); }
    .pl-card { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); padding: var(--sp-3) var(--sp-4); cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
    .pl-card-row { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
    @media (max-width: 760px) {
      .pl-desktop { display: none; }
      .pl-cards { display: flex; }
    }
    .pl-insp-block { margin-bottom: var(--sp-4); }
    .pl-insp-block h4 { font-size: var(--t-min); text-transform: none; color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
    .pl-insp-row { display: flex; justify-content: space-between; gap: var(--sp-2); padding: 4px 0; font-size: var(--t-s); }
    .pl-actions { display: flex; flex-direction: column; gap: 6px; margin-top: var(--sp-2); }
    .pl-preview, .pl-review-preview { margin-top: 8px; padding: 8px; border: 1px dashed var(--line); border-radius: var(--r); font-size: var(--t-min); color: var(--ink2); }
    .pl-nodes { margin: 8px 0; }
    /* Un bloc de code garde sa largeur propre et défile horizontalement —
       jamais pre-wrap, qui casse l'alignement d'une sortie CLI/Rich à
       chaque redimensionnement. */
    .pl-markdown { font-size: var(--t-s); }
    .pl-markdown h1, .pl-markdown h2, .pl-markdown h3 { font-size: var(--t-m); font-weight: 500; margin: 10px 0 4px; color: var(--ink); }
    .pl-markdown p { margin: 4px 0; }
    .pl-markdown pre { font-family: var(--mono); font-size: var(--t-min); background: var(--e2); border-radius: var(--r); padding: 8px; overflow-x: auto; white-space: pre; margin: 4px 0; }
    .pl-markdown code { font-family: var(--mono); }
    .pl-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .pl-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px; background: var(--e2); font-size: var(--t-min); }
    .pl-chip button { border: 0; background: none; color: var(--ink3); cursor: pointer; padding: 0; font-size: var(--t-min); line-height: 1; }
    .pl-chip button:hover { color: var(--ink); }
    .pl-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: var(--sp-2); }
    .pl-field label { font-size: var(--t-min); color: var(--ink3); }
    .pl-field textarea, .pl-field input[type="text"] { font: inherit; font-size: var(--t-s); padding: 6px 8px; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); color: var(--ink); resize: vertical; }
    .pl-tool-opts { display: flex; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--t-s); }
    .pl-tool-opts label { display: flex; align-items: center; gap: 4px; }
    /* Propositions : une carte, une ligne par proposition (artboards Douce,
       liste d'À traiter) — type et nom, puis les faits en texte secondaire,
       puis les actions et leur retour. Jamais trois colonnes serrées. */
    .pl-props { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); overflow: hidden; }
    .pl-prop-row { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line); min-width: 0; }
    .pl-prop-row:first-child { border-top: 0; }
    .pl-prop-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); min-width: 0; }
    .pl-prop-head .chip { flex: none; }
    .pl-prop-name { font-weight: 600; color: var(--ink); min-width: 0; overflow-wrap: anywhere; }
    .pl-prop-facts { margin: 0; color: var(--ink2); font-size: var(--t-s); overflow-wrap: anywhere; }
    .pl-prop-actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); margin-top: 2px; }
    .pl-prop-manual { display: flex; align-items: flex-start; gap: 6px; min-width: 0; margin: 0; padding: var(--sp-2) var(--sp-3); border-radius: var(--r); background: var(--e1); color: var(--ink2); font-size: var(--t-s); }
    .pl-prop-manual .ico { flex: none; margin-top: 1px; }
    .pl-prop-manual > span { min-width: 0; overflow-wrap: anywhere; }
    .pl-prop-manual b { color: var(--ink); font-weight: 600; }
    .pl-prop-manual code { font-family: var(--mono); font-size: var(--t-min); }
    /* Un display d auteur l emporte sur l attribut hidden : on le rend explicite. */
    .pl-sheet[hidden], .pl-wrap[hidden], .pl-cat-files[hidden], .pl-up-foot[hidden] { display: none; }
    .pl-update { max-width: 1360px; width: 100%; box-sizing: border-box; margin: 0 auto; }
    .pl-update .pl-preview { margin: 0; padding: 0; border: 0; font-size: var(--t-s); color: var(--ink); display: flex; flex-direction: column; gap: var(--sp-4); }
    .pl-up-head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-4); flex-wrap: wrap; }
    .pl-up-head > div { display: flex; flex-direction: column; gap: 4px; }
    .pl-up-head h2 { margin: 0; font-size: 20px; line-height: 28px; font-weight: 600; letter-spacing: -0.01em; color: var(--ink); display: flex; align-items: center; gap: var(--sp-3); }
    .pl-up-delta { font-weight: 500; color: var(--ink2); }
    .pl-up-crumb, .pl-up-sub { color: var(--ink3); font-size: var(--t-min); }
    .pl-up-sub { color: var(--ink2); font-size: var(--t-s); }
    .pl-up-aside { display: flex; align-items: center; gap: var(--sp-2); }
    .pl-steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(9, minmax(0, 1fr)); gap: 6px; }
    .pl-steps li { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .pl-step-bar { height: 4px; border-radius: 2px; background: var(--line); }
    .pl-step-bar[data-tone="ok"] { background: var(--ok); }
    .pl-step-bar[data-tone="warn"] { background: var(--warn); }
    .pl-step-bar[data-tone="bad"] { background: var(--bad); }
    .pl-step-bar[data-tone="acc"] { background: var(--acc); }
    .pl-step-name { font-size: var(--t-min); font-weight: 500; color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .pl-up-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--sp-4); align-items: start; }
    @media (max-width: 900px) { .pl-up-cols { grid-template-columns: minmax(0, 1fr); } }
    .pl-upcard { background: var(--card); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; min-width: 0; }
    .pl-upcard-h { padding: 12px 16px; display: flex; align-items: center; gap: var(--sp-2); background: var(--e1); }
    .pl-upcard-h h3 { margin: 0; font-size: var(--t-s); font-weight: 600; color: var(--ink); }
    .pl-upcard-h .lbl { margin-left: auto; }
    .pl-upcard .tbl-wrap { border: 0; border-radius: 0; background: transparent; }
    .pl-upcard h4 { margin: 0; padding: 10px 16px 4px; font-size: var(--t-min); font-weight: 500; color: var(--ink3); }
    .pl-cat-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: var(--sp-3); padding: 12px 16px; border-top: 1px solid var(--line); }
    .pl-cat-title { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .pl-cat-title b { font-weight: 500; }
    .pl-cat-title span { color: var(--ink3); font-size: var(--t-min); }
    .pl-cat-diff { background: transparent; border-color: transparent; color: var(--ink2); }
    .pl-cat-diff:hover { background: var(--hover); }
    .pl-cat-files { display: flex; flex-direction: column; gap: 6px; padding: 0 16px 12px; }
    .pl-file-head { display: flex; align-items: center; gap: var(--sp-2); width: 100%; text-align: left; font: inherit; font-size: var(--t-s); color: var(--ink); background: var(--e1); border: 1px solid var(--line); border-radius: var(--r); padding: 8px var(--sp-3); cursor: pointer; }
    .pl-file-head:hover { background: var(--hover); }
    .pl-file-head .lbl, .pl-file-head .badge { flex: none; white-space: nowrap; }
    .pl-file-head .mono { min-width: 0; flex-grow: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pl-diff { font-family: var(--mono); font-size: var(--t-min); background: var(--e2); border-radius: var(--r); padding: 8px; overflow-x: auto; margin: 4px 0 0; }
    .pl-diff span { display: block; white-space: pre; }
    .pl-diff .add { background: var(--ok-soft); color: var(--ok); }
    .pl-diff .del { background: var(--bad-soft); color: var(--bad); }
    .pl-diff .hunk { color: var(--ink3); }
    .pl-up-foot { display: flex; align-items: center; gap: var(--sp-4); flex-wrap: wrap; padding: 12px 16px; background: var(--e1); border: 1px solid var(--line); border-radius: var(--r); color: var(--ink2); }
    .pl-up-formula { margin-left: auto; font-family: var(--mono); font-size: var(--t-min); color: var(--ink2); white-space: nowrap; }
    .pl-up-formula b { color: var(--rub); font-weight: 500; }
    [data-focused="true"] { border-radius: var(--r); background: var(--acc-soft); box-shadow: 0 0 0 2px var(--acc); }
  `;
  document.head.append(style);
}

const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString('fr-FR') : '—');

function relativeAge(minutes) {
  if (minutes == null) return 'aucun';
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${Math.round(minutes)} min`;
  if (minutes < 60 * 24) return `il y a ${Math.round(minutes / 60)} h`;
  return `il y a ${Math.round(minutes / 60 / 24)} j`;
}

function ciWord(status) {
  return { success: 'réussie', passed: 'réussie', failure: 'échouée', failed: 'échouée' }[status] || 'inconnue';
}

function ciDotClass(status) {
  return { success: 'ok', passed: 'ok', failure: 'bad', failed: 'bad' }[status] || '';
}

function dot(cls) {
  const span = document.createElement('span');
  span.className = 'dot' + (cls ? ' ' + cls : '');
  return span;
}

// Pastille pleine (seconde marche) : remplace le point + mot pour kit/CI/
// doctor/mémoire (Inspecteur et Flotte), le type de proposition et le nœud
// du déroulé — `.chip.pill.<cls>` (shell.css) porte un fond saturé et
// `--onfill` comme texte ; un `cls` vide garde l'apparence neutre de `.chip`.
function pill(cls, word) {
  const span = document.createElement('span');
  span.className = 'chip pill' + (cls ? ' ' + cls : '');
  span.append(icon(STATE_ICON[cls] ?? 'info'), word);
  return span;
}

function row(...children) {
  const div = document.createElement('div');
  div.className = 'row';
  div.append(...children);
  return div;
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

// La fiche ne calcule jamais `doctor` au premier rendu (~350 ms, issue #548) :
// « diagnostic indisponible » y était donc affiché à chaque fois, sans cause.
// Le bloc dit ce qu'il en est et porte le geste qui le calcule, à la demande.
function doctorOnDemand(ctx, slug) {
  const box = document.createElement('div');
  box.className = 'pl-doctor';
  const note = text('div', 'lbl', "Diagnostic non lancé : il n'est pas calculé à l'ouverture de la fiche.");
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn';
  button.textContent = 'Lancer le diagnostic';
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Diagnostic en cours…';
    try {
      const report = await ctx.api.doctor(slug || undefined);
      box.replaceChildren(
        pill(report.ok ? 'ok' : 'bad', report.ok ? 'doctor conforme' : 'doctor en écart'),
        ...(report.ok ? [] : [text('div', 'lbl', 'Détail : grimoire doctor, ou l\'onglet Problèmes du dock.')]),
      );
    } catch (error) {
      note.textContent = 'Diagnostic impossible : ' + error.message;
      button.disabled = false;
      button.textContent = 'Réessayer';
    }
  });
  box.append(note, button);
  return box;
}

// ── Statut du kit : aligné (contenu à jour) ≠ installé (CLI qui tourne) ─────
//
// `kit.upToDate` dit qu'aucun fichier livré n'a de révision plus récente au
// catalogue — l'invariant qui compte pour l'utilisateur. `kit.aligned` est la
// version qui a écrit ce contenu, `kit.installed` la version du CLI qui vient
// de répondre : deux nombres différents pour une même raison saine (un fichier
// n'a pas changé depuis 3.36.0 alors que le CLI est passé en 3.38.0, cf.
// project_health.py:kit_alignment). Affirmer « à jour » à côté de deux
// versions différentes se lit comme une contradiction (#288) ; « aligné »
// reste vrai dans les deux cas, « à jour » n'est employé que quand les deux
// versions coïncident réellement.
function kitStatus(kit) {
  if (!kit || !kit.scaffolded) {
    return { word: kit ? 'non initialisé' : 'indisponible', dot: kit ? 'warn' : null };
  }
  if (!kit.upToDate) {
    return { word: `en retard (${kit.behind})`, dot: 'warn' };
  }
  if (kit.aligned && kit.installed && kit.aligned !== kit.installed) {
    return { word: 'aligné', dot: 'ok' };
  }
  return { word: 'à jour', dot: 'ok' };
}

// ── Trois versions du kit, quand elles diffèrent (issue #510 point 4c) ─────
//
// `kit.aligned` (contenu du projet) et `kit.installed` (CLI qui vient de
// répondre, ici le serveur cockpit) ne disent rien de l'outil qui gouverne
// vraiment le projet au quotidien — un pipx séparé peut être plus vieux sans
// qu'aucun des deux autres chiffres ne le montre. `kit.projectTool` (server
// side : `.venv/bin/grimoire` ou `project-context.yaml` `tool:`) porte cette
// troisième version, ou `null` quand le projet ne déclare rien de vérifiable.
function projectToolLine(kit) {
  if (!kit || !kit.aligned) return null;
  const known = [kit.aligned, kit.installed, kit.projectTool].filter(Boolean);
  const distinct = new Set(known);
  // Deux déclencheurs, l'un suffit : `aligned` et `installed` divergent déjà
  // (le cas générique — un projet pas encore aligné sur le serveur cockpit),
  // ou l'outil déclaré par le projet ajoute une 3e valeur distincte (le cas
  // réel de l'issue : cockpit et projet d'accord sur le kit, pipx en retard
  // sans que rien d'autre ne le montre). Sans l'un des deux, rien à ajouter —
  // afficher « inconnu » sur chaque projet sans `.venv` dédié serait du bruit.
  const alreadyDivergent = Boolean(kit.installed) && kit.aligned !== kit.installed;
  const toolAddsDivergence = Boolean(kit.projectTool) && distinct.size > 1;
  if (!alreadyDivergent && !toolAddsDivergence) return null;
  const toolWord = kit.projectTool
    ? kit.projectTool
    : 'inconnu, vérifiez `grimoire --version` dans le projet';
  return `outil du projet : ${toolWord}`;
}

// ── Niveau Flotte (cockpit) ──────────────────────────────────────────────────
//
// Un seul appel `GET /api/fleet` (#541, backend #542) — chaque affichage
// relançait avant ça `projects()` PUIS `health()` + `memoryStatus()` pour
// TOUS les projets du registre (34 requêtes pour 17 projets, 20 pour 10,
// constaté en pilotant neuf projets réels l'un après l'autre depuis le
// cockpit — issue #510 point 5). Le serveur (`project_health.py::
// fleet_status`) calcule désormais ces N entrées en parallèle (threads) et
// les rend en une seule réponse, `name` compris (sinon la Flotte perdrait
// l'affichage du nom humain — voir `_fleet_entry`). Le cache 60 s subsiste
// pour absorber les allers-retours Flotte/Projet dans la même session ;
// `force` (bouton « Rafraîchir la flotte ») et `invalidateFleetCache()`
// (appelé depuis `renderSheet` après une mise à jour) restent les deux
// seules façons de le court-circuiter — il ne porte plus qu'un seul blob
// (toute la flotte), pas une entrée par slug, puisqu'un seul appel réseau
// les ramène toutes ensemble de toute façon.
const FLEET_CACHE_TTL_MS = 60_000;
let fleetCache = null; // { at, rows }

function invalidateFleetCache() {
  fleetCache = null;
}

async function loadFleet(ctx, { force = false } = {}) {
  const now = Date.now();
  if (!force && fleetCache && now - fleetCache.at < FLEET_CACHE_TTL_MS) {
    return fleetCache.rows;
  }
  const payload = await ctx.api.fleet();
  // `_fleet_entry` (best-effort côté serveur) rend `{ error }` plutôt que
  // `null` pour un projet cassé — jamais le contrat qu'attendaient
  // `renderFleet` (ex. `health.kit`, `memory.state`), qui
  // lisaient jusqu'ici toujours soit un payload complet, soit `null` (`ctx.
  // api.health(slug).catch(() => null)`). On aligne ici sur ce contrat.
  const rows = (payload.projects || []).map((p) => ({
    entry: { slug: p.slug, path: p.path, name: p.name, managed: p.managed },
    health: p.health && !p.health.error ? p.health : null,
    memory: p.memory && !p.memory.error ? p.memory : null,
  }));
  fleetCache = { at: now, rows };
  return rows;
}

// ── « Nouveau projet » (#172) : bouton d'en-tête + panneau repliable ───────
//
// Toujours visible, y compris sur un registre vide — c'est justement là que
// le geste manque le plus. Le bouton prend place dans l'en-tête de la Flotte
// (maquette Douce), le formulaire s'ouvre dessous, dans `panel`. `onCreated`
// ferme le panneau et bascule sur le projet créé, par le même chemin que
// cliquer une ligne du tableau (aucune route dédiée à réinventer, et le
// sélecteur de projets — ce même tableau — le montrera dès qu'on y revient,
// sans redémarrer le cockpit).
function renderNewProject(ctx, onCreated) {
  const panel = document.createElement('div');
  panel.className = 'pl-section';
  panel.hidden = true;
  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'btn pri';
  toggleBtn.append(icon('plus'), 'Nouveau projet');

  let open = false;
  let formHost = null;
  toggleBtn.addEventListener('click', () => {
    open = !open;
    if (!open) {
      if (formHost) { formHost.remove(); formHost = null; }
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    formHost = document.createElement('div');
    formHost.className = 'pl-card';
    formHost.append(text('p', 'lbl', 'Chargement…'));
    panel.append(formHost);
    renderCreateProjectForm(ctx, (slug) => {
      open = false;
      if (formHost) { formHost.remove(); formHost = null; }
      panel.hidden = true;
      onCreated(slug);
    }).then((node) => { if (formHost) formHost.replaceWith(node); formHost = node; });
  });
  return { button: toggleBtn, panel };
}

// ── Attention par projet (P65) ───────────────────────────────────────────────
//
// Les badges de la colonne « Attention » sont les éléments que l'API
// d'attention (`/api/workspace/attention`, P22) compte déjà pour chaque
// projet — jamais une règle recalculée ici. Le retard du kit a sa propre
// colonne, et un service « résolu » n'attend plus rien : ni l'un ni l'autre
// ne fait un badge.
const ATTENTION_WORDS = {
  checkpoint: ['checkpoint à valider', 'checkpoints à valider', 'warn'],
  question: ['question', 'questions', 'warn'],
  proposal: ['proposition', 'propositions', 'warn'],
  review_blocked: ['revue bloquée', 'revues bloquées', 'warn'],
  task_blocked: ['tâche bloquée', 'tâches bloquées', 'bad'],
  directive_unacked: ['consigne non accusée', 'consignes non accusées', 'warn'],
  claim_orphaned: ['claim sans session', 'claims sans session', 'warn'],
  service: ['service en échec', 'services en échec', 'bad'],
  unreadable: ['projet illisible', 'projets illisibles', 'bad'],
  probe_failed: ['sonde en échec', 'sondes en échec', 'bad'],
};
const ATTENTION_BADGES_MAX = 3;

/** `slug` → éléments d'attention du projet (sans retard de kit ni résolus). */
function attentionBySlug(payload) {
  const map = new Map();
  for (const section of payload?.sections || []) {
    for (const item of section.items || []) {
      if (!ATTENTION_WORDS[item.kind]) continue;
      const slug = item.project?.slug;
      if (!slug) continue;
      if (!map.has(slug)) map.set(slug, []);
      map.get(slug).push(item);
    }
  }
  return map;
}

/** Les badges d'un projet : un par type, avec son compte, du plus grave au plus doux. */
function attentionBadges(items) {
  const counts = new Map();
  for (const item of items) counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
  const badges = [...counts].map(([kind, n]) => {
    const [one, many, tone] = ATTENTION_WORDS[kind];
    return { tone, label: `${n} ${n === 1 ? one : many}` };
  });
  badges.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === 'bad' ? -1 : 1));
  const shown = badges.slice(0, ATTENTION_BADGES_MAX).map((b) => badge(b.label, b.tone));
  if (badges.length > shown.length) {
    const rest = badges.slice(ATTENTION_BADGES_MAX);
    shown.push(badge(`+${rest.length}`, '', rest.map((b) => b.label).join(' · ')));
  }
  return shown;
}

// ── Kit d'un projet : version + écart (colonne « Kit ») ─────────────────────

function kitCellOf(r) {
  if (!r.health) return { main: 'indisponible', sub: 'lecture impossible', behind: false };
  if (!r.entry.managed) return { main: 'non initialisé', sub: '', behind: false };
  const kit = r.health.kit;
  if (!kit || !kit.scaffolded) return { main: kit ? 'non initialisé' : 'indisponible', sub: '', behind: false };
  const version = kit.aligned || kit.installed || 'version inconnue';
  if (kit.upToDate) {
    return { main: version, sub: kit.aligned && kit.installed && kit.aligned !== kit.installed ? 'aligné' : 'à jour', behind: false };
  }
  const n = kit.behind;
  return { main: version, sub: `${fmtInt(n)} fichier${n > 1 ? 's' : ''} d’écart`, behind: kit.catalogAvailable !== false };
}

/** « 1 active », « 2 en cours » : les exécutions encore vivantes du projet. */
function sessionsWord(health) {
  const live = (health?.activity?.runs || []).filter((run) => !run.silent).length;
  if (live) return `${live} en cours`;
  return '—';
}

const plural = (n, one, many) => `${fmtInt(n)} ${n > 1 ? many : one}`;

// Filtre de la table (texte + segment), gardé entre deux rendus : un
// rafraîchissement de la flotte ne remet pas la recherche à zéro.
const fleetFilter = { query: '', segment: 'all' };

// La Flotte a deux vues (maquette « Flotte — données ») : Projets, la table,
// et Données, la machine et qui partage quoi (P26, `donnees.js`). Le
// segmenté se pose à côté du titre ; Données n'est importée et lue qu'au
// clic, jamais au premier rendu de la Flotte.
function viewSeg(current, onView) {
  const seg = document.createElement('div');
  seg.className = 'seg pl-viewseg';
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', 'Vue');
  for (const [id, label] of [['projets', 'Projets'], ['donnees', 'Données']]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.view = id;
    button.textContent = label;
    button.setAttribute('aria-pressed', String(id === current));
    button.addEventListener('click', () => onView(id));
    seg.append(button);
  }
  return seg;
}

function withViewSeg(head, seg) {
  const title = head.querySelector('.space-title');
  if (!seg || !title) return;
  const line = document.createElement('div');
  line.className = 'pl-titlerow';
  title.replaceWith(line);
  line.append(title, seg);
}

function renderFleet(root, ctx, rows, attention, onSelect, onRefresh, { pending = false, viewSwitch = null } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-wrap pl-fleet';

  const byProject = attentionBySlug(attention);
  const asking = (r) => (byProject.get(r.entry.slug) || []).length > 0;
  const lagging = (r) => kitCellOf(r).behind;
  const alignedCount = rows.filter((r) => r.health?.kit?.upToDate).length;
  const installed = rows.map((r) => r.health?.kit?.installed).find(Boolean);
  const askingCount = rows.filter(asking).length;

  const sub = [plural(rows.length, 'projet suivi', 'projets suivis')];
  if (rows.length) {
    sub.push(`${plural(alignedCount, 'aligné', 'alignés')} sur le kit${installed ? ' ' + installed : ''}`);
    if (attention) sub.push(plural(askingCount, 'demande une action', 'demandent une action'));
    else sub.push(pending ? 'attention en cours de lecture' : 'attention indisponible');
  }

  const formula = document.createElement('span');
  formula.className = 'pl-formula';
  formula.append(text('b', null, '›'), 'grimoire cockpit list');
  const actions = [formula];
  if (onRefresh) {
    const refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'btn pl-refresh';
    refreshBtn.append(icon('refresh'), 'Rafraîchir la flotte');
    refreshBtn.title = "Relit health() et memoryStatus() pour chaque projet, sans attendre le cache (60 s).";
    refreshBtn.addEventListener('click', () => {
      refreshBtn.disabled = true;
      refreshBtn.replaceChildren(icon('refresh'), 'Rafraîchissement…');
      onRefresh();
    });
    actions.push(refreshBtn);
  }
  const create = renderNewProject(ctx, onSelect);
  actions.push(create.button);
  const head = spaceHead('Flotte', sub.join(' · '), ...actions);
  withViewSeg(head, viewSwitch);
  wrap.append(head, create.panel);

  // Le cockpit ne scanne jamais le disque (#341) : un registre vide rend une
  // flotte vide, pas une panne. Un tableau muet à zéro lignes se lisait comme
  // « le cockpit ne détecte rien » ; l'état vide nomme le geste attendu.
  if (!rows.length) {
    wrap.append(text(
      'p',
      'lbl',
      "Aucun projet enregistré. Ce cockpit lit un registre, il ne scanne pas le disque : "
      + "grimoire cockpit add <chemin> pour un projet, ou grimoire cockpit scan <racine> pour explorer un dossier.",
    ));
    root.append(wrap);
    return;
  }

  // Filtre : champ texte (nom ou chemin) + segments Tous / Demandent une
  // action / Kit en retard, avec leur compte réel.
  const bar = document.createElement('div');
  bar.className = 'pl-filterbar';
  const field = document.createElement('input');
  field.type = 'search';
  field.className = 'input pl-search';
  field.placeholder = 'Filtrer par nom ou chemin';
  field.setAttribute('aria-label', 'Filtrer les projets');
  field.value = fleetFilter.query;
  const seg = document.createElement('div');
  seg.className = 'seg';
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', 'Filtre');
  const segments = [
    ['all', 'Tous', () => true, rows.length],
    ['asking', 'Demandent une action', asking, askingCount],
    ['lagging', 'Kit en retard', lagging, rows.filter(lagging).length],
  ];
  const segButtons = new Map();
  for (const [id, label, , count] of segments) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.segment = id;
    button.append(label);
    if (id !== 'all') button.append(text('span', 'pl-seg-n', String(count)));
    if (id === 'asking' && !attention) {
      button.disabled = true;
      button.title = pending
        ? "Lecture d'attention en cours : le filtre s'active à sa réponse."
        : "La lecture d'attention n'a pas répondu : impossible de dire qui demande une action.";
    }
    button.addEventListener('click', () => { fleetFilter.segment = id; paint(); });
    segButtons.set(id, button);
    seg.append(button);
  }
  bar.append(field, seg);
  wrap.append(bar);

  const table = document.createElement('table');
  table.className = 'tbl tbl-pick';
  const headRow = document.createElement('tr');
  const headTerms = { Projet: 'projet', Kit: 'kit' };
  for (const label of ['Projet', 'Attention', 'Kit', 'Sessions', 'Dernier signe', '']) {
    const th = text('th', null, label);
    if (!label) th.setAttribute('aria-label', 'Ouvrir');
    if (headTerms[label]) {
      th.dataset.term = headTerms[label];
      const hint = icon('info');
      hint.classList.add('th-hint');
      th.append(hint);
    }
    headRow.append(th);
  }
  const thead = document.createElement('thead');
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  table.append(tbody);
  const tableWrap = document.createElement('div');
  tableWrap.className = 'tbl-wrap pl-desktop';
  tableWrap.append(table);
  const cards = document.createElement('div');
  cards.className = 'pl-cards';
  const none = text('p', 'lbl pl-none', 'Aucun projet ne correspond à ce filtre.');
  none.hidden = true;
  wrap.append(tableWrap, cards, none);

  const openButton = (r) => {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'btn ghost pl-open';
    open.setAttribute('aria-label', `Ouvrir ${r.entry.name || r.entry.slug}`);
    open.append(icon('chevron'));
    return open;
  };

  function paint() {
    const query = fleetFilter.query.trim().toLowerCase();
    const segment = segments.find(([id]) => id === fleetFilter.segment) || segments[0];
    const shown = rows.filter((r) => segment[2](r)
      && (!query || `${r.entry.name || ''} ${r.entry.slug} ${r.entry.path}`.toLowerCase().includes(query)));
    for (const [id, button] of segButtons) button.setAttribute('aria-pressed', String(id === segment[0]));
    tbody.replaceChildren();
    cards.replaceChildren();
    none.hidden = shown.length > 0;
    tableWrap.hidden = shown.length === 0;
    for (const r of shown) {
      const name = r.entry.name || r.entry.slug;
      const badges = attentionBadges(byProject.get(r.entry.slug) || []);
      const kit = kitCellOf(r);

      const tr = document.createElement('tr');
      tr.tabIndex = 0;
      tr.dataset.project = r.entry.slug;
      tr.addEventListener('click', () => onSelect(r.entry.slug));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(r.entry.slug); });

      const nameCell = document.createElement('td');
      const path = text('div', 'lbl mono pl-path', r.entry.path);
      path.title = r.entry.path;
      nameCell.append(text('div', 'pl-name', name), path);
      const attentionCell = document.createElement('td');
      if (badges.length) {
        const list = document.createElement('div');
        list.className = 'pl-badges';
        list.append(...badges);
        attentionCell.append(list);
      } else {
        attentionCell.append(text('span', 'lbl', pending && !attention ? '…' : '—'));
      }
      const kitCell = document.createElement('td');
      kitCell.append(text('div', null, kit.main));
      if (kit.sub) kitCell.append(text('div', 'lbl', kit.sub));
      const openCell = document.createElement('td');
      openCell.append(openButton(r));
      tr.append(
        nameCell, attentionCell, kitCell,
        text('td', null, sessionsWord(r.health)),
        text('td', 'lbl', relativeAge(r.health?.activity?.ageMinutes)),
        openCell,
      );
      tbody.append(tr);

      // Mobile : une carte par projet, mêmes données.
      const card = document.createElement('div');
      card.className = 'pl-card';
      card.tabIndex = 0;
      card.addEventListener('click', () => onSelect(r.entry.slug));
      card.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(r.entry.slug); });
      const cardPath = text('div', 'lbl mono pl-path', r.entry.path);
      cardPath.title = r.entry.path;
      card.append(text('div', 'pl-name', name), cardPath);
      if (badges.length) {
        const list = document.createElement('div');
        list.className = 'pl-badges';
        list.append(...badges.map((b) => b.cloneNode(true)));
        card.append(list);
      }
      card.append(text(
        'div', 'pl-card-row lbl',
        [kit.sub ? `${kit.main} · ${kit.sub}` : kit.main, relativeAge(r.health?.activity?.ageMinutes)].join(' · '),
      ));
      cards.append(card);
    }
  }

  field.addEventListener('input', () => { fleetFilter.query = field.value; paint(); });
  paint();
  root.append(wrap);
}

// ── Niveau Projet (fiche) ────────────────────────────────────────────────────

async function loadSheet(ctx, slug) {
  // #548 : un seul aller-retour (`GET /api/workspace/sheet`) là où sept
  // appels partaient un par un — health, memoryStatus, doctor, agents,
  // proposals, setupRun, flowRuns('project-upgrade') — chacun payant
  // individuellement un tour réseau, même une fois servi par le cache
  // serveur de #542. Les sept sous-vues sont désormais calculées en
  // parallèle côté serveur (`workspace_api.sheet_view`, même principe que
  // `fleet_status`) ; `doctor` en est volontairement absent (mesuré comme
  // le vrai coût des sept, ~350ms contre ~120ms pour `health` une fois
  // décaché) — l'onglet Problèmes (`ctx.api.doctor`) le rend séparément, à
  // la demande, jamais au premier rendu. `slug` explicite (jamais
  // `host.project` implicite) : cette fiche peut être celle d'un AUTRE
  // projet que celui déjà résolu par l'hôte (navigation Flotte → Projet).
  const sheet = await ctx.api.sheet(slug).catch(() => null);
  return (
    sheet || {
      health: null, memory: null, doctor: null, agents: null,
      proposals: null, setupRun: null, upgradeRuns: null, flowRuns: null, name: null,
    }
  );
}

// ── Propositions d'artefact (#395) : à la répétition d'un non-choix ────────
//
// Toujours dans la fiche projet, section agents (#382) : une proposition
// n'est rien d'autre qu'une décision différée sur un agent, un skill, ou une
// réparation (`repair`, #502 — une référence morte que la mise à jour a
// trouvée mais n'a pas touchée d'elle-même). Décider — accepter ou refuser —
// fonctionne pour n'importe quel projet du registre depuis le cockpit
// (#490) : c'est la même porte que le bouton « Mettre à jour » juste
// au-dessus, jamais la garde `readOnly` générale de la vue de travail.
// Accepter écrit l'artefact réel (ou applique la substitution `repair`
// évidente) et re-fetch `agents()` pour que la table au-dessus le montre
// aussitôt ; refuser ne fait que marquer la proposition.

// ── Retour d'action sur place ───────────────────────────────────────────────
//
// Depuis la refonte (P62) la Console n'est plus dans la coque : un refus
// envoyé à `ctx.dock` n'était lu par personne. Chaque action porte donc son
// retour à côté d'elle : le bouton se désactive avec un libellé d'attente,
// le message du serveur s'affiche en clair dans `actionStatus` (shell.js),
// puis le bouton redevient utilisable.

/**
 * Exécute `action` derrière `button` : libellé d'attente, boutons voisins
 * gelés le temps de la requête, échec affiché dans `status`. Rend `true`
 * quand l'action a abouti.
 */
async function runInline(button, pendingLabel, status, action, siblings = []) {
  const label = button.textContent;
  const frozen = [button, ...siblings].map((b) => [b, b.disabled]);
  for (const [b] of frozen) b.disabled = true;
  button.textContent = pendingLabel;
  button.setAttribute('aria-busy', 'true');
  setActionStatus(status, '');
  try {
    await action();
    return true;
  } catch (error) {
    setActionStatus(status, failureText(error), 'bad');
    return false;
  } finally {
    button.textContent = label;
    button.removeAttribute('aria-busy');
    for (const [b, was] of frozen) b.disabled = was;
  }
}

/** `texte avec `code`` → nœuds, les segments entre accents graves en `<code>`. */
function withInlineCode(value) {
  const frag = document.createDocumentFragment();
  String(value || '').split('`').forEach((part, index) => {
    if (!part) return;
    frag.append(index % 2 ? Object.assign(document.createElement('code'), { textContent: part }) : document.createTextNode(part));
  });
  return frag;
}

// `artifact_ref` porte "<chemin>:<ligne> → <cible morte>" (même forme que
// `_dead_reference_strings`, core/integrity.py) — jamais autre chose pour un
// type `repair`. `indexOf` plutôt que `split(':')` : un chemin Windows aurait
// pu porter un deuxième ':' (lecteur), même si le kit ne cible que POSIX ici.
function parseArtifactRef(ref) {
  const raw = String(ref || '');
  const colon = raw.indexOf(':');
  if (colon < 0) return null;
  const rest = raw.slice(colon + 1);
  const arrow = rest.indexOf(' → ');
  if (arrow < 0) return null;
  const line = parseInt(rest.slice(0, arrow), 10);
  const file = raw.slice(0, colon);
  if (!file || Number.isNaN(line)) return null;
  return { file, line };
}

// `artifact_type` (grimoire.proposals) porte six valeurs — avant la revue
// 2026-09, seules trois avaient un libellé et toutes partageaient le même
// point orange (`dot('warn')`), sans distinction visuelle. `PROPOSAL_TYPE_DOT`
// répare ça sur le vocabulaire des séries et des états déjà en place —
// jamais la couleur seule, toujours doublée du libellé ci-dessous.
const PROPOSAL_TYPE_LABEL = {
  skill: 'skill',
  repair: 'réparation',
  agent: 'agent',
  'memory-link': 'lien mémoire',
  'override-migration': 'migration override',
  'needs-hosts': 'besoins / hôtes',
};
const PROPOSAL_TYPE_DOT = {
  skill: 's2',
  repair: 'warn',
  agent: 's1',
  'memory-link': 'ok',
  'override-migration': 'bad',
  'needs-hosts': 's3',
};

function proposalTypeLabel(p) {
  return PROPOSAL_TYPE_LABEL[p.artifact_type] || p.artifact_type || 'agent';
}

function proposalTypeDot(p) {
  return PROPOSAL_TYPE_DOT[p.artifact_type] || '';
}

function proposalFacts(p) {
  if (p.artifact_type === 'repair') {
    // Ni compte de non-choix ni agent de repli pour une réparation — c'est
    // un défaut de fichier, pas un manque de spécialiste. `carrier_reason`
    // porte déjà toute l'explication (substitution trouvée, ou son absence).
    const bits = [];
    if (p.category) bits.push(`catégorie « ${p.category} »`);
    if (p.carrier_reason) bits.push(p.carrier_reason);
    return bits.join(' · ');
  }
  const bits = [`${fmtInt(p.count)} non-choix`];
  if (p.category) bits.push(`catégorie « ${p.category} »`);
  if (p.artifact_type === 'skill' && p.target_agent) bits.push(`à attacher à ${p.target_agent}`);
  // Le porteur retenu peut différer du repli brut observé (`fallback_agent`)
  // quand ce dernier est la persona d'entrée — `carrier_reason` explique le
  // choix (issue #402) : « repli observé », « porteur par catégorie : X »,
  // ou « persona d'entrée exclue, aucun porteur : agent ».
  if (p.carrier_reason) bits.push(p.carrier_reason);
  return bits.join(' · ');
}

// `slug` cible le projet dont cette fiche parle — jamais celui, ambiant, que
// le cockpit sert par défaut : c'est ce que `ctx.api.proposalAction` envoie
// désormais explicitement (#490), pour que décider une proposition sur un
// projet de la Flotte ne touche jamais un autre projet par erreur de query
// string ambiante.
function renderProposalsSection(ctx, proposalsPayload, onChanged, slug) {
  const section = document.createElement('div');
  section.className = 'pl-section';
  section.append(text('h3', null, 'Propositions'));

  const all = proposalsPayload?.proposals || [];
  const pending = all.filter((p) => p.status === 'pending');
  if (!pending.length) {
    // `.soft` (--ink2), pas `.lbl` (--ink3) : à cette taille de police, --ink3
    // ne tient pas le contraste 4.5:1 mesuré par
    // `tests/e2e/test_workspace_shell.py::test_aucune_encre_rendue_sous_45`.
    section.append(text('p', 'soft', 'Aucune proposition en attente — le déclencheur agit sur les non-choix répétés (`grimoire agent-miss`).'));
    return section;
  }

  const list = document.createElement('div');
  list.className = 'pl-props';
  for (const proposal of pending) {
    const line = document.createElement('div');
    line.className = 'pl-prop-row';
    line.dataset.proposal = proposal.slug;

    // Ligne 1 : type et nom. Ligne 2 : les faits, en secondaire.
    const head = document.createElement('div');
    head.className = 'pl-prop-head';
    head.append(
      pill(proposalTypeDot(proposal), proposalTypeLabel(proposal)),
      text('span', 'pl-prop-name', proposal.specialty),
    );
    line.append(head);
    const facts = proposalFacts(proposal);
    if (facts) line.append(text('p', 'pl-prop-facts', facts));
    if (proposal.use_when) line.append(text('p', 'pl-prop-facts', proposal.use_when));

    // Ligne 3 : actions, puis leur retour sur place.
    const actions = document.createElement('div');
    actions.className = 'pl-prop-actions';
    const status = actionStatus();

    const isRepair = proposal.artifact_type === 'repair';
    // `accept_blocker` (grimoire.proposals) : le refus qu'« Accepter »
    // rencontrerait à coup sûr, calculé par le serveur avec le même texte
    // que le refus réel. Dans ce cas on ne montre pas un bouton qui échoue
    // toujours, mais ce qu'il reste à faire à la main.
    const blocker = proposal.accept_blocker || '';
    const decide = (button, action, pendingLabel, siblings) => runInline(button, pendingLabel, status, async () => {
      const result = await ctx.api.proposalAction(proposal.slug, action, slug);
      if (!result || !result.ok) throw Object.assign(new Error(result?.error || 'refus sans motif'), { status: 200 });
      onChanged();
    }, siblings);

    const acceptBtn = blocker ? null : document.createElement('button');
    const rejectBtn = document.createElement('button');
    if (acceptBtn) {
      acceptBtn.type = 'button';
      acceptBtn.className = 'btn pri';
      acceptBtn.textContent = 'Accepter';
      acceptBtn.addEventListener('click', () => decide(acceptBtn, 'accept', 'Acceptation…', [rejectBtn]));
      actions.append(acceptBtn);
    }

    rejectBtn.type = 'button';
    rejectBtn.className = 'btn';
    rejectBtn.textContent = 'Refuser';
    rejectBtn.addEventListener('click', () => decide(rejectBtn, 'reject', 'Refus…', acceptBtn ? [acceptBtn] : []));
    actions.append(rejectBtn);

    if (isRepair) {
      const ref = parseArtifactRef(proposal.artifact_ref);
      if (ref) {
        const openBtn = document.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'btn';
        openBtn.textContent = 'Ouvrir le fichier';
        openBtn.title = `${ref.file}:${ref.line}`;
        openBtn.addEventListener('click', () => ctx.goto('source', { file: ref.file, line: ref.line }));
        actions.append(openBtn);
      }
    }

    // Ce qu'« Accepter » ne fera jamais : dit avant les actions, en clair.
    if (blocker) {
      const manual = document.createElement('p');
      manual.className = 'pl-prop-manual';
      manual.dataset.manual = 'true';
      const lead = document.createElement('b');
      lead.textContent = 'À faire à la main';
      const body = document.createElement('span');
      body.append(lead, document.createTextNode(' — '), withInlineCode(blocker));
      manual.append(icon('info'), body);
      line.append(manual);
    }

    line.append(actions, status);
    list.append(line);
  }
  section.append(list);
  return section;
}

// ── Agents (#374) : liste + inspecteur d'édition ────────────────────────────

const TOOL_VERBS = ['read', 'search', 'edit', 'execute', 'web'];

// Fraîcheur (#396) : verdict calculé sur le tag `agent.dispatch` du journal,
// le même que `grimoire doctor` — distinct du compteur `usage` ci-dessous
// (tout sous-agent tracé, #374). `judged` vaut faux quand le journal est trop
// jeune pour le seuil configuré : dans ce cas on ne dit rien plutôt que de
// confondre absence de données et absence d'usage.
function freshnessWord(freshness) {
  if (!freshness || !freshness.judged) return null;
  // Plancher par agent : un fichier plus jeune que le seuil n'a pas eu le
  // temps d'être choisi — ce n'est pas la même chose que « personne n'en
  // veut ». Le dire explicitement plutôt que de laisser lire « jamais
  // invoqué » comme un signal de dette.
  if (freshness.too_recent) return 'trop récent pour juger';
  if (freshness.last_seen == null) return 'jamais invoqué';
  return `invoqué il y a ${fmtInt(freshness.days_since)} j`;
}

function freshnessBadge(freshness) {
  if (!freshness || !freshness.judged) return null;
  if (freshness.too_recent) {
    return badge('trop récent', 'acc', "Le fichier de définition de cet agent est plus jeune que le seuil de fraîcheur configuré — pas encore eu le temps d'être choisi.");
  }
  if (!freshness.stale) return null;
  return badge('périmé', 'warn', 'Aucun agent.dispatch journalisé depuis le seuil de fraîcheur configuré (project-context.yaml: agents.freshness_threshold_days).');
}

/** La couche d'un agent : `overrides` se signale (accent), le reste est neutre. */
function layerBadgeFor(agent) {
  return badge(agent.layer, agent.layer === 'overrides' ? 'acc' : '');
}

function usageWord(agent) {
  const usage = agent.usage;
  const fresh = freshnessWord(agent.freshness);
  const countPart = usage && usage.choices ? `${fmtInt(usage.choices)} choix` : null;
  if (countPart && fresh) return `${countPart} · ${fresh}`;
  if (fresh) return fresh;
  if (countPart) {
    const ago = relativeAge((Date.now() - new Date(usage.last_chosen_at).getTime()) / 60000);
    return `${countPart} · dernier ${ago}`;
  }
  return 'jamais choisi';
}

function renderAgentsTable(ctx, agentsPayload, selectedName, onSelect) {
  const section = document.createElement('div');
  section.className = 'pl-section';
  const heading = text('h3', null, 'Agents');
  heading.dataset.term = 'agent';
  section.append(heading);

  const agents = agentsPayload?.agents || [];
  if (!agents.length) {
    section.append(text('p', 'lbl', "Aucun agent lisible sur ce projet — l'API des agents a répondu vide ou n'a pas répondu."));
    return section;
  }

  const tableWrap = document.createElement('div');
  tableWrap.className = 'tbl-wrap pl-desktop';
  const table = document.createElement('table');
  table.className = 'tbl tbl-pick';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Agent', 'Couche', 'Outils', 'Skills', 'Usage']) {
    headRow.append(text('th', null, label));
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement('tbody');
  for (const agent of agents) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    tr.setAttribute('aria-current', String(agent.name === selectedName));
    tr.addEventListener('click', () => onSelect(agent.name));
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(agent.name); });

    const nameCell = document.createElement('td');
    const nameRow = row(text('span', null, agent.name));
    if (agent.entry_point) nameRow.append(text('span', 'lbl', '· entrée'));
    nameCell.append(nameRow);
    tr.append(nameCell);

    const layerCell = document.createElement('td');
    const layerBadge = layerBadgeFor(agent);
    if (agent.layer === 'overrides') layerBadge.dataset.term = 'override';
    layerCell.append(layerBadge);
    tr.append(layerCell);

    tr.append(text('td', 'lbl', agent.tools.join(', ') || '—'));
    tr.append(text('td', 'lbl', agent.skills.length ? String(agent.skills.length) : '—'));
    const usageCell = document.createElement('td');
    usageCell.append(text('span', 'lbl', usageWord(agent)));
    const freshBadge = freshnessBadge(agent.freshness);
    if (freshBadge) usageCell.append(document.createTextNode(' '), freshBadge);
    tr.append(usageCell);
    tbody.append(tr);
  }
  table.append(tbody);
  tableWrap.append(table);
  section.append(tableWrap);
  return section;
}

function fieldRow(labelText, node) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-field';
  const label = document.createElement('label');
  label.textContent = labelText;
  wrap.append(label, node);
  return wrap;
}

function renderAgentInspector(ctx, agentsPayload, agent, callbacks) {
  const block = document.createElement('div');
  block.className = 'pl-insp-block';
  block.dataset.agentInspector = 'true';
  block.append(row(text('h4', null, agent.name), layerBadgeFor(agent)));
  const usageRow = row(text('span', 'lbl', usageWord(agent)));
  const inspBadge = freshnessBadge(agent.freshness);
  if (inspBadge) usageRow.append(inspBadge);
  block.append(usageRow);

  const readOnly = ctx.host.readOnly;
  const saveLabel = (verb) => (readOnly ? 'Écriture désactivée (cockpit)' : verb);

  // ── Clause d'emploi ──────────────────────────────────────────────────
  const useWhen = document.createElement('textarea');
  useWhen.rows = 2;
  useWhen.value = agent.use_when || '';
  useWhen.disabled = readOnly;
  const dontUseWhen = document.createElement('textarea');
  dontUseWhen.rows = 2;
  dontUseWhen.value = agent.dont_use_when || '';
  dontUseWhen.disabled = readOnly;

  const saveClauseBtn = document.createElement('button');
  saveClauseBtn.type = 'button';
  saveClauseBtn.className = 'btn';
  saveClauseBtn.textContent = saveLabel('Enregistrer la clause');
  saveClauseBtn.disabled = readOnly;
  const clauseStatus = actionStatus();
  saveClauseBtn.addEventListener('click', async () => {
    ctx.dock.echo(`# clause d'emploi — ${agent.name}`);
    const ok = await runInline(saveClauseBtn, 'Enregistrement…', clauseStatus, () =>
      ctx.api.agentFields(agent.name, { use_when: useWhen.value, dont_use_when: dontUseWhen.value }));
    if (ok) callbacks.refresh(agent.name);
  });

  block.append(
    fieldRow('use_when', useWhen),
    fieldRow('dont_use_when', dontUseWhen),
    saveClauseBtn,
    clauseStatus,
  );
  if (agent.tool_boundary) block.append(text('div', 'lbl', 'Frontière : ' + agent.tool_boundary));

  // ── Outils ───────────────────────────────────────────────────────────
  const toolOpts = document.createElement('div');
  toolOpts.className = 'pl-tool-opts';
  const toolChecks = {};
  for (const verb of TOOL_VERBS) {
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = agent.tools.includes(verb);
    cb.disabled = readOnly;
    toolChecks[verb] = cb;
    label.append(cb, document.createTextNode(verb));
    toolOpts.append(label);
  }
  const saveToolsBtn = document.createElement('button');
  saveToolsBtn.type = 'button';
  saveToolsBtn.className = 'btn';
  saveToolsBtn.textContent = saveLabel('Enregistrer les outils');
  saveToolsBtn.disabled = readOnly;
  const toolsStatus = actionStatus();
  saveToolsBtn.addEventListener('click', async () => {
    const chosen = TOOL_VERBS.filter((v) => toolChecks[v].checked);
    ctx.dock.echo(`# outils — ${agent.name} → ${chosen.join(', ')}`);
    const ok = await runInline(saveToolsBtn, 'Enregistrement…', toolsStatus, () =>
      ctx.api.agentFields(agent.name, { tools: chosen }));
    if (ok) callbacks.refresh(agent.name);
  });
  block.append(fieldRow('tools', toolOpts), saveToolsBtn, toolsStatus);

  // ── Contexte ─────────────────────────────────────────────────────────
  const contextArea = document.createElement('textarea');
  contextArea.rows = 3;
  contextArea.value = (agent.context || []).join('\n');
  contextArea.disabled = readOnly;
  contextArea.placeholder = 'un chemin de projet par ligne';
  const saveContextBtn = document.createElement('button');
  saveContextBtn.type = 'button';
  saveContextBtn.className = 'btn';
  saveContextBtn.textContent = saveLabel('Enregistrer le contexte');
  saveContextBtn.disabled = readOnly;
  const contextStatus = actionStatus();
  saveContextBtn.addEventListener('click', async () => {
    const paths = contextArea.value.split('\n').map((s) => s.trim()).filter(Boolean);
    ctx.dock.echo(`# contexte — ${agent.name}`);
    const ok = await runInline(saveContextBtn, 'Enregistrement…', contextStatus, () =>
      ctx.api.agentFields(agent.name, { context: paths }));
    if (ok) callbacks.refresh(agent.name);
  });
  block.append(fieldRow('context', contextArea), saveContextBtn, contextStatus);

  // ── Skills ───────────────────────────────────────────────────────────
  const skillsWrap = document.createElement('div');
  skillsWrap.className = 'pl-chips';
  const skillsStatus = actionStatus();
  for (const slug of agent.skills) {
    const chip = document.createElement('span');
    chip.className = 'pl-chip';
    chip.append(document.createTextNode(slug));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.disabled = readOnly;
    remove.setAttribute('aria-label', `Retirer ${slug}`);
    remove.addEventListener('click', async () => {
      ctx.dock.echo(`# retirer skill — ${agent.name} : ${slug}`);
      const ok = await runInline(remove, '…', skillsStatus, () => ctx.api.agentSkill(agent.name, slug, 'remove'));
      if (ok) callbacks.refresh(agent.name);
    });
    chip.append(remove);
    skillsWrap.append(chip);
  }
  if (!agent.skills.length) skillsWrap.append(text('span', 'lbl', 'aucun skill attaché'));

  const assignRow = document.createElement('div');
  assignRow.className = 'row';
  assignRow.style.marginTop = '6px';
  const select = document.createElement('select');
  const available = (agentsPayload.skills || []).filter((s) => !agent.skills.includes(s.slug));
  for (const skill of available) {
    const option = document.createElement('option');
    option.value = skill.slug;
    option.textContent = skill.name || skill.slug;
    select.append(option);
  }
  select.disabled = readOnly || !available.length;
  const assignBtn = document.createElement('button');
  assignBtn.type = 'button';
  assignBtn.className = 'btn';
  assignBtn.textContent = saveLabel('Assigner');
  assignBtn.disabled = readOnly || !available.length;
  assignBtn.addEventListener('click', async () => {
    const slug = select.value;
    if (!slug) return;
    ctx.dock.echo(`# assigner skill — ${agent.name} : ${slug}`);
    const ok = await runInline(assignBtn, 'Assignation…', skillsStatus, () => ctx.api.agentSkill(agent.name, slug, 'assign'), [select]);
    if (ok) callbacks.refresh(agent.name);
  });
  assignRow.append(select, assignBtn);

  block.append(fieldRow('skills', skillsWrap), assignRow, skillsStatus);
  return block;
}

// ── Wizard de setup — le wizard exécute (#171) ──────────────────────────────
//
// Remplace le repli « copier-coller la commande » sur le projet d'accueil
// (écritures actives, `!ctx.host.readOnly`) : exécution réelle par la même
// mécanique que `grimoire up` (`project_setup.execute_setup_plan`, jamais un
// sous-processus), needs pré-cochés depuis `needs_suggest.py` (B3 rebranché
// sur B2), état final vérifiable (doctor). Le mode copier-coller reste un
// repli explicite, toujours accessible en dessous. Naviguer vers un AUTRE
// projet du registre (cockpit, lecture seule) garde l'ancien message : la
// Console ne lance jamais `grimoire init` à distance sur ce cas-là.

function stepStatusWord(status) {
  return { done: 'fait', skipped: 'sans effet', failed: 'échec', planned: 'prévu' }[status] || status;
}

function renderRunReport(run) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-preview';
  wrap.append(row(
    dot(run.ok ? 'ok' : 'bad'),
    text('span', null, run.ok ? 'Projet initialisé.' : 'Initialisation en échec.'),
    text('span', 'lbl', run.doctorOk ? 'doctor conforme ✓' : 'doctor à revoir'),
  ));
  const list = document.createElement('div');
  list.className = 'pl-watch';
  list.style.marginTop = '8px';
  for (const step of run.steps || []) {
    const line = document.createElement('div');
    line.className = 'pl-watch-row';
    line.append(
      dot(step.status === 'failed' ? 'bad' : (step.status === 'done' ? 'ok' : '')),
      row(
        text('span', 'pl-watch-name', step.step),
        text('span', 'pl-watch-reason lbl', `${stepStatusWord(step.status)} — ${step.detail}`),
      ),
    );
    list.append(line);
  }
  wrap.append(list);
  if ((run.extensionErrors || []).length) {
    wrap.append(text('div', 'lbl', 'Extensions en échec : ' + run.extensionErrors.join(' · ')));
  }
  return wrap;
}

// Champs partagés par les deux formulaires de setup — celui qui initialise le
// projet déjà servi (`renderSetupWizard`) et celui qui en crée un nouveau
// depuis le portefeuille (`renderCreateProjectForm`, #172). Un seul endroit
// qui sait construire les questions, le sélecteur de backend et les needs, et
// lire leur valeur : les deux formulaires ne diffèrent que par leur cible (le
// projet servi, ou un chemin choisi) et leur appel API final.
//
// #888 : l'archétype n'est plus demandé — trois questions (pile, qui
// travaille, domaine) le déduisent, par le même module que `grimoire init`
// (`GET /api/setup/questions`). `options.prefill === false` (nouveau projet,
// autre dossier) ne lit pas la détection du projet servi. Sans réponse du
// serveur (ancien hôte), le sélecteur d'archétype historique reste le repli.
async function buildSetupFields(ctx, wrap, options = {}) {
  const prefill = options.prefill !== false;
  const [archetypes, backendsPayload, needsPayload, questionsPayload] = await Promise.all([
    ctx.api.archetypesCatalogue().catch(() => []),
    ctx.api.backendsCatalogue().catch(() => ({ backends: [] })),
    ctx.api.needsCatalogue().catch(() => ({ needs: [], suggested: [] })),
    ctx.api.setupQuestions(null, prefill).catch(() => null),
  ]);

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.placeholder = ctx.host.project || 'nom du projet';
  const userInput = document.createElement('input');
  userInput.type = 'text';
  userInput.placeholder = 'votre nom (artefacts)';

  // Repli historique (hôte sans `/api/setup/questions`) : un sélecteur d'archétype.
  const archSelect = document.createElement('select');
  for (const a of (archetypes || [])) {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = a.name || a.id;
    archSelect.append(opt);
  }
  if (!archSelect.options.length) {
    const opt = document.createElement('option');
    opt.value = 'minimal';
    opt.textContent = 'minimal';
    archSelect.append(opt);
  }

  // Les trois questions (#888). `deduced` suit la dernière réponse du serveur.
  const questions = questionsPayload && Array.isArray(questionsPayload.questions) ? questionsPayload : null;
  let deduced = questions ? questions.deduction : null;
  const stackChecks = {};
  const whoSelect = document.createElement('select');
  const domainSelect = document.createElement('select');
  const deducedLine = text('p', 'lbl', '');
  const questionRows = [];
  if (questions) {
    const byId = Object.fromEntries(questions.questions.map((q) => [q.id, q]));
    const answers = questions.answers || {};
    const stackWrap = document.createElement('div');
    stackWrap.className = 'pl-tool-opts';
    for (const o of (byId.stack ? byId.stack.options : [])) {
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = (answers.stack || []).includes(o.id);
      if (o.hint) label.title = o.hint;
      stackChecks[o.id] = cb;
      label.append(cb, document.createTextNode(o.label));
      stackWrap.append(label);
    }
    const fillSelect = (select, q, current) => {
      for (const o of (q ? q.options : [])) {
        const opt = document.createElement('option');
        opt.value = o.id;
        opt.textContent = o.hint ? `${o.label} — ${o.hint}` : o.label;
        opt.selected = o.id === current;
        select.append(opt);
      }
    };
    fillSelect(whoSelect, byId.who, answers.who);
    fillSelect(domainSelect, byId.domain, answers.domain);
    questionRows.push(
      fieldRow(byId.stack ? byId.stack.prompt : 'stack', stackWrap),
      fieldRow(byId.who ? byId.who.prompt : 'who', whoSelect),
      fieldRow(byId.domain ? byId.domain.prompt : 'domain', domainSelect),
      deducedLine,
    );
  }
  const currentAnswers = () => ({
    stack: Object.keys(stackChecks).filter((id) => stackChecks[id].checked).join(','),
    who: whoSelect.value,
    domain: domainSelect.value,
  });
  const renderDeduced = () => {
    if (!deduced) return;
    const parts = [`archétype déduit : ${deduced.archetypes.join(' + ')}`, `profil du standard : ${deduced.standard_profile}`];
    if ((deduced.stack_skills || []).length) parts.push(`skills : ${deduced.stack_skills.join(', ')}`);
    if ((deduced.expertises || []).length) parts.push(`expertises à attacher : ${deduced.expertises.join(', ')}`);
    deducedLine.textContent = parts.join(' · ');
  };
  const refreshDeduction = async () => {
    try {
      const payload = await ctx.api.setupQuestions(currentAnswers(), prefill);
      if (payload && payload.deduction) {
        deduced = payload.deduction;
        renderDeduced();
      }
    } catch (_error) {
      // la dernière déduction connue reste affichée
    }
  };
  if (questions) {
    renderDeduced();
    for (const cb of Object.values(stackChecks)) cb.addEventListener('change', refreshDeduction);
    whoSelect.addEventListener('change', refreshDeduction);
    domainSelect.addEventListener('change', refreshDeduction);
  }
  const archetypeValue = () => (deduced ? deduced.archetype : archSelect.value);

  const backendSelect = document.createElement('select');
  const autoOpt = document.createElement('option');
  autoOpt.value = 'auto';
  autoOpt.textContent = 'auto (recommandé)';
  backendSelect.append(autoOpt);
  for (const b of (backendsPayload.backends || [])) {
    if (b.id === 'auto') continue;
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = b.label || b.id;
    backendSelect.append(opt);
  }

  const suggestedIds = new Set((needsPayload.suggested || []).map((s) => s.id));
  const needsWrap = document.createElement('div');
  needsWrap.className = 'pl-tool-opts';
  const needChecks = {};
  // Précochés : suggestions du scan (B3) et besoins déduits de « qui travaille » (#888).
  const deducedNeeds = new Set(deduced ? (deduced.needs || []) : []);
  for (const n of (needsPayload.needs || [])) {
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = suggestedIds.has(n.id) || deducedNeeds.has(n.id);
    if (n.rationale) label.title = n.rationale;
    needChecks[n.id] = cb;
    label.append(cb, document.createTextNode(n.label || n.id));
    needsWrap.append(label);
  }
  if ((needsPayload.suggested || []).length) {
    const hint = needsPayload.suggested.map((s) => s.reason).join(' · ');
    wrap.append(text('p', 'lbl', 'Suggéré pour ce projet : ' + hint));
  }

  wrap.append(
    fieldRow('nom', nameInput),
    fieldRow('votre nom', userInput),
    ...(questions ? questionRows : [fieldRow('archétype', archSelect)]),
    fieldRow('mémoire / BDD', backendSelect),
    fieldRow('needs (standard agentique)', needsWrap),
  );

  return {
    summary: () => `--archetype ${archetypeValue()} --backend ${backendSelect.value}`,
    payload: (extra) => ({
      name: nameInput.value.trim(),
      user: userInput.value.trim(),
      archetype: archetypeValue(),
      backend: backendSelect.value,
      needs: Object.keys(needChecks).filter((id) => needChecks[id].checked),
      ...extra,
    }),
  };
}

async function renderSetupWizard(ctx, options) {
  const wrap = document.createElement('div');
  wrap.append(text('p', 'lbl', "Ce projet n'est pas initialisé — l'exécuter écrit réellement le projet."));

  const fields = await buildSetupFields(ctx, wrap);

  const runBtn = document.createElement('button');
  runBtn.type = 'button';
  runBtn.className = 'btn pri';
  runBtn.textContent = 'Initialiser le projet';

  const preview = document.createElement('div');
  preview.hidden = true;

  const fallbackBtn = document.createElement('button');
  fallbackBtn.type = 'button';
  fallbackBtn.className = 'btn';
  fallbackBtn.textContent = 'ou : obtenir la commande à copier-coller';

  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    runBtn.textContent = 'Initialisation…';
    preview.hidden = false;
    preview.replaceChildren(text('p', 'lbl', 'grimoire up — en cours…'));
    ctx.dock.echo(`grimoire up . ${fields.summary()}`);
    try {
      await ctx.api.setupPlan(fields.payload({ planOnly: false }));
      // Le rapport (doctor compris) est désormais un bloc persistant de la
      // fiche — lu depuis `_grimoire/setup-run.json`, pas ce `preview` que le
      // refresh qui suit va de toute façon effacer avec le reste de la fiche.
      options.refresh();
      return;
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = 'Initialiser le projet';
    }
  });

  fallbackBtn.addEventListener('click', async () => {
    fallbackBtn.disabled = true;
    try {
      const plan = await ctx.api.setupPlan(fields.payload({ planOnly: true }));
      preview.hidden = false;
      preview.replaceChildren(text('p', 'lbl', 'Plan écrit dans _grimoire/setup-plan.json — terminez :'));
      const code = document.createElement('code');
      code.className = 'mono';
      code.textContent = plan.initCommand;
      preview.append(code);
    } catch (error) {
      preview.hidden = false;
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      fallbackBtn.disabled = false;
    }
  });

  wrap.append(runBtn, fallbackBtn, preview);
  return wrap;
}

// ── Nouveau projet depuis le portefeuille (#172, volet création) ───────────
//
// Même formulaire que `renderSetupWizard`, plus un chemin explicite : la
// cible n'est pas le projet servi mais un dossier choisi, qui ne doit pas
// déjà être un projet (`POST /api/projects/create`, refusé sinon par le
// serveur — voir cmd_cockpit.py). À la réussite, le registre a déjà le
// nouveau projet (grimoire up l'enrôle comme tout `up`/`init` normal) :
// `onCreated(slug)` referme la modale et rafraîchit la Flotte sans recharger
// la page.
async function renderCreateProjectForm(ctx, onCreated) {
  const wrap = document.createElement('div');
  wrap.append(text('p', 'lbl', 'Un chemin qui n’est pas déjà un projet Grimoire. Le dossier est créé si besoin.'));

  const pathInput = document.createElement('input');
  pathInput.type = 'text';
  pathInput.placeholder = '/chemin/absolu/du/nouveau-projet';
  wrap.append(fieldRow('chemin', pathInput));

  // Autre dossier : la détection du projet servi ne vaut rien ici (#888).
  const fields = await buildSetupFields(ctx, wrap, { prefill: false });

  const createBtn = document.createElement('button');
  createBtn.type = 'button';
  createBtn.className = 'btn pri';
  createBtn.textContent = 'Créer le projet';

  const preview = document.createElement('div');
  preview.hidden = true;

  createBtn.addEventListener('click', async () => {
    const path = pathInput.value.trim();
    if (!path) { pathInput.focus(); return; }
    createBtn.disabled = true;
    createBtn.textContent = 'Création…';
    preview.hidden = false;
    preview.replaceChildren(text('p', 'lbl', 'grimoire up ' + path + ' ' + fields.summary() + ' — en cours…'));
    try {
      const result = await ctx.api.createProject(fields.payload({ path }));
      // Le cache 60 s de la Flotte (#541) a été rempli — sans navigation
      // encore — par l'ouverture initiale de cet espace : sans invalidation
      // ici, revenir sur Flotte juste après création montre un tableau
      // périmé, sans la ligne du projet qu'on vient de créer.
      invalidateFleetCache();
      onCreated(result.slug);
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
      createBtn.disabled = false;
      createBtn.textContent = 'Créer le projet';
    }
  });

  wrap.append(createBtn, preview);
  return wrap;
}

// ── Déroulé du flow de mise à jour, nœud par nœud (issue #506) ─────────────
//
// `report.nodes`/`result.nodes` (project_update.py::_node_statuses) porte un
// statut par nœud du blueprint `project-upgrade`, dans l'ordre fixe — jamais
// déduit du texte libre `output`. Les libellés ici sont d'affichage
// seulement ; l'identité (`node.id`) reste celle du blueprint.

const NODE_LABELS = {
  backup: 'Sauvegarde', preview: 'Aperçu', orphans: 'Orphelins', apply: 'Application',
  overrides: 'Overrides', memory: 'Mémoire', 'needs-hosts': 'Besoins / hôtes',
  verify: 'Vérification', destructive: 'Destructif (checkpoint)',
};

const NODE_STATUS_WORD = {
  fait: 'fait',
  proposition: 'proposition écrite — à décider dans Propositions',
  sauté: 'jamais atteint',
  erreur: 'en erreur',
  'checkpoint en attente': 'arrêté ici, jamais décidé à votre place',
};

const NODE_STATUS_DOT = {
  fait: 'ok', proposition: 'acc', sauté: '', erreur: 'bad', 'checkpoint en attente': 'warn',
};

// Un aperçu ne joue que `backup` et `preview` : les autres étapes n'ont pas
// « manqué », elles n'ont pas été exécutées en aperçu (P44). « Jamais atteint »
// reste réservé à un vrai run arrêté avant elles.
const NODE_SKIPPED_IN_PREVIEW = 'non exécutée en aperçu';

function renderNodeList(nodes, { dryRun = false } = {}) {
  // Une barre compacte (P44, maquette Mise à jour) : neuf traits de 4 px sur
  // une seule rangée, le nom de l'étape dessous. L'état est dit en toutes
  // lettres à qui ne voit pas la couleur (`sr-only`) et à l'infobulle.
  const list = document.createElement('ol');
  list.className = 'pl-steps pl-nodes';
  list.setAttribute('aria-label', 'Étapes');
  for (const node of nodes) {
    const word = dryRun && node.status === 'sauté' ? NODE_SKIPPED_IN_PREVIEW : (NODE_STATUS_WORD[node.status] || node.status);
    const item = document.createElement('li');
    item.title = `${NODE_LABELS[node.id] || node.id} — ${word}`;
    const bar = document.createElement('span');
    bar.className = 'pl-step-bar';
    bar.dataset.tone = NODE_STATUS_DOT[node.status] || '';
    const name = text('span', 'pl-step-name', NODE_LABELS[node.id] || node.id);
    name.append(text('span', 'sr-only', ` — ${word}`));
    item.append(bar, name);
    list.append(item);
  }
  return list;
}

//: Un rapport CLI/Rich complet peut dépasser largement cette taille ; le
//: cockpit n'a pas besoin du journal entier pour montrer ce qui a tourné, et
//: une UI qui charge tout gèlerait sur un gros projet. `truncateMarkdown`
//: coupe sur une frontière de ligne et referme un bloc de code resté ouvert.
const PREVIEW_MARKDOWN_LIMIT = 4000;

// Rend `preview.md`/`report.md` (ou, à défaut, la sortie brute) en Markdown
// structuré — titres et blocs de code, jamais un `<pre>` unique où les
// caractères de mise en forme de la commande sous-jacente s'affichent tels
// quels. `preview-wrap` cassait l'alignement d'un bloc de code multi-lignes
// à chaque redimensionnement ; un bloc de code garde son défilement
// horizontal propre (voir `.pl-markdown pre` dans les styles du module).
function appendPreviewMarkdown(container, previewText) {
  if (!previewText) return;
  const { text: shown, truncated } = truncateMarkdown(previewText, PREVIEW_MARKDOWN_LIMIT);
  const rendered = document.createElement('div');
  rendered.className = 'pl-markdown';
  rendered.innerHTML = renderMarkdown(shown);
  container.append(rendered);
  if (truncated) {
    container.append(text('div', 'lbl', 'Aperçu tronqué — le rapport complet reste sur disque sous `_grimoire-output/upgrade/<date>/`.'));
  }
}

// ── Écran « Mise à jour » plein canevas (P44) : lecture seule ──────────────
//
// Tout ce qui suit lit : le tableau de `grimoire up --dry-run` (déjà dans la
// réponse de l'aperçu), puis `GET upgrade/changes` et `GET upgrade/diff` au
// clic. Aucune écriture ici — la seule écriture reste le bouton de
// confirmation existant.

function htmlTable(headers, rows) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tbl-wrap';
  const table = document.createElement('table');
  table.className = 'tbl';
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of headers) headRow.append(text('th', null, label));
  head.append(headRow);
  const body = document.createElement('tbody');
  for (const cells of rows) {
    const tr = document.createElement('tr');
    for (const cell of cells) {
      const td = document.createElement('td');
      td.append(cell);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function upCard(title, note) {
  const card = document.createElement('section');
  card.className = 'pl-upcard';
  const head = document.createElement('div');
  head.className = 'pl-upcard-h';
  head.append(text('h3', null, title));
  if (note) head.append(text('span', 'lbl', note));
  card.append(head);
  return card;
}

// Colonne de droite (P44, sans décision de checkpoint) : la lecture de
// `up --dry-run` et des surfaces d'hôtes, en vrais tableaux.
function renderPreviewTables(table) {
  const card = upCard('Étapes de la mise à jour', 'grimoire up --dry-run');
  card.classList.add('pl-preview-tables');
  card.append(htmlTable(
    ['Étape', 'Statut', 'Détail'],
    table.steps.map((step) => [
      document.createTextNode(step.label),
      badge(step.statusLabel, step.tone),
      document.createTextNode(step.detail),
    ]),
  ));
  if (table.hosts && table.hosts.length) {
    card.append(text('h4', null, "Surfaces d'hôtes"));
    card.append(htmlTable(
      ['Hôte', 'Écrirait', 'Inchangé'],
      table.hosts.map((h) => [
        document.createTextNode(h.host),
        document.createTextNode(String(h.wouldWrite)),
        document.createTextNode(String(h.unchanged)),
      ]),
    ));
  }
  return card;
}

function renderDiff(unified) {
  const pre = document.createElement('div');
  pre.className = 'pl-diff';
  for (const line of unified.split('\n')) {
    if (!line) continue;
    let kind = '';
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) kind = 'hunk';
    else if (line.startsWith('+')) kind = 'add';
    else if (line.startsWith('-')) kind = 'del';
    pre.append(text('span', kind, line));
  }
  return pre;
}

const CATEGORY_NOTE = {
  agents: 'agents fournis par le kit',
  kit: 'fichiers du kit sous _grimoire/',
  copilot: 'instructions et prompts Copilot',
  hotes: "surfaces des hôtes et configuration MCP",
  autres: 'autres fichiers du projet',
};

// Colonne de gauche : une ligne par catégorie (titre, sous-titre, compte,
// « Diff » qui déplie les fichiers ; un clic sur un fichier charge son diff).
function renderChangesSection(ctx) {
  const card = upCard('Ce que la mise à jour écrirait', "rien n'est écrit en aperçu");
  card.classList.add('pl-changes');
  const body = document.createElement('div');
  body.className = 'pl-cats';
  body.append(text('div', 'lbl', 'Chargement des changements…'));
  body.firstChild.style.padding = '12px 16px';
  card.append(body);

  ctx.api.upgradeChanges().then((view) => {
    body.replaceChildren();
    if (!view.categories.length) {
      const empty = document.createElement('div');
      empty.className = 'pl-cat-row';
      empty.append(
        text('span', 'pl-cat-title', `Rien à écrire (${view.unchanged} fichiers déjà à jour).`),
        badge('inchangé', ''),
      );
      body.append(empty);
      return;
    }
    for (const category of view.categories) {
      const block = document.createElement('div');
      block.className = 'pl-cat';
      const rowEl = document.createElement('div');
      rowEl.className = 'pl-cat-row';
      const title = document.createElement('span');
      title.className = 'pl-cat-title';
      title.append(text('b', null, category.label), text('span', null, CATEGORY_NOTE[category.id] || ''));
      const count = category.files.length;
      const diffBtn = document.createElement('button');
      diffBtn.type = 'button';
      diffBtn.className = 'btn pl-cat-diff';
      diffBtn.dataset.category = category.id;
      diffBtn.setAttribute('aria-expanded', 'false');
      diffBtn.textContent = 'Diff';
      const files = document.createElement('div');
      files.className = 'pl-cat-files';
      files.hidden = true;
      diffBtn.addEventListener('click', () => {
        const open = files.hidden;
        files.hidden = !open;
        diffBtn.setAttribute('aria-expanded', String(open));
      });
      for (const file of category.files) files.append(renderFileEntry(ctx, file));
      rowEl.append(title, badge(`${count} fichier${count > 1 ? 's' : ''}`, 'ok'), diffBtn);
      block.append(rowEl, files);
      body.append(block);
    }
  }).catch((error) => {
    body.replaceChildren(text('div', 'lbl', `Changements indisponibles : ${error.message}`));
  });
  return card;
}

function renderFileEntry(ctx, file) {
  const entry = document.createElement('div');
  entry.className = 'pl-file';
  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'pl-file-head';
  head.dataset.path = file.path;
  head.setAttribute('aria-expanded', 'false');
  const path = text('span', 'mono', file.path);
  path.title = file.path;
  head.append(
    path,
    badge(file.label, file.status === 'nouveau' ? 'ok' : 'warn'),
    text('span', 'lbl', `+${file.added} −${file.removed}`),
  );
  const detail = document.createElement('div');
  detail.hidden = true;
  let loaded = false;
  head.addEventListener('click', async () => {
    const open = detail.hidden;
    detail.hidden = !open;
    head.setAttribute('aria-expanded', String(open));
    if (!open || loaded) return;
    loaded = true;
    detail.replaceChildren(text('div', 'lbl', 'Chargement du diff…'));
    try {
      const diff = await ctx.api.upgradeDiff(file.path);
      detail.replaceChildren(renderDiff(diff.unified));
      if (diff.truncated) detail.append(text('div', 'lbl', 'Diff tronqué — le fichier complet reste sur disque.'));
    } catch (error) {
      loaded = false;
      detail.replaceChildren(text('div', 'lbl', `Diff indisponible : ${error.message}`));
    }
  });
  entry.append(head, detail);
  return entry;
}

function renderSheet(root, ctx, slug, name, sheet, options) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-sheet';
  const { health, memory } = sheet;

  wrap.append(spaceHead(name || (ctx.scope && ctx.scope.name) || 'Projet servi'));

  const kpi = kpiRow([
    { value: health?.kit?.scaffolded ? kitStatus(health.kit).word : 'absent', label: 'kit', icon: 'seal', tone: health?.kit?.scaffolded ? kitStatus(health.kit).dot : 'warn' },
    { value: ciWord(health?.ci_status), label: 'CI', icon: 'check', tone: ciDotClass(health?.ci_status) },
    { value: fmtInt(health?.commits_total), label: 'commits', icon: 'branch' },
    // Les runs de flow — même lecture que la liste d'Observer (`sheet.flowRuns`
    // et `GET /api/workspace/flows/runs` passent par `flow_runs_view`), jamais
    // le nombre de blueprints présents (`health.flows`), qui disait « 0 flows »
    // à côté des runs qu'Observer listait.
    { value: Number.isFinite(sheet.flowRuns?.total) ? fmtInt(sheet.flowRuns.total) : 'inconnu', label: 'runs de flow', icon: 'workflow' },
  ]);
  wrap.append(kpi);

  root.append(wrap);

  // ── Inspecteur : kit, hôtes, standard, actions ─────────────────────────
  ctx.inspector.replaceChildren();

  const kitBlock = document.createElement('div');
  kitBlock.className = 'pl-insp-block';
  kitBlock.dataset.focus = 'kit';
  kitBlock.append(text('h4', null, 'Kit'));
  const kit = kitStatus(health?.kit);
  const kitRow = pill(kit.dot, kit.word);
  kitBlock.append(kitRow);
  // Issue #519 : `health.kit.aligned` nomme la version où un contenu encore
  // présent a été introduit pour la PREMIÈRE fois au catalogue, pas le
  // dernier passage réel de l'outil — un projet à jour en 3.51.1 dont
  // l'essentiel n'avait pas changé depuis 3.46.0 affichait ici « aligné sur
  // 3.46.0, installé 3.51.1 », lu comme un retard. Quand `upToDate` est vrai
  // ET que le serveur connaît la version qui a fait le dernier `up`
  // (`upVersion`, marqueur écrit par `grimoire up`/`init`), c'est ELLE la
  // référence à afficher ; `aligned` redevient un détail secondaire, réservé
  // au cas où il diffère (contenu inchangé depuis plus longtemps).
  if (health?.kit?.upToDate && health?.kit?.upVersion) {
    kitBlock.append(text('div', 'lbl', `à jour (kit ${health.kit.upVersion})`));
    if (health.kit.aligned && health.kit.aligned !== health.kit.upVersion) {
      kitBlock.append(text('div', 'lbl', `contenu inchangé depuis ${health.kit.aligned}`));
    }
  } else if (health?.kit?.aligned) {
    kitBlock.append(text('div', 'lbl', `aligné sur ${health.kit.aligned}, installé ${health.kit.installed}`));
  }
  const toolLine = projectToolLine(health?.kit);
  if (toolLine) kitBlock.append(text('div', 'lbl', toolLine));
  // Le badge « en retard (N) » nommait N sans jamais dire lesquels
  // (`kit.behindFiles`, déjà rendu par le serveur, jusqu'ici jamais lu ici).
  let behindBlock = null;
  const behindFiles = health?.kit?.behindFiles || [];
  if (behindFiles.length) {
    behindBlock = text('div', 'lbl', `En retard : ${behindFiles.join(', ')}`);
    kitBlock.append(behindBlock);
  }
  ctx.inspector.append(kitBlock);

  // Après un run confirmé arrêté au checkpoint `destructive` (le seul point
  // d'arrêt d'un flow complet, #490), le badge doit dire ce qui vient de se
  // passer plutôt que garder le même libellé qu'avant toute action.
  //
  // Restes #510/#513 (validation finale de la boucle de mise à jour) :
  // jusqu'ici purement client, ce badge ne survivait pas à une navigation
  // Flotte → Projet — un rechargement de la fiche perdait l'information
  // que le run précédent avait tourné jusqu'au bout sans qu'on décide du
  // checkpoint. Il dérive donc maintenant aussi du backend, à CHAQUE rendu
  // (voir `checkpointPendingRunId` plus bas, depuis `GET /api/workspace/
  // flows/runs`, #513), pas seulement juste après un clic « Confirmer ».
  function markKitCheckpointPending(runId) {
    kitRow.className = 'chip pill warn';
    kitRow.textContent = 'mis à niveau, checkpoint destructif en attente';
    if (runId) kitRow.title = `grimoire flow status ${runId}`;
    const kitCell = kpi.querySelector('.kpi-item .kpi-val');
    if (kitCell) kitCell.textContent = 'checkpoint en attente';
    if (behindBlock) { behindBlock.remove(); behindBlock = null; }
  }

  // Les runs `project-upgrade` de CE projet (route `flows/runs`, #513) :
  // `status`/`currentNode` viennent du kernel réel (issue #510/#513,
  // `flow_runs.list_flow_runs`), jamais d'un état client posé après un clic
  // — c'est ce qui permet au badge de survivre à un rechargement de la fiche.
  // Le checkpoint se cherche sur TOUS les runs rendus, pas seulement le
  // dernier : un run plus récent (une mise à jour en échec, par exemple) ne
  // décide pas à votre place celui qui attend toujours au checkpoint.
  const upgradeRuns = Array.isArray(sheet.upgradeRuns?.runs) ? sheet.upgradeRuns.runs : [];
  const latestUpgradeRun = upgradeRuns[0] || null;
  const pendingRun = upgradeRuns.find((r) => r.status === 'checkpointed' && r.currentNode === 'destructive');
  let checkpointPendingRunId = pendingRun ? pendingRun.runId : null;
  if (checkpointPendingRunId) markKitCheckpointPending(checkpointPendingRunId);

  const standardBlock = document.createElement('div');
  standardBlock.className = 'pl-insp-block';
  standardBlock.append(text('h4', null, 'Standard'));
  if (sheet.doctor) {
    standardBlock.append(pill(sheet.doctor.ok ? 'ok' : 'bad', sheet.doctor.ok ? 'doctor conforme' : 'doctor en écart'));
  } else {
    standardBlock.append(doctorOnDemand(ctx, slug));
  }
  ctx.inspector.append(standardBlock);

  const memBlock = document.createElement('div');
  memBlock.className = 'pl-insp-block';
  memBlock.dataset.focus = 'memory';
  memBlock.append(text('h4', null, 'Mémoire'));
  let memoryPayload = memory;
  let memPill = pill(memoryPayload?.state === 'ok' ? 'ok' : (memoryPayload?.state === 'unavailable' ? 'warn' : ''), memoryPayload?.configuredBackend ? `${memoryPayload.configuredBackend} · ${fmtInt(memoryPayload.entries)} entrée(s)` : 'non initialisée');
  memBlock.append(memPill);
  // Premier rendu toujours en mode rapide (jamais de sonde réseau — c'est le
  // même appel que la Flotte, `ctx.api.memoryStatus` sans `probe`). Une
  // sonde fraîche reste un geste explicite (#541 point 5), jamais un
  // `setInterval` : ce bouton est la seule façon de forcer `probe=1`, et il
  // ne redessine que ce bloc, jamais toute la fiche.
  const probeBtn = document.createElement('button');
  probeBtn.type = 'button';
  probeBtn.className = 'btn';
  probeBtn.textContent = 'Sonder la mémoire';
  probeBtn.title = 'Force une sonde réseau fraîche (probe=1) — coûteuse, jamais automatique.';
  probeBtn.addEventListener('click', async () => {
    probeBtn.disabled = true;
    probeBtn.textContent = 'Sonde en cours…';
    try {
      memoryPayload = await ctx.api.memoryStatus(slug, { probe: true });
      const fresh = pill(memoryPayload?.state === 'ok' ? 'ok' : (memoryPayload?.state === 'unavailable' ? 'warn' : ''), memoryPayload?.configuredBackend ? `${memoryPayload.configuredBackend} · ${fmtInt(memoryPayload.entries)} entrée(s)` : 'non initialisée');
      memPill.replaceWith(fresh);
      memPill = fresh;
    } finally {
      probeBtn.disabled = false;
      probeBtn.textContent = 'Sonder la mémoire';
    }
  });
  memBlock.append(probeBtn);
  ctx.inspector.append(memBlock);

  // ── Dernière exécution du wizard (#171) ─────────────────────────────────
  //
  // Lue depuis le journal persistant (`_grimoire/setup-run.json`), pas
  // seulement le temps d'un toast : `options.refresh()` recharge la fiche
  // après une exécution, et ce bloc doit encore montrer le rapport (doctor
  // compris) une fois la fiche redessinée.
  if (sheet.setupRun && sheet.setupRun.available) {
    const runBlock = document.createElement('div');
    runBlock.className = 'pl-insp-block';
    runBlock.append(text('h4', null, 'Dernière initialisation (wizard)'));
    runBlock.append(renderRunReport(sheet.setupRun));
    ctx.inspector.append(runBlock);
  }

  const actionsBlock = document.createElement('div');
  actionsBlock.className = 'pl-insp-block pl-actions';
  actionsBlock.dataset.focus = 'checkpoint';
  actionsBlock.append(text('h4', null, 'Actions'));

  if (!health?.kit?.scaffolded) {
    if (ctx.host.readOnly) {
      // Naviguer vers un AUTRE projet du registre depuis le cockpit reste en
      // lecture seule : la Console ne lance jamais `grimoire init` à distance
      // sur un projet qu'on ne fait que regarder (#356).
      const initRow = document.createElement('div');
      initRow.append(text('p', 'lbl', "Ce projet n'est pas initialisé. La Console ne lance jamais `grimoire init` à distance :"));
      const code = document.createElement('code');
      code.className = 'mono';
      code.textContent = `grimoire init ${slug ? '(dans le dossier du projet)' : '.'}`;
      initRow.append(code);
      actionsBlock.append(initRow);
    } else {
      const wizardHost = document.createElement('div');
      wizardHost.append(text('p', 'lbl', 'Chargement du wizard…'));
      actionsBlock.append(wizardHost);
      renderSetupWizard(ctx, options).then((node) => wizardHost.replaceWith(node));
    }
  }

  const updateBtn = document.createElement('button');
  updateBtn.type = 'button';
  updateBtn.className = 'btn';
  updateBtn.textContent = 'Mettre à jour — aperçu';
  // P44 : l'aperçu s'ouvre en plein canevas (écran `.pl-update`), pas dans
  // l'inspecteur. `.pl-preview` reste le conteneur unique du déroulé : les
  // parcours existants (aperçu, confirmation) le retrouvent sous ce nom.
  const displayName = name || (ctx.scope && ctx.scope.name) || 'Projet servi';
  const screen = document.createElement('div');
  screen.className = 'pl-wrap pl-update';
  screen.hidden = true;
  const preview = document.createElement('div');
  preview.className = 'pl-preview';
  preview.hidden = true;
  const backBtn = document.createElement('button');
  backBtn.type = 'button';
  backBtn.className = 'btn';
  backBtn.textContent = 'Retour à la fiche';
  // Le titre n'existe qu'à l'ouverture : une fiche qui n'a pas lancé d'aperçu
  // ne porte aucun second `h2` au nom du projet (les parcours qui attendent
  // « le titre du projet » n'en trouvent qu'un).
  let screenBuilt = false;
  // Avant la fiche : après une confirmation, la fiche (et ses Propositions) revient
  // SOUS le déroulé, qui peut donc dire « ci-dessous » sans mentir.
  wrap.before(screen);
  backBtn.addEventListener('click', () => {
    screen.hidden = true;
    wrap.hidden = false;
  });
  // En-tête de la maquette : fil, titre + delta de version, sous-ligne du run,
  // badge d'attente à droite.
  const kitInfo = health?.kit || {};
  const versionDelta = kitInfo.upVersion && kitInfo.installed && kitInfo.upVersion !== kitInfo.installed
    ? `${kitInfo.upVersion} → ${kitInfo.installed}`
    : (kitInfo.installed || '');
  const headSub = text('div', 'pl-up-sub', 'Aperçu en cours…');
  const foot = document.createElement('footer');
  foot.className = 'pl-up-foot';
  foot.hidden = true;
  const buildScreenHead = () => {
    const title = document.createElement('h2');
    title.className = 'space-title';
    title.append('Mise à jour du kit');
    if (versionDelta) title.append(' ', text('span', 'pl-up-delta', versionDelta));
    const left = document.createElement('div');
    left.append(text('span', 'pl-up-crumb', `Flotte › ${displayName}`), title, headSub);
    const aside = document.createElement('div');
    aside.className = 'pl-up-aside';
    if (checkpointPendingRunId || pendingRun) aside.append(badge('Décision en attente', 'warn'));
    aside.append(backBtn);
    const head = document.createElement('div');
    head.className = 'pl-up-head';
    head.append(left, aside);
    return head;
  };
  const openScreen = () => {
    if (!screenBuilt) {
      screenBuilt = true;
      screen.append(buildScreenHead(), preview, foot);
    }
    wrap.hidden = true;
    screen.hidden = false;
    const title = screen.querySelector('.space-title');
    if (title) { title.tabIndex = -1; title.focus({ preventScroll: true }); }
  };
  updateBtn.addEventListener('click', async () => {
    ctx.dock.echo(`grimoire upgrade-flow run --dry-run${slug ? ' # ' + slug : ''}`);
    // État « en cours » (issue #506) : le flow peut prendre jusqu'à une
    // minute (backup + `up --dry-run` + `host sync --dry-run`) — sans ceci,
    // l'écran passait de « cliqué » à « terminé » sans rien entre.
    updateBtn.disabled = true;
    updateBtn.textContent = 'Aperçu en cours…';
    openScreen();
    preview.hidden = false;
    preview.replaceChildren(row(dot('acc'), text('span', 'lbl', "Aperçu en cours — jusqu'à une minute…")));
    try {
      const report = await ctx.api.updateProject(slug, false);
      preview.replaceChildren();
      // L'aperçu écrit bien une sauvegarde (et son rapport `preview.md`) : le
      // message le dit, plutôt qu'un « rien d'autre écrit » démenti par le
      // disque. Son run est marqué aperçu et n'est compté nulle part.
      preview.append(text('div', null, report.ok ? 'Aperçu : sauvegarde créée, aucun fichier du projet modifié.' : (report.error || 'Aperçu en échec.')));
      const played = (report.nodes || []).filter((n) => n.status === 'fait' || n.status === 'proposition').length;
      const when = new Date().toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' });
      headSub.replaceChildren(
        'Run ', text('span', 'mono', report.runId || 'aperçu'),
        ` · lancé le ${when} · ${played} étape${played > 1 ? 's' : ''} jouée${played > 1 ? 's' : ''} sur ${(report.nodes || []).length || 9} (aperçu).`,
      );
      if (report.nodes) preview.append(renderNodeList(report.nodes, { dryRun: true }));
      const cols = document.createElement('div');
      cols.className = 'pl-up-cols';
      if (report.ok) cols.append(renderChangesSection(ctx));
      if (report.previewTable) {
        cols.append(renderPreviewTables(report.previewTable));
      } else {
        const fallback = upCard('Rapport de la mise à jour');
        const inner = document.createElement('div');
        inner.style.padding = '12px 16px';
        appendPreviewMarkdown(inner, report.preview || report.output);
        fallback.append(inner);
        cols.append(fallback);
      }
      preview.append(cols);
      // Pied : sauvegarde et commande équivalente — jamais de « Restaurer », qui écrit.
      foot.replaceChildren();
      if (report.backupPath) {
        const backup = document.createElement('span');
        backup.append('Sauvegarde ', text('span', 'mono', report.backupPath));
        foot.append(backup);
      }
      const formula = document.createElement('span');
      formula.className = 'pl-up-formula';
      formula.append(text('b', null, '›'), ` grimoire upgrade-flow run --dry-run${slug ? ' # ' + slug : ''}`);
      foot.append(formula);
      foot.hidden = false;
      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'btn pri';
      confirmBtn.textContent = 'Confirmer la mise à jour';
      confirmBtn.style.marginTop = '8px';
      confirmBtn.addEventListener('click', async () => {
        ctx.dock.echo(`grimoire upgrade-flow run --executor interactive${slug ? ' # ' + slug : ''}`);
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Mise à jour en cours…';
        const progress = row(dot('acc'), text('span', 'lbl', "Mise à jour en cours — jusqu'à une minute, ne fermez pas cet onglet."));
        preview.append(progress);
        const result = await ctx.api.updateProject(slug, true).catch((error) => ({ ok: false, error: error.message }));
        progress.remove();
        preview.append(text('div', 'lbl', result.ok
          ? "Mis à jour — le flow s'est arrêté au checkpoint final, jamais décidé à votre place."
          : ('Échec de la mise à jour' + (result.error ? ` : ${result.error}` : '.'))));
        if (result.nodes) preview.append(renderNodeList(result.nodes));
        if (result.backupPath) preview.append(text('div', 'lbl', `Sauvegarde : ${result.backupPath}`));
        // `proposals` est joint par le backend dès que le run les connaît —
        // `completed`, `upgraded-checkpoint-pending` ET `upgraded-but-failed`
        // (`project_update.py::_run_upgrade_flow`) — jamais seulement sur
        // succès. Garder cette condition sur `result.ok` (issue #538) faisait
        // disparaître la section Propositions ET son rafraîchissement dès
        // qu'`apply` refusait après écriture, alors que des propositions
        // pouvaient déjà être en attente (ou tout juste écrites par ce run
        // avant l'échec) : `state: "upgraded-but-failed"` implique toujours
        // `ok: false` (`_derive_state`), les deux blocs ci-dessous restaient
        // donc systématiquement morts sur cet état précis.
        const hasProposals = Array.isArray(result.proposals);
        if (hasProposals && result.proposals.length) {
          preview.append(text('div', 'lbl', `${result.proposals.length} proposition(s) en attente — voir la section Propositions ci-dessous.`));
        }
        confirmBtn.remove();
        // La fiche réapparaît sous le déroulé : c'est là que vivent les Propositions.
        wrap.hidden = false;
        if (result.ok) {
          markKitCheckpointPending(result.runId);
          // Un run complet s'arrête toujours, non décidé, au checkpoint
          // `destructive` (jamais un autre point d'arrêt pour un run mené à
          // son terme) — le bouton « Revoir dans l'IDE » doit donc le
          // refléter tout de suite, sans attendre un rechargement complet
          // de la fiche. N'a de sens que sur un run qui a réellement atteint
          // ce checkpoint : jamais sur `upgraded-but-failed`, dont le nœud en
          // échec (`apply`) précède `destructive` dans le déroulé.
          checkpointPendingRunId = result.runId;
        }
        if (result.ok || hasProposals) {
          // Le prochain retour sur la Flotte servirait sinon le blob mis
          // en cache d'avant la mise à jour jusqu'à 60 s (issue #510 point
          // 5). Le cache ne porte plus qu'un seul appel `/api/fleet` pour
          // tout le registre (#541) : on l'invalide en entier, ce qui reste
          // moins cher qu'avant (un seul appel réseau au prochain retour,
          // pas 2×N).
          invalidateFleetCache();
          // Jamais `options.refresh()` ici : il redessine toute la fiche et
          // effacerait ce déroulé à l'instant même où on vient de le montrer
          // (constat terrain, issue #506 — « l'Inspecteur revient
          // silencieusement à l'état initial »). Seules les propositions (et
          // la table d'agents qu'elles peuvent alimenter) ont besoin d'une
          // lecture fraîche ; `refreshProposals` — définie plus bas dans
          // cette même fiche — s'en charge sans toucher au reste du DOM.
          refreshProposals();
        }
      });
      preview.append(confirmBtn);
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      updateBtn.disabled = false;
      updateBtn.textContent = 'Mettre à jour — aperçu';
    }
  });
  actionsBlock.append(updateBtn);

  // ── Revoir dans l'IDE (issue #520, lot 2) ───────────────────────────────
  //
  // `grimoire upgrade-flow run` s'arrête volontairement, non décidé, au
  // checkpoint `destructive` ou sur une proposition V1 — rien depuis le
  // cockpit n'aidait jusqu'ici à ouvrir la revue de ces décisions ailleurs
  // que par la CLI à la main. Ce bouton n'écrit jamais rien lui-même : il
  // ne fait que préparer le texte que `/grimoire-upgrade-review` (le
  // prompt mission pack, issue #520 lot 1) attend, et le mettre à
  // disposition — presse-papiers si possible, affiché sinon. Aucun lien
  // d'ouverture d'IDE : rien dans ce projet ne déclare d'éditeur ouvrable
  // (aucun schéma `vscode://`, aucun champ de config) — inventer ce lien
  // promettrait une action que le cockpit ne peut pas tenir.
  //
  // Présent dans la page seulement s'il y a un travail réel à revoir —
  // jamais un bouton greyé sans objet, ni un bouton toujours là qui
  // inviterait à « revoir » un projet sans rien en attente.
  function pendingProposalSlugs(payload) {
    return (payload?.proposals || []).filter((p) => p.status === 'pending').map((p) => p.slug);
  }

  const reviewBtn = document.createElement('button');
  reviewBtn.type = 'button';
  reviewBtn.className = 'btn';
  reviewBtn.textContent = "Revoir dans l'IDE";
  const reviewPreview = document.createElement('div');
  // Classe distincte de `.pl-preview` (même style, `injectStyles` ci-dessus) :
  // le bouton « Mettre à jour » a déjà son propre `.pl-preview` dans le même
  // bloc d'actions — un sélecteur `.pl-preview` unique doit continuer à n'en
  // trouver qu'un (tests e2e existants, `test_workspace_cockpit_upgrade_
  // flow.py`).
  reviewPreview.className = 'pl-review-preview';
  reviewPreview.hidden = true;

  let pendingSlugs = [];
  function updateReviewButton(payload) {
    pendingSlugs = pendingProposalSlugs(payload);
    const hasWork = pendingSlugs.length > 0 || Boolean(checkpointPendingRunId);
    if (hasWork) {
      if (!reviewBtn.isConnected) actionsBlock.append(reviewBtn, reviewPreview);
    } else {
      reviewBtn.remove();
      reviewPreview.remove();
      reviewPreview.hidden = true;
    }
  }
  updateReviewButton(sheet.proposals);

  reviewBtn.addEventListener('click', async () => {
    const runId = checkpointPendingRunId || latestUpgradeRun?.runId || '';
    const toPaste = ['/grimoire-upgrade-review', runId, ...pendingSlugs].filter(Boolean).join(' ');
    const reviewCmd = `grimoire upgrade-flow review${slug ? ' # ' + slug : ''}`;
    ctx.dock.echo(reviewCmd);

    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(toPaste);
        copied = true;
      }
    } catch (_error) {
      copied = false;
    }

    reviewPreview.hidden = false;
    reviewPreview.replaceChildren();
    reviewPreview.append(text('div', null, copied
      ? 'Copié dans le presse-papiers :'
      : 'Presse-papiers indisponible — copiez ce texte à la main :'));
    const pasteCode = document.createElement('code');
    pasteCode.className = 'mono';
    pasteCode.textContent = toPaste;
    reviewPreview.append(pasteCode);
    reviewPreview.append(text('div', 'lbl', "Aucun IDE ouvrable déclaré pour ce projet — collez ce texte dans l'hôte de votre choix, ou lancez directement dans le projet :"));
    const cliCode = document.createElement('code');
    cliCode.className = 'mono';
    cliCode.textContent = 'grimoire upgrade-flow review';
    reviewPreview.append(cliCode);
  });

  ctx.inspector.append(actionsBlock);

  // ── Agents (#374) : table dans la fiche, détail éditable dans l'inspecteur ─
  //
  // Le clic sélectionne un agent et rend son détail dans l'inspecteur, sans
  // redessiner la fiche entière ni la refaire au serveur — seule une écriture
  // réussie re-fetch `agents()` pour tenir la table et l'inspecteur à jour
  // avec l'état réel du disque, jamais avec un optimisme local.
  let agentsPayload = sheet.agents;
  let selectedAgent = null;
  let agentsSection = null;

  const renderAgentBlock = () => {
    const previous = ctx.inspector.querySelector('[data-agent-inspector]');
    if (previous) previous.remove();
    if (!selectedAgent || !agentsPayload) return;
    const agent = agentsPayload.agents.find((a) => a.name === selectedAgent);
    if (!agent) { selectedAgent = null; return; }
    ctx.inspector.append(
      renderAgentInspector(ctx, agentsPayload, agent, {
        refresh: async (keepSelected) => {
          agentsPayload = await ctx.api.agents(slug).catch(() => agentsPayload);
          selectedAgent = keepSelected;
          const fresh = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
          agentsSection.replaceWith(fresh);
          agentsSection = fresh;
          renderAgentBlock();
        },
      }),
    );
  };

  const selectAgent = (agentName) => {
    selectedAgent = agentName === selectedAgent ? null : agentName;
    const fresh = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
    agentsSection.replaceWith(fresh);
    agentsSection = fresh;
    renderAgentBlock();
  };

  agentsSection = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
  wrap.append(agentsSection);

  // ── Propositions (#395) : section agents, sous la table ────────────────
  //
  // Accepter en crée un — l'agent nouvellement écrit doit apparaître dans la
  // table juste au-dessus sans recharger toute la fiche, donc son callback
  // re-fetch `agents()` en plus de `proposals()`.
  let proposalsPayload = sheet.proposals;
  let proposalsSection = null;

  const refreshProposals = async () => {
    const [freshProposals, freshAgents] = await Promise.all([
      ctx.api.proposals(slug).catch(() => proposalsPayload),
      ctx.api.agents(slug).catch(() => agentsPayload),
    ]);
    proposalsPayload = freshProposals;
    agentsPayload = freshAgents;
    updateReviewButton(proposalsPayload);
    const freshProposalsSection = renderProposalsSection(ctx, proposalsPayload, refreshProposals, slug);
    proposalsSection.replaceWith(freshProposalsSection);
    proposalsSection = freshProposalsSection;
    const freshAgentsSection = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
    agentsSection.replaceWith(freshAgentsSection);
    agentsSection = freshAgentsSection;
  };

  proposalsSection = renderProposalsSection(ctx, proposalsPayload, refreshProposals, slug);
  wrap.append(proposalsSection);
}

// À traiter (P32) ouvre la fiche sur ce qui attend : `#flotte?focus=kit`,
// `memory`, `checkpoint`, ou `proposal&proposal=<slug>`. Le bloc visé est
// marqué, amené à l'écran, et l'inspecteur s'ouvre s'il le porte.
function revealFocus(root, ctx) {
  const { focus, proposal } = ctx.params || {};
  if (!focus) return;
  const selector = focus === 'proposal' && proposal
    ? `[data-proposal="${CSS.escape(proposal)}"]`
    : `[data-focus="${CSS.escape(focus)}"]`;
  const target = root.querySelector(selector) || ctx.inspector.querySelector(selector);
  if (!target) return;
  target.dataset.focused = 'true';
  if (ctx.inspector.contains(target)) ctx.openPanel('inspector');
  target.scrollIntoView({ block: 'center' });
}

export async function mount(root, ctx) {
  injectStyles();
  const cockpit = ctx.host.kind === 'cockpit';
  // La portée de la coque décide seule (P31) : « Tous les projets » montre la
  // table de la Flotte, un projet montre sa fiche. Plus de zoom Flotte/Projet
  // propre à cet espace, plus de projet « prévisualisé » distinct du projet
  // servi : choisir une ligne de la Flotte change la portée (`ctx.scope.set`).
  const level = cockpit && ctx.scope && ctx.scope.all ? 'flotte' : 'projet';
  const selected = ctx.host.project || null;
  // Vue de la Flotte (`#flotte?vue=donnees`) : Projets par défaut.
  let view = ctx.params && ctx.params.vue === 'donnees' ? 'donnees' : 'projets';

  // Un seul tour réseau en vol à la fois (#541 point 4) : un double-clic sur
  // « Rafraîchir », ou une action qui appelle `options.refresh()` pendant
  // qu'un tour précédent tourne encore, rejoint la MÊME promesse plutôt que
  // de relancer un second lot d'appels en concurrence. Une fois ce tour
  // résolu, l'appel suivant en relance un frais — ça ne dédoublonne jamais
  // deux rafraîchissements légitimes mais séquentiels, seulement un vrai
  // chevauchement.
  //
  // Piège trouvé par le harnais générique de segments (#541 côté zoom) :
  // un simple `return inFlight` ici avale silencieusement un clic de zoom
  // survenu pendant que le tour précédent tourne encore — `level` change
  // bien (muté par le callback avant l'appel à `draw()`), mais aucun rendu
  // ne le reflète tant que ce tour précédent n'a pas déjà consommé
  // l'ancienne valeur. `queued` mémorise qu'un rendu frais reste dû ; à la
  // fin du tour en cours, on ne l'enchaîne QUE si la cible a vraiment changé
  // (`stateKey()` avant/après) — un double « Rafraîchir la flotte » pendant
  // le même tour (`test_refresh_de_la_flotte_ne_double_jamais_les_appels_en_
  // vol`) ne doit toujours produire qu'UN seul appel `/api/fleet`, jamais
  // un second relancé après coup pour une cible identique.
  let inFlight = null;
  let queued = false;
  let queuedOpts;

  const stateKey = () => level + '|' + (level === 'projet' ? selected : view);

  const runDraw = (opts) => {
    const startedKey = stateKey();
    inFlight = drawOnce(opts).finally(() => {
      inFlight = null;
      if (queued) {
        queued = false;
        const next = queuedOpts;
        queuedOpts = undefined;
        if (stateKey() !== startedKey) runDraw(next);
      }
    });
    return inFlight;
  };

  const drawOnce = async ({ forceFleet = false } = {}) => {
    ctx.docbar.setBreadcrumb([]);

    // Coquille immédiate (#541 point 3) : jamais d'écran vide le temps que
    // les appels réseau reviennent — un état « chargement… » se peint tout
    // de suite, avec le titre déjà connu côté projet (slug/host.status),
    // puis remplacé par le rendu réel une fois les données résolues.
    const shell = document.createElement('div');
    shell.className = 'pl-wrap';
    if (level === 'projet') {
      // Le nom du projet (accents compris), jamais son slug (P31).
      shell.append(spaceHead(ctx.scope ? ctx.scope.name : 'Projet servi'));
    }
    shell.append(...ctx.loading('Chargement…', 3));
    root.replaceChildren(shell);

    if (level === 'flotte' && view === 'donnees') {
      ctx.inspector.replaceChildren();
      const { renderMachine } = await import('./donnees.js');
      if (ctx.signal.aborted) return;
      await renderMachine(root, ctx, { seg: viewSeg(view, onView), decorate: withViewSeg });
    } else if (level === 'flotte') {
      ctx.inspector.replaceChildren(); // pas de détail : pas de colonne (P62)
      // `forceFleet` ne vient que du bouton « Rafraîchir la flotte » — un
      // simple retour sur ce niveau (zoom, sélection d'un autre projet puis
      // retour) sert le cache de `loadFleet` (issue #510 point 5).
      // L'attention (P22) lit tout le registre : elle ne part qu'après la
      // flotte, pour ne pas lui disputer le serveur, et ne la retient jamais.
      // La table se peint dès la flotte, puis se repeint avec les badges quand
      // l'attention répond (le filtre vit dans `fleetFilter`, la saisie en
      // cours garde son focus).
      const rows = await loadFleet(ctx, { force: forceFleet });
      if (ctx.signal.aborted) return;
      const paintFleet = (attention, pending) => {
        const field = root.querySelector('.pl-search');
        const caret = field && document.activeElement === field ? field.selectionStart : null;
        root.replaceChildren(); // retire la coquille « Chargement… » ou le rendu précédent
        renderFleet(
          root, ctx, rows, attention,
          (slug) => ctx.scope.set(slug),
          () => draw({ forceFleet: true }),
          { pending, viewSwitch: viewSeg(view, onView) },
        );
        if (caret !== null) {
          const next = root.querySelector('.pl-search');
          if (next) { next.focus(); next.setSelectionRange(caret, caret); }
        }
        return root.firstElementChild;
      };
      const painted = paintFleet(null, true);
      ctx.dock.echo('grimoire cockpit list');
      const attention = await ctx.api.attention(true).catch(() => null);
      // Un autre rendu (zoom sur un projet, rafraîchissement) a pris la main : on ne l'écrase pas.
      if (ctx.signal.aborted || root.firstElementChild !== painted) return;
      paintFleet(attention, false);
    } else {
      // #548 : `loadSheet()` porte désormais le nom du projet aussi (fiche
      // `sheet.name`, résolue côté serveur depuis le registre) — plus de
      // second tour réseau via `projects()` pour l'obtenir côté cockpit
      // (restes #541 point 2, où les deux appels partaient déjà au moins en
      // parallèle plutôt qu'en série ; ici il n'y en a plus qu'un).
      const sheet = await loadSheet(ctx, selected);
      const name = cockpit ? sheet.name || null : ctx.host.status?.slug || null;
      if (ctx.signal.aborted) return;
      root.replaceChildren(); // retire la coquille « Chargement… » avant le vrai rendu
      renderSheet(root, ctx, selected, name, sheet, { refresh: draw });
      revealFocus(root, ctx);
      ctx.dock.echo('grimoire doctor');
    }
  };

  const onView = (next) => {
    if (next === view) return;
    view = next;
    ctx.setItem(next === 'donnees' ? { vue: 'donnees' } : {});
    draw();
  };

  const draw = (opts) => {
    if (inFlight) {
      queued = true;
      queuedOpts = opts;
      return inFlight;
    }
    return runDraw(opts);
  };

  await draw();
}
