// Libellé d'un score de recherche — pur, sans DOM (testé sous node).
//
// Un score absent n'est pas un score nul (cadrage portail §5.3, inconnu ≠ zéro) :
// `MemoryEntry.score` vaut 0.0 par défaut, donc 0, null ou non fini s'affichent
// « score inconnu ». Un score connu est nommé selon ce qu'il est : BM25 (backend
// lexical, échelle propre au corpus, ≈ 0 quand le terme est dans presque toutes
// les entrées) ou rang RRF (fusion vectoriel + lexical, ≈ 0,016 au mieux).
// Les très petites valeurs passent en notation scientifique pour ne jamais
// s'arrondir à 0,000.

const KIND_LABEL = { rrf: 'rang RRF', bm25: 'BM25' };
const KIND_HINT = {
  rrf: 'Rang fusionné (reciprocal rank fusion) : compare les résultats entre eux, pas un degré de pertinence.',
  bm25: 'Score BM25 du backend lexical : propre à ce corpus ; proche de 0 quand le terme figure dans presque toutes les entrées.',
};

export function formatScore(score, kind) {
  const n = typeof score === 'number' ? score : Number.NaN;
  if (!Number.isFinite(n) || n === 0) {
    return { known: false, text: 'score inconnu', title: 'Le backend ne fournit pas de score pour ce résultat.' };
  }
  const abs = Math.abs(n);
  const value = abs < 0.001 ? n.toExponential(1) : n.toFixed(abs < 1 ? 3 : 2);
  const label = KIND_LABEL[kind] || 'score';
  return { known: true, text: `${label} ${value}`, title: KIND_HINT[kind] || 'Score brut du backend.' };
}
