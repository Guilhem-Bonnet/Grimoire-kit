// Atelier de spec : la logique pure des invites de rubrique, sans DOM (testée par tests/js/atelier_invites.test.mjs).
//
// Le serveur donne, pour chaque rubrique d'une phase (`phase.rubriques`, voir `src/grimoire/core/cadrage_guidance.py`) : ce qu'on y
// met, un exemple, son statut (`necessaire` | `utile` | `facultatif`, tiré du contrôle `cadrage check`) et son état (`rempli` | `vide` |
// `je-ne-sais-pas` | `sans-objet`). Rien n'est recalculé ici : ce module choisit les mots et compose le texte d'un « sans objet ».

/** La rubrique d'un bloc (par le nom de sa rubrique) parmi celles de sa phase ; nul si le serveur n'en a pas donné. */
export function rubricOf(phase, block) {
  const list = (phase && phase.rubriques) || [];
  return list.find((r) => r.section === (block && block.section)) || null;
}

/** La signature de ce que l'invite affiche : un changement d'état ou de statut redessine le bloc. */
export const rubricSig = (rub) => (rub ? [rub.statut, rub.etat, rub.raison, rub.pourquoi] : null);

/** Une raison saisie : une seule ligne, bornée, sans guillemets de citation en tête (elle va dans une ligne de gabarit). */
export function cleanReason(text) {
  const flat = String(text == null ? '' : text).replace(/\s+/g, ' ').replace(/^[>:\s]+/, '').trim();
  return flat.length > 200 ? `${flat.slice(0, 199)}…` : flat;
}

/** La ligne que l'on écrit dans la rubrique : le gabarit du kit (ignoré par le contrôle), avec la raison si elle est donnée. */
export function markerText(rub, reason) {
  const why = cleanReason(reason);
  return `${rub.marqueur}${why ? ` : ${why}` : ''} (à confirmer)`;
}

/** Vrai si la ligne a été posée par la page (et peut être remise au gabarit d'ici) ; celle de l'entretien se défait par l'entretien. */
export const isPageMarker = (rub, text) => String(text || '').trimStart().startsWith(rub.marqueur);

/** Le statut en mots, avec son niveau d'importance (`high` : nécessaire pour valider ; `mid` : utile ; `low` : peut rester vide). */
export function statusInfo(rub) {
  const level = rub.statut === 'necessaire' ? 'high' : rub.statut === 'utile' ? 'mid' : 'low';
  return { label: rub.statut_libelle, level, why: rub.pourquoi };
}

/** Ce que dit une rubrique marquée « sans objet » : pour une rubrique nécessaire, elle reste à confirmer pour valider. */
export function notApplicableNote(rub) {
  const base = rub.statut === 'necessaire'
    ? 'Sans objet d’après toi ; restera à confirmer pour valider.'
    : 'Sans objet pour ce besoin : elle n’est plus comptée dans ce qu’il reste à faire.';
  return rub.raison ? `${base} Raison : ${rub.raison}.` : base;
}
