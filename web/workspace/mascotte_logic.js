// La logique pure de la mascotte du chat (le « slime d'encre », #1197, vocal #1343) : machine d'états des
// actions, ressort des formes, lissage des niveaux, décision de faire tourner la boucle de rendu.
//
// La mascotte est la présence de l'assistant dans le chat, avec ou sans voix : elle vit sur les états du fil
// (réfléchit, écrit, question en attente, livré) ; avec le vocal, l'écoute et la parole s'y ajoutent.
//
// Rien ici ne touche à WebGL, au DOM ni à l'audio : ce fichier se teste sous Node (`tests/js/mascotte.test.mjs`).
// Le rendu est dans `mascotte.js`, le branchement sur le chat dans `mascotte_dock.js`.

/**
 * Les formes du prototype, dans l'ordre des poids du shader (`u_w[i]`). `label` : ce que dit le libellé visible.
 * `propose` (suggestion de l'Atelier), `delegue` et `attend` (sous-agents, workers) existent dans le shader mais ne
 * sont pas branchés : le fil du chat n'expose aucun événement qui les dise.
 */
export const ACTIONS = {
  repos: { i: 0, label: 'au repos' },
  ecoute: { i: 1, label: 'écoute' },
  reflechit: { i: 2, label: 'réfléchit' },
  parle: { i: 3, label: 'parle' },
  ecrit: { i: 4, label: 'écrit' },
  propose: { i: 5, label: 'propose' },
  question: { i: 6, label: 'attend ta réponse' },
  delegue: { i: 7, label: 'délègue' },
  attend: { i: 8, label: 'attend les workers' },
  livre: { i: 9, label: 'a terminé' },
};
export const SHAPE_COUNT = 10;

/** Les actions que `setAction` accepte : celles que le fil du chat (ou le vocal) sait dire. */
export const WIRED_ACTIONS = ['repos', 'ecoute', 'reflechit', 'parle', 'ecrit', 'question', 'livre'];
/** Les actions que seul le vocal commande. */
export const VOCAL_ACTIONS = ['ecoute', 'parle'];

/**
 * Comment chaque forme se rend une fois arrivée : `live` suit un signal (voix) et tourne à plein régime ;
 * `slow` bouge d'elle-même (nuage, plume) et passe à une cadence très basse après l'entrée ; `still` ne bouge plus
 * (image figée : zéro coût pendant les minutes d'une question en attente).
 */
export const MOTION = {
  repos: 'still', ecoute: 'live', reflechit: 'slow', parle: 'live', ecrit: 'slow', propose: 'still',
  question: 'still', delegue: 'slow', attend: 'still', livre: 'still',
};
/** Intervalle entre deux images d'une forme `slow` arrivée, et plafond sous `prefers-reduced-motion`. */
export const SLOW_FRAME_MS = 100;
export const REDUCED_FRAME_MS = 50;

/** Les formes qui sont déjà une sphère : on passe de l'une à l'autre sans se ramasser d'abord. */
export const SPHERE = new Set(['repos', 'parle', 'delegue', 'attend']);

export const clamp01 = (x) => Math.min(1, Math.max(0, x));
export const lerp = (a, b, k) => a + (b - a) * k;

/** Le libellé visible à côté de la mascotte : « réfléchit », « attend ta réponse »… */
export function captionFor(action) {
  const entry = ACTIONS[action];
  return entry ? entry.label : ACTIONS.repos.label;
}

/** Le libellé accessible du canevas : « Mascotte : écoute ». */
export function labelFor(action) {
  return `Mascotte : ${captionFor(action)}`;
}

/**
 * Ce que la mascotte doit montrer, d'après les faits du chat. Priorités, de la plus forte à la plus faible :
 * 1. `recording` : la personne parle dans le micro (la dictée) : « écoute » ;
 * 2. `playing` : un message vocal se lit : « parle » ;
 * 3. `waiting` : une question ou une demande de permission attend la personne (nombre en attente) : « question »,
 *    et elle y reste tant qu'il en reste une ; elle prime sur tout ce que fait le fil ;
 * 4. `writing` : l'orchestrateur produit du texte ou modifie des fichiers : « écrit » ;
 * 5. `busy` : un tour est en cours sans sortie visible : « réfléchit » ;
 * 6. `delivered` : un tour vient de se finir bien : « livre », un court instant ;
 * sinon « repos ». Un nouveau tour (`busy`) l'emporte sur `delivered`.
 */
