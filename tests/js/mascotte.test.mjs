import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ACTIONS, SHAPE_COUNT, VOCAL_ACTIONS, adaptScale, bandEdges, bandsFromSpectrum, isSettled, isVocalAction, labelFor,
  levelFromSamples, needsVia, pickAction, shouldRun, smoothBands, smoothValue, stepWeights,
} from '../../web/workspace/mascotte_logic.js';

test('pickAction : l’écoute prime, puis la parole, puis la réflexion, sinon le repos', () => {
  assert.equal(pickAction({}), 'repos');
  assert.equal(pickAction(), 'repos');
  assert.equal(pickAction({ busy: true }), 'reflechit');
  assert.equal(pickAction({ busy: true, playing: true }), 'parle');
  assert.equal(pickAction({ busy: true, playing: true, recording: true }), 'ecoute');
  assert.equal(pickAction({ playing: true }), 'parle');
});

test('chaque action du vocal a un libellé lisible et une place dans le shader', () => {
  assert.deepEqual(VOCAL_ACTIONS, ['repos', 'ecoute', 'reflechit', 'parle']);
  assert.equal(labelFor('repos'), 'Mascotte vocale : au repos');
  assert.equal(labelFor('ecoute'), 'Mascotte vocale : écoute');
  assert.equal(labelFor('reflechit'), 'Mascotte vocale : réfléchit');
  assert.equal(labelFor('parle'), 'Mascotte vocale : parle');
  assert.equal(labelFor('inconnue'), 'Mascotte vocale : au repos');
  const indexes = Object.values(ACTIONS).map((a) => a.i).sort((a, b) => a - b);
  assert.deepEqual(indexes, [...Array(SHAPE_COUNT).keys()]);
});

test('seules les quatre actions du vocal sont commandables', () => {
  for (const name of VOCAL_ACTIONS) assert.equal(isVocalAction(name), true);
  for (const name of ['ecrit', 'propose', 'delegue', 'livre', '', undefined, null]) assert.equal(isVocalAction(name), false);
});

test('needsVia : entre deux objets il se ramasse en sphère, jamais depuis ni vers une sphère', () => {
  assert.equal(needsVia('ecoute', 'reflechit'), true);
  assert.equal(needsVia('reflechit', 'ecoute'), true);
  assert.equal(needsVia('repos', 'ecoute'), false);
  assert.equal(needsVia('ecoute', 'repos'), false);
  assert.equal(needsVia('parle', 'reflechit'), false);
  assert.equal(needsVia('reflechit', 'parle'), false);
  assert.equal(needsVia('ecoute', 'ecoute'), false);
});

test('stepWeights : le ressort converge vers la forme visée et se calme', () => {
  const w = new Array(SHAPE_COUNT).fill(0);
  const wv = new Array(SHAPE_COUNT).fill(0);
  w[0] = 1;
  let speed = 1;
  for (let i = 0; i < 600; i++) speed = stepWeights(w, wv, 'ecoute', 1 / 60);
  assert.ok(Math.abs(w[1] - 1) < 0.01, `poids de l’écoute ${w[1]}`);
  assert.ok(w[0] < 0.01);
  assert.ok(speed < 0.02);
  assert.ok(w.every((x) => x >= 0 && x <= 1.12));
});

test('stepWeights via : la sphère garde l’essentiel du poids tant qu’il se ramasse', () => {
  const w = new Array(SHAPE_COUNT).fill(0);
  const wv = new Array(SHAPE_COUNT).fill(0);
  w[2] = 1;
  for (let i = 0; i < 600; i++) stepWeights(w, wv, 'ecoute', 1 / 60, { via: true });
  assert.ok(Math.abs(w[0] - 0.72) < 0.02 && Math.abs(w[1] - 0.28) < 0.02, `sphère ${w[0]}, écoute ${w[1]}`);
  assert.ok(w[2] < 0.01);
});

test('smoothValue : monte vite, redescend plus doucement, sans dépasser la cible', () => {
  const up = smoothValue(0, 1, 1 / 60);
  const down = 1 - smoothValue(1, 0, 1 / 60);
  assert.ok(up > down, 'la montée est plus rapide que la descente');
  assert.ok(up > 0 && up < 1 && down > 0 && down < 1);
  assert.equal(smoothValue(0.5, 0.5, 0.1), 0.5);
  assert.ok(smoothValue(0, 1, 10) <= 1);
});

test('smoothBands : lisse en place vers les bandes brutes, bornées à 0..1, et rend la plus forte', () => {
  const bands = new Float32Array(8);
  const raw = [0, 0.4, 2, -1, 0, 0, 0, 0.1];
  let peak = 0;
  for (let i = 0; i < 120; i++) peak = smoothBands(bands, raw, 1 / 60);
  assert.ok(Math.abs(bands[1] - 0.4) < 0.01);
  assert.ok(Math.abs(bands[2] - 1) < 0.01, 'une bande trop forte est bornée à 1');
  assert.equal(bands[3], 0, 'une bande négative est ramenée à 0');
  assert.ok(Math.abs(peak - bands[2]) < 1e-6);
  for (let i = 0; i < 600; i++) peak = smoothBands(bands, [], 1 / 60);
  assert.ok(peak < 0.01, 'sans niveau, tout s’éteint');
});

