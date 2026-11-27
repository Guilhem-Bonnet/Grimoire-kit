// Atelier de spec : les entrées de la palette ⌘K, côté logique pure (aucun DOM ; testé par tests/js/atelier_palette.test.mjs).
//
// La palette (shell.js) filtre une liste d'entrées chargée une fois, sans casse ni accents. Ici : la forme de ces entrées pour les
// chantiers de la bibliothèque et pour le plan du chantier ouvert (« Aller à : Exigences », « Aller à : Brief › Le problème »).

const STATE_LABEL = {
  'en-discussion': 'En discussion', 'pret-a-valider': 'Prêt à valider', valide: 'Validé', 'en-cours': 'En cours',
  idee: 'Idée', livre: 'Livré', archive: 'Archivé',
};

export const CHANTIER_SECTION = 'Chantiers';
export const OUTLINE_SECTION = 'Dans ce chantier';

/** Les chantiers lisibles de la bibliothèque : titre, état, slug (ceux qui sont à réparer ne s'ouvrent pas). */
export function chantierEntries(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.filter((row) => row && row.slug && !row.error).map((row) => ({
    slug: String(row.slug),
    label: String(row.titre || row.slug),
    hint: `Chantier · ${STATE_LABEL[row.etat] || row.etat || 'sans état'}`,
  }));
}

/**
 * Les destinations du chantier ouvert : chaque phase, puis chacune de ses rubriques. `outline` : `[{ id, title, rubriques:
 * [{ name }] }]` (celui du plan). `rub` : le rang de la rubrique dans la phase, ou -1 pour la phase elle-même.
 */
export function outlineEntries(outline) {
  const out = [];
  for (const phase of Array.isArray(outline) ? outline : []) {
    out.push({ label: `Aller à : ${phase.title}`, hint: 'Phase', phase: phase.id, rub: -1 });
    (phase.rubriques || []).forEach((rub, index) => {
      out.push({ label: `Aller à : ${phase.title} › ${rub.name}`, hint: 'Rubrique', phase: phase.id, rub: index });
    });
  }
  return out;
}
