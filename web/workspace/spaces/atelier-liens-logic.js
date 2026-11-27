// Atelier de spec : les références dans le texte d'un bloc, côté logique pure (aucun DOM ; testé par tests/js/atelier_liens.test.mjs).
//
// Trois formes deviennent des liens quand elles se retrouvent dans le document ou dans le kit : un identifiant d'exigence
// (`REQ-001`), un identifiant de tâche du kit (`GAO-<mots>-001`, celui que « Valider et découper » crée) et une référence à un
// autre chantier (`[[slug ou titre]]`). Ici : les repérer dans un texte, les résoudre sur les données déjà chargées, et dire
// l'adresse qu'elles visent. Le rendu (nœuds, survol, défilement) est dans `atelier-liens.js`.

import { fold } from './atelier-document-logic.js';

// Un seul passage : la référence de chantier (la plus englobante), puis la tâche (qui peut contenir des chiffres), puis l'exigence.
const REFERENCE = /\[\[([^[\]\n]{1,120})\]\]|\bGAO-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-\d{3,}\b|\bREQ-\d{1,4}\b/g;

/**
 * Un texte brut découpé en morceaux : `{ type: 'text', text }` ou `{ type: 'req' | 'task' | 'chantier', text, value }`
 * (`text` tel qu'écrit, `value` l'identifiant, ou le nom entre crochets, sans blancs de bord).
 */
export function splitRefs(source) {
  const text = String(source == null ? '' : source);
  const out = [];
  let last = 0;
  REFERENCE.lastIndex = 0;
  for (let match = REFERENCE.exec(text); match; match = REFERENCE.exec(text)) {
    const value = match[1] === undefined ? match[0] : match[1].trim();
    const type = match[1] !== undefined ? 'chantier' : (match[0].startsWith('REQ-') ? 'req' : 'task');
    if (type === 'chantier' && !value) continue; // `[[ ]]` n'est pas une référence
    if (match.index > last) out.push({ type: 'text', text: text.slice(last, match.index) });
    out.push({ type, text: match[0], value });
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) });
  return out;
}

/** Vrai si le texte contient au moins une référence (évite de découper les textes ordinaires). */
export function hasRefs(source) {
  REFERENCE.lastIndex = 0;
  return REFERENCE.test(String(source == null ? '' : source));
}

/** Le chantier que désigne `[[nom]]` : son slug ou son titre, sans casse ni accents ; un chantier illisible ne se désigne pas. */
export function resolveChantier(name, rows) {
  const wanted = fold(name).replace(/\s+/g, ' ').trim();
  if (!wanted || !Array.isArray(rows)) return null;
  const readable = rows.filter((row) => row && !row.error && row.slug);
  return readable.find((row) => fold(row.slug) === wanted)
    || readable.find((row) => fold(row.titre).replace(/\s+/g, ' ').trim() === wanted)
    || null;
}

/** Les identifiants d'exigence que porte le document (blocs de genre `exigence`), avec leur phase. */
export function exigenceBlocks(doc) {
  const out = new Map();
  for (const phase of (doc && doc.phases) || []) {
    for (const block of phase.blocks || []) {
      if (block.kind === 'exigence' && block.req && !out.has(block.req)) out.set(block.req, { phase: phase.id, block });
    }
  }
  return out;
}

/**
 * La carte d'une exigence au survol : titre, priorité et état de validation, d'après les données déjà chargées (le bloc et la
 * trace du découpage). `validee` : `true`, `false`, ou `null` quand la trace n'est pas (encore) chargée : on ne dit alors rien.
 */
export function exigenceCard(doc, req, trace) {
  const found = exigenceBlocks(doc).get(req);
  if (!found) return null;
  const row = trace && Array.isArray(trace.requirements) ? trace.requirements.find((r) => r.id === req) : null;
  return {
    req,
    titre: String(found.block.titre || '').trim() || req,
    priorite: String(found.block.priorite || '').trim(),
    validee: row ? Boolean(row.validated) : null,
    phase: found.phase,
    blockId: found.block.id,
  };
}

/** La ligne d'état d'une carte d'exigence. */
export function cardStateText(card) {
  if (card.validee === true) return 'Validée';
  if (card.validee === false) return 'Non validée';
  return '';
}

/** L'ancre d'URL de l'Atelier : `#atelier?chantier=<slug>&phase=<id>&req=<REQ>&lecture=1` (seuls les champs donnés y figurent). */
export function atelierHash({ slug, phase = '', req = '', lecture = false } = {}) {
  const query = new URLSearchParams({ chantier: slug });
  if (phase) query.set('phase', phase);
  if (req) query.set('req', req);
  if (lecture) query.set('lecture', '1');
  return `#atelier?${query.toString()}`;
}

/** L'exigence demandée par l'adresse (`req=REQ-001`), si sa forme est valide ; sinon vide. */
export function reqFromParams(params) {
  const wanted = params && params.req;
  return typeof wanted === 'string' && /^REQ-\d{1,4}$/.test(wanted) ? wanted : '';
}
