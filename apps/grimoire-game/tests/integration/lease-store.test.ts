import {
  LeaseStore,
  buildLeaseStore,
  createLeaseView,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  projectServerEventToCanonicalEnvelope,
  type AgentPresence
} from '../../src';

describe('lease store integration', () => {
  const agent: AgentPresence = {
    id: 'dev-1',
    name: 'Amelia',
    role: 'agent',
    status: 'working',
    roomId: 'build-room',
    position: { x: 8, y: 8 }
  };

  it('reconstructs active and expired leases from canonical envelopes', () => {
    const activeEnvelope = projectServerEventToCanonicalEnvelope(
      createTaskUpdateEvent(
        11,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'review',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T11:00:20.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-42',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        leaseId: 'lease-auth',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
      }
    );
    const expiredEnvelope = projectServerEventToCanonicalEnvelope(
      createWorkflowStepEvent(
        12,
        {
          step: 'Decision recorded',
          detail: 'Legacy contract preserved.',
          sourceEventType: 'decision',
          traceId: 'trace-legacy-1',
          taskId: 'task-legacy',
          metadata: {
            topic: 'legacy'
          }
        },
        {
          timestamp: '2026-04-11T11:00:00.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-42',
        nodeId: 'node-beta',
        workerId: 'worker-qa-1',
        leaseId: 'lease-legacy',
        worktreeId: 'wt-legacy',
        branch: 'feature/legacy'
      }
    );

    const snapshot = buildLeaseStore(
      [
        activeEnvelope as NonNullable<typeof activeEnvelope>,
        expiredEnvelope as NonNullable<typeof expiredEnvelope>
      ],
      {
        scannedAt: '2026-04-11T11:00:40.000Z',
        ttlMs: 15_000
      }
    );
    const view = createLeaseView(snapshot);

    expect(snapshot.summary).toMatchObject({
      leaseCount: 2,
      activeLeaseCount: 0,
      expiredLeaseCount: 2
    });
    expect(view.summary.alertCount).toBe(2);
    expect(view.leases.map((lease) => lease.leaseId)).toEqual(['lease-auth', 'lease-legacy']);
  });

  it('supports claim, renew and reclaim with TTL enforcement', () => {
    const store = new LeaseStore({ ttlMs: 10_000 });

    store.claim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-auth',
      taskId: 'task-auth',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-auth',
      branch: 'feature/auth',
      claimedAt: '2026-04-11T11:00:00.000Z'
    });
    store.renew({
      leaseId: 'lease-auth',
      renewedAt: '2026-04-11T11:00:05.000Z'
    });

    expect(store.getSnapshot('2026-04-11T11:00:08.000Z')?.summary.activeLeaseCount).toBe(1);
    expect(store.getSnapshot('2026-04-11T11:00:20.000Z')?.summary.expiredLeaseCount).toBe(1);

    store.reclaim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-auth-reclaimed',
      taskId: 'task-auth',
      nodeId: 'node-beta',
      workerId: 'worker-qa-1',
      worktreeId: 'wt-auth',
      branch: 'feature/auth-reclaimed',
      claimedAt: '2026-04-11T11:00:21.000Z'
    });

    const snapshot = store.getSnapshot('2026-04-11T11:00:22.000Z');
    expect(snapshot?.summary.activeLeaseCount).toBe(1);
    expect(snapshot?.leases.some((lease) => lease.leaseId === 'lease-auth-reclaimed' && lease.status === 'active')).toBe(
      true
    );
  });

  it('rejects unresolved and colliding Git ownership claims', () => {
    const store = new LeaseStore({ ttlMs: 10_000 });

    expect(() =>
      store.claim({
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-missing-branch',
        taskId: 'task-auth',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        claimedAt: '2026-04-11T11:00:00.000Z'
      })
    ).toThrow('resolved branch');

    store.claim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-auth',
      taskId: 'task-auth',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-auth',
      branch: 'feature/auth',
      claimedAt: '2026-04-11T11:00:00.000Z'
    });

    expect(() =>
      store.claim({
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-auth-branch-conflict',
        taskId: 'task-auth-review',
        nodeId: 'node-beta',
        workerId: 'worker-qa-1',
        worktreeId: 'wt-auth-review',
        branch: 'feature/auth',
        claimedAt: '2026-04-11T11:00:01.000Z'
      })
    ).toThrow('already owned by active lease lease-auth');

    expect(() =>
      store.claim({
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-auth-worktree-conflict',
        taskId: 'task-auth-lint',
        nodeId: 'node-gamma',
        workerId: 'worker-dev-2',
        worktreeId: 'wt-auth',
        branch: 'feature/auth-lint',
        claimedAt: '2026-04-11T11:00:02.000Z'
      })
    ).toThrow('already owned by active lease lease-auth');
  });
});