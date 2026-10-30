// Espace Exécuter — LOT 4.
//
// Remplace `kanban.html`. Cible : tâches en Board 4 ou 8 colonnes, Liste,
// Timeline ; la porte de chaque colonne en une ligne sous son titre ; carte de
// tâche à trois niveaux. Inspecteur : tâche — critères, preuves, prochaine
// porte avec bouton, timeline.
//
// Corrections dues par la revue §4.3 : huit états annoncés, huit accessibles
// (Board 8, ou la mention « colonnes repliées » sur Board 4) ; la transition
// suivante est l'information la plus grande, pas la plus petite (8,8 px) ;
// colonnes vides en pointillé.
//
// API consommées : api.tasks(), api.task(id), api.taskTrace(id),
// api.taskRecall(id), api.taskAction(id, 'claim'|'move'|'block'|'close', body).
// Le refus d'un gate revient en 200 avec `blocked: true` et la preuve
// manquante nommée — c'est une réponse à afficher, pas une erreur à avaler.
//
// Pilotage humain (issue #638, lot B) : les mêmes `api.taskAction` avec
// 'prioritize' | 'comment' | 'cancel' | 'ack' — jamais une écriture directe
// depuis la webview (ADR-007), toujours `TaskService`. Sélecteur de priorité
// et zone de consigne dans l'inspecteur, « Annuler » avec raison exigée, tri
// par priorité dans chaque colonne, badge « message en attente de la session »
// sur la carte (P74 : fin du jargon « consigne non lue »).
//
// Attente nommée (K6, issue #771) : un état `waiting` à raison nommée
// (`human`, `ci`, `quota`, `paused`). `waiting(human)` porte une question ;
// la vue « Décisions » (et le bandeau au-dessus du board) les liste, la plus
// ancienne d'abord, avec les réponses proposées. Répondre est `api.taskAction(
// id, 'answer', { text, question_id })` — le serveur pose l'origine humaine
// (canal cockpit, jeton de session), jamais ce corps. Une question posée par
// un checkpoint de flow ne se tranche pas ici : la décision s'atteste dans le
// flux, la vue le dit au lieu d'offrir un bouton qui ne déciderait rien.
//
// Timeline (#139) : `api.taskTrace(id)` porte maintenant jusqu'à cinq sources
// (ledger, hooks, gate, runtime, evidence, otel) triées dans le temps —
// filtrables par source et par gravité, chaque ligne s'ouvrant en accordéon
// sur son détail brut. Drill-down depuis l'inspecteur d'une carte (« Voir la
// timeline ») ou depuis `ctx.params = { task, view: 'timeline' }` qu'un
// `goto('executer', …)` externe (Observer) peut poser.
//
// Portefeuille (#638, lot C) : la portée « Tous les projets » lit
// `api.portfolioTasks()` — toutes les tâches de tous les projets du registre
// cockpit, agrégées côté serveur — avec une puce projet par carte, la session
// qui la porte (glyphe + mot : vivante / inactive / terminée / inconnue), sa priorité et ses
// messages en attente quand le champ existe. Les cartes se clé-ent par
// (projet, id) : deux ledgers peuvent dériver le même identifiant d'un même
// titre. Une action depuis le portefeuille passe par
// `api.portfolioTaskAction(id, action, body, slug)` — le `TaskService` du
// projet propriétaire, gate compris — et « Reprendre la session » ne fait
// que copier `claude --resume <id>` (hôte Claude Code) ou afficher
// l'identifiant : jamais d'exécution depuis la webview (ADR-007).

const STYLE_ID = 'ex-styles';

// Portée et filtres du portefeuille — au niveau du module, comme
// `timelineFilter` : revenir sur l'espace garde le dernier réglage.
const scope = { mode: 'project', state: '', slug: '', live: false };

// « Demande une action de moi » (artboard Travail) : le filtre garde les tâches qui
// attendent un humain — une question (attente `human`), une revue, un blocage.
// Calculé sur les colonnes et l'attente que l'API donne déjà, rien n'est deviné.
// Au niveau du module, comme `scope` : revenir sur l'espace garde le réglage.
const askMe = { on: false };

// P91 : le filtre des cinq présences (`task.presence.state`, P90), au niveau du
// module comme `askMe`. Vide = toutes les cartes ouvertes.
const presenceFilter = { state: '' };

// Les cinq présences d'une carte ouverte, dans l'ordre du cahier de vivacité
// (§2) : un glyphe (la forme), un mot, un ton qui ne fait que doubler la forme.
// « Bloquée » garde ses lignes d'attente (glyphe `user`/`hourglass`) : elle n'a
// pas de ligne de présence en plus.
const PRESENCE_UI = {
  working: { word: 'Au travail', icon: 'i-session-live', tone: 'acc' },
  silent: { word: 'Silencieuse', icon: 'i-session-idle', tone: '' },
  resume: { word: 'À reprendre', icon: 'i-session-resume', tone: 'warn' },
  blocked: { word: 'Bloquée', icon: 'hourglass', tone: 'warn' },
  none: { word: 'Sans agent', icon: 'i-session-unknown', tone: '' },
};
const PRESENCE_ORDER = ['working', 'silent', 'resume', 'blocked', 'none'];
// Tentatives avant « tentatives épuisées » (grimoire.missions.lease.MAX_ATTEMPTS).
const RESUME_MAX_ATTEMPTS = 3;
const presenceOf = (t) => (t.presence && t.presence.state) || '';

// Panneau « Assigner une session » (P47 lot 2) : l'ouverture vit au niveau du
// module, comme `scope` — le pouls live redessine l'inspecteur, et un panneau
// rouvert doit se recharger au lieu de disparaître (pattern task-create.js).
const assignUi = { task: '', open: false };
const needsHuman = (t) => isHumanWait(t) || t.board === 'review' || t.board === 'blocked';

// Les vues que l'URL peut demander (`#travail?view=liste`).
const VIEW_IDS = ['board4', 'board8', 'liste', 'sessions', 'timeline', 'decisions'];

// Colonnes du standard, dans l'ordre normatif (grimoire.missions.board.BOARD_LIFECYCLE).
const LIFECYCLE = ['proposed', 'ready', 'in_progress', 'blocked', 'review', 'accepted', 'released', 'archived'];
import {
  actionStatus, badge, copyText, failureText, glyph, icon, levelBars, listNav, monogram, moreMenu as sharedMoreMenu, rowActions,
  setActionStatus, stateIcon, undoToast,
} from '../shell.js';
import { agoText, costText, durationText } from '../live.js';
import { activityBlockAsync } from '../activity.js';
import { DONE_RECENT_LIMIT, splitRecent } from './board-split.js';
import { renderSessionInspector, renderSessions } from './sessions.js';
import { newTaskBlock, newTaskButton } from './task-create.js';
import { appendQuestionV2, isV2 } from './decision-card.js';
import { ideaBlock, ideaButton, requestIdeaFocus } from './task-intake.js';
import { el as rcEl, injectRecallStyles, plural as rcPlural, receiptMeta, replayControl, servedList, whenText } from './recall-receipt.js';

const COLUMN_LABEL = {
  proposed: 'Proposée', ready: 'Prête', in_progress: 'En cours', blocked: 'Bloquée',
  // `archived` ne reçoit que des tâches annulées (`_TO_BOARD` : CANCELLED → archived).
  review: 'Revue', accepted: 'Acceptée', released: 'Publiée', archived: 'Annulée',
};
// Board à 4 colonnes : regroupement des 8 états du standard. Les colonnes
// repliées sont nommées sous le titre, jamais tues (revue §4.3). Une tâche
// annulée n'est pas faite : elle a sa propre colonne, repliée par défaut.
const GROUPS4 = [
  { id: 'todo', icon: 'clipboard', label: 'À faire', cols: ['proposed', 'ready'] },
  // P72v2 : pas de 5e colonne pour « Bloquée » — un compteur de ton warn sous
  // « En cours » et une section « Bloqué » repliée par défaut (`blockedFold`).
  { id: 'doing', icon: 'bolt', label: 'En cours', cols: ['in_progress', 'blocked'], blockedFold: true },
  { id: 'review', icon: 'microscope', label: 'Revue', cols: ['review'] },
  { id: 'done', icon: 'seal', label: 'Fait', cols: ['accepted', 'released'], recentLimit: DONE_RECENT_LIMIT },
  { id: 'cancelled', icon: 'archive', label: 'Annulées', cols: ['archived'], folded: true },
];
// Colonne repliée dépliée ou non — au niveau du module, comme `scope` : revenir
// sur l'espace garde le dernier réglage.
const unfolded = new Set();
// I4 : colonne visible sur mobile (onglet actif) — au niveau du module, pour survivre aux re-rendus.
let activeGroup = '';
// P74 : une tâche close (acceptée, publiée, annulée) n'a plus de vivacité à
// montrer sur sa carte — le travail est fini. Si la session existe encore,
// c'est l'inspecteur qui le dit (bloc « Session »), jamais la carte.
const CLOSED_BOARDS = new Set(['accepted', 'released', 'archived']);
const isClosed = (t) => CLOSED_BOARDS.has(t.board);
// Board → état ledger, pour appeler `move` (grimoire.missions.board._FROM_BOARD).
const BOARD_TO_STATE = {
  proposed: 'proposed', ready: 'ready', in_progress: 'running', blocked: 'blocked',
  review: 'needs_verification', accepted: 'closed', released: 'closed', archived: 'cancelled',
};
// `finition` (grimoire.missions.schemas.MissionTask, lot 4.3, issue #561), en
// clair — lecture seule ici, aucune valeur pour "" (le champ n'est envoyé par
// `to_dict()` que quand il est posé).
const FINITION_LABEL = { maquette: 'Maquette', peaufine: 'Peaufinée' };
// Nature d'une dépendance (grimoire.missions.schemas.DependencyKind), en clair.
const DEP_LABEL = {
  blocks: 'bloque', relates: 'lié à', parent_child: 'parent / enfant',
  discovered_from: 'découverte depuis', supersedes: 'remplace',
};
// `risk_profile` (grimoire.missions.schemas.RiskProfile) : une vraie échelle de
// sévérité (grimoire.missions.board._PRIORITY_BY_RISK : light → low, standard →
// medium, le reste → high). Fin du point polysémique (P30) : un bouclier à 0, 1
// ou 2 traits, un octogone pour security_critical, et le mot à côté.
const RISK_ICON = {
  light: 'i-risk-light', standard: 'i-risk-standard', strict: 'i-risk-strict',
  security_critical: 'i-risk-critical', release: 'i-risk-strict',
};
// `type` (grimoire.missions.schemas.TaskType) : une icône par valeur, le
// libellé français au survol — le mot brut (« implementation ») disparaît de la carte.
const TYPE_LABEL = {
  analysis: 'Analyse', architecture: 'Architecture', implementation: 'Implémentation',
  test: 'Test', documentation: 'Documentation', migration: 'Migration',
  security: 'Sécurité', operation: 'Opération', cleanup: 'Nettoyage',
};
function typeGlyph(type) {
  return TYPE_LABEL[type] ? glyph(`i-task-${type}`, `type : ${TYPE_LABEL[type]}`, '', { alone: true }) : text('span', null, type);
}
// Échelle de priorité (grimoire.missions.board.PRIORITIES), la plus pressante
// en dernier ; `tasks_view()` la renvoie aussi (`priorities`) — la constante
// locale n'est que le repli si le serveur ne la donne pas.
const PRIORITIES = ['low', 'medium', 'high', 'critical'];
const PRIORITY_LABEL = { low: 'Basse', medium: 'Moyenne', high: 'Haute', critical: 'Critique' };
// Priorité en barres (1 à 3 sur 3) : critique garde trois barres et prend la
// teinte d'erreur. Une valeur hors échelle n'a pas de barres, seulement son mot.
const PRIORITY_BARS = { low: 1, medium: 2, high: 3, critical: 3 };
function priorityBars(priority) {
  if (!PRIORITY_BARS[priority]) return null;
  return levelBars(PRIORITY_BARS[priority], `priorité ${(PRIORITY_LABEL[priority] || priority).toLowerCase()}`, priority === 'critical' ? 'bad' : '');
}
const priorityRank = (p) => PRIORITIES.indexOf(p);
// Dans une colonne, la plus pressante d'abord ; à égalité, l'identifiant
// garde un ordre stable (même règle que `build_board`).
// Ce qui attend la décision d'un humain passe devant tout (K6).
const isHumanWait = (t) => t.status === 'waiting' && !!t.waiting && t.waiting.reason === 'human' && !!t.waiting.question;
const byPriority = (a, b) =>
  (Number(isHumanWait(b)) - Number(isHumanWait(a))) ||
  (priorityRank(b.effective_priority || 'medium') - priorityRank(a.effective_priority || 'medium')) ||
  String(a.id).localeCompare(String(b.id));
// La raison d'une attente, en clair — icône + mot, jamais la couleur seule.
const WAIT_LABEL = { human: 'décision attendue', ci: 'attend la CI', quota: 'attend le quota', paused: 'en pause' };
const waitGlyph = (waiting) => glyph(waiting.reason === 'human' ? 'user' : 'hourglass', 'attente');
const QUESTION_KIND_LABEL = {
  merge: 'fusion', permission: 'permission', choice: 'choix', resume_branch: 'reprise de branche',
  resume_unfenced: 'reprise sans frein', retry_exhausted: 'tentatives épuisées',
};
function waitingText(waiting) {
  const label = WAIT_LABEL[waiting.reason] || `attente (${waiting.reason})`;
  if (waiting.reason === 'human') return label;
  if (waiting.until && waiting.until !== 'unknown') return `${label} jusqu'à ${waiting.until.slice(0, 16).replace('T', ' ')}`;
  return `${label}, échéance inconnue`;
}
function ageText(iso) {
  const then = Date.parse(iso || '');
  if (Number.isNaN(then)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (minutes < 60) return `depuis ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `depuis ${hours} h` : `depuis ${Math.round(hours / 24)} j`;
}

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ex-wrap { padding: var(--sp-4) var(--sp-5); height: 100%; display: flex; flex-direction: column; gap: var(--sp-3); }
    @container canvas (max-width: 720px) { .ex-wrap { padding: var(--sp-3); } }
    /* I4 : le board défile seul ; les colonnes s'étirent à la hauteur du contenu pour que chaque en-tête reste collé en haut (un sticky ne dépasse jamais sa colonne) ; sous 720 px, une colonne pleine largeur à la fois. */
    .ex-board-shell { display: flex; flex-direction: column; gap: var(--sp-2); flex: 1 1 auto; min-height: 0; min-width: 0; }
    .ex-board { display: flex; gap: var(--sp-3); align-items: stretch; overflow: auto; flex: 1 1 auto; min-height: 0; position: relative; }
    .ex-tabs { display: none; flex: none; gap: var(--sp-2); overflow-x: auto; scrollbar-width: none; padding-bottom: 2px; }
    .ex-tab { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; min-height: 44px; min-width: 44px; padding: 0 var(--sp-3); border: 1px solid var(--line); border-radius: 999px; background: var(--e2); color: var(--ink2); font-size: var(--t-s); cursor: pointer; }
    .ex-tab .ico { width: 16px; height: 16px; }
    .ex-tab .mono { color: var(--ink3); }
    .ex-tab[aria-selected="true"] { background: var(--acc-soft); border-color: var(--acc); color: var(--ink); font-weight: 600; }
    .ex-tab:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
    @container canvas (max-width: 720px) {
      .ex-tabs { display: flex; }
      .ex-board { scroll-snap-type: x mandatory; overscroll-behavior-x: contain; gap: var(--sp-3); }
      .ex-board .ex-col { flex: 0 0 100%; max-width: 100%; scroll-snap-align: start; scroll-snap-stop: always; }
    }
    .ex-col { flex: 1 0 240px; max-width: 340px; display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
    .ex-col-head { padding: 6px 2px; position: sticky; top: 0; z-index: 2; background: var(--e2); }
    .ex-more { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 44px; padding: 0 var(--sp-3); border: 1px dashed var(--line); border-radius: var(--r); background: transparent; color: var(--ink2); font-size: var(--t-s); cursor: pointer; }
    .ex-more:hover { background: var(--hover); }
    .ex-more:focus-visible, .ex-older-row:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
    .ex-older { display: flex; flex-direction: column; gap: 4px; }
    .ex-older[hidden] { display: none; }
    .ex-older-row { display: flex; align-items: center; gap: var(--sp-2); min-height: 44px; padding: 0 var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); color: var(--ink); font-size: var(--t-s); text-align: left; cursor: pointer; min-width: 0; }
    .ex-older-row:hover { background: var(--hover); }
    .ex-older-row > .ex-older-title { flex: 1 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ex-older-row > time { flex: none; color: var(--ink3); font-size: var(--t-min); }
    .ex-col-title { font-size: var(--t-s); font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 6px; }
    .ex-col-title > .mono { color: var(--rub); font-weight: 600; }
    .ex-col-title .ico { width: 16px; height: 16px; color: var(--ink2); }
    .ex-col-gate { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .ex-col-body { display: flex; flex-direction: column; gap: 6px; min-height: 60px; }
    .ex-col-body[hidden] { display: none; }
    .ex-col-body.empty { border: 1px dashed var(--line); border-radius: var(--r); align-items: center; justify-content: center; color: var(--ink3); font-size: var(--t-min); padding: var(--sp-4) 0; }
    /* P72v2 : bloquées du board compact — compteur discret de ton warn (jamais une alarme), section repliée par défaut. */
    .ex-blocked-toggle { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; padding: 2px 8px; font-size: var(--t-min); border: 1px solid var(--warn); border-radius: var(--r); background: var(--warn-soft); color: var(--warn); cursor: pointer; }
    .ex-blocked-toggle:hover { background: var(--hover); }
    .ex-blocked-body { display: flex; flex-direction: column; gap: 6px; }
    .ex-blocked-body[hidden] { display: none; }
    .ex-blocked-title { font-size: var(--t-min); font-weight: 600; color: var(--ink2); }
    .ex-card { border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); box-shadow: var(--shadow); padding: var(--sp-3); cursor: pointer; display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
    /* P74 : liseré gauche stable par « ouverte par » (hash → --owner-N de tokens.css).
       Un repère de balayage, jamais le seul porteur de sens : le nom est écrit sur la carte. */
    .ex-card { border-left: 3px solid var(--owner-tone, var(--line)); }
    /* P74 : badge de type (code / design / doc / infra / autre) — pastille + mot, jamais la couleur seule. */
    .ex-type { display: inline-flex; align-items: center; gap: 5px; }
    .ex-type .ex-type-sw { width: 8px; height: 8px; border-radius: 2px; background: var(--type-tone, var(--ink3)); flex: none; }
    /* P74 : l'âge situé de la carte — un fait nommé, élidé s'il manque de place. */
    .ex-card .ex-card-age { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .ex-card-owner { display: inline-flex; align-items: center; gap: 4px; }
    .ex-card-owner > span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ex-card:hover { background: var(--hover); }
    .ex-card[aria-current="true"] { background: var(--acc-soft); box-shadow: inset 0 0 0 1px var(--acc); }
    .ex-card-top { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
    .ex-card-top .badge { flex: none; }
    .ex-card-top .ex-card-id { flex: 1 1 0; min-width: 4ch; }
    .ex-card-top .badge[data-role="route"] { flex: 0 1 auto; max-width: 70%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; }
    .ex-card-id { color: var(--ink3); font-size: var(--t-min); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
    .ex-proof { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; color: var(--ink2); font-size: var(--t-min); flex: none; }
    .ex-ring { width: 16px; height: 16px; transform: rotate(-90deg); }
    .ex-ring circle { fill: none; stroke-width: 2.6; }
    .ex-ring .ok { stroke: var(--ok-mark); }
    .ex-ring .bad { stroke: var(--bad-mark); }
    .ex-ring .todo { stroke: var(--ink3); opacity: 0.4; }
    .ex-card-title { font-size: var(--t-s); font-weight: 500; color: var(--ink); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
    .ex-card-flag { display: block; max-width: 100%; }
    .ex-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-3); }
    .ex-head .space-title { margin: 0; }
    .ex-head .seg { max-width: 100%; overflow-x: auto; scrollbar-width: none; }
    .ex-head .seg > button { flex: none; white-space: nowrap; }
    .ex-head-end { margin-left: auto; display: flex; align-items: center; gap: var(--sp-3); }
    .ex-ask { gap: 6px; }
    .ex-ask[aria-pressed="true"] { background: var(--acc-soft); border-color: var(--acc); color: var(--acc); }
    .ex-formula { font-family: var(--mono); font-size: var(--t-s); color: var(--ink2); white-space: nowrap; }
    .ex-formula b { color: var(--rub); font-weight: 500; margin-right: 6px; }
    .ex-insp-head { display: flex; flex-direction: column; gap: 6px; margin-bottom: var(--sp-3); }
    .ex-insp-head h3 { margin: 0; font-size: var(--t-l); line-height: 1.35; font-weight: 600; overflow-wrap: anywhere; }
    .ex-gate-card { background: var(--e2); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow); padding: var(--sp-4); display: flex; flex-direction: column; gap: 10px; margin-bottom: var(--sp-4); }
    .ex-gate-card .micro { font-size: var(--t-min); font-weight: 500; color: var(--ink2); }
    /* P68 : une carte d'une seule ligne n'empile plus les marges de blocs vides. */
    .ex-gate-card > :empty { display: none; }
    .ex-gate-card p { margin: 0; }
    .ex-prio-row > select { flex: 1 1 auto; width: auto; min-width: 0; }
    .ex-prio-row > .lbl { flex: none; white-space: nowrap; }
    .ex-gate-card .micro strong { color: var(--ink); }
    .ex-conds { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
    .ex-cond { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: var(--sp-2); padding: 6px 0; font-size: var(--t-s); }
    .ex-cond-strong { font-weight: 600; }
    .ex-cond-why { display: block; color: var(--ink2); font-size: var(--t-min); }
    .ex-cond-remedy { color: var(--ink); }
    .ex-gates { display: flex; flex-direction: column; }
    .ex-gates-sub { padding-top: 8px; border-top: 1px solid var(--line); margin-top: 4px; }
    .ex-last-refusal { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: var(--sp-2); padding: 6px 8px; margin-bottom: 4px; border-radius: var(--r); background: var(--bad-soft); color: var(--bad); font-size: var(--t-min); }
    .ex-cond > .sico { align-self: start; margin-top: 1px; }
    .ex-last-refusal > .sico { align-self: start; }
    /* Les gestes sous les gates, en une rangée comme l'artboard : un seul primaire (le passage suivant). */
    .ex-moves { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; }
    .ex-moves .ex-gate-row { border: 0; padding: 0; }
    .ex-moves .ex-gate-row[data-target="blocked"] { flex: 1 1 100%; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 8px; padding-top: 8px; border-top: 1px solid var(--line); border-radius: 0; }
    .ex-moves .ex-gate-row[data-target="blocked"] .input { flex: 1 1 160px; min-width: 0; }
    .ex-confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--r); background: var(--hover); }
    .ex-confirm .lbl { flex: 1 1 100%; margin: 0; }
    .ex-insp-idrow > .badge { margin-left: auto; }
    .ex-kv { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); font-size: var(--t-s); overflow-wrap: anywhere; }
    .ex-event { font-size: var(--t-min); color: var(--ink2); padding: 2px 0; }
    .ex-card-meta { font-size: var(--t-s); color: var(--ink2); display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
    /* Sur la carte (artboard Travail), une ligne par fait : rien ne se casse au
       milieu d'un mot ou d'un identifiant — ce qui déborde s'élide, le texte
       entier reste au survol. */
    .ex-card .ex-card-meta { flex-wrap: nowrap; white-space: nowrap; overflow: hidden; }
    .ex-card .ex-card-meta > * { flex: none; }
    /* P69v2 : la dernière entrée du fil d'activité — une seule ligne, élidée. */
    .ex-card-feed { display: flex; align-items: center; gap: 4px; min-width: 0; }
    .ex-card-feed .ex-card-feed-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
    /* P74 : la ligne « ouverte par … · dans <colonne> depuis … » — mêmes règles
       de tenue qu'une méta de carte (une ligne, élision, texte entier au survol). */
    .ex-card-who { font-size: var(--t-s); color: var(--ink2); display: flex; align-items: center; gap: 4px 6px; }
    .ex-card .ex-card-who { flex-wrap: nowrap; white-space: nowrap; overflow: hidden; }
    .ex-card .ex-card-who > * { flex: none; }
    .ex-card .ex-card-who > .ex-card-owner { flex: 0 1 auto; min-width: 14ch; overflow: hidden; text-overflow: ellipsis; }
    .ex-card .ex-card-who > .ex-card-age { flex: 0 3 auto; min-width: 8ch; overflow: hidden; text-overflow: ellipsis; }
    .ex-card-flags { display: flex; flex-wrap: wrap; gap: 4px; }
    .ex-card .ex-session { flex-wrap: nowrap; white-space: nowrap; overflow: hidden; color: var(--ink2); font-family: inherit; font-size: var(--t-min); }
    .ex-card .ex-session > * { flex: none; }
    .ex-card .ex-session code { border: 0; background: none; padding: 0; color: var(--ink); word-break: normal; }
    .ex-card .ex-session .ex-session-lbl { display: none; }
    .ex-card .ex-facts > * { white-space: nowrap; }
    .ex-card .ex-wait { flex-wrap: nowrap; white-space: nowrap; overflow: hidden; min-width: 0; }
    .ex-card .ex-wait > * { flex: none; }
    .ex-card .ex-wait > .lbl { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .ex-card .ex-wait.flag > span:last-child { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .ex-card-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .ex-card-next { font-size: var(--t-min); color: var(--ink2); display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px; border-top: 1px solid var(--line); padding-top: 6px; }
    /* P59 : un libellé d'action ne se casse jamais ; faute de place, ce sont les boutons qui passent à la ligne. */
    .ex-card-next > span:first-child, .ex-card-next .btn { white-space: nowrap; }
    .ex-card-next > .row { flex-wrap: wrap; justify-content: flex-end; gap: 4px; margin-left: auto; }
    .ex-card-next .btn { height: 24px; min-height: 24px; padding: 0 10px; font-size: var(--t-min); }
    .ex-card-next .row-act, .ex-card-next .more-btn { width: 24px; min-width: 24px; padding: 0; }
    .ex-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 10px; font-size: var(--t-min); }
    .ex-cost { font-size: var(--t-min); }
    .ex-presence { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; font-size: var(--t-s); color: var(--ink2); }
    .ex-presence .ex-presence-word { font-weight: 600; color: var(--ink); }
    .ex-presence.acc .glyph { color: var(--acc); }
    .ex-presence.warn .glyph { color: var(--warn); }
    .ex-presence-sub { flex: 1 0 100%; font-size: var(--t-min); color: var(--ink2); padding-left: 22px; }
    .ex-presence-act { flex: 1 0 100%; padding-left: 22px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px 12px; font-size: var(--t-min); }
    .ex-presence-act .btn { margin-left: auto; }
    .ex-presence-act .btn { height: 24px; min-height: 24px; padding: 0 10px; font-size: var(--t-min); }
    .ex-presence-filter { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .ex-presence-filter .chip[aria-pressed="true"] { background: var(--acc-soft); border-color: var(--acc); color: var(--ink); }
    .ex-resume-group { display: flex; flex-direction: column; gap: var(--sp-2); border-left: 2px solid var(--warn); padding-left: var(--sp-2); }
    .ex-col-count-resume { font-size: var(--t-min); color: var(--warn); background: var(--warn-soft); font-weight: 600; margin-left: 6px; padding: 2px 8px; border-radius: 999px; }
    .ex-card[data-presence="resume"] { border-color: var(--warn); }
    .ex-queue-title { font-size: var(--t-min); font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--ink2); margin-top: var(--sp-2); }
    .ex-resume-title { display: flex; align-items: center; gap: 6px; font-size: var(--t-min); font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--warn); }
    .ex-list-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); }
    .ex-list { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .ex-list th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); background: var(--bar); }
    .ex-list td { padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); }
    .ex-list tbody tr { cursor: pointer; }
    .ex-list tbody tr:hover { background: var(--e2); }
    .ex-list tbody tr[aria-current="true"] { background: var(--acc-soft); }
    .ex-list tbody tr:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
    .ex-list .ex-list-actions { width: 1%; white-space: nowrap; text-align: right; }
    .ex-timeline { display: flex; flex-direction: column; gap: 6px; padding: var(--sp-2) 0; }
    .ex-tl-filters { display: flex; gap: var(--sp-2); align-items: center; padding-bottom: var(--sp-2); }
    .ex-tl-filters select { font-size: var(--t-min); }
    .ex-tl-count { font-size: var(--t-min); color: var(--ink3); margin-left: auto; }
    .ex-tl-entry { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); cursor: pointer; }
    .ex-tl-entry:hover { background: var(--e2); }
    .ex-tl-entry.fail { border-color: var(--bad); }
    .ex-tl-row { display: flex; gap: var(--sp-3); align-items: flex-start; min-width: 0; }
    /* P68 : la date lisible tient dans sa colonne (largeur au contenu, jamais fixe) ; l'ISO complet est au survol. */
    .ex-tl-at { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: var(--t-min); color: var(--ink3); flex: none; min-width: 16ch; white-space: nowrap; }
    .ex-tl-body { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
    .ex-tl-detail { margin: 0; padding: var(--sp-2) var(--sp-3); border-top: 1px solid var(--line); background: var(--e2); font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap; word-break: break-word; }
    .ex-insp-block { margin-bottom: var(--sp-4); }
    .ex-insp-block h4 { font-size: var(--t-min); color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
    .ex-insp-block ul { margin: 0; padding-left: 18px; font-size: var(--t-s); }
    .ex-recall { margin: 0; padding: var(--sp-2) var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap; word-break: break-word; }
    .ex-gate-row { display: flex; flex-direction: column; gap: 4px; padding: 8px; border: 1px solid var(--line); border-radius: var(--r); }
    .ex-gate-req { font-size: var(--t-min); color: var(--ink3); }
    .ex-session { font-family: var(--mono); font-size: var(--t-min); color: var(--ink2); display: flex; align-items: center; gap: 6px; }
    .ex-session code { font-family: var(--mono); font-size: var(--t-min); padding: 1px 4px; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); word-break: break-all; }
    .ex-refusal { color: var(--bad); font-size: var(--t-s); margin-top: 6px; }
    .ex-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-3); font-size: var(--t-s); color: var(--ink2); }
    .ex-toolbar label { display: inline-flex; align-items: center; gap: 6px; }
    .ex-toolbar select, .ex-toolbar input[type="checkbox"] { font-size: var(--t-s); }
    .ex-toolbar .ex-count { margin-left: auto; color: var(--ink3); font-size: var(--t-min); }
    .ex-notice { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-size: var(--t-s); }
    .ex-card-project { font-size: var(--t-s); color: var(--ink2); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .ex-card-project .chip { font-size: var(--t-s); }
    .ex-session-cmd { margin: 6px 0 0; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); font-family: var(--mono); font-size: var(--t-s); white-space: pre-wrap; word-break: break-all; }
    .ex-directive { display: flex; flex-direction: column; gap: 2px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); margin-bottom: 6px; font-size: var(--t-s); }
    .ex-directive.unread { border-color: var(--acc); }
    .ex-directive-meta { font-size: var(--t-min); color: var(--ink3); display: flex; gap: 6px; align-items: center; }
    .ex-steer-form { display: flex; flex-direction: column; gap: 6px; }
    .ex-steer-form textarea { min-height: 56px; font: inherit; font-size: var(--t-s); }
    .ex-steer-row { display: flex; gap: 6px; align-items: center; }
    .ex-wait { font-size: var(--t-s); color: var(--ink2); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .ex-wait.flag { padding: 2px 8px; border-radius: var(--r); }
    .ex-wait.flag.bad { background: var(--bad-soft); color: var(--bad); }
    .ex-wait.flag.warn { background: var(--warn-soft); color: var(--warn); }
    .ex-wait.human { color: var(--ink); font-weight: 500; }
    .ex-banner { display: flex; align-items: center; gap: var(--sp-3); padding: 8px var(--sp-3); border: 1px solid var(--warn); border-radius: var(--r); background: var(--e1); font-size: var(--t-s); }
    .ex-banner > span:not(.glyph) { flex: 1 1 auto; min-width: 0; }
    .ex-banner > .btn { flex: none; white-space: nowrap; }
    /* Décisions (P68) : une colonne à la largeur utile, une carte par type, une seule action primaire. */
    .ex-decisions { display: flex; flex-direction: column; gap: var(--sp-4); width: 100%; max-width: 960px; }
    .ex-dec-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); }
    .ex-dec-filter { display: flex; flex-wrap: wrap; gap: 6px; }
    .ex-dec-filter .chip[aria-pressed="true"] { background: var(--acc-soft); border-color: var(--acc); color: var(--acc); }
    .ex-dec-n { font-variant-numeric: tabular-nums; font-weight: 600; }
    .ex-dec-section { display: flex; flex-direction: column; gap: var(--sp-3); }
    .ex-dec-section[hidden] { display: none; }
    .ex-dec-section-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2) var(--sp-3); min-height: var(--h-ctl); }
    .ex-dec-section-head h3 { margin: 0; display: inline-flex; align-items: center; gap: 8px; font-size: var(--t-s); font-weight: 600; color: var(--ink2); }
    .ex-dec-section-head h3 .ex-dec-n { color: var(--ink3); font-weight: 500; }
    .ex-dec-bulk { margin-left: auto; display: flex; flex-wrap: wrap; gap: var(--sp-2); }
    .ex-dec-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-3); }
    .ex-dec-refusals { margin: 6px 0 0; padding-left: 18px; }
    .ex-decision { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); min-width: 0; }
    .ex-decision[data-type="reprise"] { gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); }
    .ex-decision-head { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .ex-decision-title { margin: 0; font-size: var(--t-l); font-weight: 600; line-height: 1.35; color: var(--ink); overflow-wrap: anywhere; }
    .ex-decision-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .ex-decision-chips .chip { cursor: default; }
    .ex-decision-question { margin: 0; font-size: var(--t-s); line-height: 1.5; color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere; }
    .ex-choices { display: flex; flex-direction: column; gap: 6px; }
    .ex-choice { display: flex; align-items: flex-start; gap: 10px; width: 100%; min-height: var(--h-ctl); padding: 7px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); color: var(--ink); font: inherit; font-size: var(--t-s); line-height: 1.45; text-align: left; white-space: normal; overflow-wrap: anywhere; cursor: pointer; }
    .ex-choice:hover { background: var(--hover); }
    .ex-choice[aria-pressed="true"] { background: var(--acc-soft); border-color: var(--acc); box-shadow: inset 0 0 0 1px var(--acc); }
    .ex-choice:disabled { color: var(--ink3); border-style: dashed; cursor: not-allowed; }
    .ex-choice-mark { flex: none; width: 14px; height: 14px; margin-top: 3px; border: 2px solid var(--ink3); border-radius: 50%; }
    .ex-choice[aria-pressed="true"] .ex-choice-mark { border-color: var(--acc); background: var(--acc); box-shadow: inset 0 0 0 2px var(--card); }
    .ex-dec-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); }
    .ex-dec-free { display: flex; flex-direction: column; gap: var(--sp-2); }
    .ex-dec-free[hidden] { display: none; }
    .ex-decision textarea { width: 100%; height: auto; min-height: 64px; padding: 6px 9px; font: inherit; font-size: var(--t-s); resize: vertical; }
    .ex-dec-more { font-size: var(--t-min); color: var(--ink2); }
    .ex-dec-more > summary { width: fit-content; cursor: pointer; color: var(--ink2); padding: 2px 0; }
    .ex-dec-more[open] > summary { margin-bottom: var(--sp-2); }
    .ex-dec-more dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px var(--sp-3); margin: 0; padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .ex-dec-more dt { color: var(--ink3); }
    .ex-dec-more dd { margin: 0; color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; }
    .ex-dec-more code { font-family: var(--mono); font-size: var(--t-min); word-break: break-all; }
    .ex-dec-cmd { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; white-space: normal; }
    .ex-decision-note { font-size: var(--t-min); color: var(--ink3); }
    .ex-decision-item { display: flex; flex-direction: column; gap: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
    /* P47 : panneau Assigner une session — plan de lancement en lecture seule, section de l'inspecteur. */
    .ex-assign-panel { display: flex; flex-direction: column; gap: var(--sp-3); margin-top: var(--sp-2); }
    .ex-assign-panel h4 { font-size: var(--t-s); color: var(--ink); margin: 0; font-weight: 600; }
    .ex-assign-sub { margin: 0; font-size: var(--t-s); color: var(--ink2); }
    .ex-assign-notready { display: flex; align-items: flex-start; gap: 6px; padding: 6px var(--sp-3); border: 1px solid var(--warn); border-radius: var(--r); background: var(--warn-soft); color: var(--warn); font-size: var(--t-s); }
    .ex-assign-panel .skeleton { padding: var(--sp-2) 0; }
    .ex-assign-rows { display: flex; flex-direction: column; }
    .ex-assign-verifs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .ex-assign-verif { display: flex; align-items: flex-start; gap: 8px; font-size: var(--t-s); }
    .ex-assign-verif > .sico { align-self: start; margin-top: 1px; }
    .ex-assign-verif > span:not(.sico) { display: flex; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
    .ex-assign-launch { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
    .ex-assign-cmd { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
    .ex-assign-cmd code { font-family: var(--mono); font-size: var(--t-min); padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); word-break: break-all; }
    @container canvas (max-width: 900px) {
      .ex-dec-bulk { margin-left: 0; width: 100%; }
      .ex-decision { padding: var(--sp-3); }
      .ex-dec-more dl { grid-template-columns: minmax(0, 1fr); }
    }
  `;
  document.head.append(style);
}

// Le glyphe d'état ne dit plus qu'un ÉTAT (colonne d'une tâche, échec d'une
// ligne de timeline, ledger absent) : les catégories ont leur icône, la
// priorité ses barres, le projet son monogramme (P30 ; garde :
// tests/unit/test_workspace_polysemy.py).
const dot = (cls) => stateIcon(cls);
function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}
function row(...children) { const d = document.createElement('div'); d.className = 'row'; d.append(...children); return d; }
// Une preuve attendue : le sceau dit « preuve », le mot dit laquelle.
function chip(label) {
  const c = document.createElement('span');
  c.className = 'chip';
  c.append(glyph('seal', 'preuve attendue'), text('span', null, label));
  return c;
}

