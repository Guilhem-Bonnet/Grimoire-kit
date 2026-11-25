import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MOA_KINDS, moaGuide, moaStage } from '../../web/workspace/spaces/a-traiter-moa.js';

const item = (kind, ref) => ({ id: `${kind}:p:x`, kind, project: { slug: 'p' }, ref });

test('une exigence non validée se dit « À valider par la MOA » avec la commande approve', () => {
  const guide = moaGuide(item('requirement_unvalidated', { stage: 'validation', task_ids: ['T1', 'T2'], questions: [], postmortem: [], requirement_keys: ['REQ-002'] }));
  assert.equal(guide.stage, 'validation');
  assert.equal(guide.word, 'À valider par la MOA');
  assert.equal(guide.who, 'La MOA');
  assert.match(guide.explain, /2 tâches sont passées/);
  assert.deepEqual(guide.commands, ['grimoire cadrage approve REQ-002']);
});

test('sans exigence rattachée, l’entretien de cadrage vient d’abord', () => {
  const guide = moaGuide(item('requirement_unvalidated', { task_ids: ['T1'], requirement_keys: [] }));
  assert.deepEqual(guide.commands, ['grimoire cadrage interview']);
  assert.ok(guide.notes.some((n) => /entretien de cadrage/.test(n)));
});

test('un item mixte garde « À valider » et note les incidents et questions', () => {
  const guide = moaGuide(item('requirement_unvalidated', { task_ids: ['T1'], postmortem: ['I1'], questions: ['Q1', 'Q2'], requirement_keys: ['REQ-1'] }));
  assert.equal(guide.word, 'À valider par la MOA');
  assert.deepEqual(guide.notes, ['1 incident exempté : post-mortem dû.', '2 questions exemptées.']);
});

test('un incident seul se dit « Post-mortem dû »', () => {
  const it = item('requirement_unvalidated', { task_ids: [], postmortem: ['I1'], questions: [] });
  assert.equal(moaStage(it), 'postmortem');
  const guide = moaGuide(it);
  assert.equal(guide.word, 'Post-mortem dû');
  assert.match(guide.who, /orchestrateur/);
  assert.match(guide.explain, /rétablit d’abord/);
});

test('une question exemptée ne demande rien et n’affiche aucune commande', () => {
  const guide = moaGuide(item('requirement_unvalidated', { task_ids: [], postmortem: [], questions: ['Q1'] }));
  assert.equal(guide.word, 'Question exemptée');
  assert.deepEqual(guide.commands, []);
});

test('une exigence livrée se dit « À recetter par la MOA » avec la commande recette par clé', () => {
  const guide = moaGuide(item('requirement_recette', { stage: 'recette', requirements: ['REQ-001', 'REQ-003'] }));
  assert.equal(guide.word, 'À recetter par la MOA');
  assert.equal(guide.who, 'La MOA');
  assert.deepEqual(guide.commands, ['grimoire cadrage recette REQ-001', 'grimoire cadrage recette REQ-003']);
});

test('une recette sans tâche de suite se dit « Suite à créer » et porte la commande du serveur', () => {
  const guide = moaGuide(item('requirement_recette', { stage: 'suite', requirements: ['REQ-001'], commands: ["grimoire task add 'x' --requirement REQ-001"] }));
  assert.equal(guide.word, 'Suite à créer');
  assert.match(guide.who, /MOE/);
  assert.deepEqual(guide.commands, ["grimoire task add 'x' --requirement REQ-001"]);
});

test('jamais de commande qui valide ou recette à la place de la MOA', () => {
  for (const guide of [
    moaGuide(item('requirement_unvalidated', { task_ids: ['T'], requirement_keys: ['REQ-1'] })),
    moaGuide(item('requirement_recette', { stage: 'recette', requirements: ['REQ-1'] })),
  ]) {
    assert.ok(guide.commands.every((c) => /^grimoire cadrage (approve|recette|interview) /.test(c) && !/--verdict|--yes|-y\b/.test(c)));
  }
});

test('un autre type n’est pas un item MOA, et les deux types ont une icône et une teinte', () => {
  assert.equal(moaGuide(item('task_blocked', {})), null);
  assert.equal(moaStage(item('task_blocked', {})), '');
  for (const kind of Object.values(MOA_KINDS)) assert.ok(kind.icon && kind.word && kind.action);
});

test('un ref absent ne casse pas le rendu', () => {
  assert.equal(moaGuide({ kind: 'requirement_recette' }).commands.length, 0);
  assert.equal(moaGuide({ kind: 'requirement_unvalidated' }).stage, 'validation');
});
