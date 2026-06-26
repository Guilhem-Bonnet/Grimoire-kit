/* data-loader.js — progressive enhancement.
   Refreshes real project metrics from data/meta.json.
   The static HTML already holds real values, so the vitrine works without this;
   the local "view mode" regenerates meta.json so the figures stay current. */
(function () {
  fetch('data/meta.json', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((meta) => {
      const c = meta.counts || {};
      const bar = document.getElementById('proof-bar-inner');
      if (bar) {
        const items = [
          c.tools && `${c.tools} OUTILS CLI`,
          c.patterns && `${c.patterns} PATTERNS GOUVERNÉS`,
          c.tests && `${c.tests} TESTS`,
          c.archetypes && c.agents && `${c.archetypes} ARCHÉTYPES · ${c.agents} AGENTS`,
          meta.version && `PyPI v${meta.version} · ${(meta.links && meta.links.license) || 'MIT'}`,
          'TRACE COMPLÈTE · JSONL',
        ].filter(Boolean);
        bar.innerHTML = items
          .map((t, i) => (i ? '<span class="proof-sep">·</span>' : '') + `<span class="proof-item">${t}</span>`)
          .join('');
      }
      // Generic fills: <span data-meta="counts.patterns"></span>, data-meta="version", …
      document.querySelectorAll('[data-meta]').forEach((el) => {
        const v = el.getAttribute('data-meta').split('.').reduce((o, k) => (o == null ? o : o[k]), meta);
        if (v !== undefined && v !== null) el.textContent = v;
      });
    })
    .catch(() => { /* keep the static fallback values already in the HTML */ });
})();
