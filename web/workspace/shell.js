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
//   ctx.loading    (libellé, lignes?) → [squelette, texte pour lecteurs d'écran] à
//                  poser dans une zone qui attend ses données
//   ctx.openPanel  (id) → ouvre un panneau latéral (`explorer`, `inspector`,
//                  `evidence`) : épinglé au bureau, en tiroir sous 900 px
//   ctx.rail       `{ on(id, handler) }` : enregistre ce que fait le rail « 2 »
//                  (bibliothèque) tant que cet espace est monté ; remis à
//                  zéro par la coque à chaque changement d'espace
//   ctx.live       `{ on(handler) }` : appelé quand le pouls (live.js) annonce un
//                  changement du ledger ou des sessions ; l'espace recharge SES
//                  données. Remis à zéro à chaque changement d'espace.

import { api, boot, host } from './api.js';
import glossary from './glossary.js';
import { agoText, createLive, refreshSince } from './live.js';

// ── Les six espaces, dans l'ordre de la barre ───────────────────────────────

const SPACES = [
  { id: 'piloter',  label: 'Piloter',  icon: 'rocket', term: 'flotte' },
  { id: 'concevoir', label: 'Concevoir', icon: 'workflow', term: 'blueprint' },
  { id: 'executer',  label: 'Exécuter',  icon: 'bolt', term: 'tache' },
  { id: 'observer',  label: 'Observer',  icon: 'microscope', term: 'trace' },
  { id: 'memoire',   label: 'Mémoire',   icon: 'brain', term: 'memoire' },
  { id: 'source',    label: 'Source',    icon: 'branch', term: 'etage' },
];

// Rail : 1 explorateur, 2 bibliothèque, 3 preuves, 4 inspecteur, 5 dock.
const RAIL = [
  { key: '1', id: 'explorer',  label: 'Explorateur', term: 'explorateur',
    icon: 'i-ui-panel-left' },
  { key: '2', id: 'library',   label: 'Bibliothèque', term: 'pattern',
    icon: 'i-space-savoir' },
  { key: '3', id: 'evidence',  label: 'Preuves', term: 'evidence-pack',
    icon: 'i-ui-files' },
  { key: '4', id: 'inspector', label: 'Inspecteur', term: 'inspecteur',
    icon: 'i-ui-panel-right' },
  { key: '5', id: 'dock',      label: 'Dock', term: 'dock',
    icon: 'i-ui-dock' },
];

const DOCK_TABS = [
  { id: 'console', icon: 'terminal',  label: 'Console',  term: 'console' },
  { id: 'traces', icon: 'network',   label: 'Traces',   term: 'trace' },
  { id: 'timeline', icon: 'chart', label: 'Timeline', term: 'timeline' },
  { id: 'problemes', icon: 'alert', label: 'Problèmes', term: 'probleme' },
];

const PANELS = { explorer: 'panel-explorer', inspector: 'panel-inspector', evidence: 'panel-evidence' };

const $ = (id) => document.getElementById(id);
const root = document.documentElement;

// ── Icônes : le sprite icons.svg, figé jusqu'à sa bascule sur la famille ────
// du site (docs/assets/brand/icons.svg) ; tests/unit/
// test_workspace_icons.py échoue si un appel à icon() cite un symbole absent.
// Décoratif par défaut (`aria-hidden`) : le libellé voisin porte le sens.
// Avec `label`, l'icône porte elle-même le sens (`role="img"` + `aria-label`).

const SVG_NS = 'http://www.w3.org/2000/svg';

export function icon(name, label) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'ico');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `icons.svg#${name}`);
  svg.append(use);
  if (label) {
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', label);
  } else {
    svg.setAttribute('aria-hidden', 'true');
  }
  return svg;
}

/** Icône d'un état (point + mot reste la règle : l'icône double le mot). */
export const STATE_ICON = { ok: 'check', warn: 'alert', bad: 'cross', acc: 'info', '': 'info' };

/** Pastille d'état « icône + mot » : remplace l'ancien point coloré (`.dot`). */
export function stateIcon(cls) {
  const span = document.createElement('span');
  span.className = 'sico' + (cls ? ' ' + cls : '');
  span.append(icon(STATE_ICON[cls] ?? 'info'));
  return span;
}

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

// Le thème a trois états : `system` (suit prefers-color-scheme, y compris en
// direct), `light`, `dark`. `data-theme-pref` porte le choix, `data-theme` le
// thème RÉSOLU (toujours `dark` ou `light`) : tokens.css et les tests ne lisent
// que lui. Sans choix mémorisé, le défaut reste `dark` — celui que la coque a
// toujours eu ; `system` est un choix, pas un changement imposé.
const THEME_PREFS = ['system', 'light', 'dark'];
const colorScheme = window.matchMedia('(prefers-color-scheme: dark)');

function resolveTheme(pref) {
  if (pref === 'system') return colorScheme.matches ? 'dark' : 'light';
  return pref;
}

function themePref() {
  const saved = readState().theme;
  return THEME_PREFS.includes(saved) ? saved : 'dark';
}

function applyTheme() {
  const pref = themePref();
  root.dataset.themePref = pref;
  root.dataset.theme = resolveTheme(pref);
  for (const button of document.querySelectorAll('#st-theme [data-theme-pref]')) {
    button.setAttribute('aria-pressed', String(button.dataset.themePref === pref));
  }
}

function setThemePref(pref) {
  if (!THEME_PREFS.includes(pref)) return;
  writeState({ theme: pref });
  applyTheme();
}

