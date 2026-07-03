import {
  CONTROL_PLANE_REGISTRY_VERSION,
  LEASE_STORE_VERSION,
  NODE_REGISTRY_VERSION,
  createErrorEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot
} from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createRuntimeDashboardUiView } from '../../src/state/runtime-dashboard-ui-view';
import { createRuntimeDashboardView, createRuntimeDashboardViewFromSnapshot } from '../../src/state/runtime-dashboard-view';

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'create_file'
};

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 220,
    hydratedAt: '2026-04-09T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': DEV_AGENT,
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'idle',
        roomId: 'qa-room',
        position: { x: 10, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-qa': {
        id: 'task-qa',
        title: 'QA pass',
        status: 'in_progress',
        assigneeId: 'qa-1'
      },
      'task-docs': {
        id: 'task-docs',
        title: 'Update docs',
        status: 'review',
        assigneeId: 'ghost-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 180,
        timestamp: '2026-04-09T00:03:00.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          intent: 'Implement auth',
          missionPack: {
            objective: 'Ship auth runtime flow with proof',
            scope: ['src/auth.ts', 'tests/auth.test.ts'],
            canonicalSources: ['src/auth.ts', 'tests/auth.test.ts'],
            constraints: ['repo-first', 'evidence-before-done'],
            expectedOutput: 'patch',
            expectedProof: [
              'verify://task-auth/runtime-dashboard',
              'control://tests:unit',
              'tests://grimoire-game/runtime-dashboard#task-auth'
            ],
            mode: 'commit'
          }
        },
        sequenceId: 120,
        timestamp: '2026-04-09T00:01:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT middleware ready',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
          verdict: 'PASS'
        },
        sequenceId: 140,
        timestamp: '2026-04-09T00:01:20.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: QA pass',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-qa',
        metadata: { intent: 'QA pass' },
        sequenceId: 200,
        timestamp: '2026-04-09T00:03:20.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: [createErrorEvent(220, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-17', true, '2026-04-09T00:03:40.000Z')]
  };
}

