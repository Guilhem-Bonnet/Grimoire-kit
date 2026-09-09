// La coque : ce qui tient les six espaces ensemble.
//
// Possède la barre d'application, le rail, les trois états de panneau, le dock,
// la palette, les raccourcis, le thème et la densité. Ne possède AUCUN contenu
// d'espace : chaque espace est un module `spaces/<id>.js` qui exporte
// `mount(root, ctx)` et rend la fonction de démontage.
//
// Contrat passé aux espaces (`ctx`), figé par ce squelette :
//   ctx.api        le client de api.js, déjà ciblé sur le bon projet
//   ctx.host       { kind: 'atelier'|'cockpit', project, readOnly, status }
//   ctx.glossary   le glossaire chargé (get, open, missingTerms)
//   ctx.inspector  l'élément du panneau droit — l'espace le remplit
//   ctx.docbar     { setBreadcrumb, setZoom, setViews, setValidation }
//   ctx.dock       { log, setTab, echo } — `echo` affiche la commande équivalente
//   ctx.explorer   l'élément de l'arbre gauche
//   ctx.empty      (titre, phrase, commande) → le bloc d'état vide unique
//   ctx.signal     un AbortSignal annulé au démontage de l'espace
//   ctx.params     ce que l'appelant de `goto(id, params)` a demandé pour ce
//                  montage (ex. `{ file }` depuis ⌘K) — jamais persistant,
//                  relu à chaque montage seulement
//   ctx.rail       `{ on(id, handler) }` : enregistre ce que fait le rail « 2 »
//                  (bibliothèque) tant que cet espace est monté ; remis à
//                  zéro par la coque à chaque changement d'espace

import { api, boot, host } from './api.js';
import glossary from './glossary.js';

// ── Les six espaces, dans l'ordre de la barre ───────────────────────────────

const SPACES = [
  { id: 'piloter',  label: 'Piloter',  term: 'flotte' },
  { id: 'concevoir', label: 'Concevoir', term: 'blueprint' },
  { id: 'executer',  label: 'Exécuter',  term: 'tache' },
  { id: 'observer',  label: 'Observer',  term: 'trace' },
  { id: 'memoire',   label: 'Mémoire',   term: 'memoire' },
  { id: 'source',    label: 'Source',    term: 'etage' },
];

// Rail : 1 explorateur, 2 bibliothèque, 3 preuves, 4 inspecteur, 5 dock.
const RAIL = [
  { key: '1', id: 'explorer',  label: 'Explorateur', term: 'explorateur',
    path: 'M3 5.5h5l1.5 1.5H17v8H3z' },
  { key: '2', id: 'library',   label: 'Bibliothèque', term: 'pattern',
    path: 'M4 4h6v12H4zM10 4h6v12h-6z' },
  { key: '3', id: 'evidence',  label: 'Preuves', term: 'evidence-pack',
    path: 'M10 3l6 2v5c0 4-3 6-6 7-3-1-6-3-6-7V5z' },
  { key: '4', id: 'inspector', label: 'Inspecteur', term: 'inspecteur',
    path: 'M4 4h12v12H4zM4 8h12' },
  { key: '5', id: 'dock',      label: 'Dock', term: 'dock',
    path: 'M3 4h14v12H3zM3 11h14' },
];

const DOCK_TABS = [
  { id: 'console',  label: 'Console',  term: 'console' },
  { id: 'traces',   label: 'Traces',   term: 'trace' },
  { id: 'timeline', label: 'Timeline', term: 'timeline' },
  { id: 'problemes', label: 'Problèmes', term: 'probleme' },
];

const PANELS = { explorer: 'panel-explorer', inspector: 'panel-inspector' };

const $ = (id) => document.getElementById(id);
const root = document.documentElement;

// ── État mémorisé par espace et par projet (spec §3.1) ──────────────────────

const stateKey = () => `grimoire.workspace.${host.project || 'atelier'}`;

function readState() {
  try { return JSON.parse(localStorage.getItem(stateKey()) || '{}'); } catch { return {}; }
}

function writeState(patch) {
  try {
    localStorage.setItem(stateKey(), JSON.stringify({ ...readState(), ...patch }));
  } catch { /* navigation privée : la coque marche sans mémoire */ }
}

// ── Thème et densité ────────────────────────────────────────────────────────

