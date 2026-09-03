/* project-picker.js — sélecteur de projets, partagé par les deux hôtes locaux.

   L'atelier et le portefeuille du cockpit posent la même question — « sur quel
   projet de cette machine travaille-t-on ? » — et servent les mêmes routes.
   Une seule implémentation, donc, chargée à la demande : les pages qui ne
   l'ouvrent jamais ne la téléchargent pas.

   Dépendances injectées par l'hôte (api, esc, toast) : ce fichier ne connaît
   ni `Atelier` ni le chrome du portefeuille.
   ============================================================== */
(function () {
  'use strict';

  var deps = null;
  var REGISTRY = null;

  async function loadRegistry() {
    try { return await deps.api('/api/projects'); } catch (e) { return null; }
  }
  /* ══════════════════════════════════════════════════════════════════════
     Sélecteur de projets — le bouton du haut ouvre vraiment quelque chose.

     Trois entrées, parce que trois situations : les projets déjà connus de
     la machine, un dossier qu'on désigne à la main (navigation ou chemin
     collé), et un scan qui découvre ce qui traîne sous une racine. Le scan
     propose, il n'enrôle pas : un scan qui enrôle tout seul finit par
     remplir le registre de dossiers jetables.
     ══════════════════════════════════════════════════════════════════════ */
  const picker = { view: 'list', browsePath: null, browse: null, scan: null, scanRoot: '', busy: false };

  function pickerOverlay() { return document.getElementById('at-proj-ov'); }

  function closePicker() {
    const ov = pickerOverlay();
    if (ov) ov.remove();
  }

  async function open(options) {
    deps = options;
    if (pickerOverlay()) return;
    picker.view = 'list'; picker.browse = null; picker.scan = null;
    const ov = document.createElement('div');
    ov.className = 'at-overlay'; ov.id = 'at-proj-ov';
    ov.innerHTML = '<div class="at-modal" id="at-proj-modal"></div>';
    document.body.appendChild(ov);
    ov.addEventListener('click', (e) => { if (e.target === ov) closePicker(); });
    document.addEventListener('keydown', function esc(e) {
      if (e.key === 'Escape') { closePicker(); document.removeEventListener('keydown', esc); }
    });
    drawPicker();
    REGISTRY = await loadRegistry();
    drawPicker();
  }

  function projectRow(p, servedPath) {
    const served = p.path === servedPath;
    const badges = []
      .concat(served ? ['<span class="at-status-tag inst">servi ✓</span>'] : [])
      .concat(p.exists === false ? ['<span class="at-status-tag warn">chemin absent</span>'] : [])
      .concat(p.unregistered ? ['<span class="at-status-tag disp">hors registre</span>'] : [])
      .concat(p.exists !== false && !p.managed ? ['<span class="at-status-tag disp">non initialisé</span>'] : [])
      .join(' ');
    return `<button class="at-proj-row${served ? ' on' : ''}" data-path="${deps.esc(p.path)}" ${p.exists === false ? 'disabled' : ''}>
      <span class="at-proj-name">${deps.esc(p.name || p.path)}</span>
      <span class="at-proj-path">${deps.esc(p.path)}</span>
      <span class="at-proj-tags">${badges}</span>
    </button>`;
  }

  function pickerListHtml() {
    if (!REGISTRY) return '<p class="at-sub">lecture du registre…</p>';
    const served = REGISTRY.served || deps.servedPath || '';
    const list = (REGISTRY.projects || []);
    const rows = list.length
      ? list.map(p => projectRow(p, served)).join('')
      : `<p class="at-sub">aucun projet connu de cette machine — ouvrez-en un ci-dessous.</p>`;
    return `
      <h2>Projets de cette machine</h2>
      <p class="at-sub" style="margin-bottom:14px">${list.length} projet${list.length > 1 ? 's' : ''} au registre · ${deps.esc(deps.selectionHint || 'choisir un projet le rend courant')}</p>
      <div class="at-proj-list">${rows}</div>
      <div class="at-row" style="gap:8px;margin-top:16px;flex-wrap:wrap">
        <button class="at-btn sm" id="pk-browse">PARCOURIR UN DOSSIER…</button>
        <button class="at-btn sm" id="pk-scan">SCANNER UNE RACINE…</button>
        <span class="sp" style="flex:1"></span>
        <button class="at-btn sm ghost" id="pk-close">FERMER</button>
      </div>`;
  }

  function pickerBrowseHtml() {
    const b = picker.browse;
    if (!b) return '<h2>Parcourir</h2><p class="at-sub">lecture du dossier…</p>';
    const entries = b.entries.length
      ? b.entries.map(e => `<button class="at-proj-row" data-nav="${deps.esc(e.path)}">
          <span class="at-proj-name">${e.isProject ? '◆ ' : '▸ '}${deps.esc(e.name)}</span>
          <span class="at-proj-tags">${e.isProject ? '<span class="at-status-tag inst">projet</span>' : ''}</span>
        </button>`).join('')
      : '<p class="at-sub">aucun sous-dossier.</p>';
    return `
      <h2>Parcourir</h2>
      <p class="at-sub" style="margin-bottom:10px;word-break:break-all">${deps.esc(b.path)}</p>
      <div class="at-row" style="gap:8px;margin-bottom:10px;flex-wrap:wrap">
        ${b.parent ? `<button class="at-btn sm" data-nav="${deps.esc(b.parent)}">↑ DOSSIER PARENT</button>` : ''}
        <button class="at-btn sm" data-nav="${deps.esc(b.home)}">⌂ MAISON</button>
        <button class="at-btn sm ${b.isProject ? 'pri' : ''}" id="pk-open-here">${b.isProject ? 'OUVRIR CE PROJET →' : 'OUVRIR CE DOSSIER →'}</button>
      </div>
      <div class="at-proj-list">${entries}</div>
      <div class="at-row" style="gap:8px;margin-top:14px">
        <input class="at-search" id="pk-path" placeholder="…ou coller un chemin absolu" spellcheck="false" style="flex:1" />
        <button class="at-btn sm" id="pk-path-go">OUVRIR</button>
      </div>
      <div class="at-row" style="margin-top:14px"><button class="at-btn sm ghost" id="pk-back">← RETOUR</button></div>`;
  }

  function pickerScanHtml() {
    const sc = picker.scan;
    let body;
    if (!sc) {
      body = `<p class="at-sub">indiquez une racine — les dépôts trouvés dessous seront listés, rien ne sera enrôlé sans votre accord.</p>`;
    } else if (!sc.candidates.length) {
      body = `<p class="at-sub">aucun projet sous ${deps.esc(sc.root)} (profondeur ${sc.depth}).</p>`;
    } else {
      body = `<p class="at-sub" style="margin-bottom:10px">${sc.candidates.length} projet${sc.candidates.length > 1 ? 's' : ''} trouvé${sc.candidates.length > 1 ? 's' : ''}${sc.truncated ? ' (liste tronquée)' : ''}</p>
        <div class="at-proj-list">` + sc.candidates.map(c => `
          <label class="at-proj-row as-label">
            <input type="checkbox" data-cand="${deps.esc(c.path)}" ${c.registered ? 'disabled checked' : 'checked'} />
            <span class="at-proj-name">${deps.esc(c.name)}</span>
            <span class="at-proj-path">${deps.esc(c.path)}</span>
            <span class="at-proj-tags">${c.registered ? '<span class="at-status-tag inst">déjà au registre</span>' : (c.managed ? '<span class="at-status-tag maj">initialisé</span>' : '<span class="at-status-tag disp">dépôt git</span>')}</span>
          </label>`).join('') + `</div>
        <div class="at-row" style="margin-top:12px"><button class="at-btn sm pri" id="pk-enrol">ENRÔLER LA SÉLECTION</button></div>`;
    }
    return `
      <h2>Scanner une racine</h2>
      <div class="at-row" style="gap:8px;margin:10px 0 14px">
        <input class="at-search" id="pk-scan-root" value="${deps.esc(picker.scanRoot)}" placeholder="/home/vous/Projets" spellcheck="false" style="flex:1" />
        <input class="at-search" id="pk-scan-depth" value="4" style="width:56px;text-align:center" />
        <button class="at-btn sm" id="pk-scan-go" ${picker.busy ? 'disabled' : ''}>${picker.busy ? 'SCAN…' : 'SCANNER'}</button>
      </div>
      ${body}
      <div class="at-row" style="margin-top:14px"><button class="at-btn sm ghost" id="pk-back">← RETOUR</button></div>`;
  }

  function drawPicker() {
    const m = document.getElementById('at-proj-modal');
    if (!m) return;
    m.innerHTML = picker.view === 'browse' ? pickerBrowseHtml()
      : picker.view === 'scan' ? pickerScanHtml()
      : pickerListHtml();
    bindPicker(m);
  }

  function bindPicker(m) {
    const on = (id, fn) => { const el = m.querySelector('#' + id); if (el) el.addEventListener('click', fn); };
    on('pk-close', closePicker);
    on('pk-back', () => { picker.view = 'list'; drawPicker(); });
    on('pk-browse', () => { picker.view = 'browse'; drawPicker(); navigate(picker.browsePath); });
    on('pk-scan', () => { picker.view = 'scan'; drawPicker(); });

    m.querySelectorAll('[data-path]').forEach(el =>
      el.addEventListener('click', () => selectProjectPath(el.getAttribute('data-path'))));
    m.querySelectorAll('[data-nav]').forEach(el =>
      el.addEventListener('click', () => navigate(el.getAttribute('data-nav'))));

    on('pk-open-here', () => { if (picker.browse) selectProjectPath(picker.browse.path); });
    on('pk-path-go', () => {
      const v = (m.querySelector('#pk-path') || {}).value;
      if (v && v.trim()) selectProjectPath(v.trim());
    });
    on('pk-scan-go', async () => {
      const rootEl = m.querySelector('#pk-scan-root');
      const depthEl = m.querySelector('#pk-scan-depth');
      const root = rootEl ? rootEl.value.trim() : '';
      if (!root) { deps.toast('Indiquez une racine à scanner.'); return; }
      picker.scanRoot = root; picker.busy = true; drawPicker();
      try {
        picker.scan = await deps.api('/api/projects/scan', {
          method: 'POST',
          body: JSON.stringify({ root, depth: parseInt((depthEl && depthEl.value) || '4', 10) || 4 })
        });
      } catch (e) {
        picker.scan = null;
        deps.toast('Scan refusé : ' + deps.esc(String(e.message || e)));
      } finally { picker.busy = false; drawPicker(); }
    });
    on('pk-enrol', async () => {
      const boxes = Array.from(m.querySelectorAll('[data-cand]')).filter(b => b.checked && !b.disabled);
      if (!boxes.length) { deps.toast('Aucun nouveau projet sélectionné.'); return; }
      let added = 0;
      for (const b of boxes) {
        try { await deps.api('/api/projects/add', { method: 'POST', body: JSON.stringify({ path: b.getAttribute('data-cand') }) }); added++; }
        catch (e) { deps.toast('Refusé : ' + deps.esc(String(e.message || e))); }
      }
      REGISTRY = await loadRegistry();
      deps.toast(added + ' projet' + (added > 1 ? 's' : '') + ' enrôlé' + (added > 1 ? 's' : ''), { good: true });
      picker.view = 'list'; drawPicker();
    });
  }

  async function navigate(path) {
    try {
      picker.browse = await deps.api('/api/fs/browse' + (path ? '?path=' + encodeURIComponent(path) : ''));
      picker.browsePath = picker.browse.path;
    } catch (e) {
      deps.toast('Dossier illisible : ' + deps.esc(String(e.message || e)));
    }
    drawPicker();
  }

  async function selectProjectPath(path) {
    if (!path) return;
    if (deps.servedPath && deps.servedPath === path) { closePicker(); return; }
    try {
      const st = await deps.api('/api/projects/select', { method: 'POST', body: JSON.stringify({ path }) });
      closePicker();
      deps.onSelected(st);
    } catch (e) {
      deps.toast('Sélection refusée : ' + deps.esc(String(e.message || e)));
    }
  }

  window.GrimoirePicker = { open: open };
})();
