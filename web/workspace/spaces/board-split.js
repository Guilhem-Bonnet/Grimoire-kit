// Découpe d'une colonne du board en « récentes » et « anciennes » (I4).
//
// Pure, sans DOM : la colonne « Fait » garde les `limit` tâches closes le plus
// récemment, le reste passe derrière un bouton « Voir les N autres ». La date
// de clôture est celle de la dernière transition (`in_state_since`, servie par
// le serveur), à défaut `closed_at` / `updated_at`. Une date absente ou
// illisible range la tâche en dernier, sans exception ; à date égale, l'ordre
// d'entrée est conservé.

export const DONE_RECENT_LIMIT = 6;

export function closedAtMs(task) {
  for (const key of ['in_state_since', 'closed_at', 'updated_at']) {
    const ms = Date.parse(task && task[key]);
    if (!Number.isNaN(ms)) return ms;
  }
  return Number.NEGATIVE_INFINITY;
}

export function splitRecent(tasks, limit = DONE_RECENT_LIMIT) {
  const keep = Math.max(0, Math.floor(Number(limit)) || 0);
  const sorted = (tasks || [])
    .map((task, index) => ({ task, index, at: closedAtMs(task) }))
    .sort((a, b) => (a.at === b.at ? a.index - b.index : (b.at > a.at ? 1 : -1)))
    .map((entry) => entry.task);
  return { recent: sorted.slice(0, keep), older: sorted.slice(keep), remaining: Math.max(0, sorted.length - keep) };
}
