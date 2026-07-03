import type { JsonValue } from '../../src/contracts/events';
import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import { createSurfaceGovernanceView } from '../../src/state/surface-governance-view';

function createBaseState(
  config: Record<string, JsonValue> = {},
  recentWorkflowSteps: readonly WorkflowStepLogEntry[] = []
): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 30,
    hydratedAt: '2026-04-12T00:00:00.000Z',
    agents: {},
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review'
      }
    },
    config,
    recentToolCalls: [],
    recentWorkflowSteps,
    lastErrors: []
  };
}

describe('surface governance view', () => {
  it('projects a canonical inventory of activable runtime surfaces with explicit controls and gates', () => {
    const view = createSurfaceGovernanceView(createBaseState());

    expect(view.summary).toMatchObject({
      surfaceCount: 7,
      blockedSurfaceCount: 0,
      criticalRiskCount: 2
    });
    expect(view.configurationCards.find((card) => card.surfaceId === 'task.transition.done')).toMatchObject({
      category: 'skill',
      mutationSurface: 'task_lifecycle',
      requiredPolicy: 'elevated',
      trustStatus: 'trusted',
      riskClass: 'critical',
      requiredControls: ['verification:chain', 'review:critical-findings'],
      gateRef: 'gate://surface/task.transition.done',
      status: 'ready'
    });
    expect(view.configurationCards.find((card) => card.surfaceId === 'power-card.activate')).toMatchObject({
      category: 'power_card',
      mutationSurface: 'task_lifecycle',
      requiredPolicy: 'surface_scoped',
      riskClass: 'high'
    });
  });

  it('rejects activations with missing metadata or blocked trust status using actionable diagnostics', () => {
    const view = createSurfaceGovernanceView(
      createBaseState(
        {
          surfaceGovernance: {
            overrides: {
              'power-card.activate': {
                trustStatus: 'blocked'
              }
            }
          }
        },
        [
          {
            step: 'Power card activation blocked',
            detail: 'blocked trust',
            sourceEventType: 'surface_activation',
            traceId: 'trace-auth',
            taskId: 'task-auth',
            metadata: {
              activationAction: 'activate',
              surfaceId: 'power-card.activate',
              origin: 'runtime_ui',
              requiredPolicy: 'surface_scoped',
              trustStatus: 'trusted'
            },
            sequenceId: 20,
            timestamp: '2026-04-12T00:00:20.000Z'
          },
          {
            step: 'Tool apply missing policy',
            detail: 'missing policy',
            sourceEventType: 'surface_activation',
            traceId: 'trace-auth',
            taskId: 'task-auth',
            metadata: {
              activationAction: 'activate',
              surfaceId: 'tool.runtime-config.apply',
              origin: 'runtime_ui',
              trustStatus: 'trusted'
            },
            sequenceId: 21,
            timestamp: '2026-04-12T00:00:21.000Z'
          }
        ]
      )
    );

    expect(view.summary).toMatchObject({
      blockedSurfaceCount: 1,
      activationCount: 2,
      blockedActivationCount: 2
    });
    expect(view.activationGates).toMatchObject([
      {
        surfaceId: 'tool.runtime-config.apply',
        allowed: false,
        missingFields: ['requiredPolicy'],
        reason: 'Activation tool.runtime-config.apply is missing requiredPolicy metadata.'
      },
      {
        surfaceId: 'power-card.activate',
        allowed: false,
        missingFields: [],
        reason: 'Execution surface power-card.activate is blocked by trust status policy.'
      }
    ]);
    expect(view.securityAuditFindings.map((finding) => finding.message)).toEqual(expect.arrayContaining([
      'Execution surface power-card.activate is blocked until governance trust is restored.',
      'Activation tool.runtime-config.apply is missing requiredPolicy metadata.',
      'Execution surface power-card.activate is blocked by trust status policy.'
    ]));
  });

  it('reuses the same source of truth for configuration cards and security audit findings', () => {
    const view = createSurfaceGovernanceView(
      createBaseState(
        {
          surfaceGovernance: {
            overrides: {
              'inspection.redirect': {
                requiredPolicy: 'elevated',
                riskClass: 'critical'
              }
            }
          }
        },
        [
          {
            step: 'Inspection redirect allowed',
            detail: 'redirect',
            sourceEventType: 'surface_activation',
            traceId: 'trace-auth',
            taskId: 'task-auth',
            metadata: {
              activationAction: 'activate',
              surfaceId: 'inspection.redirect',
              origin: 'runtime_ui',
              requiredPolicy: 'elevated',
              trustStatus: 'trusted'
            },
            sequenceId: 20,
            timestamp: '2026-04-12T00:00:20.000Z'
          }
        ]
      )
    );

    expect(view.configurationCards.find((card) => card.surfaceId === 'inspection.redirect')).toMatchObject({
      requiredPolicy: 'elevated',
      riskClass: 'critical',
      gateRef: 'gate://surface/inspection.redirect',
      status: 'ready'
    });
    expect(view.activationGates[0]).toMatchObject({
      surfaceId: 'inspection.redirect',
      allowed: true,
      riskClass: 'critical'
    });
    expect(view.securityAuditFindings.find((finding) => finding.surfaceId === 'inspection.redirect')).toBeUndefined();
  });
});