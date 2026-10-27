// Les infobulles épinglables, et la seule source de définitions.
//
// Spécification §3.2. Une seule source : `framework/glossary.yaml`, servi par
// `/api/workspace/glossary`. Rien dans l'interface ne doit écrire une
// définition en dur : un terme se cite avec `data-term="<id>"`, et
// tests/unit/test_workspace_glossary.py refuse un `data-term` sans entrée.
//
// Mécanique (maquette « Mécaniques de la vue de travail ») :
//   survol 500 ms       → bulle courte, non épinglée
//   focus clavier (Tab)  → même bulle, ouverte sans délai
//   Alt                  → fige la bulle ; le pointeur peut y entrer ; cadenas affiché
//   terme dans la bulle  → bulle enfant, elle aussi épinglable, 3 niveaux max
//   Échap                → ferme toute la pile
//   clic ailleurs         → ferme les bulles non épinglées, laisse les épinglées
//   croix de la bulle     → ferme cette bulle épinglée (et ses enfants)
//   densité compacte      → nom et raccourci seulement, délai 800 ms
//
// P55 : chaque bulle est un `popover="manual"`. Elle vit dans la couche
// supérieure, au-dessus de la palette et de l'aide (`<dialog>` modaux) et d'un
// menu ouvert, sans qu'aucun `z-index` ne se dispute la place. « manual » et
// non « auto » ou « hint » : la pile (une bulle enfant ouverte depuis sa mère,
// les bulles épinglées qui survivent au clic à côté) n'est pas un modèle que
// le navigateur connaît — « auto » et « hint » fermeraient la mère en ouvrant
// l'enfant. Échap et le clic à côté restent donc ceux de la pile, ci-dessous ;
// la bulle ne prend jamais le focus, il n'y a pas de focus à rendre. Sans
// Popover API, la bulle reste un bloc en position fixe (shell.css, `.tip`).

/** Le 4e espace de la coque (P31) : son nom n'est écrit qu'ici. « Savoir » ou
 *  « Historique » reste à trancher (décision D2) : changer ces deux chaînes
 *  suffit, la coque, le fil d'Ariane, la palette et les liens profonds les
 *  relisent. L'icône garde son nom de sprite, qui n'est pas un libellé. */
export const FOURTH_SPACE = Object.freeze({ id: 'savoir', label: 'Savoir', icon: 'i-space-savoir' });

const MAX_DEPTH = 3;
const HAS_POPOVER = typeof HTMLElement === 'function' && typeof HTMLElement.prototype.showPopover === 'function';
//: Balises déjà focusables nativement — les autres reçoivent tabindex="0" pour
//: que Tab atteigne tout `[data-term]`, quel que soit l'espace qui l'a posé.
const NATIVELY_FOCUSABLE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);

let entries = new Map();
let stack = [];
let hoverTimer = null;
let pendingAnchor = null;
let tipSeq = 0;

/** Charge le glossaire. Sans lui, les infobulles se taisent — elles n'inventent pas. */
export async function load(api) {
  const payload = await api.glossary();
  entries = new Map((payload.entries || []).map((e) => [e.id, e]));
  return payload;
}

export function get(id) {
  return entries.get(id) || null;
}

export function size() {
  return entries.size;
}

/** Les termes cités par le DOM qui n'ont pas d'entrée. Vide, ou un défaut. */
export function missingTerms(root = document) {
  const cited = new Set(
    [...root.querySelectorAll('[data-term]')].map((el) => el.dataset.term).filter(Boolean),
  );
  return [...cited].filter((id) => !entries.has(id)).sort();
}

function compact() {
  return document.documentElement.dataset.density === 'compacte';
}

function delay() {
  return compact() ? 800 : 500;
}

// Le cadenas de la maquette « Encre » (bulle épinglée) : un rectangle et son anse.
function lockIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'var(--acc)');
  svg.setAttribute('stroke-width', '1.5');
  svg.classList.add('ico');
  svg.style.width = '14px';
  svg.style.height = '14px';
  const body = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  body.setAttribute('x', '5');
  body.setAttribute('y', '9');
  body.setAttribute('width', '10');
  body.setAttribute('height', '8');
  body.setAttribute('rx', '1.5');
  const shackle = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  shackle.setAttribute('d', 'M7 9V6.5a3 3 0 0 1 6 0V9');
  svg.append(body, shackle);
  return svg;
}

