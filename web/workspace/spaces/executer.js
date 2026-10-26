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
// par priorité dans chaque colonne, badge « consigne non lue » sur la carte.
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
// consignes non lues quand le champ existe. Les cartes se clé-ent par
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
const needsHuman = (t) => isHumanWait(t) || t.board === 'review' || t.board === 'blocked';

// Les vues que l'URL peut demander (`#travail?view=liste`).
const VIEW_IDS = ['board4', 'board8', 'liste', 'sessions', 'timeline', 'decisions'];

// Colonnes du standard, dans l'ordre normatif (grimoire.missions.board.BOARD_LIFECYCLE).
const LIFECYCLE = ['proposed', 'ready', 'in_progress', 'blocked', 'review', 'accepted', 'released', 'archived'];
import { actionStatus, badge, glyph, icon, levelBars, monogram, setActionStatus, stateIcon } from '../shell.js';
import { agoText, costText, durationText } from '../live.js';
import { renderSessionInspector, renderSessions } from './sessions.js';
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
  { id: 'doing', icon: 'bolt', label: 'En cours', cols: ['in_progress', 'blocked'] },
  { id: 'review', icon: 'microscope', label: 'Revue', cols: ['review'] },
  { id: 'done', icon: 'seal', label: 'Fait', cols: ['accepted', 'released'] },
  { id: 'cancelled', icon: 'archive', label: 'Annulées', cols: ['archived'], folded: true },
];
// Colonne repliée dépliée ou non — au niveau du module, comme `scope` : revenir
// sur l'espace garde le dernier réglage.
const unfolded = new Set();
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
    @media (max-width: 900px) { .ex-wrap { padding: var(--sp-3); } }
    .ex-board { display: flex; gap: var(--sp-3); align-items: flex-start; overflow-x: auto; flex-grow: 1; }
    .ex-col { flex: 1 0 240px; max-width: 340px; display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
    .ex-col-head { padding: 6px 2px; }
    .ex-col-title { font-size: var(--t-s); font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 6px; }
    .ex-col-title > .mono { color: var(--rub); font-weight: 600; }
    .ex-col-title .ico { width: 16px; height: 16px; color: var(--ink2); }
    .ex-col-gate { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .ex-col-body { display: flex; flex-direction: column; gap: 6px; min-height: 60px; }
    .ex-col-body[hidden] { display: none; }
    .ex-col-body.empty { border: 1px dashed var(--line); border-radius: var(--r); align-items: center; justify-content: center; color: var(--ink3); font-size: var(--t-min); padding: var(--sp-4) 0; }
    .ex-card { border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); box-shadow: var(--shadow); padding: var(--sp-3); cursor: pointer; display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
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
    .ex-insp-idrow > .badge { margin-left: auto; }
    .ex-more { position: relative; }
    .ex-more-btn { width: 28px; padding: 0; justify-content: center; }
    .ex-more-panel { position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; width: min(300px, 80vw); display: flex; flex-direction: column; gap: 6px; padding: var(--sp-3); background: var(--overlay); border: 1px solid var(--line2, var(--line)); border-radius: var(--r-l); box-shadow: var(--shadow); }
    .ex-more-panel[hidden] { display: none; }
    .ex-more-panel h4 { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink2); }
    .ex-kv { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); font-size: var(--t-s); overflow-wrap: anywhere; }
    .ex-event { font-size: var(--t-min); color: var(--ink2); padding: 2px 0; }
    .ex-card-meta { font-size: var(--t-s); color: var(--ink2); display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
    /* Sur la carte (artboard Travail), une ligne par fait : rien ne se casse au
       milieu d'un mot ou d'un identifiant — ce qui déborde s'élide, le texte
       entier reste au survol. */
    .ex-card .ex-card-meta { flex-wrap: nowrap; white-space: nowrap; overflow: hidden; }
    .ex-card .ex-card-meta > * { flex: none; }
    .ex-card .ex-card-meta > .ex-card-owner { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
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
    .ex-card-next { font-size: var(--t-min); color: var(--ink2); display: flex; justify-content: space-between; align-items: center; gap: 6px; border-top: 1px solid var(--line); padding-top: 6px; }
    .ex-card-next .btn { height: 24px; padding: 0 10px; font-size: var(--t-min); }
    .ex-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 10px; font-size: var(--t-min); }
    .ex-cost { font-size: var(--t-min); }
    .ex-list-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); }
    .ex-list { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .ex-list th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); background: var(--bar); }
    .ex-list td { padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); }
    .ex-list tbody tr { cursor: pointer; }
    .ex-list tbody tr:hover { background: var(--e2); }
    .ex-timeline { display: flex; flex-direction: column; gap: 6px; padding: var(--sp-2) 0; }
    .ex-tl-filters { display: flex; gap: var(--sp-2); align-items: center; padding-bottom: var(--sp-2); }
    .ex-tl-filters select { font-size: var(--t-min); }
    .ex-tl-count { font-size: var(--t-min); color: var(--ink3); margin-left: auto; }
    .ex-tl-entry { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); cursor: pointer; }
    .ex-tl-entry:hover { background: var(--e2); }
    .ex-tl-entry.fail { border-color: var(--bad); }
    .ex-tl-row { display: flex; gap: var(--sp-3); align-items: flex-start; }
    .ex-tl-at { font-family: var(--mono); font-size: var(--t-min); color: var(--ink3); width: 150px; flex: none; }
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
    .ex-decisions { display: flex; flex-direction: column; gap: var(--sp-3); max-width: 760px; }
    .ex-decision { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-3); border: 1px solid var(--line); border-left: 3px solid var(--warn); border-radius: var(--r); background: var(--e1); }
    .ex-decision-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: var(--t-s); color: var(--ink2); }
    .ex-decision-title { font-size: var(--t-s); font-weight: 500; color: var(--ink); }
    .ex-decision-question { font-size: var(--t-m, var(--t-s)); color: var(--ink); margin: 0; white-space: pre-wrap; }
    .ex-decision-options { display: flex; flex-wrap: wrap; gap: 6px; }
    .ex-decision textarea { min-height: 44px; font: inherit; font-size: var(--t-s); }
    .ex-decision-note { font-size: var(--t-min); color: var(--ink3); }
    .ex-decision-item { display: flex; flex-direction: column; gap: var(--sp-2); padding-top: var(--sp-2); border-top: 1px solid var(--line); }
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
// (glyphe de vivacité + mot), priorité (barres + mot), consignes non lues
// (bulle + compteur) — une forme par sens, jamais la couleur seule (P30).
function portfolioRow(task) {
  const line = document.createElement('div');
  line.className = 'ex-card-project';
  const project = document.createElement('span');
  project.className = 'chip';
  const projectName = task.project?.name || task.project?.slug || 'projet';
  project.append(monogram(projectName), text('span', null, projectName));
  line.append(project);
  const session = task.session;
  if (session && session.id) {
    line.append(row(sessionGlyph(session), text('span', null, `session ${livenessLabel(session)}`)));
  } else {
    line.append(text('span', 'lbl', 'aucune session'));
  }
  if (task.priority) {
    line.append(row(priorityBars(task.priority) || '', text('span', null, PORTFOLIO_PRIORITY_LABEL[task.priority] || `priorité ${task.priority}`)));
  }
  if (task.unread_directives) {
    line.append(row(glyph('chat', 'consignes'), text('span', null, `${task.unread_directives} consigne(s) non lue(s)`)));
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

function taskCard(task, onSelect, portfolio = false, selected = false) {
  const card = document.createElement('div');
  card.className = 'ex-card';
  if (selected) card.setAttribute('aria-current', 'true');
  card.tabIndex = 0;
  card.dataset.project = task.project?.slug || '';
  card.dataset.task = task.id;
  card.addEventListener('click', () => onSelect(task));
  card.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(task); });

  // Ligne du haut (artboard Travail) : identifiant, classe de route, anneau de preuve.
  const top = document.createElement('div');
  top.className = 'ex-card-top';
  const cardId = text('span', 'ex-card-id mono', task.id);
  cardId.title = task.id;
  top.append(cardId);
  appendIf(top, routeBadge(task));
  appendIf(top, proofRing(task));
  card.append(top);
  const cardTitle = text('div', 'ex-card-title', task.title || task.id);
  cardTitle.title = task.title || task.id;
  card.append(cardTitle);
  if (portfolio) card.append(portfolioRow(task));
  const meta = text('div', 'ex-card-meta lbl', '');
  if (task.type) meta.append(typeGlyph(task.type));
  const owner = text('span', 'ex-card-owner', task.owner || 'sans owner');
  owner.title = owner.textContent;
  meta.append(owner);
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
  // Le badge « consigne non lue » tant qu'une consigne de l'orchestrateur n'a
  // pas été livrée à la session — sur sa propre ligne, hors de la méta.
  const flags = document.createElement('div');
  flags.className = 'ex-card-flags';
  if (task.directives_pending > 0) {
    const unread = badge(`consigne non lue (${task.directives_pending})`, 'acc');
    unread.dataset.role = 'directive-badge';
    flags.append(unread);
  } else if (task.directives_unacknowledged > 0) {
    flags.append(badge(`consigne non accusée (${task.directives_unacknowledged})`, 'acc'));
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
    line.append(waitGlyph(task.waiting), text('span', null, waitingText(task.waiting)));
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
  if (task.session_id) {
    // La vivacité a son glyphe sur la ligne des faits, juste en dessous : pas
    // de doublon ici.
    card.append(sessionLine(task));
  }
  // UI-3 : vivacité, dernier signe, durée — en portefeuille l'état est déjà dans
  // la ligne projet (portfolioRow), on n'y ajoute que signe et durée.
  // Vivacité, signe, durée et coût sur une seule ligne de faits, chaque fait
  // d'un bloc (le coût n'a sa propre ligne que sans session).
  const facts = task.session && task.session.id ? liveRow(task.session, { withState: !portfolio, compact: true }) : null;
  if (facts) card.append(facts);
  const cost = costRow(task);
  if (cost && facts) facts.append(Object.assign(cost, { className: 'ex-cost' }));
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
  nextRow.append(actions);
  card.append(nextRow);
  return card;
}

function renderBoard(root, ctx, tasks, groups, onSelect, portfolio = false, isSelected = () => false) {
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
    const inColumn = group.cols.flatMap((c) => byColumn.get(c) || []).sort(byPriority);
    const folded = group.folded && !unfolded.has(group.id);

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
        renderBoard(fresh, ctx, tasks, groups, onSelect, portfolio, isSelected);
        board.replaceWith(fresh.firstElementChild);
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

    const body = document.createElement('div');
    body.className = 'ex-col-body' + (inColumn.length ? '' : ' empty');
    body.hidden = folded;
    if (!inColumn.length) {
      body.append(text('span', null, 'vide'));
    } else {
      for (const task of inColumn) body.append(taskCard(task, onSelect, portfolio, isSelected(task)));
    }
    col.append(body);
    board.append(col);
  }
  root.append(board);
}

