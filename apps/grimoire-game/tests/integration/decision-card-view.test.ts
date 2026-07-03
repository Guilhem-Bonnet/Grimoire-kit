import type { GameState } from '../../src/state/game-state';
import {
  createDecisionCardView,
  evaluateTaskDecisionCardGate,
  queryDecisionCardView
} from '../../src/state/decision-card-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 16,
    hydratedAt: '2026-04-11T11:00:00.000Z',
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
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        priority: 'critical',
        assigneeId: 'dev-1'
      },
      'task-docs': {
        id: 'task-docs',
        title: 'Refresh docs',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: {
          path: 'src/auth.ts',
          task_id: 'task-auth'
        },
        sourceEventType: 'artifact_created',
        traceId: 'trace-auth',
        sequenceId: 13,
        timestamp: '2026-04-11T11:00:13.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Critical transition approved',
        detail: 'Auth runtime can move to done.',
        sourceEventType: 'decision',
        traceId: 'trace-auth',
        taskId: 'task-auth',
        metadata: {
          actionId: 'task.transition.done',
          context: 'Auth runtime passed the final verification chain.',
          options: ['ship now', 'hold for another pass'],
          selectedOption: 'ship now',
          rationale: 'Security review and tests both passed.',
          impact: 'Unlocks protected UI routes.',
          evidenceRefs: ['tests://grimoire-game/auth#runtime']
        },
        sequenceId: 14,
        timestamp: '2026-04-11T11:00:14.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Docs transition drafted',
        detail: 'Docs update can move to done after editorial pass.',
        sourceEventType: 'decision',
        traceId: 'trace-docs',
        taskId: 'task-docs',
        metadata: {
          actionId: 'task.transition.done',
          context: 'Docs wording is stable.',
          options: ['ship docs'],
          selectedOption: 'ship docs',
          rationale: 'Minor release note follow-up only.'
        },
        sequenceId: 15,
        timestamp: '2026-04-11T11:00:15.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Non-transition note',
        detail: 'Auth room captured an extra observation.',
        sourceEventType: 'decision',
        traceId: 'trace-auth',
        taskId: 'task-auth',
        metadata: {
          context: 'Observability is stable.',
          options: ['note it'],
          selectedOption: 'note it',
          rationale: 'Useful for audit.',
          impact: 'No transition impacted.',
          evidenceRefs: ['docs://retro/auth-note']
        },
        sequenceId: 16,
        timestamp: '2026-04-11T11:00:16.000Z',
        agentId: 'dev-1'
      }
    ],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

describe('decision card view', () => {
  it('projects structured decision cards and supports audit-oriented filtering', () => {
    const view = createDecisionCardView(createBaseState());
    const authCard = view.cards.find((card) => card.taskId === 'task-auth' && card.actionId === 'task.transition.done');
    const structuredTransitionCards = queryDecisionCardView(view, {
      actionId: 'task.transition.done',
      structuredOnly: true,
      transitionOnly: true
    });

    expect(view.summary).toMatchObject({
      cardCount: 3,
      structuredCount: 2,
      transitionCardCount: 2,
      incompleteTransitionCount: 1
    });
    expect(authCard).toMatchObject({
      actionId: 'task.transition.done',
      decisionContext: 'Auth runtime passed the final verification chain.',
      consideredOptions: ['ship now', 'hold for another pass'],
      selectedOption: 'ship now',
      rationale: 'Security review and tests both passed.',
      impact: 'Unlocks protected UI routes.',
      evidenceRefs: ['tests://grimoire-game/auth#runtime'],
      isStructured: true,
      isTransitionCard: true,
      missingFields: []
    });
    expect(structuredTransitionCards.cards).toHaveLength(1);
    expect(structuredTransitionCards.cards[0]?.taskId).toBe('task-auth');
  });

  it('builds gates for ready and incomplete critical transition cards', () => {
    const state = createBaseState();
    const docsTask = state.tasks['task-docs'];
    if (docsTask === undefined) {
      throw new Error('Expected task-docs fixture to exist.');
    }

    const gate = evaluateTaskDecisionCardGate(state, 'task-auth');
    const docsGate = evaluateTaskDecisionCardGate(
      {
        ...state,
        tasks: {
          ...state.tasks,
          'task-docs': {
            ...docsTask,
            priority: 'critical'
          }
        }
      },
      'task-docs'
    );

    expect(gate).not.toBeNull();
    expect(gate).toMatchObject({
      taskId: 'task-auth',
      isApplicable: true,
      isReady: true,
      missingFields: []
    });
    expect(docsGate).not.toBeNull();
    expect(docsGate).toMatchObject({
      taskId: 'task-docs',
      isApplicable: true,
      isReady: false,
      missingFields: ['impact', 'evidence']
    });
  });
});
