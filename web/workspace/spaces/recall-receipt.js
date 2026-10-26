// Reçu de rappel (P27) : ce que la mémoire a servi, rendu une seule fois pour
// l'inspecteur de tâche (Travail) et la vue « Rappel » de Savoir.
//
// Le reçu vient du serveur (`workspace_recall.py`, journal P13) ; ce module ne
// fait que le rendre. Chaque entrée servie est décrite telle qu'elle est
// AUJOURD'HUI : un souvenir servi « actuel » puis remplacé depuis se lit
// « remplacée ». Le rejeu est une lecture (`memory/recall/replay`, GET) : le
// serveur relance la même requête et rend le différentiel, il n'écrit rien.
//
// Refus tenus ici :
// - une lecture ratée se dit (motif), jamais une liste vide ;
// - une entrée d'un autre projet n'a que sa portée, jamais son contenu ;
// - pas de bouton « Rejouer » quand la requête n'a pas été gardée : la raison
//   est écrite à sa place.

import { actionStatus, badge, failureText, setActionStatus } from '../shell.js';

export const STATE = {
  live: ['actuelle', 'ok'],
  superseded: ['remplacée', 'bad'],
  retracted: ['retirée', 'warn'],
  orphan: ['remplaçant introuvable', 'warn'],
  missing: ['introuvable', ''],
};
export const SCOPE = { project: 'projet', unattributed: 'non attribuée', other: 'autre projet' };
export const MODE = {
  search: 'recherche', hybrid: 'fusion lexical + sémantique', taxonomy: 'recherche par rubrique',
  typed: 'rappel typé', task_recall: 'rappel de tâche', free: 'requête libre',
};
export const DIFF = {
  appeared: ['apparue', 'acc'], replaced: ['remplacée', 'bad'], demoted: ['déclassée', 'warn'],
  vanished: ['disparue', ''], promoted: ['montée', 'ok'], same: ['inchangée', ''],
};
const FRESH_TONE = { a_jour: 'ok', perime: 'warn', source_disparue: 'bad', non_verifie: '', hypothese: 'warn' };

const STYLE_ID = 'rc-styles';
const STYLES = `
.rc-meta { margin: 0; color: var(--ink2); font-size: var(--t-s); overflow-wrap: anywhere; }
.rc-query { color: var(--ink); font-family: var(--mono); }
.rc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.rc-item { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 2px 8px; padding: 7px 0; border-top: 1px solid var(--line); }
.rc-item:first-child { border-top: 0; }
.rc-item[data-state="superseded"] { background: var(--bad-soft); border-radius: var(--r); }
.rc-rank { font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }
.rc-title { font-weight: 500; min-width: 0; overflow-wrap: anywhere; }
.rc-title.rc-muted { color: var(--ink2); font-weight: 400; font-style: italic; }
.rc-facts { grid-column: 2; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; color: var(--ink2); font-size: var(--t-min); }
.rc-replay { display: flex; flex-direction: column; gap: 8px; }
.rc-counts { display: flex; flex-wrap: wrap; gap: 6px; }
.rc-diff { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.rc-diff li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); }
.rc-diff li:first-child { border-top: 0; }
.rc-move { color: var(--ink2); font-size: var(--t-min); font-variant-numeric: tabular-nums; white-space: nowrap; }
.rc-why { margin: 0; color: var(--ink2); font-size: var(--t-s); }
`;

export function injectRecallStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.append(style);
}