function applyPreferences() {
  const saved = readState();
  root.dataset.theme = saved.theme || 'dark';
  root.dataset.density = saved.density || 'decouverte';
  root.dataset.focus = 'off';
  $('st-theme').textContent = root.dataset.theme === 'dark' ? 'Sombre' : 'Clair';
  $('st-density').textContent =
    root.dataset.density === 'concentration' ? 'Concentration' : 'Découverte';
}

function toggleTheme() {
  writeState({ theme: root.dataset.theme === 'dark' ? 'light' : 'dark' });
  applyPreferences();
}

function toggleDensity() {
  const next = root.dataset.density === 'concentration' ? 'decouverte' : 'concentration';
  writeState({ density: next });
  applyPreferences();
  // Concentration replie tout ; Découverte réépingle les deux panneaux utiles.
  setPanel('explorer', next === 'concentration' ? 'collapsed' : 'pinned');
  setPanel('inspector', next === 'concentration' ? 'collapsed' : 'pinned');
  setDock(next === 'concentration' ? 'collapsed' : 'pinned');
  selectDockTab(next === 'concentration' ? 'console' : 'traces');
}

function toggleFocus() {
  root.dataset.focus = root.dataset.focus === 'on' ? 'off' : 'on';
}

// ── Panneaux à trois états : collapsed, peek, pinned ────────────────────────
//
// L'état (et la largeur) est mémorisé par ESPACE et par PROJET (spec §3.1) :
// la clé porte `current`, l'identifiant de l'espace actif, jamais un nom de
// panneau seul. `current` est déjà affecté par `goto()` avant que quoi que ce
// soit ici ne s'exécute — voir `restorePanelsForSpace`.

let peekTimer = null;

function panelKey(kind, id) {
  return `${kind}.${current || 'piloter'}.${id}`;
}

function panelState(id) {
  const el = $(PANELS[id]);
  return el ? el.dataset.state : 'collapsed';
}

function setPanel(id, state) {
  const el = $(PANELS[id]);
  if (!el) return;
  el.dataset.state = state;
  const pin = el.querySelector(`[data-pin="${id}"]`);
  if (pin) pin.setAttribute('aria-pressed', String(state === 'pinned'));
  const button = document.querySelector(`.rail-btn[data-panel="${id}"]`);
  if (button) button.setAttribute('aria-pressed', String(state !== 'collapsed'));
  writeState({ [panelKey('panel', id)]: state });
}

function togglePanel(id) {
  setPanel(id, panelState(id) === 'collapsed' ? 'pinned' : 'collapsed');
}

function setDock(state) {
  $('dock').dataset.state = state;
  const button = document.querySelector('.rail-btn[data-panel="dock"]');
  if (button) button.setAttribute('aria-pressed', String(state !== 'collapsed'));
  writeState({ [panelKey('panel', 'dock')]: state });
}

/** Découverte ouvre par défaut ; Concentration replie tout (spec §3.4). */
function defaultPanelState() {
  return root.dataset.density === 'concentration' ? 'collapsed' : 'pinned';
}

/** Largeur : un panneau redimensionné à la souris (poignée) la garde, par
 * espace et par projet ; sinon la valeur par défaut de tokens.css s'applique. */
function restoreWidth(id) {
  const el = $(PANELS[id]);
  if (!el) return;
  const saved = readState()[panelKey('panelWidth', id)];
  el.style.width = saved ? saved + 'px' : '';
}

/** Rappelée par `goto()` à chaque changement d'espace, après que `current`
 * a été mis à jour : c'est ce qui rend l'état « par espace et par projet ». */
function restorePanelsForSpace() {
  const saved = readState();
  const fallback = defaultPanelState();
  setPanel('explorer', saved[panelKey('panel', 'explorer')] || fallback);
  setPanel('inspector', saved[panelKey('panel', 'inspector')] || fallback);
  setDock(saved[panelKey('panel', 'dock')] || 'pinned');
  restoreWidth('explorer');
  restoreWidth('inspector');
}

