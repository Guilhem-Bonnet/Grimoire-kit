// La mascotte du cockpit : le « slime d'encre » v3.1 (#1197), rendu en WebGL2 sur un champ de distance.
//
//   const mascotte = createMascotte(canvas, { sample, onFallback });
//   mascotte.setAction('ecrit');   // repos | ecoute | reflechit | parle | ecrit | question | livre
//   mascotte.setLevels(niveau, bandes);          // niveau 0..1, huit bandes 0..1 (ou `options.sample`, tiré à chaque image)
//   mascotte.start(); mascotte.stop(); mascotte.destroy();
//
// Ni visage ni émotion plaquée : sa forme dit ce qu'il fait, il se ramasse en sphère entre deux formes. Au repos une
// sphère ; qui parle : sphère déformée par les graves, frissonnante aux aigus ; qui écoute : un micro posé sur une
// onde qui suit la voix ; qui réfléchit : un nuage et des bulles.
//
// Sobriété : la boucle ne tourne que si la mascotte est démarrée, visible à l'écran ET que l'onglet est affiché, et
// qu'il y a quelque chose à montrer. Une forme figée une fois arrivée (repos, question en attente, livré) : animation
// d'entrée, une image, puis arrêt, même pendant des minutes. Une forme qui bouge d'elle-même (réfléchit, écrit) passe
// à une cadence très basse après l'entrée ; seules l'écoute et la parole (suivies à la voix) tournent à plein régime.
// `prefers-reduced-motion` ralentit tout et plafonne la cadence. Qualité adaptée à la carte graphique (échelle du rendu).
//
// Sans WebGL2, ou si le shader ne compile pas, `createMascotte` ne lève rien : l'objet rendu est inerte
// (`supported === false`) et `options.onFallback(raison)` est appelé une fois, pour que l'appelant garde son affichage.
//
// Aucune dépendance, aucune police ni ressource externe. Rien n'est enregistré ni envoyé : ce module ne reçoit que
// des niveaux (des nombres) et ne touche ni au micro ni au réseau.

import { FRAGMENT, VERTEX } from './mascotte_shader.js';
import {
  SHAPE_COUNT, adaptScale, clamp01, frameIntervalMs, isSettled, isWiredAction, labelFor, lerp, needsVia, penPose,
  shouldRun, smoothBands, smoothValue, stepWeights,
} from './mascotte_logic.js';

const UNIFORMS = [
  'u_res', 'u_time', 'u_audio', 'u_yaw', 'u_w', 'u_bands', 'u_wobble', 'u_sy', 'u_sxz', 'u_spark', 'u_glow', 'u_swirl',
  'u_exposure', 'u_penTip', 'u_penX', 'u_penY', 'u_penZ', 'u_home', 'u_mainR', 'u_dotR', 'u_split', 'u_gulp', 'u_cl',
  'u_nk', 'u_sat',
];

/** Un niveau poussé par `setLevels` plus vieux que cela est tenu pour éteint (l'appelant a cessé de le nourrir). */
const LEVELS_TTL_MS = 300;

function inert(canvas, reason, onFallback) {
  let told = false;
  const tell = () => { if (!told) { told = true; try { if (onFallback) onFallback(reason); } catch { /* l'appelant garde son affichage */ } } };
  tell();
  return {
    supported: false,
    reason,
    setAction: () => false,
    getAction: () => 'repos',
    setLevels() {},
    start() {},
    stop() {},
    destroy() {},
    stats: () => ({ frames: 0, running: false, scale: 0, action: 'repos' }),
  };
}

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  if (!shader) throw new Error('shader indisponible');
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(`shader non compilé : ${gl.getShaderInfoLog(shader) || 'sans détail'}`);
  return shader;
}

