import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildOutline, countLabel, countWords, downloadName, findMatches, fold, foldWithMap, joinMarkdown, pickCurrent,
  planOpenDefault, readingMinutes, rubriqueName, statsLabel, stepIndex,
} from '../../web/workspace/spaces/atelier-document-logic.js';

test('fold ôte la casse et les accents, et foldWithMap relie chaque unité au texte réel', () => {
  assert.equal(fold('Éléphant À LA Plage'), 'elephant a la plage');
  assert.equal(fold(null), '');
  const { folded, map, length } = foldWithMap('été');
  assert.equal(folded, 'ete');
  assert.equal(length, 4);
  assert.deepEqual(map, [0, 2, 3]);
});

test('findMatches : insensible à la casse et aux accents, index du texte réel', () => {
  const text = 'Été, ete, ÉTÉ et été.';
  const hits = findMatches(text, 'ete');
  assert.equal(hits.length, 4);
  for (const { start, end } of hits) assert.equal(fold(text.slice(start, end)), 'ete');
  assert.deepEqual(findMatches('abc', ''), []);
  assert.deepEqual(findMatches('abc', '   '), []);
  assert.deepEqual(findMatches('abc', 'zz'), []);
  assert.deepEqual(findMatches('aaaa', 'aa'), [{ start: 0, end: 2 }, { start: 2, end: 4 }]);
  assert.deepEqual(findMatches('naïve', 'AÏV'), [{ start: 1, end: 4 }]);
  // Une lettre décomposée (e + accent combinant) se relit en entier, accent compris.
  assert.deepEqual(findMatches('ét', 'e'), [{ start: 0, end: 2 }]);
  // Un caractère hors plan de base (deux unités de code) ne casse pas les index.
  assert.deepEqual(findMatches('😀 ok', 'ok'), [{ start: 3, end: 5 }]);
});

test('stepIndex boucle dans les deux sens et gère le vide', () => {
  assert.equal(stepIndex(-1, 0, 1), -1);
  assert.equal(stepIndex(-1, 3, 1), 0);
  assert.equal(stepIndex(-1, 3, -1), 2);
  assert.equal(stepIndex(2, 3, 1), 0);
  assert.equal(stepIndex(0, 3, -1), 2);
  assert.equal(stepIndex(1, 3, 1), 2);
  assert.equal(stepIndex(9, 3, 1), 0);
});

test('countLabel : « 3 sur 12 », « Aucun résultat », vide sans terme', () => {
  assert.equal(countLabel(2, 12, true), '3 sur 12');
  assert.equal(countLabel(-1, 0, true), 'Aucun résultat');
  assert.equal(countLabel(0, 5, false), '');
});

test('countWords ignore le front matter, les commentaires et la mise en forme', () => {
  const md = '---\ntitre: x\n---\n# Titre du brief\n\n- **gras** et `code` aujourd’hui\n<!-- caché -->\n> citation l’été\n';
  assert.equal(countWords(md), 9);
  assert.equal(countWords(''), 0);
  assert.equal(countWords(null), 0);
  assert.equal(countWords('un-deux trois'), 2);
});

test('readingMinutes et statsLabel', () => {
  assert.equal(readingMinutes(0), 0);
  assert.equal(readingMinutes(1), 1);
  assert.equal(readingMinutes(230), 1);
  assert.equal(readingMinutes(231), 2);
  assert.equal(statsLabel(0), 'Document vide');
  assert.equal(statsLabel(1), '1 mot · 1 min de lecture');
  assert.match(statsLabel(1500), /^1\s500 mots · 7 min de lecture$/u);
});

test('joinMarkdown concatène les phases lisibles telles que stockées, séparées d’une ligne vide', () => {
  const phases = [{ markdown: '# A\n\ntexte\n' }, { markdown: '' }, { markdown: '# B\r\n\r\nfin' }, {}];
  assert.equal(joinMarkdown(phases), '# A\n\ntexte\n\n# B\r\n\r\nfin\n');
  assert.equal(joinMarkdown([]), '');
  assert.equal(joinMarkdown(null), '');
});

