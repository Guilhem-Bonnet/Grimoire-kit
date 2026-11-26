// Portée « Tous les projets » : aucune écriture ne part sans projet cible explicite.
//
// Le client refuse (erreur visible, rien d'envoyé) plutôt que de deviner ; le
// geste lancé depuis la ligne d'un projet porte le slug de CETTE ligne, jamais
// celui du projet servi.
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';

const sent = [];

globalThis.fetch = async (url, options = {}) => {
  sent.push({ url: String(url), options });
  return { ok: true, status: 200, text: async () => '{"ok":true}' };
};
globalThis.location = { search: '' };

const { api, host } = await import('../../web/workspace/api.js');

beforeEach(() => {
  sent.length = 0;
  host.kind = 'cockpit';
  host.readOnly = false;
  host.scopeAll = true;
  host.project = null;
});

afterEach(() => {
  host.scopeAll = false;
  host.project = null;
});

const writes = () => sent.filter((s) => s.options.method && s.options.method !== 'GET');

test('en portée globale, une écriture sans cible est refusée et rien n’est envoyé', async () => {
  await assert.rejects(api.taskAction('GAO-1', 'cancel', { reason: 'x' }), (error) => {
    assert.equal(error.payload.noTarget, true);
    assert.match(error.message, /Tous les projets/);
    return true;
  });
  await assert.rejects(api.ideaAdd('une idée'), /Tous les projets/);
  await assert.rejects(api.taskAdd({ title: 't', acceptance: 'a' }), /Tous les projets/);
  await assert.rejects(api.chatSend('bonjour'), /Tous les projets/);
  await assert.rejects(api.memoryDelete('mem-1'), /Tous les projets/);
  assert.equal(writes().length, 0);
});

test('un projet « servi » résiduel ne devient jamais la cible d’une écriture globale', async () => {
  host.project = 'projet-servi';
  await assert.rejects(api.taskAction('GAO-1', 'cancel', { reason: 'x' }), /Tous les projets/);
  assert.equal(writes().length, 0);
});

test('le geste d’une ligne porte le slug de la ligne, dans l’URL et dans le corps', async () => {
  host.project = 'projet-servi';
  await api.taskAction('GAO-7', 'cancel', { reason: 'x' }, 'projet-ligne');
  const [call] = writes();
  assert.ok(call.url.includes('project=projet-ligne'), call.url);
  assert.ok(!call.url.includes('projet-servi'), call.url);
  assert.equal(JSON.parse(call.options.body).project, 'projet-ligne');
});

test('une écriture globale annonce sa portée à l’hôte', async () => {
  await api.taskAction('GAO-7', 'comment', { text: 'a' }, 'projet-ligne');
  const [call] = writes();
  assert.equal(call.options.headers['X-Grimoire-Scope'], 'all');
});

test('la suppression mémoire d’un projet nomme ce projet', async () => {
  await api.memoryDelete('mem-1', 'projet-ligne');
  const [call] = writes();
  assert.equal(JSON.parse(call.options.body).project, 'projet-ligne');
});

test('l’entretien du registre reste possible sans cible', async () => {
  await api.registryAdd('/tmp/x');
  assert.equal(writes().length, 1);
});

test('un calcul (valider, simuler un blueprint) part sans cible ; une écriture de blueprint non', async () => {
  await api.blueprintValidate('bp', { nodes: [] });
  await api.blueprintSimulate('bp', { nodes: [] });
  assert.equal(writes().length, 2);
  await assert.rejects(api.blueprintCompile('bp', { nodes: [] }), /Tous les projets/);
  await assert.rejects(api.blueprintPut('bp', { nodes: [] }), /Tous les projets/);
  assert.equal(writes().length, 2);
});

test('hors portée globale, la cible ambiante reste celle du projet ouvert', async () => {
  host.scopeAll = false;
  host.project = 'projet-ouvert';
  await api.taskAction('GAO-7', 'comment', { text: 'a' });
  const [call] = writes();
  assert.ok(call.url.includes('project=projet-ouvert'), call.url);
  assert.equal(call.options.headers['X-Grimoire-Scope'], undefined);
});
