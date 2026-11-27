// Le branchement de la mascotte sur le chat (#1343, mascotte #1197).
//
// Fonction en test : éteinte par défaut (`cockpit.test_features.mascotte`). Le drapeau est lu par `mascotte_lazy.js`,
// qui ne charge ce module qu'allumé. Allumée, la mascotte est la présence de l'assistant dans le chat, vocal installé
// ou non : elle vit sur les états du fil et un libellé visible dit ce qu'elle fait. Éteinte, ni ce module, ni le rendu,
// ni le shader, ni la feuille de style ne sont chargés.
//
// Ce que ce module reçoit du chat, il ne le garde que le temps de s'en servir :
// - `setBusy(bool)` : un tour de l'orchestrateur est en cours ;
// - `wrote()` / `toolCall(update)` : du texte vient d'arriver / un appel d'outil du fil (« écrit » s'il modifie des fichiers) ;
// - `setWaiting(n)` : n questions ou demandes de permission attendent la personne ;
// - `turnEnded(stopReason, replayed)` : un tour finit (« a terminé » après une fin normale vécue) ;
// - `micOpened({ context, source })` : le flux micro que la DICTÉE a déjà ouvert. Un analyseur se branche sur sa
//   source ; ce module n'ouvre jamais le micro et ne le garde pas (`micClosed`, appelé à la fermeture de la dictée,
//   lâche l'analyseur) ;
// - `playing(audio)` : l'élément audio du message vocal en lecture. Un analyseur le lit par `captureStream()`, une
//   copie du flux : le son n'est jamais redirigé, donc jamais coupé si l'analyse échoue. Sans flux exploitable
//   (synthèse du navigateur, Safari), la voix est simulée.
// Il ne reçoit, n'enregistre et n'envoie jamais de son : seulement des niveaux, sous forme de nombres.
// Les formes « propose » (suggestion de l'Atelier), « délègue » et « attend » (sous-agents, workers) ne sont pas
// branchées : le fil du chat n'expose aucun événement qui les dise.

import {
  bandEdges, bandsFromSpectrum, captionFor, deliversOn, isWritingCall, levelFromSamples, pickAction, simulatedBands, simulatedVoice,
} from './mascotte_logic.js';

const STYLESHEET = new URL('./mascotte.css', import.meta.url).href;
const FFT_SIZE = 1024;
/** Combien de temps « a terminé » reste montré, et combien de temps un morceau de texte reçu compte comme « écrit ». */
const DELIVERED_MS = 2400;
const WROTE_MS = 1500;

function ensureStylesheet() {
  if (document.querySelector(`link[href="${STYLESHEET}"]`)) return Promise.resolve();
  return new Promise((resolve) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = STYLESHEET;
    link.addEventListener('load', () => resolve());
    link.addEventListener('error', () => resolve());
    document.head.append(link);
    setTimeout(resolve, 1500); // une feuille lente ne retient pas la mascotte
  });
}

/** Un lecteur de niveaux sur un `AnalyserNode` : `() => { level, bands }`. */
function analyserSampler(analyser, sampleRate) {
  const time = new Float32Array(analyser.fftSize);
  const spectrum = new Uint8Array(analyser.frequencyBinCount);
  const edges = bandEdges(sampleRate, analyser.frequencyBinCount);
  const bands = new Float32Array(8);
  return () => {
    analyser.getFloatTimeDomainData(time);
    analyser.getByteFrequencyData(spectrum);
    return { level: levelFromSamples(time), bands: bandsFromSpectrum(spectrum, edges, bands) };
  };
}

function makeAnalyser(context) {
  const analyser = context.createAnalyser();
  analyser.fftSize = FFT_SIZE;
  analyser.smoothingTimeConstant = 0.55;
  return analyser;
}

/** Une copie du flux de l'élément audio (jamais une redirection de sa sortie), ou null si le navigateur n'en donne pas. */
function captured(audio) {
  try {
    const stream = typeof audio.captureStream === 'function' ? audio.captureStream()
      : (typeof audio.mozCaptureStream === 'function' ? audio.mozCaptureStream() : null);
    return stream && stream.getAudioTracks().length > 0 ? stream : null;
  } catch { return null; }
}

