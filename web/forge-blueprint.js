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
      else if (window.__bpShowProps) window.__bpShowProps(n);
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
      const pal = document.getElementById('bp-palette-panel');
      if (pal) pal.style.display = '';
      if (window.__bpShowHelp) window.__bpShowHelp();
    }

    function backToCatalog() {
      current = null;
      cy.elements().remove();
      cy.add(catalogElements);
      cy.fit(undefined, 40);
      document.getElementById('bp-title').textContent = 'BLUEPRINT · CATALOGUE DE PATTERNS';
      const pal = document.getElementById('bp-palette-panel');
      if (pal) pal.style.display = 'none';
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

      // Palette latérale : cliquer = ajouter au centre du viewport
      const extNodes = {};
      const paletteItems = [];
      Promise.all([api('/api/setup'), api('/api/extensions')]).then(([view, exts]) => {
        const artifacts = [...(view.artifacts.agents || []), ...(view.artifacts.workflows || [])];
        for (const p of cat.patterns) paletteItems.push({ group: 'Patterns', value: 'pattern:' + p.id, label: p.id + ' ' + p.name, color: FAMILY_COLORS[p.family] || '#5B6068' });
        for (const u of (cat.useCases || [])) paletteItems.push({ group: 'Use-cases', value: 'composite:use-case:' + u.id, label: u.name, color: '#A78BFA' });
        for (const a of artifacts) paletteItems.push({ group: 'Artefacts du projet', value: 'artifact:' + a, label: a.split('/').pop(), color: '#9BA0A8' });
        for (const ext of exts.available || []) for (const n of ext.nodes || []) {
          const ref = ext.id + '/' + n.id;
          extNodes[ref] = n;
          paletteItems.push({ group: 'Nodes d’extensions', value: 'extnode:' + ref, label: n.label + ' (' + ext.id + ')', color: '#FF6B3D' });
        }
        renderPalette('');
      });

      function renderPalette(q) {
        const list = document.getElementById('bp-palette-list');
        const groups = {};
        for (const it of paletteItems) {
          if (q && !it.label.toLowerCase().includes(q)) continue;
          (groups[it.group] = groups[it.group] || []).push(it);
        }
        list.innerHTML = Object.entries(groups).map(([g, items]) =>
          '<div class="bp-pal-group">' + esc(g) + '</div>' +
          items.map(it => '<div class="bp-pal-item" data-value="' + esc(it.value) + '"><span class="dot" style="background:' + it.color + '"></span>' + esc(it.label) + '</div>').join('')
        ).join('') || '<div class="bp-pal-group">aucun résultat</div>';
      }
      document.getElementById('bp-palette-search').addEventListener('input', function () { renderPalette(this.value.trim().toLowerCase()); });
      document.getElementById('bp-palette-list').addEventListener('click', e => {
        const item = e.target.closest('[data-value]');
        if (item && current) { addNode(item.dataset.value); }
        else if (item) toast('Ouvrir ou créer un blueprint d’abord.');
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

      function addNode(paletteValue) {
        snapshot();
        const [paletteKind, ...refParts] = paletteValue.split(':');
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
        const added = cy.add({
          data: { id, label: (kind === 'pattern' ? ref + '\n' : '') + label, bpNode },
          position: { x: (cy.width() / 2 - pan.x) / zoom, y: (cy.height() / 2 - pan.y) / zoom },
          style: { 'border-color': nodeColor(bpNode) },
        });
        added.select();
      }

      /* ── Undo (UX v2) : instantanés avant chaque mutation ── */
      const undoStack = [];
      function snapshot() {
        if (!current) return;
        undoStack.push(JSON.stringify(serialize()));
        if (undoStack.length > 50) undoStack.shift();
      }
      function undo() {
        if (!current || !undoStack.length) { toast('Rien à annuler.'); return; }
        openBlueprint(JSON.parse(undoStack.pop()));
        toast('Annulé.');
      }
      document.getElementById('bp-undo').addEventListener('click', undo);
      document.addEventListener('keydown', e => {
        if (!current) return;
        if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo(); }
        if ((e.key === 'Delete' || e.key === 'Backspace') && cy.$(':selected').length
            && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
          snapshot();
          cy.$(':selected').remove();
          showEditorHelp();
        }
      });

      /* ── Connexion typée par drag (edgehandles) ──
         Glisser depuis le bord d'un node vers un autre : la connexion se
         crée si un contrat commun existe, sinon elle est refusée. */
      function compatiblePins(src, dst) {
        const outs = (src.data('bpNode').pins || []).filter(p => p.direction === 'out');
        const ins = (dst.data('bpNode').pins || []).filter(p => p.direction === 'in');
        for (const o of outs) for (const i of ins) if (o.contract === i.contract) return { o, i };
        return { outs, ins };
      }

      let eh = null;
      if (typeof cy.edgehandles === 'function') {
        eh = cy.edgehandles({
          canConnect: (source, target) =>
            Boolean(current) && source.id() !== target.id() && Boolean(compatiblePins(source, target).o),
          edgeParams: () => ({ data: { kind: 'depends' } }),
          snap: true,
        });
      } else {
        toast('Plugin de connexion indisponible — drag désactivé.');
      }

      cy.on('ehcomplete', (evt, source, target, added) => {
        snapshot();
        const match = compatiblePins(source, target);
        added.data('bpEdge', {
          from: source.id() + '.' + match.o.id,
          to: target.id() + '.' + match.i.id,
          contract: match.o.contract,
        });
        toast('Connecté via contrat ' + match.o.contract + '.');
      });

      // Refus visuel : si le drag se termine sur un node incompatible
      cy.on('ehstop', (evt, source) => {
        if (!source || !current) return;
      });
      cy.on('mouseover', 'node', evt => {
        if (current) evt.target.addClass('eh-hoverable');
      });

      /* Le drag de connexion démarre depuis le bord du node (Maj+glisser
         pour forcer), le corps du node reste déplaçable. */
      let ehKeyDown = false;
      document.addEventListener('keydown', e => {
        if (e.key === 'Shift' && current && eh && !ehKeyDown) { ehKeyDown = true; eh.enableDrawMode(); }
      });
      document.addEventListener('keyup', e => {
        if (e.key === 'Shift' && ehKeyDown && eh) { ehKeyDown = false; eh.disableDrawMode(); }
      });

      /* ── Layout automatique ── */
      document.getElementById('bp-layout').addEventListener('click', () => {
        if (!current) { toast('Ouvrir un blueprint d’abord.'); return; }
        snapshot();
        cy.layout({ name: 'breadthfirst', directed: true, spacingFactor: 1.4, padding: 60, fit: true }).run();
      });

      /* ── Panneau propriétés du node sélectionné (UX v2) ── */
      const detailPanel = document.getElementById('bp-detail');
      function showEditorHelp() {
        detailPanel.innerHTML = '<div class="empty">Mode édition.<br/><br/>'
          + '- Palette (gauche) : cliquer pour ajouter un node.<br/>'
          + '- Glisser un node pour le déplacer.<br/>'
          + '- <b>Maj + glisser</b> depuis un node pour le connecter (contrats vérifiés).<br/>'
          + '- Sélectionner un node : propriétés ici.<br/>'
          + '- Suppr : retirer la sélection · Ctrl+Z : annuler.</div>';
        detailPanel.classList.add('open');
      }
      window.__bpShowHelp = showEditorHelp;

      window.__bpShowProps = function (n) {
        if (!current) return;
        const bpNode = n.data('bpNode');
        if (!bpNode) return;
        const contracts = (cat.contracts || []).map(c => c.id);
        const pinRow = (pin, idx) => '<div class="bp-d-rel"><span class="k">' + esc(pin.direction) + '</span>'
          + '<select data-pin="' + idx + '" style="background:var(--bg);border:1px solid var(--line);color:var(--ink);font-family:var(--font-mono);font-size:.6rem;padding:2px 4px;border-radius:var(--r-sm)">'
          + contracts.map(c => '<option value="' + esc(c) + '"' + (c === pin.contract ? ' selected' : '') + '>' + esc(c) + '</option>').join('')
          + '</select></div>';
        detailPanel.innerHTML =
          '<span class="bp-d-id" style="color:' + nodeColor(bpNode) + ';border-color:' + nodeColor(bpNode) + '">' + esc(bpNode.kind) + '</span>'
          + '<div class="bp-d-sec" style="margin-top:10px"><h4>Label</h4>'
          + '<input id="bp-prop-label" type="text" value="' + esc(bpNode.label) + '" style="width:100%;background:var(--bg);border:1px solid var(--line);color:var(--ink);font-family:var(--font-mono);font-size:.7rem;padding:6px 8px;border-radius:var(--r-sm)"/></div>'
          + '<div class="bp-d-sec"><h4>Référence</h4><p style="word-break:break-all">' + esc(bpNode.ref) + '</p></div>'
          + '<div class="bp-d-sec"><h4>Pins — contrats</h4>' + (bpNode.pins || []).map(pinRow).join('') + '</div>'
          + '<button type="button" class="bp-btn" id="bp-prop-del" style="color:var(--data-red);border-color:rgba(248,113,113,.4)">SUPPRIMER LE NODE</button>';
        detailPanel.classList.add('open');
        document.getElementById('bp-prop-label').addEventListener('change', function () {
          snapshot();
          bpNode.label = this.value;
          n.data('label', (bpNode.kind === 'pattern' ? bpNode.ref + '\n' : '') + this.value);
        });
        detailPanel.querySelectorAll('[data-pin]').forEach(sel => {
          sel.addEventListener('change', function () {
            snapshot();
            bpNode.pins[Number(this.dataset.pin)].contract = this.value;
            toast('Contrat du pin mis à jour — les edges existants seront revalidés au lint.');
          });
        });
        document.getElementById('bp-prop-del').addEventListener('click', () => {
          snapshot();
          n.remove();
          showEditorHelp();
        });
      };

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
