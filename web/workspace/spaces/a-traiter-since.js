// « Depuis ta dernière visite » (P53) — le résumé en tête d'À traiter.
//
// Ce qui a été livré, refusé ou laissé en attente depuis la dernière visite
// enregistrée, lu sur `/api/workspace/attention/since` (une lecture : le
// serveur ne retient rien). Chaque ligne est un lien vers sa tâche dans
// Travail, la sélection posée.
//
// La visite est propre à la personne et au navigateur : la coque la garde
// avec ses autres préférences (`grimoire.workspace.<projet>`, champ
// `visit:<portée>`), en UTC (`toISOString`) — le serveur compare en UTC
// conscient, jamais en texte. Règle de mise à jour :
//
// - aucune visite enregistrée : aucun résumé, l'ouverture devient la visite ;
// - résumé vide : l'ouverture devient la visite (rien à lire n'est perdu) ;
// - résumé affiché : la visite n'avance que quand il a été vu — « Refermer »,
//   ou la sortie d'À traiter vers un autre écran. Un rechargement ne compte
//   pas : le résumé revient tant qu'il n'a pas été refermé ni quitté.
//
// La visite enregistrée est toujours l'instant d'OUVERTURE, jamais celui de
// la fermeture : ce qui arrive pendant la lecture apparaîtra la fois suivante.
//
// Chargé après le premier rendu d'À traiter, jamais devant lui.

import { badge, glyph, icon, readState, writeState } from '../shell.js';
import { agoText } from '../live.js';

const STYLE_ID = 'ats-styles';

const CSS = `
.ats { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow);
  padding: var(--sp-3) var(--sp-4) var(--sp-3); display: flex; flex-direction: column; gap: var(--sp-3); }
.ats[hidden] { display: none; }
.ats-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2) var(--sp-3); }
.ats-head h3 { margin: 0; font-size: var(--t-s); font-weight: 600; display: flex; align-items: center; gap: var(--sp-2); }
.ats-head h3 .ico { color: var(--ink2); }
.ats-when { color: var(--ink2); font-size: var(--t-s); }
.ats-counts { display: flex; gap: var(--sp-1); flex-wrap: wrap; }
.ats-close { margin-left: auto; }
.ats-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--sp-3) var(--sp-4); }
.ats-group h4 { margin: 0 0 var(--sp-1); font-size: var(--t-min); font-weight: 500; color: var(--ink3); }
.ats-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.ats-line { display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; gap: var(--sp-2); align-items: center;
  padding: var(--sp-1) var(--sp-2); border-radius: var(--r); }
.ats-line:hover { background: var(--hover); }
.ats-ico { width: 24px; height: 24px; border-radius: var(--r); display: flex; align-items: center; justify-content: center;
  background: var(--hover); color: var(--ink2); }
.ats-ico .glyph, .ats-ico .glyph.ok, .ats-ico .glyph.bad, .ats-ico .glyph.warn { color: inherit; }
.ats-ico[data-tone="ok"] { background: var(--ok-soft); color: var(--ok); }
.ats-ico[data-tone="bad"] { background: var(--bad-soft); color: var(--bad); }
.ats-ico[data-tone="warn"] { background: var(--warn-soft); color: var(--warn); }
.ats-text { display: flex; flex-direction: column; min-width: 0; }
.ats-link { font-weight: 600; color: var(--ink); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ats-link:hover { text-decoration: underline; }
.ats-link:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; border-radius: 2px; }
.ats-sub { color: var(--ink2); font-size: var(--t-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ats-at { color: var(--ink3); font-size: var(--t-min); white-space: nowrap; }
.ats-more, .ats-note { margin: 0; color: var(--ink2); font-size: var(--t-s); padding-left: var(--sp-2); }
`;

/** Chaque sorte de ligne (`workspace_since.KINDS`) : son titre, son icône, son ton. */
const KINDS = {
  delivered: { label: 'Livré', word: 'livré', icon: 'check', tone: 'ok' },
  refused: { label: 'Refusé', word: 'refusé', icon: 'stop', tone: 'bad' },
  waiting: { label: 'En attente', word: 'en attente', icon: 'hourglass', tone: 'warn' },
};

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

/** Le champ de la visite pour la portée courante : un projet, ou « Tous les projets ». */
export function visitKey(scope) {
  return `visit:${scope.all ? '*' : scope.project || ''}`;
}

function readVisit(key) {
  const raw = readState()[key];
  return typeof raw === 'string' && Number.isFinite(Date.parse(raw)) ? raw : '';
}

function whenText(iso) {
  const at = new Date(iso);
  const year = at.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {};
  return `le ${at.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', ...year })} à ${at.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
}

/** Les paramètres de Travail pour une tâche : `of` en portée « Tous les projets ». */
function taskParams(scope, line) {
  const slug = (line.project && line.project.slug) || '';
  return scope.all && slug ? { task: line.task_id, of: slug } : { task: line.task_id };
}

function taskHref(scope, line) {
  const where = scope.all ? 'scope=all' : `project=${encodeURIComponent(scope.project || '')}`;
  return `?${where}#travail?${new URLSearchParams(taskParams(scope, line)).toString()}`;
}

