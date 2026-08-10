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
    /* Le geste d'abord, le mot ensuite : chaque étape nomme la carte par ce
       qu'elle fait ; sa référence du standard suit, entre parenthèses. */
    const STEPS = [
      {
        label: '1/4 · ~15 s',
        html: 'Un flow sérieux finit toujours par <b>une preuve, puis une porte</b>. Posez la preuve : la carte <b>Evidence pack et verification verdict</b> — celle qui transforme un « c’est fini » en décision vérifiable. La recherche est déjà remplie, cliquez la carte dans la palette.',
        setup() { E.palette.search('QUA-04'); E.pulsePalette('QUA-04'); },
        done: ev => ev.type === 'node-added' && ev.data.ref === 'QUA-04'
      },
      {
        label: '2/4 · ~20 s',
        html: 'Une preuve que personne ne lit ne décide rien. La palette montre maintenant <b>ce qui se branche</b> sur sa sortie — posez <b>Evidence-driven transition</b>, la porte qui laisse passer seulement si la preuve tient.',
        setup() { E.palette.search('QUA-05'); E.pulsePalette('QUA-05'); },
        done: ev => ev.type === 'node-added' && ev.data.ref === 'QUA-05'
      },
      {
        label: '3/4 · ~15 s',
        html: 'Reliez les deux : attrapez le point <b>○ de droite</b> de la première carte (« la preuve du travail ») et lâchez-le sur le <b>○ de gauche</b> de la seconde. Lâché dans le vide, le fil propose les cartes compatibles.',
        setup() { E.pulsePin('out', 'evidence-pack'); },
        done: ev => ev.type === 'edge-added'
      },
      {
        label: '4/4 · ~20 s',
        html: 'Simulez : le parcours se déroule étape par étape dans le panneau de droite. <b>Rien ne s’exécute</b> — c’est tout l’intérêt, vous voyez le plan avant de le lancer.',
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
      if (silent !== true) Atelier.journalEvent('tour-skipped');
      if (silent !== true) Atelier.toast('Visite fermée — rejouable à tout moment via <b>?</b>');
    }

    function successCard() {
      tour = null;
      $('#bp-progress-slot').innerHTML = '';
      Atelier.setOnboarded(true);
      Atelier.journalEvent('tour-done');
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
      /* Deux portes au premier contact : les autres départs sont des variantes
         de « partir d'un préfait » — ils attendent sous le pli. */
      emptyOverlay.innerHTML = `
        <div class="bp-empty-card">
          <h2 style="font-size:1.15rem;text-align:center;margin-bottom:6px">Par où commencer ?</h2>
          <p style="font-size:0.82rem;color:var(--ink-soft);text-align:center;margin-bottom:18px;line-height:1.6">
            Un blueprint décrit <b style="color:var(--ink)">qui fait quoi</b> dans votre projet. Il se valide, se simule, se compile — il n’exécute jamais rien.</p>
          <div class="at-col" style="gap:9px">
            ${window.BP2Composer ? `<button class="bp-start acc" id="start-composer">
              <span><b>Décrire ce que je veux</b><span>4 questions, zéro jargon — votre équipe se construit toute seule</span></span>
              <span class="arr">→</span></button>` : ''}
            <button class="bp-start" id="start-example">
              <span><b>Voir un exemple</b><span>un flow complet et relié — à lire, puis à modifier</span></span>
              <span class="arr">→</span></button>
          </div>
          <details class="bp-more" style="margin-top:14px">
            <summary style="font-size:0.75rem;color:var(--ink-muted);cursor:pointer;text-align:center">autres départs</summary>
            <div class="at-col" style="gap:9px;margin-top:10px">
              ${window.BP2Library ? `<button class="bp-start" id="start-template">
                <span><b>Partir d’un template Grimoire</b><span>6 flows préfaits — instanciés en copie, à dériver</span></span>
                <span class="arr">→</span></button>` : ''}
              <button class="bp-start" id="start-uc">
                <span><b>Partir d’un use-case</b><span>des squelettes typés, issus du catalogue</span></span>
                <span class="arr">→</span></button>
              <button class="bp-start guided" id="start-guided">
                <span><b>${onboarded ? 'Rejouer la visite guidée' : 'Toile vide, guidée'}</b><span>4 gestes · moins de 2 minutes</span></span>
                <span class="arr">→</span></button>
              <button class="bp-start" id="start-blank">
                <span><b>Commencer à vide</b><span>sans guide — la palette et le clic droit suffisent</span></span>
                <span class="arr">→</span></button>
            </div>
          </details>
        </div>`;
      canvas.appendChild(emptyOverlay);
      const sc = $('#start-composer');
      if (sc) sc.addEventListener('click', () => { hideEmpty(); window.BP2Composer.open(); });
      const stpl = $('#start-template');
      if (stpl) stpl.addEventListener('click', () => { hideEmpty(); window.BP2Library.openGallery(); });
      $('#start-example').addEventListener('click', () => {
        hideEmpty();
        /* L'exemple du studio (équipe, sous-flow, documents remplis) ne
           réclame aucune extension : première lecture sans installation. */
        if (E.loadStudioExample) { E.loadStudioExample(); return; }
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
            Atelier.toast('Relié : la seconde carte reçoit <b>la preuve du travail</b> ✓ — dans le standard, cette preuve s’appelle un <b>evidence-pack</b>.', { good: true, ms: 4600 });
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
