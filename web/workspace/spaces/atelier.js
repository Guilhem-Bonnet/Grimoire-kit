// Atelier de spec (en test) : la bibliothèque des chantiers et le document d'un chantier, en blocs.
//
// Lot 1 de l'épique #1197. Artboards « Rangement » (bibliothèque) et « A. La feuille » (document) de la
// maquette Atelier. Absent tant que le drapeau `cockpit.test_features.atelier` est éteint : la coque ne
// l'importe qu'au montage de l'espace, et l'espace n'existe pas sans le drapeau.
//
// Lot 2 : un bloc qui porte une suggestion de l'agent l'affiche dessous (avant / après, motif, posture) avec
// « Accepter » et « Refuser » ; l'agent ne modifie jamais un bloc, l'humain tranche ici. Compteur en bibliothèque.
// Lot 4 : sous chaque exigence, sa ligne d'état réel et « Valider et découper » (voir `atelier-live.js`).
// Lot 3 : des commentaires ancrés à un passage, entre l'humain et l'agent (voir `atelier-comments.js`).
// Hors de ces lots : l'assistant flottant, les diagrammes, la recette depuis le document, la voix.
// Données : `GET atelier/chantiers`, `POST atelier/chantiers`, `GET atelier/chantiers/<slug>`,
// `PUT atelier/chantiers/<slug>/blocs/<id>`, `POST atelier/chantiers/<slug>/suggestions/<id>` (voir `src/grimoire/tools/workspace_atelier.py`).

import { renderBlockMarkdown } from './atelier-rendu.js';
import { isTemplateText, plainText, templateHint, toggleTaskLine } from './atelier-rendu-logic.js';
import { atelierIsWide, badge, icon, setAtelierWide } from '../shell.js';
import { createLiveDoc } from './atelier-live.js';
import { createComments } from './atelier-comments.js';
import { createAssistant } from './atelier-assistant.js';
import { stable } from './atelier-focus.js';
import { createHistory } from './atelier-historique.js';
import { createLoopPanel, phaseState, progressCell, waitingBadges } from './atelier-avancement.js';
import { setAtelierContext, setAtelierOutline } from '../atelier-context.js';
import { createNotifier } from './atelier-notify.js';
import { createRefs, flash } from './atelier-liens.js';
import { atelierHash, exigenceBlocks, reqFromParams } from './atelier-liens-logic.js';
import { createReading } from './atelier-lecture.js';
import { createSync } from './atelier-synchro.js';
import { annexMessage } from './atelier-synchro-logic.js';
import { createDevalidation } from './atelier-devalidation.js';
import { buildOutline } from './atelier-document-logic.js';
import { createPlan } from './atelier-plan.js';
import { createFind } from './atelier-recherche.js';
import { createDocumentMenu } from './atelier-document-menu.js';
import { createDrafts } from './atelier-brouillons.js';
import { reconcileChildren, release, tag } from './atelier-reconcile.js';
import { SELF, plainKey, sigOf } from './atelier-reconcile-logic.js';
import {
  INDICATOR_LABEL, PHONE_MAX, WIDE_MIN, activePhase, capitalize, codeSegments, newOrphans, orphanNotice,
  phaseFromParams, phaseIndicator, stepsOf,
} from './atelier-layout-logic.js';

// Un rechargement du document ne redessine pas la feuille d'un bloc : il la réconcilie (voir `atelier-reconcile.js`). Un nœud
// dont les données n'ont pas changé garde sa place ; un bloc en cours d'édition, un commentaire ou une réponse en cours ne
// sont jamais remplacés. Les rechargements de fond n'ont donc plus à attendre la fin d'une saisie.

/** Ordre des groupes de la bibliothèque ; `archive` vit dans une ligne repliable. */
const GROUPS = [
  ['en-discussion', 'En discussion'], ['pret-a-valider', 'Prêt à valider'], ['valide', 'Validé'],
  ['en-cours', 'En cours'], ['idee', 'Idée'], ['livre', 'Livré'],
];
const STATE_LABEL = { ...Object.fromEntries(GROUPS), archive: 'Archivé' };
const STATE_TONE = {
  'en-discussion': 'acc', 'pret-a-valider': 'warn', valide: 'ok', 'en-cours': 'ok', livre: 'ok', idee: '', archive: '',
};
const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };


function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function formatDate(iso, { withTime = false } = {}) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '';
  const day = date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' });
  return withTime ? `${day}, ${date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}` : day;
}

/** Bandeau de test, en tête de chaque vue de l'espace. */
function testBanner(ctx) {
  const link = el('a', null, 'Désactiver', { href: '#fonctions-test' });
  link.addEventListener('click', (event) => { event.preventDefault(); ctx.goto('fonctions-test'); });
  const banner = el('div', 'at-test', null, { role: 'note', 'data-role': 'atelier-test' });
  banner.append(icon('i-act-preview'), el('span'), link, widthToggle(ctx));
  banner.children[1].append(
    el('strong', null, 'Atelier en test.'),
    " Aucune tâche n'est lancée tant que tu n'as pas validé le découpage.",
  );
  banner.children[1].className = 'at-test-text';
  return banner;
}

/** Pleine largeur : replie l'inspecteur et le dock (le « Plein écran » de la coque) ou les rouvre ; le choix est mémorisé. */
function widthToggle(ctx) {
  const button = el('button', 'at-btn at-width', 'Pleine largeur', { type: 'button', 'data-role': 'atelier-largeur' });
  const paint = () => button.setAttribute('aria-pressed', String(atelierIsWide()));
  button.title = 'Replier ou rouvrir l’inspecteur et le dock pour donner toute la largeur au document';
  button.addEventListener('click', () => { setAtelierWide(!atelierIsWide()); paint(); });
  paint();
  if (typeof MutationObserver === 'function') {
    const watch = new MutationObserver(paint);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-focus'] });
    ctx.signal.addEventListener('abort', () => watch.disconnect());
  }
  return button;
}

function page(ctx, ...content) {
  const body = el('div', 'at-body');
  body.append(...content);
  const wrap = el('div', 'at-page');
  wrap.append(testBanner(ctx), body);
  return wrap;
}

/** Un texte avec ses `accents graves` rendus comme du code (nœuds de texte et `<code>`, jamais de HTML). */
function withCode(node, text) {
  for (const part of codeSegments(text)) node.append(part.code ? el('code', 'at-code', part.text) : part.text);
  return node;
}

const open = (ctx, slug, phase) => ctx.goto('atelier', phase ? { chantier: slug, phase } : { chantier: slug });

// ── Bibliothèque (artboard « Rangement ») ───────────────────────────────────

function chantierRow(ctx, row) {
  if (row.error) {
    const node = el('div', 'at-row is-error', null, { role: 'listitem', 'data-slug': row.slug, 'data-error': 'true' });
    const cell = el('span', 'at-cell');
    cell.append(el('b', null, row.titre || row.slug), el('span', 'at-muted', `${row.error} — ${row.chemin || ''}`));
    node.append(cell);
    return node;
  }
  const link = el('a', 'at-row', null, { href: `#atelier?chantier=${encodeURIComponent(row.slug)}`, role: 'listitem', 'data-slug': row.slug });
  link.addEventListener('click', (event) => { event.preventDefault(); open(ctx, row.slug); });
  const name = el('span', 'at-name');
  name.append(badge(STATE_LABEL[row.etat] || row.etat, STATE_TONE[row.etat] || ''), el('b', null, row.titre || row.slug));
  if (row.suggestions > 0) {
    const n = row.suggestions;
    const waiting = badge(`${n} suggestion${n > 1 ? 's' : ''}`, 'acc', `${n} suggestion${n > 1 ? 's' : ''} en attente de ta décision`);
    waiting.setAttribute('data-role', 'sugg-count');
    name.append(waiting);
  }
  if (row.commentaires > 0) { // lot 3 : fils ouverts sur le document de ce chantier
    const n = row.commentaires;
    const talk = badge(`${n} commentaire${n > 1 ? 's' : ''}`, 'acc', `${n} fil${n > 1 ? 's' : ''} de commentaires ouvert${n > 1 ? 's' : ''}`);
    talk.setAttribute('data-role', 'comment-count');
    name.append(talk);
  }
  name.append(...waitingBadges(row)); // ce qui attend la personne : question, exigences à valider, question du découpage
  const { segs, text } = progressCell(row); // le calcul unique du serveur (cadrage_progress), jamais recompté ici
  const progress = el('span', 'at-cell');
  progress.append(segs, el('span', 'at-muted', text, { 'data-role': 'row-progress' }));
  link.append(
    name, progress,
    el('span', 'at-muted', row.mission ? `Mission ${row.mission}` : 'Aucune mission'),
    el('span', 'at-muted', formatDate(row.modifie || row.cree) || '—'),
  );
  return link;
}

