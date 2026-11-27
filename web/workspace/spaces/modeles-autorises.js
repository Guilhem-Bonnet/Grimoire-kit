// Modèles autorisés — quels modèles Grimoire a le droit d'utiliser directement, et pour quel usage (épique #1477, lot B1).
//
// Tout vient de `GET /api/workspace/providers/direct-use` : ce qui est détecté sur la machine (modèles locaux d'Ollama,
// fournisseurs du registre, agents installés), ce que l'humain a autorisé par usage et la décision de maintenant. La
// lecture, comme « Vérifier maintenant », ne joint aucun service distant et ne transmet aucune clé ; « Vérifier les
// connexions » le fait, sous la clé de contrôle, après avoir dit à quels points d'accès une clé va partir.
//
// Autoriser un modèle, changer l'ordre, poser ou lever un plafond sont des gestes d'humain : chaque enregistrement passe
// par le dialogue de la clé de contrôle déjà utilisé par le pilotage (`runGesture`, pilotage.js) : il dit ce qui va se
// passer, demande la clé lue dans le terminal et la redemande si elle est refusée. Le jeton de page seul n'y suffit pas.
// La clé d'API d'un fournisseur ne se saisit JAMAIS ici : la page affiche la commande à lancer au terminal.
//
// Rien n'est écrit tant que « Enregistrer » n'est pas confirmé : les interrupteurs, l'ordre et les plafonds travaillent
// sur un brouillon par usage. Aucun `innerHTML` : tout le texte passe par `textContent`.

import { badge, spaceHead } from '../shell.js';
import { runGesture } from './pilotage.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** La commande à copier, jamais lancée d'ici. */
function formula(command) {
  const button = el('button', 'formula', null, { type: 'button', title: 'Copier la commande' });
  button.append(el('b', null, '›'), command);
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(command);
      button.title = 'Commande copiée';
    } catch {
      button.title = 'Copie refusée : recopiez la commande';
    }
  });
  return button;
}

const REASONS = {
  'hors-ligne': 'Hors ligne',
  'limite-atteinte': 'Limite atteinte',
  'non-installé': 'Non installé',
  'clé-absente': 'Clé absente',
  'désactivé': 'Désactivé',
};
const EGRESS = {
  rien: 'Rien ne sort de la machine',
  texte: 'Le texte peut sortir de la machine',
};
const COST = { 'gratuit-local': 'Gratuit (local)', abonnement: 'Abonnement', payant: 'Payant à l’usage' };
const TIER = { cheap: 'léger', mid: 'intermédiaire', strong: 'fort' };
const CAPABILITY = {
  tools: 'outils',
  structured_output: 'sortie structurée',
  vision: 'images',
  context_window: 'fenêtre de contexte',
  thinking: 'raisonnement',
  prompt_cache: 'cache de requête',
};
const PROVENANCE = { declared: 'déclaré', probed: 'sondé', documented: 'documenté' };

const EMPTY_NOTE = 'Aucun modèle autorisé : Grimoire fonctionne sans modèle. '
  + 'Autoriser un modèle local ne fait rien sortir de ta machine.';

function capabilityText(model) {
  const caps = (model.capabilities || []).map((cap) => {
    const name = CAPABILITY[cap.name] || cap.name;
    const origin = PROVENANCE[cap.provenance] || cap.provenance;
    if (cap.value === true) return `${name} (${origin})`;
    if (cap.value === false) return `sans ${name} (${origin})`;
    return `${name} : ${cap.value} (${origin})`;
  });
  return caps.length ? `Capacités connues : ${caps.join(', ')}.` : 'Capacités : inconnues, rien n’a été mesuré.';
}

function tierText(model) {
  if (!model.tier) return 'Niveau : inconnu';
  const how = model.tier_provenance ? ` (${model.tier_provenance})` : '';
  return `Niveau : ${TIER[model.tier] || model.tier}${how}`;
}

function stateBadge(model) {
  if (model.state === 'disponible') return badge('Disponible', 'ok');
  const label = REASONS[model.reason] || 'Indisponible';
  return badge(label, model.reason === 'désactivé' ? '' : 'warn', model.detail || label);
}

