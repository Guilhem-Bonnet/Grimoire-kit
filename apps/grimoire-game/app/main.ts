import { createRuntimeViewsDemoData, type RuntimeViewsScenario } from '../examples/runtime-views-demo-data';
import {
  createVsCodePanelBridge,
  type VsCodePanelCommandPayload
} from '../src/bridge/vscode-webview-bridge';
import { createKanbanView, type KanbanCard } from '../src/state/kanban-view';
import { createRuntimeDashboardView } from '../src/state/runtime-dashboard-view';
import { RuntimeDashboardStore } from '../src/state/runtime-dashboard-store';
import { createVsCodePanelView, type VsCodePanelView } from '../src/state/vscode-panel-view';
import { HookEventsClient } from './hook-events-client';

import './styles.css';

type SurfaceMode =
  | 'cockpit'
  | 'mission-board'
  | 'kernel'
  | 'proofs'
  | 'game-ui'
  | 'observability'
  | 'spectator'
  | 'observer'
  | 'workflow'
  | 'expert'
  | 'observatory'
  | 'war-room'
  | 'host-bridge'
  | 'vscode';
type FilterMode = 'all' | 'attention' | 'blocked';

const PRIMARY_SURFACE_MODES: ReadonlyArray<readonly [SurfaceMode, string]> = [
  ['cockpit', 'Cockpit'],
  ['mission-board', 'Missions'],
  ['game-ui', 'Game UI'],
  ['kernel', 'Noyau'],
  ['proofs', 'Preuves'],
  ['observability', 'Observabilite'],
  ['war-room', 'War Room']
];

const ATLAS_SURFACE_MODES: ReadonlyArray<readonly [SurfaceMode, string]> = [
  ['observatory', 'Observatoire'],
  ['spectator', 'Spectateur'],
  ['observer', 'Observateur'],
  ['workflow', 'Workflow'],
  ['expert', 'Expert'],
  ['host-bridge', 'Host Bridge'],
  ['vscode', 'VS Code']
];

const SURFACE_ICONS: Record<SurfaceMode, string> = {
  cockpit: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 18 0"/><path d="M12 12l3-3"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><path d="M3 12h2M19 12h2M12 3v2"/></svg>',
  'mission-board': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="10" rx="1"/><rect x="17" y="4" width="4" height="13" rx="1"/></svg>',
  'game-ui': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/></svg>',
  kernel: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="1"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/></svg>',
  proofs: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  observability: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2-6 3 12 2-8 2 5 2-3h3"/></svg>',
  'war-room': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/></svg>',
  observatory: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18l7-12 4 2-7 12z"/><path d="M14 8l4 2"/><path d="M6 20l3-1"/><circle cx="18" cy="7" r="1.5"/></svg>',
  spectator: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  observer: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="12" r="4"/><circle cx="17" cy="12" r="4"/><path d="M7 8V5M17 8V5M10 12h4"/></svg>',
  workflow: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="12" r="2"/><path d="M8 6h5a3 3 0 0 1 3 3v0a3 3 0 0 1-3 3M8 18h5a3 3 0 0 0 3-3v0"/></svg>',
  expert: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 0-4 4v1a3 3 0 0 0-2 2.8V14a3 3 0 0 0 3 3v2a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-2a3 3 0 0 0 3-3v-3.2A3 3 0 0 0 16 8V7a4 4 0 0 0-4-4z"/><path d="M10 10h4M10 13h4"/></svg>',
  'host-bridge': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5v4M8 15v4M16 5v4M16 15v4"/><rect x="5" y="9" width="6" height="6" rx="1"/><rect x="13" y="9" width="6" height="6" rx="1"/><path d="M11 12h2"/></svg>',
  vscode: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/></svg>'
};

const SURFACE_DESCRIPTORS: Record<SurfaceMode, { label: string; detail: string }> = {
  cockpit: {
    label: 'Cockpit',
    detail: 'Release pressure, runtime proofs and host signals stay in one operator-first overview.'
  },
  'mission-board': {
    label: 'Mission Board',
    detail: 'A tactical kanban stays dominant while rooms, verification rails and causal history remain readable around the same runtime truth.'
  },
  kernel: {
    label: 'Noyau Forge',
    detail: 'Control-plane causality, contracts and invariants stay exposed without dropping into raw logs.'
  },
  proofs: {
    label: 'Dossier de preuve',
    detail: 'Evidence packs, blocking reasons and linked artifacts remain navigable as a short verdict path.'
  },
  'game-ui': {
    label: 'Game UI',
    detail: 'Rooms, missions, agents and threats now orbit a live pixel command deck instead of reading like a duplicate admin dashboard.'
  },
  observability: {
    label: 'Observability',
    detail: 'Attention queues, timeline slices and verification blockers surface runtime drift before release.'
  },
  spectator: {
    label: 'Spectator',
    detail: 'Read-only replay of the run for external observers and stakeholder walkthroughs.'
  },
  observer: {
    label: 'Observer',
    detail: 'Entity-level and room-level inspection for diagnosing where the runtime focus currently sits.'
  },
  workflow: {
    label: 'Workflow',
    detail: 'Execution paths and handoff sequencing make the run lisible as an operational flow.'
  },
  expert: {
    label: 'Expert',
    detail: 'Deep technical rails keep the heavy operational context available when the overview is not enough.'
  },
  observatory: {
    label: 'Observatory',
    detail: 'Embedded static observatories keep long-form evidence and exported dashboards one click away.'
  },
  'war-room': {
    label: 'War Room',
    detail: 'Escalation zones compress the blocked launch picture into an intervention surface.'
  },
  'host-bridge': {
    label: 'Host Bridge',
    detail: 'Cross-host packets, channels and imported reviews stay tied to the same runtime causality.'
  },
  vscode: {
    label: 'VS Code',
    detail: 'The host preview keeps the browser shell aligned with the webview transport contract.'
  }
};

/*
            <div class="game-hero-grid">
              <article class="game-focus-card">
                <div class="row-spread">
                  <div>
                    <span class="toolbar-label">Hot sector</span>
                    <strong>${escapeHtml(focalRoom?.roomId ?? 'No active room')}</strong>
                  </div>
                  <span class="pill tone-${escapeHtml(focalRoom?.tone ?? gameUiView.header.tone)}">${focalRoom === null ? 'standby' : `${focalRoom.alertCount} alert(s)`}</span>
                </div>
                <p class="muted">
                  ${escapeHtml(
                    focalRoom === null
                      ? 'No focus room projected for this scenario.'
                      : `Lead ${focalRoom.leadAgentName ?? focalRoom.leadAgentId ?? 'none'} keeps ${focalRoom.activeTaskCount} live task(s) anchored to ${focalRoom.nodeCount} runtime node(s).`
                  )}
                </p>
                <div class="chip-row">
                  ${
                    focalRoom === null
                      ? createPillList(['waiting for focus'])
                      : createPillList([
                          `${focalRoom.agentCount} agent(s)`,
                          `${focalRoom.activeTaskCount} live mission(s)`,
                          `${focalRoom.nodeCount} node(s)`
                        ])
                  }
                </div>
                <p class="game-status-line">
                  ${escapeHtml(
                    focalRoom === null
                      ? 'No occupancy signal yet.'
                      : `working ${focalRoom.workingCount} · paused ${focalRoom.pausedCount} · idle ${focalRoom.idleCount} · offline ${focalRoom.offlineCount}`
                  )}
                </p>
                <div class="game-squad-list">
                  ${
                    focalAgents.length === 0
                      ? '<span class="game-squad-chip">No visible squad</span>'
                      : focalAgents
                          .map(
                            (agent) => `
                              <span class="game-squad-chip">
                                <span class="game-avatar">${escapeHtml(getAgentInitials(agent.name))}</span>
                                ${escapeHtml(agent.name)}
                              </span>`
                          )
                          .join('')
                  }
                </div>
              </article>
              <div class="summary-grid compact-grid">
                ${heroCards
                  .map(
                    (card) => `
                      <article class="metric-card tone-${escapeHtml(card.tone)}">
                        <span>${escapeHtml(card.label)}</span>
                        <strong>${card.value}</strong>
                        <small>${escapeHtml(card.hint)}</small>
                      </article>`
                  )
                  .join('')}
              </div>
            </div>
          </section>

          <section class="content-grid game-layout">
            <div class="stack">
              <section class="panel panel-soft">
                <div class="section-head">
                  <div>
                    <h2>Theater map</h2>
                    <p class="muted">Rooms stop reading like admin buckets and start reading like live sectors with pressure, lead and occupancy.</p>
                  </div>
                </div>
                <div class="game-sector-grid">
                  ${sortedRooms
                    .map(
                      (room) => `
                        <article class="game-sector-card${room.focus ? ' is-focus' : ''}">
                          <div class="row-spread">
                            <div>
                              <span class="toolbar-label">Sector</span>
                              <h3>${escapeHtml(room.roomId)}</h3>
                            </div>
                            <span class="pill tone-${escapeHtml(room.tone)}">${room.alertCount > 0 ? `${room.alertCount} alert(s)` : room.focus ? 'focus' : 'stable'}</span>
                          </div>
                          <p class="muted">Lead ${escapeHtml(room.leadAgentName ?? room.leadAgentId ?? 'none')}</p>
                          <div class="game-sector-metrics">
                            <div>
                              <span>Agents</span>
                              <strong>${room.agentCount}</strong>
                            </div>
                            <div>
                              <span>Missions</span>
                              <strong>${room.activeTaskCount}</strong>
                            </div>
                            <div>
                              <span>Nodes</span>
                              <strong>${room.nodeCount}</strong>
                            </div>
                          </div>
                          <p class="game-status-line">working ${room.workingCount} · paused ${room.pausedCount} · idle ${room.idleCount} · offline ${room.offlineCount}</p>
                        </article>`
                    )
                    .join('')}
                </div>
              </section>

              <section class="panel panel-soft">
                <div class="section-head">
                  <div>
                    <h2>Mission deck</h2>
                    <p class="muted">Task lanes and verification queues become short objective columns instead of one more generic backlog grid.</p>
                  </div>
                </div>
                <div class="game-mission-grid">
                  <article class="mission-column">
                    <div class="row-spread">
                      <div>
                        <h3>Live missions</h3>
                        <p class="muted">Execution lanes pulled straight from the runtime board.</p>
                      </div>
                      <span class="pill">${liveMissionCount}</span>
                    </div>
                    <div class="game-mission-stack">
                      ${gameUiView.taskLanes.length === 0
                        ? '<article class="subcard"><p class="muted">No active mission lane for this scenario.</p></article>'
                        : gameUiView.taskLanes
                            .map(
                              (lane) => `
                                <article class="subcard">
                                  <div class="row-spread">
                                    <strong>${escapeHtml(lane.label)}</strong>
                                    <span class="pill tone-${escapeHtml(lane.tone)}">${lane.count}</span>
                                  </div>
                                  <div class="card-stack panel-block-tight">
                                    ${lane.tasks
                                      .slice(0, 3)
                                      .map(
                                        (task) => `
                                          <article class="lane-task">
                                            <strong>${escapeHtml(task.title)}</strong>
                                            <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                                          </article>`
                                      )
                                      .join('')}
                                    ${lane.tasks.length > 3 ? `<p class="muted">+${lane.tasks.length - 3} more mission(s)</p>` : ''}
                                  </div>
                                </article>`
                            )
                            .join('')}
                    </div>
                  </article>

                  <article class="mission-column">
                    <div class="row-spread">
                      <div>
                        <h3>Verification gate</h3>
                        <p class="muted">Checks that still hold the run stay visible next to execution, not hidden below it.</p>
                      </div>
                      <span class="pill">${verificationCount}</span>
                    </div>
                    <div class="game-mission-stack">
                      ${gameUiView.verificationLanes.length === 0
                        ? '<article class="subcard"><p class="muted">No verification pressure in this run.</p></article>'
                        : gameUiView.verificationLanes
                            .map(
                              (lane) => `
                                <article class="subcard">
                                  <div class="row-spread">
                                    <strong>${escapeHtml(lane.label)}</strong>
                                    <span class="pill tone-${escapeHtml(lane.tone)}">${lane.count}</span>
                                  </div>
                                  <div class="card-stack panel-block-tight">
                                    ${lane.items
                                      .slice(0, 3)
                                      .map(
                                        (item) => `
                                          <article class="lane-task">
                                            <strong>${escapeHtml(item.title)}</strong>
                                            <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
                                          </article>`
                                      )
                                      .join('')}
                                    ${lane.items.length > 3 ? `<p class="muted">+${lane.items.length - 3} more verification item(s)</p>` : ''}
                                  </div>
                                </article>`
                            )
                            .join('')}
                    </div>
                  </article>
                </div>
              </section>

              <section class="panel panel-soft">
                <div class="section-head">
                  <div>
                    <h2>Decision burst</h2>
                    <p class="muted">The cards that can unlock or stall the run sit in one short burst, not after several near-identical panels.</p>
                  </div>
                </div>
                <div class="game-decision-grid">
                  ${gameUiView.decisionCards.length === 0
                    ? '<article class="subcard"><p class="muted">No structured decision card for this scenario.</p></article>'
                    : gameUiView.decisionCards
                        .slice(0, 4)
                        .map(
                          (card) => `
                            <article class="subcard">
                              <div class="row-spread">
                                <strong>${escapeHtml(card.title)}</strong>
                                <span class="pill tone-${escapeHtml(card.tone)}">${card.missingFieldCount} missing</span>
                              </div>
                              <p class="muted">${escapeHtml(card.taskTitle ?? card.taskId ?? card.roomId ?? 'unscoped')}</p>
                              <p class="muted">${escapeHtml(card.detail)}</p>
                              <div class="chip-row">${createPillList([`${card.evidenceCount} evidence`, `${card.missingFieldCount} missing field(s)`])}</div>
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
                    <h2>Command rail</h2>
                    <p class="muted">Agents stay compact and immediately scannable: who is active, where, and with what load.</p>
                  </div>
                </div>
                <div class="game-agent-stack">
                  ${priorityAgents
                    .map(
                      (agent) => `
                        <article class="game-agent-card">
                          <div class="game-avatar">${escapeHtml(getAgentInitials(agent.name))}</div>
                          <div class="game-agent-detail">
                            <div class="row-spread">
                              <strong>${escapeHtml(agent.name)}</strong>
                              <span class="pill tone-${escapeHtml(agent.tone)}">${escapeHtml(agent.status)}</span>
                            </div>
                            <p class="muted">${escapeHtml(agent.role)} · room ${escapeHtml(agent.roomId)}</p>
                            <div class="game-agent-pills">${createPillList([`${agent.activeTaskCount} active`, `${agent.childAgentCount} child`, `tool ${agent.lastTool ?? 'none'}`])}</div>
                          </div>
                        </article>`
                    )
                    .join('')}
                </div>
              </section>

              <section class="panel panel-soft">
                <div class="section-head">
                  <div>
                    <h2>Threat matrix</h2>
                    <p class="muted">Board alerts, attention items and ship blockers share one escalation rail.</p>
                  </div>
                </div>
                <div class="rail-list">
                  ${threatCards.length === 0
                    ? '<article class="subcard"><p class="muted">No threat vector currently projected.</p></article>'
                    : threatCards
                        .map(
                          (card) => `
                            <article class="attention-card tone-${escapeHtml(card.tone)} game-threat-card">
                              <div class="row-spread">
                                <strong>${escapeHtml(card.title)}</strong>
                                <span class="pill tone-${escapeHtml(card.tone)}">${escapeHtml(card.tag)}</span>
                              </div>
                              <p class="muted">${escapeHtml(card.meta)}</p>
                              <p>${escapeHtml(card.detail)}</p>
                            </article>`
                        )
                        .join('')}
                </div>
              </section>
            </aside>
          </section>`;
                    : gameUiView.taskLanes
                        .map(
                          (lane) => `
                            <article class="subcard">
                              <div class="row-spread">
                                <strong>${escapeHtml(lane.label)}</strong>
                                <span class="pill tone-${escapeHtml(lane.tone)}">${lane.count}</span>
                              </div>
                              <div class="card-stack panel-block-tight">
                                ${lane.tasks
                                  .slice(0, 3)
                                  .map(
                                    (task) => `
                                      <article class="lane-task">
                                        <strong>${escapeHtml(task.title)}</strong>
                                        <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                                      </article>`
                                  )
                                  .join('')}
                                ${lane.tasks.length > 3 ? `<p class="muted">+${lane.tasks.length - 3} more mission(s)</p>` : ''}
                              </div>
                            </article>`
                        )
                        .join('')}
                </div>
              </article>

              <article class="mission-column">
                <div class="row-spread">
                  <div>
                    <h3>Verification gate</h3>
                    <p class="muted">Checks that still hold the run stay visible next to execution, not hidden below it.</p>
                  </div>
                  <span class="pill">${verificationCount}</span>
                </div>
                <div class="game-mission-stack">
                  ${gameUiView.verificationLanes.length === 0
                    ? '<article class="subcard"><p class="muted">No verification pressure in this run.</p></article>'
                    : gameUiView.verificationLanes
                        .map(
                          (lane) => `
                            <article class="subcard">
                              <div class="row-spread">
                                <strong>${escapeHtml(lane.label)}</strong>
                                <span class="pill tone-${escapeHtml(lane.tone)}">${lane.count}</span>
                              </div>
                              <div class="card-stack panel-block-tight">
                                ${lane.items
                                  .slice(0, 3)
                                  .map(
                                    (item) => `
                                      <article class="lane-task">
                                        <strong>${escapeHtml(item.title)}</strong>
                                        <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
                                      </article>`
                                  )
                                  .join('')}
                                ${lane.items.length > 3 ? `<p class="muted">+${lane.items.length - 3} more verification item(s)</p>` : ''}
                              </div>
                            </article>`
                        )
                        .join('')}
                </div>
              </article>
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Decision burst</h2>
                <p class="muted">The cards that can unlock or stall the run sit in one short burst, not after several near-identical panels.</p>
              </div>
            </div>
            <div class="game-decision-grid">
              ${gameUiView.decisionCards.length === 0
                ? '<article class="subcard"><p class="muted">No structured decision card for this scenario.</p></article>'
                : gameUiView.decisionCards
                    .slice(0, 4)
                    .map(
                      (card) => `
                        <article class="subcard">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill tone-${escapeHtml(card.tone)}">${card.missingFieldCount} missing</span>
                          </div>
                          <p class="muted">${escapeHtml(card.taskTitle ?? card.taskId ?? card.roomId ?? 'unscoped')}</p>
                          <p class="muted">${escapeHtml(card.detail)}</p>
                          <div class="chip-row">${createPillList([`${card.evidenceCount} evidence`, `${card.missingFieldCount} missing field(s)`])}</div>
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
                <h2>Command rail</h2>
                <p class="muted">Agents stay compact and immediately scannable: who is active, where, and with what load.</p>
              </div>
            </div>
            <div class="game-agent-stack">
              ${priorityAgents
                .map(
                  (agent) => `
                    <article class="game-agent-card">
                      <div class="game-avatar">${escapeHtml(getAgentInitials(agent.name))}</div>
                      <div class="game-agent-detail">
                        <div class="row-spread">
                          <strong>${escapeHtml(agent.name)}</strong>
                          <span class="pill tone-${escapeHtml(agent.tone)}">${escapeHtml(agent.status)}</span>
                        </div>
                        <p class="muted">${escapeHtml(agent.role)} · room ${escapeHtml(agent.roomId)}</p>
                        <div class="game-agent-pills">${createPillList([`${agent.activeTaskCount} active`, `${agent.childAgentCount} child`, `tool ${agent.lastTool ?? 'none'}`])}</div>
                      </div>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Threat matrix</h2>
                <p class="muted">Board alerts, attention items and ship blockers share one escalation rail.</p>
              </div>
            </div>
            <div class="rail-list">
              ${threatCards.length === 0
                ? '<article class="subcard"><p class="muted">No threat vector currently projected.</p></article>'
                : threatCards
                    .map(
                      (card) => `
                        <article class="attention-card tone-${escapeHtml(card.tone)} game-threat-card">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill tone-${escapeHtml(card.tone)}">${escapeHtml(card.tag)}</span>
                          </div>
                          <p class="muted">${escapeHtml(card.meta)}</p>
                          <p>${escapeHtml(card.detail)}</p>
                        </article>`
                    )
                    .join('')}
            </div>
          </section>
        </aside>
      </section>
      const traceId = button.dataset.traceId;
      return traceId === undefined ? null : { command: 'focus.trace', traceId };
    }
    case 'focus.task': {
      const taskId = button.dataset.taskId;
      return taskId === undefined ? null : { command: 'focus.task', taskId };
    }
    case 'open.verification': {
      const verificationRef = button.dataset.verificationRef;
      return verificationRef === undefined ? null : { command: 'open.verification', verificationRef };
    }
    case 'sync':
      return { command: 'sync' };
    default:
      return null;
  }
}

*/

