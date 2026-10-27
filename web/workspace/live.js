// Le pouls de la coque (UI-3) : l'actualisation sans rechargement, et les mots
// qui disent « il y a combien de temps » et « combien ça a coûté ».
//
// Une seule source pour toute la coque, jamais une par espace. D'abord le flux
// (P10) : un `EventSource` sur `/api/workspace/stream`, que le serveur nourrit
// dès que l'empreinte bouge (ledger, journal de session, proposition) — moins
// de 2 s après l'écriture, sans attendre l'intervalle. À défaut (navigateur sans
// `EventSource`, hôte qui ne sert pas le flux), le sondage de `/api/workspace/pulse`
// (une empreinte calculée par `stat` côté serveur, voir workspace_pulse.py), qui
// ne relit rien tant que l'empreinte ne bouge pas. Dans les deux cas, quand elle
// bouge, les espaces qui se sont inscrits (`ctx.live.on`) rechargent leurs propres
// données — la coque ne sait pas ce qu'ils affichent.
//
// Règles tenues ici, prouvées dans tests/e2e/test_workspace_live_pilot.py et
// tests/e2e/test_workspace_live_stream.py :
//   - l'intervalle est celui que le serveur déclare (`interval_seconds`), pas une
//     constante de l'interface ; avant la première réponse, un défaut prudent. En
//     flux, c'est la cadence du battement qui garde « mis à jour il y a N s » vrai ;
//   - un onglet masqué ne fait AUCUNE requête (`visibilitychange` : le flux est
//     fermé, la requête en cours annulée, le minuteur coupé) ; revenu visible, le
//     flux se rouvre (ou un sondage part) tout de suite ;
//   - deux sondages ne se chevauchent jamais (minuteur ré-armé APRÈS la réponse) ;
//   - une API injoignable se voit (état `error` + durée), elle ne gèle pas
//     silencieusement un « mis à jour il y a 2 s » mensonger ;
//   - les rechargements des espaces sont regroupés (P79) : un changement isolé
//     part tout de suite, une rafale (huit workers qui écrivent) en donne au plus
//     un par `MIN_RELOAD_MS`, jamais deux en même temps, et le dernier pouls est
//     toujours rechargé.

import { api, host } from './api.js';

