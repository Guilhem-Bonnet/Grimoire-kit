// À traiter — l'accueil de la coque : ce qui attend une décision humaine,
// dans la portée courante, lu sur `/api/workspace/attention` (P22).
//
// L'écran (P32) : une liste groupée par urgence, une ligne par élément —
// type, projet, titre et raison, ancienneté, action en ligne — et le détail
// de la ligne choisie dans l'inspecteur. L'action ouvre l'écran où la
// décision se prend, la sélection déjà posée : une tâche dans Travail
// (`?task=`, et `of=` en portée « Tous les projets »), le reste sur la fiche
// du projet (`#flotte?focus=…`). Rien ne se décide ici.
//
// États dits, jamais tus : vide (« Rien n'attend de décision » et l'heure de
// la vérification), erreur partielle (« N projets n'ont pas répondu », avec
// Réessayer, qui relance la sonde), lecture seule (actions désactivées, la
// raison écrite à côté). Clavier : j / k pour passer d'une ligne à l'autre,
// Entrée pour l'action de la ligne.
//
// PR à revoir (P48) : une ligne par PR ouverte, l'état de sa CI et sa tâche
// liée ; l'action ouvre l'écran de revue (`#a-traiter?view=revue&pr=…`,
// module `revue.js`), la PR déjà sélectionnée. Aucune fusion ici : l'écran
// de revue renvoie sur GitHub.

import { copyText, glyph, kpiRow, listNav, rowActions, spaceHead } from '../shell.js';
import { agoText } from '../live.js';
import { mountSince } from './a-traiter-since.js';

const STYLE_ID = 'at-styles';

const CSS = `
.at-wrap { display: flex; flex-direction: column; gap: var(--sp-5); padding: var(--sp-6); max-width: 1120px; }
.at-formula { font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); white-space: nowrap; }
.at-formula b { color: var(--rub); font-weight: 500; margin-right: 6px; }
.at-partial { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2) var(--sp-3); padding: var(--sp-2) var(--sp-4);
  border: 1px dashed var(--line); border-radius: var(--r); color: var(--ink2); }
.at-partial strong { color: var(--ink); font-weight: 600; }
.at-partial .btn { margin-left: auto; }
.at-ro { margin: 0; color: var(--ink2); }
.at-section { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow);
  overflow: hidden; padding: var(--sp-1) 0 var(--sp-2); }
.at-section-head { display: flex; align-items: baseline; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); }
.at-section-head h3 { margin: 0; font-size: var(--t-s); font-weight: 600; }
.at-n { color: var(--rub); font-weight: 600; }
.at-section-head .at-hint { margin-left: auto; color: var(--ink3); }
.at-list { list-style: none; margin: 0; padding: 0; }
.at-row { display: grid; grid-template-columns: 32px minmax(0, 1fr) minmax(64px, auto) auto auto;
  align-items: center; gap: 14px; padding: var(--sp-3) var(--sp-4); margin: 2px var(--sp-2);
  border-radius: 12px; cursor: pointer; }
.at-row:hover { background: var(--hover); }
.at-row[aria-current="true"] { background: var(--acc-soft); }
.at-row:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
.at-rico { width: 32px; height: 32px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
  background: var(--hover); color: var(--ink2); }
.at-rico .glyph, .at-rico .glyph.warn, .at-rico .glyph.bad, .at-rico .glyph.ok { color: inherit; }
.at-rico[data-tone="warn"] { background: var(--warn-soft); color: var(--warn); }
.at-rico[data-tone="bad"] { background: var(--bad-soft); color: var(--bad); }
.at-rico[data-tone="ok"] { background: var(--ok-soft); color: var(--ok); }
.at-title, .at-reason, .at-project { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.at-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.at-title { font-weight: 600; }
.at-sub { display: flex; gap: var(--sp-1); min-width: 0; color: var(--ink2); }
.at-sub .at-project { flex: none; max-width: 40%; }
.at-sub .at-sep { flex: none; }
.at-since { color: var(--ink3); text-align: right; white-space: nowrap; font-size: var(--t-min); }
.at-act { justify-self: end; text-decoration: none; white-space: nowrap; }
.at-detail { display: flex; flex-direction: column; gap: 14px; min-height: 100%; }
.at-eyebrow { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink3); }
.at-detail h3 { margin: 0; font-size: 21px; line-height: 1.2; font-weight: 600; overflow-wrap: anywhere; }
.at-detail p { margin: 0; overflow-wrap: anywhere; }
.at-lead { color: var(--ink2); font-size: 14px; line-height: 21px; }
.at-kv { display: grid; grid-template-columns: 100px minmax(0, 1fr); margin: 0; }
.at-kv dt, .at-kv dd { margin: 0; padding: var(--sp-2) 0; border-top: 1px solid var(--line); font-size: var(--t-s); overflow-wrap: anywhere; }
.at-kv dt { color: var(--ink3); }
.at-actions { display: flex; gap: var(--sp-2); flex-wrap: wrap; }
.at-equiv { margin-top: auto; display: flex; flex-direction: column; gap: 6px; }
.at-refusals { margin: 0; padding-left: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-1); }
@container canvas (max-width: 720px) {
  .at-wrap { padding: var(--sp-4); }
  .at-row { grid-template-columns: 32px minmax(0, 1fr) auto; gap: var(--sp-1) var(--sp-3); }
  .at-sub { flex-wrap: wrap; column-gap: var(--sp-1); }
  .at-sub .at-project { max-width: 100%; }
  .at-since { grid-column: 2; text-align: left; }
  .at-act { grid-column: 3; grid-row: 1 / span 2; }
  .at-row .row-actions { grid-column: 2; grid-row: 3; justify-self: start; }
}
`;

