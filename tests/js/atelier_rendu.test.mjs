import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  isTemplateText, parseBlocks, parseInline, plainText, tableAlignments, templateHint, toggleTaskLine,
} from '../../web/workspace/spaces/atelier-rendu-logic.js';

const one = (text) => parseBlocks(text)[0];

test('un tableau GFM : en-têtes, alignements et lignes', () => {
  const table = one('| Piste | Coût | Durée |\n| :-- | --: | :-: |\n| **A** | `3 j` | [doc](https://x.fr) |\n| B | 5 j |');
  assert.equal(table.type, 'table');
  assert.deepEqual(table.head, ['Piste', 'Coût', 'Durée']);
  assert.deepEqual(table.align, ['left', 'right', 'center']);
  assert.equal(table.rows.length, 2);
  assert.deepEqual(table.rows[1], ['B', '5 j', ''], 'une ligne courte est complétée');
  assert.equal(parseInline(table.rows[0][2])[0].type, 'link');
});

test('un tableau sans barres de bord, une barre échappée, une séparation invalide', () => {
  const table = one('A | B\n--- | ---\nx \\| y | z');
  assert.deepEqual(table.rows[0], ['x | y', 'z']);
  assert.equal(one('A | B\n--- | --- | ---\nx | y').type, 'paragraph', 'colonnes différentes : pas un tableau');
  assert.equal(tableAlignments('| a | b |'), null);
  assert.deepEqual(tableAlignments('|---|:-:|'), [null, 'center']);
});

test('un tableau s’arrête à la première ligne vide', () => {
  const blocks = parseBlocks('| a | b |\n|---|---|\n| 1 | 2 |\n\nfin');
  assert.deepEqual(blocks.map((b) => b.type), ['table', 'paragraph']);
});

test('des cases à cocher : état et numéro de ligne dans le bloc', () => {
  const list = one('- [ ] écrire\n- [x] relire\n- [X] publier\n- simple');
  assert.equal(list.type, 'list');
  assert.deepEqual(list.items.map((i) => [i.task, i.line, i.text]), [
    ['todo', 0, 'écrire'], ['done', 1, 'relire'], ['done', 2, 'publier'], [null, 3, 'simple'],
  ]);
});

test('une case dans une citation n’a pas de ligne exploitable', () => {
  const quote = one('> - [ ] x');
  assert.equal(quote.type, 'quote');
  assert.equal(quote.blocks[0].items[0].line, null);
});

test('toggleTaskLine ne touche que la ligne visée', () => {
  const text = 'Intro\n- [ ] un\n- [x] deux  \n  - [ ] trois\r\n1. [ ] quatre';
  assert.equal(toggleTaskLine(text, 1, true), 'Intro\n- [x] un\n- [x] deux  \n  - [ ] trois\r\n1. [ ] quatre');
  assert.equal(toggleTaskLine(text, 2, false), 'Intro\n- [ ] un\n- [ ] deux  \n  - [ ] trois\r\n1. [ ] quatre');
  assert.equal(toggleTaskLine(text, 3, true), 'Intro\n- [ ] un\n- [x] deux  \n  - [x] trois\r\n1. [ ] quatre');
  assert.equal(toggleTaskLine(text, 4, true), 'Intro\n- [ ] un\n- [x] deux  \n  - [ ] trois\r\n1. [x] quatre');
  assert.equal(toggleTaskLine(text, 0, true), null, 'pas une case');
  assert.equal(toggleTaskLine(text, 9, true), null);
  assert.equal(toggleTaskLine(text, -1, true), null);
  assert.equal(toggleTaskLine('- [ ]', 0, true), '- [x]');
  assert.equal(toggleTaskLine('- [ ]x', 0, true), null);
});

test('listes numérotées et imbriquées', () => {
  const [ol] = parseBlocks('3. trois\n4. quatre\n   - sous-élément\n   - autre\n5. cinq');
  assert.equal(ol.ordered, true);
  assert.equal(ol.start, 3);
  assert.equal(ol.items.length, 3);
  assert.equal(ol.items[1].children[0].ordered, false);
  assert.equal(ol.items[1].children[0].items.length, 2);
  const mixed = parseBlocks('- a\n1. b');
  assert.deepEqual(mixed.map((l) => l.ordered), [false, true]);
});

test('une continuation indentée prolonge l’élément', () => {
  const [list] = parseBlocks('- une longue\n  suite\n- deux');
  assert.equal(list.items[0].text, 'une longue suite');
});

