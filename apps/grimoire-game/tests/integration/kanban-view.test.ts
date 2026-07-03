import {
  createAgentStateEvent,
  createTaskAssign,
  createTaskTransition,
  createTaskUpdateEvent,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import type { AuthContext } from '../../src/server/auth/rbac';
import {
  createKanbanView,
  planKanbanTaskAssign,
  planKanbanTaskTransition
} from '../../src/state/kanban-view';
import { applyServerEvents, createEmptyGameState, hydrateGameState, type GameState } from '../../src/state/game-state';

const ORCH_AUTH: AuthContext = {
  principalId: 'orch-1',
  role: 'orchestrator'
};

const SPECTATOR_AUTH: AuthContext = {
  principalId: 'spectator-1',
  role: 'spectator'
};

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 20,
    hydratedAt: '2026-04-11T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      },
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'paused',
        roomId: 'qa-room',
        position: { x: 10, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-proof': {
        id: 'task-proof',
        title: 'Collect security proof',
        status: 'review',
        priority: 'critical',
        kind: 'security',
        assigneeId: 'qa-1'
      },
      'task-spec': {
        id: 'task-spec',
        title: 'Draft auth spec',
        status: 'backlog',
        priority: 'high',
        kind: 'feature',
        dependencyIds: ['task-proof'],
        blockedReason: 'Waiting for the security proof chain.'
      },
      'task-build': {
        id: 'task-build',
        title: 'Implement auth middleware',
        status: 'todo',
        priority: 'critical',
        kind: 'bug',
        dependencyIds: ['task-spec'],
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: {
          path: 'src/auth.ts',
          task_id: 'task-build'
        },
        sourceEventType: 'artifact_created',
        traceId: 'trace-build',
        sequenceId: 19,
        timestamp: '2026-04-11T00:00:19.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Implementation started',
        detail: 'Auth middleware is now being implemented.',
        sourceEventType: 'routing',
        traceId: 'trace-build',
        taskId: 'task-build',
        metadata: {
          intent: 'Implement auth middleware'
        },
        sequenceId: 18,
        timestamp: '2026-04-11T00:00:18.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('createKanbanView', () => {
  it('projects created cards with metadata, blockers and activity-synced columns', () => {
    const createdState = applyServerEvents(createEmptyGameState(), [
      createAgentStateEvent(1, {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }),
      createTaskUpdateEvent(
        2,
        {
          id: 'task-spec',
          title: 'Draft auth spec',
          status: 'backlog',
          priority: 'high',
          kind: 'feature',
          dependencyIds: ['task-proof'],
          blockedReason: 'Waiting for the security proof chain.'
        },
        {
          timestamp: '2026-04-11T00:00:02.000Z'
        }
      )
    ]);

    const state = createBaseState();
    const view = createKanbanView(state, ORCH_AUTH);
    const createdView = createKanbanView(createdState, ORCH_AUTH);

    expect(createdView.columns.find((column) => column.status === 'backlog')?.cards[0]).toMatchObject({
      taskId: 'task-spec',
      priority: 'high',
      kind: 'feature',
      dependencyIds: ['task-proof'],
      blockedReason: 'Waiting for the security proof chain.'
    });

    const buildCard = view.cards.find((card) => card.taskId === 'task-build');
    expect(buildCard).toMatchObject({
      rawStatus: 'todo',
      syncedStatus: 'in_progress',
      syncState: 'activity_promoted',
      priority: 'critical',
      kind: 'bug',
      assignee: {
        agentId: 'dev-1',
        roomId: 'build-room'
      }
    });
    expect(view.columns.find((column) => column.status === 'in_progress')?.cards.map((card) => card.taskId)).toContain(
      'task-build'
    );

    const specCard = view.cards.find((card) => card.taskId === 'task-spec');
    expect(specCard?.blockers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: 'dependency_pending',
          dependencyTaskId: 'task-proof'
        }),
        expect.objectContaining({
          code: 'explicit_blocker',
          message: 'Waiting for the security proof chain.'
        })
      ])
    );
  });

  it('plans bounded assign and transition mutations and blocks review -> done without proof', () => {
    const state = createBaseState();
    const assignPlan = planKanbanTaskAssign(state, 'task-spec', 'dev-1', ORCH_AUTH);
    const spectatorAssignPlan = planKanbanTaskAssign(state, 'task-spec', 'dev-1', SPECTATOR_AUTH);
    const buildTransitionPlan = planKanbanTaskTransition(state, 'task-build', 'in_progress', ORCH_AUTH);
    const blockedDonePlan = planKanbanTaskTransition(state, 'task-proof', 'done', ORCH_AUTH);

    expect(assignPlan).toMatchObject({
      allowed: true,
      request: {
        type: 'TASK_ASSIGN',
        taskId: 'task-spec',
        assigneeId: 'dev-1'
      }
    });
    expect(spectatorAssignPlan).toMatchObject({
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    });
    expect(buildTransitionPlan).toMatchObject({
      allowed: true,
      request: {
        type: 'TASK_TRANSITION',
        taskId: 'task-build',
        status: 'in_progress'
      }
    });
    expect(blockedDonePlan).toMatchObject({
      allowed: false,
      reason: 'Task task-proof cannot transition to done without verification evidence.'
    });

    const kanban = createKanbanView(state, ORCH_AUTH);
    expect(kanban.cards.find((card) => card.taskId === 'task-proof')).toMatchObject({
      rawStatus: 'review',
      verificationStatus: 'blocked',
      doneGateBlocked: true
    });
  });

  it('keeps client and server columns aligned after bounded assign and transition writes', async () => {
    const initialSnapshot: GameStateSnapshot = {
      protocolVersion: 'v1',
      generatedAt: '2026-04-11T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'orch-1',
          name: 'Orchestrator',
          role: 'orchestrator',
          status: 'idle',
          roomId: 'war-room',
          position: { x: 4, y: 4 }
        },
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 8, y: 8 },
          parentId: 'orch-1'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'paused',
          roomId: 'qa-room',
          position: { x: 10, y: 8 },
          parentId: 'orch-1'
        }
      ],
      tasks: [
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'todo',
          priority: 'high',
          kind: 'feature',
          assigneeId: 'dev-1'
        }
      ],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };

    const adapter = new MockAgentAdapter(initialSnapshot);
    const assignEvents = await adapter.handleClientEvent(
      createTaskAssign('req-kanban-assign', 'task-auth', 'qa-1', 'req-kanban-assign'),
      ORCH_AUTH
    );
    const transitionedEvents = await adapter.handleClientEvent(
      createTaskTransition('req-kanban-transition', 'task-auth', 'in_progress', 'req-kanban-transition'),
      ORCH_AUTH
    );

    expect(assignEvents[0]?.type).toBe('STATE_SNAPSHOT');
    expect(transitionedEvents[0]?.type).toBe('STATE_SNAPSHOT');

    if (transitionedEvents[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected STATE_SNAPSHOT after task transition.');
    }

    const kanban = createKanbanView(hydrateGameState(transitionedEvents[0].snapshot), ORCH_AUTH);
    expect(kanban.columns.find((column) => column.status === 'in_progress')?.cards).toEqual([
      expect.objectContaining({
        taskId: 'task-auth',
        rawStatus: 'in_progress',
        syncedStatus: 'in_progress',
        assignee: expect.objectContaining({
          agentId: 'qa-1',
          roomId: 'qa-room'
        })
      })
    ]);
  });
});
