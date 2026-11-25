// Travail › Décisions › « Décidé par le pilote » (P83).
//
// Une carte par décision que le pilote automatique a prise, lue au ledger (`autopilot.decided`) : la question,
// l'option prise, la justification, la source de la recommandation (l'option recommandée par la question, ou le
// jugement du pilote avec sa confiance) et l'heure. « Revenir » n'apparaît que si le geste l'est encore : rouvrir une
// tâche abandonnée, ou rendre à l'humain une tâche que personne n'a reprise.

import { api } from '../api.js';

const SOURCES = {
  recommended: 'option recommandée par la question',
  judgment: 'jugement du pilote',
  default: 'option recommandée, appliquée par défaut',
  precedent: 'réponse humaine antérieure à une question de la même classe',
};
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
  if (entry.precedent) {
    add('Précédent', `${entry.precedent.id} — « ${entry.precedent.question} »`, 'pj-precedent');
  }
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
