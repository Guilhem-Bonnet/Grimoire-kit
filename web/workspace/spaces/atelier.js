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

import { renderMarkdown } from '../markdown.js';
import { atelierIsWide, badge, icon, setAtelierWide } from '../shell.js';
import { createLiveDoc } from './atelier-live.js';
import { createComments } from './atelier-comments.js';
import { createAssistant } from './atelier-assistant.js';
import { createHistory } from './atelier-historique.js';
import { createLoopPanel, phaseState, progressCell, waitingBadges } from './atelier-avancement.js';
import { setAtelierContext } from '../atelier-context.js';
import { createNotifier } from './atelier-notify.js';
import { createPlan } from './atelier-plan.js';
import { createFind } from './atelier-recherche.js';
import { createDocumentMenu } from './atelier-document-menu.js';
import {
  INDICATOR_LABEL, WIDE_MIN, activePhase, capitalize, codeSegments, newOrphans, orphanNotice,
  phaseFromParams, phaseHash, phaseIndicator, stepsOf,
} from './atelier-layout-logic.js';


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

function markdown(text) {
  const holder = el('div');
  holder.innerHTML = renderMarkdown(text); // `renderMarkdown` échappe le HTML avant tout rendu
  return holder;
}

function renderBlock(block) {
  const node = el('div');
  switch (block.kind) {
    case 'rubrique': {
      node.className = 'at-rub';
      node.textContent = block.text.replace(/^#+\s*/, '');
      return node;
    }
    case 'question': {
      node.className = 'at-q';
      node.append(icon('i-act-preview'), markdown(bare(block.text)));
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
      node.append(badge('Décision', 'acc'), markdown(bare(block.text)));
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
      if (block.placeholder) node.className = 'at-placeholder';
      node.append(markdown(block.placeholder ? bare(block.text) : block.text));
      return node;
    }
  }
}

function blockTitle(block) {
  return bare(block.text).split('\n')[0].slice(0, 60);
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
    try {
      await decide(sug, decision, motif);
    } catch (cause) {
      error.textContent = cause.message || String(cause);
      error.hidden = false;
      accept.disabled = false;
      refuse.disabled = false;
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

function documentView(ctx, root, slug, doc, phaseFocus) {
  const state = { doc };
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
  const cm = createComments(ctx, slug, { doc: () => state.doc, sheet, reload: (message) => reload(message) });
  const steps = el('ol', 'at-steps', null, { 'aria-label': 'Déroulé du chantier', 'data-role': 'steps' });
  let liveTrace = null;
  const drawSteps = () => {
    steps.replaceChildren();
    for (const step of stepsOf(state.doc.phases.map((p) => ({ ...p, state: phaseState(state.doc, p) })), liveTrace)) {
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
    onTrace: (trace) => { liveTrace = trace; drawSteps(); },
    onSplit: (message) => reload(message, 'ok'),
  });
  const phaseIds = () => state.doc.phases.map((p) => p.id);
  let focusId = phaseFromParams({ phase: phaseFocus }, phaseIds(), state.doc.phases[0].id);
  let lockUntil = 0; // juste après un clic sur une phase, le défilement ne la redécide pas
  const narrow = () => root.clientWidth < WIDE_MIN;
  // Lot 5 : le chat sait quel chantier est ouvert (et quelle phase) ; la barre de l'assistant guide le cadrage.
  setAtelierContext({ slug, phase: focusId });
  ctx.signal.addEventListener('abort', () => setAtelierContext(null));
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
    editing: () => Boolean(sheet.querySelector('.is-editing')),
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
    const hash = phaseHash(slug, id);
    if (scroll && location.hash !== hash) history.pushState(null, '', location.pathname + location.search + hash);
    if (ctx.setItem) ctx.setItem({ chantier: slug, phase: id });
    if (!scroll) return;
    lockUntil = Date.now() + 700;
    const heading = sheet.querySelector(`[data-section="${id}"] h2`);
    if (heading) { heading.scrollIntoView({ block: 'start' }); heading.focus({ preventScroll: true }); }
  };

  const phaseNav = () => {
    nav.replaceChildren();
    for (const p of state.doc.phases) {
      const indicator = phaseIndicator(phaseState(state.doc, p)); // le calcul d'avancement unique du serveur
      const button = el('button', 'at-phase', null, { type: 'button', 'data-phase': p.id, 'data-state': indicator, title: `${PHASE_NAMES[p.id] || p.title} : ${INDICATOR_LABEL[indicator]}` });
      if (p.id === focusId) button.setAttribute('aria-current', 'step');
      const mark = el('span', 'at-phase-mark', null, { 'aria-hidden': 'true' });
      if (indicator === 'complete') mark.append(icon('i-act-approve'));
      button.append(mark, PHASE_NAMES[p.id] || p.title, el('span', 'sr-only', ` — ${INDICATOR_LABEL[indicator]}`));
      button.addEventListener('click', () => setFocus(p.id, { scroll: true }));
      nav.append(button);
    }
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

  const reload = async (message, kind) => {
    try {
      state.doc = await ctx.api.atelierChantier(slug);
    } catch (cause) {
      if (!ctx.signal.aborted) status.textContent = `Rechargement impossible : ${cause.message || cause}`;
      return;
    }
    if (ctx.signal.aborted) return;
    drawAll();
    if (message && kind) status.setAttribute('data-kind', kind);
    status.textContent = message || '';
    liveDoc.refresh();
  };

  const decide = async (sug, decision, motif) => {
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

  const editBlock = (host, phase, block) => {
    const area = el('textarea', null, null, { 'aria-label': `Modifier le bloc : ${blockTitle(block)}`, spellcheck: 'true' });
    area.value = block.text;
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
      drawSection(fresh);
      if (focusBack) sheet.querySelector(`[data-block="${CSS.escape(block.id)}"]`)?.focus();
    };
    let mark = block; // ce que le serveur attend : le bloc tel qu'on l'a lu (réarmé quand on tranche un conflit)
    let blockId = block.id;
    const conflictBox = el('div', 'at-conflict', null, { 'data-role': 'block-conflict', role: 'alert' });
    conflictBox.hidden = true;
    form.insertBefore(conflictBox, bar);
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
          mark = cur; blockId = cur.id; conflictBox.hidden = true; // la prochaine écriture part de la version actuelle
          commit();
        });
        const take = el('button', 'at-btn', 'Prendre l’autre', { type: 'button', 'data-role': 'conflict-take-other' });
        take.addEventListener('click', async () => {
          await reload('Version actuelle conservée : ta saisie n’a pas été enregistrée.');
        });
        choices.append(keep, take);
        if (payload.merged) {
          const merge = el('button', 'at-btn', 'Fusion proposée', { type: 'button', 'data-role': 'conflict-merge' });
          merge.addEventListener('click', () => {
            area.value = payload.merged; mark = cur; blockId = cur.id; conflictBox.hidden = true;
            status.textContent = 'Fusion placée dans la zone de texte : relis-la puis enregistre.';
            area.focus();
          });
          choices.append(merge);
        }
      }
      conflictBox.replaceChildren(el('p', null, lead), cols, choices);
      conflictBox.hidden = false;
    };
    const commit = async () => {
      if (area.value === mark.text && mark === block) { leave(true); return; }
      save.disabled = true;
      error.hidden = true;
      try {
        const result = await ctx.api.atelierBloc(slug, blockId, area.value, mark);
        if (ctx.signal.aborted) return;
        // La vue d'un bloc réécrit n'embarque pas les suggestions : celles des autres blocs restent, celles du
        // bloc modifié sont périmées (le serveur le dira de même au prochain chargement).
        result.phase.suggestions = (phase.suggestions || []).filter((x) => x.block_id !== block.id);
        result.phase.comments = phase.comments || []; // provisoire : le serveur recalcule les ancres, relues ci-dessous
        state.doc.phases = state.doc.phases.map((p) => (p.id === phase.id ? result.phase : p));
        countNote();
        phaseNav();
        drawSection(result.phase);
        status.textContent = 'Bloc enregistré.';
        loop.warn(result.avertissements); // un identifiant d'exigence en double dans ce fichier
        reloadProgress();
        liveDoc.refresh(); // une exigence réécrite peut être « modifiée depuis la validation »
        const written = result.phase.blocks.find((b) => b.text === area.value.trimEnd());
        sheet.querySelector(`[data-block="${CSS.escape(written ? written.id : '')}"]`)?.focus();
        if ((phase.comments || []).length) syncComments(phase.id, area.value.trimEnd());
      } catch (cause) {
        if (ctx.signal.aborted) return;
        if (cause.code === 409) {
          // Conflit par bloc : rien n'est écrit et la saisie reste dans la zone ; les deux versions sont montrées.
          if (cause.payload && cause.payload.submitted !== undefined) {
            showConflict(cause.payload);
            save.disabled = false;
            return;
          }
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
  };

  const blockNode = (phase, block) => {
    if (!block.editable) return renderBlock(block);
    const host = el('div', 'at-blk', null, {
      tabindex: '0', 'data-block': block.id, 'data-kind': block.kind,
      'aria-label': `Modifier le bloc : ${blockTitle(block)}`,
    });
    host.append(renderBlock(block), cm.addButton(phase, block, host, blockTitle(block)), assist.challenge(block));
    const edit = () => { if (!host.classList.contains('is-editing')) editBlock(host, phase, block); };
    // Sélectionner un passage (pour le commenter) ne doit pas ouvrir l'édition du bloc : seul un clic sans sélection le fait.
    host.addEventListener('click', () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !host.contains(selection.anchorNode)) edit();
    });
    host.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target === host) { event.preventDefault(); edit(); }
    });
    return host;
  };

  const drawSection = (phase) => {
    const old = sheet.querySelector(`[data-section="${phase.id}"]`);
    const section = el('section', 'at-sec', null, { 'data-section': phase.id, 'aria-labelledby': `at-h-${phase.id}` });
    section.append(el('h2', null, PHASE_NAMES[phase.id] || phase.title, { id: `at-h-${phase.id}`, tabindex: '-1' }));
    if (!phase.blocks.length) {
      section.append(el('p', 'at-muted', phase.readable === false ? 'Ce fichier n’est pas de l’UTF-8 : modifie-le hors du cockpit.' : 'Cette phase n’a pas de fichier.'));
    }
    let intro = true;
    const drawn = new Set();
    for (const block of phase.blocks) {
      if (block.kind === 'entete' || block.kind === 'titre') continue;
      if (intro && block.kind === 'paragraphe' && !block.section) {
        section.append(withCode(el('p', 'at-intro'), bare(block.text).replace(/^_|_$/g, '')));
        intro = false;
        continue;
      }
      intro = false;
      if (block.editable) {
        // Le bloc et ses fils côte à côte (marge) ou l'un sous l'autre (étroit) : la mise en page décide.
        const row = el('div', 'at-cmrow');
        row.append(blockNode(phase, block), cm.margin(block));
        section.append(row);
        drawn.add(block.id);
      } else {
        section.append(blockNode(phase, block));
      }
      if (block.kind === 'exigence' && block.req) {
        reqBlock.set(block.req, block.id);
        section.append(liveDoc.line(block.req)); // état réel, sous l'exigence
      }
      for (const sug of (phase.suggestions || []).filter((x) => x.block_id === block.id)) section.append(suggestionNode(sug, decide));
    }
    const lost = cm.orphans(phase, drawn); // les fils dont le passage a disparu : en fin de phase, avec leur extrait
    if (lost) section.append(lost);
    if (old) { if (spy) spy.unobserve(old); touching.delete(old); old.replaceWith(section); } else sheet.append(section);
    if (spy) spy.observe(section);
    cm.paint();
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
    sheet.replaceChildren();
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
  const docMenu = createDocumentMenu(ctx, { slug, doc: () => state.doc });
  const find = createFind(ctx, { sheet });
  const plan = createPlan(ctx, { root, sheet, doc: () => state.doc });
  phasebar.append(nav, steps, liveDoc.toolbar, plan.toggle, docMenu.element, assist.pill);
  find.attach(phasebar);
  const main = el('div', 'at-doc-main');
  main.append(docMenu.printTitle, status, sheet);
  const grid = el('div', 'at-doc-grid');
  grid.append(main, assist.element);
  // Historique des versions (lecture seule) : un bouton dans la barre, un volet à la place de l'assistant (voir `atelier-historique.js`).
  const versionsPane = createHistory(ctx, slug, { sheet, main, grid, say: (message) => { status.textContent = message; } });
  docMenu.element.before(versionsPane.button);
  grid.append(versionsPane.element);
  plan.attach(grid);
  const view = page(ctx, head, loop.element, phasebar, liveDoc.panels, grid);
  view.querySelector('.at-body').classList.add('at-body-doc');
  view.append(notifier.element);
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
}

export async function mount(root, ctx) {
  root.replaceChildren(el('p', 'at-muted', 'Chargement…', { role: 'status' }));
  const slug = ctx.params && ctx.params.chantier;
  try {
    if (slug) {
      const doc = await ctx.api.atelierChantier(slug);
      if (ctx.signal.aborted) return;
      documentView(ctx, root, slug, doc, ctx.params.phase);
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
