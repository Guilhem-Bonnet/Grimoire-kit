import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import {
  MUTATION_SURFACES,
  createAgentStatusUpdate,
  createConfigUpdate,
  createReconnectHandshake,
  createTaskAssign,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { AuthenticationError, LocalAuthTokenRegistry } from '../../src/server/auth/token-registry';

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

describe('LocalAuthTokenRegistry', () => {
  it('issues spectator tokens as read-only contexts', () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'spectator-1', role: 'spectator' });
    const context = registry.authenticate(issued.token);

    expect(issued.readOnly).toBe(true);
    expect(context).toEqual({
      principalId: 'spectator-1',
      role: 'spectator',
      tokenId: issued.token,
      trustLevel: 'restricted',
      authorizedMutationSurfaces: []
    });
  });

  it('issues orchestrator tokens with trusted all-surface runtime scope by default', () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'orch-1', role: 'orchestrator' });
    const context = registry.authenticate(issued.token);

    expect(issued.trustLevel).toBe('trusted');
    expect(issued.authorizedMutationSurfaces).toEqual(MUTATION_SURFACES);
    expect(context).toEqual({
      principalId: 'orch-1',
      role: 'orchestrator',
      tokenId: issued.token,
      trustLevel: 'trusted',
      authorizedMutationSurfaces: MUTATION_SURFACES
    });
  });

  it('rejects missing or unknown tokens and traces the refusal', () => {
    const registry = new LocalAuthTokenRegistry();

    expect(() => registry.authenticate('')).toThrow(AuthenticationError);
    expect(() => registry.authenticate('missing-token')).toThrow(AuthenticationError);

    expect(registry.getAuditLog().filter((entry) => entry.type === 'REJECTED')).toHaveLength(2);
  });

  it('rejects expired tokens and records expiration in the audit trail', () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({
      principalId: 'orch-expired',
      role: 'orchestrator',
      expiresAt: '1970-01-01T00:00:00.000Z'
    });

    expect(() => registry.authenticate(issued.token)).toThrow(AuthenticationError);

    const rejection = registry
      .getAuditLog()
      .find((entry) => entry.type === 'REJECTED' && entry.token === issued.token);
    expect(rejection?.reason).toBe('Expired token.');
  });

  it('revokes tokens and prevents further authentication', () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'orch-revoked', role: 'orchestrator' });

    expect(registry.revoke(issued.token)).toBe(true);
    expect(registry.revoke(issued.token)).toBe(false);
    expect(() => registry.authenticate(issued.token)).toThrow(AuthenticationError);

    expect(
      registry.getAuditLog().some((entry) => entry.type === 'REVOKED' && entry.token === issued.token)
    ).toBe(true);
  });

  it('blocks spectator mutations after token authentication and records the denial', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'spectator-1', role: 'spectator' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);
    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-auth', 'hud.theme', 'paper', 'cfg-auth'),
      context
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
    }

    expect(adapter.getAuditLog().some((entry) => entry.type === 'AUTH_REJECTED')).toBe(true);
  });

  it('blocks orchestrator writes that target a surface outside the issued token scope', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({
      principalId: 'orch-scope',
      role: 'orchestrator',
      authorizedMutationSurfaces: ['task_lifecycle']
    });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-auth-scope', 'hud.theme', 'paper', 'cfg-auth-scope'),
      context
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toBe('Role orchestrator is not authorized for governed surface runtime_config.');
    }
  });

  it('blocks legacy writes that do not carry runtime guardrail metadata', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'orch-legacy', role: 'orchestrator' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(
      {
        type: 'CONFIG_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-legacy-config',
        key: 'hud.theme',
        value: 'paper',
        idempotencyKey: 'cfg-legacy-config'
      } as never,
      context
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toBe('Mutation CONFIG_UPDATE is missing runtime guardrail metadata.');
    }
  });

  it('allows orchestrator mutations after token authentication', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'orch-1', role: 'orchestrator' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-auth-orch', 'hud.theme', 'paper', 'cfg-auth-orch'),
      context
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(adapter.getAuditLog().some((entry) => entry.type === 'AUTH_REJECTED')).toBe(false);
  });

  it('blocks elevated writes whose proof identities do not align with requestId and idempotencyKey', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'orch-proof', role: 'orchestrator' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(
      createTaskTransition(
        'req-auth-proof-mismatch',
        'write-tests',
        'done',
        'task-auth-proof-mismatch',
        undefined,
        {
          actionId: 'task.transition.done',
          traceId: 'trace-auth-proof-mismatch',
          verificationRef: 'verify://task-auth-proof-mismatch',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: ['tests://grimoire-game/runtime-source-fs#proof-mismatch'],
          requestId: 'req-other',
          idempotencyKey: 'task-other'
        }
      ),
      context
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('ERROR');
    if (events[0]?.type === 'ERROR') {
      expect(events[0].code).toBe('FORBIDDEN');
      expect(events[0].message).toBe(
        'Critical mutation TASK_TRANSITION verification metadata does not align with requestId and idempotencyKey.'
      );
    }
  });

  it('allows spectator reconnect handshakes after token authentication', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'spectator-1', role: 'spectator' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const events = await adapter.handleClientEvent(createReconnectHandshake('req-reconnect-auth'), context);

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(adapter.getAuditLog().some((entry) => entry.type === 'AUTH_REJECTED')).toBe(false);
  });

  it('blocks agent mutations on TASK_TRANSITION, TASK_ASSIGN and AGENT_STATUS_UPDATE after token authentication', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'agent-1', role: 'agent' });
    const context = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(initialSnapshot);

    const transitionEvents = await adapter.handleClientEvent(
      createTaskTransition('req-auth-transition-agent', 'write-tests', 'review', 'task-transition-auth-agent'),
      context
    );
    const assignEvents = await adapter.handleClientEvent(
      createTaskAssign('req-auth-assign-agent', 'write-tests', 'qa-1', 'task-assign-auth-agent'),
      context
    );
    const statusEvents = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-auth-agent-status-agent',
        'spectator-1',
        'paused',
        'agent-status-auth-agent'
      ),
      context
    );

    expect(transitionEvents).toHaveLength(1);
    expect(transitionEvents[0]?.type).toBe('ERROR');
    if (transitionEvents[0]?.type === 'ERROR') {
      expect(transitionEvents[0].code).toBe('FORBIDDEN');
    }

    expect(assignEvents).toHaveLength(1);
    expect(assignEvents[0]?.type).toBe('ERROR');
    if (assignEvents[0]?.type === 'ERROR') {
      expect(assignEvents[0].code).toBe('FORBIDDEN');
    }

    expect(statusEvents).toHaveLength(1);
    expect(statusEvents[0]?.type).toBe('ERROR');
    if (statusEvents[0]?.type === 'ERROR') {
      expect(statusEvents[0].code).toBe('FORBIDDEN');
    }

    const authRejectedEntries = adapter.getAuditLog().filter((entry) => entry.type === 'AUTH_REJECTED');
    expect(authRejectedEntries).toHaveLength(3);
  });
});