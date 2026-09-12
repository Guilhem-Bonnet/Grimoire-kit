// Espace Source — les fichiers par étage, l'éditeur, le diff contre le kit,
// la prise d'override, l'inspecteur de provenance. Écran nouveau : rien à
// remplacer (ADR-006, lot 5).
//
// Ce module possède aussi ce que la Console du dock ENVOIE (README, « Lot 5 ↔
// lot 1 ») : le lot 1 possède le chrome du dock (`ctx.dock.log/clear/setTab/
// echo`), donc l'entrée interactive de la Console — invite, historique,
// complétion — est construite au-dessus de ce seul contrat, en redessinant le
// transcript à chaque frappe plutôt qu'en touchant le DOM du dock, que ce
// module n'a pas le droit d'atteindre directement. C'est plus de frappes
// affichées que par un vrai `<input>`, mais ça tient la promesse « aucun
// module d'espace ne touche le DOM de l'autre » plutôt que de la contourner.
//
// La saisie de la Console est amorcée une fois pour toute la session, au
// premier montage de Source (le dock est commun à tous les espaces ; il
// resterait sourd tant que Source n'a jamais été ouvert — c'est la seule
// zone grise que le contrat figé de l'ADR laisse à ce lot).
//
// API consommées : api.files(tier), api.file(path), api.fileDiff(path),
// api.fileUsage(path), api.fileHistory(path), api.createOverride(path),
// api.writeFile(path, text), api.commands(), api.doctor(), api.run(argv),
// api.language(path, {text, pos}). L'assistance par modèle local (#280, voie
// 2 — api.assistStatus(), api.assist(path, {...})) vit entièrement dans
// source-editor.js, jamais ici.

import * as sourceEditor from './source-editor.js';

const CSS_HREF = new URL('./source.css', import.meta.url).href;

