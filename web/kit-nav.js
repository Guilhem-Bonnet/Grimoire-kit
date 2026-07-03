/* kit-nav.js — Nav + footer injection + page transitions + observers
   ================================================================= */
(function () {
  'use strict';

  const path = location.pathname.replace(/\/$/, '').split('/').pop() || 'index';

  function isActive(href) {
    if (href === 'index.html' && (path === '' || path === 'index')) return true;
    return path === href.replace('.html', '');
  }

  /* ── Nav HTML ── */
  const navEl = document.getElementById('kit-nav');
  if (navEl) {
    navEl.innerHTML = `
      <div class="container">
        <div class="nav-inner">
          <a href="index.html" class="nav-logo" aria-label="Grimoire Kit — Accueil">
            GRIMOIRE&nbsp;<span class="logo-accent" id="kit-word">KIT</span>
          </a>
          <ul class="nav-links" role="list">
            <li><a href="docs/" class="${isActive('docs/index.html') ? 'active' : ''}">DOCS</a></li>
            <li><a href="docs/concepts/" class="${isActive('concepts') ? 'active' : ''}">CONCEPTS</a></li>
            <li><a href="docs/api-reference/" class="${isActive('api-reference') ? 'active' : ''}">API</a></li>
            <li><a href="docs/changelog/" class="${isActive('changelog') ? 'active' : ''}">CHANGELOG</a></li>
          </ul>
          <a href="#cta-final" class="nav-cta">INSTALLER →</a>
        </div>
      </div>`;
  }

  /* ── Footer HTML ── */
  const footerEl = document.getElementById('kit-footer');
  if (footerEl) {
    footerEl.innerHTML = `
      <div class="container">
        <div class="footer-grid">
          <div>
            <div class="footer-brand-name">GRIMOIRE <span>KIT</span></div>
            <p class="footer-desc">SDK agentique Python.<br>Composable, traçable, open source.<br>v2026 · pip install grimoire-kit</p>
          </div>
          <div class="footer-col">
            <h4>SDK</h4>
            <ul>
              <li><a href="docs/getting-started/">Démarrage</a></li>
              <li><a href="docs/sdk-guide/">Guide SDK</a></li>
              <li><a href="docs/api-reference/">API Reference</a></li>
              <li><a href="docs/changelog/">Changelog</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>CONCEPTS</h4>
            <ul>
              <li><a href="docs/concepts/">Architecture SOG</a></li>
              <li><a href="docs/memory-system/">Mémoire</a></li>
              <li><a href="docs/creating-agents/">Créer un agent</a></li>
              <li><a href="docs/mcp-integration/">MCP</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>PROJET</h4>
            <ul>
              <li><a href="https://github.com/Guilhem-Bonnet/Grimoire-kit" target="_blank" rel="noopener">GitHub</a></li>
              <li><a href="https://pypi.org/project/grimoire-kit/" target="_blank" rel="noopener">PyPI</a></li>
              <li><a href="docs/faq/">FAQ</a></li>
              <li><a href="docs/troubleshooting/">Dépannage</a></li>
            </ul>
          </div>
        </div>
        <div class="footer-bottom">
          <p>© 2024–2026 GRIMOIRE KIT · SDK/v2026</p>
          <p>INSTALL · ORCHESTRATE · SHIP</p>
        </div>
      </div>`;
  }

  /* ── Nav scroll state ── */
  if (navEl) {
    const onScroll = () => navEl.classList.toggle('scrolled', window.scrollY > 80);
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

  /* ── View transitions ── */
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
  let ki = 0, done = sessionStorage.getItem('kit-egg');
  if (!done) {
    document.addEventListener('keydown', (e) => {
      if (e.key === KONAMI[ki]) { ki++; } else { ki = 0; }
      if (ki === KONAMI.length) {
        ki = 0; sessionStorage.setItem('kit-egg', '1');
        const w = document.getElementById('kit-word');
        if (w) { w.classList.add('kit-egg'); setTimeout(() => w.classList.remove('kit-egg'), 2000); }
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

  /* ── Force-reveal above-fold elements ── */
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

  /* ── Copy install command ── */
  const copyBtn = document.querySelector('.copy-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText('pip install "grimoire-kit[all]"').then(() => {
        const orig = copyBtn.textContent;
        copyBtn.textContent = 'COPIÉ ✓';
        setTimeout(() => { copyBtn.textContent = orig; }, 1500);
      });
    });
  }

})();
