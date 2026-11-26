// Ligne de routage d'un lancement de session dans le fil d'activité : le modèle,
// le palier, la classe et la raison que `grimoire task show` affiche déjà
// (`model_routing.describe_routing`, déjà nettoyée côté serveur). Texte seul : le
// bloc la pose par `textContent`, jamais en HTML.

const CONTROL = /[\u0000-\u001f\u007f-\u009f]/g;
const LIMIT = 400;

/** La ligne de `entry.routing`, ou `''` si l'entrée n'en porte pas. */
export function routingLine(entry) {
  const raw = entry && typeof entry.routing === 'string' ? entry.routing : '';
  const clean = raw.replace(CONTROL, ' ').trim();
  if (!clean) return '';
  return clean.length > LIMIT ? `${clean.slice(0, LIMIT - 1)}…` : clean;
}
