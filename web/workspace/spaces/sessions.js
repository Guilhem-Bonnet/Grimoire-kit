// Vue « Sessions » de Travail (P33) — artboard `Sessions`.
//
// Le board dit ce que chaque tâche attend ; cette vue dit ce que fait chaque
// SESSION : laquelle travaille, laquelle attend ta réponse, laquelle tient des
// claims sans plus donner signe de vie. Données : `api.sessions(portfolio)`
// (`GET /api/workspace/sessions`, `workspace_sessions.sessions_view`) ; la
// vivacité y est celle de `missions.liveness`, seule définition de « vivante »
// partagée avec la Flotte.
//
// Gestes, tous branchés à ce qui existe déjà — aucun bouton inerte :
// - « Envoyer une consigne » : `taskAction(id, 'comment', { kind: 'directive' })`
//   sur la tâche que la session tient (la consigne est livrée à la session par le
//   hook) — absent quand la session ne tient aucune tâche ;
// - « Ouvrir la décision » : la vue Décisions de Travail, quand la session attend
//   ta réponse sur une question ;
// - « Voir la tâche » : le Tableau, la tâche sélectionnée ;
// - « Copier » : la commande de reprise (`claude --resume <id>`), confirmée sur place.
// « Relancer la session » et « Libérer les claims… » de la maquette n'ont aucune
// route : ils ne sont pas dessinés (ADR-007, la webview n'exécute rien).

import { actionStatus, badge, icon, kpiRow, monogram, setActionStatus } from '../shell.js';
import { agoText } from '../live.js';

const STYLE_ID = 'ss-styles';

// Libellé, ton du badge et glyphe de chaque état (une forme par état, jamais la couleur seule).
export const STATE = {
  active: { label: 'active', tone: 'acc', icon: 'i-session-live' },
  waiting: { label: 'attend ta réponse', tone: 'warn', icon: 'alert' },
  silent: { label: 'silencieuse', tone: '', icon: 'i-session-unknown' },
  ended: { label: 'terminée', tone: 'ok', icon: 'i-session-done' },
};

// Le nom court d'un hôte (grimoire.bridges.schemas.HostId) ; une valeur inconnue s'affiche telle quelle.
const HOST_LABEL = {
  'host-claude-code-cli': 'Claude Code', 'host-github-copilot': 'Copilot', 'host-codex': 'Codex',
  'host-cursor': 'Cursor', 'host-gemini-cli': 'Gemini CLI', 'host-opencode': 'OpenCode',
};
export const hostLabel = (host) => (host ? HOST_LABEL[host] || host : 'hôte inconnu');

