// L'écran « En direct » de la Flotte (lot B, artboards « Proposition A :
// Tissage », « En direct, une session ouverte » et « Terminaux »).
//
// Données : uniquement `GET /api/workspace/fleet/live` (lot A, #1193). Le front
// ne lit aucun journal par lui-même : ce que le serveur n'a pas lu reste
// inconnu, jamais deviné.
//
// Deux vues, un seul geste : « Fils » (la vue principale) dessine chaque
// session comme un fil sur la ligne de temps de la fenêtre choisie, la ligne
// « maintenant » à droite ; « Terminaux » montre les vingt derniers appels
// d'outil de chaque session, comme l'artboard du même nom. Cliquer un nom de
// fil le met en avant (les autres passent à 0,18) et ouvre le panneau de
// détail. Au clavier, j / k passent de fil en fil, Entrée ouvre, Échap ferme.
//
// La géométrie est pure (`fils-geometry.js`, sans DOM) : elle est mesurée par
// `tests/unit/test_workspace_live_view.py` sous Node, sans navigateur.
//
// Aucun bouton sans route : la consigne n'existe que si la session tient une
// tâche et que l'hôte autorise l'écriture ; « Ouvrir la tâche » que si le lien
// est fourni ; « Copier la reprise » que si le serveur en a une.

import {
  actionStatus, badge, copyText, failureText, icon, listNav, setActionStatus, spaceHead,
} from '../shell.js';
import { agoText, costText } from '../live.js';
import {
  WINDOW_CHOICES, WINDOW_DEFAULT_MINUTES, clockLabel, layoutFleetThreads, modelLabel, providerToken, threadName,
} from './fils-geometry.js';

const STYLE_ID = 'fl-styles';

const WINDOW_LABEL = { 60: '1 h', 240: '4 h', 1440: '24 h' };

const LIVENESS = {
  active: { label: 'vivante', tone: 'acc', icon: 'i-session-live' },
  waiting: { label: 'attend une réponse', tone: 'warn', icon: 'alert' },
  silent: { label: 'muette', tone: '', icon: 'i-session-unknown' },
  ended: { label: 'terminée', tone: 'ok', icon: 'i-session-done' },
};

