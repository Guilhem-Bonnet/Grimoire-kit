// Bloc Activité (P69v2) — le fil de l'agent, dérivé du ledger, partagé par
// l'inspecteur de tâche (Travail › Tableau) et l'inspecteur de session
// (Travail › Sessions). Données : `api.taskFeed(id)` ou `api.sessionFeed(id)`
// (`workspace_session_feed.py`) — claim et transitions, portes, questions et
// réponses, notes de progression. Aucun geste d'écriture : la seule action est
// « Copier claude logs », la commande du contrat session (`session_link.py`),
// jamais exécutée ici.

import { actionStatus, copyText, icon, setActionStatus } from './shell.js';
import { agoText } from './live.js';

let injected = false;
function injectStyles() {
  if (injected) return;
  injected = true;
  const style = document.createElement('style');
  style.textContent = `
    .wa-activity { display: flex; flex-direction: column; gap: 6px; }
    .wa-feed { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
    .wa-feed li { display: flex; gap: 8px; align-items: baseline; font-size: 12px; min-width: 0; }
    .wa-feed .wa-when { color: var(--ink3); flex: 0 0 auto; font-variant-numeric: tabular-nums; }
    .wa-feed .wa-what { min-width: 0; overflow-wrap: anywhere; }
    .wa-feed li.bad .wa-what { color: var(--bad); }
    .wa-logs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .wa-logs code { font-size: 12px; overflow-wrap: anywhere; }
  `;
  document.head.append(style);
}

function when(at) {
  const span = document.createElement('span');
  span.className = 'wa-when';
  const stamp = Date.parse(at || '');
  span.textContent = Number.isNaN(stamp) ? '—' : agoText(Date.now() - stamp);
  if (at) span.title = at;
  return span;
}

/**
 * Le bloc, construit depuis un fil déjà lu. `feed` : la charge utile de
 * `taskFeed`/`sessionFeed` (`entries`, `total`, `note`, et la commande
 * `claude logs` — `logs_command` au fil de session, `session.logs_command`
 * au fil de tâche).
 */
export function activityBlock(feed, { heading = 'Activité' } = {}) {
  injectStyles();
  const block = document.createElement('div');
  block.className = 'wa-activity';
  block.dataset.role = 'activity-block';
  const title = document.createElement('h4');
  title.textContent = heading;
  block.append(title);

  const entries = (feed && feed.entries) || [];
  if (!entries.length) {
    const empty = document.createElement('p');
    empty.className = 'lbl';
    empty.dataset.role = 'activity-empty';
    empty.textContent = (feed && feed.note) || 'aucun événement au ledger pour ce fil.';
    block.append(empty);
  } else {
    if (feed.total > entries.length) {
      const more = document.createElement('p');
      more.className = 'lbl';
      more.textContent = `${feed.total - entries.length} entrée(s) plus anciennes — la chronologie complète est dans le dock Timeline.`;
      block.append(more);
    }
    const list = document.createElement('ul');
    list.className = 'wa-feed';
    list.dataset.role = 'activity-feed';
    for (const entry of entries) {
      const li = document.createElement('li');
      if (entry.failure) li.className = 'bad';
      li.dataset.kind = entry.kind || '';
      if (entry.task_id) li.dataset.task = entry.task_id;
      const what = document.createElement('span');
      what.className = 'wa-what';
      what.textContent = entry.label || entry.kind || '';
      li.append(when(entry.at), what);
      list.append(li);
    }
    block.append(list);
  }

  const logs = (feed && (feed.logs_command || (feed.session && feed.session.logs_command))) || '';
  if (logs) {
    const line = document.createElement('div');
    line.className = 'wa-logs';
    const code = document.createElement('code');
    code.dataset.role = 'logs-command';
    code.textContent = logs;
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn';
    copy.dataset.role = 'copy-logs';
    copy.append(icon('terminal'), Object.assign(document.createElement('span'), { textContent: 'Copier claude logs' }));
    const status = actionStatus('');
    copy.addEventListener('click', async () => {
      // `copyText` appelle le presse-papiers dans le geste même ; repli : la
      // commande est sélectionnée, prête pour Ctrl+C (pattern P68).
      if (await copyText(logs, copy)) {
        setActionStatus(status, 'Commande copiée : colle-la dans un terminal pour lire le journal de la session.', 'ok');
        return;
      }
      const selection = window.getSelection();
      if (selection) selection.selectAllChildren(code);
      setActionStatus(status, 'Copie refusée par le navigateur : la commande est sélectionnée, copie-la (Ctrl+C).');
    });
    line.append(code, copy, status);
    block.append(line);
  }
  return block;
}

/**
 * Le même bloc, chargé depuis l'API : squelette court, remplacé par le fil ou
 * l'échec nommé. `load` rend la promesse du fil (`() => ctx.api.taskFeed(id)`).
 */
export function activityBlockAsync(load, { heading = 'Activité', signal } = {}) {
  injectStyles();
  const holder = document.createElement('div');
  holder.dataset.role = 'activity-holder';
  const wait = document.createElement('p');
  wait.className = 'lbl';
  wait.textContent = 'Lecture du fil d’activité…';
  holder.append(wait);
  load().then(
    (feed) => {
      if (signal && signal.aborted) return;
      holder.replaceChildren(activityBlock(feed, { heading }));
    },
    (error) => {
      if (signal && signal.aborted) return;
      const fail = document.createElement('p');
      fail.className = 'lbl';
      fail.dataset.role = 'activity-error';
      fail.textContent = `fil d'activité illisible : ${(error && error.message) || 'erreur inconnue'}`;
      holder.replaceChildren(fail);
    },
  );
  return holder;
}
