// Configuration › Fonctions en test : les drapeaux de projet `cockpit.test_features.*`.
//
// Une seule fonction pour l'instant : l'Atelier de spec. L'interrupteur écrit le drapeau dans
// `project-context.yaml` (`POST features`) ; la page se recharge pour que l'onglet apparaisse ou disparaisse.
// Ce module ne charge jamais `spaces/atelier*.js` : il ne parle qu'à `GET/POST features`.

import { badge, spaceHead } from '../shell.js';

const STYLE_ID = 'ft-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ft-wrap { padding: var(--sp-6); max-width: 760px; width: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: var(--sp-4); }
    .ft-card { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-3); }
    .ft-line { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
    .ft-line h3 { margin: 0; font-size: var(--t-m); font-weight: 600; color: var(--ink); }
    .ft-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
    .ft-switch { display: inline-flex; align-items: center; gap: var(--sp-2); min-height: var(--h-ctl); padding: 0 var(--sp-3) 0 var(--sp-1); border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--panel); color: var(--ink); cursor: pointer; }
    .ft-switch:hover { background: var(--hover); }
    .ft-switch:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
    .ft-track { width: 34px; height: 20px; border-radius: var(--r-pill); background: var(--line); position: relative; flex: none; }
    .ft-track::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--panel); box-shadow: var(--shadow-tip, none); }
    .ft-switch[aria-checked="true"] .ft-track { background: var(--acc); }
    .ft-switch[aria-checked="true"] .ft-track::after { left: 16px; }
    .ft-switch[disabled] { opacity: .6; cursor: progress; }
    .ft-error { margin: 0; color: var(--bad); font-size: var(--t-s); }
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

export async function mount(root, ctx) {
  injectStyles();
  root.replaceChildren();
  let state;
  try {
    state = await ctx.api.features();
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