function library(ctx, root, data, ideas) {
  const rows = data.chantiers || [];
  const search = el('input', 'at-search', null, { type: 'search', placeholder: 'Chercher', 'aria-label': 'Chercher un chantier' });
  const create = el('button', 'at-btn pri', null, { type: 'button', 'data-role': 'new-chantier' });
  create.append(icon('i-act-add'), 'Nouveau chantier');
  const formSlot = el('div');
  const ideaSlot = el('div');
  const list = el('section', null, null, { 'aria-label': 'Chantiers par état' });
  const sub = rows.length
    ? `${rows.length} chantier${rows.length > 1 ? 's' : ''}, groupés par état.`
    : 'Un chantier cadre une demande avant tout code : tu remplis le document phase par phase, avec l’assistant, jusqu’aux exigences. '
      + 'Quand les exigences Must sont validées, « Valider et découper » crée les tâches, sans rien lancer. Crée un premier chantier, ou ouvre-en un depuis une idée.';
  const actions = el('div', 'at-actions');
  actions.append(search, create);
  const head = el('div', 'at-head');
  const title = el('div');
  title.append(el('h2', 'at-title', 'Chantiers'), el('p', 'at-sub', sub));
  head.append(title, actions);

  const showForm = () => {
    if (formSlot.firstChild) { formSlot.querySelector('input').focus(); return; }
    const input = el('input', 'at-input', null, { type: 'text', 'aria-label': 'Titre du chantier', placeholder: 'Titre du chantier', maxlength: '120' });
    const submit = el('button', 'at-btn pri', 'Créer', { type: 'submit' });
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button' });
    const error = el('p', 'at-error', '', { role: 'alert' });
    error.hidden = true;
    const form = el('form', 'at-form', null, { 'data-role': 'new-form' });
    form.append(input, submit, cancel, error);
    const close = () => { formSlot.replaceChildren(); create.focus(); };
    cancel.addEventListener('click', close);
    form.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      error.hidden = true;
      try {
        const made = await ctx.api.atelierNouveau(input.value);
        if (!ctx.signal.aborted) open(ctx, made.chantier.slug);
      } catch (cause) {
        error.textContent = cause.message || String(cause); // l’erreur du kit, telle quelle
        error.hidden = false;
        submit.disabled = false;
        input.focus();
      }
    });
    formSlot.append(form);
    input.focus();
  };
  create.addEventListener('click', showForm);

  const draw = () => {
    const needle = search.value.trim().toLowerCase();
    const shown = rows.filter((r) => !needle || `${r.titre} ${r.slug}`.toLowerCase().includes(needle));
    list.replaceChildren();
    if (!shown.length) {
      if (rows.length) list.append(el('p', 'at-muted', 'Aucun chantier ne correspond.'));
      return;
    }
    list.append(el('div', 'at-cols', null, { 'aria-hidden': 'true' }));
    list.firstChild.append(el('span', null, 'Chantier'), el('span', null, 'Cadrage'), el('span', null, 'Mission'), el('span', null, 'Dernière activité'));
    for (const [state, label] of GROUPS) {
      const inGroup = shown.filter((r) => r.etat === state && !r.error);
      if (!inGroup.length) continue;
      const group = el('div', null, null, { role: 'list', 'aria-label': label, 'data-group': state });
      const h = el('h3', 'at-group', label);
      h.append(el('span', 'at-muted', String(inGroup.length)));
      list.append(h, group);
      group.append(...inGroup.map((r) => chantierRow(ctx, r)));
    }
    const broken = shown.filter((r) => r.error);
    const archived = shown.filter((r) => r.etat === 'archive' && !r.error);
    if (broken.length) {
      const group = el('div', null, null, { role: 'list', 'aria-label': 'Illisibles', 'data-group': 'erreur' });
      list.append(el('h3', 'at-group', 'À réparer'), group);
      group.append(...broken.map((r) => chantierRow(ctx, r)));
    }
    if (archived.length) {
      const fold = el('details', 'at-fold', null, { 'data-group': 'archive' });
      fold.append(el('summary', null, `Voir les ${archived.length} chantier${archived.length > 1 ? 's' : ''} archivé${archived.length > 1 ? 's' : ''}`));
      const group = el('div', null, null, { role: 'list', 'aria-label': 'Archivés' });
      group.append(...archived.map((r) => chantierRow(ctx, r)));
      fold.append(group);
      list.append(fold);
    }
  };
  search.addEventListener('input', draw);
  draw();

  ideaSlot.append(ideasSection(ctx, ideas));
  root.replaceChildren(page(ctx, head, formSlot, ideaSlot, list));
}

function ideasSection(ctx, ideas) {
  // Sans idée, l'encart se replie sur une ligne : il ne prend plus la place de la liste des chantiers.
  const section = el(ideas.length ? 'section' : 'details', 'at-card' + (ideas.length ? '' : ' at-card-empty'), null, { 'data-role': 'ideas' });
  const h = el(ideas.length ? 'h3' : 'summary', null, ideas.length ? 'Idées' : 'Idées (aucune gardée)', { id: 'at-ideas-h' });
  const top = el('div', 'at-actions');
  top.append(h, el('span', 'at-muted', 'Une idée devient un chantier quand tu veux la creuser.'));
  if (ideas.length) { section.setAttribute('aria-labelledby', 'at-ideas-h'); section.append(top); } else { section.append(h, el('p', 'at-muted', 'Aucune idée gardée. Une idée devient un chantier quand tu veux la creuser.')); }
  const error = el('p', 'at-error', '', { role: 'alert' });
  error.hidden = true;
  for (const idea of ideas) {
    const line = el('div', 'at-idea', null, { 'data-idea': idea.id });
    const button = el('button', 'at-btn', 'Ouvrir un chantier', { type: 'button' });
    button.addEventListener('click', async () => {
      button.disabled = true;
      error.hidden = true;
      try {
        const made = await ctx.api.atelierNouveau(idea.text); // l’idée elle-même n’est pas modifiée
        if (!ctx.signal.aborted) open(ctx, made.chantier.slug);
      } catch (cause) {
        error.textContent = cause.message || String(cause);
        error.hidden = false;
        button.disabled = false;
      }
    });
    line.append(badge('Idée'), el('span', null, idea.text), el('span', 'at-muted', formatDate(idea.created_at) ? `notée le ${formatDate(idea.created_at)}` : ''), button);
    section.append(line);
  }
  section.append(error);
  return section;
}

// ── Document d'un chantier (artboard « A. La feuille ») ─────────────────────

/** Le texte d'un bloc sans sa puce ni son guillemet de citation : ce que l'on lit. */
const bare = (text) => text.replace(/^>\s?/gm, '').replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '');

/** Le Markdown d'un bloc en nœuds (jamais de HTML : voir `atelier-rendu.js`) ; `onToggle` rend ses cases cochables. */
function markdown(text, onToggle = null, refs = null) {
  return renderBlockMarkdown(text, { onToggle, refs });
}

/** Un bloc qui n'est que le gabarit « À compléter » : un emplacement vide, pas un contenu. */
function emptySlot(block) {
  const node = el('div', 'at-placeholder at-empty-slot', null, { 'data-role': 'empty-slot' });
  node.append(el('span', 'at-empty-hint', templateHint(block.text)));
  return node;
}

