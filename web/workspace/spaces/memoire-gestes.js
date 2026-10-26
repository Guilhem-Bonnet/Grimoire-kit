// Mémoire › correction directe d'une entrée (P34, maquette « Historique — recherche mémoire »).
//
// Le panneau de détail porte « Corriger », « Marquer remplacée… » et
// « Supprimer… » (ce dernier reste dans `memoire.js`). Chaque geste s'ouvre
// DANS le panneau, jamais dans une fenêtre native, et suit deux temps :
//
// 1. « Voir l'aperçu » demande au serveur l'avant/après de chaque champ touché
//    et la version des entrées en jeu (`POST /api/memory`, `action: "gesture"`,
//    `mode: "preview"`) ; rien n'est écrit ;
// 2. « Appliquer » renvoie cette version (`mode: "apply"`, `confirm: true`).
//    Si l'entrée a changé entre-temps, le serveur refuse (409) et rend
//    l'aperçu recalculé : il remplace l'ancien, le refus est dit sur place.
//
// Une raison est exigée pour remplacer, retirer et fusionner. Un refus du
// serveur s'affiche dans le panneau (`actionStatus`), jamais seulement dans le
// dock. L'historique de l'entrée (journal des écritures P14) liste ses
// écritures ; celles qui se défont portent « Annuler… », qui ouvre le même
// panneau d'aperçu.
//
// Gestes : tags, source, merge, retract (onglets de « Corriger »), supersede,
// unretract, undo. API consommée : api.memoryGesture.

import { actionStatus, failureText, icon, setActionStatus } from '../shell.js';

export const GESTURE_STYLES = `
.kn-gesture { display: flex; flex-direction: column; gap: 12px; padding: 14px; border: 1px solid var(--line);
  border-radius: var(--r-l); background: var(--panel); }
.kn-gesture h3 { margin: 0; font-size: 14px; line-height: 20px; font-weight: 600; }
.kn-gesture .seg { align-self: flex-start; }
.kn-g-form { display: flex; flex-direction: column; gap: 10px; }
.kn-g-form label, .kn-g-form fieldset { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.kn-g-form fieldset { border: 0; margin: 0; padding: 0; }
.kn-g-form legend, .kn-g-lbl { padding: 0; font-size: 12px; line-height: 16px; font-weight: 500; color: var(--ink3); }
.kn-g-form input[type="text"], .kn-g-form select, .kn-g-form textarea { width: 100%; min-height: var(--h-ctl); padding: 4px 9px;
  border: 1px solid var(--line); border-radius: var(--r); font: inherit; }
.kn-g-form textarea { min-height: 88px; resize: vertical; }
.kn-g-pair { display: grid; grid-template-columns: minmax(0, 1fr) 84px; gap: 8px; }
.kn-g-pick { display: flex; flex-direction: row !important; align-items: flex-start; gap: 8px; cursor: pointer; }
.kn-g-pick input { margin: 3px 0 0; flex: none; accent-color: var(--acc); }
.kn-g-pick span { overflow-wrap: anywhere; }
.kn-g-empty { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.kn-diff { display: flex; flex-direction: column; gap: 10px; }
.kn-diff-entry { margin: 0; font-size: 12px; font-weight: 600; color: var(--ink2); overflow-wrap: anywhere; }
.kn-diff-row { display: flex; flex-direction: column; gap: 4px; }
.kn-diff-field { font-size: 12px; font-weight: 500; color: var(--ink3); }
.kn-diff-cells { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px; }
.kn-diff-cells > div { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; border-radius: var(--r);
  font-size: var(--t-s); overflow-wrap: anywhere; color: var(--ink); }
.kn-diff-before { background: var(--hover); }
.kn-diff-after { background: var(--ok-soft); }
.kn-diff-cells b { font-size: 11px; font-weight: 500; color: var(--ink2); }
.kn-g-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.kn-hist { display: flex; flex-direction: column; gap: 6px; }
.kn-hist-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
.kn-hist-list li { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; border-top: 1px solid var(--line); }
.kn-hist-list li:first-child { border-top: 0; }
.kn-hist-text { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; font-size: var(--t-s); }
.kn-hist-text time { color: var(--ink3); }
.kn-hist-list .btn { flex: none; }
`;

