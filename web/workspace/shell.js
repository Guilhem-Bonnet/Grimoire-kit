// La coque : ce qui tient les cinq espaces ensemble (P31).
//
// Possède la barre d'application, la portée, le rail, les trois états de
// panneau, le dock, la palette, les raccourcis, le thème et la densité. Ne
// possède AUCUN contenu d'espace : chaque espace regroupe une ou plusieurs
// rubriques, et chaque rubrique est un module `spaces/<module>.js` qui exporte
// `mount(root, ctx)` et rend la fonction de démontage (seules les Préférences
// sont rendues par la coque elle-même : ce sont SES réglages).
//
// L'URL dit tout : `?project=<slug>` ou `?scope=all` pour la portée, puis
// `#<espace>[/<rubrique>][?<élément>]` — un lien copié rouvre le même écran
// sur la même sélection.
//
// Contrat passé aux espaces (`ctx`), figé par ce squelette :
//   ctx.api        le client de api.js, déjà ciblé sur le bon projet
//   ctx.host       { kind: 'atelier'|'cockpit', project, readOnly, status }
//   ctx.scope      { all, project, name, set(slug|null, target?) } : la seule
//                  portée de la coque. `all` vrai = « Tous les projets » ; sinon
//                  `project` (slug, pour l'API) et `name` (pour l'écran, jamais
//                  le slug). `set` change la portée partout (rechargement)
//   ctx.setItem    (params) : l'élément sélectionné entre dans l'URL (lien
//                  profond), sans remonter la rubrique
//   ctx.glossary   le glossaire chargé (get, open, missingTerms)
//   ctx.inspector  l'élément du panneau de détail (à droite) — l'espace le remplit ;
//                  hors atelier (P62) il n'est affiché que s'il porte du contenu
//   ctx.docbar     { setBreadcrumb, setZoom, setViews, setValidation } — le fil
//                  d'Ariane reçoit seulement ce qui suit la portée et l'espace,
//                  que la coque pose elle-même
//   ctx.dock       { log, setTab, echo } — `echo` affiche la commande équivalente
//   ctx.explorer   l'élément de l'arbre gauche
//   ctx.empty      (titre, phrase, commande, action?, { art? }) → le bloc d'état
//                  vide unique ; `art` nomme une composition `i-empty-*` du sprite
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
import glossary, { FOURTH_SPACE } from './glossary.js';
import { agoText, createLive, refreshSince } from './live.js';

// Fin du premier montage d'espace : la première lecture du compteur d'attention
// l'attend, pour ne pas disputer le serveur au premier rendu (voir onPulseDecisions).
let markFirstSpaceMounted;
const firstSpaceMounted = new Promise((resolve) => { markFirstSpaceMounted = resolve; });

// ── Les cinq espaces, dans l'ordre de la barre, et leurs rubriques ──────────
//
// Un espace suit une intention (trier, travailler, maintenir, se souvenir,
// configurer), plus une source de données : ses rubriques montent les modules
// d'avant. `module: null` = rendu par la coque. Le nom du 4e espace vient de
// glossary.js, jamais d'ici.

const SPACES = [
  { id: 'a-traiter', label: 'À traiter', icon: 'i-space-a-traiter', term: 'a-traiter',
    sections: [{ id: 'a-traiter', module: 'a-traiter', label: 'À traiter' }] },
  { id: 'travail', label: 'Travail', icon: 'i-space-travail', term: 'tache',
    sections: [{ id: 'travail', module: 'executer', label: 'Tâches' }] },
  { id: 'flotte', label: 'Flotte', icon: 'i-space-flotte', term: 'flotte',
    sections: [{ id: 'flotte', module: 'piloter', label: 'Flotte' }] },
  { id: FOURTH_SPACE.id, label: FOURTH_SPACE.label, icon: FOURTH_SPACE.icon, term: 'memoire',
    sections: [
      { id: 'memoire', module: 'memoire', label: 'Mémoire' },
      { id: 'journal', module: 'observer', label: 'Journal' },
    ] },
  { id: 'configuration', label: 'Configuration', icon: 'i-space-configuration', term: 'configuration',
    // Atterrit sur « Hôtes et hooks » (maquette Configuration) : l'arbre de
    // fichiers est une rubrique parmi d'autres, pas l'écran d'arrivée.
    landing: 'hotes',
    sections: [
      { id: 'agents', module: 'configurer', label: 'Agents', count: (api) => api.agents().then((d) => (d.agents || []).length) },
      { id: 'fichiers', module: 'source', label: 'Fichiers' },
      { id: 'blueprints', module: 'concevoir', label: 'Blueprints', count: (api) => api.blueprintContainers().then((d) => d.count) },
      { id: 'hotes', module: 'configurer', label: 'Hôtes et hooks', count: (api) => api.hooks().then((d) => d.total) },
      { id: 'fournisseurs', module: 'configurer', label: 'Fournisseurs et budgets' },
      { id: 'preferences', module: null, label: 'Préférences' },
    ] },
];

const SECTIONS = new Map();
for (const space of SPACES) {
  for (const section of space.sections) SECTIONS.set(section.id, Object.assign(section, { space }));
}

// Les six identifiants d'avant (anciens liens, `ctx.goto('source', …)` des
// modules) mènent à la rubrique qui les a remplacés.
const LEGACY_SECTION = {
  piloter: 'flotte', executer: 'travail', observer: 'journal',
  memoire: 'memoire', source: 'fichiers', concevoir: 'blueprints',
};

/** Espace, rubrique ou ancien identifiant → la rubrique à monter. Un espace
 * rouvre la dernière rubrique vue dans cette session, sinon la première. */
function resolveSection(target) {
  const space = SPACES.find((s) => s.id === target);
  if (space) return SECTIONS.get(lastSection[space.id]) || SECTIONS.get(space.landing) || space.sections[0];
  return SECTIONS.get(target) || SECTIONS.get(LEGACY_SECTION[target]) || SPACES[0].sections[0];
}

const lastSection = {};

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

// ── Fin du point polysémique (P30) ──────────────────────────────────────────
// Un point ne porte plus qu'une présence ou une quantité : une catégorie prend
// son icône, une échelle ses barres, une identité son monogramme. Le mot reste
// toujours lisible (texte voisin ou `title`) : jamais la forme seule.

/** Icône de catégorie avec son libellé au survol ; `tone` (ok | warn | bad |
 *  acc) ne sert qu'aux glyphes qui disent un état (vivacité de session).
 *  `alone` : aucun mot voisin ne la double, l'icône porte alors le libellé
 *  pour les lecteurs d'écran. */
export function glyph(name, label, tone, { alone = false } = {}) {
  const span = mk('span', { class: 'glyph' + (tone ? ' ' + tone : '') });
  span.title = label;
  span.append(alone ? icon(name, label) : icon(name));
  return span;
}

/** Deux lettres d'un nom : initiales des deux premiers mots, sinon ses deux
 *  premières lettres (« Grimoire-Forge » → GF, « kit » → KI). Calculé ici,
 *  rien n'est demandé au serveur. */
export function monogramText(name) {
  const words = String(name || '').split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (!words.length) return '?';
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2);
  return letters.toUpperCase();
}

/** Monogramme d'une identité (projet) : remplace le point d'accent. Le nom
 *  complet suit en texte, le monogramme est donc décoratif. */
export function monogram(name) {
  const span = mk('span', { class: 'mgram', text: monogramText(name) });
  span.setAttribute('aria-hidden', 'true');
  return span;
}

/** Échelle ordinale en barres (1 à 3 pleines sur 3) : la priorité se lit « plus
 *  ou moins », ce qu'un point de couleur ne dit pas. `tone` = bad pour la plus
 *  haute marche (critique). */
