// L'éditeur de l'espace Source — colorisation, diagnostics, complétion (#280).
//
// Surcouche par recouvrement sur une <textarea> réelle (ADR-006 D2 : pas de
// bundler, pas de dépendance embarquée — CodeMirror pèserait 500 Ko pour ce
// que sert ce module en quelques Ko). Le texte de la `<textarea>` devient
// transparent ; un `<pre>` figé dessous porte les tokens coloriés, aligné
// caractère pour caractère (même police, même padding, même interligne). La
// `<textarea>` reste la seule source de vérité du texte et le seul élément
// qui reçoit le focus, la sélection et la frappe — ce module ne fait
// jamais de `contenteditable`.
//
// Le contrat DOM que `tests/e2e/test_workspace_source.py` connaît déjà
// (`.sr-textarea`, le bouton Enregistrer, Ctrl+S, Tab) ne change pas : ce
// module construit ce que `source.js` construisait, plus la colorisation, la
// gouttière de diagnostics et la complétion.
//
// Tokens, diagnostics et complétions viennent tous de
// `GET /api/workspace/language` (`src/grimoire/tools/workspace_language.py`)
// — rien n'est recalculé côté client, ce module ne fait qu'afficher.
//
// Survol d'un identifiant → infobulle du glossaire (spec « survol d'un
// identifiant… quand un id existe ») : la surcouche colorée a
// `pointer-events: none` (la textarea, au-dessus, doit rester la seule à
// recevoir clic et frappe), donc ses spans `[data-term]` ne reçoivent jamais
// le survol natif que `glossary.attach()` écoute ailleurs dans la coque. Ce
// module traduit lui-même la position de la souris sur la textarea en
// (ligne, colonne) — même arithmétique que le popup de complétion — et
// rappelle `glossary.open()`/`closeAll()` directement : c'est la même pile
// épinglable, seulement déclenchée sans DOM survolable.
import glossary from '../glossary.js';

const LINE_HEIGHT = 20; // px — doit rester égal à la valeur de source.css
// Repos de saisie avant recalcul des diagnostics (issue 280), en ms.
const DIAGNOSTIC_DELAY = 300;
const TRIGGER_CHARS = new Set(['{', '@', '/']);
const HOVER_DELAY = 500; // ms — même délai que le reste de la vue de travail (spec §3.2)
const PAD_LEFT = 12; // var(--sp-3)
const PAD_TOP = 8; // var(--sp-2)

let charWidthCache = null;

function charWidth() {
  if (charWidthCache) return charWidthCache;
  const probe = document.createElement('span');
  probe.textContent = '0';
  probe.style.position = 'absolute';
  probe.style.visibility = 'hidden';
  probe.style.whiteSpace = 'pre';
  probe.style.font = `var(--t-s) var(--mono)`;
  probe.style.fontFamily = 'var(--mono)';
  probe.style.fontSize = 'var(--t-s)';
  document.body.append(probe);
  charWidthCache = probe.getBoundingClientRect().width || 7.2;
  probe.remove();
  return charWidthCache;
}

function escapeText(text) {
  return text; // createTextNode échappe déjà — aucune balise n'est jamais interprétée.
}

// ── La surcouche colorée ─────────────────────────────────────────────────

function renderHighlight(highlight, text, tokens) {
  const lines = text.split('\n');
  const byLine = new Map();
  for (const tok of tokens) {
    if (!byLine.has(tok.line)) byLine.set(tok.line, []);
    byLine.get(tok.line).push(tok);
  }
  highlight.replaceChildren();
  lines.forEach((line, i) => {
    const toks = (byLine.get(i) || []).slice().sort((a, b) => a.start - b.start);
    let cursor = 0;
    for (const tok of toks) {
      if (tok.start > cursor) highlight.append(document.createTextNode(escapeText(line.slice(cursor, tok.start))));
      const span = document.createElement('span');
      span.className = `tok tok-${tok.kind}` + (tok.glossaryId ? ' tok-linked' : '');
      span.textContent = line.slice(tok.start, tok.end);
      // Pas de `data-term` ici : `.sr-highlight` a `pointer-events: none` (la
      // textarea, au-dessus, doit rester seule à recevoir clic et frappe), et
      // un span avec `data-term` mais sans pointeur deviendrait quand même un
      // arrêt Tab via `glossary.js` (`ensureFocusable` ne connaît pas cette
      // surcouche) — un piège clavier avant même d'atteindre la textarea. Le
      // survol de la souris est géré à la place par un calcul de position
      // (voir plus bas, `tokenAt`/`positionFromEvent`), qui rappelle
      // `glossary.open()` sans jamais poser l'attribut ici.
      highlight.append(span);
      cursor = Math.max(cursor, tok.end);
    }
    if (cursor < line.length) highlight.append(document.createTextNode(escapeText(line.slice(cursor))));
    if (i < lines.length - 1) highlight.append(document.createTextNode('\n'));
  });
}

