// Savoir › Mémoire — la recherche d'abord (P24, maquette « Historique — recherche mémoire »).
//
// Trois colonnes : les filtres à gauche (type, source, projet, période,
// décisions remplacées), la recherche et ses résultats au centre, l'entrée
// choisie dans le panneau de détail à droite (`ctx.inspector`).
//
// Sans terme, la colonne centrale liste une page d'entrées par projet
// (`memory/entries`, lue par tranches, jamais tout le store) ; avec un terme,
// la recherche de flotte (`memory/search`) classe par rang fusionné (RRF) entre
// projets, jamais par score brut. Chaque résultat dit son type, sa date, sa
// provenance, son projet et sa chaîne de supersession.
//
// Refus tenus ici :
// - les entrées d'un AUTRE projet d'un emplacement partagé n'apparaissent
//   qu'en comptes (le serveur ne les rend jamais) ;
// - aucun bouton inerte : Corriger, Marquer remplacée… (P34, `memoire-gestes.js`)
//   et Supprimer… passent par `POST /api/memory`, gouvernée côté cockpit et
//   ouverte au seul projet servi ; chaque geste s'ouvre dans le panneau, avec
//   aperçu avant/après ou confirmation, et dit son résultat sur place ; hors du
//   projet servi, ils sont absents et la raison est écrite ;
// - une projection (copie régénérable) ne montre aucun geste d'édition :
//   « Corriger la source » mène au fichier ou à la tâche dont elle vient ;
// - la santé de la mémoire est la rubrique voisine (`sante.js`, P25) ;
//   l'historique d'une entrée vient du journal des écritures (P14), lu par
//   `memory/entry`.
//
// API consommées : api.memoryEntries, api.memoryEntry, api.memorySearch,
// api.memoryDelete, api.memoryGesture, api.projects.

import { badge, icon, listNav, monogram, undoToast } from '../shell.js';
import { formatScore } from '../score.js';
import { parseCodeProjection } from './code-projection.js';
import { GESTURE_STYLES, INVERSE_GESTURE, historyList, openGesture, undoGesture } from './memoire-gestes.js';

const STYLE_ID = 'kn-styles';

const STYLES = `
.kn-wrap { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100%; }
.kn-filters { background: var(--panel); border-right: 1px solid var(--line); padding: 20px 16px;
  display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.kn-filters fieldset { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; min-width: 0; }
.kn-filters legend { padding: 0 0 6px; font-size: 12px; line-height: 16px; font-weight: 500; color: var(--ink3); }
.kn-chk { display: flex; align-items: center; gap: 8px; padding: 4px 0; min-width: 0; cursor: pointer; }
.kn-chk input { margin: 0; flex: none; accent-color: var(--acc); }
.kn-chk { align-items: flex-start; }
.kn-chk input { margin-top: 3px; }
.kn-chk .kn-lbl { min-width: 0; overflow-wrap: anywhere; }
.kn-chk .kn-n { margin-left: auto; color: var(--ink3); font-variant-numeric: tabular-nums; }
.kn-why { margin: 0 0 4px 24px; color: var(--ink3); font-size: var(--t-min); overflow-wrap: anywhere; }
.kn-filters select { width: 100%; height: var(--h-ctl); padding: 0 9px; border: 1px solid var(--line); border-radius: var(--r); background-color: var(--card); color: var(--ink); }
.kn-scope-link { align-self: flex-start; margin-top: 4px; }
.kn-main { min-width: 0; padding: 28px 32px; display: flex; flex-direction: column; gap: 20px; }
.kn-searchbar { display: flex; gap: 10px; align-items: center; }
.kn-searchbar label { flex-grow: 1; display: flex; min-width: 0; }
.kn-searchbar input { flex-grow: 1; height: 36px; font-size: 14px; min-width: 0; padding: 0 12px; }
.kn-summary { margin: 0; color: var(--ink2); }
.kn-card { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow); overflow: hidden; }
.kn-res { display: grid; grid-template-columns: fit-content(130px) minmax(0, 1fr) 92px; gap: 14px; padding: 14px 16px;
  border-top: 1px solid var(--line); align-items: start; cursor: pointer; }
.kn-res:first-child { border-top: 0; }
.kn-res:hover { background: var(--hover); }
.kn-res[aria-current="true"] { background: var(--acc-soft); }
.kn-res:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
/* La colonne du type s'ajuste au libellé (« Apprentissage » entier, jamais ellipsé) ;
 * fit-content(130px) borne les types inconnus trop bavards. */
.kn-res > .kn-badges { justify-self: start; max-width: 130px; align-self: start; }
.kn-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.kn-title { font-weight: 600; overflow-wrap: anywhere; }
.kn-excerpt { color: var(--ink2); overflow-wrap: anywhere; }
.kn-src { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; color: var(--ink3); font-size: 12px; min-width: 0; }
.kn-src .kn-sep { color: var(--ink3); }
.kn-src .kn-path { overflow-wrap: anywhere; }
.kn-proj { display: inline-flex; align-items: center; gap: 5px; height: 20px; padding: 0 8px 0 3px; border-radius: var(--r-pill);
  background: var(--hover); color: var(--ink2); font-size: 12px; white-space: nowrap; }
.kn-proj .mgram { width: 15px; height: 15px; font-size: 8px; }
.kn-state-old { color: var(--warn); }
.kn-date { color: var(--ink3); font-size: 12px; text-align: right; white-space: nowrap; }
.kn-notes { display: flex; flex-direction: column; gap: 6px; }
.kn-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.kn-more { align-self: flex-start; }
.kn-more[hidden] { display: none; }
.kn-detail { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.kn-head { display: flex; flex-direction: column; gap: 6px; }
.kn-badges { display: flex; gap: 8px; flex-wrap: wrap; }
.kn-detail h2 { margin: 0; font-size: 17px; line-height: 24px; font-weight: 600; overflow-wrap: anywhere; }
.kn-lead { margin: 0; color: var(--ink2); font-size: 14px; line-height: 20px; overflow-wrap: anywhere; white-space: pre-line; }
.kn-mono { font-family: var(--mono); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.kn-sig { color: var(--ink2); }
.kn-codemeta { margin: 0; color: var(--ink2); }
.kn-code { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.kn-code-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.kn-code pre:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
.kn-code pre { margin: 0; padding: 10px 12px; max-height: 360px; overflow: auto; max-width: 100%; box-sizing: border-box;
  background: var(--hover); color: var(--ink); border: 1px solid var(--line); border-radius: var(--r);
  font-family: var(--mono); font-size: 12px; line-height: 18px; white-space: pre; tab-size: 4; }
.kn-proj-more { align-self: flex-start; }
.kn-proj-more[hidden] { display: none; }
.kn-kv { margin: 0; display: flex; flex-direction: column; }
.kn-kv > div { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 10px; padding: 9px 0; border-top: 1px solid var(--line); }
.kn-kv dt { margin: 0; font-size: 12px; line-height: 16px; font-weight: 500; color: var(--ink3); }
.kn-kv dd { margin: 0; overflow-wrap: anywhere; }
.kn-kv .kn-linkish { padding: 0; border: 0; background: none; color: var(--acc); cursor: pointer; text-align: left; font: inherit; }
.kn-kv .kn-linkish:hover { text-decoration: underline; }
.kn-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.kn-danger { color: var(--bad); }
.kn-detail > .kn-actions { gap: 6px; }
.kn-actions .btn.kn-ghost { background: transparent; border-color: transparent; padding: 0 6px; }
.kn-actions .btn.kn-ghost:hover { background: var(--hover); }
.kn-confirm { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: var(--r); background: var(--bad-soft); }
.kn-confirm p { margin: 0; }
.kn-status { margin: 0; font-size: var(--t-s); }
.kn-status[data-tone="ok"] { color: var(--ok); }
.kn-status[data-tone="bad"] { color: var(--bad); }
.kn-equiv { display: flex; flex-direction: column; gap: 6px; }
.kn-eyebrow { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink3); }
.kn-equiv .formula { overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.kn-fix { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: var(--r); background: var(--acc-soft); }
.kn-fix p { margin: 0; }
.kn-fix .formula { overflow: hidden; text-overflow: ellipsis; max-width: 100%; align-self: flex-start; }
@container canvas (max-width: 720px) {
  .kn-wrap { grid-template-columns: minmax(0, 1fr); }
  .kn-filters { border-right: 0; border-bottom: 1px solid var(--line); }
  .kn-main { padding: var(--sp-4); }
  .kn-searchbar { flex-wrap: wrap; }
  .kn-res { grid-template-columns: minmax(0, 1fr) auto; }
  .kn-res > .kn-badges { grid-column: 1 / -1; }
}
`;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES + GESTURE_STYLES;
  document.head.append(style);
}

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** La commande équivalente, à copier d'un clic (jamais lancée d'ici). */
function formula(command) {
  const button = el('button', 'formula', null, { type: 'button', title: 'Copier la commande' });
  button.append(el('b', null, '›'), command);
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(command);
      button.title = 'Commande copiée';
    } catch {
      button.title = 'Copie refusée : recopie la commande';
    }
  });
  return button;
}

