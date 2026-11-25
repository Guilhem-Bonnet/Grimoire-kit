// Configuration › Fonctions en test : les drapeaux de projet `cockpit.test_features.*`.
//
// Une seule fonction pour l'instant : l'Atelier de spec. L'interrupteur écrit le drapeau dans
// `project-context.yaml` (`POST features`) ; la page se recharge pour que l'onglet apparaisse ou disparaisse.
// Ce module ne charge jamais `spaces/atelier*.js` : il ne parle qu'à `GET/POST features`.

import { badge, spaceHead } from '../shell.js';



function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

export async function mount(root, ctx) {
  root.replaceChildren();
  let state;
  try {
    state = await ctx.api.testFeatures();
  } catch (error) {
    if (ctx.signal.aborted) return;
    root.append(ctx.empty('Fonctions en test indisponibles', String(error.message || error)));
    return;
  }
  if (ctx.signal.aborted) return;

  const wrap = el('div', 'ft-wrap');
  const toggle = el('button', 'ft-switch', null, {
    type: 'button', role: 'switch', 'aria-checked': String(state.atelier === true), 'data-feature': 'atelier',
  });
  toggle.append(el('span', 'ft-track', null, { 'aria-hidden': 'true' }), el('span', null, 'Activer l’Atelier'));
  const error = el('p', 'ft-error', '', { role: 'alert' });
  error.hidden = true;

  toggle.addEventListener('click', async () => {
    const next = toggle.getAttribute('aria-checked') !== 'true';
    toggle.disabled = true;
    error.hidden = true;
    try {
      await ctx.api.setFeature('atelier', next);
      // L’onglet apparaît ou disparaît avec le drapeau : la coque relit `features` au chargement.
      location.reload();
    } catch (cause) {
      toggle.disabled = false;
      error.textContent = `Réglage impossible : ${cause.message || cause}`;
      error.hidden = false;
    }
  });

  const card = el('section', 'ft-card', null, { 'aria-label': 'Atelier de spec' });
  card.append(
    el('div', 'ft-line'),
    el('p', 'ft-note', 'L’Atelier sert à cadrer un projet avant de le construire : brief, brainstorm, compréhension, '
      + 'exigences et cahier des charges, en chantiers que tu relis et corriges bloc par bloc. '
      + 'Il est en test : aucune tâche n’est lancée, et tu peux l’éteindre à tout moment.'),
    toggle,
    error,
  );
  card.firstChild.append(el('h3', null, 'Atelier de spec'), badge('Test', 'warn', 'Fonction en test'));
  wrap.append(
    spaceHead('Fonctions en test', 'Ce qui n’est pas encore stable reste éteint tant que tu ne l’allumes pas.'),
    card,
  );
  root.append(wrap);
}
