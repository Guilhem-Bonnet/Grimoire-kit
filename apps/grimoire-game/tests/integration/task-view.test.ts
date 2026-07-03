import type { GameState } from '../../src/state/game-state';
import { createTaskInspection, createTaskView } from '../../src/state/task-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 24,
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
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'in_progress',
        assigneeId: 'dev-1'
      },
      'task-ghost': {
        id: 'task-ghost',
        title: 'Ghost review',
        status: 'review',
        assigneeId: 'ghost-agent'
      },
      'task-done-no-proof': {
        id: 'task-done-no-proof',
        title: 'Silent completion',
        status: 'done'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'semantic_search',
        params: { query: 'auth strategy', task_id: 'task-auth' },
        sourceEventType: 'graph_update',
        traceId: 'session-001',
        sequenceId: 21,
        timestamp: '2026-04-08T00:00:21.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 22,
        timestamp: '2026-04-08T00:00:22.000Z',
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
        sequenceId: 20,
        timestamp: '2026-04-08T00:00:20.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'auth' },
        sequenceId: 23,
        timestamp: '2026-04-08T00:00:23.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Review dispatched',
        detail: 'Ghost review started in fallback lane',
        sourceEventType: 'routing',
        traceId: 'session-ghost-a',
        taskId: 'task-ghost',
        metadata: { intent: 'Ghost review' },
        sequenceId: 18,
        timestamp: '2026-04-08T00:00:18.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Review rerouted',
        detail: 'Ghost review rerouted to another trace',
        sourceEventType: 'routing',
        traceId: 'session-ghost-b',
        taskId: 'task-ghost',
        metadata: { intent: 'Ghost review rerouted' },
        sequenceId: 19,
        timestamp: '2026-04-08T00:00:19.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

describe('createTaskView', () => {
  it('builds a task-centric inspection with traces, entries and room context', () => {
    const inspection = createTaskInspection(createBaseState(), 'task-auth');

    expect(inspection).not.toBeNull();
    expect(inspection).toMatchObject({
      statusCategory: 'active',
      assigneeAgentId: 'dev-1',
      assigneeAgentName: 'Amelia',
      roomId: 'build-room',
      traceIds: ['session-001'],
      handoffAgentIds: ['dev-1', 'orch-1'],
      lastActivityAt: '2026-04-08T00:00:23.000Z'
    });
    expect(inspection?.recentToolCalls.map((entry) => entry.tool)).toEqual(['create_file', 'semantic_search']);
    expect(inspection?.recentWorkflowSteps.map((entry) => entry.sequenceId)).toEqual([23, 20]);
    expect(inspection?.decisionCards.map((card) => card.sequenceId)).toEqual([23, 20]);
    expect(inspection?.alerts).toEqual([]);
  });

  it('surfaces task-specific alerts for missing assignees, multi-trace activity and missing evidence', () => {
    const taskView = createTaskView(createBaseState());
    const ghostTask = taskView.tasks.find((task) => task.task.id === 'task-ghost');
    const silentTask = taskView.tasks.find((task) => task.task.id === 'task-done-no-proof');

    expect(ghostTask?.alerts.map((alert) => alert.code)).toEqual([
      'TASK_ASSIGNEE_MISSING',
      'TASK_MULTI_TRACE'
    ]);
    expect(silentTask?.alerts.map((alert) => alert.code)).toEqual([
      'TASK_WITHOUT_ACTIVITY',
      'TASK_DONE_WITHOUT_EVIDENCE'
    ]);
  });

  it('computes task-level metrics for traced, active and attention-requiring work', () => {
    const taskView = createTaskView(createBaseState());

    expect(taskView.metrics).toEqual({
      taskCount: 3,
      tracedTaskCount: 2,
      activeCount: 2,
      completedCount: 1,
      attentionCount: 2
    });
    expect(taskView.tasks[0]?.task.id).toBe('task-ghost');
  });
});