export function pickAction({
  recording = false, playing = false, waiting = 0, writing = false, busy = false, delivered = false,
} = {}) {
  if (recording) return 'ecoute';
  if (playing) return 'parle';
  if (waiting > 0) return 'question';
  if (writing) return 'ecrit';
  if (busy) return 'reflechit';
  if (delivered) return 'livre';
  return 'repos';
}

/**
 * Le tour qui finit a-t-il « livré » ? Seulement une fin normale (`end_turn`), pas un arrêt demandé, un refus
 * ni une erreur, et pas un tour rejoué à la reconnexion.
 */
export function deliversOn(stopReason, replayed = false) {
  return !replayed && stopReason === 'end_turn';
}

/** Un appel d'outil qui modifie des fichiers (types ACP `edit`, `delete`, `move`), encore en cours. */
const WRITING_KINDS = new Set(['edit', 'delete', 'move']);
export function isWritingCall({ kind, status } = {}) {
  return WRITING_KINDS.has(kind) && status !== 'completed' && status !== 'failed';
}

/** Entre deux objets, il se ramasse d'abord en sphère : jamais un fondu direct d'une forme à l'autre. */
export function needsVia(previous, next) {
  return previous !== next && !SPHERE.has(previous) && !SPHERE.has(next);
}

/** Vrai pour une action que `setAction` accepte (celles que le fil ou le vocal commandent). */
export function isWiredAction(name) {
  return WIRED_ACTIONS.includes(name);
}

const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/** La pose du stylo plume (forme « écrit ») à l'instant `t` : pointe et base orthonormée, comme le prototype. */
export function penPose(t) {
  const sweep = 0.36 * Math.sin(t * 0.5);
  const lx = 0.06 * Math.cos(t * 7.5);
  const lz = 0.05 * Math.sin(t * 7.5);
  const lift = 0.08 * Math.pow(Math.max(0, Math.sin(t * 1.1 + 0.5)), 10);
  const Y = unit([0.5 + 0.09 * Math.cos(t * 7.5), 1.0, -0.18 + 0.07 * Math.sin(t * 7.5)]);
  const X = unit(cross(Y, [0, 0, 1]));
  return { tip: [-0.38 + sweep + lx, -1.065 + lift, 0.15 + lz], X, Y, Z: cross(X, Y) };
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
 * L'entrée de la forme est-elle finie ? Formes arrivées, ressorts calmes, niveaux éteints. Les actions qui suivent
 * un signal (écoute, parole) ne sont jamais « arrivées » ; les autres le sont une fois l'animation d'entrée posée.
 * `swirlTarget` : le tourbillon visé par l'action (plus vif quand il réfléchit).
 */
export function isSettled({ action, speed, vy, sy, audio, peak, wobble, wobbleTarget, swirl, swirlTarget = 1, via }) {
  return MOTION[action] !== 'live' && !via && speed < 0.02 && Math.abs(vy) < 0.02 && Math.abs(sy - 1) < 0.003
    && audio < 0.01 && peak < 0.01 && Math.abs(wobble - wobbleTarget) < 0.003 && Math.abs(swirl - swirlTarget) < 0.02;
}

/**
 * La boucle de rendu doit-elle tourner ? Seulement quand la mascotte est démarrée, visible, l'onglet affiché, et
 * qu'il y a quelque chose à montrer : une forme qui suit un signal, une entrée ou une transition en cours, une
 * forme qui bouge d'elle-même (à cadence réduite, voir `frameIntervalMs`), ou une image encore à poser (`dirty` :
 * première image, changement de taille). Une forme figée arrivée (repos, question en attente, livré) : aucune
 * image, donc aucun coût, même pendant des minutes.
 */
export function shouldRun({ started, destroyed = false, failed = false, visible, docHidden, action, settled, dirty = false }) {
  if (!started || destroyed || failed || !visible || docHidden) return false;
  return dirty || !settled || MOTION[action] === 'live' || MOTION[action] === 'slow';
}

/** Le délai minimal entre deux images : cadence très basse pour une forme `slow` arrivée, plafond sous `prefers-reduced-motion`. */
export function frameIntervalMs({ action, settled, reduced = false }) {
  const slow = settled && MOTION[action] === 'slow' ? SLOW_FRAME_MS : 0;
  return Math.max(slow, reduced ? REDUCED_FRAME_MS : 0);
}

/** Qualité adaptée à la carte graphique : l'échelle du rendu baisse quand les images sont lentes, remonte quand elles sont rapides. */
export function adaptScale(scale, averageMs) {
  if (averageMs > 24 && scale > 0.4) return Math.max(0.4, scale - 0.08);
  if (averageMs < 13 && scale < 1) return Math.min(1, scale + 0.05);
  return scale;
}
