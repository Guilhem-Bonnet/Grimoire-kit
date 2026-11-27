import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  cleanMarkdown, cleanPhase, downloadName, findAcross, joinMarkdown, matchSlices, printDateLabel, printableComments,
} from '../../web/workspace/spaces/atelier-document-logic.js';
import { PHONE_MAX, initialOpen } from '../../web/workspace/spaces/atelier-layout-logic.js';

// ── Recherche à travers les nœuds inline ────────────────────────────────────

test('findAcross : une occurrence peut commencer dans un nœud et finir dans un autre', () => {
  // « Les comptables passent <b>trois jours</b> par mois »
  const segments = ['Les comptables passent ', 'trois jours', ' par mois'];
  assert.deepEqual(findAcross(segments, 'passent trois'), [{ startSeg: 0, startOffset: 15, endSeg: 1, endOffset: 5 }]);
  assert.deepEqual(findAcross(segments, 'trois jours par'), [{ startSeg: 1, startOffset: 0, endSeg: 2, endOffset: 4 }]);
  assert.deepEqual(findAcross(segments, 'jours'), [{ startSeg: 1, startOffset: 6, endSeg: 1, endOffset: 11 }]);
  assert.deepEqual(findAcross(segments, 'comptables passent trois jours par mois'), [{ startSeg: 0, startOffset: 4, endSeg: 2, endOffset: 9 }]);
});

test('findAcross : la fin d’une occurrence pile sur la frontière reste dans le nœud qui la porte', () => {
  const segments = ['abc', 'def'];
  assert.deepEqual(findAcross(segments, 'abc'), [{ startSeg: 0, startOffset: 0, endSeg: 0, endOffset: 3 }]);
  assert.deepEqual(findAcross(segments, 'def'), [{ startSeg: 1, startOffset: 0, endSeg: 1, endOffset: 3 }]);
  assert.deepEqual(findAcross(segments, 'cd'), [{ startSeg: 0, startOffset: 2, endSeg: 1, endOffset: 1 }]);
});

test('findAcross : insensible à la casse et aux accents, plusieurs occurrences, nœuds vides ignorés, rien sans terme', () => {
  const segments = ['Été ', '', 'ÉTÉ ', 'été'];
  assert.equal(findAcross(segments, 'ete').length, 3);
  assert.deepEqual(findAcross(segments, '  '), []);
  assert.deepEqual(findAcross([], 'x'), []);
  assert.deepEqual(findAcross(null, 'x'), []);
  assert.deepEqual(findAcross(['abc'], 'zz'), []);
  // Un accent combinant dans un nœud, la lettre dans le précédent : l'occurrence couvre les deux.
  assert.deepEqual(findAcross(['cafe', '́ ok'], 'cafe'), [{ startSeg: 0, startOffset: 0, endSeg: 1, endOffset: 1 }]);
});

test('matchSlices : un morceau par nœud touché, bornes comprises', () => {
  const segments = ['Les comptables passent ', 'trois jours', ' par mois'];
  const [match] = findAcross(segments, 'passent trois jours par');
  assert.deepEqual(matchSlices(match, segments), [
    { seg: 0, start: 15, end: 23 }, { seg: 1, start: 0, end: 11 }, { seg: 2, start: 0, end: 4 },
  ]);
  const [inside] = findAcross(segments, 'jours');
  assert.deepEqual(matchSlices(inside, segments), [{ seg: 1, start: 6, end: 11 }]);
  // Concaténés, les morceaux redonnent le texte trouvé.
  assert.equal(matchSlices(match, segments).map((s) => segments[s.seg].slice(s.start, s.end)).join(''), 'passent trois jours par');
});

// ── Export propre ───────────────────────────────────────────────────────────

const BRIEF = [
  '---', 'phase: brief', 'projet: export CSV', 'status: draft', '---', '',
  '# Brief — l’intention', '', '_Une page, pas plus._', '',
  '## Le problème', '', 'Les comptables passent **trois jours** par mois.', '',
  '## L’utilisateur', '', '> À compléter — l’utilisateur.', '',
  '## Pourquoi maintenant', '', '- [ ] Clôture TVA', '- [x] Accord', '',
].join('\n');
const EMPTY_PHASE = ['---', 'phase: cahier-des-charges', '---', '', '# Cahier des charges', '', '_La consolidation._', '', '## Synthèse', '', '> À compléter — synthèse.', ''].join('\n');

test('cleanPhase : sans front matter ni gabarit, titres descendus d’un niveau, rubrique vide retirée', () => {
  const out = cleanPhase(BRIEF);
  assert.equal(out, [
    '## Brief — l’intention', '', '### Le problème', '', 'Les comptables passent **trois jours** par mois.', '',
    '### Pourquoi maintenant', '', '- [ ] Clôture TVA', '- [x] Accord',
  ].join('\n'));
  assert.ok(!out.includes('phase:') && !out.includes('À compléter') && !out.includes('_Une page'));
});

