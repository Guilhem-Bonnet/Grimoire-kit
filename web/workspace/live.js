// Le pouls de la coque (UI-3) : l'actualisation sans rechargement, et les mots
// qui disent « il y a combien de temps » et « combien ça a coûté ».
//
// Une seule boucle de sondage pour toute la coque, jamais une par espace : elle
// interroge `/api/workspace/pulse` (une empreinte calculée par `stat` côté
// serveur, voir workspace_pulse.py), et ne relit rien tant que l'empreinte ne
// bouge pas. Quand elle bouge, les espaces qui se sont inscrits (`ctx.live.on`)
// rechargent leurs propres données — la coque ne sait pas ce qu'ils affichent.
//
// Règles tenues ici, chacune prouvée dans tests/e2e/test_workspace_live_pilot.py :
//   - l'intervalle est celui que le serveur déclare (`interval_seconds`), pas une
//     constante de l'interface ; avant la première réponse, un défaut prudent ;
//   - un onglet masqué ne fait AUCUNE requête (`visibilitychange` : la requête en
//     cours est annulée, le minuteur est coupé) ; revenu visible, un sondage
//     part tout de suite ;
//   - deux sondages ne se chevauchent jamais (minuteur ré-armé APRÈS la réponse) ;
//   - une API injoignable se voit (état `error` + durée), elle ne gèle pas
//     silencieusement un « mis à jour il y a 2 s » mensonger.

import { api, host } from './api.js';

const DEFAULT_INTERVAL_MS = 5000;

const hidden = () => document.visibilityState === 'hidden';

/** « il y a 4 s », « il y a 12 min », « il y a 3 h », « il y a 2 j ». */
export function agoText(milliseconds) {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return `il y a ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `il y a ${hours} h` : `il y a ${Math.round(hours / 24)} j`;
}

/** Une durée en secondes, ou « inconnue » : jamais « 0 » pour ce qu'on ne sait pas calculer. */
export function durationText(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return 'inconnue';
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total} s`;
  const minutes = Math.round(total / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * Un coût en trois états (W1-01) : `inconnu`, « ≥ X USD » (borne basse : certains
 * appels n'ont pas de prix), « X USD » (tout est chiffré). Jamais « 0 » pour
 * un coût que personne n'a mesuré, et jamais une absence de champ lue comme zéro.
 */
export function costText(cost) {
  if (!cost || cost.status === 'unknown' || cost.usd === null || cost.usd === undefined) return 'inconnu';
  const amount = `${Number(cost.usd).toFixed(4)} USD`;
  return cost.status === 'lower_bound' ? `≥ ${amount}` : amount;
}

/** Remet à jour les `<time data-since="<iso>">` d'un arbre : « il y a 40 s » reste vrai entre deux sondages. */
export function refreshSince(root = document) {
  const now = Date.now();
  for (const node of root.querySelectorAll('[data-since]')) {
    const then = Date.parse(node.dataset.since || '');
    if (!Number.isNaN(then)) node.textContent = agoText(now - then);
  }
}

export function createLive({ onDecisions, onState } = {}) {
  const state = {
    // 'idle' | 'ok' | 'paused' | 'error' — lu par la coque pour écrire l'horodatage.
    status: 'idle',
    intervalMs: DEFAULT_INTERVAL_MS,
    fingerprint: null,
    lastOkAt: null,
    failingSince: null,
    requests: 0,
  };
  let handlers = [];
  let timer = null;
  let inflight = null;
  let running = false;

  const notifyState = (status) => {
    state.status = status;
    if (onState) onState(state);
  };

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
  }

  function schedule() {
    clearTimer();
    if (!running || hidden()) return;
    timer = setTimeout(() => { poll(); }, state.intervalMs);
  }

  async function poll() {
    clearTimer();
    if (!running || hidden() || inflight) return;
    const controller = new AbortController();
    inflight = controller;
    state.requests += 1;
    try {
      const scope = host.kind === 'cockpit' ? 'portfolio' : undefined;
      const pulse = await api.pulse(state.fingerprint, scope, controller.signal);
      const seconds = Number(pulse.interval_seconds);
      if (seconds >= 1) state.intervalMs = seconds * 1000;
      const first = state.fingerprint === null;
      state.lastOkAt = Date.now();
      state.failingSince = null;
      if (pulse.changed) {
        state.fingerprint = pulse.fingerprint;
        if (onDecisions && pulse.decisions) onDecisions(pulse.decisions);
        // Le tout premier sondage fixe la référence : l'espace vient d'être
        // chargé, il n'a rien à recharger.
        if (!first) {
          for (const handler of [...handlers]) {
            try { handler(pulse); } catch (error) { console.error('actualisation de l\'espace :', error); }
          }
        }
      }
      notifyState('ok');
    } catch (error) {
      if (error && error.name === 'AbortError') return; // onglet masqué : ce n'est pas une panne
      if (state.failingSince === null) state.failingSince = Date.now();
      notifyState('error');
    } finally {
      inflight = null;
      schedule();
    }
  }

  function onVisibility() {
    if (!running) return;
    if (hidden()) {
      clearTimer();
      if (inflight) inflight.abort();
      notifyState('paused');
    } else {
      poll();
    }
  }

  return {
    state,
    /** Premier sondage (attendu par l'appelant), puis la boucle. */
    async start() {
      if (running) return;
      running = true;
      document.addEventListener('visibilitychange', onVisibility);
      if (hidden()) { notifyState('paused'); return; }
      await poll();
    },
    stop() {
      running = false;
      clearTimer();
      if (inflight) inflight.abort();
      document.removeEventListener('visibilitychange', onVisibility);
    },
    /** Inscrit ce que l'espace courant fait quand le pouls annonce un changement. */
    on(handler) { handlers.push(handler); },
    /** La coque l'appelle à chaque changement d'espace : un espace démonté n'écoute plus. */
    clear() { handlers = []; },
  };
}
