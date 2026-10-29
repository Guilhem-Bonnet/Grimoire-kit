import assert from 'node:assert/strict';
import { test } from 'node:test';

import { closedAtMs, splitRecent } from '../../web/workspace/spaces/board-split.js';

const task = (id, since) => ({ id, in_state_since: since });

test('renvoie les N plus récentes, par date de clôture décroissante, et le reste', () => {
  const tasks = [
    task('a', '2026-10-01T10:00:00Z'), task('b', '2026-10-05T10:00:00Z'),
    task('c', '2026-10-03T10:00:00Z'), task('d', '2026-10-04T10:00:00Z'),
  ];
  const { recent, older, remaining } = splitRecent(tasks, 2);
  assert.deepEqual(recent.map((t) => t.id), ['b', 'd']);
  assert.deepEqual(older.map((t) => t.id), ['c', 'a']);
  assert.equal(remaining, 2);
});

test('111 tâches, 6 gardées : 105 restantes', () => {
  const tasks = Array.from({ length: 111 }, (_, i) => task(`t${i}`, new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString()));
  const { recent, remaining } = splitRecent(tasks, 6);
  assert.equal(recent.length, 6);
  assert.equal(remaining, 105);
  assert.equal(recent[0].id, 't110');
});

test('une date absente ou illisible passe en dernier, sans exception', () => {
  const tasks = [{ id: 'sans' }, task('vieille', '2020-01-01T00:00:00Z'), task('cassée', 'pas une date'), task('neuve', '2026-10-01T00:00:00Z')];
  const { recent, older } = splitRecent(tasks, 2);
  assert.deepEqual(recent.map((t) => t.id), ['neuve', 'vieille']);
  assert.deepEqual(older.map((t) => t.id), ['sans', 'cassée']);
  assert.equal(closedAtMs({}), Number.NEGATIVE_INFINITY);
});

test('moins de tâches que la limite, ou liste vide : rien de restant', () => {
  assert.equal(splitRecent([task('a', '2026-10-01T00:00:00Z')], 6).remaining, 0);
  assert.deepEqual(splitRecent([], 6), { recent: [], older: [], remaining: 0 });
  assert.deepEqual(splitRecent(undefined), { recent: [], older: [], remaining: 0 });
});

test("ne modifie pas la liste d'entrée", () => {
  const tasks = [task('a', '2026-10-01T00:00:00Z'), task('b', '2026-10-02T00:00:00Z')];
  splitRecent(tasks, 1);
  assert.deepEqual(tasks.map((t) => t.id), ['a', 'b']);
});
