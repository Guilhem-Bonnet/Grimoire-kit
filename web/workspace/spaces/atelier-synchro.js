// Atelier de spec : le document suit le fichier quand il change hors du cockpit (un agent, un éditeur, git).
//
// Aucune route et aucun minuteur de plus : l'assistant sonde déjà l'entretien du chantier, dont la réponse porte l'empreinte de
// chaque fichier de phase (`fichiers`). Quand l'une diffère de celle que la page affiche, le document est rechargé par le
// redessin réconcilié par bloc (un bloc inchangé garde son nœud ; un bloc en cours d'édition n'est jamais remplacé et reçoit le
// bandeau « modifié ailleurs »), puis les blocs changés sont brièvement surlignés et une notification discrète le dit.
// Onglet caché : rien. Au retour, la prochaine réponse du pouls décide.

import { diffBlocks, staleFiles, syncMessage } from './atelier-synchro-logic.js';
import { flash } from './atelier-liens.js';

/**
 * `hooks` : `doc()` (le document affiché), `reload()` (recharge et redessine ; ne rend rien), `sheet`, `notify(message, options)`.
 * Rend `{ check(fichiers) }`, à appeler à chaque réponse du pouls de l'assistant.
 */
export function createSync(ctx, hooks) {
  let running = false;

  const check = async (files) => {
    if (running || ctx.signal.aborted || document.hidden) return;
    if (!staleFiles(hooks.doc(), files).length) return;
    running = true;
    try {
      const before = hooks.doc();
      await hooks.reload();
      const after = hooks.doc();
      if (ctx.signal.aborted || after === before) return; // le rechargement a échoué : le prochain pouls réessaie
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
