/* atelier-nav.js — Chrome du mode Atelier (local) + couche données réelle.
   Anti-corruption layer : consomme l'API locale de `grimoire serve`
   (/api/status, /api/setup, /api/extensions, /api/blueprints) et les JSON
   générés depuis les sources réelles (catalogue-export.json, extensions.json),
   et les normalise vers la forme interne attendue par le Studio (bp2).
   Les caches mémoire rendent les accès synchrones (loadBp, project, ...) ;
   `Atelier.ready` est la barrière d'init que les pages attendent.
   ============================================================== */
(function () {
  'use strict';

  const LS = {
    project:    'grimoire.atelier.project',
    onboarded:  'grimoire.atelier.onboarded',
    artifacts:  'grimoire.atelier.artifacts',
    bpCurrent:  'grimoire.atelier.bp.current'
  };

  function lsRead(key, fallback) {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
    catch (e) { return fallback; }
  }
  function lsWrite(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* quota */ }
  }

  /* ── Palette (alignée sur le viewer v1) ── */
  const FAMILY_COLORS = {
    ORG: '#FF6B3D', ORC: '#6EE7FF', GOV: '#A78BFA', MOD: '#F472B6',
    QUA: '#34D399', KNO: '#FCD34D', RUN: '#F87171', COG: '#8B9DFF'
  };
  const CONTRACT_COLORS = {
    'task-envelope': '#93C5FD', 'handoff-packet': '#34D399',
    'context-pack': '#6EE7FF', 'evidence-pack': '#FCD34D',
    'verification-verdict': '#A78BFA', 'memory-record': '#F472B6',
    'telemetry-event': '#F87171', 'incident-record': '#FF6B3D'
  };
  const COLOR_CYCLE = ['#93C5FD', '#34D399', '#FCD34D', '#A78BFA', '#6EE7FF', '#F472B6', '#F87171', '#8B9DFF'];

  /* Pins par famille — heuristique sémantique en attendant la curation
     par pattern dans le catalogue. `handoff-packet` circule partout ;
     chaque famille émet/consomme ses contrats spécialisés. */
  const FAMILY_PINS = {
    ORG: { in: ['handoff-packet'], out: ['task-envelope'] },
    ORC: { in: ['task-envelope'], out: ['task-envelope', 'handoff-packet'] },
    GOV: { in: ['task-envelope'], out: ['task-envelope'] },
    MOD: { in: ['task-envelope'], out: ['handoff-packet'] },
    COG: { in: ['task-envelope', 'context-pack'], out: ['handoff-packet'] },
    QUA: { in: ['handoff-packet', 'evidence-pack'], out: ['evidence-pack', 'verification-verdict'] },
    KNO: { in: ['handoff-packet', 'evidence-pack'], out: ['context-pack', 'memory-record'] },
    RUN: { in: ['handoff-packet'], out: ['telemetry-event'] }
  };
  const DEFAULT_PINS = { in: ['task-envelope'], out: ['handoff-packet'] };

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(url + ' -> ' + r.status);
    return r.json();
  }
  /* Le site peut vivre à la racine ou sous un sous-chemin (Pages). */
  async function fetchData(name) {
    try { return await fetchJson('data/' + name, { cache: 'no-store' }); }
    catch (e) { return fetchJson('/data/' + name, { cache: 'no-store' }); }
  }
  async function api(path, opts) {
    const o = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {});
    return fetchJson(path, o);
  }

  /* ── État interne (caches remplis par init) ── */
  let ONLINE = false;
  let STATUS = null;          // /api/status
  let SETUP = null;           // /api/setup (artifacts par surface)
  let PROJECT = lsRead(LS.project, null);
  let INSTALLED = [];         // ids d'extensions installées
  const BP_CACHE = new Map(); // id -> état studio

  const Atelier = {
    LS,
    online: false,
    status: null,
    api,
    setupInfo() { return SETUP; },
    esc(s) {
      return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    },

    /* ── Projet (cache sync, vérité = API) ── */
    project() { return PROJECT; },
    async setProject(p) {
      if (ONLINE && p && p.path) {
        try {
          await api('/api/projects/select', { method: 'POST', body: JSON.stringify({ path: p.path }) });
        } catch (e) { /* endpoint absent : on garde la sélection locale */ }
      }
      PROJECT = p; lsWrite(LS.project, p);
      await refreshProjectCaches();
      Atelier.refreshChrome();
    },
    clearProject() {
      PROJECT = null;
      Object.keys(localStorage)
        .filter(k => k.startsWith('grimoire.atelier.'))
        .forEach(k => localStorage.removeItem(k));
      BP_CACHE.clear(); INSTALLED = [];
    },

    /* ── Extensions installées (cache sync + API optimiste) ── */
    installedExts() { return INSTALLED.slice(); },
    installExt(id) {
      if (!INSTALLED.includes(id)) INSTALLED.push(id);
      if (ONLINE) {
        api('/api/extensions/add', { method: 'POST', body: JSON.stringify({ source: id }) })
          .then(r => {
            Atelier.toast('Extension <b>' + Atelier.esc(id) + '</b> installée' +
              ((r.skipped || []).length ? ' <span style="opacity:.7">(' + Atelier.esc(r.skipped.join(', ')) + ')</span>' : ''), { good: true });
          })
          .catch(e => {
            INSTALLED = INSTALLED.filter(x => x !== id);
            Atelier.toast('Installation de <b>' + Atelier.esc(id) + '</b> refusée : ' + Atelier.esc(String(e.message || e)));
            Atelier.refreshChrome();
          });
      }
    },
    removeExt(id) {
      INSTALLED = INSTALLED.filter(x => x !== id);
      if (ONLINE) {
        api('/api/extensions/remove', { method: 'POST', body: JSON.stringify({ id }) })
          .catch(e => Atelier.toast('Désinstallation : ' + Atelier.esc(String(e.message || e))));
      }
    },

    /* ── Blueprints (cache sync préchargé, écriture vers l'API) ── */
    bpList() { return Array.from(BP_CACHE.keys()); },
    loadBp(id) { return BP_CACHE.get(id) || null; },
    saveBp(id, graph) {
      BP_CACHE.set(id, graph);
      if (ONLINE) {
        const payload = Object.assign({ blueprintVersion: 2, id, name: (graph.meta && graph.meta.name) || id }, graph);
        api('/api/blueprints/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(payload) })
          .catch(e => Atelier.toast('Sauvegarde de <b>' + Atelier.esc(id) + '</b> en échec : ' + Atelier.esc(String(e.message || e))));
      }
    },
    deleteBp(id) {
      BP_CACHE.delete(id);
      /* pas d'endpoint delete côté API v1 : la liste locale l'oublie */
    },

    /* ── Artefacts compilés (journal local, vérité = diff git du projet) ── */
    artifacts() { return lsRead(LS.artifacts, []); },
    pushArtifacts(entry) {
      const list = Atelier.artifacts();
      list.unshift(entry);
      lsWrite(LS.artifacts, list.slice(0, 20));
    },

    /* ── Onboarding ── */
    onboarded() { return localStorage.getItem(LS.onboarded) === '1'; },
    setOnboarded(v) {
      if (v) localStorage.setItem(LS.onboarded, '1');
      else localStorage.removeItem(LS.onboarded);
    },

    /* ── Data : catalogue + extensions normalisés depuis les sources réelles ── */
    catalogue: null,
    extensions: null,
    async data() {
      await Atelier.ready;
      if (Atelier.catalogue) return { catalogue: Atelier.catalogue, extensions: Atelier.extensions };

      const [rawCat, rawMarket] = await Promise.all([
        fetchData('catalogue-export.json'),
        fetchData('extensions.json').catch(() => ({ available: [], blueprints: [], candidates: [] }))
      ]);
      /* En atelier, l'API donne les specs complètes des nodes d'extensions. */
      let apiExts = null;
      if (ONLINE) { try { apiExts = await api('/api/extensions'); } catch (e) { apiExts = null; } }

      Atelier.catalogue = normalizeCatalogue(rawCat);
      Atelier.extensions = normalizeExtensions(rawMarket, apiExts);

      Atelier.byRef = {};
      Atelier.catalogue.patterns.forEach(p => { Atelier.byRef[p.ref] = p; });
      Atelier.catById = {};
      Atelier.catalogue.categories.forEach(c => { Atelier.catById[c.id] = c; });
      Atelier.contractById = {};
      Atelier.catalogue.contracts.forEach(c => { Atelier.contractById[c.id] = c; });
      Atelier.extById = {};
      Atelier.extensions.extensions.forEach(e => { Atelier.extById[e.id] = e; });
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
      const found = Atelier.paletteNodes().find(n => n.ref === ref);
      if (found) return found;
      /* Ref hors palette (artefact projet d'un blueprint v1, ext retirée…) :
         spec de secours pour que le Studio reste rendable. */
      const short = String(ref || '').split('/').filter(Boolean).pop() || String(ref);
      return {
        kind: 'pattern', ref, name: short, desc: 'Référence hors catalogue (' + ref + ')',
        cat: 'EXT', in: ['task-envelope'], out: ['handoff-packet'], locked: false
      };
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

  /* ── Normalisations (sources réelles → forme interne du Studio) ── */
  function normalizeCatalogue(cat) {
    const contracts = (cat.contracts || []).map((c, i) => ({
      id: c.id, name: c.name,
      color: CONTRACT_COLORS[c.id] || COLOR_CYCLE[i % COLOR_CYCLE.length],
      desc: c.description || ''
    }));
    const categories = (cat.families || []).map(f => ({
      id: f.id, name: f.name, color: FAMILY_COLORS[f.id] || '#9BA0A8', desc: f.description || ''
    }));
    const patterns = (cat.patterns || []).map(p => {
      const pins = FAMILY_PINS[p.family] || DEFAULT_PINS;
      return {
        ref: p.id, cat: p.family, name: p.name, desc: p.intent || '',
        in: pins.in.slice(), out: pins.out.slice(),
        checks: p.controls || [], maturity: p.maturity || null, docPath: p.docPath || null
      };
    });
    const nameOf = {};
    patterns.forEach(p => { nameOf[p.ref] = p.name; });
    const use_cases = (cat.useCases || []).filter(u => (u.patterns || []).length >= 2).slice(0, 12).map(u => ({
      id: u.id, name: u.name, desc: u.description || '',
      slots: (u.patterns || []).slice(0, 5).map(pid => ({ label: nameOf[pid] || pid, suggest: [pid] }))
    }));
    return {
      version: cat.catalogVersion,
      source: cat.source,
      contracts, categories, patterns, use_cases,
      example_blueprint: exampleBlueprint()
    };
  }

  /* Graine du tutoriel : flow gouverné minimal sur des patterns réels.
     Les edges référencent les nodes par INDEX (contrat instantiateExample). */
  function exampleBlueprint() {
    return {
      id: 'exemple-gouverne', name: 'Délégation gouvernée (exemple)',
      desc: 'Mission cadrée, policy engine, orchestration, preuve.',
      nodes: [
        { ref: 'ORC-02', x: 120, y: 260 }, { ref: 'GOV-01', x: 460, y: 260 },
        { ref: 'ORC-01', x: 800, y: 260 }, { ref: 'QUA-04', x: 1140, y: 260 }
      ],
      edges: [{ from: 0, to: 1 }, { from: 1, to: 2 }, { from: 2, to: 3 }]
    };
  }

  function normalizeExtensions(market, apiExts) {
    const nodesByExt = {};
    if (apiExts && Array.isArray(apiExts.available)) {
      apiExts.available.forEach(e => {
        nodesByExt[e.id] = (e.nodes || []).map(n => ({
          id: n.id, name: n.label, desc: n.description || '',
          in: (n.pins || []).filter(p => p.direction === 'in').map(p => p.contract),
          out: (n.pins || []).filter(p => p.direction === 'out').map(p => p.contract)
        }));
      });
    }
    const permList = perm => {
      const out = [];
      if (!perm) return out;
      if (perm.filesystem && perm.filesystem !== 'none')
        out.push({ scope: 'filesystem · ' + perm.filesystem, why: 'écrit uniquement sous les surfaces gouvernées du projet' });
      if (perm.network) out.push({ scope: 'network', why: 'accès réseau requis par le pont vers l’outil amont' });
      if (perm.memory && perm.memory !== 'none')
        out.push({ scope: 'memory · ' + perm.memory, why: 'accès à la mémoire via l’API Memory OS' });
      (perm.hooks || []).forEach(ev => out.push({ scope: 'hook · ' + ev, why: 'toujours enregistré en mode shadow' }));
      return out;
    };
    const avail = (market.available || []).map(e => ({
      id: e.id, name: e.name, kind: e.kind || null, version: e.version,
      license: e.license, status: 'available', upstream: e.upstream || null,
      desc: e.description || '', patterns: e.patterns || [], requires: e.requires || [],
      permissions: permList(e.permissions),
      artifacts: e.provides || {},
      hooks: ((e.permissions || {}).hooks || []).map(ev => ({ event: ev, desc: 'déclaré par le manifeste', mode: 'shadow' })),
      provides_nodes: nodesByExt[e.id] || []
    }));
    const candidates = (market.candidates || []).map(c => ({
      id: c.id, name: c.name, kind: null, status: 'study',
      upstream: c.upstream || null, desc: c.description || '', patterns: [], provides_nodes: []
    }));
    return {
      registry_version: market.source || 'registry',
      registry: market.registry || null,
      extensions: avail.concat(candidates),
      published_blueprints: (market.blueprints || []).map(b => ({
        id: b.id, name: b.name, desc: b.description || '', requires: b.extensions || []
      }))
    };
  }

  /* Un blueprint v1 (format compilable : pins, edges node.pin) devient un
     état Studio ; un v2 est déjà l'état Studio (champs top en plus, inoffensifs). */
  function upgradeToStudio(bp) {
    if (!bp) return null;
    if (bp.blueprintVersion === 2 || Array.isArray(bp.comments) || bp.view) return bp;
    const nodes = (bp.nodes || []).map((n, i) => ({
      id: n.id, ref: n.ref,
      x: (n.x != null ? n.x : 140 + (i % 4) * 300),
      y: (n.y != null ? n.y : 200 + Math.floor(i / 4) * 180)
    }));
    const edges = (bp.edges || []).map((e, i) => ({
      id: e.id || ('e' + (i + 1)),
      from: String(e.from || '').split('.')[0],
      to: String(e.to || '').split('.')[0],
      contract: e.contract || null
    }));
    return {
      nodes, edges, comments: [],
      view: null,
      meta: { name: bp.name || bp.id, validated: false, simulated: false, compiledAt: null, dirty: false, path: [] }
    };
  }

  /* ── Init : détection API + remplissage des caches ── */
  async function refreshProjectCaches() {
    if (!ONLINE) return;
    try {
      const setup = await api('/api/setup');
      SETUP = setup;
      INSTALLED = Object.keys(setup.extensions || {});
      const ids = (setup.blueprints || []);
      const loaded = await Promise.all(ids.map(id =>
        api('/api/blueprints/' + encodeURIComponent(id)).catch(() => null)));
      BP_CACHE.clear();
      loaded.forEach((bp, i) => {
        const st = upgradeToStudio(bp);
        if (st) BP_CACHE.set(ids[i], st);
      });
    } catch (e) { /* setup indisponible : caches vides */ }
  }

  Atelier.ready = (async function init() {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 1200);
      STATUS = await fetchJson('/api/status', { signal: ctl.signal });
      clearTimeout(t);
      ONLINE = true;
      Atelier.online = true;
      Atelier.status = STATUS;
      const root = STATUS.projectRoot || '';
      PROJECT = { name: root.split('/').filter(Boolean).pop() || 'projet', path: root };
      lsWrite(LS.project, PROJECT);
      await refreshProjectCaches();
    } catch (e) {
      ONLINE = false;
      Atelier.online = false;
      /* Sans API : pas de projet actif — l'atelier affiche le premier
         lancement avec les commandes d'installation réelles. */
      PROJECT = null;
    }
    Atelier.refreshChrome();
    guardToolPages();
  })();

  /* ── Monde courant + garde sans-projet (après init : vérité API) ── */
  const MODE_ATELIER = document.documentElement.classList.contains('mode-atelier');
  const GUARDED = ['blueprints.html', 'observability.html', 'memory.html', 'kanban.html', 'extensions.html'];
  function pageFile() {
    return (location.pathname.replace(/\/$/, '').split('/').pop() || 'atelier.html');
  }
  function guardToolPages() {
    if (MODE_ATELIER && !PROJECT && GUARDED.indexOf(pageFile()) !== -1) {
      location.replace('atelier.html');
    }
  }

  window.Atelier = Atelier;

  /* ══ Injection du chrome ══ */
  const ICONS = {
    atelier:    '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="2" width="12" height="12" rx="2"/><path d="M2 6.5h12M6.5 6.5V14"/></svg>',
    blueprints: '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="4" cy="4.5" r="2"/><circle cx="12" cy="11.5" r="2"/><path d="M6 4.5h4a2 2 0 0 1 2 2v3"/></svg>',
    extensions: '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="7" width="7" height="7" rx="1.5"/><path d="M9 4.5A2.5 2.5 0 1 1 11.5 7H9z"/></svg>',
    patterns:   '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 13V8m5 5V3m5 10v-7"/></svg>',
    docs:       '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 2h6l3 3v9H4z"/><path d="M10 2v3h3"/></svg>',
    observatory:'<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="1.6"/><path d="M8 2v2.5M14 8h-2.5"/></svg>',
    memory:     '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="3" width="10" height="10" rx="2"/><path d="M6 6.5h4M6 9.5h4"/></svg>',
    kanban:     '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 3v10M8 3v6.5M13 3v10"/></svg>',
    labs:       '<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M6.5 2h3M7 2v4.2L3.6 12a1.6 1.6 0 0 0 1.4 2.4h6a1.6 1.6 0 0 0 1.4-2.4L9 6.2V2"/><path d="M5 10.5h6"/></svg>'
  };

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
        ${navItem('labs.html', 'Labs', 'labs')}
        <div class="at-foot">
          <a href="index.html" class="at-nav-i">Site public ↗</a>
        </div>`;

      const btn = document.getElementById('at-project-btn');
      if (btn) { btn.addEventListener('click', () => { location.href = 'atelier.html'; }); }
    }

    const status = document.getElementById('atelier-status');
    if (status) {
      const kitV = STATUS && STATUS.kitVersion ? 'kit v' + Atelier.esc(STATUS.kitVersion) : '';
      const catV = Atelier.catalogue && Atelier.catalogue.version
        ? 'catalogue v' + Atelier.esc(String(Atelier.catalogue.version)) : '';
      const apiChip = ONLINE
        ? `<span><span class="ok">●</span> API locale · :${Atelier.esc(location.port || '80')}</span>`
        : `<span><span style="color:var(--data-red,#F87171)">●</span> API locale indisponible — <code>grimoire serve</code></span>`;
      status.innerHTML = `
        ${apiChip}
        ${kitV ? `<span>${kitV}</span>` : ''}
        ${catV ? `<span>${catV}</span>` : ''}
        <span class="sp"></span>
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