test('cleanPhase : une phase qui n’a que des gabarits est omise ; sans titre h1, le titre de repli sert', () => {
  assert.equal(cleanPhase(EMPTY_PHASE), '');
  assert.equal(cleanPhase(''), '');
  assert.equal(cleanPhase(null), '');
  assert.equal(cleanPhase('Du texte sans titre.\n', 'Brainstorm'), '## Brainstorm\n\nDu texte sans titre.');
});

test('cleanPhase : un `#` dans un bloc de code n’est pas un titre ; une sous-rubrique vide s’en va, pas celle qui a du contenu', () => {
  const md = '# Phase\n\n## A\n\n```\n# pas un titre\n```\n\n## B\n\n### B1\n\n> À compléter — b1.\n\n### B2\n\ntexte\n';
  assert.equal(cleanPhase(md), '## Phase\n\n### A\n\n```\n# pas un titre\n```\n\n### B\n\n#### B2\n\ntexte');
});

test('cleanPhase : retours chariot Windows et titre qui finit par un dièse', () => {
  assert.equal(cleanPhase('# Choix C#\r\n\r\n## Pourquoi\r\n\r\nParce que.\r\n'), '## Choix C#\n\n### Pourquoi\n\nParce que.');
});

test('cleanMarkdown : un titre de chantier, un ## par phase qui a du contenu, vide s’il n’y en a aucune', () => {
  const phases = [{ markdown: BRIEF, title: 'Brief' }, { markdown: EMPTY_PHASE, title: 'Cahier' }, { markdown: '# Exigences\n\n- REQ-001 [Must] : x\n', title: 'Exigences' }];
  const out = cleanMarkdown(phases, 'Export CSV');
  assert.ok(out.startsWith('# Export CSV\n\n## Brief — l’intention\n'));
  assert.deepEqual(out.match(/^## .+$/gm), ['## Brief — l’intention', '## Exigences']);
  assert.ok(out.endsWith('- REQ-001 [Must] : x\n'));
  assert.equal(cleanMarkdown([{ markdown: EMPTY_PHASE }], 'X'), '');
  assert.equal(cleanMarkdown(null, 'X'), '');
  assert.equal(cleanMarkdown([{ markdown: '# A\n\ntexte\n' }], ''), '## A\n\ntexte\n');
});

test('les fichiers bruts gardent l’ancien contenu exact et un nom distinct', () => {
  const phases = [{ markdown: BRIEF }, { markdown: EMPTY_PHASE }];
  assert.equal(joinMarkdown(phases), `${BRIEF}\n${EMPTY_PHASE}`);
  assert.equal(downloadName('mon-chantier'), 'mon-chantier.md');
  assert.equal(downloadName('mon-chantier', '-fichiers-bruts'), 'mon-chantier-fichiers-bruts.md');
});

// ── Impression ──────────────────────────────────────────────────────────────

test('printableComments : les fils ouverts seulement, avec leurs réponses et le nom de leur phase', () => {
  const doc = {
    phases: [
      { id: 'brief', title: 'Brief — l’intention', comments: [
        { etat: 'ouvert', auteur: 'humain', quote: 'trois\n jours', texte: ' Quel volume ? ', replies: [{ auteur: 'agent', texte: 'Environ 3000.' }, { auteur: 'humain', texte: '  ' }] },
        { etat: 'resolu', auteur: 'humain', quote: 'x', texte: 'fait' },
        { etat: 'ouvert', auteur: 'humain', quote: 'y', texte: '   ' },
      ] },
      { id: 'exigences', title: 'Exigences — quoi', comments: [{ etat: 'ouvert', auteur: 'agent', quote: '', texte: 'Précise REQ-002.' }] },
    ],
  };
  assert.deepEqual(printableComments(doc, { brief: 'Brief' }), [
    { phase: 'Brief', quote: 'trois jours', author: 'Humain', text: 'Quel volume ?', replies: [{ author: 'Agent', text: 'Environ 3000.' }] },
    { phase: 'Exigences', quote: '', author: 'Agent', text: 'Précise REQ-002.', replies: [] },
  ]);
  assert.deepEqual(printableComments(null), []);
});

test('printDateLabel : jour, mois en toutes lettres et année', () => {
  assert.match(printDateLabel(new Date(2026, 10, 27)), /^27 novembre 2026$/);
});

// ── Assistant sous 900 px ───────────────────────────────────────────────────

test('initialOpen : sous 900 px le document passe d’abord, quel que soit le choix venu d’un autre écran', () => {
  assert.equal(PHONE_MAX, 900);
  assert.equal(initialOpen('open', true, true), false);
  assert.equal(initialOpen(null, true, true), false);
  assert.equal(initialOpen('open', false), true); // le comportement d'avant, hors téléphone
  assert.equal(initialOpen('closed', true, false), false);
  assert.equal(initialOpen(null, true, false), true);
});
