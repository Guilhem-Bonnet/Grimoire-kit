import {
  LEASE_STORE_VERSION,
  LeaseStoreSnapshotSchema,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('lease store contract', () => {
  it('accepts a valid lease store snapshot', () => {
    const snapshot = LeaseStoreSnapshotSchema.parse({
      registryVersion: LEASE_STORE_VERSION,
      generatedAt: '2026-04-11T11:00:10.000Z',
      projectId: 'grimoire-game',
      runId: 'run-42',
      leases: [
        {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          projectId: 'grimoire-game',
          runId: 'run-42',
          leaseId: 'lease-auth',
          taskId: 'task-auth',
          nodeId: 'node-alpha',
          workerId: 'worker-dev-1',
          worktreeId: 'wt-auth',
          branch: 'feature/auth',
          claimedAt: '2026-04-11T11:00:00.000Z',
          lastRenewedAt: '2026-04-11T11:00:05.000Z',
          expiresAt: '2026-04-11T11:00:35.000Z',
          ttlMs: 30_000,
          ageMs: 5_000,
          status: 'active',
          messageCount: 2,
          lastSequenceId: 12,
          traceId: 'trace-auth-1',
          channels: ['runtime'],
          messageTypes: ['task.update', 'workflow.step']
        }
      ],
      summary: {
        leaseCount: 1,
        activeLeaseCount: 1,
        expiredLeaseCount: 0
      }
    });

    expect(snapshot.leases[0]?.leaseId).toBe('lease-auth');
    expect(snapshot.summary.activeLeaseCount).toBe(1);
  });

  it('rejects snapshots whose summary does not match lease totals', () => {
    expect(() =>
      LeaseStoreSnapshotSchema.parse({
        registryVersion: LEASE_STORE_VERSION,
        generatedAt: '2026-04-11T11:00:10.000Z',
        projectId: 'grimoire-game',
        runId: 'run-42',
        leases: [],
        summary: {
          leaseCount: 1,
          activeLeaseCount: 1,
          expiredLeaseCount: 0
        }
      })
    ).toThrow();
  });
});