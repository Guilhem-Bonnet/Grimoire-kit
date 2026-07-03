import {
  createAgentStatusUpdate,
  type ClientEvent,
  createTaskAssign,
  createConfigUpdate,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { MockAgentAdapter } from '../../src/bridge/agent-adapter';

const initialSnapshot: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-08T00:00:00.000Z',
  lastSequenceId: 0,
  agents: [
    {
      id: 'orch-1',
      name: 'Orchestrator',
      role: 'orchestrator' as const,
      status: 'idle' as const,
      roomId: 'war-room',
      position: { x: 4, y: 8 }
    }
  ],
  tasks: [],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

describe('MockAgentAdapter', () => {
  it('returns a snapshot event without exposing grimoire internals', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
    const events = await adapter.getInitialSnapshot({ principalId: 'orch-1', role: 'orchestrator' });

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(events[0]?.sequenceId).toBe(0);
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.agents[0]?.name).toBe('Orchestrator');
    }
  });

  it('blocks spectator writes through RBAC', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-4', 'hud.theme', 'paper'),
      { principalId: 'spectator-1', role: 'spectator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].correlationId).toBe('req-4');
    }
  });

  it('applies TASK_TRANSITION for orchestrator and updates task status', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ]
    });
    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-1', 'write-tests', 'in_progress', 'task-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'in_progress',
          assigneeId: 'dev-1'
        }
      ]);
    }

    expect(
      adapter.getAuditLog().some((entry) => entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-task-1')
    ).toBe(true);
  });

  it('applies TASK_ASSIGN for orchestrator and updates task assignee', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'idle',
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
      ]
    });
    const events = await adapter.handleClientEvent(
      createTaskAssign('req-assign-1', 'write-tests', 'qa-1', 'task-assign-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'qa-1'
        }
      ]);
    }

    expect(
      adapter.getAuditLog().some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-assign-1')
    ).toBe(true);
  });

  it('applies AGENT_STATUS_UPDATE for orchestrator and updates agent status', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
      agents: [
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'working',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ]
    });
    const events = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-1', 'dev-1', 'paused', 'agent-status-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.agents).toEqual([
        {
          id: 'dev-1',
          name: 'Amelia',
          role: 'agent',
          status: 'paused',
          roomId: 'build-room',
          position: { x: 2, y: 2 }
        }
      ]);
    }

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-agent-status-1')
    ).toBe(true);
  });

  it('rejects config keys outside bounded V5 write budget', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-config-out', 'runtime.secret', true, 'cfg-out'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('outside the bounded V5 write budget');
      expect(events[0].correlationId).toBe('req-config-out');
    }
  });

  it('rejects task transitions outside bounded V5 transition graph', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
      tasks: [
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'todo',
          assigneeId: 'dev-1'
        }
      ]
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-graph-out', 'write-tests', 'done', 'task-graph-out'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('outside the bounded V5 transition graph');
      expect(events[0].correlationId).toBe('req-task-graph-out');
    }
  });

  it('rejects TASK_TRANSITION review -> done when verification gate is not satisfied', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
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
          status: 'review',
          assigneeId: 'dev-1'
        }
      ]
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-no-proof', 'write-tests', 'done', 'task-done-no-proof', undefined, {
        traceId: 'trace-done-no-proof',
        verificationRef: 'verify://write-tests/runtime-gate-fail',
        controlsExecuted: ['tests:unit'],
        evidenceRefs: ['tests://grimoire-game/adapter-mock#done-gate-fail']
      }),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(2);
    expect(events[0]?.type).toBe('VERIFICATION_GATE');
    expect(events[1]?.type).toBe('ERROR');
    if (events[0]?.type === 'VERIFICATION_GATE') {
      expect(events[0]).toMatchObject({
        result: 'FAIL',
        actionId: 'task.transition.done',
        verificationRef: 'verify://write-tests/runtime-gate-fail',
        taskId: 'write-tests',
        meta: {
          actorId: 'orch-1',
          actorRole: 'orchestrator',
          correlationId: 'req-task-done-no-proof'
        }
      });
      expect(events[0].unmetControls.length).toBeGreaterThan(0);
    }
    if (events[1]?.type === 'ERROR') {
      expect(events[1].code).toBe('FORBIDDEN');
      expect(events[1].message).toContain('cannot transition to done without verification evidence');
      expect(events[1].correlationId).toBe('req-task-done-no-proof');
    }
  });

  it('allows TASK_TRANSITION review -> done when verification gate is satisfied', async () => {
    const adapter = new MockAgentAdapter({
      ...initialSnapshot,
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
          status: 'review',
          assigneeId: 'dev-1'
        }
      ],
      recentToolCalls: [
        {
          tool: 'runTests',
          params: { task_id: 'write-tests' },
          sourceEventType: 'tool_call',
          traceId: 'trace-001',
          sequenceId: 0,
          timestamp: '2026-04-08T00:00:00.000Z',
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
            evidenceRefs: ['tests://grimoire-game/adapter-mock#done-gate'],
            verdict: 'PASS'
          },
          sequenceId: 0,
          timestamp: '2026-04-08T00:00:00.000Z',
          agentId: 'dev-1'
        }
      ]
    });

    const events = await adapter.handleClientEvent(
      createTaskTransition('req-task-done-ok', 'write-tests', 'done', 'task-done-ok', undefined, {
        traceId: 'trace-001',
        verificationRef: 'verify://write-tests/1',
        controlsExecuted: ['tests:unit'],
        evidenceRefs: ['tests://grimoire-game/adapter-mock#done-gate']
      }),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(events).toHaveLength(2);
    expect(events[0]?.type).toBe('VERIFICATION_GATE');
    expect(events[1]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'VERIFICATION_GATE') {
      expect(events[0]).toMatchObject({
        result: 'PASS',
        actionId: 'task.transition.done',
        verificationRef: 'verify://write-tests/1',
        taskId: 'write-tests',
        meta: {
          actorId: 'orch-1',
          actorRole: 'orchestrator',
          correlationId: 'req-task-done-ok'
        }
      });
      expect(events[0].evidenceRefs).toEqual([{ kind: 'test', ref: 'tests://grimoire-game/adapter-mock#done-gate' }]);
    }
    if (events[1]?.type === 'STATE_SNAPSHOT') {
      expect(events[1].snapshot.tasks).toEqual([
        {
          id: 'write-tests',
          title: 'Write tests',
          status: 'done',
          assigneeId: 'dev-1'
        }
      ]);
      expect(events[1].snapshot.recentWorkflowSteps.at(-1)).toMatchObject({
        sourceEventType: 'verification_gate',
        taskId: 'write-tests',
        metadata: {
          verificationRef: 'verify://write-tests/1',
          correlationId: 'req-task-done-ok',
          actorId: 'orch-1',
          actorRole: 'orchestrator',
          typedEvidenceRefs: [{ kind: 'test', ref: 'tests://grimoire-game/adapter-mock#done-gate' }]
        }
      });
    }
  });

  it('rejects non-canonical mutation identities before write dispatch', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
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

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toContain('must not contain leading or trailing spaces');
      expect(events[0].correlationId).toBe(' req-non-canonical ');
    }
  });

  it('evicts oldest processed mutation keys when cache size is bounded', async () => {
    const adapter = new MockAgentAdapter(
      {
        ...initialSnapshot,
        config: { 'hud.theme': 'paper' }
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

    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);
    expect(replayedAfterEviction).toHaveLength(1);
    expect(first[0]?.sequenceId).toBe(1);
    expect(second[0]?.sequenceId).toBe(2);
    expect(replayedAfterEviction[0]?.sequenceId).toBe(3);
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
});