/* bp2-team.js — L'équipe concrète : orchestrateur, agents, sous-agents.
   Un agent est un node du flow (il reçoit une tâche, rend une preuve).
   Son ÉQUIPEMENT (outils, MCP, skills, hooks, prompt) vit dans sa fiche —
   comme un inventaire — pas sur la toile.
   ========================================================================== */
(function () {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let core = null;

  const COLOR = '#6EE7FF';

  /* ══ Catalogues (langage clair) ══ */
  const TOOLS = [
    { id: 'fs-read',     name: 'Lire des fichiers',        risk: 0, why: 'consulter le code et les docs du projet' },
    { id: 'fs-write',    name: 'Écrire des fichiers',      risk: 1, why: 'modifier le projet — borné au périmètre' },
    { id: 'code-search', name: 'Chercher dans le code',    risk: 0, why: 'retrouver définitions et usages' },
    { id: 'tests',       name: 'Lancer les tests',         risk: 0, why: 'produire des preuves vertes' },
    { id: 'shell',       name: 'Exécuter des commandes',   risk: 2, why: 'build, scripts — surveillé par hooks' },
    { id: 'web',         name: 'Naviguer sur le web',      risk: 1, why: 'documentation externe, veille' }
  ];
  const MCPS = [
    { id: 'github',   name: 'GitHub',                 scope: 'lire les repos · ouvrir des PR',  risk: 1 },
    { id: 'postgres', name: 'PostgreSQL',             scope: 'requêtes en lecture seule',       risk: 1 },
    { id: 'slack',    name: 'Slack',                  scope: 'poster dans #agents',             risk: 1 },
    { id: 'browser',  name: 'Navigateur piloté',      scope: 'pages autorisées uniquement',     risk: 2 },
    { id: 'files',    name: 'Fichiers étendus',       scope: 'hors du projet — à éviter',       risk: 2 }
  ];
  const SKILLS = [
    { id: 'revue-code',      name: 'Revue de code' },
    { id: 'ecriture-tests',  name: 'Écriture de tests' },
    { id: 'debug',           name: 'Debug méthodique' },
    { id: 'migration-sql',   name: 'Migrations SQL' },
    { id: 'redaction-doc',   name: 'Rédaction de docs' },
    { id: 'analyse-donnees', name: 'Analyse de données' }
  ];
  const WHEN = [
    { id: 'after-tool',   name: 'après chaque outil' },
    { id: 'before-write', name: 'avant d\u2019écrire un fichier' },
    { id: 'before-mcp',   name: 'avant un appel MCP' },
    { id: 'before-commit', name: 'avant un commit' },
    { id: 'task-end',     name: 'à la fin de la tâche' }
  ];
  const THEN = [
    { id: 'scan-secrets', name: 'scanner les secrets' },
    { id: 'log-trace',    name: 'journaliser la trace' },
    { id: 'ask-human',    name: 'demander confirmation humaine' },
    { id: 'run-tests',    name: 'lancer les tests' },
    { id: 'block-scope',  name: 'bloquer si hors périmètre' }
  ];
  const MODELS = [
    { id: 'haiku',  hint: 'rapide · économique' },
    { id: 'sonnet', hint: 'équilibré' },
    { id: 'opus',   hint: 'profond · coûteux' }
  ];
  const byId = (list, id) => list.find(x => x.id === id);

  /* ══ Fournisseurs LLM — pas forcément Claude ══ */
  const PROVIDERS = [
    { id: 'anthropic', name: 'Anthropic', models: [
      { id: 'haiku',  label: 'haiku',  in: 0.8, out: 4,  hint: 'rapide' },
      { id: 'sonnet', label: 'sonnet', in: 3,   out: 15, hint: 'équilibré' },
      { id: 'opus',   label: 'opus',   in: 15,  out: 75, hint: 'profond' }
    ] },
    { id: 'openai', name: 'OpenAI', models: [
      { id: 'gpt-4.1-mini', label: '4.1-mini', in: 0.4, out: 1.6 },
      { id: 'gpt-4.1',      label: '4.1',      in: 2,   out: 8 },
      { id: 'o3',           label: 'o3',       in: 2,   out: 8 }
    ] },
    { id: 'google', name: 'Google', models: [
      { id: 'gemini-flash', label: 'gemini flash', in: 0.3,  out: 2.5 },
      { id: 'gemini-pro',   label: 'gemini pro',   in: 1.25, out: 10 }
    ] },
    { id: 'mistral', name: 'Mistral', models: [
      { id: 'mistral-small', label: 'small', in: 0.1, out: 0.3 },
      { id: 'mistral-large', label: 'large', in: 2,   out: 6 }
    ] },
    { id: 'local', name: 'Local (Ollama)', note: '0 $ · votre machine', models: [
      { id: 'llama-3.3',  label: 'llama 3.3 70b', in: 0, out: 0 },
      { id: 'qwen-coder', label: 'qwen coder',    in: 0, out: 0 }
    ] }
  ];
  const MODEL_RATES = {};
  PROVIDERS.forEach(p => p.models.forEach(m => { MODEL_RATES[m.id] = { in: m.in, out: m.out, provider: p.id, label: m.label }; }));
  const LS_LLM = 'grimoire.atelier.llm';
  function llmConnected(pid) {
    try { return (JSON.parse(localStorage.getItem(LS_LLM)) || ['anthropic', 'local']).includes(pid); }
    catch (e) { return pid === 'anthropic' || pid === 'local'; }
  }
  function connectLLM(pid, after) {
    const p = PROVIDERS.find(x => x.id === pid);
    if (!p) return;
    const steps = pid === 'local'
      ? ['instance détectée sur :11434 ✓', 'modèles listés ✓', 'connecté — 0 $, tout reste chez vous']
      : ['clé API vérifiée ✓', 'quotas lus ✓', 'connecté — utilisable par tous vos agents'];
    let i = 0;
    (function tick() {
      Atelier.toast('Connexion <b>' + esc(p.name) + '</b> — ' + steps.slice(0, i + 1).join(' · '), { good: i === steps.length - 1, ms: 1500 });
      i++;
      if (i < steps.length) setTimeout(tick, 480);
      else {
        const list = (function () { try { return JSON.parse(localStorage.getItem(LS_LLM)) || ['anthropic', 'local']; } catch (e) { return ['anthropic', 'local']; } })();
        if (!list.includes(pid)) list.push(pid);
        localStorage.setItem(LS_LLM, JSON.stringify(list));
        if (after) after();
      }
    })();
  }
  const providerOf = mid => PROVIDERS.find(p => p.models.some(m => m.id === mid)) || PROVIDERS[0];
  const llmOk = mid => llmConnected(providerOf(mid).id);

  /* ══ Déclencheurs — ce qui lance le flow ══ */
  const TRIGS = [
    { id: 'manuel',  name: 'manuel',        desc: 'lancé à la main, depuis l’Atelier' },
    { id: 'cron',    name: 'planifié',      desc: 'part tout seul — ex. chaque matin à 9h' },
    { id: 'webhook', name: 'webhook',       desc: 'déclenché par un événement extérieur' },
    { id: 'pr',      name: 'pull request',  desc: 'part quand une PR s’ouvre sur le repo' }
  ];

  /* ══ Cartes palette / menu ══ */
  const CARDS = [
    { ref: 'team:trigger', kind: 'team', cat: 'TRG', name: 'Déclencheur', locked: false,
      desc: 'ce qui lance le flow : manuel, planifié, webhook ou PR',
      in: [], out: ['mission-brief'] },
    { ref: 'team:orch',  kind: 'team', cat: 'AGT', name: 'Orchestrateur', locked: false,
      desc: 'reçoit la mission, découpe, délègue — ne produit pas lui-même',
      in: ['mission-brief'], out: ['task-envelope'] },
    { ref: 'team:agent', kind: 'team', cat: 'AGT', name: 'Agent', locked: false,
      desc: 'un spécialiste : fait le travail et rend la preuve — s\u2019équipe dans sa fiche',
      in: ['task-envelope'], out: ['evidence-pack'] },
    { ref: 'team:sub',   kind: 'team', cat: 'AGT', name: 'Sous-agent', locked: false,
      desc: 'un petit agent rapide (haiku) auquel un agent délègue une sous-tâche',
      in: ['task-envelope'], out: ['evidence-pack'] }
  ];
  const isTeamRef = ref => typeof ref === 'string' && ref.startsWith('team:');
  const cardSpec = ref => CARDS.find(c => c.ref === ref) || null;

  function newNode(ref) {
    if (ref === 'team:trigger') return { kind: 'trigger', trig: 'manuel', name: 'déclencheur' };
    if (ref === 'team:orch') return { kind: 'agent', role: 'orchestrateur', name: 'orchestrateur', model: 'sonnet', delegates: false, tools: [], mcp: [], skills: [], hooks: [{ when: 'task-end', then: 'log-trace' }], docs: {} };
    if (ref === 'team:sub') return { kind: 'agent', role: 'agent', sub: true, name: 'sous-agent', model: 'haiku', delegates: false, tools: [], mcp: [], skills: [], hooks: [], docs: {} };
    return { kind: 'agent', role: 'agent', name: 'agent', model: 'sonnet', delegates: false, tools: [], mcp: [], skills: [], hooks: [], docs: {} };
  }

  /* ══ Spec dynamique ══ */
  function specOf(n) {
    if (n.kind === 'trigger') {
      const t = byId(TRIGS, n.trig) || TRIGS[0];
      return { kind: 'trigger', ref: 'team:trigger', name: n.name || 'déclencheur', cat: 'TRG',
        desc: t.name + ' — ' + t.desc, in: [], out: ['mission-brief'], agents: [], locked: false };
    }
    const orch = n.role === 'orchestrateur';
    return {
      kind: 'agent', ref: n.sub ? 'team:sub' : (orch ? 'team:orch' : 'team:agent'),
      name: n.name || (orch ? 'orchestrateur' : 'agent'), cat: 'AGT',
      desc: orch ? 'reçoit la mission, découpe, délègue aux agents'
        : (n.sub ? 'sous-agent délégué — rapide, périmètre étroit' : 'spécialiste : travaille et rend la preuve'),
      in: orch ? ['mission-brief'] : ['task-envelope'],
      out: orch ? ['task-envelope'] : (n.delegates ? ['evidence-pack', 'task-envelope'] : ['evidence-pack']),
      agents: [n.name || 'agent'], locked: false
    };
  }

  /* ══ Rendu node (meta équipement) ══ */
  function initials(name) {
    const p = String(name || 'ag').replace(/[^A-Za-zÀ-ÿ0-9 -]/g, '').split(/[\s-]+/).filter(Boolean);
    return ((p[0] || 'a')[0] + ((p[1] || p[0] || 'g')[0])).toUpperCase();
  }
  function bodyMeta(n) {
    const c = [];
    c.push(n.tools.length + ' outil' + (n.tools.length > 1 ? 's' : ''));
    if (n.mcp.length) c.push(n.mcp.length + ' MCP');
    if (n.skills.length) c.push(n.skills.length + ' skill' + (n.skills.length > 1 ? 's' : ''));
    if (n.hooks.length) c.push(n.hooks.length + ' hook' + (n.hooks.length > 1 ? 's' : ''));
    const empty = !n.tools.length && !n.mcp.length;
    return `<span class="ag-meta${empty ? ' empty' : ''}" title="L'équipement se règle dans la fiche de l'agent — double-clic">${c.join(' · ')} · ${esc(n.model)}</span>
      <span class="g-meta">double-clic : configurer l\u2019agent</span>`;
  }
  const risky = n => n.tools.some(t => (byId(TOOLS, t) || {}).risk === 2) || n.mcp.some(m => (byId(MCPS, m) || {}).risk === 2) || n.tools.includes('fs-write');
  const guarded = n => n.hooks.some(h => h.then === 'scan-secrets' || h.then === 'ask-human' || h.then === 'block-scope');

  /* ══ Coût : surcharge de prompt due à l'équipement ══ */
  function promptOverheadK(n) {
    return n.tools.length * 0.5 + n.mcp.length * 0.9 + n.skills.length * 0.7 + n.hooks.length * 0.15;
  }

  /* ══ Compile : contributions ══ */
  function counts(root) {
    const out = { agents: 0, tools: new Set(), mcp: new Set(), hooks: 0, skills: 0 };
    (function walk(g) {
      (g.nodes || []).forEach(n => {
        if (n.kind === 'group') { walk(n.sub); return; }
        if (n.kind !== 'agent') return;
        out.agents++;
        n.tools.forEach(t => out.tools.add(t));
        n.mcp.forEach(m => out.mcp.add(m));
        out.hooks += n.hooks.length;
        out.skills += n.skills.length;
      });
    })(root);
    return out;
  }

  /* ══ Inspecteur (résumé) ══ */
  function inspector(n) {
    const s = specOf(n);
    const eq = [
      ['outils', n.tools.map(t => (byId(TOOLS, t) || {}).name || t)],
      ['MCP', n.mcp.map(m => (byId(MCPS, m) || {}).name || m)],
      ['skills', n.skills.map(k => (byId(SKILLS, k) || {}).name || k)],
      ['hooks', n.hooks.map(h => (byId(WHEN, h.when) || {}).name + ' → ' + ((byId(THEN, h.then) || {}).name || ''))]
    ];
    return `
      <div class="bp-prop"><div class="k">Agent</div>
        <div class="v" style="display:flex;align-items:center;gap:9px"><span class="ag-ava">${initials(n.name)}</span>
        <span style="font-family:var(--font-mono)">${esc(n.name)} <span style="color:var(--ink-muted);font-size:0.7rem;display:block">${esc(n.role)}${n.sub ? ' · sous-agent' : ''} · ${esc(n.model)}</span></span></div></div>
      <div class="bp-prop"><div class="k">Rôle</div><div class="v soft">${esc(s.desc)}</div></div>
      <div class="bp-prop"><div class="k">Équipement</div>
        ${eq.map(([k, list]) => `<div class="np-cost"><span>${k}</span><b style="color:${list.length ? 'var(--ink)' : 'var(--ink-muted)'};font-weight:500;text-align:right;max-width:150px">${list.length ? esc(list.join(', ')) : 'aucun'}</b></div>`).join('')}
        ${!n.tools.length && !n.mcp.length ? '<p class="empty" style="padding:6px 0 0">cet agent n\u2019a encore accès à rien — équipez-le.</p>' : ''}
      </div>`;
  }

  /* ══ Fiche de l'agent (drawer) ══ */
  function openSheet(node, onChange) {
    const f = document.querySelector('#bp-fiche');
    const change = part => { if (onChange) onChange(part); };

    function head() {
      return `
      <div class="f-head">
        <div class="at-row sb">
          <span style="display:flex;align-items:center;gap:10px"><span class="ag-ava lg">${initials(node.name)}</span>
            <input class="grp-name-input" id="ag-name" value="${esc(node.name)}" spellcheck="false" style="width:170px" /></span>
          <button class="at-btn sm ghost" id="fiche-close">✕</button>
        </div>
        <div class="at-row" style="gap:8px;margin-top:10px">
          <span class="at-chip"><span class="cdot" style="background:${COLOR}"></span>${node.role === 'orchestrateur' ? 'Orchestrateur' : (node.sub ? 'Sous-agent' : 'Agent')}</span>
          <span class="at-chip" title="Ce que cet agent devient à la compilation : un fichier d'agent versionné, avec ses permissions déclarées.">compilé en artefact ⓘ</span>
        </div>
      </div>`;
    }

    function draw() {
      const s = specOf(node);
      f.innerHTML = head() + `
      <div class="f-body">

        <div class="sh-sec">
          <div class="at-lbl">Rôle <span class="sh-help" title="L'orchestrateur découpe et délègue (il ne produit pas). L'agent travaille et rend une preuve.">ⓘ</span></div>
          <div class="sh-seg" id="ag-role">
            <button data-v="orchestrateur" class="${node.role === 'orchestrateur' ? 'on' : ''}">orchestrateur<span>délègue</span></button>
            <button data-v="agent" class="${node.role === 'agent' ? 'on' : ''}">agent<span>travaille & prouve</span></button>
          </div>
          ${node.role === 'agent' ? `<label class="sh-check" style="margin-top:8px"><input type="checkbox" id="ag-deleg" ${node.delegates ? 'checked' : ''} />
            <span class="nm">peut déléguer à des sous-agents</span><span class="why">ajoute une sortie task-envelope</span></label>` : ''}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Modèle & fournisseur <span class="sh-help" title="Chaque agent choisit son LLM — pas forcément Claude. Un fournisseur se connecte une fois pour tout le projet.">ⓘ</span></div>
          ${PROVIDERS.map(p => {
            const on = llmConnected(p.id);
            return `<div class="prov${on ? '' : ' off'}">
              <div class="prov-h"><span class="pnm">${esc(p.name)}${p.note ? ` <span class=\"pnote\">${esc(p.note)}</span>` : ''}</span>
                ${on ? '<span class="pst on">connecté ●</span>' : `<button class="pst connect" data-connect="${p.id}">connecter →</button>`}</div>
              <div class="sh-chips">${p.models.map(m => `<button class="sh-chip${node.model === m.id ? ' on' : ''}${on ? '' : ' lock'}" data-model="${m.id}" data-prov="${p.id}" title="${m.in}/${m.out} $ par MTok">${esc(m.label)}</button>`).join('')}</div>
            </div>`;
          }).join('')}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Prompt système <span class="sh-help" title="Le texte qui définit cet agent : rôle, mission, refus. C'est un document — coloration et autocomplétion Grimoire.">ⓘ</span></div>
          ${window.BP2Docs ? BP2Docs.ficheSection(node) : ''}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Outils <span class="sh-help" title="Ce que l'agent a le droit de FAIRE. Tout le reste lui est refusé — la permission se déclare, elle ne se devine pas.">ⓘ</span></div>
          ${TOOLS.map(t => `<label class="sh-check"><input type="checkbox" data-tool="${t.id}" ${node.tools.includes(t.id) ? 'checked' : ''} />
            <span class="nm">${esc(t.name)}</span><span class="risk r${t.risk}">${t.risk === 0 ? 'sûr' : t.risk === 1 ? 'à border' : 'risqué'}</span><span class="why">${esc(t.why)}</span></label>`).join('')}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Branchements MCP <span class="sh-help" title="Des services extérieurs (GitHub, base de données…) branchés via le protocole MCP. Chaque branchement déclare son périmètre exact.">ⓘ</span></div>
          ${MCPS.map(m => `<label class="sh-check"><input type="checkbox" data-mcp="${m.id}" ${node.mcp.includes(m.id) ? 'checked' : ''} />
            <span class="nm">${esc(m.name)}</span><span class="risk r${m.risk}">${m.risk === 1 ? 'à border' : 'risqué'}</span><span class="why">périmètre : ${esc(m.scope)}</span></label>`).join('')}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Skills <span class="sh-help" title="Des savoir-faire réutilisables ajoutés au bagage de l'agent — compilés en fichiers de skill.">ⓘ</span></div>
          <div class="sh-chips">${SKILLS.map(k => `<button class="sh-chip${node.skills.includes(k.id) ? ' on' : ''}" data-skill="${k.id}">${esc(k.name)}</button>`).join('')}</div>
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Hooks — automatisations <span class="sh-help" title="Des réflexes automatiques : QUAND il se passe ça, ALORS fais ça. Ils tournent tout seuls, à chaque fois, sans dépendre de la bonne volonté de l'agent.">ⓘ</span></div>
          ${node.hooks.map((h, i) => `<div class="hook-row">
            <span class="w">quand</span><select data-hwhen="${i}">${WHEN.map(w => `<option value="${w.id}"${h.when === w.id ? ' selected' : ''}>${esc(w.name)}</option>`).join('')}</select>
            <span class="w">alors</span><select data-hthen="${i}">${THEN.map(t => `<option value="${t.id}"${h.then === t.id ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
            <button class="hx" data-hdel="${i}" title="Retirer ce hook">✕</button>
          </div>`).join('') || '<p class="empty" style="padding:2px 0 6px">aucun réflexe automatique.</p>'}
          <button class="at-btn sm ghost" id="ag-addhook">＋ AJOUTER UN HOOK</button>
          ${risky(node) && !guarded(node) ? '<p class="sh-warn">⚠ équipement risqué sans garde-fou — ajoutez « scanner les secrets » ou « demander confirmation ».</p>' : ''}
        </div>

        <div class="sh-sec">
          <div class="at-lbl">Ce que ça pèse</div>
          <div class="np-cost"><span>équipement dans le prompt</span><b>+${(promptOverheadK(node)).toFixed(1).replace('.', ',')}k tok / run</b></div>
          <p class="sh-note">pins actuels : ${s.in.map(c => 'in ' + c).concat(s.out.map(c => 'out ' + c)).join(' · ')}</p>
        </div>
      </div>
      <div class="f-foot">
        <button class="at-btn sm acc" id="ag-done">TERMINÉ</button>
        <span class="at-sub" style="margin-left:auto">tout est sauvegardé en direct</span>
      </div>`;

      /* ── events ── */
      f.querySelector('#fiche-close').addEventListener('click', () => f.classList.remove('open'));
      f.querySelector('#ag-done').addEventListener('click', () => f.classList.remove('open'));
      const nameI = f.querySelector('#ag-name');
      nameI.addEventListener('input', () => { node.name = nameI.value; change('name'); });
      f.querySelectorAll('#ag-role button').forEach(b => b.addEventListener('click', () => {
        if (node.role === b.dataset.v) return;
        node.role = b.dataset.v; change('pins'); draw();
      }));
      const del = f.querySelector('#ag-deleg');
      if (del) del.addEventListener('change', () => { node.delegates = del.checked; change('pins'); draw(); });
      f.querySelectorAll('[data-model]').forEach(b => b.addEventListener('click', () => {
        const set = () => { node.model = b.dataset.model; change('model'); draw(); };
        if (!llmConnected(b.dataset.prov)) connectLLM(b.dataset.prov, set);
        else set();
      }));
      f.querySelectorAll('[data-connect]').forEach(b => b.addEventListener('click', () => {
        connectLLM(b.dataset.connect, () => { change('model'); draw(); });
      }));
      f.querySelectorAll('[data-tool]').forEach(cb => cb.addEventListener('change', () => {
        node.tools = node.tools.filter(t => t !== cb.dataset.tool);
        if (cb.checked) node.tools.push(cb.dataset.tool);
        change('tools'); draw();
      }));
      f.querySelectorAll('[data-mcp]').forEach(cb => cb.addEventListener('change', () => {
        node.mcp = node.mcp.filter(m => m !== cb.dataset.mcp);
        if (cb.checked) node.mcp.push(cb.dataset.mcp);
        change('mcp'); draw();
      }));
      f.querySelectorAll('[data-skill]').forEach(b => b.addEventListener('click', () => {
        node.skills = node.skills.includes(b.dataset.skill) ? node.skills.filter(s2 => s2 !== b.dataset.skill) : node.skills.concat(b.dataset.skill);
        change('skills'); draw();
      }));
      f.querySelectorAll('[data-hwhen]').forEach(sel => sel.addEventListener('change', () => { node.hooks[+sel.dataset.hwhen].when = sel.value; change('hooks'); }));
      f.querySelectorAll('[data-hthen]').forEach(sel => sel.addEventListener('change', () => { node.hooks[+sel.dataset.hthen].then = sel.value; change('hooks'); draw(); }));
      f.querySelectorAll('[data-hdel]').forEach(b => b.addEventListener('click', () => { node.hooks.splice(+b.dataset.hdel, 1); change('hooks'); draw(); }));
      f.querySelector('#ag-addhook').addEventListener('click', () => { node.hooks.push({ when: 'after-tool', then: 'scan-secrets' }); change('hooks'); draw(); });
      if (window.BP2Docs) {
        f.querySelectorAll('[data-doc-open]').forEach(el => el.addEventListener('click', () => {
          BP2Docs.openEditor(node, el.dataset.docOpen, () => { change('docs'); draw(); });
        }));
      }
    }
    draw();
    f.classList.add('open');
  }

  /* ══ Fiche du déclencheur ══ */
  function openTriggerSheet(node, onChange) {
    const f = document.querySelector('#bp-fiche');
    function draw() {
      const t = byId(TRIGS, node.trig) || TRIGS[0];
      f.innerHTML = `
      <div class="f-head">
        <div class="at-row sb">
          <span style="display:flex;align-items:center;gap:10px"><span class="ag-ava lg trg">▶</span>
            <input class="grp-name-input" id="tr-name" value="${esc(node.name || 'déclencheur')}" spellcheck="false" style="width:170px" /></span>
          <button class="at-btn sm ghost" id="fiche-close">✕</button>
        </div>
        <div class="at-row" style="gap:8px;margin-top:10px">
          <span class="at-chip"><span class="cdot" style="background:#FDBA74"></span>Déclencheur · ${esc(t.name)}</span>
        </div>
      </div>
      <div class="f-body">
        <div class="sh-sec">
          <div class="at-lbl">Quand le flow part-il ? <span class="sh-help" title="Le déclencheur émet l'intention brute (mission-brief). La gouvernance du flow fait le reste.">ⓘ</span></div>
          ${TRIGS.map(x => `<label class="sh-check"><input type="radio" name="trig" data-trig="${x.id}" ${node.trig === x.id ? 'checked' : ''} />
            <span class="nm">${esc(x.name)}</span><span class="why">${esc(x.desc)}</span></label>`).join('')}
        </div>
        <div class="sh-sec"><p class="sh-note">un flow n’a besoin que d’<b>un seul</b> déclencheur — sa sortie mission-brief se branche sur un orchestrateur ou un dispatch ORC-01.</p></div>
      </div>
      <div class="f-foot"><button class="at-btn sm acc" id="tr-done">TERMINÉ</button></div>`;
      f.querySelector('#fiche-close').addEventListener('click', () => f.classList.remove('open'));
      f.querySelector('#tr-done').addEventListener('click', () => f.classList.remove('open'));
      const nameI = f.querySelector('#tr-name');
      nameI.addEventListener('input', () => { node.name = nameI.value; if (onChange) onChange('name'); });
      f.querySelectorAll('[data-trig]').forEach(r => r.addEventListener('change', () => { node.trig = r.dataset.trig; if (onChange) onChange('trig'); draw(); }));
    }
    draw();
    f.classList.add('open');
  }

  function triggerInspector(n) {
    const t = byId(TRIGS, n.trig) || TRIGS[0];
    return `
      <div class="bp-prop"><div class="k">Déclencheur</div>
        <div class="v" style="display:flex;align-items:center;gap:9px"><span class="ag-ava trg">▶</span>
        <span style="font-family:var(--font-mono)">${esc(n.name || 'déclencheur')} <span style="color:var(--ink-muted);font-size:0.7rem;display:block">${esc(t.name)}</span></span></div></div>
      <div class="bp-prop"><div class="k">Rôle</div><div class="v soft">${esc(t.desc)} — émet l’intention (mission-brief) qui lance tout.</div></div>`;
  }

  window.BP2Team = {
    init(c) { core = c; },
    COLOR, TOOLS, MCPS, SKILLS, WHEN, THEN, MODELS, CARDS,
    PROVIDERS, MODEL_RATES, TRIGS, llmConnected, connectLLM, providerOf, llmOk,
    isTeamRef, cardSpec, newNode, specOf, bodyMeta, initials,
    promptOverheadK, counts, inspector, openSheet, openTriggerSheet, triggerInspector, risky, guarded
  };
})();
