/* bp2-library.js — Gestion des blueprints & bibliothèque de templates
   · un seul blueprint ACTIF par projet (●)
   · gérer : renommer, dupliquer, exporter/importer JSON, supprimer
   · templates préfaits Grimoire → instanciés en copie locale (dérive)
   ========================================================================== */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const LS_ACTIVE = 'grimoire.atelier.bp.active';

  const blankMeta = () => ({ validated: false, simulated: false, compiledAt: null, dirty: true, path: [] });

  /* ══ Templates préfaits ══ */
  function crewGraph(uid) {
    const n = (ref, x, y) => ({ id: uid(), ref, x, y });
    const prd = n('ORC-02', 110, 300), orc = n('ORC-01', 410, 340), crew = { id: uid(), ref: 'crewai-crew', x: 710, y: 300 },
      qua = n('QUA-04', 1010, 360), gov = n('QUA-05', 1310, 300);
    const e = (a, b, c) => ({ id: uid(), from: a.id, to: b.id, contract: c });
    return { nodes: [prd, orc, crew, qua, gov],
      edges: [e(prd, orc, 'task-envelope'), e(orc, crew, 'task-envelope'), e(crew, qua, 'evidence-pack'), e(qua, gov, 'evidence-pack')],
      comments: [{ id: uid(), x: 60, y: 210, w: 1500, h: 320, label: 'délégation à un crew externe — sous contrat', color: '#FF6B3D' }] };
  }
  const composed = (answers, post) => uid => {
    const r = window.BP2Composer.makeGraph(answers, uid);
    if (post) post(r, uid);
    return r;
  };
  const TEMPLATES = [
    { slug: 'feature-dev', name: 'Développement de feature', tags: ['équipe', 'prouvé', 'mémoire'],
      desc: 'Un chef délègue à deux spécialistes équipés ; revue tierce, journal chaîné, mémoire.',
      make: composed({ goal: 'dev', team: 'orch', care: 'proven', mem: 'yes' }) },
    { slug: 'chasse-bugs', name: 'Chasse aux bugs', tags: ['solo', 'prouvé'],
      desc: 'Un débogueur outillé reproduit, corrige et prouve — revue avant le « fini ».',
      make: composed({ goal: 'bugs', team: 'solo', care: 'proven', mem: 'no' }) },
    { slug: 'analyse-data', name: 'Analyse de données sourcée', tags: ['équipe', 'MCP', 'mémoire'],
      desc: 'Analyste branché PostgreSQL + vérificateur ; chiffres sourcés, décisions persistées.',
      make: composed({ goal: 'data', team: 'orch', care: 'proven', mem: 'yes' }) },
    { slug: 'doc-vivante', name: 'Documentation vivante', tags: ['solo', 'léger', 'mémoire'],
      desc: 'Un rédacteur aligné sur le code réel ; la mémoire garde ce qui a été décidé.',
      make: composed({ goal: 'doc', team: 'solo', care: 'fast', mem: 'yes' }) },
    { slug: 'release-blindee', name: 'Release blindée', tags: ['équipe', 'blindé', 'CI'],
      desc: 'Hooks de scan sur chaque agent, porte fail-closed, déploiement gated en CI.',
      make: composed({ goal: 'dev', team: 'orch', care: 'armored', mem: 'yes' }, (r, uid) => {
        const gov = r.nodes.find(n => n.ref === 'QUA-05');
        if (gov) {
          const ops = { id: uid(), ref: 'GOV-02', x: gov.x + 300, y: gov.y };
          r.nodes.push(ops);
          r.edges.push({ id: uid(), from: gov.id, to: ops.id, contract: 'verification-verdict' });
        }
      }) },
    { slug: 'crew-externe', name: 'Délégation à un crew externe', tags: ['extension', 'CrewAI'], requires: 'crewai',
      desc: 'Mission cadrée, dispatchée à un crew CrewAI sous contrat — preuve puis porte.',
      make: crewGraph }
  ];

  /* ══ Actif (un seul par projet) ══ */
  const activeId = () => localStorage.getItem(LS_ACTIVE);
  function setActive(id) {
    localStorage.setItem(LS_ACTIVE, id);
    updateChip();
    BPEditor.refreshSelect();
    Atelier.toast('<b>' + esc(id) + '</b> est le blueprint <b>actif</b> du projet — c\u2019est lui que l\u2019Atelier compile et lance. Les autres restent des brouillons.', { good: true, ms: 5200 });
  }
  function updateChip() {
    const chip = $('#bp-active');
    if (!chip || !window.BPEditor) return;
    const on = activeId() === BPEditor.bpId();
    chip.classList.toggle('on', on);
    chip.innerHTML = on ? '● ACTIF' : '○ activer';
    chip.title = on ? 'Blueprint actif du projet — les autres sont des brouillons.'
      : 'Faire de ce blueprint LE blueprint actif du projet (un seul à la fois).';
  }

  /* ══ Stockage : rename / duplicate / delete / export / import ══ */
  const slugify = s => String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'flow';
  function renameBp(oldId, raw) {
    const newId = slugify(raw);
    if (!newId || newId === oldId) return;
    const list = Atelier.bpList();
    if (list.includes(newId)) { Atelier.toast('<b>' + esc(newId) + '</b> existe déjà.'); return; }
    const st = Atelier.loadBp(oldId);
    Atelier.saveBp(newId, st);
    Atelier.deleteBp(oldId);
    if (activeId() === oldId) localStorage.setItem(LS_ACTIVE, newId);
    BPEditor.loadBp(newId);
    Atelier.toast('Renommé en <b>' + esc(newId) + '</b> ✓ — l\u2019ancien fichier reste dans _grimoire/blueprints, supprimez-le au diff git.');
  }
  function duplicateBp(id) {
    const list = Atelier.bpList();
    let copy = id + '-derive', i = 2;
    while (list.includes(copy)) copy = id + '-derive-' + i++;
    const st = JSON.parse(JSON.stringify(Atelier.loadBp(id) || {}));
    st.meta = Object.assign(blankMeta(), st.meta || {}, { compiledAt: null, dirty: true });
    Atelier.saveBp(copy, st);
    BPEditor.loadBp(copy);
    Atelier.toast('<b>' + esc(copy) + '</b> créé — une dérive libre, l\u2019original n\u2019a pas bougé.', { good: true });
  }
  function deleteBp(id) {
    Atelier.deleteBp(id);
    const list = Atelier.bpList().filter(x => x !== id);
    if (activeId() === id) localStorage.removeItem(LS_ACTIVE);
    if (list.length) BPEditor.loadBp(list[0]);
    else {
      Atelier.saveBp('premier-flow', { nodes: [], edges: [], comments: [], view: null, meta: blankMeta() });
      BPEditor.loadBp('premier-flow');
    }
    Atelier.toast('<b>' + esc(id) + '</b> supprimé.');
  }

  function cleanCopy(st) {
    return JSON.parse(JSON.stringify(st, (k, v) => (k && k[0] === '_') ? undefined : v));
  }
  function exportBp(id) {
    const payload = {
      format: 'grimoire-blueprint', kit: '3.16.0',
      id, project: (Atelier.project() || {}).name || null,
      exportedAt: new Date().toISOString(),
      graph: cleanCopy(Atelier.loadBp(id) || {})
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = id + '.grimoire-blueprint.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    Atelier.toast('<b>' + esc(id) + '</b> exporté en JSON — partageable, versionnable, ré-importable ici.', { good: true });
  }
  function copyBpJson(id) {
    const payload = { format: 'grimoire-blueprint', id, graph: cleanCopy(Atelier.loadBp(id) || {}) };
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
      .then(() => Atelier.toast('JSON copié dans le presse-papier ✓'))
      .catch(() => Atelier.toast('Impossible d\u2019accéder au presse-papier — utilisez Exporter.'));
  }
  function importBp(file) {
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const j = JSON.parse(rd.result);
        const graph = j.graph || j;
        if (!Array.isArray(graph.nodes)) throw new Error('pas de nodes');
        graph.meta = Object.assign(blankMeta(), graph.meta || {}, { compiledAt: null, dirty: true });
        const list = Atelier.bpList();
        let id = slugify(j.id || file.name.replace(/\.grimoire-blueprint\.json$|\.json$/i, '')), i = 2;
        const base = id;
        while (list.includes(id)) id = base + '-' + i++;
        Atelier.saveBp(id, graph);
        BPEditor.loadBp(id);
        Atelier.toast('<b>' + esc(id) + '</b> importé — ' + graph.nodes.length + ' nodes. Validez-le avant de compiler.', { good: true, ms: 5000 });
      } catch (e) {
        Atelier.toast('Import impossible — ce fichier n\u2019est pas un blueprint Grimoire valide.');
      }
    };
    rd.readAsText(file);
  }

  /* ══ Menu ⋯ ══ */
  let menuEl = null;
  function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
  function openMenu(anchor) {
    closeMenu();
    const id = BPEditor.bpId();
    const isActive = activeId() === id;
    menuEl = document.createElement('div');
    menuEl.className = 'bp-mgr';
    const r = anchor.getBoundingClientRect();
    menuEl.style.left = r.left + 'px';
    menuEl.style.top = (r.bottom + 6) + 'px';
    menuEl.innerHTML = `
      <div class="mi${isActive ? ' dis' : ''}" data-act="activate">● ${isActive ? 'actif — c\u2019est lui que l\u2019Atelier lance' : 'rendre actif <span class="hint">un seul par projet</span>'}</div>
      <div class="mi" data-act="rename">✎ renommer…</div>
      <div class="mi" data-act="dup">⧉ dupliquer <span class="hint">créer une dérive</span></div>
      <div class="sep"></div>
      <div class="mi" data-act="tpl">❖ nouveau depuis un template…</div>
      <div class="mi" data-act="compose">✚ composer dans un nouveau flow…</div>
      <div class="sep"></div>
      <div class="mi" data-act="export">⬇ exporter (.json)</div>
      <div class="mi" data-act="copy">⧉ copier le JSON</div>
      <div class="mi" data-act="import">⬆ importer un blueprint…</div>
      <div class="sep"></div>
      <div class="mi danger" data-act="del">✕ supprimer <b>${esc(id)}</b></div>`;
    document.body.appendChild(menuEl);
    let delArmed = false;
    menuEl.querySelectorAll('.mi').forEach(mi => mi.addEventListener('click', () => {
      const act = mi.dataset.act;
      if (act === 'activate') { if (!isActive) setActive(id); closeMenu(); }
      else if (act === 'rename') { closeMenu(); startRename(id); }
      else if (act === 'dup') { closeMenu(); duplicateBp(id); }
      else if (act === 'tpl') { closeMenu(); openGallery(); }
      else if (act === 'compose') {
        closeMenu();
        $('#bp-new').click();
        setTimeout(() => window.BP2Composer && BP2Composer.open(), 350);
      }
      else if (act === 'export') { closeMenu(); exportBp(id); }
      else if (act === 'copy') { closeMenu(); copyBpJson(id); }
      else if (act === 'import') { closeMenu(); fileInput.click(); }
      else if (act === 'del') {
        if (!delArmed) { delArmed = true; mi.innerHTML = '✕ <b>sûr ?</b> cliquez encore pour supprimer définitivement'; return; }
        closeMenu(); deleteBp(id);
      }
    }));
  }
  document.addEventListener('pointerdown', e => {
    if (menuEl && !menuEl.contains(e.target) && e.target.id !== 'bp-manage') closeMenu();
  }, true);

  function startRename(id) {
    const sel = $('#bp-select');
    const inp = document.createElement('input');
    inp.className = 'at-search';
    inp.style.cssText = 'width:170px;padding:6px 10px;font-family:var(--font-mono);font-size:0.73rem';
    inp.value = id;
    sel.style.display = 'none';
    sel.parentNode.insertBefore(inp, sel);
    inp.focus(); inp.select();
    const done = commit => {
      inp.remove(); sel.style.display = '';
      if (commit) renameBp(id, inp.value);
    };
    inp.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Enter') done(true);
      if (e.key === 'Escape') done(false);
    });
    inp.addEventListener('blur', () => done(true));
  }

  /* input fichier caché (import) */
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = '.json,application/json';
  fileInput.style.display = 'none';
  document.body.appendChild(fileInput);
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) importBp(fileInput.files[0]);
    fileInput.value = '';
  });

  /* ══ Galerie de templates ══ */
  let gal = null;
  function closeGallery() { if (gal) { gal.remove(); gal = null; } }
  function openGallery() {
    closeGallery();
    gal = document.createElement('div');
    gal.className = 'cp-overlay show';
    gal.innerHTML = `
      <div class="cp lib">
        <div class="cp-head">
          <span class="cp-tag">templates grimoire · points de départ</span>
          <button class="at-btn sm ghost" id="lib-close">✕</button>
        </div>
        <h2>Partir d\u2019un template</h2>
        <p class="cp-sub">chaque template s\u2019instancie en <b>copie locale</b> — votre dérive, l\u2019original reste au catalogue.</p>
        <div class="lib-grid">
          ${TEMPLATES.map((t, i) => `
            <div class="lib-card" data-tpl="${i}">
              <div class="r1"><b>${esc(t.name)}</b>${t.requires ? '<span class="req">ext · ' + esc(t.requires) + '</span>' : ''}</div>
              <p>${esc(t.desc)}</p>
              <div class="r2">
                <span class="tags">${t.tags.map(x => `<i>${esc(x)}</i>`).join('')}</span>
                <span class="use">utiliser →</span>
              </div>
            </div>`).join('')}
        </div>
      </div>`;
    document.body.appendChild(gal);
    gal.querySelector('#lib-close').addEventListener('click', closeGallery);
    gal.addEventListener('pointerdown', e => { if (e.target === gal) closeGallery(); });
    gal.querySelectorAll('.lib-card').forEach(card => card.addEventListener('click', () => {
      instantiate(TEMPLATES[parseInt(card.dataset.tpl, 10)]);
    }));
  }
  function instantiate(tpl) {
    const doIt = () => {
      const r = tpl.make(BPEditor.uid);
      const st = { nodes: r.nodes, edges: r.edges, comments: r.comments || [], view: null, meta: blankMeta() };
      const list = Atelier.bpList();
      let id = tpl.slug, i = 2;
      while (list.includes(id)) id = tpl.slug + '-' + i++;
      Atelier.saveBp(id, st);
      closeGallery();
      BPEditor.loadBp(id);
      Atelier.toast('Template <b>' + esc(tpl.name) + '</b> instancié en <b>' + esc(id) + '</b> — dérivez librement : agents, équipement, gouvernance, tout est à vous.', { good: true, ms: 6500 });
    };
    if (tpl.requires === 'crewai' && !Atelier.installedExts().includes('crewai')) BPEditor.installExtInline('crewai', doIt);
    else doIt();
  }

  /* ══ Boot ══ */
  function boot() {
    if (!window.BPEditor || !window.Atelier) { setTimeout(boot, 80); return; }
    const mg = $('#bp-manage');
    if (mg) mg.addEventListener('click', () => menuEl ? closeMenu() : openMenu(mg));
    const chip = $('#bp-active');
    if (chip) chip.addEventListener('click', () => {
      if (activeId() !== BPEditor.bpId()) setActive(BPEditor.bpId());
    });
    BPEditor.on('bp-loaded', () => { updateChip(); });
    /* premier blueprint du projet : actif par défaut */
    if (!activeId() && Atelier.bpList().length) localStorage.setItem(LS_ACTIVE, BPEditor.bpId());
    updateChip();
    BPEditor.refreshSelect();
  }
  boot();

  window.BP2Library = { openGallery, setActive, activeId, exportBp, importBp };
})();
