// Atelier de spec : le document suit le fichier, côté logique pure (aucun DOM ; testé par tests/js/atelier_synchro.test.mjs).
//
// L'assistant sonde déjà l'entretien du chantier ; cette lecture porte `fichiers` (une empreinte par fichier de phase, la même
// que celle des phases du document). Si l'une diffère de ce que la page affiche, le fichier a changé hors du cockpit : on
// recharge, puis on compare l'avant et l'après bloc par bloc pour dire ce qui a bougé.

/** Les phases affichées dont le fichier a changé côté serveur (empreinte différente) ; `files` absent : aucune. */
export function staleFiles(doc, files) {
  if (!doc || !Array.isArray(doc.phases) || !files || typeof files !== 'object') return [];
  return doc.phases.filter((phase) => Object.hasOwn(files, phase.id) && files[phase.id] !== (phase.fingerprint || '')).map((p) => p.id);
}

/**
 * Ce qui a changé entre deux états du document. Un bloc est identifié par son texte (son identifiant en dérive) : un texte
 * absent de l'avant est « nouveau » (à surligner), un texte de l'avant absent de l'après est « retiré ». Un bloc réécrit
 * compte pour un (un retiré et un nouveau s'apparient). Rend `{ added: [{ phase, id }], removed, count }`.
 */
export function diffBlocks(before, after) {
  const added = [];
  let removed = 0;
  let paired = 0;
  for (const phase of (after && after.phases) || []) {
    const old = ((before && before.phases) || []).find((p) => p.id === phase.id);
    const pool = new Map();
    for (const block of (old && old.blocks) || []) pool.set(block.text, (pool.get(block.text) || 0) + 1);
    const fresh = [];
    for (const block of phase.blocks || []) {
      const left = pool.get(block.text) || 0;
      if (left > 0) pool.set(block.text, left - 1);
      else fresh.push({ phase: phase.id, id: block.id });
    }
    const gone = [...pool.values()].reduce((n, v) => n + v, 0);
    removed += gone;
    paired += Math.min(gone, fresh.length);
    added.push(...fresh);
  }
  return { added, removed, count: added.length + removed - paired };
}

/** La phrase de la notification, ou vide quand rien n'a changé dans les blocs. */
export function syncMessage(count) {
  if (!count) return '';
  return `Document mis à jour : ${count} bloc${count > 1 ? 's' : ''} modifié${count > 1 ? 's' : ''} par ailleurs`;
}
