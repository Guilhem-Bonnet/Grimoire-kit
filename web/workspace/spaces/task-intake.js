// Travail › « Une idée ? » (I2) : de la phrase libre aux tâches prêtes.
//
// Le champ envoie la phrase à `POST /api/workspace/tasks/intake` (l'intake
// déterministe de `grimoire task add --intake`, sans LLM) et dessine les
// propositions en liste à cocher : titre, type, risque, vérifiabilité, critère
// proposé. Pour une tâche V0/V1, une « commande de vérification » est requise
// pour passer en prête ; sans elle la tâche est créée « Proposée » et le dit.
// « Créer N tâches (prêtes) » passe par `POST /api/workspace/tasks/intake/create`
// → `TaskService.add`, les gardes de `tasks/add`. Une proposition critique
// n'est jamais cochée d'office.
//
// L'état vit au niveau du module : un rafraîchissement du tableau redessine la
// carte sans perdre la saisie ni les cases.

import { actionStatus, failureText, icon, setActionStatus } from '../shell.js';
import { newTaskBlockedReason } from './task-create.js';

const STYLE_ID = 'ti-styles';

const state = { text: '', proposals: null, busy: false, result: null, focus: false };

const RISK_LABEL = { light: 'Léger', standard: 'Standard', strict: 'Strict', critical: 'Critique', release: 'Release' };
const RISK_ICON = { light: 'i-risk-light', standard: 'i-risk-standard', strict: 'i-risk-strict', critical: 'i-risk-critical', release: 'i-risk-critical' };
const TYPE_LABEL = {
  analysis: 'Analyse', architecture: 'Architecture', implementation: 'Implémentation', test: 'Test',
  documentation: 'Documentation', migration: 'Migration', security: 'Sécurité', operation: 'Opération', cleanup: 'Nettoyage',
};
const STATUS_LABEL = { ready: 'Prête', proposed: 'Proposée' };

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ti-card { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); border: 1px solid var(--line);
      border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); max-width: 720px; }
    .ti-card h3 { margin: 0; font-size: var(--t-m); line-height: 20px; font-weight: 600; color: var(--ink); }
    .ti-sub { margin: 0; color: var(--ink2); font-size: var(--t-s); }
    .ti-ask { display: flex; gap: var(--sp-2); align-items: flex-end; flex-wrap: wrap; }
    .ti-ask label { display: flex; flex-direction: column; gap: 4px; flex: 1 1 240px; min-width: 0; }
    .ti-lbl { font-size: 12px; line-height: 16px; font-weight: 500; color: var(--ink2); }
    .ti-card .ti-ask input, .ti-card .ti-cmd input { width: 100%; box-sizing: border-box; min-height: 44px; padding: 6px 9px;
      border: 1px solid var(--line); border-radius: var(--r); font: inherit; }
    .ti-ask input:focus, .ti-cmd input:focus { border-color: var(--acc); }
    .ti-card .btn { min-height: 44px; }
    .ti-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
    .ti-item { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-3); border: 1px solid var(--line);
      border-radius: var(--r); }
    .ti-item[data-risk="critical"] { border-color: var(--bad, var(--line)); }
    .ti-pick { display: flex; gap: var(--sp-2); align-items: flex-start; min-height: 44px; cursor: pointer; }
    .ti-pick input { width: 24px; height: 24px; margin: 10px 0 0; flex: none; }
    .ti-title { font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
    .ti-meta { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin: 0; padding: 0; list-style: none;
      font-size: var(--t-s); color: var(--ink2); }
    .ti-meta li { display: inline-flex; gap: 4px; align-items: center; }
    .ti-crit { margin: 0; color: var(--ink2); font-size: var(--t-s); overflow-wrap: anywhere; }
    .ti-cmd { display: flex; flex-direction: column; gap: 4px; }
    .ti-foot { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; }
    .ti-res { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
    .ti-res a { color: var(--acc); }
    .ti-why { color: var(--ink2); font-size: var(--t-s); display: block; }
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

/** Ouvre le champ depuis le raccourci `n` ou la palette : le focus ira à la saisie au prochain dessin. */
export function requestIdeaFocus() { state.focus = true; }

function selected() { return state.proposals.filter((p) => p.checked); }

function resultView(onOpen, onChange) {
  const box = el('section', 'ti-card', null, { 'data-role': 'idea-done', 'aria-label': 'Tâches créées' });
  const ready = state.result.filter((r) => r.status === 'ready').length;
  const status = actionStatus(`${state.result.length} tâche(s) créée(s), dont ${ready} prête(s).`, ready === state.result.length ? 'ok' : undefined);
  const list = el('ul', 'ti-res');
  for (const task of state.result) {
    const item = el('li', null, null, { 'data-id': task.id });
    const link = el('a', null, task.id, { href: `#travail?task=${encodeURIComponent(task.id)}` });
    link.addEventListener('click', (event) => { event.preventDefault(); onOpen(task.id); });
    item.append(link, ` · ${task.title} · ${STATUS_LABEL[task.status] || task.status}`);
    if (task.reason) item.append(el('span', 'ti-why', `Refus : ${task.reason}`, { 'data-role': 'idea-refus' }));
    list.append(item);
  }
  const close = el('button', 'btn', 'Fermer', { type: 'button' });
  close.addEventListener('click', () => { state.result = null; onChange(); });
  box.append(status, list, close);
  return box;
}

function proposalItem(proposal, index, count) {
  const item = el('li', 'ti-item', null, { 'data-role': 'idea-proposal', 'data-risk': proposal.risk_profile });
  const pick = el('label', 'ti-pick');
  const box = el('input', null, null, { type: 'checkbox', 'data-role': 'idea-check' });
  box.checked = proposal.checked;
  pick.append(box, el('span', 'ti-title', proposal.title));
  const meta = el('ul', 'ti-meta');
  const risk = el('li', null, null, { 'data-role': 'idea-risk' });
  risk.append(icon(RISK_ICON[proposal.risk_profile] || 'i-risk-standard'), `Risque : ${RISK_LABEL[proposal.risk_profile] || proposal.risk_profile}`);
  const type = el('li');
  type.append(icon(`i-task-${proposal.task_type}`), TYPE_LABEL[proposal.task_type] || proposal.task_type);
  meta.append(type, risk, el('li', null, `Vérifiabilité : ${proposal.verifiability}`, { 'data-role': 'idea-verif' }));
  const crit = el('p', 'ti-crit', `Critère proposé : ${proposal.acceptance[0]}`);
  item.append(pick, meta, crit);
  let cmdInput = null;
  if (proposal.needs_check) {
    const id = `ti-cmd-${index}`;
    const wrap = el('div', 'ti-cmd');
    wrap.append(el('label', 'ti-lbl', 'Commande de vérification (obligatoire pour passer en prête)', { for: id }));
    cmdInput = el('input', null, null, { type: 'text', id, 'data-role': 'idea-cmd', autocomplete: 'off', placeholder: 'pytest tests/unit -q' });
    cmdInput.value = proposal.check || '';
    cmdInput.addEventListener('input', () => { proposal.check = cmdInput.value; });
    wrap.append(cmdInput);
    item.append(wrap);
  }
  box.addEventListener('change', () => { proposal.checked = box.checked; item.dispatchEvent(new CustomEvent('ti-change', { bubbles: true })); });
  box.setAttribute('aria-describedby', cmdInput ? cmdInput.id : '');
  if (!cmdInput) box.removeAttribute('aria-describedby');
  void count;
  return item;
}

function card(ctx, onCreated, onChange) {
  const root = el('section', 'ti-card', null, { 'data-role': 'idea', 'aria-label': 'Une idée ?' });
  root.append(
    el('h3', null, 'Une idée ?'),
    el('p', 'ti-sub', 'Décris-la en une phrase : l’intake propose des tâches avec leur critère, sans IA. Même entrée que grimoire task add --intake.'),
  );
  const ask = el('form', 'ti-ask', null, { novalidate: '' });
  const label = el('label');
  label.append(el('span', 'ti-lbl', 'Une idée ?'));
  const input = el('input', null, null, { type: 'text', name: 'idea', 'data-role': 'idea-text', autocomplete: 'off', maxlength: '500' });
  input.value = state.text;
  label.append(input);
  const go = el('button', 'btn pri', 'Proposer des tâches', { type: 'submit', 'data-role': 'idea-submit' });
  go.disabled = state.busy;
  ask.append(label, go);
  const status = actionStatus();
  input.addEventListener('input', () => { state.text = input.value; });
  ask.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!input.value.trim()) {
      setActionStatus(status, 'Écris ton idée d’abord.', 'bad');
      input.focus();
      return;
    }
    state.busy = true;
    go.disabled = true;
    setActionStatus(status, 'Analyse…');
    ctx.dock.echo(`grimoire task add --intake "${input.value.trim()}"`);
    try {
      const out = await ctx.api.taskIntake(input.value.trim());
      state.busy = false;
      if (ctx.signal.aborted) return;
      state.proposals = out.proposals.map((p) => ({ ...p, check: '' }));
      state.result = null;
      onChange();
    } catch (error) {
      state.busy = false;
      go.disabled = false;
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  root.append(ask, status);

  if (state.proposals) {
    const list = el('ul', 'ti-list', null, { 'aria-label': 'Tâches proposées' });
    state.proposals.forEach((p, i) => list.append(proposalItem(p, i, state.proposals.length)));
    const create = el('button', 'btn pri', null, { type: 'button', 'data-role': 'idea-create' });
    const count = el('span', 'ti-count', null, { 'data-role': 'idea-count', role: 'status', 'aria-live': 'polite' });
    const sync = () => {
      const n = selected().length;
      create.replaceChildren(icon('plus'), el('span', null, `Créer ${n} tâche${n > 1 ? 's' : ''} (prête${n > 1 ? 's' : ''})`));
      create.disabled = state.busy || n === 0;
      count.textContent = `${n} proposition${n > 1 ? 's' : ''} cochée${n > 1 ? 's' : ''} sur ${state.proposals.length}`;
    };
    list.addEventListener('ti-change', sync);
    sync();
    const result = actionStatus();
    create.addEventListener('click', async () => {
      const chosen = selected();
      state.busy = true;
      create.disabled = true;
      setActionStatus(result, 'Création…');
      ctx.dock.echo(`grimoire task add --intake "${state.text.trim()}" --create`);
      try {
        const out = await ctx.api.taskIntakeCreate(chosen.map((p) => ({
          title: p.title, acceptance: p.acceptance, check: p.check || '',
        })));
        state.busy = false;
        if (ctx.signal.aborted) return;
        state.result = out.created;
        state.proposals = null;
        state.text = '';
        onCreated(out.created);
      } catch (error) {
        state.busy = false;
        sync();
        setActionStatus(result, failureText(error), 'bad');
      }
    });
    const foot = el('div', 'ti-foot');
    foot.append(create, count);
    root.append(list, foot, result);
  }
  if (state.focus) {
    state.focus = false;
    queueMicrotask(() => { if (input.isConnected) input.focus(); });
  }
  return root;
}

/**
 * Ce que le tableau pose sous sa tête : le champ « Une idée ? » avec ses
 * propositions, puis le récapitulatif de la dernière création. `null` quand le
 * cockpit ne peut pas écrire ici (portefeuille, lecture seule) : même garde que
 * « Nouvelle tâche ».
 */
export function ideaBlock(ctx, { portfolio, onCreated, onOpen, onChange }) {
  injectStyles();
  if (newTaskBlockedReason(ctx, portfolio)) return null;
  const wrap = el('div', null, null, { 'data-role': 'idea-block' });
  wrap.style.display = 'flex';
  wrap.style.flexDirection = 'column';
  wrap.style.gap = 'var(--sp-3)';
  wrap.append(card(ctx, onCreated, onChange));
  if (state.result) wrap.append(resultView(onOpen, onChange));
  return wrap;
}
