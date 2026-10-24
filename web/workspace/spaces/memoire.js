// Espace Mémoire — LOT 4.
//
// Remplace `memory.html`. Cible : le store et le graphe D'ABORD, les couches
// ensuite, l'explication d'architecture derrière un onglet. Inspecteur :
// entrée.
//
// Correction due par la revue §4.5 : la page ne doit pas expliquer
// l'architecture à la place de montrer la mémoire — deux grandes cartes
// « Vitrine / Cockpit local » remplaçaient la vue du store réel. Ici,
// l'explication existe encore (onglet Architecture) mais n'est plus ce que
// l'espace montre en premier.
//
// Correction due par la revue §4.4, appliquée ici pour le même défaut de
// classe : la dérive store ↔ graphe (`parity`) est un objet vide quand aucun
// graphe n'est câblé — ce module le lit avec des reprises (`?.`), jamais un
// accès direct qui casserait sur l'absence (le `graph_stats` de l'observatoire
// hérité).
//
// Onglet Flotte (#172, dernier volet de « du générateur statique au
// portefeuille actif ») : l'agrégation mémoire multi-projets que
// `memory_link_status()` ne porte pas (mono-projet). Le sélecteur Zoom du
// docbar (déjà utilisé par Piloter et Concevoir) porte « Ce projet / Tous les
// projets » ; le tableau et la recherche croisée sont propres à cet onglet et
// ne s'affichent que là.
//
// API consommées : api.memoryStatus(), api.memoryOverview(projects),
// api.memorySearch(q, projects).

import { icon, kpiRow, STATE_ICON, stateIcon } from '../shell.js';
import { formatScore } from '../score.js';

