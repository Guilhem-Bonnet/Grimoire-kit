/* bp2-assist.js — Création assistée
   · suggestions fantômes : le prochain pattern probable, d'après les
     meilleures pratiques agentiques (preuve → porte → journal → mémoire)
   · règles best-practice dans l'onglet RÈGLES, avec correctif en 1 clic
   ========================================================================== */
(function () {
  'use strict';
  let core = null;
  const esc = s => (window.Atelier ? Atelier.esc(s) : String(s == null ? '' : s));
  const resolve = ref => (window.BP2Team && BP2Team.isTeamRef(ref)) ? BP2Team.cardSpec(ref) : Atelier.nodeSpec(ref);

  /* ── adjacences recommandées : ref → [[ref suivant, pourquoi]] ── */
  const NEXT = {
    'ORC-02': [['ORC-01', 'router la mission \u2014 un seul point d\u2019entrée'], ['GOV-01', 'la policy cadre avant de dispatcher']],
    'ORC-01': [['ORC-11', 'déléguer l\u2019implémentation sous contrat'], ['GOV-12', 'menace modélisée avant action sensible'], ['QUA-04', 'exiger la preuve de chaque restitution']],
    'ORC-11': [['QUA-04', 'assembler la preuve du travail'], ['QUA-14', 'valider la sortie avant la porte']],
    'COG-01': [['ORC-11', 'exécuter chaque sous-objectif avec preuves']],
    'COG-03': [['QUA-04', 'prouver l\u2019équivalence avant/après']],
    'QUA-04': [['QUA-05', 'porte fail-closed \u2014 le done se prouve'], ['QUA-15', 'revue par un agent distinct'], ['QUA-13', 'mesurer la couverture réelle']],
    'QUA-15': [['QUA-03', 'chaîner la décision au journal'], ['KNO-02', 'persister le contexte utile']],
    'QUA-13': [['QUA-05', 'décider sur preuve couverte']],
    'QUA-05': [['GOV-02', 'déployer seulement sur verdict vert']],
    'QUA-03': [['KNO-02', 'la mémoire repart du réel'], ['GOV-04', 'auditer la boucle antifragile']],
    'GOV-15': [['QUA-03', 'journaliser l\u2019arbitrage']],
    'GOV-12': [['QUA-15', 'revue du threat model par un tiers']],
    'QUA-14': [['QUA-05', 'fermer sur preuve propre']],
    'GOV-02': [['GOV-04', 'score antifragile sur les audits'], ['KNO-02', 'la mémoire repart du réel']]
  };
  const EXT_NEXT = [['QUA-04', 'prouver ce que le framework a produit'], ['QUA-14', 'valider la sortie avant la porte']];
  const FALLBACK_ORDER = ['QUA', 'GOV', 'KNO', 'RUN', 'ORC', 'MOD', 'COG', 'ORG'];

  function suggestFor(node, g, specOf) {
    const s = specOf(node);
    if (!s || !s.out.length) return [];
    const freeOuts = s.out.filter(c => !g.edges.some(e => e.from === node.id && e.contract === c));
    if (!freeOuts.length) return [];
    const already = new Set(g.edges.filter(e => e.from === node.id)
      .map(e => {
        const t = g.nodes.find(n => n.id === e.to);
        if (!t) return null;
        if (t.kind === 'trigger') return 'team:trigger';
        if (t.kind === 'agent') return t.sub ? 'team:sub' : (t.role === 'orchestrateur' ? 'team:orch' : 'team:agent');
        return t.ref;
      }).filter(Boolean));
    const out = [], used = new Set();

    const push = (ref, why) => {
      if (used.has(ref) || already.has(ref)) return;
      const t = resolve(ref);
      if (!t || t.locked) return;
      const contract = freeOuts.find(c => t.in.includes(c));
      if (!contract) return;
      used.add(ref);
      out.push({ ref, label: (t.kind === 'ext' || t.kind === 'team') ? t.name : t.ref, cat: t.cat, contract, why });
    };

    let recs;
    if (node.kind === 'trigger') {
      recs = [['team:orch', 'confier la mission à un orchestrateur'], ['ORC-01', 'router vers le bon agent']];
    } else if (node.kind === 'agent') {
      recs = node.role === 'orchestrateur'
        ? [['team:agent', 'déléguer à un spécialiste — il travaille et prouve'], ['GOV-12', 'menace modélisée avant action sensible']]
        : (node.delegates ? [['team:sub', 'déléguer une sous-tâche rapide']] : []).concat([['QUA-05', 'porte fail-closed sur sa preuve'], ['QUA-15', 'revue par un agent tiers'], ['QUA-14', 'valider la sortie']]);
    } else if (s.kind === 'ext') recs = EXT_NEXT;
    else if (node.kind === 'group') recs = [['QUA-05', 'fermer le sous-flow par une porte'], ['QUA-15', 'revue de ce que le sous-flow produit']];
    else recs = NEXT[node.ref] || [];
    recs.forEach(([r, w]) => push(r, w));

    if (out.length < 2) {
      const pool = Atelier.paletteNodes()
        .filter(n => !n.locked && n.kind === 'pattern' && n.ref !== node.ref && n.in.some(c => freeOuts.includes(c)))
        .sort((a, b) => FALLBACK_ORDER.indexOf(a.cat) - FALLBACK_ORDER.indexOf(b.cat));
      pool.forEach(n => { if (out.length < 3) push(n.ref, 'consomme ' + freeOuts.find(c => n.in.includes(c))); });
    }
    return out;
  }

  /* ── règles best-practice (par niveau) ── */
  function placeAfter(anchor, ref, dy) {
    const n = core.addNode(ref, anchor.x + (anchor._w || 190) + 130, anchor.y + (dy || 0), { silent: true });
    return n;
  }
  function wire(from, to, specOf) {
    const sf = specOf(from), st = specOf(to);
    const c = sf.out.find(x => st.in.includes(x));
    if (c) core.addEdge(from.id, to.id, c);
  }

  function rules(g, path, where, specOf, fed, drained) {
    fed = fed || new Set(); drained = drained || new Set();
    const out = [];
    if (!g.nodes.length) return out;
    const nodes = g.nodes, edges = g.edges;
    const byId = {}; nodes.forEach(n => byId[n.id] = n);
    const specCache = {}; const sp = n => specCache[n.id] || (specCache[n.id] = specOf(n));
    const hasCat = cat => nodes.some(n => { const s = sp(n); return s && s.cat === cat; });
    const hasRef = ref => nodes.some(n => n.ref === ref);
    const nameOf = n => { const s = sp(n); return n.kind === 'group' ? '◇ ' + (n.name || 'sous-flow') : (n.kind === 'agent' ? esc(n.name) : (s.kind === 'ext' ? s.name : s.ref)); };

    /* R-01 · une preuve doit franchir une porte */
    nodes.forEach(n => {
      const s = sp(n);
      if (!s || !s.out.includes('evidence-pack') || n.ref === 'QUA-05') return;
      const consumed = edges.some(e => e.from === n.id && e.contract === 'evidence-pack') || drained.has('evidence-pack');
      if (consumed) return;
      out.push({ level: 'warn', rule: 'R-01 · preuve → porte', node: n.id, path, ref: nameOf(n),
        text: `${where}sa preuve (evidence-pack) ne franchit aucune porte — un « fini » se décide fail-closed.`,
        fix: { label: 'AJOUTER QUA-05 ET RELIER', run: () => { const gov = placeAfter(n, 'QUA-05', 0); wire(n, gov, specOf); } } });
    });

    /* R-02 · pas de dispatch sans mission cadrée */
    nodes.filter(n => n.ref === 'ORC-01' || (n.kind === 'agent' && n.role === 'orchestrateur')).forEach(n => {
      if (edges.some(e => e.to === n.id && e.contract === 'task-envelope') || fed.has('task-envelope')) return;
      out.push({ level: 'warn', rule: 'R-02 · cadrer avant de router', node: n.id, path, ref: n.ref || esc(n.name),
        text: `${where}le dispatch reçoit une intention non cadrée — posez un Mission Brief en amont.`,
        fix: { label: 'AJOUTER ORC-02 EN AMONT', run: () => {
          const p = core.addNode('ORC-02', n.x - 300, n.y, { silent: true });
          core.addEdge(p.id, n.id, 'task-envelope');
        } } });
    });

    /* R-03 · décisions → mémoire */
    const dec = nodes.find(n => { const s = sp(n); return s && s.out.includes('verification-verdict'); });
    if (dec && !hasCat('KNO')) {
      out.push({ level: 'info', rule: 'R-03 · anti-amnésie', node: dec.id, path, ref: nameOf(dec),
        text: `${where}des décisions sont produites mais rien ne les persiste — les agents suivants repartiront de zéro.`,
        fix: { label: 'AJOUTER KNO-02 ET RELIER', run: () => { const m = placeAfter(dec, 'KNO-02', 60); wire(dec, m, specOf); } } });
    }

    /* R-04 · déploiement gated */
    nodes.filter(n => n.ref === 'GOV-02').forEach(n => {
      if (hasRef('QUA-05')) return;
      out.push({ level: 'err', rule: 'R-04 · pas de déploiement sans verdict', node: n.id, path, ref: 'GOV-02',
        text: `${where}la CI consomme un verdict — aucune porte QUA-05 n'existe pour l'émettre.`,
        fix: { label: 'AJOUTER QUA-05 EN AMONT', run: () => {
          const gov = core.addNode('QUA-05', n.x - 300, n.y, { silent: true });
          core.addEdge(gov.id, n.id, 'verification-verdict');
        } } });
    });

    /* R-05 · exécution sans scan de secrets */
    const exec = nodes.find(n => { const s = sp(n); return s && (s.kind === 'ext' || n.ref === 'ORC-11'); });
    if (exec && !hasRef('QUA-14') && !path.length) {
      out.push({ level: 'info', rule: 'R-05 · sortie validée', node: exec.id, path, ref: nameOf(exec),
        text: `${where}de l'exécution sans validateur de sortie — une sortie hors contrat doit fermer la porte, pas passer.`,
        fix: { label: 'AJOUTER QUA-14', run: () => { const s2 = placeAfter(exec, 'QUA-14', -70); wire(exec, s2, specOf); } } });
    }

    /* R-06 · orchestration sans journal de mission */
    if (hasRef('ORC-01') && !hasRef('QUA-03')) {
      const fo = nodes.find(n => n.ref === 'ORC-01');
      out.push({ level: 'info', rule: 'R-06 · mission journalisée', node: fo.id, path, ref: 'ORC-01',
        text: `${where}de l'orchestration sans Mission Ledger — ce qui est délégué doit être tracé, pas supposé.`,
        fix: { label: 'AJOUTER QUA-03', run: () => { const q = placeAfter(fo, 'QUA-03', 70); wire(fo, q, specOf); } } });
    }

    /* R-07 · orchestrateur sans équipe */
    nodes.filter(n => n.kind === 'agent' && n.role === 'orchestrateur').forEach(n => {
      if (edges.some(e => e.from === n.id && e.contract === 'task-envelope') || drained.has('task-envelope')) return;
      out.push({ level: 'warn', rule: 'R-07 · orchestrateur sans équipe', node: n.id, path, ref: esc(n.name),
        text: `${where}il ne délègue à personne — un orchestrateur sans agents ne produit rien.`,
        fix: { label: 'AJOUTER UN AGENT ET RELIER', run: () => { const a = placeAfter(n, 'team:agent', 0); core.addEdge(n.id, a.id, 'task-envelope'); } } });
    });

    /* R-08 · agent sans accès */
    nodes.filter(n => n.kind === 'agent' && n.role !== 'orchestrateur').forEach(n => {
      if ((n.tools || []).length || (n.mcp || []).length) return;
      out.push({ level: 'warn', rule: 'R-08 · agent sans accès', node: n.id, path, ref: esc(n.name),
        text: `${where}il n'a accès à aucun outil ni branchement MCP — il ne peut rien faire.`,
        fix: { label: 'OUVRIR SA FICHE POUR L\u2019ÉQUIPER', run: () => core.openFiche(n) } });
    });

    /* R-09 · équipement risqué sans garde-fou */
    if (window.BP2Team) nodes.filter(n => n.kind === 'agent' && BP2Team.risky(n) && !BP2Team.guarded(n)).forEach(n => {
      out.push({ level: 'warn', rule: 'R-09 · risqué sans garde', node: n.id, path, ref: esc(n.name),
        text: `${where}équipement risqué (écriture, commandes ou MCP large) sans hook de garde.`,
        fix: { label: 'POSER UN HOOK « SCANNER LES SECRETS »', run: () => {
          n.hooks.push({ when: 'after-tool', then: 'scan-secrets' });
          core.markDirty();
          Atelier.toast('Hook posé sur <b>' + esc(n.name) + '</b> — après chaque outil → scanner les secrets.');
        } } });
    });

    /* R-10 · fournisseur LLM non connecté */
    if (window.BP2Team) nodes.filter(n => n.kind === 'agent' && n.model && !BP2Team.llmOk(n.model)).forEach(n => {
      const prov = BP2Team.providerOf(n.model);
      out.push({ level: 'err', rule: 'R-10 · fournisseur non connecté', node: n.id, path, ref: esc(n.name),
        text: `${where}son modèle « ${esc(n.model)} » (${esc(prov.name)}) n'est pas connecté au projet.`,
        fix: { label: 'CONNECTER ' + esc(prov.name).toUpperCase(), run: () => BP2Team.connectLLM(prov.id, () => { core.markDirty(); }) } });
    });

    /* R-11 · un seul départ */
    const trigs = nodes.filter(n => n.kind === 'trigger');
    if (trigs.length > 1) {
      out.push({ level: 'warn', rule: 'R-11 · départ unique', node: trigs[1].id, path, ref: esc(trigs[1].name || 'déclencheur'),
        text: `${where}${trigs.length} déclencheurs — un flow part d'un seul endroit ; gardez le plus clair.` });
    }

    return out;
  }

  window.BP2Assist = {
    init(c) { core = c; },
    suggestFor, rules
  };
})();
