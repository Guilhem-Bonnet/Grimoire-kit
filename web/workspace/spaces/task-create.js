// Travail › « Nouvelle tâche » (P35, maquette Travail : bouton primaire en tête).
//
// Le formulaire n'est dessiné par aucune maquette : il reprend la carte de la
// coque (style Douce — clair, arrondi, ombre douce, indigo seule couleur
// d'action) et les trois champs de `TaskService.add` que la CLI expose déjà :
// titre, critères d'acceptation (un par ligne), description. L'envoi passe par
// `api.taskAdd` → `POST /api/workspace/tasks/add` → `TaskService.add`, la même
// entrée que `grimoire task add` et l'outil MCP `task_add`.
//
// Le cockpit n'écrit des tâches que dans le projet qu'il sert (P02) : en portée
// « Tous les projets », ou sur un projet qu'on ne fait que regarder, le bouton
// est désactivé et la raison est écrite à côté. Le retour est sur place : la
// tâche créée s'annonce avec un lien qui l'ouvre ; un refus garde le formulaire
// et dit pourquoi.
//
// L'état vit au niveau du module : un rafraîchissement du tableau redessine
// la carte sans perdre la saisie ni la confirmation.

import { actionStatus, failureText, icon, setActionStatus } from '../shell.js';

const STYLE_ID = 'tc-styles';

