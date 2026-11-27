// À traiter › Démarrer — le premier lancement, en lecture seule (P36).
//
// Le cockpit ouvert sur un projet non configuré n'affiche pas un accueil
// vide : À traiter lui cède la place (`a-traiter.js`), et le guide reste
// joignable à tout moment par `#a-traiter?view=demarrer` et la palette.
//
// Ce que la page lit, et seulement ça :
//   /api/status, /api/health, /api/setup/run, /api/memory/status (sans sonde)
//     → l'état S0 à S3, classé par `setup-state.js` et toujours cité avec sa
//       source (« lu dans /api/health · kit.scaffolded = false ») ;
//   /api/workspace/setup/guide
//     → la machine : détection, profils mémoire possibles ou non et pourquoi,
//       plan, commandes.
//
// Refus tenus ici :
// - AUCUNE écriture à l'ouverture : ni POST, ni PUT. Les commandes restent à
//   copier. Seul « Lancer » (P45) écrit, sur un clic, et uniquement par les
//   actions typées de P40 (`POST setup/launch` : plan puis apply, avec journal),
//   jamais une commande libre ;
// - une lecture ratée se dit « inconnu », avec sa raison, jamais un faux S0 ;
// - un profil impossible reste visible, grisé, avec sa raison.

import { actionStatus, badge, failureText, icon, setActionStatus, spaceHead, stateIcon } from '../shell.js';
import { classify, NEXT_COMMAND, ROUTES } from '../setup-state.js';




function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** Ton d'une ligne de détection : le mot est toujours écrit dans le badge. */
const CHECK_TONE = { ok: 'ok', warn: 'warn', missing: 'bad', unknown: '' };
const CHECK_ICON = {
  python: 'code', installer: 'download', docker: 'container', embedding: 'vector', egress: 'network',
  services: 'server', hosts: 'i-host-ide', projects: 'fleet',
};
const PROFILE_ICON = { lexical: 'i-store-lexical', standard: 'i-store-vector', graphe: 'i-store-graph', complet: 'i-store-hot' };

/** Une lecture d'`api.js` sous la forme que `classify` attend. */
const settle = (promise) => promise.then(
  (data) => ({ ok: true, data }),
  (error) => ({ ok: false, error: error && error.message ? error.message : String(error) }),
);

function stepHead(num, title, hint) {
  const head = el('div', 'dm-step-head');
  head.append(el('span', 'dm-num', String(num), { 'aria-hidden': 'true' }), el('h3', null, title));
  if (hint) head.append(el('span', 'dm-hint', hint));
  return head;
}

/** Une commande à copier : « Copier » confirme sur place, rien ne s'exécute. */
function commandRow(index, command, why) {
  const row = el('div', 'dm-cmd');
  row.dataset.command = command;
  const code = el('code');
  code.append(el('b', null, '›'));
  // Coupure aux espaces seulement : « --memory-profile » ne se casse jamais en deux.
  command.split(' ').forEach((word, i) => code.append(i ? ' ' : '', el('span', 'dm-word', word)));
  const copy = el('button', 'btn', null, { type: 'button', 'data-role': 'copy', 'aria-label': `Copier : ${command}` });
  const label = el('span', null, 'Copier');
  copy.append(icon('copy'), label);
  const status = actionStatus('');
  copy.addEventListener('click', () => {
    const done = () => {
      copy.dataset.copied = 'ok';
      label.textContent = 'Copié';
      setActionStatus(status, 'Copiée : lance-la toi-même dans un terminal. Le cockpit n’exécute rien.', 'ok');
    };
    const refused = () => {
      copy.dataset.copied = 'refused';
      setActionStatus(status, 'Copie refusée par le navigateur : sélectionne la commande à la main.', 'bad');
    };
    try {
      navigator.clipboard.writeText(command).then(done, refused);
    } catch {
      refused();
    }
  });
  row.append(el('span', 'dm-num', String(index), { 'aria-hidden': 'true' }), code, el('p', 'dm-cmd-why', why || ''), copy, status);
  return row;
}

