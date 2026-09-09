// Espace Concevoir — LOT 3.
//
// Remplace `blueprints.html`, `patterns.html`, `extensions.html` (bibliothèque
// en panneau). Zoom Projet → Workflow → Nœud (+ Flotte, lecture seule, sur le
// cockpit) ; vues Carte, Board, Liste au niveau Projet ; éditeur de graphe au
// niveau Workflow ; inspecteur à quatre onglets au niveau Nœud.
//
// L'éditeur hérité `web/bp2-core.js` (97 Ko, ~1900 lignes) est un script
// classique câblé sur les identifiants DOM fixes de `blueprints.html` — il
// n'exporte rien, n'est pas un module ES et écrit ses propres couleurs
// (bp2.css). Il n'est donc pas importable tel quel derrière le contrat
// `mount(root, ctx)` (aucun `fetch` hors `ctx.api`, aucune couleur hors
// tokens). Ce que ce module RÉUTILISE de bp2 : les mêmes routes et le même
// contrat de données (`/api/blueprints/<id>`, `/validate`, `/simulate`,
// `/compile`, `/api/primitives`, `/api/cost-model`) et la même mécanique
// conceptuelle (toile avec zoom, palette de nœuds, inspecteur à onglets,
// résultats de simulation projetés sur les nœuds). Ce qui est NEUF : le rendu,
// entièrement réécrit contre `ctx` et les primitives de `shell.css`.
//
// API consommées : ctx.api.blueprintContainers() (nouvelle route,
// `/api/workspace/blueprints` — enrichit `/api/blueprints` avec le genre, les
// agents délégués, l'équipe et la dernière modification, absents de
// `project_health.flows`), ctx.api.blueprintGet/Put/Validate/Simulate/Compile,
// ctx.api.primitives(), ctx.api.costModel().
//
// Limite restante (#356, suite à la fusion #351 de `serve` dans `cockpit
// serve`) : seules `blueprintPut` (PUT) et `/compile` écrivent sur disque et
// restent atelier-only, bloquées côté client par `readOnly` (api.js). GET,
// `/diff`, `/validate` et `/simulate` sont des lectures ou du calcul pur —
// elles répondent désormais sur les deux hôtes pour le projet déjà
// sélectionné (`cmd_cockpit.py::do_GET`/`do_POST`), donc les niveaux Workflow
// et Nœud fonctionnent identiquement sur l'atelier et sur le cockpit.

const ZOOM_LEVELS_ATELIER = [
  { id: 'projet', label: 'Projet' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'noeud', label: 'Nœud' },
];
const ZOOM_LEVELS_COCKPIT = [
  { id: 'flotte', label: 'Flotte' },
  { id: 'projet', label: 'Projet' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'noeud', label: 'Nœud' },
];
const VIEWS = [
  { id: 'carte', label: 'Carte' },
  { id: 'board', label: 'Board' },
  { id: 'liste', label: 'Liste' },
];
const GENRE_LABEL = { blueprint: 'Blueprint', studio: 'Studio' };
const RANK_COL = 230;
const RANK_ROW = 118;

// ── Styles scopés : injectés une fois, jamais dans shell.css (règle du lot) ─

