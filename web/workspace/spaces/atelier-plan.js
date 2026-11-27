// Atelier de spec (en test) : le plan du document, comme le « plan » d'un document partagé.
//
// Un volet à gauche de la feuille liste les phases puis leurs rubriques ; l'élément courant suit le défilement, un clic mène à
// la rubrique, un indicateur dit si elle est remplie ou « à compléter » (lu de l'avancement unique du serveur, jamais recalculé
// ici). Lecture seule : le volet ne touche ni au document ni à l'API. Replié sous 1100 px ; le choix est mémorisé par navigateur.

import { icon } from '../shell.js';
import { buildOutline, pickCurrent, planOpenDefault, STATE_WORDS } from './atelier-document-logic.js';
import { WIDE_MIN } from './atelier-layout-logic.js';
import { keepFocus } from './atelier-focus.js';

const PREF_KEY = 'grimoire.atelier.plan';
const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const readPref = () => { try { return localStorage.getItem(PREF_KEY); } catch { return null; } };
const writePref = (value) => { try { localStorage.setItem(PREF_KEY, value); } catch { /* le choix vaut pour la session */ } };

/**
 * `root` : la toile qui défile ; `sheet` : la feuille ; `doc()` : le document chargé (suivi à chaque redessin de la feuille).
 * Rend `{ element, attach(grid), refresh() }`.
 */
