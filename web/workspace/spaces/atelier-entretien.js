// Atelier de spec (en test) : le cadrage guidé SANS agent, joué dans la page.
//
// Sans agent joignable, le kit sait pourtant poser l'entretien (`interview_plan`) et consigner une réponse
// (`cadrage interview answer`). Ici la page joue cet entretien : les questions une à une, le champ de réponse,
// « Je ne sais pas », « Sans objet », le retour à la question précédente pour corriger, la reprise après rechargement
// (le serveur déduit le curseur de `entretien.json`). Aucun modèle : ce sont les questions du kit, et la réponse est
// consignée dans le bon bloc du document, exactement comme la commande.
//
// Aucune route nouvelle : des gestes de `POST atelier/chantiers/<slug>/assistant` (`guide-demarrer`, `guide-repondre`,
// `guide-ne-sais-pas`, `guide-sans-objet`, `guide-retour`), avec les mêmes gardes. Le geste porte la question que l'écran
// montrait (`angle`, `index`) : si elle a changé entre-temps (autre onglet), le serveur refuse (409) et on recharge.
// Données : `src/grimoire/tools/workspace_atelier_guide.py`.

import { badge, icon } from '../shell.js';
import { avancementText } from './atelier-avancement.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** Le moteur du panneau en mots simples : « Questions guidées, sans IA » ou « Avec l'agent <nom> ». */
export function engineBadge(view) {
  const label = (view.moteur && view.moteur.label) || (view.agent && view.agent.available ? 'Avec l’agent de l’hôte' : 'Questions guidées, sans IA');
  return el('p', 'at-asst-engine', label, { 'data-role': 'engine-label', 'data-engine': (view.moteur && view.moteur.id) || '' });
}

/**
 * `hooks` : `focus()` la phase affichée, `reload(message)` recharge le document, `say(message)` la ligne d'état,
 * `refresh()` relit l'état de l'assistant. Rend `{ render(view) }` : le nœud du panneau guidé pour la vue donnée.
 */
