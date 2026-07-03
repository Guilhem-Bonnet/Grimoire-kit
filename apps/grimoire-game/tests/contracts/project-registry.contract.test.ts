import {
  CONTROL_PLANE_REGISTRY_VERSION,
  ProjectRegistrySnapshotSchema,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('project registry contract', () => {
  it('accepts a valid active project registry snapshot', () => {
    const snapshot = ProjectRegistrySnapshotSchema.parse({
      registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
      generatedAt: '2026-04-11T09:20:08.000Z',
      activeProject: {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        firstEventAt: '2026-04-11T09:20:07.000Z',
        lastEventAt: '2026-04-11T09:20:08.000Z',
        firstSequenceId: 7,
        lastSequenceId: 8,
        eventCount: 2,
        lastMessageId: 'workflow.step:8',
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        leaseId: 'lease-auth-review',
        worktreeId: 'wt-auth',
        nodeIds: ['node-alpha', 'node-beta'],
        workerIds: ['worker-dev-1'],
        leaseIds: ['lease-auth-review'],
        worktreeIds: ['wt-auth'],
        channels: ['runtime'],
        messageTypes: ['task.update', 'workflow.step']
      }
    });

    expect(snapshot).toMatchObject({
      registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
      activeProject: {
        projectId: 'grimoire-game',
        runId: 'run-42',
        lastSequenceId: 8
      }
    });
  });

  it('rejects registries whose active node is not part of the known fleet', () => {
    expect(() =>
      ProjectRegistrySnapshotSchema.parse({
        registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
        generatedAt: '2026-04-11T09:20:08.000Z',
        activeProject: {
          protocolVersion: RUNTIME_PROTOCOL_VERSION,
          projectId: 'grimoire-game',
          runId: 'run-42',
          firstEventAt: '2026-04-11T09:20:07.000Z',
          lastEventAt: '2026-04-11T09:20:08.000Z',
          firstSequenceId: 7,
          lastSequenceId: 8,
          eventCount: 2,
          lastMessageId: 'workflow.step:8',
          nodeId: 'node-gamma',
          nodeIds: ['node-alpha', 'node-beta'],
          workerIds: [],
          leaseIds: [],
          worktreeIds: [],
          channels: ['runtime'],
          messageTypes: ['task.update']
        }
      })
    ).toThrow();
  });
});