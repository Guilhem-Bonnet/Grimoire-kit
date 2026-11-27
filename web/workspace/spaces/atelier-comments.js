// Atelier de spec (en test), lot 3 : les commentaires ancrés à un passage.
//
// L'humain sélectionne un passage d'un bloc, clique « Commenter », puis répond, résout, rouvre ou supprime le fil ;
// l'agent commente et répond par l'outil MCP `atelier_comment` (jamais d'ici) et ne peut ni résoudre, ni rouvrir, ni
// supprimer. Chaque fil porte l'extrait cité et une ancre que le serveur recalcule à chaque lecture : `ancre` (le bloc
// n'a pas changé), `retrouve` (le bloc a changé, l'extrait a été retrouvé mot pour mot et en contexte) ou `orphelin`
// (extrait modifié ou disparu : le fil reste lisible, avec l'extrait d'origine, et n'est jamais rattaché ailleurs).
//
// Tout texte est posé par `textContent`, jamais par `innerHTML`. Le surlignage utilise l'API CSS Custom Highlight
// (aucune mutation du DOM du bloc) ; sans elle, le bloc entier porte un filet.
// Données : `POST atelier/chantiers/<slug>/comments[/<id>]` (voir `src/grimoire/tools/workspace_atelier_comments.py`).

import { badge, icon } from '../shell.js';
import { QUOTE_MAX, attachTarget, canAttach, historyLine, nearestStart } from './atelier-logic.js';

