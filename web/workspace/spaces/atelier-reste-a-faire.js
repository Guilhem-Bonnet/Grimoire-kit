// Atelier de spec : « Ce qu'il me reste à faire », en tête du document.
//
// Un résumé court et honnête, calculé par le serveur d'après le même critère que `cadrage check` (`reste_a_faire` du document) :
// ce qui manque pour pouvoir valider (chaque élément est un lien qui défile jusqu'à la rubrique), puis « le reste est facultatif ».
// Quand tout le nécessaire est là, il le dit et montre le geste suivant. Il complète la pastille de l'en-tête, qui compte, elle,
// tous les points au gabarit comme s'ils étaient obligatoires. Lecture seule : aucun geste d'écriture ici.
// Replié d'office sous 900 px (le document passe avant tout) : seule sa phrase d'en-tête reste visible ; déplié, il garde son choix.

import { keepFocus, stable } from './atelier-focus.js';
import { restSummary } from './atelier-reste-a-faire-logic.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** `hooks` : `goTo(phaseId, section)` défile jusqu'à la rubrique, `goSplit()` met « Valider et découper » sous les yeux, `phone()` (écran étroit). */
export function createRest(hooks) {
  const element = el('details', 'at-reste', null, { 'data-role': 'reste-a-faire' });
  element.hidden = true;
  let shown = '';
  let first = true;

  const update = (reste) => {
    const key = stable(reste || null);
    if (key === shown) return; // rien n'a changé : ni redessin, ni nouvelle annonce
    shown = key;
    const sum = restSummary(reste);
    keepFocus(element, () => {
      element.replaceChildren();
      element.hidden = !sum;
      if (!sum) return;
      if (first) { element.open = !(hooks.phone && hooks.phone()); first = false; } // ensuite, le choix de la personne
      element.setAttribute('data-ready', String(sum.ready));
      const head = el('summary', 'at-reste-sum');
      head.append(el('span', 'at-reste-h', 'Ce qu’il me reste à faire'), el('span', 'at-reste-head', sum.headline, { 'data-role': 'reste-titre' }));
      element.append(head);
      if (sum.items.length) {
        const list = el('ul', 'at-reste-list', null, { 'data-role': 'reste-necessaire' });
        for (const item of sum.items) {
          const li = el('li');
          const link = el('button', 'at-link at-reste-link', item.place, { type: 'button', 'data-act': 'reste-go', 'data-phase': item.phase, 'data-section': item.section });
          link.addEventListener('click', () => hooks.goTo(item.phase, item.section));
          li.append(link);
          if (item.note) li.append(el('span', 'at-muted', ` — ${item.note}`));
          list.append(li);
        }
        element.append(list);
      }
      if (sum.next) {
        const next = el('p', 'at-reste-next', null, { 'data-role': 'reste-suite' });
        const go = el('button', 'at-link', 'Aller à « Valider et découper »', { type: 'button', 'data-act': 'reste-split' });
        go.addEventListener('click', () => hooks.goSplit());
        next.append(`${sum.next} `, go);
        element.append(next);
      }
      if (sum.tail) element.append(el('p', 'at-muted at-reste-tail', sum.tail, { 'data-role': 'reste-facultatif' }));
    });
  };

  return { element, update };
}