function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined && value !== null) node.textContent = value;
  return node;
}

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ss-kpi .kpi-item[data-tone="warn"] { background: var(--warn-soft); border-color: transparent; }
    .ss-kpi .kpi-item[data-tone="warn"] .kpi-val { color: var(--warn); }
    .ss-table td { white-space: nowrap; }
    .ss-table tbody tr:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
    .ss-table tbody tr[aria-current="true"] { box-shadow: inset 0 0 0 1px var(--acc); }
    .ss-state { display: inline-flex; align-items: center; gap: 5px; }
    .ss-state .ico { width: 14px; height: 14px; flex: none; }
    .ss-id { font-family: var(--mono); font-size: var(--t-s); font-weight: 500; color: var(--ink); }
    /* La colonne de la tâche prend la place qui reste et s'ellipse : le signe reste visible. */
    .ss-table td.ss-task-cell { width: 100%; max-width: 0; }
    .ss-task { display: flex; flex-direction: column; min-width: 0; }
    .ss-task-title { font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ss-task-project { font-size: var(--t-min); color: var(--ink3); display: flex; align-items: center; gap: 6px; }
    .ss-act { display: inline-flex; align-items: flex-end; gap: 2px; height: 14px; }
    .ss-act i { width: 7px; border-radius: 1px; background: var(--acc); }
    .ss-act i.zero { background: var(--line); }
    .ss-sign { color: var(--ink3); font-size: var(--t-min); text-align: right; }
    .ss-muted { color: var(--ink3); font-size: var(--t-min); }
    .ss-notice { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-size: var(--t-s); color: var(--ink2); }
    .ss-insp { display: flex; flex-direction: column; gap: var(--sp-4); min-height: 100%; }
    .ss-insp-head { display: flex; flex-direction: column; gap: 6px; }
    .ss-insp-head h3 { margin: 0; font-size: var(--t-l); line-height: 1.35; font-weight: 600; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .ss-insp-head h3 .ss-id { font-size: var(--t-m, var(--t-s)); }
    .ss-why { margin: 0; color: var(--ink2); font-size: var(--t-s); line-height: 1.45; }
    .ss-kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); font-size: var(--t-s); overflow-wrap: anywhere; }
    .ss-kv > :first-child { color: var(--ink3); font-size: var(--t-min); font-weight: 500; }
    .ss-kv ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; }
    .ss-actions { display: flex; flex-direction: column; gap: var(--sp-2); }
    .ss-actions .row { display: flex; gap: var(--sp-2); }
    .ss-actions .row > .btn { flex: 1 1 0; justify-content: center; }
    .ss-form { display: flex; flex-direction: column; gap: 6px; }
    .ss-form textarea { min-height: 56px; font: inherit; font-size: var(--t-s); }
    .ss-resume { display: flex; flex-direction: column; gap: 6px; margin-top: auto; }
    .ss-resume-box { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); padding: 6px 10px; min-width: 0; }
    .ss-cmd { font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
    .ss-copy { height: 24px; padding: 0 8px; flex: none; background: transparent; border-color: transparent; color: var(--ink2); }
    .ss-copy .ico { width: 14px; height: 14px; }
    .ss-cmd b { color: var(--rub); font-weight: 500; margin-right: 6px; }
  `;
  document.head.append(style);
}

function sinceNode(iso) {
  const node = document.createElement('time');
  node.dateTime = iso;
  node.dataset.since = iso;
  node.title = iso;
  node.textContent = agoText(Date.now() - Date.parse(iso));
  return node;
}

const hasDate = (iso) => !!iso && !Number.isNaN(Date.parse(iso));
const shortId = (id) => (id.length > 12 ? `${id.slice(0, 8)}…` : id);

export function stateBadge(session) {
  const def = STATE[session.state] || STATE.silent;
  const node = badge(def.label, def.tone, `session ${def.label}`);
  node.classList.add('ss-state');
  node.dataset.state = session.state;
  node.prepend(icon(def.icon));
  return node;
}

// Activité des quinze dernières minutes : sept barres, hauteur relative à la plus
// active. `null` : rien ne trace les appels d'outil de ce projet — « non lue », jamais zéro.
function activityNode(activity) {
  if (!activity) {
    const none = el('span', 'ss-muted', 'non lue');
    none.title = "Aucun TraceLedger lisible dans ce projet : l'activité n'est pas mesurée.";
    return none;
  }
  const box = el('span', 'ss-act');
  const label = `${activity.calls} appel(s) d'outil en ${activity.window_minutes} min`;
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', label);
  box.title = label;
  const top = Math.max(...activity.buckets, 1);
  for (const count of activity.buckets) {
    const bar = el('i', count ? '' : 'zero');
    bar.style.height = `${count ? 3 + Math.round((11 * count) / top) : 2}px`;
    box.append(bar);
  }
  return box;
}

function taskSummary(session) {
  const tasks = session.tasks || [];
  if (!tasks.length) return 'aucune tâche tenue';
  if (session.question) return `${session.question.task} — attend ta réponse`;
  if (tasks.length === 1) return tasks[0].title || tasks[0].id;
  return `${tasks.map((t) => t.id).join(', ')} — ${tasks.length} claims`;
}

function personaText(session) {
  if (session.persona && session.model) return `${session.persona} · ${session.model}`;
  return session.persona || '';
}

function signNode(session) {
  const at = session.last_sign && session.last_sign.at;
  if (!hasDate(at)) return el('span', 'ss-muted', 'aucun signe lu');
  return sinceNode(at);
}