export function createGuided(ctx, slug, hooks) {
  const drafts = new Map(); // brouillon par question, gardé d'un rendu à l'autre
  let busy = false;

  const act = async (body, done) => {
    busy = true;
    try {
      const result = await ctx.api.atelierAssistant(slug, { phase: hooks.focus(), ...body });
      if (ctx.signal.aborted) return;
      if (result && result.retracted) {
        // La réponse retirée revient dans le champ, pour être corrigée ; elle l'était pour la question précédente.
        const gone = result.retracted;
        drafts.set(`${gone.angle}:${body.index}`, gone.kind === 'answer' ? gone.text : '');
        await hooks.reload(gone.removed
          ? 'Réponse retirée du document : corrige-la, puis renvoie-la.'
          : 'Réponse retirée de l’entretien ; la ligne du document, retouchée à la main, est restée en place.', gone.removed ? 'ok' : 'warn');
      } else {
        await hooks.reload(done, 'ok');
      }
    } catch (cause) {
      if (ctx.signal.aborted) return;
      // Le type est explicite (succès, avertissement, erreur), jamais déduit du texte.
      hooks.say(cause.code === 409
        ? 'La question a changé depuis l’affichage (autre onglet ?) : l’entretien est rechargé.'
        : `Le cadrage guidé n’a pas pu avancer : ${cause.message || cause}`, cause.code === 409 ? 'warn' : 'error');
    } finally {
      busy = false;
    }
    await hooks.refresh();
  };

  const button = (label, role, onClick, { primary = false, iconName = '', disabled = false } = {}) => {
    const node = el('button', 'at-btn' + (primary ? ' pri' : ''), null, { type: 'button', 'data-act': role });
    if (iconName) node.append(icon(iconName));
    node.append(label);
    node.disabled = disabled;
    node.addEventListener('click', onClick);
    return node;
  };

  const question = (v, guide) => {
    const cur = guide.current;
    const key = `${cur.angle}:${cur.index}`;
    const box = el('div', 'at-asst-q', null, { 'data-role': 'guided-question', 'data-angle': cur.angle });
    const tags = el('div', 'at-asst-tags');
    tags.append(badge(`${cur.title}, ${cur.phase_title.split(' — ')[0]}`, 'acc'), el('span', 'at-muted', ` Question ${guide.position + 1} sur ${guide.total}`));
    box.append(tags, el('p', null, cur.question, { 'data-role': 'guided-text' }), el('p', 'at-muted', cur.intent));
    const area = el('textarea', 'at-asst-area', null, { rows: '2', 'aria-label': 'Ta réponse', placeholder: 'Ta réponse — Entrée envoie, Maj+Entrée saute une ligne', maxlength: '4000' });
    area.value = drafts.get(key) || '';
    area.addEventListener('input', () => { drafts.set(key, area.value); });
    const send = () => {
      const text = area.value.trim();
      if (!text) { area.focus(); return; }
      if (busy) return;
      drafts.delete(key);
      act({ geste: 'guide-repondre', angle: cur.angle, index: cur.index, text }, 'Réponse consignée dans le document.');
    };
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    });
    const row = el('div', 'at-asst-row');
    row.append(
      button('Répondre', 'answer', send, { primary: true, iconName: 'i-act-send' }),
      button('Je ne sais pas', 'unknown', () => !busy && act({ geste: 'guide-ne-sais-pas', angle: cur.angle, index: cur.index }, 'Noté comme hypothèse à valider.')),
      button('Sans objet', 'skip', () => !busy && act({ geste: 'guide-sans-objet', angle: cur.angle, index: cur.index }, 'Angle écarté : « sans objet ».')),
      previous(guide),
    );
    const form = el('div', 'at-asst-form');
    form.append(area, row);
    return [box, form];
  };

  const previous = (guide) => button('Question précédente', 'previous', () => {
    if (busy || !guide.previous) return;
    act({ geste: 'guide-retour', index: guide.previous.index }, '');
  }, { iconName: 'i-act-back', disabled: !guide.previous });

  const render = (v) => {
    const guide = v.guide || {};
    const body = el('div', 'at-asst-body', null, { 'data-role': 'guided' });
    if (v.agent && !v.agent.available) {
      const terminal = el('p', 'at-muted');
      terminal.append('Le même cadrage se mène au terminal : ', el('code', null, v.equivalent), '.');
      body.append(
        el('p', 'at-muted', `Aucun agent n’est joignable (${v.agent.reason || 'aucun agent d’hôte'}) : le cadrage se mène ici avec les questions du kit, sans IA.`, { 'data-role': 'assistant-off' }),
        terminal,
      );
    }
    if (v.error || guide.error) { body.append(el('p', 'at-error', v.error || guide.error, { role: 'alert' })); return body; }
    if (!guide.available) {
      body.append(el('p', 'at-muted', guide.note || 'Le cadrage guidé n’est pas disponible pour ce chantier.'));
      return body;
    }
    if (!guide.started) {
      body.append(el('p', null, 'Je te pose les questions une à une, tu réponds, et le document se remplit. Ce sont les questions du kit : aucune IA ne répond.', { 'data-role': 'assistant-invite' }));
      const row = el('div', 'at-asst-row');
      row.append(button('Démarrer le cadrage', 'start', () => !busy && act({ geste: 'guide-demarrer' }, 'Cadrage guidé démarré : voici la première question.'), { primary: true, iconName: 'i-act-send' }));
      body.append(row);
      return body;
    }
    if (guide.finished || !guide.current) {
      const av = v.avancement;
      body.append(el('p', null, av && av.etat === 'complet'
        ? 'Toutes les questions sont posées et chaque point du document est renseigné.'
        : `Toutes les questions du kit sont posées. ${av ? avancementText(av) : ''}.`, { 'data-role': 'guided-done' }));
      if (av && av.etat !== 'complet') body.append(el('p', 'at-muted', 'Complète les points restants directement dans le document : clique un bloc pour l’écrire.'));
      const row = el('div', 'at-asst-row');
      row.append(previous(guide));
      body.append(row);
      return body;
    }
    body.append(...question(v, guide));
    return body;
  };

  return { render };
}