export function createMascotte(canvas, options = {}) {
  const { sample = null, onFallback = null, label = labelFor } = options;
  let gl = null;
  try {
    gl = canvas.getContext('webgl2', { antialias: false, alpha: true, premultipliedAlpha: true, powerPreference: 'high-performance' });
  } catch { gl = null; }
  if (!gl) return inert(canvas, 'WebGL 2 indisponible', onFallback);

  let program = null;
  try {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`programme non lié : ${gl.getProgramInfoLog(program) || 'sans détail'}`);
  } catch (error) {
    return inert(canvas, String(error && error.message ? error.message : error), onFallback);
  }
  gl.useProgram(program);
  gl.bindVertexArray(gl.createVertexArray());
  const loc = {};
  for (const name of UNIFORMS) loc[name] = gl.getUniformLocation(program, name);

  const motion = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  let reduced = Boolean(motion && motion.matches);

  // ── État de la dynamique (le sous-ensemble du prototype utile au vocal) ──────────────────────────────────────
  const w = new Array(SHAPE_COUNT).fill(0);
  const wv = new Array(SHAPE_COUNT).fill(0);
  w[0] = 1;
  const bands = new Float32Array(8);
  const rawBands = new Float32Array(8);
  const pushedBands = new Float32Array(8);
  const zeros = new Float32Array(8);
  const pushed = { level: 0, at: -1e9 };
  const fx = { now: 0, viaUntil: 0, wobKick: 0 };
  let action = 'repos';
  let sy = 1;
  let vy = 0;
  let wobble = 0.06;
  let audio = 0;
  let swirl = 1;
  let speed = 0;
  let wobbleTarget = 0.06;
  let settled = false;
  let dirty = true;

  // Uniformes des formes non branchées (clones de « délègue », trois points de « attend ») : zéros, elles sont éteintes.
  // Le stylo de « écrit » est piloté par `penPose`.
  const cl = new Float32Array(24);
  const nk = new Float32Array(12);
  const sat = new Float32Array(24);
  const home = [0, 0, 0];
  let pen = penPose(0);

  // ── Visibilité, taille, qualité ───────────────────────────────────────────────────────────────────────────────
  let started = false;
  let destroyed = false;
  let failed = false;
  let visible = typeof IntersectionObserver !== 'function'; // sans observateur : supposée visible, l'onglet caché suffit
  let raf = 0;
  let timer = 0; // l'attente entre deux images d'une cadence réduite : aucun réveil en continu
  let last = 0;
  let frames = 0;
  let scale = 0.7;
  let averageMs = 16;
  let counted = 0;
  let cssW = 1;
  let cssH = 1;

  canvas.setAttribute('role', 'img');
  const paintLabel = () => canvas.setAttribute('aria-label', label(action));
  paintLabel();

  const run = () => shouldRun({ started, destroyed, failed, visible, docHidden: document.hidden, action, settled, dirty });

  function wake() {
    if (raf || timer || !run()) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function resize() {
    if (destroyed || failed) return;
    const rect = canvas.getBoundingClientRect();
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.round(cssW * dpr * scale));
    canvas.height = Math.max(1, Math.round(cssH * dpr * scale));
    gl.viewport(0, 0, canvas.width, canvas.height);
    dirty = true; // redimensionner efface le canevas : l'image est à reposer, même au repos
    wake();
  }

  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
  const visibleObserver = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      for (const entry of entries) visible = entry.isIntersecting;
      wake();
    })
    : null;
  if (resizeObserver) resizeObserver.observe(canvas);
  if (visibleObserver) visibleObserver.observe(canvas);
  const onVisibility = () => { if (!document.hidden) wake(); };
  const onMotion = () => { reduced = Boolean(motion && motion.matches); dirty = true; wake(); };
  const onLost = (event) => { event.preventDefault(); fail('contexte WebGL perdu'); };
  document.addEventListener('visibilitychange', onVisibility);
  if (motion && motion.addEventListener) motion.addEventListener('change', onMotion);
  canvas.addEventListener('webglcontextlost', onLost);
  resize();

  function fail(reason) {
    if (failed) return;
    failed = true;
    stopLoop();
    try { if (onFallback) onFallback(reason); } catch { /* l'appelant garde son affichage */ }
  }

  function stopLoop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    if (timer) clearTimeout(timer);
    timer = 0;
  }

  // ── Un pas de dynamique : ressort des formes, squash and stretch, niveaux lissés ────────────────────────────
  function step(dt) {
    fx.now += dt;
    const via = fx.now < fx.viaUntil;
    speed = stepWeights(w, wv, action, dt, { via });
    // Squash and stretch : anticipation au départ, rebond à l'arrivée ; il s'étire un peu quand il parle.
    const bias = action === 'parle' ? 0.05 * audio : 0;
    if (reduced) vy *= 0.2;
    vy += ((1 + bias - sy) * 70 - vy * 4.4) * dt;
    sy = Math.min(1.3, Math.max(0.72, sy + vy * dt));
    fx.wobKick = Math.max(0, fx.wobKick - dt * 1.6);
    wobbleTarget = Math.min(1, 0.06 + speed * 0.3 + Math.abs(vy) * 0.1 + fx.wobKick);
    wobble = lerp(wobble, wobbleTarget, 1 - Math.exp(-6 * dt));
    const swirlTarget = action === 'reflechit' ? 3.5 : 1;
    swirl = lerp(swirl, swirlTarget, 1 - Math.exp(-2 * dt));
    pen = penPose(fx.now);

    // Niveaux : ceux que l'appelant tire (`sample`) ou pousse (`setLevels`) ; seuls l'écoute et la parole en ont.
    let level = 0;
    rawBands.fill(0);
    const live = action === 'ecoute' || action === 'parle';
    let fed = null;
    if (live && sample) {
      try { fed = sample(fx.now); } catch { fed = null; }
    }
    if (fed) {
      level = clamp01(fed.level || 0);
      rawBands.set(fed.bands ? Array.from(fed.bands).slice(0, 8) : zeros);
    } else if (live && performance.now() - pushed.at < LEVELS_TTL_MS) {
      level = pushed.level;
      rawBands.set(pushedBands);
    }
    audio = smoothValue(audio, level, dt, 14, 14);
    const peak = smoothBands(bands, rawBands, dt);
    settled = isSettled({ action, speed, vy, sy, audio, peak, wobble, wobbleTarget, swirl, swirlTarget, via });
  }

  function draw() {
    gl.uniform2f(loc.u_res, canvas.width, canvas.height);
    gl.uniform1f(loc.u_time, fx.now);
    gl.uniform1f(loc.u_audio, audio);
    gl.uniform1f(loc.u_yaw, 0);
    gl.uniform1fv(loc.u_w, w);
    gl.uniform1fv(loc.u_bands, bands);
    gl.uniform1f(loc.u_wobble, reduced ? wobble * 0.3 : wobble);
    gl.uniform1f(loc.u_sy, sy);
    gl.uniform1f(loc.u_sxz, 1 / Math.sqrt(sy));
    gl.uniform1f(loc.u_spark, 1);
    gl.uniform1f(loc.u_glow, 0);
    gl.uniform1f(loc.u_swirl, swirl);
    gl.uniform1f(loc.u_exposure, 1);
    gl.uniform3fv(loc.u_penTip, pen.tip);
    gl.uniform3fv(loc.u_penX, pen.X);
    gl.uniform3fv(loc.u_penY, pen.Y);
    gl.uniform3fv(loc.u_penZ, pen.Z);
    gl.uniform3fv(loc.u_home, home);
    gl.uniform1f(loc.u_mainR, 0.62);
    gl.uniform1f(loc.u_dotR, 0.15);
    gl.uniform1f(loc.u_split, 0);
    gl.uniform1f(loc.u_gulp, 0);
    gl.uniform4fv(loc.u_cl, cl);
    gl.uniform2fv(loc.u_nk, nk);
    gl.uniform4fv(loc.u_sat, sat);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function frame(now) {
    raf = 0;
    if (!run()) return;
    const ms = now - last;
    const interval = frameIntervalMs({ action, settled, reduced });
    if (ms < interval) { // cadence réduite : on attend sans réveiller la page à chaque image
      timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(frame); }, Math.max(1, interval - ms));
      return;
    }
    last = now;
    let dt = Math.min(interval > 60 ? 0.15 : 0.05, ms / 1000);
    if (reduced) dt *= 0.35;
    step(dt);
    try {
      draw();
    } catch {
      fail('rendu WebGL impossible');
      return;
    }
    dirty = false;
    frames += 1;
    // Qualité : l'échelle du rendu suit le temps d'image moyen, par paliers de soixante images.
    averageMs = averageMs * 0.95 + ms * 0.05;
    counted += 1;
    if (counted % 60 === 0) {
      const next = adaptScale(scale, averageMs);
      if (next !== scale) { scale = next; resize(); }
    }
    if (run()) {
      const wait = frameIntervalMs({ action, settled, reduced });
      if (wait > 60) timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(frame); }, wait);
      else raf = requestAnimationFrame(frame);
    }
  }

  return {
    supported: true,
    reason: '',
    /** Change d'action (sept : celles que le fil et le vocal disent). Rend faux, sans rien changer, pour un nom inconnu ou non branché. */
    setAction(name) {
      if (!isWiredAction(name) || destroyed) return false;
      if (name === action) return true;
      const previous = action;
      action = name;
      // Entre deux objets il se ramasse d'abord en sphère : une seule matière, jamais un fondu.
      fx.viaUntil = needsVia(previous, name) ? fx.now + 0.32 : 0;
      vy -= 1.3;
      fx.wobKick = 0.7;
      settled = false;
      paintLabel();
      if (timer) { clearTimeout(timer); timer = 0; } // une nouvelle action ne attend pas la cadence réduite
      wake();
      return true;
    },
    getAction: () => action,
    /** Pousse un niveau (0..1) et huit bandes (0..1). Sans appel suivi, ils s'éteignent d'eux-mêmes. */
    setLevels(level, bandValues) {
      pushed.level = clamp01(Number(level) || 0);
      pushed.at = performance.now();
      for (let b = 0; b < 8; b++) pushedBands[b] = clamp01(Number(bandValues && bandValues[b]) || 0);
      if (pushed.level > 0) wake();
    },
    start() {
      if (destroyed) return;
      started = true;
      wake();
    },
    stop() {
      started = false;
      stopLoop();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      started = false;
      stopLoop();
      if (resizeObserver) resizeObserver.disconnect();
      if (visibleObserver) visibleObserver.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      if (motion && motion.removeEventListener) motion.removeEventListener('change', onMotion);
      canvas.removeEventListener('webglcontextlost', onLost);
      try { if (program) gl.deleteProgram(program); } catch { /* déjà perdu */ }
      try { const lose = gl.getExtension('WEBGL_lose_context'); if (lose) lose.loseContext(); } catch { /* sans extension */ }
    },
    /** Pour les tests et la mesure : images posées, boucle active, échelle du rendu, action. */
    stats: () => ({ frames, running: raf !== 0 || timer !== 0, scale, action, settled }),
  };
}
