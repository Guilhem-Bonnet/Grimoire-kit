import { createTaskUpdateEvent } from '../../src/contracts/events';
import { applyServerEvent, createEmptyGameState } from '../../src/state/game-state';

describe('GameState task updates', () => {
  it('updates task and linked agent state from a single replay event', () => {
    const initialState = createEmptyGameState();

    const nextState = applyServerEvent(
      initialState,
      createTaskUpdateEvent(
        4,
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-amelia'
        },
        {
          timestamp: '2026-04-08T00:00:05.000Z',
          agent: {
            id: 'dev-amelia',
            name: 'Amelia',
            role: 'agent',
            status: 'working',
            roomId: 'build-room',
            position: { x: 8, y: 8 }
          }
        }
      )
    );

    expect(nextState.lastSequenceId).toBe(4);
    expect(nextState.tasks['write-tests']?.status).toBe('in_progress');
    expect(nextState.agents['dev-amelia']?.status).toBe('working');
  });

  it('ignores stale task updates', () => {
    const currentState = applyServerEvent(
      createEmptyGameState(),
      createTaskUpdateEvent(5, {
        id: 'write-tests',
        title: 'Write tests',
        status: 'done',
        assigneeId: 'dev-amelia'
      })
    );

    const staleState = applyServerEvent(
      currentState,
      createTaskUpdateEvent(4, {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'dev-amelia'
      })
    );

    expect(staleState).toBe(currentState);
    expect(staleState.tasks['write-tests']?.status).toBe('done');
  });
});