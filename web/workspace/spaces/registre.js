// Configuration › Projets (P35) : entretenir le registre des projets du cockpit.
//
// Le registre est l'état de la machine (`~/.grimoire/cockpit/registry.json`),
// pas un dépôt : ajouter, scanner, retirer et purger reprennent `grimoire
// cockpit add|scan|remove|prune` par `POST /api/projects/<geste>`
// (`cockpit_registry_routes.py`). Les libellés suivent la maquette Flotte
// (« Scanner un dossier… », « Ajouter un projet existant… », « Retirer du
// registre… » et sa note) ; la rubrique elle-même n'est dessinée par aucune
// maquette : elle compose les primitives de la coque (en-tête, tableau, badge,
// retour d'action) dans le style Douce.
//
// Retirer et purger se font en deux temps, dans la page, jamais par `confirm()` :
// un aperçu (rien n'est écrit) puis la confirmation. Aucun des deux ne touche au
// dossier d'un projet : l'écran le répète à chaque fois. Le projet que ce
// cockpit sert ne se retire pas d'ici (le serveur refuse, le bouton le dit).

import { actionStatus, badge, failureText, icon, listNav, monogram, setActionStatus, spaceHead } from '../shell.js';

const STYLE_ID = 'rg-styles';
const FOLDER_NOTE = 'Retirer ne touche pas au dossier, seulement au registre du cockpit.';

