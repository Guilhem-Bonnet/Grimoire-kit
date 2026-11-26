// La logique pure de la mascotte vocale (le « slime d'encre », #1197, vocal #1343) : machine d'états des
// actions, ressort des formes, lissage des niveaux, décision de faire tourner la boucle de rendu.
//
// Rien ici ne touche à WebGL, au DOM ni à l'audio : ce fichier se teste sous Node (`tests/js/mascotte.test.mjs`).
// Le rendu est dans `mascotte.js`, le branchement sur le chat dans `mascotte_dock.js`.

/** Les formes du prototype, dans l'ordre des poids du shader (`u_w[i]`). Seules les quatre premières sont branchées. */
export const ACTIONS = {
  repos: { i: 0, label: 'au repos' },
  ecoute: { i: 1, label: 'écoute' },
  reflechit: { i: 2, label: 'réfléchit' },
  parle: { i: 3, label: 'parle' },
  ecrit: { i: 4, label: 'écrit dans la page' },
  propose: { i: 5, label: 'propose' },
  question: { i: 6, label: 'pose une question' },
  delegue: { i: 7, label: 'délègue' },
  attend: { i: 8, label: 'attend les workers' },
  livre: { i: 9, label: 'livre' },
};
export const SHAPE_COUNT = 10;

/** Les actions que le vocal sait montrer : les autres formes restent dans le shader, sans être commandées. */
export const VOCAL_ACTIONS = ['repos', 'ecoute', 'reflechit', 'parle'];

/** Les formes qui sont déjà une sphère : on passe de l'une à l'autre sans se ramasser d'abord. */
export const SPHERE = new Set(['repos', 'parle', 'delegue', 'attend']);

export const clamp01 = (x) => Math.min(1, Math.max(0, x));
export const lerp = (a, b, k) => a + (b - a) * k;

/** Le libellé lu par un lecteur d'écran : « Mascotte vocale : écoute ». */
export function labelFor(action) {
  const entry = ACTIONS[action];
  return `Mascotte vocale : ${entry ? entry.label : ACTIONS.repos.label}`;
}

/**
 * Ce que la mascotte doit montrer, d'après ce qui se passe dans le chat. L'écoute prime (la personne parle),
 * puis la voix de l'orchestrateur, puis sa réflexion ; sinon elle est au repos.
 */
export function pickAction({ recording = false, playing = false, busy = false } = {}) {
  if (recording) return 'ecoute';
  if (playing) return 'parle';
  if (busy) return 'reflechit';
  return 'repos';
}

/** Entre deux objets, il se ramasse d'abord en sphère : jamais un fondu direct d'une forme à l'autre. */
export function needsVia(previous, next) {
  return previous !== next && !SPHERE.has(previous) && !SPHERE.has(next);
}

/** Vrai pour une action que `setAction` accepte (le vocal n'en commande que quatre). */
export function isVocalAction(name) {
  return VOCAL_ACTIONS.includes(name);
}

/**
 * Un pas du ressort des poids de formes (légèrement sous-amorti ; la viscosité ralentit tout). Modifie `w` et
 * `wv` en place et rend la somme des vitesses. `via` : poids repartis entre la sphère et la forme visée.
 */
export function stepWeights(w, wv, target, dt, { via = false, visc = 1 } = {}) {
  const ai = ACTIONS[target].i;
  const k = 18 / visc;
  const c = 2 * Math.sqrt(k) * 0.82;
  let speed = 0;
  for (let i = 0; i < w.length; i++) {
    let goal = i === ai ? 1 : 0;
    if (via) goal = i === 0 ? 0.72 : (i === ai ? 0.28 : 0);
    wv[i] += ((goal - w[i]) * k - wv[i] * c) * dt;
    w[i] = Math.min(1.12, Math.max(0, w[i] + wv[i] * dt));
    speed += Math.abs(wv[i]);
  }
  return speed;
}

/** Les bornes (indices de bins) des huit bandes de fréquence, pour un taux d'échantillonnage donné. */
const BAND_HZ = [94, 188, 328, 563, 938, 1547, 2578, 4219, 7031];
export function bandEdges(sampleRate, binCount) {
  const nyquist = sampleRate / 2;
  const edges = [];
  for (let b = 0; b < BAND_HZ.length; b++) {
    let bin = Math.round((BAND_HZ[b] / nyquist) * binCount);
    if (b > 0) bin = Math.max(bin, edges[b - 1] + 1);
    edges.push(Math.min(bin, binCount - (BAND_HZ.length - b)));
  }
  // Les bornes restent strictement croissantes même quand le spectre est court.
  for (let b = 1; b < edges.length; b++) edges[b] = Math.max(edges[b], edges[b - 1] + 1);
  return edges;
}

