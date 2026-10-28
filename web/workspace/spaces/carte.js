// Savoir › Carte 3D — tout le graphe du projet, rendu sur le GPU (P81).
//
// Une lecture : `memory/graph` (workspace_graph.py) rend TOUS les nœuds et
// tous les liens, en colonnes compactes (tableaux typés en base64). Rien n'est
// échantillonné : le nombre de nœuds dessinés est celui de la route.
//
// Rendu : WebGL2 brut, sans bibliothèque (le cockpit est hors ligne, aucun CDN,
// rien à vendorer). Un seul draw call pour tous les nœuds (gl.POINTS), un seul
// pour tous les liens (gl.LINES indexés sur le même tampon de positions) ;
// aucun objet de scène par nœud. Niveau de détail : couleur par communauté,
// liens atténués avec la distance, liens décimés pendant le mouvement,
// étiquettes seulement au survol et près de la caméra.
//
// Picking : un passage de rendu vers une texture d'ids (un id par pixel), puis
// readPixels sur quelques pixels — pas de lancer de rayon sur 27 k objets.
//
// Disposition : portée par le serveur (communautés, gardée par empreinte) ;
// l'affinage par forces Barnes-Hut tourne dans `carte-worker.js`, jamais sur le
// fil principal. Les positions reviennent en Float32Array transférés.
//
// Sans WebGL2 : repli 2D (Canvas2D), même caméra, même recherche, mêmes filtres.
//
// Couleurs : lues des jetons du thème (getComputedStyle), jamais écrites ici.
//
// API consommées : api.memoryGraph, api.memoryEntry.

import { failureText, spaceHead } from '../shell.js';

const STYLE_ID = 'sc-styles';
const STYLES = `
.sc-wrap { display: flex; flex-direction: column; gap: 10px; padding: 20px 24px; min-width: 0; height: 100%; box-sizing: border-box; }
.sc-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.sc-bar input[type="search"] { height: var(--h-ctl); min-width: 240px; padding: 0 9px; border: 1px solid var(--line);
  border-radius: var(--r); background: var(--card); color: var(--ink); }
.sc-kinds { display: flex; gap: 10px; border: 0; margin: 0; padding: 0; }
.sc-kinds label { display: inline-flex; align-items: center; gap: 4px; color: var(--ink2); }
.sc-status { color: var(--ink2); font-size: 12px; margin-left: auto; }
.sc-stage { position: relative; flex: 1; min-height: 420px; border: 1px solid var(--line); border-radius: var(--r-l);
  background: var(--bg); overflow: hidden; }
.sc-stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; }
.sc-labels { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.sc-label { position: absolute; transform: translate(8px, -50%); padding: 1px 5px; border-radius: var(--r); font-size: 12px;
  background: var(--card); color: var(--ink); border: 1px solid var(--line); white-space: nowrap; max-width: 280px;
  overflow: hidden; text-overflow: ellipsis; }
.sc-label[data-hover="1"] { border-color: var(--acc); }
.sc-results { position: absolute; left: 10px; top: 10px; z-index: 2; margin: 0; padding: 4px; list-style: none; max-width: 360px;
  background: var(--card); border: 1px solid var(--line); border-radius: var(--r); box-shadow: var(--shadow); }
.sc-results[hidden] { display: none; }
.sc-results button { appearance: none; font: inherit; display: block; width: 100%; text-align: left; padding: 4px 8px;
  background: transparent; border: 0; color: var(--ink); cursor: pointer; border-radius: var(--r); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.sc-results button:hover, .sc-results button:focus-visible { background: var(--acc-soft); outline: none; }
.sc-note { padding: 8px 12px; border-bottom: 1px solid var(--line); color: var(--ink2); font-size: 12px; background: var(--card); }
.sc-detail { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; }
.sc-detail h3 { margin: 0; font-size: 15px; line-height: 20px; overflow-wrap: anywhere; }
.sc-detail p { margin: 0; color: var(--ink2); overflow-wrap: anywhere; }
.sc-detail .sc-meta { font-size: 12px; }
`;

const KIND_LABEL = { souvenir: 'Souvenirs', code: 'Code', tache: 'Tâches' };
const POINT_SIZE = { souvenir: 5, code: 3, tache: 7 };

function el(tag, cls, text, attrs) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.append(style);
}

// ── Décodage des colonnes ───────────────────────────────────────────────────

function column(b64, Type) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return new Type(bytes.buffer);
}

function decode(payload) {
  const n = payload.counts.nodes;
  return {
    n,
    m: payload.counts.edges,
    fingerprint: payload.fingerprint,
    kinds: payload.kinds,
    kind: column(payload.nodes.kind, Uint8Array),
    community: column(payload.nodes.community, Uint16Array),
    label: payload.nodes.label,
    ref: payload.nodes.ref,
    pos: column(payload.layout.positions, Float32Array),
    src: column(payload.edges.src, Int32Array),
    dst: column(payload.edges.dst, Int32Array),
    sources: payload.sources || {},
    layout: payload.layout,
  };
}

