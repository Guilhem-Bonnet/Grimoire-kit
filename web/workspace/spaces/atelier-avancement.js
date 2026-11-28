// Atelier de spec (en test) : l'avancement honnête et la boucle après le découpage.
//
// Un seul calcul, côté serveur (`src/grimoire/core/cadrage_progress.py`) : par phase, les blocs remplis, vides (gabarit
// non touché), « je ne sais pas » et « sans objet » ; un état global (à cadrer, en cours, cadré avec N points à
// préciser, complet). Ce module ne calcule rien : il met en mots ce que le serveur rend, pour la liste des chantiers,
// la page du chantier et l'assistant. Il porte aussi ce qui suit « Valider et découper » (les tâches créées, avec un
// lien vers l'espace Travail), les avertissements de saisie, et l'aide à la validation (déclarer la MOA, lancer la
// commande dans son propre terminal). Aucun geste de validation ici : la MOA valide au terminal.

import { badge } from '../shell.js';
import { keepFocus, setText, stable } from './atelier-focus.js';

const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };
const TASK_STATUS = {
  proposed: 'proposée', ready: 'prête', claimed: 'réservée', running: 'en cours', waiting: 'en attente',
  blocked: 'bloquée', needs_verification: 'à vérifier', failed: 'en échec', closed: 'fermée', cancelled: 'annulée',
};

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

export const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/**
 * Les rubriques encore au gabarit, séparées de celles que la personne a marquées « sans objet » (une ligne de gabarit pour le
 * contrôle de validation, mais plus un point à faire). `rubs` : les phases du document (`rubriques` avec leur état) ; sans elles,
 * on garde le décompte du serveur. Rend `{ count, open: [{ id, title, names }], na: [...] }`.
 */
export function pendingSplit(av, rubs) {
  const marked = new Map();
  for (const phase of rubs || []) {
    marked.set(phase.id, new Set((phase.rubriques || []).filter((r) => r.etat === 'sans-objet').map((r) => r.section)));
  }
  const open = [];
  const na = [];
  for (const phase of av.phases) {
    const skip = marked.get(phase.id) || new Set();
    const empty = Object.entries(phase.sections).filter(([, state]) => state === 'vide').map(([name]) => name);
    const here = empty.filter((name) => !skip.has(name));
    const off = empty.filter((name) => skip.has(name));
    if (here.length) open.push({ id: phase.id, title: phase.title, names: here });
    if (off.length) na.push({ id: phase.id, title: phase.title, names: off });
  }
  return { count: open.reduce((n, p) => n + p.names.length, 0), open, na };
}

/** L'état global en quelques mots, pour une ligne de la liste. Jamais « complet » s'il reste un point au gabarit. */
export function avancementText(av) {
  if (!av) return '';
  if (av.etat === 'a-cadrer') return 'À cadrer';
  if (av.etat === 'en-cours') return `En cours, ${plural(av.a_preciser, 'point', 'points')} au gabarit`;
  if (av.etat === 'cadre') return `Cadré, ${plural(av.a_preciser, 'point', 'points')} à préciser`;
  return av.a_confirmer ? `Complet, dont ${av.a_confirmer} à confirmer` : 'Complet';
}

/**
 * L'état d'une phase pour la barre collante et le déroulé : lu du calcul d'avancement unique du serveur (`doc.avancement`),
 * jamais de l'ancien `phase.state`. `rempli` : plus aucun bloc au gabarit ; `partiel` : des blocs remplis ou couverts ;
 * `vide` sinon. Sans avancement (chargement), l'état de la phase tel que le serveur l'a donné.
 */
export function phaseState(doc, phase) {
  const mine = ((doc && doc.avancement && doc.avancement.phases) || []).find((p) => p.id === phase.id);
  if (!mine) return phase.state;
  if (mine.complete) return 'rempli';
  return mine.remplis + mine.je_ne_sais_pas + mine.sans_objet > 0 ? 'partiel' : 'vide';
}

/** La barre de cinq segments et le texte de la liste : un segment est allumé quand sa phase n'a plus aucun point au gabarit. */
export function progressCell(row) {
  const phases = (row.avancement && row.avancement.phases) || [];
  const done = phases.filter((p) => p.complete).length;
  const segs = el('span', 'at-segs', null, { role: 'img', 'aria-label': `${done} phase${done > 1 ? 's' : ''} sur ${phases.length || 5} sans point au gabarit` });
  for (let i = 0; i < 5; i += 1) segs.append(el('span', 'at-seg' + (i < done ? ' on' : '')));
  return { segs, text: avancementText(row.avancement) };
}