test('citations, code avec clôture, titres, règle', () => {
  const blocks = parseBlocks('### Titre\n\n> citation\n> suite\n\n```js\nconst a = 1;\n\n- [ ] pas une case\n```\n\n---\n\n#### Sous-titre\ntexte');
  assert.deepEqual(blocks.map((b) => b.type), ['heading', 'quote', 'code', 'hr', 'heading', 'paragraph']);
  assert.equal(blocks[0].level, 3);
  assert.equal(blocks[2].lang, 'js');
  assert.equal(blocks[2].text, 'const a = 1;\n\n- [ ] pas une case');
  assert.equal(blocks[1].blocks[0].lines.length, 2);
});

test('un code non refermé court jusqu’à la fin', () => {
  assert.equal(one('```\nabc').text, 'abc');
});

test('une règle ne se confond pas avec une liste', () => {
  assert.equal(one('- - -').type, 'hr');
  assert.equal(one('***').type, 'hr');
  assert.equal(one('- a').type, 'list');
});

test('inline : gras, italique, barré, code', () => {
  const nodes = parseInline('un **gras**, *ital*, ~~barré~~ et `code` __fort__ _em_');
  assert.deepEqual(nodes.filter((n) => n.type !== 'text').map((n) => n.type), ['strong', 'em', 'del', 'code', 'strong', 'em']);
  assert.equal(parseInline('snake_case_name')[0].type, 'text');
  assert.equal(parseInline('2 * 3 * 4').length, 1);
  assert.equal(parseInline('``a`b``')[0].text, 'a`b');
});

test('inline : liens, liens automatiques, schémas refusés', () => {
  const [link] = parseInline('[doc TVA](https://exemple.fr/a?b=1 "titre")');
  assert.deepEqual([link.type, link.href, link.children[0].text], ['link', 'https://exemple.fr/a?b=1', 'doc TVA']);
  assert.equal(parseInline('[x](#ancre)')[0].href, '#ancre');
  assert.equal(parseInline('[x](mailto:a@b.fr)')[0].type, 'link');
  for (const bad of ['javascript:alert(1)', 'JaVa\tScRiPt:alert(1)', 'data:text/html,x', '//evil.test/x', 'vbscript:x']) {
    const nodes = parseInline(`[clic](${bad})`);
    assert.deepEqual(nodes.map((n) => n.type), ['text'], bad);
    assert.equal(nodes[0].text, 'clic');
  }
  const bare = parseInline('voir https://exemple.fr/page. Puis <https://auto.fr/x> et (https://p.fr/a_(b)).');
  const hrefs = bare.filter((n) => n.type === 'link').map((n) => n.href);
  assert.deepEqual(hrefs, ['https://exemple.fr/page', 'https://auto.fr/x', 'https://p.fr/a_(b)']);
  assert.equal(parseInline('<javascript:alert(1)>')[0].type, 'text');
});

test('le HTML brut reste du texte', () => {
  const nodes = parseInline('<img src=x onerror=alert(1)> <b>x</b>');
  assert.ok(nodes.every((n) => n.type === 'text'));
  assert.equal(nodes.map((n) => n.text).join(''), '<img src=x onerror=alert(1)> <b>x</b>');
});

test('une image n’est pas chargée : son texte alternatif reste', () => {
  assert.equal(plainText('![schéma](https://x.fr/a.png)'), 'schéma');
  assert.ok(parseInline('![schéma](https://x.fr/a.png)').every((n) => n.type === 'text'));
});

test('plainText retire le Markdown inline et les marqueurs de bloc', () => {
  assert.equal(plainText('**trois jours**'), 'trois jours');
  assert.equal(plainText('voir [doc TVA](https://exemple.fr/tva) et `code`'), 'voir doc TVA et code');
  assert.equal(plainText('> - [ ] **à faire** ~~vite~~'), 'à faire vite');
  assert.equal(plainText('## Titre _fin_'), 'Titre fin');
  assert.equal(plainText('1. un\n2. deux'), 'un deux');
  assert.equal(plainText('<https://a.fr>'), 'https://a.fr');
  assert.equal(plainText(''), '');
  assert.equal(plainText(null), '');
  assert.equal(plainText('a \\* b'), 'a * b');
});

test('le gabarit « à compléter » et son invite', () => {
  assert.equal(isTemplateText('> À compléter — le problème.'), true);
  assert.equal(isTemplateText('  > À compléter'), true);
  assert.equal(isTemplateText('À compléter plus tard'), false, 'pas une citation');
  assert.equal(isTemplateText('> Une vraie citation'), false);
  assert.equal(isTemplateText(null), false);
  assert.equal(templateHint('> À compléter — le problème.'), 'À compléter — le problème.');
  assert.equal(templateHint(undefined), '');
});

test('un texte vide ne donne aucun bloc', () => {
  assert.deepEqual(parseBlocks(''), []);
  assert.deepEqual(parseBlocks(undefined), []);
  assert.deepEqual(parseInline(undefined), []);
});
