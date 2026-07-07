/* bp2-composer.js — « Décrire ce que je veux »
   4 questions en langage courant → une équipe équipée + la gouvernance
   qui va bien. Zéro jargon requis : le vocabulaire s'apprend APRÈS,
   en regardant ce qui a été construit.
   ========================================================================== */
(function () {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const STEPS = [
    { id: 'goal', q: 'Que doit faire votre système ?', sub: 'on parle du travail, pas de la technique', opts: [
      { id: 'dev',  t: 'Développer des fonctionnalités', s: 'écrire du code, avec preuves à l\u2019appui' },
      { id: 'bugs', t: 'Corriger des bugs',              s: 'reproduire, réparer, prouver que c\u2019est réparé' },
      { id: 'data', t: 'Analyser des données',           s: 'requêter, synthétiser, sourcer' },
      { id: 'doc',  t: 'Rédiger de la documentation',    s: 'écrire et tenir à jour des docs' }
    ] },
    { id: 'team', q: 'Qui travaille ?', sub: 'vous pourrez toujours changer après', opts: [
      { id: 'solo', t: 'Un seul agent',                    s: 'simple : il reçoit la mission et la traite' },
      { id: 'orch', t: 'Un orchestrateur + des spécialistes', s: 'la mission est découpée et déléguée — recommandé' }
    ] },
    { id: 'care', q: 'Quel niveau de prudence ?', sub: 'combien de verrous avant de dire « fini »', opts: [
      { id: 'fast',   t: 'Rapide',  s: 'une preuve, une porte — le minimum sérieux' },
      { id: 'proven', t: 'Prouvé',  s: '+ revue par un agent tiers, décisions au journal' },
      { id: 'armored', t: 'Blindé', s: '+ scan des secrets et garde-fous automatiques (hooks)' }
    ] },
    { id: 'mem', q: 'Le système doit-il se souvenir ?', sub: 'décisions et contexte réutilisables la prochaine fois', opts: [
      { id: 'yes', t: 'Oui, garder la mémoire', s: 'les agents suivants repartent du réel' },
      { id: 'no',  t: 'Non, chaque mission repart à neuf', s: 'plus simple, moins de contexte' }
    ] }
  ];

  const PRESETS = {
    dev:  { name: 'implémenteur', tools: ['fs-read', 'fs-write', 'code-search', 'tests'], skills: ['ecriture-tests'], second: { name: 'testeur', tools: ['fs-read', 'tests'], skills: ['ecriture-tests', 'revue-code'] }, mission: 'implémenter la fonctionnalité décrite dans le mission brief, preuves jointes' },
    bugs: { name: 'débogueur', tools: ['fs-read', 'fs-write', 'code-search', 'tests'], skills: ['debug'], second: { name: 'testeur', tools: ['fs-read', 'tests'], skills: ['ecriture-tests'] }, mission: 'reproduire le bug, le corriger, prouver la correction par un test' },
    data: { name: 'analyste', tools: ['fs-read', 'code-search'], mcp: ['postgres'], skills: ['analyse-donnees'], second: { name: 'vérificateur', tools: ['fs-read'], skills: ['revue-code'] }, mission: 'répondre à la question posée, chiffres sourcés et requêtes jointes' },
    doc:  { name: 'rédacteur', tools: ['fs-read', 'fs-write', 'code-search'], skills: ['redaction-doc'], second: { name: 'relecteur', tools: ['fs-read'], skills: ['revue-code'] }, mission: 'rédiger une documentation exacte, alignée sur le code réel' }
  };

  let overlay = null, idx = 0, answers = {};

  function open() {
    idx = 0; answers = {};
    close();
    overlay = document.createElement('div');
    overlay.className = 'cp-overlay';
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('show'));
    draw();
  }
  function close() {
    if (!overlay) return;
    overlay.remove(); overlay = null;
  }

  function draw() {
    const s = STEPS[idx];
    overlay.innerHTML = `
      <div class="cp">
        <div class="cp-head">
          <span class="cp-tag">composer · ${idx + 1}/${STEPS.length}</span>
          <button class="at-btn sm ghost" id="cp-close">✕</button>
        </div>
        <h2>${esc(s.q)}</h2>
        <p class="cp-sub">${esc(s.sub)}</p>
        <div class="cp-opts">
          ${s.opts.map(o => `<button class="cp-opt${answers[s.id] === o.id ? ' on' : ''}" data-v="${o.id}">
            <span><b>${esc(o.t)}</b><span>${esc(o.s)}</span></span><span class="arr">${answers[s.id] === o.id ? '✓' : '→'}</span>
          </button>`).join('')}
        </div>
        <div class="cp-foot">
          ${idx > 0 ? '<button class="at-btn sm ghost" id="cp-back">← retour</button>' : '<span></span>'}
          <span class="cp-dots">${STEPS.map((_, i) => `<i class="${i <= idx ? 'on' : ''}"></i>`).join('')}</span>
          <button class="at-btn sm acc" id="cp-next" ${answers[s.id] ? '' : 'disabled'}>${idx === STEPS.length - 1 ? 'CRÉER MON FLOW →' : 'CONTINUER →'}</button>
        </div>
      </div>`;
    overlay.querySelector('#cp-close').addEventListener('click', close);
    overlay.querySelectorAll('.cp-opt').forEach(b => b.addEventListener('click', () => {
      answers[s.id] = b.dataset.v;
      if (idx < STEPS.length - 1) { idx++; draw(); }
      else draw();
    }));
    const back = overlay.querySelector('#cp-back');
    if (back) back.addEventListener('click', () => { idx--; draw(); });
    overlay.querySelector('#cp-next').addEventListener('click', () => {
      if (!answers[s.id]) return;
      if (idx === STEPS.length - 1) { close(); build(); }
      else { idx++; draw(); }
    });
  }

  /* ── construction du graphe (réutilisé par la bibliothèque de templates) ── */
  function makeGraph(answers, uid) {
    const preset = PRESETS[answers.goal] || PRESETS.dev;
    const orch = answers.team === 'orch';
    const care = answers.care;
    const mem = answers.mem === 'yes';
    const armored = care === 'armored';

    const promptDoc = (name, mission) => ({
      'system-prompt.md': { at: Date.now(), content: `# Prompt système — ${name}

// généré par le composer — ajustez librement

role: ${name}
mission: ${mission}
perimetre: le projet courant, rien d'autre
style: sobre, tracé, jamais silencieux
refus:
  - secrets en clair
  - actions hors périmètre
  - « fini » sans evidence-pack
` } });

    const mkAgent = (p, x, y, extra) => ({
      id: uid(), kind: 'agent', role: 'agent', name: p.name, model: 'sonnet', delegates: false,
      tools: (p.tools || []).slice(), mcp: (p.mcp || []).slice(), skills: (p.skills || []).slice(),
      hooks: armored ? [{ when: 'after-tool', then: 'scan-secrets' }, { when: 'before-write', then: 'block-scope' }] : [{ when: 'task-end', then: 'log-trace' }],
      docs: promptDoc(p.name, p.mission || preset.mission), x, y, ...(extra || {})
    });

    const nodes = [], edges = [];
    const wire = (a, b, c) => edges.push({ id: uid(), from: a.id, to: b.id, contract: c });

    const prd = { id: uid(), ref: 'PRD-01', x: 110, y: 320 };
    nodes.push(prd);
    let producers = [];

    if (orch) {
      const chef = { id: uid(), kind: 'agent', role: 'orchestrateur', name: 'chef-de-mission', model: 'sonnet', delegates: false, tools: [], mcp: [], skills: [], hooks: [{ when: 'task-end', then: 'log-trace' }], docs: promptDoc('chef-de-mission', 'découper la mission, déléguer aux spécialistes, ne jamais produire soi-même'), x: 420, y: 320 };
      const a1 = mkAgent(preset, 730, 210);
      const a2 = mkAgent({ ...preset.second, mission: 'vérifier et compléter le travail de ' + preset.name }, 730, 430);
      nodes.push(chef, a1, a2);
      wire(prd, chef, 'mission-brief');
      wire(chef, a1, 'task-envelope');
      wire(chef, a2, 'task-envelope');
      producers = [a1, a2];
    } else {
      const sog = { id: uid(), ref: 'ORC-01', x: 410, y: 320 };
      const a1 = mkAgent(preset, 720, 320);
      nodes.push(sog, a1);
      wire(prd, sog, 'mission-brief');
      wire(sog, a1, 'task-envelope');
      producers = [a1];
    }

    const px = orch ? 1040 : 1010;
    let proofSrc = producers;
    if (armored) {
      const sec = { id: uid(), ref: 'SEC-02', x: px, y: 320 };
      nodes.push(sec);
      producers.forEach(a => wire(a, sec, 'evidence-pack'));
      proofSrc = [sec];
    }
    const gov = { id: uid(), ref: 'GOV-01', x: px + 300, y: 250 };
    nodes.push(gov);
    proofSrc.forEach(a => wire(a, gov, 'evidence-pack'));

    if (care !== 'fast') {
      const rev = { id: uid(), ref: 'QUA-02', x: px + 300, y: 450 };
      const jr = { id: uid(), ref: 'GOV-02', x: px + 600, y: 450 };
      nodes.push(rev, jr);
      proofSrc.forEach(a => wire(a, rev, 'evidence-pack'));
      wire(rev, jr, 'decision-trace');
      if (mem) {
        const m = { id: uid(), ref: 'MEM-01', x: px + 900, y: 450 };
        nodes.push(m);
        wire(rev, m, 'decision-trace');
      }
    } else if (mem) {
      const rev = { id: uid(), ref: 'QUA-02', x: px + 300, y: 450 };
      const m = { id: uid(), ref: 'MEM-01', x: px + 600, y: 450 };
      nodes.push(rev, m);
      proofSrc.forEach(a => wire(a, rev, 'evidence-pack'));
      wire(rev, m, 'decision-trace');
    }

    /* zone de commentaire */
    const goalTxt = { dev: 'développer, avec preuves', bugs: 'corriger, avec preuves', data: 'analyser, sourcé', doc: 'documenter, aligné sur le code' }[answers.goal];
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const comment = { id: uid(), x: Math.min(...xs) - 40, y: Math.min(...ys) - 70,
      w: Math.max(...xs) - Math.min(...xs) + 300, h: Math.max(...ys) - Math.min(...ys) + 220,
      label: 'composé — ' + goalTxt + (armored ? ' · blindé' : care === 'proven' ? ' · prouvé' : '') };

    return { nodes, edges, comments: [comment], orch, armored };
  }

  function build() {
    const E = window.BPEditor;
    if (!E) return;
    const r = makeGraph(answers, E.uid);
    const g = E.G();
    g.nodes.push(...r.nodes);
    g.edges.push(...r.edges);
    g.comments.push(...r.comments);
    E.markDirty();
    E.render();
    E.fitView();
    const agentNames = r.nodes.filter(n => n.kind === 'agent').map(n => n.name);
    Atelier.toast(
      'Votre flow est construit : <b>' + esc(agentNames.join(', ')) + '</b>' +
      (r.orch ? ' — le chef délègue, les spécialistes prouvent.' : ' reçoit les tâches routées.') +
      ' Chaque agent est déjà <b>équipé</b> (double-clic pour voir sa fiche) et son prompt est écrit. ' +
      (r.armored ? 'Des <b>hooks</b> scannent les secrets automatiquement. ' : '') +
      'Ensuite : <b>SIMULER</b>.', { good: true, ms: 9000 });
  }

  window.BP2Composer = { open, makeGraph };
})();