test('downloadName garde des caractères sûrs', () => {
  assert.equal(downloadName('mon-chantier'), 'mon-chantier.md');
  assert.equal(downloadName('a/b c'), 'a-b-c.md');
  assert.equal(downloadName(''), 'chantier.md');
  assert.equal(downloadName('../..'), '..-...md');
});

test('rubriqueName retire les dièses', () => {
  assert.equal(rubriqueName('## Contexte  '), 'Contexte');
  assert.equal(rubriqueName(undefined), '');
});

const DOC = {
  phases: [
    {
      id: 'brief', title: 'Brief',
      blocks: [
        { kind: 'rubrique', text: '## Contexte' }, { kind: 'paragraphe', section: 'Contexte', text: 'écrit' },
        { kind: 'rubrique', text: '## Objectif' }, { kind: 'paragraphe', section: 'Objectif', text: 'À compléter', placeholder: true },
      ],
    },
    {
      id: 'exigences', title: 'Exigences',
      blocks: [{ kind: 'rubrique', text: '## Must' }, { kind: 'paragraphe', section: 'Must', text: 'x' }],
    },
    { id: 'vide', title: 'Sans rubrique', blocks: [] },
  ],
  avancement: { phases: [{ id: 'brief', sections: { Contexte: 'rempli', Objectif: 'vide' } }] },
};

test('buildOutline : états lus de l’avancement, repli sur les blocs, phase remplie seulement si tout est couvert', () => {
  const out = buildOutline(DOC, { brief: 'Le brief' });
  assert.deepEqual(out[0], { id: 'brief', title: 'Le brief', state: 'vide', rubriques: [{ name: 'Contexte', state: 'rempli' }, { name: 'Objectif', state: 'vide' }] });
  assert.deepEqual(out[1].rubriques, [{ name: 'Must', state: 'rempli' }]);
  assert.equal(out[1].state, 'rempli');
  assert.equal(out[2].state, 'vide');
  const covered = { ...DOC, avancement: { phases: [{ id: 'brief', sections: { Contexte: 'rempli', objectif: 'je-ne-sais-pas' } }] } };
  const again = buildOutline(covered);
  assert.equal(again[0].state, 'rempli');
  assert.equal(again[0].rubriques[1].state, 'couvert');
  assert.deepEqual(buildOutline(null), []);
});

test('pickCurrent : le premier de la bande, sinon le précédent, en bas de page le dernier', () => {
  const order = ['a', 'b', 'c'];
  assert.equal(pickCurrent(order, new Set(['b', 'c']), 'a'), 'b');
  assert.equal(pickCurrent(order, new Set(), 'c'), 'c');
  assert.equal(pickCurrent(order, new Set(), 'c', false, true), 'a', 'tout en haut : le premier');
  assert.equal(pickCurrent(order, new Set(['b']), 'c', true, true), order[order.length - 1], 'en bas, le dernier l’emporte');
  assert.equal(pickCurrent(order, new Set(), 'zz'), 'a');
  assert.equal(pickCurrent(order, new Set(['a']), 'a', true), 'c');
  assert.equal(pickCurrent([], new Set(), ''), '');
});

test('planOpenDefault : le choix mémorisé l’emporte, replié sous 1100 px, déplié seulement si la feuille garde sa place', () => {
  assert.equal(planOpenDefault('open', 500), true);
  assert.equal(planOpenDefault('closed', 2000), false);
  assert.equal(planOpenDefault(null, 1099), false);
  assert.equal(planOpenDefault(null, 1100), false); // 1100 - 48 - 228 = 824 : la feuille serait trop étroite
  assert.equal(planOpenDefault(null, 1116), true); // 840 pile, sans assistant
  assert.equal(planOpenDefault(null, 1316, 1100, true), false); // l'assistant ouvert prend aussi sa place
  assert.equal(planOpenDefault(null, 1456, 1100, true), true);
  assert.equal(planOpenDefault(null, 1796, 1100, true), true); // le document plafonne à 1480
  assert.equal(planOpenDefault(null, 1796, 1100, true) && planOpenDefault(null, 1455, 1100, true), false);
  assert.equal(planOpenDefault('n’importe quoi', 1400), true);
  assert.equal(planOpenDefault(null, 1400), true);
});