// ── La carte d'un modèle détecté ────────────────────────────────────────────

function modelCard(model, usedToday, onAddLocal, localEntry) {
  const card = el('li', 'ma-model', null, { 'data-role': 'model', 'data-key': model.key });
  const head = el('div', 'ma-model-head');
  const names = el('div', 'ma-model-names');
  names.append(el('span', 'ma-model-name', model.label), el('span', 'ma-sub', model.origin));
  head.append(names, stateBadge(model));
  const facts = el('div', 'ma-facts');
  facts.append(
    badge(EGRESS[model.egress] || model.egress, model.egress === 'rien' ? 'ok' : 'warn'),
    badge(COST[model.cost] || model.cost, model.cost === 'payant' ? 'warn' : ''),
  );
  if (!model.configured) facts.append(badge('Détecté, non configuré', 'acc'));
  card.append(head, facts);
  if (model.state !== 'disponible' && model.detail) card.append(el('p', 'ma-note', model.detail));
  card.append(el('p', 'ma-note', `${tierText(model)} · ${capabilityText(model)}`));
  if (model.size) card.append(el('p', 'ma-note', `Taille lue : ${model.size} de paramètres.`));
  if (typeof usedToday === 'number') {
    card.append(el('p', 'ma-note', `Consommé aujourd’hui : au moins ${usedToday} jetons (borne basse : seul ce que l’Atelier a compté).`));
  }
  if (model.key_present !== null && model.key_present !== undefined) {
    card.append(el('p', 'ma-note', model.key_present ? 'Clé présente dans le magasin de clés.' : 'Clé absente : à poser au terminal.'));
    if (model.key_command) {
      card.append(el('p', 'ma-note', 'La clé ne se saisit pas ici. Lance cette commande dans ton terminal :'), formula(model.key_command));
    }
  }
  if (model.install_hint && model.reason === 'non-installé') card.append(formula(model.install_hint));
  if (!model.configured) {
    const add = el('button', 'btn', 'Ajouter au registre', { type: 'button', 'data-role': 'add-local' });
    add.setAttribute('aria-label', `Ajouter ${model.label} au registre du projet`);
    add.addEventListener('click', () => onAddLocal(model));
    card.append(add);
    if (localEntry && !localEntry.genuine) {
      add.disabled = true;
      card.append(el('p', 'ma-warn', `L’entrée « ${localEntry.provider_id} » du registre n’est pas l’Ollama local (${localEntry.reason}) : ajout impossible, corrige-la à la main.`));
    }
  }
  return card;
}

// ── Le brouillon d'un usage : interrupteurs, ordre, plafonds ────────────────

function draftOf(usage) {
  return usage.authorized.filter((a) => a.detected).map((a) => ({ key: a.key, cap: a.daily_cap_tokens }));
}

const sameDraft = (a, b) => a.length === b.length && a.every((x, i) => x.key === b[i].key && x.cap === b[i].cap);

function parseCap(text) {
  const raw = text.trim();
  if (!raw) return { value: null };
  if (!/^\d{1,9}$/.test(raw) || Number(raw) < 1) return { error: true };
  return { value: Number(raw) };
}

