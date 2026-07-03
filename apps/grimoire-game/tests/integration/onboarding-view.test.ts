import type { GameState } from '../../src/state/game-state';
import { createOnboardingView } from '../../src/state/onboarding-view';

function createBaseState(config: GameState['config'] = {}): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 1,
    hydratedAt: '2026-04-08T00:00:00.000Z',
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
    tasks: {},
    config,
    recentToolCalls: [],
    recentWorkflowSteps: [],
    lastErrors: []
  };
}

describe('onboarding view', () => {
  it('starts automatically on first run only', () => {
    const firstRun = createOnboardingView(createBaseState());
    const subsequentRun = createOnboardingView(
      createBaseState({
        'onboarding.completed': true,
        'onboarding.started': false
      })
    );

    expect(firstRun).toMatchObject({
      isActive: true,
      launchMode: 'automatic',
      currentStepId: 'welcome'
    });
    expect(subsequentRun).toMatchObject({
      isActive: false,
      launchMode: 'inactive',
      currentStepId: null
    });
  });

  it('stops automatic relaunch after a permanent skip while keeping manual replay available', () => {
    const view = createOnboardingView(
      createBaseState({
        'onboarding.completed': true,
        'onboarding.skippedPermanently': true,
        'onboarding.started': false,
        'onboarding.currentStepIndex': 1
      })
    );

    expect(view).toMatchObject({
      isActive: false,
      skippedPermanently: true,
      launchMode: 'inactive',
      currentStepId: null,
      manualReplayAvailable: true
    });
    expect(view.manualReplayEntrypoints).toEqual(['hud', 'help']);
  });

  it('restores the interrupted onboarding step on resume', () => {
    const view = createOnboardingView(
      createBaseState({
        'onboarding.started': true,
        'onboarding.currentStepIndex': 3
      })
    );

    expect(view).toMatchObject({
      isActive: true,
      launchMode: 'resume',
      currentStepId: 'first-investigation',
      currentStepIndex: 3
    });
    expect(view.summary.completedStepCount).toBe(3);
  });

  it('supports a manual replay request from HUD or help even after completion', () => {
    const view = createOnboardingView(
      createBaseState({
        'onboarding.completed': true,
        'onboarding.manualReplayRequested': true,
        'onboarding.currentStepIndex': 0
      })
    );

    expect(view).toMatchObject({
      isActive: true,
      launchMode: 'manual',
      currentStepId: 'welcome'
    });
  });
});