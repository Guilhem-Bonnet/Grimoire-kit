// Atelier de spec : le rendu DOM d'un bloc (tableaux, cases à cocher, listes, citations, code…).
//
// Le Markdown est lu par `atelier-rendu-logic.js` (sans DOM) ; ici, on ne fait que construire des nœuds (`createElement`,
// `textContent`), jamais de `innerHTML` : un texte du document ne devient jamais du HTML. Les liens sont déjà jugés sûrs par
// l'analyse (http, https, mailto, ancre ou chemin relatif) et portent `rel="noopener noreferrer"`.

import { parseBlocks, parseInline } from './atelier-rendu-logic.js';
import { hasRefs, splitRefs } from './atelier-liens-logic.js';

function make(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const WRAP = { strong: 'strong', em: 'em', del: 's' };

/**
 * Ajoute à `parent` le Markdown inline de `text` (gras, italique, barré, code, liens), sans HTML. `refs` (facultatif, voir
 * `atelier-liens.js`) : `resolve(type, valeur)` dit si une référence (`REQ-001`, tâche du kit, `[[chantier]]`) devient un lien ;
 * jamais dans du code ni dans un lien déjà posé.
 */
export function renderInline(parent, text, refs = null) {
  renderParts(parent, parseInline(text), refs);
  return parent;
}

/** Une référence du texte : un lien interne, le texte tel quel (rien à viser), ou un texte signalé « introuvable ». */
function refNode(piece, refs) {
  const ref = refs.resolve(piece.type, piece.value);
  if (!ref) return piece.text;
  if (ref.missing) {
    const span = make('span', 'at-ref at-ref-missing', { title: ref.title || '' });
    span.textContent = piece.text;
    return span;
  }
  const link = make('a', `at-ref at-ref-${piece.type}`, { href: ref.href, 'data-ref': piece.type });
  link.textContent = piece.text;
  if (ref.title) link.title = ref.title;
  // Un clic sur une référence la suit : il n'ouvre pas l'édition du bloc qui la porte.
  link.addEventListener('click', (event) => {
    event.stopPropagation();
    if (refs.activate) refs.activate(event, piece.type, piece.value);
  });
  if (refs.bind) refs.bind(link, piece.type, piece.value);
  return link;
}

function renderParts(parent, parts, refs = null) {
  for (const part of parts) {
    if (part.type === 'text') {
      if (refs && hasRefs(part.text)) parent.append(...splitRefs(part.text).map((piece) => (piece.type === 'text' ? piece.text : refNode(piece, refs))));
      else parent.append(part.text);
    } else if (part.type === 'code') { const code = make('code', 'at-code'); code.textContent = part.text; parent.append(code); }
    else if (part.type === 'link') {
      const link = make('a', null, { href: part.href, rel: 'noopener noreferrer' });
      if (/^https?:/i.test(part.href)) link.target = '_blank';
      // Un clic sur un lien suit le lien : il n'ouvre pas l'édition du bloc qui le porte.
      link.addEventListener('click', (event) => event.stopPropagation());
      renderParts(link, part.children); // pas de référence dans un lien : jamais de lien dans un lien
      parent.append(link);
    } else {
      const node = make(WRAP[part.type]);
      renderParts(node, part.children, refs);
      parent.append(node);
    }
  }
}

function lines(parent, rows, refs) {
  rows.forEach((row, index) => {
    if (index) parent.append(make('br'));
    renderInline(parent, row, refs);
  });
}

function tableNode(table, refs) {
  const wrap = make('div', 'at-md-tablewrap', { role: 'region', 'aria-label': 'Tableau', tabindex: '0' });
  const grid = make('table', 'at-md-table');
  const cell = (tag, text, index) => {
    const node = make(tag, table.align[index] ? `is-${table.align[index]}` : null);
    if (tag === 'th') node.setAttribute('scope', 'col');
    renderInline(node, text, refs);
    return node;
  };
  const head = make('thead');
  const headRow = make('tr');
  table.head.forEach((text, index) => headRow.append(cell('th', text, index)));
  head.append(headRow);
  const body = make('tbody');
  for (const row of table.rows) {
    const tr = make('tr');
    row.forEach((text, index) => tr.append(cell('td', text, index)));
    body.append(tr);
  }
  grid.append(head, body);
  wrap.append(grid);
  return wrap;
}

function listNode(list, opts) {
  const node = make(list.ordered ? 'ol' : 'ul');
  if (list.ordered && list.start !== 1) node.setAttribute('start', String(list.start));
  for (const item of list.items) {
    const li = make('li');
    if (item.task) {
      li.className = 'at-task';
      const label = make('label', 'at-task-label');
      const box = make('input', 'at-task-box', { type: 'checkbox', 'data-line': String(item.line == null ? '' : item.line) });
      box.checked = item.task === 'done';
      // Cochable seulement dans un bloc éditable, hors édition, et sur une ligne que l'on sait retrouver.
      box.disabled = !(opts.onToggle && item.line != null);
      label.addEventListener('click', (event) => event.stopPropagation()); // cocher n'ouvre pas l'édition du bloc
      box.addEventListener('change', () => opts.onToggle(item.line, box.checked, box));
      const text = make('span', item.task === 'done' ? 'at-task-text is-done' : 'at-task-text');
      renderInline(text, item.text, opts.refs);
      label.append(box, text);
      li.append(label);
    } else {
      renderInline(li, item.text, opts.refs);
    }
    for (const child of item.children) li.append(listNode(child, opts));
    node.append(li);
  }
  if (list.items.length && list.items.every((i) => i.task)) node.classList.add('at-tasks');
  return node;
}

function blocksInto(parent, blocks, opts) {
  for (const block of blocks) {
    switch (block.type) {
      case 'heading': {
        // Dans un bloc, un titre reste sous le titre de la phase (h2) : niveau 3 au plus haut.
        const level = Math.min(6, Math.max(3, block.level + 1));
        const node = make(`h${level}`, 'at-md-h');
        renderInline(node, block.text, opts.refs);
        parent.append(node);
        break;
      }
      case 'paragraph': { const p = make('p'); lines(p, block.lines, opts.refs); parent.append(p); break; }
      case 'list': parent.append(listNode(block, opts)); break;
      case 'table': parent.append(tableNode(block, opts.refs)); break;
      case 'hr': parent.append(make('hr', 'at-md-hr')); break;
      case 'quote': {
        const quote = make('blockquote', 'at-md-quote');
        blocksInto(quote, block.blocks, { ...opts, onToggle: null });
        parent.append(quote);
        break;
      }
      case 'code': {
        const pre = make('pre', 'at-md-code', { tabindex: '0' });
        const code = make('code', block.lang ? `lang-${block.lang}` : null);
        code.textContent = block.text;
        pre.append(code);
        parent.append(pre);
        break;
      }
      default:
    }
  }
}

/**
 * Le Markdown d'un bloc en nœuds. `onToggle(ligne, coché, case)` rend les cases cliquables (elle écrit le bloc) ; sans lui, les
 * cases sont désactivées (bloc non éditable, ou en cours d'édition).
 */
export function renderBlockMarkdown(text, { onToggle = null, refs = null } = {}) {
  const root = make('div', 'at-md');
  blocksInto(root, parseBlocks(text), { onToggle, refs });
  return root;
}
