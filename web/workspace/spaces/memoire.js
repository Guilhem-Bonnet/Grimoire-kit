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

const STYLE_ID = 'me-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .me-wrap { padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-5); }
    .me-kpi { display: flex; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); overflow: hidden; }
    .me-kpi-item { flex: 1; padding: var(--sp-3) var(--sp-4); border-left: 1px solid var(--line); }
    .me-kpi-item:first-child { border-left: 0; }
    .me-kpi-val { font-family: var(--mono); font-size: var(--t-xl); font-weight: 600; color: var(--ink); }
    .me-kpi-lbl { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .me-graph { display: flex; gap: var(--sp-4); align-items: center; padding: var(--sp-4); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .me-graph-node { flex: 1; text-align: center; padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); }
    .me-graph-node .val { font-family: var(--mono); font-size: var(--t-l); color: var(--ink); }
    .me-graph-arrow { color: var(--ink3); font-size: var(--t-l); }
    .me-table { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .me-table th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--line); }
    .me-table td { padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
    .me-arch p { max-width: 640px; color: var(--ink2); }
    .me-search { display: flex; gap: var(--sp-2); max-width: 480px; }
    .me-search input { flex: 1; }
    .me-results { display: flex; flex-direction: column; gap: var(--sp-2); }
    .me-result { border: 1px solid var(--line); border-radius: var(--r); padding: var(--sp-2) var(--sp-3); background: var(--e1); cursor: pointer; }
    .me-result:hover, .me-result:focus-visible { background: var(--e2); outline: none; }
    .me-result .me-result-head { justify-content: space-between; margin-bottom: 4px; }
    .me-result .me-result-text { color: var(--ink2); font-size: var(--t-s); }
    .me-summary { color: var(--ink3); font-size: var(--t-min); }
  `;
  document.head.append(style);
}

const dot = (cls) => Object.assign(document.createElement('span'), { className: 'dot' + (cls ? ' ' + cls : '') });
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

function kpiCard(items) {
  const card = document.createElement('div');
  card.className = 'me-kpi';
  for (const item of items) {
    const cell = document.createElement('div');
    cell.className = 'me-kpi-item';
    cell.append(text('div', 'me-kpi-val mono', item.value), text('div', 'me-kpi-lbl', item.label));
    card.append(cell);
  }
  return card;
}

function renderStore(wrap, ctx, memory) {
  wrap.append(kpiCard([
    { value: memory.entries == null ? '—' : String(memory.entries), label: 'entrées' },
    { value: memory.configuredBackend || '—', label: 'backend configuré' },
    { value: memory.resolvedBackend || '—', label: 'backend résolu' },
    { value: STATE_WORD[memory.state] || memory.state, label: 'état' },
  ]));

  const status = document.createElement('div');
  status.append(row(dot(STATE_DOT[memory.state] || ''), text('span', null, `store ${STATE_WORD[memory.state] || memory.state}`)));
  wrap.append(status);

  if (memory.error) {
    wrap.append(text('p', 'lbl', 'erreur best-effort : ' + memory.error));
  }

  if (memory.detail && Object.keys(memory.detail).length) {
    const table = document.createElement('table');
    table.className = 'me-table';
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
    wrap.append(ctx.empty('Couches', 'Aucune couche déclarée — la config mémoire du projet est absente ou illisible.', 'grimoire memory status'));
    return;
  }
  const table = document.createElement('table');
  table.className = 'me-table';
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

function fleetOverviewTable(ctx, data) {
  const wrap = document.createElement('div');
  if (!data || !data.projects.length) {
    wrap.append(ctx.empty(
      'Flotte',
      "Aucun projet lisible pour cette portée — le registre de la machine "
      + "(`grimoire cockpit list`) est peut-être vide, ou aucun projet "
      + "sélectionné ne s'y trouve.",
      'grimoire cockpit list',
    ));
    return wrap;
  }
  const table = document.createElement('table');
  table.className = 'me-table';
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

function fleetSearchResults(ctx, data) {
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
    wrap.append(text('p', 'lbl', `Aucun résultat pour « ${data.query} ».`));
  }
  for (const entry of data.results) {
    const item = document.createElement('div');
    item.className = 'me-result';
    item.tabIndex = 0;
    item.setAttribute('role', 'button');
    const head = row(
      text('span', 'chip', entry.projectName || entry.projectSlug),
      text('span', 'lbl mono', `score ${Number(entry.score || 0).toFixed(3)}`),
    );
    head.className += ' me-result-head';
    item.append(head, text('div', 'me-result-text', (entry.text || '').slice(0, 220)));
    const openInspector = () => {
      ctx.inspector.replaceChildren(
        text('h3', null, entry.projectName || entry.projectSlug),
        text('div', 'lbl mono', entry.id),
        text('p', null, entry.text || ''),
        text('div', 'lbl', `score : ${Number(entry.score || 0).toFixed(3)} — tags : ${(entry.tags || []).join(', ') || '—'}`),
      );
    };
    item.addEventListener('click', openInspector);
    item.addEventListener('keydown', (evt) => { if (evt.key === 'Enter' || evt.key === ' ') { evt.preventDefault(); openInspector(); } });
    wrap.append(item);
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

  const resultsHost = document.createElement('div');
  wrap.append(resultsHost);

  const projectsParam = () => (state.scope === 'all' ? 'all' : undefined);

  const loadOverview = async () => {
    tableHost.replaceChildren(text('p', 'lbl', 'Chargement…'));
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
    const data = await ctx.api.memorySearch(q, projectsParam()).catch(() => null);
    resultsHost.replaceChildren(fleetSearchResults(ctx, data));
  };

  searchBtn.addEventListener('click', runSearch);
  input.addEventListener('keydown', (evt) => { if (evt.key === 'Enter') runSearch(); });

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
