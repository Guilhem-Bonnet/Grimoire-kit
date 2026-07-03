import {
  NODE_REGISTRY_VERSION,
  NodeRegistrySnapshotSchema,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('node registry contract', () => {
  it('accepts a valid node registry snapshot', () => {
    const snapshot = NodeRegistrySnapshotSchema.parse({
      registryVersion: NODE_REGISTRY_VERSION,
      generatedAt: '2026-04-11T10:00:10.000Z',
      projectId: 'grimoire-game',
      runId: 'run-41',
      nodes: [
        {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          projectId: 'grimoire-game',
          runId: 'run-41',
          nodeId: 'node-alpha',
          firstSeenAt: '2026-04-11T10:00:00.000Z',
          lastSeenAt: '2026-04-11T10:00:08.000Z',
          firstSequenceId: 5,
          lastSequenceId: 8,
          messageCount: 2,
          staleAfterMs: 5_000,
          offlineAfterMs: 30_000,
          ageMs: 2_000,
          status: 'live',
          traceId: 'trace-auth-1',
          taskId: 'task-auth',
          capabilityTags: [],
          workerIds: ['worker-dev-1'],
          workers: [
            {
              workerId: 'worker-dev-1',
              firstSeenAt: '2026-04-11T10:00:00.000Z',
              lastSeenAt: '2026-04-11T10:00:08.000Z',
              firstSequenceId: 5,
              lastSequenceId: 8,
              messageCount: 2,
              traceId: 'trace-auth-1',
              taskId: 'task-auth'
            }
          ],
          channels: ['runtime'],
          messageTypes: ['task.update', 'workflow.step']
        }
      ],
      summary: {
        nodeCount: 1,
        liveNodeCount: 1,
        staleNodeCount: 0,
        offlineNodeCount: 0,
        workerCount: 1
      }
    });

    expect(snapshot.summary.nodeCount).toBe(1);
    expect(snapshot.nodes[0]?.status).toBe('live');
  });

  it('rejects snapshots whose summary does not match node totals', () => {
    expect(() =>
      NodeRegistrySnapshotSchema.parse({
        registryVersion: NODE_REGISTRY_VERSION,
        generatedAt: '2026-04-11T10:00:10.000Z',
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodes: [],
        summary: {
          nodeCount: 1,
          liveNodeCount: 1,
          staleNodeCount: 0,
          offlineNodeCount: 0,
          workerCount: 0
        }
      })
    ).toThrow();
  });
});