function readBadges(reads) {
  const box = el('div', 'dm-reads', null, { 'aria-label': 'Lectures' });
  for (const [key, route] of Object.entries(ROUTES)) {
    const read = reads[key];
    const ok = read && read.ok;
    const b = badge(`${route} ${ok ? 'lu' : 'illisible'}`, ok ? '' : 'bad', ok ? `${route} lu` : `${route} : ${read && read.error}`);
    b.dataset.read = key;
    box.append(b);
  }
  return box;
}

function stateCard(state, reads) {
  const card = el('section', 'dm-card dm-state', null, { 'aria-labelledby': 'dm-state-title', 'data-role': 'setup-state' });
  card.dataset.state = state.id;
  card.dataset.tone = state.tone;
  const head = el('div', 'dm-state-head');
  const key = state.id === 'inconnu' ? 'S?' : state.id;
  head.append(
    stateIcon({ warn: 'warn', bad: 'bad', ok: 'ok', acc: 'acc' }[state.tone] || ''),
    el('span', 'dm-key', `État ${key}`),
    badge(state.label, state.tone),
  );
  card.append(head, el('h2', 'dm-state-title', state.title, { id: 'dm-state-title' }));
  if (state.sentence) card.append(el('p', 'dm-sentence', state.sentence));
  if (state.failedSteps && state.failedSteps.length) {
    const list = el('ul', 'dm-steps-failed');
    for (const step of state.failedSteps) list.append(el('li', null, `${step.step} : ${step.detail || 'échec'}`));
    card.append(list);
  }
  const source = el('p', 'dm-source', null, { 'data-role': 'setup-source' });
  source.append('lu dans ', el('code', null, state.source));
  card.append(source, readBadges(reads));
  return card;
}

function checksStep(guide) {
  const step = el('section', 'dm-card dm-step', null, { 'aria-label': 'Détection', 'data-role': 'setup-detection' });
  step.append(stepHead(1, 'Détection', 'lue sur ce poste, rien n’est modifié'));
  const list = el('div', 'dm-checks');
  for (const check of guide.checks || []) {
    const row = el('div', 'dm-check');
    row.dataset.check = check.id;
    row.dataset.state = check.state;
    const glyph = el('span', 'glyph-ico');
    glyph.append(icon(CHECK_ICON[check.id] || 'info'));
    row.append(glyph, el('span', 'dm-check-label', check.label), badge(check.word, CHECK_TONE[check.state] ?? '', check.detail));
    // Ce qui va bien tient sur une ligne (le détail au survol) ; le reste dit
    // ce qui manque et son remède.
    row.title = check.detail;
    const detail = check.state === 'ok' ? '' : [check.detail, check.remedy].filter(Boolean).join(' ');
    if (detail) row.append(el('p', 'dm-check-detail', detail));
    list.append(row);
  }
  step.append(list);
  return step;
}

function profileCard(profile, selected, onSelect) {
  const impossible = profile.possible === false;
  const card = el('button', 'dm-profile', null, {
    type: 'button', role: 'radio', 'aria-checked': String(selected), 'data-profile': profile.id,
  });
  if (impossible) {
    card.setAttribute('aria-disabled', 'true');
    card.dataset.possible = 'false';
  } else {
    card.dataset.possible = profile.possible === null ? 'unknown' : 'true';
  }
  const head = el('span', 'dm-profile-head');
  head.append(icon(PROFILE_ICON[profile.id] || 'database'), el('span', 'dm-profile-name', profile.label));
  card.append(head);
  if (impossible) card.append(badge('impossible ici', 'bad'));
  else if (profile.possible === null) card.append(badge('inconnu', ''));
  else if (profile.recommended) card.append(badge('recommandé', 'acc'));
  else card.append(badge('possible', 'ok'));
  card.append(el('span', 'dm-profile-summary', profile.summary));
  for (const reason of profile.reasons || []) {
    const why = el('span', 'dm-profile-why', reason);
    why.dataset.role = 'reason';
    card.append(why);
  }
  for (const caveat of profile.caveats || []) card.append(el('span', 'dm-profile-summary', caveat));
  card.addEventListener('click', () => { if (!impossible) onSelect(profile.id); });
  return card;
}

