import {
  CONTROL_PLANE_REGISTRY_VERSION,
  LEASE_STORE_VERSION,
  NODE_REGISTRY_VERSION,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence
} from '../../src/contracts/events';
import { createCollaborationView } from '../../src/state/collaboration-view';
import type { GameState } from '../../src/state/game-state';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';
import { createRuntimeObserverView } from '../../src/state/runtime-observer-view';

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
        status: 'working',
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
        assigneeId: 'qa-1'
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
        step: 'Code implementation',
        detail: 'Auth layer coded',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'auth' },
        sequenceId: 140,
        timestamp: '2026-04-09T00:01:20.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'QA handoff',
        detail: 'Task moved to QA',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'qa' },
        sequenceId: 150,
        timestamp: '2026-04-09T00:01:30.000Z',
        agentId: 'qa-1'
      }
    ],
    lastErrors: []
  };
}

describe('runtime-observer-view', () => {
  it('keeps scene parity with cockpit while exposing handoffs and room-level signals', () => {
    const state = createBaseState();
    const dashboard = createRuntimeDashboardView(
      state,
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
    const observer = createRuntimeObserverView(dashboard, createCollaborationView(state), {
      maxTasksPerLane: 2,
      maxAttentionItems: 4,
      maxTimelinePoints: 6
    });

    expect(observer.focus).toMatchObject({
      runId: 'run-42',
      nodeId: 'node-alpha',
      taskId: 'task-auth'
    });
    expect(observer.parity).toMatchObject({
      runId: 'run-42',
      sameTaskCount: true,
      sameAttentionCount: true,
      sameFocus: true
    });
    expect(observer.entities.some((entity) => entity.kind === 'task' && entity.taskId === 'task-auth')).toBe(true);
    expect(observer.entities.some((entity) => entity.kind === 'node' && entity.nodeId === 'node-alpha')).toBe(true);
    expect(observer.handoffs.some((edge) => edge.relation === 'task_handoff' && edge.taskId === 'task-auth')).toBe(true);
    expect(observer.warRoomAttention).toHaveLength(observer.ui.attention.length);
  });
});