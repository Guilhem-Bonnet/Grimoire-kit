import {
  createAgentStatusUpdate,
  createMutationGuardrail,
  createTaskAssign,
  createConfigUpdate,
  createReconnectHandshake,
  createStateSnapshotEvent,
  createTaskTransition,
  createToolCallEvent,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  parseClientEvent,
  parseServerEvent,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('runtime contracts', () => {
  it('accepts a reconnect handshake payload', () => {
    const event = parseClientEvent(createReconnectHandshake('req-1', 12));

    expect(event.type).toBe('RECONNECT_HANDSHAKE');
    if (event.type !== 'RECONNECT_HANDSHAKE') {
      throw new Error('Expected a reconnect handshake event.');
    }
    expect(event.version).toBe(RUNTIME_PROTOCOL_VERSION);
    expect(event.lastSequenceId).toBe(12);
  });

  it('rejects malformed config updates', () => {
    expect(() =>
      parseClientEvent({
        type: 'CONFIG_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-2',
        key: 'hud.theme'
      })
    ).toThrow();
  });

  it('rejects non-canonical requestId for mutable events', () => {
    expect(() =>
      parseClientEvent({
        type: 'CONFIG_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: ' req-with-spaces ',
        key: 'hud.theme',
        value: 'paper',
        idempotencyKey: 'cfg-canonical'
      })
    ).toThrow();
  });

  it('rejects non-canonical idempotencyKey for mutable events', () => {
    expect(() =>
      parseClientEvent({
        type: 'TASK_TRANSITION',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-canonical',
        taskId: 'write-tests',
        status: 'review',
        idempotencyKey: 'task key with spaces'
      })
    ).toThrow();
  });

  it('rejects oversized mutation identities', () => {
    const oversized = 'a'.repeat(129);

    expect(() =>
      parseClientEvent({
        type: 'TASK_ASSIGN',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: oversized,
        taskId: 'write-tests',
        assigneeId: 'qa-1',
        idempotencyKey: 'task-assign-canonical'
      })
    ).toThrow();
  });

  it('rejects malformed task transitions', () => {
    expect(() =>
      parseClientEvent({
        type: 'TASK_TRANSITION',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-2b',
        taskId: 'write-tests',
        idempotencyKey: 'task-2b'
      })
    ).toThrow();
  });

  it('rejects malformed task assignments', () => {
    expect(() =>
      parseClientEvent({
        type: 'TASK_ASSIGN',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-2c',
        taskId: 'write-tests',
        idempotencyKey: 'task-2c'
      })
    ).toThrow();
  });

  it('rejects malformed agent status updates', () => {
    expect(() =>
      parseClientEvent({
        type: 'AGENT_STATUS_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-2d',
        agentId: 'dev-1',
        idempotencyKey: 'agent-2d'
      })
    ).toThrow();
  });

  it('rejects server events with undeclared fields', () => {
    const snapshotEvent = createStateSnapshotEvent(0, {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-08T00:00:00.000Z',
      lastSequenceId: 0,
      agents: [],
      tasks: [],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    });

    expect(() => parseServerEvent({ ...snapshotEvent, extra: true })).toThrow();
  });

  it('accepts config updates with JSON payloads', () => {
    const event = parseClientEvent(createConfigUpdate('req-3', 'hud.theme', { palette: 'paper', volume: 0.5 }));

    expect(event.type).toBe('CONFIG_UPDATE');
    if (event.type !== 'CONFIG_UPDATE') {
      throw new Error('Expected a config update event.');
    }
    expect(event.value).toEqual({ palette: 'paper', volume: 0.5 });
    expect(event.idempotencyKey).toBe('req-3');
    expect(event.guardrail).toEqual({
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'trusted',
      provenance: {
        source: 'runtime_ui',
        actorTag: 'config.update'
      }
    });
    expect(event.verification).toEqual({
      actionId: 'config.update',
      traceId: 'req-3',
      verificationRef: 'verify://config.update/req-3',
      controlsExecuted: ['policy:config.update'],
      evidenceRefs: ['mutation://config.update/req-3'],
      requestId: 'req-3',
      idempotencyKey: 'req-3',
      unmetControls: []
    });
  });

  it('accepts bounded task transitions payloads', () => {
    const event = parseClientEvent(createTaskTransition('req-3b', 'write-tests', 'review', 'task-3b'));

    expect(event.type).toBe('TASK_TRANSITION');
    if (event.type !== 'TASK_TRANSITION') {
      throw new Error('Expected a task transition event.');
    }
    expect(event.taskId).toBe('write-tests');
    expect(event.status).toBe('review');
    expect(event.idempotencyKey).toBe('task-3b');
    expect(event.guardrail?.surface).toBe('task_lifecycle');
    expect(event.guardrail?.policy).toBe('surface_scoped');
  });

  it('accepts bounded task assignment payloads', () => {
    const event = parseClientEvent(createTaskAssign('req-3c', 'write-tests', 'qa-1', 'task-3c'));

    expect(event.type).toBe('TASK_ASSIGN');
    if (event.type !== 'TASK_ASSIGN') {
      throw new Error('Expected a task assign event.');
    }
    expect(event.taskId).toBe('write-tests');
    expect(event.assigneeId).toBe('qa-1');
    expect(event.idempotencyKey).toBe('task-3c');
    expect(event.guardrail?.surface).toBe('task_assignment');
  });

  it('accepts bounded agent status update payloads', () => {
    const event = parseClientEvent(createAgentStatusUpdate('req-3d', 'dev-1', 'paused', 'agent-3d'));

    expect(event.type).toBe('AGENT_STATUS_UPDATE');
    if (event.type !== 'AGENT_STATUS_UPDATE') {
      throw new Error('Expected an agent status update event.');
    }
    expect(event.agentId).toBe('dev-1');
    expect(event.status).toBe('paused');
    expect(event.idempotencyKey).toBe('agent-3d');
    expect(event.guardrail?.surface).toBe('agent_presence');
  });

  it('keeps schema compatibility for legacy write payloads that do not declare guardrails', () => {
    const event = parseClientEvent({
      type: 'TASK_ASSIGN',
      version: RUNTIME_PROTOCOL_VERSION,
      requestId: 'req-legacy-task-assign',
      taskId: 'write-tests',
      assigneeId: 'qa-1',
      idempotencyKey: 'task-assign-legacy'
    });

    expect(event.type).toBe('TASK_ASSIGN');
    if (event.type !== 'TASK_ASSIGN') {
      throw new Error('Expected a task assign event.');
    }
    expect(event.guardrail).toBeUndefined();
  });

  it('keeps schema compatibility for elevated writes that do not yet declare verification metadata', () => {
    const event = parseClientEvent({
      type: 'CONFIG_UPDATE',
      version: RUNTIME_PROTOCOL_VERSION,
      requestId: 'req-legacy-config-verify',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg-legacy-config-verify',
      guardrail: {
        surface: 'runtime_config',
        policy: 'elevated',
        trustLevel: 'trusted',
        provenance: {
          source: 'runtime_ui',
          actorTag: 'config.update'
        }
      }
    });

    expect(event.type).toBe('CONFIG_UPDATE');
    if (event.type !== 'CONFIG_UPDATE') {
      throw new Error('Expected a config update event.');
    }
    expect(event.verification).toBeUndefined();
  });

  it('allows callers to override guardrail provenance when needed for bounded runtime bridges', () => {
    const event = createTaskTransition('req-guardrail-override', 'write-tests', 'done', 'task-guardrail-override', {
      trustLevel: 'trusted',
      provenance: {
        source: 'runtime_adapter',
        actorTag: 'bridge.sync'
      }
    });

    expect(event.guardrail).toEqual(
      createMutationGuardrail('task_lifecycle', {
        policy: 'elevated',
        trustLevel: 'trusted',
        provenance: {
          source: 'runtime_adapter',
          actorTag: 'bridge.sync'
        }
      })
    );
  });

  it('accepts task updates with linked agent presence', () => {
    const event = parseServerEvent(
      createTaskUpdateEvent(
        7,
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

    expect(event.type).toBe('TASK_UPDATE');
    if (event.type !== 'TASK_UPDATE') {
      throw new Error('Expected a task update event.');
    }
    expect(event.task.status).toBe('in_progress');
    expect(event.agent?.status).toBe('working');
  });

  it('accepts tool call replay events with structured params', () => {
    const event = parseServerEvent(
      createToolCallEvent(
        8,
        {
          tool: 'create_file',
          params: {
            path: 'src/auth.ts',
            lines: 42
          },
          sourceEventType: 'artifact_created',
          traceId: 'session-001'
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

    expect(event.type).toBe('TOOL_CALL');
    if (event.type !== 'TOOL_CALL') {
      throw new Error('Expected a tool call event.');
    }
    expect(event.call.tool).toBe('create_file');
    expect(event.call.params.path).toBe('src/auth.ts');
    expect(event.call.sourceEventType).toBe('artifact_created');
    expect(event.call.traceId).toBe('session-001');
  });

  it('accepts workflow step events for explainability traces', () => {
    const event = parseServerEvent(
      createWorkflowStepEvent(
        9,
        {
          step: 'Decision recorded',
          detail: 'auth: JWT RS256 stateless',
          sourceEventType: 'decision',
          traceId: 'session-001',
          metadata: {
            topic: 'auth',
            choice: 'JWT RS256 stateless'
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

    expect(event.type).toBe('WORKFLOW_STEP');
    if (event.type !== 'WORKFLOW_STEP') {
      throw new Error('Expected a workflow step event.');
    }
    expect(event.step.step).toBe('Decision recorded');
    expect(event.step.sourceEventType).toBe('decision');
    expect(event.step.traceId).toBe('session-001');
  });
});