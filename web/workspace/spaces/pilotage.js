// Pilotage de l'orchestrateur — pause, reprise, disjoncteur (fiche Piloter et vue « Tous les projets »).
//
// Tout vient de `GET /api/workspace/pilotage` (grimoire/tools/workspace_pilotage.py) : l'état est relu, jamais celui du
// dernier clic. Les gestes (`POST pilotage/...`) sont des gestes d'humain : chacun passe par un dialogue qui dit CE QUI
// va se passer, demande la clé de contrôle (le jeton de session ne suffit pas, comme pour le pilote automatique ; la
// clé n'est gardée qu'en mémoire de page) et nomme le projet visé dans l'URL et dans le corps. Une pause posée par
// quelqu'un d'autre (règle d'usage, autre humain) s'affiche avec son auteur et son motif, et le dialogue de reprise le
// redit avant de la lever. Aucun réglage de rôle ni de cadence ici : ils ne sont que lus.

import { api } from '../api.js';
import { icon } from '../shell.js';
import { appliedPause, availableGestures, confirmLines, machineStops, pauseMeta } from './pilotage-logic.js';

function el(tag, className, textContent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent !== undefined) node.textContent = textContent;
  return node;
}

// ── Dialogue unique : ce qui va se passer, la clé de contrôle, un motif ──────────

let controlKey = '';

function field(labelText, control) {
  const label = el('label', 'pi-field');
  label.append(el('span', null, labelText), control);
  return label;
}

/**
 * Demande confirmation, clé de contrôle et motif. Résout `{ key, reason, mode }` ou `null` (annulé).
 * `error` : le refus de la tentative précédente (clé fausse), affiché dans le dialogue.
 */
export function askGesture({ title, lines, reasonLabel = '', reasonRequired = false, modes = null, confirmLabel, error = '' }) {
  return new Promise((resolve) => {
    const dialog = el('dialog', 'kn-confirm-project pi-dialog');
    dialog.setAttribute('aria-label', title);
    const form = el('form', 'kn-confirm-project-box pi-form');
    form.method = 'dialog';
    form.append(el('h2', null, title));
    for (const line of lines) form.append(el('p', null, line));
    let mode = null;
    if (modes) {
      mode = el('div', 'pi-modes');
      mode.setAttribute('role', 'radiogroup');
      mode.setAttribute('aria-label', 'Mode');
      modes.forEach((m, index) => {
        const label = el('label', 'pi-mode');
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'pi-mode';
        radio.value = m.value;
        radio.checked = index === 0;
        label.append(radio, el('span', null, m.label));
        mode.append(label);
      });
      form.append(mode);
    }
    let reason = null;
    if (reasonLabel) {
      reason = document.createElement('input');
      reason.type = 'text';
      reason.maxLength = 200;
      reason.required = reasonRequired;
      reason.autocomplete = 'off';
      form.append(field(reasonLabel, reason));
    }
    const key = document.createElement('input');
    key.type = 'password';
    key.autocomplete = 'off';
    key.spellcheck = false;
    key.required = true;
    key.value = controlKey;
    form.append(field('Clé de contrôle (cat ~/.grimoire/control/cockpit-key)', key));
    const note = el('p', 'pi-error', error);
    note.setAttribute('role', 'alert');
    note.hidden = !error;
    form.append(note);
    const actions = el('div', 'kn-confirm-project-actions');
    const cancel = el('button', 'btn ghost', 'Annuler');
    cancel.type = 'button';
    const ok = el('button', 'btn pri', confirmLabel);
    ok.type = 'submit';
    actions.append(cancel, ok);
    form.append(actions);
    dialog.append(form);
    document.body.append(dialog);

    let result = null;
    const done = () => { dialog.removeEventListener('close', done); dialog.remove(); resolve(result); };
    dialog.addEventListener('close', done);
    cancel.addEventListener('click', () => dialog.close());
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      controlKey = key.value.trim();
      const picked = mode ? mode.querySelector('input:checked') : null;
      result = { key: controlKey, reason: reason ? reason.value.trim() : '', mode: picked ? picked.value : null };
      dialog.close();
    });
    dialog.showModal();
    (reason || key).focus();
  });
}

