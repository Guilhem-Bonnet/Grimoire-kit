import type { GameState } from '../../src/state/game-state';
import {
  createIncidentRecoveryView,
  evaluateTaskIncidentRecoveryGate
} from '../../src/state/incident-recovery-view';

function createIncidentRecoveryState(includeProof: boolean): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 12,
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
        roomId: 'runtime-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Recover auth runtime after websocket outage',
        status: 'review',
        priority: 'critical',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'runTests',
        params: { task_id: 'task-auth' },
        sourceEventType: 'test_run',
        traceId: 'incident-001',
        sequenceId: 10,
        timestamp: '2026-04-08T00:00:10.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Incident declared',
        detail: 'Websocket transport became unavailable during auth replay.',
        sourceEventType: 'incident',
        traceId: 'incident-001',
        taskId: 'task-auth',
        metadata: {
          incidentType: 'ws_unavailable',
          runbookRef: 'runbook://incident/ws-unavailable/v1'
        },
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Recovery exercise completed',
        detail: 'Recovery checklist executed and client/server state resynchronized.',
        sourceEventType: 'recovery',
        traceId: 'incident-001',
        taskId: 'task-auth',
        metadata: {
          incidentType: 'ws_unavailable',
          exerciseRef: 'exercise://task-auth/ws-unavailable-001',
          recoveryChecklist: includeProof
            ? ['detection', 'containment', 'recovery', 'verification']
            : ['detection', 'containment', 'recovery'],
          beforeStateRef: 'snapshot://task-auth/before/ws',
          afterStateRef: 'snapshot://task-auth/after/ws',
          ...(includeProof ? { resyncProofRef: 'resync://task-auth/ws-unavailable-001' } : {})
        },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('incident recovery view', () => {
  it('publishes versioned runbooks and recovery exercises with resync proof coverage', () => {
    const view = createIncidentRecoveryView(createIncidentRecoveryState(true));

    expect(view.summary).toEqual({
      taskCount: 1,
      applicableCount: 1,
      readyCount: 1,
      blockedCount: 0,
      runbookCount: 1,
      exerciseCount: 1,
      proofReadyCount: 1
    });
    expect(view.runbooks).toMatchObject([
      {
        taskId: 'task-auth',
        scenario: 'ws_unavailable',
        runbookRef: 'runbook://incident/ws-unavailable/v1'
      }
    ]);
    expect(view.exercises).toMatchObject([
      {
        taskId: 'task-auth',
        scenario: 'ws_unavailable',
        exerciseRef: 'exercise://task-auth/ws-unavailable-001',
        checklistMissingPhases: [],
        beforeStateRef: 'snapshot://task-auth/before/ws',
        afterStateRef: 'snapshot://task-auth/after/ws',
        resyncProofRef: 'resync://task-auth/ws-unavailable-001',
        isChecklistComplete: true
      }
    ]);
    expect(view.tasks[0]).toMatchObject({
      taskId: 'task-auth',
      isApplicable: true,
      isReady: true,
      scenarios: ['ws_unavailable']
    });
  });

  it('blocks incident scenarios when the recovery checklist or resync proof is incomplete', () => {
    const gate = evaluateTaskIncidentRecoveryGate(createIncidentRecoveryState(false), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReady).toBe(false);
    expect(gate?.issueCodes).toEqual(
      expect.arrayContaining(['INCIDENT_RECOVERY_CHECKLIST_INCOMPLETE', 'INCIDENT_RESYNC_PROOF_MISSING'])
    );
  });
});