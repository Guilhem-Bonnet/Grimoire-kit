// Atelier de spec (en test) : l'historique des versions du document, la comparaison de deux versions, la restauration.
//
// Une entrée « Historique » dans la barre ouvre un volet à droite (à la place de la colonne de l'assistant tant qu'il est
// ouvert) : les versions du chantier regroupées par jour, avec l'auteur et un résumé « +12 −3, Brief et Exigences ». Cliquer
// une version met la feuille en mode « comparaison » : un bandeau « Tu regardes la version du … — Revenir au document », puis,
// phase par phase, les lignes ajoutées (+, vert) et retirées (−, rouge, barrées). La feuille elle-même n'est jamais modifiée par
// la comparaison : elle est seulement masquée, et revient intacte (Échap ou le bouton).
//
// Comparer : « Comparer avec la version actuelle » sur chaque version, ou deux versions choisies (case à cocher ou Maj+clic) ;
// le bandeau dit « Tu compares A et B ». Restaurer (depuis la comparaison d'une version ancienne avec la version actuelle) :
// « Restaurer ce bloc » réécrit le bloc par la route d'édition d'un bloc (son contrôle de conflit par bloc), « Restaurer cette
// phase » remplace le fichier de la phase par celui de la version (le contenu vient du serveur, jamais d'ici ; 409 si le
// fichier a changé depuis la lecture). Une boîte de confirmation dit ce qui va changer ; l'accusé propose « Annuler » pendant
// la session. Rien n'est commité : le message le dit.
// Données : `GET atelier/chantiers/<slug>/history[?page=n]`, `…/history/<id>`, `…/compare?from&to`, `POST …/restore`
// (voir `workspace_atelier_history.py` et `workspace_atelier_restore.py`).
// Tout texte venu du serveur passe par `textContent` : aucun innerHTML.

