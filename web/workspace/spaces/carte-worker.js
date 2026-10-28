// Savoir › Carte 3D — l'affinage de la disposition, dans un Web Worker (P81).
//
// Le fil principal ne calcule jamais une force : il reçoit des positions.
// Disposition à forces de Fruchterman-Reingold où la répulsion est évaluée par
// Barnes-Hut (octree, O(n log n)) et l'attraction le long des liens. Le point
// de départ est la disposition par communautés gardée côté serveur ; le worker
// ne fait qu'affiner, avec un refroidissement, et rend ses positions en
// Float32Array transféré (aucune copie structurée).
//
// Protocole :
//   → { type: 'start', pos: Float32Array(3n), src: Int32Array, dst: Int32Array, ticks }
//   → { type: 'stop' }
//   ← { type: 'positions', pos: Float32Array(3n), tick, ticks, ms }  (transféré)
//   ← { type: 'done', tick }

let running = 0;

self.onmessage = (event) => {
  const msg = event.data;
  if (msg.type === 'stop') { running += 1; return; }
  if (msg.type === 'start') {
    running += 1;
    run(msg, running);
  }
};

const THETA2 = 0.81; // (ouverture 0,9)²
const MAX_PER_SLICE_MS = 12;

/** Octree à plat : 8 enfants par cellule, masse et centre de masse accumulés. */
class Octree {
  constructor(capacity) {
    this.child = new Int32Array(capacity * 8);
    this.mass = new Float32Array(capacity);
    this.cx = new Float32Array(capacity);
    this.cy = new Float32Array(capacity);
    this.cz = new Float32Array(capacity);
    this.ox = new Float32Array(capacity); // coin minimal
    this.oy = new Float32Array(capacity);
    this.oz = new Float32Array(capacity);
    this.size = new Float32Array(capacity);
    this.leaf = new Int32Array(capacity); // nœud porté par une feuille, -1 sinon
    this.count = 0;
  }

  reset(ox, oy, oz, size) {
    this.count = 0;
    return this.cell(ox, oy, oz, size);
  }

  cell(ox, oy, oz, size) {
    const i = this.count++;
    this.child.fill(-1, i * 8, i * 8 + 8);
    this.mass[i] = 0; this.cx[i] = 0; this.cy[i] = 0; this.cz[i] = 0;
    this.ox[i] = ox; this.oy[i] = oy; this.oz[i] = oz; this.size[i] = size;
    this.leaf[i] = -1;
    return i;
  }

  insert(root, node, x, y, z, px, py, pz) {
    let cell = root;
    for (let depth = 0; depth < 40; depth += 1) {
      const m = this.mass[cell];
      this.cx[cell] = (this.cx[cell] * m + x) / (m + 1);
      this.cy[cell] = (this.cy[cell] * m + y) / (m + 1);
      this.cz[cell] = (this.cz[cell] * m + z) / (m + 1);
      this.mass[cell] = m + 1;
      if (m === 0) { this.leaf[cell] = node; return; }
      const held = this.leaf[cell];
      if (held >= 0) {
        // La feuille devient interne : son nœud descend d'un niveau.
        this.leaf[cell] = -1;
        this.descend(cell, held, px[held * 3], px[held * 3 + 1], px[held * 3 + 2]);
      }
      cell = this.descend(cell, node, x, y, z, true);
    }
  }

  /** L'enfant de `cell` qui contient (x,y,z) ; le crée au besoin. Sans `walk`, y dépose le nœud. */
  descend(cell, node, x, y, z, walk = false) {
    const half = this.size[cell] / 2;
    const ix = x >= this.ox[cell] + half ? 1 : 0;
    const iy = y >= this.oy[cell] + half ? 1 : 0;
    const iz = z >= this.oz[cell] + half ? 1 : 0;
    const slot = cell * 8 + ix + iy * 2 + iz * 4;
    let next = this.child[slot];
    if (next < 0) {
      next = this.cell(this.ox[cell] + ix * half, this.oy[cell] + iy * half, this.oz[cell] + iz * half, half);
      this.child[slot] = next;
      if (!walk) {
        this.mass[next] = 1; this.cx[next] = x; this.cy[next] = y; this.cz[next] = z;
        this.leaf[next] = node;
        return next;
      }
    } else if (!walk) {
      const m = this.mass[next];
      this.cx[next] = (this.cx[next] * m + x) / (m + 1);
      this.cy[next] = (this.cy[next] * m + y) / (m + 1);
      this.cz[next] = (this.cz[next] * m + z) / (m + 1);
      this.mass[next] = m + 1;
    }
    return next;
  }
}