export function levelBars(level, label, tone) {
  const span = mk('span', { class: 'bars' + (tone ? ' ' + tone : '') });
  span.dataset.level = String(level);
  span.setAttribute('role', 'img');
  span.setAttribute('aria-label', label);
  span.title = label;
  for (let i = 1; i <= 3; i += 1) span.append(mk('i', { class: i <= level ? 'on' : '' }));
  return span;
}

// ── Composants partagés des espaces (P21) ───────────────────────────────────
// Une seule implémentation chacun, stylée par shell.css : un espace les
// compose, il n'en recopie jamais le CSS sous son préfixe.

/** Badge d'état : `tone` ∈ ok | warn | bad | acc, neutre sinon. Le texte
 *  complet reste lisible au survol même quand l'ellipse le coupe. */
export function badge(label, tone, title) {
  const span = mk('span', { class: 'badge' + (tone ? ' ' + tone : ''), text: label });
  span.title = title || label;
  return span;
}

/** Dernière valeur rendue de chaque tuile, par rubrique et par identifiant de
 *  tuile (`item.id`, sinon le libellé ; deux tuiles de même libellé dans une
 *  rangée se distinguent par leur rang). Jamais vidée par `goto()` : revenir
 *  sur une rubrique compare à la dernière valeur VUE, donc n'anime que si elle a
 *  changé entre-temps ; le premier rendu d'une tuile n'anime jamais. */
const kpiSeen = new Map();