// Le réglage « système » se suit en direct : changer le thème de l'OS pendant
// que la coque est ouverte la fait suivre, sans rechargement.
colorScheme.addEventListener('change', () => {
  if (themePref() === 'system') applyTheme();
});

function applyPreferences() {
  const saved = readState();
  root.dataset.density = saved.density || 'decouverte';
  root.dataset.focus = 'off';
  applyTheme();
  $('st-density').textContent =
    root.dataset.density === 'concentration' ? 'Concentration' : 'Découverte';
}

function toggleDensity() {
  const next = root.dataset.density === 'concentration' ? 'decouverte' : 'concentration';
  writeState({ density: next });
  applyPreferences();
  // Concentration replie tout ; Découverte réépingle les deux panneaux utiles.
  // Sous 900 px les panneaux sont des tiroirs qu'on ouvre à la main : la
  // densité ne les ouvre pas d'office.
  if (!isNarrow()) {
    setPanel('explorer', next === 'concentration' ? 'collapsed' : 'pinned');
    setPanel('inspector', next === 'concentration' ? 'collapsed' : 'pinned');
  }
  setDock(next === 'concentration' || isNarrow() ? 'collapsed' : 'pinned');
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

// Sous 900 px la coque tient en une colonne : les panneaux latéraux ne sont plus
// des colonnes mais des tiroirs en surimpression (shell.css), un seul à la fois,
// fermés au chargement. Les états `pinned` et `peek` s'y confondent : les deux
// ouvrent le tiroir. L'état n'y est pas mémorisé — un tiroir ouvert sur mobile
// ne doit pas rouvrir, épinglé, sur l'écran de bureau du même navigateur.
const narrowQuery = window.matchMedia('(max-width: 900px)');
function isNarrow() { return narrowQuery.matches; }

let drawerOpener = null;
let narrowDockSeen = false;

function syncDrawer() {
  const open = Object.keys(PANELS).some((id) => panelState(id) !== 'collapsed');
  $('middle').toggleAttribute('data-drawer', isNarrow() && open);
}

function panelKey(kind, id) {
  return `${kind}.${current || 'piloter'}.${id}`;
}

function panelState(id) {
  const el = $(PANELS[id]);
  return el ? el.dataset.state : 'collapsed';
}

// Contenu propre à un panneau, chargé quand il s'ouvre (peek ou pinned) —
// jamais quand il se referme. Un seul point d'accroche pour toute la coque :
// `setPanel` est appelé depuis quatre chemins différents (clic, survol,
// ⌘/Ctrl-clic, restauration par espace) et le panneau des Preuves (#534) doit
// se rafraîchir depuis chacun sans dupliquer la condition dans les quatre.
const PANEL_ON_OPEN = {};

function setPanel(id, state, { persist = true } = {}) {
  const el = $(PANELS[id]);
  if (!el) return;
  const narrow = isNarrow();
  // Un seul tiroir à la fois : ouvrir l'un referme les autres.
  if (narrow && state !== 'collapsed') {
    for (const other of Object.keys(PANELS)) {
      if (other !== id && panelState(other) !== 'collapsed') setPanel(other, 'collapsed', { persist: false });
    }
  }
  el.dataset.state = state;
  const pin = el.querySelector(`[data-pin="${id}"]`);
  if (pin) pin.setAttribute('aria-pressed', String(state === 'pinned'));
  const button = document.querySelector(`.rail-btn[data-panel="${id}"]`);
  if (button) {
    button.setAttribute('aria-pressed', String(state !== 'collapsed'));
    button.setAttribute('aria-expanded', String(state !== 'collapsed'));
  }
  if (persist && !narrow) writeState({ [panelKey('panel', id)]: state });
  syncDrawer();
  if (state !== 'collapsed') PANEL_ON_OPEN[id]?.();
}

/** Ouvre un panneau à la demande d'un espace (bouton d'un état vide, par exemple). */
function openPanel(id) {
  if (!PANELS[id]) return;
  if (isNarrow()) {
    setPanel(id, 'peek');
    enterDrawer(id, document.activeElement);
  } else {
    setPanel(id, 'pinned');
  }
}

function togglePanel(id) {
  const opening = panelState(id) === 'collapsed';
  setPanel(id, opening ? 'pinned' : 'collapsed');
  if (isNarrow()) {
    if (opening) enterDrawer(id, document.querySelector(`.rail-btn[data-panel="${id}"]`));
    else leaveDrawer();
  }
}

/** Le focus entre dans le tiroir qui vient de s'ouvrir (bouton « Fermer »), et
 * on retient d'où l'on vient pour y revenir à la fermeture. */
function enterDrawer(id, opener) {
  drawerOpener = opener || document.activeElement;
  $(PANELS[id]).querySelector('.panel-close')?.focus();
}

/** Ferme tout tiroir ouvert et rend le focus à l'élément qui l'avait ouvert. */
function leaveDrawer() {
  let wasOpen = false;
  for (const id of Object.keys(PANELS)) {
    if (panelState(id) !== 'collapsed') { wasOpen = true; setPanel(id, 'collapsed', { persist: false }); }
  }
  if (wasOpen && drawerOpener && drawerOpener.isConnected) drawerOpener.focus();
  drawerOpener = null;
  return wasOpen;
}

function setDock(state) {
  $('dock').dataset.state = state;
  const button = document.querySelector('.rail-btn[data-panel="dock"]');
  if (button) {
    button.setAttribute('aria-pressed', String(state !== 'collapsed'));
    button.setAttribute('aria-expanded', String(state !== 'collapsed'));
  }
  if (!isNarrow()) writeState({ [panelKey('panel', 'dock')]: state });
}

/** Découverte ouvre par défaut ; Concentration replie tout (spec §3.4). */
function defaultPanelState() {
  return root.dataset.density === 'concentration' || isNarrow() ? 'collapsed' : 'pinned';
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
  // Tiroirs : toujours fermés à l'arrivée dans un espace, quoi qu'ait mémorisé
  // le bureau (voir `narrowQuery`).
  if (isNarrow()) {
    for (const id of Object.keys(PANELS)) setPanel(id, 'collapsed', { persist: false });
    // Le dock, lui, garde ce que la personne en a fait d'un espace à l'autre :
    // replié à la première arrivée seulement.
    if (!narrowDockSeen) { setDock('collapsed'); narrowDockSeen = true; }
    return;
  }
  setPanel('explorer', saved[panelKey('panel', 'explorer')] || fallback);
  setPanel('inspector', saved[panelKey('panel', 'inspector')] || fallback);
  // Preuves (#534) : jamais ouvert par défaut (ce n'est pas un des « deux
  // panneaux utiles » que la densité Découverte réépingle) — seulement s'il
  // a été explicitement ouvert dans cet espace, pour ce projet, auparavant.
  setPanel('evidence', saved[panelKey('panel', 'evidence')] || 'collapsed');
  setDock(saved[panelKey('panel', 'dock')] || 'pinned');
  restoreWidth('explorer');
  restoreWidth('inspector');
  restoreWidth('evidence');
}

// ── Panneau « Preuves » (rail « 3 », touche 3) ──────────────────────────────
//
// Possédé par la coque, pas par un espace (contrairement à « 2 »/bibliothèque,
// propre à Concevoir) : le rail promettait « Preuves » sur les six espaces,
// mais aucun ne l'enregistrait — bouton et raccourci restaient sans effet
// (#534). Liste les tâches du standard gouverné (`_grimoire/standard/
// task-board.yaml`), l'état de leurs gates et le chemin de leur pack ;
// jamais une relecture parallèle des fichiers, seulement
// `GET /api/workspace/evidence`, qui rejoue les mêmes fonctions que
// `grimoire standard verify`/`grimoire_standard_gate`.
let evidenceRequest = 0;

async function loadEvidencePanel() {
  const body = $('evidence-body');
  const requestId = ++evidenceRequest;
  body.replaceChildren(...loadingNodes('Chargement des preuves…', 3));
  body.setAttribute('aria-busy', 'true');
  let payload;
  try {
    payload = await api.evidence();
  } catch (error) {
    if (requestId !== evidenceRequest) return;
    body.setAttribute('aria-busy', 'false');
    body.replaceChildren(empty('Preuves indisponibles', error.message, 'grimoire standard doctor', {
      label: 'Réessayer', run: loadEvidencePanel,
    }));
    return;
  }
  body.setAttribute('aria-busy', 'false');
  if (requestId !== evidenceRequest) return;
  renderEvidencePanel(body, payload);
}

function renderEvidencePanel(body, payload) {
  body.replaceChildren();
  if (!payload.enrolled) {
    body.append(empty(
      'Preuves',
      "Ce projet n'a pas encore de standard agentique : aucune tâche, aucune enveloppe, aucun gate à montrer.",
      'grimoire standard init',
    ));
    return;
  }
  if (!payload.tasks || !payload.tasks.length) {
    body.append(empty(
      'Preuves',
      'Le standard est initialisé mais le board ne porte encore aucune tâche.',
      'grimoire standard init',
    ));
    return;
  }
  for (const task of payload.tasks) {
    const cls = task.gates.ok ? 'ok' : (task.gates.missing.length ? 'bad' : 'warn');
    const text = task.gates.ok
      ? 'validé'
      : (task.gates.missing.length ? `${task.gates.missing.length} manquant(s)` : 'avertissement');
    const row = mk(
      'button',
      { type: 'button', class: 'ev-row' },
      mk('span', { class: `dot ${cls}` }),
      mk('span', { class: 'ev-id mono', text: task.task_id }),
      mk('span', { class: 'ev-title', text: task.title || task.status || '' }),
      mk('span', { class: 'grow' }),
      mk('span', { class: `chip pill ${cls}` }, icon(STATE_ICON[cls] ?? 'info'), text),
    );
    row.title = task.pack_exists
      ? task.pack_path
      : `${task.pack_path} (pas encore écrit)`;
    row.addEventListener('click', () => goto('source', { file: task.pack_path }));
    body.append(row);
  }
}

PANEL_ON_OPEN.evidence = loadEvidencePanel;

/** Petit constructeur DOM local — shell.js n'importe rien de `spaces/*.js`
 * (README : aucun espace ne touche le DOM d'un autre, et ça vaut à l'envers).
 * Nommé `mk` (pas `el`) : plusieurs fonctions du fichier ont déjà une
 * variable locale `el` pour l'élément d'un panneau — même nom, même fichier,
 * aurait masqué l'une des deux à chaque lecture.
 * `mk(tag, attrs, ...children)` : `attrs.text` pose `textContent`, le reste
 * pose des attributs ; `null`/`undefined` parmi les enfants est ignoré. */
function mk(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

// ── Onglets accessibles ─────────────────────────────────────────────────────
//
// `role="tablist"` / `role="tab"` / `aria-selected`, un seul onglet dans l'ordre
// de tabulation (le courant ; les autres s'atteignent aux flèches), gauche/droite
// avec rebouclage, Début/Fin. `activate: 'auto'` active l'onglet à la flèche
// (dock : le contenu est local) ; `'manual'` ne déplace que le focus et laisse
// Entrée/Espace activer (espaces : monter un espace coûte des requêtes).

function bindTablist(list, { activate }) {
  list.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...list.querySelectorAll('[role="tab"]')].filter((tab) => !tab.disabled);
    const index = tabs.indexOf(event.target.closest('[role="tab"]'));
    if (index < 0) return;
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    event.preventDefault();
    tabs[next].focus();
    if (activate === 'auto') tabs[next].click();
  });
}

