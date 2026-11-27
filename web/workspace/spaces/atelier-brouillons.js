// Atelier de spec : le filet de sécurité des saisies, des brouillons locaux.
//
// Le texte d'un bloc en cours d'édition, d'un commentaire (nouveau ou réponse) et de la réponse guidée est gardé dans
// `sessionStorage` à chaque frappe : clé = projet + chantier + genre + bloc ou fil (voir `atelier-brouillons-logic.js`). Rien
// n'est envoyé au serveur. Il est effacé à l'enregistrement, à l'envoi ou à l'annulation. Au retour sur le document
// (autre espace puis retour, rechargement de la page), il est PROPOSÉ sur le bloc ou le fil (« Reprendre » / « Jeter »), jamais
// appliqué en silence. `beforeunload` n'avertit que s'il existe une saisie non enregistrée. Stockage indisponible (navigation
// privée, accès bloqué) : tout se passe comme sans brouillon, sans erreur.

import { storedProject } from '../api.js';
import { decodeDraft, draftKey, encodeDraft, isDirty, worthKeeping } from './atelier-brouillons-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

export function createDrafts(ctx, slug) {
  const bound = new Set(); // { area, base } : les champs en cours de saisie
  const storage = () => {
    try { return window.sessionStorage; } catch { return null; }
  };
  const keyOf = (kind, id) => draftKey(storedProject(), slug, kind, id);

  const read = (kind, id) => {
    try {
      const store = storage();
      return store ? decodeDraft(store.getItem(keyOf(kind, id))) : null;
    } catch {
      return null;
    }
  };
  const write = (kind, id, text) => {
    try {
      const store = storage();
      if (store) store.setItem(keyOf(kind, id), encodeDraft(text));
    } catch { /* quota ou accès bloqué : pas de brouillon, la saisie, elle, continue */ }
  };
  const clear = (kind, id) => {
    try {
      const store = storage();
      if (store) store.removeItem(keyOf(kind, id));
    } catch { /* idem */ }
  };

  /** Garde le texte du champ à chaque frappe (`base` : le texte du serveur, qui n'est pas un brouillon). */
  const bind = (area, kind, id, base = '') => {
    bound.add({ area, base });
    area.addEventListener('input', () => {
      if (worthKeeping(area.value, base)) write(kind, id, area.value); else clear(kind, id);
    });
  };

  /** Vrai s'il existe une saisie non enregistrée dans un champ encore à l'écran. */
  const dirty = () => {
    for (const entry of [...bound]) {
      if (!entry.area.isConnected) bound.delete(entry);
      else if (isDirty(entry.area.value, entry.base)) return true;
    }
    return false;
  };

  /**
   * Le bandeau « Brouillon non enregistré — Reprendre / Jeter », ou null s'il n'y a rien à proposer. `resume(texte)` rouvre la
   * saisie avec le texte ; `back()` rend l'élément qui reçoit le focus quand le bandeau disparaît. `base` : le texte actuel du
   * serveur (un brouillon identique n'est pas proposé).
   */
  const offer = (kind, id, { resume, back = () => null, base = '' }) => {
    const draft = read(kind, id);
    if (!draft) return null;
    if (!worthKeeping(draft.text, base)) { clear(kind, id); return null; }
    const note = el('div', 'at-draft', null, { role: 'status', 'data-role': 'draft-offer', 'data-kind': kind });
    const stop = (event) => event.stopPropagation(); // le bandeau est posé sur un bloc : ses boutons n'ouvrent pas l'édition
    const take = el('button', 'at-btn pri', 'Reprendre', { type: 'button', 'data-act': 'draft-resume' });
    const drop = el('button', 'at-btn ghost', 'Jeter', { type: 'button', 'data-act': 'draft-discard' });
    note.append(el('span', 'at-draft-text', 'Brouillon non enregistré — '), take, drop);
    note.addEventListener('click', stop);
    note.addEventListener('keydown', stop);
    take.addEventListener('click', () => { note.remove(); resume(draft.text); });
    drop.addEventListener('click', () => {
      clear(kind, id);
      const next = back();
      note.remove();
      if (next && next.isConnected) next.focus({ preventScroll: true });
    });
    return note;
  };

  // Quitter la page avec une saisie non enregistrée : le navigateur demande confirmation ; sinon, il ne dit rien.
  const onUnload = (event) => {
    if (!dirty()) return;
    event.preventDefault();
    event.returnValue = '';
  };
  window.addEventListener('beforeunload', onUnload);
  ctx.signal.addEventListener('abort', () => window.removeEventListener('beforeunload', onUnload));

  return { bind, clear, offer, dirty, read };
}
