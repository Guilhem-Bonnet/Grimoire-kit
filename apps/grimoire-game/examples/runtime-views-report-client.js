const payloadNode = document.getElementById('report-data');
if (!(payloadNode instanceof HTMLScriptElement)) {
  throw new Error('Missing report payload.');
}

const payload = JSON.parse(payloadNode.textContent ?? '{}');
const initialUrl = new URL(window.location.href);
const vscodeBridge = createVsCodeBridge();
const restoredVsCodeState = vscodeBridge.restoreState();
const requestedScenarioId = initialUrl.searchParams.get('scenario') ?? restoredVsCodeState?.scenarioId ?? null;
const requestedSpectatorToken = initialUrl.searchParams.get('token');
const state = {
  mode:
    normalizeMode(initialUrl.searchParams.get('mode')) ??
    normalizeMode(restoredVsCodeState?.mode ?? null) ??
    (requestedSpectatorToken === null ? 'cockpit' : 'spectator'),
  scenarioId: payload.scenarios.some((scenario) => scenario.id === requestedScenarioId)
    ? requestedScenarioId
    : payload.defaultScenarioId,
  filter: normalizeFilter(initialUrl.searchParams.get('filter')) ?? normalizeFilter(restoredVsCodeState?.filter ?? null) ?? 'attention',
  jsonTab: 'branch',
  requestedSpectatorToken,
  proofSourceId:
    payload.proofSources.find((source) => source.available && source.emphasis === 'primary')?.id ??
    payload.proofSources.find((source) => source.available)?.id ??
    payload.proofSources[0]?.id ??
    null,
  observatorySourceId:
    payload.observatorySources.find((source) => source.available)?.id ?? payload.observatorySources[0]?.id ?? null
};

const outcomeLabel = {
  clear: 'clear',
  attention: 'attention',
  blocked: 'blocked'
};

function resolveVsCodeApi() {
  return typeof globalThis.acquireVsCodeApi === 'function' ? globalThis.acquireVsCodeApi() : null;
}

function normalizePersistedVsCodeState(value) {
  if (value === null || typeof value !== 'object') {
    return null;
  }

  const scenarioId = typeof value.scenarioId === 'string' && value.scenarioId.length > 0 ? value.scenarioId : null;
  const filter = normalizeFilter(typeof value.filter === 'string' ? value.filter : null);
  const mode = normalizeMode(typeof value.mode === 'string' ? value.mode : null);

  if (scenarioId === null || filter === null || mode === null) {
    return null;
  }

  return { scenarioId, filter, mode };
}

function createVsCodeBridge(api = resolveVsCodeApi()) {
  const transport = api === null ? 'browser-fallback' : 'vscode-webview';

  return {
    transport,
    degraded: api === null,
    postReady(nextState) {
      if (api === null) {
        return false;
      }

      api.postMessage({
        type: 'grimoire.vscode-panel.ready',
        protocolVersion: 'v1',
        transport,
        state: nextState
      });
      return true;
    },
    postCommand(payload) {
      if (api === null) {
        return false;
      }

      api.postMessage({
        type: 'grimoire.vscode-panel.command',
        protocolVersion: 'v1',
        payload
      });
      return true;
    },
    persistState(nextState) {
      if (typeof api?.setState !== 'function') {
        return;
      }

      api.setState(nextState);
    },
    restoreState() {
      if (typeof api?.getState !== 'function') {
        return null;
      }

      return normalizePersistedVsCodeState(api.getState());
    }
  };
}

const modeSelector = document.getElementById('mode-selector');
const modeCockpit = document.getElementById('mode-cockpit');
const modeKernel = document.getElementById('mode-kernel');
const modeProofs = document.getElementById('mode-proofs');
const modeGameUi = document.getElementById('mode-game-ui');
const modeObservability = document.getElementById('mode-observability');
const modeSpectator = document.getElementById('mode-spectator');
const modeObserver = document.getElementById('mode-observer');
const modeWorkflow = document.getElementById('mode-workflow');
const modeExpert = document.getElementById('mode-expert');
const modeObservatory = document.getElementById('mode-observatory');
const modeWarRoom = document.getElementById('mode-war-room');
const modeHostBridge = document.getElementById('mode-host-bridge');
const modeVsCode = document.getElementById('mode-vscode');
const scenarioCatalog = document.getElementById('scenario-catalog');
const scenarioSelector = document.getElementById('scenario-selector');
const filterSelector = document.getElementById('filter-selector');
const jsonSelector = document.getElementById('json-selector');
const scenarioTitle = document.getElementById('scenario-title');
const scenarioDescription = document.getElementById('scenario-description');
const scenarioOutcome = document.getElementById('scenario-outcome');
const scenarioTags = document.getElementById('scenario-tags');
const compareGrid = document.getElementById('compare-grid');
const walkthrough = document.getElementById('walkthrough');
const summaryGrid = document.getElementById('summary-grid');
const powerCardsGrid = document.getElementById('power-cards-grid');
const provenanceGrid = document.getElementById('provenance-grid');
const branchGrid = document.getElementById('branch-grid');
const jsonOutput = document.getElementById('json-output');
const copyJsonButton = document.getElementById('copy-json');
const kernelShell = document.getElementById('kernel-shell');
const proofsShell = document.getElementById('proofs-shell');
const gameUiShell = document.getElementById('game-ui-shell');
const observabilityShell = document.getElementById('observability-shell');
const observerShell = document.getElementById('observer-shell');
const workflowShell = document.getElementById('workflow-shell');
const expertShell = document.getElementById('expert-shell');
const spectatorShell = document.getElementById('spectator-shell');
const hostBridgeShell = document.getElementById('host-bridge-shell');
const vscodeShell = document.getElementById('vscode-shell');
const observatorySourceSelector = document.getElementById('observatory-source-selector');
const observatorySourceStatus = document.getElementById('observatory-source-status');
const observatoryOpenLink = document.getElementById('observatory-open-link');
const observatoryFrame = document.getElementById('observatory-frame');
const warRoomTitle = document.getElementById('war-room-title');
const warRoomSubtitle = document.getElementById('war-room-subtitle');
const warRoomOutcome = document.getElementById('war-room-outcome');
const warRoomTags = document.getElementById('war-room-tags');
const warRoomSummaryGrid = document.getElementById('war-room-summary-grid');
const warRoomFocusGrid = document.getElementById('war-room-focus-grid');
const warRoomRail = document.getElementById('war-room-rail');
const warRoomGrid = document.getElementById('war-room-grid');

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeMode(value) {
  switch (value) {
    case 'cockpit':
    case 'kernel':
    case 'proofs':
    case 'game-ui':
    case 'observability':
    case 'spectator':
    case 'observer':
    case 'workflow':
    case 'expert':
    case 'observatory':
    case 'war-room':
    case 'host-bridge':
    case 'vscode':
      return value;
    default:
      return null;
  }
}

function normalizeFilter(value) {
  switch (value) {
    case 'all':
    case 'attention':
    case 'blocked':
      return value;
    default:
      return null;
  }
}

function createShareUrl(shareQuery) {
  const shareUrl = new URL(window.location.href);
  shareUrl.search = shareQuery.startsWith('?') ? shareQuery.slice(1) : shareQuery;
  shareUrl.hash = '';
  return shareUrl.toString();
}

function createMetricGrid(items) {
  return items
    .map(
      (item) => `
        <article class="metric${item.tone === undefined ? '' : ` tone-${escapeHtml(item.tone)}`}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          ${item.note === undefined ? '' : `<small>${escapeHtml(item.note)}</small>`}
        </article>`
    )
    .join('');
}

function createPillList(values) {
  return values.map((value) => `<span class="pill">${escapeHtml(value)}</span>`).join('');
}

function badgeClassForTone(tone) {
  switch (tone) {
    case 'critical':
      return 'badge-outcome-blocked';
    case 'warning':
      return 'badge-outcome-attention';
    case 'positive':
      return 'badge-outcome-clear';
    default:
      return '';
  }
}

function getScenarioById(id) {
  return payload.scenarios.find((scenario) => scenario.id === id) ?? payload.scenarios[0];
}

function getReleaseReadyScenario() {
  return payload.scenarios.find((scenario) => scenario.id === 'release-ready') ?? payload.scenarios[0];
}

function getObservatorySourceById(id) {
  return payload.observatorySources.find((source) => source.id === id) ?? payload.observatorySources[0] ?? null;
}

function getProofSourceById(id) {
  return payload.proofSources.find((source) => source.id === id) ?? payload.proofSources[0] ?? null;
}

function getRoomPressureScore(room) {
  return room.alertCount * 5 + room.activeTaskCount * 4 + room.workingCount * 3 + room.nodeCount * 2 + (room.focus ? 7 : 0);
}

function getAgentPriority(agent) {
  const statusWeight = agent.status === 'working' ? 4 : agent.status === 'paused' ? 3 : agent.status === 'idle' ? 2 : 1;

  return statusWeight * 10 + agent.activeTaskCount * 4 + agent.childAgentCount;
}

function canPreviewProofSource(source) {
  return source?.available === true && source.browserPath !== null && source.format !== 'tgz';
}

function getFilterPredicate(kind) {
  if (state.filter === 'all') {
    return () => true;
  }

  if (kind === 'power') {
    if (state.filter === 'blocked') {
      return (card) => card.trustStatus === 'blocked' || card.issueCodes.includes('POWER_CARD_ACTIVATION_REJECTED');
    }

    return (card) => card.issueCodes.length > 0 || card.trustStatus === 'blocked';
  }

  if (kind === 'provenance') {
    if (state.filter === 'blocked') {
      return (entry) => entry.complianceStatus !== 'compliant';
    }

    return (entry) => entry.complianceStatus !== 'compliant' || entry.blockingReason !== null;
  }

  if (kind === 'branch') {
    if (state.filter === 'blocked') {
      return (option) => option.allowed === false;
    }

    return (option) => option.allowed === false || option.blockedReasons.length > 0;
  }

  return () => true;
}

