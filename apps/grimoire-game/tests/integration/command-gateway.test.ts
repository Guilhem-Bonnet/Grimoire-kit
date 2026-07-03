import { CommandGateway } from '../../src/server/control-plane/command-gateway';
import { LocalAuthTokenRegistry } from '../../src/server/auth/token-registry';

describe('command-gateway', () => {
  it('dedupes reclaim commands by idempotency key and audits the replay', () => {
    const gateway = new CommandGateway();
    const auth = { principalId: 'orch-1', role: 'orchestrator' } as const;

    const first = gateway.execute(
      {
        commandId: 'cmd-1',
        type: 'lease.reclaim',
        idempotencyKey: 'lease-reclaim-1',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        leaseExpired: true,
        ownershipResolved: true
      },
      auth
    );
    const second = gateway.execute(
      {
        commandId: 'cmd-2',
        type: 'lease.reclaim',
        idempotencyKey: 'lease-reclaim-1',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        leaseExpired: true,
        ownershipResolved: true
      },
      auth
    );

    expect(first).toMatchObject({
      allowed: true,
      mutation: true,
      replayed: false,
      commandType: 'lease.reclaim'
    });
    expect(second).toMatchObject({
      allowed: true,
      mutation: true,
      replayed: true,
      commandType: 'lease.reclaim'
    });
    expect(gateway.getAuditLog()).toHaveLength(2);
    expect(gateway.getAuditLog()[1]).toMatchObject({
      commandType: 'lease.reclaim',
      allowed: true,
      replayed: true
    });
  });

  it('rejects mutation commands for spectator and agent roles with actionable reasons', () => {
    const gateway = new CommandGateway();

    const spectatorResult = gateway.execute(
      {
        commandId: 'cmd-spectator',
        type: 'lease.release',
        idempotencyKey: 'lease-release-spectator',
        leaseId: 'lease-auth',
        ownershipActive: true,
        reason: 'cleanup'
      },
      { principalId: 'spectator-1', role: 'spectator' }
    );
    const agentResult = gateway.execute(
      {
        commandId: 'cmd-agent',
        type: 'lease.reclaim',
        idempotencyKey: 'lease-reclaim-agent',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        leaseExpired: true,
        ownershipResolved: true
      },
      { principalId: 'agent-1', role: 'agent' }
    );

    expect(spectatorResult).toMatchObject({
      allowed: false,
      reason: 'Role spectator cannot execute lease.release.'
    });
    expect(agentResult).toMatchObject({
      allowed: false,
      reason: 'Role agent cannot execute lease.reclaim.'
    });
  });

  it('issues a read-only spectator token through the gateway and blocks follow-up mutations', () => {
    const tokenRegistry = new LocalAuthTokenRegistry();
    const gateway = new CommandGateway({ tokenRegistry });

    const shareResult = gateway.execute(
      {
        commandId: 'cmd-share-spectator',
        type: 'spectator.share',
        idempotencyKey: 'share-spectator-1'
      },
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(shareResult.allowed).toBe(true);
    expect(shareResult.issuedToken).toMatchObject({
      role: 'spectator',
      readOnly: true,
      authorizedMutationSurfaces: []
    });

    const spectatorAuth = tokenRegistry.authenticate(shareResult.issuedToken?.token);
    const mutationAttempt = gateway.execute(
      {
        commandId: 'cmd-spectator-mutation',
        type: 'node.set_maintenance',
        idempotencyKey: 'node-maint-spectator',
        nodeId: 'node-alpha'
      },
      spectatorAuth
    );

    expect(mutationAttempt).toMatchObject({
      allowed: false,
      reason: 'Role spectator cannot execute node.set_maintenance.'
    });
  });

  it('allows focus changes for every role without creating an audit entry', () => {
    const gateway = new CommandGateway();

    const result = gateway.execute(
      {
        commandId: 'cmd-focus',
        type: 'focus.set_local',
        focus: {
          runId: 'run-42',
          taskId: 'task-auth',
          nodeId: 'node-alpha'
        }
      },
      { principalId: 'spectator-1', role: 'spectator' }
    );

    expect(result).toMatchObject({
      allowed: true,
      mutation: false,
      replayed: false,
      focus: {
        runId: 'run-42',
        taskId: 'task-auth',
        nodeId: 'node-alpha'
      }
    });
    expect(gateway.getAuditLog()).toHaveLength(0);
  });

  it('rejects reclaim commands that do not satisfy the ownership preconditions', () => {
    const gateway = new CommandGateway();

    const result = gateway.execute(
      {
        commandId: 'cmd-reclaim-precondition',
        type: 'lease.reclaim',
        idempotencyKey: 'lease-reclaim-precondition',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        leaseExpired: false,
        ownershipResolved: false
      },
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(result).toMatchObject({
      allowed: false,
      reason: 'Lease reclaim requires an expired lease.'
    });
    expect(gateway.getAuditLog()[0]).toMatchObject({
      commandType: 'lease.reclaim',
      allowed: false
    });
  });
});