// ── La gouttière : numéros + marqueurs de diagnostic ────────────────────────

function renderGutter(gutterNums, gutterMarks, lineCount, diagnostics, onHoverLine) {
  gutterNums.textContent = Array.from({ length: lineCount }, (_, i) => String(i + 1)).join('\n');
  gutterMarks.replaceChildren();
  const byLine = new Map();
  for (const diag of diagnostics) {
    const worst = byLine.get(diag.line);
    if (!worst || (worst.severity === 'warning' && diag.severity === 'error')) byLine.set(diag.line, diag);
  }
  for (const [line, worst] of byLine) {
    const mark = document.createElement('span');
    mark.className = `dot ${worst.severity === 'error' ? 'bad' : 'warn'} sr-gutter-dot`;
    mark.style.top = `${line * LINE_HEIGHT + 3}px`;
    mark.dataset.line = String(line);
    gutterMarks.append(mark);
  }
  gutterMarks.onpointerover = (event) => {
    const mark = event.target.closest('.sr-gutter-dot');
    if (!mark) return;
    const line = Number(mark.dataset.line);
    onHoverLine(mark, diagnostics.filter((d) => d.line === line));
  };
  gutterMarks.onpointerout = (event) => {
    if (event.target.closest('.sr-gutter-dot')) onHoverLine(null, []);
  };
}

// ── Infobulle de diagnostic — pas la pile du glossaire : un survol simple,
// non épinglable, propre à la gouttière. ─────────────────────────────────

function createDiagTip() {
  const tip = document.createElement('div');
  tip.className = 'sr-diag-tip';
  tip.hidden = true;
  document.body.append(tip);
  return tip;
}

function showDiagTip(tip, anchor, diagnostics) {
  if (!anchor || !diagnostics.length) {
    tip.hidden = true;
    return;
  }
  tip.replaceChildren();
  for (const diag of diagnostics) {
    const row = document.createElement('div');
    row.className = 'sr-diag-tip-row';
    const dot = document.createElement('span');
    dot.className = `dot ${diag.severity === 'error' ? 'bad' : 'warn'}`;
    row.append(dot, document.createTextNode(diag.message));
    tip.append(row);
  }
  tip.hidden = false;
  const box = anchor.getBoundingClientRect();
  tip.style.left = `${Math.max(8, box.left)}px`;
  tip.style.top = `${box.bottom + 6}px`;
}

// ── Complétion : Ctrl+Espace ou déclenchée par {, @, / ──────────────────────

function createCompletionMenu() {
  const menu = document.createElement('ul');
  menu.className = 'sr-complete';
  menu.hidden = true;
  document.body.append(menu);
  return menu;
}

function placeCompletionMenu(menu, textarea, line, col) {
  const box = textarea.getBoundingClientRect();
  const left = box.left + PAD_LEFT + col * charWidth() - textarea.scrollLeft;
  const top = box.top + PAD_TOP + (line + 1) * LINE_HEIGHT - textarea.scrollTop;
  menu.style.left = `${Math.max(8, Math.min(left, window.innerWidth - 260))}px`;
  menu.style.top = `${top + 4 + 300 > window.innerHeight ? Math.max(8, top - 4 - 160) : top + 4}px`;
}

// ── Survol d'un identifiant colorié : infobulle du glossaire ───────────────
//
// La police est monospace, donc la position (ligne, colonne) sous la souris
// se déduit de sa position pixel sans mesure DOM par caractère — même
// approximation que le popup de complétion, l'erreur reste sous un caractère.

function positionFromEvent(textarea, event) {
  const box = textarea.getBoundingClientRect();
  const x = event.clientX - box.left - PAD_LEFT + textarea.scrollLeft;
  const y = event.clientY - box.top - PAD_TOP + textarea.scrollTop;
  return { line: Math.max(0, Math.floor(y / LINE_HEIGHT)), col: Math.max(0, Math.round(x / charWidth())) };
}

function tokenAt(tokens, line, col) {
  return tokens.find((t) => t.line === line && col >= t.start && col < t.end && t.glossaryId);
}

