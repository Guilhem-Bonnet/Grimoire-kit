import assert from 'node:assert/strict';
import { test } from 'node:test';

import { displayOrder } from '../../web/workspace/voice_outbox.js';

const msg = (id, order, kind = 'report', answered = false) => ({ id, order, kind, answered });

test('une question sans réponse passe en tête, quel que soit son rang d’arrivée', () => {
  const ordered = displayOrder([msg('a', 1), msg('b', 2), msg('q', 3, 'question'), msg('c', 4)]);
  assert.deepEqual(ordered.map((m) => m.id), ['q', 'a', 'b', 'c']);
});

test('les questions en attente gardent leur ordre, une question répondue rentre dans le rang', () => {
  const ordered = displayOrder([msg('q1', 1, 'question'), msg('r', 2), msg('q2', 3, 'question'), msg('old', 0, 'question', true)]);
  assert.deepEqual(ordered.map((m) => m.id), ['q1', 'q2', 'old', 'r']);
});
