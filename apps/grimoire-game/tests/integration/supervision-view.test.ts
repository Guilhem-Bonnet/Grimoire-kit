import type { GameState } from '../../src/state/game-state';
import { createSupervisionView } from '../../src/state/supervision-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 49,
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
      }
    },
    tasks: {
      'task-verifying': {
        id: 'task-verifying',
        title: 'Ship auth middleware',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-rejected': {
        id: 'task-rejected',
        title: 'Close insecure branch',
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-needs-work': {
        id: 'task-needs-work',
        title: 'Patch verification gaps',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-verifying' },
        sourceEventType: 'artifact_created',
        traceId: 'trace-verifying',
        sequenceId: 20,
        timestamp: '2026-04-10T00:00:20.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Ship auth middleware',
        sourceEventType: 'routing',
        traceId: 'trace-verifying',
        taskId: 'task-verifying',
        metadata: {
          intent: 'Ship auth middleware',
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 10,
        timestamp: '2026-04-10T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Auth middleware verified',
        sourceEventType: 'decision',
        traceId: 'trace-verifying',
        taskId: 'task-verifying',
        metadata: {
          topic: 'auth',
          runId: 'run-001',
          correlationId: 'corr-001',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-verifying/runtime',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://runtime#task-verifying'],
          verdict: 'PASS'
        },
        sequenceId: 21,
        timestamp: '2026-04-10T00:00:21.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Close insecure branch',
        sourceEventType: 'routing',
        traceId: 'trace-rejected',
        taskId: 'task-rejected',
        metadata: {
          intent: 'Close insecure branch',
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 30,
        timestamp: '2026-04-10T00:00:30.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Security branch rejected',
        sourceEventType: 'decision',
        traceId: 'trace-rejected',
        taskId: 'task-rejected',
        metadata: {
          topic: 'security',
          runId: 'run-001',
          correlationId: 'corr-001',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-rejected/runtime',
          controlsExecuted: ['tests:integration', 'review:critical-findings'],
          evidenceRefs: ['tests://runtime#task-rejected'],
          verdict: 'FAIL'
        },
        sequenceId: 31,
        timestamp: '2026-04-10T00:00:31.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix proposed too early',
        detail: 'Patched without root cause',
        sourceEventType: 'decision',
        traceId: 'trace-needs-work',
        taskId: 'task-needs-work',
        metadata: { phase: 'fix_proposed' },
        sequenceId: 42,
        timestamp: '2026-04-10T00:00:42.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('supervision view', () => {
  it('aggregates mission lanes, verification lanes, lineage alerts and release readiness', () => {
    const view = createSupervisionView(createBaseState());

    expect(view.summary).toEqual({
      missionCount: 3,
      blockedMissionCount: 1,
      verificationQueueCount: 3,
      lineageAlertCount: 3,
      releaseBlocked: true
    });
    expect(view.missionLanes.find((lane) => lane.status === 'blocked')?.missions[0]).toMatchObject({
      missionId: 'mission:task:task-rejected',
      title: 'Close insecure branch',
      openEscalationCount: 1
    });
    expect(view.verificationLanes.map((lane) => [lane.status, lane.count])).toEqual([
      ['rejected', 1],
      ['needs_work', 1],
      ['verifying', 1],
      ['queued', 0],
      ['accepted', 0]
    ]);
    expect(view.lineageAlerts).toEqual([
      {
        code: 'MISSING_LINEAGE',
        severity: 'warning',
        count: 1,
        traceIds: ['trace-needs-work']
      },
      {
        code: 'MISSING_EVIDENCE',
        severity: 'warning',
        count: 1,
        traceIds: ['trace-needs-work']
      },
      {
        code: 'MISSING_RUN_ID',
        severity: 'info',
        count: 1,
        traceIds: ['trace-needs-work']
      }
    ]);
    expect(view.releaseGate).toMatchObject({
      shipBlocked: false,
      releaseBlocked: true,
      blockedMissionCount: 1,
      staleLineageAlertCount: 3,
      securityBlockingCount: 0,
      verificationBlockingCount: 2,
      blockingTaskIds: ['task-rejected', 'task-needs-work']
    });
    expect(view.releaseGate.blockingReasons).toContain('One or more missions are blocked in the mission ledger.');
    expect(view.releaseGate.blockingReasons).toContain('Task Close insecure branch is rejected in verification.');
    expect(view.releaseGate.blockingReasons).toContain(
      'Task Patch verification gaps still needs work before verification can complete.'
    );
    expect(view.releaseGate.blockingReasons).toContain(
      'Session trace-needs-work has decisions but no predecessor or successor lineage.'
    );
  });
});