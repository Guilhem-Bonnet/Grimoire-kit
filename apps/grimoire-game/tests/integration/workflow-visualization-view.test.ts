import {
  createStateSnapshotEvent,
  createToolCallEvent,
  createVerificationGateEvent,
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
import { createWorkflowVisualizationView } from '../../src/state/workflow-visualization-view';

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

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-11T13:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT],
  tasks: [
    {
      id: 'task-auth',
      title: 'Implement auth',
      status: 'review',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

function createWorkflowEvents(): ServerEvent[] {
  return [
    createWorkflowStepEvent(
      2,
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        metadata: {
          intent: 'Implement auth'
        }
      },
      {
        timestamp: '2026-04-11T13:00:02.000Z',
        agent: ORCHESTRATOR
      }
    ),
    createWorkflowStepEvent(
      3,
      {
        step: 'Draft patch',
        detail: 'JWT middleware scaffolded for the auth flow.',
        sourceEventType: 'implementation',
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        metadata: {
          room: 'build-room'
        }
      },
      {
        timestamp: '2026-04-11T13:00:03.000Z',
        agent: DEV_AGENT
      }
    ),
    createToolCallEvent(
      4,
      {
        tool: 'create_file',
        params: {
          path: 'src/auth.ts',
          task_id: 'task-auth'
        },
        sourceEventType: 'artifact_created',
        traceId: 'trace-auth-1'
      },
      {
        timestamp: '2026-04-11T13:00:04.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      5,
      {
        step: 'Decision recorded',
        detail: 'JWT middleware ready for review.',
        sourceEventType: 'decision',
        traceId: 'trace-auth-1',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth'
        }
      },
      {
        timestamp: '2026-04-11T13:00:05.000Z',
        agent: DEV_AGENT
      }
    ),
    createVerificationGateEvent(
      6,
      {
        result: 'PASS',
        actionId: 'task.transition.done',
        verificationRef: 'verify://task-auth/workflow',
        evidenceRefs: [
          { kind: 'test', ref: 'tests://grimoire-game/workflow-visualization#task-auth' }
        ],
        controlsExecuted: ['tests:unit'],
        traceId: 'trace-auth-1',
        taskId: 'task-auth'
      },
      {
        timestamp: '2026-04-11T13:00:06.000Z'
      }
    )
  ];
}

describe('workflow visualization view', () => {
  it('exposes the active workflow path, current step and decision history without ambiguity', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T13:00:01.000Z'), createWorkflowEvents());
    const view = createWorkflowVisualizationView(state);
    const path = view.paths[0];

    expect(view.focus).toMatchObject({
      traceId: 'trace-auth-1',
      taskId: 'task-auth',
      currentStepId: 'workflow-step:6'
    });
    expect(path).toMatchObject({
      traceId: 'trace-auth-1',
      taskId: 'task-auth',
      taskTitle: 'Implement auth',
      taskStatus: 'review',
      isActive: true,
      currentStepId: 'workflow-step:6'
    });
    expect(path?.steps.map((step) => ({ id: step.id, title: step.title, status: step.status, dependsOn: step.dependsOn }))).toEqual([
      {
        id: 'workflow-step:2',
        title: 'Routing dispatch',
        status: 'completed',
        dependsOn: []
      },
      {
        id: 'workflow-step:3',
        title: 'Draft patch',
        status: 'completed',
        dependsOn: ['workflow-step:2']
      },
      {
        id: 'workflow-step:5',
        title: 'Decision recorded',
        status: 'completed',
        dependsOn: ['workflow-step:3']
      },
      {
        id: 'workflow-step:6',
        title: 'Verification gate PASS',
        status: 'active',
        dependsOn: ['workflow-step:5']
      }
    ]);
    expect(path?.contributorAgentIds).toEqual(['dev-1', 'orch-1']);
    expect(path?.contributors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          agentId: 'dev-1',
          roomId: 'build-room',
          stepCount: 2,
          decisionCount: 2
        }),
        expect.objectContaining({
          agentId: 'orch-1',
          roomId: 'war-room',
          stepCount: 1,
          decisionCount: 1
        })
      ])
    );
    expect(path?.edges).toEqual([
      expect.objectContaining({ fromStepId: 'workflow-step:2', toStepId: 'workflow-step:3' }),
      expect.objectContaining({ fromStepId: 'workflow-step:3', toStepId: 'workflow-step:5' }),
      expect.objectContaining({ fromStepId: 'workflow-step:5', toStepId: 'workflow-step:6' })
    ]);
    expect(path?.decisions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'decision-5',
          title: 'Decision recorded',
          stepId: 'workflow-step:5',
          supportingToolCount: 1
        }),
        expect.objectContaining({
          id: 'decision-6',
          title: 'Verification gate PASS',
          stepId: 'workflow-step:6'
        })
      ])
    );
    expect(path?.auditTrail).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: 'workflow_step',
          sequenceId: 5,
          relatedStepId: 'workflow-step:5'
        }),
        expect.objectContaining({
          kind: 'decision_card',
          sequenceId: 5,
          relatedStepId: 'workflow-step:5',
          relatedDecisionId: 'decision-5'
        }),
        expect.objectContaining({
          kind: 'tool_call',
          sequenceId: 4,
          traceId: 'trace-auth-1'
        })
      ])
    );
  });

  it('keeps workflow causality stable after replay from snapshot and event stream', () => {
    const deltaEvents = createWorkflowEvents();
    const hydratedState = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T13:00:01.000Z'), deltaEvents);
    const replayedState = applyServerEvents(createEmptyGameState(), [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T13:00:00.000Z'),
      ...deltaEvents
    ]);

    const view = createWorkflowVisualizationView(hydratedState, { traceId: 'trace-auth-1' });
    const replayedView = createWorkflowVisualizationView(replayedState, { traceId: 'trace-auth-1' });

    expect(replayedView).toEqual(view);
  });
});