const AUTHOR = { humain: 'Humain', agent: 'Agent' };
const STATE_NOTE = {
  retrouve: 'Le bloc a changé depuis le commentaire : le passage cité y figure toujours, tel quel.',
  orphelin: 'Le passage cité a changé ou disparu : le commentaire reste orphelin : rien ne le rattache de soi-même, seul « Rattacher ici » le fait, sur ton geste.',
};

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function when(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '';
  return `${date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' })}, ${date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** Les nœuds de texte d'un bloc rendu, hors boutons (le bouton « Commenter » n'est pas du texte du bloc). */
function textNodes(host) {
  const out = [];
  const walker = document.createTreeWalker(host, window.NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement && node.parentElement.closest('.at-cm-add') ? window.NodeFilter.FILTER_REJECT : window.NodeFilter.FILTER_ACCEPT),
  });
  while (walker.nextNode()) out.push(walker.currentNode);
  return out;
}

/** Une plage DOM couvrant `needle` dans le texte rendu de `host`, ou null (mise en forme entre les lettres, etc.). */
function findRange(host, needle) {
  if (!needle) return null;
  const nodes = textNodes(host);
  const full = nodes.map((n) => n.data).join('');
  const at = full.indexOf(needle);
  if (at < 0) return null;
  const locate = (offset, atEnd) => {
    let seen = 0;
    for (const node of nodes) {
      const next = seen + node.data.length;
      if (offset < next || (atEnd && offset === next)) return [node, offset - seen];
      seen = next;
    }
    return null;
  };
  const from = locate(at, false);
  const to = locate(at + needle.length, true);
  if (!from || !to) return null;
  const range = document.createRange();
  range.setStart(from[0], from[1]);
  range.setEnd(to[0], to[1]);
  return range;
}

/** Ce que l'humain a sélectionné dans `host`, retrouvé dans le texte du bloc (`block.text`) : `{ quote, start, whole }`. */
function readSelection(host, block) {
  const selection = window.getSelection();
  const whole = { quote: '', start: 0, whole: true };
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return whole;
  const range = selection.getRangeAt(0);
  if (!host.contains(range.startContainer) || !host.contains(range.endContainer)) return whole;
  const picked = selection.toString().trim().slice(0, QUOTE_MAX);
  if (!picked) return whole;
  // Position approximative dans le texte rendu : départage les occurrences quand le même extrait revient.
  const before = document.createRange();
  before.selectNodeContents(host);
  before.setEnd(range.startContainer, range.startOffset);
  const best = nearestStart(block.text, picked, before.toString().length);
  // Mise en forme au milieu de la sélection (gras, puce…) : le texte brut du bloc ne la contient pas, on cite le bloc.
  return best < 0 ? whole : { quote: picked, start: best, whole: false };
}

export function createComments(ctx, slug, { doc, sheet, reload }) {
  const ranges = new Map();
  const supported = typeof CSS !== 'undefined' && CSS.highlights && typeof window.Highlight === 'function';
  // « Rattacher ici » : la dernière sélection de l'humain dans un bloc et le dernier bloc touché (le clic sur le bouton ne les efface pas).
  let lastPick = null;
  let lastBlock = null;

  const phases = () => doc().phases;
  // Le nom déclaré (MOA, sinon utilisateur du projet) quand il existe ; sinon « Toi ».
  const humanName = () => (doc().auteur && doc().auteur.nom) || 'Toi';
  const all = () => phases().flatMap((p) => p.comments || []);
  const attached = (c) => c.anchor.state !== 'orphelin';

  /** Fils ouverts rattachés à ce bloc (pour l'exigence : « n commentaire(s) ouvert(s) » de sa ligne d'état). */
  const openOn = (blockId) => all().filter((c) => c.etat === 'ouvert' && attached(c) && c.anchor.block_id === blockId).length;
  const openTotal = () => all().filter((c) => c.etat === 'ouvert').length;

  const focusThread = (id) => {
    const node = sheet.querySelector(`[data-com="${CSS.escape(id)}"]`);
    if (node) { node.scrollIntoView({ block: 'nearest' }); node.focus(); }
  };

  const run = async (box, work, done) => {
    const error = box.querySelector('.at-error');
    error.hidden = true;
    for (const button of box.querySelectorAll('button')) button.disabled = true;
    try {
      const result = await work();
      if (ctx.signal.aborted) return;
      await reload(done);
      if (result && result.focus) focusThread(result.focus);
    } catch (cause) {
      if (ctx.signal.aborted) return;
      if (cause.code === 409) {
        const why = cause.payload && cause.payload.reason;
        await reload(why === 'resolu'
          ? 'Ce fil a été résolu entre-temps : rouvre-le pour y répondre.'
          : 'Ce fil avait déjà changé d’état : le document a été rechargé.');
        return;
      }
      error.textContent = cause.message || String(cause);
      error.hidden = false;
      for (const button of box.querySelectorAll('button')) button.disabled = false;
    }
  };

  const author = (who) => {
    const agent = who === 'agent';
    const mark = el('span', `at-cm-who is-${agent ? 'agent' : 'humain'}`);
    mark.append(el('span', 'at-cm-ava', null, { 'aria-hidden': 'true' }), el('b', null, agent ? AUTHOR.agent : humanName()));
    mark.firstChild.append(agent ? icon('agent') : icon('user')); // icônes de la famille Douce (docs/assets/icons)
    return mark;
  };

  const message = (who, date, texte) => {
    const head = el('div', 'at-cm-head');
    head.append(author(who), el('time', 'at-muted', when(date), { datetime: date }));
    const body = el('p', 'at-cm-text', texte);
    const item = el('div', 'at-cm-msg', null, { 'data-author': who });
    item.append(head, body);
    return item;
  };

  const replyForm = (c, box) => {
    const area = el('textarea', 'at-cm-area', null, { 'aria-label': 'Ta réponse', rows: '3', maxlength: '2000', spellcheck: 'true' });
    const send = el('button', 'at-btn pri', null, { type: 'button', 'data-act': 'reply-send' });
    send.append(icon('i-act-send'), 'Répondre');
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button' });
    const bar = el('div', 'at-edit-bar');
    bar.append(send, cancel, el('span', 'at-muted', 'Ctrl+Entrée envoie, Échap annule.'));
    const form = el('div', 'at-cm-reply', null, { 'data-role': 'reply-form' });
    form.append(area, bar);
    const submit = () => {
      if (!area.value.trim()) { area.focus(); return; }
      run(box, () => ctx.api.atelierFil(slug, c.id, 'reply', area.value).then(() => ({ focus: c.id })), 'Réponse ajoutée.');
    };
    send.addEventListener('click', submit);
    cancel.addEventListener('click', () => { form.remove(); box.querySelector('[data-act="reply"]')?.focus(); });
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel.click(); }
      else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
    });
    return { form, area };
  };

  const suggestionLink = (c) => {
    // La carte de la suggestion est dessinée après le fil : on la cherche au clic, et la liste du document dit si elle attend encore.
    const waiting = phases().some((p) => (p.suggestions || []).some((s) => s.id === c.suggestion));
    if (!waiting) return el('p', 'at-muted at-cm-link', 'Accompagne une suggestion de l’agent, déjà tranchée ou périmée.');
    const go = el('button', 'at-btn ghost at-cm-go', null, { type: 'button', 'data-act': 'to-suggestion' });
    go.append(icon('i-act-next'), 'Voir la suggestion liée');
    go.addEventListener('click', () => {
      const target = sheet.querySelector(`[data-sugg="${CSS.escape(c.suggestion)}"]`);
      if (!target) return;
      target.scrollIntoView({ block: 'center' });
      target.setAttribute('tabindex', '-1');
      target.focus();
    });
    return go;
  };

  const threadNode = (c) => {
    const resolved = c.etat === 'resolu';
    const orphan = !attached(c);
    const quote = el('blockquote', 'at-cm-quote', c.quote, { 'data-role': 'quote' });
    const box = el(resolved ? 'details' : 'div', `at-cm${orphan ? ' is-orphan' : ''}${resolved ? ' is-resolved' : ''}`, null, {
      'data-com': c.id, 'data-author': c.auteur, 'data-anchor': c.anchor.state, 'data-etat': c.etat, tabindex: '-1',
      role: 'group', 'aria-label': `Commentaire de ${c.auteur === 'agent' ? AUTHOR.agent.toLowerCase() : humanName()},${resolved ? 'résolu' : 'ouvert'}`,
    });
    const flags = el('div', 'at-cm-flags');
    if (resolved) flags.append(badge('Résolu', 'ok'));
    if (orphan) flags.append(badge('Orphelin', 'warn', STATE_NOTE.orphelin));
    if (c.anchor.state === 'retrouve') flags.append(badge('Passage modifié depuis le commentaire', 'warn', STATE_NOTE.retrouve));
    const first = message(c.auteur, c.date, c.texte);
    const inner = [];
    if (orphan) inner.push(el('p', 'at-muted', 'Passage d’origine, modifié ou supprimé :'));
    inner.push(quote, first);
    for (const r of c.reponses || []) inner.push(message(r.auteur, r.date, r.texte));
    if (c.suggestion) inner.push(suggestionLink(c));
    if ((c.historique || []).length) {
      const trace = el('ul', 'at-cm-trace', null, { 'data-role': 'attach-history', 'aria-label': 'Rattachements de ce fil' });
      for (const entry of c.historique) trace.append(el('li', 'at-muted', historyLine(entry, when)));
      inner.push(trace);
    }
    const actions = el('div', 'at-edit-bar');
    if (canAttach(c)) {
      const attach = el('button', 'at-btn', null, { type: 'button', 'data-act': 'attach', 'aria-label': 'Rattacher ce commentaire à la sélection du document ou au bloc courant' });
      attach.append(icon('i-act-next'), 'Rattacher ici');
      attach.addEventListener('click', () => {
        const exists = (id) => phases().some((p) => p.blocks.some((b) => b.id === id));
        const target = attachTarget(lastPick && exists(lastPick.blockId) ? lastPick : null, lastBlock && exists(lastBlock.id) ? lastBlock : null);
        const error = box.querySelector('.at-error');
        if (!target) {
          error.textContent = 'Sélectionne un passage du document (ou clique dans un bloc), puis rattache le commentaire.';
          error.hidden = false;
          return;
        }
        run(box, () => ctx.api.atelierRattacher(slug, c.id, target.blockId, target.quote, target.start).then(() => {
          lastPick = null;
          return { focus: c.id };
        }), target.whole ? 'Commentaire rattaché au bloc.' : 'Commentaire rattaché à la sélection.');
      });
      actions.append(attach);
      inner.push(el('p', 'at-muted', 'Sélectionne un passage du document (ou clique dans un bloc), puis « Rattacher ici ».', { 'data-role': 'attach-hint' }));
    }
    if (resolved) {
      const reopen = el('button', 'at-btn', null, { type: 'button', 'data-act': 'reopen' });
      reopen.append(icon('i-act-retry'), 'Rouvrir');
      reopen.addEventListener('click', () => run(box, () => ctx.api.atelierFil(slug, c.id, 'reopen').then(() => ({ focus: c.id })), 'Fil rouvert.'));
      actions.append(reopen);
    } else {
      const reply = el('button', 'at-btn', null, { type: 'button', 'data-act': 'reply' });
      reply.append(icon('i-act-send'), 'Répondre');
      reply.addEventListener('click', () => {
        const open = box.querySelector('[data-role="reply-form"]');
        if (open) { open.querySelector('textarea').focus(); return; }
        const { form, area } = replyForm(c, box);
        actions.before(form);
        area.focus();
      });
      const resolve = el('button', 'at-btn', null, { type: 'button', 'data-act': 'resolve' });
      resolve.append(icon('i-act-approve'), 'Résoudre');
      resolve.addEventListener('click', () => run(box, () => ctx.api.atelierFil(slug, c.id, 'resolve').then(() => ({})), 'Fil résolu.'));
      actions.append(reply, resolve);
    }
    const drop = el('button', 'at-btn ghost', null, { type: 'button', 'data-act': 'delete', 'aria-label': 'Supprimer ce fil' });
    drop.append(icon('i-act-remove'), 'Supprimer');
    drop.addEventListener('click', () => {
      if (box.querySelector('.at-cm-confirm')) return;
      const yes = el('button', 'at-btn pri', 'Supprimer le fil', { type: 'button', 'data-act': 'delete-confirm' });
      const no = el('button', 'at-btn ghost', 'Garder', { type: 'button' });
      const ask = el('div', 'at-form at-cm-confirm');
      ask.append(el('span', null, 'Le fil et ses réponses seront supprimés.'), yes, no);
      no.addEventListener('click', () => { ask.remove(); drop.focus(); });
      yes.addEventListener('click', () => run(box, () => ctx.api.atelierFil(slug, c.id, 'delete').then(() => ({})), 'Fil supprimé.'));
      actions.after(ask);
      no.focus();
    });
    actions.append(drop);
    const error = el('p', 'at-error', '', { role: 'alert' });
    error.hidden = true;
    const body = el('div', 'at-cm-body');
    body.append(...inner, actions, error);
    if (resolved) {
      const summary = el('summary', 'at-cm-sum');
      summary.append(author(c.auteur), el('span', 'at-muted at-cm-cut', c.texte));
      summary.append(flags);
      box.append(summary, body);
    } else {
      body.prepend(flags);
      box.append(body);
    }
    // Survol ou focus : le passage cité s'accentue dans le texte.
    const lit = () => { if (supported && ranges.has(c.id)) CSS.highlights.set('at-cm-on', new window.Highlight(ranges.get(c.id))); };
    const dim = () => { if (supported) CSS.highlights.delete('at-cm-on'); };
    box.addEventListener('pointerenter', lit);
    box.addEventListener('pointerleave', dim);
    box.addEventListener('focusin', lit);
    box.addEventListener('focusout', dim);
    return box;
  };

  const composer = (phase, block, host, row) => {
    const margin = row.querySelector('.at-cm-margin');
    const open = margin.querySelector('.at-cm-new');
    if (open) { open.querySelector('textarea').focus(); return; }
    const picked = readSelection(host, block);
    const quote = picked.whole ? block.text.slice(0, QUOTE_MAX) : picked.quote;
    const form = el('form', 'at-cm at-cm-new', null, { 'data-role': 'comment-form', 'aria-label': 'Nouveau commentaire' });
    const cite = el('blockquote', 'at-cm-quote', quote, { 'data-role': 'quote' });
    const note = el('p', 'at-muted', picked.whole
      ? 'Aucun passage sélectionné : le commentaire porte sur le bloc.'
      : 'Le commentaire porte sur le passage sélectionné.');
    const area = el('textarea', 'at-cm-area', null, { 'aria-label': 'Ton commentaire', rows: '3', maxlength: '2000', spellcheck: 'true' });
    const send = el('button', 'at-btn pri', null, { type: 'submit', 'data-act': 'comment-send' });
    send.append(icon('i-act-send'), 'Commenter');
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button' });
    const error = el('p', 'at-error', '', { role: 'alert' });
    error.hidden = true;
    const bar = el('div', 'at-edit-bar');
    bar.append(send, cancel, el('span', 'at-muted', 'Ctrl+Entrée envoie, Échap annule.'));
    form.append(cite, note, area, bar, error);
    margin.prepend(form);
    sheet.classList.add('has-cm');
    area.focus();
    const close = () => { form.remove(); sheet.classList.toggle('has-cm', all().length > 0); host.querySelector('.at-cm-add')?.focus(); };
    cancel.addEventListener('click', close);
    const submit = () => {
      if (!area.value.trim()) { area.focus(); return; }
      run(form, async () => {
        const made = await ctx.api.atelierCommenter(slug, block.id, picked.whole ? '' : picked.quote, picked.whole ? null : picked.start, area.value);
        return { focus: made.comment.id };
      }, 'Commentaire ajouté.');
    };
    form.addEventListener('submit', (event) => { event.preventDefault(); submit(); });
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
    });
  };

  /** Le bouton « Commenter » d'un bloc : au clavier comme à la souris, il porte la sélection courante ou le bloc entier. */
  const addButton = (phase, block, host, title) => {
    const button = el('button', 'at-cm-add', null, { type: 'button', 'data-act': 'comment', 'aria-label': `Commenter le bloc : ${title}` });
    button.append(icon('i-act-add'), el('span', null, 'Commenter'));
    button.addEventListener('click', (event) => {
      event.stopPropagation(); // un clic ici n'ouvre pas l'édition du bloc
      composer(phase, block, host, host.closest('.at-cmrow'));
    });
    button.addEventListener('keydown', (event) => event.stopPropagation());
    return button;
  };

  /** Le fil de ces blocs : dans la marge (large) ou sous le bloc (étroit), géré par la mise en page. */
  const margin = (block) => {
    const aside = el('aside', 'at-cm-margin', null, { 'aria-label': 'Commentaires du bloc' });
    const mine = all().filter((c) => (attached(c) ? c.anchor.block_id === block.id : c.anchor.near === block.id));
    aside.append(...mine.map(threadNode));
    return aside;
  };

  /** Les fils orphelins sans bloc voisin : en fin de phase, avec l'extrait d'origine. */
  const orphans = (phase, blockIds) => {
    const lost = (phase.comments || []).filter((c) => !attached(c) && !blockIds.has(c.anchor.near));
    if (!lost.length) return null;
    const box = el('section', 'at-cm-orphans', null, { 'data-role': 'orphans', 'aria-label': 'Commentaires dont le passage a changé' });
    box.append(el('h3', null, 'Passage modifié ou supprimé'), el('p', 'at-muted', STATE_NOTE.orphelin));
    box.append(...lost.map(threadNode));
    return box;
  };

  /** Surligne le passage cité de chaque fil ouvert rattaché ; à rappeler après chaque dessin du document. */
  const paint = () => {
    ranges.clear();
    const marks = [];
    for (const c of all()) {
      if (c.etat !== 'ouvert' || !attached(c)) continue;
      const host = sheet.querySelector(`[data-block="${CSS.escape(c.anchor.block_id)}"]`);
      if (!host) continue;
      const range = findRange(host, c.quote) || findRange(host, c.quote.replace(/[*_`~]/g, ''));
      if (range && supported) { ranges.set(c.id, range); marks.push(range); } else host.classList.add('cm-block');
      host.classList.add('has-cm');
    }
    if (supported) {
      if (marks.length) CSS.highlights.set('at-cm', new window.Highlight(...marks)); else CSS.highlights.delete('at-cm');
      CSS.highlights.delete('at-cm-on');
    }
    sheet.classList.toggle('has-cm', all().length > 0);
  };

  // Le bouton se montre au survol, au focus et dès qu'une sélection est dans le bloc (`.has-sel`).
  const onSelect = () => {
    const selection = window.getSelection();
    for (const host of sheet.querySelectorAll('.at-blk.has-sel')) host.classList.remove('has-sel');
    if (!selection || selection.isCollapsed || !selection.anchorNode) return;
    const holder = selection.anchorNode.parentElement && selection.anchorNode.parentElement.closest('.at-blk');
    if (holder && sheet.contains(holder)) {
      holder.classList.add('has-sel');
      const block = phases().flatMap((p) => p.blocks).find((b) => b.id === holder.getAttribute('data-block'));
      const picked = block ? readSelection(holder, block) : null;
      if (picked && !picked.whole) lastPick = { blockId: block.id, quote: picked.quote, start: picked.start };
    }
  };
  document.addEventListener('selectionchange', onSelect);
  const onTouch = (event) => {
    const holder = event.target.closest && event.target.closest('.at-blk');
    if (holder && sheet.contains(holder)) lastBlock = { id: holder.getAttribute('data-block') };
  };
  sheet.addEventListener('pointerdown', onTouch);
  sheet.addEventListener('focusin', onTouch);

  // Un agent a pu commenter pendant que l'onglet était ailleurs : au retour on relit, sauf si l'humain écrit.
  const writing = () => sheet.querySelector('.is-editing, .at-cm-new, .at-cm-reply, .at-cm-confirm');
  const onVisible = () => { if (!document.hidden && !writing()) reload(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', onVisible);
  ctx.signal.addEventListener('abort', () => {
    document.removeEventListener('selectionchange', onSelect);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
    if (supported) { CSS.highlights.delete('at-cm'); CSS.highlights.delete('at-cm-on'); }
  });

  const summary = () => {
    const n = openTotal();
    return n ? ` ${plural(n, 'commentaire ouvert', 'commentaires ouverts')}.` : '';
  };

  return { addButton, margin, orphans, paint, openOn, summary };
}
