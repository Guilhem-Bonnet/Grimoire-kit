import {
  createErrorEvent,
  createStateSnapshotEvent,
  createToolCallEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot
} from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import {
  createRuntimeDashboardView,
  createRuntimeDashboardViewFromEvents,
  createRuntimeDashboardViewFromSnapshot
} from '../../src/state/runtime-dashboard-view';

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
        metadata: { intent: 'Implement auth' },
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

describe('runtime-dashboard-view', () => {
  it('composes board and observability into a single runtime facade', () => {
    const dashboard = createRuntimeDashboardView(createBaseState(), {
      observability: {
        maxTimelineRows: 2,
        maxAttentionItems: 4
      }
    });

    expect(dashboard.protocolVersion).toBe('v1');
    expect(dashboard.lastSequenceId).toBe(220);
    expect(dashboard.board.metrics.taskCount).toBe(3);
    expect(dashboard.observability.timelineRows).toHaveLength(2);
    expect(dashboard.session.metrics.sessionCount).toBeGreaterThan(0);
    expect(dashboard.missionLedger.summary.missionCount).toBe(3);
    expect(dashboard.sessionLineage.metrics.sessionCount).toBe(2);
    expect(dashboard.tasks.metrics.taskCount).toBe(3);
    expect(dashboard.verification.metrics.taskCount).toBe(3);
    expect(dashboard.verificationEvidencePacks.summary.packCount).toBe(1);
    expect(dashboard.verificationQueue.metrics.itemCount).toBe(2);
    expect(dashboard.sessionDiff).not.toBeNull();
    expect(dashboard.sessionDiff?.newerTraceId).toBe('session-002');
    expect(dashboard.securityAudit.shipBlocked).toBe(false);
    expect(dashboard.branchFinisher.options.some((option) => option.option === 'keep' && option.allowed)).toBe(true);
    expect(dashboard.library.summary.contextEntryCount).toBe(0);
    expect(dashboard.leaseView.summary.leaseCount).toBe(0);
    expect(dashboard.nodeFleet.summary.nodeCount).toBe(0);
    expect(dashboard.supervision.summary.releaseBlocked).toBe(true);
    expect(dashboard.summary).toMatchObject({
      blockedTaskCount: 2,
      activeTaskCount: 3,
      workingAgentCount: 1,
      missionCount: 3,
      blockedMissionCount: 1,
      verificationCount: 1,
      verificationQueueCount: 2,
      verificationVerifyingCount: 1,
      verificationNeedsWorkCount: 1,
      verificationEvidencePackCount: 1,
      verificationAttestationCount: 1,
      lineageEdgeCount: 0,
      canonicalEnvelopeCount: 5,
      securityCardCount: 0,
      securityBlockingFindingCount: 0,
      leaseCount: 0,
      activeLeaseCount: 0,
      expiredLeaseCount: 0,
      leaseAlertCount: 0,
      nodeCount: 0,
      liveNodeCount: 0,
      staleNodeCount: 0,
      offlineNodeCount: 0,
      nodeWorkerCount: 0,
      nodeAlertCount: 0,
      shipBlocked: false
    });
    expect(dashboard.summary.libraryContextCount).toBe(0);
    expect(dashboard.summary.libraryStaleContextCount).toBe(0);
    expect(dashboard.summary.libraryOpenReviewFindingCount).toBe(0);
    expect(dashboard.summary.releaseBlocked).toBe(true);
    expect(dashboard.summary.staleLineageAlertCount).toBeGreaterThan(0);
    expect(dashboard.canonicalEnvelopes).toHaveLength(dashboard.summary.canonicalEnvelopeCount);
    expect(dashboard.canonicalEnvelopes.every((envelope) => envelope.header.channel === 'session')).toBe(true);
    expect(dashboard.summary.criticalAttentionCount).toBeGreaterThan(0);
    expect(dashboard.summary.totalAttentionCount).toBe(dashboard.observability.attentionItems.length);
  });

  it('hydrates dashboard directly from runtime snapshots', () => {
    const snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-09T00:00:00.000Z',
      lastSequenceId: 7,
      agents: [
        {
          id: 'orch-1',
          name: 'Orchestrator',
          role: 'orchestrator',
          status: 'idle',
          roomId: 'war-room',
          position: { x: 4, y: 4 }
        },
        DEV_AGENT
      ],
      tasks: [
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };

    const dashboard = createRuntimeDashboardViewFromSnapshot(snapshot, {
      observability: {
        maxTimelineRows: 5
      }
    });

    expect(dashboard.lastSequenceId).toBe(7);
    expect(dashboard.board.metrics.agentCount).toBe(2);
    expect(dashboard.observability.source.summary.taskCount).toBe(1);
    expect(dashboard.session.metrics.sessionCount).toBe(0);
    expect(dashboard.sessionDiff).toBeNull();
    expect(dashboard.observability.timelineRows).toHaveLength(0);
  });

  it('derives dashboard from ordered replay of server events', () => {
    const snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-09T00:00:00.000Z',
      lastSequenceId: 10,
      agents: [DEV_AGENT],
      tasks: [
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };

    const events = [
      createToolCallEvent(
        12,
        {
          tool: 'create_file',
          params: { path: 'src/auth.ts', task_id: 'task-auth' },
          sourceEventType: 'artifact_created',
          traceId: 'session-001'
        },
        {
          timestamp: '2026-04-09T00:00:12.000Z',
          agent: DEV_AGENT
        }
      ),
      createErrorEvent(13, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-99', true, '2026-04-09T00:00:13.000Z'),
      createStateSnapshotEvent(10, snapshot, '2026-04-09T00:00:10.000Z'),
      createWorkflowStepEvent(
        11,
        {
          step: 'Decision recorded',
          detail: 'auth: JWT middleware ready',
          sourceEventType: 'decision',
          traceId: 'session-001',
          taskId: 'task-auth',
          metadata: { topic: 'auth' }
        },
        {
          timestamp: '2026-04-09T00:00:11.000Z',
          agent: DEV_AGENT
        }
      )
    ];

    const dashboard = createRuntimeDashboardViewFromEvents(events, {
      observability: {
        maxTimelineRows: 8,
        maxAttentionItems: 8
      }
    });

    expect(dashboard.lastSequenceId).toBe(13);
    expect(dashboard.observability.timelineRows.length).toBeGreaterThan(0);
    expect(dashboard.observability.timelineRows.at(-1)?.sequenceId).toBe(13);
    expect(dashboard.tasks.metrics.taskCount).toBe(1);
    expect(dashboard.verification.metrics.taskCount).toBe(1);
    expect(dashboard.sessionDiff).toBeNull();
    expect(dashboard.summary.criticalAttentionCount).toBeGreaterThan(0);
  });
});