// Le nom court d'un hôte (grimoire.bridges.schemas.HostId) — jamais deviné :
// une valeur inconnue s'affiche telle quelle.
const HOST_LABEL = {
  'host-claude-code-cli': 'Claude Code', 'host-github-copilot': 'Copilot',
  'host-codex': 'Codex', 'host-cursor': 'Cursor', 'host-gemini-cli': 'Gemini CLI',
};
// Pictogramme générique de l'hôte, jamais un logo de marque : la forme dit le
// genre d'outil (terminal, éditeur), le nom reste en texte.
const HOST_ICON = {
  'host-claude-code-cli': 'i-host-cli', 'host-codex': 'i-host-cli', 'host-gemini-cli': 'i-host-cli',
  'host-opencode': 'i-host-cli', 'host-github-copilot': 'i-host-ide', 'host-cursor': 'i-host-caret',
};

// Une ligne « session » : identifiant (tronqué sur la carte, entier dans
// l'inspecteur) + hôte. `resume_command` n'existe que pour un hôte dont le
// kit connaît la commande (`grimoire.missions.session_link.RESUME_COMMANDS`) ;
// pour les autres, l'identifiant seul — rien n'est inventé côté vue.
function sessionLine(task, { full = false } = {}) {
  const line = document.createElement('div');
  line.className = 'ex-session';
  const id = full ? task.session_id : `${task.session_id.slice(0, 8)}…`;
  line.append(text('span', 'lbl ex-session-lbl', 'session'), Object.assign(document.createElement('code'), { textContent: id, title: task.session_id }));
  if (task.host) {
    if (HOST_ICON[task.host]) line.append(glyph(HOST_ICON[task.host], 'hôte'));
    line.append(text('span', 'lbl', HOST_LABEL[task.host] || task.host));
  }
  return line;
}

// ── P74 : lisibilité humaine des cartes ─────────────────────────────────────

// « Ouverte par » : qui a ouvert la tâche (champ `owner` du ledger), pas qui
// travaille dessus. Humain ou agent : déduit du nom (aucun champ ne le dit),
// le `title` l'assume. Icônes du sprite : `user` (personne), `agent` (agent).
const OWNER_AGENT = /\b(agent|bot|claude|copilot|codex|cursor|gemini|ci|kit)\b|[-_](dev|bot|agent)\b/i;
const ownerIsAgent = (owner) => OWNER_AGENT.test(String(owner || ''));
function ownerNode(task) {
  const node = document.createElement('span');
  node.className = 'ex-card-owner';
  node.dataset.role = 'owner';
  const owner = task.owner || '';
  const agent = ownerIsAgent(owner);
  node.dataset.ownerKind = owner ? (agent ? 'agent' : 'humain') : 'inconnu';
  node.title = owner
    ? `ouverte par ${owner} — qui a ouvert la tâche, pas qui travaille dessus (${agent ? 'agent' : 'humain'}, déduit du nom)`
    : 'qui a ouvert la tâche, pas qui travaille dessus — personne n’est nommé';
  node.append(glyph(agent ? 'agent' : 'user', agent ? 'ouverte par un agent' : 'ouverte par un humain'));
  node.append(text('span', null, owner ? `ouverte par ${owner}` : 'ouverte par — non renseigné'));
  return node;
}

// Couleur stable par « ouverte par » : hash du nom → un des six tokens
// --owner-N (tokens.css, dérivés des teintes d'identité). Un liseré de
// balayage, jamais le seul porteur de sens (le nom est écrit sur la carte).
function ownerTone(owner) {
  const value = String(owner || '');
  if (!value) return '';
  let hash = 0;
  for (const ch of value) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return `var(--owner-${(hash % 6) + 1})`;
}

// Type lisible — code / design / doc / infra / autre. D'abord le `type`
// déclaré au ledger quand il tranche seul (documentation, migration,
// opération, sécurité) ; sinon une heuristique honnête sur titre + critères
// (mots-clés), dite telle quelle dans le `title` (« type déduit du contenu »).
const TYPE_DECLARED_CATEGORY = {
  documentation: 'doc', migration: 'infra', operation: 'infra', security: 'infra', test: 'code',
};
const TYPE_FALLBACK_CATEGORY = { implementation: 'code', cleanup: 'code', architecture: 'code', analysis: 'autre' };
const CATEGORY_HINTS = [
  ['design', /\b(maquette|design|artboard|ui|ux|visuel|écran|ecran|css|th[eè]me|style)\b/i],
  ['doc', /\b(doc|docs|documentation|documenter|readme|guide|changelog|glossaire)\b/i],
  ['infra', /\b(ci|cd|pipeline|docker|déploi|deploi|release|infra|serveur|hook|workflow|qdrant)\b/i],
  ['code', /\b(bug|fix|test|refactor|impl[ée]ment|module|api|fonction|commande)\b/i],
];
function taskCategory(task) {
  const declared = TYPE_DECLARED_CATEGORY[task.type];
  if (declared) return { category: declared, why: `type déclaré au ledger (${TYPE_LABEL[task.type] || task.type})` };
  const content = [task.title || '', ...(task.acceptance || [])].join(' ');
  for (const [category, pattern] of CATEGORY_HINTS) {
    if (pattern.test(content)) return { category, why: 'type déduit du contenu (titre et critères)' };
  }
  const fallback = TYPE_FALLBACK_CATEGORY[task.type];
  if (fallback) return { category: fallback, why: `type déclaré au ledger (${TYPE_LABEL[task.type] || task.type})` };
  return { category: 'autre', why: 'type déduit du contenu (aucun indice : autre)' };
}
function typeBadge(task) {
  const { category, why } = taskCategory(task);
  const node = document.createElement('span');
  node.className = 'badge ex-type';
  node.dataset.role = 'type-badge';
  node.dataset.cat = category;
  node.title = why;
  const swatch = document.createElement('i');
  swatch.className = 'ex-type-sw';
  swatch.style.setProperty('--type-tone', `var(--type-${category})`);
  swatch.setAttribute('aria-hidden', 'true');
  node.append(swatch, text('span', null, category));
  return node;
}

// La session sur la CARTE, sans identifiant brut (l'UUID ne dit rien à un
// humain : il reste dans l'inspecteur, avec la commande de reprise). À la
// place : hôte, persona du claim (`agent_id` du sous-agent, sinon `actor_id`)
// et palier de modèle recommandé — seulement quand ils sont connus, rien
// n'est inventé (« hôte inconnu » plutôt qu'un vide).
function cardSessionLine(task) {
  const line = document.createElement('div');
  line.className = 'ex-session';
  line.dataset.role = 'card-session';
  line.title = "la session qui tient cette tâche — l'identifiant et la commande de reprise sont dans le détail";
  if (task.host) {
    if (HOST_ICON[task.host]) line.append(glyph(HOST_ICON[task.host], 'hôte'));
    line.append(text('span', 'lbl', HOST_LABEL[task.host] || task.host));
  }
  const claim = task.claim || {};
  const persona = claim.agent_id || claim.actor_id || '';
  if (persona) {
    const who = text('span', null, persona);
    who.title = 'persona du claim : qui tient la tâche dans la session';
    line.append(who);
  }
  // P75 : le palier RÉEL lu du journal de session quand il est rapporté ; le
  // palier recommandé (route) en repli ; sinon le dire — rien n'est inventé.
  const work = (task.work && task.work.session) || null;
  const model = document.createElement('span');
  model.dataset.role = 'work-model';
  if (work && (work.tier || work.model)) {
    model.textContent = work.tier || work.model;
    model.title = work.tier
      ? `palier réel, lu du journal de session${work.model ? ` (${work.model})` : ''}`
      : 'modèle lu du journal de session (hors paliers haiku/sonnet/opus)';
  } else if (!work && task.model_tier && task.model_tier !== 'session') {
    // « session » n'est pas un palier : le badge de route dit déjà « modèle de session ».
    model.className = 'lbl';
    model.textContent = `palier ${task.model_tier}`;
  }
  if (model.textContent) line.append(model);
  return line;
}

// P75 : la ligne de travail d'une carte tenue — effort, tokens en direct,
// derniers skills. Sa propre ligne : la ligne session est en nowrap/élision,
// ces faits y disparaissaient. Rien n'est inventé : un effort absent se dit.
function workLiveRow(task) {
  const work = (task.work && task.work.session) || null;
  if (!work) return null;
  const line = document.createElement('div');
  line.className = 'ex-facts lbl';
  line.dataset.role = 'work-live';
  // P91 : un fait absent ne s'écrit pas sur la carte (« modèle non rapporté »,
  // « effort non rapporté » restent dans l'inspecteur) ; un fait connu s'affiche.
  if (work.effort) {
    const effort = text('span', null, `effort ${work.effort}`);
    effort.dataset.role = 'work-effort';
    effort.title = 'effort de raisonnement, lu du journal de session';
    line.append(effort);
  }
  const usage = (task.work && task.work.usage) || null;
  if (usage && usage.usage_source === 'claude-code-transcript') {
    const tokens = text('span', null, tokensText(usage.total_tokens || 0));
    tokens.dataset.role = 'work-tokens';
    tokens.title = `${usage.input_tokens} in / ${usage.output_tokens} out, cache ${usage.cache_read_tokens} lu / ${usage.cache_creation_tokens} écrit — repliés des traces (grimoire task fold-usage)`;
    line.append(tokens);
  }
  if (work.skills && work.skills.length) {
    const last = work.skills.slice(-2);
    const skills = text('span', null, last.join(' · '));
    skills.dataset.role = 'work-skills';
    skills.title = `derniers skills invoqués par la session (liste complète dans le détail) : ${work.skills.join(', ')}`;
    line.append(skills);
  }
  return line.childElementCount ? line : null;
}

// P75 : tokens compacts pour une carte — « 48,2 k tokens » au-delà du millier.
function tokensText(total) {
  if (total < 1000) return `${total} tokens`;
  const k = total / 1000;
  const rounded = k >= 100 ? String(Math.round(k)) : (Math.round(k * 10) / 10).toFixed(1).replace(/\.0$/, '');
  return `${rounded.replace('.', ',')} k tokens`;
}

// Montant CAD : virgule décimale, l'unité écrite — jamais un symbole seul.
function cadText(cad) {
  return `${cad.toFixed(2).replace('.', ',')} $ CAD`;
}

// Statut d'usage en toutes lettres quand ce n'est pas le journal de l'hôte (P75).
const USAGE_SOURCE_WORDS = {
  none: 'aucun usage replié depuis le journal de l’hôte (none)',
  unavailable: "l'hôte de la tâche n'expose pas de journal d'usage vérifié (unavailable)",
  reported: "coût rapporté par l'ouvrier seulement (reported)",
};

// P75 : fin de parcours — total de tokens repliés et coût approximatif en CAD
// au taux des options du projet ; « coût inconnu » quand rien n'est replié.
function workTotalRow(work) {
  const usage = work.usage || {};
  const line = text('div', 'ex-cost lbl', '');
  line.dataset.role = 'work-total';
  line.dataset.usageSource = usage.usage_source || 'none';
  const parts = [];
  if (usage.usage_source === 'claude-code-transcript') {
    if (usage.calls) parts.push(`${usage.calls} appel${usage.calls > 1 ? 's' : ''}`);
    parts.push(`total ${tokensText(usage.total_tokens || 0)}`);
    if (work.cost_cad != null) parts.push(`${usage.cost_status === 'lower_bound' ? 'au moins ' : '≈ '}${cadText(work.cost_cad)}`);
    else parts.push('coût inconnu');
  } else {
    // Un journal absent ou purgé n'est pas « zéro » : le serveur le dit (usage_note).
    parts.push(work.usage_note || 'coût inconnu');
  }
  line.textContent = parts.join(' · ');
  const source = USAGE_SOURCE_WORDS[usage.usage_source];
  line.title = `${source ? source + '. ' : ''}Taux : 1 USD = ${String(work.usd_cad).replace('.', ',')} $ CAD `
    + `(${work.usd_cad_source === 'default' ? 'défaut du kit' : 'déclaré par le projet'}, modifiable dans Configuration → Fournisseurs et budgets).`;
  return line;
}

// Les deux temps d'une carte, nommés (P74) : l'âge dans la colonne courante
// (depuis la dernière transition, `in_state_since` servi par le serveur) sur
// la carte ; l'âge total (depuis l'ouverture) au survol et dans l'inspecteur —
// « depuis N h » tout seul ne disait pas de quoi.
function cardAge(task) {
  const inColumn = ageText(task.in_state_since || '');
  if (!inColumn) return null;
  const node = text('span', 'ex-card-age', `dans ${COLUMN_LABEL[task.board] || task.board} ${inColumn}`);
  node.dataset.role = 'card-age';
  const total = ageText(task.created_at || '');
  node.title = `Dans la colonne « ${COLUMN_LABEL[task.board] || task.board} » ${inColumn} (dernière transition).`
    + (total ? ` Ouverte ${total} (âge total).` : '');
  return node;
}

// La suite d'une carte, lue sur le graphe réel du ledger
// (`grimoire.missions.ledger._TASK_TRANSITIONS`) projeté sur le board — jamais
// la colonne suivante de `LIFECYCLE`, qui faisait de « Bloquée » la suite de
// toute carte en cours. Bloquer n'est pas une étape : c'est un geste à part,
// qui demande une raison.
const FORWARD = {
  proposed: 'ready', ready: 'in_progress', in_progress: 'review',
  blocked: 'ready', review: 'accepted', accepted: 'released',
};
// READY, CLAIMED et RUNNING peuvent passer à BLOCKED ; rien d'autre.
const BLOCKABLE = new Set(['ready', 'in_progress']);
function nextMoves(task) {
  // Une attente nommée (K6) repart en cours (WAITING → RUNNING), pas en « Prête ».
  const forward = task.board === 'blocked' && task.status === 'waiting' ? 'in_progress' : (FORWARD[task.board] || null);
  return { forward, canBlock: BLOCKABLE.has(task.board) };
}
function nextMovesText(task, none) {
  const { forward, canBlock } = nextMoves(task);
  const parts = [];
  if (forward) parts.push(`→ ${COLUMN_LABEL[forward]}`);
  if (canBlock) parts.push('Bloquer…');
  return parts.length ? parts.join(' · ') : none;
}
// « Bloquer… » depuis une carte : l'inspecteur qui s'ouvre met le curseur dans
// le champ de raison (posé par le clic, consommé par `renderInspector`).
let focusBlockReason = false;