/** Chaque type d'élément (`workspace_attention.KINDS`) : son icône, son mot,
 *  le verbe de son action en ligne. Un type inconnu prend `FALLBACK`. */
const KINDS = {
  checkpoint: { word: 'checkpoint', icon: 'gate', tone: 'warn', action: 'Examiner' },
  question: { word: 'question', icon: 'chat', tone: 'warn', action: 'Répondre' },
  proposal: { word: 'proposition', icon: 'agent', tone: 'warn', action: 'Examiner' },
  pr_review: { word: 'PR à revoir', icon: 'pr', tone: '', action: 'Revoir' },
  review_blocked: { word: 'revue bloquée', icon: 'scale', tone: 'warn', action: 'Voir ce qui manque' },
  task_blocked: { word: 'tâche bloquée', icon: 'stop', tone: 'bad', action: 'Débloquer' },
  directive_unacked: { word: 'consigne non accusée', icon: 'bell', tone: 'warn', action: 'Voir les consignes' },
  claim_orphaned: { word: 'claim sans session', icon: 'terminal', tone: '', action: 'Reprendre' },
  service: { word: 'service', icon: 'server', tone: 'bad', action: 'Voir le service' },
  unreadable: { word: 'projet illisible', icon: 'alert', tone: 'bad', action: 'Voir le projet' },
  probe_failed: { word: 'sonde en échec', icon: 'pulse', tone: 'bad', action: 'Voir la sonde' },
  kit_behind: { word: 'kit en retard', icon: 'seal', tone: '', action: 'Mettre à jour' },
  service_resolved: { word: 'résolu', icon: 'check', tone: 'ok', action: 'Voir' },
  // Hygiène de la mémoire (P25) : l'action ouvre Savoir › Santé et carte sur le constat.
  memory_quality: { word: 'mémoire en alerte', icon: 'i-mem-entry', tone: 'warn', action: 'Voir la santé' },
  memory_contradiction: { word: 'contradiction', icon: 'i-mem-contradict', tone: 'warn', action: 'Voir la santé' },
  memory_orphan_supersession: { word: 'remplaçant introuvable', icon: 'i-mem-supersede', tone: '', action: 'Voir la santé' },
  memory_parity_drift: { word: 'dérive des index', icon: 'i-store-graph', tone: '', action: 'Voir la santé' },
  codegraph_stale: { word: 'graphe de code périmé', icon: 'code', tone: '', action: 'Voir la santé' },
  shared_aging: { word: 'motif à reconfirmer', icon: 'i-mem-shared', tone: '', action: 'Voir la santé' },
};
const HYGIENE = new Set(['memory_quality', 'memory_contradiction', 'memory_orphan_supersession',
  'memory_parity_drift', 'codegraph_stale', 'shared_aging']);