function sessionRow(session, selected, onSelect) {
  const tr = document.createElement('tr');
  tr.dataset.session = session.id;
  tr.dataset.project = session.project.slug;
  tr.dataset.state = session.state;
  tr.tabIndex = 0;
  if (selected) tr.setAttribute('aria-current', 'true');
  tr.addEventListener('click', () => onSelect(session));
  // Entrée (P58) passe par le contrôleur de liste de la coque (le clic de la ligne).
  tr.addEventListener('keydown', (event) => {
    if (event.key === ' ') { event.preventDefault(); onSelect(session); }
  });
  const stateCell = el('td');
  stateCell.append(stateBadge(session));
  const hostCell = el('td', 'lbl', hostLabel(session.host));
  const idCell = el('td');
  idCell.append(Object.assign(el('span', 'ss-id', shortId(session.id)), { title: session.id }));
  const taskCell = el('td', 'ss-task-cell');
  const task = el('span', 'ss-task');
  const title = el('span', 'ss-task-title', taskSummary(session));
  title.title = (session.tasks || []).map((t) => `${t.id} ${t.title}`).join('\n') || title.textContent;
  const project = el('span', 'ss-task-project');
  project.append(monogram(session.project.name), el('span', null, session.project.name));
  task.append(title, project);
  taskCell.append(task);
  const personaCell = el('td');
  const persona = personaText(session);
  if (persona) {
    const b = badge(persona, '', session.model ? persona : `${persona} — modèle non consigné par l'hôte`);
    personaCell.append(b);
  } else {
    personaCell.append(Object.assign(el('span', 'ss-muted', '—'), { title: 'persona non lue' }));
  }
  const actCell = el('td');
  actCell.append(activityNode(session.activity));
  const signCell = el('td', 'ss-sign');
  signCell.append(signNode(session));
  tr.append(stateCell, hostCell, idCell, taskCell, personaCell, actCell, signCell);
  return tr;
}

/** Les quatre tuiles de la maquette — un chiffre lu, jamais inventé. */
function kpis(summary) {
  const row = kpiRow([
    { id: 'active', label: 'Actives', value: summary.active },
    { id: 'waiting', label: 'Attendent ta réponse', value: summary.waiting, tone: 'warn', icon: 'alert' },
    { id: 'silent', label: 'Silencieuses avec claim', value: summary.silent_with_claims, icon: 'i-session-unknown' },
    { id: 'ended', label: 'Terminées · 24 h', value: summary.ended_24h },
  ]);
  row.classList.add('ss-kpi');
  return row;
}

/** Le tableau et ses tuiles. `data` : la réponse de `api.sessions` ; `error` : sa lecture ratée. */
export function renderSessions(root, ctx, { data, error, selectedId, onSelect }) {
  injectStyles();
  const box = el('div', 'ss-view');
  box.dataset.role = 'sessions';
  box.style.display = 'flex';
  box.style.flexDirection = 'column';
  box.style.gap = 'var(--sp-3)';
  if (error || !data) {
    const notice = el('div', 'ss-notice');
    notice.dataset.role = 'sessions-error';
    notice.append(el('strong', null, 'Sessions illisibles'), el('span', null, (error && error.message) || 'motif non communiqué'));
    box.append(notice);
    root.append(box);
    return;
  }
  box.append(kpis(data.summary));
  const unreadable = (data.projects || []).filter((p) => p.state !== 'ok');
  if (unreadable.length) {
    const notice = el('div', 'ss-notice');
    notice.dataset.role = 'sessions-unreadable';
    notice.append(el('span', null, `${unreadable.length} projet(s) non lu(s) :`));
    for (const p of unreadable) notice.append(el('span', null, `${p.name || p.slug} — ${p.reason}`));
    box.append(notice);
  }
  if (data.probe === 'muette') {
    box.append(el('div', 'ss-muted', "Sonde de l'hôte muette (claude agents) : la vivacité vient des seuls journaux de session."));
  }
  if (!data.sessions.length) {
    box.append(ctx.empty(
      'Sessions',
      "Aucune session vue depuis 24 h : aucun journal de session, aucun claim tenu, rien que la sonde de l'hôte ne liste.",
      'grimoire task list',
      undefined,
      { art: 'i-empty-sessions' },
    ));
    root.append(box);
    return;
  }
  const wrap = el('div', 'tbl-wrap');
  const table = el('table', 'tbl tbl-pick ss-table');
  table.setAttribute('aria-label', 'Sessions');
  const head = el('tr');
  for (const label of ['État', 'Hôte', 'Session', 'Projet · tâche tenue', 'Persona · modèle', `${data.activity_minutes} dernières min`, 'Signe']) {
    head.append(el('th', null, label));
  }
  head.lastElementChild.style.textAlign = 'right';
  const thead = el('thead');
  thead.append(head);
  const tbody = el('tbody');
  for (const session of data.sessions) tbody.append(sessionRow(session, session.id === selectedId, onSelect));
  table.append(thead, tbody);
  wrap.append(table);
  box.append(wrap);
  root.append(box);
}

