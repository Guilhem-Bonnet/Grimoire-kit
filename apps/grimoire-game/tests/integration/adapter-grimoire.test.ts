import { AdapterGrimoire, type GrimoireAgentRecord } from '../../src/bridge/adapter-grimoire';
import {
  createAgentStatusUpdate,
  type ClientEvent,
  createAgentStateEvent,
  createConfigUpdate,
  createTaskAssign,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot
} from '../../src/contracts/events';

const runtimeAgent: GrimoireAgentRecord = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'idle',
  roomId: 'war-room',
  position: { x: 2, y: 4 },
  lastTool: null
};

const initialAgent: AgentPresence = {
  ...runtimeAgent
};

describe('AdapterGrimoire', () => {
  it('replays bounded events when the runtime source exposes them', async () => {
    const replayEvent = createAgentStateEvent(3, {
      ...initialAgent,
      status: 'working',
      lastTool: 'runSubagent'
    });
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 3,
          agents: [runtimeAgent],
          tasks: [],
          config: { 'hud.theme': 'paper' }
        };
      },
      async readEventsSince(lastSequenceId) {
        return lastSequenceId < 3 ? [replayEvent] : [];
      }
    });

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });
    const upToDate = await adapter.reconnect(3, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toEqual([replayEvent]);
    expect(upToDate).toEqual([]);
  });

  it('falls back to a snapshot when only readAgents is available', async () => {
    const adapter = new AdapterGrimoire({
      async readAgents() {
        return [runtimeAgent];
      }
    });

    const events = await adapter.getInitialSnapshot({ principalId: 'orch-1', role: 'orchestrator' });

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.protocolVersion).toBe(RUNTIME_PROTOCOL_VERSION);
      expect(events[0].snapshot.config).toEqual({});
    }
  });

  it('applies bounded CONFIG_UPDATE events and dedupes idempotency keys', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
        }
      ],
      tasks: [],
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedMutationKeys: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        appliedMutationKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const first = await adapter.handleClientEvent(
      createConfigUpdate('req-1', 'hud.theme', 'neon', 'cfg-1'),
      auth
    );
    const deduped = await adapter.handleClientEvent(
      createConfigUpdate('req-2', 'hud.theme', 'paper', 'cfg-1'),
      auth
    );

    expect(appliedMutationKeys).toEqual(['hud.theme']);
    expect(first).toHaveLength(1);
    expect(deduped).toHaveLength(1);
    expect(deduped[0]?.sequenceId).toBe(first[0]?.sequenceId);
    expect(first[0]?.type).toBe('STATE_SNAPSHOT');
    if (first[0]?.type === 'STATE_SNAPSHOT') {
      expect(first[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
    }

    expect(
      adapter.getAuditLog().some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-1')
    ).toBe(true);
    expect(
      adapter.getAuditLog().some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-2')
    ).toBe(true);
  });

  it('propagates mutation guardrails to runtime write handlers', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    let capturedConfigGuardrail: Extract<ClientEvent, { type: 'CONFIG_UPDATE' }>['guardrail'];
    let capturedTransitionGuardrail: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>['guardrail'];
    let capturedAssignGuardrail: Extract<ClientEvent, { type: 'TASK_ASSIGN' }>['guardrail'];
    let capturedStatusGuardrail: Extract<ClientEvent, { type: 'AGENT_STATUS_UPDATE' }>['guardrail'];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        capturedConfigGuardrail = mutation.guardrail;
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskTransition(mutation) {
        capturedTransitionGuardrail = mutation.guardrail;
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskAssign(mutation) {
        capturedAssignGuardrail = mutation.guardrail;
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:30.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        capturedStatusGuardrail = mutation.guardrail;
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:40.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };
    const configEvent = createConfigUpdate('req-guardrail-config', 'hud.theme', 'neon', 'guardrail-config');
    const transitionEvent = createTaskTransition(
      'req-guardrail-transition',
      'write-tests',
      'in_progress',
      'guardrail-transition'
    );
    const assignEvent = createTaskAssign('req-guardrail-assign', 'write-tests', 'qa-1', 'guardrail-assign');
    const statusEvent = createAgentStatusUpdate(
      'req-guardrail-status',
      'dev-1',
      'paused',
      'guardrail-status'
    );

    await adapter.handleClientEvent(configEvent, auth);
    await adapter.handleClientEvent(transitionEvent, auth);
    await adapter.handleClientEvent(assignEvent, auth);
    await adapter.handleClientEvent(statusEvent, auth);

    expect(capturedConfigGuardrail).toEqual(configEvent.guardrail);
    expect(capturedTransitionGuardrail).toEqual(transitionEvent.guardrail);
    expect(capturedAssignGuardrail).toEqual(assignEvent.guardrail);
    expect(capturedStatusGuardrail).toEqual(statusEvent.guardrail);
  });

  it('evicts oldest processed mutation keys when cache size is bounded', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
        }
      ],
      tasks: [],
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedConfigValues: string[] = [];
    const adapter = new AdapterGrimoire(
      {
        async readSnapshot() {
          return snapshot;
        },
        async applyConfigUpdate(mutation) {
          appliedConfigValues.push(String(mutation.value));
          const sequenceId = snapshot.lastSequenceId + 1;
          snapshot = {
            ...snapshot,
            generatedAt: '2026-04-08T00:00:10.000Z',
            lastSequenceId: sequenceId,
            config: {
              ...snapshot.config,
              [mutation.key]: mutation.value
            }
          };
          return {
            sequenceId,
            snapshot
          };
        }
      },
      { processedMutationCacheMaxEntries: 1 }
    );
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };

    const first = await adapter.handleClientEvent(
      createConfigUpdate('req-evict-1', 'hud.theme', 'neon', 'cfg-evict-1'),
      auth
    );
    const second = await adapter.handleClientEvent(
      createConfigUpdate('req-evict-2', 'hud.theme', 'paper', 'cfg-evict-2'),
      auth
    );
    const replayedAfterEviction = await adapter.handleClientEvent(
      createConfigUpdate('req-evict-3', 'hud.theme', 'midnight', 'cfg-evict-1'),
      auth
    );

    expect(appliedConfigValues).toEqual(['neon', 'paper', 'midnight']);
    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);
    expect(replayedAfterEviction).toHaveLength(1);
    expect(replayedAfterEviction[0]?.sequenceId).toBe((second[0]?.sequenceId ?? 0) + 1);
    expect(replayedAfterEviction[0]?.type).toBe('STATE_SNAPSHOT');
    if (replayedAfterEviction[0]?.type === 'STATE_SNAPSHOT') {
      expect(replayedAfterEviction[0].snapshot.config).toEqual({ 'hud.theme': 'midnight' });
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-evict-3')
    ).toBe(false);
  });

  it('does not dedupe across mutation types when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
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
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedConfigKeys: string[] = [];
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-1', 'hud.theme', 'neon', 'shared-idempotency-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition('req-cross-2', 'write-tests', 'in_progress', 'shared-idempotency-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(appliedTransitions).toEqual(['write-tests:in_progress']);
    expect(configResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);
    expect(transitionResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (transitionResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(transitionResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(transitionResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-1')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-cross-2')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-1')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_TRANSITION_DEDUPED' && entry.requestId === 'req-cross-2')
    ).toBe(false);
  });

  it('does not dedupe across mutation types when idempotency keys are shared in reverse order', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
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
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedConfigKeys: string[] = [];
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition('req-cross-transition-rev', 'write-tests', 'in_progress', 'shared-idempotency-key-rev'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-rev', 'hud.theme', 'neon', 'shared-idempotency-key-rev'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:in_progress']);
    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(transitionResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);
    expect(configResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (configResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(configResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(configResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-cross-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-rev')
    ).toBe(false);
  });

  it('does not dedupe TASK_TRANSITION and AGENT_STATUS_UPDATE when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedTransitions: string[] = [];
    const appliedStatusUpdates: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-status',
        'write-tests',
        'review',
        'shared-transition-status-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-agent-status',
        'dev-1',
        'paused',
        'shared-transition-status-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:review']);
    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(transitionResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);
    expect(statusResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (statusResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(statusResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'dev-1'
        }
      ]);
      expect(statusResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-cross-agent-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-status'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-cross-agent-status'
        )
    ).toBe(false);
  });

  it('does not dedupe AGENT_STATUS_UPDATE and TASK_TRANSITION when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedStatusUpdates: string[] = [];
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-agent-status-rev',
        'dev-1',
        'paused',
        'shared-status-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-status-rev',
        'write-tests',
        'review',
        'shared-status-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(appliedTransitions).toEqual(['write-tests:review']);
    expect(statusResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);
    expect(transitionResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (transitionResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(transitionResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'dev-1'
        }
      ]);
      expect(transitionResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-cross-agent-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-cross-agent-status-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-status-rev'
        )
    ).toBe(false);
  });

  it('does not dedupe TASK_TRANSITION and TASK_ASSIGN when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedTransitions: string[] = [];
    const appliedAssignments: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-assign',
        'write-tests',
        'review',
        'shared-transition-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-transition',
        'write-tests',
        'qa-1',
        'shared-transition-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:review']);
    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(transitionResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((transitionResponse[0]?.sequenceId ?? 0) + 1);
    expect(assignResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (assignResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(assignResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-assign'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-cross-assign-transition'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-assign'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-cross-assign-transition'
        )
    ).toBe(false);
  });

  it('does not dedupe TASK_ASSIGN and TASK_TRANSITION when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedAssignments: string[] = [];
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-transition-rev',
        'write-tests',
        'qa-1',
        'shared-assign-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const transitionResponse = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-assign-rev',
        'write-tests',
        'review',
        'shared-assign-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(appliedTransitions).toEqual(['write-tests:review']);
    expect(assignResponse).toHaveLength(1);
    expect(transitionResponse).toHaveLength(1);
    expect(transitionResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);
    expect(transitionResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (transitionResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(transitionResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-cross-assign-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-assign-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-cross-assign-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-assign-rev'
        )
    ).toBe(false);
  });

  it('does not dedupe TASK_ASSIGN and AGENT_STATUS_UPDATE when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedAssignments: string[] = [];
    const appliedStatusUpdates: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign('req-cross-assign', 'write-tests', 'qa-1', 'shared-assign-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status', 'dev-1', 'paused', 'shared-assign-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(assignResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);
    expect(statusResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (statusResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(statusResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
      expect(statusResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status')
    ).toBe(false);
  });

  it('does not dedupe AGENT_STATUS_UPDATE and TASK_ASSIGN when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedAssignments: string[] = [];
    const appliedStatusUpdates: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status-rev', 'dev-1', 'paused', 'shared-status-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign('req-cross-assign-rev', 'write-tests', 'qa-1', 'shared-status-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(statusResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);
    expect(assignResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (assignResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(assignResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
      expect(assignResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-rev')
    ).toBe(false);
  });

  it('does not dedupe CONFIG_UPDATE and TASK_ASSIGN when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedConfigKeys: string[] = [];
    const appliedAssignments: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-assign', 'hud.theme', 'neon', 'shared-config-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-config',
        'write-tests',
        'qa-1',
        'shared-config-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(configResponse).toHaveLength(1);
    expect(assignResponse).toHaveLength(1);
    expect(assignResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);
    expect(assignResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (assignResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(assignResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(assignResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-config')
    ).toBe(false);
  });

  it('does not dedupe TASK_ASSIGN and CONFIG_UPDATE when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedAssignments: string[] = [];
    const appliedConfigKeys: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const assignResponse = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-config-rev',
        'write-tests',
        'qa-1',
        'shared-assign-config-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-assign-rev', 'hud.theme', 'neon', 'shared-assign-config-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(assignResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((assignResponse[0]?.sequenceId ?? 0) + 1);
    expect(configResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (configResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(configResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(configResponse[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-assign-rev')
    ).toBe(false);
  });

  it('does not dedupe CONFIG_UPDATE and AGENT_STATUS_UPDATE when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        }
      ],
      tasks: [],
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedConfigKeys: string[] = [];
    const appliedStatusUpdates: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-status', 'hud.theme', 'neon', 'shared-config-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status-config', 'dev-1', 'paused', 'shared-config-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(configResponse).toHaveLength(1);
    expect(statusResponse).toHaveLength(1);
    expect(statusResponse[0]?.sequenceId).toBe((configResponse[0]?.sequenceId ?? 0) + 1);
    expect(statusResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (statusResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(statusResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(statusResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-status')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-config')
    ).toBe(false);
  });

  it('does not dedupe AGENT_STATUS_UPDATE and CONFIG_UPDATE when idempotency keys are shared', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        }
      ],
      tasks: [],
      config: { 'hud.theme': 'paper' },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedStatusUpdates: string[] = [];
    const appliedConfigKeys: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      },
      async applyConfigUpdate(mutation) {
        appliedConfigKeys.push(mutation.key);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:20.000Z',
          lastSequenceId: sequenceId,
          config: {
            ...snapshot.config,
            [mutation.key]: mutation.value
          }
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const statusResponse = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status-config-rev', 'dev-1', 'paused', 'shared-status-config-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configResponse = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-status-rev', 'hud.theme', 'neon', 'shared-status-config-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(appliedConfigKeys).toEqual(['hud.theme']);
    expect(statusResponse).toHaveLength(1);
    expect(configResponse).toHaveLength(1);
    expect(configResponse[0]?.sequenceId).toBe((statusResponse[0]?.sequenceId ?? 0) + 1);
    expect(configResponse[0]?.type).toBe('STATE_SNAPSHOT');
    if (configResponse[0]?.type === 'STATE_SNAPSHOT') {
      expect(configResponse[0].snapshot.config).toEqual({ 'hud.theme': 'neon' });
      expect(configResponse[0].snapshot.agents).toContainEqual({
        ...runtimeAgent,
        status: 'paused'
      });
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-status-rev')
    ).toBe(false);
  });

  it('applies bounded TASK_TRANSITION events and dedupes idempotency keys', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const first = await adapter.handleClientEvent(
      createTaskTransition('req-task-1', 'write-tests', 'in_progress', 'task-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const deduped = await adapter.handleClientEvent(
      createTaskTransition('req-task-2', 'write-tests', 'review', 'task-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:in_progress']);
    expect(first).toHaveLength(1);
    expect(deduped).toHaveLength(1);
    expect(deduped[0]?.sequenceId).toBe(first[0]?.sequenceId);
    expect(first[0]?.type).toBe('STATE_SNAPSHOT');
    if (first[0]?.type === 'STATE_SNAPSHOT') {
      expect(first[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-task-1')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_TRANSITION_DEDUPED' && entry.requestId === 'req-task-2')
    ).toBe(true);
  });

  it('applies bounded TASK_ASSIGN events and dedupes idempotency keys', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent
        },
        {
          id: 'qa-1',
          name: 'Quinn',
          role: 'agent',
          status: 'idle',
          roomId: 'qa-room',
          position: { x: 4, y: 4 },
          lastTool: null
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedAssignments: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskAssign(mutation) {
        appliedAssignments.push(`${mutation.taskId}:${mutation.assigneeId}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  assigneeId: mutation.assigneeId
                }
              : task
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const first = await adapter.handleClientEvent(
      createTaskAssign('req-assign-1', 'write-tests', 'qa-1', 'assign-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const deduped = await adapter.handleClientEvent(
      createTaskAssign('req-assign-2', 'write-tests', 'dev-1', 'assign-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedAssignments).toEqual(['write-tests:qa-1']);
    expect(first).toHaveLength(1);
    expect(deduped).toHaveLength(1);
    expect(deduped[0]?.sequenceId).toBe(first[0]?.sequenceId);
    expect(first[0]?.type).toBe('STATE_SNAPSHOT');
    if (first[0]?.type === 'STATE_SNAPSHOT') {
      expect(first[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-assign-1')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-assign-2')
    ).toBe(true);
  });

  it('applies bounded AGENT_STATUS_UPDATE events and dedupes idempotency keys', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 3,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        }
      ],
      tasks: [],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    const appliedStatusUpdates: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyAgentStatusUpdate(mutation) {
        appliedStatusUpdates.push(`${mutation.agentId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:10.000Z',
          lastSequenceId: sequenceId,
          agents: snapshot.agents.map((agent) =>
            agent.id === mutation.agentId
              ? {
                  ...agent,
                  status: mutation.status
                }
              : agent
          )
        };
        return {
          sequenceId,
          snapshot
        };
      }
    });

    const first = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-1', 'dev-1', 'paused', 'agent-status-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const deduped = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-2', 'dev-1', 'working', 'agent-status-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedStatusUpdates).toEqual(['dev-1:paused']);
    expect(first).toHaveLength(1);
    expect(deduped).toHaveLength(1);
    expect(deduped[0]?.sequenceId).toBe(first[0]?.sequenceId);
    expect(first[0]?.type).toBe('STATE_SNAPSHOT');
    if (first[0]?.type === 'STATE_SNAPSHOT') {
      expect(first[0].snapshot.agents).toEqual([
        {
          ...runtimeAgent,
          status: 'paused'
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-agent-status-1')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-agent-status-2')
    ).toBe(true);
  });

  it('rejects TASK_TRANSITION when task is unknown', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 7,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-unknown', 'missing-task', 'review', 'task-missing'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(8);
      expect(events[0].code).toBe('NOT_FOUND');
      expect(events[0].correlationId).toBe('req-task-unknown');
    }
  });

  it('rejects TASK_ASSIGN when task is unknown', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 8,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskAssign('req-assign-unknown-task', 'missing-task', 'dev-1', 'assign-missing-task'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(9);
      expect(events[0].code).toBe('NOT_FOUND');
      expect(events[0].correlationId).toBe('req-assign-unknown-task');
    }
  });

  it('rejects TASK_ASSIGN when assignee is unknown', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 9,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'todo',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskAssign('req-assign-unknown-agent', 'write-tests', 'qa-999', 'assign-missing-agent'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(10);
      expect(events[0].code).toBe('NOT_FOUND');
      expect(events[0].correlationId).toBe('req-assign-unknown-agent');
    }
  });

  it('rejects AGENT_STATUS_UPDATE when agent is unknown', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 10,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-unknown', 'missing-agent', 'paused', 'agent-status-missing'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(11);
      expect(events[0].code).toBe('NOT_FOUND');
      expect(events[0].correlationId).toBe('req-agent-status-unknown');
    }
  });

  it('rejects AGENT_STATUS_UPDATE outside bounded pause/resume graph', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 11,
          agents: [
            {
              ...runtimeAgent,
              status: 'paused'
            }
          ],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-forbidden', 'dev-1', 'paused', 'agent-status-forbidden'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(12);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].correlationId).toBe('req-agent-status-forbidden');
    }
  });

  it('rejects TASK_TRANSITION outside bounded transition graph', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 10,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'todo',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-forbidden', 'write-tests', 'done', 'task-forbidden'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(11);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].correlationId).toBe('req-task-forbidden');
    }
  });

  it('rejects TASK_TRANSITION in_progress -> review when investigation gate is not satisfied', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 20,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'in_progress',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: [
            {
              step: 'Fix proposed too early',
              detail: 'Candidate patch prepared before root cause analysis',
              sourceEventType: 'decision',
              traceId: 'trace-review-blocked-001',
              taskId: 'write-tests',
              metadata: { phase: 'fix_proposed', topic: 'investigation' },
              sequenceId: 19,
              timestamp: '2026-04-08T00:00:19.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-review-gate-blocked', 'write-tests', 'review', 'task-review-gate-blocked'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(21);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to review before investigation evidence is complete');
      expect(events[0].message).toContain('TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED');
      expect(events[0].correlationId).toBe('req-task-review-gate-blocked');
    }
  });

  it('rejects TASK_TRANSITION in_progress -> review when obsolete memory recall exceeds the threshold', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 30,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'in_progress',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: [
            {
              step: 'Root cause identified',
              detail: 'Review keeps pulling obsolete auth notes',
              sourceEventType: 'decision',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: { phase: 'root_cause_identified', topic: 'investigation' },
              sequenceId: 25,
              timestamp: '2026-04-08T00:00:25.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Pattern identified',
              detail: 'Obsolete memory resurfaces during review.',
              sourceEventType: 'decision',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: { phase: 'pattern_identified', topic: 'investigation' },
              sequenceId: 26,
              timestamp: '2026-04-08T00:00:26.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Hypothesis validated',
              detail: 'Expired refs are reused after handoff.',
              sourceEventType: 'decision',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: { phase: 'hypothesis', topic: 'investigation' },
              sequenceId: 27,
              timestamp: '2026-04-08T00:00:27.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Implementation completed',
              detail: 'Recall instrumentation merged.',
              sourceEventType: 'decision',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: { phase: 'implementation_completed', topic: 'investigation' },
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix proposed',
              detail: 'Ready for review handoff.',
              sourceEventType: 'decision',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: { phase: 'fix_proposed', topic: 'investigation' },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Review imported obsolete memory',
              detail: 'Review still references expired auth guidance.',
              sourceEventType: 'review',
              traceId: 'trace-review-memory-001',
              taskId: 'write-tests',
              metadata: {
                memoryAccess: 'read',
                contentRefs: ['memory://auth/obsolete-guardrail'],
                correlationId: 'corr-review-memory-1'
              },
              sequenceId: 30,
              timestamp: '2026-04-08T00:00:30.000Z',
              agentId: 'dev-1'
            }
          ],
          hostBindings: {
            'host-memory': {
              sequenceId: 21,
              timestamp: '2026-04-08T00:00:21.000Z',
              binding: {
                hostId: 'host-memory',
                hostType: 'mcp',
                displayName: 'Mnemo Archive',
                authMode: 'delegated',
                connectionState: 'online',
                trustStatus: 'trusted',
                scopes: ['fs'],
                capabilityManifestRef: 'manifest://host-memory',
                sourceOfTruth: 'secondary',
                lastSeenAt: '2026-04-08T00:00:21.000Z'
              },
              manifest: {
                manifestId: 'manifest://host-memory',
                hostId: 'host-memory',
                routines: ['memory.search'],
                toolProviders: ['grimoire-memory'],
                reviewChannels: ['review-import'],
                contextSources: ['memory'],
                permissionMode: 'policy',
                supportsStreaming: false,
                supportsReviewImport: true,
                supportsContextImport: true,
                supportsPreviewCommit: false
              }
            }
          },
          recentHostContextEntries: [
            {
              sequenceId: 22,
              timestamp: '2026-04-08T00:00:22.000Z',
              entry: {
                entryId: 'ctx-obsolete-1',
                hostId: 'host-memory',
                sourceType: 'memory',
                visibility: 'shared',
                confidence: 7.6,
                importedAt: '2026-04-08T00:00:00.000Z',
                ttlSeconds: 10,
                contentRef: 'memory://auth/obsolete-guardrail',
                trustStatus: 'review'
              },
              meta: {
                traceId: 'trace-review-memory-001',
                taskId: 'write-tests',
                correlationId: 'corr-review-memory-1',
                hostId: 'host-memory'
              }
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-review-obsolete-memory', 'write-tests', 'review', 'task-review-obsolete-memory'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(31);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to review before investigation evidence is complete');
      expect(events[0].message).toContain('TASK_OBSOLESCENCE_RATE_WITHIN_THRESHOLD');
      expect(events[0].correlationId).toBe('req-task-review-obsolete-memory');
    }
  });

  it('rejects TASK_TRANSITION in_progress -> review after repeated fix failures without architecture escalation', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 32,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'in_progress',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: [
            {
              step: 'Root cause identified',
              detail: 'Found the regression source.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { phase: 'root_cause_identified', topic: 'investigation' },
              sequenceId: 26,
              timestamp: '2026-04-08T00:00:26.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Pattern identified',
              detail: 'The issue only appears on retried writes.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { phase: 'pattern_identified', topic: 'investigation' },
              sequenceId: 27,
              timestamp: '2026-04-08T00:00:27.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Hypothesis validated',
              detail: 'The race reproduced in harness.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { phase: 'hypothesis', topic: 'investigation' },
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Implementation completed',
              detail: 'Candidate fix is ready.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { phase: 'implementation_completed', topic: 'investigation' },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix failed 1',
              detail: 'First candidate patch failed.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 30,
              timestamp: '2026-04-08T00:00:30.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix failed 2',
              detail: 'Second candidate patch failed.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Fix failed 3',
              detail: 'Third candidate patch failed.',
              sourceEventType: 'decision',
              traceId: 'trace-review-architecture-001',
              taskId: 'write-tests',
              metadata: { outcome: 'fix_failed', topic: 'investigation' },
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-review-architecture', 'write-tests', 'review', 'task-review-architecture'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(33);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to review before investigation evidence is complete');
      expect(events[0].message).toContain('TASK_ARCHITECTURE_REVIEW_TRIGGERED_AFTER_REPEAT_FIX_FAILURES');
      expect(events[0].correlationId).toBe('req-task-review-architecture');
    }
  });

  it('allows TASK_TRANSITION in_progress -> review when investigation gate is satisfied', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 50,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
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
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: [
        {
          step: 'Root cause identified',
          detail: 'Failure caused by missing retry guard',
          sourceEventType: 'decision',
          traceId: 'trace-review-ok-001',
          taskId: 'write-tests',
          metadata: { phase: 'root_cause_identified', topic: 'investigation' },
          sequenceId: 45,
          timestamp: '2026-04-08T00:00:45.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Pattern identified',
          detail: 'Issue appears on burst traffic',
          sourceEventType: 'decision',
          traceId: 'trace-review-ok-001',
          taskId: 'write-tests',
          metadata: { phase: 'pattern_identified', topic: 'investigation' },
          sequenceId: 46,
          timestamp: '2026-04-08T00:00:46.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Hypothesis confirmed',
          detail: 'Race condition confirmed in replay',
          sourceEventType: 'decision',
          traceId: 'trace-review-ok-001',
          taskId: 'write-tests',
          metadata: { phase: 'hypothesis', topic: 'investigation' },
          sequenceId: 47,
          timestamp: '2026-04-08T00:00:47.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Implementation completed',
          detail: 'Retry guard applied',
          sourceEventType: 'decision',
          traceId: 'trace-review-ok-001',
          taskId: 'write-tests',
          metadata: { phase: 'implementation_completed', topic: 'investigation' },
          sequenceId: 48,
          timestamp: '2026-04-08T00:00:48.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Fix proposed',
          detail: 'Ready for review handoff',
          sourceEventType: 'decision',
          traceId: 'trace-review-ok-001',
          taskId: 'write-tests',
          metadata: { phase: 'fix_proposed', topic: 'investigation' },
          sequenceId: 49,
          timestamp: '2026-04-08T00:00:49.000Z',
          agentId: 'dev-1'
        }
      ]
    };
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:51.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };

        return {
          sequenceId,
          snapshot
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-review-gate-ok', 'write-tests', 'review', 'task-review-gate-ok'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:review']);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].sequenceId).toBe(51);
      expect(events[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'dev-1'
        }
      ]);
    }
  });

  it('rejects TASK_TRANSITION review -> done when verification gate is not satisfied', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 30,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-no-proof', 'write-tests', 'done', 'task-done-no-proof'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(31);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_HAS_ACTIVITY');
      expect(events[0].message).toContain('TASK_HAS_TRACE');
      expect(events[0].message).toContain('TASK_HAS_ACTIONABLE_EVIDENCE');
      expect(events[0].correlationId).toBe('req-task-done-no-proof');
    }
  });

  it('rejects TASK_TRANSITION review -> done for critical tasks when the counter-review protocol is missing', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 30,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              priority: 'critical',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: { task_id: 'write-tests' },
              sourceEventType: 'tool_call',
              traceId: 'trace-critical-001',
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before done transition',
              sourceEventType: 'decision',
              traceId: 'trace-critical-001',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/critical',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#critical-counter-review'],
                verdict: 'PASS'
              },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-counter-review', 'write-tests', 'done', 'task-done-critical-counter-review'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(31);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_CRITICAL_COUNTER_REVIEW_COMPLETE');
      expect(events[0].correlationId).toBe('req-task-done-critical-counter-review');
    }
  });

  it('rejects TASK_TRANSITION review -> done for critical tasks when the structured decision card is missing', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 36,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              priority: 'critical',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: {
                task_id: 'write-tests',
                model: 'gpt-5.4',
                tokensUsed: 1_200,
                latencyMs: 900,
                costUsd: 0.024
              },
              sourceEventType: 'test_run',
              traceId: 'trace-critical-003',
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before done transition',
              sourceEventType: 'decision',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/critical-card',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#critical-decision-card'],
                verdict: 'PASS'
              },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Presentation opened',
              detail: 'Critical test pack opened for orthogonal review.',
              sourceEventType: 'challenge_presentation',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-2',
                challengePhase: 'presentation',
                challengeRole: 'presenter',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Question asked',
              detail: 'What validates the replay claim?',
              sourceEventType: 'challenge_question',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-2',
                challengePhase: 'questions',
                challengeRole: 'reviewer',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Critical objection raised',
              detail: 'Attach the replay proof before merge.',
              sourceEventType: 'challenge_critique',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-2',
                challengePhase: 'critiques',
                challengeRole: 'critic',
                objectionId: 'obj-proof',
                objectionSeverity: 'high',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Vote recorded',
              detail: 'Proceed once the objection is resolved.',
              sourceEventType: 'challenge_vote',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-2',
                challengePhase: 'vote',
                challengeRole: 'voter',
                vote: 'approve',
                score: 90,
                challengeVerdict: 'approved',
                linkedTaskIds: ['write-tests'],
                linkedObjectionIds: ['obj-proof']
              },
              sequenceId: 34,
              timestamp: '2026-04-08T00:00:34.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Iteration closed',
              detail: 'Objection resolved and challenge approved.',
              sourceEventType: 'challenge_iteration',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-2',
                challengePhase: 'iteration',
                challengeRole: 'moderator',
                challengeVerdict: 'approved',
                resolvedObjectionIds: ['obj-proof'],
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 35,
              timestamp: '2026-04-08T00:00:35.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'FinOps extract attached',
              detail: 'Cost, token and latency extract attached to the review proof.',
              sourceEventType: 'decision',
              traceId: 'trace-critical-003',
              taskId: 'write-tests',
              metadata: {
                finopsExtractRef: 'finops://write-tests/review-extract-001',
                evidenceRefs: ['finops://write-tests/review-extract-001'],
                tokensUsed: 100,
                latencyMs: 120,
                costUsd: 0.002,
                complexity: 'expert'
              },
              sequenceId: 36,
              timestamp: '2026-04-08T00:00:36.000Z',
              agentId: 'orch-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-card', 'write-tests', 'done', 'task-done-critical-card'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(37);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_CRITICAL_DECISION_CARD_COMPLETE');
      expect(events[0].correlationId).toBe('req-task-done-critical-card');
    }
  });

  it('rejects TASK_TRANSITION review -> done for critical tasks when the FinOps extract is missing', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 35,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              priority: 'critical',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: {
                task_id: 'write-tests',
                model: 'gpt-5.4',
                tokensUsed: 1_200,
                latencyMs: 900,
                costUsd: 0.024
              },
              sourceEventType: 'test_run',
              traceId: 'trace-critical-002',
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before done transition',
              sourceEventType: 'decision',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/critical',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#critical-finops'],
                verdict: 'PASS'
              },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Presentation opened',
              detail: 'Critical test pack opened for orthogonal review.',
              sourceEventType: 'challenge_presentation',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-1',
                challengePhase: 'presentation',
                challengeRole: 'presenter',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Question asked',
              detail: 'What validates the replay claim?',
              sourceEventType: 'challenge_question',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-1',
                challengePhase: 'questions',
                challengeRole: 'reviewer',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Critical objection raised',
              detail: 'Attach the replay proof before merge.',
              sourceEventType: 'challenge_critique',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-1',
                challengePhase: 'critiques',
                challengeRole: 'critic',
                objectionId: 'obj-proof',
                objectionSeverity: 'high',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Vote recorded',
              detail: 'Proceed once the objection is resolved.',
              sourceEventType: 'challenge_vote',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-1',
                challengePhase: 'vote',
                challengeRole: 'voter',
                vote: 'approve',
                score: 90,
                challengeVerdict: 'approved',
                linkedTaskIds: ['write-tests'],
                linkedObjectionIds: ['obj-proof']
              },
              sequenceId: 34,
              timestamp: '2026-04-08T00:00:34.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Iteration closed',
              detail: 'Objection resolved and challenge approved.',
              sourceEventType: 'challenge_iteration',
              traceId: 'trace-critical-002',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-1',
                challengePhase: 'iteration',
                challengeRole: 'moderator',
                challengeVerdict: 'approved',
                resolvedObjectionIds: ['obj-proof'],
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 35,
              timestamp: '2026-04-08T00:00:35.000Z',
              agentId: 'orch-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-finops', 'write-tests', 'done', 'task-done-critical-finops'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(36);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_CRITICAL_FINOPS_EXTRACT_PRESENT');
      expect(events[0].correlationId).toBe('req-task-done-critical-finops');
    }
  });

  it('rejects TASK_TRANSITION review -> done for critical tasks when prompt/policy drift exceeds threshold', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 37,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              priority: 'critical',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: {
                task_id: 'write-tests',
                model: 'gpt-5.4',
                tokensUsed: 1_200,
                latencyMs: 900,
                costUsd: 0.024
              },
              sourceEventType: 'test_run',
              traceId: 'trace-critical-004',
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            },
            {
              tool: 'create_file',
              params: {
                task_id: 'write-tests',
                path: '.github/prompts/auth-runtime.prompt.md'
              },
              sourceEventType: 'artifact_created',
              traceId: 'trace-critical-004',
              sequenceId: 30,
              timestamp: '2026-04-08T00:00:30.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before done transition',
              sourceEventType: 'decision',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/critical-governance',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#critical-governance'],
                verdict: 'PASS',
                context: 'Prompt/policy update is ready for closeout.',
                options: ['delay the rollout', 'ship the updated governance assets'],
                selectedOption: 'ship the updated governance assets',
                rationale: 'All review artifacts except drift remain complete.',
                impact: 'Unlocks the updated governance guardrails for the next review cycle.'
              },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Presentation opened',
              detail: 'Critical test pack opened for orthogonal review.',
              sourceEventType: 'challenge_presentation',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-4',
                challengePhase: 'presentation',
                challengeRole: 'presenter',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Question asked',
              detail: 'What validates the policy drift score?',
              sourceEventType: 'challenge_question',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-4',
                challengePhase: 'questions',
                challengeRole: 'reviewer',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Critical objection raised',
              detail: 'The canary drift must stay below the configured threshold.',
              sourceEventType: 'challenge_critique',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-4',
                challengePhase: 'critiques',
                challengeRole: 'critic',
                objectionId: 'obj-governance-drift',
                objectionSeverity: 'high',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Vote recorded',
              detail: 'Proceed once the governance canary passes.',
              sourceEventType: 'challenge_vote',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-4',
                challengePhase: 'vote',
                challengeRole: 'voter',
                vote: 'approve',
                score: 93,
                challengeVerdict: 'approved',
                linkedTaskIds: ['write-tests'],
                linkedObjectionIds: ['obj-governance-drift']
              },
              sequenceId: 34,
              timestamp: '2026-04-08T00:00:34.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Iteration closed',
              detail: 'Challenge completed while governance canary remains above threshold.',
              sourceEventType: 'challenge_iteration',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-4',
                challengePhase: 'iteration',
                challengeRole: 'moderator',
                challengeVerdict: 'approved',
                resolvedObjectionIds: ['obj-governance-drift'],
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 35,
              timestamp: '2026-04-08T00:00:35.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'FinOps extract attached',
              detail: 'Cost, token and latency extract attached to the review proof.',
              sourceEventType: 'decision',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
              metadata: {
                finopsExtractRef: 'finops://write-tests/review-extract-002',
                evidenceRefs: ['finops://write-tests/review-extract-002'],
                tokensUsed: 100,
                latencyMs: 120,
                costUsd: 0.002,
                complexity: 'expert'
              },
              sequenceId: 36,
              timestamp: '2026-04-08T00:00:36.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Governance canary completed',
              detail: 'Prompt/policy candidate replayed against the canary suite.',
              sourceEventType: 'decision',
              traceId: 'trace-critical-004',
              taskId: 'write-tests',
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
                canaryReportRef: 'canary://write-tests/governance-001',
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
                    candidateVerdict: 'WARN',
                    driftScore: 0.35,
                    diagnostic: 'Candidate prompt drift assessment.'
                  }
                ]
              },
              sequenceId: 37,
              timestamp: '2026-04-08T00:00:37.000Z',
              agentId: 'orch-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-governance', 'write-tests', 'done', 'task-done-critical-governance'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(38);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD');
      expect(events[0].correlationId).toBe('req-task-done-critical-governance');
    }
  });

  it('rejects TASK_TRANSITION review -> done for critical tasks when the recovery proof is incomplete', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 38,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              priority: 'critical',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: {
                task_id: 'write-tests',
                model: 'gpt-5.4',
                tokensUsed: 1_200,
                latencyMs: 900,
                costUsd: 0.024
              },
              sourceEventType: 'test_run',
              traceId: 'trace-critical-005',
              sequenceId: 28,
              timestamp: '2026-04-08T00:00:28.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before done transition',
              sourceEventType: 'decision',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/critical-recovery',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#critical-recovery'],
                verdict: 'PASS',
                context: 'Recovery proof is being attached before closeout.',
                options: ['ship after recovery proof', 'hold until resync is verified'],
                selectedOption: 'ship after recovery proof',
                rationale: 'All non-recovery checks are complete.',
                impact: 'Allows the critical task to close once recovery evidence is compliant.'
              },
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Presentation opened',
              detail: 'Critical test pack opened for orthogonal review.',
              sourceEventType: 'challenge_presentation',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-5',
                challengePhase: 'presentation',
                challengeRole: 'presenter',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Question asked',
              detail: 'What proves the resync completed correctly?',
              sourceEventType: 'challenge_question',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-5',
                challengePhase: 'questions',
                challengeRole: 'reviewer',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Critical objection raised',
              detail: 'Attach the resync proof before merge.',
              sourceEventType: 'challenge_critique',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-5',
                challengePhase: 'critiques',
                challengeRole: 'critic',
                objectionId: 'obj-recovery-proof',
                objectionSeverity: 'high',
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'review-1'
            },
            {
              step: 'Vote recorded',
              detail: 'Proceed once the recovery proof is attached.',
              sourceEventType: 'challenge_vote',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-5',
                challengePhase: 'vote',
                challengeRole: 'voter',
                vote: 'approve',
                score: 92,
                challengeVerdict: 'approved',
                linkedTaskIds: ['write-tests'],
                linkedObjectionIds: ['obj-recovery-proof']
              },
              sequenceId: 34,
              timestamp: '2026-04-08T00:00:34.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Iteration closed',
              detail: 'Challenge completed while recovery proof remains incomplete.',
              sourceEventType: 'challenge_iteration',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                challengeId: 'challenge-write-tests-5',
                challengePhase: 'iteration',
                challengeRole: 'moderator',
                challengeVerdict: 'approved',
                resolvedObjectionIds: ['obj-recovery-proof'],
                linkedTaskIds: ['write-tests']
              },
              sequenceId: 35,
              timestamp: '2026-04-08T00:00:35.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'FinOps extract attached',
              detail: 'Cost, token and latency extract attached to the review proof.',
              sourceEventType: 'decision',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                finopsExtractRef: 'finops://write-tests/review-extract-003',
                evidenceRefs: ['finops://write-tests/review-extract-003'],
                tokensUsed: 100,
                latencyMs: 120,
                costUsd: 0.002,
                complexity: 'expert'
              },
              sequenceId: 36,
              timestamp: '2026-04-08T00:00:36.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Incident declared',
              detail: 'Websocket transport became unavailable during replay.',
              sourceEventType: 'incident',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                incidentType: 'ws_unavailable',
                runbookRef: 'runbook://incident/ws-unavailable/v1'
              },
              sequenceId: 37,
              timestamp: '2026-04-08T00:00:37.000Z',
              agentId: 'orch-1'
            },
            {
              step: 'Recovery exercise completed',
              detail: 'Recovery checklist executed but resync proof is still missing.',
              sourceEventType: 'recovery',
              traceId: 'trace-critical-005',
              taskId: 'write-tests',
              metadata: {
                incidentType: 'ws_unavailable',
                exerciseRef: 'exercise://write-tests/ws-unavailable-001',
                recoveryChecklist: ['detection', 'containment', 'recovery'],
                beforeStateRef: 'snapshot://write-tests/before/ws',
                afterStateRef: 'snapshot://write-tests/after/ws'
              },
              sequenceId: 38,
              timestamp: '2026-04-08T00:00:38.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-recovery', 'write-tests', 'done', 'task-done-critical-recovery'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(39);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE');
      expect(events[0].correlationId).toBe('req-task-done-critical-recovery');
    }
  });

  it('rejects TASK_TRANSITION review -> done when experimentation closeout has no explicit decision', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 31,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: { task_id: 'write-tests' },
              sourceEventType: 'test_run',
              traceId: 'trace-experiment-001',
              sequenceId: 29,
              timestamp: '2026-04-08T00:00:29.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Evidence collected',
              detail: 'Tests passed before the done transition.',
              sourceEventType: 'decision',
              traceId: 'trace-experiment-001',
              taskId: 'write-tests',
              metadata: {
                topic: 'verification',
                actionId: 'task.transition.done',
                verificationRef: 'verify://write-tests/experiment-closeout',
                controlsExecuted: ['tests:unit'],
                evidenceRefs: ['tests://grimoire-game/adapter-grimoire#experiment-closeout'],
                verdict: 'PASS',
                context: 'All engineering checks are green before product experimentation closeout.',
                options: ['ship the slice', 'wait for product decision'],
                selectedOption: 'wait for product decision',
                rationale: 'Shipping is blocked until the product experiment is explicitly closed.',
                impact: 'Prevents backlog drift after the experiment completes.'
              },
              sequenceId: 30,
              timestamp: '2026-04-08T00:00:30.000Z',
              agentId: 'dev-1'
            },
            {
              step: 'Experiment measured',
              detail: 'The activation uplift was measured but no closeout decision was logged.',
              sourceEventType: 'decision',
              traceId: 'trace-experiment-001',
              taskId: 'write-tests',
              metadata: {
                topic: 'experiment',
                experimentId: 'exp-write-tests-001',
                experimentTheme: 'onboarding',
                hypothesis: 'Reducing ceremony should improve completion.',
                experimentMetric: 'completion_rate',
                experimentGuardrail: 'support_tickets <= baseline + 1',
                measurementRef: 'measure://write-tests/exp-001'
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-experiment', 'write-tests', 'done', 'task-done-experiment'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(32);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_EXPERIMENT_DECISION_COMPLETE');
      expect(events[0].correlationId).toBe('req-task-done-experiment');
    }
  });

  it('rejects TASK_TRANSITION review -> done when a critical review finding is still open', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 34,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: { task_id: 'write-tests' },
              sourceEventType: 'tool_call',
              traceId: 'trace-002',
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Critical review finding',
              detail: 'Blocking issue still open',
              sourceEventType: 'review',
              traceId: 'trace-002',
              taskId: 'write-tests',
              metadata: { severity: 'critical', status: 'open' },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-critical-open', 'write-tests', 'done', 'task-done-critical-open'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(35);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_NO_OPEN_CRITICAL_FINDINGS');
      expect(events[0].correlationId).toBe('req-task-done-critical-open');
    }
  });

  it('rejects TASK_TRANSITION review -> done when a blocking security finding is still open', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 34,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'review',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [
            {
              tool: 'runTests',
              params: { task_id: 'write-tests' },
              sourceEventType: 'tool_call',
              traceId: 'trace-sec-004',
              sequenceId: 32,
              timestamp: '2026-04-08T00:00:32.000Z',
              agentId: 'dev-1'
            }
          ],
          recentWorkflowSteps: [
            {
              step: 'Security finding recorded',
              detail: 'Missing policy on runtime_config',
              sourceEventType: 'security_finding',
              traceId: 'trace-sec-004',
              taskId: 'write-tests',
              metadata: {
                findingId: 'SEC-420',
                severity: 'high',
                status: 'open',
                confidenceScore: 9.3,
                surfaceId: 'runtime_config',
                missingPolicy: true,
                exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.'
              },
              sequenceId: 33,
              timestamp: '2026-04-08T00:00:33.000Z',
              agentId: 'dev-1'
            }
          ]
        };
      },
      async applyTaskTransition() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-security-open', 'write-tests', 'done', 'task-done-security-open'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(35);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('cannot transition to done without verification evidence');
      expect(events[0].message).toContain('TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS');
      expect(events[0].correlationId).toBe('req-task-done-security-open');
    }
  });

  it('allows TASK_TRANSITION review -> done when verification gate is satisfied', async () => {
    let snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 40,
      agents: [
        {
          ...runtimeAgent,
          status: 'working'
        }
      ],
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'review',
          assigneeId: 'dev-1'
        }
      ],
      config: {},
      recentToolCalls: [
        {
          tool: 'runTests',
          params: { task_id: 'write-tests' },
          sourceEventType: 'tool_call',
          traceId: 'trace-001',
          sequenceId: 38,
          timestamp: '2026-04-08T00:00:38.000Z',
          agentId: 'dev-1'
        }
      ],
      recentWorkflowSteps: [
        {
          step: 'Evidence collected',
          detail: 'Tests passed before done transition',
          sourceEventType: 'decision',
          traceId: 'trace-001',
          taskId: 'write-tests',
          metadata: {
            topic: 'verification',
            actionId: 'task.transition.done',
            verificationRef: 'verify://write-tests/1',
            controlsExecuted: ['tests:unit'],
            evidenceRefs: ['tests://grimoire-game/adapter-grimoire#done-gate'],
            verdict: 'PASS'
          },
          sequenceId: 39,
          timestamp: '2026-04-08T00:00:39.000Z',
          agentId: 'dev-1'
        }
      ]
    };
    const appliedTransitions: string[] = [];
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return snapshot;
      },
      async applyTaskTransition(mutation) {
        appliedTransitions.push(`${mutation.taskId}:${mutation.status}`);
        const sequenceId = snapshot.lastSequenceId + 1;
        snapshot = {
          ...snapshot,
          generatedAt: '2026-04-08T00:00:41.000Z',
          lastSequenceId: sequenceId,
          tasks: snapshot.tasks.map((task) =>
            task.id === mutation.taskId
              ? {
                  ...task,
                  status: mutation.status
                }
              : task
          )
        };

        return {
          sequenceId,
          snapshot
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-ok', 'write-tests', 'done', 'task-done-ok'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(appliedTransitions).toEqual(['write-tests:done']);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].sequenceId).toBe(41);
      expect(events[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'done',
          assigneeId: 'dev-1'
        }
      ]);
    }
  });

  it('rejects TASK_TRANSITION target status outside bounded transition budget', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 14,
          agents: [runtimeAgent],
          tasks: [
            {
              id: 'write-tests',
              title: 'Write tests',
              status: 'todo',
              assigneeId: 'dev-1'
            }
          ],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-budget', 'write-tests', 'backlog', 'task-budget'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(15);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].correlationId).toBe('req-task-budget');
    }
  });

  it('rejects spectator mutations and records the authorization audit trail', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 12,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-spectator', 'hud.theme', 'paper', 'cfg-spectator'),
      { principalId: 'spectator-1', role: 'spectator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(13);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('read-only');
      expect(events[0].correlationId).toBe('req-spectator');
    }

    const authAudit = adapter
      .getAuditLog()
      .find((entry) => entry.type === 'AUTH_REJECTED' && entry.requestId === 'req-spectator');
    expect(authAudit?.principalId).toBe('spectator-1');
    expect(authAudit?.role).toBe('spectator');
  });

  it('rejects config keys outside the bounded write budget', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 5,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      },
      async applyConfigUpdate() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-out', 'runtime.secret', true, 'cfg-out'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(6);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].correlationId).toBe('req-out');
    }
  });

  it('rejects non-canonical requestId before dispatching CONFIG_UPDATE writes', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 6,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      },
      async applyConfigUpdate() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const malformedEvent: ClientEvent = {
      type: 'CONFIG_UPDATE',
      version: RUNTIME_PROTOCOL_VERSION,
      requestId: ' req-non-canonical ',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg-canonical'
    } as unknown as ClientEvent;

    const events = await adapter.handleClientEvent(malformedEvent, {
      principalId: 'orch-1',
      role: 'orchestrator'
    });

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(7);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('must not contain leading or trailing spaces');
      expect(events[0].correlationId).toBe(' req-non-canonical ');
    }
  });

  it('rejects non-canonical idempotencyKey before dispatching CONFIG_UPDATE writes', async () => {
    let applyCalled = false;
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 7,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      },
      async applyConfigUpdate() {
        applyCalled = true;
        throw new Error('should not be called');
      }
    });

    const malformedEvent: ClientEvent = {
      type: 'CONFIG_UPDATE',
      version: RUNTIME_PROTOCOL_VERSION,
      requestId: 'req-canonical',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg key with spaces'
    } as unknown as ClientEvent;

    const events = await adapter.handleClientEvent(malformedEvent, {
      principalId: 'orch-1',
      role: 'orchestrator'
    });

    expect(applyCalled).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(8);
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('contains unsupported characters');
      expect(events[0].correlationId).toBe('req-canonical');
    }
  });

  it('emits monotonic NOT_IMPLEMENTED errors across consecutive denied writes', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 9,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      }
    });

    const first = await adapter.handleClientEvent(
      createConfigUpdate('req-nowrite-1', 'hud.theme', 'paper', 'cfg-nowrite-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const second = await adapter.handleClientEvent(
      createConfigUpdate('req-nowrite-2', 'board.zoom', 1.25, 'cfg-nowrite-2'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);
    expect(first[0]?.type).toBe('ERROR');
    expect(second[0]?.type).toBe('ERROR');

    if (first[0]?.type === 'ERROR' && second[0]?.type === 'ERROR') {
      expect(first[0].sequenceId).toBe(10);
      expect(second[0].sequenceId).toBe(11);
      expect(first[0].code).toBe('NOT_IMPLEMENTED');
      expect(second[0].code).toBe('NOT_IMPLEMENTED');
      expect(first[0].correlationId).toBe('req-nowrite-1');
      expect(second[0].correlationId).toBe('req-nowrite-2');
    }
  });

  it('fails closed with RUNTIME_WRITE_FAILED when runtime source write throws', async () => {
    const adapter = new AdapterGrimoire({
      async readSnapshot() {
        return {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          generatedAt: '2026-04-08T00:00:00.000Z',
          lastSequenceId: 4,
          agents: [runtimeAgent],
          tasks: [],
          config: {},
          recentToolCalls: [],
          recentWorkflowSteps: []
        };
      },
      async applyConfigUpdate() {
        throw new Error('disk-full');
      }
    });

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-write-fail', 'hud.theme', 'paper', 'cfg-write-fail'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].sequenceId).toBe(5);
      expect(events[0].code).toBe('RUNTIME_WRITE_FAILED');
      expect(events[0].correlationId).toBe('req-write-fail');
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'ERROR_EMITTED' && entry.detail?.includes('RUNTIME_WRITE_FAILED'))
    ).toBe(true);
  });
});