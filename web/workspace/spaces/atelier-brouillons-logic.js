// Atelier de spec : les brouillons locaux, sans DOM ni stockage (testés par tests/js/atelier_brouillons.test.mjs).
//
// Le texte d'un bloc en cours d'édition, d'un commentaire en cours ou de la réponse guidée est gardé dans `sessionStorage`
// (jamais envoyé au serveur), sous une clé qui dit le projet, le chantier et le bloc ou le fil.

/** Les genres de brouillon : bloc en édition, nouveau commentaire, réponse à un fil, réponse guidée. */
export const KINDS = ['bloc', 'comment', 'reply', 'guide'];

const PREFIX = 'grimoire.atelier.brouillon';
/** Au-delà, un brouillon n'est pas gardé (un bloc fait au plus 20 000 caractères). */
export const MAX_DRAFT = 24_000;

/** La clé de stockage : projet + chantier + genre + bloc ou fil. */
export function draftKey(project, slug, kind, id) {
  return [PREFIX, project || '-', slug, kind, id].map(String).join('|');
}

/** Ce qui vaut d'être gardé : un texte non vide et différent de celui du serveur. */
export function worthKeeping(text, base = '') {
  return typeof text === 'string' && text.trim() !== '' && text !== base && text.length <= MAX_DRAFT;
}

/** Une saisie non enregistrée : le champ diffère de son point de départ (un champ vide n'est pas une saisie). */
export function isDirty(value, base = '') {
  return typeof value === 'string' && value.trim() !== '' && value !== base;
}

export function encodeDraft(text, at = Date.now()) {
  return JSON.stringify({ text, at });
}

/** Le brouillon lu, ou null pour tout ce qui n'en est pas un (valeur absente, illisible, d'un autre format). */
export function decodeDraft(raw) {
  if (typeof raw !== 'string' || !raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value.text !== 'string' || !value.text.trim()) return null;
    return { text: value.text, at: Number.isFinite(value.at) ? value.at : 0 };
  } catch {
    return null;
  }
}
