// Atelier de spec (en test) : la logique pure de la mise en page, sans DOM, pour la tester seule (tests/js).
//
// Panneau de l'assistant (état replié mémorisé par chantier, pastille), notifications (ton, durée, file), barre de phases
// (indicateur par phase, phase active suivie au défilement, ancre d'URL), bande des quatre étapes, commentaires orphelins.

/** Largeur de la toile (px) sous laquelle l'assistant devient un tiroir plutôt qu'une colonne. */
export const WIDE_MIN = 1100;

export const PHASE_ORDER = ['brief', 'brainstorm', 'comprehension', 'exigences', 'cahier-des-charges'];

// ── Panneau de l'assistant : replié / déplié, mémorisé par chantier ──────────

export const prefKey = (slug) => `grimoire.atelier.assistant.${slug}`;

/** `'open'`, `'closed'` ou null (aucun choix mémorisé, ou stockage indisponible). */
export function readPref(storage, slug) {
  try {
    const value = storage ? storage.getItem(prefKey(slug)) : null;
    return value === 'open' || value === 'closed' ? value : null;
  } catch {
    return null;
  }
}

/** Vrai si le choix a pu être mémorisé ; le panneau marche de même sans. */
export function writePref(storage, slug, open) {
  try {
    if (!storage) return false;
    storage.setItem(prefKey(slug), open ? 'open' : 'closed');
    return true;
  } catch {
    return false;
  }
}

