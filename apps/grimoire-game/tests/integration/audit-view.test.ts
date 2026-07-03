import { createErrorEvent } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createAuditView } from '../../src/state/audit-view';

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
      }
    },
    tasks: {
      'task-2': {
        id: 'task-2',
        title: 'Implement auth',
        status: 'in_progress',
        assigneeId: 'dev-1'
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
        agentId: 'orch-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-2',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          decisionContext: 'Auth middleware is ready to move from review to done.',
          consideredOptions: ['Keep the task in review', 'Ship the middleware now'],
          selectedOption: 'Ship the middleware now',
          rationale: 'Security checks and integration evidence are already present.',
          impact: 'The critical auth task can close without leaving verification debt.',
          evidenceRefs: ['tool:create_file#40', 'workflow:session-001#41']
        },
        sequenceId: 41,
        timestamp: '2026-04-08T00:00:41.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: [createErrorEvent(42, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-9', true, '2026-04-08T00:00:42.000Z')]
  };
}

describe('createAuditView', () => {
  it('aggregates runtime errors, decision cards, workflow steps and tool calls in reverse sequence order', () => {
    const auditView = createAuditView(createBaseState());

    expect(auditView.entries.map((entry) => `${entry.kind}:${entry.sequenceId}`)).toEqual([
      'runtime_error:42',
      'decision_card:41',
      'task_handoff:41',
      'workflow_step:41',
      'tool_call:40',
      'tool_call:39',
      'decision_card:38',
      'workflow_step:38'
    ]);
    expect(auditView.metrics).toEqual({
      totalCount: 8,
      filteredCount: 8,
      runtimeErrorCount: 1,
      decisionCardCount: 2,
      workflowStepCount: 2,
      toolCallCount: 2,
      errorCount: 1,
      warningCount: 0,
      infoCount: 7
    });
    expect(auditView.decisionCards).toHaveLength(2);
    expect(auditView.entries[1]).toMatchObject({
      kind: 'decision_card',
      agentId: 'dev-1',
      roomId: 'build-room',
      taskId: 'task-2',
      taskTitle: 'Implement auth',
      traceId: 'session-001'
    });
    expect(auditView.decisionCards[0]).toMatchObject({
      actionId: 'task.transition.done',
      isStructured: true,
      decisionContext: 'Auth middleware is ready to move from review to done.',
      selectedOption: 'Ship the middleware now',
      evidenceRefs: ['tool:create_file#40', 'workflow:session-001#41']
    });
  });

  it('applies compound filters across agent, trace, kind and free-text query', () => {
    const auditView = createAuditView(createBaseState(), {
      agentId: 'dev-1',
      traceId: 'session-001',
      kinds: ['decision_card', 'workflow_step'],
      query: 'jwt'
    });

    expect(auditView.hasActiveFilters).toBe(true);
    expect(auditView.entries).toHaveLength(2);
    expect(auditView.entries.map((entry) => `${entry.kind}:${entry.sequenceId}`)).toEqual([
      'decision_card:41',
      'workflow_step:41'
    ]);
    expect(auditView.metrics.filteredCount).toBe(2);
    expect(auditView.decisionCards).toHaveLength(1);
    expect(auditView.decisionCards[0]?.sequenceId).toBe(41);
  });

  it('exposes facets for agents, rooms, tasks and traces to drive an audit UI', () => {
    const auditView = createAuditView(createBaseState());

    expect(auditView.facets.agents).toEqual([
      { value: 'dev-1', label: 'Amelia', count: 4 },
      { value: 'orch-1', label: 'Orchestrator', count: 3 }
    ]);
    expect(auditView.facets.rooms).toEqual([
      { value: 'build-room', count: 4 },
      { value: 'war-room', count: 3 }
    ]);
    expect(auditView.facets.tasks).toEqual([
      { value: 'task-2', label: 'Implement auth', count: 7 }
    ]);
    expect(auditView.facets.traces).toEqual([
      { value: 'session-001', count: 5 }
    ]);
    expect(auditView.facets.kinds).toEqual([
      { value: 'runtime_error', count: 1 },
      { value: 'decision_card', count: 2 },
      { value: 'task_handoff', count: 1 },
      { value: 'workflow_step', count: 2 },
      { value: 'tool_call', count: 2 }
    ]);
    expect(auditView.facets.levels).toEqual([
      { value: 'error', count: 1 },
      { value: 'info', count: 7 }
    ]);
  });

  it('derives task handoff entries so audit and timeline can inspect departures and arrivals', () => {
    const auditView = createAuditView(createBaseState(), {
      kinds: ['task_handoff']
    });

    expect(auditView.entries).toHaveLength(1);
    expect(auditView.entries[0]).toMatchObject({
      kind: 'task_handoff',
      sourceEventType: 'task_handoff',
      agentId: 'orch-1',
      roomId: 'war-room',
      taskId: 'task-2',
      traceId: 'session-001',
      title: 'Task handoff: Implement auth',
      detail: 'Orchestrator -> Amelia: Implementation finished',
      metadata: {
        fromAgentId: 'orch-1',
        toAgentId: 'dev-1',
        scope: 'inter_room',
        correlationId: 'trace:session-001'
      }
    });
  });

  it('keeps governed surface rejection evidence visible in audit entries', () => {
    const state = {
      ...createBaseState(),
      lastErrors: [
        createErrorEvent(
          43,
          'FORBIDDEN',
          'Governed surface runtime_config is blocked by trust status policy.',
          'req-governed-surface',
          false,
          '2026-04-08T00:00:43.000Z'
        )
      ]
    };

    const auditView = createAuditView(state, {
      kinds: ['runtime_error']
    });

    expect(auditView.entries).toHaveLength(1);
    expect(auditView.entries[0]).toMatchObject({
      kind: 'runtime_error',
      title: 'Runtime error: FORBIDDEN',
      detail: 'Governed surface runtime_config is blocked by trust status policy.',
      metadata: {
        retryable: false,
        correlation_id: 'req-governed-surface'
      }
    });
  });

  it('keeps verification-chain metadata visible in workflow-step audit entries', () => {
    const state: GameState = {
      ...createBaseState(),
      recentWorkflowSteps: [
        ...createBaseState().recentWorkflowSteps,
        {
          step: 'Verification gate PASS',
          detail: 'task.transition.done: verify://task-2/8',
          sourceEventType: 'verification_gate',
          traceId: 'session-001',
          taskId: 'task-2',
          metadata: {
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-2/8',
            controlsExecuted: ['tests:unit', 'review:critical-findings'] as string[],
            evidenceRefs: ['tests://grimoire-game/audit-view#verification-chain'] as string[]
          },
          sequenceId: 43,
          timestamp: '2026-04-08T00:00:43.000Z',
          agentId: 'dev-1'
        }
      ]
    } as const;

    const auditView = createAuditView(state, {
      kinds: ['workflow_step'],
      query: 'verify://task-2/8'
    });

    expect(auditView.entries).toHaveLength(1);
    expect(auditView.entries[0]).toMatchObject({
      kind: 'workflow_step',
      sourceEventType: 'verification_gate',
      traceId: 'session-001',
      taskId: 'task-2',
      metadata: {
        actionId: 'task.transition.done',
        verificationRef: 'verify://task-2/8',
        controlsExecuted: ['tests:unit', 'review:critical-findings'],
        evidenceRefs: ['tests://grimoire-game/audit-view#verification-chain']
      }
    });
  });
});