const FALLBACK = { word: 'élément', icon: 'info', tone: '', action: 'Ouvrir' };

/** Une ligne d'aide par section (`workspace_attention.SECTIONS`). */
const HINTS = {
  decide: 'les plus anciennes d’abord',
  review: 'la fusion se fait sur GitHub',
  unblock: 'une condition de passage manque',
  repair: 'une lecture ou un service a échoué',
  maintain: 'rien d’urgent',
  resolved: 'revenu seul, gardé une heure',
};

/** Les tuiles de comptes : une par section de l'API, son compte réel et son
 *  icône ; « résolu » et « autre » n'en ont pas (rien n'attend l'humain). */
const TILES = {
  decide: { icon: 'gate', tone: 'warn' },
  review: { icon: 'pr', tone: '' },
  unblock: { icon: 'stop', tone: 'bad' },
  repair: { icon: 'alert', tone: 'bad' },
  maintain: { icon: 'seal', tone: '' },
};

/** L'équivalent en ligne de commande, quand le serveur en connaît un vrai. */
function cliOf(item) {
  const ref = item.ref || {};
  if (ref.task_id) return `grimoire task show ${ref.task_id}`;
  if (item.kind === 'pr_review' && ref.pr) return `gh pr view ${ref.pr}${ref.repo ? ` -R ${ref.repo}` : ''}`;
  if (ref.gesture && ref.gesture.command) return ref.gesture.command;
  if (item.kind === 'checkpoint') return 'grimoire upgrade-flow review';
  return '';
}

/** Où, sur la fiche du projet, se pose la sélection d'un élément sans tâche.
 *  Une sonde en échec suit la source qui n'a pas répondu (fin de son `id`). */
const FOCUS = { checkpoint: 'checkpoint', proposal: 'proposal', kit_behind: 'kit', service: 'memory', service_resolved: 'memory' };
const PROBE_FOCUS = { memory: 'memory', 'mémoire': 'memory', services: 'memory', kit: 'kit', propositions: 'proposal', checkpoints: 'checkpoint' };

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

const kindOf = (item) => KINDS[item.kind] || FALLBACK;
/** La teinte d'une PR suit sa CI : échec en rouge, verte en vert, en cours neutre. */
const CI_TONE = { failed: 'bad', passed: 'ok' };
const toneOf = (item) => (item.kind === 'pr_review'
  ? CI_TONE[(item.ref && item.ref.checks && item.ref.checks.state) || ''] || ''
  : kindOf(item).tone);
/** Une ligne qui mène à l'écran de revue : une PR, ou la lecture des PR en échec. */
const isReview = (item) => item.kind === 'pr_review'
  || (item.kind === 'probe_failed' && String(item.id || '').endsWith(':pr'));
const projectName = (item) => (item.project && (item.project.name || item.project.slug)) || '';

function hm(iso) {
  const at = Date.parse(iso || '');
  return Number.isFinite(at) ? new Date(at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '';
}

function hashOf(target, params) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value)).toString();
  return `#${target}${query ? '?' + query : ''}`;
}

/** L'écran où l'élément se décide, avec sa sélection. `slug: null` = rester
 *  en portée « Tous les projets » ; sinon la portée du projet de l'élément. */
function destination(ctx, item) {
  const slug = (item.project && item.project.slug) || '';
  const ref = item.ref || {};
  if (isReview(item)) {
    return {
      target: 'a-traiter',
      params: { view: 'revue', pr: ref.pr || '', of: ctx.scope.all ? slug : '' },
      slug: ctx.scope.all ? null : slug,
    };
  }
  if (ref.task_id) {
    return {
      target: 'travail',
      params: { view: item.kind === 'question' ? 'decisions' : '', task: ref.task_id, of: ctx.scope.all ? slug : '' },
      slug: ctx.scope.all ? null : slug,
    };
  }
  if (item.kind === 'unreadable' && ctx.scope.all) return { target: 'flotte', params: {}, slug: null };
  if (HYGIENE.has(item.kind)) return { target: 'sante', params: { item: item.id }, slug };
  const source = String(item.id || '').split(':').pop();
  const focus = item.kind === 'probe_failed' ? PROBE_FOCUS[source] : FOCUS[item.kind];
  const params = { focus: focus || '', run: ref.run_id || '', proposal: item.kind === 'proposal' ? ref.slug : '' };
  return { target: 'flotte', params, slug };
}

