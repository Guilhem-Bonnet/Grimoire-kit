// À traiter — le regroupement par tâche (pur, sans DOM : testé par node --test).
//
// Le serveur émet un élément par motif (`workspace_attention.py`) : une même
// tâche peut porter une question, un claim sans session et une consigne à lire.
// Pour l'humain c'est une seule chose à traiter. Dans une section, les éléments
// d'une même tâche (`ref.task_id` du même projet) forment une ligne ; les
// éléments sans tâche restent une ligne chacun.
//
// La section arrive triée du motif le plus grave au moins grave (rang du type
// puis ancienneté) : le premier élément d'une tâche est donc son motif le plus
// grave, celui dont l'action et le titre font la ligne.

const slugOf = (item) => (item.project && item.project.slug) || '';

/** Les lignes d'une section : `{ id, taskId, lead, items }`, dans l'ordre du
 *  serveur. `id` est stable d'un rendu à l'autre : celui de la tâche (et de la
 *  section) quand il y en a une, l'id de l'élément sinon. */
export function groupSection(sectionId, items) {
  const groups = [];
  const byTask = new Map();
  for (const item of items || []) {
    const taskId = (item.ref && item.ref.task_id) || '';
    if (!taskId) {
      groups.push({ id: item.id, taskId: '', lead: item, items: [item] });
      continue;
    }
    const key = `${slugOf(item)}\u0000${taskId}`;
    const known = byTask.get(key);
    if (known) {
      known.items.push(item);
      continue;
    }
    const group = { id: `task:${sectionId}:${slugOf(item)}:${taskId}`, taskId, lead: item, items: [item] };
    byTask.set(key, group);
    groups.push(group);
  }
  return groups;
}

/** Les sections avec leurs lignes et un compte en lignes (tâches distinctes). */
export function groupSections(sections) {
  return (sections || []).map((section) => {
    const groups = groupSection(section.id, section.items);
    return { ...section, groups, count: groups.length };
  });
}

/** Les motifs distincts d'une ligne, dans l'ordre de gravité, sans répétition. */
export function kindsOf(group) {
  return [...new Set(group.items.map((item) => item.kind))];
}
