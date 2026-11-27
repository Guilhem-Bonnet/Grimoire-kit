// Atelier de spec : le mode lecture. Une page propre et centrée, sans rien de ce qui sert à écrire.
//
// Le mode se pose en classe (`is-reading`) sur la page ; la feuille de style masque les gestes d'écriture (Commenter, Challenger,
// assistant, suggestions, lignes d'état et « Valider et découper »). Ici : l'état (adresse `&lecture=1`, puis `localStorage`),
// le bouton de la barre, Alt+L et Échap, les cases à cocher rendues non cliquables, les blocs retirés du parcours au clavier,
// et la pastille discrète en marge d'un bloc qui porte un fil ouvert (un clic sort du mode lecture, sur le fil).
// Un bloc en cours d'édition, ou un commentaire commencé, empêche d'entrer en lecture : rien n'est perdu.

import { dotLabel, isReadingShortcut, LECTURE_KEY, readingButtonText, readingFrom } from './atelier-lecture-logic.js';
import { BUSY } from './atelier-reconcile.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const store = {
  read() { try { return window.localStorage.getItem(LECTURE_KEY); } catch { return null; } },
  write(on) { try { window.localStorage.setItem(LECTURE_KEY, on ? '1' : '0'); } catch { /* sans stockage, le mode vaut pour cette page */ } },
};

/**
 * `hooks` : `sheet`, `setItem(on)` (met `lecture` dans l'adresse), `say(message, kind)`. `params` : ceux de l'adresse.
 * Rend `{ button, on, attach(page), set(on), sync(), dot(count) }`.
 */
export function createReading(ctx, hooks, params) {
  let on = readingFrom(params, store.read());
  let page = null;
  const button = el('button', 'at-btn at-read-btn', null, { type: 'button', 'data-role': 'atelier-lecture', 'aria-keyshortcuts': 'Alt+L' });
  button.title = 'Mode lecture : une page propre, sans les gestes d’écriture (Alt+L ; Échap pour en sortir)';

  const paintButton = () => {
    button.textContent = readingButtonText(on);
    button.setAttribute('aria-pressed', String(on));
  };

  /** Cases à cocher non cliquables et blocs hors du parcours au clavier, tant que la lecture dure (et rétablis à la sortie). */
  const sync = () => {
    for (const box of hooks.sheet.querySelectorAll('.at-task-box')) {
      if (on && !box.disabled) { box.disabled = true; box.setAttribute('data-read-off', '1'); }
      else if (!on && box.hasAttribute('data-read-off')) { box.disabled = false; box.removeAttribute('data-read-off'); }
    }
    for (const block of hooks.sheet.querySelectorAll('.at-blk')) {
      if (on) { block.setAttribute('tabindex', '-1'); block.setAttribute('data-read-off', '1'); }
      else if (block.hasAttribute('data-read-off')) { block.setAttribute('tabindex', '0'); block.removeAttribute('data-read-off'); }
    }
  };

  const apply = () => {
    if (page) {
      page.classList.toggle('is-reading', on);
      page.setAttribute('data-reading', on ? '1' : '0');
    }
    paintButton();
    sync();
  };

  const set = (next, { remember = true } = {}) => {
    if (next === on) return true;
    if (next && hooks.sheet.querySelector(BUSY)) {
      hooks.say('Termine ou annule la saisie en cours avant de passer en lecture.', 'warn');
      return false;
    }
    on = next;
    if (remember) store.write(on);
    apply();
    hooks.setItem(on);
    return true;
  };

  button.addEventListener('click', () => set(!on));
  const onKey = (event) => {
    if (isReadingShortcut(event)) {
      event.preventDefault();
      if (set(!on) && document.activeElement === document.body) button.focus({ preventScroll: true });
      return;
    }
    if (event.key !== 'Escape' || !on || event.defaultPrevented) return;
    // Échap appartient d'abord à ce qui est ouvert au-dessus de la page : palette, aide, recherche, menus.
    if (document.querySelector('dialog[open]') || (event.target.closest && event.target.closest('.at-find, .at-docmenu, .at-hist'))) return;
    event.preventDefault();
    set(false);
    button.focus({ preventScroll: true });
  };
  document.addEventListener('keydown', onKey);
  ctx.signal.addEventListener('abort', () => document.removeEventListener('keydown', onKey));
  paintButton();

  return {
    button,
    on: () => on,
    set,
    sync,
    /** La page entière (le mode s'y pose) ; à appeler une fois qu'elle existe. */
    attach(node) {
      page = node;
      // Lecture pure : le guidage (le bloc « reste à faire » et les invites d'écriture) est masqué ; une ligne discrète le dit.
      const note = el('p', 'at-read-note', 'Mode lecture : le guidage est masqué. ', { 'data-role': 'lecture-guidage' });
      const exit = el('button', 'at-link', 'Quitter la lecture', { type: 'button', 'data-act': 'lecture-quitter' });
      exit.addEventListener('click', () => { set(false); button.focus({ preventScroll: true }); });
      note.append(exit);
      const anchor = node.querySelector('.at-reste');
      if (anchor) anchor.before(note); else hooks.sheet.before(note);
      apply();
    },
    /** La pastille d'un fil ouvert, en marge du bloc : discrète, un clic quitte la lecture et mène au fil. */
    dot(count) {
      const dot = el('button', 'at-read-dot', String(count), { type: 'button', 'data-role': 'read-dot', 'aria-label': dotLabel(count) });
      dot.addEventListener('click', () => {
        const row = dot.closest('.at-cmrow');
        set(false);
        requestAnimationFrame(() => {
          const thread = row && row.querySelector('[data-com]');
          if (!thread) return;
          thread.scrollIntoView({ block: 'center' });
          thread.focus({ preventScroll: true });
        });
      });
      return dot;
    },
  };
}