// Priorité effective (lot B la calcule côté serveur : `effective_priority`) — barres + mot.
const PORTFOLIO_PRIORITY_LABEL = { low: 'priorité basse', medium: 'priorité moyenne', high: 'priorité haute', critical: 'priorité critique' };

// La ligne « portefeuille » d'une carte : projet (monogramme + nom), session
// (glyphe de vivacité + mot), priorité (barres + mot), messages en attente
// (bulle + compteur) — une forme par sens, jamais la couleur seule (P30).
// P74 : une tâche close n'affiche plus de vivacité ici non plus.
function portfolioRow(task) {
  const line = document.createElement('div');
  line.className = 'ex-card-project';
  const project = document.createElement('span');
  project.className = 'chip';
  const projectName = task.project?.name || task.project?.slug || 'projet';
  project.append(monogram(projectName), text('span', null, projectName));
  line.append(project);
  if (!isClosed(task)) {
    const session = task.session;
    if (session && session.id) {
      line.append(row(sessionGlyph(session), text('span', null, `session ${livenessLabel(session)}`)));
    } else {
      line.append(text('span', 'lbl', 'aucune session'));
    }
  }
  if (task.priority) {
    line.append(row(priorityBars(task.priority) || '', text('span', null, PORTFOLIO_PRIORITY_LABEL[task.priority] || `priorité ${task.priority}`)));
  }
  if (task.unread_directives) {
    const pending = row(glyph('chat', 'messages'), text('span', null, `${task.unread_directives} message(s) en attente de la session`));
    pending.title = 'Consigne posée au ledger, pas encore accusée par la session qui tient la tâche.';
    line.append(pending);
  }
  return line;
}

// Dernier signe de vie, durée et coût d'une session / d'une carte (UI-3). Le
// temps relatif est un <time data-since> : le minuteur de la coque le tient à
// jour entre deux sondages. Un coût que personne n'a mesuré se lit « inconnu »,
// jamais « 0 » (W1-01) ; la portée du chiffre (dispatch) est écrite, parce que
// le coût de la session hôte n'est mesuré nulle part.
function sinceNode(iso) {
  const node = document.createElement('time');
  node.dateTime = iso;
  node.dataset.since = iso;
  node.title = iso;
  node.textContent = agoText(Date.now() - Date.parse(iso));
  return node;
}

// `compact` (carte) : la raison d'une inactivité (« sonde de l'hôte : au
// repos ») passe au survol — sur la carte, elle cassait la ligne en deux.
function liveRow(session, { withState = true, compact = false } = {}) {
  const line = document.createElement('div');
  line.className = 'ex-facts lbl';
  line.dataset.role = 'live-facts';
  if (withState) {
    const state = row(sessionGlyph(session), text('span', null, `session ${livenessLabel(session, { reason: !compact })}`));
    state.title = `session ${livenessLabel(session)}`;
    line.append(state);
  }
  if (session.updated_at && !Number.isNaN(Date.parse(session.updated_at))) {
    const sign = text('span', null, 'signe ');
    sign.append(sinceNode(session.updated_at));
    line.append(sign);
  } else {
    line.append(text('span', null, 'aucun signe de vie lu'));
  }
  line.append(text('span', null, `durée ${durationText(session.duration_s)}`));
  return line;
}

// P11 : le coût est sur chaque carte, backlog compris — c'est avant de lancer
// une tâche qu'on veut savoir qu'aucun chiffre n'existe. Inconnu se lit
// « coût inconnu », jamais « 0 » (W1-01, #709).
function costLabel(cost) {
  const unpriced = cost && cost.unpriced_calls ? `, ${cost.unpriced_calls} non chiffré(s)` : '';
  const text_ = costText(cost);
  if (text_ === 'inconnu') return unpriced ? `coût inconnu${unpriced}` : 'coût inconnu';
  return `coût ${text_} (dispatch${unpriced})`;
}
function costRow(task) {
  const line = text('div', 'ex-cost lbl', costLabel(task.cost));
  line.dataset.role = 'cost';
  line.dataset.costStatus = (task.cost && task.cost.status) || 'unknown';
  line.title = task.cost && task.cost.calls
    ? `${task.cost.calls} tentative(s) de dispatch. Le coût de la session hôte n'est pas mesuré.`
    : "Aucun dispatch : le kit n'a rien mesuré. Le coût de la session hôte n'est pas mesuré non plus.";
  return line;
}

// Classe de route, modèle recommandé (P11) : le même calcul que `grimoire task
// dispatch` (`dispatch_advice`). V2 n'a pas de nom de modèle (`recommended_model:
// null`) : la consigne est de garder celui de la session, écrite telle quelle.
function modelLabel(task) {
  return task.recommended_model || 'modèle de session';
}
function routeText(task) {
  return `${task.verifiability_class} · ${modelLabel(task)}`;
}
function routeTitle(task) {
  const model = task.recommended_model
    ? `modèle recommandé : ${task.recommended_model}`
    : 'aucun modèle recommandé : garde celui de la session';
  return `classe de route ${task.verifiability_class} · ${model}${task.model_tier ? ` (palier ${task.model_tier})` : ''}`;
}
function routeBadge(task) {
  if (!task.verifiability_class) return null;
  const node = badge(routeText(task), '', routeTitle(task));
  node.dataset.role = 'route';
  node.dataset.class = task.verifiability_class;
  return node;
}

// L'anneau de preuve : un segment par condition du prochain passage (le nombre
// vient du fichier de gates, pas d'un « 3 » fixe), plein quand le gate l'accepte.
// Une condition non remplie est grise, rouge seulement pour une carte en revue
// (c'est elle qui attend un verdict). Rien n'est dessiné sans `next_gate`.
const SVG_NS = 'http://www.w3.org/2000/svg';
function proofRing(task) {
  const gate = task.next_gate;
  if (!gate || !(gate.conditions || []).length) return null;
  const n = gate.conditions.length;
  const done = gate.conditions.filter((c) => c.ok).length;
  const circumference = 2 * Math.PI * 7;
  const slot = circumference / n;
  const length = n === 1 ? circumference : slot - 2.2;
  const wrap = document.createElement('span');
  wrap.className = 'ex-proof';
  wrap.dataset.role = 'proof';
  wrap.title = `conditions de passage vers ${COLUMN_LABEL[gate.to] || gate.to} : ${done} sur ${n}`;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 18 18');
  svg.setAttribute('class', 'ex-ring');
  svg.setAttribute('aria-hidden', 'true');
  gate.conditions.forEach((c, i) => {
    const arc = document.createElementNS(SVG_NS, 'circle');
    arc.setAttribute('cx', '9');
    arc.setAttribute('cy', '9');
    arc.setAttribute('r', '7');
    arc.setAttribute('class', c.ok ? 'ok' : (task.board === 'review' ? 'bad' : 'todo'));
    arc.setAttribute('stroke-dasharray', `${length.toFixed(2)} ${(circumference - length).toFixed(2)}`);
    arc.setAttribute('stroke-dashoffset', (-i * slot).toFixed(2));
    svg.append(arc);
  });
  wrap.append(svg, text('span', null, `${done}/${n}`));
  return wrap;
}

/** Les actions secondaires d'une tâche (P56), sur sa carte comme sur sa ligne :
 *  révélées au survol et au focus, toutes dans le menu « … ». L'action
 *  principale reste le clic sur la carte (et Entrée) : ouvrir le détail. */
// P91 : UNE ligne d'état par carte ouverte — glyphe + mot + âge, lue du seul
// champ `task.presence` (P90) : la même vérité pour la forme, le mot et le temps.
// Une absence ne s'écrit pas ici (« aucun signe de vie lu », « durée inconnue »
// restent dans l'inspecteur). « Bloquée » est portée par les lignes d'attente.
function ageWords(iso) {
  const ms = Date.now() - Date.parse(iso);
  return Number.isFinite(ms) ? agoText(ms).replace(/^il y a /, '') : '';
}
function clockText(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}
function resumeNowButton(task) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn pri';
  btn.dataset.role = 'resume-now';
  btn.textContent = 'Reprendre maintenant';
  btn.title = `Copie la commande qui reprend la tâche sans attendre la prochaine tentative : grimoire task claim ${task.id} (la webview n'exécute rien, ADR-007).`;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    copyText(`grimoire task claim ${task.id}`, btn);
  });
  return btn;
}
function presenceLine(task) {
  const p = task.presence;
  if (!p || isClosed(task) || p.state === 'blocked') return null;
  const ui = PRESENCE_UI[p.state];
  if (!ui) return null;
  const line = document.createElement('div');
  line.className = 'ex-presence' + (ui.tone ? ` ${ui.tone}` : '');
  line.dataset.role = 'presence';
  line.dataset.state = p.state;
  line.append(glyph(ui.icon, ui.word, ui.tone), text('span', 'ex-presence-word', ui.word));
  const sub = [];
  if (p.state === 'working') {
    if (p.sign_at) line.append(text('span', null, '·'), sinceNode(p.sign_at));
  } else if (p.state === 'silent') {
    line.append(text('span', null, '·'), text('span', null, p.sign_at ? `rien depuis ${ageWords(p.sign_at)}` : (p.cause || 'sans signal d’hôte')));
    if (p.lease_left_s != null) sub.push(`reprise auto dans ${durationText(p.lease_left_s)} si ça continue`);
  } else if (p.state === 'resume') {
    if (p.attempt) line.append(text('span', null, '·'), text('span', null, `tentative ${p.attempt} sur ${RESUME_MAX_ATTEMPTS}`));
    if (p.cause) sub.push(p.cause);
  } else if (p.state === 'none' && p.queue_position != null) {
    line.append(text('span', null, '·'), text('span', null, `en file, position ${p.queue_position}`));
  }
  if (sub.length) line.append(text('div', 'ex-presence-sub', sub.join(' · ')));
  if (p.state === 'resume') {
    const act = document.createElement('div');
    act.className = 'ex-presence-act';
    act.append(text('span', null, p.retry_at && clockText(p.retry_at) ? `retentée vers ${clockText(p.retry_at)}` : 'dès qu’un créneau se libère'), resumeNowButton(task));
    line.append(act);
  }
  return line;
}

function taskActions(task, onTimeline) {
  return rowActions([
    { id: 'copy-id', label: `Copier l'identifiant ${task.id}`, icon: 'copy', run: (b) => copyText(task.id, b) },
    { id: 'copy-command', label: 'Copier la commande', icon: 'terminal', run: (b) => copyText(`grimoire task show ${task.id}`, b) },
    onTimeline ? { id: 'timeline', label: 'Voir la chronologie', icon: 'history', run: () => onTimeline(task) } : null,
  ], { label: `Plus d'actions pour ${task.id}` });
}

function taskCard(task, onSelect, portfolio = false, selected = false, onTimeline = null) {
  const card = document.createElement('div');
  card.className = 'ex-card';
  if (selected) card.setAttribute('aria-current', 'true');
  card.tabIndex = 0;
if (task.presence && !isClosed(task)) card.dataset.presence = task.presence.state;
  card.dataset.project = task.project?.slug || '';
  card.dataset.task = task.id;
  // P74 : liseré gauche stable par « ouverte par » (hash → token --owner-N).
  const tone = ownerTone(task.owner);
  if (tone) card.style.setProperty('--owner-tone', tone);
  // Entrée (P58) passe par le contrôleur de liste de la coque : le clic de la carte.
  card.addEventListener('click', () => onSelect(task));

  // Ligne du haut (artboard Travail) : identifiant, classe de route, anneau de preuve.
  const top = document.createElement('div');
  top.className = 'ex-card-top';
  const cardId = text('span', 'ex-card-id mono', task.id);
  cardId.title = task.id;
  top.append(cardId);
  appendIf(top, routeBadge(task));
  appendIf(top, proofRing(task));
  card.append(top);
  // P74 : le préfixe interne (« R52 — », « [P47] », « P68 — ») quitte le titre
  // affiché ; le titre entier reste au survol, l'identifiant dans l'inspecteur.
  const cardTitle = text('div', 'ex-card-title', titleParts(task.title || task.id).title);
  cardTitle.title = task.title || task.id;
  card.append(cardTitle);
  if (portfolio) card.append(portfolioRow(task));
  // P74 : qui a ouvert, et depuis quand la carte est là — une ligne à eux,
  // la méta (type, risque, priorité) garde la sienne : rien ne se collapse.
  const who = text('div', 'ex-card-who lbl', '');
  who.append(ownerNode(task));
  appendIf(who, cardAge(task));
  card.append(who);
  const meta = text('div', 'ex-card-meta lbl', '');
  if (task.type) meta.append(typeGlyph(task.type));
  meta.append(typeBadge(task));
  if (task.risk_profile) {
    // Le profil par défaut garde son glyphe (forme + nom au survol) ; seul un
    // profil qui s'écarte du défaut écrit son mot, la ligne tient sur une carte.
    const riskGlyph = RISK_ICON[task.risk_profile] ? glyph(RISK_ICON[task.risk_profile], `profil de risque : ${task.risk_profile}`) : null;
    if (riskGlyph) meta.append(riskGlyph);
    if (task.risk_profile !== 'standard' || !riskGlyph) meta.append(text('span', null, task.risk_profile));
  }
  // Priorité effective (issue #638) : barres + mot, jamais la couleur seule.
  const priority = task.effective_priority || 'medium';
  const bars = priorityBars(priority);
  if (bars) meta.append(bars);
  meta.append(text('span', null, PRIORITY_LABEL[priority] || priority));
  card.append(meta);
  // Le badge « message en attente de la session » tant qu'une consigne de
  // l'orchestrateur n'a pas été livrée à la session — sur sa propre ligne.
  const flags = document.createElement('div');
  flags.className = 'ex-card-flags';
  if (task.directives_pending > 0) {
    const unread = badge(
      `message en attente de la session (${task.directives_pending})`, 'acc',
      'Consigne posée au ledger, pas encore livrée à la session qui tient la tâche.',
    );
    unread.dataset.role = 'directive-badge';
    flags.append(unread);
  } else if (task.directives_unacknowledged > 0) {
    flags.append(badge(
      `message livré, sans accusé (${task.directives_unacknowledged})`, 'acc',
      'Consigne livrée à la session, dont aucun agent n’a encore accusé réception.',
    ));
  }
  if (flags.childElementCount) card.append(flags);
  if (task.board === 'blocked' && task.status !== 'waiting') {
    // Le mot ET la raison, jamais la seule couleur (une attente nommée a déjà
    // sa propre ligne juste en dessous).
    const line = document.createElement('div');
    line.className = 'ex-wait flag bad';
    line.dataset.role = 'blocked';
    line.title = `Bloquée : ${task.blocked_reason || 'raison non écrite'}`;
    line.append(stateIcon('bad'), text('span', null, 'Bloquée'));
    line.append(text('span', 'lbl', task.blocked_reason || 'raison non écrite'));
    card.append(line);
  }
  if (task.status === 'waiting' && task.waiting) {
    const human = isHumanWait(task);
    const line = document.createElement('div');
    line.className = 'ex-wait' + (human ? ' human' : '');
    line.dataset.role = 'waiting';
    line.title = waitingText(task.waiting);
    // P91 : « Bloquée » est le mot d'état, la cause nommée vient après.
    line.append(waitGlyph(task.waiting), text('span', 'ex-presence-word', 'Bloquée'), text('span', null, `· ${human ? 'attend ta réponse' : waitingText(task.waiting)}`));
    if (human) line.append(text('span', 'lbl', QUESTION_KIND_LABEL[task.waiting.question.kind] || task.waiting.question.kind));
    card.append(line);
    if (human) card.append(text('div', 'lbl', task.waiting.question.text));
  }

  // Une carte en revue dit ce qui lui manque : la première condition rouge du
  // passage à « Acceptée », en clair, avec son gate au survol.
  if (task.board === 'review' && task.next_gate) {
    const missing = (task.next_gate.conditions || []).find((c) => !c.ok);
    if (missing) {
      const flag = document.createElement('div');
      flag.className = 'ex-wait flag warn';
      flag.dataset.role = 'missing-condition';
      flag.title = `${missing.gate} — ${missing.reason}`;
      flag.append(stateIcon('warn'), text('span', null, sentence(missing.reason) || gateLabel(missing.gate)));
      card.append(flag);
    }
  }

  // Session qui porte la carte (issue #638, lot A) : `session_id` et `host`
  // viennent du claim, posés par le hook UserPromptSubmit de la session —
  // absents tant qu'aucune session d'hôte n'a travaillé sous ce claim.
  // P74 : plus d'UUID sur la carte (hôte + persona + palier), et plus rien de
  // tout cela sur une tâche close — c'est l'inspecteur qui parle de sa session.
  // P91 : la ligne de présence est LA ligne d'état ; en dessous, seulement les
  // faits connus (hôte · persona · palier · effort · tokens), jamais une absence.
  appendIf(card, presenceLine(task));
  if (task.session_id && !isClosed(task)) {
    appendIf(card, cardSessionLine(task));
    appendIf(card, workLiveRow(task));
  }
  // P69v2 : la dernière entrée du fil d'activité, en une ligne — le serveur ne
  // la pose que sur une tâche tenue dont la session est vivante.
  if (task.feed_last && task.feed_last.label) {
    const feedLine = text('div', 'ex-card-feed lbl', '');
    feedLine.dataset.role = 'feed-last';
    feedLine.title = `Dernière entrée du fil d'activité (ledger) : ${task.feed_last.label}`;
    feedLine.append(icon('history'), text('span', 'ex-card-feed-text', task.feed_last.label));
    card.append(feedLine);
  }
  // P75 : une carte close dit le total de tokens et le coût approximatif en
  // CAD — c'est son bilan ; la ligne « coût (dispatch) » de P11 reste aux
  // cartes ouvertes (deux « coût inconnu » superposés ne disaient rien).
  const closedTotal = isClosed(task) && task.work ? workTotalRow(task.work) : null;
  const cost = closedTotal || !(task.cost && task.cost.status && task.cost.status !== 'unknown') ? null : costRow(task);
  if (closedTotal) card.append(closedTotal);
  else if (cost) card.append(cost);

  const { forward, canBlock } = nextMoves(task);
  const nextRow = document.createElement('div');
  nextRow.className = 'ex-card-next';
  nextRow.dataset.role = 'next-moves';
  nextRow.append(text('span', null, forward ? `→ ${COLUMN_LABEL[forward]}` : 'colonne terminale'));
  const actions = document.createElement('span');
  actions.className = 'row';
  if (canBlock) {
    const blockBtn = document.createElement('button');
    blockBtn.type = 'button';
    blockBtn.className = 'btn';
    blockBtn.textContent = 'Bloquer…';
    blockBtn.title = 'Ouvre la tâche ; bloquer demande une raison.';
    blockBtn.addEventListener('click', (e) => { e.stopPropagation(); focusBlockReason = true; onSelect(task); });
    actions.append(blockBtn);
  }
  // « Voir la porte » doublait le clic sur la carte (qui ouvre le même panneau de
  // détail) : la carte entière est le geste, comme sur la maquette.
  actions.append(taskActions(task, onTimeline));
  nextRow.append(actions);
  card.append(nextRow);
  return card;
}

function renderBoard(root, ctx, tasks, groups, onSelect, portfolio = false, isSelected = () => false, onTimeline = null) {
  const shell = document.createElement('div');
  shell.className = 'ex-board-shell';
  const tabs = document.createElement('div');
  tabs.className = 'ex-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Colonnes du tableau');
  const board = document.createElement('div');
  board.className = 'ex-board';
  const byColumn = new Map();
  for (const t of tasks) {
    if (!byColumn.has(t.board)) byColumn.set(t.board, []);
    byColumn.get(t.board).push(t);
  }
  for (const group of groups) {
    const col = document.createElement('div');
    col.className = 'ex-col' + (group.folded ? ' ex-col-folded' : '');
    col.dataset.group = group.id;
    col.id = `ex-col-${group.id}`;
    const inColumn = group.cols.flatMap((c) => byColumn.get(c) || []).sort(byPriority);
    const folded = group.folded && !unfolded.has(group.id);
    // P72v2 : sur le board compact, les bloquées sortent du flux de la colonne —
    // compteur de ton warn sous le titre, section « Bloqué » repliée par défaut.
    const heldBack = group.blockedFold ? inColumn.filter((t) => t.board === 'blocked') : [];
    const shown = heldBack.length ? inColumn.filter((t) => t.board !== 'blocked') : inColumn;

    const head = document.createElement('div');
    head.className = 'ex-col-head';
    const title = document.createElement('div');
    title.className = 'ex-col-title';
    title.append(icon(group.icon), text('span', null, group.label), text('span', 'mono lbl', String(inColumn.length)));
    if (group.cols.length > 1) title.dataset.term = 'porte-de-preuve';
    head.append(title);
    if (group.folded) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'btn';
      toggle.textContent = folded ? 'Afficher' : 'Replier';
      toggle.setAttribute('aria-expanded', String(!folded));
      toggle.addEventListener('click', () => {
        if (unfolded.has(group.id)) unfolded.delete(group.id); else unfolded.add(group.id);
        const fresh = document.createElement('div');
        renderBoard(fresh, ctx, tasks, groups, onSelect, portfolio, isSelected, onTimeline);
        shell.replaceWith(fresh.firstElementChild);
      });
      head.append(toggle);
      head.append(text('div', 'ex-col-gate', 'tâches annulées : ni faites, ni à reprendre'));
    } else if (group.cols.length > 1) {
      head.append(text('div', 'ex-col-gate', 'colonnes repliées : ' + group.cols.map((c) => COLUMN_LABEL[c]).join(', ')));
    } else {
      const evidences = [...new Set(inColumn.flatMap((t) => t.expected_evidence || []))];
      head.append(text('div', 'ex-col-gate', evidences.length ? `porte : requiert ${evidences.slice(0, 3).join(', ')}` : 'porte : aucune preuve déclarée'));
    }
    col.append(head);

    if (heldBack.length) {
      // Compteur + repli (décision P72v2) : pas de colonne dédiée, pas d'alarme.
      // Absent dès qu'aucune tâche n'est bloquée.
      const foldKey = `${group.id}:blocked`;
      const open = unfolded.has(foldKey);
      const sectionId = `ex-blocked-${group.id}`;
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'ex-blocked-toggle';
      toggle.dataset.role = 'blocked-count';
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-controls', sectionId);
      toggle.title = open ? 'Replier les tâches bloquées' : 'Afficher les tâches bloquées et leur raison';
      toggle.append(stateIcon('warn'), text('span', null, `${heldBack.length} bloquée${heldBack.length > 1 ? 's' : ''}`));
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (unfolded.has(foldKey)) unfolded.delete(foldKey); else unfolded.add(foldKey);
        const fresh = document.createElement('div');
        renderBoard(fresh, ctx, tasks, groups, onSelect, portfolio, isSelected, onTimeline);
        shell.replaceWith(fresh.firstElementChild);
      });
      col.append(toggle);
      const section = document.createElement('div');
      section.className = 'ex-blocked-body';
      section.id = sectionId;
      section.dataset.role = 'blocked-section';
      section.hidden = !open;
      section.append(text('div', 'ex-blocked-title', 'Bloqué'));
      for (const task of heldBack) section.append(taskCard(task, onSelect, portfolio, isSelected(task), onTimeline));
      col.append(section);
    }

    // P91 : le couloir « À reprendre » est un groupe en tête de « À faire » (pas
    // de 5e colonne) : ces tâches sont `ready` au ledger, le glisser-déposer
    // reste valide, et une tâche rendue passe avant une neuve.
    const resuming = group.id === 'todo' ? shown.filter((t) => presenceOf(t) === 'resume') : [];
    const rest = resuming.length ? shown.filter((t) => presenceOf(t) !== 'resume') : shown;
    if (resuming.length) {
      const pill = text('span', 'ex-col-count-resume', `${resuming.length} à reprendre`);
      pill.dataset.role = 'resume-count';
      head.querySelector('.ex-col-title').append(pill);
    }

    const body = document.createElement('div');
    body.className = 'ex-col-body' + (shown.length ? '' : ' empty');
    body.hidden = folded;
    // I4 : « Fait » garde les plus récentes en cartes ; le reste, en lignes compactes derrière un bouton.
    const split = group.recentLimit && shown.length > group.recentLimit ? splitRecent(shown, group.recentLimit) : null;
    if (!shown.length) {
      body.append(text('span', null, 'vide'));
    } else {
      if (resuming.length) {
        const lane = document.createElement('section');
        lane.className = 'ex-resume-group';
        lane.dataset.role = 'resume-group';
        lane.setAttribute('aria-label', `À reprendre, ${resuming.length}`);
        const laneTitle = text('div', 'ex-resume-title', '');
        laneTitle.append(glyph('i-session-resume', 'à reprendre', 'warn'), text('span', null, `À reprendre · ${resuming.length}`));
        lane.append(laneTitle);
        for (const task of resuming) lane.append(taskCard(task, onSelect, portfolio, isSelected(task), onTimeline));
        body.append(lane);
        if (rest.length) body.append(text('div', 'ex-queue-title', `En file · ${rest.length}`));
      }
      for (const task of split ? split.recent : rest) body.append(taskCard(task, onSelect, portfolio, isSelected(task), onTimeline));
    }
    if (split) {
      const key = `${group.id}:older`;
      const open = unfolded.has(key);
      const listId = `ex-older-${group.id}`;
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'ex-more';
      more.dataset.role = 'older-toggle';
      more.setAttribute('aria-expanded', String(open));
      more.setAttribute('aria-controls', listId);
      more.textContent = open ? 'Replier les plus anciennes' : `Voir les ${split.remaining} autres`;
      more.addEventListener('click', () => {
        if (unfolded.has(key)) unfolded.delete(key); else unfolded.add(key);
        const fresh = document.createElement('div');
        renderBoard(fresh, ctx, tasks, groups, onSelect, portfolio, isSelected, onTimeline);
        shell.replaceWith(fresh.firstElementChild);
      });
      const older = document.createElement('div');
      older.className = 'ex-older';
      older.id = listId;
      older.hidden = !open;
      for (const task of split.older) {
        const rowBtn = document.createElement('button');
        rowBtn.type = 'button';
        rowBtn.className = 'ex-older-row';
        rowBtn.dataset.task = task.id;
        rowBtn.dataset.project = task.project?.slug || '';
        if (isSelected(task)) rowBtn.setAttribute('aria-current', 'true');
        rowBtn.title = task.title || task.id;
        rowBtn.append(text('span', 'ex-older-title', titleParts(task.title || task.id).title));
        const at = task.in_state_since || '';
        if (at && !Number.isNaN(Date.parse(at))) rowBtn.append(sinceNode(at));
        rowBtn.addEventListener('click', () => onSelect(task));
        older.append(rowBtn);
      }
      body.append(more, older);
    }
    col.append(body);
    board.append(col);

    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'ex-tab';
    tab.id = `ex-tab-${group.id}`;
    tab.dataset.group = group.id;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', col.id);
    tab.append(icon(group.icon), text('span', null, group.label), text('span', 'mono', String(inColumn.length)));
    tabs.append(tab);
  }

  // I4 : sous 720 px une colonne à la fois ; l'onglet actif suit la colonne visible.
  const tabEls = [...tabs.children];
  const cols = [...board.children];
  const select = (index, { scroll = false, focus = false } = {}) => {
    const i = Math.max(0, Math.min(tabEls.length - 1, index));
    tabEls.forEach((t, k) => {
      t.setAttribute('aria-selected', String(k === i));
      t.tabIndex = k === i ? 0 : -1;
    });
    activeGroup = cols[i]?.dataset.group || '';
    if (scroll && cols[i]) board.scrollLeft = cols[i].offsetLeft;
    if (focus) tabEls[i]?.focus();
  };
  tabEls.forEach((t, k) => t.addEventListener('click', () => select(k, { scroll: true })));
  tabs.addEventListener('keydown', (e) => {
    const k = tabEls.indexOf(document.activeElement);
    if (k < 0) return;
    const delta = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    const target = e.key === 'Home' ? 0 : e.key === 'End' ? tabEls.length - 1 : k + delta;
    if (!delta && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    select(target, { scroll: true, focus: true });
  });
  board.addEventListener('scroll', () => {
    if (!cols.length || !board.clientWidth) return;
    // Seule la mise en page mobile (colonnes pleine largeur) lie défilement et onglet.
    if (cols[0].offsetWidth < board.clientWidth - 2) return;
    const step = cols.length > 1 ? cols[1].offsetLeft - cols[0].offsetLeft : board.clientWidth;
    const i = Math.max(0, Math.min(cols.length - 1, Math.round(board.scrollLeft / step)));
    if (tabEls[i]?.getAttribute('aria-selected') !== 'true') select(i);
  }, { passive: true });
  const start = Math.max(0, cols.findIndex((c) => c.dataset.group === activeGroup));
  select(start);
  if (start > 0) requestAnimationFrame(() => { if (cols[0].offsetWidth >= board.clientWidth - 2) board.scrollLeft = cols[start].offsetLeft; });

  shell.append(tabs, board);
  root.append(shell);
}

