/* atelier-nav.js — Chrome du mode Atelier (local)
   Sidebar + statusbar + état projet (localStorage) + helpers data.
   Simule la détection de l'API locale : ce prototype EST l'atelier.
   ============================================================== */
(function () {
  'use strict';

  const LS = {
    project:    'grimoire.atelier.project',
    extensions: 'grimoire.atelier.extensions',
    artifacts:  'grimoire.atelier.artifacts',
    bpPrefix:   'grimoire.atelier.bp.',
    bpList:     'grimoire.atelier.bplist',
    onboarded:  'grimoire.atelier.onboarded'
  };

  function read(key, fallback) {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
    catch (e) { return fallback; }
  }
  function write(key, value) { localStorage.setItem(key, JSON.stringify(value)); }

  const Atelier = {
    LS,
    esc(s) {
      return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    },

    /* ── Projet ── */
    project() { return read(LS.project, null); },
    setProject(p) { write(LS.project, p); },
    clearProject() {
      Object.keys(localStorage)
        .filter(k => k.startsWith('grimoire.atelier.'))
        .forEach(k => localStorage.removeItem(k));
    },

    /* ── Extensions installées ── */
    installedExts() { return read(LS.extensions, []); },
    installExt(id) {
      const list = Atelier.installedExts();
      if (!list.includes(id)) { list.push(id); write(LS.extensions, list); }
    },
    removeExt(id) {
      write(LS.extensions, Atelier.installedExts().filter(x => x !== id));
    },

    /* ── Blueprints ── */
    bpList() { return read(LS.bpList, []); },
    saveBp(id, graph) {
      write(LS.bpPrefix + id, graph);
      const list = Atelier.bpList();
      if (!list.includes(id)) { list.push(id); write(LS.bpList, list); }
    },
    loadBp(id) { return read(LS.bpPrefix + id, null); },

    /* ── Artefacts compilés ── */
    artifacts() { return read(LS.artifacts, []); },
    pushArtifacts(entry) {
      const list = Atelier.artifacts();
      list.unshift(entry);
      write(LS.artifacts, list.slice(0, 20));
    },

    /* ── Onboarding ── */
    onboarded() { return localStorage.getItem(LS.onboarded) === '1'; },
    setOnboarded(v) {
      if (v) localStorage.setItem(LS.onboarded, '1');
      else localStorage.removeItem(LS.onboarded);
    },

    /* ── Data ── */
    catalogue: null,
    extensions: null,
    async data() {
      if (!Atelier.catalogue) {
        const [cat, ext] = await Promise.all([
          fetch('data/catalogue.json', { cache: 'no-store' }).then(r => r.json()),
          fetch('data/extensions.json', { cache: 'no-store' }).then(r => r.json())
        ]);
        Atelier.catalogue = cat;
        Atelier.extensions = ext;
        // index rapides
        Atelier.byRef = {};
        cat.patterns.forEach(p => { Atelier.byRef[p.ref] = p; });
        Atelier.catById = {};
        cat.categories.forEach(c => { Atelier.catById[c.id] = c; });
        Atelier.contractById = {};
        cat.contracts.forEach(c => { Atelier.contractById[c.id] = c; });
        Atelier.extById = {};
        ext.extensions.forEach(e => { Atelier.extById[e.id] = e; });
      }
      return { catalogue: Atelier.catalogue, extensions: Atelier.extensions };
    },

    /* Tous les "nodes posables" : patterns + nodes fournis par les extensions.
       Un node d'extension non installée est présent mais locked. */
    paletteNodes() {
      const installed = Atelier.installedExts();
      const items = Atelier.catalogue.patterns.map(p => ({
        kind: 'pattern', ref: p.ref, name: p.name, desc: p.desc,
        cat: p.cat, in: p.in, out: p.out, locked: false
      }));
      (Atelier.extensions.extensions || []).forEach(e => {
        (e.provides_nodes || []).forEach(n => {
          items.push({
            kind: 'ext', ref: n.id, name: n.name, desc: n.desc,
            cat: 'EXT', ext: e.id, extName: e.name,
            in: n.in, out: n.out,
            locked: e.status !== 'available' || !installed.includes(e.id)
          });
        });
      });
      return items;
    },
    nodeSpec(ref) {
      return Atelier.paletteNodes().find(n => n.ref === ref) || null;
    },
    catColor(catId) {
      if (catId === 'EXT') return '#FF6B3D';
      const c = Atelier.catById && Atelier.catById[catId];
      return c ? c.color : '#9BA0A8';
    },
    contractColor(id) {
      const c = Atelier.contractById && Atelier.contractById[id];
      return c ? c.color : '#9BA0A8';
    },

    /* ── Toast ── */
    toast(html, opts) {
      let el = document.getElementById('at-toast');
      if (!el) {
        el = document.createElement('div');
        el.id = 'at-toast'; el.className = 'at-toast';
        document.body.appendChild(el);
      }
      el.className = 'at-toast' + ((opts && opts.good) ? ' good' : '');
      el.innerHTML = html;
      requestAnimationFrame(() => el.classList.add('show'));
      clearTimeout(el._t);
      el._t = setTimeout(() => el.classList.remove('show'), (opts && opts.ms) || 3200);
    }
  };

  window.Atelier = Atelier;

  /* ── Monde courant + garde sans-projet ──
     Le chrome atelier ne s'injecte qu'en mode atelier (grimoire-mode.js).
     Les pages outil sans projet renvoient au premier lancement. */
  const MODE_ATELIER = document.documentElement.classList.contains('mode-atelier');
  const GUARDED = ['blueprints.html', 'observability.html', 'memory.html', 'kanban.html', 'extensions.html'];
  const _pf = (location.pathname.replace(/\/$/, '').split('/').pop() || 'atelier.html');
  if (MODE_ATELIER && !Atelier.project() && GUARDED.indexOf(_pf) !== -1) {
    location.replace('atelier.html');
  }

  /* ══ Injection du chrome ══ */
  const ICONS = {
    atelier:    '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="2" width="12" height="12" rx="2"/><path d="M2 6.5h12M6.5 6.5V14"/></svg>',
    blueprints: '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="4" cy="4.5" r="2"/><circle cx="12" cy="11.5" r="2"/><path d="M6 4.5h4a2 2 0 0 1 2 2v3"/></svg>',
    extensions: '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="7" width="7" height="7" rx="1.5"/><path d="M9 4.5A2.5 2.5 0 1 1 11.5 7H9z"/></svg>',
    patterns:   '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 13V8m5 5V3m5 10v-7"/></svg>',
    docs:       '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 2h6l3 3v9H4z"/><path d="M10 2v3h3"/></svg>',
    observatory:'<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="1.6"/><path d="M8 2v2.5M14 8h-2.5"/></svg>',
    memory:     '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="3" width="10" height="10" rx="2"/><path d="M6 6.5h4M6 9.5h4"/></svg>',
    kanban:     '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 3v10M8 3v6.5M13 3v10"/></svg>'
  };

  function pageFile() {
    return (location.pathname.replace(/\/$/, '').split('/').pop() || 'atelier.html');
  }

  function navItem(href, label, icon, opts) {
    const active = pageFile() === href;
    const dis = opts && opts.disabled;
    return `<a href="${href}" title="${label}" class="at-nav-i${active ? ' active' : ''}${dis ? ' disabled' : ''}" ${dis ? 'aria-disabled="true" tabindex="-1"' : ''}>${ICONS[icon] || ''}${label}</a>`;
  }

  function injectChrome() {
    if (!MODE_ATELIER) return; // mode vitrine : la page porte le chrome forge-nav
    const mount = document.getElementById('atelier-side');
    const proj = Atelier.project();
    const noProj = !proj;

    if (mount) {
      mount.innerHTML = `
        <a class="at-logo" href="index.html">GRIMOIRE&nbsp;<span>KIT</span></a>
        <button class="at-project${noProj ? ' empty' : ''}" id="at-project-btn" title="${noProj ? 'Aucun projet' : Atelier.esc(proj.path || '')}">
          <span class="dot"></span>
          <span class="name">${noProj ? 'aucun projet' : Atelier.esc(proj.name)}</span>
          <span class="caret">▾</span>
        </button>
        ${navItem('atelier.html', 'Atelier', 'atelier')}
        ${navItem('blueprints.html', 'Blueprints', 'blueprints', { disabled: noProj })}
        ${navItem('extensions.html', 'Extensions', 'extensions', { disabled: noProj })}
        <div class="at-nav-sec">Comprendre</div>
        ${navItem('patterns.html', 'Patterns', 'patterns')}
        ${navItem('documentation.html', 'Docs', 'docs')}
        <div class="at-nav-sec">Observer</div>
        ${navItem('observability.html', 'Observatoire', 'observatory', { disabled: noProj })}
        ${navItem('memory.html', 'Mémoire', 'memory', { disabled: noProj })}
        ${navItem('kanban.html', 'Kanban', 'kanban', { disabled: noProj })}
        <div class="at-foot">
          <a href="index.html" class="at-nav-i">Site public ↗</a>
        </div>`;

      const btn = document.getElementById('at-project-btn');
      if (btn) { btn.title = btn.title || 'Atelier'; btn.addEventListener('click', () => { location.href = 'atelier.html'; }); }
    }

    const status = document.getElementById('atelier-status');
    if (status) {
      const proj2 = Atelier.project();
      status.innerHTML = `
        <span><span class="ok">●</span> API locale · :7431 <span style="opacity:.6">(simulée — prototype)</span></span>
        <span>kit v3.16.0</span>
        <span>standard · 36 patterns ✓</span>
        <span class="sp"></span>
        ${proj2 ? `<span>profil ${Atelier.esc(proj2.profile)}</span>` : ''}
        <a href="index.html">site public ↗</a>`;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectChrome);
  } else {
    injectChrome();
  }
  Atelier.refreshChrome = injectChrome;
})();
