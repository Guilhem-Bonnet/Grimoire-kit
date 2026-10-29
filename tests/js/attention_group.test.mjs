import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupSection, groupSections, kindsOf } from '../../web/workspace/spaces/a-traiter-group.js';

const item = (kind, task, extra = {}) => ({
  id: `${kind}:p:${task || extra.key}`, kind, project: { slug: 'p' }, ref: task ? { task_id: task } : {}, ...extra,
});

test('deux items de même task_id donnent une seule ligne portant les deux motifs', () => {
  const rows = groupSection('unblock', [item('task_blocked', 'T1'), item('directive_unacked', 'T1'), item('task_blocked', 'T2')]);
  assert.equal(rows.length, 2);
  assert.deepEqual(kindsOf(rows[0]), ['task_blocked', 'directive_unacked']);
  assert.equal(rows[0].items.length, 2);
});

test('le compte de section est le nombre de tâches distinctes', () => {
  const [section] = groupSections([{ id: 'unblock', count: 12, items: [
    ...['A', 'B', 'C', 'D'].flatMap((t) => [item('task_blocked', t), item('directive_unacked', t)]),
    ...['E', 'F', 'G', 'H'].map((t) => item('task_blocked', t)),
  ] }]);
  assert.equal(section.count, 8);
  assert.equal(section.groups.length, 8);
});

test('le motif le plus grave (premier de la section) choisit l’action', () => {
  const [row] = groupSection('unblock', [item('review_blocked', 'T1'), item('directive_unacked', 'T1')]);
  assert.equal(row.lead.kind, 'review_blocked');
});

test('les éléments sans tâche restent des lignes séparées ; l’id de groupe est stable', () => {
  const a = groupSection('repair', [item('service', '', { key: 'a' }), item('service', '', { key: 'b' })]);
  assert.equal(a.length, 2);
  const first = groupSection('unblock', [item('task_blocked', 'T1')])[0].id;
  const again = groupSection('unblock', [item('task_blocked', 'T1'), item('directive_unacked', 'T1')])[0].id;
  assert.equal(first, again);
});

test('deux projets avec la même tâche ne se mélangent pas', () => {
  const other = { ...item('task_blocked', 'T1'), project: { slug: 'q' } };
  assert.equal(groupSection('unblock', [item('task_blocked', 'T1'), other]).length, 2);
});