test('levelFromSamples : valeur efficace amplifiée et bornée', () => {
  assert.equal(levelFromSamples(new Float32Array(0)), 0);
  assert.equal(levelFromSamples(null), 0);
  assert.equal(levelFromSamples(new Float32Array(256)), 0);
  assert.equal(levelFromSamples(new Float32Array(256).fill(0.5)), 1);
  const quiet = levelFromSamples(new Float32Array(256).fill(0.05));
  assert.ok(Math.abs(quiet - 0.25) < 1e-6);
});

test('bandEdges : huit bandes strictement croissantes, quel que soit le taux d’échantillonnage', () => {
  for (const [rate, bins] of [[48000, 512], [44100, 512], [16000, 512], [8000, 64]]) {
    const edges = bandEdges(rate, bins);
    assert.equal(edges.length, 9);
    for (let i = 1; i < edges.length; i++) assert.ok(edges[i] > edges[i - 1], `${rate} Hz : ${edges}`);
  }
});

test('bandsFromSpectrum : un spectre plat sous le seuil donne 0, un spectre plein donne 1', () => {
  const edges = bandEdges(48000, 512);
  const quiet = bandsFromSpectrum(new Uint8Array(512).fill(20), edges);
  const loud = bandsFromSpectrum(new Uint8Array(512).fill(255), edges);
  assert.ok(Array.from(quiet).every((x) => x === 0));
  assert.ok(Array.from(loud).every((x) => x === 1));
  const low = new Uint8Array(512);
  low.fill(255, edges[0], edges[1]);
  const mix = bandsFromSpectrum(low, edges);
  assert.equal(mix[0], 1);
  assert.equal(mix[7], 0);
});

const RESTING = {
  action: 'repos', speed: 0, vy: 0, sy: 1, audio: 0, peak: 0, wobble: 0.06, wobbleTarget: 0.06, swirl: 1, via: false,
};

test('isSettled : seul le repos calme est stable', () => {
  assert.equal(isSettled(RESTING), true);
  for (const action of ['ecoute', 'reflechit', 'parle']) assert.equal(isSettled({ ...RESTING, action }), false);
  assert.equal(isSettled({ ...RESTING, via: true }), false);
  assert.equal(isSettled({ ...RESTING, speed: 0.5 }), false);
  assert.equal(isSettled({ ...RESTING, vy: 0.3 }), false);
  assert.equal(isSettled({ ...RESTING, sy: 1.05 }), false);
  assert.equal(isSettled({ ...RESTING, audio: 0.2 }), false);
  assert.equal(isSettled({ ...RESTING, peak: 0.2 }), false);
  assert.equal(isSettled({ ...RESTING, wobble: 0.3 }), false);
  assert.equal(isSettled({ ...RESTING, swirl: 3 }), false);
});

const LIVE = { started: true, visible: true, docHidden: false, action: 'repos', settled: true, dirty: false };

test('shouldRun : au repos stable, aucune image ; une action ou une transition remet la boucle en marche', () => {
  assert.equal(shouldRun(LIVE), false);
  assert.equal(shouldRun({ ...LIVE, settled: false }), true, 'transition en cours');
  assert.equal(shouldRun({ ...LIVE, action: 'ecoute' }), true);
  assert.equal(shouldRun({ ...LIVE, action: 'reflechit', settled: true }), true);
  assert.equal(shouldRun({ ...LIVE, action: 'parle' }), true);
  assert.equal(shouldRun({ ...LIVE, dirty: true }), true, 'première image ou changement de taille');
});

test('shouldRun : jamais hors écran, onglet caché, arrêtée, détruite ou en échec', () => {
  const busy = { ...LIVE, action: 'parle', settled: false, dirty: true };
  assert.equal(shouldRun(busy), true);
  assert.equal(shouldRun({ ...busy, visible: false }), false);
  assert.equal(shouldRun({ ...busy, docHidden: true }), false);
  assert.equal(shouldRun({ ...busy, started: false }), false);
  assert.equal(shouldRun({ ...busy, destroyed: true }), false);
  assert.equal(shouldRun({ ...busy, failed: true }), false);
});

test('adaptScale : baisse quand les images sont lentes, remonte quand elles sont rapides, reste bornée', () => {
  assert.ok(adaptScale(0.7, 40) < 0.7);
  assert.equal(adaptScale(0.4, 100), 0.4);
  assert.ok(adaptScale(0.7, 8) > 0.7);
  assert.equal(adaptScale(1, 5), 1);
  assert.equal(adaptScale(0.7, 18), 0.7);
});
