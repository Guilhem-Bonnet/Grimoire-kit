// Atelier de spec (en test) : l'historique des versions du document, en lecture seule.
//
// Une entrée « Historique » dans la barre ouvre un volet à droite (à la place de la colonne de l'assistant tant qu'il est
// ouvert) : les versions du chantier regroupées par jour, avec l'auteur et un résumé « +12 −3, Brief et Exigences ». Cliquer
// une version met la feuille en mode « comparaison » : un bandeau « Tu regardes la version du … — Revenir au document », puis,
// phase par phase, les lignes ajoutées (+, vert) et retirées (−, rouge, barrées). La feuille elle-même n'est jamais modifiée :
// elle est seulement masquée, et revient intacte (Échap ou le bouton). Aucune restauration ici.
// Données : `GET atelier/chantiers/<slug>/history[?page=n]` et `…/history/<id>` (voir `workspace_atelier_history.py`).
// Tout texte venu du serveur passe par `textContent` : aucun innerHTML.

import { icon } from '../shell.js';
import {
  arrowTarget, compareLabel, diffLines, emptyText, groupByDay, phaseName, timeLabel, versionSummary, versionTitle,
} from './atelier-historique-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/**
 * Le volet d'historique. `hooks` : `sheet` (la feuille du document), `main` (son conteneur : la comparaison s'y insère
 * à côté de la feuille), `grid` (la grille feuille + volet), `say(message)` (la ligne d'état), `onCompare(ids)` (les phases de la
 * comparaison affichée, nul à son retrait). Rend `{ button, element,
 * isOpen, close }` : `button` va dans la barre, `element` dans la grille.
 */