function renderList(root, ctx, tasks, onSelect, portfolio = false, isSelected = () => false, onTimeline = null) {
  const wrap = document.createElement('div');
  wrap.className = 'ex-list-wrap';
  const table = document.createElement('table');
  table.className = 'ex-list';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const columns = portfolio
    ? ['Projet', 'Tâche', 'État', 'Session', 'Priorité', 'Owner', 'Coût', 'Prochaine porte']
    : ['Tâche', 'État', 'Priorité', 'Owner', 'Session', 'Preuves', 'Coût', 'Prochaine porte'];
  for (const label of columns) headRow.append(text('th', null, label));
  const actionsHead = text('th', 'ex-list-actions', '');
  actionsHead.setAttribute('aria-label', 'Actions');
  headRow.append(actionsHead);
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  const ordered = [...tasks].sort((a, b) => (Number(isHumanWait(b)) - Number(isHumanWait(a))) || (LIFECYCLE.indexOf(a.board) - LIFECYCLE.indexOf(b.board)) || byPriority(a, b));
  for (const task of ordered) {
    const tr = document.createElement('tr');
    tr.dataset.project = task.project?.slug || '';
    tr.dataset.task = task.id;
    if (isSelected(task)) tr.setAttribute('aria-current', 'true');
    tr.addEventListener('click', () => onSelect(task));
    if (portfolio) tr.append(text('td', null, task.project?.name || task.project?.slug || '—'));
    // P74 : même titre lisible que la carte ; le titre entier reste au survol.
    const titleCell = text('td', null, titleParts(task.title || task.id).title);
    titleCell.title = task.title || task.id;
    tr.append(titleCell);
    const stateCell = document.createElement('td');
    const waitingNow = task.status === 'waiting' && task.waiting;
    stateCell.append(row(
      dot(waitingNow ? 'warn' : (task.board === 'blocked' ? 'bad' : (task.board === 'accepted' || task.board === 'released' ? 'ok' : ''))),
      text('span', null, waitingNow ? waitingText(task.waiting) : (COLUMN_LABEL[task.board] || task.board)),
    ));
    tr.append(stateCell);
    if (portfolio) {
      const sessionCell = document.createElement('td');
      const session = task.session;
      // P74 : même règle que la carte — une tâche close n'affiche plus de vivacité.
      if (session && session.id && !isClosed(task)) {
        sessionCell.append(row(sessionGlyph(session), text('span', null, livenessLabel(session))));
      } else {
        sessionCell.append(text('span', 'lbl', '—'));
      }
      tr.append(sessionCell);
      const prioCell = document.createElement('td');
      if (task.priority) prioCell.append(row(priorityBars(task.priority) || '', text('span', null, task.priority)));
      else prioCell.append(text('span', 'lbl', '—'));
      tr.append(prioCell);
    } else {
      const prio = task.effective_priority || 'medium';
      const prioCell = document.createElement('td');
      prioCell.append(row(priorityBars(prio) || '', text('span', null, PRIORITY_LABEL[prio] || prio)));
      tr.append(prioCell);
    }
    tr.append(text('td', null, task.owner || '—'));
    if (!portfolio) {
      const sessionCell = document.createElement('td');
      if (task.session_id) sessionCell.append(sessionLine(task));
      else sessionCell.append(text('span', 'lbl', '—'));
      tr.append(sessionCell);
      const evCell = document.createElement('td');
      evCell.append(row(...(task.expected_evidence || []).slice(0, 3).map((e) => chip(e))));
      if (!(task.expected_evidence || []).length) evCell.append(text('span', 'lbl', '—'));
      tr.append(evCell);
    }
    // Coût : « inconnu » reste lisible, et une tâche jamais démarrée garde un tiret.
    tr.append(text('td', 'lbl', costRow(task) ? costLabel(task.cost).replace(/^coût /, '') : '—'));
    tr.append(text('td', 'lbl', nextMovesText(task, '—')));
    const actionCell = text('td', 'ex-list-actions', '');
    actionCell.append(taskActions(task, onTimeline));
    tr.append(actionCell);
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  root.append(wrap);
}

// État des filtres de la Timeline — vit au niveau du module : changer de
// tâche ou revenir sur la vue garde le dernier filtre choisi, comme les
// autres réglages d'affichage de cet espace (vue, sélection).
const timelineFilter = { source: 'tous', gravite: 'tous' };

// « 26 oct. 07:47:43 » (l'année quand ce n'est pas l'année en cours) : l'ISO
// brut (microsecondes, fuseau) débordait sur la colonne voisine (P68). L'ISO
// complet reste dans `datetime` et au survol.
const TL_DATE = new Intl.DateTimeFormat('fr-FR', {
  day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});
function timelineDate(iso) {
  const when = Date.parse(iso || '');
  if (Number.isNaN(when)) return iso || '—';
  const parts = Object.fromEntries(TL_DATE.formatToParts(new Date(when)).map((p) => [p.type, p.value]));
  const year = Number(parts.year) === new Date().getFullYear() ? '' : ` ${parts.year}`;
  return `${parts.day} ${parts.month}${year} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function timelineTime(iso) {
  const node = document.createElement('time');
  node.className = 'ex-tl-at';
  node.textContent = timelineDate(iso);
  if (iso) { node.dateTime = iso; node.title = iso; }
  return node;
}

function timelineEntryNode(entry) {
  const node = document.createElement('div');
  node.className = 'ex-tl-entry' + (entry.failure ? ' fail' : '');
  const head = document.createElement('div');
  head.className = 'ex-tl-row';
  head.append(timelineTime(entry.at));
  const body = document.createElement('div');
  body.className = 'ex-tl-body';
  body.append(row(dot(entry.failure ? 'bad' : 'ok'), text('span', null, entry.summary || entry.kind)));
  // Sans résumé, le titre est déjà le type : la ligne du dessous ne le répète pas.
  body.append(text('div', 'lbl', entry.summary ? `${entry.source} · ${entry.kind}` : entry.source));
  head.append(body);
  node.append(head);

  // Le détail brut (identifiants de trace, tags, span OTel…) s'ouvre en
  // accordéon sous la ligne : le panneau d'inspecteur de cet espace reste
  // occupé par la tâche sélectionnée (transitions, preuves, porte suivante),
  // quelle que soit la vue active — ouvrir le détail d'une ligne ne doit pas
  // le lui retirer. Jamais le contenu d'un prompt : `entry.detail` ne porte
  // que des identifiants, des tags et des résumés déjà affichés ailleurs.
  let expanded = false;
  node.addEventListener('click', () => {
    expanded = !expanded;
    const already = node.querySelector('.ex-tl-detail');
    if (already) { already.remove(); if (!expanded) return; }
    if (!expanded) return;
    const hasDetail = entry.detail && Object.keys(entry.detail).length;
    const pre = document.createElement('pre');
    pre.className = 'ex-tl-detail';
    pre.textContent = hasDetail ? JSON.stringify(entry.detail, null, 2) : 'Aucun détail supplémentaire.';
    node.append(pre);
  });
  return node;
}

function timelineFilterBar(sources, onChange) {
  const bar = document.createElement('div');
  bar.className = 'ex-tl-filters';

  const sourceSelect = document.createElement('select');
  sourceSelect.className = 'input';
  for (const value of ['tous', ...sources]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value === 'tous' ? 'Toutes les sources' : value;
    if (value === timelineFilter.source) option.selected = true;
    sourceSelect.append(option);
  }
  sourceSelect.addEventListener('change', () => { timelineFilter.source = sourceSelect.value; onChange(); });

  const graviteSelect = document.createElement('select');
  graviteSelect.className = 'input';
  for (const [value, label] of [['tous', 'Toutes les gravités'], ['causes', 'Causes seulement']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === timelineFilter.gravite) option.selected = true;
    graviteSelect.append(option);
  }
  graviteSelect.addEventListener('change', () => { timelineFilter.gravite = graviteSelect.value; onChange(); });

  bar.append(sourceSelect, graviteSelect);
  return bar;
}

async function renderTimeline(root, ctx, task) {
  const trace = await ctx.api.taskTrace(task.id, task.project?.slug).catch(() => null);
  const wrap = document.createElement('div');
  wrap.className = 'ex-timeline';
  const allEntries = trace?.entries || [];
  if (!trace || !allEntries.length) {
    const sourcesLues = trace ? Object.entries(trace.sources || {}) : [];
    const lues = sourcesLues.filter(([, path]) => path).map(([name]) => name);
    const absentes = sourcesLues.filter(([, path]) => !path).map(([name]) => name);
    wrap.append(ctx.empty(
      'Timeline',
      lues.length || absentes.length
        ? `Aucun événement pour cette tâche. Sources lues : ${lues.join(', ') || 'aucune'}` +
          (absentes.length ? ` — absentes : ${absentes.join(', ')}.` : '.')
        : "Aucun journal ne mentionne cette tâche : ni le Mission Ledger, ni le TraceLedger, ni le runtime.",
      `grimoire task trace ${task.id}`,
    ));
  } else {
    const sources = [...new Set(allEntries.map((e) => e.source))].sort();
    const list = document.createElement('div');
    const draw = () => {
      list.replaceChildren();
      const filtered = allEntries.filter((e) => (
        (timelineFilter.source === 'tous' || e.source === timelineFilter.source) &&
        (timelineFilter.gravite === 'tous' || e.failure)
      ));
      for (const entry of filtered) list.append(timelineEntryNode(entry));
      if (!filtered.length) list.append(text('p', 'lbl', 'Aucun événement pour ce filtre.'));
      count.textContent = `${filtered.length}/${allEntries.length}`;
    };
    const bar = timelineFilterBar(sources, draw);
    const count = text('span', 'ex-tl-count', '');
    bar.append(count);
    wrap.append(bar, list);
    draw();
  }
  root.append(wrap);
  ctx.dock.log('traces', ...allEntries.map((e) => `${e.at} · ${e.source} · ${e.summary}`));
}

// ── Décisions en attente (K6, P68) ────────────────────────────────────────

// Une décision : la question, ses réponses proposées, une zone libre. Le clic
// poste `answer` ; le serveur répond 200 avec la tâche rendue (`ready`) ou
// refuse (question déjà tranchée, question de flow) — le refus s'affiche.
// W6-07 (issue #564) : un lot de questions posé en une fois est UNE carte, une
// réponse par question encore ouverte (`waiting.batch`, statut lu au registre
// `task.questions`) ; la tâche n'est rendue qu'une fois les bloquantes tranchées.
//
// P68 : une carte par type de décision, une seule action primaire par carte.
//   - geste humain (`permission`) : « Fait », ou les réponses proposées ;
//   - choix (`choice`, `merge` avec réponses) : liste de choix, « Valider ce choix » ;
//   - question libre (aucune réponse proposée) : la zone de réponse, ouverte ;
//   - reprise (`resume_*`, `retry_exhausted`) : une phrase écrite ici, « Reprendre »
//     ou « Abandonner » — mappés vers le libellé d'option réel envoyé au serveur.
// Le texte serveur intégral, le « pourquoi », les identifiants et la commande de
// reprise vivent dans « Détails » : rien n'est retiré, seulement replié.
function openQuestions(task) {
  const w = task.waiting;
  const group = [w.question, ...(w.batch || [])];
  const answered = new Set((task.questions || [])
    .filter((r) => r.answer_origin === 'human' && r.question).map((r) => r.question.id));
  const open = group.filter((q) => !answered.has(q.id));
  return open.length ? open : [w.question];
}

const RESUME_KINDS = new Set(['resume_branch', 'resume_unfenced', 'retry_exhausted']);
// Ordre des sections : ce qu'un humain seul peut faire d'abord, les reprises (souvent en nombre) à la fin.
const DECISION_TYPES = [
  { id: 'geste', label: 'Gestes humains', icon: 'user' },
  { id: 'choix', label: 'Choix', icon: 'split' },
  { id: 'libre', label: 'Questions libres', icon: 'chat' },
  { id: 'reprise', label: 'Reprises', icon: 'refresh' },
];
const DECISION_TYPE = Object.fromEntries(DECISION_TYPES.map((t) => [t.id, t]));
function decisionType(q) {
  if (RESUME_KINDS.has(q.kind)) return 'reprise';
  if (q.kind === 'permission') return 'geste';
  return (q.options || []).length ? 'choix' : 'libre';
}

// Ce que la vue garde d'un rendu à l'autre (le pouls redessine tout) : filtre,
// choix en cours, « Autre réponse… » ouvert, détails dépliés, confirmation et
// bilan d'un lot. Au niveau du module, comme `scope`.
const decisionUi = { filter: '', picked: new Map(), other: new Set(), details: new Set(), confirm: '', bulk: null };
const decisionKey = (task, q) => `${task.project?.slug || ''}|${task.id}|${q.id}`;

const isAbandon = (option) => String(option).trim().toLowerCase() === 'abandonner';
// Les deux gestes d'une reprise, lus dans les options réelles (« reprendre »,
// « réessayer »…) : jamais un libellé inventé envoyé au serveur.
function resumeOptions(q) {
  const options = q.options || [];
  const abandon = options.find(isAbandon) || '';
  const resume = options.find((o) => /^(reprendre|réessayer|relancer)$/i.test(String(o).trim()))
    || options.find((o) => !isAbandon(o)) || '';
  return { resume, abandon };
}

// « R52 — Poser le secret », « [P47] Refonte », « P68 — Décisions » :
// l'identifiant interne quitte les titres affichés (cartes, inspecteur,
// décisions) ; il reste lisible en métadonnée discrète (P74).
// Deux formes : « [P47] … » (le crochet suffit, tiret optionnel) et
// « P68 — … » / « R52 — … » (le tiret est requis : « P2P sans tiret » n'est
// pas un préfixe).
const TITLE_REF = /^(?:\[([PRW]\d+[a-z]?)\]\s*(?:[—–-]\s*)?|([PRW]\d+[a-z]?)\s*[—–-]\s*)/;
function titleParts(title) {
  const value = String(title || '');
  const match = TITLE_REF.exec(value);
  return match && value.length > match[0].length
    ? { title: value.slice(match[0].length), ref: match[1] || match[2] }
    : { title: value, ref: '' };
}

const askedAt = (task, q) => Date.parse(task.waiting.since || q.asked_at || '') || 0;

// La phrase d'une reprise, écrite ici à partir du type et de l'âge : le texte
// serveur (sonde, variable d'environnement, commande) reste entier dans les détails.
function resumeSentence(task, q) {
  const age = ageText(q.asked_at || task.waiting.since).replace(/^depuis /, '');
  const since = age ? ` depuis au moins ${age}` : '';
  if (q.kind === 'retry_exhausted') {
    const lost = /perdu son bail (\d+) fois/.exec(q.text || '');
    return lost
      ? `Cette tâche a perdu son worker ${lost[1]} fois : la relancer encore, ou l'abandonner ?`
      : "Cette tâche a épuisé ses tentatives automatiques : la relancer encore, ou l'abandonner ?";
  }
  if (q.kind === 'resume_branch') return `Le worker de cette tâche s'est arrêté${since} en laissant du travail local non poussé.`;
  return `La session de ce worker ne donne plus signe de vie${since}. Son hôte n'a pas de frein vérifié : rien n'est relancé sans votre accord.`;
}

function decisionChip(label, { icon: iconName = '', tone = '', title = '' } = {}) {
  const chipNode = document.createElement('span');
  chipNode.className = 'chip pill' + (tone ? ` ${tone}` : '');
  if (iconName) chipNode.append(icon(iconName));
  chipNode.append(text('span', null, label));
  if (title) chipNode.title = title;
  return chipNode;
}

function postAnswer(ctx, task, q, answer, portfolio, data) {
  const body = { text: answer, question_id: q.id, ...(data ? { answer_data: data } : {}) };
  ctx.dock.echo(`grimoire task answer ${task.id} "${answer}" --question ${q.id}` + (portfolio ? `  # projet ${task.project?.slug}` : ''));
  return portfolio
    ? ctx.api.portfolioTaskAction(task.id, 'answer', body, task.project?.slug)
    : ctx.api.taskAction(task.id, 'answer', body);
}

function decisionCard(ctx, task, portfolio, onWritten) {
  const open = openQuestions(task);
  const q = open[0];
  const type = decisionType(q);
  const { title, ref } = titleParts(task.title || task.id);
  const node = document.createElement('article');
  node.className = 'ex-decision';
  node.dataset.role = 'decision';
  node.dataset.task = task.id;
  node.dataset.type = type;
  node.dataset.kind = q.kind;
  if (task.project?.slug) node.dataset.project = task.project.slug;

  const head = document.createElement('div');
  head.className = 'ex-decision-head';
  const heading = text('h3', 'ex-decision-title', title);
  heading.title = task.title || task.id;
  const chips = document.createElement('div');
  chips.className = 'ex-decision-chips';
  // Une pastille d'avertissement porte le type : plus de bord jaune.
  const kindLabel = type === 'libre' ? 'question libre' : (QUESTION_KIND_LABEL[q.kind] || q.kind);
  chips.append(decisionChip(kindLabel, { icon: DECISION_TYPE[type].icon, tone: 'warn' }));
  if (open.length > 1) chips.append(decisionChip(`${open.length} questions en un lot`));
  const round = Number(q.round) || (task.waiting.rounds || []).length || 1;
  if (isV2(q) && round > 1) chips.append(decisionChip(`${round}e demande`, { icon: 'history' }));
  if (isV2(q) && q.blocking === false) chips.append(decisionChip('Non bloquant'));
  if (portfolio) {
    const projectName = task.project?.name || task.project?.slug || 'projet';
    const project = decisionChip(projectName);
    project.prepend(monogram(projectName));
    chips.append(project);
  }
  const age = ageText(task.waiting.since || q.asked_at);
  if (age) chips.append(decisionChip(age, { icon: 'clock', title: task.waiting.since || q.asked_at || '' }));
  head.append(heading, chips);
  node.append(head);

  if (q.origin === 'flow') {
    node.append(text('p', 'ex-decision-question', q.text));
    node.append(text('div', 'ex-decision-note', "Cette décision est celle d'un checkpoint de flow : elle s'atteste dans le flux (un tour humain de la session qui l'a présenté, puis la même décision renvoyée), pas ici."));
    node.append(decisionDetails(task, q, ref, { fullText: false }));
    return node;
  }
  const locked = !portfolio && ctx.host.readOnly;
  for (const question of open) {
    let box = node;
    if (open.length > 1) {
      box = document.createElement('div');
      box.className = 'ex-decision-item';
      box.dataset.role = 'decision-question';
      box.dataset.question = question.id;
      node.append(box);
    }
    if (isV2(question)) {
      appendQuestionV2(box, task, question, {
        locked,
        onWritten,
        article: open.length > 1 ? null : node,
        rounds: (task.waiting.rounds || []),
        post: (answer, data) => postAnswer(ctx, task, question, answer, portfolio, data),
        failure: failureText,
      });
      continue;
    }
    appendQuestionForm(ctx, box, task, question, { portfolio, locked, onWritten, grouped: open.length > 1, ref });
  }
  if (locked) node.append(text('div', 'ex-decision-note', "le cockpit est en lecture seule : ouvrez l'atelier de ce projet, ou la portée « Tous les projets »"));
  return node;
}

// « Détails » : tout ce que la carte replie — texte serveur, pourquoi,
// identifiants, session et commande de reprise (copiée, jamais exécutée : ADR-007).
function decisionDetails(task, q, ref, { fullText }) {
  const key = decisionKey(task, q);
  const details = document.createElement('details');
  details.className = 'ex-dec-more';
  details.dataset.role = 'decision-details';
  details.open = decisionUi.details.has(key);
  details.addEventListener('toggle', () => {
    if (details.open) decisionUi.details.add(key);
    else decisionUi.details.delete(key);
  });
  const summary = text('summary', null, 'Détails');
  summary.dataset.focusKey = `${key}|details`;
  details.append(summary);
  const list = document.createElement('dl');
  const add = (label, value, { mono = false } = {}) => {
    if (!value) return null;
    const dd = document.createElement('dd');
    if (value instanceof Node) dd.append(value);
    else if (mono) dd.append(text('code', null, value));
    else dd.textContent = value;
    list.append(text('dt', null, label), dd);
    return dd;
  };
  if (fullText) add('Texte du serveur', q.text);
  add('Pourquoi', q.why);
  add('Identifiant', ref);
  add('Tâche', task.id, { mono: true });
  add('Question', q.id, { mono: true });
  if (q.blocking === false) add('Portée', 'non bloquante');
  const hint = task.waiting.resume_hint || null;
  if (hint && hint.session_id) {
    const session = document.createElement('span');
    session.append(text('code', null, hint.session_id));
    if (hint.host) session.append(text('span', 'lbl', ` · ${HOST_LABEL[hint.host] || hint.host}`));
    add('Session', session);
  }
  if (hint && hint.branch) add('Branche', hint.branch + (hint.observed_head ? ` @ ${hint.observed_head.slice(0, 10)}` : ''), { mono: true });
  if (hint && hint.resume_command) {
    const box = document.createElement('div');
    box.className = 'ex-dec-cmd';
    const code = text('code', null, hint.resume_command);
    code.dataset.role = 'resume-command';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn';
    copy.dataset.role = 'copy-resume';
    copy.dataset.focusKey = `${key}|copy`;
    copy.append(icon('copy'), text('span', null, 'Copier la commande'));
    const status = actionStatus('');
    copy.addEventListener('click', async () => {
      // `copyText` appelle le presse-papiers dans le geste même (permission du navigateur).
      if (await copyText(hint.resume_command, copy)) {
        setActionStatus(status, 'Commande copiée : collez-la dans un terminal pour reprendre la session.', 'ok');
        return;
      }
      // Repli : la commande est sélectionnée, prête pour Ctrl+C.
      const selection = window.getSelection();
      if (selection) selection.selectAllChildren(code);
      setActionStatus(status, 'Copie refusée par le navigateur : la commande est sélectionnée, copiez-la (Ctrl+C).');
    });
    box.append(code, copy, status);
    add('Reprendre', box);
  }
  details.append(list);
  return details;
}

