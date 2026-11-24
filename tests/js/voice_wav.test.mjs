import assert from 'node:assert/strict';
import { test } from 'node:test';

import { MIC_CONSTRAINTS, SAMPLE_RATE, encodeWav, resample, toBase64 } from '../../web/workspace/voice.js';

test('le micro est demandé en mono 16 kHz, avec annulation d’écho et suppression de bruit', () => {
  assert.deepEqual(MIC_CONSTRAINTS.audio, {
    channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true,
  });
});

test('encodeWav : en-tête RIFF/WAVE PCM 16 bits mono 16 kHz et durée exacte', () => {
  const wav = encodeWav(new Float32Array(SAMPLE_RATE)); // 1 s
  const view = new DataView(wav.buffer);
  assert.equal(String.fromCharCode(...wav.subarray(0, 4)), 'RIFF');
  assert.equal(String.fromCharCode(...wav.subarray(8, 12)), 'WAVE');
  assert.equal(view.getUint16(22, true), 1); // mono
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 32000);
  assert.equal(wav.length, 44 + 32000);
});

test('encodeWav écrête au-delà de ±1', () => {
  const view = new DataView(encodeWav(new Float32Array([2, -2, 0.5])).buffer);
  assert.equal(view.getInt16(44, true), 32767);
  assert.equal(view.getInt16(46, true), -32768);
});

test('resample : 48 kHz vers 16 kHz garde la durée', () => {
  const out = resample(new Float32Array(48000), 48000);
  assert.equal(out.length, 16000);
  assert.equal(resample(new Float32Array(10), 16000).length, 10);
});

test('toBase64 supporte plusieurs Mo', () => {
  const bytes = new Uint8Array(3 * 1024 * 1024).fill(65);
  assert.equal(toBase64(bytes).length, Math.ceil(bytes.length / 3) * 4);
});
