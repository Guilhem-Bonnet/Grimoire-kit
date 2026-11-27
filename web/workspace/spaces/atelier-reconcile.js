// Atelier de spec : redessiner une zone de la feuille sans toucher à ce qui n'a pas changé ni à ce que l'on est en train d'écrire.
//
// Les nœuds portent leur identité (`data-rk`) et leur signature (`data-rs`) ; un conteneur dont les enfants sont eux-mêmes
// réconciliés porte `data-rc`. La décision (quel nœud garde sa place) est dans `atelier-reconcile-logic.js`, sans DOM.

import { captureFocus, restoreFocus } from './atelier-focus.js';
import { planChildren } from './atelier-reconcile-logic.js';

/** Ce qui montre une saisie en cours : bloc en édition, nouveau commentaire, réponse, confirmation, refus, rattachement. */
export const BUSY = '.is-editing, .at-cm-new, .at-cm-reply, .at-cm-confirm, .at-cm-pick, .at-refuse, .at-inv-form';

/** Pose l'identité et la signature d'un nœud (rend le nœud). */
export function tag(node, key, sig) {
  node.setAttribute('data-rk', key);
  node.setAttribute('data-rs', sig);
  return node;
}

/** Le nœud ne porte plus de saisie à garder : au prochain dessin il est remplacé, même si ses données n'ont pas changé. */
export function release(node) {
  node.setAttribute('data-rs', '~');
  for (let up = node.parentElement && node.parentElement.closest('[data-rk]'); up; up = up.parentElement && up.parentElement.closest('[data-rk]')) {
    up.setAttribute('data-rs', '~');
  }
}

const isBusy = (node) => node.matches(BUSY) || Boolean(node.querySelector(BUSY));
const describe = (node, busy) => ({ key: node.getAttribute('data-rk') || '', sig: node.getAttribute('data-rs') || '', busy: busy && isBusy(node) });

/**
 * Met `oldParent` à jour d'après `newParent` (un nœud fraîchement construit, jeté ensuite) en gardant les anciens nœuds
 * inchangés ou occupés. `hooks.stale(oldNode, newNode | null)` : un nœud occupé dont l'identité a disparu du document
 * (réécrit ou supprimé ailleurs) ; `newNode` est son homologue du nouveau document.
 */
export function reconcileChildren(oldParent, newParent, hooks = {}) {
  const oldNodes = [...oldParent.children];
  const newNodes = [...newParent.children];
  const plan = planChildren(oldNodes.map((node) => describe(node, true)), newNodes.map((node) => describe(node, false)));
  const snap = captureFocus(oldParent);
  for (const index of plan.drop) oldNodes[index].remove();
  const final = plan.slots.map((slot) => (slot.from === 'old' ? oldNodes[slot.old] : newNodes[slot.new]));
  let ref = oldParent.firstElementChild;
  for (const node of final) {
    if (node === ref) ref = ref.nextElementSibling;
    else oldParent.insertBefore(node, ref);
  }
  plan.slots.forEach((slot, at) => {
    if (slot.from !== 'old' || !(slot.changed || slot.stale)) return;
    const node = final[at];
    const twin = slot.new >= 0 ? newNodes[slot.new] : null;
    if (slot.stale && hooks.stale) hooks.stale(node, twin);
    if (!twin || !node.hasAttribute('data-rc')) return;
    reconcileChildren(node, twin, hooks); // le conteneur occupé : ses enfants se mettent à jour, la saisie reste
    node.setAttribute('data-rs', twin.getAttribute('data-rs') || '');
    if (slot.stale) node.setAttribute('data-rk', twin.getAttribute('data-rk') || '');
  });
  restoreFocus(oldParent, snap);
}
