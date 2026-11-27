// Atelier de spec : « Ce qu'il me reste à faire », la logique pure, sans DOM (tests/js/atelier_reste_a_faire.test.mjs).
//
// Le serveur calcule (`reste_a_faire` du document, voir `src/grimoire/core/cadrage_guidance.py`) ce que `cadrage check` exige encore, puis
// ce qui est utile ou facultatif. Ce module le met en mots ; il ne recompte rien.

const PHASE_NAMES = { brief: 'Brief', brainstorm: 'Brainstorm', comprehension: 'Compréhension', exigences: 'Exigences', 'cahier-des-charges': 'Cahier des charges' };

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** Une chose à faire, en mots simples : où elle est, et ce qui reste à dire (sans objet à confirmer, angle de l'entretien). */
export function itemLabel(item) {
  const place = `${PHASE_NAMES[item.phase] || item.phase_title} › ${item.section}`;
  if (item.kind === 'angle') return { place, note: `répondre à la question « ${item.angle} » de l’entretien` };
  if (item.etat === 'sans-objet') return { place, note: 'sans objet d’après toi ; à confirmer' };
  if (item.etat === 'je-ne-sais-pas') return { place, note: 'noté « je ne sais pas » ; à préciser' };
  return { place, note: '' };
}

/**
 * Le résumé à afficher : `ready`, la phrase d'en-tête, les choses nécessaires (avec leur lien), la phrase sur le facultatif.
 * Sans donnée (`reste` nul : chargement, ancien serveur), rien n'est affiché.
 */
export function restSummary(reste) {
  if (!reste) return null;
  const need = reste.necessaires || [];
  const useful = (reste.utiles || []).length;
  const optional = (reste.facultatives || []).length;
  const na = reste.sans_objet || 0;
  const bits = [];
  if (useful) bits.push(plural(useful, 'rubrique utile', 'rubriques utiles'));
  if (optional) bits.push(plural(optional, 'rubrique qui peut rester vide', 'rubriques qui peuvent rester vides'));
  let tail = '';
  if (bits.length) tail = `Le reste est facultatif : ${bits.join(' et ')}.`;
  else if (!reste.erreur) tail = 'Rien d’autre à remplir.';
  if (na) tail += ` ${plural(na, 'rubrique marquée sans objet', 'rubriques marquées sans objet')}.`;
  if (reste.erreur) {
    return { ready: false, broken: true, headline: 'L’état de l’entretien est illisible : corrige-le pour savoir ce qu’il reste à faire.', items: [], tail: '' };
  }
  if (!need.length) {
    return {
      ready: true, broken: false, items: [], tail,
      headline: 'Tout le nécessaire est rempli : le contrôle de validation passe.',
      next: 'Étape suivante : les exigences Must se valident au terminal (par la MOA, la personne qui valide), puis « Valider et découper » crée les tâches.',
    };
  }
  return {
    ready: false, broken: false, tail,
    headline: `Il manque ${plural(need.length, 'chose', 'choses')} pour pouvoir valider :`,
    items: need.map((item) => ({ ...itemLabel(item), phase: item.phase, section: item.section, kind: item.kind })),
  };
}
