/* blueprint-onboarding.js — Premier flow guidé (< 2 min)
   Règles : gestes réels sur le vrai éditeur · jamais bloquant ·
   le vocabulaire arrive APRÈS le geste réussi · rejouable, jamais réimposé.
   ======================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);

  function whenReady(cb) {
    if (window.BPEditor && window.Atelier) return cb();
    setTimeout(() => whenReady(cb), 60);
  }

  whenReady(() => {
    const E = window.BPEditor;
    const canvas = $('#bp-canvas');
    let tour = null;          // {step}
    let emptyOverlay = null;
    let dismissedFor = null;  // bpId où l'état vide a été volontairement fermé

    /* ══ Étapes ══ */
    const STEPS = [
      {
        label: '1/4 · ~15 s',
        html: 'Tout flow gouverné finit par <b>une preuve et une porte</b>. Posez la preuve : <b>QUA-01 — Evidence Pack</b>. La recherche est déjà remplie — cliquez la carte dans la palette.',
        setup() { E.palette.search('QUA-01'); E.pulsePalette('QUA-01'); },
        done: ev => ev.type === 'node-added' && ev.data.ref === 'QUA-01'
      },
      {
        label: '2/4 · ~20 s',
        html: 'Une preuve sans porte ne décide rien. La palette montre maintenant <b>ce qui se branche</b> sur sa sortie — posez <b>GOV-01 — Completion Contract</b>.',
        setup() { E.palette.search(''); E.pulsePalette('GOV-01'); },
        done: ev => ev.type === 'node-added' && ev.data.ref === 'GOV-01'
      },
      {
        label: '3/4 · ~15 s',
        html: 'Reliez : attrapez le pin <b>sortie ○ evidence-pack</b> de QUA-01 et lâchez-le sur l\u2019<b>entrée ○</b> de GOV-01. (Lâché dans le vide, le fil ouvre le menu des nodes compatibles.)',
        setup() { E.pulsePin('out', 'evidence-pack'); },
        done: ev => ev.type === 'edge-added'
      },
      {
        label: '4/4 · ~20 s',
        html: 'Simulez : le plan des contrats se déroule, étape par étape, dans l\u2019inspecteur. <b>Rien ne s\u2019exécute</b> — c\u2019est tout l\u2019intérêt.',
        setup() { E.pulse('#bp-simulate'); },
        done: ev => ev.type === 'sim-done'
      }
    ];

    /* ══ Coach ══ */
    function coachEl() {
      let el = $('#bp-coach');
      if (!el) {
        el = document.createElement('div');
        el.id = 'bp-coach'; el.className = 'bp-coach';
        canvas.appendChild(el);
      }
      return el;
    }
    function removeCoach() { const el = $('#bp-coach'); if (el) el.remove(); }

    function renderStep() {
      if (!tour) return;
      const s = STEPS[tour.step];
      const el = coachEl();
      el.innerHTML = `
        <span class="step">Premier flow · ${s.label}</span>
        <p>${s.html}</p>
        <div class="acts">
          <button class="at-btn sm" id="coach-show">MONTRE-MOI</button>
          <button class="at-btn sm ghost" id="coach-skip">PASSER LA VISITE</button>
        </div>`;
      $('#coach-show').addEventListener('click', () => s.setup());
      $('#coach-skip').addEventListener('click', endTour);
      $('#bp-progress-slot').innerHTML = `
        <span class="bp-progress">PREMIER FLOW · ${tour.step + 1}/4
          <span class="skip" id="prog-skip">· passer</span></span>`;
      const ps = $('#prog-skip');
      if (ps) ps.addEventListener('click', endTour);
      s.setup();
    }

    function startTour() {
      hideEmpty();
      tour = { step: 0 };
      renderStep();
    }

    function endTour(silent) {
      tour = null;
      removeCoach();
      $('#bp-progress-slot').innerHTML = '';
      Atelier.setOnboarded(true);
      if (silent !== true) Atelier.toast('Visite fermée — rejouable à tout moment via <b>?</b>');
    }

    function successCard() {
      tour = null;
      $('#bp-progress-slot').innerHTML = '';
      Atelier.setOnboarded(true);
      const el = coachEl();
      el.innerHTML = `
        <span class="step" style="color:var(--data-green)">Premier flow simulé ✓</span>
        <p>Vous savez poser, brancher, simuler. <b>Compiler</b> produira les artefacts de ce blueprint dans votre projet — <b>rien ne s\u2019exécutera</b>.</p>
        <div class="acts">
          <button class="at-btn sm pri" id="coach-compile">COMPILER LE BLUEPRINT →</button>
          <button class="at-btn sm ghost" id="coach-later">PLUS TARD — TOUT EST SAUVÉ</button>
        </div>`;
      $('#coach-compile').addEventListener('click', () => { removeCoach(); E.compile(); });
      $('#coach-later').addEventListener('click', removeCoach);
    }

    /* ══ État vide — jamais de toile blanche ══ */
    function showEmpty() {
      hideEmpty();
      const onboarded = Atelier.onboarded();
      emptyOverlay = document.createElement('div');
      emptyOverlay.className = 'bp-empty';
      emptyOverlay.innerHTML = `
        <div class="bp-empty-card">
          <h2 style="font-size:1.15rem;text-align:center;margin-bottom:6px">Par où commencer ?</h2>
          <p style="font-size:0.82rem;color:var(--ink-soft);text-align:center;margin-bottom:18px;line-height:1.6">
            Un blueprint <b style="color:var(--ink)">compose</b> des patterns ; il se valide, se simule, se compile — il n\u2019exécute jamais rien.</p>
          <div class="at-col" style="gap:9px">
            ${window.BP2Composer ? `<button class="bp-start acc" id="start-composer">
              <span><b>Décrire ce que je veux</b><span>4 questions, zéro jargon — votre équipe se construit</span></span>
              <span class="arr">→</span></button>` : ''}
            ${window.BP2Library ? `<button class="bp-start" id="start-template">
              <span><b>Partir d\u2019un template Grimoire</b><span>6 flows préfaits — instanciés en copie, à dériver</span></span>
              <span class="arr">→</span></button>` : ''}
            <button class="bp-start" id="start-example">
              <span><b>Partir du modèle</b><span>délégation gouvernée · 5 nodes · requiert crewai</span></span>
              <span class="arr">→</span></button>
            <button class="bp-start" id="start-uc">
              <span><b>Partir d\u2019un use-case</b><span>des squelettes typés, issus du catalogue</span></span>
              <span class="arr">→</span></button>
            <button class="bp-start guided" id="start-guided">
              <span><b>${onboarded ? 'Rejouer la visite guidée' : 'Toile vide, guidée'}</b><span>4 gestes · moins de 2 minutes</span></span>
              <span class="arr">→</span></button>
          </div>
          <p style="text-align:center;margin-top:14px">
            <button class="at-btn sm ghost" id="start-blank">commencer à vide, sans guide ✕</button></p>
        </div>`;
      canvas.appendChild(emptyOverlay);
      const sc = $('#start-composer');
      if (sc) sc.addEventListener('click', () => { hideEmpty(); window.BP2Composer.open(); });
      const stpl = $('#start-template');
      if (stpl) stpl.addEventListener('click', () => { hideEmpty(); window.BP2Library.openGallery(); });
      $('#start-example').addEventListener('click', () => {
        hideEmpty();
        const crewInstalled = Atelier.installedExts().includes('crewai');
        if (!crewInstalled) E.installExtInline('crewai', () => E.loadExample());
        else E.loadExample();
      });
      $('#start-uc').addEventListener('click', () => { location.href = 'patterns.html'; });
      $('#start-guided').addEventListener('click', startTour);
      $('#start-blank').addEventListener('click', () => { dismissedFor = E.bpId(); hideEmpty(); });
    }
    function hideEmpty() { if (emptyOverlay) { emptyOverlay.remove(); emptyOverlay = null; } }

    /* ══ Câblage aux événements de l'éditeur ══ */
    function onEvent(type, data) {
      if (tour) {
        const s = STEPS[tour.step];
        if (s.done({ type, data })) {
          if (type === 'edge-added') {
            Atelier.toast('Connecté via contrat <b>evidence-pack</b> ✓ — vous venez d\u2019apprendre le mot le plus important du standard.', { good: true, ms: 4200 });
          }
          tour.step++;
          if (tour.step >= STEPS.length) successCard();
          else renderStep();
        }
      }
      if (type === 'mutated') {
        const st = E.state();
        if (!st.nodes.length && !tour && dismissedFor !== E.bpId()) showEmpty();
        else if (st.nodes.length) hideEmpty();
      }
      if (type === 'bp-loaded') {
        removeCoach(); tour = null; $('#bp-progress-slot').innerHTML = '';
        if (data.empty && dismissedFor !== data.id) showEmpty();
        else hideEmpty();
      }
      if (type === 'tour-replay') {
        // rejouer proprement : sur toile non vide, on repart sur un nouveau blueprint
        const st = E.state();
        if (st.nodes.length) { Atelier.setOnboarded(true); $('#bp-new').click(); setTimeout(startTour, 250); }
        else startTour();
      }
    }
    ['node-added', 'edge-added', 'sim-done', 'sim-blocked', 'compiled', 'mutated', 'bp-loaded', 'tour-replay', 'ext-installed']
      .forEach(ev => E.on(ev, d => onEvent(ev, d)));

    /* état initial (l'événement bp-loaded du boot est déjà passé) */
    const st = E.state();
    if (st && !st.nodes.length) showEmpty();
  });
})();
