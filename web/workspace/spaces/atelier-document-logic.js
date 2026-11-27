// Atelier de spec (en test) : la logique pure du plan, de la recherche et du menu « Document », sans DOM (tests/js).
//
// Tout est en lecture seule et se calcule sur les données déjà renvoyées par `GET atelier/chantiers/<slug>` : les rubriques et
// leur état viennent de l'avancement unique du serveur, le Markdown de chaque phase est celui du fichier tel que stocké.

export const READING_WPM = 230;

/** Le nom d'une rubrique tel que le document l'affiche : sans dièses ni espaces de bord. */
export const rubriqueName = (text) => String(text || '').replace(/^#+\s*/, '').trim();

/** Un texte sans casse ni accents, pour comparer des noms et chercher. */
export function fold(text) {
  return String(text || '').normalize('NFD').replace(/\p{M}+/gu, '').toLowerCase();
}

/**
 * Le texte replié (casse et accents ôtés) et, pour chaque unité de code du texte replié, l'index de l'unité d'origine dont il
 * provient : une occurrence trouvée dans le replié se relit ainsi dans le texte réel, même quand une lettre accentuée s'écrit
 * en deux unités une fois décomposée.
 */
export function foldWithMap(text) {
  const source = String(text || '');
  let folded = '';
  const map = [];
  let at = 0;
  for (const char of source) {
    const piece = fold(char);
    for (let i = 0; i < piece.length; i += 1) map.push(at);
    folded += piece;
    at += char.length;
  }
  return { folded, map, length: source.length };
}

/** Les occurrences de `needle` dans `text`, insensibles à la casse et aux accents, en index du texte réel : `[{ start, end }]`. */
export function findMatches(text, needle) {
  const wanted = fold(needle).trim();
  if (!wanted) return [];
  const { folded, map, length } = foldWithMap(text);
  const out = [];
  for (let at = folded.indexOf(wanted); at >= 0; at = folded.indexOf(wanted, at + wanted.length)) {
    const start = map[at];
    const last = at + wanted.length;
    const end = last < map.length ? map[last] : length;
    if (end > start) out.push({ start, end });
  }
  return out;
}

/** Index de l'occurrence suivante (`dir` = 1) ou précédente (-1), en boucle ; -1 s'il n'y en a aucune. */
export function stepIndex(current, count, dir) {
  if (count <= 0) return -1;
  if (current < 0 || current >= count) return dir < 0 ? count - 1 : 0;
  return (current + dir + count) % count;
}

/** Le compteur de la barre de recherche : « 3 sur 12 », « Aucun résultat », ou rien tant qu'aucun terme n'est saisi. */
export function countLabel(index, count, hasQuery) {
  if (!hasQuery) return '';
  if (count <= 0) return 'Aucun résultat';
  return `${index + 1} sur ${count}`;
}

/** Les mots lisibles d'un Markdown : sans front matter, commentaires HTML, ni signes de mise en forme. */
export function countWords(markdown) {
  const text = String(markdown || '')
    .replace(/^﻿?---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/```[^\n]*\n?/g, ' ');
  const words = text.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu);
  return words ? words.length : 0;
}

/** Le temps de lecture estimé, en minutes entières (au moins une dès qu'il y a un mot). */
export function readingMinutes(words, wpm = READING_WPM) {
  return words > 0 ? Math.max(1, Math.ceil(words / wpm)) : 0;
}

const nf = new Intl.NumberFormat('fr-FR');

/** « 1 234 mots · 6 min de lecture », ou « Document vide ». */
export function statsLabel(words) {
  if (!words) return 'Document vide';
  return `${nf.format(words)} mot${words > 1 ? 's' : ''} · ${readingMinutes(words)} min de lecture`;
}

/**
 * Le document entier en Markdown : les phases lisibles, dans l'ordre, chacune telle que le serveur l'a stockée (`markdown`),
 * séparées d'une ligne vide. Une phase sans fichier ou non lisible est omise.
 */
export function joinMarkdown(phases) {
  const parts = (phases || []).map((p) => (typeof p.markdown === 'string' ? p.markdown : '')).filter((t) => t.trim());
  return parts.map((t) => (t.endsWith('\n') ? t : `${t}\n`)).join('\n');
}

/** Le nom du fichier téléchargé : le chantier, en caractères sûrs. */
export function downloadName(slug) {
  const safe = String(slug || 'chantier').replace(/[^\p{L}\p{N}._-]+/gu, '-').replace(/^-+|-+$/g, '');
  return `${safe || 'chantier'}.md`;
}

/** L'état d'une rubrique pour le plan : `rempli`, `couvert` (« je ne sais pas » ou « sans objet ») ou `vide` (à compléter). */
function sectionState(raw) {
  if (raw === 'rempli') return 'rempli';
  if (raw === 'je-ne-sais-pas' || raw === 'sans-objet') return 'couvert';
  return 'vide';
}

/**
 * Le plan du document : par phase, ses rubriques dans l'ordre du fichier, chacune avec son état. L'état vient de l'avancement
 * unique du serveur (`doc.avancement.phases[].sections`) ; sans lui, une rubrique dont aucun bloc n'est un gabarit est remplie.
 * Rend `[{ id, title, state, rubriques: [{ name, state }] }]` ; l'état d'une phase est `rempli` quand tout est rempli ou couvert.
 */
export function buildOutline(doc, titles = {}) {
  const progress = new Map(((doc && doc.avancement && doc.avancement.phases) || []).map((p) => [p.id, p.sections || {}]));
  const out = [];
  for (const phase of (doc && doc.phases) || []) {
    const known = progress.get(phase.id);
    const byFold = new Map(Object.entries(known || {}).map(([name, value]) => [fold(name), value]));
    const names = [];
    for (const block of phase.blocks || []) {
      if (block.kind === 'rubrique') names.push(rubriqueName(block.text));
    }
    const rubriques = names.map((name) => {
      if (byFold.has(fold(name))) return { name, state: sectionState(byFold.get(fold(name))) };
      const inside = (phase.blocks || []).filter((b) => b.section === name && b.kind !== 'rubrique');
      const filled = inside.length > 0 && inside.every((b) => !b.placeholder);
      return { name, state: filled ? 'rempli' : 'vide' };
    });
    const state = rubriques.length && rubriques.every((r) => r.state !== 'vide') ? 'rempli' : 'vide';
    out.push({ id: phase.id, title: titles[phase.id] || phase.title, state, rubriques });
  }
  return out;
}

/** Les mots d'état d'un indicateur, dits aux lecteurs d'écran en plus de la forme. */
export const STATE_WORDS = { rempli: 'remplie', couvert: 'couverte', vide: 'à compléter' };

/**
 * L'élément courant du plan : le premier, dans l'ordre du document, qui touche la bande de lecture ; sans aucun, le précédent
 * reste. En bas de page, le dernier. `order` : identifiants dans l'ordre du document ; `inBand` : l'ensemble de ceux qui touchent.
 */
export function pickCurrent(order, inBand, previous, atBottom = false) {
  if (!order.length) return '';
  if (atBottom) return order[order.length - 1];
  const hit = order.find((id) => inBand.has(id));
  return hit !== undefined ? hit : (order.includes(previous) ? previous : order[0]);
}

export const PLAN_WIDTH = 228; // la colonne du plan et son espacement
export const SHEET_MIN = 840; // le plan ne s'ouvre pas d'office si la feuille devait descendre sous cette largeur
export const ASSISTANT_WIDTH = 340; // le panneau de l'assistant déplié et son espacement
export const CANVAS_PAD = 48;
export const BODY_MAX = 1480; // la largeur maximale du document (`.at-body-doc`)

/**
 * L'état replié ou déplié du plan : le choix mémorisé s'il existe ; sinon replié sous `wideMin`, et déplié au-dessus seulement
 * si la feuille garde au moins `SHEET_MIN` de large une fois le plan (et l'assistant, s'il est ouvert) à ses côtés.
 */
export function planOpenDefault(stored, width, wideMin = 1100, assistantOpen = false) {
  if (stored === 'open') return true;
  if (stored === 'closed') return false;
  if (width < wideMin) return false;
  return Math.min(width, BODY_MAX) - CANVAS_PAD - PLAN_WIDTH - (assistantOpen ? ASSISTANT_WIDTH : 0) >= SHEET_MIN;
}
