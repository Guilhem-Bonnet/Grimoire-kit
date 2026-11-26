// Atelier de spec, lot 5 : l'assistant qui guide le cadrage (épique #1197, #1342).
//
// Une barre, toujours au même endroit sous la feuille. Le moteur est l'agent de l'hôte, piloté par le chat du cockpit :
// la page n'envoie jamais le texte d'une consigne, seulement un identifiant de geste (liste fermée côté serveur,
// `src/grimoire/tools/workspace_atelier_assistant.py`) et, pour « Répondre », la réponse de la personne. L'assistant n'écrit
// pas dans le document : l'agent consigne la réponse que l'humain vient de donner, propose par suggestion, commente.
// L'état vient de `GET atelier/chantiers/<slug>/entretien` (état de l'entretien et faits du chat), relu régulièrement.

import { badge, icon } from '../shell.js';

const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };
const PHASE_ORDER = ['brief', 'brainstorm', 'comprehension', 'exigences', 'cahier-des-charges'];
const POSTURES = [['explorer', 'Explorer'], ['sceptique', 'Sceptique'], ['simplificateur', 'Simplificateur']];
const FAST_MS = 1000;
const SLOW_MS = 5000;

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/**
 * `hooks` : `focus()` la phase affichée, `goPhase(id)` la navigation existante, `reload(message)` le rechargement
 * du document, `say(message)` la ligne d'état de la feuille, `editing()` vrai si un bloc est en cours d'édition.
 */