function buildRail() {
  const rail = $('rail');
  rail.replaceChildren();
  for (const item of RAIL) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rail-btn';
    button.dataset.panel = item.id;
    button.dataset.term = item.term;
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', `${item.label} (${item.key})`);
    button.innerHTML =
      `<svg class="ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="${item.path}"/></svg>` +
      `<span class="rail-lbl">${item.label}</span>`;
    // Clic sur l'icône du rail : ouvre en surimpression (spec §3.1) ; un
    // second clic referme. ⌘/Ctrl + clic épingle directement dans la grille,
    // sans passer par l'entrouvert — le même geste que le cadenas du panneau.
    button.addEventListener('click', (event) => {
      clearTimeout(peekTimer);
      if (item.id === 'dock') { setDock($('dock').dataset.state === 'collapsed' ? 'pinned' : 'collapsed'); return; }
      if (!PANELS[item.id]) { triggerRailAction(item.id, event); return; }
      if (event.metaKey || event.ctrlKey) {
        setPanel(item.id, panelState(item.id) === 'pinned' ? 'collapsed' : 'pinned');
        return;
      }
      setPanel(item.id, panelState(item.id) === 'collapsed' ? 'peek' : 'collapsed');
    });
    // Survol du rail, 450 ms : entrouvre en surimpression. Jamais au survol du contenu.
    button.addEventListener('pointerenter', () => {
      if (!PANELS[item.id] || panelState(item.id) !== 'collapsed') return;
      clearTimeout(peekTimer);
      peekTimer = setTimeout(() => setPanel(item.id, 'peek'), 450);
    });
    button.addEventListener('pointerleave', () => clearTimeout(peekTimer));
    rail.append(button);
    if (item.id === 'evidence') rail.append(Object.assign(document.createElement('div'), { className: 'grow' }));
  }
  for (const [id, elementId] of Object.entries(PANELS)) {
    $(elementId).querySelector(`[data-pin="${id}"]`)?.addEventListener('click', () => {
      setPanel(id, panelState(id) === 'pinned' ? 'collapsed' : 'pinned');
    });
  }
  // Un clic hors d'un panneau entrouvert le referme ; un panneau épinglé reste.
  document.addEventListener('pointerdown', (event) => {
    for (const [id, elementId] of Object.entries(PANELS)) {
      const el = $(elementId);
      if (el.dataset.state === 'peek' && !el.contains(event.target) && !event.target.closest('.rail-btn')) {
        setPanel(id, 'collapsed');
      }
    }
  });
}

// ── Redimensionnement à la poignée ───────────────────────────────────────────
//
// Seulement quand le panneau est épinglé dans la grille : en surimpression, il
// n'y a pas de grille à redimensionner (la poignée est masquée en CSS).

const PANEL_MIN_WIDTH = 180;
const PANEL_MAX_WIDTH = 480;

function bindResize() {
  for (const handle of document.querySelectorAll('.panel-resize')) {
    const id = handle.dataset.resize;
    const panel = handle.closest('.panel');
    if (!panel) continue;
    // Le panneau de gauche s'élargit vers la droite, celui de droite vers la
    // gauche : le signe du déplacement dépend du bord que la poignée tient.
    const sign = id === 'explorer' ? 1 : -1;
    handle.addEventListener('pointerdown', (event) => {
      if (panel.dataset.state !== 'pinned') return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = panel.getBoundingClientRect().width;
      handle.setPointerCapture(event.pointerId);
      const onMove = (moveEvent) => {
        const delta = (moveEvent.clientX - startX) * sign;
        const width = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, Math.round(startWidth + delta)));
        panel.style.width = width + 'px';
      };
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        const width = parseInt(panel.style.width, 10);
        if (Number.isFinite(width)) writeState({ [panelKey('panelWidth', id)]: width });
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
    });
  }
}

// ── Dock ────────────────────────────────────────────────────────────────────

const dockBuffers = Object.fromEntries(DOCK_TABS.map((t) => [t.id, []]));
let dockTab = 'traces';

function buildDock() {
  const tabs = $('dock-tabs');
  tabs.replaceChildren();
  for (const tab of DOCK_TABS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tab';
    button.role = 'tab';
    button.dataset.dockTab = tab.id;
    button.dataset.term = tab.term;
    button.textContent = tab.label;
    button.setAttribute('aria-selected', String(tab.id === dockTab));
    button.addEventListener('click', () => selectDockTab(tab.id));
    tabs.append(button);
  }
  tabs.append(Object.assign(document.createElement('div'), { className: 'grow' }));
  const echo = document.createElement('div');
  echo.className = 'row lbl';
  echo.id = 'dock-echo';
  echo.style.gap = '6px';
  tabs.append(echo);
  const kbdRow = document.createElement('div');
  kbdRow.className = 'row';
  kbdRow.style.marginLeft = '14px';
  const kbd = document.createElement('span');
  kbd.className = 'kbd';
  kbd.textContent = '5';
  kbdRow.append(kbd);
  tabs.append(kbdRow);
  renderDock();
}

