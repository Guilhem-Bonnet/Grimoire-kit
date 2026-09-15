// Rendu Markdown minimal, sans dépendance (ADR-006 D2).
//
// Extrait de `spaces/source.js` (issue #506, PR B) pour que Piloter puisse
// rendre `preview.md`/`report.md` (le flow de mise à jour) sans dupliquer ce
// module ni faire dépendre un espace du DOM d'un autre. Volontairement
// limité : titres, gras, italique, code inline, blocs de code, listes,
// liens, paragraphes. Pas de tableaux ni de citations imbriquées — ce n'est
// pas un moteur Markdown, c'est une prévisualisation.

export function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export function renderMarkdown(source) {
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

// Coupe *text* à *limit* caractères sur une frontière de ligne complète
// (jamais en plein milieu), et referme un bloc de code resté ouvert par la
// coupe — sinon tout le reste du rendu se retrouverait dans un unique <pre>
// géant. Rend `{ text, truncated }` : `truncated` dit à l'appelant s'il doit
// afficher une mention « aperçu tronqué ».
export function truncateMarkdown(source, limit) {
  if (source.length <= limit) return { text: source, truncated: false };
  let cut = source.slice(0, limit);
  const lastNewline = cut.lastIndexOf('\n');
  if (lastNewline > 0) cut = cut.slice(0, lastNewline);
  const fences = (cut.match(/^```/gm) || []).length;
  if (fences % 2 === 1) cut += '\n```';
  return { text: cut, truncated: true };
}