// ── Vocabulaire : types, natures, états ─────────────────────────────────────

/** Types (`workspace_knowledge.classify`) : libellé du filtre, du badge, teinte. */
const TYPES = [
  { id: 'decision', filter: 'Décision', label: 'Décision', tone: 'acc', always: true },
  { id: 'incident', filter: 'Incident, piège', label: 'Piège', tone: 'warn', always: true },
  { id: 'convention', filter: 'Convention', label: 'Convention', tone: '', always: true },
  { id: 'learning', filter: 'Apprentissage', label: 'Apprentissage', tone: '' },
  { id: 'story', filter: 'Récit', label: 'Récit', tone: '' },
  { id: 'untyped', filter: 'Fait non typé', label: 'Fait', tone: '', always: true, off: true },
  { id: 'code', filter: 'Code (projection)', label: 'Code', tone: '', off: true },
  { id: 'docs', filter: 'Documentation (projection)', label: 'Doc', tone: '' },
  { id: 'task', filter: 'Tâche (projection)', label: 'Tâche', tone: '', off: true },
  { id: 'projection', filter: 'Autre projection', label: 'Projection', tone: '' },
];
const TYPE = Object.fromEntries(TYPES.map((t) => [t.id, t]));
// Les décisions, conventions et incidents passent avant le reste.
const HUMAN = ['decision', 'convention', 'incident'];
const humanRank = (entry) => { const i = HUMAN.indexOf(entry.type); return i < 0 ? HUMAN.length : i; };
const byHuman = (items) => items
  .map((entry, i) => ({ entry, i }))
  .sort((a, b) => humanRank(a.entry) - humanRank(b.entry) || a.i - b.i)
  .map((x) => x.entry);
const HIDDEN_PROJECTIONS = ['code', 'task'];
const KINDS = [
  { id: 'souvenir', label: 'Souvenirs' },
  { id: 'projection', label: 'Projections' },
];
const PERIODS = [
  { id: 'all', label: 'Toutes les dates', days: 0 },
  { id: '7', label: '7 derniers jours', days: 7 },
  { id: '30', label: '30 derniers jours', days: 30 },
  { id: '365', label: '12 derniers mois', days: 365 },
];
const STATE = {
  live: { label: 'en vigueur', tone: 'ok' },
  superseded: { label: 'remplacée', tone: 'warn' },
  orphan: { label: 'remplaçant introuvable', tone: 'warn' },
  retracted: { label: 'retirée', tone: 'bad' },
};

const DATE_SHORT = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' });
const DATE_YEAR = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
const DATE_LONG = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });

