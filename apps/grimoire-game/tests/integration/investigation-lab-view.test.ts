import type { GameState } from '../../src/state/game-state';
import {
  createInvestigationLabView,
  evaluateTaskInvestigationLab
} from '../../src/state/investigation-lab-view';

function createInvestigationLabState(options: {
  includeOpenCritical?: boolean;
  includeFixFailureRun?: boolean;
  includeArchitectureEscalation?: boolean;
} = {}): GameState {
  const {
    includeOpenCritical = false,
    includeFixFailureRun = false,
    includeArchitectureEscalation = false
  } = options;

  return {
    protocolVersion: 'v1',
    lastSequenceId: includeArchitectureEscalation ? 13 : includeFixFailureRun || includeOpenCritical ? 12 : 9,
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
        roomId: 'lab-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-investigation': {
        id: 'task-investigation',
        title: 'Investigate runtime regression',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Root cause identified',
        detail: 'The regression comes from stale auth cache state.',
        sourceEventType: 'decision',
        traceId: 'investigation-001',
        taskId: 'task-investigation',
        metadata: { phase: 'root_cause_identified', topic: 'investigation' },
        sequenceId: 6,
        timestamp: '2026-04-08T00:00:06.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Pattern identified',
        detail: 'The defect only appears on retried writes.',
        sourceEventType: 'decision',
        traceId: 'investigation-001',
        taskId: 'task-investigation',
        metadata: { phase: 'pattern_identified', topic: 'investigation' },
        sequenceId: 7,
        timestamp: '2026-04-08T00:00:07.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Hypothesis validated',
        detail: 'The race reproduces in the replay harness.',
        sourceEventType: 'decision',
        traceId: 'investigation-001',
        taskId: 'task-investigation',
        metadata: { phase: 'hypothesis', topic: 'investigation' },
        sequenceId: 8,
        timestamp: '2026-04-08T00:00:08.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Implementation completed',
        detail: 'The retry guard is ready for review.',
        sourceEventType: 'decision',
        traceId: 'investigation-001',
        taskId: 'task-investigation',
        metadata: { phase: 'implementation_completed', topic: 'investigation' },
        sequenceId: 9,
        timestamp: '2026-04-08T00:00:09.000Z',
        agentId: 'dev-1'
      },
      ...(includeOpenCritical
        ? [
            {
              step: 'Critical review finding',
              detail: 'The regression is still exploitable.',
              sourceEventType: 'review',
              traceId: 'investigation-001',
              taskId: 'task-investigation',
              metadata: { severity: 'critical', status: 'open' },
              sequenceId: 10,
              timestamp: '2026-04-08T00:00:10.000Z',
              agentId: 'dev-1'
            }
          ]
        : []),
      ...(includeFixFailureRun
        ? [
            {
              step: 'Fix failed 1',
              detail: 'The first candidate patch did not hold.',
              sourceEventType: 'decision',
              traceId: 'investigation-001',
              taskId: 'task-investigation',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 10,
              timestamp: '2026-04-08T00:00:10.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix failed 2',
              detail: 'The second candidate patch also failed.',
              sourceEventType: 'decision',
              traceId: 'investigation-001',
              taskId: 'task-investigation',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 11,
              timestamp: '2026-04-08T00:00:11.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix failed 3',
              detail: 'The third candidate patch failed again.',
              sourceEventType: 'decision',
              traceId: 'investigation-001',
              taskId: 'task-investigation',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 12,
              timestamp: '2026-04-08T00:00:12.000Z',
              agentId: 'dev-1'
            },
            ...(includeArchitectureEscalation
              ? [
                  {
                    step: 'Architecture review required',
                    detail: 'The task escalated to architecture review after repeated failures.',
                    sourceEventType: 'decision',
                    traceId: 'investigation-001',
                    taskId: 'task-investigation',
                    metadata: { checkpoint: 'architecture_review_required', topic: 'architecture_review' },
                    sequenceId: 13,
                    timestamp: '2026-04-08T00:00:13.000Z',
                    agentId: 'orch-1'
                  }
                ]
              : [])
          ]
        : [])
    ],
    lastErrors: []
  };
}

describe('investigation lab view', () => {
  it('summarizes ordered debug phases and non-blocking review severities', () => {
    const view = createInvestigationLabView(createInvestigationLabState());

    expect(view.summary).toEqual({
      taskCount: 1,
      applicableCount: 1,
      readyCount: 1,
      blockedCount: 0,
      architectureEscalationCount: 0,
      openCriticalBlockingCount: 0
    });
    expect(view.tasks[0]).toMatchObject({
      taskId: 'task-investigation',
      isApplicable: true,
      isReadyForReviewProgression: true,
      completedPhases: ['root_cause_identified', 'pattern_identified', 'hypothesis', 'implementation_completed'],
      issueCodes: []
    });
  });

  it('blocks review progression while a critical finding remains unresolved', () => {
    const gate = evaluateTaskInvestigationLab(createInvestigationLabState({ includeOpenCritical: true }), 'task-investigation');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForReviewProgression).toBe(false);
    expect(gate?.issueCodes).toContain('INVESTIGATION_OPEN_CRITICAL_FINDING');
    expect(gate?.openCriticalFindingCount).toBe(1);
  });

  it('requires architecture review after three consecutive fix_failed events', () => {
    const gate = evaluateTaskInvestigationLab(createInvestigationLabState({ includeFixFailureRun: true }), 'task-investigation');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForReviewProgression).toBe(false);
    expect(gate?.consecutiveFixFailureCount).toBe(3);
    expect(gate?.issueCodes).toContain('INVESTIGATION_ARCHITECTURE_REVIEW_REQUIRED');
  });

  it('surfaces the latest verification gate reference for investigation review', () => {
    const state = createInvestigationLabState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Verification gate PASS',
        detail: 'task.transition.done: verify://task-investigation/1',
        sourceEventType: 'verification_gate',
        traceId: 'investigation-001',
        taskId: 'task-investigation',
        metadata: {
          verificationRef: 'verify://task-investigation/1',
          verdict: 'PASS',
          correlationId: 'req-investigation-proof-1'
        },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'orch-1'
      }
    ];
    state.lastSequenceId = 14;

    const gate = evaluateTaskInvestigationLab(state, 'task-investigation');

    expect(gate).not.toBeNull();
    expect(gate).toMatchObject({
      latestVerificationRef: 'verify://task-investigation/1',
      latestVerificationVerdict: 'PASS',
      latestVerificationCorrelationId: 'req-investigation-proof-1'
    });
  });
});