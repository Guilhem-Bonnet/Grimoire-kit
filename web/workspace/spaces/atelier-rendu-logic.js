// Atelier de spec : le rendu d'un bloc, côté logique pure (aucun DOM ; testé par tests/js/atelier_rendu.test.mjs).
//
// `parseBlocks` lit le Markdown d'un bloc (titres, paragraphes, listes imbriquées et cases à cocher, tableaux GFM, citations,
// blocs de code, règle horizontale) et rend un arbre de données ; `parseInline` fait de même pour le gras, l'italique, le barré,
// le code, les liens et les liens automatiques. Le DOM se construit dans `atelier-rendu.js`, jamais par `innerHTML`.
// Autour : la bascule d'une case sur sa seule ligne, le retrait du Markdown inline pour les libellés lisibles, et la
// reconnaissance du gabarit « À compléter » (le même critère que le découpage `tools/atelier_blocs.py`).

import { safeLinkUrl } from '../markdown.js';

const TASK = /^(\s*(?:[-*+]|\d{1,9}[.)])\s+\[)([ xX])(\](?:\s|$))/;
const ITEM = /^(\s*)([-*+]|(\d{1,9})[.)])\s+(.*)$/;
const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)/;
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const QUOTE = /^\s{0,3}>/;
const SEPARATOR_CELL = /^\s*:?-+:?\s*$/;

// ── Le gabarit « à compléter » ──────────────────────────────────────────────

/** Vrai pour le gabarit d'une rubrique non remplie : une citation qui contient « À compléter » (cf. `atelier_blocs.py`). */
export function isTemplateText(text) {
  const value = String(text == null ? '' : text);
  return value.trimStart().startsWith('>') && value.includes('À compléter');
}

/** Le gabarit sans son guillemet de citation : ce que l'on affiche en invite. */
export function templateHint(text) {
  return String(text == null ? '' : text).replace(/^\s*>\s?/gm, '').trim();
}

// ── Cases à cocher ──────────────────────────────────────────────────────────

/**
 * Le texte du bloc avec la case de la ligne `line` (0 = première ligne du bloc) cochée ou décochée ; toutes les autres lignes
 * restent identiques à l'octet près. `null` si cette ligne n'est pas une case.
 */
export function toggleTaskLine(text, line, checked) {
  const lines = String(text).split('\n');
  if (!Number.isInteger(line) || line < 0 || line >= lines.length) return null;
  const match = TASK.exec(lines[line]);
  if (!match) return null;
  lines[line] = match[1] + (checked ? 'x' : ' ') + lines[line].slice(match[0].length - match[3].length);
  return lines.join('\n');
}

// ── Markdown inline ─────────────────────────────────────────────────────────

