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
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_all, label, url) => linkHtml(label, url));
}

// Liste blanche des schémas d'URL qu'un lien rendu peut porter. Tout autre
// schéma (actif ou inconnu) fait perdre sa cible au lien : il reste le texte.
const ALLOWED_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

// Remet en clair ce que `escapeHtml` a échappé, pour juger l'URL telle que le
// navigateur la verra une fois l'attribut décodé.
function unescapeHtml(text) {
  return text.replace(/&(amp|lt|gt|quot);/g, (_m, name) => ({ amp: '&', lt: '<', gt: '>', quot: '"' }[name]));
}

// Normalise comme le fait un navigateur avant de lire le schéma : décodage
// pourcent (protégé, répété), blancs et caractères de contrôle retirés,
// minuscules, `\` lu comme `/`.
function normalizeForCheck(url) {
  let value = String(url);
  for (let i = 0; i < 3; i += 1) {
    try {
      const decoded = decodeURIComponent(value);
      if (decoded === value) break;
      value = decoded;
    } catch { break; }
  }
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\u0000-\u0020\u007f-\u009f\u00a0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u2060\u3000\ufeff]/g, '')
    .replace(/\\/g, '/')
    .toLowerCase();
}

/**
 * Valide l'URL d'un lien : renvoie l'URL (nettoyée des blancs de bord) si son
 * schéma est dans la liste blanche (`http:`, `https:`, `mailto:`) ou s'il n'y
 * en a pas (ancre, chemin relatif ou absolu), sinon `''`. Un `//hôte` relatif
 * au protocole est refusé.
 */
export function safeLinkUrl(url) {
  const raw = unescapeHtml(String(url == null ? '' : url)).trim();
  if (!raw) return '';
  const check = normalizeForCheck(raw);
  if (check.startsWith('//')) return '';
  const colon = check.search(/[:/?#]/);
  if (colon >= 0 && check[colon] === ':') {
    return ALLOWED_SCHEMES.has(check.slice(0, colon + 1)) ? raw : '';
  }
  return raw;
}

/** Comme `safeLinkUrl`, mais seulement une URL absolue http(s) : pour les liens « ouvrir ailleurs » (PR, CI). */
export function safeWebUrl(url) {
  const safe = safeLinkUrl(url);
  return /^\s*https?:/i.test(safe) ? safe : '';
}

function linkHtml(label, url) {
  const safe = safeLinkUrl(url);
  if (!safe) return label;
  const href = escapeHtml(safe);
  if (/^https?:/i.test(safe)) return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  return `<a href="${href}" rel="noopener">${label}</a>`;
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