function injectStyles() {
  if (document.getElementById('cv-style')) return;
  const style = document.createElement('style');
  style.id = 'cv-style';
  style.textContent = `
    .cv-projet { padding: var(--sp-4); }
    .cv-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--sp-3); }
    .cv-card {
      text-align: left; border: 1px solid var(--line); border-radius: var(--r);
      background: var(--e1); padding: var(--sp-3); cursor: pointer; display: flex;
      flex-direction: column; gap: 6px; color: var(--ink);
    }
    .cv-card:hover { background: var(--e2); }
    .cv-card[aria-selected="true"] { outline: 2px solid var(--acc); outline-offset: -2px; }
    .cv-card h3 { margin: 0; font-size: var(--t-s); font-weight: 500; }
    .cv-board { display: flex; gap: var(--sp-4); align-items: flex-start; }
    .cv-board-col { flex: 1; min-width: 220px; }
    .cv-board-col h3 { font-size: var(--t-min); color: var(--ink2); font-weight: 500; margin: 0 0 var(--sp-2); }
    .cv-board-col .cv-cards { grid-template-columns: 1fr; }
    table.cv-table { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    table.cv-table th { text-align: left; color: var(--ink3); font-weight: 500; font-size: var(--t-min);
      padding: 6px var(--sp-2); border-bottom: 1px solid var(--line); }
    table.cv-table td { padding: 7px var(--sp-2); border-bottom: 1px solid var(--line); }
    table.cv-table tbody tr { cursor: pointer; }
    table.cv-table tbody tr:hover { background: var(--e1); }
    table.cv-table tbody tr[aria-selected="true"] { background: var(--accsoft); }
    .cv-graph { position: relative; min-height: 100%; padding: var(--sp-4); }
    .cv-toolbar { position: sticky; top: 0; z-index: 6; display: flex; align-items: center;
      gap: var(--sp-2); padding: var(--sp-2) var(--sp-3); background: var(--bar);
      border: 1px solid var(--line); border-radius: var(--r); margin-bottom: var(--sp-3); }
    .cv-svg { position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }
    .cv-node {
      position: absolute; width: 190px; border: 1px solid var(--line); border-radius: var(--r);
      background: var(--e2); padding: 8px 10px; cursor: pointer; box-shadow: var(--shadow);
    }
    .cv-node:hover { background: var(--e3); }
    .cv-node[aria-selected="true"] { outline: 2px solid var(--acc); outline-offset: 1px; }
    .cv-node .kind { font-size: var(--t-min); text-transform: uppercase; letter-spacing: .04em; color: var(--ink3); }
    .cv-node .label { font-size: var(--t-s); color: var(--ink); margin-top: 2px; }
    .cv-node .ref { font-family: var(--mono); font-size: var(--t-min); color: var(--ink2); margin-top: 2px; }
    .cv-node.dim { opacity: .45; }
    .cv-palette {
      position: absolute; top: var(--sp-3); right: var(--sp-3); z-index: 7; width: 220px;
      background: var(--e1); border: 1px solid var(--line); border-radius: var(--r);
      box-shadow: var(--shadow-tip); max-height: 70%; display: flex; flex-direction: column;
    }
    .cv-palette[data-open="0"] { display: none; }
    .cv-palette-head { display: flex; align-items: center; justify-content: space-between;
      padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--line); }
    .cv-palette-body { overflow: auto; padding: var(--sp-2); display: flex; flex-direction: column; gap: 4px; }
    .cv-prim { text-align: left; border: 1px solid var(--line); border-radius: var(--r); background: var(--e2);
      padding: 7px 9px; cursor: pointer; }
    .cv-prim:hover { background: var(--e3); }
    .cv-prim .n { font-size: var(--t-s); color: var(--ink); }
    .cv-prim .d { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .cv-noeud { padding: var(--sp-4); display: flex; flex-direction: column; align-items: center; }
    .cv-noeud .cv-graph { width: 100%; min-height: 260px; }
    .cv-tabs { display: flex; border-bottom: 1px solid var(--line); margin-bottom: var(--sp-3); }
    .cv-tab { padding: 0 var(--sp-2); height: 32px; background: none; border: 0; color: var(--ink2);
      border-bottom: 2px solid transparent; cursor: pointer; font-size: var(--t-s); }
    .cv-tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--acc); font-weight: 500; }
    .cv-kv { display: grid; grid-template-columns: auto 1fr; gap: 4px var(--sp-2); font-size: var(--t-s); }
    .cv-kv dt { color: var(--ink3); }
    .cv-kv dd { margin: 0; color: var(--ink); word-break: break-word; }
    .cv-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .cv-msg { color: var(--ink2); font-size: var(--t-s); padding: var(--sp-2) 0; }
    .cv-json { font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap;
      background: var(--e1); border: 1px solid var(--line); border-radius: var(--r); padding: var(--sp-2); }
  `;
  document.head.append(style);
}

