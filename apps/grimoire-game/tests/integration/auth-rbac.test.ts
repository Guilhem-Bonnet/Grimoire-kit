import {
  MUTATION_SURFACES,
  createAgentStatusUpdate,
  createConfigUpdate,
  createReconnectHandshake,
  createTaskAssign,
  createTaskTransition
} from '../../src/contracts/events';
import {
  AuthorizationError,
  authorizeClientEvent,
  createAuthorizationAuditEntry,
  requireAuthorizedClientEvent
} from '../../src/server/auth/rbac';

describe('RBAC runtime guardrails', () => {
  it('allows RECONNECT_HANDSHAKE for every runtime role', () => {
    const event = createReconnectHandshake('req-reconnect', 9);

    expect(authorizeClientEvent({ principalId: 'orch-1', role: 'orchestrator' }, event)).toEqual({
      allowed: true
    });
    expect(authorizeClientEvent({ principalId: 'agent-1', role: 'agent' }, event)).toEqual({
      allowed: true
    });
    expect(authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event)).toEqual({
      allowed: true
    });
  });

  it('allows orchestrator mutations for each bounded write event', () => {
    const orchestrator = { principalId: 'orch-1', role: 'orchestrator' } as const;
    const events = [
      createConfigUpdate('req-config-allow', 'hud.theme', 'paper', 'cfg-allow'),
      createTaskTransition('req-transition-allow', 'write-tests', 'review', 'task-transition-allow'),
      createTaskAssign('req-assign-allow', 'write-tests', 'qa-1', 'task-assign-allow'),
      createAgentStatusUpdate('req-agent-status-allow', 'qa-1', 'paused', 'agent-status-allow')
    ] as const;

    for (const event of events) {
      expect(authorizeClientEvent(orchestrator, event)).toMatchObject({ allowed: true });
      expect(() => requireAuthorizedClientEvent(orchestrator, event)).not.toThrow();
    }
  });

  it('rejects CONFIG_UPDATE for non-orchestrator roles with explicit reasons', () => {
    const event = createConfigUpdate('req-config', 'hud.theme', 'paper', 'cfg-1');

    const agentDecision = authorizeClientEvent({ principalId: 'agent-1', role: 'agent' }, event);
    const spectatorDecision = authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event);

    expect(agentDecision).toEqual({
      allowed: false,
      reason: 'Role agent cannot execute CONFIG_UPDATE.'
    });
    expect(spectatorDecision).toEqual({
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    });
  });

  it('rejects TASK_TRANSITION for non-orchestrator roles with explicit reasons', () => {
    const event = createTaskTransition('req-transition', 'write-tests', 'review', 'task-transition-1');

    const agentDecision = authorizeClientEvent({ principalId: 'agent-1', role: 'agent' }, event);
    const spectatorDecision = authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event);

    expect(agentDecision).toEqual({
      allowed: false,
      reason: 'Role agent cannot execute TASK_TRANSITION.'
    });
    expect(spectatorDecision).toEqual({
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    });
  });

  it('rejects TASK_ASSIGN for non-orchestrator roles with explicit reasons', () => {
    const event = createTaskAssign('req-assign', 'write-tests', 'qa-1', 'task-assign-1');

    const agentDecision = authorizeClientEvent({ principalId: 'agent-1', role: 'agent' }, event);
    const spectatorDecision = authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event);

    expect(agentDecision).toEqual({
      allowed: false,
      reason: 'Role agent cannot execute TASK_ASSIGN.'
    });
    expect(spectatorDecision).toEqual({
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    });
  });

  it('rejects AGENT_STATUS_UPDATE for non-orchestrator roles with explicit reasons', () => {
    const event = createAgentStatusUpdate('req-agent-status-deny', 'qa-1', 'paused', 'agent-status-deny');

    const agentDecision = authorizeClientEvent({ principalId: 'agent-1', role: 'agent' }, event);
    const spectatorDecision = authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event);

    expect(agentDecision).toEqual({
      allowed: false,
      reason: 'Role agent cannot execute AGENT_STATUS_UPDATE.'
    });
    expect(spectatorDecision).toEqual({
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    });
  });

  it('rejects governed mutations that omit runtime guardrail metadata', () => {
    const event = {
      type: 'CONFIG_UPDATE',
      version: 'v1',
      requestId: 'req-missing-guardrail',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg-missing-guardrail'
    } as const;

    const decision = authorizeClientEvent({ principalId: 'orch-1', role: 'orchestrator' }, event as never);

    expect(decision).toEqual({
      allowed: false,
      reason: 'Mutation CONFIG_UPDATE is missing runtime guardrail metadata.'
    });
  });

  it('rejects governed mutations that omit runtime guardrail origin metadata', () => {
    const event = {
      type: 'CONFIG_UPDATE',
      version: 'v1',
      requestId: 'req-missing-origin',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg-missing-origin',
      guardrail: {
        surface: 'runtime_config',
        policy: 'elevated',
        trustLevel: 'trusted'
      }
    } as never;

    const decision = authorizeClientEvent({ principalId: 'orch-1', role: 'orchestrator' }, event);

    expect(decision).toEqual({
      allowed: false,
      reason: 'Mutation CONFIG_UPDATE is missing runtime guardrail origin metadata.',
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'trusted'
    });
  });

  it('rejects governed mutations that omit required policy metadata', () => {
    const event = {
      type: 'CONFIG_UPDATE',
      version: 'v1',
      requestId: 'req-missing-policy',
      key: 'hud.theme',
      value: 'paper',
      idempotencyKey: 'cfg-missing-policy',
      guardrail: {
        surface: 'runtime_config',
        trustLevel: 'trusted',
        provenance: {
          source: 'runtime_ui',
          actorTag: 'config.update'
        }
      }
    } as never;

    const decision = authorizeClientEvent({ principalId: 'orch-1', role: 'orchestrator' }, event);

    expect(decision).toEqual({
      allowed: false,
      reason: 'Mutation CONFIG_UPDATE is missing required policy metadata.',
      surface: 'runtime_config',
      trustLevel: 'trusted',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'config.update'
    });
  });

  it('rejects governed mutations when trust status is blocked', () => {
    const event = createConfigUpdate('req-config-blocked', 'hud.theme', 'paper', 'cfg-config-blocked', {
      trustLevel: 'blocked'
    });

    const decision = authorizeClientEvent(
      {
        principalId: 'orch-1',
        role: 'orchestrator',
        trustLevel: 'trusted',
        authorizedMutationSurfaces: MUTATION_SURFACES
      },
      event
    );

    expect(decision).toEqual({
      allowed: false,
      reason: 'Governed surface runtime_config is blocked by trust status policy.',
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'blocked',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'config.update'
    });
  });

  it('rejects governed surfaces that are outside the token scope', () => {
    const event = createConfigUpdate('req-config-surface-scope', 'hud.theme', 'paper', 'cfg-surface-scope');

    const decision = authorizeClientEvent(
      {
        principalId: 'orch-1',
        role: 'orchestrator',
        trustLevel: 'trusted',
        authorizedMutationSurfaces: ['task_lifecycle']
      },
      event
    );

    expect(decision).toEqual({
      allowed: false,
      reason: 'Role orchestrator is not authorized for governed surface runtime_config.',
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'trusted',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'config.update'
    });
  });

  it('rejects elevated surfaces for restricted runtime contexts', () => {
    const event = createConfigUpdate('req-config-restricted', 'hud.theme', 'paper', 'cfg-restricted');

    const decision = authorizeClientEvent(
      {
        principalId: 'orch-1',
        role: 'orchestrator',
        trustLevel: 'restricted',
        authorizedMutationSurfaces: MUTATION_SURFACES
      },
      event
    );

    expect(decision).toEqual({
      allowed: false,
      reason: 'Governed surface runtime_config requires a trusted runtime context.',
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'trusted',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'config.update'
    });
  });

  it('throws AuthorizationError for denied bounded mutations through requireAuthorizedClientEvent', () => {
    const deniedCases = [
      {
        context: { principalId: 'agent-1', role: 'agent' } as const,
        event: createConfigUpdate('req-denied-config', 'hud.theme', 'paper', 'cfg-denied')
      },
      {
        context: { principalId: 'agent-1', role: 'agent' } as const,
        event: createTaskTransition('req-denied-transition', 'write-tests', 'review', 'task-transition-denied')
      },
      {
        context: { principalId: 'spectator-1', role: 'spectator' } as const,
        event: createTaskAssign('req-denied-assign', 'write-tests', 'qa-1', 'task-assign-denied')
      },
      {
        context: { principalId: 'agent-1', role: 'agent' } as const,
        event: createAgentStatusUpdate(
          'req-denied-agent-status',
          'qa-1',
          'paused',
          'agent-status-denied'
        )
      }
    ] as const;

    for (const deniedCase of deniedCases) {
      expect(() => requireAuthorizedClientEvent(deniedCase.context, deniedCase.event)).toThrow(
        AuthorizationError
      );
    }
  });

  it('omits optional denial fields for allowed authorization audit entries', () => {
    const event = createTaskAssign('req-audit-allow', 'write-tests', 'qa-1', 'task-assign-audit-allow');
    const decision = authorizeClientEvent({ principalId: 'orch-1', role: 'orchestrator' }, event);
    const audit = createAuthorizationAuditEntry(
      { principalId: 'orch-1', role: 'orchestrator' },
      event,
      decision,
      '2026-04-09T00:00:00.000Z'
    );

    expect(audit).toEqual({
      at: '2026-04-09T00:00:00.000Z',
      principalId: 'orch-1',
      role: 'orchestrator',
      eventType: 'TASK_ASSIGN',
      allowed: true,
      surface: 'task_assignment',
      policy: 'surface_scoped',
      trustLevel: 'trusted',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'task.assign'
    });
  });

  it('includes token identity in authorization audit entries', () => {
    const event = createConfigUpdate('req-audit', 'hud.theme', 'paper', 'cfg-audit');
    const decision = authorizeClientEvent({ principalId: 'spectator-1', role: 'spectator' }, event);
    const audit = createAuthorizationAuditEntry(
      { principalId: 'spectator-1', role: 'spectator', tokenId: 'tok-spectator-1' },
      event,
      decision,
      '2026-04-09T00:00:00.000Z'
    );

    expect(audit).toEqual({
      at: '2026-04-09T00:00:00.000Z',
      principalId: 'spectator-1',
      role: 'spectator',
      tokenId: 'tok-spectator-1',
      eventType: 'CONFIG_UPDATE',
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.',
      surface: 'runtime_config',
      policy: 'elevated',
      trustLevel: 'trusted',
      provenanceSource: 'runtime_ui',
      provenanceActorTag: 'config.update'
    });
  });
});