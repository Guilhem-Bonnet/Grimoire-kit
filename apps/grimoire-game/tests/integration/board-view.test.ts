import { createErrorEvent } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createAgentInspection, createBoardView } from '../../src/state/board-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 42,
    hydratedAt: '2026-04-08T00:00:00.000Z',
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
      'task-1': {
        id: 'task-1',
        title: 'Plan review',
        status: 'backlog'
      },
      'task-2': {
        id: 'task-2',
        title: 'Implement auth',
        status: 'in_progress',
        assigneeId: 'dev-1'
      },
      'task-3': {
        id: 'task-3',
        title: 'Review auth',
        status: 'review',
        assigneeId: 'qa-1'
      },
      'task-4': {
        id: 'task-4',
        title: 'Ship auth',
        status: 'done',
        assigneeId: 'qa-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'semantic_search',
        params: {
          query: 'auth strategy',
          task_id: 'task-2'
        },
        sourceEventType: 'graph_update',
        traceId: 'session-001',
        sequenceId: 39,
        timestamp: '2026-04-08T00:00:39.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'create_file',
        params: {
          path: 'src/auth.ts',
          task_id: 'task-2'
        },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 40,
        timestamp: '2026-04-08T00:00:40.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-001',
        taskId: 'task-2',
        metadata: {
          intent: 'Implement auth'
        },
        sequenceId: 38,
        timestamp: '2026-04-08T00:00:38.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-2',
        metadata: {
          topic: 'auth'
        },
        sequenceId: 41,
        timestamp: '2026-04-08T00:00:41.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('board view projection', () => {
  it('derives room occupancy and canonical kanban columns from GameState', () => {
    const view = createBoardView(createBaseState());

    expect(view.taskColumns.map((column) => column.status)).toEqual([
      'backlog',
      'todo',
      'in_progress',
      'review',
      'done'
    ]);
    expect(view.taskColumns[2]?.tasks.map((task) => task.id)).toEqual(['task-2']);
    expect(view.taskColumns[3]?.tasks.map((task) => task.id)).toEqual(['task-3']);

    const buildRoom = view.rooms.find((room) => room.id === 'build-room');
    const warRoom = view.rooms.find((room) => room.id === 'war-room');

    expect(buildRoom).toMatchObject({
      agentIds: ['dev-1'],
      taskIds: ['task-2'],
      taskCount: 1,
      activeTaskCount: 1,
      workingCount: 1,
      leadAgentId: 'dev-1'
    });
    expect(warRoom).toMatchObject({
      agentIds: ['orch-1'],
      leadAgentId: 'orch-1'
    });
    expect(view.metrics).toMatchObject({
      roomCount: 3,
      agentCount: 3,
      workingAgentCount: 1,
      taskCount: 4,
      activeTaskCount: 2,
      alertCount: 0
    });
  });

  it('surfaces integrity alerts for runtime errors and inconsistent references', () => {
    const baseState = createBaseState();
    const state: GameState = {
      ...baseState,
      agents: {
        ...baseState.agents,
        'dev-2': {
          id: 'dev-2',
          name: 'Barry',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 9, y: 8 },
          parentId: 'ghost-parent'
        }
      },
      tasks: {
        ...baseState.tasks,
        'task-5': {
          id: 'task-5',
          title: 'Ghost review',
          status: 'review',
          assigneeId: 'ghost-agent'
        },
        'task-6': {
          id: 'task-6',
          title: 'Unassigned QA',
          status: 'todo'
        }
      },
      lastErrors: [createErrorEvent(43, 'WS_TIMEOUT', 'Live sync stalled.', 'req-43')]
    };

    const view = createBoardView(state);
    const alertCodes = view.alerts.map((alert) => alert.code);

    expect(alertCodes).toEqual([
      'RUNTIME_ERROR',
      'TASK_ASSIGNEE_MISSING',
      'AGENT_PARENT_MISSING',
      'TASK_UNASSIGNED_ACTIVE',
      'WORKING_AGENT_WITHOUT_ACTIVE_TASK'
    ]);
    expect(view.alerts[0]).toMatchObject({
      level: 'error',
      message: 'WS_TIMEOUT: Live sync stalled. (correlation req-43)',
      sequenceId: 43
    });
  });

  it('builds inspection views with parent-child links, room context and assigned tasks', () => {
    const state = createBaseState();
    const inspection = createAgentInspection(state, 'dev-1');
    const orchestratorInspection = createAgentInspection(state, 'orch-1');

    expect(inspection).not.toBeNull();
    expect(inspection?.parentAgent?.id).toBe('orch-1');
    expect(inspection?.assignedTasks.map((task) => task.id)).toEqual(['task-2']);
    expect(inspection?.recentToolCalls.map((entry) => entry.tool)).toEqual(['create_file', 'semantic_search']);
    expect(inspection?.recentWorkflowSteps.map((entry) => entry.step)).toEqual([
      'Implementation finished',
      'Routing dispatch'
    ]);
    expect(inspection?.decisionCards).toHaveLength(2);
    expect(inspection?.decisionCards[0]).toMatchObject({
      title: 'Implementation finished',
      traceId: 'session-001',
      taskId: 'task-2',
      taskTitle: 'Implement auth',
      roomId: 'build-room'
    });
    expect(inspection?.decisionCards[0]?.supportingToolCalls.map((entry) => entry.tool)).toEqual([
      'semantic_search',
      'create_file'
    ]);
    expect(inspection?.decisionCards[0]?.evidence.map((entry) => entry.sequenceId)).toEqual([38, 39, 40, 41]);
    expect(inspection?.decisionCards[0]?.evidence.map((entry) => entry.kind)).toEqual([
      'workflow_step',
      'tool_call',
      'tool_call',
      'workflow_step'
    ]);
    expect(inspection?.room).toMatchObject({
      id: 'build-room',
      taskIds: ['task-2']
    });
    expect(inspection?.alerts).toEqual([]);

    expect(orchestratorInspection?.childAgents.map((agent) => agent.id)).toEqual(['dev-1', 'qa-1']);
    expect(orchestratorInspection?.parentAgent).toBeNull();
    expect(orchestratorInspection?.decisionCards).toEqual([]);
  });

  it('keeps decision tool evidence scoped to the same trace when available', () => {
    const baseState = createBaseState();
    const state: GameState = {
      ...baseState,
      recentToolCalls: [
        ...baseState.recentToolCalls,
        {
          tool: 'runTests',
          params: {
            task_id: 'task-2'
          },
          sourceEventType: 'graph_update',
          traceId: 'session-002',
          sequenceId: 40,
          timestamp: '2026-04-08T00:00:40.500Z',
          agentId: 'dev-1'
        },
        {
          tool: 'memory',
          params: {
            task_id: 'task-2'
          },
          sourceEventType: 'graph_update',
          sequenceId: 40,
          timestamp: '2026-04-08T00:00:40.600Z',
          agentId: 'dev-1'
        }
      ]
    };

    const inspection = createAgentInspection(state, 'dev-1');

    expect(inspection).not.toBeNull();
    expect(inspection?.decisionCards[0]?.supportingToolCalls.map((entry) => entry.tool)).toEqual([
      'semantic_search',
      'create_file'
    ]);
    expect(
      inspection?.decisionCards[0]?.supportingToolCalls.every((entry) => entry.traceId === 'session-001')
    ).toBe(true);
  });

  it('projects security findings into board security cards and blocking alerts', () => {
    const baseState = createBaseState();
    const state: GameState = {
      ...baseState,
      recentWorkflowSteps: [
        ...baseState.recentWorkflowSteps,
        {
          step: 'Security finding recorded',
          detail: 'Critical missing policy',
          sourceEventType: 'security_finding',
          traceId: 'session-003',
          metadata: {
            findingId: 'SEC-004',
            title: 'Missing required policy',
            severity: 'critical',
            status: 'open',
            confidenceScore: 9.1,
            exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.',
            surfaceId: 'runtime_config',
            missingPolicy: true,
            origin: 'runtime_ui'
          },
          sequenceId: 44,
          timestamp: '2026-04-08T00:00:44.000Z',
          agentId: 'dev-1'
        }
      ]
    };

    const view = createBoardView(state);

    expect(view.securityCards).toHaveLength(1);
    expect(view.securityCards[0]).toMatchObject({
      findingId: 'SEC-004',
      status: 'review',
      blocksShip: true
    });
    expect(view.alerts.some((alert) => alert.code === 'SECURITY_FINDING_BLOCKING')).toBe(true);
    expect(view.metrics.securityCardCount).toBe(1);
  });
});