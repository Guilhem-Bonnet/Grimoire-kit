// Atelier de spec : la logique pure du panneau des questions guidées, sans DOM (testée par tests/js/atelier_entretien.test.mjs).

/** Une réponse prend en moyenne une demi-minute : de quoi dire ce qu'il reste en minutes, sans promettre davantage. */
export const SECONDS_PER_QUESTION = 30;

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** « Encore 4 questions, environ 2 minutes » : ce qu'il reste, en mots, à la place de « Question 3 sur 10 ». */
export function remainingText(guide) {
  const left = Math.max(0, (guide.total || 0) - (guide.position || 0));
  if (left <= 0) return 'Plus aucune question';
  if (left === 1) return 'Dernière question, moins d’une minute';
  const minutes = Math.max(1, Math.ceil((left * SECONDS_PER_QUESTION) / 60));
  return `Encore ${left} questions, environ ${plural(minutes, 'minute', 'minutes')}`;
}

/** Où ira la réponse à la question courante : « Le problème (Brief) ». */
export function destination(cur) {
  const phase = String(cur.target_phase_title || cur.phase_title || '').split(' — ')[0];
  return { section: cur.target_section || cur.section, phase, phaseId: cur.target_phase || cur.phase };
}

/** L'accusé d'une réponse : où elle a été rangée, selon le geste (réponse, « je ne sais pas », « sans objet »). */
export function acknowledgement(previous) {
  if (!previous || !previous.stored) return null;
  const { kind, section, phase_title: title, phase } = previous.stored;
  const where = `${section} (${String(title || '').split(' — ')[0]})`;
  const text = kind === 'unknown'
    ? `Noté comme question à préciser : ${where}`
    : kind === 'skip' ? `Rubrique marquée « sans objet » : ${where}` : `Ta réponse est rangée dans : ${where}`;
  return { text, section, phase };
}

/** La hauteur d'une zone de réponse qui grandit avec son texte : jamais sous `min`, jamais au-delà de `max` (elle défile alors). */
export function areaHeight(scrollHeight, min, max) {
  return Math.min(Math.max(scrollHeight, min), max);
}

// ── « Agrandir » : l'entretien au centre, le document replié à côté ; le choix est mémorisé par navigateur ─────────

export const BIG_KEY = 'grimoire.atelier.entretien.grand';

/** Vrai si l'entretien agrandi a été choisi (stockage absent ou illisible : non). */
export function readBig(storage) {
  try {
    return Boolean(storage) && storage.getItem(BIG_KEY) === '1';
  } catch {
    return false;
  }
}

/** Vrai si le choix a pu être mémorisé ; l'agrandissement marche de même sans. */
export function writeBig(storage, on) {
  try {
    if (!storage) return false;
    storage.setItem(BIG_KEY, on ? '1' : '0');
    return true;
  } catch {
    return false;
  }
}
