// Pilotage : quels gestes proposer selon l'état, ce que chaque confirmation dit.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  appliedPause, availableGestures, clockText, confirmLines, machineStops, pauseMeta,
} from '../../web/workspace/spaces/pilotage-logic.js';

const breaker = (extra = {}) => ({ open: false, failures: 0, threshold: 3, reason: '', source: '', ...extra });
const flag = (extra = {}) => ({
  scope: 'project', mode: 'drain', author: 'le cockpit', reason: '', since: '', foreign: false, usage_rule: false, ...extra,
});
const ids = (state, scope) => availableGestures(state, scope).map((g) => g.id);

test('sans pause ni disjoncteur ouvert : mettre en pause, arrêter net, ouvrir le disjoncteur', () => {
  const state = { pause: { project: null, machine: null }, breaker: breaker() };
  assert.deepEqual(ids(state), ['drain', 'hold', 'trip']);
  assert.deepEqual(ids(state, 'row'), ['drain'], 'la ligne de la Flotte ne propose que la pause simple');
});

test('une pause du projet propose de reprendre, et d’arrêter net seulement si elle laisse finir', () => {
  assert.deepEqual(ids({ pause: { project: flag(), machine: null }, breaker: breaker() }), ['resume', 'hold', 'trip']);
  assert.deepEqual(ids({ pause: { project: flag({ mode: 'hold' }), machine: null }, breaker: breaker() }), ['resume', 'trip']);
  assert.deepEqual(ids({ pause: { project: flag(), machine: null }, breaker: breaker() }, 'row'), ['resume']);
});

test('une pause de la machine ne se lève pas depuis le projet : aucun geste de pause n’est proposé', () => {
  const state = { pause: { project: null, machine: flag({ scope: 'global' }) }, breaker: breaker() };
  assert.deepEqual(ids(state), ['trip']);
  assert.deepEqual(ids(state, 'row'), []);
});

test('un disjoncteur ouvert propose de le refermer, jamais de l’ouvrir', () => {
  const state = { pause: { project: null, machine: null }, breaker: breaker({ open: true, reason: '3 échecs' }) };
  assert.deepEqual(ids(state), ['drain', 'hold', 'reset']);
  assert.equal(availableGestures(state).find((g) => g.id === 'trip'), undefined);
});

test('sans état lisible, aucun geste', () => {
  assert.deepEqual(availableGestures(null), []);
});

test('la pause qui s’applique est celle du projet, sinon celle de la machine', () => {
  const mine = flag();
  const machine = flag({ scope: 'global' });
  assert.equal(appliedPause({ project: mine, machine }), mine);
  assert.equal(appliedPause({ project: null, machine }), machine);
  assert.equal(appliedPause({ project: null, machine: null }), null);
  assert.equal(appliedPause(null), null);
});

test('auteur, heure et motif d’une pause se lisent ensemble', () => {
  const text = pauseMeta(flag({ author: 'la règle d’usage day:gros', reason: 'seuil atteint', since: '2026-11-25T14:02:00' }));
  assert.match(text, /^posée par la règle d’usage day:gros · à 14 h 02 · motif : seuil atteint$/);
  assert.equal(pauseMeta(flag({ since: 'pas une date' })), 'posée par le cockpit');
  assert.equal(pauseMeta(null), '');
  assert.equal(clockText('nope'), '');
});

test('reprendre une pause d’une règle d’usage le dit avant de la lever', () => {
  const rule = flag({ author: 'la règle d’usage day:gros', usage_rule: true, foreign: true, reason: 'seuil' });
  const lines = confirmLines('resume', { pause: { project: rule }, breaker: breaker() }, 'Alpha');
  assert.match(lines[0], /posée par la règle d’usage day:gros/);
  assert.match(lines[1], /peut la reposer/);
});

test('poser une pause sur une pause en place annonce qu’elle sera remplacée', () => {
  const lines = confirmLines('hold', { pause: { project: flag({ author: 'guilhem', reason: 'je tiens' }) }, breaker: breaker() }, 'Alpha');
  assert.ok(lines.some((l) => /déjà en place, posée par guilhem/.test(l) && /remplacée/.test(l)));
  assert.equal(confirmLines('drain', { pause: { project: null }, breaker: breaker() }, 'Alpha').length, 1);
});

test('refermer le disjoncteur affiche son motif d’ouverture ; l’ouvrir dit ce qui s’arrête', () => {
  const open = { pause: {}, breaker: breaker({ open: true, reason: '3 échecs consécutifs', source: 'count' }) };
  const lines = confirmLines('reset', open, 'Alpha');
  assert.match(lines[0], /Ouvert \(count\) : 3 échecs consécutifs/);
  assert.match(confirmLines('reset', { pause: {}, breaker: breaker({ open: true }) }, 'Alpha')[0], /motif non noté/);
  assert.match(confirmLines('trip', { pause: {}, breaker: breaker() }, 'Alpha')[0], /Plus aucune session/);
});

test('la pause de la machine nomme tous les projets qui seront arrêtés', () => {
  assert.match(machineStops([{ slug: 'a', name: 'Alpha' }, { slug: 'b' }]), /Alpha, b \(2 projets\)/);
  assert.match(machineStops([{ slug: 'a', name: 'Alpha' }]), /\(1 projet\)/);
  assert.match(machineStops([]), /rien à arrêter/);
});
