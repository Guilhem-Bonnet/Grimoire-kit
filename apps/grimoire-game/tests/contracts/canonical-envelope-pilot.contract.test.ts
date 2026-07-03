import {
  CanonicalEnvelopePilotSchema,
  createCanonicalEnvelopePilot,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('canonical envelope pilot contract', () => {
  it('accepts a valid pilot envelope payload for bounded runtime mapping', () => {
    const envelope = createCanonicalEnvelopePilot({
      header: {
        messageType: 'task.update',
        messageId: 'task.update:42',
        emittedAt: '2026-04-09T12:00:42.000Z',
        channel: 'runtime'
      },
      context: {
        traceId: 'trace-42',
        taskId: 'task-auth',
        correlationId: 'task.update:42'
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

    expect(envelope).toMatchObject({
      header: {
        messageType: 'task.update',
        messageVersion: 'pilot-v1',
        channel: 'runtime'
      },
      context: {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        traceId: 'trace-42',
        taskId: 'task-auth'
      }
    });
  });

  it('defaults protocolVersion to runtime v1 when omitted', () => {
    const envelope = createCanonicalEnvelopePilot({
      header: {
        messageType: 'workflow.step',
        messageId: 'workflow.step:7',
        emittedAt: '2026-04-09T12:00:07.000Z',
        channel: 'session'
      },
      context: {
        traceId: 'trace-7'
      },
      body: {
        step: {
          step: 'Decision recorded',
          detail: 'auth: JWT RS256 stateless'
        }
      }
    });

    expect(envelope.context.protocolVersion).toBe(RUNTIME_PROTOCOL_VERSION);
  });

  it('accepts optional control-plane identifiers and sequence metadata', () => {
    const envelope = createCanonicalEnvelopePilot({
      header: {
        messageType: 'task.update',
        messageId: 'task.update:43',
        emittedAt: '2026-04-11T09:15:43.000Z',
        channel: 'runtime',
        sequenceId: 43
      },
      context: {
        projectId: 'grimoire-game',
        runId: 'run-43',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
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

    expect(envelope).toMatchObject({
      header: {
        sequenceId: 43
      },
      context: {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-43',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
      }
    });
  });

  it('rejects envelopes that do not use pilot-v1 message version', () => {
    expect(() =>
      CanonicalEnvelopePilotSchema.parse({
        header: {
          messageType: 'runtime.error',
          messageVersion: 'pilot-v2',
          messageId: 'runtime.error:9',
          emittedAt: '2026-04-09T12:00:09.000Z',
          channel: 'replay'
        },
        context: {
          protocolVersion: RUNTIME_PROTOCOL_VERSION
        },
        body: {
          code: 'WS_TIMEOUT',
          message: 'Lost connection',
          retryable: true
        }
      })
    ).toThrow();
  });

  it('rejects envelopes with unsupported channels', () => {
    expect(() =>
      CanonicalEnvelopePilotSchema.parse({
        header: {
          messageType: 'task.update',
          messageVersion: 'pilot-v1',
          messageId: 'task.update:90',
          emittedAt: '2026-04-09T12:00:90.000Z',
          channel: 'cli'
        },
        context: {
          protocolVersion: RUNTIME_PROTOCOL_VERSION
        },
        body: {
          task: {
            id: 'task-auth'
          }
        }
      })
    ).toThrow();
  });
});