const TITLES = {
  correct: 'Corriger',
  supersede: 'Marquer remplacée',
  unretract: 'Rendre au service',
  undo: 'Annuler une écriture',
};
const TABS = [
  { id: 'tags', label: 'Étiquettes' },
  { id: 'source', label: 'Source' },
  { id: 'merge', label: 'Fusionner' },
  { id: 'retract', label: 'Retirer' },
];
const REASON_REQUIRED = new Set(['supersede', 'retract', 'merge']);
const SOURCE_KINDS = [
  { id: 'task', label: 'Tâche', placeholder: 'GAO-…' },
  { id: 'session', label: 'Session', placeholder: 'identifiant de session' },
  { id: 'file', label: 'Fichier et ligne', placeholder: 'chemin/relatif/au/projet.py' },
  { id: 'commit', label: 'Commit', placeholder: 'empreinte du commit (7 à 40 caractères)' },
];
const WHEN = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function button(label, { pri = false, iconName = '' } = {}) {
  const node = el('button', pri ? 'btn pri' : 'btn', label, { type: 'button' });
  if (iconName) node.prepend(icon(iconName));
  return node;
}

function entryTitle(entry) {
  return entry.title || entry.text || entry.id;
}

// ── Historique de l'entrée (journal des écritures P14) ─────────────────────

function opLabel(row, entryId) {
  if (row.op === 'store') return 'écrite';
  if (row.op === 'delete') return 'supprimée';
  if (row.op === 'retract') return 'retirée du service';
  if (row.op === 'unretract') return 'rendue au service';
  if (row.op === 'supersede') return row.before_id === entryId ? 'marquée remplacée' : 'remplace une autre entrée';
  if (row.op === 'update_tags') {
    const parts = [...(row.tags_added || []).map((t) => `+${t}`), ...(row.tags_removed || []).map((t) => `−${t}`)];
    return parts.length ? `étiquettes ${parts.join(' ')}` : 'étiquettes';
  }
  if (row.op === 'update') {
    if (row.gesture === 'merge') return 'fusion : survivante mise à jour';
    if (row.gesture === 'source') return 'source rattachée';
    if (row.gesture === 'undo') return 'écriture annulée';
    return 'modifiée';
  }
  return row.op || 'écriture';
}

/** La liste des écritures, la plus récente d'abord ; « Annuler… » sur celles qui se défont. */
export function historyList(entryId, rows, { canEdit, onUndo }) {
  if (!rows || !rows.length) return null;
  const box = el('section', 'kn-hist', null, { 'aria-label': 'Historique de l’entrée' });
  box.append(el('p', 'kn-eyebrow', 'Historique'));
  const list = el('ol', 'kn-hist-list');
  for (const row of [...rows].reverse()) {
    const item = el('li');
    item.dataset.op = row.op || '';
    const text = el('span', 'kn-hist-text');
    const when = new Date(row.ts || '');
    const time = el('time', null, Number.isNaN(when.getTime()) ? String(row.ts || '—') : WHEN.format(when));
    if (row.ts) time.setAttribute('datetime', String(row.ts));
    text.append(time, ` · ${opLabel(row, entryId)}`);
    if (row.actor) text.append(` · par ${row.actor}`);
    if (row.reason) text.append(` — ${row.reason}`);
    item.append(text);
    if (canEdit && row.undoable) {
      const undo = button('Annuler…', { iconName: 'undo' });
      undo.classList.add('kn-undo');
      undo.addEventListener('click', () => onUndo(row));
      item.append(undo);
    }
    list.append(item);
  }
  box.append(list);
  return box;
}

// ── Le panneau d'un geste ───────────────────────────────────────────────────

/**
 * Ouvre un geste à la place de `anchor` (la rangée d'actions) dans le panneau.
 * `kind` : correct (onglets), supersede, unretract ou undo (`row` requis).
 * `onDone(message)` est appelé après une application réussie.
 */