interface ObservatoryManifestSource {
  id: string;
  label: string;
  scope: string;
  available: boolean;
  browserPath: string | null;
}

interface ObservatoryManifest {
  generatedAt: string;
  sources: readonly ObservatoryManifestSource[];
}

interface ProofManifestSource {
  id: string;
  label: string;
  kind: 'summary' | 'decision' | 'artifact' | 'spec';
  format: 'md' | 'json' | 'html' | 'tgz';
  emphasis: 'primary' | 'secondary';
  description: string;
  runId: string | null;
  available: boolean;
  browserPath: string | null;
}

interface ProofManifest {
  generatedAt: string;
  latestRunId: string | null;
  sources: readonly ProofManifestSource[];
}

const appRoot = document.querySelector('#app');
const vscodeBridge = createVsCodePanelBridge();

if (!(appRoot instanceof HTMLElement)) {
  throw new Error('Missing app root.');
}

const root = appRoot;
const demoData = createRuntimeViewsDemoData();
const initialUrl = new URL(window.location.href);
const restoredVsCodeState = vscodeBridge.restoreState();
const requestedScenarioId = initialUrl.searchParams.get('scenario') ?? restoredVsCodeState?.scenarioId ?? null;
const requestedSpectatorToken = initialUrl.searchParams.get('token');
const initialScenario: RuntimeViewsScenario = (() => {
  const scenario =
    demoData.scenarios.find((candidate) => candidate.id === requestedScenarioId) ??
    demoData.scenarios.find((candidate) => candidate.id === demoData.defaultScenarioId) ??
    demoData.scenarios[0];

  if (scenario === undefined) {
    throw new Error('No runtime cockpit scenario available.');
  }

  return scenario;
})();

const store = new RuntimeDashboardStore({
  initialState: initialScenario.state
});

const appState = {
  mode:
    normalizeSurfaceMode(initialUrl.searchParams.get('mode')) ??
    normalizeSurfaceMode(restoredVsCodeState?.mode ?? null) ??
    (requestedSpectatorToken === null ? 'cockpit' : 'spectator'),
  filter:
    normalizeFilterMode(initialUrl.searchParams.get('filter')) ??
    normalizeFilterMode(restoredVsCodeState?.filter ?? null) ??
    ('attention' as FilterMode),
  scenarioId: initialScenario.id,
  missionBoardFocusTaskId: null as string | null,
  observatorySourceId: null as string | null,
  proofSourceId: null as string | null,
  requestedSpectatorToken,
  // PR 3 — Linear-inspired UI state
  openPopoverId: null as string | null,
  inspectorOpen: false,
  inspectorTaskId: null as string | null,
  commandPaletteOpen: false,
  commandPaletteQuery: '',
  collapsedSections: new Set<string>([
    'branch-finisher',
    'watchtower',
    'workshop',
    'intake-desk',
    'seance-archive'
  ])
};

let observatoryManifest: ObservatoryManifest | null = null;
let observatoryError: string | null = null;
let proofManifest: ProofManifest | null = null;
let proofError: string | null = null;

// V1.6 — hook-events polling client. Created before the first render so
// the observability surface re-mounts the ledger on each render().
const hookEventsClient = new HookEventsClient();

render();
void loadObservatoryManifest();
void loadProofManifest();
setupKeyboardShortcuts();

hookEventsClient.start();

function setupKeyboardShortcuts(): void {
  const allModes: SurfaceMode[] = [
    ...PRIMARY_SURFACE_MODES.map(([m]) => m),
    ...ATLAS_SURFACE_MODES.map(([m]) => m)
  ];
  window.addEventListener('keydown', (event) => {
    // Ignore si on tape dans un input/textarea/contenteditable
    const target = event.target as HTMLElement | null;
    const inEditable =
      target !== null && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);

    // Cmd/Ctrl + K : ouvrir la palette (toujours actif, même depuis input)
    if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      appState.commandPaletteOpen = true;
      appState.commandPaletteQuery = '';
      appState.openPopoverId = null;
      render();
      // Focus input après render
      requestAnimationFrame(() => {
        const input = document.querySelector<HTMLInputElement>('[data-cmdk-input]');
        input?.focus();
      });
      return;
    }

    // Cmd/Ctrl + J : toggle inspector
    if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'j') {
      event.preventDefault();
      appState.inspectorOpen = !appState.inspectorOpen;
      render();
      return;
    }

    if (inEditable) {
      return;
    }

    // Cmd/Ctrl + 1..9 : switch surface
    if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey) {
      const digit = Number.parseInt(event.key, 10);
      if (digit >= 1 && digit <= 9 && digit <= allModes.length) {
        const nextMode = allModes[digit - 1];
        if (nextMode === undefined) {
          return;
        }
        event.preventDefault();
        appState.mode = nextMode;
        appState.missionBoardFocusTaskId = null;
        render();
        return;
      }
    }
    // Escape : ferme cmdk > popover > inspector > selection
    if (event.key === 'Escape') {
      if (appState.commandPaletteOpen) {
        event.preventDefault();
        appState.commandPaletteOpen = false;
        appState.commandPaletteQuery = '';
        render();
        return;
      }
      if (appState.openPopoverId !== null) {
        event.preventDefault();
        appState.openPopoverId = null;
        render();
        return;
      }
      if (appState.inspectorOpen) {
        event.preventDefault();
        appState.inspectorOpen = false;
        render();
        return;
      }
      if (appState.missionBoardFocusTaskId) {
        event.preventDefault();
        appState.missionBoardFocusTaskId = null;
        render();
      }
    }
  });
}

async function loadObservatoryManifest(): Promise<void> {
  try {
    const response = await fetch(new URL('./observatory/manifest.json', window.location.href).toString());
    if (!response.ok) {
      throw new Error(`Manifest request failed with status ${response.status}.`);
    }

    observatoryManifest = (await response.json()) as ObservatoryManifest;
    appState.observatorySourceId =
      observatoryManifest.sources.find((source) => source.available)?.id ?? observatoryManifest.sources[0]?.id ?? null;
    observatoryError = null;
  } catch (error) {
    observatoryManifest = null;
    observatoryError = error instanceof Error ? error.message : 'Observatory manifest unavailable.';
  }

  render();
}