function ensureStylesheet() {
  if (document.querySelector(`link[href="${CSS_HREF}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = CSS_HREF;
  document.head.append(link);
}

// ── État de l'espace (réinitialisé à chaque montage) ────────────────────────

function freshState() {
  return {
    tree: null,
    dirsOpen: new Set(),
    currentPath: null,
    currentEntry: null,
    view: 'source',
    inspectorTab: 'fichier',
    dirty: false,
    draft: '',
  };
}

let state = freshState();

// ── Montage ──────────────────────────────────────────────────────────────

export async function mount(root, ctx) {
  ensureStylesheet();
  state = freshState();
  bindConsoleOnce(ctx);
  populateProblems(ctx);

  ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Source']);
  ctx.docbar.setViews(
    [
      { id: 'source', label: 'Source' },
      { id: 'diff', label: 'Diff contre le kit' },
      { id: 'rendu', label: 'Rendu' },
    ],
    state.view,
    (id) => { state.view = id; renderCanvas(root, ctx); },
  );

  let tree;
  try {
    tree = await ctx.api.files();
  } catch (error) {
    root.append(ctx.empty(
      'Source injoignable',
      "L'API locale n'a pas répondu pour /api/workspace/files : " + error.message,
      'grimoire doctor --check-paths',
    ));
    return;
  }
  state.tree = tree;
  renderExplorer(ctx, root);
  renderCanvas(root, ctx);

  // ⌘K « fichier de Source » : la coque passe le chemin choisi dans
  // `ctx.params.file` plutôt que dans le hash (README, « aucun module
  // d'espace n'écrit dans le DOM de l'autre » — et le hash n'est pas fait
  // pour porter un chemin arbitraire). Sans ce relais, la palette listait
  // les fichiers mais n'en ouvrait jamais un.
  if (ctx.params && ctx.params.file) {
    await openFile(ctx, root, ctx.params.file);
  }
}

// ── Explorateur : trois étages, dossiers, badges ────────────────────────────

function tierRootOf(path, roots) {
  for (const r of roots) {
    if (path === r || path.startsWith(r + '/')) return r;
  }
  return roots[0] || '';
}

function dirOf(path) {
  const idx = path.lastIndexOf('/');
  return idx === -1 ? '' : path.slice(0, idx);
}

function baseName(path) {
  const idx = path.lastIndexOf('/');
  return idx === -1 ? path : path.slice(idx + 1);
}

function fileBadge(entry) {
  // Le badge de dérive (spec §4, « livrer »·1) : un override qui masque un
  // fichier du kit toujours présent, mais dont le contenu a divergé.
  if (entry.tier === 'overrides') {
    if (entry.diverges) return { cls: 'dot warn', title: 'diverge du kit — ' + (entry.kit_counterpart || '') };
    if (entry.masks_kit) return { cls: 'dot ok', title: 'identique au kit' };
    return { cls: 'dot bad', title: 'le kit ne livre plus ce chemin' };
  }
  if (entry.tier === 'kit' && entry.overridden) {
    return { cls: 'dot acc', title: 'masqué par un override du projet' };
  }
  return null;
}

function renderExplorer(ctx, root) {
  ctx.explorer.replaceChildren();
  for (const tier of state.tree.tiers) {
    const head = document.createElement('div');
    head.className = 'sr-tree-tier';
    if (tier.term) head.dataset.term = tier.term;
    head.textContent = `${tier.label} · ${tier.count}${tier.truncated ? ' (+ )' : ''}`;
    ctx.explorer.append(head);

    if (!tier.exists) {
      const note = document.createElement('div');
      note.className = 'sr-tree-file lbl';
      note.textContent = tier.note ? `aucun fichier — ${tier.note}` : 'aucun fichier';
      ctx.explorer.append(note);
      continue;
    }

    // Groupement à un niveau : dossier immédiat → fichiers. Un vrai arbre
    // récursif n'ajoute rien pour les profondeurs vues dans un projet réel
    // (agents/, workflows/, framework/…) et coûterait un état de pliage par
    // niveau au lieu d'un seul.
    const byDir = new Map();
    for (const file of tier.files) {
      const dir = dirOf(file.path);
      if (!byDir.has(dir)) byDir.set(dir, []);
      byDir.get(dir).push(file);
    }
    for (const [dir, files] of [...byDir.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      const dirKey = `${tier.id}:${dir}`;
      const open = !state.dirsOpen.has('closed:' + dirKey);
      if (dir) {
        const dirBtn = document.createElement('button');
        dirBtn.type = 'button';
        dirBtn.className = 'sr-tree-dir';
        dirBtn.textContent = `${open ? '▾' : '▸'} ${dir}/ · ${files.length}`;
        dirBtn.addEventListener('click', () => {
          if (open) state.dirsOpen.add('closed:' + dirKey);
          else state.dirsOpen.delete('closed:' + dirKey);
          renderExplorer(ctx, root);
        });
        ctx.explorer.append(dirBtn);
      }
      if (!open && dir) continue;
      for (const file of files.sort((a, b) => a.path.localeCompare(b.path))) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'sr-tree-file';
        row.setAttribute('aria-current', String(file.path === state.currentPath));
        if (file.path === state.currentPath) row.classList.add('sel');
        const name = document.createElement('span');
        name.textContent = baseName(file.path);
        row.append(name);
        const badge = fileBadge(file);
        if (badge) {
          const dot = document.createElement('span');
          dot.className = `dot ${badge.cls.split(' ')[1]} sr-badge`;
          dot.title = badge.title;
          row.append(dot);
        }
        row.addEventListener('click', () => openFile(ctx, root, file.path));
        ctx.explorer.append(row);
      }
    }
  }
}

// ── Ouvrir un fichier ────────────────────────────────────────────────────

async function openFile(ctx, root, path) {
  if (state.dirty && !confirm('Des modifications non enregistrées seront perdues. Continuer ?')) {
    return;
  }
  state.currentPath = path;
  state.dirty = false;
  state.inspectorTab = 'fichier';
  editorProblemLines = []; // le fichier change : les diagnostics de l'ancien ne s'appliquent plus
  renderProblems(ctx);
  try {
    state.currentEntry = await ctx.api.file(path);
  } catch (error) {
    state.currentEntry = null;
    root.replaceChildren(ctx.empty('Fichier illisible', error.message, `grimoire doctor`));
    return;
  }
  state.draft = state.currentEntry.text || '';
  renderExplorer(ctx, root);
  renderCanvas(root, ctx);
  renderInspector(ctx);
  ctx.docbar.setValidation(
    state.currentEntry.editable ? 'éditable' : 'lecture seule',
    state.currentEntry.editable ? 'ok' : 'warn',
  );
}

// ── Canevas : Source / Diff / Rendu ─────────────────────────────────────────

function chipEtat(entry) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  const dot = document.createElement('span');
  if (!entry.editable) {
    dot.className = 'dot warn';
    chip.append(dot, document.createTextNode(' lecture seule'));
  } else if (entry.tier === 'overrides' && entry.masks_kit && !entry.diverges) {
    dot.className = 'dot ok';
    chip.append(dot, document.createTextNode(` identique au kit${entry.kit_version ? ' ' + entry.kit_version : ''}`));
  } else if (entry.tier === 'overrides' && entry.diverges) {
    dot.className = 'dot warn';
    chip.append(dot, document.createTextNode(' override — diverge du kit'));
  } else {
    dot.className = 'dot acc';
    chip.append(dot, document.createTextNode(' override'));
  }
  return chip;
}

function renderCanvas(root, ctx) {
  root.replaceChildren();
  if (!state.currentPath || !state.currentEntry) {
    const total = state.tree ? state.tree.tiers.reduce((sum, t) => sum + t.count, 0) : 0;
    root.append(ctx.empty(
      'Source',
      total
        ? `${total} fichier(s) répartis sur ${state.tree.tiers.length} étages. Choisissez un fichier dans l'explorateur.`
        : "Aucun fichier trouvé. `grimoire init` puis `grimoire standard init` peuplent les étages.",
      'grimoire doctor --check-paths',
    ));
    return;
  }
  const entry = state.currentEntry;
  const wrap = document.createElement('div');
  wrap.className = 'sr-editor';

  const docrow = document.createElement('div');
  docrow.className = 'sr-docrow';
  const path = document.createElement('span');
  path.className = 'mono';
  path.textContent = entry.path;
  docrow.append(path);
  docrow.append(chipEtat(entry));
  if (entry.editable) {
    const save = document.createElement('button');
    save.className = 'btn';
    save.type = 'button';
    save.textContent = state.dirty ? 'Enregistrer (modifié)' : 'Enregistrer';
    save.disabled = !state.dirty;
    save.addEventListener('click', () => saveCurrent(ctx, root));
    docrow.append(save);
  }
  wrap.append(docrow);

  if (!entry.editable && state.view === 'source') {
    const overridePath = entry.override_path || '(à calculer par le serveur)';
    const banner = document.createElement('div');
    banner.className = 'sr-banner';
    banner.dataset.term = 'kit';
    const dot = document.createElement('span');
    dot.className = 'dot acc';
    const text = document.createElement('span');
    text.append(document.createTextNode('Ce fichier est généré par le kit. Le modifier crée '));
    const code = document.createElement('span');
    code.className = 'mono';
    code.textContent = overridePath;
    text.append(code, document.createTextNode(', qui prime et survit aux mises à jour.'));
    const btn = document.createElement('button');
    btn.className = 'btn pri';
    btn.type = 'button';
    btn.textContent = 'Créer un override';
    btn.addEventListener('click', () => createOverrideAndOpen(ctx, root));
    banner.append(dot, text, btn);
    wrap.append(banner);
  }

  const body = document.createElement('div');
  body.className = 'sr-body';
  if (entry.binary) {
    body.append(ctx.empty('Fichier binaire', `${entry.size} octet(s) — pas de rendu texte pour ce fichier.`, ''));
  } else if (entry.truncated) {
    body.append(ctx.empty(
      'Fichier trop volumineux',
      `Au-delà de ${(entry.size / 1024).toFixed(0)} Ko, l'éditeur ne charge pas le contenu.`,
      '',
    ));
  } else if (state.view === 'source') {
    body.append(buildSourceView(ctx, root, entry));
  } else if (state.view === 'diff') {
    body.append(buildDiffPlaceholder());
    loadDiff(ctx, body, entry);
  } else {
    body.append(buildRenderedPlaceholder());
    loadRendered(body, entry);
  }
  wrap.append(body);
  root.append(wrap);
}