function createWarRoomZones(scenario) {
  const blockedPowerCards = scenario.powerCardsView.cards.filter(
    (card) => card.issueCodes.length > 0 || card.trustStatus === 'blocked'
  );
  const blockedProvenance = scenario.provenanceView.entries.filter((entry) => entry.complianceStatus !== 'compliant');
  const blockedOptions = scenario.branchFinisherView.options.filter((option) => option.allowed === false);
  const mergeOption = scenario.branchFinisherView.options.find((option) => option.option === 'merge');
  const prOption = scenario.branchFinisherView.options.find((option) => option.option === 'pr');

  return [
    {
      id: 'ops-desk',
      title: 'Ops Desk',
      tone: scenario.branchFinisherView.shipBlocked ? 'blocked' : 'clear',
      subtitle: `branche ${scenario.branchFinisherView.branch}`,
      pills: [
        `ship ${scenario.branchFinisherView.shipBlocked ? 'blocked' : 'clear'}`,
        `${scenario.branchFinisherView.options.filter((option) => option.allowed).length} action(s) autorisee(s)`
      ],
      bullets:
        scenario.branchFinisherView.blockingReasons.length > 0
          ? scenario.branchFinisherView.blockingReasons
          : ['Aucun blocage global.']
    },
    {
      id: 'challenge-room',
      title: 'Challenge Room',
      tone: blockedPowerCards.length > 0 ? 'attention' : 'clear',
      subtitle: `${scenario.powerCardsView.summary.cardCount} power card(s) suivie(s)`,
      pills: [
        `${scenario.powerCardsView.summary.rejectedActivationCount} rejet(s)`,
        `${scenario.powerCardsView.summary.divergedCount} drift(s)`
      ],
      bullets: scenario.powerCardsView.cards.map((card) => {
        const issueLabel = card.issueCodes[0] ?? 'OK';
        return `${card.label}: ${card.trustStatus} / ${card.persistenceStatus} / ${issueLabel}`;
      })
    },
    {
      id: 'compliance-library',
      title: 'Compliance Library',
      tone: blockedProvenance.length > 0 ? 'blocked' : 'clear',
      subtitle: `${scenario.provenanceView.summary.entryCount} entree(s) de provenance`,
      pills: [
        `${scenario.provenanceView.summary.blockedEntryCount} blocage(s)`,
        `${scenario.provenanceView.summary.attributionBundleCount} bundle(s)`
      ],
      bullets:
        blockedProvenance.length > 0
          ? blockedProvenance.map((entry) => entry.blockingReason ?? `${entry.label} incomplete.`)
          : ['Toutes les entrees sont conformes.']
    },
    {
      id: 'release-gate',
      title: 'Release Gate',
      tone: blockedOptions.length > 0 ? 'blocked' : 'clear',
      subtitle: 'merge / pr / keep / discard',
      pills: [
        `merge ${mergeOption?.allowed ? 'allowed' : 'blocked'}`,
        `pr ${prOption?.allowed ? 'allowed' : 'blocked'}`
      ],
      bullets: scenario.branchFinisherView.options.map((option) => {
        const detail = option.blockedReasons[0] ?? 'aucune raison bloquante';
        return `${option.option}: ${option.allowed ? 'allowed' : 'blocked'} - ${detail}`;
      })
    }
  ];
}

function createCurrentVsCodePanelState() {
  return {
    scenarioId: state.scenarioId,
    filter: state.filter,
    mode: state.mode
  };
}

function createVsCodeCommandPayload(button) {
  switch (button.dataset.vscodeCommand) {
    case 'focus.trace':
      return button.dataset.traceId === undefined ? null : { command: 'focus.trace', traceId: button.dataset.traceId };
    case 'focus.task':
      return button.dataset.taskId === undefined ? null : { command: 'focus.task', taskId: button.dataset.taskId };
    case 'open.verification':
      return button.dataset.verificationRef === undefined
        ? null
        : { command: 'open.verification', verificationRef: button.dataset.verificationRef };
    case 'sync':
      return { command: 'sync' };
    default:
      return null;
  }
}

function renderModeButtons() {
  const modes = [
    { id: 'cockpit', label: 'Cockpit' },
    { id: 'kernel', label: 'Noyau Forge' },
    { id: 'proofs', label: 'Dossier de preuve' },
    { id: 'game-ui', label: 'Game UI' },
    { id: 'observability', label: 'Observability' },
    { id: 'spectator', label: 'Spectator' },
    { id: 'observer', label: 'Observer' },
    { id: 'workflow', label: 'Workflow' },
    { id: 'expert', label: 'Expert' },
    { id: 'observatory', label: 'Observatory' },
    { id: 'war-room', label: 'War Room' },
    { id: 'host-bridge', label: 'Host Bridge' },
    { id: 'vscode', label: 'VS Code' }
  ];

  modeSelector.innerHTML = modes
    .map(
      (mode) => `
        <button type="button" class="mode-button ${mode.id === state.mode ? 'is-active' : ''}" data-mode-id="${mode.id}">
          ${mode.label}
        </button>`
    )
    .join('');
}

function renderModeSections() {
  modeCockpit.hidden = state.mode !== 'cockpit';
  modeKernel.hidden = state.mode !== 'kernel';
  modeProofs.hidden = state.mode !== 'proofs';
  modeGameUi.hidden = state.mode !== 'game-ui';
  modeObservability.hidden = state.mode !== 'observability';
  modeSpectator.hidden = state.mode !== 'spectator';
  modeObserver.hidden = state.mode !== 'observer';
  modeWorkflow.hidden = state.mode !== 'workflow';
  modeExpert.hidden = state.mode !== 'expert';
  modeObservatory.hidden = state.mode !== 'observatory';
  modeWarRoom.hidden = state.mode !== 'war-room';
  modeHostBridge.hidden = state.mode !== 'host-bridge';
  modeVsCode.hidden = state.mode !== 'vscode';
}

function renderCatalog() {
  scenarioCatalog.innerHTML = payload.scenarios
    .map(
      (scenario) => `
        <article class="scenario-card">
          <div class="scenario-card-header">
            <div>
              <h3>${escapeHtml(scenario.title)}</h3>
              <p class="muted">${escapeHtml(scenario.description)}</p>
            </div>
            <span class="pill badge-outcome-${scenario.outcome}">${outcomeLabel[scenario.outcome]}</span>
          </div>
          <div class="scenario-tags">${scenario.tags.map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join('')}</div>
        </article>`
    )
    .join('');
}

function renderScenarioButtons() {
  scenarioSelector.innerHTML = payload.scenarios
    .map(
      (scenario) => `
        <button type="button" class="scenario-button ${scenario.id === state.scenarioId ? 'is-active' : ''}" data-scenario-id="${scenario.id}">
          ${escapeHtml(scenario.title)}
        </button>`
    )
    .join('');
}

function renderFilterButtons() {
  const filters = [
    { id: 'all', label: 'Tout' },
    { id: 'attention', label: 'Attention' },
    { id: 'blocked', label: 'Bloques' }
  ];

  filterSelector.innerHTML = filters
    .map(
      (filter) => `
        <button type="button" class="filter-button ${filter.id === state.filter ? 'is-active' : ''}" data-filter-id="${filter.id}">
          ${filter.label}
        </button>`
    )
    .join('');
}

function renderJsonButtons() {
  const tabs = [
    { id: 'power', label: 'powerCardsView' },
    { id: 'provenance', label: 'provenanceView' },
    { id: 'branch', label: 'branchFinisherView' },
    { id: 'kernel', label: 'kernelView' },
    { id: 'proofs', label: 'proofDossierView' },
    { id: 'game-ui', label: 'gameUiView' },
    { id: 'observability', label: 'observabilityView' },
    { id: 'spectator', label: 'spectatorView' },
    { id: 'workflow', label: 'workflowView' },
    { id: 'expert', label: 'expertView' },
    { id: 'host-bridge', label: 'genericHostBridgeView' },
    { id: 'vscode', label: 'vscodePanelView' },
    { id: 'control-plane', label: 'controlPlane' }
  ];

  jsonSelector.innerHTML = tabs
    .map(
      (tab) => `
        <button type="button" class="tab-button ${tab.id === state.jsonTab ? 'is-active' : ''}" data-tab-id="${tab.id}">
          ${tab.label}
        </button>`
    )
    .join('');
}

function renderScenarioHeader(scenario) {
  scenarioTitle.textContent = scenario.title;
  scenarioDescription.textContent = scenario.description;
  scenarioOutcome.className = `pill badge-outcome-${scenario.outcome}`;
  scenarioOutcome.textContent = outcomeLabel[scenario.outcome];
  scenarioTags.innerHTML = scenario.tags.map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join('');
}

function renderSummary(scenario) {
  const summaryItems = [
    {
      label: 'Cards with issues',
      value: scenario.powerCardsView.cards.filter((card) => card.issueCodes.length > 0).length,
      note: 'power cards'
    },
    {
      label: 'Kernel invariants',
      value: scenario.webViews.kernelView.invariants.length,
      note: 'noyau forge'
    },
    {
      label: 'Proof artefacts',
      value: payload.proofSources.filter((source) => source.available).length,
      note: payload.latestProofRunId ?? 'latest run unavailable'
    },
    {
      label: 'Blocked provenance entries',
      value: scenario.provenanceView.summary.blockedEntryCount,
      note: 'compliance'
    },
    {
      label: 'Blocked branch options',
      value: scenario.branchFinisherView.options.filter((option) => option.allowed === false).length,
      note: 'branch options'
    },
    {
      label: 'Workflow paths',
      value: scenario.webViews.workflowView.paths.length,
      note: 'web surface parity'
    }
  ];

  summaryGrid.innerHTML = createMetricGrid(summaryItems);
}

function renderCompare(scenario) {
  const baseline = getReleaseReadyScenario();
  const compareItems = [
    {
      label: 'Power card issues',
      current: scenario.powerCardsView.cards.filter((card) => card.issueCodes.length > 0).length,
      baseline: baseline.powerCardsView.cards.filter((card) => card.issueCodes.length > 0).length
    },
    {
      label: 'Blocked provenance entries',
      current: scenario.provenanceView.summary.blockedEntryCount,
      baseline: baseline.provenanceView.summary.blockedEntryCount
    },
    {
      label: 'Blocked branch options',
      current: scenario.branchFinisherView.options.filter((option) => option.allowed === false).length,
      baseline: baseline.branchFinisherView.options.filter((option) => option.allowed === false).length
    },
    {
      label: 'Workflow paths',
      current: scenario.webViews.workflowView.paths.length,
      baseline: baseline.webViews.workflowView.paths.length
    }
  ];

  compareGrid.innerHTML = compareItems
    .map((item) => {
      const delta = item.current - item.baseline;
      const deltaPrefix = delta > 0 ? '+' : '';
      return `
        <div class="metric">
          <span>${escapeHtml(item.label)}</span>
          <strong>${item.current}</strong>
          <small>delta ${deltaPrefix}${delta} vs baseline (${item.baseline})</small>
        </div>`;
    })
    .join('');
}

function renderWalkthrough(scenario) {
  walkthrough.innerHTML = scenario.walkthrough
    .map(
      (step, index) => `
        <div class="timeline-item">
          <div class="timeline-marker">${index + 1}</div>
          <div>
            <h3>Etape ${index + 1}</h3>
            <p class="muted">${escapeHtml(step)}</p>
          </div>
        </div>`
    )
    .join('');
}

