/* ════════════════════════════════════════════════════════════════════
   forge-observatory.js — Observatory render engine
   Data-driven : data/{observatory,meta,activity,insights}.json
   Aucune dépendance externe. Tout chart est du SVG fait main.
   Statuts de données : RÉEL (live local) · SNAPSHOT (vitrine) · PRIVÉ.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Couleurs ── */
  const EVT_COLOR = {
    ACTION: 'var(--accent)', DECISION: 'var(--data-cyan)', HANDOFF: 'var(--data-violet)',
    CHECKPOINT: 'var(--data-green)', REMEMBER: 'var(--data-amber)', WARN: 'var(--data-amber)', ERROR: 'var(--data-red)',
  };
  const REL_COLOR = { handoff: 'var(--data-violet)', spec: 'var(--data-cyan)', memory: 'var(--data-amber)', escalation: 'var(--data-red)' };
  const MODEL_COLOR = { 'opus-4-8': 'var(--data-cyan)', 'gpt-5.3-codex': 'var(--data-violet)', 'gemini-3-pro': 'var(--data-amber)', 'gpt-4o': 'var(--data-green)' };
  const PROVIDER_COLOR = { anthropic: 'var(--data-cyan)', openai: 'var(--data-violet)', google: 'var(--data-amber)' };
  const TOOL_CAT = { 'orchestrator': 'var(--accent)', 'llm.complete': 'var(--data-cyan)', 'shell.exec': 'var(--data-violet)', 'fs.read': 'var(--data-amber)', 'web.fetch': 'var(--data-amber)', 'memory.query': 'var(--data-green)', 'memory.write': 'var(--data-green)' };

  /* ── Helpers ── */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));
  const evColor = (t) => EVT_COLOR[t] || 'var(--ink-soft)';
  const relColor = (t) => REL_COLOR[t] || 'var(--accent)';
  const modelColor = (m) => MODEL_COLOR[m] || 'var(--ink-soft)';
  const provColor = (p) => PROVIDER_COLOR[p] || 'var(--ink-soft)';
  const num = (n) => (n == null ? '—' : Number(n).toLocaleString('fr-FR'));
  const usd = (n) => '$' + Number(n || 0).toFixed(Number(n) < 1 ? (Number(n) < 0.1 ? 4 : 3) : 2);
  const ms = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 2) + ' s' : Math.round(n) + ' ms');
  const pct = (n, d = 1) => (n == null ? '—' : Number(n).toFixed(d) + '%');
  const hide = (sel) => { const el = typeof sel === 'string' ? document.querySelector(sel) : sel; if (el) el.style.display = 'none'; };
  function countBy(arr, key) { const m = {}; (arr || []).forEach((x) => { const k = typeof key === 'function' ? key(x) : x[key]; if (k != null) m[k] = (m[k] || 0) + 1; }); return m; }
  function sum(arr, f) { return (arr || []).reduce((a, x) => a + (f ? f(x) : x), 0); }

  /* ── Chart primitives ── */
  function bars(rows, opts) {
    opts = opts || {};
    const max = Math.max(1, ...rows.map((r) => r.value));
    return rows.map((r) => {
      const w = (r.value / max * 100).toFixed(1);
      const lbl = opts.disp ? opts.disp(r.value, r) : r.value;
      return `<div class="bar-row"><span class="bar-lbl" title="${esc(r.label)}">${esc(r.label)}</span>` +
        `<span class="bar-track"><span class="bar-fill" style="width:${w}%;background:${r.color || 'var(--accent)'}"></span></span>` +
        `<span class="bar-val">${esc(lbl)}</span></div>`;
    }).join('');
  }

  // Donut from segments [{label,value,color}]
  function donut(segs, opts) {
    opts = opts || {}; const R = 52, S = 14, C = 2 * Math.PI * R, tot = Math.max(0.0001, sum(segs, (s) => s.value));
    let off = 0, arcs = '';
    segs.forEach((s) => {
      const frac = s.value / tot, len = frac * C;
      arcs += `<circle cx="70" cy="70" r="${R}" fill="none" stroke="${s.color}" stroke-width="${S}" ` +
        `stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" ` +
        `transform="rotate(-90 70 70)" class="donut-seg"><title>${esc(s.label)} · ${(frac * 100).toFixed(1)}%</title></circle>`;
      off += len;
    });
    return `<svg viewBox="0 0 140 140" class="donut-svg" aria-hidden="true">` +
      `<circle cx="70" cy="70" r="${R}" fill="none" stroke="var(--elev-3)" stroke-width="${S}"/>${arcs}` +
      `<text x="70" y="66" text-anchor="middle" class="donut-c1">${esc(opts.center || '')}</text>` +
      `<text x="70" y="84" text-anchor="middle" class="donut-c2">${esc(opts.sub || '')}</text></svg>`;
  }

  function legend(segs, dispFn) {
    return `<div class="leg">` + segs.map((s) =>
      `<div class="leg-row"><span class="leg-dot" style="background:${s.color}"></span>` +
      `<span class="leg-lbl">${esc(s.label)}</span><span class="leg-val">${esc(dispFn ? dispFn(s) : s.value)}</span></div>`
    ).join('') + `</div>`;
  }

  // Polar->cartesian + semicircle gauge (0..1 of value/max)
  function polar(cx, cy, r, deg) { const a = (deg - 90) * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
  function arcPath(cx, cy, r, a0, a1) {
    const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
    const large = (a1 - a0) % 360 > 180 ? 1 : 0;
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }
  function gauge(value, max, opts) {
    opts = opts || {}; const cx = 90, cy = 88, r = 70, frac = Math.max(0, Math.min(1, value / max));
    const end = -90 + frac * 180; const col = opts.color || 'var(--accent)';
    return `<svg viewBox="0 0 180 110" class="gauge-svg" aria-hidden="true">` +
      `<path d="${arcPath(cx, cy, r, -90, 90)}" fill="none" stroke="var(--elev-3)" stroke-width="12" stroke-linecap="round"/>` +
      `<path d="${arcPath(cx, cy, r, -90, end)}" fill="none" stroke="${col}" stroke-width="12" stroke-linecap="round" class="gauge-val"/>` +
      `<text x="90" y="80" text-anchor="middle" class="gauge-num" style="fill:${col}">${esc(opts.label || value)}</text>` +
      `<text x="90" y="100" text-anchor="middle" class="gauge-sub">${esc(opts.sub || '')}</text></svg>`;
  }

  // Radar for dims {name:value(0..100)}
  function radar(dims) {
    const keys = Object.keys(dims), n = keys.length, cx = 130, cy = 125, R = 95;
    const ring = (f) => keys.map((k, i) => polar(cx, cy, R * f, i * 360 / n).map((v) => v.toFixed(1)).join(',')).join(' ');
    const grid = [0.25, 0.5, 0.75, 1].map((f) => `<polygon points="${ring(f)}" fill="none" stroke="var(--line)" stroke-width="1"/>`).join('');
    const spokes = keys.map((k, i) => { const [x, y] = polar(cx, cy, R, i * 360 / n); return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--line)" stroke-width="1"/>`; }).join('');
    const pts = keys.map((k, i) => polar(cx, cy, R * (dims[k] / 100), i * 360 / n).map((v) => v.toFixed(1)).join(',')).join(' ');
    const labels = keys.map((k, i) => {
      const [x, y] = polar(cx, cy, R + 18, i * 360 / n);
      const anchor = Math.abs(x - cx) < 8 ? 'middle' : (x > cx ? 'start' : 'end');
      return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}" class="radar-lbl">${esc(k)}</text>` +
        `<text x="${x.toFixed(1)}" y="${(y + 11).toFixed(1)}" text-anchor="${anchor}" class="radar-val">${Math.round(dims[k])}</text>`;
    }).join('');
    const dots = keys.map((k, i) => { const [x, y] = polar(cx, cy, R * (dims[k] / 100), i * 360 / n); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" fill="var(--accent)"/>`; }).join('');
    return `<svg viewBox="0 0 260 250" class="radar-svg" aria-hidden="true">${grid}${spokes}` +
      `<polygon points="${pts}" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="1.5" class="radar-poly"/>${dots}${labels}</svg>`;
  }

  // Line chart points [{x,y}] over [w,h], y normalized to [min,max]
  function lineChart(values, opts) {
    opts = opts || {}; const w = opts.w || 480, h = opts.h || 90, pad = 6;
    const ys = values.map((v) => v.y), max = Math.max(...ys), min = opts.min != null ? opts.min : Math.min(...ys);
    const span = Math.max(0.0001, max - min);
    const X = (i) => pad + i * (w - 2 * pad) / Math.max(1, values.length - 1);
    const Y = (v) => h - pad - (v - min) / span * (h - 2 * pad);
    const d = values.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v.y).toFixed(1)}`).join(' ');
    const area = `M${X(0).toFixed(1)} ${(h - pad).toFixed(1)} ` + values.map((v, i) => `L${X(i).toFixed(1)} ${Y(v.y).toFixed(1)}`).join(' ') + ` L${X(values.length - 1).toFixed(1)} ${(h - pad).toFixed(1)} Z`;
    const col = opts.color || 'var(--accent)';
    const dots = opts.dots ? values.map((v, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(v.y).toFixed(1)}" r="2" fill="${col}"><title>${esc(v.label || '')} · ${esc(v.y)}</title></circle>`).join('') : '';
    return `<svg viewBox="0 0 ${w} ${h}" class="line-svg" preserveAspectRatio="none" aria-hidden="true">` +
      `<path d="${area}" fill="${col}" opacity="0.08"/><path d="${d}" fill="none" stroke="${col}" stroke-width="1.6"/>${dots}</svg>`;
  }

  // Column chart values [{label,value}]
  function columns(values, opts) {
    opts = opts || {}; const max = Math.max(1, ...values.map((v) => v.value));
    return `<div class="cols">` + values.map((v) =>
      `<div class="col-item"><div class="col-bar-wrap"><span class="col-bar" style="height:${(v.value / max * 100).toFixed(1)}%;background:${opts.color || 'var(--accent)'}"><span class="col-tip">${esc(v.label)} · ${v.value}</span></span></div></div>`
    ).join('') + `</div>`;
  }

  /* ── State / load ── */
  let OBS = null, META = null, ACT = null, INS = null, filter = null;
  const NEIGH = {};

  function getJSON(path) { return fetch(path, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null); }

  Promise.all([
    getJSON('data/observatory.json'),
    getJSON('data/meta.json'),
    getJSON('data/activity.json'),
    getJSON('data/insights.json'),
  ]).then(([obs, meta, act, ins]) => {
    OBS = obs; META = meta; ACT = act; INS = ins;
    if (!obs) { const e = $('obs-empty'); if (e) e.style.display = 'block'; const b = $('obs-badge'); if (b) b.innerHTML = '<span class="dot" style="background:var(--ink-muted)"></span>AUCUNE DONNÉE'; }
    try { render(); } catch (err) { console.error('[observatory] render error', err); }
    requestAnimationFrame(() => document.querySelectorAll('.reveal:not(.visible)').forEach((el) => {
      const r = el.getBoundingClientRect(); if (r.top < innerHeight * 1.4) el.classList.add('visible');
    }));
  });

  function render() {
    renderHero();
    if (OBS) { renderKPIs(); renderPerf(); renderLLM(); renderConstellation(); renderRoster(); renderEvents(); renderChains(); renderTraceLog(); }
    else { ['#band-runtime'].forEach(hide); }
    renderGovernance();
    renderProject();
  }

  /* ── HERO badge ── */
  function renderHero() {
    const b = $('obs-badge'); if (b && OBS) {
      const live = !OBS.is_demo;
      b.innerHTML = `<span class="dot" style="background:${live ? 'var(--data-green)' : 'var(--accent)'};${live ? 'box-shadow:0 0 6px var(--data-green)' : ''}"></span>` +
        (live ? 'LIVE · observatory.py' : 'SNAPSHOT DÉMO') + ` · ${(OBS.traces || []).length} traces`;
    }
    const v = $('obs-version'); if (v && META) v.textContent = 'grimoire-kit v' + META.version;
  }

  /* ── BAND A : KPIs ── */
  function renderKPIs() {
    const traces = OBS.traces || [], m = OBS.metrics || {};
    const ids = OBS.agent_ids || (OBS.agents || []).map((a) => a.id);
    const tiles = [
      { l: 'TRACES', v: num(traces.length) },
      { l: 'AGENTS', v: num(ids.length) },
      { l: 'SESSIONS', v: num((OBS.sessions || []).length) },
      { l: 'TYPES', v: num((OBS.event_types || []).length) },
      { l: 'SPANS', v: num(m.span_count) },
      { l: 'COÛT', v: usd(m.total_cost_usd) },
    ];
    $('kpis').innerHTML = tiles.map((t) =>
      `<div class="kpi"><div class="kpi-lbl">${t.l}</div><div class="kpi-val">${t.v}</div></div>`).join('');
  }

  /* ── Performance ── */
  function renderPerf() {
    const m = OBS.metrics || {}, p = OBS.perf || {};
    const lat = [
      { label: 'p50', value: m.p50_latency_ms || p.p50_ms, color: 'var(--data-green)' },
      { label: 'p95', value: m.p95_latency_ms || p.p95_ms, color: 'var(--data-amber)' },
      { label: 'p99', value: m.p99_latency_ms || p.p99_ms, color: 'var(--data-red)' },
    ];
    $('perf-latency').innerHTML = bars(lat, { disp: (v) => ms(v) });
    const stats = [
      ['TOKENS', num(m.total_tokens)], ['IN / OUT', num(m.total_input_tokens) + ' / ' + num(m.total_output_tokens)],
      ['DÉBIT', (m.throughput_per_min || 0).toFixed(1) + '/min'], ['COÛT / TRACE', usd(m.avg_cost_per_trace)],
      ['TOKENS / SPAN', num(m.avg_tokens_per_span)], ['SPANS / TRACE', (m.avg_spans_per_trace || 0).toFixed(1)],
    ];
    $('perf-stats').innerHTML = stats.map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val">${v}</span></div>`).join('');
    // SLO / error budget
    const er = m.error_rate || 0, rr = m.retry_rate || 0, target = 0.05;
    $('perf-slo').innerHTML =
      sloRow('TAUX D\'ERREUR', er, target) + sloRow('TAUX DE RETRY', rr, 0.15);
    // slowest
    $('perf-slowest').innerHTML = (p.slowest || []).map((s) =>
      `<div class="slow-row"><span class="slow-bar" style="width:${(s.duration_ms / (p.p99_ms || 5090) * 100).toFixed(0)}%;background:${s.status === 'error' ? 'var(--data-red)' : 'var(--data-cyan)'}"></span>` +
      `<span class="slow-lbl">${esc(s.label)}</span><span class="slow-meta">${esc(s.agent)} · <span style="color:${modelColor(s.model)}">${esc(s.model)}</span></span>` +
      `<span class="slow-dur">${ms(s.duration_ms)}</span><span class="slow-cost">${usd(s.cost_usd)}</span></div>`).join('');
  }
  function sloRow(label, val, target) {
    const ok = val <= target, w = Math.min(100, val / (target * 2) * 100);
    return `<div class="slo-row"><div class="slo-top"><span class="slo-lbl">${label}</span>` +
      `<span class="slo-val" style="color:${ok ? 'var(--data-green)' : 'var(--data-red)'}">${pct(val * 100, 1)} <span class="slo-tgt">/ cible ${pct(target * 100, 0)}</span></span></div>` +
      `<div class="slo-track"><span class="slo-fill" style="width:${w}%;background:${ok ? 'var(--data-green)' : 'var(--data-red)'}"></span>` +
      `<span class="slo-mark" style="left:50%"></span></div></div>`;
  }

  /* ── Multi-LLM ── */
  function renderLLM() {
    const m = OBS.metrics || {};
    const cbm = m.cost_by_model || {}, tbm = m.by_model || {}, cbp = m.cost_by_provider || {}, tbp = m.by_provider || {};
    if (!Object.keys(cbm).length) { hide('#card-llm'); return; }
    const modelSegs = Object.entries(cbm).map(([k, v]) => ({ label: k, value: v, color: modelColor(k) }));
    $('llm-donut').innerHTML = donut(modelSegs, { center: usd(m.total_cost_usd), sub: 'COÛT TOTAL' });
    $('llm-legend').innerHTML = legend(modelSegs, (s) => usd(s.value) + ' · ' + num(tbm[s.label] || 0) + ' tk');
    // provider concentration (vendor risk)
    const provSegs = Object.entries(tbp).map(([k, v]) => ({ label: k, value: v, color: provColor(k) }));
    const ptot = Math.max(1, sum(provSegs, (s) => s.value));
    const top = provSegs.slice().sort((a, b) => b.value - a.value)[0];
    $('llm-prov').innerHTML = bars(provSegs.map((s) => ({ ...s, value: s.value })), { disp: (v) => ((v / ptot) * 100).toFixed(0) + '%' });
    $('llm-prov-note').innerHTML = top ? `Concentration max <strong style="color:${provColor(top.label)}">${esc(top.label)}</strong> · ${((top.value / ptot) * 100).toFixed(0)}% des tokens · ${Object.keys(tbp).length} fournisseurs` : '';
  }

  /* ── Constellation (SVG graph) ── */
  function renderConstellation() {
    const agents = OBS.agents || [], rels = OBS.relationships || [];
    const ids = OBS.agent_ids || agents.map((a) => a.id);
    const byAgent = countBy(OBS.traces, 'agent');
    rels.forEach((r) => { (NEIGH[r.from_agent] = NEIGH[r.from_agent] || new Set()).add(r.to_agent); (NEIGH[r.to_agent] = NEIGH[r.to_agent] || new Set()).add(r.from_agent); });
    const svg = $('graph'); const W = 640, H = 460, cx = W / 2, cy = H / 2 + 6, rx = 250, ry = 168;
    const trOf = (id) => (byAgent[id] || (agents.find((a) => a.id === id) || {}).metrics?.traces || 0);
    const maxTr = Math.max(1, ...ids.map(trOf)); const POS = {};
    ids.forEach((id, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / ids.length; POS[id] = { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a), r: 13 + trOf(id) / maxTr * 15 }; });
    let edges = '', nodes = '';
    const maxStr = Math.max(0.0001, ...rels.map((r) => r.strength || 0));
    rels.forEach((r) => {
      const a = POS[r.from_agent], b = POS[r.to_agent]; if (!a || !b) return;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, qx = mx + (cx - mx) * 0.35, qy = my + (cy - my) * 0.35;
      const w = 1 + (r.strength || 0) / maxStr * 3.4;
      edges += `<path class="edge" data-a="${esc(r.from_agent)}" data-b="${esc(r.to_agent)}" d="M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${qx.toFixed(1)},${qy.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}" style="stroke:${relColor(r.type)};stroke-width:${w.toFixed(2)}"><title>${esc(r.from_agent)} → ${esc(r.to_agent)} · ${esc(r.type)} · ${r.interactions || 0}× · trust ${(r.avg_trust || 0).toFixed(2)}</title></path>`;
    });
    ids.forEach((id) => {
      const p = POS[id];
      nodes += `<g class="gnode" data-id="${esc(id)}" tabindex="0" role="button" aria-label="${esc(id)}">` +
        `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.toFixed(1)}" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="1.4"/>` +
        `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="var(--accent)"/>` +
        `<text x="${p.x.toFixed(1)}" y="${(p.y + p.r + 14).toFixed(1)}" text-anchor="middle">${esc(id)}</text></g>`;
    });
    svg.innerHTML = edges + nodes;
    svg.querySelectorAll('.gnode').forEach((g) => {
      const id = g.dataset.id;
      g.addEventListener('mouseenter', () => focus(id)); g.addEventListener('mouseleave', blur);
      g.addEventListener('focus', () => focus(id)); g.addEventListener('blur', blur);
    });
    $('graph-sub').textContent = `${ids.length} agents · ${rels.length} relations`;
    // relation legend
    const used = [...new Set(rels.map((r) => r.type))];
    $('graph-legend').innerHTML = used.map((t) => `<span class="leg-inline"><span class="leg-dot" style="background:${relColor(t)}"></span>${esc(t)}</span>`).join('');
    // graph stats
    const gs = OBS.graph_stats;
    if (gs) $('graph-stats').innerHTML =
      `<span>nœuds <b>${gs.nodes}</b></span><span>arêtes <b>${gs.edges}</b></span>` +
      `<span>densité <b>${(gs.density).toFixed(2)}</b></span><span>degré moy <b>${(gs.avg_degree).toFixed(2)}</b></span>` +
      `<span>plus central <b style="color:var(--accent)">${esc(gs.most_central.agent)}</b> · ${gs.most_central.degree}</span>`;
  }
  function focus(id) {
    const nb = NEIGH[id] || new Set();
    const g = $('graph'); g.classList.add('focus');
    g.querySelectorAll('.gnode').forEach((el) => { el.classList.toggle('src', el.dataset.id === id); el.classList.toggle('lit', nb.has(el.dataset.id)); });
    g.querySelectorAll('.edge').forEach((p) => p.classList.toggle('lit', p.dataset.a === id || p.dataset.b === id));
    const r = $('roster'); if (r) { r.classList.add('focus'); r.querySelectorAll('.ag-card').forEach((el) => { el.classList.toggle('src', el.dataset.id === id); el.classList.toggle('lit', nb.has(el.dataset.id)); }); }
  }
  function blur() {
    const g = $('graph'); g.classList.remove('focus');
    g.querySelectorAll('.gnode').forEach((el) => el.classList.remove('src', 'lit'));
    g.querySelectorAll('.edge').forEach((p) => p.classList.remove('lit'));
    const r = $('roster'); if (r) { r.classList.remove('focus'); r.querySelectorAll('.ag-card').forEach((el) => el.classList.remove('src', 'lit')); }
  }

  function renderRoster() {
    const agents = OBS.agents || [], byAgent = countBy(OBS.traces, 'agent');
    const ids = (OBS.agent_ids || agents.map((a) => a.id)).slice().sort((a, b) => (byAgent[b] || 0) - (byAgent[a] || 0));
    $('roster').innerHTML = ids.map((id) => {
      const a = agents.find((x) => x.id === id) || {};
      const caps = (a.capabilities || []).map((c) => `<span class="ag-cap">${esc(c)}</span>`).join('');
      const tr = byAgent[id] || (a.metrics && a.metrics.traces) || 0;
      return `<div class="ag-card" data-id="${esc(id)}" tabindex="0"><div class="ag-top"><span class="ag-id">${esc(id)}</span><span class="ag-tr">${tr} tr</span></div>` +
        `<div class="ag-persona">${esc(a.persona || '—')}</div><div class="ag-caps">${caps}</div></div>`;
    }).join('');
    $('roster-sub').textContent = ids.length + ' agents · nœud ∝ volume de traces';
    $('roster').querySelectorAll('.ag-card').forEach((el) => {
      el.addEventListener('mouseenter', () => focus(el.dataset.id)); el.addEventListener('mouseleave', blur);
      el.addEventListener('focus', () => focus(el.dataset.id)); el.addEventListener('blur', blur);
    });
  }

  function renderEvents() {
    const traces = OBS.traces || [];
    const byType = countBy(traces, 'event_type');
    $('evt-bars').innerHTML = bars(Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v, color: evColor(k) })));
    $('evt-total').textContent = Object.keys(byType).length + ' types';
    $('sessions').innerHTML = bars((OBS.sessions || []).map((s) => ({ label: s, value: traces.filter((t) => t.session === s).length, color: 'var(--accent-dim)' })));
    $('sess-total').textContent = (OBS.sessions || []).length + ' sessions';
  }

  /* ── Chaînes causales (waterfall de spans par trace_id) ── */
  function renderChains() {
    const spans = OBS.spans || [];
    if (!spans.length) { hide('#card-chains'); return; }
    const traces = [...new Set(spans.map((s) => s.trace_id))];
    const sessOf = {}; // best-effort label
    $('chains').innerHTML = traces.map((tid) => {
      const ss = spans.filter((s) => s.trace_id === tid);
      const root = ss.find((s) => !s.parent_span_id) || ss[0];
      const rootDur = Math.max(1, ...ss.map((s) => s.duration_ms));
      const totalCost = sum(ss, (s) => s.cost_usd || 0), totalTok = sum(ss, (s) => s.tokens || 0);
      // depth map
      const depth = {}; const byId = {}; ss.forEach((s) => (byId[s.span_id] = s));
      const d = (s) => { if (depth[s.span_id] != null) return depth[s.span_id]; depth[s.span_id] = s.parent_span_id && byId[s.parent_span_id] ? d(byId[s.parent_span_id]) + 1 : 0; return depth[s.span_id]; };
      // order: dfs from root
      const ordered = []; const visit = (s) => { ordered.push(s); ss.filter((c) => c.parent_span_id === s.span_id).forEach(visit); };
      ss.filter((s) => !s.parent_span_id).forEach(visit); ss.forEach((s) => { if (!ordered.includes(s)) ordered.push(s); });
      const rows = ordered.map((s) => {
        const w = Math.max(2, s.duration_ms / rootDur * 100);
        const col = TOOL_CAT[s.tool] || 'var(--ink-soft)';
        const err = s.status === 'error';
        return `<div class="wf-row"><span class="wf-lbl" style="padding-left:${d(s) * 16}px" title="${esc(s.tool)} · ${esc(s.operation)}">` +
          `<span class="wf-tool">${esc(s.tool)}</span> · ${esc(s.operation)}</span>` +
          `<span class="wf-agent">${esc(s.agent)}</span>` +
          `<span class="wf-track"><span class="wf-bar${err ? ' err' : ''}" style="width:${w.toFixed(1)}%;background:${err ? 'var(--data-red)' : col}">` +
          `${s.retries ? `<span class="wf-retry">↻${s.retries}</span>` : ''}</span></span>` +
          `<span class="wf-model"${s.model ? ` style="color:${modelColor(s.model)}"` : ''}>${esc(s.model || '—')}</span>` +
          `<span class="wf-dur">${ms(s.duration_ms)}</span></div>`;
      }).join('');
      const errBadge = ss.some((s) => s.status === 'error') ? `<span class="chain-err">ERROR</span>` : '';
      return `<div class="chain"><div class="chain-head"><span class="chain-id">trace ${esc(tid)}</span>${errBadge}` +
        `<span class="chain-meta">${ss.length} spans · ${ms(rootDur)} · ${num(totalTok)} tk · ${usd(totalCost)}</span></div>${rows}</div>`;
    }).join('');
    $('chains-sub').textContent = `${traces.length} chaînes · ${spans.length} spans · largeur ∝ durée`;
  }

  /* ── Trace log filtrable ── */
  function renderTraceLog() {
    const byType = countBy(OBS.traces, 'event_type');
    $('tl-filters').innerHTML = `<span class="filt active" data-f="">TOUT</span>` +
      Object.keys(byType).map((t) => `<span class="filt" data-f="${esc(t)}" style="border-color:${evColor(t)}">${esc(t)}</span>`).join('');
    $('tl-filters').querySelectorAll('.filt').forEach((el) => el.addEventListener('click', () => {
      filter = el.dataset.f || null;
      $('tl-filters').querySelectorAll('.filt').forEach((x) => x.classList.toggle('active', x === el));
      drawLog();
    }));
    drawLog();
  }
  function drawLog() {
    const rows = (OBS.traces || []).filter((t) => !filter || t.event_type === filter).slice().reverse();
    $('tl-count').textContent = rows.length + ' entrées';
    $('tl-body').innerHTML = rows.map((t) => {
      const hh = (t.timestamp || '').replace('T', ' ').replace(/(\+\d\d:\d\d|Z)$/, '').slice(5, 16);
      return `<tr><td>${esc(hh)}</td><td>${esc(t.agent)}</td><td><span class="ev-pill" style="color:${evColor(t.event_type)};border-color:${evColor(t.event_type)}">${esc(t.event_type)}</span></td><td style="color:var(--ink-muted)">${esc(t.session)}</td></tr>`;
    }).join('') || '<tr><td colspan="4" style="color:var(--ink-muted)">—</td></tr>';
  }

  /* ════════════ BAND B : GOUVERNANCE ════════════ */
  function renderGovernance() {
    if (!INS) { hide('#band-gov'); return; }
    renderAntifragile(); renderMemory(); renderBench(); renderRouting();
  }

  function renderAntifragile() {
    const af = INS.governance && INS.governance.antifragile;
    if (!af) { hide('#card-af'); return; }
    $('af-gauge').innerHTML = gauge(af.score, 100, { label: af.score, sub: '/ 100', color: 'var(--accent)' });
    $('af-level').textContent = af.level;
    $('af-evidence').textContent = af.evidence + ' preuves';
    $('af-summary').textContent = af.summary || '';
    $('af-radar').innerHTML = radar(af.dimensions);
  }

  function renderMemory() {
    const mem = INS.memory; if (!mem) { hide('#card-mem'); return; }
    const tiles = [
      ['CONTRADICTIONS', mem.contradictions, mem.contradictions > 0 ? 'var(--data-amber)' : 'var(--ink)'],
      ['ÉCHECS CAPTURÉS', mem.failures, 'var(--data-cyan)'],
      ['DÉCISIONS', mem.decisions, 'var(--ink)'],
      ['FICHIERS LEARNINGS', mem.learnings_files, 'var(--ink)'],
    ];
    $('mem-stats').innerHTML = tiles.map(([l, v, c]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val" style="color:${c}">${num(v)}</span></div>`).join('');
    $('mem-backends').innerHTML = (mem.backends || []).map((b) => `<span class="chip">${esc(b)}</span>`).join('') || '<span class="ms-lbl">aucun backend</span>';
  }

  function renderBench() {
    const b = INS.bench; if (!b || !(b.latest || []).length) { hide('#card-bench'); return; }
    const trend = (b.trend || []).map((p) => ({ y: p.score, label: p.date }));
    if (trend.length) { $('bench-trend').innerHTML = lineChart(trend, { color: 'var(--data-cyan)', dots: true, w: 460, h: 84, min: 60 }); }
    const last = trend.length ? trend[trend.length - 1].y : '—', first = trend.length ? trend[0].y : 0;
    const delta = trend.length ? (last - first) : 0;
    $('bench-headline').innerHTML = `<span class="bh-num">${last}</span><span class="bh-delta ${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)}</span>`;
    $('bench-sub').textContent = `${b.report_count} rapports · au ${b.as_of}`;
    $('bench-latest').innerHTML = (b.latest || []).sort((a, c) => c.score - a.score).map((r) =>
      `<div class="bench-row"><span class="bench-ag">${esc(r.agent)}</span>` +
      `<span class="bar-track"><span class="bar-fill" style="width:${r.score}%;background:var(--data-cyan)"></span></span>` +
      `<span class="bench-score">${r.score}</span><span class="bench-ac">AC ${(r.ac_pass * 100).toFixed(0)}%</span>` +
      `<span class="bench-fail" style="color:${r.failures ? 'var(--data-amber)' : 'var(--ink-muted)'}">${r.failures} éch.</span></div>`).join('');
  }

  function renderRouting() {
    const r = INS.routing; if (!r) { hide('#card-routing'); return; }
    const comp = r.by_complexity || {};
    const segs = [
      { label: 'low', value: comp.low || 0, color: 'var(--data-green)' },
      { label: 'medium', value: comp.medium || 0, color: 'var(--data-amber)' },
      { label: 'high', value: comp.high || 0, color: 'var(--data-red)' },
    ].filter((s) => s.value > 0);
    $('routing-donut').innerHTML = donut(segs, { center: num(r.samples), sub: 'ÉCHANTILLONS' });
    $('routing-legend').innerHTML = legend(segs, (s) => s.value);
    const models = Object.entries(r.by_model || {}).map(([k, v]) => `<span class="chip" style="border-color:${modelColor(k)};color:var(--ink-soft)">${esc(k)} · ${v}</span>`).join('');
    const tasks = Object.entries(r.by_task_type || {}).map(([k, v]) => `<span class="chip">${esc(k)} · ${v}</span>`).join('');
    $('routing-meta').innerHTML = `<div class="rt-line"><span class="ms-lbl">MODÈLES</span>${models}</div>` +
      `<div class="rt-line"><span class="ms-lbl">TÂCHES</span>${tasks}</div>` +
      `<div class="rt-line"><span class="ms-lbl">COÛT EST.</span><span class="chip">${usd(r.est_cost_total)}</span></div>`;
  }

  /* ════════════ BAND C : PROJET & ÉCONOMIE ════════════ */
  function renderProject() {
    if (!ACT) { hide('#band-proj'); return; }
    renderRTK(); renderActivity(); renderDelivery(); renderTracked(); renderCode(); renderSignals(); renderPrivate();
  }

  function renderRTK() {
    const rtk = ACT.economy && ACT.economy.rtk; if (!rtk) { hide('#card-rtk'); return; }
    $('rtk-pct').textContent = (rtk.savings_pct).toFixed(1) + '%';
    $('rtk-saved').textContent = num(rtk.saved_tokens);
    $('rtk-cmds').textContent = num(rtk.total_commands);
    const monthly = (rtk.monthly || []).map((m) => ({ label: m.month.slice(5), value: m.saved_tokens }));
    $('rtk-trend').innerHTML = columns(monthly, { color: 'var(--accent)' });
    $('rtk-trend-lbls').innerHTML = monthly.map((m) => `<span>${m.label}</span>`).join('');
  }

  function renderActivity() {
    const g = ACT.git; if (!g) { hide('#card-activity'); return; }
    $('act-stats').innerHTML = [
      ['COMMITS', num(g.commits_total)], ['7 JOURS', '+' + num(g.commits_7d)],
      ['30 JOURS', '+' + num(g.commits_30d)], ['/ JOUR (30j)', (g.avg_per_day_30d).toFixed(1)],
    ].map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val">${v}</span></div>`).join('');
    $('commits-chart').innerHTML = columns((g.per_day || []).map((d) => ({ label: d.date.slice(5), value: d.count })), { color: 'var(--accent-dim)' });
    const maxC = Math.max(1, ...(g.contributors || []).map((c) => c.commits));
    $('contributors').innerHTML = bars((g.contributors || []).map((c) => ({ label: c.name, value: c.commits, color: 'var(--data-violet)' })), { disp: (v) => num(v) });
    $('pulls').innerHTML = (ACT.pulls || []).map((p) =>
      `<a class="pr-row" href="${esc(p.url)}" target="_blank" rel="noopener"><span class="pr-num">#${p.number}</span>` +
      `<span class="pr-title">${esc(p.title)}</span><span class="pr-state ${esc(p.state)}">${esc(p.state)}</span></a>`).join('') || '<div class="ms-lbl">aucune PR ouverte</div>';
    $('pulls-sub').textContent = (ACT.pulls_open || 0) + ' ouvertes';
    $('releases').innerHTML = (ACT.releases || []).map((r, i) =>
      `<div class="rel-row${i === 0 ? ' latest' : ''}"><span class="rel-tag">${esc(r.tag)}</span><span class="rel-date">${esc(r.date)}</span></div>`).join('');
    const repo = ACT.repo || {};
    $('repo-meta').innerHTML = `<span class="chip">★ ${repo.stars} stars</span><span class="chip">⑂ ${repo.forks} forks</span>`;
  }

  function renderDelivery() {
    const d = ACT.delivery; if (!d) { hide('#card-dora'); return; }
    const tiles = [
      ['DÉPLOIS / 7J', num(d.deploy_freq_7d), 'var(--data-green)'],
      ['ÉCHEC CHANGEMENT', pct(d.change_failure_rate * 100, 0), d.change_failure_rate === 0 ? 'var(--data-green)' : 'var(--data-amber)'],
      ['CI MOYENNE', (d.ci_avg_duration_s).toFixed(1) + ' s', 'var(--ink)'],
      ['LEAD TIME PR (méd.)', (d.pr_lead_time_median_h).toFixed(0) + ' h', 'var(--ink)'],
    ];
    $('dora-stats').innerHTML = tiles.map(([l, v, c]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val" style="color:${c}">${v}</span></div>`).join('');
    $('dora-sub').textContent = 'sur ' + d.pr_merged_sample + ' PR fusionnées';
  }

  function renderTracked() {
    const tr = ACT.tracking; if (!tr) { hide('#card-tracked'); return; }
    $('ci-checks').innerHTML = (tr.ci || []).map((c) => {
      const ok = c.conclusion === 'success';
      return `<div class="ci-row"><span class="ci-dot" style="background:${ok ? 'var(--data-green)' : 'var(--data-red)'}"></span>` +
        `<span class="ci-name">${esc(c.name)}</span><span class="ci-concl" style="color:${ok ? 'var(--data-green)' : 'var(--data-red)'}">${esc(c.conclusion)}</span></div>`;
    }).join('');
    const cov = tr.coverage ? tr.coverage.percent : null;
    if (cov != null) $('coverage-gauge').innerHTML = gauge(cov, 100, { label: cov.toFixed(1) + '%', sub: 'COUVERTURE', color: cov >= 80 ? 'var(--data-green)' : 'var(--data-amber)' });
    const py = tr.pypi || {};
    $('pypi-stats').innerHTML = [
      ['JOUR', num(py.last_day)], ['SEMAINE', num(py.last_week)], ['MOIS', num(py.last_month)],
    ].map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val">${v}</span></div>`).join('');
  }

  function renderCode() {
    const c = INS && INS.code, eff = INS && INS.efficiency; if (!c) { hide('#card-code'); return; }
    $('code-stats').innerHTML = [
      ['LIGNES DE CODE', num(c.loc)], ['RATIO TESTS/CODE', (c.tests_code_ratio).toFixed(2)],
      ['TESTS / KLOC', eff ? (eff.tests_per_kloc).toFixed(1) : '—'], ['TAGS', num(c.tags_total)],
      ['RELEASES / 30J', num(c.releases_30d)], ['ISSUES OUVERTES', num(c.issues_open)],
    ].map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val">${v}</span></div>`).join('');
    $('churn').innerHTML = bars((c.churn_top || []).map((f) => ({ label: f.file.split('/').pop(), value: f.changes, color: 'var(--data-amber)' })), { disp: (v) => v + '×' });
  }

  function renderSignals() {
    const f = INS && INS.freshness, eff = INS && INS.efficiency, cp = ACT && ACT.context_pressure;
    if (!f && !cp) { hide('#card-signals'); return; }
    const fresh = f ? [
      ['DERNIER COMMIT', f.days_since_commit + ' j'], ['DERNIÈRE RELEASE', f.days_since_release + ' j'], ['DERNIER BENCH', f.days_since_bench + ' j'],
    ] : [];
    $('fresh-stats').innerHTML = fresh.map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val" style="color:var(--data-green)">${v}</span></div>`).join('');
    if (cp) {
      $('cp-chart').innerHTML = lineChart((cp.by_day || []).map((d) => ({ y: d.peak_pct * 100, label: d.date })), { color: 'var(--data-violet)', dots: true, w: 380, h: 70, min: 0 });
      $('cp-peak').textContent = (cp.peak_pct * 100).toFixed(2) + '%';
      $('cp-sub').textContent = 'pic · fenêtre ' + num(cp.window) + ' tk';
    } else hide('#cp-block');
    if (eff) $('eff-stats').innerHTML = [
      ['COMMITS / RELEASE', (eff.commits_per_release).toFixed(1)], ['RTK / COMMANDE', num(eff.rtk_saved_per_command) + ' tk'],
    ].map(([l, v]) => `<div class="ministat"><span class="ms-lbl">${l}</span><span class="ms-val">${v}</span></div>`).join('');
  }

  // PRIVÉ — ccusage : jamais publié sur la vitrine. Honnêteté des données.
  function renderPrivate() {
    const cc = ACT.economy && ACT.economy.ccusage;
    const hasData = cc && Object.keys(cc).length;
    if (hasData) {
      // En local opt-in : on rendrait les coûts réels ici.
      hide('#card-private-locked');
      // (rendu live laissé au câblage local)
    } else {
      hide('#card-private-live');
      // garde le panneau "privé" verrouillé visible : il EST l'argument gouvernance.
    }
  }

})();