/** Le choix mémorisé l'emporte ; sans lui, déplié en colonne (large), replié en tiroir (étroit). */
export function initialOpen(pref, wide) {
  if (pref === 'open') return true;
  if (pref === 'closed') return false;
  return Boolean(wide);
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/**
 * Ce que la pastille du panneau replié dit : `{ text, count, tone }`. `facts` : `{ error, unavailable, permission,
 * question, busy, suggestions }` (les mêmes faits que ceux de la mascotte).
 */
export function pillSummary(facts) {
  const f = facts || {};
  const waiting = (f.question ? 1 : 0) + (f.permission ? 1 : 0) + (f.suggestions || 0);
  if (f.error) return { text: 'Assistant en erreur', count: waiting, tone: 'error' };
  const parts = [];
  if (f.permission) parts.push('permission attendue');
  if (f.question) parts.push('question en cours');
  if (f.suggestions > 0) parts.push(plural(f.suggestions, 'suggestion', 'suggestions')); // elles attendent, assistant joignable ou non
  if (!parts.length) {
    if (f.unavailable) return { text: 'Assistant indisponible', count: 0, tone: 'off' };
    return { text: f.busy ? 'Assistant réfléchit' : 'Assistant', count: 0, tone: f.busy ? 'busy' : '' };
  }
  const text = parts.join(', ');
  return { text: text.charAt(0).toUpperCase() + text.slice(1), count: waiting, tone: 'wait' };
}

// ── Notifications ────────────────────────────────────────────────────────────

const ERROR_RE = /impossible|n[’']a pas pu|non transmis|indisponible|refus[ée]|échec|introuvable/i;
const WARN_RE = /a changé|périmée|déjà été tranchée|n’a pas été enregistrée|orphelin|plus leur passage|plus son passage/i;

/** `'error'` (reste jusqu'à fermeture, `role="alert"`), `'warn'` (longue durée) ou `'ok'` (courte). */
export function toneOf(message) {
  const text = String(message || '');
  if (ERROR_RE.test(text)) return 'error';
  if (WARN_RE.test(text)) return 'warn';
  return 'ok';
}

/** Durée d'affichage en ms ; 0 = jusqu'à fermeture. Une erreur ne disparaît jamais seule. */
export function ttlOf(kind, hasAction) {
  if (kind === 'error') return 0;
  if (kind === 'warn' || hasAction) return 12000;
  return 6000;
}

export const MAX_TOASTS = 4;

/** La file après l'ajout de `item` : un texte identique est remplacé (pas de doublon), les plus anciens tombent au-delà du plafond. */
export function pushToast(list, item, max = MAX_TOASTS) {
  const next = list.filter((t) => t.text !== item.text);
  next.push(item);
  return next.slice(Math.max(0, next.length - max));
}

export const dropToast = (list, id) => list.filter((t) => t.id !== id);

// ── Barre de phases ─────────────────────────────────────────────────────────

/** `'vide'`, `'en-cours'` ou `'complete'` d'après l'état de la phase connu de la page (`absent|vide|partiel|rempli`). */
export function phaseIndicator(state) {
  if (state === 'rempli') return 'complete';
  if (state === 'partiel') return 'en-cours';
  return 'vide';
}

export const INDICATOR_LABEL = { vide: 'vide', 'en-cours': 'en cours', complete: 'complète' };

/**
 * La phase active au défilement. `order` : les identifiants dans l'ordre du document ; `inBand` : ceux dont la section
 * touche la bande de lecture ; `atBottom` : la fin du document est atteinte (la dernière, trop courte pour atteindre la
 * bande, est alors la phase lue). Sans rien dans la bande, la phase courante reste.
 */
export function activePhase(order, inBand, current, atBottom = false) {
  if (atBottom && order.length) return order[order.length - 1];
  let found = null;
  for (const id of order) if (inBand.has(id)) found = id;
  return found || current;
}

/** L'ancre d'URL d'une phase : `#atelier?chantier=<slug>&phase=<id>`. */
export function phaseHash(slug, phase) {
  const query = new URLSearchParams({ chantier: slug });
  if (phase) query.set('phase', phase);
  return `#atelier?${query.toString()}`;
}

/** La phase d'une ancre d'URL, si elle existe dans le document ; sinon `fallback`. */
export function phaseFromParams(params, valid, fallback) {
  const wanted = params && params.phase;
  return wanted && valid.includes(wanted) ? wanted : fallback;
}

// ── Bande des quatre étapes ─────────────────────────────────────────────────

export const STEPS = [
  ['cadrer', 'Cadrer'], ['exigences', 'Exigences'], ['valider', 'Valider'], ['decouper', 'Découper'],
];

/**
 * L'étape courante déduite de ce que la page a déjà : les états des phases (`doc.phases[].state`) et la trace des
 * exigences (`trace.requirements`, `trace.summary`), nulle tant qu'elle n'est pas chargée.
 * Rend `[{ id, label, status: 'done'|'current'|'todo' }]` ; au plus une étape courante, aucune quand tout est fait.
 */
export function stepsOf(phases, trace) {
  const state = (id) => ((phases || []).find((p) => p.id === id) || {}).state;
  const reqs = (trace && trace.requirements) || [];
  const done = {
    cadrer: ['brief', 'brainstorm', 'comprehension'].every((id) => state(id) === 'rempli'),
    exigences: state('exigences') === 'rempli' && reqs.length > 0,
    valider: reqs.length > 0 && reqs.filter((r) => r.priority === 'must').every((r) => r.validated),
    decouper: reqs.length > 0 && reqs.every((r) => (r.task_count || 0) > 0),
  };
  let currentSet = false;
  return STEPS.map(([id, label]) => {
    if (currentSet) return { id, label, status: 'todo' }; // le déroulé est ordonné : rien n'est « fait » après l'étape courante
    if (done[id]) return { id, label, status: 'done' };
    currentSet = true;
    return { id, label, status: 'current' };
  });
}

// ── Commentaires orphelins ──────────────────────────────────────────────────

/** Identifiants des fils devenus orphelins entre `before` et `after` (listes de fils du serveur, ouverts ou non). */
export function newOrphans(before, after) {
  const was = new Set((before || []).filter((c) => c.anchor && c.anchor.state === 'orphelin').map((c) => c.id));
  return (after || []).filter((c) => c.anchor && c.anchor.state === 'orphelin' && !was.has(c.id)).map((c) => c.id);
}

export function orphanNotice(n) {
  return n > 1 ? `${n} commentaires n’ont plus leur passage` : '1 commentaire n’a plus son passage';
}

// ── Finitions ───────────────────────────────────────────────────────────────

export const capitalize = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : '');

/** Un texte découpé aux accents graves : `[{ text, code }]` ; un accent isolé reste du texte. */
export function codeSegments(text) {
  const out = [];
  const parts = String(text || '').split('`');
  if (parts.length % 2 === 0) return [{ text: String(text || ''), code: false }]; // accent non fermé : tel quel
  parts.forEach((part, i) => { if (part) out.push({ text: part, code: i % 2 === 1 }); });
  return out;
}