function appendQuestionForm(ctx, box, task, q, { portfolio, locked, onWritten, grouped, ref }) {
  const key = decisionKey(task, q);
  const type = decisionType(q);
  const status = actionStatus('');
  status.dataset.role = 'answer-status';
  const controls = [];
  const send = async (value) => {
    const answer = (value || '').trim();
    if (!answer) { setActionStatus(status, 'Une réponse vide ne tranche rien.', 'bad'); return; }
    for (const control of controls) control.disabled = true;
    setActionStatus(status, 'Envoi de la réponse…');
    try {
      const result = await postAnswer(ctx, task, q, answer, portfolio);
      if (result && result.blocked) throw new Error(result.reason || result.message || 'réponse refusée');
      decisionUi.picked.delete(key);
      decisionUi.other.delete(key);
      setActionStatus(status, 'Réponse envoyée.', 'ok');
      onWritten();
    } catch (error) {
      for (const control of controls) control.disabled = locked;
      setActionStatus(status, failureText(error), 'bad');
    }
  };
  const button = (label, { primary = false, role = '', focus = '' } = {}) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn' + (primary ? ' pri' : '');
    btn.textContent = label;
    if (role) btn.dataset.role = role;
    btn.dataset.focusKey = `${key}|${focus || role || label}`;
    btn.disabled = locked;
    controls.push(btn);
    return btn;
  };

  if (grouped) {
    const tags = [QUESTION_KIND_LABEL[q.kind] || q.kind];
    if (q.blocking === false) tags.push('non bloquante');
    box.append(text('div', 'ex-decision-note', tags.join(' · ')));
  }

  // Reprise : la phrase, deux gestes, le texte serveur dans les détails.
  const resume = type === 'reprise' ? resumeOptions(q) : { resume: '', abandon: '' };
  if (resume.resume || resume.abandon) {
    box.append(text('p', 'ex-decision-question', resumeSentence(task, q)));
    const actions = document.createElement('div');
    actions.className = 'ex-dec-actions';
    if (resume.resume) {
      const go = button('Reprendre', { primary: true, role: 'answer-resume' });
      go.dataset.answer = resume.resume;
      go.title = `Envoie « ${resume.resume} »`;
      go.addEventListener('click', () => send(resume.resume));
      actions.append(go);
    }
    if (resume.abandon) {
      const stop = button('Abandonner', { role: 'answer-abandon' });
      stop.dataset.answer = resume.abandon;
      stop.title = `Envoie « ${resume.abandon} » : la tâche est annulée`;
      stop.addEventListener('click', () => send(resume.abandon));
      actions.append(stop);
    }
    box.append(actions, status, decisionDetails(task, q, ref, { fullText: true }));
    return;
  }

  box.append(text('p', 'ex-decision-question', q.text));
  const options = q.options || [];
  const actions = document.createElement('div');
  actions.className = 'ex-dec-actions';

  // La zone libre : ouverte d'office pour une question sans réponse proposée,
  // repliée derrière « Autre réponse… » sinon.
  const free = document.createElement('div');
  free.className = 'ex-dec-free';
  free.id = `ex-free-${key.replace(/[^\w-]/g, '_')}`;
  const area = document.createElement('textarea');
  area.className = 'input';
  area.dataset.role = 'answer-text';
  area.disabled = locked;
  area.setAttribute('aria-label', 'Votre réponse');
  controls.push(area);
  const freeOnly = type === 'libre' || (type === 'reprise' && !options.length);
  area.placeholder = freeOnly ? 'Votre réponse' : 'Une autre réponse que celles proposées';
  const submit = button('Répondre', { primary: freeOnly, role: 'answer-submit' });
  submit.addEventListener('click', () => send(area.value));
  const submitRow = document.createElement('div');
  submitRow.className = 'ex-dec-actions';
  submitRow.append(submit);
  free.append(area, submitRow);

  if (freeOnly) {
    box.append(free, status, decisionDetails(task, q, ref, { fullText: false }));
    return;
  }

  if (options.length) {
    // Liste de choix : un clic choisit (aria-pressed), « Valider ce choix » envoie.
    const list = document.createElement('div');
    list.className = 'ex-choices';
    list.setAttribute('role', 'group');
    list.setAttribute('aria-label', 'Réponses proposées');
    const validate = button('Valider ce choix', { primary: true, role: 'answer-validate' });
    const choiceButtons = [];
    const paint = () => {
      const picked = decisionUi.picked.get(key);
      for (const choice of choiceButtons) choice.setAttribute('aria-pressed', String(choice.dataset.answer === picked));
      validate.disabled = locked || !picked;
    };
    options.forEach((option, index) => {
      const choice = document.createElement('button');
      choice.type = 'button';
      choice.className = 'ex-choice';
      choice.dataset.role = 'answer-option';
      choice.dataset.answer = option;
      choice.dataset.focusKey = `${key}|opt|${index}`;
      choice.disabled = locked;
      const mark = document.createElement('span');
      mark.className = 'ex-choice-mark';
      mark.setAttribute('aria-hidden', 'true');
      choice.append(mark, text('span', null, option));
      choice.addEventListener('click', () => {
        if (decisionUi.picked.get(key) === option) decisionUi.picked.delete(key);
        else decisionUi.picked.set(key, option);
        paint();
      });
      controls.push(choice);
      choiceButtons.push(choice);
      list.append(choice);
    });
    validate.addEventListener('click', () => send(decisionUi.picked.get(key)));
    box.append(list);
    actions.append(validate);
    paint();
    // Le désactivé de « Valider » dépend du choix : `controls` ne doit pas le réactiver seul.
    const reenable = controls.indexOf(validate);
    if (reenable >= 0) controls.splice(reenable, 1);
  } else {
    // Geste humain sans réponse proposée : « Fait » envoie « fait ».
    const done = button('Fait', { primary: true, role: 'answer-done' });
    done.title = 'Envoie « fait » : le geste demandé est accompli';
    done.addEventListener('click', () => send('fait'));
    actions.append(done);
  }

  const other = button('Autre réponse…', { role: 'answer-other' });
  other.setAttribute('aria-controls', free.id);
  const showFree = (on) => {
    free.hidden = !on;
    other.setAttribute('aria-expanded', String(on));
  };
  showFree(decisionUi.other.has(key));
  other.addEventListener('click', () => {
    const on = free.hidden;
    if (on) decisionUi.other.add(key);
    else decisionUi.other.delete(key);
    showFree(on);
    if (on) area.focus();
  });
  actions.append(other);
  box.append(actions, free, status, decisionDetails(task, q, ref, { fullText: false }));
}

// Lot « Tout reprendre » / « Tout abandonner » : aucune route groupée, donc N
// réponses envoyées l'une après l'autre ; un refus n'arrête pas les suivantes,
// le bilan les nomme. L'état vit au module : le pouls peut redessiner la vue
// pendant le lot, la progression se repeint sur la nouvelle zone.
function bulkEligible(tasks, action, portfolio, ctx) {
  if (!portfolio && ctx.host.readOnly) return [];
  return tasks.filter((task) => {
    const q = openQuestions(task)[0];
    if (q.origin === 'flow') return false;
    const options = resumeOptions(q);
    return action === 'resume' ? !!options.resume : !!options.abandon;
  });
}

function bulkMessage(state) {
  const resume = state.action === 'resume';
  if (state.running) return `${resume ? 'Reprise' : 'Abandon'} en cours : ${state.done}/${state.total}…`;
  const n = state.ok;
  const done = resume
    ? `${n} tâche${n > 1 ? 's' : ''} reprise${n > 1 ? 's' : ''}`
    : `${n} tâche${n > 1 ? 's' : ''} abandonnée${n > 1 ? 's' : ''}`;
  if (!state.refused.length) return `${done} sur ${state.total}.`;
  return `${done} sur ${state.total} ; ${state.refused.length} refus :`;
}

function paintBulk() {
  const state = decisionUi.bulk;
  for (const box of document.querySelectorAll('[data-role="bulk-status"]')) {
    if (!state) { setActionStatus(box, ''); continue; }
    const tone = state.running ? '' : (state.refused.length ? 'bad' : 'ok');
    setActionStatus(box, bulkMessage(state), tone);
    if (!state.running && state.refused.length) {
      const list = document.createElement('ul');
      list.className = 'ex-dec-refusals';
      for (const refusal of state.refused) list.append(text('li', null, `${refusal.title} — ${refusal.why}`));
      box.append(list);
    }
  }
  for (const btn of document.querySelectorAll('[data-role="bulk-resume"], [data-role="bulk-abandon"]')) {
    btn.disabled = !!(state && state.running);
  }
}

async function runBulk(ctx, action, tasks, portfolio, onWritten) {
  const state = { action, total: tasks.length, done: 0, ok: 0, refused: [], running: true };
  decisionUi.bulk = state;
  paintBulk();
  for (const task of tasks) {
    const q = openQuestions(task)[0];
    const options = resumeOptions(q);
    try {
      const result = await postAnswer(ctx, task, q, action === 'resume' ? options.resume : options.abandon, portfolio);
      if (result && result.blocked) throw new Error(result.reason || result.message || 'réponse refusée');
      state.ok += 1;
    } catch (error) {
      state.refused.push({ title: titleParts(task.title || task.id).title, why: failureText(error) });
    }
    state.done += 1;
    paintBulk();
  }
  state.running = false;
  paintBulk();
  onWritten();
}

function bulkControls(ctx, tasks, portfolio, onWritten) {
  const box = document.createElement('div');
  box.className = 'ex-dec-bulk';
  const confirmSlot = document.createElement('div');
  confirmSlot.dataset.role = 'bulk-confirm-slot';
  const showConfirm = ({ focus = false } = {}) => {
    confirmSlot.replaceChildren();
    const action = decisionUi.confirm;
    if (!action) return;
    const targets = bulkEligible(tasks, action, portfolio, ctx);
    if (targets.length < 2) { decisionUi.confirm = ''; return; }
    const confirm = document.createElement('div');
    confirm.className = 'ex-confirm';
    confirm.dataset.role = 'bulk-confirm';
    confirm.setAttribute('role', 'group');
    confirm.setAttribute('aria-label', 'Confirmer le lot');
    const n = targets.length;
    confirm.append(text('p', 'lbl', action === 'resume'
      ? `Reprendre ${n} tâches ? Chacune repart en « Prête » pour le prochain worker ; les réponses partent l'une après l'autre.`
      : `Abandonner ${n} tâches ? Chacune est annulée ; ce geste ne s'annule pas.`));
    const go = document.createElement('button');
    go.type = 'button';
    go.className = 'btn pri';
    go.dataset.role = 'bulk-confirm-go';
    go.textContent = action === 'resume' ? `Reprendre les ${n}` : `Abandonner les ${n}`;
    go.addEventListener('click', () => {
      decisionUi.confirm = '';
      confirmSlot.replaceChildren();
      runBulk(ctx, action, targets, portfolio, onWritten);
    });
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn';
    cancel.dataset.role = 'bulk-confirm-cancel';
    cancel.textContent = 'Annuler';
    cancel.addEventListener('click', () => { decisionUi.confirm = ''; confirmSlot.replaceChildren(); });
    confirm.append(go, cancel);
    confirmSlot.append(confirm);
    if (focus) go.focus({ preventScroll: true });
  };
  for (const [action, label, role] of [['resume', 'Tout reprendre', 'bulk-resume'], ['abandon', 'Tout abandonner', 'bulk-abandon']]) {
    const n = bulkEligible(tasks, action, portfolio, ctx).length;
    if (n < 2) continue;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.dataset.role = role;
    btn.dataset.focusKey = `bulk|${role}`;
    btn.textContent = `${label} (${n})`;
    btn.disabled = !!(decisionUi.bulk && decisionUi.bulk.running);
    btn.addEventListener('click', () => { decisionUi.confirm = action; showConfirm({ focus: true }); });
    box.append(btn);
  }
  if (decisionUi.confirm) showConfirm();
  return box.childElementCount ? { box, confirmSlot } : null;
}

function renderDecisions(root, ctx, tasks, portfolio, onWritten) {
  const pending = tasks.filter(isHumanWait);
  const wrap = document.createElement('div');
  wrap.className = 'ex-decisions';
  wrap.dataset.role = 'decisions';
  const bulkStatus = actionStatus('');
  bulkStatus.dataset.role = 'bulk-status';
  if (!pending.length) {
    wrap.append(bulkStatus, ctx.empty('Décisions en attente', "Aucune décision n'attend un humain.", 'grimoire task decisions', undefined, { art: 'i-empty-inbox' }));
    root.append(wrap);
    paintBulk();
    return;
  }
  const groups = new Map(DECISION_TYPES.map((t) => [t.id, []]));
  for (const task of pending) groups.get(decisionType(openQuestions(task)[0])).push(task);
  // Dans chaque section, la plus ancienne d'abord.
  for (const list of groups.values()) {
    list.sort((a, b) => (askedAt(a, openQuestions(a)[0]) - askedAt(b, openQuestions(b)[0])) || String(a.id).localeCompare(String(b.id)));
  }
  if (decisionUi.filter && !(groups.get(decisionUi.filter) || []).length) decisionUi.filter = '';

  // Compteurs par type : des filtres, comme les puces d'À traiter.
  const bar = document.createElement('div');
  bar.className = 'ex-dec-bar';
  const filters = document.createElement('div');
  filters.className = 'ex-dec-filter';
  filters.setAttribute('role', 'group');
  filters.setAttribute('aria-label', 'Filtrer les décisions par type');
  const sections = [];
  const filterButtons = [];
  const applyFilter = () => {
    for (const btn of filterButtons) btn.setAttribute('aria-pressed', String((btn.dataset.filter || '') === decisionUi.filter));
    for (const section of sections) section.hidden = !!decisionUi.filter && section.dataset.type !== decisionUi.filter;
  };
  const filterButton = (id, label, count, iconName) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip pill';
    btn.dataset.role = 'decision-filter';
    btn.dataset.filter = id;
    btn.dataset.focusKey = `filter|${id}`;
    if (iconName) btn.append(icon(iconName));
    btn.append(text('span', null, label), text('span', 'ex-dec-n', String(count)));
    btn.addEventListener('click', () => {
      decisionUi.filter = decisionUi.filter === id || !id ? '' : id;
      applyFilter();
    });
    filterButtons.push(btn);
    return btn;
  };
  filters.append(filterButton('', 'Toutes', pending.length, ''));
  for (const type of DECISION_TYPES) {
    const count = groups.get(type.id).length;
    if (count) filters.append(filterButton(type.id, type.label, count, type.icon));
  }
  bar.append(filters);
  wrap.append(bar, bulkStatus);

  for (const type of DECISION_TYPES) {
    const list = groups.get(type.id);
    if (!list.length) continue;
    const section = document.createElement('section');
    section.className = 'ex-dec-section';
    section.dataset.type = type.id;
    const head = document.createElement('div');
    head.className = 'ex-dec-section-head';
    const h = document.createElement('h3');
    h.id = `ex-dec-h-${type.id}`;
    h.append(icon(type.icon), text('span', null, type.label), text('span', 'ex-dec-n', String(list.length)));
    head.append(h);
    section.setAttribute('aria-labelledby', h.id);
    section.append(head);
    if (type.id === 'reprise') {
      const bulk = bulkControls(ctx, list, portfolio, onWritten);
      if (bulk) { head.append(bulk.box); section.append(bulk.confirmSlot); }
    }
    const ul = document.createElement('ul');
    ul.className = 'ex-dec-list';
    for (const task of list) {
      const li = document.createElement('li');
      li.append(decisionCard(ctx, task, portfolio, onWritten));
      ul.append(li);
    }
    section.append(ul);
    sections.push(section);
    wrap.append(section);
  }
  applyFilter();
  root.append(wrap);
  paintBulk();
}

// Le bandeau au-dessus du board : ce qui attend un humain ne se découvre pas en cherchant.
function decisionsBanner(count, onOpen) {
  const banner = document.createElement('div');
  banner.className = 'ex-banner';
  banner.dataset.role = 'decisions-banner';
  banner.append(glyph('user', 'décisions'), text('span', null, `${count} décision${count > 1 ? 's' : ''} en attente d'un humain`));
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn pri';
  btn.textContent = 'Voir les décisions';
  btn.addEventListener('click', onOpen);
  banner.append(btn);
  return banner;
}

// ── Inspecteur ────────────────────────────────────────────────────────────

// Vivacité calculée à quatre valeurs (K5, #770) : jamais un booléen. `inconnu`
// n'est pas « mort » ; `attention` (vivant sans progression, inconnu prolongé)
// colore le point sans rien tuer.
function livenessLabel(session, { reason = true } = {}) {
  const l = session.liveness || { state: 'inconnu' };
  // Accord avec « session » : les valeurs stockées restent masculines.
  const word = { vivant: 'vivante', inactif: 'inactive', inconnu: 'inconnue', 'terminé': 'terminée' }[l.state] || l.state;
  return reason && l.reason && l.state === 'inactif' ? `${word} (${l.reason})` : word;
}
// Glyphe de vivacité (P30) : une forme par valeur — anneau à moitié plein
// (vivante), anneau vide (inactive), anneau coché (terminée), anneau en
// tirets (inconnue) ; `attention` le teinte sans changer sa forme.
const SESSION_ICON = { vivant: 'i-session-live', inactif: 'i-session-idle', 'terminé': 'i-session-done', inconnu: 'i-session-unknown' };
function sessionGlyph(session) {
  const state = (session.liveness || {}).state || 'inconnu';
  const tone = session.attention ? 'warn' : (state === 'vivant' ? 'ok' : '');
  return glyph(SESSION_ICON[state] || 'i-session-unknown', 'vivacité de la session', tone);
}

// « Reprendre la session » (#638) : copie la commande sur l'hôte Claude Code,
// affiche l'identifiant ailleurs — jamais d'exécution depuis la webview.
function sessionBlock(session, { resume = true } = {}) {
  const block = document.createElement('div');
  block.className = 'ex-insp-block ex-session';
  block.append(text('h4', null, 'Session'));
  block.append(row(
    sessionGlyph(session),
    text('span', null, `${livenessLabel(session)} · ${session.id}` + (session.host ? ` · ${session.host}` : '')),
  ));
  if (session.attention) block.append(text('div', 'lbl', `attention : ${session.attention.reason}`));
  if (session.updated_at && !Number.isNaN(Date.parse(session.updated_at))) {
    const sign = text('div', 'lbl', 'dernier signe de vie : ');
    sign.dataset.role = 'last-sign';
    sign.append(sinceNode(session.updated_at));
    block.append(sign);
  } else {
    block.append(text('div', 'lbl', 'aucun signe de vie lu'));
  }
  block.append(text('div', 'lbl', `durée : ${durationText(session.duration_s)}` + (session.duration_s == null ? '' : ' (du premier au dernier signe de vie)')));
  block.append(text('div', 'lbl', `coût de la session : ${costText(session.cost)}` + (costText(session.cost) === 'inconnu' ? " (non mesuré par le kit)" : '')));
  const feedback = text('div', 'lbl', '');
  if (!resume) return block;
  if (session.resume_command) {
    const cmd = document.createElement('pre');
    cmd.className = 'ex-session-cmd';
    cmd.textContent = session.resume_command;
    block.append(cmd);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Reprendre la session (copier la commande)';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(session.resume_command);
        feedback.textContent = 'commande copiée — à coller dans un terminal';
      } catch {
        feedback.textContent = 'copie impossible ici : recopiez la commande ci-dessus';
      }
    });
    block.append(btn);
  } else {
    block.append(text('p', 'lbl', "cet hôte n'a pas de commande de reprise connue : reprenez la session par son identifiant"));
  }
  block.append(feedback);
  return block;
}

// P75 : la section « Travail de la session » de l'inspecteur — persona, hôte,
// modèle réel, effort, skills invoqués, tokens détaillés. Chaque donnée absente
// est dite absente (« non rapporté »), et le statut d'usage est écrit en toutes
// lettres quand le chiffre ne vient pas du journal de l'hôte.
function workBlock(task) {
  const work = task.work || {};
  const block = document.createElement('div');
  block.className = 'ex-insp-block';
  block.dataset.role = 'work-block';
  block.append(text('h4', null, 'Travail de la session'));
  const claim = task.claim || {};
  const persona = claim.agent_id || claim.actor_id || '';
  block.append(text('div', 'lbl', `persona : ${persona || 'non rapportée'}`));
  block.append(text('div', 'lbl', `hôte : ${task.host ? (HOST_LABEL[task.host] || task.host) : 'inconnu'}`));
  const session = work.session;
  const modelLine = text('div', 'lbl', '');
  modelLine.dataset.role = 'work-model-detail';
  modelLine.textContent = session && session.model
    ? `modèle réel : ${session.model}${session.tier ? ` (palier ${session.tier})` : ''}`
    : 'modèle réel : non rapporté';
  block.append(modelLine);
  const effortLine = text('div', 'lbl', `effort : ${session && session.effort ? session.effort : 'non rapporté'}`);
  effortLine.dataset.role = 'work-effort-detail';
  block.append(effortLine);
  const skillsLine = text('div', 'lbl', 'skills invoqués : ');
  skillsLine.dataset.role = 'work-skills-list';
  skillsLine.append(text('span', null, session && session.skills && session.skills.length ? session.skills.join(', ') : 'aucun rapporté'));
  block.append(skillsLine);
  const usage = work.usage || {};
  const usageLine = text('div', 'lbl', '');
  usageLine.dataset.role = 'work-usage';
  if (usage.usage_source === 'claude-code-transcript') {
    usageLine.textContent = `tokens : ${usage.total_tokens} (${usage.input_tokens} in / ${usage.output_tokens} out, `
      + `cache ${usage.cache_read_tokens} lu / ${usage.cache_creation_tokens} écrit, ${usage.calls} appel(s))`;
    block.append(usageLine);
    const costLine = text('div', 'lbl', '');
    costLine.dataset.role = 'work-cost';
    costLine.textContent = work.cost_cad != null && usage.cost_usd != null
      ? `coût : ${usage.cost_status === 'lower_bound' ? 'au moins ' : '≈ '}${cadText(work.cost_cad)} `
        + `(${usage.cost_usd.toFixed(4)} USD, taux ${String(work.usd_cad).replace('.', ',')})`
      : 'coût : inconnu (appels non tarifés)';
    block.append(costLine);
  } else {
    usageLine.textContent = `usage : ${USAGE_SOURCE_WORDS[usage.usage_source] || 'aucun usage replié (none)'}`;
    block.append(usageLine);
  }
  return block;
}

// Les conditions de passage (`next_gate`, lu par `workspace_api.annotate_gates`
// depuis le verdict du gate) en clair : le libellé dit la preuve, l'identifiant
// du gate reste affiché à côté pour qu'on puisse le chercher dans le fichier de portes.
const GATE_LABEL = {
  acceptance_criteria: "Critères d'acceptation déclarés",
  owner_or_agent_role: 'Propriétaire ou rôle nommé',
  provider_policy: 'Fournisseur activé au registre',
  evidence_pack: 'Pack de preuve présent',
  review_gate: 'Verdict de revue',
  evidence_gate: 'Critères couverts par la preuve',
  context_bundle: 'Context bundle présent',
  decision_trace: 'Trace de décision présente',
  blocker_reason: 'Raison du blocage écrite',
  remediation_plan: 'Plan de remédiation',
  compliance_score: 'Score de conformité',
  release_authorization: 'Autorisation de publication',
  // Reçu de revue (W3-01) : exigé par la clôture d'une tâche relue, comme `TaskService.gate`.
  'review.receipt_missing': 'Reçu de revue présent',
  'review.receipt_stale': 'Reçu de revue à jour',
  'review.receipt_invalid': 'Reçu de revue lisible',
  'review.finding_ungrounded': 'Constats de revue étayés',
  'review.not_ready': 'Relecture rendue READY',
  'review.criteria_changed': 'Critères inchangés depuis la relecture',
};
const gateLabel = (gate) => GATE_LABEL[gate] || gate;
// Les raisons du gate sont écrites en minuscules (« aucun verdict… ») : en tête de ligne, une majuscule.
const sentence = (value) => (value ? value.charAt(0).toUpperCase() + value.slice(1) : '');
const STATE_TONE = { review: 'warn', blocked: 'bad', accepted: 'ok', released: 'ok', in_progress: 'acc' };

function conditionList(gate) {
  const list = document.createElement('ul');
  list.className = 'ex-conds';
  list.dataset.role = 'gate-conditions';
  for (const c of gate.conditions) {
    const li = document.createElement('li');
    li.className = 'ex-cond ' + (c.ok ? 'ok' : 'bad');
    li.dataset.ok = String(c.ok);
    li.dataset.gate = c.gate;
    if (c.refused_at) li.dataset.refused = c.refused_at;
    const body = document.createElement('span');
    body.append(text('span', c.ok ? null : 'ex-cond-strong', gateLabel(c.gate)), text('span', 'lbl', ` · ${c.gate}`));
    // P11 : la raison, puis le remède sur sa ligne — ce qu'il faut faire, pas seulement ce qui manque.
    if (!c.ok) {
      const why = text('span', 'ex-cond-why', sentence(c.reason));
      why.dataset.role = 'gate-reason';
      body.append(why);
      if (c.remedy) {
        const remedy = text('span', 'ex-cond-why ex-cond-remedy', `Remède : ${c.remedy}`);
        remedy.dataset.role = 'gate-remedy';
        body.append(remedy);
      }
    }
    if (c.refused_at) {
      const refused = text('span', 'ex-cond-why', `déjà refusée le ${stamp(c.refused_at)}`);
      refused.dataset.role = 'gate-refused-at';
      body.append(refused);
    }
    li.append(stateIcon(c.ok ? 'ok' : 'bad'), body);
    list.append(li);
  }
  return list;
}

// Une date du journal, lisible : « 2026-10-25 14:02 » (heure du serveur, sans fuseau inventé).
const stamp = (iso) => String(iso || '').slice(0, 16).replace('T', ' ');

// Le dernier `task.gate.refused` (TraceLedger) : quand, quel gate, ce qui manquait.
function lastRefusalLine(refusal) {
  if (!refusal) return null;
  const line = document.createElement('div');
  line.className = 'ex-last-refusal';
  line.dataset.role = 'last-refusal';
  const missing = (refusal.missing || []).join(', ') || 'preuve non nommée';
  const who = refusal.actor ? ` par ${refusal.actor}` : '';
  line.append(stateIcon('bad'), text('span', null,
    `Dernier refus le ${stamp(refusal.at)}${who} : gate « ${refusal.transition_id || '?'} », manque ${missing}`));
  return line;
}

// Le refus d'une transition, sur place : une ligne par preuve, raison puis remède.
function refusalNode(refusals) {
  const box = document.createElement('div');
  box.className = 'ex-refusal';
  box.dataset.role = 'refusal';
  box.append(text('div', 'ex-cond-strong', 'refusé par le gate :'));
  for (const r of refusals) {
    const item = text('div', null, `${r.evidence} — ${sentence(r.reason)}`);
    if (r.remedy) item.append(text('span', 'ex-cond-why', `Remède : ${r.remedy}`));
    box.append(item);
  }
  return box;
}

