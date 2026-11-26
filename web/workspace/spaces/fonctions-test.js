// Configuration › Fonctions en test.
//
// Deux fonctions : l'Atelier de spec (drapeau du PROJET, `cockpit.test_features.atelier` de `project-context.yaml`) et la
// mascotte (préférence de COCKPIT de la machine : tous les projets et la vue globale), toutes deux par `POST features`.
// L'Atelier est un onglet : la page se recharge pour qu'il apparaisse ou disparaisse. La mascotte se pose ou se retire
// tout de suite (événement `grimoire:features`, lu par la coque) ; la portée de chaque carte est dite à l'écran.
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
    scope: 'Portée : ce projet seulement (réglage du projet).',
    note: 'L’Atelier sert à cadrer un projet avant de le construire : brief, brainstorm, compréhension, '
      + 'exigences et cahier des charges, en chantiers que tu relis et corriges bloc par bloc. '
      + 'Il est en test : aucune tâche n’est lancée, et tu peux l’éteindre à tout moment.',
  },
  {
    name: 'mascotte', title: 'Mascotte', action: 'Activer la mascotte', reload: false,
    scope: 'Portée : toute cette machine, tous les projets et la vue « Tous les projets ». L’éteindre l’éteint partout ; '
      + 'tu la règles d’ici, quel que soit le projet ouvert (un ancien réglage posé dans un seul projet est repris).',
    where: 'Où : un petit bouton en bas à droite du cockpit, sur toutes les pages, qui ouvre le chat ; la mascotte '
      + 'elle-même se dessine dans le fil de l’orchestrateur et dans l’assistant de l’Atelier.',
    note: 'Une petite mascotte, le slime d’encre, est la présence de l’assistant dans le cockpit, avec ou sans voix : '
      + 'elle réfléchit pendant un tour de l’orchestrateur, écrit quand il produit du texte ou modifie des fichiers, '
      + 'attend ta réponse quand une question ou une permission est en suspens, et un libellé dit ce qu’elle fait. '
      + 'Un clic l’ouvre sur le chat de l’orchestrateur, ou sur l’Atelier quand c’est son assistant qui attend. '
      + 'Avec le vocal, elle écoute pendant la dictée et parle pendant la lecture d’un message vocal ; elle ne lit que '
      + 'des niveaux sonores : rien n’est enregistré ni envoyé, et le micro reste celui de la dictée.',
  },
];

/** « voix : disponible » ou « voix : non installée » (avec la raison que le serveur donne). */
async function voiceLine(ctx) {
  try {
    const config = await ctx.api.voiceConfig();
    if (config && config.available === true) return 'Voix : disponible.';
    return `Voix : non installée${config && config.reason ? ` (${config.reason})` : ''}.`;
  } catch {
    return 'Voix : état inconnu.';
  }
}

function featureCard(feature, state, ctx, extra) {
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
      // La coque pose ou retire la mascotte permanente tout de suite, sans recharger.
      if (feature.name === 'mascotte') document.dispatchEvent(new CustomEvent('grimoire:features', { detail: { mascotte: next } }));
    } catch (cause) {
      toggle.disabled = false;
      error.textContent = `Réglage impossible : ${cause.message || cause}`;
      error.hidden = false;
    }
  });

  const card = el('section', 'ft-card', null, { 'aria-label': feature.title });
  const facts = [feature.scope, feature.where, extra].filter(Boolean);
  card.append(el('div', 'ft-line'), el('p', 'ft-note', feature.note),
    ...facts.map((text) => el('p', 'ft-note ft-fact', text)), toggle, error);
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

  const voice = await voiceLine(ctx);
  if (ctx.signal.aborted) return;
  const wrap = el('div', 'ft-wrap');
  wrap.append(
    spaceHead('Fonctions en test', 'Ce qui n’est pas encore stable reste éteint tant que tu ne l’allumes pas.'),
    ...FEATURES.map((feature) => featureCard(feature, state, ctx, feature.name === 'mascotte' ? voice : '')),
  );
  root.append(wrap);
}