async function loadProofManifest(): Promise<void> {
  try {
    const response = await fetch(new URL('./proofs/manifest.json', window.location.href).toString());
    if (!response.ok) {
      throw new Error(`Proof manifest request failed with status ${response.status}.`);
    }

    proofManifest = (await response.json()) as ProofManifest;
    appState.proofSourceId =
      proofManifest.sources.find((source) => source.available && source.emphasis === 'primary')?.id ??
      proofManifest.sources.find((source) => source.available)?.id ??
      proofManifest.sources[0]?.id ??
      null;
    proofError = null;
  } catch (error) {
    proofManifest = null;
    proofError = error instanceof Error ? error.message : 'Proof manifest unavailable.';
  }

  render();
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function describeSurfaceMode(mode: SurfaceMode): { label: string; detail: string } {
  return SURFACE_DESCRIPTORS[mode];
}

function getRoomPressureScore(room: RuntimeViewsScenario['webViews']['gameUiView']['rooms'][number]): number {
  return room.alertCount * 5 + room.activeTaskCount * 4 + room.workingCount * 3 + room.nodeCount * 2 + (room.focus ? 7 : 0);
}

function getAgentPriority(agent: RuntimeViewsScenario['webViews']['gameUiView']['agents'][number]): number {
  const statusWeight =
    agent.status === 'working' ? 4 : agent.status === 'paused' ? 3 : agent.status === 'idle' ? 2 : 1;

  return statusWeight * 10 + agent.activeTaskCount * 4 + agent.childAgentCount;
}

function getAgentInitials(name: string): string {
  const initials = name
    .split(/\s+/)
    .filter((segment) => segment.length > 0)
    .slice(0, 2)
    .map((segment) => segment[0]?.toUpperCase() ?? '')
    .join('');

  return initials.length > 0 ? initials : '??';
}

function normalizeSurfaceMode(value: string | null): SurfaceMode | null {
  switch (value) {
    case 'cockpit':
    case 'mission-board':
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

function getMissionBoardEmptyMessage(roomId: 'intake-desk' | 'war-room' | 'workshop' | 'branch-finisher' | 'seance-archive' | 'watchtower'): string {
  switch (roomId) {
    case 'intake-desk':
      return 'Aucune entree ne demande de qualification immediate.'
    case 'war-room':
      return 'Aucun arbitrage tactique actif sur cette run.'
    case 'workshop':
      return 'Aucun run actif ne monopolise encore le workshop.'
    case 'branch-finisher':
      return 'La file de verification est calme pour cette branche.'
    case 'seance-archive':
      return 'Aucune seance archivee ne remonte encore en surface.'
    case 'watchtower':
      return 'Aucun signal de derive ou d escalation ne domine la veille actuelle.'
  }
}

function normalizeFilterMode(value: string | null): FilterMode | null {
  switch (value) {
    case 'all':
    case 'attention':
    case 'blocked':
      return value;
    default:
      return null;
  }
}

function createShareUrl(shareQuery: string): string {
  const shareUrl = new URL(window.location.href);
  shareUrl.search = shareQuery.startsWith('?') ? shareQuery.slice(1) : shareQuery;
  shareUrl.hash = '';
  return shareUrl.toString();
}

function getScenarioById(scenarioId: string): RuntimeViewsScenario {
  return demoData.scenarios.find((scenario) => scenario.id === scenarioId) ?? initialScenario;
}

function getCurrentObservatorySource(): ObservatoryManifestSource | null {
  if (observatoryManifest === null) {
    return null;
  }

  return (
    observatoryManifest.sources.find((source) => source.id === appState.observatorySourceId) ??
    observatoryManifest.sources[0] ??
    null
  );
}

function getCurrentProofSource(): ProofManifestSource | null {
  if (proofManifest === null) {
    return null;
  }

  return proofManifest.sources.find((source) => source.id === appState.proofSourceId) ?? proofManifest.sources[0] ?? null;
}

function canPreviewProofSource(source: ProofManifestSource | null): boolean {
  return source?.available === true && source.browserPath !== null && source.format !== 'tgz';
}

function matchesPowerFilter(card: RuntimeViewsScenario['powerCardsView']['cards'][number]): boolean {
  if (appState.filter === 'all') {
    return true;
  }

  if (appState.filter === 'blocked') {
    return card.trustStatus === 'blocked' || card.issueCodes.includes('POWER_CARD_ACTIVATION_REJECTED');
  }

  return card.issueCodes.length > 0 || card.trustStatus === 'blocked';
}

function matchesProvenanceFilter(entry: RuntimeViewsScenario['provenanceView']['entries'][number]): boolean {
  if (appState.filter === 'all') {
    return true;
  }

  if (appState.filter === 'blocked') {
    return entry.complianceStatus !== 'compliant';
  }

  return entry.complianceStatus !== 'compliant' || entry.blockingReason !== null;
}

function matchesBranchFilter(option: RuntimeViewsScenario['branchFinisherView']['options'][number]): boolean {
  if (appState.filter === 'all') {
    return true;
  }

  if (appState.filter === 'blocked') {
    return option.allowed === false;
  }

  return option.allowed === false || option.blockedReasons.length > 0;
}

function createMetricGrid(
  items: ReadonlyArray<{ label: string; value: string | number; note?: string; tone?: string }>
): string {
  return items
    .map(
      (item) => `
        <article class="metric-card${item.tone === undefined ? '' : ` tone-${escapeHtml(item.tone)}`}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(String(item.value))}</strong>
          ${item.note === undefined ? '' : `<small>${escapeHtml(item.note)}</small>`}
        </article>`
    )
    .join('');
}

function createPillList(values: readonly string[]): string {
  return values.map((value) => `<span class="chip">${escapeHtml(value)}</span>`).join('');
}

function createWarRoomZones(scenario: RuntimeViewsScenario) {
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
      subtitle: `branch ${scenario.branchFinisherView.branch}`,
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
        `merge ${mergeOption?.allowed === true ? 'allowed' : 'blocked'}`,
        `pr ${prOption?.allowed === true ? 'allowed' : 'blocked'}`
      ],
      bullets: scenario.branchFinisherView.options.map((option) => {
        const detail = option.blockedReasons[0] ?? 'aucune raison bloquante';
        return `${option.option}: ${option.allowed ? 'allowed' : 'blocked'} - ${detail}`;
      })
    }
  ];
}

function createCurrentVsCodePanelState(): { scenarioId: string; filter: FilterMode; mode: SurfaceMode } {
  return {
    scenarioId: appState.scenarioId,
    filter: appState.filter,
    mode: appState.mode
  };
}

function createVsCodeCommandPayload(button: HTMLButtonElement): VsCodePanelCommandPayload | null {
  const commandId = button.dataset.vscodeCommand;

  switch (commandId) {
    case 'focus.trace': {
      const traceId = button.dataset.traceId;
      return traceId === undefined ? null : { command: 'focus.trace', traceId };
    }
    case 'focus.task': {
      const taskId = button.dataset.taskId;
      return taskId === undefined ? null : { command: 'focus.task', taskId };
    }
    case 'open.verification': {
      const verificationRef = button.dataset.verificationRef;
      return verificationRef === undefined ? null : { command: 'open.verification', verificationRef };
    }
    case 'sync':
      return { command: 'sync' };
    default:
      return null;
  }
}