function renderBlock(block, opts = {}) {
  const node = el('div');
  switch (block.kind) {
    case 'rubrique': {
      node.className = 'at-rub';
      node.textContent = block.text.replace(/^#+\s*/, '');
      return node;
    }
    case 'question': {
      node.className = 'at-q';
      node.append(icon('i-act-preview'), markdown(bare(block.text), null, opts.refs));
      return node;
    }
    case 'ecartee': {
      node.className = 'at-gone';
      const line = el('p');
      line.append(el('s', null, block.piste || bare(block.text)));
      if (block.raison) line.append(el('span', 'at-because', ` : ${block.raison}`));
      node.append(line);
      return node;
    }
    case 'decision': {
      node.className = 'at-req-head';
      node.append(badge('Décision', 'acc'), markdown(bare(block.text), null, opts.refs));
      return node;
    }
    case 'exigence': {
      node.className = 'at-req';
      const head = el('div', 'at-req-head');
      head.append(el('span', 'at-req-id', block.req));
      if (block.priorite) head.append(badge(block.priorite, block.priorite.toLowerCase() === 'must' ? 'warn' : ''));
      head.append(el('span', null, block.titre));
      node.append(head);
      if ((block.criteres || []).length) {
        const ul = el('ul');
        for (const c of block.criteres) ul.append(el('li', null, c));
        node.append(ul);
      }
      return node;
    }
    default: {
      if (block.placeholder || isTemplateText(block.text)) return emptySlot(block);
      node.append(markdown(block.text, opts.onToggle, opts.refs));
      return node;
    }
  }
}

function blockTitle(block) {
  return plainText(bare(block.text)).slice(0, 60); // un libellé lisible : pas de `**`, de `[](…)` ni de puce
}

const POSTURE_LABEL = { explorer: 'Explorer', sceptique: 'Sceptique', simplificateur: 'Simplificateur' };

/** Une suggestion de l'agent sous son bloc : avant / après en texte brut, motif, et le geste humain. */
function suggestionNode(sug, decide) {
  const mark = el('span', 'at-ai', null, { 'aria-hidden': 'true' });
  mark.append(icon('i-act-add'));
  const who = el('span', 'at-sugg-who');
  who.append(mark, `${capitalize(sug.auteur || 'agent')} propose`);
  const head = el('div', 'at-sugg-head');
  head.append(who, badge(POSTURE_LABEL[sug.posture] || sug.posture, ''));
  const before = el('div', 'at-diff del');
  before.append(el('span', 'at-diff-tag', 'Avant'), el('del', null, sug.avant));
  const after = el('div', 'at-diff ins');
  after.append(el('span', 'at-diff-tag', 'Proposé'), el('ins', null, sug.texte));
  const reason = el('p', 'at-sugg-why', sug.motif ? `Pourquoi : ${sug.motif}` : '');
  reason.hidden = !sug.motif;
  const accept = el('button', 'at-btn pri', null, { type: 'button', 'data-act': 'accept' });
  accept.append(icon('i-act-approve'), 'Accepter');
  const refuse = el('button', 'at-btn', null, { type: 'button', 'data-act': 'reject' });
  refuse.append(icon('i-act-reject'), 'Refuser');
  const actions = el('div', 'at-edit-bar');
  actions.append(accept, refuse);
  const error = el('p', 'at-error', '', { role: 'alert' });
  error.hidden = true;
  const node = el('div', 'at-sugg', null, {
    role: 'group', 'data-sugg': sug.id, 'data-block-of': sug.block_id,
    'aria-label': `Suggestion de ${sug.auteur || 'l’agent'}, posture ${POSTURE_LABEL[sug.posture] || sug.posture}`,
  });
  node.append(head, before, after, reason, actions, error);

  const run = async (decision, motif) => {
    accept.disabled = true;
    refuse.disabled = true;
    error.hidden = true;
    // Le motif part avec le geste : le formulaire de refus ne porte plus de saisie à garder, le rechargement peut redessiner la carte.
    const form = node.querySelector('.at-refuse');
    form?.remove();
    try {
      if ((await decide(sug, decision, motif)) === false) { // renoncé à défaire la validation d'une exigence : la suggestion reste à trancher
        accept.disabled = false;
        refuse.disabled = false;
        accept.focus();
      }
    } catch (cause) {
      error.textContent = cause.message || String(cause);
      error.hidden = false;
      accept.disabled = false;
      refuse.disabled = false;
      if (form) actions.after(form);
    }
  };
  accept.addEventListener('click', () => run('accept'));
  refuse.addEventListener('click', () => {
    if (node.querySelector('.at-refuse')) return;
    const input = el('input', 'at-input', null, { type: 'text', 'aria-label': 'Motif du refus (facultatif)', placeholder: 'Motif (facultatif)', maxlength: '300' });
    const confirm = el('button', 'at-btn pri', 'Confirmer le refus', { type: 'button' });
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button' });
    const form = el('div', 'at-form at-refuse');
    form.append(input, confirm, cancel);
    const close = () => { form.remove(); refuse.focus(); };
    confirm.addEventListener('click', () => run('reject', input.value.trim()));
    cancel.addEventListener('click', close);
    form.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.stopPropagation(); close(); }
      else if (event.key === 'Enter') { event.preventDefault(); run('reject', input.value.trim()); }
    });
    actions.after(form);
    input.focus();
  });
  return node;
}

