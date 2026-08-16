/* bp2-evals.js — Déclarer la preuve de comportement (P1.2)
   Le Studio valide la forme d'un flow, jamais son comportement. Les évals
   sont la réponse : un cas d'entrée, des assertions sur la sortie, attachés
   au node ou au blueprint et versionnés avec lui.
   Le backend savait les lire, les linter, les compiler et en rapporter le
   taux ; rien ici ne savait les écrire. Ce module comble ce dernier écart.
   Le Studio n'exécute toujours rien : il déclare, l'hôte exécute.
   ========================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  /* Libellés des cinq genres d'assertion. Les identifiants viennent du
     serveur (/api/primitives) ; ceci ne fait que les rendre lisibles. */
  const KIND_LABEL = {
    'contract': 'la sortie honore un contrat',
    'cost': 'le coût reste sous un plafond',
    'no-refusal': "l'agent ne refuse pas",
    'verdict': 'le verdict attendu tombe',
    'path-taken': 'le chemin suivi après un échec injecté'
  };
  const VERDICTS = ['pass', 'fail', 'partial', 'block'];

  function whenReady(cb) {
    if (window.BPEditor && window.Atelier) return cb();
    setTimeout(() => whenReady(cb), 60);
  }

  whenReady(() => {
    const E = window.BPEditor;
    const esc = Atelier.esc;

    const suiteOf = n => (n.config && n.config.evals) || null;

    /* Une suite non vide doit être versionnée (R-E1) : on pose la version
       à la création plutôt que de laisser le lint la réclamer après coup. */
    function ensureSuite(n) {
      n.config = n.config || {};
      if (!n.config.evals) n.config.evals = { version: '1.0.0', cases: [] };
      return n.config.evals;
    }

    function assertionLine(a) {
      const k = a.kind;
      let detail = '';
      if (k === 'contract') detail = a.contract || '?';
      else if (k === 'cost') detail = a.maxTokens ? `${a.maxTokens} tok` : `${a.maxUsd} $`;
      else if (k === 'verdict') detail = a.expected || '?';
      else if (k === 'path-taken') detail = (a.path || []).join(' → ');
      return `<span class="ev-a"><b>${esc(KIND_LABEL[k] || k)}</b>${detail ? `<i>${esc(detail)}</i>` : ''}</span>`;
    }

    function html(n) {
      const suite = suiteOf(n);
      const cases = (suite && suite.cases) || [];
      if (!suite) {
        return `<div class="bp-prop"><div class="k">Évals — preuve de comportement</div>
          <div class="v soft">Ce node n'a aucune éval. Le flow peut être valide et se comporter mal : la validation vérifie la forme, pas ce que l'agent produit.</div>
          <button class="at-btn sm" id="ev-add-suite" style="margin-top:8px">DÉCLARER UNE PREUVE</button></div>`;
      }
      return `<div class="bp-prop"><div class="k">Évals — preuve de comportement <span class="cid">v${esc(suite.version || '?')}</span></div>
        <div class="bp-evals">
          ${cases.length ? cases.map((c, i) => `
            <div class="ev-case">
              <div class="ev-head"><b>${esc(c.id || 'cas sans identifiant')}</b>
                <button class="cb-x" data-ev-del="${i}" title="Retirer ce cas" aria-label="Retirer ce cas">✕</button></div>
              <div class="ev-in">${esc(JSON.stringify(c.input || {}).slice(0, 90))}</div>
              <div class="ev-as">${(c.assert || []).map(assertionLine).join('')}</div>
            </div>`).join('')
            : '<p class="empty">Suite déclarée, aucun cas — le lint la signalera.</p>'}
        </div>
        <button class="at-btn sm acc" id="ev-add-case" style="margin-top:8px">AJOUTER UN CAS</button>
        <div class="v soft" style="margin-top:6px">Déclarer n'est pas exécuter : l'hôte (<code>agent-test</code>) fait tourner ces cas, le Studio ne fait que les attacher au graphe.</div>
      </div>`;
    }

    /* ── Formulaire d'ajout ── */
    function openCaseForm(n, onDone) {
      const kinds = (E.vocab ? E.vocab('evalAssertionKinds', null) : null)
        || Object.keys(KIND_LABEL);
      const ov = document.createElement('div');
      ov.className = 'cp-overlay show';
      ov.innerHTML = `
        <div class="cp">
          <div class="cp-head"><span class="cp-tag">éval · nouveau cas</span>
            <button class="at-btn sm ghost" id="ev-close">✕</button></div>
          <h2>Qu'est-ce qui doit être vrai ?</h2>
          <p class="cp-sub">un cas d'entrée, puis ce que la sortie doit honorer</p>
          <div class="ctx-grid">
            <label class="ctx-field"><span>identifiant du cas</span>
              <input class="grp-name-input" id="ev-id" placeholder="ex. : mission-nominale-produit-une-preuve" /></label>
            <label class="ctx-field"><span>entrée (la mission donnée)</span>
              <input class="grp-name-input" id="ev-input" placeholder="ex. : Ajouter un endpoint /health" /></label>
            <label class="ctx-field"><span>ce qui doit être vrai</span>
              <select class="ctx-sel" id="ev-kind">
                ${kinds.map(k => `<option value="${esc(k)}">${esc(KIND_LABEL[k] || k)}</option>`).join('')}
              </select></label>
            <label class="ctx-field" id="ev-detail-wrap"><span id="ev-detail-label">contrat attendu</span>
              <input class="grp-name-input" id="ev-detail" placeholder="evidence-pack" /></label>
          </div>
          <div class="cp-foot"><span></span><span></span>
            <button class="at-btn sm acc" id="ev-ok">AJOUTER →</button></div>
        </div>`;
      document.body.appendChild(ov);

      const kind = $('#ev-kind', ov), detail = $('#ev-detail', ov);
      const label = $('#ev-detail-label', ov), wrap = $('#ev-detail-wrap', ov);
      const sync = () => {
        const k = kind.value;
        if (k === 'no-refusal') { wrap.style.display = 'none'; return; }
        wrap.style.display = '';
        if (k === 'contract') { label.textContent = 'contrat attendu'; detail.placeholder = 'evidence-pack'; }
        else if (k === 'cost') { label.textContent = 'plafond en tokens'; detail.placeholder = '60000'; }
        else if (k === 'verdict') { label.textContent = `verdict attendu (${VERDICTS.join(' | ')})`; detail.placeholder = 'pass'; }
        else { label.textContent = 'chemin attendu (nodes séparés par des virgules)'; detail.placeholder = 'crew, verify'; }
      };
      kind.addEventListener('change', sync); sync();

      const close = () => ov.remove();
      $('#ev-close', ov).addEventListener('click', close);
      $('#ev-ok', ov).addEventListener('click', () => {
        const id = $('#ev-id', ov).value.trim();
        const input = $('#ev-input', ov).value.trim();
        if (!id) { Atelier.toast('Un cas a besoin d’un identifiant — c’est ce que le rapport affichera.'); return; }
        const k = kind.value, raw = detail.value.trim();
        const a = { kind: k };
        if (k === 'contract') a.contract = raw || 'handoff-packet';
        else if (k === 'cost') a.maxTokens = parseInt(raw, 10) || 60000;
        else if (k === 'verdict') a.expected = VERDICTS.includes(raw) ? raw : 'pass';
        else if (k === 'path-taken') {
          a.path = raw.split(',').map(s => s.trim()).filter(Boolean);
          a.inject = { node: '', class: 'timeout' };   /* à compléter dans le JSON */
        }
        const suite = ensureSuite(n);
        suite.cases.push({ id, input: input ? { mission: input } : {}, assert: [a] });
        close();
        onDone();
      });
    }

    /* ── Câblage dans l'inspecteur ── */
    function bind(panel, n) {
      const refresh = () => { E.markDirty(); E.render(); };
      const addSuite = $('#ev-add-suite', panel);
      if (addSuite) addSuite.addEventListener('click', () => { ensureSuite(n); refresh(); });
      const addCase = $('#ev-add-case', panel);
      if (addCase) addCase.addEventListener('click', () => openCaseForm(n, refresh));
      $$('[data-ev-del]', panel).forEach(el => el.addEventListener('click', () => {
        const suite = suiteOf(n);
        if (!suite) return;
        suite.cases.splice(parseInt(el.dataset.evDel, 10), 1);
        if (!suite.cases.length) delete n.config.evals;   /* pas de suite vide */
        refresh();
      }));
    }

    window.BP2Evals = { html, bind };
  });
})();
