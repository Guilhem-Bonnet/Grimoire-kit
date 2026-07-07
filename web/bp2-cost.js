/* bp2-cost.js — Estimation de coût en tokens (statique, jamais une facture)
   Modèle : par node = (contexte consommé + documents injectés) × itérations + sorties.
   Par chemin = somme des nodes traversés (source → puits).
   Les sous-flows comptent la somme de leur intérieur.
   ========================================================================== */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);

  /* k-tokens par run : { in, out, runs } — hypothèses moyennes par pattern */
  const NODE_COST = {
    'PRD-01': { in: 2.4, out: 1.2, runs: 1 },
    'ORC-01': { in: 3.2, out: 0.9, runs: 1 },
    'ORC-02': { in: 4.5, out: 2.4, runs: 3 },
    'ORC-03': { in: 5.0, out: 2.2, runs: 2 },
    'ENG-01': { in: 14.0, out: 6.5, runs: 3 },
    'ENG-02': { in: 12.0, out: 5.0, runs: 2 },
    'QUA-01': { in: 6.0, out: 2.8, runs: 1 },
    'QUA-02': { in: 9.0, out: 1.8, runs: 1 },
    'QUA-03': { in: 7.0, out: 1.4, runs: 1 },
    'GOV-01': { in: 3.5, out: 0.6, runs: 1 },
    'GOV-02': { in: 1.8, out: 0.4, runs: 1 },
    'GOV-03': { in: 6.0, out: 1.1, runs: 1 },
    'GOV-04': { in: 8.0, out: 2.2, runs: 1 },
    'MEM-01': { in: 2.2, out: 1.1, runs: 1 },
    'MEM-02': { in: 5.0, out: 1.6, runs: 1 },
    'SEC-01': { in: 7.5, out: 2.4, runs: 1 },
    'SEC-02': { in: 3.0, out: 0.5, runs: 1 },
    'OPS-01': { in: 2.5, out: 0.6, runs: 1 },
    'DAT-01': { in: 8.0, out: 3.0, runs: 2 }
  };
  const EXT_DEFAULT = { in: 18.0, out: 7.0, runs: 1 };   // un crew/graph externe
  const CAT_DEFAULT = { in: 5.0, out: 2.0, runs: 1 };
  /* agents concrets : coût par rôle, modèle propre à l'agent */
  const ROLE_COST = { orchestrateur: { in: 4.0, out: 1.5, runs: 1 }, agent: { in: 10.0, out: 4.5, runs: 2 }, sub: { in: 5.5, out: 2.2, runs: 1 } };

  /* $ / MTok (entrée / sortie) — ordres de grandeur, à titre indicatif */
  const MODELS = {
    haiku:  { name: 'haiku',  in: 0.8,  out: 4 },
    sonnet: { name: 'sonnet', in: 3,    out: 15 },
    opus:   { name: 'opus',   in: 15,   out: 75 }
  };
  /* budget mission (k tokens) par profil projet */
  const CAPS = { starter: 80, controlled: 200, orchestrated: 500, governed: 1200, production: 2500 };

  let core = null;
  let model = localStorage.getItem('grimoire.atelier.bp2.model') || 'sonnet';
  const rateOf = m => (window.BP2Team && BP2Team.MODEL_RATES[m]) || MODELS[m] || MODELS.sonnet;

  /* ── primitives ── */
  function baseOf(node, specOf) {
    if (node.kind === 'group') return null;
    if (NODE_COST[node.ref]) return NODE_COST[node.ref];
    const s = specOf(node);
    if (s && s.kind === 'ext') return EXT_DEFAULT;
    return CAT_DEFAULT;
  }
  function usdOf(inK, outK, m) {
    const r = rateOf(m || model);
    return (inK * r.in + outK * r.out) / 1000;
  }
  function nodeParts(node, specOf) {
    if (node.kind === 'group') {
      const acc = { inK: 0, outK: 0, usd: 0 };
      (node.sub && node.sub.nodes || []).forEach(sn => {
        const p = nodeParts(sn, specOf);
        acc.inK += p.inK; acc.outK += p.outK; acc.usd += p.usd;
      });
      return acc;
    }
    let b, eqK = 0, m = null;
    if (node.kind === 'trigger') {
      b = { in: 0.1, out: 0.4, runs: 1 };
    } else if (node.kind === 'agent') {
      b = node.role === 'orchestrateur' ? ROLE_COST.orchestrateur : (node.sub ? ROLE_COST.sub : ROLE_COST.agent);
      eqK = window.BP2Team ? BP2Team.promptOverheadK(node) : 0;
      m = node.model || null;
    } else {
      b = baseOf(node, specOf);
    }
    const docK = (window.BP2Docs ? BP2Docs.docTokens(node) : 0) / 1000;
    const inK = (b.in + docK + eqK) * b.runs, outK = b.out * b.runs;
    return { inK, outK, runs: b.runs, docK, eqK, model: m, usd: usdOf(inK, outK, m) };
  }
  const nodeK = (node, specOf) => { const p = nodeParts(node, specOf); return p.inK + p.outK; };
  const flowK = (node, specOf) => nodeParts(node, specOf).outK;

  function totals(graph, specOf) {
    let inK = 0, outK = 0, usd = 0;
    (graph.nodes || []).forEach(n => { const p = nodeParts(n, specOf); inK += p.inK; outK += p.outK; usd += p.usd; });
    return { inK, outK, k: inK + outK, usd };
  }

  /* ── chemins source → puits (niveau racine, groupes atomiques) ── */
  function paths(graph) {
    const nodes = graph.nodes || [], edges = graph.edges || [];
    const hasIn = id => edges.some(e => e.to === id);
    const outsOf = id => edges.filter(e => e.from === id);
    const sources = nodes.filter(n => !hasIn(n.id));
    const out = [];
    function dfs(id, acc, seen) {
      if (out.length >= 24 || acc.length > 24) return;
      const nexts = outsOf(id);
      if (!nexts.length) { out.push(acc.slice()); return; }
      nexts.forEach(e => {
        if (seen.has(e.to)) return;
        seen.add(e.to); acc.push(e.to);
        dfs(e.to, acc, seen);
        acc.pop(); seen.delete(e.to);
      });
    }
    sources.forEach(s => dfs(s.id, [s.id], new Set([s.id])));
    return out;
  }

  /* ── formats (fr) ── */
  const fmtK = k => {
    if (k >= 1000) return (k / 1000).toFixed(1).replace('.', ',') + 'M';
    return (k >= 100 ? Math.round(k) : k.toFixed(1)).toString().replace('.', ',') + 'k';
  };
  const fmt$ = v => v < 0.005 ? '<0,01 $' : v.toFixed(2).replace('.', ',') + ' $';

  function cap() {
    const p = (Atelier.project() || {}).profile || 'controlled';
    return { profile: p, k: CAPS[p] || 200 };
  }

  /* ── surfaces ── */
  function refresh() {
    if (!core || !core.state()) return;
    const t = totals(core.state(), core.specOf);
    const chip = $('#bp-cost-chip');
    if (chip) {
      const c = cap();
      chip.innerHTML = `Σ <b>${fmtK(t.k)} tok</b> <span class="eur">· ~${fmt$(t.usd)} / run</span>`;
      chip.classList.toggle('over', t.k > c.k);
      chip.title = 'Estimation statique par run complet — budget du profil ' + c.profile + ' : ' + fmtK(c.k) + ' tok. Ouvre l\u2019onglet COÛT.';
    }
    const panel = $('#panel-cout');
    if (panel && panel.classList.contains('on')) renderTab();
  }

  function heatClass(node, graph, specOf) {
    const t = totals(graph, specOf).k || 1;
    const share = nodeK(node, specOf) / t;
    if (share >= 0.34) return ' heat-hot';
    if (share >= 0.16) return ' heat-warm';
    return '';
  }

  function nodeRows(node, specOf) {
    const p = nodeParts(node, specOf);
    const docLine = p.docK ? `<div class="np-cost"><span>documents injectés</span><b>+${fmtK(p.docK * (p.runs || 1))} tok</b></div>` : '';
    const eqLine = p.eqK ? `<div class="np-cost"><span>équipement (outils, MCP, skills)</span><b>+${fmtK(p.eqK * (p.runs || 1))} tok</b></div>` : '';
    return `<div class="bp-prop"><div class="k">Coût estimé / run <span style="text-transform:none;letter-spacing:0;color:var(--accent);cursor:help" title="Estimation statique : contexte consommé × itérations moyennes + sorties. Documents et équipement s'ajoutent au prompt. Jamais une facture.">· comment ? ⓘ</span></div>
      <div class="np-cost"><span>contexte (in)${p.runs > 1 ? ' × ' + p.runs + ' itérations' : ''}</span><b>~${fmtK(p.inK)} tok</b></div>
      <div class="np-cost"><span>sorties (out)</span><b>~${fmtK(p.outK)} tok</b></div>
      ${docLine}${eqLine}
      <div class="np-cost" style="border-top:1px solid var(--line);margin-top:3px;padding-top:6px"><span>total · ~${fmt$(p.usd)} en ${p.model || model}</span><b>~${fmtK(p.inK + p.outK)} tok</b></div>
    </div>`;
  }

  function summaryLine(graph, specOf) {
    const t = totals(graph, specOf);
    if (!t.k) return '';
    return `Coût estimé du run : <b>~${fmtK(t.k)} tok</b> (~${fmt$(t.usd)}) — détail par chemin dans l\u2019onglet COÛT.`;
  }

  function renderTab() {
    if (!core) return;
    const panel = $('#panel-cout');
    if (!panel) return;
    const state = core.state();
    const specOf = core.specOf;
    if (!state.nodes.length) {
      panel.innerHTML = '<p class="empty">posez des nodes — chaque chemin du flow sera estimé en tokens et en dollars, avant toute exécution.</p>';
      return;
    }
    const t = totals(state, specOf);
    const c = cap();
    const pct = Math.min(100, Math.round(t.k / c.k * 100));
    const budgetCls = t.k > c.k ? 'over' : (t.k > c.k * 0.75 ? 'warn' : '');

    const ps = paths(state).map(p => {
      let inK = 0, outK = 0, usd = 0;
      const steps = p.map(id => state.nodes.find(n => n.id === id)).filter(Boolean);
      steps.forEach(n => { const q = nodeParts(n, specOf); inK += q.inK; outK += q.outK; usd += q.usd; });
      return { steps, inK, outK, usd, k: inK + outK };
    }).sort((a, b) => b.k - a.k).slice(0, 8);
    const maxK = ps.length ? ps[0].k : 1;

    const stepChip = n => {
      const s = specOf(n);
      const isG = n.kind === 'group';
      const isA = n.kind === 'agent';
      const col = isG ? '#A78BFA' : (isA ? '#6EE7FF' : Atelier.catColor(s ? s.cat : ''));
      const nm = isG ? '◇ ' + (n.name || 'sous-flow') : (isA ? (n.name || 'agent') : (s ? (s.kind === 'ext' ? s.name : s.ref) : '?'));
      return `<span class="d" style="background:${col}"></span>${Atelier.esc(nm)}`;
    };

    panel.innerHTML = `
      <div class="bp-prop"><div class="k">Modèle par défaut <span style="text-transform:none;letter-spacing:0;color:var(--ink-muted)">· chaque agent garde le sien</span></div>
        <select class="cost-model-sel" id="cost-model-sel">${modelOptions()}</select></div>

      <div class="bp-prop"><div class="k">Run complet — tout le blueprint</div>
        <div class="cost-total"><span class="big">Σ ${fmtK(t.k)} tok</span><span class="eur">~${fmt$(t.usd)} / run</span></div>
        <div class="np-cost" style="margin-top:4px"><span>entrée ${fmtK(t.inK)} · sortie ${fmtK(t.outK)}</span><span></span></div>
        <div class="cost-budget ${budgetCls}">
          <div class="bar"><i style="width:${pct}%"></i></div>
          <div class="cap"><span>budget mission · profil ${c.profile}</span><span>${fmtK(t.k)} / ${fmtK(c.k)} tok${t.k > c.k ? ' — dépassé' : ''}</span></div>
        </div></div>

      <div class="bp-prop"><div class="k">Coût par chemin <span style="text-transform:none;letter-spacing:0;color:var(--ink-muted)">· source → puits</span></div>
        ${ps.map((p, i) => `
          <div class="cost-path${i === 0 && ps.length > 1 ? ' crit' : ''}">
            <div class="seq">${p.steps.map(stepChip).join('<span class="arr">→</span>')}</div>
            <div class="m"><span class="tok">~${fmtK(p.k)} tok</span>${i === 0 && ps.length > 1 ? '<span class="tag">chemin critique</span>' : ''}<span class="eur">~${fmt$(p.usd)}</span></div>
            <div class="bar"><i style="width:${Math.max(6, Math.round(p.k / maxK * 100))}%"></i></div>
          </div>`).join('') || '<p class="empty">reliez vos nodes — les chemins apparaîtront ici.</p>'}
      </div>

      <div class="cost-note">Estimation <b>statique</b> : contexte moyen × itérations typiques + sorties — documents édités et équipement des agents inclus dans le prompt (±35 %). Les agents utilisent <b>leur</b> modèle ; les patterns, le modèle par défaut. Ce n'est jamais une facture — c'est un ordre de grandeur pour arbitrer <i>avant</i> de compiler.</div>`;

    const sel = panel.querySelector('#cost-model-sel');
    if (sel) sel.addEventListener('change', () => {
      model = sel.value;
      localStorage.setItem('grimoire.atelier.bp2.model', model);
      refresh(); renderTab();
      if (core.heat()) core.render();
    });
  }

  function modelOptions() {
    if (window.BP2Team) {
      return BP2Team.PROVIDERS.filter(p => BP2Team.llmConnected(p.id)).map(p =>
        `<optgroup label="${p.name}">` + p.models.map(m =>
          `<option value="${m.id}"${m.id === model ? ' selected' : ''}>${m.label} — ${m.in}/${m.out} $/MTok</option>`).join('') + '</optgroup>'
      ).join('');
    }
    return Object.keys(MODELS).map(m => `<option value="${m}"${m === model ? ' selected' : ''}>${m} — ${MODELS[m].in}/${MODELS[m].out} $/MTok</option>`).join('');
  }

  window.BP2Cost = {
    init(c) {
      core = c;
      const chip = $('#bp-cost-chip');
      if (chip) chip.addEventListener('click', () => core.setTab('cout'));
      refresh();
    },
    refresh, renderTab, nodeK, flowK, heatClass, nodeRows, summaryLine, fmtK,
    totals: g => totals(g, core ? core.specOf : (() => null)),
    model: () => model
  };
})();