// Ce qu'un re-rendu ne doit jamais écraser : la saisie en cours (valeur, focus,
// curseur) des champs d'un même inspecteur. Les champs sont retrouvés par leur
// `data-role` et leur rang ; une autre tâche ne reçoit rien.
function snapshotFields(container) {
  const fields = [];
  if (container.dataset.owner !== 'executer') return { task: '', fields };
  for (const el of container.querySelectorAll('input[data-role], textarea[data-role]')) {
    if (el.type === 'checkbox') continue;
    const focused = el === document.activeElement;
    const menu = el.closest('[data-role="more-panel"]');
    fields.push({
      inOpenMenu: Boolean(menu && (menu.popover ? menu.matches(':popover-open') : !menu.hidden)),
      role: el.dataset.role,
      nth: [...container.querySelectorAll(`[data-role="${el.dataset.role}"]`)].indexOf(el),
      value: el.value,
      focused,
      start: focused ? el.selectionStart : null,
      end: focused ? el.selectionEnd : null,
    });
  }
  return { task: container.dataset.task || '', fields };
}

function restoreFields(container, kept) {
  if (!kept || !kept.task || kept.task !== container.dataset.task) return;
  for (const f of kept.fields) {
    const el = container.querySelectorAll(`[data-role="${f.role}"]`)[f.nth];
    if (!el) continue;
    // Un menu « ⋯ » ouvert sur une saisie (raison d'annulation) se rouvre après
    // le re-rendu : un popover (P55) par la Popover API, qui remet aussi
    // `aria-expanded` ; un menu fermé, brouillon compris, le reste.
    const panel = el.closest('[data-role="more-panel"]');
    if (panel && f.inOpenMenu) {
      const toggle = container.querySelector('[data-role="more"]');
      if (panel.popover) {
        if (!panel.matches(':popover-open')) panel.showPopover({ source: toggle || undefined });
      } else if (panel.hidden) {
        panel.hidden = false;
        toggle?.setAttribute('aria-expanded', 'true');
      }
    }
    if (f.value) {
      el.value = f.value;
      // Les gardes du formulaire (« Annuler » inerte sans raison) écoutent `input`.
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (f.focused) {
      el.focus({ preventScroll: true });
      try { el.setSelectionRange(f.start, f.end); } catch { /* champ sans sélection */ }
    }
  }
}

// Bloc « Mémoire servie » (P27) : les lectures mémoire servies à cette tâche,
// la plus récente dépliée (requête, heure, entrées avec rang, score, portée,
// état et fraîcheur d'aujourd'hui), les autres au choix. « Rejouer maintenant »
// relance la même lecture (GET, aucune écriture) et rend le différentiel. Sans
// reçu, la phrase le dit : jamais une liste vide.
function receiptBlock(ctx, taskId, payload, project) {
  injectRecallStyles();
  const block = rcEl('div', 'ex-insp-block');
  block.dataset.role = 'recall-receipt';
  block.append(rcEl('h4', null, 'Mémoire servie'));
  if (!payload || payload.failed) {
    block.append(rcEl('p', 'ex-refusal', `Lecture des reçus impossible : ${payload && payload.failed ? payload.failed.message : 'réponse vide'}.`));
    return block;
  }
  if (payload.reason) {
    block.append(rcEl('p', 'lbl', `Reçus illisibles : ${payload.reason}.`));
    return block;
  }
  const receipts = payload.receipts || [];
  if (!receipts.length) {
    block.dataset.empty = 'true';
    block.append(rcEl('p', 'lbl', payload.logPresent
      ? 'aucune lecture mémoire enregistrée pour cette tâche'
      : 'aucune lecture mémoire enregistrée pour cette tâche : ce projet n’a encore aucun journal des lectures'));
    return block;
  }
  if (payload.memoryReason) block.append(rcEl('p', 'lbl', `Entrées non décrites : ${payload.memoryReason}.`));
  const body = rcEl('div');
  const show = (receipt) => {
    const parts = [receiptMeta(receipt), servedList(receipt)];
    if (receipt.replayable) {
      parts.push(replayControl(() => ctx.api.memoryReplay({ receipt: receipt.key, task: taskId, project })));
    } else if (receipt.notReplayable) {
      const why = rcEl('p', 'lbl', `Pas de rejeu : ${receipt.notReplayable}.`);
      why.dataset.role = 'not-replayable';
      parts.push(why);
    }
    body.replaceChildren(...parts);
    body.dataset.key = receipt.key;
  };
  if (receipts.length > 1) {
    const label = rcEl('label', 'lbl', `Lecture (${rcPlural(payload.total, 'reçu', 'reçus')}) `);
    const select = rcEl('select', 'input', null, { 'aria-label': 'Lecture mémoire affichée' });
    select.dataset.role = 'receipt-pick';
    receipts.forEach((receipt, index) => {
      const option = rcEl('option', null, `${whenText(receipt.ts)} · ${rcPlural((receipt.served || []).length, 'souvenir', 'souvenirs')}`, { value: String(index) });
      select.append(option);
    });
    select.addEventListener('change', () => show(receipts[Number(select.value)]));
    label.append(select);
    block.append(label);
  }
  show(receipts[0]);
  block.append(body);
  const open = rcEl('button', 'btn ghost', 'Voir dans Savoir › Rappel', { type: 'button' });
  open.dataset.role = 'open-recall';
  open.addEventListener('click', () => ctx.goto('rappel', { task: taskId, of: project || '' }));
  block.append(open);
  return block;
}

// `task` porte l'id et, en portefeuille, `project.slug` (cible des lectures et
// des actions) et `session` (bloc « Session »). `portfolio` choisit la porte
// d'écriture : `portfolioTaskAction` (dérogation nommée, tout projet du
// registre) plutôt que `taskAction` (projet de lancement seulement).
//
// Le panneau de détail (P64) suit l'artboard Travail : identifiant et état, titre,
// carte « Pour passer en … » (conditions vertes ou rouges et leur gate, puis les
// gestes qui existent déjà), preuves, consigne à la session, derniers événements ;
// le reste du détail (description, critères, dépendances…) vient dessous.
//
// Aucune écriture nouvelle : chaque bouton ci-dessous existait avant P64.
//
// Une saisie en cours n'est jamais perdue (P64, course connue de
// `test_executer_priorite_consigne_et_annulation_depuis_l_inspecteur`) : le
// remplacement du panneau attend les trois lectures réseau, pendant lesquelles
// l'utilisateur a pu taper. `guard.defer()` dit si une saisie est en cours (alors
// le re-rendu est reporté, `false` rendu) ; et quand il a lieu quand même (clic
// sur une carte, transition), valeur, focus et curseur sont rétablis.
// ── Assigner une session (P47 lot 2) ────────────────────────────────────────
//
// Une section de l'inspecteur (jamais une modale) : le plan de lancement que
// GET tasks/<id>/launch-plan rend — modèle, worktree, branche, les cinq
// vérifications du préflight et la commande équivalente à copier. Le bouton
// « Lancer la session » est réel depuis le lot 3 : il envoie l'empreinte du
// plan montré, le serveur refuse si l'état a changé (ADR-008) et lance par le
// lanceur du tick, derrière la garde d'écriture commune (GAO-garde-d-crit-001).

//: Les verdicts du préflight sur les tons existants du cockpit (stateIcon).
const PREFLIGHT_TONE = { ok: 'ok', warn: 'warn', bloquant: 'bad', inconnu: '' };
const PREFLIGHT_WORD = { ok: 'ok', warn: 'attention', bloquant: 'bloquant', inconnu: 'inconnu' };
// Le cockpit multi-projets regarde, l'atelier du projet lance (même frontière
// que réclamer une tâche) ; un plan sans empreinte vient d'un serveur plus
// ancien que cette page.
const LAUNCH_REMOTE_REASON = "Lancement depuis l'atelier du projet seulement (grimoire serve) — copie la commande en attendant.";
const LAUNCH_STALE_REASON = 'Plan sans empreinte (serveur plus ancien que la page) : copie la commande en attendant.';
// Les états que le préflight accepte en entrée de file : une tâche prête est
// candidate à l'admission, une proposée reçoit un plan qui dit ce qui manque.
const ASSIGNABLE_STATUS = new Set(['proposed', 'ready']);

function assignPlanNode(ctx, plan, taskId, project, refresh) {
  const panel = document.createElement('div');
  panel.className = 'ex-assign-panel';
  panel.append(text('h4', null, 'Assigner une session'));
  panel.append(text('p', 'ex-assign-sub', 'Tu lances, la session travaille dans son worktree, elle finit par une PR que tu fusionnes.'));

  // Pas prêt : le dire clairement en tête, avec la raison du préflight (ton warn).
  if (plan.pret === false) {
    const notReady = document.createElement('div');
    notReady.className = 'ex-assign-notready';
    notReady.dataset.role = 'assign-not-ready';
    notReady.append(stateIcon('warn'), text('span', null, `Pas prêt : ${plan.raison || 'raison non communiquée'}`));
    panel.append(notReady);
  }

  // Modèle, worktree, branche — les mêmes lignes clé-valeur que le bloc Preuves.
  const rows = document.createElement('div');
  rows.className = 'ex-assign-rows';
  const kv = (label, role, ...nodes) => {
    const line = document.createElement('div');
    line.className = 'ex-kv';
    line.dataset.role = role;
    const value = document.createElement('span');
    value.append(...nodes);
    line.append(text('span', 'lbl', label), value);
    return line;
  };
  const modele = plan.modele || {};
  const modelValue = text('span', null, modele.recommande || 'aucun modèle imposé — la session garde son défaut');
  if (modele.source) modelValue.title = `source : ${modele.source}${modele.palier ? ` · palier ${modele.palier}` : ''}`;
  const modelWhy = text('span', 'ex-cond-why', `classe ${modele.classe || '?'}, ${modele.justification || 'justification non communiquée'}`);
  rows.append(kv('Modèle', 'assign-model', modelValue, modelWhy));
  const worktree = plan.worktree || {};
  const worktreeName = worktree.chemin ? String(worktree.chemin).split('/').filter(Boolean).pop() : '';
  const wtValue = worktreeName
    ? text('code', null, worktreeName)
    : text('span', 'lbl', worktree.detail || 'chemin inconnu');
  if (worktree.chemin) wtValue.title = worktree.chemin;
  const wtNodes = [wtValue];
  if (worktree.existant) wtNodes.push(text('span', 'ex-cond-why', 'worktree déjà créé : la session le reprendrait'));
  rows.append(kv('Worktree', 'assign-worktree', ...wtNodes));
  rows.append(kv('Branche', 'assign-branch', text('code', null, worktree.branche || '?')));
  panel.append(rows);

  // Les cinq vérifications du préflight, chacune avec son verdict.
  const verifsBlock = document.createElement('div');
  verifsBlock.append(text('h4', null, 'Vérifications avant lancement'));
  const list = document.createElement('ul');
  list.className = 'ex-assign-verifs';
  list.dataset.role = 'assign-verifs';
  for (const v of plan.verifications || []) {
    const li = document.createElement('li');
    const tone = PREFLIGHT_TONE[v.verdict] ?? '';
    li.className = 'ex-assign-verif' + (tone ? ' ' + tone : '');
    li.dataset.role = 'assign-verif';
    li.dataset.verif = v.id;
    li.dataset.verdict = v.verdict;
    const body = document.createElement('span');
    body.append(
      text('span', v.verdict === 'bloquant' ? 'ex-cond-strong' : null, `${v.libelle} — ${PREFLIGHT_WORD[v.verdict] || v.verdict}`),
      text('span', 'ex-cond-why', v.detail || ''),
    );
    li.append(stateIcon(tone), body);
    list.append(li);
  }
  if (!(plan.verifications || []).length) verifsBlock.append(text('p', 'lbl', 'aucune vérification lue'));
  else verifsBlock.append(list);
  panel.append(verifsBlock);

  // Lancer : réel (P47 lot 3). L'empreinte du plan montré part dans le corps ;
  // le serveur refuse si l'état a changé depuis (ADR-008) et lance par le
  // lanceur du tick, toutes gardes comprises. Désactivé quand le plan n'est
  // pas prêt, quand on regarde un autre projet depuis le cockpit, ou sans
  // empreinte — la raison est toujours écrite à côté.
  const launchRow = document.createElement('div');
  launchRow.className = 'ex-assign-launch';
  const launch = document.createElement('button');
  launch.type = 'button';
  launch.className = 'btn pri';
  launch.dataset.role = 'assign-launch';
  launch.textContent = 'Lancer la session';
  const launchStatus = actionStatus('');
  const lockReason = text('p', 'lbl', '');
  lockReason.id = 'ex-assign-lock-reason';
  lockReason.dataset.role = 'assign-launch-reason';
  const lock = (why) => {
    launch.disabled = true;
    launch.title = why;
    lockReason.textContent = why;
    launch.setAttribute('aria-describedby', 'ex-assign-lock-reason');
  };
  if (project) lock(LAUNCH_REMOTE_REASON);
  else if (plan.pret === false) lock(`Pas prêt : ${plan.raison || 'raison non communiquée'}`);
  else if (!plan.empreinte) lock(LAUNCH_STALE_REASON);
  else {
    launch.addEventListener('click', async () => {
      launch.disabled = true;
      setActionStatus(launchStatus, 'Lancement en cours…');
      let result;
      try {
        result = await ctx.api.taskLaunch(taskId, plan.empreinte);
      } catch (error) {
        setActionStatus(launchStatus, failureText(error));
        launch.disabled = false;
        return;
      }
      if (result.lance) {
        // La tâche passe réclamée : le pouls fera disparaître le panneau.
        setActionStatus(launchStatus, `Session ${result.session || '?'} lancée — ${result.detail || 'elle travaille dans son worktree.'}`, 'ok');
        return;
      }
      if (result.deja_lancee) {
        setActionStatus(launchStatus, `Session déjà lancée (${result.session || '?'}) : rien n'est relancé.`, 'ok');
        return;
      }
      if (result.empreinte && refresh) {
        // L'état a changé depuis le plan montré : on recharge le plan à jour.
        refresh();
        return;
      }
      setActionStatus(launchStatus, `Lancement refusé : ${result.raison || 'raison non communiquée'}`);
      launch.disabled = false;
    });
  }
  launchRow.append(launch, lockReason, launchStatus);
  panel.append(launchRow);

  // Équivalent : la commande du payload, à copier — même pattern statut +
  // repli (sélection prête pour Ctrl+C) que la vue Décisions.
  const equiv = document.createElement('div');
  equiv.append(text('h4', null, 'Équivalent'));
  const commande = plan.commande || {};
  if (commande.texte) {
    const box = document.createElement('div');
    box.className = 'ex-assign-cmd';
    const code = text('code', null, commande.texte);
    code.dataset.role = 'assign-command';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn';
    copy.dataset.role = 'assign-copy';
    copy.append(icon('copy'), text('span', null, 'Copier la commande'));
    const status = actionStatus('');
    copy.addEventListener('click', async () => {
      // `copyText` appelle le presse-papiers dans le geste même (permission du navigateur).
      if (await copyText(commande.texte, copy)) {
        setActionStatus(status, 'Commande copiée : collez-la dans un terminal pour lancer la session.', 'ok');
        return;
      }
      // Repli : la commande est sélectionnée, prête pour Ctrl+C.
      const selection = window.getSelection();
      if (selection) selection.selectAllChildren(code);
      setActionStatus(status, 'Copie refusée par le navigateur : la commande est sélectionnée, copiez-la (Ctrl+C).');
    });
    box.append(code, copy, status);
    equiv.append(box);
  } else {
    equiv.append(text('p', 'lbl', `commande indisponible : ${commande.raison || 'non composée par le préflight'}`));
  }
  panel.append(equiv);
  return panel;
}

async function renderAssignPanel(ctx, holder, taskId, project) {
  // Chargement : squelette court, remplacé par le plan ou par l'échec nommé.
  const wait = document.createElement('div');
  wait.className = 'skeleton';
  wait.dataset.shape = 'lines';
  wait.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 3; i += 1) wait.append(text('div', 'skeleton-line', ''));
  holder.replaceChildren(text('p', 'lbl sr-only', 'Lecture du plan de lancement…'), wait);
  let plan;
  try {
    plan = await ctx.api.taskLaunchPlan(taskId, project);
  } catch (error) {
    if (ctx.signal.aborted) return;
    holder.replaceChildren(text('p', 'ex-refusal', failureText(error)));
    return;
  }
  // Le pouls a pu fermer le panneau ou changer de tâche pendant la lecture.
  if (ctx.signal.aborted || !(assignUi.open && assignUi.task === taskId)) return;
  holder.replaceChildren(assignPlanNode(ctx, plan, taskId, project, () => renderAssignPanel(ctx, holder, taskId, project)));
}

function assignBlock(ctx, detail, project) {
  if (!ASSIGNABLE_STATUS.has(detail.status)) return null;
  const block = document.createElement('section');
  block.className = 'ex-insp-block';
  block.dataset.role = 'assign-block';
  const holder = document.createElement('div');
  holder.dataset.role = 'assign-panel-holder';
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'btn';
  open.dataset.role = 'assign-open';
  open.append(icon('play'), text('span', null, 'Assigner une session…'));
  const paint = (opened) => {
    open.setAttribute('aria-expanded', String(opened));
    if (opened) renderAssignPanel(ctx, holder, detail.id, project);
    else holder.replaceChildren();
  };
  open.addEventListener('click', () => {
    const opened = !(assignUi.open && assignUi.task === detail.id);
    assignUi.open = opened;
    assignUi.task = opened ? detail.id : '';
    paint(opened);
  });
  block.append(open, holder);
  // Le panneau survit au re-rendu de l'inspecteur (pouls live) : rouvert, il se rafraîchit.
  paint(assignUi.open && assignUi.task === detail.id);
  return block;
}