/** Voisins en CSR : `start[i]..start[i+1]` indexe `nbr`. */
function adjacency(g) {
  const start = new Uint32Array(g.n + 1);
  for (let e = 0; e < g.m; e += 1) { start[g.src[e] + 1] += 1; start[g.dst[e] + 1] += 1; }
  for (let i = 0; i < g.n; i += 1) start[i + 1] += start[i];
  const fill = start.slice(0, g.n);
  const nbr = new Uint32Array(start[g.n]);
  for (let e = 0; e < g.m; e += 1) {
    nbr[fill[g.src[e]]++] = g.dst[e];
    nbr[fill[g.dst[e]]++] = g.src[e];
  }
  return { start, nbr };
}

// ── Couleurs : jetons du thème, palette par communauté ─────────────────────

function readToken(name) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const probe = document.createElement('span');
  probe.style.color = raw || 'transparent';
  document.body.append(probe);
  const rgb = getComputedStyle(probe).color.match(/[\d.]+/g) || [0, 0, 0];
  probe.remove();
  return [Number(rgb[0]) / 255, Number(rgb[1]) / 255, Number(rgb[2]) / 255];
}

function hueToRgb(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

function theme() {
  const bg = readToken('--bg');
  const luminance = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2];
  return {
    bg, ink: readToken('--ink'), ink3: readToken('--ink3'), acc: readToken('--acc'),
    dark: luminance < 0.5,
  };
}

function communityColor(c, dark) {
  // Angle d'or : deux communautés voisines dans l'ordre ne se ressemblent pas.
  return hueToRgb((c * 137.508) % 360, 0.62, dark ? 0.68 : 0.42);
}

/** Couleur CSS à partir de canaux 0..255, écrite sans littéral : la source de couleur reste tokens.css. */
function hexColor(r, g, b, a) {
  const part = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return '#' + part(r) + part(g) + part(b) + part(a);
}

// ── Caméra orbitale ─────────────────────────────────────────────────────────

class Camera {
  constructor() {
    this.yaw = 0.6; this.pitch = 0.35; this.dist = 400;
    this.target = [0, 0, 0];
    this.fov = 55 * Math.PI / 180;
    this.flight = null;
  }

  eye() {
    const cp = Math.cos(this.pitch);
    return [
      this.target[0] + this.dist * cp * Math.sin(this.yaw),
      this.target[1] + this.dist * Math.sin(this.pitch),
      this.target[2] + this.dist * cp * Math.cos(this.yaw),
    ];
  }

  /** Matrice vue-projection (colonnes, Float32Array(16)). */
  matrix(aspect, near, far) {
    const e = this.eye();
    let zx = e[0] - this.target[0]; let zy = e[1] - this.target[1]; let zz = e[2] - this.target[2];
    let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
    let xx = zz; let xz = -zx; // up = (0,1,0) × z
    l = Math.hypot(xx, xz) || 1; xx /= l; xz /= l;
    const yx = zy * xz; const yy = zz * xx - zx * xz; const yz = -zy * xx;
    const view = [
      xx, yx, zx, 0,
      0, yy, zy, 0,
      xz, yz, zz, 0,
      -(xx * e[0] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1,
    ];
    const f = 1 / Math.tan(this.fov / 2);
    const proj = [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0];
    const out = new Float32Array(16);
    for (let c = 0; c < 4; c += 1) {
      for (let r = 0; r < 4; r += 1) {
        let s = 0;
        for (let k = 0; k < 4; k += 1) s += proj[k * 4 + r] * view[c * 4 + k];
        out[c * 4 + r] = s;
      }
    }
    return out;
  }

  fit(radius) { this.dist = radius * 2.4; this.target = [0, 0, 0]; }

  flyTo(point, dist, now) {
    this.flight = { from: [...this.target], to: point, d0: this.dist, d1: dist, t0: now, ms: 600 };
  }

  step(now) {
    if (!this.flight) return false;
    const f = this.flight;
    const t = Math.min(1, (now - f.t0) / f.ms);
    const k = t * t * (3 - 2 * t);
    for (let i = 0; i < 3; i += 1) this.target[i] = f.from[i] + (f.to[i] - f.from[i]) * k;
    this.dist = f.d0 + (f.d1 - f.d0) * k;
    if (t >= 1) this.flight = null;
    return true;
  }
}

function project(m, p, w, h) {
  const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
  const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
  const wc = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
  if (wc <= 0) return null;
  return { x: (x / wc * 0.5 + 0.5) * w, y: (1 - (y / wc * 0.5 + 0.5)) * h, w: wc };
}

// ── Rendu WebGL2 ────────────────────────────────────────────────────────────

const VS_POINTS = `#version 300 es
in vec3 aPos; in vec4 aColor; in float aSize;
uniform mat4 uVP; uniform float uScale; uniform float uPick;
out vec4 vColor; out float vId;
void main() {
  vec4 clip = uVP * vec4(aPos, 1.0);
  gl_Position = clip;
  float s = aSize * uScale / max(clip.w, 1.0);
  gl_PointSize = clamp(s, 1.5, 14.0);
  vColor = aColor;
  vId = float(gl_VertexID);
}`;

const FS_POINTS = `#version 300 es
precision highp float;
in vec4 vColor; in float vId;
uniform float uPick;
out vec4 o;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  if (dot(d, d) > 0.25 || vColor.a < 0.02) discard;
  if (uPick > 0.5) {
    float id = vId + 1.0;
    o = vec4(mod(id, 256.0), mod(floor(id / 256.0), 256.0), floor(id / 65536.0), 255.0) / 255.0;
  } else {
    o = vec4(vColor.rgb, vColor.a);
  }
}`;

const VS_LINES = `#version 300 es
in vec3 aPos; in vec4 aColor;
uniform mat4 uVP; uniform float uFade;
out float vAlpha;
void main() {
  vec4 clip = uVP * vec4(aPos, 1.0);
  gl_Position = clip;
  // Atténuation avec la distance : les liens lointains s'effacent.
  vAlpha = aColor.a * clamp(1.0 - clip.w / uFade, 0.12, 1.0);
}`;

const FS_LINES = `#version 300 es
precision highp float;
in float vAlpha; uniform vec4 uColor;
out vec4 o;
void main() { o = vec4(uColor.rgb, uColor.a * vAlpha); }`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}

