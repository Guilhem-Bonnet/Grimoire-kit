import {
  createStateSnapshotEvent,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot,
  type ServerEvent
} from '../../src/contracts/events';
import {
  applyServerEvents,
  createEmptyGameState,
  hydrateGameState
} from '../../src/state/game-state';
import { createChallengeRoomView } from '../../src/state/challenge-room-view';

const ORCHESTRATOR: AgentPresence = {
  id: 'orch-1',
  name: 'Orchestrator',
  role: 'orchestrator',
  status: 'working',
  roomId: 'war-room',
  position: { x: 4, y: 4 }
};

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'create_file'
};

const REVIEWER_AGENT: AgentPresence = {
  id: 'review-1',
  name: 'Rodin',
  role: 'agent',
  status: 'working',
  roomId: 'challenge-room',
  position: { x: 12, y: 8 },
  parentId: 'orch-1',
  lastTool: 'semantic_search'
};

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-11T14:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT, REVIEWER_AGENT],
  tasks: [
    {
      id: 'task-auth',
      title: 'Ship auth middleware',
      status: 'review',
      priority: 'high',
      kind: 'feature',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

function createApprovedChallengeEvents(): ServerEvent[] {
  return [
    createWorkflowStepEvent(
      2,
      {
        step: 'Presentation opened',
        detail: 'Auth middleware pitch opened in the amphitheatre.',
        sourceEventType: 'challenge_presentation',
        traceId: 'trace-challenge-1',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'presentation',
          challengeRole: 'presenter',
          linkedTaskIds: ['task-auth']
        }
      },
      {
        timestamp: '2026-04-11T14:00:02.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      3,
      {
        step: 'Question asked',
        detail: 'What evidence proves replay stability?',
        sourceEventType: 'challenge_question',
        traceId: 'trace-challenge-1',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'questions',
          challengeRole: 'reviewer',
          linkedTaskIds: ['task-auth']
        }
      },
      {
        timestamp: '2026-04-11T14:00:03.000Z',
        agent: REVIEWER_AGENT
      }
    ),
    createWorkflowStepEvent(
      4,
      {
        step: 'Critical objection raised',
        detail: 'Add the replay proof before merge.',
        sourceEventType: 'challenge_critique',
        traceId: 'trace-challenge-1',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'critiques',
          challengeRole: 'critic',
          objectionId: 'obj-replay',
          objectionSeverity: 'high',
          linkedTaskIds: ['task-auth'],
          linkedTraceIds: ['trace-challenge-1']
        }
      },
      {
        timestamp: '2026-04-11T14:00:04.000Z',
        agent: REVIEWER_AGENT
      }
    ),
    createWorkflowStepEvent(
      5,
      {
        step: 'Vote recorded',
        detail: 'Proceed after the replay proof is attached.',
        sourceEventType: 'challenge_vote',
        traceId: 'trace-challenge-1',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'vote',
          challengeRole: 'voter',
          vote: 'approve',
          score: 85,
          challengeVerdict: 'approved',
          linkedTaskIds: ['task-auth'],
          linkedObjectionIds: ['obj-replay']
        }
      },
      {
        timestamp: '2026-04-11T14:00:05.000Z',
        agent: ORCHESTRATOR
      }
    ),
    createWorkflowStepEvent(
      6,
      {
        step: 'Iteration closed',
        detail: 'Replay proof attached, objection resolved, challenge approved.',
        sourceEventType: 'challenge_iteration',
        traceId: 'trace-challenge-1',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'iteration',
          challengeRole: 'moderator',
          challengeVerdict: 'approved',
          resolvedObjectionIds: ['obj-replay'],
          linkedTaskIds: ['task-auth']
        }
      },
      {
        timestamp: '2026-04-11T14:00:06.000Z',
        agent: ORCHESTRATOR
      }
    )
  ];
}

