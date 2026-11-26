import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ACTIONS, MOTION, SHAPE_COUNT, SLOW_FRAME_MS, VOCAL_ACTIONS, WIRED_ACTIONS, adaptScale, bandEdges, bandsFromSpectrum,
  captionFor, deliversOn, frameIntervalMs, isSettled, isWiredAction, isWritingCall, labelFor, levelFromSamples,
  needsVia, penPose, pickAction, shouldRun, smoothBands, smoothValue, stepWeights,
} from '../../web/workspace/mascotte_logic.js';

test('pickAction : un fait seul donne son état, sans fait le repos', () => {
  assert.equal(pickAction({}), 'repos');
  assert.equal(pickAction(), 'repos');
  assert.equal(pickAction({ busy: true }), 'reflechit');
  assert.equal(pickAction({ writing: true }), 'ecrit');
  assert.equal(pickAction({ waiting: 1 }), 'question');
  assert.equal(pickAction({ delivered: true }), 'livre');
  assert.equal(pickAction({ playing: true }), 'parle');
  assert.equal(pickAction({ recording: true }), 'ecoute');
});

test('pickAction : priorités quand plusieurs faits coexistent', () => {
  // une question en attente prime sur tout ce que fait le fil
  assert.equal(pickAction({ waiting: 2, busy: true, writing: true, delivered: true }), 'question');
  assert.equal(pickAction({ waiting: 1, busy: true }), 'question');
  // l'écriture prime sur la réflexion, qui prime sur « livré »
  assert.equal(pickAction({ writing: true, busy: true }), 'ecrit');
  assert.equal(pickAction({ busy: true, delivered: true }), 'reflechit', 'un nouveau tour l’emporte sur « livré »');
  assert.equal(pickAction({ writing: true, delivered: true }), 'ecrit');
  // la voix (vocal) : la personne qui parle, puis la parole, passent avant le fil
  assert.equal(pickAction({ playing: true, busy: true, waiting: 1 }), 'parle');
  assert.equal(pickAction({ recording: true, playing: true, waiting: 1, busy: true }), 'ecoute');
  // aucune question en attente : zéro ne compte pas
  assert.equal(pickAction({ waiting: 0, busy: true }), 'reflechit');
});

test('sans vocal (jamais recording ni playing), la mascotte vit sur les états du fil', () => {
  const fil = [
    [{}, 'repos'], [{ busy: true }, 'reflechit'], [{ busy: true, writing: true }, 'ecrit'],
    [{ busy: true, waiting: 1 }, 'question'], [{ delivered: true }, 'livre'],
  ];
  for (const [facts, expected] of fil) assert.equal(pickAction(facts), expected);
});

test('chaque action a un libellé visible lisible et une place dans le shader', () => {
  assert.deepEqual(VOCAL_ACTIONS, ['ecoute', 'parle']);
  assert.deepEqual(WIRED_ACTIONS, ['repos', 'ecoute', 'reflechit', 'parle', 'ecrit', 'question', 'livre']);
  assert.equal(captionFor('repos'), 'au repos');
  assert.equal(captionFor('reflechit'), 'réfléchit');
  assert.equal(captionFor('question'), 'attend ta réponse');
  assert.equal(captionFor('ecrit'), 'écrit');
  assert.equal(captionFor('inconnue'), 'au repos');
  assert.equal(labelFor('ecoute'), 'Mascotte : écoute');
  assert.equal(labelFor('inconnue'), 'Mascotte : au repos');
  const indexes = Object.values(ACTIONS).map((a) => a.i).sort((a, b) => a - b);
  assert.deepEqual(indexes, [...Array(SHAPE_COUNT).keys()]);
  for (const name of Object.keys(ACTIONS)) assert.ok(MOTION[name], `mouvement de ${name}`);
});

test('setAction n’accepte que les états branchés : ni propose, ni délègue, ni attend (aucun événement du fil)', () => {
  for (const name of WIRED_ACTIONS) assert.equal(isWiredAction(name), true);
  for (const name of ['propose', 'delegue', 'attend', '', undefined, null]) assert.equal(isWiredAction(name), false);
});

test('deliversOn : seule une fin normale vécue livre', () => {
  assert.equal(deliversOn('end_turn'), true);
  assert.equal(deliversOn('end_turn', true), false, 'un tour rejoué n’est pas vécu');
  for (const reason of ['cancelled', 'error', 'refusal', 'max_tokens', undefined]) assert.equal(deliversOn(reason), false);
});