function build(entry, depth, pinned) {
  const tip = document.createElement('div');
  tip.id = `glossary-tip-${++tipSeq}`;
  tip.className = 'tip';
  tip.dataset.depth = String(depth);
  tip.dataset.term = entry.id;
  tip.setAttribute('role', 'tooltip');
  if (HAS_POPOVER) tip.popover = 'manual';

  const head = document.createElement('div');
  head.className = 'row';
  head.style.justifyContent = 'space-between';
  const name = document.createElement('span');
  name.className = 't';
  name.textContent = entry.nom || entry.id;
  head.append(name);

  if (pinned) {
    // Cadenas + libellé (maquette « Encre »), et la croix qui referme cette
    // bulle — et ce qu'elle a ouvert au-dessus d'elle.
    const status = document.createElement('span');
    status.className = 'row';
    status.style.gap = '4px';
    status.append(lockIcon());
    const lbl = document.createElement('span');
    lbl.className = 'lbl';
    lbl.textContent = 'épinglée';
    status.append(lbl);
    head.append(status);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn';
    closeBtn.style.height = '20px';
    closeBtn.style.width = '20px';
    closeBtn.style.padding = '0';
    closeBtn.style.marginLeft = '8px';
    closeBtn.setAttribute('aria-label', `Fermer « ${entry.nom || entry.id} »`);
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => closeFrom(depth));
    head.append(closeBtn);
  } else {
    const hint = document.createElement('span');
    hint.className = 'lbl';
    hint.textContent = 'Alt · épingler';
    head.append(hint);
  }
  tip.append(head);

  // Densité compacte, non épinglée : nom et raccourci seulement. Alt rouvre la définition.
  if (!compact() || pinned) {
    const body = document.createElement('div');
    body.style.marginTop = '6px';
    renderDefinition(body, entry, depth);
    tip.append(body);
  }

  const foot = document.createElement('div');
  foot.className = 'row';
  foot.style.marginTop = '8px';
  foot.style.gap = '10px';
  if (entry.raccourci) {
    const kbd = document.createElement('span');
    kbd.className = 'kbd';
    kbd.textContent = entry.raccourci;
    foot.append(kbd);
  }
  const esc = document.createElement('span');
  esc.className = 'lbl';
  esc.textContent = 'Échap ferme la pile';
  foot.append(esc);
  tip.append(foot);
  return tip;
}

// Les termes liés sont rendus comme boutons : le clavier les atteint, et une
// bulle enfant n'est pas un lien qui navigue.
function renderDefinition(host, entry, depth) {
  host.textContent = entry.definition || '';
  if (depth + 1 >= MAX_DEPTH) return;
  const related = (entry.termes || []).filter((id) => entries.has(id));
  if (!related.length) return;
  const row = document.createElement('div');
  row.className = 'row';
  row.style.marginTop = '8px';
  row.style.flexWrap = 'wrap';
  for (const id of related) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'term';
    button.dataset.term = id;
    button.textContent = entries.get(id).nom;
    button.addEventListener('click', () => open(id, button, depth + 1, true));
    row.append(button);
  }
  host.append(row);
}

function place(tip, anchor) {
  const box = anchor.getBoundingClientRect();
  tip.style.left = Math.max(8, Math.min(box.left, window.innerWidth - 288)) + 'px';
  const below = box.bottom + 8;
  tip.style.top = (below + 160 > window.innerHeight ? Math.max(8, box.top - 168) : below) + 'px';
}

/** Ouvre (ou remplace) la bulle du niveau *depth*. Trois niveaux au plus :
 * un quatrième est refusé ici, pas seulement laissé sans bouton pour l'ouvrir. */
export function open(id, anchor, depth = 0, pinned = false) {
  if (depth >= MAX_DEPTH) return null;
  const entry = entries.get(id);
  if (!entry) return null;
  closeFrom(depth);
  const tip = build(entry, depth, pinned);
  document.body.append(tip);
  if (HAS_POPOVER) tip.showPopover();
  place(tip, anchor);
  const frame = { id, tip, anchor, depth, pinned };
  stack.push(frame);
  anchor.setAttribute('aria-describedby', tip.id);
  return frame;
}

function closeFrom(depth) {
  while (stack.length && stack[stack.length - 1].depth >= depth) {
    const frame = stack.pop();
    frame.anchor.removeAttribute('aria-describedby');
    frame.tip.remove();
  }
}

/** Ferme toute la pile — c'est ce que fait Échap. */
export function closeAll() {
  closeFrom(0);
  clearTimeout(hoverTimer);
  pendingAnchor = null;
}

/** Ferme les bulles non épinglées, du sommet de la pile vers le bas — c'est ce
 * que fait un clic ailleurs. Une bulle épinglée plus profonde que la première
 * bulle non épinglée reste : la croix ou Échap restent son seul remède. */