function documentView(ctx, root, slug, doc, phaseFocus, chantiers = null) {
  const state = { doc, chantiers }; // `chantiers` : les lignes de la bibliothèque (pour `[[chantier]]`), nul si illisibles
  const chantier = doc.chantier;
  const sheet = el('article', 'at-sheet', null, { 'aria-label': 'Document du chantier' });
  const nav = el('nav', 'at-phases', null, { 'aria-label': 'Étapes du cadrage' });
  // La ligne d'état reste le miroir lisible du dernier retour (les modules y écrivent) ; l'affichage et l'annonce passent
  // par la zone de notification, ancrée au bas de la fenêtre visible.
  const status = el('p', 'at-status sr-only', '', { 'data-role': 'atelier-status' });
  const notifier = createNotifier(ctx);
  // Un message peut porter son type (`data-kind` : ok, warn, error) ; sans lui, le type se déduit du texte.
  const mirror = new MutationObserver(() => {
    const kind = status.getAttribute('data-kind');
    status.removeAttribute('data-kind');
    if (status.textContent) notifier.push(status.textContent, kind ? { kind } : {});
  });
  mirror.observe(status, { childList: true, characterData: true, subtree: true });
  ctx.signal.addEventListener('abort', () => mirror.disconnect());
  // Lot 3 : les commentaires ancrés. `reqBlock` dit quel bloc porte chaque exigence, pour compter ses fils ouverts.
  const reqBlock = new Map();
  const drafts = createDrafts(ctx, slug); // le filet de sécurité : les saisies en cours sont gardées dans le navigateur
  let liveTrace = null; // la trace du découpage (carte d'une exigence au survol)
  const params = ctx.params || {};
  const say = (message, kind) => { if (kind) status.setAttribute('data-kind', kind); status.textContent = message; };
  // Mode lecture (adresse puis navigateur) : l'adresse garde aussi la phase et l'exigence visées (voir `itemParams`).
  const reading = createReading(ctx, { sheet, say, setItem: () => { if (ctx.setItem) ctx.setItem(itemParams()); } }, params);
  const itemParams = (extra = {}) => ({ chantier: slug, phase: focusId, ...(reading.on() ? { lecture: '1' } : {}), ...extra });
  // Les références du texte (REQ-001, tâche du kit, [[chantier]]) : liens vers l'exigence, la tâche ou le chantier.
  const refs = createRefs(ctx, { slug, doc: () => state.doc, trace: () => liveTrace, chantiers: () => state.chantiers, go: (req) => goReq(req) });
  const cm = createComments(ctx, slug, { doc: () => state.doc, sheet, reload: (message) => reload(message), drafts });
  const steps = el('ol', 'at-steps', null, { 'aria-label': 'Déroulé du chantier', 'data-role': 'steps' });
  let stepsShown = '';
  const drawSteps = () => {
    const list = stepsOf(state.doc.phases.map((p) => ({ ...p, state: phaseState(state.doc, p) })), liveTrace);
    const key = stable(list);
    if (key === stepsShown) return; // rien n'a changé : le déroulé n'est pas redessiné (ni relu par un lecteur d'écran)
    stepsShown = key;
    steps.replaceChildren();
    for (const step of list) {
      const item = el('li', `at-step is-${step.status}`, null, { 'data-step': step.id });
      if (step.status === 'current') item.setAttribute('aria-current', 'step');
      const mark = el('span', 'at-step-mark', null, { 'aria-hidden': 'true' });
      if (step.status === 'done') mark.append(icon('i-act-approve'));
      item.append(mark, step.label, el('span', 'sr-only', step.status === 'done' ? ' — fait' : step.status === 'current' ? ' — en cours' : ' — à venir'));
      steps.append(item);
    }
  };
  const liveDoc = createLiveDoc(ctx, slug, {
    sheet, status, comments: (req) => cm.openOn(reqBlock.get(req)),
    onTrace: (trace) => { liveTrace = trace; drawSteps(); refs.refresh(); }, // la carte d'exigence ouverte se met à jour
    onSplit: (message) => reload(message, 'ok'),
  });
  // Modifier une exigence validée défait sa validation : une confirmation avant d'écrire, un rappel de la commande après.
  const devalidation = createDevalidation(ctx, slug, {
    host: root, trace: () => liveTrace, refresh: () => liveDoc.refresh(), notify: (message, options) => notifier.push(message, options),
  });
  const phaseIds = () => state.doc.phases.map((p) => p.id);
  let focusId = phaseFromParams({ phase: phaseFocus }, phaseIds(), state.doc.phases[0].id);
  let compareIds = null; // les phases de la comparaison d'historique affichée (la feuille est alors masquée), sinon nul
  let compareFocus = ''; // celle que la barre marque pendant la comparaison
  let lockUntil = 0; // juste après un clic sur une phase, le défilement ne la redécide pas
  const narrow = () => root.clientWidth < WIDE_MIN;
  // Lot 5 : le chat sait quel chantier est ouvert (et quelle phase) ; la barre de l'assistant guide le cadrage.
  setAtelierContext({ slug, phase: focusId });
  ctx.signal.addEventListener('abort', () => setAtelierContext(null));
  // Le document suit son fichier : l'empreinte que porte chaque réponse du pouls de l'assistant décide (voir `atelier-synchro.js`).
  const sync = createSync(ctx, { doc: () => state.doc, docAt: () => docAt, reload: () => reload(), sheet, notify: (message, options) => notifier.push(message, options) });
  const assist = createAssistant(ctx, slug, {
    focus: () => focusId,
    goPhase: (id) => sheet.isConnected && nav.querySelector(`[data-phase="${id}"]`)?.click(),
    reload: (message, kind) => reload(message, kind),
    say: (message, kind) => { if (kind) status.setAttribute('data-kind', kind); status.textContent = message; },
    // Une erreur liée à un bloc se lit aussi à son pied, là où l'on agissait.
    blockError: (blockId, message) => {
      const host = sheet.querySelector(`[data-block="${CSS.escape(blockId)}"]`);
      if (!host) return;
      host.querySelector('[data-role="block-error"]')?.remove();
      const note = el('p', 'at-error at-blk-err', message, { role: 'alert', 'data-role': 'block-error' });
      note.addEventListener('click', (event) => event.stopPropagation()); // lire l'erreur n'ouvre pas l'édition du bloc
      host.append(note);
    },
    narrow,
    phone: () => root.clientWidth < PHONE_MAX, // sous 900 px, le document d'abord : l'assistant reste derrière sa pastille
    drafts,
    files: (fichiers, annexes, options) => sync.check(fichiers, annexes, options).catch(() => {}), // un fichier de phase modifié hors du cockpit : le document le suit
    // La mascotte de l'assistant : combien de suggestions attendent, et la première mise sous les yeux.
    suggestions: () => state.doc.phases.reduce((n, p) => n + (p.suggestions || []).length, 0),
    showSuggestion: () => {
      const card = sheet.querySelector('.at-sugg');
      if (!card) return false;
      card.scrollIntoView({ block: 'center' });
      (card.querySelector('button') || card).focus({ preventScroll: true });
      return true;
    },
  });
  const pendingNote = el('span', 'at-pending', null, { 'data-role': 'doc-sugg-count' });
  const commentNote = el('span', 'at-pending', null, { 'data-role': 'doc-comment-count' });
  const countNote = () => {
    const waiting = state.doc.phases.reduce((n, p) => n + (p.suggestions || []).length, 0);
    pendingNote.textContent = waiting ? ` ${waiting} suggestion${waiting > 1 ? 's' : ''} en attente.` : '';
    commentNote.textContent = cm.summary();
    assist.sync();
  };

  /**
   * La phase affichée change : l'assistant et le chat le savent, la barre la marque, l'URL la garde. Un clic (`scroll`)
   * crée une entrée d'historique (« Précédent » y revient) ; le suivi du défilement la remplace seulement.
   */
  const setFocus = (id, { scroll = false } = {}) => {
    if (id === focusId && !scroll) return;
    const changed = id !== focusId;
    focusId = id;
    if (changed) {
      setAtelierContext({ slug, phase: focusId });
      assist.refresh();
      phaseNav();
    }
    const hash = atelierHash({ slug, phase: id, lecture: reading.on() });
    if (scroll && location.hash !== hash) history.pushState(null, '', location.pathname + location.search + hash);
    if (ctx.setItem) ctx.setItem(itemParams({ phase: id }));
    if (!scroll) return;
    lockUntil = Date.now() + 700;
    const heading = sheet.querySelector(`[data-section="${id}"] h2`);
    if (heading) { heading.scrollIntoView({ block: 'start' }); heading.focus({ preventScroll: true }); }
  };

  /**
   * Un lien `REQ-001` du texte : défile jusqu'à la ligne de l'exigence (phase Exigences du même chantier), la surligne
   * brièvement et garde `&req=` dans l'adresse. Sans rechargement : le document, le focus et les saisies restent.
   */
  const goReq = (req, { push = true } = {}) => {
    const found = exigenceBlocks(state.doc).get(req);
    const host = found && sheet.querySelector(`.at-blk[data-req="${CSS.escape(req)}"]`);
    if (!host) return false;
    lockUntil = Date.now() + 900;
    setFocus(found.phase);
    host.scrollIntoView({ block: 'center' });
    if (!reading.on()) host.focus({ preventScroll: true });
    flash(ctx, host);
    const hash = atelierHash({ slug, phase: found.phase, req, lecture: reading.on() });
    if (push && location.hash !== hash) history.pushState(null, '', location.pathname + location.search + hash);
    if (ctx.setItem) ctx.setItem(itemParams({ phase: found.phase, req }));
    return true;
  };

  /** Un choix de la palette ⌘K : une phase (`rub` = -1) ou une de ses rubriques, sans recharger le document. */
  const goOutline = (phaseId, rub) => {
    if (rub < 0) { nav.querySelector(`[data-phase="${CSS.escape(phaseId)}"]`)?.click(); return; }
    const target = sheet.querySelectorAll(`[data-section="${CSS.escape(phaseId)}"] > .at-rub`)[rub];
    if (!target) return;
    lockUntil = Date.now() + 900;
    setFocus(phaseId);
    target.setAttribute('tabindex', '-1');
    target.scrollIntoView({ block: 'start' });
    target.focus({ preventScroll: true });
  };

  // Mise à jour EN PLACE : un bouton de phase qui porte le focus n'est jamais remplacé (le suivi du défilement et le
  // rechargement de l'avancement appellent ceci à tout moment, y compris pendant que quelqu'un tabule dans la barre).
  const phaseNav = () => {
    const have = new Map([...nav.children].map((node) => [node.getAttribute('data-phase'), node]));
    state.doc.phases.forEach((p, index) => {
      const indicator = phaseIndicator(phaseState(state.doc, p)); // le calcul d'avancement unique du serveur
      let button = have.get(p.id);
      have.delete(p.id);
      if (!button) {
        button = el('button', 'at-phase', null, { type: 'button', 'data-phase': p.id });
        button.addEventListener('click', () => {
          if (compareIds) { // la feuille est masquée : la phase se lit dans la comparaison, si elle y figure
            const target = compareIds.includes(p.id) ? document.querySelector(`[data-role="hist-comparaison"] [data-phase="${p.id}"]`) : null;
            if (target) { compareFocus = p.id; phaseNav(); target.scrollIntoView({ block: 'start' }); return; }
          }
          setFocus(p.id, { scroll: true });
        });
      }
      const name = PHASE_NAMES[p.id] || p.title;
      if (button.getAttribute('data-state') !== indicator || button.getAttribute('data-name') !== name) {
        button.setAttribute('data-state', indicator);
        button.setAttribute('data-name', name);
        button.title = `${name} : ${INDICATOR_LABEL[indicator]}`;
        const mark = el('span', 'at-phase-mark', null, { 'aria-hidden': 'true' });
        if (indicator === 'complete') mark.append(icon('i-act-approve'));
        button.replaceChildren(mark, name, el('span', 'sr-only', ` — ${INDICATOR_LABEL[indicator]}`));
      }
      if (p.id === (compareIds ? compareFocus : focusId)) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
      if (nav.children[index] !== button) nav.insertBefore(button, nav.children[index] || null);
    });
    for (const gone of have.values()) gone.remove();
  };

  // La phase lue suit le défilement : les sections qui touchent la bande de lecture (sous la barre collante).
  const touching = new Set(); // les éléments de section dans la bande ; ceux qui ont été remplacés ne comptent plus
  const follow = () => {
    if (Date.now() < lockUntil || !sheet.isConnected) return;
    const atBottom = root.scrollTop > 0 && root.scrollHeight - root.scrollTop - root.clientHeight < 4;
    const inBand = new Set([...touching].filter((node) => node.isConnected).map((node) => node.getAttribute('data-section')));
    const next = activePhase(phaseIds(), inBand, focusId, atBottom);
    if (next !== focusId) setFocus(next);
  };
  const spy = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) touching.add(entry.target); else touching.delete(entry.target);
      }
      follow();
    }, { root, rootMargin: '-88px 0px -62% 0px' })
    : null;
  let frame = 0;
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; follow(); }); };
  root.addEventListener('scroll', onScroll, { passive: true });
  ctx.signal.addEventListener('abort', () => {
    if (spy) spy.disconnect();
    root.removeEventListener('scroll', onScroll);
    cancelAnimationFrame(frame);
  });

  let docAt = Date.now(); // quand le document affiché a été relu ou réécrit par un geste de cette page
  /** Ce que le serveur dit des suggestions et des commentaires est adopté si rien d'extérieur n'est à voir : le pouls n'y verra pas un dépôt. */
  const adoptAnnexes = (fresh) => {
    docAt = Date.now();
    if (fresh && fresh.annexes && annexMessage(state.doc, fresh) === '' && annexMessage(fresh, state.doc) === '') state.doc.annexes = fresh.annexes;
  };
  const reload = async (message, kind) => {
    try {
      state.doc = await ctx.api.atelierChantier(slug);
    } catch (cause) {
      if (!ctx.signal.aborted) status.textContent = `Rechargement impossible : ${cause.message || cause}`;
      return;
    }
    if (ctx.signal.aborted) return;
    const top = root.scrollTop;
    drawAll();
    if (root.scrollTop !== top) root.scrollTop = top; // la lecture ne saute pas
    docAt = Date.now();
    // La ligne d'état est une zone d'annonce : un rechargement sans message (synchro, pouls) ne la vide jamais, seul un nouveau message la change.
    if (message) {
      if (kind) status.setAttribute('data-kind', kind);
      status.textContent = message;
    }
    liveDoc.refresh();
  };

  const decide = async (sug, decision, motif) => {
    let guard = null;
    if (decision === 'accept') {
      // Une suggestion qui réécrit une exigence validée en défait la validation : confirmation avant d'écrire.
      const target = state.doc.phases.flatMap((p) => p.blocks || []).find((b) => b.id === sug.block_id);
      guard = await devalidation.ask(target ? target.text : sug.avant, sug.texte);
      if (ctx.signal.aborted) return;
      if (!guard.go) return false; // renoncé : la suggestion reste en attente, rien n'est écrit
    }
    try {
      await ctx.api.atelierDecision(slug, sug.id, decision, motif);
    } catch (cause) {
      if (ctx.signal.aborted) return;
      if (cause.code === 409) {
        // Deux causes de 409, dites séparément : déjà tranchée (autre onglet, autre geste) ou bloc changé.
        const done = cause.payload && cause.payload.reason === 'deja-tranchee';
        await reload(done
          ? 'Cette suggestion a déjà été tranchée ailleurs : le document a été rechargé, rien n’a été écrit de plus.'
          : 'Le bloc a changé depuis la suggestion : elle est périmée, rien n’a été écrit.');
        return;
      }
      throw cause;
    }
    if (ctx.signal.aborted) return;
    await reload(decision === 'accept' ? 'Suggestion acceptée : le bloc est réécrit.' : 'Suggestion refusée : le document n’a pas changé.');
    if (guard) devalidation.after(guard);
  };

  /** Après une réécriture de bloc : les fils de la phase suivent leur passage (ancres recalculées par le serveur). */
  const syncComments = async (phaseId, written) => {
    try {
      const fresh = await ctx.api.atelierChantier(slug);
      if (ctx.signal.aborted) return;
      const mine = state.doc.phases.find((p) => p.id === phaseId);
      const next = fresh.phases.find((p) => p.id === phaseId);
      if (!mine || !next) return;
      // Des fils dont le passage vient de disparaître : on le dit là où l'on a écrit, avec un lien qui y mène.
      const lost = newOrphans(mine.comments, next.comments);
      mine.comments = next.comments;
      adoptAnnexes(fresh);
      if (lost.length) {
        const where = lost[0];
        notifier.push(orphanNotice(lost.length), {
          kind: 'warn',
          action: {
            label: lost.length > 1 ? 'Voir les commentaires' : 'Voir le commentaire',
            run: () => {
              const thread = sheet.querySelector(`[data-com="${CSS.escape(where)}"]`);
              if (thread) { thread.scrollIntoView({ block: 'center' }); thread.focus({ preventScroll: true }); }
            },
          },
        });
      }
      countNote();
      drawSection(mine);
      const again = mine.blocks.find((b) => b.text === written);
      sheet.querySelector(`[data-block="${CSS.escape(again ? again.id : '')}"]`)?.focus();
    } catch {
      // Le prochain chargement du document remettra les fils à jour.
    }
  };

  /** La saisie d'un bloc se termine : son nœud ne porte plus rien à garder, le prochain dessin le remplace. */
  const endEdit = (host) => {
    host.classList.remove('is-editing');
    release(host);
  };

  const editBlock = (host, phase, block, resumed = null, conflict = null) => {
    const area = el('textarea', null, null, { 'aria-label': `Modifier le bloc : ${blockTitle(block)}`, spellcheck: 'true' });
    // Un gabarit « À compléter » s'ouvre VIDE (le gabarit n'est que l'invite) ; vide ou annulé, le fichier garde le gabarit.
    const template = Boolean(block.placeholder || isTemplateText(block.text));
    if (template) area.setAttribute('placeholder', templateHint(block.text));
    area.value = resumed != null ? resumed : (template ? '' : block.text); // un brouillon n'est remis que sur demande (« Reprendre »)
    drafts.bind(area, 'bloc', block.id, block.text);
    const save = el('button', 'at-btn pri', null, { type: 'button' });
    save.append(icon('i-act-save'), 'Enregistrer');
    const cancel = el('button', 'at-btn ghost', 'Annuler', { type: 'button' });
    const hint = el('span', 'at-muted', 'Ctrl+Entrée enregistre, Échap annule.');
    const error = el('p', 'at-error', '', { role: 'alert' });
    error.hidden = true;
    const bar = el('div', 'at-edit-bar');
    bar.append(save, cancel, hint);
    const form = el('div', 'at-edit', null, { 'data-role': 'block-edit', 'data-block': block.id });
    form.append(area, bar, error);
    host.replaceChildren(form);
    host.classList.add('is-editing');
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);

    const leave = (focusBack) => {
      const fresh = state.doc.phases.find((p) => p.id === phase.id);
      drafts.clear('bloc', block.id);
      endEdit(host);
      drawSection(fresh);
      if (focusBack) sheet.querySelector(`[data-block="${CSS.escape(block.id)}"]`)?.focus();
    };
    let mark = block; // ce que le serveur attend : le bloc tel qu'on l'a lu (réarmé quand on tranche un conflit)
    let blockId = block.id;
    // La zone de conflit n'existe que le temps d'un conflit : aucune barre vide sous la zone de texte.
    const conflictBox = el('div', 'at-conflict', null, { 'data-role': 'block-conflict', role: 'alert' });
    const showConflict = (payload) => {
      const cur = payload.current;
      const lead = cur
        ? 'Ce bloc a changé depuis que tu l’as ouvert. Rien n’est écrit, ta saisie est conservée : choisis quelle version garder.'
        : (payload.error || 'Ce bloc est introuvable ou ambigu.') + ' Ta saisie est conservée ci-dessous.';
      const pane = (label, body, role) => {
        const box = el('div', 'at-conflict-col');
        const pre = el('pre', 'at-conflict-text', body, { 'data-role': role });
        box.append(el('strong', null, label), pre);
        return box;
      };
      const cols = el('div', 'at-conflict-cols');
      cols.append(pane('Ma version', area.value, 'conflict-mine'));
      if (cur) cols.append(pane('Version actuelle', cur.text, 'conflict-theirs'));
      const choices = el('div', 'at-edit-bar');
      if (cur) {
        const keep = el('button', 'at-btn pri', 'Garder la mienne', { type: 'button', 'data-role': 'conflict-keep-mine' });
        keep.addEventListener('click', () => {
          mark = cur; blockId = cur.id; conflictBox.remove(); // la prochaine écriture part de la version actuelle
          commit();
        });
        const take = el('button', 'at-btn', 'Prendre l’autre', { type: 'button', 'data-role': 'conflict-take-other' });
        take.addEventListener('click', async (event) => {
          event.stopPropagation(); // l'édition vient de se fermer : ce clic ne doit pas la rouvrir en remontant jusqu'au bloc
          drafts.clear('bloc', block.id);
          endEdit(host);
          await reload('Version actuelle conservée : ta saisie n’a pas été enregistrée.');
        });
        choices.append(keep, take);
        if (payload.merged) {
          const merge = el('button', 'at-btn', 'Fusion proposée', { type: 'button', 'data-role': 'conflict-merge' });
          merge.addEventListener('click', () => {
            area.value = payload.merged; mark = cur; blockId = cur.id; conflictBox.remove();
            status.textContent = 'Fusion placée dans la zone de texte : relis-la puis enregistre.';
            area.focus();
          });
          choices.append(merge);
        }
      }
      conflictBox.replaceChildren(el('p', null, lead), cols, choices);
      form.insertBefore(conflictBox, bar);
      conflictBox.hidden = false;
      // Le panneau peut tomber sous le pli : il vient sous les yeux et prend le focus (la zone `role="alert"` l'annonce).
      conflictBox.setAttribute('tabindex', '-1');
      conflictBox.scrollIntoView({ block: 'start' });
      conflictBox.focus({ preventScroll: true });
    };
    const commit = async () => {
      if (area.value === mark.text && mark === block) { leave(true); return; }
      if (template && mark === block && !area.value.trim()) { leave(true); return; } // rien saisi : le gabarit reste tel quel
      save.disabled = true;
      error.hidden = true;
      const guard = await devalidation.ask(mark.text, area.value); // exigence validée : confirmation avant d'écrire
      if (ctx.signal.aborted) return;
      if (!guard.go) { save.disabled = false; area.focus(); return; } // renoncé : rien n'est écrit, la saisie reste
      try {
        const result = await ctx.api.atelierBloc(slug, blockId, area.value, mark);
        if (ctx.signal.aborted) return;
        // La vue d'un bloc réécrit n'embarque pas les suggestions : celles des autres blocs restent, celles du
        // bloc modifié sont périmées (le serveur le dira de même au prochain chargement).
        // La phase d'AUJOURD'HUI : des rechargements ont pu remplacer l'objet lu à l'ouverture pendant la saisie.
        const live = state.doc.phases.find((p) => p.id === phase.id) || phase;
        result.phase.suggestions = (live.suggestions || []).filter((x) => x.block_id !== block.id);
        result.phase.comments = live.comments || []; // provisoire : le serveur recalcule les ancres, relues ci-dessous
        state.doc.phases = state.doc.phases.map((p) => (p.id === phase.id ? result.phase : p));
        countNote();
        phaseNav();
        drafts.clear('bloc', block.id);
        endEdit(host);
        drawSection(result.phase);
        status.textContent = 'Bloc enregistré.';
        loop.warn(result.avertissements); // un identifiant d'exigence en double dans ce fichier
        reloadProgress();
        liveDoc.refresh(); // une exigence réécrite peut être « modifiée depuis la validation »
        const written = result.phase.blocks.find((b) => b.text === area.value.trimEnd());
        sheet.querySelector(`[data-block="${CSS.escape(written ? written.id : '')}"]`)?.focus();
        if ((live.comments || []).length) syncComments(phase.id, area.value.trimEnd());
        devalidation.after(guard); // la validation défaite : lignes d'état relues, commande rappelée
      } catch (cause) {
        if (ctx.signal.aborted) return;
        if (cause.code === 409) {
          // Conflit par bloc : rien n'est écrit et la saisie reste dans la zone ; les deux versions sont montrées.
          if (cause.payload && cause.payload.submitted !== undefined) {
            showConflict(cause.payload);
            save.disabled = false;
            return;
          }
          endEdit(host); // le brouillon local garde la saisie
          await reload('Le fichier a changé entre-temps : le document a été rechargé, ta modification n’a pas été enregistrée.');
          return;
        }
        error.textContent = cause.message || String(cause);
        error.hidden = false;
        save.disabled = false;
        area.focus();
      }
    };
    save.addEventListener('click', commit);
    cancel.addEventListener('click', () => leave(true));
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); leave(true); }
      else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); commit(); }
    });
    if (conflict) showConflict(conflict); // une case cochée a rencontré un conflit : le panneau habituel, avec la case refusée comme « ma version »
  };

  /**
   * Cocher ou décocher une case : la ligne visée du bloc est réécrite (les autres, à l'octet près) par la route d'édition de bloc,
   * avec le contrôle de conflit par bloc. Un conflit rétablit la case et ouvre le panneau de conflit de l'éditeur.
   */
  const toggleTask = async (host, phase, block, line, checked, box) => {
    const next = toggleTaskLine(block.text, line, checked);
    const boxes = [...host.querySelectorAll('.at-task-box')];
    const undo = () => { box.checked = !checked; boxes.forEach((b) => { b.disabled = false; }); };
    if (next == null) { undo(); return; }
    boxes.forEach((b) => { b.disabled = true; });
    const guard = await devalidation.ask(block.text, next); // une case d'une exigence validée : confirmation si le texte lu par la validation change
    if (ctx.signal.aborted) return;
    if (!guard.go) { undo(); return; }
    try {
      const result = await ctx.api.atelierBloc(slug, block.id, next, block);
      if (ctx.signal.aborted) return;
      const live = state.doc.phases.find((p) => p.id === phase.id) || phase;
      result.phase.suggestions = (live.suggestions || []).filter((x) => x.block_id !== block.id);
      result.phase.comments = live.comments || [];
      state.doc.phases = state.doc.phases.map((p) => (p.id === phase.id ? result.phase : p));
      countNote();
      phaseNav();
      drawSection(result.phase);
      status.textContent = checked ? 'Case cochée.' : 'Case décochée.';
      loop.warn(result.avertissements);
      reloadProgress();
      liveDoc.refresh();
      const written = result.phase.blocks.find((b) => b.text === next.trimEnd());
      const again = sheet.querySelector(`[data-block="${CSS.escape(written ? written.id : '')}"]`);
      again?.querySelector(`.at-task-box[data-line="${line}"]`)?.focus();
      if ((live.comments || []).length) syncComments(phase.id, next.trimEnd());
      devalidation.after(guard);
    } catch (cause) {
      if (ctx.signal.aborted) return;
      undo();
      if (cause.code === 409) {
        if (cause.payload && cause.payload.submitted !== undefined) {
          if (!host.classList.contains('is-editing')) editBlock(host, phase, block, next, cause.payload);
          return;
        }
        await reload('Le fichier a changé entre-temps : le document a été rechargé, la case n’a pas été modifiée.', 'warn');
        return;
      }
      status.setAttribute('data-kind', 'error');
      status.textContent = `La case n’a pas été enregistrée : ${cause.message || cause}`;
    }
  };

  const blockNode = (phase, block) => {
    if (!block.editable) return renderBlock(block, { refs });
    const host = el('div', 'at-blk', null, {
      tabindex: '0', 'data-block': block.id, 'data-kind': block.kind,
      'aria-label': `Modifier le bloc : ${blockTitle(block)}`,
      ...(block.req ? { 'data-req': block.req } : {}), // la cible d'un lien `REQ-001` du texte
    });
    const onToggle = (line, checked, box) => toggleTask(host, phase, block, line, checked, box);
    host.append(renderBlock(block, { onToggle, refs }), cm.addButton(phase, block, host, blockTitle(block)), assist.challenge(block));
    tag(host, 'host', sigOf(block));
    const edit = (resumed = null) => { if (!host.classList.contains('is-editing')) editBlock(host, phase, block, resumed); };
    // Une saisie commencée avant un rechargement de la page est PROPOSÉE ici (Reprendre / Jeter), jamais remise en silence.
    const saved = drafts.offer('bloc', block.id, { base: block.text, resume: (text) => edit(text), back: () => host });
    const unsent = cm.draftOffer(block, host);
    host.append(...[saved, unsent].filter(Boolean));
    // Sélectionner un passage (pour le commenter) ne doit pas ouvrir l'édition du bloc : seul un clic sans sélection le fait.
    host.addEventListener('click', () => {
      if (reading.on()) return; // en lecture, un clic sur un bloc n'ouvre pas l'éditeur
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !host.contains(selection.anchorNode)) edit();
    });
    host.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target === host && !reading.on()) { event.preventDefault(); edit(); }
    });
    return host;
  };

  const drawSection = (phase) => {
    const old = sheet.querySelector(`[data-section="${phase.id}"]`);
    const section = el('section', 'at-sec', null, { 'data-section': phase.id, 'aria-labelledby': `at-h-${phase.id}` });
    // Chaque enfant de la section porte son identité (`data-rk`) et ce qu'il affiche (`data-rs`) : c'est ce qui permet de
    // réconcilier le nouveau dessin avec l'ancien au lieu de le remplacer (voir `atelier-reconcile.js`).
    const plain = new Map();
    const refsKey = refs.signature(); // une exigence ou un chantier qui apparaît change le rendu des références
    section.append(tag(el('h2', null, PHASE_NAMES[phase.id] || phase.title, { id: `at-h-${phase.id}`, tabindex: '-1' }), 'title', sigOf(PHASE_NAMES[phase.id] || phase.title)));
    if (!phase.blocks.length) {
      const text = phase.readable === false ? 'Ce fichier n’est pas de l’UTF-8 : modifie-le hors du cockpit.' : 'Cette phase n’a pas de fichier.';
      section.append(tag(el('p', 'at-muted', text), 'empty', sigOf(text)));
    }
    let intro = true;
    const drawn = new Set();
    for (const block of phase.blocks) {
      if (block.kind === 'entete' || block.kind === 'titre') continue;
      if (intro && block.kind === 'paragraphe' && !block.section) {
        const text = bare(block.text).replace(/^_|_$/g, '');
        section.append(tag(withCode(el('p', 'at-intro'), text), 'intro', sigOf(text)));
        intro = false;
        continue;
      }
      intro = false;
      if (block.editable) {
        // Le bloc et ses fils côte à côte (marge) ou l'un sous l'autre (étroit) : la mise en page décide.
        const row = el('div', 'at-cmrow', null, { 'data-rc': '1' });
        row.append(blockNode(phase, block), cm.margin(block));
        const opened = cm.threadsOf(block).filter((c) => c.etat === 'ouvert').length;
        if (opened) row.append(tag(reading.dot(opened), 'dot', sigOf(opened))); // la pastille du mode lecture (masquée hors lecture)
        section.append(tag(row, `blk:${block.id}`, sigOf([block, cm.threadsOf(block).map(cm.threadSig), refsKey])));
        drawn.add(block.id);
      } else {
        section.append(tag(blockNode(phase, block), plainKey(block.kind, block.text, plain), refsKey));
      }
      if (block.kind === 'exigence' && block.req) {
        reqBlock.set(block.req, block.id);
        section.append(tag(liveDoc.line(block.req), `live:${block.req}`, SELF)); // état réel, sous l'exigence : se met à jour lui-même
      }
      for (const sug of (phase.suggestions || []).filter((x) => x.block_id === block.id)) section.append(tag(suggestionNode(sug, decide), `sugg:${sug.id}`, sigOf(sug)));
    }
    const lost = cm.orphans(phase, drawn); // les fils dont le passage a disparu : en fin de phase, avec leur extrait
    if (lost) section.append(lost);
    if (old) {
      reconcileChildren(old, section, { stale: staleBlock });
    } else {
      sheet.append(section);
      if (spy) spy.observe(section);
    }
    cm.paint();
    reading.sync(); // en lecture, les nouveaux nœuds sont eux aussi non éditables
  };

  /**
   * Un bloc en cours d'édition dont l'identité a disparu du document rechargé : le serveur l'a réécrit (`twin` : sa version)
   * ou supprimé (`twin` nul) pendant la saisie. La saisie reste, un bandeau le dit ; l'enregistrement passe par le contrôle de
   * conflit par bloc, qui montre les deux versions.
   */
  const staleBlock = (row, twin) => {
    const form = row.querySelector('.is-editing > [data-role="block-edit"]');
    if (!form) return;
    let note = form.querySelector('[data-role="block-stale"]');
    if (!note) {
      note = el('p', 'at-stale', '', { role: 'status', 'data-role': 'block-stale' });
      form.insertBefore(note, form.firstChild);
    }
    const text = twin
      ? 'Ce bloc a changé dans le document pendant ta saisie. Ta saisie est conservée : à l’enregistrement, tu verras les deux versions.'
      : 'Ce bloc a disparu du document pendant ta saisie (modifié ou supprimé ailleurs). Ta saisie est conservée : à l’enregistrement, tu verras ce qui s’est passé.';
    if (note.textContent !== text) note.textContent = text;
  };

  const loop = createLoopPanel(ctx); // avancement honnête, tâches du découpage, avertissements de saisie
  /** Après un bloc écrit : l'avancement et les avertissements se relisent, sans redessiner la feuille en cours d'édition. */
  const reloadProgress = async () => {
    try {
      const fresh = await ctx.api.atelierChantier(slug);
      if (ctx.signal.aborted) return;
      state.doc.avancement = fresh.avancement;
      state.doc.taches = fresh.taches;
      state.doc.chantier = fresh.chantier;
      adoptAnnexes(fresh); // l'écriture qui précède est la nôtre : le pouls n'y verra pas un dépôt extérieur
      paintHead();
      loop.update(fresh);
      phaseNav(); // la barre collante lit l'avancement unique
      drawSteps();
      assist.refresh();
    } catch {
      // Le prochain chargement du document remettra l'avancement à jour.
    }
  };
  let paintHead = () => {};
  const drawAll = () => {
    paintHead();
    loop.update(state.doc);
    countNote();
    phaseNav();
    drawSteps();
    for (const phase of state.doc.phases) drawSection(phase);
  };

  const back = el('button', 'at-btn ghost', null, { type: 'button' });
  back.append(icon('i-act-back'), 'Tous les chantiers');
  back.addEventListener('click', () => ctx.goto('atelier', {}));
  const title = el('h2', 'at-title', chantier.titre || chantier.slug);
  // L'état et la date de modification suivent les écritures : repeints à chaque rechargement du document.
  const metaText = (c) => [
    formatDate(c.cree) ? `Ouvert le ${formatDate(c.cree)}.` : 'Chantier historique du projet.',
    c.modifie ? `Modifié le ${formatDate(c.modifie, { withTime: true })}.` : '',
  ].filter(Boolean).join(' ');
  let stateBadge = badge(STATE_LABEL[chantier.etat] || chantier.etat, STATE_TONE[chantier.etat] || '');
  title.append(stateBadge);
  const metaNode = document.createTextNode(metaText(chantier));
  const meta = el('p', 'at-sub');
  meta.append(metaNode, pendingNote, commentNote);
  paintHead = () => {
    const c = state.doc.chantier;
    const next = badge(STATE_LABEL[c.etat] || c.etat, STATE_TONE[c.etat] || '');
    stateBadge.replaceWith(next);
    stateBadge = next;
    metaNode.nodeValue = metaText(c);
  };
  // En-tête compact : retour, titre et statut, dates, avancement (pastille dépliable) sur une seule ligne.
  back.classList.add('at-back');
  const head = el('div', 'at-head at-head-doc');
  const ident = el('div', 'at-ident');
  ident.append(title, meta);
  head.append(back, ident, loop.pill);
  drawAll();
  // Une seule barre collante : phases, déroulé en quatre étapes, « Valider et découper » et son état, pastille de l'assistant
  // replié. L'aide MOA et l'aperçu du découpage s'ouvrent sous elle ; le document et l'assistant viennent ensuite côte à côte.
  const phasebar = el('div', 'at-phasebar', null, { 'data-role': 'atelier-barre' });
  // Lecture et navigation (lecture seule) : menu « Document », recherche Ctrl+F, plan à gauche de la feuille.
  const docMenu = createDocumentMenu(ctx, {
    slug, doc: () => state.doc,
    say: (message, kind) => { if (kind) status.setAttribute('data-kind', kind); status.textContent = message; },
  });
  const find = createFind(ctx, { sheet });
  const plan = createPlan(ctx, { root, sheet, doc: () => state.doc });
  phasebar.append(nav, steps, liveDoc.toolbar, plan.toggle, docMenu.element, reading.button, assist.pill);
  find.attach(phasebar);
  const main = el('div', 'at-doc-main');
  main.append(docMenu.printTitle, status, sheet, docMenu.printNotes);
  const grid = el('div', 'at-doc-grid');
  grid.append(main, assist.element);
  // Historique des versions (lecture seule) : un bouton dans la barre, un volet à la place de l'assistant (voir `atelier-historique.js`).
  const versionsPane = createHistory(ctx, slug, {
    sheet, main, grid, say: (message) => { status.textContent = message; },
    reload: (message, kind) => reload(message, kind), // après une restauration (ou son annulation) : le document est relu
    // Restaurer un bloc qui réécrit une exigence validée en défait la validation : même confirmation que pour une édition.
    guard: (before, after) => devalidation.ask(before, after),
    guarded: (guard) => devalidation.after(guard),
    // La comparaison affichée (ses phases) : la barre des phases marque celle dont le diff est sous les yeux.
    onCompare: (ids) => { compareIds = ids; compareFocus = ids && ids.length ? ids[0] : ''; phaseNav(); },
  });
  docMenu.element.before(versionsPane.button);
  grid.append(versionsPane.element);
  plan.attach(grid);
  const view = page(ctx, head, loop.element, phasebar, liveDoc.panels, grid);
  view.querySelector('.at-body').classList.add('at-body-doc');
  // Lien d'évitement : en tête de l'Atelier, visible au focus, il saute l'en-tête et la barre jusqu'au premier bloc du document.
  const skip = el('a', 'at-skip', 'Aller au document', { href: '#at-document', 'data-role': 'skip-document' });
  skip.addEventListener('click', (event) => {
    event.preventDefault();
    const target = sheet.querySelector('.at-blk') || sheet.querySelector('h2');
    if (!target) return;
    target.scrollIntoView({ block: 'center' });
    target.focus({ preventScroll: true });
  });
  view.prepend(skip);
  view.append(notifier.element);
  reading.attach(view); // le mode lecture (adresse, puis dernier choix du navigateur) se pose sur la page
  refs.mount();
  root.replaceChildren(view);
  assist.layout(); // le choix mémorisé, ou la largeur, décide si le panneau est déplié
  // Le tiroir (écran étroit) recouvre le bas de la fenêtre : la toile garde une marge d'autant, et les notifications le dépassent.
  const lift = () => {
    const drawer = narrow() && !assist.element.hidden;
    const height = drawer ? assist.element.offsetHeight : 0;
    view.style.setProperty('--at-lift', `${height}px`);
    root.style.scrollPaddingBottom = height ? `${height + 16}px` : '';
  };
  if (typeof ResizeObserver === 'function') {
    const watch = new ResizeObserver(lift);
    watch.observe(assist.element);
    watch.observe(root);
    ctx.signal.addEventListener('abort', () => watch.disconnect());
  }
  ctx.signal.addEventListener('abort', () => { root.style.scrollPaddingBottom = ''; });
  lift();
  liveDoc.refresh();
  if (phaseFocus) {
    lockUntil = Date.now() + 700;
    sheet.querySelector(`[data-section="${focusId}"] h2`)?.scrollIntoView({ block: 'start' });
  }
  const wantedReq = reqFromParams(params); // `&req=REQ-001` : l'exigence visée par le lien est mise sous les yeux
  if (wantedReq) goReq(wantedReq, { push: false });
  if (!wantedReq && reading.on()) ctx.setItem?.(itemParams()); // l'adresse reflète le mode (et reste partageable)

  // Le plan du chantier ouvert, pour la palette ⌘K : « Aller à : Exigences », « Aller à : Brief › Le problème ».
  setAtelierOutline({ slug, outline: () => buildOutline(state.doc, PHASE_NAMES), go: goOutline });
  ctx.signal.addEventListener('abort', () => setAtelierOutline(null));
}

