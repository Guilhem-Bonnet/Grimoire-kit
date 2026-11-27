// Atelier de spec (en test) : la recherche dans le document.
//
// Ctrl/Cmd+F, tant que le document d'un chantier est affiché, ouvre une petite barre : les occurrences sont surlignées dans toutes
// les phases, un compteur dit « 3 sur 12 », Entrée et Maj+Entrée vont à la suivante et à la précédente, Échap ferme et retire
// tout. Surlignage sans `innerHTML` : l'API CSS Custom Highlight quand elle existe, sinon des `<mark>` créés par le DOM et
// retirés proprement. Insensible à la casse et aux accents. Lecture seule : la feuille n'est jamais modifiée dans son contenu.

/* global NodeFilter, Highlight */

import { icon } from '../shell.js';
import { countLabel, findAcross, matchSlices, stepIndex } from './atelier-document-logic.js';

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
      if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
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
 * Les nœuds de texte de la feuille, groupés par bloc : le texte d'un bloc se cherche d'un seul tenant, à travers les nœuds
 * inline (gras, code, lien…) qui le coupent. Un bloc est l'ancêtre le plus proche dont l'affichage n'est pas « inline » (un
 * bouton, une pastille ou une cellule sont des blocs : une occurrence ne les traverse pas). `[[Text, …], …]`.
 */
function textBlocks(sheet) {
  const display = new Map();
  const isInline = (element) => {
    if (!display.has(element)) display.set(element, getComputedStyle(element).display);
    const value = display.get(element);
    return value === 'inline' || value === 'contents';
  };
  const blockOf = (node) => {
    let parent = node.parentElement;
    while (parent && parent !== sheet && isInline(parent)) parent = parent.parentElement;
    return parent || sheet;
  };
  const groups = [];
  let last = null;
  for (const node of textNodes(sheet)) {
    const block = blockOf(node);
    if (block !== last) { groups.push([]); last = block; }
    groups[groups.length - 1].push(node);
  }
  return groups;
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
  let marks = []; // le repli : les `<mark>` posés, une liste par occurrence (une occurrence sur plusieurs nœuds en porte plusieurs)
  let index = -1;
  let opened = false;
  let returnTo = null;
  let busy = false;

  const clear = () => {
    if (hasHighlightApi()) { CSS.highlights.delete('at-find'); CSS.highlights.delete('at-find-current'); }
    for (const mark of marks.flat()) {
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
    marks.forEach((group, i) => group.forEach((mark) => mark.classList.toggle('is-current', i === index)));
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
      // Une occurrence se cherche dans le texte du bloc entier : elle peut commencer dans un nœud et finir dans un autre.
      const found = []; // { nodes, segments, match }
      for (const nodes of textBlocks(sheet)) {
        const segments = nodes.map((node) => node.nodeValue);
        for (const match of findAcross(segments, needle)) found.push({ nodes, segments, match });
      }
      if (hasHighlightApi()) {
        ranges = found.map(({ nodes, match }) => {
          const range = document.createRange();
          range.setStart(nodes[match.startSeg], match.startOffset);
          range.setEnd(nodes[match.endSeg], match.endOffset);
          return range;
        });
        CSS.highlights.set('at-find', new Highlight(...ranges));
      } else {
        // Un `<mark>` par nœud touché, posés de la dernière occurrence à la première (et du dernier morceau au premier) : poser
        // un `<mark>` coupe un nœud de texte sans décaler les index de ce qui reste à poser avant lui.
        const groups = new Array(found.length);
        for (let i = found.length - 1; i >= 0; i -= 1) {
          const { nodes, segments, match } = found[i];
          const slices = matchSlices(match, segments);
          const made = [];
          for (let k = slices.length - 1; k >= 0; k -= 1) {
            const range = document.createRange();
            range.setStart(nodes[slices[k].seg], slices[k].start);
            range.setEnd(nodes[slices[k].seg], slices[k].end);
            const mark = el('mark', 'at-hit');
            range.surroundContents(mark);
            made[k] = mark;
          }
          groups[i] = made;
        }
        marks = groups;
        ranges = groups.map((group) => {
          const r = document.createRange();
          r.setStartBefore(group[0]);
          r.setEndAfter(group[group.length - 1]);
          return r;
        });
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