function parseDate(value) {
  const when = value ? new Date(value) : null;
  return when && !Number.isNaN(when.getTime()) ? when : null;
}

function shortDate(value) {
  const when = parseDate(value);
  if (!when) return '—';
  return (when.getFullYear() === new Date().getFullYear() ? DATE_SHORT : DATE_YEAR).format(when);
}

function sinceDate(period) {
  const days = (PERIODS.find((p) => p.id === period) || PERIODS[0]).days;
  if (!days) return '';
  return new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function sourceLabel(entry) {
  const source = (entry.provenance && entry.provenance.source) || {};
  if (source.kind === 'saisie libre') return 'Saisie libre';
  if (source.kind === 'tâche') return `Tâche ${String(source.uri || '').replace(/^task:/, '')}`;
  if (source.uri) return source.uri;
  return entry.source && entry.source !== 'inconnue' ? entry.source : 'Source inconnue';
}

function projectChip(entry) {
  const name = entry.projectName || entry.projectSlug || 'projet';
  const chip = el('span', 'kn-proj', null, { title: `Projet : ${name}` });
  chip.dataset.project = entry.projectSlug || '';
  chip.append(monogram(name), document.createTextNode(name));
  return chip;
}

/** La chaîne de supersession dite en une ligne : en vigueur, remplacée par…, remplace… */
function stateText(entry) {
  const sup = entry.supersession || {};
  const parts = [];
  if (sup.state === 'superseded' && sup.replacedBy) {
    parts.push(`remplacée par « ${sup.replacedBy.title || sup.replacedBy.id} »`);
  } else {
    parts.push((STATE[sup.state] || STATE.live).label);
  }
  const replaces = sup.replaces || [];
  if (replaces.length) {
    const first = replaces[0].title || replaces[0].id;
    parts.push(replaces.length > 1 ? `remplace « ${first} » et ${replaces.length - 1} autre(s)` : `remplace « ${first} »`);
  }
  return parts.join(' · ');
}

// ── L'espace ────────────────────────────────────────────────────────────────

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([]);
  ctx.docbar.setViews([], null);

  const allScope = Boolean(ctx.scope && ctx.scope.all);
  const canDelete = ctx.host.kind === 'cockpit' && !ctx.host.readOnly;
  const homeSlug = (ctx.host.status && ctx.host.status.home) || ctx.host.project || '';
  const params = ctx.params || {};

  // `types` dans l'URL (clic sur un hall de la carte, P73) : la vue s'ouvre
  // filtrée sur ces seuls types ; sinon, les types cochés par défaut.
  const wantedTypes = String(params.types || '').split(',').filter((t) => TYPE[t]);
  const state = {
    q: String(params.q || ''),
    projects: [],
    selected: new Set(),
    types: new Set(wantedTypes.length ? wantedTypes : TYPES.filter((t) => !t.off).map((t) => t.id)),
    kinds: new Set(KINDS.map((k) => k.id)),
    period: 'all',
    includeSuperseded: false,
    items: [],
    perProject: new Map(),
    counts: { type: {}, kind: {} },
    filteredOut: 0,
    selectedKey: params.entry ? `${params.of || ''}::${params.entry}` : '',
    status: null,
    loading: false,
    token: 0,
  };

  // Les projets de la portée : le projet en portée, ou ceux du registre.
  if (allScope) {
    const payload = await ctx.api.projects().catch(() => null);
    state.projects = ((payload && payload.projects) || [])
      .filter((p) => p.exists !== false)
      .map((p) => ({ slug: p.slug, name: p.name || p.slug }));
  } else {
    state.projects = [{ slug: '', name: ctx.scope.name || 'ce projet' }];
  }
  for (const p of state.projects) state.selected.add(p.slug);
  if (ctx.signal.aborted) return;

  const wrap = el('div', 'kn-wrap');
  const filters = el('nav', 'kn-filters', null, { 'aria-label': 'Filtres' });
  const main = el('main', 'kn-main');
  wrap.append(filters, main);
  root.replaceChildren(wrap);

  // Barre de recherche : le champ et la commande équivalente.
  const searchbar = el('div', 'kn-searchbar');
  const label = el('label');
  const input = el('input', 'input', null, { type: 'search', placeholder: 'Rechercher dans la mémoire…', autocomplete: 'off' });
  input.value = state.q;
  label.append(el('span', 'sr-only', 'Rechercher dans la mémoire'), input);
  const formulaHost = el('span');
  searchbar.append(label, formulaHost);
  const summary = el('p', 'kn-summary', null, { role: 'status' });
  const results = el('section', 'kn-card', null, { 'aria-label': 'Résultats' });
  // P58 : j / k parcourent les résultats, le détail suit ; Entrée le rouvre.
  listNav(results, { rows: '.kn-res', onMove: (row) => row.click() });
  const notes = el('div', 'kn-notes');
  const more = el('button', 'btn kn-more', 'Plus d’entrées', { type: 'button' });
  more.hidden = true;
  // Projections de code et de tâche décochées d'office : « +N projections » les rend.
  const projMore = el('button', 'btn kn-proj-more', null, { type: 'button' });
  projMore.hidden = true;
  projMore.addEventListener('click', () => {
    for (const t of HIDDEN_PROJECTIONS) state.types.add(t);
    refresh();
  });
  main.append(searchbar, summary, projMore, results, notes, more);

  const command = () => (state.q ? `grimoire memory search "${state.q}"` : 'grimoire memory list');
  const drawFormula = () => {
    formulaHost.replaceChildren(formula(command()));
    ctx.dock.echo(command());
  };

  const keyOf = (entry) => `${entry.projectSlug || ''}::${entry.id}`;
  const visibleProjects = () => state.projects.filter((p) => state.selected.has(p.slug));

  // ── Filtres ───────────────────────────────────────────────────────────────

  const checkbox = (labelText, checked, count, onChange, { disabled = false, why = '' } = {}) => {
    const row = el('label', 'kn-chk');
    const box = el('input', null, null, { type: 'checkbox' });
    box.checked = checked;
    box.disabled = disabled;
    box.addEventListener('change', () => onChange(box.checked));
    row.append(box, el('span', 'kn-lbl', labelText));
    if (count != null) row.append(el('span', 'kn-n', String(count)));
    const nodes = [row];
    if (why) nodes.push(el('p', 'kn-why', why));
    return nodes;
  };

  const drawFilters = () => {
    filters.replaceChildren();

    const typeSet = el('fieldset');
    typeSet.append(el('legend', null, 'Type'));
    for (const type of TYPES) {
      const n = state.counts.type[type.id] || 0;
      // Un type rare n'apparaît que s'il compte, ou si on l'a décoché (pour le recocher).
      if (!type.always && !n && state.types.has(type.id)) continue;
      typeSet.append(...checkbox(type.filter, state.types.has(type.id), n, (on) => {
        if (on) state.types.add(type.id); else state.types.delete(type.id);
        refresh();
      }));
    }

    const kindSet = el('fieldset');
    kindSet.append(el('legend', null, 'Source'));
    for (const kind of KINDS) {
      kindSet.append(...checkbox(kind.label, state.kinds.has(kind.id), state.counts.kind[kind.id] || 0, (on) => {
        if (on) state.kinds.add(kind.id); else state.kinds.delete(kind.id);
        refresh();
      }));
    }

    const projectSet = el('fieldset');
    projectSet.append(el('legend', null, 'Projet'));
    for (const project of state.projects) {
      const info = state.perProject.get(project.slug) || {};
      const shown = allScope ? state.items.filter((e) => (e.projectSlug || '') === project.slug).length : state.items.length;
      projectSet.append(...checkbox(project.name, state.selected.has(project.slug), info.reason ? null : shown, (on) => {
        if (on) state.selected.add(project.slug); else state.selected.delete(project.slug);
        refresh();
      }, { disabled: !allScope, why: info.reason ? `non interrogé : ${info.reason}` : '' }));
    }
    if (!allScope) {
      const widen = el('button', 'btn kn-scope-link', 'Tous les projets', { type: 'button' });
      widen.addEventListener('click', () => ctx.scope.set(null));
      projectSet.append(widen);
    }

    const periodSet = el('fieldset');
    periodSet.append(el('legend', null, 'Date'));
    const select = el('select', null, null, { 'aria-label': 'Période' });
    for (const period of PERIODS) {
      const option = el('option', null, period.label, { value: period.id });
      option.selected = period.id === state.period;
      select.append(option);
    }
    select.addEventListener('change', () => { state.period = select.value; refresh(); });
    periodSet.append(select);

    const superseded = checkbox('Inclure les décisions remplacées', state.includeSuperseded, null, (on) => {
      state.includeSuperseded = on;
      refresh();
    });

    filters.append(typeSet, kindSet, projectSet, periodSet, ...superseded);
  };

  // ── Lecture : une page par projet, ou la recherche de flotte ─────────────

  const matches = (entry) => {
    if (!state.kinds.has(entry.kind)) return false;
    if (!state.types.has(entry.type)) return false;
    const since = sinceDate(state.period);
    return !(since && String(entry.date || '').slice(0, 10) < since);
  };

  const typesParam = () => (TYPES.every((t) => state.types.has(t.id)) ? '' : [...state.types].join(','));
  const kindParam = () => (state.kinds.size === KINDS.length ? '' : [...state.kinds][0] || '');

  const loadList = async (append) => {
    const targets = visibleProjects().filter((p) => !append || (state.perProject.get(p.slug) || {}).nextOffset != null);
    const settled = await Promise.allSettled(targets.map((p) => ctx.api.memoryEntries(p.slug || undefined, {
      kind: kindParam(), types: typesParam(), since: sinceDate(state.period),
      includeSuperseded: state.includeSuperseded,
      offset: append ? state.perProject.get(p.slug).nextOffset : 0,
    })));
    const counts = append ? state.counts : { type: {}, kind: {} };
    const lists = [];
    settled.forEach((result, i) => {
      const project = targets[i];
      const previous = append ? state.perProject.get(project.slug) || {} : {};
      if (result.status !== 'fulfilled') {
        state.perProject.set(project.slug, { reason: String(result.reason && result.reason.message || 'API injoignable') });
        return;
      }
      const page = result.value;
      const name = (page.project && page.project.name) || project.name;
      if (page.project && page.project.name && !allScope) project.name = page.project.name;
      for (const [k, n] of Object.entries(page.counts.type || {})) counts.type[k] = (counts.type[k] || 0) + n;
      for (const [k, n] of Object.entries(page.counts.kind || {})) counts.kind[k] = (counts.kind[k] || 0) + n;
      const hidden = page.hidden || {};
      state.perProject.set(project.slug, {
        reason: page.reason,
        name,
        owner: page.project && page.project.owner,
        nextOffset: page.nextOffset,
        others: mergeOthers(previous.others, hidden.otherProjects),
        unattributed: (previous.unattributed || 0) + (hidden.unattributed || 0),
        superseded: (previous.superseded || 0) + (hidden.superseded || 0),
        filtered: (previous.filtered || 0) + (hidden.filtered || 0),
      });
      lists.push(page.entries || []);
    });
    // Un projet après l'autre, rang par rang : aucun store ne masque les autres.
    const merged = [];
    for (let i = 0; lists.some((list) => i < list.length); i += 1) {
      for (const list of lists) if (i < list.length) merged.push(list[i]);
    }
    state.counts = counts;
    state.items = byHuman(append ? state.items.concat(merged) : merged);
    state.filteredOut = [...state.perProject.values()].reduce((n, p) => n + (p.filtered || 0), 0);
  };

  const loadSearch = async () => {
    const all = visibleProjects();
    const projectsParam = allScope ? all.map((p) => p.slug).join(',') : undefined;
    const data = await ctx.api.memorySearch(state.q, projectsParam, { includeSuperseded: state.includeSuperseded });
    const counts = { type: {}, kind: {} };
    for (const entry of data.results || []) {
      counts.type[entry.type] = (counts.type[entry.type] || 0) + 1;
      counts.kind[entry.kind] = (counts.kind[entry.kind] || 0) + 1;
    }
    for (const project of data.projects || []) {
      const slug = allScope ? project.slug : '';
      state.perProject.set(slug, {
        reason: project.reason,
        name: project.name,
        owner: project.owner,
        others: project.hiddenOther || [],
        unattributed: project.hiddenUnattributed || 0,
        stale: project.hiddenStale || 0,
      });
    }
    const kept = (data.results || []).filter(matches);
    state.counts = counts;
    state.filteredOut = (data.results || []).length - kept.length;
    state.items = kept;
  };

  const refresh = async ({ append = false, stay = null } = {}) => {
    const token = ++state.token;
    drawFormula();
    if (!append) {
      state.perProject = new Map();
      summary.textContent = 'Lecture de la mémoire…';
      results.replaceChildren(...ctx.loading('Lecture de la mémoire…', 4, 'results'));
    }
    more.disabled = true;
    try {
      if (!visibleProjects().length || !state.kinds.size) {
        state.items = [];
        state.counts = state.counts || { type: {}, kind: {} };
      } else if (state.q) {
        await loadSearch();
      } else {
        await loadList(append);
      }
    } catch (error) {
      if (token !== state.token || ctx.signal.aborted) return;
      state.items = [];
      results.replaceChildren(ctx.empty(
        'Mémoire',
        `Lecture impossible : ${error && error.message ? error.message : 'API locale injoignable'}.`,
        command(), undefined, { compact: true, art: 'i-empty-memory' },
      ));
      summary.textContent = '';
      notes.replaceChildren();
      drawFilters();
      return;
    }
    if (token !== state.token || ctx.signal.aborted) return;
    drawFilters();
    drawResults();
    // `stay` : l'entrée qu'un geste vient de corriger reste ouverte, même si la
    // liste ne la sert plus (retirée, remplacée).
    const kept = stay && keyOf(stay) === state.selectedKey ? stay : null;
    const keep = state.items.find((e) => keyOf(e) === state.selectedKey) || kept || state.items.find((e) => HUMAN.includes(e.type)) || state.items[0];
    if (keep) select(keep, { focus: false });
    else { state.selectedKey = ''; ctx.inspector.replaceChildren(); }
  };

  // ── Résultats ─────────────────────────────────────────────────────────────

  const scopeLabel = () => {
    const shown = visibleProjects();
    if (!allScope) return shown[0] ? shown[0].name : 'ce projet';
    return shown.length === 1 ? shown[0].name : plural(shown.length, 'projet', 'projets');
  };

  const row = (entry) => {
    const type = TYPE[entry.type] || { label: entry.type || 'Fait', tone: '' };
    const article = el('article', 'kn-res', null, { tabindex: '0', role: 'button' });
    article.dataset.key = keyOf(entry);
    article.dataset.type = entry.type || '';
    article.dataset.kind = entry.kind || '';
    article.dataset.project = entry.projectSlug || '';
    article.setAttribute('aria-current', String(keyOf(entry) === state.selectedKey));
    const body = el('span', 'kn-body');
    const code = entry.type === 'code' ? parseCodeProjection(entry.text) : null;
    if (code) {
      body.append(el('span', 'kn-title', code.name));
      body.append(el('span', 'kn-mono kn-codemeta', `${code.file}:${code.lines.replace('-', '–')}`));
      if (code.signature) body.append(el('span', 'kn-mono kn-sig', code.signature));
    } else {
      body.append(el('span', 'kn-title', entry.title || entry.text || '(entrée vide)'));
      if (entry.excerpt) body.append(el('span', 'kn-excerpt', entry.excerpt));
    }
    const src = el('span', 'kn-src');
    const sup = entry.supersession || {};
    const stateNode = el('span', sup.state === 'live' ? 'kn-state' : 'kn-state kn-state-old', stateText(entry));
    stateNode.dataset.state = sup.state || 'live';
    src.append(
      el('span', 'kn-path', sourceLabel(entry)), el('span', 'kn-sep', '·'),
      projectChip(entry), el('span', 'kn-sep', '·'), stateNode,
    );
    body.append(src);
    const date = el('span', 'kn-date', shortDate(entry.date));
    if (entry.date) date.title = String(entry.date);
    const badges = el('span', 'kn-badges');
    badges.append(badge(type.label, type.tone));
    if (code && code.test) badges.append(badge('Test', ''));
    article.append(badges, body, date);
    article.addEventListener('click', () => select(entry, { focus: false }));
    // Entrée (P58) passe par le contrôleur de liste de la coque (le clic du résultat).
    article.addEventListener('keydown', (event) => {
      if (event.key === ' ') { event.preventDefault(); select(entry, { focus: false }); }
    });
    return article;
  };

  const drawResults = () => {
    const n = state.items.length;
    const sort = state.q
      ? (allScope && visibleProjects().length > 1 ? 'triés par rang fusionné entre projets' : 'triés par pertinence')
      : 'dans l’ordre du store';
    const noun = state.q ? plural(n, 'résultat', 'résultats') : plural(n, 'entrée', 'entrées');
    const masked = state.filteredOut ? ` · ${plural(state.filteredOut, 'masquée par les filtres', 'masquées par les filtres')}` : '';
    summary.textContent = `${noun} sur ${scopeLabel()} · ${sort}${masked}`;
    const unseen = HIDDEN_PROJECTIONS.filter((t) => !state.types.has(t))
      .reduce((sum, t) => sum + (state.counts.type[t] || 0), 0);
    projMore.hidden = !unseen;
    projMore.textContent = `+${plural(unseen, 'projection', 'projections')}`;
    projMore.title = 'Afficher les projections de code et de tâche';

    if (!visibleProjects().length) {
      results.replaceChildren(ctx.empty('Mémoire', 'Coche au moins un projet pour lire sa mémoire.', '', undefined, { compact: true, art: 'i-empty-memory' }));
    } else if (!state.kinds.size) {
      results.replaceChildren(ctx.empty('Mémoire', 'Coche au moins une source : souvenirs ou projections.', '', undefined, { compact: true, art: 'i-empty-memory' }));
    } else if (!n) {
      results.replaceChildren(state.q
        ? ctx.empty('Recherche', `Aucun résultat pour « ${state.q} ».`, command(), undefined, { compact: true, art: 'i-empty-search' })
        : ctx.empty('Mémoire', state.filteredOut ? 'Aucune entrée ne passe ces filtres.' : 'Aucune entrée dans ce store.', command(), undefined, { compact: true, art: 'i-empty-memory' }));
    } else {
      results.replaceChildren(...state.items.map(row));
    }

    notes.replaceChildren();
    const info = [...state.perProject.entries()];
    // En portée « Tous les projets », un projet affiché n'est pas « caché » :
    // seules comptent les entrées de projets que la vue ne montre pas.
    const shownNames = new Set(visibleProjects().flatMap((p) => {
      const info = state.perProject.get(p.slug) || {};
      return [p.name, info.name, info.owner];
    }));
    const others = info.flatMap(([, p]) => p.others || []).filter((o) => !allScope || !shownNames.has(o.name));
    const othersTotal = others.reduce((sum, o) => sum + o.count, 0);
    if (othersTotal) {
      const detail = mergeOthers([], others).map((o) => `${o.name} ${o.count}`).join(', ');
      notes.append(noteNode('others', `${plural(othersTotal, 'entrée', 'entrées')} d’autres projets dans un emplacement partagé (${detail}) : comptées, jamais montrées.`));
    }
    const unattributed = info.reduce((sum, [, p]) => sum + (p.unattributed || 0), 0);
    if (unattributed) {
      notes.append(noteNode('unattributed', `${plural(unattributed, 'entrée sans projet', 'entrées sans projet')} dans un emplacement partagé : comptées, jamais montrées.`));
    }
    const superseded = info.reduce((sum, [, p]) => sum + (p.superseded || 0), 0);
    if (superseded && !state.includeSuperseded) {
      notes.append(noteNode('superseded', `${plural(superseded, 'décision remplacée masquée', 'décisions remplacées masquées')} : coche « Inclure les décisions remplacées » pour les lire.`));
    }
    const stale = info.reduce((sum, [, p]) => sum + (p.stale || 0), 0);
    if (stale) notes.append(noteNode('stale', `${plural(stale, 'résultat écarté', 'résultats écartés')} : source disparue.`));
    const failed = info.filter(([, p]) => p.reason);
    if (failed.length) {
      notes.append(noteNode('failed', 'Non interrogés : ' + failed.map(([slug, p]) => `${p.name || slug || 'ce projet'} (${p.reason})`).join(', ')));
    }
    more.hidden = Boolean(state.q) || !info.some(([, p]) => p.nextOffset != null);
    more.disabled = false;
  };

  // ── Détail : l'entrée choisie dans le panneau de droite ───────────────────

  const select = (entry, { focus }) => {
    state.selectedKey = keyOf(entry);
    state.status = state.status && state.status.key === state.selectedKey ? state.status : null;
    for (const node of results.querySelectorAll('.kn-res')) {
      node.setAttribute('aria-current', String(node.dataset.key === state.selectedKey));
    }
    ctx.setItem({ q: state.q, entry: entry.id, of: entry.projectSlug || '' });
    drawDetail(entry, null);
    const wanted = state.selectedKey;
    ctx.api.memoryEntry(entry.id, entry.projectSlug || undefined).then((detail) => {
      if (ctx.signal.aborted || state.selectedKey !== wanted || !detail || !detail.found) return;
      drawDetail({ ...detail.entry, projectSlug: entry.projectSlug }, detail.entry);
    }).catch(() => {});
    if (focus) results.querySelector(`[data-key="${CSS.escape(wanted)}"]`)?.focus();
  };

  const drawDetail = (entry, full) => {
    const type = TYPE[entry.type] || { label: entry.type || 'Fait', tone: '' };
    const sup = entry.supersession || {};
    const st = STATE[sup.state] || STATE.live;
    const box = el('div', 'kn-detail');
    box.dataset.entry = entry.id;

    const head = el('div', 'kn-head');
    const badges = el('span', 'kn-badges');
    badges.append(badge(type.label, type.tone), badge(st.label, st.tone));
    const title = entry.title || entry.text || '(entrée vide)';
    const text = String(entry.text || '').trim();
    // Le titre est la première phrase : le corps reprend la suite, pas tout le texte.
    const rest = !title.endsWith('…') && text.startsWith(title) ? text.slice(title.length).trim() : text;
    const code = entry.type === 'code' ? parseCodeProjection(entry.text) : null;
    if (code) {
      badges.append(badge(code.kind, ''));
      if (code.test) badges.append(badge('Test', ''));
      head.append(badges, el('h2', null, code.name));
      head.append(el('p', 'kn-mono kn-codemeta', `${code.file}:${code.lines.replace('-', '–')}`));
      if (code.signature) head.append(el('p', 'kn-mono kn-sig', code.signature));
      if (code.docstring) head.append(el('p', 'kn-lead', code.docstring));
      box.append(head);
      if (code.source) box.append(codeBlock(code));
    } else {
      head.append(badges, el('h2', null, title));
      if (rest) head.append(el('p', 'kn-lead', rest));
      box.append(head);
    }

    const kv = el('dl', 'kn-kv');
    const add = (key, value) => {
      if (value == null || value === '') return;
      const line = el('div');
      const dd = el('dd');
      if (value instanceof Node) dd.append(value); else dd.textContent = String(value);
      line.append(el('dt', null, key), dd);
      kv.append(line);
    };
    const when = parseDate(entry.date);
    add(entry.type === 'decision' ? 'Décidé le' : 'Écrit le', when ? DATE_LONG.format(when) : '');
    const who = entry.actor || entry.agent || (entry.userId && entry.userId !== 'global' ? entry.userId : '');
    add('Par', who ? (entry.sessionId ? `${who}, session ${entry.sessionId}` : who) : (entry.sessionId ? `session ${entry.sessionId}` : 'inconnu'));
    const replaces = sup.replaces || [];
    add('Remplace', replaces.map((r) => r.title || r.id).join(' ; '));
    if (sup.replacedBy) {
      const chain = full && full.chain && full.chain.length ? full.chain : [sup.replacedBy];
      add('Remplacée par', chain.map((r) => (r.found === false ? `${r.id} (introuvable)` : r.title || r.id)).join(' → '));
    }
    if (entry.taskRef) {
      const link = el('button', 'kn-linkish', entry.taskRef, { type: 'button' });
      link.addEventListener('click', () => ctx.goto('travail', { task: entry.taskRef, of: allScope ? entry.projectSlug || '' : '' }));
      add('Tâche liée', link);
    }
    add('Source', sourceLabel(entry));
    const fresh = entry.provenance && (entry.provenance.fraicheur_libelle || entry.freshness);
    add('Fraîcheur', fresh ? `${fresh}${entry.provenance.fraicheur_raison ? ` — ${entry.provenance.fraicheur_raison}` : ''}` : '');
    add('Projet', projectChip(entry));
    add('Nature', entry.kind === 'projection' ? 'projection régénérable' : 'souvenir');
    if (state.q && entry.fleetScore != null) {
      const shown = formatScore(entry.score, entry.scoreKind);
      add('Classement', `rang ${entry.projectRank} dans son projet · ${shown.text}`);
    }
    if (entry.tags && entry.tags.length) add('Tags', entry.tags.join(', '));
    box.append(kv);

    const ownProject = canDelete && (allScope ? entry.projectSlug === homeSlug : true);
    const actions = el('div', 'kn-actions');
    const gesture = (kind, row) => {
      // Un nouveau geste efface le compte rendu du précédent.
      state.status = null;
      box.querySelector(':scope > .kn-status')?.remove();
      return openGestureIn(kind, row);
    };
    const openGestureIn = (kind, row) => openGesture(ctx, {
      entry, kind, row, anchor: actions,
      candidates: candidatesFor(entry),
      project: entry.projectSlug || homeSlug || undefined,
      onDone: async (message, { gesture: done } = {}) => {
        state.status = { key: keyOf(entry), tone: 'ok', text: message };
        await refresh({ stay: entry });
        // P57 : retirer et rendre au service se défont en un clic, 10 s durant.
        if (!INVERSE_GESTURE[done]) return;
        const project = entry.projectSlug || homeSlug || undefined;
        undoToast(done === 'retract' ? 'Entrée retirée du service.' : 'Entrée rendue au service.', {
          undone: done === 'retract' ? 'Entrée rendue au service.' : 'Entrée de nouveau retirée du service.',
          undo: async () => {
            await undoGesture(ctx, { gesture: done, entryId: entry.id, project });
            state.status = null;
            if (!ctx.signal.aborted) await refresh({ stay: entry });
          },
        });
      },
    });
    if (entry.kind === 'projection') {
      box.append(sourceFix(entry));
    } else if (ownProject) {
      // Libellés seuls, comme la maquette : les trois gestes tiennent sur une ligne.
      const correct = el('button', 'btn', 'Corriger', { type: 'button' });
      correct.addEventListener('click', () => gesture('correct'));
      const replace = el('button', 'btn', 'Marquer remplacée…', { type: 'button' });
      replace.addEventListener('click', () => gesture('supersede'));
      actions.append(correct, replace);
      if (sup.state === 'retracted') {
        const restore = el('button', 'btn', 'Rendre au service…', { type: 'button' });
        restore.prepend(icon('undo'));
        restore.addEventListener('click', () => gesture('unretract'));
        actions.append(restore);
      }
      // Bouton discret, comme la maquette : la suppression reste le dernier recours.
      const remove = el('button', 'btn kn-danger kn-ghost', 'Supprimer…', { type: 'button' });
      remove.addEventListener('click', () => confirmDelete(entry, box, actions));
      actions.append(remove);
      box.append(actions);
    } else {
      box.append(noteNode('readonly', readOnlyReason()));
    }
    if (state.status && state.status.key === keyOf(entry)) box.append(statusNode(state.status));
    const history = historyList(entry.id, full && full.journal, {
      canEdit: ownProject && entry.kind !== 'projection',
      onUndo: (row) => gesture('undo', row),
    });
    if (history) box.append(history);

    const equiv = el('div', 'kn-equiv');
    equiv.append(el('p', 'kn-eyebrow', 'Équivalent'), formula(`grimoire memory show ${entry.id}`));
    box.append(equiv);

    ctx.inspector.replaceChildren(box);
  };

  /** Les autres entrées en vigueur du même projet, déjà chargées : remplaçantes ou doublons possibles. */
  const candidatesFor = (entry) => state.items.filter((e) => keyOf(e) !== keyOf(entry)
    && (e.projectSlug || '') === (entry.projectSlug || '') && e.kind === 'souvenir'
    && (e.supersession || {}).state === 'live');

  const readOnlyReason = () => {
    if (ctx.host.kind !== 'cockpit') return 'Correction depuis le cockpit seulement : lance « grimoire cockpit serve » dans ce projet.';
    if (ctx.host.readOnly) return 'Ce cockpit est en lecture seule : il n’écrit que dans le projet qu’il sert.';
    return `Correction réservée au projet que ce cockpit sert (${homeSlug || 'projet de lancement'}) : lance « grimoire cockpit serve » dans ce projet-ci pour le corriger.`;
  };

  /** Une projection ne se corrige pas : sa source, si. */
  const sourceFix = (entry) => {
    const fix = el('div', 'kn-fix', null, { role: 'group', 'aria-label': 'Corriger la source' });
    fix.dataset.fix = 'source';
    fix.append(el('p', null, 'Projection régénérable : elle se corrige à sa source, puis se régénère. La corriger ici serait écrasé à la prochaine synchronisation.'));
    const source = (entry.provenance && entry.provenance.source) || {};
    const uri = String(source.uri || '');
    const task = source.kind === 'tâche' ? uri.replace(/^task:/, '') : '';
    const file = !task && uri ? uri.match(/^(.*?)(?::(\d+))?$/) : null;
    if (task || (file && file[1])) {
      const go = el('button', 'btn', 'Corriger la source', { type: 'button' });
      go.prepend(icon('wrench'));
      go.addEventListener('click', () => (task
        ? ctx.goto('travail', { task, of: allScope ? entry.projectSlug || '' : '' })
        : ctx.goto('source', { file: file[1], line: file[2] ? Number(file[2]) : undefined })));
      fix.append(go);
    } else {
      fix.append(el('p', null, 'Sa source n’est pas déclarée : régénère la projection.'));
    }
    fix.append(formula(entry.type === 'task' ? 'grimoire memory vector sync-tasks' : 'grimoire memory vector sync-code'));
    return fix;
  };

  const confirmDelete = (entry, box, actions) => {
    const panel = el('div', 'kn-confirm', null, { role: 'group', 'aria-label': 'Confirmer la suppression' });
    panel.append(el('p', null, `Supprimer définitivement cette entrée de ${entry.projectName || 'ce projet'} ? Le journal des écritures garde la trace de la suppression, pas son texte.`));
    const buttons = el('div', 'kn-actions');
    const yes = el('button', 'btn kn-danger', 'Supprimer définitivement', { type: 'button' });
    const no = el('button', 'btn', 'Annuler', { type: 'button' });
    buttons.append(yes, no);
    panel.append(buttons);
    actions.replaceWith(panel);
    no.focus();
    no.addEventListener('click', () => panel.replaceWith(actions));
    yes.addEventListener('click', async () => {
      yes.disabled = true;
      no.disabled = true;
      yes.textContent = 'Suppression…';
      let outcome;
      try {
        const res = await ctx.api.memoryDelete(entry.id, entry.projectSlug || homeSlug || undefined);
        outcome = res && res.ok
          ? { tone: 'ok', text: 'Entrée supprimée.' }
          : { tone: 'bad', text: `Suppression refusée : ${failureOf(res)}` };
      } catch (error) {
        outcome = { tone: 'bad', text: `Suppression refusée : ${error && error.message ? error.message : 'API injoignable'}` };
      }
      if (ctx.signal.aborted) return;
      if (outcome.tone === 'ok') {
        const index = state.items.findIndex((e) => keyOf(e) === keyOf(entry));
        state.items = state.items.filter((e) => keyOf(e) !== keyOf(entry));
        drawResults();
        const next = state.items[Math.min(Math.max(index, 0), state.items.length - 1)];
        if (next) {
          state.status = { key: keyOf(next), ...outcome };
          select(next, { focus: true });
        } else {
          state.selectedKey = '';
          const done = el('div', 'kn-detail');
          done.append(statusNode(outcome));
          ctx.inspector.replaceChildren(done);
        }
        return;
      }
      state.status = { key: keyOf(entry), ...outcome };
      panel.replaceWith(actions);
      box.querySelector('.kn-status')?.remove();
      actions.after(statusNode(outcome));
    });
  };

  // ── Gestes ────────────────────────────────────────────────────────────────

  const run = () => {
    const q = input.value.trim();
    if (q === state.q) return;
    state.q = q;
    state.selectedKey = '';
    refresh();
  };
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); run(); } });
  input.addEventListener('search', run);
  more.addEventListener('click', () => refresh({ append: true }));

  drawFilters();
  await refresh();
}

