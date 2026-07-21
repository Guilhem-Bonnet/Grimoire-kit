/* bp2-core.js — Blueprint Studio v2 (moteur)
   Évolution de blueprint-editor.js :
   · sous-flows C4 (grouper ⌘G, entrer au double-clic, fil d'Ariane, ports)
   · documents portés par les nodes (via bp2-docs.js)
   · coût en tokens (via bp2-cost.js) — chip toolbar, vue chaleur, onglet COÛT
   · suggestions fantômes + règles de bonnes pratiques (via bp2-assist.js)
   Invariant inchangé : un blueprint se valide, se simule, se compile — n'exécute JAMAIS.
   ========================================================================== */
(async function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = Atelier.esc;

  await Atelier.ready;
  if (!Atelier.project()) { location.href = 'atelier.html'; return; }
  await Atelier.data();
  const CAT = Atelier.catalogue;

  /* ══ Mini event-emitter ══ */
  const listeners = {};
  const emit = (ev, data) => (listeners[ev] || []).forEach(cb => cb(data));
  const on = (ev, cb) => { (listeners[ev] = listeners[ev] || []).push(cb); };

  /* ══ État ══ */
  let state = null;            // racine { nodes, edges, comments, view, meta }
  let bpId = null;
  let curPath = [];            // ids de groupes → niveau courant (C4)
  let history = [];
  let heatMode = false;
  let ctxPressure = {};        // nodeId → verdict de pression (simulation serveur)
  let selection = { nodes: new Set(), edge: null, comment: null };
  const uid = () => 'x' + Math.random().toString(36).slice(2, 8);

  const wrap = $('#bp-canvas'), world = $('#bp-world'), svg = $('#bp-edges'), items = $('#bp-items');
  let view = { x: 80, y: 40, k: 1 };

  function blankState() {
    return { nodes: [], edges: [], comments: [], view: null,
      meta: { validated: false, simulated: false, compiledAt: null, dirty: true, path: [] } };
  }

  /* ══ Niveaux (C4) ══ */
  function levelGraph(path) {
    let g = state;
    for (const id of path) {
      const n = (g.nodes || []).find(x => x.id === id && x.kind === 'group');
      if (!n) return null;
      n.sub = n.sub || { nodes: [], edges: [], comments: [] };
      g = n.sub;
    }
    return g;
  }
  const G = () => levelGraph(curPath) || state;
  function groupAt(path) {
    if (!path.length) return null;
    const parent = levelGraph(path.slice(0, -1));
    return parent ? parent.nodes.find(n => n.id === path[path.length - 1]) : null;
  }
  const curGroup = () => groupAt(curPath);
  function storeView() {
    const grp = curGroup();
    if (grp) grp.subView = { ...view };
    else state.view = { ...view };
  }
  function restoreView() {
    const grp = curGroup();
    const v = grp ? grp.subView : state.view;
    view = v ? { ...v } : { x: 80, y: 40, k: 1 };
  }

  /* ══ Specs (patterns + groupes) ══ */
  function groupPins(grp) {
    const ins = [], outs = [];
    (grp.sub && grp.sub.nodes || []).forEach(sn => {
      const ss = specOf(sn);
      if (!ss) return;
      ss.in.forEach(c => { if (!grp.sub.edges.some(e => e.to === sn.id && e.contract === c) && !ins.includes(c)) ins.push(c); });
      ss.out.forEach(c => { if (!grp.sub.edges.some(e => e.from === sn.id && e.contract === c) && !outs.includes(c)) outs.push(c); });
    });
    return { ins, outs };
  }
  function countInside(grp) {
    let n = 0, docs = 0;
    (grp.sub && grp.sub.nodes || []).forEach(sn => {
      if (sn.kind === 'group') { const c = countInside(sn); n += c.n; docs += c.docs; }
      else { n++; docs += Object.keys(sn.docs || {}).length; }
    });
    docs += Object.keys(grp.docs || {}).length;
    return { n, docs };
  }
  function specOf(n) {
    if (!n) return null;
    if (n.kind === 'agent' || n.kind === 'trigger') return window.BP2Team ? BP2Team.specOf(n) : null;
    if (n.kind === 'group') {
      const p = groupPins(n);
      const c = countInside(n);
      return { kind: 'group', ref: null, name: n.name || 'sous-flow', cat: 'GRP',
        desc: c.n ? `${c.n} node${c.n > 1 ? 's' : ''} interne${c.n > 1 ? 's' : ''}` : 'vide — double-cliquez pour construire',
        in: p.ins, out: p.outs, agents: [] };
    }
    return Atelier.nodeSpec(n.ref);
  }
  const isExtLocked = n => { const s = specOf(n); return s && s.kind === 'ext' && s.locked; };

  /* ══ Persistance ══ */
  let saveT = null;
  function persist() {
    clearTimeout(saveT);
    saveT = setTimeout(() => {
      storeView();
      state.meta.path = curPath.slice();
      Atelier.saveBp(bpId, state);
      localStorage.setItem('grimoire.atelier.bp.current', bpId);
      $('#bp-saved').textContent = 'sauvegardé ✓ auto';
    }, 250);
  }
  function pushHistory() {
    history.push(JSON.stringify({ nodes: state.nodes, edges: state.edges, comments: state.comments }));
    if (history.length > 60) history.shift();
  }
  function undo() {
    const prev = history.pop();
    if (!prev) return;
    const s = JSON.parse(prev);
    state.nodes = s.nodes; state.edges = s.edges; state.comments = s.comments;
    if (!levelGraph(curPath)) curPath = [];
    clearSelection(); markDirty(); render();
  }
  function markDirty() {
    state.meta.dirty = true;
    state.meta.validated = false;
    state.meta.simulated = false;
    ctxPressure = {};
    updateCompileBtn();
    runValidation(true);
    if (window.BP2Cost) BP2Cost.refresh();
    persist();
    emit('mutated', state);
  }

  /* ══ Vue (pan/zoom) ══ */
  function applyView() {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
    $('#hud-zoom').textContent = Math.round(view.k * 100) + ' %';
  }
  function toWorld(cx, cy) {
    const r = wrap.getBoundingClientRect();
    return { x: (cx - r.left - view.x) / view.k, y: (cy - r.top - view.y) / view.k };
  }
  function zoomAt(cx, cy, f) {
    const r = wrap.getBoundingClientRect();
    const px = cx - r.left, py = cy - r.top;
    const k2 = Math.min(2, Math.max(0.3, view.k * f));
    view.x = px - (px - view.x) * (k2 / view.k);
    view.y = py - (py - view.y) * (k2 / view.k);
    view.k = k2;
    applyView(); persist();
  }
  function fitView() {
    const g = G();
    if (!g.nodes.length && !g.comments.length) { view = { x: 80, y: 40, k: 1 }; applyView(); return; }
    const xs = [], ys = [], xe = [], ye = [];
    g.nodes.forEach(n => { xs.push(n.x); ys.push(n.y); xe.push(n.x + (n._w || 190)); ye.push(n.y + (n._h || 80)); });
    g.comments.forEach(c => { xs.push(c.x); ys.push(c.y); xe.push(c.x + c.w); ye.push(c.y + c.h); });
    const bx = Math.min(...xs) - 60, by = Math.min(...ys) - 60;
    const bw = Math.max(...xe) - bx + 60, bh = Math.max(...ye) - by + 60;
    const r = wrap.getBoundingClientRect();
    view.k = Math.min(1.15, Math.max(0.3, Math.min(r.width / bw, r.height / bh)));
    view.x = (r.width - bw * view.k) / 2 - bx * view.k;
    view.y = (r.height - bh * view.k) / 2 - by * view.k;
    applyView(); persist();
  }

  /* ══ Rendu ══ */
  const PIN_TOP = 38, PIN_GAP = 22;

  function nodeHtml(n) {
    const s = specOf(n);
    if (!s) return '';
    const isGrp = n.kind === 'group';
    const isAg = n.kind === 'agent';
    const isTrg = n.kind === 'trigger';
    const color = isGrp ? '#A78BFA' : (isAg ? BP2Team.COLOR : (isTrg ? '#FDBA74' : Atelier.catColor(s.cat)));
    const locked = isExtLocked(n);
    const ins = s.in.map((c, i) =>
      `<div class="bp-pin in" data-node="${n.id}" data-dir="in" data-contract="${c}" style="top:${PIN_TOP + i * PIN_GAP}px;border-color:${Atelier.contractColor(c)}"><span class="bp-pin-lbl">${esc(c)}</span></div>`).join('');
    const outs = s.out.map((c, i) =>
      `<div class="bp-pin out" data-node="${n.id}" data-dir="out" data-contract="${c}" style="top:${PIN_TOP + i * PIN_GAP}px;border-color:${Atelier.contractColor(c)}"><span class="bp-pin-lbl">${esc(c)}</span></div>`).join('');
    const minH = PIN_TOP + Math.max(s.in.length, s.out.length, 1) * PIN_GAP - 14;
    const warns = (state._warnByNode && state._warnByNode[n.id]) || null;
    const badge = window.BP2Docs ? BP2Docs.badgeFor(n) : null;
    let cost = '';
    if (heatMode && window.BP2Cost) {
      const k = BP2Cost.nodeK(n, specOf);
      cost = `<span class="cost-badge">~${BP2Cost.fmtK(k)} tok</span>`;
    }
    const heatCls = heatMode && window.BP2Cost ? BP2Cost.heatClass(n, G(), specOf) : '';
    const ctxIso = ctxOf(n).isolation === 'isolated';
    const ctxCrit = ctxPressure[n.id] === 'critical';
    return `<div class="bp-node${isGrp ? ' group' : ''}${isAg ? ' agent' : ''}${isTrg ? ' trigger' : ''}${selection.nodes.has(n.id) ? ' sel' : ''}${heatCls}${ctxIso ? ' ctx-isolated' : ''}${ctxCrit ? ' ctx-critical' : ''}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px${locked ? ';border-style:dashed;opacity:.75' : ''}">
      ${warns ? `<span class="warn-badge" data-warn="${n.id}">⚠ ${esc(warns)}</span>` : ''}
      ${cost}
      <div class="head" style="background:${color}14">
        ${isGrp ? '<span class="g-ico">◇</span>' : (isAg ? `<span class="ag-ava">${BP2Team.initials(n.name)}</span>` : (isTrg ? '<span class="ag-ava trg">▶</span>' : `<span class="cat" style="background:${color}"></span>`))}
        <span class="ref" style="color:${color}">${esc(isGrp ? (n.name || 'sous-flow') : (isAg || isTrg ? (n.name || s.name) : (s.kind === 'ext' ? s.name : s.ref)))}</span>
        ${s.kind === 'ext' ? `<span class="ext-tag">ext · ${esc(s.ext)}</span>` : ''}
        ${isAg ? `<span class="ag-role-tag">${n.role === 'orchestrateur' ? 'orchestre' : (n.sub ? 'sous-agent' : 'agent')}</span>` : ''}
        ${isTrg ? `<span class="ag-role-tag">${esc(n.trig || 'manuel')}</span>` : ''}
        ${isGrp ? `<span class="ext-tag" style="margin-left:auto">N${curPath.length + 2}</span>` : ''}
      </div>
      <div class="body" style="min-height:${minH}px">${esc(isGrp ? s.desc : (isAg || isTrg ? s.desc : (s.kind === 'ext' ? (locked ? 'extension non installée' : s.desc.split('—')[0]) : s.name)))}${isGrp ? '<span class="g-meta">sous-flow · double-clic pour entrer</span>' : ''}${isAg ? BP2Team.bodyMeta(n) : ''}${isTrg ? '<span class="g-meta">double-clic : configurer le départ</span>' : ''}</div>
      ${isGrp ? '<span class="enter-hint">entrer ↵</span>' : ''}
      ${badge && badge.n ? `<span class="doc-badge${badge.edited ? ' edited' : ''}" data-docs="${n.id}" title="Documents du node — ouvrir le dossier">▤ ${badge.n} doc${badge.n > 1 ? 's' : ''}${badge.edited ? ' ·' + badge.edited + ' édité' + (badge.edited > 1 ? 's' : '') : ''}</span>` : ''}
      ${ins}${outs}
    </div>`;
  }

  function pinPos(nodeId, dir, contract) {
    const n = G().nodes.find(x => x.id === nodeId);
    const s = n && specOf(n);
    if (!s) return { x: 0, y: 0 };
    const list = dir === 'in' ? s.in : s.out;
    const i = Math.max(0, list.indexOf(contract));
    return { x: n.x + (dir === 'in' ? 0 : (n._w || (n.kind === 'group' ? 210 : 190))), y: n.y + PIN_TOP + i * PIN_GAP + 6 };
  }
  function edgePath(e) {
    const a = pinPos(e.from, 'out', e.contract);
    const b = pinPos(e.to, 'in', e.contract);
    const dx = Math.min(160, Math.max(40, Math.abs(b.x - a.x) * 0.5));
    return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
  }

  function ghostsHtml() {
    if (!window.BP2Assist || selection.nodes.size !== 1) return '';
    const n = G().nodes.find(x => x.id === Array.from(selection.nodes)[0]);
    if (!n) return '';
    const s = specOf(n);
    if (!s || !s.out.length) return '';
    const sugg = BP2Assist.suggestFor(n, G(), specOf).slice(0, 3);
    const baseX = n.x + (n._w || 190) + 86;
    return sugg.map((g, i) => `
      <div class="bp-ghost" data-gref="${esc(g.ref)}" data-gcontract="${esc(g.contract)}" data-gfrom="${n.id}"
           style="left:${baseX}px;top:${n.y + i * 70 - 6}px">
        <span class="r1"><span class="plus">＋</span><span class="cat" style="background:${Atelier.catColor(g.cat)}"></span>${esc(g.label)}</span>
        <span class="why">${esc(g.why)}</span>
        <span class="src">suggestion · pratique agentique</span>
      </div>`).join('');
  }

  function render() {
    const g = G();
    items.innerHTML =
      g.comments.map(c => `
        <div class="bp-comment${selection.comment === c.id ? ' sel' : ''}" data-cid="${c.id}" style="left:${c.x}px;top:${c.y}px;width:${c.w}px;height:${c.h}px${c.color ? `;border-color:${c.color}55` : ''}">
          <div class="cm-head" data-cmove="${c.id}"${c.color ? ` style="background:${c.color}10"` : ''}><input value="${esc(c.label)}" data-clabel="${c.id}" spellcheck="false"${c.color ? ` style="color:${c.color}"` : ''} /></div>
        </div>`).join('') +
      g.nodes.map(nodeHtml).join('') +
      ghostsHtml();

    $$('.bp-node', items).forEach(el => {
      const n = g.nodes.find(x => x.id === el.dataset.id);
      if (n) { n._w = el.offsetWidth; n._h = el.offsetHeight; }
    });

    svg.innerHTML = g.edges.map(e => `
      <path class="edge-hit" data-eid="${e.id}" d="${edgePath(e)}"></path>
      <path class="edge${e.channel && e.channel !== 'happy' ? ' edge-' + e.channel : ''}${selection.edge === e.id ? ' sel' : ''}" data-e="${e.id}" d="${edgePath(e)}" stroke="${Atelier.contractColor(e.contract)}"></path>`).join('')
      + '<path class="ghost" id="ghost-wire" d="" style="display:none"></path>';

    $$('.edge-label', world).forEach(el => el.remove());
    g.edges.forEach(e => {
      const p = svg.querySelector(`path.edge[data-e="${e.id}"]`);
      if (!p) return;
      try {
        const m = p.getPointAtLength(p.getTotalLength() / 2);
        const lbl = document.createElement('div');
        lbl.className = 'edge-label';
        lbl.style.left = m.x + 'px'; lbl.style.top = m.y + 'px';
        let flow = '';
        if (heatMode && window.BP2Cost) {
          const src = g.nodes.find(x => x.id === e.from);
          if (src) flow = ` <span class="flowtok">· ~${BP2Cost.fmtK(BP2Cost.flowK(src, specOf))}</span>`;
        }
        lbl.innerHTML = esc(e.contract) + flow;
        world.appendChild(lbl);
      } catch (err) { /* path pas encore mesurable */ }
    });

    renderCrumb();
    renderPorts();
    applyView();
    renderInspector();
    renderPalette();
  }

  /* ── Fil d'Ariane ── */
  function renderCrumb() {
    const el = $('#bp-crumb');
    let html = `<button class="c-seg${curPath.length ? '' : ' cur'}" data-lv="-1"><span>◆ ${esc(bpId)}</span><span class="lv">N1</span></button>`;
    curPath.forEach((id, i) => {
      const grp = groupAt(curPath.slice(0, i + 1));
      html += `<span class="c-sep">›</span>
        <button class="c-seg${i === curPath.length - 1 ? ' cur' : ''}" data-lv="${i}"><span>◇ ${esc(grp ? grp.name || 'sous-flow' : '?')}</span><span class="lv">N${i + 2}</span></button>`;
    });
    if (curPath.length) html += `<button class="c-up" data-up="1" title="Remonter d'un niveau (Échap)">↑ remonter</button>`;
    el.innerHTML = html;
    $$('.c-seg', el).forEach(b => b.addEventListener('click', () => {
      const lv = parseInt(b.dataset.lv, 10);
      goToLevel(lv < 0 ? [] : curPath.slice(0, lv + 1));
    }));
    const up = $('.c-up', el);
    if (up) up.addEventListener('click', upLevel);
  }
  function renderPorts() {
    $$('.bp-ports', wrap).forEach(el => el.remove());
    const grp = curGroup();
    if (!grp) return;
    const p = groupPins(grp);
    const row = c => `<div class="p-row"><span class="d" style="background:${Atelier.contractColor(c)}"></span>${esc(c)}</div>`;
    const mk = (side, title, list) => {
      const el = document.createElement('div');
      el.className = 'bp-ports ' + side;
      el.innerHTML = `<div class="t">${title}</div>` + (list.length ? list.map(row).join('') : '<div class="none">aucun — tout est résolu en interne</div>');
      wrap.appendChild(el);
    };
    mk('left', 'entrées du sous-flow', p.ins);
    mk('right', 'sorties du sous-flow', p.outs);
  }

  /* ── Navigation entre niveaux ── */
  function goToLevel(path) {
    storeView();
    curPath = path;
    clearSelection();
    restoreView();
    render();
    runValidation(true);
    if (G().nodes.length) fitView();
    persist();
    emit('level-changed', { depth: curPath.length });
  }
  function enterGroup(id) {
    const n = G().nodes.find(x => x.id === id && x.kind === 'group');
    if (!n) return;
    goToLevel(curPath.concat(id));
    Atelier.toast('Niveau N' + (curPath.length + 1) + ' — <b>' + esc(n.name || 'sous-flow') + '</b>. Ses ports = les contrats non résolus en interne. <b>Échap</b> pour remonter.');
  }
  function upLevel() {
    if (!curPath.length) return;
    const grpId = curPath[curPath.length - 1];
    storeView();
    curPath = curPath.slice(0, -1);
    // purge les liens du parent devenus orphelins (contrat plus exposé)
    const g = G();
    const grp = g.nodes.find(n => n.id === grpId);
    if (grp) {
      const p = groupPins(grp);
      const before = g.edges.length;
      g.edges = g.edges.filter(e =>
        !(e.to === grpId && !p.ins.includes(e.contract)) &&
        !(e.from === grpId && !p.outs.includes(e.contract)));
      if (g.edges.length !== before) Atelier.toast((before - g.edges.length) + ' lien(s) détaché(s) — le sous-flow n\u2019expose plus ce contrat.');
    }
    clearSelection(); restoreView(); render(); runValidation(true); persist();
    emit('level-changed', { depth: curPath.length });
  }

  /* ══ Palette ══ */
  let palQ = '';
  function paletteItems() {
    let list = Atelier.paletteNodes();
    if (palQ) {
      const s = palQ.toLowerCase();
      list = list.filter(n => n.ref.toLowerCase().includes(s) || n.name.toLowerCase().includes(s) ||
        (n.desc || '').toLowerCase().includes(s) || n.in.concat(n.out).some(c => c.includes(s)));
    }
    return list;
  }
  function selectedOutContracts() {
    const ids = Array.from(selection.nodes);
    if (ids.length !== 1) return null;
    const n = G().nodes.find(x => x.id === ids[0]);
    const s = n && specOf(n);
    return s && s.out.length ? { node: n, spec: s, outs: s.out } : null;
  }
  function renderPalette() {
    const listEl = $('#pal-list');
    const all = paletteItems();
    const ctx = selectedOutContracts();
    let html = '';
    if (window.BP2Team) {
      const q = palQ.toLowerCase();
      const tc = BP2Team.CARDS.filter(c => !q || c.name.toLowerCase().includes(q) || c.desc.toLowerCase().includes(q));
      if (tc.length) html += `<div class="bp-pal-sec team">Équipe · qui fait le travail</div>` + tc.map(c => `
        <div class="bp-pal-item team" data-ref="${esc(c.ref)}" title="${esc(c.desc)}">
          <div class="r1"><span class="cat" style="background:${BP2Team.COLOR}"></span><span class="ref">${esc(c.name)}</span></div>
          <span class="nm">${esc(c.desc)}</span>
        </div>`).join('');
    }
    const item = (n, hot) => `
      <div class="bp-pal-item${hot ? ' hot' : ''}${n.locked ? ' locked' : ''}" data-ref="${esc(n.ref)}" title="${esc(n.desc || '')}">
        <div class="r1"><span class="cat" style="background:${Atelier.catColor(n.cat)}"></span>
        <span class="ref">${esc(n.kind === 'ext' ? n.name : n.ref)}</span>
        ${n.kind === 'ext' ? `<span style="font-family:var(--font-mono);font-size:0.6rem;color:var(--ink-muted);margin-left:auto">ext · ${esc(n.ext)}</span>` : ''}</div>
        <span class="nm">${esc(n.kind === 'ext' ? n.desc.split('—')[0] : n.name)}</span>
        ${n.locked ? `<span class="lock-cta" data-install-ext="${esc(n.ext)}">requiert l'extension → installer · 1 clic</span>` : ''}
      </div>`;
    if (ctx) {
      const teamCompat = window.BP2Team ? BP2Team.CARDS.filter(c => c.in.some(cc => ctx.outs.includes(cc))) : [];
      const compat = all.filter(n => !n.locked && n.in.some(c => ctx.outs.includes(c)) && n.ref !== ctx.spec.ref);
      if (compat.length || teamCompat.length) {
        html += `<div class="bp-pal-sec hot">Compatibles · sortie ${esc(ctx.outs.join(', '))}</div>`;
        html += teamCompat.map(c => `
        <div class="bp-pal-item team hot" data-ref="${esc(c.ref)}" title="${esc(c.desc)}">
          <div class="r1"><span class="cat" style="background:${BP2Team.COLOR}"></span><span class="ref">${esc(c.name)}</span></div>
          <span class="nm">${esc(c.desc)}</span>
        </div>`).join('');
        html += compat.map(n => item(n, true)).join('');
      }
    }
    const groups = {};
    all.forEach(n => { (groups[n.cat] = groups[n.cat] || []).push(n); });
    CAT.categories.forEach(c => {
      if (!groups[c.id]) return;
      html += `<div class="bp-pal-sec">${esc(c.id)} · ${esc(c.name)}</div>` + groups[c.id].map(n => item(n, false)).join('');
    });
    if (groups.EXT) html += `<div class="bp-pal-sec">Extensions</div>` + groups.EXT.map(n => item(n, false)).join('');
    listEl.innerHTML = html || '<p class="at-sub" style="padding:12px 2px">aucun résultat.</p>';

    $$('.bp-pal-item', listEl).forEach(el => {
      el.addEventListener('click', ev => {
        if (ev.target.dataset.installExt) return;
        const ref = el.dataset.ref;
        const n = (window.BP2Team && BP2Team.isTeamRef(ref)) ? BP2Team.cardSpec(ref) : Atelier.nodeSpec(ref);
        if (!n || n.locked) return;
        const r = wrap.getBoundingClientRect();
        const c = toWorld(r.left + r.width / 2, r.top + r.height / 2);
        addNode(n.ref, c.x - 90 + (Math.random() * 60 - 30), c.y - 40 + (Math.random() * 40 - 20));
      });
    });
    $$('[data-install-ext]', listEl).forEach(el => el.addEventListener('click', ev => {
      ev.stopPropagation();
      installExtInline(el.dataset.installExt);
    }));
  }
  $('#pal-search').addEventListener('input', e => { palQ = e.target.value.trim(); renderPalette(); });

  /* ── Installation inline ── */
  function installExtInline(extId, after) {
    const e = Atelier.extById[extId];
    if (!e) return;
    const steps = ['manifeste validé ✓', 'artefacts copiés ✓', 'hooks en shadow ✓', 'installée · votre toile n\u2019a pas bougé'];
    let i = 0;
    (function tick() {
      Atelier.toast('Installation de <b>' + esc(e.name) + '</b> — ' + steps.slice(0, i + 1).join(' · '), { good: i === steps.length - 1, ms: 1600 });
      i++;
      if (i < steps.length) setTimeout(tick, 520);
      else { Atelier.installExt(extId); render(); if (after) after(); emit('ext-installed', extId); }
    })();
  }

  /* ══ Mutations ══ */
  function addNode(ref, x, y, opts) {
    pushHistory();
    const g = G();
    let px = Math.round(x), py = Math.round(y), guard = 0;
    while (guard++ < 24 && g.nodes.some(n => Math.abs(n.x - px) < 90 && Math.abs(n.y - py) < 60)) { px += 46; py += 42; }
    let n;
    if (ref === '__group__') n = { id: uid(), kind: 'group', name: 'sous-flow', x: px, y: py, sub: { nodes: [], edges: [], comments: [] } };
    else if (window.BP2Team && BP2Team.isTeamRef(ref)) n = { id: uid(), x: px, y: py, ...BP2Team.newNode(ref) };
    else n = { id: uid(), ref, x: px, y: py };
    g.nodes.push(n);
    clearSelection(); selection.nodes.add(n.id);
    markDirty(); render();
    emit('node-added', { ref, id: n.id });
    if (!(opts && opts.silent)) setTab('node');
    return n;
  }
  function addEdge(fromNode, toNode, contract) {
    const g = G();
    if (fromNode === toNode) return null;
    if (g.edges.some(e => e.from === fromNode && e.to === toNode && e.contract === contract)) return null;
    pushHistory();
    const e = { id: uid(), from: fromNode, to: toNode, contract };
    g.edges.push(e);
    markDirty(); render();
    Atelier.toast('Connecté via contrat <b>' + esc(contract) + '</b> ✓ — un contrat = la forme des données que le lien transporte');
    emit('edge-added', e);
    return e;
  }
  function deleteSelection() {
    if (!selection.nodes.size && !selection.edge && !selection.comment) return;
    pushHistory();
    const g = G();
    if (selection.nodes.size) {
      g.edges = g.edges.filter(e => !selection.nodes.has(e.from) && !selection.nodes.has(e.to));
      g.nodes = g.nodes.filter(n => !selection.nodes.has(n.id));
    }
    if (selection.edge) g.edges = g.edges.filter(e => e.id !== selection.edge);
    if (selection.comment) g.comments = g.comments.filter(c => c.id !== selection.comment);
    clearSelection(); markDirty(); render();
  }
  function deepClone(n) {
    const c = JSON.parse(JSON.stringify(n));
    (function reId(node) {
      node.id = uid();
      if (node.sub) {
        const map = {};
        node.sub.nodes.forEach(sn => { const old = sn.id; reId(sn); map[old] = sn.id; });
        node.sub.edges.forEach(e => { e.id = uid(); e.from = map[e.from] || e.from; e.to = map[e.to] || e.to; });
        (node.sub.comments || []).forEach(cm => cm.id = uid());
      }
    })(c);
    return c;
  }
  function duplicateSelection() {
    const ids = Array.from(selection.nodes);
    if (!ids.length) return;
    pushHistory();
    const g = G();
    clearSelection();
    ids.forEach(id => {
      const n = g.nodes.find(x => x.id === id);
      if (!n) return;
      const c = deepClone(n);
      c.x = n.x + 40; c.y = n.y + 40;
      g.nodes.push(c); selection.nodes.add(c.id);
    });
    markDirty(); render();
  }
  function addComment() {
    pushHistory();
    const g = G();
    let x = 200, y = 160, w = 380, h = 240;
    const ids = Array.from(selection.nodes);
    if (ids.length) {
      const ns = g.nodes.filter(n => ids.includes(n.id));
      x = Math.min(...ns.map(n => n.x)) - 30;
      y = Math.min(...ns.map(n => n.y)) - 44;
      w = Math.max(...ns.map(n => n.x + (n._w || 190))) - x + 30;
      h = Math.max(...ns.map(n => n.y + (n._h || 80))) - y + 30;
    } else {
      const r = wrap.getBoundingClientRect();
      const c = toWorld(r.left + r.width / 2, r.top + r.height / 2);
      x = c.x - w / 2; y = c.y - h / 2;
    }
    g.comments.push({ id: uid(), x, y, w, h, label: 'zone de commentaire' });
    markDirty(); render();
  }
  function clearSelection() {
    selection.nodes.clear(); selection.edge = null; selection.comment = null;
  }
  function pruneEdgesFor(n) {
    const s = specOf(n);
    if (!s) return;
    const g = G();
    g.edges = g.edges.filter(e => !(e.from === n.id && !s.out.includes(e.contract)) && !(e.to === n.id && !s.in.includes(e.contract)));
  }

  /* ── Grouper / dégrouper (C4) ── */
  function groupSelection() {
    const ids = Array.from(selection.nodes);
    if (ids.length < 2) { Atelier.toast('Sélectionnez au moins <b>2 nodes</b> (⇧ clic), puis <b>⌘G</b> pour les regrouper en sous-flow.'); return; }
    pushHistory();
    const g = G();
    const inside = g.nodes.filter(n => ids.includes(n.id));
    const gx = Math.min(...inside.map(n => n.x)), gy = Math.min(...inside.map(n => n.y));
    const grp = { id: uid(), kind: 'group', name: 'sous-flow', x: gx + 40, y: gy + 30,
      sub: { nodes: inside, edges: [], comments: [] } };
    grp.sub.edges = g.edges.filter(e => ids.includes(e.from) && ids.includes(e.to));
    // liens frontière → re-câblés sur le groupe (même contrat)
    const boundary = g.edges.filter(e => ids.includes(e.from) !== ids.includes(e.to));
    g.edges = g.edges.filter(e => !ids.includes(e.from) && !ids.includes(e.to));
    g.nodes = g.nodes.filter(n => !ids.includes(n.id));
    g.nodes.push(grp);
    const seen = new Set();
    boundary.forEach(e => {
      const e2 = { id: uid(), from: ids.includes(e.from) ? grp.id : e.from, to: ids.includes(e.to) ? grp.id : e.to, contract: e.contract };
      const k = e2.from + '>' + e2.to + '>' + e2.contract;
      if (!seen.has(k)) { seen.add(k); g.edges.push(e2); }
    });
    clearSelection(); selection.nodes.add(grp.id);
    markDirty(); render();
    Atelier.toast('<b>' + inside.length + ' nodes</b> regroupés en sous-flow ◇ — double-clic pour entrer (vue C4), ⌘⇧G pour dégrouper.', { good: true, ms: 4200 });
    emit('grouped', { id: grp.id, count: inside.length });
  }
  function ungroupNode(id) {
    const g = G();
    const grp = g.nodes.find(n => n.id === id && n.kind === 'group');
    if (!grp) return;
    pushHistory();
    const dx = grp.x - Math.min(...(grp.sub.nodes.length ? grp.sub.nodes.map(n => n.x) : [grp.x]));
    const dy = grp.y - Math.min(...(grp.sub.nodes.length ? grp.sub.nodes.map(n => n.y) : [grp.y]));
    grp.sub.nodes.forEach(n => { n.x += dx; n.y += dy; });
    const boundary = g.edges.filter(e => e.from === id || e.to === id);
    g.edges = g.edges.filter(e => e.from !== id && e.to !== id);
    g.nodes = g.nodes.filter(n => n.id !== id);
    g.nodes.push(...grp.sub.nodes);
    g.edges.push(...grp.sub.edges);
    let dropped = 0;
    boundary.forEach(e => {
      if (e.to === id) {
        const t = grp.sub.nodes.find(n => { const s = specOf(n); return s && s.in.includes(e.contract) && !grp.sub.edges.some(x => x.to === n.id && x.contract === e.contract); });
        if (t) g.edges.push({ id: uid(), from: e.from, to: t.id, contract: e.contract }); else dropped++;
      } else {
        const f = grp.sub.nodes.find(n => { const s = specOf(n); return s && s.out.includes(e.contract) && !grp.sub.edges.some(x => x.from === n.id && x.contract === e.contract); });
        if (f) g.edges.push({ id: uid(), from: f.id, to: e.to, contract: e.contract }); else dropped++;
      }
    });
    clearSelection();
    grp.sub.nodes.forEach(n => selection.nodes.add(n.id));
    markDirty(); render();
    Atelier.toast('Sous-flow dégroupé' + (dropped ? ' — ' + dropped + ' lien(s) non re-câblé(s)' : ' ✓'));
  }

  /* ══ Menu contextuel ══ */
  let menuEl = null, pendingWire = null;
  function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } pendingWire = null; }

  function openAddMenu(screenX, screenY, ctx) {
    closeMenu();
    pendingWire = ctx && ctx.wire ? ctx.wire : null;
    menuEl = document.createElement('div');
    menuEl.className = 'bp-menu';
    const mh = 380;
    menuEl.style.left = Math.min(screenX, innerWidth - 292) + 'px';
    menuEl.style.top = Math.min(screenY, innerHeight - mh - 12) + 'px';
    let contextOnly = !!pendingWire;
    const worldPos = ctx.world;

    function list_() {
      let list = (window.BP2Team ? BP2Team.CARDS.slice() : []).concat(Atelier.paletteNodes());
      const q = (menuEl.querySelector('input[type="text"]') || {}).value || '';
      if (q) {
        const s = q.toLowerCase();
        list = list.filter(n => n.ref.toLowerCase().includes(s) || n.name.toLowerCase().includes(s));
      }
      if (pendingWire && contextOnly) {
        list = list.filter(n => pendingWire.dir === 'out'
          ? n.in.includes(pendingWire.contract)
          : n.out.includes(pendingWire.contract));
      }
      return list.slice(0, 40);
    }

    function draw() {
      const list = list_();
      const grpItem = pendingWire ? '' : `<div class="m-item" data-ref="__group__">
        <span class="cat" style="background:#A78BFA"></span>
        <span class="ref">◇ sous-flow</span><span class="nm">conteneur vide — construire dedans (C4)</span></div>
      <div class="m-item" data-ref="__zone__">
        <span class="cat" style="background:#5B6068"></span>
        <span class="ref">▭ zone</span><span class="nm">cadre coloré pour regrouper visuellement</span></div>`;
      menuEl.innerHTML = `
        <div class="m-head">
          <input type="text" class="at-search" placeholder="rechercher un node…" />
          ${pendingWire ? `<label class="m-ctx"><input type="checkbox" ${contextOnly ? 'checked' : ''} id="m-ctx-cb" />
            compatibles seulement · ${pendingWire.dir === 'out' ? 'sortie' : 'entrée'} <b style="color:var(--ink)">${esc(pendingWire.contract)}</b></label>` : ''}
        </div>
        <div class="m-list">
          ${grpItem}
          ${list.map((n, i) => `<div class="m-item${n.locked ? ' locked' : ''}${i === 0 && pendingWire ? ' focus' : ''}" data-ref="${esc(n.ref)}">
            <span class="cat" style="background:${n.kind === 'team' ? BP2Team.COLOR : Atelier.catColor(n.cat)}"></span>
            <span class="ref">${esc(n.kind === 'ext' || n.kind === 'team' ? n.name : n.ref)}</span>
            <span class="nm">${esc(n.kind === 'ext' ? '' : (n.kind === 'team' ? n.desc : n.name))}</span>
            ${pendingWire && !n.locked ? '<span class="star">✦</span>' : ''}
          </div>`).join('') || '<p class="at-sub" style="padding:10px">aucun node compatible — décochez le filtre.</p>'}
        </div>
        <div class="m-foot">${CAT.total_patterns} patterns au total${pendingWire ? ' — décochez pour tout voir' : ''} · Échap pour fermer</div>`;
      const inp = menuEl.querySelector('input[type="text"]');
      inp.addEventListener('input', draw);
      setTimeout(() => inp.focus(), 30);
      const cb = menuEl.querySelector('#m-ctx-cb');
      if (cb) cb.addEventListener('change', () => { contextOnly = cb.checked; draw(); });
      $$('.m-item', menuEl).forEach(el => el.addEventListener('click', () => pick(el.dataset.ref)));
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { const f = menuEl.querySelector('.m-item'); if (f) pick(f.dataset.ref); }
        if (e.key === 'Escape') closeMenu();
      });
    }

    function pick(ref) {
      if (ref === '__group__') { closeMenu(); addNode('__group__', worldPos.x - 20, worldPos.y - 20); return; }
      if (ref === '__zone__') {
        closeMenu();
        pushHistory();
        G().comments.push({ id: uid(), x: worldPos.x - 190, y: worldPos.y - 120, w: 380, h: 240, label: 'zone', color: '#A78BFA' });
        markDirty(); render();
        return;
      }
      const s = (window.BP2Team && BP2Team.isTeamRef(ref)) ? BP2Team.cardSpec(ref) : Atelier.nodeSpec(ref);
      if (!s) return;
      const doAdd = () => {
        const wire = pendingWire;
        const n = addNode(ref, worldPos.x - 20, worldPos.y - 20);
        if (wire) {
          if (wire.dir === 'out') addEdge(wire.node, n.id, wire.contract);
          else addEdge(n.id, wire.node, wire.contract);
        }
      };
      if (s.locked) { const keep = pendingWire; closeMenu(); pendingWire = keep; installExtInline(s.ext, doAdd); }
      else { const keep = pendingWire; closeMenu(); pendingWire = keep; doAdd(); pendingWire = null; }
    }

    document.body.appendChild(menuEl);
    draw();
  }
  document.addEventListener('pointerdown', e => {
    if (menuEl && !menuEl.contains(e.target)) closeMenu();
  }, true);

  /* ══ Interactions canvas ══ */
  let drag = null;

  wrap.addEventListener('contextmenu', e => {
    e.preventDefault();
    openAddMenu(e.clientX, e.clientY, { world: toWorld(e.clientX, e.clientY) });
  });

  wrap.addEventListener('pointerdown', e => {
    if (e.button === 2) return;
    const ghost = e.target.closest('.bp-ghost');
    const pin = e.target.closest('.bp-pin');
    const nodeEl = e.target.closest('.bp-node');
    const cmHead = e.target.closest('[data-cmove]');
    const cmEl = e.target.closest('.bp-comment');
    const warn = e.target.closest('[data-warn]');
    const docB = e.target.closest('[data-docs]');
    closeMenu();

    if (ghost) {
      const from = ghost.dataset.gfrom, ref = ghost.dataset.gref, contract = ghost.dataset.gcontract;
      const r = ghost.getBoundingClientRect();
      const w = toWorld(r.left, r.top);
      const spec = Atelier.nodeSpec(ref);
      const doAdd = () => {
        const n = addNode(ref, w.x, w.y, { silent: true });
        addEdge(from, n.id, contract);
      };
      if (spec && spec.locked) installExtInline(spec.ext, doAdd); else doAdd();
      e.preventDefault();
      return;
    }
    if (warn) { setTab('validation'); return; }
    if (docB) {
      const n = G().nodes.find(x => x.id === docB.dataset.docs);
      if (n) { clearSelection(); selection.nodes.add(n.id); render(); openFiche(n); }
      return;
    }

    if (pin) {
      const dir = pin.dataset.dir, contract = pin.dataset.contract, nodeId = pin.dataset.node;
      drag = { type: 'wire', dir, contract, node: nodeId };
      $$('.bp-pin').forEach(p => {
        const ok = p.dataset.dir !== dir && p.dataset.contract === contract && p.dataset.node !== nodeId;
        p.classList.toggle('compat', ok);
        p.classList.toggle('dim', !ok && p !== pin);
      });
      e.preventDefault();
      return;
    }

    if (nodeEl && !e.target.matches('input')) {
      const id = nodeEl.dataset.id;
      if (!e.shiftKey && !selection.nodes.has(id)) clearSelection();
      selection.nodes.add(id); selection.edge = null; selection.comment = null;
      const w = toWorld(e.clientX, e.clientY);
      const grabbed = G().nodes.filter(n => selection.nodes.has(n.id))
        .map(n => ({ n, ox: w.x - n.x, oy: w.y - n.y }));
      drag = { type: 'node', grabbed, moved: false };
      render();
      return;
    }

    if (cmHead) {
      const g = G();
      const c = g.comments.find(x => x.id === cmHead.dataset.cmove);
      const w = toWorld(e.clientX, e.clientY);
      const inside = g.nodes.filter(n => {
        const cx = n.x + (n._w || 190) / 2, cy = n.y + (n._h || 80) / 2;
        return cx > c.x && cx < c.x + c.w && cy > c.y && cy < c.y + c.h;
      }).map(n => ({ n, ox: w.x - n.x, oy: w.y - n.y }));
      selection.comment = c.id; selection.nodes.clear(); selection.edge = null;
      drag = { type: 'comment', c, ox: w.x - c.x, oy: w.y - c.y, inside, moved: false };
      return;
    }
    if (cmEl && !e.target.matches('input')) {
      selection.comment = cmEl.dataset.cid; selection.nodes.clear(); selection.edge = null;
      render();
      return;
    }

    const hit = e.target.closest('.edge-hit');
    if (hit) {
      clearSelection();
      selection.edge = hit.dataset.eid;
      render(); setTab('node');
      return;
    }

    drag = { type: 'pan', sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, moved: false };
  });

  document.addEventListener('pointermove', e => {
    if (!drag) return;
    if (drag.type === 'pan') {
      const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.x = drag.vx + dx; view.y = drag.vy + dy;
      applyView();
    } else if (drag.type === 'node') {
      const w = toWorld(e.clientX, e.clientY);
      drag.grabbed.forEach(g => { g.n.x = Math.round(w.x - g.ox); g.n.y = Math.round(w.y - g.oy); });
      drag.moved = true;
      renderEdgesLight();
      drag.grabbed.forEach(g => {
        const el = items.querySelector(`.bp-node[data-id="${g.n.id}"]`);
        if (el) { el.style.left = g.n.x + 'px'; el.style.top = g.n.y + 'px'; }
      });
    } else if (drag.type === 'comment') {
      const w = toWorld(e.clientX, e.clientY);
      drag.c.x = Math.round(w.x - drag.ox); drag.c.y = Math.round(w.y - drag.oy);
      drag.inside.forEach(g => { g.n.x = Math.round(w.x - g.ox); g.n.y = Math.round(w.y - g.oy); });
      drag.moved = true;
      const el = items.querySelector(`.bp-comment[data-cid="${drag.c.id}"]`);
      if (el) { el.style.left = drag.c.x + 'px'; el.style.top = drag.c.y + 'px'; }
      drag.inside.forEach(g => {
        const nel = items.querySelector(`.bp-node[data-id="${g.n.id}"]`);
        if (nel) { nel.style.left = g.n.x + 'px'; nel.style.top = g.n.y + 'px'; }
      });
      renderEdgesLight();
    } else if (drag.type === 'wire') {
      const ghost = $('#ghost-wire');
      const a = pinPos(drag.node, drag.dir, drag.contract);
      const b = toWorld(e.clientX, e.clientY);
      const dx = Math.min(160, Math.max(40, Math.abs(b.x - a.x) * 0.5));
      ghost.style.display = '';
      ghost.setAttribute('d', drag.dir === 'out'
        ? `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
        : `M ${b.x} ${b.y} C ${b.x + dx} ${b.y}, ${a.x - dx} ${a.y}, ${a.x} ${a.y}`);
    }
  });

  document.addEventListener('pointerup', e => {
    if (!drag) return;
    const d = drag; drag = null;
    if (d.type === 'wire') {
      $('#ghost-wire').style.display = 'none';
      $$('.bp-pin').forEach(p => p.classList.remove('compat', 'dim'));
      const pin = document.elementFromPoint(e.clientX, e.clientY);
      const target = pin && pin.closest ? pin.closest('.bp-pin') : null;
      if (target && target.dataset.dir !== d.dir && target.dataset.contract === d.contract && target.dataset.node !== d.node) {
        if (d.dir === 'out') addEdge(d.node, target.dataset.node, d.contract);
        else addEdge(target.dataset.node, d.node, d.contract);
      } else if (!target || !target.closest('.bp-node')) {
        const overCanvas = wrap.contains(document.elementFromPoint(e.clientX, e.clientY));
        if (overCanvas) {
          openAddMenu(e.clientX, e.clientY, {
            world: toWorld(e.clientX, e.clientY),
            wire: { dir: d.dir, contract: d.contract, node: d.node }
          });
        }
      }
    } else if (d.type === 'pan' && !d.moved) {
      clearSelection(); render();
    } else if ((d.type === 'node' || d.type === 'comment') && d.moved) {
      markDirty(); render();
    } else if (d.type === 'node' && !d.moved) {
      render();
    }
    if (d.type === 'pan') persist();
  });

  function renderEdgesLight() {
    const g = G();
    g.edges.forEach(e2 => {
      const d = edgePath(e2);
      const p1 = svg.querySelector(`path.edge[data-e="${e2.id}"]`);
      const p2 = svg.querySelector(`path.edge-hit[data-eid="${e2.id}"]`);
      if (p1) p1.setAttribute('d', d);
      if (p2) p2.setAttribute('d', d);
      const lbl = $$('.edge-label', world)[g.edges.indexOf(e2)];
      if (p1 && lbl) {
        try { const m = p1.getPointAtLength(p1.getTotalLength() / 2); lbl.style.left = m.x + 'px'; lbl.style.top = m.y + 'px'; } catch (err) {}
      }
    });
  }

  wrap.addEventListener('wheel', e => {
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0012));
  }, { passive: false });

  wrap.addEventListener('dblclick', e => {
    const nodeEl = e.target.closest('.bp-node');
    if (!nodeEl) return;
    const n = G().nodes.find(x => x.id === nodeEl.dataset.id);
    if (!n) return;
    if (n.kind === 'group') enterGroup(n.id);
    else openFiche(n);
  });

  items.addEventListener('input', e => {
    if (e.target.dataset.clabel) {
      const c = G().comments.find(x => x.id === e.target.dataset.clabel);
      if (c) { c.label = e.target.value; persist(); }
    }
  });

  /* ══ Clavier ══ */
  document.addEventListener('keydown', e => {
    if (window.BP2Docs && BP2Docs.isOpen()) return; // l'éditeur de doc gère son clavier
    if (e.target.matches('input, textarea, select')) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === 'Escape') {
      if (menuEl) { closeMenu(); return; }
      if ($('#bp-fiche').classList.contains('open')) { closeFiche(); return; }
      if (selection.nodes.size || selection.edge || selection.comment) { clearSelection(); render(); return; }
      if (curPath.length) { upLevel(); return; }
    }
    else if ((e.key === 'Delete' || e.key === 'Backspace')) { e.preventDefault(); deleteSelection(); }
    else if (mod && e.shiftKey && e.key.toLowerCase() === 'g') {
      e.preventDefault();
      const ids = Array.from(selection.nodes);
      const grp = ids.length === 1 && G().nodes.find(n => n.id === ids[0] && n.kind === 'group');
      if (grp) ungroupNode(grp.id);
    }
    else if (mod && e.key.toLowerCase() === 'g') { e.preventDefault(); groupSelection(); }
    else if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
    else if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicateSelection(); }
    else if (e.key.toLowerCase() === 'c' && !mod) { addComment(); }
    else if (e.key.toLowerCase() === 'f' && !mod) { fitView(); }
  });

  /* ══ HUD ══ */
  $('#hud-in').addEventListener('click', () => { const r = wrap.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1.2); });
  $('#hud-out').addEventListener('click', () => { const r = wrap.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1 / 1.2); });
  $('#hud-fit').addEventListener('click', fitView);
  $('#hud-tidy').addEventListener('click', tidy);

  /* ── Auto-layout ── */
  function tidy() {
    const g = G();
    if (!g.nodes.length) return;
    const order = topoOrder();
    if (!order) { Atelier.toast('Cycle détecté — impossible de ranger un graphe qui boucle.'); return; }
    pushHistory();
    const rank = {};
    order.forEach(id => {
      const incoming = g.edges.filter(e => e.to === id);
      rank[id] = incoming.length ? Math.max(...incoming.map(e => (rank[e.from] || 0))) + 1 : 0;
    });
    const byRank = {};
    g.nodes.forEach(n => { (byRank[rank[n.id] || 0] = byRank[rank[n.id] || 0] || []).push(n); });
    Object.keys(byRank).forEach(r => {
      byRank[r].sort((a, b) => a.y - b.y);
      let y = 150;
      byRank[r].forEach(n => { n.x = 130 + r * 320; n.y = y; y += (n._h || 90) + 60; });
    });
    markDirty(); render(); fitView();
    Atelier.toast('Graphe rangé par rangs topologiques — <b>⌘Z</b> pour revenir.');
  }

  /* ══ Inspecteur ══ */
  function setTab(name) {
    $$('.bp-tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    $$('.bp-panel').forEach(p => p.classList.toggle('on', p.dataset.panel === name));
    if (name === 'cout' && window.BP2Cost) BP2Cost.renderTab();
  }
  $$('.bp-tab').forEach(t => t.addEventListener('click', () => setTab(t.dataset.tab)));

  /* ── Contexte (ingénierie de contexte, C1) : politique par node ── */
  const CTX_TIERS = ['tiny', 'small', 'medium', 'deep'];
  const CTX_STRATEGIES = ['digest', 'selective', 'index-guided', 'full'];
  const CTX_ISOLATIONS = ['shared', 'isolated'];
  function ctxOf(n) { return (n && n.config && n.config.context) || {}; }
  function findNodeDeep(id, g) {
    for (const n of (g || state).nodes || []) {
      if (n.id === id) return n;
      if (n.kind === 'group' && n.sub) { const f = findNodeDeep(id, n.sub); if (f) return f; }
    }
    return null;
  }
  function contextSectionHtml(n) {
    const ctx = ctxOf(n);
    const budget = ctx.budget || {};
    const comp = ctx.compaction || {};
    const tier = budget.tier || 'medium';
    const sel = (id, opts, cur) => `<select class="ctx-sel" id="${id}">${opts.map(o => `<option value="${o}"${o === cur ? ' selected' : ''}>${o}</option>`).join('')}</select>`;
    return `
      <div class="bp-prop"><div class="k">Contexte — politique de fenêtre</div>
        <div class="ctx-grid">
          <label class="ctx-field"><span>budget (tier)</span>${sel('ctx-tier', CTX_TIERS, tier)}</label>
          <label class="ctx-field"><span>plafond (tokens)</span><input class="grp-name-input" type="number" id="ctx-max" min="1" step="1000" value="${budget.maxTokens || ''}" placeholder="aucun" /></label>
          <label class="ctx-field"><span>justification</span><input class="grp-name-input" type="text" id="ctx-just" value="${esc(budget.justification || '')}" placeholder="${tier === 'deep' ? 'requise pour le tier deep' : 'optionnelle'}" spellcheck="false" /></label>
          <label class="ctx-field"><span>compaction de l'amont</span>${sel('ctx-strategy', CTX_STRATEGIES, comp.strategy || 'full')}</label>
          <label class="ctx-field"><span>isolation</span>${sel('ctx-iso', CTX_ISOLATIONS, ctx.isolation || 'shared')}</label>
        </div>
        <div class="v soft" style="margin-top:6px">budget reçu, compression de l'amont, fenêtre partagée ou quarantaine — compilé en directives dans le mission pack. Un node isolé ne sort que par digest (handoff-packet ou context-pack).</div>
      </div>`;
  }
  function bindContextSection(panel, n) {
    const write = () => {
      const tier = $('#ctx-tier', panel).value;
      const maxTokens = parseInt($('#ctx-max', panel).value, 10);
      const justification = $('#ctx-just', panel).value.trim();
      const strategy = $('#ctx-strategy', panel).value;
      const isolation = $('#ctx-iso', panel).value;
      const ctx = {};
      const budget = {};
      if (tier !== 'medium') budget.tier = tier;
      if (!isNaN(maxTokens) && maxTokens > 0) budget.maxTokens = maxTokens;
      if (justification) budget.justification = justification;
      if (Object.keys(budget).length) ctx.budget = budget;
      if (strategy !== 'full') ctx.compaction = { strategy };
      if (isolation !== 'shared') ctx.isolation = isolation;
      if (Object.keys(ctx).length) {
        n.config = n.config || {};
        n.config.context = ctx;
      } else if (n.config) {
        delete n.config.context;
        if (!Object.keys(n.config).length) delete n.config;
      }
      markDirty(); render();
    };
    ['ctx-tier', 'ctx-max', 'ctx-just', 'ctx-strategy', 'ctx-iso'].forEach(id => {
      const el = $('#' + id, panel);
      if (el) el.addEventListener('change', write);
    });
  }

  function renderInspector() {
    const panel = $('#panel-node');
    const g = G();
    const ids = Array.from(selection.nodes);
    if (selection.edge) {
      const e = g.edges.find(x => x.id === selection.edge);
      if (!e) { panel.innerHTML = ''; return; }
      const c = Atelier.contractById[e.contract];
      const src = g.nodes.find(x => x.id === e.from);
      const flowK = window.BP2Cost && src ? BP2Cost.flowK(src, specOf) : 0;
      panel.innerHTML = `
        <div class="bp-prop"><div class="k">Lien — contrat</div>
          <div class="v" style="font-family:var(--font-mono);display:flex;align-items:center;gap:8px">
            <span style="width:9px;height:9px;border-radius:50%;background:${Atelier.contractColor(e.contract)}"></span>${esc(e.contract)}</div></div>
        <div class="bp-prop"><div class="k">Ce que ce contrat transporte</div><div class="v soft">${esc(c ? c.desc : '')}</div></div>
        ${flowK ? `<div class="bp-prop"><div class="k">Volume estimé</div><div class="np-cost"><span>tokens émis par run</span><b>~${BP2Cost.fmtK(flowK)} tok</b></div></div>` : ''}
        <button class="at-btn sm" id="del-edge">SUPPRIMER LE LIEN</button>`;
      $('#del-edge').addEventListener('click', deleteSelection);
      return;
    }
    if (selection.comment) {
      const c = g.comments.find(x => x.id === selection.comment);
      if (!c) { panel.innerHTML = ''; return; }
      const SW = ['#A78BFA', '#6EE7FF', '#34D399', '#FCD34D', '#F87171', '#FDBA74'];
      panel.innerHTML = `
        <div class="bp-prop"><div class="k">Zone — libellé</div>
          <input class="grp-name-input" id="zn-label" value="${esc(c.label)}" spellcheck="false" /></div>
        <div class="bp-prop"><div class="k">Couleur</div>
          <div class="zone-sws">
            <button class="zone-sw none${!c.color ? ' on' : ''}" data-zc="" title="neutre">✕</button>
            ${SW.map(col => `<button class="zone-sw${c.color === col ? ' on' : ''}" data-zc="${col}" style="background:${col}"></button>`).join('')}
          </div></div>
        <div class="bp-prop"><div class="k">La zone n'est qu'un cadre</div><div class="v soft">elle se déplace avec ses nodes et colore la lecture — elle peut devenir un vrai sous-flow ◇.</div></div>
        <button class="at-btn sm acc" id="zn-group">◇ CONVERTIR EN SOUS-FLOW</button>
        <button class="at-btn sm ghost" id="del-zone">SUPPRIMER LA ZONE</button>`;
      const zl = $('#zn-label');
      zl.addEventListener('input', () => {
        c.label = zl.value; persist();
        const el = items.querySelector(`[data-clabel="${c.id}"]`);
        if (el) el.value = zl.value;
      });
      $$('.zone-sw', panel).forEach(b => b.addEventListener('click', () => { c.color = b.dataset.zc || null; markDirty(); render(); }));
      $('#zn-group').addEventListener('click', () => {
        const inside = g.nodes.filter(n2 => { const cx = n2.x + (n2._w || 190) / 2, cy = n2.y + (n2._h || 80) / 2; return cx > c.x && cx < c.x + c.w && cy > c.y && cy < c.y + c.h; });
        if (inside.length < 2) { Atelier.toast('Il faut au moins <b>2 nodes</b> dans la zone pour en faire un sous-flow.'); return; }
        clearSelection();
        inside.forEach(n2 => selection.nodes.add(n2.id));
        groupSelection();
      });
      $('#del-zone').addEventListener('click', deleteSelection);
      return;
    }
    if (ids.length !== 1) {
      if (ids.length > 1) {
        panel.innerHTML = `<p class="empty">${ids.length} nodes sélectionnés.</p>
          <button class="at-btn sm acc" id="do-group">◇ REGROUPER EN SOUS-FLOW <span class="at-kbd" style="margin-left:6px">⌘G</span></button>
          <p class="empty" style="margin-top:10px">⌘D duplique · suppr efface · C encadre d'un commentaire.</p>`;
        $('#do-group').addEventListener('click', groupSelection);
      } else {
        panel.innerHTML = `<p class="empty">sélectionnez un node, un lien ou une zone.<br><br>clic droit : ajouter un node<br>fil lâché dans le vide : nodes compatibles<br>⌘G : regrouper en sous-flow</p>`;
      }
      return;
    }
    const n = g.nodes.find(x => x.id === ids[0]);
    const s = specOf(n);
    if (!s) { panel.innerHTML = ''; return; }
    if (n.kind === 'agent' && window.BP2Team) {
      const agCost = window.BP2Cost ? BP2Cost.nodeRows(n, specOf) : '';
      panel.innerHTML = BP2Team.inspector(n) + agCost + contextSectionHtml(n) + `
      <button class="at-btn sm acc" id="ag-config">CONFIGURER L'AGENT →</button>
      <button class="at-btn sm ghost" id="del-node">SUPPRIMER</button>`;
      bindContextSection(panel, n);
      $('#ag-config').addEventListener('click', () => openFiche(n));
      $('#del-node').addEventListener('click', deleteSelection);
      return;
    }
    if (n.kind === 'trigger' && window.BP2Team) {
      panel.innerHTML = BP2Team.triggerInspector(n) + `
      <button class="at-btn sm acc" id="tr-config">CONFIGURER LE DÉPART →</button>
      <button class="at-btn sm ghost" id="del-node">SUPPRIMER</button>`;
      $('#tr-config').addEventListener('click', () => openFiche(n));
      $('#del-node').addEventListener('click', deleteSelection);
      return;
    }
    const pinRow = (dir, c2) => {
      const connected = g.edges.some(e2 => dir === 'in' ? (e2.to === n.id && e2.contract === c2) : (e2.from === n.id && e2.contract === c2));
      return `<div class="at-row sb" style="padding:4px 0">
        <span style="font-family:var(--font-mono);font-size:0.73rem;color:var(--ink-soft);display:flex;align-items:center;gap:7px">
          <span style="width:8px;height:8px;border-radius:50%;background:${Atelier.contractColor(c2)}"></span>${dir} · ${esc(c2)}</span>
        <span style="font-family:var(--font-mono);font-size:0.67rem;color:${connected ? 'var(--data-green)' : 'var(--ink-muted)'}">${connected ? 'connecté ✓' : 'libre'}</span>
      </div>`;
    };
    const costRows = window.BP2Cost ? BP2Cost.nodeRows(n, specOf) : '';

    if (n.kind === 'group') {
      const c = countInside(n);
      panel.innerHTML = `
        <div class="bp-prop"><div class="k">Sous-flow ◇ — nom</div>
          <input class="grp-name-input" id="grp-name" value="${esc(n.name || 'sous-flow')}" spellcheck="false" /></div>
        <div class="bp-prop"><div class="k">Contenu</div><div class="v soft">${c.n} node${c.n > 1 ? 's' : ''} · ${c.docs} document${c.docs > 1 ? 's' : ''} — niveau N${curPath.length + 2} (vue C4)</div></div>
        <div class="bp-prop"><div class="k">Ports — contrats exposés</div>
          ${s.in.map(c2 => pinRow('in', c2)).join('')}${s.out.map(c2 => pinRow('out', c2)).join('')}
          ${!s.in.length && !s.out.length ? '<p class="empty" style="padding:4px 0">aucun — tout est résolu en interne, ou le sous-flow est vide.</p>' : ''}</div>
        ${costRows}
        <button class="at-btn sm acc" id="grp-enter">ENTRER DANS LE SOUS-FLOW →</button>
        <button class="at-btn sm" id="grp-ungroup">DÉGROUPER (⌘⇧G)</button>
        <button class="at-btn sm ghost" id="del-node">SUPPRIMER</button>`;
      $('#grp-enter').addEventListener('click', () => enterGroup(n.id));
      $('#grp-ungroup').addEventListener('click', () => ungroupNode(n.id));
      $('#del-node').addEventListener('click', deleteSelection);
      const nameInp = $('#grp-name');
      nameInp.addEventListener('input', () => { n.name = nameInp.value; persist(); renderCrumb(); });
      nameInp.addEventListener('change', () => render());
      return;
    }

    const docsList = window.BP2Docs ? BP2Docs.inspectorList(n) : '';
    panel.innerHTML = `
      <div class="bp-prop"><div class="k">Node</div>
        <div class="v" style="font-family:var(--font-mono)">${esc(s.kind === 'ext' ? s.name : s.ref)} <span style="color:var(--ink-muted);font-size:0.73rem">${esc(s.kind === 'ext' ? 'ext · ' + s.ext : s.name)}</span></div></div>
      <div class="bp-prop"><div class="k">Rôle</div><div class="v soft">${esc(s.desc)}</div></div>
      <div class="bp-prop"><div class="k">Pins — contrats <span style="text-transform:none;letter-spacing:0;color:var(--accent);cursor:help" title="Un pin est la prise typée d'un node : ce qu'il accepte (in), ce qu'il produit (out). Le contrat est la forme des données transportées.">· c'est quoi ? ⓘ</span></div>
        ${s.in.map(c2 => pinRow('in', c2)).join('')}${s.out.map(c2 => pinRow('out', c2)).join('')}</div>
      ${docsList}
      ${costRows}
      ${contextSectionHtml(n)}
      <button class="at-btn sm" id="open-fiche">OUVRIR LE DOSSIER DU NODE →</button>
      <button class="at-btn sm ghost" id="del-node">SUPPRIMER LE NODE</button>`;
    bindContextSection(panel, n);
    $('#open-fiche').addEventListener('click', () => openFiche(n));
    $('#del-node').addEventListener('click', deleteSelection);
    $$('[data-doc-open]', panel).forEach(el => el.addEventListener('click', () => {
      BP2Docs.openEditor(n, el.dataset.docOpen, () => { markDirty(); render(); });
    }));
  }

  /* ══ Validation (arbre complet + règles agentiques) ══ */
  function computeValidation() {
    const out = [];
    state._warnByNode = {};
    if (!state.nodes.length) {
      out.push({ level: 'info', text: 'Toile vide — posez un premier pattern (clic droit, ou depuis la palette).' });
      return out;
    }
    const visit = (graph, path, where, fed, drained) => {
      fed = fed || new Set(); drained = drained || new Set();
      graph.nodes.forEach(n => {
        const s = specOf(n);
        if (!s) return;
        if (isExtLocked(n)) {
          out.push({ level: 'err', node: n.id, path, ref: s.name, text: `${where}« ${s.name} » requiert l'extension ${s.ext} — non installée.` });
          if (samePath(path)) state._warnByNode[n.id] = 'extension manquante';
          return;
        }
        s.in.forEach(c => {
          if (!graph.edges.some(e => e.to === n.id && e.contract === c) && !fed.has(c)) {
            const nm = n.kind === 'group' ? (n.name || 'sous-flow') : (s.kind === 'ext' || s.kind === 'agent' ? s.name : s.ref);
            out.push({ level: 'warn', node: n.id, path, ref: nm, text: `${where}${nm} — entrée ${c} non connectée.` });
            if (samePath(path)) state._warnByNode[n.id] = 'entrée non connectée';
          }
        });
        if (n.kind === 'group') visit(n.sub, path.concat(n.id), '◇ ' + (n.name || 'sous-flow') + ' · ',
          new Set(graph.edges.filter(e => e.to === n.id).map(e => e.contract)),
          new Set(graph.edges.filter(e => e.from === n.id).map(e => e.contract)));
      });
      if (topoOrderOf(graph) === null)
        out.push({ level: 'err', path, text: `${where}Cycle détecté — un flow de contrats ne boucle pas.` });
      if (window.BP2Assist) BP2Assist.rules(graph, path, where, specOf, fed, drained).forEach(r => out.push(r));
    };
    visit(state, [], '');
    const allCats = [];
    (function walk(g) { g.nodes.forEach(n => { if (n.kind === 'group') walk(n.sub); else { const s = specOf(n); if (s) allCats.push(s.cat); } }); })(state);
    if (!allCats.includes('GOV') && !allCats.includes('QUA'))
      out.push({ level: 'warn', path: [], text: 'Aucun pattern de gouvernance (GOV) ni de preuve (QUA) — un flow gouverné en attend au moins un.' });
    if (!out.some(r => r.level === 'warn' || r.level === 'err')) {
      const c = countAll();
      out.push({ level: 'ok', text: 'Blueprint valide — ' + c.n + ' nodes (' + c.g + ' sous-flow' + (c.g > 1 ? 's' : '') + '), ' + c.e + ' liens, 0 exécution.' });
    }
    return out;
  }
  const samePath = p => p.join('/') === curPath.join('/');
  function countAll() {
    let n = 0, e = 0, g = 0;
    (function walk(gr) { e += gr.edges.length; gr.nodes.forEach(x => { if (x.kind === 'group') { g++; walk(x.sub); } else n++; }); })(state);
    return { n, e, g };
  }

  function runValidation(silent) {
    const res = computeValidation();
    const problems = res.filter(r => r.level === 'warn' || r.level === 'err');
    const badge = $('#val-count');
    badge.style.display = problems.length ? '' : 'none';
    badge.textContent = problems.length;
    state.meta.validated = state.nodes.length > 0 && !res.some(r => r.level === 'err');
    $('#panel-validation').innerHTML = res.map((r, i) => `
      <div class="bp-vitem ${r.level === 'err' ? 'err' : r.level === 'ok' ? 'okv' : ''}" ${r.node ? `data-goto="${i}"` : ''}>
        ${r.rule ? `<span class="v-rule">${esc(r.rule)} · pratique agentique</span>` : ''}
        ${r.ref ? `<b>${esc(r.ref)}</b> · ` : ''}${r.text}
        ${r.fix && samePath(r.path || []) ? `<br><button class="v-fix" data-fix="${i}">⚡ ${esc(r.fix.label)}</button>` : (r.fix ? `<br><button class="v-fix" data-goto2="${i}">→ ALLER AU NIVEAU CONCERNÉ</button>` : '')}
      </div>`).join('');
    const goto_ = r => {
      if (!samePath(r.path || [])) goToLevel(r.path || []);
      const n = G().nodes.find(x => x.id === r.node);
      if (!n) return;
      clearSelection(); selection.nodes.add(n.id);
      centerOn(n); render();
    };
    $$('#panel-validation [data-goto]').forEach(el => el.addEventListener('click', e => {
      if (e.target.closest('.v-fix')) return;
      goto_(res[parseInt(el.dataset.goto, 10)]);
    }));
    $$('#panel-validation [data-fix]').forEach(el => el.addEventListener('click', e => {
      e.stopPropagation();
      const r = res[parseInt(el.dataset.fix, 10)];
      if (r && r.fix) { r.fix.run(); runValidation(false); }
    }));
    $$('#panel-validation [data-goto2]').forEach(el => el.addEventListener('click', e => {
      e.stopPropagation();
      goto_(res[parseInt(el.dataset.goto2, 10)]);
    }));
    if (!silent) setTab('validation');
    return res;
  }
  function centerOn(n) {
    const r = wrap.getBoundingClientRect();
    view.x = r.width / 2 - (n.x + 95) * view.k;
    view.y = r.height / 2 - (n.y + 40) * view.k;
  }
  $('#bp-validate').addEventListener('click', () => { runValidation(false); render(); });

  /* ══ Simulation ══ */
  function topoOrderOf(g) {
    const indeg = {}; g.nodes.forEach(n => indeg[n.id] = 0);
    g.edges.forEach(e => indeg[e.to] = (indeg[e.to] || 0) + 1);
    const queue = g.nodes.filter(n => !indeg[n.id]).map(n => n.id);
    const order = [];
    while (queue.length) {
      const id = queue.shift(); order.push(id);
      g.edges.filter(e => e.from === id).forEach(e => { if (--indeg[e.to] === 0) queue.push(e.to); });
    }
    return order.length === g.nodes.length ? order : null;
  }
  const topoOrder = () => topoOrderOf(G());

  let simRunning = false;
  async function simulate() {
    if (simRunning) return;
    setTab('simulation');
    const panel = $('#panel-simulation');
    const g = G();
    if (!g.nodes.length) {
      panel.innerHTML = '<p class="empty">rien à simuler — la toile est vide.</p>'; return;
    }
    if (g.nodes.some(n => isExtLocked(n))) {
      panel.innerHTML = `<div class="bp-verdict warn"><div class="t">extension manquante</div><p>Un node du flow vient d'une extension non installée — installez-la depuis la palette, votre toile n'a pas bougé.</p></div>`;
      return;
    }
    if (g.nodes.length >= 2 && !g.edges.length) {
      panel.innerHTML = `<div class="bp-verdict warn"><div class="t">${g.nodes.length} nodes, aucun lien</div>
        <p>La simulation suit les liens. Reliez d'abord : tirez depuis un pin <b>sortie ○</b> vers une <b>entrée ○</b> compatible.</p></div>`;
      emit('sim-blocked', 'no-edges');
      return;
    }
    const order = topoOrder();
    if (!order) {
      panel.innerHTML = `<div class="bp-verdict warn"><div class="t">cycle détecté</div><p>Un flow de contrats ne boucle pas — cassez le cycle pour simuler.</p></div>`;
      return;
    }
    simRunning = true;
    const lines = order.map((id, i) => {
      const n = g.nodes.find(x => x.id === id);
      const s = specOf(n);
      const outs = g.edges.filter(e => e.from === id);
      const nm = n.kind === 'group' ? '◇ ' + (n.name || 'sous-flow') : (s.kind === 'ext' || s.kind === 'agent' ? s.name : s.ref);
      const inner = n.kind === 'group' ? ` <span style="color:var(--ink-muted)">(traverse ${countInside(n).n} nodes internes)</span>` : '';
      return { id, html: `${i + 1} · ${esc(nm)}${inner} — ${outs.length ? outs.map(o => esc(o.contract) + ' émis').join(', ') : (s.out.length ? s.out.map(esc).join(', ') + ' (non consommé)' : 'terminal')}` };
    });
    panel.innerHTML = `<div class="bp-prop"><div class="k">Plan — ordre des contrats${curPath.length ? ' · niveau N' + (curPath.length + 1) : ''}</div></div>
      <div class="bp-sim-log">${lines.map(l => `<div class="bp-sim-line" data-sim="${l.id}">${l.html}</div>`).join('')}</div>
      <div id="sim-verdict"></div>`;
    for (const l of lines) {
      const lineEl = panel.querySelector(`[data-sim="${l.id}"]`);
      const nodeEl = items.querySelector(`.bp-node[data-id="${l.id}"]`);
      if (lineEl) lineEl.classList.add('on');
      if (nodeEl) nodeEl.classList.add('sim-active');
      g.edges.filter(e => e.from === l.id).forEach(e => {
        const p = svg.querySelector(`path.edge[data-e="${e.id}"]`);
        if (p) p.classList.add('flow');
      });
      await new Promise(r => setTimeout(r, 560));
    }
    setTimeout(() => {
      $$('.bp-node.sim-active', items).forEach(el => el.classList.remove('sim-active'));
      $$('path.flow', svg).forEach(p => p.classList.remove('flow'));
    }, 900);
    const flat = [];
    (function walk(gr) { gr.nodes.forEach(n => { if (n.kind === 'group') walk(n.sub); else flat.push(n); }); })(g);
    const hasExec = flat.some(n => { const s = specOf(n); return s && (s.kind === 'ext' || s.kind === 'agent' || s.cat === 'ENG'); });
    const contracts = new Set(g.edges.map(e => e.contract));
    const cost = window.BP2Cost ? BP2Cost.summaryLine(g, specOf) : '';
    let verdict = `<div class="bp-verdict"><div class="t">prêt à compiler ✓</div>
      <p>${g.nodes.length} nodes · ${g.edges.length} liens · ${contracts.size} contrat${contracts.size > 1 ? 's' : ''} échangé${contracts.size > 1 ? 's' : ''} · <b>0 exécution</b> — la simulation est statique.${cost ? '<br>' + cost : ''}</p></div>`;
    if (!hasExec) {
      verdict += `<div class="bp-verdict warn" style="margin-top:8px"><div class="t">ce flow gouverne mais ne délègue rien</div>
        <p>Ajoutez un node d'exécution quand vous serez prêt — ORC-11, ou un Crew d'extension.</p></div>`;
    }
    $('#sim-verdict').innerHTML = verdict;
    state.meta.simulated = true;

    /* verdict du serveur réel : lint normatif + prérequis projet (extensions,
       artefacts présents) — la vue locale reste l'animation, lui décide. */
    if (Atelier.online) {
      try {
        const payload = Object.assign({ blueprintVersion: 2, id: bpId, name: (state.meta && state.meta.name) || bpId }, state);
        const r = await Atelier.api('/api/blueprints/' + encodeURIComponent(bpId) + '/simulate',
          { method: 'POST', body: JSON.stringify(payload) });
        if ((r.blockers || []).length) {
          state.meta.simulated = false;
          verdict = `<div class="bp-verdict warn"><div class="t">bloqué par le serveur — ${r.blockers.length} prérequis</div>
            <p>${r.blockers.map(esc).join('<br>')}</p></div>`;
        } else {
          verdict += `<div class="bp-verdict" style="margin-top:8px"><div class="t">vérifié par l'API locale ✓</div>
            <p>${esc(r.verdict)} · ${(r.steps || []).length} étapes ordonnées${(r.warnings || []).length ? '<br>avertissements : ' + r.warnings.map(esc).join(' · ') : ''}</p></div>`;
        }
        /* pression de contexte (C1) : verdicts par node, nodes critical colorés */
        ctxPressure = {};
        (r.contextPressure || []).forEach(p => { ctxPressure[p.nodeId] = p.verdict; });
        const tension = (r.contextPressure || []).filter(p => p.verdict !== 'ok');
        if (tension.length) {
          const nameOf = idn => {
            const nn = findNodeDeep(idn);
            const ss = nn && specOf(nn);
            return nn ? (nn.kind === 'group' ? (nn.name || 'sous-flow') : (nn.name || (ss && (ss.kind === 'ext' || ss.kind === 'agent') ? ss.name : nn.ref) || idn)) : idn;
          };
          verdict += `<div class="bp-verdict${tension.some(p => p.verdict === 'critical') ? ' warn' : ''}" style="margin-top:8px"><div class="t">pression de contexte — ${tension.length} node${tension.length > 1 ? 's' : ''} sous tension</div>
            <p>${tension.map(p => `<span class="ctx-vd ${p.verdict}">${p.verdict}</span> ${esc(nameOf(p.nodeId))} — ${p.windowPct} % de la fenêtre (~${Math.round(p.estimatedTokens / 1000)}k tokens)`).join('<br>')}
            <br>remède : compaction digest ou isolation en amont.</p></div>`;
          tension.forEach(p => {
            if (p.verdict !== 'critical') return;
            const el = items.querySelector(`.bp-node[data-id="${p.nodeId}"]`);
            if (el) el.classList.add('ctx-critical');
          });
        }
        $('#sim-verdict').innerHTML = verdict;
      } catch (e) { /* API indisponible en cours de route : verdict local seul */ }
    }
    updateCompileBtn();
    persist();
    simRunning = false;
    emit('sim-done', state);
  }
  $('#bp-simulate').addEventListener('click', simulate);

  /* ══ Compilation ══ */
  function updateCompileBtn() {
    const btn = $('#bp-compile');
    const st = $('#bp-compile-state');
    if (state.meta.compiledAt && !state.meta.dirty) {
      btn.dataset.state = 'clean'; btn.disabled = true; st.textContent = '· À JOUR ✓';
    } else if (state.meta.simulated) {
      btn.dataset.state = 'dirty'; btn.disabled = false; st.textContent = '· PRÊT';
    } else {
      btn.dataset.state = 'dirty'; btn.disabled = true;
      st.textContent = state.nodes.length ? '· SIMULEZ D\u2019ABORD' : '· MODIFIÉ';
    }
  }
  async function compile() {
    if (!state.meta.simulated) return;
    const agents = new Set();
    let skills = 0, groups = 0, hasExt = false;
    (function walk(g) {
      g.nodes.forEach(n => {
        if (n.kind === 'group') { groups++; walk(n.sub); return; }
        if (n.kind === 'agent') { agents.add(n.name || 'agent'); skills += (n.skills || []).length; return; }
        if (n.kind === 'trigger') return;
        const s = specOf(n);
        if (!s) return;
        skills++;
        (s.agents || []).forEach(a => agents.add(a));
        if (s.kind === 'ext') hasExt = true;
      });
    })(state);
    const tc = window.BP2Team ? BP2Team.counts(state) : { tools: new Set(), mcp: new Set(), hooks: 0 };
    const itemsOut = { agents: agents.size || 1, skills, workflows: 1 + groups };
    if (tc.tools.size) itemsOut.outils = tc.tools.size;
    if (tc.mcp.size) itemsOut.mcp = tc.mcp.size;
    if (hasExt || tc.hooks) itemsOut.hooks = (hasExt ? 1 : 0) + tc.hooks;

    if (!Atelier.online) {
      Atelier.toast('Compilation impossible hors atelier \u2014 lancez <code>grimoire serve</code> dans le projet.', { ms: 5200 });
      return;
    }
    const btn = $('#bp-compile'); if (btn) btn.disabled = true;
    Atelier.toast('<b>' + esc(bpId) + '</b> \u2014 compilation par l\u2019API locale\u2026', { ms: 2200 });
    try {
      const payload = Object.assign({ blueprintVersion: 2, id: bpId, name: (state.meta && state.meta.name) || bpId }, state);
      const r = await Atelier.api('/api/blueprints/' + encodeURIComponent(bpId) + '/compile',
        { method: 'POST', body: JSON.stringify(payload) });
      Atelier.pushArtifacts({ bp: bpId, when: Date.now(), items: itemsOut, artifact: r.artifact, hash: r.hash });
      /* la section compiled vit dans l'état : l'auto-save (PUT) la persiste
         avec le blueprint — détection de dérive au hash. */
      state.compiled = { at: new Date().toISOString(), artifacts: [{ path: r.artifact, hash: r.hash, sourceNode: bpId }] };
      state.meta.compiledAt = Date.now();
      state.meta.dirty = false;
      updateCompileBtn(); persist();
      Atelier.toast('<b>' + esc(bpId) + '</b> compilé ✓ \u2014 <code>' + esc(r.artifact) + '</code> écrit dans le projet'
        + (r.hash ? ' · <span style="opacity:.7">' + esc(String(r.hash).slice(0, 19)) + '\u2026</span>' : '')
        + '<br>rien ne s\u2019est exécuté \u2014 la revue est le diff git.', { good: true, ms: 6500 });
      emit('compiled', itemsOut);
    } catch (e) {
      Atelier.toast('Compilation refusée : ' + esc(String(e.message || e)), { ms: 6500 });
      updateCompileBtn();
    }
    if (btn) btn.disabled = false;
  }
  $('#bp-compile').addEventListener('click', compile);

  /* ══ Fiche / dossier (drawer) ══ */
  function openFiche(nodeOrRef) {
    const isNode = typeof nodeOrRef === 'object';
    const n = isNode ? nodeOrRef : null;
    if (n && n.kind === 'agent' && window.BP2Team) {
      BP2Team.openSheet(n, part => { if (part === 'pins') pruneEdgesFor(n); markDirty(); render(); });
      return;
    }
    if (n && n.kind === 'trigger' && window.BP2Team) {
      BP2Team.openTriggerSheet(n, () => { markDirty(); render(); });
      return;
    }
    const s = isNode ? specOf(n) : Atelier.nodeSpec(nodeOrRef);
    if (!s) return;
    const f = $('#bp-fiche');
    if (n && n.kind === 'group') {
      const c = countInside(n);
      f.innerHTML = `
        <div class="f-head">
          <div class="at-row sb">
            <span style="font-family:var(--font-mono);font-size:0.88rem;font-weight:700">◇ ${esc(n.name || 'sous-flow')}</span>
            <button class="at-btn sm ghost" id="fiche-close">✕</button>
          </div>
          <div class="at-row" style="gap:8px;margin-top:6px">
            <span class="at-chip"><span class="cdot" style="background:#A78BFA"></span>Sous-flow · niveau N${curPath.length + 2}</span>
            <span class="at-chip">${c.n} nodes · ${c.docs} docs</span>
          </div>
        </div>
        <div class="f-body">
          <p style="font-size:0.82rem;color:var(--ink-soft);line-height:1.6">Un sous-flow encapsule une partie du blueprint (vue C4). Ses <b>ports</b> sont les contrats que son intérieur ne résout pas lui-même.</p>
          <div><div class="at-lbl" style="margin-bottom:6px">Contenu</div>
          ${(n.sub.nodes || []).map(sn => { const ss = specOf(sn); return `<div style="font-family:var(--font-mono);font-size:0.73rem;color:var(--ink-soft);padding:3px 0">${sn.kind === 'group' ? '◇ ' + esc(sn.name || 'sous-flow') : esc(ss ? (ss.kind === 'ext' ? ss.name : ss.ref) : '?')} <span style="color:var(--ink-muted)">${esc(ss && ss.kind !== 'group' ? ss.name || '' : '')}</span></div>`; }).join('') || '<p class="empty">vide.</p>'}</div>
        </div>
        <div class="f-foot"><button class="at-btn sm acc" id="fiche-enter">ENTRER →</button></div>`;
      f.classList.add('open');
      $('#fiche-close').addEventListener('click', closeFiche);
      $('#fiche-enter').addEventListener('click', () => { closeFiche(); enterGroup(n.id); });
      return;
    }
    const catName = s.kind === 'ext' ? 'Extension · ' + s.extName : (Atelier.catById[s.cat] || {}).name || s.cat;
    const p = s.kind === 'pattern' ? Atelier.byRef[s.ref] : null;
    const docsHtml = n && window.BP2Docs ? BP2Docs.ficheSection(n) : '';
    f.innerHTML = `
      <div class="f-head">
        <div class="at-row sb">
          <span style="font-family:var(--font-mono);font-size:0.88rem;font-weight:700">${esc(s.kind === 'ext' ? s.name : s.ref)}</span>
          <button class="at-btn sm ghost" id="fiche-close">✕</button>
        </div>
        <div class="at-row" style="gap:8px;margin-top:6px">
          <span class="at-chip"><span class="cdot" style="background:${Atelier.catColor(s.cat)}"></span>${esc(catName)}</span>
          ${p && p.maturity ? `<span class="at-chip">maturité · ${esc(p.maturity)}</span>` : ''}
        </div>
      </div>
      <div class="f-body">
        <h3 style="font-size:1rem">${esc(s.name)}</h3>
        <p style="font-size:0.82rem;color:var(--ink-soft);line-height:1.6">${esc(s.desc)}</p>
        ${docsHtml}
        <div>
          <div class="at-lbl" style="margin-bottom:6px">Contrats</div>
          ${s.in.map(c => `<div style="font-family:var(--font-mono);font-size:0.73rem;color:var(--ink-soft);padding:3px 0;display:flex;gap:8px;align-items:center"><span style="width:8px;height:8px;border-radius:50%;background:${Atelier.contractColor(c)}"></span>in · ${esc(c)} — ${esc((Atelier.contractById[c] || {}).desc || '')}</div>`).join('')}
          ${s.out.map(c => `<div style="font-family:var(--font-mono);font-size:0.73rem;color:var(--ink-soft);padding:3px 0;display:flex;gap:8px;align-items:center"><span style="width:8px;height:8px;border-radius:50%;background:${Atelier.contractColor(c)}"></span>out · ${esc(c)} — ${esc((Atelier.contractById[c] || {}).desc || '')}</div>`).join('')}
        </div>
        ${p ? `<div><div class="at-lbl" style="margin-bottom:6px">Checks</div>${p.checks.map(c => `<div style="font-size:0.77rem;color:var(--ink-soft);padding:2px 0">✓ ${esc(c)}</div>`).join('')}</div>
        <div><div class="at-lbl" style="margin-bottom:6px">Agents</div><div class="at-row" style="gap:5px;flex-wrap:wrap">${p.agents.map(a => `<span class="at-chip" style="font-size:0.64rem">${esc(a)}</span>`).join('')}</div></div>` : ''}
      </div>
      <div class="f-foot">
        ${s.kind === 'pattern'
          ? `<a class="at-btn sm" href="patterns.html#${esc(s.ref)}">VOIR DANS LE CATALOGUE →</a>`
          : `<a class="at-btn sm" href="extensions.html?ext=${esc(s.ext)}">VOIR L'EXTENSION →</a>`}
      </div>`;
    f.classList.add('open');
    $('#fiche-close').addEventListener('click', closeFiche);
    if (n && window.BP2Docs) {
      $$('[data-doc-open]', f).forEach(el => el.addEventListener('click', () => {
        BP2Docs.openEditor(n, el.dataset.docOpen, () => { markDirty(); render(); openFiche(n); });
      }));
    }
  }
  function closeFiche() { $('#bp-fiche').classList.remove('open'); }

  /* ══ Lexique / aide ══ */
  $('#bp-help').addEventListener('click', () => {
    const f = $('#bp-fiche');
    const LEX = [
      ['agent', 'un travailleur concret du flow : il reçoit une tâche, travaille avec ses outils, rend une preuve — sa fiche se règle au double-clic'],
      ['orchestrateur', 'l\u2019agent qui ne produit pas : il reçoit la mission, la découpe et délègue aux spécialistes'],
      ['déclencheur ▶', 'ce qui lance le flow : manuel, planifié, webhook ou pull request — un seul par flow'],
      ['outil', 'ce qu\u2019un agent a le droit de FAIRE (lire, écrire, exécuter…) — tout le reste lui est refusé'],
      ['branchement MCP', 'un service extérieur (GitHub, base de données…) branché à un agent, avec un périmètre déclaré'],
      ['hook', 'un réflexe automatique : « quand il se passe ça → fais ça » — tourne tout seul, à chaque fois'],
      ['skill', 'un savoir-faire réutilisable ajouté au bagage d\u2019un agent — compilé en fichier'],
      ['prompt système', 'le document qui définit un agent : rôle, mission, refus — éditable avec coloration'],
      ['pattern', 'une pratique normée du standard — 36, en 11 catégories, chacune avec ses checks'],
      ['blueprint', 'un flow composé d\u2019agents et de patterns ; il se valide, se simule, se compile — n\u2019exécute jamais'],
      ['blueprint actif ●', 'LE flow du projet — un seul à la fois ; les autres sont des brouillons ou des dérives'],
      ['template', 'un flow préfait du catalogue Grimoire — instancié en copie locale que vous dérivez librement'],
      ['sous-flow ◇', 'un conteneur C4 : une partie du flow encapsulée, avec ses ports — ⌘G pour grouper, double-clic pour entrer'],
      ['pin', 'la prise typée d\u2019un node : entrée (ce qu\u2019il accepte), sortie (ce qu\u2019il produit)'],
      ['contrat', 'la forme des données qu\u2019un lien transporte — des types d\u2019artefacts réels : task envelope, evidence pack…'],
      ['document', 'un artefact éditable porté par un node : mission brief, contrat de complétion, prompt système…'],
      ['coût (tokens)', 'estimation statique par node et par chemin : contexte consommé + sorties, × itérations — jamais une facture'],
      ['compilation', 'la transformation d\u2019un blueprint en artefacts dans le projet — sans exécution'],
      ['artefact', 'ce que la compilation produit : agents, skills, workflows, hooks — versionnés, tracés']
    ];
    f.innerHTML = `
      <div class="f-head"><div class="at-row sb"><h2 style="font-size:1rem">Lexique</h2><button class="at-btn sm ghost" id="fiche-close">✕</button></div></div>
      <div class="f-body">
        ${LEX.map(([k, v]) => `<div><b style="font-family:var(--font-mono);font-size:0.77rem;color:var(--accent)">${k}</b><p style="font-size:0.82rem;color:var(--ink-soft);line-height:1.55;margin-top:2px">${v}</p></div>`).join('')}
        <div style="border-top:1px solid var(--line);padding-top:14px">
          <div class="at-lbl" style="margin-bottom:8px">Gestes</div>
          <p style="font-size:0.77rem;color:var(--ink-soft);line-height:1.8">clic droit — ajouter un node<br>fil lâché dans le vide — menu des nodes compatibles<br>double-clic — entrer (sous-flow) ou dossier (node)<br><span class="at-kbd">⌘G</span> grouper · <span class="at-kbd">⌘⇧G</span> dégrouper · <span class="at-kbd">Échap</span> remonter<br><span class="at-kbd">C</span> commentaire · <span class="at-kbd">F</span> recadrer · <span class="at-kbd">⌘Z</span> annuler · <span class="at-kbd">⌘D</span> dupliquer</p>
        </div>
      </div>
      <div class="f-foot">
        <button class="at-btn sm acc" id="replay-tour">REJOUER LA VISITE GUIDÉE</button>
        <button class="at-btn sm ghost" id="replay-news">NOUVEAUTÉS ✦</button>
      </div>`;
    f.classList.add('open');
    $('#fiche-close').addEventListener('click', closeFiche);
    $('#replay-tour').addEventListener('click', () => { closeFiche(); emit('tour-replay'); });
    $('#replay-news').addEventListener('click', () => { closeFiche(); emit('news-replay'); });
  });

  /* ══ Vue chaleur ══ */
  $('#bp-heat').addEventListener('click', () => {
    heatMode = !heatMode;
    $('#bp-heat').classList.toggle('on', heatMode);
    render();
    if (heatMode) Atelier.toast('Vue chaleur — coût estimé par node et par lien. Détail par chemin dans l\u2019onglet <b>COÛT</b>.');
  });

  /* ══ Blueprints : sélection / création / chargement ══ */
  function refreshSelect() {
    const list = Atelier.bpList();
    const act = localStorage.getItem('grimoire.atelier.bp.active');
    $('#bp-select').innerHTML = list.map(id => `<option value="${esc(id)}"${id === bpId ? ' selected' : ''}>${id === act ? '● ' : '◆ '}${esc(id)}</option>`).join('');
  }
  $('#bp-select').addEventListener('change', e => loadBp(e.target.value));
  $('#bp-new').addEventListener('click', () => {
    const list = Atelier.bpList();
    let i = list.length + 1, id;
    do { id = 'flow-' + i++; } while (list.includes(id));
    Atelier.saveBp(id, blankState());
    loadBp(id);
  });

  function instantiateExample() {
    const ex = CAT.example_blueprint;
    const s2 = blankState();
    s2.nodes = ex.nodes.map(n => ({ id: uid(), ref: n.ref, x: n.x, y: n.y }));
    s2.edges = ex.edges.map(e => {
      const from = s2.nodes[e.from], to = s2.nodes[e.to];
      const sf = Atelier.nodeSpec(from.ref), st = Atelier.nodeSpec(to.ref);
      const contract = sf.out.find(c => st.in.includes(c)) || sf.out[0];
      return { id: uid(), from: from.id, to: to.id, contract };
    });
    s2.comments = [{ id: uid(), x: 60, y: 200, w: 1620, h: 340, label: 'délégation gouvernée — exemple' }];
    return s2;
  }

  /* exemple studio : équipe concrète + sous-flow + documents remplis (C4 / docs / coût) */
  function studioExample() {
    const s2 = blankState();
    const id = {};
    ['prd', 'chef', 'grp', 'gov', 'qua2', 'ops', 'gov2', 'mem', 'impl', 'test', 'sec'].forEach(k => id[k] = uid());
    const EX = window.BP2Docs ? BP2Docs.EXAMPLES : {};
    s2.nodes = [
      { id: id.prd, ref: 'ORC-02', x: 90, y: 300, docs: { 'mission-brief.md': { content: EX['mission-brief.md'] || '', at: Date.now() } } },
      { id: id.chef, kind: 'agent', role: 'orchestrateur', name: 'chef-de-mission', model: 'sonnet', delegates: false,
        tools: [], mcp: [], skills: [], hooks: [{ when: 'task-end', then: 'log-trace' }], docs: {}, x: 400, y: 300 },
      { id: id.grp, kind: 'group', name: 'exécution prouvée', x: 720, y: 290,
        sub: {
          nodes: [
            { id: id.impl, kind: 'agent', role: 'agent', name: 'implémenteur', model: 'sonnet', delegates: false,
              tools: ['fs-read', 'fs-write', 'code-search', 'tests'], mcp: ['github'], skills: ['ecriture-tests'],
              hooks: [{ when: 'after-tool', then: 'scan-secrets' }, { when: 'before-write', then: 'block-scope' }],
              docs: { 'system-prompt.md': { content: EX['system-prompt.md'] || '', at: Date.now() } }, x: 140, y: 180 },
            { id: id.test, kind: 'agent', role: 'agent', name: 'testeur', sub: true, model: 'haiku', delegates: false,
              tools: ['fs-read', 'tests'], mcp: [], skills: ['ecriture-tests', 'revue-code'],
              hooks: [{ when: 'task-end', then: 'run-tests' }], docs: {}, x: 140, y: 400 },
            { id: id.sec, ref: 'QUA-04', x: 560, y: 290 }
          ],
          edges: [
            { id: uid(), from: id.impl, to: id.sec, contract: 'evidence-pack' },
            { id: uid(), from: id.test, to: id.sec, contract: 'evidence-pack' }
          ],
          comments: [{ id: uid(), x: 80, y: 110, w: 800, h: 430, label: 'les agents produisent, le scan verrouille' }]
        } },
      { id: id.gov, ref: 'QUA-05', x: 1060, y: 240, docs: { 'completion-contract.yaml': { content: EX['completion-contract.yaml'] || '', at: Date.now() } } },
      { id: id.qua2, ref: 'QUA-02', x: 1060, y: 440 },
      { id: id.ops, ref: 'QUA-08', x: 1370, y: 240 },
      { id: id.gov2, ref: 'QUA-01', x: 1370, y: 440 },
      { id: id.mem, ref: 'KNO-02', x: 1670, y: 440 }
    ];
    s2.edges = [
      { id: uid(), from: id.prd, to: id.chef, contract: 'task-envelope' },
      { id: uid(), from: id.chef, to: id.grp, contract: 'task-envelope' },
      { id: uid(), from: id.grp, to: id.gov, contract: 'evidence-pack' },
      { id: uid(), from: id.grp, to: id.qua2, contract: 'evidence-pack' },
      { id: uid(), from: id.gov, to: id.ops, contract: 'evidence-pack' },
      { id: uid(), from: id.qua2, to: id.gov2, contract: 'evidence-pack' },
      { id: uid(), from: id.qua2, to: id.mem, contract: 'evidence-pack' }
    ];
    s2.comments = [{ id: uid(), x: 1020, y: 160, w: 560, h: 220, label: 'décider — porte + déploiement' }];
    return s2;
  }
  function loadStudioExample() {
    loadBp('studio-demo', studioExample());
    Atelier.toast('Exemple chargé : un <b>chef-de-mission</b> délègue à deux agents équipés dans un sous-flow ◇. Double-clic sur le sous-flow pour entrer, puis sur un agent pour sa <b>fiche</b> (outils, hooks, prompt). Coût par chemin dans <b>COÛT</b>.', { good: true, ms: 7000 });
  }

  function seedUseCase(uc) {
    const s2 = blankState();
    let x = 140;
    uc.slots.forEach(slot => {
      const ref = slot.suggest[0];
      const ns = Atelier.nodeSpec(ref);
      if (!ns || ns.locked) { return; }
      s2.nodes.push({ id: uid(), ref, x, y: 280 + (s2.nodes.length % 2) * 70 });
      x += 300;
    });
    for (let i = 0; i < s2.nodes.length - 1; i++) {
      const a = Atelier.nodeSpec(s2.nodes[i].ref), b = Atelier.nodeSpec(s2.nodes[i + 1].ref);
      const c = a.out.find(cc => b.in.includes(cc));
      if (c) s2.edges.push({ id: uid(), from: s2.nodes[i].id, to: s2.nodes[i + 1].id, contract: c });
    }
    s2.comments = [{ id: uid(), x: 80, y: 190, w: Math.max(420, x - 160), h: 300, label: uc.name }];
    return s2;
  }

  function loadBp(id, preset) {
    bpId = id;
    state = preset || Atelier.loadBp(id) || blankState();
    state.meta = state.meta || blankState().meta;
    curPath = (state.meta.path || []).slice();
    if (!levelGraph(curPath)) curPath = [];
    history = [];
    clearSelection();
    restoreView();
    Atelier.saveBp(id, state);
    refreshSelect();
    render();
    runValidation(true);
    updateCompileBtn();
    if (window.BP2Cost) BP2Cost.refresh();
    if (G().nodes.length) fitView();
    const missing = [];
    (function walk(g) { g.nodes.forEach(n => { if (n.kind === 'group') walk(n.sub); else if (isExtLocked(n)) { const s = specOf(n); if (s && !missing.includes(s.ext)) missing.push(s.ext); } }); })(state);
    if (missing.length) {
      Atelier.toast('Ce blueprint requiert l\u2019extension <b>' + esc(missing[0]) + '</b> — installez-la depuis la palette (pointillés).', { ms: 5200 });
    }
    history = [];
    emit('bp-loaded', { id, empty: !state.nodes.length });
  }

  /* ══ Boot ══ */
  const params = new URLSearchParams(location.search);
  const addRef = params.get('add');
  let initial = params.get('bp');
  const ucId = params.get('uc');

  /* init des modules compagnons */
  const core = {
    on, state: () => state, bpId: () => bpId, path: () => curPath.slice(),
    G, specOf, addNode, addEdge, markDirty, render, setTab, uid,
    enterGroup, upLevel, groupSelection, ungroupNode, openFiche, fitView,
    simulate, compile, installExtInline, loadBp, refreshSelect,
    heat: () => heatMode,
    palette: { search: q => { $('#pal-search').value = q; palQ = q; renderPalette(); } },
    pulse(sel) {
      const el = document.querySelector(sel);
      if (!el) return;
      el.classList.add('bp-pulse');
      setTimeout(() => el.classList.remove('bp-pulse'), 2600);
    },
    pulsePalette(refOrName) {
      const el = $$('.bp-pal-item').find(x => x.textContent.includes(refOrName));
      if (el) { el.classList.add('bp-pulse'); setTimeout(() => el.classList.remove('bp-pulse'), 2600); }
    },
    pulsePin(dir, contract) {
      const el = $$('.bp-pin').find(p => p.dataset.dir === dir && p.dataset.contract === contract);
      if (el) { el.classList.add('bp-pulse'); setTimeout(() => el.classList.remove('bp-pulse'), 2600); }
    },
    loadExample: () => loadBp('onboarding-crew', instantiateExample()),
    loadStudioExample,
    countAll
  };
  window.BPEditor = core;
  if (window.BP2Team) BP2Team.init(core);
  if (window.BP2Cost) BP2Cost.init(core);
  if (window.BP2Docs) BP2Docs.init(core);
  if (window.BP2Assist) BP2Assist.init(core);

  if (params.get('new')) {
    const list = Atelier.bpList();
    let i = list.length + 1, id;
    do { id = 'flow-' + i++; } while (list.includes(id));
    loadBp(id);
  } else if (ucId) {
    const uc = CAT.use_cases.find(u => u.id === ucId);
    const id = ucId;
    if (uc && !Atelier.loadBp(id)) loadBp(id, seedUseCase(uc));
    else loadBp(id);
    Atelier.toast('Squelette posé depuis le use-case <b>' + esc(uc ? uc.name : ucId) + '</b> — divergez librement.');
  } else if (initial === 'onboarding-crew' && !Atelier.loadBp('onboarding-crew')) {
    loadBp('onboarding-crew', instantiateExample());
  } else if (initial === 'studio-demo' && !Atelier.loadBp('studio-demo')) {
    loadStudioExample();
  } else if (initial) {
    loadBp(initial);
  } else {
    const last = localStorage.getItem('grimoire.atelier.bp.current');
    const list = Atelier.bpList();
    loadBp(last && list.includes(last) ? last : (list[0] || 'premier-flow'));
  }

  if (addRef) {
    const s = Atelier.nodeSpec(addRef);
    if (s) {
      const doAdd = () => {
        const r = wrap.getBoundingClientRect();
        const c = toWorld(r.left + r.width / 2, r.top + r.height / 2);
        addNode(addRef, c.x - 90, c.y - 40);
        Atelier.toast('<b>' + esc(s.kind === 'ext' ? s.name : s.ref) + '</b> posé — reliez-le par ses pins.');
      };
      if (s.locked) installExtInline(s.ext, doAdd); else doAdd();
    }
  }
})();
