import {
  createConfigUpdate,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import { hydrateGameState, type GameState } from '../../src/state/game-state';
import { createPowerCardsView } from '../../src/state/power-cards-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 20,
    hydratedAt: '2026-04-12T01:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    },
    tasks: {
      'task-power': {
        id: 'task-power',
        title: 'Activate power cards',
        status: 'review',
        assigneeId: 'orch-1'
      }
    },
    config: {
      'powerCards.runtimeSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: false }
      },
      'powerCards.storageSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: false }
      },
      'powerCards.cardGovernance': {
        'power-card.host-review': {
          origin: 'runtime_adapter',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'trusted',
          riskClass: 'high'
        },
        'power-card.branch-guard': {
          origin: 'runtime_ui',
          requiredPolicy: 'elevated',
          trustStatus: 'trusted',
          riskClass: 'critical'
        }
      }
    },
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Power card activation applied',
        detail: 'Host review relay enabled.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-001',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.host-review',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 20,
        timestamp: '2026-04-12T01:00:20.000Z',
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

function createBaseSnapshot(): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt: '2026-04-12T01:10:00.000Z',
    lastSequenceId: 0,
    agents: [
      {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    ],
    tasks: [],
    config: {
      'powerCards.runtimeSnapshot': {},
      'powerCards.storageSnapshot': {}
    },
    recentToolCalls: [],
    recentWorkflowSteps: []
  };
}

describe('power cards view', () => {
  it('projects governed power cards with persistence and visible targets', () => {
    const view = createPowerCardsView(createBaseState());

    expect(view.summary).toEqual({
      cardCount: 2,
      enabledRuntimeCount: 1,
      enabledStorageCount: 1,
      blockedCount: 0,
      divergedCount: 0,
      invalidCount: 0,
      rejectedActivationCount: 0
    });
    expect(view.cards.find((card) => card.cardId === 'power-card.host-review')).toMatchObject({
      pluginId: 'plugin.host-review',
      targetKind: 'room',
      targetId: 'challenge-room',
      runtimeEnabled: true,
      storageEnabled: true,
      persistenceStatus: 'synced',
      origin: 'runtime_adapter',
      requiredPolicy: 'surface_scoped',
      trustStatus: 'trusted',
      lastActivation: {
        allowed: true,
        actorId: 'orch-1'
      }
    });
  });

  it('persists power card activation across config reloads', async () => {
    const adapter = new MockAgentAdapter(createBaseSnapshot());
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };
    const activatedSnapshot = {
      'power-card.host-review': { enabled: true },
      'power-card.branch-guard': { enabled: true }
    };

    await adapter.handleClientEvent(
      createConfigUpdate('req-power-runtime', 'powerCards.runtimeSnapshot', activatedSnapshot, 'cfg-power-runtime'),
      auth
    );
    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-power-storage', 'powerCards.storageSnapshot', activatedSnapshot, 'cfg-power-storage'),
      auth
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type !== 'STATE_SNAPSHOT') {
      return;
    }

    const reloadedState = hydrateGameState(events[0].snapshot, events[0].timestamp);
    const view = createPowerCardsView(reloadedState);

    expect(view.cards.find((card) => card.cardId === 'power-card.host-review')).toMatchObject({
      runtimeEnabled: true,
      storageEnabled: true,
      effectiveEnabled: true,
      persistenceStatus: 'synced'
    });
    expect(view.cards.find((card) => card.cardId === 'power-card.branch-guard')).toMatchObject({
      runtimeEnabled: true,
      storageEnabled: true,
      persistenceStatus: 'synced'
    });
  });

  it('blocks power card activation when trust is blocked and records the rejection', () => {
    const view = createPowerCardsView({
      ...createBaseState(),
      config: {
        ...createBaseState().config,
        'powerCards.cardGovernance': {
          'power-card.host-review': {
            origin: 'runtime_adapter',
            requiredPolicy: 'surface_scoped',
            trustStatus: 'trusted',
            riskClass: 'high'
          },
          'power-card.branch-guard': {
            origin: 'runtime_ui',
            requiredPolicy: 'elevated',
            trustStatus: 'blocked',
            riskClass: 'critical'
          }
        }
      },
      recentWorkflowSteps: [
        {
          step: 'Power card activation blocked',
          detail: 'blocked trust',
          sourceEventType: 'power_card_activation',
          traceId: 'power-002',
          taskId: 'task-power',
          metadata: {
            powerCardId: 'power-card.branch-guard',
            enabled: true,
            allowed: false,
            reason: 'Power card Branch Guard is blocked by trust status blocked.',
            actorId: 'orch-1'
          },
          sequenceId: 21,
          timestamp: '2026-04-12T01:00:21.000Z',
          agentId: 'orch-1'
        }
      ]
    });

    expect(view.summary).toMatchObject({
      blockedCount: 0,
      rejectedActivationCount: 1
    });
    expect(view.cards.find((card) => card.cardId === 'power-card.branch-guard')).toMatchObject({
      trustStatus: 'blocked',
      issueCodes: ['POWER_CARD_ACTIVATION_REJECTED'],
      diagnostic: 'Power card Branch Guard is blocked by trust status blocked.',
      lastActivation: {
        allowed: false,
        requestedEnabled: true
      }
    });
  });
});