test('isWritingCall : seuls les appels qui modifient des fichiers, tant qu’ils sont en cours', () => {
  assert.equal(isWritingCall({ kind: 'edit', status: 'in_progress' }), true);
  assert.equal(isWritingCall({ kind: 'edit' }), true);
  assert.equal(isWritingCall({ kind: 'delete', status: 'pending' }), true);
  assert.equal(isWritingCall({ kind: 'move', status: 'pending' }), true);
  assert.equal(isWritingCall({ kind: 'edit', status: 'completed' }), false);
  assert.equal(isWritingCall({ kind: 'edit', status: 'failed' }), false);
  for (const kind of ['read', 'search', 'execute', 'fetch', 'think', undefined]) assert.equal(isWritingCall({ kind, status: 'in_progress' }), false);
  assert.equal(isWritingCall(), false);
});

test('penPose : une base orthonormée, la pointe près du sol', () => {
  for (const t of [0, 1.3, 7]) {
    const { tip, X, Y, Z } = penPose(t);
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    assert.ok(Math.abs(dot(X, Y)) < 1e-9 && Math.abs(dot(Y, Z)) < 1e-9);
    assert.ok(Math.abs(dot(Y, Y) - 1) < 1e-9);
    assert.ok(tip[1] < -0.9 && tip[1] > -1.1);
  }
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

test('isSettled : une forme arrivée et calme est stable, sauf celles qui suivent la voix', () => {
  assert.equal(isSettled(RESTING), true);
  for (const action of ['ecoute', 'parle']) assert.equal(isSettled({ ...RESTING, action }), false);
  for (const action of ['reflechit', 'ecrit', 'question', 'livre']) assert.equal(isSettled({ ...RESTING, action }), true, action);
  assert.equal(isSettled({ ...RESTING, action: 'reflechit', swirl: 3.5, swirlTarget: 3.5 }), true);
  assert.equal(isSettled({ ...RESTING, action: 'reflechit', swirl: 1, swirlTarget: 3.5 }), false, 'le tourbillon n’est pas encore monté');
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

test('shouldRun : une forme figée arrivée ne rend aucune image, même longtemps ; une transition remet la boucle en marche', () => {
  assert.equal(shouldRun(LIVE), false);
  for (const action of ['repos', 'question', 'livre']) {
    assert.equal(shouldRun({ ...LIVE, action }), false, `${action} arrivée : figée`);
    assert.equal(shouldRun({ ...LIVE, action, settled: false }), true, `${action} : entrée en cours`);
    assert.equal(shouldRun({ ...LIVE, action, dirty: true }), true, 'première image ou changement de taille');
  }
  assert.equal(shouldRun({ ...LIVE, action: 'ecoute' }), true);
  assert.equal(shouldRun({ ...LIVE, action: 'parle' }), true);
  assert.equal(shouldRun({ ...LIVE, action: 'reflechit' }), true, 'une forme qui bouge continue, à cadence basse');
  assert.equal(shouldRun({ ...LIVE, action: 'ecrit' }), true);
});

test('frameIntervalMs : cadence très basse pour réfléchit/écrit arrivés, pleine pour la voix et pendant l’entrée', () => {
  assert.equal(frameIntervalMs({ action: 'reflechit', settled: true }), SLOW_FRAME_MS);
  assert.equal(frameIntervalMs({ action: 'ecrit', settled: true }), SLOW_FRAME_MS);
  assert.equal(frameIntervalMs({ action: 'reflechit', settled: false }), 0, 'animation d’entrée à plein régime');
  assert.equal(frameIntervalMs({ action: 'parle', settled: false }), 0);
  assert.equal(frameIntervalMs({ action: 'question', settled: true }), 0);
  assert.equal(frameIntervalMs({ action: 'parle', settled: false, reduced: true }), 50, 'plafond sous prefers-reduced-motion');
  assert.equal(frameIntervalMs({ action: 'reflechit', settled: true, reduced: true }), SLOW_FRAME_MS);
});

test('une question en attente pendant des minutes : le nombre d’images rendues reste borné', () => {
  // Simule la décision de la boucle à 60 Hz pendant 5 minutes : entrée (ressorts non calmes) 1,5 s, puis arrivée.
  let frames = 0;
  for (let i = 0; i < 5 * 60 * 60; i++) {
    const settled = i > 90;
    if (shouldRun({ ...LIVE, action: 'question', settled })) frames += 1;
  }
  assert.ok(frames <= 100, `${frames} images pour 5 minutes de question en attente`);
  // une forme qui bouge d'elle-même, à cadence basse : au plus 10 images par seconde une fois arrivée
  let slowFrames = 0;
  let last = -1e9;
  for (let ms = 0; ms < 60_000; ms += 16) {
    const settled = ms > 1500;
    if (!shouldRun({ ...LIVE, action: 'reflechit', settled })) continue;
    if (ms - last >= frameIntervalMs({ action: 'reflechit', settled })) { slowFrames += 1; last = ms; }
  }
  assert.ok(slowFrames < 10 * 60 + 100, `${slowFrames} images pour une minute de réflexion`);
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
