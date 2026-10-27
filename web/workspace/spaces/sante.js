// Savoir › Santé et carte — la santé de la mémoire et sa file d'hygiène (P25,
// maquette « Savoir — santé et carte »).
//
// Une lecture : `memory/quality` (workspace_hygiene.py), mesurée à chaque
// ouverture. La carte « Santé de la mémoire » rend chaque critère avec sa
// mesure : en échec, réussi ou inconnu — un critère qu'on ne peut pas mesurer
// est « inconnu » avec son motif, jamais un zéro. La « File d'hygiène » rend
// les mêmes constats que À traiter, chacun avec son geste.
//
// La « Carte par hall » (P73, maquette « Savoir — santé et carte ») rend la
// même mesure en surfaces : un pavé par hall, surface proportionnelle au
// nombre d'entrées classées (`measures.types`), forme = hall (le même langage
// que la légende « Lire la carte » du panneau de droite). Un clic ouvre la
// Recherche filtrée sur les types du hall.
//
// Refus tenus ici :
// - aucun bouton inerte : chaque geste ouvre d'abord un aperçu (ce qui sera
//   touché, pourquoi) puis donne la commande qui le fait, à copier ; aucune
//   écriture n'a de route sans export préalable, donc aucune ne part d'ici ;
// - la carte ne dessine que ce qui est mesuré : un scan incomplet le dit
//   (« comptes sur les N premières entrées »), un store vide dit sa place
//   vide ; la fraîcheur et les liens par entrée ne sont pas encore rendus,
//   la légende ne documente donc que la forme.
//
// API consommées : api.memoryQuality, api.projects.

import { actionStatus, badge, failureText, setActionStatus, spaceHead } from '../shell.js';

const STYLE_ID = 'sm-styles';

