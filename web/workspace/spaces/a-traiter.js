// À traiter — l'accueil de la coque (P31) : ce qui attend une décision
// humaine, dans la portée courante, lu sur `/api/workspace/attention` (P22).
//
// Ce module ne pose que la liste, section par section, et le lien profond de
// chaque élément vers l'écran où il se décide (une tâche dans Travail, le
// reste sur la fiche du projet). L'écran complet — actions en ligne, états
// d'erreur partielle, navigation j/k — est l'affaire de P32.

import { badge, spaceHead } from '../shell.js';
import { agoText } from '../live.js';

const STYLE_ID = 'at-styles';

const CSS = `
.at-wrap { display: flex; flex-direction: column; gap: var(--sp-5); padding: var(--sp-5); max-width: 960px; }
.at-section { display: flex; flex-direction: column; gap: var(--sp-2); }
.at-section h3 { margin: 0; display: flex; align-items: center; gap: var(--sp-2); font-size: var(--t-m); font-weight: 600; }
.at-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-1); }
.at-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px var(--sp-3); padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r); background: var(--card); color: var(--ink); text-decoration: none; }
.at-item:hover, .at-item:focus-visible { background: var(--e2); }
.at-title { font-weight: 500; overflow-wrap: anywhere; }
.at-reason { grid-column: 1; color: var(--ink2); overflow-wrap: anywhere; }
.at-meta { grid-row: 1 / span 2; grid-column: 2; align-self: center; color: var(--ink3); text-align: right; }
`;

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

/** Où se décide l'élément : la tâche dans Travail, sinon la fiche du projet. */
function destination(item) {
  const task = item.ref && item.ref.task_id;
  return task ? { target: 'travail', hash: `#travail?task=${encodeURIComponent(task)}`, params: { task } }
    : { target: 'flotte', hash: '#flotte', params: {} };
}

function itemLink(ctx, item) {
  const slug = item.project && item.project.slug;
  const where = destination(item);
  const here = !ctx.scope.all && slug === ctx.scope.project;
  const link = node('a', 'at-item');
  // Un vrai lien : copiable, ouvrable dans un autre onglet. Dans le projet
  // déjà en portée, le clic navigue sans recharger la page.
  link.href = `?project=${encodeURIComponent(slug || '')}${where.hash}`;
  link.dataset.kind = item.kind;
  link.addEventListener('click', (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    if (here) ctx.goto(where.target, where.params);
    else ctx.scope.set(slug, where.hash);
  });
  const title = ctx.scope.all && item.project ? `${item.project.name || slug} · ${item.title}` : item.title;
  link.append(node('span', 'at-title', title), node('span', 'at-reason', item.reason || ''));
  if (item.since) {
    const since = Date.parse(item.since);
    if (Number.isFinite(since)) link.append(node('span', 'at-meta lbl', `depuis ${agoText(Date.now() - since)}`));
  }
  const li = node('li');
  li.append(link);
  return li;
}

export async function mount(root, ctx) {
  injectStyles();
  const wrap = node('div', 'at-wrap');
  root.append(wrap);
  let data;
  try {
    data = await ctx.api.attention(ctx.scope.all);
  } catch (error) {
    if (ctx.signal.aborted) return;
    wrap.append(ctx.empty('À traiter indisponible', String(error.message || error), null, {
      label: 'Réessayer', run: () => ctx.goto('a-traiter', {}),
    }));
    return;
  }
  if (ctx.signal.aborted) return;
  const summary = data.summary || {};
  wrap.append(spaceHead('À traiter', ctx.scope.all
    ? `Ce qui attend une décision humaine, sur les ${summary.projects ?? 0} projet(s) du registre.`
    : `Ce qui attend une décision humaine dans ${ctx.scope.name}.`));
  if (!data.count) {
    wrap.append(ctx.empty(
      "Rien n'attend de décision",
      "Aucun élément d'attention dans cette portée : la route d'attention n'a rien remonté.",
      null, undefined, { art: 'i-empty-inbox' },
    ));
  }
  for (const section of data.sections || []) {
    if (!section.count) continue;
    const block = node('section', 'at-section');
    block.dataset.section = section.id;
    const heading = node('h3', null, section.label);
    heading.append(badge(String(section.count)));
    const list = node('ul', 'at-list');
    for (const item of section.items) list.append(itemLink(ctx, item));
    block.append(heading, list);
    wrap.append(block);
  }
  ctx.inspector.replaceChildren();
}
