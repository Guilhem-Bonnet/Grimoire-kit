// Atelier de spec (en test), lot 4 : le document vivant et « Valider et découper ».
//
// Sous chaque exigence REQ-nnn du document, une ligne d'état réel : proposée, validée, en cours, livrée, recette
// prononcée, reprise à faire ; ses tâches, un compteur de fichiers, ses trous, « modifiée depuis la validation ».
// Le détail se replie. La ligne se rafraîchit au pouls du cockpit (`ctx.live`), au retour sur l'onglet et à la demande :
// aucun minuteur propre.
//
// AUTORITÉ : valider une exigence est un geste de la MOA, scellé au journal d'autorité depuis un terminal (tour humain
// attesté). Le cockpit n'a aucun canal qui le prouve : le bouton « Valider » donne donc la commande à copier
// (`grimoire cadrage approve REQ-nnn --chantier <slug>`), et la ligne se met à jour quand la MOA l'a lancée.
// « Valider et découper » ne crée que des tâches (aucun travail lancé) : aperçu d'abord, puis confirmation.
// Données : `GET atelier/chantiers/<slug>/trace`, `GET|POST atelier/chantiers/<slug>/decoupage`
// (voir `src/grimoire/tools/workspace_atelier_trace.py`).

import { badge, icon } from '../shell.js';
import { diffRows, driftNote } from './atelier-logic.js';
import { moaHelp, validationHelp } from './atelier-avancement.js';

const TONE = { proposee: '', validee: 'acc', 'en-cours': 'acc', livree: 'ok', recette: 'ok', reprise: 'warn' };
const TASK_STATUS = {
  proposed: 'proposée', ready: 'prête', claimed: 'réservée', running: 'en cours', waiting: 'en attente',
  blocked: 'bloquée', needs_verification: 'à vérifier', failed: 'en échec', closed: 'fermée', cancelled: 'annulée',
};

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function day(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' }) : '';
}

/** Copie dans le presse-papiers ; faux si le navigateur refuse (la commande reste affichée et sélectionnable). */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** La commande à lancer au terminal, et le geste de copie. */
function commandBlock(row, say) {
  const code = el('code', 'at-live-cmd', row.command, { 'data-role': 'approve-command' });
  const copy = el('button', 'at-btn', null, { type: 'button', 'data-act': 'copy-command' });
  copy.append(icon('i-act-copy'), 'Copier la commande');
  copy.addEventListener('click', async () => {
    if (await copyText(row.command)) {
      say(`Commande copiée : lance-la dans un terminal ouvert par toi, hors d’une session d’agent ; ${row.id} se mettra à jour ici ensuite.`, 'ok');
      copy.dispatchEvent(new CustomEvent('at-awaiting-validation', { bubbles: true })); // la page relit l'état quelque temps
    } else {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      say('Copie impossible ici : la commande est sélectionnée, copie-la à la main.', 'warn');
    }
  });
  const wrap = el('div', 'at-live-do', null, { 'data-role': 'approve' });
  wrap.append(
    validationHelp(), // où lancer la commande (son propre terminal, hors agent) et ce que l'écran affichera ensuite
    code, copy,
  );
  return wrap;
}

