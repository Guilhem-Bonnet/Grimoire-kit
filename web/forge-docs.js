/* forge-docs.js — Documentation surface
   ================================================================
   Rend en direct les fichiers markdown du dépôt Grimoire-kit.
   Aucune copie locale : fetch GitHub à l'exécution (CORS public).
   Dépend de window.marked (chargé avant ce script).
   ================================================================ */
(function () {
  'use strict';

  const REPO = {
    owner: 'Guilhem-Bonnet',
    name: 'Grimoire-kit',
    branch: 'main',
    dir: 'docs',
  };
  const RAW  = (f) => `https://raw.githubusercontent.com/${REPO.owner}/${REPO.name}/${REPO.branch}/${REPO.dir}/${f}`;
  const API  = `https://api.github.com/repos/${REPO.owner}/${REPO.name}/contents/${REPO.dir}?ref=${REPO.branch}`;
  const BLOB = (f) => `https://github.com/${REPO.owner}/${REPO.name}/blob/${REPO.branch}/${REPO.dir}/${f}`;
  const TREE = `https://github.com/${REPO.owner}/${REPO.name}/tree/${REPO.branch}/${REPO.dir}`;

  /* ── Curated taxonomy (labels + ordre). Tout fichier .md du dépôt
       absent de cette carte tombe dans « Autres ». ── */
  const TAXONOMY = [
    { label: 'Démarrage', items: [
      ['index.md', 'Introduction'],
      ['getting-started.md', 'Prise en main'],
      ['onboarding.md', 'Onboarding'],
      ['vscode-setup.md', 'Setup VS Code'],
      ['faq.md', 'FAQ'],
    ]},
    { label: 'Concepts', items: [
      ['concepts.md', 'Concepts fondamentaux'],
      ['archetype-guide.md', 'Guide des archétypes'],
      ['workflow-design-patterns.md', 'Patterns de workflow'],
      ['workflow-taxonomy.md', 'Taxonomie des workflows'],
    ]},
    { label: 'Agents & Mémoire', items: [
      ['creating-agents.md', 'Créer des agents'],
      ['memory-system.md', 'Système de mémoire'],
      ['memory-os-roadmap.md', 'Memory OS · roadmap'],
    ]},
    { label: 'Référence', items: [
      ['cli-reference.md', 'CLI'],
      ['api-reference.md', 'API'],
      ['config-reference.md', 'Configuration'],
      ['grimoire-yaml-reference.md', 'grimoire.yaml'],
      ['observatory-api.md', 'Observatory API'],
    ]},
    { label: 'Intégration', items: [
      ['sdk-guide.md', 'SDK'],
      ['mcp-integration.md', 'MCP'],
      ['plugin-development.md', 'Développement de plugins'],
    ]},
    { label: 'Gouvernance', items: [
      ['governed-controls.md', 'Contrôles gouvernés'],
      ['grimoire-game-runtime-guardrails.md', 'Runtime guardrails'],
      ['adr-001-no-multi-llm.md', 'ADR-001 · No multi-LLM'],
      ['adr-002-semver-policy.md', 'ADR-002 · SemVer'],
    ]},
    { label: 'Agentic Standard', items: [
      ['agentic-standard-final-target.md', 'Cible finale'],
      ['agentic-standard-target-architecture.md', 'Architecture cible'],
      ['agentic-standard-target-plan.md', 'Plan cible'],
      ['agentic-standard-integration.md', 'Intégration'],
      ['agentic-standard-install-by-needs.md', 'Install par besoins'],
      ['agentic-standard-benchmark-corpus-2026Q2.md', 'Corpus benchmark'],
    ]},
    { label: 'Exploitation', items: [
      ['troubleshooting.md', 'Dépannage'],
      ['migration-v2-v3.md', 'Migration v2 → v3'],
      ['changelog.md', 'Changelog'],
    ]},
  ];

  const DEFAULT_DOC = 'index.md';

  /* ── DOM refs ── */
  const $ = (id) => document.getElementById(id);
  const els = {};
  let booted = false;
  let current = null;
  let allItems = [];          // [{file,label,catLabel}]
  let tocLinks = [];

  /* ── Helpers ── */
  function prettify(name) {
    return name.replace(/\.md$/, '')
      .replace(/[-_]/g, ' ')
      .replace(/\b([a-z])/g, (m, c) => c.toUpperCase());
  }
  function slugify(s) {
    return s.toLowerCase().trim()
      .replace(/[àáâä]/g, 'a').replace(/[éèêë]/g, 'e').replace(/[îï]/g, 'i')
      .replace(/[ôö]/g, 'o').replace(/[ûüù]/g, 'u').replace(/ç/g, 'c')
      .replace(/[^\w\s-]/g, '').replace(/\s+/g, '-').replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
  }

  /* ── Build the navigable tree ── */
  async function discover() {
    try {
      const r = await fetch(API, { headers: { Accept: 'application/vnd.github.v3+json' } });
      if (!r.ok) throw new Error('api');
      const j = await r.json();
      return new Set(j.filter(x => x.type === 'file' && x.name.endsWith('.md')).map(x => x.name));
    } catch (e) {
      return null; // rate-limited / offline → on retombe sur la taxonomie
    }
  }

  function composeTree(available) {
    const seen = new Set();
    const groups = [];
    TAXONOMY.forEach(cat => {
      const items = cat.items
        .filter(([f]) => !available || available.has(f))
        .map(([f, label]) => { seen.add(f); return { file: f, label, catLabel: cat.label }; });
      if (items.length) groups.push({ label: cat.label, items });
    });
    if (available) {
      const extras = [...available].filter(f => !seen.has(f)).sort();
      if (extras.length) {
        groups.push({ label: 'Autres', items: extras.map(f => ({ file: f, label: prettify(f), catLabel: 'Autres' })) });
      }
    }
    return groups;
  }

  function renderTree(groups) {
    allItems = [];
    const tree = els.tree;
    tree.innerHTML = '';
    const pick = els.focusPick;
    pick.innerHTML = '';
    groups.forEach(g => {
      const og = document.createElement('optgroup');
      og.label = g.label;
      const grp = document.createElement('div');
      grp.className = 'docs-group';
      const lab = document.createElement('div');
      lab.className = 'docs-group-label';
      lab.textContent = g.label;
      grp.appendChild(lab);
      g.items.forEach(it => {
        allItems.push(it);
        const btn = document.createElement('button');
        btn.className = 'docs-link';
        btn.dataset.file = it.file;
        btn.dataset.cat = it.catLabel;
        btn.dataset.label = it.label;
        btn.innerHTML = `<span class="bullet"></span><span class="lbl">${it.label}</span>`;
        btn.addEventListener('click', () => openDoc(it.file));
        grp.appendChild(btn);

        const opt = document.createElement('option');
        opt.value = it.file; opt.textContent = it.label;
        og.appendChild(opt);
      });
      tree.appendChild(grp);
      pick.appendChild(og);
    });
    pick.onchange = () => openDoc(pick.value);
  }

  /* ── Search filter ── */
  function filterTree(q) {
    q = q.trim().toLowerCase();
    const tree = els.tree;
    let anyVisible = false;
    tree.querySelectorAll('.docs-group').forEach(grp => {
      let groupHit = false;
      grp.querySelectorAll('.docs-link').forEach(btn => {
        const hit = !q ||
          btn.dataset.label.toLowerCase().includes(q) ||
          btn.dataset.file.toLowerCase().includes(q);
        btn.style.display = hit ? '' : 'none';
        if (hit) { groupHit = true; anyVisible = true; }
      });
      grp.style.display = groupHit ? '' : 'none';
    });
    let empty = tree.querySelector('.docs-empty');
    if (!anyVisible) {
      if (!empty) {
        empty = document.createElement('div');
        empty.className = 'docs-empty';
        tree.appendChild(empty);
      }
      empty.textContent = 'Aucun document · ' + q;
      empty.style.display = '';
    } else if (empty) { empty.style.display = 'none'; }
  }

  /* ── Render one document ── */
  function setState(html) { els.article.innerHTML = html; }
  function loadingState() {
    setState('<div class="docs-state"><div class="spin"></div><div>FETCH ' + current + '</div></div>');
  }
  function errorState(file) {
    setState('<div class="docs-state"><div>⚠ Échec du chargement</div>'
      + '<div style="color:var(--ink-muted);font-size:.64rem">' + file + '</div>'
      + '<button class="retry">RÉESSAYER</button></div>');
    const b = els.article.querySelector('.retry');
    if (b) b.addEventListener('click', () => openDoc(file));
  }

  async function openDoc(file) {
    if (!window.marked) return;
    current = file;
    // active states
    els.tree.querySelectorAll('.docs-link').forEach(b =>
      b.classList.toggle('active', b.dataset.file === file));
    const meta = allItems.find(i => i.file === file) || { label: prettify(file), catLabel: '' };
    els.focusPick.value = file;
    setCrumb(meta);
    loadingState();
    els.source.href = BLOB(file);

    let md;
    try {
      const r = await fetch(RAW(file));
      if (!r.ok) throw new Error('raw ' + r.status);
      md = await r.text();
    } catch (e) { errorState(file); els.toc.innerHTML = '<div class="toc-empty">—</div>'; return; }

    let html;
    try { html = window.marked.parse(md, { mangle: false, headerIds: false }); }
    catch (e) { html = '<pre><code>' + md.replace(/</g, '&lt;') + '</code></pre>'; }
    setState(html);
    postProcess(file);
    els.article.scrollTop = 0;
  }

  function setCrumb(meta) {
    els.crumb.innerHTML = '';
    const mk = (txt, cls, fn) => {
      const e = document.createElement(fn ? 'button' : 'span');
      e.className = 'cr' + (cls ? ' ' + cls : '');
      e.textContent = txt;
      if (fn) e.addEventListener('click', fn);
      return e;
    };
    const sep = () => { const s = document.createElement('span'); s.className = 'sep'; s.textContent = '/'; return s; };
    els.crumb.appendChild(mk('DOCUMENTATION', '', () => openDoc(DEFAULT_DOC)));
    if (meta.catLabel) { els.crumb.appendChild(sep()); els.crumb.appendChild(mk(meta.catLabel.toUpperCase())); }
    els.crumb.appendChild(sep());
    els.crumb.appendChild(mk(meta.label, 'now'));
  }

  /* ── Post-render: links, images, headings/TOC, copy buttons ── */
  function postProcess(file) {
    const art = els.article;

    // Rewrite relative images → raw
    art.querySelectorAll('img').forEach(img => {
      const src = img.getAttribute('src') || '';
      if (!/^https?:|^data:/.test(src)) img.src = RAW(src.replace(/^\.?\//, ''));
      img.loading = 'lazy';
    });

    // Rewrite links: relative .md → open in viewer; external → new tab
    art.querySelectorAll('a').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (/^https?:/.test(href)) { a.target = '_blank'; a.rel = 'noopener'; return; }
      if (href.startsWith('#')) {
        a.addEventListener('click', (e) => {
          e.preventDefault();
          const t = art.querySelector('#' + CSS.escape(href.slice(1)));
          if (t) art.scrollTo({ top: t.offsetTop - 12, behavior: 'smooth' });
        });
        return;
      }
      const m = href.split('#')[0].replace(/^\.?\//, '');
      if (m.endsWith('.md')) {
        a.addEventListener('click', (e) => { e.preventDefault(); openDoc(m); });
      }
    });

    // Heading ids + anchors + TOC
    const used = {};
    const toc = [];
    art.querySelectorAll('h2, h3').forEach(h => {
      let id = slugify(h.textContent);
      if (!id) id = 'sec';
      if (used[id] != null) { used[id]++; id = id + '-' + used[id]; } else used[id] = 0;
      h.id = id;
      const a = document.createElement('a');
      a.className = 'anchor'; a.href = '#' + id; a.textContent = '#';
      a.addEventListener('click', (e) => {
        e.preventDefault();
        art.scrollTo({ top: h.offsetTop - 12, behavior: 'smooth' });
        history.replaceState(null, '', '#' + id);
      });
      h.appendChild(a);
      toc.push({ id, text: h.textContent.replace(/#$/, '').trim(), level: h.tagName === 'H3' ? 3 : 2 });
    });
    buildToc(toc);

    // Mermaid diagrams (```mermaid blocks)
    const mermaidNodes = [];
    art.querySelectorAll('pre > code.language-mermaid').forEach(code => {
      const div = document.createElement('div');
      div.className = 'mermaid-diagram';
      div.textContent = code.textContent;
      code.parentElement.replaceWith(div);
      mermaidNodes.push(div);
    });
    if (mermaidNodes.length && window.mermaid) {
      try {
        Promise.resolve(window.mermaid.run({ nodes: mermaidNodes }))
          .then(() => enhanceMermaid(mermaidNodes))
          .catch(() => {});
      } catch (e) { /* laisse le texte brut */ }
    }

    // Coloration syntaxique + label langage + bouton copier
    art.querySelectorAll('pre > code').forEach(code => {
      const pre = code.parentElement;
      if (window.hljs) { try { window.hljs.highlightElement(code); } catch (e) {} }
      const m = code.className.match(/language-([\w+#-]+)/);
      const lang = m ? m[1] : (code.dataset.highlightedLang || '');
      if (lang && lang !== 'plaintext') {
        const tag = document.createElement('span');
        tag.className = 'md-pre-lang';
        tag.textContent = lang;
        pre.appendChild(tag);
      }
      const btn = document.createElement('button');
      btn.className = 'md-copy'; btn.textContent = 'COPIER'; btn.type = 'button';
      btn.addEventListener('click', () => {
        navigator.clipboard.writeText(code.innerText).then(() => {
          btn.textContent = 'COPIÉ'; btn.classList.add('done');
          setTimeout(() => { btn.textContent = 'COPIER'; btn.classList.remove('done'); }, 1400);
        });
      });
      pre.appendChild(btn);
    });
  }

  /* ── Mermaid init (thème Forge) ── */
  function initMermaid() {
    if (!window.mermaid || window.__forgeMermaidInit) return;
    window.__forgeMermaidInit = true;
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'loose',
        theme: 'base',
        fontFamily: 'Geist Mono, ui-monospace, monospace',
        themeVariables: {
          background: '#121418',
          mainBkg: '#1A1D22',
          primaryColor: '#1A1D22',
          primaryBorderColor: 'rgba(255,107,61,0.55)',
          primaryTextColor: '#F6F7F8',
          secondaryColor: '#22262C',
          secondaryBorderColor: 'rgba(255,255,255,0.14)',
          tertiaryColor: '#0B0C0E',
          tertiaryBorderColor: 'rgba(255,255,255,0.08)',
          lineColor: '#7c828b',
          textColor: '#9BA0A8',
          nodeBorder: 'rgba(255,107,61,0.55)',
          clusterBkg: '#0B0C0E',
          clusterBorder: 'rgba(255,255,255,0.10)',
          edgeLabelBackground: '#121418',
          labelBackground: '#121418',
          fontSize: '13px',
        },
      });
    } catch (e) {}
  }

  function buildToc(toc) {
    const nav = els.toc;
    nav.innerHTML = '';
    tocLinks = [];
    if (!toc.length) { nav.innerHTML = '<div class="toc-empty">Pas de sous-titres</div>'; return; }
    toc.forEach(t => {
      const a = document.createElement('a');
      a.href = '#' + t.id; a.textContent = t.text;
      a.className = 'lvl-' + t.level;
      a.dataset.id = t.id;
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const h = els.article.querySelector('#' + CSS.escape(t.id));
        if (h) els.article.scrollTo({ top: h.offsetTop - 12, behavior: 'smooth' });
      });
      nav.appendChild(a);
      tocLinks.push(a);
    });
  }

  /* ══ Mermaid viewer — zoom / pan / plein écran (style GitHub) ══ */
  const MMD_ICONS = {
    minus:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3.5 8h9"/></svg>',
    plus:   '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8 3.5v9M3.5 8h9"/></svg>',
    reset:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 3v2.2h-2.2"/></svg>',
    expand: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2.5H2.5V6M10 2.5h3.5V6M6 13.5H2.5V10M10 13.5h3.5V10"/></svg>',
    close:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  };

  function mmdBtn(icon, title) {
    const b = document.createElement('button');
    b.className = 'mmd-btn'; b.type = 'button'; b.title = title;
    b.setAttribute('aria-label', title); b.innerHTML = icon;
    return b;
  }

  function svgNatural(svg) {
    let nw = 0, nh = 0;
    if (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) {
      nw = svg.viewBox.baseVal.width; nh = svg.viewBox.baseVal.height;
    }
    if (!nw || !nh) { const r = svg.getBoundingClientRect(); nw = r.width || 600; nh = r.height || 360; }
    return [nw, nh];
  }

  function prepSvg(svg, nw, nh) {
    svg.style.maxWidth = 'none';
    svg.setAttribute('width', nw); svg.setAttribute('height', nh);
    svg.style.width = nw + 'px'; svg.style.height = nh + 'px';
  }

  function panZoom(stage, pan, nw, nh, initScale, onChange) {
    let scale = initScale, tx = 0, ty = 0;
    const min = 0.15, max = 16;
    function apply() { pan.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')'; if (onChange) onChange(scale); }
    function centerAt(s) {
      const sr = stage.getBoundingClientRect();
      scale = s;
      tx = (sr.width - nw * scale) / 2;
      ty = (sr.height - nh * scale) / 2;
      if (nh * scale > sr.height - 16) ty = 12;
      apply();
    }
    function zoomAt(factor, cx, cy) {
      const sr = stage.getBoundingClientRect();
      const px = cx - sr.left, py = cy - sr.top;
      const ns = Math.min(max, Math.max(min, scale * factor));
      const k = ns / scale;
      tx = px - (px - tx) * k; ty = py - (py - ty) * k;
      scale = ns; apply();
    }
    stage.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY); }, { passive: false });
    let drag = false, lx = 0, ly = 0;
    stage.addEventListener('pointerdown', (e) => { drag = true; lx = e.clientX; ly = e.clientY; try { stage.setPointerCapture(e.pointerId); } catch (_) {} stage.classList.add('grabbing'); });
    stage.addEventListener('pointermove', (e) => { if (!drag) return; tx += e.clientX - lx; ty += e.clientY - ly; lx = e.clientX; ly = e.clientY; apply(); });
    const end = () => { drag = false; stage.classList.remove('grabbing'); };
    stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
    stage.addEventListener('dblclick', (e) => { zoomAt(1.5, e.clientX, e.clientY); });
    const ctr = () => { const r = stage.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
    centerAt(initScale);
    return {
      zoomIn:  () => { const [a, b] = ctr(); zoomAt(1.25, a, b); },
      zoomOut: () => { const [a, b] = ctr(); zoomAt(1 / 1.25, a, b); },
      reset:   () => centerAt(initScale),
    };
  }

  function enhanceMermaid(nodes) {
    nodes.forEach((node) => {
      const svg = node.querySelector('svg');
      if (!svg || node.dataset.enhanced) return;
      node.dataset.enhanced = '1';
      const [nw, nh] = svgNatural(svg);
      prepSvg(svg, nw, nh);
      const stage = document.createElement('div'); stage.className = 'mmd-stage';
      const pan = document.createElement('div'); pan.className = 'mmd-pan';
      pan.appendChild(svg); stage.appendChild(pan); node.appendChild(stage);
      const sw = (stage.getBoundingClientRect().width || node.getBoundingClientRect().width || 600) - 28;
      const initScale = Math.min(1, sw / nw) || 1;
      const ctl = panZoom(stage, pan, nw, nh, initScale > 0 ? initScale : 1);
      const tb = document.createElement('div'); tb.className = 'mmd-toolbar';
      const zo = mmdBtn(MMD_ICONS.minus, 'Dézoomer');
      const rs = mmdBtn(MMD_ICONS.reset, 'Réinitialiser');
      const zi = mmdBtn(MMD_ICONS.plus, 'Zoomer');
      const fs = mmdBtn(MMD_ICONS.expand, 'Plein écran');
      zo.onclick = () => ctl.zoomOut(); zi.onclick = () => ctl.zoomIn(); rs.onclick = () => ctl.reset();
      fs.onclick = () => openMermaidFullscreen(svg, nw, nh);
      tb.append(zo, rs, zi, fs); node.appendChild(tb);
      const hint = document.createElement('div'); hint.className = 'mmd-hint';
      hint.textContent = 'molette : zoom · glisser : déplacer · double-clic : zoom';
      node.appendChild(hint);
    });
  }

  function openMermaidFullscreen(srcSvg, nw, nh) {
    const ov = document.createElement('div'); ov.className = 'mmd-overlay';
    const bar = document.createElement('div'); bar.className = 'mmd-overlay-bar';
    const title = document.createElement('span'); title.textContent = 'DIAGRAMME · MERMAID';
    const sp = document.createElement('span'); sp.className = 'sp';
    const zo = mmdBtn(MMD_ICONS.minus, 'Dézoomer');
    const zi = mmdBtn(MMD_ICONS.plus, 'Zoomer');
    const rs = mmdBtn(MMD_ICONS.reset, 'Ajuster');
    const zl = document.createElement('span'); zl.className = 'mmd-zoomlabel'; zl.textContent = '100%';
    const close = document.createElement('button'); close.className = 'mmd-close'; close.innerHTML = MMD_ICONS.close + '<span>ESC</span>';
    bar.append(title, sp, zo, zl, zi, rs, close);
    const stage = document.createElement('div'); stage.className = 'mmd-overlay-stage';
    const pan = document.createElement('div'); pan.className = 'mmd-pan';
    const clone = srcSvg.cloneNode(true);
    clone.style.width = nw + 'px'; clone.style.height = nh + 'px'; clone.style.maxWidth = 'none';
    pan.appendChild(clone); stage.appendChild(pan);
    ov.append(bar, stage); document.body.appendChild(ov);
    const sr = stage.getBoundingClientRect();
    const fit = Math.min((sr.width * 0.92) / nw, (sr.height * 0.92) / nh) || 1;
    const ctl = panZoom(stage, pan, nw, nh, fit > 0 ? fit : 1, (s) => { zl.textContent = Math.round(s * 100) + '%'; });
    zo.onclick = () => ctl.zoomOut(); zi.onclick = () => ctl.zoomIn(); rs.onclick = () => ctl.reset();
    function closeOv() { ov.remove(); document.removeEventListener('keydown', onKey); }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); closeOv(); }
      else if (e.key === '+' || e.key === '=') { ctl.zoomIn(); }
      else if (e.key === '-' || e.key === '_') { ctl.zoomOut(); }
      else if (e.key === '0') { ctl.reset(); }
    }
    close.onclick = closeOv;
    document.addEventListener('keydown', onKey);
  }

  /* ── Scroll-spy for TOC ── */
  function onArticleScroll() {
    if (!tocLinks.length) return;
    const art = els.article;
    const top = art.scrollTop + 24;
    let activeId = null;
    tocLinks.forEach(a => {
      const h = art.querySelector('#' + CSS.escape(a.dataset.id));
      if (h && h.offsetTop <= top) activeId = a.dataset.id;
    });
    tocLinks.forEach(a => a.classList.toggle('active', a.dataset.id === activeId));
  }

  /* ── Boot (lazy, on viewport) ── */
  async function boot() {
    if (booted) return;
    booted = true;
    initMermaid();
    els.article.innerHTML = '<div class="docs-state"><div class="spin"></div><div>SYNCHRONISATION /docs</div></div>';
    const available = await discover();
    renderTree(composeTree(available));
    els.search.addEventListener('input', () => filterTree(els.search.value));
    els.article.addEventListener('scroll', () => { window.requestAnimationFrame(onArticleScroll); }, { passive: true });
    openDoc(DEFAULT_DOC);
  }

  function init() {
    const shell = $('docs-shell');
    if (!shell) return;
    els.tree = $('docs-tree');
    els.article = $('docs-article');
    els.toc = $('docs-toc-nav');
    els.crumb = $('docs-crumb');
    els.search = $('docs-search-input');
    els.source = $('docs-source-link');
    els.focusPick = $('docs-focus-select');
    els.source.href = TREE;

    // Boot quand la surface est (ou entre) dans le viewport — robuste même
    // si l'iframe démarre à 0 hauteur (IntersectionObserver peut rater le 1er état).
    let io = null;
    const cleanup = () => {
      window.removeEventListener('scroll', nearView);
      window.removeEventListener('resize', nearView);
      if (io) io.disconnect();
    };
    const nearView = () => {
      const rect = shell.getBoundingClientRect();
      const vh = window.innerHeight || document.documentElement.clientHeight || 800;
      if (rect.top < vh + 240 && rect.bottom > -240) { boot(); cleanup(); }
    };
    if ('IntersectionObserver' in window) {
      io = new IntersectionObserver((ents) => {
        ents.forEach(e => { if (e.isIntersecting) { boot(); cleanup(); } });
      }, { rootMargin: '240px' });
      io.observe(shell);
    }
    window.addEventListener('scroll', nearView, { passive: true });
    window.addEventListener('resize', nearView);
    nearView();

    // Déclenche aussi le boot via l'ancre (lien de nav DOCUMENTATION)
    const hashBoot = () => { if (location.hash.replace('#', '') === 'documentation') boot(); };
    window.addEventListener('hashchange', hashBoot);
    hashBoot();

    // Hook de test / déclenchement manuel
    window.__forgeDocsBoot = boot;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