export async function mount(root, ctx) {
  root.replaceChildren(el('p', 'at-muted', 'Chargement…', { role: 'status' }));
  const slug = ctx.params && ctx.params.chantier;
  try {
    if (slug) {
      // La liste des chantiers sert aux références `[[chantier]]` : une seule lecture, en même temps que le document.
      const [doc, rows] = await Promise.all([
        ctx.api.atelierChantier(slug),
        ctx.api.atelierChantiers().then((data) => data.chantiers || []).catch(() => null),
      ]);
      if (ctx.signal.aborted) return;
      documentView(ctx, root, slug, doc, ctx.params.phase, rows);
      return;
    }
    const [data, ideas] = await Promise.all([
      ctx.api.atelierChantiers(),
      ctx.api.ideas().then((d) => (d.ideas || []).filter((i) => i.state === 'open')).catch(() => []),
    ]);
    if (ctx.signal.aborted) return;
    library(ctx, root, data, ideas);
  } catch (error) {
    if (ctx.signal.aborted) return;
    root.replaceChildren(page(ctx, ctx.empty(
      slug ? 'Chantier introuvable' : 'Atelier indisponible',
      String(error.message || error),
      null,
      { label: 'Tous les chantiers', run: () => ctx.goto('atelier', {}) },
    )));
  }
}