export function createHistory(ctx, slug, hooks) {
  const element = el('aside', 'at-hist', null, { 'aria-label': 'Historique des versions', 'data-role': 'atelier-historique', id: 'at-hist-panel', hidden: '' });
  const button = el('button', 'at-btn at-hist-btn', null, {
    type: 'button', 'data-role': 'atelier-historique-bouton', 'aria-expanded': 'false', 'aria-controls': 'at-hist-panel',
    title: 'Voir les versions précédentes du document (lecture seule)',
  });
  button.append(icon('i-act-preview'), 'Historique');

  const head = el('div', 'at-hist-head');
  const close = el('button', 'at-hist-close', null, { type: 'button', 'aria-label': 'Fermer l’historique', 'data-role': 'hist-fermer' });
  close.append(icon('i-act-close'));
  head.append(el('h2', 'at-hist-title', 'Historique'), close);
  const note = el('p', 'at-muted at-hist-note', 'Lecture seule : rien n’est restauré ni modifié.');
  const body = el('div', 'at-hist-body', null, { 'data-role': 'hist-liste' });
  element.append(head, note, body);

  let compare = null; // la section de comparaison, posée à côté de la feuille ; la feuille n'est que masquée
  let current = null; // l'identifiant de la version regardée
  let versions = [];
  let page = 1;
  let loaded = false;
  let requested = 0;

  const open = () => element.isConnected && !element.hidden;

  // ── Comparaison ─────────────────────────────────────────────────────────────────────────────────────────

  const leaveCompare = ({ focus = false } = {}) => {
    if (!compare) return;
    compare.remove();
    compare = null;
    current = null;
    hooks.sheet.hidden = false;
    if (hooks.onCompare) hooks.onCompare(null);
    for (const row of body.querySelectorAll('[aria-current="true"]')) row.removeAttribute('aria-current');
    if (focus) hooks.sheet.querySelector('[data-section] h2')?.focus({ preventScroll: true });
  };

  const phaseView = (phase) => {
    const section = el('section', 'at-cmp-phase', null, { 'data-phase': phase.id, 'aria-label': `${phaseName(phase)} : ${phase.added} ajoutée${phase.added > 1 ? 's' : ''}, ${phase.removed} retirée${phase.removed > 1 ? 's' : ''}` });
    section.append(el('h3', 'at-cmp-h', `${phaseName(phase)}  +${phase.added} −${phase.removed}`));
    if (phase.note) section.append(el('p', 'at-muted', phase.note));
    const lines = el('div', 'at-cmp-lines', null, { role: 'list' });
    for (const line of diffLines(phase)) {
      const row = el('div', `at-cmp-line is-${line.kind}`, null, { role: 'listitem', 'data-t': line.kind });
      if (line.kind === 'gap') { row.append(el('span', 'at-cmp-gap', line.text)); lines.append(row); continue; }
      row.append(
        el('span', 'sr-only', `${line.spoken} : `),
        el('span', 'at-cmp-no', String(line.now || line.old), { 'aria-hidden': 'true' }),
        el('span', 'at-cmp-mark', line.mark, { 'aria-hidden': 'true' }),
        el('span', 'at-cmp-text', line.text || ' '),
      );
      lines.append(row);
    }
    section.append(lines);
    return section;
  };

  const showCompare = (version, data) => {
    leaveCompare();
    const section = el('section', 'at-cmp', null, { 'aria-label': 'Comparaison avec une version précédente', 'data-role': 'hist-comparaison' });
    const back = el('button', 'at-btn pri at-cmp-back', 'Revenir au document', { type: 'button', 'data-role': 'hist-retour' });
    back.addEventListener('click', () => { leaveCompare({ focus: true }); button.focus({ preventScroll: true }); });
    const banner = el('div', 'at-cmp-banner', null, { role: 'status', 'data-role': 'hist-bandeau' });
    const text = el('span', 'at-cmp-banner-text');
    text.append(el('strong', null, compareLabel(version)), ` ${versionTitle(version)} — ${versionSummary(data)}. `);
    banner.append(text, back);
    section.append(banner);
    if (!data.phases.length) section.append(el('p', 'at-muted', 'Aucune ligne ne diffère dans les phases de ce chantier.', { 'data-role': 'hist-vide' }));
    for (const phase of data.phases) section.append(phaseView(phase));
    if (data.truncated) section.append(el('p', 'at-muted', 'Comparaison tronquée : le document est trop long pour tout afficher ici.', { 'data-role': 'hist-tronque' }));
    hooks.sheet.hidden = true;
    hooks.main.insertBefore(section, hooks.sheet);
    compare = section;
    current = version.id;
    if (hooks.onCompare) hooks.onCompare(data.phases.map((phase) => phase.id)); // la barre des phases marque celle du diff affiché
    back.focus({ preventScroll: true });
  };

  const pick = async (version, row) => {
    for (const other of body.querySelectorAll('[aria-current="true"]')) other.removeAttribute('aria-current');
    row.setAttribute('aria-current', 'true');
    const ticket = ++requested;
    try {
      const data = await ctx.api.atelierVersion(slug, version.id);
      if (ctx.signal.aborted || ticket !== requested || !open()) return;
      showCompare(version, data);
    } catch (cause) {
      if (ctx.signal.aborted || ticket !== requested) return;
      row.removeAttribute('aria-current');
      hooks.say(`Impossible de lire cette version : ${cause.message || cause}`);
    }
  };

  // ── Liste ───────────────────────────────────────────────────────────────────────────────────────────────

  const draw = (data) => {
    body.replaceChildren();
    if (!versions.length) {
      body.append(el('p', 'at-muted', emptyText(data), { 'data-role': 'hist-vide', role: 'status' }));
      return;
    }
    const list = el('div', 'at-hist-list');
    const buttons = [];
    for (const group of groupByDay(versions)) {
      const section = el('section', 'at-hist-day', null, { 'aria-label': group.label });
      section.append(el('h3', 'at-hist-day-h', group.label));
      for (const version of group.items) {
        const row = el('button', 'at-hist-row', null, { type: 'button', 'data-version': version.id, 'data-kind': version.kind });
        if (version.id === current) row.setAttribute('aria-current', 'true');
        const when = version.kind === 'working' ? '' : timeLabel(version.date);
        row.append(
          el('span', 'at-hist-subject', versionTitle(version)),
          el('span', 'at-hist-sum', versionSummary(version)),
          el('span', 'at-muted at-hist-who', [when, version.author].filter(Boolean).join(' · ')),
        );
        row.addEventListener('click', () => pick(version, row));
        buttons.push(row);
        section.append(row);
      }
      list.append(section);
    }
    list.addEventListener('keydown', (event) => {
      const at = buttons.indexOf(document.activeElement);
      const next = at < 0 ? null : arrowTarget(at, event.key, buttons.length);
      if (next == null) return;
      event.preventDefault();
      buttons[next].focus();
    });
    body.append(list);
    if (data.has_more) {
      const more = el('button', 'at-btn ghost', 'Versions plus anciennes', { type: 'button', 'data-role': 'hist-plus' });
      more.addEventListener('click', () => load(page + 1));
      body.append(more);
    }
  };

  const load = async (target) => {
    if (target === 1) body.replaceChildren(el('p', 'at-muted', 'Chargement…', { role: 'status' }));
    try {
      const data = await ctx.api.atelierHistory(slug, target);
      if (ctx.signal.aborted) return;
      versions = target === 1 ? data.versions : versions.concat(data.versions);
      page = target;
      loaded = true;
      draw(data);
    } catch (cause) {
      if (ctx.signal.aborted) return;
      body.replaceChildren(el('p', 'at-error', `Historique indisponible : ${cause.message || cause}`, { role: 'alert' }));
    }
  };

  // ── Ouverture et fermeture ──────────────────────────────────────────────────────────────────────────────

  const setOpen = (next) => {
    if (next === open()) return;
    element.hidden = !next;
    button.setAttribute('aria-expanded', String(next));
    hooks.grid.classList.toggle('has-hist', next);
    if (next) {
      load(1); // chaque ouverture relit : un commit a pu arriver depuis
      close.focus({ preventScroll: true });
    } else {
      leaveCompare();
      loaded = false;
      versions = [];
      button.focus({ preventScroll: true });
    }
  };

  button.addEventListener('click', () => setOpen(!open()));
  close.addEventListener('click', () => setOpen(false));
  const onKey = (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented || !open()) return;
    if (compare) { event.preventDefault(); leaveCompare({ focus: true }); return; }
    if (element.contains(document.activeElement)) { event.preventDefault(); setOpen(false); }
  };
  document.addEventListener('keydown', onKey);
  ctx.signal.addEventListener('abort', () => document.removeEventListener('keydown', onKey));

  return { button, element, isOpen: open, close: () => setOpen(false), isLoaded: () => loaded };
}