function buildSourceView(ctx, root, entry) {
  // La colorisation, la gouttière de diagnostics et la complétion vivent dans
  // leur propre module (lot 5, #280) : `sourceEditor.build` rend le même
  // `.sr-code` qu'avant — gouttière + `.sr-textarea` — augmenté d'une
  // surcouche colorée. Le contrat que
  // tests/e2e/test_workspace_source.py connaît (`.sr-textarea`, Ctrl+S, Tab,
  // le bouton Enregistrer) ne change pas.
  return sourceEditor.build(ctx, entry, state, {
    onDirtyChange: () => {
      const saveBtn = root.querySelector('.sr-docrow .btn:not(.pri)');
      if (saveBtn) {
        saveBtn.disabled = !state.dirty;
        saveBtn.textContent = state.dirty ? 'Enregistrer (modifié)' : 'Enregistrer';
      }
    },
    onSave: () => saveCurrent(ctx, root),
    onDiagnostics: (diagnostics) => updateEditorProblems(ctx, entry.path, diagnostics),
  });
}

function buildDiffPlaceholder() {
  const div = document.createElement('div');
  div.className = 'sr-diff lbl';
  div.textContent = 'Calcul du diff…';
  return div;
}

async function loadDiff(ctx, body, entry) {
  let diff;
  try {
    diff = await ctx.api.fileDiff(entry.path);
  } catch (error) {
    body.replaceChildren(ctx.empty('Diff indisponible', error.message, ''));
    return;
  }
  const div = document.createElement('div');
  div.className = 'sr-diff';
  if (!diff.comparable) {
    div.append(rowText(diff.reason || 'non comparable', 'lbl'));
    body.replaceChildren(div);
    return;
  }
  if (diff.identical) {
    div.append(rowText('identique au kit — aucune ligne à comparer', 'lbl'));
    body.replaceChildren(div);
    return;
  }
  for (const line of diff.unified.split('\n')) {
    if (!line) continue;
    let cls = '';
    if (line.startsWith('+++') || line.startsWith('---')) cls = 'lbl';
    else if (line.startsWith('@@')) cls = 'hunk';
    else if (line.startsWith('+')) cls = 'add';
    else if (line.startsWith('-')) cls = 'del';
    div.append(rowText(line, cls));
  }
  body.replaceChildren(div);
}