function explanation(session) {
  const n = session.claims;
  const since = session.last_sign && hasDate(session.last_sign.at) ? agoText(Date.now() - Date.parse(session.last_sign.at)).replace(/^il y a /, '') : '';
  const reason = (session.liveness && session.liveness.reason) || '';
  if (session.state === 'waiting') {
    return session.question
      ? `Attend ta réponse sur ${session.question.task} : « ${session.question.text} »`
      : `Attend une réponse (${reason}).`;
  }
  if (session.state === 'active') {
    return `Travaille : ${reason}.` + (n ? ` Tient ${n} claim${n > 1 ? 's' : ''}.` : ' Ne tient aucun claim.');
  }
  if (session.state === 'ended') return `Session terminée (${reason}).` + (n ? ` Ses ${n} claim(s) restent posés jusqu'à expiration de leur bail.` : '');
  if (n) {
    const many = n > 1;
    return `Tient ${many ? `${n} claims` : 'un claim'} sans signe de vie ${since ? `depuis ${since}` : 'lu'}. `
      + `${many ? 'Les tâches restent bloquées' : 'La tâche reste bloquée'} tant que la session ne reprend pas ou que le bail n'a pas expiré.`;
  }
  return `Aucun signe de vie ${since ? `depuis ${since}` : 'lu'} (${reason}). Elle ne tient aucun claim : rien n'est bloqué.`;
}

function kv(label, value) {
  const line = el('div', 'ss-kv');
  line.append(el('span', null, label));
  if (value instanceof Node) line.append(value); else line.append(el('span', null, value));
  return line;
}

/** Le panneau de détail d'une session. `go` : `{ decisions(task), task(task) }` — les vues de Travail. */
export function renderSessionInspector(ctx, session, { portfolio, go, onWritten, notice = '' }) {
  injectStyles();
  const panel = ctx.inspector;
  panel.replaceChildren();
  panel.dataset.owner = 'sessions';
  panel.dataset.task = '';
  const box = el('div', 'ss-insp');
  box.dataset.role = 'session-inspector';
  box.dataset.session = session.id;

  const head = el('div', 'ss-insp-head');
  head.append(el('span', 'lbl', `${hostLabel(session.host)} · ${session.project.name}`));
  const h3 = el('h3');
  h3.append(Object.assign(el('span', 'ss-id', shortId(session.id)), { title: session.id }), stateBadge(session));
  head.append(h3, el('p', 'ss-why', explanation(session)));
  box.append(head);

  const facts = el('div');
  const tasks = session.tasks || [];
  if (tasks.length) {
    const list = el('ul');
    for (const t of tasks) list.append(el('li', null, `${t.id} ${t.title}`));
    facts.append(kv('Claims', list));
  } else {
    facts.append(kv('Claims', 'aucun'));
  }
  const sign = el('span');
  sign.dataset.role = 'last-sign';
  if (session.last_sign && hasDate(session.last_sign.at)) {
    sign.append(sinceNode(session.last_sign.at), ` · ${session.last_sign.source}`);
  } else {
    sign.textContent = 'aucun signe lu';
  }
  facts.append(kv('Dernier signe', sign));
  const pending = el('span', null, tasks.length ? `${session.directives_pending} en attente` : 'aucune tâche à qui en poser');
  pending.dataset.role = 'directives-pending';
  facts.append(kv('Consignes', pending));
  facts.append(kv('Persona · modèle', personaText(session) || 'non lue'));
  box.append(facts);

  // `notice` : le retour d'un geste qui a déclenché ce re-rendu (la consigne posée) — gardé sur place.
  const status = actionStatus(notice, notice ? 'ok' : '');
  const actions = el('div', 'ss-actions');
  const buttons = [];
  if (session.question && go) {
    const answer = el('button', 'btn pri', 'Ouvrir la décision');
    answer.type = 'button';
    answer.dataset.role = 'open-decision';
    answer.addEventListener('click', () => go.decisions(session.question.task, session.project.slug));
    actions.append(answer);
  }
  const canWrite = portfolio || !ctx.host.readOnly;
  if (tasks.length && canWrite) {
    const send = el('button', 'btn', 'Envoyer une consigne');
    send.type = 'button';
    send.dataset.role = 'send-directive';
    send.addEventListener('click', () => {
      send.disabled = true;
      actions.append(directiveForm(ctx, session, portfolio, status, () => { send.disabled = false; }, onWritten));
      actions.querySelector('textarea')?.focus();
    });
    buttons.push(send);
  }
  if (tasks.length === 1 && go) {
    const see = el('button', 'btn', 'Voir la tâche');
    see.type = 'button';
    see.dataset.role = 'open-task';
    see.addEventListener('click', () => go.task(tasks[0].id, session.project.slug));
    buttons.push(see);
  }
  if (buttons.length) {
    const line = el('div', 'row');
    line.append(...buttons);
    actions.append(line);
  }
  if (actions.childElementCount) box.append(actions);
  box.append(status);
  box.append(resumeBlock(session));
  panel.append(box);
  ctx.dock.echo(session.resume_command || `grimoire task list${portfolio ? ' --all-projects' : ''}`);
}

