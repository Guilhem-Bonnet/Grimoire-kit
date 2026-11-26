// Le branchement de la mascotte vocale sur le chat (#1343, mascotte #1197).
//
// Fonction en test : éteinte par défaut (`cockpit.test_features.mascotte`). Le drapeau est lu par `mascotte_lazy.js`,
// qui ne charge ce module qu'allumé. Ici, la mascotte n'apparaît que lorsque le mode vocal est actif
// (`GET voice/config` : `available` et `mode: voice`) ; sinon la bande reste cachée et le module de rendu, son
// shader et sa feuille de style ne sont pas chargés.
//
// Ce que ce module reçoit du chat, il ne le garde que le temps de s'en servir :
// - `micOpened({ context, source })` : le flux micro que la DICTÉE a déjà ouvert. Un analyseur se branche sur sa
//   source ; ce module n'ouvre jamais le micro et ne le garde pas (`micClosed`, appelé à la fermeture de la dictée,
//   lâche l'analyseur) ;
// - `playing(audio)` : l'élément audio du message vocal en lecture. Un analyseur le lit par `captureStream()`, une
//   copie du flux : le son n'est jamais redirigé, donc jamais coupé si l'analyse échoue. Sans flux exploitable
//   (synthèse du navigateur, Safari), la voix est simulée ;
// - `setBusy(bool)` : un tour de l'orchestrateur est en cours.
// Il ne reçoit, n'enregistre et n'envoie jamais de son : seulement des niveaux, sous forme de nombres.

import {
  bandEdges, bandsFromSpectrum, labelFor, levelFromSamples, pickAction, simulatedBands, simulatedVoice,
} from './mascotte_logic.js';

const STYLESHEET = new URL('./mascotte.css', import.meta.url).href;
const FFT_SIZE = 1024;

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

export function createMascotteDock({ api }) {
  const element = document.createElement('figure');
  element.className = 'chat-mascotte';
  element.hidden = true;
  // Le canevas et la légende n'existent que mascotte montée : éteinte, la bande est une coquille vide et cachée.
  let canvas = null;
  let caption = null;

  const state = { recording: false, playing: false, busy: false };
  let mascotte = null;
  let epoch = 0; // invalide un montage devenu inutile (onglet fermé pendant le chargement)
  let mic = null; // { analyser, sample } de la dictée en cours
  let playback = null; // { context, source, analyser, sample, audio, attach } de la lecture en cours
  const simBands = new Float32Array(8);

  function apply() {
    if (caption) caption.textContent = labelFor(pickAction(state)).replace('Mascotte vocale : ', '');
    if (mascotte) mascotte.setAction(pickAction(state));
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

  async function wanted() {
    let config;
    try { config = await api.voiceConfig(); } catch { return false; }
    return Boolean(config) && config.available === true && config.mode === 'voice';
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
    hide();
  }

  function hide() {
    element.hidden = true;
    element.replaceChildren();
    canvas = null;
    caption = null;
  }

  async function mount() {
    if (mascotte) return;
    const mine = ++epoch;
    await ensureStylesheet();
    const { createMascotte } = await import('./mascotte.js');
    if (mine !== epoch) return;
    canvas = document.createElement('canvas');
    canvas.className = 'chat-mascotte-canvas';
    caption = document.createElement('figcaption');
    caption.className = 'chat-mascotte-caption';
    caption.setAttribute('aria-hidden', 'true');
    element.replaceChildren(canvas, caption);
    element.hidden = false; // visible avant la création : le canevas a une taille quand le contexte WebGL s'ouvre
    let made = null;
    made = createMascotte(canvas, {
      sample: levels,
      // Sans WebGL2 ou shader refusé : l'affichage actuel reste, aucun canevas vide ne subsiste.
      onFallback: () => {
        if (made === mascotte || !mascotte) { mascotte = null; hide(); }
        if (made) made.destroy();
      },
    });
    if (!made.supported) { hide(); return; }
    mascotte = made;
    apply();
    made.start();
  }

  return {
    element,
    /** À l'ouverture de l'onglet : lit le drapeau et le mode, monte ou démonte la mascotte. */
    async refresh() {
      if (await wanted()) await mount(); else unmount();
      apply();
    },
    setBusy(value) { state.busy = Boolean(value); apply(); },
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
    },
  };
}