const CSS = `
.fl-wrap { padding: var(--sp-6); max-width: 1440px; width: 100%; box-sizing: border-box; margin: 0 auto;
  display: flex; flex-direction: column; gap: var(--sp-4); }
.fl-attention { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--line); border-radius: var(--r); background: var(--card); color: var(--ink2); font-size: var(--t-s); }
.fl-attention strong { color: var(--ink); font-weight: 600; }
.fl-attention .btn { margin-left: auto; }
.fl-seg { display: flex; }
.fl-board { position: relative; border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card);
  box-shadow: var(--shadow); overflow-x: auto; }
.fl-svg { display: block; width: 100%; min-width: 720px; height: auto; }
.fl-thread { fill: none; stroke-linecap: round; }
.fl-thread.pale { opacity: 0.45; }
.fl-dim { opacity: 0.18; }
.fl-edge { fill: none; stroke: var(--ink3); stroke-width: 1.2; stroke-dasharray: 4 4; opacity: 0.8; }
.fl-now { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 3; }
.fl-sep { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 4; }
.fl-project { fill: var(--ink3); font-size: var(--t-s); font-weight: 500; }
.fl-change-diamond { fill: var(--card); stroke: var(--ink); stroke-width: 1.3; }
.fl-change-label { fill: var(--ink2); font-size: var(--t-min); }
.fl-end-silent { fill: var(--card); stroke: var(--ink3); stroke-width: 1.5; }
.fl-end-live { stroke: none; }
.fl-end-ended { fill: var(--ink3); }
.fl-loop { fill: var(--bad); stroke: var(--card); stroke-width: 1.5; }
.fl-loop-label { fill: var(--bad); font-size: var(--t-min); font-weight: 500; }
.fl-marker { fill: var(--ink3); }
.fl-name { cursor: pointer; outline: none; }
.fl-name-hit { fill: transparent; }
.fl-name:focus-visible .fl-name-hit { stroke: var(--acc); stroke-width: 1.5; }
.fl-name[aria-current="true"] .fl-name-hit { fill: var(--acc-soft); }
.fl-name-text { fill: var(--ink); font-size: var(--t-s); font-weight: 500; }
.fl-name-model { fill: var(--ink3); font-size: var(--t-min); }
.fl-name-ring { fill: none; stroke-width: 2; }
.fl-terms { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: var(--sp-3); }
.fl-terminal { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow);
  display: flex; flex-direction: column; min-width: 0; overflow: hidden; cursor: pointer; }
.fl-terminal:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
.fl-term-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--line); }
.fl-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.fl-term-name { font-weight: 500; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.fl-term-model { color: var(--ink3); font-size: var(--t-min); }
.fl-term-body { margin: 0; padding: var(--sp-2) var(--sp-3); background: var(--term); color: var(--termink);
  font-family: var(--mono); font-size: var(--t-min); line-height: 1.5; max-height: 220px; overflow: auto; white-space: pre-wrap;
  overflow-wrap: anywhere; }
.fl-term-empty { padding: var(--sp-3); color: var(--ink3); font-size: var(--t-s); }
.fl-insp { display: flex; flex-direction: column; gap: var(--sp-4); min-height: 100%; }
.fl-insp h3 { margin: 0; font-size: var(--t-l); font-weight: 600; display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.fl-insp-sub { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.fl-kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 10px; padding: 8px 0; border-top: 1px solid var(--line);
  font-size: var(--t-s); overflow-wrap: anywhere; }
.fl-kv > :first-child { color: var(--ink3); font-size: var(--t-min); font-weight: 500; }
.fl-kv b { font-weight: 500; color: var(--ink); }
.fl-journey { display: flex; flex-direction: column; gap: 0; margin: 0; padding: 0; list-style: none; }
.fl-journey li { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: var(--sp-2); padding: 6px 0;
  border-top: 1px solid var(--line); font-size: var(--t-s); }
.fl-journey time { color: var(--ink3); font-family: var(--mono); font-size: var(--t-min); }
.fl-term { margin: 0; padding: var(--sp-2); border-radius: var(--r); background: var(--term); color: var(--termink);
  font-family: var(--mono); font-size: var(--t-min); line-height: 1.5; max-height: 260px; overflow: auto;
  white-space: pre-wrap; overflow-wrap: anywhere; }
.fl-actions { display: flex; flex-direction: column; gap: var(--sp-2); }
.fl-actions .row { display: flex; flex-wrap: wrap; gap: var(--sp-2); }
.fl-form { display: flex; flex-direction: column; gap: var(--sp-2); }
.fl-form textarea { font: inherit; font-size: var(--t-s); padding: 6px 8px; border: 1px solid var(--line);
  border-radius: var(--r); background: var(--e1); color: var(--ink); resize: vertical; min-height: 64px; }
.fl-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.fl-error { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); padding: var(--sp-2) var(--sp-3);
  border: 1px dashed var(--line); border-radius: var(--r); color: var(--ink2); font-size: var(--t-s); }
@container canvas (max-width: 720px) {
  .fl-wrap { padding: var(--sp-3); }
  .fl-terms { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: no-preference) {
  .fl-thread { stroke-dasharray: 2400; stroke-dashoffset: 2400; animation: fl-draw 900ms ease-out forwards; }
  @keyframes fl-draw { to { stroke-dashoffset: 0; } }
}
`;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  return node;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function livenessBadge(session) {
  const state = LIVENESS[session.liveness] || LIVENESS.ended;
  return badge(state.label, state.tone);
}

function livenessIcon(session) {
  const state = LIVENESS[session.liveness] || LIVENESS.ended;
  return icon(state.icon);
}