function program(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

/** Le moteur de rendu GPU. Lève si WebGL2 est absent (l'appelant bascule en 2D). */
class GpuView {
  constructor(canvas, g) {
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: false });
    if (!gl) throw new Error('webgl2 indisponible');
    this.gl = gl; this.canvas = canvas; this.g = g;
    this.mode = 'webgl';
    this.pPoints = program(gl, VS_POINTS, FS_POINTS);
    this.pLines = program(gl, VS_LINES, FS_LINES);
    this.posBuf = gl.createBuffer();
    this.colBuf = gl.createBuffer();
    this.sizeBuf = gl.createBuffer();
    this.idxBuf = gl.createBuffer();
    this.hiBuf = gl.createBuffer();
    this.hiCount = 0;
    this.pickTex = null; this.pickFbo = null; this.pickDepth = null;
    this.vaoPoints = gl.createVertexArray();
    this.vaoLines = gl.createVertexArray();
    this.vaoHi = gl.createVertexArray();
    this.drawnNodes = 0; this.drawnEdges = 0;
    this.edgeFraction = 0.3;
    this.restFraction = 0.3;
    this.radius = radiusOf(g.pos);
    this.upload();
  }

  upload() {
    const { gl, g } = this;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, g.pos, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.colBuf);
    gl.bufferData(gl.ARRAY_BUFFER, g.n * 4, gl.DYNAMIC_DRAW);
    const sizes = new Float32Array(g.n);
    for (let i = 0; i < g.n; i += 1) sizes[i] = POINT_SIZE[g.kinds[g.kind[i]]] || 4;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.sizeBuf);
    gl.bufferData(gl.ARRAY_BUFFER, sizes, gl.STATIC_DRAW);
    // Liens : paires d'indices, mélangées une fois — les `k` premiers forment
    // toujours un échantillon uniforme (décimation pendant le mouvement).
    const order = new Uint32Array(g.m);
    for (let i = 0; i < g.m; i += 1) order[i] = i;
    for (let i = g.m - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = order[i]; order[i] = order[j]; order[j] = t;
    }
    const index = new Uint32Array(g.m * 2);
    for (let i = 0; i < g.m; i += 1) { index[i * 2] = g.src[order[i]]; index[i * 2 + 1] = g.dst[order[i]]; }
    gl.bindVertexArray(this.vaoLines);
    this.bindAttrs(this.pLines, ['aPos', 'aColor']);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.idxBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index, gl.STATIC_DRAW);
    gl.bindVertexArray(this.vaoHi);
    this.bindAttrs(this.pLines, ['aPos', 'aColor']);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.hiBuf);
    gl.bindVertexArray(this.vaoPoints);
    this.bindAttrs(this.pPoints, ['aPos', 'aColor', 'aSize']);
    gl.bindVertexArray(null);
  }

  bindAttrs(prog, names) {
    const { gl } = this;
    const spec = {
      aPos: [this.posBuf, 3, gl.FLOAT, false],
      aColor: [this.colBuf, 4, gl.UNSIGNED_BYTE, true],
      aSize: [this.sizeBuf, 1, gl.FLOAT, false],
    };
    for (const name of names) {
      const loc = gl.getAttribLocation(prog, name);
      if (loc < 0) continue;
      const [buf, size, type, norm] = spec[name];
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, type, norm, 0, 0);
    }
  }

  /** Taille d'un point : fonction de l'espacement moyen des nœuds et de la focale, bornée dans le shader. */
  pointScale(h, cam) {
    const spacing = this.radius / Math.cbrt(Math.max(1, this.g.n));
    return (h / (2 * Math.tan(cam.fov / 2))) * spacing * 0.12;
  }

  setPositions(pos) {
    const { gl } = this;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pos);
  }

  setColors(rgba) {
    const { gl } = this;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.colBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, rgba);
  }

  setHighlight(edgeIndex) {
    const { gl } = this;
    this.hiCount = edgeIndex.length;
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.hiBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, edgeIndex, gl.DYNAMIC_DRAW);
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h;
      this.pickTex = null;
    }
    return [w, h];
  }

  draw(cam, th, moving) {
    const { gl, g } = this;
    const [w, h] = this.resize();
    const aspect = w / h;
    const far = cam.dist * 6 + 2000;
    const vp = cam.matrix(aspect, Math.max(1, cam.dist * 0.01), far);
    gl.viewport(0, 0, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.clearColor(th.bg[0], th.bg[1], th.bg[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    // Liens : un draw call. Décimés tant que la caméra bouge.
    const fraction = this.fixedEdges ?? (moving ? Math.min(this.edgeFraction, this.restFraction) : this.restFraction);
    const count = Math.floor(g.m * fraction) * 2;
    gl.useProgram(this.pLines);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.pLines, 'uVP'), false, vp);
    gl.uniform1f(gl.getUniformLocation(this.pLines, 'uFade'), cam.dist * 3.2 + 1200);
    const lineAlpha = th.dark ? 0.16 : 0.22;
    gl.uniform4f(gl.getUniformLocation(this.pLines, 'uColor'), th.ink3[0], th.ink3[1], th.ink3[2], lineAlpha);
    gl.bindVertexArray(this.vaoLines);
    gl.drawElements(gl.LINES, count, gl.UNSIGNED_INT, 0);
    this.drawnEdges = count / 2;
    if (this.hiCount) {
      gl.uniform4f(gl.getUniformLocation(this.pLines, 'uColor'), th.acc[0], th.acc[1], th.acc[2], 0.95);
      gl.bindVertexArray(this.vaoHi);
      gl.drawElements(gl.LINES, this.hiCount, gl.UNSIGNED_INT, 0);
    }
    // Nœuds : un draw call.
    gl.useProgram(this.pPoints);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.pPoints, 'uVP'), false, vp);
    gl.uniform1f(gl.getUniformLocation(this.pPoints, 'uScale'), this.pointScale(h, cam));
    gl.uniform1f(gl.getUniformLocation(this.pPoints, 'uPick'), 0);
    gl.bindVertexArray(this.vaoPoints);
    if (!this.noPoints) gl.drawArrays(gl.POINTS, 0, g.n);
    this.drawnNodes = g.n;
    gl.bindVertexArray(null);
    this.vp = vp; this.size = [w, h];
  }

  /** Id de nœud sous le pixel (x,y en px CSS), ou -1. Rendu vers une texture d'ids. */
  pick(cam, x, y) {
    const { gl, g } = this;
    if (!this.vp) return -1;
    const [w, h] = this.size;
    if (!this.pickTex) {
      this.pickTex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.pickTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      this.pickFbo = this.pickFbo || gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.pickFbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.pickTex, 0);
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.pickFbo);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.disable(gl.BLEND);
    gl.useProgram(this.pPoints);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.pPoints, 'uVP'), false, this.vp);
    gl.uniform1f(gl.getUniformLocation(this.pPoints, 'uScale'), this.pointScale(h, cam));
    gl.uniform1f(gl.getUniformLocation(this.pPoints, 'uPick'), 1);
    gl.bindVertexArray(this.vaoPoints);
    gl.drawArrays(gl.POINTS, 0, g.n);
    gl.bindVertexArray(null);
    const ratio = w / this.canvas.clientWidth;
    const px = Math.round(x * ratio); const py = Math.round(h - y * ratio);
    const r = 3; // fenêtre 7×7 : le plus proche du curseur l'emporte
    const side = r * 2 + 1;
    const buf = new Uint8Array(side * side * 4);
    const x0 = Math.max(0, Math.min(w - side, px - r)); const y0 = Math.max(0, Math.min(h - side, py - r));
    gl.readPixels(x0, y0, side, side, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    let best = -1; let bestD = Infinity;
    for (let j = 0; j < side; j += 1) {
      for (let i = 0; i < side; i += 1) {
        const o = (j * side + i) * 4;
        const id = buf[o] + buf[o + 1] * 256 + buf[o + 2] * 65536;
        if (!id) continue;
        const d = (x0 + i - px) ** 2 + (y0 + j - py) ** 2;
        if (d < bestD) { bestD = d; best = id - 1; }
      }
    }
    return best >= 0 && best < g.n ? best : -1;
  }
}