function planStep(profile, guide, configured) {
  const step = el('section', 'dm-card dm-step', null, { 'aria-label': 'Plan', 'data-role': 'setup-plan' });
  step.append(stepHead(3, 'Plan', `profil ${profile.label} · ce que les commandes feraient`));
  const grid = el('div', 'dm-plan');
  const left = el('div', 'dm-plan-col');
  if (configured) {
    left.append(el('span', 'dm-label', 'Écrit dans ce projet'));
    const tree = el('ul', 'dm-tree');
    const li = el('li');
    li.append(el('span', 'dm-mono', 'project-context.yaml'), el('span', null, 'le bloc memory:, aligné sur ce que la machine sert'));
    tree.append(li);
    left.append(tree);
  } else {
    left.append(el('span', 'dm-label', 'Écrit dans ce dossier'));
    const tree = el('ul', 'dm-tree');
    for (const entry of guide.writes || []) {
      const li = el('li');
      li.append(el('span', 'dm-mono', entry.path), el('span', null, entry.what));
      tree.append(li);
    }
    left.append(tree);
  }
  const right = el('div', 'dm-plan-col');
  right.append(el('span', 'dm-label', 'Services à démarrer'));
  right.append(el('p', 'dm-sentence', profile.start.length
    ? `${profile.start.join(', ')} : démarrés par la commande, avec les gabarits Docker Compose du kit.`
    : 'Aucun : ce profil ne demande aucun service.'));
  if (profile.shared.length) {
    const shared = el('p', 'dm-shared', `Partagés avec d’autres projets : ${profile.shared.join(', ')} répondent déjà sur ce poste et seront réutilisés.`);
    shared.dataset.role = 'shared';
    right.append(shared);
  }
  right.append(el('span', 'dm-label', 'Extras Python, à installer toi-même'));
  if (profile.extras.length) {
    right.append(el('code', 'dm-mono', profile.extrasHint || profile.extras.join(', ')));
  } else {
    right.append(el('p', 'dm-sentence', 'Aucun : tout ce que ce profil importe est déjà là.'));
  }
  grid.append(left, right);
  step.append(grid);
  return step;
}

function commandsFor(state, profile) {
  if (state.id === 'S3') return [];
  const memory = profile ? profile.commands.memory : [{ command: NEXT_COMMAND.S2, why: 'Montre le plan mémoire, n’écrit rien.' }];
  if (state.id === 'S0') {
    return profile ? profile.commands.init : [{ command: NEXT_COMMAND.S0, why: 'Équipe ce dossier.' }];
  }
  if (state.id === 'S1') {
    return [
      { command: NEXT_COMMAND.S1, why: 'Répare ce que doctor sait réparer : dossiers, wrappers, .mcp.json.' },
      { command: 'grimoire up .', why: 'Rejoue la mise en place ; chaque étape déjà faite est sautée.' },
    ];
  }
  if (state.id === 'S2') return memory;
  return [{ command: NEXT_COMMAND.inconnu, why: 'Relit l’installation, sans rien modifier.' }, ...memory];
}

function commandsStep(state, profile, root, ctx) {
  const step = el('section', 'dm-card dm-step', null, { 'aria-label': 'Commandes à copier', 'data-role': 'setup-commands' });
  step.append(stepHead(4, 'Commandes à copier', 'tu les lances, le guide relit'));
  const cmds = commandsFor(state, profile);
  if (!cmds.length) {
    step.append(el('p', 'dm-sentence', 'Rien à lancer : le projet est prêt.'));
    const open = el('button', 'btn pri', 'Ouvrir À traiter', { type: 'button' });
    open.addEventListener('click', () => ctx.goto('a-traiter'));
    step.append(el('div', 'dm-actions'));
    step.lastChild.append(open);
    return step;
  }
  if (root) {
    const where = el('p', 'dm-where');
    where.append('Dans ', el('code', 'dm-mono', root), ' :');
    step.append(where);
  }
  const list = el('div', 'dm-cmds');
  cmds.forEach((c, i) => list.append(commandRow(i + 1, c.command, c.why)));
  step.append(list);
  return step;
}

