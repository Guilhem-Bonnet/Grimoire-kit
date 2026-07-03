import type { GameState } from '../../src/state/game-state';
import {
  createExperimentView,
  evaluateTaskExperimentGate,
  queryExperimentView
} from '../../src/state/experiment-view';

function createExperimentState(includeDecision: boolean): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 14,
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
      'pm-1': {
        id: 'pm-1',
        name: 'John',
        role: 'agent',
        status: 'working',
        roomId: 'product-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'semantic_search'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Validate auth onboarding experiment',
        status: 'review',
        priority: 'high',
        assigneeId: 'pm-1'
      },
      'task-copy': {
        id: 'task-copy',
        title: 'Refresh marketing copy',
        status: 'todo',
        assigneeId: 'pm-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Experiment framed',
        detail: 'Onboarding hypothesis captured before shipping the slice.',
        sourceEventType: 'decision',
        traceId: 'experiment-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'experiment',
          experimentId: 'exp-auth-onboarding-001',
          experimentTheme: 'onboarding',
          hypothesis: 'Reducing the initial auth friction should improve D7 retention.',
          experimentMetric: 'retention_d7',
          experimentGuardrail: 'activation_rate >= 0.40',
          linkedTaskIds: ['task-auth', 'task-copy']
        },
        sequenceId: 13,
        timestamp: '2026-04-08T00:00:13.000Z',
        agentId: 'pm-1'
      },
      {
        step: 'Experiment measured',
        detail: 'Retention uplift measured before the final product decision.',
        sourceEventType: 'decision',
        traceId: 'experiment-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'experiment',
          experimentId: 'exp-auth-onboarding-001',
          measurementRef: 'measure://auth/onboarding-001',
          measurementSummary: 'D7 retention improved by 5.2 points while activation held steady.',
          ...(includeDecision ? { experimentDecision: 'adopt' } : {})
        },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'pm-1'
      }
    ],
    lastErrors: []
  };
}

describe('experiment view', () => {
  it('publishes a central experimentation register queryable by task and theme', () => {
    const view = createExperimentView(createExperimentState(true));
    const result = queryExperimentView(view, {
      taskId: 'task-copy',
      theme: 'onboarding'
    });

    expect(view.summary).toEqual({
      taskCount: 2,
      applicableCount: 1,
      readyCount: 1,
      blockedCount: 0,
      experimentCount: 1,
      themeCount: 1
    });
    expect(view.experiments).toMatchObject([
      {
        experimentId: 'exp-auth-onboarding-001',
        taskId: 'task-auth',
        theme: 'onboarding',
        metric: 'retention_d7',
        guardrail: 'activation_rate >= 0.40',
        measurementRef: 'measure://auth/onboarding-001',
        decision: 'adopt',
        linkedTaskIds: ['task-auth', 'task-copy'],
        missingFields: [],
        isReady: true
      }
    ]);
    expect(result.totalCount).toBe(1);
    expect(result.experiments[0]).toMatchObject({
      experimentId: 'exp-auth-onboarding-001',
      theme: 'onboarding'
    });
  });

  it('blocks experiment closeout when measurement or explicit decision is missing', () => {
    const gate = evaluateTaskExperimentGate(createExperimentState(false), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReady).toBe(false);
    expect(gate?.missingFields).toEqual(['decision']);
    expect(gate?.blockingReason).toContain('exp-auth-onboarding-001');
  });
});