export function openGesture(ctx, { entry, kind, row, candidates, project, anchor, onDone }) {
  const panel = el('section', 'kn-gesture', null, { 'aria-label': TITLES[kind] || 'Corriger' });
  const title = el('h3', null, TITLES[kind] || 'Corriger');
  const tabs = el('div', 'seg', null, { role: 'group', 'aria-label': 'Geste' });
  const form = el('div', 'kn-g-form');
  const previewBox = el('div');
  const status = actionStatus('', '');
  const buttons = el('div', 'kn-actions');
  let tab = kind === 'correct' ? 'tags' : kind;
  let preview = null;
  let busy = false;
  let fields = {};

  const gesture = () => tab;
  const reasonValue = () => (fields.reason ? fields.reason.value.trim() : '');

  const close = () => panel.replaceWith(anchor);

  const invalidate = () => {
    if (!preview) return;
    preview = null;
    previewBox.replaceChildren();
    drawButtons();
  };

  // `stale` : changer ce champ périme l'aperçu (la raison, elle, n'y entre pas).
  const input = (labelText, value, attrs = {}, { stale = true } = {}) => {
    const label = el('label');
    const field = el('input', null, null, { type: 'text', autocomplete: 'off', ...attrs });
    field.value = value || '';
    if (stale) field.addEventListener('input', invalidate);
    label.append(el('span', 'kn-g-lbl', labelText), field);
    return { label, field };
  };

  const reasonField = () => {
    const required = REASON_REQUIRED.has(gesture());
    const { label, field } = input(required ? 'Raison (obligatoire)' : 'Raison (facultative)', '', { name: 'reason' }, { stale: false });
    if (required) field.setAttribute('aria-required', 'true');
    fields.reason = field;
    return label;
  };

  const emptyNote = (text) => {
    fields.blocked = true;
    return el('p', 'kn-g-empty', text);
  };

  const drawForm = () => {
    fields = {};
    form.replaceChildren();
    const g = gesture();
    if (g === 'tags') {
      const { label, field } = input('Étiquettes, séparées par des virgules', (entry.tags || []).join(', '), { name: 'tags' });
      fields.tags = field;
      form.append(label);
    } else if (g === 'source') {
      const kindLabel = el('label');
      const select = el('select', null, null, { name: 'source-kind' });
      for (const k of SOURCE_KINDS) select.append(el('option', null, k.label, { value: k.id }));
      kindLabel.append(el('span', 'kn-g-lbl', 'Type de source'), select);
      const pair = el('div', 'kn-g-pair');
      const value = input('Référence', '', { name: 'source-value' });
      const line = input('Ligne', '', { name: 'source-line', inputmode: 'numeric' });
      pair.append(value.label, line.label);
      const sync = () => {
        const k = SOURCE_KINDS.find((s) => s.id === select.value) || SOURCE_KINDS[0];
        value.field.placeholder = k.placeholder;
        line.label.hidden = k.id !== 'file';
        pair.style.gridTemplateColumns = k.id === 'file' ? '' : 'minmax(0, 1fr)';
      };
      select.addEventListener('change', () => { sync(); invalidate(); });
      sync();
      Object.assign(fields, { kind: select, value: value.field, line: line.field });
      form.append(kindLabel, pair);
    } else if (g === 'merge') {
      if (!candidates.length) {
        form.append(emptyNote('Aucune autre entrée en vigueur de ce projet dans la liste : cherche les doublons d’abord, puis reviens ici.'));
        return;
      }
      const set = el('fieldset');
      set.append(el('legend', null, 'Entrées à fusionner dans celle-ci (elles seront marquées remplacées)'));
      fields.others = [];
      for (const other of candidates) {
        const pick = el('label', 'kn-g-pick');
        const box = el('input', null, null, { type: 'checkbox', value: other.id });
        box.addEventListener('change', invalidate);
        pick.append(box, el('span', null, entryTitle(other)));
        fields.others.push(box);
        set.append(pick);
      }
      const textLabel = el('label');
      const text = el('textarea', null, null, { name: 'survivor-text' });
      text.value = entry.text || '';
      text.addEventListener('input', invalidate);
      textLabel.append(el('span', 'kn-g-lbl', 'Texte de l’entrée qui reste'), text);
      fields.text = text;
      form.append(set, textLabel);
    } else if (g === 'supersede') {
      if (!candidates.length) {
        form.append(emptyNote('Aucune autre entrée en vigueur de ce projet dans la liste : cherche la remplaçante d’abord, puis reviens ici.'));
        return;
      }
      const label = el('label');
      const select = el('select', null, null, { name: 'replacement' });
      for (const other of candidates) select.append(el('option', null, entryTitle(other), { value: other.id }));
      select.addEventListener('change', invalidate);
      label.append(el('span', 'kn-g-lbl', 'Remplacée par'), select);
      fields.by = select;
      form.append(label);
    } else if (g === 'retract') {
      form.append(el('p', 'kn-g-note', 'L’entrée n’est plus servie aux agents. Elle n’est pas effacée : elle reste lisible ici et se rétablit depuis son historique.'));
    }
    if (g !== 'undo') form.append(reasonField());
  };

  const params = () => {
    const g = gesture();
    if (g === 'tags') return { tags: fields.tags.value };
    if (g === 'source') return { kind: fields.kind.value, value: fields.value.value.trim(), line: fields.line.value.trim() };
    if (g === 'merge') return { others: fields.others.filter((b) => b.checked).map((b) => b.value), text: fields.text.value };
    if (g === 'supersede') return { by: fields.by.value };
    if (g === 'undo') return { row };
    return {};
  };

  const drawPreview = (payload) => {
    const box = el('div', 'kn-diff', null, { role: 'group', 'aria-label': 'Aperçu avant / après' });
    box.dataset.version = payload.version || '';
    box.append(el('p', 'kn-eyebrow', 'Aperçu avant / après'));
    const several = new Set((payload.changes || []).map((c) => c.entryId)).size > 1;
    let lastEntry = null;
    for (const change of payload.changes || []) {
      if (several && change.entryId !== lastEntry) {
        box.append(el('p', 'kn-diff-entry', change.entry));
        lastEntry = change.entryId;
      }
      const line = el('div', 'kn-diff-row');
      line.dataset.field = change.field;
      line.dataset.entry = change.entryId;
      const cells = el('div', 'kn-diff-cells');
      const before = el('div', 'kn-diff-before');
      before.append(el('b', null, 'Avant'), el('span', null, change.before));
      const after = el('div', 'kn-diff-after');
      after.append(el('b', null, 'Après'), el('span', null, change.after));
      cells.append(before, after);
      line.append(el('span', 'kn-diff-field', change.field), cells);
      box.append(line);
    }
    if (payload.note) box.append(el('p', 'kn-g-note', payload.note));
    previewBox.replaceChildren(box);
  };

  const request = (body) => ctx.api.memoryGesture({ gesture: gesture(), id: entry.id, params: params(), ...body }, project);

  const askPreview = async () => {
    busy = true;
    drawButtons();
    setActionStatus(status, '');
    try {
      preview = await request({ mode: 'preview' });
      drawPreview(preview);
    } catch (error) {
      preview = null;
      previewBox.replaceChildren();
      setActionStatus(status, failureText(error), 'bad');
    }
    busy = false;
    if (!ctx.signal.aborted) drawButtons();
  };

  const apply = async () => {
    if (REASON_REQUIRED.has(gesture()) && !reasonValue()) {
      setActionStatus(status, 'Refusé : donne une raison, elle est exigée pour remplacer, retirer ou fusionner.', 'bad');
      fields.reason.focus();
      return;
    }
    busy = true;
    drawButtons();
    setActionStatus(status, 'Application…');
    try {
      const done = await request({ mode: 'apply', version: preview.version, reason: reasonValue(), confirm: true });
      if (ctx.signal.aborted) return;
      await onDone(done.message || 'Geste appliqué.');
      return;
    } catch (error) {
      const fresh = error && error.payload && error.payload.stale ? error.payload.preview : null;
      if (fresh) {
        preview = fresh;
        drawPreview(fresh);
        setActionStatus(status, `Refusé : ${error.message} Relis le nouvel aperçu avant d’appliquer.`, 'bad');
      } else {
        setActionStatus(status, failureText(error), 'bad');
      }
    }
    busy = false;
    if (!ctx.signal.aborted) drawButtons();
  };

  function drawButtons() {
    buttons.replaceChildren();
    if (!fields.blocked) {
      const main = preview ? button('Appliquer', { pri: true }) : button('Voir l’aperçu', { pri: true, iconName: 'eye' });
      main.disabled = busy;
      main.addEventListener('click', preview ? apply : askPreview);
      buttons.append(main);
    }
    const cancel = button('Fermer');
    cancel.disabled = busy;
    cancel.addEventListener('click', close);
    buttons.append(cancel);
  }

  if (kind === 'correct') {
    for (const t of TABS) {
      const b = el('button', null, t.label, { type: 'button', 'aria-pressed': String(t.id === tab) });
      b.dataset.tab = t.id;
      b.addEventListener('click', () => {
        if (tab === t.id) return;
        tab = t.id;
        for (const other of tabs.children) other.setAttribute('aria-pressed', String(other.dataset.tab === tab));
        preview = null;
        previewBox.replaceChildren();
        setActionStatus(status, '');
        drawForm();
        drawButtons();
      });
      tabs.append(b);
    }
    panel.append(title, tabs);
  } else {
    panel.append(title);
  }
  if (kind === 'undo') panel.dataset.op = row.op || '';
  panel.dataset.gesture = kind;
  panel.append(form, previewBox, status, buttons);
  drawForm();
  drawButtons();
  anchor.replaceWith(panel);
  (panel.querySelector('input, select, textarea') || panel.querySelector('button')).focus();
  // Sans formulaire (annuler, rendre au service), l'aperçu est demandé d'emblée.
  if (kind === 'undo' || kind === 'unretract') askPreview();
  return panel;
}