const RUN_STATUS = {
  pending: ['à venir', ''], running: ['en cours', 'acc'], done: ['fait', 'ok'], failed: ['échec', 'bad'],
  skipped: ['sautée', ''], rolled_back: ['annulée', ''],
};
const SETTLED = ['done', 'skipped', 'rolled_back'];

/**
 * « Lancer » (P45) : le plan exécuté étape par étape par les actions typées de
 * P40. `launch` est l'avancement enregistré côté serveur ; chaque bouton est un
 * verbe de `POST setup/launch`, rien d'autre.
 */
function launchPanel(profile, launch, ctl) {
  const card = el('section', 'dm-card dm-step', null, { 'aria-label': 'Lancer', 'data-role': 'setup-launch' });
  card.append(stepHead(5, 'Lancer', 'le guide applique le plan, puis vérifie'));
  const exists = launch && launch.exists;
  if (exists) card.dataset.finished = String(Boolean(launch.finished));
  const buttons = el('div', 'dm-actions');
  if (!exists) {
    card.append(el('p', 'dm-sentence', profile
      ? `Profil ${profile.label} : la mémoire est composée par les actions typées du kit (plan, application, journal), puis vérifiée par un aller-retour réel. Chaque étape se rejoue, se saute si elle n’est pas bloquante, ou se défait.`
      : 'Choisis d’abord un profil mémoire possible sur cette machine.'));
    const go = el('button', 'btn pri', 'Lancer', { type: 'button', 'data-role': 'launch-start' });
    go.disabled = ctl.busy || !profile;
    go.addEventListener('click', () => ctl.start(profile.id));
    buttons.append(go);
  } else {
    const list = el('ol', 'dm-run', null, { 'aria-label': 'Étapes du lancement' });
    launch.steps.forEach((step, i) => {
      const interrupted = launch.interrupted === step.id && !ctl.busy;
      const [word, tone] = interrupted ? ['interrompue', 'warn'] : (RUN_STATUS[step.status] || [step.status, '']);
      const row = el('li', 'dm-run-step');
      row.dataset.step = step.id;
      row.dataset.status = interrupted ? 'interrupted' : step.status;
      row.append(el('span', 'dm-num', String(i + 1), { 'aria-hidden': 'true' }), el('span', 'dm-run-label', step.label), badge(word, tone));
      if (step.detail && step.status !== 'failed') row.append(el('p', 'dm-run-detail', step.detail));
      for (const f of step.findings || []) {
        const line = el('p', 'dm-run-find', `${f.label} : ${f.detail}${f.remedy && f.state !== 'ok' ? ` — ${f.remedy}` : ''}`);
        line.dataset.state = f.state;
        row.append(line);
      }
      if (step.status === 'failed' && step.error) {
        const err = el('p', 'dm-run-error', null, { role: 'alert', 'data-role': 'step-error' });
        err.append(el('b', null, step.error.message), ' ', step.error.remedy || '');
        row.append(err);
        if (step.error.raw) row.append(el('p', 'dm-run-detail', `Motif : ${step.error.raw}`));
        const own = el('div', 'dm-run-buttons');
        const retry = el('button', 'btn', 'Réessayer', { type: 'button', 'data-role': 'step-retry' });
        retry.disabled = ctl.busy;
        retry.addEventListener('click', () => ctl.verb('retry', { step: step.id }));
        own.append(retry);
        if (step.skippable) {
          const skip = el('button', 'btn', 'Sauter', { type: 'button', 'data-role': 'step-skip' });
          skip.disabled = ctl.busy;
          skip.addEventListener('click', () => ctl.verb('skip', { step: step.id }));
          own.append(skip);
        }
        row.append(own);
      }
      list.append(row);
    });
    card.append(list);
    if (launch.interrupted && !ctl.busy) {
      card.append(el('p', 'dm-sentence', `Le lancement s’est arrêté à l’étape « ${(launch.steps.find((s) => s.id === launch.interrupted) || {}).label} » : il reprend là.`));
    }
    const blocked = launch.steps.some((s) => s.status === 'failed');
    if (!launch.finished && !blocked) {
      const resume = el('button', 'btn pri', launch.interrupted ? 'Reprendre' : 'Continuer', { type: 'button', 'data-role': 'launch-resume' });
      resume.disabled = ctl.busy;
      resume.addEventListener('click', () => ctl.runAll());
      buttons.append(resume);
    }
    if (launch.canRollback) {
      const back = el('button', 'btn', 'Revenir en arrière', { type: 'button', 'data-role': 'launch-rollback' });
      back.disabled = ctl.busy;
      back.addEventListener('click', () => ctl.verb('rollback'));
      buttons.append(back);
    }
    const reset = el('button', 'btn', launch.finished ? 'Recommencer' : 'Abandonner', { type: 'button', 'data-role': 'launch-reset' });
    reset.disabled = ctl.busy;
    reset.addEventListener('click', () => ctl.verb('reset'));
    buttons.append(reset);
    if (launch.finished) card.append(el('p', 'dm-sentence', 'Lancement terminé : l’état du projet est relu ci-dessus.', { 'data-role': 'launch-done' }));
  }
  card.append(buttons);
  if (ctl.message) card.append(actionStatus(ctl.message, 'bad'));
  return card;
}