function usageCard(view, usage, handlers) {
  const models = new Map(view.inventory.models.map((m) => [m.key, m]));
  const saved = draftOf(usage);
  let draft = saved.map((d) => ({ ...d }));
  const invalid = new Set(); // lignes dont le plafond saisi n'est pas un entier : « Enregistrer » reste éteint
  const used = new Map(usage.authorized.map((a) => [a.key, a.used_today]));

  const section = el('section', 'ma-usage', null, { 'data-role': 'usage', 'data-usage': usage.id, 'aria-label': usage.label });
  const title = el('h3', null, usage.label);
  const decision = el('p', 'ma-decision', null, { 'data-role': 'decision' });
  const why = el('details', 'ma-why');
  why.append(el('summary', null, 'Pourquoi ce choix ?'));
  const list = el('ul', 'ma-rows', null, { 'data-role': 'rows' });
  const actions = el('div', 'ma-actions');
  const save = el('button', 'btn pri', 'Enregistrer…', { type: 'button', 'data-role': 'save' });
  const reset = el('button', 'btn', 'Annuler les changements', { type: 'button', 'data-role': 'reset' });
  const status = el('p', 'ma-note', '', { role: 'status', 'aria-live': 'polite', 'data-role': 'usage-status' });
  actions.append(save, reset);
  section.append(title, decision, why, list, actions, status);

  const d = usage.decision;
  const sentence = d.kind === 'use' ? `Maintenant : ${d.label} — ${d.reason}.`
    : d.kind === 'propose' ? `Proposition : ${d.label} — ${d.reason}.` : d.reason;
  decision.textContent = sentence;
  decision.dataset.kind = d.kind;
  if (d.warning) decision.append(' ', el('strong', null, `Attention : ${d.warning}.`));
  if (!d.trace.length) why.hidden = true;
  const trace = el('ul', 'ma-trace');
  for (const line of d.trace) trace.append(el('li', null, `${line.label} (${line.verdict}) : ${line.why}`));
  why.append(trace);

  const dirty = () => !sameDraft(draft, saved);
  const refresh = (focus) => {
    invalid.clear();
    save.disabled = !dirty();
    reset.disabled = !dirty();
    list.replaceChildren();
    const order = draft.map((x) => x.key);
    // Un agent que la machine n'a pas installé n'est pas proposé : il se trouve, replié, dans « Ce qui est branché ici ».
    const rest = view.inventory.models.filter((m) => !order.includes(m.key) && !(m.source === 'hote' && m.reason === 'non-installé'));
    const rows = [...draft.map((x) => ({ key: x.key, on: true, cap: x.cap })), ...rest.map((m) => ({ key: m.key, on: false, cap: null }))];
    rows.forEach((row, index) => list.append(rowFor(row, index)));
    if (focus) {
      const target = list.querySelector(`[data-key="${CSS.escape(focus.key)}"] [data-control="${focus.control}"]:not(:disabled)`)
        || list.querySelector(`[data-key="${CSS.escape(focus.key)}"] [data-control="switch"]`);
      if (target) target.focus();
    }
  };

  const move = (key, delta) => {
    const at = draft.findIndex((x) => x.key === key);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= draft.length) return;
    [draft[at], draft[to]] = [draft[to], draft[at]];
    status.textContent = '';
    refresh({ key, control: delta < 0 ? 'up' : 'down' });
  };

  const rowFor = (row, index) => {
    const model = models.get(row.key);
    const li = el('li', `ma-row${row.on ? ' on' : ''}`, null, { 'data-key': row.key, 'data-on': String(row.on) });
    const label = model ? model.label : row.key;
    const toggle = el('input', null, null, {
      type: 'checkbox', role: 'switch', 'data-control': 'switch',
      'aria-label': `Autoriser ${label} pour ${usage.label}`,
    });
    toggle.checked = row.on;
    toggle.addEventListener('change', () => {
      if (toggle.checked) draft.push({ key: row.key, cap: null });
      else draft = draft.filter((x) => x.key !== row.key);
      status.textContent = '';
      refresh({ key: row.key, control: 'switch' });
    });
    const text = el('label', 'ma-row-label');
    text.append(toggle, el('span', 'ma-row-name', label));
    if (row.on) text.append(el('span', 'ma-rank', `n° ${index + 1}`));
    li.append(text);
    if (model && model.state !== 'disponible') li.append(stateBadge(model));
    if (row.on) {
      const pos = draft.findIndex((x) => x.key === row.key);
      const up = el('button', 'btn ghost', '↑', { type: 'button', 'data-control': 'up', 'aria-label': `Monter ${label} dans l’ordre de préférence` });
      const down = el('button', 'btn ghost', '↓', { type: 'button', 'data-control': 'down', 'aria-label': `Descendre ${label} dans l’ordre de préférence` });
      up.disabled = pos === 0;
      down.disabled = pos === draft.length - 1;
      up.addEventListener('click', () => move(row.key, -1));
      down.addEventListener('click', () => move(row.key, 1));
      const cap = el('input', 'ma-cap', null, {
        type: 'text', inputmode: 'numeric', autocomplete: 'off', 'data-control': 'cap',
        'aria-label': `Plafond par jour de ${label} (jetons, facultatif)`, placeholder: 'jetons/jour',
      });
      cap.value = row.cap == null ? '' : String(row.cap);
      cap.addEventListener('input', () => {
        const parsed = parseCap(cap.value);
        cap.setAttribute('aria-invalid', String(Boolean(parsed.error)));
        const entry = draft.find((x) => x.key === row.key);
        if (entry && !parsed.error) entry.cap = parsed.value;
        if (parsed.error) invalid.add(row.key); else invalid.delete(row.key);
        save.disabled = !dirty() || invalid.size > 0;
        reset.disabled = !dirty();
        status.textContent = parsed.error ? 'Plafond invalide : un nombre entier de jetons, ou rien.' : '';
      });
      li.append(up, down, cap);
      if (model && !model.usage_measured) {
        li.append(el('span', 'ma-sub', 'consommation non mesurée pour ce modèle : ce plafond ne s’appliquera qu’une fois le compteur branché', { 'data-role': 'cap-unmeasured' }));
      }
      if (typeof used.get(row.key) === 'number') {
        li.append(el('span', 'ma-sub', `consommé : au moins ${used.get(row.key)} jetons aujourd’hui (borne basse)`));
      }
      li.addEventListener('keydown', (event) => {
        if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
        event.preventDefault();
        move(row.key, event.key === 'ArrowUp' ? -1 : 1);
      });
    }
    return li;
  };

  reset.addEventListener('click', () => {
    draft = saved.map((x) => ({ ...x }));
    status.textContent = '';
    refresh();
  });
  save.addEventListener('click', () => handlers.save(usage, draft, models, status));
  refresh();
  return section;
}

