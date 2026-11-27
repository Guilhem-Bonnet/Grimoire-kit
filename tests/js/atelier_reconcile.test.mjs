import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SELF, hashText, planChildren, plainKey, sigOf } from '../../web/workspace/spaces/atelier-reconcile-logic.js';

const item = (key, sig = 's', busy = false) => ({ key, sig, busy });
const shape = (plan) => plan.slots.map((s) => `${s.from}${s.from === 'old' ? s.old : s.new}${s.changed ? '!' : ''}${s.stale ? '?' : ''}`);

test('hashText et sigOf : stables, indépendants de l’ordre des clés', () => {
  assert.equal(hashText('abc'), hashText('abc'));
  assert.notEqual(hashText('abc'), hashText('abd'));
  assert.equal(sigOf({ a: 1, b: 2 }), sigOf({ b: 2, a: 1 }));
  assert.notEqual(sigOf({ a: 1 }), sigOf({ a: 2 }));
});

test('plainKey : le rang départage deux blocs identiques', () => {
  const seen = new Map();
  const a = plainKey('paragraphe', 'même texte', seen);
  const b = plainKey('paragraphe', 'même texte', seen);
  assert.notEqual(a, b);
  assert.equal(plainKey('paragraphe', 'même texte', new Map()), a);
});

test('inchangé : tous les anciens nœuds restent, rien à retirer', () => {
  const plan = planChildren([item('a'), item('b')], [item('a'), item('b')]);
  assert.deepEqual(shape(plan), ['old0', 'old1']);
  assert.deepEqual(plan.drop, []);
});

test('signature différente : le nouveau nœud remplace l’ancien, qui est retiré', () => {
  const plan = planChildren([item('a'), item('b', 'x')], [item('a'), item('b', 'y')]);
  assert.deepEqual(shape(plan), ['old0', 'new1']);
  assert.deepEqual(plan.drop, [1]);
});

test('un nœud occupé n’est jamais remplacé, même si sa signature diffère (changed)', () => {
  const plan = planChildren([item('a', 'x', true)], [item('a', 'y')]);
  assert.deepEqual(shape(plan), ['old0!']);
  assert.deepEqual(plan.drop, []);
});

test('un nœud qui se met à jour lui-même reste toujours', () => {
  const plan = planChildren([item('live:R', 'x')], [item('live:R', SELF)]);
  assert.deepEqual(shape(plan), ['old0']);
});

test('insertion et suppression : les voisins gardent leur nœud', () => {
  const plan = planChildren([item('a'), item('b'), item('c')], [item('a'), item('n'), item('c')]);
  assert.deepEqual(shape(plan), ['old0', 'new1', 'old2']);
  assert.deepEqual(plan.drop, [1]);
});

test('un bloc occupé dont la clé a changé prend la place de son homologue du nouveau document', () => {
  const old = [item('blk:a'), item('blk:b', 's', true), item('blk:c')];
  const next = [item('blk:a'), item('blk:b2'), item('blk:c')];
  const plan = planChildren(old, next);
  assert.deepEqual(shape(plan), ['old0', 'old1?', 'old2']);
  assert.equal(plan.slots[1].new, 1);
  assert.deepEqual(plan.drop, []);
});

test('un bloc occupé dont la clé a disparu reste à sa place, sans homologue', () => {
  const old = [item('blk:a'), item('blk:b', 's', true), item('blk:c')];
  const plan = planChildren(old, [item('blk:a'), item('blk:c')]);
  assert.deepEqual(shape(plan), ['old0', 'old1?', 'old2']);
  assert.equal(plan.slots[1].new, -1);
});

test('l’homologue est de la même famille : une suggestion n’est pas l’homologue d’un bloc', () => {
  const old = [item('blk:b', 's', true)];
  const plan = planChildren(old, [item('sugg:1')]);
  assert.deepEqual(shape(plan), ['old0?', 'new0']);
  assert.equal(plan.slots[0].new, -1);
});

test('un formulaire occupé sans clé reste, devant ses voisins', () => {
  const old = [item('', '', true), item('com:1')];
  const plan = planChildren(old, [item('com:1'), item('com:2')]);
  assert.deepEqual(shape(plan), ['old0?', 'old1', 'new1']);
});

test('deux anciens occupés ne prennent pas le même homologue', () => {
  const old = [item('blk:a', 's', true), item('blk:b', 's', true)];
  const plan = planChildren(old, [item('blk:a2'), item('blk:b2')]);
  assert.deepEqual(shape(plan), ['old0?', 'old1?']);
  assert.deepEqual(plan.slots.map((s) => s.new), [0, 1]);
});