function tokenScreenRect(textarea, tok) {
  const box = textarea.getBoundingClientRect();
  return {
    left: box.left + PAD_LEFT + tok.start * charWidth() - textarea.scrollLeft,
    top: box.top + PAD_TOP + tok.line * LINE_HEIGHT - textarea.scrollTop,
    width: (tok.end - tok.start) * charWidth(),
    height: LINE_HEIGHT,
  };
}

function renderCompletionMenu(menu, items, onPick) {
  menu.replaceChildren();
  if (!items.length) {
    menu.hidden = true;
    return;
  }
  items.forEach((item, i) => {
    const li = document.createElement('li');
    li.setAttribute('aria-selected', String(i === 0));
    li.dataset.index = String(i);
    const label = document.createElement('span');
    label.className = 'mono';
    label.textContent = item.label;
    const kind = document.createElement('span');
    kind.className = 'cmd';
    kind.textContent = item.kind;
    li.append(label, kind);
    li.addEventListener('mousedown', (event) => {
      event.preventDefault(); // ne pas voler le focus de la textarea
      onPick(item);
    });
    menu.append(li);
  });
  menu.hidden = false;
}

function moveCompletionSelection(menu, delta) {
  const items = [...menu.querySelectorAll('li')];
  if (!items.length) return;
  const current = items.findIndex((li) => li.getAttribute('aria-selected') === 'true');
  const next = (current + delta + items.length) % items.length;
  items.forEach((li, i) => li.setAttribute('aria-selected', String(i === next)));
  items[next].scrollIntoView({ block: 'nearest' });
}

function selectedCompletion(menu, items) {
  const li = menu.querySelector('li[aria-selected="true"]');
  return li ? items[Number(li.dataset.index)] : null;
}

// ── Position (ligne, colonne) depuis l'index de sélection de la textarea ────

function positionOf(text, index) {
  const upTo = text.slice(0, index);
  const lines = upTo.split('\n');
  return { line: lines.length - 1, col: lines[lines.length - 1].length };
}

// ── Point d'entrée ───────────────────────────────────────────────────────────

/**
 * Construit l'éditeur de l'espace Source : gouttière, textarea, surcouche
 * colorée, diagnostics, complétion. Rend l'élément `.sr-code` à insérer dans
 * le canevas — même rôle que l'ancienne `buildSourceView` interne.
 *
 * @param {object} ctx contexte de l'espace (voir web/workspace/README.md)
 * @param {object} entry l'entrée de fichier courante (`ctx.api.file`)
 * @param {object} state état mutable de l'espace : lit/écrit `state.draft`,
 *   `state.dirty` — mêmes champs que `source.js` utilisait déjà.
 * @param {object} hooks `{ onDirtyChange(), onSave(), onDiagnostics(diags) }`
 */
// Une seule instance d'éditeur à la fois porte des éléments flottants
// (infobulle de diagnostic, popup de complétion, ancre du glossaire) : `build`
// est rappelé à chaque ouverture de fichier et à chaque prise d'override —
// bien plus souvent que le démontage de l'espace Source, qui est le seul
// moment où `ctx.signal` s'annule. Sans ce nettoyage explicite, chaque rappel
// laissait les éléments flottants du précédent orphelins dans le document.
let activeCleanup = null;