// ── La page ─────────────────────────────────────────────────────────────────

function targetLine(ctx) {
  return `Cible : ${ctx.scope.name}. Geste d’humain : la clé de contrôle se lit dans le terminal, elle n’est jamais gardée.`;
}

function outgoingLine(models, keys) {
  const out = keys.map((k) => models.get(k)).filter((m) => m && m.egress === 'texte');
  if (!out.length) return 'Aucun de ces modèles ne fait sortir de texte de ta machine.';
  return `Le texte envoyé à ces modèles peut sortir de ta machine :${out.map((m) => `${m.label} (${COST[m.cost] || m.cost})`).join(', ')}.`;
}

export async function mount(root, ctx) {
  if (ctx.scope.all) {
    root.replaceChildren(ctx.empty(
      'Choisissez un projet',
      'Les modèles autorisés sont propres à un projet : sélectionnez-en un dans la portée (en haut).',
      'grimoire providers direct',
    ));
    return;
  }
  let view;
  try {
    view = await ctx.api.directUse();
  } catch (error) {
    root.replaceChildren(ctx.empty('Modèles injoignables', `L’API locale n’a pas répondu : ${error.message}`, 'grimoire doctor'));
    return;
  }
  if (ctx.signal.aborted) return;

  const wrap = el('div', 'ma-wrap', null, { 'data-role': 'modeles-autorises' });
  const draw = (next) => {
    view = next;
    wrap.replaceChildren();
    const inventory = view.inventory;
    const verify = el('button', 'btn', 'Vérifier maintenant', { type: 'button', 'data-role': 'verify' });
    const verifyStatus = el('span', 'ma-note', '', { role: 'status', 'aria-live': 'polite', 'data-role': 'verify-status' });
    const remote = el('button', 'btn', 'Vérifier les connexions…', { type: 'button', 'data-role': 'verify-remote' });
    const sub = inventory.verified
      ? `Connexions vérifiées à ${inventory.at.slice(11, 16)} (UTC)`
      : `Relu à ${inventory.at.slice(11, 16)} (UTC) : lecture locale, aucune clé transmise, aucun service distant joint`;
    wrap.append(spaceHead('Modèles autorisés', sub, verify, remote, verifyStatus));
    remote.addEventListener('click', async () => {
      remote.disabled = true;
      let changed = false;
      try {
        for (;;) {
          // Ce qui est montré est exactement ce qui est confirmé : le serveur refuse l'envoi si la liste a changé.
          const endpoints = (view.remote_endpoints || []).map((e) => ({ provider_id: e.provider_id, url: e.url }));
          const lines = [
            targetLine(ctx),
            ...(changed ? ['La liste des points d’accès a changé depuis votre dernière lecture : relisez-la ci-dessous. Rien n’a été envoyé.'] : []),
            endpoints.length
              ? `La clé d’API de chaque fournisseur ci-dessous va être ENVOYÉE à son point d’accès : ${endpoints.map((e) => `${e.provider_id} → ${e.url}`).join(' ; ')}.`
              : 'Aucune clé d’API ne sera envoyée : aucun fournisseur n’a de clé posée et de point d’accès connu.',
            'La connexion des agents installés sera aussi testée (aucun modèle n’est appelé).',
          ];
          try {
            const next2 = await runGesture(
              { title: 'Vérifier les connexions', lines, confirmLabel: 'Vérifier' },
              (answer) => ctx.api.directUseVerifyRemote(endpoints, answer.key),
            );
            if (next2 && !ctx.signal.aborted) draw(next2);
            else remote.disabled = false;
            return;
          } catch (error) {
            if (!String(error.message).includes('la liste a changé')) throw error;
            // Liste périmée : la vue est relue et le dialogue réaffiché avec la nouvelle liste.
            view = await ctx.api.directUse();
            changed = true;
            if (ctx.signal.aborted) return;
          }
        }
      } catch (error) {
        verifyStatus.textContent = `Refusé : ${error.message}`;
        remote.disabled = false;
      }
    });
    verify.addEventListener('click', async () => {
      verify.disabled = true;
      verifyStatus.textContent = 'Vérification en cours…';
      try {
        const next2 = await ctx.api.directUseVerify();
        if (!ctx.signal.aborted) draw(next2);
      } catch (error) {
        verifyStatus.textContent = `Vérification impossible : ${error.message}`;
        verify.disabled = false;
      }
    });

    const notice = el('p', 'ma-note', '', { role: 'status', 'aria-live': 'polite', 'data-role': 'notice' });
    wrap.append(notice);
    if (!view.any_authorized) wrap.append(el('p', 'ma-empty', EMPTY_NOTE, { 'data-role': 'empty-note', role: 'note' }));
    if (view.hold) wrap.append(el('p', 'ma-warn', `Une règle d’usage retient les modèles payants : ${view.hold}`));
    for (const problem of view.problems) wrap.append(el('p', 'ma-warn', problem));
    for (const gone of view.ignored) wrap.append(el('p', 'ma-warn', `Autorisation ignorée, modèle non détecté — ${gone}`));
    if (inventory.registry_error) wrap.append(el('p', 'ma-warn', inventory.registry_error));

    const detected = el('section', 'ma-card', null, { 'data-role': 'detected', 'aria-label': 'Modèles détectés' });
    detected.append(el('h3', null, 'Ce qui est branché ici'));
    if (!inventory.ollama.reachable) {
      detected.append(el('p', 'ma-note', `Ollama ne répond pas sur cette machine (${inventory.ollama.note || 'injoignable'}).`));
    }
    const used = new Map(view.usages.flatMap((u) => u.authorized.map((a) => [a.key, a.used_today])));
    const list = el('ul', 'ma-models');
    const onAddLocal = (model) => addLocal(ctx, model, view.local_ollama, draw, notice);
    // Les agents que la machine n'a pas installés restent repliés : ils ne sont pas « branchés », juste connus du kit.
    const missing = inventory.models.filter((m) => m.source === 'hote' && m.reason === 'non-installé');
    const present = inventory.models.filter((m) => !missing.includes(m));
    for (const model of present) list.append(modelCard(model, used.get(model.key), onAddLocal, view.local_ollama));
    if (!present.length) detected.append(el('p', 'ma-note', 'Aucun modèle détecté : ni registre de fournisseurs, ni Ollama, ni agent installé.'));
    detected.append(list);
    if (missing.length) {
      const more = el('details', 'ma-missing', null, { 'data-role': 'missing-hosts' });
      more.append(el('summary', null, `Agents que Grimoire sait utiliser, non installés ici (${missing.length})`));
      const rest = el('ul', 'ma-models');
      for (const model of missing) rest.append(modelCard(model, used.get(model.key), onAddLocal, view.local_ollama));
      more.append(rest);
      detected.append(more);
    }
    wrap.append(detected);

    const usages = el('div', 'ma-usages', null, { 'data-role': 'usages' });
    const handlers = { save: (usage, draft, models, status) => saveUsage(ctx, usage, draft, models, status, draw) };
    for (const usage of view.usages) usages.append(usageCard(view, usage, handlers));
    wrap.append(el('h3', 'ma-h', 'Pour quel usage'), usages);
    wrap.append(el('p', 'ma-note', 'Le repli automatique ne va jamais vers un modèle plus coûteux ni plus exposé que le courant : Grimoire te le propose. '
      + view.used_note));
    wrap.append(formula('grimoire providers direct'));
  };

  draw(view);
  root.replaceChildren(wrap);
}