export function createMascotteDock() {
  const element = document.createElement('figure');
  element.className = 'chat-mascotte';
  element.hidden = true;
  // Le canevas (ou la pastille de repli) et le libellé n'existent que mascotte montée : éteinte, la bande est cachée.
  let stage = null;
  let caption = null;

  const state = { recording: false, playing: false, busy: false, editing: false, wrote: false, waiting: 0, delivered: false };
  const timers = { wrote: 0, delivered: 0 };
  const editCalls = new Map(); // appels d'outil qui modifient des fichiers, en cours
  let mascotte = null;
  let epoch = 0; // invalide un montage devenu inutile (onglet fermé pendant le chargement)
  let mic = null; // { analyser, sample } de la dictée en cours
  let playback = null; // { context, source, analyser, sample, audio, attach } de la lecture en cours
  const simBands = new Float32Array(8);

  const wanted = () => pickAction({ ...state, writing: state.editing || state.wrote });

  /** Le libellé visible, annoncé poliment par le lecteur d'écran : le texte ne change que si l'état change. */
  function apply() {
    const action = wanted();
    const text = captionFor(action);
    if (caption && caption.textContent !== text) caption.textContent = text;
    element.dataset.action = action;
    if (mascotte) mascotte.setAction(action);
  }

  function pulse(key, ms) {
    state[key] = true;
    clearTimeout(timers[key]);
    timers[key] = setTimeout(() => { state[key] = false; timers[key] = 0; apply(); }, ms);
  }

  function clearTimers() {
    for (const key of Object.keys(timers)) { clearTimeout(timers[key]); timers[key] = 0; }
  }

  /** Les niveaux du moment : le micro de la dictée, l'audio en lecture, ou une voix simulée sans audio exploitable. */
  function levels(t) {
    if (state.recording) return mic ? mic.sample() : null;
    if (state.playing) {
      if (playback && playback.sample) return playback.sample();
      const level = simulatedVoice(t) * 0.8;
      return { level, bands: simulatedBands(level, t, simBands) };
    }
    return null;
  }

  function releaseMic() {
    const current = mic;
    mic = null;
    if (current) { try { current.analyser.disconnect(); } catch { /* déjà détaché */ } }
  }

  function releasePlayback() {
    const current = playback;
    playback = null;
    if (!current) return;
    current.audio.removeEventListener('playing', current.attach);
    for (const node of [current.analyser, current.source]) {
      if (node) { try { node.disconnect(); } catch { /* déjà détaché */ } }
    }
    if (current.context) current.context.close().catch(() => {});
  }

  function unmount() {
    epoch += 1;
    if (mascotte) { mascotte.destroy(); mascotte = null; }
    releasePlayback();
    releaseMic();
    clearTimers();
    hide();
  }

  function hide() {
    element.hidden = true;
    element.replaceChildren();
    delete element.dataset.fallback;
    stage = null;
    caption = null;
  }

  /** Une pastille sobre à la place du canevas : le libellé reste, et on sait pourquoi il n'y a pas de dessin. */
  function showDot(reason) {
    if (mascotte) { mascotte.destroy(); mascotte = null; }
    if (!caption) return;
    const dot = document.createElement('span');
    dot.className = 'chat-mascotte-dot';
    dot.setAttribute('aria-hidden', 'true');
    dot.title = `Rendu indisponible : ${reason}`;
    stage = dot;
    element.replaceChildren(dot, caption);
    element.dataset.fallback = reason;
  }

  async function mount() {
    if (stage) return;
    const mine = ++epoch;
    // Le libellé et la bande existent tout de suite : la mascotte ne reste jamais cachée le temps du chargement.
    caption = document.createElement('figcaption');
    caption.className = 'chat-mascotte-caption';
    caption.setAttribute('aria-live', 'polite');
    caption.textContent = captionFor(wanted());
    stage = document.createElement('span'); // remplacé par le canevas, ou par la pastille de repli
    element.replaceChildren(stage, caption);
    element.dataset.action = wanted();
    element.hidden = false;
    let createMascotte;
    try {
      await ensureStylesheet();
      ({ createMascotte } = await import('./mascotte.js'));
    } catch {
      if (mine === epoch) showDot('module de rendu non chargé');
      return;
    }
    if (mine !== epoch) return;
    const canvas = document.createElement('canvas');
    canvas.className = 'chat-mascotte-canvas';
    canvas.setAttribute('aria-hidden', 'true'); // décoratif : le libellé dit déjà tout
    element.replaceChildren(canvas, caption); // visible avant la création : le canevas a une taille quand WebGL s'ouvre
    stage = canvas;
    let made = null;
    made = createMascotte(canvas, {
      sample: levels,
      // Sans WebGL2, shader refusé ou contexte perdu : pastille et libellé, aucun canevas vide ne subsiste.
      onFallback: (reason) => {
        showDot(reason);
        if (made) made.destroy();
      },
    });
    if (!made.supported) return;
    mascotte = made;
    apply();
    made.start();
  }

  return {
    element,
    /** À l'ouverture de l'onglet : monte la mascotte (drapeau allumé), vocal ou non. */
    async refresh() {
      await mount();
      apply();
    },
    setBusy(value) {
      state.busy = Boolean(value);
      if (state.busy) { state.delivered = false; clearTimeout(timers.delivered); }
      apply();
    },
    /** Un morceau de texte de l'orchestrateur vient d'arriver : « écrit » pendant un court instant. */
    wrote() { pulse('wrote', WROTE_MS); apply(); },
    /** Un appel d'outil du fil : « écrit » tant qu'un appel qui modifie des fichiers (edit, delete, move) est en cours. */
    toolCall(update) {
      const id = String((update && update.toolCallId) || '');
      if (!id) return;
      const known = editCalls.get(id) || {};
      const next = { kind: update.kind || known.kind, status: update.status || known.status };
      if (isWritingCall(next)) editCalls.set(id, next); else editCalls.delete(id);
      state.editing = editCalls.size > 0;
      apply();
    },
    /** Fin de tour : plus d'écriture en cours ; « a terminé » seulement après une fin normale vécue (pas rejouée). */
    turnEnded(stopReason, replayed) {
      editCalls.clear();
      state.editing = false;
      state.wrote = false;
      clearTimeout(timers.wrote);
      if (deliversOn(stopReason, replayed)) pulse('delivered', DELIVERED_MS);
      apply();
    },
    /** Le nombre de questions et de demandes de permission qui attendent la personne : tant qu'il en reste, « attend ta réponse ». */
    setWaiting(count) { state.waiting = Math.max(0, Number(count) || 0); apply(); },
    /** La dictée vient d'ouvrir le micro : un analyseur se branche sur SA source ; rien d'autre n'est ouvert. */
    micOpened({ context, source }) {
      state.recording = true;
      releaseMic();
      try {
        const analyser = makeAnalyser(context);
        source.connect(analyser);
        mic = { analyser, sample: analyserSampler(analyser, context.sampleRate) };
      } catch { mic = null; }
      apply();
    },
    /** La dictée a fermé le micro (arrêt, plafond de durée, échec) : l'analyseur est lâché. */
    micClosed() {
      state.recording = false;
      releaseMic();
      apply();
    },
    /** Un message vocal se lit : `audio` est son élément (ou null pour la synthèse du navigateur). */
    playing(audio) {
      releasePlayback();
      state.playing = true;
      apply();
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!audio || !mascotte || !Context) return;
      const current = { audio, context: null, source: null, analyser: null, sample: null, attach: null };
      current.attach = () => {
        if (playback !== current || current.sample) return;
        const stream = captured(audio);
        if (!stream) return;
        try {
          current.context = new Context();
          current.context.resume().catch(() => {});
          current.source = current.context.createMediaStreamSource(stream);
          current.analyser = makeAnalyser(current.context);
          current.source.connect(current.analyser); // l'analyseur n'est relié à aucune sortie : la copie ne s'entend pas
          current.sample = analyserSampler(current.analyser, current.context.sampleRate);
        } catch { current.sample = null; }
      };
      playback = current;
      current.attach();
      if (!current.sample) audio.addEventListener('playing', current.attach, { once: true });
    },
    /** La lecture s'est arrêtée (fin, arrêt, erreur). */
    stopped() {
      state.playing = false;
      releasePlayback();
      apply();
    },
    /** À la fermeture de l'onglet : plus de boucle, plus d'analyseur, plus de contexte WebGL. */
    destroy() {
      unmount();
      state.recording = false;
      state.playing = false;
      state.busy = false;
      state.editing = false;
      state.wrote = false;
      state.waiting = 0;
      state.delivered = false;
    },
  };
}
