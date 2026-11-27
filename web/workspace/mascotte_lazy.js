// La porte de la mascotte du chat : le seul fichier que le chat charge d'office.
//
// Le drapeau `cockpit.test_features.mascotte` est éteint par défaut. Tant qu'il l'est, ce fichier ne fait rien et
// n'importe rien : ni `mascotte_dock.js`, ni `mascotte.js`, ni le shader, ni la feuille de style. Allumé, il charge
// le branchement (`mascotte_dock.js`) à la première ouverture du chat et lui relaie ce que le chat sait déjà : un tour
// en cours, du texte ou des fichiers écrits, une question en attente, la fin d'un tour, et, si le vocal est là, la
// dictée (son flux micro déjà ouvert) et la lecture d'un message vocal. Aucun relais n'ouvre ni n'enregistre rien.

export function createMascotteLink({ api, place }) {
  let dock = null;
  let loading = null;
  // Avant la première lecture du drapeau (la veille du fil de la mascotte permanente branche le flux avant l'ouverture
  // du chat) comme pendant elle : on garde les événements du fil, bornés, pour les rejouer au montage.
  let armed = true;
  const facts = { busy: false, waiting: 0 };

  // Les événements du fil qui arrivent pendant le chargement du dock (flux ouvert en même temps) sont rejoués dans
  // l'ordre au montage, bornés : le chargement n'a lieu qu'une fois et seulement drapeau allumé.
  const queue = [];
  function relay(method, ...args) {
    if (dock) dock[method](...args);
    else if (armed && queue.length < 50) queue.push([method, args]);
  }

  async function load() {
    const { createMascotteDock } = await import('./mascotte_dock.js');
    dock = createMascotteDock();
    place(dock.element);
    dock.setBusy(facts.busy);
    dock.setWaiting(facts.waiting);
    for (const [method, args] of queue.splice(0)) dock[method](...args);
  }

  return {
    setBusy(value) { facts.busy = Boolean(value); if (dock) dock.setBusy(facts.busy); },
    setWaiting(count) { facts.waiting = Math.max(0, Number(count) || 0); if (dock) dock.setWaiting(facts.waiting); },
    /** Un appel d'outil du fil (`update` ACP) : seuls ceux qui modifient des fichiers comptent. */
    toolCall(update) { relay('toolCall', update); },
    /** Du texte de l'orchestrateur vient d'arriver. */
    wrote() { relay('wrote'); },
    /** Un tour vient de finir (`replayed` : rejoué à la reconnexion, pas vécu). */
    turnEnded(stopReason, replayed) { relay('turnEnded', stopReason, replayed); },
    micOpened(session) { if (dock) dock.micOpened(session); },
    micClosed() { if (dock) dock.micClosed(); },
    playing(audio) { if (dock) dock.playing(audio); },
    stopped() { if (dock) dock.stopped(); },
    /** À l'ouverture du chat : relit le drapeau ; éteint, rien n'est chargé (et une mascotte montée est retirée). */
    async refresh() {
      let on = false;
      armed = !dock;
      try { on = (await api.testFeatures()).mascotte === true; } catch { on = false; }
      if (!on) { armed = false; queue.length = 0; if (dock) dock.destroy(); return; }
      if (!dock) {
        loading ||= load().finally(() => { loading = null; });
        try { await loading; } catch { armed = false; queue.length = 0; return; } // un module qui ne se charge pas : l'affichage actuel reste
      }
      armed = false;
      if (dock) await dock.refresh();
    },
    destroy() { if (dock) dock.destroy(); },
  };
}