/** Ce qui survit à un redessin : le panneau ouvert et le dernier retour. */
const view = { panel: '', notice: null, removing: '' };

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .rg-wrap { padding: var(--sp-6); max-width: 1100px; width: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: var(--sp-4); }
    .rg-bar { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; }
    .rg-note { margin: 0 0 0 auto; color: var(--ink2); font-size: var(--t-s); }
    .rg-card { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); border: 1px solid var(--line);
      border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); }
    .rg-card h3 { margin: 0; font-size: var(--t-m); line-height: 20px; font-weight: 600; color: var(--ink); }
    .rg-sub { margin: 0; color: var(--ink2); font-size: var(--t-s); overflow-wrap: anywhere; }
    .rg-line { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
    .rg-line input[type="text"] { flex: 1 1 320px; min-width: 0; min-height: var(--h-ctl); padding: 4px 9px; border: 1px solid var(--line);
      border-radius: var(--r); font: inherit; }
    .rg-line input[type="text"]:focus { border-color: var(--acc); }
    .rg-check { display: flex; gap: var(--sp-2); align-items: center; font-size: var(--t-s); color: var(--ink2); }
    .rg-check input { accent-color: var(--acc); }
    .rg-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
    .rg-list li { display: flex; gap: var(--sp-2); align-items: center; padding: 7px 0; border-top: 1px solid var(--line); min-width: 0; }
    .rg-list li:first-child { border-top: 0; }
    .rg-list .rg-path { flex: 1 1 auto; }
    .rg-path { font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
    .rg-name { display: flex; gap: var(--sp-2); align-items: center; min-width: 0; font-weight: 500; }
    .rg-name span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .rg-wrap .tbl { table-layout: fixed; }
    .rg-wrap .tbl td { overflow: hidden; }
    .rg-wrap .tbl td > .rg-path { display: block; }
    .rg-confirm td { background: var(--hover); }
    .rg-confirm .rg-card { box-shadow: none; }
    @media (max-width: 900px) { .rg-wrap { padding: var(--sp-3); } .rg-note { margin-left: 0; } }
    @media (max-width: 700px) { .rg-wrap .tbl { table-layout: auto; } }
  `;
  document.head.append(style);
}

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function button(label, { pri = false, iconName = '', role = '' } = {}) {
  const node = el('button', pri ? 'btn pri' : 'btn', null, { type: 'button' });
  if (role) node.dataset.role = role;
  if (iconName) node.append(icon(iconName));
  node.append(el('span', null, label));
  return node;
}

function formula(command) {
  const node = el('button', 'formula', null, { type: 'button', title: 'Copier la commande' });
  node.append(el('b', null, '›'), command);
  node.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(command); node.title = 'Commande copiée'; } catch { node.title = 'Copie refusée : recopie la commande'; }
  });
  return node;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function stateBadge(project, home) {
  if (project.slug === home) return badge('servi par ce cockpit', 'acc');
  if (!project.exists) return badge('dossier absent', 'warn');
  return project.is_grimoire ? badge('présent', 'ok') : badge('sans marqueur', '');
}

// ── Panneaux : ajouter, scanner, purger ─────────────────────────────────────

function addPanel(ctx, reload) {
  const card = el('section', 'rg-card', null, { 'data-role': 'registry-add', 'aria-label': 'Ajouter un projet existant' });
  const input = el('input', null, null, { type: 'text', 'aria-label': 'Chemin du dossier', placeholder: '~/chemin/du/projet', 'data-role': 'registry-add-path' });
  const go = button('Ajouter au registre', { pri: true, role: 'registry-add-submit' });
  const cancel = button('Fermer');
  const status = actionStatus();
  cancel.addEventListener('click', () => { view.panel = ''; reload(); });
  go.addEventListener('click', async () => {
    if (!input.value.trim()) { setActionStatus(status, 'Refusé : écris le chemin d’un dossier.', 'bad'); input.focus(); return; }
    go.disabled = true;
    setActionStatus(status, 'Ajout…');
    ctx.dock.echo(`grimoire cockpit add ${input.value.trim()}`);
    try {
      const done = await ctx.api.registryAdd(input.value.trim());
      view.panel = '';
      view.notice = { tone: 'ok', text: done.already ? `${done.path} était déjà au registre (${done.slug}).` : `${done.path} ajouté au registre (${done.slug}).` };
      reload();
    } catch (error) {
      go.disabled = false;
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  const line = el('div', 'rg-line');
  line.append(input, go, cancel);
  card.append(el('h3', null, 'Ajouter un projet existant'),
    el('p', 'rg-sub', 'Un dossier sous ton répertoire personnel ou à côté d’un projet déjà suivi. Rien n’est écrit dans le dossier.'),
    line, status);
  queueMicrotask(() => { if (input.isConnected) input.focus(); });
  return card;
}

function scanPanel(ctx, reload) {
  const card = el('section', 'rg-card', null, { 'data-role': 'registry-scan', 'aria-label': 'Scanner un dossier' });
  const input = el('input', null, null, { type: 'text', 'aria-label': 'Dossier à scanner', placeholder: '~/Projets', 'data-role': 'registry-scan-root' });
  const go = button('Scanner', { pri: true, iconName: 'search', role: 'registry-scan-submit' });
  const cancel = button('Fermer');
  const status = actionStatus();
  const results = el('ul', 'rg-list', null, { 'data-role': 'registry-scan-results' });
  cancel.addEventListener('click', () => { view.panel = ''; reload(); });
  const drawResults = (payload) => {
    results.replaceChildren();
    for (const candidate of payload.candidates) {
      const item = el('li');
      item.dataset.path = candidate.path;
      item.append(el('span', 'rg-name', candidate.name), el('span', 'rg-path', candidate.path, { title: candidate.path }));
      if (candidate.registered) {
        item.append(badge('déjà au registre', ''));
      } else {
        const add = button('Ajouter', { role: 'registry-scan-add' });
        add.addEventListener('click', async () => {
          add.disabled = true;
          ctx.dock.echo(`grimoire cockpit add ${candidate.path}`);
          try {
            await ctx.api.registryAdd(candidate.path);
            candidate.registered = true;
            view.notice = { tone: 'ok', text: `${candidate.path} ajouté au registre.` };
            drawResults(payload);
            setActionStatus(status, `${candidate.name} ajouté au registre.`, 'ok');
            reload({ keepPanel: true });
          } catch (error) {
            add.disabled = false;
            setActionStatus(status, failureText(error), 'bad');
          }
        });
        item.append(add);
      }
      results.append(item);
    }
  };
  go.addEventListener('click', async () => {
    go.disabled = true;
    setActionStatus(status, 'Scan…');
    ctx.dock.echo(`grimoire cockpit scan ${input.value.trim() || '~'}`);
    try {
      const payload = await ctx.api.registryScan(input.value.trim());
      const fresh = payload.candidates.filter((c) => !c.registered).length;
      setActionStatus(status, payload.candidates.length
        ? `${plural(payload.candidates.length, 'projet trouvé', 'projets trouvés')} sous ${payload.root}, ${plural(fresh, 'à ajouter', 'à ajouter')}.`
        : `Aucun projet sous ${payload.root}.`, payload.candidates.length ? 'ok' : '');
      drawResults(payload);
    } catch (error) {
      setActionStatus(status, failureText(error), 'bad');
    }
    go.disabled = false;
  });
  const line = el('div', 'rg-line');
  line.append(input, go, cancel);
  card.append(el('h3', null, 'Scanner un dossier'),
    el('p', 'rg-sub', 'Le scan propose, tu choisis : rien n’est ajouté sans ton clic.'),
    line, status, results);
  queueMicrotask(() => { if (input.isConnected) input.focus(); });
  return card;
}

function prunePanel(ctx, reload) {
  const card = el('section', 'rg-card', null, { 'data-role': 'registry-prune', 'aria-label': 'Purger le registre' });
  const stale = el('input', null, null, { type: 'checkbox', 'data-role': 'registry-prune-stale' });
  const check = el('label', 'rg-check');
  check.append(stale, el('span', null, 'Inclure les dossiers présents sans marqueur Grimoire'));
  const list = el('ul', 'rg-list', null, { 'data-role': 'registry-prune-list' });
  const status = actionStatus();
  const actions = el('div', 'rg-line');
  let candidates = [];
  const confirm = button('Retirer du registre', { pri: true, iconName: 'trash', role: 'registry-prune-confirm' });
  const cancel = button('Annuler');
  cancel.addEventListener('click', () => { view.panel = ''; reload(); });
  const preview = async () => {
    setActionStatus(status, 'Aperçu…');
    confirm.disabled = true;
    ctx.dock.echo(`grimoire cockpit prune --dry-run${stale.checked ? ' --stale' : ''}`);
    try {
      const payload = await ctx.api.registryPrune({ stale: stale.checked });
      candidates = payload.candidates;
      list.replaceChildren(...candidates.map((c) => {
        const item = el('li');
        item.dataset.slug = c.slug;
        item.append(el('span', 'rg-name', c.name || c.slug), el('span', 'rg-path', c.path, { title: c.path }));
        return item;
      }));
      confirm.querySelector('span').textContent = candidates.length ? `Retirer ${plural(candidates.length, 'entrée', 'entrées')} du registre` : 'Retirer du registre';
      confirm.disabled = !candidates.length;
      setActionStatus(status, candidates.length
        ? `Aperçu : ${plural(candidates.length, 'entrée', 'entrées')} sur ${payload.total} partirai${candidates.length > 1 ? 'ent' : 't'}. Rien n’est encore retiré. ${payload.note}`
        : `Registre propre : aucune entrée à retirer sur ${payload.total}.`, candidates.length ? '' : 'ok');
    } catch (error) {
      setActionStatus(status, failureText(error), 'bad');
    }
  };
  stale.addEventListener('change', preview);
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    setActionStatus(status, 'Retrait…');
    ctx.dock.echo(`grimoire cockpit prune -y${stale.checked ? ' --stale' : ''}`);
    try {
      const done = await ctx.api.registryPrune({ confirm: true, stale: stale.checked, slugs: candidates.map((c) => c.slug) });
      view.panel = '';
      view.notice = { tone: 'ok', text: `${plural(done.removed.length, 'entrée retirée', 'entrées retirées')} du registre, ${done.remaining} conservée${done.remaining > 1 ? 's' : ''}. ${done.note}` };
      reload();
    } catch (error) {
      confirm.disabled = false;
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  actions.append(confirm, cancel);
  card.append(el('h3', null, 'Purger les entrées mortes'),
    el('p', 'rg-sub', 'Retire du registre les projets dont le dossier a disparu. ' + FOLDER_NOTE),
    check, status, list, actions);
  preview();
  return card;
}

// ── Retirer une entrée : aperçu puis confirmation, sur sa ligne ─────────────

function confirmRow(ctx, project, colspan, reload) {
  const tr = el('tr', 'rg-confirm', null, { 'data-role': 'registry-remove-confirm', 'data-slug': project.slug });
  const td = el('td', null, null, { colspan: String(colspan) });
  const card = el('div', 'rg-card');
  const status = actionStatus();
  const yes = button('Retirer du registre', { pri: true, iconName: 'trash', role: 'registry-remove-yes' });
  const no = button('Annuler');
  no.addEventListener('click', () => { view.removing = ''; reload(); });
  yes.disabled = true;
  ctx.api.registryRemove(project.slug, false).then((preview) => {
    if (ctx.signal.aborted) return;
    yes.disabled = false;
    setActionStatus(status, preview.note);
  }, (error) => setActionStatus(status, failureText(error), 'bad'));
  yes.addEventListener('click', async () => {
    yes.disabled = true;
    ctx.dock.echo(`grimoire cockpit remove ${project.slug}`);
    try {
      const done = await ctx.api.registryRemove(project.slug, true);
      view.removing = '';
      view.notice = { tone: 'ok', text: `« ${done.removed.name || done.removed.slug} » retiré du registre. ${done.note}` };
      reload();
    } catch (error) {
      yes.disabled = false;
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  const line = el('div', 'rg-line');
  line.append(yes, no);
  card.append(el('h3', null, `Retirer « ${project.name || project.slug} » du registre ?`),
    el('p', 'rg-sub', project.path), status, line);
  td.append(card);
  tr.append(td);
  return tr;
}

function projectsTable(ctx, data, home, reload) {
  const wrap = el('div', 'tbl-wrap');
  const tbl = el('table', 'tbl', null, { 'aria-label': 'Projets du registre' });
  const head = el('tr');
  [['Projet', '24%'], ['Chemin', ''], ['État', '180px'], ['', '132px']].forEach(([label, width]) => {
    const th = el('th', null, label, { scope: 'col' });
    if (width) th.style.width = width;
    head.append(th);
  });
  const thead = el('thead');
  thead.append(head);
  const body = el('tbody');
  for (const project of data.projects) {
    const tr = el('tr', null, null, { 'data-slug': project.slug });
    const name = el('span', 'rg-name');
    name.append(monogram(project.name || project.slug), el('span', null, project.name || project.slug, { title: project.slug }));
    const cells = [name, el('span', 'rg-path', project.path, { title: project.path }), stateBadge(project, home)];
    const remove = button('Retirer…', { iconName: 'trash', role: 'registry-remove' });
    if (project.slug === home) {
      remove.disabled = true;
      remove.title = 'C’est le projet que ce cockpit sert : il ne se retire pas d’ici.';
    } else {
      remove.addEventListener('click', () => { view.removing = project.slug; reload(); });
    }
    cells.push(remove);
    for (const cell of cells) {
      const td = el('td');
      td.append(cell);
      tr.append(td);
    }
    body.append(tr);
    if (view.removing === project.slug) body.append(confirmRow(ctx, project, 4, reload));
  }
  tbl.append(thead, body);
  wrap.append(tbl);
  return wrap;
}

export async function mount(root, ctx) {
  injectStyles();
  // P58 : j / k parcourent les projets du registre (`aria-current` sur la ligne
  // active) ; Entrée ouvre le projet dans la Flotte, comme une ligne de la Flotte.
  listNav(root, {
    rows: '.tbl tbody tr[data-slug]:not(.rg-confirm)', mark: 'aria-current', key: 'slug',
    onActivate: (row) => ctx.scope.set(row.dataset.slug, '#flotte'),
  });
  const draw = async ({ keepPanel = false } = {}) => {
    let data;
    try {
      data = await ctx.api.projects();
    } catch (error) {
      if (ctx.signal.aborted) return;
      root.replaceChildren(ctx.empty('Registre injoignable', `L'API locale n'a pas répondu : ${error.message}`, 'grimoire cockpit list'));
      return;
    }
    if (ctx.signal.aborted) return;
    const home = (ctx.host.status && ctx.host.status.home) || '';
    const dead = data.projects.filter((p) => !p.exists).length;
    const wrap = el('div', 'rg-wrap');
    const sub = data.projects.length
      ? `${plural(data.projects.length, 'projet suivi', 'projets suivis')} par ce cockpit · ${dead ? plural(dead, 'entrée morte', 'entrées mortes') : 'aucune entrée morte'}`
      : 'Le registre est vide.';
    wrap.append(spaceHead('Projets', sub, formula('grimoire cockpit list')));
    const bar = el('div', 'rg-bar');
    const scan = button('Scanner un dossier…', { iconName: 'search', role: 'registry-open-scan' });
    const add = button('Ajouter un projet existant…', { iconName: 'plus', role: 'registry-open-add' });
    const prune = button('Purger les entrées mortes…', { iconName: 'trash', role: 'registry-open-prune' });
    for (const [node, id] of [[scan, 'scan'], [add, 'add'], [prune, 'prune']]) {
      node.setAttribute('aria-expanded', String(view.panel === id));
      node.addEventListener('click', () => { view.panel = view.panel === id ? '' : id; view.notice = null; draw(); });
    }
    bar.append(scan, add, prune, el('p', 'rg-note', FOLDER_NOTE));
    wrap.append(bar);
    if (view.notice) {
      const notice = actionStatus(view.notice.text, view.notice.tone);
      notice.dataset.role = 'registry-notice';
      wrap.append(notice);
    }
    const panels = { add: addPanel, scan: scanPanel, prune: prunePanel };
    if (view.panel && panels[view.panel]) {
      const existing = keepPanel ? root.querySelector(`[data-role='registry-${view.panel}']`) : null;
      wrap.append(existing || panels[view.panel](ctx, draw));
    }
    if (data.projects.length) wrap.append(projectsTable(ctx, data, home, draw));
    else wrap.append(ctx.empty('Aucun projet suivi', 'Ajoute un projet existant ou scanne un dossier pour peupler le registre.', 'grimoire cockpit add <chemin>'));
    root.replaceChildren(wrap);
    ctx.dock.echo('grimoire cockpit list');
  };
  ctx.signal.addEventListener('abort', () => { view.removing = ''; });
  await draw();
}
