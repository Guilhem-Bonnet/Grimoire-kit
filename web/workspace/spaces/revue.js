// L'écran de revue (P48) — une vue d'À traiter (`#a-traiter?view=revue&pr=…`),
// lue sur `/api/workspace/reviews`.
//
// Maquette « Revue » : la liste des PR à revoir à gauche, les plus prêtes
// d'abord ; au centre la PR choisie — d'où elle vient (branche, tâche du
// ledger liée), l'état de sa CI en quatre tuiles (verts, échecs, ignorés,
// tâche), ce qu'elle touche et ses croisements (autres PR sur les mêmes
// fichiers, issues fermées) ; dans l'inspecteur, les trois questions à se
// poser avant de fusionner.
//
// Lecture seule, sans exception : aucune fusion, aucun commentaire, aucune
// écriture GitHub d'ici. Le seul geste est un lien qui ouvre la PR sur GitHub
// (nouvel onglet), où l'humain fusionne. Un `gh` absent ou non authentifié
// s'écrit « état des PR inconnu », jamais une liste vide.

import { badge, glyph, icon, kpiRow, spaceHead } from '../shell.js';

const STYLE_ID = 'rv-styles';

const CSS = `
.rv-wrap { display: grid; grid-template-columns: minmax(240px, 300px) minmax(0, 1fr); min-height: 100%; }
.rv-list { border-right: 1px solid var(--line); background: var(--panel); display: flex; flex-direction: column; min-width: 0; }
.rv-list-head { display: flex; align-items: baseline; gap: var(--sp-2); padding: var(--sp-4) var(--sp-4) var(--sp-2); }
.rv-list-head h2 { margin: 0; font-size: var(--t-s); font-weight: 600; }
.rv-n { color: var(--rub); font-weight: 600; }
.rv-hint { margin-left: auto; color: var(--ink3); font-size: var(--t-min); }
.rv-items { list-style: none; margin: 0; padding: 0 var(--sp-2) var(--sp-3); display: flex; flex-direction: column; gap: 2px; }
.rv-pr { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; padding: var(--sp-2) var(--sp-3);
  border-radius: 12px; cursor: pointer; text-decoration: none; color: var(--ink); }
.rv-pr:hover { background: var(--hover); }
.rv-pr[aria-current="true"] { background: var(--acc-soft); }
.rv-pr:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
.rv-pr-title { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rv-pr-sub { color: var(--ink2); font-size: var(--t-min); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rv-pr-sub .mono { font-family: var(--mono); }
.rv-main { display: flex; flex-direction: column; gap: var(--sp-5); padding: var(--sp-6); min-width: 0; max-width: 1040px; }
.rv-back { align-self: flex-start; display: inline-flex; align-items: center; gap: 6px; color: var(--ink2); text-decoration: none; font-size: var(--t-s); }
.rv-back:hover { color: var(--ink); }
.rv-back .ico { transform: rotate(180deg); }
.rv-head { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.rv-eyebrow { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink3); overflow-wrap: anywhere; }
.rv-head h1 { margin: 0; font-size: 21px; line-height: 1.25; font-weight: 600; overflow-wrap: anywhere; }
.rv-lead { margin: 0; color: var(--ink2); }
.rv-card { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow);
  padding: var(--sp-3) var(--sp-4); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-5); }
.rv-card h2 { margin: 0 0 6px; font-size: var(--t-s); font-weight: 600; }
.rv-fg { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 6px 0; border-top: 1px solid var(--line);
  font-size: var(--t-s); align-items: center; }
.rv-fg > :last-child { text-align: right; color: var(--ink2); overflow-wrap: anywhere; }
.rv-fg .mono { font-family: var(--mono); font-size: var(--t-min); }
.rv-fails { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: var(--sp-1); }
.rv-fails li { display: flex; gap: var(--sp-2); align-items: center; font-size: var(--t-s); }
.rv-fails .rv-link, .rv-fails .rv-link:visited { color: var(--acc); }
.rv-actions { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; }
.rv-actions .btn { text-decoration: none; }
.rv-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.rv-unknown { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2) var(--sp-3); padding: var(--sp-2) var(--sp-4);
  border: 1px dashed var(--line); border-radius: var(--r); color: var(--ink2); }
.rv-unknown strong { color: var(--ink); font-weight: 600; }
.rv-unknown .btn { margin-left: auto; }
.rv-aside { display: flex; flex-direction: column; gap: 14px; min-height: 100%; }
.rv-aside h3 { margin: 0; font-size: 15px; font-weight: 600; }
.rv-kv { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; padding: var(--sp-2) 0; border-top: 1px solid var(--line); font-size: var(--t-s); }
.rv-kv > :first-child { color: var(--ink3); font-weight: 500; }
.rv-formula { margin-top: auto; font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); overflow-wrap: anywhere; }
.rv-formula b { color: var(--rub); font-weight: 500; margin-right: 6px; }
@media (max-width: 860px) {
  .rv-wrap { grid-template-columns: minmax(0, 1fr); }
  .rv-list { border-right: 0; border-bottom: 1px solid var(--line); }
  .rv-card { grid-template-columns: minmax(0, 1fr); }
  .rv-main { padding: var(--sp-4); }
}
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

/** Les conclusions de GitHub en mots (celles d'un check en échec). */
const CONCLUSIONS = {
  failure: 'échec', timed_out: 'délai dépassé', cancelled: 'annulé', action_required: 'action requise',
  startup_failure: 'échec au démarrage', stale: 'périmé', error: 'erreur', inconnu: 'issue inconnue',
};

const plural = (n, word) => `${n} ${word}${n > 1 ? 's' : ''}`;

/** Le badge de CI d'une ligne (maquette) : verte, 1 échec, N échecs, en cours, sans check. */
function ciBadge(checks) {
  if (checks.state === 'failed') return badge(plural(checks.failed, 'échec'), checks.failed > 1 ? 'bad' : 'warn');
  if (checks.state === 'green') return badge('CI verte', 'ok');
  if (checks.state === 'pending') return badge('CI en cours', '');
  return badge('sans check', '');
}

const keyOf = (pr) => `${(pr.project && pr.project.slug) || ''}#${pr.number}`;