function markSelectedTab(list, selected) {
  for (const tab of list.querySelectorAll('[role="tab"]')) {
    const on = tab === selected;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
  }
}

/** Ramène l'onglet choisi dans la zone visible d'une barre d'onglets qui défile. */
function revealTab(list, tab) {
  if (!tab || list.scrollWidth <= list.clientWidth) return;
  const left = tab.offsetLeft;
  const right = left + tab.offsetWidth;
  if (left < list.scrollLeft) list.scrollLeft = left;
  else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth;
}

// ── Chargement : squelettes et zones de données ─────────────────────────────
//
// Une zone qui se remplit montre un squelette (la forme de ce qui vient) plutôt
// qu'un cadre vide ; le texte pour les lecteurs d'écran est séparé du squelette,
// qui reste décoratif (`aria-hidden`). Les nœuds posés par la coque portent
// `data-shell-placeholder` : dès que quelqu'un d'autre écrit dans la zone, ils
// disparaissent seuls (`watchPlaceholders`) — aucun espace n'a à les connaître.

const PLACEHOLDER = 'data-shell-placeholder';

// `cover` : le squelette de la toile recouvre la zone (position absolue) au lieu
// de s'y ajouter, pour ne pas déplacer ce qu'un espace y pose pendant qu'il charge.
function loadingNodes(label, rows = 4, { cover = false } = {}) {
  const bars = mk('div', {
    class: cover ? 'skeleton skeleton-cover' : 'skeleton', 'aria-hidden': 'true', [PLACEHOLDER]: 'loading',
  });
  for (let i = 0; i < rows; i += 1) bars.append(mk('div', { class: 'skeleton-line' }));
  return [bars, mk('p', { class: 'sr-only', [PLACEHOLDER]: 'loading', text: label })];
}