const STYLES = `
.sm-wrap { display: flex; flex-direction: column; gap: 14px; padding: 28px 32px; max-width: 1280px; min-width: 0; }
.sm-pick { display: flex; align-items: center; gap: 8px; color: var(--ink2); }
.sm-pick select { height: var(--h-ctl); padding: 0 9px; border: 1px solid var(--line); border-radius: var(--r); background-color: var(--card); color: var(--ink); }
.sm-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 14px; align-items: stretch; }
.sm-card { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow); min-width: 0; }
.sm-health { padding: 14px 16px; display: flex; flex-direction: column; }
.sm-cardhead { display: flex; align-items: baseline; gap: 4px 10px; flex-wrap: wrap; }
.sm-health .sm-cardhead { padding-bottom: 6px; }
.sm-cardhead h2 { margin: 0; font-size: 15px; line-height: 20px; font-weight: 600; }
.sm-cardhead .badge { margin-left: auto; }
.sm-crit { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto; gap: 10px; align-items: center;
  padding: 9px 0; border-top: 1px solid var(--line); }
.sm-mark { width: 10px; height: 10px; border-radius: 2px; background: var(--line); }
.sm-crit[data-state="fail"] .sm-mark { background: var(--bad-mark); }
.sm-crit[data-state="ok"] .sm-mark { background: var(--ok-mark); }
.sm-crit[data-state="unknown"] .sm-mark { background: transparent; border: 1.5px solid var(--ink3); }
.sm-text { display: flex; flex-direction: column; min-width: 0; }
.sm-label { font-weight: 500; }
.sm-detail { color: var(--ink2); font-size: 12px; overflow-wrap: anywhere; }
.sm-value { text-align: right; font-weight: 600; max-width: 180px; overflow-wrap: anywhere; }
.sm-crit[data-state="fail"] .sm-value { color: var(--bad); }
.sm-crit[data-state="unknown"] .sm-value { color: var(--ink2); font-weight: 500; }
.sm-map { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.sm-map .sm-sub { color: var(--ink2); font-size: 12px; }
.sm-slot { flex: 1; min-height: 160px; display: flex; align-items: center; justify-content: center; text-align: center;
  padding: 16px; border: 1px dashed var(--line); border-radius: var(--r); color: var(--ink2); }
.sm-tmap { display: flex; gap: 4px; flex: 1; min-height: 220px; }
/* Les surfaces suivent les comptes, mais aucun pavé ne descend sous une
 * largeur lisible : un store très déséquilibré garde ses petits halls nommés. */
.sm-tcol { display: flex; flex-direction: column; gap: 4px; min-width: 168px; }
.sm-tmap > .sm-tile { min-width: 128px; }
.sm-tile { appearance: none; font: inherit; text-align: left; cursor: pointer; border-radius: var(--r);
  padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 90px;
  overflow: hidden; box-sizing: border-box; background: var(--card); border: 1px solid var(--line2); color: var(--ink); }
.sm-tile:hover { border-color: var(--ink3); }
.sm-tile:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
.sm-tile[data-tone="acc"] { background: var(--acc-soft); border-color: var(--acc); }
.sm-tile[data-tone="warn"] { background: var(--warn-soft); border-color: var(--warn-mark); }
.sm-tile[data-tone="off"] { background: transparent; border-style: dashed; }
.sm-tile-head { display: flex; align-items: center; gap: 6px; min-width: 0; font-weight: 600; }
.sm-tile-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sm-tile-n { font-size: 24px; line-height: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sm-tile-sub { color: var(--ink2); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sm-shape { width: 12px; height: 12px; flex: none; fill: currentColor; color: var(--ink2); }
.sm-tile[data-tone="acc"] .sm-shape { color: var(--acc); }
.sm-tile[data-tone="warn"] .sm-shape { color: var(--warn-mark); }
.sm-partial { margin: 0; color: var(--ink2); font-size: 12px; }
.sm-legend { display: flex; flex-direction: column; gap: 14px; }
.sm-legend-head { display: flex; flex-direction: column; gap: 4px; }
.sm-legend-head h2 { margin: 0; font-size: 15px; font-weight: 600; }
.sm-legend-lead { margin: 0; color: var(--ink2); font-size: 12.5px; }
.sm-leg { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 10px; align-items: center; padding: 6px 0; }
.sm-leg .sm-shape { justify-self: center; }
.sm-leg-note { margin: 0; color: var(--ink2); font-size: 12px; }
.sm-hygiene { overflow: hidden; }
.sm-hygiene > .sm-cardhead { padding: 14px 16px 10px; }
.sm-cnt { color: var(--rub); font-weight: 600; }
.sm-hint { margin-left: auto; color: var(--ink2); font-size: 12px; }
.sm-list { list-style: none; margin: 0; padding: 0; }
.sm-hyg { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center;
  padding: 11px 16px; border-top: 1px solid var(--line); }
.sm-hyg[data-open="true"] { background: var(--hover); }
.sm-hyg-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sm-hyg-title { font-weight: 500; overflow-wrap: anywhere; }
.sm-hyg-reason { color: var(--ink2); font-size: 12.5px; overflow-wrap: anywhere; }
.sm-danger { color: var(--bad); }
.sm-preview { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: var(--r);
  background: var(--card); border: 1px solid var(--line); }
.sm-preview[hidden] { display: none; }
.sm-preview p { margin: 0; color: var(--ink2); overflow-wrap: anywhere; }
.sm-eyebrow { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink2); }
.sm-touched { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
.sm-touched li { overflow-wrap: anywhere; }
.sm-cmd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sm-cmd code { font-family: var(--mono); font-size: var(--t-s); padding: 4px 8px; border-radius: var(--r); background: var(--hover);
  color: var(--ink); overflow-wrap: anywhere; }
.sm-none { margin: 0; padding: 12px 16px 14px; border-top: 1px solid var(--line); color: var(--ink2); }
@container canvas (max-width: 760px) {
  .sm-wrap { padding: var(--sp-4); }
  .sm-grid { grid-template-columns: minmax(0, 1fr); }
  .sm-hyg { grid-template-columns: minmax(0, 1fr); }
  .sm-hint { margin-left: 0; flex-basis: 100%; }
  .sm-cardhead { flex-wrap: wrap; }
}
`;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.append(style);
}

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** La formule de la rubrique, copiable comme toutes les formules de la coque. */
function formula(command) {
  const button = el('button', 'formula', null, { type: 'button', title: 'Copier la commande' });
  button.append(el('b', null, '›'), command);
  button.addEventListener('click', () => {
    navigator.clipboard.writeText(command).then(
      () => { button.title = 'Commande copiée'; },
      () => { button.title = 'Copie refusée : recopie la commande'; },
    );
  });
  return button;
}