/** Un lien qui ouvre GitHub dans un nouvel onglet, sans lien de retour vers le cockpit. */
function external(label, href, cls) {
  const link = node('a', cls, label);
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function fg(label, value, mono) {
  const row = node('div', 'rv-fg');
  const right = value instanceof Node ? value : node('span', mono ? 'mono' : null, String(value));
  row.append(node('span', null, label), right);
  return row;
}

function taskHref(ctx, pr) {
  const slug = (pr.project && pr.project.slug) || '';
  const params = new URLSearchParams({ task: pr.task.id });
  if (ctx.scope.all && slug) params.set('of', slug);
  const scope = ctx.scope.all ? 'scope=all' : `project=${encodeURIComponent(slug)}`;
  return `?${scope}#travail?${params.toString()}`;
}

function renderMain(ctx, main, pr, { missing } = {}) {
  const checks = pr.checks;
  const head = node('div', 'rv-head');
  const origin = [`PR #${pr.number}`, pr.branch, pr.task ? `tâche ${pr.task.id}` : 'aucune tâche liée'];
  if (ctx.scope.all && pr.project) origin.unshift(pr.project.name || pr.project.slug);
  head.append(node('p', 'rv-eyebrow', origin.filter(Boolean).join(' · ')), node('h1', null, pr.title));
  const lead = [`+${pr.additions} −${pr.deletions} sur ${plural(pr.changed_files, 'fichier')}`];
  if (pr.author) lead.push(`par ${pr.author}`);
  if (pr.base) lead.push(`vers ${pr.base}`);
  head.append(node('p', 'rv-lead', lead.join(' · ')));

  const back = node('a', 'rv-back');
  back.href = '#a-traiter';
  back.append(icon('chevron'), 'À traiter');
  back.addEventListener('click', (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    ctx.goto('a-traiter', {});
  });

  const failingNames = checks.failing.map((f) => f.name).join(', ');
  const tiles = kpiRow([
    { id: 'green', value: checks.green, label: 'CI verte', icon: 'check', tone: checks.green ? 'ok' : '',
      sub: checks.pending ? `${checks.pending} encore en cours` : `sur ${plural(checks.total, 'check')}` },
    { id: 'failed', value: checks.failed, label: 'En échec', icon: 'cross', tone: checks.failed ? 'bad' : '',
      sub: failingNames || 'aucun' },
    { id: 'skipped', value: checks.skipped, label: 'Ignorés', icon: 'filter', sub: 'non concernés' },
    { id: 'task', value: pr.task ? pr.task.status : 'aucune', label: 'Tâche liée', icon: 'board',
      sub: pr.task ? pr.task.title : 'aucune tâche citée' },
  ]);

  const nodes = [back, head];
  if (missing) nodes.push(node('p', 'rv-note', `La PR #${missing} n'est plus ouverte : voici la plus prête.`));
  nodes.push(tiles);

  if (checks.failing.length) {
    const fails = node('ul', 'rv-fails');
    fails.setAttribute('aria-label', 'Checks en échec');
    for (const fail of checks.failing) {
      const li = node('li');
      li.append(glyph('cross', 'en échec', 'bad', { alone: true }));
      const label = [fail.workflow, fail.name].filter(Boolean).join(' › ');
      li.append(fail.url ? external(label, fail.url, 'rv-link') : node('span', null, label));
      li.append(node('span', 'rv-note', CONCLUSIONS[fail.conclusion] || fail.conclusion));
      fails.append(li);
    }
    nodes.push(fails);
  }

  const card = node('section', 'rv-card');
  const touched = node('div');
  touched.append(node('h2', null, 'Ce que touche la PR'));
  for (const group of pr.files) {
    touched.append(fg(`${group.label} · ${plural(group.count, 'fichier')}`, group.paths.join(', '), true));
  }
  if (!pr.files.length) touched.append(fg('Fichiers', 'liste non rapportée'));
  touched.append(fg('CHANGELOG', pr.changelog ? badge('présent', 'ok') : badge('absent', 'warn')));
  const crossings = node('div');
  crossings.append(node('h2', null, 'Croisements'));
  crossings.append(fg('Autres PR sur les mêmes fichiers', pr.overlaps.length ? pr.overlaps.map((n) => `#${n}`).join(', ') : 'aucune'));
  crossings.append(fg('Issue fermée à la fusion', pr.issues.length ? pr.issues.map((n) => `#${n}`).join(', ') : 'aucune'));
  const decision = { APPROVED: 'approuvée', CHANGES_REQUESTED: 'changements demandés', REVIEW_REQUIRED: 'revue requise' };
  crossings.append(fg('Revue sur GitHub', decision[pr.review_decision] || 'aucune'));
  crossings.append(fg('Dépôt', pr.repo, true));
  card.append(touched, crossings);
  nodes.push(card);

  const actions = node('div', 'rv-actions');
  if (pr.url) {
    const open = external('', pr.url, 'btn pri');
    open.dataset.action = 'open-github';
    open.append(icon('external'), 'Ouvrir sur GitHub pour fusionner');
    actions.append(open);
  } else {
    actions.append(node('span', 'rv-note', 'Adresse de la PR non rapportée par GitHub.'));
  }
  if (pr.task) {
    const task = node('a', 'btn', 'Voir la tâche');
    task.href = taskHref(ctx, pr);
    task.dataset.action = 'task';
    actions.append(task);
  }
  nodes.push(actions);
  nodes.push(node('p', 'rv-note', 'Le cockpit ne fusionne jamais : la fusion reste un geste humain sur GitHub.'));
  main.replaceChildren(...nodes);
}

function renderAside(ctx, pr) {
  const box = node('div', 'rv-aside');
  box.append(node('p', 'rv-eyebrow', 'Avant de fusionner'), node('h3', null, 'Trois questions, dans l’ordre'));
  const questions = [
    ['1. Preuve', 'Les critères de la tâche sont-ils prouvés par la CI ou par le pack ?'],
    ['2. Échecs', 'Chaque échec est-il qualifié (nouveau, préexistant, sans rapport) ?'],
    ['3. Portée', 'La PR reste-t-elle dans sa tâche ? Les défauts voisins sont des commentaires, pas des correctifs.'],
  ];
  for (const [label, text] of questions) {
    const row = node('div', 'rv-kv');
    row.append(node('span', null, label), node('span', null, text));
    box.append(row);
  }
  box.append(node('p', 'rv-note', 'La fusion reste un geste humain sur GitHub. Le cockpit prépare la décision, il ne fusionne pas.'));
  const command = pr && pr.task ? `grimoire task verify ${pr.task.id}` : pr ? `gh pr view ${pr.number} -R ${pr.repo}` : '';
  if (command) {
    const formula = node('span', 'rv-formula');
    formula.append(node('b', null, '›'), command);
    box.append(formula);
  }
  ctx.inspector.replaceChildren(box);
  ctx.dock.echo(command);
}

function unknownBanner(sources, retry) {
  const box = node('div', 'rv-unknown');
  box.dataset.state = 'unknown';
  box.setAttribute('role', 'status');
  const text = node('span');
  const names = sources.map((s) => `${s.project.name || s.project.slug} (${s.detail || 'raison non communiquée'})`).join(', ');
  text.append(node('strong', null, 'État des PR inconnu'), ` pour ${names}. Ce qui n'a pas été lu n'est pas compté ici.`);
  const button = node('button', 'btn', 'Réessayer');
  button.type = 'button';
  button.addEventListener('click', retry);
  box.append(text, button);
  return box;
}

export async function mount(root, ctx) {
  injectStyles();
  const wrap = node('div', 'rv-wrap');
  wrap.dataset.view = 'revue';
  root.append(wrap);
  let wanted = ctx.params.pr ? `${ctx.params.of || ''}#${ctx.params.pr}` : '';
  let prs = [];
  let rows = [];
  let polls = 0;
  let timer = 0;
  ctx.signal.addEventListener('abort', () => clearTimeout(timer));

  const main = node('main', 'rv-main');
  const list = node('nav', 'rv-list');
  list.setAttribute('aria-label', 'PR à revoir');

  const select = (key, { focus = false, remember = true } = {}) => {
    const matches = (pr) => (key.startsWith('#') ? `#${pr.number}` === key : keyOf(pr) === key);
    const pr = prs.find(matches) || prs[0];
    if (!pr) return;
    const missing = key && !prs.some(matches) ? key.split('#').pop() : '';
    for (const row of rows) {
      const on = row.pr === pr;
      row.el.setAttribute('aria-current', String(on));
      row.el.tabIndex = on ? 0 : -1;
    }
    renderMain(ctx, main, pr, { missing });
    renderAside(ctx, pr);
    wanted = ctx.scope.all ? keyOf(pr) : `#${pr.number}`;
    if (focus) rows.find((r) => r.pr === pr)?.el.focus();
    if (remember) {
      ctx.setItem?.({ view: 'revue', pr: pr.number, of: ctx.scope.all ? (pr.project && pr.project.slug) || '' : '' });
    }
  };

  const render = (data) => {
    prs = data.prs || [];
    const head = node('div', 'rv-list-head');
    head.append(node('h2', null, 'PR à revoir'), node('span', 'rv-n', String(prs.length)), node('span', 'rv-hint', 'les plus prêtes d’abord'));
    const items = node('ul', 'rv-items');
    rows = [];
    for (const pr of prs) {
      const li = node('li', 'rv-pr');
      li.dataset.pr = String(pr.number);
      li.dataset.ci = pr.checks.state;
      li.tabIndex = -1;
      const title = node('span', 'rv-pr-title', `#${pr.number} ${pr.title}`);
      title.title = pr.title;
      const sub = node('span', 'rv-pr-sub');
      sub.append(node('span', 'mono', `+${pr.additions} −${pr.deletions}`));
      if (ctx.scope.all && pr.project) sub.append(` · ${pr.project.name || pr.project.slug}`);
      li.append(title, ciBadge(pr.checks), sub);
      li.addEventListener('click', () => select(keyOf(pr), { focus: true }));
      li.addEventListener('keydown', (event) => {
        const index = rows.findIndex((r) => r.el === li);
        if (event.key === 'ArrowDown' || event.key === 'j') {
          event.preventDefault();
          if (rows[index + 1]) select(keyOf(rows[index + 1].pr), { focus: true });
        } else if (event.key === 'ArrowUp' || event.key === 'k') {
          event.preventDefault();
          if (rows[index - 1]) select(keyOf(rows[index - 1].pr), { focus: true });
        }
      });
      items.append(li);
      rows.push({ pr, el: li });
    }
    list.replaceChildren(head, items);

    const unknown = (data.sources || []).filter((s) => s.state === 'unknown');
    const loading = (data.sources || []).some((s) => s.state === 'loading');
    const banner = unknown.length ? unknownBanner(unknown, () => refresh()) : null;
    if (prs.length) {
      select(wanted, { remember: Boolean(ctx.params.pr) });
      if (banner) main.prepend(banner);
    } else {
      const sentence = loading
        ? 'Lecture des PR en cours sur GitHub : la liste arrive.'
        : unknown.length
          ? 'Aucune PR n’a pu être lue : leur état est inconnu, pas nul.'
          : 'Aucune PR ouverte n’attend de revue dans cette portée.';
      const title = loading ? 'Lecture en cours' : unknown.length ? 'État des PR inconnu' : 'Aucune PR à revoir';
      main.replaceChildren(spaceHead('PR à revoir', 'Les PR ouvertes du dépôt, l’état de leur CI et leur tâche.'));
      if (banner) main.append(banner);
      main.append(ctx.empty(title, sentence, null, { label: 'Retour à À traiter', run: () => ctx.goto('a-traiter', {}) },
        { art: 'i-empty-inbox' }));
      renderAside(ctx, null);
    }
    clearTimeout(timer);
    if (loading && polls < 12) {
      polls += 1;
      timer = setTimeout(refresh, 1500);
    }
  };

  const refresh = async () => {
    let data;
    try {
      data = await ctx.api.reviews(ctx.scope.all);
    } catch (error) {
      if (ctx.signal.aborted) return;
      main.replaceChildren(ctx.empty('Revue indisponible', String(error.message || error), null, {
        label: 'Réessayer', run: () => refresh(),
      }));
      return;
    }
    if (ctx.signal.aborted) return;
    render(data);
  };

  wrap.append(list, main);
  ctx.live.on(() => refresh());
  await refresh();
}
