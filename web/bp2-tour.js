/* bp2-tour.js — Visite « nouveautés du studio »
   Popups spotlight à la première ouverture : skippable, rejouable via ✦
   (toolbar) ou depuis le lexique (?). Ne se superpose jamais à la visite
   gestuelle du premier flow : elle attend que celle-ci soit passée.
   ========================================================================== */
(function () {
  'use strict';
  const LS_SEEN = 'grimoire.atelier.bp2.tour2';
  const $ = s => document.querySelector(s);

  const STEPS = [
    {
      sel: '#bp-palette', side: 'right',
      tag: 'nouveau · équipe concrète',
      title: 'Votre équipe se pose comme un node',
      html: 'En haut de la palette : <b>Orchestrateur</b>, <b>Agent</b>, <b>Sous-agent</b>. Déléguer = tirer un fil. <b>Double-clic sur un agent</b> → sa fiche : <b>outils</b> (ce qu\u2019il a le droit de faire), <b>branchements MCP</b>, <b>skills</b>, <b>hooks</b> (« quand → alors ») et son <b>prompt système</b>.'
    },
    {
      sel: '#bp-crumb', side: 'below',
      tag: 'profondeur C4',
      title: 'Votre flow a des étages',
      html: 'Sélectionnez plusieurs nodes puis <b>⌘G</b> : ils deviennent un <b>sous-flow ◇</b>. Double-clic pour entrer, <b>Échap</b> pour remonter — le fil d\u2019Ariane vous situe (N1, N2…). Un sous-flow expose ses <b>ports</b> : les contrats que son intérieur ne résout pas.'
    },
    {
      sel: '.bp-tabs', side: 'left',
      tag: 'documents & prompts',
      title: 'Chaque node porte ses documents',
      html: 'Mission brief, contrat de complétion, <b>prompt système</b> des agents… Éditez avec la <b>coloration Grimoire</b> et l\u2019autocomplétion <span class="at-kbd">@</span> <span class="at-kbd">#</span> <span class="at-kbd">$</span> <span class="at-kbd">{{</span>. Le lint vérifie les champs requis. L\u2019onglet <b>RÈGLES</b> veille sur le flow et corrige en 1 clic ⚡.'
    },
    {
      sel: '#bp-cost-chip', side: 'below',
      tag: 'coût en tokens',
      title: 'Combien coûtera ce flow ?',
      html: 'Chaque node est estimé — équipement et documents compris, chaque agent avec <b>son</b> modèle. L\u2019onglet <b>COÛT</b> détaille chaque chemin source → puits en tokens et en dollars. <b>◉ COÛT</b> allume la vue chaleur.'
    },
    {
      sel: '#bp-tour-btn', side: 'below',
      tag: 'à retrouver ici',
      title: 'C\u2019est tout — explorez',
      html: 'Toile vide ? <b>« Décrire ce que je veux »</b> : 4 questions sans jargon, l\u2019équipe se construit toute seule. Rejouez ces nouveautés via <b>✦</b>, le lexique vit sous <b>?</b>. Rien ne s\u2019exécute jamais.',
      last: true
    }
  ];

  let dim = null, ring = null, pop = null, idx = 0, active = false;

  function seen() { return localStorage.getItem(LS_SEEN) === '1'; }
  function markSeen() {
    localStorage.setItem(LS_SEEN, '1');
    const b = $('#bp-tour-btn');
    if (b) b.classList.remove('fresh');
  }

  function place() {
    const s = STEPS[idx];
    const t = document.querySelector(s.sel);
    if (!t) { next(); return; }
    const r = t.getBoundingClientRect();
    const pad = 7;
    ring.style.left = (r.left - pad) + 'px';
    ring.style.top = (r.top - pad) + 'px';
    ring.style.width = (r.width + pad * 2) + 'px';
    ring.style.height = (r.height + pad * 2) + 'px';

    const pw = 316, ph = pop.offsetHeight || 200, m = 14;
    let x, y;
    if (s.side === 'right') { x = r.right + m; y = r.top + 40; }
    else if (s.side === 'left') { x = r.left - pw - m; y = r.bottom + m; }
    else { x = r.left; y = r.bottom + m; }
    x = Math.max(10, Math.min(x, innerWidth - pw - 10));
    y = Math.max(10, Math.min(y, innerHeight - ph - 10));
    pop.style.left = x + 'px';
    pop.style.top = y + 'px';
  }

  function draw() {
    const s = STEPS[idx];
    pop.innerHTML = `
      <span class="tag">${s.tag}</span>
      <h4>${s.title}</h4>
      <p>${s.html}</p>
      <div class="acts">
        <div class="dots">${STEPS.map((_, i) => `<i class="${i === idx ? 'on' : ''}"></i>`).join('')}</div>
        ${s.last
          ? `<button class="at-btn sm" id="tp-demo">VOIR L'EXEMPLE ◇</button><button class="at-btn sm acc" id="tp-next">TERMINER</button>`
          : `<button class="at-btn sm ghost" id="tp-skip">passer</button><button class="at-btn sm acc" id="tp-next">SUIVANT →</button>`}
      </div>`;
    const skip = $('#tp-skip');
    if (skip) skip.addEventListener('click', end);
    $('#tp-next').addEventListener('click', next);
    const demo = $('#tp-demo');
    if (demo) demo.addEventListener('click', () => {
      end();
      if (window.BPEditor && BPEditor.loadStudioExample) BPEditor.loadStudioExample();
    });
    place();
    requestAnimationFrame(place); // seconde passe une fois la hauteur connue
  }

  function next() {
    idx++;
    if (idx >= STEPS.length) { end(); return; }
    draw();
  }
  function end() {
    active = false;
    markSeen();
    if (dim) dim.remove(); if (ring) ring.remove(); if (pop) pop.remove();
    dim = ring = pop = null;
  }

  function start() {
    if (active) return;
    active = true;
    idx = 0;
    dim = document.createElement('div'); dim.className = 'tour-dim';
    ring = document.createElement('div'); ring.className = 'tour-ring';
    pop = document.createElement('div'); pop.className = 'tour-pop';
    document.body.append(dim, ring, pop);
    requestAnimationFrame(() => dim.classList.add('show'));
    draw();
  }

  addEventListener('resize', () => { if (active) place(); });
  document.addEventListener('keydown', e => { if (active && e.key === 'Escape') { e.stopPropagation(); end(); } }, true);

  function boot() {
    if (!window.BPEditor || !window.Atelier) { setTimeout(boot, 80); return; }
    const btn = $('#bp-tour-btn');
    if (btn) btn.addEventListener('click', start);
    BPEditor.on('news-replay', start);
    if (seen()) return;
    if (btn) btn.classList.add('fresh');

    const st = BPEditor.state();
    const canStart = Atelier.onboarded() || (st && st.nodes.length);
    if (canStart) { setTimeout(start, 900); return; }
    /* toile vierge + visite gestuelle jamais faite : on laisse la priorité
       au premier flow, puis on présente les nouveautés. */
    let armed = false;
    const arm = () => { if (armed || seen()) return; armed = true; setTimeout(() => { if (!seen()) start(); }, 1600); };
    BPEditor.on('sim-done', arm);
    const poll = setInterval(() => {
      if (seen()) { clearInterval(poll); return; }
      if (Atelier.onboarded()) { clearInterval(poll); arm(); }
    }, 1800);
  }
  boot();
})();