function renderPowerCards(scenario) {
  const predicate = getFilterPredicate('power');
  const cards = scenario.powerCardsView.cards.filter(predicate);
  powerCardsGrid.innerHTML =
    cards.length === 0
      ? `<article class="card"><p class="muted">Aucune power card visible avec ce filtre.</p></article>`
      : cards
          .map(
            (card) => `
              <article class="card">
                <div class="card-header">
                  <div>
                    <h3>${escapeHtml(card.label)}</h3>
                    <p class="card-subtitle">${escapeHtml(card.cardId)} · ${escapeHtml(card.targetKind)}:${escapeHtml(card.targetId)}</p>
                  </div>
                  <span class="pill ${card.trustStatus === 'blocked' ? 'badge-outcome-blocked' : 'badge-outcome-clear'}">trust ${escapeHtml(card.trustStatus)}</span>
                </div>
                <div class="stat-pills">
                  <span class="pill">runtime ${card.runtimeEnabled ? 'on' : 'off'}</span>
                  <span class="pill">storage ${card.storageEnabled ? 'on' : 'off'}</span>
                  <span class="pill">${escapeHtml(card.persistenceStatus)}</span>
                  <span class="pill">${escapeHtml(card.requiredPolicy)}</span>
                </div>
                <ul class="issue-list">
                  ${card.issueCodes.length === 0 ? '<li>Aucun issue code</li>' : card.issueCodes.map((issue) => `<li>${escapeHtml(issue)}</li>`).join('')}
                </ul>
                <p class="muted">${escapeHtml(card.diagnostic ?? 'Aucun diagnostic bloquant.')}</p>
              </article>`
          )
          .join('');
}

function renderProvenance(scenario) {
  const predicate = getFilterPredicate('provenance');
  const entries = scenario.provenanceView.entries.filter(predicate);
  provenanceGrid.innerHTML =
    entries.length === 0
      ? `<article class="card"><p class="muted">Aucune entree de provenance visible avec ce filtre.</p></article>`
      : entries
          .map(
            (entry) => `
              <article class="card">
                <div class="card-header">
                  <div>
                    <h3>${escapeHtml(entry.label)}</h3>
                    <p class="card-subtitle">${escapeHtml(entry.entryId)} · ${escapeHtml(entry.kind)}</p>
                  </div>
                  <span class="pill ${entry.complianceStatus === 'compliant' ? 'badge-outcome-clear' : 'badge-outcome-blocked'}">${escapeHtml(entry.complianceStatus)}</span>
                </div>
                <div class="stat-pills">
                  <span class="pill">source ${escapeHtml(entry.sourceRef ?? 'missing')}</span>
                  <span class="pill">licence ${escapeHtml(entry.licenseId ?? 'missing')}</span>
                  <span class="pill">attribution ${entry.attributionRequired ? 'required' : 'optional'}</span>
                </div>
                <p class="muted">${escapeHtml(entry.blockingReason ?? 'Aucun blocage.')}</p>
              </article>`
          )
          .join('');
}

function renderBranchOptions(scenario) {
  const predicate = getFilterPredicate('branch');
  const options = scenario.branchFinisherView.options.filter(predicate);
  branchGrid.innerHTML =
    options.length === 0
      ? `<article class="card"><p class="muted">Aucune option de branche visible avec ce filtre.</p></article>`
      : options
          .map(
            (option) => `
              <article class="card">
                <div class="card-header">
                  <div>
                    <h3>${escapeHtml(option.option)}</h3>
                    <p class="card-subtitle">branch ${escapeHtml(scenario.branchFinisherView.branch)}</p>
                  </div>
                  <span class="pill ${option.allowed ? 'badge-outcome-clear' : 'badge-outcome-blocked'}">${option.allowed ? 'allowed' : 'blocked'}</span>
                </div>
                <ul class="issue-list">
                  ${option.blockedReasons.length === 0 ? '<li>Aucune raison bloquante</li>' : option.blockedReasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}
                </ul>
              </article>`
          )
          .join('');
}

function renderJson(scenario) {
  const tabToValue = {
    power: scenario.powerCardsView,
    provenance: scenario.provenanceView,
    branch: scenario.branchFinisherView,
    kernel: scenario.webViews.kernelView,
    proofs: scenario.webViews.proofDossierView,
    'game-ui': scenario.webViews.gameUiView,
    observability: scenario.webViews.observabilityView,
    spectator: scenario.webViews.spectatorView,
    workflow: scenario.webViews.workflowView,
    expert: scenario.webViews.expertView,
    'host-bridge': scenario.webViews.genericHostBridgeView,
    vscode: {
      ...scenario.webViews.vscodePanelView,
      connection: {
        ...scenario.webViews.vscodePanelView.connection,
        transport: vscodeBridge.transport,
        degraded: vscodeBridge.degraded,
        reason: vscodeBridge.degraded
          ? 'API VS Code indisponible. Le panel reste en preview navigateur et n envoie aucune commande au host.'
          : null
      }
    },
    'control-plane': scenario.controlPlane
  };
  jsonOutput.textContent = JSON.stringify(tabToValue[state.jsonTab], null, 2);
}