function rowText(text, cls) {
  const row = document.createElement('div');
  if (cls) row.className = cls;
  row.textContent = text;
  return row;
}

function buildRenderedPlaceholder() {
  const div = document.createElement('div');
  div.className = 'sr-rendered lbl';
  div.textContent = 'Rendu…';
  return div;
}

function loadRendered(body, entry) {
  const div = document.createElement('div');
  div.className = 'sr-rendered';
  div.innerHTML = renderMarkdown(state.draft || '');
  body.replaceChildren(div);
}

// ── Rendu Markdown minimal, sans dépendance (ADR-006 D2) ────────────────────
//
// Volontairement limité : titres, gras, italique, code inline, blocs de code,
// listes, liens, paragraphes. Pas de tableaux ni de citations imbriquées —
// l'espace Source n'est pas un moteur Markdown, c'est une prévisualisation.

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function renderMarkdown(source) {
  const lines = escapeHtml(source).split('\n');
  const html = [];
  let inCode = false;
  let listOpen = false;
  for (const raw of lines) {
    if (raw.startsWith('```')) {
      html.push(inCode ? '</pre>' : '<pre>');
      inCode = !inCode;
      continue;
    }
    if (inCode) { html.push(raw + '\n'); continue; }
    let line = raw;
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      if (listOpen) { html.push('</ul>'); listOpen = false; }
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    const item = /^[-*]\s+(.*)$/.exec(line);
    if (item) {
      if (!listOpen) { html.push('<ul>'); listOpen = true; }
      html.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (listOpen) { html.push('</ul>'); listOpen = false; }
    if (!line.trim()) { html.push(''); continue; }
    html.push(`<p>${inline(line)}</p>`);
  }
  if (listOpen) html.push('</ul>');
  if (inCode) html.push('</pre>');
  return html.join('\n');
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" rel="noopener">$1</a>');
}

// ── Enregistrer / créer un override ─────────────────────────────────────────

async function saveCurrent(ctx, root) {
  const entry = state.currentEntry;
  if (!entry || !entry.editable) return;
  try {
    const updated = await ctx.api.writeFile(entry.path, state.draft);
    state.currentEntry = updated;
    state.draft = updated.text || '';
    state.dirty = false;
    ctx.dock.log('console', `$ enregistré · ${entry.path}`);
    ctx.dock.echo('POST /api/workspace/file/write');
    renderCanvas(root, ctx);
    renderInspector(ctx);
  } catch (error) {
    ctx.dock.log('console', `refusé · ${error.message}`);
  }
}

async function createOverrideAndOpen(ctx, root) {
  const entry = state.currentEntry;
  if (!entry) return;
  try {
    const created = await ctx.api.createOverride(entry.path);
    ctx.dock.log('console', `$ override créé · ${created.override_path}`);
    ctx.dock.echo('POST /api/workspace/file/override');
    await ctx.api.files().then((tree) => { state.tree = tree; renderExplorer(ctx, root); });
    await openFile(ctx, root, created.override_path);
  } catch (error) {
    ctx.dock.log('console', `refusé · ${error.message}`);
  }
}

// ── Inspecteur : Fichier / Utilisé par / Historique ─────────────────────────

function renderInspector(ctx) {
  const entry = state.currentEntry;
  ctx.inspector.replaceChildren();
  if (!entry) return;

  const tabs = document.createElement('div');
  tabs.className = 'sr-insp-tabs';
  const body = document.createElement('div');
  body.className = 'sr-insp-body';

  const TABS = [
    { id: 'fichier', label: 'Fichier' },
    { id: 'usage', label: 'Utilisé par' },
    { id: 'historique', label: 'Historique' },
  ];
  for (const tab of TABS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tab';
    btn.setAttribute('aria-selected', String(tab.id === state.inspectorTab));
    btn.textContent = tab.label;
    btn.addEventListener('click', () => { state.inspectorTab = tab.id; renderInspector(ctx); });
    tabs.append(btn);
  }
  ctx.inspector.append(tabs, body);

  if (state.inspectorTab === 'fichier') renderInspectorFichier(ctx, body, entry);
  else if (state.inspectorTab === 'usage') renderInspectorUsage(ctx, body, entry);
  else renderInspectorHistorique(ctx, body, entry);
}

function field(label, valueNode) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  const lbl = document.createElement('span');
  lbl.className = 'lbl';
  lbl.textContent = label;
  wrap.append(lbl, valueNode);
  return wrap;
}