function run(msg, token) {
  const n = msg.pos.length / 3;
  const pos = new Float32Array(msg.pos);
  const { src, dst } = msg;
  const ticks = msg.ticks || 120;
  const disp = new Float32Array(n * 3);
  const tree = new Octree(Math.max(64, n * 4));
  // Longueur idéale d'un lien : racine cubique du volume par nœud.
  let min = Infinity; let max = -Infinity;
  for (let i = 0; i < pos.length; i += 1) { if (pos[i] < min) min = pos[i]; if (pos[i] > max) max = pos[i]; }
  const span = Math.max(1, max - min);
  const k = span / Math.cbrt(Math.max(1, n)) * 0.6;
  const k2 = k * k;
  let tick = 0;
  let temperature = span * 0.01;

  const step = () => {
    // Octree englobant.
    let lox = Infinity; let loy = Infinity; let loz = Infinity; let hi = -Infinity;
    for (let i = 0; i < n; i += 1) {
      const x = pos[i * 3]; const y = pos[i * 3 + 1]; const z = pos[i * 3 + 2];
      if (x < lox) lox = x; if (y < loy) loy = y; if (z < loz) loz = z;
      if (x > hi) hi = x; if (y > hi) hi = y; if (z > hi) hi = z;
    }
    const size = Math.max(1e-3, hi - Math.min(lox, loy, loz)) * 1.001;
    const base = Math.min(lox, loy, loz);
    const root = tree.reset(base, base, base, size);
    for (let i = 0; i < n; i += 1) tree.insert(root, i, pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2], pos);
    disp.fill(0);
    const stack = new Int32Array(512);
    for (let i = 0; i < n; i += 1) {
      const x = pos[i * 3]; const y = pos[i * 3 + 1]; const z = pos[i * 3 + 2];
      let fx = 0; let fy = 0; let fz = 0;
      let top = 0; stack[top++] = root;
      while (top > 0) {
        const c = stack[--top];
        const leafNode = tree.leaf[c];
        if (leafNode === i) continue;
        const dx = x - tree.cx[c]; const dy = y - tree.cy[c]; const dz = z - tree.cz[c];
        const d2 = dx * dx + dy * dy + dz * dz + 1e-6;
        const s = tree.size[c];
        if (leafNode >= 0 || s * s / d2 < THETA2) {
          const f = k2 * tree.mass[c] / d2;
          fx += dx * f; fy += dy * f; fz += dz * f;
        } else {
          for (let o = 0; o < 8; o += 1) {
            const ch = tree.child[c * 8 + o];
            if (ch >= 0 && top < 511) stack[top++] = ch;
          }
        }
      }
      disp[i * 3] = fx; disp[i * 3 + 1] = fy; disp[i * 3 + 2] = fz;
    }
    // Attraction le long des liens.
    for (let e = 0; e < src.length; e += 1) {
      const a = src[e] * 3; const b = dst[e] * 3;
      const dx = pos[a] - pos[b]; const dy = pos[a + 1] - pos[b + 1]; const dz = pos[a + 2] - pos[b + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6;
      const f = d / k;
      const ux = dx * f; const uy = dy * f; const uz = dz * f;
      disp[a] -= ux; disp[a + 1] -= uy; disp[a + 2] -= uz;
      disp[b] += ux; disp[b + 1] += uy; disp[b + 2] += uz;
    }
    // Gravité douce vers l'origine, puis déplacement borné par la température.
    for (let i = 0; i < n; i += 1) {
      const o = i * 3;
      disp[o] -= pos[o] * 0.02 * k; disp[o + 1] -= pos[o + 1] * 0.02 * k; disp[o + 2] -= pos[o + 2] * 0.02 * k;
      const len = Math.sqrt(disp[o] * disp[o] + disp[o + 1] * disp[o + 1] + disp[o + 2] * disp[o + 2]) + 1e-9;
      const m = Math.min(len, temperature) / len;
      pos[o] += disp[o] * m; pos[o + 1] += disp[o + 1] * m; pos[o + 2] += disp[o + 2] * m;
    }
    temperature *= 0.96;
  };

  const loop = () => {
    if (token !== running) return;
    const began = performance.now();
    // Une tranche courte, puis on rend la main : un `stop` reçu s'applique sans attendre.
    while (tick < ticks && performance.now() - began < MAX_PER_SLICE_MS) {
      step();
      tick += 1;
      if (performance.now() - began >= MAX_PER_SLICE_MS) break;
    }
    const copy = new Float32Array(pos);
    self.postMessage({ type: 'positions', pos: copy, tick, ticks, ms: performance.now() - began }, [copy.buffer]);
    if (tick < ticks) setTimeout(loop, 0);
    else self.postMessage({ type: 'done', tick });
  };
  loop();
}
