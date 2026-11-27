// Atelier de spec : prévenir avant de défaire la validation d'une exigence.
//
// Modifier le texte d'une exigence que la MOA a validée fait perdre la validation (elle est liée à l'empreinte du texte) ; le serveur
// reste sûr, mais l'utilisateur ne le savait pas. Avant d'enregistrer (édition d'un bloc, case cochée, suggestion acceptée, bloc
// restauré), `ask` compare l'exigence avant/après (voir `atelier-devalidation-logic.js`) et, si elle est validée, demande
// confirmation : le focus est sur « Annuler », la commande de revalidation est affichée. Rien n'est bloqué : trace indisponible,
// l'enregistrement passe et `after` le dit discrètement. Aucune route de plus que la lecture de la trace déjà utilisée par la page ;
// ni les gardes ni le gate ne sont touchés.

import {
  afterMessage, approveCommand, changedRequirements, confirmLine, unknownMessage, validationOf,
} from './atelier-devalidation-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/**
 * `hooks` : `host` (où poser la boîte), `trace()` (la dernière trace lue, ou nul), `refresh()` (relit la trace et redessine les
 * lignes d'état et la carte), `notify(message, options)`. Rend `{ ask(before, after), after(guard) }`.
 */
export function createDevalidation(ctx, slug, hooks) {
  /** La boîte de confirmation : promesse de booléen (« Modifier quand même » : vrai ; « Annuler », Échap : faux). */
  const confirmBox = (items) => new Promise((resolve) => {
    const dialog = el('dialog', 'at-hist-confirm', null, { 'data-role': 'devalidation-confirm', 'aria-labelledby': 'at-dv-title' });
    const form = el('form', 'at-hist-confirm-form', null, { method: 'dialog' });
    form.append(el('h3', 'at-hist-confirm-title', items.length > 1 ? 'Modifier des exigences validées ?' : `Modifier ${items[0].req}, déjà validée ?`, { id: 'at-dv-title' }));
    for (const item of items) {
      form.append(el('p', 'at-hist-confirm-line', confirmLine(item), { 'data-role': 'devalidation-line' }));
      form.append(el('code', 'at-live-cmd', approveCommand(item.req, slug), { 'data-role': 'devalidation-command' }));
    }
    const actions = el('div', 'at-hist-confirm-actions');
    const no = el('button', 'at-btn', 'Annuler', { type: 'submit', value: 'no', 'data-role': 'devalidation-annuler' });
    const ok = el('button', 'at-btn pri', 'Modifier quand même', { type: 'submit', value: 'ok', 'data-role': 'devalidation-confirmer' });
    actions.append(no, ok);
    form.append(actions);
    dialog.append(form);
    dialog.addEventListener('close', () => { const answer = dialog.returnValue === 'ok'; dialog.remove(); resolve(answer); });
    hooks.host.append(dialog);
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    no.focus(); // par défaut, le geste prudent
  });

  /** L'état connu au moment du geste : la trace fraîche si elle se lit, sinon la dernière lue, sinon rien. */
  const currentTrace = async () => {
    try {
      return await ctx.api.atelierTrace(slug);
    } catch {
      return hooks.trace();
    }
  };

  /**
   * Avant d'écrire `after` à la place de `before` : `{ go, validated, unknown }`. `go` faux : la personne a renoncé, rien n'est écrit.
   * Un changement qui ne touche pas l'exigence (autre ligne, espace final, case cochée) ne demande rien et ne lit rien.
   */
  const ask = async (before, after) => {
    const reqs = changedRequirements(before, after);
    if (!reqs.length) return { go: true, validated: [], unknown: [] };
    const state = validationOf(reqs, await currentTrace());
    if (state.validated.length && !(await confirmBox(state.validated))) return { go: false, ...state };
    return { go: true, ...state };
  };

  /** Après l'écriture : la trace est relue (lignes d'état et carte disent « Non validée ») et la commande est rappelée. */
  const after = async (guard) => {
    if (!guard || (!guard.validated.length && !guard.unknown.length)) return;
    try {
      await hooks.refresh();
    } catch {
      // la ligne d'état se mettra à jour au prochain pouls
    }
    if (ctx.signal.aborted) return;
    if (guard.validated.length) hooks.notify(afterMessage(guard.validated.map((v) => v.req), slug), { kind: 'warn' });
    else hooks.notify(unknownMessage(guard.unknown, slug), { kind: 'warn' });
  };

  return { ask, after };
}