export function build(ctx, entry, state, hooks) {
  if (activeCleanup) activeCleanup();

  const code = document.createElement('div');
  code.className = 'sr-code';

  const gutter = document.createElement('div');
  gutter.className = 'sr-gutter';
  const gutterNums = document.createElement('div');
  gutterNums.className = 'sr-gutter-nums';
  const gutterMarks = document.createElement('div');
  gutterMarks.className = 'sr-gutter-marks';
  gutter.append(gutterNums, gutterMarks);

  const main = document.createElement('div');
  main.className = 'sr-code-main';
  const highlight = document.createElement('pre');
  highlight.className = 'sr-highlight';
  highlight.setAttribute('aria-hidden', 'true');

  const textarea = document.createElement('textarea');
  textarea.className = 'sr-textarea';
  textarea.spellcheck = false;
  textarea.value = state.draft;
  textarea.readOnly = !entry.editable;

  main.append(highlight, textarea);
  code.append(gutter, main);

  const diagTip = createDiagTip();
  const completionMenu = createCompletionMenu();
  let completionItems = [];
  let completionRequestId = 0; // écarte une réponse arrivée après une plus récente (frappe rapide)
  let diagnostics = [];
  let tokens = [];
  let debounceTimer = null;
  let completionDebounceTimer = null;
  let destroyed = false;

  // Anchor invisible réutilisée pour positionner la bulle du glossaire —
  // `glossary.open()` a besoin d'un élément dont il lit `getBoundingClientRect()`.
  const glossaryAnchor = document.createElement('span');
  glossaryAnchor.style.position = 'fixed';
  glossaryAnchor.style.pointerEvents = 'none';
  glossaryAnchor.style.opacity = '0';
  document.body.append(glossaryAnchor);
  let hoveredGlossaryId = null;
  let glossaryHoverTimer = null;

  function lineCount() {
    return state.draft.split('\n').length;
  }

  function syncGutterHeight() {
    textarea.rows = Math.max(lineCount(), 20);
  }

  function paint() {
    // La colorisation attend le serveur ; en son absence (offline, latence),
    // le texte reste lisible via la textarea elle-même — jamais invisible.
    renderHighlight(highlight, state.draft, []);
    renderGutter(gutterNums, gutterMarks, Math.max(lineCount(), 20), [], () => {});
  }

  async function refreshLanguage() {
    if (destroyed) return;
    let payload;
    try {
      payload = await ctx.api.language(entry.path, { text: state.draft });
    } catch {
      return; // API locale indisponible : la textarea brute reste utilisable.
    }
    if (destroyed) return;
    tokens = payload.tokens || [];
    renderHighlight(highlight, state.draft, tokens);
    diagnostics = payload.diagnostics || [];
    renderGutter(gutterNums, gutterMarks, Math.max(lineCount(), 20), diagnostics, (anchor, diags) => {
      showDiagTip(diagTip, anchor, diags);
    });
    hooks.onDiagnostics(diagnostics);
  }

  function scheduleLanguageRefresh(delay = DIAGNOSTIC_DELAY) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refreshLanguage, delay);
  }

  // Distinct de `completionMenu.hidden` : une frappe rapide relance la
  // requête *avant* que la précédente ait eu le temps de rendre et de lever
  // `hidden` — attendre le rendu pour décider de continuer à interroger
  // revenait à figer la liste sur le tout premier caractère tapé après le
  // déclencheur (`@c`, `@co`, `@con`… ignorés, seul `@` comptait).
  let completionActive = false;

  function closeCompletion() {
    completionMenu.hidden = true;
    completionItems = [];
    completionActive = false;
  }

  async function openCompletion() {
    completionActive = true;
    const { line, col } = positionOf(textarea.value, textarea.selectionStart);
    const requestId = ++completionRequestId;
    let payload;
    try {
      payload = await ctx.api.language(entry.path, { text: state.draft, pos: { line, col } });
    } catch {
      return;
    }
    // Une frappe plus récente a déjà relancé la requête : cette réponse, plus
    // lente, ne doit pas écraser une liste déjà à jour avec un préfixe périmé.
    if (requestId !== completionRequestId || destroyed) return;
    completionItems = payload.completions || [];
    if (!completionItems.length) {
      closeCompletion();
      return;
    }
    renderCompletionMenu(completionMenu, completionItems, (item) => acceptCompletion(item));
    placeCompletionMenu(completionMenu, textarea, line, col);
  }

  function acceptCompletion(item) {
    const start = textarea.selectionStart;
    const before = textarea.value.slice(0, start);
    const wordMatch = /[\w./@-]*$/.exec(before);
    const wordStart = wordMatch ? start - wordMatch[0].length : start;
    const trigger = before.slice(wordStart, wordStart + 1);
    // `@`/`/` restent à l'écran : seul l'identifiant qui suit est remplacé.
    const replaceFrom = trigger === '@' || trigger === '/' ? wordStart + 1 : wordStart;
    const insert = item.insertText;
    textarea.value = textarea.value.slice(0, replaceFrom) + insert + textarea.value.slice(start);
    const caret = replaceFrom + insert.length;
    textarea.selectionStart = textarea.selectionEnd = caret;
    closeCompletion();
    textarea.dispatchEvent(new Event('input'));
    textarea.focus();
  }

  textarea.addEventListener('input', (event) => {
    state.draft = textarea.value;
    state.dirty = state.draft !== (entry.text || '');
    syncGutterHeight();
    renderHighlight(highlight, state.draft, []); // remise à plat immédiate, sans attendre le serveur
    hooks.onDirtyChange();
    scheduleLanguageRefresh();

    const typed = event.data;
    if (typed && TRIGGER_CHARS.has(typed)) {
      void openCompletion(); // le déclencheur lui-même : réponse immédiate, pas de repos à attendre
    } else if (completionActive) {
      // Continuer de taper après `@`/`/`/`{` doit réinterroger — mais pas à
      // chaque caractère : un chemin comme `_grimoire/kit/agents/…` a assez
      // de `/` pour lancer une requête par lettre si rien ne les regroupe, et
      // la fenêtre de retard sur la dernière fait alors errer une réponse en
      // retard sous une plus récente (cf. `completionRequestId`).
      clearTimeout(completionDebounceTimer);
      completionDebounceTimer = setTimeout(openCompletion, 120);
    }
  });

  textarea.addEventListener('keydown', (event) => {
    if (!completionMenu.hidden) {
      if (event.key === 'ArrowDown') { event.preventDefault(); moveCompletionSelection(completionMenu, 1); return; }
      if (event.key === 'ArrowUp') { event.preventDefault(); moveCompletionSelection(completionMenu, -1); return; }
      if (event.key === 'Enter' || event.key === 'Tab') {
        const picked = selectedCompletion(completionMenu, completionItems);
        if (picked) { event.preventDefault(); acceptCompletion(picked); return; }
      }
      if (event.key === 'Escape') { event.preventDefault(); closeCompletion(); return; }
    }

    const meta = event.metaKey || event.ctrlKey;
    if (meta && event.key === ' ') {
      event.preventDefault();
      void openCompletion();
      return;
    }
    if (meta && event.key.toLowerCase() === 's') {
      event.preventDefault();
      if (entry.editable) hooks.onSave();
      return;
    }
    if (event.key === 'Tab' && completionMenu.hidden) {
      event.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.slice(0, start) + '  ' + textarea.value.slice(end);
      textarea.selectionStart = textarea.selectionEnd = start + 2;
      textarea.dispatchEvent(new Event('input'));
    }
  });

  textarea.addEventListener('blur', () => {
    // Un clic dans le menu passe par `mousedown` (preventDefault), donc le
    // focus n'a jamais quitté la textarea à cet instant — un vrai blur ferme.
    setTimeout(closeCompletion, 120);
  });

  // Un clic hors du popup le referme — même geste que la palette de
  // commandes et la pile du glossaire (shell.js, glossary.js). Sans lui, un
  // popup resté ouvert après une frappe (`_grimoire/kit/…` déclenche `/` à
  // chaque segment) reste posé au-dessus d'un contrôle plus loin dans la
  // page et avale le clic qui devrait l'atteindre.
  function onDocumentClick(event) {
    if (completionMenu.hidden) return;
    if (event.target === textarea || completionMenu.contains(event.target)) return;
    closeCompletion();
  }
  document.addEventListener('mousedown', onDocumentClick);

  textarea.addEventListener('scroll', () => {
    gutterNums.scrollTop = textarea.scrollTop;
    gutterMarks.scrollTop = textarea.scrollTop;
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
  });

  function stopGlossaryHover() {
    clearTimeout(glossaryHoverTimer);
    hoveredGlossaryId = null;
    glossary.closeAll();
  }

  textarea.addEventListener('mousemove', (event) => {
    const { line, col } = positionFromEvent(textarea, event);
    const tok = tokenAt(tokens, line, col);
    const id = tok ? tok.glossaryId : null;
    if (id === hoveredGlossaryId) return;
    clearTimeout(glossaryHoverTimer);
    hoveredGlossaryId = id;
    if (!id) { glossary.closeAll(); return; }
    glossaryHoverTimer = setTimeout(() => {
      const rect = tokenScreenRect(textarea, tok);
      glossaryAnchor.style.left = `${rect.left}px`;
      glossaryAnchor.style.top = `${rect.top}px`;
      glossaryAnchor.style.width = `${rect.width}px`;
      glossaryAnchor.style.height = `${rect.height}px`;
      glossary.open(id, glossaryAnchor, 0, false);
    }, HOVER_DELAY);
  });

  textarea.addEventListener('mouseleave', stopGlossaryHover);

  activeCleanup = () => {
    destroyed = true;
    clearTimeout(debounceTimer);
    clearTimeout(completionDebounceTimer);
    clearTimeout(glossaryHoverTimer);
    glossary.closeAll();
    document.removeEventListener('mousedown', onDocumentClick);
    diagTip.remove();
    completionMenu.remove();
    glossaryAnchor.remove();
  };
  ctx.signal.addEventListener('abort', () => {
    if (activeCleanup) { activeCleanup(); activeCleanup = null; }
  });

  syncGutterHeight();
  paint();
  void refreshLanguage();

  return code;
}