/**
 * Joue un geste jusqu'au bout : dialogue, appel, et nouvelle demande de clé si elle est refusée (403).
 * Résout la réponse du serveur, ou `null` si l'humain a annulé.
 */
export async function runGesture(options, call) {
  let error = '';
  for (;;) {
    const answer = await askGesture({ ...options, error });
    if (!answer) return null;
    try {
      return await call(answer);
    } catch (failure) {
      if (failure && failure.code === 403) {
        controlKey = '';
        error = 'Clé refusée : relisez-la dans votre terminal.';
        continue;
      }
      throw failure;
    }
  }
}

const bodyOf = (answer, extra = {}) => ({ ...(answer.reason ? { reason: answer.reason } : {}), ...extra });

/** L'appel de l'API qui correspond à un geste du bloc. */
function callFor(gesture, slug, state) {
  return (answer) => {
    if (gesture === 'resume') return api.pilotageGesture('resume', slug, {}, answer.key);
    if (gesture === 'reset') return api.pilotageGesture('breaker/reset', slug, {}, answer.key);
    if (gesture === 'trip') return api.pilotageGesture('breaker/trip', slug, bodyOf(answer), answer.key);
    const replace = !!(state.pause && state.pause.project);
    return api.pilotageGesture('pause', slug, bodyOf(answer, { mode: gesture === 'hold' ? 'hold' : 'drain', ...(replace ? { replace: true } : {}) }), answer.key);
  };
}

function dialogOptions(gesture, state, name) {
  const titles = {
    drain: `Mettre « ${name} » en pause`, hold: `Arrêter net « ${name} »`, resume: `Reprendre « ${name} »`,
    reset: 'Refermer le disjoncteur', trip: 'Ouvrir le disjoncteur',
  };
  const labels = { drain: 'Mettre en pause', hold: 'Arrêter net', resume: 'Reprendre', reset: 'Refermer', trip: 'Ouvrir' };
  const asksReason = gesture === 'drain' || gesture === 'hold' || gesture === 'trip';
  return {
    title: titles[gesture], lines: confirmLines(gesture, state, name), confirmLabel: labels[gesture],
    reasonLabel: asksReason ? (gesture === 'trip' ? 'Motif (obligatoire)' : 'Motif (facultatif)') : '',
    reasonRequired: gesture === 'trip',
  };
}

function button(g, onClick) {
  const node = el('button', `btn${g.quiet ? ' ghost' : ''}`);
  node.type = 'button';
  node.dataset.gesture = g.id;
  node.append(icon(g.icon), g.label);
  node.addEventListener('click', onClick);
  return node;
}

// ── La fiche d'un projet ────────────────────────────────────────────────────────

function rolesLine(roles) {
  if (!roles) return null;
  if (roles.error) return `Rôles : politique illisible (${roles.error})`;
  const parts = [];
  for (const [name, role] of Object.entries(roles.roles || {})) {
    const bits = [role.enabled ? 'activé' : 'désactivé'];
    if (role.enabled && role.wip) bits.push(`${role.wip} en même temps`);
    if (role.enabled && role.sessions_per_day) {
      bits.push(role.sessions_today === null || role.sessions_today === undefined
        ? `plafond ${role.sessions_per_day}/jour (consommation non lue)`
        : `${role.sessions_today}/${role.sessions_per_day} sessions aujourd’hui`);
    }
    parts.push(`${name} ${bits.join(', ')}`);
  }
  return parts.length ? `Rôles : ${parts.join(' · ')}` : null;
}

function timerLine(timer) {
  if (!timer) return null;
  if (!timer.installed) return 'Minuteur du tick : absent — sans lui, rien ne reprend tout seul.';
  const every = timer.interval_minutes ? `toutes les ${timer.interval_minutes} min` : 'période non lue';
  return `Minuteur du tick : posé, ${every} · dernier tick : non consigné par le kit`;
}

