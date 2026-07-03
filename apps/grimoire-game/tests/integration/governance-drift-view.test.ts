import type { GameState } from '../../src/state/game-state';
import {
  createGovernanceDriftView,
  evaluateTaskGovernanceDriftGate
} from '../../src/state/governance-drift-view';

function createGovernanceState(driftScore: number): GameState {
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
        roomId: 'policy-lab',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Stabilize auth governance prompts',
        status: 'review',
        priority: 'critical',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: {
          task_id: 'task-auth',
          path: '.github/prompts/auth-runtime.prompt.md'
        },
        sourceEventType: 'artifact_created',
        traceId: 'gov-001',
        sequenceId: 10,
        timestamp: '2026-04-08T00:00:10.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'create_file',
        params: {
          task_id: 'task-auth',
          path: '.github/instructions/runtime-policy.instructions.md'
        },
        sourceEventType: 'artifact_created',
        traceId: 'gov-001',
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Governance canary completed',
        detail: 'Prompt/policy candidate replayed against the canary suite.',
        sourceEventType: 'decision',
        traceId: 'gov-001',
        taskId: 'task-auth',
        metadata: {
          governanceChangeDetected: true,
          governanceVersions: [
            {
              artifactType: 'prompt',
              targetRef: 'prompt://auth/runtime',
              baselineVersion: 'prompt/v1',
              candidateVersion: 'prompt/v2'
            },
            {
              artifactType: 'policy',
              targetRef: 'policy://auth/runtime',
              baselineVersion: 'policy/v4',
              candidateVersion: 'policy/v5'
            }
          ],
          canaryReportRef: 'canary://task-auth/governance-001',
          governanceDriftThreshold: 0.2,
          canaryScenarios: [
            {
              scenarioId: 'scenario-block-runtime-config',
              title: 'Block unsafe runtime_config mutation',
              baselineVerdict: 'BLOCK',
              candidateVerdict: 'BLOCK',
              driftScore: 0
            },
            {
              scenarioId: 'scenario-read-audit',
              title: 'Allow read-only audit import',
              baselineVerdict: 'PASS',
              candidateVerdict: driftScore === 0 ? 'PASS' : 'WARN',
              driftScore,
              diagnostic: 'Candidate prompt drift assessment.'
            }
          ]
        },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

describe('governance drift view', () => {
  it('publishes a version register and scenario-by-scenario canary report for governed changes', () => {
    const view = createGovernanceDriftView(createGovernanceState(0.1));

    expect(view.summary).toEqual({
      taskCount: 1,
      applicableCount: 1,
      readyCount: 1,
      blockedCount: 0,
      versionTraceCount: 2,
      scenarioCount: 2,
      exceededScenarioCount: 0
    });
    expect(view.register).toMatchObject([
      {
        taskId: 'task-auth',
        artifactType: 'prompt',
        targetRef: 'prompt://auth/runtime',
        baselineVersion: 'prompt/v1',
        candidateVersion: 'prompt/v2'
      },
      {
        taskId: 'task-auth',
        artifactType: 'policy',
        targetRef: 'policy://auth/runtime',
        baselineVersion: 'policy/v4',
        candidateVersion: 'policy/v5'
      }
    ]);
    expect(view.reports).toMatchObject([
      {
        taskId: 'task-auth',
        scenarioId: 'scenario-read-audit',
        driftScore: 0.1,
        exceedsThreshold: false,
        reportRef: 'canary://task-auth/governance-001'
      },
      {
        taskId: 'task-auth',
        scenarioId: 'scenario-block-runtime-config',
        driftScore: 0,
        exceedsThreshold: false,
        reportRef: 'canary://task-auth/governance-001'
      }
    ]);
    expect(view.tasks[0]).toMatchObject({
      taskId: 'task-auth',
      isApplicable: true,
      isReady: true,
      reportRef: 'canary://task-auth/governance-001',
      maxDriftScore: 0.1
    });
  });

  it('flags governed prompt changes that are missing version trace or canary report evidence', () => {
    const state = createGovernanceState(0.1);
    const gate = evaluateTaskGovernanceDriftGate(
      {
        ...state,
        recentWorkflowSteps: state.recentWorkflowSteps.map((workflowStep) => ({
          ...workflowStep,
          metadata: {
            governanceChangeDetected: true
          }
        }))
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReady).toBe(false);
    expect(gate?.issueCodes).toEqual(
      expect.arrayContaining(['GOVERNANCE_VERSION_TRACE_MISSING', 'GOVERNANCE_CANARY_REPORT_MISSING'])
    );
  });
});