// P88 : « Accepter… » — l'acceptation humaine de la preuve déclarative (#865), dans la colonne Revue seulement.
// La page demande (ADR-007), le service décide : `accept` passe par le canal humain, comme `answer`, avec le
// motif obligatoire ; Confirmer reste désactivé tant que le motif est vide. Le geste du pilote automatique (P82)
// s'affiche dans le même bloc, en lecture seule.
function acceptBlock(ctx, detail, trace, { taskId, project, portfolio, onWritten }) {
  const verdicts = (trace?.entries || []).filter((e) => e.kind && e.kind.startsWith('verdict.')
    && String(e.detail?.created_by || '').startsWith('orchestrator:'));
  const inReview = detail.board === 'review';
  if (!inReview && !verdicts.length) return null;
  const block = document.createElement('section');
  block.className = 'ex-insp-block';
  block.dataset.role = 'accept-block';
  block.append(text('h4', null, 'Acceptation'));
  for (const v of verdicts) {
    const line = document.createElement('div');
    line.dataset.role = 'autopilot-acceptance';
    line.append(text('div', 'lbl', `Acceptée par le pilote automatique (${v.detail.created_by})`));
    const why = (v.detail.reasons || []).join(' ; ');
    if (why) line.append(text('p', null, why));
    block.append(line);
  }
  if (!inReview) return block;
  const locked = !portfolio && ctx.host.readOnly;
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'btn';
  open.dataset.role = 'accept-open';
  open.textContent = 'Accepter…';
  open.disabled = locked;
  const form = document.createElement('div');
  form.hidden = true;
  form.dataset.role = 'accept-form';
  const reason = document.createElement('textarea');
  reason.className = 'input';
  reason.rows = 3;
  reason.dataset.role = 'accept-reason';
  reason.placeholder = 'Ce qui a été relu, et pourquoi la preuve suffit, ou la dérogation';
  reason.setAttribute('aria-label', "Motif de l'acceptation");
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = 'btn pri';
  confirm.dataset.role = 'accept-confirm';
  confirm.textContent = 'Confirmer';
  confirm.disabled = true;
  const status = actionStatus('');
  status.dataset.role = 'accept-status';
  reason.addEventListener('input', () => { confirm.disabled = !reason.value.trim(); });
  open.addEventListener('click', () => { form.hidden = false; open.hidden = true; reason.focus(); });
  confirm.addEventListener('click', async () => {
    const motif = reason.value.trim();
    if (!motif) return;
    confirm.disabled = true;
    reason.disabled = true;
    const body = { reason: motif };
    ctx.dock.echo(`grimoire task accept ${taskId} --reason ${JSON.stringify(motif)}${portfolio && project ? `  # projet ${project}` : ''}`);
    setActionStatus(status, 'Acceptation…');
    try {
      const result = await (portfolio
        ? ctx.api.portfolioTaskAction(taskId, 'accept', body, project)
        : ctx.api.taskAction(taskId, 'accept', body));
      if (result && result.blocked) throw new Error((result.refusals || []).map((r) => r.reason || r.evidence).join(' ; ') || 'acceptation refusée');
      setActionStatus(status, 'Tâche acceptée.', 'ok');
      onWritten();
    } catch (error) {
      reason.disabled = false;
      confirm.disabled = !reason.value.trim();
      setActionStatus(status, failureText(error), 'bad');
    }
  });
  form.append(reason, confirm, status);
  block.append(open, form);
  return block;
}

async function renderInspector(ctx, task, onWritten, onTimeline, portfolio = false, guard = {}) {
  const taskId = task.id;
  const project = portfolio ? task.project?.slug : undefined;
  const [detail, trace, recall, receipts] = await Promise.all([
    ctx.api.task(taskId, project).catch(() => null),
    ctx.api.taskTrace(taskId, project).catch(() => null),
    ctx.api.taskRecall(taskId, project).catch(() => null),
    // Une lecture ratée garde son erreur : « lecture impossible » ≠ « aucune lecture ».
    ctx.api.taskReceipts(taskId, project).catch((error) => ({ failed: error })),
  ]);
  if (ctx.signal.aborted) return false;
  if (guard.defer && guard.defer()) return false;
  const kept = snapshotFields(ctx.inspector);
  ctx.inspector.replaceChildren();
  ctx.inspector.dataset.owner = 'executer';
  ctx.inspector.dataset.task = taskId;
  if (!detail) {
    ctx.inspector.append(text('p', 'lbl', 'Tâche indisponible.'));
    return true;
  }

  // ── En-tête : identifiant, état, titre ──────────────────────────────────
  const waitingNow = detail.status === 'waiting' && detail.waiting;
  const stateLabel = waitingNow ? waitingText(detail.waiting) : (COLUMN_LABEL[detail.board] || detail.board);
  const head = document.createElement('div');
  head.className = 'ex-insp-head';
  // P74 : le préfixe interne quitte aussi le titre de l'inspecteur ; il reste
  // ici, en métadonnée discrète à côté de l'identifiant complet.
  const { title: shownTitle, ref: titleRef } = titleParts(detail.title || detail.id);
  const headRow = row(
    text('span', 'lbl mono', detail.id),
    badge(stateLabel, waitingNow ? 'warn' : STATE_TONE[detail.board]),
  );
  headRow.classList.add('ex-insp-idrow');
  if (titleRef) {
    const refNode = text('span', 'lbl mono', titleRef);
    refNode.dataset.role = 'title-ref';
    refNode.title = `identifiant interne du titre : ${detail.title}`;
    headRow.insertBefore(refNode, headRow.children[1]);
  }
  appendIf(headRow, moreMenu(ctx, detail, onWritten));
  head.append(headRow);
  head.append(text('h3', null, shownTitle));
  // P74 : les deux temps, nommés — l'âge dans la colonne et l'âge total.
  const inColumnAge = ageText(task.in_state_since || detail.in_state_since || '');
  const totalAge = ageText(detail.created_at || '');
  if (inColumnAge || totalAge) {
    const ages = text('div', 'lbl', [
      inColumnAge ? `dans ${COLUMN_LABEL[detail.board] || detail.board} ${inColumnAge}` : '',
      totalAge ? `ouverte ${totalAge}` : '',
    ].filter(Boolean).join(' · '));
    ages.dataset.role = 'task-ages';
    ages.title = 'Âge dans la colonne courante (dernière transition), puis âge total (depuis l’ouverture).';
    head.append(ages);
  }
  ctx.inspector.append(head);
  if (portfolio) ctx.inspector.append(text('div', 'lbl', `Projet : ${task.project?.name || task.project?.slug || '—'}`));

  if (waitingNow) {
    const waitBlock = document.createElement('div');
    waitBlock.className = 'ex-insp-block';
    waitBlock.dataset.role = 'waiting-block';
    waitBlock.append(text('h4', null, 'En attente'));
    waitBlock.append(row(waitGlyph(detail.waiting), text('span', null, waitingText(detail.waiting))));
    if (detail.waiting.since) waitBlock.append(text('div', 'lbl', ageText(detail.waiting.since)));
    if (isHumanWait(detail)) {
      waitBlock.append(decisionCard(ctx, { ...detail, project: task.project }, portfolio, onWritten));
    }
    ctx.inspector.append(waitBlock);
  }

  // ── Carte « Pour passer en … » : d'abord les gates, puis les gestes (P11) ──
  // Chaque passage déclaré depuis la colonne (`next_moves_require`) avec ses
  // conditions vertes ou rouges, leur raison et leur remède (`gate_checks`), et
  // le dernier `task.gate.refused` : tout est lisible AVANT le bouton.
  const gate = detail.next_gate || null;
  const checks = detail.gate_checks || {};
  const requirements = detail.next_moves_require || {};
  const gateBlock = document.createElement('section');
  gateBlock.className = 'ex-gate-card';
  gateBlock.dataset.role = 'gate-card';
  const gateHead = text('span', 'micro', '');
  gateHead.dataset.term = 'porte-de-preuve';
  if (gate) {
    gateHead.append('Pour passer en ', text('strong', null, COLUMN_LABEL[gate.to] || gate.to));
  } else {
    gateHead.textContent = 'Passage à la colonne suivante';
  }
  gateBlock.append(gateHead);

  const gatesBox = document.createElement('div');
  gatesBox.className = 'ex-gates';
  gatesBox.dataset.role = 'gates';
  appendIf(gatesBox, lastRefusalLine(detail.last_gate_refusal));
  // Le passage suivant d'abord (celui de l'en-tête), puis les autres passages
  // déclarés qui exigent quelque chose ; un passage sans exigence n'a rien à lister.
  const primary = gate ? (checks[gate.to] || gate) : null;
  if (primary && primary.conditions.length) {
    gatesBox.append(conditionList(primary));
  } else if (gate) {
    gatesBox.append(text('p', 'lbl', 'aucune condition déclarée pour ce passage'));
  }
  for (const [to, entry] of Object.entries(checks)) {
    if ((gate && to === gate.to) || !entry.conditions.length) continue;
    const sub = text('div', 'ex-gates-sub micro', '');
    sub.append('Pour passer en ', text('strong', null, COLUMN_LABEL[to] || to));
    gatesBox.append(sub, conditionList(entry));
  }
  gateBlock.append(gatesBox);

  const targets = Object.keys(requirements);
  // Le ledger permet de bloquer une tâche prête ou en cours même quand le
  // fichier de portes ne déclare aucune transition vers `blocked` : le geste
  // reste offert (avec sa raison), sans preuve requise par une porte.
  if (nextMoves(detail).canBlock && !targets.includes('blocked')) targets.push('blocked');
  if (!targets.length) {
    gateBlock.append(text('p', 'lbl', 'colonne terminale : aucune transition déclarée depuis ici'));
  }
  const moves = document.createElement('div');
  moves.className = 'ex-moves';
  moves.dataset.role = 'transitions';
  const feedback = document.createElement('div');
  feedback.dataset.role = 'transition-feedback';
  feedback.setAttribute('aria-live', 'polite');
  for (const target of targets) {
    const gateRow = document.createElement('div');
    gateRow.className = 'ex-gate-row';
    gateRow.dataset.target = target;
    const required = requirements[target] || [];
    // Les conditions de ce passage sont déjà listées plus haut, avec leur état.
    if (!(checks[target] && checks[target].conditions.length) && !(gate && gate.to === target && gate.conditions.length)) {
      gateRow.append(text('div', 'ex-gate-req', required.length ? `requiert : ${required.join(', ')}` : 'aucune preuve requise'));
    }

    let reasonInput = null;
    if (target === 'blocked') {
      reasonInput = document.createElement('input');
      reasonInput.type = 'text';
      reasonInput.className = 'input';
      reasonInput.placeholder = 'raison du blocage';
      reasonInput.dataset.role = 'block-reason';
      reasonInput.setAttribute('aria-label', 'Raison du blocage');
      gateRow.append(reasonInput);
    }

    // En portefeuille, le verrou `readOnly` général ne s'applique pas : l'action
    // suit la dérogation nommée du serveur (`is_registry_scoped_write`), qui
    // route vers le projet propriétaire — même précédent que « Migrer les
    // tâches » (#560) et que les boutons Accepter/Refuser de Piloter.
    const locked = !portfolio && ctx.host.readOnly;
    const btn = document.createElement('button');
    btn.type = 'button';
    // Un seul bouton primaire : le passage suivant (celui que les gates ci-dessus décrivent en tête).
    btn.className = target === targets.find((t) => t !== 'blocked') ? 'btn pri' : 'btn';
    btn.dataset.role = 'transition';
    btn.textContent = locked ? 'Écriture désactivée (cockpit)' : (target === 'blocked' ? 'Réaliser : bloquer' : `Réaliser : → ${COLUMN_LABEL[target] || target}`);
    btn.disabled = locked;
    if (locked) {
      gateRow.append(text('div', 'lbl', "le cockpit est en lecture seule : ouvrez l'atelier de ce projet, ou la portée « Tous les projets »"));
    }
    const send = (action, body) => (portfolio
      ? ctx.api.portfolioTaskAction(taskId, action, body, project)
      : ctx.api.taskAction(taskId, action, body));
    const realise = async () => {
      const [action, body, command] = actionFor(target, detail, reasonInput?.value || '');
      ctx.dock.echo(portfolio && project ? `${command}  # projet ${project}` : command);
      // P57 : un passage dont le ledger connaît le chemin retour s'annule
      // 10 s durant ; l'annulation rappelle `move` vers l'état de départ.
      const back = reverseMove(detail.status, target);
      try {
        const result = await send(action, body);
        feedback.replaceChildren();
        if (result && result.blocked) {
          feedback.append(refusalNode(result.refusals || []));
        } else {
          feedback.append(text('div', 'lbl', `transition : ${result.transition}`));
          onWritten();
          if (back) {
            const from = COLUMN_LABEL[detail.board] || detail.board;
            undoToast(`${taskId} : ${from} → ${COLUMN_LABEL[target] || target}.`, {
              undone: `${taskId} est revenue en ${from}.`,
              undo: async () => {
                ctx.dock.echo(`grimoire task move ${taskId} --to ${back}${portfolio && project ? `  # projet ${project}` : ''}`);
                const undone = await send('move', { to: back });
                if (undone && undone.blocked) {
                  throw new Error((undone.refusals || []).map((r) => r.reason || r.evidence).join(' ; ') || 'passage refusé');
                }
                onWritten();
              },
            });
          }
        }
      } catch (error) {
        feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
      }
    };
    btn.addEventListener('click', () => {
      // Accepter clôt la tâche pour de bon (le ledger n'a aucun chemin retour) :
      // pas de toast, une confirmation sur place.
      if (!IRREVERSIBLE_TARGETS.has(target)) { realise(); return; }
      btn.hidden = true;
      const confirmBox = document.createElement('div');
      confirmBox.className = 'ex-confirm';
      confirmBox.dataset.role = 'transition-confirm';
      confirmBox.setAttribute('role', 'group');
      confirmBox.setAttribute('aria-label', `Confirmer : → ${COLUMN_LABEL[target] || target}`);
      const yes = document.createElement('button');
      yes.type = 'button';
      yes.className = 'btn pri';
      yes.dataset.role = 'transition-confirm-yes';
      yes.textContent = `Confirmer : → ${COLUMN_LABEL[target] || target}`;
      const no = document.createElement('button');
      no.type = 'button';
      no.className = 'btn';
      no.textContent = 'Annuler';
      confirmBox.append(text('p', 'lbl', `${COLUMN_LABEL[target] || target} clôt la tâche : ce passage ne se défait pas.`), yes, no);
      no.addEventListener('click', () => { confirmBox.remove(); btn.hidden = false; btn.focus(); });
      yes.addEventListener('click', () => { confirmBox.remove(); btn.hidden = false; realise(); });
      btn.after(confirmBox);
      no.focus();
    });
    gateRow.append(btn);
    moves.append(gateRow);
  }
  gateBlock.append(moves, feedback);
  ctx.inspector.append(gateBlock);
  appendIf(ctx.inspector, acceptBlock(ctx, detail, trace, { taskId, project, portfolio, onWritten }));

  // ── Critères d'acceptation : ce que la transition doit prouver ──────────
  const acceptance = document.createElement('div');
  acceptance.className = 'ex-insp-block';
  acceptance.dataset.role = 'acceptance-block';
  acceptance.append(text('h4', null, "Critères d'acceptation"));
  const ul = document.createElement('ul');
  for (const item of detail.acceptance || []) ul.append(text('li', null, item));
  if (!(detail.acceptance || []).length) acceptance.append(text('p', 'lbl', 'aucun critère déclaré'));
  else acceptance.append(ul);
  ctx.inspector.append(acceptance);

  // ── Preuves : ce que le kit sait, ligne à ligne ─────────────────────────
  const hasEvidencePack = (trace?.entries || []).some((e) => e.kind === 'evidence.pack');
  const evidenceBlock = document.createElement('div');
  evidenceBlock.className = 'ex-insp-block';
  evidenceBlock.dataset.role = 'evidence-block';
  const evHead = text('h4', null, 'Preuves');
  evHead.dataset.term = 'evidence-pack';
  evidenceBlock.append(evHead);
  const kv = (label, ...nodes) => {
    const line = document.createElement('div');
    line.className = 'ex-kv';
    line.append(text('span', 'lbl', label), (() => { const v = document.createElement('span'); v.append(...nodes); return v; })());
    return line;
  };
  evidenceBlock.append(kv('Pack', hasEvidencePack ? 'présent (voir la timeline)' : 'aucun pack de preuve lu'));
  // Routage (artboard Travail) : classe · modèle · coût, le coût inconnu écrit « inconnu ».
  if (detail.verifiability_class) {
    const routing = text('span', null, `classe ${routeText(detail)} · ${costLabel(task.cost)}`);
    routing.dataset.role = 'routing';
    routing.title = routeTitle(detail);
    const costNote = text('span', 'ex-cond-why', task.cost && task.cost.calls
      ? `${task.cost.calls} tentative(s) de dispatch. Le coût de la session hôte n'est mesuré nulle part dans le kit.`
      : "Aucun dispatch sur cette tâche : le kit n'a rien mesuré, ce qui n'est pas un coût nul.");
    evidenceBlock.append(kv('Routage', routing, costNote));
  }
  const chips = document.createElement('div');
  chips.className = 'ex-card-chips';
  for (const evidence of detail.expected_evidence || []) chips.append(chip(evidence, hasEvidencePack));
  if (!(detail.expected_evidence || []).length) chips.append(text('span', 'lbl', 'aucune preuve déclarée'));
  evidenceBlock.append(kv('Attendues', chips));
  if (task.session && task.session.id) {
    evidenceBlock.append(kv('Session', sessionGlyph(task.session), text('span', 'mono', ` ${task.session.id.slice(0, 8)}… `), text('span', null, `${task.session.host ? (HOST_LABEL[task.session.host] || task.session.host) + ' · ' : ''}${livenessLabel(task.session)}`)));
  }
  ctx.inspector.append(evidenceBlock);

  // ── Session : vivacité, signes de vie, reprise ──────────────────────────
  const sessionWrap = document.createElement('div');
  sessionWrap.dataset.role = 'session-block';
  if (task.session && task.session.id) {
    // Portée projet : la reprise reste dans le bloc « Session » du claim, juste dessous.
    sessionWrap.append(sessionBlock(task.session, { resume: portfolio }));
  }
  // Session (issue #638, lot A) : la session d'hôte qui porte le claim, et la
  // commande qui la reprend quand l'hôte en a une — `resume_command` vient
  // du serveur, cette vue ne compose jamais une commande elle-même.
  if (detail.session_id) {
    const sessionHold = document.createElement('div');
    sessionHold.className = 'ex-insp-block';
    sessionHold.append(text('h4', null, 'Session'));
    sessionHold.append(sessionLine(detail, { full: true }));
    if (detail.resume_command) {
      const resumeRow = row();
      resumeRow.append(Object.assign(document.createElement('code'), { textContent: detail.resume_command }));
      // Le dock n'est plus affiché hors atelier (P62) : y écrire seul laissait
      // ce bouton sans effet visible. Il copie, et le dit sous la commande.
      const resumeBtn = document.createElement('button');
      resumeBtn.type = 'button';
      resumeBtn.className = 'btn';
      resumeBtn.textContent = 'Copier la commande';
      resumeBtn.title = 'Copier la commande de reprise, à coller dans un terminal';
      const resumeStatus = actionStatus();
      resumeBtn.addEventListener('click', async () => {
        ctx.dock.echo(detail.resume_command);
        try {
          await navigator.clipboard.writeText(detail.resume_command);
          setActionStatus(resumeStatus, 'Commande copiée : collez-la dans un terminal pour reprendre la session.', 'ok');
        } catch {
          setActionStatus(resumeStatus, 'Copie refusée par le navigateur : recopiez la commande ci-dessus.', 'bad');
        }
      });
      resumeRow.append(resumeBtn);
      sessionHold.append(resumeRow, resumeStatus);
    } else {
      sessionHold.append(text('p', 'lbl', 'aucune commande de reprise connue pour cet hôte'));
    }
    sessionWrap.append(sessionHold);
  }
  if (!sessionWrap.childElementCount) {
    const none = document.createElement('div');
    none.className = 'ex-insp-block';
    none.append(text('h4', null, 'Session'), text('p', 'lbl', "aucune session d'hôte n'a encore travaillé sous ce claim"));
    sessionWrap.append(none);
  }
  ctx.inspector.append(sessionWrap);

  // ── P75 : qui travaille — persona, modèle réel, effort, skills, tokens ──
  if (task.work) ctx.inspector.append(workBlock(task));

  // ── Assigner une session (P47) : le plan de lancement, sans rien lancer ──
  appendIf(ctx.inspector, assignBlock(ctx, detail, project));

  // ── Mémoire servie : le reçu de rappel, rejouable (P27) ─────────────────
  ctx.inspector.append(receiptBlock(ctx, taskId, receipts, project));

  // ── Consigne à la session, priorité (gestes existants) ──────────────────
  renderSteering(ctx, detail, onWritten);

  // ── Activité (P69v2, artboard Travail « Derniers événements ») ──────────
  // Le fil de l'agent dérivé du ledger (claim, transitions, portes,
  // questions/réponses, notes de progression), le même bloc que l'inspecteur
  // de session (`activity.js`), avec la commande `claude logs` à copier.
  const eventCount = (trace?.entries || []).length;
  const eventsBlock = document.createElement('div');
  eventsBlock.className = 'ex-insp-block';
  eventsBlock.dataset.role = 'last-events';
  eventsBlock.append(activityBlockAsync(
    () => ctx.api.taskFeed(taskId, project),
    { heading: 'Derniers événements', signal: ctx.signal },
  ));
  // Drill-down (#139) : la timeline complète de la tâche sans passer par le sélecteur de vue.
  const timelineBtn = document.createElement('button');
  timelineBtn.type = 'button';
  timelineBtn.className = 'btn ghost';
  timelineBtn.textContent = eventCount ? `Voir la timeline (${eventCount})` : 'Voir la timeline';
  timelineBtn.addEventListener('click', () => onTimeline(taskId));
  eventsBlock.append(timelineBtn);
  ctx.inspector.append(eventsBlock);

  // Finition (lot 4.3, issue #561) : lecture seule — absente (`""`) tant que
  // personne ne l'a posée : pas de ligne plutôt qu'une valeur vide.
  if (detail.finition) {
    ctx.inspector.append(text('div', 'lbl', `Finition : ${FINITION_LABEL[detail.finition] || detail.finition}`));
  }

  // Rappel (#141) : avec parcimonie — le bloc n'existe pas quand il n'a rien à dire.
  if (recall && recall.has_content) {
    const recallBlock = document.createElement('div');
    recallBlock.className = 'ex-insp-block';
    recallBlock.append(text('h4', null, 'Rappel'));
    const pre = document.createElement('pre');
    pre.className = 'ex-recall';
    pre.textContent = recall.text;
    recallBlock.append(pre);
    ctx.inspector.append(recallBlock);
  }

  // Corps réel de la tâche (#140) : de quoi elle parle.
  if (detail.description) {
    const description = document.createElement('div');
    description.className = 'ex-insp-block';
    description.dataset.role = 'description-block';
    description.append(text('h4', null, 'Description'));
    const pre = document.createElement('pre');
    pre.className = 'ex-recall';
    pre.textContent = detail.description;
    description.append(pre);
    ctx.inspector.append(description);
  }

  if ((detail.guardrails || []).length) {
    const guardrailsBlock = document.createElement('div');
    guardrailsBlock.className = 'ex-insp-block';
    guardrailsBlock.append(text('h4', null, 'Garde-fous'));
    const gUl = document.createElement('ul');
    for (const item of detail.guardrails) gUl.append(text('li', null, item));
    guardrailsBlock.append(gUl);
    ctx.inspector.append(guardrailsBlock);
  }

  // Dépendances (#140) : une tâche `blocks` une autre est le cas qui compte le plus, en premier.
  if ((detail.dependencies || []).length) {
    const depsBlock = document.createElement('div');
    depsBlock.className = 'ex-insp-block';
    depsBlock.append(text('h4', null, 'Dépendances'));
    const dUl = document.createElement('ul');
    const deps = [...detail.dependencies].sort((a, b) => (a.kind === 'blocks' ? -1 : b.kind === 'blocks' ? 1 : 0));
    for (const dep of deps) {
      const label = DEP_LABEL[dep.kind] || dep.kind;
      dUl.append(text('li', dep.kind === 'blocks' ? 'ex-refusal' : null, `${label} ${dep.target}`));
    }
    depsBlock.append(dUl);
    ctx.inspector.append(depsBlock);
  }
  restoreFields(ctx.inspector, kept);
  if (focusBlockReason) {
    focusBlockReason = false;
    const reasonField = gateBlock.querySelector('[data-role="block-reason"]');
    if (reasonField) reasonField.focus();
  }
  return true;
}

// ── Pilotage humain (issue #638, lot B) ───────────────────────────────────
//
// Deux blocs dans l'inspecteur : la priorité (sélecteur) et les consignes
// (journal + zone de saisie). L'annulation vit dans le menu « ⋯ » (P11,
// `moreMenu`). Chaque geste est un `api.taskAction` ; le refus revient en 200
// `blocked: true`.

function renderSteering(ctx, detail, onWritten) {
  // Conteneur dédié (issue #638, lot B, correctif de course résiduelle) :
  // priorité, consignes et annulation vivent dans LEUR PROPRE bloc, que
  // `send()` réécrit en place avec la réponse déjà en main plutôt que
  // d'attendre le rafraîchissement externe de l'inspecteur (`onWritten`, qui
  // refait un aller-retour réseau puis remplace tout l'inspecteur). Sans ce
  // conteneur, un second geste (poser une consigne juste après avoir changé
  // la priorité, ou annuler juste après une consigne) pouvait tomber dans la
  // fenêtre où l'inspecteur venait d'être vidé pour ce second rendu externe :
  // le clic ne trouvait plus de bouton où se poser, et rien n'était écrit
  // côté serveur, sans erreur visible (`test_workspace_executer_steering.py`,
  // course reproduite en CI sur `cancel` puis sur `comment`).
  const container = document.createElement('div');
  container.className = 'ex-steering-root';
  renderSteeringInto(container, ctx, detail, onWritten);
  ctx.inspector.append(container);
}

function renderSteeringInto(container, ctx, detail, onWritten) {
  const readOnly = ctx.host.readOnly;
  const feedback = document.createElement('div');
  const send = async (action, body, command) => {
    ctx.dock.echo(command);
    feedback.replaceChildren();
    try {
      const result = await ctx.api.taskAction(detail.id, action, body);
      if (result && result.blocked) {
        const refusals = (result.refusals || []).map((r) => `${r.evidence} — ${r.reason} (${r.remedy})`);
        feedback.append(text('div', 'ex-refusal', 'refusé : ' + refusals.join(' ; ')));
        return result;
      }
      // Réécrit ce bloc en place avec `result` — la réponse porte déjà la
      // tâche à jour (priorité, consignes, board) — au lieu d'attendre le
      // rafraîchissement externe. Celui-ci reste déclenché (badges et tri du
      // board), mais en mode « board seul » : il ne touche plus jamais ce
      // conteneur, donc ne peut plus le vider sous un geste qui suit.
      container.replaceChildren();
      renderSteeringInto(container, ctx, result, onWritten);
      onWritten({ skipInspector: true });
      return result;
    } catch (error) {
      feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
      return null;
    }
  };

  // Priorité.
  const prioBlock = document.createElement('div');
  prioBlock.className = 'ex-insp-block';
  prioBlock.append(text('h4', null, 'Priorité'));
  const select = document.createElement('select');
  select.className = 'input';
  select.dataset.role = 'priority';
  select.disabled = readOnly;
  const current = detail.effective_priority || 'medium';
  for (const value of PRIORITIES) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = PRIORITY_LABEL[value] || value;
    if (value === current) option.selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => {
    send('prioritize', { to: select.value }, `grimoire task prioritize ${detail.id} --to ${select.value}`);
  });
  const prioRow = row(select, text('span', 'lbl', detail.priority ? 'déclarée' : 'dérivée du profil de risque'));
  prioRow.classList.add('ex-prio-row');
  prioBlock.append(prioRow);
  container.append(prioBlock);

  // Consignes.
  const dirBlock = document.createElement('div');
  dirBlock.className = 'ex-insp-block';
  dirBlock.append(text('h4', null, "Consignes de l'orchestrateur"));
  const directives = detail.directives || [];
  if (!directives.length) dirBlock.append(text('p', 'lbl', 'aucune consigne posée'));
  for (const d of directives) {
    const node = document.createElement('div');
    node.className = 'ex-directive' + (d.delivered_at ? '' : ' unread');
    node.dataset.role = 'directive';
    // P74 : « non lue » était du jargon — la consigne est posée au ledger,
    // c'est la session qui ne l'a pas encore reçue.
    const status = d.acknowledged_at ? 'accusée' : (d.delivered_at ? 'livrée' : 'en attente de la session');
    const metaLine = text('div', 'ex-directive-meta', `${d.kind} · ${d.author} · ${(d.created_at || '').slice(0, 16)} · ${status}`);
    if (!d.delivered_at) metaLine.title = 'Consigne posée au ledger, pas encore livrée à la session qui tient la tâche.';
    node.append(text('div', null, d.text));
    node.append(metaLine);
    dirBlock.append(node);
  }
  if (!readOnly) {
    const form = document.createElement('div');
    form.className = 'ex-steer-form';
    const area = document.createElement('textarea');
    area.className = 'input';
    area.placeholder = 'Commentaire ou consigne pour la session qui tient cette tâche';
    area.dataset.role = 'directive-text';
    const kind = document.createElement('select');
    kind.className = 'input';
    for (const [value, label] of [['comment', 'Commentaire'], ['directive', 'Consigne']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      kind.append(option);
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Poser';
    btn.dataset.role = 'directive-submit';
    btn.addEventListener('click', async () => {
      const value = area.value.trim();
      if (!value) { feedback.replaceChildren(text('div', 'ex-refusal', 'une consigne vide n\'a rien à dire')); return; }
      const result = await send('comment', { text: value, kind: kind.value }, `grimoire task comment ${detail.id} "${value}" --kind ${kind.value}`);
      if (result && !result.blocked) area.value = '';
    });
    form.append(area, row(kind, btn));
    dirBlock.append(form);
  }
  container.append(dirBlock);

  container.append(feedback);
}

// Le menu « ⋯ » de l'inspecteur (P11) : les gestes rares et destructifs, hors
// du chemin de la transition. Seul « Annuler la tâche » y vit : raison exigée,
// `force` proposé seulement quand le service a refusé pour un claim tenu
// ailleurs. Aucun menu en lecture seule ni en colonne terminale : le cockpit ne
// montre pas un geste qu'il ne laisserait pas partir.
const CANCEL_TERMINAL = new Set(['archived', 'accepted', 'released']);
function moreMenu(ctx, detail, onWritten) {
  if (ctx.host.readOnly || CANCEL_TERMINAL.has(detail.board)) return null;
  const reason = document.createElement('input');
  // Le composant de la coque (P56 le partage avec les lignes, P55 le pose sur
  // la Popover API) ; une raison déjà saisie survit à un clic à côté : le
  // champ la garde et la retrouve à la réouverture du menu.
  const menu = sharedMoreMenu({
    id: 'ex-more-panel',
    onOpen: () => reason.focus(),
  });
  const { panel } = menu;
  menu.wrap.classList.add('ex-more');
  menu.toggle.classList.add('ex-more-btn');
  panel.classList.add('ex-more-panel');
  panel.append(text('h4', null, 'Annuler la tâche'));
  reason.type = 'text';
  reason.className = 'input';
  reason.placeholder = "raison de l'annulation (obligatoire)";
  reason.dataset.role = 'cancel-reason';
  reason.setAttribute('aria-label', "Raison de l'annulation");

  const force = document.createElement('input');
  force.type = 'checkbox';
  force.dataset.role = 'cancel-force';
  const forceLabel = document.createElement('label');
  forceLabel.className = 'lbl';
  forceLabel.append(force, text('span', null, ' forcer même si une session la tient'));
  forceLabel.hidden = true;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = 'Annuler la tâche';
  btn.dataset.role = 'cancel-submit';
  btn.disabled = true;
  const feedback = document.createElement('div');
  feedback.setAttribute('aria-live', 'polite');
  reason.addEventListener('input', () => { btn.disabled = !reason.value.trim(); });
  btn.addEventListener('click', async () => {
    const body = { reason: reason.value.trim() };
    if (force.checked) body.force = true;
    ctx.dock.echo(`grimoire task cancel ${detail.id} --reason "${body.reason}"${force.checked ? ' --force' : ''}`);
    feedback.replaceChildren();
    try {
      const result = await ctx.api.taskAction(detail.id, 'cancel', body);
      if (result && result.blocked) {
        feedback.append(refusalNode(result.refusals || []));
        // Le refus « claim » (tenue par une autre session) ouvre l'option de
        // forcer — jamais avant : forcer n'est pas le geste par défaut.
        if ((result.refusals || []).some((r) => r.evidence === 'claim')) forceLabel.hidden = false;
        return;
      }
      // La saisie est consommée : le re-rendu (colonne terminale, plus de menu) n'attend plus rien.
      reason.value = '';
      onWritten();
    } catch (error) {
      feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
    }
  });
  panel.append(reason, forceLabel, btn, feedback);
  return menu.wrap;
}

// P57 : les passages que le ledger sait défaire (`_TASK_TRANSITIONS`), état de
// départ → colonne visée → état où l'annulation ramène. Bloquer une tâche prête
// se défait (blocked → ready) ; remettre en revue une tâche qui tournait aussi
// (needs_verification → running). Bloquer une tâche réclamée ne revient qu'à
// « prête », pas à son claim : pas d'annulation. Accepter ne se défait jamais.
const REVERSIBLE_MOVES = { ready: { blocked: 'ready' }, running: { review: 'running' } };
const IRREVERSIBLE_TARGETS = new Set(['accepted']);

function reverseMove(status, target) {
  return (REVERSIBLE_MOVES[status] || {})[target] || null;
}

function actionFor(target, task, reason) {
  if (target === 'in_progress' && !task.claim) {
    return ['claim', {}, `grimoire task claim ${task.id}`];
  }
  if (target === 'blocked') {
    return ['block', { reason }, `grimoire task block ${task.id} --reason "${reason}"`];
  }
  if (target === 'accepted') {
    return ['close', {}, `grimoire task close ${task.id}`];
  }
  const state = BOARD_TO_STATE[target] || target;
  return ['move', { to: state }, `grimoire task move ${task.id} --to ${state}`];
}

// Les filtres du portefeuille (#638). La portée elle-même n'est plus choisie
// ici (P31) : c'est celle de la coque, « Tous les projets » ou un projet —
// `ctx.scope`. Hors portefeuille il n'y a rien à filtrer : pas de barre.
function scopeToolbar(ctx, portfolio, onChange) {
  if (scope.mode !== 'portfolio' || !portfolio) return null;
  const bar = document.createElement('div');
  bar.className = 'ex-toolbar';
  const stateLabel = document.createElement('label');
  stateLabel.append(text('span', null, 'État'));
  const stateSelect = document.createElement('select');
  stateSelect.className = 'input';
  stateSelect.dataset.role = 'state';
  const stateOptions = [['', 'Tous les états'], ...LIFECYCLE.map((c) => [c, COLUMN_LABEL[c]])];
  for (const [value, label] of stateOptions) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === scope.state) option.selected = true;
    stateSelect.append(option);
  }
  stateSelect.addEventListener('change', () => { scope.state = stateSelect.value; onChange(); });
  stateLabel.append(stateSelect);

  const projectLabel = document.createElement('label');
  projectLabel.append(text('span', null, 'Projet'));
  const projectSelect = document.createElement('select');
  projectSelect.className = 'input';
  projectSelect.dataset.role = 'project';
  const projectOptions = [['', 'Tous les projets'], ...(portfolio.projects || []).map((p) => [p.slug, p.name || p.slug])];
  for (const [value, label] of projectOptions) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === scope.slug) option.selected = true;
    projectSelect.append(option);
  }
  projectSelect.addEventListener('change', () => { scope.slug = projectSelect.value; onChange(); });
  projectLabel.append(projectSelect);

  const liveLabel = document.createElement('label');
  const liveBox = document.createElement('input');
  liveBox.type = 'checkbox';
  liveBox.dataset.role = 'live';
  liveBox.checked = scope.live;
  liveBox.addEventListener('change', () => { scope.live = liveBox.checked; onChange(); });
  liveLabel.append(liveBox, text('span', null, `Sessions vivantes (< ${portfolio.live_minutes} min)`));

  const summary = portfolio.summary || {};
  const count = text('span', 'ex-count', `${portfolio.count} / ${summary.tasks ?? 0} tâche(s) · ${(summary.liveness || {}).vivant ?? 0} session(s) vivante(s) · ${summary.readable ?? 0}/${summary.projects ?? 0} projet(s) lisible(s)`);
  bar.append(stateLabel, projectLabel, liveLabel, count);
  return bar;
}