function renderInto(node, ctx, slug, name, state, onChange) {
  node.replaceChildren();
  const h = el('h3');
  h.append(icon('bolt'), document.createTextNode('Pilotage'));
  node.append(h);
  if (!state) {
    node.append(el('p', 'pi-line', 'Pilotage illisible — la lecture a échoué, rien n’est deviné.'));
    return;
  }
  const line = el('p', 'pi-line', state.sentence);
  line.dataset.state = state.state;
  node.append(line);
  const flag = appliedPause(state.pause);
  if (flag) {
    const meta = el('p', 'pi-meta', pauseMeta(flag));
    meta.dataset.foreign = String(!!flag.foreign);
    node.append(meta);
  }
  if (state.pause && state.pause.machine && !(state.pause && state.pause.project)) {
    node.append(el('p', 'pi-meta', 'Pause de toute la machine : elle se lève depuis « Tous les projets ».'));
  }
  const note = el('p', 'pi-note');
  note.setAttribute('role', 'status');
  note.hidden = true;
  const bar = el('div', 'pi-actions');
  const disabled = ctx.host.readOnly ? 'Cockpit en lecture seule : le pilotage se règle depuis le projet.' : '';
  for (const g of availableGestures(state, 'project')) {
    const b = button(g, async () => {
      b.disabled = true;
      try {
        const answer = await runGesture(dialogOptions(g.id, state, name), callFor(g.id, slug, state));
        if (answer) { if (onChange) onChange(answer); }
      } catch (failure) {
        note.hidden = false;
        note.textContent = `Pilotage : ${failure && failure.message ? failure.message : 'geste refusé'}`;
      } finally {
        b.disabled = !!disabled;
      }
    });
    if (disabled) { b.disabled = true; b.title = disabled; }
    bar.append(b);
  }
  node.append(bar, note);
  const facts = el('ul', 'pi-facts');
  for (const text of [timerLine(state.timer), rolesLine(state.roles)]) if (text) facts.append(el('li', null, text));
  if (state.breaker && !state.breaker.open && state.breaker.failures) {
    facts.append(el('li', null, `Disjoncteur fermé : ${state.breaker.failures}/${state.breaker.threshold} échec(s) consécutif(s).`));
  }
  node.append(facts);
}

/**
 * Le bloc « Pilotage » de la fiche d'un projet : `{ node, refresh }`. L'hôte insère `node` et branche `refresh` sur le
 * pouls de la coque (`ctx.live`) ; le bloc ne sonde jamais de lui-même.
 */
export function renderPilotagePanel(ctx, { slug, name, initial = null }) {
  const node = el('section', 'pl-section pi-panel');
  node.dataset.panel = 'pilotage';
  let generation = 0;
  let current = initial;
  const paint = (state) => { current = state; renderInto(node, ctx, slug, name, state, apply); };
  const refresh = async () => {
    const mine = ++generation;
    const state = await api.pilotage(slug).catch(() => null);
    if (ctx.signal.aborted || mine !== generation) return;
    paint(state);
  };
  function apply(answer) {
    generation += 1;
    paint(answer.pilotage);
    const said = answer.notice || '';
    const status = node.querySelector('.pi-note');
    if (status && (said || answer.journaled === false)) {
      status.hidden = false;
      status.textContent = answer.journaled === false ? `${said} Le journal d’autorité n’a pas pu l’inscrire.`.trim() : said;
    }
  }
  if (current) paint(current); else { node.append(el('p', 'pi-line', 'Chargement du pilotage…')); refresh(); }
  return { node, refresh };
}

// ── La vue « Tous les projets » : une ligne par projet, et la machine ───────────

function projectRow(ctx, row, anchor, onChange) {
  const li = el('li', 'pi-row');
  li.dataset.project = row.slug;
  li.dataset.state = row.state;
  const head = el('div', 'pi-row-head');
  head.append(el('b', 'pi-row-name', row.name), el('span', 'pi-row-state', row.sentence));
  li.append(head);
  const flag = row.pause && row.pause.project;
  if (flag) li.append(el('p', 'pi-meta', pauseMeta(flag)));
  const bar = el('div', 'pi-actions');
  for (const g of availableGestures(row, 'row')) {
    const b = button(g, async () => {
      b.disabled = true;
      try {
        const answer = await runGesture(dialogOptions(g.id, row, row.name), callFor(g.id, row.slug, row));
        if (answer) onChange(answer);
      } catch (failure) {
        li.append(el('p', 'pi-note', `Pilotage : ${failure && failure.message ? failure.message : 'geste refusé'}`));
      } finally {
        b.disabled = !!ctx.host.readOnly;
      }
    });
    bar.append(b);
  }
  li.append(bar);
  return li;
}

