/* forge-nav.js — Shared nav + footer injection + page transitions
   ================================================================ */
(function () {
  'use strict';

  /* ── Active page detection ── */
  const path = location.pathname.replace(/\/$/, '').split('/').pop() || 'index';

  function isActive(href) {
    if (href === 'index.html' && (path === '' || path === 'index')) return true;
    return path === href.replace('.html', '');
  }

  /* ── Nav HTML ── */
  const navEl = document.getElementById('forge-nav');
  if (navEl) {
    navEl.innerHTML = `
      <div class="container">
        <div class="nav-inner">
          <a href="index.html" class="nav-logo" aria-label="Grimoire Kit — Accueil">
            GRIMOIRE&nbsp;<span class="logo-accent" id="forge-word">KIT</span>
          </a>
          <ul class="nav-links" role="list">
            <li><a href="demo.html"         class="${isActive('demo.html') ? 'active' : ''}">DÉMO</a></li>
            <li><a href="game-ui.html"       class="${isActive('game-ui.html') ? 'active' : ''}">GAME UI</a></li>
            <li><a href="observability.html" class="${isActive('observability.html') ? 'active' : ''}">OBSERVABILITY</a></li>
            <li><a href="memory.html"        class="${isActive('memory.html') ? 'active' : ''}">MEMORY</a></li>
            <li><a href="kanban.html"        class="${isActive('kanban.html') ? 'active' : ''}">KANBAN</a></li>
            <li><a href="documentation.html" class="${isActive('documentation.html') ? 'active' : ''}">DOCUMENTATION</a></li>
            <li><a href="anatomy.html"       class="${isActive('anatomy.html') ? 'active' : ''}">ANATOMIE</a></li>
          </ul>
          <a href="https://github.com/Guilhem-Bonnet/Grimoire-kit" class="nav-cta" target="_blank" rel="noopener">VOIR LE PROJET →</a>
        </div>
      </div>`;
  }

  /* ── Footer HTML ── */
  const footerEl = document.getElementById('forge-footer');
  if (footerEl) {
    footerEl.innerHTML = `
      <div class="container">
        <div class="footer-grid">
          <div>
            <div class="footer-brand-name">GRIMOIRE <span>KIT</span></div>
            <p class="footer-desc">Noyau agentique modulaire.<br>Frappé, pas assemblé.<br>v2026 · runtime typé · open surface protocol.</p>
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
            <h4>SURFACES</h4>
            <ul>
              <li><a href="game-ui.html">Game UI</a></li>
              <li><a href="observability.html">Observatory</a></li>
              <li><a href="memory.html">Memory</a></li>
              <li><a href="kanban.html">Kanban</a></li>
              <li><a href="documentation.html">Documentation</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>PROJET</h4>
            <ul>
              <li><a href="demo.html">Démo</a></li>
              <li><a href="anatomy.html">Anatomie</a></li>
              <li><a href="https://pypi.org/project/grimoire-kit/" target="_blank" rel="noopener">Installer (pip)</a></li>
              <li><a href="https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md" target="_blank" rel="noopener">Roadmap</a></li>
            </ul>
          </div>
        </div>
        <div class="footer-bottom">
          <p>© 2026 GRIMOIRE KIT · runtime/v2026</p>
          <p>BUILD · TRACE · EMIT</p>
        </div>
      </div>`;
  }

  /* ── Nav scroll state ── */
  if (navEl) {
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