function verdictBadge(summary) {
  const { fail = 0, unknown = 0, total = 0 } = summary || {};
  if (fail) return badge(`${fail} critère${fail > 1 ? 's' : ''} sur ${total} en échec`, 'bad');
  if (unknown) return badge(`${plural(unknown, 'critère inconnu', 'critères inconnus')} sur ${total}`, '');
  return badge('tous les critères tiennent', 'ok');
}

function healthCard(report) {
  const card = el('section', 'sm-card sm-health', null, { 'aria-labelledby': 'sm-h-health' });
  const head = el('div', 'sm-cardhead');
  head.append(el('h2', null, 'Santé de la mémoire', { id: 'sm-h-health' }), verdictBadge(report.summary));
  card.append(head);
  const words = { fail: 'en échec', ok: 'réussi', unknown: 'inconnu' };
  for (const criterion of report.criteria || []) {
    const row = el('div', 'sm-crit');
    row.dataset.criterion = criterion.id;
    row.dataset.state = criterion.state;
    const mark = el('span', 'sm-mark', null, { role: 'img', 'aria-label': words[criterion.state] || criterion.state });
    mark.title = words[criterion.state] || criterion.state;
    const text = el('span', 'sm-text');
    text.append(el('span', 'sm-label', criterion.label), el('span', 'sm-detail', criterion.detail || ''));
    row.append(mark, text, el('span', 'sm-value', criterion.value));
    card.append(row);
  }
  return card;
}

// ── Carte par hall : la mesure en surfaces (P73) ───────────────────────────

/** Les halls : un regroupement de types classés, une forme, une teinte. */
const HALLS = [
  { id: 'faits', label: 'Faits et décisions', sub: 'hall des faits', shape: 'square', tone: 'acc',
    types: ['decision', 'convention', 'untyped'] },
  { id: 'evenements', label: 'Événements, échecs', sub: 'hall des événements', shape: 'circle', tone: 'warn',
    types: ['incident', 'story'] },
  { id: 'decouvertes', label: 'Découvertes', sub: 'hall des apprentissages', shape: 'diamond', tone: '',
    types: ['learning'] },
  { id: 'projections', label: 'Projections', sub: 'copies régénérables', shape: 'dashed', tone: 'off',
    types: ['code', 'docs', 'task', 'projection'] },
];

const TYPE_WORDS = {
  decision: ['décision', 'décisions'], convention: ['convention', 'conventions'],
  untyped: ['fait non typé', 'faits non typés'], incident: ['incident', 'incidents'],
  story: ['récit', 'récits'], learning: ['apprentissage', 'apprentissages'],
  code: ['code', 'code'], docs: ['doc', 'docs'], task: ['tâche', 'tâches'],
  projection: ['autre projection', 'autres projections'],
};

const SHAPES = {
  square: '<rect x="1" y="1" width="10" height="10"/>',
  circle: '<circle cx="6" cy="6" r="5"/>',
  diamond: '<path d="M6 .5 11.5 6 6 11.5.5 6z"/>',
  dashed: '<rect x="1.5" y="1.5" width="9" height="9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.2 2"/>',
};

/** La forme d'un hall, en SVG local (fill/stroke = currentColor, jamais une couleur). */
function shapeIcon(shape) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 12 12');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('sm-shape');
  svg.innerHTML = SHAPES[shape] || SHAPES.square;
  return svg;
}