function kpiChanged(tile, value) {
  const key = `${current}|${tile}`;
  const before = kpiSeen.get(key);
  kpiSeen.set(key, value);
  if (before === undefined || before === value) return false;
  if (!/\d/.test(value)) return false; // un mot d'état ne « compte » pas
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Rangée de tuiles KPI : `items` = [{ value, label, id?, icon?, tone?, sub? }]. Une
 *  valeur sans chiffre (« absent », « verte ») est un statut, pas un
 *  indicateur : la tuile la pose en mot d'état (`data-kind="word"`). */
export function kpiRow(items) {
  const rowNode = mk('div', { class: 'kpi' });
  const rank = new Map();
  for (const item of items) {
    const value = String(item.value);
    const cell = mk('div', { class: 'kpi-item' });
    const base = item.id || item.label;
    const nth = rank.get(base) || 0;
    rank.set(base, nth + 1);
    const changed = kpiChanged(nth ? `${base}#${nth}` : base, value);
    if (item.tone) cell.dataset.tone = item.tone;
    if (!/\d/.test(value) && value !== '—') cell.dataset.kind = 'word';
    const lbl = mk('div', { class: 'kpi-lbl' }, item.icon ? icon(item.icon) : null, mk('span', { text: item.label }));
    const val = mk('div', { class: 'kpi-val', text: value });
    // P60 : un chiffre ne s'anime que si sa valeur a réellement changé depuis
    // le dernier rendu de la même tuile — une seule fois, jamais en boucle, et
    // pas du tout sous prefers-reduced-motion (l'attribut n'est même pas posé).
    if (changed) val.dataset.changed = 'true';
    val.title = value;
    cell.append(lbl, val);
    if (item.sub) cell.append(mk('div', { class: 'kpi-sub', text: item.sub }));
    rowNode.append(cell);
  }
  return rowNode;
}

/** En-tête d'espace : titre, sous-titre d'une ligne (facultatif), actions. */
export function spaceHead(title, sub, ...actions) {
  const head = mk('header', { class: 'space-head' },
    mk('div', null, mk('h2', { class: 'space-title', text: title }), sub ? mk('p', { class: 'space-sub', text: sub }) : null));
  const kept = actions.filter(Boolean);
  if (kept.length) head.append(mk('div', { class: 'space-head-actions' }, ...kept));
  return head;
}

/** Retour d'action sur place. Depuis P62 la Console n'est plus dans la coque :
 *  un refus envoyé à `ctx.dock` n'était lu par personne. L'espace pose cette
 *  zone à côté du bouton ; vide, elle ne prend aucune place. */
export function actionStatus(message, tone) {
  const box = mk('div', { class: 'action-status', role: 'status', 'aria-live': 'polite' });
  setActionStatus(box, message, tone);
  return box;
}

/** `tone` ∈ ok | bad, neutre sinon ; un message vide efface la zone. */
export function setActionStatus(box, message, tone) {
  if (!box) return;
  box.dataset.tone = (message && tone) || '';
  box.textContent = message || '';
}

/** Libellé d'un échec : « Échec » quand la requête n'a pas abouti (api.js pose
 *  alors `code: 0`), « Refusé » sinon — le serveur a répondu non. */
export function failureText(error) {
  if (error && error.payload && error.payload.readOnly) {
    return "Refusé : lecture seule — ce cockpit n'écrit que dans le projet qu'il sert. Lancez « grimoire cockpit serve » dans ce projet pour le modifier.";
  }
  const message = (error && error.message) || 'motif non communiqué';
  return error && error.code === 0 ? `Échec : ${message}` : `Refusé : ${message}`;
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
// que lui. Sans choix mémorisé, le défaut est `system` (P67) : la coque suit la
// préférence de l'OS ; un choix explicite du sélecteur reste prioritaire.
const THEME_PREFS = ['system', 'light', 'dark'];
const colorScheme = window.matchMedia('(prefers-color-scheme: dark)');

function resolveTheme(pref) {
  if (pref === 'system') return colorScheme.matches ? 'dark' : 'light';
  return pref;
}

// Avant `boot()`, le projet est inconnu et la clé par projet illisible : le
// premier rendu lit la copie non scopée (voir `mirrorPref`) — sinon la page
// peindrait le thème du système avant de basculer sur le choix mémorisé.
let hostKnown = false;

function savedPref(key) {
  const own = readState()[key];
  if (own || hostKnown) return own;
  try { return localStorage.getItem(`grimoire.workspace.${key}`); } catch { return null; }
}

function themePref() {
  const saved = savedPref('theme');
  return THEME_PREFS.includes(saved) ? saved : 'system';
}

// Copie non scopée du choix de thème et de contraste : le script inline de
// index.html la lit avant le premier paint (la clé par projet n'est connue
// qu'après `boot()`). Le dernier projet ouvert gagne.
function mirrorPref(key, value) {
  try { localStorage.setItem(`grimoire.workspace.${key}`, value); } catch { /* sans mémoire */ }
}

function applyTheme() {
  const pref = themePref();
  mirrorPref('theme', pref);
  root.dataset.themePref = pref;
  root.dataset.theme = resolveTheme(pref);
  // La barre d'état et la rubrique Préférences portent les mêmes boutons.
  for (const button of document.querySelectorAll('[data-theme-pref]')) {
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

// Contraste (P61) : `standard` ou `high` (7:1 sur tout texte, voir tokens.css).
// Sans choix mémorisé, la coque suit prefers-contrast: more du système.
const CONTRASTS = ['standard', 'high'];
const contrastQuery = window.matchMedia('(prefers-contrast: more)');

function contrastPref() {
  const saved = savedPref('contrast');
  if (CONTRASTS.includes(saved)) return saved;
  return contrastQuery.matches ? 'high' : 'standard';
}

function setContrast(next) {
  if (!CONTRASTS.includes(next)) return;
  writeState({ contrast: next });
  applyPreferences();
}

contrastQuery.addEventListener('change', () => {
  if (!CONTRASTS.includes(readState().contrast)) applyPreferences();
});

// Deux réglages, deux mots (P31) : la densité (confortable ou compacte) et le
// plein écran (⇧⌘F). Le mot « Concentration » désignait les deux à la fois.
// Les valeurs mémorisées avant P31 se relisent sous leur nouveau nom.
const DENSITIES = { confortable: 'confortable', compacte: 'compacte', decouverte: 'confortable', concentration: 'compacte' };
const DENSITY_LABEL = { confortable: 'confortable', compacte: 'compacte' };

function applyPreferences() {
  const saved = readState();
  root.dataset.density = DENSITIES[saved.density] || 'confortable';
  root.dataset.focus = root.dataset.focus === 'on' ? 'on' : 'off';
  root.dataset.atelier = saved.atelier === 'on' ? 'on' : 'off';
  $('st-atelier').setAttribute('aria-pressed', String(root.dataset.atelier === 'on'));
  applyTheme();
  root.dataset.contrast = contrastPref();
  mirrorPref('contrast', root.dataset.contrast);
  for (const button of document.querySelectorAll('[data-contrast-pref]')) {
    button.setAttribute('aria-pressed', String(button.dataset.contrastPref === root.dataset.contrast));
  }
  // « Densité : » s'efface sous 900 px (`.st-pre`) : la barre d'état y est serrée.
  $('st-density').replaceChildren(
    mk('span', null, mk('span', { class: 'st-pre', text: 'Densité : ' }), DENSITY_LABEL[root.dataset.density]),
  );
  $('st-density').setAttribute('aria-pressed', String(root.dataset.density === 'compacte'));
  $('st-fullscreen').setAttribute('aria-pressed', String(root.dataset.focus === 'on'));
  for (const button of document.querySelectorAll('[data-density-pref]')) {
    button.setAttribute('aria-pressed', String(button.dataset.densityPref === root.dataset.density));
  }
  for (const button of document.querySelectorAll('[data-fullscreen-pref]')) {
    button.setAttribute('aria-pressed', String(root.dataset.focus === 'on'));
  }
}

function setDensity(next) {
  if (!DENSITY_LABEL[next] || next === root.dataset.density) return;
  writeState({ density: next });
  applyPreferences();
  // Compacte replie tout ; confortable réépingle les deux panneaux utiles.
  // Sous 900 px les panneaux sont des tiroirs qu'on ouvre à la main : la
  // densité ne les ouvre pas d'office.
  if (!isNarrow()) {
    setPanel('explorer', next === 'compacte' ? 'collapsed' : 'pinned');
    setPanel('inspector', next === 'compacte' ? 'collapsed' : 'pinned');
  }
  setDock(next === 'compacte' || isNarrow() ? 'collapsed' : 'pinned');
  selectDockTab(next === 'compacte' ? 'console' : 'traces');
}

function toggleDensity() {
  setDensity(root.dataset.density === 'compacte' ? 'confortable' : 'compacte');
}

// ── Panneaux d'atelier (P62) ────────────────────────────────────────────────
//
// Par défaut la coque ne montre que la barre du haut, le contenu et, quand
// l'écran y met quelque chose, le panneau de détail (l'ancien Inspecteur). Rail,
// explorateur, inspecteur permanent, preuves et dock servent au diagnostic : ils
// restent là, derrière cette bascule (⇧⌘L, palette, pied de page), mémorisée par
// projet comme le thème. Sous 900 px la coque garde ses tiroirs : le rail y est
// la seule porte vers eux.
function setAtelier(on) {
  writeState({ atelier: on ? 'on' : 'off' });
  applyPreferences();
  fillEmptyPanels();
}

function toggleAtelier() {
  setAtelier(root.dataset.atelier !== 'on');
}

/** Un panneau latéral n'existe, hors atelier, que si l'écran y a posé du
 * contenu : les squelettes de chargement, l'indice vide de la coque et les
 * phrases « Choisissez un élément » ne comptent pas. Vaut pour le panneau de
 * détail (ancien Inspecteur) et pour l'arbre de la rubrique Fichiers, qui est
 * sa navigation et non un outil de diagnostic. */
function syncDetail() {
  for (const [bodyId, panelId] of [['inspector-body', 'panel-inspector'], ['explorer-body', 'panel-explorer']]) {
    const filled = [...$(bodyId).children].some((child) => (
      !child.hasAttribute(PLACEHOLDER)
      && !child.matches('p.lbl, p.cv-msg, .sr-only')
      && (child.children.length || child.textContent.trim())
    ));
    $(panelId).dataset.detail = filled ? 'on' : 'off';
  }
}

function toggleFocus() {
  root.dataset.focus = root.dataset.focus === 'on' ? 'off' : 'on';
  applyPreferences();
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
  return `${kind}.${current || 'flotte'}.${id}`;
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

/** Confortable ouvre par défaut ; compacte replie tout (spec §3.4). */
function defaultPanelState() {
  return root.dataset.density === 'compacte' || isNarrow() ? 'collapsed' : 'pinned';
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
  // panneaux utiles » que la densité confortable réépingle) — seulement s'il
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
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...list.querySelectorAll('[role="tab"]')].filter((tab) => !tab.disabled);
    const index = tabs.indexOf(event.target.closest('[role="tab"]'));
    if (index < 0) return;
    let next = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + tabs.length) % tabs.length;
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
    if (!el) return;
    if (!command) { el.replaceChildren(); return; }
    el.innerHTML = 'Commande équivalente : <span class="mono">' + escapeHtml(command) + '</span>';
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ── Barre de document ───────────────────────────────────────────────────────

// Le fil d'Ariane commence toujours par la portée (nom du projet, jamais son
// slug) puis l'espace, et la rubrique quand l'espace en a plusieurs : la coque
// les pose, un module n'ajoute que ce qui suit.
let crumbBase = [];

const docbar = {
  setBreadcrumb(parts) {
    const el = $('breadcrumb');
    el.replaceChildren();
    [...crumbBase, ...parts.filter(Boolean)].forEach((part, index, all) => {
      if (index) el.append(Object.assign(document.createElement('span'), { textContent: '›' }));
      const span = document.createElement('span');
      span.textContent = part;
      if (index === all.length - 1) { span.style.color = 'var(--ink)'; span.style.fontWeight = '500'; }
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
    // `item.count` (P33, « Sessions 4 ») : le compte suit le libellé, en encre secondaire.
    if (typeof item.count === 'number') {
      const count = document.createElement('span');
      count.className = 'seg-count';
      count.textContent = String(item.count);
      button.append(count);
    }
    button.dataset.value = item.id || item;
    // Un segment désactivé n'est jamais pressé : la vue active d'un espace
    // vide se lit à l'état vide de la toile, pas à un bouton inerte allumé.
    button.setAttribute('aria-pressed', String(!item.disabled && (item.id || item) === active));
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

// `art` : une composition d'état vide du sprite (`i-empty-*`, 96 × 64) posée
// au-dessus du titre, à la place de la petite icône de bac.
function empty(title, sentence, command, action, { compact = false, art = '' } = {}) {
  const block = document.createElement('div');
  block.className = compact ? 'empty empty-compact' : 'empty';
  if (art) {
    const scene = icon(art);
    scene.setAttribute('class', 'empty-art');
    block.dataset.art = art;
    block.append(scene);
  }
  const heading = document.createElement('h2');
  heading.append(...(art ? [] : [icon('inbox')]), mk('span', { text: title }));
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
      "Cet espace n'a rien à parcourir à gauche. Les fichiers du projet, étage par étage, sont dans Configuration › Fichiers.",
      null,
      current === 'fichiers' ? null : { label: 'Ouvrir les fichiers', run: () => goto('fichiers') },
      { compact: true },
    );
  }
  return empty(
    'Rien à inspecter',
    "Choisissez un élément dans la zone centrale : son détail s'affiche ici.",
    null,
    { label: 'Rechercher ou agir', run: () => openPalette() },
    { compact: true },
  );
}

/** Pose l'indication dans une zone latérale restée vide ; elle s'efface seule
 * dès que l'espace y écrit (`watchPlaceholders`). */
function fillEmptyPanels() {
  // Hors atelier (et hors tiroirs) ces panneaux sont masqués : leur indication
  // le serait aussi, et ne doit pas répondre à la place du contenu de l'écran.
  if (root.dataset.atelier !== 'on' && !isNarrow()) return;
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

function tabButton(id, label, iconName, term) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'tab';
  button.setAttribute('role', 'tab');
  button.setAttribute('aria-controls', 'canvas');
  button.setAttribute('aria-selected', 'false');
  button.tabIndex = -1;
  if (term) button.dataset.term = term;
  button.append(...(iconName ? [icon(iconName)] : []), mk('span', { class: 'tab-lbl', text: label }));
  return button;
}

function buildSpaces() {
  const nav = $('spaces');
  nav.replaceChildren();
  SPACES.forEach((space, index) => {
    const button = tabButton(space.id, space.label, space.icon, space.term);
    button.id = `tab-space-${space.id}`;
    button.dataset.space = space.id;
    button.tabIndex = index === 0 ? 0 : -1;
    // Nom accessible stable : sous 900 px seul l'onglet courant garde son libellé visible.
    button.setAttribute('aria-label', space.label);
    button.title = `${space.label} — ⌘${index + 1}`;
    button.addEventListener('click', () => goto(space.id));
    if (space.id === 'a-traiter') {
      // Le compteur de décisions vit sur l'onglet (maquette Douce), pas dans une pastille à part.
      const count = badge('0', 'warn');
      count.id = 'decisions-count';
      count.hidden = true;
      button.append(count);
    }
    nav.append(button);
  });
  bindTablist(nav, { activate: 'manual' });
  bindTablist($('section-tabs'), { activate: 'manual' });
}

/** Les rubriques de l'espace courant ; masquées quand il n'en a qu'une. */
/** Le compte d'une rubrique (« Agents 3 ») : lu une fois par portée, jamais
 * deviné. Une lecture qui échoue laisse l'étiquette sans nombre. */
const sectionCounts = new Map();

function fillSectionCount(item, button) {
  const show = (n) => {
    if (!Number.isFinite(n) || !button.isConnected) return;
    button.append(mk('span', { class: 'tab-count', text: String(n), 'data-count': item.id }));
    button.setAttribute('aria-label', `${item.label}, ${n}`);
  };
  if (!sectionCounts.has(item.id)) {
    sectionCounts.set(item.id, Promise.resolve(item.count(api)).catch(() => null));
  }
  sectionCounts.get(item.id).then(show);
}

function renderSectionTabs(space, section) {
  const list = $('section-tabs');
  list.replaceChildren();
  list.hidden = space.sections.length < 2;
  if (list.hidden) return;
  for (const item of space.sections) {
    const button = tabButton(item.id, item.label);
    button.id = `tab-section-${item.id}`;
    button.dataset.section = item.id;
    button.addEventListener('click', () => goto(item.id));
    list.append(button);
    if (item.count) fillSectionCount(item, button);
  }
  markSelectedTab(list, list.querySelector(`[data-section="${section.id}"]`));
}

// ── Lien profond : l'URL dit la portée, l'espace et l'élément ───────────────
//
// `#<espace>[/<rubrique>][?<clé>=<valeur>…]` ; la portée est dans la query
// (`?project=` / `?scope=all`), lue par api.js au chargement. Seuls les
// paramètres simples (texte, nombre, booléen) entrent dans l'URL : ce sont
// ceux qu'un module relit dans `ctx.params`.

let appliedHash = '';

function locationHash(section, params) {
  const space = section.space;
  let hash = '#' + space.id + (space.sections.length > 1 ? '/' + section.id : '');
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === null || value === undefined || value === '' || typeof value === 'object') continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${hash}?${text}` : hash;
}

function parseHash(hash) {
  const raw = String(hash || '').replace(/^#/, '');
  const [path, query = ''] = raw.split('?');
  const [first = '', second = ''] = path.split('/');
  const params = Object.fromEntries(new URLSearchParams(query));
  return { target: second || first || SPACES[0].id, params: Object.keys(params).length ? params : null };
}

function writeHash(hash, { push }) {
  appliedHash = hash;
  if (location.hash === hash) return;
  const url = location.pathname + location.search + hash;
  if (push) history.pushState(null, '', url);
  else history.replaceState(null, '', url);
}

/** L'URL a changé sans nous (précédent/suivant, lien collé) : la suivre. */
function followLocation() {
  if (location.hash === appliedHash) return;
  const { target, params } = parseHash(location.hash);
  // `{}` plutôt que rien : revenir de `#travail?task=…` à `#travail` doit
  // remonter la rubrique sans la sélection, pas être pris pour un doublon.
  goto(target, params || {}, { fromUrl: true });
}

/** `ctx.setItem` : la sélection d'un module entre dans l'URL, sans remontage. */
function setItem(params) {
  if (!current) return;
  currentParams = { ...(params || {}) };
  writeHash(locationHash(SECTIONS.get(current), currentParams), { push: false });
}

let currentParams = {};

/** Vrai si l'échange d'espace peut passer par une transition de vue : l'API
 *  existe ET le mouvement n'est pas réduit (le repli n'a aucune transition). */
function canViewTransition() {
  return typeof document.startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

async function goto(id, params, { fromUrl = false } = {}) {
  const section = resolveSection(id);
  const space = section.space;
  // Un deuxième `goto()` vers la rubrique déjà active est un doublon SAUF s'il
  // porte des `params` (⌘K « ouvrir ce fichier » alors que Fichiers est déjà
  // monté) : sans l'exception, la palette perdrait silencieusement la
  // navigation quand la rubrique ne change pas.
  if (current === section.id && !params) {
    writeHash(locationHash(section, currentParams), { push: false });
    return;
  }
  if (controller) controller.abort();
  controller = new AbortController();
  const previousSpace = current ? SECTIONS.get(current)?.space.id ?? null : null;
  current = section.id;
  currentParams = { ...(params || {}) };
  lastSection[space.id] = section.id;
  railActions = {};
  live.clear();
  restorePanelsForSpace();
  // Une navigation venue de l'URL ne la réécrit qu'à l'identique (forme
  // canonique d'un ancien `#piloter`) ; un clic, lui, crée une entrée
  // d'historique — « Précédent » ramène à l'espace d'avant.
  writeHash(locationHash(section, currentParams), { push: !fromUrl && Boolean(appliedHash) });
  // Identité d'espace (revue 2026-09) : shell.css en tire --id-current pour
  // le filet sous l'onglet actif et le liseré des en-têtes de panneau.
  // `data-active-space`, jamais `data-space` : ce dernier est réservé aux
  // boutons d'onglet (le sélecteur générique `[data-space]` juste en dessous
  // les recense pour poser `aria-selected` — poser aussi `data-space` sur
  // `<body>` le ferait matcher par ce même sélecteur). La teinte reste celle
  // du module monté (tokens.css), l'espace est porté à part.
  // P54 : changer d'ESPACE passe par la View Transitions API quand elle existe
  // et que le mouvement n'est pas réduit ; sinon, repli : le même échange, sans
  // transition. Seul l'échange synchrone de la coque est enveloppé — le montage
  // du module, lui, reste hors transition (il peut durer).
  const canvas = $('canvas');
  let swapped = false;
  const swapShell = () => {
    document.body.dataset.activeSpace = section.module || section.id;
    document.body.dataset.activeArea = space.id;
    const tab = document.querySelector(`#spaces [data-space="${space.id}"]`);
    markSelectedTab($('spaces'), tab);
    revealTab($('spaces'), tab);
    renderSectionTabs(space, section);
    canvas.setAttribute('aria-labelledby', space.sections.length > 1 ? `tab-section-${section.id}` : `tab-space-${space.id}`);
    canvas.setAttribute('aria-busy', 'true');
    canvas.replaceChildren(...loadingNodes(`Chargement de l'espace ${space.label}…`, 5, { cover: true }));
    canvas.dataset.grid = section.module === 'concevoir' ? 'on' : 'off';
    $('inspector-body').replaceChildren(...loadingNodes('Chargement du détail…', 3));
    $('explorer-body').replaceChildren(...loadingNodes("Chargement de l'arborescence…", 3));
    swapped = true;
  };
  const crossSpace = previousSpace !== null && previousSpace !== space.id;
  if (crossSpace && canViewTransition()) {
    try {
      const transition = document.startViewTransition(swapShell);
      // Une transition écartée (goto rapprochés) rejette ces promesses : sans
      // gestionnaire, c'est une erreur non rattrapée.
      transition.ready.catch(() => {});
      transition.finished.catch(() => {});
      await transition.updateCallbackDone;
    } catch { /* API qui lève, ou rappel qui a échoué : le repli ci-dessous reprend */ }
    // Un `goto()` plus récent a repris la main pendant la transition.
    if (controller.signal.aborted) return;
  }
  // Repli : pas de transition, ou le rappel n'est pas allé au bout.
  if (!swapped) swapShell();
  crumbBase = [scopeName(), space.label, ...(space.sections.length > 1 ? [section.label] : [])];
  docbar.setBreadcrumb([]);
  docbar.setZoom([], null);
  docbar.setViews([], null);
  docbar.setValidation('');
  // Rien d'un espace quitté ne survit dans la coque (M7) : la commande
  // équivalente se réécrit au montage suivant.
  dock.echo('');

  const ctx = {
    api, host, glossary, dock, docbar, empty, icon,
    canvas,
    section: section.id,
    scope,
    setItem,
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
    if (section.module) {
      const module = await import(`./spaces/${section.module}.js`);
      await module.mount(canvas, ctx);
    } else {
      mountPreferences(canvas, ctx);
    }
  } catch (error) {
    if (ctx.signal.aborted) return;
    clearLoading(canvas);
    canvas.append(empty(
      `L'espace ${space.label} n'a pas pu s'ouvrir`,
      String(error && error.message ? error.message : error),
      null,
      { label: 'Réessayer', run: () => goto(section.id, {}) },
    ));
  }
  // Un `goto()` plus récent a repris la main : il gère lui-même son chargement.
  if (ctx.signal.aborted) return;
  for (const area of [canvas, $('inspector-body'), $('explorer-body')]) clearLoading(area);
  canvas.setAttribute('aria-busy', 'false');
  fillEmptyPanels();
  markFirstSpaceMounted();
}

// ── Préférences : la seule rubrique que la coque rend elle-même ─────────────
//
// Trois réglages distincts, trois mots distincts : Thème, Densité, Plein
// écran. Les mêmes boutons que la barre d'état, jamais une seconde mémoire.

function mountPreferences(canvas, ctx) {
  const choice = (label, attr, value, pressed, run) => {
    const button = mk('button', { type: 'button', [attr]: value, 'aria-pressed': String(pressed), text: label });
    button.addEventListener('click', run);
    return button;
  };
  const group = (title, sentence, ...controls) => mk('section', { class: 'pref-group' },
    mk('h3', { class: 'pref-title', text: title }),
    mk('p', { class: 'space-sub', text: sentence }),
    mk('div', { class: 'seg', role: 'group', 'aria-label': title }, ...controls));
  const theme = root.dataset.themePref;
  const density = root.dataset.density;
  const contrast = root.dataset.contrast;
  const full = root.dataset.focus === 'on';
  const wrap = mk('div', { class: 'pref-wrap' },
    spaceHead('Préférences', 'Réglages de cet écran, gardés par projet dans ce navigateur.'),
    group('Thème', 'Clair, sombre, ou celui du système.',
      choice('Système', 'data-theme-pref', 'system', theme === 'system', () => setThemePref('system')),
      choice('Clair', 'data-theme-pref', 'light', theme === 'light', () => setThemePref('light')),
      choice('Sombre', 'data-theme-pref', 'dark', theme === 'dark', () => setThemePref('dark'))),
    group('Densité', 'Confortable ouvre les panneaux et écrit les libellés ; compacte les replie et resserre les contrôles.',
      choice('Confortable', 'data-density-pref', 'confortable', density === 'confortable', () => setDensity('confortable')),
      choice('Compacte', 'data-density-pref', 'compacte', density === 'compacte', () => setDensity('compacte'))),
    group('Contraste', 'Élevé porte tout texte à 7:1 au moins, dans les deux thèmes. Par défaut, celui du système.',
      choice('Standard', 'data-contrast-pref', 'standard', contrast === 'standard', () => setContrast('standard')),
      choice('Élevé', 'data-contrast-pref', 'high', contrast === 'high', () => setContrast('high'))),
    group('Plein écran', "Donne la toile à tout l'écran : panneaux épinglés et dock s'effacent. ⇧⌘F le bascule de partout.",
      choice('Plein écran', 'data-fullscreen-pref', 'on', full, toggleFocus)),
  );
  canvas.replaceChildren(wrap);
  ctx.inspector.replaceChildren();
}

// ── Portée : « Tous les projets », ou un projet (P31) ───────────────────────
//
// Une seule sélection pour toute la coque : plus de zoom Flotte/Projet par
// espace, plus de projet « prévisualisé » distinct du projet servi. La portée
// vit dans l'URL ; en changer recharge la page, pour que l'hôte relise le
// projet servi — et donc s'il accepte l'écriture.

/** Le registre (`/api/projects`), lu une fois à l'amorçage. */
let registry = [];

function projectEntry(slug) {
  return registry.find((p) => p.slug === slug) || null;
}

function baseName(path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || '';
}

/** Le nom d'un projet à l'écran, jamais son slug : celui-ci mutile les accents
 * (« Référence agentique » devient « r-f-rence-agentique »). Hors registre, le
 * dossier servi porte le même nom. */
function projectName(slug) {
  const entry = projectEntry(slug);
  if (entry && entry.name) return entry.name;
  if (entry && entry.path) return baseName(entry.path);
  if (slug === host.project && host.status) return baseName(host.status.projectRoot) || 'projet servi';
  return 'projet';
}

function scopeName() {
  return host.scopeAll ? 'Tous les projets' : projectName(host.project);
}

function scopeUrl(slug, hash) {
  const params = new URLSearchParams(location.search);
  params.delete('project');
  params.delete('scope');
  if (slug) params.set('project', slug);
  else params.set('scope', 'all');
  return `${location.pathname}?${params.toString()}${hash}`;
}

/** Change la portée partout. Choisir un projet ouvre sa fiche (Flotte en
 * portée projet), pas la table de la Flotte ; revenir à « Tous les projets »
 * garde l'espace courant. `target` impose un autre écran (lien profond). */
function setScope(slug, target) {
  const section = SECTIONS.get(current) || SPACES[0].sections[0];
  const hash = target ?? (slug ? '#flotte' : locationHash(section, {}));
  location.assign(scopeUrl(slug || null, hash));
}

const scope = {
  get all() { return host.scopeAll; },
  get project() { return host.scopeAll ? null : host.project; },
  get name() { return scopeName(); },
  set: setScope,
};

function renderScopeChip() {
  $('project-name').textContent = scopeName();
  // Maquettes : « Tous les projets 11 » — le nombre de projets du registre.
  const count = $('project-count');
  count.hidden = !(host.scopeAll && registry.length);
  count.textContent = String(registry.length);
  $('project-chip').setAttribute('aria-label', `Portée : ${scopeName()} — changer de portée`);
}

// Lecture seule (M6) : dit en tête de la toile, pas seulement dans la barre
// d'état, avec le geste qui rend l'écriture — revenir au projet ouvert, ou
// ouvrir le cockpit depuis celui-ci.
function renderReadOnlyBanner() {
  const banner = $('readonly-banner');
  banner.hidden = !host.readOnly;
  banner.replaceChildren();
  if (!host.readOnly) return;
  const home = host.status && host.status.home;
  const away = home && home !== host.project;
  const sentence = away
    ? `${scopeName()} est consulté depuis le cockpit ouvert sur ${projectName(home)} : seul le projet ouvert accepte une écriture.`
    : "Ce cockpit n'a été ouvert depuis aucun projet : il lit le registre sans y écrire.";
  const actions = mk('div', { class: 'ro-actions' });
  if (away) {
    const back = mk('button', { type: 'button', class: 'btn pri', 'data-ro-action': 'home', text: `Revenir à ${projectName(home)}` });
    back.addEventListener('click', () => setScope(home, locationHash(SECTIONS.get(current) || SPACES[0].sections[0], {})));
    actions.append(back);
  }
  const path = (projectEntry(host.project) || {}).path || (host.status && host.status.projectRoot);
  if (path && !host.scopeAll) {
    const command = `grimoire cockpit serve --project-root "${path}"`;
    const copy = mk('button', { type: 'button', class: 'btn', 'data-ro-action': 'copy', text: 'Copier la commande' });
    copy.title = `Ouvre le cockpit sur ce projet, en écriture : ${command}`;
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(command); copy.textContent = 'Commande copiée'; } catch { copy.textContent = 'Copie refusée : recopiez la commande'; }
    });
    actions.append(mk('code', { class: 'mono', text: command }), copy);
  }
  banner.append(
    icon('lock'),
    mk('div', { class: 'ro-text' }, mk('strong', { text: 'Lecture seule' }), mk('span', { text: ' — ' + sentence })),
    actions,
  );
}

// ── Pouls : actualisation, compteur de décisions, horodatage ────────────────

// Le compteur de l'onglet « À traiter » : le `count` de `/api/workspace/attention`
// (hors « résolu automatiquement »), dans la portée courante — le nombre
// d'éléments qui attendent dans l'espace, celui qui décide aussi de son état
// vide. Lecture seule : relu au premier pouls et chaque fois que l'empreinte du
// pouls change ; le pouls lui-même (décisions K6) n'est plus qu'un déclencheur.
let attentionPrint = null;
let attentionSeq = 0;
// La première lecture parcourt tout le registre : elle attend la fin du premier
// montage d'espace pour ne pas lui disputer le serveur (Flotte : budget de
// 900 ms), et reprend la réponse que l'espace vient de lire (Flotte, À traiter)
// si elle a moins de `ATTENTION_REUSE_MS`. Jamais onglet masqué. Les pouls
// suivants relisent toujours le serveur.
const ATTENTION_REUSE_MS = 5000;
let attentionFirstRead = true;

function renderAttentionCount(count, unreadable = 0) {
  const badgeNode = $('decisions-count');
  const tab = $('tab-space-a-traiter');
  badgeNode.hidden = count === 0;
  badgeNode.textContent = String(count);
  let label = `${count} élément${count > 1 ? 's' : ''} à traiter`;
  if (unreadable) label += ` — ${unreadable} projet(s) illisible(s)`;
  badgeNode.title = label;
  tab.setAttribute('aria-label', badgeNode.hidden ? 'À traiter' : `À traiter — ${label}`);
  badgeNode.dataset.pending = String(count);
}

async function refreshAttentionCount({ maxAgeMs = 0 } = {}) {
  const seq = ++attentionSeq;
  try {
    const data = await api.attention(host.scopeAll, { maxAgeMs });
    if (seq !== attentionSeq) return;
    const unreadable = (data.projects || []).filter((p) => p.state === 'unreadable').length;
    renderAttentionCount(Number(data.count) || 0, unreadable);
  } catch { /* le compteur garde sa dernière valeur : l'espace dit l'erreur */ }
}

function onPulseDecisions() {
  const print = live.state.fingerprint;
  if (print !== null && print === attentionPrint) return;
  attentionPrint = print;
  if (attentionFirstRead) {
    attentionFirstRead = false;
    firstSpaceMounted.then(() => {
      if (document.hidden) {
        // Reprise au prochain pouls visible, par le même chemin.
        attentionFirstRead = true;
        attentionPrint = null;
        return;
      }
      refreshAttentionCount({ maxAgeMs: ATTENTION_REUSE_MS });
    });
    return;
  }
  refreshAttentionCount();
}

function openDecisions() {
  // Sur le cockpit le compteur est global : la vue s'ouvre sur tout le registre,
  // donc en portée « Tous les projets ».
  if (host.kind === 'cockpit' && !host.scopeAll) return setScope(null, '#travail?view=decisions');
  return goto('travail', { view: 'decisions' });
}

const live = createLive({
  onDecisions: onPulseDecisions,
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
  const top = $('top-live');
  top.dataset.state = status === 'error' ? 'error' : (status === 'paused' ? 'paused' : 'ok');
  $('top-live-text').textContent = status === 'error' ? 'hors ligne' : (status === 'paused' ? 'en pause' : 'en direct');
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
// ouverte depuis la puce de portée (#project-chip), elle ne montre QUE la
// section « Projets » — c'est le sélecteur de portée — voir `openPalette()`.
let paletteSectionFirst = null;
let paletteOnly = false;

// Ordre par défaut des sections titrées ; ne pas fusionner en une liste
// plate (30+ entrées sans repère) comme avant.
const PALETTE_SECTION_ORDER = ['Espaces', 'Affichage', 'Commandes', 'Projets', 'Tâches', 'Workflows', 'Fichiers'];
const PALETTE_SECTION_CAP = 12;
// Le sélecteur de portée n'a pas de plafond : un projet que l'on ne voit pas
// sans le taper n'est pas choisissable (M6, 4 projets sur 16 cachés).
const PALETTE_UNCAPPED = new Set(['Projets']);

// Chaque source alimente la palette indépendamment : une route absente ou en
// erreur (projet sans ledger, sans blueprint…) ne doit jamais vider les
// autres. `settle` réduit un Promise.allSettled à sa valeur, ou au repli.
function settle(result, fallback) {
  return result.status === 'fulfilled' ? result.value : fallback;
}

async function buildPalette() {
  // Un espace, et chaque rubrique d'un espace qui en a plusieurs.
  const spaces = SPACES.flatMap((s) => [
    { section: 'Espaces', label: s.label, hint: 'Espace', command: `grimoire cockpit serve # ${s.id}`, run: () => goto(s.id) },
    ...(s.sections.length > 1 ? s.sections.map((r) => ({
      section: 'Espaces', label: `${s.label} › ${r.label}`, hint: 'Rubrique',
      command: `grimoire cockpit serve # ${s.id}/${r.id}`, run: () => goto(r.id),
    })) : []),
  ]);

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
  const listed = settle(projectsResult, {}).projects;
  if (listed) registry = listed;
  // Une ligne par projet : pastille d'état, nom, dossier en texte secondaire
  // (le dossier, pas le slug : deux projets homonymes s'y distinguent). La
  // portée en cours porte une coche (`current`), plus une colonne de texte.
  const projects = host.kind === 'cockpit'
    ? [
        {
          section: 'Projets', kind: 'scope-all', label: 'Tous les projets',
          hint: registry.length === 1 ? '1 projet' : `${registry.length} projets`,
          current: host.scopeAll, run: () => setScope(null),
        },
        ...registry.map((p) => ({
          section: 'Projets', kind: 'project', label: projectName(p.slug),
          hint: p.path || '', state: projectState(p),
          current: !host.scopeAll && p.slug === host.project,
          run: () => setScope(p.slug),
        })),
      ]
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
  // Ouverte pendant l'amorçage, la palette restait vide jusqu'à la frappe suivante.
  if (!$('palette').hidden) renderPalette($('palette-input').value);
}

/** Pastille d'un projet : suivi par Grimoire, dossier sans Grimoire, ou disparu. */
function projectState(entry) {
  if (entry.exists === false) return 'missing';
  if (entry.managed || entry.is_grimoire) return 'ok';
  return 'plain';
}

const PROJECT_STATE_LABEL = {
  ok: 'suivi par Grimoire',
  plain: 'sans Grimoire',
  missing: 'dossier introuvable',
};

// La recherche ignore la casse ET les accents : « ref » trouve « Référence
// agentique », « portee » trouve « Portée ». Chaque mot tapé doit figurer
// (dans l'ordre que l'on veut) : « kit forge » trouve Grimoire-Forge/grimoire-kit.
function fold(text) {
  return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function paletteMatches(item, words) {
  if (!words.length) return true;
  const haystack = fold(`${item.label} ${item.hint}`);
  return words.every((word) => haystack.includes(word));
}

/** Entrée « Affichage » de la palette : son libellé suit l'état de la bascule. */
function displayItems() {
  const on = root.dataset.atelier === 'on';
  return [{
    section: 'Affichage',
    label: on ? "Masquer les panneaux d'atelier" : "Afficher les panneaux d'atelier",
    hint: 'Rail, explorateur, inspecteur, dock',
    command: '⇧⌘L',
    run: toggleAtelier,
  }];
}

// `sectionFirst` : section à placer en tête. `only` : ne montrer qu'elle
// (la puce de portée ouvre ainsi le sélecteur de projets). `null` garde
// l'ordre par défaut.
function openPalette(sectionFirst = null, { only = false } = {}) {
  paletteSectionFirst = sectionFirst;
  paletteOnly = Boolean(sectionFirst && only);
  const palette = $('palette');
  palette.hidden = false;
  // Deux présentations d'un même composant : la palette ⌘K flotte au centre ;
  // le sélecteur de portée est un menu ancré sous la puce qui l'ouvre.
  palette.dataset.mode = paletteOnly ? 'scope' : 'commands';
  palette.setAttribute('aria-label', paletteOnly ? 'Choisir la portée' : 'Palette de commandes');
  $('palette-input').value = '';
  $('palette-input').placeholder = paletteOnly
    ? 'Rechercher un projet ou un dossier…'
    : 'Aller à, ou lancer une commande grimoire…';
  placePalette();
  $('palette-input').focus();
  renderPalette('');
}

function closePalette() {
  $('palette').hidden = true;
  paletteSectionFirst = null;
  paletteOnly = false;
}

/** Ancre le sélecteur de portée sous sa puce ; la palette ⌘K garde sa place. */
function placePalette() {
  const box = $('palette-box');
  if (!paletteOnly) {
    box.style.removeProperty('top');
    box.style.removeProperty('left');
    return;
  }
  const chip = $('project-chip').getBoundingClientRect();
  const gutter = 16;
  const width = Math.min(440, window.innerWidth - 2 * gutter);
  const left = Math.max(gutter, Math.min(chip.left, window.innerWidth - width - gutter));
  box.style.top = `${Math.round(chip.bottom + 8)}px`;
  box.style.left = `${Math.round(left)}px`;
}

/** Habille la palette une fois : loupe dans le champ, aide clavier en pied. */
function dressPalette() {
  const box = $('palette-box');
  const input = $('palette-input');
  if (box.querySelector('.pal-field')) return;
  const field = mk('label', { class: 'pal-field' }, icon('search'));
  input.replaceWith(field);
  field.append(input);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-controls', 'palette-list');
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-autocomplete', 'list');
  box.append(mk('div', { class: 'pal-foot', 'aria-hidden': 'true' },
    mk('span', null, mk('span', { class: 'kbd', text: '↑' }), mk('span', { class: 'kbd', text: '↓' }), ' parcourir'),
    mk('span', null, mk('span', { class: 'kbd', text: 'Entrée' }), ' choisir'),
    mk('span', null, mk('span', { class: 'kbd', text: 'Échap' }), ' fermer'),
  ));
}

/** Dossier sur une ligne : le début s'élide, la fin reste lisible — le dernier
 * segment, et son parent s'il est court (« …/Grimoire-Forge/grimoire-kit »). */
function pathNode(path) {
  const parts = String(path || '').split(/(?=[\\/])/);
  const keep = parts.length > 1 && parts.slice(-2).join('').length <= 36 ? 2 : 1;
  const tail = parts.slice(-keep).join('');
  const head = parts.slice(0, -keep).join('');
  return mk('span', { class: 'lbl pal-path', title: path },
    head ? mk('span', { class: 'pal-path-head', text: head }) : null,
    mk('span', { class: 'pal-path-tail', text: tail }));
}

function paletteRow(item, index) {
  const li = mk('li', {
    role: 'option',
    id: `palette-opt-${index}`,
    class: `pal-row${item.kind ? ` pal-${item.kind}` : ''}`,
    'aria-selected': String(index === 0),
  });
  li.dataset.index = String(index);
  if (item.kind === 'project' || item.kind === 'scope-all') {
    // Le nom d'abord dans le DOM (premier `span`) ; la pastille est dessinée
    // par la CSS (`::before`, selon `data-state`), la coche par une icône.
    if (item.kind === 'project') {
      li.dataset.state = item.state;
      li.title = `${item.label} — ${PROJECT_STATE_LABEL[item.state]}\n${item.hint}`;
    } else {
      li.append(icon('scope'));
    }
    li.append(mk('span', { class: 'pal-label', text: item.label }));
    li.append(item.kind === 'project' ? pathNode(item.hint) : mk('span', { class: 'lbl', text: item.hint }));
    if (item.current) {
      li.setAttribute('aria-current', 'true');
      li.append(icon('check', 'Portée actuelle'));
    }
    return li;
  }
  li.append(mk('span', { class: 'pal-label', text: item.label }));
  li.append(mk('span', { class: 'lbl', text: item.hint }));
  li.append(mk('span', { class: 'cmd', text: item.command, title: item.command }));
  return li;
}

function selectPaletteIndex(index, { scroll = true } = {}) {
  const options = [...$('palette-list').querySelectorAll('li[role="option"]')];
  if (!options.length) {
    $('palette-input').removeAttribute('aria-activedescendant');
    return;
  }
  paletteIndex = (index + options.length) % options.length;
  options.forEach((li, i) => li.setAttribute('aria-selected', String(i === paletteIndex)));
  $('palette-input').setAttribute('aria-activedescendant', options[paletteIndex].id);
  if (scroll) options[paletteIndex].scrollIntoView({ block: 'nearest' });
}

function paletteSectionOrder() {
  if (!paletteSectionFirst) return PALETTE_SECTION_ORDER;
  if (paletteOnly) return [paletteSectionFirst];
  return [paletteSectionFirst, ...PALETTE_SECTION_ORDER.filter((s) => s !== paletteSectionFirst)];
}

// La palette était une liste plate de 30+ entrées sans repère (spaces,
// commandes, projets, workflows, tâches, fichiers concaténés) : on la
// regroupe désormais par section titrée, plafonnée par section pour qu'une
// section bavarde (Commandes) n'évince pas les autres (Projets, Tâches…).
function renderPalette(query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  const list = $('palette-list');
  list.replaceChildren();
  list.scrollTop = 0;

  const bySection = new Map();
  for (const item of [...paletteItems, ...displayItems()]) {
    if (!paletteMatches(item, words)) continue;
    if (!bySection.has(item.section)) bySection.set(item.section, []);
    bySection.get(item.section).push(item);
  }

  const matches = [];
  for (const section of paletteSectionOrder()) {
    const all = bySection.get(section) || [];
    const items = PALETTE_UNCAPPED.has(section) ? all : all.slice(0, PALETTE_SECTION_CAP);
    if (!items.length) continue;

    // Le compte vit dans un attribut (rendu par `::after`) : le texte de
    // l'en-tête reste le nom de la section.
    const header = mk('li', { role: 'presentation', class: 'palette-section', text: section });
    header.dataset.count = String(all.filter((item) => item.kind !== 'scope-all').length);
    list.append(header);

    for (const item of items) {
      const li = paletteRow(item, matches.length);
      li.addEventListener('click', () => { closePalette(); item.run(); });
      list.append(li);
      matches.push(item);
    }
  }
  if (!matches.length) {
    list.append(mk('li', { role: 'presentation', class: 'pal-empty', text: query.trim()
      ? `Aucun résultat pour « ${query.trim()} ».`
      : 'Rien à proposer pour le moment.' }));
  }
  list._matches = matches;
  selectPaletteIndex(0, { scroll: false });
}

function movePalette(delta) {
  // Les en-têtes de section (`role="presentation"`) ne sont pas des options :
  // seuls les `li[role="option"]` comptent pour la navigation clavier, dans
  // le même ordre que `list._matches`.
  selectPaletteIndex(paletteIndex + delta);
}

// ── Console ─────────────────────────────────────────────────────────────────

async function runCommand(argv) {
  // La sortie vit dans la Console du dock : lancer une commande la montre, même
  // quand les panneaux d'atelier sont fermés (sous 900 px le dock reste à part).
  if (root.dataset.atelier !== 'on' && !isNarrow()) setAtelier(true);
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

// P58 : la table des raccourcis est la source de l'aide (« ? ») ; la liste ne
// décrit que ce que `bindShortcuts` et les espaces câblent réellement.
const SHORTCUTS = [
  ['⌘K', 'Ouvrir la palette, depuis tous les espaces'],
  ['⌘1 à ⌘5', "Aller à l'espace 1 à 5"],
  ['J / K', 'Ligne suivante / précédente dans À traiter'],
  ['Entrée', "Lancer l'action principale de la ligne tenue"],
  ['1 à 5', 'Ouvrir ou fermer un panneau'],
  ['⇧⌘F', 'Plein écran'],
  ['⇧⌘L', "Afficher ou masquer les panneaux d'atelier"],
  ['?', "Afficher cette aide"],
  ['Échap', 'Fermer la palette, un tiroir ou cette aide'],
];

let shortcutsReturnFocus = null;

// Tout le reste de la page devient inerte tant que l'aide est ouverte (focus,
// clics et lecteurs d'écran restent dans la boîte).
function setBackgroundInert(on) {
  for (const id of ['shell']) if ($(id)) $(id).inert = on;
}

function openShortcuts() {
  const dialog = $('shortcuts');
  const list = $('shortcuts-list');
  list.replaceChildren(...SHORTCUTS.flatMap(([keys, what]) => [
    mk('dt', null, mk('span', { class: 'kbd', text: keys })),
    mk('dd', { text: what }),
  ]));
  shortcutsReturnFocus = document.activeElement;
  dialog.hidden = false;
  setBackgroundInert(true);
  $('shortcuts-close').focus();
}

function closeShortcuts() {
  $('shortcuts').hidden = true;
  setBackgroundInert(false);
  const back = shortcutsReturnFocus;
  shortcutsReturnFocus = null;
  if (back && back.isConnected && typeof back.focus === 'function') back.focus();
}

function bindShortcuts() {
  document.addEventListener('keydown', (event) => {
    const meta = event.metaKey || event.ctrlKey;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;

    if (!$('shortcuts').hidden) {
      // L'aide est modale : Échap ou « ? » la ferme, ⌘K la remplace par la palette.
      if (event.key === 'Escape' || event.key === '?') { event.preventDefault(); closeShortcuts(); return; }
      // Le seul élément focalisable est « Fermer » : Tab y reste (piège de focus).
      if (event.key === 'Tab') { event.preventDefault(); $('shortcuts-close').focus(); return; }
      if (!meta) return;
      closeShortcuts();
    }

    if (meta && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return; }
    if (meta && event.shiftKey && event.key.toLowerCase() === 'f') { event.preventDefault(); toggleFocus(); return; }
    if (meta && event.shiftKey && event.key.toLowerCase() === 'l') { event.preventDefault(); toggleAtelier(); return; }
    if (meta && /^[1-5]$/.test(event.key)) { event.preventDefault(); goto(SPACES[Number(event.key) - 1].id); return; }

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

    if (event.key === '?' && !meta && !event.altKey) { event.preventDefault(); openShortcuts(); return; }

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
    fillEmptyPanels();
  });
  $('st-density').addEventListener('click', toggleDensity);
  $('st-atelier').addEventListener('click', toggleAtelier);
  for (const id of ['inspector-body', 'explorer-body']) {
    new MutationObserver(syncDetail).observe($(id), { childList: true, subtree: true, characterData: true });
  }
  syncDetail();
  $('st-fullscreen').addEventListener('click', toggleFocus);
  $('palette-open').addEventListener('click', () => openPalette());
  $('shortcuts-close').addEventListener('click', closeShortcuts);
  $('shortcuts').addEventListener('click', (e) => { if (e.target.id === 'shortcuts') closeShortcuts(); });
  startStampTicker();
  // La puce de portée ouvre le sélecteur : la palette réduite à « Projets »,
  // sans plafond, « Tous les projets » en tête. Changer de portée recharge la
  // page (voir `setScope`).
  $('project-chip').addEventListener('click', () => openPalette('Projets', { only: true }));
  dressPalette();
  $('palette').addEventListener('pointerdown', (e) => { if (e.target.id === 'palette') closePalette(); });
  $('palette-input').addEventListener('input', (e) => renderPalette(e.target.value));
  // La souris et le clavier tiennent la même ligne : survoler une entrée la
  // rend active, et Entrée choisit celle que l'on voit en surbrillance.
  $('palette-list').addEventListener('pointermove', (e) => {
    const li = e.target.closest('li[role="option"]');
    if (li && Number(li.dataset.index) !== paletteIndex) selectPaletteIndex(Number(li.dataset.index), { scroll: false });
  });
  window.addEventListener('resize', () => { if (!$('palette').hidden) placePalette(); });

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

  hostKnown = true;
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
    registry = (await api.projects().catch(() => null))?.projects || [];
    renderScopeChip();
    renderReadOnlyBanner();
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
    // « À traiter » est l'accueil (⌘1) ; un lien profond ouvre son écran.
    const { target, params } = parseHash(location.hash || '#' + SPACES[0].id);
    await goto(target, params, { fromUrl: true });
    window.addEventListener('hashchange', followLocation);
    window.addEventListener('popstate', followLocation);
  } else {
    restorePanelsForSpace();
  }

  // Surface de test : Playwright interroge la coque plutôt que ses détails.
  window.GrimoireWorkspace = {
    spaces: SPACES.map((s) => s.id),
    sections: Object.fromEntries(SPACES.map((s) => [s.id, s.sections.map((r) => r.id)])),
    goto, host, glossary, dock, empty,
    panelState, setPanel,
    live: { state: live.state, openDecisions },
    get scope() { return { all: scope.all, project: scope.project, name: scope.name }; },
    /** L'espace (un des cinq), la rubrique, et le module qu'elle monte. */
    get space() { return SECTIONS.get(current)?.space.id ?? null; },
    get section() { return current; },
    get module() { return SECTIONS.get(current)?.module ?? null; },
    get focus() { return root.dataset.focus; },
    get theme() { return root.dataset.theme; },
    get themePref() { return root.dataset.themePref; },
    get density() { return root.dataset.density; },
    get contrast() { return root.dataset.contrast; },
    get atelier() { return root.dataset.atelier; },
    setAtelier,
    get paletteOpen() { return !$('palette').hidden; },
  };
  document.body.dataset.ready = '1';
}

main();
