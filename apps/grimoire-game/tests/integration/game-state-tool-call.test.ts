import { createToolCallEvent } from '../../src/contracts/events';
import { applyServerEvent, createEmptyGameState } from '../../src/state/game-state';

describe('GameState tool call replay', () => {
  it('stores recent tool calls and updates linked agent state', () => {
    const initialState = createEmptyGameState();

    const nextState = applyServerEvent(
      initialState,
      createToolCallEvent(
        5,
        {
          tool: 'create_file',
          params: {
            path: 'src/auth.ts',
            lines: 42
          },
          sourceEventType: 'artifact_created'
        },
        {
          timestamp: '2026-04-08T00:00:06.000Z',
          agent: {
            id: 'dev-amelia',
            name: 'Amelia',
            role: 'agent',
            status: 'working',
            roomId: 'build-room',
            position: { x: 8, y: 8 },
            lastTool: 'create_file'
          }
        }
      )
    );

    expect(nextState.lastSequenceId).toBe(5);
    expect(nextState.agents['dev-amelia']?.lastTool).toBe('create_file');
    expect(nextState.recentToolCalls).toHaveLength(1);
    expect(nextState.recentToolCalls[0]?.tool).toBe('create_file');
    expect(nextState.recentToolCalls[0]?.agentId).toBe('dev-amelia');
  });

  it('ignores stale tool call events', () => {
    const currentState = applyServerEvent(
      createEmptyGameState(),
      createToolCallEvent(6, {
        tool: 'memory',
        params: { edge: 'qa→architect' },
        sourceEventType: 'graph_update'
      })
    );

    const staleState = applyServerEvent(
      currentState,
      createToolCallEvent(5, {
        tool: 'create_file',
        params: { path: 'src/auth.ts' },
        sourceEventType: 'artifact_created'
      })
    );

    expect(staleState).toBe(currentState);
    expect(staleState.recentToolCalls).toHaveLength(1);
    expect(staleState.recentToolCalls[0]?.tool).toBe('memory');
  });
});