export function createAssistant(ctx, slug, hooks) {
  const bar = el('section', 'at-asst', null, { 'aria-label': 'Assistant du cadrage', 'data-role': 'assistant' });
  let view = null;
  let signature = '';
  let wasMine = false;
  let covered = -1;
  let timer = null;
  let draft = '';

  const call = async (body, done) => {
    try {
      await ctx.api.atelierAssistant(slug, { phase: hooks.focus(), ...body });
      if (done) hooks.say(done);
    } catch (cause) {
      if (!ctx.signal.aborted) hooks.say(`L’assistant n’a pas pu partir : ${cause.message || cause}`);
    }
    await tick();
  };

  const button = (label, act, onClick, { primary = false, iconName = '' } = {}) => {
    const node = el('button', 'at-btn' + (primary ? ' pri' : ''), null, { type: 'button', 'data-act': act });
    if (iconName) node.append(icon(iconName));
    node.append(label);
    node.addEventListener('click', onClick);
    return node;
  };

  const head = (text) => {
    const mark = el('span', 'at-ai', null, { 'aria-hidden': 'true' });
    mark.append(icon('i-act-add'));
    const who = el('div', 'at-asst-head');
    who.append(mark, el('b', null, 'Grimoire'), el('span', 'at-muted', text || ''));
    return who;
  };

  const unavailable = (v) => {
    const node = el('div', 'at-asst-body');
    node.append(
      el('p', null, `L’assistant n’est pas joignable : ${v.agent.reason || 'aucun agent d’hôte'}.`, { 'data-role': 'assistant-off' }),
      el('p', 'at-muted'),
    );
    node.lastChild.append('Le même cadrage se mène au terminal : ', el('code', null, v.equivalent), '.');
    return node;
  };

  const render = () => {
    const v = view;
    bar.replaceChildren();
    if (!v) return;
    const busy = Boolean(v.turn.mine);
    const where = v.current ? `${v.current.title}, ${PHASE_NAMES[v.current.phase] || v.current.phase}` : '';
    const progress = v.started && v.total ? `${v.covered} angle${v.covered > 1 ? 's' : ''} sur ${v.total} couvert${v.covered > 1 ? 's' : ''}` : '';
    bar.append(head(progress));
    if (!v.agent.available) { bar.append(unavailable(v)); return; }
    const body = el('div', 'at-asst-body');
    bar.append(body);
    if (v.error) { body.append(el('p', 'at-error', v.error, { role: 'alert' })); return; }
    if (v.turn.permission) {
      const ask = el('p', null, 'L’agent attend une permission dans l’onglet Orchestrateur.', { 'data-role': 'assistant-permission' });
      body.append(ask, button('Ouvrir l’orchestrateur', 'open-chat', () => ctx.dock.setTab('orchestrateur')));
    }
    if (!v.started) {
      body.append(el('p', null, 'Je peux t’aider à cadrer ce chantier : je pose les questions une à une, tu réponds, et le document se remplit.', { 'data-role': 'assistant-invite' }));
      const row = el('div', 'at-asst-row');
      row.append(button('Démarrer le cadrage', 'start', () => call({ geste: 'demarrer' }, 'Cadrage lancé : la première question arrive.'), { primary: true, iconName: 'i-act-send' }));
      if (busy) { row.replaceChildren(el('span', 'at-muted', 'Grimoire prépare la première question…', { role: 'status' }), pause()); }
      body.append(row);
      return;
    }
    const focus = hooks.focus();
    const here = v.phases[focus];
    if (v.complete) {
      body.append(el('p', null, 'Le cadrage est complet : tous les angles obligatoires sont couverts. Relis le document, puis valide et découpe quand tu es prêt.', { 'data-role': 'assistant-complete' }));
      return;
    }
    if (here && here.complete) {
      const next = PHASE_ORDER[PHASE_ORDER.indexOf(focus) + 1];
      const line = el('p', null, `La phase ${PHASE_NAMES[focus] || focus} est complète.`, { 'data-role': 'assistant-phase-done' });
      body.append(line);
      if (next) line.after(button(`Passer à ${PHASE_NAMES[next]}`, 'next-phase', () => hooks.goPhase(next)));
    }
    if (busy) {
      const row = el('div', 'at-asst-row');
      row.append(el('span', 'at-muted', 'Grimoire réfléchit…', { role: 'status', 'data-role': 'assistant-busy' }), pause());
      body.append(row);
      return;
    }
    if (v.question) {
      const q = el('div', 'at-asst-q', null, { 'data-role': 'assistant-question' });
      const tags = el('div', 'at-asst-tags');
      if (where) tags.append(badge(where, 'acc'));
      q.append(tags, el('p', null, v.question.text));
      body.append(q, answerForm(v));
    } else {
      body.append(el('p', null, `L’entretien est ouvert${where ? ` : prochain angle, ${where}` : ''}.`, { 'data-role': 'assistant-open' }));
      body.append(button('Reprendre', 'resume', () => call({ geste: 'reprendre' }, 'Je reprends l’entretien : la prochaine question arrive.'), { primary: true, iconName: 'i-act-next' }));
    }
  };

  const pause = () => button('Pause', 'pause', async () => {
    try { await ctx.api.chatCancel(); hooks.say('Tour arrêté : tu peux reprendre quand tu veux.'); } catch (cause) { hooks.say(`Arrêt non transmis : ${cause.message || cause}`); }
    await tick();
  }, { iconName: 'i-act-pause' });

  const answerForm = (v) => {
    const shown = v.current ? v.current.angle : ''; // l'angle que la barre affiche : celui que le geste vise
    const area = el('textarea', 'at-asst-area', null, { rows: '2', 'aria-label': 'Ta réponse', placeholder: 'Ta réponse — Entrée envoie, Maj+Entrée saute une ligne', maxlength: '4000' });
    area.value = draft;
    area.addEventListener('input', () => { draft = area.value; });
    const send = async () => {
      const text = area.value.trim();
      if (!text) { area.focus(); return; }
      draft = '';
      area.value = '';
      await call({ geste: 'repondre', text }, 'Réponse envoyée : l’agent la consigne.');
    };
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    });
    const row = el('div', 'at-asst-row');
    row.append(
      button('Répondre', 'answer', send, { primary: true, iconName: 'i-act-send' }),
      button('Je ne sais pas', 'unknown', () => call({ geste: 'ne-sais-pas', angle: shown },'Noté comme hypothèse : la question suivante arrive.')),
      button('Sans objet', 'skip', () => call({ geste: 'sans-objet', angle: shown },'Angle écarté : la question suivante arrive.')),
    );
    const form = el('div', 'at-asst-form');
    form.append(area, row);
    return form;
  };

  const tick = async () => {
    clearTimeout(timer);
    if (ctx.signal.aborted) return;
    let next;
    try {
      next = await ctx.api.atelierEntretien(slug);
    } catch {
      timer = setTimeout(tick, SLOW_MS);
      return;
    }
    if (ctx.signal.aborted) return;
    const mine = Boolean(next.turn.mine);
    const changed = (wasMine && !mine) || (covered >= 0 && next.covered !== covered);
    const was = signature;
    signature = JSON.stringify([next, hooks.focus()]);
    view = next;
    if (signature !== was) render();
    covered = next.covered;
    wasMine = mine;
    if (changed && !hooks.editing()) await hooks.reload();
    timer = setTimeout(tick, mine || next.turn.busy ? FAST_MS : SLOW_MS);
  };

  /** Le bouton « Challenger » d'un bloc : trois postures, une liste fermée ; la réponse arrive comme suggestion ou commentaire. */
  const challenge = (block) => {
    const wrap = el('span', 'at-ch');
    const open = el('button', 'at-ch-add', null, { type: 'button', 'data-act': 'challenge', 'aria-haspopup': 'true', 'aria-expanded': 'false' });
    open.append(icon('i-act-add'), el('span', null, 'Challenger ce bloc'));
    const menu = el('div', 'at-ch-menu', null, { role: 'group', 'aria-label': 'Posture du challenge' });
    menu.hidden = true;
    const close = () => { menu.hidden = true; open.setAttribute('aria-expanded', 'false'); };
    for (const [id, label] of POSTURES) {
      const item = el('button', 'at-btn', label, { type: 'button', 'data-posture': id });
      item.addEventListener('click', async (event) => {
        event.stopPropagation();
        close();
        if (view && !view.agent.available) {
          hooks.say(`Challenge impossible : ${view.agent.reason || 'aucun agent d’hôte'}. Au terminal : ${view.equivalent}`);
          return;
        }
        await call({ geste: `challenger-${id}`, block_id: block.id }, `Challenge ${label.toLowerCase()} demandé : la réponse arrive sous le bloc, en suggestion ou en commentaire.`);
      });
      menu.append(item);
    }
    open.addEventListener('click', (event) => {
      event.stopPropagation(); // n'ouvre pas l'édition du bloc
      menu.hidden = !menu.hidden;
      open.setAttribute('aria-expanded', String(!menu.hidden));
    });
    wrap.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Escape') { close(); open.focus(); }
    });
    wrap.addEventListener('click', (event) => event.stopPropagation());
    wrap.append(open, menu);
    return wrap;
  };

  ctx.signal.addEventListener('abort', () => clearTimeout(timer));
  tick();
  return { element: bar, challenge, refresh: () => { signature = ''; return tick(); } };
}
