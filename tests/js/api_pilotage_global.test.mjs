// Piloter depuis la vue « Tous les projets » : chaque geste nomme son projet cible, jamais de cible implicite.
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';

const sent = [];

globalThis.fetch = async (url, options = {}) => {
  sent.push({ url: String(url), options });
  return { ok: true, status: 200, text: async () => '{"ok":true}' };
};
globalThis.location = { search: '' };

const { api, host, setChatTarget } = await import('../../web/workspace/api.js');
const {
  authorityLine, pilotConfirmLines, pilotToggle,
} = await import('../../web/workspace/spaces/pilotage-logic.js');

beforeEach(() => {
  sent.length = 0;
  host.kind = 'cockpit';
  host.readOnly = false;
  host.scopeAll = true;
  host.project = null;
  setChatTarget(null);
});

afterEach(() => {
  host.scopeAll = false;
  setChatTarget(null);
});

const writes = () => sent.filter((s) => s.options.method && s.options.method !== 'GET');

test('le pilote automatique d’un projet nommé : URL et corps portent le slug de la ligne', async () => {
  await api.autopilotSet(true, 'clé', 'projet-b');
  const [call] = writes();
  assert.ok(call.url.includes('autonomy/autopilot?project=projet-b'), call.url);
  assert.deepEqual(JSON.parse(call.options.body), { on: true, control_key: 'clé', project: 'projet-b' });
});

test('le pilote automatique sans projet nommé est refusé en vue globale', async () => {
  await assert.rejects(api.autopilotSet(true, 'clé'), /Tous les projets/);
  assert.equal(writes().length, 0);
});

test('lancer une session sur la tâche d’un projet nommé : cible dans l’URL et le corps', async () => {
  await api.taskLaunch('GAO-3', 'abc', 'projet-b');
  const [call] = writes();
  assert.ok(call.url.endsWith('/tasks/GAO-3/launch?project=projet-b'), call.url);
  assert.equal(JSON.parse(call.options.body).project, 'projet-b');
  await assert.rejects(api.taskLaunch('GAO-3', 'abc'), /Tous les projets/);
  assert.equal(writes().length, 1);
});

test('le chat n’a aucune cible tant que la personne n’en a pas choisi une', async () => {
  await assert.rejects(api.chatSend('bonjour'), /Tous les projets/);
  await assert.rejects(api.chatCancel(), /Tous les projets/);
  assert.equal(writes().length, 0);
  assert.ok(!api.chatStreamUrl(true).includes('project='));
});

test('le chat vise le projet choisi, et lui seul : flux, envoi, arrêt, hôte', async () => {
  setChatTarget('projet-b');
  assert.ok(api.chatStreamUrl(true).includes('project=projet-b'));
  await api.chatSend('bonjour');
  await api.chatCancel();
  assert.equal(writes().length, 2);
  for (const call of writes()) assert.ok(call.url.includes('project=projet-b'), call.url);
});

test('la cible du chat ne sert à aucune autre écriture', async () => {
  setChatTarget('projet-b');
  await assert.rejects(api.taskAction('GAO-1', 'cancel', { reason: 'x' }), /Tous les projets/);
  await assert.rejects(api.ideaAdd('une idée'), /Tous les projets/);
  assert.equal(writes().length, 0);
});

test('la cible du chat ne s’applique pas hors vue globale', async () => {
  host.scopeAll = false;
  host.project = 'projet-ouvert';
  setChatTarget('projet-b');
  await api.chatSend('bonjour');
  const [call] = writes();
  assert.ok(call.url.includes('project=projet-ouvert') && !call.url.includes('projet-b'), call.url);
});

const row = (autopilot, mandate) => ({ error: '', autopilot, mandate });

test('la ligne d’un projet dit le pilote et le mandat', () => {
  assert.match(authorityLine(row({ active: false }, { active: true, until: '2026-12-01T00:00:00Z' })),
    /Pilote automatique : coupé · Mandat : actif jusqu’au 2026-12-01/);
  assert.match(authorityLine(row({ active: true, since: '' }, { active: false, reason: 'expiré' })),
    /Pilote automatique : activé · Mandat : inactif \(expiré\)/);
  assert.match(authorityLine({ error: 'x', autopilot: null, mandate: null }), /lecture impossible/);
});

test('le bouton du pilote : allumer exige un mandat actif, éteindre reste toujours possible, jamais d’octroi', () => {
  assert.equal(pilotToggle(row({ active: false }, { active: true })).disabled, false);
  assert.equal(pilotToggle(row({ active: false }, { active: true })).on, true);
  const noMandate = pilotToggle(row({ active: false }, { active: false }));
  assert.equal(noMandate.disabled, true);
  assert.match(noMandate.why, /Aucun mandat actif/);
  const off = pilotToggle(row({ active: true }, { active: false }));
  assert.equal(off.disabled, false);
  assert.equal(off.on, false);
  assert.equal(pilotToggle(row({ active: false }, { active: true }), { readOnly: true }).disabled, true);
  assert.equal(pilotToggle({ error: 'x' }).disabled, true);
});

test('le dialogue du pilote nomme le projet visé', () => {
  assert.match(pilotConfirmLines(true, 'Beta').join(' '), /« Beta »/);
  assert.match(pilotConfirmLines(false, 'Beta').join(' '), /« Beta »/);
});
