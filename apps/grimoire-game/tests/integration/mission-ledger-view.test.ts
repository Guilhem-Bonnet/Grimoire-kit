import { createMissionLedgerView } from '../../src/state/mission-ledger-view';
import type { GameState } from '../../src/state/game-state';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 33,
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
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-observability': {
        id: 'task-observability',
        title: 'Add observability panel',
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
        sequenceId: 12,
        timestamp: '2026-04-10T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'runTests',
        params: { query: 'observability panel', task_id: 'task-observability' },
        sourceEventType: 'test_run',
        traceId: 'session-002',
        sequenceId: 31,
        timestamp: '2026-04-10T00:00:31.000Z',
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
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
          verdict: 'PASS',
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 11,
        timestamp: '2026-04-10T00:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Add observability panel',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-observability',
        metadata: {
          intent: 'Add observability panel',
          runId: 'run-002',
          correlationId: 'corr-002'
        },
        sequenceId: 30,
        timestamp: '2026-04-10T00:00:30.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

describe('mission-ledger-view', () => {
  it('projects missions, work items, evidence and verification from runtime state', () => {
    const ledger = createMissionLedgerView(createBaseState());

    expect(ledger.summary).toMatchObject({
      missionCount: 2,
      blockedMissionCount: 0,
      verifyingMissionCount: 1,
      completedMissionCount: 1,
      verificationCount: 1,
      openEscalationCount: 1
    });

    const authMission = ledger.missions.find((mission) => mission.missionId === 'mission:task:task-auth');
    expect(authMission).toMatchObject({
      title: 'Implement auth',
      status: 'completed',
      owner: 'dev-1'
    });
    expect(authMission?.traceRefs).toEqual(['session-001']);

    const observabilityMission = ledger.missions.find(
      (mission) => mission.missionId === 'mission:task:task-observability'
    );
    expect(observabilityMission).toMatchObject({
      title: 'Add observability panel',
      status: 'verifying',
      owner: 'qa-1'
    });

    expect(
      ledger.workItems.some(
        (item) => item.itemId === 'verification:task-auth' && item.status === 'done' && item.verificationRef !== null
      )
    ).toBe(true);
    expect(
      ledger.workItems.some(
        (item) => item.itemId === 'verification:task-observability' && item.status === 'review'
      )
    ).toBe(true);
    expect(ledger.dependencies).toContainEqual({
      dependencyId: 'dep:task:task-auth:verification:task-auth',
      fromItemId: 'task:task-auth',
      toItemId: 'verification:task-auth',
      type: 'requires_verification_of',
      status: 'satisfied'
    });
    expect(ledger.verificationRecords[0]).toMatchObject({
      verificationRef: 'verify://task-auth/runtime-dashboard',
      verdict: 'pass',
      status: 'accepted'
    });
    expect(ledger.evidenceRecords.map((record) => record.evidenceRef)).toContain(
      'tests://grimoire-game/runtime-dashboard#task-auth'
    );
    expect(ledger.assignments).toContainEqual({
      assignmentId: 'assignment:task-auth:dev-1',
      itemId: 'task:task-auth',
      assignee: 'dev-1',
      role: 'owner',
      status: 'released',
      assignedAt: '2026-04-10T00:00:10.000Z',
      releasedAt: '2026-04-10T00:00:12.000Z'
    });
    expect(
      ledger.escalationRecords.some(
        (record) =>
          record.itemId === 'verification:task-observability' &&
          record.reason.includes('Verification gate blocked')
      )
    ).toBe(true);
  });
});