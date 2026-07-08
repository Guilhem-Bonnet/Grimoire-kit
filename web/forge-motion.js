/* forge-motion.js — Grimoire Forge signature effects
   IntersectionObserver-based, zero infinite loops (except ambient glow).
   prefers-reduced-motion respected throughout.
   ============================================================ */

(function () {
  'use strict';

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Scroll progress bar ── */
  const progressBar = document.getElementById('scroll-progress');
  if (progressBar) {
    window.addEventListener('scroll', () => {
      const h = document.documentElement;
      const pct = (h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100;
      progressBar.style.width = pct + '%';
    }, { passive: true });
  }

  /* ── Hero blur-in staggered ──
     Elements with [data-hero-reveal] animate in sequence (80ms stagger). */
  const heroItems = document.querySelectorAll('[data-hero-reveal]');
  heroItems.forEach((el, i) => {
    const delay = i * 80;
    if (reduced) {
      el.style.opacity = '1';
      return;
    }
    el.classList.add('reveal-blur');
    setTimeout(() => {
      requestAnimationFrame(() => el.classList.add('visible'));
    }, delay);
  });

  /* ── Generic scroll reveal ── */
  const revealObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const el = entry.target;
        const delay = el.dataset.revealDelay || 0;
        setTimeout(() => el.classList.add('visible'), parseInt(delay));
        revealObs.unobserve(el);
      }
    });
  }, { threshold: 0.15 });

  document.querySelectorAll('.reveal').forEach(el => revealObs.observe(el));

  /* ── Section divider lines (draw left → right on enter) ── */
  const dividerObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('drawn');
        dividerObs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });

  document.querySelectorAll('.section-divider').forEach(el => dividerObs.observe(el));

  /* ── Manifeste border-left reveal ── */
  const manifesteObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const delay = entry.target.dataset.revealDelay || 0;
        setTimeout(() => entry.target.classList.add('revealed'), parseInt(delay));
        manifesteObs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.3 });

  document.querySelectorAll('.manifeste-block').forEach(el => manifesteObs.observe(el));

  /* ── Timeline progress fill on scroll ── */
  const timelineTrack = document.querySelector('.timeline-track-fill');
  const timelineSection = document.getElementById('timeline');
  const timelineDots = document.querySelectorAll('.timeline-dot');

  if (timelineTrack && timelineSection) {
    const timelineObs = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          if (!reduced) {
            let progress = 0;
            const interval = setInterval(() => {
              progress += 2;
              timelineTrack.style.width = Math.min(progress, 100) + '%';
              // Activate dots progressively
              timelineDots.forEach((dot, i) => {
                if (progress >= (i + 1) * 25) dot.classList.add('active');
              });
              if (progress >= 100) clearInterval(interval);
            }, 16);
          } else {
            timelineTrack.style.width = '100%';
            timelineDots.forEach(dot => dot.classList.add('active'));
          }
          timelineObs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.3 });

    timelineObs.observe(timelineSection);
  }

  /* ── CTA primary shimmer at reveal ── */
  const ctaObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && !reduced) {
        const btn = entry.target;
        btn.classList.add('shimmer-once');
        ctaObs.unobserve(btn);
      }
    });
  }, { threshold: 0.8 });

  document.querySelectorAll('.btn-primary').forEach(btn => ctaObs.observe(btn));

  /* ── CTA spark on click ── */
  document.querySelectorAll('.btn-primary').forEach(btn => {
    btn.addEventListener('click', function () {
      if (reduced) return;
      this.classList.remove('spark-active');
      void this.offsetWidth; // reflow
      this.classList.add('spark-active');
      setTimeout(() => this.classList.remove('spark-active'), 200);
    });
  });

  /* ── Cursor-reactive blueprint parallax (hero only, desktop only) ── */
  if (!reduced && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    const heroEl = document.getElementById('hero');
    let lastMove = 0;
    const THROTTLE = 32;

    document.addEventListener('mousemove', (e) => {
      const now = Date.now();
      if (now - lastMove < THROTTLE) return;
      lastMove = now;

      if (!heroEl) return;
      const rect = heroEl.getBoundingClientRect();
      if (e.clientY > rect.bottom) return;

      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const dx = ((e.clientX - cx) / cx) * 4;
      const dy = ((e.clientY - cy) / cy) * 4;

      heroEl.style.setProperty('--grid-dx', dx + 'px');
      heroEl.style.setProperty('--grid-dy', dy + 'px');
      heroEl.style.backgroundPosition = `calc(50% + ${dx}px) calc(50% + ${dy}px)`;
    }, { passive: true });
  }

  /* ── Konami code easter egg ── */
  const KONAMI = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
  let konamiIdx = 0;
  let forgeDone = false;

  document.addEventListener('keydown', (e) => {
    if (forgeDone) return;
    if (e.key === KONAMI[konamiIdx]) {
      konamiIdx++;
      if (konamiIdx === KONAMI.length) {
        konamiIdx = 0;
        forgeDone = true;
        triggerForgeEgg();
      }
    } else {
      konamiIdx = 0;
    }
  });

  function triggerForgeEgg() {
    const forgeWord = document.querySelector('.forge-word');
    if (!forgeWord) return;
    forgeWord.style.transition = 'color 100ms, text-shadow 100ms';
    forgeWord.style.color = '#FF2A00';
    forgeWord.style.textShadow = '0 0 16px #FF2A00, 0 0 32px #FF2A00';

    // Shimmer sweep
    forgeWord.classList.add('egg-shimmer');

    setTimeout(() => {
      forgeWord.style.color = '';
      forgeWord.style.textShadow = '';
      forgeWord.classList.remove('egg-shimmer');
    }, 1800);
  }

  /* ── Metric counters tick on reveal ── */
  const counterObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const el = entry.target;
        const target = parseFloat(el.dataset.target);
        const suffix = el.dataset.suffix || '';
        const prefix = el.dataset.prefix || '';
        const decimals = el.dataset.decimals ? parseInt(el.dataset.decimals) : 0;
        const duration = 1200;
        const start = Date.now();

        if (reduced) {
          el.textContent = prefix + target.toFixed(decimals) + suffix;
          counterObs.unobserve(el);
          return;
        }

        function tick() {
          const elapsed = Date.now() - start;
          const progress = Math.min(elapsed / duration, 1);
          // Ease out
          const eased = 1 - Math.pow(1 - progress, 3);
          const current = target * eased;
          el.textContent = prefix + current.toFixed(decimals) + suffix;
          if (progress < 1) requestAnimationFrame(tick);
        }

        requestAnimationFrame(tick);
        counterObs.unobserve(el);
      }
    });
  }, { threshold: 0.5 });

  document.querySelectorAll('[data-counter]').forEach(el => counterObs.observe(el));

})();
