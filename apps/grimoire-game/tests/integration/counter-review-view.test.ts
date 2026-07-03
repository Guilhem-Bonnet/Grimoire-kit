import {
  createStateSnapshotEvent,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot,
  type ServerEvent
} from '../../src/contracts/events';
import { applyServerEvents, createEmptyGameState, hydrateGameState } from '../../src/state/game-state';
import { createCounterReviewView } from '../../src/state/counter-review-view';

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
  generatedAt: '2026-04-11T19:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT, REVIEWER_AGENT],
  tasks: [
    {
      id: 'task-auth',
      title: 'Ship auth middleware',
      status: 'review',
      priority: 'critical',
      kind: 'feature',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

function createApprovedCounterReviewEvents(): ServerEvent[] {
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
        timestamp: '2026-04-11T19:00:02.000Z',
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
        timestamp: '2026-04-11T19:00:03.000Z',
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
        timestamp: '2026-04-11T19:00:04.000Z',
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
          score: 91,
          challengeVerdict: 'approved',
          linkedTaskIds: ['task-auth'],
          linkedObjectionIds: ['obj-replay']
        }
      },
      {
        timestamp: '2026-04-11T19:00:05.000Z',
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
        timestamp: '2026-04-11T19:00:06.000Z',
        agent: ORCHESTRATOR
      }
    )
  ];
}

function createBlockedCounterReviewEvents(): ServerEvent[] {
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
        timestamp: '2026-04-11T19:10:02.000Z',
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
        timestamp: '2026-04-11T19:10:03.000Z',
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
        timestamp: '2026-04-11T19:10:04.000Z',
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
        timestamp: '2026-04-11T19:10:05.000Z',
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
        timestamp: '2026-04-11T19:10:06.000Z'
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
        timestamp: '2026-04-11T19:10:07.000Z',
        agent: ORCHESTRATOR
      }
    )
  ];
}

describe('counter review view', () => {
  it('marks a critical deliverable as ready when the orthogonal counter-review protocol is fully traced', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T19:00:01.000Z'), createApprovedCounterReviewEvents());
    const view = createCounterReviewView(state);

    expect(view.metrics).toEqual({
      requiredCount: 1,
      readyCount: 1,
      blockedCount: 0,
      objectionCount: 1
    });
    expect(view.tasks).toEqual([
      expect.objectContaining({
        taskId: 'task-auth',
        isReady: true,
        sessionStatus: 'completed',
        presenterAgentIds: ['dev-1'],
        criticAgentIds: ['review-1'],
        orthogonalAgentIds: expect.arrayContaining(['orch-1', 'review-1']),
        objectionCount: 1,
        openObjectionCount: 0,
        substantialObjectionCount: 1,
        verdictKind: 'approved'
      })
    ]);
    expect(view.tasks[0]?.checklist.every((item) => item.satisfied)).toBe(true);
  });

  it('keeps blocked counter-review sessions stable after replay and exposes unresolved substantial objections', () => {
    const deltaEvents = createBlockedCounterReviewEvents();
    const hydratedState = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T19:10:01.000Z'), deltaEvents);
    const replayedState = applyServerEvents(createEmptyGameState(), [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T19:10:00.000Z'),
      ...deltaEvents
    ]);

    const view = createCounterReviewView(hydratedState);
    const replayedView = createCounterReviewView(replayedState);

    expect(replayedView).toEqual(view);
    expect(view.tasks).toEqual([
      expect.objectContaining({
        taskId: 'task-auth',
        isReady: false,
        sessionStatus: 'blocked',
        objectionCount: 1,
        openObjectionCount: 1,
        substantialObjectionCount: 1,
        verdictKind: 'iteration_required'
      }),
      expect.objectContaining({
        taskId: 'task-proof-pack',
        isReady: false,
        sessionStatus: 'missing'
      })
    ]);
    expect(view.tasks[0]?.checklist.filter((item) => !item.satisfied).map((item) => item.code)).toEqual(
      expect.arrayContaining([
        'COUNTER_REVIEW_SUBSTANTIAL_OBJECTIONS_RESOLVED',
        'COUNTER_REVIEW_NON_BLOCKING_VERDICT'
      ])
    );
  });
});