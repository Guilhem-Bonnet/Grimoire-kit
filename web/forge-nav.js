/* forge-nav.js — Shared nav + footer injection + page transitions
   ================================================================ */
(function () {
  'use strict';

  /* Mode atelier : le chrome vitrine (topbar + footer) ne s'injecte pas.
     Les helpers de motion (reveal, dividers, compteurs) restent actifs. */
  const MODE_ATELIER = document.documentElement.classList.contains('mode-atelier');

  /* ── Active page detection ── */
  const path = location.pathname.replace(/\/$/, '').split('/').pop() || 'index';

  function isActive(href) {
    if (href === 'index.html' && (path === '' || path === 'index')) return true;
    return path === href.replace('.html', '');
  }

  /* ── Nav HTML ── */
  const navEl = document.getElementById('forge-nav');
  if (navEl && !MODE_ATELIER) {
    const napStyle = document.createElement('style');
    napStyle.textContent = `
      .nav-atelier { position: relative; }
      .nav-atelier .nav-cta { background: var(--accent); color: var(--bg); cursor: pointer; font-weight: 600; }
      .nav-atelier .nav-cta:hover { filter: brightness(1.08); }
      .nav-atelier-panel {
        position: absolute; top: calc(100% + 12px); right: 0; width: 300px;
        background: var(--elev-2); border: 1px solid var(--line-strong); border-radius: 8px;
        padding: 16px; box-shadow: 0 18px 48px rgba(0,0,0,0.55); z-index: 950; }
      .nav-atelier-panel .nap-lbl { display: block; font-family: var(--font-mono); font-size: 0.56rem; letter-spacing: 0.14em; color: var(--ink-muted); margin-bottom: 8px; }
      .nav-atelier-panel code { display: block; font-family: var(--font-mono); font-size: 0.68rem; color: var(--ink); background: var(--elev-1); border: 1px solid var(--line); border-radius: 4px; padding: 7px 10px; margin-bottom: 6px; }
      .nav-atelier-panel p { font-size: 0.7rem; color: var(--ink-soft); line-height: 1.55; margin: 10px 0 12px; }
      .nav-atelier-panel p b { color: var(--ink); }
      .nav-atelier-panel .nap-open { display: block; text-align: center; font-family: var(--font-mono); font-size: 0.66rem; font-weight: 600; background: var(--accent); color: var(--bg); padding: 9px 12px; border-radius: 4px; text-decoration: none; margin-bottom: 10px; }
      .nav-atelier-panel .nap-gh { font-family: var(--font-mono); font-size: 0.6rem; color: var(--ink-muted); text-decoration: none; }
      .nav-atelier-panel .nap-gh:hover { color: var(--ink); }`;
    document.head.appendChild(napStyle);

    navEl.innerHTML = `
      <div class="container">
        <div class="nav-inner">
          <div class="nav-left">
            <a href="index.html" class="nav-logo" aria-label="Grimoire Kit — Accueil">
              GRIMOIRE&nbsp;<span class="logo-accent" id="forge-word">KIT</span>
            </a>
            <div id="forge-projects-mount"></div>
          </div>
          <ul class="nav-links" role="list">
            <li><a href="demo.html"          class="${isActive('demo.html') ? 'active' : ''}">DÉMO</a></li>
            <li><a href="patterns.html"      class="${isActive('patterns.html') ? 'active' : ''}">PATTERNS</a></li>
            <li><a href="extensions.html"    class="${isActive('extensions.html') ? 'active' : ''}">EXTENSIONS</a></li>
            <li><a href="portfolio.html"     class="${isActive('portfolio.html') ? 'active' : ''}">PORTEFEUILLE</a></li>
            <li><a href="documentation.html" class="${isActive('documentation.html') ? 'active' : ''}">DOCS</a></li>
          </ul>
          <div class="nav-atelier" id="nav-atelier">
            <button class="nav-cta" id="nav-atelier-btn" type="button">LANCER L'ATELIER →</button>
            <div class="nav-atelier-panel" id="nav-atelier-panel" hidden>
              <span class="nap-lbl">DANS VOTRE TERMINAL</span>
              <code>pip install grimoire-kit</code>
              <code>grimoire serve</code>
              <p>Le même site s'ouvre en <b>mode atelier</b> : projet, éditeur de blueprints, extensions — l'API locale active les capacités.</p>
              <a class="nap-open" href="atelier.html">OUVRIR L'ATELIER →</a>
              <a class="nap-gh" href="https://github.com/Guilhem-Bonnet/Grimoire-kit" target="_blank" rel="noopener">GitHub ↗</a>
            </div>
          </div>
        </div>
      </div>`;

    const napBtn = document.getElementById('nav-atelier-btn');
    const napPanel = document.getElementById('nav-atelier-panel');
    if (napBtn && napPanel) {
      napBtn.addEventListener('click', (e) => { e.stopPropagation(); napPanel.hidden = !napPanel.hidden; });
      document.addEventListener('click', (e) => { if (!napPanel.hidden && !e.target.closest('#nav-atelier')) napPanel.hidden = true; });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !napPanel.hidden) napPanel.hidden = true; });
    }

    /* Tout lien .js-launch-atelier ouvre le seuil — l'unique passage vers l'atelier */
    document.addEventListener('click', (e) => {
      const t = e.target.closest('.js-launch-atelier');
      if (!t) return;
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      if (napPanel) napPanel.hidden = false;
    });
  }

  /* ── Project selector (data-driven, monté dans la nav) ── */
  (function mountProjectSelector() {
    const mount = document.getElementById('forge-projects-mount');
    if (!mount) return;

    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const ciClass = (ci) => (ci === 'pass' || ci === 'green' || ci === 'ok') ? 'fp-dot--pass'
      : (ci === 'fail' || ci === 'red' || ci === 'ko') ? 'fp-dot--fail' : '';
    const afGrade = (n) => (n >= 85 ? 'a' : n >= 78 ? 'b' : 'c');

    fetch('data/projects.json', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        const projects = (data && data.projects) || [];
        if (!projects.length) { mount.remove(); return; }

        const current = new URLSearchParams(location.search).get('project') || data.active;
        const cur = projects.find((p) => p.slug === current) || projects[0];
        const portfolio = data.portfolio_url || 'portfolio.html';

        // ── Mono-projet : étiquette compacte, pas de dropdown ──
        if (projects.length === 1) {
          mount.outerHTML = `
            <span class="forge-projects forge-projects--solo">
              <span class="fp-dot ${ciClass(cur.ci)}" title="${esc(cur.ci_label || '')}"></span>
              <span class="fp-trigger-label">
                <span class="fp-eyebrow">PROJET</span>
                <span class="fp-current">${esc(cur.name)}</span>
              </span>
            </span>`;
          return;
        }

        // ── Multi-projets : dropdown ──
        const items = projects.map((p) => {
          const active = p.slug === cur.slug;
          return `
            <a href="?project=${esc(p.slug)}" class="fp-item${active ? ' is-active' : ''}" role="menuitem"${active ? ' aria-current="true"' : ''}>
              <span class="fp-dot ${ciClass(p.ci)}" title="${esc(p.ci_label || '')}"></span>
              <span class="fp-meta">
                <span class="fp-name">${esc(p.name)}</span>
                <span class="fp-slug">${esc(p.slug)}</span>
              </span>
              <span class="fp-stats">
                <span class="fp-cost" title="Coût run">${esc(p.cost)}</span>
                <span class="fp-af" data-grade="${afGrade(+p.antifragile || 0)}" title="Score antifragile">${esc(p.antifragile)}<small>AF</small></span>
              </span>
            </a>`;
        }).join('');

        mount.outerHTML = `
          <details class="forge-projects" id="forge-projects">
            <summary class="fp-trigger" aria-label="Changer de projet">
              <span class="fp-dot ${ciClass(cur.ci)}" aria-hidden="true"></span>
              <span class="fp-trigger-label">
                <span class="fp-eyebrow">PROJET</span>
                <span class="fp-current">${esc(cur.name)}</span>
              </span>
              <span class="fp-caret" aria-hidden="true"></span>
            </summary>
            <div class="fp-panel" role="menu">
              <div class="fp-panel-head">
                <span>SÉLECTIONNER UN PROJET</span>
                <span class="fp-count">${projects.length}</span>
              </div>
              ${items}
              <a href="${esc(portfolio)}" class="fp-portfolio" role="menuitem">
                Portefeuille
                <span class="fp-portfolio-arrow" aria-hidden="true">&rarr;</span>
              </a>
            </div>
          </details>`;

        // Fermer au clic extérieur / Échap
        const det = document.getElementById('forge-projects');
        if (det) {
          document.addEventListener('click', (e) => {
            if (det.open && !det.contains(e.target)) det.open = false;
          });
          document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && det.open) det.open = false;
          });
        }
      })
      .catch(() => { mount.remove(); });
  })();

  /* ── Footer HTML ── */
  const footerEl = document.getElementById('forge-footer');
  if (footerEl && !MODE_ATELIER) {
    footerEl.innerHTML = `
      <div class="container">
        <div class="footer-grid">
          <div>
            <div class="footer-brand-name">GRIMOIRE <span>KIT</span></div>
            <p class="footer-desc">Noyau agentique modulaire.<br>Composé, gouverné, tracé.<br>v2026 · runtime typé · open surface protocol.</p>
          </div>
          <div class="footer-col">
            <h4>RUNTIME</h4>
            <ul>
              <li><a href="documentation.html">Documentation</a></li>
              <li><a href="https://github.com/Guilhem-Bonnet/Grimoire-kit" target="_blank" rel="noopener">GitHub</a></li>
              <li><a href="https://pypi.org/project/grimoire-kit/" target="_blank" rel="noopener">PyPI</a></li>
              <li><a href="https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md" target="_blank" rel="noopener">Changelog</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>EXPLORER</h4>
            <ul>
              <li><a href="demo.html">Démo</a></li>
              <li><a href="patterns.html">Patterns</a></li>
              <li><a href="extensions.html">Extensions</a></li>
              <li><a href="portfolio.html">Portefeuille</a></li>
              <li><a href="game-ui.html">Labs · Game UI</a></li>
              <li><a href="anatomy.html">Labs · Anatomie</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>ATELIER</h4>
            <ul>
              <li><a href="#" class="js-launch-atelier" style="color:var(--accent)">Lancer l'atelier →</a></li>
            </ul>
            <p style="font-family:var(--font-mono);font-size:0.6rem;color:var(--ink-muted);line-height:1.7;margin-top:10px">grimoire serve — le même site<br>s'ouvre en mode atelier :<br>éditeur, surfaces, projet.</p>
          </div>
        </div>
        <div class="footer-bottom">
          <p>© 2026 GRIMOIRE KIT · runtime/v2026</p>
          <p>BUILD · TRACE · EMIT</p>
        </div>
      </div>`;
  }

  /* ── Nav scroll state ── */
  if (navEl && !MODE_ATELIER) {
    const onScroll = () => {
      navEl.classList.toggle('scrolled', window.scrollY > 80);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ── Scroll progress bar ── */
  const bar = document.getElementById('scroll-progress');
  if (bar) {
    window.addEventListener('scroll', () => {
      const h = document.documentElement;
      bar.style.width = ((h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100) + '%';
    }, { passive: true });
  }

  /* ── View transitions (native API, fade + 8px slide) ── */
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]');
    if (!link) return;
    const href = link.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('http') || link.target === '_blank') return;
    if (!document.startViewTransition) return;
    e.preventDefault();
    document.startViewTransition(() => { window.location.href = href; });
  });

  /* ── Konami easter egg ── */
  const KONAMI = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
  let ki = 0, done = sessionStorage.getItem('forge-egg');
  if (!done) {
    document.addEventListener('keydown', (e) => {
      if (e.key === KONAMI[ki]) { ki++; } else { ki = 0; }
      if (ki === KONAMI.length) {
        ki = 0; sessionStorage.setItem('forge-egg', '1');
        const w = document.getElementById('forge-word');
        if (w) { w.classList.add('forge-egg'); setTimeout(() => w.classList.remove('forge-egg'), 2000); }
      }
    });
  }

  /* ── Generic reveal observer ── */
  const ro = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const delay = +(el.dataset.delay || 0);
      setTimeout(() => el.classList.add('visible'), delay);
      ro.unobserve(el);
    });
  }, { threshold: 0.05 });
  document.querySelectorAll('.reveal').forEach(el => ro.observe(el));

  /* ── Force-reveal above-fold elements immediately ── */
  function forceRevealViewport() {
    document.querySelectorAll('.reveal:not(.visible)').forEach(el => {
      const rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0) {
        const delay = +(el.dataset.delay || 0);
        setTimeout(() => el.classList.add('visible'), delay);
        ro.unobserve(el);
      }
    });
  }
  // Run immediately + after fonts/layout settle
  forceRevealViewport();
  setTimeout(forceRevealViewport, 100);
  setTimeout(forceRevealViewport, 400);

  /* ── Divider observer ── */
  const divObs = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('drawn'); divObs.unobserve(e.target); } });
  }, { threshold: 0.5 });
  document.querySelectorAll('.divider').forEach(el => divObs.observe(el));

  /* ── Manifeste border-left ── */
  const mObs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (!e.isIntersecting) return;
      const delay = +(e.target.dataset.delay || 0);
      setTimeout(() => e.target.classList.add('lit'), delay);
      mObs.unobserve(e.target);
    });
  }, { threshold: 0.3 });
  document.querySelectorAll('.manifeste-block').forEach(el => mObs.observe(el));

  /* ── Counter animation ── */
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cObs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (!e.isIntersecting) return;
      const el = e.target;
      const target = parseFloat(el.dataset.target || '0');
      const suffix = el.dataset.suffix || '';
      const decimals = +(el.dataset.dec || 0);
      cObs.unobserve(el);
      if (reduced) { el.textContent = target.toFixed(decimals) + suffix; return; }
      const t0 = Date.now();
      (function tick() {
        const p = Math.min((Date.now() - t0) / 1200, 1);
        const v = target * (1 - Math.pow(1 - p, 3));
        el.textContent = v.toFixed(decimals) + suffix;
        if (p < 1) requestAnimationFrame(tick);
      })();
    });
  }, { threshold: 0.5 });
  document.querySelectorAll('[data-counter]').forEach(el => cObs.observe(el));

  /* ── CTA shimmer on reveal ── */
  const sObs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (!e.isIntersecting || reduced) return;
      e.target.classList.add('shimmer-once');
      setTimeout(() => e.target.classList.remove('shimmer-once'), 700);
      sObs.unobserve(e.target);
    });
  }, { threshold: 0.8 });
  document.querySelectorAll('.btn-primary').forEach(el => sObs.observe(el));

  /* ── Timeline progress ── */
  const tl = document.querySelector('.timeline-track-fill');
  const tls = document.getElementById('section-timeline');
  if (tl && tls) {
    new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      const dots = document.querySelectorAll('.timeline-dot');
      if (reduced) { tl.style.width = '100%'; dots.forEach(d => d.classList.add('active')); return; }
      let p = 0;
      const iv = setInterval(() => {
        p += 1.5; tl.style.width = Math.min(p, 100) + '%';
        dots.forEach((d, i) => { if (p >= (i + 1) * 25) d.classList.add('active'); });
        if (p >= 100) clearInterval(iv);
      }, 16);
    }, { threshold: 0.3 }).observe(tls);
  }

  /* ── Hero blur-in stagger ── */
  document.querySelectorAll('[data-hero]').forEach((el, i) => {
    el.classList.add('reveal-blur');
    if (reduced) { el.classList.add('visible'); return; }
    setTimeout(() => requestAnimationFrame(() => el.classList.add('visible')), i * 90);
  });

})();