function textNode(text, cls) {
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = text;
  return span;
}

function renderInspectorFichier(ctx, body, entry) {
  const tierLabels = { overrides: 'overrides, possédé par le projet', kit: 'kit, généré', projections: 'projection, régénérée' };
  body.append(field('Étage', textNode(tierLabels[entry.tier] || entry.tier)));
  body.append(field('Version', textNode(entry.kit_version || '—', 'mono')));
  const digestRow = document.createElement('span');
  digestRow.className = 'row';
  digestRow.dataset.term = 'empreinte';
  const dot = document.createElement('span');
  dot.className = entry.shipped_by_kit ? 'dot ok' : 'dot';
  digestRow.append(dot, textNode(entry.shipped_by_kit ? 'au catalogue' : 'hors catalogue', 'soft'));
  body.append(field('Empreinte', digestRow));
  body.append(field(
    'Override',
    textNode(entry.override_path ? (entry.overridden ? entry.override_path : `${entry.override_path} (non créé)`) : 'aucun', 'mono soft'),
  ));
}

function renderInspectorUsage(ctx, body, entry) {
  body.append(textNode('Chargement…', 'lbl'));
  ctx.api.fileUsage(entry.path).then((usage) => {
    body.replaceChildren();
    body.append(field(
      'Projeté vers',
      usage.projections.length
        ? listOfRows(usage.projections, 'ok')
        : textNode('aucune projection trouvée sous .claude/ ou .github/', 'sr-usage-empty'),
    ));
    body.append(field(
      'Chargé par',
      usage.loaded_by.entries.length
        ? chipsOf(usage.loaded_by.entries.map((e) => `${e.path}:${e.line}`))
        : textNode('aucune référence trouvée dans les autres fichiers du projet', 'sr-usage-empty'),
    ));
    if (usage.loaded_by.truncated) body.append(textNode('résultats tronqués — la liste est plus longue', 'lbl'));
  }).catch((error) => {
    body.replaceChildren(textNode('indisponible · ' + error.message, 'sr-usage-empty'));
  });
}

function listOfRows(items, dotCls) {
  const wrap = document.createElement('div');
  wrap.style.display = 'flex';
  wrap.style.flexDirection = 'column';
  wrap.style.gap = '4px';
  for (const item of items) {
    const row = document.createElement('div');
    row.className = 'sr-usage-row';
    const dot = document.createElement('span');
    dot.className = `dot ${dotCls}`;
    row.append(dot, textNode(item));
    wrap.append(row);
  }
  return wrap;
}