function createBlockedChallengeEvents(): ServerEvent[] {
  return [
    createWorkflowStepEvent(
      2,
      {
        step: 'Presentation opened',
        detail: 'Auth middleware pitch opened in the amphitheatre.',
        sourceEventType: 'challenge_presentation',
        traceId: 'trace-challenge-2',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-2',
          challengePhase: 'presentation',
          challengeRole: 'presenter',
          linkedTaskIds: ['task-auth']
        }
      },
      {
        timestamp: '2026-04-11T15:00:02.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      3,
      {
        step: 'Question asked',
        detail: 'Where is the replay proof pack?',
        sourceEventType: 'challenge_question',
        traceId: 'trace-challenge-2',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-2',
          challengePhase: 'questions',
          challengeRole: 'reviewer',
          linkedTaskIds: ['task-auth']
        }
      },
      {
        timestamp: '2026-04-11T15:00:03.000Z',
        agent: REVIEWER_AGENT
      }
    ),
    createWorkflowStepEvent(
      4,
      {
        step: 'Blocking objection raised',
        detail: 'Replay proof is still missing from the evidence pack.',
        sourceEventType: 'challenge_critique',
        traceId: 'trace-challenge-2',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-2',
          challengePhase: 'critiques',
          challengeRole: 'critic',
          objectionId: 'obj-proof-pack',
          objectionSeverity: 'critical',
          linkedTaskIds: ['task-auth'],
          linkedTraceIds: ['trace-challenge-2']
        }
      },
      {
        timestamp: '2026-04-11T15:00:04.000Z',
        agent: REVIEWER_AGENT
      }
    ),
    createWorkflowStepEvent(
      5,
      {
        step: 'Vote recorded',
        detail: 'Iteration is required before approval.',
        sourceEventType: 'challenge_vote',
        traceId: 'trace-challenge-2',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-2',
          challengePhase: 'vote',
          challengeRole: 'voter',
          vote: 'revise',
          score: 42,
          challengeVerdict: 'iteration_required',
          linkedTaskIds: ['task-auth'],
          linkedObjectionIds: ['obj-proof-pack'],
          correctiveTaskId: 'task-proof-pack',
          autoCreated: true
        }
      },
      {
        timestamp: '2026-04-11T15:00:05.000Z',
        agent: ORCHESTRATOR
      }
    ),
    createTaskUpdateEvent(
      6,
      {
        id: 'task-proof-pack',
        title: 'Collect replay proof pack',
        status: 'backlog',
        priority: 'critical',
        kind: 'feature',
        dependencyIds: ['task-auth'],
        blockedReason: 'Waiting for new replay evidence.'
      },
      {
        timestamp: '2026-04-11T15:00:06.000Z'
      }
    ),
    createWorkflowStepEvent(
      7,
      {
        step: 'Iteration planned',
        detail: 'A corrective backlog card was opened for the missing replay proof.',
        sourceEventType: 'challenge_iteration',
        traceId: 'trace-challenge-2',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-2',
          challengePhase: 'iteration',
          challengeRole: 'moderator',
          challengeVerdict: 'iteration_required',
          linkedTaskIds: ['task-auth'],
          correctiveTaskIds: ['task-proof-pack'],
          autoCreated: true
        }
      },
      {
        timestamp: '2026-04-11T15:00:07.000Z',
        agent: ORCHESTRATOR
      }
    )
  ];
}

describe('challenge room view', () => {
  it('projects the full challenge sequence with speech turns, objections, votes and a completed verdict', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T14:00:01.000Z'), createApprovedChallengeEvents());
    const view = createChallengeRoomView(state);
    const session = view.sessions[0];

    expect(view.focus).toMatchObject({
      traceId: 'trace-challenge-1',
      taskId: 'task-auth'
    });
    expect(session).toMatchObject({
      status: 'completed',
      currentPhase: 'iteration',
      traceId: 'trace-challenge-1',
      taskId: 'task-auth',
      taskTitle: 'Ship auth middleware',
      verdict: {
        kind: 'approved',
        blocking: false,
        approveCount: 1,
        score: 85
      }
    });
    expect(session?.phases.map((phase) => ({ phase: phase.phase, status: phase.status }))).toEqual([
      { phase: 'presentation', status: 'completed' },
      { phase: 'questions', status: 'completed' },
      { phase: 'critiques', status: 'completed' },
      { phase: 'vote', status: 'completed' },
      { phase: 'iteration', status: 'completed' }
    ]);
    expect(session?.speechTurns.map((turn) => turn.phase)).toEqual([
      'presentation',
      'questions',
      'critiques',
      'vote',
      'iteration'
    ]);
    expect(session?.speechTurns.every((turn) => turn.relatedBubbleIds.length > 0)).toBe(true);
    expect(session?.objections).toEqual([
      expect.objectContaining({
        id: 'obj-replay',
        status: 'resolved',
        linkedTaskIds: ['task-auth'],
        linkedTraceIds: ['trace-challenge-1']
      })
    ]);
    expect(session?.votes).toEqual([
      expect.objectContaining({
        value: 'approve',
        score: 85,
        linkedObjectionIds: ['obj-replay']
      })
    ]);
    expect(session?.journal).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: 'presentation',
          taskId: 'task-auth'
        }),
        expect.objectContaining({
          kind: 'iteration',
          taskId: 'task-auth'
        })
      ])
    );
  });

  it('marks an iteration-required session as blocked and exposes the auto-created corrective backlog card', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T15:00:01.000Z'), createBlockedChallengeEvents());
    const view = createChallengeRoomView(state);
    const session = view.sessions[0];

    expect(session).toMatchObject({
      status: 'blocked',
      currentPhase: 'iteration',
      blockedReason: 'Iteration required: corrective action Collect replay proof pack is still backlog.',
      verdict: {
        kind: 'iteration_required',
        blocking: true,
        reviseCount: 1,
        score: 42
      }
    });
    expect(session?.phases.find((phase) => phase.phase === 'iteration')).toMatchObject({
      status: 'blocked'
    });
    expect(session?.correctiveActions).toEqual([
      expect.objectContaining({
        taskId: 'task-proof-pack',
        title: 'Collect replay proof pack',
        status: 'backlog',
        syncedStatus: 'backlog',
        autoCreated: true,
        visibleInBacklog: true,
        parentTaskId: 'task-auth',
        blockerCount: 2
      })
    ]);
    expect(session?.journal).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: 'corrective_action',
          taskId: 'task-proof-pack'
        })
      ])
    );
  });

  it('keeps challenge causality stable after replay from snapshot and event stream', () => {
    const deltaEvents = createBlockedChallengeEvents();
    const hydratedState = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T15:00:01.000Z'), deltaEvents);
    const replayedState = applyServerEvents(createEmptyGameState(), [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T15:00:00.000Z'),
      ...deltaEvents
    ]);

    const view = createChallengeRoomView(hydratedState, { traceId: 'trace-challenge-2' });
    const replayedView = createChallengeRoomView(replayedState, { traceId: 'trace-challenge-2' });

    expect(replayedView).toEqual(view);
  });
});