// ── Petits utilitaires de rendu ─────────────────────────────────────────────

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key.startsWith('data-')) node.setAttribute(key, value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function dotWord(level, word) {
  return el('span', { class: 'chip' }, el('span', { class: 'dot' + (level ? ' ' + level : '') }), word);
}

function term(id, label) {
  return el('span', { 'data-term': id }, label);
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('fr-FR', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function nodeId(ref) {
  return String(ref || '').split('.')[0];
}

/** Rang topologique (colonne) de chaque nœud, sur le canal `happy` seulement —
 * même canal nominal que `blueprint_simulate`. Un cycle ne bloque pas le
 * rendu : les nœuds restants gardent leur rang par défaut (0). */
function computeRanks(nodes, edges) {
  const ids = nodes.map((n) => n.id);
  const preds = new Map(ids.map((id) => [id, new Set()]));
  const succs = new Map(ids.map((id) => [id, new Set()]));
  for (const edge of edges || []) {
    const channel = edge.channel || 'happy';
    if (channel !== 'happy') continue;
    const src = nodeId(edge.from);
    const dst = nodeId(edge.to);
    if (preds.has(dst) && preds.has(src) && src !== dst) {
      preds.get(dst).add(src);
      succs.get(src).add(dst);
    }
  }
  const rank = new Map(ids.map((id) => [id, 0]));
  const indeg = new Map(ids.map((id) => [id, preds.get(id).size]));
  const queue = ids.filter((id) => indeg.get(id) === 0);
  let guard = 0;
  while (queue.length && guard++ < ids.length * 2 + 4) {
    const id = queue.shift();
    for (const next of succs.get(id) || []) {
      rank.set(next, Math.max(rank.get(next), rank.get(id) + 1));
      indeg.set(next, indeg.get(next) - 1);
      if (indeg.get(next) <= 0) queue.push(next);
    }
  }
  return rank;
}

/** Position {x, y} par nœud, en colonnes de rang avec des rangées empilées.
 *
 * `topOffset` dégage la barre d'outils du niveau Workflow (`position:
 * sticky`) : elle occupe visuellement le haut de la toile quel que soit
 * l'ordre de peinture, donc un nœud posé dessous intercepterait ses propres
 * clics — trouvé par le harnais Playwright, pas à la lecture de la feuille. */
function layoutNodes(nodes, edges, topOffset = 24) {
  const rank = computeRanks(nodes, edges);
  const perCol = new Map();
  const pos = new Map();
  for (const node of nodes) {
    const col = rank.get(node.id) || 0;
    const row = perCol.get(col) || 0;
    perCol.set(col, row + 1);
    pos.set(node.id, { x: col * RANK_COL + 24, y: row * RANK_ROW + topOffset });
  }
  return pos;
}

function renderEdgesSvg(nodes, edges, pos) {
  const width = Math.max(...[...pos.values()].map((p) => p.x), 0) + RANK_COL;
  const height = Math.max(...[...pos.values()].map((p) => p.y), 0) + RANK_ROW;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'cv-svg');
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  for (const edge of edges || []) {
    const from = pos.get(nodeId(edge.from));
    const to = pos.get(nodeId(edge.to));
    if (!from || !to) continue;
    const x1 = from.x + 190, y1 = from.y + 22, x2 = to.x, y2 = to.y + 22;
    const mid = (x1 + x2) / 2;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);
    path.setAttribute('fill', 'none');
    const channel = edge.channel || 'happy';
    path.setAttribute('stroke', channel === 'happy' ? 'var(--ink3)' : 'var(--warn)');
    path.setAttribute('stroke-width', '1.5');
    if (channel !== 'happy') path.setAttribute('stroke-dasharray', '4 3');
    svg.append(path);
  }
  return svg;
}

// ── Mount ────────────────────────────────────────────────────────────────

export async function mount(root, ctx) {
  injectStyles();

  // Un projet déjà ciblé (`?project=`, ou sélection courante du cockpit —
  // voir `api.js: boot()`) doit ouvrir directement sur ses blueprints : la
  // Flotte n'est le point d'entrée que quand aucun projet n'est encore choisi
  // (#356). Piloter, lui, reste Flotte-first même projet sélectionné : son
  // niveau Flotte est la vue de pilotage, pas un repli d'absence de projet.
  const state = {
    zoom: ctx.host.kind === 'cockpit' && !ctx.host.project ? 'flotte' : 'projet',
    view: 'carte',
    containers: [],
    selectedId: null,
    blueprint: null,
    lint: null,
    simulate: null,
    paletteOpen: false,
    selectedNodeId: null,
  };

  const zoomLevels = ctx.host.kind === 'cockpit' ? ZOOM_LEVELS_COCKPIT : ZOOM_LEVELS_ATELIER;
  // Lecture, validation et simulation du graphe marchent désormais sur les
  // deux hôtes : `cockpit serve` est le seul serveur restant (#351) et sait
  // depuis #356 servir `/api/blueprints/<id>` (+ validate/simulate) pour le
  // projet déjà sélectionné — seules l'écriture (`blueprintPut`, absente
  // d'ici) et `/compile` restent atelier-only, derrière `readOnly` côté
  // client. Cette constante reste nommée `graphAvailable` (elle garde ses six
  // points d'appel) mais n'a plus de condition réelle à trancher.
  const graphAvailable = true;

  // Rail « 2 » (bibliothèque) : la coque ne sait pas ce qu'est une palette de
  // nœuds (README, « ctx.rail … enregistre ce que fait le rail ‘2’ tant que
  // cet espace est monté »). Le tiroir n'existe qu'au niveau Workflow, avec
  // un blueprint chargé — mêmes conditions que le bouton « Bibliothèque » de
  // la barre d'outils du graphe ; en dehors, « 2 » n'a rien à faire.
  ctx.rail?.on('library', () => {
    if (!graphAvailable || state.zoom !== 'workflow' || !state.selectedId) return;
    state.paletteOpen = !state.paletteOpen;
    render();
  });

  function aborted() {
    return ctx.signal.aborted;
  }

  function setZoom(id) {
    if (aborted()) return;
    state.zoom = id;
    ctx.docbar.setZoom(zoomLevels, id, setZoom);
    render();
  }

  function setView(id) {
    state.view = id;
    render();
  }

  // ── Chargement ────────────────────────────────────────────────────────

  async function loadContainers() {
    const payload = await ctx.api.blueprintContainers();
    if (aborted()) return;
    state.containers = payload.blueprints || [];
  }

  async function loadBlueprint(id) {
    state.blueprint = null;
    state.lint = null;
    state.simulate = null;
    state.selectedNodeId = null;
    if (!graphAvailable) return;
    const blueprint = await ctx.api.blueprintGet(id);
    if (aborted()) return;
    state.blueprint = blueprint;
    try {
      const [lint, simulate] = await Promise.all([
        ctx.api.blueprintValidate(id, blueprint),
        ctx.api.blueprintSimulate(id, blueprint),
      ]);
      if (aborted()) return;
      state.lint = lint;
      state.simulate = simulate;
      const count = (lint.errors || []).length + (lint.warnings || []).length;
      ctx.docbar.setValidation(
        (lint.errors || []).length
          ? `${lint.errors.length} erreur(s)`
          : (lint.warnings || []).length ? `${lint.warnings.length} avertissement(s)` : 'validé',
        (lint.errors || []).length ? 'bad' : (lint.warnings || []).length ? 'warn' : 'ok',
      );
      ctx.dock.log(
        'problemes',
        `$ grimoire blueprint validate ${id}`,
        ...(lint.errors || []).map((m) => `erreur : ${m}`),
        ...(lint.warnings || []).map((m) => `avertissement : ${m}`),
        count ? '' : 'aucune erreur ni avertissement.',
      );
    } catch (error) {
      ctx.dock.log('problemes', `validation impossible : ${error.message}`);
    }
  }

  function selectContainer(id) {
    state.selectedId = id;
    renderInspectorProjet();
    renderCanvasOnly();
  }

  async function zoomToWorkflow(id) {
    state.selectedId = id;
    if (!graphAvailable) {
      setZoom('workflow');
      return;
    }
    setZoom('workflow');
    await loadBlueprint(id);
    if (aborted()) return;
    render();
  }

  function zoomToNode(id) {
    if (!state.blueprint) return;
    state.selectedNodeId = id;
    setZoom('noeud');
  }

  // ── Niveau Flotte (cockpit, lecture seule) ──────────────────────────────

  async function renderFlotte() {
    ctx.docbar.setViews([], null);
    ctx.docbar.setValidation('');
    let projects = [];
    try {
      const payload = await ctx.api.projects();
      projects = payload.projects || [];
    } catch { /* état vide ci-dessous */ }
    if (aborted()) return;
    root.replaceChildren();
    if (!projects.length) {
      root.append(ctx.empty(
        'Concevoir — Flotte',
        "Aucun projet enregistré auprès du cockpit. Un projet s'enregistre au premier `grimoire init`.",
        'grimoire init',
      ));
      return;
    }
    const cards = el('div', { class: 'cv-cards' });
    for (const project of projects) {
      const card = el(
        'button', { type: 'button', class: 'cv-card' },
        el('h3', { text: project.name || project.slug }),
        el('span', { class: 'lbl mono' }, project.slug),
      );
      card.addEventListener('click', () => { location.search = '?project=' + encodeURIComponent(project.slug); });
      cards.append(card);
    }
    root.append(el('div', { class: 'cv-projet' },
      el('p', { class: 'cv-msg' }, 'Sélectionnez un projet — la Flotte est en lecture seule ici, et ', term('cockpit', 'le cockpit'), " n'édite aucun blueprint."),
      cards,
    ));
    ctx.inspector.replaceChildren(el('p', { class: 'cv-msg' }, 'Sélectionnez un projet pour voir ses blueprints.'));
  }

  // ── Niveau Projet ────────────────────────────────────────────────────────

  function containerById(id) {
    return state.containers.find((c) => c.id === id) || null;
  }

  function renderInspectorProjet() {
    const container = containerById(state.selectedId);
    if (!container) {
      ctx.inspector.replaceChildren(el('p', { class: 'cv-msg' }, 'Sélectionnez un ', term('blueprint', 'blueprint'), ' pour voir son détail.'));
      return;
    }
    const kv = el('dl', { class: 'cv-kv' },
      el('dt', {}, 'Genre'), el('dd', { text: GENRE_LABEL[container.genre] || container.genre }),
      el('dt', {}, term('noeud', 'Nœuds')), el('dd', { text: String(container.nodes) }),
      el('dt', {}, 'Connexions'), el('dd', { text: String(container.edges) }),
      el('dt', {}, term('equipe', 'Équipe')), el('dd', { text: container.team || '—' }),
      el('dt', {}, term('agent', 'Agents')), el('dd', { text: container.agents.length ? container.agents.join(', ') : '—' }),
      el('dt', {}, term('validation', 'Validation')),
      el('dd', {}, dotWord(container.validated ? 'ok' : 'warn', container.validated ? 'validé' : 'à valider')),
      el('dt', {}, 'Modifié'), el('dd', { text: fmtDate(container.modified_at) }),
    );
    const open = el('button', { type: 'button', class: 'btn pri' }, 'Ouvrir le workflow →');
    open.addEventListener('click', () => zoomToWorkflow(container.id));
    ctx.inspector.replaceChildren(
      el('h3', { style: 'margin:0 0 8px' }, container.name),
      kv,
      el('div', { style: 'margin-top:12px' }, open),
    );
  }

  function renderProjet() {
    ctx.docbar.setViews(VIEWS, state.view, setView);
    const aggregate = state.containers.length
      ? `${state.containers.length} blueprint(s)`
      : '';
    ctx.docbar.setValidation(
      aggregate,
      state.containers.length && state.containers.every((c) => c.validated) ? 'ok' : 'warn',
    );
    root.replaceChildren();
    if (!state.containers.length) {
      root.append(ctx.empty(
        'Concevoir',
        "Ce projet n'a pas encore de blueprint. La toile se peuplera dès qu'il en aura un.",
        'grimoire blueprint new <id>',
      ));
      return;
    }
    const wrap = el('div', { class: 'cv-projet' });
    if (state.view === 'carte') wrap.append(renderCarte());
    else if (state.view === 'board') wrap.append(renderBoard());
    else wrap.append(renderListe());
    root.append(wrap);
    renderInspectorProjet();
  }

  function renderCanvasOnly() {
    // Redessine la sélection sans tout reconstruire (évite de perdre le focus
    // clavier ou le défilement quand on ne fait que sélectionner).
    for (const node of root.querySelectorAll('[data-container-id]')) {
      node.setAttribute('aria-selected', String(node.dataset.containerId === state.selectedId));
    }
  }

  function makeCard(container) {
    const card = el(
      'button', { type: 'button', class: 'cv-card', 'data-container-id': container.id, 'aria-selected': String(container.id === state.selectedId) },
      el('h3', { text: container.name }),
      el('div', { class: 'row' }, dotWord(container.validated ? 'ok' : 'warn', container.validated ? 'validé' : 'à valider')),
      el('span', { class: 'lbl' }, `${GENRE_LABEL[container.genre] || container.genre} · ${container.nodes} nœud(s)`),
    );
    card.addEventListener('click', () => selectContainer(container.id));
    card.addEventListener('dblclick', () => zoomToWorkflow(container.id));
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') zoomToWorkflow(container.id);
    });
    return card;
  }

  function renderCarte() {
    const cards = el('div', { class: 'cv-cards' });
    for (const container of state.containers) cards.append(makeCard(container));
    return cards;
  }

  function renderBoard() {
    const board = el('div', { class: 'cv-board' });
    const genres = [...new Set(state.containers.map((c) => c.genre))];
    for (const genre of genres) {
      const items = state.containers.filter((c) => c.genre === genre);
      const cards = el('div', { class: 'cv-cards' });
      for (const container of items) cards.append(makeCard(container));
      board.append(el('div', { class: 'cv-board-col' },
        el('h3', { text: `${GENRE_LABEL[genre] || genre} · ${items.length}` }),
        cards,
      ));
    }
    return board;
  }

  function renderListe() {
    const table = el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', { text: 'Nom' }), el('th', { text: 'Genre' }), el('th', { text: 'Agents' }),
        el('th', { text: 'Équipe' }), el('th', { text: 'Validation' }), el('th', { text: 'Dernière modification' }),
      )),
    );
    const tbody = el('tbody');
    for (const container of state.containers) {
      const row = el(
        'tr', { 'data-container-id': container.id, 'aria-selected': String(container.id === state.selectedId), tabindex: '0' },
        el('td', { text: container.name }),
        el('td', { text: GENRE_LABEL[container.genre] || container.genre }),
        el('td', { text: container.agents.length ? container.agents.join(', ') : '—' }),
        el('td', { text: container.team || '—' }),
        el('td', {}, dotWord(container.validated ? 'ok' : 'warn', container.validated ? 'validé' : 'à valider')),
        el('td', { class: 'mono lbl', text: fmtDate(container.modified_at) }),
      );
      row.addEventListener('click', () => selectContainer(container.id));
      row.addEventListener('dblclick', () => zoomToWorkflow(container.id));
      row.addEventListener('keydown', (event) => { if (event.key === 'Enter') zoomToWorkflow(container.id); });
      tbody.append(row);
    }
    table.append(tbody);
    return table;
  }

  // ── Niveau Workflow : l'éditeur de graphe ───────────────────────────────

  function lintForNode(id) {
    if (!state.lint) return { errors: [], warnings: [] };
    const match = (m) => m.includes(id);
    return {
      errors: (state.lint.errors || []).filter(match),
      warnings: (state.lint.warnings || []).filter(match),
    };
  }

  function pressureForNode(id) {
    return (state.simulate?.contextPressure || []).find((p) => p.nodeId === id) || null;
  }

  async function runValidate() {
    if (!state.blueprint) return;
    ctx.dock.setTab('problemes');
    ctx.dock.echo(`grimoire blueprint validate ${state.selectedId}`);
    await loadBlueprint(state.selectedId);
    render();
  }

  async function runSimulate() {
    if (!state.blueprint) return;
    ctx.dock.echo(`grimoire blueprint simulate ${state.selectedId}`);
    try {
      const simulate = await ctx.api.blueprintSimulate(state.selectedId, state.blueprint);
      state.simulate = simulate;
      ctx.dock.log('problemes', `$ grimoire blueprint simulate ${state.selectedId}`, `verdict : ${simulate.verdict}`);
      render();
    } catch (error) {
      ctx.dock.log('problemes', `simulation refusée : ${error.message}`);
    }
  }

  async function runCompile() {
    if (!state.blueprint) return;
    ctx.dock.echo(`grimoire blueprint compile ${state.selectedId}`);
    try {
      const compiled = await ctx.api.blueprintCompile(state.selectedId, state.blueprint);
      ctx.dock.log('problemes', `$ grimoire blueprint compile ${state.selectedId}`, `artefact : ${compiled.artifact || '(sans nom)'}`);
      ctx.dock.setTab('problemes');
    } catch (error) {
      ctx.dock.log('problemes', `compilation refusée : ${error.message}`);
    }
  }

  async function loadPrimitivesInto(body) {
    body.replaceChildren(el('p', { class: 'cv-msg' }, 'Chargement…'));
    try {
      const catalogue = await ctx.api.primitives();
      body.replaceChildren();
      for (const [name, info] of Object.entries(catalogue.primitives || {})) {
        const button = el('button', { type: 'button', class: 'cv-prim' },
          el('div', { class: 'n' }, term('pattern', name)),
          el('div', { class: 'd', text: info.role }),
        );
        button.addEventListener('click', () => addNode(name));
        body.append(button);
      }
    } catch (error) {
      body.replaceChildren(el('p', { class: 'cv-msg', text: 'primitives indisponibles : ' + error.message }));
    }
  }

  function addNode(primitiveRole) {
    if (!state.blueprint) return;
    const existing = new Set((state.blueprint.nodes || []).map((n) => n.id));
    let candidate = primitiveRole.toLowerCase();
    let n = 1;
    while (existing.has(candidate)) candidate = `${primitiveRole.toLowerCase()}-${++n}`;
    state.blueprint.nodes = [...(state.blueprint.nodes || []), {
      id: candidate, kind: 'pattern', ref: '', role: primitiveRole,
      label: `Nouveau nœud (${primitiveRole})`, description: '', pins: [],
    }];
    ctx.dock.log('problemes', `nœud ajouté : ${candidate} (${primitiveRole}) — référence à compléter dans Propriétés.`);
    render();
  }

  function renderWorkflow() {
    ctx.docbar.setViews([], null);
    if (!graphAvailable) {
      ctx.docbar.setValidation('atelier requis', 'warn');
      root.replaceChildren(ctx.empty(
        "L'éditeur de graphe n'est pas disponible ici",
        "Le cockpit sert la Flotte en lecture seule ; ouvrir et éditer un blueprint se fait depuis l'atelier de ce projet.",
        'grimoire serve',
      ));
      ctx.inspector.replaceChildren();
      return;
    }
    if (!state.selectedId) {
      root.replaceChildren(ctx.empty('Concevoir — Workflow', 'Aucun blueprint sélectionné : revenez au niveau Projet.', ''));
      return;
    }
    if (!state.blueprint) {
      root.replaceChildren(el('p', { class: 'cv-msg' }, 'Chargement du blueprint…'));
      return;
    }
    const bp = state.blueprint;
    const pos = layoutNodes(bp.nodes || [], bp.edges || [], 68);
    const graph = el('div', { class: 'cv-graph' });

    const toolbar = el('div', { class: 'cv-toolbar' },
      el('strong', { style: 'margin-right:auto' }, bp.name || bp.id),
    );
    const btnValidate = el('button', { type: 'button', class: 'btn', text: 'Valider' });
    const btnSimulate = el('button', { type: 'button', class: 'btn', text: 'Simuler' });
    const btnCompile = el('button', { type: 'button', class: 'btn pri', text: 'Compiler' });
    btnValidate.addEventListener('click', runValidate);
    btnSimulate.addEventListener('click', runSimulate);
    btnCompile.addEventListener('click', runCompile);
    const btnLib = el('button', { type: 'button', class: 'btn', 'data-term': 'pattern', text: 'Bibliothèque' });
    btnLib.addEventListener('click', () => { state.paletteOpen = !state.paletteOpen; render(); });
    toolbar.append(btnLib, btnValidate, btnSimulate, btnCompile);
    graph.append(toolbar);

    graph.append(renderEdgesSvg(bp.nodes || [], bp.edges || [], pos));
    for (const node of bp.nodes || []) {
      const p = pos.get(node.id) || { x: 0, y: 0 };
      const nodeLint = lintForNode(node.id);
      const box = el(
        'div',
        {
          class: 'cv-node', tabindex: '0', 'aria-selected': String(node.id === state.selectedNodeId),
          style: `left:${p.x}px; top:${p.y}px`,
        },
        el('div', { class: 'kind' }, term('noeud', node.role || node.kind || 'nœud')),
        el('div', { class: 'label', text: node.label || node.id }),
        el('div', { class: 'ref mono', text: node.ref || '(sans référence)' }),
        nodeLint.errors.length ? el('div', {}, dotWord('bad', `${nodeLint.errors.length} erreur(s)`)) : null,
      );
      box.addEventListener('click', () => { state.selectedNodeId = node.id; render(); });
      box.addEventListener('dblclick', () => zoomToNode(node.id));
      box.addEventListener('keydown', (event) => { if (event.key === 'Enter') zoomToNode(node.id); });
      graph.append(box);
    }

    const palette = el('div', { class: 'cv-palette', 'data-open': state.paletteOpen ? '1' : '0' },
      el('div', { class: 'cv-palette-head' },
        el('strong', {}, term('pattern', 'Bibliothèque de nœuds')),
      ),
      el('div', { class: 'cv-palette-body' }),
    );
    graph.append(palette);
    if (state.paletteOpen) loadPrimitivesInto(palette.querySelector('.cv-palette-body'));

    root.replaceChildren(graph);
    renderInspectorWorkflow();
  }

  function renderInspectorWorkflow() {
    const bp = state.blueprint;
    if (!bp) { ctx.inspector.replaceChildren(); return; }
    if (state.selectedNodeId) {
      const node = (bp.nodes || []).find((n) => n.id === state.selectedNodeId);
      if (node) {
        const open = el('button', { type: 'button', class: 'btn pri' }, 'Ouvrir le nœud →');
        open.addEventListener('click', () => zoomToNode(node.id));
        ctx.inspector.replaceChildren(
          el('h3', { style: 'margin:0 0 8px', text: node.label || node.id }),
          el('div', { style: 'margin-bottom:10px' }, open),
        );
        return;
      }
    }
    const kv = el('dl', { class: 'cv-kv' },
      el('dt', {}, term('noeud', 'Nœuds')), el('dd', { text: String((bp.nodes || []).length) }),
      el('dt', {}, 'Connexions'), el('dd', { text: String((bp.edges || []).length) }),
      el('dt', {}, term('validation', 'Validation')),
      el('dd', {}, state.lint
        ? dotWord((state.lint.errors || []).length ? 'bad' : (state.lint.warnings || []).length ? 'warn' : 'ok',
          `${(state.lint.errors || []).length} erreur(s), ${(state.lint.warnings || []).length} avertissement(s)`)
        : '—'),
    );
    ctx.inspector.replaceChildren(
      el('h3', { style: 'margin:0 0 8px', text: bp.name || bp.id }),
      el('p', { class: 'cv-msg', text: bp.description || '' }),
      kv,
    );
  }

  // ── Niveau Nœud : focus + voisins + inspecteur à quatre onglets ────────

  function neighborsOf(bp, id) {
    const ids = new Set([id]);
    for (const edge of bp.edges || []) {
      const src = nodeId(edge.from), dst = nodeId(edge.to);
      if (src === id) ids.add(dst);
      if (dst === id) ids.add(src);
    }
    return (bp.nodes || []).filter((n) => ids.has(n.id));
  }

  function renderNoeud() {
    ctx.docbar.setViews([], null);
    if (!graphAvailable || !state.blueprint || !state.selectedNodeId) {
      root.replaceChildren(ctx.empty('Concevoir — Nœud', 'Aucun nœud sélectionné : revenez au niveau Workflow et choisissez-en un.', ''));
      return;
    }
    const bp = state.blueprint;
    const node = (bp.nodes || []).find((n) => n.id === state.selectedNodeId);
    if (!node) { root.replaceChildren(ctx.empty('Concevoir — Nœud', 'Ce nœud a disparu du blueprint.', '')); return; }
    const neighbors = neighborsOf(bp, node.id);
    const neighborEdges = (bp.edges || []).filter((e) => nodeId(e.from) === node.id || nodeId(e.to) === node.id);
    const pos = layoutNodes(neighbors, neighborEdges);
    const graph = el('div', { class: 'cv-graph' });
    graph.append(renderEdgesSvg(neighbors, neighborEdges, pos));
    for (const n of neighbors) {
      const p = pos.get(n.id) || { x: 0, y: 0 };
      graph.append(el(
        'div',
        { class: 'cv-node' + (n.id === node.id ? '' : ' dim'), 'aria-selected': String(n.id === node.id), style: `left:${p.x}px; top:${p.y}px` },
        el('div', { class: 'kind' }, n.role || n.kind || 'nœud'),
        el('div', { class: 'label', text: n.label || n.id }),
        el('div', { class: 'ref mono', text: n.ref || '(sans référence)' }),
      ));
    }
    root.replaceChildren(el('div', { class: 'cv-noeud' }, graph));
    renderNodeInspector(node);
  }

  function renderNodeInspector(node) {
    const tabs = ['proprietes', 'validation', 'cout', 'preuves'];
    const labels = { proprietes: 'Propriétés', validation: 'Validation', cout: 'Coût', preuves: 'Preuves' };
    const activeTab = ctx.inspector._cvTab && tabs.includes(ctx.inspector._cvTab) ? ctx.inspector._cvTab : 'proprietes';

    const tabsRow = el('div', { class: 'cv-tabs' });
    const body = el('div', { class: 'cv-tab-body' });
    for (const id of tabs) {
      const button = el('button', { type: 'button', class: 'cv-tab', 'aria-selected': String(id === activeTab), text: labels[id] });
      button.addEventListener('click', () => { ctx.inspector._cvTab = id; renderNodeInspector(node); });
      tabsRow.append(button);
    }

    if (activeTab === 'proprietes') {
      body.append(el('dl', { class: 'cv-kv' },
        el('dt', {}, 'Identifiant'), el('dd', { class: 'mono', text: node.id }),
        el('dt', {}, 'Genre'), el('dd', { text: node.kind || '—' }),
        el('dt', {}, 'Rôle'), el('dd', { text: node.role || '—' }),
        el('dt', {}, 'Référence'), el('dd', { class: 'mono', text: node.ref || '—' }),
        el('dt', {}, term('equipe', 'Équipe')), el('dd', { text: node.team || '—' }),
      ));
      if (node.description) body.append(el('p', { class: 'cv-msg', text: node.description }));
      body.append(el('div', { class: 'cv-json', text: JSON.stringify(node.config || {}, null, 2) }));
    } else if (activeTab === 'validation') {
      const nodeLint = lintForNode(node.id);
      if (!nodeLint.errors.length && !nodeLint.warnings.length) {
        body.append(el('p', { class: 'cv-msg' }, dotWord('ok', 'aucun problème rapporté pour ce nœud')));
      }
      for (const message of nodeLint.errors) body.append(el('p', { class: 'cv-msg' }, dotWord('bad', message)));
      for (const message of nodeLint.warnings) body.append(el('p', { class: 'cv-msg' }, dotWord('warn', message)));
    } else if (activeTab === 'cout') {
      const pressure = pressureForNode(node.id);
      if (!pressure) {
        body.append(el('p', { class: 'cv-msg' }, "Simulez le workflow pour estimer la pression de contexte de ce nœud."));
      } else {
        body.append(
          dotWord(pressure.verdict === 'ok' ? 'ok' : pressure.verdict === 'warn' ? 'warn' : 'bad', pressure.verdict),
          el('p', { class: 'cv-msg', text: `${pressure.estimatedTokens} tokens estimés — ${pressure.windowPct}% de la fenêtre.` }),
        );
      }
    } else if (activeTab === 'preuves') {
      const step = (state.simulate?.steps || []).find((s) => s.id === node.id);
      const requirements = step?.requirements || [];
      if (!requirements.length) {
        body.append(el('p', { class: 'cv-msg' }, "Aucune preuve exigée par le pattern de ce nœud à la porte suivante."));
      } else {
        const chips = el('div', { class: 'cv-chips' });
        for (const req of requirements) chips.append(dotWord('warn', req));
        body.append(el('p', { class: 'cv-msg' }, term('porte-de-preuve', 'Preuves exigées à la porte suivante :')), chips);
      }
    }

    ctx.inspector.replaceChildren(
      el('h3', { style: 'margin:0 0 8px', text: node.label || node.id }),
      tabsRow, body,
    );
  }

  // ── Rendu principal ──────────────────────────────────────────────────────

  function render() {
    if (aborted()) return;
    ctx.docbar.setZoom(zoomLevels, state.zoom, setZoom);
    ctx.docbar.setBreadcrumb([
      ctx.host.project || 'projet', 'Concevoir',
      ...(state.zoom !== 'projet' && state.zoom !== 'flotte' && state.selectedId ? [state.selectedId] : []),
      ...(state.zoom === 'noeud' && state.selectedNodeId ? [state.selectedNodeId] : []),
    ]);
    if (state.zoom === 'flotte') { renderFlotte(); return; }
    if (state.zoom === 'projet') { renderProjet(); return; }
    if (state.zoom === 'workflow') { renderWorkflow(); return; }
    renderNoeud();
  }

  ctx.docbar.setZoom(zoomLevels, state.zoom, setZoom);

  // Le cockpit peut s'ouvrir sans `?project=` (pure vue de Flotte) : lire les
  // blueprints échoue alors, et c'est attendu — le niveau Flotte n'en a pas
  // besoin. On tente quand même le chargement ici (plutôt que seulement au
  // moment où l'utilisateur zoome sur Projet) pour que la Liste et le Board
  // soient déjà prêts dès qu'un projet est choisi.
  try {
    await loadContainers();
  } catch (error) {
    if (aborted()) return;
    if (state.zoom !== 'flotte') {
      root.append(ctx.empty('Concevoir', `Impossible de lire les blueprints du projet : ${error.message}`, 'grimoire blueprint list'));
      return;
    }
    state.containers = [];
  }
  if (aborted()) return;
  ctx.dock.echo('grimoire blueprint list');
  render();
}
