import {
  createErrorEvent,
  createTaskUpdateEvent,
  createToolCallEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { RuntimeDashboardStore } from '../../src/state/runtime-dashboard-store';

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

function createBaseSnapshot(): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt: '2026-04-09T00:00:00.000Z',
    lastSequenceId: 10,
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
}

describe('RuntimeDashboardStore', () => {
  it('hydrates state from snapshot and exposes dashboard facade', () => {
    const store = new RuntimeDashboardStore({
      dashboard: {
        observability: {
          maxTimelineRows: 5,
          maxAttentionItems: 5
        }
      }
    });

    const dashboard = store.hydrateSnapshot(createBaseSnapshot(), '2026-04-09T00:00:10.000Z');

    expect(dashboard.lastSequenceId).toBe(10);
    expect(dashboard.board.metrics.agentCount).toBe(2);
    expect(dashboard.observability.source.summary.taskCount).toBe(1);
    expect(store.getState().hydratedAt).toBe('2026-04-09T00:00:10.000Z');
  });

  it('applies out-of-order events and keeps runtime dashboard consistent', () => {
    const store = new RuntimeDashboardStore({
      dashboard: {
        observability: {
          maxTimelineRows: 10,
          maxAttentionItems: 10
        }
      }
    });

    store.hydrateSnapshot(createBaseSnapshot());

    const dashboard = store.applyEvents([
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
    ]);

    expect(store.getState().lastSequenceId).toBe(13);
    expect(dashboard.lastSequenceId).toBe(13);
    expect(dashboard.observability.timelineRows.length).toBeGreaterThan(0);
    expect(dashboard.observability.timelineRows.at(-1)?.sequenceId).toBe(13);
    expect(dashboard.summary.criticalAttentionCount).toBeGreaterThan(0);
  });

  it('supports dashboard reconfiguration without replaying events', () => {
    const store = new RuntimeDashboardStore();

    store.hydrateSnapshot(createBaseSnapshot());
    store.applyEvents([
      createWorkflowStepEvent(
        11,
        {
          step: 'Routing dispatch',
          detail: 'Intent routed: Implement auth',
          sourceEventType: 'routing',
          traceId: 'session-001',
          taskId: 'task-auth',
          metadata: { intent: 'Implement auth' }
        },
        {
          timestamp: '2026-04-09T00:00:11.000Z',
          agent: DEV_AGENT
        }
      ),
      createToolCallEvent(
        12,
        {
          tool: 'runTests',
          params: { task_id: 'task-auth', query: 'auth' },
          sourceEventType: 'verification',
          traceId: 'session-002'
        },
        {
          timestamp: '2026-04-09T00:00:12.000Z',
          agent: DEV_AGENT
        }
      )
    ]);

    const dashboard = store.configureDashboard({
      observability: {
        focus: {
          traceId: 'session-001'
        },
        maxTimelineRows: 1,
        maxAttentionItems: 3
      }
    });

    expect(dashboard.observability.focus.traceId).toBe('session-001');
    expect(dashboard.observability.timelineRows).toHaveLength(1);
    expect(dashboard.observability.timelineRows[0]?.traceId).toBe('session-001');
  });

  it('notifies subscribers on dashboard updates and supports unsubscribe', () => {
    const store = new RuntimeDashboardStore();
    const observedSequenceIds: number[] = [];
    const unsubscribe = store.subscribe((dashboard) => {
      observedSequenceIds.push(dashboard.lastSequenceId);
    });

    store.hydrateSnapshot(createBaseSnapshot());
    store.applyEvents([
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
    ]);
    store.configureDashboard({
      observability: {
        maxTimelineRows: 1
      }
    });

    unsubscribe();
    store.reset();

    expect(observedSequenceIds).toEqual([10, 11, 11]);
  });

  it('projects node fleet state when control-plane context is provided', () => {
    const store = new RuntimeDashboardStore({
      controlPlane: {
        nodeRegistry: {
          scannedAt: '2026-04-11T10:00:12.000Z',
          staleAfterMs: 5_000,
          offlineAfterMs: 15_000
        },
        leaseStore: {
          scannedAt: '2026-04-11T10:00:12.000Z',
          ttlMs: 15_000
        }
      }
    });

    store.hydrateSnapshot(createBaseSnapshot());

    const dashboard = store.applyEvents(
      [
        createTaskUpdateEvent(
          11,
          {
            id: 'task-auth',
            title: 'Implement auth',
            status: 'review',
            assigneeId: 'dev-1'
          },
          {
            timestamp: '2026-04-11T10:00:10.000Z',
            agent: DEV_AGENT
          }
        )
      ],
      {
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        leaseId: 'lease-auth',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
      }
    );

    expect(dashboard.projectRegistry?.activeProject.projectId).toBe('grimoire-game');
    expect(dashboard.nodeFleet.summary.nodeCount).toBe(1);
    expect(dashboard.nodeFleet.summary.liveNodeCount).toBe(1);
    expect(dashboard.leaseView.summary.activeLeaseCount).toBe(1);
    expect(dashboard.leaseView.leases[0]).toMatchObject({
      branch: 'feature/auth',
      worktreeId: 'wt-auth',
      ownerId: 'worker-dev-1',
      ownershipStatus: 'owned',
      dirtyStatus: 'dirty'
    });
    expect(dashboard.summary.leaseCount).toBe(1);
    expect(dashboard.summary.nodeWorkerCount).toBe(1);
  });
});