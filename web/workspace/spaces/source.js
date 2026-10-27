// Espace Source — les fichiers par étage, l'éditeur, le diff contre le kit,
// la prise d'override, l'inspecteur de provenance. Écran nouveau : rien à
// remplacer (ADR-006, lot 5).
//
// La Console du dock n'est plus ici (P06) : c'est un vrai champ de saisie que
// la coque possède (`shell.js`, « Console du dock »). Ce module n'écrit dans
// le dock que ce qui le concerne — l'onglet Problèmes et la trace de ses
// enregistrements —, et seulement tant qu'il est monté (`ctx.signal`).
//
// API consommées : api.files(tier), api.file(path), api.fileDiff(path),
// api.fileUsage(path), api.fileHistory(path), api.createOverride(path),
// api.writeFile(path, text), api.doctor(),
// api.language(path, {text, pos}). L'assistance par modèle local (#280, voie
// 2 — api.assistStatus(), api.assist(path, {...})) vit entièrement dans
// source-editor.js, jamais ici.

import * as sourceEditor from './source-editor.js';
import { actionStatus, failureText, setActionStatus, stateIcon } from '../shell.js';
import { renderMarkdown } from '../markdown.js';

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
    treeEl: null,
    dirsOpen: new Set(),
    currentPath: null,
    currentEntry: null,
    view: 'source',
    inspectorTab: 'fichier',
    dirty: false,
    draft: '',
    // Retour du dernier enregistrement ou override (`{ text, tone }`), posé
    // sous la barre du fichier : la Console n'est plus dans la coque (P62).
    notice: null,
  };
}

let state = freshState();

// ── Montage ──────────────────────────────────────────────────────────────

