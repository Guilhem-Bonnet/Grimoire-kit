// Configuration › Fonctions en test : les drapeaux de projet `cockpit.test_features.*`.
//
// Deux fonctions : l'Atelier de spec et la mascotte vocale du chat. L'interrupteur écrit le drapeau dans
// `project-context.yaml` (`POST features`). L'Atelier est un onglet : la page se recharge pour qu'il apparaisse ou
// disparaisse. La mascotte se lit à l'ouverture du chat : pas de rechargement.
// Ce module ne charge jamais `spaces/atelier*.js` ni `mascotte*.js` : il ne parle qu'à `GET/POST features`.

import { badge, spaceHead } from '../shell.js';



function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const FEATURES = [
  {
    name: 'atelier', title: 'Atelier de spec', action: 'Activer l’Atelier', reload: true,
    note: 'L’Atelier sert à cadrer un projet avant de le construire : brief, brainstorm, compréhension, '
      + 'exigences et cahier des charges, en chantiers que tu relis et corriges bloc par bloc. '
      + 'Il est en test : aucune tâche n’est lancée, et tu peux l’éteindre à tout moment.',
  },
  {
    name: 'mascotte', title: 'Mascotte vocale', action: 'Activer la mascotte', reload: false,
    note: 'Une petite mascotte, le slime d’encre, apparaît dans le chat quand le mode vocal est actif : elle écoute '
      + 'pendant la dictée, réfléchit pendant un tour de l’orchestrateur, parle pendant la lecture d’un message '
      + 'vocal. Elle ne lit que des niveaux sonores : rien n’est enregistré ni envoyé, et le micro reste celui de la dictée.',
  },
];

function featureCard(feature, state, ctx) {
  const toggle = el('button', 'ft-switch', null, {
    type: 'button', role: 'switch', 'aria-checked': String(state[feature.name] === true), 'data-feature': feature.name,
  });
  toggle.append(el('span', 'ft-track', null, { 'aria-hidden': 'true' }), el('span', null, feature.action));
  const error = el('p', 'ft-error', '', { role: 'alert' });
  error.hidden = true;

  toggle.addEventListener('click', async () => {
    const next = toggle.getAttribute('aria-checked') !== 'true';
    toggle.disabled = true;
    error.hidden = true;
    try {
      await ctx.api.setFeature(feature.name, next);
      // L’onglet apparaît ou disparaît avec le drapeau : la coque relit `features` au chargement.
      if (feature.reload) { location.reload(); return; }
      toggle.setAttribute('aria-checked', String(next));
      toggle.disabled = false;
    } catch (cause) {
      toggle.disabled = false;
      error.textContent = `Réglage impossible : ${cause.message || cause}`;
      error.hidden = false;
    }
  });

  const card = el('section', 'ft-card', null, { 'aria-label': feature.title });
  card.append(el('div', 'ft-line'), el('p', 'ft-note', feature.note), toggle, error);
  card.firstChild.append(el('h3', null, feature.title), badge('Test', 'warn', 'Fonction en test'));
  return card;
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
  wrap.append(
    spaceHead('Fonctions en test', 'Ce qui n’est pas encore stable reste éteint tant que tu ne l’allumes pas.'),
    ...FEATURES.map((feature) => featureCard(feature, state, ctx)),
  );
  root.append(wrap);
}