// ── Repli 2D ────────────────────────────────────────────────────────────────

class Canvas2dView {
  constructor(canvas, g) {
    this.ctx = canvas.getContext('2d');
    this.canvas = canvas; this.g = g; this.mode = '2d';
    this.colors = new Uint8Array(g.n * 4);
    this.drawnNodes = 0; this.drawnEdges = 0;
    this.edgeFraction = 1;
    this.hiEdges = new Uint32Array(0);
  }

  setPositions(pos) { this.g.pos = pos; }
  setColors(rgba) { this.colors = rgba; }
  setHighlight(edgeIndex) { this.hiEdges = edgeIndex; }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    return [w, h, dpr];
  }

  draw(cam, th) {
    const { ctx, g } = this;
    const [w, h, dpr] = this.resize();
    const vp = cam.matrix(w / h, Math.max(1, cam.dist * 0.01), cam.dist * 6 + 2000);
    this.vp = vp; this.size = [w / dpr, h / dpr];
    const tint = (c, a) => hexColor(c[0] * 255, c[1] * 255, c[2] * 255, a * 255);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = tint(th.bg, 1);
    ctx.fillRect(0, 0, w, h);
    const sw = this.size[0]; const sh = this.size[1];
    const px = new Float32Array(g.n * 2);
    const ok = new Uint8Array(g.n);
    const p = [0, 0, 0];
    for (let i = 0; i < g.n; i += 1) {
      p[0] = g.pos[i * 3]; p[1] = g.pos[i * 3 + 1]; p[2] = g.pos[i * 3 + 2];
      const s = project(vp, p, sw, sh);
      if (s) { px[i * 2] = s.x; px[i * 2 + 1] = s.y; ok[i] = 1; }
    }
    this.px = px;
    ctx.strokeStyle = tint(th.ink3, 0.18);
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    let edges = 0;
    for (let e = 0; e < g.m; e += 1) {
      const a = g.src[e]; const b = g.dst[e];
      if (!ok[a] || !ok[b] || this.colors[a * 4 + 3] < 5 || this.colors[b * 4 + 3] < 5) continue;
      ctx.moveTo(px[a * 2], px[a * 2 + 1]); ctx.lineTo(px[b * 2], px[b * 2 + 1]);
      edges += 1;
    }
    ctx.stroke();
    if (this.hiEdges.length) {
      ctx.strokeStyle = tint(th.acc, 0.95); ctx.lineWidth = 1.2; ctx.beginPath();
      for (let i = 0; i < this.hiEdges.length; i += 2) {
        const a = this.hiEdges[i]; const b = this.hiEdges[i + 1];
        ctx.moveTo(px[a * 2], px[a * 2 + 1]); ctx.lineTo(px[b * 2], px[b * 2 + 1]);
      }
      ctx.stroke();
    }
    let nodes = 0;
    for (let i = 0; i < g.n; i += 1) {
      const o = i * 4;
      if (!ok[i] || this.colors[o + 3] < 5) continue;
      ctx.fillStyle = hexColor(this.colors[o], this.colors[o + 1], this.colors[o + 2], this.colors[o + 3]);
      const size = POINT_SIZE[g.kinds[g.kind[i]]] > 5 ? 3 : 2;
      ctx.fillRect(px[i * 2] - size / 2, px[i * 2 + 1] - size / 2, size, size);
      nodes += 1;
    }
    this.drawnNodes = g.n; this.drawnEdges = edges; this.visibleNodes = nodes;
  }

  pick(cam, x, y) {
    if (!this.px) return -1;
    let best = -1; let bestD = 64;
    for (let i = 0; i < this.g.n; i += 1) {
      if (this.colors[i * 4 + 3] < 5) continue;
      const d = (this.px[i * 2] - x) ** 2 + (this.px[i * 2 + 1] - y) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }
}