const DEFAULT_INTERVAL_MS = 5000;
const FIRST_PULSE_TIMEOUT_MS = 5000;
// Écart minimal entre deux rechargements des espaces : chacun relit tâches, attention et sessions.
const MIN_RELOAD_MS = 3000;

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
    // 'stream' (flux, P10) | 'poll' (sondage) : le flux d'abord, le sondage s'il manque.
    transport: typeof EventSource === 'function' ? 'stream' : 'poll',
    intervalMs: DEFAULT_INTERVAL_MS,
    fingerprint: null,
    lastOkAt: null,
    failingSince: null,
    requests: 0,
  };
  let handlers = [];
  let timer = null;
  let inflight = null;
  let source = null;
  let running = false;
  // Regroupement des rechargements (P79) : le dernier pouls changé attend son tour.
  let pendingPulse = null;
  let reloadTimer = null;
  let reloading = false;
  let lastReloadAt = 0;
  // Résout l'attente de `start()` au premier pouls reçu, quel que soit le transport.
  let settle = () => {};

  const scope = () => (host.kind === 'cockpit' ? 'portfolio' : undefined);

  const notifyState = (status) => {
    state.status = status;
    if (onState) onState(state);
  };

  /** Un pouls, sondé ou poussé : même traitement. */
  function receive(pulse) {
    const seconds = Number(pulse.interval_seconds);
    if (seconds >= 1) state.intervalMs = seconds * 1000;
    const first = state.fingerprint === null;
    state.lastOkAt = Date.now();
    state.failingSince = null;
    const changed = Boolean(pulse.fingerprint) && pulse.fingerprint !== state.fingerprint;
    if (changed) state.fingerprint = pulse.fingerprint;
    if (onDecisions && pulse.decisions) onDecisions(pulse.decisions);
    // Le tout premier pouls fixe la référence : l'espace vient d'être chargé,
    // il n'a rien à recharger.
    if (changed && !first) requestReload(pulse);
    notifyState('ok');
  }

  function requestReload(pulse) {
    pendingPulse = pulse;
    if (reloadTimer !== null || reloading) return;
    const wait = Math.max(0, lastReloadAt + MIN_RELOAD_MS - Date.now());
    reloadTimer = setTimeout(runReload, wait);
  }

  async function runReload() {
    reloadTimer = null;
    // Onglet masqué : le pouls attend, il sera rechargé au retour (son empreinte est déjà connue,
    // aucun pouls ne la redira).
    if (!running || hidden()) return;
    const pulse = pendingPulse;
    pendingPulse = null;
    if (!pulse) return;
    reloading = true;
    lastReloadAt = Date.now();
    try {
      await Promise.allSettled([...handlers].map(async (handler) => {
        try { await handler(pulse); } catch (error) { console.error('actualisation de l\'espace :', error); }
      }));
    } finally {
      reloading = false;
      if (pendingPulse) requestReload(pendingPulse);
    }
  }

  function clearReload() {
    if (reloadTimer !== null) { clearTimeout(reloadTimer); reloadTimer = null; }
    pendingPulse = null;
  }

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
  }

  function schedule() {
    clearTimer();
    if (!running || hidden() || state.transport !== 'poll') return;
    timer = setTimeout(() => { poll(); }, state.intervalMs);
  }

  async function poll() {
    clearTimer();
    if (!running || hidden() || inflight) return;
    const controller = new AbortController();
    inflight = controller;
    state.requests += 1;
    try {
      receive(await api.pulse(state.fingerprint, scope(), controller.signal));
    } catch (error) {
      if (error && error.name === 'AbortError') return; // onglet masqué : ce n'est pas une panne
      if (state.failingSince === null) state.failingSince = Date.now();
      notifyState('error');
    } finally {
      inflight = null;
      settle();
      schedule();
    }
  }

  function closeStream() {
    if (source) { source.close(); source = null; }
  }

  function openStream() {
    if (source || !running || hidden()) return;
    const stream = new EventSource(api.pulseStreamUrl(state.fingerprint, scope()));
    let heard = false;
    source = stream;
    state.requests += 1;
    stream.addEventListener('pulse', (event) => {
      if (source !== stream) return;
      let pulse;
      try { pulse = JSON.parse(event.data); } catch { return; }
      heard = true;
      receive(pulse);
      settle();
    });
    stream.onerror = () => {
      if (source !== stream) return;
      if (!heard || stream.readyState === EventSource.CLOSED) {
        // Jamais entendu : l'hôte ne sert pas le flux. Fermé : le navigateur a
        // renoncé à se reconnecter. Dans les deux cas, on sonde.
        closeStream();
        state.transport = 'poll';
        poll();
        return;
      }
      // Coupé après avoir parlé : le navigateur se reconnecte seul (`retry`).
      // En attendant, la panne se voit.
      if (state.failingSince === null) state.failingSince = Date.now();
      notifyState('error');
    };
  }

  function resume() {
    if (state.transport === 'stream') openStream();
    else poll();
  }

  function onVisibility() {
    if (!running) return;
    if (hidden()) {
      clearTimer();
      if (inflight) inflight.abort();
      closeStream();
      notifyState('paused');
    } else {
      resume();
      if (pendingPulse) requestReload(pendingPulse);
    }
  }

  return {
    state,
    /** Premier pouls (attendu par l'appelant), puis le flux ou la boucle. */
    async start() {
      if (running) return;
      running = true;
      document.addEventListener('visibilitychange', onVisibility);
      if (hidden()) { notifyState('paused'); return; }
      await new Promise((resolve) => {
        settle = () => { settle = () => {}; resolve(); };
        // Un flux muet (intermédiaire qui retient la réponse) ne bloque pas la coque.
        setTimeout(() => settle(), FIRST_PULSE_TIMEOUT_MS);
        resume();
      });
    },
    stop() {
      running = false;
      clearTimer();
      clearReload();
      if (inflight) inflight.abort();
      closeStream();
      document.removeEventListener('visibilitychange', onVisibility);
    },
    /** Inscrit ce que l'espace courant fait quand le pouls annonce un changement. */
    on(handler) { handlers.push(handler); },
    /** La coque l'appelle à chaque changement d'espace : un espace démonté n'écoute plus. */
    clear() { handlers = []; clearReload(); },
  };
}