function renderVsCodeMode(scenario: RuntimeViewsScenario, vscodePanelView: VsCodePanelView): string {
  const activeCommands = vscodePanelView.commands.filter((command) => command.enabled);

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(vscodePanelView.header.title)}</h2>
            <p class="muted">${escapeHtml(vscodePanelView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(vscodePanelView.header.tone)}">${escapeHtml(vscodePanelView.connection.transport)}</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            {
              label: 'Transport',
              value: vscodePanelView.connection.transport,
              note: vscodePanelView.connection.degraded ? 'preview navigateur' : 'webview host active',
              tone: vscodePanelView.connection.degraded ? 'warning' : 'positive'
            },
            {
              label: 'Scenario',
              value: scenario.title,
              note: scenario.id
            },
            {
              label: 'Focus',
              value: vscodePanelView.focus.taskId ?? vscodePanelView.focus.traceId ?? 'none',
              note: vscodePanelView.focus.taskTitle ?? vscodePanelView.focus.traceTitle ?? 'no focused trace'
            },
            {
              label: 'Commands',
              value: activeCommands.length,
              note: 'bounded remounts only',
              tone: activeCommands.length === 0 ? 'warning' : 'positive'
            }
          ])}
        </div>
        ${
          vscodePanelView.connection.reason === null
            ? ''
            : `<p class="callout warning panel-block-tight">${escapeHtml(vscodePanelView.connection.reason)}</p>`
        }
      </section>

      <section class="content-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Host commands</h2>
                <p class="muted">Les boutons remontent uniquement des intentions read-only ou de resynchronisation vers le host VS Code.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${vscodePanelView.commands
                .map((command) => {
                  const actionable = command.enabled && !vscodePanelView.connection.degraded;

                  return `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(command.label)}</strong>
                        <span class="pill tone-${actionable ? 'positive' : 'warning'}">${actionable ? 'route to host' : 'preview only'}</span>
                      </div>
                      <p class="muted">${escapeHtml(command.detail)}</p>
                      <div class="button-row button-row-wrap panel-block-tight">
                        <button
                          type="button"
                          class="control-button"
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
                <p class="muted">Le panel VS Code reste aligne sur les memes lanes de taches et de verification que le dashboard runtime.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${vscodePanelView.taskLanes
                .map(
                  (lane) => `
                    <article class="lane tone-${escapeHtml(lane.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="lane-list">
                        ${lane.tasks
                          .map(
                            (task) => `
                              <article class="lane-task">
                                <strong>${escapeHtml(task.title)}</strong>
                                <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                              </article>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
              ${vscodePanelView.verificationLanes
                .map(
                  (lane) => `
                    <article class="lane tone-${escapeHtml(lane.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="lane-list">
                        ${lane.items
                          .map(
                            (item) => `
                              <article class="lane-task">
                                <strong>${escapeHtml(item.title)}</strong>
                                <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
                              </article>`
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
                <p class="muted">Focus, verification et signaux critiques restent lisibles meme en fallback navigateur.</p>
              </div>
            </div>
            <div class="rail-list">
              ${vscodePanelView.attention
                .map(
                  (item) => `
                    <article class="attention-card tone-${escapeHtml(item.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(item.label)}</strong>
                        <span class="pill tone-${escapeHtml(item.tone)}">${escapeHtml(item.severity)}</span>
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
                <p class="muted">Les memes cartes host/proof restent visibles avant tout raccordement d extension.</p>
              </div>
            </div>
            <div class="card-stack">
              ${vscodePanelView.hosts
                .map(
                  (host) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(host.displayName)}</strong>
                        <span class="pill tone-${escapeHtml(host.tone)}">${escapeHtml(host.connectionState)}</span>
                      </div>
                      <p class="muted">${escapeHtml(host.hostType)} · ${escapeHtml(host.detail)}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Proof snapshot</h2>
                <p class="muted">Resume compact des cartes critiques conservees dans le panel.</p>
              </div>
            </div>
            <div class="summary-grid compact-grid">
              ${vscodePanelView.statCards
                .map(
                  (card) => `
                    <article class="metric-card tone-${escapeHtml(card.tone)}">
                      <span>${escapeHtml(card.label)}</span>
                      <strong>${card.value}</strong>
                      <small>${escapeHtml(card.hint)}</small>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderHostBridgeMode(scenario: RuntimeViewsScenario): string {
  const hostBridgeView = scenario.webViews.genericHostBridgeView;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(hostBridgeView.header.title)}</h2>
            <p class="muted">${escapeHtml(hostBridgeView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(hostBridgeView.header.tone)}">${hostBridgeView.summary.hostCount} host(s)</span>
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
              note: `${hostBridgeView.summary.readyPacketCount} ready / ${hostBridgeView.summary.reviewPendingPacketCount} review pending`
            },
            {
              label: 'Imported proofs',
              value: hostBridgeView.summary.importedReviewCount,
              note: `${hostBridgeView.summary.importedContextCount} context import(s)`
            },
            {
              label: 'Decisions',
              value: hostBridgeView.summary.deniedDecisionCount,
              note: `${hostBridgeView.summary.promptedDecisionCount} prompt / deny path(s)`
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
                <p class="muted">Le navigateur, VS Code et les hôtes externes lisent le meme run sans modele concurrent.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.channels
                .map(
                  (channel) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(channel.label)}</strong>
                        <span class="pill tone-${escapeHtml(channel.tone)}">${escapeHtml(channel.status)}</span>
                      </div>
                      <p class="muted">${escapeHtml(channel.detail)}</p>
                      <div class="chip-row">
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
                <p class="muted">Chaque paquet expose son statut de dispatch, ses preconditions et ses host ids sans perdre la trace runtime.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${hostBridgeView.packets
                .map(
                  (packet) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(packet.taskTitle ?? packet.taskId)}</strong>
                        <span class="pill tone-${escapeHtml(packet.tone)}">${escapeHtml(packet.status)}</span>
                      </div>
                      <p class="muted">packet ${escapeHtml(packet.packetId)} · trace ${escapeHtml(packet.traceId ?? 'none')}</p>
                      <div class="chip-row">
                        ${createPillList([
                          `${packet.hostIds.length} host(s)`,
                          `${packet.readyHostIds.length} ready`,
                          packet.readyForDispatch ? 'dispatchable' : 'gated'
                        ])}
                      </div>
                      <p class="muted">Decision ${escapeHtml(packet.latestDecision ?? 'none')} · review ${escapeHtml(packet.latestReviewVerdict ?? 'none')} · ${packet.openReviewFindingCount} open finding(s)</p>
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
                <p class="muted">Vue generique par host: permission mode, routines, review channels et dernier signal de confiance.</p>
              </div>
            </div>
            <div class="card-stack">
              ${hostBridgeView.dispatchHosts
                .map(
                  (host) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(host.displayName)}</strong>
                        <span class="pill tone-${escapeHtml(host.tone)}">${escapeHtml(host.connectionState)}</span>
                      </div>
                      <p class="muted">${escapeHtml(host.hostType)} · ${escapeHtml(host.permissionMode)} · trust ${escapeHtml(host.trustStatus)}</p>
                      <div class="chip-row">
                        ${createPillList([
                          `${host.packetCount} packet(s)`,
                          `${host.readyPacketCount} ready`,
                          `${host.blockedPacketCount} blocked`
                        ])}
                      </div>
                      <p class="muted">Decision ${escapeHtml(host.latestDecision ?? 'none')} · review ${escapeHtml(host.latestReviewVerdict ?? 'none')} · ${host.openReviewFindingCount} open finding(s)</p>
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
                <p class="muted">Les review artifacts, decisions et context imports restent relies a la meme causalite du run.</p>
              </div>
            </div>
            <div class="card-stack">
              ${hostBridgeView.recentReviews
                .map(
                  (review) => `
                    <article class="subcard">
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
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(entry.entry.entryId)}</strong>
                        <span class="pill">${escapeHtml(entry.entry.sourceType)}</span>
                      </div>
                      <p class="muted">${escapeHtml(entry.entry.hostId)} · trust ${escapeHtml(entry.entry.trustStatus)} · confidence ${entry.entry.confidence}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderCockpitMode(scenario: RuntimeViewsScenario): string {
  const cockpitView = scenario.webViews.cockpitView;
  const powerCards = scenario.powerCardsView.cards.filter(matchesPowerFilter);
  const provenanceEntries = scenario.provenanceView.entries.filter(matchesProvenanceFilter);
  const branchOptions = scenario.branchFinisherView.options.filter(matchesBranchFilter);
  const attentionItems = cockpitView.ui.attention.slice(0, 6);
  const timelinePoints = cockpitView.ui.timeline.slice(0, 6);
  const taskLanes = cockpitView.ui.lanes.filter((lane) => lane.count > 0);

  return `
    <section class="content-grid">
      <div class="stack">
        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>${escapeHtml(cockpitView.ui.header.title)}</h2>
              <p class="muted">${escapeHtml(cockpitView.ui.header.subtitle)}</p>
            </div>
            <span class="pill tone-${escapeHtml(cockpitView.header.tone)}">${escapeHtml(cockpitView.header.summary)}</span>
          </div>
          <div class="summary-grid">
            ${cockpitView.ui.statCards
              .slice(0, 8)
              .map(
                (card) => `
                  <article class="metric-card tone-${escapeHtml(card.tone)}">
                    <span>${escapeHtml(card.label)}</span>
                    <strong>${card.value}</strong>
                    <small>${escapeHtml(card.hint)}</small>
                  </article>`
              )
              .join('')}
          </div>
        </section>

        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Read model signals</h2>
              <p class="muted">Power cards, provenance et branch finisher restent lisibles sans second modele concurrent.</p>
            </div>
          </div>
          <div class="card-grid triple-grid">
            <article class="card-stack">
              <h3>Power cards</h3>
              ${
                powerCards.length === 0
                  ? '<p class="muted">Aucune power card visible avec ce filtre.</p>'
                  : powerCards
                      .map(
                        (card) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(card.label)}</strong>
                              <span class="pill tone-${card.trustStatus === 'blocked' ? 'critical' : 'positive'}">${escapeHtml(card.trustStatus)}</span>
                            </div>
                            <p class="muted">${escapeHtml(card.persistenceStatus)} · ${escapeHtml(card.requiredPolicy)}</p>
                            <ul class="bullet-list">
                              ${(card.issueCodes.length === 0 ? ['Aucun issue code'] : card.issueCodes)
                                .map((issue) => `<li>${escapeHtml(issue)}</li>`)
                                .join('')}
                            </ul>
                          </article>`
                      )
                      .join('')
              }
            </article>

            <article class="card-stack">
              <h3>Provenance</h3>
              ${
                provenanceEntries.length === 0
                  ? '<p class="muted">Aucune entree visible avec ce filtre.</p>'
                  : provenanceEntries
                      .map(
                        (entry) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(entry.label)}</strong>
                              <span class="pill tone-${entry.complianceStatus === 'compliant' ? 'positive' : 'critical'}">${escapeHtml(entry.complianceStatus)}</span>
                            </div>
                            <p class="muted">${escapeHtml(entry.kind)} · licence ${escapeHtml(entry.licenseId ?? 'missing')}</p>
                            <p class="muted">${escapeHtml(entry.blockingReason ?? 'Aucun blocage.')}</p>
                          </article>`
                      )
                      .join('')
              }
            </article>

            <article class="card-stack">
              <h3>Branch finisher</h3>
              ${
                branchOptions.length === 0
                  ? '<p class="muted">Aucune option visible avec ce filtre.</p>'
                  : branchOptions
                      .map(
                        (option) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(option.option)}</strong>
                              <span class="pill tone-${option.allowed ? 'positive' : 'critical'}">${option.allowed ? 'allowed' : 'blocked'}</span>
                            </div>
                            <ul class="bullet-list">
                              ${(option.blockedReasons.length === 0 ? ['Aucune raison bloquante'] : option.blockedReasons)
                                .map((reason) => `<li>${escapeHtml(reason)}</li>`)
                                .join('')}
                            </ul>
                          </article>`
                      )
                      .join('')
              }
            </article>
          </div>
        </section>

        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Hosts and proof rail</h2>
              <p class="muted">Le shell web ajoute la couche host et verification en restant aligne avec les read models runtime.</p>
            </div>
          </div>
          <div class="card-grid dual-grid">
            <article class="card-stack">
              <h3>Host bridge</h3>
              ${cockpitView.hosts
                .map(
                  (host) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(host.displayName)}</strong>
                        <span class="pill tone-${escapeHtml(host.tone)}">${escapeHtml(host.connectionState)}</span>
                      </div>
                      <p class="muted">${escapeHtml(host.hostType)} · trust ${escapeHtml(host.trustStatus)}</p>
                      <div class="chip-row">
                        ${createPillList([
                          `${host.reviewArtifactCount} review(s)`,
                          `${host.openReviewFindingCount} open finding(s)`
                        ])}
                      </div>
                      <p class="muted">${escapeHtml(host.routines.join(', ') || 'no routines')}</p>
                    </article>`
                )
                .join('')}
            </article>

            <article class="card-stack">
              <h3>Proofs</h3>
              ${cockpitView.proofs
                .slice(0, 6)
                .map(
                  (proof) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(proof.source)}</strong>
                        <span class="pill tone-${escapeHtml(proof.tone)}">${escapeHtml(proof.verificationRef ?? proof.taskId ?? 'runtime')}</span>
                      </div>
                      <p class="muted">${escapeHtml(proof.detail)}</p>
                    </article>`
                )
                .join('')}
            </article>
          </div>
        </section>

        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Flow lanes</h2>
              <p class="muted">Lecture rapide des tasks du dashboard runtime.</p>
            </div>
          </div>
          <div class="lane-grid">
            ${taskLanes
              .map(
                (lane) => `
                  <article class="lane tone-${escapeHtml(lane.tone)}">
                    <div class="row-spread">
                      <strong>${escapeHtml(lane.label)}</strong>
                      <span class="pill">${lane.count}</span>
                    </div>
                    <div class="lane-list">
                      ${lane.tasks
                        .map(
                          (task) => `
                            <article class="lane-task">
                              <strong>${escapeHtml(task.title)}</strong>
                              <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                            </article>`
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
              <p class="muted">Synthese des signaux critiques deja structures par le cockpit view.</p>
            </div>
          </div>
          <div class="rail-list">
            ${
              attentionItems.length === 0
                ? '<p class="muted">Aucun signal critique pour ce scenario.</p>'
                : attentionItems
                    .map(
                      (item) => `
                        <article class="attention-card tone-${escapeHtml(item.tone)}">
                          <div class="row-spread">
                            <strong>${escapeHtml(item.label)}</strong>
                            <span class="pill tone-${escapeHtml(item.tone)}">${escapeHtml(item.severity)}</span>
                          </div>
                          <p class="muted">${escapeHtml(item.detail)}</p>
                        </article>`
                    )
                    .join('')
            }
          </div>
        </section>

        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Fleet and ownership</h2>
              <p class="muted">Noeuds et leases actifs detectes dans le run.</p>
            </div>
          </div>
          <div class="card-stack">
            ${cockpitView.fleet
              .map(
                (node) => `
                  <article class="subcard">
                    <div class="row-spread">
                      <strong>${escapeHtml(node.nodeId)}</strong>
                      <span class="pill tone-${escapeHtml(node.tone)}">${escapeHtml(node.status)}</span>
                    </div>
                    <p class="muted">${node.workerCount} worker(s) · ${node.activeLeaseCount} active lease(s)</p>
                  </article>`
              )
              .join('')}
            ${cockpitView.ownership
              .slice(0, 4)
              .map(
                (ownership) => `
                  <article class="subcard">
                    <div class="row-spread">
                      <strong>${escapeHtml(ownership.taskTitle ?? ownership.taskId)}</strong>
                      <span class="pill tone-${escapeHtml(ownership.tone)}">${escapeHtml(ownership.ownershipStatus)}</span>
                    </div>
                    <p class="muted">${escapeHtml(ownership.detail)}</p>
                  </article>`
              )
              .join('')}
          </div>
        </section>

        <section class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Timeline</h2>
              <p class="muted">Walkthrough scenario plus timeline runtime.</p>
            </div>
          </div>
          <div class="timeline-list">
            ${scenario.walkthrough
              .map(
                (step, index) => `
                  <article class="timeline-card">
                    <span class="timeline-index">${index + 1}</span>
                    <p>${escapeHtml(step)}</p>
                  </article>`
              )
              .join('')}
            ${timelinePoints
              .map(
                (point) => `
                  <article class="timeline-card tone-${point.level === 'error' ? 'critical' : point.level === 'warning' ? 'warning' : 'neutral'}">
                    <span class="timeline-index">#${point.sequenceId}</span>
                    <p>${escapeHtml(point.title)}</p>
                  </article>`
              )
              .join('')}
          </div>
        </section>
      </aside>
    </section>`;
}

interface CollapsibleSectionOptions {
  id: string;
  label: string;
  count?: number;
  accent?: 'neutral' | 'warning' | 'critical' | 'positive' | 'attention';
  rightHint?: string;
  body: string;
  domId?: string;
}

function renderCollapsibleSection(opts: CollapsibleSectionOptions): string {
  const isCollapsed = appState.collapsedSections.has(opts.id);
  const tone = opts.accent ?? 'neutral';
  const domAttr = opts.domId ? ` id="${opts.domId}"` : '';
  const countMarkup =
    typeof opts.count === 'number'
      ? `<span class="collapsible__count">${opts.count}</span>`
      : '';
  const hintMarkup = opts.rightHint
    ? `<span class="collapsible__hint">${escapeHtml(opts.rightHint)}</span>`
    : '';
  return `
    <section${domAttr} class="collapsible tone-${tone} ${isCollapsed ? 'is-collapsed' : 'is-expanded'}" data-collapsible-id="${escapeHtml(opts.id)}">
      <button type="button" class="collapsible__header" data-collapsible-toggle="${escapeHtml(opts.id)}" aria-expanded="${isCollapsed ? 'false' : 'true'}">
        <svg class="collapsible__caret" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 2L7 5L3 8"/></svg>
        <span class="collapsible__dot" aria-hidden="true"></span>
        <span class="collapsible__label">${escapeHtml(opts.label)}</span>
        ${countMarkup}
        <span class="collapsible__spacer"></span>
        ${hintMarkup}
      </button>
      <div class="collapsible__body">${opts.body}</div>
    </section>`;
}

function renderMissionBoardMode(_scenario: RuntimeViewsScenario): string {
  // Grimoire: Mission Board replaced by embedded Switchboard-style Kanban.
  // The UI is served as a standalone HTML at /switchboard/kanban.html with
  // mock data injected at load. Extension-level dispatch is intentionally stubbed.
  return `<section class="mission-board-shell mission-board-shell--switchboard" aria-label="Switchboard Kanban">
    <iframe
      class="switchboard-frame"
      src="./switchboard/kanban.html"
      title="Switchboard Kanban"
      loading="lazy"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
    ></iframe>
  </section>`;
}

/*
function renderGameUiMode(scenario: RuntimeViewsScenario): string {
  const gameUiView = scenario.webViews.gameUiView;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(gameUiView.header.title)}</h2>
            <p class="muted">${escapeHtml(gameUiView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(gameUiView.header.tone)}">${escapeHtml(gameUiView.header.summary)}</span>
        </div>
        <div class="summary-grid compact-grid">
          ${gameUiView.statCards
            .map(
              (card) => `
                <article class="metric-card tone-${escapeHtml(card.tone)}">
                  <span>${escapeHtml(card.label)}</span>
                  <strong>${card.value}</strong>
                  <small>${escapeHtml(card.hint)}</small>
                </article>`
            )
            .join('')}
        </div>
      </section>

      <section class="content-grid observer-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Rooms HUD</h2>
                <p class="muted">Vue board-first des rooms, des leads et des noeuds actifs visibles depuis le meme run.</p>
              </div>
            </div>
            <div class="observer-room-grid">
              ${gameUiView.rooms
                .map(
                  (room) => `
                    <article class="subcard${room.focus ? ' is-focus' : ''}">
                      <div class="row-spread">
                        <strong>${escapeHtml(room.roomId)}</strong>
                        <span class="pill tone-${escapeHtml(room.tone)}">${room.alertCount} alert(s)</span>
                      </div>
                      <p class="muted">lead ${escapeHtml(room.leadAgentName ?? room.leadAgentId ?? 'none')}</p>
                      <div class="chip-row">
                        ${createPillList([
                          `${room.agentCount} agent(s)`,
                          `${room.activeTaskCount} active task(s)`,
                          `${room.nodeCount} node(s)`
                        ])}
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
                <h2>Task and verification lanes</h2>
                <p class="muted">Le HUD rejoue les lanes de taches et de verification sans creer de modele concurrent cote UI.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${gameUiView.taskLanes
                .map(
                  (lane) => `
                    <article class="lane tone-${escapeHtml(lane.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="lane-list">
                        ${lane.tasks
                          .map(
                            (task) => `
                              <article class="lane-task">
                                <strong>${escapeHtml(task.title)}</strong>
                                <p class="muted">${escapeHtml(task.assigneeName ?? task.assigneeId ?? 'unassigned')}</p>
                              </article>`
                          )
                          .join('')}
                      </div>
                    </article>`
                )
                .join('')}
              ${gameUiView.verificationLanes
                .map(
                  (lane) => `
                    <article class="lane tone-${escapeHtml(lane.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(lane.label)}</strong>
                        <span class="pill">${lane.count}</span>
                      </div>
                      <div class="lane-list">
                        ${lane.items
                          .map(
                            (item) => `
                              <article class="lane-task">
                                <strong>${escapeHtml(item.title)}</strong>
                                <p class="muted">${escapeHtml(item.verificationRef ?? item.detail)}</p>
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
                <h2>Decision cards</h2>
                <p class="muted">La couche game UI expose les cartes de decision structurees plutot que de les laisser enfouies dans le board brut.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${gameUiView.decisionCards.length === 0
                ? '<p class="muted">Aucune decision structuree pour ce scenario.</p>'
                : gameUiView.decisionCards
                    .slice(0, 6)
                    .map(
                      (card) => `
                        <article class="subcard">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill tone-${escapeHtml(card.tone)}">${card.missingFieldCount} missing</span>
                          </div>
                          <p class="muted">${escapeHtml(card.taskTitle ?? card.taskId ?? card.roomId ?? 'unscoped')}</p>
                          <p class="muted">${escapeHtml(card.detail)}</p>
                          <div class="chip-row">${createPillList([`${card.evidenceCount} evidence`, `${card.missingFieldCount} missing field(s)`])}</div>
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
                <h2>Agent roster</h2>
                <p class="muted">Etat, room et dernier outil des agents visibles sans quitter le HUD.</p>
              </div>
            </div>
            <div class="card-stack">
              ${gameUiView.agents
                .map(
                  (agent) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(agent.name)}</strong>
                        <span class="pill tone-${escapeHtml(agent.tone)}">${escapeHtml(agent.status)}</span>
                      </div>
                      <p class="muted">${escapeHtml(agent.role)} · room ${escapeHtml(agent.roomId)}</p>
                      <div class="chip-row">${createPillList([`${agent.activeTaskCount} active task(s)`, `${agent.childAgentCount} child agent(s)`])}</div>
                      <p class="muted">last tool ${escapeHtml(agent.lastTool ?? 'none')}</p>
                    </article>`
                )
                .join('')}
            </div>
          </section>

          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Board alerts</h2>
                <p class="muted">Alerte board et signaux d attention restent visibles dans la meme surface operateur.</p>
              </div>
            </div>
            <div class="rail-list">
              ${gameUiView.alerts.length === 0
                ? '<p class="muted">Aucune alerte board pour ce scenario.</p>'
                : gameUiView.alerts
                    .slice(0, 6)
                    .map(
                      (alert) => `
                        <article class="attention-card tone-${escapeHtml(alert.tone)}">
                          <div class="row-spread">
                            <strong>${escapeHtml(alert.code)}</strong>
                            <span class="pill tone-${escapeHtml(alert.tone)}">${escapeHtml(alert.level)}</span>
                          </div>
                          <p class="muted">${escapeHtml(alert.message)}</p>
                        </article>`
                    )
                    .join('')}
              ${gameUiView.attention
                .slice(0, 4)
                .map(
                  (item) => `
                    <article class="attention-card tone-${escapeHtml(item.tone)}">
                      <div class="row-spread">
                        <strong>${escapeHtml(item.label)}</strong>
                        <span class="pill tone-${escapeHtml(item.tone)}">${escapeHtml(item.severity)}</span>
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
                <h2>Security rail</h2>
                <p class="muted">Les cartes security du board ne restent plus implicites: elles font partie du HUD principal.</p>
              </div>
            </div>
            <div class="card-stack">
              ${gameUiView.securityCards.length === 0
                ? '<p class="muted">Aucune carte security pour ce scenario.</p>'
                : gameUiView.securityCards
                    .slice(0, 6)
                    .map(
                      (card) => `
                        <article class="subcard">
                          <div class="row-spread">
                            <strong>${escapeHtml(card.title)}</strong>
                            <span class="pill tone-${escapeHtml(card.tone)}">${escapeHtml(card.severity)}</span>
                          </div>
                          <p class="muted">${escapeHtml(card.surfaceId)} · ${card.blocksShip ? 'ship blocked' : 'non-blocking'}</p>
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

*/

function renderGameUiMode(_scenario: RuntimeViewsScenario, _observatorySource: ObservatoryManifestSource | null): string {
  // Pixel Agents integration (cf. public/pixel-agents/). Fork adapté de pablodelucca/pixel-agents.
  // Les agents Grimoire sont dispatchés via grimoire-pixel-shim.js (13 characters).
  return `
    <section class="game-ui-shell game-ui-shell--pixel-agents">
      <iframe class="pixel-agents-frame" src="./pixel-agents/index.html" title="Grimoire Pixel Office" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
    </section>`;
}

function renderKernelMode(scenario: RuntimeViewsScenario): string {
  const kernelView = scenario.webViews.kernelView;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(kernelView.header.title)}</h2>
            <p class="muted">${escapeHtml(kernelView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(kernelView.header.tone)}">${escapeHtml(kernelView.header.projectId ?? 'project unavailable')}</span>
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
        <div class="chip-row chip-row-spaced">
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
            <div class="card-grid triple-grid">
              ${kernelView.triad
                .map(
                  (panel) => `
                    <article class="card stack">
                      <div class="row-spread">
                        <div>
                          <h3>${escapeHtml(panel.title)}</h3>
                          <p class="muted">${escapeHtml(panel.subtitle)}</p>
                        </div>
                        <span class="pill tone-${escapeHtml(panel.tone)}">${panel.items.length}</span>
                      </div>
                      <div class="card-stack panel-block-tight">
                        ${panel.items
                          .map(
                            (item) => `
                              <article class="subcard ${item.focus ? 'is-focus' : ''} tone-${escapeHtml(item.tone)}">
                                <div class="row-spread">
                                  <strong>${escapeHtml(item.title)}</strong>
                                  <span class="pill">${escapeHtml(item.subtitle)}</span>
                                </div>
                                <ul class="bullet-list">
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
            <div class="timeline-list">
              ${kernelView.causality
                .map(
                  (step, index) => `
                    <article class="timeline-card tone-${escapeHtml(step.tone)}">
                      <span class="timeline-index">${index + 1}</span>
                      <div class="row-spread">
                        <strong>${escapeHtml(step.label)}</strong>
                        <span class="pill">${escapeHtml(step.value)}</span>
                      </div>
                      <p>${escapeHtml(step.detail)}</p>
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
                        <span class="pill tone-${escapeHtml(contract.tone)}">${escapeHtml(contract.version ?? 'linked')}</span>
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
                <p class="muted">Cette vue ne cherche pas l exhaustivite. Elle rend visibles les invariants qui conditionnent la confiance operatoire.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${kernelView.invariants
                .map(
                  (invariant) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(invariant.label)}</strong>
                        <span class="pill tone-${escapeHtml(invariant.tone)}">${escapeHtml(invariant.status)}</span>
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

function renderProofDossierMode(scenario: RuntimeViewsScenario, proofSource: ProofManifestSource | null): string {
  const dossierView = scenario.webViews.proofDossierView;
  const availableSourceCount = proofManifest?.sources.filter((source) => source.available).length ?? 0;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(dossierView.header.title)}</h2>
            <p class="muted">${escapeHtml(dossierView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(dossierView.header.tone)}">${dossierView.header.releaseBlocked ? 'NO-GO' : 'GO'}</span>
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
        <div class="chip-row chip-row-spaced">
          ${createPillList([
            `run ${proofManifest?.latestRunId ?? dossierView.header.runId ?? 'indisponible'}`,
            `${availableSourceCount} artefact(s) relies`,
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
                <p class="muted">Le verdict reste explicite avant l exploration détaillée des preuves et artefacts relies.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${dossierView.gates
                .map(
                  (gate) => `
                    <article class="card">
                      <div class="row-spread">
                        <strong>${escapeHtml(gate.label)}</strong>
                        <span class="pill tone-${escapeHtml(gate.tone)}">${escapeHtml(String(gate.value))}</span>
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
                : `<ul class="bullet-list">${dossierView.blockingReasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`}
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
                            <span class="pill tone-${escapeHtml(pack.tone)}">${escapeHtml(pack.verdict)}</span>
                          </div>
                          <p class="muted">${escapeHtml(pack.verificationRef)} · ${escapeHtml(pack.status)}</p>
                          <div class="chip-row">
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
                <p class="muted">Le shell embarque le run de preuve et ses decisions, avec une navigation courte orientee verdict.</p>
              </div>
              <span class="pill">${escapeHtml(proofManifest?.generatedAt ?? 'manifest pending')}</span>
            </div>
            <div class="button-row button-row-wrap">
              ${
                proofManifest === null
                  ? '<span class="muted">Manifest en chargement...</span>'
                  : proofManifest.sources
                      .map(
                        (source) => `
                          <button type="button" class="control-button ${source.id === proofSource?.id ? 'is-active' : ''}" data-proof-id="${escapeHtml(source.id)}">
                            ${escapeHtml(source.label)}
                          </button>`
                      )
                      .join('')
              }
            </div>
            <div class="source-grid">
              ${
                proofManifest === null
                  ? ''
                  : proofManifest.sources
                      .map(
                        (source) => `
                          <article class="source-card ${source.id === proofSource?.id ? 'is-selected' : ''}" data-available="${source.available}">
                            <div class="row-spread">
                              <strong>${escapeHtml(source.label)}</strong>
                              <span class="pill tone-${source.available ? 'positive' : 'warning'}">${escapeHtml(source.kind)}</span>
                            </div>
                            <p class="muted">${escapeHtml(source.description)}</p>
                            <div class="chip-row">
                              ${createPillList([source.format, source.emphasis, source.runId ?? 'hors run'])}
                            </div>
                          </article>`
                      )
                      .join('')
              }
            </div>
            ${proofError === null ? '' : `<p class="callout warning">${escapeHtml(proofError)}</p>`}
          </section>

          <section class="panel panel-soft iframe-panel">
            <div class="section-head">
              <div>
                <h2>${escapeHtml(proofSource?.label ?? 'Apercu du dossier')}</h2>
                <p class="muted">
                  ${
                    proofSource?.available === true
                      ? canPreviewProofSource(proofSource)
                        ? 'Preview integre du document ou artefact selectionne.'
                        : 'Artefact disponible au telechargement mais non previsualisable dans cette surface.'
                      : 'Aucun artefact disponible pour cette entree dans ce workspace.'
                  }
                </p>
              </div>
              ${
                proofSource?.available === true && proofSource.browserPath !== null
                  ? `<a class="link-button" href="${escapeHtml(proofSource.browserPath)}" target="_blank" rel="noreferrer">Ouvrir seul</a>`
                  : ''
              }
            </div>
            ${
              proofSource?.available === true && proofSource.browserPath !== null
                ? canPreviewProofSource(proofSource)
                  ? `<iframe class="observatory-frame" title="${escapeHtml(proofSource.label)}" src="${escapeHtml(proofSource.browserPath)}"></iframe>`
                  : '<div class="empty-state"><p>Cet artefact est embarque dans le shell, mais son format est pense pour le telechargement.</p></div>'
                : '<div class="empty-state"><p>Le manifest de preuves est pret, mais cette entree n est pas disponible dans le workspace courant.</p></div>'
            }
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObservabilityMode(scenario: RuntimeViewsScenario): string {
  const observabilityView = scenario.webViews.observabilityView;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(observabilityView.header.title)}</h2>
            <p class="muted">${escapeHtml(observabilityView.header.subtitle)}</p>
          </div>
          <span class="pill tone-${escapeHtml(observabilityView.header.tone)}">${escapeHtml(observabilityView.header.summary)}</span>
        </div>
        <div class="summary-grid compact-grid">
          ${observabilityView.metricCards
            .map(
              (card) => `
                <article class="metric-card tone-${escapeHtml(card.status === 'critical' ? 'critical' : card.status === 'warning' ? 'warning' : 'positive')}">
                  <span>${escapeHtml(card.label)}</span>
                  <strong>${card.value}</strong>
                  <small>${escapeHtml(card.hint)}</small>
                </article>`
            )
            .join('')}
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
            <div class="rail-list">
              ${observabilityView.attentionItems.length === 0
                ? '<p class="muted">Aucun signal d attention pour ce scenario.</p>'
                : observabilityView.attentionItems
                    .slice(0, 8)
                    .map(
                      (item) => `
                        <article class="attention-card tone-${escapeHtml(item.severity === 'critical' ? 'critical' : item.severity === 'warning' ? 'warning' : 'neutral')}">
                          <div class="row-spread">
                            <strong>${escapeHtml(item.label)}</strong>
                            <span class="pill tone-${escapeHtml(item.severity === 'critical' ? 'critical' : item.severity === 'warning' ? 'warning' : 'neutral')}">${escapeHtml(item.severity)}</span>
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
            <div class="timeline-list">
              ${observabilityView.timelineRows
                .slice(0, 10)
                .map(
                  (row) => `
                    <article class="timeline-card tone-${row.level === 'error' ? 'critical' : row.level === 'warning' ? 'warning' : 'neutral'}">
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
                ? '<p class="muted">Aucun blocker de verification pour ce scenario.</p>'
                : observabilityView.blockedTasks
                    .slice(0, 6)
                    .map(
                      (task) => `
                        <article class="subcard">
                          <div class="row-spread">
                            <strong>${escapeHtml(task.title)}</strong>
                            <span class="pill tone-${escapeHtml(task.tone)}">${escapeHtml(task.status)}</span>
                          </div>
                          <div class="chip-row">${createPillList([`${task.unmetRequirementCount} unmet`, `${task.evidenceCount} evidence`, `${task.traceCount} trace(s)`])}</div>
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
            <div class="card-stack">
              ${observabilityView.connectionIssues.length === 0
                ? '<p class="muted">Aucun incident de connexion detecte.</p>'
                : observabilityView.connectionIssues
                    .map(
                      (issue) => `
                        <article class="subcard">
                          <div class="row-spread">
                            <strong>${escapeHtml(issue.agentName ?? issue.agentId)}</strong>
                            <span class="pill tone-${escapeHtml(issue.tone)}">${escapeHtml(issue.status)}</span>
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
            <div class="card-stack">
              ${observabilityView.securityHotspots
                .slice(0, 4)
                .map(
                  (hotspot) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(hotspot.label)}</strong>
                        <span class="pill tone-${escapeHtml(hotspot.tone)}">security</span>
                      </div>
                      <p class="muted">${escapeHtml(hotspot.detail)}</p>
                    </article>`
                )
                .join('')}
              ${observabilityView.collaborationHotspots
                .slice(0, 4)
                .map(
                  (hotspot) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(hotspot.label)}</strong>
                        <span class="pill tone-${escapeHtml(hotspot.tone)}">collab</span>
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
            <div class="card-stack">
              ${observabilityView.sessions
                .slice(0, 6)
                .map(
                  (session) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(session.title)}</strong>
                        <span class="pill tone-${escapeHtml(session.tone)}">${escapeHtml(session.status)}</span>
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

      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Ledger runtime — hooks</h2>
            <p class="muted">Snapshot publie par le prepare step (V1.5b), rafraichi toutes les 2s dans la surface observability.</p>
          </div>
        </div>
        <div id="hook-events-ledger" class="hook-events-panel">
          <div class="muted">Chargement du ledger runtime&hellip;</div>
        </div>
      </section>
    </section>`;
}

function renderSpectatorMode(scenario: RuntimeViewsScenario): string {
  const spectatorView = scenario.webViews.spectatorView;
  const shareUrl = createShareUrl(scenario.spectatorShare.shareQuery);
  const requestedTokenMismatch =
    appState.requestedSpectatorToken !== null && appState.requestedSpectatorToken !== scenario.spectatorShare.tokenId;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(spectatorView.banner.title)}</h2>
            <p class="muted">${escapeHtml(spectatorView.banner.detail)}</p>
          </div>
          <span class="pill tone-${escapeHtml(spectatorView.banner.tone)}">${spectatorView.banner.readOnly ? 'read-only' : 'mutable'}</span>
        </div>
        <div class="summary-grid">
          ${createMetricGrid([
            {
              label: 'Principal',
              value: spectatorView.banner.principalId,
              note: spectatorView.banner.role
            },
            {
              label: 'Blocked writes',
              value: spectatorView.blockedMutations.length,
              note: 'runtime writes denied'
            },
            {
              label: 'Audit entries',
              value: spectatorView.auditTrail.length,
              note: 'forbidden actions traced'
            },
            {
              label: 'Channels',
              value: spectatorView.channels.length,
              note: spectatorView.channels.every((channel) => channel.reconnectable) ? 'reconnectable' : 'partial'
            }
          ])}
        </div>
        <div class="button-row button-row-wrap panel-block-tight">
          <a class="link-button" href="${escapeHtml(shareUrl)}">Ouvrir en spectateur</a>
          <button type="button" class="control-button" data-copy-text="${escapeHtml(shareUrl)}" data-default-label="Copier le lien">Copier le lien</button>
          <button type="button" class="control-button" data-copy-text="${escapeHtml(scenario.spectatorShare.tokenId)}" data-default-label="Copier le token">Copier le token</button>
        </div>
        ${requestedTokenMismatch ? '<p class="callout warning">Le token fourni dans l URL ne correspond pas au scenario actif. Le shell reste en lecture seule, mais ce lien n ouvre pas la meme session spectateur.</p>' : ''}
      </section>

      <section class="content-grid">
        <div class="stack">
          <section class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Shared channels</h2>
                <p class="muted">Le navigateur et VS Code lisent la meme causalite sans ouvrir de surface d ecriture.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.channels
                .map(
                  (channel) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(channel.channel)}</strong>
                        <span class="pill tone-${channel.readOnly ? 'warning' : 'positive'}">${channel.readOnly ? 'read-only' : 'write enabled'}</span>
                      </div>
                      <div class="chip-row">
                        ${createPillList([
                          channel.reconnectable ? 'reconnectable' : 'fixed session',
                          channel.focusNavigation ? 'focus navigation' : 'focus locked',
                          `${channel.writeSurfaceCount} write surface(s)`
                        ])}
                      </div>
                      <ul class="bullet-list">
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
                <p class="muted">Les writes restent bloques, mais la navigation de focus conserve la lecture locale autorisee.</p>
              </div>
            </div>
            <div class="card-grid dual-grid">
              ${spectatorView.blockedMutations
                .map(
                  (capability) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(capability.label)}</strong>
                        <span class="pill tone-critical">blocked</span>
                      </div>
                      <p class="muted">${escapeHtml(capability.source)} · ${capability.mutation ? 'mutation' : 'read-only'}</p>
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
            <div class="card-stack">
              ${spectatorView.capabilities
                .map(
                  (capability) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(capability.label)}</strong>
                        <span class="pill tone-${capability.allowed ? 'positive' : 'critical'}">${capability.allowed ? 'allowed' : 'blocked'}</span>
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
                <p class="muted">Chaque tentative interdite reste explicite et re-jouable cote preuve.</p>
              </div>
            </div>
            <div class="card-stack">
              ${spectatorView.auditTrail
                .map(
                  (entry) => `
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(entry.actionId)}</strong>
                        <span class="pill tone-critical">${escapeHtml(entry.code)}</span>
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
                <p class="muted">Lien partageable borne au scenario et token read-only emis via ${escapeHtml(scenario.spectatorShare.commandId)}.</p>
              </div>
            </div>
            <div class="card-stack">
              <article class="subcard">
                <strong>Token</strong>
                <p class="muted">${escapeHtml(scenario.spectatorShare.tokenId)}</p>
              </article>
              <article class="subcard">
                <strong>Principal</strong>
                <p class="muted">${escapeHtml(scenario.spectatorShare.principalId)}</p>
              </article>
              <article class="subcard">
                <strong>Issued at</strong>
                <p class="muted">${escapeHtml(scenario.spectatorShare.issuedAt)}</p>
              </article>
            </div>
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObserverMode(scenario: RuntimeViewsScenario): string {
  const observerView = scenario.webViews.observerView;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Runtime observer</h2>
            <p class="muted">Projection spatiale du run: rooms, entites, handoffs et verification de parite avec le cockpit.</p>
          </div>
          <span class="pill tone-${observerView.parity.sameTaskCount && observerView.parity.sameAttentionCount ? 'positive' : 'warning'}">
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
                    <article class="subcard${room.focus ? ' is-focus' : ''}">
                      <div class="row-spread">
                        <strong>${escapeHtml(room.label)}</strong>
                        <span class="pill tone-${escapeHtml(room.tone)}">${room.alertCount} alert(s)</span>
                      </div>
                      <div class="chip-row">
                        ${createPillList([
                          `${room.agentIds.length} agent(s)`,
                          `${room.taskIds.length} task(s)`,
                          `${room.nodeIds.length} node(s)`
                        ])}
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
            <div class="card-stack">
              ${
                observerView.handoffs.length === 0
                  ? '<p class="muted">Aucun handoff pour ce scenario.</p>'
                  : observerView.handoffs
                      .map(
                        (handoff) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(handoff.label)}</strong>
                              <span class="pill tone-${escapeHtml(handoff.tone)}">${escapeHtml(handoff.relation)}</span>
                            </div>
                            <p class="muted">${escapeHtml(handoff.fromRoomId)} -> ${escapeHtml(handoff.toRoomId)}</p>
                            <p class="muted">task ${escapeHtml(handoff.taskId ?? 'none')} · ${escapeHtml(handoff.traceIds.join(', ') || 'no trace')}</p>
                          </article>`
                      )
                      .join('')
              }
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
                    <article class="subcard">
                      <div class="row-spread">
                        <strong>${escapeHtml(entity.label)}</strong>
                        <span class="pill tone-${escapeHtml(entity.tone)}">${escapeHtml(entity.kind)}</span>
                      </div>
                      <p class="muted">room ${escapeHtml(entity.roomId)}</p>
                      <div class="chip-row">${createPillList(entity.badges)}</div>
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
            <div class="rail-list">
              ${observerView.warRoomAttention
                .slice(0, 6)
                .map(
                  (item) => `
                    <article class="attention-card tone-${escapeHtml(item.tone)}">
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

function renderWorkflowMode(scenario: RuntimeViewsScenario): string {
  const workflowView = scenario.webViews.workflowView;
  const workflowPaths = workflowView.paths.slice(0, 3);

  return `
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
              <article class="panel panel-soft workflow-panel">
                <div class="section-head">
                  <div>
                    <h2>${escapeHtml(path.taskTitle ?? path.traceId ?? path.id)}</h2>
                    <p class="muted">${escapeHtml(path.roomId ?? 'war-room')} · ${escapeHtml(path.taskStatus ?? 'unknown')}</p>
                  </div>
                  <span class="pill tone-${path.isActive ? 'positive' : 'neutral'}">${path.isActive ? 'active' : 'completed'}</span>
                </div>
                <div class="chip-row chip-row-spaced">
                  ${createPillList(path.contributors.map((contributor) => `${contributor.agentName ?? contributor.agentId} ${contributor.stepCount}/${contributor.decisionCount}`))}
                </div>
                <div class="workflow-columns">
                  <div class="card-stack">
                    <h3>Steps</h3>
                    ${path.steps
                      .map(
                        (step) => `
                          <article class="subcard tone-${step.status === 'active' ? 'positive' : 'neutral'}">
                            <div class="row-spread">
                              <strong>${escapeHtml(step.title)}</strong>
                              <span class="pill">#${step.sequenceId}</span>
                            </div>
                            <p class="muted">${escapeHtml(step.detail)}</p>
                            <p class="muted">${escapeHtml(step.agentName ?? step.agentId ?? 'system')} · ${escapeHtml(step.sourceEventType)}</p>
                          </article>`
                      )
                      .join('')}
                  </div>
                  <div class="card-stack">
                    <h3>Decisions</h3>
                    ${
                      path.decisions.length === 0
                        ? '<p class="muted">Aucune decision rattachee.</p>'
                        : path.decisions
                            .map(
                              (decision) => `
                                <article class="subcard">
                                  <div class="row-spread">
                                    <strong>${escapeHtml(decision.title)}</strong>
                                    <span class="pill">${decision.evidenceCount} evidence</span>
                                  </div>
                                  <p class="muted">${escapeHtml(decision.detail)}</p>
                                  <p class="muted">${escapeHtml(decision.agentName ?? decision.agentId ?? 'system')} · ${escapeHtml(decision.sourceEventType)}</p>
                                </article>`
                            )
                            .join('')
                    }
                  </div>
                  <div class="card-stack">
                    <h3>Audit</h3>
                    ${
                      path.auditTrail.length === 0
                        ? '<p class="muted">Aucun audit trail disponible.</p>'
                        : path.auditTrail
                            .map(
                              (entry) => `
                                <article class="subcard">
                                  <div class="row-spread">
                                    <strong>${escapeHtml(entry.title)}</strong>
                                    <span class="pill">${escapeHtml(entry.kind)}</span>
                                  </div>
                                  <p class="muted">${escapeHtml(entry.detail)}</p>
                                  <p class="muted">#${entry.sequenceId} · ${escapeHtml(entry.sourceEventType)}</p>
                                </article>`
                            )
                            .join('')
                    }
                  </div>
                </div>
              </article>`
          )
          .join('')}
      </section>
    </section>`;
}

function renderExpertMode(scenario: RuntimeViewsScenario): string {
  const expertView = scenario.webViews.expertView;
  const inspection = expertView.inspection;

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Expert cockpit</h2>
            <p class="muted">Point d entree pour la revue experte: decisions host, preuve, replay et deep inspection d agent.</p>
          </div>
          <span class="pill tone-${expertView.status === 'accepted' ? 'positive' : expertView.status === 'refused' ? 'critical' : 'warning'}">
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
            <div class="card-stack">
              ${
                expertView.decisions.length === 0
                  ? '<p class="muted">Aucune decision host importee.</p>'
                  : expertView.decisions
                      .map(
                        (decision) => `
                          <article class="subcard tone-${escapeHtml(decision.tone)}">
                            <div class="row-spread">
                              <strong>${escapeHtml(decision.actionKind)}</strong>
                              <span class="pill">${escapeHtml(decision.decision)}</span>
                            </div>
                            <p class="muted">${escapeHtml(decision.reason)}</p>
                            <p class="muted">${escapeHtml(decision.mode)} · scopes ${escapeHtml(decision.requiredScopes.join(', ') || 'none')}</p>
                          </article>`
                      )
                      .join('')
              }
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
            <div class="card-grid dual-grid expert-detail-grid">
              <article class="card-stack">
                <h3>Workflow summary</h3>
                ${expertView.workflow.recentSteps
                  .map(
                    (step) => `
                      <article class="subcard">
                        <div class="row-spread">
                          <strong>${escapeHtml(step.title)}</strong>
                          <span class="pill">#${step.sequenceId}</span>
                        </div>
                        <p class="muted">${escapeHtml(step.detail)}</p>
                      </article>`
                  )
                  .join('')}
              </article>
              <article class="card-stack">
                <h3>Replay facets</h3>
                ${createMetricGrid([
                  { label: 'Canonical envelopes', value: expertView.replay.canonicalEnvelopeCount },
                  { label: 'Message kinds', value: expertView.replay.messageTypes.length },
                  { label: 'Current step', value: expertView.workflow.currentStep ?? 'none' },
                  { label: 'Decision count', value: expertView.workflow.decisionCount }
                ])}
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
            ${
              inspection === null
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
                  <div class="card-stack panel-block-tight">
                    ${inspection.actions
                      .map(
                        (action) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(action.label)}</strong>
                              <span class="pill tone-${action.allowed ? 'positive' : 'warning'}">${action.allowed ? 'allowed' : 'blocked'}</span>
                            </div>
                            <p class="muted">${escapeHtml(action.reason ?? 'No restriction')}</p>
                          </article>`
                      )
                      .join('')}
                    ${inspection.toolHistory
                      .slice(0, 5)
                      .map(
                        (entry) => `
                          <article class="subcard">
                            <div class="row-spread">
                              <strong>${escapeHtml(entry.tool)}</strong>
                              <span class="pill">#${entry.sequenceId}</span>
                            </div>
                            <p class="muted">${escapeHtml(entry.summary)}</p>
                          </article>`
                      )
                      .join('')}
                  </div>`
            }
          </section>
        </aside>
      </section>
    </section>`;
}

function renderObservatoryMode(observatorySource: ObservatoryManifestSource | null): string {
  return `
    <section class="content-grid observatory-grid">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Observatory handoff</h2>
            <p class="muted">La surface app garde l observatory en lecture seule et l expose via les assets prepares localement.</p>
          </div>
          <span class="pill">${escapeHtml(observatoryManifest?.generatedAt ?? 'manifest pending')}</span>
        </div>
        <div class="button-row button-row-wrap">
          ${
            observatoryManifest === null
              ? '<span class="muted">Manifest en chargement...</span>'
              : observatoryManifest.sources
                  .map(
                    (source) => `
                      <button type="button" class="control-button ${source.id === observatorySource?.id ? 'is-active' : ''}" data-observatory-id="${escapeHtml(source.id)}">
                        ${escapeHtml(source.label)}
                      </button>`
                  )
                  .join('')
          }
        </div>
        <div class="source-grid">
          ${
            observatoryManifest === null
              ? ''
              : observatoryManifest.sources
                  .map(
                    (source) => `
                      <article class="source-card ${source.id === observatorySource?.id ? 'is-selected' : ''}">
                        <div class="row-spread">
                          <strong>${escapeHtml(source.label)}</strong>
                          <span class="pill tone-${source.available ? 'positive' : 'warning'}">${source.available ? 'ready' : 'missing'}</span>
                        </div>
                        <p class="muted">scope ${escapeHtml(source.scope)}</p>
                      </article>`
                  )
                  .join('')
          }
        </div>
        ${observatoryError === null ? '' : `<p class="callout warning">${escapeHtml(observatoryError)}</p>`}
      </section>

      <section class="panel panel-soft iframe-panel">
        <div class="section-head">
          <div>
            <h2>${escapeHtml(observatorySource?.label ?? 'Observatory')}</h2>
            <p class="muted">
              ${
                observatorySource?.available === true
                  ? 'L iframe charge la copie preparee pour la surface locale.'
                  : 'Aucune source observatory disponible dans ce workspace pour le moment.'
              }
            </p>
          </div>
          ${
            observatorySource?.available === true && observatorySource.browserPath !== null
              ? `<a class="link-button" href="${escapeHtml(observatorySource.browserPath)}" target="_blank" rel="noreferrer">Ouvrir seul</a>`
              : ''
          }
        </div>
        ${
          observatorySource?.available === true && observatorySource.browserPath !== null
            ? `<iframe class="observatory-frame" title="${escapeHtml(observatorySource.label)}" src="${escapeHtml(observatorySource.browserPath)}"></iframe>`
            : '<div class="empty-state"><p>Genere ou mets a disposition un observatory.html dans les sorties runtime pour activer cet embed.</p></div>'
        }
      </section>
    </section>`;
}

function renderWarRoomMode(scenario: RuntimeViewsScenario): string {
  const warRoomZones = createWarRoomZones(scenario);
  const attentionItems = scenario.webViews.observerView.warRoomAttention.slice(0, 6);

  return `
    <section class="stack">
      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>War room</h2>
            <p class="muted">Lecture tactique du meme scenario, reformulee en zones de decision et de friction.</p>
          </div>
          <span class="pill badge badge-${escapeHtml(scenario.outcome)}">${escapeHtml(scenario.outcome)}</span>
        </div>
        <div class="war-grid">
          ${warRoomZones
            .map(
              (zone) => `
                <article class="war-card tone-${zone.tone === 'blocked' ? 'critical' : zone.tone === 'attention' ? 'warning' : 'positive'}">
                  <div class="row-spread">
                    <div>
                      <h3>${escapeHtml(zone.title)}</h3>
                      <p class="muted">${escapeHtml(zone.subtitle)}</p>
                    </div>
                  </div>
                  <div class="chip-row">
                    ${zone.pills.map((pill) => `<span class="chip">${escapeHtml(pill)}</span>`).join('')}
                  </div>
                  <ul class="bullet-list">
                    ${zone.bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}
                  </ul>
                </article>`
            )
            .join('')}
        </div>
      </section>

      <section class="panel panel-soft">
        <div class="section-head">
          <div>
            <h2>Attention to brief</h2>
            <p class="muted">Ce rail combine les alertes du dashboard avec les blocages fonctionnels du scenario.</p>
          </div>
        </div>
        <div class="brief-grid">
          ${attentionItems
            .map(
              (item) => `
                <article class="attention-card tone-${escapeHtml(item.tone)}">
                  <strong>${escapeHtml(item.label)}</strong>
                  <p class="muted">${escapeHtml(item.detail)}</p>
                </article>`
            )
            .join('')}
        </div>
      </section>
    </section>`;
}

function renderScenarioPopover(
  scenarios: readonly RuntimeViewsScenario[],
  currentId: string,
  isOpen: boolean
): string {
  if (!isOpen) return '';
  return `
    <div class="cp-popover-backdrop" data-action="close-popover" aria-hidden="true"></div>
    <div class="cp-popover cp-popover--scenario" role="dialog" aria-label="Changer de sc\u00e9nario" data-popover-anchor="scenario">
      <div class="cp-popover__header">
        <span class="cp-popover__title">Sc\u00e9nario</span>
        <span class="cp-popover__hint">${scenarios.length} disponibles</span>
      </div>
      <ul class="cp-popover__list" role="listbox">
        ${scenarios
          .map(
            (sc) => `
              <li>
                <button type="button" class="cp-popover__item${sc.id === currentId ? ' is-current' : ''}" data-scenario-id="${escapeHtml(sc.id)}" role="option" aria-selected="${sc.id === currentId ? 'true' : 'false'}">
                  <span class="cp-popover__item-dot tone-${escapeHtml(sc.outcome)}" aria-hidden="true"></span>
                  <span class="cp-popover__item-body">
                    <span class="cp-popover__item-title">${escapeHtml(sc.title)}</span>
                    <span class="cp-popover__item-sub">${escapeHtml(sc.description)}</span>
                  </span>
                  ${sc.id === currentId ? '<svg class="cp-popover__item-check" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6L5 8.5L9.5 3.5"/></svg>' : ''}
                </button>
              </li>`
          )
          .join('')}
      </ul>
    </div>`;
}

function renderFilterPopover(
  currentFilter: 'all' | 'attention' | 'blocked',
  isOpen: boolean
): string {
  if (!isOpen) return '';
  const options: Array<{ id: 'all' | 'attention' | 'blocked'; label: string; hint: string; tone: string }> = [
    { id: 'all', label: 'Tout', hint: 'Aucun filtre de statut', tone: 'neutral' },
    { id: 'attention', label: 'Attention', hint: 'V\u00e9rification \u00e0 reprendre', tone: 'warning' },
    { id: 'blocked', label: 'Bloqu\u00e9s', hint: 'Transitions bloqu\u00e9es uniquement', tone: 'critical' }
  ];
  return `
    <div class="cp-popover-backdrop" data-action="close-popover" aria-hidden="true"></div>
    <div class="cp-popover cp-popover--filter" role="dialog" aria-label="Filtre" data-popover-anchor="filter">
      <div class="cp-popover__header">
        <span class="cp-popover__title">Filtre statut</span>
      </div>
      <ul class="cp-popover__list" role="listbox">
        ${options
          .map(
            (opt) => `
              <li>
                <button type="button" class="cp-popover__item${opt.id === currentFilter ? ' is-current' : ''}" data-filter-id="${opt.id}" role="option" aria-selected="${opt.id === currentFilter ? 'true' : 'false'}">
                  <span class="cp-popover__item-dot tone-${opt.tone}" aria-hidden="true"></span>
                  <span class="cp-popover__item-body">
                    <span class="cp-popover__item-title">${escapeHtml(opt.label)}</span>
                    <span class="cp-popover__item-sub">${escapeHtml(opt.hint)}</span>
                  </span>
                  ${opt.id === currentFilter ? '<svg class="cp-popover__item-check" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6L5 8.5L9.5 3.5"/></svg>' : ''}
                </button>
              </li>`
          )
          .join('')}
      </ul>
    </div>`;
}

function renderDisplayPopover(collapsed: Set<string>, isOpen: boolean): string {
  if (!isOpen) return '';
  const sections: Array<{ id: string; label: string }> = [
    { id: 'branch-finisher', label: 'Branch Finisher' },
    { id: 'watchtower', label: 'Watchtower' },
    { id: 'workshop', label: 'Workshop' },
    { id: 'intake-desk', label: 'Intake Desk' },
    { id: 'seance-archive', label: 'Seance Archive' },
    { id: 'timeline', label: 'Preuves r\u00e9centes' }
  ];
  return `
    <div class="cp-popover-backdrop" data-action="close-popover" aria-hidden="true"></div>
    <div class="cp-popover cp-popover--display" role="dialog" aria-label="Affichage" data-popover-anchor="display">
      <div class="cp-popover__header">
        <span class="cp-popover__title">Affichage</span>
        <div class="cp-popover__actions">
          <button type="button" class="cp-popover__action" data-action="display-expand-all">Tout ouvrir</button>
          <button type="button" class="cp-popover__action" data-action="display-collapse-all">Tout fermer</button>
        </div>
      </div>
      <ul class="cp-popover__list cp-popover__list--checkboxes">
        ${sections
          .map((sec) => {
            const isVisible = !collapsed.has(sec.id);
            return `
              <li>
                <button type="button" class="cp-popover__item cp-popover__item--toggle" data-collapsible-toggle="${escapeHtml(sec.id)}" aria-pressed="${isVisible ? 'true' : 'false'}">
                  <span class="cp-checkbox${isVisible ? ' is-checked' : ''}" aria-hidden="true">
                    ${isVisible ? '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M2 5L4 7L8 3"/></svg>' : ''}
                  </span>
                  <span class="cp-popover__item-title">${escapeHtml(sec.label)}</span>
                </button>
              </li>`;
          })
          .join('')}
      </ul>
    </div>`;
}

function renderCommandPalette(
  scenarios: readonly RuntimeViewsScenario[],
  isOpen: boolean
): string {
  if (!isOpen) return '';
  const query = appState.commandPaletteQuery.trim().toLowerCase();
  const match = (txt: string): boolean => query === '' || txt.toLowerCase().includes(query);

  const navItems = [
    ['cockpit', 'Cockpit', 'Accueil op\u00e9ratoire'],
    ['mission-board', 'Missions', 'Board des missions'],
    ['kernel', 'Noyau', 'Trust kernel et d\u00e9cisions'],
    ['proofs', 'Preuves', 'Dossier de preuves'],
    ['observability', 'Observabilit\u00e9', 'Pulses et signaux'],
    ['game-ui', 'Jeu', 'Interface game UI'],
    ['observatory', 'Observatoire', 'Vue d ensemble'],
    ['spectator', 'Spectateur', 'Vue publique'],
    ['war-room', 'War Room', 'Coordination live']
  ] as const;

  const filteredNav = navItems.filter(([, label, hint]) => match(`${label} ${hint}`));
  const filteredScenarios = scenarios.filter((sc) => match(`${sc.title} ${sc.description}`));

  const actions = [
    { id: 'toggle-inspector', label: 'Basculer l inspecteur', hint: 'Ctrl J', icon: 'inspector' },
    { id: 'display-expand-all', label: 'Tout ouvrir', hint: 'Sections', icon: 'expand' },
    { id: 'display-collapse-all', label: 'Tout fermer', hint: 'Sections', icon: 'collapse' }
  ];
  const filteredActions = actions.filter((a) => match(`${a.label} ${a.hint}`));

  return `
    <div class="cp-cmdk-backdrop" data-action="close-cmdk" aria-hidden="true"></div>
    <div class="cp-cmdk" role="dialog" aria-modal="true" aria-label="Palette de commandes">
      <div class="cp-cmdk__inputrow">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input type="text" class="cp-cmdk__input" placeholder="Tapez pour rechercher\u2026" value="${escapeHtml(appState.commandPaletteQuery)}" data-cmdk-input autocomplete="off" spellcheck="false" />
        <kbd class="cp-cmdk__kbd">Esc</kbd>
      </div>
      <div class="cp-cmdk__body">
        ${
          filteredNav.length === 0 && filteredScenarios.length === 0 && filteredActions.length === 0
            ? '<div class="cp-cmdk__empty">Aucun r\u00e9sultat</div>'
            : ''
        }
        ${
          filteredNav.length > 0
            ? `
          <div class="cp-cmdk__group">
            <div class="cp-cmdk__group-title">Surfaces</div>
            ${filteredNav
              .map(
                ([modeId, label, hint]) => `
                  <button type="button" class="cp-cmdk__item" data-cmdk-action="mode" data-mode-id="${modeId}">
                    <span class="cp-cmdk__item-icon" aria-hidden="true">${SURFACE_ICONS[modeId]}</span>
                    <span class="cp-cmdk__item-label">${escapeHtml(label)}</span>
                    <span class="cp-cmdk__item-hint">${escapeHtml(hint)}</span>
                  </button>`
              )
              .join('')}
          </div>`
            : ''
        }
        ${
          filteredScenarios.length > 0
            ? `
          <div class="cp-cmdk__group">
            <div class="cp-cmdk__group-title">Sc\u00e9narios</div>
            ${filteredScenarios
              .map(
                (sc) => `
                  <button type="button" class="cp-cmdk__item" data-cmdk-action="scenario" data-scenario-id="${escapeHtml(sc.id)}">
                    <span class="cp-cmdk__item-icon cp-cmdk__item-dot tone-${escapeHtml(sc.outcome)}" aria-hidden="true"></span>
                    <span class="cp-cmdk__item-label">${escapeHtml(sc.title)}</span>
                    <span class="cp-cmdk__item-hint">${escapeHtml(sc.outcome)}</span>
                  </button>`
              )
              .join('')}
          </div>`
            : ''
        }
        ${
          filteredActions.length > 0
            ? `
          <div class="cp-cmdk__group">
            <div class="cp-cmdk__group-title">Actions</div>
            ${filteredActions
              .map(
                (a) => `
                  <button type="button" class="cp-cmdk__item" data-cmdk-action="${a.id}">
                    <span class="cp-cmdk__item-icon" aria-hidden="true">\u25AB</span>
                    <span class="cp-cmdk__item-label">${escapeHtml(a.label)}</span>
                    <span class="cp-cmdk__item-hint">${escapeHtml(a.hint)}</span>
                  </button>`
              )
              .join('')}
          </div>`
            : ''
        }
      </div>
    </div>`;
}

function render(): void {
  const scenario = getScenarioById(appState.scenarioId);
  const runtimeDashboard = store.reset(scenario.state);
  const vscodePanelView = createVsCodePanelView(runtimeDashboard, { transport: vscodeBridge.transport });
  const observatorySource = getCurrentObservatorySource();
  const proofSource = getCurrentProofSource();
  const surfaceDescriptor = describeSurfaceMode(appState.mode);
  const focusTitle =
    scenario.webViews.cockpitView.ui.focus.taskTitle ?? scenario.webViews.cockpitView.ui.focus.traceTitle ?? 'No focused task';
  const proofPressureCount = scenario.webViews.cockpitView.proofs.filter((proof) => proof.tone !== 'positive').length;
  const proofArtifactCount = proofManifest?.sources.filter((source) => source.available).length ?? scenario.webViews.proofDossierView.packs.length;
  const blockingReasonCount = scenario.webViews.proofDossierView.blockingReasons.length;

  root.innerHTML = `
    <div class="cockpit-grid shell-mode-${appState.mode}">
      <nav class="cockpit-nav" aria-label="Surfaces">
        <div class="cockpit-nav__section">
          ${PRIMARY_SURFACE_MODES.map(
            ([modeId, label]) => `
              <button
                type="button"
                class="cockpit-nav__item ${modeId === appState.mode ? 'is-active' : ''}"
                data-mode-id="${modeId}"
                data-tooltip="${escapeHtml(label)}"
                aria-label="${escapeHtml(label)}"
                aria-current="${modeId === appState.mode ? 'page' : 'false'}"
              >${SURFACE_ICONS[modeId]}</button>`
          ).join('')}
        </div>
        <div class="cockpit-nav__divider" role="separator"></div>
        <div class="cockpit-nav__section">
          ${ATLAS_SURFACE_MODES.map(
            ([modeId, label]) => `
              <button
                type="button"
                class="cockpit-nav__item ${modeId === appState.mode ? 'is-active' : ''}"
                data-mode-id="${modeId}"
                data-tooltip="${escapeHtml(label)}"
                aria-label="${escapeHtml(label)}"
                aria-current="${modeId === appState.mode ? 'page' : 'false'}"
              >${SURFACE_ICONS[modeId]}</button>`
          ).join('')}
        </div>
      </nav>

      <header class="cockpit-topbar" role="banner">
        <div class="cockpit-topbar__breadcrumb">
          <span class="cockpit-topbar__crumb-surface">${escapeHtml(surfaceDescriptor.label)}</span>
          <span class="cockpit-topbar__crumb-sep" aria-hidden="true">/</span>
          <button
            type="button"
            class="cockpit-topbar__crumb-trigger"
            data-popover-trigger="scenario"
            aria-haspopup="dialog"
            aria-expanded="${appState.openPopoverId === 'scenario' ? 'true' : 'false'}"
            title="${escapeHtml(scenario.title)}"
          >
            <span>${escapeHtml(scenario.title)}</span>
            <svg class="cockpit-topbar__crumb-caret" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 4L5 6.5L7.5 4"/></svg>
          </button>
        </div>
        <div class="cockpit-topbar__spacer"></div>
        <button
          type="button"
          class="cockpit-topbar__search"
          data-action="open-cmdk"
          aria-label="Ouvrir la palette de commandes"
          title="Palette (Ctrl+K)"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
          <span class="cockpit-topbar__search-label">Rechercher</span>
          <kbd class="cockpit-topbar__kbd">Ctrl K</kbd>
        </button>
        <button
          type="button"
          class="cockpit-topbar__icon-button"
          aria-label="Notifications (bient\u00f4t)"
          title="Notifications (bient\u00f4t)"
          disabled
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>
        </button>
        <button
          type="button"
          class="cockpit-topbar__icon-button${appState.inspectorOpen ? ' is-active' : ''}"
          data-action="toggle-inspector"
          aria-label="Inspecteur (Ctrl+J)"
          title="Inspecteur (Ctrl+J)"
          aria-pressed="${appState.inspectorOpen ? 'true' : 'false'}"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>
        </button>
        <button
          type="button"
          class="cockpit-topbar__status ${blockingReasonCount === 0 ? 'is-ok' : 'is-critical'}"
          aria-label="Blocages"
          title="${blockingReasonCount === 0 ? 'Aucun blocage' : `${blockingReasonCount} blocage(s)`}"
        >
          <span class="cockpit-topbar__status-dot" aria-hidden="true"></span>
          <span class="cockpit-topbar__status-label">${blockingReasonCount === 0 ? 'OK' : String(blockingReasonCount)}</span>
        </button>
      </header>

      <main class="cockpit-workspace" role="main">
        <div class="shell shell-mode-${appState.mode}">
          ${appState.mode === 'cockpit' ? renderCockpitMode(scenario) : ''}
          ${appState.mode === 'mission-board' ? renderMissionBoardMode(scenario) : ''}
          ${appState.mode === 'kernel' ? renderKernelMode(scenario) : ''}
          ${appState.mode === 'proofs' ? renderProofDossierMode(scenario, proofSource) : ''}
          ${appState.mode === 'game-ui' ? renderGameUiMode(scenario, observatorySource) : ''}
          ${appState.mode === 'observability' ? renderObservabilityMode(scenario) : ''}
          ${appState.mode === 'spectator' ? renderSpectatorMode(scenario) : ''}
          ${appState.mode === 'observer' ? renderObserverMode(scenario) : ''}
          ${appState.mode === 'workflow' ? renderWorkflowMode(scenario) : ''}
          ${appState.mode === 'expert' ? renderExpertMode(scenario) : ''}
          ${appState.mode === 'observatory' ? renderObservatoryMode(observatorySource) : ''}
          ${appState.mode === 'war-room' ? renderWarRoomMode(scenario) : ''}
          ${appState.mode === 'host-bridge' ? renderHostBridgeMode(scenario) : ''}
          ${appState.mode === 'vscode' ? renderVsCodeMode(scenario, vscodePanelView) : ''}
        </div>
      </main>

      <footer class="cockpit-statusbar" role="contentinfo">
        <span class="cockpit-statusbar__item">seq ${runtimeDashboard.lastSequenceId}</span>
        <span class="cockpit-statusbar__item">${scenario.webViews.cockpitView.hosts.length} host(s)</span>
        <span class="cockpit-statusbar__item">${proofPressureCount} signal(s)</span>
        <span class="cockpit-statusbar__item">${proofArtifactCount} preuve(s)</span>
        <span class="cockpit-statusbar__spacer"></span>
        <span class="cockpit-statusbar__item" title="${escapeHtml(focusTitle)}">focus: ${escapeHtml(focusTitle)}</span>
      </footer>

      ${renderScenarioPopover(demoData.scenarios, appState.scenarioId, appState.openPopoverId === 'scenario')}
      ${renderFilterPopover(appState.filter, appState.openPopoverId === 'filter')}
      ${renderDisplayPopover(appState.collapsedSections, appState.openPopoverId === 'display')}
      ${renderCommandPalette(demoData.scenarios, appState.commandPaletteOpen)}
    </div>`;

  const currentVsCodeState = createCurrentVsCodePanelState();
  vscodeBridge.persistState(currentVsCodeState);
  if (appState.mode === 'vscode') {
    vscodeBridge.postReady(currentVsCodeState);
  }

  bindEvents();

  // V1.6 — refresh the ledger panel immediately after DOM swap so the
  // mount picks up cached snapshot without waiting for the next tick.
  hookEventsClient.render();
}

function bindEvents(): void {
  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-scenario-id]')) {
    button.addEventListener('click', () => {
      const scenarioId = button.dataset.scenarioId;
      if (scenarioId === undefined) {
        return;
      }

      appState.scenarioId = scenarioId;
      appState.missionBoardFocusTaskId = null;
      appState.openPopoverId = null;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-mode-id]')) {
    button.addEventListener('click', () => {
      const modeId = button.dataset.modeId;
      if (
        modeId !== 'cockpit' &&
        modeId !== 'mission-board' &&
        modeId !== 'kernel' &&
        modeId !== 'proofs' &&
        modeId !== 'game-ui' &&
        modeId !== 'observability' &&
        modeId !== 'spectator' &&
        modeId !== 'observer' &&
        modeId !== 'workflow' &&
        modeId !== 'expert' &&
        modeId !== 'observatory' &&
        modeId !== 'war-room' &&
        modeId !== 'host-bridge' &&
        modeId !== 'vscode'
      ) {
        return;
      }

      appState.mode = modeId;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-filter-id]')) {
    button.addEventListener('click', () => {
      const filterId = button.dataset.filterId;
      if (filterId !== 'all' && filterId !== 'attention' && filterId !== 'blocked') {
        return;
      }

      appState.filter = filterId;
      appState.openPopoverId = null;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-collapsible-toggle]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      const id = button.dataset.collapsibleToggle;
      if (!id) return;
      if (appState.collapsedSections.has(id)) {
        appState.collapsedSections.delete(id);
      } else {
        appState.collapsedSections.add(id);
      }
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-popover-trigger]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const id = button.dataset.popoverTrigger ?? null;
      appState.openPopoverId = appState.openPopoverId === id ? null : id;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="open-cmdk"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.commandPaletteOpen = true;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-observatory-id]')) {
    button.addEventListener('click', () => {
      const observatoryId = button.dataset.observatoryId;
      if (observatoryId === undefined) {
        return;
      }

      appState.observatorySourceId = observatoryId;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-proof-id]')) {
    button.addEventListener('click', () => {
      const proofId = button.dataset.proofId;
      if (proofId === undefined) {
        return;
      }

      appState.proofSourceId = proofId;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-mission-task-id]')) {
    button.addEventListener('click', () => {
      const taskId = button.dataset.missionTaskId;
      if (taskId === undefined) {
        return;
      }

      appState.missionBoardFocusTaskId = taskId;
      appState.inspectorTaskId = taskId;
      appState.inspectorOpen = true;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLElement>('[data-action="close-inspector"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.inspectorOpen = false;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="toggle-inspector"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.inspectorOpen = !appState.inspectorOpen;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLElement>('[data-action="close-popover"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.openPopoverId = null;
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLElement>('[data-action="close-cmdk"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.commandPaletteOpen = false;
      appState.commandPaletteQuery = '';
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="display-expand-all"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.collapsedSections.clear();
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="display-collapse-all"]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      appState.collapsedSections = new Set<string>([
        'branch-finisher',
        'watchtower',
        'workshop',
        'intake-desk',
        'seance-archive',
        'timeline'
      ]);
      render();
    });
  }

  const cmdkInput = root.querySelector<HTMLInputElement>('[data-cmdk-input]');
  if (cmdkInput !== null) {
    cmdkInput.addEventListener('input', () => {
      appState.commandPaletteQuery = cmdkInput.value;
      const caret = cmdkInput.selectionStart;
      render();
      requestAnimationFrame(() => {
        const freshInput = document.querySelector<HTMLInputElement>('[data-cmdk-input]');
        if (freshInput !== null) {
          freshInput.focus();
          if (caret !== null) {
            freshInput.setSelectionRange(caret, caret);
          }
        }
      });
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('.cp-cmdk__item')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      const action = button.dataset['cmdkAction'];
      if (action === 'mode') {
        const modeId = button.dataset.modeId as SurfaceMode | undefined;
        if (modeId !== undefined) {
          appState.mode = modeId;
          appState.missionBoardFocusTaskId = null;
        }
      } else if (action === 'scenario') {
        const scenarioId = button.dataset.scenarioId;
        if (scenarioId !== undefined) {
          appState.scenarioId = scenarioId;
          appState.missionBoardFocusTaskId = null;
        }
      } else if (action === 'toggle-inspector') {
        appState.inspectorOpen = !appState.inspectorOpen;
      } else if (action === 'display-expand-all') {
        appState.collapsedSections.clear();
      } else if (action === 'display-collapse-all') {
        appState.collapsedSections = new Set<string>([
          'branch-finisher',
          'watchtower',
          'workshop',
          'intake-desk',
          'seance-archive',
          'timeline'
        ]);
      }
      appState.commandPaletteOpen = false;
      appState.commandPaletteQuery = '';
      render();
    });
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-copy-text]')) {
    button.addEventListener('click', async () => {
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
  }

  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-vscode-command]')) {
    button.addEventListener('click', () => {
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
  }
}
