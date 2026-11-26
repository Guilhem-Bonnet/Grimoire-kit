// Le lanceur de la mascotte (fonction en test « Mascotte », #1197) : un petit bouton discret, en bas à droite, présent sur
// toutes les pages d'un projet comme en vue « Tous les projets », hors du dock. Il porte le libellé d'état de
// l'orchestrateur et de l'assistant de l'Atelier (au repos, réfléchit, attend ta réponse avec compteur, erreur) et ouvre le
// chat de l'orchestrateur (ou l'Atelier quand c'est son assistant qui attend).
//
// Volontairement minimal : ni cadre travaillé ni canevas WebGL (la mascotte se dessine dans le chat et dans l'Atelier ;
// sa présence sur l'interface est en cours de refonte). Les états viennent des faits que le chat et l'assistant posent sur
// `mascotte_bus.js` : aucune requête de plus au premier rendu. Chargé seulement si la fonction est allumée.

import { subscribe } from './mascotte_bus.js';
import { anchorDestination, assistantState, mergeFacts } from './mascotte_logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/**
 * `hooks` : `openChat()` met le chat de l'orchestrateur au premier plan, `openAtelier(slug)` ouvre l'Atelier sur le
 * chantier, `watchChat()` branche la veille du fil (sans rien afficher). Rend `{ element, destroy() }`.
 */
export function mountMascotteAnchor(hooks) {
  const element = el('button', 'mascotte-launcher', null, { type: 'button', 'data-role': 'mascotte-anchor' });
  const pip = el('span', 'mascotte-launcher-pip', null, { 'aria-hidden': 'true' });
  const label = el('span', 'mascotte-launcher-label', 'Mascotte', { 'data-role': 'mascotte-anchor-caption' });
  const count = el('span', 'mascotte-launcher-count', null, { 'aria-hidden': 'true', 'data-role': 'mascotte-anchor-count' });
  count.hidden = true;
  const live = el('span', 'sr-only', '', { 'aria-live': 'polite' }); // annoncé seulement quand l'état change
  element.append(pip, label, count, live);

  let sources = { orchestrator: null, assistant: null };
  let destroyed = false;

  function paint() {
    const state = assistantState(mergeFacts(sources));
    // Sur la page de l'Atelier, la mascotte de l'assistant est déjà là : le lanceur s'efface.
    element.hidden = sources.assistant !== null;
    const text = `Mascotte : ${state.caption}`;
    if (label.textContent !== text) { label.textContent = text; live.textContent = text; }
    element.dataset.tone = state.tone;
    element.dataset.action = state.action;
    count.hidden = state.count < 1;
    count.textContent = String(state.count);
    element.setAttribute('aria-label', `${text}${state.count > 0 ? ` (${state.count} en attente)` : ''}. Ouvrir le chat de l’orchestrateur.`);
  }

  element.addEventListener('click', () => {
    if (anchorDestination(sources) === 'atelier') hooks.openAtelier(sources.assistant.slug);
    else hooks.openChat();
  });

  document.body.append(element);
  const unsubscribe = subscribe((next) => { sources = next; if (!destroyed) paint(); });
  paint();
  // La veille du fil ne part qu'après le premier rendu de la page : elle n'ajoute rien à son chargement.
  const later = window.requestIdleCallback ? (fn) => window.requestIdleCallback(fn, { timeout: 2500 }) : (fn) => setTimeout(fn, 1500);
  later(() => { if (!destroyed) hooks.watchChat(); });

  return {
    element,
    destroy() {
      destroyed = true;
      unsubscribe();
      element.remove();
    },
  };
}
