import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import type { AgentAdapter } from '../../src/bridge/agent-adapter';
import { RuntimeDashboardSession } from '../../src/bridge/runtime-dashboard-session';
import {
  createAgentStatusUpdate,
  createTaskUpdateEvent,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type ClientEvent,
  type ServerEvent,
  type GameStateSnapshot
} from '../../src/contracts/events';
import type { AuthContext } from '../../src/server/auth/rbac';

const AUTH: AuthContext = {
  principalId: 'orch-1',
  role: 'orchestrator'
};

function createInitialSnapshot(): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt: '2026-04-09T00:00:00.000Z',
    lastSequenceId: 0,
    agents: [
      {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    ],
    tasks: [
      {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'todo',
        assigneeId: 'dev-1'
      }
    ],
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: []
  };
}

const STREAMING_AGENT = (() => {
  const agent = createInitialSnapshot().agents[1];
  if (agent === undefined) {
    throw new Error('Expected streaming agent in initial snapshot.');
  }

  return agent;
})();

class StreamingTaskAdapter implements AgentAdapter {
  readonly source = 'streaming-test';

  async getInitialSnapshot(): Promise<readonly ServerEvent[]> {
    return [
      createTaskUpdateEvent(
        1,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'in_progress',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T10:00:01.000Z',
          agent: STREAMING_AGENT
        }
      )
    ];
  }

  async reconnect(): Promise<readonly ServerEvent[]> {
    return [];
  }

  async handleClientEvent(_event: ClientEvent): Promise<readonly ServerEvent[]> {
    return [];
  }
}

describe('RuntimeDashboardSession', () => {
  it('bootstraps dashboard from adapter snapshot', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const session = new RuntimeDashboardSession(adapter, {
      storeOptions: {
        dashboard: {
          observability: {
            maxTimelineRows: 5,
            maxAttentionItems: 5
          }
        }
      }
    });

    const result = await session.bootstrap(AUTH);

    expect(result.events).toHaveLength(1);
    expect(result.events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(result.dashboard.lastSequenceId).toBe(0);
    expect(session.getLastSequenceId()).toBe(0);
    expect(result.dashboard.board.metrics.agentCount).toBe(2);
    expect(result.dashboard.summary.workingAgentCount).toBe(1);
  });

  it('updates dashboard after mutating dispatch events', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const session = new RuntimeDashboardSession(adapter);

    await session.bootstrap(AUTH);
    const transitionResult = await session.dispatch(
      createTaskTransition('req-task-1', 'task-auth', 'in_progress', 'task-1'),
      AUTH
    );
    const statusResult = await session.dispatch(
      createAgentStatusUpdate('req-agent-1', 'dev-1', 'paused', 'agent-1'),
      AUTH
    );

    expect(transitionResult.events).toHaveLength(1);
    expect(transitionResult.events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(
      transitionResult.dashboard.board.taskColumns.find((column) => column.status === 'in_progress')?.count
    ).toBe(1);

    expect(statusResult.events).toHaveLength(1);
    expect(statusResult.events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusResult.dashboard.summary.workingAgentCount).toBe(0);
  });

  it('reconnects and rebuilds dashboard from replayed adapter events', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const sessionA = new RuntimeDashboardSession(adapter);

    await sessionA.bootstrap(AUTH);
    await sessionA.dispatch(
      createTaskTransition('req-task-2', 'task-auth', 'in_progress', 'task-2'),
      AUTH
    );

    const sessionB = new RuntimeDashboardSession(adapter);
    const reconnectResult = await sessionB.reconnect(0, AUTH);

    expect(reconnectResult.events).toHaveLength(1);
    expect(reconnectResult.events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(reconnectResult.dashboard.lastSequenceId).toBe(1);
    expect(
      reconnectResult.dashboard.board.taskColumns.find((column) => column.status === 'in_progress')?.count
    ).toBe(1);
  });

  it('tracks internal sequence cursor and syncs without explicit offsets', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const session = new RuntimeDashboardSession(adapter);

    expect(session.getLastSequenceId()).toBeUndefined();

    const firstSync = await session.sync(AUTH);
    expect(firstSync.events).toHaveLength(1);
    expect(firstSync.events[0]?.type).toBe('STATE_SNAPSHOT');
    expect(session.getLastSequenceId()).toBe(0);

    await session.dispatch(
      createTaskTransition('req-task-4', 'task-auth', 'in_progress', 'task-4'),
      AUTH
    );
    expect(session.getLastSequenceId()).toBe(1);

    const idleSync = await session.sync(AUTH);
    expect(idleSync.events).toHaveLength(0);
    expect(session.getLastSequenceId()).toBe(1);

    adapter.emitAgentState({
      id: 'dev-1',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 },
      parentId: 'orch-1',
      lastTool: 'runSubagent'
    });

    const replaySync = await session.sync(AUTH);
    expect(replaySync.events).toHaveLength(1);
    expect(replaySync.events[0]?.type).toBe('AGENT_STATE');
    expect(replaySync.events[0]?.sequenceId).toBe(2);
    expect(replaySync.dashboard.lastSequenceId).toBe(2);
    expect(session.getLastSequenceId()).toBe(2);
  });

  it('propagates live dashboard updates to subscribers', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const session = new RuntimeDashboardSession(adapter);
    const observedSequenceIds: number[] = [];
    const unsubscribe = session.subscribe((dashboard) => {
      observedSequenceIds.push(dashboard.lastSequenceId);
    });

    await session.bootstrap(AUTH);
    await session.dispatch(
      createTaskTransition('req-task-3', 'task-auth', 'in_progress', 'task-3'),
      AUTH
    );

    unsubscribe();
    await session.dispatch(createAgentStatusUpdate('req-agent-3', 'dev-1', 'paused', 'agent-3'), AUTH);

    expect(observedSequenceIds).toEqual([0, 1]);
  });

  it('keeps control-plane node fleet in sync for streamed runtime events', async () => {
    const session = new RuntimeDashboardSession(new StreamingTaskAdapter(), {
      controlPlane: {
        projectId: 'grimoire-game',
        runId: 'run-41',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        leaseId: 'lease-auth',
        worktreeId: 'wt-auth',
        branch: 'feature/auth'
      }
    });

    const result = await session.bootstrap(AUTH);

    expect(result.dashboard.projectRegistry?.activeProject.runId).toBe('run-41');
    expect(result.dashboard.nodeFleet.summary.nodeCount).toBe(1);
    expect(result.dashboard.nodeFleet.nodes[0]?.nodeId).toBe('node-alpha');
    expect(result.dashboard.leaseView.summary.activeLeaseCount).toBe(1);
    expect(result.dashboard.leaseView.leases[0]).toMatchObject({
      branch: 'feature/auth',
      worktreeId: 'wt-auth',
      ownerId: 'worker-dev-1',
      ownershipStatus: 'owned',
      dirtyStatus: 'dirty'
    });
    expect(result.dashboard.summary.leaseCount).toBe(1);
    expect(result.dashboard.summary.liveNodeCount).toBe(1);
  });
});