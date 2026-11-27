// Atelier de spec (en test) : la zone de notification, ancrée au bas de la fenêtre visible de la toile.
//
// Une seule file. Un retour réussi disparaît seul ; une erreur reste jusqu'à sa fermeture (`role="alert"`, les autres
// `role="status"`). Rien ne prend le focus : le message s'annonce, la croix et le lien d'action restent atteignables au clavier.
// L'ancrage est une enveloppe `position: sticky; bottom: 0` de hauteur nulle, posée en fin de page (voir atelier.css).

import { icon } from '../shell.js';
import { dropToast, pushToast, toneOf, ttlOf } from './atelier-layout-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const ICON = { ok: 'i-act-approve', warn: 'alert', error: 'alert' };

/**
 * `ctx.signal` arrête les minuteries. Rend `{ element, push(text, { kind, action, onClose }), clear() }` ;
 * `action` : `{ label, run }` (un lien dans la notification), `kind` : `'ok' | 'warn' | 'error'` (sinon déduit du texte).
 */
export function createNotifier(ctx) {
  const wrap = el('div', 'at-toasts', null, { 'data-role': 'atelier-toasts' });
  const list = el('div', 'at-toasts-list');
  wrap.append(list);
  let items = [];
  let seq = 0;
  const timers = new Map();

  const forget = (id) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
  };

  const draw = () => {
    const keep = new Set(items.map((t) => t.id));
    for (const node of [...list.children]) if (!keep.has(Number(node.getAttribute('data-toast')))) node.remove();
    for (const item of items) {
      if (list.querySelector(`[data-toast="${item.id}"]`)) continue;
      const node = el('div', `at-toast is-${item.kind}`, null, {
        role: item.kind === 'error' ? 'alert' : 'status', 'data-toast': String(item.id), 'data-kind': item.kind,
      });
      const mark = el('span', 'at-toast-ico', null, { 'aria-hidden': 'true' });
      mark.append(icon(ICON[item.kind]));
      const text = el('span', 'at-toast-text', item.text);
      node.append(mark, text);
      if (item.action) {
        const go = el('button', 'at-toast-act', item.action.label, { type: 'button', 'data-act': 'toast-action' });
        go.addEventListener('click', () => { item.action.run(); dismiss(item.id); });
        node.append(go);
      }
      const close = el('button', 'at-toast-close', null, { type: 'button', 'aria-label': 'Fermer la notification', 'data-act': 'toast-close' });
      close.append(icon('i-act-close'));
      close.addEventListener('click', () => dismiss(item.id));
      node.append(close);
      list.append(node);
    }
  };

  function dismiss(id) {
    forget(id);
    items = dropToast(items, id);
    draw();
  }

  const push = (message, options = {}) => {
    const text = String(message || '').trim();
    if (!text || ctx.signal.aborted) return;
    const kind = options.kind || toneOf(text);
    const id = (seq += 1);
    const replaced = items.find((t) => t.text === text);
    if (replaced) { forget(replaced.id); }
    items = pushToast(items, { id, text, kind, action: options.action || null });
    for (const node of [...list.children]) {
      if (!items.some((t) => t.id === Number(node.getAttribute('data-toast')))) forget(Number(node.getAttribute('data-toast')));
    }
    draw();
    const ttl = ttlOf(kind, Boolean(options.action));
    if (ttl > 0) timers.set(id, setTimeout(() => dismiss(id), ttl));
  };

  const clear = () => {
    for (const id of timers.keys()) forget(id);
    items = [];
    draw();
  };
  ctx.signal.addEventListener('abort', clear);
  return { element: wrap, push, clear };
}