/** Ce qui attend la personne, en pastilles : la question en cours, les exigences à valider, la question du découpage.
 * Les suggestions et les commentaires ont déjà leur pastille dans la liste. */
export function waitingBadges(row) {
  const wait = row.attente || {};
  const out = [];
  const add = (role, label, title) => {
    const node = badge(label, 'warn', title);
    node.setAttribute('data-role', role);
    out.push(node);
  };
  if (wait.question) add('wait-question', 'Question en cours', 'Une question de l’entretien attend ta réponse');
  if (wait.a_valider) add('wait-validate', `${plural(wait.a_valider, 'exigence', 'exigences')} à valider`, 'Exigences Must que la MOA n’a pas encore validées, au terminal');
  if (wait.decoupage) add('wait-split', `${plural(wait.decoupage, 'question', 'questions')} du découpage`, 'Le découpage a posé une question : réponds-y pour débloquer les tâches');
  return out;
}

/** Copie dans le presse-papiers ; faux si le navigateur refuse. */
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Comment déclarer la MOA : la clé exacte, son fichier, un exemple à copier. Texte seulement : aucun bouton ne l'écrit
 * à la place de la personne (c'est elle qui engage son nom).
 */
export function moaHelp(moa, say = () => {}) {
  const how = moa.how || {};
  const box = el('div', 'at-moa', null, { 'data-role': 'moa-help' });
  box.append(el('p', null, 'Aucune MOA n’est déclarée : personne ne peut valider une exigence.'));
  const where = el('p', 'at-muted');
  where.append('Pose la clé ', el('code', null, how.key || 'governance.roles.moa'), ' dans ', el('code', null, how.file || 'project-context.yaml'), ' (à la racine du projet) :');
  const example = el('pre', 'at-moa-example', null, { 'data-role': 'moa-example' });
  example.append(el('code', null, how.example || 'governance:\n  roles:\n    moa: Prénom Nom\n'));
  const button = el('button', 'at-btn', 'Copier l’exemple', { type: 'button', 'data-act': 'copy-moa' });
  button.addEventListener('click', async () => {
    say((await copy(how.example || '')) ? 'Exemple copié : colle-le dans project-context.yaml, avec ton nom.' : 'Copie impossible ici : sélectionne l’exemple et copie-le à la main.');
  });
  box.append(where, example, button);
  if (how.rule) box.append(el('p', 'at-muted', how.rule));
  return box;
}

/** La consigne de validation : où lancer la commande, et ce que l'écran affichera ensuite. */
export function validationHelp() {
  return el('p', 'at-muted', 'Lance cette commande dans un terminal que tu ouvres toi-même, hors de toute session d’agent : elle te demande de confirmer à l’écran (o/N), car valider engage la MOA. Ensuite cet écran passe l’exigence à « validée », avec le nom de la MOA et la date, au prochain pouls ou avec « Actualiser ».', { 'data-role': 'validation-help' });
}

/**
 * Sous l'en-tête d'un chantier : l'avancement honnête, les tâches créées par le découpage, les avertissements de saisie.
 * `update(doc)` à chaque (re)chargement du document ; `warn(liste)` après l'écriture d'un bloc.
 */
export function createLoopPanel(ctx) {
  // En-tête compact : l'avancement tient en une pastille (dans la ligne du titre) dont le détail se déplie en bulle ;
  // les tâches créées et les avertissements, eux, restent sous l'en-tête (`element`), vides tant qu'il n'y en a pas.
  const pill = el('div', 'at-loop-pill', null, { 'data-role': 'loop' });
  const summary = el('p', 'at-loop-sum', '', { 'data-role': 'avancement', role: 'status' });
  const detail = el('div', 'at-loop-detail');
  pill.append(summary, detail);
  pill.addEventListener('keydown', (event) => { // Échap referme la bulle et rend le focus à son bouton
    const box = detail.querySelector('details[open]');
    if (event.key !== 'Escape' || !box) return;
    event.stopPropagation();
    box.open = false;
    box.querySelector('summary')?.focus();
  });
  const element = el('section', 'at-loop', null, { 'aria-label': 'Tâches du découpage et avertissements', 'data-role': 'loop-extras' });
  const tasks = el('div', 'at-loop-tasks', null, { 'data-role': 'chantier-taches' });
  const warnings = el('div', 'at-loop-warn', null, { 'data-role': 'dup-warnings' });
  element.append(tasks, warnings);

  // Chaque zone se redessine seulement si ce qu'elle montre a changé : le détail déplié, son focus et la lecture
  // d'un lecteur d'écran survivent aux rafraîchissements, et le focus est rendu si un remplacement est inévitable.
  const shown = { sections: null, tasks: null, warn: null };
  const redraw = (zone, host, value, draw) => {
    const key = stable(value);
    if (shown[zone] === key) return;
    shown[zone] = key;
    keepFocus(host, draw);
  };

  const sections = (av, rubs) => {
    const split = pendingSplit(av, rubs);
    return redraw('sections', detail, split, () => {
      detail.replaceChildren();
      if (!split.open.length && !split.na.length) return;
      const box = el('details', 'at-loop-todo', null, { 'data-role': 'avancement-detail' });
      box.append(el('summary', 'at-muted', split.open.length
        ? `Voir les ${plural(split.count, 'point restant', 'points restants')} au gabarit`
        : `Voir les ${plural(split.na.length, 'rubrique marquée sans objet', 'rubriques marquées sans objet')}`));
      if (split.open.length) {
        const list = el('ul');
        for (const phase of split.open) list.append(el('li', null, `${PHASE_NAMES[phase.id] || phase.title} : ${phase.names.join(', ')}`));
        box.append(list);
      }
      if (split.na.length) { // marquées sans objet : elles ne comptent plus dans les points restants
        const list = el('ul', null, null, { 'data-role': 'avancement-sans-objet' });
        list.append(el('li', 'at-muted', `Marquées sans objet, non comptées : ${split.na.map((p) => `${PHASE_NAMES[p.id] || p.title} : ${p.names.join(', ')}`).join(' ; ')}`));
        box.append(list);
      }
      detail.append(box);
    });
  };

  const taskList = (list) => redraw('tasks', tasks, list, () => {
    tasks.replaceChildren();
    if (!list.length) return;
    tasks.append(el('h3', 'at-loop-h', `${plural(list.length, 'tâche créée', 'tâches créées')} par le découpage`));
    const ul = el('ul', 'at-loop-list');
    for (const task of list) {
      const li = el('li', null, null, { 'data-task': task.id });
      const link = el('a', 'at-loop-link', task.id, { href: `#travail?task=${encodeURIComponent(task.id)}`, 'data-act': 'open-task' });
      link.addEventListener('click', (event) => { event.preventDefault(); ctx.goto('travail', { task: task.id }); });
      li.append(link, ` ${task.title} `, badge(TASK_STATUS[task.status] || task.status, task.status === 'closed' ? 'ok' : ''), el('span', 'at-muted', ` sert ${task.requirement}`));
      ul.append(li);
    }
    tasks.append(ul);
  });

  const warn = (list) => redraw('warn', warnings, list || [], () => {
    warnings.replaceChildren();
    for (const item of list || []) warnings.append(el('p', 'at-live-gap', item.message, { role: 'alert', 'data-req': item.req }));
  });

  const update = (doc) => {
    const av = doc.avancement;
    if (av) {
      const marks = [av.a_confirmer ? `${av.a_confirmer} « je ne sais pas »` : '', av.phases.some((p) => p.sans_objet) ? `${av.phases.reduce((n, p) => n + p.sans_objet, 0)} « sans objet »` : ''].filter(Boolean);
      setText(summary, av.resume + (marks.length && av.etat !== 'complet' ? ` (dont ${marks.join(' et ')}, comptés comme couverts)` : ''));
      summary.setAttribute('data-etat', av.etat);
      // Cette pastille compte TOUTES les rubriques encore au gabarit, facultatives comprises : le résumé « Ce qu’il me reste à faire » ne compte que le nécessaire.
      summary.title = 'Toutes les rubriques encore au gabarit, facultatives comprises. « Ce qu’il me reste à faire », en haut du document, ne compte que ce qui est nécessaire pour valider.';
      sections(av, doc.phases);
    }
    taskList(doc.taches || []);
    warn(doc.avertissements);
  };
  return { element, pill, update, warn };
}