describe('runtime-dashboard-ui-view', () => {
  it('projects dashboard payload into UI-ready lanes, cards and timeline', () => {
    const dashboard = createRuntimeDashboardView(createBaseState(), {
      observability: {
        maxTimelineRows: 12,
        maxAttentionItems: 12
      }
    });
    const uiView = createRuntimeDashboardUiView(dashboard, {
      maxTasksPerLane: 1,
      maxAttentionItems: 4,
      maxTimelinePoints: 3
    });

    expect(uiView.protocolVersion).toBe('v1');
    expect(uiView.lastSequenceId).toBe(220);
    expect(uiView.header.tone).toBe('critical');
    expect(uiView.statCards.find((card) => card.id === 'blocked-tasks')?.value).toBe(dashboard.summary.blockedTaskCount);
    expect(uiView.statCards.find((card) => card.id === 'verification-queue')).toMatchObject({
      value: dashboard.summary.verificationQueueCount,
      tone: 'warning'
    });
    expect(uiView.statCards.find((card) => card.id === 'evidence-packs')).toMatchObject({
      value: dashboard.summary.verificationAttestationCount,
      tone: 'positive'
    });
    expect(uiView.lanes.every((lane) => lane.tasks.length <= 1)).toBe(true);
    expect(uiView.attention.length).toBeLessThanOrEqual(4);
    expect(uiView.verificationQueue).toHaveLength(5);
    expect(uiView.verificationQueue.map((lane) => [lane.status, lane.count])).toEqual([
      ['rejected', 0],
      ['needs_work', 1],
      ['verifying', 1],
      ['queued', 0],
      ['accepted', 0]
    ]);
    expect(uiView.verificationQueue.find((lane) => lane.status === 'verifying')?.items[0]).toMatchObject({
      taskId: 'task-auth',
      queueStatus: 'verifying',
      verdict: 'PASS',
      tone: 'positive'
    });
    expect(uiView.evidencePacks).toHaveLength(1);
    expect(uiView.evidencePacks[0]).toMatchObject({
      missionId: 'mission:task:task-auth',
      verificationRef: 'verify://task-auth/runtime-dashboard',
      attested: true,
      tone: 'positive',
      missionMode: 'commit',
      expectedProofCount: 3,
      missingExpectedProofCount: 0
    });
    expect(uiView.evidencePacks[0]?.detail).toContain('3/3 mission proof(s) satisfied');
    expect(uiView.timeline.length).toBeLessThanOrEqual(3);
    expect(uiView.timeline.map((point) => point.sequenceId)).toEqual(
      [...uiView.timeline.map((point) => point.sequenceId)].sort((left, right) => left - right)
    );

    const inProgressLane = uiView.lanes.find((lane) => lane.status === 'in_progress');
    expect(inProgressLane?.tasks[0]?.assigneeName).toBe('Quinn');
  });

  it('keeps neutral headline tone when runtime has no active signal', () => {
    const snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-09T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          id: 'orch-1',
          name: 'Orchestrator',
          role: 'orchestrator',
          status: 'idle',
          roomId: 'war-room',
          position: { x: 4, y: 4 }
        }
      ],
      tasks: [],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };

    const dashboard = createRuntimeDashboardViewFromSnapshot(snapshot);
    const uiView = createRuntimeDashboardUiView(dashboard);

    expect(uiView.header.tone).toBe('neutral');
    expect(uiView.header.title).toBe('Runtime is idle');
    expect(uiView.attention).toHaveLength(0);
  });

  it('projects fleet and ownership cards from the control-plane read models', () => {
    const dashboard = createRuntimeDashboardView(
      createBaseState(),
      {
        observability: {
          maxTimelineRows: 12,
          maxAttentionItems: 12
        }
      },
      {
        projectRegistry: {
          registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          activeProject: {
            protocolVersion: RUNTIME_PROTOCOL_VERSION,
            projectId: 'grimoire-game',
            runId: 'run-42',
            firstEventAt: '2026-04-11T10:00:00.000Z',
            lastEventAt: '2026-04-11T10:00:10.000Z',
            firstSequenceId: 1,
            lastSequenceId: 220,
            eventCount: 3,
            lastMessageId: 'task.update:220',
            nodeId: 'node-alpha',
            workerId: 'worker-dev-1',
            leaseId: 'lease-auth',
            worktreeId: 'wt-auth',
            nodeIds: ['node-alpha'],
            workerIds: ['worker-dev-1'],
            leaseIds: ['lease-auth'],
            worktreeIds: ['wt-auth'],
            channels: ['runtime'],
            messageTypes: ['task.update']
          }
        },
        nodeRegistry: {
          registryVersion: NODE_REGISTRY_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          projectId: 'grimoire-game',
          runId: 'run-42',
          nodes: [
            {
              protocolVersion: RUNTIME_PROTOCOL_VERSION,
              projectId: 'grimoire-game',
              runId: 'run-42',
              nodeId: 'node-alpha',
              firstSeenAt: '2026-04-11T10:00:00.000Z',
              lastSeenAt: '2026-04-11T10:00:10.000Z',
              firstSequenceId: 1,
              lastSequenceId: 220,
              messageCount: 3,
              staleAfterMs: 5_000,
              offlineAfterMs: 15_000,
              ageMs: 1_000,
              status: 'live',
              leaseId: 'lease-auth',
              worktreeId: 'wt-auth',
              capabilityTags: ['typescript', 'tests'],
              workerIds: ['worker-dev-1'],
              workers: [
                {
                  workerId: 'worker-dev-1',
                  firstSeenAt: '2026-04-11T10:00:00.000Z',
                  lastSeenAt: '2026-04-11T10:00:10.000Z',
                  firstSequenceId: 1,
                  lastSequenceId: 220,
                  messageCount: 3,
                  leaseId: 'lease-auth',
                  worktreeId: 'wt-auth'
                }
              ],
              channels: ['runtime'],
              messageTypes: ['task.update']
            }
          ],
          summary: {
            nodeCount: 1,
            liveNodeCount: 1,
            staleNodeCount: 0,
            offlineNodeCount: 0,
            workerCount: 1
          }
        },
        leaseStore: {
          registryVersion: LEASE_STORE_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          projectId: 'grimoire-game',
          runId: 'run-42',
          leases: [
            {
              protocolVersion: RUNTIME_PROTOCOL_VERSION,
              projectId: 'grimoire-game',
              runId: 'run-42',
              leaseId: 'lease-auth',
              taskId: 'task-auth',
              nodeId: 'node-alpha',
              workerId: 'worker-dev-1',
              worktreeId: 'wt-auth',
              branch: 'feature/auth',
              claimedAt: '2026-04-11T10:00:00.000Z',
              lastRenewedAt: '2026-04-11T10:00:05.000Z',
              expiresAt: '2026-04-11T10:00:35.000Z',
              ttlMs: 30_000,
              ageMs: 5_000,
              status: 'active',
              messageCount: 2,
              lastSequenceId: 220,
              channels: ['runtime'],
              messageTypes: ['task.update']
            }
          ],
          summary: {
            leaseCount: 1,
            activeLeaseCount: 1,
            expiredLeaseCount: 0
          }
        }
      }
    );
    const uiView = createRuntimeDashboardUiView(dashboard);

    expect(uiView.statCards.find((card) => card.id === 'live-nodes')).toMatchObject({
      value: 1,
      tone: 'positive'
    });
    expect(uiView.statCards.find((card) => card.id === 'active-leases')).toMatchObject({
      value: 1,
      tone: 'positive'
    });
    expect(uiView.fleet[0]).toMatchObject({
      nodeId: 'node-alpha',
      activeLeaseCount: 1,
      workerCount: 1,
      tone: 'positive'
    });
    expect(uiView.ownership[0]).toMatchObject({
      leaseId: 'lease-auth',
      taskId: 'task-auth',
      taskTitle: 'Implement auth',
      ownerId: 'worker-dev-1',
      branch: 'feature/auth',
      worktreeId: 'wt-auth',
      ownershipStatus: 'owned',
      dirtyStatus: 'dirty',
      tone: 'positive'
    });
    expect(uiView.focus.runId).toBe('run-42');
    expect(uiView.focus.nodeId).toBe('node-alpha');
  });
});