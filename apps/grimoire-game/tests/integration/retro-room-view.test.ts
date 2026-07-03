import {
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot,
  type TaskSnapshot
} from '../../src/contracts/events';
import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import {
  createRetroRoomSnapshot,
  createRetroRoomSnapshotFromGameStateSnapshot,
  createRetroRoomView,
  createRetroRoomViewFromGameStateSnapshots,
  createRetroRoomViewFromStates
} from '../../src/state/retro-room-view';

const ORCHESTRATOR: AgentPresence = {
  id: 'orch-1',
  name: 'Orchestrator',
  role: 'orchestrator',
  status: 'idle',
  roomId: 'war-room',
  position: { x: 4, y: 4 }
};

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'retro-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'semantic_search'
};

function createState(
  tasks: Record<string, TaskSnapshot>,
  recentWorkflowSteps: readonly WorkflowStepLogEntry[],
  hydratedAt: string
): GameState {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    lastSequenceId: recentWorkflowSteps[recentWorkflowSteps.length - 1]?.sequenceId ?? 0,
    hydratedAt,
    agents: {
      'orch-1': ORCHESTRATOR,
      'dev-1': DEV_AGENT
    },
    tasks,
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps,
    lastErrors: []
  };
}

function toSnapshot(state: GameState, generatedAt: string): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt,
    lastSequenceId: state.lastSequenceId,
    agents: Object.values(state.agents),
    tasks: Object.values(state.tasks),
    config: state.config,
    recentToolCalls: [...state.recentToolCalls],
    recentWorkflowSteps: [...state.recentWorkflowSteps]
  };
}

function createLeftState(): GameState {
  return createState(
    {
      'task-retro': {
        id: 'task-retro',
        title: 'GAME-TKT-019 Retro comparison',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-auth': {
        id: 'task-auth',
        title: 'Auth hardening',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    [
      {
        step: 'Routing dispatch',
        detail: 'Retro compare iteration A',
        sourceEventType: 'routing',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          intent: 'retro compare'
        },
        sequenceId: 10,
        timestamp: '2026-04-12T10:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Use baseline snapshot for delta review',
        sourceEventType: 'decision',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          topic: 'retro',
          choice: 'baseline'
        },
        sequenceId: 11,
        timestamp: '2026-04-12T10:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Retro output captured',
        detail: 'Initial retro evidence captured',
        sourceEventType: 'retro_capture',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          verificationRef: 'verify://retro/task-retro',
          evidenceRefs: ['evidence://retro/base'],
          expectedProofRefs: ['evidence://retro/base', 'attestation://retro/base'],
          actionId: 'task.transition.review'
        },
        sequenceId: 12,
        timestamp: '2026-04-12T10:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Security finding recorded',
        detail: 'Open blocker on retro evidence attestation',
        sourceEventType: 'security_finding',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          findingId: 'SEC-RETRO-1',
          title: 'Missing attestation proof',
          severity: 'critical',
          status: 'open',
          confidenceScore: 9.1,
          exploitScenario: 'Post mortem lacks attested proof.',
          surfaceId: 'retro_room',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'blocked',
          owaspCategory: 'LLM05:2025 Improper Output Handling',
          controls: ['retro:proof']
        },
        sequenceId: 13,
        timestamp: '2026-04-12T10:00:13.000Z',
        agentId: 'dev-1'
      }
    ],
    '2026-04-12T10:00:00.000Z'
  );
}

function createRightState(): GameState {
  return createState(
    {
      'task-retro': {
        id: 'task-retro',
        title: 'GAME-TKT-019 Retro comparison',
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-auth': {
        id: 'task-auth',
        title: 'Auth hardening',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    [
      {
        step: 'Routing dispatch',
        detail: 'Retro compare iteration B',
        sourceEventType: 'routing',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          intent: 'retro compare'
        },
        sequenceId: 20,
        timestamp: '2026-04-12T10:05:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Adopt delta snapshot before closeout',
        sourceEventType: 'decision',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          topic: 'retro',
          choice: 'delta'
        },
        sequenceId: 21,
        timestamp: '2026-04-12T10:05:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Retro output captured',
        detail: 'Attested retro evidence captured',
        sourceEventType: 'retro_capture',
        traceId: 'trace-retro',
        taskId: 'task-retro',
        metadata: {
          verificationRef: 'verify://retro/task-retro',
          evidenceRefs: ['evidence://retro/base', 'attestation://retro/base'],
          expectedProofRefs: ['evidence://retro/base', 'attestation://retro/base'],
          actionId: 'task.transition.done'
        },
        sequenceId: 22,
        timestamp: '2026-04-12T10:05:12.000Z',
        agentId: 'dev-1'
      }
    ],
    '2026-04-12T10:05:00.000Z'
  );
}

