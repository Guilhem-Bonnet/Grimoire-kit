/* forge-blueprint.js — Blueprint viewer read-only du catalogue de patterns
   Consomme data/catalogue-export.json (généré depuis le catalogue normatif).
   ======================================================================== */
(function () {
  'use strict';

  const FAMILY_ORDER = ['ORG', 'ORC', 'GOV', 'MOD', 'QUA', 'KNO', 'RUN', 'COG'];
  const FAMILY_COLORS = {
    ORG: '#FF6B3D', ORC: '#6EE7FF', GOV: '#A78BFA', MOD: '#F472B6',
    QUA: '#34D399', KNO: '#FCD34D', RUN: '#F87171', COG: '#8B9DFF',
  };
  const KIND_STYLES = {
    founds:     { color: '#FF6B3D', style: 'solid',  label: 'fonde' },
    depends:    { color: '#6EE7FF', style: 'solid',  label: 'dépend de' },
    feeds:      { color: '#34D399', style: 'solid',  label: 'alimente' },
    governs:    { color: '#A78BFA', style: 'solid',  label: 'gouverne' },
    produces:   { color: '#FCD34D', style: 'solid',  label: 'produit' },
    triggers:   { color: '#F87171', style: 'solid',  label: 'déclenche' },
    extends:    { color: '#F472B6', style: 'dashed', label: 'étend' },
    reinforces: { color: '#8B9DFF', style: 'dashed', label: 'renforce' },
    related:    { color: '#5B6068', style: 'dotted', label: 'apparenté' },
  };
  const MATURITY_COLORS = {
    'Minimal': '#34D399', 'Contrôlé': '#6EE7FF', 'Orchestré': '#FCD34D',
    'Gouverné': '#A78BFA', 'Production': '#F87171',
  };

  function loadData(name) {
    return fetch('data/' + name).catch(() => null)
      .then(r => (r && r.ok) ? r.json() : fetch('/data/' + name).then(r2 => r2.json()));
  }

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  loadData('catalogue-export.json').then(init).catch(() => {
    document.getElementById('bp-source').innerHTML = 'DONNÉES INDISPONIBLES';
  });

  function init(cat) {
    /* ── Topbar ── */
    document.getElementById('bp-stats').innerHTML =
      `<span><b>${cat.patterns.length}</b> patterns</span>` +
      `<span><b>${cat.relations.length}</b> relations</span>` +
      `<span><b>${cat.contracts.length}</b> contrats</span>` +
      `<span><b>${(cat.useCases || []).length}</b> use-cases</span>`;
    document.getElementById('bp-source').innerHTML =
      `<span class="sync"></span>CATALOGUE v${esc(cat.catalogVersion)} · ${esc(String(cat.source.commit).slice(0, 7))}`;

    /* ── Éléments Cytoscape : layout en colonnes par famille ── */
    const byFamily = {};
    for (const p of cat.patterns) (byFamily[p.family] = byFamily[p.family] || []).push(p);

    const COL_W = 260, ROW_H = 72;
    const nodes = [];
    FAMILY_ORDER.forEach((fam, col) => {
      (byFamily[fam] || []).forEach((p, row) => {
        nodes.push({
          data: { id: p.id, label: p.id + '\n' + p.name, family: fam, pattern: p },
          position: { x: col * COL_W, y: row * ROW_H },
        });
      });
    });
    const ids = new Set(cat.patterns.map(p => p.id));
    const edges = cat.relations
      .filter(r => ids.has(r.from) && ids.has(r.to))
      .map((r, i) => ({ data: { id: 'e' + i, source: r.from, target: r.to, kind: r.kind, label: r.label || '' } }));

    const cy = cytoscape({
      container: document.getElementById('bp-cy'),
      elements: { nodes, edges },
      layout: { name: 'preset', fit: true, padding: 40 },
      wheelSensitivity: 0.2,
      style: [
        {
          selector: 'node',
          style: {
            shape: 'round-rectangle',
            width: 190, height: 46,
            'background-color': '#1A1D22',
            'border-width': 1.5,
            'border-color': ele => FAMILY_COLORS[ele.data('family')] || '#5B6068',
            label: 'data(label)',
            color: '#F6F7F8',
            'font-family': 'Geist Mono, monospace',
            'font-size': 9,
            'text-wrap': 'wrap',
            'text-max-width': 175,
            'text-valign': 'center',
            'text-halign': 'center',
          },
        },
        {
          selector: 'node:selected',
          style: { 'border-width': 3, 'border-color': '#FF6B3D', 'background-color': '#22262C' },
        },
        { selector: 'node.dim', style: { opacity: 0.14 } },
        { selector: 'node.hit', style: { 'border-width': 3 } },
        {
          selector: 'node.replay',
          style: { 'border-width': 4, 'border-color': '#34D399', 'background-color': '#152019' },
        },
        {
          selector: 'edge',
          style: {
            width: 1.2,
            'curve-style': 'unbundled-bezier',
            'control-point-distances': [40],
            'control-point-weights': [0.5],
            'line-color': ele => KIND_STYLES[ele.data('kind')].color,
            'line-style': ele => KIND_STYLES[ele.data('kind')].style,
            'target-arrow-shape': 'triangle',
            'target-arrow-color': ele => KIND_STYLES[ele.data('kind')].color,
            'arrow-scale': 0.7,
            opacity: 0.55,
          },
        },
        { selector: 'edge.dim', style: { opacity: 0.05 } },
        { selector: 'edge.focus', style: { opacity: 1, width: 2.2 } },
      ],
    });

    /* ── Filtres familles ── */
    const famBox = document.getElementById('bp-families');
    famBox.innerHTML = FAMILY_ORDER.filter(f => byFamily[f]).map(f => {
      const famMeta = (cat.families || []).find(x => x.id === f);
      const title = famMeta ? famMeta.name : f;
      return `<label class="bp-filter" title="${esc(famMeta?.description || '')}">
        <input type="checkbox" data-family="${f}" checked/>
        <span class="dot" style="background:${FAMILY_COLORS[f]}"></span>${f} · ${esc(title)}
        <span class="cnt">${byFamily[f].length}</span></label>`;
    }).join('');

    /* ── Filtres kinds ── */
    const kindCounts = {};
    for (const e of edges) kindCounts[e.data.kind] = (kindCounts[e.data.kind] || 0) + 1;
    document.getElementById('bp-kinds').innerHTML = Object.entries(KIND_STYLES)
      .filter(([k]) => kindCounts[k])
      .map(([k, s]) => `<label class="bp-filter">
        <input type="checkbox" data-kind="${k}" checked/>
        <span class="line ${s.style}" style="border-color:${s.color}"></span>${esc(s.label)}
        <span class="cnt">${kindCounts[k]}</span></label>`).join('');

    function applyFilters() {
      const famOn = new Set([...document.querySelectorAll('[data-family]:checked')].map(i => i.dataset.family));
      const kindOn = new Set([...document.querySelectorAll('[data-kind]:checked')].map(i => i.dataset.kind));
      const q = document.getElementById('bp-search').value.trim().toLowerCase();
      cy.batch(() => {
        cy.nodes().forEach(n => {
          const famOk = famOn.has(n.data('family'));
          const qOk = !q || n.data('id').toLowerCase().includes(q) || n.data('pattern').name.toLowerCase().includes(q);
          n.toggleClass('dim', !(famOk && qOk));
          n.toggleClass('hit', Boolean(q) && famOk && qOk);
        });
        cy.edges().forEach(e => {
          const visible = kindOn.has(e.data('kind')) && !e.source().hasClass('dim') && !e.target().hasClass('dim');
          e.toggleClass('dim', !visible);
        });
      });
    }
    document.getElementById('bp-families').addEventListener('change', applyFilters);
    document.getElementById('bp-kinds').addEventListener('change', applyFilters);
    document.getElementById('bp-search').addEventListener('input', applyFilters);

    /* ── Panneau de détail ── */
    const detail = document.getElementById('bp-detail');
    const relByPattern = {};
    for (const r of cat.relations) {
      (relByPattern[r.from] = relByPattern[r.from] || []).push({ ...r, dir: 'out' });
      (relByPattern[r.to] = relByPattern[r.to] || []).push({ ...r, dir: 'in' });
    }

    function showPattern(p) {
      const color = FAMILY_COLORS[p.family];
      const famMeta = (cat.families || []).find(x => x.id === p.family);
      const rels = (relByPattern[p.id] || []).map(r => {
        const other = r.dir === 'out' ? r.to : r.from;
        const arrow = r.dir === 'out' ? '→' : '←';
        return `<div class="bp-d-rel"><span class="k">${arrow} ${esc(KIND_STYLES[r.kind].label)}</span>
          <a data-goto="${esc(other)}">${esc(other)}</a>
          <span class="lbl">${esc(r.label || '')}</span></div>`;
      }).join('');
      detail.innerHTML = `
        <span class="bp-d-id" style="color:${color};border-color:${color}">${esc(p.id)}</span>
        ${p.maturity ? `<span class="bp-d-mat" style="color:${MATURITY_COLORS[p.maturity] || 'var(--ink-soft)'}">${esc(p.maturity)}</span>` : ''}
        <div class="bp-d-name">${esc(p.name)}</div>
        <div class="bp-d-fam">${esc(famMeta ? famMeta.name : p.family)}</div>
        <div class="bp-d-sec"><h4>Intention</h4><p>${esc(p.intent)}</p></div>
        <div class="bp-d-sec"><h4>Problème</h4><p>${esc(p.problem)}</p></div>
        <div class="bp-d-sec"><h4>Solution</h4><p>${esc(p.solution)}</p></div>
        ${p.controls && p.controls.length ? `<div class="bp-d-sec"><h4>Contrôles</h4><ul>${p.controls.map(c => `<li>${esc(c)}</li>`).join('')}</ul></div>` : ''}
        ${p.antiPattern ? `<div class="bp-d-sec"><h4>Anti-pattern</h4><p>${esc(p.antiPattern)}</p></div>` : ''}
        ${rels ? `<div class="bp-d-sec"><h4>Relations</h4>${rels}</div>` : ''}
        <div class="bp-d-doc">${esc(p.docPath)}</div>`;
      detail.classList.add('open');
      detail.querySelectorAll('[data-goto]').forEach(a => {
        a.addEventListener('click', () => {
          const n = cy.getElementById(a.dataset.goto);
          if (n.length) { cy.animate({ center: { eles: n }, zoom: 1 }, { duration: 240 }); n.select(); }
        });
      });
    }

    cy.on('select', 'node', evt => {
      const n = evt.target;
      const p = n.data('pattern');
      if (p) showPattern(p);
      cy.edges().removeClass('focus');
      n.connectedEdges().not('.dim').addClass('focus');
    });
    cy.on('unselect', 'node', () => {
      cy.edges().removeClass('focus');
      detail.classList.remove('open');
    });

    setupEditor(cy, cat);
  }

  /* ── Éditeur v1 (mode local via grimoire serve) ─────────────────────────
     Connexions non typées, pas de subgraphs (limites assumées, roadmap H2).
     Le blueprint compile vers des artefacts ; il n'exécute rien. */
  function setupEditor(cy, cat) {
    const api = (path, opts) => fetch(path, opts).then(r => r.json());
    api('/api/status').then(() => enable()).catch(() => {});

    function toast(msg, ms) {
      const el = document.getElementById('bp-toast');
      el.textContent = msg;
      el.classList.add('show');
      clearTimeout(toast._t);
      toast._t = setTimeout(() => el.classList.remove('show'), ms || 3200);
    }

    const catalogElements = cy.elements().jsons();
    let current = null;        // blueprint ouvert (objet) ou null = vue catalogue
    let linkSource = null;     // node source en mode connexion
    let seq = 1;

    function nodeColor(n) {
      if (n.kind === 'pattern') return FAMILY_COLORS[String(n.ref).split('-')[0]] || '#5B6068';
      if (n.kind === 'extension-node') return '#FF6B3D';
      if (n.kind === 'composite') return '#A78BFA';
      return '#9BA0A8';
    }

    function bpElements(bp) {
      const nodes = (bp.nodes || []).map((n, i) => ({
        data: { id: n.id, label: (n.kind === 'pattern' ? n.ref + '\n' : '') + n.label, bpNode: n },
        position: n.position ? { x: n.position.x, y: n.position.y } : { x: 120 + (i % 5) * 240, y: 120 + Math.floor(i / 5) * 110 },
        style: { 'border-color': nodeColor(n) },
      }));
      const edges = (bp.edges || []).map((e, i) => ({
        data: { id: 'be' + i, source: e.from.split('.')[0], target: e.to.split('.')[0], kind: 'depends', label: e.contract, bpEdge: e },
      }));
      return { nodes, edges };
    }

    function openBlueprint(bp) {
      current = bp;
      cy.elements().remove();
      cy.add(bpElements(bp));
      cy.fit(undefined, 80);
      document.getElementById('bp-title').textContent = 'BLUEPRINT · ' + (bp.name || bp.id).toUpperCase();
    }

    function backToCatalog() {
      current = null;
      cy.elements().remove();
      cy.add(catalogElements);
      cy.fit(undefined, 40);
      document.getElementById('bp-title').textContent = 'BLUEPRINT · CATALOGUE DE PATTERNS';
    }

    function serialize() {
      const nodes = cy.nodes().map(n => {
        const bpNode = { ...n.data('bpNode') };
        bpNode.position = { x: Math.round(n.position('x')), y: Math.round(n.position('y')) };
        return bpNode;
      });
      const edges = cy.edges().map(e => e.data('bpEdge'));
      return { ...current, nodes, edges };
    }

    function refreshFileList(selected) {
      api('/api/blueprints').then(list => {
        const sel = document.getElementById('bp-file');
        sel.innerHTML = '<option value="">— catalogue —</option>'
          + '<option value="__new__">+ nouveau blueprint</option>'
          + list.map(b => `<option value="${b.id}" ${b.id === selected ? 'selected' : ''}>${b.id} (${b.nodes}n/${b.edges}e)</option>`).join('');
      });
    }

    function enable() {
      document.getElementById('bp-edit').classList.add('on');
      refreshFileList();

      // Palette : patterns du catalogue + artefacts du projet + node packs d'extensions
      const palette = document.getElementById('bp-palette');
      const extNodes = {};
      Promise.all([api('/api/setup'), api('/api/extensions')]).then(([view, exts]) => {
        const artifacts = [...(view.artifacts.agents || []), ...(view.artifacts.workflows || [])];
        let extGroup = '';
        for (const ext of exts.available || []) {
          for (const n of ext.nodes || []) {
            const ref = ext.id + '/' + n.id;
            extNodes[ref] = n;
            extGroup += `<option value="extnode:${ref}">${n.label} (${ext.id})</option>`;
          }
        }
        palette.innerHTML =
          '<optgroup label="Patterns">' + cat.patterns.map(p => `<option value="pattern:${p.id}">${p.id} ${p.name}</option>`).join('') + '</optgroup>' +
          '<optgroup label="Use-cases (composites)">' + (cat.useCases || []).map(u => `<option value="composite:use-case:${u.id}">${u.name}</option>`).join('') + '</optgroup>' +
          '<optgroup label="Artefacts du projet">' + artifacts.map(a => `<option value="artifact:${a}">${a.split('/').pop()}</option>`).join('') + '</optgroup>' +
          (extGroup ? '<optgroup label="Nodes d’extensions">' + extGroup + '</optgroup>' : '');
      });

      document.getElementById('bp-file').addEventListener('change', function () {
        if (!this.value) { backToCatalog(); return; }
        if (this.value === '__new__') {
          const id = prompt('Identifiant du blueprint (kebab-case) :');
          if (!id || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(id)) { toast('Identifiant invalide.'); refreshFileList(); return; }
          openBlueprint({ blueprintVersion: 1, id, name: id, catalogRef: { version: cat.catalogVersion }, nodes: [], edges: [] });
          refreshFileList(id);
          return;
        }
        api('/api/blueprints/' + this.value).then(openBlueprint);
      });

      document.getElementById('bp-add').addEventListener('click', () => {
        if (!current) { toast('Ouvrir ou créer un blueprint d’abord.'); return; }
        const [paletteKind, ...refParts] = palette.value.split(':');
        const ref = refParts.join(':');
        const id = 'n' + Date.now().toString(36) + (seq++);
        const kind = paletteKind === 'extnode' ? 'extension-node' : paletteKind;
        let label, pins;
        if (paletteKind === 'extnode') {
          const decl = extNodes[ref] || {};
          label = decl.label || ref;
          pins = (decl.pins || []).map(p => ({ ...p }));
        } else if (paletteKind === 'composite') {
          const ucId = ref.replace('use-case:', '');
          label = ((cat.useCases || []).find(u => u.id === ucId) || {}).name || ucId;
          pins = [
            { id: 'in', direction: 'in', contract: 'task-envelope' },
            { id: 'out', direction: 'out', contract: 'handoff-packet' },
          ];
        } else {
          label = paletteKind === 'pattern'
            ? (cat.patterns.find(p => p.id === ref) || {}).name || ref
            : ref.split('/').pop();
          pins = [
            { id: 'in', direction: 'in', contract: 'task-envelope' },
            { id: 'out', direction: 'out', contract: 'handoff-packet' },
          ];
        }
        const bpNode = { id, kind, ref, label, pins };
        const pan = cy.pan(), zoom = cy.zoom();
        cy.add({
          data: { id, label: (kind === 'pattern' ? ref + '\n' : '') + label, bpNode },
          position: { x: (cy.width() / 2 - pan.x) / zoom, y: (cy.height() / 2 - pan.y) / zoom },
          style: { 'border-color': nodeColor(bpNode) },
        });
      });

      document.getElementById('bp-link').addEventListener('click', function () {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        linkSource = null;
        this.classList.toggle('armed');
        toast(this.classList.contains('armed')
          ? 'Mode connexion : cliquer le node source puis le node cible.'
          : 'Mode connexion désactivé.');
      });

      // Connexion typée (H4) : les contrats des pins doivent correspondre,
      // sinon la connexion est refusée — le dessin suit le pseudo-code.
      function compatiblePins(src, dst) {
        const outs = (src.data('bpNode').pins || []).filter(p => p.direction === 'out');
        const ins = (dst.data('bpNode').pins || []).filter(p => p.direction === 'in');
        for (const o of outs) for (const i of ins) if (o.contract === i.contract) return { o, i };
        return { outs, ins };
      }

      cy.on('tap', 'node', evt => {
        const armed = document.getElementById('bp-link').classList.contains('armed');
        if (!armed || !current) return;
        if (!linkSource) { linkSource = evt.target; toast('Source : ' + linkSource.id()); return; }
        const target = evt.target;
        if (target.id() !== linkSource.id()) {
          const match = compatiblePins(linkSource, target);
          if (!match.o) {
            const outC = (match.outs || []).map(p => p.contract).join(', ') || 'aucun';
            const inC = (match.ins || []).map(p => p.contract).join(', ') || 'aucun';
            toast('Connexion refusée : contrats incompatibles.\nSorties : ' + outC + '\nEntrées : ' + inC, 5000);
          } else {
            const bpEdge = {
              from: linkSource.id() + '.' + match.o.id,
              to: target.id() + '.' + match.i.id,
              contract: match.o.contract,
            };
            cy.add({ data: { id: 'be' + Date.now().toString(36), source: linkSource.id(), target: target.id(), kind: 'depends', bpEdge } });
            toast('Connecté via contrat ' + match.o.contract + '.');
          }
        }
        linkSource = null;
        document.getElementById('bp-link').classList.remove('armed');
      });

      document.getElementById('bp-del').addEventListener('click', () => {
        if (!current) return;
        cy.$(':selected').remove();
      });

      function lintReport(r) {
        const parts = [];
        if ((r.errors || []).length) parts.push('ERREURS (bloquantes) :\n- ' + r.errors.join('\n- '));
        if ((r.warnings || []).length) parts.push('Avertissements :\n- ' + r.warnings.join('\n- '));
        return parts.join('\n\n');
      }

      document.getElementById('bp-check').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        api('/api/blueprints/' + current.id + '/validate', { method: 'POST', body: JSON.stringify(serialize()) })
          .then(r => toast(lintReport(r) || 'Lint : aucun problème.', 8000));
      });

      document.getElementById('bp-save').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        const bp = serialize();
        api('/api/blueprints/' + current.id, { method: 'PUT', body: JSON.stringify(bp) })
          .then(r => {
            current = bp;
            refreshFileList(current.id);
            const report = lintReport(r);
            toast('Sauvé : ' + r.saved + (report ? '\n\n' + report : ''), 8000);
          });
      });

      /* Simulation pré-exécution (H4) : dry-run côté serveur, rapport dans
         le panneau de détail. Aucun effet produit. */
      document.getElementById('bp-sim').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        api('/api/blueprints/' + current.id + '/simulate', { method: 'POST', body: JSON.stringify(serialize()) })
          .then(r => {
            const detailEl = document.getElementById('bp-detail');
            const ok = r.verdict === 'prêt à appliquer';
            detailEl.innerHTML = `
              <span class="bp-d-id" style="color:${ok ? '#34D399' : '#F87171'};border-color:${ok ? '#34D399' : '#F87171'}">SIMULATION</span>
              <div class="bp-d-name">${esc(r.verdict)}</div>
              ${r.blockers.length ? `<div class="bp-d-sec"><h4>Bloqueurs</h4>${r.blockers.map(b => `<p>${esc(b)}</p>`).join('')}</div>` : ''}
              ${r.warnings.length ? `<div class="bp-d-sec"><h4>Avertissements</h4>${r.warnings.map(w => `<p>${esc(w)}</p>`).join('')}</div>` : ''}
              <div class="bp-d-sec"><h4>Plan d’exécution</h4>
                ${r.steps.map(s => `<div class="bp-d-rel"><span class="k">${s.order}.</span>
                  <a data-goto="${esc(s.id)}">${esc(s.label || s.ref)}</a>
                  <span class="lbl">${esc(s.action || '')}${s.ready === false ? ' — NON PRÊT' : ''}</span></div>`).join('')}
              </div>
              <div class="bp-d-sec"><h4>Entrées / sorties</h4>
                <p>in : ${r.entryNodes.map(esc).join(', ') || '—'}<br/>out : ${r.exitNodes.map(esc).join(', ') || '—'}</p></div>`;
            detailEl.classList.add('open');
            detailEl.querySelectorAll('[data-goto]').forEach(a => {
              a.addEventListener('click', () => {
                const n = cy.getElementById(a.dataset.goto);
                if (n.length) { cy.animate({ center: { eles: n }, zoom: 1 }, { duration: 240 }); n.select(); }
              });
            });
            toast('Simulation : ' + r.verdict + '.');
          });
      });

      /* Compilation v1 (H4) : blueprint prêt -> mission pack gouverné.
         Aucun apply automatique : le diff git reste la revue. */
      document.getElementById('bp-compile').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        api('/api/blueprints/' + current.id + '/compile', { method: 'POST', body: JSON.stringify(serialize()) })
          .then(r => {
            if (r.error) { toast('Compilation refusée :\n' + r.error, 8000); return; }
            api('/api/blueprints/' + current.id).then(openBlueprint);
            toast('Compilé : ' + r.artifact + '\n' + r.hash.slice(0, 19) + '…'
              + ((r.warnings || []).length ? '\nAvertissements : ' + r.warnings.length : ''), 8000);
          });
      });

      /* Replay télémétrie (H4) : rejoue les events.jsonl sur le graphe via
         les bindings du blueprint. Lecture seule, aucune exécution. */
      document.getElementById('bp-replay').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        const bindings = (current.telemetry || {}).bindings || [];
        if (!bindings.length) { toast('Aucun binding télémétrie dans ce blueprint.'); return; }
        api('/api/events/log').then(log => {
          const seq = [];
          for (const [source, events] of Object.entries(log)) {
            for (const ev of events) {
              const s = JSON.stringify(ev);
              for (const b of bindings) {
                if (b.eventSource !== source) continue;
                const ok = Object.entries(b.match || {}).every(([k, v]) =>
                  s.includes('"' + k + '"') && s.includes(String(v)));
                if (ok) seq.push(b.nodeId);
              }
            }
          }
          if (!seq.length) { toast('Aucun événement ne matche les bindings.'); return; }
          toast('Replay : ' + seq.length + ' événement(s)…');
          let i = 0;
          const timer = setInterval(() => {
            cy.nodes().removeClass('replay');
            if (i >= seq.length) { clearInterval(timer); toast('Replay terminé (' + seq.length + ' événements).'); return; }
            const n = cy.getElementById(seq[i++]);
            if (n.length) n.addClass('replay');
          }, 550);
        });
      });
    }
  }
})();