const STYLE_ID = 'me-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .me-wrap { padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-5); }
    .me-graph { display: flex; gap: var(--sp-4); align-items: center; padding: var(--sp-4); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .me-graph-node { flex: 1; text-align: center; padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); }
    .me-graph-node .val { font-family: var(--mono); font-size: var(--t-l); color: var(--ink); }
    .me-graph-arrow { color: var(--ink3); font-size: var(--t-l); }
    .me-arch p { max-width: 640px; color: var(--ink2); }
    .me-search { display: flex; gap: var(--sp-2); max-width: 480px; }
    .me-search input { flex: 1; }
    .me-results { display: flex; flex-direction: column; gap: var(--sp-2); }
    .me-result { border: 1px solid var(--line); border-radius: var(--r); padding: var(--sp-2) var(--sp-3); background: var(--e1); cursor: pointer; }
    .me-result:hover, .me-result:focus-visible { background: var(--e2); outline: none; }
    .me-result .me-result-head { justify-content: space-between; align-items: center; margin-bottom: 4px; }
    .me-result .me-result-head > .row { flex: 0 1 auto; justify-content: flex-start; }
    .me-result .me-result-text { color: var(--ink2); font-size: var(--t-s); }
    .me-summary { color: var(--ink3); font-size: var(--t-min); }
    .me-result-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); margin-top: 6px; color: var(--ink3); font-size: var(--t-min); }
    .me-src { display: inline-flex; align-items: center; gap: 4px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--mono); color: var(--ink2); background: none; border: 0; padding: 0; }
    button.me-src { cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
    button.me-src:hover, button.me-src:focus-visible { color: var(--ink); outline: none; }
    .me-src .ico { width: 13px; height: 13px; flex: none; }
    .me-opts { gap: var(--sp-4); flex-wrap: wrap; }
    .me-opt { display: inline-flex; align-items: center; gap: 6px; }
    .me-replaced { text-decoration: line-through; }
    .me-fact { display: grid; grid-template-columns: max-content 1fr; gap: 4px var(--sp-3); font-size: var(--t-s); }
    .me-fact dt { color: var(--ink3); }
    .me-fact dd { margin: 0; color: var(--ink2); word-break: break-word; }
    @media (max-width: 640px) {
      .me-wrap { padding: var(--sp-3); }
      .me-search { max-width: none; }
    }
  `;
  document.head.append(style);
}

const dot = (cls) => stateIcon(cls);
function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}
function row(...children) { const d = document.createElement('div'); d.className = 'row'; d.append(...children); return d; }

const STATE_DOT = { ok: 'ok', ready: 'ok', unavailable: 'warn', partial: 'warn', planned: '', disabled: 'bad' };
const STATE_WORD = {
  ok: 'opérationnel', ready: 'prêt', unavailable: 'indisponible',
  partial: 'partiel', planned: 'planifié', disabled: 'désactivé', uninitialized: 'non initialisée',
};

function renderStore(wrap, ctx, memory) {
  wrap.append(kpiRow([
    { value: memory.entries == null ? '—' : String(memory.entries), label: 'entrées', icon: 'inbox' },
    { value: memory.configuredBackend || '—', label: 'backend configuré', icon: 'server' },
    { value: memory.resolvedBackend || '—', label: 'backend résolu', icon: 'plug' },
    { value: STATE_WORD[memory.state] || memory.state, label: 'état', icon: STATE_ICON[STATE_DOT[memory.state] || ''], tone: STATE_DOT[memory.state] || '' },
  ]));
  if (memory.retrieval) {
    // Ce que la recherche fait réellement, pas ce que le profil annonce.
    const r = memory.retrieval;
    wrap.append(row(
      dot(r.honored ? 'ok' : 'warn'),
      text('span', null, `recherche : ${r.label}`),
      text('span', 'lbl', `profil ${r.profile}`),
    ));
  }

  const status = document.createElement('div');
  status.append(row(dot(STATE_DOT[memory.state] || ''), text('span', null, `store ${STATE_WORD[memory.state] || memory.state}`)));
  wrap.append(status);

  if (memory.error) {
    wrap.append(text('p', 'lbl', 'erreur best-effort : ' + memory.error));
  }

  if (memory.detail && Object.keys(memory.detail).length) {
    const table = document.createElement('table');
    table.className = 'tbl';
    const tbody = document.createElement('tbody');
    for (const [key, value] of Object.entries(memory.detail)) {
      const tr = document.createElement('tr');
      const rendered = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value);
      tr.append(text('td', 'lbl', key), text('td', 'mono', rendered));
      tbody.append(tr);
    }
    table.append(tbody);
    wrap.append(table);
  }
}

function renderGraph(wrap, ctx, memory) {
  const parity = memory.parity || {};
  const h3 = text('h3', null, 'Graphe');
  h3.dataset.term = 'graphe';
  wrap.append(h3);

  if (!Object.keys(parity).length) {
    wrap.append(ctx.empty(
      'Graphe',
      "Aucun graphe de mémoire n'est câblé sur ce projet — la dérive store ↔ graphe "
      + 'ne se mesure qu\'avec un backend graphe configuré (Neo4j).',
      'grimoire memory status',
    ));
    return;
  }
  if (parity.error) {
    wrap.append(text('p', 'lbl', 'sonde du graphe en échec : ' + parity.error));
    return;
  }

  const graph = document.createElement('div');
  graph.className = 'me-graph';
  const node = (label, value) => {
    const box = document.createElement('div');
    box.className = 'me-graph-node';
    box.append(text('div', 'val mono', value == null ? '—' : String(value)), text('div', 'lbl', label));
    return box;
  };
  graph.append(node('store', parity.storeEntries));
  graph.append(text('div', 'me-graph-arrow', '↔'));
  graph.append(node('graphe', parity.graphMemories));
  graph.append(text('div', 'me-graph-arrow', '↔'));
  graph.append(node('vecteurs', parity.graphVectorObjects));
  wrap.append(graph);
  wrap.append(row(dot(parity.ok ? 'ok' : 'warn'), text('span', null, parity.ok ? 'store et graphe alignés' : `dérive : ${parity.drift}`)));
}

function renderLayers(wrap, ctx, memory) {
  const layers = memory.layers || [];
  if (!layers.length) {
    wrap.append(ctx.empty('Couches', 'Aucune couche déclarée — la config mémoire du projet est absente ou illisible.', 'grimoire memory status', undefined, { art: 'i-empty-memory' }));
    return;
  }
  const table = document.createElement('table');
  table.className = 'tbl';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Couche', 'État', 'Backend', 'Objet', 'Écarts']) headRow.append(text('th', null, label));
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const layer of layers) {
    const tr = document.createElement('tr');
    tr.append(text('td', null, layer.label || layer.id));
    const stateCell = document.createElement('td');
    stateCell.append(row(dot(STATE_DOT[layer.state] || ''), text('span', null, STATE_WORD[layer.state] || layer.state)));
    tr.append(stateCell);
    tr.append(text('td', 'lbl', layer.backend || '—'));
    tr.append(text('td', null, layer.purpose || ''));
    tr.append(text('td', 'lbl', (layer.gaps || []).join(', ') || '—'));
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
}

function renderArchitecture(wrap, ctx, memory) {
  wrap.className += ' me-arch';
  wrap.append(text('h3', null, `Profil : ${memory.layerProfile || 'non résolu'}`));
  wrap.append(text('p', null,
    "Le Memory OS du kit organise sept couches — court terme, épisodique, sémantique, "
    + 'procédurale, code graph, tâches et projet — chacune servie par un backend '
    + "qui peut être local ou distant. Cette page ne montre l'explication qu'ici : "
    + 'le store et le graphe restent la vue par défaut.',
  ));
  for (const layer of memory.layers || []) {
    const block = document.createElement('div');
    block.style.marginTop = '10px';
    block.append(text('div', null, layer.label || layer.id), text('div', 'lbl', layer.purpose || ''));
    wrap.append(block);
  }
}

// ── Flotte : agrégation mémoire multi-projets (#172) ────────────────────────
//
// « Ce projet / Tous les projets » vit sur le sélecteur Zoom du docbar — le
// même widget que Piloter (Flotte/Projet) et Concevoir (Projet/Workflow/
// Nœud) — pour ne pas ajouter un second contrôle de portée dans la page.
// `mount()` le pilote : construit ici, il ne s'affiche que pendant que
// l'onglet Flotte est actif.

// ── Fraîcheur : le vocabulaire du badge ─────────────────────────────────────
//
// Calculée côté serveur, à chaque lecture (`memory/knowledge.py`) : le cockpit
// n'en décide rien, il l'affiche. Mot + icône + teinte, jamais la teinte seule.
const FRESH = {
  a_jour:          { label: 'à jour',          cls: '',     icon: 'check' },
  perime:          { label: 'périmé',          cls: 'warn', icon: 'alert' },
  source_disparue: { label: 'source disparue', cls: 'bad',  icon: 'cross' },
  non_verifie:     { label: 'non vérifié',     cls: 'acc',  icon: 'info' },
  hypothese:       { label: 'hypothèse',       cls: '',     icon: 'clock' },
};

function freshnessBadge(entry) {
  const spec = FRESH[entry.freshness] || { label: entry.freshness || 'inconnu', cls: 'acc', icon: 'info' };
  const badge = document.createElement('span');
  badge.className = `chip pill ${spec.cls}`.trim();
  badge.dataset.freshness = entry.freshness || '';
  badge.append(icon(spec.icon), document.createTextNode(spec.label));
  const why = entry.provenance && entry.provenance.fraicheur_raison;
  if (why) badge.title = why;
  return badge;
}

function entryDate(entry) {
  const fact = entry.provenance || {};
  const pick = fact.date_source || fact.date_confirmation || fact.date_indexation || '';
  const kind = fact.date_source ? 'source' : (fact.date_confirmation ? 'confirmé' : 'indexé');
  return pick ? `${kind} ${String(pick).slice(0, 10)}` : '';
}

const FILE_KINDS = new Set(['fichier', 'doc', 'code']);

function sourceNode(ctx, entry, canLink) {
  const fact = entry.provenance || {};
  const source = fact.source || {};
  const label = entry.source || 'inconnue';
  const linkable = canLink && FILE_KINDS.has(source.kind) && source.uri;
  if (!linkable) {
    const span = text('span', 'me-src', label);
    span.prepend(icon(source.kind === 'tâche' ? 'board' : 'tag'));
    span.title = `source : ${source.kind || 'inconnue'}`;
    return span;
  }
  const [file, line] = String(source.uri).split(/:(\d+)$/);
  const btn = text('button', 'me-src', label);
  btn.type = 'button';
  btn.prepend(icon('link'));
  btn.title = 'Ouvrir dans Source';
  btn.addEventListener('click', (evt) => {
    evt.stopPropagation();
    ctx.goto('source', line ? { file, line: Number(line) } : { file });
  });
  return btn;
}

function factInspector(ctx, entry) {
  const fact = entry.provenance || {};
  const source = fact.source || {};
  const list = document.createElement('dl');
  list.className = 'me-fact';
  const add = (k, v) => { if (v) list.append(text('dt', null, k), text('dd', k === 'empreinte' ? 'mono' : null, v)); };
  add('fraîcheur', (FRESH[entry.freshness] || {}).label || entry.freshness);
  add('raison', fact.fraicheur_raison);
  add('source', `${source.kind || 'inconnue'}${source.uri ? ' — ' + source.uri : ''}`);
  add('empreinte', source.empreinte);
  add('date de la source', fact.date_source);
  add('date d’indexation', fact.date_indexation);
  add('dernière confirmation', fact.date_confirmation);
  add('changement constaté', fact.date_changement);
  add('cycle', fact.cycle === 'remplacé' ? `remplacé par ${fact.remplace_par}` : fact.cycle);
  add('producteur', fact.producteur);
  add('schéma', fact.schema);
  return list;
}

function fleetOverviewTable(ctx, data) {
  const wrap = document.createElement('div');
  if (!data || !data.projects.length) {
    wrap.append(ctx.empty(
      'Flotte',
      "Aucun projet lisible pour cette portée — le registre de la machine "
      + "(`grimoire cockpit list`) est peut-être vide, ou aucun projet "
      + "sélectionné ne s'y trouve.",
      'grimoire cockpit list',
      undefined,
      { art: 'i-empty-fleet' },
    ));
    return wrap;
  }
  const table = document.createElement('table');
  table.className = 'tbl';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Projet', 'Backend configuré', 'Backend résolu', 'Entrées', 'Dernière écriture', 'Index lexical', 'État']) {
    headRow.append(text('th', null, label));
  }
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const p of data.projects) {
    const tr = document.createElement('tr');
    tr.append(text('td', null, p.name || p.slug));
    tr.append(text('td', 'lbl', p.configuredBackend || '—'));
    tr.append(text('td', 'lbl', p.resolvedBackend || '—'));
    tr.append(text('td', 'mono', p.entries == null ? '—' : String(p.entries)));
    tr.append(text('td', 'lbl mono', p.lastWrite || '—'));
    tr.append(text('td', 'lbl', p.lexicalIndex || '—'));
    const stateCell = document.createElement('td');
    if (p.reason) {
      stateCell.append(row(dot('bad'), text('span', 'lbl', p.reason)));
    } else {
      stateCell.append(row(dot(STATE_DOT[p.state] || ''), text('span', null, STATE_WORD[p.state] || p.state)));
    }
    tr.append(stateCell);
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  wrap.append(text('p', 'me-summary',
    `${data.summary.readable}/${data.summary.count} projet(s) lisible(s) — ${data.summary.totalEntries} entrée(s) au total.`));
  return wrap;
}

function fleetSearchResults(ctx, data, canLink) {
  const wrap = document.createElement('div');
  wrap.className = 'me-results';
  if (!data) {
    wrap.append(text('p', 'lbl', 'Recherche indisponible.'));
    return wrap;
  }
  if (!data.query) {
    wrap.append(text('p', 'lbl', 'Saisissez un terme puis « Rechercher ».'));
    return wrap;
  }
  if (!data.results.length) {
    wrap.append(ctx.empty('Recherche', `Aucun résultat pour « ${data.query} ».`, '', undefined, { compact: true, art: 'i-empty-search' }));
  }
  for (const entry of data.results) {
    const item = document.createElement('div');
    item.className = 'me-result';
    item.tabIndex = 0;
    item.setAttribute('role', 'button');
    const who = row(text('span', 'chip', entry.projectName || entry.projectSlug), freshnessBadge(entry));
    const shown = formatScore(entry.score, entry.scoreKind);
    const scoreNode = text('span', 'lbl mono', shown.text);
    scoreNode.title = shown.title;
    const head = row(who, scoreNode);
    head.className += ' me-result-head';
    const replaced = entry.provenance && entry.provenance.cycle === 'remplacé';
    const body = text('div', 'me-result-text' + (replaced ? ' me-replaced' : ''), (entry.text || '').slice(0, 220));
    const meta = document.createElement('div');
    meta.className = 'me-result-meta';
    meta.append(sourceNode(ctx, entry, canLink));
    const when = entryDate(entry);
    if (when) meta.append(text('span', null, when));
    if (replaced) meta.append(text('span', null, 'remplacé'));
    item.append(head, body, meta);
    const openInspector = () => {
      ctx.inspector.replaceChildren(
        text('h3', null, entry.projectName || entry.projectSlug),
        text('div', 'lbl mono', entry.id),
        text('p', null, entry.text || ''),
        factInspector(ctx, entry),
        text('div', 'lbl', `${shown.text} — tags : ${(entry.tags || []).join(', ') || '—'}`),
      );
    };
    item.addEventListener('click', openInspector);
    item.addEventListener('keydown', (evt) => { if (evt.key === 'Enter' || evt.key === ' ') { evt.preventDefault(); openInspector(); } });
    wrap.append(item);
  }
  const hidden = data.projects.reduce((n, p) => n + (p.hiddenStale || 0), 0);
  if (hidden) {
    wrap.append(text('p', 'lbl', `${hidden} résultat(s) écarté(s) : source disparue.`));
  }
  const failed = data.projects.filter((p) => p.reason);
  if (failed.length) {
    wrap.append(text('p', 'lbl', 'Projets non interrogés : ' + failed.map((p) => `${p.name} (${p.reason})`).join(', ')));
  }
  return wrap;
}

function renderFleet(wrap, ctx, state) {
  const h3 = text('h3', null, 'Flotte — mémoire multi-projets');
  h3.dataset.term = 'cockpit';
  wrap.append(h3);
  wrap.append(text('p', 'lbl',
    "Lecture seule, par projet du registre de la machine — jamais de fusion "
    + "des stores : chaque ligne, chaque résultat porte son propre projet."));

  const tableHost = document.createElement('div');
  wrap.append(tableHost);

  const searchWrap = document.createElement('div');
  searchWrap.className = 'me-search';
  const input = document.createElement('input');
  input.className = 'input';
  input.type = 'search';
  input.placeholder = 'Rechercher dans la mémoire…';
  const searchBtn = text('button', 'btn', 'Rechercher');
  searchBtn.type = 'button';
  searchWrap.append(input, searchBtn);
  wrap.append(searchWrap);

  // Ce qui n'est pas servi par défaut reste atteignable : une décision remplacée
  // ou une source disparue ne s'efface pas, elle se demande.
  const options = row();
  options.classList.add('me-opts');
  const toggle = (label) => {
    const box = document.createElement('input');
    box.type = 'checkbox';
    const lbl = document.createElement('label');
    lbl.className = 'lbl me-opt';
    lbl.append(box, document.createTextNode(label));
    options.append(lbl);
    return box;
  };
  const superseded = toggle('Décisions remplacées');
  const stale = toggle('Sources disparues');
  wrap.append(options);

  const resultsHost = document.createElement('div');
  wrap.append(resultsHost);

  const projectsParam = () => (state.scope === 'all' ? 'all' : undefined);

  const loadOverview = async () => {
    tableHost.replaceChildren(...ctx.loading('Chargement…', 3));
    const data = await ctx.api.memoryOverview(projectsParam()).catch(() => null);
    tableHost.replaceChildren(fleetOverviewTable(ctx, data));
  };

  const runSearch = async () => {
    const q = input.value.trim();
    if (!q) {
      resultsHost.replaceChildren(fleetSearchResults(ctx, { query: '', results: [], projects: [] }));
      return;
    }
    resultsHost.replaceChildren(text('p', 'lbl', 'Recherche…'));
    const data = await ctx.api.memorySearch(q, projectsParam(), {
      includeSuperseded: superseded.checked, includeStale: stale.checked,
    }).catch(() => null);
    resultsHost.replaceChildren(fleetSearchResults(ctx, data, state.scope === 'this'));
  };

  searchBtn.addEventListener('click', runSearch);
  input.addEventListener('keydown', (evt) => { if (evt.key === 'Enter') runSearch(); });
  superseded.addEventListener('change', runSearch);
  stale.addEventListener('change', runSearch);

  loadOverview();
  resultsHost.replaceChildren(fleetSearchResults(ctx, { query: '', results: [], projects: [] }));

  // Le zoom du docbar pilote `state.scope` ; `draw()` (mount) rappelle
  // `renderFleet` à chaque bascule, donc `loadOverview` ci-dessus reflète
  // déjà la portée courante à chaque montage.
}

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Mémoire']);

  const memory = await ctx.api.memoryStatus().catch(() => null);

  if (!memory) {
    ctx.docbar.setViews([], null);
    ctx.docbar.setZoom([], null, () => {});
    root.append(ctx.empty(
      'Mémoire',
      "L'API mémoire du projet est injoignable — vérifiez l'hôte local "
      + '(`grimoire serve` / `grimoire cockpit serve`).',
      'grimoire memory status',
    ));
    ctx.dock.echo('grimoire memory status');
    ctx.inspector.replaceChildren(text('p', 'lbl', 'Aucune entrée à inspecter.'));
    return;
  }

  // Sans mémoire propre, ce projet ne montre ni Store, ni Graphe, ni Couches
  // — mais la Flotte reste utile : c'est peut-être exactement le projet
  // qu'on veut voir « en échec » depuis un AUTRE projet déjà initialisé.
  const hasOwnMemory = memory.state !== 'uninitialized';
  let view = hasOwnMemory ? 'store' : 'flotte';
  const fleetState = { scope: 'this' };

  const views = hasOwnMemory
    ? [{ id: 'store', label: 'Store' }, { id: 'graphe', label: 'Graphe' },
       { id: 'couches', label: 'Couches' }, { id: 'architecture', label: 'Architecture' },
       { id: 'flotte', label: 'Flotte' }]
    : [{ id: 'flotte', label: 'Flotte' }];
  const setView = (id) => { view = id; draw(); };

  const draw = () => {
    root.replaceChildren();
    // `setViews` doit être rappelé À CHAQUE `draw()` (comme `executer.js` et
    // `concevoir.js`), sinon le shell ne remet jamais `aria-pressed` à jour
    // sur les boutons existants : un clic sur « Couches » changeait bien le
    // contenu mais laissait « Store » visuellement actif — régression
    // constatée à la revue du 2026-09-14.
    ctx.docbar.setViews(views, view, setView);
    const wrap = document.createElement('div');
    wrap.className = 'me-wrap';
    if (view === 'store') renderStore(wrap, ctx, memory);
    else if (view === 'graphe') renderGraph(wrap, ctx, memory);
    else if (view === 'couches') renderLayers(wrap, ctx, memory);
    else if (view === 'architecture') renderArchitecture(wrap, ctx, memory);
    else if (view === 'flotte') renderFleet(wrap, ctx, fleetState);
    root.append(wrap);

    if (view === 'flotte') {
      ctx.docbar.setZoom(
        [{ id: 'this', label: 'Ce projet' }, { id: 'all', label: 'Tous les projets' }],
        fleetState.scope,
        (id) => { fleetState.scope = id; draw(); },
      );
    } else {
      ctx.docbar.setZoom([], null, () => {});
    }
  };

  draw();
  if (hasOwnMemory) {
    // Aucune route ne sert le détail d'une entrée individuelle du store — seul
    // son statut agrégé (`/api/memory/status`) existe côté vue de travail. Le
    // dire honnêtement plutôt que fabriquer une liste d'entrées inspectables.
    // L'onglet Flotte, lui, ouvre un vrai résultat de recherche dans ce même
    // panneau (voir `fleetSearchResults`).
    ctx.inspector.replaceChildren(text('p', 'lbl',
      "L'inspecteur d'entrée individuelle n'a pas de route dédiée côté store — "
      + "cliquez un résultat de l'onglet Flotte pour inspecter une entrée réelle."));
  } else {
    ctx.inspector.replaceChildren(text('p', 'lbl', 'Aucune entrée à inspecter — cliquez un résultat de recherche dans l’onglet Flotte.'));
  }
  ctx.dock.echo('grimoire memory status');
}