export async function mount(root, ctx) {
  ensureStylesheet();
  state = freshState();
  populateProblems(ctx);

  ctx.docbar.setBreadcrumb([]);
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
    if (ctx.signal.aborted) return;
    root.append(ctx.empty(
      'Source injoignable',
      "L'API locale n'a pas répondu pour /api/workspace/files : " + error.message,
      'grimoire doctor',
    ));
    return;
  }
  // Rubrique quittée pendant la lecture : rien de ce qui suit ne la concerne plus.
  if (ctx.signal.aborted) return;
  state.tree = tree;
  // L'arbre vit dans le contenu de la rubrique (P66), une colonne à gauche du
  // fichier ouvert : l'explorateur d'atelier (⇧⌘L) reste celui de la coque.
  const split = document.createElement('div');
  split.className = 'sr-split';
  const treeEl = document.createElement('nav');
  treeEl.className = 'tree sr-tree';
  treeEl.setAttribute('aria-label', 'Arborescence des fichiers');
  const pane = document.createElement('div');
  pane.className = 'sr-pane';
  split.append(treeEl, pane);
  root.replaceChildren(split);
  state.treeEl = treeEl;
  root = pane;
  renderExplorer(ctx, root);
  renderCanvas(root, ctx);

  // ⌘K « fichier de Source » : la coque passe le chemin choisi dans
  // `ctx.params.file` plutôt que dans le hash (README, « aucun module
  // d'espace n'écrit dans le DOM de l'autre » — et le hash n'est pas fait
  // pour porter un chemin arbitraire). Sans ce relais, la palette listait
  // les fichiers mais n'en ouvrait jamais un. `ctx.params.line` (1-indexée)
  // vient de Piloter → Propositions → « Ouvrir le fichier » pour une
  // réparation (#490) : la ligne citée dans `artifact_ref`.
  if (ctx.params && ctx.params.file) {
    // Venue d'un lien profond, la ligne est du texte (`#…?line=12`).
    await openFile(ctx, root, ctx.params.file, Number(ctx.params.line) || undefined);
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

// Le statut d'un fichier en lettre façon git (P30), jamais en pastille sans
// mot : la lettre se lit sans la couleur, le `title` la dit en clair. Le kit
// joue le rôle de la révision de base ; tout se déduit des champs du serveur
// (`masks_kit`, `diverges`, `shipped_by_kit`, `overridden`), rien n'est deviné.
//   M  override modifié : diverge du kit, contenu jamais livré par le kit
//   U  override à fusionner : copie d'une version livrée, le kit a changé depuis
//   O  override identique au kit, ou fichier du kit masqué par un override
//   D  le kit ne livre plus ce chemin (l'override en garde une version livrée)
//   A  ajouté par le projet : aucun équivalent dans le kit, ni livré
function fileBadge(entry) {
  if (entry.tier === 'overrides') {
    const counterpart = entry.kit_counterpart || '';
    if (entry.diverges && entry.shipped_by_kit) {
      const copy = entry.kit_version ? `copie du kit ${entry.kit_version}` : 'copie du kit';
      return { letter: 'U', tone: 'warn', title: `à fusionner : ${copy}, le kit a changé depuis — ${counterpart}` };
    }
    if (entry.diverges) return { letter: 'M', tone: 'warn', title: 'modifié : diverge du kit — ' + counterpart };
    if (entry.masks_kit) return { letter: 'O', tone: 'ok', title: 'override identique au kit' };
    if (entry.shipped_by_kit) return { letter: 'D', tone: 'bad', title: 'le kit ne livre plus ce chemin' };
    return { letter: 'A', tone: 'acc', title: 'ajouté par le projet : aucun équivalent dans le kit' };
  }
  if (entry.tier === 'kit' && entry.overridden) {
    return { letter: 'O', tone: 'acc', title: 'masqué par un override du projet' };
  }
  return null;
}

/** `dir` sans la racine de son étage (`_grimoire/kit/agents` → `agents`) ;
 *  la racine elle-même garde son chemin. */
function dirLabel(tier, dir) {
  for (const root of tier.roots || []) {
    const prefix = root.replace(/\/+$/, '') + '/';
    if (dir.startsWith(prefix)) return dir.slice(prefix.length);
  }
  return dir;
}

function renderExplorer(ctx, root) {
  state.treeEl.replaceChildren();
  for (const tier of state.tree.tiers) {
    const head = document.createElement('div');
    head.className = 'sr-tree-tier';
    if (tier.term) head.dataset.term = tier.term;
    head.textContent = `${tier.label} · ${tier.count}${tier.truncated ? ' (+ )' : ''}`;
    state.treeEl.append(head);

    if (!tier.exists) {
      const note = document.createElement('div');
      note.className = 'sr-tree-file lbl';
      note.textContent = tier.note ? `aucun fichier — ${tier.note}` : 'aucun fichier';
      state.treeEl.append(note);
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
        // Une ligne par dossier : nommé depuis la racine de son étage (le
        // titre de l'étage dit déjà « Kit »), chemin complet au survol.
        const label = document.createElement('span');
        label.textContent = `${open ? '▾' : '▸'} ${dirLabel(tier, dir)}/ · ${files.length}`;
        dirBtn.title = `${dir}/ · ${files.length} fichier(s)`;
        dirBtn.append(label);
        dirBtn.addEventListener('click', () => {
          if (open) state.dirsOpen.add('closed:' + dirKey);
          else state.dirsOpen.delete('closed:' + dirKey);
          renderExplorer(ctx, root);
        });
        state.treeEl.append(dirBtn);
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
        row.title = file.path;
        row.append(name);
        const badge = fileBadge(file);
        if (badge) {
          const letter = document.createElement('span');
          letter.className = `sr-badge sr-git ${badge.tone}`;
          letter.textContent = badge.letter;
          letter.title = badge.title;
          letter.setAttribute('aria-label', badge.title);
          row.append(letter);
        }
        row.addEventListener('click', () => openFile(ctx, root, file.path));
        state.treeEl.append(row);
      }
    }
  }
}

// ── Ouvrir un fichier ────────────────────────────────────────────────────

async function openFile(ctx, root, path, line) {
  if (state.dirty && !confirm('Des modifications non enregistrées seront perdues. Continuer ?')) {
    return;
  }
  state.currentPath = path;
  state.dirty = false;
  state.notice = null;
  state.inspectorTab = 'fichier';
  editorProblemLines = []; // le fichier change : les diagnostics de l'ancien ne s'appliquent plus
  renderProblems(ctx);
  let entry;
  try {
    entry = await ctx.api.file(path);
  } catch (error) {
    if (ctx.signal.aborted) return;
    state.currentEntry = null;
    root.replaceChildren(ctx.empty('Fichier illisible', error.message, `grimoire doctor`));
    return;
  }
  // Une réponse arrivée après le changement d'espace ne pose ni pastille, ni
  // URL, ni inspecteur sur l'espace suivant (P06).
  if (ctx.signal.aborted) return;
  state.currentEntry = entry;
  state.draft = state.currentEntry.text || '';
  // Le fichier ouvert entre dans l'URL : un lien copié le rouvre (P31).
  ctx.setItem?.({ file: path, ...(line ? { line } : {}) });
  renderExplorer(ctx, root);
  renderCanvas(root, ctx);
  renderInspector(ctx);
  ctx.docbar.setValidation(
    state.currentEntry.editable ? 'éditable' : 'lecture seule',
    state.currentEntry.editable ? 'ok' : 'warn',
  );
  if (line) jumpToLine(root, line);
}

