// Atelier de spec : la réconciliation d'une liste de nœuds, sans DOM (testée par tests/js/atelier_reconcile.test.mjs).
//
// Un rechargement du document ne redessine plus la feuille : chaque enfant d'une zone porte une clé (son identité : un bloc,
// une suggestion, un fil) et une signature (ce qu'il affiche). Pour chaque nouvel enfant :
//   - même clé et même signature : l'ancien nœud reste (focus, sélection, détails dépliés, défilement) ;
//   - même clé et signature différente : le nouveau nœud le remplace, SAUF si l'ancien porte une saisie en cours (`busy`) :
//     il reste, et ce qui l'entoure est mis à jour ;
//   - clé inconnue : le nouveau nœud s'insère.
// Un ancien nœud occupé dont la clé a disparu (le serveur a réécrit ou supprimé le bloc pendant la saisie) n'est jamais jeté :
// il prend la place de son homologue du nouveau document, dit par `stale`.

import { stable } from './atelier-focus.js';

/** Signature réservée : « ce nœud se met à jour lui-même », l'ancien reste toujours. */
export const SELF = '=';

/** Une empreinte courte et stable d'un texte (djb2, en base 36). */
export function hashText(text) {
  let h = 5381;
  const s = String(text);
  for (let i = 0; i < s.length; i += 1) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

/** La signature d'une donnée : indépendante de l'ordre des clés. */
export const sigOf = (value) => hashText(stable(value));

/** La clé d'un bloc non éditable : son genre, l'empreinte de son texte et son rang parmi les identiques de la zone. */
export function plainKey(kind, text, seen) {
  const base = `n:${kind}:${hashText(text)}`;
  const rank = (seen.get(base) || 0) + 1;
  seen.set(base, rank);
  return `${base}:${rank}`;
}

const family = (key) => String(key || '').split(':', 1)[0];

/**
 * Le plan d'une réconciliation. `oldItems` et `newItems` : `{ key, sig, busy }` dans l'ordre du DOM (`busy` ne compte que
 * pour les anciens). Rend `{ slots, drop }` :
 *   - `slots` : l'ordre final ; chaque case dit d'où vient le nœud (`from: 'old' | 'new'`), son rang d'origine (`old`, `new`,
 *     -1 si absent), `changed` (ancien occupé dont la signature diffère : ses propres enfants sont à mettre à jour) et
 *     `stale` (ancien occupé sans clé en commun : `new` est son homologue du nouveau document, ou -1 s'il a disparu) ;
 *   - `drop` : les rangs des anciens nœuds à retirer.
 */
export function planChildren(oldItems, newItems) {
  const oldByKey = new Map();
  oldItems.forEach((item, index) => { if (item.key && !oldByKey.has(item.key)) oldByKey.set(item.key, index); });
  const used = new Set();
  const slots = newItems.map((item, index) => {
    const at = item.key ? oldByKey.get(item.key) : undefined;
    if (at === undefined || used.has(at)) return { from: 'new', old: -1, new: index };
    used.add(at);
    const before = oldItems[at];
    const same = before.sig === item.sig || item.sig === SELF;
    if (before.busy || same) return { from: 'old', old: at, new: index, changed: !same };
    return { from: 'new', old: at, new: index };
  });

  const kept = new Set();
  const orphans = oldItems.map((item, index) => (!used.has(index) && item.busy ? index : -1)).filter((index) => index >= 0);
  for (const index of orphans) {
    const anchor = (step) => {
      for (let j = index + step; j >= 0 && j < oldItems.length; j += step) {
        if (used.has(j)) return slots.findIndex((slot) => slot.old === j);
      }
      return step < 0 ? -1 : slots.length;
    };
    const from = anchor(-1);
    const to = anchor(1);
    let twin = -1;
    for (let p = from + 1; p < to && twin < 0; p += 1) {
      const slot = slots[p];
      if (slot.from === 'new' && slot.old < 0 && !slot.claimed && family(newItems[slot.new].key) === family(oldItems[index].key)) twin = p;
    }
    if (twin >= 0) {
      slots[twin] = { from: 'old', old: index, new: slots[twin].new, stale: true, claimed: true };
    } else {
      slots.splice(from + 1, 0, { from: 'old', old: index, new: -1, stale: true, claimed: true });
    }
    kept.add(index);
  }
  const replaced = new Set(slots.filter((slot) => slot.from === 'new' && slot.old >= 0).map((slot) => slot.old));
  const drop = oldItems.map((_, index) => index).filter((index) => !used.has(index) && !kept.has(index) && !replaced.has(index));
  for (const index of replaced) drop.push(index);
  return { slots, drop: [...new Set(drop)].sort((a, b) => a - b) };
}
