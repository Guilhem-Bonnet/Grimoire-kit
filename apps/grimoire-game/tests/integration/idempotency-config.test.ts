import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import {
  createAgentStatusUpdate,
  createConfigUpdate,
  createTaskAssign,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { applyServerEvents, createEmptyGameState } from '../../src/state/game-state';

const initialSnapshot: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-08T00:00:00.000Z',
  lastSequenceId: 0,
  agents: [],
  tasks: [],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

describe('CONFIG_UPDATE idempotence', () => {
  it('applies a config mutation once and dedupes repeated idempotency keys', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const firstResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-1', 'hud.theme', { palette: 'paper' }, 'cfg-1'),
      auth
    );
    const dedupedResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-2', 'hud.theme', { palette: 'neon' }, 'cfg-1'),
      auth
    );

    expect(firstResponse).toHaveLength(1);
    expect(dedupedResponse).toHaveLength(1);
    expect(dedupedResponse[0]?.sequenceId).toBe(firstResponse[0]?.sequenceId);

    const state = applyServerEvents(createEmptyGameState(), firstResponse);

    expect(state.config['hud.theme']).toEqual({ palette: 'paper' });
    expect(adapter.getAuditLog().some((entry) => entry.type === 'CONFIG_APPLIED')).toBe(true);
    expect(adapter.getAuditLog().some((entry) => entry.type === 'CONFIG_DEDUPED')).toBe(true);
  });

  it('isolates idempotency keys by mutation type', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-shared-config', 'hud.theme', { palette: 'paper' }, 'shared-key'),
      auth
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition('req-shared-transition', 'write-tests', 'in_progress', 'shared-key'),
      auth
    );

    expect(configResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...configResponse, ...transitionResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'paper'
      }
    });
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-shared-transition'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' && entry.requestId === 'req-shared-transition'
        )
    ).toBe(false);
  });

  it('isolates idempotency keys by mutation type in reverse order', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition('req-shared-transition-rev', 'write-tests', 'in_progress', 'shared-key-rev'),
      auth
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-shared-config-rev', 'hud.theme', { palette: 'neon' }, 'shared-key-rev'),
      auth
    );

    expect(transitionResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...transitionResponse, ...configResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'neon'
      }
    });
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-shared-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' && entry.requestId === 'req-shared-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config-rev')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for TASK_TRANSITION and AGENT_STATUS_UPDATE', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-shared-transition-status',
        'write-tests',
        'review',
        'shared-transition-status-key'
      ),
      auth
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-shared-agent-status',
        'dev-1',
        'paused',
        'shared-transition-status-key'
      ),
      auth
    );

    expect(transitionResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...transitionResponse, ...statusResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-1'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-shared-transition-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-shared-agent-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-shared-transition-status'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-shared-agent-status'
        )
    ).toBe(false);
  });

  it('isolates shared idempotency keys for AGENT_STATUS_UPDATE then TASK_TRANSITION', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-shared-agent-status-rev',
        'dev-1',
        'paused',
        'shared-status-transition-key'
      ),
      auth
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-shared-transition-status-rev',
        'write-tests',
        'review',
        'shared-status-transition-key'
      ),
      auth
    );

    expect(statusResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...statusResponse, ...transitionResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-1'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-shared-agent-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-shared-transition-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-shared-agent-status-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-shared-transition-status-rev'
        )
    ).toBe(false);
  });

  it('isolates shared idempotency keys for TASK_TRANSITION and TASK_ASSIGN', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-shared-transition-assign',
        'write-tests',
        'review',
        'shared-transition-assign-key'
      ),
      auth
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-shared-assign-transition',
        'write-tests',
        'qa-1',
        'shared-transition-assign-key'
      ),
      auth
    );

    expect(transitionResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...transitionResponse, ...assignResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'qa-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-shared-transition-assign'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-shared-assign-transition'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-shared-transition-assign'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-shared-assign-transition'
        )
    ).toBe(false);
  });

  it('isolates shared idempotency keys for TASK_ASSIGN then TASK_TRANSITION', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-shared-assign-transition-rev',
        'write-tests',
        'qa-1',
        'shared-assign-transition-key'
      ),
      auth
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-shared-transition-assign-rev',
        'write-tests',
        'review',
        'shared-assign-transition-key'
      ),
      auth
    );

    expect(assignResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...assignResponse, ...transitionResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'qa-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-shared-assign-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-shared-transition-assign-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-shared-assign-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-shared-transition-assign-rev'
        )
    ).toBe(false);
  });

  it('isolates shared idempotency keys for TASK_ASSIGN and AGENT_STATUS_UPDATE', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign('req-shared-assign', 'write-tests', 'qa-1', 'shared-assign-status-key'),
      auth
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-shared-status', 'dev-1', 'paused', 'shared-assign-status-key'),
      auth
    );

    expect(assignResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...assignResponse, ...statusResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'todo',
        assigneeId: 'qa-1'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-shared-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-shared-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-shared-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-shared-status')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for AGENT_STATUS_UPDATE then TASK_ASSIGN', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-shared-status-rev', 'dev-1', 'paused', 'shared-status-assign-key'),
      auth
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign('req-shared-assign-rev', 'write-tests', 'qa-1', 'shared-status-assign-key'),
      auth
    );

    expect(statusResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...statusResponse, ...assignResponse]);
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'todo',
        assigneeId: 'qa-1'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-shared-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-shared-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-shared-status-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-shared-assign-rev')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for CONFIG_UPDATE and TASK_ASSIGN', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate(
        'req-shared-config-assign',
        'hud.theme',
        { palette: 'paper' },
        'shared-config-assign-key'
      ),
      auth
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign('req-shared-assign-config', 'write-tests', 'qa-1', 'shared-config-assign-key'),
      auth
    );

    expect(configResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...configResponse, ...assignResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'paper'
      }
    });
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'todo',
        assigneeId: 'qa-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-shared-assign-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-shared-assign-config')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for TASK_ASSIGN then CONFIG_UPDATE', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 2 }
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-shared-assign-config-rev',
        'write-tests',
        'qa-1',
        'shared-assign-config-key'
      ),
      auth
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate(
        'req-shared-config-assign-rev',
        'hud.theme',
        { palette: 'neon' },
        'shared-assign-config-key'
      ),
      auth
    );

    expect(assignResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...assignResponse, ...configResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'neon'
      }
    });
    expect(nextState.tasks).toEqual({
      'write-tests': {
        id: 'write-tests',
        title: 'Write tests',
        status: 'todo',
        assigneeId: 'qa-1'
      }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-shared-assign-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-shared-assign-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config-assign-rev')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for CONFIG_UPDATE and AGENT_STATUS_UPDATE', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ],
      tasks: [],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate(
        'req-shared-config-status',
        'hud.theme',
        { palette: 'paper' },
        'shared-config-status-key'
      ),
      auth
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-shared-status-config',
        'dev-1',
        'paused',
        'shared-config-status-key'
      ),
      auth
    );

    expect(configResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...configResponse, ...statusResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'paper'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-shared-status-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config-status')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-shared-status-config')
    ).toBe(false);
  });

  it('isolates shared idempotency keys for AGENT_STATUS_UPDATE then CONFIG_UPDATE', async () => {
    const adapter = new MockAgentAdapter({
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ],
      tasks: [],
      config: { ...initialSnapshot.config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-shared-status-config-rev',
        'dev-1',
        'paused',
        'shared-status-config-key'
      ),
      auth
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate(
        'req-shared-config-status-rev',
        'hud.theme',
        { palette: 'neon' },
        'shared-status-config-key'
      ),
      auth
    );

    expect(statusResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);

    const nextState = applyServerEvents(createEmptyGameState(), [...statusResponse, ...configResponse]);
    expect(nextState.config).toEqual({
      'hud.theme': {
        palette: 'neon'
      }
    });
    expect(nextState.agents['dev-1']).toMatchObject({
      id: 'dev-1',
      status: 'paused'
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-shared-status-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-shared-config-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-shared-status-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-shared-config-status-rev')
    ).toBe(false);
  });
});