const PUNCT = /[\\`*_{}[\]()#+\-.!|~<>]/;
const isWord = (c) => c !== undefined && /[\p{L}\p{N}]/u.test(c);
const isSpace = (c) => c === undefined || /\s/.test(c);

/** Fin du `)` qui ferme la parenthèse ouverte avant `from` (parenthèses équilibrées tolérées), ou -1. */
function closeParen(text, from) {
  let depth = 1;
  for (let i = from; i < text.length; i += 1) {
    const c = text[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '(') depth += 1;
    else if (c === ')') { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
}

function closeBracket(text, from) {
  let depth = 1;
  for (let i = from; i < text.length; i += 1) {
    const c = text[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '`') { const end = text.indexOf('`', i + 1); if (end > 0) i = end; continue; }
    if (c === '[') depth += 1;
    else if (c === ']') { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
}

/** La destination d'un lien `(url "titre")` : l'URL seule, sans titre ni chevrons. */
function destination(raw) {
  let value = raw.trim();
  if (value.startsWith('<') && value.endsWith('>')) return value.slice(1, -1);
  const title = /^(\S+)\s+(?:"[^"]*"|'[^']*')$/.exec(value);
  if (title) value = title[1];
  return value;
}

function bareUrlEnd(text, from) {
  let end = from;
  while (end < text.length && !/[\s<]/.test(text[end])) end += 1;
  while (end > from) {
    const c = text[end - 1];
    if (/[.,;:!?'"]/.test(c)) end -= 1;
    else if (c === ')' && (text.slice(from, end).match(/\(/g) || []).length < (text.slice(from, end).match(/\)/g) || []).length) end -= 1;
    else break;
  }
  return end;
}

/** Un texte Markdown inline en nœuds : `text`, `code`, `strong`, `em`, `del` et `link` (`href` déjà jugé sûr, sinon pas de lien). */
export function parseInline(source, inLink = false) {
  const text = String(source == null ? '' : source);
  const out = [];
  let buffer = '';
  const flush = () => { if (buffer) { out.push({ type: 'text', text: buffer }); buffer = ''; } };
  const push = (node) => { flush(); out.push(node); };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    const next = text[i + 1];
    if (c === '\\' && next !== undefined && PUNCT.test(next)) { buffer += next; i += 2; continue; }
    if (c === '`') {
      let run = 1;
      while (text[i + run] === '`') run += 1;
      const fence = '`'.repeat(run);
      const end = text.indexOf(fence, i + run);
      if (end > i + run - 1 && text[end + run] !== '`') {
        push({ type: 'code', text: text.slice(i + run, end).replace(/^ (.*[^ ].*) $/, '$1') });
        i = end + run;
        continue;
      }
      buffer += fence; i += run; continue;
    }
    if (c === '!' && next === '[') {
      const close = closeBracket(text, i + 2);
      if (close > 0 && text[close + 1] === '(') {
        const end = closeParen(text, close + 2);
        if (end > 0) { buffer += text.slice(i + 2, close); i = end + 1; continue; } // pas d'image : son texte alternatif
      }
    }
    if (c === '[') {
      const close = closeBracket(text, i + 1);
      if (close > 0 && text[close + 1] === '(') {
        const end = closeParen(text, close + 2);
        if (end > 0) {
          const children = parseInline(text.slice(i + 1, close), true);
          const href = safeLinkUrl(destination(text.slice(close + 2, end)));
          if (href) push({ type: 'link', href, children }); else { flush(); out.push(...children); }
          i = end + 1;
          continue;
        }
      }
    }
    if (c === '<' && !inLink) {
      const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i.exec(text.slice(i));
      if (auto) {
        const href = safeLinkUrl(auto[1]);
        if (href) push({ type: 'link', href, children: [{ type: 'text', text: auto[1] }] }); else buffer += auto[1];
        i += auto[0].length;
        continue;
      }
    }
    if (c === 'h' && !inLink && /^https?:\/\//i.test(text.slice(i, i + 8)) && !isWord(text[i - 1])) {
      const end = bareUrlEnd(text, i);
      const url = text.slice(i, end);
      const href = /^https?:\/\/[^\s/]+/i.test(url) ? safeLinkUrl(url) : '';
      if (href) { push({ type: 'link', href, children: [{ type: 'text', text: url }] }); i = end; continue; }
    }
    if ((c === '*' || c === '_') && next === c) {
      const end = text.indexOf(c + c, i + 2);
      if (end > i + 2 && !isSpace(text[i + 2]) && !isSpace(text[end - 1]) && !(c === '_' && (isWord(text[i - 1]) || isWord(text[end + 2])))) {
        push({ type: 'strong', children: parseInline(text.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    if (c === '~' && next === '~') {
      const end = text.indexOf('~~', i + 2);
      if (end > i + 2 && !isSpace(text[i + 2]) && !isSpace(text[end - 1])) {
        push({ type: 'del', children: parseInline(text.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    if ((c === '*' || c === '_') && next !== c && !isSpace(next) && !(c === '_' && isWord(text[i - 1]))) {
      let end = i + 1;
      for (;;) {
        end = text.indexOf(c, end + 1);
        if (end < 0 || (text[end - 1] !== '\\' && text[end + 1] !== c && !isSpace(text[end - 1]) && !(c === '_' && isWord(text[end + 1])))) break;
      }
      if (end > i + 1) {
        push({ type: 'em', children: parseInline(text.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }
    buffer += c;
    i += 1;
  }
  flush();
  return out;
}

/** Le texte lisible d'un nœud inline : sans balisage. */
function nodesText(nodes) {
  return nodes.map((n) => (n.children ? nodesText(n.children) : n.text)).join('');
}

/**
 * Un texte Markdown tel qu'on le lit : sans marqueurs de titre, de citation, de puce ou de case, sans gras, italique, barré,
 * code ni cible de lien (`[doc TVA](https://…)` → `doc TVA`). Les blancs sont ramenés à un seul : pour un libellé ou une citation.
 */
export function plainText(source) {
  const lines = String(source == null ? '' : source).split('\n').map((line) => line
    .replace(/^\s{0,3}(?:>\s?)+/, '')
    .replace(/^\s{0,3}#{1,6}\s+/, '')
    .replace(/^\s*(?:[-*+]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?/, '')
    .replace(/^\s*(?:```|~~~).*$/, ''));
  return nodesText(parseInline(lines.join(' '))).replace(/\s+/g, ' ').trim();
}

// ── Blocs ───────────────────────────────────────────────────────────────────

/** Les cellules d'une ligne de tableau (`\|` reste une barre dans la cellule). */
function splitRow(line) {
  let value = line.trim();
  if (value.startsWith('|')) value = value.slice(1);
  if (value.endsWith('|') && !value.endsWith('\\|')) value = value.slice(0, -1);
  const cells = [];
  let cell = '';
  for (let i = 0; i < value.length; i += 1) {
    if (value[i] === '\\' && value[i + 1] === '|') { cell += '|'; i += 1; } else if (value[i] === '|') { cells.push(cell.trim()); cell = ''; } else cell += value[i];
  }
  cells.push(cell.trim());
  return cells;
}

/** Les alignements d'une ligne de séparation (`:--`, `--:`, `:-:`, `---`), ou null si ce n'en est pas une. */
export function tableAlignments(line) {
  if (!line || !line.includes('-')) return null;
  const cells = splitRow(line);
  if (!cells.every((cell) => SEPARATOR_CELL.test(cell))) return null;
  return cells.map((cell) => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return left ? 'left' : null;
  });
}

const startsBlock = (line) => FENCE.test(line) || HR.test(line) || HEADING.test(line) || QUOTE.test(line)
  || /^\s{0,3}(?:[-*+]|1[.)])\s+/.test(line);

function tableAt(lines, i) {
  if (!lines[i].includes('|') || i + 1 >= lines.length) return null;
  const align = tableAlignments(lines[i + 1]);
  if (!align || !(lines[i + 1].includes('|') || align.length === 1)) return null;
  const head = splitRow(lines[i]);
  if (head.length !== align.length) return null;
  const rows = [];
  let j = i + 2;
  while (j < lines.length && lines[j].trim() && lines[j].includes('|') && !startsBlock(lines[j])) {
    const cells = splitRow(lines[j]);
    rows.push(head.map((_, n) => cells[n] || ''));
    j += 1;
  }
  return { node: { type: 'table', align, head, rows }, next: j };
}

function buildList(entries, from, indent) {
  const first = entries[from];
  const list = { type: 'list', ordered: first.ordered, start: first.start, items: [] };
  let pos = from;
  while (pos < entries.length) {
    const entry = entries[pos];
    const last = list.items[list.items.length - 1];
    if (entry.cont) { last.text += ' ' + entry.text.trim(); pos += 1; continue; }
    if (entry.indent < indent) break;
    if (entry.indent > indent) {
      const [child, after] = buildList(entries, pos, entry.indent);
      last.children.push(child);
      pos = after;
      continue;
    }
    if (entry.ordered !== list.ordered) break;
    const task = /^\[([ xX])\](?:\s+|$)/.exec(entry.content);
    list.items.push({
      text: task ? entry.content.slice(task[0].length) : entry.content,
      task: task ? (task[1] === ' ' ? 'todo' : 'done') : null,
      line: entry.line,
      children: [],
    });
    pos += 1;
  }
  return [list, pos];
}

/**
 * Le Markdown d'un bloc en arbre : `heading`, `paragraph` (lignes), `list` (items imbriqués ; une case porte `task` et `line`,
 * son numéro de ligne dans le bloc), `quote` (blocs), `code`, `hr`, `table` (en-têtes, alignements, lignes).
 * `taskLines: false` (dans une citation) : les cases n'ont pas de numéro de ligne exploitable (`line` nul).
 */
export function parseBlocks(source, { taskLines = true } = {}) {
  const lines = String(source == null ? '' : source).split('\n').map((l) => l.replace(/\r$/, ''));
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !(lines[i].trim().startsWith(fence[1][0].repeat(fence[1].length)) && /^\s*[`~]+\s*$/.test(lines[i]))) { body.push(lines[i]); i += 1; }
      i += 1;
      out.push({ type: 'code', lang: fence[2], text: body.join('\n') });
      continue;
    }
    if (HR.test(line)) { out.push({ type: 'hr' }); i += 1; continue; }
    const heading = HEADING.exec(line);
    if (heading) { out.push({ type: 'heading', level: heading[1].length, text: heading[2] }); i += 1; continue; }
    const table = tableAt(lines, i);
    if (table) { out.push(table.node); i = table.next; continue; }
    if (QUOTE.test(line)) {
      const inner = [];
      while (i < lines.length && QUOTE.test(lines[i])) { inner.push(lines[i].replace(/^\s{0,3}>\s?/, '')); i += 1; }
      out.push({ type: 'quote', blocks: parseBlocks(inner.join('\n'), { taskLines: false }) });
      continue;
    }
    if (ITEM.test(line)) {
      const entries = [];
      let j = i;
      while (j < lines.length) {
        const item = ITEM.exec(lines[j]);
        if (item && !HR.test(lines[j])) {
          entries.push({ indent: item[1].replace(/\t/g, '    ').length, ordered: item[3] !== undefined, start: item[3] === undefined ? 1 : Number(item[3]), content: item[4], line: taskLines ? j : null });
          j += 1;
        } else if (lines[j].trim() && /^\s{2,}\S/.test(lines[j]) && !FENCE.test(lines[j])) {
          entries.push({ cont: true, text: lines[j] });
          j += 1;
        } else if (!lines[j].trim() && j + 1 < lines.length && ITEM.test(lines[j + 1])) {
          j += 1;
        } else break;
      }
      let pos = 0;
      while (pos < entries.length) {
        const [list, after] = buildList(entries, pos, entries[pos].indent);
        out.push(list);
        pos = after;
      }
      i = j;
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && (para.length === 0 || !startsBlock(lines[i])) && !(para.length && tableAt(lines, i))) {
      para.push(lines[i].trim());
      i += 1;
    }
    out.push({ type: 'paragraph', lines: para });
  }
  return out;
}
