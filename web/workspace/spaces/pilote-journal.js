// Travail › Décisions › « Décidé par le pilote » (P83).
//
// Une carte par décision que le pilote automatique a prise, lue au ledger (`autopilot.decided`) : la question,
// l'option prise, la justification, la source de la recommandation (l'option recommandée par la question, ou le
// jugement du pilote avec sa confiance) et l'heure. « Revenir » n'apparaît que si le geste l'est encore : rouvrir une
// tâche abandonnée, ou rendre à l'humain une tâche que personne n'a reprise.

import { api } from '../api.js';

const SOURCES = { recommended: 'option recommandée par la question', judgment: 'jugement du pilote' };
const CONFIDENCES = { high: 'confiance haute', medium: 'confiance moyenne', low: 'confiance basse' };
const REVERTS = { reopen: 'Revenir · rouvrir la tâche', hold: 'Revenir · rendre à l’humain' };

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;
  return n;
}

function whenText(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return '';
  const day = moment.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
  return `${day}, ${moment.getHours()} h ${String(moment.getMinutes()).padStart(2, '0')}`;
}

function card(entry, reload) {
  const li = node('li', 'pj-card');
  li.dataset.role = 'pilot-decision';
  li.dataset.task = entry.task_id;
  li.dataset.question = entry.question_id;
  const head = node('div', 'pj-head');
  const time = node('time', 'pj-time', whenText(entry.ts));
  time.dateTime = entry.ts;
  head.append(node('strong', 'pj-title', entry.title || entry.task_id), time);
  const rows = node('dl', 'pj-kv');
  const add = (label, value, role) => {
    if (!value) return;
    const dt = node('dt', null, label);
    const dd = node('dd', null, value);
    if (role) dd.dataset.role = role;
    rows.append(dt, dd);
  };
  add('Question', entry.question, 'pj-question');
  add('Option prise', entry.option, 'pj-option');
  add('Justification', entry.justification, 'pj-why');
  const source = SOURCES[entry.source] || entry.source;
  add('Source', [source, CONFIDENCES[entry.confidence]].filter(Boolean).join(' · '), 'pj-source');
  li.append(head, rows);
  const foot = node('div', 'pj-foot');
  if (entry.reverted_at) {
    foot.append(node('span', 'pj-done', `Repris par l’humain ${whenText(entry.reverted_at)}`));
  } else if (entry.revert) {
    const status = node('span', 'pj-status');
    status.setAttribute('role', 'status');
    const button = node('button', 'btn', REVERTS[entry.revert] || 'Revenir');
    button.type = 'button';
    button.dataset.role = 'pilot-revert';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api.autopilotRevert(entry.task_id, entry.question_id);
        await reload();
      } catch (error) {
        button.disabled = false;
        status.textContent = `Retour impossible. ${error && error.message ? error.message : ''}`;
      }
    });
    foot.append(button, status);
  } else {
    foot.append(node('span', 'pj-done', 'Plus réversible : la tâche a repris sa réponse'));
  }
  li.append(foot);
  return li;
}

/** Le bloc du journal, ajouté sous les décisions en attente. `onChanged` relit la vue après un « Revenir ». */
export function pilotJournalBlock(onChanged) {
  const section = node('section', 'pj');
  section.dataset.role = 'pilot-journal';
  section.setAttribute('aria-labelledby', 'pj-title');
  const title = node('h3', 'pj-h', 'Décidé par le pilote');
  title.id = 'pj-title';
  section.append(title);
  const body = node('div');
  section.append(body);
  const draw = async () => {
    let data;
    try {
      data = await api.autopilotDecisions();
    } catch (error) {
      body.replaceChildren(node('p', 'pj-empty', `Journal illisible. ${error && error.message ? error.message : ''}`));
      return;
    }
    const list = (data && data.decisions) || [];
    if (!list.length) {
      body.replaceChildren(node('p', 'pj-empty', 'Le pilote n’a encore rien décidé.'));
      return;
    }
    const ul = node('ul', 'pj-list');
    for (const entry of list) ul.append(card(entry, async () => { await draw(); if (onChanged) onChanged(); }));
    body.replaceChildren(ul);
  };
  draw();
  return section;
}

const STYLE_ID = 'pj-styles';
if (!document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
.pj { margin-top: var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-3); }
.pj-h { margin: 0; font-size: var(--t-s); font-weight: 600; }
.pj-empty { margin: 0; color: var(--ink2); }
.pj-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-3); }
.pj-card { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); padding: var(--sp-3) var(--sp-4);
  display: flex; flex-direction: column; gap: var(--sp-2); }
.pj-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--sp-3); }
.pj-time { color: var(--ink3); font-size: var(--t-min); white-space: nowrap; }
.pj-kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); margin: 0; }
.pj-kv dt, .pj-kv dd { margin: 0; padding: var(--sp-1) 0; border-top: 1px solid var(--line); font-size: var(--t-s); overflow-wrap: anywhere; }
.pj-kv dt { color: var(--ink3); }
.pj-foot { display: flex; align-items: center; gap: var(--sp-3); }
.pj-done, .pj-status { color: var(--ink2); font-size: var(--t-min); }
`;
  document.head.append(style);
}