/** Les huit bandes (0..1) d'un spectre en octets (`getByteFrequencyData`). */
export function bandsFromSpectrum(spectrum, edges, out = new Float32Array(8)) {
  for (let b = 0; b < 8; b++) {
    const from = edges[b];
    const to = Math.max(from + 1, edges[b + 1]);
    let acc = 0;
    for (let i = from; i < to && i < spectrum.length; i++) acc += spectrum[i];
    const avg = acc / (to - from) / 255;
    out[b] = clamp01((avg - 0.12) / 0.55);
  }
  return out;
}

/** Le niveau (0..1) d'une fenêtre temporelle (`getFloatTimeDomainData`) : valeur efficace, amplifiée. */
export function levelFromSamples(samples) {
  if (!samples || samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length) * 5);
}

/** Lissage exponentiel indépendant du rythme d'images : monte vite (`up`), redescend plus doucement (`down`). */
export function smoothValue(current, target, dt, up = 25, down = 8) {
  const rate = target > current ? up : down;
  return lerp(current, target, 1 - Math.exp(-rate * dt));
}

/** Lisse les huit bandes en place vers `raw` et rend la plus forte. */
export function smoothBands(bands, raw, dt) {
  let peak = 0;
  for (let b = 0; b < bands.length; b++) {
    bands[b] = smoothValue(bands[b], clamp01(raw[b] || 0), dt);
    if (bands[b] > peak) peak = bands[b];
  }
  return peak;
}

/** Une voix de remplacement, sans audio exploitable (synthèse du navigateur) : un souffle de phrases et de syllabes. */
export function simulatedVoice(t, rate = 4.4) {
  const phrase = (t % 3.4) < 2.5 ? 1 : 0;
  const syllable = Math.pow(Math.max(0, Math.sin(t * Math.PI * 2 * rate)), 2);
  const prosody = 0.55 + 0.45 * Math.sin(t * 1.3) * Math.sin(t * 0.37 + 1);
  return phrase * syllable * prosody * (0.75 + 0.25 * Math.sin(t * 17.0));
}

/** Les huit bandes d'une voix simulée à partir de son niveau. */
export function simulatedBands(level, t, out = new Float32Array(8)) {
  for (let b = 0; b < 8; b++) out[b] = level * (0.4 + 0.6 * Math.abs(Math.sin(t * (2.3 + b * 1.31) + b * 0.9))) * (1 - b * 0.06);
  return out;
}

/**
 * La mascotte est-elle au repos stable ? Formes arrivées, ressorts calmes, niveaux éteints : une seule image
 * suffit alors. Les actions qui bougent d'elles-mêmes (écoute, réflexion, parole) ne sont jamais « stables ».
 */
export function isSettled({ action, speed, vy, sy, audio, peak, wobble, wobbleTarget, swirl, via }) {
  return action === 'repos' && !via && speed < 0.02 && Math.abs(vy) < 0.02 && Math.abs(sy - 1) < 0.003
    && audio < 0.01 && peak < 0.01 && Math.abs(wobble - wobbleTarget) < 0.003 && Math.abs(swirl - 1) < 0.02;
}

/**
 * La boucle de rendu doit-elle tourner ? Seulement quand la mascotte est démarrée, visible, l'onglet affiché, et
 * qu'il y a quelque chose à montrer : une action en cours, une transition, ou une image encore à poser (`dirty` :
 * première image, changement de taille). Au repos stable : aucune image, donc aucun coût.
 */
export function shouldRun({ started, destroyed = false, failed = false, visible, docHidden, action, settled, dirty = false }) {
  if (!started || destroyed || failed || !visible || docHidden) return false;
  return dirty || action !== 'repos' || !settled;
}

/** Qualité adaptée à la carte graphique : l'échelle du rendu baisse quand les images sont lentes, remonte quand elles sont rapides. */
export function adaptScale(scale, averageMs) {
  if (averageMs > 24 && scale > 0.4) return Math.max(0.4, scale - 0.08);
  if (averageMs < 13 && scale < 1) return Math.min(1, scale + 0.05);
  return scale;
}