function href(where) {
  const scope = where.slug === null ? 'scope=all' : `project=${encodeURIComponent(where.slug)}`;
  return `?${scope}${hashOf(where.target, where.params)}`;
}

/** Ouvre l'écran de décision : sans rechargement quand la portée ne change
 *  pas, sinon par la portée de la coque (la page se recharge). */
function open(ctx, item) {
  const where = destination(ctx, item);
  const here = where.slug === null ? ctx.scope.all : !ctx.scope.all && where.slug === ctx.scope.project;
  if (here) ctx.goto(where.target, where.params);
  else ctx.scope.set(where.slug, hashOf(where.target, where.params));
}

function readOnlyReason(ctx) {
  return `Lecture seule : ${ctx.scope.name} se consulte d’ici, rien ne s’y décide. Le bandeau en tête dit comment ouvrir l’écriture.`;
}

/** L'action en ligne : un vrai lien (copiable, ouvrable dans un autre onglet),
 *  ou un bouton désactivé qui renvoie à la raison en lecture seule. */
function actionNode(ctx, item, { primary = false } = {}) {
  const verb = kindOf(item).action;
  if (ctx.host.readOnly) {
    const button = node('button', 'btn at-act', verb);
    button.type = 'button';
    button.disabled = true;
    button.title = readOnlyReason(ctx);
    button.setAttribute('aria-describedby', 'at-ro-reason');
    return button;
  }
  const link = node('a', primary ? 'btn pri at-act' : 'btn at-act', verb);
  link.href = href(destination(ctx, item));
  link.dataset.action = item.kind;
  link.addEventListener('click', (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    open(ctx, item);
  });
  return link;
}

/** Les actions secondaires d'une ligne (P56) : révélées au survol et au focus,
 *  toutes dans le menu « … ». Copier ne décide rien : elles restent offertes en
 *  lecture seule. L'action principale reste le bouton de la ligne (et Entrée). */
function rowActionsOf(ctx, item) {
  const command = cliOf(item);
  return rowActions([
    { id: 'copy-link', label: 'Copier le lien', icon: 'link',
      run: (b) => copyText(new URL(href(destination(ctx, item)), window.location.href).href, b) },
    command ? { id: 'copy-command', label: 'Copier la commande', icon: 'terminal', run: (b) => copyText(command, b) } : null,
  ], { label: `Plus d'actions : ${item.title || kindOf(item).word}` });
}

function sinceNode(item) {
  const at = Date.parse(item.since || '');
  if (!Number.isFinite(at)) return node('span', 'at-since', 'date inconnue');
  const time = node('time', 'at-since', agoText(Date.now() - at));
  time.dateTime = item.since;
  time.dataset.since = item.since;
  time.title = `depuis le ${new Date(at).toLocaleString('fr-FR')}`;
  return time;
}

/** Les checks d'une PR en une phrase : verts, échecs, ignorés, en cours. */
function ciText(checks) {
  const parts = [`${checks.passed} vert${checks.passed > 1 ? 's' : ''}`];
  if (checks.failed) parts.push(`${checks.failed} en échec`);
  if (checks.skipped) parts.push(`${checks.skipped} ignoré${checks.skipped > 1 ? 's' : ''}`);
  if (checks.pending) parts.push(`${checks.pending} en cours`);
  return checks.total ? parts.join(', ') : 'aucun check rapporté';
}

function kv(rows) {
  const list = node('dl', 'at-kv');
  for (const [label, value, mono] of rows) {
    if (value === undefined || value === null || value === '') continue;
    list.append(node('dt', null, label), node('dd', mono ? 'mono' : null, String(value)));
  }
  return list;
}