// Une enveloppe vide (un espace pose son conteneur avant d'avoir les données) ne
// compte pas comme du contenu : `subtree` rattrape le moment où elle se remplit.
function watchPlaceholders(host) {
  const own = `:scope > [${PLACEHOLDER}]`;
  new MutationObserver(() => {
    const placeholders = host.querySelectorAll(own);
    if (!placeholders.length) return;
    const filled = [...host.children].some(
      (child) => !child.hasAttribute(PLACEHOLDER) && (child.children.length || child.textContent.trim()),
    );
    if (filled) for (const node of placeholders) node.remove();
  }).observe(host, { childList: true, subtree: true, characterData: true });
}

function clearLoading(host) {
  for (const node of host.querySelectorAll(`[${PLACEHOLDER}="loading"]`)) node.remove();
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
    button.title = `${item.label} (${item.key})`;
    const controls = PANELS[item.id] || (item.id === 'dock' ? 'dock' : null);
    if (controls) button.setAttribute('aria-controls', controls);
    button.append(icon(item.icon), mk('span', { class: 'rail-lbl', text: item.label }));
    // Clic sur l'icône du rail : ouvre en surimpression (spec §3.1) ; un
    // second clic referme. ⌘/Ctrl + clic épingle directement dans la grille,
    // sans passer par l'entrouvert — le même geste que le cadenas du panneau.
    button.addEventListener('click', (event) => {
      clearTimeout(peekTimer);
      if (item.id === 'dock') { setDock($('dock').dataset.state === 'collapsed' ? 'pinned' : 'collapsed'); return; }
      if (!PANELS[item.id]) { triggerRailAction(item.id, event); return; }
      if (isNarrow()) {
        // Tiroir : le bouton ouvre ou referme ; ⌘/Ctrl n'a pas de sens sans grille.
        if (panelState(item.id) === 'collapsed') {
          setPanel(item.id, 'peek');
          enterDrawer(item.id, button);
        } else {
          setPanel(item.id, 'collapsed');
          leaveDrawer();
        }
        return;
      }
      if (event.metaKey || event.ctrlKey) {
        setPanel(item.id, panelState(item.id) === 'pinned' ? 'collapsed' : 'pinned');
        return;
      }
      setPanel(item.id, panelState(item.id) === 'collapsed' ? 'peek' : 'collapsed');
    });
    // Survol du rail, 450 ms : entrouvre en surimpression. Jamais au survol du contenu.
    button.addEventListener('pointerenter', () => {
      if (isNarrow() || !PANELS[item.id] || panelState(item.id) !== 'collapsed') return;
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
    $(elementId).querySelector(`[data-close="${id}"]`)?.addEventListener('click', () => leaveDrawer());
  }
  // Un clic hors d'un panneau entrouvert le referme ; un panneau épinglé reste.
  // Sous 900 px il n'y a pas d'épinglage : tout tiroir ouvert se referme.
  document.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.rail-btn')) return;
    if (isNarrow()) {
      // Un tiroir se ferme comme à Échap : le focus retourne au bouton du rail.
      const outside = Object.values(PANELS).some((elementId) => {
        const el = $(elementId);
        return el.dataset.state !== 'collapsed' && !el.contains(event.target);
      });
      if (outside) {
        // Sans cela le navigateur déplace le focus vers la cible juste après
        // l'appui et annule le retour au rail ; le clic qui suit reste émis.
        event.preventDefault();
        leaveDrawer();
      }
      return;
    }
    for (const [id, elementId] of Object.entries(PANELS)) {
      const el = $(elementId);
      if (el.dataset.state === 'peek' && !el.contains(event.target)) setPanel(id, 'collapsed');
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
    button.setAttribute('role', 'tab');
    button.id = `tab-dock-${tab.id}`;
    button.setAttribute('aria-controls', 'dock-body');
    button.dataset.dockTab = tab.id;
    button.dataset.term = tab.term;
    button.append(icon(tab.icon), mk('span', { class: 'tab-lbl', text: tab.label }));
    button.addEventListener('click', () => selectDockTab(tab.id));
    tabs.append(button);
  }
  tabs.setAttribute('aria-label', 'Dock');
  bindTablist(tabs, { activate: 'auto' });
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
  selectDockTab(dockTab);
}

function selectDockTab(id) {
  dockTab = id;
  markSelectedTab($('dock-tabs'), document.querySelector(`[data-dock-tab="${id}"]`));
  $('dock-body').setAttribute('aria-labelledby', `tab-dock-${id}`);
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

// `item.disabled` (+ `item.disabledReason` optionnel, en `title`) rend le
// bouton visible mais inactif — un espace vide (pas encore de Mission
// Ledger, pas encore de blueprint) garde ainsi ses vues affichées plutôt que
// de les faire disparaître par early-return.
function segment(el, items, active, onPick) {
  el.replaceChildren();
  el.hidden = !items.length;
  for (const item of items) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = item.label || item;
    button.dataset.value = item.id || item;
    button.setAttribute('aria-pressed', String((item.id || item) === active));
    if (item.disabled) {
      button.disabled = true;
      if (item.disabledReason) button.title = item.disabledReason;
    } else {
      button.addEventListener('click', () => onPick && onPick(button.dataset.value));
    }
    el.append(button);
  }
}

// ── Le bloc d'état vide : un seul, et il dit d'où viendra la donnée ─────────

// Une commande à recopier est une impasse sur mobile et un détour au bureau : si
// la Console du dock sait l'exécuter (liste blanche lue dans /commands, aucun
// argument à fournir, hôte non lecture seule), l'état vide porte un bouton qui
// la lance. `action` (`{ label, run }`) le remplace quand l'espace sait mieux.
let consoleCatalogue = new Map();

function consoleArgv(command) {
  if (!command || host.readOnly) return null;
  const words = command.trim().split(/\s+/);
  if (words[0] !== 'grimoire') return null;
  const rest = words.slice(1);
  for (const depth of [2, 1]) {
    const sub = consoleCatalogue.get(rest.slice(0, depth).join(' '));
    if (!sub) continue;
    const extra = rest.slice(depth);
    return sub.positionals === 0 && extra.every((word) => sub.flags.has(word)) ? rest : null;
  }
  return null;
}

function empty(title, sentence, command, action, { compact = false } = {}) {
  const block = document.createElement('div');
  block.className = compact ? 'empty empty-compact' : 'empty';
  const heading = document.createElement('h2');
  heading.append(icon('inbox'), mk('span', { text: title }));
  block.append(heading);
  const paragraph = document.createElement('p');
  paragraph.textContent = sentence;
  block.append(paragraph);
  if (command) {
    const code = document.createElement('code');
    code.textContent = command;
    block.append(code);
  }
  const argv = action ? null : consoleArgv(command);
  const resolved = action || (argv ? { label: 'Lancer dans la Console', run: () => runCommand(argv) } : null);
  if (resolved) {
    const button = mk('button', { type: 'button', class: 'btn', 'data-empty-action': '', text: resolved.label });
    button.addEventListener('click', () => resolved.run());
    block.append(mk('div', { class: 'empty-actions' }, button));
  }
  return block;
}

/** Ce que la coque dit quand un espace laisse l'explorateur ou l'inspecteur muets. */
function emptyPanelHint(which) {
  if (which === 'explorer') {
    return empty(
      "Pas d'arborescence ici",
      current === 'source'
        ? "Aucun fichier à parcourir pour l'instant."
        : "Cet espace n'a rien à parcourir à gauche. Les fichiers du projet, étage par étage, sont dans Source.",
      null,
      current === 'source' ? null : { label: 'Ouvrir Source', run: () => goto('source') },
      { compact: true },
    );
  }
  return empty(
    'Rien à inspecter',
    "Choisissez un élément dans la zone centrale : son détail s'affiche ici.",
    null,
    { label: 'Rechercher ou commander', run: () => openPalette() },
    { compact: true },
  );
}

/** Pose l'indication dans une zone latérale restée vide ; elle s'efface seule
 * dès que l'espace y écrit (`watchPlaceholders`). */
function fillEmptyPanels() {
  for (const [which, id] of [['explorer', 'explorer-body'], ['inspector', 'inspector-body']]) {
    const body = $(id);
    if (body.children.length) continue;
    const hint = emptyPanelHint(which);
    hint.setAttribute(PLACEHOLDER, 'hint');
    body.append(hint);
  }
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
    button.setAttribute('role', 'tab');
    button.id = `tab-space-${space.id}`;
    button.setAttribute('aria-controls', 'canvas');
    button.dataset.space = space.id;
    button.dataset.term = space.term;
    button.append(icon(space.icon), mk('span', { class: 'tab-lbl', text: space.label }));
    button.setAttribute('aria-selected', 'false');
    button.tabIndex = index === 0 ? 0 : -1;
    // Nom accessible stable : sous 900 px seul l'onglet courant garde son libellé visible.
    button.setAttribute('aria-label', space.label);
    button.title = `${space.label} — ⌘${index + 1}`;
    button.addEventListener('click', () => goto(space.id));
    nav.append(button);
  });
  bindTablist(nav, { activate: 'manual' });
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
  live.clear();
  restorePanelsForSpace();
  location.hash = '#' + space.id;
  // Identité d'espace (revue 2026-09) : shell.css en tire --id-current pour
  // le filet sous l'onglet actif et le liseré des en-têtes de panneau.
  // `data-active-space`, jamais `data-space` : ce dernier est réservé aux
  // boutons d'onglet (le sélecteur générique `[data-space]` juste en dessous
  // les recense pour poser `aria-selected` — poser aussi `data-space` sur
  // `<body>` le ferait matcher par ce même sélecteur).
  document.body.dataset.activeSpace = space.id;
  const tab = document.querySelector(`#spaces [data-space="${space.id}"]`);
  markSelectedTab($('spaces'), tab);
  revealTab($('spaces'), tab);
  const canvas = $('canvas');
  canvas.setAttribute('aria-labelledby', `tab-space-${space.id}`);
  canvas.setAttribute('aria-busy', 'true');
  canvas.replaceChildren(...loadingNodes(`Chargement de l'espace ${space.label}…`, 5, { cover: true }));
  canvas.dataset.grid = space.id === 'concevoir' ? 'on' : 'off';
  $('inspector-body').replaceChildren(...loadingNodes('Chargement du détail…', 3));
  $('explorer-body').replaceChildren(...loadingNodes("Chargement de l'arborescence…", 3));
  docbar.setBreadcrumb([host.project || host.status?.slug || 'projet', space.label]);
  docbar.setZoom([], null);
  docbar.setViews([], null);
  docbar.setValidation('');

  const ctx = {
    api, host, glossary, dock, docbar, empty, icon,
    canvas,
    inspector: $('inspector-body'),
    explorer: $('explorer-body'),
    signal: controller.signal,
    goto,
    openPanel,
    loading: (label, rows) => loadingNodes(label, rows),
    params: params || {},
    rail: { on: registerRailAction },
    live: { on: (handler) => live.on(handler) },
  };
  try {
    const module = await import(`./spaces/${space.id}.js`);
    await module.mount(canvas, ctx);
  } catch (error) {
    if (ctx.signal.aborted) return;
    clearLoading(canvas);
    canvas.append(empty(
      `L'espace ${space.label} n'a pas pu s'ouvrir`,
      String(error && error.message ? error.message : error),
      null,
      { label: 'Réessayer', run: () => goto(space.id, {}) },
    ));
  }
  // Un `goto()` plus récent a repris la main : il gère lui-même son chargement.
  if (ctx.signal.aborted) return;
  for (const area of [canvas, $('inspector-body'), $('explorer-body')]) clearLoading(area);
  canvas.setAttribute('aria-busy', 'false');
  fillEmptyPanels();
}

