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
          <span id="fp-slot"></span>
          <ul class="nav-links" role="list">
            <li><a href="demo.html"         class="${isActive('demo.html') ? 'active' : ''}">DÉMO</a></li>
            <li><a href="game-ui.html"       class="${isActive('game-ui.html') ? 'active' : ''}">GAME UI</a></li>
            <li><a href="observability.html" class="${isActive('observability.html') ? 'active' : ''}">OBSERVABILITY</a></li>
            <li><a href="memory.html"        class="${isActive('memory.html') ? 'active' : ''}">MEMORY</a></li>
            <li><a href="kanban.html"        class="${isActive('kanban.html') ? 'active' : ''}">KANBAN</a></li>
            <li><a href="documentation.html" class="${isActive('documentation.html') ? 'active' : ''}">DOCUMENTATION</a></li>
            <li><a href="extensions.html"    class="${isActive('extensions.html') ? 'active' : ''}">EXTENSIONS</a></li>
            <li><a href="blueprint.html"     class="${isActive('blueprint.html') ? 'active' : ''}">BLUEPRINT</a></li>
            <li><a href="setup.html"         class="${isActive('setup.html') ? 'active' : ''}">SETUP</a></li>
            <li><a href="anatomy.html"       class="${isActive('anatomy.html') ? 'active' : ''}">ANATOMIE</a></li>
          </ul>
          <a href="https://github.com/Guilhem-Bonnet/Grimoire-kit" class="nav-cta" target="_blank" rel="noopener">VOIR LE PROJET →</a>
        </div>
      </div>`;
  }

  /* ── Sélecteur de projet (multi-projets) — CSS-only dropdown, data-driven ── */
  const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));
  if (navEl && !document.getElementById('forge-projects-css')) {
    const st = document.createElement('style');
    st.id = 'forge-projects-css';
    st.textContent = `
      .forge-projects{--fp-accent:var(--accent,#FF6B3D);--fp-bg:var(--elev-1,#121418);--fp-bg-2:var(--elev-2,#1A1D22);--fp-line:var(--line,rgba(255,255,255,0.08));--fp-line-s:var(--line-strong,rgba(255,255,255,0.14));--fp-ink:var(--ink,#F6F7F8);--fp-soft:var(--ink-soft,#9BA0A8);--fp-muted:var(--ink-muted,#5B6068);--fp-green:var(--data-green,#34D399);--fp-red:var(--data-red,#F87171);--fp-mono:var(--font-mono,'Geist Mono',monospace);position:relative;display:inline-block;user-select:none;margin-left:6px}
      .forge-projects[open]{z-index:60}
      .fp-trigger{list-style:none;display:flex;align-items:center;gap:10px;height:34px;padding:0 10px 0 12px;border:1px solid var(--fp-line-s);border-radius:var(--r-md,8px);background:var(--fp-bg);cursor:pointer;transition:border-color 160ms,background 160ms}
      .fp-trigger::-webkit-details-marker{display:none}
      .fp-trigger:hover,.forge-projects[open] .fp-trigger{border-color:var(--fp-accent);background:var(--fp-bg-2)}
      .fp-trigger-label{display:flex;flex-direction:column;line-height:1;gap:3px}
      .fp-eyebrow{font-family:var(--fp-mono);font-size:.56rem;letter-spacing:.16em;color:var(--fp-muted);text-transform:uppercase}
      .fp-current{font-family:var(--fp-mono);font-size:.74rem;font-weight:600;color:var(--fp-ink);letter-spacing:.02em}
      .fp-caret{width:7px;height:7px;margin-left:2px;border-right:1.5px solid var(--fp-soft);border-bottom:1.5px solid var(--fp-soft);transform:rotate(45deg) translateY(-1px);transition:transform 200ms,border-color 160ms}
      .forge-projects[open] .fp-caret{transform:rotate(-135deg) translateY(-1px);border-color:var(--fp-accent)}
      .fp-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--fp-muted)}
      .fp-dot--pass{background:var(--fp-green);box-shadow:0 0 0 3px color-mix(in oklab,var(--fp-green) 22%,transparent)}
      .fp-dot--fail{background:var(--fp-red);box-shadow:0 0 0 3px color-mix(in oklab,var(--fp-red) 22%,transparent)}
      .fp-panel{position:absolute;top:calc(100% + 8px);left:0;width:320px;max-width:88vw;background:var(--fp-bg);border:1px solid var(--fp-line-s);border-radius:var(--r-md,8px);box-shadow:0 18px 48px rgba(0,0,0,.55);padding:6px;animation:fp-in 180ms cubic-bezier(0,0,.2,1)}
      @keyframes fp-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
      @media (prefers-reduced-motion:reduce){.fp-panel{animation:none}}
      .fp-panel-head{display:flex;align-items:center;justify-content:space-between;padding:8px 10px 10px;font-family:var(--fp-mono);font-size:.58rem;letter-spacing:.16em;color:var(--fp-muted);text-transform:uppercase}
      .fp-count{font-size:.6rem;letter-spacing:0;color:var(--fp-soft);border:1px solid var(--fp-line);border-radius:999px;padding:1px 7px}
      .fp-item{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:var(--r-sm,4px);text-decoration:none;border:1px solid transparent;transition:background 140ms,border-color 140ms}
      .fp-item:hover{background:var(--fp-bg-2)}
      .fp-item.is-active{background:var(--accent-soft,rgba(255,107,61,.12));border-color:color-mix(in oklab,var(--fp-accent) 40%,transparent)}
      .fp-meta{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}
      .fp-name{font-family:var(--font-sans,system-ui);font-size:.82rem;font-weight:500;color:var(--fp-ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .fp-item.is-active .fp-name{color:var(--fp-accent)}
      .fp-slug{font-family:var(--fp-mono);font-size:.62rem;letter-spacing:.04em;color:var(--fp-muted)}
      .fp-stats{display:flex;align-items:center;gap:10px;flex:none}
      .fp-cost{font-family:var(--fp-mono);font-size:.7rem;font-weight:500;color:var(--fp-soft);font-variant-numeric:tabular-nums}
      .fp-af{font-family:var(--fp-mono);font-size:.72rem;font-weight:600;color:var(--fp-ink);font-variant-numeric:tabular-nums;display:inline-flex;align-items:baseline;gap:2px;padding:2px 7px;border-radius:var(--r-sm,4px);border:1px solid var(--fp-line);background:rgba(255,255,255,.02)}
      .fp-af small{font-size:.5rem;letter-spacing:.08em;color:var(--fp-muted);font-weight:500}
      .fp-af[data-grade="a"]{color:var(--fp-green);border-color:color-mix(in oklab,var(--fp-green) 30%,transparent)}
      .fp-af[data-grade="b"]{color:var(--data-amber,#FCD34D);border-color:color-mix(in oklab,var(--data-amber,#FCD34D) 30%,transparent)}
      .fp-af[data-grade="c"]{color:var(--fp-red);border-color:color-mix(in oklab,var(--fp-red) 30%,transparent)}
      .fp-af[data-grade] small{color:inherit;opacity:.7}
      .fp-portfolio{display:flex;align-items:center;justify-content:space-between;margin-top:6px;padding:11px 12px;border-top:1px solid var(--fp-line);font-family:var(--fp-mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--fp-soft);text-decoration:none;transition:color 140ms}
      .fp-portfolio:hover{color:var(--fp-accent)}
      .fp-portfolio-arrow{transition:transform 180ms}
      .fp-portfolio:hover .fp-portfolio-arrow{transform:translateX(4px)}
      .forge-projects--solo{display:inline-flex;align-items:center;gap:10px;height:34px;padding:0 12px;border:1px solid var(--fp-line);border-radius:var(--r-md,8px);background:transparent;margin-left:6px}
      @media (max-width:880px){.fp-eyebrow{display:none}.fp-panel{width:280px}}`;
    document.head.appendChild(st);

    const grade = (s) => (s == null ? '' : (s >= 85 ? 'a' : (s >= 70 ? 'b' : 'c')));
    const dotCls = (ci) => (ci === 'failure' ? 'fp-dot--fail' : 'fp-dot--pass');
    // Env : vitrine publique (features de pilotage bloquées) vs cockpit local.
    // Un site servi en localhost est TOUJOURS local — même s'il affiche le
    // snapshot démo committé (env=vitrine) tant qu'aucun projet réel n'est ajouté.
    const isLocal = /^(localhost|127\.|0\.0\.0\.0)/.test(location.hostname) ||
      location.hostname === '::1' || location.hostname.endsWith('.local') || location.hostname === '';
    const hostVitrine = /github\.io$/.test(location.hostname);
    document.documentElement.dataset.env = hostVitrine ? 'vitrine' : 'local';
    fetch('data/projects.json', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((idx) => {
        if (idx && idx.env && !isLocal) document.documentElement.dataset.env = idx.env;
        const slot = document.getElementById('fp-slot');
        if (!slot || !idx || !(idx.projects || []).length) return;
        const cur = new URLSearchParams(location.search).get('project') || idx.primary;
        const active = idx.projects.find((p) => p.slug === cur) || idx.projects[0];
        const soloLabel = `<span class="fp-dot ${dotCls(active.ci_status)}"></span><span class="fp-trigger-label"><span class="fp-eyebrow">PROJET</span><span class="fp-current">${_esc(active.name)}</span></span>`;
        if (idx.projects.length <= 1) {
          slot.innerHTML = `<span class="forge-projects forge-projects--solo">${soloLabel}</span>`;
          return;
        }
        const items = idx.projects.map((p) => {
          const af = p.antifragile;
          return `<a href="?project=${encodeURIComponent(p.slug)}" class="fp-item ${p.slug === cur ? 'is-active' : ''}" role="menuitem"${p.slug === cur ? ' aria-current="true"' : ''}>` +
            `<span class="fp-dot ${dotCls(p.ci_status)}"></span>` +
            `<span class="fp-meta"><span class="fp-name">${_esc(p.name)}</span><span class="fp-slug">${_esc(p.slug)}</span></span>` +
            `<span class="fp-stats"><span class="fp-cost">$${(p.total_cost_usd || 0).toFixed(2)}</span>` +
            (af != null ? `<span class="fp-af" data-grade="${grade(af)}">${Math.round(af)}<small>AF</small></span>` : '') +
            `</span></a>`;
        }).join('');
        slot.innerHTML = `<details class="forge-projects" id="forge-projects"><summary class="fp-trigger">${soloLabel}<span class="fp-caret"></span></summary>` +
          `<div class="fp-panel" role="menu"><div class="fp-panel-head"><span>SÉLECTIONNER UN PROJET</span><span class="fp-count">${idx.projects.length}</span></div>` +
          items + `<a href="portfolio.html" class="fp-portfolio" role="menuitem">Portefeuille <span class="fp-portfolio-arrow">→</span></a></div></details>`;
      }).catch(() => {});
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