// P91 : combien de cartes ouvertes par présence — les cinq états sont exclusifs,
// leur somme est donc le nombre de cartes ouvertes.
function presenceTally(tasks) {
  const counts = Object.fromEntries(PRESENCE_ORDER.map((k) => [k, 0]));
  for (const t of tasks) if (counts[presenceOf(t)] !== undefined) counts[presenceOf(t)] += 1;
  return counts;
}

function presenceFilterBar(counts, onChange) {
  if (!PRESENCE_ORDER.some((k) => counts[k])) return null;
  const bar = document.createElement('div');
  bar.className = 'ex-presence-filter';
  bar.dataset.role = 'presence-filter';
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', 'Filtrer par présence');
  for (const state of PRESENCE_ORDER) {
    const ui = PRESENCE_UI[state];
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip';
    btn.dataset.state = state;
    btn.setAttribute('aria-pressed', String(presenceFilter.state === state));
    btn.append(glyph(ui.icon, ui.word, ui.tone), text('span', null, ui.word), text('span', 'mono lbl', String(counts[state])));
    btn.addEventListener('click', () => { presenceFilter.state = presenceFilter.state === state ? '' : state; onChange(); });
    bar.append(btn);
  }
  return bar;
}

function appendIf(host, child) {
  if (child) host.append(child);
}

// Les projets du registre que le portefeuille n'a pas pu lire — nommés avec
// leur raison, jamais tus (leçon #264, même règle que l'aperçu mémoire).
function unreadableNotice(portfolio) {
  const failed = (portfolio.projects || []).filter((p) => p.state !== 'ok');
  if (!failed.length) return null;
  const notice = document.createElement('div');
  notice.className = 'ex-notice';
  for (const p of failed) {
    notice.append(row(dot(p.state === 'no_ledger' ? 'warn' : 'bad'), text('span', null, `${p.name || p.slug} (${p.slug}) — ${p.reason || p.state}`)));
  }
  return notice;
}

// Ce qu'une actualisation conserve : défilement de la toile et du board / de la
// liste, et la carte qui avait le focus clavier.
function captureView(wrap) {
  const canvas = wrap.parentElement;
  const board = wrap.querySelector('.ex-board, .ex-list-wrap');
  const focus = document.activeElement && document.activeElement.closest ? document.activeElement.closest('.ex-card, tr[data-task]') : null;
  // Décisions (P68) : un bouton de choix, de filtre ou de lot garde le focus après une actualisation.
  const keyed = document.activeElement && wrap.contains(document.activeElement) ? document.activeElement.dataset?.focusKey || '' : '';
  return {
    focusKey: keyed,
    canvasTop: canvas ? canvas.scrollTop : 0,
    wrapTop: wrap.scrollTop,
    boardLeft: board ? board.scrollLeft : 0,
    boardTop: board ? board.scrollTop : 0,
    focus: focus && wrap.contains(focus) ? { task: focus.dataset.task || '', project: focus.dataset.project || '' } : null,
  };
}

function restoreView(wrap, kept) {
  const canvas = wrap.parentElement;
  if (canvas) canvas.scrollTop = kept.canvasTop;
  wrap.scrollTop = kept.wrapTop;
  const board = wrap.querySelector('.ex-board, .ex-list-wrap');
  if (board) { board.scrollLeft = kept.boardLeft; board.scrollTop = kept.boardTop; }
  if (kept.focus && kept.focus.task) {
    const same = [...wrap.querySelectorAll('.ex-card, tr[data-task]')]
      .find((n) => n.dataset.task === kept.focus.task && (n.dataset.project || '') === kept.focus.project);
    if (same && typeof same.focus === 'function') same.focus({ preventScroll: true });
  }
  if (kept.focusKey) {
    const same = [...wrap.querySelectorAll('[data-focus-key]')].find((n) => n.dataset.focusKey === kept.focusKey);
    if (same) same.focus({ preventScroll: true });
  }
}

export async function mount(root, ctx) {
  injectStyles();
  // Le bilan d'un lot de décisions ne survit pas à un retour sur l'espace (sauf lot en cours).
  if (decisionUi.bulk && !decisionUi.bulk.running) decisionUi.bulk = null;
  decisionUi.confirm = '';
  const wrap = document.createElement('div');
  wrap.className = 'ex-wrap';
  root.append(wrap);
  // P58 : j / k parcourent les cartes du tableau, les lignes de la liste et de
  // Sessions ; Entrée ouvre celle qui a le focus (le clic de la ligne).
  listNav(wrap, { rows: '.ex-card, .ex-list tbody tr[data-task], .ss-table tbody tr[data-session]' });

  // `ctx.params` porte ce qu'un `goto('travail', { task, view })` — ou un lien
  // profond `#travail?task=…` — a demandé. Observer s'en sert pour ouvrir
  // directement la timeline d'une tâche depuis un span OTel qui en porte
  // l'identifiant (#139) ; `decisions` : le compteur de la barre du haut
  // (UI-3) ouvre directement la vue.
  let view = VIEW_IDS.includes(ctx.params.view) ? ctx.params.view : 'board4';
  // Sélection par (projet, id) : en portée projet, `project` reste vide ; en
  // portefeuille, `of` dit le projet de la tâche.
  let selected = ctx.params.task ? { id: ctx.params.task, project: ctx.params.of || '' } : null;
  // Vue Sessions (P33) : la session sélectionnée, et le dernier compte lu pour le segment.
  let selectedSession = ctx.params.session || '';
  let sessionsCount = null;
  const viewLabels = () => VIEW_LABELS.map((v) => (v.id === 'sessions' && sessionsCount !== null ? { ...v, count: sessionsCount } : v));
  // La portée de la coque (P31) : « Tous les projets » = le portefeuille.
  scope.mode = ctx.host.kind === 'cockpit' && ctx.scope && ctx.scope.all ? 'portfolio' : 'project';
  let board = await ctx.api.tasks();
  if (ctx.params.idee) requestIdeaFocus();

  // Libellés français des artboards Travail (Tableau / Liste) et Sessions (P33) ; les
  // autres vues réelles gardent un nom clair. « Sessions » porte son compte quand il est lu.
  const VIEW_LABELS = [
    { id: 'board4', label: 'Tableau' },
    { id: 'board8', label: 'Huit colonnes' },
    { id: 'liste', label: 'Liste' },
    { id: 'sessions', label: 'Sessions' },
    { id: 'timeline', label: 'Chronologie' },
    { id: 'decisions', label: 'Décisions' },
  ];

  // Le sélecteur de vues est celui de la barre de document (`#view-seg`, géré par
  // la coque) : l'espace le pose dans SON en-tête pour qu'il tienne sur la ligne du
  // titre comme dans la maquette, et le rend à la barre en partant (ou quand il
  // redessine son contenu : un élément détaché ne se retrouve plus par son id).
  const viewSeg = document.getElementById('view-seg');
  const releaseSeg = () => {
    const bar = document.getElementById('docbar');
    if (viewSeg && bar && !bar.contains(viewSeg)) {
      const anchor = bar.querySelector('#doc-validation');
      if (anchor) bar.insertBefore(viewSeg, anchor); else bar.append(viewSeg);
    }
  };
  ctx.signal.addEventListener('abort', releaseSeg);

  function renderHead(shown) {
    const head = document.createElement('header');
    head.className = 'ex-head';
    head.append(text('h2', 'space-title', 'Travail'));
    if (viewSeg) head.append(viewSeg);
    if (view === 'sessions') {
      // L'en-tête de l'artboard Sessions : titre, vues, commande — le filtre des tâches n'a rien à filtrer ici.
      const formula = document.createElement('span');
      formula.className = 'ex-formula';
      formula.append(text('b', null, '›'), scope.mode === 'portfolio' ? 'grimoire task list --all-projects' : 'grimoire task list');
      head.append(row(formula));
      head.lastElementChild.classList.add('ex-head-end');
      return head;
    }
    const ask = document.createElement('button');
    ask.type = 'button';
    ask.className = 'btn ex-ask';
    ask.dataset.role = 'ask-me';
    ask.setAttribute('aria-pressed', String(askMe.on));
    ask.title = "Garde les tâches qui attendent un humain : une question, une revue, un blocage";
    ask.append(icon('filter'), text('span', null, 'Demande une action de moi'), badge(String(shown), askMe.on ? 'acc' : ''));
    ask.addEventListener('click', () => { askMe.on = !askMe.on; draw(); });
    const formula = document.createElement('span');
    formula.className = 'ex-formula';
    formula.append(text('b', null, '›'), 'grimoire task list');
    // « Nouvelle tâche » (P35) : désactivé, raison écrite, hors du projet servi.
    head.append(ask, row(formula, ...ideaButton(ctx, { portfolio: scope.mode === 'portfolio', onToggle: () => draw() }), ...newTaskButton(ctx, { portfolio: scope.mode === 'portfolio', onToggle: () => draw() })));
    head.lastElementChild.classList.add('ex-head-end');
    return head;
  }

  const setView = (id) => { view = id; draw(); };
  const redraw = () => { selected = null; draw(); };
  // Confirmation et formulaire « Nouvelle tâche », redessinés depuis leur état à chaque dessin.
  const newTask = (portfolio) => newTaskBlock(ctx, {
    portfolio,
    onCreated: (task) => { selected = { id: task.id, project: '' }; draw(); },
    onOpen: (id) => { selected = { id, project: '' }; if (view === 'sessions' || view === 'decisions') view = 'board4'; draw(); },
    onChange: () => draw(),
    columnLabel: (b) => COLUMN_LABEL[b] || b,
  });

  function renderNoLedger() {
    releaseSeg();
    ctx.docbar.setBreadcrumb([]);
    // Avant cette issue, un early-return ici évitait tout appel à
    // `setViews` : les boutons de vue disparaissaient purement et
    // simplement plutôt que rester visibles (désactivés) — l'utilisateur ne
    // pouvait pas deviner ce que l'espace propose une fois un Mission Ledger
    // ouvert. On les rend désormais, désactivés, avec la raison en `title`.
    ctx.docbar.setViews(
      VIEW_LABELS.map((v) => ({
        ...v,
        disabled: true,
        disabledReason: "Aucun Mission Ledger : ouvrez-en un d'abord.",
      })),
      null,
      () => {},
    );
    appendIf(wrap, scopeToolbar(ctx, null, redraw));
    wrap.append(row(...ideaButton(ctx, { portfolio: false, onToggle: () => draw() }), ...newTaskButton(ctx, { portfolio: false, onToggle: () => draw() })));
    appendIf(wrap, ideaBlock(ctx, {
      portfolio: false,
      onCreated: () => draw(),
      onOpen: (id) => { selected = { id, project: '' }; draw(); },
      onChange: () => draw(),
    }));
    appendIf(wrap, newTask(false));
    wrap.append(ctx.empty(
      'Exécuter',
      board.note || "Ce projet n'a pas encore de Mission Ledger.",
      'grimoire task add "<titre>" --acceptance "<critère>"',
      undefined,
      { art: 'i-empty-tasks' },
    ));
    // ADR-007 (issue #559) : un board du standard scaffoldé avant le lot 4.1,
    // ou jamais migré, n'a pas encore de Mission Ledger — mais SES tâches
    // existent déjà dans `task-board.yaml`. Plutôt que le seul rappel de
    // `grimoire task add` (qui ouvrirait une tâche neuve, pas celles déjà
    // déclarées), l'action de migration est proposée quand le serveur la dit
    // disponible (`tasks_view`/`workspace_api.py`).
    if (board.migration_available) {
      const migrateBtn = document.createElement('button');
      migrateBtn.type = 'button';
      migrateBtn.className = 'btn pri';
      migrateBtn.textContent = 'Migrer les tâches';
      // Jamais désactivé par `ctx.host.readOnly` (issue #560) : migrer un
      // board vers le Mission Ledger s'applique à tout projet du registre,
      // comme mettre à jour ou décider une proposition — même précédent que
      // les boutons Accepter/Refuser de Piloter (`ctx.api.proposalAction`),
      // qui ne se désactivent pas non plus sur ce verrou général.
      const feedback = text('p', 'lbl', '');
      migrateBtn.addEventListener('click', async () => {
        ctx.dock.echo('grimoire task migrate-standard .');
        migrateBtn.disabled = true;
        try {
          const report = await ctx.api.migrateStandardTasks();
          if (ctx.signal.aborted) return;
          feedback.textContent = `${report.tasks_imported} tâche(s) migrée(s) vers le Mission Ledger.`;
          root.replaceChildren();
          await mount(root, ctx);
        } catch (error) {
          migrateBtn.disabled = false;
          feedback.textContent = `échec : ${error.message}`;
        }
      });
      wrap.append(migrateBtn, feedback);
    }
    ctx.dock.echo('grimoire task add');
  }

  // Un seul dessin « gagne » : un clic pendant qu'une actualisation est en vol,
  // ou deux actualisations qui se chevauchent, ne s'écrasent pas à moitié.
  let drawSeq = 0;
  // Signature de la carte rendue dans l'inspecteur : une actualisation ne le
  // reconstruit (trois lectures, perte du défilement) que si la carte a changé.
  let inspectedSig = '';

  async function draw(opts = {}) {
    const seq = ++drawSeq;
    const portfolioMode = scope.mode === 'portfolio';
    // Les sessions se lisent à chaque dessin, en parallèle des tâches : le segment porte leur compte.
    const sessionsRead = ctx.api.sessions(portfolioMode).then((data) => ({ data }), (error) => ({ error }));
    if (!portfolioMode && !board.ledger) {
      // Le ledger peut naître HORS du navigateur (`grimoire task add`) pendant que cet état vide
      // est affiché : on relit avant de choisir, sinon le pouls tourne pour rien (UI3-S1-01).
      const latest = await ctx.api.tasks();
      if (ctx.signal.aborted || seq !== drawSeq) return;
      const unchanged = !latest.ledger && !board.ledger
        && (latest.note || '') === (board.note || '')
        && !!latest.migration_available === !!board.migration_available;
      board = latest;
      if (!board.ledger) {
        if (opts.live && unchanged) return; // rien de neuf : on ne redessine pas l'état vide
        wrap.replaceChildren();
        renderNoLedger();
        return;
      }
    }
    let tasks = [];
    let portfolio = null;
    if (portfolioMode) {
      const params = {};
      if (scope.state) params.state = scope.state;
      if (scope.slug) params.slug = scope.slug;
      if (scope.live) params.live = '1';
      portfolio = await ctx.api.portfolioTasks(Object.keys(params).length ? params : undefined);
      tasks = portfolio.tasks || [];
    } else {
      const fresh = await ctx.api.tasks();
      tasks = fresh.tasks || [];
    }
    const sessions = await sessionsRead;
    if (ctx.signal.aborted || seq !== drawSeq) return;
    sessionsCount = sessions.data ? sessions.data.count : null;
    ctx.docbar.setBreadcrumb(view === 'sessions' ? (selectedSession ? [selectedSession.slice(0, 8)] : []) : (selected ? [selected.id] : []));
    ctx.docbar.setViews(viewLabels(), view, setView);

    // Les données sont là : on remplace d'un geste synchrone, en gardant le
    // défilement du board et la carte au clavier (une actualisation ne doit
    // pas renvoyer l'utilisateur au début ni lui voler le focus).
    const kept = captureView(wrap);
    releaseSeg();
    wrap.replaceChildren();
    const everyTask = tasks;
    if (askMe.on) tasks = tasks.filter(needsHuman);
    const presenceCounts = presenceTally(tasks);
    if (presenceFilter.state) tasks = tasks.filter((t) => presenceOf(t) === presenceFilter.state);
    wrap.append(renderHead(everyTask.filter(needsHuman).length));
    if (view !== 'sessions') appendIf(wrap, presenceFilterBar(presenceCounts, draw));
    if (view === 'sessions') {
      drawSessions(sessions, portfolioMode, kept, opts);
      return;
    }
    appendIf(wrap, scopeToolbar(ctx, portfolio, redraw));
    appendIf(wrap, ideaBlock(ctx, {
      portfolio: portfolioMode,
      onCreated: () => draw(),
      onOpen: (id) => { selected = { id, project: '' }; if (view === 'sessions' || view === 'decisions') view = 'board4'; draw(); },
      onChange: () => draw(),
    }));
    appendIf(wrap, newTask(portfolioMode));
    if (portfolio) {
      const notice = unreadableNotice(portfolio);
      if (notice) wrap.append(notice);
    }

    const same = (t) => selected && t.id === selected.id && (!portfolioMode || (t.project?.slug || '') === selected.project);
    const onSelect = async (task) => {
      selected = { id: task.id, project: task.project?.slug || '' };
      // Sous 900 px le détail est un tiroir, sauf quand il tient sous la liste
      // (P59) : la coque en décide, choisir une carte le montre.
      ctx.openPanel?.('inspector', { detail: true });
      draw();
    };
    const onRowTimeline = (task) => { selected = { id: task.id, project: task.project?.slug || '' }; setView('timeline'); };

    // K6 : ce qui attend un humain ne se cherche pas dans une colonne — bandeau sur le board,
    // vue dédiée « Décisions ».
    const waitingHuman = tasks.filter(isHumanWait).length;
    if (waitingHuman && view !== 'decisions' && view !== 'timeline') {
      wrap.append(decisionsBanner(waitingHuman, () => setView('decisions')));
    }
    if (view === 'decisions') {
      renderDecisions(wrap, ctx, tasks, portfolioMode, () => draw());
    } else if (view === 'board4') {
      renderBoard(wrap, ctx, tasks, GROUPS4, onSelect, portfolioMode, same, onRowTimeline);
    } else if (view === 'board8') {
      renderBoard(wrap, ctx, tasks, LIFECYCLE.map((id) => ({ id, label: COLUMN_LABEL[id], cols: [id] })), onSelect, portfolioMode, same, onRowTimeline);
    } else if (view === 'liste') {
      renderList(wrap, ctx, tasks, onSelect, portfolioMode, same, onRowTimeline);
    } else if (view === 'timeline') {
      const task = tasks.find(same) || tasks[0];
      if (!task) {
        wrap.append(ctx.empty('Timeline', 'Aucune tâche à tracer.', 'grimoire task list'));
      } else {
        selected = { id: task.id, project: task.project?.slug || '' };
        await renderTimeline(wrap, ctx, task);
        if (seq !== drawSeq) return;
      }
    }
    restoreView(wrap, kept);

    // `{ skipInspector: true }` (issue #638, lot B) : le pilotage humain
    // réécrit son propre bloc en place (`renderSteeringInto`) et ne déclenche
    // ce rafraîchissement que pour le board (tri par priorité, badge
    // consigne) — jamais pour reconstruire l'inspecteur par-dessus un
    // panneau que l'utilisateur est peut-être déjà en train de réutiliser.
    if (opts.skipInspector) return;

    const current = everyTask.find(same);
    // L'URL suit la sélection (lien profond, P31) : vue et tâche.
    ctx.setItem?.({
      ...(view !== 'board4' ? { view } : {}),
      ...(current ? { task: current.id, ...(portfolioMode && current.project?.slug ? { of: current.project.slug } : {}) } : {}),
    });
    if (current) {
      const sig = JSON.stringify(current);
      if (opts.live && sig === inspectedSig) return;
      const onTimeline = (id) => { selected = { id, project: current.project?.slug || '' }; setView('timeline'); };
      // Une saisie en cours reporte le re-rendu (elle repart à la sortie du champ) ;
      // la signature n'est retenue qu'une fois le panneau réellement reconstruit.
      const drawn = await renderInspector(ctx, current, draw, onTimeline, portfolioMode, {
        defer: () => opts.live && editing(),
      });
      if (seq !== drawSeq) return;
      if (!drawn) { inspectedSig = ''; refreshPending = true; return; }
      inspectedSig = sig;
      ctx.dock.echo(`grimoire task show ${current.id}`);
    } else {
      inspectedSig = '';
      ctx.inspector.dataset.owner = '';
      ctx.inspector.replaceChildren(text('p', 'lbl', 'Sélectionnez une tâche pour voir sa porte suivante.'));
      ctx.dock.echo(portfolioMode ? 'grimoire task list --all-projects' : 'grimoire task list');
    }
  }

  // Vue Sessions (P33) : tableau et tuiles, puis le panneau de la session choisie.
  // Une actualisation ne reconstruit le panneau que si la session a changé (une
  // consigne en cours de saisie n'est jamais jetée : `refresh` attend la fin de la saisie).
  let inspectedSession = '';
  let sessionNotice = null;
  function drawSessions(sessions, portfolioMode, kept, opts) {
    const list = (sessions.data && sessions.data.sessions) || [];
    const current = list.find((s) => s.id === selectedSession) || null;
    const onPick = (session) => {
      selectedSession = session.id;
      sessionNotice = null;
      ctx.openPanel?.('inspector', { detail: true });
      draw();
    };
    renderSessions(wrap, ctx, { data: sessions.data, error: sessions.error, selectedId: current ? current.id : '', onSelect: onPick });
    restoreView(wrap, kept);
    ctx.setItem?.({ view: 'sessions', ...(current ? { session: current.id } : {}) });
    if (current) {
      const sig = JSON.stringify(current);
      if (opts.live && sig === inspectedSession) return;
      inspectedSession = sig;
      renderSessionInspector(ctx, current, {
        portfolio: portfolioMode,
        notice: sessionNotice && sessionNotice.id === current.id ? sessionNotice.message : '',
        onWritten: (message) => { sessionNotice = { id: current.id, message }; draw({ live: true }); },
        go: {
          decisions: (taskId, slug) => { selected = { id: taskId, project: slug || '' }; setView('decisions'); },
          task: (taskId, slug) => { selected = { id: taskId, project: slug || '' }; setView('board4'); },
        },
      });
    } else {
      inspectedSession = '';
      inspectedSig = '';
      ctx.inspector.dataset.owner = '';
      ctx.inspector.replaceChildren(text('p', 'lbl', 'Choisis une session pour voir ses claims, son dernier signe et comment la reprendre.'));
      ctx.dock.echo(portfolioMode ? 'grimoire task list --all-projects' : 'grimoire task list');
    }
  }

  // Actualisation sans rechargement (UI-3) : quand le pouls annonce un changement,
  // l'espace relit SES données. Rien n'est jeté sous les doigts de l'utilisateur :
  // tant qu'il écrit une réponse ou une consigne (champ actif, ou texte déjà
  // saisi), l'actualisation attend — elle repart à la sortie du champ.
  let refreshPending = false;
  const editing = () => {
    const active = document.activeElement;
    const focused = !!active && !!active.closest && (wrap.contains(active) || ctx.inspector.contains(active))
      && (active.tagName === 'TEXTAREA' || (active.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(active.type)));
    if (focused) return true;
    // Un `<input>` sans attribut `type` (raison de blocage, raison d'annulation) vaut « text » mais
    // n'est pas trouvé par `input[type="text"]` : on filtre sur la propriété (UI3-S1-02).
    const typed = (root) => [...root.querySelectorAll('textarea, input')]
      .filter((field) => field.tagName === 'TEXTAREA' || field.type === 'text');
    return [...typed(wrap), ...typed(ctx.inspector)].some((field) => field.value.trim() !== '');
  };
  const refresh = () => {
    if (editing()) { refreshPending = true; return; }
    refreshPending = false;
    draw({ live: true });
  };
  ctx.live.on(refresh, { scope: () => (scope.mode === 'portfolio' ? 'portfolio' : 'project') });
  const retry = () => { if (refreshPending) setTimeout(refresh, 0); };
  wrap.addEventListener('focusout', retry, { signal: ctx.signal });
  ctx.inspector.addEventListener('focusout', retry, { signal: ctx.signal });

  await draw();
}