function detail(row, say) {
  const box = el('div', 'at-live-detail', null, { 'data-role': 'live-detail' });
  if (row.detail === false) {
    box.append(el('p', 'at-muted', 'Chargement du détail…', { role: 'status' })); // chantier long : détail à la demande
  } else if (row.tasks.length) {
    const list = el('ul', 'at-live-tasks');
    for (const task of row.tasks) {
      const item = el('li');
      item.append(el('code', null, task.id), ` ${task.title} `, badge(TASK_STATUS[task.status] || task.status, task.delivered ? 'ok' : ''));
      list.append(item);
    }
    box.append(list, el('p', 'at-muted', `${plural(row.files, 'fichier touché', 'fichiers touchés')}, dont ${row.tests} de test.`));
  } else {
    box.append(el('p', 'at-muted', 'Aucune tâche ne sert cette exigence.'));
  }
  for (const gap of row.gaps) box.append(el('p', 'at-live-gap', `${gap.label}${gap.detail ? ` : ${gap.detail}` : ''}`));
  if (row.validated) {
    box.append(el('p', 'at-muted', `Validée par ${row.validated_by || 'la MOA'}${day(row.validated_at) ? ` le ${day(row.validated_at)}` : ''}.`));
  } else if (row.drift) {
    box.append(el('p', 'at-live-gap', `Modifiée depuis la validation (${row.drift.what}) : validée par ${row.drift.validated_by || 'la MOA'}${day(row.drift.validated_at) ? ` le ${day(row.drift.validated_at)}` : ''}, à revalider.`));
    const rows = diffRows(row.drift);
    if (rows.length) { // le diff court « validé → courant » : ce qui a changé depuis la validation
      const diff = el('pre', 'at-live-diff', null, { 'data-role': 'drift-diff', 'aria-label': 'Ce qui a changé depuis la validation' });
      for (const line of rows) diff.append(el('span', `at-diff-${line.kind}`, `${line.text}\n`));
      box.append(diff);
    }
    if (driftNote(row.drift)) box.append(el('p', 'at-muted', driftNote(row.drift), { 'data-role': 'drift-note' }));
  } else if (row.reason) {
    box.append(el('p', 'at-muted', row.reason));
  }
  if (row.command) box.append(commandBlock(row, say));
  return box;
}

/** Une ligne d'état par exigence ; `open` dit si son détail est ouvert, `toggle` retient son choix. */
function liveLine(row, { open, toggle, say, more, comments = 0 }) {
  const node = el('div', 'at-live', null, { 'data-live': row.id, 'data-state': row.state, role: 'group', 'aria-label': `État de ${row.id} : ${row.label}` });
  const line = el('div', 'at-live-row');
  const fold = el('button', 'at-live-toggle', null, { type: 'button', 'aria-expanded': String(open), 'data-act': 'toggle-detail', 'aria-label': `Détail de ${row.id}` });
  fold.append(badge(row.label, TONE[row.state] || ''));
  if (row.drift) fold.append(badge('modifiée depuis la validation', 'warn'));
  fold.append(el('span', 'at-muted', row.task_count ? plural(row.task_count, 'tâche', 'tâches') : 'aucune tâche'));
  if (row.task_count) fold.append(el('span', 'at-muted', plural(row.files, 'fichier', 'fichiers')));
  for (const gap of row.gaps) fold.append(badge(gap.label, 'warn', gap.detail || gap.label));
  if (comments > 0) { // lot 3 : les fils ouverts sur cette exigence, d'un coup d'œil sur sa ligne d'état
    const talk = badge(plural(comments, 'commentaire ouvert', 'commentaires ouverts'), 'acc');
    talk.setAttribute('data-role', 'live-comments');
    fold.append(talk);
  }
  line.append(fold);
  let panel = null;
  const sync = (isOpen) => {
    fold.setAttribute('aria-expanded', String(isOpen));
    if (isOpen && !panel) {
      panel = detail(row, say);
      node.append(panel);
      if (row.detail === false) {
        more(row.id).then((full) => {
          if (!full || !panel) return;
          const fresh = detail(full, say);
          fresh.hidden = panel.hidden;
          panel.replaceWith(fresh);
          panel = fresh;
        });
      }
    }
    if (panel) panel.hidden = !isOpen;
  };
  fold.addEventListener('click', () => { const next = fold.getAttribute('aria-expanded') !== 'true'; toggle(next); sync(next); });
  if (!row.validated) {
    const ask = el('button', 'at-btn', null, { type: 'button', 'data-act': 'validate' });
    ask.append(icon('i-act-approve'), row.revalidate ? 'Revalider' : 'Valider');
    ask.setAttribute('aria-label', `${row.revalidate ? 'Revalider' : 'Valider'} ${row.id} : copier la commande du terminal`);
    ask.addEventListener('click', () => { toggle(true); sync(true); node.querySelector('[data-act="copy-command"]')?.focus(); });
    line.append(ask);
  }
  node.prepend(line);
  sync(open);
  return node;
}