function selectDockTab(id) {
  dockTab = id;
  for (const button of document.querySelectorAll('[data-dock-tab]')) {
    button.setAttribute('aria-selected', String(button.dataset.dockTab === id));
  }
  renderDock();
}

function renderDock() {
  const body = $('dock-body');
  body.replaceChildren();
  const lines = dockBuffers[dockTab] || [];
  if (!lines.length) {
    const empty = document.createElement('div');
    empty.className = 'lbl';
    empty.textContent = dockPlaceholder(dockTab);
    body.append(empty);
    return;
  }
  for (const line of lines.slice(-400)) {
    const row = document.createElement('div');
    if (line.startsWith('$ ')) {
      row.innerHTML = '<span class="prompt">$</span> ';
      row.append(line.slice(2));
    } else {
      row.textContent = line;
    }
    body.append(row);
  }
  body.scrollTop = body.scrollHeight;
}

function dockPlaceholder(id) {
  return {
    console: 'Tapez une sous-commande grimoire, ou ` pour le curseur.',
    traces: 'Aucune trace : le TraceLedger de ce projet est vide.',
    timeline: 'Sélectionnez une tâche dans Exécuter pour voir sa timeline.',
    problemes: 'Lancez le diagnostic (grimoire doctor) pour peupler cet onglet.',
  }[id] || '';
}

