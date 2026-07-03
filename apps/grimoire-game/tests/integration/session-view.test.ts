import { createErrorEvent } from '../../src/contracts/events';
import { createSessionDiff, createSessionView } from '../../src/state/session-view';
import type { GameState } from '../../src/state/game-state';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 19,
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
        lastTool: 'semantic_search'
      },
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
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-observability': {
        id: 'task-observability',
        title: 'Add observability panel',
        status: 'in_progress',
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
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'semantic_search',
        params: { query: 'observability panel', task_id: 'task-observability' },
        sourceEventType: 'graph_update',
        traceId: 'session-002',
        sequenceId: 18,
        timestamp: '2026-04-08T00:00:18.000Z',
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
        sequenceId: 10,
        timestamp: '2026-04-08T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'auth', choice: 'JWT RS256 stateless' },
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Aggregation completed',
        detail: 'Completed 1 tasks with average trust 91',
        sourceEventType: 'aggregation',
        traceId: 'session-001',
        metadata: { tasks_completed: 1, trust_avg: 91 },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Add observability panel',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-observability',
        metadata: { intent: 'Add observability panel' },
        sequenceId: 16,
        timestamp: '2026-04-08T00:00:16.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'observability: start with audit panel',
        sourceEventType: 'decision',
        traceId: 'session-002',
        taskId: 'task-observability',
        metadata: { topic: 'observability', choice: 'audit panel' },
        sequenceId: 17,
        timestamp: '2026-04-08T00:00:17.000Z',
        agentId: 'qa-1'
      }
    ],
    lastErrors: [createErrorEvent(19, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-9', true, '2026-04-08T00:00:19.000Z')]
  };
}

describe('createSessionView', () => {
  it('groups traced audit entries into session summaries ordered by status and freshness', () => {
    const sessionView = createSessionView(createBaseState());

    expect(sessionView.sessions.map((session) => `${session.summary.traceId}:${session.summary.status}`)).toEqual([
      'session-002:active',
      'session-001:completed'
    ]);
    expect(sessionView.metrics).toEqual({
      sessionCount: 2,
      activeCount: 1,
      completedCount: 1,
      attentionCount: 0,
      unscopedEntryCount: 1,
      canonicalEnvelopeCount: 7
    });
    expect(sessionView.sessions[0]?.summary).toMatchObject({
      title: 'Add observability panel',
      toolCallCount: 1,
      activeTaskCount: 1,
      completedTaskCount: 0,
      lastEventType: 'graph_update'
    });
    expect(sessionView.sessions[1]?.summary).toMatchObject({
      title: 'Implement auth',
      toolCallCount: 1,
      activeTaskCount: 0,
      completedTaskCount: 1,
      lastEventType: 'aggregation'
    });
    expect(sessionView.sessions[0]?.canonicalEnvelopes.some((envelope) => envelope.header.messageType === 'task.update')).toBe(true);
    expect(sessionView.sessions[0]?.canonicalEnvelopes.some((envelope) => envelope.header.messageType === 'workflow.step')).toBe(true);
    expect(sessionView.sessions[0]?.canonicalEnvelopes.some((envelope) => envelope.header.messageType === 'tool.call')).toBe(false);
  });

  it('keeps unscoped runtime errors outside traced sessions', () => {
    const sessionView = createSessionView(createBaseState());

    expect(sessionView.unscopedEntries).toHaveLength(1);
    expect(sessionView.unscopedEntries[0]).toMatchObject({
      kind: 'runtime_error',
      level: 'error',
      sequenceId: 19
    });
  });

  it('computes a meaningful diff between two sessions', () => {
    const diff = createSessionDiff(createBaseState(), 'session-001', 'session-002');

    expect(diff).not.toBeNull();
    expect(diff?.newerTraceId).toBe('session-002');
    expect(diff?.sequenceGap).toBe(4);
    expect(diff?.sharedAgentIds).toEqual(['dev-1', 'orch-1']);
    expect(diff?.onlyLeftAgentIds).toEqual([]);
    expect(diff?.onlyRightAgentIds).toEqual(['qa-1']);
    expect(diff?.onlyLeftTaskIds).toEqual(['task-auth']);
    expect(diff?.onlyRightTaskIds).toEqual(['task-observability']);
    expect(diff?.onlyLeftToolNames).toEqual(['create_file']);
    expect(diff?.onlyRightToolNames).toEqual(['semantic_search']);
    expect(diff?.sharedDecisionTitles).toEqual(['Decision recorded', 'Routing dispatch']);
    expect(diff?.onlyLeftDecisionTitles).toEqual(['Aggregation completed']);
    expect(diff?.onlyRightDecisionTitles).toEqual([]);
  });

  it('projects traced security findings into canonical session envelopes', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Security finding recorded',
        detail: 'Missing policy on runtime_config surface',
        sourceEventType: 'security_finding',
        traceId: 'session-002',
        taskId: 'task-observability',
        metadata: {
          findingId: 'SEC-OBS-001',
          title: 'Missing required policy',
          severity: 'critical',
          status: 'open',
          confidenceScore: 9.3,
          exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.',
          surfaceId: 'runtime_config',
          requiredPolicy: 'surface_scoped',
          owaspCategory: 'LLM06:2025 Excessive Agency',
          controls: ['owasp:asvs-v4']
        },
        sequenceId: 19,
        timestamp: '2026-04-08T00:00:19.500Z',
        agentId: 'qa-1'
      }
    ];
    state.lastSequenceId = 20;
    state.lastErrors = [];

    const sessionView = createSessionView(state);
    const session = sessionView.sessions.find((record) => record.summary.traceId === 'session-002');
    const envelope = session?.canonicalEnvelopes.find((candidate) => candidate.header.messageType === 'security.finding');

    expect(envelope).toMatchObject({
      header: {
        messageType: 'security.finding',
        channel: 'session'
      },
      context: {
        traceId: 'session-002',
        taskId: 'task-observability'
      },
      body: {
        findingId: 'SEC-OBS-001',
        title: 'Missing required policy',
        surfaceId: 'runtime_config',
        requiredPolicy: 'surface_scoped',
        owaspCategory: 'LLM06:2025 Excessive Agency'
      }
    });
  });

  it('preserves verification gate correlation and typed evidence refs in canonical session envelopes', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Verification gate PASS',
        detail: 'task.transition.done: verify://task-auth/session-proof',
        sourceEventType: 'verification_gate',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/session-proof',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: ['screenshot://runtime/task-auth-proof.png'],
          typedEvidenceRefs: [{ kind: 'screenshot', ref: 'screenshot://runtime/task-auth-proof.png' }],
          verdict: 'PASS',
          correlationId: 'req-session-proof-1'
        },
        sequenceId: 20,
        timestamp: '2026-04-08T00:00:20.000Z',
        agentId: 'dev-1'
      }
    ];
    state.lastSequenceId = 20;

    const sessionView = createSessionView(state);
    const session = sessionView.sessions.find((record) => record.summary.traceId === 'session-001');
    const envelope = session?.canonicalEnvelopes.find((candidate) => candidate.header.messageType === 'verification.gate');

    expect(envelope).toMatchObject({
      header: {
        messageType: 'verification.gate',
        channel: 'session'
      },
      context: {
        traceId: 'session-001',
        taskId: 'task-auth',
        verificationRef: 'verify://task-auth/session-proof',
        correlationId: 'req-session-proof-1'
      },
      body: {
        actionId: 'task.transition.done',
        evidenceRefs: [{ kind: 'screenshot', ref: 'screenshot://runtime/task-auth-proof.png' }]
      }
    });
  });
});