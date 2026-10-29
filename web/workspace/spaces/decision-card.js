// Carte de décision v2 (P93) — le corps d'une question posée par `task ask`
// avec titre, contexte, pièces, choix riches, recommandation et rounds
// (schéma P92, cahier de conception UX §1, §4–§8).
//
// Le bandeau, le projet et l'âge restent dessinés par `decisionCard`
// (executer.js) ; ce module dessine le reste : titre, contexte en deux lignes,
// puces de pièces dépliables, recommandation, choix unique / multiple /
// texte, « Envoyer » / « Plus tard », et « Demandes précédentes ».
//
// Aucune écriture hors de `send` : la réponse part en texte lisible
// (`text`) et en structure (`answer_data`) dans la même requête — le chemin
// d'écriture ne change pas (§5.4). Rien ne s'envoie sans clic : la
// recommandation est préremplie, jamais envoyée, et aucun raccourci n'envoie
// en un seul appui (§5.3). La carte n'exécute rien et n'appelle aucun réseau :
// les pièces sont des faits déjà relevés par l'émetteur ; le texte est posé
// par `textContent`, jamais en HTML.

import { actionStatus, copyText, icon, setActionStatus } from '../shell.js';

const STYLE_ID = 'dc-styles';
const MAX_ATTACHMENTS = 6;
const SOFT_LIMIT = 500;
const BULK_FROM = 5;