// Consigne livrée à la session par la tâche qu'elle tient : le même `comment`
// (kind `directive`) que l'inspecteur d'une tâche, refus du service rendu sur place.
function directiveForm(ctx, session, portfolio, status, onClose, onWritten) {
  const form = el('div', 'ss-form');
  form.dataset.role = 'directive-form';
  const tasks = session.tasks || [];
  let target = tasks[0];
  if (tasks.length > 1) {
    const select = el('select', 'input');
    select.setAttribute('aria-label', 'Tâche qui reçoit la consigne');
    for (const t of tasks) {
      const option = el('option', null, `${t.id} ${t.title}`);
      option.value = t.id;
      select.append(option);
    }
    select.addEventListener('change', () => { target = tasks.find((t) => t.id === select.value) || tasks[0]; });
    form.append(select);
  }
  const area = el('textarea', 'input');
  area.dataset.role = 'directive-text';
  area.placeholder = 'Consigne pour la session — elle la reçoit à son prochain tour';
  area.setAttribute('aria-label', 'Consigne');
  const submit = el('button', 'btn pri', 'Envoyer');
  submit.type = 'button';
  submit.dataset.role = 'directive-submit';
  const cancel = el('button', 'btn', 'Annuler');
  cancel.type = 'button';
  cancel.addEventListener('click', () => { form.remove(); onClose(); });
  submit.addEventListener('click', async () => {
    const value = area.value.trim();
    if (!value) { setActionStatus(status, "Une consigne vide n'a rien à dire.", 'bad'); return; }
    submit.disabled = true;
    ctx.dock.echo(`grimoire task comment ${target.id} "${value}" --kind directive`);
    try {
      const body = { text: value, kind: 'directive' };
      const result = portfolio
        ? await ctx.api.portfolioTaskAction(target.id, 'comment', body, session.project.slug)
        : await ctx.api.taskAction(target.id, 'comment', body);
      if (result && result.blocked) {
        const why = (result.refusals || []).map((r) => `${r.evidence} — ${r.reason}`).join(' ; ');
        setActionStatus(status, `Refusé : ${why || 'motif non communiqué'}`, 'bad');
        submit.disabled = false;
        return;
      }
      const done = `Consigne posée sur ${target.id} : la session la reçoit à son prochain tour.`;
      setActionStatus(status, done, 'ok');
      area.value = '';
      form.remove();
      onClose();
      onWritten && onWritten(done);
    } catch (err) {
      setActionStatus(status, `${err && err.code === 0 ? 'Échec' : 'Refusé'} : ${(err && err.message) || 'motif non communiqué'}`, 'bad');
      submit.disabled = false;
    }
  });
  const line = el('div', 'row');
  line.append(cancel, submit);
  form.append(area, line);
  return form;
}

// « Reprendre à la main » : la commande à coller dans un terminal, jamais exécutée ici.
// Un hôte sans commande connue : l'identifiant, à copier tel quel.
function resumeBlock(session) {
  const block = el('div', 'ss-resume');
  block.dataset.role = 'resume';
  block.append(el('span', 'lbl', 'Reprendre à la main'));
  const value = session.resume_command || session.id;
  const line = el('div', 'ss-resume-box');
  const cmd = el('span', 'ss-cmd');
  cmd.dataset.role = 'resume-command';
  cmd.title = value;
  cmd.append(el('b', null, '›'), value);
  const copy = el('button', 'btn ss-copy', 'Copier');
  copy.type = 'button';
  copy.dataset.role = 'copy-resume';
  copy.setAttribute('aria-label', session.resume_command ? 'Copier la commande de reprise' : "Copier l'identifiant de la session");
  const status = actionStatus('', '');
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = 'Copié';
      copy.prepend(icon('check'));
      setActionStatus(status, session.resume_command ? 'Commande copiée : colle-la dans un terminal.' : 'Identifiant copié.', 'ok');
    } catch {
      setActionStatus(status, 'Copie impossible ici : sélectionne la commande ci-dessus.', 'bad');
    }
  });
  line.append(cmd, copy);
  block.append(line);
  if (!session.resume_command) {
    block.append(el('span', 'ss-muted', `${hostLabel(session.host)} n'a pas de commande de reprise connue : reprends la session par son identifiant.`));
  }
  block.append(status);
  return block;
}