export function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** « 25/10 14:07 », heure locale ; une date illisible est rendue telle quelle. */
export function whenText(ts) {
  if (!ts) return 'heure inconnue';
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  return date.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function scoreText(score) {
  if (typeof score !== 'number' || !Number.isFinite(score)) return '—';
  return score.toLocaleString('fr-FR', { maximumSignificantDigits: 3 });
}

/** Le titre d'une entrée servie, ou ce qu'on peut honnêtement en dire. */
export function entryTitle(entry) {
  if (entry.hidden) return 'Entrée d’un autre projet : contenu masqué';
  if (entry.found === false) return 'Entrée introuvable dans le store aujourd’hui';
  return entry.title || entry.id;
}

export function stateBadge(state) {
  const [label, tone] = STATE[state] || [state || 'état inconnu', ''];
  const node = badge(label, tone);
  node.dataset.state = state || '';
  return node;
}

export const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** Une ligne : « Requête « … » · 25/10 14:07 · rappel de tâche · 3 souvenirs servis ». */
export function receiptMeta(receipt) {
  const meta = el('p', 'rc-meta');
  meta.dataset.role = 'receipt-meta';
  if (receipt.query) {
    meta.append('Requête ', el('span', 'rc-query', `« ${receipt.query} »`));
  } else {
    meta.append(receipt.queryChars ? `Requête non conservée (${receipt.queryChars} caractères)` : 'Lecture sans requête');
  }
  const when = el('time', null, whenText(receipt.ts));
  if (receipt.ts) when.dateTime = receipt.ts;
  when.dataset.role = 'receipt-time';
  const served = receipt.served || [];
  meta.append(' · ', when, ` · ${MODE[receipt.mode] || receipt.mode || 'lecture'} · ${plural(served.length, 'souvenir servi', 'souvenirs servis')}`);
  if (receipt.servedTruncated) meta.append(` (sur ${receipt.servedTruncated})`);
  return meta;
}

/** La liste classée des entrées servies, compacte (inspecteur). */
export function servedList(receipt) {
  const served = receipt.served || [];
  if (!served.length) {
    return el('p', 'rc-why', 'Cette lecture n’a servi aucun souvenir : la mémoire n’avait rien de proche.');
  }
  const list = el('ol', 'rc-list');
  list.dataset.role = 'receipt-served';
  for (const entry of served) {
    const item = el('li', 'rc-item');
    item.dataset.id = entry.id;
    item.dataset.state = entry.state || '';
    const title = el('span', 'rc-title' + (entry.hidden || entry.found === false ? ' rc-muted' : ''), entryTitle(entry));
    title.title = entry.id;
    const facts = el('span', 'rc-facts');
    facts.append(
      el('span', 'rc-score', `score ${scoreText(entry.score)}`),
      el('span', 'rc-scope', `portée ${SCOPE[entry.scope] || 'inconnue'}`),
      stateBadge(entry.state),
    );
    if (entry.freshness) {
      const fresh = badge(entry.freshnessLabel || entry.freshness, FRESH_TONE[entry.freshness] || '');
      fresh.dataset.freshness = entry.freshness;
      facts.append(fresh);
    }
    item.append(el('span', 'rc-rank', String(entry.rank ?? '—')), title, facts);
    list.append(item);
  }
  return list;
}

/** Le différentiel d'un rejeu : comptes par sortie, puis chaque entrée qui a bougé. */
export function diffView(payload) {
  const box = el('div', 'rc-replay');
  box.dataset.role = 'replay-result';
  if (payload.reason) {
    box.append(el('p', 'rc-why', `Rejeu impossible : ${payload.reason}.`));
    return box;
  }
  const diff = payload.diff;
  if (!diff) return box;
  const counts = el('div', 'rc-counts');
  counts.dataset.role = 'replay-counts';
  for (const kind of ['appeared', 'replaced', 'demoted', 'vanished', 'promoted']) {
    const n = diff.counts[kind] || 0;
    if (!n) continue;
    const chip = badge(`${n} ${DIFF[kind][0]}${n > 1 ? 's' : ''}`, DIFF[kind][1]);
    chip.dataset.kind = kind;
    counts.append(chip);
  }
  const at = whenText(payload.ranAt);
  if (!diff.changed) {
    box.append(el('p', 'rc-why', `Rejoué à ${at} : même lot, même ordre. Rien n’a bougé depuis cette lecture.`));
    return box;
  }
  box.append(el('p', 'rc-why', `Rejoué à ${at} sur la mémoire d’aujourd’hui : ${plural(diff.changed, 'changement', 'changements')}.`), counts);
  const list = el('ul', 'rc-diff');
  for (const row of diff.rows) {
    if (row.kind === 'same') continue;
    const [label, tone] = DIFF[row.kind];
    const li = el('li');
    li.dataset.kind = row.kind;
    li.dataset.id = row.id;
    const move = row.before && row.after ? `rang ${row.before} → ${row.after}`
      : row.after ? `rang ${row.after}` : `était rang ${row.before}`;
    const title = el('span', 'rc-title', entryTitle(row.entry || { id: row.id }));
    title.title = row.id;
    li.append(badge(label, tone), title, el('span', 'rc-move', move));
    list.append(li);
  }
  box.append(list);
  return box;
}

/** Bouton « Rejouer maintenant » + zone de résultat ; `run()` rend la charge utile du rejeu. */
export function replayControl(run, { label = 'Rejouer maintenant', primary = false, onResult } = {}) {
  const wrap = el('div', 'rc-replay');
  const button = el('button', primary ? 'btn pri' : 'btn', label, { type: 'button' });
  button.dataset.role = 'replay';
  const status = actionStatus('');
  const out = el('div');
  out.setAttribute('aria-live', 'polite');
  button.addEventListener('click', async () => {
    button.disabled = true;
    setActionStatus(status, 'Rejeu en cours…', '');
    try {
      const payload = await run();
      setActionStatus(status, '', '');
      out.replaceChildren(diffView(payload));
      if (onResult) onResult(payload);
    } catch (error) {
      setActionStatus(status, `Rejeu impossible : ${failureText(error)}`, 'bad');
    } finally {
      button.disabled = false;
    }
  });
  wrap.append(button, status, out);
  return wrap;
}