function machineRow(ctx, view, anchor, onChange) {
  const box = el('div', 'pi-machine');
  const flag = view.machine;
  box.dataset.state = flag ? 'pause' : 'actif';
  const text = flag
    ? `Toute la machine est en pause (${flag.mode === 'hold' ? 'arrêt net' : 'les sessions en cours finissent'}), ${pauseMeta(flag)}.`
    : 'La machine n’est pas en pause.';
  box.append(el('p', 'pi-line', text));
  if (!anchor) return box;
  const g = flag
    ? { id: 'machine-resume', label: 'Reprendre toute la machine', icon: 'play' }
    : { id: 'machine-pause', label: 'Mettre toute la machine en pause', icon: 'hourglass' };
  const b = button(g, async () => {
    b.disabled = true;
    try {
      const answer = flag
        ? await runGesture(
          {
            title: 'Reprendre toute la machine',
            lines: [`Cette pause est ${pauseMeta(flag)}. La lever rend l’admission à tous les projets.`], confirmLabel: 'Reprendre',
          },
          (a) => api.pilotageGesture('machine/resume', anchor, {}, a.key),
        )
        : await runGesture(
          {
            title: 'Mettre toute la machine en pause', lines: [machineStops(view.projects)], confirmLabel: 'Mettre en pause',
            reasonLabel: 'Motif (facultatif)',
            modes: [{ value: 'drain', label: 'Laisser finir les sessions en cours' }, { value: 'hold', label: 'Arrêter net' }],
          },
          (a) => api.pilotageGesture('machine/pause', anchor, bodyOf(a, { mode: a.mode || 'drain', confirm: true }), a.key),
        );
      if (answer) onChange(answer);
    } catch (failure) {
      box.append(el('p', 'pi-note', `Pilotage : ${failure && failure.message ? failure.message : 'geste refusé'}`));
    } finally {
      b.disabled = !!ctx.host.readOnly;
    }
  });
  if (ctx.host.readOnly) b.disabled = true;
  box.append(b);
  return box;
}

/**
 * Le bloc « Pilotage » de la vue globale : `{ node, refresh }`. Une ligne par projet (état, auteur et motif de la
 * pause, « Mettre en pause » / « Reprendre ») et le bouton de la machine. Les gestes de machine nomment un projet
 * d'ancrage (le premier de la liste) : l'hôte exige un projet cible explicite pour toute écriture.
 */
export function renderFleetPilotage(ctx) {
  const node = el('section', 'pl-section pi-panel pi-fleet-block');
  node.dataset.panel = 'pilotage-flotte';
  let generation = 0;
  const paint = (view) => {
    node.replaceChildren();
    const h = el('h3');
    h.append(icon('bolt'), document.createTextNode('Pilotage de l’orchestrateur'));
    node.append(h);
    if (!view) { node.append(el('p', 'pi-line', 'Pilotage illisible — la lecture a échoué, rien n’est deviné.')); return; }
    const anchor = view.projects.length ? view.projects[0].slug : '';
    node.append(machineRow(ctx, view, anchor, () => refresh()));
    const list = el('ul', 'pi-rows');
    for (const row of view.projects) list.append(projectRow(ctx, row, anchor, () => refresh()));
    node.append(list);
  };
  const refresh = async () => {
    const mine = ++generation;
    const view = await api.pilotagePortfolio().catch(() => null);
    if (ctx.signal.aborted || mine !== generation) return;
    paint(view);
  };
  node.append(el('p', 'pi-line', 'Chargement du pilotage…'));
  refresh();
  return { node, refresh };
}