function chipsOf(items) {
  const wrap = document.createElement('div');
  wrap.className = 'row';
  wrap.style.flexWrap = 'wrap';
  wrap.style.gap = '6px';
  for (const item of items) {
    const chip = document.createElement('span');
    chip.className = 'chip mono';
    chip.textContent = item;
    wrap.append(chip);
  }
  return wrap;
}

function renderInspectorHistorique(ctx, body, entry) {
  body.append(textNode('Chargement…', 'lbl'));
  ctx.api.fileHistory(entry.path).then((history) => {
    body.replaceChildren();
    if (!history.is_repo) {
      body.append(textNode("ce projet n'est pas un dépôt git — aucun historique à montrer", 'sr-usage-empty'));
      return;
    }
    if (!history.commits.length) {
      body.append(textNode('aucun commit ne touche ce fichier', 'sr-usage-empty'));
      return;
    }
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.gap = '8px';
    for (const commit of history.commits) {
      const row = document.createElement('div');
      const subject = document.createElement('div');
      subject.textContent = commit.subject;
      const meta = document.createElement('div');
      meta.className = 'lbl mono';
      meta.textContent = `${commit.sha.slice(0, 8)} · ${commit.author} · ${commit.date}`;
      row.append(subject, meta);
      wrap.append(row);
    }
    body.append(wrap);
  }).catch((error) => {
    body.replaceChildren(textNode('indisponible · ' + error.message, 'sr-usage-empty'));
  });
}

// ── Onglet Problèmes : doctor au montage, plus l'éditeur en continu ────────
//
// Deux sources dans le même onglet : `grimoire doctor`, une fois par montage
// de Source, et les diagnostics du fichier ouvert dans l'éditeur (#280),
// recalculés à l'enregistrement et au repos de saisie. `ctx.dock.clear/log`
// remplace tout le contenu de l'onglet à chaque appel (README, « Lot 5 ↔
// lot 1 ») : les deux sources sont donc tenues à part et refusionnées à
// chaque mise à jour de l'une ou l'autre, plutôt que de s'écraser.

let doctorLines = [];
let editorProblemLines = [];

function renderProblems(ctx) {
  ctx.dock.clear('problemes');
  const lines = [...doctorLines];
  if (editorProblemLines.length) {
    if (lines.length) lines.push('');
    lines.push(...editorProblemLines);
  }
  ctx.dock.log('problemes', ...(lines.length ? lines : ['aucune ligne']));
}

async function populateProblems(ctx) {
  doctorLines = ['diagnostic en cours — grimoire doctor --check-paths…'];
  renderProblems(ctx);
  try {
    const report = await ctx.api.doctor();
    doctorLines = report.lines.length ? report.lines : ['aucune ligne — ' + report.command];
  } catch (error) {
    doctorLines = ['diagnostic indisponible · ' + error.message];
  }
  renderProblems(ctx);
}

function updateEditorProblems(ctx, path, diagnostics) {
  editorProblemLines = diagnostics.map(
    (d) => `${path}:${d.line + 1}:${d.start + 1} — [${d.severity}] ${d.family} — ${d.message}`,
  );
  renderProblems(ctx);
}

// ── Console du dock : invite, historique, complétion ────────────────────────
//
// Rendue entièrement à travers `ctx.dock.log`/`clear` (voir l'en-tête de ce
// fichier) : chaque frappe redessine le transcript plutôt que de manipuler un
// `<input>` que ce module n'a pas le droit de posséder.

const consoleState = {
  transcript: [],   // { input, output, code, refusal }
  inputHistory: [],
  historyIndex: 0,
  buffer: '',
  catalogue: null,
  running: false,
};

let consoleCtx = null;
let consoleBound = false;

function bindConsoleOnce(ctx) {
  consoleCtx = ctx;
  if (consoleBound) return;
  consoleBound = true;
  document.addEventListener('keydown', onConsoleKeydown);
  renderConsole();
}

function consoleTabIsActive() {
  const btn = document.querySelector('[data-dock-tab="console"]');
  return !!btn && btn.getAttribute('aria-selected') === 'true';
}

function paletteIsOpen() {
  const el = document.getElementById('palette');
  return !!el && !el.hidden;
}