const CSS = `
.dc { display: flex; flex-direction: column; gap: var(--sp-3); min-width: 0; }
.dc-context { margin: 0; font-size: var(--t-s); line-height: 1.5; color: var(--ink2); overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dc-context[data-open="true"] { display: block; }
.dc-why { width: fit-content; border: 0; background: none; padding: 0; font: inherit; font-size: var(--t-min); color: var(--pri); cursor: pointer; text-decoration: underline; }
.dc-why-text { margin: 0; padding: var(--sp-2) var(--sp-3); border-left: 2px solid var(--line); font-size: var(--t-s); color: var(--ink2); white-space: pre-wrap; overflow-wrap: anywhere; }
.dc-pieces { display: flex; flex-direction: column; gap: var(--sp-2); }
.dc-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.dc-chip { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 0 10px; border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--card); color: var(--ink); font: inherit; font-size: var(--t-min); cursor: pointer; max-width: 100%; }
.dc-chip:hover { background: var(--hover); }
.dc-chip[aria-expanded="true"] { border-color: var(--pri); background: var(--accsoft); }
.dc-chip .dc-chip-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dc-chip[data-state="failure"] .sico, .dc-chip[data-state="failure"] .ico { color: var(--bad); }
.dc-chip[data-state="success"] .ico { color: var(--ok); }
.dc-detail { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-size: var(--t-s); min-width: 0; }
.dc-detail[hidden] { display: none; }
.dc-detail pre { margin: 0; max-height: 320px; overflow: auto; font-family: var(--mono); font-size: 12px; line-height: 1.5; white-space: pre; }
.dc-detail .dc-add { color: var(--ok); background: var(--ok-soft); display: block; }
.dc-detail .dc-del { color: var(--bad); background: var(--bad-soft); display: block; }
.dc-meta { font-size: var(--t-min); color: var(--ink3); }
.dc-link { width: fit-content; }
.dc-reco { display: flex; flex-direction: column; gap: 2px; padding: var(--sp-2) var(--sp-3); border-radius: var(--r); background: var(--accsoft); font-size: var(--t-s); color: var(--ink); }
.dc-reco strong { font-weight: 600; }
.dc-reco span { color: var(--ink2); }
.dc-opts { display: flex; flex-direction: column; gap: 6px; border: 0; margin: 0; padding: 0; min-width: 0; }
.dc-opts legend { padding: 0; margin-bottom: 6px; font-size: var(--t-min); color: var(--ink3); }
.dc-opt { position: relative; display: flex; align-items: flex-start; gap: 10px; width: 100%; min-height: 56px; padding: 10px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--card); color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.dc-opt:hover { border-color: var(--ink3); }
.dc-opt:focus-visible, .dc-chip:focus-visible, .dc article:focus-visible { outline: 2px solid var(--pri); outline-offset: 2px; }
.dc-opt[aria-checked="true"] { border-color: var(--pri); box-shadow: inset 0 0 0 1px var(--pri); background: var(--accsoft); }
.dc-opt:disabled { opacity: .6; cursor: not-allowed; }
.dc-mark { flex: none; width: 16px; height: 16px; margin-top: 2px; border: 2px solid var(--ink3); border-radius: 50%; display: grid; place-items: center; }
.dc-opt[data-multi="true"] .dc-mark { border-radius: 4px; }
.dc-opt[aria-checked="true"] .dc-mark { border-color: var(--pri); background: var(--pri); color: var(--onpri); }
.dc-mark .ico { width: 10px; height: 10px; visibility: hidden; }
.dc-opt[aria-checked="true"] .dc-mark .ico { visibility: visible; }
.dc-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.dc-label { font-size: 15px; font-weight: 600; line-height: 1.35; overflow-wrap: anywhere; }
.dc-opt[data-danger="true"] .dc-label { color: var(--bad); }
.dc-conseq { font-size: 13px; line-height: 1.4; color: var(--ink2); overflow-wrap: anywhere; }
.dc-tag { align-self: flex-start; padding: 0 8px; border-radius: var(--r-pill); background: var(--pri); color: var(--onpri); font-size: 11px; font-weight: 600; line-height: 18px; }
.dc-tag[data-kind="done"] { background: var(--e2); color: var(--ink2); }
.dc-key { flex: none; align-self: center; min-width: 20px; padding: 0 6px; border: 1px solid var(--line); border-radius: 6px; background: var(--e1); color: var(--ink3); font-family: var(--mono); font-size: 11px; text-align: center; }
.dc-opts[data-grid="true"] .dc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.dc-opts[data-grid="true"] .dc-opt { min-height: 52px; }
.dc-opts[data-grid="true"] .dc-label { font-size: 14px; }
.dc-bulk { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); font-size: var(--t-min); color: var(--ink2); }
.dc-note { display: flex; flex-direction: column; gap: 4px; }
.dc-note label { font-size: var(--t-min); color: var(--ink2); }
.dc-note textarea { width: 100%; min-height: 64px; max-height: 192px; padding: 6px 9px; font: inherit; font-size: var(--t-s); resize: vertical; }
.dc-count { font-size: var(--t-min); color: var(--ink3); align-self: flex-end; }
.dc-count[data-over="true"] { color: var(--warn); }
.dc-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); }
.dc-later { margin-left: 0; }
.dc-hint { margin-left: auto; font-size: var(--t-min); color: var(--ink3); }
.dc-foot { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-3); border-top: 1px solid var(--line); padding-top: var(--sp-2); font-size: 12px; color: var(--ink3); }
.dc-foot .dc-copy { margin-left: auto; border: 0; background: none; padding: 0; font: inherit; color: var(--pri); cursor: pointer; }
.dc-later-line { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--t-s); color: var(--ink2); }
.dc-rounds { border-top: 1px solid var(--line); padding-top: var(--sp-2); font-size: var(--t-s); }
.dc-rounds > summary { cursor: pointer; width: fit-content; color: var(--ink2); font-size: var(--t-min); padding: 2px 0; }
.dc-rounds ol { list-style: none; margin: var(--sp-2) 0 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
.dc-round { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px var(--sp-3); }
.dc-round-n { grid-row: 1 / span 3; align-self: start; padding: 0 8px; border: 1px solid var(--line); border-radius: var(--r-pill); font-size: var(--t-min); line-height: 20px; color: var(--ink2); }
.dc-round-when { font-size: var(--t-min); color: var(--ink3); }
.dc-round-ans { color: var(--ink); overflow-wrap: anywhere; }
.dc-round-next { color: var(--ink2); overflow-wrap: anywhere; }
.dc-warn { font-size: var(--t-min); color: var(--warn); }
@container canvas (max-width: 720px) {
  .dc-chips { flex-wrap: nowrap; overflow-x: auto; scroll-snap-type: x proximity; padding-bottom: 2px; }
  .dc-chip { flex: none; scroll-snap-align: start; min-height: 36px; }
  .dc-opt { min-height: 48px; }
  .dc-key { display: none; }
  .dc-detail pre { font-size: 12px; }
  .dc-opts[data-grid="true"] .dc-grid { grid-template-columns: minmax(0, 1fr); }
  .dc-actions { position: sticky; bottom: 0; z-index: 2; flex-direction: column; align-items: stretch; gap: 4px; margin: 0 calc(-1 * var(--sp-3)); padding: var(--sp-2) var(--sp-3) var(--sp-3); background: var(--card); border-top: 1px solid var(--line); }
  .dc-actions .btn.pri { min-height: 48px; border-radius: var(--r-pill); }
  .dc-later { margin-left: 0; min-height: 40px; border: 0; background: transparent; justify-content: center; }
  .dc-hint { display: none; }
}
`;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}

