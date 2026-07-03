import type { GameState } from '../../src/state/game-state';
import {
  createProgressionView,
  evaluateAgentProgression
} from '../../src/state/progression-view';

function createProgressionState(config: GameState['config'] = {}): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 14,
    hydratedAt: '2026-04-08T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'forge-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    config,
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'XP awarded 1',
        detail: 'First eligible action credited.',
        sourceEventType: 'decision',
        traceId: 'progress-001',
        taskId: 'task-auth',
        metadata: {
          agentId: 'dev-1',
          progressionActionId: 'action-1',
          xpAward: 35,
          achievementId: 'first-proof'
        },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'XP awarded duplicate',
        detail: 'Duplicate action should not be credited twice.',
        sourceEventType: 'decision',
        traceId: 'progress-001',
        taskId: 'task-auth',
        metadata: {
          agentId: 'dev-1',
          progressionActionId: 'action-1',
          xpAward: 35,
          achievementId: 'first-proof'
        },
        sequenceId: 13,
        timestamp: '2026-04-08T00:00:13.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'XP awarded 2',
        detail: 'Second eligible action credited.',
        sourceEventType: 'decision',
        traceId: 'progress-001',
        taskId: 'task-auth',
        metadata: {
          agentId: 'dev-1',
          progressionActionId: 'action-2',
          xpAward: 70,
          achievementIds: ['review-master']
        },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('progression view', () => {
  it('credits eligible actions once and computes deterministic levels', () => {
    const view = createProgressionView(createProgressionState());
    const agent = evaluateAgentProgression(createProgressionState(), 'dev-1');

    expect(view.summary).toEqual({
      agentCount: 2,
      totalXp: 105,
      highestLevel: 2,
      achievementCount: 2,
      duplicateCreditBlockedCount: 1
    });
    expect(agent).toMatchObject({
      agentId: 'dev-1',
      totalXp: 105,
      level: 2,
      creditedActionIds: ['action-1', 'action-2']
    });
    expect(agent?.unlockedAchievements.map((achievement) => achievement.achievementId)).toEqual([
      'first-proof',
      'review-master'
    ]);
  });

  it('rebuilds the same progression state after restart from the persisted snapshot', () => {
    const initialView = createProgressionView(createProgressionState());
    const restartedView = createProgressionView(
      createProgressionState({
        'progression.snapshot': initialView.persistenceState,
        'progression.xpPerLevel': 100
      })
    );

    expect(restartedView.persistenceState).toEqual(initialView.persistenceState);
    expect(restartedView.agents.find((agent) => agent.agentId === 'dev-1')).toMatchObject({
      totalXp: 105,
      level: 2,
      creditedActionIds: ['action-1', 'action-2']
    });
  });

  it('keeps achievements consultable from the persisted snapshot over time', () => {
    const view = createProgressionView(
      createProgressionState({
        'progression.snapshot': {
          xpPerLevel: 100,
          agents: {
            'dev-1': {
              totalXp: 105,
              level: 2,
              creditedActionIds: ['action-1', 'action-2'],
              achievements: ['first-proof', 'review-master']
            }
          }
        }
      })
    );

    expect(view.agents.find((agent) => agent.agentId === 'dev-1')?.unlockedAchievements).toMatchObject([
      { achievementId: 'first-proof' },
      { achievementId: 'review-master' }
    ]);
  });
});