/** Le détail de l'élément choisi, dans le panneau de droite : de quoi il
 *  s'agit, depuis quand, ce que le serveur sait de sa référence, la même
 *  action que la ligne (rien de nouveau ne s'écrit d'ici), et l'équivalent CLI. */
function renderDetail(ctx, item) {
  const kind = kindOf(item);
  const ref = item.ref || {};
  const box = node('div', 'at-detail');
  box.dataset.item = item.id;
  box.append(
    node('p', 'at-eyebrow', `${projectName(item)} · ${kind.word}`),
    node('h3', null, item.title || kind.word),
    node('p', 'at-lead', item.reason || ''),
  );
  const at = Date.parse(item.since || '');
  box.append(kv([
    ['Projet', projectName(item)],
    ['En attente', Number.isFinite(at) ? `depuis le ${new Date(at).toLocaleString('fr-FR')}` : 'date inconnue'],
    ['Tâche', ref.task_id, true],
    ['Run', ref.run_id, true],
    ['Nœud', ref.node],
    ['Proposition', ref.slug, true],
    ['Service', ref.service],
    ['Adresse', ref.url, true],
    ['Fichiers', ref.behind ? `${ref.behind} en retard` : ''],
    ['PR', ref.pr ? `#${ref.pr}` : '', true],
    ['Dépôt', ref.repo, true],
    ['Branche', ref.branch, true],
    ['Tâche liée', item.kind === 'pr_review' ? (ref.linked_task || 'aucune') : '', true],
    ['CI', ref.checks ? ciText(ref.checks) : ''],
    ['Geste', ref.gesture ? ref.gesture.note : ''],
  ]));
  if (Array.isArray(ref.refusals) && ref.refusals.length) {
    const list = node('ul', 'at-refusals');
    for (const refusal of ref.refusals) {
      list.append(node('li', null, [refusal.evidence, refusal.reason, refusal.remedy && `remède : ${refusal.remedy}`].filter(Boolean).join(' — ')));
    }
    box.append(node('span', 'at-eyebrow', 'Ce que le gate refuse'), list);
  }
  const actions = node('div', 'at-actions');
  actions.append(actionNode(ctx, item, { primary: true }));
  box.append(actions);
  if (ctx.host.readOnly) box.append(node('p', 'at-lead', readOnlyReason(ctx)));
  const command = cliOf(item);
  if (command) {
    const formula = node('span', 'at-formula');
    formula.append(node('b', null, '›'), command);
    const equiv = node('div', 'at-equiv');
    equiv.append(node('span', 'at-eyebrow', 'Équivalent'), formula);
    box.append(equiv);
  }
  ctx.inspector.replaceChildren(box);
  ctx.dock.echo(command);
}

/** Une tuile par section qui attend quelque chose : son compte, et l'ancienneté
 *  de son plus vieil élément quand le serveur en donne une. */
function tilesOf(sections) {
  const tiles = [];
  for (const section of sections) {
    const tile = TILES[section.id];
    if (!tile || !section.count) continue;
    const times = (section.items || []).map((i) => Date.parse(i.since || '')).filter(Number.isFinite);
    const oldest = times.length ? agoText(Date.now() - Math.min(...times)).replace(/^il y a /, '') : '';
    tiles.push({
      value: section.count, label: section.label, icon: tile.icon, tone: tile.tone,
      sub: oldest ? `la plus ancienne : ${oldest}` : '',
    });
  }
  return tiles;
}

function partialError(unreadable, retry) {
  const count = unreadable.length;
  const box = node('div', 'at-partial');
  box.dataset.state = 'partial';
  box.setAttribute('role', 'status');
  const lead = node('strong', null, count > 1 ? `${count} projets n'ont pas répondu` : `${count} projet n'a pas répondu`);
  const names = unreadable.map((p) => p.name || p.slug).join(', ');
  const tail = node('span', null, ` : ${names}. Ce qu'ils attendent n'est pas compté ici.`);
  const button = node('button', 'btn', 'Réessayer');
  button.type = 'button';
  button.dataset.action = 'retry';
  button.addEventListener('click', retry);
  const text = node('span');
  text.append(lead, tail);
  box.append(text, button);
  return box;
}

