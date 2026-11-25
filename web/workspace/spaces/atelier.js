// Atelier de spec (en test) : la bibliothèque des chantiers et le document d'un chantier, en blocs.
//
// Lot 1 de l'épique #1197. Artboards « Rangement » (bibliothèque) et « A. La feuille » (document) de la
// maquette Atelier. Absent tant que le drapeau `cockpit.test_features.atelier` est éteint : la coque ne
// l'importe qu'au montage de l'espace, et l'espace n'existe pas sans le drapeau.
//
// Hors de ce lot : l'assistant (sa place reste vide), les suggestions, « Valider et découper », la voix.
// Données : `GET atelier/chantiers`, `POST atelier/chantiers`, `GET atelier/chantiers/<slug>`,
// `PUT atelier/chantiers/<slug>/blocs/<id>` (voir `src/grimoire/tools/workspace_atelier.py`).

import { renderMarkdown } from '../markdown.js';
import { badge, icon } from '../shell.js';


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
  banner.append(icon('i-act-preview'), el('span'), link);
  banner.children[1].append(
    el('strong', null, 'Atelier en test.'),
    " Aucune tâche n'est lancée tant que tu n'as pas validé le découpage.",
  );
  return banner;
}

function page(ctx, ...content) {
  const body = el('div', 'at-body');
  body.append(...content);
  const wrap = el('div', 'at-page');
  wrap.append(testBanner(ctx), body);
  return wrap;
}

const open = (ctx, slug, phase) => ctx.goto('atelier', phase ? { chantier: slug, phase } : { chantier: slug });

// ── Bibliothèque (artboard « Rangement ») ───────────────────────────────────

function segments(row) {
  const done = (row.phases || []).filter((state) => state === 'rempli').length;
  const segs = el('span', 'at-segs', null, { role: 'img', 'aria-label': `${done} étapes sur ${(row.phases || []).length || 5} terminées` });
  for (let i = 0; i < 5; i += 1) segs.append(el('span', 'at-seg' + (i < done ? ' on' : '')));
  return { segs, done };
}

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
  const { segs, done } = segments(row);
  const progress = el('span', 'at-cell');
  progress.append(segs, el('span', 'at-muted', done >= 5 ? 'Cahier des charges complet' : `${done} étape${done > 1 ? 's' : ''} sur 5`));
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
    : 'Aucun chantier pour l’instant : crée-en un, ou ouvre-en un depuis une idée.';
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
  const section = el('section', 'at-card', null, { 'aria-labelledby': 'at-ideas-h', 'data-role': 'ideas' });
  const h = el('h3', null, 'Idées', { id: 'at-ideas-h' });
  const top = el('div', 'at-actions');
  top.append(h, el('span', 'at-muted', 'Une idée devient un chantier quand tu veux la creuser.'));
  section.append(top);
  const error = el('p', 'at-error', '', { role: 'alert' });
  error.hidden = true;
  if (!ideas.length) section.append(el('p', 'at-muted', 'Aucune idée gardée.'));
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

function documentView(ctx, root, slug, doc, phaseFocus) {
  const state = { doc };
  const chantier = doc.chantier;
  const sheet = el('article', 'at-sheet', null, { 'aria-label': 'Document du chantier' });
  const nav = el('nav', 'at-phases', null, { 'aria-label': 'Étapes du cadrage' });
  const status = el('p', 'at-muted at-status', '', { role: 'status', 'aria-live': 'polite', 'data-role': 'atelier-status' });
  let focusId = phaseFocus || state.doc.phases[0].id;

  const phaseNav = () => {
    nav.replaceChildren();
    for (const p of state.doc.phases) {
      const button = el('button', 'at-phase', null, { type: 'button', 'data-phase': p.id });
      if (p.id === focusId) button.setAttribute('aria-current', 'step');
      if (p.state === 'rempli') button.append(icon('i-act-approve'));
      button.append(PHASE_NAMES[p.id] || p.title);
      button.addEventListener('click', () => {
        focusId = p.id;
        phaseNav();
        const heading = sheet.querySelector(`[data-section="${p.id}"] h2`);
        if (heading) { heading.scrollIntoView({ block: 'start' }); heading.focus(); }
      });
      nav.append(button);
    }
  };

  const reload = async (message) => {
    try {
      state.doc = await ctx.api.atelierChantier(slug);
    } catch (cause) {
      if (!ctx.signal.aborted) status.textContent = `Rechargement impossible : ${cause.message || cause}`;
      return;
    }
    if (ctx.signal.aborted) return;
    drawAll();
    status.textContent = message || '';
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
    const commit = async () => {
      if (area.value === block.text) { leave(true); return; }
      save.disabled = true;
      error.hidden = true;
      try {
        const result = await ctx.api.atelierBloc(slug, block.id, area.value, phase.fingerprint);
        if (ctx.signal.aborted) return;
        state.doc.phases = state.doc.phases.map((p) => (p.id === phase.id ? result.phase : p));
        phaseNav();
        drawSection(result.phase);
        status.textContent = 'Bloc enregistré.';
        const written = result.phase.blocks.find((b) => b.text === area.value.trimEnd());
        sheet.querySelector(`[data-block="${CSS.escape(written ? written.id : '')}"]`)?.focus();
      } catch (cause) {
        if (ctx.signal.aborted) return;
        if (cause.code === 409) {
          // Le fichier a changé depuis la lecture : rien n'est écrit, on recharge et on le dit.
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
    host.append(renderBlock(block));
    const edit = () => { if (!host.classList.contains('is-editing')) editBlock(host, phase, block); };
    host.addEventListener('click', edit);
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
    for (const block of phase.blocks) {
      if (block.kind === 'entete' || block.kind === 'titre') continue;
      if (intro && block.kind === 'paragraphe' && !block.section) {
        section.append(el('p', 'at-intro', bare(block.text).replace(/^_|_$/g, '')));
        intro = false;
        continue;
      }
      intro = false;
      section.append(blockNode(phase, block));
    }
    if (old) old.replaceWith(section); else sheet.append(section);
  };

  const drawAll = () => {
    sheet.replaceChildren();
    phaseNav();
    for (const phase of state.doc.phases) drawSection(phase);
  };

  const back = el('button', 'at-btn ghost', null, { type: 'button' });
  back.append(icon('i-act-back'), 'Tous les chantiers');
  back.addEventListener('click', () => ctx.goto('atelier', {}));
  const title = el('h2', 'at-title', chantier.titre || chantier.slug);
  title.append(badge(STATE_LABEL[chantier.etat] || chantier.etat, STATE_TONE[chantier.etat] || ''));
  const opened = formatDate(chantier.cree);
  const meta = el('p', 'at-sub', [
    opened ? `Ouvert le ${opened}.` : 'Chantier historique du projet.',
    chantier.modifie ? `Modifié le ${formatDate(chantier.modifie, { withTime: true })}.` : '',
  ].filter(Boolean).join(' '));
  const head = el('div', 'at-head');
  const left = el('div');
  left.append(back, title, meta);
  head.append(left);
  drawAll();
  root.replaceChildren(page(ctx, head, nav, status, sheet));
  if (phaseFocus) sheet.querySelector(`[data-section="${phaseFocus}"] h2`)?.scrollIntoView({ block: 'start' });
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