function renderKernelShell(scenario) {
  const kernelView = scenario.webViews.kernelView;

  kernelShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(kernelView.header.title)}</h2>
            <p class="muted">${escapeHtml(kernelView.header.subtitle)}</p>
          </div>
          <span class="pill ${badgeClassForTone(kernelView.header.tone)}">${escapeHtml(kernelView.header.projectId ?? 'project unavailable')}</span>
        </div>
        <div class="summary-grid compact-grid">
          ${createMetricGrid(
            kernelView.statCards.map((card) => ({
              label: card.label,
              value: card.value,
              note: card.hint,
              tone: card.tone
            }))
          )}
        </div>
        <div class="stat-pills panel-block-tight">
          ${createPillList([
            `run ${kernelView.header.runId ?? 'indisponible'}`,
            `protocol ${kernelView.header.protocolVersion}`,
            `trace ${kernelView.header.focusTraceId ?? 'aucune'}`,
            `task ${kernelView.header.focusTaskId ?? 'aucune'}`
          ])}
        </div>
      </section>

      <section class="content-grid expert-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Triade runtime</h2>
                <p class="muted">Nodes, leases et hosts partagent le meme focus pour rendre le plan de controle lisible d un seul regard.</p>
              </div>
            </div>
            <div class="card-grid">
              ${kernelView.triad
                .map(
                  (panel) => `
                    <article class="card">
                      <div class="row-spread">
                        <div>
                          <h3>${escapeHtml(panel.title)}</h3>
                          <p class="muted">${escapeHtml(panel.subtitle)}</p>
                        </div>
                        <span class="pill ${badgeClassForTone(panel.tone)}">${panel.items.length}</span>
                      </div>
                      <div class="card-grid dual-grid panel-block-tight">
                        ${panel.items
                          .map(
                            (item) => `
                              <article class="card${item.focus ? ' subcard is-focus' : ''}">
                                <div class="row-spread">
                                  <strong>${escapeHtml(item.title)}</strong>
                                  <span class="pill">${escapeHtml(item.subtitle)}</span>
                                </div>
                                <ul class="issue-list">
                                  ${item.details.map((detail) => `<li>${escapeHtml(detail)}</li>`).join('')}
                                </ul>
                              </article>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Causalite partagee</h2>
                <p class="muted">Le noyau montre le chainage court du run, de la trace focus au verdict release qui en decoule.</p>
              </div>
            </div>
            <div class="timeline">
              ${kernelView.causality
                .map(
                  (step, index) => `
                    <article class="card">
                      <div class="row-spread">
                        <span class="timeline-marker">${index + 1}</span>
                        <span class="pill ${badgeClassForTone(step.tone)}">${escapeHtml(step.value)}</span>
                      </div>
                      <div class="panel-block-tight">
                        <strong>${escapeHtml(step.label)}</strong>
                        <p class="muted">${escapeHtml(step.detail)}</p>
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Contrats en vigueur</h2>
                <p class="muted">Seulement les contrats utiles pour comprendre si le run reste robuste, relie et explicable.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${kernelView.contracts
                .map(
                  (contract) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(contract.label)}</strong>
                        <span class="pill ${badgeClassForTone(contract.tone)}">${escapeHtml(contract.version ?? 'linked')}</span>
                      </div>
                      <p class="muted">${escapeHtml(contract.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Invariants sous surveillance</h2>
                <p class="muted">Cette vue rend visibles les invariants qui conditionnent la confiance operatoire.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${kernelView.invariants
                .map(
                  (invariant) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(invariant.label)}</strong>
                        <span class="pill ${badgeClassForTone(invariant.tone)}">${escapeHtml(invariant.status)}</span>
                      </div>
                      <p class="muted">${escapeHtml(invariant.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderProofSources() {
  const sourceSelector = proofsShell.querySelector('#proof-source-selector');
  if (!(sourceSelector instanceof HTMLElement)) {
    return;
  }

  if (payload.proofSources.length === 0) {
    sourceSelector.innerHTML = `<article class="source-card"><p class="muted">Aucune preuve materialisee pour cette coque.</p></article>`;
    return;
  }

  sourceSelector.innerHTML = payload.proofSources
    .map(
      (source) => `
        <article class="source-card" data-available="${source.available}">
          <div class="row-spread">
            <div>
              <h3>${escapeHtml(source.label)}</h3>
              <p class="muted">${escapeHtml(source.description)}</p>
            </div>
            <span class="pill ${source.available ? 'badge-outcome-clear' : 'badge-outcome-attention'}">${escapeHtml(source.kind)}</span>
          </div>
          <div class="stat-pills panel-block-tight">
            ${createPillList([source.format, source.emphasis, source.runId ?? 'hors run'])}
          </div>
          <div class="stat-pills panel-block-tight">
            <button type="button" class="source-button ${source.id === state.proofSourceId ? 'is-active' : ''}" data-proof-source-id="${source.id}" ${source.available ? '' : 'disabled'}>
              ${source.available ? 'Afficher' : 'Indisponible'}
            </button>
          </div>
        </article>`
    )
    .join('');
}

function renderProofsShell(scenario) {
  const dossierView = scenario.webViews.proofDossierView;

  proofsShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(dossierView.header.title)}</h2>
            <p class="muted">${escapeHtml(dossierView.header.subtitle)}</p>
          </div>
          <span class="pill ${badgeClassForTone(dossierView.header.tone)}">${dossierView.header.releaseBlocked ? 'NO-GO' : 'GO'}</span>
        </div>
        <div class="summary-grid compact-grid">
          ${createMetricGrid(
            dossierView.statCards.map((card) => ({
              label: card.label,
              value: card.value,
              note: card.hint,
              tone: card.tone
            }))
          )}
        </div>
        <div class="stat-pills panel-block-tight">
          ${createPillList([
            `run ${payload.latestProofRunId ?? dossierView.header.runId ?? 'indisponible'}`,
            `${payload.proofSources.filter((source) => source.available).length} artefact(s) relies`,
            `${dossierView.header.blockingReasonCount} raison(s) bloquante(s)`
          ])}
        </div>
      </section>

      <section class="content-grid observatory-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Release gate</h2>
                <p class="muted">Le verdict reste explicite avant l exploration detaillee des preuves et artefacts relies.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${dossierView.gates
                .map(
                  (gate) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(gate.label)}</strong>
                        <span class="pill ${badgeClassForTone(gate.tone)}">${escapeHtml(String(gate.value))}</span>
                      </div>
                      <p class="muted">${escapeHtml(gate.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
            <div class="panel-block-tight">
              <h3>Raisons bloquantes</h3>
              ${dossierView.blockingReasons.length === 0
                ? '<p class="muted">Aucune raison bloquante explicite pour ce scenario.</p>'
                : `<ul class="issue-list">${dossierView.blockingReasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Evidence packs</h2>
                <p class="muted">Chaque pack reste lisible comme une unite de justification: mission, verification, coverage et reviews externes.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${dossierView.packs.length === 0
                ? '<article class="card"><p class="muted">Aucun evidence pack lie au scenario courant.</p></article>'
                : dossierView.packs
                    .map(
                      (pack) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(pack.missionTitle)}</strong>
                            <span class="pill ${badgeClassForTone(pack.tone)}">${escapeHtml(pack.verdict)}</span>
                          </div>
                          <p class="muted">${escapeHtml(pack.verificationRef)} · ${escapeHtml(pack.status)}</p>
                          <div class="stat-pills panel-block-tight">
                            ${createPillList([
                              `${pack.evidenceCount} evidence`,
                              `${pack.controlCount} controls`,
                              `${pack.externalReviewCount} review(s)`,
                              `${pack.attested ? 'attested' : 'pending attestation'}`
                            ])}
                          </div>
                          <p class="muted">${escapeHtml(pack.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Artefacts relies</h2>
                <p class="muted">Le rapport embarque le run de preuve et ses decisions, avec une navigation courte orientee verdict.</p>
              </div>
            </div>
            <div id="proof-source-selector" class="source-grid"></div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Apercu du dossier</h2>
                <p id="proof-source-status" class="muted"></p>
              </div>
              <a id="proof-open-link" class="nav-link-button" href="#" target="_blank" rel="noopener noreferrer">Ouvrir seul</a>
            </div>
            <iframe id="proof-frame" class="observatory-frame" title="Dossier de preuve"></iframe>
          </section>
        </aside>
      </section>
    </section>`;

  const source = getProofSourceById(state.proofSourceId);
  const sourceStatus = proofsShell.querySelector('#proof-source-status');
  const openLink = proofsShell.querySelector('#proof-open-link');
  const frame = proofsShell.querySelector('#proof-frame');

  renderProofSources();

  if (!(sourceStatus instanceof HTMLElement) || !(openLink instanceof HTMLAnchorElement) || !(frame instanceof HTMLIFrameElement)) {
    return;
  }

  if (source === null) {
    sourceStatus.textContent = 'Aucune source de preuve n est configuree pour cette coque.';
    openLink.href = '#';
    frame.removeAttribute('src');
    return;
  }

  sourceStatus.textContent = `${source.label} · ${source.kind} · ${source.available ? 'embarque' : 'absent'}`;
  openLink.href = source.browserPath ?? '#';

  if (canPreviewProofSource(source)) {
    if (frame.getAttribute('src') !== source.browserPath) {
      frame.setAttribute('src', source.browserPath);
    }
  } else {
    frame.removeAttribute('src');
  }
}

function renderGameUiShell(scenario) {
  const gameUiView = scenario.webViews.gameUiView;
  const observatorySource = getObservatorySourceById(state.observatorySourceId);
  const sortedRooms = [...gameUiView.rooms].sort((left, right) => getRoomPressureScore(right) - getRoomPressureScore(left));
  const focalRoom = sortedRooms.find((room) => room.focus) ?? sortedRooms[0] ?? null;
  const focalAgents =
    focalRoom === null
      ? []
      : [...gameUiView.agents]
          .filter((agent) => agent.roomId === focalRoom.roomId)
          .sort((left, right) => getAgentPriority(right) - getAgentPriority(left))
          .slice(0, 3);
  const priorityAgents = [...gameUiView.agents].sort((left, right) => getAgentPriority(right) - getAgentPriority(left)).slice(0, 6);
  const liveMissionCount = gameUiView.taskLanes.reduce((count, lane) => count + lane.count, 0);
  const verificationCount = gameUiView.verificationLanes.reduce((count, lane) => count + lane.count, 0);
  const shipBlockingSecurityCount = gameUiView.securityCards.filter((card) => card.blocksShip).length;
  const pixelDeckReady = observatorySource?.available === true;
  const focalSquadLabel =
    focalAgents.length === 0
      ? 'Aucune escouade visible'
      : focalAgents.map((agent) => agent.name.split(/\s+/)[0] ?? agent.name).join(' · ');
  const threatCards = [
    ...gameUiView.alerts.slice(0, 4).map((alert) => ({
      title: alert.code,
      detail: alert.message,
      meta: alert.roomId ?? alert.taskId ?? alert.agentId ?? 'global board',
      tone: alert.tone,
      tag: `Board ${alert.level}`
    })),
    ...gameUiView.attention.slice(0, 3).map((item) => ({
      title: item.label,
      detail: item.detail,
      meta: item.severity,
      tone: item.tone,
      tag: `Attention ${item.severity}`
    })),
    ...gameUiView.securityCards.slice(0, 3).map((card) => ({
      title: card.title,
      detail: card.detail,
      meta: `${card.surfaceId} · ${card.blocksShip ? 'ship blocked' : 'non-blocking'}`,
      tone: card.tone,
      tag: `Security ${card.severity}`
    }))
  ].slice(0, 8);

  gameUiShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft game-ui-stage">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(gameUiView.header.title)}</h2>
            <p class="muted">${escapeHtml(gameUiView.header.subtitle)}</p>
          </div>
          <span class="pill ${badgeClassForTone(gameUiView.header.tone)}">${escapeHtml(gameUiView.header.summary)}</span>
        </div>
        <div class="game-ui-stage-grid">
          <div class="game-ui-stage-stack">
            <div class="section-head">
              <div>
                <h3>Pixel command deck</h3>
                <p class="muted">Le Pixel Office adapte est maintenant dans la Game UI elle-meme, aligne sur la meme source Observatory que la vue dediee.</p>
              </div>
              ${
                pixelDeckReady && observatorySource?.url
                  ? `<a class="nav-link-button" href="${escapeHtml(observatorySource.url)}" target="_blank" rel="noopener noreferrer">Ouvrir seul</a>`
                  : ''
              }
            </div>
            <div class="stat-pills">
              ${
                payload.observatorySources.length === 0
                  ? '<span class="pill">Aucune source observatory</span>'
                  : payload.observatorySources
                      .map(
                        (source) => `
                          <button type="button" class="source-button ${source.id === state.observatorySourceId ? 'is-active' : ''}" data-game-ui-source-id="${source.id}" ${source.available ? '' : 'disabled'}>
                            ${escapeHtml(source.label)}
                          </button>`
                      )
                      .join('')
              }
            </div>
            <article class="game-ui-stage-panel">
              <div class="row-spread">
                <div>
                  <span class="toolbar-label">Live source</span>
                  <strong>${escapeHtml(observatorySource?.label ?? 'Pixel deck pending')}</strong>
                </div>
                <span class="pill ${pixelDeckReady ? 'badge-outcome-clear' : 'badge-outcome-attention'}">${pixelDeckReady ? 'synced' : 'waiting'}</span>
              </div>
              <p class="muted">
                ${escapeHtml(
                  pixelDeckReady
                    ? `${observatorySource?.scope ?? 'workspace'} artefact embarque dans la surface game UI.`
                    : 'Aucun observatory embarquable disponible pour la Game UI dans ce run.'
                )}
              </p>
              <div class="stat-pills">
                ${createPillList([`${liveMissionCount} mission(s)`, `${verificationCount} verification(s)`, `${sortedRooms.length} room(s)`])}
              </div>
            </article>
            <div class="summary-grid compact-grid">
              ${createMetricGrid([
                {
                  label: 'Pixel stage',
                  value: pixelDeckReady ? 'Live' : 'Pending',
                  note: observatorySource?.scope ?? 'manifest pending'
                },
                {
                  label: 'Focus room',
                  value: focalRoom?.roomId ?? 'No focus',
                  note: focalRoom === null ? 'Aucun secteur actif' : `${focalRoom.agentCount} agent(s) · ${focalRoom.nodeCount} node(s)`
                },
                {
                  label: 'Visible squad',
                  value: focalAgents.length,
                  note: focalSquadLabel
                },
                {
                  label: 'Threat rail',
                  value: threatCards.length,
                  note: `${gameUiView.alerts.length} alert(s) · ${shipBlockingSecurityCount} ship blocker(s)`
                }
              ])}
            </div>
          </div>
          <div class="game-ui-stage-panel">
            <div class="section-head">
              <div>
                <h3>Office command floor</h3>
                <p class="muted">Viewport pixel-agent reel, branche sur la source Observatory selectionnee.</p>
              </div>
            </div>
            ${
              pixelDeckReady && observatorySource?.url
                ? `<iframe class="observatory-frame game-ui-stage-frame" title="${escapeHtml(observatorySource.label)}" src="${escapeHtml(observatorySource.url)}"></iframe>`
                : '<article class="callout"><h3>Pixel stage indisponible</h3><p class="muted">Ajoute ou regenere un observatory.html pour rebrancher le viewport pixel-agent dans le report.</p></article>'
            }
          </div>
        </div>
      </section>

      <section class="content-grid observer-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Theater map</h2>
                <p class="muted">Les rooms lisent comme des secteurs vivants avec lead, pression et occupation plutot que comme un simple HUD de board.</p>
              </div>
            </div>
            <div class="observer-room-grid">
              ${sortedRooms
                .map(
                  (room) => `
                    <article class="card${room.focus ? ' subcard is-focus' : ''}">
                      <div class="row-spread">
                        <strong>${escapeHtml(room.roomId)}</strong>
                        <span class="pill ${badgeClassForTone(room.tone)}">${room.alertCount > 0 ? `${room.alertCount} alert(s)` : room.focus ? 'focus' : 'stable'}</span>
                      </div>
                      <p class="muted">lead ${escapeHtml(room.leadAgentName ?? room.leadAgentId ?? 'none')}</p>
                      <div class="stat-pills">
                        ${createPillList([`${room.agentCount} agent(s)`, `${room.activeTaskCount} mission(s)`, `${room.nodeCount} node(s)`])}
                      </div>
                      <p class="muted">working ${room.workingCount} · paused ${room.pausedCount} · idle ${room.idleCount} · offline ${room.offlineCount}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Mission deck</h2>
                <p class="muted">Les lanes de taches et de verification restent sur le meme plateau de commandement, sans creer de modele concurrent cote UI.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${gameUiView.taskLanes
                .map(
                  (lane) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill ${badgeClassForTone(lane.tone)}">${lane.count}</span>
                      </div>
                      <div class="panel-block-tight">
                        ${lane.tasks
                          .map(
                            (task) => `
                              <div class="panel-block-tight">
                                <strong>${escapeHtml(task.title)}</strong>
                                <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                              </div>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
              ${gameUiView.verificationLanes
                .map(
                  (lane) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill ${badgeClassForTone(lane.tone)}">${lane.count}</span>
                      </div>
                      <div class="panel-block-tight">
                        ${lane.items
                          .map(
                            (item) => `
                              <div class="panel-block-tight">
                                <strong>${escapeHtml(item.title)}</strong>
                                <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
                              </div>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Decision burst</h2>
                <p class="muted">Les cartes de decision qui peuvent debloquer ou ralentir le run restent concentrees dans une rafale courte.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${gameUiView.decisionCards.length === 0
                ? '<article class="card"><p class="muted">Aucune decision structuree pour ce scenario.</p></article>'
                : gameUiView.decisionCards
                    .slice(0, 6)
                    .map(
                      (card) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill ${badgeClassForTone(card.tone)}">${card.missingFieldCount} missing</span>
                          </div>
                          <p class="muted">${escapeHtml(card.taskTitle ?? card.taskId ?? card.roomId ?? 'unscoped')}</p>
                          <p class="muted">${escapeHtml(card.detail)}</p>
                          <div class="stat-pills">${createPillList([`${card.evidenceCount} evidence`, `${card.missingFieldCount} missing field(s)`])}</div>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Squad rail</h2>
                <p class="muted">Etat, room et dernier outil des agents visibles sans quitter la table tactique.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${priorityAgents
                .map(
                  (agent) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(agent.name)}</strong>
                        <span class="pill ${badgeClassForTone(agent.tone)}">${escapeHtml(agent.status)}</span>
                      </div>
                      <p class="muted">${escapeHtml(agent.role)} · room ${escapeHtml(agent.roomId)}</p>
                      <div class="stat-pills">${createPillList([`${agent.activeTaskCount} active task(s)`, `${agent.childAgentCount} child agent(s)`])}</div>
                      <p class="muted">last tool ${escapeHtml(agent.lastTool ?? 'none')}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Threat matrix</h2>
                <p class="muted">Alertes board, attention runtime et cartes security partagent maintenant la meme rampe d escalation.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${threatCards.length === 0
                ? '<article class="card"><p class="muted">Aucune menace runtime pour ce scenario.</p></article>'
                : threatCards
                    .map(
                      (card) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill ${badgeClassForTone(card.tone)}">${escapeHtml(card.tag)}</span>
                          </div>
                          <p class="muted">${escapeHtml(card.meta)}</p>
                          <p class="muted">${escapeHtml(card.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObservabilityShell(scenario) {
  const observabilityView = scenario.webViews.observabilityView;

  observabilityShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(observabilityView.header.title)}</h2>
            <p class="muted">${escapeHtml(observabilityView.header.subtitle)}</p>
          </div>
          <span class="pill ${badgeClassForTone(observabilityView.header.tone)}">${escapeHtml(observabilityView.header.summary)}</span>
        </div>
        <div class="summary-grid compact-grid">
          ${createMetricGrid(
            observabilityView.metricCards.map((card) => ({
              label: card.label,
              value: card.value,
              note: card.hint,
              tone: card.status === 'critical' ? 'critical' : card.status === 'warning' ? 'warning' : 'positive'
            }))
          )}
        </div>
      </section>

      <section class="content-grid expert-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Attention queue</h2>
                <p class="muted">Les signaux critiques, gaps timeline et findings security ont maintenant leur propre surface dediee.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observabilityView.attentionItems.length === 0
                ? '<article class="card"><p class="muted">Aucun signal d attention pour ce scenario.</p></article>'
                : observabilityView.attentionItems
                    .slice(0, 8)
                    .map(
                      (item) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(item.label)}</strong>
                            <span class="pill ${badgeClassForTone(item.severity === 'critical' ? 'critical' : item.severity === 'warning' ? 'warning' : 'neutral')}">${escapeHtml(item.severity)}</span>
                          </div>
                          <p class="muted">${escapeHtml(item.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Timeline slices</h2>
                <p class="muted">Lecture chronologique des derniers evenements observables, sans noyer le cockpit principal.</p>
              </div>
            </div>
            <div class="timeline">
              ${observabilityView.timelineRows
                .slice(0, 10)
                .map(
                  (row) => `
                    <article class="timeline-card ${badgeClassForTone(row.level === 'error' ? 'critical' : row.level === 'warning' ? 'warning' : 'neutral')}">
                      <span class="timeline-index">#${row.sequenceId}</span>
                      <strong>${escapeHtml(row.title)}</strong>
                      <p class="muted">${escapeHtml(row.detail)}</p>
                      <p class="muted">${escapeHtml(row.timestamp)} · ${escapeHtml(row.kind)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Verification blockers</h2>
                <p class="muted">Les taches non pretes pour le done sont explicites, avec leurs exigences manquantes.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observabilityView.blockedTasks.length === 0
                ? '<article class="card"><p class="muted">Aucun blocker de verification pour ce scenario.</p></article>'
                : observabilityView.blockedTasks
                    .slice(0, 6)
                    .map(
                      (task) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(task.title)}</strong>
                            <span class="pill ${badgeClassForTone(task.tone)}">${escapeHtml(task.status)}</span>
                          </div>
                          <div class="stat-pills">${createPillList([`${task.unmetRequirementCount} unmet`, `${task.evidenceCount} evidence`, `${task.traceCount} trace(s)`])}</div>
                          <p class="muted">${escapeHtml(task.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Connection health</h2>
                <p class="muted">Etat live/stale/disconnected des flux runtime et de leurs JSONL sources.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observabilityView.connectionIssues.length === 0
                ? '<article class="card"><p class="muted">Aucun incident de connexion detecte.</p></article>'
                : observabilityView.connectionIssues
                    .map(
                      (issue) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(issue.agentName ?? issue.agentId)}</strong>
                            <span class="pill ${badgeClassForTone(issue.tone)}">${escapeHtml(issue.status)}</span>
                          </div>
                          <p class="muted">${escapeHtml(issue.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Security hotspots</h2>
                <p class="muted">OWASP focus areas et clusters de collaboration visibles depuis la meme lecture observability.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observabilityView.securityHotspots
                .slice(0, 4)
                .map(
                  (hotspot) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(hotspot.label)}</strong>
                        <span class="pill ${badgeClassForTone(hotspot.tone)}">security</span>
                      </div>
                      <p class="muted">${escapeHtml(hotspot.detail)}</p>
                    </article>`
                )
                .join('')}
              ${observabilityView.collaborationHotspots
                .slice(0, 4)
                .map(
                  (hotspot) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(hotspot.label)}</strong>
                        <span class="pill ${badgeClassForTone(hotspot.tone)}">collab</span>
                      </div>
                      <p class="muted">${escapeHtml(hotspot.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Session watch</h2>
                <p class="muted">Les traces actives ou en attention sont enfin visibles hors de la seule lecture cockpit.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observabilityView.sessions
                .slice(0, 6)
                .map(
                  (session) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(session.title)}</strong>
                        <span class="pill ${badgeClassForTone(session.tone)}">${escapeHtml(session.status)}</span>
                      </div>
                      <p class="muted">trace ${escapeHtml(session.traceId)} · ${session.entryCount} entry(s) · ${session.errorCount} error(s)</p>
                      <p class="muted">${escapeHtml(session.lastEventTitle)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderSpectatorShell(scenario) {
  const spectatorView = scenario.webViews.spectatorView;
  const shareUrl = createShareUrl(scenario.spectatorShare.shareQuery);
  const requestedTokenMismatch =
    state.requestedSpectatorToken !== null && state.requestedSpectatorToken !== scenario.spectatorShare.tokenId;

  spectatorShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(spectatorView.banner.title)}</h2>
            <p class="muted">${escapeHtml(spectatorView.banner.detail)}</p>
          </div>
          <span class="pill ${spectatorView.banner.readOnly ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${spectatorView.banner.readOnly ? 'read-only' : 'mutable'}</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            { label: 'Principal', value: spectatorView.banner.principalId, note: spectatorView.banner.role },
            { label: 'Blocked writes', value: spectatorView.blockedMutations.length, note: 'runtime writes denied' },
            { label: 'Audit entries', value: spectatorView.auditTrail.length, note: 'forbidden actions traced' },
            {
              label: 'Channels',
              value: spectatorView.channels.length,
              note: spectatorView.channels.every((channel) => channel.reconnectable) ? 'reconnectable' : 'partial'
            }
          ])}
        </div>
        <div class="stat-pills panel-block-tight">
          <a class="nav-link-button" href="${escapeHtml(shareUrl)}">Ouvrir en spectateur</a>
          <button type="button" class="copy-button" data-copy-text="${escapeHtml(shareUrl)}" data-default-label="Copier le lien">Copier le lien</button>
          <button type="button" class="copy-button" data-copy-text="${escapeHtml(scenario.spectatorShare.tokenId)}" data-default-label="Copier le token">Copier le token</button>
        </div>
        ${requestedTokenMismatch ? '<article class="callout panel-block-tight"><h3>Token non concordant</h3><p class="muted">Le token demande dans l URL ne correspond pas au scenario courant. Le shell reste lisible, mais ce lien ne reference pas la meme session spectateur.</p></article>' : ''}
      </section>

      <section class="content-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Shared channels</h2>
                <p class="muted">Le navigateur et VS Code lisent la meme causalite sans exposer de surface d ecriture.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.channels
                .map(
                  (channel) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(channel.channel)}</strong>
                        <span class="pill ${channel.readOnly ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${channel.readOnly ? 'read-only' : 'write enabled'}</span>
                      </div>
                      <div class="stat-pills">
                        <span class="pill">${channel.reconnectable ? 'reconnectable' : 'fixed session'}</span>
                        <span class="pill">${channel.focusNavigation ? 'focus navigation' : 'focus locked'}</span>
                        <span class="pill">${channel.writeSurfaceCount} write surface(s)</span>
                      </div>
                      <ul class="issue-list">
                        ${channel.diagnostics.map((diagnostic) => `<li>${escapeHtml(diagnostic)}</li>`).join('')}
                      </ul>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Mutation guardrails</h2>
                <p class="muted">Les commandes de write restent bloquees tandis que la navigation locale conserve la lecture autorisee.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.blockedMutations
                .map(
                  (capability) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(capability.label)}</strong>
                        <span class="pill badge-outcome-blocked">blocked</span>
                      </div>
                      <p class="muted">${escapeHtml(capability.source)} · ${capability.mutation ? 'mutation' : 'navigation'}</p>
                      <p class="muted">${escapeHtml(capability.reason ?? 'Forbidden.')}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Capability rail</h2>
                <p class="muted">Preview complet des actions permises ou interdites pour le role spectateur.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.capabilities
                .map(
                  (capability) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(capability.label)}</strong>
                        <span class="pill ${capability.allowed ? 'badge-outcome-clear' : 'badge-outcome-blocked'}">${capability.allowed ? 'allowed' : 'blocked'}</span>
                      </div>
                      <p class="muted">${escapeHtml(capability.source)} · ${capability.mutation ? 'mutation' : 'navigation'}</p>
                      <p class="muted">${escapeHtml(capability.reason ?? 'No restriction.')}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Audit trail</h2>
                <p class="muted">Chaque tentative interdite reste visible dans la surface partagee.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.auditTrail
                .map(
                  (entry) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(entry.actionId)}</strong>
                        <span class="pill badge-outcome-blocked">${escapeHtml(entry.code)}</span>
                      </div>
                      <p class="muted">${escapeHtml(entry.source)} · ${escapeHtml(entry.at)}</p>
                      <p class="muted">${escapeHtml(entry.reason)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Share contract</h2>
                <p class="muted">Lien borne au scenario et token read-only emis via ${escapeHtml(scenario.spectatorShare.commandId)}.</p>
              </div>
            </div>
            <div class="summary-grid compact-grid">
              ${createMetricGrid([
                { label: 'Token', value: scenario.spectatorShare.tokenId, note: 'read-only' },
                { label: 'Principal', value: scenario.spectatorShare.principalId, note: 'issued by orchestrator' },
                { label: 'Issued at', value: scenario.spectatorShare.issuedAt, note: 'local demo proof' }
              ])}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObserverShell(scenario) {
  const observerView = scenario.webViews.observerView;

  observerShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Runtime observer</h2>
            <p class="muted">Projection spatiale du run: rooms, entites, handoffs et verification de parite avec le cockpit.</p>
          </div>
          <span class="pill ${observerView.parity.sameTaskCount && observerView.parity.sameAttentionCount ? 'badge-outcome-clear' : 'badge-outcome-attention'}">
            ${observerView.parity.sameTaskCount && observerView.parity.sameAttentionCount ? 'parity ok' : 'parity drift'}
          </span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            { label: 'Rooms', value: observerView.rooms.length, note: 'board rooms' },
            { label: 'Entities', value: observerView.entities.length, note: 'nodes, agents, tasks' },
            { label: 'Handoffs', value: observerView.handoffs.length, note: 'cross-room traces' },
            {
              label: 'Focus',
              value: observerView.parity.focusTaskId ?? observerView.parity.focusNodeId ?? 'none',
              note: observerView.parity.sameFocus ? 'shared focus' : 'focus drift'
            }
          ])}
        </div>
      </section>

      <section class="content-grid observer-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Rooms</h2>
                <p class="muted">Chaque room regroupe agents, tasks et noeuds lisibles depuis la meme causalite runtime.</p>
              </div>
            </div>
            <div class="observer-room-grid">
              ${observerView.rooms
                .map(
                  (room) => `
                    <article class="card${room.focus ? ' subcard is-focus' : ''}">
                      <div class="row-spread">
                        <strong>${escapeHtml(room.label)}</strong>
                        <span class="pill ${room.tone === 'critical' ? 'badge-outcome-blocked' : room.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${room.alertCount} alert(s)</span>
                      </div>
                      <div class="stat-pills">
                        <span class="pill">${room.agentIds.length} agent(s)</span>
                        <span class="pill">${room.taskIds.length} task(s)</span>
                        <span class="pill">${room.nodeIds.length} node(s)</span>
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Handoffs</h2>
                <p class="muted">Arcs de collaboration rehydrates depuis les edges du run.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observerView.handoffs.length === 0
                ? '<article class="card"><p class="muted">Aucun handoff pour ce scenario.</p></article>'
                : observerView.handoffs
                    .map(
                      (handoff) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(handoff.label)}</strong>
                            <span class="pill">${escapeHtml(handoff.relation)}</span>
                          </div>
                          <p class="muted">${escapeHtml(handoff.fromRoomId)} -> ${escapeHtml(handoff.toRoomId)}</p>
                          <p class="muted">task ${escapeHtml(handoff.taskId ?? 'none')} · ${escapeHtml(handoff.traceIds.join(', ') || 'no trace')}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Entities</h2>
                <p class="muted">Inventaire compact des noeuds, agents et tasks presents dans l observer.</p>
              </div>
            </div>
            <div class="observer-entity-grid">
              ${observerView.entities
                .map(
                  (entity) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(entity.label)}</strong>
                        <span class="pill">${escapeHtml(entity.kind)}</span>
                      </div>
                      <p class="muted">room ${escapeHtml(entity.roomId)}</p>
                      <div class="stat-pills">${entity.badges.map((badge) => `<span class="pill">${escapeHtml(badge)}</span>`).join('')}</div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Attention parity</h2>
                <p class="muted">Le meme rail d attention que le cockpit, visible depuis la carte spatiale.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${observerView.warRoomAttention
                .slice(0, 6)
                .map(
                  (item) => `
                    <article class="card">
                      <strong>${escapeHtml(item.label)}</strong>
                      <p class="muted">${escapeHtml(item.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderWorkflowShell(scenario) {
  const workflowView = scenario.webViews.workflowView;
  const workflowPaths = workflowView.paths.slice(0, 3);

  workflowShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Workflow visualization</h2>
            <p class="muted">Chemins de travail, decisions et audit trail relies par trace et task.</p>
          </div>
          <span class="pill">focus ${escapeHtml(workflowView.focus.taskId ?? workflowView.focus.traceId ?? 'none')}</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            { label: 'Paths', value: workflowView.paths.length, note: 'workflow groups' },
            {
              label: 'Active',
              value: workflowView.paths.filter((path) => path.isActive).length,
              note: 'active task status'
            },
            {
              label: 'Decisions',
              value: workflowView.paths.reduce((count, path) => count + path.decisions.length, 0),
              note: 'board cards'
            },
            {
              label: 'Audit rows',
              value: workflowView.paths.reduce((count, path) => count + path.auditTrail.length, 0),
              note: 'trimmed trail'
            }
          ])}
        </div>
      </section>

      <section class="workflow-path-grid">
        ${workflowPaths
          .map(
            (path) => `
              <article class="panel panel-soft">
                <div class="section-head">
                  <div>
                    <h2>${escapeHtml(path.taskTitle ?? path.traceId ?? path.id)}</h2>
                    <p class="muted">${escapeHtml(path.roomId ?? 'war-room')} · ${escapeHtml(path.taskStatus ?? 'unknown')}</p>
                  </div>
                  <span class="pill ${path.isActive ? 'badge-outcome-clear' : 'pill'}">${path.isActive ? 'active' : 'completed'}</span>
                </div>
                <div class="scenario-tags">${createPillList(path.contributors.map((contributor) => `${contributor.agentName ?? contributor.agentId} ${contributor.stepCount}/${contributor.decisionCount}`))}</div>
                <div class="workflow-columns panel-block-tight">
                  <div class="card-grid dual-grid">
                    ${path.steps
                      .map(
                        (step) => `
                          <article class="card">
                            <div class="row-spread">
                              <strong>${escapeHtml(step.title)}</strong>
                              <span class="pill">#${step.sequenceId}</span>
                            </div>
                            <p class="muted">${escapeHtml(step.detail)}</p>
                          </article>`
                      )
                      .join('')}
                  </div>
                  <div class="card-grid dual-grid">
                    ${path.decisions.length === 0
                      ? '<article class="card"><p class="muted">Aucune decision rattachee.</p></article>'
                      : path.decisions
                          .map(
                            (decision) => `
                              <article class="card">
                                <div class="row-spread">
                                  <strong>${escapeHtml(decision.title)}</strong>
                                  <span class="pill">${decision.evidenceCount} evidence</span>
                                </div>
                                <p class="muted">${escapeHtml(decision.detail)}</p>
                              </article>`
                          )
                          .join('')}
                  </div>
                  <div class="card-grid dual-grid">
                    ${path.auditTrail.length === 0
                      ? '<article class="card"><p class="muted">Aucun audit trail disponible.</p></article>'
                      : path.auditTrail
                          .map(
                            (entry) => `
                              <article class="card">
                                <div class="row-spread">
                                  <strong>${escapeHtml(entry.title)}</strong>
                                  <span class="pill">${escapeHtml(entry.kind)}</span>
                                </div>
                                <p class="muted">${escapeHtml(entry.detail)}</p>
                              </article>`
                          )
                          .join('')}
                  </div>
                </div>
              </article>`
          )
          .join('')}
      </section>
    </section>`;
}

function renderExpertShell(scenario) {
  const expertView = scenario.webViews.expertView;
  const inspection = expertView.inspection;

  expertShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Expert cockpit</h2>
            <p class="muted">Point d entree pour la revue experte: decisions host, preuve, replay et deep inspection d agent.</p>
          </div>
          <span class="pill ${expertView.status === 'accepted' ? 'badge-outcome-clear' : expertView.status === 'refused' ? 'badge-outcome-blocked' : 'badge-outcome-attention'}">
            ${escapeHtml(expertView.status)}
          </span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            { label: 'Trace', value: expertView.traceId ?? 'none', note: 'focus trace' },
            { label: 'Task', value: expertView.taskId ?? 'none', note: 'focus task' },
            { label: 'Host', value: expertView.hostDisplayName ?? 'none', note: expertView.summary },
            { label: 'Verification', value: expertView.proof.verdict ?? 'pending', note: expertView.proof.verificationRef ?? 'no verification ref' }
          ])}
        </div>
      </section>

      <section class="content-grid expert-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Decision relay</h2>
                <p class="muted">${escapeHtml(expertView.summary)}</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${expertView.decisions.length === 0
                ? '<article class="card"><p class="muted">Aucune decision host importee.</p></article>'
                : expertView.decisions
                    .map(
                      (decision) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(decision.actionKind)}</strong>
                            <span class="pill">${escapeHtml(decision.decision)}</span>
                          </div>
                          <p class="muted">${escapeHtml(decision.reason)}</p>
                          <p class="muted">${escapeHtml(decision.mode)} · scopes ${escapeHtml(decision.requiredScopes.join(', ') || 'none')}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Proof and replay</h2>
                <p class="muted">Verification queue, evidence pack et resume du replay canonical envelope.</p>
              </div>
            </div>
            <div class="summary-grid">
              ${createMetricGrid([
                { label: 'Queue', value: expertView.proof.queueStatus ?? 'none', note: expertView.proof.detail ?? 'no queue item' },
                { label: 'Evidence refs', value: expertView.proof.evidenceRefCount, note: expertView.proof.evidencePackId ?? 'no pack' },
                { label: 'External reviews', value: expertView.proof.externalReviewCount, note: expertView.proof.verificationRef ?? 'no review' },
                { label: 'Replay entries', value: expertView.replay.entryCount, note: expertView.replay.lastEventType ?? 'no replay' }
              ])}
            </div>
            <div class="card-grid dual-grid panel-block-tight">
              <article class="card">
                <h3>Workflow summary</h3>
                ${expertView.workflow.recentSteps
                  .map(
                    (step) => `
                      <div class="panel-block-tight">
                        <strong>${escapeHtml(step.title)}</strong>
                        <p class="muted">${escapeHtml(step.detail)}</p>
                      </div>`
                  )
                  .join('')}
              </article>
              <article class="card">
                <h3>Replay facets</h3>
                <div class="summary-grid compact-grid panel-block-tight">
                  ${createMetricGrid([
                    { label: 'Canonical envelopes', value: expertView.replay.canonicalEnvelopeCount },
                    { label: 'Message kinds', value: expertView.replay.messageTypes.length },
                    { label: 'Current step', value: expertView.workflow.currentStep ?? 'none' },
                    { label: 'Decision count', value: expertView.workflow.decisionCount }
                  ])}
                </div>
              </article>
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Inspection</h2>
                <p class="muted">Profil agent, budget tokens, actions autorisees et historique outil.</p>
              </div>
            </div>
            ${inspection === null
              ? '<p class="muted">Inspection non resolue.</p>'
              : `
                <div class="summary-grid compact-grid">
                  ${createMetricGrid([
                    { label: 'Agent', value: expertView.agentId ?? 'none', note: inspection.profile.branch ?? 'no branch' },
                    { label: 'Model', value: inspection.profile.model ?? 'unknown', note: inspection.profile.activeTool ?? 'no active tool' },
                    { label: 'Tool calls', value: inspection.sessionSummary.toolCallCount, note: `${inspection.sessionSummary.traceCount} trace(s)` },
                    { label: 'Verification', value: inspection.latestVerificationVerdict ?? 'pending', note: inspection.latestVerificationCorrelationId ?? 'no correlation' }
                  ])}
                </div>
                <div class="card-grid dual-grid panel-block-tight">
                  ${inspection.actions
                    .map(
                      (action) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(action.label)}</strong>
                            <span class="pill ${action.allowed ? 'badge-outcome-clear' : 'badge-outcome-attention'}">${action.allowed ? 'allowed' : 'blocked'}</span>
                          </div>
                          <p class="muted">${escapeHtml(action.reason ?? 'No restriction')}</p>
                        </article>`
                    )
                    .join('')}
                  ${inspection.toolHistory
                    .slice(0, 5)
                    .map(
                      (entry) => `
                        <article class="card">
                          <div class="row-spread">
                            <strong>${escapeHtml(entry.tool)}</strong>
                            <span class="pill">#${entry.sequenceId}</span>
                          </div>
                          <p class="muted">${escapeHtml(entry.summary)}</p>
                        </article>`
                    )
                    .join('')}
                </div>`}
          </section>
        </aside>
      </section>
    </section>`;
}

function renderVsCodeShell(scenario) {
  const panelView = {
    ...scenario.webViews.vscodePanelView,
    connection: {
      ...scenario.webViews.vscodePanelView.connection,
      transport: vscodeBridge.transport,
      degraded: vscodeBridge.degraded,
      reason: vscodeBridge.degraded
        ? 'API VS Code indisponible. Le panel reste en preview navigateur et n envoie aucune commande au host.'
        : null
    }
  };

  vscodeShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(panelView.header.title)}</h2>
            <p class="muted">${escapeHtml(panelView.header.subtitle)}</p>
          </div>
          <span class="pill ${panelView.connection.degraded ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(panelView.connection.transport)}</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            {
              label: 'Transport',
              value: panelView.connection.transport,
              note: panelView.connection.degraded ? 'preview navigateur' : 'webview host active',
              tone: panelView.connection.degraded ? 'warning' : 'positive'
            },
            { label: 'Scenario', value: scenario.title, note: scenario.id },
            {
              label: 'Focus',
              value: panelView.focus.taskId ?? panelView.focus.traceId ?? 'none',
              note: panelView.focus.taskTitle ?? panelView.focus.traceTitle ?? 'no focused trace'
            },
            {
              label: 'Commands',
              value: panelView.commands.filter((command) => command.enabled).length,
              note: 'bounded remounts only'
            }
          ])}
        </div>
        ${panelView.connection.reason === null ? '' : `<article class="callout panel-block-tight"><h3>Fallback navigateur</h3><p class="muted">${escapeHtml(panelView.connection.reason)}</p></article>`}
      </section>

      <section class="content-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Host commands</h2>
                <p class="muted">Remontee read-only vers le host VS Code, sinon preview purement locale.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${panelView.commands
                .map((command) => {
                  const actionable = command.enabled && !panelView.connection.degraded;

                  return `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(command.label)}</strong>
                        <span class="pill ${actionable ? 'badge-outcome-clear' : 'badge-outcome-attention'}">${actionable ? 'route to host' : 'preview only'}</span>
                      </div>
                      <p class="muted">${escapeHtml(command.detail)}</p>
                      <div class="stat-pills panel-block-tight">
                        <button
                          type="button"
                          class="source-button"
                          data-vscode-command="${escapeHtml(command.commandId)}"
                          ${command.traceId === null ? '' : `data-trace-id="${escapeHtml(command.traceId)}"`}
                          ${command.taskId === null ? '' : `data-task-id="${escapeHtml(command.taskId)}"`}
                          ${command.verificationRef === null ? '' : `data-verification-ref="${escapeHtml(command.verificationRef)}"`}
                          data-default-label="${escapeHtml(command.label)}"
                          ${actionable ? '' : 'disabled'}
                        >
                          ${escapeHtml(command.label)}
                        </button>
                      </div>
                    </article>`;
                })
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Runtime lanes</h2>
                <p class="muted">Les memes lanes de tasks et de verification que le cockpit restent visibles dans le panel.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${panelView.taskLanes
                .map(
                  (lane) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="panel-block-tight">
                        ${lane.tasks
                          .map(
                            (task) => `
                              <div class="panel-block-tight">
                                <strong>${escapeHtml(task.title)}</strong>
                                <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                              </div>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
              ${panelView.verificationLanes
                .map(
                  (lane) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="panel-block-tight">
                        ${lane.items
                          .map(
                            (item) => `
                              <div class="panel-block-tight">
                                <strong>${escapeHtml(item.title)}</strong>
                                <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
                              </div>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Attention rail</h2>
                <p class="muted">Meme lecture critique que le dashboard runtime, sans modele concurrent.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${panelView.attention
                .map(
                  (item) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(item.label)}</strong>
                        <span class="pill ${item.tone === 'critical' ? 'badge-outcome-blocked' : item.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(item.severity)}</span>
                      </div>
                      <p class="muted">${escapeHtml(item.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Host bridge</h2>
                <p class="muted">La fiche host reste visible meme en navigateur pur.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${panelView.hosts
                .map(
                  (host) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(host.displayName)}</strong>
                        <span class="pill ${host.tone === 'critical' ? 'badge-outcome-blocked' : host.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(host.connectionState)}</span>
                      </div>
                      <p class="muted">${escapeHtml(host.hostType)} · ${escapeHtml(host.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderHostBridgeShell(scenario) {
  const hostBridgeView = scenario.webViews.genericHostBridgeView;

  hostBridgeShell.innerHTML = `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(hostBridgeView.header.title)}</h2>
            <p class="muted">${escapeHtml(hostBridgeView.header.subtitle)}</p>
          </div>
          <span class="pill ${hostBridgeView.header.tone === 'critical' ? 'badge-outcome-blocked' : hostBridgeView.header.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${hostBridgeView.summary.hostCount} host(s)</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            {
              label: 'Channels',
              value: hostBridgeView.channels.length,
              note: `${hostBridgeView.summary.readyChannelCount} ready / ${hostBridgeView.summary.degradedChannelCount} degraded / ${hostBridgeView.summary.blockedChannelCount} blocked`
            },
            {
              label: 'Packets',
              value: hostBridgeView.packets.length,
              note: `${hostBridgeView.summary.readyPacketCount} ready / ${hostBridgeView.summary.reviewPendingPacketCount} pending`
            },
            {
              label: 'Imported reviews',
              value: hostBridgeView.summary.importedReviewCount,
              note: `${hostBridgeView.summary.importedContextCount} context import(s)`
            },
            {
              label: 'Denied decisions',
              value: hostBridgeView.summary.deniedDecisionCount,
              note: `${hostBridgeView.summary.promptedDecisionCount} prompt path(s)`
            }
          ])}
        </div>
      </section>

      <section class="content-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Channels</h2>
                <p class="muted">Une lecture unifiee du navigateur, du panel VS Code et des hotes externes a partir du meme run.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.channels
                .map(
                  (channel) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(channel.label)}</strong>
                        <span class="pill ${channel.tone === 'critical' ? 'badge-outcome-blocked' : channel.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(channel.status)}</span>
                      </div>
                      <p class="muted">${escapeHtml(channel.detail)}</p>
                      <div class="stat-pills panel-block-tight">
                        ${createPillList([`${channel.hostCount} host(s)`, `${channel.packetCount} packet(s)`])}
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Dispatch packets</h2>
                <p class="muted">Etat de dispatch, verdicts et preconditions d un meme packet a travers les surfaces.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.packets
                .map(
                  (packet) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(packet.taskTitle ?? packet.taskId)}</strong>
                        <span class="pill ${packet.tone === 'critical' ? 'badge-outcome-blocked' : packet.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(packet.status)}</span>
                      </div>
                      <p class="muted">packet ${escapeHtml(packet.packetId)} · trace ${escapeHtml(packet.traceId ?? 'none')}</p>
                      <div class="stat-pills panel-block-tight">
                        ${createPillList([
                          `${packet.hostIds.length} host(s)`,
                          `${packet.readyHostIds.length} ready`,
                          packet.readyForDispatch ? 'dispatchable' : 'gated'
                        ])}
                      </div>
                      <p class="muted">Decision ${escapeHtml(packet.latestDecision ?? 'none')} · review ${escapeHtml(packet.latestReviewVerdict ?? 'none')}</p>
                      <p class="muted">${escapeHtml(packet.missingRequirements.join(', ') || 'Aucun prerequis manquant.')}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </div>

        <aside class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Dispatch hosts</h2>
                <p class="muted">Permission mode, confiance et dernier signal importes par host.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.dispatchHosts
                .map(
                  (host) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(host.displayName)}</strong>
                        <span class="pill ${host.tone === 'critical' ? 'badge-outcome-blocked' : host.tone === 'warning' ? 'badge-outcome-attention' : 'badge-outcome-clear'}">${escapeHtml(host.connectionState)}</span>
                      </div>
                      <p class="muted">${escapeHtml(host.hostType)} · ${escapeHtml(host.permissionMode)} · trust ${escapeHtml(host.trustStatus)}</p>
                      <div class="stat-pills panel-block-tight">
                        ${createPillList([
                          `${host.packetCount} packet(s)`,
                          `${host.readyPacketCount} ready`,
                          `${host.openReviewFindingCount} finding(s)`
                        ])}
                      </div>
                      <p class="muted">Decision ${escapeHtml(host.latestDecision ?? 'none')} · review ${escapeHtml(host.latestReviewVerdict ?? 'none')}</p>
                      <p class="muted">${escapeHtml(host.reason ?? (host.routines.join(', ') || 'No routine declared.'))}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Imported evidence</h2>
                <p class="muted">Reviews externes, decisions et context imports relies au meme graphe runtime.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.recentReviews
                .map(
                  (review) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(review.review.reviewId)}</strong>
                        <span class="pill">${escapeHtml(review.review.verdict)}</span>
                      </div>
                      <p class="muted">${escapeHtml(review.review.hostId)} · ${escapeHtml(review.review.sourceType)}</p>
                    </article>`
                )
                .join('')}
              ${hostBridgeView.recentContextEntries
                .map(
                  (entry) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(entry.entry.entryId)}</strong>
                        <span class="pill">${escapeHtml(entry.entry.sourceType)}</span>
                      </div>
                      <p class="muted">${escapeHtml(entry.entry.hostId)} · trust ${escapeHtml(entry.entry.trustStatus)} · confidence ${entry.entry.confidence}</p>
                    </article>`
                )
                .join('')}
              ${hostBridgeView.recentInvocations
                .map(
                  (record) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(record.envelope.envelopeId)}</strong>
                        <span class="pill">${escapeHtml(record.decision)}</span>
                      </div>
                      <p class="muted">${escapeHtml(record.envelope.hostId)} · ${escapeHtml(record.envelope.actionKind)} · ${escapeHtml(record.reason)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObservatorySources() {
  if (payload.observatorySources.length === 0) {
    observatorySourceSelector.innerHTML = `<article class="source-card"><p class="muted">Aucune source observatory detectee.</p></article>`;
    return;
  }

  observatorySourceSelector.innerHTML = payload.observatorySources
    .map(
      (source) => `
        <article class="source-card" data-available="${source.available}">
          <div class="row-spread">
            <div>
              <h3>${escapeHtml(source.label)}</h3>
              <p class="muted">${escapeHtml(source.scope)}</p>
            </div>
            <span class="pill ${source.available ? 'badge-outcome-clear' : 'badge-outcome-attention'}">${source.available ? 'disponible' : 'absent'}</span>
          </div>
          <div class="stat-pills">
            <button type="button" class="source-button ${source.id === state.observatorySourceId ? 'is-active' : ''}" data-source-id="${source.id}" ${source.available ? '' : 'disabled'}>
              ${source.available ? 'Afficher' : 'Indisponible'}
            </button>
          </div>
        </article>`
    )
    .join('');
}

function renderObservatory() {
  const source = getObservatorySourceById(state.observatorySourceId);

  renderObservatorySources();

  if (source === null) {
    observatorySourceStatus.textContent = 'Aucune source observatory n est configuree pour cette coque.';
    observatoryOpenLink.href = '#';
    observatoryFrame.removeAttribute('src');
    return;
  }

  observatorySourceStatus.textContent = `${source.label} · ${source.scope} · ${source.available ? 'lecture seule disponible' : 'fichier absent'}`;
  observatoryOpenLink.href = source.url;

  if (source.available) {
    if (observatoryFrame.getAttribute('src') !== source.url) {
      observatoryFrame.setAttribute('src', source.url);
    }
  } else {
    observatoryFrame.removeAttribute('src');
  }
}

function renderWarRoom(scenario) {
  const blockedProvenanceCount = scenario.provenanceView.summary.blockedEntryCount;
  const blockedOptionsCount = scenario.branchFinisherView.options.filter((option) => option.allowed === false).length;
  const powerIssueCount = scenario.powerCardsView.cards.filter((card) => card.issueCodes.length > 0).length;
  const zones = createWarRoomZones(scenario);

  warRoomTitle.textContent = `${scenario.title} · War Room`;
  warRoomSubtitle.textContent = 'Lecture spatiale du meme run: ops desk, challenge room, compliance library et release gate.';
  warRoomOutcome.className = `pill badge-outcome-${scenario.outcome}`;
  warRoomOutcome.textContent = outcomeLabel[scenario.outcome];
  warRoomTags.innerHTML = scenario.tags.map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join('');

  warRoomSummaryGrid.innerHTML = createMetricGrid([
    { label: 'Zones bloquees', value: zones.filter((zone) => zone.tone === 'blocked').length, note: 'war room' },
    { label: 'Power issues', value: powerIssueCount, note: 'challenge room' },
    { label: 'Compliance gaps', value: blockedProvenanceCount, note: 'library' },
    { label: 'Branch gate', value: blockedOptionsCount === 0 ? 'clear' : 'blocked', note: 'release gate' }
  ]);

  warRoomFocusGrid.innerHTML = createMetricGrid([
    { label: 'Branche suivie', value: scenario.branchFinisherView.branch, note: 'focus courant' },
    { label: 'Primary blocker', value: scenario.branchFinisherView.blockingReasons[0] ?? 'none', note: 'cause racine' },
    { label: 'Observatory scope', value: 'adjacent read-only', note: 'meme shell' },
    { label: 'Command posture', value: scenario.branchFinisherView.shipBlocked ? 'caution' : 'ready', note: 'ops desk' }
  ]);

  warRoomRail.innerHTML = scenario.walkthrough
    .map(
      (step, index) => `
        <article class="war-room-rail-card">
          <h3>Signal ${index + 1}</h3>
          <p class="muted">${escapeHtml(step)}</p>
        </article>`
    )
    .join('');

  warRoomGrid.innerHTML = zones
    .map(
      (zone) => `
        <article class="war-room-zone" data-tone="${zone.tone}">
          <div class="section-head">
            <div>
              <h3>${escapeHtml(zone.title)}</h3>
              <p class="muted">${escapeHtml(zone.subtitle)}</p>
            </div>
            <span class="pill badge-outcome-${zone.tone}">${zone.tone}</span>
          </div>
          <div class="stat-pills">${zone.pills.map((pill) => `<span class="pill">${escapeHtml(pill)}</span>`).join('')}</div>
          <ul class="issue-list">${zone.bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}</ul>
        </article>`
    )
    .join('');
}

function render() {
  const scenario = getScenarioById(state.scenarioId);
  renderModeButtons();
  renderModeSections();
  renderScenarioButtons();
  renderFilterButtons();
  renderJsonButtons();
  renderScenarioHeader(scenario);
  renderSummary(scenario);
  renderCompare(scenario);
  renderWalkthrough(scenario);
  renderPowerCards(scenario);
  renderProvenance(scenario);
  renderBranchOptions(scenario);
  renderJson(scenario);
  renderKernelShell(scenario);
  renderProofsShell(scenario);
  renderGameUiShell(scenario);
  renderObservabilityShell(scenario);
  renderSpectatorShell(scenario);
  renderObserverShell(scenario);
  renderWorkflowShell(scenario);
  renderExpertShell(scenario);
  renderHostBridgeShell(scenario);
  renderVsCodeShell(scenario);
  renderObservatory();
  renderWarRoom(scenario);

  const currentVsCodeState = createCurrentVsCodePanelState();
  vscodeBridge.persistState(currentVsCodeState);
  if (state.mode === 'vscode') {
    vscodeBridge.postReady(currentVsCodeState);
  }
}

modeSelector.addEventListener('click', (event) => {
  const button = event.target.closest('[data-mode-id]');
  if (!button) {
    return;
  }

  state.mode = button.dataset.modeId;
  render();
});

scenarioSelector.addEventListener('click', (event) => {
  const button = event.target.closest('[data-scenario-id]');
  if (!button) {
    return;
  }

  state.scenarioId = button.dataset.scenarioId;
  render();
});

filterSelector.addEventListener('click', (event) => {
  const button = event.target.closest('[data-filter-id]');
  if (!button) {
    return;
  }

  state.filter = button.dataset.filterId;
  render();
});

jsonSelector.addEventListener('click', (event) => {
  const button = event.target.closest('[data-tab-id]');
  if (!button) {
    return;
  }

  state.jsonTab = button.dataset.tabId;
  render();
});

observatorySourceSelector.addEventListener('click', (event) => {
  const button = event.target.closest('[data-source-id]');
  if (!button) {
    return;
  }

  state.observatorySourceId = button.dataset.sourceId;
  render();
});

gameUiShell.addEventListener('click', (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }

  const button = event.target.closest('[data-game-ui-source-id]');
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  state.observatorySourceId = button.dataset.gameUiSourceId;
  render();
});

proofsShell.addEventListener('click', (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }

  const button = event.target.closest('[data-proof-source-id]');
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  state.proofSourceId = button.dataset.proofSourceId;
  render();
});

copyJsonButton.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(jsonOutput.textContent ?? '');
    copyJsonButton.textContent = 'Copie';
    window.setTimeout(() => {
      copyJsonButton.textContent = 'Copier JSON';
    }, 1200);
  } catch {
    copyJsonButton.textContent = 'Echec copie';
    window.setTimeout(() => {
      copyJsonButton.textContent = 'Copier JSON';
    }, 1200);
  }
});

spectatorShell.addEventListener('click', async (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }

  const button = event.target.closest('[data-copy-text]');
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  const copyText = button.dataset.copyText;
  const defaultLabel = button.dataset.defaultLabel ?? button.textContent ?? 'Copier';
  if (copyText === undefined) {
    return;
  }

  try {
    await navigator.clipboard.writeText(copyText);
    button.textContent = 'Copie';
  } catch {
    button.textContent = 'Echec copie';
  }

  window.setTimeout(() => {
    button.textContent = defaultLabel;
  }, 1200);
});

vscodeShell.addEventListener('click', (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }

  const button = event.target.closest('[data-vscode-command]');
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  const payload = createVsCodeCommandPayload(button);
  const defaultLabel = button.dataset.defaultLabel ?? button.textContent ?? 'Envoyer';
  if (payload === null) {
    return;
  }

  const posted = vscodeBridge.postCommand(payload);
  button.textContent = posted ? 'Envoye' : 'Preview only';

  window.setTimeout(() => {
    button.textContent = defaultLabel;
  }, 1200);
});

renderCatalog();
render();