const dock = {
  log(tab, ...lines) {
    (dockBuffers[tab] ||= []).push(...lines.flatMap((l) => String(l).split('\n')));
    if (tab === dockTab) renderDock();
  },
  clear(tab) { dockBuffers[tab] = []; if (tab === dockTab) renderDock(); },
  setTab: selectDockTab,
  // Chaque action à la souris affiche sa commande équivalente (spec §3.3).
  echo(command) {
    const el = $('dock-echo');
    if (el) el.innerHTML = 'Commande équivalente : <span class="mono">' + escapeHtml(command) + '</span>';
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ── Barre de document ───────────────────────────────────────────────────────

const docbar = {
  setBreadcrumb(parts) {
    const el = $('breadcrumb');
    el.replaceChildren();
    parts.forEach((part, index) => {
      if (index) el.append(Object.assign(document.createElement('span'), { textContent: '›' }));
      const span = document.createElement('span');
      span.textContent = part;
      if (index === parts.length - 1) { span.style.color = 'var(--ink)'; span.style.fontWeight = '500'; }
      el.append(span);
    });
  },
  setZoom(levels, active, onPick) { segment($('zoom-seg'), levels, active, onPick); },
  setViews(views, active, onPick) { segment($('view-seg'), views, active, onPick); },
  setValidation(text, level) {
    const chip = $('doc-validation');
    chip.hidden = !text;
    if (!text) return;
    chip.querySelector('.dot').className = 'dot' + (level ? ' ' + level : '');
    chip.querySelector('span:last-child').textContent = text;
  },
};

function segment(el, items, active, onPick) {
  el.replaceChildren();
  el.hidden = !items.length;
  for (const item of items) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = item.label || item;
    button.dataset.value = item.id || item;
    button.setAttribute('aria-pressed', String((item.id || item) === active));
    button.addEventListener('click', () => onPick && onPick(button.dataset.value));
    el.append(button);
  }
}

// ── Le bloc d'état vide : un seul, et il dit d'où viendra la donnée ─────────

function empty(title, sentence, command) {
  const block = document.createElement('div');
  block.className = 'empty';
  const heading = document.createElement('h2');
  heading.textContent = title;
  block.append(heading);
  const paragraph = document.createElement('p');
  paragraph.textContent = sentence;
  block.append(paragraph);
  if (command) {
    const code = document.createElement('code');
    code.textContent = command;
    block.append(code);
  }
  return block;
}

// ── Routage des espaces ─────────────────────────────────────────────────────

let current = null;
let controller = null;

// ── Actions du rail sans panneau dédié ──────────────────────────────────────
//
// `explorer` et `inspector` sont des panneaux de la coque (PANELS). `dock` a
// sa propre bascule. `library` (rail « 2 ») n'appartient qu'à l'espace
// Concevoir : la spec §4 en fait un tiroir de la toile, pas un panneau de la
// coque. Plutôt que d'apprendre à `shell.js` ce qu'est une bibliothèque de
// nœuds (violerait le contrat `mount(root, ctx)`, README « aucun module
// d'espace n'écrit dans le DOM de l'autre » — et l'inverse est vrai aussi),
// l'espace monté enregistre ici ce que « 2 » doit faire tant qu'il est actif.
// Remis à zéro à chaque changement d'espace : un espace qui ne s'en sert pas
// laisse « 2 » sans effet, plutôt que de rejouer l'action d'un espace quitté.
let railActions = {};

function registerRailAction(id, handler) {
  railActions[id] = handler;
}

function triggerRailAction(id, event) {
  const handler = railActions[id];
  if (handler) handler(event);
}

function buildSpaces() {
  const nav = $('spaces');
  nav.replaceChildren();
  SPACES.forEach((space, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tab';
    button.dataset.space = space.id;
    button.dataset.term = space.term;
    button.textContent = space.label;
    button.setAttribute('aria-selected', 'false');
    button.title = `${space.label} — ⌘${index + 1}`;
    button.addEventListener('click', () => goto(space.id));
    nav.append(button);
  });
}

async function goto(id, params) {
  const space = SPACES.find((s) => s.id === id) || SPACES[0];
  // Un deuxième `goto()` vers l'espace déjà actif est un doublon SAUF s'il
  // porte des `params` (⌘K « ouvrir ce fichier de Source » alors que Source
  // est déjà monté) : sans l'exception, la palette perdrait silencieusement
  // la navigation quand l'espace ne change pas.
  if (current === space.id && !params) return;
  if (controller) controller.abort();
  controller = new AbortController();
  current = space.id;
  railActions = {};
  restorePanelsForSpace();
  location.hash = '#' + space.id;
  for (const button of document.querySelectorAll('[data-space]')) {
    button.setAttribute('aria-selected', String(button.dataset.space === space.id));
  }
  const canvas = $('canvas');
  canvas.replaceChildren();
  canvas.dataset.grid = space.id === 'concevoir' ? 'on' : 'off';
  $('inspector-body').replaceChildren();
  $('explorer-body').replaceChildren();
  docbar.setBreadcrumb([host.project || host.status?.slug || 'projet', space.label]);
  docbar.setZoom([], null);
  docbar.setViews([], null);
  docbar.setValidation('');

  const ctx = {
    api, host, glossary, dock, docbar, empty,
    canvas,
    inspector: $('inspector-body'),
    explorer: $('explorer-body'),
    signal: controller.signal,
    goto,
    params: params || {},
    rail: { on: registerRailAction },
  };
  try {
    const module = await import(`./spaces/${space.id}.js`);
    await module.mount(canvas, ctx);
  } catch (error) {
    canvas.append(empty(
      `L'espace ${space.label} n'a pas pu s'ouvrir`,
      String(error && error.message ? error.message : error),
      null,
    ));
  }
}

// ── Palette de commandes ────────────────────────────────────────────────────

let paletteItems = [];
let paletteIndex = 0;

// Chaque source alimente la palette indépendamment : une route absente ou en
// erreur (projet sans ledger, sans blueprint…) ne doit jamais vider les
// autres. `settle` réduit un Promise.allSettled à sa valeur, ou au repli.
function settle(result, fallback) {
  return result.status === 'fulfilled' ? result.value : fallback;
}

async function buildPalette() {
  const spaces = SPACES.map((s) => ({
    label: s.label, hint: 'Espace', command: `grimoire serve # ${s.id}`,
    run: () => goto(s.id),
  }));

  const [commandsResult, projectsResult, blueprintsResult, tasksResult, filesResult] =
    await Promise.allSettled([api.commands(), api.projects(), api.blueprints(), api.tasks(), api.files()]);

  const commands = (settle(commandsResult, {}).commands || []).map((c) => ({
    label: c.command, hint: c.summary, command: c.command,
    run: () => runCommand(c.key.split(' ')),
  }));

  // Projets : seulement au cockpit — l'atelier sert un projet fixe, en
  // changer n'a pas de sens (spec §3.3 : « projets (/api/workspace… sur le
  // cockpit) »).
  const projects = host.kind === 'cockpit'
    ? (settle(projectsResult, {}).projects || []).map((p) => ({
        label: p.name || p.slug, hint: 'Projet', command: `grimoire cockpit serve # ${p.slug}`,
        run: () => { location.search = '?project=' + encodeURIComponent(p.slug); },
      }))
    : [];

  const workflows = (settle(blueprintsResult, [])).map((b) => ({
    label: b.name || b.id, hint: 'Workflow', command: `grimoire blueprint validate # ${b.id}`,
    run: () => goto('concevoir'),
  }));

  // Tâches : `task show <id>` est une vraie sous-commande de la Console
  // (workspace_exec.ALLOWED) — sélectionner l'entrée l'exécute réellement,
  // au lieu de naviguer vers un espace encore vide (lot 4).
  const tasks = (settle(tasksResult, {}).tasks || []).map((t) => ({
    label: t.title || t.id, hint: 'Tâche', command: `grimoire task show ${t.id}`,
    run: () => runCommand(['task', 'show', t.id]),
  }));

  const files = (settle(filesResult, {}).tiers || []).flatMap((tier) =>
    (tier.files || []).map((f) => ({
      label: f.path, hint: `Fichier · ${tier.label || tier.id}`, command: `grimoire # source ${f.path}`,
      // `goto('source', { file })` : sans le second argument, `shell.js`
      // écrasait `location.hash` avant que `source.js` n'ait vu le chemin —
      // la palette listait les fichiers mais n'en ouvrait jamais un seul.
      run: () => goto('source', { file: f.path }),
    })),
  ).slice(0, 500);

  paletteItems = [...spaces, ...commands, ...projects, ...workflows, ...tasks, ...files];
}

function openPalette() {
  $('palette').hidden = false;
  $('palette-input').value = '';
  $('palette-input').focus();
  renderPalette('');
}

function closePalette() {
  $('palette').hidden = true;
}

function renderPalette(query) {
  const needle = query.trim().toLowerCase();
  const list = $('palette-list');
  list.replaceChildren();
  const matches = paletteItems
    .filter((item) => !needle || (item.label + ' ' + item.hint).toLowerCase().includes(needle))
    .slice(0, 40);
  paletteIndex = 0;
  matches.forEach((item, index) => {
    const li = document.createElement('li');
    li.role = 'option';
    li.setAttribute('aria-selected', String(index === 0));
    li.append(Object.assign(document.createElement('span'), { textContent: item.label }));
    li.append(Object.assign(document.createElement('span'), { className: 'lbl', textContent: item.hint }));
    li.append(Object.assign(document.createElement('span'), { className: 'cmd', textContent: item.command }));
    li.addEventListener('click', () => { closePalette(); item.run(); });
    list.append(li);
  });
  list._matches = matches;
}

function movePalette(delta) {
  const list = $('palette-list');
  const options = [...list.children];
  if (!options.length) return;
  paletteIndex = (paletteIndex + delta + options.length) % options.length;
  options.forEach((li, i) => li.setAttribute('aria-selected', String(i === paletteIndex)));
  options[paletteIndex].scrollIntoView({ block: 'nearest' });
}

// ── Console ─────────────────────────────────────────────────────────────────

async function runCommand(argv) {
  selectDockTab('console');
  setDock('pinned');
  dock.log('console', '$ grimoire ' + argv.join(' '));
  dock.echo('grimoire ' + argv.join(' '));
  try {
    const result = await api.run(argv);
    dock.log('console', result.output || '(aucune sortie)');
  } catch (error) {
    dock.log('console', 'refusé : ' + error.message);
  }
}

// ── Raccourcis ──────────────────────────────────────────────────────────────

function bindShortcuts() {
  document.addEventListener('keydown', (event) => {
    const meta = event.metaKey || event.ctrlKey;
    const typing = /^(INPUT|TEXTAREA)$/.test(event.target.tagName) || event.target.isContentEditable;

    if (meta && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return; }
    if (meta && event.shiftKey && event.key.toLowerCase() === 'f') { event.preventDefault(); toggleFocus(); return; }
    if (meta && /^[1-6]$/.test(event.key)) { event.preventDefault(); goto(SPACES[Number(event.key) - 1].id); return; }

    if (!$('palette').hidden) {
      if (event.key === 'Escape') { event.preventDefault(); closePalette(); }
      if (event.key === 'ArrowDown') { event.preventDefault(); movePalette(1); }
      if (event.key === 'ArrowUp') { event.preventDefault(); movePalette(-1); }
      if (event.key === 'Enter') {
        event.preventDefault();
        const item = ($('palette-list')._matches || [])[paletteIndex];
        closePalette();
        if (item) item.run();
      }
      return;
    }
    if (typing) return;

    // 1 à 5 : bascule des panneaux. Sans modificateur, comme un atelier 3D.
    if (/^[1-5]$/.test(event.key)) {
      const item = RAIL[Number(event.key) - 1];
      if (item.id === 'dock') setDock($('dock').dataset.state === 'collapsed' ? 'pinned' : 'collapsed');
      else if (PANELS[item.id]) togglePanel(item.id);
      else triggerRailAction(item.id, event);
      return;
    }
    if (event.key === '`') { event.preventDefault(); selectDockTab('console'); setDock('pinned'); }
  });
}

// ── Amorçage ────────────────────────────────────────────────────────────────

async function main() {
  applyPreferences();
  buildSpaces();
  buildRail();
  buildDock();
  bindResize();
  bindShortcuts();
  $('st-theme').addEventListener('click', toggleTheme);
  $('st-density').addEventListener('click', toggleDensity);
  $('palette-open').addEventListener('click', openPalette);
  $('palette').addEventListener('pointerdown', (e) => { if (e.target.id === 'palette') closePalette(); });
  $('palette-input').addEventListener('input', (e) => renderPalette(e.target.value));

  let status = null;
  try {
    status = await boot();
  } catch (error) {
    $('canvas').append(empty(
      'API locale injoignable',
      "Cette coque se sert elle-même : ouvrez-la depuis `grimoire serve` ou `grimoire cockpit serve`.",
      'grimoire serve',
    ));
  }

  if (status) {
    // `applyPreferences()` a déjà tourné une fois plus haut, avant que
    // `host.project` ne soit connu (pour éviter un flash de mauvais thème
    // pendant l'aller-retour réseau) — avec la clé de repli `atelier`. Tant
    // que `grimoire serve` servait toujours `project: null`, cette clé était
    // la même avant et après l'amorçage et le rejeu ci-dessous ne changeait
    // rien d'observable. Depuis #351/#356, `cockpit serve` résout un vrai
    // slug : sans ce second appel, thème et densité se lisaient sous la
    // bonne clé après un clic (`toggleTheme`, appelé après amorçage) mais
    // sous `atelier` après un rechargement — la préférence semblait perdue.
    applyPreferences();
    $('project-name').textContent = host.project || status.slug || '(projet servi)';
    $('project-dot').classList.add('ok');
    $('st-kit').textContent = 'grimoire-kit ' + (status.kitVersion || '—');
    $('st-api').textContent = 'API locale ' + location.host;
    $('st-host').textContent = host.readOnly ? 'cockpit · lecture seule' : (host.kind === 'cockpit' ? 'cockpit' : 'atelier');
    try {
      await glossary.load(api);
      glossary.attach(document);
    } catch { /* sans glossaire, pas d'infobulle — et pas d'invention */ }
    await buildPalette();
  }

  // L'état des panneaux (replié, entrouvert, épinglé, largeur) est restauré
  // par `goto()` lui-même, par espace et par projet — voir `restorePanelsForSpace`.
  await goto((location.hash || '#piloter').slice(1));
  window.addEventListener('hashchange', () => goto(location.hash.slice(1)));

  // Surface de test : Playwright interroge la coque plutôt que ses détails.
  window.GrimoireWorkspace = {
    spaces: SPACES.map((s) => s.id),
    goto, host, glossary, dock,
    panelState, setPanel,
    get focus() { return root.dataset.focus; },
    get theme() { return root.dataset.theme; },
    get density() { return root.dataset.density; },
    get space() { return current; },
    get paletteOpen() { return !$('palette').hidden; },
  };
  document.body.dataset.ready = '1';
}

main();
