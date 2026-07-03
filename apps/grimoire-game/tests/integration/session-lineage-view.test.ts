import { createSessionLineageView, querySeanceSessions } from '../../src/state/session-lineage-view';
import type { GameState } from '../../src/state/game-state';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 45,
    hydratedAt: '2026-04-10T00:00:00.000Z',
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
        status: 'done',
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
        timestamp: '2026-04-10T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'runTests',
        params: { query: 'auth handoff', task_id: 'task-auth' },
        sourceEventType: 'test_run',
        traceId: 'session-002',
        sequenceId: 23,
        timestamp: '2026-04-10T00:00:23.000Z',
        agentId: 'qa-1'
      },
      {
        tool: 'semantic_search',
        params: { query: 'observability panel', task_id: 'task-observability' },
        sourceEventType: 'graph_update',
        traceId: 'session-003',
        sequenceId: 34,
        timestamp: '2026-04-10T00:00:34.000Z',
        agentId: 'qa-1'
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
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 10,
        timestamp: '2026-04-10T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          runId: 'run-001',
          correlationId: 'corr-001',
          evidenceRefs: ['tests://auth#lineage']
        },
        sequenceId: 11,
        timestamp: '2026-04-10T00:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Ship auth',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-auth',
        metadata: {
          intent: 'Ship auth',
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 20,
        timestamp: '2026-04-10T00:00:20.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: ship candidate validated',
        sourceEventType: 'decision',
        traceId: 'session-002',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          runId: 'run-001',
          correlationId: 'corr-001',
          evidenceRefs: ['tests://auth#lineage']
        },
        sequenceId: 21,
        timestamp: '2026-04-10T00:00:21.000Z',
        agentId: 'qa-1'
      },
      {
        step: 'Decision recorded',
        detail: 'observability: ship audit panel first',
        sourceEventType: 'decision',
        traceId: 'session-003',
        taskId: 'task-observability',
        metadata: {
          topic: 'observability'
        },
        sequenceId: 33,
        timestamp: '2026-04-10T00:00:33.000Z',
        agentId: 'qa-1'
      }
    ],
    lastErrors: []
  };
}

describe('session-lineage-view', () => {
  it('builds a stable predecessor graph and flags stale closed sessions', () => {
    const lineage = createSessionLineageView(createBaseState());

    expect(lineage.metrics).toMatchObject({
      sessionCount: 3,
      closedSessionCount: 3,
      edgeCount: 1
    });

    const latestAuthSession = lineage.nodes.find((node) => node.traceId === 'session-002');
    expect(latestAuthSession?.predecessorTraceIds).toEqual(['session-001']);
    expect(latestAuthSession?.missionIds).toEqual(['mission:task:task-auth']);

    expect(lineage.edges).toContainEqual({
      edgeId: 'lineage:session-001:session-002',
      fromTraceId: 'session-001',
      toTraceId: 'session-002',
      kind: 'handoff',
      score: 9,
      sharedMissionIds: ['mission:task:task-auth'],
      sharedTaskIds: ['task-auth'],
      sharedAgentIds: ['orch-1'],
      sharedEvidenceRefs: ['tests://auth#lineage']
    });

    const staleCodes = lineage.alerts
      .filter((alert) => alert.traceId === 'session-003')
      .map((alert) => alert.code)
      .sort();
    expect(staleCodes).toEqual(['MISSING_EVIDENCE', 'MISSING_LINEAGE', 'MISSING_RUN_ID']);
  });

  it('answers Seance read-only queries by mission, evidence and trace', () => {
    const lineage = createSessionLineageView(createBaseState());

    const missionQuery = querySeanceSessions(lineage, {
      missionId: 'mission:task:task-auth'
    });
    expect(missionQuery.sessions.map((session) => session.traceId)).toEqual(['session-002', 'session-001']);

    const evidenceQuery = querySeanceSessions(lineage, {
      evidenceRef: 'tests://auth#lineage'
    });
    expect(evidenceQuery.sessions).toHaveLength(2);

    const traceQuery = querySeanceSessions(lineage, {
      traceId: 'session-003'
    });
    expect(traceQuery.sessions).toHaveLength(1);
    expect(traceQuery.sessions[0]?.title).toBe('Add observability panel');
  });
});