// ── Pouls : actualisation, compteur de décisions, horodatage ────────────────

// Le compteur de la barre du haut : les décisions que K6 met en attente d'un
// humain, de tout le registre sur le cockpit. Caché à zéro (rien à faire), mais
// un projet illisible reste dit : un compteur qui ignorerait ce qu'il ne peut
// pas lire mentirait par omission.
function renderDecisionsPill(decisions) {
  const pill = $('decisions-pill');
  const pending = Number(decisions && decisions.pending) || 0;
  const unreadable = (decisions && decisions.unreadable) || [];
  pill.hidden = pending === 0 && unreadable.length === 0;
  $('decisions-count').textContent = String(pending);
  $('decisions-lbl').textContent = pending > 1 ? ' décisions' : ' décision';
  let label = `${pending} décision${pending > 1 ? 's' : ''} en attente`;
  if (unreadable.length) label += ` — ${unreadable.length} projet(s) illisible(s) : ${unreadable.join(', ')}`;
  pill.setAttribute('aria-label', label);
  pill.title = `${label} — ouvrir la vue Décisions`;
  pill.dataset.pending = String(pending);
}

function openDecisions() {
  // Sur le cockpit le compteur est global : la vue s'ouvre sur tout le registre.
  return goto('executer', { view: 'decisions', ...(host.kind === 'cockpit' ? { scope: 'portfolio' } : {}) });
}

