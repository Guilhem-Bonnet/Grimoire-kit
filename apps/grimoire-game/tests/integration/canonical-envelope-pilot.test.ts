import {
  createErrorEvent,
  createStateSnapshotEvent,
  createTaskUpdateEvent,
  createToolCallEvent,
  createVerificationGateEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence
} from '../../src/contracts/events';
import {
  projectServerEventToCanonicalEnvelope,
  projectServerEventsToCanonicalEnvelopes
} from '../../src/state/canonical-envelope-pilot';

describe('canonical envelope pilot projection', () => {
  const agent: AgentPresence = {
    id: 'dev-1',
    name: 'Amelia',
    role: 'agent',
    status: 'working',
    roomId: 'build-room',
    position: { x: 8, y: 8 }
  };

  it('projects the bounded basket without semantic loss for task and workflow events', () => {
    const events = [
      createTaskUpdateEvent(
        7,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'review',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-09T12:00:07.000Z',
          agent
        }
      ),
      createWorkflowStepEvent(
        8,
        {
          step: 'Decision recorded',
          detail: 'auth: JWT middleware ready',
          sourceEventType: 'decision',
          traceId: 'trace-auth-1',
          taskId: 'task-auth',
          metadata: {
            topic: 'auth'
          }
        },
        {
          timestamp: '2026-04-09T12:00:08.000Z',
          agent
        }
      )
    ];

    const envelopes = projectServerEventsToCanonicalEnvelopes(events, 'replay');

    expect(envelopes).toHaveLength(2);
    expect(envelopes[0]).toMatchObject({
      header: {
        messageType: 'task.update',
        channel: 'replay',
        messageVersion: 'pilot-v1'
      },
      context: {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        taskId: 'task-auth'
      },
      body: {
        task: {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'review',
          assigneeId: 'dev-1'
        }
      }
    });
    expect(envelopes[1]).toMatchObject({
      header: {
        messageType: 'workflow.step',
        channel: 'replay'
      },
      context: {
        traceId: 'trace-auth-1',
        taskId: 'task-auth'
      },
      body: {
        step: {
          step: 'Decision recorded',
          detail: 'auth: JWT middleware ready',
          sourceEventType: 'decision'
        }
      }
    });
  });

  it('projects verification gate and runtime error with required causal fields', () => {
    const verificationEvent = createVerificationGateEvent(
      9,
      {
        result: 'PASS',
        actionId: 'task.transition.done',
        verificationRef: 'verify://task-auth/9',
        evidenceRefs: [{ kind: 'test', ref: 'tests://grimoire-game/pilot#gate' }],
        controlsExecuted: ['tests:unit'],
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        meta: {
          correlationId: 'req-auth-9',
          actorId: 'orch-1'
        }
      },
      {
        timestamp: '2026-04-09T12:00:09.000Z'
      }
    );
    const errorEvent = createErrorEvent(
      10,
      'WS_TIMEOUT',
      'Lost connection to runtime.',
      'req-timeout',
      true,
      '2026-04-09T12:00:10.000Z'
    );

    const envelopes = projectServerEventsToCanonicalEnvelopes([verificationEvent, errorEvent], 'runtime');

    expect(envelopes).toHaveLength(2);
    expect(envelopes[0]).toMatchObject({
      header: {
        messageType: 'verification.gate'
      },
      context: {
        verificationRef: 'verify://task-auth/9',
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        correlationId: 'req-auth-9'
      },
      body: {
        actionId: 'task.transition.done',
        verificationRef: 'verify://task-auth/9',
        controlsExecuted: ['tests:unit'],
        evidenceRefs: [{ kind: 'test', ref: 'tests://grimoire-game/pilot#gate' }]
      }
    });
    expect(envelopes[1]).toMatchObject({
      header: {
        messageType: 'runtime.error'
      },
      body: {
        code: 'WS_TIMEOUT',
        message: 'Lost connection to runtime.',
        retryable: true,
        correlationId: 'req-timeout'
      }
    });
  });

  it('projects control-plane identifiers and sequence ids when provided', () => {
    const event = createTaskUpdateEvent(
      13,
      {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      },
      {
        timestamp: '2026-04-11T09:13:13.000Z',
        agent
      }
    );

    const envelope = projectServerEventToCanonicalEnvelope(event, 'runtime', {
      projectId: 'grimoire-game',
      runId: 'run-13',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-auth',
      branch: 'feature/auth'
    });

    expect(envelope).not.toBeNull();
    expect(envelope).toMatchObject({
      header: {
        sequenceId: 13
      },
      context: {
        projectId: 'grimoire-game',
        runId: 'run-13',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
      }
    });
  });

  it('does not project events outside the bounded pilot basket', () => {
    const unsupported = createToolCallEvent(
      11,
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts' },
        sourceEventType: 'artifact_created',
        traceId: 'trace-auth-1'
      },
      {
        timestamp: '2026-04-09T12:00:11.000Z',
        agent
      }
    );
    const snapshot = createStateSnapshotEvent(
      12,
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        generatedAt: '2026-04-09T12:00:12.000Z',
        lastSequenceId: 12,
        agents: [agent],
        tasks: [],
        config: {},
        recentToolCalls: [],
        recentWorkflowSteps: []
      },
      '2026-04-09T12:00:12.000Z'
    );

    expect(projectServerEventToCanonicalEnvelope(unsupported, 'runtime')).toBeNull();
    expect(projectServerEventToCanonicalEnvelope(snapshot, 'runtime')).toBeNull();
    expect(projectServerEventsToCanonicalEnvelopes([unsupported, snapshot], 'runtime')).toEqual([]);
  });
});
