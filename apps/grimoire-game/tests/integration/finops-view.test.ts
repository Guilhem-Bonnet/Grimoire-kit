import type { AgentPresence } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createFinOpsView, evaluateTaskFinOpsGate } from '../../src/state/finops-view';

const ORCHESTRATOR: AgentPresence = {
  id: 'orch-1',
  name: 'Orchestrator',
  role: 'orchestrator',
  status: 'idle',
  roomId: 'war-room',
  position: { x: 4, y: 4 }
};

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'runTests'
};

function createBaseState(includeExtract: boolean): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: includeExtract ? 15 : 14,
    hydratedAt: '2026-04-11T10:00:00.000Z',
    agents: {
      'orch-1': ORCHESTRATOR,
      'dev-1': DEV_AGENT
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        priority: 'critical',
        assigneeId: 'dev-1'
      },
      'task-docs': {
        id: 'task-docs',
        title: 'Refresh docs',
        status: 'todo',
        priority: 'low',
        assigneeId: 'orch-1'
      }
    },
    config: {
      agentProfiles: {
        'dev-1': {
          model: 'gpt-5.4'
        },
        'orch-1': {
          model: 'gpt-5-mini'
        }
      },
      finops: {
        thresholds: {
          normalizedCostUsd: 0.01,
          normalizedTokens: 400,
          normalizedLatencyMs: 500
        },
        pricing: {
          models: {
            'gpt-5.4': {
              usdPer1kTokens: 0.02
            },
            'gpt-5-mini': {
              usdPer1kTokens: 0.005
            }
          }
        }
      }
    },
    recentToolCalls: [
      {
        tool: 'runTests',
        params: {
          task_id: 'task-auth',
          model: 'gpt-5.4',
          tokensUsed: 1_200,
          latencyMs: 900
        },
        sourceEventType: 'test_run',
        traceId: 'session-001',
        sequenceId: 11,
        timestamp: '2026-04-11T10:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'semantic_search',
        params: {
          task_id: 'task-auth',
          model: 'gpt-5-mini',
          usage: {
            promptTokens: 200,
            completionTokens: 100
          },
          durationMs: 350
        },
        sourceEventType: 'analysis',
        traceId: 'session-001',
        sequenceId: 12,
        timestamp: '2026-04-11T10:00:12.000Z',
        agentId: 'orch-1'
      },
      {
        tool: 'create_file',
        params: {
          task_id: 'task-docs',
          model: 'gpt-5-mini',
          tokensUsed: 40,
          latencyMs: 80,
          costUsd: 0.001
        },
        sourceEventType: 'artifact_created',
        traceId: 'session-002',
        sequenceId: 13,
        timestamp: '2026-04-11T10:00:13.000Z',
        agentId: 'orch-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          complexity: 'expert',
          tokensUsed: 100,
          latencyMs: 120,
          costUsd: 0.002,
          ...(includeExtract
            ? {
                finopsExtractRef: 'finops://task-auth/review-extract-001',
                evidenceRefs: ['finops://task-auth/review-extract-001']
              }
            : {})
        },
        sequenceId: 14,
        timestamp: '2026-04-11T10:00:14.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

describe('finops view', () => {
  it('aggregates cost, token and latency metrics by task, role and model and exposes review and retro digests', () => {
    const view = createFinOpsView(createBaseState(true));
    const authTask = view.tasks.find((task) => task.taskId === 'task-auth');

    expect(view.summary).toMatchObject({
      taskCount: 2,
      metricsReadyCount: 2,
      criticalTaskCount: 1,
      extractMissingCount: 0
    });
    expect(authTask).toMatchObject({
      taskId: 'task-auth',
      complexity: 'expert',
      metricsAvailable: true,
      totalCostUsd: 0.028,
      totalTokens: 1_600,
      totalLatencyMs: 1_370,
      normalizedCostUsd: 0.006,
      normalizedTokens: 320,
      normalizedLatencyMs: 274,
      hasReviewExtract: true,
      reviewExtractRef: 'finops://task-auth/review-extract-001'
    });
    expect(authTask?.roleMetrics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: 'agent', tokens: 1_200, latencyMs: 900, costUsd: 0.024 }),
        expect.objectContaining({ role: 'orchestrator', tokens: 400, latencyMs: 470, costUsd: 0.004 })
      ])
    );
    expect(authTask?.modelMetrics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ model: 'gpt-5.4', costUsd: 0.024, tokens: 1_200 }),
        expect.objectContaining({ model: 'gpt-5-mini', costUsd: 0.004, tokens: 400 })
      ])
    );
    expect(view.reviewQueue).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          taskId: 'task-auth',
          requiresReviewExtract: true,
          hasReviewExtract: true,
          reviewExtractRef: 'finops://task-auth/review-extract-001'
        })
      ])
    );
    expect(view.retroDigest[0]).toMatchObject({
      taskId: 'task-auth',
      normalizedCostUsd: 0.006,
      normalizedTokens: 320,
      normalizedLatencyMs: 274
    });
  });

  it('raises drift alerts and marks critical tasks without a review extract as blocked for FinOps evidence', () => {
    const state = createBaseState(false);
    const gate = evaluateTaskFinOpsGate(state, 'task-auth');
    const view = createFinOpsView(state, {
      thresholds: {
        normalizedCostUsd: 0.005,
        normalizedTokens: 300,
        normalizedLatencyMs: 250
      }
    });
    const alert = view.alerts.find((entry) => entry.taskId === 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate).toMatchObject({
      taskId: 'task-auth',
      metricsAvailable: true,
      hasReviewExtract: false,
      reviewExtractRef: null,
      normalizedCostUsd: 0.006,
      normalizedTokens: 320,
      normalizedLatencyMs: 274
    });
    expect(view.summary.extractMissingCount).toBe(1);
    expect(alert?.driftCodes).toEqual(
      expect.arrayContaining([
        'FINOPS_COST_DRIFT',
        'FINOPS_TOKEN_DRIFT',
        'FINOPS_LATENCY_DRIFT',
        'FINOPS_REVIEW_EXTRACT_MISSING'
      ])
    );
    expect(view.reviewQueue).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          taskId: 'task-auth',
          hasReviewExtract: false,
          driftCodes: expect.arrayContaining(['FINOPS_REVIEW_EXTRACT_MISSING'])
        })
      ])
    );
  });
});