/** Ce que la vue garde d'un rendu à l'autre (le pouls redessine tout). */
const ui = { picked: new Map(), note: new Map(), open: new Map(), later: new Set(), why: new Set(), history: new Set() };

function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value != null) node.textContent = value;
  return node;
}

/** Une question porte-t-elle du contenu v2 ? `schema`, `task_title` et `round` sont posés sur toute
 *  question écrite par le service : seuls les champs que l'émetteur remplit décident. Sinon, la
 *  carte v1 la rend (repli `split_legacy` côté API pour la CLI et À traiter). */
export function isV2(q) {
  return !!q && !!(q.title || q.context || (q.choices || []).length || (q.attachments || []).length
    || q.answer_mode || q.recommendation);
}

/** Les choix riches ; à défaut, les `options` simples deviennent des choix. */
export function choicesOf(q) {
  if ((q.choices || []).length) return q.choices.map((c) => ({ ...c, label: c.label || c.value }));
  return (q.options || []).map((o) => ({ value: String(o), label: String(o) }));
}

/** Le mode de réponse : celui de l'émetteur, sinon unique s'il y a des choix, texte sinon. */
export function modeOf(q) {
  const given = String(q.answer_mode || '');
  if (['single', 'multi', 'text', 'single_text'].includes(given)) return given;
  return choicesOf(q).length ? 'single' : 'text';
}

/** La réponse en texte lisible par l'agent (§5.4). */
export function serialize(mode, values, note) {
  const head = mode === 'multi'
    ? (values.length ? `Retenu : ${values.join(', ')}` : '')
    : (values[0] || '');
  const clean = (note || '').trim();
  if (head && clean) return `${head} — ${clean}`;
  return head || clean;
}

/** Les URL qu'on ouvre : https, ou http sur localhost. */
export function safeUrl(ref) {
  try {
    const url = new URL(String(ref || ''));
    if (url.protocol === 'https:') return url.href;
    if (url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)) return url.href;
  } catch { /* URL illisible : puce neutre */ }
  return '';
}

const TYPE_ICON = { link: 'link', file: 'i-ui-files', diff: 'branch', ci: 'pr', image: 'i-ui-files', command: 'terminal' };
const STATE_WORD = { open: 'ouverte', merged: 'fusionnée', success: 'verte', failure: 'rouge', pending: 'en cours' };