/** Les halls mesurés : compte total et composition par type, dans l'ordre décroissant. */
function hallCounts(types) {
  const halls = [];
  for (const hall of HALLS) {
    const parts = hall.types
      .map((t) => ({ type: t, count: types[t] || 0 }))
      .filter((p) => p.count > 0);
    const count = parts.reduce((sum, p) => sum + p.count, 0);
    if (count) halls.push({ ...hall, count, parts });
  }
  return halls.sort((a, b) => b.count - a.count);
}

/** Un pavé de la carte : forme, hall, compte ; un clic ouvre la Recherche filtrée. */
function hallTile(ctx, hall, slug) {
  const noun = plural(hall.count, 'entrée', 'entrées');
  const tile = el('button', 'sm-tile', null, {
    type: 'button',
    'aria-label': `${hall.label} : ${noun} — ouvrir dans Recherche`,
  });
  tile.dataset.hall = hall.id;
  if (hall.tone) tile.dataset.tone = hall.tone;
  tile.style.flex = `${hall.count} 1 0`;
  const head = el('span', 'sm-tile-head');
  head.append(shapeIcon(hall.shape), el('span', null, hall.label));
  const detail = hall.parts.map((p) => `${p.count} ${TYPE_WORDS[p.type][p.count > 1 ? 1 : 0]}`).join(' · ');
  tile.append(head, el('span', 'sm-tile-n', String(hall.count)), el('span', 'sm-tile-sub', detail || hall.sub));
  tile.title = `${hall.sub} · ${detail}`;
  tile.addEventListener('click', () => ctx.goto('memoire', { types: hall.types.join(','), of: slug || '' }));
  return tile;
}

/** La carte par hall : surface = nombre d'entrées classées, mesurées par `memory/quality`. */
function mapCard(ctx, report, slug) {
  const card = el('section', 'sm-card sm-map', null, { 'aria-labelledby': 'sm-h-map' });
  const head = el('div', 'sm-cardhead');
  head.append(el('h2', null, 'Carte par hall', { id: 'sm-h-map' }));
  card.append(head, el('span', 'sm-sub', 'surface = nombre d’entrées · clic ouvre la Recherche sur le hall'));
  const measures = report.measures || {};
  const types = measures.types;
  if (types == null) {
    card.append(el('p', 'sm-slot', `Carte non mesurable : ${report.storeReason || 'store non lu'}.`));
    return card;
  }
  const halls = hallCounts(types);
  if (!halls.length) {
    card.append(el('p', 'sm-slot', 'Aucune entrée à cartographier : le store est vide.'));
    return card;
  }
  const map = el('div', 'sm-tmap', null, { role: 'group', 'aria-label': 'Carte par hall' });
  const [first, ...rest] = halls;
  map.append(hallTile(ctx, first, slug));
  if (rest.length === 1) {
    map.append(hallTile(ctx, rest[0], slug));
  } else if (rest.length) {
    const column = el('div', 'sm-tcol');
    column.style.flex = `${rest.reduce((sum, h) => sum + h.count, 0)} 1 0`;
    for (const hall of rest) column.append(hallTile(ctx, hall, slug));
    map.append(column);
  }
  card.append(map);
  if (!measures.complete) {
    card.append(el('p', 'sm-partial', `Comptes sur les ${measures.scanned} premières entrées parcourues : le store n’a pas été lu en entier.`));
  }
  return card;
}

/** La légende « Lire la carte » du panneau de droite : le langage des formes, rien de plus. */
function legend() {
  const box = el('div', 'sm-legend', null, { role: 'group', 'aria-label': 'Lire la carte' });
  const head = el('div', 'sm-legend-head');
  head.append(
    el('p', 'sm-eyebrow', 'Un seul langage, partout'),
    el('h2', null, 'Lire la carte'),
    el('p', 'sm-legend-lead', 'Le même encodage dans la carte et les tableaux de Savoir : une dimension par canal.'),
  );
  box.append(head);
  const list = el('div');
  list.append(el('p', 'sm-eyebrow', 'Forme = hall'));
  for (const hall of HALLS) {
    const row = el('div', 'sm-leg');
    row.append(shapeIcon(hall.shape), el('span', null, hall.label));
    list.append(row);
  }
  box.append(list, el('p', 'sm-leg-note',
    'La fraîcheur et les liens (remplace, contredit) se lisent entrée par entrée dans la Recherche : la carte ne les dessine pas encore.'));
  return box;
}

