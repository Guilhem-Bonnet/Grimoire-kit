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
const MODEL_GESTES = [['modele-reformuler', 'Reformuler'], ['modele-suggerer', 'Suggérer'], ['modele-challenger', 'Challenger'], ['modele-relance', 'Relancer']];
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
  // Le contenu change à chaque état ; la mascotte (si la fonction en test est allumée) reste posée à côté, jamais refaite.
  const main = el('div', 'at-asst-main');
  bar.append(main);
  let mascotte = null;
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

  /** Les faits que la mascotte reflète : ce que l'assistant dit (entretien) et les suggestions du document. */
  const syncMascotte = () => {
    if (!mascotte) return;
    const v = view;
    const turn = (v && v.turn) || {};
    mascotte.update({
      error: v ? v.error : '',
      unavailable: Boolean(v && v.agent && !v.agent.available),
      question: Boolean(v && v.question && !turn.mine),
      permission: Boolean(turn.permission),
      suggestions: hooks.suggestions ? hooks.suggestions() : 0,
      busy: Boolean(turn.mine),
      text: v && v.question ? v.question.text : '',
    });
  };

  /** Le clic sur la mascotte : le panneau passe au premier plan sur ce qui attend. */
  const activate = (target) => {
    if (target === 'suggestion') { if (hooks.showSuggestion && hooks.showSuggestion()) return; }
    bar.scrollIntoView({ block: 'nearest' });
    const aim = (target === 'question' && bar.querySelector('.at-asst-area'))
      || (target === 'permission' && bar.querySelector('[data-act="open-chat"]'))
      || bar.querySelector('.at-asst-area, button:not([disabled])');
    if (aim) aim.focus();
  };

  /**
   * La dictée du mode conversation arrive dans le champ de réponse. Rien n'est envoyé : l'envoi reste un clic ou Entrée,
   * car un geste de l'assistant ne se déclenche pas par une dictée (garde du serveur).
   */
  const fill = (text) => {
    const area = bar.querySelector('.at-asst-area');
    if (!area) return false;
    area.value = area.value.trim() ? `${area.value.trim()} ${text}` : text;
    area.dispatchEvent(new Event('input', { bubbles: true })); // le brouillon suit
    area.focus({ preventScroll: true });
    return true;
  };

  // La mascotte : seulement si la fonction en test « Mascotte » est allumée (sinon ce module n'est jamais demandé).
  ctx.api.testFeatures().then(async (features) => {
    if (!features || features.mascotte !== true || ctx.signal.aborted) return;
    const { createAtelierMascotte } = await import('./atelier-mascotte.js');
    if (ctx.signal.aborted) return;
    mascotte = createAtelierMascotte(ctx, { activate, fill, say: hooks.say });
    bar.prepend(mascotte.element);
    syncMascotte();
  }).catch(() => { /* sans mascotte, la barre reste telle quelle */ });

  /** Le moteur de chaque geste, dit en clair : l'agent de l'hôte (sans modèle du kit), ou le modèle optionnel du kit. */
  const engineNote = (v) => {
    const m = v.modele;
    let text = 'Moteur : agent de l’hôte, sans modèle du kit.';
    if (m && m.enabled && m.available) {
      text = `Moteur : agent de l’hôte (sans modèle) ; modèle du kit actif sur les blocs, haiku ou sonnet au choix. Jetons du jour : ${m.budget.day} sur ${m.budget.day_cap}.`;
    } else if (m && m.enabled) {
      text = `Moteur : agent de l’hôte, sans modèle. Modèle du kit indisponible : ${m.reason}.`;
    }
    return el('p', 'at-muted at-asst-engine', text, { 'data-role': 'engine-note' });
  };

  const unavailable = (v) => {
    const node = el('div', 'at-asst-body');
    node.append(
      el('p', null, `L’assistant n’est pas joignable : ${v.agent.reason || 'aucun agent d’hôte'}.`, { 'data-role': 'assistant-off' }),
      el('p', 'at-muted'),
    );
    node.lastChild.append('Le même cadrage se mène au terminal : ', el('code', null, v.equivalent), '.');
    // Le geste existe mais refuse d'avance, avec sa raison, plutôt que d'échouer après coup.
    if (!v.started) {
      const start = button('Démarrer le cadrage', 'start', () => {}, { primary: true, iconName: 'i-act-send' });
      start.disabled = true;
      start.title = v.agent.reason || 'Aucun agent d’hôte utilisable';
      node.append(start);
    }
    return node;
  };

  const render = () => {
    const v = view;
    main.replaceChildren();
    syncMascotte();
    if (!v) return;
    const busy = Boolean(v.turn.mine);
    const where = v.current ? `${v.current.title}, ${PHASE_NAMES[v.current.phase] || v.current.phase}` : '';
    const progress = v.started && v.total ? `${v.covered} angle${v.covered > 1 ? 's' : ''} sur ${v.total} couvert${v.covered > 1 ? 's' : ''}` : '';
    main.append(head(progress), engineNote(v));
    if (!v.agent.available) { main.append(unavailable(v)); return; }
    const body = el('div', 'at-asst-body');
    main.append(body);
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
      const asked = v.question;
      if (asked.angle) tags.append(badge(`${asked.title}, ${PHASE_NAMES[asked.phase] || asked.phase}`, 'acc'));
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
    // L'angle que l'agent a NOMMÉ pour sa question (validé par le serveur) : celui que les deux gestes visent.
    const shown = v.question && v.question.angle ? v.question.angle : '';
    const label = shown ? ` : ${v.question.title}` : '';
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
    const unknown = button(`Je ne sais pas${label}`, 'unknown', () => call({ geste: 'ne-sais-pas', angle: shown }, 'Noté comme hypothèse : la question suivante arrive.'));
    const skip = button(`Sans objet${label}`, 'skip', () => call({ geste: 'sans-objet', angle: shown }, 'Angle écarté : la question suivante arrive.'));
    row.append(button('Répondre', 'answer', send, { primary: true, iconName: 'i-act-send' }), unknown, skip);
    const form = el('div', 'at-asst-form');
    form.append(area, row);
    if (!shown) { // sans angle nommé, ces deux gestes ne sauraient pas ce qu'ils touchent : fermés, et on le dit
      unknown.disabled = true;
      skip.disabled = true;
      form.append(el('p', 'at-muted', 'L’assistant n’a pas dit à quel angle se rattache sa question : « Je ne sais pas » et « Sans objet » sont indisponibles. Tu peux toujours répondre.', { 'data-role': 'assistant-noangle' }));
    }
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
      item.append(el('span', 'at-ch-tag', ' · sans modèle')); // l'agent de l'hôte, pas le modèle du kit
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
    // Le modèle optionnel du kit (éteint par défaut) : un sélecteur et quatre gestes, seulement si l'option est active.
    const modelBox = el('div', 'at-ch-model', null, { 'data-role': 'model-box' });
    menu.append(modelBox);
    const fillModel = () => {
      modelBox.replaceChildren();
      const m = view && view.modele;
      if (!m || !m.enabled) return;
      if (!m.available) {
        modelBox.append(el('p', 'at-muted', `Modèle du kit indisponible : ${m.reason}.`, { 'data-role': 'model-off' }));
        return;
      }
      const pick = el('select', 'at-ch-engine', null, { 'aria-label': 'Modèle du kit pour ce geste', 'data-role': 'engine' });
      pick.append(
        el('option', null, 'Modèle par défaut du geste', { value: '' }),
        el('option', null, `Haiku (${m.models.haiku})`, { value: 'haiku' }),
        el('option', null, `Sonnet (${m.models.sonnet})`, { value: 'sonnet' }),
      );
      modelBox.append(pick);
      for (const [geste, label] of MODEL_GESTES) {
        const go = el('button', 'at-btn', null, { type: 'button', 'data-geste': geste });
        const tag = () => { go.textContent = `${label} · ${pick.value || m.defaults[geste]}`; };
        tag();
        pick.addEventListener('change', tag);
        go.addEventListener('click', async (event) => {
          event.stopPropagation();
          close();
          hooks.say('Appel du modèle du kit…');
          try {
            const done = await ctx.api.atelierAssistant(slug, { geste, block_id: block.id, modele: pick.value, phase: hooks.focus() });
            await hooks.reload(`Proposition de ${done.modele} déposée (${done.jetons} jetons) : à relire sous le bloc.`);
          } catch (cause) {
            if (!ctx.signal.aborted) hooks.say(`Modèle du kit : ${cause.message || cause}`);
          }
          await tick();
        });
        modelBox.append(go);
      }
      modelBox.append(el('p', 'at-muted', `Jetons du jour : ${m.budget.day} sur ${m.budget.day_cap} ; ce chantier : ${m.budget.chantier} sur ${m.budget.chantier_cap}.`));
    };
    open.addEventListener('click', (event) => {
      event.stopPropagation(); // n'ouvre pas l'édition du bloc
      fillModel();
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
  return { element: bar, challenge, sync: syncMascotte, refresh: () => { signature = ''; return tick(); } };
}