describe('retro-room-view', () => {
  it('builds comparable snapshots and surfaces decision, blocker, output and progression diffs with resolvable refs', () => {
    const leftSnapshot = createRetroRoomSnapshot(createLeftState(), {
      snapshotId: 'retro-left',
      label: 'Iteration A',
      generatedAt: '2026-04-12T10:00:15.000Z'
    });
    const rightSnapshot = createRetroRoomSnapshot(createRightState(), {
      snapshotId: 'retro-right',
      label: 'Iteration B',
      generatedAt: '2026-04-12T10:05:15.000Z'
    });
    const view = createRetroRoomView(leftSnapshot, rightSnapshot);

    expect(leftSnapshot.tasks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          taskId: 'task-retro',
          traceIds: ['trace-retro'],
          evidenceRefs: ['evidence://retro/base'],
          blockerCount: expect.any(Number),
          ticketRefs: ['GAME-TKT-019']
        })
      ])
    );
    expect(leftSnapshot.outputs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          outputKey: 'retro_capture::task-retro::trace-retro::retro-output-captured::verify://retro/task-retro',
          missingExpectedProofRefs: ['attestation://retro/base']
        })
      ])
    );

    expect(view.traceComparison).toEqual({
      sharedTraceIds: ['trace-retro'],
      onlyLeftTraceIds: [],
      onlyRightTraceIds: []
    });
    expect(view.summary.blockerDiffCount).toBeGreaterThanOrEqual(1);
    expect(view.summary.progressionDiffCount).toBeGreaterThanOrEqual(1);
    expect(view.summary.decisionDiffCount).toBeGreaterThanOrEqual(1);
    expect(view.summary.outputDiffCount).toBeGreaterThanOrEqual(1);
    expect(view.focusItems.length).toBeGreaterThan(0);
    expect(view.diffItems).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: 'progression',
          focus: 'GAME-TKT-019 Retro comparison',
          leftValue: 'review',
          rightValue: 'done',
          refs: expect.objectContaining({
            taskIds: ['task-retro'],
            traceIds: ['trace-retro'],
            ticketRefs: ['GAME-TKT-019']
          })
        }),
        expect.objectContaining({
          category: 'decision',
          focus: 'Decision recorded',
          leftValue: 'Use baseline snapshot for delta review',
          rightValue: 'Adopt delta snapshot before closeout',
          refs: expect.objectContaining({
            taskIds: ['task-retro'],
            traceIds: ['trace-retro'],
            evidenceRefs: ['attestation://retro/base', 'evidence://retro/base']
          })
        }),
        expect.objectContaining({
          category: 'output',
          focus: 'Retro output captured',
          refs: expect.objectContaining({
            taskIds: ['task-retro'],
            traceIds: ['trace-retro'],
            evidenceRefs: ['attestation://retro/base', 'evidence://retro/base']
          })
        })
      ])
    );
  });

  it('keeps retro snapshots and comparative diffs stable after loading from GameStateSnapshot', () => {
    const leftState = createLeftState();
    const rightState = createRightState();
    const leftSnapshot = toSnapshot(leftState, '2026-04-12T10:00:15.000Z');
    const rightSnapshot = toSnapshot(rightState, '2026-04-12T10:05:15.000Z');

    expect(
      createRetroRoomSnapshotFromGameStateSnapshot(leftSnapshot, {
        snapshotId: 'retro-left',
        label: 'Iteration A'
      })
    ).toEqual(
      createRetroRoomSnapshot(leftState, {
        snapshotId: 'retro-left',
        label: 'Iteration A',
        generatedAt: '2026-04-12T10:00:15.000Z'
      })
    );

    expect(
      createRetroRoomViewFromGameStateSnapshots(
        leftSnapshot,
        rightSnapshot,
        {
          snapshotId: 'retro-left',
          label: 'Iteration A'
        },
        {
          snapshotId: 'retro-right',
          label: 'Iteration B'
        }
      )
    ).toEqual(
      createRetroRoomViewFromStates(
        leftState,
        rightState,
        {
          snapshotId: 'retro-left',
          label: 'Iteration A',
          generatedAt: '2026-04-12T10:00:15.000Z'
        },
        {
          snapshotId: 'retro-right',
          label: 'Iteration B',
          generatedAt: '2026-04-12T10:05:15.000Z'
        }
      )
    );
  });
});