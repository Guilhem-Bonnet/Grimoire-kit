// Atelier de spec (en test) : la recherche dans le document.
//
// Ctrl/Cmd+F, tant que le document d'un chantier est affiché, ouvre une petite barre : les occurrences sont surlignées dans toutes
// les phases, un compteur dit « 3 sur 12 », Entrée et Maj+Entrée vont à la suivante et à la précédente, Échap ferme et retire
// tout. Surlignage sans `innerHTML` : l'API CSS Custom Highlight quand elle existe, sinon des `<mark>` créés par le DOM et
// retirés proprement. Insensible à la casse et aux accents. Lecture seule : la feuille n'est jamais modifiée dans son contenu.

import { icon } from '../shell.js';
import { countLabel, findMatches, stepIndex } from './atelier-document-logic.js';

const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'SELECT', 'NOSCRIPT']);

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const hasHighlightApi = () => typeof CSS !== 'undefined' && Boolean(CSS.highlights) && typeof Highlight === 'function';

/** Les nœuds de texte de la feuille où chercher : ni champs de saisie, ni texte réservé aux lecteurs d'écran, ni parties masquées. */
function textNodes(sheet) {
  const walker = document.createTreeWalker(sheet, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      for (let parent = node.parentElement; parent && parent !== sheet.parentElement; parent = parent.parentElement) {
        if (SKIP.has(parent.tagName) || parent.hidden || parent.classList.contains('sr-only') || parent.classList.contains('at-edit')) return NodeFilter.FILTER_REJECT;
        if (parent === sheet) break;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const out = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) out.push(node);
  return out;
}

/**
 * `sheet` : la feuille dont on cherche le texte. Rend `{ element, attach(parent), open(), close(), isOpen() }`.
 * Le raccourci n'est écouté que tant que le document est monté (le signal du montage le retire).
 */
export function createFind(ctx, { sheet }) {
  const element = el('div', 'at-find', null, { role: 'search', 'aria-label': 'Rechercher dans le document', 'data-role': 'atelier-recherche' });
  element.hidden = true;
  const input = el('input', 'at-find-input', null, { type: 'text', 'aria-label': 'Rechercher dans le document', placeholder: 'Rechercher', autocomplete: 'off', spellcheck: 'false', 'data-role': 'find-input' });
  const counter = el('span', 'at-find-count', '', { 'aria-live': 'polite', 'data-role': 'find-count' });
  const prev = el('button', 'at-btn at-find-btn', null, { type: 'button', 'aria-label': 'Occurrence précédente', title: 'Précédente (Maj+Entrée)', 'data-act': 'find-prev' });
  prev.append(icon('i-act-back'));
  const next = el('button', 'at-btn at-find-btn', null, { type: 'button', 'aria-label': 'Occurrence suivante', title: 'Suivante (Entrée)', 'data-act': 'find-next' });
  next.append(icon('i-act-next'));
  const close = el('button', 'at-btn at-find-btn ghost', null, { type: 'button', 'aria-label': 'Fermer la recherche', title: 'Fermer (Échap)', 'data-act': 'find-close' });
  close.append(icon('i-act-close'));
  element.append(input, counter, prev, next, close);

  let ranges = []; // une plage par occurrence, dans l'ordre du document
  let marks = []; // le repli : les `<mark>` posés, une plage par occurrence
  let index = -1;
  let opened = false;
  let returnTo = null;
  let busy = false;

  const clear = () => {
    if (hasHighlightApi()) { CSS.highlights.delete('at-find'); CSS.highlights.delete('at-find-current'); }
    for (const mark of marks) {
      const parent = mark.parentNode;
      if (!parent) continue;
      mark.replaceWith(...mark.childNodes);
      parent.normalize();
    }
    marks = [];
    ranges = [];
    index = -1;
  };

  const paintCurrent = () => {
    if (hasHighlightApi() && ranges.length) {
      CSS.highlights.set('at-find-current', new Highlight(ranges[index]));
    }
    marks.forEach((mark, i) => mark.classList.toggle('is-current', i === index));
  };

  const reveal = () => {
    const range = ranges[index];
    if (!range) return;
    const host = range.startContainer.parentElement;
    if (host) host.scrollIntoView({ block: 'center', behavior: 'auto' });
  };

  const paintCount = () => { counter.textContent = countLabel(index, ranges.length, Boolean(input.value.trim())); };

  const run = ({ keep = false } = {}) => {
    const previous = index;
    busy = true;
    clear();
    const needle = input.value;
    if (opened && needle.trim()) {
      const found = [];
      for (const node of textNodes(sheet)) {
        for (const { start, end } of findMatches(node.nodeValue, needle)) found.push({ node, start, end });
      }
      // Du dernier au premier dans un même nœud : poser un `<mark>` ne décale pas les index des occurrences encore à poser.
      const made = found.map(({ node, start, end }) => {
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, end);
        return range;
      });
      if (hasHighlightApi()) {
        ranges = made;
        CSS.highlights.set('at-find', new Highlight(...ranges));
      } else {
        const wraps = [];
        for (let i = made.length - 1; i >= 0; i -= 1) {
          const mark = el('mark', 'at-hit');
          made[i].surroundContents(mark);
          wraps[i] = mark;
        }
        marks = wraps;
        ranges = wraps.map((mark) => { const r = document.createRange(); r.selectNodeContents(mark); return r; });
      }
      index = ranges.length ? (keep && previous >= 0 ? Math.min(previous, ranges.length - 1) : 0) : -1;
      paintCurrent();
      if (!keep) reveal();
    }
    paintCount();
    if (watch) watch.takeRecords();
    busy = false;
  };

  const step = (dir) => {
    if (!ranges.length) return;
    index = stepIndex(index, ranges.length, dir);
    paintCurrent();
    reveal();
    paintCount();
  };

  const open = () => {
    if (!opened) returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    opened = true;
    element.hidden = false;
    const selected = String(window.getSelection ? window.getSelection() : '').trim();
    if (selected && !selected.includes('\n') && selected.length <= 80 && !element.contains(document.activeElement)) input.value = selected;
    input.focus();
    input.select();
    run();
  };

  const shut = () => {
    if (!opened) return;
    opened = false;
    clear();
    element.hidden = true;
    input.value = '';
    paintCount();
    if (returnTo && returnTo.isConnected) returnTo.focus({ preventScroll: true });
    returnTo = null;
  };

  input.addEventListener('input', () => run());
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); step(event.shiftKey ? -1 : 1); }
  });
  element.addEventListener('keydown', (event) => { // Échap ferme, de n'importe où dans la barre (champ ou boutons)
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); shut(); }
  });
  prev.addEventListener('click', () => step(-1));
  next.addEventListener('click', () => step(1));
  close.addEventListener('click', shut);

  // La feuille est redessinée pendant que la barre est ouverte : les occurrences sont relues, sans rien changer à la place courante.
  let frame = 0;
  const watch = typeof MutationObserver === 'function' ? new MutationObserver(() => {
    if (busy || !opened || frame) return;
    frame = requestAnimationFrame(() => { frame = 0; if (opened && sheet.isConnected) run({ keep: true }); });
  }) : null;
  if (watch) watch.observe(sheet, { childList: true, subtree: true, characterData: true });

  // Ctrl/Cmd+F : seulement tant que ce document est monté et visible. Une barre déjà ouverte et active laisse la recherche du
  // navigateur faire son travail au second appui ; dans un champ d'édition de bloc, c'est elle aussi qui répond.
  const onKey = (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || String(event.key).toLowerCase() !== 'f') return;
    if (!sheet.isConnected || ctx.signal.aborted || !sheet.getClientRects().length) return;
    if (event.target instanceof Element && event.target.closest('.at-edit, dialog[open]')) return;
    if (opened && document.activeElement === input) return; // second appui : le navigateur cherche à son tour
    event.preventDefault();
    open();
  };
  document.addEventListener('keydown', onKey);
  ctx.signal.addEventListener('abort', () => {
    document.removeEventListener('keydown', onKey);
    if (watch) watch.disconnect();
    cancelAnimationFrame(frame);
    opened = false;
    clear();
  });

  return {
    element,
    attach(parent) { parent.append(element); },
    open,
    close: shut,
    isOpen: () => opened,
  };
}