async function saveUsage(ctx, usage, draft, models, status, draw) {
  const keys = draft.map((x) => x.key);
  const caps = draft.filter((x) => x.cap != null).map((x) => `${models.get(x.key)?.label || x.key} : ${x.cap} jetons par jour`);
  const lines = [
    targetLine(ctx),
    `Usage : ${usage.label}.`,
    keys.length
      ? `Modèles autorisés, par ordre de préférence : ${keys.map((k, i) => `${i + 1}. ${models.get(k)?.label || k}`).join(' ; ')}.`
      : 'Plus aucun modèle autorisé pour cet usage : Grimoire fonctionnera sans modèle.',
    caps.length ? `Plafonds : ${caps.join(' ; ')}.` : 'Aucun plafond par jour.',
    outgoingLine(models, keys),
  ];
  try {
    const next = await runGesture(
      { title: `Enregistrer les modèles autorisés — ${usage.label}`, lines, confirmLabel: 'Enregistrer' },
      (answer) => ctx.api.directUseWrite(usage.id, draft.map((x) => ({ key: x.key, daily_cap_tokens: x.cap })), answer.key),
    );
    if (next && !ctx.signal.aborted) draw(next);
  } catch (error) {
    status.textContent = `Refusé : ${error.message}`;
  }
}