import { icon } from '../shell.js';
import {
  arrowTarget, blockCounts, compareLabel, countsText, diffLines, emptyText, findOnlyBlock, groupByDay, orderPair, pairLabel, phaseName,
  restoreCounts, restoreNote, restoredText, timeLabel, toggleSelection, versionSummary, versionTitle,
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
 * comparaison affichée, nul à son retrait), `reload(message, kind)` (relit le document après une restauration), `guard(avant, après)`
 * et `guarded(garde)` (facultatifs : la confirmation avant de défaire la validation d'une exigence, et son rappel après). Rend `{ button,
 * element, isOpen, close }` : `button` va dans la barre, `element` dans la grille.
 */
export function createHistory(ctx, slug, hooks) {
  const element = el('aside', 'at-hist', null, { 'aria-label': 'Historique des versions', 'data-role': 'atelier-historique', id: 'at-hist-panel', hidden: '' });
  const button = el('button', 'at-btn at-hist-btn', null, {
    type: 'button', 'data-role': 'atelier-historique-bouton', 'aria-expanded': 'false', 'aria-controls': 'at-hist-panel',
    title: 'Voir, comparer et restaurer les versions précédentes du document',
  });
  button.append(icon('i-act-preview'), 'Historique');

  const head = el('div', 'at-hist-head');
  const close = el('button', 'at-hist-close', null, { type: 'button', 'aria-label': 'Fermer l’historique', 'data-role': 'hist-fermer' });
  close.append(icon('i-act-close'));
  head.append(el('h2', 'at-hist-title', 'Historique'), close);
  const note = el('p', 'at-muted at-hist-note', 'Compare deux versions, restaure un bloc ou une phase. Une restauration modifie seulement tes fichiers : rien n’est commité.');
  const ackBox = el('div', 'at-hist-ack', null, { role: 'status', 'data-role': 'hist-accuse', hidden: '' });
  const pairBar = el('div', 'at-hist-pair', null, { 'data-role': 'hist-selection', hidden: '' });
  const pairText = el('span', 'at-hist-pair-text');
  const pairGo = el('button', 'at-btn pri', 'Comparer ces deux versions', { type: 'button', 'data-role': 'hist-comparer-deux' });
  const pairClear = el('button', 'at-btn ghost', 'Effacer', { type: 'button', 'data-role': 'hist-effacer' });
  pairBar.append(pairText, pairGo, pairClear);
  const body = el('div', 'at-hist-body', null, { 'data-role': 'hist-liste' });
  element.append(head, note, ackBox, pairBar, body);

  let compare = null; // la section de comparaison, posée à côté de la feuille ; la feuille n'est que masquée
  let current = null; // l'identifiant de la version regardée
  let shown = null; // { from, to, heading } : de quoi relire la comparaison affichée
  let versions = [];
  let selected = []; // les identifiants cochés (deux au plus)
  const checks = new Map();
  let page = 1;
  let loaded = false;
  let requested = 0;

  const open = () => element.isConnected && !element.hidden;

  // ── Accusé (avec « Annuler » pendant la session) ────────────────────────────────────────────────────────

  /** `undo` : une fonction asynchrone qui rétablit le contenu d'avant (ou rien : pas de bouton). */
  const setAck = (text, { undo = null, kind = 'ok' } = {}) => {
    ackBox.replaceChildren(el('span', 'at-hist-ack-text', text));
    ackBox.setAttribute('data-kind', kind);
    ackBox.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    if (undo) {
      const again = el('button', 'at-btn', 'Annuler', { type: 'button', 'data-role': 'hist-annuler' });
      again.addEventListener('click', async () => {
        again.disabled = true;
        try {
          await undo();
          setAck('Annulé : le contenu d’avant est revenu.');
          hooks.say('Restauration annulée.');
          if (hooks.reload) await hooks.reload('Restauration annulée : le contenu d’avant est revenu.', 'ok');
          if (open()) load(1);
        } catch (cause) {
          const conflict = cause && cause.code === 409;
          setAck(conflict ? 'Le fichier a changé depuis la restauration : l’annulation est refusée, rien n’est écrit.' : `Annulation impossible : ${cause.message || cause}`, { kind: 'error' });
        }
      });
      ackBox.append(again);
    }
    ackBox.hidden = false;
  };

  // ── Comparaison ─────────────────────────────────────────────────────────────────────────────────────────

  const leaveCompare = ({ focus = false } = {}) => {
    if (!compare) return;
    compare.remove();
    compare = null;
    current = null;
    shown = null;
    hooks.sheet.hidden = false;
    if (hooks.onCompare) hooks.onCompare(null);
    for (const row of body.querySelectorAll('[aria-current="true"]')) row.removeAttribute('aria-current');
    if (focus) hooks.sheet.querySelector('[data-section] h2')?.focus({ preventScroll: true });
  };

  /** La boîte de confirmation : ce qui va changer, « Restaurer » ou « Renoncer ». Rend une promesse de booléen. */
  const confirmBox = ({ title, lines, yes }) => new Promise((resolve) => {
    const dialog = el('dialog', 'at-hist-confirm', null, { 'data-role': 'hist-confirmation', 'aria-labelledby': 'at-hist-confirm-title' });
    const form = el('form', 'at-hist-confirm-form', null, { method: 'dialog' });
    form.append(el('h3', 'at-hist-confirm-title', title, { id: 'at-hist-confirm-title' }));
    for (const line of lines) form.append(el('p', 'at-hist-confirm-line', line));
    const actions = el('div', 'at-hist-confirm-actions');
    const no = el('button', 'at-btn', 'Renoncer', { type: 'submit', value: 'no', 'data-role': 'hist-renoncer', autofocus: '' });
    const ok = el('button', 'at-btn pri', yes, { type: 'submit', value: 'ok', 'data-role': 'hist-confirmer' });
    actions.append(no, ok);
    form.append(actions);
    dialog.append(form);
    dialog.addEventListener('close', () => { const answer = dialog.returnValue === 'ok'; dialog.remove(); resolve(answer); });
    element.append(dialog);
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    no.focus();
  });

  const failure = async (cause, what) => {
    if (cause && cause.code === 409) {
      setAck(`${what} : le contenu a changé depuis ta lecture, rien n’est restauré. La comparaison est relue.`, { kind: 'error' });
      await refreshCompare();
      return;
    }
    setAck(`${what} : ${cause.message || cause}`, { kind: 'error' });
  };

  const restorePhase = async (data, phase) => {
    const name = phaseName(phase);
    const planned = restoreCounts(phase);
    const yes = await confirmBox({
      title: `Restaurer la phase ${name} ?`,
      lines: [
        `Le fichier de la phase ${name} sera remplacé par celui de ${data.from.kind === 'working' ? 'la version actuelle' : `la version « ${versionTitle(data.from)} »`} (${countsText(planned)}).`,
        'Rien n’est commité : c’est une modification non commitée de tes fichiers, que tu pourras annuler dans cette session.',
      ],
      yes: 'Restaurer cette phase',
    });
    if (!yes) return;
    try {
      const out = await ctx.api.atelierRestore(slug, { phase: phase.id, expected: phase.file_fp, version: data.from.id });
      leaveCompare();
      if (out.unchanged) {
        setAck(`La phase ${name} est déjà identique à cette version : rien n’a changé.`);
      } else {
        const undo = out.undo ? async () => ctx.api.atelierRestore(slug, { phase: phase.id, undo: out.undo, expected: out.phase.fingerprint }) : null;
        setAck(restoredText(`La phase ${name}`, { plus: out.added, minus: out.removed }), { undo });
      }
      hooks.say(`Phase ${name} restaurée (non commité).`);
      if (hooks.reload) await hooks.reload(`Phase ${name} restaurée (non commité).`, 'ok');
      if (open()) load(1);
    } catch (cause) {
      await failure(cause, `La phase ${name} n’a pas été restaurée`);
    }
  };

  const restoreBlock = async (data, phase, block) => {
    const planned = blockCounts(block);
    const yes = await confirmBox({
      title: 'Restaurer ce bloc ?',
      lines: [
        `Le bloc de la phase ${phaseName(phase)} (ligne ${block.line}) reprendra le texte de ${data.from.kind === 'working' ? 'la version actuelle' : `la version « ${versionTitle(data.from)} »`} (${countsText(planned)}).`,
        'Rien n’est commité : c’est une modification non commitée de tes fichiers, que tu pourras annuler dans cette session.',
      ],
      yes: 'Restaurer ce bloc',
    });
    if (!yes) return;
    const guard = hooks.guard ? await hooks.guard(block.text, block.old_text) : null; // exigence validée : confirmation propre
    if (guard && !guard.go) return;
    try {
      const mark = { fp: block.fp, prev: block.prev, next: block.next, twins: block.twins, text: block.text };
      const saved = await ctx.api.atelierBloc(slug, block.id, block.old_text, mark);
      const back = findOnlyBlock(saved.phase && saved.phase.blocks, block.old_text);
      const undo = back ? async () => ctx.api.atelierBloc(slug, back.id, block.text, { fp: back.fp, prev: back.prev, next: back.next, twins: back.twins, text: back.text }) : null;
      setAck(restoredText('Le bloc', planned), { undo });
      hooks.say('Bloc restauré (non commité).');
      if (hooks.reload) await hooks.reload('Bloc restauré (non commité).', 'ok');
      if (guard && hooks.guarded) hooks.guarded(guard);
      await refreshCompare();
    } catch (cause) {
      await failure(cause, 'Le bloc n’a pas été restauré');
    }
  };

  const phaseView = (phase, data) => {
    const section = el('section', 'at-cmp-phase', null, { 'data-phase': phase.id, 'aria-label': `${phaseName(phase)} : ${phase.added} ajoutée${phase.added > 1 ? 's' : ''}, ${phase.removed} retirée${phase.removed > 1 ? 's' : ''}` });
    section.append(el('h3', 'at-cmp-h', `${phaseName(phase)}  +${phase.added} −${phase.removed}`));
    if (phase.note) section.append(el('p', 'at-muted', phase.note));
    if (data.restorable) {
      const actions = el('div', 'at-cmp-actions');
      const why = restoreNote(phase);
      const whole = el('button', 'at-btn', 'Restaurer cette phase', { type: 'button', 'data-role': 'hist-restaurer-phase' });
      if (why) {
        whole.disabled = true;
        whole.setAttribute('aria-disabled', 'true');
        actions.append(whole, el('span', 'at-muted at-cmp-why', why, { 'data-role': 'hist-refus' }));
      } else {
        whole.addEventListener('click', () => restorePhase(data, phase));
        actions.append(whole);
      }
      section.append(actions);
    }
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
    const blocks = data.restorable && !restoreNote(phase) ? phase.blocks || [] : [];
    if (blocks.length) {
      const group = el('details', 'at-cmp-blocks', null, { open: '', 'data-role': 'hist-blocs' });
      group.append(el('summary', null, `Blocs à restaurer un par un (${blocks.length})`));
      for (const block of blocks) {
        const item = el('div', 'at-cmp-block', null, { 'data-block': block.id });
        item.append(el('p', 'at-muted at-cmp-block-h', `Ligne ${block.line} — texte de la version choisie :`), el('pre', 'at-cmp-old', block.old_text));
        const one = el('button', 'at-btn', 'Restaurer ce bloc', { type: 'button', 'data-role': 'hist-restaurer-bloc' });
        one.addEventListener('click', () => restoreBlock(data, phase, block));
        item.append(one);
        group.append(item);
      }
      section.append(group);
    }
    return section;
  };

  /** `heading` : `{ strong, rest }`, le bandeau. `pair` : `{ from, to }` des identifiants demandés (pour relire). */
  const showCompare = (heading, data, pair) => {
    leaveCompare();
    const section = el('section', 'at-cmp', null, { 'aria-label': 'Comparaison de versions', 'data-role': 'hist-comparaison' });
    const back = el('button', 'at-btn pri at-cmp-back', 'Revenir au document', { type: 'button', 'data-role': 'hist-retour' });
    back.addEventListener('click', () => { leaveCompare({ focus: true }); button.focus({ preventScroll: true }); });
    const banner = el('div', 'at-cmp-banner', null, { role: 'status', 'data-role': 'hist-bandeau' });
    const text = el('span', 'at-cmp-banner-text');
    text.append(el('strong', null, heading.strong), ` ${heading.rest} `);
    banner.append(text, back);
    section.append(banner);
    if (!data.phases.length) section.append(el('p', 'at-muted', 'Aucune ligne ne diffère dans les phases de ce chantier.', { 'data-role': 'hist-vide' }));
    for (const phase of data.phases) section.append(phaseView(phase, data));
    if (data.truncated) section.append(el('p', 'at-muted', 'Comparaison tronquée : le document est trop long pour tout afficher ici.', { 'data-role': 'hist-tronque' }));
    hooks.sheet.hidden = true;
    hooks.main.insertBefore(section, hooks.sheet);
    compare = section;
    shown = { heading, pair };
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
      showCompare({ strong: compareLabel(version), rest: `${versionTitle(version)} — ${versionSummary(data)}.` }, data, null);
      current = version.id;
    } catch (cause) {
      if (ctx.signal.aborted || ticket !== requested) return;
      row.removeAttribute('aria-current');
      hooks.say(`Impossible de lire cette version : ${cause.message || cause}`);
    }
  };

  /** Compare deux versions quelconques (`from` la plus ancienne ; `to` peut être `working`). */
  const comparePair = async (from, to, row) => {
    const ticket = ++requested;
    for (const other of body.querySelectorAll('[aria-current="true"]')) other.removeAttribute('aria-current');
    if (row) row.setAttribute('aria-current', 'true');
    try {
      const data = await ctx.api.atelierCompare(slug, from, to);
      if (ctx.signal.aborted || ticket !== requested || !open()) return;
      showCompare({ strong: pairLabel(data.from, data.to), rest: `${versionSummary(data)}.` }, data, { from, to });
      current = from;
    } catch (cause) {
      if (ctx.signal.aborted || ticket !== requested) return;
      if (row) row.removeAttribute('aria-current');
      hooks.say(`Impossible de comparer ces versions : ${cause.message || cause}`);
    }
  };

  async function refreshCompare() {
    if (!shown || !shown.pair || !open()) return;
    const { from, to } = shown.pair;
    await comparePair(from, to, null);
  }

  // ── Sélection de deux versions ──────────────────────────────────────────────────────────────────────────

  const syncSelection = () => {
    for (const [id, box] of checks) {
      box.checked = selected.includes(id);
      box.closest('.at-hist-item')?.classList.toggle('is-picked', selected.includes(id));
    }
    pairBar.hidden = selected.length === 0;
    pairGo.disabled = selected.length !== 2;
    pairText.textContent = selected.length === 2 ? '2 versions choisies.' : 'Choisis une deuxième version à comparer.';
  };

  const choose = (id) => { selected = toggleSelection(selected, id); syncSelection(); };

  pairGo.addEventListener('click', () => {
    const pair = orderPair(versions, selected[0], selected[1]);
    if (pair) comparePair(pair[0], pair[1], null);
  });
  pairClear.addEventListener('click', () => { selected = []; syncSelection(); });

  // ── Liste ───────────────────────────────────────────────────────────────────────────────────────────────

  const draw = (data) => {
    body.replaceChildren();
    checks.clear();
    selected = selected.filter((id) => versions.some((v) => v.id === id));
    if (!versions.length) {
      body.append(el('p', 'at-muted', emptyText(data), { 'data-role': 'hist-vide', role: 'status' }));
      syncSelection();
      return;
    }
    const list = el('div', 'at-hist-list');
    const buttons = [];
    for (const group of groupByDay(versions)) {
      const section = el('section', 'at-hist-day', null, { 'aria-label': group.label });
      section.append(el('h3', 'at-hist-day-h', group.label));
      for (const version of group.items) {
        const item = el('div', 'at-hist-item');
        const row = el('button', 'at-hist-row', null, { type: 'button', 'data-version': version.id, 'data-kind': version.kind });
        if (version.id === current) row.setAttribute('aria-current', 'true');
        const when = version.kind === 'working' ? '' : timeLabel(version.date);
        row.append(
          el('span', 'at-hist-subject', versionTitle(version)),
          el('span', 'at-hist-sum', versionSummary(version)),
          el('span', 'at-muted at-hist-who', [when, version.author].filter(Boolean).join(' · ')),
        );
        row.addEventListener('click', (event) => {
          if (event.shiftKey) { event.preventDefault(); choose(version.id); return; } // Maj+clic : choisir pour comparer
          pick(version, row);
        });
        buttons.push(row);
        const tools = el('div', 'at-hist-tools');
        const label = el('label', 'at-hist-choose');
        const box = el('input', null, null, { type: 'checkbox', 'data-role': 'hist-choisir', 'aria-label': `Choisir « ${versionTitle(version)} » pour la comparer à une autre version` });
        box.addEventListener('change', () => choose(version.id));
        checks.set(version.id, box);
        label.append(box, 'Choisir');
        tools.append(label);
        if (version.kind !== 'working') {
          const vs = el('button', 'at-btn ghost at-hist-vs', 'Comparer avec la version actuelle', { type: 'button', 'data-role': 'hist-vs-actuelle' });
          vs.addEventListener('click', () => comparePair(version.id, 'working', row));
          tools.append(vs);
        }
        item.append(row, tools);
        section.append(item);
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
    syncSelection();
    if (data.has_more) {
      const more = el('button', 'at-btn ghost', 'Versions plus anciennes', { type: 'button', 'data-role': 'hist-plus' });
      more.addEventListener('click', () => load(page + 1));
      body.append(more);
    }
  };

  async function load(target) {
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
  }

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
      selected = [];
      button.focus({ preventScroll: true });
    }
  };

  button.addEventListener('click', () => setOpen(!open()));
  close.addEventListener('click', () => setOpen(false));
  const onKey = (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented || !open()) return;
    if (element.querySelector('dialog[open]')) return; // la boîte de confirmation se ferme d'elle-même (Échap = Renoncer)
    if (compare) { event.preventDefault(); leaveCompare({ focus: true }); return; }
    if (element.contains(document.activeElement)) { event.preventDefault(); setOpen(false); }
  };
  document.addEventListener('keydown', onKey);
  ctx.signal.addEventListener('abort', () => document.removeEventListener('keydown', onKey));

  return { button, element, isOpen: open, close: () => setOpen(false), isLoaded: () => loaded };
}
