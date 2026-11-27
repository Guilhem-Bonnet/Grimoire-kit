// Atelier de spec (en test) : le menu « Document » de la barre de l'Atelier.
//
// Le nombre de mots et le temps de lecture estimé ; « Copier en Markdown » (le document entier, les phases telles que
// stockées) ; « Télécharger .md » ; « Imprimer / PDF » (la feuille d'impression de `atelier.css` ne garde que le document).
// Lecture seule : tout vient du Markdown déjà renvoyé par `GET atelier/chantiers/<slug>` (`phase.markdown`), aucune route de plus.

import { icon } from '../shell.js';
import { countWords, downloadName, joinMarkdown, statsLabel } from './atelier-document-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** Copie dans le presse-papiers ; faux si le navigateur refuse. */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Le fichier Markdown à enregistrer : un lien temporaire sur un blob, retiré aussitôt. */
function saveFile(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
  const link = el('a', null, null, { href: url, download: name, hidden: '' });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * `doc()` : le document chargé. Rend `{ element, printTitle }` : le bouton et son panneau (à ranger dans la barre), et un titre
 * visible seulement à l'impression (à ranger au-dessus de la feuille).
 */
export function createDocumentMenu(ctx, { slug, doc }) {
  const element = el('div', 'at-docmenu', null, { 'data-role': 'atelier-document' });
  const button = el('button', 'at-docmenu-btn', null, {
    type: 'button', 'aria-expanded': 'false', 'aria-controls': 'at-docmenu-panel', 'data-role': 'document-menu',
  });
  button.append(icon('i-ui-files'), 'Document');
  const panel = el('div', 'at-docmenu-panel', null, { id: 'at-docmenu-panel', role: 'group', 'aria-label': 'Document' });
  panel.hidden = true;
  const stats = el('p', 'at-docmenu-stats', '', { 'data-role': 'doc-stats' });
  const say = el('p', 'at-docmenu-say', '', { 'aria-live': 'polite', 'data-role': 'doc-say' });
  const action = (label, role, glyph) => {
    const item = el('button', 'at-docmenu-item', null, { type: 'button', 'data-role': role });
    item.append(icon(glyph), label);
    return item;
  };
  const copy = action('Copier en Markdown', 'doc-copy', 'i-act-copy');
  const save = action('Télécharger .md', 'doc-download', 'i-act-save');
  const print = action('Imprimer / PDF', 'doc-print', 'i-ui-files');
  panel.append(stats, copy, save, print, say);
  element.append(button, panel);

  const printTitle = el('h1', 'at-print-title', '', { 'aria-hidden': 'true' });
  const markdown = () => joinMarkdown((doc() && doc().phases) || []);

  let isOpen = false;
  const setOpen = (value, { focusBack = false } = {}) => {
    isOpen = value;
    panel.hidden = !value;
    button.setAttribute('aria-expanded', String(value));
    if (value) {
      const text = markdown();
      stats.textContent = statsLabel(countWords(text));
      say.textContent = '';
      copy.focus();
    } else if (focusBack) {
      button.focus();
    }
  };
  button.addEventListener('click', () => setOpen(!isOpen));
  element.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isOpen) { event.stopPropagation(); setOpen(false, { focusBack: true }); }
  });
  const away = (event) => { if (isOpen && !element.contains(event.target)) setOpen(false); };
  document.addEventListener('pointerdown', away);
  ctx.signal.addEventListener('abort', () => document.removeEventListener('pointerdown', away));

  copy.addEventListener('click', async () => {
    const text = markdown();
    say.textContent = text
      ? ((await copyText(text)) ? 'Markdown copié.' : 'Copie impossible ici : utilise « Télécharger .md ».')
      : 'Le document est vide : rien à copier.';
  });
  save.addEventListener('click', () => {
    const text = markdown();
    if (!text) { say.textContent = 'Le document est vide : rien à télécharger.'; return; }
    saveFile(downloadName(slug), text);
    say.textContent = `${downloadName(slug)} téléchargé.`;
  });
  // Le titre n'existe qu'à l'impression (menu ou Ctrl+P) : posé juste avant, à jour du chantier affiché.
  const paintTitle = () => { printTitle.textContent = (doc() && doc().chantier && (doc().chantier.titre || doc().chantier.slug)) || slug; };
  window.addEventListener('beforeprint', paintTitle);
  ctx.signal.addEventListener('abort', () => window.removeEventListener('beforeprint', paintTitle));
  print.addEventListener('click', () => {
    setOpen(false);
    window.print();
  });

  return { element, printTitle };
}