/** Le point d'attention : un seul, le plus net, avec une action. */
function attentionFor(data) {
  const sessions = data.sessions || [];
  // Une boucle ne compte que dans la fenêtre affichée : la géométrie l'a déjà
  // détectée (trois erreurs au même libellé), le bandeau ne la recompte pas.
  const loop = layoutFleetThreads(data).loops[0];
  const looping = loop && sessions.find((session) => session.id === loop.session_id);
  if (looping) return { session: looping, text: 'Une session tourne en boucle : même échec, trois fois.' };
  const waiting = sessions.find((session) => session.liveness === 'waiting');
  if (waiting) return { session: waiting, text: 'Une session attend ta réponse.' };
  const silent = sessions.find((session) => session.liveness === 'silent');
  if (silent) {
    const since = clockLabel(silent.last_at);
    return { session: silent, text: since ? `Une session est muette depuis ${since}.` : 'Une session est muette.' };
  }
  return null;
}

function sentence(data) {
  const sessions = data.sessions || [];
  const projects = new Set(sessions.map((session) => session.project).filter(Boolean));
  if (!sessions.length) return `Aucune session dans la fenêtre de ${WINDOW_LABEL[data.window_minutes] || `${data.window_minutes} min`}.`;
  const live = sessions.filter((session) => session.liveness === 'active' || session.liveness === 'waiting').length;
  const parts = [plural(sessions.length, 'session', 'sessions')];
  if (live) parts.push(`${live} ${live > 1 ? 'vivantes' : 'vivante'}`);
  if (projects.size) parts.push(plural(projects.size, 'projet', 'projets'));
  return parts.join(', ') + '.';
}

function windowSegment(current, onPick) {
  const seg = el('div', 'seg fl-seg');
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', 'Fenêtre');
  for (const minutes of WINDOW_CHOICES) {
    const button = el('button', 'btn', WINDOW_LABEL[minutes] || `${minutes} min`);
    button.type = 'button';
    button.dataset.window = String(minutes);
    button.setAttribute('aria-pressed', String(minutes === current));
    button.addEventListener('click', () => { if (minutes !== current) onPick(minutes); });
    seg.append(button);
  }
  return seg;
}

function viewSegment(current, onPick) {
  const seg = el('div', 'seg fl-seg');
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', 'Vue');
  for (const [id, label] of [['fils', 'Fils'], ['terminaux', 'Terminaux']]) {
    const button = el('button', 'btn', label);
    button.type = 'button';
    button.dataset.view = id;
    button.setAttribute('aria-pressed', String(id === current));
    button.addEventListener('click', () => { if (id !== current) onPick(id); });
    seg.append(button);
  }
  return seg;
}

/** Le parcours du modèle : les lanes lues au journal, ou le modèle unique. */
function modelJourney(session) {
  const lanes = Array.isArray(session.lanes) ? session.lanes.filter((lane) => lane && lane.model) : [];
  const box = el('div');
  if (!lanes.length && !(session.model_changes || []).length) {
    box.append(el('p', 'fl-note', `Un seul modèle sur la fenêtre : ${modelLabel(session.model && (session.model.current || session.model.requested))}.`));
    return box;
  }
  const list = el('ul', 'fl-journey');
  for (const lane of lanes) {
    const row = el('li');
    const from = clockLabel(lane.from);
    const to = clockLabel(lane.to);
    row.append(el('time', null, from ? (to && to !== from ? `${from}–${to}` : from) : '—'));
    const text = el('span');
    text.append(el('b', null, modelLabel(lane.model)));
    if (lane.effort) text.append(el('span', null, `, effort ${lane.effort}`));
    row.append(text);
    list.append(row);
  }
  for (const change of session.model_changes || []) {
    const row = el('li');
    row.append(el('time', null, clockLabel(change.at) || '—'));
    row.append(el('span', null, `${modelLabel(change.from)} → ${modelLabel(change.to)}${change.kind === 'fallback' ? ' (repli)' : ''}`));
    list.append(row);
  }
  box.append(list);
  return box;
}

function tailText(session) {
  const calls = Array.isArray(session.tail) ? session.tail : [];
  if (!calls.length) return '';
  return calls.map((call) => {
    const when = clockLabel(call.at);
    const head = [when, call.tool || 'outil', call.summary || ''].filter(Boolean).join('  ');
    return call.excerpt ? `${head}\n  ${call.excerpt}` : head;
  }).join('\n');
}

