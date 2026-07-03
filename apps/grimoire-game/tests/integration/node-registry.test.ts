import {
  buildNodeRegistry,
  createNodeFleetView,
  projectServerEventToCanonicalEnvelope,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  type AgentPresence
} from '../../src';

describe('node registry integration', () => {
  const agent: AgentPresence = {
    id: 'dev-1',
    name: 'Amelia',
    role: 'agent',
    status: 'working',
    roomId: 'build-room',
    position: { x: 8, y: 8 }
  };

  it('reconstructs node health and fleet alerts from canonical envelopes', () => {
    const nodeAlpha = projectServerEventToCanonicalEnvelope(
      createTaskUpdateEvent(
        5,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'in_progress',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T10:00:00.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1'
      }
    );
    const nodeBeta = projectServerEventToCanonicalEnvelope(
      createWorkflowStepEvent(
        8,
        {
          step: 'Decision recorded',
          detail: 'Auth contract frozen.',
          sourceEventType: 'decision',
          traceId: 'trace-auth-1',
          taskId: 'task-auth',
          metadata: {
            topic: 'auth'
          }
        },
        {
          timestamp: '2026-04-11T10:00:08.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodeId: 'node-beta',
        workerId: 'worker-qa-1'
      }
    );

    expect(nodeAlpha).not.toBeNull();
    expect(nodeBeta).not.toBeNull();

    const snapshot = buildNodeRegistry(
      [
        nodeAlpha as NonNullable<typeof nodeAlpha>,
        nodeBeta as NonNullable<typeof nodeBeta>
      ],
      {
        scannedAt: '2026-04-11T10:00:40.000Z',
        staleAfterMs: 5_000,
        offlineAfterMs: 20_000
      }
    );
    const fleet = createNodeFleetView(snapshot);

    expect(snapshot.summary).toMatchObject({
      nodeCount: 2,
      liveNodeCount: 0,
      staleNodeCount: 0,
      offlineNodeCount: 2,
      workerCount: 2
    });
    expect(fleet.summary.alertCount).toBe(2);
    expect(fleet.nodes.map((node) => node.nodeId)).toEqual(['node-alpha', 'node-beta']);
    expect(fleet.alerts.every((alert) => alert.code === 'node_offline')).toBe(true);
  });

  it('fails closed when node envelopes span multiple runs', () => {
    const firstEnvelope = projectServerEventToCanonicalEnvelope(
      createTaskUpdateEvent(
        5,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'in_progress',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T10:00:00.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodeId: 'node-alpha'
      }
    );
    const secondEnvelope = projectServerEventToCanonicalEnvelope(
      createWorkflowStepEvent(
        8,
        {
          step: 'Decision recorded',
          detail: 'Auth contract frozen.',
          sourceEventType: 'decision',
          traceId: 'trace-auth-1',
          taskId: 'task-auth',
          metadata: {
            topic: 'auth'
          }
        },
        {
          timestamp: '2026-04-11T10:00:08.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-42',
        nodeId: 'node-beta'
      }
    );

    expect(() =>
      buildNodeRegistry(
        [
          firstEnvelope as NonNullable<typeof firstEnvelope>,
          secondEnvelope as NonNullable<typeof secondEnvelope>
        ],
        {
          scannedAt: '2026-04-11T10:00:10.000Z'
        }
      )
    ).toThrow('fail-closed');
  });
});