/** Les tâches qui viennent d'être créées : identifiant COMPLET, et un lien vers l'espace Travail. */
function createdNode(ctx, created) {
  const box = el('div', 'at-card at-split-done', null, { 'data-role': 'split-done' });
  box.append(el('p', null, plural(created.length, 'tâche créée', 'tâches créées'), { 'data-role': 'split-done-title' }));
  const list = el('ul', 'at-live-tasks');
  for (const task of created) {
    const li = el('li');
    const link = el('a', 'at-loop-link', task.task_id, { href: `#travail?task=${encodeURIComponent(task.task_id)}`, 'data-act': 'open-task' });
    link.addEventListener('click', (event) => { event.preventDefault(); ctx.goto('travail', { task: task.task_id }); });
    li.append(link, ` ${task.requirement} — ${task.title.replace(/^REQ-\d+\s—\s/, '')}`);
    list.append(li);
  }
  box.append(list);
  return box;
}

function previewPanel(data, { confirm, cancel }) {
  const panel = el('section', 'at-card at-split', null, { 'aria-labelledby': 'at-split-h', 'data-role': 'decoupage' });
  const close = el('button', 'at-btn ghost', 'Fermer', { type: 'button', 'data-act': 'split-cancel' });
  close.addEventListener('click', cancel);
  const head = el('div', 'at-actions');
  head.append(el('h3', null, 'Valider et découper', { id: 'at-split-h' }), close);
  panel.append(head);
  panel.append(el('p', 'at-muted', 'Aperçu : rien n’est écrit tant que tu n’as pas confirmé. Aucun travail n’est lancé : les tâches sont seulement créées.'));
  const group = (title, rows, build) => {
    if (!rows.length) return;
    const list = el('ul', 'at-split-list');
    for (const item of rows) list.append(build(item));
    panel.append(el('h4', null, title), list);
  };
  group(plural(data.create.length, 'tâche sera créée', 'tâches seront créées'), data.create, (t) => {
    const li = el('li', null, null, { 'data-create': t.requirement });
    li.append(el('code', null, t.requirement), ` ${t.title.replace(/^REQ-\d+\s—\s/, '')} `, el('span', 'at-muted', `priorité ${t.priority || 'non précisée'}, vérifiabilité ${t.verifiability}`));
    // Ce qui sera écrit se lit avant de confirmer : le texte des critères, ouvert d'emblée quand ils sont peu nombreux.
    const crit = el('details', 'at-split-crit', null, { 'data-role': 'criteria' });
    if (t.criteria.length <= 3) crit.open = true;
    crit.append(el('summary', 'at-muted', plural(t.criteria.length, 'critère repris', 'critères repris')));
    const items = el('ul');
    for (const text of t.criteria) items.append(el('li', null, text));
    crit.append(items);
    li.append(crit);
    if ((t.already_linked || []).length) {
      li.append(el('span', 'at-live-gap', ` Déjà liée à ${t.already_linked.join(', ')} (ajoutée à la main) : une tâche de plus sera créée.`));
    }
    return li;
  });
  group('Déjà couvertes (rien à recréer)', data.existing, (t) => {
    const li = el('li', null, null, { 'data-existing': t.requirement });
    li.append(el('code', null, t.requirement), ` ${t.tasks.join(', ')}`);
    return li;
  });
  group('Ignorées', data.skipped, (s) => {
    const li = el('li', null, null, { 'data-skipped': s.id });
    li.append(el('code', null, s.id), ` ${s.why}`);
    return li;
  });
  group('Questions qui seront posées', data.questions, (q) => el('li', null, `${q.requirement} : ${q.text}${q.blocking ? ' (bloquante)' : ''}`));
  if (!data.create.length) panel.append(el('p', 'at-muted', 'Rien à créer : chaque exigence planifiable a déjà sa tâche.'));
  const bar = el('div', 'at-edit-bar');
  const go = el('button', 'at-btn pri', null, { type: 'button', 'data-act': 'split-confirm' });
  go.append(icon('i-act-launch'), data.create.length ? `Créer ${plural(data.create.length, 'tâche', 'tâches')}` : 'Poser les questions');
  go.disabled = !data.create.length && !data.questions.length;
  go.addEventListener('click', () => confirm(go));
  bar.append(go);
  panel.append(bar);
  return panel;
}

/**
 * Le document vivant d'un chantier. `sheet` : le conteneur du document, où chercher les lignes ; `status` : la ligne
 * de statut du document. Rend `{ line(req), toolbar, refresh }` : `line` pour chaque bloc d'exigence au rendu,
 * `toolbar` à poser dans l'en-tête, `refresh` pour relire l'état.
 */