/** Le panneau de détail (inspecteur) d'une session. */
function renderDetail(ctx, session) {
  const panel = ctx.inspector;
  panel.replaceChildren();
  panel.dataset.owner = 'fils';
  const box = el('div', 'fl-insp');
  box.dataset.role = 'fils-inspector';
  box.dataset.session = session.id;

  const head = el('div');
  const h3 = el('h3');
  h3.append(el('span', null, threadName(session)), livenessBadge(session));
  head.append(h3);
  const facts = el('div');

  const model = session.model || {};
  const add = (label, value) => {
    const row = el('div', 'fl-kv');
    row.append(el('span', null, label));
    row.append(value instanceof Node ? value : el('span', null, String(value)));
    facts.append(row);
  };
  add('Projet', session.project || '—');
  add('Rôle', session.role || 'inconnu');
  add('Fournisseur', model.provider || 'inconnu');
  add('Modèle servi', modelLabel(model.current || model.requested));
  if (model.requested && model.current && model.requested !== model.current) add('Modèle demandé', modelLabel(model.requested));
  if (model.effort) add('Effort', model.effort);
  add('Dernier signe', session.last_at ? agoText(Date.now() - Date.parse(session.last_at)) : 'aucun signe lu');
  add('Tours', String((session.turns && session.turns.count) || 0));
  if (session.usage) add('Jetons', `${session.usage.input || 0} entrée, ${session.usage.output || 0} sortie`);
  if (session.usage) add('Coût', costText(session.usage.cost_usd !== undefined ? { status: session.usage.cost_usd === null ? 'unknown' : 'exact', usd: session.usage.cost_usd } : null));
  if (session.branch) add('Branche', session.branch);
  if (session.worktree) add('Worktree', session.worktree);
  if (session.pr && session.pr.url) {
    const link = el('a', null, `#${session.pr.number || ''}`);
    link.href = session.pr.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    add('PR', link);
  }
  box.append(head, facts);

  const journey = el('div');
  journey.append(el('h3', null, 'Parcours du modèle'), modelJourney(session));
  box.append(journey);

  const terminal = el('div');
  terminal.append(el('h3', null, 'Terminal'));
  const text = tailText(session);
  if (text) {
    const pre = el('pre', 'fl-term');
    pre.dataset.role = 'tail';
    pre.textContent = text;
    terminal.append(pre);
  } else {
    terminal.append(el('p', 'fl-note', 'Aucun appel d’outil lu dans la fenêtre.'));
  }
  box.append(terminal);

  const status = actionStatus('', '');
  const actions = el('div', 'fl-actions');
  const row = el('div', 'row');
  const canWrite = !ctx.host.readOnly || (ctx.host.kind === 'cockpit' && ctx.scope && ctx.scope.all);
  if (session.task_id && canWrite) {
    const send = el('button', 'btn', 'Envoyer une consigne');
    send.type = 'button';
    send.dataset.role = 'send-directive';
    send.addEventListener('click', () => {
      send.disabled = true;
      actions.insertBefore(directiveForm(ctx, session, status), status);
      actions.querySelector('textarea')?.focus();
    });
    row.append(send);
  }
  if (session.task_id && session.links && session.links.task) {
    const open = el('button', 'btn', 'Ouvrir la tâche');
    open.type = 'button';
    open.dataset.role = 'open-task';
    open.addEventListener('click', () => ctx.goto('travail', { task: session.task_id }));
    row.append(open);
  }
  if (session.links && session.links.resume) {
    const copy = el('button', 'btn', 'Copier la reprise');
    copy.type = 'button';
    copy.dataset.role = 'copy-resume';
    copy.append(icon('copy'), 'Copier la reprise');
    copy.addEventListener('click', () => copyText(session.links.resume, copy));
    row.append(copy);
  }
  if (row.childElementCount) actions.append(row);
  actions.append(status);
  if (actions.childElementCount) box.append(actions);
  panel.append(box);
}