const live = createLive({
  onDecisions: renderDecisionsPill,
  onState: () => renderLiveStamp(),
});

// « mis à jour il y a 4 s » : écrit par le minuteur d'une seconde (aucune requête),
// donc vrai entre deux pouls, poussés ou sondés. Un onglet masqué ou une API muette se disent.
function renderLiveStamp() {
  const text = $('st-live-text');
  const box = $('st-live');
  const { status, lastOkAt, failingSince, intervalMs, transport } = live.state;
  box.dataset.state = status;
  // `pre` : ce qu'un petit écran délaisse (`.live-pre`) ; `tail` : ce qui reste, seul, sous 900 px.
  let pre = '';
  let tail = 'actualisation…';
  if (status === 'paused') {
    tail = 'suspendue';
    pre = 'actualisation ';
    box.title = "Onglet masqué : aucune requête n'est faite tant qu'il l'est.";
  } else if (status === 'error') {
    pre = 'API ';
    tail = `injoignable ${agoText(Date.now() - (failingSince || Date.now()))}`;
    box.title = transport === 'stream'
      ? "Le flux est coupé, reconnexion en cours ; l'affichage n'est plus garanti à jour."
      : "Le dernier sondage a échoué ; l'affichage n'est plus garanti à jour.";
  } else if (lastOkAt) {
    pre = 'mis à jour ';
    tail = agoText(Date.now() - lastOkAt);
    const seconds = Math.round(intervalMs / 1000);
    const at = new Date(lastOkAt).toLocaleTimeString('fr-FR');
    box.title = transport === 'stream'
      ? `Flux en direct, confirmé au moins toutes les ${seconds} s, dernier à ${at}`
      : `Sondage toutes les ${seconds} s, dernier à ${at}`;
  }
  const lead = document.createElement('span');
  lead.className = 'live-pre';
  lead.textContent = pre;
  text.replaceChildren(lead, document.createTextNode(tail));
}

