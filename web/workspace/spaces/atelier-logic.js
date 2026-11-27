// Atelier de spec (en test) : la logique pure des deux finitions, sans DOM, pour la tester seule (tests/js).
//
// 1. « Rattacher ici » : un fil orphelin s'ancre à la sélection courante du document, ou au bloc courant sans sélection.
//    Le serveur revérifie tout contre le texte réel du bloc (`POST atelier/chantiers/<slug>/comments/<id>`, action `attach`).
// 2. La dérive détaillée : le diff « validé → courant » d'une exigence modifiée depuis sa validation.

export const QUOTE_MAX = 300;

/** Parmi les occurrences de `picked` dans `text`, celle qui est la plus proche de `guess` ; -1 s'il n'y en a aucune. */
export function nearestStart(text, picked, guess) {
  let best = -1;
  for (let at = text.indexOf(picked); at >= 0; at = text.indexOf(picked, at + 1)) {
    if (best < 0 || Math.abs(at - guess) < Math.abs(best - guess)) best = at;
  }
  return best;
}

/**
 * Ce que « Rattacher ici » envoie : `pick` est la dernière sélection de l'humain dans un bloc (`{ blockId, quote, start }`)
 * ou null ; `current` est le bloc courant (`{ id }`) ou null. Une sélection l'emporte ; sans elle, le bloc courant entier
 * (`quote` vide : le serveur cite alors le début du bloc). Rend `{ blockId, quote, start, whole }` ou null (rien à désigner).
 */
export function attachTarget(pick, current) {
  if (pick && pick.blockId && pick.quote) {
    return { blockId: pick.blockId, quote: pick.quote.slice(0, QUOTE_MAX), start: pick.start, whole: false };
  }
  if (current && current.id) return { blockId: current.id, quote: '', start: null, whole: true };
  return null;
}

/** Le fil orphelin que « Rattacher ici » peut reprendre : ouvert, sans bloc d'ancrage. */
export function canAttach(comment) {
  return Boolean(comment) && comment.etat === 'ouvert' && comment.anchor && comment.anchor.state === 'orphelin';
}

/** Les lignes d'un diff de dérive, chacune avec son sens (`del`, `add`, `more`) pour le style : `[{ kind, text }]`. */
export function diffRows(drift) {
  if (!drift || !Array.isArray(drift.diff)) return [];
  return drift.diff.map((line) => {
    const head = String(line).charAt(0);
    if (head === '-') return { kind: 'del', text: String(line) };
    if (head === '+') return { kind: 'add', text: String(line) };
    return { kind: 'more', text: String(line) };
  });
}

/** La phrase qui dit pourquoi le diff manque ou est vide ; vide quand le diff dit tout. */
export function driftNote(drift) {
  return drift && drift.note ? String(drift.note) : '';
}

/** La ligne d'historique d'un rattachement : « Rattaché le … : « ancien » → « nouveau » ». */
export function historyLine(entry, dayOf) {
  const day = dayOf ? dayOf(entry.date) : entry.date;
  const cut = (text) => (text.length > 60 ? `${text.slice(0, 59)}…` : text);
  return `Rattaché${day ? ` le ${day}` : ''} : « ${cut(entry.avant.quote)} » → « ${cut(entry.apres.quote)} »`;
}
