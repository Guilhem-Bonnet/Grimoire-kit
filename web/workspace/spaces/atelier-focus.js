// Atelier de spec : ne pas perdre la place de la personne quand un rafraîchissement redessine une zone.
//
// Le pouls du cockpit, le retour sur l'onglet, la phase lue qui change au défilement ou l'entretien relu font redessiner
// des zones de l'Atelier. Remplacer un nœud qui porte le focus renvoie le focus au `body` : une personne au clavier
// (ou un lecteur d'écran) perd sa place, et une saisie en cours perd son curseur. Trois règles, dans cet ordre :
//   1. ne rien redessiner quand l'état n'a pas changé (`stable`, `same`) ;
//   2. mettre à jour les nœuds en place quand c'est simple ;
//   3. sinon remplacer, puis rendre le focus (et le curseur) à l'élément équivalent (`keepFocus`, `replaceKeepingFocus`),
//      sans jamais le voler s'il était ailleurs.

// Ce qui identifie un élément d'une zone redessinée, par ordre de préférence.
const KEY_ATTRS = ['data-act', 'data-phase', 'data-plan', 'data-role', 'data-block', 'data-posture', 'data-geste', 'aria-label'];

const quote = (value) => `"${String(value).replace(/["\\]/g, '\\$&')}"`;

/**
 * Le sélecteur qui retrouve l'élément équivalent après un redessin. `attrs` : ses attributs ; `tag` : son nom ; `cls` : sa
 * première classe. Rend '' quand rien ne le distingue (il se retrouve alors par son rang parmi ses semblables).
 */
export function focusSelector(attrs, tag = '', cls = '') {
  const name = KEY_ATTRS.find((key) => attrs && attrs[key] != null && attrs[key] !== '');
  if (name) return `${tag.toLowerCase()}[${name}=${quote(attrs[name])}]`;
  if (tag) return `${tag.toLowerCase()}${cls ? `.${cls}` : ''}`;
  return '';
}

/** Une représentation textuelle indépendante de l'ordre des clés : deux états égaux donnent le même texte. */
export function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().filter((k) => value[k] !== undefined).map((k) => `${JSON.stringify(k)}:${stable(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** Deux états égaux : rien à redessiner. */
export const same = (a, b) => stable(a) === stable(b);

/** Parmi les `count` éléments qui répondent au sélecteur, celui à rendre : le même rang, sinon le dernier, sinon aucun. */
export function pickIndex(index, count) {
  if (!count) return -1;
  return Math.min(Math.max(index, 0), count - 1);
}

function describe(node, container) {
  const attrs = {};
  for (const key of KEY_ATTRS) if (node.hasAttribute(key)) attrs[key] = node.getAttribute(key);
  const selector = focusSelector(attrs, node.tagName, [...node.classList][0] || '');
  const index = selector ? [...container.querySelectorAll(selector)].indexOf(node) : -1;
  return { selector, index };
}

/** Retient l'élément qui a le focus s'il est dans `container` (et son curseur s'il se tape dedans) ; sinon null. */
export function captureFocus(container) {
  const node = document.activeElement;
  if (!container || !node || node === document.body || !container.contains(node)) return null;
  const snap = { node, ...describe(node, container) };
  if (typeof node.selectionStart === 'number') {
    try { Object.assign(snap, { start: node.selectionStart, end: node.selectionEnd, dir: node.selectionDirection, top: node.scrollTop }); } catch { /* type sans sélection */ }
  }
  return snap;
}

/** Rend le focus à l'élément équivalent de `container`. Sans effet si le focus n'a pas bougé ou si rien ne correspond. */
export function restoreFocus(container, snap) {
  if (!snap || snap.node.isConnected || !snap.selector) return false;
  const found = [...container.querySelectorAll(snap.selector)];
  const target = found[pickIndex(snap.index, found.length)];
  if (!target || target.disabled || target.hidden) return false;
  target.focus({ preventScroll: true });
  if (snap.start != null && typeof target.setSelectionRange === 'function') {
    try {
      target.setSelectionRange(snap.start, snap.end, snap.dir || 'none');
      target.scrollTop = snap.top || 0;
    } catch { /* type sans sélection */ }
  }
  return document.activeElement === target;
}

/** Applique `change()` (qui redessine une partie de `container`) puis rend le focus à son équivalent. */
export function keepFocus(container, change) {
  const snap = captureFocus(container);
  change();
  restoreFocus(container, snap);
}

/** `old.replaceWith(next)`, le focus (et le curseur) restitués sur l'élément équivalent de `next`. */
export function replaceKeepingFocus(old, next) {
  const snap = captureFocus(old);
  old.replaceWith(next);
  restoreFocus(next, snap);
}

/** Pose un texte seulement s'il change : pas de nœud texte remplacé, pas de réannonce inutile. */
export function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}