let stampTimer = null;
function startStampTicker() {
  const tick = () => { renderLiveStamp(); refreshSince(document); };
  const arm = () => {
    if (stampTimer !== null) clearInterval(stampTimer);
    stampTimer = document.visibilityState === 'hidden' ? null : setInterval(tick, 1000);
    tick();
  };
  document.addEventListener('visibilitychange', arm);
  arm();
}

// ── Palette de commandes ────────────────────────────────────────────────────

let paletteItems = [];
let paletteIndex = 0;
// Ouverte depuis le raccourci ⌘K, la palette n'a pas de section prioritaire ;
// ouverte depuis le chip projet (#project-chip), « Projets » doit apparaître
// en premier — voir `openPalette()`.
let paletteSectionFirst = null;

// Ordre par défaut des sections titrées ; ne pas fusionner en une liste
// plate (30+ entrées sans repère) comme avant.
const PALETTE_SECTION_ORDER = ['Espaces', 'Commandes', 'Projets', 'Tâches', 'Workflows', 'Fichiers'];
const PALETTE_SECTION_CAP = 12;

// Chaque source alimente la palette indépendamment : une route absente ou en
// erreur (projet sans ledger, sans blueprint…) ne doit jamais vider les
// autres. `settle` réduit un Promise.allSettled à sa valeur, ou au repli.
function settle(result, fallback) {
  return result.status === 'fulfilled' ? result.value : fallback;
}

async function buildPalette() {
  const spaces = SPACES.map((s) => ({
    section: 'Espaces', label: s.label, hint: 'Espace', command: `grimoire serve # ${s.id}`,
    run: () => goto(s.id),
  }));

  const [commandsResult, projectsResult, blueprintsResult, tasksResult, filesResult] =
    await Promise.allSettled([api.commands(), api.projects(), api.blueprints(), api.tasks(), api.files()]);

  const catalogue = settle(commandsResult, {}).commands || [];
  consoleCatalogue = new Map(catalogue.map((c) => [c.key, { flags: new Set(c.flags || []), positionals: c.positionals || 0 }]));
  const commands = catalogue.map((c) => ({
    section: 'Commandes', label: c.command, hint: c.summary, command: c.command,
    run: () => runCommand(c.key.split(' ')),
  }));

  // Projets : seulement au cockpit — l'atelier sert un projet fixe, en
  // changer n'a pas de sens (spec §3.3 : « projets (/api/workspace… sur le
  // cockpit) »).
  const projects = host.kind === 'cockpit'
    ? (settle(projectsResult, {}).projects || []).map((p) => ({
        section: 'Projets', label: p.name || p.slug, hint: 'Projet', command: `grimoire cockpit serve # ${p.slug}`,
        run: () => { location.search = '?project=' + encodeURIComponent(p.slug); },
      }))
    : [];

  const workflows = (settle(blueprintsResult, [])).map((b) => ({
    section: 'Workflows', label: b.name || b.id, hint: 'Workflow', command: `grimoire blueprint validate # ${b.id}`,
    run: () => goto('concevoir'),
  }));

  // Tâches : `task show <id>` est une vraie sous-commande de la Console
  // (workspace_exec.ALLOWED) — sélectionner l'entrée l'exécute réellement,
  // au lieu de naviguer vers un espace encore vide (lot 4).
  const tasks = (settle(tasksResult, {}).tasks || []).map((t) => ({
    section: 'Tâches', label: t.title || t.id, hint: 'Tâche', command: `grimoire task show ${t.id}`,
    run: () => runCommand(['task', 'show', t.id]),
  }));

  const files = (settle(filesResult, {}).tiers || []).flatMap((tier) =>
    (tier.files || []).map((f) => ({
      section: 'Fichiers', label: f.path, hint: `Fichier · ${tier.label || tier.id}`, command: `grimoire # source ${f.path}`,
      // `goto('source', { file })` : sans le second argument, `shell.js`
      // écrasait `location.hash` avant que `source.js` n'ait vu le chemin —
      // la palette listait les fichiers mais n'en ouvrait jamais un seul.
      run: () => goto('source', { file: f.path }),
    })),
  ).slice(0, 500);

  paletteItems = [...spaces, ...commands, ...projects, ...workflows, ...tasks, ...files];
}