/** La consigne : le geste existant `taskAction(id, 'comment', {kind:'directive'})`. */
function directiveForm(ctx, session, status) {
  const form = el('div', 'fl-form');
  form.dataset.role = 'directive-form';
  const area = el('textarea');
  area.setAttribute('aria-label', 'Consigne');
  area.placeholder = 'Consigne pour la session — elle la reçoit à son prochain tour';
  const line = el('div', 'row');
  const submit = el('button', 'btn pri', 'Envoyer');
  submit.type = 'button';
  submit.dataset.role = 'directive-submit';
  const cancel = el('button', 'btn', 'Annuler');
  cancel.type = 'button';
  cancel.addEventListener('click', () => form.remove());
  submit.addEventListener('click', async () => {
    const value = area.value.trim();
    if (!value) { setActionStatus(status, 'Une consigne vide n’a rien à dire.', 'bad'); return; }
    submit.disabled = true;
    try {
      const body = { text: value, kind: 'directive' };
      const result = ctx.host.kind === 'cockpit' && ctx.scope && ctx.scope.all
        ? await ctx.api.portfolioTaskAction(session.task_id, 'comment', body, session.project)
        : await ctx.api.taskAction(session.task_id, 'comment', body);
      if (result && result.blocked) {
        const why = (result.refusals || []).map((refusal) => refusal.reason).filter(Boolean).join(' ; ');
        setActionStatus(status, `Refusé : ${why || 'motif non communiqué'}`, 'bad');
        submit.disabled = false;
        return;
      }
      setActionStatus(status, `Consigne posée sur ${session.task_id} : la session la reçoit à son prochain tour.`, 'ok');
      area.value = '';
      form.remove();
    } catch (error) {
      setActionStatus(status, failureText(error), 'bad');
      submit.disabled = false;
    }
  });
  line.append(cancel, submit);
  form.append(area, line);
  return form;
}