// ── La vue ──────────────────────────────────────────────────────────────────

function radiusOf(pos) {
  let r = 1;
  for (let i = 0; i < pos.length; i += 3) r = Math.max(r, Math.hypot(pos[i], pos[i + 1], pos[i + 2]));
  return r;
}

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([]);
  ctx.docbar.setViews([], null);
  ctx.inspector.replaceChildren();
  const wrap = el('div', 'sc-wrap');
  root.append(wrap);
  wrap.append(spaceHead('Carte 3D', 'Lecture du graphe complet…'), ...ctx.loading('Lecture du graphe…', 4, 'kpi'));
  const began = performance.now();
  let payload;
  try {
    payload = await ctx.api.memoryGraph();
  } catch (error) {
    if (ctx.signal.aborted) return undefined;
    wrap.replaceChildren(spaceHead('Carte 3D', null), ctx.empty(
      'Carte indisponible', failureText(error), null, { label: 'Réessayer', run: () => ctx.goto('carte', {}) },
    ));
    return undefined;
  }
  if (ctx.signal.aborted) return undefined;
  const g = decode(payload);
  if (!g.n) {
    wrap.replaceChildren(spaceHead('Carte 3D', null), ctx.empty(
      'Rien à cartographier',
      'Ni souvenirs, ni graphe de code, ni tâches dans ce projet.', null, undefined, { compact: true, art: 'i-empty-memory' },
    ));
    return undefined;
  }
  return start(wrap, ctx, g, began);
}

