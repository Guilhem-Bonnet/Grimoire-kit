// Atelier de spec : dire ce qui est attendu, rubrique par rubrique, à la place du seul « À compléter — … ».
//
// Une rubrique vide montre son invite (ce qu'on y met, un exemple, son statut tiré du contrôle `cadrage check`) et un geste :
// « Sans objet pour ce besoin », avec une raison facultative. Ce geste n'ouvre aucun nouveau chemin d'écriture : il réécrit le
// bloc de gabarit par la route d'édition de bloc existante (contrôle de conflit compris), en une ligne qui commence par le gabarit.
// Le contrôle de validation l'ignore donc exactement comme le gabarit lui-même : une rubrique nécessaire marquée sans objet reste
// « à compléter » pour lui, et la page le dit. « Remettre à compléter » réécrit le gabarit du kit, tel que le serveur le donne.
// Les textes viennent du serveur (`src/grimoire/core/cadrage_guidance.py`) : rien n'est inventé ici.

import { isPageMarker, markerText, notApplicableNote, statusInfo } from './atelier-invites-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/**
 * `hooks` : `reload(message, kind)` relit le document. Rend `{ node(block, rub) }` : le contenu d'un bloc resté au gabarit
 * (une invite, ou la ligne repliée d'une rubrique « sans objet »).
 */
export function createInvites(ctx, slug, hooks) {
  /** Réécrit le bloc de gabarit ; un conflit ou un refus se dit à côté du geste, sans rien perdre. */
  const write = async (block, text, done, error, buttons, form = null) => {
    buttons.forEach((b) => { b.disabled = true; });
    error.hidden = true;
    try {
      await ctx.api.atelierBloc(slug, block.id, text, block);
      if (ctx.signal.aborted) return;
      if (form) form.remove(); // plus de saisie à garder : le rechargement peut remplacer la rubrique
      await hooks.reload(done, 'ok');
    } catch (cause) {
      if (ctx.signal.aborted) return;
      buttons.forEach((b) => { b.disabled = false; });
      if (cause.code === 409) {
        await hooks.reload('La rubrique a changé entre-temps : le document a été rechargé, rien n’a été écrit.', 'warn');
        return;
      }
      error.textContent = cause.message || String(cause);
      error.hidden = false;
    }
  };

  /** Le formulaire « sans objet » : une raison facultative, puis la ligne de gabarit. Il tient dans la rubrique. */
  const naForm = (block, rub, onClose) => {
    const input = el('input', 'at-input', null, { type: 'text', 'aria-label': 'Pourquoi cette rubrique est sans objet (facultatif)', placeholder: 'Pourquoi ? (facultatif)', maxlength: '200', 'data-role': 'na-reason' });
    const confirm = el('button', 'at-btn pri', 'Marquer sans objet', { type: 'button', 'data-act': 'na-confirm' });
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button', 'data-act': 'na-cancel' });
    const error = el('p', 'at-error', '', { role: 'alert' });
    error.hidden = true;
    const form = el('div', 'at-form at-inv-form', null, { 'data-role': 'na-form' });
    form.append(input, confirm, cancel, error);
    const go = () => write(block, markerText(rub, input.value), `« ${rub.section} » marquée sans objet pour ce besoin.`, error, [confirm, cancel], form);
    confirm.addEventListener('click', go);
    cancel.addEventListener('click', onClose);
    form.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      else if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); go(); }
    });
    form.addEventListener('click', (event) => event.stopPropagation()); // ne rouvre pas l'édition du bloc
    return { form, input };
  };

  /** Rubrique vide : ce qu'on y met, un exemple, son statut, et les deux gestes. */
  const invite = (block, rub) => {
    const info = statusInfo(rub);
    const node = el('div', 'at-placeholder at-empty-slot at-invite', null, { 'data-role': 'empty-slot', 'data-invite': rub.section, 'data-statut': rub.statut });
    const status = el('span', 'at-inv-statut', info.label, { 'data-level': info.level, 'data-role': 'invite-statut', title: info.why });
    const head = el('p', 'at-inv-head');
    head.append(status, el('span', 'at-inv-what', rub.quoi, { 'data-role': 'invite-quoi' }));
    const example = el('p', 'at-inv-ex', null, { 'data-role': 'invite-exemple' });
    example.append(el('span', 'at-inv-ex-tag', 'Exemple'), ` ${rub.exemple}`);
    node.append(head, example);
    if (rub.etat === 'je-ne-sais-pas') node.append(el('p', 'at-muted at-inv-note', 'Noté « je ne sais pas » : l’hypothèse est dans « Questions ouvertes ». Tu peux préciser ici quand tu sauras.'));
    const write_ = el('button', 'at-btn', 'Écrire ici', { type: 'button', 'data-act': 'invite-write' }); // le clic remonte au bloc : l'éditeur s'ouvre
    const skip = el('button', 'at-btn ghost', 'Sans objet pour ce besoin', { type: 'button', 'data-act': 'na-open', title: 'Cette rubrique ne concerne pas ce chantier : elle se replie et sort de ce qu’il reste à faire.' });
    const actions = el('div', 'at-inv-actions');
    actions.append(write_, skip);
    node.append(actions);
    skip.addEventListener('click', (event) => {
      event.stopPropagation();
      if (node.querySelector('.at-inv-form')) return;
      const { form, input } = naForm(block, rub, () => { form.remove(); skip.focus(); });
      actions.after(form);
      input.focus();
    });
    return node;
  };

  /** Rubrique « sans objet » : repliée sur une ligne, avec sa raison et le moyen de la rouvrir. */
  const notApplicable = (block, rub) => {
    const node = el('div', 'at-placeholder at-empty-slot at-inv-na', null, { 'data-role': 'empty-slot', 'data-invite': rub.section, 'data-etat': 'sans-objet', 'data-statut': rub.statut });
    node.append(el('span', 'at-inv-na-tag', 'Sans objet'), el('span', 'at-inv-na-text', notApplicableNote(rub), { 'data-role': 'na-note' }));
    if (isPageMarker(rub, block.text)) { // la ligne de l'entretien se défait par l'entretien, pas d'ici
      const error = el('p', 'at-error', '', { role: 'alert' });
      error.hidden = true;
      const back = el('button', 'at-btn ghost', 'Remettre à compléter', { type: 'button', 'data-act': 'na-undo' });
      back.addEventListener('click', (event) => {
        event.stopPropagation();
        write(block, rub.gabarit, `« ${rub.section} » remise à compléter.`, error, [back]);
      });
      node.append(back, error);
    }
    return node;
  };

  return { node: (block, rub) => (rub.etat === 'sans-objet' ? notApplicable(block, rub) : invite(block, rub)) };
}
