/* bp2-lexicon.js — Le lexique du Studio, et ses termes cliquables
   Extrait de bp2-core.js : le hub avait dépassé de 700 lignes le seuil que ce
   dépôt applique ailleurs, et le lexique n'a besoin de rien d'autre que du
   drawer et de l'émetteur d'événements — il n'avait aucune raison d'y vivre.

   Un terme s'explique là où il apparaît, sans quitter la toile : chaque mot du
   lexique devient un bouton dans les textes de règles, atteignable au clavier.
   ========================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);

  function whenReady(cb) {
    if (window.BPEditor && window.Atelier) return cb();
    setTimeout(() => whenReady(cb), 60);
  }

  whenReady(() => {
    const E = window.BPEditor;
    const esc = Atelier.esc;
    const emit = ev => E.emit(ev);
    const closeFiche = () => { const f = $('#bp-fiche'); if (f) f.classList.remove('open'); };

  /* ══ Lexique / aide ══
     Le lexique n'est pas une page à aller chercher : chaque terme devient
     cliquable là où il apparaît, et s'explique sans quitter la toile. */
  const LEX = [
    ['agent', 'un travailleur concret du flow : il reçoit une tâche, travaille avec ses outils, rend une preuve — sa fiche se règle au double-clic'],
    ['orchestrateur', 'l’agent qui ne produit pas : il reçoit la mission, la découpe et délègue aux spécialistes'],
    ['déclencheur ▶', 'ce qui lance le flow : manuel, planifié, webhook ou pull request — un seul par flow'],
    ['outil', 'ce qu’un agent a le droit de FAIRE (lire, écrire, exécuter…) — tout le reste lui est refusé'],
    ['branchement MCP', 'un service extérieur (GitHub, base de données…) branché à un agent, avec un périmètre déclaré'],
    ['hook', 'un réflexe automatique : « quand il se passe ça → fais ça » — tourne tout seul, à chaque fois'],
    ['skill', 'un savoir-faire réutilisable ajouté au bagage d’un agent — compilé en fichier'],
    ['prompt système', 'le document qui définit un agent : rôle, mission, refus — éditable avec coloration'],
    ['pattern', 'une pratique normée du standard — chacune avec ses contrôles ; la référence (QUA-04…) est son identifiant'],
    ['preuve', 'ce qu’un agent joint pour montrer que le travail est fait : tests, sorties, traces. Dans le standard : evidence-pack'],
    ['porte', 'le point de passage qui décide sur preuve : tant que la preuve ne tient pas, rien ne passe (fail-closed)'],
    ['mission brief', 'le cadrage donné à un agent avant qu’il commence : ce qu’il doit faire, dans quelles limites'],
    ['blueprint', 'un flow composé d’agents et de patterns ; il se valide, se simule, se compile — n’exécute jamais'],
    ['blueprint actif ●', 'LE flow du projet — un seul à la fois ; les autres sont des brouillons ou des dérives'],
    ['template', 'un flow préfait du catalogue Grimoire — instancié en copie locale que vous dérivez librement'],
    ['sous-flow ◇', 'un conteneur C4 : une partie du flow encapsulée, avec ses ports — ⌘G pour grouper, double-clic pour entrer'],
    ['pin', 'la prise typée d’un node : entrée (ce qu’il accepte), sortie (ce qu’il produit)'],
    ['contrat', 'la forme des données qu’un lien transporte — des types d’artefacts réels : task envelope, evidence pack…'],
    ['document', 'un artefact éditable porté par un node : mission brief, contrat de complétion, prompt système…'],
    ['coût (tokens)', 'estimation statique par node et par chemin : contexte consommé + sorties, × itérations — jamais une facture'],
    ['compilation', 'la transformation d’un blueprint en artefacts dans le projet — sans exécution'],
    ['artefact', 'ce que la compilation produit : agents, skills, workflows, hooks — versionnés, tracés']
  ];
  /* Termes auto-liés dans les textes de l'interface. Volontairement court :
     seulement le vocabulaire qu'un débutant ne peut pas deviner. */
  const LEX_AUTO = ['orchestrateur', 'sous-flow', 'blueprint', 'pattern', 'hook', 'skill',
    'prompt système', 'mission brief', 'contrat', 'artefact', 'porte', 'preuve', 'pin'];
  const LEX_ALIAS = {
    'evidence-pack': 'preuve', 'task-envelope': 'mission brief',
    'handoff-packet': 'contrat', 'verification-verdict': 'porte',
    'context-pack': 'contrat', 'memory-record': 'contrat',
    'hooks': 'hook', 'skills': 'skill', 'patterns': 'pattern',
    'contrats': 'contrat', 'artefacts': 'artefact', 'portes': 'porte',
    'preuves': 'preuve', 'sous-flows': 'sous-flow', 'blueprints': 'blueprint'
  };
  const lexBase = k => k.replace(/\s*[◇●▶].*$/, '').replace(/\s*\(.*\)$/, '').trim();
  function lexEntry(key) {
    const want = (LEX_ALIAS[key] || key).toLowerCase();
    return LEX.find(([k]) => lexBase(k).toLowerCase() === want) || null;
  }
  let lexRe;
  function lexPattern() {
    if (lexRe !== undefined) return lexRe;
    const terms = LEX_AUTO.concat(Object.keys(LEX_ALIAS))
      .sort((a, b) => b.length - a.length)
      .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    /* Le lookbehind manque aux moteurs anciens : sans lui on renonce aux
       liens de lexique, jamais au rendu du texte. */
    try {
      lexRe = new RegExp('(?<![\\w\\u00C0-\\u024F-])(' + terms.join('|') + ')(?![\\w\\u00C0-\\u024F-])', 'gi');
    } catch (e) { lexRe = null; }
    return lexRe;
  }
  /* Enveloppe la première occurrence de chaque terme, hors balises HTML. */
  function lexify(html) {
    const re = lexPattern();
    if (!re) return String(html == null ? '' : html);
    const seen = new Set();
    return String(html == null ? '' : html).split(/(<[^>]*>)/).map(seg => {
      if (seg.startsWith('<')) return seg;
      return seg.replace(re, (m) => {
        const key = m.toLowerCase();
        if (seen.has(LEX_ALIAS[key] || key)) return m;
        if (!lexEntry(key)) return m;
        seen.add(LEX_ALIAS[key] || key);
        /* Un bouton, pas un <abbr> : le terme s'ouvre aussi au clavier. */
        return `<button type="button" class="lex" data-lex="${esc(key)}" title="Voir « ${esc(key)} » au lexique">${m}</button>`;
      });
    }).join('');
  }
  /* Trois indicateurs de prise en main, lisibles par la personne concernée —
     ils restent dans ce navigateur et ne partent nulle part. */
  function journalHtml() {
    const j = Atelier.studioJournal();
    if (!j) return '';
    const c = j.counts || {};
    const sec = j.firstEdgeMs === null ? '—' : Math.round(j.firstEdgeMs / 1000) + ' s';
    return `<div style="border-top:1px solid var(--line);padding-top:14px">
      <div class="at-lbl" style="margin-bottom:8px">Votre prise en main</div>
      <p style="font-size:0.77rem;color:var(--ink-soft);line-height:1.8">
        premier lien posé après <b>${sec}</b><br>
        ${c['node-added'] || 0} nodes · ${c['edge-added'] || 0} liens · ${c['sim-done'] || 0} simulations · ${c['compiled'] || 0} compilations<br>
        premier flow simulé : <b>${j.reachedSim ? 'oui' : 'pas encore'}</b>${j.tourSkipped ? ' · visite passée ' + j.tourSkipped + '×' : ''}
      </p>
      <p class="at-sub" style="margin-top:6px">Ces chiffres restent dans ce navigateur.</p>
    </div>`;
  }

  /* Le lexique dit ce qu'un terme veut dire ; le manuel dit pourquoi. Le lien
     n'apparaît que pour les termes qui ont une page — un lien mort serait pire
     que pas de lien. */
  function manualLink(term) {
    const pages = (window.BP2Manual && window.BP2Manual.pages) || {};
    const page = pages[term];
    if (!page) return '';
    const base = (window.BP2Manual && window.BP2Manual.base()) || 'docs/';
    return `<a class="at-chip" style="font-size:0.56rem;text-decoration:none;margin-top:6px;display:inline-block"
      href="${esc(base + page)}" target="_blank" rel="noopener">lire dans le manuel ↗</a>`;
  }

  function openLex(focusKey) {
    const f = $('#bp-fiche');
    const entry = focusKey ? lexEntry(focusKey) : null;
    const focusBase = entry ? lexBase(entry[0]).toLowerCase() : null;
    f.innerHTML = `
      <div class="f-head"><div class="at-row sb"><h2 style="font-size:1rem">Lexique</h2><button class="at-btn sm ghost" id="fiche-close">✕</button></div></div>
      <div class="f-body">
        ${LEX.map(([k, v]) => `<div class="lex-entry${lexBase(k).toLowerCase() === focusBase ? ' on' : ''}" data-lex-entry="${esc(lexBase(k).toLowerCase())}"><b style="font-family:var(--font-mono);font-size:0.77rem;color:var(--accent)">${k}</b><p style="font-size:0.82rem;color:var(--ink-soft);line-height:1.55;margin-top:2px">${v}</p>${manualLink(k)}</div>`).join('')}
        <div style="border-top:1px solid var(--line);padding-top:14px">
          <div class="at-lbl" style="margin-bottom:8px">Gestes</div>
          <p style="font-size:0.77rem;color:var(--ink-soft);line-height:1.8">clic droit — ajouter un node<br>fil lâché dans le vide — menu des nodes compatibles<br>double-clic — entrer (sous-flow) ou dossier (node)<br><span class="at-kbd">⌘G</span> grouper · <span class="at-kbd">⌘⇧G</span> dégrouper · <span class="at-kbd">Échap</span> remonter<br><span class="at-kbd">C</span> commentaire · <span class="at-kbd">F</span> recadrer · <span class="at-kbd">⌘Z</span> annuler · <span class="at-kbd">⌘D</span> dupliquer<br><span class="at-kbd">F1</span> ouvrir le manuel sur le node sélectionné</p>
        </div>
        ${journalHtml()}
      </div>
      <div class="f-foot">
        <button class="at-btn sm acc" id="replay-tour">REJOUER LA VISITE GUIDÉE</button>
        <button class="at-btn sm ghost" id="replay-news">NOUVEAUTÉS ✦</button>
      </div>`;
    f.classList.add('open');
    $('#fiche-close').addEventListener('click', closeFiche);
    $('#replay-tour').addEventListener('click', () => { closeFiche(); emit('tour-replay'); });
    $('#replay-news').addEventListener('click', () => { closeFiche(); emit('news-replay'); });
    if (focusBase) {
      const el = f.querySelector(`[data-lex-entry="${focusBase}"]`);
      if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
    const help = $('#bp-help');
    if (help) help.addEventListener('click', () => openLex(null));
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-lex]');
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    openLex(a.dataset.lex);
  });
    window.BP2Lexicon = { openLex, lexify };
  });
})();