/** Relectures tant que les PR d'un projet sont « en cours de lecture » : la
 *  route d'attention n'attend jamais GitHub, elle le dit et l'écran relit. */
const REVIEW_POLL_MS = 1500;
const REVIEW_POLL_MAX = 12;

export async function mount(root, ctx) {
  // L'écran de revue (P48) vit dans À traiter : même espace, autre vue.
  if (ctx.params.view === 'revue') {
    const review = await import('./revue.js');
    await review.mount(root, ctx);
    return;
  }
  // Premier lancement (P36) : le guide, en lecture seule, joignable à tout
  // moment par `#a-traiter?view=demarrer` ; et sur un projet que `/api/health`
  // dit non initialisé, il prend d'office la place de l'accueil.
  if (ctx.params.view === 'demarrer') {
    const guide = await import('./demarrer.js');
    await guide.mount(root, ctx);
    return;
  }
  if (!ctx.scope.all) {
    const health = await ctx.api.health().catch(() => null);
    if (ctx.signal.aborted) return;
    if (health && health.kit && health.kit.scaffolded === false) {
      const guide = await import('./demarrer.js');
      await guide.mount(root, ctx, { health });
      return;
    }
  }
  injectStyles();
  const wrap = node('div', 'at-wrap');
  root.append(wrap);

  // La sélection : un identifiant d'élément (`#a-traiter?item=…`, lien profond).
  let selected = ctx.params.item || '';
  // Le résumé « depuis ta dernière visite » (P53) : gardé d'un rendu à l'autre.
  const since = document.createElement('section');
  let rows = [];

  const load = (probe) => ctx.api.attention(ctx.scope.all, probe ? { probe: true } : undefined);

  const select = (id, { focus = false, remember = true } = {}) => {
    const row = rows.find((r) => r.item.id === id) || rows[0];
    if (!row) return;
    selected = row.item.id;
    for (const r of rows) {
      const on = r === row;
      r.el.setAttribute('aria-current', String(on));
      r.el.tabIndex = on ? 0 : -1;
      // L'action primaire est celle de la ligne choisie (maquette Douce).
      const act = r.el.querySelector('a.at-act');
      if (act) act.classList.toggle('pri', on);
    }
    renderDetail(ctx, row.item);
    if (focus) {
      row.el.focus();
      row.el.scrollIntoView({ block: 'nearest' });
    }
    if (remember) ctx.setItem?.({ item: row.item.id });
  };

  const render = (data) => {
    const hadFocus = wrap.contains(document.activeElement) && document.activeElement.classList.contains('at-row');
    const summary = data.summary || {};
    const count = summary.projects ?? 0;
    const formula = node('span', 'at-formula');
    formula.append(node('b', null, '›'), 'grimoire status');
    const head = spaceHead('À traiter', ctx.scope.all
      ? `Ce qui attend une décision de toi, sur ${count} projet${count > 1 ? 's' : ''}.`
      : `Ce qui attend une décision de toi dans ${ctx.scope.name}.`, formula);
    wrap.replaceChildren(head, since);
    rows = [];

    const unreadable = (data.projects || []).filter((p) => p.state === 'unreadable');
    if (unreadable.length) wrap.append(partialError(unreadable, () => refresh(true)));

    const items = (data.sections || []).reduce((n, s) => n + (s.count || 0), 0);
    if (!data.count) {
      const at = hm(data.generated_at);
      wrap.append(ctx.empty(
        "Rien n'attend de décision",
        at ? `Dernière vérification : ${at}.` : 'Heure de la vérification inconnue.',
        null,
        { label: 'Ouvrir le Travail', run: () => ctx.goto('travail') },
        { art: 'i-empty-inbox' },
      ));
    }
    if (items && ctx.host.readOnly) wrap.append(Object.assign(node('p', 'at-ro', readOnlyReason(ctx)), { id: 'at-ro-reason' }));

    const tiles = tilesOf(data.sections || []);
    if (tiles.length) wrap.append(kpiRow(tiles));

    for (const section of data.sections || []) {
      if (!section.count) continue;
      const block = node('section', 'at-section');
      block.dataset.section = section.id;
      const heading = node('div', 'at-section-head');
      const title = node('h3', null, section.label);
      title.id = `at-h-${section.id}`;
      heading.append(title, node('span', 'at-n', String(section.count)));
      if (HINTS[section.id]) heading.append(node('span', 'at-hint', HINTS[section.id]));
      const list = node('ul', 'at-list');
      list.setAttribute('aria-labelledby', title.id);
      for (const item of section.items || []) {
        const kind = kindOf(item);
        const li = node('li', 'at-row');
        li.dataset.item = item.id;
        li.dataset.kind = item.kind;
        li.tabIndex = -1;
        const pastille = node('span', 'at-rico');
        const tone = toneOf(item);
        if (tone) pastille.dataset.tone = tone;
        pastille.append(glyph(kind.icon, kind.word, tone, { alone: true }));
        const text = node('span', 'at-text');
        const sub = node('span', 'at-sub');
        const project = node('span', 'at-project', projectName(item));
        project.title = projectName(item);
        const reason = node('span', 'at-reason', item.reason || '');
        reason.title = item.reason || '';
        sub.append(project);
        if (item.reason) sub.append(node('span', 'at-sep', '·'), reason);
        const title = node('span', 'at-title', item.title || kind.word);
        title.title = item.title || '';
        text.append(title, sub);
        li.append(pastille, text, sinceNode(item), rowActionsOf(ctx, item), actionNode(ctx, item));
        li.addEventListener('click', () => select(item.id, { focus: true }));
        list.append(li);
        rows.push({ item, el: li });
      }
      block.append(heading, list);
      wrap.append(block);
    }
    const piloted = (data.reviews || []).reduce((n, r) => n + (r.piloted || 0), 0);
    if (piloted) {
      const fold = node('details', 'at-piloted');
      fold.append(
        node('summary', null, `Relues par le pilote : ${piloted} en cours`),
        node('p', 'at-hint', 'Le relecteur du tick relit ces PR sous mandat, le merger les fusionne : rien à faire de ton côté.'),
      );
      wrap.append(fold);
    }
    if (rows.length) select(selected, { focus: hadFocus, remember: Boolean(ctx.params.item) || hadFocus });
    else ctx.inspector.replaceChildren();
  };

  let polls = 0;
  let pollTimer = 0;
  ctx.signal.addEventListener('abort', () => clearTimeout(pollTimer));

  const refresh = async (probe = false) => {
    let data;
    try {
      data = await load(probe);
    } catch (error) {
      if (ctx.signal.aborted) return;
      wrap.replaceChildren(ctx.empty('À traiter indisponible', String(error.message || error), null, {
        label: 'Réessayer', run: () => refresh(true),
      }));
      ctx.inspector.replaceChildren();
      // Les lignes sont détachées : sans ça, j / k agiraient encore sur elles.
      rows = [];
      return;
    }
    if (ctx.signal.aborted) return;
    render(data);
    clearTimeout(pollTimer);
    const loading = (data.reviews || []).some((r) => r.state === 'loading');
    if (loading && polls < REVIEW_POLL_MAX) {
      polls += 1;
      pollTimer = setTimeout(() => refresh(false), REVIEW_POLL_MS);
    } else if (!loading) {
      polls = 0;
    }
  };

  // j / k : ligne suivante, précédente ; Entrée : l'action de la ligne tenue.
  // Le contrôleur de liste de la coque (P58) : jamais pendant une saisie, la
  // palette ouverte, ou sous un modificateur.
  listNav(wrap, {
    rows: '.at-row',
    onMove: (row) => select(row.dataset.item, { focus: true }),
    onActivate: (row) => {
      const hit = rows.find((r) => r.el === row);
      if (hit && !ctx.host.readOnly) open(ctx, hit.item);
    },
  });

  // Le pouls (P10) annonce un changement : la liste se relit, la sélection reste.
  ctx.live.on(() => refresh(false));

  await refresh(false);
  // Après le premier rendu, jamais devant lui.
  mountSince(since, ctx);
}