// Place le curseur sur *line* (1-indexée) et fait défiler l'éditeur pour
// qu'elle soit visible — la seule chose que « Ouvrir le fichier » (Piloter →
// Propositions, réparation #490) demande : ni coloration ni sélection
// multi-ligne, juste amener l'humain au bon endroit du fichier cité.
function jumpToLine(root, line) {
  const textarea = root.querySelector('.sr-textarea');
  if (!textarea || !Number.isFinite(line) || line < 1) return;
  const lines = textarea.value.split('\n');
  const target = Math.min(line, lines.length) - 1;
  const offset = lines.slice(0, target).reduce((sum, l) => sum + l.length + 1, 0);
  textarea.focus();
  textarea.setSelectionRange(offset, offset + (lines[target] || '').length);
  const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 18;
  textarea.scrollTop = Math.max(0, (target - 5) * lineHeight);
}

// ── Canevas : Source / Diff / Rendu ─────────────────────────────────────────

function chipEtat(entry) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  let dot;
  if (!entry.editable) {
    dot = stateIcon('warn');
    chip.append(dot, document.createTextNode(' lecture seule'));
  } else if (entry.tier === 'overrides' && entry.masks_kit && !entry.diverges) {
    dot = stateIcon('ok');
    chip.append(dot, document.createTextNode(` identique au kit${entry.kit_version ? ' ' + entry.kit_version : ''}`));
  } else if (entry.tier === 'overrides' && entry.diverges) {
    dot = stateIcon('warn');
    chip.append(dot, document.createTextNode(' override — diverge du kit'));
  } else {
    dot = stateIcon('acc');
    chip.append(dot, document.createTextNode(' override'));
  }
  return chip;
}

// Le bandeau d'un fichier en lecture seule dit ce qu'on peut en faire, selon
// son étage (P06). « Créer un override » n'existe que pour un fichier du kit
// dont le serveur a calculé l'override (`override_path`) : un pack de preuve,
// une projection d'hôte ou un fichier du standard sans override possible ont
// leur propre phrase, sans bouton — et aucun chemin n'est jamais inventé.
function readOnlyBanner(ctx, root, entry) {
  const banner = document.createElement('div');
  banner.className = 'sr-banner';
  const text = document.createElement('span');
  const mono = (value) => Object.assign(document.createElement('span'), { className: 'mono', textContent: value });
  let button = null;
  if (entry.tier === 'kit' && entry.override_path) {
    banner.dataset.term = 'kit';
    banner.dataset.kind = 'override';
    button = document.createElement('button');
    button.className = 'btn pri';
    button.type = 'button';
    if (entry.overridden) {
      text.append('Un override de ce fichier existe déjà : ', mono(entry.override_path), '. C’est lui qui prime.');
      button.textContent = 'Ouvrir l’override';
      button.addEventListener('click', () => openFile(ctx, root, entry.override_path));
    } else {
      text.append('Ce fichier est généré par le kit. Le modifier crée ', mono(entry.override_path), ', qui prime et survit aux mises à jour.');
      button.textContent = 'Créer un override';
      button.addEventListener('click', () => createOverrideAndOpen(ctx, root));
    }
  } else if (entry.tier === 'evidence') {
    banner.dataset.term = 'evidence-pack';
    banner.dataset.kind = 'evidence';
    text.append('Pack de preuve : il est écrit par la tâche qui le produit, jamais à la main.');
  } else if (entry.tier === 'projections') {
    banner.dataset.term = 'projection';
    banner.dataset.kind = 'projection';
    text.append('Projection régénérée depuis le kit pour un hôte : une modification ici serait perdue à la prochaine projection.');
  } else {
    banner.dataset.term = 'kit';
    banner.dataset.kind = 'generated';
    text.append('Fichier généré, sans override possible à cet emplacement : il se régénère à chaque mise à jour.');
  }
  banner.append(stateIcon('acc'), text, ...(button ? [button] : []));
  return banner;
}