const state = { open: false, title: '', acceptance: '', description: '', created: null, busy: false };

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .tc-card { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); border: 1px solid var(--line);
      border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); max-width: 720px; }
    .tc-card h3 { margin: 0; font-size: var(--t-m); line-height: 20px; font-weight: 600; color: var(--ink); }
    .tc-sub { margin: 0; color: var(--ink2); font-size: var(--t-s); }
    .tc-form { display: flex; flex-direction: column; gap: var(--sp-3); }
    .tc-form label { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .tc-lbl { font-size: 12px; line-height: 16px; font-weight: 500; color: var(--ink2); }
    .tc-lbl small { font-weight: 400; color: var(--ink2); }
    .tc-form input[type="text"], .tc-form textarea { width: 100%; box-sizing: border-box; min-height: var(--h-ctl);
      padding: 6px 9px; border: 1px solid var(--line); border-radius: var(--r); font: inherit; }
    .tc-form input[type="text"]:focus, .tc-form textarea:focus { border-color: var(--acc); }
    .tc-form textarea { min-height: 72px; resize: vertical; }
    .tc-actions { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; }
    .tc-done { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; }
    .tc-done .action-status { flex: 1 1 260px; }
    .tc-reason { color: var(--ink2); font-size: var(--t-s); max-width: 280px; }
    .tc-done a.btn { text-decoration: none; }
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

/** Pourquoi le cockpit refuse ici d'ouvrir une tâche, ou `''` s'il le peut. */
export function newTaskBlockedReason(ctx, portfolio) {
  if (portfolio) return 'Choisis un projet dans la portée : une tâche s’ouvre dans un projet, pas dans tous.';
  if (ctx.host.readOnly) return 'Lecture seule : ce cockpit n’ouvre des tâches que dans le projet qu’il sert.';
  return '';
}

/** Le bouton de tête « Nouvelle tâche », et la raison écrite quand il est désactivé. */
export function newTaskButton(ctx, { portfolio, onToggle }) {
  injectStyles();
  const button = el('button', 'btn pri', null, { type: 'button', 'data-role': 'new-task' });
  button.append(icon('plus'), el('span', null, 'Nouvelle tâche'));
  const reason = newTaskBlockedReason(ctx, portfolio);
  if (reason) {
    button.disabled = true;
    button.title = reason;
    const note = el('span', 'tc-reason', reason, { 'data-role': 'new-task-reason', id: 'tc-reason' });
    button.setAttribute('aria-describedby', 'tc-reason');
    return [button, note];
  }
  button.setAttribute('aria-expanded', String(state.open));
  button.addEventListener('click', () => {
    state.open = !state.open;
    onToggle();
  });
  return [button];
}

function confirmation(onOpen, onChange) {
  const { id, title, column } = state.created;
  const box = el('section', 'tc-card', null, { 'data-role': 'new-task-done', 'aria-label': 'Tâche créée' });
  const row = el('div', 'tc-done');
  const status = actionStatus(`Tâche ${id} créée${column ? ` dans « ${column} »` : ''} : ${title}`, 'ok');
  const link = el('a', 'btn', null, { href: `#travail?task=${encodeURIComponent(id)}`, 'data-role': 'open-created' });
  link.append(icon('external'), el('span', null, 'Ouvrir la tâche'));
  link.addEventListener('click', (event) => { event.preventDefault(); onOpen(id); });
  const close = el('button', 'btn', 'Fermer', { type: 'button' });
  close.addEventListener('click', () => { state.created = null; onChange(); });
  row.append(status, link, close);
  box.append(row);
  return box;
}

function form(ctx, onCreated, onChange, columnLabel) {
  const card = el('section', 'tc-card', null, { 'data-role': 'new-task-form', 'aria-label': 'Nouvelle tâche' });
  card.append(
    el('h3', null, 'Nouvelle tâche'),
    el('p', 'tc-sub', `Dans ${ctx.host.project || 'ce projet'}, en « Proposée ». Même entrée que grimoire task add.`),
  );
  const body = el('form', 'tc-form', null, { novalidate: '' });
  const field = (label, hint, control) => {
    const wrap = el('label');
    const lbl = el('span', 'tc-lbl', label);
    if (hint) lbl.append(' ', el('small', null, hint));
    wrap.append(lbl, control);
    return wrap;
  };
  const title = el('input', null, null, { type: 'text', name: 'title', 'data-role': 'new-task-title', autocomplete: 'off' });
  title.value = state.title;
  const acceptance = el('textarea', null, null, { name: 'acceptance', 'data-role': 'new-task-acceptance', rows: '3' });
  acceptance.value = state.acceptance;
  const description = el('textarea', null, null, {
    name: 'description', 'data-role': 'new-task-description', rows: '3', maxlength: '4000',
  });
  description.value = state.description;
  const status = actionStatus();
  for (const [node, key] of [[title, 'title'], [acceptance, 'acceptance'], [description, 'description']]) {
    node.addEventListener('input', () => {
      state[key] = node.value;
      // Un refus dit ce qui manque : dès qu'on corrige, il ne doit plus le prétendre.
      if (status.dataset.tone === 'bad') setActionStatus(status, '');
    });
  }
  const submit = el('button', 'btn pri', 'Créer la tâche', { type: 'submit', 'data-role': 'new-task-submit' });
  const cancel = el('button', 'btn', 'Annuler', { type: 'button' });
  submit.disabled = state.busy;
  cancel.addEventListener('click', () => {
    Object.assign(state, { open: false, title: '', acceptance: '', description: '' });
    onChange();
  });
  body.addEventListener('submit', async (event) => {
    event.preventDefault();
    const criteria = acceptance.value.split('\n').map((line) => line.trim()).filter(Boolean);
    if (!title.value.trim()) {
      setActionStatus(status, 'Refusé : donne un titre à la tâche.', 'bad');
      title.focus();
      return;
    }
    if (!criteria.length) {
      setActionStatus(status, 'Refusé : écris au moins un critère d’acceptation, un par ligne.', 'bad');
      acceptance.focus();
      return;
    }
    state.busy = true;
    submit.disabled = true;
    setActionStatus(status, 'Création…');
    ctx.dock.echo(`grimoire task add "${title.value.trim()}"` + criteria.map((c) => ` -a "${c}"`).join(''));
    try {
      const task = await ctx.api.taskAdd({ title: title.value.trim(), acceptance: criteria, description: description.value });
      state.busy = false;
      if (ctx.signal.aborted) return;
      Object.assign(state, {
        open: false, title: '', acceptance: '', description: '',
        created: { id: task.id, title: task.title, column: columnLabel(task.board) },
      });
      onCreated(task);
    } catch (error) {
      state.busy = false;
      submit.disabled = false;
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  const actions = el('div', 'tc-actions');
  actions.append(submit, cancel);
  body.append(
    field('Titre', null, title),
    field('Critères d’acceptation', 'un par ligne, au moins un', acceptance),
    field('Description', 'facultative : contexte, pourquoi, pistes', description),
    actions,
    status,
  );
  card.append(body);
  // Le formulaire vient d'être ouvert : le titre prend le focus.
  if (!state.title) queueMicrotask(() => { if (title.isConnected) title.focus(); });
  return card;
}

/**
 * Ce que la tête du tableau pose sous elle : la confirmation de la dernière
 * création, puis le formulaire s'il est ouvert ; `null` s'il n'y a rien.
 * `portfolio` ferme le formulaire (P02) sans perdre la saisie.
 */
export function newTaskBlock(ctx, { portfolio, onCreated, onOpen, onChange, columnLabel = (b) => b }) {
  injectStyles();
  const parts = [];
  if (state.created) parts.push(confirmation(onOpen, onChange));
  if (state.open && !newTaskBlockedReason(ctx, portfolio)) parts.push(form(ctx, onCreated, onChange, columnLabel));
  if (!parts.length) return null;
  const wrap = el('div', null, null, { 'data-role': 'new-task-block' });
  wrap.style.display = 'flex';
  wrap.style.flexDirection = 'column';
  wrap.style.gap = 'var(--sp-3)';
  wrap.append(...parts);
  return wrap;
}
