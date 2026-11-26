// La porte de la mascotte vocale : le seul fichier que le chat charge d'office.
//
// Le drapeau `cockpit.test_features.mascotte` est éteint par défaut. Tant qu'il l'est, ce fichier ne fait rien et
// n'importe rien : ni `mascotte_dock.js`, ni `mascotte.js`, ni le shader, ni la feuille de style. Allumé, il charge
// le branchement (`mascotte_dock.js`) à la première ouverture du chat et lui relaie ce que le chat sait déjà : un tour en
// cours, la dictée (son flux micro déjà ouvert), la lecture d'un message vocal. Aucun de ces relais n'ouvre ni n'enregistre quoi que ce soit.

export function createMascotteLink({ api, place }) {
  let dock = null;
  let loading = null;
  let busy = false;

  async function load() {
    const { createMascotteDock } = await import('./mascotte_dock.js');
    dock = createMascotteDock({ api });
    place(dock.element);
    dock.setBusy(busy);
  }

  return {
    setBusy(value) { busy = Boolean(value); if (dock) dock.setBusy(busy); },
    micOpened(session) { if (dock) dock.micOpened(session); },
    micClosed() { if (dock) dock.micClosed(); },
    playing(audio) { if (dock) dock.playing(audio); },
    stopped() { if (dock) dock.stopped(); },
    /** À l'ouverture du chat : relit le drapeau ; éteint, rien n'est chargé (et une mascotte montée est retirée). */
    async refresh() {
      let on = false;
      try { on = (await api.testFeatures()).mascotte === true; } catch { on = false; }
      if (!on) { if (dock) dock.destroy(); return; }
      if (!dock) {
        loading ||= load().finally(() => { loading = null; });
        try { await loading; } catch { return; } // un module qui ne se charge pas : l'affichage actuel reste
      }
      if (dock) await dock.refresh();
    },
    destroy() { if (dock) dock.destroy(); },
  };
}