function renderCanvas(root, ctx) {
  root.replaceChildren();
  if (!state.currentPath || !state.currentEntry) {
    const total = state.tree ? state.tree.tiers.reduce((sum, t) => sum + t.count, 0) : 0;
    root.append(ctx.empty(
      'Source',
      total
        ? `${total} fichier(s) répartis sur ${state.tree.tiers.length} étages. Choisissez un fichier dans l'arborescence.`
        : "Aucun fichier trouvé. `grimoire init` puis `grimoire standard init` peuplent les étages.",
      'grimoire doctor',
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
  wrap.append(actionStatus(state.notice && state.notice.text, state.notice && state.notice.tone));

  if (!entry.editable && state.view === 'source') wrap.append(readOnlyBanner(ctx, root, entry));

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

// ── Enregistrer / créer un override ─────────────────────────────────────────

//
// Le dock n'est visible qu'en mode atelier (P62) : chaque action y garde sa
// trace, et rend aussi son résultat sous la barre du fichier. Le bouton se
// désactive avec un libellé d'attente le temps de la requête ; un refus garde
// le brouillon intact, l'éditeur n'est pas reconstruit.

let pending = false;

/** Gèle `button` le temps de `action` ; un refus s'affiche dans la zone de
 *  retour sans reconstruire l'éditeur (le brouillon reste où il est). */
async function runInline(ctx, root, button, pendingLabel, action) {
  if (pending) return;
  pending = true;
  const label = button && button.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = pendingLabel;
    button.setAttribute('aria-busy', 'true');
  }
  const status = root.querySelector('.sr-editor > .action-status');
  setActionStatus(status, '');
  try {
    await action();
  } catch (error) {
    ctx.dock.log('console', `refusé · ${error.message}`);
    state.notice = { text: failureText(error), tone: 'bad' };
    setActionStatus(status, state.notice.text, 'bad');
    if (button && button.isConnected) {
      button.textContent = label;
      button.disabled = false;
    }
  } finally {
    pending = false;
    if (button) button.removeAttribute('aria-busy');
  }
}

function saveCurrent(ctx, root) {
  const entry = state.currentEntry;
  if (!entry || !entry.editable) return undefined;
  const button = root.querySelector('.sr-docrow .btn:not(.pri)');
  return runInline(ctx, root, button, 'Enregistrement…', async () => {
    const updated = await ctx.api.writeFile(entry.path, state.draft);
    if (ctx.signal.aborted) return;
    state.currentEntry = updated;
    state.draft = updated.text || '';
    state.dirty = false;
    ctx.dock.log('console', `$ enregistré · ${entry.path}`);
    ctx.dock.echo('POST /api/workspace/file/write');
    state.notice = { text: `Enregistré : ${entry.path}`, tone: 'ok' };
    renderCanvas(root, ctx);
    renderInspector(ctx);
  });
}

function createOverrideAndOpen(ctx, root) {
  const entry = state.currentEntry;
  if (!entry) return undefined;
  const button = root.querySelector('.sr-banner .btn');
  return runInline(ctx, root, button, 'Création…', async () => {
    const created = await ctx.api.createOverride(entry.path);
    ctx.dock.log('console', `$ override créé · ${created.override_path}`);
    ctx.dock.echo('POST /api/workspace/file/override');
    await ctx.api.files().then((tree) => { state.tree = tree; renderExplorer(ctx, root); });
    await openFile(ctx, root, created.override_path);
    // `openFile` vient de construire l'éditeur : on remplit sa zone de retour
    // sans le reconstruire (l'assistance et la saisie y ont déjà démarré).
    state.notice = {
      text: `Override créé : ${created.override_path} — il prime sur le fichier du kit et survit aux mises à jour.`,
      tone: 'ok',
    };
    setActionStatus(root.querySelector('.sr-editor > .action-status'), state.notice.text, 'ok');
  });
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
  doctorLines = ['diagnostic en cours — grimoire doctor…'];
  renderProblems(ctx);
  try {
    const report = await ctx.api.doctor();
    doctorLines = report.lines.length ? report.lines : ['aucune ligne — ' + report.command];
  } catch (error) {
    doctorLines = ['diagnostic indisponible · ' + error.message];
  }
  if (ctx.signal.aborted) return;
  renderProblems(ctx);
}

function updateEditorProblems(ctx, path, diagnostics) {
  editorProblemLines = diagnostics.map(
    (d) => `${path}:${d.line + 1}:${d.start + 1} — [${d.severity}] ${d.family} — ${d.message}`,
  );
  renderProblems(ctx);
}