/** Le board des fils : SVG pour la géométrie, HTML/SVG focalisable pour les noms. */
function renderThreads(ctx, data, state, rootWidth) {
  // Le viewBox suit la largeur réelle du board : sans ça, un SVG de 960 étiré
  // sur 1 200 px grossit tout le texte de 25 %.
  const available = rootWidth || (typeof window !== 'undefined' ? window.innerWidth - 280 : 960);
  const layout = layoutFleetThreads(data, { paddingTop: 30, width: Math.max(720, Math.min(1400, available - 64)) });
  const board = el('div', 'fl-board');
  board.dataset.role = 'fils-board';
  board.style.setProperty('--fl-height', `${layout.height}px`);
  const drawing = svg('svg', {
    class: 'fl-svg',
    viewBox: `0 0 ${layout.width} ${layout.height}`,
    role: 'group',
    'aria-label': 'Fils des sessions',
  });

  // Noms des projets, à gauche ; un pointillé sépare deux projets.
  for (const project of layout.projects) {
    const label = svg('text', { class: 'fl-project', x: 8, y: Math.max(14, project.top + 2) });
    label.textContent = project.name || 'projet inconnu';
    drawing.append(label);
    if (layout.projects.indexOf(project) > 0) {
      drawing.append(svg('line', {
        class: 'fl-sep', x1: 0, y1: project.top - 11, x2: layout.width - 8, y2: project.top - 11,
      }));
    }
  }

  // Les arêtes : un sous-agent part et revient ; un lancé ne fait que partir.
  for (const edge of layout.edges) {
    const path = svg('path', { class: 'fl-edge', d: edge.departure, 'data-edge': `${edge.from}>${edge.to}` });
    drawing.append(path);
    if (edge.return) {
      drawing.append(svg('path', { class: 'fl-edge', d: edge.return, 'data-edge': `${edge.to}>${edge.from}` }));
      drawing.append(svg('circle', { class: 'fl-marker', cx: edge.return_point.x, cy: edge.return_point.y, r: 3 }));
      const title = svg('title');
      title.textContent = edge.label || 'retour';
      path.append(title);
    }
  }

  // Les fils, découpés par changement de modèle.
  for (const thread of layout.threads) {
    // Le tick : pointillé gris, comme sur l'artboard ; les fils de session
    // prennent la teinte de leur fournisseur.
    const tint = thread.tick ? 'var(--ink3)' : `var(${thread.label.token})`;
    const dash = thread.tick ? '; stroke-dasharray: 5 5' : '';
    const segments = thread.segments.length ? thread.segments : [{ path: thread.path, pale: false }];
    for (const segment of segments) {
      drawing.append(svg('path', {
        class: `fl-thread${thread.tick ? ' tick' : ''}${segment.pale ? ' pale' : ''}`,
        d: segment.path,
        'data-session': thread.id,
        style: `stroke: ${tint}; stroke-width: ${thread.tick ? 1.5 : thread.label.stroke}${dash}`,
      }));
    }
    // Le losange au point de bascule et son étiquette directe.
    for (const change of thread.changes) {
      drawing.append(svg('path', {
        class: 'fl-change-diamond',
        d: `M ${change.x - 5} ${change.y} L ${change.x} ${change.y - 5} L ${change.x + 5} ${change.y} L ${change.x} ${change.y + 5} Z`,
        'data-session': thread.id,
      }));
      const tip = svg('title');
      tip.textContent = change.label;
      drawing.lastChild.append(tip);
    }
    // Les repères du journal (une infobulle native porte le libellé).
    for (const marker of thread.markers) {
      const dot = svg('circle', { class: 'fl-marker', cx: marker.x, cy: marker.y, r: 2.5 });
      const title = svg('title');
      title.textContent = marker.label || marker.kind;
      dot.append(title);
      drawing.append(dot);
    }
    // Une boucle : nœud rouge et une seule étiquette.
    for (const loop of layout.loops.filter((entry) => entry.session_id === thread.id)) {
      drawing.append(svg('circle', { class: 'fl-loop', cx: loop.x, cy: loop.y, r: 5, 'data-session': thread.id }));
    }
    // Fin du fil : un cercle vide pour une muette, plein pour une terminée,
    // rien pour une vivante (elle touche la ligne « maintenant »).
    if (thread.end.kind === 'silent') {
      drawing.append(svg('circle', { class: 'fl-end-silent', cx: thread.end.x, cy: thread.end.y, r: 5, 'data-session': thread.id }));
    } else if (thread.end.kind === 'ended') {
      drawing.append(svg('circle', { class: 'fl-end-ended', cx: thread.end.x, cy: thread.end.y, r: 3.5 }));
    } else {
      drawing.append(svg('circle', { class: 'fl-end-live', cx: thread.end.x, cy: thread.end.y, r: 4, style: `fill: ${tint}` }));
    }
    // L'étiquette directe, au bout du fil : nom et modèle, sans légende.
    const name = svg('g', {
      class: 'fl-name',
      'data-session': thread.id,
      tabindex: '-1',
      role: 'button',
      'aria-label': `${thread.label.name}, ${thread.label.model}`,
    });
    name.append(svg('rect', {
      class: 'fl-name-hit', x: layout.plot_right + 6, y: thread.y - 17, width: layout.width - layout.plot_right - 10, height: 34, rx: 6,
    }));
    const ring = svg('circle', { class: 'fl-name-ring', cx: layout.plot_right - 5, cy: thread.y, r: 5, style: `stroke: ${tint}` });
    const nameText = svg('text', { class: 'fl-name-text', x: layout.plot_right + 12, y: thread.y - 3 });
    nameText.textContent = thread.label.short;
    const modelText = svg('text', { class: 'fl-name-model', x: layout.plot_right + 12, y: thread.y + 11 });
    modelText.textContent = thread.label.model;
    name.append(ring, nameText, modelText);
    const title = svg('title');
    title.textContent = `${thread.label.name} — ${thread.label.model}${thread.end.label ? ` (${thread.end.label})` : ''}`;
    name.append(title);
    name.addEventListener('click', () => select(thread.id, { open: true }));
    drawing.append(name);
  }

  // Les étiquettes directes, placées sans recouvrement par la géométrie.
  for (const label of layout.labels) {
    const text = svg('text', {
      class: label.kind === 'loop' ? 'fl-loop-label' : 'fl-change-label',
      x: label.x,
      y: label.y,
      'text-anchor': label.anchor,
      'data-session': label.session_id,
    });
    text.textContent = label.text;
    drawing.append(text);
  }

  // La ligne « maintenant », à droite.
  drawing.append(svg('line', {
    class: 'fl-now', x1: layout.now_x, y1: 6, x2: layout.now_x, y2: layout.height - 6,
  }));
  const nowLabel = svg('text', { class: 'fl-project', x: layout.now_x - 4, y: 14, 'text-anchor': 'end' });
  nowLabel.textContent = 'maintenant';
  drawing.append(nowLabel);

  board.append(drawing);
  listNav(board, {
    rows: '.fl-name',
    key: 'session',
    mark: 'aria-current',
    onMove: (row) => select(row.dataset.session, { open: false }),
    onActivate: (row) => select(row.dataset.session, { open: true }),
  });

  function highlight(id) {
    for (const node of board.querySelectorAll('[data-session]')) {
      const mine = node.getAttribute('data-session') === id;
      node.classList.toggle('fl-dim', Boolean(id) && !mine);
      if (node.classList.contains('fl-name')) node.setAttribute('aria-current', String(mine));
    }
  }

  function select(id, { open }) {
    if (open) {
      state.selected = id;
      state.highlight = id;
    } else {
      state.highlight = id;
    }
    highlight(open ? id : state.highlight || state.selected);
    if (!open) return;
    const session = (data.sessions || []).find((row) => row.id === id);
    if (!session) return;
    renderDetail(ctx, session);
    ctx.openPanel('inspector', { detail: true });
  }
  const focus = state.highlight || state.selected;
  if (focus) highlight(focus);
  return board;
}

