// Atelier de spec : le mode lecture, côté logique pure (aucun DOM ; testé par tests/js/atelier_lecture.test.mjs).
//
// Le mode lecture est un état du navigateur : l'adresse (`&lecture=1`) le partage, `localStorage` le garde d'une visite à
// l'autre. L'adresse prime quand elle dit quelque chose ; sinon, le dernier choix de ce navigateur.

export const LECTURE_KEY = 'grimoire.atelier.lecture';

/** Le mode demandé : `lecture=1` ou `lecture=0` dans l'adresse l'emportent ; sinon la valeur mémorisée (`'1'`). */
export function readingFrom(params, stored) {
  const wanted = params && params.lecture;
  if (wanted === '1') return true;
  if (wanted === '0') return false;
  return stored === '1';
}

/** Vrai si la touche est le raccourci Alt+L (lue par `code` : sous macOS, Alt+L produit un autre caractère), seule. */
export function isReadingShortcut(event) {
  return Boolean(event) && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
    && (event.code === 'KeyL' || String(event.key).toLowerCase() === 'l');
}

/** Le libellé et l'état du bouton de la barre. */
export function readingButtonText(on) {
  return on ? 'Modifier' : 'Lecture';
}

/** La pastille d'un fil ouvert en marge : son libellé accessible. */
export function dotLabel(count) {
  return `${count} commentaire${count > 1 ? 's' : ''} ouvert${count > 1 ? 's' : ''} : ouvrir le fil`;
}