function lineNode(ctx, kind, line) {
  const li = node('li', 'ats-line');
  li.dataset.kind = line.kind;
  li.dataset.task = line.task_id;
  const pastille = node('span', 'ats-ico');
  pastille.dataset.tone = kind.tone;
  pastille.append(glyph(kind.icon, kind.word, kind.tone, { alone: true }));
  const text = node('span', 'ats-text');
  const link = node('a', 'ats-link', line.title || line.task_id);
  link.href = taskHref(ctx.scope, line);
  link.title = `${line.title || ''} (${line.task_id})`;
  link.addEventListener('click', (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    ctx.goto('travail', taskParams(ctx.scope, line));
  });
  const project = ctx.scope.all && line.project ? `${line.project.name || line.project.slug} · ` : '';
  const times = line.count > 1 ? ` (${line.count} fois)` : '';
  const sub = node('span', 'ats-sub', `${project}${line.reason || ''}${times}`);
  sub.title = sub.textContent;
  text.append(link, sub);
  const at = Date.parse(line.at || '');
  const time = node('time', 'ats-at', Number.isFinite(at) ? agoText(Date.now() - at) : '');
  if (Number.isFinite(at)) {
    time.dateTime = line.at;
    time.title = new Date(at).toLocaleString('fr-FR');
  }
  li.append(pastille, text, time);
  return li;
}

function render(ctx, box, data, close) {
  const head = node('div', 'ats-head');
  const title = node('h3');
  title.id = 'ats-title';
  title.append(icon('history'), 'Depuis ta dernière visite');
  const when = node('time', 'ats-when', whenText(data.since));
  when.dateTime = data.since;
  const counts = node('div', 'ats-counts');
  for (const section of data.sections) {
    const kind = KINDS[section.id];
    if (kind && section.count) counts.append(badge(`${section.count} ${kind.word}`, kind.tone));
  }
  const button = node('button', 'btn ats-close');
  button.type = 'button';
  button.dataset.action = 'since-close';
  button.title = 'Marquer comme vu : ce résumé ne reviendra pas';
  button.append(icon('cross'), 'Refermer');
  button.addEventListener('click', close);
  head.append(title, when, counts, button);

  const groups = node('div', 'ats-groups');
  for (const section of data.sections) {
    const kind = KINDS[section.id];
    if (!kind || !section.count) continue;
    const group = node('div', 'ats-group');
    group.dataset.group = section.id;
    const heading = node('h4', null, `${kind.label} · ${section.count}`);
    heading.id = `ats-h-${section.id}`;
    const list = node('ul', 'ats-list');
    list.setAttribute('aria-labelledby', heading.id);
    for (const line of section.items) list.append(lineNode(ctx, kind, line));
    group.append(heading, list);
    if (section.more) group.append(node('p', 'ats-more', `et ${section.more} de plus`));
    groups.append(group);
  }
  box.replaceChildren(head, groups);
  const silent = (data.projects || []).filter((p) => p.state === 'unreadable');
  if (silent.length) {
    const names = silent.map((p) => p.name || p.slug).join(', ');
    box.append(node('p', 'ats-note', `Non lus : ${names}. Ce qui s'y est passé n'est pas compté.`));
  }
  box.hidden = false;
}

/** Pose le résumé dans `box` (vide et masqué tant qu'il n'y a rien à dire).
 *  Ne lève jamais : une lecture ratée laisse le résumé absent et la visite
 *  intacte, pour qu'il soit retenté à la prochaine ouverture. */
export async function mountSince(box, ctx) {
  injectStyles();
  box.className = 'ats';
  box.hidden = true;
  box.dataset.state = 'loading';
  box.setAttribute('aria-labelledby', 'ats-title');
  const key = visitKey(ctx.scope);
  const openedAt = new Date().toISOString();
  const previous = readVisit(key);
  const seen = () => writeState({ [key]: openedAt });
  if (!previous) {
    box.dataset.state = 'none';
    seen();
    return;
  }
  let data;
  try {
    data = await ctx.api.attentionSince(ctx.scope.all, previous);
  } catch {
    box.dataset.state = 'error';
    return;
  }
  if (ctx.signal.aborted) return;
  if (!data || !data.visit || !data.count) {
    box.dataset.state = 'empty';
    seen();
    return;
  }
  let done = false;
  const markSeen = () => {
    if (done) return;
    done = true;
    seen();
  };
  // La sortie d'À traiter vers un autre écran vaut lecture ; un rechargement non.
  ctx.signal.addEventListener('abort', markSeen, { once: true });
  render(ctx, box, data, () => {
    markSeen();
    box.hidden = true;
    box.dataset.state = 'closed';
    box.replaceChildren();
  });
  box.dataset.state = 'shown';
}
