// Atelier de spec (en test) : le menu « Document » de la barre de l'Atelier.
//
// Le nombre de mots et le temps de lecture estimé ; « Copier en Markdown » et « Télécharger .md » (un document propre : un titre de
// chantier, un `##` par phase, sans front matter ni gabarit vide) ; « Fichiers bruts (.md) » (les cinq fichiers tels que stockés) ;
// « Imprimer / PDF » (la feuille d'impression de `atelier.css` ne garde que le document, un en-tête et les commentaires ouverts).
// Lecture seule : tout vient du Markdown déjà renvoyé par `GET atelier/chantiers/<slug>` (`phase.markdown`), aucune route de plus.
// Le menu se ferme après chaque action ; le résultat est annoncé par une zone `role="status"` hors du panneau, et par `hooks.say`.

import { icon } from '../shell.js';
import {
  cleanMarkdown, countWords, downloadName, joinMarkdown, printDateLabel, printableComments, statsLabel,
} from './atelier-document-logic.js';

const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };

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
 * `doc()` : le document chargé ; `say(message, kind)` (facultatif) : la notification de la page. Rend `{ element, printTitle,
 * printNotes }` : le bouton et son panneau (à ranger dans la barre), l'en-tête d'impression (à ranger au-dessus de la feuille) et
 * les commentaires ouverts d'impression (à ranger sous la feuille). Ces deux derniers n'existent qu'à l'impression.
 */
export function createDocumentMenu(ctx, { slug, doc, say: notify = null }) {
  const element = el('div', 'at-docmenu', null, { 'data-role': 'atelier-document' });
  const button = el('button', 'at-docmenu-btn', null, {
    type: 'button', 'aria-expanded': 'false', 'aria-controls': 'at-docmenu-panel', 'data-role': 'document-menu',
  });
  button.append(icon('i-ui-files'), 'Document');
  const panel = el('div', 'at-docmenu-panel', null, { id: 'at-docmenu-panel', role: 'group', 'aria-label': 'Document' });
  panel.hidden = true;
  const stats = el('p', 'at-docmenu-stats', '', { 'data-role': 'doc-stats' });
  // Hors du panneau : le menu se ferme après l'action, le résultat reste annoncé.
  const say = el('p', 'at-docmenu-say sr-only', '', { role: 'status', 'data-role': 'doc-say' });
  const action = (label, role, glyph) => {
    const item = el('button', 'at-docmenu-item', null, { type: 'button', 'data-role': role });
    item.append(icon(glyph), label);
    return item;
  };
  const copy = action('Copier en Markdown', 'doc-copy', 'i-act-copy');
  const save = action('Télécharger .md', 'doc-download', 'i-act-save');
  const raw = action('Fichiers bruts (.md)', 'doc-download-raw', 'i-act-save');
  const print = action('Imprimer / PDF', 'doc-print', 'i-ui-files');
  panel.append(stats, copy, save, raw, print);
  element.append(button, panel, say);

  const printTitle = el('div', 'at-print-head', null, { 'aria-hidden': 'true' });
  const printName = el('h1', 'at-print-title');
  const printDate = el('p', 'at-print-date');
  printTitle.append(printName, printDate);
  const printNotes = el('section', 'at-print-notes', null, { 'aria-hidden': 'true', 'data-role': 'print-notes' });

  const phases = () => (doc() && doc().phases) || [];
  const heading = () => (doc() && doc().chantier && (doc().chantier.titre || doc().chantier.slug)) || slug;
  const clean = () => cleanMarkdown(phases(), heading());

  let isOpen = false;
  const setOpen = (value, { focusBack = false } = {}) => {
    isOpen = value;
    panel.hidden = !value;
    button.setAttribute('aria-expanded', String(value));
    if (value) {
      stats.textContent = statsLabel(countWords(joinMarkdown(phases())));
      say.textContent = '';
      copy.focus();
    } else if (focusBack) {
      button.focus();
    }
  };
  /** L'action est faite : le menu se ferme, le focus revient au bouton, le résultat est dit. */
  const finish = (message, kind = 'ok') => {
    setOpen(false, { focusBack: true });
    say.textContent = message;
    if (notify) notify(message, kind);
  };
  button.addEventListener('click', () => setOpen(!isOpen));
  element.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isOpen) { event.stopPropagation(); setOpen(false, { focusBack: true }); }
  });
  const away = (event) => { if (isOpen && !element.contains(event.target)) setOpen(false); };
  document.addEventListener('pointerdown', away);
  ctx.signal.addEventListener('abort', () => document.removeEventListener('pointerdown', away));

  const EMPTY = 'Le document n’a encore aucun contenu rempli : rien à exporter. « Fichiers bruts (.md) » donne les gabarits.';
  copy.addEventListener('click', async () => {
    const text = clean();
    if (!text) { finish(EMPTY, 'warn'); return; }
    if (await copyText(text)) finish('Markdown copié.');
    else finish('Copie impossible ici : utilise « Télécharger .md ».', 'warn');
  });
  save.addEventListener('click', () => {
    const text = clean();
    if (!text) { finish(EMPTY, 'warn'); return; }
    saveFile(downloadName(slug), text);
    finish(`${downloadName(slug)} téléchargé.`);
  });
  raw.addEventListener('click', () => {
    const text = joinMarkdown(phases());
    if (!text) { finish('Le document est vide : rien à télécharger.', 'warn'); return; }
    const name = downloadName(slug, '-fichiers-bruts');
    saveFile(name, text);
    finish(`${name} téléchargé.`);
  });

  // L'en-tête et les commentaires n'existent qu'à l'impression (menu ou Ctrl+P) : posés juste avant, à jour du chantier affiché.
  const paintPrint = () => {
    printName.textContent = heading();
    printDate.textContent = `Imprimé le ${printDateLabel()}`;
    const notes = printableComments(doc(), PHASE_NAMES);
    printNotes.replaceChildren();
    if (!notes.length) return;
    printNotes.append(el('h2', 'at-print-notes-h', 'Commentaires ouverts'));
    const list = el('ol', 'at-print-notes-list');
    for (const note of notes) {
      const item = el('li', 'at-print-note');
      item.append(el('p', 'at-print-note-head', `${note.phase}${note.quote ? ` — « ${note.quote.length > 140 ? `${note.quote.slice(0, 139)}…` : note.quote} »` : ''}`));
      item.append(el('p', 'at-print-note-text', `${note.author} : ${note.text}`));
      for (const reply of note.replies) item.append(el('p', 'at-print-note-text is-reply', `${reply.author} : ${reply.text}`));
      list.append(item);
    }
    printNotes.append(list);
  };
  window.addEventListener('beforeprint', paintPrint);
  ctx.signal.addEventListener('abort', () => window.removeEventListener('beforeprint', paintPrint));
  print.addEventListener('click', () => {
    setOpen(false);
    window.print();
  });

  return { element, printTitle, printNotes };
}