function closeUnpinned() {
  while (stack.length && !stack[stack.length - 1].pinned) {
    const frame = stack.pop();
    frame.anchor.removeAttribute('aria-describedby');
    frame.tip.remove();
  }
  clearTimeout(hoverTimer);
  pendingAnchor = null;
}

function topUnpinned() {
  return stack.length && !stack[stack.length - 1].pinned;
}

// Balises focusables nativement mises à part, tout `[data-term]` doit
// atteindre le clavier — l'espace qui l'a posé n'a pas à le savoir (ADR-006,
// « Lot 2 ↔ tous »). Rejoué à chaque mutation du sous-arbre : les espaces
// montent leur contenu après `attach()`.
function ensureFocusable(root) {
  const scope = root.querySelectorAll ? root : document;
  for (const el of scope.querySelectorAll('[data-term]')) {
    // La bulle elle-même porte `data-term` (pour la cibler en test et en
    // CSS) mais n'est pas une ancre : elle ne doit pas devenir un arrêt Tab,
    // et le mutation observer la voit dès qu'elle s'ouvre.
    if (el.closest('.tip')) continue;
    if (!NATIVELY_FOCUSABLE.has(el.tagName) && !el.hasAttribute('tabindex')) {
      el.tabIndex = 0;
    }
  }
}

/** Branche la mécanique sur un sous-arbre. Idempotent au niveau du document. */
export function attach(root = document) {
  ensureFocusable(root);
  const watched = root === document ? document.body : root;
  new MutationObserver(() => ensureFocusable(root)).observe(watched, {
    childList: true,
    subtree: true,
  });

  const openFromAnchor = (anchor, wait) => {
    clearTimeout(hoverTimer);
    pendingAnchor = anchor;
    if (wait <= 0) {
      open(anchor.dataset.term, anchor, 0, false);
    } else {
      hoverTimer = setTimeout(() => open(anchor.dataset.term, anchor, 0, false), wait);
    }
  };

  root.addEventListener('pointerover', (event) => {
    const anchor = event.target.closest('[data-term]');
    if (!anchor || !entries.has(anchor.dataset.term)) return;
    if (anchor.closest('.tip')) return; // les termes d'une bulle s'ouvrent au clic
    openFromAnchor(anchor, delay());
  });

  root.addEventListener('pointerout', (event) => {
    const anchor = event.target.closest('[data-term]');
    if (!anchor) return;
    clearTimeout(hoverTimer);
    // Une bulle épinglée survit au départ du pointeur : c'est tout l'intérêt.
    if (topUnpinned() && !event.relatedTarget?.closest?.('.tip')) closeAll();
  });

  // Le clavier : focus ouvre tout de suite, sans le délai d'intention du
  // survol — attendre un survol qui n'arrivera jamais serait inaccessible.
  root.addEventListener('focusin', (event) => {
    const anchor = event.target.closest('[data-term]');
    if (!anchor || !entries.has(anchor.dataset.term)) return;
    if (anchor.closest('.tip')) return;
    openFromAnchor(anchor, 0);
  });

  root.addEventListener('focusout', (event) => {
    const anchor = event.target.closest('[data-term]');
    if (!anchor) return;
    clearTimeout(hoverTimer);
    if (topUnpinned() && !event.relatedTarget?.closest?.('.tip')) closeAll();
  });

  // Clic ailleurs : ferme les bulles non épinglées, laisse les épinglées à
  // leur croix ou à Échap. Un clic à l'intérieur d'une bulle (y compris sur
  // un terme lié, qui gère lui-même son ouverture) ne referme rien ici.
  document.addEventListener('click', (event) => {
    if (event.target.closest('.tip')) return;
    closeUnpinned();
  });

  // Alt fige la bulle ouverte — ou l'ouvre tout de suite si le survol court encore.
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && stack.length) {
      // La pile d'abord : la palette ou l'aide ouverte dessous ne se ferme
      // qu'à l'Échap suivant (`preventDefault` retient la demande de fermeture).
      event.preventDefault();
      event.stopPropagation();
      closeAll();
      return;
    }
    if (event.key !== 'Alt') return;
    if (stack.length) {
      const frame = stack[stack.length - 1];
      if (!frame.pinned) {
        const { id, anchor, depth } = frame;
        closeFrom(depth);
        open(id, anchor, depth, true);
      }
      return;
    }
    if (pendingAnchor) {
      clearTimeout(hoverTimer);
      open(pendingAnchor.dataset.term, pendingAnchor, 0, true);
    }
  });
}

export default { load, get, size, open, closeAll, attach, missingTerms, FOURTH_SPACE };