function renderList(root, ctx, tasks, onSelect, portfolio = false) {
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
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  const ordered = [...tasks].sort((a, b) => (Number(isHumanWait(b)) - Number(isHumanWait(a))) || (LIFECYCLE.indexOf(a.board) - LIFECYCLE.indexOf(b.board)) || byPriority(a, b));
  for (const task of ordered) {
    const tr = document.createElement('tr');
    tr.dataset.project = task.project?.slug || '';
    tr.dataset.task = task.id;
    tr.addEventListener('click', () => onSelect(task));
    if (portfolio) tr.append(text('td', null, task.project?.name || task.project?.slug || '—'));
    tr.append(text('td', null, task.title || task.id));
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
      if (session && session.id) {
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

function timelineEntryNode(entry) {
  const node = document.createElement('div');
  node.className = 'ex-tl-entry' + (entry.failure ? ' fail' : '');
  const head = document.createElement('div');
  head.className = 'ex-tl-row';
  head.append(text('div', 'ex-tl-at mono', entry.at || '—'));
  const body = document.createElement('div');
  body.append(row(dot(entry.failure ? 'bad' : 'ok'), text('span', null, entry.summary || entry.kind)));
  body.append(text('div', 'lbl', `${entry.source} · ${entry.kind}`));
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

// ── Décisions en attente (K6) ─────────────────────────────────────────────

// Une décision : la question, ses réponses proposées, une zone libre. Le clic
// poste `answer` ; le serveur répond 200 avec la tâche rendue (`ready`) ou
// refuse (question déjà tranchée, question de flow) — le refus s'affiche.
// W6-07 (issue #564) : un lot de questions posé en une fois est UNE carte, une
// réponse par question encore ouverte (`waiting.batch`, statut lu au registre
// `task.questions`) ; la tâche n'est rendue qu'une fois les bloquantes tranchées.
function openQuestions(task) {
  const w = task.waiting;
  const group = [w.question, ...(w.batch || [])];
  const answered = new Set((task.questions || [])
    .filter((r) => r.answer_origin === 'human' && r.question).map((r) => r.question.id));
  const open = group.filter((q) => !answered.has(q.id));
  return open.length ? open : [w.question];
}

function decisionCard(ctx, task, portfolio, onWritten) {
  const open = openQuestions(task);
  const q = open[0];
  const node = document.createElement('div');
  node.className = 'ex-decision';
  node.dataset.role = 'decision';
  node.dataset.task = task.id;
  const head = document.createElement('div');
  head.className = 'ex-decision-head';
  if (portfolio) head.append(text('span', 'chip', task.project?.name || task.project?.slug || 'projet'));
  head.append(
    text('span', 'ex-decision-title', task.title || task.id),
    text('span', 'lbl', open.length > 1 ? `${open.length} questions en un lot` : (QUESTION_KIND_LABEL[q.kind] || q.kind)),
    text('span', 'lbl', ageText(task.waiting.since || q.asked_at)),
  );
  node.append(head);

  const hint = task.waiting.resume_hint;
  const hintNote = hint && hint.session_id
    ? text('div', 'ex-decision-note', `session ${hint.session_id}` + (hint.resume_command ? ` — ${hint.resume_command}` : ''))
    : null;
  if (q.origin === 'flow') {
    node.append(text('p', 'ex-decision-question', q.text));
    if (hintNote) node.append(hintNote);
    node.append(text('div', 'ex-decision-note', "Cette décision est celle d'un checkpoint de flow : elle s'atteste dans le flux (un tour humain de la session qui l'a présenté, puis la même décision renvoyée), pas ici."));
    return node;
  }
  const locked = !portfolio && ctx.host.readOnly;
  for (const question of open) {
    const box = open.length > 1 ? document.createElement('div') : node;
    if (box !== node) {
      box.className = 'ex-decision-item';
      box.dataset.role = 'decision-question';
      box.dataset.question = question.id;
      node.append(box);
    }
    appendQuestionForm(ctx, box, task, question, portfolio, locked, onWritten, open.length > 1);
    if (box === node && hintNote) node.insertBefore(hintNote, node.querySelector('.ex-decision-options'));
  }
  if (open.length > 1 && hintNote) node.append(hintNote);
  if (locked) node.append(text('div', 'ex-decision-note', "le cockpit est en lecture seule : ouvrez l'atelier de ce projet, ou la portée « Tous les projets »"));
  return node;
}

function appendQuestionForm(ctx, box, task, q, portfolio, locked, onWritten, grouped) {
  box.append(text('p', 'ex-decision-question', q.text));
  const tags = [];
  if (grouped) tags.push(QUESTION_KIND_LABEL[q.kind] || q.kind);
  if (q.blocking === false) tags.push('non bloquante');
  if (tags.length) box.append(text('div', 'ex-decision-note', tags.join(' · ')));
  if (q.why) box.append(text('div', 'ex-decision-note', `pourquoi : ${q.why}`));
  const feedback = document.createElement('div');
  const area = document.createElement('textarea');
  area.className = 'input';
  area.placeholder = 'Votre réponse (ou choisissez une option)';
  area.dataset.role = 'answer-text';
  area.disabled = locked;
  const send = async (value) => {
    const answer = (value || '').trim();
    if (!answer) { feedback.replaceChildren(text('div', 'ex-refusal', 'une réponse vide ne tranche rien')); return; }
    const body = { text: answer, question_id: q.id };
    ctx.dock.echo(`grimoire task answer ${task.id} "${answer}" --question ${q.id}` + (portfolio ? `  # projet ${task.project?.slug}` : ''));
    try {
      if (portfolio) await ctx.api.portfolioTaskAction(task.id, 'answer', body, task.project?.slug);
      else await ctx.api.taskAction(task.id, 'answer', body);
      onWritten();
    } catch (error) {
      feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
    }
  };
  const options = document.createElement('div');
  options.className = 'ex-decision-options';
  for (const option of q.options || []) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn' + (option.toLowerCase() === 'abandonner' ? '' : ' pri');
    btn.textContent = option;
    btn.dataset.role = 'answer-option';
    btn.disabled = locked;
    btn.addEventListener('click', () => send(option));
    options.append(btn);
  }
  const submit = document.createElement('button');
  submit.type = 'button';
  submit.className = 'btn';
  submit.textContent = 'Répondre';
  submit.dataset.role = 'answer-submit';
  submit.disabled = locked;
  submit.addEventListener('click', () => send(area.value));
  box.append(options, area, row(submit), feedback);
}

function renderDecisions(root, ctx, tasks, portfolio, onWritten) {
  const pending = tasks.filter(isHumanWait).sort((a, b) =>
    String(a.waiting.since || '').localeCompare(String(b.waiting.since || '')) || String(a.id).localeCompare(String(b.id)));
  const wrap = document.createElement('div');
  wrap.className = 'ex-decisions';
  if (!pending.length) {
    wrap.append(ctx.empty('Décisions en attente', "Aucune décision n'attend un humain.", 'grimoire task decisions', undefined, { art: 'i-empty-inbox' }));
  }
  for (const task of pending) wrap.append(decisionCard(ctx, task, portfolio, onWritten));
  root.append(wrap);
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
    fields.push({
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
    // Une saisie dans le menu « ⋯ » (raison d'annulation) rouvre le menu qui la porte.
    const panel = el.closest('[data-role="more-panel"]');
    if (panel && panel.hidden && (f.value || f.focused)) {
      panel.hidden = false;
      container.querySelector('[data-role="more"]')?.setAttribute('aria-expanded', 'true');
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
  const headRow = row(
    text('span', 'lbl mono', detail.id),
    badge(stateLabel, waitingNow ? 'warn' : STATE_TONE[detail.board]),
  );
  headRow.classList.add('ex-insp-idrow');
  appendIf(headRow, moreMenu(ctx, detail, onWritten));
  head.append(headRow);
  head.append(text('h3', null, detail.title || detail.id));
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
    btn.addEventListener('click', async () => {
      const [action, body, command] = actionFor(target, detail, reasonInput?.value || '');
      ctx.dock.echo(portfolio && project ? `${command}  # projet ${project}` : command);
      try {
        const result = portfolio
          ? await ctx.api.portfolioTaskAction(taskId, action, body, project)
          : await ctx.api.taskAction(taskId, action, body);
        feedback.replaceChildren();
        if (result && result.blocked) {
          feedback.append(refusalNode(result.refusals || []));
        } else {
          feedback.append(text('div', 'lbl', `transition : ${result.transition}`));
          onWritten();
        }
      } catch (error) {
        feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
      }
    });
    gateRow.append(btn);
    moves.append(gateRow);
  }
  gateBlock.append(moves, feedback);
  ctx.inspector.append(gateBlock);

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

  // ── Mémoire servie : le reçu de rappel, rejouable (P27) ─────────────────
  ctx.inspector.append(receiptBlock(ctx, taskId, receipts, project));

  // ── Consigne à la session, priorité (gestes existants) ──────────────────
  renderSteering(ctx, detail, onWritten);

  // ── Derniers événements ─────────────────────────────────────────────────
  const eventCount = (trace?.entries || []).length;
  const eventsBlock = document.createElement('div');
  eventsBlock.className = 'ex-insp-block';
  eventsBlock.dataset.role = 'last-events';
  eventsBlock.append(text('h4', null, 'Derniers événements'));
  const recent = [...(trace?.entries || [])].slice(-3).reverse();
  if (!recent.length) eventsBlock.append(text('p', 'lbl', 'aucun événement lu'));
  for (const entry of recent) {
    const line = text('div', 'ex-event', '');
    line.append(text('span', 'lbl', `${(entry.at || '').slice(0, 16).replace('T', ' ')} · `), entry.summary || entry.kind);
    eventsBlock.append(line);
  }
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
  prioBlock.append(row(select, text('span', 'lbl', detail.priority ? 'déclarée' : 'dérivée du profil de risque')));
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
    const status = d.acknowledged_at ? 'accusée' : (d.delivered_at ? 'livrée' : 'non lue');
    node.append(text('div', null, d.text));
    node.append(text('div', 'ex-directive-meta', `${d.kind} · ${d.author} · ${(d.created_at || '').slice(0, 16)} · ${status}`));
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
let moreMenuListener = null;
function moreMenu(ctx, detail, onWritten) {
  if (ctx.host.readOnly || CANCEL_TERMINAL.has(detail.board)) return null;
  const wrap = document.createElement('div');
  wrap.className = 'ex-more';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'btn ghost ex-more-btn';
  toggle.dataset.role = 'more';
  toggle.setAttribute('aria-label', "Plus d'actions");
  toggle.title = "Plus d'actions";
  toggle.setAttribute('aria-haspopup', 'true');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'ex-more-panel');
  toggle.append(icon('more'));
  const panel = document.createElement('div');
  panel.className = 'ex-more-panel';
  panel.id = 'ex-more-panel';
  panel.dataset.role = 'more-panel';
  panel.setAttribute('role', 'group');
  panel.setAttribute('aria-label', "Plus d'actions");
  panel.hidden = true;
  const setOpen = (open) => {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };
  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(panel.hidden);
    if (!panel.hidden) panel.querySelector('input')?.focus();
  });
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { setOpen(false); toggle.focus(); }
  });
  panel.append(text('h4', null, 'Annuler la tâche'));
  const reason = document.createElement('input');
  reason.type = 'text';
  reason.className = 'input';
  reason.placeholder = "raison de l'annulation (obligatoire)";
  reason.dataset.role = 'cancel-reason';
  reason.setAttribute('aria-label', "Raison de l'annulation");
  // Un seul écouteur « clic hors du menu » vit à la fois : chaque re-rendu de
  // l'inspecteur retire celui du menu précédent.
  moreMenuListener?.abort();
  moreMenuListener = new AbortController();
  const listening = AbortSignal.any ? AbortSignal.any([ctx.signal, moreMenuListener.signal]) : moreMenuListener.signal;
  document.addEventListener('click', (e) => {
    // Une raison déjà saisie garde le menu ouvert (un brouillon ne se perd pas sur un clic à côté).
    if (!panel.hidden && !wrap.contains(e.target) && !reason.value.trim()) setOpen(false);
  }, { signal: listening });

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
  wrap.append(toggle, panel);
  return wrap;
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
  return {
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
}

export async function mount(root, ctx) {
  injectStyles();
  const wrap = document.createElement('div');
  wrap.className = 'ex-wrap';
  root.append(wrap);

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
    formula.append(text('b', null, '›'), 'grimoire task board');
    head.append(ask, row(formula));
    head.lastElementChild.classList.add('ex-head-end');
    return head;
  }

  const setView = (id) => { view = id; draw(); };
  const redraw = () => { selected = null; draw(); };

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
    wrap.append(renderHead(everyTask.filter(needsHuman).length));
    if (view === 'sessions') {
      drawSessions(sessions, portfolioMode, kept, opts);
      return;
    }
    appendIf(wrap, scopeToolbar(ctx, portfolio, redraw));
    if (portfolio) {
      const notice = unreadableNotice(portfolio);
      if (notice) wrap.append(notice);
    }

    const same = (t) => selected && t.id === selected.id && (!portfolioMode || (t.project?.slug || '') === selected.project);
    const onSelect = async (task) => {
      selected = { id: task.id, project: task.project?.slug || '' };
      // Sous 900 px le panneau de détail est un tiroir : choisir une carte l'ouvre.
      if (window.matchMedia('(max-width: 900px)').matches) ctx.openPanel?.('inspector');
      draw();
    };

    // K6 : ce qui attend un humain ne se cherche pas dans une colonne — bandeau sur le board,
    // vue dédiée « Décisions ».
    const waitingHuman = tasks.filter(isHumanWait).length;
    if (waitingHuman && view !== 'decisions' && view !== 'timeline') {
      wrap.append(decisionsBanner(waitingHuman, () => setView('decisions')));
    }
    if (view === 'decisions') {
      renderDecisions(wrap, ctx, tasks, portfolioMode, () => draw());
    } else if (view === 'board4') {
      renderBoard(wrap, ctx, tasks, GROUPS4, onSelect, portfolioMode, same);
    } else if (view === 'board8') {
      renderBoard(wrap, ctx, tasks, LIFECYCLE.map((id) => ({ id, label: COLUMN_LABEL[id], cols: [id] })), onSelect, portfolioMode, same);
    } else if (view === 'liste') {
      renderList(wrap, ctx, tasks, onSelect, portfolioMode);
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
      ctx.dock.echo(portfolioMode ? 'grimoire task list --all-projects' : 'grimoire task board');
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
      if (window.matchMedia('(max-width: 900px)').matches) ctx.openPanel?.('inspector');
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
  ctx.live.on(refresh);
  const retry = () => { if (refreshPending) setTimeout(refresh, 0); };
  wrap.addEventListener('focusout', retry, { signal: ctx.signal });
  ctx.inspector.addEventListener('focusout', retry, { signal: ctx.signal });

  await draw();
}