function renderTerminals(ctx, data, state) {
  const grid = el('div', 'fl-terms');
  const sessions = data.sessions || [];
  if (!sessions.length) {
    grid.append(el('p', 'fl-note', 'Aucun terminal dans la fenêtre.'));
    return grid;
  }
  for (const session of sessions) {
    const card = el('article', 'fl-terminal');
    card.dataset.session = session.id;
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Terminal de ${threadName(session)}`);
    const head = el('div', 'fl-term-head');
    const dot = el('span', 'fl-dot');
    dot.style.background = `var(${providerToken(session.model && session.model.provider)})`;
    head.append(dot, el('span', 'fl-term-name', threadName(session)));
    head.append(el('span', 'fl-term-model', modelLabel(session.model && (session.model.current || session.model.requested))));
    head.append(livenessIcon(session));
    card.append(head);
    const text = tailText(session);
    if (text) {
      const pre = el('pre', 'fl-term-body');
      pre.textContent = text;
      card.append(pre);
    } else {
      card.append(el('p', 'fl-term-empty', 'Aucun appel d’outil lu dans la fenêtre.'));
    }
    const pick = () => {
      state.selected = session.id;
      renderDetail(ctx, session);
      ctx.openPanel('inspector', { detail: true });
    };
    card.addEventListener('click', pick);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); }
    });
    grid.append(card);
  }
  return grid;
}

export async function mount(root, ctx) {
  injectStyles();
  const wanted = Number(ctx.params && ctx.params.window);
  let windowMinutes = WINDOW_CHOICES.includes(wanted) ? wanted : WINDOW_DEFAULT_MINUTES;
  let view = ctx.params && ctx.params.vue === 'terminaux' ? 'terminaux' : 'fils';
  const state = { selected: null, highlight: null };
  let data = null;
  let error = null;
  let sequence = 0;
  let drawnWidth = 0;

  ctx.live.on(() => { load({ quiet: true }); }, { scope: () => (ctx.scope.all ? 'portfolio' : 'project') });

  async function load({ quiet = false } = {}) {
    if (ctx.signal.aborted) return;
    const ticket = ++sequence;
    if (!quiet) {
      root.replaceChildren(...ctx.loading('Lecture des journaux de session…', 5, 'lines'));
    }
    try {
      const payload = await ctx.api.fleetLive(windowMinutes);
      if (ticket !== sequence || ctx.signal.aborted) return;
      data = payload;
      error = null;
    } catch (failure) {
      if (ticket !== sequence || ctx.signal.aborted) return;
      data = null;
      error = failure;
    }
    draw();
  }

  function draw() {
    const wrap = el('div', 'fl-wrap');
    const actions = [windowSegment(windowMinutes, (minutes) => { windowMinutes = minutes; load(); })];
    actions.push(viewSegment(view, (next) => { view = next; draw(); }));
    const head = spaceHead('En direct', data ? sentence(data) : 'Lecture des journaux de session…', ...actions);
    wrap.append(head);

    if (error || !data) {
      const notice = el('div', 'fl-error');
      notice.append(el('strong', null, 'Flotte en direct illisible'));
      notice.append(el('span', null, (error && error.message) || 'motif non communiqué'));
      const retry = el('button', 'btn', 'Réessayer');
      retry.type = 'button';
      retry.addEventListener('click', () => load());
      notice.append(retry);
      wrap.append(notice);
      root.replaceChildren(wrap);
      return;
    }

    const attention = attentionFor(data);
    if (attention) {
      const line = el('div', 'fl-attention');
      line.append(icon('alert'));
      line.append(el('span', null, attention.text));
      line.append(el('strong', null, threadName(attention.session)));
      const see = el('button', 'btn', 'Voir le fil');
      see.type = 'button';
      see.addEventListener('click', () => {
        const label = root.querySelector(`.fl-name[data-session="${CSS.escape(attention.session.id)}"]`);
        if (label) { label.click(); return; }
        state.selected = attention.session.id;
        state.highlight = attention.session.id;
        renderDetail(ctx, attention.session);
        ctx.openPanel('inspector', { detail: true });
      });
      line.append(see);
      wrap.append(line);
    }

    if (view === 'terminaux') {
      wrap.append(renderTerminals(ctx, data, state));
    } else if (!(data.sessions || []).length) {
      wrap.append(el('p', 'fl-note', 'Aucun fil dans la fenêtre : aucune session Claude Code n’a écrit de journal récemment.'));
    } else {
      drawnWidth = root.clientWidth;
      wrap.append(renderThreads(ctx, data, state, drawnWidth));
    }
    root.replaceChildren(wrap);

    // Le détail suit la sélection après un re-rendu (rafraîchissement du pouls).
    const still = (data.sessions || []).find((session) => session.id === state.selected);
    if (still) {
      renderDetail(ctx, still);
      ctx.openPanel('inspector', { detail: true });
    }
  }

  // Le panneau de détail rétrécit la toile : le viewBox est recalculé à la
  // nouvelle largeur (sinon le dessin est réduit et le texte devient
  // illisible). Seuil de 40 px pour ne pas redessiner à chaque pixel.
  if (typeof ResizeObserver !== 'undefined') {
    let pending = 0;
    const observer = new ResizeObserver(() => {
      if (!data || view !== 'fils' || !drawnWidth) return;
      if (Math.abs(root.clientWidth - drawnWidth) < 40) return;
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => draw());
    });
    observer.observe(root);
    if (ctx.signal) ctx.signal.addEventListener('abort', () => observer.disconnect(), { once: true });
  }

  if (ctx.signal) {
    ctx.signal.addEventListener('abort', () => {
      const panel = ctx.inspector;
      if (panel && panel.dataset.owner === 'fils') panel.replaceChildren();
    }, { once: true });
  }
  // Échap ferme le détail : la sélection s'efface, l'inspecteur se vide.
  const onKey = (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || '')) return;
    if (!state.selected) return;
    state.selected = null;
    state.highlight = null;
    for (const node of root.querySelectorAll('[data-session]')) {
      node.classList.remove('fl-dim');
      if (node.classList.contains('fl-name')) node.setAttribute('aria-current', 'false');
    }
    if (ctx.inspector && ctx.inspector.dataset.owner === 'fils') ctx.inspector.replaceChildren();
  };
  document.addEventListener('keydown', onKey);
  if (ctx.signal) ctx.signal.addEventListener('abort', () => document.removeEventListener('keydown', onKey), { once: true });

  await load();
}
