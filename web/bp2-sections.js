/* bp2-sections.js — Les sections déclaratives de l'inspecteur
   Extrait de bp2-core.js. Ce sont des constructeurs d'HTML et leurs câblages :
   ils prennent un node, rendent une section, et écrivent la déclaration dans
   `config`. Aucune n'a besoin de l'état interne du cœur — seulement du
   vocabulaire serveur et d'un moyen de signaler la mutation.

   Porte (P2.1), point de reprise (P3.2), éclatement borné (P4.1) et politique
   de contexte (C1/C3) : tout ce que le Studio sait déclarer sur un node.
   ========================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);

  function whenReady(cb) {
    if (window.BPEditor && window.Atelier) return cb();
    setTimeout(() => whenReady(cb), 60);
  }

  whenReady(() => {
    const E = window.BPEditor;
    const esc = Atelier.esc;
    const vocab = (k, f) => E.vocab(k, f);
    const markDirty = () => E.markDirty();
    const render = () => E.render();
    const CTX_TIERS = E.CTX_TIERS, CTX_STRATEGIES = E.CTX_STRATEGIES,
          CTX_ISOLATIONS = E.CTX_ISOLATIONS;
    const ctxOf = n => E.ctxOf(n);

  /* ══ Déclarer une porte (P2.1) ══
     Le Gate universel et la reprise (P3.2) lintent `config.gate` ; jusqu'ici
     rien dans le Studio ne l'écrivait, donc leurs règles ne pouvaient ni
     s'appliquer ni se corriger depuis l'interface. */
  const gateOf = n => (n.config && n.config.gate) || null;
  const GATE_LABEL = {
    'human': 'humaine — une personne décide',
    'budget': 'budget — plafond de dépense',
    'evidence': 'preuve — exige un evidence-pack',
    'output-contract': 'contrat de sortie — valide un schéma',
    'guardrail': 'garde-fou — filtre entrée ou sortie',
    'mcp-trust': 'confiance MCP — qualifie un service externe'
  };
  const HUMAN_LABEL = {
    'approve': 'approuver',
    'edit': 'corriger la sortie',
    'input': 'fournir ce qui manque',
    'sample': 'échantillonner un % des runs',
    'escalate-on-uncertainty': 'escalader si incertain'
  };
  function gateSectionHtml(n) {
    if (!E.hasVocab()) return '';
    const g = gateOf(n);
    const opt = (list, cur, labels) => list.map(v =>
      `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc((labels && labels[v]) || v)}</option>`).join('');
    const human = g && g.mode === 'human';
    return `
      <div class="bp-prop"><div class="k">Porte — ce qui doit tenir pour passer</div>
        <select class="ctx-sel" id="gate-mode" style="width:100%">
          <option value=""${!g ? ' selected' : ''}>ce node n'est pas une porte</option>
          ${opt(vocab('gateModes', []), g ? g.mode : '', GATE_LABEL)}
        </select>
        ${human ? `<div class="ctx-grid" style="margin-top:8px">
          <label class="ctx-field"><span>action attendue</span>
            <select class="ctx-sel" id="gate-action">${opt(vocab('humanActions', []), (g.params || {}).action || 'approve', HUMAN_LABEL)}</select></label>
          <label class="ctx-field"><span>si refus</span>
            <select class="ctx-sel" id="gate-reject">${opt(vocab('onReject', []), g.onReject || 'escalation', null)}</select></label>
        </div>
        <div class="v soft" style="margin-top:6px">Une porte humaine <b>suspend</b> le flow. Reprendre après l'attente exige un point de reprise en amont, sinon le travail déjà fait est perdu.</div>` : ''}
      </div>
      <div class="bp-prop"><div class="k">Éclatement parallèle</div>
        <label class="ctx-field" style="flex-direction:row;align-items:center;gap:8px">
          <input type="checkbox" id="sc-on"${(n.config && n.config.scatter) ? ' checked' : ''} />
          <span>ce node éclate le travail en branches</span></label>
        ${(n.config && n.config.scatter) ? `<div class="ctx-grid" style="margin-top:8px">
          <label class="ctx-field"><span>éclate sur</span>
            <input class="grp-name-input" id="sc-over" value="${esc(n.config.scatter.over || '')}" placeholder="ex. : fichiers à analyser" /></label>
          <label class="ctx-field"><span>branches simultanées (max)</span>
            <input class="grp-name-input" id="sc-max" type="number" min="1" value="${esc(String(n.config.scatter.maxParallel || 4))}" /></label>
        </div>
        <div class="v soft" style="margin-top:6px">Le plafond n'est pas une option : douze branches, c'est douze fois le contexte et douze fois les sorties. Il faut aussi une garde de budget en amont — le plafond borne la largeur, la garde borne la dépense.</div>` : ''}
      </div>
      <div class="bp-prop"><div class="k">Point de reprise</div>
        <label class="ctx-field" style="flex-direction:row;align-items:center;gap:8px">
          <input type="checkbox" id="gate-ckpt"${(n.config && n.config.checkpoint) ? ' checked' : ''} />
          <span>l'état est persisté ici</span></label>
        <div class="v soft" style="margin-top:6px">Ce que le flow peut reprendre après une suspension. Une porte humaine sans point de reprise, ici ou en amont, perd le travail déjà fait.</div>
      </div>`;
  }
  function bindGateSection(panel, n) {
    if (!E.hasVocab()) return;
    const mode = $('#gate-mode', panel);
    if (!mode) return;
    const write = () => {
      const m = mode.value;
      if (!m) {
        if (n.config) delete n.config.gate;
        markDirty(); render(); return;
      }
      n.config = n.config || {};
      const prev = n.config.gate || {};
      const gate = { mode: m, params: prev.params || {} };
      const act = $('#gate-action', panel), rej = $('#gate-reject', panel);
      if (m === 'human') {
        gate.params = { ...gate.params, action: act ? act.value : 'approve' };
        if (gate.params.action === 'sample' && !gate.params.pct) gate.params.pct = 10;
        if (rej) gate.onReject = rej.value;
      }
      n.config.gate = gate;
      n.role = 'Gate';        /* le rôle suit la déclaration (P0.3) */
      markDirty(); render();
    };
    [mode, $('#gate-action', panel), $('#gate-reject', panel)]
      .forEach(el => { if (el) el.addEventListener('change', write); });
    const scOn = $('#sc-on', panel);
    if (scOn) scOn.addEventListener('change', () => {
      n.config = n.config || {};
      if (scOn.checked) { n.config.scatter = { over: '', maxParallel: 4 }; n.role = 'Scatter'; }
      else { delete n.config.scatter; if (n.role === 'Scatter') delete n.role; }
      markDirty(); render();
    });
    const scOver = $('#sc-over', panel), scMax = $('#sc-max', panel);
    const scWrite = () => {
      if (!n.config || !n.config.scatter) return;
      n.config.scatter.over = scOver ? scOver.value.trim() : '';
      const m = parseInt(scMax ? scMax.value : '', 10);
      n.config.scatter.maxParallel = (!isNaN(m) && m > 0) ? m : 1;
      markDirty();
    };
    [scOver, scMax].forEach(el => { if (el) el.addEventListener('change', scWrite); });

    const ck = $('#gate-ckpt', panel);
    if (ck) ck.addEventListener('change', () => {
      n.config = n.config || {};
      if (ck.checked) n.config.checkpoint = { scope: 'state' };
      else delete n.config.checkpoint;
      markDirty(); render();
    });
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
    window.BP2Sections = { gateSectionHtml, bindGateSection,
                           contextSectionHtml, bindContextSection };
  });
})();