const itemMatches = (item, wanted) => Boolean(wanted)
  && wanted.startsWith(`${item.kind}:`) && wanted.endsWith(`:${item.key}`);

function previewOf(ctx, item, slug) {
  const gesture = (item.ref && item.ref.gesture) || {};
  const box = el('div', 'sm-preview', null, { role: 'region', 'aria-label': `Aperçu : ${item.title}` });
  box.hidden = true;
  if (gesture.note) box.append(el('p', null, gesture.note));
  const touched = Array.isArray(gesture.preview) ? gesture.preview : [];
  if (touched.length) {
    box.append(el('p', 'sm-eyebrow', 'Ce qui sera touché'));
    const list = el('ul', 'sm-touched');
    for (const row of touched) {
      const bits = [`« ${row.text} »`];
      if (row.count) bits.push(`×${row.count}`);
      if (row.users && row.users.length) bits.push(`utilisateur ${row.users.join(', ')}`);
      if (row.file) bits.push(row.file);
      list.append(el('li', null, bits.join(' · ')));
    }
    box.append(list);
  }
  if (gesture.entry) {
    const open = el('button', 'btn', 'Ouvrir l’entrée dans Recherche', { type: 'button' });
    open.addEventListener('click', () => ctx.goto('memoire', { entry: gesture.entry, of: slug || '' }));
    box.append(el('div', 'sm-cmd'));
    box.lastChild.append(open);
  }
  if (gesture.command) {
    box.append(el('p', 'sm-eyebrow', 'La commande, à lancer dans le projet'));
    const line = el('div', 'sm-cmd');
    const copy = el('button', 'btn pri', 'Copier', { type: 'button' });
    const status = actionStatus('');
    copy.addEventListener('click', () => {
      navigator.clipboard.writeText(gesture.command).then(
        () => setActionStatus(status, 'Commande copiée : colle-la dans un terminal du projet.', 'ok'),
        () => setActionStatus(status, 'Copie refusée par le navigateur : sélectionne la commande à la main.', 'bad'),
      );
    });
    line.append(el('code', null, gesture.command), copy);
    box.append(line, status);
  }
  return box;
}

function hygieneCard(ctx, report, wanted) {
  const items = report.hygiene || [];
  const card = el('section', 'sm-card sm-hygiene', null, { 'aria-labelledby': 'sm-h-hyg' });
  const head = el('div', 'sm-cardhead');
  head.append(
    el('h2', null, 'File d’hygiène', { id: 'sm-h-hyg' }),
    el('span', 'sm-cnt', String(items.length)),
    el('span', 'sm-hint', 'chaque geste passe par un aperçu ; les suppressions exportent d’abord'),
  );
  card.append(head);
  if (!items.length) {
    card.append(el('p', 'sm-none', 'Rien à entretenir : aucun constat d’hygiène sur ce projet.'));
    return { card, opened: null };
  }
  const list = el('ul', 'sm-list');
  const slug = (report.project && report.project.slug) || '';
  let opened = null;
  items.forEach((item, index) => {
    const gesture = (item.ref && item.ref.gesture) || {};
    const row = el('li', 'sm-hyg');
    row.dataset.kind = item.kind;
    row.dataset.key = item.key;
    const text = el('span', 'sm-hyg-text');
    text.append(el('span', 'sm-hyg-title', item.title), el('span', 'sm-hyg-reason', item.reason || ''));
    const cls = gesture.tone === 'danger' ? 'btn sm-danger' : index === 0 ? 'btn pri' : 'btn';
    const button = el('button', cls, gesture.label || 'Aperçu…', { type: 'button', 'aria-expanded': 'false' });
    const preview = previewOf(ctx, item, slug);
    preview.id = `sm-prev-${index}`;
    button.setAttribute('aria-controls', preview.id);
    const toggle = (open) => {
      preview.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      row.dataset.open = String(open);
      if (open) ctx.dock.echo(gesture.command || '');
    };
    button.addEventListener('click', () => toggle(preview.hidden));
    row.append(text, button, preview);
    list.append(row);
    if (!opened && itemMatches(item, wanted)) {
      toggle(true);
      opened = row;
    }
  });
  card.append(list);
  return { card, opened };
}