function start(wrap, ctx, g, began) {
  const adj = adjacency(g);
  const cam = new Camera();
  let th = theme();
  const kindOn = g.kinds.map(() => true);
  const state = {
    hover: -1, selected: -1, autoRotate: false, moving: false, dirty: true,
    first: 0, frames: 0, positions: g.pos, workerTicks: 0, workerDone: false, longestSlice: 0,
  };

  let view;
  const status = el('span', 'sc-status', '');
  const summary = () => {
    const drawn = view && view.drawnEdges < g.m ? ` · ${view.drawnEdges} tracés (échantillon uniforme, la machine ne suit pas)` : '';
    return `${g.n} nœuds · ${g.m} liens${drawn}`;
  };
  const search = el('input', null, null, { type: 'search', placeholder: 'Chercher un symbole, un souvenir, une tâche…', 'aria-label': 'Chercher dans la carte' });
  const kinds = el('fieldset', 'sc-kinds');
  const counts = g.kinds.map(() => 0);
  for (let i = 0; i < g.n; i += 1) counts[g.kind[i]] += 1;
  g.kinds.forEach((name, idx) => {
    const label = el('label');
    const box = el('input', null, null, { type: 'checkbox', 'data-kind': name });
    box.checked = true;
    box.addEventListener('change', () => { kindOn[idx] = box.checked; recolor(); });
    label.append(box, `${KIND_LABEL[name] || name} (${counts[idx]})`);
    kinds.append(label);
  });
  const bar = el('div', 'sc-bar');
  bar.append(search, kinds, status);
  const stage = el('div', 'sc-stage');
  const canvas = el('canvas', null, null, { 'aria-label': 'Carte du graphe', tabindex: '0', role: 'img' });
  const labels = el('div', 'sc-labels');
  const results = el('ul', 'sc-results');
  results.hidden = true;
  stage.append(canvas, labels, results);
  const head = spaceHead('Carte 3D', `${g.n} nœuds · ${g.m} liens`);
  wrap.replaceChildren(head, bar, stage);

  try {
    view = new GpuView(canvas, g);
  } catch {
    view = new Canvas2dView(canvas, g);
    stage.dataset.mode = '2d';
    wrap.insertBefore(el('div', 'sc-note', 'WebGL est indisponible : repli 2D, même graphe complet, même recherche.', { 'data-note': 'fallback' }), bar);
  }
  stage.dataset.mode = view.mode;
  stage.dataset.nodes = String(g.n);
  stage.dataset.edges = String(g.m);

  const colors = new Uint8Array(g.n * 4);
  const neighbors = new Set();
  const recolor = () => {
    th = theme();
    const focus = state.hover >= 0 ? state.hover : state.selected;
    neighbors.clear();
    if (focus >= 0) {
      neighbors.add(focus);
      for (let k = adj.start[focus]; k < adj.start[focus + 1]; k += 1) neighbors.add(adj.nbr[k]);
    }
    const dim = focus >= 0;
    for (let i = 0; i < g.n; i += 1) {
      const c = communityColor(g.community[i], th.dark);
      const on = kindOn[g.kind[i]];
      const lit = !dim || neighbors.has(i);
      colors[i * 4] = c[0] * 255; colors[i * 4 + 1] = c[1] * 255; colors[i * 4 + 2] = c[2] * 255;
      colors[i * 4 + 3] = on ? (lit ? 255 : 46) : 0;
    }
    if (focus >= 0) {
      const edges = [];
      for (let k = adj.start[focus]; k < adj.start[focus + 1]; k += 1) edges.push(focus, adj.nbr[k]);
      view.setHighlight(new Uint32Array(edges));
    } else {
      view.setHighlight(new Uint32Array(0));
    }
    view.setColors(colors);
    state.dirty = true;
  };

  let pointer = null;
  const toCss = (event) => {
    const box = canvas.getBoundingClientRect();
    return [event.clientX - box.left, event.clientY - box.top];
  };

  // Étiquettes : seulement le survol, et les nœuds les plus proches de la caméra quand on est près.
  const labelNodes = new Map();
  const refreshLabels = () => {
    labels.replaceChildren();
    labelNodes.clear();
    if (!view.vp) return;
    const [w, h] = view.mode === 'webgl' ? [canvas.clientWidth, canvas.clientHeight] : view.size;
    const wanted = [];
    if (state.hover >= 0) wanted.push([state.hover, true]);
    else if (state.selected >= 0) wanted.push([state.selected, true]);
    if (cam.dist < radiusOf(g.pos) * 0.9) {
      const eye = cam.eye();
      const near = [];
      for (let i = 0; i < g.n; i += 1) {
        if (!kindOn[g.kind[i]]) continue;
        const d = (g.pos[i * 3] - eye[0]) ** 2 + (g.pos[i * 3 + 1] - eye[1]) ** 2 + (g.pos[i * 3 + 2] - eye[2]) ** 2;
        near.push([d, i]);
        if (near.length > 4000) { near.sort((a, b) => a[0] - b[0]); near.length = 200; }
      }
      near.sort((a, b) => a[0] - b[0]);
      for (const [, i] of near.slice(0, 14)) wanted.push([i, false]);
    }
    const seen = new Set();
    for (const [i, strong] of wanted) {
      if (seen.has(i)) continue;
      seen.add(i);
      const s = project(view.vp, [g.pos[i * 3], g.pos[i * 3 + 1], g.pos[i * 3 + 2]], w, h);
      if (!s || s.x < 0 || s.y < 0 || s.x > w || s.y > h) continue;
      const tag = el('span', 'sc-label', g.label[i]);
      if (strong) tag.dataset.hover = '1';
      tag.style.left = `${s.x}px`; tag.style.top = `${s.y}px`;
      labels.append(tag);
    }
  };

  // Entrée sélectionnée → inspecteur.
  const inspect = async (i) => {
    const kind = g.kinds[g.kind[i]];
    const box = el('div', 'sc-detail');
    box.append(el('h3', null, g.label[i]), el('p', 'sc-meta', `${KIND_LABEL[kind] || kind} · ${adj.start[i + 1] - adj.start[i]} liens`));
    box.dataset.node = String(i);
    ctx.inspector.replaceChildren(box);
    ctx.openPanel('inspector');
    if (kind === 'souvenir') {
      try {
        const data = await ctx.api.memoryEntry(g.ref[i]);
        if (state.selected !== i || ctx.signal.aborted) return;
        if (data.found && data.entry) {
          box.append(el('p', null, data.entry.excerpt || data.entry.title || ''));
          box.dataset.entry = data.entry.id || g.ref[i];
          const open = el('button', 'btn', 'Ouvrir dans la Recherche', { type: 'button' });
          open.addEventListener('click', () => ctx.goto('memoire', { q: '', entry: g.ref[i] }));
          box.append(open);
        }
      } catch { /* l'inspecteur garde le titre : la fiche n'est pas indispensable au clic */ }
    } else if (kind === 'tache') {
      const open = el('button', 'btn', 'Ouvrir la tâche', { type: 'button' });
      open.addEventListener('click', () => ctx.goto('travail', { task: g.ref[i] }));
      box.append(open);
    } else {
      box.append(el('p', 'sc-meta', g.ref[i]));
    }
    box.dataset.ready = '1';
  };

  const select = (i) => {
    state.selected = i;
    recolor();
    if (i >= 0) inspect(i);
    refreshLabels();
  };

  // Interaction souris.
  let drag = null;
  let idle;
  const markMoving = () => {
    state.moving = true; state.dirty = true;
    clearTimeout(idle);
    idle = setTimeout(() => { state.moving = false; state.dirty = true; refreshLabels(); status.textContent = summary(); }, 180);
  };
  canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, moved: false }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => {
    if (drag) {
      const dx = e.clientX - drag.x; const dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      drag.x = e.clientX; drag.y = e.clientY;
      cam.yaw -= dx * 0.006;
      cam.pitch = Math.max(-1.5, Math.min(1.5, cam.pitch + dy * 0.006));
      markMoving();
    } else {
      pointer = toCss(e);
      hoverSoon();
    }
  });
  canvas.addEventListener('pointerup', (e) => {
    const was = drag; drag = null;
    if (was && !was.moved) {
      const [x, y] = toCss(e);
      select(view.pick(cam, x, y));
    }
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    cam.dist = Math.max(20, Math.min(radiusOf(g.pos) * 6, cam.dist * Math.exp(e.deltaY * 0.0012)));
    markMoving();
  }, { passive: false });
  let hoverPending = false;
  const hoverSoon = () => {
    if (hoverPending) return;
    hoverPending = true;
    requestAnimationFrame(() => {
      hoverPending = false;
      if (!pointer || drag || ctx.signal.aborted) return;
      const next = view.pick(cam, pointer[0], pointer[1]);
      if (next !== state.hover) { state.hover = next; recolor(); refreshLabels(); canvas.style.cursor = next >= 0 ? 'pointer' : ''; }
    });
  };
  canvas.addEventListener('pointerleave', () => { pointer = null; if (state.hover >= 0) { state.hover = -1; recolor(); refreshLabels(); } });

  // Recherche : liste de résultats, Entrée centre la caméra sur le premier.
  const matches = (term) => {
    const q = term.trim().toLowerCase();
    if (!q) return [];
    const exact = []; const prefix = []; const inner = [];
    for (let i = 0; i < g.n; i += 1) {
      if (!kindOn[g.kind[i]]) continue;
      const text = g.label[i].toLowerCase();
      const at = text.indexOf(q);
      if (at < 0) continue;
      if (text === q) exact.push(i); else if (at === 0) prefix.push(i); else inner.push(i);
      if (exact.length + prefix.length + inner.length > 200) break;
    }
    return [...exact, ...prefix, ...inner].slice(0, 8);
  };
  const focusOn = (i) => {
    results.hidden = true;
    cam.flyTo([g.pos[i * 3], g.pos[i * 3 + 1], g.pos[i * 3 + 2]], Math.max(60, radiusOf(g.pos) * 0.35), performance.now());
    state.dirty = true;
    markMoving();
    select(i);
  };
  search.addEventListener('input', () => {
    const found = matches(search.value);
    results.replaceChildren();
    results.hidden = !found.length;
    for (const i of found) {
      const item = el('li');
      const button = el('button', null, g.label[i], { type: 'button', 'data-node': String(i) });
      button.addEventListener('click', () => focusOn(i));
      item.append(button);
      results.append(item);
    }
  });
  search.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const found = matches(search.value);
    if (found.length) focusOn(found[0]);
  });

  // Affinage de la disposition : un Web Worker, jamais le fil principal.
  let worker = null;
  try {
    worker = new Worker(new URL('./carte-worker.js', import.meta.url));
    worker.onmessage = (event) => {
      const msg = event.data;
      if (msg.type === 'positions') {
        if (!state.workerStartedAt) state.workerStartedAt = performance.now();
        state.workerTicks = msg.tick; state.longestSlice = Math.max(state.longestSlice, msg.ms);
        g.pos = msg.pos; state.positions = msg.pos;
        view.setPositions(msg.pos);
        state.dirty = true;
        status.textContent = `Affinage ${msg.tick}/${msg.ticks}`;
      } else if (msg.type === 'done') {
        state.workerDone = true;
        status.textContent = summary();
        refreshLabels();
      }
    };
    worker.onerror = () => { state.workerDone = true; status.textContent = summary(); };
    const copy = new Float32Array(g.pos);
    worker.postMessage({ type: 'start', pos: copy, src: new Int32Array(g.src), dst: new Int32Array(g.dst), ticks: 80 }, [copy.buffer]);
  } catch {
    state.workerDone = true;
  }

  // Boucle de rendu : à la demande, en continu si la caméra tourne.
  cam.fit(radiusOf(g.pos));
  recolor();
  let lastFrame = 0;
  // Le fil principal ne doit jamais rester bloqué : la présentation d'une image GL logicielle peut le
  // bloquer bien après la soumission. Une tâche longue réduit les liens tracés au repos, le calme les rend.
  let lastLong = 0;
  let observerLong = null;
  try {
    observerLong = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration < 50 || view.mode !== 'webgl') continue;
        lastLong = performance.now();
        view.restFraction = Math.max(0.03, view.restFraction * 0.7);
        view.edgeFraction = Math.min(view.edgeFraction, view.restFraction);
      }
    });
    observerLong.observe({ type: 'longtask' });
  } catch { /* pas de longtask : le budget d'images reste le seul garde */ }
  const frame = (now) => {
    if (ctx.signal.aborted) return;
    const flying = cam.step(now);
    if (state.autoRotate) { cam.yaw += 0.01; state.moving = true; }
    if (flying) state.moving = true;
    // Pendant l'affinage, chaque pas du worker redessine : liens décimés comme en mouvement.
    const refining = Boolean(worker) && !state.workerDone;
    if (refining) view.edgeFraction = Math.min(view.edgeFraction, 0.1);
    if (state.dirty || state.autoRotate || flying) {
      view.draw(cam, th, state.moving || refining);
      state.frames += 1;
      if (!state.first) {
        state.first = performance.now() - began;
        stage.dataset.firstRender = String(Math.round(state.first));
        status.textContent = summary();
      }
      if (view.mode === 'webgl' && performance.now() - lastLong > 2000) view.restFraction = Math.min(1, view.restFraction * 1.08);
      // Budget adaptatif sur l'intervalle réel entre images (le coût GPU n'est pas visible côté JS) :
      // trop lent, moins de liens pendant le mouvement ; large marge, on en rend à nouveau.
      if ((state.moving || refining) && lastFrame) {
        const dt = now - lastFrame;
        view.edgeFraction = Math.max(0.03, Math.min(1, view.edgeFraction * (dt > 30 ? 0.8 : dt < 20 ? 1.04 : 1)));
      }
      state.dirty = false;
      if (flying && !cam.flight) { state.moving = false; refreshLabels(); }
    }
    lastFrame = state.moving || refining ? now : 0;
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);

  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const onTheme = () => recolor();
  media.addEventListener('change', onTheme);
  const observer = new MutationObserver(onTheme);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  window.addEventListener('resize', markMoving);

  // Prise pour les mesures (tests e2e, console) : lecture seule de l'état, plus quelques gestes.
  window.GrimoireCarte = {
    get nodes() { return g.n; },
    get edges() { return g.m; },
    get mode() { return view.mode; },
    get renderedNodes() { return view.drawnNodes; },
    get renderedEdges() { return view.drawnEdges; },
    get firstRenderMs() { return state.first; },
    get frames() { return state.frames; },
    get workerTicks() { return state.workerTicks; },
    get workerDone() { return state.workerDone; },
    get workerStartedAt() { return state.workerStartedAt || 0; },
    get layoutReused() { return Boolean(g.layout.reused); },
    get selected() { return state.selected; },
    get target() { return [...cam.target]; },
    get flying() { return Boolean(cam.flight); },
    label: (i) => g.label[i],
    find: (text) => matches(text)[0] ?? -1,
    position: (i) => [g.pos[i * 3], g.pos[i * 3 + 1], g.pos[i * 3 + 2]],
    /** Position écran (px CSS) du nœud `i`, ou null s'il est derrière la caméra. */
    screenOf: (i) => {
      if (!view.vp) return null;
      const [w, h] = view.mode === 'webgl' ? [canvas.clientWidth, canvas.clientHeight] : view.size;
      return project(view.vp, [g.pos[i * 3], g.pos[i * 3 + 1], g.pos[i * 3 + 2]], w, h);
    },
    tune: (o) => Object.assign(view, o),
    setAutoRotate: (on) => { state.autoRotate = Boolean(on); if (!on) { state.moving = false; state.dirty = true; } },
  };

  ctx.signal.addEventListener('abort', () => {
    if (worker) worker.postMessage({ type: 'stop' });
    if (worker) worker.terminate();
    media.removeEventListener('change', onTheme);
    observer.disconnect();
    if (observerLong) observerLong.disconnect();
    window.removeEventListener('resize', markMoving);
    clearTimeout(idle);
    delete window.GrimoireCarte;
  });
  return () => {};
}
