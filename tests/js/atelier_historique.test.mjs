import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  arrowTarget, compareLabel, dayLabel, diffLines, emptyText, groupByDay, joinNames, phaseName, versionSummary, versionTitle,
} from '../../web/workspace/spaces/atelier-historique-logic.js';

test('phaseName : le nom court, sinon le titre avant le tiret, sinon l’identifiant', () => {
  assert.equal(phaseName({ id: 'cahier-des-charges', title: 'x' }), 'Cahier des charges');
  assert.equal(phaseName({ id: 'autre', title: 'Autre — détail' }), 'Autre');
  assert.equal(phaseName({ id: 'autre' }), 'autre');
  assert.equal(phaseName(null), '');
});

test('joinNames et versionSummary : « +12 −3, Brief et Exigences »', () => {
  assert.equal(joinNames([]), '');
  assert.equal(joinNames(['Brief']), 'Brief');
  assert.equal(joinNames(['Brief', 'Exigences']), 'Brief et Exigences');
  assert.equal(joinNames(['Brief', 'Exigences', 'Brainstorm']), 'Brief, Exigences et Brainstorm');
  assert.equal(versionSummary({ added: 12, removed: 3, phases: [{ id: 'brief' }, { id: 'exigences' }] }), '+12 −3, Brief et Exigences');
  assert.equal(versionSummary({ phases: [] }), '+0 −0');
});

test('groupByDay : par jour, les modifications non commitées en tête, une date illisible à part', () => {
  const now = new Date(2026, 10, 27, 15, 0);
  const versions = [
    { id: 'w', kind: 'working', date: null },
    { id: 'a', kind: 'commit', date: new Date(2026, 10, 27, 9, 0).toISOString() },
    { id: 'b', kind: 'commit', date: new Date(2026, 10, 27, 8, 0).toISOString() },
    { id: 'c', kind: 'commit', date: new Date(2026, 10, 26, 8, 0).toISOString() },
    { id: 'd', kind: 'commit', date: new Date(2026, 9, 1, 8, 0).toISOString() },
    { id: 'e', kind: 'commit', date: 'pas une date' },
  ];
  const groups = groupByDay(versions, now);
  assert.deepEqual(groups.map((g) => g.label), ['Pas encore enregistré', 'Aujourd’hui', 'Hier', '1 octobre 2026', 'Date inconnue']);
  assert.deepEqual(groups[1].items.map((i) => i.id), ['a', 'b']);
  assert.deepEqual(groupByDay(undefined), []);
});

test('dayLabel : aujourd’hui, hier, sinon la date longue', () => {
  const now = new Date(2026, 10, 27, 1, 0);
  assert.equal(dayLabel(new Date(2026, 10, 27, 23, 0), now), 'Aujourd’hui');
  assert.equal(dayLabel(new Date(2026, 10, 26, 23, 0), now), 'Hier');
  assert.equal(dayLabel(new Date(2026, 10, 20, 12, 0), now), '20 novembre 2026');
});

test('compareLabel et versionTitle', () => {
  const iso = new Date(2026, 10, 27, 14, 32).toISOString();
  assert.match(compareLabel({ kind: 'commit', date: iso, author: 'Ada' }), /^Tu regardes la version du 27 novembre 2026 à 14:32, par Ada\.$/);
  assert.equal(compareLabel({ kind: 'commit', date: 'x', author: '' }), 'Tu regardes la version.');
  assert.match(compareLabel({ kind: 'working' }), /pas encore enregistrées/);
  assert.match(compareLabel(null), /pas encore enregistrées/);
  assert.equal(versionTitle({ kind: 'working' }), 'Modifications en cours');
  assert.equal(versionTitle({ kind: 'commit', subject: 'feat: x' }), 'feat: x');
  assert.equal(versionTitle({ kind: 'commit', subject: '', short: 'abc1234' }), 'Version abc1234');
});

test('diffLines : un signe et un libellé parlé pour chaque ligne, la couleur ne porte pas seule le sens', () => {
  const lines = diffLines({ rows: [
    { t: 'ctx', old: 1, new: 1, text: 'a' },
    { t: 'del', old: 2, new: null, text: 'b' },
    { t: 'add', old: null, new: 2, text: 'c' },
    { t: 'gap', skipped: 1 },
    { t: 'gap', skipped: 12 },
  ] });
  assert.deepEqual(lines.map((l) => [l.kind, l.mark, l.spoken]), [
    ['ctx', ' ', 'ligne inchangée'], ['del', '−', 'ligne retirée'], ['add', '+', 'ligne ajoutée'], ['gap', '', ''], ['gap', '', ''],
  ]);
  assert.deepEqual([lines[1].old, lines[1].now, lines[2].old, lines[2].now], [2, '', '', 2]);
  assert.equal(lines[3].text, '… 1 ligne inchangée …');
  assert.equal(lines[4].text, '… 12 lignes inchangées …');
  assert.deepEqual(diffLines(null), []);
});

test('emptyText : la raison du serveur, sinon un texte neutre', () => {
  assert.equal(emptyText({ reason_text: 'Pas de dépôt.' }), 'Pas de dépôt.');
  assert.match(emptyText({}), /Aucune version/);
  assert.match(emptyText(null), /Aucune version/);
});

test('arrowTarget : flèches bornées, Home et End, rien pour les autres touches', () => {
  assert.equal(arrowTarget(0, 'ArrowDown', 3), 1);
  assert.equal(arrowTarget(2, 'ArrowDown', 3), 2);
  assert.equal(arrowTarget(0, 'ArrowUp', 3), 0);
  assert.equal(arrowTarget(2, 'ArrowUp', 3), 1);
  assert.equal(arrowTarget(1, 'Home', 3), 0);
  assert.equal(arrowTarget(1, 'End', 3), 2);
  assert.equal(arrowTarget(1, 'a', 3), null);
  assert.equal(arrowTarget(0, 'ArrowDown', 0), null);
});