// `sectionFirst` : section à placer en tête (ex. « Projets » quand on ouvre
// depuis le chip projet, #project-chip). `null` garde l'ordre par défaut.
function openPalette(sectionFirst = null) {
  paletteSectionFirst = sectionFirst;
  $('palette').hidden = false;
  $('palette-input').value = '';
  $('palette-input').focus();
  renderPalette('');
}

function closePalette() {
  $('palette').hidden = true;
  paletteSectionFirst = null;
}

function paletteSectionOrder() {
  if (!paletteSectionFirst) return PALETTE_SECTION_ORDER;
  return [paletteSectionFirst, ...PALETTE_SECTION_ORDER.filter((s) => s !== paletteSectionFirst)];
}

// La palette était une liste plate de 30+ entrées sans repère (spaces,
// commandes, projets, workflows, tâches, fichiers concaténés) : on la
// regroupe désormais par section titrée, plafonnée par section pour qu'une
// section bavarde (Commandes) n'évince pas les autres (Projets, Tâches…).
function renderPalette(query) {
  const needle = query.trim().toLowerCase();
  const list = $('palette-list');
  list.replaceChildren();

  const bySection = new Map();
  for (const item of paletteItems) {
    if (needle && !(item.label + ' ' + item.hint).toLowerCase().includes(needle)) continue;
    if (!bySection.has(item.section)) bySection.set(item.section, []);
    bySection.get(item.section).push(item);
  }

  const matches = [];
  paletteIndex = 0;
  for (const section of paletteSectionOrder()) {
    const items = (bySection.get(section) || []).slice(0, PALETTE_SECTION_CAP);
    if (!items.length) continue;

    const header = document.createElement('li');
    header.role = 'presentation';
    header.className = 'palette-section';
    header.textContent = section;
    list.append(header);

    for (const item of items) {
      const index = matches.length;
      const li = document.createElement('li');
      li.role = 'option';
      li.setAttribute('aria-selected', String(index === 0));
      li.append(Object.assign(document.createElement('span'), { textContent: item.label }));
      li.append(Object.assign(document.createElement('span'), { className: 'lbl', textContent: item.hint }));
      li.append(Object.assign(document.createElement('span'), { className: 'cmd', textContent: item.command }));
      li.addEventListener('click', () => { closePalette(); item.run(); });
      list.append(li);
      matches.push(item);
    }
  }
  list._matches = matches;
}

function movePalette(delta) {
  const list = $('palette-list');
  // Les en-têtes de section (`role="presentation"`) ne sont pas des options :
  // seuls les `li[role="option"]` comptent pour la navigation clavier, dans
  // le même ordre que `list._matches`.
  const options = [...list.querySelectorAll('li[role="option"]')];
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

    if ($('palette').hidden && event.key === 'Escape' && isNarrow() && leaveDrawer()) {
      event.preventDefault();
      return;
    }

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
  for (const button of document.querySelectorAll('#st-theme [data-theme-pref]')) {
    button.addEventListener('click', () => setThemePref(button.dataset.themePref));
  }
  for (const area of [$('canvas'), $('inspector-body'), $('explorer-body')]) watchPlaceholders(area);
  // Passer le seuil des 900 px (rotation, redimensionnement) recompose les
  // panneaux : tiroirs fermés d'un côté, états mémorisés du bureau de l'autre.
  narrowQuery.addEventListener('change', () => {
    narrowDockSeen = false;
    if (current) restorePanelsForSpace();
    syncDrawer();
  });
  $('st-density').addEventListener('click', toggleDensity);
  $('palette-open').addEventListener('click', () => openPalette());
  $('decisions-pill').addEventListener('click', openDecisions);
  startStampTicker();
  // Le chip projet ressemblait à un sélecteur mais n'avait aucun
  // gestionnaire : un clic n'y faisait rien. Il ouvre la palette directement
  // sur la section Projets — le changement de projet reste un
  // `location.search = ?project=` (rechargement complet, voir `buildPalette`
  // ci-dessus), inchangé.
  $('project-chip').addEventListener('click', () => openPalette('Projets'));
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
      { label: 'Réessayer', run: () => location.reload() },
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
    // bonne clé après un clic (`setThemePref`, appelé après amorçage) mais
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
  //
  // Sans API il n'y a pas d'espace à monter : le bloc « API locale injoignable »
  // posé plus haut doit rester. Avant, `goto()` l'effaçait aussitôt pour afficher
  // l'erreur d'un espace qui n'avait aucune donnée.
  if (status) {
    // Le premier sondage AVANT le premier montage : l'empreinte de référence
    // précède les données de l'espace, donc un changement survenu pendant son
    // chargement est vu au sondage suivant au lieu d'être perdu.
    await live.start();
    await goto((location.hash || '#piloter').slice(1));
    window.addEventListener('hashchange', () => goto(location.hash.slice(1)));
  } else {
    restorePanelsForSpace();
  }

  // Surface de test : Playwright interroge la coque plutôt que ses détails.
  window.GrimoireWorkspace = {
    spaces: SPACES.map((s) => s.id),
    goto, host, glossary, dock, empty,
    panelState, setPanel,
    live: { state: live.state, openDecisions },
    get focus() { return root.dataset.focus; },
    get theme() { return root.dataset.theme; },
    get themePref() { return root.dataset.themePref; },
    get density() { return root.dataset.density; },
    get space() { return current; },
    get paletteOpen() { return !$('palette').hidden; },
  };
  document.body.dataset.ready = '1';
}

main();
