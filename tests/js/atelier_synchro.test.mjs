import assert from 'node:assert/strict';
import { test } from 'node:test';
import { diffBlocks, staleFiles, syncMessage } from '../../web/workspace/spaces/atelier-synchro-logic.js';

const doc = (phases) => ({ phases });
const phase = (id, fingerprint, texts) => ({ id, fingerprint, blocks: texts.map((text, i) => ({ id: `${id}.${text}.${i}`, text })) });

test('staleFiles : les phases dont l’empreinte du fichier ne correspond plus', () => {
  const shown = doc([phase('brief', 'aa', []), phase('brainstorm', 'bb', [])]);
  assert.deepEqual(staleFiles(shown, { brief: 'aa', brainstorm: 'bb' }), []);
  assert.deepEqual(staleFiles(shown, { brief: 'aa', brainstorm: 'zz' }), ['brainstorm']);
  assert.deepEqual(staleFiles(shown, { brief: 'x', brainstorm: 'y' }), ['brief', 'brainstorm']);
});

test('staleFiles : un fichier absent des deux côtés (empreinte vide) est à jour', () => {
  assert.deepEqual(staleFiles(doc([{ id: 'brief' }]), { brief: '' }), []);
  assert.deepEqual(staleFiles(doc([phase('brief', 'aa', [])]), { brief: '' }), ['brief'], 'le fichier a disparu');
});

test('staleFiles : sans relevé exploitable, ou pour une phase non relevée : rien à recharger', () => {
  const shown = doc([phase('brief', 'aa', [])]);
  assert.deepEqual(staleFiles(shown, undefined), []);
  assert.deepEqual(staleFiles(shown, null), []);
  assert.deepEqual(staleFiles(shown, {}), []);
  assert.deepEqual(staleFiles(shown, { autre: 'x' }), []);
  assert.deepEqual(staleFiles(null, { brief: 'x' }), []);
  assert.deepEqual(staleFiles({}, { brief: 'x' }), []);
});

test('diffBlocks : rien n’a changé', () => {
  const a = doc([phase('brief', '1', ['a', 'b'])]);
  const b = doc([phase('brief', '2', ['a', 'b'])]);
  assert.deepEqual(diffBlocks(a, b), { added: [], removed: 0, count: 0 });
});

test('diffBlocks : un bloc réécrit compte pour un, et c’est le nouveau que l’on surligne', () => {
  const a = doc([phase('brief', '1', ['a', 'b', 'c'])]);
  const b = doc([phase('brief', '2', ['a', 'B', 'c'])]);
  const diff = diffBlocks(a, b);
  assert.equal(diff.count, 1);
  assert.deepEqual(diff.added.map((x) => x.id), ['brief.B.1']);
  assert.equal(diff.removed, 1);
});

test('diffBlocks : deux blocs modifiés, un ajouté, un retiré', () => {
  const a = doc([phase('brief', '1', ['a', 'b']), phase('exigences', '1', ['x', 'y'])]);
  const b = doc([phase('brief', '2', ['A', 'B']), phase('exigences', '2', ['x', 'y', 'z'])]);
  assert.equal(diffBlocks(a, b).count, 3);
  const c = doc([phase('brief', '2', ['a'])]);
  assert.deepEqual(diffBlocks(a, c), { added: [], removed: 1, count: 1 });
});

test('diffBlocks : les textes identiques se comptent un à un', () => {
  const a = doc([phase('brief', '1', ['x', 'x'])]);
  const b = doc([phase('brief', '2', ['x', 'x', 'x'])]);
  const diff = diffBlocks(a, b);
  assert.equal(diff.count, 1);
  assert.equal(diff.added.length, 1);
});

test('diffBlocks : une phase absente de l’avant est entièrement nouvelle ; documents vides sans erreur', () => {
  const diff = diffBlocks(doc([]), doc([phase('brief', '1', ['a', 'b'])]));
  assert.equal(diff.count, 2);
  assert.deepEqual(diffBlocks(null, null), { added: [], removed: 0, count: 0 });
  assert.deepEqual(diffBlocks({}, {}), { added: [], removed: 0, count: 0 });
});

test('syncMessage : la phrase de la notification, au singulier comme au pluriel', () => {
  assert.equal(syncMessage(0), '');
  assert.equal(syncMessage(1), 'Document mis à jour : 1 bloc modifié par ailleurs');
  assert.equal(syncMessage(2), 'Document mis à jour : 2 blocs modifiés par ailleurs');
});