export function createPlan(ctx, { root, sheet, doc }) {
  // Le volet n'existe dans la grille que déplié ; son bouton, lui, reste dans la barre collante pour le rouvrir.
  const element = el('aside', 'at-plan', null, { 'data-role': 'atelier-plan' });
  const toggle = el('button', 'at-docmenu-btn at-plan-toggle', null, { type: 'button', 'data-role': 'plan-toggle', 'aria-controls': 'at-plan-list' });
  toggle.append(icon('i-ui-panel-left'), 'Plan');
  const list = el('nav', 'at-plan-list', null, { id: 'at-plan-list', 'aria-label': 'Plan du document' });
  element.append(list);

  const stored = readPref();
  const assistantOpen = () => Boolean(root.querySelector('.at-asst:not([hidden])'));
  let isOpen = planOpenDefault(stored, root.clientWidth, WIDE_MIN, assistantOpen());
  let userChose = stored === 'open' || stored === 'closed';
  let targets = []; // `{ id, node, link }` dans l'ordre du document
  let current = '';
  const touching = new Set();

  const paintOpen = () => {
    element.setAttribute('data-open', String(isOpen));
    element.hidden = !isOpen;
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.setAttribute('aria-pressed', String(isOpen));
    toggle.title = isOpen ? 'Replier le plan du document' : 'Ouvrir le plan du document';
  };
  toggle.addEventListener('click', () => {
    isOpen = !isOpen;
    userChose = true;
    writePref(isOpen ? 'open' : 'closed');
    paintOpen();
  });
  list.addEventListener('keydown', (event) => { // Échap replie le plan et rend le focus à son bouton
    if (event.key !== 'Escape' || !isOpen) return;
    event.stopPropagation();
    isOpen = false;
    userChose = true;
    writePref('closed');
    paintOpen();
    toggle.focus();
  });

  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const go = (entry) => {
    if (entry.phase) {
      // Une phase : le geste existant de la barre des phases (adresse, assistant, chat).
      const button = root.querySelector(`.at-phases [data-phase="${CSS.escape(entry.id)}"]`);
      if (button) { button.click(); return; }
    }
    const node = entry.node();
    if (!node) return;
    node.scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' });
    node.focus({ preventScroll: true });
  };

  const markCurrent = (id) => {
    current = id;
    for (const t of targets) {
      if (t.id === id) t.link.setAttribute('aria-current', 'location'); else t.link.removeAttribute('aria-current');
    }
    const active = targets.find((t) => t.id === id);
    if (active && isOpen && list.scrollHeight > list.clientHeight) { // garde l'élément courant visible dans un long plan
      const top = active.link.offsetTop;
      if (top < list.scrollTop || top + active.link.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - 40);
    }
  };

  const decide = () => {
    const atBottom = root.scrollTop > 0 && root.scrollHeight - root.scrollTop - root.clientHeight < 4;
    const inBand = new Set(targets.filter((t) => touching.has(t.node())).map((t) => t.id));
    markCurrent(pickCurrent(targets.map((t) => t.id), inBand, current, atBottom));
  };

  const spy = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) touching.add(entry.target); else touching.delete(entry.target);
      }
      decide();
    }, { root, rootMargin: '-88px 0px -65% 0px' })
    : null;
  let frame = 0;
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; decide(); }); };
  root.addEventListener('scroll', onScroll, { passive: true });

  const indicator = (state) => {
    const mark = el('span', `at-plan-mark is-${state}`, null, { 'aria-hidden': 'true', 'data-state': state });
    return mark;
  };

  // Le plan se redessine à chaque rechargement de la feuille : un lien qui a le focus le garde (même `data-plan`).
  const draw = () => keepFocus(list, build);
  const build = () => {
    if (spy) spy.disconnect();
    touching.clear();
    targets = [];
    list.replaceChildren();
    const outline = buildOutline(doc(), PHASE_NAMES);
    const ol = el('ol', 'at-plan-phases');
    for (const phase of outline) {
      const section = sheet.querySelector(`[data-section="${CSS.escape(phase.id)}"]`);
      if (!section) continue;
      const item = el('li', 'at-plan-phase');
      const head = el('button', 'at-plan-link at-plan-phase-link', null, { type: 'button', 'data-plan': phase.id, 'data-state': phase.state });
      head.append(indicator(phase.state), el('span', 'at-plan-text', phase.title), el('span', 'sr-only', ` — ${STATE_WORDS[phase.state]}`));
      const entry = { id: phase.id, phase: true, link: head, node: () => sheet.querySelector(`[data-section="${CSS.escape(phase.id)}"] h2`) };
      head.addEventListener('click', () => go(entry));
      targets.push(entry);
      item.append(head);
      const rubs = [...section.querySelectorAll(':scope > .at-rub')];
      const sub = el('ol', 'at-plan-rubs');
      phase.rubriques.forEach((rub, index) => {
        const node = rubs[index];
        if (!node) return;
        const id = `${phase.id}/${index}`;
        const link = el('button', 'at-plan-link', null, { type: 'button', 'data-plan': id, 'data-state': rub.state });
        link.append(indicator(rub.state), el('span', 'at-plan-text', rub.name), el('span', 'sr-only', ` — ${STATE_WORDS[rub.state]}`));
        node.setAttribute('tabindex', '-1');
        const rubEntry = { id, link, node: () => rubs[index] };
        link.addEventListener('click', () => go(rubEntry));
        targets.push(rubEntry);
        const li = el('li');
        li.append(link);
        sub.append(li);
      });
      if (sub.children.length) item.append(sub);
      ol.append(item);
    }
    list.append(ol);
    if (spy) for (const t of targets) { const n = t.node(); if (n) spy.observe(n); }
    markCurrent(targets.some((t) => t.id === current) ? current : (targets[0] ? targets[0].id : ''));
  };

  // La feuille est redessinée (rechargement, bloc réécrit, suggestion tranchée) : le plan suit, sans toucher à la feuille.
  let redraw = 0;
  const watch = typeof MutationObserver === 'function' ? new MutationObserver(() => {
    if (!redraw) redraw = requestAnimationFrame(() => { redraw = 0; if (sheet.isConnected) draw(); });
  }) : null;
  if (watch) watch.observe(sheet, { childList: true });
  // Sans choix de la personne, la largeur de la toile décide : replié sous 1100 px.
  const size = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    if (userChose) return;
    const next = planOpenDefault(null, root.clientWidth, WIDE_MIN, assistantOpen());
    if (next !== isOpen) { isOpen = next; paintOpen(); }
  }) : null;
  if (size) size.observe(root);

  ctx.signal.addEventListener('abort', () => {
    if (spy) spy.disconnect();
    if (watch) watch.disconnect();
    if (size) size.disconnect();
    root.removeEventListener('scroll', onScroll);
    cancelAnimationFrame(frame);
    cancelAnimationFrame(redraw);
  });

  paintOpen();
  return {
    element,
    toggle,
    /** Place le volet dans la grille du document, avant la feuille, et dessine le plan. */
    attach(grid) {
      grid.classList.add('at-has-plan');
      grid.prepend(element);
      draw();
      const asst = grid.querySelector('.at-asst'); // l'assistant déplié ou replié change la place laissée à la feuille
      if (size && asst) size.observe(asst);
    },
    refresh: draw,
  };
}
