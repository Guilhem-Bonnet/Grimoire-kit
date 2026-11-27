// Le bus des faits de la mascotte permanente du cockpit : le chat de l'orchestrateur et l'assistant de l'Atelier y posent
// ce qu'ils savent (un tour en cours, ce qui attend la personne, une erreur), la mascotte l'y lit. Rien ne passe par le
// réseau et rien ne se charge ici : quelques lignes, importées d'office sans coût. Éteinte, la mascotte n'écoute pas.

const sources = { orchestrator: null, assistant: null };
const listeners = new Set();

/** Pose les faits de `name` (`orchestrator` ou `assistant`) ; `null` les retire. */
export function publish(name, facts) {
  sources[name] = facts;
  for (const listener of listeners) {
    try { listener(sources); } catch { /* un observateur n'arrête jamais celui qui publie */ }
  }
}

/** Écoute les faits ; rend la fonction qui arrête d'écouter. L'état courant est rendu tout de suite. */
export function subscribe(listener) {
  listeners.add(listener);
  try { listener(sources); } catch { /* idem */ }
  return () => listeners.delete(listener);
}