async function addLocal(ctx, model, entry, draw, notice) {
  // Le texte vient de ce qui sera réellement écrit (l'entrée du registre, créée ou existante), jamais d'une formule fixe.
  const target = entry && entry.exists
    ? `à l’entrée existante « ${entry.provider_id} » (invocation : ${entry.invocation})`
    : `à une nouvelle entrée locale « ${entry ? entry.provider_id : 'ollama'} » (invocation : ${entry ? entry.invocation : 'ollama run {model} {prompt}'})`;
  const lines = [
    targetLine(ctx),
    `Ajoute ${model.label} au registre du projet, ${target}. Cette entrée est reconnue comme l’Ollama local de la machine : rien ne sort de la machine.`,
    `Son niveau sera ${model.tier ? TIER[model.tier] || model.tier : 'léger'}, ${model.tier_provenance ? model.tier_provenance : 'par prudence'}.`,
  ];
  try {
    const answer = await runGesture(
      { title: `Ajouter ${model.label} au registre`, lines, confirmLabel: 'Ajouter' },
      (gesture) => ctx.api.providersAddLocal([model.model_id], gesture.key),
    );
    if (answer && !ctx.signal.aborted) draw(await ctx.api.directUse());
  } catch (error) {
    notice.textContent = `Refusé : ${error.message}`;
  }
}
