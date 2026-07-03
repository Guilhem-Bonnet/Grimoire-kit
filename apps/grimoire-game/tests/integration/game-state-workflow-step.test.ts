import { createWorkflowStepEvent } from '../../src/contracts/events';
import { applyServerEvent, createEmptyGameState } from '../../src/state/game-state';

describe('GameState workflow steps', () => {
  it('stores recent workflow steps with agent context', () => {
    const initialState = createEmptyGameState();

    const nextState = applyServerEvent(
      initialState,
      createWorkflowStepEvent(
        7,
        {
          step: 'Decision recorded',
          detail: 'auth: JWT RS256 stateless',
          sourceEventType: 'decision',
          traceId: 'session-001',
          metadata: {
            topic: 'auth'
          }
        },
        {
          timestamp: '2026-04-08T00:00:07.000Z',
          agent: {
            id: 'architect-winston',
            name: 'Winston',
            role: 'agent',
            status: 'working',
            roomId: 'design-room',
            position: { x: 8, y: 4 }
          }
        }
      )
    );

    expect(nextState.lastSequenceId).toBe(7);
    expect(nextState.agents['architect-winston']?.status).toBe('working');
    expect(nextState.recentWorkflowSteps).toHaveLength(1);
    expect(nextState.recentWorkflowSteps[0]?.step).toBe('Decision recorded');
    expect(nextState.recentWorkflowSteps[0]?.agentId).toBe('architect-winston');
  });

  it('ignores stale workflow step events', () => {
    const currentState = applyServerEvent(
      createEmptyGameState(),
      createWorkflowStepEvent(8, {
        step: 'Aggregation completed',
        detail: 'Completed 8 tasks with average trust 89',
        sourceEventType: 'aggregation',
        metadata: {
          tasks_completed: 8
        }
      })
    );

    const staleState = applyServerEvent(
      currentState,
      createWorkflowStepEvent(7, {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        metadata: {
          topic: 'auth'
        }
      })
    );

    expect(staleState).toBe(currentState);
    expect(staleState.recentWorkflowSteps).toHaveLength(1);
    expect(staleState.recentWorkflowSteps[0]?.sourceEventType).toBe('aggregation');
  });
});