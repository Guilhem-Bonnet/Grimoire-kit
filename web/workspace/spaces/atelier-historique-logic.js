// Atelier de spec (en test) : la logique pure de l'historique des versions, sans DOM, pour la tester seule (tests/js).
//
// Les données viennent de `GET atelier/chantiers/<slug>/history` et `…/history/<id>` (voir
// `src/grimoire/tools/workspace_atelier_history.py`) ; rien ici n'écrit, ne touche au document ni ne construit de HTML.

const PHASE_SHORT = {
  brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges',
};
const MARK = { add: '+', del: '−', ctx: ' ' };
const SPOKEN = { add: 'ligne ajoutée', del: 'ligne retirée', ctx: 'ligne inchangée' };

/** Le nom court d'une phase (`{ id, title }` du serveur) ; son titre, ou son identifiant, à défaut. */
export function phaseName(phase) {
  if (!phase) return '';
  return PHASE_SHORT[phase.id] || String(phase.title || phase.id || '').split(' — ')[0];
}

/** « Brief », « Brief et Exigences », « Brief, Exigences et Cahier des charges ». */
export function joinNames(names) {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} et ${names[names.length - 1]}`;
}

/** Le résumé d'une version : « +12 −3, Brief et Exigences » (sans compteur, seulement les phases, si rien n'est compté). */
export function versionSummary(version) {
  const phases = joinNames((version.phases || []).map(phaseName));
  const counts = `+${version.added || 0} −${version.removed || 0}`;
  return phases ? `${counts}, ${phases}` : counts;
}

function validDate(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? date : null;
}

const dayKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

/** « 27 novembre 2026 », ou « Aujourd’hui » / « Hier » par rapport à `now`. */
export function dayLabel(date, now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((today - new Date(date.getFullYear(), date.getMonth(), date.getDate())) / 86_400_000);
  if (days === 0) return 'Aujourd’hui';
  if (days === 1) return 'Hier';
  return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function timeLabel(iso) {
  const date = validDate(iso);
  return date ? date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '';
}

/**
 * Les versions regroupées par jour, plus récentes d'abord : `[{ key, label, items }]`. L'entrée « modifications non
 * commitées » (sans date) forme son propre groupe en tête ; une date illisible va dans « Date inconnue ».
 */
export function groupByDay(versions, now = new Date()) {
  const groups = [];
  const find = (key, label) => {
    let group = groups.find((g) => g.key === key);
    if (!group) { group = { key, label, items: [] }; groups.push(group); }
    return group;
  };
  for (const version of versions || []) {
    if (version.kind === 'working') { find('working', 'Pas encore enregistré').items.push(version); continue; }
    const date = validDate(version.date);
    if (!date) { find('inconnu', 'Date inconnue').items.push(version); continue; }
    find(dayKey(date), dayLabel(date, now)).items.push(version);
  }
  return groups;
}

/** La phrase du bandeau de comparaison : « Tu regardes la version du 27 novembre 2026 à 14:32, par Ada. » */
export function compareLabel(version) {
  if (!version || version.kind === 'working') return 'Tu regardes les modifications pas encore enregistrées.';
  const date = validDate(version.date);
  const when = date ? ` du ${date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })} à ${timeLabel(version.date)}` : '';
  return `Tu regardes la version${when}${version.author ? `, par ${version.author}` : ''}.`;
}

/** Le titre d'une version dans la liste : son sujet de commit, ou « Modifications en cours ». */
export function versionTitle(version) {
  if (version.kind === 'working') return 'Modifications en cours';
  return version.subject || `Version ${version.short || ''}`.trim();
}

/** Les lignes d'une phase prêtes à afficher : le signe (+, −) porte l'information en plus de la couleur, et un libellé lu à voix haute. */
export function diffLines(phase) {
  const out = [];
  for (const row of (phase && phase.rows) || []) {
    if (row.t === 'gap') {
      out.push({ kind: 'gap', mark: '', spoken: '', old: '', now: '', text: `… ${row.skipped} ligne${row.skipped > 1 ? 's' : ''} inchangée${row.skipped > 1 ? 's' : ''} …` });
      continue;
    }
    out.push({ kind: row.t, mark: MARK[row.t] ?? ' ', spoken: SPOKEN[row.t] || '', old: row.old ?? '', now: row.new ?? '', text: row.text });
  }
  return out;
}

/** La liste vide : le texte du serveur, ou un texte neutre. */
export function emptyText(data) {
  if (data && data.reason_text) return data.reason_text;
  return 'Aucune version enregistrée pour ce chantier.';
}

/** L'index visé par une flèche dans une liste de `length` éléments (sans boucle) ; `null` si la touche n'est pas une flèche. */
export function arrowTarget(index, key, length) {
  if (!length) return null;
  if (key === 'ArrowDown') return Math.min(index + 1, length - 1);
  if (key === 'ArrowUp') return Math.max(index - 1, 0);
  if (key === 'Home') return 0;
  if (key === 'End') return length - 1;
  return null;
}
