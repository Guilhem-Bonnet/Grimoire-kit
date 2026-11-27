// Atelier de spec : le document suit le fichier quand il change hors du cockpit (un agent, un éditeur, git).
//
// Aucune route et aucun minuteur de plus : l'assistant sonde déjà l'entretien du chantier, dont la réponse porte l'empreinte de
// chaque fichier de phase (`fichiers`). Quand l'une diffère de celle que la page affiche, le document est rechargé par le
// redessin réconcilié par bloc (un bloc inchangé garde son nœud ; un bloc en cours d'édition n'est jamais remplacé et reçoit le
// bandeau « modifié ailleurs »), puis les blocs changés sont brièvement surlignés et une notification discrète le dit.
// Onglet caché : rien. Au retour, la prochaine réponse du pouls décide.

import { annexMessage, diffBlocks, staleAnnexes, staleFiles, syncMessage } from './atelier-synchro-logic.js';
import { flash } from './atelier-liens.js';

/**
 * `hooks` : `doc()` (le document affiché), `reload()` (recharge et redessine ; ne rend rien), `sheet`, `notify(message, options)`.
 * Rend `{ check(fichiers) }`, à appeler à chaque réponse du pouls de l'assistant.
 */
export function createSync(ctx, hooks) {
  let running = false;

  // `annexes` : l'empreinte des suggestions et des commentaires (un dépôt de l'agent plus court que le pouls ne change ni le
  // tour vu en cours ni les angles couverts). `force` : un tour d'agent vient de finir, on recharge dans tous les cas.
  // `started` : l'instant où la lecture du pouls est partie. Une lecture partie AVANT le dernier geste de cette page (document relu
  // ou réécrit par elle) ne dit rien d'un dépôt extérieur : elle est ignorée, la suivante tranche (elle compare à l'état rendu par
  // le geste, donc une écriture extérieure tombée entre-temps reste détectée).
  const check = async (files, annexes, { force = false, started = Infinity } = {}) => {
    if (ctx.signal.aborted) return;
    // Même pour `force` : un tour qui « change » à cause d'une réponse guidée de cette page (angles couverts) est son propre geste,
    // dont le rechargement suit ; la fin d'un tour d'agent déposée avant ce geste y est lue aussi.
    if (hooks.docAt && started < hooks.docAt()) return;
    if (running && force) { await hooks.reload(); return; } // un rechargement est déjà en cours : celui-ci reste dû
    if (running || document.hidden) return;
    const stale = staleFiles(hooks.doc(), files).length > 0;
    if (!force && !stale && !staleAnnexes(hooks.doc(), annexes).length) return;
    running = true;
    try {
      const before = hooks.doc();
      await hooks.reload();
      const after = hooks.doc();
      if (ctx.signal.aborted || after === before) return; // le rechargement a échoué : le prochain pouls réessaie
      const arrived = annexMessage(before, after);
      if (arrived) hooks.notify(arrived, { kind: 'ok' });
      if (force) return; // la fin d'un tour d'agent se recharge sans le message des blocs, comme avant
      const diff = diffBlocks(before, after);
      const message = syncMessage(diff.count);
      if (!message) return;
      for (const { id } of diff.added) flash(ctx, hooks.sheet.querySelector(`[data-block="${CSS.escape(id)}"]`), 'is-ext-changed', 3200);
      hooks.notify(message, { kind: 'ok' });
    } finally {
      running = false;
    }
  };
  return { check };
}