function onConsoleKeydown(event) {
  if (!consoleCtx || consoleState.running) return;
  if (paletteIsOpen()) return;
  if (!consoleTabIsActive()) return;
  const target = event.target;
  const typingElsewhere = /^(INPUT|TEXTAREA)$/.test(target.tagName) || target.isContentEditable;
  if (typingElsewhere) return; // l'éditeur de Source a la priorité sur ses propres frappes.
  const meta = event.metaKey || event.ctrlKey || event.altKey;

  if (event.key === 'Enter') {
    event.preventDefault();
    void executeConsole();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    historyStep(-1);
  } else if (event.key === 'ArrowDown') {
    event.preventDefault();
    historyStep(1);
  } else if (event.key === 'Tab') {
    event.preventDefault();
    void completeConsole();
  } else if (event.key === 'Backspace') {
    event.preventDefault();
    consoleState.buffer = consoleState.buffer.slice(0, -1);
    renderConsole();
  } else if (event.key === 'Escape') {
    event.preventDefault();
    consoleState.buffer = '';
    renderConsole();
  } else if (event.key === '`') {
    // Raccourci du raccourci : la coque (shell.js) traite déjà `` ` `` comme
    // « place le curseur dans la Console » et vient de sélectionner l'onglet
    // dans ce même tour d'événement (les deux écouteurs sont sur `document`,
    // le sien enregistré en premier). Sans ce garde, le caractère atterrirait
    // dans le tampon au lieu d'y ouvrir simplement la saisie.
  } else if (!meta && event.key.length === 1) {
    consoleState.buffer += event.key;
    renderConsole();
  }
}

function historyStep(delta) {
  if (!consoleState.inputHistory.length) return;
  const idx = Math.max(0, Math.min(consoleState.inputHistory.length, consoleState.historyIndex + delta));
  consoleState.historyIndex = idx;
  consoleState.buffer = idx < consoleState.inputHistory.length ? consoleState.inputHistory[idx] : '';
  renderConsole();
}

function renderConsole() {
  if (!consoleCtx) return;
  const lines = [];
  for (const item of consoleState.transcript) {
    lines.push('$ ' + item.input);
    for (const outLine of (item.output || '').split('\n')) {
      if (outLine) lines.push(outLine);
    }
    if (item.code !== null && item.code !== undefined) lines.push(`(code de sortie ${item.code})`);
  }
  lines.push('$ ' + consoleState.buffer + (consoleState.running ? ' …' : ''));
  consoleCtx.dock.clear('console');
  consoleCtx.dock.log('console', ...lines);
}

async function executeConsole() {
  const raw = consoleState.buffer.trim();
  consoleState.buffer = '';
  if (!raw) { renderConsole(); return; }
  consoleState.inputHistory.push(raw);
  consoleState.historyIndex = consoleState.inputHistory.length;
  const argv = raw.replace(/^grimoire\s+/, '').split(/\s+/);
  const entry = { input: raw, output: '', code: null };
  consoleState.transcript.push(entry);
  consoleState.running = true;
  renderConsole();
  consoleCtx.dock.echo('grimoire ' + argv.join(' '));
  try {
    const result = await consoleCtx.api.run(argv);
    entry.output = result.output || '(aucune sortie)';
    entry.code = result.code;
    if (result.timed_out) entry.output += '\n(délai dépassé)';
  } catch (error) {
    // Le refus explicite (liste blanche, drapeau, hôte) : la raison vient du
    // serveur (workspace_exec.CommandRefusedError), affichée telle quelle.
    entry.output = 'refusé : ' + error.message;
  } finally {
    consoleState.running = false;
    renderConsole();
  }
}

function commonPrefix(words) {
  if (!words.length) return '';
  let prefix = words[0];
  for (const word of words.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < word.length && prefix[i] === word[i]) i += 1;
    prefix = prefix.slice(0, i);
  }
  return prefix;
}

async function completeConsole() {
  if (!consoleState.catalogue) {
    try { consoleState.catalogue = (await consoleCtx.api.commands()).commands; }
    catch { consoleState.catalogue = []; }
  }
  const withoutPrefix = consoleState.buffer.replace(/^grimoire\s*/, '');
  const matches = consoleState.catalogue.map((c) => c.key).filter((k) => k.startsWith(withoutPrefix));
  if (matches.length === 1) {
    consoleState.buffer = 'grimoire ' + matches[0] + ' ';
  } else if (matches.length > 1) {
    const common = commonPrefix(matches);
    if (common.length > withoutPrefix.length) consoleState.buffer = 'grimoire ' + common;
    consoleState.transcript.push({
      input: consoleState.buffer,
      output: matches.map((m) => 'grimoire ' + m).join('   '),
      code: null,
    });
  }
  renderConsole();
}