function headline(report) {
  const m = report.measures || {};
  const name = (report.project && report.project.name) || 'ce projet';
  const backend = report.backend ? ` › ${report.backend}` : '';
  if (m.entries == null) return `${name}${backend} · store non lu : ${report.storeReason || 'motif inconnu'}`;
  const projections = (m.projections && m.projections.count) || 0;
  const souvenirs = m.entries - projections;
  return `${name}${backend} · ${plural(souvenirs, 'souvenir', 'souvenirs')}, ${plural(projections, 'projection', 'projections')}`;
}

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([]);
  ctx.docbar.setViews([], null);
  ctx.inspector.replaceChildren();
  const wrap = el('div', 'sm-wrap');
  root.append(wrap);
  const params = ctx.params || {};
  const allScope = Boolean(ctx.scope && ctx.scope.all);
  let projects = [];
  let slug = String(params.of || '');
  if (allScope) {
    const payload = await ctx.api.projects().catch(() => null);
    projects = ((payload && payload.projects) || []).filter((p) => p.exists !== false)
      .map((p) => ({ slug: p.slug, name: p.name || p.slug }));
    if (!projects.some((p) => p.slug === slug)) slug = projects.length ? projects[0].slug : '';
    if (ctx.signal.aborted) return;
  }

  const picker = () => {
    if (!allScope || !projects.length) return null;
    const label = el('label', 'sm-pick', 'Projet');
    const select = el('select', null, null, { 'aria-label': 'Projet mesuré' });
    for (const p of projects) {
      const option = el('option', null, p.name, { value: p.slug });
      option.selected = p.slug === slug;
      select.append(option);
    }
    select.addEventListener('change', () => {
      slug = select.value;
      ctx.setItem({ of: slug });
      load();
    });
    label.append(select);
    return label;
  };

  let token = 0;
  const load = async () => {
    const mine = ++token;
    wrap.replaceChildren(spaceHead('Santé et carte', 'Mesure de la mémoire…', picker()), ...ctx.loading('Mesure de la mémoire…', 6, 'kpi'));
    let report;
    try {
      report = await ctx.api.memoryQuality(slug || undefined);
    } catch (error) {
      if (mine !== token || ctx.signal.aborted) return;
      wrap.replaceChildren(spaceHead('Santé et carte', null, picker()), ctx.empty(
        'Santé indisponible', failureText(error), null, { label: 'Réessayer', run: load }, { art: 'i-empty-memory' },
      ));
      return;
    }
    if (mine !== token || ctx.signal.aborted) return;
    const again = el('button', 'btn', 'Mesurer à nouveau', { type: 'button' });
    again.addEventListener('click', load);
    const head = spaceHead('Santé et carte', report.reason ? (report.project && report.project.name) || '' : headline(report),
      picker(), formula('grimoire memory status'), again);
    if (report.reason) {
      wrap.replaceChildren(head, ctx.empty('Santé de la mémoire', `Mesure impossible : ${report.reason}.`, null, undefined,
        { compact: true, art: 'i-empty-memory' }));
      return;
    }
    const grid = el('div', 'sm-grid');
    grid.append(healthCard(report), mapCard(ctx, report, slug));
    const { card, opened } = hygieneCard(ctx, report, String(params.item || ''));
    wrap.replaceChildren(head, grid, card);
    ctx.inspector.replaceChildren(legend());
    wrap.dataset.alert = String(Boolean(report.alert));
    ctx.dock.echo('grimoire memory status');
    if (opened) opened.scrollIntoView({ block: 'nearest' });
  };

  await load();
}
