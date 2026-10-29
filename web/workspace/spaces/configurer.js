// Configuration — les trois rubriques en lecture (P66) : Agents, Hôtes et
// hooks, Fournisseurs et budgets.
//
// Rien ici n'écrit. Passer un hook en observation, le promouvoir, remettre un
// budget à zéro sont des écritures : le cockpit n'en propose pas (P01/P02), il
// donne la commande à copier quand elle existe. Les données viennent de trois
// routes GET : /api/workspace/hooks, /providers et /agents (celle qui nourrit
// déjà la table Agents de la fiche projet).
//
// Composants partagés (P21) : en-tête, badge, segments, tableau, formule. Le CSS
// propre à cet espace tient en quelques lignes `cf-`, en tokens uniquement.

import { badge, spaceHead } from '../shell.js';

const STYLE_ID = 'cf-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .cf-wrap { padding: var(--sp-6); max-width: 1100px; width: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: var(--sp-4); }
    .cf-wrap .tbl { table-layout: fixed; }
    .cf-id { font-family: var(--mono); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cf-muted { color: var(--ink2); }
    .cf-note { margin: 0; color: var(--ink2); font-size: var(--t-s); }
    .cf-panel { display: flex; flex-direction: column; gap: var(--sp-4); min-height: 100%; }
    .cf-panel-title { font-size: var(--t-min); color: var(--ink3); }
    .cf-panel-sub { font-size: var(--t-s); color: var(--ink2); }
    .cf-prov { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--sp-2); padding: 9px 0; border-top: 1px solid var(--line); align-items: center; }
    .cf-prov-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
    .cf-prov-sub { font-size: var(--t-min); color: var(--ink3); overflow: hidden; text-overflow: ellipsis; }
    .cf-card { border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-2); }
    .cf-card h3 { margin: 0; font-size: var(--t-m); font-weight: 500; color: var(--ink); }
    .cf-foot { margin-top: auto; }
    .cf-line { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; }
    .cf-input { flex: 1 1 220px; min-width: 0; font-family: var(--mono); }
    @container canvas (max-width: 720px) { .cf-wrap { padding: var(--sp-3); } }
    @container canvas (max-width: 700px) { .cf-wrap .tbl { table-layout: auto; } .cf-wrap .tbl th, .cf-wrap .tbl td { white-space: nowrap; } }
  `;
  document.head.append(style);
}

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** La commande équivalente, à copier d'un clic (jamais lancée d'ici). */
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

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function table(headings, widths) {
  const wrap = el('div', 'tbl-wrap');
  const tbl = el('table', 'tbl');
  const head = el('tr');
  headings.forEach((label, index) => {
    const th = el('th', null, label, { scope: 'col' });
    if (widths[index]) th.style.width = widths[index];
    head.append(th);
  });
  tbl.append(el('thead', null, null), el('tbody'));
  tbl.firstChild.append(head);
  wrap.append(tbl);
  return { wrap, body: tbl.tBodies[0] };
}

function row(body, cells) {
  const tr = el('tr');
  for (const cell of cells) {
    const td = el('td');
    if (cell instanceof Node) td.append(cell);
    else td.textContent = cell;
    tr.append(td);
  }
  body.append(tr);
}

function failure(ctx, root, what, error) {
  root.replaceChildren(ctx.empty(
    `${what} injoignables`,
    `L'API locale n'a pas répondu : ${error.message}`,
    'grimoire doctor',
  ));
}

// ── Fournisseurs : le panneau de détail et la rubrique ──────────────────────

function providerSub(provider) {
  const parts = [];
  if (provider.currency) parts.push(provider.currency);
  const models = provider.models.map((m) => m.id);
  if (models.length) parts.push(models.join(', '));
  if (!provider.probed_at && !models.length) parts.push('non sondé');
  return parts.join(' · ') || 'non sondé';
}

function providersSummary(data) {
  const cooling = data.providers.filter((p) => p.cooling_down || p.suspended).length;
  const parts = [cooling ? plural(cooling, 'refroidi', 'refroidis') : 'Aucun refroidi'];
  for (const [tier, id] of Object.entries(data.next_choice || {})) {
    if (id) parts.push(`prochain ${tier} : ${id}`);
  }
  return parts.join(' · ');
}

// P75 : options du projet — le taux USD→CAD que la colonne Fait applique au
// coût replié. Lu et écrit par /api/workspace/options (project-context.yaml,
// section cockpit) ; le défaut du kit est dit comme défaut, jamais comme un choix.
function optionsCard(ctx) {
  const card = el('section', 'cf-card');
  card.dataset.role = 'project-options';
  card.setAttribute('aria-label', 'Options du projet');
  card.append(el('h3', null, 'Options du projet'));
  const note = el('p', 'cf-note', 'Taux de conversion du coût affiché par la colonne Fait (en CAD pour 1 USD).');
  const source = el('p', 'cf-note', 'lecture du taux…');
  source.dataset.role = 'options-source';
  const line = el('div');
  line.style.display = 'flex';
  line.style.gap = 'var(--sp-2)';
  line.style.alignItems = 'center';
  const input = el('input', null, null, {
    type: 'number', step: '0.01', min: '0.01', max: '999.99',
    'aria-label': 'CAD pour 1 USD', 'data-role': 'usd-cad-input',
  });
  input.style.width = '110px';
  const save = el('button', 'btn', 'Enregistrer', { type: 'button', 'data-role': 'usd-cad-save' });
  const status = el('span', 'cf-note', '');
  status.dataset.role = 'usd-cad-status';
  line.append(el('span', null, '1 USD ='), input, el('span', null, '$ CAD'), save, status);
  const describe = (data) => {
    source.textContent = data.usd_cad_source === 'project'
      ? 'Taux déclaré par le projet (project-context.yaml, cockpit.usd_cad).'
      : `Défaut du kit (1 USD = ${String(data.usd_cad_default).replace('.', ',')} $ CAD) tant que le projet ne déclare rien.`;
  };
  ctx.api.options()
    .then((data) => { if (ctx.signal.aborted) return; input.value = String(data.usd_cad); describe(data); })
    .catch((error) => { source.textContent = `Options injoignables : ${error.message}`; });
  save.addEventListener('click', async () => {
    const rate = Number.parseFloat(input.value);
    if (!Number.isFinite(rate) || rate <= 0) {
      status.textContent = 'taux invalide : un nombre positif est attendu';
      return;
    }
    try {
      const data = await ctx.api.optionsWrite(rate);
      describe(data);
      status.textContent = 'taux enregistré — la colonne Fait l\'applique au prochain rafraîchissement';
    } catch (error) {
      status.textContent = `écriture refusée : ${error.message}`;
    }
  });
  card.append(note, line, source);
  return card;
}

// I1 : la commande qui prévient hors du navigateur quand une décision attend (options.notify.command
// du profil du standard, lue par `grimoire tick`). « Tester » la lance avec une décision d'essai.
function notifyCard(ctx) {
  const card = el('section', 'cf-card');
  card.dataset.role = 'notify-options';
  card.setAttribute('aria-label', 'Commande de notification');
  card.append(el('h3', null, 'Commande de notification'));
  const note = el('p', 'cf-note', 'Lancée une fois par décision attendue, sans shell. {title} et {message} y sont remplacés ; les variables GRIMOIRE_NOTIFY_* sont aussi posées.');
  const source = el('p', 'cf-note', 'lecture de la commande…');
  source.dataset.role = 'notify-source';
  const line = el('div', 'cf-line');
  const input = el('input', 'cf-input', null, {
    type: 'text', 'aria-label': 'Commande de notification', 'data-role': 'notify-input',
    placeholder: 'notify-send {title} {message}', autocomplete: 'off', spellcheck: 'false',
  });
  const save = el('button', 'btn', 'Enregistrer', { type: 'button', 'data-role': 'notify-save' });
  const test = el('button', 'btn', 'Tester', { type: 'button', 'data-role': 'notify-test' });
  const status = el('p', 'cf-note', '', { role: 'status', 'aria-live': 'polite', 'data-role': 'notify-status' });
  line.append(input, save, test);
  const describe = (data) => {
    if (data.source === 'env') source.textContent = 'Fixée par la variable GRIMOIRE_NOTIFY_COMMAND : elle prime sur le profil.';
    else if (data.source === 'profile') source.textContent = 'Déclarée par le profil du standard (options.notify.command).';
    else source.textContent = 'Aucune commande : seuls le titre de l\'onglet et les notifications du navigateur préviennent.';
    if (data.problem) status.textContent = `Commande illisible : ${data.problem}`;
  };
  ctx.api.notify()
    .then((data) => { if (ctx.signal.aborted) return; input.value = data.command || ''; describe(data); })
    .catch((error) => { source.textContent = `Commande injoignable : ${error.message}`; });
  save.addEventListener('click', async () => {
    try {
      const data = await ctx.api.notifyWrite(input.value);
      describe(data);
      status.textContent = data.command ? 'Commande enregistrée.' : 'Commande retirée.';
    } catch (error) {
      status.textContent = `Écriture refusée : ${error.message}`;
    }
  });
  test.addEventListener('click', async () => {
    test.disabled = true;
    status.textContent = 'Essai en cours…';
    try {
      // Ce qui est dans le champ s'essaie tel quel, sans l'enregistrer.
      const result = await ctx.api.notifyTest(input.value.trim() || undefined);
      status.textContent = result.ok ? 'Essai réussi : la commande est sortie avec le code 0.' : `Essai échoué (code ${result.code}) : ${result.error}`;
    } catch (error) {
      status.textContent = `Essai impossible : ${error.message}`;
    } finally {
      test.disabled = false;
    }
  });
  card.append(note, line, source, status);
  return card;
}

function budgetsCard() {
  const card = el('section', 'cf-card');
  card.setAttribute('aria-label', 'Budgets de session');
  card.append(
    el('h3', null, 'Budgets de session'),
    el('p', 'cf-note', 'Les règles de budget et ce que la session a consommé se lisent avec la commande ci-dessous.'),
    formula('grimoire policies status'),
  );
  return card;
}

function renderProvidersPanel(target, data) {
  const panel = el('div', 'cf-panel');
  panel.dataset.panel = 'providers';
  const head = el('div');
  head.style.display = 'flex';
  head.style.flexDirection = 'column';
  head.style.gap = '4px';
  head.append(el('span', 'cf-panel-title', 'Fournisseurs de modèles'));
  if (data.error) {
    head.append(el('span', 'cf-panel-sub', data.error));
  } else if (!data.providers.length) {
    head.append(el('span', 'cf-panel-sub', 'Aucun registre de fournisseurs (grimoire standard init).'));
  } else {
    head.append(el('span', 'cf-panel-sub', providersSummary(data)));
  }
  const list = el('div');
  for (const provider of data.providers) {
    const left = el('span');
    left.style.display = 'flex';
    left.style.flexDirection = 'column';
    left.style.minWidth = '0';
    left.append(el('span', 'cf-prov-name', provider.id), el('span', 'cf-prov-sub', providerSub(provider)));
    const item = el('div', 'cf-prov');
    item.append(left, badge(provider.status, provider.tone));
    list.append(item);
  }
  const foot = formula('grimoire providers status');
  foot.classList.add('cf-foot');
  panel.append(head, list, budgetsCard(), foot);
  target.replaceChildren(panel);
}

async function mountProviders(root, ctx) {
  let data;
  try {
    data = await ctx.api.providers();
  } catch (error) {
    failure(ctx, root, 'Fournisseurs', error);
    return;
  }
  const wrap = el('div', 'cf-wrap');
  const sub = data.error || (data.providers.length
    ? providersSummary(data)
    : 'Aucun registre de fournisseurs : `grimoire standard init` le crée.');
  wrap.append(spaceHead('Fournisseurs et budgets', sub, formula('grimoire providers status')));
  if (data.providers.length) {
    const { wrap: box, body } = table(['Fournisseur', 'Monnaie', 'Paliers', 'Modèles', 'État'], ['22%', '12%', '20%', '', '130px']);
    for (const p of data.providers) {
      row(body, [
        el('span', 'cf-id', p.id),
        p.currency || '—',
        p.tiers.join(', ') || '—',
        p.models.map((m) => m.id).join(', ') || (p.probed_at ? '—' : 'non sondé'),
        badge(p.status, p.tone),
      ]);
    }
    wrap.append(box);
  } else if (!data.error) {
    wrap.append(ctx.empty('Aucun fournisseur déclaré', 'Le registre des fournisseurs est absent ou vide.', 'grimoire standard init'));
  }
  wrap.append(budgetsCard(), optionsCard(ctx), notifyCard(ctx));
  root.replaceChildren(wrap);
}

// ── Hôtes et hooks ──────────────────────────────────────────────────────────

function hooksSub(data) {
  const parts = [];
  for (const [label, count] of Object.entries(data.counts)) {
    const prefix = label === 'appliqué' ? '' : 'en ';
    const word = label === 'appliqué' ? (count > 1 ? 'appliqués' : 'appliqué') : (label === 'essai' ? 'essai' : label);
    parts.push(`${count} ${prefix}${word}`);
  }
  return parts.join(' · ');
}

async function mountHooks(root, ctx) {
  let data;
  try {
    data = await ctx.api.hooks();
  } catch (error) {
    failure(ctx, root, 'Hooks', error);
    return;
  }
  // Le panneau de détail à droite : les fournisseurs (lecture), même écran.
  ctx.api.providers()
    .then((providers) => { if (!ctx.signal.aborted) renderProvidersPanel(ctx.inspector, providers); })
    .catch((error) => {
      if (!ctx.signal.aborted) ctx.inspector.replaceChildren(el('p', 'cf-note', `Fournisseurs injoignables : ${error.message}`));
    });

  const wrap = el('div', 'cf-wrap');
  const sub = data.error || (data.registry ? hooksSub(data) : "Aucun registre de sûreté des hooks dans ce projet.");
  wrap.append(spaceHead('Hôtes et hooks', sub, formula('grimoire hooks status')));
  if (!data.registry || data.error || !data.hooks.length) {
    if (data.registry && !data.error) {
      wrap.append(ctx.empty('Registre vide', 'Le registre de sûreté ne déclare aucun hook.', 'grimoire hooks status'));
    }
    root.replaceChildren(wrap);
    return;
  }

  const { wrap: box, body } = table(['Hook', 'Événement', 'Mode'], ['', '24%', '150px']);
  const draw = (host) => {
    body.replaceChildren();
    for (const hook of data.hooks) {
      if (host !== 'all' && !hook.hosts.includes(host)) continue;
      row(body, [el('span', 'cf-id', hook.id), el('span', 'cf-muted', hook.event || '—'), badge(hook.label, hook.tone)]);
    }
  };
  if (data.hosts.length) {
    const seg = el('div', 'seg', null, { role: 'group', 'aria-label': 'Hôte' });
    seg.style.alignSelf = 'flex-start';
    const choices = [{ id: 'all', label: 'Tous' }, ...data.hosts];
    for (const choice of choices) {
      const button = el('button', null, choice.label, { type: 'button', 'aria-pressed': String(choice.id === 'all') });
      button.addEventListener('click', () => {
        for (const other of seg.children) other.setAttribute('aria-pressed', String(other === button));
        draw(choice.id);
      });
      seg.append(button);
    }
    wrap.append(seg);
  }
  draw('all');
  wrap.append(box);
  wrap.append(el('p', 'cf-note', 'Le mode se change hors du cockpit : celui-ci lit le registre de sûreté, il ne l\'écrit pas.'));
  root.replaceChildren(wrap);
}

// ── Agents ──────────────────────────────────────────────────────────────────

async function mountAgents(root, ctx) {
  let data;
  try {
    data = await ctx.api.agents();
  } catch (error) {
    failure(ctx, root, 'Agents', error);
    return;
  }
  const agents = data.agents || [];
  const custom = agents.filter((a) => a.layer === 'overrides').length;
  const wrap = el('div', 'cf-wrap');
  const sub = agents.length
    ? `${plural(agents.length, 'agent', 'agents')} · ${plural(custom, 'personnalisé', 'personnalisés')} par le projet`
    : 'Aucun agent dans ce projet.';
  wrap.append(spaceHead('Agents', sub, formula('grimoire host status')));
  if (agents.length) {
    const { wrap: box, body } = table(['Agent', 'Couche', 'Outils', 'Skills', 'Choisi'], ['', '110px', '22%', '90px', '90px']);
    for (const a of agents) {
      const name = el('span');
      name.append(el('span', 'cf-id', a.name));
      if (a.entry_point) name.append(' ', badge("point d'entrée", 'acc'));
      row(body, [
        name,
        badge(a.layer === 'overrides' ? 'projet' : 'kit', a.layer === 'overrides' ? 'acc' : ''),
        el('span', 'cf-muted', (a.tools || []).join(', ') || '—'),
        String((a.skills || []).length),
        String((a.usage && a.usage.choices) || 0),
      ]);
    }
    wrap.append(box);
  } else {
    wrap.append(ctx.empty('Aucun agent', "Ce projet n'a pas encore d'agent.", 'grimoire host status'));
  }
  root.replaceChildren(wrap);
}

export async function mount(root, ctx) {
  injectStyles();
  if (ctx.section === 'agents') return mountAgents(root, ctx);
  if (ctx.section === 'fournisseurs') return mountProviders(root, ctx);
  return mountHooks(root, ctx);
}
