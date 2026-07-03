import {
  MockAgentAdapter,
  RuntimeDashboardSession,
  createTaskTransition,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  type AgentPresence,
  type GameStateSnapshot
} from '../../src';
import {
  ProjectRegistry,
  buildActiveProjectRegistry
} from '../../src/server/control-plane/project-registry';
import {
  projectServerEventToCanonicalEnvelope,
  projectServerEventsToCanonicalEnvelopes
} from '../../src/state/canonical-envelope-pilot';

describe('project registry control-plane integration', () => {
  const agent: AgentPresence = {
    id: 'dev-1',
    name: 'Amelia',
    role: 'agent',
    status: 'working',
    roomId: 'build-room',
    position: { x: 8, y: 8 }
  };

  const initialSnapshot = {
    protocolVersion: 'v1',
    generatedAt: '2026-04-11T09:30:00.000Z',
    lastSequenceId: 6,
    agents: [agent],
    tasks: [
      {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    ],
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: []
  } satisfies GameStateSnapshot;

  it('reconstructs a single active run from canonical envelopes with multi-PC identifiers', () => {
    const taskUpdate = createTaskUpdateEvent(
      7,
      {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      },
      {
        timestamp: '2026-04-11T09:30:07.000Z',
        agent
      }
    );
    const workflowStep = createWorkflowStepEvent(
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
        timestamp: '2026-04-11T09:30:08.000Z',
        agent
      }
    );

    const firstEnvelope = projectServerEventToCanonicalEnvelope(taskUpdate, 'runtime', {
      projectId: 'grimoire-game',
      runId: 'run-42',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-auth'
    });
    const secondEnvelope = projectServerEventToCanonicalEnvelope(workflowStep, 'runtime', {
      projectId: 'grimoire-game',
      runId: 'run-42',
      nodeId: 'node-beta',
      workerId: 'worker-qa-1',
      leaseId: 'lease-auth-review',
      worktreeId: 'wt-auth'
    });

    expect(firstEnvelope).not.toBeNull();
    expect(secondEnvelope).not.toBeNull();

    const snapshot = buildActiveProjectRegistry([
      firstEnvelope as NonNullable<typeof firstEnvelope>,
      secondEnvelope as NonNullable<typeof secondEnvelope>
    ]);

    expect(snapshot).toMatchObject({
      activeProject: {
        projectId: 'grimoire-game',
        runId: 'run-42',
        firstSequenceId: 7,
        lastSequenceId: 8,
        eventCount: 2,
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        nodeId: 'node-beta',
        workerId: 'worker-qa-1',
        leaseId: 'lease-auth-review',
        worktreeId: 'wt-auth',
        nodeIds: ['node-alpha', 'node-beta'],
        workerIds: ['worker-dev-1', 'worker-qa-1'],
        leaseIds: ['lease-auth-review'],
        worktreeIds: ['wt-auth'],
        channels: ['runtime'],
        messageTypes: ['task.update', 'workflow.step']
      }
    });
  });

  it('fails closed when envelopes span multiple runs', () => {
    const firstEnvelope = projectServerEventToCanonicalEnvelope(
      createTaskUpdateEvent(
        7,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'review',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T09:30:07.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-42',
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
          timestamp: '2026-04-11T09:30:08.000Z',
          agent
        }
      ),
      'runtime',
      {
        projectId: 'grimoire-game',
        runId: 'run-43',
        nodeId: 'node-beta'
      }
    );

    expect(firstEnvelope).not.toBeNull();
    expect(secondEnvelope).not.toBeNull();

    expect(() =>
      buildActiveProjectRegistry([
        firstEnvelope as NonNullable<typeof firstEnvelope>,
        secondEnvelope as NonNullable<typeof secondEnvelope>
      ])
    ).toThrow('fail-closed');
  });

  it('stays stable when fed from runtime dashboard session mutations', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
    const session = new RuntimeDashboardSession(adapter);
    const registry = new ProjectRegistry();

    const seededSnapshot = registry.applyEnvelope(
      projectServerEventToCanonicalEnvelope(
        createTaskUpdateEvent(
          5,
          {
            id: 'task-auth',
            title: 'Implement auth',
            status: 'in_progress',
            assigneeId: 'dev-1'
          },
          {
            timestamp: '2026-04-11T09:30:05.000Z',
            agent
          }
        ),
        'runtime',
        {
          projectId: 'grimoire-game',
          runId: 'run-99',
          nodeId: 'node-alpha',
          workerId: 'worker-orchestrator-1'
        }
      ) as NonNullable<ReturnType<typeof projectServerEventToCanonicalEnvelope>>
    );

    await session.bootstrap({
      principalId: 'orch-1',
      role: 'orchestrator'
    });

    const result = await session.dispatch(
      createTaskTransition('req-task-auth-review', 'task-auth', 'review', 'task-auth-review'),
      {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    );

    const projectedEnvelopes = projectServerEventsToCanonicalEnvelopes(result.events, 'runtime', {
      projectId: 'grimoire-game',
      runId: 'run-99',
      nodeId: 'node-alpha',
      workerId: 'worker-orchestrator-1'
    });

    expect(projectedEnvelopes).toEqual([]);
    expect(registry.getSnapshot()).toEqual(seededSnapshot);
    expect(result.dashboard.lastSequenceId).toBeGreaterThan(seededSnapshot.activeProject.lastSequenceId);
  });
});