/**
 * Monte le guide. `prefetched.health` : la réponse de `/api/health` qu'À
 * traiter vient de lire pour décider de céder la place — pas relue deux fois.
 */
export async function mount(root, ctx, prefetched = {}) {
  const wrap = el('div', 'dm-wrap');
  wrap.dataset.role = 'setup-guide';
  const reread = el('button', 'btn', null, { type: 'button', 'data-role': 'setup-reread' });
  reread.append(icon('refresh'), 'Relire');
  const head = spaceHead('Démarrer', 'Lecture de l’état du projet…', reread);
  wrap.append(head, ...ctx.loading('Lecture de l’état du projet et de la machine…', 3, 'lines'));
  root.replaceChildren(wrap);
  ctx.inspector.replaceChildren();
  ctx.dock.echo('grimoire doctor .');

  let guidePromise = settle(ctx.api.setupGuide());
  let selected = null;
  let health = prefetched.health ? Promise.resolve({ ok: true, data: prefetched.health }) : null;
  // P45 : l'avancement du « Lancer » vit côté serveur ; la page le relit à chaque ouverture.
  let launch = null;
  let launchBusy = false;
  let launchMessage = '';

  const paint = async () => {
    reread.disabled = true;
    const [status, healthRead, setupRun, memory, guideRead, launchRead] = await Promise.all([
      settle(ctx.api.status()),
      health || settle(ctx.api.health()),
      settle(ctx.api.setupRun()),
      settle(ctx.api.memoryStatus()),
      guidePromise,
      launchBusy ? Promise.resolve({ ok: true, data: launch }) : settle(ctx.api.setupLaunchState()),
    ]);
    health = null;
    if (launchRead.ok) launch = launchRead.data;
    if (ctx.signal.aborted) return;
    const reads = { status, health: healthRead, setupRun, memory };
    const state = classify(reads);
    const projectRoot = (status.ok && status.data.projectRoot) || (healthRead.ok && healthRead.data.projectRoot) || '';
    head.querySelector('.space-sub').textContent = projectRoot
      ? `Premier lancement · ${projectRoot}`
      : 'Premier lancement';

    const note = el('p', 'dm-note', null, { role: 'note', 'data-role': 'setup-readonly' });
    note.append(icon('lock'), 'Ce guide n’écrit rien tant que tu ne cliques pas sur Lancer : il lit l’état du projet et de la machine, te donne les commandes à copier, et « Lancer » n’applique que les actions typées du kit, journalisées et réversibles.');
    const main = el('div', 'dm-main');
    const aside = el('aside', 'dm-aside', null, { 'aria-label': 'À lancer' });
    const layout = el('div', 'dm-layout');
    layout.append(main, aside);
    const nodes = [head, stateCard(state, reads), layout];
    aside.append(note);

    if (!guideRead.ok) {
      main.append(actionStatus(`Lecture de la machine impossible : ${failureText({ message: guideRead.error, code: 0 })}`, 'bad'));
      aside.append(commandsStep(state, null, projectRoot, ctx));
      wrap.replaceChildren(...nodes);
      reread.disabled = false;
      return;
    }
    const guide = guideRead.data;
    const profiles = guide.profiles || [];
    if (!selected || !profiles.some((p) => p.id === selected && p.possible !== false)) {
      const reco = profiles.find((p) => p.recommended && p.possible !== false);
      selected = (reco || profiles.find((p) => p.possible !== false) || {}).id || null;
    }
    main.append(checksStep(guide));

    const profileStep = el('section', 'dm-card dm-step', null, { 'aria-label': 'Profil mémoire', 'data-role': 'setup-profiles' });
    profileStep.append(stepHead(2, 'Profil mémoire', 'une composition, pas une base'));
    if (guide.recommended) {
      profileStep.append(el('p', 'dm-reco', `Recommandé ici : ${guide.recommended.id}, ${guide.recommended.reason}.`));
    }
    const grid = el('div', 'dm-profiles', null, { role: 'radiogroup', 'aria-label': 'Profil mémoire' });
    profileStep.append(grid);
    main.append(profileStep);
    const planHost = el('div', 'dm-step');
    const launchHost = el('div', 'dm-step');
    const cmdHost = el('div', 'dm-step');
    main.append(planHost);
    aside.append(launchHost, cmdHost);

    const renderLaunch = () => {
      const profile = profiles.find((p) => p.id === (launch && launch.exists ? launch.profile.id : selected)) || null;
      if (state.id === 'S3' && !(launch && launch.exists)) {
        launchHost.replaceChildren();
        return;
      }
      launchHost.replaceChildren(launchPanel(profile, launch, {
        busy: launchBusy, message: launchMessage, start, verb, runAll,
      }));
    };
    const settleLaunch = async (work) => {
      launchBusy = true;
      launchMessage = '';
      renderLaunch();
      try {
        await work();
      } catch (error) {
        launchMessage = failureText(error);
        const fresh = await settle(ctx.api.setupLaunchState());
        if (fresh.ok) launch = fresh.data;
      }
      launchBusy = false;
      if (!ctx.signal.aborted) renderLaunch();
    };
    // Une étape à la fois : la progression se voit, et un échec arrête la suite.
    const runAll = () => settleLaunch(async () => {
      for (let guard = 0; guard < 12; guard += 1) {
        const current = launch.steps.find((s) => !SETTLED.includes(s.status));
        if (!current || current.status === 'failed') break;
        current.status = 'running';
        launch.interrupted = null;
        renderLaunch();
        launch = await ctx.api.setupLaunch('next');
        renderLaunch();
      }
    }).then(() => { if (!ctx.signal.aborted && launch && launch.finished) paint(); });
    const start = (profileId) => settleLaunch(async () => {
      launch = await ctx.api.setupLaunch('start', { profile: profileId });
    }).then(() => (launch && launch.exists ? runAll() : null));
    const verb = (name, body) => settleLaunch(async () => {
      launch = await ctx.api.setupLaunch(name, body);
    }).then(() => ((name === 'retry' || name === 'skip') && launch && !launch.finished && !launch.steps.some((s) => s.status === 'failed') ? runAll() : null));

    const renderChoice = () => {
      grid.replaceChildren(...profiles.map((p) => profileCard(p, p.id === selected, (id) => { selected = id; renderChoice(); })));
      const profile = profiles.find((p) => p.id === selected) || null;
      planHost.replaceChildren(...(profile && state.id !== 'S3' ? [planStep(profile, guide, guide.configured)] : []));
      cmdHost.replaceChildren(commandsStep(state, profile, projectRoot, ctx));
      renderLaunch();
    };
    renderChoice();
    wrap.replaceChildren(...nodes);
    reread.disabled = false;
  };

  reread.addEventListener('click', () => {
    guidePromise = settle(ctx.api.setupGuide());
    paint();
  });
  // Une commande lancée dans un terminal change le ledger : le pouls le dit,
  // le guide relit l'état (pas la machine, plus lente à sonder).
  ctx.live.on(() => { if (!ctx.signal.aborted) paint(); }, { scope: () => (ctx.scope.all ? 'portfolio' : 'project') });
  await paint();
}