export function createLiveDoc(ctx, slug, { sheet, status, comments = () => 0, onTrace = () => {}, onSplit = async () => {} }) {
  const commentsOf = (req) => comments(req) || 0; // lot 3 : fils ouverts sur l'exigence, comptés par le document
  const state = { trace: null, open: new Set() };
  // `kind` (ok, warn, error) donne son type à la notification ; sans lui, il se déduit du texte.
  const say = (text, kind) => { if (kind && text) status.setAttribute('data-kind', kind); status.textContent = text; };
  const toolbar = el('div', 'at-actions at-live-bar', null, { 'data-role': 'live-bar' });
  const summary = el('span', 'at-muted', '', { 'data-role': 'live-summary' });
  // Désactivé = `aria-disabled` (le bouton reste atteignable au clavier et au survol, et dit pourquoi), jamais `disabled`.
  const split = el('button', 'at-btn pri', null, { type: 'button', 'data-act': 'split', 'aria-disabled': 'true', 'aria-describedby': 'at-split-why' });
  split.append(icon('i-act-launch'), 'Valider et découper');
  const why = el('span', 'at-tip', '', { id: 'at-split-why', role: 'tooltip', 'data-role': 'split-why' });
  const splitWrap = el('span', 'at-split-wrap');
  splitWrap.append(split, why);
  const lockSplit = (locked, reason = '') => {
    split.setAttribute('aria-disabled', String(locked));
    why.textContent = locked ? reason : '';
    split.title = locked ? reason : '';
  };
  lockSplit(true, 'État des exigences en cours de lecture.');
  const refreshButton = el('button', 'at-btn ghost', null, { type: 'button', 'data-act': 'live-refresh', 'aria-label': 'Actualiser l’état des exigences' });
  refreshButton.append(icon('i-act-retry'), 'Actualiser');
  const slot = el('div', null, null, { 'data-role': 'decoupage-slot' });
  const moaSlot = el('div', null, null, { 'data-role': 'moa-slot' }); // comment déclarer la MOA, quand elle ne l'est pas
  toolbar.append(splitWrap, summary, refreshButton);

  /** Le détail complet d'une exigence, pour un chantier dont les lignes sont allégées. */
  const more = async (req) => {
    try {
      const full = await ctx.api.atelierTrace(slug, req);
      return ctx.signal.aborted ? null : full.requirements.find((r) => r.id === req) || null;
    } catch {
      return null;
    }
  };
  const rowOf = (req) => (state.trace ? state.trace.requirements.find((r) => r.id === req) : null);
  const line = (req) => {
    const row = rowOf(req);
    if (!row) return el('div', 'at-live', null, { 'data-live': req, hidden: '' }); // pas (encore) dans la trace
    return liveLine(row, {
      open: state.open.has(req), toggle: (on) => { if (on) state.open.add(req); else state.open.delete(req); }, say, more,
      comments: commentsOf(req),
    });
  };

  const paint = () => {
    for (const node of sheet.querySelectorAll('[data-live]')) {
      node.replaceWith(line(node.getAttribute('data-live')));
    }
    const trace = state.trace;
    moaSlot.replaceChildren(...(trace && !trace.moa.declared ? [moaHelp(trace.moa, say)] : []));
    onTrace(trace);
    if (!trace || !trace.requirements.length) {
      summary.textContent = trace ? 'Aucune exigence identifiée : écris-les en « REQ-001 : … » pour les suivre.' : '';
      lockSplit(true, summary.textContent || 'État des exigences en cours de lecture.');
      return;
    }
    const { must, must_validated: done } = trace.summary;
    const missing = trace.requirements.filter((r) => r.priority === 'must' && !r.validated).map((r) => r.id);
    summary.textContent = missing.length
      ? `${plural(done, 'exigence Must validée', 'exigences Must validées')} sur ${must} : à valider d’abord ${missing.join(', ')}.`
      : (must ? (must > 1 ? `Les ${must} exigences Must sont validées.` : 'L’exigence Must est validée.') : 'Aucune exigence Must : le découpage est possible.');
    if (!trace.moa.declared) summary.textContent += ' Aucune MOA déclarée : voir ci-dessous comment la déclarer.';
    lockSplit(missing.length > 0, `Valide d’abord les exigences Must : ${missing.join(', ')}.`);
  };

  let busy = false;
  let again = false;
  const refresh = async () => {
    if (busy) { again = true; return; } // un état plus récent est demandé : relu dès que la lecture en cours finit
    busy = true;
    try {
      do {
        again = false;
        const trace = await ctx.api.atelierTrace(slug);
        if (ctx.signal.aborted) return;
        state.trace = trace;
        paint();
      } while (again);
    } catch (cause) {
      if (!ctx.signal.aborted) summary.textContent = `État des exigences indisponible : ${cause.message || cause}`;
    } finally {
      busy = false;
    }
  };

  const closeSplit = () => { slot.replaceChildren(); split.focus(); };
  const openSplit = async (message) => {
    lockSplit(true, 'Aperçu du découpage en cours.');
    try {
      const data = await ctx.api.atelierDecoupage(slug);
      if (ctx.signal.aborted) return;
      if (!data.ready) {
        say(`Valide d’abord les exigences Must : ${data.blocked_by.map((b) => b.id).join(', ')}.`, 'warn');
        slot.replaceChildren(...(data.moa && !data.moa.declared ? [moaHelp(data.moa, say)] : []));
        return;
      }
      const confirm = async (go) => {
        go.disabled = true;
        try {
          const done = await ctx.api.atelierDecouper(slug, data.digest);
          if (ctx.signal.aborted) return;
          slot.replaceChildren(...(done.created.length ? [createdNode(ctx, done.created)] : []));
          const message = done.created.length
            ? `${plural(done.created.length, 'tâche créée', 'tâches créées')}. Aucun travail n’est lancé : ouvre-les dans l’espace Travail.`
            : 'Rien de nouveau à créer : le découpage était déjà fait.';
          say(message, 'ok');
          await refresh();
          await onSplit(message); // la page du chantier relit son état et ses tâches
        } catch (cause) {
          if (ctx.signal.aborted) return;
          if (cause.code === 409) {
            const why = cause.payload && cause.payload.reason;
            await openSplit(why === 'apercu-perime' ? 'Le plan a changé depuis l’aperçu : relis-le avant de confirmer.' : `Refusé : ${cause.message}`);
            await refresh();
            return;
          }
          say(`Découpage impossible : ${cause.message || cause}`, 'error');
          go.disabled = false;
        }
      };
      slot.replaceChildren(previewPanel(data, { confirm, cancel: closeSplit }));
      if (message) say(message);
      slot.querySelector('[data-act="split-confirm"]')?.focus();
    } catch (cause) {
      if (!ctx.signal.aborted) say(`Aperçu impossible : ${cause.message || cause}`, 'error');
    } finally {
      if (!ctx.signal.aborted) paint();
    }
  };
  split.addEventListener('click', () => {
    // Désactivé, le bouton explique au lieu de rester muet.
    if (split.getAttribute('aria-disabled') === 'true') { if (why.textContent) say(why.textContent); return; }
    openSplit();
  });
  refreshButton.addEventListener('click', () => { say(''); refresh(); });

  // Le pouls du cockpit annonce un changement du ledger ; au retour sur l'onglet on relit aussi (la MOA vient peut-être
  // de valider au terminal, ce que le pouls ne voit pas). Aucun minuteur propre.
  ctx.live.on(() => refresh(), { scope: () => 'project' });
  const onVisible = () => { if (!document.hidden) refresh(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', onVisible);
  ctx.signal.addEventListener('abort', () => {
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
  });
  // Une commande de validation vient d'être copiée : la MOA la lance au terminal. Le pouls ne voit pas toujours le journal
  // d'autorité tout de suite ; on relit donc l'état toutes les quatre secondes, onglet visible, jusqu'à six minutes au plus.
  let watching = null;
  sheet.addEventListener('at-awaiting-validation', () => {
    if (watching) return;
    let left = 90;
    watching = setInterval(async () => {
      left -= 1;
      if (ctx.signal.aborted || left <= 0 || (state.trace && state.trace.requirements.every((r) => r.validated))) {
        clearInterval(watching);
        watching = null;
        return;
      }
      if (!document.hidden) await refresh();
    }, 4000);
    ctx.signal.addEventListener('abort', () => clearInterval(watching));
  });
  const wrap = el('div');
  wrap.append(toolbar, moaSlot, slot);
  return { line, toolbar: wrap, refresh };
}