/** La source du symbole : <pre> mono, focalisable, avec bouton « Copier ». */
function codeBlock(code) {
  const wrap = el('div', 'kn-code');
  const bar = el('div', 'kn-code-bar');
  const copy = el('button', 'btn', 'Copier', { type: 'button', title: 'Copier le code' });
  copy.prepend(icon('copy'));
  const status = el('span', 'sr-only', null, { role: 'status' });
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code.source);
      status.textContent = 'Code copié.';
    } catch {
      status.textContent = 'Copie refusée : sélectionne le code.';
    }
  });
  bar.append(el('p', 'kn-eyebrow', 'Code source'), copy, status);
  // Le <pre> défile : c'est lui qui prend le focus clavier, nommé comme région.
  const pre = el('pre', null, null, { role: 'region', tabindex: '0', 'aria-label': `Code source de ${code.name}` });
  pre.append(el('code', null, code.source));
  wrap.append(bar, pre);
  return wrap;
}

function mergeOthers(previous, next) {
  const total = new Map();
  for (const o of [...(previous || []), ...(next || [])]) total.set(o.name, (total.get(o.name) || 0) + o.count);
  return [...total.entries()].map(([name, count]) => ({ name, count }));
}

function noteNode(kind, text) {
  const node = el('p', 'kn-note', text);
  node.dataset.note = kind;
  return node;
}

function statusNode({ tone, text }) {
  const node = el('p', 'kn-status', text, { role: 'status' });
  node.dataset.tone = tone;
  return node;
}

function failureOf(res) {
  if (!res) return 'réponse vide';
  const raw = String(res.stderr || res.error || res.stdout || '').trim();
  const line = raw.split('\n').filter(Boolean).pop() || `code ${res.code}`;
  return line.length > 200 ? `${line.slice(0, 199)}…` : line;
}
