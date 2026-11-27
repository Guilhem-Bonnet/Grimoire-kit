// Atelier de spec : les références du texte deviennent des liens (exigence, tâche du kit, autre chantier).
//
// Le repérage et la résolution sont dans `atelier-liens-logic.js` (sans DOM) ; le rendu des nœuds dans `atelier-rendu.js`, qui
// appelle l'objet rendu ici (`resolve`, `activate`, `bind`). Un lien d'exigence défile jusqu'à sa ligne (sans recharger la page),
// la surligne brièvement, et s'accompagne au survol et au focus d'une petite carte (titre, priorité, état de validation) lue dans
// les données déjà chargées. Une référence qui ne vise rien reste du texte ; `[[chantier]]` inexistant porte une infobulle.

import { atelierHash, cardStateText, exigenceBlocks, exigenceCard, resolveChantier } from './atelier-liens-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** Surligne brièvement un nœud (la classe retombe seule ; le mouvement réduit garde un simple contour). */
export function flash(ctx, node, className = 'is-ref-target', ms = 2200) {
  if (!node) return;
  node.classList.remove(className);
  void node.offsetWidth; // relance l'animation si le nœud était déjà surligné
  node.classList.add(className);
  const timer = setTimeout(() => node.classList.remove(className), ms);
  ctx.signal.addEventListener('abort', () => clearTimeout(timer));
}

/**
 * `hooks` : `slug`, `doc()`, `trace()` (la trace du découpage, ou nulle), `chantiers()` (les lignes de la bibliothèque, ou
 * `null` si elles n'ont pas pu être lues), `go(req)` (défiler jusqu'à l'exigence). Rend `{ resolve, activate, bind, hide,
 * signature, mount }`.
 */
export function createRefs(ctx, hooks) {
  const card = el('div', 'at-refcard', null, { role: 'tooltip', id: 'at-refcard', hidden: '', 'data-role': 'ref-card' });
  let shownFor = null;

  const resolve = (type, value) => {
    if (type === 'req') {
      return exigenceBlocks(hooks.doc()).has(value) ? { href: atelierHash({ slug: hooks.slug, phase: 'exigences', req: value }) } : null;
    }
    if (type === 'task') return { href: `#travail?task=${encodeURIComponent(value)}`, title: 'Ouvrir la tâche dans Travail' };
    if (type === 'chantier') {
      const rows = hooks.chantiers();
      if (!rows) return null; // la liste n'a pas pu être lue : on n'affirme rien, le texte reste tel quel
      const found = resolveChantier(value, rows);
      if (!found) return { missing: true, title: 'chantier introuvable' };
      return { href: atelierHash({ slug: found.slug }), title: `Ouvrir le chantier ${found.titre || found.slug}` };
    }
    return null;
  };

  const activate = (event, type, value) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; // un nouvel onglet reste possible
    event.preventDefault();
    hide();
    if (type === 'req') hooks.go(value);
    else if (type === 'task') ctx.goto('travail', { task: value });
    else if (type === 'chantier') {
      const found = resolveChantier(value, hooks.chantiers() || []);
      if (found) ctx.goto('atelier', { chantier: found.slug });
    }
  };

  const fill = (data) => {
    card.replaceChildren();
    const head = el('div', 'at-refcard-head');
    head.append(el('span', 'at-refcard-id', data.req));
    if (data.priorite) head.append(el('span', 'at-refcard-prio', data.priorite));
    card.append(head, el('p', 'at-refcard-title', data.titre));
    const state = cardStateText(data);
    if (state) card.append(el('p', `at-refcard-state${data.validee ? ' is-ok' : ''}`, state));
  };

  function hide() {
    if (!shownFor) return;
    card.hidden = true;
    shownFor.removeAttribute('aria-describedby');
    shownFor = null;
  }

  const show = (link, req) => {
    const data = exigenceCard(hooks.doc(), req, hooks.trace());
    if (!data) return;
    hide();
    fill(data);
    card.hidden = false;
    shownFor = link;
    link.setAttribute('aria-describedby', card.id);
    const rect = link.getBoundingClientRect();
    const width = card.offsetWidth;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const below = rect.bottom + 6;
    const top = below + card.offsetHeight > window.innerHeight - 8 ? Math.max(8, rect.top - card.offsetHeight - 6) : below;
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  };

  const bind = (link, type, value) => {
    if (type !== 'req') return;
    link.addEventListener('pointerenter', () => show(link, value));
    link.addEventListener('pointerleave', hide);
    link.addEventListener('focus', () => show(link, value));
    link.addEventListener('blur', hide);
  };

  const onKey = (event) => { if (event.key === 'Escape' && shownFor) hide(); };
  document.addEventListener('keydown', onKey);
  ctx.signal.addEventListener('abort', () => { document.removeEventListener('keydown', onKey); card.remove(); });

  return {
    resolve,
    activate,
    bind,
    hide,
    /** Ce dont dépend le rendu des références : change quand une exigence ou un chantier apparaît ou disparaît. */
    signature: () => [...exigenceBlocks(hooks.doc()).keys()].join(',') + '|' + (hooks.chantiers() || []).map((r) => `${r.slug}:${r.titre}`).join(','),
    // Sous `<body>` : la toile est un conteneur (`container-type`), qui ancrerait un `position: fixed` à elle-même.
    mount: () => document.body.append(card),
  };
}
