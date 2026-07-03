import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import {
  createReconnectHandshake,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence
} from '../../src/contracts/events';
import { applyServerEvent, applyServerEvents, createEmptyGameState } from '../../src/state/game-state';

const initialAgent: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'idle',
  roomId: 'build-room',
  position: { x: 2, y: 3 },
  lastTool: null
};

describe('reconnect handshake flow', () => {
  it('rehydrates from snapshot and applies missed events once', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [initialAgent],
      tasks: [],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    });

    const initialEvents = await adapter.getInitialSnapshot({ principalId: 'orch-1', role: 'orchestrator' });
    const initialState = applyServerEvents(createEmptyGameState(), initialEvents);

    adapter.emitAgentState({
      ...initialAgent,
      status: 'working',
      lastTool: 'runSubagent'
    });

    const replayEvents = await adapter.handleClientEvent(
      createReconnectHandshake('req-5', initialState.lastSequenceId),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    const rehydratedState = applyServerEvents(initialState, replayEvents);

    expect(rehydratedState.lastSequenceId).toBe(1);
    expect(rehydratedState.agents['dev-1']?.status).toBe('working');
    expect(rehydratedState.agents['dev-1']?.lastTool).toBe('runSubagent');

    const replayedAgain = replayEvents.reduce(applyServerEvent, rehydratedState);
    expect(replayedAgain).toBe(rehydratedState);
  });
});