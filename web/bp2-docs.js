/* bp2-docs.js — Documents portés par les nodes
   Chaque node porte ses documents (mission brief, contrat de complétion…).
   Éditeur avec coloration Grimoire (contrats, #patterns, @agents, {{variables}}),
   autocomplétion (@ # $ {{) et lint basé sur le template du pattern.
   ========================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let core = null;
  let modal = null, onSaveCb = null;

  /* ══ Templates par pattern ══ */
  const T = {};
  T['PRD-01'] = [{ id: 'mission-brief.md', title: 'Mission brief', required: ['objectif', 'done_when', 'budget_tokens'],
    body: `# Mission brief — {{titre}}

// cadré par @prd-author — le SOG route sur cette base

objectif: …
contexte: …
contraintes:
  - …
  - budget_tokens: …

done_when:
  - evidence-pack signé, hashes joints
  - gate #GOV-01 verte (fail-closed)

agents: @prd-author
sortie: mission-brief
` }];
  T['ORC-01'] = [{ id: 'routing-table.yaml', title: 'Table de routage', required: ['intentions', 'budget_tokens'],
    body: `# Routing — SOG Dispatch

// @navigator classe l'intention et route

intentions:
  - implémenter → @implementer via task-envelope
  - corriger → @debugger via task-envelope
  - documenter → @doc-writer via task-envelope

budget_tokens: …
fallback: escalade humaine — jamais silencieux
` }];
  T['QUA-01'] = [{ id: 'evidence-manifest.yaml', title: 'Manifeste de preuve', required: ['checks', 'hashes'],
    body: `# Evidence manifest

// assemblé par @test-author — la matière du verdict

entree: task-envelope
checks:
  - tests: …
  - lint: …
hashes: sha256 sur chaque pièce
sortie: evidence-pack
` }];
  T['QUA-02'] = [{ id: 'review-charter.md', title: 'Charte de revue', required: ['criteres'],
    body: `# Charte de revue — Review Gate

// @reviewer est distinct de l'auteur — toujours

entree: evidence-pack
criteres:
  - périmètre respecté
  - preuves complètes, datées
motifs: explicites, jamais silencieux
sortie: decision-trace
` }];
  T['ENG-01'] = [{ id: 'impl-loop.md', title: 'Boucle implement & prove', required: ['perimetre', 'preuves'],
    body: `# Implement & Prove

// @implementer travaille, @debugger répare — preuves en continu

entree: task-envelope
perimetre: …
preuves:
  - diff borné au périmètre
  - tests verts joints
budget_tokens: …
sortie: evidence-pack
` }];
  T['GOV-01'] = [{ id: 'completion-contract.yaml', title: 'Contrat de complétion', required: ['entree', 'regles', 'mode'],
    body: `# Completion contract — porte fail-closed

// vérifié par @policy-warden — cc-verify

entree: evidence-pack
regles:
  - tests: verts, joints à la preuve
  - diff: borné au périmètre déclaré
mode: fail-closed
sortie: compliance-declaration
` }];
  T['GOV-02'] = [{ id: 'journal-config.yaml', title: 'Config du journal', required: ['chaine'],
    body: `# Decision log — journal chaîné

entree: decision-trace
chaine: sha256, chaque entrée référence la précédente
horodatage: signé
sortie: audit-log
` }];
  T['SEC-01'] = [{ id: 'threat-model.md', title: 'Threat model', required: ['surfaces', 'mitigations'],
    body: `# Threat model

// @threat-modeler énumère, @sentinel bloque tant que non accepté

surfaces:
  - …
mitigations:
  - …
sortie: evidence-pack
` }];
  T['MEM-01'] = [{ id: 'memory-policy.md', title: 'Politique mémoire', required: ['expiration'],
    body: `# Shared context — politique

// @memory-keeper publie au Memory OS

entree: decision-trace
portee: bornée au projet {{projet}}
expiration: …
sortie: memory-slice
` }];
  T['OPS-01'] = [{ id: 'pipeline-gate.yaml', title: 'Gate CI', required: ['verdict', 'rollback'],
    body: `# Pipeline gate

entree: compliance-declaration
verdict: requis vert — sinon la CI s'arrête
rollback: déclaré avant tout déploiement
sortie: audit-log
` }];

  const EXT_TPL = [{ id: 'adapter-config.yaml', title: 'Config de l\u2019adaptateur', required: ['perimetre'],
    body: `# Adaptateur d'extension

// le framework externe travaille SOUS contrat Grimoire

entree: task-envelope
perimetre: …
traces: normalisées, exportées en shadow
sortie: evidence-pack
` }];
  const GENERIC = ref => [{ id: 'notes.md', title: 'Notes du node', required: [],
    body: `# Notes — ${ref}

// espace libre — versionné avec le blueprint

` }];
  const T_AGENT = [{ id: 'system-prompt.md', title: 'Prompt système', required: ['role', 'mission'],
    body: `# Prompt système — {{titre}}

// qui est cet agent, ce qu'il refuse — compilé tel quel

role: …
mission: …
perimetre: le projet courant, rien d'autre
style: sobre, tracé, jamais silencieux
refus:
  - secrets en clair
  - actions hors périmètre
  - « fini » sans evidence-pack
` }];

  /* Docs remplis pour l'exemple studio */
  const EXAMPLES = {
    'mission-brief.md': `# Mission brief — {{titre}}

// cadré par @prd-author — le SOG route sur cette base

objectif: refonte du module d'export PDF
contexte: legacy non testé — périmètre borné à src/export/
contraintes:
  - aucun changement d'API publique
  - budget_tokens: 90k
  - modèle: sonnet

done_when:
  - evidence-pack signé, hashes joints
  - gate #GOV-01 verte (fail-closed)
  - décision au journal via #GOV-02

agents: @prd-author @navigator
sortie: mission-brief
`,
    'completion-contract.yaml': `# Completion contract — porte fail-closed

// vérifié par @policy-warden — cc-verify passe ou rien ne passe

entree: evidence-pack
regles:
  - tests: verts, joints à la preuve
  - diff: borné à src/export/
  - secrets: scan zéro fuite (#SEC-02 en amont)
mode: fail-closed
budget_tokens: 8k
sortie: compliance-declaration
`,
    'system-prompt.md': `# Prompt système — implémenteur

// compilé tel quel dans l'artefact de l'agent

role: implémenteur — spécialiste du code, preuves à l'appui
mission: implémenter la tâche reçue via task-envelope, dans le périmètre déclaré
perimetre: src/export/ uniquement — rien d'autre
style: sobre, tracé, jamais silencieux
outils: déclarés dans ma fiche — tout le reste m'est refusé
refus:
  - secrets en clair (hook scan-secrets actif)
  - écrire hors périmètre (hook block-scope actif)
  - déclarer « fini » sans evidence-pack signé (#GOV-01)
agents_amis: @reviewer relit, le testeur contre-vérifie
`
  };

  function templatesFor(node) {
    if (!node || node.kind === 'group' || node.kind === 'trigger') return [];
    if (node.kind === 'agent') return T_AGENT;
    if (T[node.ref]) return T[node.ref];
    if (String(node.ref).startsWith('ext:')) return EXT_TPL;
    return GENERIC(node.ref);
  }
  function docsFor(node) {
    const tpls = templatesFor(node);
    const docs = node.docs || {};
    const list = tpls.map(t => ({ ...t, edited: !!docs[t.id], content: docs[t.id] ? docs[t.id].content : t.body, at: docs[t.id] ? docs[t.id].at : null }));
    Object.keys(docs).forEach(id => {
      if (!tpls.some(t => t.id === id)) list.push({ id, title: 'Note libre', required: [], body: '', edited: true, content: docs[id].content, at: docs[id].at });
    });
    return list;
  }
  function badgeFor(node) {
    if (node.kind === 'group') {
      let edited = 0;
      (function walk(g) { (g.nodes || []).forEach(n => { if (n.kind === 'group') walk(n.sub); else edited += Object.keys(n.docs || {}).length; }); })(node.sub || {});
      return edited ? { n: edited, edited } : { n: 0, edited: 0 };
    }
    const l = docsFor(node);
    return { n: l.length, edited: l.filter(d => d.edited).length };
  }
  function docTokens(node) {
    let t = 0;
    Object.values(node.docs || {}).forEach(d => { t += Math.round((d.content || '').length / 4); });
    return t;
  }

  /* ══ Surfaces inspecteur / fiche ══ */
  function inspectorList(node) {
    const l = docsFor(node);
    if (!l.length) return '';
    return `<div class="bp-prop"><div class="k">Documents <span style="text-transform:none;letter-spacing:0;color:var(--ink-muted)">· éditables, versionnés avec le flow</span></div>
      ${l.map(d => `<div class="f-doc" data-doc-open="${esc(d.id)}" style="margin-bottom:5px">
        <span class="ic">▤</span>
        <span class="meta"><span class="nm">${esc(d.id)}</span>
        <span class="st${d.edited ? ' edited' : ''}">${d.edited ? 'édité · compte dans le prompt' : 'modèle du pattern — à remplir'}</span></span>
        <span class="open">éditer →</span>
      </div>`).join('')}</div>`;
  }
  function ficheSection(node) {
    const l = docsFor(node);
    if (!l.length) return '';
    return `<div><div class="at-lbl" style="margin-bottom:6px">Documents du node</div>
      <div class="at-col" style="gap:6px">
      ${l.map(d => `<div class="f-doc" data-doc-open="${esc(d.id)}">
        <span class="ic">▤</span>
        <span class="meta"><span class="nm">${esc(d.id)}</span>
        <span class="st${d.edited ? ' edited' : ''}">${d.edited ? 'édité · ≈ ' + Math.round((d.content || '').length / 4) + ' tok injectés' : 'modèle — coloration & autocomplétion Grimoire'}</span></span>
        <span class="open">éditer →</span>
      </div>`).join('')}
      <div class="f-doc" data-doc-open="__new__"><span class="ic">＋</span><span class="meta"><span class="nm">note libre</span><span class="st">un .md de plus sur ce node</span></span><span class="open">créer →</span></div>
      </div></div>`;
  }

  /* ══ Coloration Grimoire ══ */
  function grammar() {
    const contracts = (Atelier.catalogue.contracts || []).map(c => c.id).join('|');
    return new RegExp([
      '(^[ \\t]*\\/\\/[^\\n]*)',                                  // 1 commentaire
      '(^#{1,3} [^\\n]*)',                                        // 2 titre
      '(\\*\\*[^*\\n]+\\*\\*)',                                   // 3 gras
      '(\\{\\{[^}\\n]*\\}\\})',                                   // 4 variable
      '(#[A-Z]{3}-\\d{2})',                                       // 5 ref pattern
      '(@[a-z][a-z0-9-]*)',                                       // 6 agent
      '(^[ \\t]*[A-Za-zà-ÿ_][A-Za-zà-ÿ0-9_ -]{0,28}:)',           // 7 clé
      `(\\b(?:${contracts})\\b)`,                                 // 8 contrat
      '(\\b\\d+(?:[.,]\\d+)?(?:k|M)?\\b)',                        // 9 nombre
      '(^[ \\t]*- )'                                              // 10 puce
    ].join('|'), 'gm');
  }
  function highlight(src) {
    const AG = agentSet();
    const text = esc(src);
    const re = grammar();
    let out = '', last = 0, m;
    while ((m = re.exec(text)) !== null) {
      out += text.slice(last, m.index);
      const s = m[0];
      if (m[1]) out += `<span class="tk-cmt">${s}</span>`;
      else if (m[2]) out += `<span class="tk-h">${s}</span>`;
      else if (m[3]) out += `<span class="tk-b">${s}</span>`;
      else if (m[4]) out += `<span class="tk-var">${s}</span>`;
      else if (m[5]) {
        const ref = s.slice(1);
        const known = !!Atelier.byRef[ref];
        out += `<span style="color:${Atelier.catColor(ref.slice(0, 3))}" class="${known ? '' : 'tk-bad'}">${s}</span>`;
      }
      else if (m[6]) out += `<span class="tk-agent ${AG.has(s.slice(1)) ? '' : 'tk-bad'}">${s}</span>`;
      else if (m[7]) out += `<span class="tk-key">${s}</span>`;
      else if (m[8]) out += `<span style="color:${Atelier.contractColor(s)}">${s}</span>`;
      else if (m[9]) out += `<span class="tk-num">${s}</span>`;
      else if (m[10]) out += `<span class="tk-li">${s}</span>`;
      else out += s;
      last = m.index + s.length;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    return out + text.slice(last);
  }

  /* ══ Vocabulaire (autocomplétion) ══ */
  let _agents = null;
  function agentSet() {
    if (_agents) return _agents;
    _agents = new Set();
    (Atelier.catalogue.patterns || []).forEach(p => (p.agents || []).forEach(a => _agents.add(a)));
    ['doc-writer', 'navigator', 'context-curator'].forEach(a => _agents.add(a));
    return _agents;
  }
  const VARS = ['projet', 'titre', 'profil', 'date', 'budget', 'perimetre'];
  function candidates(kind, q) {
    q = (q || '').toLowerCase();
    if (kind === 'agent') return [...agentSet()].filter(a => a.startsWith(q)).sort().map(a => ({ ins: '@' + a, show: '@' + a, hint: 'agent', d: '#F9A8D4' }));
    if (kind === 'ref') return (Atelier.catalogue.patterns || []).filter(p => p.ref.toLowerCase().includes(q) || p.name.toLowerCase().includes(q))
      .map(p => ({ ins: '#' + p.ref, show: '#' + p.ref, hint: p.name, d: Atelier.catColor(p.cat) }));
    if (kind === 'contract') return (Atelier.catalogue.contracts || []).filter(c => c.id.includes(q))
      .map(c => ({ ins: c.id, show: c.id, hint: c.name, d: c.color }));
    if (kind === 'var') return VARS.filter(v => v.startsWith(q)).map(v => ({ ins: '{{' + v + '}}', show: '{{' + v + '}}', hint: 'variable', d: '#FCD34D' }));
    return [];
  }
  function detectTrigger(val, caret) {
    const tail = val.slice(Math.max(0, caret - 30), caret);
    let m;
    if ((m = tail.match(/\{\{([\w.-]*)$/))) return { kind: 'var', q: m[1], len: m[0].length };
    if ((m = tail.match(/(^|[^\w@])@([a-z0-9-]*)$/i))) return { kind: 'agent', q: m[2], len: m[2].length + 1 };
    if ((m = tail.match(/(^|[^\w#])#([A-Za-z0-9-]*)$/))) return { kind: 'ref', q: m[2], len: m[2].length + 1 };
    if ((m = tail.match(/(^|[\s([:>])\$([\w-]*)$/))) return { kind: 'contract', q: m[2], len: m[2].length + 1 };
    return null;
  }

  /* ══ Lint ══ */
  function lint(content, tpl) {
    const items = [];
    const lineOf = idx => content.slice(0, idx).split('\n').length;
    (tpl.required || []).forEach(k => {
      if (!new RegExp('^[ \\t]*(?:-\\s*)?' + k + '\\s*:', 'm').test(content))
        items.push({ level: 'err', line: null, text: 'clé requise absente : ' + k });
    });
    let m;
    const agRe = /@([a-z][a-z0-9-]*)/g;
    while ((m = agRe.exec(content))) if (!agentSet().has(m[1])) items.push({ level: 'warn', line: lineOf(m.index), text: 'agent inconnu : @' + m[1] });
    const refRe = /#([A-Z]{3}-\d{2})/g;
    while ((m = refRe.exec(content))) if (!Atelier.byRef[m[1]]) items.push({ level: 'warn', line: lineOf(m.index), text: 'pattern inconnu : #' + m[1] });
    const todo = (content.match(/…|TODO/g) || []).length;
    if (todo) items.push({ level: 'info', line: null, text: todo + ' champ' + (todo > 1 ? 's' : '') + ' à compléter (…)' });
    const b = content.match(/budget_tokens\s*:\s*(\d+(?:[.,]\d+)?)\s*(k|M)?/i);
    if (b) items.push({ level: 'info', line: lineOf(b.index), text: 'budget déclaré : ' + b[1] + (b[2] || '') + ' tok — compté dans l\u2019estimation' });
    return items;
  }

  /* ══ Éditeur (modal) ══ */
  function isOpen() { return !!modal; }
  function close() {
    if (!modal) return;
    modal.classList.remove('show');
    const el = modal;
    setTimeout(() => el.remove(), 160);
    modal = null;
  }

  function openEditor(node, docId, onSave) {
    onSaveCb = onSave || null;
    if (docId === '__new__') {
      let i = 1, id;
      do { id = 'notes' + (i > 1 ? '-' + i : '') + '.md'; i++; } while ((node.docs || {})[id] || templatesFor(node).some(t => t.id === id));
      node.docs = node.docs || {};
      node.docs[id] = { content: `# Notes — ${node.ref || node.name || 'node'}\n\n// espace libre — versionné avec le blueprint\n\n`, at: Date.now() };
      docId = id;
    }
    const all = docsFor(node);
    const doc = all.find(d => d.id === docId);
    if (!doc) return;
    const s = core ? core.specOf(node) : null;
    const nodeName = s ? (s.kind === 'ext' ? s.name : (s.ref || s.name)) : (node.ref || '');
    const nodeColor = s ? (node.kind === 'group' ? '#A78BFA' : Atelier.catColor(s.cat)) : '#888';

    let content = doc.content;
    let dirty = false, escArm = 0;

    const root = $('#bp-doc-host');
    root.innerHTML = `
      <div class="dm-overlay" id="dm-ov">
        <div class="dm" role="dialog" aria-label="Éditeur de document">
          <div class="dm-head">
            <span class="fn">▤ ${esc(docId)}</span>
            <span class="node-chip"><span class="d" style="background:${nodeColor}"></span>${esc(nodeName)}</span>
            <span class="at-sp"></span>
            <button class="at-btn sm ghost" id="dm-close">✕</button>
          </div>
          <div class="dm-body">
            <div class="dm-main">
              <div class="doc-ed">
                <div class="doc-gutter"><div class="gut-in" id="dm-gut"></div></div>
                <div class="doc-scroll" id="dm-scroll">
                  <pre class="doc-hl" aria-hidden="true"><code id="dm-hl"></code></pre>
                  <textarea class="doc-ta" id="dm-ta" spellcheck="false" wrap="off" autocomplete="off"></textarea>
                </div>
              </div>
            </div>
            <div class="dm-rail">
              <div><div class="r-t">Champs requis</div><div id="dm-req"></div></div>
              <div><div class="r-t">Lint</div><div id="dm-lint"></div></div>
              <div><div class="r-t">Poids</div><div class="dm-tok" id="dm-tok"></div></div>
              <div><div class="r-t">Autocomplétion</div>
                <p class="trig-help"><span class="at-kbd">@</span> agents · <span class="at-kbd">#</span> patterns<br><span class="at-kbd">$</span> contrats · <span class="at-kbd">{{</span> variables<br><span class="at-kbd">↹</span> ou <span class="at-kbd">↵</span> pour accepter</p></div>
            </div>
          </div>
          <div class="dm-foot">
            <button class="at-btn sm acc" id="dm-save">SAUVEGARDER <span class="at-kbd" style="margin-left:5px">⌘S</span></button>
            <button class="at-btn sm ghost" id="dm-reset">revenir au modèle</button>
            <span class="at-sp"></span>
            <span class="state" id="dm-state">${doc.edited ? 'édité' : 'modèle du pattern'}</span>
          </div>
        </div>
      </div>`;
    modal = $('#dm-ov');
    const mEl = modal;
    requestAnimationFrame(() => { if (mEl) mEl.classList.add('show'); });

    const ta = $('#dm-ta'), hl = $('#dm-hl'), gut = $('#dm-gut'), scroll = $('#dm-scroll');
    ta.value = content;

    /* métriques caret */
    const cs = getComputedStyle(ta);
    const lineH = parseFloat(cs.lineHeight);
    const cv = document.createElement('canvas').getContext('2d');
    cv.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily.split(',')[0].replace(/"/g, '');
    const charW = cv.measureText('MMMMMMMMMM').width / 10;

    let ac = null; // {items, idx, kind, len, el}
    function closeAc() { if (ac && ac.el) ac.el.remove(); ac = null; }

    function sync() {
      content = ta.value;
      hl.innerHTML = highlight(content) + '\n';
      const n = content.split('\n').length;
      gut.textContent = Array.from({ length: n }, (_, i) => i + 1).join('\n');
      $('#dm-tok').innerHTML = `≈ <b>${Math.round(content.length / 4)} tok</b> injectés au prompt du node`;
      renderChecks();
    }
    let lintT = null;
    function renderChecks() {
      clearTimeout(lintT);
      lintT = setTimeout(() => {
        $('#dm-req').innerHTML = (doc.required || []).map(k => {
          const ok = new RegExp('^[ \\t]*(?:-\\s*)?' + k + '\\s*:', 'm').test(content);
          return `<div class="rq ${ok ? 'ok' : 'ko'}"><span class="s">${ok ? '✓' : '✗'}</span>${esc(k)}</div>`;
        }).join('') || '<div class="rq" style="color:var(--ink-muted)">aucun — note libre</div>';
        const items = lint(content, doc);
        $('#dm-lint').innerHTML = items.length
          ? items.map(i => `<div class="dl-i ${i.level}" ${i.line ? `data-line="${i.line}"` : ''}>${i.line ? `<span class="ln">L${i.line}</span>` : ''}${esc(i.text)}</div>`).join('')
          : '<div class="dl-ok">✓ rien à signaler</div>';
        $('#dm-lint').querySelectorAll('[data-line]').forEach(el => el.addEventListener('click', () => {
          const ln = parseInt(el.dataset.line, 10);
          const idx = content.split('\n').slice(0, ln - 1).join('\n').length + (ln > 1 ? 1 : 0);
          ta.focus(); ta.setSelectionRange(idx, idx);
          ta.scrollTop = Math.max(0, (ln - 4) * lineH);
        }));
      }, 220);
    }
    function onScroll() {
      hl.style.transform = '';
      hl.firstChild && (hl.style.transform = '');
      $('#dm-hl').style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
      gut.style.transform = `translateY(${-ta.scrollTop}px)`;
      if (ac) closeAc();
    }
    ta.addEventListener('scroll', onScroll);

    function maybeAc() {
      const t = detectTrigger(ta.value, ta.selectionStart);
      if (!t) { closeAc(); return; }
      const items = candidates(t.kind, t.q).slice(0, 9);
      if (!items.length) { closeAc(); return; }
      closeAc();
      const before = ta.value.slice(0, ta.selectionStart);
      const rows = before.split('\n');
      const row = rows.length - 1, col = rows[rows.length - 1].length - t.len;
      const el = document.createElement('div');
      el.className = 'doc-ac';
      let left = 16 + col * charW - ta.scrollLeft;
      let top = 14 + (row + 1) * lineH - ta.scrollTop + 4;
      const r = scroll.getBoundingClientRect();
      left = Math.max(4, Math.min(left, r.width - 288));
      if (top + 200 > r.height) top = Math.max(4, 14 + row * lineH - ta.scrollTop - 204);
      el.style.left = left + 'px'; el.style.top = top + 'px';
      ac = { items, idx: 0, kind: t.kind, len: t.len, el };
      drawAc();
      scroll.appendChild(el);
    }
    function drawAc() {
      ac.el.innerHTML = ac.items.map((it, i) => `
        <div class="i${i === ac.idx ? ' on' : ''}" data-i="${i}"><span class="d" style="background:${it.d}"></span>${esc(it.show)}<span class="h">${esc(it.hint)}</span></div>`).join('')
        + `<div class="foot">↑↓ naviguer · ↹/↵ accepter · échap fermer</div>`;
      ac.el.querySelectorAll('.i').forEach(el => el.addEventListener('pointerdown', e => {
        e.preventDefault(); ac.idx = parseInt(el.dataset.i, 10); accept();
      }));
    }
    function accept() {
      if (!ac) return;
      const it = ac.items[ac.idx];
      const pos = ta.selectionStart;
      ta.value = ta.value.slice(0, pos - ac.len) + it.ins + ta.value.slice(pos);
      const np = pos - ac.len + it.ins.length;
      ta.setSelectionRange(np, np);
      closeAc(); markDirtyDoc(); sync();
    }
    function markDirtyDoc() {
      dirty = true;
      const st = $('#dm-state');
      st.textContent = 'modifié — non sauvegardé'; st.className = 'state';
    }

    ta.addEventListener('input', () => { markDirtyDoc(); sync(); maybeAc(); });
    ta.addEventListener('click', closeAc);
    ta.addEventListener('keydown', e => {
      e.stopPropagation();
      if (ac) {
        if (e.key === 'ArrowDown') { e.preventDefault(); ac.idx = (ac.idx + 1) % ac.items.length; drawAc(); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); ac.idx = (ac.idx - 1 + ac.items.length) % ac.items.length; drawAc(); return; }
        if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); accept(); return; }
        if (e.key === 'Escape') { e.preventDefault(); closeAc(); return; }
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        const p = ta.selectionStart;
        ta.value = ta.value.slice(0, p) + '  ' + ta.value.slice(ta.selectionEnd);
        ta.setSelectionRange(p + 2, p + 2);
        markDirtyDoc(); sync();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
      if (e.key === 'Escape') tryClose();
    });

    function save() {
      node.docs = node.docs || {};
      node.docs[docId] = { content: ta.value, at: Date.now() };
      dirty = false;
      const st = $('#dm-state');
      st.textContent = 'sauvegardé ✓ — ≈ ' + Math.round(ta.value.length / 4) + ' tok au prompt';
      st.className = 'state saved';
      doc.edited = true;
      if (onSaveCb) onSaveCb();
    }
    function tryClose() {
      if (dirty && Date.now() - escArm > 2400) {
        escArm = Date.now();
        Atelier.toast('Modifications <b>non sauvegardées</b> — Échap (ou clic hors fenêtre) à nouveau pour fermer sans garder, <b>⌘S</b> pour sauvegarder.');
        return;
      }
      close();
    }

    $('#dm-close').addEventListener('click', tryClose);
    $('#dm-save').addEventListener('click', save);
    $('#dm-reset').addEventListener('click', () => {
      ta.value = doc.body || '';
      markDirtyDoc(); sync();
      Atelier.toast('Contenu du modèle restauré — <b>⌘S</b> pour l\u2019adopter.');
    });
    modal.addEventListener('pointerdown', e => { if (e.target === modal) tryClose(); });
    modal.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); } });

    sync();
    setTimeout(() => ta.focus(), 60);
  }

  window.BP2Docs = {
    init(c) { core = c; },
    templatesFor, docsFor, badgeFor, docTokens,
    inspectorList, ficheSection, openEditor, isOpen, highlight,
    EXAMPLES
  };
})();