function ageOf(iso) {
  const then = Date.parse(iso || '');
  if (Number.isNaN(then)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `il y a ${hours} h` : `il y a ${Math.round(hours / 24)} j`;
}

function chipLabel(a) {
  const stats = a.stats || {};
  if (a.type === 'diff' && (stats.files != null || stats.added != null)) {
    return `Diff · ${stats.files ?? '?'} fichier${stats.files > 1 ? 's' : ''}, +${stats.added ?? 0} −${stats.removed ?? 0}`;
  }
  if (a.type === 'ci' && stats.jobs != null) return `${a.label || 'CI'} : ${stats.failed ?? 0} échec${stats.failed > 1 ? 's' : ''} sur ${stats.jobs}`;
  if (a.type === 'command') return (a.label || a.ref || 'Commande').slice(0, 40);
  if (a.type === 'file' && a.lines && a.label && !a.label.includes(a.lines)) return `${a.label}:${a.lines}`;
  return a.label || a.ref || a.type;
}

function excerptNode(a) {
  const pre = el('pre');
  pre.tabIndex = 0;
  const lines = String(a.excerpt || '').split('\n');
  if (a.type === 'diff') {
    for (const line of lines.slice(0, 200)) {
      const span = el('span', line.startsWith('+') ? 'dc-add' : (line.startsWith('-') || line.startsWith('−') ? 'dc-del' : ''), line || ' ');
      pre.append(span);
    }
  } else {
    const first = Number.parseInt(String(a.lines || '').split('-')[0], 10);
    pre.textContent = Number.isFinite(first)
      ? lines.slice(0, 12).map((line, i) => `${String(first + i).padStart(4)}  ${line}`).join('\n')
      : lines.slice(0, 12).join('\n');
  }
  return pre;
}

function detailOf(a) {
  const box = el('div', 'dc-detail');
  box.dataset.role = 'attachment-detail';
  const meta = [];
  if (a.state) meta.push(STATE_WORD[a.state] || a.state);
  if (a.rev) meta.push(`révision ${a.rev}`);
  if (a.fetched_at) meta.push(`relevé ${ageOf(a.fetched_at)}`);
  if (a.type === 'link') {
    box.append(el('strong', null, a.label || a.ref));
    const url = safeUrl(a.ref);
    if (url) {
      const open = el('a', 'btn dc-link', 'Ouvrir');
      open.href = url;
      open.target = '_blank';
      open.rel = 'noopener noreferrer';
      box.append(open);
    } else if (a.ref) {
      box.append(el('span', 'dc-meta', a.ref.length > 60 ? `${a.ref.slice(0, 57)}…` : a.ref));
    }
  } else if (a.type === 'command') {
    const code = el('pre', null, a.ref || a.excerpt || '');
    box.append(code);
    box.append(el('span', 'dc-meta', a.mutates ? 'Modifie des fichiers' : 'Lecture seule'));
    const copy = el('button', 'btn', null);
    copy.type = 'button';
    copy.dataset.role = 'copy-command';
    copy.append(icon('copy'), el('span', null, 'Copier'));
    copy.addEventListener('click', () => { void copyText(a.ref || a.excerpt || '', copy); });
    box.append(copy);
  } else if (a.type === 'image') {
    box.append(el('span', null, a.alt || a.label || 'Capture'));
    const url = safeUrl(a.ref);
    if (url) {
      const img = el('img');
      img.src = url;
      img.alt = a.alt || a.label || '';
      img.style.maxWidth = '320px';
      img.style.borderRadius = 'var(--r)';
      box.append(img);
    } else {
      box.append(el('span', 'dc-meta', a.ref || ''));
    }
  } else if (a.excerpt) {
    box.append(el('strong', null, a.type === 'ci' ? chipLabel(a) : (a.label || chipLabel(a))));
    box.append(excerptNode(a));
    if (a.type === 'diff' && String(a.excerpt).split('\n').length > 200) box.append(el('span', 'dc-meta', 'Aperçu des 200 premières lignes'));
  } else {
    const empty = { diff: 'Aucune différence', ci: 'Aucun run associé', file: `Fichier introuvable${a.rev ? ` à la révision ${a.rev}` : ''}` };
    box.append(el('span', 'dc-meta', empty[a.type] || a.ref || 'Rien à montrer'));
  }
  if (meta.length) box.append(el('span', 'dc-meta', meta.join(' · ')));
  return box;
}

function piecesNode(q, key, shortcuts) {
  const all = q.attachments || [];
  if (!all.length) return null;
  const shown = all.slice(0, MAX_ATTACHMENTS);
  const wrap = el('div', 'dc-pieces');
  wrap.dataset.role = 'attachments';
  const row = el('div', 'dc-chips');
  const details = [];
  const chips = [];
  const primary = shown.findIndex((a) => a.primary);
  if (!ui.open.has(key)) ui.open.set(key, primary);
  const paint = () => {
    const current = ui.open.get(key);
    chips.forEach((chipNode, i) => chipNode.setAttribute('aria-expanded', String(i === current)));
    details.forEach((d, i) => { d.hidden = i !== current; });
  };
  shown.forEach((a, i) => {
    const chipNode = el('button', 'dc-chip');
    chipNode.type = 'button';
    chipNode.dataset.role = 'attachment';
    chipNode.dataset.type = a.type;
    if (a.state) chipNode.dataset.state = a.state;
    chipNode.append(icon(TYPE_ICON[a.type] || 'info'), el('span', 'dc-chip-label', chipLabel(a)));
    const stateWord = STATE_WORD[a.state];
    if (stateWord && a.type !== 'ci') chipNode.append(el('span', 'dc-meta', stateWord));
    chipNode.addEventListener('click', () => { ui.open.set(key, ui.open.get(key) === i ? -1 : i); paint(); });
    chips.push(chipNode);
    row.append(chipNode);
    const detail = detailOf(a);
    details.push(detail);
  });
  if (all.length > MAX_ATTACHMENTS) row.append(el('span', 'dc-meta', `+${all.length - MAX_ATTACHMENTS} autres`));
  wrap.append(row, ...details);
  paint();
  shortcuts.toggleAttachments = () => {
    ui.open.set(key, ui.open.get(key) >= 0 ? -1 : 0);
    paint();
  };
  return wrap;
}

function roundsNode(rounds, key, shortcuts) {
  const past = (rounds || []).filter((r) => r.answered_at);
  if (!past.length) return null;
  const details = el('details', 'dc-rounds');
  details.dataset.role = 'rounds';
  details.open = ui.history.has(key) || past.length > 0;
  details.addEventListener('toggle', () => { if (details.open) ui.history.add(key); else ui.history.delete(key); });
  details.append(el('summary', null, 'Demandes précédentes'));
  const list = el('ol');
  for (const r of [...past].reverse().slice(0, 5)) {
    const li = el('li', 'dc-round');
    li.dataset.role = 'round';
    li.append(el('span', 'dc-round-n', r.round === 1 ? '1re' : `${r.round}e`));
    li.dataset.round = String(r.round);
    li.append(el('span', 'dc-round-when', ageOf(r.answered_at || r.asked_at)));
    li.append(el('span', 'dc-round-ans', `Tu as répondu : ${r.answer_label || '—'}.`));
    li.append(el('span', 'dc-round-next', r.aftermath ? `Suite : ${r.aftermath}` : 'Aucune suite enregistrée'));
    list.append(li);
  }
  details.append(list);
  if ((rounds || []).length >= 3) details.append(el('p', 'dc-warn', 'Troisième demande pour cette tâche : la réponse précédente n’a pas suffi.'));
  shortcuts.toggleHistory = () => { details.open = !details.open; };
  return details;
}

/**
 * Dessine le corps d'une question v2 dans `box`.
 *
 * `deps` vient d'executer.js : `post(answer, data)` (envoie, rend la promesse),
 * `locked`, `onWritten`, `round` (numéro de la demande), `rounds` (le fil lu
 * au ledger) et `article` (la carte, qui reçoit les raccourcis).
 */
export function appendQuestionV2(box, task, q, deps) {
  injectStyles();
  const key = `${task.project?.slug || ''}|${task.id}|${q.id}`;
  const choices = choicesOf(q);
  const mode = modeOf(q);
  const multi = mode === 'multi';
  const hasNote = mode === 'text' || mode === 'single_text' || multi;
  const reco = q.recommendation || null;
  const recoValues = reco ? [].concat(reco.value || []).map(String) : [];
  const shortcuts = {};
  const root = el('div', 'dc');
  root.dataset.role = 'decision-v2';
  root.dataset.mode = mode;
  box.append(root);

  if (ui.later.has(key)) {
    const line = el('div', 'dc-later-line');
    line.dataset.role = 'later-line';
    line.append(el('span', null, `« ${q.title || task.title} » : remis à plus tard, rien n'a été envoyé.`));
    const reopen = el('button', 'btn', 'Rouvrir');
    reopen.type = 'button';
    reopen.addEventListener('click', () => { ui.later.delete(key); root.remove(); appendQuestionV2(box, task, q, deps); });
    line.append(reopen);
    root.append(line);
    return;
  }

  const title = q.title || '';
  if (title) {
    const h = el('h4', 'ex-decision-title', title);
    h.id = `dc-t-${key.replace(/[^\w-]/g, '_')}`;
    h.dataset.role = 'decision-title';
    root.append(h);
    if (deps.article) deps.article.setAttribute('aria-labelledby', h.id);
  }
  if (q.context) {
    const context = el('p', 'dc-context', q.context);
    context.dataset.role = 'decision-context';
    root.append(context);
  }
  if (q.why) {
    const why = el('button', 'dc-why', 'Pourquoi cette question');
    why.type = 'button';
    why.setAttribute('aria-expanded', String(ui.why.has(key)));
    const whyText = el('p', 'dc-why-text', q.why);
    whyText.hidden = !ui.why.has(key);
    why.addEventListener('click', () => {
      const on = whyText.hidden;
      whyText.hidden = !on;
      why.setAttribute('aria-expanded', String(on));
      if (on) ui.why.add(key); else ui.why.delete(key);
    });
    root.append(why, whyText);
  }
  const pieces = piecesNode(q, key, shortcuts);
  if (pieces) root.append(pieces);

  // Recommandation : en tête des choix, préremplie — jamais envoyée sans clic.
  if (reco && recoValues.length) {
    const box2 = el('div', 'dc-reco');
    box2.dataset.role = 'recommendation';
    const names = recoValues.map((v) => (choices.find((c) => c.value === v) || { label: v }).label).join(', ');
    box2.append(el('strong', null, `Recommandé : ${names}`));
    if (reco.reason) box2.append(el('span', null, reco.reason));
    if (reco.consequence) box2.append(el('span', null, reco.consequence));
    root.append(box2);
  }

  const state = () => ui.picked.get(key) || [];
  const controls = [];
  const send = el('button', 'btn pri', 'Envoyer');
  send.type = 'button';
  send.dataset.role = 'answer-send';
  const status = actionStatus('');
  status.dataset.role = 'answer-status';
  const noteArea = el('textarea', 'input');
  const count = el('span', 'dc-count');
  const optionNodes = [];
  const counter = el('span', 'dc-meta');
  counter.setAttribute('aria-live', 'polite');

  if (!ui.picked.has(key) && recoValues.length && choices.length) {
    ui.picked.set(key, multi ? recoValues : recoValues.slice(0, 1));
  }

  const note = () => noteArea.value;
  const ready = () => {
    if (mode === 'text') return note().trim().length > 0;
    return state().length > 0;
  };
  const paint = () => {
    const picked = new Set(state());
    for (const opt of optionNodes) opt.setAttribute('aria-checked', String(picked.has(opt.dataset.value)));
    send.disabled = deps.locked || !ready();
    if (multi) counter.textContent = `${picked.size} sélectionnée${picked.size > 1 ? 's' : ''} sur ${choices.length}`;
    const n = note().length;
    count.textContent = `${n} / ${SOFT_LIMIT}`;
    count.dataset.over = String(n > SOFT_LIMIT);
  };
  const choose = (value) => {
    const current = state();
    if (multi) ui.picked.set(key, current.includes(value) ? current.filter((v) => v !== value) : [...current, value]);
    else ui.picked.set(key, [value]);
    paint();
  };

  if (choices.length && mode !== 'text') {
    const group = el('fieldset', 'dc-opts');
    group.setAttribute('role', multi ? 'group' : 'radiogroup');
    const legend = el('legend', null, multi ? 'Plusieurs choix possibles' : 'Un seul choix');
    group.append(legend);
    if (multi && choices.length > BULK_FROM) {
      const bulk = el('div', 'dc-bulk');
      bulk.dataset.role = 'bulk';
      const all = el('button', 'btn', 'Tout cocher');
      all.type = 'button';
      all.dataset.role = 'check-all';
      all.addEventListener('click', () => { ui.picked.set(key, choices.map((c) => c.value)); paint(); });
      const none = el('button', 'btn', 'Tout décocher');
      none.type = 'button';
      none.dataset.role = 'uncheck-all';
      none.addEventListener('click', () => { ui.picked.set(key, []); paint(); });
      controls.push(all, none);
      bulk.append(all, none, counter);
      group.append(bulk);
    } else if (multi) {
      group.append(counter);
    }
    const grid = multi && choices.length > BULK_FROM;
    const list = grid ? el('div', 'dc-grid') : group;
    if (grid) group.dataset.grid = 'true';
    choices.forEach((c, i) => {
      const opt = el('button', 'dc-opt');
      opt.type = 'button';
      opt.setAttribute('role', multi ? 'checkbox' : 'radio');
      opt.dataset.role = 'answer-option';
      opt.dataset.value = c.value;
      opt.dataset.multi = String(multi);
      if (c.danger) opt.dataset.danger = 'true';
      const mark = el('span', 'dc-mark');
      mark.setAttribute('aria-hidden', 'true');
      mark.append(icon('check'));
      const body = el('span', 'dc-body');
      if (recoValues.includes(c.value)) body.append(el('span', 'dc-tag', 'Recommandé'));
      body.append(el('span', 'dc-label', c.label));
      if (c.consequence) body.append(el('span', 'dc-conseq', c.consequence));
      opt.append(mark, body);
      if (i < 9) opt.append(el('kbd', 'dc-key', String(i + 1)));
      opt.disabled = deps.locked;
      opt.addEventListener('click', () => choose(c.value));
      optionNodes.push(opt);
      controls.push(opt);
      list.append(opt);
    });
    if (grid) group.append(list);
    root.append(group);
  }

  if (hasNote) {
    const field = el('div', 'dc-note');
    const labelText = mode === 'text' ? 'Ta réponse' : (multi ? 'Précision (facultatif)' : 'Consigne (facultatif)');
    const label = el('label', null, labelText);
    const id = `dc-n-${key.replace(/[^\w-]/g, '_')}`;
    label.htmlFor = id;
    noteArea.id = id;
    noteArea.dataset.role = 'answer-text';
    noteArea.rows = 3;
    noteArea.value = ui.note.get(key) || '';
    noteArea.disabled = deps.locked;
    noteArea.addEventListener('input', () => {
      ui.note.set(key, noteArea.value);
      noteArea.style.height = 'auto';
      noteArea.style.height = `${Math.min(noteArea.scrollHeight, 192)}px`;
      paint();
    });
    controls.push(noteArea);
    field.append(label, noteArea, count);
    root.append(field);
  }

  const actions = el('div', 'dc-actions');
  const later = el('button', 'btn dc-later', 'Plus tard');
  later.type = 'button';
  later.dataset.role = 'answer-later';
  later.addEventListener('click', () => { ui.later.add(key); root.remove(); appendQuestionV2(box, task, q, deps); });
  controls.push(later);
  actions.append(send, later);
  if (hasNote) actions.append(el('span', 'dc-hint', 'Ctrl + Entrée pour envoyer'));
  root.append(actions, status);

  const submit = async () => {
    if (!ready() || deps.locked) return;
    const values = state();
    const answer = serialize(mode, values, hasNote ? note() : '');
    if (!answer) return;
    const data = { choices: values, note: note().trim(), question_id: q.id };
    for (const c of controls) c.disabled = true;
    send.disabled = true;
    send.textContent = 'Envoi…';
    root.setAttribute('aria-busy', 'true');
    setActionStatus(status, 'Envoi de la réponse…');
    try {
      const result = await deps.post(answer, data);
      if (result && result.blocked) throw new Error(result.reason || result.message || 'réponse refusée');
      ui.picked.delete(key);
      ui.note.delete(key);
      setActionStatus(status, `Répondu : ${answer}`, 'ok');
      deps.onWritten();
    } catch (error) {
      for (const c of controls) c.disabled = deps.locked;
      send.textContent = 'Envoyer';
      root.removeAttribute('aria-busy');
      setActionStatus(status, deps.failure(error), 'bad');
      paint();
    }
  };
  send.addEventListener('click', submit);
  noteArea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void submit(); }
    if (event.key === 'Escape') { noteArea.blur(); event.stopPropagation(); }
  });

  const rounds = roundsNode(deps.rounds, key, shortcuts);
  if (rounds) root.append(rounds);

  // Zone 8 : les identifiants en métadonnée, jamais dans le titre (§2.4) ; la commande équivalente se copie.
  const foot = el('div', 'dc-foot');
  foot.dataset.role = 'decision-meta';
  foot.append(el('span', null, task.id), el('span', null, q.id), el('span', null, `origine : ${q.origin || 'agent'}`));
  const command = `grimoire task answer ${task.id} "…" --question ${q.id}`;
  const copy = el('button', 'dc-copy', 'Copier la commande');
  copy.type = 'button';
  copy.addEventListener('click', () => { void copyText(command, copy); });
  foot.append(copy);
  root.append(foot);

  // Raccourcis (§5.3) : inertes dans un champ texte, aucun n'envoie.
  if (deps.article) {
    deps.article.tabIndex = 0;
    deps.article.onkeydown = (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) return;
      if (event.ctrlKey || event.metaKey || event.altKey || deps.locked) return;
      const k = event.key.toLowerCase();
      if (/^[1-9]$/.test(k) && choices[Number(k) - 1] && mode !== 'text') { event.preventDefault(); choose(choices[Number(k) - 1].value); }
      else if (k === 'r' && recoValues.length) { event.preventDefault(); ui.picked.set(key, multi ? recoValues : recoValues.slice(0, 1)); paint(); }
      else if (k === 'e' && shortcuts.toggleAttachments) { event.preventDefault(); shortcuts.toggleAttachments(); }
      else if (k === 'h' && shortcuts.toggleHistory) { event.preventDefault(); shortcuts.toggleHistory(); }
      else if (k === 'enter' && target === deps.article && ready()) { event.preventDefault(); send.focus(); }
    };
  }
  paint();
}
